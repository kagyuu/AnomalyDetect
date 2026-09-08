"""U010 の単体テスト: sar (sysstat) の解析と各所への登録 (※CR-010)。

**このテストの中心は「登録漏れを捕まえること」である。**

`metrics.DETECTABLE_METRICS` はハードコードの許可リストであり、sar の
メトリクスをここに入れ忘れると、**取り込みも `metrics` 構築も成功したまま、
検知だけが 1 件も起きずに正常終了する**(P003 DS-07-07b)。同じ型の欠陥は
CR-002・CR-009 で既に 2 回起きている(`docs/CLAUDE.md`)。

したがって、**「例外が出ないこと」ではなく、対応表どうしが一致していること**と
**実際に系列が検知対象として現れること**を検証する。
"""

import io
import os
import tempfile
import unittest

from s_anomaly import (
    bootstrap, config as config_mod, discovery, loaders, metrics,
    progress as progress_mod, reporter, schema,
)
from s_anomaly.detectors import c1_saturation
from s_anomaly.loaders import sar

HEADERS = {
    "cpu": "# hostname;interval;timestamp;CPU;%user;%nice;%system;%iowait;%steal;%idle",
    "memory": ("# hostname;interval;timestamp;kbmemfree;kbavail;kbmemused;"
               "%memused;kbbuffers;kbcached;kbcommit;%commit"),
    "swap_space": "# hostname;interval;timestamp;kbswpfree;kbswpused;%swpused;kbswpcad;%swpcad",
    "io": "# hostname;interval;timestamp;tps;rtps;wtps;bread/s;bwrtn/s",
    "load": "# hostname;interval;timestamp;runq-sz;plist-sz;ldavg-1;ldavg-5;ldavg-15;blocked",
    "swapping": "# hostname;interval;timestamp;pswpin/s;pswpout/s",
    "disk": ("# hostname;interval;timestamp;DEV;tps;rd_sec/s;wr_sec/s;"
             "avgrq-sz;avgqu-sz;await;svctm;%util"),
    "network": ("# hostname;interval;timestamp;IFACE;rxpck/s;txpck/s;rxkB/s;"
                "txkB/s;rxcmp/s;txcmp/s;rxmcst/s;%ifutil"),
    # ※CR-011
    "nfs": "# hostname;interval;timestamp;call/s;retrans/s;read/s;write/s;access/s;getatt/s",
    "nfsd": ("# hostname;interval;timestamp;scall/s;badcall/s;packet/s;udp/s;"
             "tcp/s;hit/s;miss/s;sread/s;swrite/s;saccess/s;sgetatt/s"),
}


def make_progress():
    out, err = io.StringIO(), io.StringIO()
    return progress_mod.setup(out, err), out, err


class TestNormalize(unittest.TestCase):
    """列名の正規化 (P001 FR-143 / DS-SAR-04)。"""

    def test_percent_becomes_prefix(self):
        self.assertEqual(sar.normalize("%user"), "pct_user")
        self.assertEqual(sar.normalize("%memused"), "pct_memused")
        self.assertEqual(sar.normalize("%ifutil"), "pct_ifutil")

    def test_slash_and_hyphen_become_underscore(self):
        self.assertEqual(sar.normalize("rd_sec/s"), "rd_sec_s")
        self.assertEqual(sar.normalize("ldavg-1"), "ldavg_1")
        self.assertEqual(sar.normalize("runq-sz"), "runq_sz")

    def test_case_is_folded(self):
        # `rxkB/s` は版によって大文字小文字が違う。小文字化で吸収する。
        self.assertEqual(sar.normalize("rxkB/s"), "rxkb_s")

    def test_prefix_and_suffix_are_different(self):
        """**接頭 `pct_` と接尾 `_pct` を混同しない** (ADR-005 との関係)。"""
        for name in sar.ALL_METRICS:
            self.assertFalse(
                name.endswith("_pct"),
                "sar のメトリクスに接尾 `_pct` を使ってはならない: " + name)


