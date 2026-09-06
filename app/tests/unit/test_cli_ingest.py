"""U002-T5 の単体テスト: cli の取り込み結線と終了コード 2 / 4。"""

import io
import os
import shutil
import tempfile
import unittest

from s_anomaly import cli

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures"
)


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


def copy_fixtures(dest, names):
    os.makedirs(dest, exist_ok=True)
    for name in names:
        shutil.copy(os.path.join(FIXTURES, name), os.path.join(dest, name))


class TestIngestWiring(unittest.TestCase):
    def test_three_kinds_load_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            copy_fixtures(logs, [
                "DBConnection_20260601.csv",
                "app01_gc_host1_20260601.txt",
                "bqueues_grid01_20260601.txt",
            ])
            code, out, err = run_main([logs])
        self.assertEqual(code, 0, err)
        self.assertIn("[S3]", out)
        self.assertIn("[S4]", out)
        self.assertIn("[1/3]", out)
        self.assertIn("総レコード数", out)

    def test_empty_dir_returns_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            os.makedirs(logs)
            code, out, err = run_main([logs])
        self.assertEqual(code, 2)
        self.assertIn("探索したパス", err)
        self.assertIn("DBConnection_", err)
        self.assertIn("bqueues_", err)

    def test_dir_with_only_irrelevant_files_returns_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            os.makedirs(logs)
            with open(os.path.join(logs, "readme.txt"), "w", encoding="utf-8") as h:
                h.write("x")
            code, out, err = run_main([logs])
        self.assertEqual(code, 2)

    def test_all_broken_returns_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            copy_fixtures(logs, [
                "bad01_gc_host1_20260601.txt",
                "bqueues_grid03_20260601.txt",
                "DBConnection_20260603.csv",
            ])
            code, out, err = run_main([logs])
        self.assertEqual(code, 4, err)
        self.assertIn("すべてのファイルの解析に失敗", err)

    def test_partial_failure_returns_0(self):
        """1 ファイルだけ壊れていて他が読める場合は 0 (FR-091)。"""
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            copy_fixtures(logs, [
                "DBConnection_20260601.csv",
                "bad01_gc_host1_20260601.txt",
            ])
            code, out, err = run_main([logs])
        self.assertEqual(code, 0, err)
        self.assertIn("判別できません", err)

    def test_duplicate_files_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            copy_fixtures(os.path.join(logs, "a"), ["DBConnection_20260601.csv"])
            copy_fixtures(os.path.join(logs, "b"), ["DBConnection_20260601.csv"])
            code, out, err = run_main([logs])
        self.assertEqual(code, 0, err)
        self.assertIn("重複レコードを", out)


if __name__ == "__main__":
    unittest.main()
