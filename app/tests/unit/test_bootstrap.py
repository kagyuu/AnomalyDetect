"""U001-T1 の単体テスト: bootstrap と errors。"""

import io
import os
import re
import tempfile
import unittest

from s_anomaly import bootstrap, errors, progress as progress_mod


def make_progress():
    out, err = io.StringIO(), io.StringIO()
    return progress_mod.setup(out, err), out, err


class TestPlatformTag(unittest.TestCase):
    def test_format(self):
        tag = bootstrap.platform_tag()
        self.assertRegex(tag, r"^[a-z0-9_]+-[a-z0-9_]+-cp\d{2,3}$")

    def test_contains_running_python_version(self):
        import sys

        tag = bootstrap.platform_tag()
        self.assertTrue(
            tag.endswith("cp{0}{1}".format(sys.version_info[0], sys.version_info[1])),
            tag,
        )


class TestVendorResolution(unittest.TestCase):
    def test_resolve_returns_matching_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            want = os.path.join(tmp, "vendor", bootstrap.platform_tag())
            os.makedirs(want)
            self.assertEqual(bootstrap.resolve_vendor_dir(tmp), want)

    def test_resolve_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(bootstrap.resolve_vendor_dir(tmp))

    def test_list_available_is_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("win-amd64-cp38", "linux-x86_64-cp39", "linux-x86_64-cp310"):
                os.makedirs(os.path.join(tmp, "vendor", name))
            got = bootstrap.list_available_vendors(tmp)
            self.assertEqual(got, sorted(got))
            self.assertEqual(len(got), 3)

    def test_list_available_empty_when_no_vendor_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(bootstrap.list_available_vendors(tmp), [])


class TestLoadDuckdb(unittest.TestCase):
    def test_fallback_to_environment(self):
        progress, out, _ = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            module, note = bootstrap.load_duckdb(tmp, progress)
        self.assertTrue(hasattr(module, "connect"))
        # フォールバック経路を通ったことが記録されていること (DS-14-02 の ACCEPTED)
        self.assertIsNotNone(note)
        self.assertIn("vendor を使わず環境の DuckDB", out.getvalue())


class TestOpenConnection(unittest.TestCase):
    def test_opens_and_creates_temp_dir(self):
        progress, _, _ = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            module, _ = bootstrap.load_duckdb(tmp, progress)
            temp_dir = os.path.join(tmp, "nested", "tmp")
            con = bootstrap.open_connection(module, "512MB", temp_dir, progress)
            self.assertEqual(con.execute("SELECT 1").fetchone()[0], 1)
            self.assertTrue(os.path.isdir(temp_dir))
            con.close()

    def test_temp_directory_pointing_to_file_raises_config_error(self):
        progress, _, _ = make_progress()
        with tempfile.TemporaryDirectory() as tmp:
            module, _ = bootstrap.load_duckdb(tmp, progress)
            blocker = os.path.join(tmp, "blocker")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("x")
            with self.assertRaises(errors.ConfigError) as ctx:
                bootstrap.open_connection(module, "512MB", blocker, progress)
            self.assertEqual(ctx.exception.exit_code, 3)


class TestErrorExitCodes(unittest.TestCase):
    def test_exit_code_table(self):
        expected = {
            errors.ArgumentError: 1,
            errors.NoInputFileError: 2,
            errors.ConfigError: 3,
            errors.AllParseFailedError: 4,
            errors.ReportWriteError: 5,
            errors.StorageError: 6,
            errors.VendorError: 7,
        }
        for cls, code in expected.items():
            self.assertEqual(cls("x").exit_code, code, cls.__name__)

    def test_exit_code_map_matches_classes(self):
        for code, cls in errors.EXIT_CODE_MAP.items():
            self.assertEqual(cls.exit_code, code)

    def test_unexpected_exit_code(self):
        self.assertEqual(errors.UNEXPECTED_EXIT_CODE, 99)

    def test_user_message_includes_detail(self):
        exc = errors.ConfigError("だめ", "  理由: てすと")
        self.assertIn("だめ", exc.user_message())
        self.assertIn("理由: てすと", exc.user_message())


class TestVendorErrorMessage(unittest.TestCase):
    def test_detail_has_four_elements(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "vendor", "linux-x86_64-cp39"))
            detail = bootstrap._vendor_error_detail(tmp)
        self.assertIn("期待した vendor ディレクトリ", detail)
        self.assertIn("実行中の環境", detail)
        self.assertIn("用意されている vendor", detail)
        self.assertIn("README.md", detail)
        self.assertIn("linux-x86_64-cp39", detail)


if __name__ == "__main__":
    unittest.main()
