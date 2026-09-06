"""T004 — 検知器の一括実行と失敗耐性 (FR-032)、および S6 の並列実行 (※CR-007)。

**`_run_all` は逐次実行の基準**である。`cli.detect`(並列)の結果がこれと
一致することを `TestParallelDetect` が確かめる。
"""

import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from s_anomaly import (
    bootstrap, cli, config, detectors, discovery, loaders, metrics,
    progress as progress_mod, schema,
)
from s_anomaly.loaders import bqueues, dbconn, jstat
from tests.integration import _setup_baseline

OVERLAP_TOLERANCE = timedelta(minutes=60)


class TestDetectorsRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()
        cls.progress = progress_mod.setup(io.StringIO(), io.StringIO())
        cls._tmp = tempfile.TemporaryDirectory()
        module, _ = bootstrap.load_duckdb(cls._tmp.name, cls.progress)
        cls.con = bootstrap.open_connection(
            module, "1GB", os.path.join(cls._tmp.name, "tmp"), cls.progress)
        schema.create_all(cls.con)
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
        }
        logs = os.path.join(cls.paths["normal"], "logs")
        for logfile in discovery.discover(logs, cls.progress):
            loader[logfile.kind](cls.con, logfile, cls.progress)
        loaders.dedupe_all(cls.con)
        metrics.build_jvm_gc_derived(cls.con)
        metrics.build_metrics(cls.con)
        cls.cfg = config.load(None, cls.progress)
        cls.series = metrics.list_series(cls.con)
        with open(os.path.join(cls.paths["normal"], "expected.json"),
                  "r", encoding="utf-8") as handle:
            cls.expected = json.load(handle)
        started = time.monotonic()
        cls.detections, cls.skips = cls._run_all(cls.cfg)
        cls.elapsed = time.monotonic() - started

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    @classmethod
    def _run_all(cls, cfg):
        found, skipped = [], []
        for detector in detectors.enabled_detectors(cfg):
            for meta in cls.series:
                d, s = detector.run(cls.con, cfg, meta, cls.progress)
                found.extend(d)
                skipped.extend(s)
        found.sort(key=lambda d: (d.series_id, d.metric, d.algorithm, d.start_ts))
        return found, skipped

    def test_01_registry_size(self):
        self.assertEqual(len(detectors.REGISTRY), 11)   # ※CR-005 ALG-C1

    def test_02_aspect_split(self):
        counts = {}
        for det in detectors.REGISTRY.values():
            counts[det.aspect] = counts.get(det.aspect, 0) + 1
        self.assertEqual(counts, {"観点1": 5, "観点2": 5, "観点3": 1})

    def test_03_all_run_without_exception(self):
        self.assertGreater(len(self.detections), 0)

    def test_04_no_missed_injections(self):
        misses = []
        for inj in self.expected["injected"]:
            want = set(inj["expect_algorithms"])
            t_from = datetime.fromisoformat(inj["from"]) - OVERLAP_TOLERANCE
            t_to = datetime.fromisoformat(inj["to"]) + OVERLAP_TOLERANCE
            hit = any(
                d.algorithm in want and d.series_id == inj["series_id"]
                and d.metric == inj["metric"]
                and not (d.end_ts < t_from or d.start_ts > t_to)
                for d in self.detections
            )
            if not hit:
                misses.append(inj["id"])
        self.assertEqual(misses, [], "検知漏れ: {0}".format(misses))

    def test_05_clean_series_rate(self):
        for clean in self.expected["clean_series"]:
            hits = [d for d in self.detections if d.series_id == clean]
            total = self.con.execute(
                "SELECT count(*) FROM metrics WHERE series_id = ?",
                [clean]).fetchone()[0]
            ratio = len(hits) / float(total) if total else 0.0
            self.assertLess(ratio, 0.02,
                            "{0}: {1}/{2}".format(clean, len(hits), total))

    def test_06_skips_recorded(self):
        for skip in self.skips:
            self.assertTrue(skip.reason)
            self.assertIn(skip.algorithm, detectors.REGISTRY)

    def test_07_elapsed_recorded(self):
        # 所要時間は記録するのみ (超過は FAIL としない)
        self.assertGreater(self.elapsed, 0.0)
        self.assertLess(self.elapsed, 600.0, "所要 {0:.1f}s".format(self.elapsed))

    def test_08_reproducible(self):
        again, _ = self._run_all(self.cfg)
        self.assertEqual(len(again), len(self.detections))
        keys1 = [(d.series_id, d.metric, d.algorithm, d.start_ts)
                 for d in self.detections]
        keys2 = [(d.series_id, d.metric, d.algorithm, d.start_ts) for d in again]
        self.assertEqual(keys1, keys2)

    def test_09_10_11_failure_isolation(self):
        """1 つの検知器が必ず失敗しても、他の結果が失われないこと。"""
        target = detectors.REGISTRY["ALG-A1"]
        original = target.run

        def boom(con, cfg, series, progress):
            raise RuntimeError("意図的な失敗")

        target.run = boom
        self.addCleanup(setattr, target, "run", original)

        found, failures = [], []
        for detector in detectors.enabled_detectors(self.cfg):
            for meta in self.series:
                try:
                    d, _s = detector.run(self.con, self.cfg, meta, self.progress)
                except Exception as exc:
                    failures.append((detector.id, meta.series_id, str(exc)))
                    continue
                found.extend(d)

        self.assertTrue(failures)
        self.assertTrue(all(f[0] == "ALG-A1" for f in failures))
        expected_rest = [d for d in self.detections if d.algorithm != "ALG-A1"]
        self.assertEqual(len(found), len(expected_rest))


