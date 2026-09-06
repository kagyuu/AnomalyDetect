"""DuckDB のスキーマ定義 (P002 6.2)。

永続化しないため、制約は張らず「論理 PK」として LOGICAL_KEYS に持つ。
制約で INSERT が失敗すると、破損行を読み飛ばす方針 (FR-014) と衝突するため。
"""

DDL = {
    "db_connection": """
        CREATE TABLE IF NOT EXISTS db_connection (
            ts TIMESTAMP,
            host VARCHAR,
            port INTEGER,
            datasource VARCHAR,
            active_connections BIGINT,
            _load_seq BIGINT
        )
    """,
    "jvm_gc": """
        CREATE TABLE IF NOT EXISTS jvm_gc (
            ts TIMESTAMP,
            container VARCHAR,
            host VARCHAR,
            jvm_uptime_sec DOUBLE,
            gc_format VARCHAR,
            s0c DOUBLE, s1c DOUBLE, s0u DOUBLE, s1u DOUBLE,
            ec DOUBLE, eu DOUBLE, oc DOUBLE, ou DOUBLE,
            mc DOUBLE, mu DOUBLE, ccsc DOUBLE, ccsu DOUBLE,
            ygc BIGINT, ygct DOUBLE, fgc BIGINT, fgct DOUBLE, gct DOUBLE,
            _load_seq BIGINT
        )
    """,
    "lsf_queue": """
        CREATE TABLE IF NOT EXISTS lsf_queue (
            ts TIMESTAMP,
            host VARCHAR,
            queue VARCHAR,
            njobs BIGINT,
            pend BIGINT,
            run BIGINT,
            susp BIGINT,
            _load_seq BIGINT
        )
    """,
    "load_error": """
        CREATE TABLE IF NOT EXISTS load_error (
            file_path VARCHAR,
            reason VARCHAR,
            line_count BIGINT
        )
    """,
}

#: 重複排除 (DS-LD-06) に使う論理主キー。
LOGICAL_KEYS = {
    "db_connection": ["ts", "host", "port", "datasource"],
    "jvm_gc": ["ts", "container", "host"],
    "lsf_queue": ["ts", "host", "queue"],
}

#: 列 -> DuckDB の型。一括挿入 (read_csv 経由) で列型を明示するために使う。
COLUMN_TYPES = {
    "db_connection": {
        "ts": "TIMESTAMP", "host": "VARCHAR", "port": "INTEGER",
        "datasource": "VARCHAR", "active_connections": "BIGINT",
        "_load_seq": "BIGINT",
    },
    "jvm_gc": {
        "ts": "TIMESTAMP", "container": "VARCHAR", "host": "VARCHAR",
        "jvm_uptime_sec": "DOUBLE", "gc_format": "VARCHAR",
        "s0c": "DOUBLE", "s1c": "DOUBLE", "s0u": "DOUBLE", "s1u": "DOUBLE",
        "ec": "DOUBLE", "eu": "DOUBLE", "oc": "DOUBLE", "ou": "DOUBLE",
        "mc": "DOUBLE", "mu": "DOUBLE", "ccsc": "DOUBLE", "ccsu": "DOUBLE",
        "ygc": "BIGINT", "ygct": "DOUBLE", "fgc": "BIGINT", "fgct": "DOUBLE",
        "gct": "DOUBLE", "_load_seq": "BIGINT",
    },
    "lsf_queue": {
        "ts": "TIMESTAMP", "host": "VARCHAR", "queue": "VARCHAR",
        "njobs": "BIGINT", "pend": "BIGINT", "run": "BIGINT", "susp": "BIGINT",
        "_load_seq": "BIGINT",
    },
}

#: 各テーブルへ INSERT する列 (`_load_seq` を含む)。
COLUMNS = {
    "db_connection": [
        "ts", "host", "port", "datasource", "active_connections", "_load_seq",
    ],
    "jvm_gc": [
        "ts", "container", "host", "jvm_uptime_sec", "gc_format",
        "s0c", "s1c", "s0u", "s1u", "ec", "eu", "oc", "ou",
        "mc", "mu", "ccsc", "ccsu",
        "ygc", "ygct", "fgc", "fgct", "gct", "_load_seq",
    ],
    "lsf_queue": [
        "ts", "host", "queue", "njobs", "pend", "run", "susp", "_load_seq",
    ],
}


def create_all(con):
    """4 テーブルを作成する。冪等 (2 回呼んでも失敗しない)。"""
    for ddl in DDL.values():
        con.execute(ddl)
