"""U001-T2 の単体テスト: progress。"""

import io
import re
import unittest

from s_anomaly import progress as progress_mod

LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(INFO|WARNING|ERROR)\] \[\S+\] .+$"
)


def lines(stream):
    return [ln for ln in stream.getvalue().splitlines() if ln.strip()]


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.p = progress_mod.setup(self.out, self.err)

    def test_info_goes_to_stdout_only(self):
        self.p.info("S3", "情報です")
        self.assertEqual(len(lines(self.out)), 1)
        self.assertEqual(lines(self.err), [])
        self.assertIn("情報です", self.out.getvalue())

    def test_warning_goes_to_stderr_only(self):
        self.p.warning("S4", "警告です")
        self.assertEqual(lines(self.out), [])
        self.assertEqual(len(lines(self.err)), 1)
        self.assertIn("[WARNING]", self.err.getvalue())

    def test_error_goes_to_stderr_only(self):
        self.p.error("--", "異常です")
        self.assertEqual(lines(self.out), [])
        self.assertIn("[ERROR]", self.err.getvalue())

    def test_no_warning_in_stdout(self):
        self.p.info("S1", "a")
        self.p.warning("S1", "b")
        self.p.error("S1", "c")
        self.assertNotIn("[WARNING]", self.out.getvalue())
        self.assertNotIn("[ERROR]", self.out.getvalue())
        self.assertNotIn("[INFO]", self.err.getvalue())


class TestFormat(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.p = progress_mod.setup(self.out, self.err)

    def test_line_format(self):
        self.p.info("S6", "書式の確認")
        for line in lines(self.out):
            self.assertRegex(line, LINE_RE)

    def test_step_id_appears(self):
        self.p.info("S6", "x")
        self.assertIn("[S6]", self.out.getvalue())

    def test_progress_count(self):
        self.p.progress_count("S4", 3, 10, "処理中")
        self.assertIn("[3/10] 処理中", self.out.getvalue())

    def test_default_step_when_dashes(self):
        self.p.info("--", "起動")
        self.assertIn("[--]", self.out.getvalue())


class TestNoDuplicateHandlers(unittest.TestCase):
    def test_setup_twice_does_not_duplicate_output(self):
        out1, err1 = io.StringIO(), io.StringIO()
        p1 = progress_mod.setup(out1, err1)
        p1.info("S1", "一回目")
        first = len(lines(out1))

        out2, err2 = io.StringIO(), io.StringIO()
        p2 = progress_mod.setup(out2, err2)
        p2.info("S1", "二回目")
        second = len(lines(out2))

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        # 1 回目のストリームに 2 回目の出力が漏れていないこと
        self.assertNotIn("二回目", out1.getvalue())


if __name__ == "__main__":
    unittest.main()
