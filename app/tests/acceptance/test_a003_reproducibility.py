"""A003 — 再現性 (NFR-009)。

統計処理を行うアプリで再現性が崩れると、**運用者が「前回と結果が違う」ことを
異常の兆候と誤認する**。乱数・辞書の反復順・ソートキー不足を検出する。

比較から除外してよいのは「実行日時」の行と、標準出力の時刻・所要時間だけである。
加えて、標準出力の S9 行に出る**作業ディレクトリの絶対パス**は `{WORK}` に
正規化する。A003【事前準備】2 が「1 回目用と 2 回目用に別々の一時ディレクトリ」を
作ることを指示している以上、このパスは必ず異なるためであり、
**アプリの非決定性ではない**(`report.md` 側は除外なしで一致している)。
"""

import difflib
import os
import tempfile
import unittest

from tests.acceptance import _harness as H


class TestA003Reproducibility(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA003Reproducibility, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.procs = []
        cls.reports = []
        cls.stdouts = []
        for i in range(3):
            work = os.path.join(cls._tmp.name, "run{0}".format(i + 1))
            os.makedirs(work)
            proc = H.run_app(cls.normal_logs, work)
            cls.procs.append(proc)
            cls.reports.append(H.read_report(work))
            cls.stdouts.append(H.strip_stdout_volatile(H.out(proc), work))
        cls.events = [H.parse_events(text) for text in cls.reports]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _diff(self, left, right):
        return "\n".join(list(difflib.unified_diff(
            left.splitlines(), right.splitlines(),
            fromfile="run1", tofile="run2", lineterm=""))[:50])

    # 1
    def test_01_all_exit_codes_are_zero(self):
        for i, proc in enumerate(self.procs):
            self.assertEqual(proc.returncode, 0,
                             "run{0}: {1}".format(i + 1, H.err(proc)))

    # 2
    def test_02_reports_identical(self):
        first = H.strip_volatile(self.reports[0])
        second = H.strip_volatile(self.reports[1])
        self.assertEqual(first, second, self._diff(first, second))

    # 3
    def test_03_event_ids_identical(self):
        left = [(e.event_id, e.series_id(), e.metric) for e in self.events[0]]
        right = [(e.event_id, e.series_id(), e.metric) for e in self.events[1]]
        self.assertEqual(left, right)

    # 4
    def test_04_event_order_identical(self):
        self.assertEqual([e.event_id for e in self.events[0]],
                         [e.event_id for e in self.events[1]])

    # 5
    def test_05_severity_counts_identical(self):
        self.assertEqual(H.severity_counts(self.events[0]),
                         H.severity_counts(self.events[1]))

    # 6
    def test_06_algorithm_section_identical(self):
        self.assertEqual(H.section(self.reports[0], "## 2. 適用したアルゴリズム"),
                         H.section(self.reports[1], "## 2. 適用したアルゴリズム"))

    # 7
    def test_07_notes_section_identical(self):
        for heading in ("### 4.1 読み飛ばした行",
                        "### 4.2 点数不足でスキップした系列",
                        "### 4.3 失敗したアルゴリズム"):
            self.assertEqual(H.section(self.reports[0], heading),
                             H.section(self.reports[1], heading), heading)

    # 8
    def test_08_stdout_identical(self):
        first, second = self.stdouts[0], self.stdouts[1]
        self.assertEqual(first, second, self._diff(first, second))

    # 9
    def test_09_third_run_identical(self):
        first = H.strip_volatile(self.reports[0])
        third = H.strip_volatile(self.reports[2])
        self.assertEqual(first, third, self._diff(first, third))
        self.assertEqual([e.event_id for e in self.events[0]],
                         [e.event_id for e in self.events[2]])
        self.assertEqual(self.stdouts[0], self.stdouts[2])

    def test_09b_byte_identical(self):
        """項目 2 の「バイト単位」を、除外行を落としたうえで確認する。"""
        left = H.strip_volatile(self.reports[0]).encode("utf-8")
        right = H.strip_volatile(self.reports[1]).encode("utf-8")
        self.assertEqual(left, right)


class TestA003ThreadCountDoesNotChangeOutput(H.BaselineCase):
    """※CR-007 — **スレッド数を変えてもレポートが変わらないこと**(NFR-009)。

    S6 を系列ごとのスレッドで並列実行するようにしたため、**実行順が毎回変わる。**
    A003 本体は既定のスレッド数でしか確かめないので、逐次(1)と並列(8)を
    突き合わせる試験を別に置く。
    """

    @classmethod
    def setUpClass(cls):
        super(TestA003ThreadCountDoesNotChangeOutput, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.reports = {}
        cls.procs = {}
        for threads in (1, 8):
            entry = H.make_dist(
                os.path.join(cls._tmp.name, "dist{0}".format(threads)),
                settings="[parameters]\ncommon.detect_threads = {0}\n".format(threads),
                with_tests=False)
            work = os.path.join(cls._tmp.name, "work{0}".format(threads))
            os.makedirs(work)
            cls.procs[threads] = H.run_app(cls.normal_logs, work, entry=entry)
            cls.reports[threads] = H.read_all_reports(work)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_01_both_succeed(self):
        for threads, proc in self.procs.items():
            self.assertEqual(proc.returncode, 0,
                             "threads={0}: {1}".format(threads, H.err(proc)))

    def test_02_reports_identical(self):
        left = H.strip_volatile(self.reports[1])
        right = H.strip_volatile(self.reports[8])
        self.assertTrue(left, "レポートが空である")
        self.assertEqual(left, right, "\n".join(list(difflib.unified_diff(
            left.splitlines(), right.splitlines(),
            fromfile="threads=1", tofile="threads=8", lineterm=""))[:50]))

    def test_03_parallelism_is_reported(self):
        """並列度が進捗ログに出ること(DS-12-09)。"""
        self.assertIn("並列度 1)", H.out(self.procs[1]))
        self.assertIn("並列度 8)", H.out(self.procs[8]))


if __name__ == "__main__":
    unittest.main()