class TestClassifyHeader(unittest.TestCase):
    """活動種別の判別 (DS-SAR-02)。"""

    def test_all_activities_are_recognised(self):
        """※CR-011 で 8 → 10 活動になった。"""
        seen = {}
        for activity, header in HEADERS.items():
            got, device_col, index = sar.classify_header(header[1:].split(";"))
            self.assertEqual(got, activity, header)
            self.assertIsNotNone(index)
            seen[got] = device_col
        self.assertEqual(len(seen), 10)
        self.assertEqual(seen["cpu"], "CPU")
        self.assertEqual(seen["disk"], "DEV")
        self.assertEqual(seen["network"], "IFACE")
        for activity in ("memory", "swap_space", "io", "load", "swapping",
                         "nfs", "nfsd"):
            self.assertIsNone(seen[activity])

    def test_nfs_and_nfsd_are_not_confused(self):
        """★`read/s` と `sread/s` を取り違えない★ (DS-SAR-02a)。

        `-n NFS` と `-n NFSD` は**接頭の `s` があるかどうかだけ**が違う列を
        いくつも持つ。判別列を部分一致に変えると取り違える。
        """
        nfs, _, nfs_index = sar.classify_header(
            HEADERS["nfs"][1:].split(";"))
        nfsd, _, nfsd_index = sar.classify_header(
            HEADERS["nfsd"][1:].split(";"))
        self.assertEqual(nfs, "nfs")
        self.assertEqual(nfsd, "nfsd")
        # 判別に使う列は、片方にしか現れない
        self.assertIn("retrans_s", nfs_index)
        self.assertNotIn("retrans_s", nfsd_index)
        self.assertIn("scall_s", nfsd_index)
        self.assertNotIn("scall_s", nfs_index)
        # 紛らわしい対
        self.assertIn("read_s", nfs_index)
        self.assertNotIn("read_s", nfsd_index)
        self.assertIn("sread_s", nfsd_index)
        self.assertNotIn("sread_s", nfs_index)

    def test_unknown_header_is_rejected(self):
        got, _, _ = sar.classify_header(
            "hostname;interval;timestamp;INTR;intr/s".split(";"))
        self.assertIsNone(got)

    def test_header_without_body_is_rejected(self):
        got, _, _ = sar.classify_header("hostname;interval;timestamp".split(";"))
        self.assertIsNone(got)

    def test_index_is_absolute_position(self):
        _, _, index = sar.classify_header(HEADERS["cpu"][1:].split(";"))
        # 固定 3 列のあとに CPU、その次が %user
        self.assertEqual(index["pct_user"], 4)


class TestParseTimestamp(unittest.TestCase):
    def test_with_timezone(self):
        ts, tz = sar.parse_timestamp("2026-06-01 00:10:00 UTC")
        self.assertEqual(ts.hour, 0)
        self.assertEqual(tz, "UTC")

    def test_without_timezone_is_normal(self):
        """**表記が無いのは正常である** (版によって付かない。DS-SAR-05)。"""
        ts, tz = sar.parse_timestamp("2026-06-01 00:10:00")
        self.assertIsNotNone(ts)
        self.assertIsNone(tz)

    def test_broken(self):
        self.assertEqual(sar.parse_timestamp("2026/06/01 00:10:00"), (None, None))


class SarDbCase(unittest.TestCase):
    """DuckDB へ実際に投入して確かめる基底。"""

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
        loaders.reset_load_seq()

    def write(self, name, lines):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        meta = discovery.classify(name)
        self.assertIsNotNone(meta, name)
        return discovery.LogFile(
            path=path, kind=meta["kind"], host=meta.get("host"),
            date=meta.get("date"))

    def rows(self):
        return self.con.execute(
            "SELECT activity, device, metric, value FROM sar "
            "ORDER BY activity, device, metric").fetchall()


def sample_lines(ts="2026-06-01 00:00:00 UTC", host="host01"):
    p = "{0};300;{1};".format(host, ts)
    return [
        HEADERS["cpu"], p + "-1;12.00;0.00;4.00;1.50;0.00;82.50",
        HEADERS["memory"], p + "1024000;1100000;2000000;52.00;204800;1048576;900000;40.00",
        HEADERS["swap_space"], p + "8000000;100000;2.00;0;0.00",
        HEADERS["io"], p + "18.00;10.00;8.00;400.00;300.00",
        HEADERS["load"], p + "2;180;1.40;1.20;1.10;0",
        HEADERS["swapping"], p + "0.20;0.10",
        HEADERS["disk"],
        p + "sda;14.00;448.00;252.00;46.83;0.09;5.00;0.61;18.00",
        p + "sdb;9.00;288.00;162.00;46.83;0.09;4.00;0.61;11.00",
        HEADERS["network"],
        p + "eth0;600.00;380.00;260.00;180.00;0.00;0.00;0.00;9.00",
        p + "eth1;300.00;190.00;130.00;90.00;0.00;0.00;0.00;4.50",
        # ※CR-011
        HEADERS["nfs"], p + "180.00;0.05;63.00;36.00;32.40;39.60",
        HEADERS["nfsd"],
        p + "320.00;0.02;336.00;6.40;313.60;281.60;38.40;105.60;60.80;54.40;67.20",
    ]


