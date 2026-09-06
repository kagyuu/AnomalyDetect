"""U008-T3 の単体テスト: 終了コード 9 種の網羅 (P006 F-18)。"""

import io
import glob
import os
import shutil
import sys
import tempfile
import unittest

from s_anomaly import cli, config, reporter

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures"
)


def run_main(argv, cwd=None):
    out, err = io.StringIO(), io.StringIO()
    previous = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        code = cli.main(argv, stream_out=out, stream_err=err)
    finally:
        os.chdir(previous)
    return code, out.getvalue(), err.getvalue()


def copy_fixtures(dest, names):
    os.makedirs(dest, exist_ok=True)
    for name in names:
        shutil.copy(os.path.join(FIXTURES, name), os.path.join(dest, name))


GOOD_LOGS = [
    "DBConnection_20260601.csv",
    "app01_gc_host1_20260601.txt",
    "bqueues_grid01_20260601.txt",
]


class ExitCodeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = os.path.join(self._tmp.name, "work")
        os.makedirs(self.work)
        self.logs = os.path.join(self._tmp.name, "logs")

    def with_good_logs(self):
        copy_fixtures(self.logs, GOOD_LOGS)
        return self.logs


class TestExitCode0(ExitCodeCase):
    def _reports(self):
        """※CR-001: 出力されたレポート群のパス一覧。"""
        return sorted(glob.glob(os.path.join(self.work, "report_*.md")))

    def test_normal_run_writes_reports(self):
        code, out, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 0, err)
        paths = self._reports()
        self.assertTrue(paths, "レポートが 1 つも出力されていない")
        summaries = [p for p in paths
                     if os.path.basename(p).startswith("report_summary_")]
        self.assertEqual(len(summaries), 1)
        for path in paths:
            self.assertFalse(os.path.exists(path + ".tmp"))
        with open(summaries[0], "r", encoding="utf-8") as h:
            text = h.read()
        for heading in ("# 異常検知サマリ", "## 1. 実行サマリ",
                        "## 2. ホスト別の集計", "## 3. 日別のイベント数",
                        "## 4. 複数ホストで同時に発生したアノマリー"):
            self.assertIn(heading, text)

    def test_no_host_report_when_no_events(self):
        """検知 0 件ならホスト別ファイルは作らない (UI-04-F02 / UI-04-03)。

        本ケースのログは 3 ファイル・数行しかなく、common.min_points に
        満たないため検知は 0 件になる。サマリだけが出るのが正しい。
        """
        run_main([self.with_good_logs()], cwd=self.work)
        hosts = [p for p in self._reports()
                 if not os.path.basename(p).startswith("report_summary_")]
        self.assertEqual(hosts, [])

    def test_report_is_lf_utf8(self):
        run_main([self.with_good_logs()], cwd=self.work)
        for path in self._reports():
            with open(path, "rb") as h:
                raw = h.read()
            self.assertNotIn(b"\r\n", raw)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))

    def test_all_algorithms_disabled_still_zero(self):
        """全アルゴリズム false でも 0 件レポートを出して 0 (FR-084)。"""
        original = config.load

        def patched(path, progress):
            cfg = original(path, progress)
            values = dict(cfg.as_dict())
            for alg in config.ALGORITHM_IDS:
                values[("algorithms", alg)] = False
            return config.Config(values)

        cli.config.load = patched
        self.addCleanup(setattr, cli.config, "load", original)
        code, out, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 0, err)
        summaries = glob.glob(os.path.join(self.work, "report_summary_*.md"))
        self.assertEqual(len(summaries), 1)
        with open(summaries[0], "r", encoding="utf-8") as h:
            self.assertIn("検知された異常はありません。", h.read())
        self.assertIn("有効なアルゴリズムがありません", err)


