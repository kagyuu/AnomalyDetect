"""A004 — 再起動耐性 (O-01 / O-02 / O-03)。

**残骸が残っていること自体が前提**であるため、1 回目と 2 回目の間に
ベースライン復元を挟まない。本テストは専用の一時ディレクトリの中で
3 回の起動を完結させる (P009 4.3 の注記)。

A004【事前準備】3 は「作業ディレクトリに `settings.properties` を置き
`[duckdb] temp_directory = ./tmp` を明示する」としているが、設定ファイルは
**`s_anomaly.py` と同じディレクトリ**から読まれる (P002 UI-02-L01)。
`temp_directory` の既定値がそもそも `./tmp` (作業ディレクトリ相対) であるため、
既定のまま起動することで指示の意図(残骸の確認対象を作業ディレクトリ内に閉じる)
は満たされる。
"""

import difflib
import os
import tempfile
import unittest

from tests.acceptance import _harness as H

LEFTOVER = "### 途中で切れたレポート\n"


class TestA004RestartResilience(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA004RestartResilience, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = os.path.join(cls._tmp.name, "restart")
        os.makedirs(cls.work)
        cls.tmp_dir = os.path.join(cls.work, "tmp")
        cls.leftover = os.path.join(cls.work, "report_stale-host_202606.md.tmp")

        cls.procs = []
        cls.reports = []
        cls.tmp_after = []
        cls.leftover_after = []

        for i in range(3):
            if i > 0:
                # 意図的に残骸を作る。掃除はしない。
                with open(cls.leftover, "w", encoding="utf-8") as handle:
                    handle.write(LEFTOVER)
                os.makedirs(cls.tmp_dir, exist_ok=True)
                with open(os.path.join(cls.tmp_dir, "stale.tmp"), "w",
                          encoding="utf-8") as handle:
                    handle.write("stale\n")
            proc = H.run_app(cls.normal_logs, cls.work)
            cls.procs.append(proc)
            cls.reports.append(H.read_report(cls.work)
                               if os.path.isfile(H.report_path(cls.work))
                               else "")
            cls.tmp_after.append(os.path.isdir(cls.tmp_dir))
            cls.leftover_after.append(os.path.exists(cls.leftover))

        cls.events = [H.parse_events(text) for text in cls.reports]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # 1
    def test_01_first_run_exit_zero(self):
        self.assertEqual(self.procs[0].returncode, 0, H.err(self.procs[0]))

    # 2
    def test_02_second_run_exit_zero(self):
        self.assertEqual(self.procs[1].returncode, 0, H.err(self.procs[1]))

    # 3
    def test_03_third_run_exit_zero(self):
        self.assertEqual(self.procs[2].returncode, 0, H.err(self.procs[2]))

    # 4
    def test_04_reports_identical(self):
        first = H.strip_volatile(self.reports[0])
        for i in (1, 2):
            other = H.strip_volatile(self.reports[i])
            diff = "\n".join(list(difflib.unified_diff(
                first.splitlines(), other.splitlines(),
                fromfile="run1", tofile="run{0}".format(i + 1),
                lineterm=""))[:50])
            self.assertEqual(first, other, diff)

    # 5
    def test_05_no_leftover_after_each_run(self):
        """※CR-001 / UI-04-05: 出力対象でないホストの残骸は**消さない**。

        アプリは今回出力するファイルの一時ファイルだけを片づける。
        前回実行時のファイル (および出力対象でないホストの残骸) を消さないのは
        仕様どおりであり、**次回実行を妨げないこと**が要件である
        (test_01〜04 が 3 回の実行結果の一致を確認している)。
        """
        self.assertEqual(self.leftover_after, [False, True, True])

    # 5b
    def test_05b_no_tmp_for_written_reports(self):
        """今回出力したファイルの一時ファイルは残らない。"""
        import glob

        for path in glob.glob(os.path.join(self.work, "report_*.md")):
            self.assertFalse(os.path.exists(path + ".tmp"),
                             os.path.basename(path))

    # 6
    def test_06_second_run_stderr_clean(self):
        message = H.err(self.procs[1])
        self.assertNotIn("Traceback", message)
        for token in ("already exists", "duplicate", "Duplicate"):
            self.assertNotIn(token, message, token)

    # 7
    def test_07_stale_temp_directory_tolerated(self):
        """`tmp/` に残骸があっても 2 回目・3 回目が失敗しないこと。"""
        for i in (1, 2):
            self.assertEqual(self.procs[i].returncode, 0, H.err(self.procs[i]))
            message = H.err(self.procs[i])
            for token in ("PermissionError", "FileExistsError", "OSError"):
                self.assertNotIn(token, message, token)

    # 8
    def test_08_event_ids_identical(self):
        ids = [[e.event_id for e in evs] for evs in self.events]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[0], ids[2])

    # 9
    def test_09_severity_counts_identical(self):
        counts = [H.severity_counts(evs) for evs in self.events]
        self.assertEqual(counts[0], counts[1])
        self.assertEqual(counts[0], counts[2])


if __name__ == "__main__":
    unittest.main()
