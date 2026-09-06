"""U004 の単体テスト: metrics。"""

import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import bootstrap, metrics, progress as progress_mod, schema
from s_anomaly.loaders import BatchInserter

EXPECTED_METRICS = sorted(metrics.DETECTABLE_METRICS + metrics.RATIO_METRICS)


class MetricsFixture(unittest.TestCase):
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

    def add_jvm(self, rows):
        ins = BatchInserter(self.con, "jvm_gc")
        for r in rows:
            ins.add(r)
        ins.flush()

    def add_db(self, rows):
        ins = BatchInserter(self.con, "db_connection")
        for r in rows:
            ins.add(r)
        ins.flush()

    def add_lsf(self, rows):
        ins = BatchInserter(self.con, "lsf_queue")
        for r in rows:
            ins.add(r)
        ins.flush()

    def build(self):
        metrics.build_jvm_gc_derived(self.con)
        metrics.build_metrics(self.con)

    def q(self, sql, params=None):
        return self.con.execute(sql, params or []).fetchall()


def gcutil_row(ts, ou, eu=30.0, mu=50.0, uptime=1000.0, ygc=1, ygct=0.1,
               fgc=0, fgct=0.0, container="app01", host="h1"):
    return {
        "ts": ts, "container": container, "host": host,
        "jvm_uptime_sec": uptime, "gc_format": "gcutil",
        "s0u": 10.0, "s1u": 10.0, "eu": eu, "ou": ou, "mu": mu, "ccsu": 60.0,
        "ygc": ygc, "ygct": ygct, "fgc": fgc, "fgct": fgct, "gct": ygct + fgct,
    }


def gc_row(ts, ou_kb, oc_kb=20480.0, container="app02", host="h2",
           uptime=1000.0, ygc=1, ygct=0.1, fgc=0, fgct=0.0):
    return {
        "ts": ts, "container": container, "host": host,
        "jvm_uptime_sec": uptime, "gc_format": "gc",
        "s0c": 1024.0, "s1c": 1024.0, "s0u": 100.0, "s1u": 0.0,
        "ec": 8192.0, "eu": 4096.0, "oc": oc_kb, "ou": ou_kb,
        "mc": 4096.0, "mu": 3000.0, "ccsc": 512.0, "ccsu": 400.0,
        "ygc": ygc, "ygct": ygct, "fgc": fgc, "fgct": fgct, "gct": ygct + fgct,
    }


