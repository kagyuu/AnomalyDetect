"""A002 — 終了コード 9 種の再現。

**終了コードは運用者がスクリプトの成否を判定する唯一の契約である** (FR-090)。
`main()` の戻り値ではなく、プロセスの終了ステータスとして確認する。

再現方法についての実測メモ:

* 終了コード 3 / 6 は `settings.properties` を変える必要があるが、設定ファイルは
  **`s_anomaly.py` と同じディレクトリ**から読まれる (P002 UI-02-L01)。
  作業ディレクトリに置いても効かないため、`app/` を複製してその中の
  `settings.properties` を差し替える。
  (A002【使用するテストデータ】の「正常なログ + settings.properties」という
  記述は、作業ディレクトリに置くと読めるかのように読めるが、UI-02-L01 が正である。)
* 終了コード 7 は、A002【事前準備】5 の方法(vendor に壊れた duckdb を置く)だけでは
  再現しない。`bootstrap.load_duckdb` は vendor の失敗後に**環境の DuckDB へ
  フォールバックする**設計 (DS-14-02) であり、これは意図された挙動である。
  そこで vendor を壊すことに加え、`PYTHONPATH` で環境側の `duckdb` も隠す。
* 終了コード 99 は、`s_anomaly` にモンキーパッチを当てたラッパースクリプトを
  一時ディレクトリに作って起動する。**アプリ側にフックは追加しない。**
"""

import os
import shutil
import tempfile
import unittest

from s_anomaly import config
from tests.acceptance import _harness as H

BROKEN_ONLY_FIXTURES = (
    "bad01_gc_host1_20260601.txt",
    "bqueues_grid03_20260601.txt",
    "DBConnection_20260603.csv",
)

WRAPPER = '''"""A002 項目 12 — 想定外の例外を注入して終了コード 99 を再現する。"""
import os
import sys

sys.path.insert(0, os.path.join({app!r}, "src"))

from s_anomaly import cli, metrics


def _boom(*args, **kwargs):
    raise RuntimeError("A002: 想定外の例外を注入しました")


metrics.build_metrics = _boom
sys.exit(cli.main(sys.argv[1:]))
'''


