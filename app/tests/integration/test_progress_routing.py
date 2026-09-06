"""T008 — 進捗ログの出力先振り分けと書式 (F-19)。"""

import io
import os
import re
import tempfile
import unittest

from s_anomaly import cli
from tests.integration import _setup_baseline

LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(INFO|WARNING|ERROR)\] \[\S+\] .+$"
)


def run(logs_dir, cwd):
    out, err = io.StringIO(), io.StringIO()
    previous = os.getcwd()
    os.chdir(cwd)
    try:
        code = cli.main([str(logs_dir)], stream_out=out, stream_err=err)
    finally:
        os.chdir(previous)
    return code, out.getvalue(), err.getvalue()


def lines(text):
    return [ln for ln in text.splitlines() if ln.strip()]


class TestProgressRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()
        cls._tmp = tempfile.TemporaryDirectory()
        work = os.path.join(cls._tmp.name, "normal")
        os.makedirs(work)
        cls.code, cls.out, cls.err = run(
            os.path.join(cls.paths["normal"], "logs"), work)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_01_line_format(self):
        for line in lines(self.out) + lines(self.err):
            self.assertRegex(line, LINE_RE)

    def test_02_stdout_is_info_only(self):
        self.assertNotIn("[WARNING]", self.out)
        self.assertNotIn("[ERROR]", self.out)
        self.assertTrue(lines(self.out))

    def test_03_stderr_has_no_info(self):
        self.assertNotIn("[INFO]", self.err)

    def test_04_default_step_marker(self):
        self.assertIn("[--]", self.out)

    def test_05_startup_message(self):
        self.assertIn("Python", self.out)
        self.assertIn("DuckDB", self.out)

    def test_06_all_steps_present(self):
        for i in range(1, 10):
            self.assertIn("[S{0}]".format(i), self.out, "S{0}".format(i))

    def test_07_s3_counts(self):
        self.assertIn("探索したファイル総数", self.out)
        self.assertIn("対象外", self.out)

    def test_08_s4_per_file_progress(self):
        matches = re.findall(r"\[S4\] \[\d+/\d+\]", self.out)
        self.assertGreaterEqual(len(matches), 56)

    def test_09_s5_summary(self):
        self.assertIn("対象期間", self.out)
        self.assertIn("系列", self.out)

    def test_10_s6_per_algorithm(self):
        """※CR-007 開始行は 1 本にまとめ、完了行はアルゴリズムごとに出す。"""
        starts = re.findall(r"\[S6\] \d+ アルゴリズムを \d+ 系列に適用します \(並列度 \d+\)",
                            self.out)
        ends = re.findall(r"\[S6\] ALG-[ABC]\d 完了:", self.out)
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 11)          # ※CR-005 ALG-C1 を含む
        self.assertIn("検知の総所要", self.out)   # 壁時計

    def test_11_s6_completion_fields(self):
        for line in lines(self.out):
            if "完了:" in line:
                self.assertIn("検知", line)
                self.assertIn("スキップ", line)
                self.assertIn("所要", line)

    def test_12_s7_breakdown(self):
        self.assertIn("SEVERE:", self.out)
        self.assertIn("FATAL:", self.out)

    def test_13_s9_output_path(self):
        self.assertIn("レポートを出力しました", self.out)
        self.assertIn("バイト", self.out)

    def test_14_completion_message(self):
        self.assertIn("総所要時間", self.out)
        self.assertIn("終了コード", self.out)

    def test_15_16_17_broken_data_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run(os.path.join(self.paths["broken"], "logs"), tmp)
        self.assertEqual(code, 0)
        self.assertIn("[WARNING]", err)
        self.assertNotIn("[WARNING]", out)

    def test_18_no_duplicate_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "a")
            os.makedirs(work)
            _c1, out1, _e1 = run(os.path.join(self.paths["broken"], "logs"), work)
            _c2, out2, _e2 = run(os.path.join(self.paths["broken"], "logs"), work)
        self.assertLess(len(lines(out2)), len(lines(out1)) * 1.5)


if __name__ == "__main__":
    unittest.main()
