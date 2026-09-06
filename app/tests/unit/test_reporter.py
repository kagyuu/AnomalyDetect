"""U008 の単体テスト: reporter。"""

import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import causes, config, events, progress as progress_mod, reporter
from s_anomaly.detectors.base import Detection
from s_anomaly.errors import ReportWriteError

BASE_TS = datetime(2026, 6, 1, 0, 0, 0)
NOW = datetime(2026, 6, 15, 10, 23, 45)


def make_cfg():
    return config.load(None, progress_mod.setup(io.StringIO(), io.StringIO()))


class FakeCoAnomaly(object):
    """※CR-002: 同時に発生したアノマリー 1 件。"""

    def __init__(self, event_id, metric, severity="WARN", same_host=True,
                 overlap_seconds=300.0, source="jvm_gc", shape="spike_up"):
        self.event_id = event_id
        self.metric = metric
        self.severity = severity
        self.same_host = same_host
        self.overlap_seconds = overlap_seconds
        self.source = source
        self.host = "host01"
        self.shape = shape


def make_event(shape="spike_up", metric="active_connections",
               source="db_connection", algorithms=("ALG-A1",),
               values=None, detail=None, severity="WARN",
               series_id="db_connection/host01:7003/OraclePool_1",
               causes_list=None, co_anomalies=None, span_min=0):
    cfg = make_cfg()
    dets = []
    for i, alg in enumerate(algorithms):
        dets.append(Detection(
            series_id=series_id, source=source, metric=metric, algorithm=alg,
            start_ts=BASE_TS, end_ts=BASE_TS + timedelta(minutes=span_min),
            values=values if values is not None else [1.0, 2.0, 3.0],
            score=1.5, severity_hint="WARN",
            detail=(detail or {}).get(alg, {}), shape=shape,
        ))
    event = events.merge_detections(dets, cfg)[0]
    event.severity = severity
    event.causes = causes_list or []
    # None = 観点2 を含む (欄ごと省略)、[] = 観点1 だが相手なし
    event.co_anomalies = co_anomalies
    return event


def make_ctx(events_list, **kwargs):
    params = dict(
        now=NOW, target_dir="/var/log/apps",
        period=(BASE_TS, BASE_TS + timedelta(days=14)),
        file_count=42, record_count=1234567, series_count=128,
        events=events_list,
        algorithms=[("ALG-A1", "移動平均乖離率", "観点1", True, "k_warn=2.0"),
                    ("ALG-B3", "ローリング最小値の単調増加", "観点2", False, "")],
        load_errors=[], skipped=[], failures=[], vendor_note=None,
    )
    params.update(kwargs)
    return reporter.ReportContext(**params)


# ---------------------------------------------------------------------------
class TestFormatting(unittest.TestCase):
    def test_format_number(self):
        self.assertEqual(reporter.format_number(91.34567), "91.35")
        self.assertEqual(reporter.format_number(None), "不明")

    def test_format_values_short(self):
        self.assertEqual(reporter.format_values([1, 2, 3]), "1→2→3")

    def test_format_values_truncated(self):
        got = reporter.format_values(list(range(15)))
        self.assertIn("…", got)
        self.assertEqual(len(got.split("→")), 9)

    def test_format_values_empty(self):
        self.assertEqual(reporter.format_values([]), "(値なし)")

    def test_format_values_with_none(self):
        got = reporter.format_values([1.0, None, 3.0])
        self.assertIn("不明", got)

    def test_format_ts(self):
        # ※CR-008 タイムゾーンを併記する。既定は UTC。
        self.assertEqual(reporter.format_ts(NOW), "2026-06-15 10:23:45 (UTC)")
        self.assertEqual(reporter.format_ts(None), "不明")

    def test_metric_labels_cover_all(self):
        from s_anomaly import metrics as metrics_mod

        for name in metrics_mod.DETECTABLE_METRICS + metrics_mod.RATIO_METRICS:
            self.assertIn(name, reporter.METRIC_LABELS, name)


