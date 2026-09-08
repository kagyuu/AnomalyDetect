"""T009 — sar 取り込みパイプラインの連携 (※CR-010)。

**本テストの中心は「sar 由来のイベントが 1 件以上出ること」である。**

`metrics.DETECTABLE_METRICS` への登録漏れは**例外も警告も出さず、検知 0 件の
まま正常終了する**(`docs/P003-backend-spec.md` DS-07-07b)。したがって
「例外が出ないこと」を合格条件にしない。**件数を数える。**
"""

import io
import os
import tempfile
import unittest

from s_anomaly import (
    bootstrap, co_anomaly, config as config_mod, discovery, events, loaders,
    metrics, progress as progress_mod, schema,
)
from s_anomaly.detectors import REGISTRY
from s_anomaly.loaders import bqueues, dbconn, jstat, sar
from tests.integration import _setup_baseline


def default_config(**overrides):
    values = {}
    for param in config_mod.PARAMS:
        values[(param.section, param.key)] = param.default
    for key, value in overrides.items():
        values[("sar", key)] = value
    return config_mod.Config(values)


class SarPipelineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()

    def setUp(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        self.progress = progress_mod.setup(self.out, self.err)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "1GB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        self.addCleanup(self.con.close)
        schema.create_all(self.con)
        loaders.reset_load_seq()

    def ingest(self, logs_dir, cfg=None):
        loader = {
            discovery.KIND_DBCONN: dbconn.load,
            discovery.KIND_JVMGC: jstat.load,
            discovery.KIND_LSF: bqueues.load,
            discovery.KIND_SAR: sar.load,
        }
        found = discovery.discover(logs_dir, self.progress)
        for logfile in found:
            loader[logfile.kind](self.con, logfile, self.progress, cfg)
        loaders.dedupe_all(self.con)
        metrics.build_jvm_gc_derived(self.con)
        metrics.build_metrics(self.con)
        return found

    def q(self, sql, params=None):
        return self.con.execute(sql, params or []).fetchall()

    def run_placeholder(self):
        """`SarPipelineCase` を取り込み用の入れ物として使うための空メソッド。"""


class TestSarThroughDetection(SarPipelineCase):
    """ベースライン一式を通しで流す。

    **取り込みと検知はクラスで 1 回だけ行う。** テストごとに 42 ファイルを
    読み直すと 1 スイートで 3 分を超え、「続けて 2 回実行する」規則
    (`docs/CLAUDE.md`) の負担が大きくなる。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.logs = os.path.join(cls.paths["normal"], "logs")
        cls._shared = None
        # **取り込みはクラスで 1 回だけ。** 42 ファイルを毎テスト読み直さない。
        case = SarPipelineCase("run_placeholder")
        case.setUp()
        cls._case = case
        cls.found = case.ingest(cls.logs)

    @classmethod
    def tearDownClass(cls):
        cls._case.doCleanups()

    def setUp(self):
        # 共有した接続と進捗をそのまま使う (再取り込みしない)
        case = type(self)._case
        self.con = case.con
        self.progress = case.progress
        self.out, self.err = case.out, case.err
        self._tmp = case._tmp

    def detect_all(self):
        """全系列 × 全検知器を 1 回だけ実行し、結果を使い回す。"""
        cls = type(self)
        if cls._shared is None:
            cfg = default_config()
            detections = []
            for meta in metrics.list_series(self.con):
                for detector in REGISTRY.values():
                    found, _ = detector.run(self.con, cfg, meta, self.progress)
                    detections.extend(found)
            merged = events.merge_detections(detections, cfg)
            events.assign_event_ids(merged)
            co_anomaly.collect(merged)
            cls._shared = merged
        return cls._shared

    # --- 1. 探索 --------------------------------------------------------
    def test_discovery_recognises_sar_files(self):
        sar_files = [f for f in self.found if f.kind == discovery.KIND_SAR]
        self.assertEqual(len(sar_files), 42)
        self.assertEqual({f.host for f in sar_files},
                         {"host01", "host02", "lsfhost01"})
        self.assertTrue(all(f.date and len(f.date) == 8 for f in sar_files))

    # --- 2/3. 格納 ------------------------------------------------------
    def test_all_activities_are_stored(self):
        got = {r[0] for r in self.q("SELECT DISTINCT activity FROM sar")}
        self.assertEqual(got, set(sar.WANTED.keys()))

    def test_devices_are_separate_rows(self):
        disks = {r[0] for r in self.q(
            "SELECT DISTINCT device FROM sar WHERE activity = 'disk'")}
        self.assertEqual(disks, {"sda", "sdb"})
        ifaces = {r[0] for r in self.q(
            "SELECT DISTINCT device FROM sar WHERE activity = 'network'")}
        self.assertEqual(ifaces, {"eth0", "eth1"})

    # --- 4. metrics への合流 --------------------------------------------
    def test_series_id_format(self):
        rows = self.q("SELECT DISTINCT series_id FROM metrics "
                      "WHERE source = 'sar' ORDER BY series_id")
        self.assertTrue(rows)
        for (sid,) in rows:
            parts = sid.split("/")
            self.assertEqual(len(parts), 4, sid)
            self.assertEqual(parts[0], "sar")
            self.assertIn(parts[2], sar.WANTED, sid)

    def test_devices_become_separate_series(self):
        series = {r[0] for r in self.q(
            "SELECT DISTINCT series_id FROM metrics WHERE source = 'sar'")}
        self.assertIn("sar/host01/disk/sda", series)
        self.assertIn("sar/host01/disk/sdb", series)
        self.assertIn("sar/host01/network/eth0", series)

    # --- 5. 検知対象として現れること ------------------------------------
    def test_sar_series_are_detectable(self):
        """★登録漏れがあるとここが空になる (例外は出ない)★"""
        metas = [m for m in metrics.list_series(self.con) if m.source == "sar"]
        self.assertGreater(len(metas), 0,
                           "sar の系列が list_series に現れていない。"
                           "metrics.DETECTABLE_METRICS を疑うこと")
        got = {m.metric for m in metas}
        self.assertEqual(got, set(sar.ALL_METRICS),
                         "取り込んだメトリクスと検知対象が食い違っている")

    # --- 6. 実際に検知されること ----------------------------------------
    def test_sar_events_are_produced(self):
        """★このテストが本 CR の要である★ — 0 件なら FAIL。"""
        merged = self.detect_all()
        sar_events = [e for e in merged if e.source == "sar"]
        self.assertGreater(
            len(sar_events), 0,
            "sar 由来のイベントが 1 件も無い。取り込みが成功していても "
            "DETECTABLE_METRICS への登録漏れで検知だけが消える (DS-07-07b)")
        # ホスト名が series_id から取れていること (#10)
        for event in sar_events:
            self.assertNotEqual(events.extract_host(event.series_id),
                                events.UNKNOWN_HOST, event.series_id)

    # --- 7/8. ALG-C1 の扱い ---------------------------------------------
    def _c1(self, series_id, metric):
        cfg = default_config()
        metas = [m for m in metrics.list_series(self.con)
                 if m.series_id == series_id and m.metric == metric]
        self.assertEqual(len(metas), 1, (series_id, metric))
        return REGISTRY["ALG-C1"].run(self.con, cfg, metas[0], self.progress)

    def test_pct_idle_is_never_saturated(self):
        """**閑散なホストを全件「張り付き」にしない** (FR-148)。

        生成データの `%idle` は夜間に 90% 超が何時間も続くため、除外が
        効いていなければ必ず検知される。
        """
        for host in ("host01", "host02", "lsfhost01"):
            found, _ = self._c1("sar/{0}/cpu/-1".format(host), "pct_idle")
            self.assertEqual(found, [], host)

    def test_pct_memused_uses_the_real_ceiling(self):
        """INJ-011 の張り付きが、**推定上限ではなく上限 100** で検知される。"""
        found, _ = self._c1("sar/host02/memory/-", "pct_memused")
        self.assertGreater(len(found), 0, "INJ-011 が検知されていない")
        detail = found[0].detail
        self.assertEqual(detail.get("ceiling"), 100.0)
        self.assertFalse(detail.get("ceiling_estimated"),
                         "sar の比率で ADR-016 の推定上限を使ってはならない")

    # --- ※CR-011 NFS / NFSD ---------------------------------------------
    def test_nfs_activities_are_stored(self):
        got = {r[0] for r in self.q(
            "SELECT DISTINCT activity FROM sar WHERE activity IN ('nfs','nfsd')")}
        self.assertEqual(got, {"nfs", "nfsd"})

    def test_nfsd_only_on_the_server_host(self):
        """`nfsd` は NFS サーバ役の 1 ホストだけに出る(P002 8.2)。"""
        hosts = {r[0] for r in self.q(
            "SELECT DISTINCT host FROM sar WHERE activity = 'nfsd'")}
        self.assertEqual(len(hosts), 1, hosts)

    def test_nfs_events_are_produced(self):
        """★`nfs` と `nfsd` がそれぞれ 1 件以上イベントになること★"""
        merged = self.detect_all()
        for activity in ("nfs", "nfsd"):
            hits = [e for e in merged
                    if e.series_id.startswith("sar/")
                    and e.series_id.split("/")[2] == activity]
            self.assertGreater(
                len(hits), 0,
                "{0} 由来のイベントが 1 件も無い。"
                "metrics.DETECTABLE_METRICS と config.SAR_ACTIVITIES を疑うこと"
                .format(activity))

    def test_retrans_anomaly_shares_the_host_with_the_lsf_backlog(self):
        """**NFS の再送異常が、LSF のジョブ滞留と同じホストで検知されること。**

        依頼者の指摘「LSF 計算グリッドが NFS でファイルを共有するため、
        NFS の詰まりがジョブ滞留の原因になりうる」(P001 FR-142a)を、
        レポート上で突き合わせられる状態にあるかを見る。
        """
        merged = self.detect_all()
        retrans = [e for e in merged if e.metric == "retrans_s"]
        self.assertGreater(len(retrans), 0, "INJ-013 が検知されていない")
        nfs_hosts = {events.extract_host(e.series_id) for e in retrans}
        lsf_hosts = {events.extract_host(e.series_id) for e in merged
                     if e.source == "lsf_queue" and e.metric == "pend"}
        self.assertTrue(nfs_hosts & lsf_hosts,
                        "NFS の異常と LSF の滞留が同じホストに無い: "
                        "{0} / {1}".format(nfs_hosts, lsf_hosts))

    def test_untaken_nfsd_columns_are_absent(self):
        """`packet_s` / `udp_s` / `tcp_s` を取り込んでいないこと(FR-142b)。"""
        stored = {r[0] for r in self.q(
            "SELECT DISTINCT metric FROM sar WHERE activity = 'nfsd'")}
        self.assertEqual(stored, set(sar.WANTED["nfsd"]))

    def test_no_unknown_activity_warning_for_nfs(self):
        """★CR-011 の目的のひとつ★ — 読み飛ばしの警告が出なくなること。"""
        self.assertNotIn("未知の活動種別", self.err.getvalue())

    # --- 9. 既存ログとの突き合わせ --------------------------------------
    def test_sar_and_app_layer_events_are_linked(self):
        """**アプリ層と OS 資源が「同時に発生したアノマリー」で結び付くこと。**

        これが sar 対応の主目的である (P001 FR-150)。
        """
        merged = self.detect_all()

        cross = 0
        for event in merged:
            if event.source == "sar" or not event.co_anomalies:
                continue
            if any(c.source == "sar" for c in event.co_anomalies):
                cross += 1
        self.assertGreater(
            cross, 0,
            "アプリ層のイベントが sar のイベントを同時アノマリーに挙げていない")

        reverse = 0
        for event in merged:
            if event.source != "sar" or not event.co_anomalies:
                continue
            if any(c.source != "sar" for c in event.co_anomalies):
                reverse += 1
        self.assertGreater(
            reverse, 0, "sar のイベントがアプリ層のイベントを挙げていない")


class TestSarTolerance(SarPipelineCase):
    """版差・未知ブロック・設定による絞り込み (#11〜#15)。"""

    def _write(self, lines, name="sa-hostX-20260601.csv"):
        d = os.path.join(self._tmp.name, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8",
                  newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        return d

    def _sample(self, ts="2026-06-01 00:00:00 UTC"):
        p = "hostX;300;{0};".format(ts)
        return [
            "# hostname;interval;timestamp;CPU;%user;%nice;%system;"
            "%iowait;%steal;%idle",
            p + "-1;12.00;0.00;4.00;1.50;0.00;82.50",
            "# hostname;interval;timestamp;kbmemfree;kbavail;kbmemused;"
            "%memused;kbbuffers;kbcached;kbcommit;%commit",
            p + "1024000;1100000;2000000;52.00;204800;1048576;900000;40.00",
        ]

    def test_unknown_block_does_not_stop_the_run(self):
        lines = self._sample()
        for _ in range(3):
            lines += ["# hostname;interval;timestamp;INTR;intr/s",
                      "hostX;300;2026-06-01 00:00:00 UTC;0;12.5"]
        self.ingest(self._write(lines))
        self.assertEqual(
            {r[0] for r in self.q("SELECT DISTINCT activity FROM sar")},
            {"cpu", "memory"})
        self.assertEqual(self.err.getvalue().count("未知の活動種別"), 1)

    def test_unknown_column_is_silent(self):
        lines = [
            "# hostname;interval;timestamp;CPU;%user;%nice;%system;"
            "%iowait;%steal;%idle;%guest",
            "hostX;300;2026-06-01 00:00:00 UTC;-1;12.00;0.00;4.00;"
            "1.50;0.00;82.50;0.30",
        ]
        self.ingest(self._write(lines))
        self.assertNotIn("未知", self.err.getvalue())
        self.assertEqual(
            {r[0] for r in self.q("SELECT DISTINCT metric FROM sar")},
            set(sar.WANTED["cpu"]))

    def test_timezone_mismatch_warns_and_continues(self):
        cfg = default_config()
        values = dict(cfg.as_dict())
        values[("timezone", "sar")] = "Asia/Tokyo"
        cfg = config_mod.Config(values)
        self.ingest(self._write(self._sample()), cfg=cfg)
        self.assertIn("タイムゾーン表記", self.err.getvalue())
        self.assertGreater(
            self.q("SELECT count(*) FROM sar")[0][0], 0, "処理は続行する")

    def test_activities_filter_reduces_the_table(self):
        cfg = default_config(activities="cpu")
        self.ingest(self._write(self._sample()), cfg=cfg)
        self.assertEqual(
            {r[0] for r in self.q("SELECT DISTINCT activity FROM sar")},
            {"cpu"})

    def test_pipeline_works_without_any_sar_file(self):
        """**後方互換**: `sa-*.csv` が 1 件も無くても全ステップが通る。"""
        d = os.path.join(self._tmp.name, "nosar")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "DBConnection_20260601.csv"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("日付,ホスト名,ポート番号,データソース,"
                         "activeConnectionsCurrentCount\n")
            for i in range(40):
                handle.write("2026/06/01 00:{0:02d}:00,h1,7003,ds1,{1}\n"
                             .format(i, i))
        self.ingest(d)
        self.assertEqual(self.q("SELECT count(*) FROM sar")[0][0], 0)
        self.assertGreater(
            self.q("SELECT count(*) FROM metrics")[0][0], 0)


if __name__ == "__main__":
    unittest.main()
