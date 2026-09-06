"""A010 — 配布物としての可搬性 (NFR-011 / FR-001 / FR-002)。

P000 第2章「実行環境はインターネットに接続されていない環境のため `pip install`
はできません。配布資産一式を持っていけばそのまま動くようにしてください」に
直接対応する、受け入れ観点として最も重要なテストの 1 つである。

★ACCEPTED★ `vendor/` の実体 (DuckDB の wheel) は配布先の Python 版が未確定で
あるため用意していない。本テストは **(a) 環境の DuckDB へのフォールバック経路**
と **(b) `vendor/` 不一致時の終了コード 7 とメッセージ**を確認する。
**`vendor/` からの実際の読み込みは確認できない。P302 の実行前チェックで必ず
確認する。**
"""

import difflib
import os
import shutil
import tempfile
import unittest

from tests.acceptance import _harness as H

FALLBACK_NOTE = "vendor を使わず環境の DuckDB を使用しています"


class TestA010Portability(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA010Portability, cls).setUpClass()
        from s_anomaly import bootstrap

        cls.tag = bootstrap.platform_tag()
        cls._tmp = tempfile.TemporaryDirectory()

        # 基準となるリポジトリ側の実行 (A001 相当)
        cls.base_work = os.path.join(cls._tmp.name, "base")
        os.makedirs(cls.base_work)
        cls.base_proc = H.run_app(cls.normal_logs, cls.base_work)
        cls.base_report = H.read_report(cls.base_work)

        # --- ケース A: コピー先での実行 ---
        cls.dist_root = os.path.join(cls._tmp.name, "outside")
        cls.entry = H.make_dist(cls.dist_root)
        cls.dist_app = os.path.dirname(cls.entry)
        shutil.rmtree(os.path.join(cls.dist_app, "tests", "_work"),
                      ignore_errors=True)
        cls.work_a = os.path.join(cls._tmp.name, "work-a")
        os.makedirs(cls.work_a)
        cls.proc_a = H.run_app(cls.normal_logs, cls.work_a, entry=cls.entry)
        cls.report_a = (H.read_report(cls.work_a)
                        if os.path.isfile(H.report_path(cls.work_a)) else "")

        # --- ケース B: vendor 不一致 ---
        cls.root_b = os.path.join(cls._tmp.name, "vendor-b")
        cls.entry_b = H.make_dist(cls.root_b, with_tests=False)
        app_b = os.path.dirname(cls.entry_b)
        broken = os.path.join(app_b, "vendor", cls.tag, "duckdb")
        os.makedirs(broken)
        with open(os.path.join(broken, "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('raise ImportError("broken vendor")\n')
        cls.other_tags = ("linux-x86_64-cp39", "win-amd64-cp38")
        for other in cls.other_tags:
            os.makedirs(os.path.join(app_b, "vendor", other))
        # 環境の DuckDB も隠さないと、DS-14-02 のフォールバックが成功して
        # 終了コード 0 になる (これは意図された挙動である)。
        cls.shadow = os.path.join(cls._tmp.name, "shadow")
        os.makedirs(os.path.join(cls.shadow, "duckdb"))
        with open(os.path.join(cls.shadow, "duckdb", "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('raise ImportError("no duckdb in this environment")\n')
        cls.vendor_dir_b = os.path.join(app_b, "vendor", cls.tag)
        cls.work_b = os.path.join(cls._tmp.name, "work-b")
        os.makedirs(cls.work_b)
        cls.proc_b = H.run_app(cls.normal_logs, cls.work_b, entry=cls.entry_b,
                               env_extra={"PYTHONPATH": cls.shadow})

        # --- ケース C: tests/ を丸ごと削除 ---
        shutil.rmtree(os.path.join(cls.dist_app, "tests"))
        cls.work_c = os.path.join(cls._tmp.name, "work-c")
        os.makedirs(cls.work_c)
        cls.proc_c = H.run_app(cls.normal_logs, cls.work_c, entry=cls.entry)
        cls.report_c = (H.read_report(cls.work_c)
                        if os.path.isfile(H.report_path(cls.work_c)) else "")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _diff(self, left, right, a="base", b="dist"):
        return "\n".join(list(difflib.unified_diff(
            left.splitlines(), right.splitlines(),
            fromfile=a, tofile=b, lineterm=""))[:50])

    # 1
    def test_01_case_a_exit_zero(self):
        self.assertEqual(self.proc_a.returncode, 0, H.err(self.proc_a))

    # 2
    def test_02_case_a_report_created(self):
        self.assertTrue(os.path.isfile(H.report_path(self.work_a)))

    # 3
    def test_03_case_a_matches_repository_run(self):
        left = H.strip_volatile(self.base_report, drop_target_dir=True)
        right = H.strip_volatile(self.report_a, drop_target_dir=True)
        self.assertEqual(left, right, self._diff(left, right))

    # 4
    def test_04_case_a_records_vendor_fallback(self):
        """DS-14-02: フォールバック経路を通ったことが必ず記録されること。"""
        self.assertIn(FALLBACK_NOTE, H.out(self.proc_a),
                      "起動ログにフォールバックの記録がありません")
        self.assertIn("実行環境の注記", self.report_a,
                      "report.md 1 章にフォールバックの記録がありません")
        self.assertIn(FALLBACK_NOTE, self.report_a)

    # 5
    def test_05_no_pip_install_needed(self):
        """コピーしただけで動いていること (テストは pip を呼ばない)。"""
        self.assertEqual(self.proc_a.returncode, 0)
        self.assertNotIn("pip install", H.err(self.proc_a))
        self.assertFalse(os.path.exists(os.path.join(self.dist_app, "setup.py")))
        self.assertFalse(
            os.path.exists(os.path.join(self.dist_app, "requirements.txt")))

    # 6
    def test_06_case_b_exit_seven(self):
        self.assertEqual(self.proc_b.returncode, 7, H.err(self.proc_b))

    # 7
    def test_07_case_b_message_has_four_elements(self):
        message = H.err(self.proc_b)
        self.assertIn(self.vendor_dir_b, message, "期待した vendor パス")
        self.assertIn(self.tag, message, "実行環境のタグ")
        for other in self.other_tags:
            self.assertIn(other, message, other)
        self.assertIn("README", message, "README への案内")

    # 8
    def test_08_case_b_no_report(self):
        self.assertFalse(os.path.exists(H.report_path(self.work_b)))

    # 9
    def test_09_case_c_runs_without_tests_dir(self):
        self.assertFalse(os.path.exists(os.path.join(self.dist_app, "tests")))
        self.assertEqual(self.proc_c.returncode, 0, H.err(self.proc_c))

    # 10
    def test_10_case_c_matches_case_a(self):
        left = H.strip_volatile(self.report_a, drop_target_dir=True)
        right = H.strip_volatile(self.report_c, drop_target_dir=True)
        self.assertEqual(left, right, self._diff(left, right, "caseA", "caseC"))


if __name__ == "__main__":
    unittest.main()
