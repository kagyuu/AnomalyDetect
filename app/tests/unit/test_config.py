"""U001-T4 の単体テスト: config。"""

import io
import os
import tempfile
import unittest

from s_anomaly import config, errors, progress as progress_mod

# docs/P002-frontend-spec.md 2.2 の表の全キー(集合比較で網羅を担保する)
EXPECTED_KEYS = set(
    [("algorithms", a) for a in config.ALGORITHM_IDS]
    + [
        ("parameters", k) for k in [
            "ALG-A1.window_minutes", "ALG-A1.k_warn", "ALG-A1.k_fatal",
            "ALG-A2.window_minutes", "ALG-A2.k",
            "ALG-A3.iqr_warn", "ALG-A3.iqr_fatal",
            "ALG-A4.lambda", "ALG-A4.k",
            "ALG-A5.z", "ALG-A5.min_weeks",
            "ALG-B1.short_minutes", "ALG-B1.long_minutes", "ALG-B1.min_run",
            "ALG-B2.bucket_minutes", "ALG-B2.p_warn", "ALG-B2.p_fatal",
            "ALG-B2.max_buckets",
            "ALG-B3.window_minutes", "ALG-B3.min_increases",
            "ALG-B4.baseline_minutes", "ALG-B4.h",
            "ALG-B5.min_r2",
            # ※CR-005 ALG-C1 上限への張り付き
            "ALG-C1.ratio_pct", "ALG-C1.min_minutes", "ALG-C1.min_spread",
            # ※CR-007 S6 のスレッド数
            "common.detect_threads",
            "common.min_points", "common.sigma_floor_ratio", "common.sigma_floor_abs",
            "events.severe_pct", "events.merge_gap_minutes",
        ]
    ]
    + [("duckdb", "max_memory"), ("duckdb", "temp_directory")]
    # ※CR-008 タイムゾーン。格納先 1 つと、入力の種別ごとに 1 つずつ。
    + [("timezone", "storage")]
    + [("timezone", kind) for kind in config.SOURCE_KINDS]
    # ※CR-009 チャート。
    + [("chart", k) for k in ("enabled", "min_severity", "max_per_host",
                              "max_overlay_per_host", "pad_ratio")]
    # ※CR-010 sar。取り込む活動種別。
    + [("sar", "activities")]
)


def make_progress():
    out, err = io.StringIO(), io.StringIO()
    return progress_mod.setup(out, err), out, err


def write_config(tmpdir, text):
    path = os.path.join(tmpdir, "settings.properties")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


class TestParamsTable(unittest.TestCase):
    def test_keys_match_spec_exactly(self):
        got = set((p.section, p.key) for p in config.PARAMS)
        self.assertEqual(got, EXPECTED_KEYS)

    def test_no_duplicate_keys(self):
        got = [(p.section, p.key) for p in config.PARAMS]
        self.assertEqual(len(got), len(set(got)))

    def test_algorithm_ids_count(self):
        self.assertEqual(len(config.ALGORITHM_IDS), 11)   # ※CR-005 ALG-C1


