"""A011 — 一般ユーザー権限での実行 (NFR-004)。

運用者が日常的に手で実行するツールであり、実行のたびに管理者権限を要求する
設計は受け入れられない。あわせて、**書き込む先がカレントディレクトリと
`temp_directory` に限られている**ことを確認する。

★FIXME★ 【事前準備】4 は入力ディレクトリの読み取り専用化を指示しているが、
Windows のディレクトリに対する読み取り専用属性は配下のファイル作成を防がない。
そのため本テストは指示が併記する代替手段、**実行前後のファイル一覧の比較**で
確認する (Linux では `chmod -w` も併用する)。
"""

import os
import shutil
import sys
import tempfile
import unittest

from tests.acceptance import _harness as H


def is_admin():
    """管理者/root で動いていれば True。判定できなければ None。"""
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return None
    if hasattr(os, "geteuid"):
        return os.geteuid() == 0
    return None


ADMIN = is_admin()
REASON = ("管理者/root で実行しているため本テストの結果は無意味である "
          "(NOT RUN。P302 の未整備事項へ引き継ぐ)")


@unittest.skipIf(ADMIN is True, REASON)
@unittest.skipIf(ADMIN is None, "権限の判定手段が使えないため NOT RUN")
class TestA011UserPrivilege(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA011UserPrivilege, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()

        # 入力は一時ディレクトリへコピーし、可能なら読み取り専用にする
        cls.input_dir = os.path.join(cls._tmp.name, "input")
        shutil.copytree(cls.normal_logs, cls.input_dir)
        cls.readonly_applied = _make_readonly(cls.input_dir)

        cls.work = os.path.join(cls._tmp.name, "work")
        os.makedirs(cls.work)

        cls.input_before = H.snapshot_tree(cls.input_dir)
        cls.work_before = H.snapshot_tree(cls.work)
        cls.app_before = H.snapshot_tree(H.APP_DIR)
        cls.home_before = _shallow_listing(os.path.expanduser("~"))
        cls.repo_before = _shallow_listing(H.REPO_DIR)

        cls.proc = H.run_app(cls.input_dir, cls.work)

        cls.input_after = H.snapshot_tree(cls.input_dir)
        cls.work_after = H.snapshot_tree(cls.work)
        cls.app_after = H.snapshot_tree(H.APP_DIR)
        cls.home_after = _shallow_listing(os.path.expanduser("~"))
        cls.repo_after = _shallow_listing(H.REPO_DIR)

    @classmethod
    def tearDownClass(cls):
        _clear_readonly(cls.input_dir)
        cls._tmp.cleanup()

    # 1
    def test_01_not_running_as_admin(self):
        self.assertFalse(ADMIN, "管理者/root で実行しています")

    # 2
    def test_02_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, H.err(self.proc))

    # 3
    def test_03_no_permission_error(self):
        message = H.err(self.proc)
        for token in ("PermissionError", "Access is denied",
                      "アクセスが拒否されました", "Operation not permitted"):
            self.assertNotIn(token, message, token)

    # 4
    def test_04_input_directory_unchanged(self):
        self.assertEqual(self.input_before, self.input_after,
                         "入力ディレクトリが書き換えられています")

    # 5
    def test_05_source_tree_unchanged(self):
        added = sorted(set(self.app_after) - set(self.app_before))
        removed = sorted(set(self.app_before) - set(self.app_after))
        changed = sorted(k for k in self.app_before
                         if k in self.app_after
                         and self.app_before[k] != self.app_after[k])
        self.assertEqual((added, removed, changed), ([], [], []))
        for key in self.app_before:
            if key.startswith("tests/_work/"):
                self.assertEqual(self.app_before[key], self.app_after[key], key)

    # 6
    def test_06_work_directory_gets_report(self):
        """作業ディレクトリに出るのは、想定した生成物だけであること。

        ※CR-009 で `charts/` 配下の SVG が加わった。**作業ディレクトリの外へ
        書かないこと**(test_07)が本試験の主眼であり、ここは生成物の内訳を見る。
        """
        added = sorted(set(self.work_after) - set(self.work_before))
        self.assertTrue([n for n in added if n.startswith("report_")], added)
        allowed = ("report_", "tmp/", "charts/", "charts")
        for name in added:
            self.assertTrue(name.startswith(allowed),
                            "想定外の生成物: {0}".format(name))

    # 7a
    def test_07a_test_suites_do_not_pollute_the_repository(self):
        """**テストスイート自体がリポジトリを汚さないこと。**

        `cli.run_pipeline` は `os.getcwd()` を出力先にする。
        **同一プロセスで `cli.main()` を呼ぶテストが作業ディレクトリを移さないと、
        リポジトリ直下へレポートと `charts/` を書いてしまう**(2026-09-06 に発生)。

        既存の生成物が残っていないことを、リポジトリ直下の一覧で確かめる。
        """
        strays = [n for n in os.listdir(H.REPO_DIR)
                  if n.startswith("report_") or n == "charts"]
        self.assertEqual(strays, [],
                         "リポジトリ直下に生成物が残っている: {0}".format(strays))

    # 7
    def test_07_nothing_written_outside(self):
        self.assertEqual(self.home_before, self.home_after,
                         "ユーザーのホーム直下に変化があります")
        self.assertEqual(self.repo_before, self.repo_after,
                         "リポジトリ直下に変化があります")

    # 8
    def test_08_no_elevation_prompt(self):
        """昇格待ちでブロックせずに終了していること。"""
        self.assertIsNotNone(self.proc.returncode)
        self.assertEqual(self.proc.returncode, 0)

    def test_09_readonly_method_recorded(self):
        """読み取り専用化が成立したかどうかを可視化する (★FIXME★ の確認)。"""
        self.assertIn(self.readonly_applied, ("chmod", "attrib", "none"))


def _shallow_listing(path):
    try:
        return sorted(os.listdir(str(path)))
    except OSError:
        return []


def _make_readonly(path):
    if os.name == "nt":
        try:
            for dirpath, _dirs, files in os.walk(str(path)):
                for name in files:
                    target = os.path.join(dirpath, name)
                    os.chmod(target, 0o444)
            return "attrib"
        except OSError:
            return "none"
    try:
        for dirpath, _dirs, files in os.walk(str(path)):
            for name in files:
                os.chmod(os.path.join(dirpath, name), 0o444)
        return "chmod"
    except OSError:
        return "none"


def _clear_readonly(path):
    for dirpath, _dirs, files in os.walk(str(path)):
        for name in files:
            try:
                os.chmod(os.path.join(dirpath, name), 0o666)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
