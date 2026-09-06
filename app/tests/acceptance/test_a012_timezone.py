"""A012 — タイムゾーンの疎通確認 (※CR-008)。

## 試験の範囲(2026-09-06 に依頼者が指定)

**本アプリが使われることが想定されているシステムはすべて UTC である。**
したがって

* **性能・バリエーション・エラー系の試験は、入力 UTC / レポート UTC だけで行う**
  (A001〜A011 および単体・結合テストがそれにあたる。本ファイルでは行わない)。
* **タイムゾーンを指定した構成については、疎通確認のみを行う。**

本ファイルが確かめるのは次の 3 つだけである。

| # | 構成 | 確かめること |
| --- | --- | --- |
| 1 | 入力 JST / レポート UTC | 完走し、時刻が UTC へ寄せられ、`(UTC)` が併記される |
| 2 | 入力 UTC / レポート JST | 完走し、時刻が JST へ寄せられ、`(Asia/Tokyo)` が併記される |
| 3 | **sar が無い場合** | sar は当面 分析対象にならないため、**無くても完走する**こと |

**深追いはしない。** 変換規則そのものの正しさは単体テスト
(`tests/unit/test_timezone.py`)が確かめている。
"""

import os
import re
import shutil
import tempfile
import unittest

from tests.acceptance import _harness as H

#: 時刻欄の書式。※CR-008 で「(タイムゾーン)」が併記されるようになった。
TS_WITH_TZ = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \(([^)]+)\)")

#: タイムゾーンが併記されていない時刻(あってはならない)。
TS_BARE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?! \()")


def timezone_settings(storage, sources):
    """`[timezone]` セクションの設定文字列を作る。"""
    lines = ["[timezone]", "storage = {0}".format(storage)]
    for kind, name in sorted(sources.items()):
        lines.append("{0} = {1}".format(kind, name))
    return "\n".join(lines) + "\n"


def run_with(tmp, label, settings, logs):
    """設定を差し替えた配布物で 1 回実行する。"""
    entry = H.make_dist(os.path.join(tmp, "dist-" + label),
                        settings=settings, with_tests=False)
    work = os.path.join(tmp, "work-" + label)
    os.makedirs(work)
    proc = H.run_app(logs, work, entry=entry)
    return proc, work


def period_start(summary_text):
    """サマリの「対象期間」の開始時刻と、併記されたタイムゾーンを返す。"""
    for line in summary_text.splitlines():
        if line.startswith("| 対象期間 |"):
            m = TS_WITH_TZ.search(line)
            if m:
                return m.group(1), m.group(2)
    return None, None


