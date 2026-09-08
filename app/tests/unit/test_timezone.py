"""U010 の単体テスト: タイムゾーンの設定・変換・併記 (※CR-008)。

**本アプリが使われることが想定されているシステムはすべて UTC である。**
したがって最も重要な試験は「**既定の構成では変換が一切起きないこと**」であり、
変換そのものよりも、**変換しない条件が正しく効いているか**を厚く確かめる。
"""

import io
import os
import tempfile
import unittest
from datetime import datetime

from s_anomaly import bootstrap, config, loaders, progress as progress_mod, schema
from s_anomaly import reporter


def make_progress():
    out, err = io.StringIO(), io.StringIO()
    return progress_mod.setup(out, err), out, err


def write_config(tmpdir, text):
    path = os.path.join(tmpdir, "settings.properties")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def load_cfg(text=None):
    progress, _, _ = make_progress()
    if text is None:
        return config.load(None, progress)
    with tempfile.TemporaryDirectory() as tmp:
        return config.load(write_config(tmp, text), progress)


# ---------------------------------------------------------------------------
# 設定値
# ---------------------------------------------------------------------------
class TestSettings(unittest.TestCase):
    def test_defaults_are_all_utc(self):
        """**既定はすべて UTC**(依頼者の明示。2026-09-06)。"""
        cfg = load_cfg()
        self.assertEqual(cfg.storage_timezone, "UTC")
        for kind in config.SOURCE_KINDS:
            self.assertEqual(cfg.source_timezone(kind), "UTC", kind)

    def test_defaults_need_no_conversion(self):
        """**既定の構成では変換が一度も発生しない。**"""
        cfg = load_cfg()
        self.assertEqual(config.converted_kinds(cfg), [])
        for kind in config.SOURCE_KINDS:
            self.assertFalse(cfg.needs_conversion(kind), kind)

    def test_shipped_settings_file_is_all_utc(self):
        """**配布する settings.properties も全て UTC であること。**"""
        progress, _, _ = make_progress()
        shipped = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "settings.properties")
        self.assertTrue(os.path.isfile(shipped), shipped)
        cfg = config.load(shipped, progress)
        self.assertEqual(cfg.storage_timezone, "UTC")
        for kind in config.SOURCE_KINDS:
            self.assertEqual(cfg.source_timezone(kind), "UTC", kind)
        self.assertEqual(config.converted_kinds(cfg), [])

    def test_per_kind_override(self):
        cfg = load_cfg("[timezone]\nstorage = UTC\ndb_connection = Asia/Tokyo\n")
        self.assertEqual(cfg.source_timezone("db_connection"), "Asia/Tokyo")
        self.assertEqual(cfg.source_timezone("jvm_gc"), "UTC")
        self.assertTrue(cfg.needs_conversion("db_connection"))
        self.assertFalse(cfg.needs_conversion("jvm_gc"))
        self.assertEqual(config.converted_kinds(cfg), ["db_connection"])

    def test_same_source_and_storage_needs_no_conversion(self):
        """**両方を同じ値にすれば、UTC 以外でも変換しない**(2026-09-06 の指示)。"""
        cfg = load_cfg("[timezone]\nstorage = Asia/Tokyo\n"
                       "db_connection = Asia/Tokyo\njvm_gc = Asia/Tokyo\n"
                       "lsf_queue = Asia/Tokyo\nsar = Asia/Tokyo\n")   # ※CR-010
        self.assertEqual(config.converted_kinds(cfg), [])

    def test_unknown_kind_falls_back_to_storage(self):
        """未知の種別は「変換しない」に倒す。"""
        cfg = load_cfg()
        self.assertEqual(cfg.source_timezone("sar"), cfg.storage_timezone)
        self.assertFalse(cfg.needs_conversion("sar"))


# ---------------------------------------------------------------------------
# タイムゾーン名の検証
# ---------------------------------------------------------------------------
class TestValidation(unittest.TestCase):
    def setUp(self):
        self.progress, _, _ = make_progress()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "256MB", os.path.join(self._tmp.name, "tmp"), self.progress)
        self.addCleanup(self.con.close)

    def _validate(self, text=None):
        config.validate_timezones(self.con, load_cfg(text), self.progress)

    def test_defaults_pass(self):
        self._validate()

    def test_named_zone_passes(self):
        self._validate("[timezone]\ndb_connection = Asia/Tokyo\n")

    def test_fixed_offset_is_rejected(self):
        """**固定オフセットは DuckDB が解釈できない**ため受け付けない。"""
        with self.assertRaises(Exception) as ctx:
            self._validate("[timezone]\ndb_connection = +09:00\n")
        self.assertEqual(ctx.exception.exit_code, 3)
        self.assertIn("+09:00", str(ctx.exception.detail))

    def test_case_is_significant(self):
        """`utc` は通らない(DuckDB が大文字小文字を区別する)。"""
        with self.assertRaises(Exception) as ctx:
            self._validate("[timezone]\nstorage = utc\n")
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_unknown_zone_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._validate("[timezone]\njvm_gc = Asia/Nowhere\n")
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_all_violations_are_reported_at_once(self):
        """誤りは 1 件ずつではなく**まとめて全件**報告する。"""
        with self.assertRaises(Exception) as ctx:
            self._validate("[timezone]\ndb_connection = Bad/One\n"
                           "jvm_gc = Bad/Two\nlsf_queue = Bad/Three\n")
        detail = str(ctx.exception.detail)
        for bad in ("Bad/One", "Bad/Two", "Bad/Three"):
            self.assertIn(bad, detail)

    def test_progress_says_no_conversion_for_defaults(self):
        # **cfg を先に作る。** load_cfg が内部で progress を作り直すため、
        # 順序を逆にすると出力先が差し替わって拾えない。
        cfg = load_cfg()
        progress, out, _ = make_progress()
        config.validate_timezones(self.con, cfg, progress)
        self.assertIn("変換は行いません", out.getvalue())

    def test_progress_lists_conversions(self):
        cfg = load_cfg("[timezone]\ndb_connection = Asia/Tokyo\n")
        progress, out, _ = make_progress()
        config.validate_timezones(self.con, cfg, progress)
        text = out.getvalue()
        self.assertIn("変換します", text)
        self.assertIn("db_connection <- Asia/Tokyo", text)


