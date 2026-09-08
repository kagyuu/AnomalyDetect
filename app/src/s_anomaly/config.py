"""M02 config — settings.properties の読み込みと検証 (P002 2章 / P003 3章)。

P002 2.2 の表を PARAMS として宣言的に持つ。ハードコードした if の羅列にしない。
検証は 2 段階 (単項 -> 相関) で行い、最初の 1 件で止めず全違反を集めて一括で出す。
"""

import configparser
import os
import re
from typing import Any, Dict, List, Optional

from .errors import ConfigError

STEP = "S2"

ALGORITHM_IDS = [
    "ALG-A1", "ALG-A2", "ALG-A3", "ALG-A4", "ALG-A5",
    "ALG-B1", "ALG-B2", "ALG-B3", "ALG-B4", "ALG-B5",
    "ALG-C1",   # ※CR-005 観点3: 上限への張り付き
]

_MEMORY_RE = re.compile(r"^\d+(\.\d+)?(KB|MB|GB|TB)$", re.IGNORECASE)


class Param(object):
    """1 つの設定キーの定義 (P002 2.2 の 1 行に対応)。"""

    def __init__(self, section, key, kind, default,
                 min_value=None, max_value=None,
                 min_exclusive=False, max_exclusive=False, note=""):
        self.section = section
        self.key = key
        self.kind = kind
        self.default = default
        self.min_value = min_value
        self.max_value = max_value
        self.min_exclusive = min_exclusive
        self.max_exclusive = max_exclusive
        self.note = note

    @property
    def full_key(self):
        return "{0}.{1}".format(self.section, self.key)

    def range_text(self):
        parts = []
        if self.min_value is not None:
            parts.append(
                "{0} より大きい".format(self.min_value) if self.min_exclusive
                else "{0} 以上".format(self.min_value)
            )
        if self.max_value is not None:
            parts.append(
                "{0} 未満".format(self.max_value) if self.max_exclusive
                else "{0} 以下".format(self.max_value)
            )
        if self.note:
            parts.append(self.note)
        if not parts:
            return "型 {0}".format(self.kind.__name__)
        return "型 {0} で、".format(self.kind.__name__) + "かつ".join(parts)


def _p(key, kind, default, **kw):
    return Param("parameters", key, kind, default, **kw)


#: ※CR-009 `chart.min_severity` に指定できる値。`events.SEVERITY_ORDER` と同じ並び。
#: **ここで import しないのは循環参照を避けるため。** 値の一致は単体テストで担保する。
CHART_SEVERITIES = ["INFO", "WARN", "FATAL", "SEVERE"]

#: ※CR-008 タイムゾーンの既定値。
#: **本アプリが使われることが想定されているシステムはすべて UTC である**
#: (2026-09-06 に依頼者が明示)。既定を UTC にしておけば、読み込み元と格納が
#: 一致するため**変換そのものが発生しない**。
DEFAULT_TIMEZONE = "UTC"

#: ※CR-008 タイムゾーンを個別に設定できる入力の種別。
#: `discovery` の KIND_* および格納先テーブル名と一致させる。
#: ※CR-010 で `sar` を追加した。**ここに無いと `[timezone] sar` が
#: 「未知のキー」として警告だけ出して無視され、時差のある環境で
#: 突き合わせが静かに全て外れる。**
SOURCE_KINDS = ["db_connection", "jvm_gc", "lsf_queue", "sar"]

#: ※CR-010 取り込む sar の活動種別。**既定は 8 種類すべて** (P001 FR-142)。
#: 名前は `loaders.sar.ACTIVITY_RULES` と一致させる。
#: ※CR-011 で `nfs` / `nfsd` を追加した (8 → 10)。
#: **ここに無いと、ローダが見出しを判別できても既定の絞り込みから外れ、
#: 警告も出ないまま読み飛ばされる。**
SAR_ACTIVITIES = ["cpu", "memory", "swap_space", "io",
                  "load", "swapping", "disk", "network",
                  "nfs", "nfsd"]


