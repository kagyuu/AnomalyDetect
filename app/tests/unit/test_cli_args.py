"""U001-T5 の単体テスト: cli の引数解析と終了コードの骨格。"""

import io
import os
import tempfile
import unittest

from s_anomaly import cli, errors


def run_main(argv):
    """`cli.main` を呼ぶ。

    **必ず一時ディレクトリへ移ってから呼ぶ。** `cli.run_pipeline` は
    `os.getcwd()` を出力先にするため、移らないと**リポジトリ直下へ
    レポートと `charts/` を書いてしまう**(実際に起きた)。
    """
    out, err = io.StringIO(), io.StringIO()
    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as work:
        os.chdir(work)
        try:
            code = cli.main(argv, stream_out=out, stream_err=err)
        finally:
            os.chdir(previous)
    return code, out.getvalue(), err.getvalue()


class TestParseArgs(unittest.TestCase):
    def test_existing_dir_returns_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = cli.parse_args([tmp])
            self.assertTrue(os.path.isabs(got))
            self.assertEqual(os.path.realpath(tmp), got)

    def test_relative_path_resolved(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            try:
                os.chdir(tmp)
                got = cli.parse_args(["sub"])
                self.assertTrue(os.path.isabs(got))
                self.assertEqual(got, os.path.realpath(sub))
            finally:
                # Windows では CWD のままだと一時ディレクトリを削除できない
                os.chdir(cwd)

    def test_zero_args(self):
        with self.assertRaises(errors.ArgumentError) as ctx:
            cli.parse_args([])
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("usage:", ctx.exception.user_message())

    def test_two_args(self):
        with self.assertRaises(errors.ArgumentError) as ctx:
            cli.parse_args(["a", "b"])
        self.assertIn("引数は 1 個です", ctx.exception.user_message())

    def test_missing_path(self):
        with self.assertRaises(errors.ArgumentError) as ctx:
            cli.parse_args([os.path.join("does", "not", "exist")])
        self.assertIn("存在しません", ctx.exception.user_message())

    def test_file_instead_of_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("x")
            with self.assertRaises(errors.ArgumentError) as ctx:
                cli.parse_args([path])
            self.assertIn("ディレクトリではありません", ctx.exception.user_message())


class TestMainExitCodes(unittest.TestCase):
    def test_empty_dir_returns_2(self):
        # 対象ログが 0 件のディレクトリは終了コード 2 (P002 UI-05)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main([tmp])
        self.assertEqual(code, 2, err)

    def test_startup_log_contains_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main([tmp])
        self.assertIn("Python", out)
        self.assertIn("DuckDB", out)

    def test_zero_args_returns_1(self):
        code, out, err = run_main([])
        self.assertEqual(code, 1)
        self.assertIn("usage:", err)

    def test_two_args_returns_1(self):
        code, out, err = run_main(["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("引数は 1 個です", err)

    def test_missing_path_returns_1(self):
        code, out, err = run_main([os.path.join("no", "such", "dir")])
        self.assertEqual(code, 1)
        self.assertIn("存在しません", err)

    def test_unexpected_exception_returns_99(self):
        original = cli.bootstrap.load_duckdb

        def boom(*args, **kwargs):
            raise RuntimeError("意図的な失敗")

        cli.bootstrap.load_duckdb = boom
        self.addCleanup(setattr, cli.bootstrap, "load_duckdb", original)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main([tmp])
        self.assertEqual(code, 99)
        self.assertIn("Traceback", err)

    def test_main_does_not_call_sys_exit(self):
        # SystemExit を捕捉しない: main が sys.exit を呼べばここで失敗する
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = run_main([tmp])
        self.assertIsInstance(code, int)


class TestUsage(unittest.TestCase):
    def test_usage_mentions_all_file_patterns(self):
        self.assertIn("DBConnection_", cli.USAGE)
        self.assertIn("_gc_", cli.USAGE)
        self.assertIn("bqueues_", cli.USAGE)
        self.assertIn("report.md", cli.USAGE)
        self.assertIn("settings.properties", cli.USAGE)


if __name__ == "__main__":
    unittest.main()