class TestDefaults(unittest.TestCase):
    def test_missing_file_uses_defaults(self):
        progress, out, _ = make_progress()
        cfg = config.load(None, progress)
        self.assertEqual(cfg.param("ALG-A1.window_minutes"), 60)
        self.assertEqual(cfg.max_memory, "4GB")
        self.assertIn("既定値で動作します", out.getvalue())

    def test_enabled_algorithms_sorted(self):
        progress, _, _ = make_progress()
        cfg = config.load(None, progress)
        got = cfg.enabled_algorithms()
        self.assertEqual(got, sorted(got))
        self.assertEqual(len(got), 11)   # ※CR-005

    def test_partial_override(self):
        progress, _, _ = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[parameters]\nALG-A1.k_warn = 2.5\n")
            cfg = config.load(path, progress)
        self.assertEqual(cfg.param("ALG-A1.k_warn"), 2.5)
        self.assertEqual(cfg.param("ALG-A1.k_fatal"), 3.0)

    def test_boolean_spellings(self):
        for truthy in ("true", "TRUE", "yes", "on", "1"):
            progress, _, _ = make_progress()
            with tempfile.TemporaryDirectory() as tmp:
                path = write_config(tmp, "[algorithms]\nALG-A1 = {0}\n".format(truthy))
                cfg = config.load(path, progress)
            self.assertIn("ALG-A1", cfg.enabled_algorithms(), truthy)
        for falsy in ("false", "FALSE", "no", "off", "0"):
            progress, _, _ = make_progress()
            with tempfile.TemporaryDirectory() as tmp:
                path = write_config(tmp, "[algorithms]\nALG-A1 = {0}\n".format(falsy))
                cfg = config.load(path, progress)
            self.assertNotIn("ALG-A1", cfg.enabled_algorithms(), falsy)

    def test_all_disabled(self):
        progress, _, _ = make_progress()
        body = "[algorithms]\n" + "".join(
            "{0} = false\n".format(a) for a in config.ALGORITHM_IDS
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(write_config(tmp, body), progress)
        self.assertTrue(cfg.all_disabled())
        self.assertEqual(cfg.enabled_algorithms(), [])

    def test_not_all_disabled_when_one_enabled(self):
        progress, _, _ = make_progress()
        body = "[algorithms]\n" + "".join(
            "{0} = false\n".format(a) for a in config.ALGORITHM_IDS[:-1]
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(write_config(tmp, body), progress)
        self.assertFalse(cfg.all_disabled())


class TestValidation(unittest.TestCase):
    #: 表駆動: 各キーに範囲外の値を与えて ConfigError になること
    OUT_OF_RANGE = [
        ("parameters", "ALG-A4.lambda", "1.5"),
        ("parameters", "ALG-A4.lambda", "0"),
        ("parameters", "common.min_points", "1"),
        ("parameters", "ALG-B2.p_warn", "1.0"),
        ("parameters", "ALG-B2.p_warn", "0"),
        ("parameters", "ALG-B3.min_increases", "1"),
        ("parameters", "ALG-B2.max_buckets", "10"),
        ("parameters", "events.severe_pct", "0"),
        ("parameters", "events.severe_pct", "120"),
        ("parameters", "events.merge_gap_minutes", "-1"),
        ("parameters", "ALG-A1.window_minutes", "0"),
        ("parameters", "ALG-B5.min_r2", "1.5"),
        ("parameters", "common.sigma_floor_ratio", "2"),
        ("parameters", "common.sigma_floor_abs", "-1"),
        ("parameters", "ALG-A5.min_weeks", "0"),
    ]

    def test_out_of_range_values(self):
        for section, key, value in self.OUT_OF_RANGE:
            progress, _, _ = make_progress()
            body = "[{0}]\n{1} = {2}\n".format(section, key, value)
            with tempfile.TemporaryDirectory() as tmp:
                path = write_config(tmp, body)
                with self.assertRaises(errors.ConfigError, msg="{0}={1}".format(key, value)):
                    config.load(path, progress)

    def test_non_numeric(self):
        progress, _, _ = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[parameters]\ncommon.min_points = abc\n")
            with self.assertRaises(errors.ConfigError):
                config.load(path, progress)

    def test_correlation_rules(self):
        cases = [
            "[parameters]\nALG-A1.k_warn = 3.0\nALG-A1.k_fatal = 2.0\n",
            "[parameters]\nALG-A3.iqr_warn = 3.0\nALG-A3.iqr_fatal = 1.5\n",
            "[parameters]\nALG-B1.short_minutes = 2000\n",
            "[parameters]\nALG-B2.p_warn = 0.01\nALG-B2.p_fatal = 0.05\n",
        ]
        for body in cases:
            progress, _, _ = make_progress()
            with tempfile.TemporaryDirectory() as tmp:
                path = write_config(tmp, body)
                with self.assertRaises(errors.ConfigError, msg=body):
                    config.load(path, progress)

    def test_all_violations_reported_at_once(self):
        progress, _, _ = make_progress()
        body = (
            "[parameters]\n"
            "ALG-A4.lambda = 1.5\n"
            "common.min_points = 1\n"
            "ALG-B5.min_r2 = 9\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, body)
            with self.assertRaises(errors.ConfigError) as ctx:
                config.load(path, progress)
        detail = ctx.exception.user_message()
        self.assertIn("ALG-A4.lambda", detail)
        self.assertIn("common.min_points", detail)
        self.assertIn("ALG-B5.min_r2", detail)

    def test_bad_max_memory(self):
        progress, _, _ = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[duckdb]\nmax_memory = 4 gigabytes\n")
            with self.assertRaises(errors.ConfigError):
                config.load(path, progress)

    def test_good_max_memory_forms(self):
        for value in ("4GB", "512MB", "1.5gb", "100KB"):
            progress, _, _ = make_progress()
            with tempfile.TemporaryDirectory() as tmp:
                path = write_config(tmp, "[duckdb]\nmax_memory = {0}\n".format(value))
                cfg = config.load(path, progress)
            self.assertEqual(cfg.max_memory, value)

    def test_config_error_exit_code(self):
        self.assertEqual(errors.ConfigError("x").exit_code, 3)


class TestUnknownKeys(unittest.TestCase):
    def test_unknown_key_warns_only(self):
        progress, _, err = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[parameters]\nnot.a.key = 1\n")
            cfg = config.load(path, progress)
        self.assertIsNotNone(cfg)
        self.assertIn("未知のキー", err.getvalue())

    def test_unknown_section_warns_only(self):
        progress, _, err = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[nope]\nx = 1\n")
            config.load(path, progress)
        self.assertIn("未知のセクション", err.getvalue())

    def test_algorithm_like_unknown_key_gets_stronger_warning(self):
        progress, _, err = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[algorithms]\nALG-A6 = true\n")
            config.load(path, progress)
        self.assertIn("有効な ID は", err.getvalue())


class TestShippedSettingsFile(unittest.TestCase):
    def test_default_settings_properties_is_valid(self):
        """配布する settings.properties 自体が検証を通ること。"""
        here = os.path.dirname(os.path.abspath(__file__))
        app_dir = os.path.dirname(os.path.dirname(here))
        path = os.path.join(app_dir, "settings.properties")
        self.assertTrue(os.path.isfile(path), path)
        progress, _, err = make_progress()
        cfg = config.load(path, progress)
        self.assertEqual(len(cfg.enabled_algorithms()), 11)   # ※CR-005
        self.assertNotIn("未知の", err.getvalue())


if __name__ == "__main__":
    unittest.main()