class TestRenderEvent(unittest.TestCase):
    def test_all_items_present(self):
        # ※CR-002: 観点1 のイベント (co_anomalies が None でない) では
        # 「同時に発生したアノマリー」欄まで全 10 項目が出る。
        event = make_event(co_anomalies=[])
        text = reporter.render_event(event)
        self.assertTrue(text.startswith("# イベントID( EVT-"))
        for label in ("* イベント種別 :", "* 脅威度 :", "* データ :", "* 値     :",
                      "* 開始   :", "* 終了   :", "* 現象   :", "* 説明   :",
                      "* 原因候補 :", "* 同時に発生したアノマリー :"):
            self.assertIn(label, text, label)

    def test_trend_event_omits_co_anomaly_item(self):
        """※CR-002: 観点2 を含むイベントでは項目行ごと出ない (UI-04-02 の例外)。"""
        text = reporter.render_event(make_event(co_anomalies=None))
        self.assertNotIn("同時に発生したアノマリー", text)
        # 他の 9 項目は出る
        self.assertIn("* 原因候補 :", text)

    def test_header_format_exact(self):
        event = make_event()
        first_line = reporter.render_event(event).splitlines()[0]
        # ※CR-003: ID にホスト名が入る
        self.assertRegex(first_line,
                         r"^# イベントID\( EVT-\d{8}-[^ ]+-\d{3} \)$")

    def test_no_paren_prefixed_items(self):
        text = reporter.render_event(make_event())
        for line in text.splitlines():
            self.assertFalse(line.startswith("( "), line)

    def test_empty_causes_uses_placeholder(self):
        text = reporter.render_event(make_event(causes_list=[]))
        self.assertIn("* 原因候補 : 該当する候補なし (確度: —)", text)

    def test_empty_co_anomalies_uses_placeholder(self):
        text = reporter.render_event(make_event(co_anomalies=[]))
        self.assertIn("(同じ時間帯に検知された他のアノマリーはありません)", text)

    def test_causes_listed(self):
        cause = causes.Cause("CR-01", "メモリリーク", "高", "理由です")
        text = reporter.render_event(make_event(causes_list=[cause]))
        self.assertIn("メモリリーク (確度: 高) — 理由です", text)

    def test_co_anomalies_listed(self):
        c = FakeCoAnomaly("EVT-20260601-h9-007", "fgc_delta",
                          severity="FATAL", same_host=False,
                          overlap_seconds=600.0)
        text = reporter.render_event(make_event(co_anomalies=[c]))
        self.assertIn("EVT-20260601-h9-007", text)
        self.assertIn("(他ホスト)", text)
        self.assertIn("fgc_delta", text)
        self.assertIn("FATAL", text)
        self.assertIn("重なり 10 分", text)

    def test_co_anomaly_same_host_label(self):
        c = FakeCoAnomaly("EVT-20260601-h1-002", "eu", same_host=True)
        text = reporter.render_event(make_event(co_anomalies=[c]))
        self.assertIn("(同一ホスト)", text)

    def test_format_overlap_units(self):
        self.assertEqual(reporter.format_overlap(0), "0 分")
        self.assertEqual(reporter.format_overlap(59), "0 分")
        self.assertEqual(reporter.format_overlap(600), "10 分")
        self.assertEqual(reporter.format_overlap(3600 * 5), "5.0 時間")
        self.assertEqual(reporter.format_overlap(86400 * 3), "3.0 日")

    def test_values_truncation_in_event(self):
        event = make_event(values=list(range(20)))
        self.assertIn("…", reporter.render_event(event))

    def test_all_seven_shapes_have_phenomenon(self):
        for shape in ("spike_up", "spike_down", "sustained", "floor_rise",
                      "trend_up", "level_shift", "seasonal_dev"):
            event = make_event(shape=shape, span_min=60)
            text = reporter.render_event(event)
            line = [l for l in text.splitlines() if l.startswith("* 現象")][0]
            body = line.split(":", 1)[1].strip()
            self.assertTrue(body, shape)

    def test_floor_rise_phrase(self):
        event = make_event(shape="floor_rise", algorithms=("ALG-B3",),
                           span_min=60 * 24 * 14,
                           detail={"ALG-B3": {"window_minutes": 360,
                                              "run_length": 56,
                                              "floor_start": 40.0,
                                              "floor_end": 92.0,
                                              "total_rise": 52.0}})
        text = reporter.render_event(event)
        self.assertIn("最低値が", text)

    def test_multiple_algorithms_in_type_and_explanation(self):
        event = make_event(algorithms=("ALG-A1", "ALG-B3"),
                           detail={"ALG-A1": {"dev": 3.0, "k_warn": 2.0,
                                              "k_fatal": 3.0},
                                   "ALG-B3": {"window_minutes": 360,
                                              "run_length": 20}})
        text = reporter.render_event(event)
        self.assertIn("ALG-A1", text)
        self.assertIn("ALG-B3", text)

    def test_missing_detail_says_unknown_but_keeps_line(self):
        event = make_event(algorithms=("ALG-A1",), detail={"ALG-A1": {}})
        text = reporter.render_event(event)
        self.assertIn("* 説明   :", text)
        self.assertIn("不明", text)

    def test_unknown_metric_no_error(self):
        event = make_event(metric="unknown_metric")
        text = reporter.render_event(event)
        self.assertIn("unknown_metric", text)


HOST = "host01"
YM = "202606"


def host_report(events_list, **kwargs):
    ctx = make_ctx(events_list, **kwargs)
    return reporter.render_host_report(ctx, HOST, YM, events_list,
                                       "report_summary_{0}.md".format(YM))


def summary_text(events_list, **kwargs):
    ctx = make_ctx(events_list, **kwargs)
    groups = reporter.group_events(events_list)
    files = {k: "report_{0}_{1}.md".format(k[0], k[1]) for k in groups}
    return reporter.render_summary(ctx, YM, groups, files)


