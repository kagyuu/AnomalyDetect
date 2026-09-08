"""T003 — metrics 構築の連携 (ローダ -> metrics)。"""

import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import (
    bootstrap, discovery, loaders, metrics, progress as progress_mod, schema,
)
from s_anomaly.loaders import bqueues, dbconn, jstat, sar
from tests.integration import _setup_baseline

EXPECTED_METRICS = sorted(metrics.DETECTABLE_METRICS + metrics.RATIO_METRICS)


class TestMetricsBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()

    def setUp(self):
        self.progress = progress_mod.setup(io.StringIO(), io.StringIO())
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "1GB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        self.addCleanup(self.con.close)
        schema.create_all(self.con)
        self._load(os.path.join(self.paths["normal"], "logs"))
        self._add_restart_series()
        loaders.dedupe_all(self.con)
        metrics.build_jvm_gc_derived(self.con)
        metrics.build_metrics(self.con)

    def _load(self, logs_dir):
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
            discovery.KIND_SAR: sar.load,      # ※CR-010
        }
        for logfile in discovery.discover(logs_dir, self.progress):
            loader[logfile.kind](self.con, logfile, self.progress)

    def _add_restart_series(self):
        """JVM 再起動を含む系列を追加する (生成データには含まれない)。"""
        base = datetime(2026, 6, 1)
        uptimes = [1000.0 * (i + 1) for i in range(10)] + \
                  [500.0 + 300.0 * i for i in range(10)]
        fgcs = list(range(10)) + list(range(10))
        inserter = loaders.BatchInserter(self.con, "jvm_gc")
        for i in range(20):
            inserter.add({
                "ts": base + timedelta(minutes=5 * i),
                "container": "restart01", "host": "hostR",
                "jvm_uptime_sec": uptimes[i], "gc_format": "gcutil",
                "s0u": 10.0, "s1u": 10.0, "eu": 30.0, "ou": 50.0,
                "mu": 60.0, "ccsu": 70.0,
                "ygc": i, "ygct": 0.1 * i, "fgc": fgcs[i],
                "fgct": 0.5 * fgcs[i], "gct": 0.1 * i + 0.5 * fgcs[i],
            })
        inserter.flush()

    def q(self, sql, params=None):
        return self.con.execute(sql, params or []).fetchall()

    def test_01_metric_kinds(self):
        got = sorted(r[0] for r in self.q("SELECT DISTINCT metric FROM metrics"))
        self.assertEqual(got, EXPECTED_METRICS)
        # ※CR-010 で 14 + 22 = 36、※CR-011 で NFS の 14 件を足して 50
        self.assertEqual(len(got), 50)

    def test_02_detectable_metrics(self):
        # ※CR-010 で 11 + 22 = 33、※CR-011 で NFS の 14 件を足して 47
        self.assertEqual(len(metrics.DETECTABLE_METRICS), 47)
        for name in metrics.RATIO_METRICS:
            self.assertNotIn(name, metrics.DETECTABLE_METRICS)

    def test_03_list_series_excludes_pct(self):
        got = set(s.metric for s in metrics.list_series(self.con))
        for name in metrics.RATIO_METRICS:
            self.assertNotIn(name, got)

    def test_04_series_id_formats(self):
        ids = set(r[0] for r in self.q("SELECT DISTINCT series_id FROM metrics"))
        self.assertTrue(any(i.startswith("db_connection/") and ":" in i for i in ids))
        self.assertTrue(any(i.startswith("jvm_gc/") and "@" in i for i in ids))
        self.assertTrue(any(i.startswith("lsf_queue/") for i in ids))

    def test_05_gcutil_ou_equals_pct(self):
        rows = self.q(
            "SELECT a.value, b.value FROM metrics a JOIN metrics b"
            " ON a.ts=b.ts AND a.series_id=b.series_id"
            " WHERE a.series_id='jvm_gc/app01@host01'"
            "   AND a.metric='ou' AND b.metric='ou_pct' LIMIT 50")
        self.assertTrue(rows)
        for ou, pct in rows:
            self.assertAlmostEqual(ou, pct, places=6)

    def test_06_07_gc_units_differ(self):
        rows = self.q(
            "SELECT a.value, b.value FROM metrics a JOIN metrics b"
            " ON a.ts=b.ts AND a.series_id=b.series_id"
            " WHERE a.series_id='jvm_gc/app02@host02'"
            "   AND a.metric='ou' AND b.metric='ou_pct' LIMIT 50")
        self.assertTrue(rows)
        for ou, pct in rows:
            self.assertGreater(ou, 100.0)
            self.assertLessEqual(pct, 100.0)
            self.assertAlmostEqual(pct, ou / 20480.0 * 100.0, places=6)

    def test_08_no_null_values(self):
        self.assertEqual(
            self.q("SELECT count(*) FROM metrics WHERE value IS NULL")[0][0], 0)

    def test_09_10_restart_splits_and_nulls_delta(self):
        segs = [r[0] for r in self.q(
            "SELECT segment FROM jvm_gc_derived WHERE container='restart01'"
            " ORDER BY ts")]
        self.assertEqual(segs, [0] * 10 + [1] * 10)
        delta = self.q(
            "SELECT fgc_delta FROM jvm_gc_derived WHERE container='restart01'"
            " ORDER BY ts")
        self.assertIsNone(delta[10][0])
        self.assertEqual(delta[11][0], 1)

    def test_11_normal_delta(self):
        delta = self.q(
            "SELECT fgc_delta FROM jvm_gc_derived WHERE container='restart01'"
            " ORDER BY ts")
        self.assertEqual(delta[5][0], 1)

    def test_12_interval(self):
        intervals = set(s.interval_sec for s in metrics.list_series(self.con)
                        if s.series_id.startswith("db_connection/"))
        self.assertEqual(intervals, {300.0})

    def test_13_metrics_is_table(self):
        kind = self.q("SELECT table_type FROM information_schema.tables"
                      " WHERE table_name='metrics'")[0][0]
        self.assertEqual(kind.upper(), "BASE TABLE")

    def test_14_rebuild_stable(self):
        first = self.q("SELECT count(*) FROM metrics")[0][0]
        order1 = [(s.series_id, s.metric) for s in metrics.list_series(self.con)]
        metrics.build_metrics(self.con)
        self.assertEqual(self.q("SELECT count(*) FROM metrics")[0][0], first)
        order2 = [(s.series_id, s.metric) for s in metrics.list_series(self.con)]
        self.assertEqual(order1, order2)


if __name__ == "__main__":
    unittest.main()
