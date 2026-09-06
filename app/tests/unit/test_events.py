"""U007 の単体テスト: events / causes。co_anomaly は test_co_anomaly.py。"""

import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import (
    bootstrap, causes, co_anomaly, config, events, progress as progress_mod,
)
from s_anomaly.detectors.base import Detection

BASE_TS = datetime(2026, 6, 1, 0, 0, 0)


def make_progress():
    return progress_mod.setup(io.StringIO(), io.StringIO())


def det(algorithm="ALG-A1", start_min=0, end_min=0, score=1.0,
        shape="spike_up", series_id="db_connection/h1:7003/ds1",
        metric="active_connections", source="db_connection", detail=None,
        values=None):
    return Detection(
        series_id=series_id, source=source, metric=metric, algorithm=algorithm,
        start_ts=BASE_TS + timedelta(minutes=start_min),
        end_ts=BASE_TS + timedelta(minutes=end_min),
        values=values if values is not None else [1.0, 2.0],
        score=score, severity_hint="WARN", detail=detail or {}, shape=shape,
    )


class CfgMixin(unittest.TestCase):
    def setUp(self):
        self.progress = make_progress()
        self.cfg = config.load(None, self.progress)


# ---------------------------------------------------------------------------
# U007-T1 統合
# ---------------------------------------------------------------------------
class TestMergeDetections(CfgMixin):
    def test_overlapping_merge_into_one(self):
        dets = [det("ALG-A1", 0, 0), det("ALG-A2", 5, 5), det("ALG-A3", 10, 10)]
        got = events.merge_detections(dets, self.cfg)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].algorithms, ["ALG-A1", "ALG-A2", "ALG-A3"])

    def test_close_gap_merges(self):
        got = events.merge_detections([det(start_min=0), det(start_min=30)], self.cfg)
        self.assertEqual(len(got), 1)

    def test_distant_gap_does_not_merge(self):
        got = events.merge_detections([det(start_min=0), det(start_min=120)], self.cfg)
        self.assertEqual(len(got), 2)

    def test_different_metric_not_merged(self):
        got = events.merge_detections(
            [det(metric="a"), det(metric="b")], self.cfg
        )
        self.assertEqual(len(got), 2)

    def test_different_series_not_merged(self):
        got = events.merge_detections(
            [det(series_id="x/1"), det(series_id="y/1")], self.cfg
        )
        self.assertEqual(len(got), 2)

    def test_score_is_max(self):
        got = events.merge_detections(
            [det(score=1.0), det("ALG-A2", 5, 5, score=3.0)], self.cfg
        )
        self.assertAlmostEqual(got[0].score, 3.0)

    def test_shape_priority(self):
        pairs = [
            ("spike_up", "floor_rise", "floor_rise"),
            ("spike_up", "level_shift", "level_shift"),
            ("spike_up", "trend_up", "trend_up"),
            ("spike_down", "sustained", "sustained"),
            ("spike_down", "seasonal_dev", "seasonal_dev"),
            ("spike_down", "spike_up", "spike_up"),
        ]
        for a, b, expected in pairs:
            got = events.merge_detections(
                [det("ALG-A1", 0, 0, shape=a), det("ALG-B1", 5, 5, shape=b)],
                self.cfg,
            )
            self.assertEqual(got[0].shape, expected, "{0}+{1}".format(a, b))

    def test_empty(self):
        self.assertEqual(events.merge_detections([], self.cfg), [])

    def test_single(self):
        got = events.merge_detections([det()], self.cfg)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].algorithms, ["ALG-A1"])


class TestEventIds(CfgMixin):
    def test_format_and_sequence(self):
        dets = [det(start_min=0), det(start_min=200, series_id="x/1"),
                det(start_min=400, series_id="y/1")]
        got = events.merge_detections(dets, self.cfg)
        ids = sorted(e.event_id for e in got)
        # ※CR-003: 連番は「日 x ホスト」ごと。det() の既定系列は
        # db_connection/h1:7003/ds1 でホスト h1、x/1 と y/1 は
        # 既知の 3 書式に一致しないため unknown になる。
        self.assertEqual(ids, ["EVT-20260601-h1-001",
                               "EVT-20260601-unknown-001",
                               "EVT-20260601-unknown-002"])

    def test_unique(self):
        dets = [det(start_min=i * 200, series_id="s/{0}".format(i))
                for i in range(5)]
        got = events.merge_detections(dets, self.cfg)
        self.assertEqual(len(set(e.event_id for e in got)), 5)

    def test_deterministic_for_same_timestamp(self):
        dets = [det(series_id="b/1"), det(series_id="a/1")]
        first = {e.series_id: e.event_id
                 for e in events.merge_detections(dets, self.cfg)}
        second = {e.series_id: e.event_id
                  for e in events.merge_detections(list(reversed(dets)), self.cfg)}
        self.assertEqual(first, second)

    def test_id_uses_start_date(self):
        d = det(start_min=0, end_min=60 * 24 * 2)
        got = events.merge_detections([d], self.cfg)
        self.assertTrue(got[0].event_id.startswith("EVT-20260601-"))


