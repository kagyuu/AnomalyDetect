"""U002-T2/T3/T4/T5 の単体テスト: ローダ共通基盤と 3 形式の解析、重複排除。"""

import io
import os
import tempfile
import unittest
from datetime import datetime

from s_anomaly import bootstrap, discovery, loaders, progress as progress_mod, schema
from s_anomaly.loaders import bqueues, dbconn, jstat

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures"
)


def make_progress():
    out, err = io.StringIO(), io.StringIO()
    return progress_mod.setup(out, err), out, err


class DbFixture(unittest.TestCase):
    """DuckDB 接続を用意する共通の基底。"""

    def setUp(self):
        self.progress, self.out, self.err = make_progress()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "512MB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        self.addCleanup(self.con.close)
        schema.create_all(self.con)

    def logfile(self, name):
        path = os.path.join(FIXTURES, name)
        meta = discovery.classify(name)
        self.assertIsNotNone(meta, name)
        return discovery.LogFile(
            path=path, kind=meta["kind"], container=meta.get("container"),
            host=meta.get("host"), date=meta.get("date"),
        )

    def count(self, table):
        return self.con.execute("SELECT count(*) FROM {0}".format(table)).fetchone()[0]


class TestCommonHelpers(unittest.TestCase):
    def test_parse_number(self):
        self.assertEqual(loaders.parse_number("12.5"), (True, 12.5))
        self.assertEqual(loaders.parse_number(""), (True, None))
        self.assertEqual(loaders.parse_number("-"), (True, None))
        self.assertEqual(loaders.parse_number("  "), (True, None))
        self.assertEqual(loaders.parse_number("abc"), (False, None))

    def test_parse_int(self):
        self.assertEqual(loaders.parse_int("7"), (True, 7))
        self.assertEqual(loaders.parse_int("7.0"), (True, 7))
        self.assertEqual(loaders.parse_int("-"), (True, None))
        self.assertEqual(loaders.parse_int("x"), (False, None))

    def test_parse_ts(self):
        got = loaders.parse_ts("2026/06/01 00:00:01", "%Y/%m/%d %H:%M:%S")
        self.assertEqual(got, datetime(2026, 6, 1, 0, 0, 1))
        self.assertIsNone(loaders.parse_ts("2026-06-01", "%Y/%m/%d %H:%M:%S"))

    def test_open_text_variants(self):
        base = loaders.open_text(os.path.join(FIXTURES, "enc_utf8_lf.csv"))
        self.assertIsNotNone(base)
        self.assertFalse(base.startswith("﻿"))
        for name in ("enc_utf8bom_lf.csv", "enc_utf8_crlf.csv"):
            got = loaders.open_text(os.path.join(FIXTURES, name))
            self.assertEqual(got, base, name)
            self.assertFalse(got.startswith("﻿"), name)

    def test_open_text_cp932_fallback(self):
        got = loaders.open_text(os.path.join(FIXTURES, "enc_cp932_lf.csv"))
        self.assertIsNotNone(got)
        self.assertIn("ホスト名", got)

    def test_open_text_undecodable_returns_none(self):
        got = loaders.open_text(os.path.join(FIXTURES, "enc_undecodable.bin"))
        self.assertIsNone(got)

    def test_reason_codes_are_exactly_four(self):
        names = [n for n in dir(loaders) if n.startswith("REASON_")]
        self.assertEqual(len(names), 4, names)
        self.assertEqual(len(loaders.ALL_REASONS), 4)


class TestBatchInserter(DbFixture):
    def test_crosses_batch_boundary(self):
        inserter = loaders.BatchInserter(self.con, "db_connection", batch_size=1000)
        for i in range(2500):
            inserter.add({
                "ts": datetime(2026, 6, 1), "host": "h", "port": 1,
                "datasource": "d{0}".format(i), "active_connections": i,
            })
        inserter.flush()
        self.assertEqual(self.count("db_connection"), 2500)

    def test_load_seq_monotonic(self):
        a = loaders.next_load_seq()
        b = loaders.next_load_seq()
        self.assertLess(a, b)


