"""T002 — 生成データの読み取り可能性 (生成ツールとローダの形式解釈の一致)。"""

import io
import os
import sys
import tempfile
import unittest

from s_anomaly import (
    bootstrap, discovery, loaders, progress as progress_mod, schema,
)
from s_anomaly.loaders import bqueues, dbconn, jstat
from tests.integration import _setup_baseline

sys.path.insert(0, os.path.join(_setup_baseline.APP_DIR, "tools"))
import gen_testdata  # noqa: E402


class LoadableBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()

    def setUp(self):
        self.progress = progress_mod.setup(io.StringIO(), io.StringIO())
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "512MB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        self.addCleanup(self.con.close)
        schema.create_all(self.con)

    def load_all(self, logs_dir):
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
        }
        results = {}
        for logfile in discovery.discover(logs_dir, self.progress):
            results[os.path.basename(logfile.path)] = loader[logfile.kind](
                self.con, logfile, self.progress)
        return results

    def count(self, table):
        return self.con.execute(
            "SELECT count(*) FROM {0}".format(table)).fetchone()[0]


class TestNormalLoadable(LoadableBase):
    def test_01_02_03_no_errors(self):
        results = self.load_all(os.path.join(self.paths["normal"], "logs"))
        self.assertGreater(len(results), 0)
        for name, result in results.items():
            self.assertEqual(result.errors, {}, name)
            self.assertEqual(result.skipped_rows, 0, name)
            self.assertGreater(result.ok_rows, 0, name)

    def test_04_no_load_error_rows(self):
        self.load_all(os.path.join(self.paths["normal"], "logs"))
        self.assertEqual(self.count("load_error"), 0)

    def test_05_both_gc_formats(self):
        self.load_all(os.path.join(self.paths["normal"], "logs"))
        formats = sorted(r[0] for r in self.con.execute(
            "SELECT DISTINCT gc_format FROM jvm_gc").fetchall())
        self.assertEqual(formats, ["gc", "gcutil"])

    def test_06_all_tables_populated(self):
        self.load_all(os.path.join(self.paths["normal"], "logs"))
        for table in ("db_connection", "jvm_gc", "lsf_queue"):
            self.assertGreater(self.count(table), 0, table)

    def test_07_period(self):
        self.load_all(os.path.join(self.paths["normal"], "logs"))
        lo, hi = self.con.execute(
            "SELECT min(ts), max(ts) FROM db_connection").fetchone()
        self.assertEqual(lo.strftime("%Y-%m-%d"), "2026-06-01")
        self.assertEqual(hi.strftime("%Y-%m-%d"), "2026-06-14")


class TestBrokenLoadable(LoadableBase):
    def test_08_09_matches_expectations(self):
        results = self.load_all(os.path.join(self.paths["broken"], "logs"))
        for name, want in gen_testdata.BROKEN_EXPECTATIONS.items():
            self.assertIn(name, results, name)
            got = results[name]
            self.assertEqual(got.ok_rows, want["ok_rows"], name)
            self.assertEqual(got.errors, want["errors"], name)

    def test_10_reason_codes_within_four(self):
        results = self.load_all(os.path.join(self.paths["broken"], "logs"))
        seen = set()
        for result in results.values():
            seen.update(result.errors)
        self.assertTrue(seen.issubset(set(loaders.ALL_REASONS)), seen)

    def test_11_some_valid_records(self):
        self.load_all(os.path.join(self.paths["broken"], "logs"))
        total = sum(self.count(t) for t in
                    ("db_connection", "jvm_gc", "lsf_queue"))
        self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()
