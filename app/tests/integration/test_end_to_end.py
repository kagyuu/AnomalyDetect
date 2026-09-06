"""U008-T4 / T006 — 通し確認と正解突き合わせ (P006 TP-15)。

サブプロセスとしてアプリを起動し、レポート群を解析して expected.json と
突き合わせる (ADR-006)。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

from tests.integration import _setup_baseline, report_parser

APP_DIR = _setup_baseline.APP_DIR
ENTRY = os.path.join(APP_DIR, "s_anomaly.py")

#: 期間の重なりの許容幅 (P002 8.3)
OVERLAP_TOLERANCE = timedelta(minutes=60)

#: clean_series の許容検知率 (ADR-012 により「0 件」から改めた)
CLEAN_MAX_RATIO = 0.01

SEVERITY_RANK = {"INFO": 0, "WARN": 1, "FATAL": 2, "SEVERE": 3}

_TIMESTAMP_LINE = re.compile(r"^\| 実行日時 \| .* \|$")


def run_app(logs_dir, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, ENTRY, str(logs_dir)],
        cwd=str(cwd), capture_output=True, env=env,
    )


def decode(raw):
    return raw.decode("utf-8", errors="replace")


def report_paths(work_dir, kind="host"):
    """出力されたレポート群のパス (※CR-001)。

    kind: "host" (イベント本文) / "summary" / "all"
    """
    import glob

    paths = sorted(glob.glob(os.path.join(work_dir, "report_*.md")))
    if kind == "all":
        return paths
    is_summary = lambda p: os.path.basename(p).startswith("report_summary_")
    return [p for p in paths if (is_summary(p) if kind == "summary"
                                 else not is_summary(p))]


def read_report(work_dir):
    """ホスト別ファイルを名前順に連結して返す (※CR-001)。

    突き合わせは全ファイルを合わせた集合に対して行う (P006 TP-15)。
    """
    parts = []
    for path in report_paths(work_dir):
        with open(path, "r", encoding="utf-8") as handle:
            parts.append(handle.read())
    return chr(10).join(parts)


def read_summary(work_dir):
    parts = []
    for path in report_paths(work_dir, "summary"):
        with open(path, "r", encoding="utf-8") as handle:
            parts.append(handle.read())
    return chr(10).join(parts)


def strip_volatile(text):
    """実行日時の行を除いた本文を返す (再現性の比較用)。"""
    return "\n".join(
        line for line in text.splitlines() if not _TIMESTAMP_LINE.match(line)
    )


class EndToEndBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()
        cls.normal_logs = os.path.join(cls.paths["normal"], "logs")
        cls.broken_logs = os.path.join(cls.paths["broken"], "logs")
        with open(os.path.join(cls.paths["normal"], "expected.json"),
                  "r", encoding="utf-8") as handle:
            cls.expected = json.load(handle)
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = os.path.join(cls._tmp.name, "run1")
        os.makedirs(cls.work)
        cls.proc = run_app(cls.normal_logs, cls.work)
        cls.report = read_report(cls.work) if cls.proc.returncode == 0 else ""
        cls.events = report_parser.parse_events(cls.report)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestNormalRun(EndToEndBase):
    def test_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, decode(self.proc.stderr))

    def test_no_traceback(self):
        self.assertNotIn("Traceback", decode(self.proc.stderr))

    def test_no_tmp_left(self):
        import glob
        self.assertFalse(glob.glob(os.path.join(self.work, "*.md.tmp")))

    def test_sections(self):
        # ※CR-001・CR-004: ホスト別レポートは 5 章構成 (先頭に集計章)
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項", "### 5.1 読み飛ばした行",
                        "### 5.2 点数不足でスキップした系列",
                        "### 5.3 失敗したアルゴリズム"):
            self.assertIn(heading, self.report, heading)

    def test_summary_sections(self):
        """※CR-004: サマリは 4 章構成。"""
        summary = read_summary(self.work)
        for heading in ("# 異常検知サマリ", "## 1. 実行サマリ",
                        "## 2. ホスト別の集計", "## 3. 日別のイベント数",
                        "## 4. 複数ホストで同時に発生したアノマリー"):
            self.assertIn(heading, summary, heading)

    def test_all_ten_algorithms_listed(self):
        for i in range(1, 6):
            self.assertIn("| ALG-A{0} |".format(i), self.report)
            self.assertIn("| ALG-B{0} |".format(i), self.report)

    def test_events_present(self):
        self.assertGreater(len(self.events), 0)

    def test_every_event_has_all_items(self):
        for event in self.events:
            self.assertEqual(report_parser.missing_items(event), [],
                             "{0} に欠けた項目があります".format(event.event_id))

    def test_report_encoding_and_newlines(self):
        raw = b""
        for path in report_paths(self.work, "all"):
            with open(path, "rb") as handle:
                raw += handle.read()
        self.assertNotIn(b"\r\n", raw)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))

    def test_progress_steps_in_stdout(self):
        out = decode(self.proc.stdout)
        for step in ["S{0}".format(i) for i in range(1, 10)]:
            self.assertIn("[{0}]".format(step), out, step)

    def test_events_sorted_by_severity(self):
        # ※CR-001: 並び順はファイル内で保たれる。連結するとファイル境界で
        # 戻るため、ファイルごとに確認する。
        for path in report_paths(self.work):
            with open(path, "r", encoding="utf-8") as handle:
                evs = report_parser.parse_events(handle.read())
            ranks = [SEVERITY_RANK.get(e.severity, -1) for e in evs]
            self.assertEqual(ranks, sorted(ranks, reverse=True),
                             os.path.basename(path))


class TestExpectedMatch(EndToEndBase):
    def _events_for(self, series_id, metric):
        return [e for e in self.events
                if e.series_id() == series_id and e.metric == metric]

    def test_no_missed_injections(self):
        """検知漏れ: injected 全件が期待アルゴリズムのいずれかで検知される。"""
        misses = []
        for inj in self.expected["injected"]:
            want = set(inj["expect_algorithms"])
            t_from = datetime.fromisoformat(inj["from"]) - OVERLAP_TOLERANCE
            t_to = datetime.fromisoformat(inj["to"]) + OVERLAP_TOLERANCE
            hit = False
            for event in self._events_for(inj["series_id"], inj["metric"]):
                if event.start_ts is None or event.end_ts is None:
                    continue
                if event.end_ts < t_from or event.start_ts > t_to:
                    continue
                if want & set(event.algorithms):
                    hit = True
                    break
            if not hit:
                misses.append(inj["id"])
        self.assertEqual(misses, [], "検知漏れ: {0}".format(misses))

    def test_severity_at_least_expected(self):
        failures = []
        for inj in self.expected["injected"]:
            want_rank = SEVERITY_RANK[inj["expect_min_severity"]]
            events = self._events_for(inj["series_id"], inj["metric"])
            best = max([SEVERITY_RANK.get(e.severity, -1) for e in events],
                       default=-1)
            if best < want_rank:
                failures.append((inj["id"], inj["expect_min_severity"], best))
        self.assertEqual(failures, [], "脅威度不足: {0}".format(failures))

    def test_false_positive_rate_within_limit(self):
        """誤検知: clean_series の検知率が 1% 未満、SEVERE が 0 件 (ADR-012)。"""
        for clean in self.expected["clean_series"]:
            events = [e for e in self.events if e.series_id() == clean]
            severes = [e for e in events if e.severity == "SEVERE"]
            self.assertEqual(
                severes, [],
                "clean_series に SEVERE が出ています: {0}".format(
                    [e.event_id for e in severes]),
            )
            # 系列あたりの点数は 4032 x メトリクス数。イベント数で比率を見る
            self.assertLess(
                len(events), 4032 * CLEAN_MAX_RATIO * 4,
                "clean_series の検知が多すぎます: {0} 件".format(len(events)),
            )

    def test_known_causes_identified(self):
        """メモリリークとジョブ滞留が原因候補として特定されること。"""
        leak = self._events_for("jvm_gc/app01@host01", "ou")
        self.assertTrue(any("メモリリーク" in e.causes_text for e in leak),
                        [e.causes_text for e in leak])
        stall = self._events_for("lsf_queue/lsfhost01/long", "pend")
        self.assertTrue(any("計算資源の枯渇" in e.causes_text for e in stall),
                        [e.causes_text for e in stall])


class TestReproducibility(EndToEndBase):
    def test_two_runs_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            work2 = os.path.join(tmp, "run2")
            os.makedirs(work2)
            proc2 = run_app(self.normal_logs, work2)
            self.assertEqual(proc2.returncode, 0, decode(proc2.stderr))
            report2 = read_report(work2)
        self.assertEqual(strip_volatile(self.report), strip_volatile(report2))

    def test_third_run_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            work3 = os.path.join(tmp, "run3")
            os.makedirs(work3)
            proc3 = run_app(self.normal_logs, work3)
            self.assertEqual(proc3.returncode, 0, decode(proc3.stderr))
            report3 = read_report(work3)
        self.assertEqual(strip_volatile(self.report), strip_volatile(report3))


class TestRestartResilience(EndToEndBase):
    def test_second_run_in_same_dir(self):
        """残骸がある状態での 2 回目が 1 回目と同じ結果になること (O-02)。"""
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "restart")
            os.makedirs(work)
            first = run_app(self.normal_logs, work)
            self.assertEqual(first.returncode, 0, decode(first.stderr))
            report1 = read_report(work)

            # 意図的に残骸を作る。※CR-001: 出力対象のホストの一時ファイルと、
            # 出力対象でないホストの一時ファイルの両方を置く。
            leftovers = [os.path.basename(p) + ".tmp"
                         for p in report_paths(work, "all")]
            leftovers.append("report_stale-host_202606.md.tmp")
            for name in leftovers:
                with open(os.path.join(work, name), "w",
                          encoding="utf-8") as handle:
                    handle.write("### 途中で切れたレポート")

            second = run_app(self.normal_logs, work)
            self.assertEqual(second.returncode, 0, decode(second.stderr))
            self.assertNotIn("Traceback", decode(second.stderr))
            report2 = read_report(work)
            # 今回出力したファイルの一時ファイルは消える
            for path in report_paths(work, "all"):
                self.assertFalse(os.path.exists(path + ".tmp"),
                                 os.path.basename(path))
            # ※CR-001 / UI-04-05: 出力対象でないホストの残骸は**消さない**。
            # これは仕様どおりの挙動であり、次回実行を妨げないことが要件である。
            self.assertTrue(os.path.exists(
                os.path.join(work, "report_stale-host_202606.md.tmp")))

            third = run_app(self.normal_logs, work)
            self.assertEqual(third.returncode, 0, decode(third.stderr))
            report3 = read_report(work)

        self.assertEqual(strip_volatile(report1), strip_volatile(report2))
        self.assertEqual(strip_volatile(report1), strip_volatile(report3))


class TestBrokenInput(EndToEndBase):
    def test_broken_data_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "broken")
            os.makedirs(work)
            proc = run_app(self.broken_logs, work)
            self.assertEqual(proc.returncode, 0, decode(proc.stderr))
            report = read_report(work) + "\n" + read_summary(work)
        # ※CR-001: 破損データでは検知 0 件になりホスト別ファイルが作られない。
        # 読み飛ばした行の記録先はサマリである (UI-04-F10 / FR-014)。
        self.assertIn("### 5.1 読み飛ばした行", report)
        for reason in ("列数不一致", "日付書式不正", "数値変換不能", "見出し不明"):
            self.assertIn(reason, report, reason)


class TestReportParser(unittest.TestCase):
    def test_parses_minimal_event(self):
        text = (
            "# イベントID( EVT-20260601-h1-001 )\n"
            "\n"
            "* イベント種別 : ALG-A1 移動平均乖離率, ALG-B3 ローリング最小値の単調増加\n"
            "* 脅威度 : FATAL\n"
            "* データ : jvm_gc / ou (Old 使用量) / container=app01, host=h1\n"
            "* 値     : 1→2→3\n"
            "* 開始   : 2026-06-01 00:00:00\n"
            "* 終了   : 2026-06-02 00:00:00\n"
            "* 現象   : てすと\n"
            "* 説明   : てすと\n"
            "* 原因候補 : メモリリーク (確度: 高) — 理由\n"
            "* 同時に発生したアノマリー :\n"
            "  - EVT-20260601-h2-003 (他ホスト) jvm_gc / eu : WARN / 重なり 5 分\n"
        )
        events = report_parser.parse_events(text)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_id, "EVT-20260601-h1-001")
        self.assertEqual(event.algorithms, ["ALG-A1", "ALG-B3"])
        self.assertEqual(event.severity, "FATAL")
        self.assertEqual(event.source, "jvm_gc")
        self.assertEqual(event.metric, "ou")
        self.assertEqual(event.series_id(), "jvm_gc/app01@h1")
        self.assertEqual(len(event.correlations), 1)
        self.assertEqual(report_parser.missing_items(event), [])

    def test_ignores_unknown_lines(self):
        events = report_parser.parse_events("なんらかの行\n### 見出し\n")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