class TestSegmentAndDeltas(MetricsFixture):
    def test_no_restart_single_segment(self):
        base = datetime(2026, 6, 1)
        self.add_jvm([
            gcutil_row(base + timedelta(minutes=5 * i), 50.0,
                       uptime=1000.0 + 300 * i, ygc=i, ygct=0.1 * i,
                       fgc=i // 3, fgct=0.5 * (i // 3))
            for i in range(10)
        ])
        metrics.build_jvm_gc_derived(self.con)
        segs = self.q("SELECT DISTINCT segment FROM jvm_gc_derived")
        self.assertEqual(segs, [(0,)])

    def test_fgc_delta_first_point_is_null(self):
        base = datetime(2026, 6, 1)
        self.add_jvm([
            gcutil_row(base + timedelta(minutes=5 * i), 50.0,
                       uptime=1000.0 + 300 * i, fgc=i)
            for i in range(5)
        ])
        metrics.build_jvm_gc_derived(self.con)
        rows = self.q("SELECT ts, fgc_delta FROM jvm_gc_derived ORDER BY ts")
        self.assertIsNone(rows[0][1])
        self.assertEqual(rows[1][1], 1)

    def test_restart_splits_segment_and_nulls_delta(self):
        base = datetime(2026, 6, 1)
        uptimes = [1000.0, 2000.0, 3000.0, 50.0, 150.0]
        fgcs = [10, 11, 12, 0, 1]
        self.add_jvm([
            gcutil_row(base + timedelta(minutes=5 * i), 50.0,
                       uptime=uptimes[i], fgc=fgcs[i])
            for i in range(5)
        ])
        metrics.build_jvm_gc_derived(self.con)
        rows = self.q("SELECT segment, fgc_delta FROM jvm_gc_derived ORDER BY ts")
        self.assertEqual([r[0] for r in rows], [0, 0, 0, 1, 1])
        self.assertIsNone(rows[3][1])
        self.assertEqual(rows[4][1], 1)

    def test_restart_detected_by_gct_when_uptime_null(self):
        base = datetime(2026, 6, 1)
        gcts = [5.0, 6.0, 7.0, 1.0, 2.0]
        rows = []
        for i in range(5):
            r = gcutil_row(base + timedelta(minutes=5 * i), 50.0)
            r["jvm_uptime_sec"] = None
            r["gct"] = gcts[i]
            rows.append(r)
        self.add_jvm(rows)
        metrics.build_jvm_gc_derived(self.con)
        got = [r[0] for r in self.q("SELECT segment FROM jvm_gc_derived ORDER BY ts")]
        self.assertEqual(got, [0, 0, 0, 1, 1])

    def test_no_ygc_delta_or_gct_delta_columns(self):
        self.add_jvm([gcutil_row(datetime(2026, 6, 1), 50.0)])
        metrics.build_jvm_gc_derived(self.con)
        cols = [r[0] for r in self.q("DESCRIBE jvm_gc_derived")]
        self.assertNotIn("ygc_delta", cols)
        self.assertNotIn("gct_delta", cols)
        self.assertIn("fgc_delta", cols)
        self.assertIn("fgct_delta", cols)
        self.assertIn("ygct_delta", cols)

    def test_single_point_series_ok(self):
        self.add_jvm([gcutil_row(datetime(2026, 6, 1), 50.0)])
        metrics.build_jvm_gc_derived(self.con)
        self.assertEqual(
            self.q("SELECT count(*) FROM jvm_gc_derived")[0][0], 1
        )

    def test_multiple_series_independent(self):
        base = datetime(2026, 6, 1)
        rows = []
        for i in range(4):
            rows.append(gcutil_row(base + timedelta(minutes=5 * i), 50.0,
                                   uptime=1000.0 + 300 * i, container="a"))
            rows.append(gcutil_row(base + timedelta(minutes=5 * i), 50.0,
                                   uptime=50.0 if i == 2 else 1000.0 + 300 * i,
                                   container="b"))
        self.add_jvm(rows)
        metrics.build_jvm_gc_derived(self.con)
        a = [r[0] for r in self.q(
            "SELECT segment FROM jvm_gc_derived WHERE container='a' ORDER BY ts")]
        b = [r[0] for r in self.q(
            "SELECT segment FROM jvm_gc_derived WHERE container='b' ORDER BY ts")]
        self.assertEqual(a, [0, 0, 0, 0])
        self.assertEqual(b, [0, 0, 1, 1])


class TestBuildMetrics(MetricsFixture):
    def _seed(self):
        base = datetime(2026, 6, 1)
        self.add_db([
            {"ts": base + timedelta(minutes=5 * i), "host": "host01",
             "port": 7003, "datasource": "OraclePool_1", "active_connections": i}
            for i in range(5)
        ])
        self.add_jvm([gcutil_row(base + timedelta(minutes=5 * i), 40.0 + i)
                      for i in range(5)])
        self.add_jvm([gc_row(base + timedelta(minutes=5 * i), 5120.0)
                      for i in range(5)])
        self.add_lsf([
            {"ts": base + timedelta(minutes=5 * i), "host": "lsfhost01",
             "queue": "normal", "njobs": 10, "pend": 2, "run": 7, "susp": 1}
            for i in range(5)
        ])
        self.build()

    def test_metric_kinds(self):
        self._seed()
        got = sorted(r[0] for r in self.q("SELECT DISTINCT metric FROM metrics"))
        self.assertEqual(got, EXPECTED_METRICS)
        self.assertEqual(len(got), 14)

    def test_detectable_metrics_constant(self):
        self.assertEqual(len(metrics.DETECTABLE_METRICS), 11)
        for name in metrics.RATIO_METRICS:
            self.assertNotIn(name, metrics.DETECTABLE_METRICS)

    def test_series_id_formats(self):
        self._seed()
        ids = sorted(set(r[0] for r in self.q("SELECT series_id FROM metrics")))
        self.assertIn("db_connection/host01:7003/OraclePool_1", ids)
        self.assertIn("jvm_gc/app01@h1", ids)
        self.assertIn("lsf_queue/lsfhost01/normal", ids)

    def test_gcutil_ou_equals_ou_pct(self):
        self._seed()
        rows = self.q(
            "SELECT a.value, b.value FROM metrics a JOIN metrics b"
            " ON a.ts=b.ts AND a.series_id=b.series_id"
            " WHERE a.series_id='jvm_gc/app01@h1' AND a.metric='ou' AND b.metric='ou_pct'"
        )
        self.assertTrue(rows)
        for ou, pct in rows:
            self.assertAlmostEqual(ou, pct)

    def test_gc_ou_pct_is_ratio(self):
        self._seed()
        rows = self.q(
            "SELECT a.value, b.value FROM metrics a JOIN metrics b"
            " ON a.ts=b.ts AND a.series_id=b.series_id"
            " WHERE a.series_id='jvm_gc/app02@h2' AND a.metric='ou' AND b.metric='ou_pct'"
        )
        self.assertTrue(rows)
        for ou, pct in rows:
            self.assertAlmostEqual(pct, ou / 20480.0 * 100.0)
            self.assertLessEqual(pct, 100.0)
            self.assertGreater(ou, 100.0)  # KB 値のまま

    def test_no_null_values(self):
        self._seed()
        self.assertEqual(
            self.q("SELECT count(*) FROM metrics WHERE value IS NULL")[0][0], 0
        )

    def test_null_capacity_excludes_pct_row(self):
        base = datetime(2026, 6, 1)
        self.add_jvm([gc_row(base + timedelta(minutes=5 * i), 5120.0, oc_kb=None)
                      for i in range(3)])
        self.build()
        got = self.q("SELECT count(*) FROM metrics WHERE metric='ou_pct'")
        self.assertEqual(got[0][0], 0)

    def test_zero_capacity_no_division_error(self):
        base = datetime(2026, 6, 1)
        self.add_jvm([gc_row(base + timedelta(minutes=5 * i), 5120.0, oc_kb=0.0)
                      for i in range(3)])
        self.build()  # 例外にならないこと
        self.assertEqual(
            self.q("SELECT count(*) FROM metrics WHERE metric='ou_pct'")[0][0], 0
        )

    def test_metrics_is_a_table(self):
        self._seed()
        kinds = self.q(
            "SELECT table_type FROM information_schema.tables WHERE table_name='metrics'"
        )
        self.assertEqual(kinds[0][0].upper(), "BASE TABLE")

    def test_empty_input_ok(self):
        self.build()
        self.assertEqual(self.q("SELECT count(*) FROM metrics")[0][0], 0)
        self.assertEqual(metrics.list_series(self.con), [])

    def test_rebuild_does_not_double(self):
        self._seed()
        first = self.q("SELECT count(*) FROM metrics")[0][0]
        self.build()
        self.assertEqual(self.q("SELECT count(*) FROM metrics")[0][0], first)


class TestListSeries(MetricsFixture):
    def _seed_interval(self, minutes=5, points=10):
        base = datetime(2026, 6, 1)
        self.add_db([
            {"ts": base + timedelta(minutes=minutes * i), "host": "h",
             "port": 1, "datasource": "d", "active_connections": i}
            for i in range(points)
        ])
        self.build()

    def test_interval_median(self):
        self._seed_interval(minutes=5, points=10)
        series = metrics.list_series(self.con)
        self.assertEqual(len(series), 1)
        self.assertAlmostEqual(series[0].interval_sec, 300.0)
        self.assertEqual(series[0].n_points, 10)
        self.assertEqual(series[0].metric, "active_connections")

    def test_irregular_interval_uses_median(self):
        base = datetime(2026, 6, 1)
        offsets = [0, 5, 10, 15, 20, 25, 30, 35, 95]  # 分。1 点だけ大きく空く
        self.add_db([
            {"ts": base + timedelta(minutes=m), "host": "h", "port": 1,
             "datasource": "d", "active_connections": i}
            for i, m in enumerate(offsets)
        ])
        self.build()
        series = metrics.list_series(self.con)
        self.assertAlmostEqual(series[0].interval_sec, 300.0)

    def test_single_point_interval_zero(self):
        self._seed_interval(points=1)
        series = metrics.list_series(self.con)
        self.assertEqual(series[0].n_points, 1)
        self.assertEqual(series[0].interval_sec, 0.0)

    def test_excludes_pct_metrics(self):
        base = datetime(2026, 6, 1)
        self.add_jvm([gcutil_row(base + timedelta(minutes=5 * i), 40.0)
                      for i in range(5)])
        self.build()
        got = set(s.metric for s in metrics.list_series(self.con))
        for name in metrics.RATIO_METRICS:
            self.assertNotIn(name, got)

    def test_sorted_and_stable(self):
        base = datetime(2026, 6, 1)
        self.add_jvm([gcutil_row(base + timedelta(minutes=5 * i), 40.0)
                      for i in range(5)])
        self.add_lsf([
            {"ts": base + timedelta(minutes=5 * i), "host": "g", "queue": "q",
             "njobs": 1, "pend": 1, "run": 1, "susp": 0}
            for i in range(5)
        ])
        self.build()
        first = [(s.series_id, s.metric) for s in metrics.list_series(self.con)]
        second = [(s.series_id, s.metric) for s in metrics.list_series(self.con)]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first))


if __name__ == "__main__":
    unittest.main()