#: P002 2.2 の表の全行。件数を数え上げてハードコードしない。
PARAMS = (
    [Param("algorithms", alg, bool, True) for alg in ALGORITHM_IDS]
    + [
        _p("ALG-A1.window_minutes", int, 60, min_value=1, max_value=100000),
        _p("ALG-A1.k_warn", float, 2.0, min_value=0, min_exclusive=True),
        _p("ALG-A1.k_fatal", float, 3.0, min_value=0, min_exclusive=True),
        _p("ALG-A2.window_minutes", int, 60, min_value=1, max_value=100000),
        _p("ALG-A2.k", float, 3.0, min_value=0, min_exclusive=True),
        _p("ALG-A3.iqr_warn", float, 1.5, min_value=0, min_exclusive=True),
        _p("ALG-A3.iqr_fatal", float, 3.0, min_value=0, min_exclusive=True),
        _p("ALG-A4.lambda", float, 0.2, min_value=0, max_value=1, min_exclusive=True),
        _p("ALG-A4.k", float, 3.0, min_value=0, min_exclusive=True),
        _p("ALG-A5.z", float, 3.0, min_value=0, min_exclusive=True),
        _p("ALG-A5.min_weeks", int, 2, min_value=1),
        _p("ALG-B1.short_minutes", int, 60, min_value=1),
        _p("ALG-B1.long_minutes", int, 1440, min_value=1),
        _p("ALG-B1.min_run", int, 60, min_value=1),
        _p("ALG-B2.bucket_minutes", int, 60, min_value=1),
        _p("ALG-B2.p_warn", float, 0.05, min_value=0, max_value=1,
           min_exclusive=True, max_exclusive=True),
        _p("ALG-B2.p_fatal", float, 0.01, min_value=0, min_exclusive=True),
        _p("ALG-B2.max_buckets", int, 2000, min_value=100),
        _p("ALG-B3.window_minutes", int, 360, min_value=1),
        _p("ALG-B3.min_increases", int, 10, min_value=2),
        _p("ALG-B4.baseline_minutes", int, 1440, min_value=1),
        _p("ALG-B4.h", float, 5.0, min_value=0, min_exclusive=True),
        _p("ALG-B5.min_r2", float, 0.7, min_value=0, max_value=1),
        # ※CR-005 ALG-C1 (上限への張り付き)
        _p("ALG-C1.ratio_pct", float, 90.0, min_value=0, max_value=100,
           min_exclusive=True),
        _p("ALG-C1.min_minutes", float, 360.0, min_value=1),
        _p("ALG-C1.min_spread", float, 1.5, min_value=1.0),
        # ※CR-007 S6 のスレッド数。0 は自動 (min(8, 論理コア数))。
        _p("common.detect_threads", int, 0, min_value=0, max_value=64),
        _p("common.min_points", int, 30, min_value=2),
        _p("common.sigma_floor_ratio", float, 0.02, min_value=0, max_value=1),
        _p("common.sigma_floor_abs", float, 0.5, min_value=0),
        _p("events.severe_pct", float, 90.0, min_value=0, max_value=100,
           min_exclusive=True),
        _p("events.merge_gap_minutes", int, 60, min_value=0),
    ]
    + [
        Param("duckdb", "max_memory", str, "4GB",
              note="DuckDB が受理する容量表記 (例 4GB)"),
        Param("duckdb", "temp_directory", str, "./tmp",
              note="書き込み可能なパス"),
    ]
    # ※CR-008 タイムゾーン。**IANA の名前付きタイムゾーンのみ**を受け付ける。
    # 固定オフセット (+09:00) は DuckDB が解釈できない (Unknown TimeZone)。
    # 名前が実在するかは接続を開いたあと `validate_timezones` が検証する
    # (`pg_timezone_names()` を引くため、接続前には判定できない)。
    # ※CR-009 チャート。**既定は SEVERE のみ。** 30 日規模でイベントは 7,229 件
    # あり、全件に付けるとレポート一式が倍増する。
    + [
        Param("chart", "enabled", bool, True),
        # **FATAL を既定にする。** SEVERE は定義上「観点2 の検知」であり
        # (README 5章)、観点2 には同時アノマリーが付かない (CR-002)。
        # SEVERE 止まりにすると重ね合わせのチャートが一度も出ない。
        Param("chart", "min_severity", str, "FATAL",
              note="SEVERE / FATAL / WARN / INFO のいずれか"),
        Param("chart", "max_per_host", int, 100, min_value=0, max_value=10000),
        Param("chart", "max_overlay_per_host", int, 100,
              min_value=0, max_value=10000),
        Param("chart", "pad_ratio", float, 0.25, min_value=0.0, max_value=10.0),
    ]
    + [
        Param("timezone", "storage", str, DEFAULT_TIMEZONE,
              note="IANA のタイムゾーン名 (例 UTC, Asia/Tokyo)"),
    ]
    + [
        Param("timezone", kind, str, DEFAULT_TIMEZONE,
              note="IANA のタイムゾーン名 (例 UTC, Asia/Tokyo)")
        for kind in SOURCE_KINDS
    ]
    # ※CR-010 取り込む sar の活動種別。カンマ区切り。
    # **未知の語は警告して無視する** (P001 FR-149。終了コード 3 にしない)。
    # 空文字は「sar を取り込まない」を意味し、これもエラーではない。
    + [
        Param("sar", "activities", str, ",".join(SAR_ACTIVITIES),
              note="cpu,memory,swap_space,io,load,swapping,disk,network から選ぶ"),
    ]
)

