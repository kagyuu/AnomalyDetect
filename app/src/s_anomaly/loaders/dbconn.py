"""M04 loaders.dbconn — DB コネクションログ (CSV) の解析 (P003 5.2)。"""

import csv
import io

from . import (
    REASON_COLUMNS, REASON_DATE, REASON_HEADER, REASON_NUMBER,
    BatchInserter, LoadResult, open_text, parse_int, parse_ts,
)

TS_FORMAT = "%Y/%m/%d %H:%M:%S"
EXPECTED_COLUMNS = 5


def load(con, logfile, progress, cfg=None) -> LoadResult:
    result = LoadResult()
    text = open_text(logfile.path)
    if text is None:
        # 文字コードが判別できない = 内容から列構成を判別できない (DS-LD-02)
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "UTF-8 でも CP932 でもデコードできません: {0}".format(logfile.path),
        )
        return result

    reader = csv.reader(io.StringIO(text))
    inserter = BatchInserter(con, "db_connection", cfg=cfg)
    for index, row in enumerate(reader):
        if index == 0:
            # 見出し行。列名の検証は行わない (列順が固定である前提)
            continue
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        if len(row) != EXPECTED_COLUMNS:
            result.add_error(REASON_COLUMNS)
            continue
        ts = parse_ts(row[0], TS_FORMAT)
        if ts is None:
            result.add_error(REASON_DATE)
            continue
        ok_port, port = parse_int(row[2])
        if not ok_port:
            result.add_error(REASON_NUMBER)
            continue
        ok_conn, conn = parse_int(row[4])
        if not ok_conn:
            result.add_error(REASON_NUMBER)
            continue
        inserter.add({
            "ts": ts,
            "host": row[1].strip(),
            "port": port,
            "datasource": row[3].strip(),
            "active_connections": conn,
        })
        result.ok_rows += 1
    inserter.flush()
    return result