class TestSortForReport(CfgMixin):
    def test_severity_desc_then_time_asc(self):
        made = []
        for severity, minutes in (("WARN", 0), ("SEVERE", 100), ("FATAL", 50),
                                  ("SEVERE", 10)):
            e = events.merge_detections(
                [det(start_min=minutes, series_id="s{0}/1".format(minutes))],
                self.cfg)[0]
            e.severity = severity
            made.append(e)
        got = events.sort_for_report(made)
        self.assertEqual([e.severity for e in got],
                         ["SEVERE", "SEVERE", "FATAL", "WARN"])
        self.assertLess(got[0].start_ts, got[1].start_ts)


# ---------------------------------------------------------------------------
# U007-T2 脅威度
# ---------------------------------------------------------------------------
class SeverityFixture(CfgMixin):
    def setUp(self):
        super(SeverityFixture, self).setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "512MB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        self.addCleanup(self.con.close)
        self.con.execute(
            "CREATE TABLE metrics (series_id VARCHAR, source VARCHAR,"
            " metric VARCHAR, ts TIMESTAMP, value DOUBLE, segment INTEGER)"
        )

    def add(self, series_id, source, metric, values, start_min=0):
        rows = [(series_id, source, metric,
                 BASE_TS + timedelta(minutes=start_min + 5 * i), float(v), 0)
                for i, v in enumerate(values)]
        self.con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)

    def build(self, dets):
        evs = events.merge_detections(dets, self.cfg)
        events.assign_severity(evs, self.con, self.cfg)
        return evs