class TestParallelDetect(unittest.TestCase):
    """※CR-007 — `cli.detect` の並列実行が逐次実行と同じ結果になること。"""

    @classmethod
    def setUpClass(cls):
        # **自前で取り込む。** `TestDetectorsRun` の接続はそのクラスの
        # tearDownClass で閉じられるため、共有すると使えない。
        cls.paths = _setup_baseline.restore()
        cls.progress = progress_mod.setup(io.StringIO(), io.StringIO())
        cls._tmp = tempfile.TemporaryDirectory()
        module, _ = bootstrap.load_duckdb(cls._tmp.name, cls.progress)
        cls.con = bootstrap.open_connection(
            module, "1GB", os.path.join(cls._tmp.name, "tmp"), cls.progress)
        schema.create_all(cls.con)
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
        }
        logs = os.path.join(cls.paths["normal"], "logs")
        for logfile in discovery.discover(logs, cls.progress):
            loader[logfile.kind](cls.con, logfile, cls.progress)
        loaders.dedupe_all(cls.con)
        metrics.build_jvm_gc_derived(cls.con)
        metrics.build_metrics(cls.con)
        cls.cfg = config.load(None, cls.progress)
        cls.series = metrics.list_series(cls.con)
        cls.sequential = cls._detect(1)
        cls.parallel = cls._detect(8)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    @classmethod
    def _detect(cls, workers):
        original = cli._detect_workers
        cli._detect_workers = lambda cfg, total: min(workers, total)
        try:
            return cli.detect(cls.con, cls.cfg, cls.series, cls.progress)
        finally:
            cli._detect_workers = original

    @staticmethod
    def _keys(result):
        return [(d.series_id, d.metric, d.algorithm, d.start_ts, d.score)
                for d in result["detections"]]

    def test_01_parallel_matches_sequential(self):
        """**検知点の並びまで一致すること**(NFR-009)。"""
        self.assertGreater(len(self.parallel["detections"]), 0)
        self.assertEqual(self._keys(self.parallel), self._keys(self.sequential))

    def test_02_skips_match(self):
        def rows(r):
            return sorted((s.series_id, s.metric, s.algorithm, s.reason)
                          for s in r["skips"])
        self.assertEqual(rows(self.parallel), rows(self.sequential))

    def test_03_no_failures_in_normal_run(self):
        self.assertEqual(self.parallel["failures"], [])

    def test_04_thread_count_is_capped(self):
        """スレッド数は設定値・系列数のいずれも超えない(DS-12-09)。"""
        self.assertEqual(cli._detect_workers(self.cfg, 3), 3)
        self.assertGreaterEqual(cli._detect_workers(self.cfg, 1000), 1)
        self.assertLessEqual(cli._detect_workers(self.cfg, 1000), 8)

    def test_05_failure_isolation_under_threads(self):
        """**並列でも FR-032 が保たれ、`failures` が整列していること。**"""
        target = detectors.REGISTRY["ALG-A1"]
        original = target.run

        def boom(con, cfg, series, progress):
            raise RuntimeError("意図的な失敗")

        target.run = boom
        self.addCleanup(setattr, target, "run", original)
        result = self._detect(8)

        self.assertTrue(result["failures"])
        self.assertTrue(all(f[0] == "ALG-A1" for f in result["failures"]))
        # 実行順に依存しないこと (NFR-009)
        self.assertEqual(result["failures"], sorted(result["failures"]))
        rest = [d for d in self.sequential["detections"] if d.algorithm != "ALG-A1"]
        self.assertEqual(len(result["detections"]), len(rest))


if __name__ == "__main__":
    unittest.main()
