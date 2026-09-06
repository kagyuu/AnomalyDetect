"""U002-T1 の単体テスト: discovery。"""

import io
import os
import tempfile
import unittest

from s_anomaly import discovery, progress as progress_mod


def make_progress():
    return progress_mod.setup(io.StringIO(), io.StringIO())


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("x")


class TestClassify(unittest.TestCase):
    def test_dbconnection(self):
        got = discovery.classify("DBConnection_20260601.csv")
        self.assertEqual(got["kind"], discovery.KIND_DBCONN)
        self.assertEqual(got["date"], "20260601")

    def test_jstat(self):
        got = discovery.classify("app01_gc_host01_20260601.txt")
        self.assertEqual(got["kind"], discovery.KIND_JVMGC)
        self.assertEqual(got["container"], "app01")
        self.assertEqual(got["host"], "host01")
        self.assertEqual(got["date"], "20260601")

    def test_bqueues(self):
        got = discovery.classify("bqueues_lsfhost01_20260601.txt")
        self.assertEqual(got["kind"], discovery.KIND_LSF)
        self.assertEqual(got["host"], "lsfhost01")

    def test_container_with_underscores(self):
        got = discovery.classify("my_app_01_gc_host1_20260601.txt")
        self.assertEqual(got["container"], "my_app_01")
        self.assertEqual(got["host"], "host1")

    def test_container_containing_gc_marker(self):
        # rsplit("_gc_", 1) により最後の _gc_ で分割される
        got = discovery.classify("a_gc_b_gc_host1_20260601.txt")
        self.assertEqual(got["container"], "a_gc_b")
        self.assertEqual(got["host"], "host1")

    def test_rejects(self):
        for name in (
            "readme.txt",
            "DBConnection.csv",
            "DBConnection_2026060.csv",
            "app01_gc_host_2026ab01.txt",
            "app01_host_20260601.txt",   # _gc_ を含まない
            "notes.md",
        ):
            self.assertIsNone(discovery.classify(name), name)

    def test_bqueues_not_misread_as_jstat(self):
        got = discovery.classify("bqueues_host_20260601.txt")
        self.assertEqual(got["kind"], discovery.KIND_LSF)


class TestDiscover(unittest.TestCase):
    def test_recursive_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(os.path.join(tmp, "a", "DBConnection_20260601.csv"))
            touch(os.path.join(tmp, "a", "DBConnection_20260602.csv"))
            touch(os.path.join(tmp, "a", "sub", "app01_gc_h1_20260601.txt"))
            touch(os.path.join(tmp, "b", "app02_gc_h2_20260601.txt"))
            touch(os.path.join(tmp, "b", "bqueues_g1_20260601.txt"))
            touch(os.path.join(tmp, "b", "bqueues_g2_20260601.txt"))
            touch(os.path.join(tmp, "b", "readme.txt"))
            touch(os.path.join(tmp, "b", "notes.md"))
            touch(os.path.join(tmp, "other.log"))
            got = discovery.discover(tmp, make_progress())
        self.assertEqual(len(got), 6)
        kinds = {}
        for f in got:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        self.assertEqual(kinds[discovery.KIND_DBCONN], 2)
        self.assertEqual(kinds[discovery.KIND_JVMGC], 2)
        self.assertEqual(kinds[discovery.KIND_LSF], 2)

    def test_stable_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                touch(os.path.join(tmp, "d{0}".format(i),
                                   "DBConnection_2026060{0}.csv".format(i + 1)))
            first = [f.path for f in discovery.discover(tmp, make_progress())]
            second = [f.path for f in discovery.discover(tmp, make_progress())]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first, key=lambda p: os.path.basename(p)))

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discovery.discover(tmp, make_progress()), [])

    def test_symlink_loop_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = os.path.join(tmp, "inner")
            os.makedirs(inner)
            touch(os.path.join(inner, "DBConnection_20260601.csv"))
            link = os.path.join(inner, "loop")
            try:
                os.symlink(tmp, link, target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError) as exc:
                self.skipTest("シンボリックリンクを作成できません: {0}".format(exc))
            got = discovery.discover(tmp, make_progress())
        self.assertGreaterEqual(len(got), 1)

    def test_progress_reports_counts(self):
        out = io.StringIO()
        p = progress_mod.setup(out, io.StringIO())
        with tempfile.TemporaryDirectory() as tmp:
            touch(os.path.join(tmp, "DBConnection_20260601.csv"))
            touch(os.path.join(tmp, "readme.txt"))
            discovery.discover(tmp, p)
        text = out.getvalue()
        self.assertIn("[S3]", text)
        self.assertIn("対象外", text)


if __name__ == "__main__":
    unittest.main()