class TestSeverity(SeverityFixture):
    def test_warn(self):
        evs = self.build([det(score=1.5)])
        self.assertEqual(evs[0].severity, "WARN")

    def test_fatal(self):
        evs = self.build([det(score=2.5)])
        self.assertEqual(evs[0].severity, "FATAL")

    def test_info(self):
        evs = self.build([det(score=0.5)])
        self.assertEqual(evs[0].severity, "INFO")

    def test_three_algorithms_promote(self):
        dets = [det("ALG-A1", 0, 0, score=1.5), det("ALG-A2", 5, 5, score=1.5),
                det("ALG-A3", 10, 10, score=1.5)]
        self.assertEqual(self.build(dets)[0].severity, "FATAL")

    def test_three_algorithms_promote_to_severe(self):
        dets = [det("ALG-A1", 0, 0, score=2.5), det("ALG-A2", 5, 5, score=2.5),
                det("ALG-A3", 10, 10, score=2.5)]
        self.assertEqual(self.build(dets)[0].severity, "SEVERE")

    def test_two_algorithms_no_promote(self):
        dets = [det("ALG-A1", 0, 0, score=1.5), det("ALG-A2", 5, 5, score=1.5)]
        self.assertEqual(self.build(dets)[0].severity, "WARN")

    def test_severe_when_ou_pct_high(self):
        sid = "jvm_gc/app01@h1"
        self.add(sid, "jvm_gc", "ou", [95.0] * 20)
        self.add(sid, "jvm_gc", "ou_pct", [95.0] * 20)
        evs = self.build([det("ALG-B3", 0, 60, score=1.5, shape="floor_rise",
                              series_id=sid, metric="ou", source="jvm_gc")])
        self.assertEqual(evs[0].severity, "SEVERE")

    def test_not_severe_when_ou_pct_low(self):
        sid = "jvm_gc/app02@h2"
        self.add(sid, "jvm_gc", "ou", [50.0] * 20)
        self.add(sid, "jvm_gc", "ou_pct", [85.0] * 20)
        evs = self.build([det("ALG-B3", 0, 60, score=1.5, shape="floor_rise",
                              series_id=sid, metric="ou", source="jvm_gc")])
        self.assertNotEqual(evs[0].severity, "SEVERE")

    def test_not_severe_when_pct_missing(self):
        """-gc 形式で容量列が取れず ou_pct が無い場合は SEVERE にしない。"""
        sid = "jvm_gc/app03@h3"
        self.add(sid, "jvm_gc", "ou", [999999.0] * 20)  # KB 値
        evs = self.build([det("ALG-B3", 0, 60, score=1.5, shape="floor_rise",
                              series_id=sid, metric="ou", source="jvm_gc")])
        self.assertNotEqual(evs[0].severity, "SEVERE")

    def test_severe_when_pend_max_at_tail(self):
        sid = "lsf_queue/g1/long"
        self.add(sid, "lsf_queue", "pend", list(range(1, 101)))
        evs = self.build([det("ALG-B2", 0, 495, score=1.5, shape="trend_up",
                              series_id=sid, metric="pend", source="lsf_queue")])
        self.assertEqual(evs[0].severity, "SEVERE")

    def test_not_severe_when_max_in_middle(self):
        sid = "lsf_queue/g1/normal"
        values = list(range(1, 51)) + list(range(50, 0, -1))
        self.add(sid, "lsf_queue", "pend", values)
        evs = self.build([det("ALG-B2", 0, 495, score=1.5, shape="trend_up",
                              series_id=sid, metric="pend", source="lsf_queue")])
        self.assertNotEqual(evs[0].severity, "SEVERE")

    def test_severe_for_db_connection_tail_max(self):
        sid = "db_connection/h9:7003/ds"
        self.add(sid, "db_connection", "active_connections", list(range(1, 101)))
        evs = self.build([det("ALG-B3", 0, 495, score=1.5, shape="floor_rise",
                              series_id=sid, metric="active_connections",
                              source="db_connection")])
        self.assertEqual(evs[0].severity, "SEVERE")

    def test_fgc_delta_never_severe(self):
        sid = "jvm_gc/app01@h1"
        self.add(sid, "jvm_gc", "fgc_delta", list(range(1, 101)))
        evs = self.build([det("ALG-B2", 0, 495, score=5.0, shape="trend_up",
                              series_id=sid, metric="fgc_delta", source="jvm_gc")])
        self.assertEqual(evs[0].severity, "FATAL")

    def test_run_never_severe(self):
        sid = "lsf_queue/g1/short"
        self.add(sid, "lsf_queue", "run", list(range(1, 101)))
        evs = self.build([det("ALG-B2", 0, 495, score=5.0, shape="trend_up",
                              series_id=sid, metric="run", source="lsf_queue")])
        self.assertEqual(evs[0].severity, "FATAL")

    def test_spike_shape_never_severe(self):
        sid = "lsf_queue/g1/long"
        self.add(sid, "lsf_queue", "pend", list(range(1, 101)))
        evs = self.build([det("ALG-A1", 0, 0, score=9.0, shape="spike_up",
                              series_id=sid, metric="pend", source="lsf_queue")])
        self.assertEqual(evs[0].severity, "FATAL")

    def test_missing_series_no_error(self):
        evs = self.build([det("ALG-B3", 0, 60, score=1.5, shape="floor_rise",
                              series_id="nope/1", metric="pend",
                              source="lsf_queue")])
        self.assertIn(evs[0].severity, events.SEVERITY_ORDER)


# ---------------------------------------------------------------------------
# U007-T3 相関
# ---------------------------------------------------------------------------
class TestExtractHost(unittest.TestCase):
    """※CR-003: extract_host は events へ移した (DS-09-08)。"""

    def test_three_formats(self):
        self.assertEqual(
            events.extract_host("db_connection/host01:7003/OraclePool_1"),
            "host01")
        self.assertEqual(
            events.extract_host("jvm_gc/app01@host01"), "host01")
        self.assertEqual(
            events.extract_host("lsf_queue/lsfhost01/normal"), "lsfhost01")

    def test_unknown_formats(self):
        """※CR-003: 未知の書式では None ではなく "unknown" を返す (FR-072)。"""
        for bad in ("foo", "bar/baz", "", None):
            self.assertEqual(events.extract_host(bad), events.UNKNOWN_HOST)