PARAMS_BY_SECTION = {}
for _param in PARAMS:
    PARAMS_BY_SECTION.setdefault(_param.section, {})[_param.key] = _param

#: 相関検証: (左, 比較, 右, 説明)
CORRELATION_RULES = [
    ("ALG-A1.k_fatal", ">", "ALG-A1.k_warn", "k_fatal は k_warn より大きい必要があります"),
    ("ALG-A3.iqr_fatal", ">", "ALG-A3.iqr_warn", "iqr_fatal は iqr_warn より大きい必要があります"),
    ("ALG-B1.long_minutes", ">", "ALG-B1.short_minutes", "long_minutes は short_minutes より大きい必要があります"),
    ("ALG-B2.p_fatal", "<", "ALG-B2.p_warn", "p_fatal は p_warn より小さい必要があります"),
]


def _parse_activities(raw):
    """`[sar] activities` を (既知の活動のリスト, 未知の語のリスト) に分ける。

    ※CR-010。**大文字小文字を区別しない。** 空要素は捨てる。
    既知の語は `SAR_ACTIVITIES` の並び順に正規化して返す (NFR-009 の再現性)。
    """
    known, unknown = [], []
    for token in str(raw or "").split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name in SAR_ACTIVITIES:
            if name not in known:
                known.append(name)
        else:
            unknown.append(token.strip())
    known.sort(key=SAR_ACTIVITIES.index)
    return known, unknown


class Config(object):
    """検証済みの設定値。"""

    def __init__(self, values):
        # values: {(section, key): value}
        self._values = values

    def enabled_algorithms(self) -> List[str]:
        """有効なアルゴリズム ID を ID 昇順で返す。"""
        result = [a for a in ALGORITHM_IDS if self._values[("algorithms", a)]]
        result.sort()
        return result

    def all_disabled(self) -> bool:
        return not any(self._values[("algorithms", a)] for a in ALGORITHM_IDS)

    def param(self, key: str) -> Any:
        return self._values[("parameters", key)]

    @property
    def max_memory(self) -> str:
        return self._values[("duckdb", "max_memory")]

    @property
    def temp_directory(self) -> str:
        return self._values[("duckdb", "temp_directory")]

    # --- タイムゾーン (※CR-008) -----------------------------------------
    @property
    def storage_timezone(self) -> str:
        """DuckDB へ投入するときのタイムゾーン。"""
        return self._values[("timezone", "storage")]

    def source_timezone(self, kind: str) -> str:
        """入力の種別 (テーブル名) ごとの、読み込むデータのタイムゾーン。

        未知の種別は格納タイムゾーンとみなす。**変換しない**の意である。
        """
        return self._values.get(("timezone", kind), self.storage_timezone)

    def needs_conversion(self, kind: str) -> bool:
        """変換が要るか。

        **読み込み元と格納が同じなら変換しない** (※CR-008。2026-09-06 の指示)。
        既定はすべて UTC なので、通常の構成ではここが常に False になり、
        `AT TIME ZONE` を一度も発行しない。
        """
        return self.source_timezone(kind) != self.storage_timezone

    # --- チャート (※CR-009) --------------------------------------------
    @property
    def chart_enabled(self) -> bool:
        return self._values[("chart", "enabled")]

    @property
    def chart_min_severity(self) -> str:
        return self._values[("chart", "min_severity")]

    @property
    def chart_max_per_host(self) -> int:
        return self._values[("chart", "max_per_host")]

    @property
    def chart_max_overlay_per_host(self) -> int:
        """重ね合わせが描けるイベントに割り当てる別枠 (※CR-009)。"""
        return self._values[("chart", "max_overlay_per_host")]

    @property
    def chart_pad_ratio(self) -> float:
        return self._values[("chart", "pad_ratio")]

    # --- sar (※CR-010) --------------------------------------------------
    @property
    def sar_activities(self) -> frozenset:
        """取り込む sar の活動種別 (正規化済み)。

        **未知の語は捨てる。** 警告は `load` が出す (P001 FR-149)。
        空集合は「sar を取り込まない」を意味し、エラーではない。
        """
        raw = self._values.get(("sar", "activities"), "")
        return frozenset(_parse_activities(raw)[0])

    def timezone_names(self) -> Dict[str, str]:
        """検証のために、設定されている全タイムゾーン名を {表示名: 値} で返す。"""
        out = {"timezone.storage": self.storage_timezone}
        for kind in SOURCE_KINDS:
            out["timezone." + kind] = self.source_timezone(kind)
        return out

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._values)

    def algorithm_params(self, alg_id: str) -> Dict[str, Any]:
        """`ALG-A1.` で始まるパラメータを {短いキー: 値} で返す (report.md 2 章用)。"""
        prefix = alg_id + "."
        out = {}
        for (section, key), value in self._values.items():
            if section == "parameters" and key.startswith(prefix):
                out[key[len(prefix):]] = value
        return out