class TestLoad(SarDbCase):
    def test_all_activities_and_devices_are_stored(self):
        result = sar.load(self.con, self.write("sa-host01-20260601.csv",
                                               sample_lines()), self.progress)
        self.assertEqual(result.skipped_rows, 0, self.err.getvalue())
        activities = {r[0] for r in self.rows()}
        self.assertEqual(len(activities), 10, activities)   # ※CR-011
        devices = {r[1] for r in self.rows() if r[0] == "disk"}
        self.assertEqual(devices, {"sda", "sdb"})
        ifaces = {r[1] for r in self.rows() if r[0] == "network"}
        self.assertEqual(ifaces, {"eth0", "eth1"})

    def test_metrics_match_the_wanted_table(self):
        sar.load(self.con, self.write("sa-host01-20260601.csv", sample_lines()),
                 self.progress)
        stored = {r[2] for r in self.rows()}
        self.assertEqual(stored, set(sar.ALL_METRICS))

    def test_device_placeholder_for_activities_without_device(self):
        sar.load(self.con, self.write("sa-host01-20260601.csv", sample_lines()),
                 self.progress)
        for activity in ("memory", "swap_space", "io", "load", "swapping",
                         "nfs", "nfsd"):        # ※CR-011
            devices = {r[1] for r in self.rows() if r[0] == activity}
            self.assertEqual(devices, {sar.NO_DEVICE}, activity)

    def test_cpu_device_is_the_cpu_column(self):
        """**`cpu` のデバイスは CPU 番号であり `-1` が全 CPU を意味する。**

        占位子の `-` とは別物である (P001 FR-144)。
        """
        sar.load(self.con, self.write("sa-host01-20260601.csv", sample_lines()),
                 self.progress)
        devices = {r[1] for r in self.rows() if r[0] == "cpu"}
        self.assertEqual(devices, {"-1"})

    def test_host_comes_from_the_file_name_not_the_row(self):
        """**ホスト名はファイル名を正とする** (DS-SAR-06)。食い違っても警告しない。"""
        lines = sample_lines(host="other-name")
        sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                 self.progress)
        hosts = {r[0] for r in self.con.execute(
            "SELECT DISTINCT host FROM sar").fetchall()}
        self.assertEqual(hosts, {"host01"})
        self.assertNotIn("ホスト", self.err.getvalue())

    def test_unknown_block_is_skipped_and_warned_once(self):
        lines = sample_lines()
        for _ in range(3):
            lines += ["# hostname;interval;timestamp;INTR;intr/s",
                      "host01;300;2026-06-01 00:00:00 UTC;0;12.5"]
        result = sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                          self.progress)
        self.assertEqual(result.skipped_rows, 0)
        self.assertEqual(self.err.getvalue().count("未知の活動種別"), 1,
                         "同じ見出しの警告は 1 回だけにする")
        self.assertEqual({r[0] for r in self.rows()},
                         set(sar.WANTED.keys()))

    def test_unknown_column_in_known_block_is_silently_ignored(self):
        """既知の活動の未知の列は**警告を出さない**(取り込まないのが設計)。"""
        header = HEADERS["cpu"] + ";%guest"
        lines = [header,
                 "host01;300;2026-06-01 00:00:00 UTC;-1;12.00;0.00;4.00;"
                 "1.50;0.00;82.50;0.30"]
        sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                 self.progress)
        self.assertEqual({r[2] for r in self.rows()}, set(sar.WANTED["cpu"]))
        self.assertEqual(self.err.getvalue(), "")

    def test_missing_column_from_version_difference_is_tolerated(self):
        """版差で列が減っても落ちない。**取れた列だけ入る。**"""
        header = "# hostname;interval;timestamp;DEV;tps;await"
        lines = [header, "host01;300;2026-06-01 00:00:00 UTC;sda;14.00;5.00"]
        # `%util` が無いので disk と判別できない -> ブロックごと読み飛ばす
        result = sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                          self.progress)
        self.assertEqual(result.ok_rows, 0)
        self.assertIn("未知の活動種別", self.err.getvalue())

    def test_broken_number_is_counted_not_raised(self):
        lines = [HEADERS["swapping"],
                 "host01;300;2026-06-01 00:00:00 UTC;abc;0.10"]
        result = sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                          self.progress)
        self.assertEqual(result.ok_rows, 0)
        self.assertEqual(result.errors.get(loaders.REASON_NUMBER), 1)

    def test_broken_timestamp_is_counted(self):
        lines = [HEADERS["swapping"], "host01;300;2026/06/01 00:00:00;0.2;0.1"]
        result = sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                          self.progress)
        self.assertEqual(result.errors.get(loaders.REASON_DATE), 1)

    def test_file_without_header_is_reported(self):
        lines = ["host01;300;2026-06-01 00:00:00 UTC;0.2;0.1"]
        result = sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                          self.progress)
        self.assertEqual(result.errors.get(loaders.REASON_HEADER), 1)

    def test_null_token_becomes_null(self):
        lines = [HEADERS["swapping"], "host01;300;2026-06-01 00:00:00 UTC;-;0.10"]
        sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                 self.progress)
        values = dict((r[2], r[3]) for r in self.rows())
        self.assertIsNone(values["pswpin_s"])
        self.assertEqual(values["pswpout_s"], 0.10)