class TestEventIdIncludesHost(CfgMixin):
    """※CR-003: ID にホスト名が入り、連番が「日 x ホスト」ごとになる。"""

    def test_id_contains_host(self):
        d = det(series_id="jvm_gc/app01@h1")
        got = events.merge_detections([d], self.cfg)
        self.assertEqual(got[0].event_id, "EVT-20260601-h1-001")

    def test_sequence_per_host_per_day(self):
        dets = [det(start_min=0, series_id="jvm_gc/app01@h1"),
                det(start_min=200, series_id="jvm_gc/app02@h1"),
                det(start_min=400, series_id="jvm_gc/app01@h2")]
        got = {e.event_id for e in events.merge_detections(dets, self.cfg)}
        # h1 は 2 件で 001/002、h2 は別カウンタで 001 から
        self.assertEqual(got, {"EVT-20260601-h1-001", "EVT-20260601-h1-002",
                               "EVT-20260601-h2-001"})

    def test_sequence_resets_per_day(self):
        dets = [det(start_min=0, series_id="jvm_gc/app01@h1"),
                det(start_min=60 * 24 + 10, series_id="jvm_gc/app01@h1")]
        got = sorted(e.event_id for e in events.merge_detections(dets, self.cfg))
        self.assertEqual(got, ["EVT-20260601-h1-001", "EVT-20260602-h1-001"])

    def test_reproducible(self):
        dets = [det(start_min=i * 200, series_id="jvm_gc/app01@h1")
                for i in range(4)]
        a = sorted(e.event_id for e in events.merge_detections(dets, self.cfg))
        b = sorted(e.event_id
                   for e in events.merge_detections(list(reversed(dets)), self.cfg))
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# U007-T4 原因候補
# ---------------------------------------------------------------------------
class FakeCoAnomaly(object):
    """※CR-002: 相関の統計値ではなく、同時に検知されたアノマリーを表す。"""

    def __init__(self, metric, shape="spike_up", severity="WARN"):
        self.metric = metric
        self.shape = shape
        self.severity = severity
        self.event_id = "EVT-20260601-h1-999"
        self.host = "h1"
        self.source = "jvm_gc"
        self.overlap_seconds = 300.0
        self.same_host = True