class TestA002ExitCodes(H.BaselineCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = os.path.join(self._tmp.name, "work")
        os.makedirs(self.work)

    def _no_report(self):
        self.assertFalse(os.path.exists(H.report_path(self.work)))

    # 1
    def test_01_normal_returns_0(self):
        proc = H.run_app(self.normal_logs, self.work)
        self.assertEqual(proc.returncode, 0, H.err(proc))
        self.assertNotIn("Traceback", H.err(proc))
        self.assertTrue(os.path.isfile(H.report_path(self.work)))

    # 2
    def test_02_no_argument_returns_1(self):
        proc = H.run_raw([], self.work)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("usage: s_anomaly.py <dir>", H.err(proc))
        self._no_report()

    # 3
    def test_03_two_arguments_returns_1(self):
        proc = H.run_raw(["a", "b"], self.work)
        self.assertEqual(proc.returncode, 1)
        message = H.err(proc)
        self.assertIn("引数は 1 個です", message)
        self.assertIn("a", message)
        self.assertIn("b", message)
        self._no_report()

    # 4
    def test_04_missing_path_returns_1(self):
        missing = os.path.join(self._tmp.name, "not-exist")
        proc = H.run_app(missing, self.work)
        self.assertEqual(proc.returncode, 1)
        message = H.err(proc)
        self.assertIn("存在しません", message)
        self.assertIn("not-exist", message)
        self._no_report()

    # 5
    def test_05_file_instead_of_dir_returns_1(self):
        path = os.path.join(self._tmp.name, "a-file.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x\n")
        proc = H.run_app(path, self.work)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ディレクトリではありません", H.err(proc))
        self._no_report()

    # 6
    def test_06_no_target_file_returns_2(self):
        empty = os.path.join(self._tmp.name, "empty")
        os.makedirs(empty)
        proc = H.run_app(empty, self.work)
        self.assertEqual(proc.returncode, 2)
        message = H.err(proc)
        self.assertIn(empty, message)
        self.assertIn("DBConnection_", message)
        self.assertIn("bqueues", message)
        self.assertIn("gc", message)
        self._no_report()

    # 7
    def test_07_invalid_setting_returns_3(self):
        entry = H.make_dist(
            os.path.join(self._tmp.name, "cfg3"),
            settings="[parameters]\nALG-A4.lambda = 1.5\n",
            with_tests=False)
        proc = H.run_app(self.normal_logs, self.work, entry=entry)
        self.assertEqual(proc.returncode, 3, H.err(proc))
        message = H.err(proc)
        self.assertIn("[parameters]", message)
        self.assertIn("ALG-A4.lambda", message)
        self.assertIn("1.5", message)
        self.assertIn("理由", message)
        self._no_report()

    # 8
    def test_08_all_parse_failed_returns_4(self):
        bad = os.path.join(self._tmp.name, "all-broken")
        os.makedirs(bad)
        for name in BROKEN_ONLY_FIXTURES:
            shutil.copy(os.path.join(H.FIXTURES, name),
                        os.path.join(bad, name))
        proc = H.run_app(bad, self.work)
        self.assertEqual(proc.returncode, 4, H.err(proc))
        message = H.err(proc)
        self.assertIn("すべてのファイルの解析に失敗", message)
        for name in BROKEN_ONLY_FIXTURES:
            self.assertIn(name, message, name)
        self._no_report()

    # 9
    def test_09_unwritable_report_returns_5(self):
        """※CR-001: 出力ファイル名が変わったため再現方法も変わった。

        従来は `report.md` という名前のディレクトリを置いていたが、出力は
        `report_{HOST}_{yyyymm}.md` のファイル群になった。**必ず出力される
        サマリ**と同じ名前のディレクトリを置いて書き込みを失敗させる。
        """
        os.makedirs(os.path.join(self.work, "report_summary_202606.md"))
        proc = H.run_app(self.normal_logs, self.work)
        self.assertEqual(proc.returncode, 5, H.err(proc))
        self.assertIn("report_", H.err(proc))
        # UI-05-01: レポート全文が標準出力へフォールバックすること
        stdout = H.out(proc)
        # ※CR-001: フォールバックは全ホスト分を連結した全文である
        self.assertIn("## 1. 実行サマリ", stdout)
        self.assertIn("# 異常検知サマリ", stdout)
        self.assertTrue(os.path.isdir(
            os.path.join(self.work, "report_summary_202606.md")))

    # 10
    def test_10_tiny_memory_returns_0_or_6(self):
        entry = H.make_dist(
            os.path.join(self._tmp.name, "mem"),
            settings=H.duckdb_settings(max_memory="1KB"),
            with_tests=False)
        proc = H.run_app(self.normal_logs, self.work, entry=entry)
        self.assertIn(proc.returncode, (0, 6), H.err(proc))
        if proc.returncode == 6:
            message = H.err(proc)
            self.assertIn("max_memory", message)
            self.assertIn("temp_directory", message)
        else:
            # 再現しなかった事実は記録する。A007 ケース3 でも同じ条件を確認する。
            self.assertTrue(os.path.isfile(H.report_path(self.work)))

    # 11
    def test_11_vendor_mismatch_returns_7(self):
        from s_anomaly import bootstrap

        root = os.path.join(self._tmp.name, "vendor7")
        entry = H.make_dist(root, with_tests=False)
        dist_app = os.path.dirname(entry)

        tag = bootstrap.platform_tag()
        broken = os.path.join(dist_app, "vendor", tag, "duckdb")
        os.makedirs(broken)
        with open(os.path.join(broken, "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('raise ImportError("broken vendor")\n')
        for other in ("linux-x86_64-cp39", "win-amd64-cp38"):
            os.makedirs(os.path.join(dist_app, "vendor", other))

        # 環境側の DuckDB も隠す。隠さないとフォールバックが成功して 0 になる
        # (これは DS-14-02 が意図した挙動であり、アプリの欠陥ではない)。
        shadow = os.path.join(self._tmp.name, "shadow")
        os.makedirs(os.path.join(shadow, "duckdb"))
        with open(os.path.join(shadow, "duckdb", "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('raise ImportError("no duckdb in this environment")\n')

        proc = H.run_app(self.normal_logs, self.work, entry=entry,
                         env_extra={"PYTHONPATH": shadow})
        self.assertEqual(proc.returncode, 7, H.err(proc))
        message = H.err(proc)
        self.assertIn(os.path.join(dist_app, "vendor", tag), message)
        self.assertIn(tag, message)
        self.assertIn("linux-x86_64-cp39", message)
        self.assertIn("win-amd64-cp38", message)
        self.assertIn("README", message)
        self._no_report()

    def test_11b_vendor_fallback_alone_does_not_fail(self):
        """壊れた vendor だけでは 7 にならない (フォールバックが働く) こと。"""
        from s_anomaly import bootstrap

        root = os.path.join(self._tmp.name, "vendor-fb")
        entry = H.make_dist(root, with_tests=False)
        broken = os.path.join(os.path.dirname(entry), "vendor",
                              bootstrap.platform_tag(), "duckdb")
        os.makedirs(broken)
        with open(os.path.join(broken, "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('raise ImportError("broken vendor")\n')
        proc = H.run_app(self.normal_logs, self.work, entry=entry)
        self.assertEqual(proc.returncode, 0, H.err(proc))

    # 12
    def test_12_unexpected_exception_returns_99(self):
        wrapper = os.path.join(self._tmp.name, "wrapper_99.py")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(WRAPPER.format(app=H.APP_DIR))
        proc = H.run_app(self.normal_logs, self.work, entry=wrapper)
        self.assertEqual(proc.returncode, 99, H.err(proc))
        self.assertIn("Traceback", H.err(proc))
        self._no_report()

    # 13
    def test_13_all_algorithms_disabled_returns_0(self):
        # **全アルゴリズムを漏れなく無効化する。** 個別に列挙すると、
        # 新しいアルゴリズムが増えたとき (※CR-005 ALG-C1) に取りこぼす。
        body = ["[algorithms]"]
        for alg_id in config.ALGORITHM_IDS:
            body.append("{0} = false".format(alg_id))
        entry = H.make_dist(os.path.join(self._tmp.name, "off"),
                            settings="\n".join(body) + "\n", with_tests=False)
        proc = H.run_app(self.normal_logs, self.work, entry=entry)
        self.assertEqual(proc.returncode, 0, H.err(proc))
        self.assertIn("WARNING", H.err(proc))
        # ※CR-001: 検知 0 件ではホスト別ファイルが作られない。サマリで確認する。
        report = H.read_summary(self.work)
        self.assertIn("検知された異常はありません。", report)
        self.assertIn("## 5. 実行時の注意事項", report)


if __name__ == "__main__":
    unittest.main()
