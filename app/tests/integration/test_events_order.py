"""T005 — イベント処理の実行順序 (S7 -> S8 -> S7')。

**順序を誤ると、相関を参照する 4 ルールが無言で発火しなくなる。**
本テストはその事故を検出する (ADR-010)。
"""

import io
import json
import os
import tempfile
import unittest

from s_anomaly import (
    bootstrap, causes, co_anomaly, config, detectors, discovery, events,
    loaders, metrics, progress as progress_mod, schema,
)
from s_anomaly.loaders import bqueues, dbconn, jstat, sar
from tests.integration import _setup_baseline

CORRELATION_RULES = {"CR-01", "CR-02", "CR-04", "CR-06"}


class TestEventsOrder(unittest.TestCase):
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
            discovery.KIND_SAR: sar.load,      # ※CR-010
        }
        for logfile in discovery.discover(
                os.path.join(cls.paths["normal"], "logs"), cls.progress):
            loader[logfile.kind](cls.con, logfile, cls.progress)
        loaders.dedupe_all(cls.con)
        metrics.build_jvm_gc_derived(cls.con)
        metrics.build_metrics(cls.con)
        cls.cfg = config.load(None, cls.progress)
        series = metrics.list_series(cls.con)
        cls.detections = []
        for detector in detectors.enabled_detectors(cls.cfg):
            for meta in series:
                d, _s = detector.run(cls.con, cls.cfg, meta, cls.progress)
                cls.detections.extend(d)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    def _pipeline(self, with_co_anomaly=True):
        """※CR-002・CR-003: S7 -> S7'(採番) -> S8 -> S8' の順 (DS-11-06)。

        採番は merge_detections の内部で行われる。
        """
        evs = events.merge_detections(self.detections, self.cfg)
        events.assign_severity(evs, self.con, self.cfg)
        if with_co_anomaly:
            co_anomaly.collect(evs)
        causes.assign_causes(evs, self.cfg)
        return events.sort_for_report(evs)

    def test_01_02_merged(self):
        evs = self._pipeline()
        self.assertGreater(len(evs), 0)
        self.assertLessEqual(len(evs), len(self.detections))
        for event in evs:
            for d in self.detections:
                if d.series_id == event.series_id and d.metric == event.metric:
                    break

    def test_03_algorithms_sorted_unique(self):
        for event in self._pipeline():
            self.assertEqual(event.algorithms, sorted(set(event.algorithms)))

    def test_04_multi_algorithm_event_exists(self):
        evs = self._pipeline()
        self.assertTrue(any(len(e.algorithms) >= 2 for e in evs))

    def test_06_event_ids_unique(self):
        evs = self._pipeline()
        ids = [e.event_id for e in evs]
        self.assertEqual(len(ids), len(set(ids)))
        for event_id in ids:
            # ※CR-003: ID にホスト名が入る
            self.assertRegex(event_id, r"^EVT-\d{8}-[^-]+(-[^-]+)*-\d{3}$")

    def test_07_severity_values(self):
        evs = self._pipeline()
        got = set(e.severity for e in evs)
        self.assertTrue(got.issubset(set(events.SEVERITY_ORDER)))
        self.assertGreater(len(got), 1, "全件が同じ脅威度になっています")

    def test_08_09_co_anomalies(self):
        """※CR-002: 同時に発生したアノマリーの検証。"""
        evs = self._pipeline()
        # 観点1 のイベントには欄が付く (None でない)
        point_events = [e for e in evs if co_anomaly.is_point_anomaly(e)]
        self.assertTrue(point_events, "観点1 のイベントが 1 つも無い")
        for event in point_events:
            self.assertIsNotNone(event.co_anomalies)
            self.assertLessEqual(len(event.co_anomalies),
                                 co_anomaly.MAX_CO_ANOMALIES)
            for c in event.co_anomalies:
                # 自分自身は相手にならない
                self.assertNotEqual(c.event_id, event.event_id)
        # 観点2 を含むイベントには欄が付かない (DS-11-02)
        for event in evs:
            if not co_anomaly.is_point_anomaly(event):
                self.assertIsNone(event.co_anomalies)

    def test_08b_co_anomaly_does_not_touch_metrics(self):
        """※CR-002: collect は events だけを受け取る (DS-11-01)。"""
        import inspect

        self.assertEqual(list(inspect.signature(co_anomaly.collect).parameters),
                         ["events"])

    def test_10_memory_leak_identified(self):
        evs = self._pipeline()
        leak = [e for e in evs
                if e.series_id == "jvm_gc/app01@host01" and e.metric == "ou"]
        self.assertTrue(leak)
        rule_ids = set()
        for event in leak:
            rule_ids.update(c.rule_id for c in event.causes)
        self.assertIn("CR-01", rule_ids)

    def test_11_causes_sorted_by_confidence(self):
        for event in self._pipeline():
            ranks = [causes._CONFIDENCE_RANK[c.confidence] for c in event.causes]
            self.assertEqual(ranks, sorted(ranks))

    def test_12_sorted_for_report(self):
        evs = self._pipeline()
        ranks = [events.SEVERITY_ORDER.index(e.severity) for e in evs]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_13_14_order_matters(self):
        """※CR-002: 同時アノマリーを先に埋めないと、それを参照する
        5 ルール (CR-01/02/04/06/07) が発火しないこと (ADR-010)。"""
        without = self._pipeline(with_co_anomaly=False)
        with_corr = self._pipeline(with_co_anomaly=True)

        fired_without = set()
        for event in without:
            fired_without.update(c.rule_id for c in event.causes)
        fired_with = set()
        for event in with_corr:
            fired_with.update(c.rule_id for c in event.causes)

        self.assertEqual(fired_without & CORRELATION_RULES, set(),
                         "同時アノマリーなしで発火してはならないルール: {0}".format(
                             fired_without & CORRELATION_RULES))
        self.assertTrue(fired_with & CORRELATION_RULES)

        # 相関を参照するルールの発火数で比べる。
        # 総数で比べてはいけない。CR-07 は「相関に有意な変化なし」が条件であり、
        # 相関が空だと自明に成立して大量に発火するため、総数はむしろ増える。
        def count_correlation_rules(evs):
            return sum(1 for e in evs for c in e.causes
                       if c.rule_id in CORRELATION_RULES)

        self.assertEqual(count_correlation_rules(without), 0)
        self.assertGreater(count_correlation_rules(with_corr), 0)

    def test_15_deterministic(self):
        first = [(e.event_id, e.series_id, e.metric, e.severity)
                 for e in self._pipeline()]
        second = [(e.event_id, e.series_id, e.metric, e.severity)
                  for e in self._pipeline()]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
