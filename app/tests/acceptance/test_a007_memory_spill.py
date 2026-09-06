"""A007 — メモリ制限下での退避 (NFR-003)。

P000 第2章の明示要求「DuckDB インメモリ(+メモリからあふれたら temp ファイルを
使う)」に対応する。**メモリを超えたら落ちるのではなく、退避して完走する**ことが
要求である。

A007【事前準備】3 は「各作業ディレクトリに `settings.properties` を置く」と
しているが、設定ファイルは **`s_anomaly.py` と同じディレクトリ**から読まれる
(P002 UI-02-L01)。したがってケースごとに `app/` を複製し、複製側の
`settings.properties` を差し替える。
"""

import difflib
import os
import tempfile
import unittest

from tests.acceptance import _harness as H

CASES = (
    ("case1", "4GB", "./tmp"),
    ("case2", "256MB", "./tmp"),
    ("case3", "1KB", "./tmp"),
)


class TestA007MemorySpill(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA007MemorySpill, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.result = {}
        for name, max_memory, temp_directory in CASES:
            work = os.path.join(cls._tmp.name, name, "work")
            os.makedirs(work)
            entry = H.make_dist(
                os.path.join(cls._tmp.name, name, "dist"),
                settings=H.duckdb_settings(max_memory, temp_directory),
                with_tests=False)
            proc = H.run_app(cls.normal_logs, work, entry=entry)
            report = (H.read_report(work)
                      if os.path.isfile(H.report_path(work)) else "")
            tmp_dir = os.path.join(work, "tmp")
            cls.result[name] = {
                "proc": proc,
                "work": work,
                "report": report,
                "tmp_exists": os.path.isdir(tmp_dir),
                "tmp_entries": (sorted(os.listdir(tmp_dir))
                                if os.path.isdir(tmp_dir) else []),
            }

        # 項目 9: 存在しない temp_directory を指定しても自動的に作られること
        work = os.path.join(cls._tmp.name, "case9", "work")
        os.makedirs(work)
        entry = H.make_dist(
            os.path.join(cls._tmp.name, "case9", "dist"),
            settings=H.duckdb_settings("4GB", "./deep/nested/spill"),
            with_tests=False)
        cls.case9 = H.run_app(cls.normal_logs, work, entry=entry)
        cls.case9_dir = os.path.join(work, "deep", "nested", "spill")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # 1
    def test_01_case1_exit_zero(self):
        proc = self.result["case1"]["proc"]
        self.assertEqual(proc.returncode, 0, H.err(proc))

    # 2
    def test_02_case2_exit_zero(self):
        proc = self.result["case2"]["proc"]
        self.assertEqual(proc.returncode, 0, H.err(proc))

    # 3
    def test_03_case2_report_structure(self):
        report = self.result["case2"]["report"]
        # ※CR-001・CR-004
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項"):
            self.assertIn(heading, report, heading)

    # 4
    def test_04_case1_and_case2_identical(self):
        left = H.strip_volatile(self.result["case1"]["report"])
        right = H.strip_volatile(self.result["case2"]["report"])
        diff = "\n".join(list(difflib.unified_diff(
            left.splitlines(), right.splitlines(),
            fromfile="case1", tofile="case2", lineterm=""))[:50])
        self.assertEqual(left, right, diff)

    # 5
    def test_05_case2_temp_directory_created(self):
        self.assertTrue(self.result["case2"]["tmp_exists"],
                        "退避先 ./tmp が作られていません")

    # 6
    def test_06_case2_no_memory_exception(self):
        message = H.err(self.result["case2"]["proc"])
        for token in ("MemoryError", "OutOfMemoryException", "Traceback"):
            self.assertNotIn(token, message, token)

    # 7
    def test_07_case3_exit_zero_or_six(self):
        proc = self.result["case3"]["proc"]
        self.assertIn(proc.returncode, (0, 6),
                      "終了コード {0}: {1}".format(proc.returncode,
                                                   H.err(proc)[-3000:]))
        self.assertNotEqual(proc.returncode, 99,
                            "想定外の例外になっています (DS-01-04 の捕捉漏れ)")

    # 8
    def test_08_case3_message_when_six(self):
        proc = self.result["case3"]["proc"]
        if proc.returncode != 6:
            self.skipTest(
                "max_memory=1KB では終了コード 6 が再現しなかった "
                "(実際の終了コード {0})。DuckDB が下限へ丸めたためであり、"
                "メッセージの確認は A002 項目 10 と単体テストで代替する".format(
                    proc.returncode))
        message = H.err(proc)
        self.assertIn("max_memory", message)
        self.assertIn("1KB", message)
        self.assertIn("temp_directory", message)
        self.assertIn("対処", message)

    # 9
    def test_09_missing_temp_directory_is_created(self):
        self.assertEqual(self.case9.returncode, 0, H.err(self.case9))
        self.assertTrue(os.path.isdir(self.case9_dir),
                        "存在しない temp_directory が作られていません")


if __name__ == "__main__":
    unittest.main()
