"""U007-T7 の単体テスト: co_anomaly (同時に発生したアノマリー)。

※CR-002 により新設。旧 correlate の単体テストを置き換える。
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src"))

from s_anomaly import co_anomaly  # noqa: E402

BASE_TS = datetime(2026, 6, 1, 0, 0, 0)


class FakeEvent(object):
    """co_anomaly が必要とする属性だけを持つイベント。"""

    def __init__(self, event_id, series_id, metric, algorithms,
                 start_min, end_min, severity="WARN", shape="spike_up",
                 source="db_connection"):
        self.event_id = event_id
        self.series_id = series_id
        self.metric = metric
        self.algorithms = algorithms
        self.start_ts = BASE_TS + timedelta(minutes=start_min)
        self.end_ts = BASE_TS + timedelta(minutes=end_min)
        self.severity = severity
        self.shape = shape
        self.source = source
        self.co_anomalies = None


def point(event_id, host, metric="active_connections", start_min=0,
          end_min=10, severity="WARN"):
    """観点1 (ALG-A* のみ) のイベント。"""
    return FakeEvent(event_id, "db_connection/{0}:7003/ds".format(host),
                     metric, ["ALG-A1"], start_min, end_min, severity)


def trend(event_id, host, metric="ou", start_min=0, end_min=1000):
    """観点2 (ALG-B* のみ) のイベント。"""
    return FakeEvent(event_id, "jvm_gc/app01@{0}".format(host), metric,
                     ["ALG-B3"], start_min, end_min, source="jvm_gc")


def mixed(event_id, host, start_min=0, end_min=1000):
    """観点1 と観点2 の混在イベント。"""
    return FakeEvent(event_id, "db_connection/{0}:7003/ds".format(host),
                     "active_connections", ["ALG-A1", "ALG-B4"],
                     start_min, end_min)


class TestIsPointAnomaly(unittest.TestCase):
    def test_only_alg_a_is_point_anomaly(self):
        self.assertTrue(co_anomaly.is_point_anomaly(point("E1", "h1")))

    def test_only_alg_b_is_not(self):
        self.assertFalse(co_anomaly.is_point_anomaly(trend("E2", "h1")))

    def test_mixed_is_not(self):
        """混在イベントも対象外である (DS-11-02)。"""
        self.assertFalse(co_anomaly.is_point_anomaly(mixed("E3", "h1")))

    def test_no_algorithms_is_not(self):
        e = point("E4", "h1")
        e.algorithms = []
        self.assertFalse(co_anomaly.is_point_anomaly(e))


class TestOverlap(unittest.TestCase):
    def test_overlapping_pair_links_both_ways(self):
        a = point("E1", "h1", start_min=0, end_min=10)
        b = point("E2", "h2", start_min=5, end_min=15)
        co_anomaly.collect([a, b])
        self.assertEqual([c.event_id for c in a.co_anomalies], ["E2"])
        self.assertEqual([c.event_id for c in b.co_anomalies], ["E1"])

    def test_disjoint_pair_does_not_link(self):
        a = point("E1", "h1", start_min=0, end_min=10)
        b = point("E2", "h2", start_min=20, end_min=30)
        co_anomaly.collect([a, b])
        self.assertEqual(a.co_anomalies, [])
        self.assertEqual(b.co_anomalies, [])

    def test_touching_endpoints_count_as_overlap(self):
        """a_end == b_start は重なりとみなす (DS-11-03 の不等号)。"""
        a = point("E1", "h1", start_min=0, end_min=10)
        b = point("E2", "h2", start_min=10, end_min=20)
        co_anomaly.collect([a, b])
        self.assertEqual([c.event_id for c in a.co_anomalies], ["E2"])

    def test_same_instant_points_overlap_zero_seconds(self):
        a = point("E1", "h1", start_min=5, end_min=5)
        b = point("E2", "h2", start_min=5, end_min=5)
        co_anomaly.collect([a, b])
        self.assertEqual(a.co_anomalies[0].overlap_seconds, 0.0)


class TestScope(unittest.TestCase):
    def test_trend_event_gets_none(self):
        """観点2 のみのイベントには欄自体が出ない (None)。"""
        a = point("E1", "h1", start_min=0, end_min=10)
        b = trend("E2", "h1", start_min=0, end_min=1000)
        co_anomaly.collect([a, b])
        self.assertIsNone(b.co_anomalies)

    def test_mixed_event_gets_none(self):
        a = point("E1", "h1", start_min=0, end_min=10)
        m = mixed("E2", "h1", start_min=0, end_min=1000)
        co_anomaly.collect([a, m])
        self.assertIsNone(m.co_anomalies)

    def test_trend_event_is_not_a_counterpart(self):
        """観点2 は相手としても現れない (DS-11-02)。"""
        a = point("E1", "h1", start_min=0, end_min=10)
        b = trend("E2", "h2", start_min=0, end_min=1000)
        co_anomaly.collect([a, b])
        self.assertEqual(a.co_anomalies, [])

    def test_point_without_partner_gets_empty_list(self):
        a = point("E1", "h1")
        co_anomaly.collect([a])
        self.assertEqual(a.co_anomalies, [])


class TestRanking(unittest.TestCase):
    def _subject_with(self, others):
        subject = point("E0", "h1", start_min=0, end_min=100)
        co_anomaly.collect([subject] + others)
        return subject

    def test_capped_at_ten(self):
        others = [point("E{0:02d}".format(i), "h9", start_min=0, end_min=100)
                  for i in range(1, 15)]
        subject = self._subject_with(others)
        self.assertEqual(len(subject.co_anomalies),
                         co_anomaly.MAX_CO_ANOMALIES)

    def test_same_host_first(self):
        same = point("E1", "h1", start_min=0, end_min=100, severity="WARN")
        other = point("E2", "h2", start_min=0, end_min=100, severity="SEVERE")
        subject = self._subject_with([same, other])
        # 脅威度は other のほうが上だが、同一ホストが優先される
        self.assertEqual(subject.co_anomalies[0].event_id, "E1")

    def test_severity_second(self):
        low = point("E1", "h2", start_min=0, end_min=100, severity="WARN")
        high = point("E2", "h3", start_min=0, end_min=100, severity="SEVERE")
        subject = self._subject_with([low, high])
        self.assertEqual(subject.co_anomalies[0].event_id, "E2")

    def test_overlap_length_third(self):
        short = point("E1", "h2", start_min=0, end_min=10)
        long_ = point("E2", "h3", start_min=0, end_min=100)
        subject = self._subject_with([short, long_])
        self.assertEqual(subject.co_anomalies[0].event_id, "E2")

    def test_event_id_breaks_ties(self):
        """上位 3 キーが同値なら ID の昇順で決まる (NFR-009)。"""
        b = point("E-b", "h2", start_min=0, end_min=100)
        a = point("E-a", "h2", start_min=0, end_min=100)
        subject = self._subject_with([b, a])
        self.assertEqual([c.event_id for c in subject.co_anomalies],
                         ["E-a", "E-b"])

    def test_reproducible(self):
        def run(order):
            evs = [point("E{0}".format(i), "h{0}".format(i % 3),
                         start_min=i, end_min=i + 50) for i in order]
            co_anomaly.collect(evs)
            return {e.event_id: [c.event_id for c in e.co_anomalies]
                    for e in evs}

        self.assertEqual(run(range(8)), run(reversed(range(8))))


class TestCrossHostGroups(unittest.TestCase):
    def test_only_cross_host_groups_are_returned(self):
        a = point("E1", "h1", start_min=0, end_min=10)
        b = point("E2", "h2", start_min=0, end_min=10)
        same = point("E3", "h1", start_min=0, end_min=10)
        co_anomaly.collect([a, b, same])
        rows = co_anomaly.cross_host_groups([a, b, same])
        self.assertTrue(rows)
        self.assertGreaterEqual(len(rows[0]["hosts"]), 2)

    def test_single_host_produces_no_group(self):
        a = point("E1", "h1", start_min=0, end_min=10)
        b = point("E2", "h1", start_min=0, end_min=10)
        co_anomaly.collect([a, b])
        self.assertEqual(co_anomaly.cross_host_groups([a, b]), [])


class TestNoMetricsAccess(unittest.TestCase):
    def test_collect_takes_only_events(self):
        """DuckDB コネクションも設定も受け取らない (DS-11-01)。"""
        import inspect

        sig = inspect.signature(co_anomaly.collect)
        self.assertEqual(list(sig.parameters), ["events"])

    def test_empty_input_is_safe(self):
        co_anomaly.collect([])


if __name__ == "__main__":
    unittest.main()
