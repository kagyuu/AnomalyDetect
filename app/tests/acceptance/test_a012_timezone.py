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
| 3 | **sar を含む構成** ※CR-010 で意味が変わった | sar も `[timezone]` の対象になった。**sar が無くても完走し、`[timezone] sar` が既知のキーとして受理される**こと |

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
        # ※CR-010 sar を含めた 4 種別をまとめて変換する。
        # 一部だけ変換すると入力の時刻軸がずれ、疎通確認の意味が薄れる。
        kinds = ["db_connection", "jvm_gc", "lsf_queue", "sar"]

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
    """3 — sar を含む/含まない構成の疎通確認。

    ★CR-010 で前提が変わった★ 以前は「sar は当面 分析対象にならない」
    (2026-09-06 の指示)ため「置いても無視されること」を確かめていた。
    **CR-010 で sar は正式な入力種別になった**ため、確かめることを
    「**sar が無くても完走すること**」と「**`[timezone] sar` が既知のキーとして
    受理されること**」に改めた。
    """

    @classmethod
    def setUpClass(cls):
        super(TestA012WithoutSar, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name

        # 3a) sar のファイルも sar の設定も無い (通常の構成)
        cls.plain_proc, cls.plain_work = run_with(
            tmp, "nosar", None, cls.normal_logs)

        # 3b) ※CR-010 sar のファイルを取り除いた構成でも完走すること
        logs = os.path.join(tmp, "logs-without-sa")
        shutil.copytree(cls.normal_logs, logs)
        for name in os.listdir(logs):
            if name.startswith("sa-"):
                os.unlink(os.path.join(logs, name))
        cls.without_sa_proc, cls.without_sa_work = run_with(
            tmp, "without-sa", None, logs)

        # 3c) [timezone] に sar のキーを書いた場合 (※CR-010 で既知のキーになった)
        cls.sar_key_proc, cls.sar_key_work = run_with(
            tmp, "sar-key",
            "[timezone]\nstorage = UTC\nsar = UTC\n", cls.normal_logs)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_01_runs_with_sar_input(self):
        self.assertEqual(self.plain_proc.returncode, 0,
                         H.err(self.plain_proc)[-2000:])
        self.assertTrue(H.summary_paths(self.plain_work))

    def test_02_runs_without_any_sar_file(self):
        """※CR-010 **`sa-*.csv` が 1 件も無くても完走する**(後方互換)。"""
        self.assertEqual(self.without_sa_proc.returncode, 0,
                         H.err(self.without_sa_proc)[-2000:])
        self.assertTrue(H.summary_paths(self.without_sa_work))
        self.assertNotIn("sar /", H.read_all_reports(self.without_sa_work))

    def test_03_sar_key_is_accepted(self):
        """※CR-010 **`[timezone] sar` は既知のキーである。** 警告を出さない。

        `config.SOURCE_KINDS` に `sar` が無いと、ここが「未知のキー」に戻る。
        そのとき**時刻変換が行われず、時差のある環境で突き合わせが静かに
        全て外れる**(P007 U010 §3 #2)。
        """
        self.assertEqual(self.sar_key_proc.returncode, 0,
                         H.err(self.sar_key_proc)[-2000:])
        combined = H.err(self.sar_key_proc) + H.out(self.sar_key_proc)
        self.assertNotIn("未知のキー [timezone] sar", combined)

    def test_04_no_sar_key_is_required(self):
        """設定に sar が無くても、タイムゾーンの検証は通る。"""
        self.assertNotIn("タイムゾーン", H.err(self.plain_proc))
        self.assertIn("変換は行いません", H.out(self.plain_proc))


if __name__ == "__main__":
    unittest.main()
