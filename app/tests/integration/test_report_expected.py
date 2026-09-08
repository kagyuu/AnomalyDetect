"""T006 — レポート生成と正解の突き合わせ (モジュール連結、プロセス起動なし)。

プロセスを起動する確認は A001 (P009) が担当する。ここでは write_reports が
返す文字列を検証する。
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import (
    bootstrap, causes, co_anomaly, config, detectors, discovery, events,
    loaders, metrics, progress as progress_mod, reporter, schema,
)
from s_anomaly.loaders import bqueues, dbconn, jstat, sar
from tests.integration import _setup_baseline, report_parser

def _join_host_reports(out_dir):
    """ホスト別ファイルだけを名前順に連結する (※CR-001)。"""
    import glob

    parts = []
    for path in sorted(glob.glob(os.path.join(out_dir, "report_*.md"))):
        if os.path.basename(path).startswith("report_summary_"):
            continue
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parts.append(handle.read())
    return chr(10).join(parts)


NOW = datetime(2026, 6, 15, 10, 23, 45)
OVERLAP_TOLERANCE = timedelta(minutes=60)
SEVERITY_RANK = {"INFO": 0, "WARN": 1, "FATAL": 2, "SEVERE": 3}


class TestReportExpected(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()
        cls.progress = progress_mod.setup(io.StringIO(), io.StringIO())
        cls._tmp = tempfile.TemporaryDirectory()
        # ※CR-001: レポート群の出力先
        cls._out = tempfile.TemporaryDirectory()
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
        logs = os.path.join(cls.paths["normal"], "logs")
        files = discovery.discover(logs, cls.progress)
        records = 0
        for logfile in files:
            records += loader[logfile.kind](cls.con, logfile, cls.progress).ok_rows
        loaders.dedupe_all(cls.con)
        metrics.build_jvm_gc_derived(cls.con)
        metrics.build_metrics(cls.con)
        cfg = config.load(None, cls.progress)
        series = metrics.list_series(cls.con)

        detections, skips = [], []
        for detector in detectors.enabled_detectors(cfg):
            for meta in series:
                d, s = detector.run(cls.con, cfg, meta, cls.progress)
                detections.extend(d)
                skips.extend(s)

        evs = events.merge_detections(detections, cfg)
        events.assign_severity(evs, cls.con, cfg)
        co_anomaly.collect(evs)
        causes.assign_causes(evs, cfg)
        ordered = events.sort_for_report(evs)

        ctx = reporter.ReportContext(
            now=NOW, target_dir=logs,
            period=(datetime(2026, 6, 1), datetime(2026, 6, 14, 23, 55)),
            file_count=len(files), record_count=records,
            series_count=len(series), events=ordered,
            algorithms=[(a, detectors.REGISTRY[a].name,
                         detectors.REGISTRY[a].aspect, True, "")
                        for a in sorted(detectors.REGISTRY)],
            load_errors=[], skipped=[], failures=[], vendor_note=None,
        )
        cls.ctx = ctx
        # ※CR-001: レポート群を生成し、ホスト別ファイルを連結して解析する。
        # 突き合わせは全ファイルを合わせた集合に対して行う (P006 TP-15)。
        cls.written = reporter.write_reports(ctx, cls._out.name)
        cls.text = _join_host_reports(cls._out.name)
        cls.parsed = report_parser.parse_events(cls.text)
        with open(os.path.join(cls.paths["normal"], "expected.json"),
                  "r", encoding="utf-8") as handle:
            cls.expected = json.load(handle)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()
        cls._out.cleanup()

    def _events_for(self, series_id, metric):
        return [e for e in self.parsed
                if e.series_id() == series_id and e.metric == metric]

    def test_01_title(self):
        # ※CR-001: 各ホスト別ファイルが「# {ホスト} {年}-{月} の異常検知レポート」で始まる
        self.assertRegex(self.text.split(chr(10))[0],
                         r"^# \S+ \d{4}-\d{2} の異常検知レポート$")

    def test_02_03_sections(self):
        # ※CR-001・CR-004: ホスト別レポートは 5 章構成 (先頭に集計章)
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項",
                        "### 5.1 読み飛ばした行",
                        "### 5.2 点数不足でスキップした系列",
                        "### 5.3 失敗したアルゴリズム"):
            self.assertIn(heading, self.text, heading)

    def test_04_summary_fields(self):
        self.assertIn("2026-06-15 10:23:45", self.text)
        self.assertIn("2026-06-01 00:00:00", self.text)

    def test_05_ten_algorithm_rows(self):
        for i in range(1, 6):
            self.assertIn("| ALG-A{0} |".format(i), self.text)
            self.assertIn("| ALG-B{0} |".format(i), self.text)

    def test_06_empty_notes(self):
        # ※CR-001: 注意事項は各ホスト別ファイルに出るため、3 x ファイル数
        n_files = len([p for p in self.written
                       if "report_summary_" not in os.path.basename(p)])
        self.assertEqual(self.text.count("(なし)"), 3 * n_files)

    def test_07_event_headings(self):
        # ※CR-003: ID にホスト名が入る
        for event in self.parsed:
            self.assertRegex(event.event_id, r"^EVT-\d{8}-.+-\d{3}$")

    def test_08_no_paren_items(self):
        for line in self.text.splitlines():
            self.assertFalse(line.startswith("( "), line)

    def test_09_all_items_present(self):
        for event in self.parsed:
            self.assertEqual(report_parser.missing_items(event), [],
                             event.event_id)

    def test_10_11_placeholders_used(self):
        no_cause = [e for e in self.parsed if "該当する候補なし" in e.causes_text]
        self.assertTrue(no_cause)
        # ※CR-002: 「同時に発生したアノマリー」欄は観点1 のイベントにのみ出る。
        # 出ているイベントでは、明細が 1 行以上ある (該当なしの定型文を含む)。
        with_item = [e for e in self.parsed
                     if "同時に発生したアノマリー" in e.items]
        self.assertTrue(with_item, "同時アノマリー欄を持つイベントが無い")
        for event in with_item:
            self.assertTrue(event.correlations, event.event_id)

    def test_12_values_truncation(self):
        truncated = [e for e in self.parsed if "…" in e.values_text]
        self.assertTrue(truncated)
        for event in truncated:
            self.assertEqual(len(event.values_text.split("→")), 9)

    def test_13_phenomenon_not_empty(self):
        for event in self.parsed:
            self.assertTrue(event.phenomenon.strip(), event.event_id)

    def test_14_sorted_by_severity(self):
        # ※CR-001: 並び順はファイル内で保たれる。連結すると
        # ファイル境界で戻るため、ファイルごとに確認する。
        import glob

        for path in sorted(glob.glob(os.path.join(self._out.name, "report_*.md"))):
            if os.path.basename(path).startswith("report_summary_"):
                continue
            with open(path, "r", encoding="utf-8", newline="") as handle:
                evs = report_parser.parse_events(handle.read())
            ranks = [SEVERITY_RANK.get(e.severity, -1) for e in evs]
            self.assertEqual(ranks, sorted(ranks, reverse=True),
                             os.path.basename(path))

    def test_15_no_missed_injections(self):
        misses = []
        for inj in self.expected["injected"]:
            want = set(inj["expect_algorithms"])
            t_from = datetime.fromisoformat(inj["from"]) - OVERLAP_TOLERANCE
            t_to = datetime.fromisoformat(inj["to"]) + OVERLAP_TOLERANCE
            hit = any(
                (want & set(e.algorithms))
                and e.start_ts is not None and e.end_ts is not None
                and not (e.end_ts < t_from or e.start_ts > t_to)
                for e in self._events_for(inj["series_id"], inj["metric"])
            )
            if not hit:
                misses.append(inj["id"])
        self.assertEqual(misses, [], "検知漏れ: {0}".format(misses))

    def test_16_false_positives_within_limit(self):
        for clean in self.expected["clean_series"]:
            evs = [e for e in self.parsed if e.series_id() == clean]
            self.assertEqual([e.event_id for e in evs if e.severity == "SEVERE"], [])

    def test_17_severity_at_least_expected(self):
        for inj in self.expected["injected"]:
            want = SEVERITY_RANK[inj["expect_min_severity"]]
            evs = self._events_for(inj["series_id"], inj["metric"])
            best = max([SEVERITY_RANK.get(e.severity, -1) for e in evs], default=-1)
            self.assertGreaterEqual(best, want, inj["id"])

    def test_18_deterministic(self):
        # ※CR-001: 2 回書き出して、ファイル名・件数・内容が一致すること
        second_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, second_dir, True)
        reporter.write_reports(self.ctx, second_dir)
        self.assertEqual(_join_host_reports(second_dir), self.text)

    def test_19_every_timestamp_carries_a_timezone(self):
        """※CR-008 — **レポートの全ての時刻欄にタイムゾーンが併記されること。**

        書式は `2026-06-15 10:23:45 (UTC)`。**1 箇所でも欠けていれば失敗する。**
        """
        import re

        bare = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?! \()")
        offenders = [
            line.strip() for line in self.text.splitlines()
            if bare.search(line)
        ]
        self.assertEqual(offenders, [], "タイムゾーンの併記が無い行: {0}".format(
            offenders[:5]))

    def test_20_timezone_label_is_the_storage_timezone(self):
        """併記されるのは**格納タイムゾーン**である(表示のための再変換はしない)。"""
        self.assertIn("(UTC)", self.text)
        self.assertEqual(reporter.timezone_label(), "UTC")


if __name__ == "__main__":
    unittest.main()
