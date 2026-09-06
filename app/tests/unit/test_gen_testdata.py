"""U003 の単体テスト: テストデータ生成ツール。"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

from s_anomaly import bootstrap, discovery, progress as progress_mod, schema
from s_anomaly.loaders import bqueues, dbconn, jstat

# app/tests/unit/ -> app/
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(APP_DIR, "tools", "gen_testdata.py")

sys.path.insert(0, os.path.join(APP_DIR, "tools"))
import gen_testdata  # noqa: E402

SERIES_ID_RE = re.compile(
    r"^(db_connection/[^/:]+:\d+/.+|jvm_gc/[^@]+@.+|lsf_queue/[^/]+/.+)$"
)
ALGORITHM_IDS = ["ALG-A{0}".format(i) for i in range(1, 6)] + \
                ["ALG-B{0}".format(i) for i in range(1, 6)]
SEVERITIES = ("INFO", "WARN", "FATAL", "SEVERE")


def generate(dest, broken=False):
    args = [dest] + (["--broken"] if broken else [])
    return gen_testdata.main(args)


def digest_dir(path):
    out = {}
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            full = os.path.join(root, name)
            with open(full, "rb") as handle:
                out[os.path.relpath(full, path)] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    return out


class DbHelper(object):
    def __init__(self):
        self.progress = progress_mod.setup(io.StringIO(), io.StringIO())
        self._tmp = tempfile.TemporaryDirectory()
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "512MB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        schema.create_all(self.con)

    def close(self):
        self.con.close()
        self._tmp.cleanup()

    def load_all(self, logs_dir):
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
        }
        results = {}
        for logfile in discovery.discover(logs_dir, self.progress):
            results[os.path.basename(logfile.path)] = loader[logfile.kind](
                self.con, logfile, self.progress
            )
        return results


class TestNormalGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dest = os.path.join(cls._tmp.name, "normal")
        cls.code = generate(cls.dest)
        cls.logs = os.path.join(cls.dest, "logs")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_exit_zero(self):
        self.assertEqual(self.code, 0)

    def test_file_count(self):
        names = sorted(os.listdir(self.logs))
        # ① 14 + ② 28 (2 組 x 14 日) + ③ 14 = 56
        self.assertEqual(len(names), 56, names[:5])
        self.assertEqual(len([n for n in names if n.startswith("DBConnection_")]), 14)
        self.assertEqual(len([n for n in names if "_gc_" in n]), 28)
        self.assertEqual(len([n for n in names if n.startswith("bqueues_")]), 14)

    def test_loadable_without_errors(self):
        helper = DbHelper()
        self.addCleanup(helper.close)
        results = helper.load_all(self.logs)
        self.assertEqual(len(results), 56)
        for name, result in results.items():
            self.assertEqual(result.errors, {}, name)
            self.assertGreater(result.ok_rows, 0, name)

    def test_both_gc_formats_present(self):
        helper = DbHelper()
        self.addCleanup(helper.close)
        helper.load_all(self.logs)
        formats = [r[0] for r in helper.con.execute(
            "SELECT DISTINCT gc_format FROM jvm_gc ORDER BY gc_format"
        ).fetchall()]
        self.assertEqual(formats, ["gc", "gcutil"])

    def test_three_tables_populated(self):
        helper = DbHelper()
        self.addCleanup(helper.close)
        helper.load_all(self.logs)
        for table in ("db_connection", "jvm_gc", "lsf_queue"):
            count = helper.con.execute(
                "SELECT count(*) FROM {0}".format(table)
            ).fetchone()[0]
            self.assertGreater(count, 0, table)

    def test_period_fixed(self):
        helper = DbHelper()
        self.addCleanup(helper.close)
        helper.load_all(self.logs)
        lo, hi = helper.con.execute(
            "SELECT min(ts), max(ts) FROM db_connection"
        ).fetchone()
        self.assertEqual(lo, datetime(2026, 6, 1, 0, 0, 0))
        self.assertEqual(hi, datetime(2026, 6, 14, 23, 55, 0))

    def test_output_is_lf_and_no_bom(self):
        for name in sorted(os.listdir(self.logs))[:6]:
            with open(os.path.join(self.logs, name), "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"\r\n", raw, name)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), name)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            dest2 = os.path.join(other, "normal")
            generate(dest2)
            self.assertEqual(digest_dir(self.dest), digest_dir(dest2))


class TestExpectedJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dest = os.path.join(cls._tmp.name, "normal")
        generate(cls.dest)
        with open(os.path.join(cls.dest, "expected.json"), "r", encoding="utf-8") as h:
            cls.expected = json.load(h)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_structure(self):
        for key in ("generated_at", "seed", "period", "injected", "clean_series"):
            self.assertIn(key, self.expected)

    def test_injected_count(self):
        self.assertEqual(len(self.expected["injected"]), 7)

    def test_clean_series_present(self):
        self.assertGreaterEqual(len(self.expected["clean_series"]), 1)
        for sid in self.expected["clean_series"]:
            self.assertRegex(sid, SERIES_ID_RE)

    def test_series_id_format(self):
        for item in self.expected["injected"]:
            self.assertRegex(item["series_id"], SERIES_ID_RE, item["id"])

    def test_expect_algorithms_valid(self):
        for item in self.expected["injected"]:
            self.assertGreater(len(item["expect_algorithms"]), 0, item["id"])
            for alg in item["expect_algorithms"]:
                self.assertIn(alg, ALGORITHM_IDS, item["id"])

    def test_expect_min_severity_valid(self):
        for item in self.expected["injected"]:
            self.assertIn(item["expect_min_severity"], SEVERITIES, item["id"])

    def test_fixed_generated_at_and_period(self):
        self.assertEqual(self.expected["generated_at"], "2026-06-01T00:00:00")
        self.assertEqual(self.expected["period"]["from"], "2026-06-01T00:00:00")
        self.assertEqual(self.expected["period"]["to"], "2026-06-14T23:55:00")
        self.assertEqual(self.expected["seed"], 20260601)

    def test_ids_unique_and_sorted(self):
        ids = [i["id"] for i in self.expected["injected"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))


class TestInjectedShapes(unittest.TestCase):
    """埋め込んだ異常が、実際に意図した形状になっていることを確認する。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dest = os.path.join(cls._tmp.name, "normal")
        generate(cls.dest)
        cls.helper = DbHelper()
        cls.helper.load_all(os.path.join(cls.dest, "logs"))

    @classmethod
    def tearDownClass(cls):
        cls.helper.close()
        cls._tmp.cleanup()

    def test_inj001_spike_is_large(self):
        rows = self.helper.con.execute(
            "SELECT ts, active_connections FROM db_connection "
            "WHERE host='host01' AND datasource='OraclePool_1' ORDER BY ts"
        ).fetchall()
        values = [r[1] for r in rows]
        at = datetime(2026, 6, 5, 14, 0, 0)
        index = [i for i, r in enumerate(rows) if r[0] == at][0]
        others = [v for i, v in enumerate(values) if i != index]
        mean = sum(others) / len(others)
        self.assertGreater(values[index], mean * 5.0)

    def test_inj004_rolling_min_increases(self):
        rows = self.helper.con.execute(
            "SELECT time_bucket(INTERVAL '6 hours', ts) AS b, min(ou) AS m "
            "FROM jvm_gc WHERE container='app01' GROUP BY 1 ORDER BY 1"
        ).fetchall()
        mins = [r[1] for r in rows]
        self.assertGreaterEqual(len(mins), 40)
        # 単調非減少であること(小さな揺れは許容しない設計にしてある)
        drops = sum(1 for a, b in zip(mins, mins[1:]) if b < a)
        self.assertLessEqual(drops, 2, mins[:10])
        self.assertGreater(mins[-1] - mins[0], 40.0)

    def test_inj006_pend_increases_run_flat(self):
        rows = self.helper.con.execute(
            "SELECT ts, pend, run FROM lsf_queue "
            "WHERE queue='long' ORDER BY ts"
        ).fetchall()
        pend = [r[1] for r in rows]
        run = [r[2] for r in rows]
        self.assertGreater(pend[-1] - pend[0], 150)
        first_run = sum(run[:100]) / 100.0
        last_run = sum(run[-100:]) / 100.0
        self.assertLess(abs(last_run - first_run), 10.0)

    def test_clean_series_has_no_extremes(self):
        rows = self.helper.con.execute(
            "SELECT pend FROM lsf_queue WHERE queue='short'"
        ).fetchall()
        values = [r[0] for r in rows]
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        sd = var ** 0.5
        limit = mean + 5.0 * max(sd, 0.5)
        self.assertLessEqual(max(values), limit)


class TestBrokenGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dest = os.path.join(cls._tmp.name, "broken")
        generate(cls.dest, broken=True)
        cls.logs = os.path.join(cls.dest, "logs")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_files_present(self):
        names = set(os.listdir(self.logs))
        self.assertEqual(names, set(gen_testdata.BROKEN_EXPECTATIONS))

    def test_no_expected_json(self):
        self.assertFalse(os.path.exists(os.path.join(self.dest, "expected.json")))

    def test_matches_expectations(self):
        helper = DbHelper()
        self.addCleanup(helper.close)
        results = helper.load_all(self.logs)
        for name, want in gen_testdata.BROKEN_EXPECTATIONS.items():
            self.assertIn(name, results, name)
            got = results[name]
            self.assertEqual(got.ok_rows, want["ok_rows"], name)
            self.assertEqual(got.errors, want["errors"], name)

    def test_bom_and_crlf_fixtures_are_real(self):
        with open(os.path.join(self.logs, "DBConnection_20260604.csv"), "rb") as h:
            self.assertTrue(h.read(3) == b"\xef\xbb\xbf")
        with open(os.path.join(self.logs, "DBConnection_20260605.csv"), "rb") as h:
            self.assertIn(b"\r\n", h.read())

    def test_reason_codes_within_four(self):
        from s_anomaly import loaders

        for want in gen_testdata.BROKEN_EXPECTATIONS.values():
            for reason in want["errors"]:
                self.assertIn(reason, loaders.ALL_REASONS)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            dest2 = os.path.join(other, "broken")
            generate(dest2, broken=True)
            self.assertEqual(digest_dir(self.dest), digest_dir(dest2))


class TestToolCli(unittest.TestCase):
    def test_missing_argument(self):
        proc = subprocess.run(
            [sys.executable, TOOL], capture_output=True
        )
        self.assertEqual(proc.returncode, 1)

    def test_output_path_is_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")
            with open(blocker, "w", encoding="utf-8") as h:
                h.write("x")
            self.assertEqual(gen_testdata.main([blocker]), 1)

    def test_creates_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "a", "b", "c")
            self.assertEqual(gen_testdata.main([dest, "--broken"]), 0)
            self.assertTrue(os.path.isdir(os.path.join(dest, "logs")))


if __name__ == "__main__":
    unittest.main()