class TestTimezoneNotice(SarDbCase):
    """TZ 表記の検証 (P001 FR-147 / DS-SAR-05)。"""

    def config(self, sar_tz):
        values = {}
        for param in config_mod.PARAMS:
            values[(param.section, param.key)] = param.default
        values[("timezone", "sar")] = sar_tz
        return config_mod.Config(values)

    def test_mismatch_warns_once_and_continues(self):
        lines = sample_lines() + sample_lines(ts="2026-06-01 00:05:00 UTC")
        result = sar.load(
            self.con, self.write("sa-host01-20260601.csv", lines),
            self.progress, cfg=self.config("Asia/Tokyo"))
        self.assertGreater(result.ok_rows, 0, "警告を出しても処理は続行する")
        self.assertEqual(self.err.getvalue().count("タイムゾーン表記"), 1)

    def test_match_is_silent(self):
        sar.load(self.con, self.write("sa-host01-20260601.csv", sample_lines()),
                 self.progress, cfg=self.config("UTC"))
        self.assertNotIn("タイムゾーン表記", self.err.getvalue())

    def test_absent_notation_is_silent(self):
        lines = sample_lines(ts="2026-06-01 00:00:00")
        sar.load(self.con, self.write("sa-host01-20260601.csv", lines),
                 self.progress, cfg=self.config("Asia/Tokyo"))
        self.assertNotIn("タイムゾーン表記", self.err.getvalue())


class TestActivityFilter(SarDbCase):
    """`[sar] activities` による絞り込み (P001 FR-149 / DS-SAR-07)。"""

    def config(self, activities):
        values = {}
        for param in config_mod.PARAMS:
            values[(param.section, param.key)] = param.default
        values[("sar", "activities")] = activities
        return config_mod.Config(values)

    def test_narrowing(self):
        sar.load(self.con, self.write("sa-host01-20260601.csv", sample_lines()),
                 self.progress, cfg=self.config("cpu,memory"))
        self.assertEqual({r[0] for r in self.rows()}, {"cpu", "memory"})

    def test_empty_takes_nothing(self):
        result = sar.load(
            self.con, self.write("sa-host01-20260601.csv", sample_lines()),
            self.progress, cfg=self.config(""))
        self.assertEqual(result.ok_rows, 0)
        self.assertEqual(self.rows(), [])


