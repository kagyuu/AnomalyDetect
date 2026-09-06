"""U001-T3 の単体テスト: schema。"""

import io
import tempfile
import unittest

from s_anomaly import bootstrap, progress as progress_mod, schema

EXPECTED_COLUMNS = {
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
    "load_error": ["file_path", "reason", "line_count"],
}


def open_con():
    progress = progress_mod.setup(io.StringIO(), io.StringIO())
    tmp = tempfile.TemporaryDirectory()
    module, _ = bootstrap.load_duckdb(tmp.name, progress)
    con = bootstrap.open_connection(module, "512MB", tmp.name + "/tmp", progress)
    return con, tmp


class TestCreateAll(unittest.TestCase):
    def setUp(self):
        self.con, self._tmp = open_con()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.con.close)

    def test_tables_exist_and_empty(self):
        schema.create_all(self.con)
        for table in EXPECTED_COLUMNS:
            rows = self.con.execute("SELECT * FROM {0}".format(table)).fetchall()
            self.assertEqual(rows, [], table)

    def test_columns_match_spec(self):
        schema.create_all(self.con)
        for table, expected in EXPECTED_COLUMNS.items():
            got = [r[0] for r in self.con.execute("DESCRIBE {0}".format(table)).fetchall()]
            self.assertEqual(got, expected, table)

    def test_idempotent(self):
        schema.create_all(self.con)
        schema.create_all(self.con)  # 2 回目が例外にならないこと (O-01)
        for table in EXPECTED_COLUMNS:
            self.con.execute("SELECT * FROM {0}".format(table)).fetchall()

    def test_logical_keys_reference_existing_columns(self):
        schema.create_all(self.con)
        for table, keys in schema.LOGICAL_KEYS.items():
            cols = set(EXPECTED_COLUMNS[table])
            for key in keys:
                self.assertIn(key, cols, "{0}.{1}".format(table, key))

    def test_columns_constant_matches_ddl(self):
        for table, cols in schema.COLUMNS.items():
            self.assertEqual(cols, EXPECTED_COLUMNS[table], table)

    def test_numeric_columns_accept_null(self):
        schema.create_all(self.con)
        self.con.execute(
            "INSERT INTO db_connection VALUES (TIMESTAMP '2026-06-01 00:00:00',"
            " 'h', 7003, 'ds', NULL, 1)"
        )
        got = self.con.execute("SELECT active_connections FROM db_connection").fetchone()
        self.assertIsNone(got[0])

    def test_jvm_gc_accepts_all_null_numerics(self):
        schema.create_all(self.con)
        # ts/container/host/jvm_uptime_sec/gc_format と _load_seq を除く数値列 17 個
        placeholders = ", ".join(["NULL"] * 17)
        self.con.execute(
            "INSERT INTO jvm_gc VALUES (TIMESTAMP '2026-06-01 00:00:00',"
            " 'c', 'h', NULL, 'gcutil', {0}, 1)".format(placeholders)
        )
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM jvm_gc").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