def _convert(param, raw, parser, section):
    """文字列を型変換する。失敗したら (None, 理由) を返す。"""
    if param.kind is bool:
        try:
            return parser.getboolean(section, param.key), None
        except ValueError:
            return None, "真偽値 (true/false, yes/no, on/off, 1/0) を指定してください"
    if param.kind is int:
        try:
            return int(raw.strip()), None
        except (ValueError, AttributeError):
            return None, "整数を指定してください"
    if param.kind is float:
        try:
            return float(raw.strip()), None
        except (ValueError, AttributeError):
            return None, "実数を指定してください"
    return raw.strip(), None


def _check_range(param, value):
    """範囲を検証する。違反していれば理由を返す。"""
    if param.min_value is not None:
        if param.min_exclusive:
            if not value > param.min_value:
                return param.range_text()
        elif not value >= param.min_value:
            return param.range_text()
    if param.max_value is not None:
        if param.max_exclusive:
            if not value < param.max_value:
                return param.range_text()
        elif not value <= param.max_value:
            return param.range_text()
    return None


def load(path: Optional[str], progress) -> Config:
    """settings.properties を読んで検証済みの Config を返す。"""
    values = {}
    for param in PARAMS:
        values[(param.section, param.key)] = param.default

    if path is None or not os.path.isfile(str(path)):
        progress.info(
            STEP, "settings.properties が見つかりません。既定値で動作します"
        )
        return Config(values)

    parser = configparser.ConfigParser()
    # キー名の大文字小文字を保持する (ALG-A1 のような ID を扱うため)
    parser.optionxform = str
    try:
        with open(str(path), "r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (configparser.Error, OSError) as exc:
        raise ConfigError(
            "settings.properties を読み込めません",
            "  ファイル: {0}\n  理由: {1}".format(path, exc),
        )

    progress.info(STEP, "設定ファイルを読み込みました: {0}".format(path))

    violations = []
    changed = []

    for section in parser.sections():
        known_in_section = PARAMS_BY_SECTION.get(section)
        if known_in_section is None:
            progress.warning(
                STEP, "未知のセクション [{0}] を無視しました".format(section)
            )
            continue
        for key in parser.options(section):
            param = known_in_section.get(key)
            if param is None:
                if section == "algorithms":
                    progress.warning(
                        STEP,
                        "[algorithms] の '{0}' はアルゴリズム ID として解釈されませんでした。"
                        "有効な ID は {1} です".format(key, ", ".join(ALGORITHM_IDS)),
                    )
                else:
                    progress.warning(
                        STEP, "未知のキー [{0}] {1} を無視しました".format(section, key)
                    )
                continue
            raw = parser.get(section, key)
            value, reason = _convert(param, raw, parser, section)
            if reason is not None:
                violations.append((section, key, raw, reason))
                continue
            if param.kind in (int, float):
                reason = _check_range(param, value)
                if reason is not None:
                    violations.append((section, key, raw, reason))
                    continue
            if section == "duckdb" and key == "max_memory":
                if not _MEMORY_RE.match(str(value)):
                    violations.append(
                        (section, key, raw,
                         "容量表記 (例: 4GB, 512MB) を指定してください")
                    )
                    continue
            if section == "sar" and key == "activities":
                # ※CR-010 未知の活動名は警告して無視する (P001 FR-149)。
                # **終了コード 3 にしない。** 設定の書き間違いで解析が止まる
                # ほうが害が大きく、取り込まれていないことは系列数から分かる。
                known, unknown = _parse_activities(value)
                for name in unknown:
                    progress.warning(
                        STEP,
                        "[sar] activities の '{0}' は活動種別として解釈されませんでした。"
                        "有効な名前は {1} です".format(name, ", ".join(SAR_ACTIVITIES)),
                    )
                if not known:
                    progress.warning(
                        STEP, "[sar] activities が空です。sar を取り込みません"
                    )
                value = ",".join(known)
            values[(section, key)] = value
            if value != param.default:
                changed.append("[{0}] {1} = {2}".format(section, key, value))

    # 相関検証は全キーの単項検証が終わったあとに行う (DS-02-02)
    for left, op, right, message in CORRELATION_RULES:
        lv = values.get(("parameters", left))
        rv = values.get(("parameters", right))
        if lv is None or rv is None:
            continue
        ok = (lv > rv) if op == ">" else (lv < rv)
        if not ok:
            violations.append(
                ("parameters", left, lv,
                 "{0} (現在の {1} = {2})".format(message, right, rv))
            )

    # ※CR-009 chart.min_severity は決められた値のいずれかでなければならない。
    # 数値の範囲検証では表現できないため、ここで個別に見る。
    min_sev = values.get(("chart", "min_severity"))
    if min_sev not in CHART_SEVERITIES:
        violations.append(
            ("chart", "min_severity", min_sev,
             "次のいずれかであること: " + " / ".join(CHART_SEVERITIES))
        )

    if violations:
        lines = []
        for section, key, raw, reason in violations:
            lines.append(
                "  [{0}] {1} = {2}\n  理由: {3}".format(section, key, raw, reason)
            )
        raise ConfigError(
            "settings.properties の設定値が不正です", "\n".join(lines)
        )

    if changed:
        progress.info(STEP, "既定値からの変更: " + ", ".join(changed))
    disabled = [a for a in ALGORITHM_IDS if not values[("algorithms", a)]]
    if disabled:
        progress.info(STEP, "無効なアルゴリズム: " + ", ".join(disabled))

    return Config(values)


def validate_timezones(con, cfg, progress):
    """設定されたタイムゾーン名が実在するかを検証する (※CR-008)。

    **接続を開いたあとに呼ぶ。** `pg_timezone_names()` を引くため、
    設定を読み込む時点 (S2) では判定できない。

    **固定オフセット (`+09:00`) は DuckDB が解釈できない**ため、ここで弾かれる。
    誤りは 1 件ずつではなく**まとめて全件**報告する (他の設定値の検証と同じ)。
    """
    try:
        known = set(
            row[0] for row in con.execute(
                "SELECT name FROM pg_timezone_names()").fetchall()
        )
    except Exception as exc:
        # タイムゾーン一覧を引けない DuckDB では検証できない。
        # **変換が要らない構成 (既定の全 UTC) なら、これでも支障は無い。**
        if not converted_kinds(cfg):
            progress.info(
                STEP, "タイムゾーンの検証を省略しました (変換は発生しません)")
            return
        raise ConfigError(
            "タイムゾーン名を検証できません",
            "  pg_timezone_names() を取得できませんでした: {0}\n"
            "  対処: [timezone] の値をすべて同じにすると、"
            "変換そのものが不要になり本検証も行いません".format(exc),
        )

    violations = []
    for label, value in sorted(cfg.timezone_names().items()):
        if value not in known:
            violations.append(
                "  [timezone] {0} = {1}\n"
                "  理由: IANA のタイムゾーン名ではありません"
                " (大文字小文字を区別します。固定オフセット +09:00 は使えません)"
                .format(label.split(".", 1)[1], value)
            )
    if violations:
        raise ConfigError(
            "settings.properties のタイムゾーン設定が不正です",
            "\n".join(violations),
        )

    converted = converted_kinds(cfg)
    if converted:
        progress.info(
            STEP,
            "投入時にタイムゾーンを変換します (格納 {0}): {1}".format(
                cfg.storage_timezone,
                ", ".join("{0} <- {1}".format(k, cfg.source_timezone(k))
                          for k in converted)),
        )
    else:
        progress.info(
            STEP,
            "タイムゾーンはすべて {0} です。変換は行いません".format(
                cfg.storage_timezone),
        )


def converted_kinds(cfg):
    """変換が必要な入力種別を返す (※CR-008)。すべて同じなら空になる。"""
    return [k for k in SOURCE_KINDS if cfg.needs_conversion(k)]