class TestA012TimezonePassthrough(H.BaselineCase):
    """1 と 2 — タイムゾーンを指定した構成の疎通確認。"""

    @classmethod
    def setUpClass(cls):
        super(TestA012TimezonePassthrough, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name
        kinds = ["db_connection", "jvm_gc", "lsf_queue"]

        # 基準: 入力 UTC / レポート UTC (既定の構成)
        cls.base_proc, cls.base_work = run_with(
            tmp, "utc",
            timezone_settings("UTC", dict((k, "UTC") for k in kinds)),
            cls.normal_logs)

        # 1) 入力 JST / レポート UTC
        cls.jst_in_proc, cls.jst_in_work = run_with(
            tmp, "jst-in",
            timezone_settings("UTC", dict((k, "Asia/Tokyo") for k in kinds)),
            cls.normal_logs)

        # 2) 入力 UTC / レポート JST
        cls.jst_out_proc, cls.jst_out_work = run_with(
            tmp, "jst-out",
            timezone_settings("Asia/Tokyo", dict((k, "UTC") for k in kinds)),
            cls.normal_logs)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # --- 1) 入力 JST / レポート UTC -------------------------------------
    def test_01_jst_input_completes(self):
        self.assertEqual(self.jst_in_proc.returncode, 0,
                         H.err(self.jst_in_proc)[-2000:])
        self.assertTrue(H.summary_paths(self.jst_in_work), "サマリが無い")

    def test_02_jst_input_says_it_converts(self):
        self.assertIn("投入時にタイムゾーンを変換します",
                      H.out(self.jst_in_proc))

    def test_03_jst_input_is_shifted_back_nine_hours(self):
        """JST の 00:00 は UTC の前日 15:00 になる。"""
        base, base_tz = period_start(H.read_summary(self.base_work))
        got, got_tz = period_start(H.read_summary(self.jst_in_work))
        self.assertEqual(base_tz, "UTC")
        self.assertEqual(got_tz, "UTC")
        self.assertEqual(base, "2026-06-01 00:00:00")
        self.assertEqual(got, "2026-05-31 15:00:00")

    # --- 2) 入力 UTC / レポート JST -------------------------------------
    def test_04_jst_output_completes(self):
        self.assertEqual(self.jst_out_proc.returncode, 0,
                         H.err(self.jst_out_proc)[-2000:])
        self.assertTrue(H.summary_paths(self.jst_out_work), "サマリが無い")

    def test_05_jst_output_is_shifted_forward_nine_hours(self):
        got, got_tz = period_start(H.read_summary(self.jst_out_work))
        self.assertEqual(got_tz, "Asia/Tokyo")
        self.assertEqual(got, "2026-06-01 09:00:00")

    def test_06_label_is_the_storage_timezone(self):
        """**併記されるのは格納タイムゾーンである。**"""
        self.assertIn("(Asia/Tokyo)", H.read_all_reports(self.jst_out_work))
        self.assertNotIn("(Asia/Tokyo)", H.read_all_reports(self.jst_in_work))

    # --- 共通 ------------------------------------------------------------
    def test_07_every_timestamp_carries_a_timezone(self):
        for label, work in (("UTC", self.base_work),
                            ("JST入力", self.jst_in_work),
                            ("JST出力", self.jst_out_work)):
            text = H.read_all_reports(work)
            offenders = [l.strip() for l in text.splitlines() if TS_BARE.search(l)]
            self.assertEqual(offenders, [],
                             "{0}: 併記の無い行 {1}".format(label, offenders[:3]))

    def test_08_baseline_does_not_convert(self):
        """**既定(全 UTC)では変換が発生しない。**"""
        self.assertEqual(self.base_proc.returncode, 0)
        out = H.out(self.base_proc)
        self.assertIn("変換は行いません", out)
        self.assertNotIn("投入時にタイムゾーンを変換します", out)


class TestA012WithoutSar(H.BaselineCase):
    """3 — sar が無い場合の疎通確認。

    **sar は当面 分析対象にならない**(2026-09-06 の依頼者の指示)。
    したがって「sar が無くても支障が無いこと」を確かめる。
    """

    @classmethod
    def setUpClass(cls):
        super(TestA012WithoutSar, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name

        # 3a) sar のファイルも sar の設定も無い (通常の構成)
        cls.plain_proc, cls.plain_work = run_with(
            tmp, "nosar", None, cls.normal_logs)

        # 3b) sa-*.csv が置いてあっても無視されること
        logs = os.path.join(tmp, "logs-with-sa")
        shutil.copytree(cls.normal_logs, logs)
        with open(os.path.join(logs, "sa-host01-20260601.csv"),
                  "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# hostname;interval;timestamp;CPU;%user\n")
            handle.write("host01;600;2026-06-01 00:10:00 UTC;-1;1.23\n")
        cls.with_sa_proc, cls.with_sa_work = run_with(
            tmp, "with-sa", None, logs)

        # 3c) [timezone] に sar のキーを書いた場合 (未知のキーとして無視される)
        cls.sar_key_proc, cls.sar_key_work = run_with(
            tmp, "sar-key",
            "[timezone]\nstorage = UTC\nsar = UTC\n", cls.normal_logs)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_01_runs_without_any_sar_input(self):
        self.assertEqual(self.plain_proc.returncode, 0,
                         H.err(self.plain_proc)[-2000:])
        self.assertTrue(H.summary_paths(self.plain_work))

    def test_02_sa_csv_is_ignored(self):
        """`sa-*.csv` を置いても**無視され、結果が変わらない**。"""
        self.assertEqual(self.with_sa_proc.returncode, 0,
                         H.err(self.with_sa_proc)[-2000:])

        def normalize(work):
            # 「対象ディレクトリ」は本試験でのみ別の場所へ複製しているため除く。
            # **アプリの非決定性ではない。**
            return [line for line in
                    H.strip_volatile(H.read_all_reports(work)).splitlines()
                    if not line.startswith("| 対象ディレクトリ |")]

        self.assertEqual(normalize(self.with_sa_work),
                         normalize(self.plain_work))

    def test_03_sar_key_is_ignored_with_a_warning(self):
        """**`[timezone] sar` はまだ未知のキーである。** 警告して無視し、止まらない。"""
        self.assertEqual(self.sar_key_proc.returncode, 0,
                         H.err(self.sar_key_proc)[-2000:])
        self.assertIn("未知のキー", H.err(self.sar_key_proc)
                      + H.out(self.sar_key_proc))

    def test_04_no_sar_key_is_required(self):
        """設定に sar が無くても、タイムゾーンの検証は通る。"""
        self.assertNotIn("タイムゾーン", H.err(self.plain_proc))
        self.assertIn("変換は行いません", H.out(self.plain_proc))


if __name__ == "__main__":
    unittest.main()
