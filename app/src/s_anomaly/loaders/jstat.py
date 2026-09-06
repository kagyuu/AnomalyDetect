"""M05 loaders.jstat — JavaEE の GC 統計の解析 (P003 5.3)。

`-gc` と `-gcutil` の 2 形式を自動判別して両方を受け付ける (FR-012)。

**データ行の値の個数を見出しより優先して判定する** (ADR-004)。
docs/P000-concept-analysis.md の実サンプルは「見出しは -gc 系(17列)、
データは -gcutil(12個)」という食い違いを持ち、見出しを信じると
ファイルが丸ごと失われるため。
"""

from typing import Optional

from . import (
    REASON_COLUMNS, REASON_DATE, REASON_HEADER, REASON_NUMBER,
    BatchInserter, LoadResult, open_text, parse_int, parse_number, parse_ts,
)

TS_FORMAT = "%Y/%m/%d %H:%M:%S"

FORMAT_GC = "gc"
FORMAT_GCUTIL = "gcutil"

# Timestamp を除く列(先頭の time は 2 トークンで別扱い)
COLS_GCUTIL = ["S0", "S1", "E", "O", "M", "CCS",
               "YGC", "YGCT", "FGC", "FGCT", "GCT"]
COLS_GC_18 = ["S0C", "S1C", "S0U", "S1U", "EC", "EU", "OC", "OU",
              "MC", "MU", "CCSC", "CCSU",
              "YGC", "YGCT", "FGC", "FGCT", "GCT"]
COLS_GC_17 = ["S0C", "S1C", "S0U", "S1U", "EC", "EU", "OC", "OU",
              "MC", "MU", "CCSC",
              "YGC", "YGCT", "FGC", "FGCT", "GCT"]

# Timestamp + 上記 -> 値の個数
COUNT_GCUTIL = 1 + len(COLS_GCUTIL)   # 12
COUNT_GC_18 = 1 + len(COLS_GC_18)     # 18
COUNT_GC_17 = 1 + len(COLS_GC_17)     # 17

# 値の列名 -> jvm_gc テーブルの列名
MAP_GCUTIL = {
    "S0": "s0u", "S1": "s1u", "E": "eu", "O": "ou", "M": "mu", "CCS": "ccsu",
    "YGC": "ygc", "YGCT": "ygct", "FGC": "fgc", "FGCT": "fgct", "GCT": "gct",
}
MAP_GC = {
    "S0C": "s0c", "S1C": "s1c", "S0U": "s0u", "S1U": "s1u",
    "EC": "ec", "EU": "eu", "OC": "oc", "OU": "ou",
    "MC": "mc", "MU": "mu", "CCSC": "ccsc", "CCSU": "ccsu",
    "YGC": "ygc", "YGCT": "ygct", "FGC": "fgc", "FGCT": "fgct", "GCT": "gct",
}

INT_COLUMNS = ("ygc", "fgc")


def _value_count(tokens):
    """時刻 2 トークンを 1 つに結合したあとの値の個数を返す。"""
    return len(tokens) - 2


def detect_format(header_tokens, first_data_count: Optional[int]) -> Optional[str]:
    """形式を判別する (DS-05-01)。決まらなければ None。

    1. データ行の値の個数を優先する
    2. 決まらない場合のみ見出しの列名集合で判定する
    """
    if first_data_count == COUNT_GCUTIL:
        return FORMAT_GCUTIL
    if first_data_count in (COUNT_GC_17, COUNT_GC_18):
        return FORMAT_GC

    upper = set(t.strip().upper() for t in header_tokens)
    if "S0C" in upper or "S1C" in upper or "S0U" in upper:
        return FORMAT_GC
    if "CCS" in upper and "S0" in upper and "O" in upper:
        return FORMAT_GCUTIL
    return None


def _columns_for(fmt, value_count):
    """値の個数に対応する列名リスト(Timestamp を除く)を返す。"""
    if fmt == FORMAT_GCUTIL:
        return COLS_GCUTIL if value_count == COUNT_GCUTIL else None
    if value_count == COUNT_GC_18:
        return COLS_GC_18
    if value_count == COUNT_GC_17:
        return COLS_GC_17
    return None


def load(con, logfile, progress, cfg=None) -> LoadResult:
    result = LoadResult()
    text = open_text(logfile.path)
    if text is None:
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "UTF-8 でも CP932 でもデコードできません: {0}".format(logfile.path),
        )
        return result

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return result

    header_tokens = lines[0].split()
    first_count = None
    for line in lines[1:]:
        tokens = line.split()
        if len(tokens) >= 3:
            first_count = _value_count(tokens)
            break

    fmt = detect_format(header_tokens, first_count)
    if fmt is None:
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "jstat の形式を判別できません(見出しの列数 {0}, データの値数 {1}): {2}".format(
                len(header_tokens), first_count, logfile.path
            ),
        )
        return result

    columns = _columns_for(fmt, first_count)
    if columns is None:
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "jstat の列構成に対応できません(形式 {0}, 値数 {1}): {2}".format(
                fmt, first_count, logfile.path
            ),
        )
        return result

    mapping = MAP_GCUTIL if fmt == FORMAT_GCUTIL else MAP_GC
    expected = len(columns) + 1  # Timestamp を含む値の個数
    inserter = BatchInserter(con, "jvm_gc", cfg=cfg)

    for line in lines[1:]:
        tokens = line.split()
        if len(tokens) < 3:
            result.add_error(REASON_COLUMNS)
            continue
        if _value_count(tokens) != expected:
            result.add_error(REASON_COLUMNS)
            continue
        ts = parse_ts("{0} {1}".format(tokens[0], tokens[1]), TS_FORMAT)
        if ts is None:
            result.add_error(REASON_DATE)
            continue

        ok_uptime, uptime = parse_number(tokens[2])
        if not ok_uptime:
            result.add_error(REASON_NUMBER)
            continue

        row = {
            "ts": ts,
            "container": logfile.container,
            "host": logfile.host,
            "jvm_uptime_sec": uptime,
            "gc_format": fmt,
        }
        bad = False
        for offset, col_name in enumerate(columns):
            token = tokens[3 + offset]
            target = mapping[col_name]
            if target in INT_COLUMNS:
                ok, value = parse_int(token)
            else:
                ok, value = parse_number(token)
            if not ok:
                bad = True
                break
            row[target] = value
        if bad:
            result.add_error(REASON_NUMBER)
            continue

        inserter.add(row)
        result.ok_rows += 1

    inserter.flush()
    return result
