"""ローダ共通基盤 (P003 5.1)。

行の解析失敗は例外にせず、4 種の理由コードで返す (FR-014)。
理由コードは docs/P002-frontend-spec.md 6.2.5 が定める外部契約であり、
5 種目を作ってはならない。
"""

import csv
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..schema import COLUMN_TYPES, COLUMNS, LOGICAL_KEYS

STEP = "S4"

# --- 解析エラーの理由コード (この 4 種のみ) -------------------------------
REASON_COLUMNS = "列数不一致"
REASON_DATE = "日付書式不正"
REASON_NUMBER = "数値変換不能"
REASON_HEADER = "見出し不明"

ALL_REASONS = (REASON_COLUMNS, REASON_DATE, REASON_NUMBER, REASON_HEADER)

BATCH_SIZE = 10000

_NULL_TOKENS = ("", "-")

# プロセス全体で単調増加する挿入通番 (DS-LD-06 の重複排除で使う)
_load_seq_counter = [0]


def next_load_seq():
    _load_seq_counter[0] += 1
    return _load_seq_counter[0]


def reset_load_seq():
    """テスト用: 通番をリセットする。"""
    _load_seq_counter[0] = 0


class LoadResult(object):
    """1 ファイルの解析結果。"""

    def __init__(self):
        self.ok_rows = 0
        self.skipped_rows = 0
        self.errors = {}

    def add_error(self, reason, count=1):
        self.errors[reason] = self.errors.get(reason, 0) + count
        self.skipped_rows += count

    def __repr__(self):
        return "LoadResult(ok={0}, skipped={1}, errors={2})".format(
            self.ok_rows, self.skipped_rows, self.errors
        )


def open_text(path) -> Optional[str]:
    """ファイル全文を返す。どの文字コードでも読めなければ None (DS-LD-02)。

    BOM 付き UTF-8 を透過的に扱い、CRLF/LF をユニバーサル改行で吸収する。
    """
    for encoding in ("utf-8-sig", "cp932"):
        try:
            with open(str(path), "r", encoding=encoding, newline=None) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def parse_number(token) -> Tuple[bool, Optional[float]]:
    """数値を解釈する。`-` や空は正常な NULL として扱う (FR-015)。"""
    if token is None:
        return True, None
    text = str(token).strip()
    if text in _NULL_TOKENS:
        return True, None
    try:
        return True, float(text)
    except ValueError:
        return False, None


def parse_int(token) -> Tuple[bool, Optional[int]]:
    ok, value = parse_number(token)
    if not ok:
        return False, None
    if value is None:
        return True, None
    return True, int(value)


def parse_ts(token, fmt) -> Optional[datetime]:
    if token is None:
        return None
    try:
        return datetime.strptime(str(token).strip(), fmt)
    except ValueError:
        return None


#: CSV 上で NULL を表す番兵。実データに現れない文字列を選ぶ。
NULL_SENTINEL = "\\N"


def _sql_literal(value: str) -> str:
    """SQL のリテラルへ埋め込むための最小限のエスケープ (※CR-008)。

    タイムゾーン名は `pg_timezone_names()` で検証済みだが、**検証を通らない
    経路 (単体テストなど) でも壊れないように**ここでも単引用符を潰す。
    """
    return str(value).replace("'", "''")


class BatchInserter(object):
    """バッチ単位で一括挿入する挿入器 (DS-LD-04)。

    **実装方式**: 設計書 (DS-LD-04) は `executemany` を指定していたが、
    DuckDB の `executemany` は 1 行ずつ prepared statement を実行するため
    500〜600 行/秒しか出ず、NFR-002 (1,000万レコードで10分以内) を満たせない
    (Windows / Linux の双方で計測)。一時 CSV へ書き出して `read_csv` で
    一括投入する方式に変更したところ約 143,000 行/秒となり、240 倍高速化した。
    詳細は docs/ADR.md ADR-011 を参照。
    """

    def __init__(self, con, table, batch_size=BATCH_SIZE, cfg=None):
        self._con = con
        self._table = table
        self._columns = COLUMNS[table]
        self._types = COLUMN_TYPES[table]
        self._buffer = []
        self._batch_size = batch_size
        # ※CR-008 投入時のタイムゾーン変換。cfg を渡さなければ変換しない
        # (単体テストは検知器と同じく直接生成するため、この経路を保つ)。
        self._select = self._build_select(cfg)

    def _build_select(self, cfg):
        """read_csv からの取り出し方を決める (※CR-008)。

        **読み込み元と格納のタイムゾーンが同じなら `SELECT *` のままにする。**
        既定はすべて UTC であり、通常の構成では `AT TIME ZONE` を一度も発行しない
        (2026-09-06 の依頼者の指示)。
        """
        if cfg is None or not cfg.needs_conversion(self._table):
            return "*"
        src = cfg.source_timezone(self._table)
        dst = cfg.storage_timezone
        # 素朴な時刻を「src の時刻」と解釈し、「dst の素朴な時刻」へ直す。
        # 型は TIMESTAMP のまま変わらない (格納後は dst の時刻という意味になる)。
        converted = "(ts AT TIME ZONE '{0}') AT TIME ZONE '{1}' AS ts".format(
            _sql_literal(src), _sql_literal(dst))
        return ", ".join(
            converted if col == "ts" else col for col in self._columns)

    def add(self, values: dict):
        row = tuple(values.get(col) for col in self._columns[:-1]) + (next_load_seq(),)
        self._buffer.append(row)
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def _format(self, value):
        if value is None:
            return NULL_SENTINEL
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, float):
            return repr(value)
        return str(value)

    def flush(self):
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        fd, path = tempfile.mkstemp(suffix=".csv", prefix="s_anomaly_load_")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                for row in rows:
                    writer.writerow([self._format(v) for v in row])
            columns_spec = ", ".join(
                "'{0}': '{1}'".format(col, self._types[col]) for col in self._columns
            )
            self._con.execute(
                "INSERT INTO {0} ({1}) SELECT {3} FROM read_csv(?, header=false, "
                "columns={{{2}}}, nullstr=?)".format(
                    self._table, ", ".join(self._columns), columns_spec,
                    self._select
                ),
                [path, NULL_SENTINEL],
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def record_errors(con, file_path: str, errors: Dict[str, int]):
    """load_error テーブルへ書き込む。"""
    rows = []
    for reason in ALL_REASONS:
        count = errors.get(reason)
        if count:
            rows.append((file_path, reason, count))
    if rows:
        con.executemany("INSERT INTO load_error VALUES (?, ?, ?)", rows)


def dedupe_all(con) -> Dict[str, int]:
    """論理 PK が重複するレコードを、先に読んだほうを残して排除する (DS-LD-06)。"""
    dropped = {}
    for table, keys in LOGICAL_KEYS.items():
        before = con.execute("SELECT count(*) FROM {0}".format(table)).fetchone()[0]
        if before == 0:
            dropped[table] = 0
            continue
        partition = ", ".join(keys)
        con.execute(
            """
            CREATE OR REPLACE TABLE {t} AS
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY {p} ORDER BY _load_seq
                ) AS _rn
                FROM {t}
            ) WHERE _rn = 1
            """.format(t=table, p=partition)
        )
        after = con.execute("SELECT count(*) FROM {0}".format(table)).fetchone()[0]
        dropped[table] = before - after
    return dropped


def relative_path(path, root) -> str:
    """load_error に記録する相対パスを返す。"""
    try:
        return os.path.relpath(str(path), str(root))
    except ValueError:
        return str(path)