class TestExitCode1(ExitCodeCase):
    def test_no_args(self):
        code, _, err = run_main([])
        self.assertEqual(code, 1)
        self.assertIn("usage:", err)

    def test_two_args(self):
        code, _, err = run_main(["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("引数は 1 個です", err)

    def test_missing_path(self):
        code, _, err = run_main([os.path.join("no", "such")])
        self.assertEqual(code, 1)
        self.assertIn("存在しません", err)

    def test_file_not_dir(self):
        path = os.path.join(self._tmp.name, "f.txt")
        with open(path, "w", encoding="utf-8") as h:
            h.write("x")
        code, _, err = run_main([path])
        self.assertEqual(code, 1)
        self.assertIn("ディレクトリではありません", err)


class TestExitCode2(ExitCodeCase):
    def test_empty_dir(self):
        os.makedirs(self.logs)
        code, _, err = run_main([self.logs], cwd=self.work)
        self.assertEqual(code, 2)
        self.assertIn("探索したパス", err)
        self.assertIn("DBConnection_", err)
        self.assertFalse(glob.glob(os.path.join(self.work, "report_*.md")))


class TestExitCode3(ExitCodeCase):
    def test_invalid_settings(self):
        original = config.load

        def patched(path, progress):
            from s_anomaly.errors import ConfigError

            raise ConfigError(
                "settings.properties の設定値が不正です",
                "  [parameters] ALG-A4.lambda = 1.5\n  理由: 0 より大きく 1 以下",
            )

        cli.config.load = patched
        self.addCleanup(setattr, cli.config, "load", original)
        code, _, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 3)
        self.assertIn("ALG-A4.lambda", err)


class TestExitCode4(ExitCodeCase):
    def test_all_files_broken(self):
        copy_fixtures(self.logs, [
            "bad01_gc_host1_20260601.txt",
            "bqueues_grid03_20260601.txt",
            "DBConnection_20260603.csv",
        ])
        code, _, err = run_main([self.logs], cwd=self.work)
        self.assertEqual(code, 4)
        self.assertIn("すべてのファイルの解析に失敗", err)
        self.assertFalse(glob.glob(os.path.join(self.work, "report_*.md")))


class TestExitCode5(ExitCodeCase):
    def test_write_failure_falls_back_to_stdout(self):
        original = reporter.write_report

        def boom(text, path):
            from s_anomaly.errors import ReportWriteError

            raise ReportWriteError("report.md を書き込めませんでした",
                                   "  理由: テスト")

        cli.reporter.write_report = boom
        self.addCleanup(setattr, cli.reporter, "write_report", original)
        code, out, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 5)
        # レポート全文が標準出力へフォールバックされること (UI-05-01)
        # ※CR-001: フォールバックは全ホスト分を連結した全文である
        self.assertIn("# 異常検知サマリ", out)
        self.assertIn("## 1. 実行サマリ", out)


class TestExitCode6(ExitCodeCase):
    def test_duckdb_error_becomes_storage_error(self):
        import duckdb

        original = cli.metrics.build_metrics

        def boom(con):
            raise duckdb.Error("simulated storage failure")

        cli.metrics.build_metrics = boom
        self.addCleanup(setattr, cli.metrics, "build_metrics", original)
        code, _, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 6, err)
        self.assertIn("max_memory", err)
        self.assertIn("temp_directory", err)


class TestExitCode7(ExitCodeCase):
    def test_vendor_error(self):
        original = cli.bootstrap.load_duckdb

        def boom(app_root, progress):
            from s_anomaly.errors import VendorError
            from s_anomaly import bootstrap as bs

            raise VendorError("DuckDB を読み込めませんでした",
                              bs._vendor_error_detail(app_root))

        cli.bootstrap.load_duckdb = boom
        self.addCleanup(setattr, cli.bootstrap, "load_duckdb", original)
        code, _, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 7)
        self.assertIn("期待した vendor ディレクトリ", err)
        self.assertIn("実行中の環境", err)
        self.assertIn("用意されている vendor", err)
        self.assertIn("README.md", err)


class TestExitCode99(ExitCodeCase):
    def test_unexpected_exception(self):
        original = cli.metrics.list_series

        def boom(con):
            raise RuntimeError("想定外")

        cli.metrics.list_series = boom
        self.addCleanup(setattr, cli.metrics, "list_series", original)
        code, _, err = run_main([self.with_good_logs()], cwd=self.work)
        self.assertEqual(code, 99)
        self.assertIn("Traceback", err)


class TestAllNineCodesCovered(unittest.TestCase):
    def test_nine_distinct_codes_are_tested(self):
        covered = {0, 1, 2, 3, 4, 5, 6, 7, 99}
        from s_anomaly import errors

        declared = set(errors.EXIT_CODE_MAP) | {0, errors.UNEXPECTED_EXIT_CODE}
        self.assertEqual(covered, declared)


if __name__ == "__main__":
    unittest.main()
