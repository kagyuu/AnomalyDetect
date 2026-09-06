"""T001 — 取り込みパイプラインの連携 (discovery → 3 ローダ → 格納 → 重複排除)。"""

import io
import os
import shutil
import tempfile
import unittest

from s_anomaly import (
    bootstrap, discovery, loaders, progress as progress_mod, schema,
)
from s_anomaly.loaders import bqueues, dbconn, jstat
from tests.integration import _setup_baseline

FIXTURES = os.path.join(_setup_baseline.APP_DIR, "tests", "fixtures")


class TestIngestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup_baseline.restore()

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
        self.root = self._build_tree()

    def _build_tree(self):
        root = os.path.join(self._tmp.name, "root")
        layout = {
            "a": ["DBConnection_20260601.csv", "app01_gc_host1_20260601.txt"],
            "a/sub": [],
            "b": ["app02_gc_host2_20260601.txt", "bqueues_grid01_20260601.txt"],
            "c": ["DBConnection_20260601.csv"],  # a/ と同一内容 = 重複
        }
        for rel, names in layout.items():
            os.makedirs(os.path.join(root, rel), exist_ok=True)
            for name in names:
                shutil.copy(os.path.join(FIXTURES, name),
                            os.path.join(root, rel, name))
        with open(os.path.join(root, "b", "readme.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("対象外")
        return root

    def _load_all(self):
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
        }
        files = discovery.discover(self.root, self.progress)
        for logfile in files:
            loader[logfile.kind](self.con, logfile, self.progress)
        return files

    def count(self, table):
        return self.con.execute(
            "SELECT count(*) FROM {0}".format(table)).fetchone()[0]

    def test_01_discover_count_and_kinds(self):
        files = discovery.discover(self.root, self.progress)
        self.assertEqual(len(files), 5)
        kinds = {}
        for f in files:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        self.assertEqual(kinds[discovery.KIND_DBCONN], 2)
        self.assertEqual(kinds[discovery.KIND_JVMGC], 2)
        self.assertEqual(kinds[discovery.KIND_LSF], 1)

    def test_03_jvm_metadata(self):
        files = discovery.discover(self.root, self.progress)
        gc = sorted((f.container, f.host) for f in files
                    if f.kind == discovery.KIND_JVMGC)
        self.assertEqual(gc, [("app01", "host1"), ("app02", "host2")])

    def test_04_05_06_dedupe_first_wins(self):
        self._load_all()
        self.assertEqual(self.count("db_connection"), 4)  # 2 行 x 2 ファイル
        before = self.con.execute(
            "SELECT min(_load_seq) FROM db_connection").fetchone()[0]
        loaders.dedupe_all(self.con)
        self.assertEqual(self.count("db_connection"), 2)
        after = self.con.execute(
            "SELECT min(_load_seq) FROM db_connection").fetchone()[0]
        self.assertEqual(before, after)

    def test_07_08_gc_formats_and_capacity(self):
        self._load_all()
        loaders.dedupe_all(self.con)
        rows = dict(self.con.execute(
            "SELECT container, any_value(gc_format) FROM jvm_gc GROUP BY 1"
        ).fetchall())
        self.assertEqual(rows["app01"], "gcutil")
        self.assertEqual(rows["app02"], "gc")
        oc_gcutil = self.con.execute(
            "SELECT oc FROM jvm_gc WHERE container='app01' LIMIT 1").fetchone()[0]
        oc_gc = self.con.execute(
            "SELECT oc FROM jvm_gc WHERE container='app02' LIMIT 1").fetchone()[0]
        self.assertIsNone(oc_gcutil)
        self.assertIsNotNone(oc_gc)

    def test_09_wide_to_long(self):
        self._load_all()
        loaders.dedupe_all(self.con)
        # 3 データ行 x QUEUE 2 個
        self.assertEqual(self.count("lsf_queue"), 6)

    def test_10_no_load_errors(self):
        self._load_all()
        self.assertEqual(self.count("load_error"), 0)

    def test_11_discover_is_stable(self):
        first = [f.path for f in discovery.discover(self.root, self.progress)]
        second = [f.path for f in discovery.discover(self.root, self.progress)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