class TestRenderHostReport(unittest.TestCase):
    """※CR-001・CR-004: ホスト別レポートは 5 章構成 (先頭に集計章)。"""

    def test_five_sections(self):
        text = host_report([make_event(co_anomalies=[])])
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項",
                        "### 5.1 読み飛ばした行",
                        "### 5.2 点数不足でスキップした系列",
                        "### 5.3 失敗したアルゴリズム"):
            self.assertIn(heading, text, heading)

    def test_title_has_host_and_month(self):
        text = host_report([make_event(co_anomalies=[])])
        self.assertTrue(text.startswith("# host01 2026-06 の異常検知レポート"))

    def test_digest_counts_match_events(self):
        """集計章の件数が、そのファイルのイベント実数と一致する (DS-12-07)。"""
        evs = [make_event(co_anomalies=[]) for _ in range(3)]
        text = host_report(evs)
        self.assertIn("| 検知イベント数 | 3 (SEVERE: 0, FATAL: 0, WARN: 3, INFO: 0) |",
                      text)

    def test_summary_links_to_summary_file(self):
        """どのサマリを見ればよいかが各ファイルから辿れる (UI-04-F08)。"""
        text = host_report([make_event(co_anomalies=[])])
        self.assertIn("| サマリ | report_summary_202606.md |", text)

    def test_summary_fields(self):
        text = host_report([make_event(co_anomalies=[])])
        self.assertIn("2026-06-15 10:23:45", text)
        self.assertIn("/var/log/apps", text)
        self.assertIn("1,234,567", text)

    def test_algorithms_table_includes_disabled(self):
        text = host_report([])
        self.assertIn("| ALG-B3 |", text)
        self.assertIn("無効", text)

    def test_zero_events_message(self):
        text = host_report([])
        self.assertIn("検知された異常はありません。", text)

    def test_empty_notes_use_placeholder(self):
        self.assertEqual(host_report([]).count("(なし)"), 3)

    def test_notes_tables(self):
        text = host_report(
            [], load_errors=[("a.csv", "列数不一致", 12)],
            skipped=[("ALG-A5", "基準期間の不足", 8)],
            failures=[("ALG-B2", "s/1", "m", "boom")])
        self.assertIn("| a.csv | 列数不一致 | 12 |", text)
        self.assertIn("| ALG-A5 | 基準期間の不足 | 8 |", text)
        self.assertIn("| ALG-B2 | s/1 | m | boom |", text)

    def test_vendor_note(self):
        text = host_report([], vendor_note="環境の DuckDB")
        self.assertIn("環境の DuckDB", text)

    def test_deterministic(self):
        evs = [make_event(co_anomalies=[])]
        self.assertEqual(host_report(evs), host_report(evs))

    def test_digest_fits_4k(self):
        """集計章は 4K トークン (目安 4,000 バイト) に収まる (UI-04-F07)。"""
        evs = [make_event(co_anomalies=[]) for _ in range(200)]
        digest = reporter.render_host_digest(HOST, YM, evs)
        self.assertLess(len(digest.encode("utf-8")), 4000)


class TestRenderSummary(unittest.TestCase):
    """※CR-004: サマリファイルは 4 章構成で 4K に収まる。"""

    def test_four_sections(self):
        text = summary_text([make_event(co_anomalies=[])])
        for heading in ("## 1. 実行サマリ", "## 2. ホスト別の集計",
                        "## 3. 日別のイベント数",
                        "## 4. 複数ホストで同時に発生したアノマリー"):
            self.assertIn(heading, text, heading)

    def test_title(self):
        self.assertTrue(summary_text([]).startswith("# 異常検知サマリ 2026-06"))

    def test_host_row_links_to_file(self):
        text = summary_text([make_event(co_anomalies=[])])
        self.assertIn("report_host01_202606.md", text)

    def test_host_total_matches_event_count(self):
        """サマリの合計が全イベント数と一致する (DS-12-07)。"""
        evs = [make_event(co_anomalies=[]) for _ in range(4)]
        text = summary_text(evs)
        self.assertIn("| 検知イベント数 | 4 (SEVERE: 0, FATAL: 0, WARN: 4, INFO: 0) |",
                      text)

    def test_zero_events_still_generates(self):
        text = summary_text([])
        self.assertIn("# 異常検知サマリ", text)
        self.assertIn("検知された異常はありません。", text)

    def test_fits_4k(self):
        evs = [make_event(co_anomalies=[]) for _ in range(300)]
        self.assertLess(len(summary_text(evs).encode("utf-8")), 4000)

    def test_deterministic(self):
        evs = [make_event(co_anomalies=[])]
        self.assertEqual(summary_text(evs), summary_text(evs))


class TestSafeHost(unittest.TestCase):
    def test_replaces_unsafe_chars(self):
        self.assertEqual(reporter.safe_host('a:b/c*d'), "a_b_c_d")

    def test_empty_becomes_unknown(self):
        self.assertEqual(reporter.safe_host(""), "unknown")

    def test_collision_gets_suffix(self):
        """置換で衝突したら連番を付す (DS-12-06)。"""
        m = reporter._safe_host_map(["a:b", "a/b", "plain"])
        self.assertEqual(len({m["a:b"], m["a/b"]}), 2)
        self.assertEqual(m["plain"], "plain")


if __name__ == "__main__":
    unittest.main()