class TestNfsIsIngested(SarDbCase):
    """NFS / NFSD を**取り込む**(※CR-011)。

    ★このクラスは `TestNfsIsSkippedForNow` を置き換えたものである。★
    CR-010 の時点では「変換スクリプトが出しても読み飛ばす」ことを固定していた。
    **CR-011 で読み込むようになったため、期待を反転させた**
    (`docs/P007-impl-direction/U010-sar.md` の追補 6 章)。

    依頼者の指摘: 「**対象システムでは極めて重要な指標である
    (LSF 計算グリッドが NFS でファイルを共有するため)**」(2026-09-08)。
    """

    def _lines(self):
        lines = []
        for i in range(3):
            p = "host01;600;2026-06-01 00:0{0}:00 UTC;".format(i)
            lines += [HEADERS["cpu"], p + "-1;12.00;0.00;4.00;1.50;0.00;82.50"]
            lines += [HEADERS["nfs"], p + "180.00;0.05;63.00;36.00;32.40;39.60"]
            lines += [HEADERS["nfsd"],
                      p + "320.00;0.02;336.00;6.40;313.60;281.60;38.40;"
                          "105.60;60.80;54.40;67.20"]
        return lines

    def test_headers_are_recognised(self):
        for header, expected in ((HEADERS["nfs"], "nfs"),
                                 (HEADERS["nfsd"], "nfsd")):
            activity, device_col, _ = sar.classify_header(header[1:].split(";"))
            self.assertEqual(activity, expected, header)
            self.assertIsNone(device_col, "NFS はデバイス列を持たない")

    def test_metrics_are_stored(self):
        result = sar.load(self.con, self.write("sa-host01-20260601.csv",
                                               self._lines()), self.progress)
        self.assertEqual(result.ok_rows, 9, "cpu / nfs / nfsd の 3 時刻ぶん")
        got = {(r[0], r[2]) for r in self.rows()}
        for metric in sar.WANTED["nfs"]:
            self.assertIn(("nfs", metric), got)
        for metric in sar.WANTED["nfsd"]:
            self.assertIn(("nfsd", metric), got)

    def test_untaken_columns_are_ignored(self):
        """**`packet_s` / `udp_s` / `tcp_s` は取り込まない** (P001 FR-142b)。"""
        sar.load(self.con, self.write("sa-host01-20260601.csv", self._lines()),
                 self.progress)
        stored = {r[2] for r in self.rows() if r[0] == "nfsd"}
        self.assertEqual(stored, set(sar.WANTED["nfsd"]))
        for name in ("packet_s", "udp_s", "tcp_s"):
            self.assertNotIn(name, stored)

    def test_values_are_not_swapped_between_nfs_and_nfsd(self):
        """★`read_s` と `sread_s` の取り違えを値で確かめる★ (DS-SAR-02a)。"""
        sar.load(self.con, self.write("sa-host01-20260601.csv", self._lines()),
                 self.progress)
        values = {(r[0], r[2]): r[3] for r in self.rows()}
        self.assertEqual(values[("nfs", "read_s")], 63.00)
        self.assertEqual(values[("nfsd", "sread_s")], 105.60)
        self.assertEqual(values[("nfs", "call_s")], 180.00)
        self.assertEqual(values[("nfsd", "scall_s")], 320.00)

    def test_no_unknown_activity_warning(self):
        """★CR-011 の目的のひとつ★ — 警告が出なくなること。

        CR-010 の時点では 1 ファイルにつき 2 行の WARNING が出ていた。
        **読み込むようになったので出ない。**
        """
        sar.load(self.con, self.write("sa-host01-20260601.csv", self._lines()),
                 self.progress)
        self.assertNotIn("未知の活動種別", self.err.getvalue())