class TestDbconnLoader(DbFixture):
    def test_normal(self):
        result = dbconn.load(self.con, self.logfile("DBConnection_20260601.csv"), self.progress)
        self.assertEqual(result.ok_rows, 2)
        self.assertEqual(result.errors, {})
        rows = self.con.execute(
            "SELECT host, port, datasource, active_connections FROM db_connection ORDER BY datasource"
        ).fetchall()
        self.assertEqual(rows[0], ("host01", 7003, "OraclePool_1", 4))

    def test_broken_rows(self):
        result = dbconn.load(self.con, self.logfile("DBConnection_20260602.csv"), self.progress)
        self.assertEqual(result.errors.get(loaders.REASON_COLUMNS), 1)
        self.assertEqual(result.errors.get(loaders.REASON_DATE), 1)
        self.assertEqual(result.errors.get(loaders.REASON_NUMBER), 1)
        # 正常 1 行 + NULL 行 1 行 = 2 行が格納される
        self.assertEqual(result.ok_rows, 2)
        self.assertEqual(self.count("db_connection"), 2)

    def test_null_value_stored(self):
        dbconn.load(self.con, self.logfile("DBConnection_20260602.csv"), self.progress)
        nulls = self.con.execute(
            "SELECT count(*) FROM db_connection WHERE active_connections IS NULL"
        ).fetchone()[0]
        self.assertEqual(nulls, 1)

    def test_empty_file(self):
        result = dbconn.load(self.con, self.logfile("DBConnection_20260603.csv"), self.progress)
        self.assertEqual(result.ok_rows, 0)
        self.assertEqual(result.errors, {})

    def test_header_only(self):
        result = dbconn.load(self.con, self.logfile("DBConnection_20260604.csv"), self.progress)
        self.assertEqual(result.ok_rows, 0)
        self.assertEqual(result.errors, {})


