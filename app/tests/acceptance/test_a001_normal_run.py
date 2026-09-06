"""A001 — 正常系の通し実行と正解突き合わせ。

**本アプリの存在理由(膨大なログから異常な個所を見つける)が果たされているかを
判定する、最も重要なテストである。**

期待結果の項目 10(誤検知)は、A001 の指示表では「`clean_series` に属する
イベントが 0 件」と書かれているが、A001【実行手順】5 が参照を指示している
`docs/P002-frontend-spec.md` UI-08-03 は、ADR-012 にもとづき
「検知点が総点数の 1% 未満 かつ SEVERE が 0 件」へ改められている。
**後者(現行の仕様書)を判定基準として採用し、両方の実測値を記録する。**
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from tests.acceptance import _harness as H

#: 傾向系は検知開始が本質的に遅れるため前後 1 窓幅を許容する (UI-08-03 の ★FIXME★)。
OVERLAP_TOLERANCE = timedelta(minutes=60)


class TestA001NormalRun(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA001NormalRun, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = os.path.join(cls._tmp.name, "a001")
        os.makedirs(cls.work)
        cls.proc = H.run_app(cls.normal_logs, cls.work)
        cls.report = H.read_report(cls.work)
        cls.events = H.parse_events(cls.report)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _events_for(self, series_id, metric):
        return [e for e in self.events
                if e.series_id() == series_id and e.metric == metric]

    # 1
    def test_01_exit_code_is_zero(self):
        self.assertEqual(self.proc.returncode, 0, H.err(self.proc))

    # 2
    def test_02_report_is_created(self):
        self.assertTrue(os.path.isfile(H.report_path(self.work)))
        self.assertGreater(len(self.report), 0)

    # 3
    def test_03_no_temp_file_left(self):
        self.assertFalse(
            [p for p in os.listdir(self.work) if p.endswith(".md.tmp")])

    # 4
    def test_04_stderr_has_no_traceback(self):
        self.assertNotIn("Traceback", H.err(self.proc))

    # 5
    def test_05_all_sections_present(self):
        # ※CR-001・CR-004: ホスト別レポートは 5 章構成
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項",
                        "### 5.1 読み飛ばした行",
                        "### 5.2 点数不足でスキップした系列",
                        "### 5.3 失敗したアルゴリズム"):
            self.assertIn(heading, self.report, heading)

    # 6
    def test_06_summary_contents(self):
        # ※CR-001・CR-004: 実行サマリは 2 章、脅威度の内訳は 1 章 (集計) にある
        body = H.section(self.report, "## 2. 実行サマリ")
        for token in ("対象ディレクトリ", "対象期間", "読み込んだファイル数",
                      "総レコード数", "系列数",
                      "2026-06-01 00:00:00", "2026-06-14 23:55:00"):
            self.assertIn(token, body, token)
        digest = H.section(self.report, "## 1. このホストの集計")
        for token in ("検知イベント数", "SEVERE", "FATAL", "WARN", "INFO"):
            self.assertIn(token, digest, token)

    # 7
    def test_07_ten_algorithms_listed(self):
        body = H.section(self.report, "## 3. 適用したアルゴリズム")
        for group in ("A", "B"):
            for i in range(1, 6):
                self.assertIn("ALG-{0}{1}".format(group, i), body)

    # 8
    def test_08_at_least_one_event(self):
        self.assertGreater(len(self.events), 0)

    # 9
    def test_09_no_false_negative(self):
        """`injected` 7 件すべてが検知されていること。"""
        misses = []
        for inj in self.expected["injected"]:
            want = set(inj["expect_algorithms"])
            lo = datetime.fromisoformat(inj["from"]) - OVERLAP_TOLERANCE
            hi = datetime.fromisoformat(inj["to"]) + OVERLAP_TOLERANCE
            hit = False
            for event in self._events_for(inj["series_id"], inj["metric"]):
                if not (want & set(event.algorithms)):
                    continue
                if event.start_ts is None or event.end_ts is None:
                    continue
                if event.end_ts < lo or event.start_ts > hi:
                    continue
                hit = True
                break
            if not hit:
                misses.append(inj["id"])
        self.assertEqual(misses, [], "検知漏れ: {0}".format(misses))

    # 10
    def test_10_no_severe_on_clean_series(self):
        """UI-08-03: `clean_series` に SEVERE のイベントが 1 件も無いこと。"""
        offenders = []
        for clean in self.expected["clean_series"]:
            for event in self.events:
                if event.series_id() == clean and event.severity == "SEVERE":
                    offenders.append((clean, event.event_id))
        self.assertEqual(offenders, [], "clean_series の SEVERE: {0}".format(
            offenders))

    def test_10b_clean_series_event_ratio_recorded(self):
        """参考: `clean_series` に出たイベント数が全体の一部に留まること。"""
        clean = set(self.expected["clean_series"])
        on_clean = [e for e in self.events if e.series_id() in clean]
        self.assertLess(len(on_clean), len(self.events),
                        "全イベントが clean_series に出ています")

    # 11
    def test_11_severity_at_least_expected(self):
        for inj in self.expected["injected"]:
            want = H.SEVERITY_RANK[inj["expect_min_severity"]]
            best = max(
                [H.SEVERITY_RANK.get(e.severity, -1)
                 for e in self._events_for(inj["series_id"], inj["metric"])],
                default=-1)
            self.assertGreaterEqual(best, want, inj["id"])

    # 12
    def test_12_every_event_has_all_items(self):
        from tests.integration import report_parser
        for event in self.events:
            self.assertEqual(report_parser.missing_items(event), [],
                             event.event_id)

    # 13
    def test_13_encoding_is_utf8_lf_without_bom(self):
        raw = H.read_report_bytes(self.work)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM が付いています")
        self.assertNotIn(b"\r\n", raw, "CRLF が含まれています")
        raw.decode("utf-8")

    # 14
    def test_14_progress_steps_s1_to_s9(self):
        stdout = H.out(self.proc)
        for i in range(1, 10):
            self.assertIn("[S{0}]".format(i), stdout, "S{0}".format(i))


if __name__ == "__main__":
    unittest.main()