# ---------------------------------------------------------------------------
# 投入時の変換
# ---------------------------------------------------------------------------
class TestIngestConversion(unittest.TestCase):
    def setUp(self):
        self.progress, _, _ = make_progress()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "256MB", os.path.join(self._tmp.name, "tmp"), self.progress)
        self.addCleanup(self.con.close)
        schema.create_all(self.con)

    def _insert(self, cfg, ts=datetime(2026, 6, 1, 9, 0, 0)):
        ins = loaders.BatchInserter(self.con, "db_connection", cfg=cfg)
        ins.add({"ts": ts, "host": "h", "port": 1, "datasource": "d",
                 "active_connections": 1})
        ins.flush()
        return self.con.execute("SELECT ts FROM db_connection").fetchone()[0]

    def test_no_cfg_means_no_conversion(self):
        """cfg を渡さなければ変換しない(既存の呼び出しを壊さない)。"""
        self.assertEqual(self._insert(None), datetime(2026, 6, 1, 9, 0, 0))

    def test_defaults_store_the_value_as_is(self):
        """**既定(全 UTC)では投入値がそのまま入る。**"""
        self.assertEqual(self._insert(load_cfg()), datetime(2026, 6, 1, 9, 0, 0))

    def test_jst_to_utc(self):
        """JST の 09:00 は UTC の 00:00 になる。"""
        cfg = load_cfg("[timezone]\nstorage = UTC\ndb_connection = Asia/Tokyo\n")
        self.assertEqual(self._insert(cfg), datetime(2026, 6, 1, 0, 0, 0))

    def test_utc_to_jst(self):
        """逆向きも効く。"""
        cfg = load_cfg("[timezone]\nstorage = Asia/Tokyo\ndb_connection = UTC\n")
        self.assertEqual(self._insert(cfg), datetime(2026, 6, 1, 18, 0, 0))

    def test_same_zone_emits_no_conversion_sql(self):
        """**同一なら SQL に `AT TIME ZONE` を出さない**(2026-09-06 の指示)。"""
        ins = loaders.BatchInserter(self.con, "db_connection", cfg=load_cfg())
        self.assertEqual(ins._select, "*")

    def test_different_zone_emits_conversion_sql(self):
        cfg = load_cfg("[timezone]\ndb_connection = Asia/Tokyo\n")
        ins = loaders.BatchInserter(self.con, "db_connection", cfg=cfg)
        self.assertIn("AT TIME ZONE 'Asia/Tokyo'", ins._select)
        self.assertIn("AS ts", ins._select)

    def test_other_kinds_are_unaffected(self):
        """種別ごとに独立していること。"""
        cfg = load_cfg("[timezone]\ndb_connection = Asia/Tokyo\n")
        self.assertEqual(
            loaders.BatchInserter(self.con, "jvm_gc", cfg=cfg)._select, "*")


# ---------------------------------------------------------------------------
# レポートへの併記
# ---------------------------------------------------------------------------
class TestReportLabel(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reporter.set_timezone, config.DEFAULT_TIMEZONE)

    def test_default_label_is_utc(self):
        reporter.set_timezone(None)
        self.assertEqual(reporter.timezone_label(), "UTC")
        self.assertEqual(
            reporter.format_ts(datetime(2026, 9, 6, 1, 1, 0)),
            "2026-09-06 01:01:00 (UTC)")

    def test_label_follows_storage_timezone(self):
        reporter.set_timezone("Asia/Tokyo")
        self.assertEqual(
            reporter.format_ts(datetime(2026, 9, 6, 10, 1, 0)),
            "2026-09-06 10:01:00 (Asia/Tokyo)")

    def test_value_is_not_reconverted(self):
        """**表示のために時刻を変換しない。** 併記するだけである。"""
        ts = datetime(2026, 9, 6, 1, 1, 0)
        reporter.set_timezone("Asia/Tokyo")
        self.assertTrue(reporter.format_ts(ts).startswith("2026-09-06 01:01:00"))

    def test_none_is_still_unknown(self):
        self.assertEqual(reporter.format_ts(None), "不明")


if __name__ == "__main__":
    unittest.main()
