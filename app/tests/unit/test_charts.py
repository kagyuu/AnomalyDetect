"""U011 の単体テスト: SVG チャートの生成 (※CR-009)。

**外部ライブラリを使わずに SVG を組み立てる**ため、正しさは文字列として確かめる。
重点は次の 3 つである。

1. **決定的であること**(NFR-009)。同じ入力なら同じバイト列になる。
2. **重ね合わせが実際に描かれること。** 設計上ここは一度取りこぼしており
   (`SEVERE` は観点2 であり同時アノマリーを持たない)、回帰試験が要る。
3. **出しすぎないこと。** 30 日規模でイベントは 7,229 件ある。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import charts


BASE = datetime(2026, 6, 1, 0, 0, 0)


class FakeCo(object):
    def __init__(self, event_id):
        self.event_id = event_id


class FakeEvent(object):
    def __init__(self, event_id, series_id="db_connection/h1:7003/ds",
                 metric="active_connections", severity="FATAL", score=1.0,
                 start=None, end=None, co=None):
        self.event_id = event_id
        self.series_id = series_id
        self.metric = metric
        self.severity = severity
        self.score = score
        self.start_ts = start or BASE
        self.end_ts = end or (BASE + timedelta(hours=1))
        self.co_anomalies = co
        self.chart_path = None


class FakeConfig(object):
    def __init__(self, enabled=True, min_severity="FATAL", per_host=10,
                 overlay_per_host=10, pad_ratio=0.25):
        self.chart_enabled = enabled
        self.chart_min_severity = min_severity
        self.chart_max_per_host = per_host
        self.chart_max_overlay_per_host = overlay_per_host
        self.chart_pad_ratio = pad_ratio


def ramp(n, start=10.0, step=0.5):
    return [start + step * i for i in range(n)]


def main_series(n=50, label="db_connection/h1:7003/ds  active_connections"):
    return {"label": label, "values": ramp(n),
            "stamps": ["2026-06-01 00:00:00", "2026-06-01 04:05:00"]}


# ---------------------------------------------------------------------------
# SVG の組み立て
# ---------------------------------------------------------------------------
class TestBuildSvg(unittest.TestCase):
    def test_produces_wellformed_svg(self):
        svg = charts.build_svg(main_series())
        self.assertTrue(svg.startswith("<svg "))
        self.assertTrue(svg.rstrip().endswith("</svg>"))
        self.assertIn("<polyline", svg)
        self.assertEqual(svg.count("<svg "), 1)

    def test_is_deterministic(self):
        """**同じ入力なら同じバイト列になる**(NFR-009)。"""
        a = charts.build_svg(main_series(), span=(10, 20))
        b = charts.build_svg(main_series(), span=(10, 20))
        self.assertEqual(a, b)

    def test_no_date_or_random_in_output(self):
        """日付や乱数を埋めない。**埋めると実行ごとに変わる。**"""
        svg = charts.build_svg(main_series())
        for banned in ("<!--", "dc:date", "created", "generated"):
            self.assertNotIn(banned, svg.lower(), banned)

    def test_empty_series_returns_none(self):
        self.assertIsNone(charts.build_svg({"label": "x", "values": []}))

    def test_flat_series_does_not_divide_by_zero(self):
        """完全に平坦な系列でも描ける(最大 = 最小)。"""
        svg = charts.build_svg({"label": "flat", "values": [7.0] * 20})
        self.assertIn("<polyline", svg)

    def test_shades_the_anomaly_span(self):
        with_span = charts.build_svg(main_series(), span=(10, 20))
        without = charts.build_svg(main_series())
        self.assertIn(charts.SPAN_FILL, with_span)
        self.assertNotIn(charts.SPAN_FILL, without)

    def test_escapes_special_characters(self):
        """系列名に `&` や `<` が来ても壊れない。"""
        svg = charts.build_svg(
            {"label": 'a&b<c>"d"', "values": ramp(10)})
        self.assertIn("a&amp;b&lt;c&gt;&quot;d&quot;", svg)
        self.assertNotIn("<c>", svg)

    def test_timezone_is_noted(self):
        """**どのタイムゾーンの時刻かを図にも書く**(※CR-008 と揃える)。"""
        svg = charts.build_svg(main_series(), timezone="Asia/Tokyo")
        self.assertIn("Asia/Tokyo", svg)


class TestOverlay(unittest.TestCase):
    def test_overlay_adds_a_second_panel_and_legend(self):
        """下段には**本体の系列も引く。** 比較の相手が無いと重ねる意味がない。

        したがって折れ線は 3 本(上段の本体 + 下段の本体 + 下段の相手 1)。
        """
        overlays = [{"label": "other/one  pend", "values": ramp(50, 100, -1)}]
        svg = charts.build_svg(main_series(), overlays)
        self.assertEqual(svg.count("<polyline"), 3)
        self.assertIn("同時に発生したアノマリー", svg)
        self.assertIn("other/one  pend", svg)

    def test_legend_shows_real_ranges(self):
        """正規化で単位が消えるため、**実際の最小・最大を凡例に出す。**"""
        overlays = [{"label": "pend", "values": [0.0, 5.0, 10.0]}]
        svg = charts.build_svg(main_series(3), overlays)
        self.assertIn("(0 〜 10)", svg)

    def test_overlay_count_is_capped(self):
        overlays = [{"label": "s%d" % i, "values": ramp(20)}
                    for i in range(20)]
        svg = charts.build_svg(main_series(20), overlays)
        # 本体 1 本 + 上限ぶん
        self.assertEqual(svg.count("<polyline"),
                         1 + charts.MAX_OVERLAY_SERIES + 1)

    def test_no_overlay_means_single_panel(self):
        svg = charts.build_svg(main_series())
        self.assertNotIn("同時に発生したアノマリー", svg)


# ---------------------------------------------------------------------------
# 対象イベントの選別
# ---------------------------------------------------------------------------
class TestSelectEvents(unittest.TestCase):
    def test_disabled_selects_nothing(self):
        events = [FakeEvent("E1")]
        self.assertEqual(charts.select_events(events, FakeConfig(enabled=False)),
                         [])

    def test_min_severity_is_a_floor(self):
        events = [FakeEvent("E1", severity="WARN"),
                  FakeEvent("E2", severity="FATAL")]
        got = charts.select_events(events, FakeConfig(min_severity="FATAL"))
        self.assertEqual([e.event_id for e in got], ["E2"])

    def test_per_host_cap(self):
        events = [FakeEvent("E%02d" % i, score=float(i)) for i in range(30)]
        got = charts.select_events(events, FakeConfig(per_host=5,
                                                     overlay_per_host=0))
        self.assertEqual(len(got), 5)

    def test_hosts_are_counted_separately(self):
        events = ([FakeEvent("A%d" % i, series_id="db_connection/h1:1/d")
                   for i in range(10)]
                  + [FakeEvent("B%d" % i, series_id="db_connection/h2:1/d")
                     for i in range(10)])
        got = charts.select_events(events, FakeConfig(per_host=3,
                                                     overlay_per_host=0))
        self.assertEqual(len(got), 6)

    def test_severe_is_preferred(self):
        events = [FakeEvent("E1", severity="FATAL", score=99.0),
                  FakeEvent("E2", severity="SEVERE", score=0.1)]
        got = charts.select_events(events, FakeConfig(per_host=1,
                                                     overlay_per_host=0))
        self.assertEqual([e.event_id for e in got], ["E2"])

    def test_overlay_quota_is_separate(self):
        """**別枠が無いと重ね合わせが一度も描かれない。**

        `SEVERE` は定義上「観点2 の検知」であり、観点2 のイベントには
        同時アノマリーが付かない(CR-002)。脅威度順だけで選ぶと、
        枠を SEVERE が食い尽くして重ね合わせが出なくなる。**その回帰試験である。**
        """
        events = [FakeEvent("S%d" % i, severity="SEVERE", score=float(100 - i))
                  for i in range(5)]
        events.append(FakeEvent("P1", severity="FATAL", score=0.1,
                                co=[FakeCo("S0")]))
        got = charts.select_events(events, FakeConfig(per_host=5,
                                                     overlay_per_host=3))
        ids = [e.event_id for e in got]
        self.assertIn("P1", ids, "同時アノマリーを持つイベントが選ばれていない")
        self.assertEqual(len(got), 6)

    def test_overlay_quota_zero_disables_the_second_pass(self):
        events = [FakeEvent("S1", severity="SEVERE", score=9.0),
                  FakeEvent("P1", severity="FATAL", co=[FakeCo("S1")])]
        got = charts.select_events(events, FakeConfig(per_host=1,
                                                      overlay_per_host=0))
        self.assertEqual([e.event_id for e in got], ["S1"])

    def test_no_duplicates_between_quotas(self):
        events = [FakeEvent("P1", severity="SEVERE", co=[FakeCo("X")])]
        got = charts.select_events(events, FakeConfig())
        self.assertEqual(len(got), 1)

    def test_output_order_is_by_event_id(self):
        """**出力順も決定的である**(NFR-009)。"""
        events = [FakeEvent("E3", score=1.0), FakeEvent("E1", score=3.0),
                  FakeEvent("E2", score=2.0)]
        got = charts.select_events(events, FakeConfig())
        self.assertEqual([e.event_id for e in got], ["E1", "E2", "E3"])


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------
class TestHelpers(unittest.TestCase):
    def test_thin_out_keeps_both_ends(self):
        points = list(range(1000))
        got = charts.thin_out(points, 100)
        self.assertEqual(len(got), 100)
        self.assertEqual(got[0], 0)
        self.assertEqual(got[-1], 999)

    def test_thin_out_is_a_noop_when_short(self):
        self.assertEqual(charts.thin_out([1, 2, 3], 100), [1, 2, 3])

    def test_window_pads_around_the_event(self):
        event = FakeEvent("E1", start=BASE, end=BASE + timedelta(hours=2))
        t_from, t_to = charts.window_of(event, 0.25)
        self.assertLess(t_from, event.start_ts)
        self.assertGreater(t_to, event.end_ts)

    def test_window_pad_has_a_floor(self):
        """一瞬のイベントでも前後が見えるだけの幅を取る。"""
        event = FakeEvent("E1", start=BASE, end=BASE)
        t_from, t_to = charts.window_of(event, 0.25)
        self.assertEqual((event.start_ts - t_from).total_seconds(),
                         charts.MIN_PAD_SECONDS)

    def test_window_pad_has_a_ceiling(self):
        """30 日続くイベントでも余白は際限なく広がらない。"""
        event = FakeEvent("E1", start=BASE, end=BASE + timedelta(days=30))
        t_from, t_to = charts.window_of(event, 0.25)
        self.assertEqual((event.start_ts - t_from).total_seconds(),
                         charts.MAX_PAD_SECONDS)

    def test_relative_path_is_not_absolute(self):
        """**レポートは別の場所へ移されうる。** 絶対パスにしてはならない。"""
        path = charts.relative_path("EVT-20260601-h1-001")
        self.assertFalse(path.startswith("/"))
        self.assertFalse(path.startswith("\\"))
        self.assertNotIn(":", path)
        self.assertEqual(path, "charts/EVT-20260601-h1-001.svg")

    def test_write_svg_creates_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = charts.write_svg(tmp, "EVT-1", "<svg/>\n")
            self.assertEqual(rel, "charts/EVT-1.svg")
            full = os.path.join(tmp, "charts", "EVT-1.svg")
            self.assertTrue(os.path.isfile(full))
            with open(full, "rb") as handle:
                self.assertEqual(handle.read(), b"<svg/>\n")

    def test_write_svg_uses_lf(self):
        """改行は LF(レポート本文と同じ物理仕様)。"""
        with tempfile.TemporaryDirectory() as tmp:
            charts.write_svg(tmp, "EVT-2", "a\nb\n")
            with open(os.path.join(tmp, "charts", "EVT-2.svg"), "rb") as handle:
                self.assertNotIn(b"\r\n", handle.read())

    def test_align_holds_the_last_value(self):
        """採取間隔が違う系列を、本体の時刻列に合わせる。"""
        stamps = [BASE + timedelta(minutes=5 * i) for i in range(6)]
        rows = [(BASE, 1.0), (BASE + timedelta(minutes=15), 2.0)]
        got = charts._align(rows, stamps)
        self.assertEqual(got, [1.0, 1.0, 1.0, 2.0, 2.0, 2.0])

    def test_align_returns_none_for_empty(self):
        self.assertIsNone(charts._align([], [BASE]))

    def test_severities_match_events_module(self):
        """`config.CHART_SEVERITIES` が `events.SEVERITY_ORDER` と一致すること。

        循環 import を避けるため定義を分けている。**ずれると静かに壊れる。**
        """
        from s_anomaly import events as events_mod
        self.assertEqual(charts.CHART_SEVERITIES, events_mod.SEVERITY_ORDER)


class TestNoChartPlaceholder(unittest.TestCase):
    """「チャートなし」の画像 (※CR-009。2026-09-06 の指示)。

    **図が無いと「ツールの不備」に見える**ため、チャートが無いイベントにも
    画像を出す。**画像は 1 つだけ作り、全イベントが同じものを参照する。**
    """

    def test_is_wellformed_svg(self):
        svg = charts.build_no_chart_svg()
        self.assertTrue(svg.startswith("<svg "))
        self.assertTrue(svg.rstrip().endswith("</svg>"))
        self.assertIn("[NO CHART]", svg)

    def test_is_small(self):
        """折れ線の図より小さくする。一覧したときに区別がつくように。"""
        self.assertLess(charts.NO_CHART_WIDTH, charts.WIDTH)
        self.assertLess(len(charts.build_no_chart_svg()), 1000)

    def test_does_not_explain_the_reason(self):
        """**理由は書かない**(2026-09-06 の指示)。分量に効くため。"""
        svg = charts.build_no_chart_svg()
        for word in ("脅威度", "上限", "点数", "SEVERE", "FATAL"):
            self.assertNotIn(word, svg, word)

    def test_is_deterministic(self):
        self.assertEqual(charts.build_no_chart_svg(),
                         charts.build_no_chart_svg())

    def test_path_is_shared_and_relative(self):
        """**全イベントが同じパスを参照する。**"""
        path = charts.no_chart_path()
        self.assertEqual(path, "charts/no-chart.svg")
        self.assertFalse(path.startswith("/"))
        self.assertEqual(charts.no_chart_path(), path)

    def test_write_creates_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = charts.write_no_chart(tmp)
            self.assertEqual(rel, "charts/no-chart.svg")
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "charts", "no-chart.svg")))

    def test_write_is_idempotent(self):
        """2 回書いても 1 ファイルのまま。"""
        with tempfile.TemporaryDirectory() as tmp:
            charts.write_no_chart(tmp)
            charts.write_no_chart(tmp)
            self.assertEqual(os.listdir(os.path.join(tmp, "charts")),
                             ["no-chart.svg"])


class TestFewPointsReturnsRows(unittest.TestCase):
    """点数不足のときは SVG ではなく生の点列を返す (※CR-009)。"""

    def test_min_points_is_the_documented_value(self):
        self.assertEqual(charts.MIN_POINTS, 5)

    def test_max_points_is_the_documented_value(self):
        self.assertEqual(charts.MAX_POINTS, 360)


class TestConfigValidation(unittest.TestCase):
    """`chart.min_severity` は決められた値のいずれかでなければならない。"""

    def _load(self, text):
        import io as _io
        import tempfile as _tf
        from s_anomaly import config, progress as progress_mod
        progress = progress_mod.setup(_io.StringIO(), _io.StringIO())
        with _tf.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.properties")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            return config.load(path, progress)

    def test_valid_value_passes(self):
        cfg = self._load("[chart]\nmin_severity = WARN\n")
        self.assertEqual(cfg.chart_min_severity, "WARN")

    def test_unknown_value_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._load("[chart]\nmin_severity = CRITICAL\n")
        self.assertEqual(ctx.exception.exit_code, 3)
        self.assertIn("CRITICAL", str(ctx.exception.detail))

    def test_case_is_significant(self):
        """`severe` は通らない(レポートの表記と揃えて大文字である)。"""
        with self.assertRaises(Exception) as ctx:
            self._load("[chart]\nmin_severity = severe\n")
        self.assertEqual(ctx.exception.exit_code, 3)


if __name__ == "__main__":
    unittest.main()