class TestNfsdAllZero(SarDbCase):
    """NFS サーバでないホストの `nfsd` は全て 0 になる (P001 FR-142c)。

    **実行が止まらず、誤検知も出ないこと。** 分散が 0 の系列は
    `effective_sigma` の下限 (ADR-003) により検知されない。
    """

    def _lines(self):
        lines = []
        for i in range(40):
            p = "host01;600;2026-06-01 {0:02d}:{1:02d}:00 UTC;".format(
                i // 12, (i % 12) * 5)
            lines += [HEADERS["nfsd"],
                      p + "0.00;0.00;0.00;0.00;0.00;0.00;0.00;0.00;0.00;0.00;0.00"]
        return lines

    def test_load_succeeds(self):
        result = sar.load(self.con, self.write("sa-host01-20260601.csv",
                                               self._lines()), self.progress)
        self.assertEqual(result.ok_rows, 40)
        self.assertEqual(result.errors, {})
        self.assertEqual(self.err.getvalue(), "")

    def test_no_detection_from_a_flat_zero_series(self):
        """**全て 0 の系列から検知が出ない。**"""
        from s_anomaly import config as cfg_mod, metrics
        from s_anomaly.detectors import REGISTRY
        sar.load(self.con, self.write("sa-host01-20260601.csv", self._lines()),
                 self.progress)
        metrics.build_jvm_gc_derived(self.con)
        metrics.build_metrics(self.con)
        values = {}
        for param in cfg_mod.PARAMS:
            values[(param.section, param.key)] = param.default
        cfg = cfg_mod.Config(values)
        found_total = 0
        for meta in metrics.list_series(self.con):
            for detector in REGISTRY.values():
                found, _ = detector.run(self.con, cfg, meta, self.progress)
                found_total += len(found)
        self.assertEqual(found_total, 0,
                         "全て 0 の系列から検知が出てはならない")


class TestRegistration(unittest.TestCase):
    """**登録漏れを捕まえる。** 例外は出ないため、対応表どうしを突き合わせる。"""

    def test_all_sar_metrics_are_detectable(self):
        """★`DETECTABLE_METRICS` への登録漏れは、検知 0 件のまま正常終了する★"""
        missing = [m for m in sar.ALL_METRICS
                   if m not in metrics.DETECTABLE_METRICS]
        self.assertEqual(
            missing, [],
            "metrics.DETECTABLE_METRICS に無いメトリクスは 1 つの検知器も走らない "
            "(例外も警告も出ない。P003 DS-07-07b)")

    def test_sar_metrics_are_not_in_ratio_metrics(self):
        """接頭 `pct_` を接尾 `_pct` の仲間に入れない (ADR-005 との書き分け)。"""
        for name in sar.ALL_METRICS:
            self.assertNotIn(name, metrics.RATIO_METRICS)

    def test_every_metric_has_a_japanese_label(self):
        missing = [m for m in sar.ALL_METRICS
                   if m not in reporter.METRIC_LABELS]
        self.assertEqual(missing, [], "レポートの「データ」欄の和名が無い")

    def test_sar_is_a_source_kind(self):
        """`SOURCE_KINDS` に無いと `[timezone] sar` が無言で無視される。"""
        self.assertIn("sar", config_mod.SOURCE_KINDS)
        self.assertIn(("timezone", "sar"),
                      [(p.section, p.key) for p in config_mod.PARAMS])

    def test_c1_classifies_every_sar_metric(self):
        """**22 = 8 + 14。** どちらにも入らないメトリクスがあってはならない。"""
        self_ratio = set(c1_saturation.SELF_RATIO_METRICS)
        excluded = set(c1_saturation.NO_CEILING["sar"])
        self.assertEqual(self_ratio & excluded, set())
        self.assertEqual(self_ratio | excluded, set(sar.ALL_METRICS))

    def test_pct_idle_is_excluded_from_c1(self):
        """★除外し忘れると閑散なホストが全件「張り付き」になる★ (FR-148)"""
        self.assertIn("pct_idle", c1_saturation.NO_CEILING["sar"])
        self.assertNotIn("pct_idle", c1_saturation.SELF_RATIO_METRICS)

    def test_gen_testdata_constants_match(self):
        """※CR-011 生成ツールの数え上げが `WANTED` と一致すること。

        **`gen_testdata` はアプリを import しない**(配布資産の独立性)。
        そのため NFS のメトリクス一覧を二重に持っている。**A006 の規模計算が
        これを参照する**ため、ずれると期待レコード数が合わなくなる。
        """
        import os
        import sys
        tools = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import gen_testdata as gen
        self.assertEqual(sorted(gen.SAR_WANTED_NFS), sorted(sar.WANTED["nfs"]))
        self.assertEqual(sorted(gen.SAR_WANTED_NFSD),
                         sorted(sar.WANTED["nfsd"]))

    def test_activity_names_match_the_config(self):
        self.assertEqual(sorted(sar.WANTED.keys()),
                         sorted(config_mod.SAR_ACTIVITIES))
        self.assertEqual(sorted(a for a, _, _ in sar.ACTIVITY_RULES),
                         sorted(config_mod.SAR_ACTIVITIES))


if __name__ == "__main__":
    unittest.main()
