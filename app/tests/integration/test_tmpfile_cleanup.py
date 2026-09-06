"""T007 — 一時ファイルの後始末 (O-03)。"""

import io
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import cli, config, events, progress as progress_mod, reporter
from s_anomaly.detectors.base import Detection
from s_anomaly.errors import ReportWriteError

NOW = datetime(2026, 6, 15, 10, 23, 45)
BASE_TS = datetime(2026, 6, 1)


def make_ctx(events_list):
    return reporter.ReportContext(
        now=NOW, target_dir="/logs", period=(BASE_TS, BASE_TS + timedelta(days=1)),
        file_count=1, record_count=10, series_count=1, events=events_list,
        algorithms=[("ALG-A1", "移動平均乖離率", "観点1", True, "")],
        load_errors=[], skipped=[], failures=[], vendor_note=None,
    )


def make_events(n, cfg):
    dets = []
    for i in range(n):
        dets.append(Detection(
            series_id="s/{0}".format(i), source="db_connection",
            metric="active_connections", algorithm="ALG-A1",
            start_ts=BASE_TS + timedelta(hours=i),
            end_ts=BASE_TS + timedelta(hours=i),
            values=[1.0, 2.0], score=1.5, severity_hint="WARN",
            detail={"mu": 1.0, "value": 2.0}, shape="spike_up",
        ))
    evs = events.merge_detections(dets, cfg)
    for e in evs:
        e.severity = "WARN"
    return evs


class TestTmpFileCleanup(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(
            None, progress_mod.setup(io.StringIO(), io.StringIO()))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = self._tmp.name
        self.path = os.path.join(self.work, "report.md")

    def files(self):
        return sorted(os.listdir(self.work))

    def test_01_02_normal_write(self):
        # ※CR-001: write_reports がファイル群を書く。一時ファイルは残らない。
        ctx = make_ctx(make_events(3, self.cfg))
        written = reporter.write_reports(ctx, self.work)
        self.assertTrue(written)
        names = self.files()
        for path in written:
            self.assertIn(os.path.basename(path), names)
        self.assertFalse([n for n in names if n.endswith(".tmp")], names)

    def test_01b_summary_always_written(self):
        """検知 0 件でもサマリは必ず出る (UI-04-F04)。"""
        reporter.write_reports(make_ctx([]), self.work)
        self.assertTrue([n for n in self.files()
                         if n.startswith("report_summary_")])

    def test_03_04_05_physical_format(self):
        written = reporter.write_reports(make_ctx(make_events(2, self.cfg)),
                                         self.work)
        # ※CR-001: 物理仕様は全ファイルについて確認する
        for path in written:
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"\r\n", raw)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            raw.decode("utf-8")

    def test_06_07_overwrite(self):
        reporter.write_report("first\n", self.path)
        reporter.write_report("second\n", self.path)
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "second\n")
        self.assertNotIn("report.md.tmp", self.files())

    def test_08_09_10_11_failure_keeps_previous(self):
        reporter.write_report("good\n", self.path)
        original = reporter.os.replace

        def boom(a, b):
            raise OSError("disk full")

        reporter.os.replace = boom
        self.addCleanup(setattr, reporter.os, "replace", original)
        with self.assertRaises(ReportWriteError) as ctx:
            reporter.write_report("bad\n", self.path)
        self.assertEqual(ctx.exception.exit_code, 5)
        self.assertNotIn("report.md.tmp", self.files())
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "good\n")

    def test_12_13_exit_code_5_and_fallback(self):
        original = reporter.write_report

        def boom(text, path):
            raise ReportWriteError("書き込めません", "  理由: テスト")

        cli.reporter.write_report = boom
        self.addCleanup(setattr, cli.reporter, "write_report", original)

        fixtures = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "fixtures")
        logs = os.path.join(self.work, "logs")
        os.makedirs(logs)
        shutil.copy(os.path.join(fixtures, "DBConnection_20260601.csv"),
                    os.path.join(logs, "DBConnection_20260601.csv"))
        out, err = io.StringIO(), io.StringIO()
        previous = os.getcwd()
        os.chdir(self.work)
        try:
            code = cli.main([logs], stream_out=out, stream_err=err)
        finally:
            os.chdir(previous)
        self.assertEqual(code, 5)
        # ※CR-001: フォールバックは全ホスト分を連結した全文である
        self.assertIn("# 異常検知サマリ", out.getvalue())

    def test_14_15_stale_tmp_removed(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as handle:
            handle.write("### 途中で切れたレポート")
        reporter.write_report("ok\n", self.path)
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "ok\n")
        self.assertNotIn("report.md.tmp", self.files())


if __name__ == "__main__":
    unittest.main()
