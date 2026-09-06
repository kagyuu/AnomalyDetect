"""M06 loaders.bqueues — LSF キュー状態の解析 (P003 5.4)。

横持ち(wide) -> 縦持ち(long) へ変換する。
時刻の末尾 `:SS`(1/100 秒)は切り捨てる (P001 4.3、人間に確認済みの確定仕様)。
"""

import csv
import io
import re

from . import (
    REASON_COLUMNS, REASON_DATE, REASON_HEADER, REASON_NUMBER,
    BatchInserter, LoadResult, open_text, parse_int, parse_ts,
)

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
FIELDS = ("NJOBS", "PEND", "RUN", "SUSP")

_HEADER_RE = re.compile(r"^(?P<queue>.+)_(?P<field>NJOBS|PEND|RUN|SUSP)$")
# `-` 区切りと `/` 区切りの両方を受理し、末尾の 1/100 秒を切り落とす
_TS_RE = re.compile(
    r"^\s*(\d{4}[-/]\d{2}[-/]\d{2})[ T](\d{2}:\d{2}:\d{2})(?::\d+)?\s*$"
)


def normalize_ts_token(token):
    """`2026-06-01 00:00:00:02` -> `2026-06-01 00:00:00`。合わなければ None。"""
    if token is None:
        return None
    m = _TS_RE.match(str(token))
    if not m:
        return None
    return "{0} {1}".format(m.group(1).replace("/", "-"), m.group(2))


def parse_header(header_row):
    """見出し行から {QUEUE 名: {field: 列index}} を作る (DS-06-01)。"""
    mapping = {}
    for index, name in enumerate(header_row):
        text = name.strip()
        if index == 0 or text.lower() == "time":
            continue
        m = _HEADER_RE.match(text)
        if not m:
            continue
        mapping.setdefault(m.group("queue"), {})[m.group("field")] = index
    return mapping


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

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return result

    mapping = parse_header(rows[0])
    if not mapping:
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "見出しから QUEUE 名を抽出できません: {0}".format(logfile.path),
        )
        return result

    header_len = len(rows[0])
    inserter = BatchInserter(con, "lsf_queue", cfg=cfg)
    for row in rows[1:]:
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        if len(row) != header_len:
            result.add_error(REASON_COLUMNS)
            continue
        token = normalize_ts_token(row[0])
        if token is None:
            result.add_error(REASON_DATE)
            continue
        ts = parse_ts(token, TS_FORMAT)
        if ts is None:
            result.add_error(REASON_DATE)
            continue

        bad_number = False
        pending = []
        for queue in sorted(mapping):
            values = {}
            for field in FIELDS:
                index = mapping[queue].get(field)
                if index is None:
                    values[field] = None
                    continue
                ok, value = parse_int(row[index])
                if not ok:
                    bad_number = True
                    break
                values[field] = value
            if bad_number:
                break
            pending.append((queue, values))
        if bad_number:
            result.add_error(REASON_NUMBER)
            continue

        for queue, values in pending:
            inserter.add({
                "ts": ts,
                "host": logfile.host,
                "queue": queue,
                "njobs": values["NJOBS"],
                "pend": values["PEND"],
                "run": values["RUN"],
                "susp": values["SUSP"],
            })
        result.ok_rows += 1
    inserter.flush()
    return result