class TestJstatLoader(DbFixture):
    def test_gcutil(self):
        result = jstat.load(self.con, self.logfile("app01_gc_host1_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 2, result.errors)
        row = self.con.execute(
            "SELECT gc_format, ou, oc, jvm_uptime_sec, container, host FROM jvm_gc ORDER BY ts"
        ).fetchone()
        self.assertEqual(row[0], "gcutil")
        self.assertAlmostEqual(row[1], 53.52)
        self.assertIsNone(row[2])
        self.assertAlmostEqual(row[3], 3019.0)
        self.assertEqual(row[4], "app01")
        self.assertEqual(row[5], "host1")

    def test_gc_18(self):
        result = jstat.load(self.con, self.logfile("app02_gc_host2_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 2, result.errors)
        row = self.con.execute(
            "SELECT gc_format, ou, oc, ccsu FROM jvm_gc ORDER BY ts"
        ).fetchone()
        self.assertEqual(row[0], "gc")
        self.assertAlmostEqual(row[1], 10240.0)
        self.assertAlmostEqual(row[2], 20480.0)
        self.assertAlmostEqual(row[3], 400.0)

    def test_gc_17_has_null_ccsu(self):
        result = jstat.load(self.con, self.logfile("app03_gc_host3_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 1, result.errors)
        row = self.con.execute("SELECT gc_format, ccsc, ccsu FROM jvm_gc").fetchone()
        self.assertEqual(row[0], "gc")
        self.assertAlmostEqual(row[1], 512.0)
        self.assertIsNone(row[2])

    def test_real_sample_shape_header_data_mismatch(self):
        """P000 の実サンプル(見出しは -gc 17列、データは -gcutil 12個)。

        本タスクの中心的な確認項目。列数不一致で 0 行になってはならない。
        """
        result = jstat.load(self.con, self.logfile("real01_gc_host9_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 2, result.errors)
        self.assertEqual(result.errors, {})
        row = self.con.execute("SELECT gc_format, ou, oc FROM jvm_gc ORDER BY ts").fetchone()
        self.assertEqual(row[0], "gcutil")
        self.assertAlmostEqual(row[1], 53.52)
        self.assertIsNone(row[2])

    def test_undetectable(self):
        result = jstat.load(self.con, self.logfile("bad01_gc_host1_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 0)
        self.assertEqual(result.errors.get(loaders.REASON_HEADER), 1)

    def test_detect_format_priority(self):
        header_gc = "time Timestamp S0C S1C S0U S1U EC EU OC OU MC MU CCSC YGC YGCT FGC FGCT GCT".split()
        # 見出しは -gc だがデータは 12 個 -> データを優先して gcutil
        self.assertEqual(jstat.detect_format(header_gc, 12), jstat.FORMAT_GCUTIL)
        self.assertEqual(jstat.detect_format(header_gc, 18), jstat.FORMAT_GC)
        self.assertEqual(jstat.detect_format(header_gc, None), jstat.FORMAT_GC)
        self.assertIsNone(jstat.detect_format("a b c".split(), 5))


class TestBqueuesLoader(DbFixture):
    def test_wide_to_long(self):
        result = bqueues.load(self.con, self.logfile("bqueues_grid01_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 3, result.errors)
        # 3 データ行 x 2 QUEUE = 6 レコード
        self.assertEqual(self.count("lsf_queue"), 6)
        queues = [r[0] for r in self.con.execute(
            "SELECT DISTINCT queue FROM lsf_queue ORDER BY queue"
        ).fetchall()]
        self.assertEqual(queues, ["long", "normal"])

    def test_centisecond_truncated(self):
        bqueues.load(self.con, self.logfile("bqueues_grid01_20260601.txt"), self.progress)
        first = self.con.execute("SELECT min(ts) FROM lsf_queue").fetchone()[0]
        self.assertEqual(first, datetime(2026, 6, 1, 0, 0, 0))

    def test_slash_separator_accepted(self):
        bqueues.load(self.con, self.logfile("bqueues_grid01_20260601.txt"), self.progress)
        last = self.con.execute("SELECT max(ts) FROM lsf_queue").fetchone()[0]
        self.assertEqual(last, datetime(2026, 6, 1, 0, 10, 0))

    def test_normalize_ts_token(self):
        self.assertEqual(
            bqueues.normalize_ts_token("2026-06-01 00:00:00:02"), "2026-06-01 00:00:00"
        )
        self.assertEqual(
            bqueues.normalize_ts_token("2026/06/01 00:00:00"), "2026-06-01 00:00:00"
        )
        self.assertEqual(
            bqueues.normalize_ts_token("2026-06-01 00:00:00"), "2026-06-01 00:00:00"
        )
        self.assertIsNone(bqueues.normalize_ts_token("20260601 000000"))

    def test_bad_date(self):
        result = bqueues.load(self.con, self.logfile("bqueues_grid02_20260601.txt"), self.progress)
        self.assertEqual(result.errors.get(loaders.REASON_DATE), 1)
        self.assertEqual(result.ok_rows, 2)

    def test_header_without_fields(self):
        result = bqueues.load(self.con, self.logfile("bqueues_grid03_20260601.txt"), self.progress)
        self.assertEqual(result.ok_rows, 0)
        self.assertEqual(result.errors.get(loaders.REASON_HEADER), 1)


class TestRecordErrors(DbFixture):
    def test_records_only_known_reasons(self):
        loaders.record_errors(self.con, "a.csv", {
            loaders.REASON_COLUMNS: 2, loaders.REASON_DATE: 1,
        })
        rows = self.con.execute(
            "SELECT reason, line_count FROM load_error ORDER BY reason"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(dict(rows)[loaders.REASON_COLUMNS], 2)


class TestDedupe(DbFixture):
    def _insert(self, datasource, value):
        loaders.BatchInserter(self.con, "db_connection")
        inserter = loaders.BatchInserter(self.con, "db_connection")
        inserter.add({
            "ts": datetime(2026, 6, 1), "host": "h", "port": 7003,
            "datasource": datasource, "active_connections": value,
        })
        inserter.flush()

    def test_first_wins(self):
        self._insert("ds", 1)
        self._insert("ds", 2)
        self.assertEqual(self.count("db_connection"), 2)
        dropped = loaders.dedupe_all(self.con)
        self.assertEqual(dropped["db_connection"], 1)
        got = self.con.execute("SELECT active_connections FROM db_connection").fetchone()
        self.assertEqual(got[0], 1)

    def test_distinct_keys_kept(self):
        self._insert("ds1", 1)
        self._insert("ds2", 2)
        loaders.dedupe_all(self.con)
        self.assertEqual(self.count("db_connection"), 2)

    def test_idempotent(self):
        self._insert("ds", 1)
        self._insert("ds", 2)
        loaders.dedupe_all(self.con)
        first = self.count("db_connection")
        loaders.dedupe_all(self.con)
        self.assertEqual(self.count("db_connection"), first)

    def test_all_three_tables(self):
        for logfile in ("DBConnection_20260601.csv", "app01_gc_host1_20260601.txt",
                        "bqueues_grid01_20260601.txt"):
            meta = self.logfile(logfile)
            if meta.kind == discovery.KIND_DBCONN:
                dbconn.load(self.con, meta, self.progress)
                dbconn.load(self.con, meta, self.progress)
            elif meta.kind == discovery.KIND_JVMGC:
                jstat.load(self.con, meta, self.progress)
                jstat.load(self.con, meta, self.progress)
            else:
                bqueues.load(self.con, meta, self.progress)
                bqueues.load(self.con, meta, self.progress)
        dropped = loaders.dedupe_all(self.con)
        self.assertGreater(dropped["db_connection"], 0)
        self.assertGreater(dropped["jvm_gc"], 0)
        self.assertGreater(dropped["lsf_queue"], 0)
        self.assertEqual(self.count("db_connection"), 2)
        self.assertEqual(self.count("jvm_gc"), 2)
        self.assertEqual(self.count("lsf_queue"), 6)


if __name__ == "__main__":
    unittest.main()