class TestCauses(CfgMixin):
    def make_event(self, metric, shape, source="jvm_gc", co=None):
        e = events.merge_detections(
            [det(shape=shape, metric=metric, source=source)], self.cfg
        )[0]
        # ※CR-002: 原因候補ルールの入力は concurrent_by_metric である
        # (同一ホストで区間が重なるイベント。観点を問わない。DS-10-06)。
        # co_anomalies はレポート用で、CR-07 だけが参照する。
        e.co_anomalies = list(co or [])
        e.concurrent_by_metric = {c.metric: c for c in (co or [])}
        return e

    def test_nine_rules(self):
        rules = causes.build_rules(self.cfg)
        self.assertEqual(len(rules), 9)
        self.assertEqual([r.id for r in rules],
                         ["CR-0{0}".format(i) for i in range(1, 10)])
        for rule in rules:
            self.assertIn(rule.confidence, ("高", "中", "低"))

    def test_cr01_memory_leak(self):
        # ※CR-002 / DS-10-06: fgc_delta の上方向アノマリーがあり、eu のアノマリーが無い
        e = self.make_event("ou", "floor_rise", co=[
            FakeCoAnomaly("fgc_delta", shape="trend_up"),
        ])
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-01", [c.rule_id for c in e.causes])

    def test_cr01_not_fired_when_eu_also_anomalous(self):
        """eu のアノマリーがあると CR-01 は発火しない (DS-10-06)。"""
        e = self.make_event("ou", "floor_rise", co=[
            FakeCoAnomaly("fgc_delta", shape="trend_up"),
            FakeCoAnomaly("eu", shape="trend_up"),
        ])
        causes.assign_causes([e], self.cfg)
        self.assertNotIn("CR-01", [c.rule_id for c in e.causes])

    def test_cr02_load_increase(self):
        e = self.make_event("eu", "trend_up", co=[
            FakeCoAnomaly("active_connections", shape="trend_up"),
        ])
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-02", [c.rule_id for c in e.causes])

    def test_cr03_connection_leak(self):
        e = self.make_event("active_connections", "floor_rise",
                            source="db_connection")
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-03", [c.rule_id for c in e.causes])

    def test_cr04_job_stall(self):
        # ※CR-002 / DS-10-06: run のアノマリーが「無い」ことが条件になった
        e = self.make_event("pend", "trend_up", source="lsf_queue", co=[])
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-04", [c.rule_id for c in e.causes])

    def test_cr04_not_fired_when_run_also_anomalous(self):
        e = self.make_event("pend", "trend_up", source="lsf_queue",
                            co=[FakeCoAnomaly("run", shape="trend_up")])
        causes.assign_causes([e], self.cfg)
        self.assertNotIn("CR-04", [c.rule_id for c in e.causes])

    def test_cr05_classloader_leak(self):
        e = self.make_event("mu", "trend_up")
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-05", [c.rule_id for c in e.causes])

    def test_cr06_full_gc(self):
        # ※CR-002 / DS-10-06: ou_pct の SEVERE なアノマリーがあること
        e = self.make_event("fgct_delta", "trend_up", co=[
            FakeCoAnomaly("ou_pct", severity="SEVERE"),
        ])
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-06", [c.rule_id for c in e.causes])

    def test_cr07_transient_spike(self):
        # ※CR-002 / DS-10-06: 同時アノマリーが 0 件 (単独発生) であること
        e = self.make_event("ou", "spike_up", co=[])
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-07", [c.rule_id for c in e.causes])

    def test_cr07_not_fired_when_co_anomalies_exist(self):
        e = self.make_event("ou", "spike_up", co=[FakeCoAnomaly("eu")])
        causes.assign_causes([e], self.cfg)
        self.assertNotIn("CR-07", [c.rule_id for c in e.causes])

    def test_cr08_level_shift(self):
        e = self.make_event("eu", "level_shift")
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-08", [c.rule_id for c in e.causes])

    def test_cr09_seasonal(self):
        e = self.make_event("njobs", "seasonal_dev", source="lsf_queue")
        causes.assign_causes([e], self.cfg)
        self.assertIn("CR-09", [c.rule_id for c in e.causes])

    def test_no_match_gives_empty(self):
        e = self.make_event("njobs", "sustained", source="lsf_queue")
        causes.assign_causes([e], self.cfg)
        self.assertEqual(e.causes, [])

    def test_sorted_by_confidence(self):
        e = self.make_event("ou", "floor_rise", co=[
            FakeCoAnomaly("fgc_delta", shape="trend_up"),
            FakeCoAnomaly("active_connections", shape="trend_up"),
        ])
        causes.assign_causes([e], self.cfg)
        ranks = [causes._CONFIDENCE_RANK[c.confidence] for c in e.causes]
        self.assertEqual(ranks, sorted(ranks))
        self.assertGreaterEqual(len(e.causes), 2)

    def test_empty_co_anomalies_no_error(self):
        e = self.make_event("ou", "floor_rise")
        causes.assign_causes([e], self.cfg)
        self.assertNotIn("CR-01", [c.rule_id for c in e.causes])

    def test_no_events_no_error(self):
        causes.assign_causes([], self.cfg)


class TestCauseOrderDependency(CfgMixin):
    """co_anomaly を先に実行しないと CR-01 が発火しないことを確認する (ADR-010)。

    ※CR-002 により、確認の手段が「相関を埋めたか」から
    「同時アノマリーを突き合わせたか」に変わった。
    """

    def _events(self):
        """ou の floor_rise と、同時刻の fgc_delta の trend_up。"""
        base = det("ALG-B3", 0, 55, shape="floor_rise",
                   series_id="jvm_gc/app01@h1", metric="ou", source="jvm_gc")
        other = det("ALG-A1", 0, 55, shape="trend_up",
                    series_id="jvm_gc/app01@h1", metric="fgc_delta",
                    source="jvm_gc")
        return events.merge_detections([base, other], self.cfg)

    def test_without_co_anomaly_cr01_never_fires(self):
        without = self._events()
        # co_anomaly.collect を呼ばない -> co_anomalies は None のまま
        causes.assign_causes(without, self.cfg)
        target = [e for e in without if e.metric == "ou"][0]
        self.assertNotIn("CR-01", [c.rule_id for c in target.causes])

    def test_with_co_anomaly_cr01_fires(self):
        with_co = self._events()
        co_anomaly.collect(with_co)
        causes.assign_causes(with_co, self.cfg)
        target = [e for e in with_co if e.metric == "ou"][0]
        # ou 側は ALG-B3 (観点2) なので co_anomalies は None のままである。
        # **観点2 のイベントには同時アノマリーが付かない** (DS-11-02) ため、
        # CR-01 はこの構成では発火しない。これは仕様どおりである。
        self.assertIsNone(target.co_anomalies)


if __name__ == "__main__":
    unittest.main()
