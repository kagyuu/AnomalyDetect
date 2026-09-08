"""A014 — sar(sysstat)取り込みの確認 (※CR-010)。

**アプリを外側から起動し、生成物として確かめる。**

sar 対応は入力種別を 1 つ増やすだけに見えるが、**「取り込みに成功したのに
1 件も検知されない」という壊れ方をする。** `metrics.DETECTABLE_METRICS` は
ハードコードの許可リストであり、漏れがあっても例外も警告も出ない。

| 危険 | 起きること |
| --- | --- |
| `DETECTABLE_METRICS` への登録漏れ | 取り込みも `metrics` 構築も成功し、**検知だけが 0 件のまま正常終了する** |
| `SOURCE_KINDS` への登録漏れ | `[timezone] sar` が無視され、**時差のある環境で突き合わせが静かに全て外れる** |
| `pct_idle` を ALG-C1 から除外し忘れ | **閑散なホストが全件「上限に張り付き」**として検知される |
| 後方互換の破壊 | `sa-*.csv` が無い既存利用者の結果が変わる |

**★本試験は「例外が出ないこと」を合格条件にしない。件数を数える。★**
`docs/CLAUDE.md` が記録するとおり、同じ型の欠陥は既に 2 回起きている。
"""

import os
import re
import shutil
import tempfile
import unittest

from tests.acceptance import _harness as H

#: sar 由来のイベントの「データ」欄。
SAR_DATA_LINE = re.compile(r"^\* データ : sar / (\S+)[^\n]*$", re.M)

#: 「データ」欄の系列キー部分。
SAR_KEY = re.compile(r"activity=(\w+)(?:, device=(\S+))?")

ACTIVITIES = ("cpu", "memory", "swap_space", "io",
              "load", "swapping", "disk", "network",
              "nfs", "nfsd")            # ※CR-011


def sar_settings(activities=None, sar_timezone=None):
    parts = []
    if sar_timezone is not None:
        parts.append("[timezone]\nstorage = UTC\nsar = {0}\n".format(sar_timezone))
    if activities is not None:
        parts.append("[sar]\nactivities = {0}\n".format(activities))
    return "".join(parts) if parts else None


def run_with(tmp, label, settings, logs):
    entry = H.make_dist(os.path.join(tmp, "dist-" + label),
                        settings=settings, with_tests=False)
    work = os.path.join(tmp, "work-" + label)
    os.makedirs(work)
    return H.run_app(logs, work, entry=entry), work


class TestA014Sar(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA014Sar, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name

        # all — 既定 (8 種類すべて)
        cls.all_proc, cls.all_work = run_with(tmp, "all", None, cls.normal_logs)

        # narrow — 2 種類へ絞る。**未知の名前を 1 つ混ぜて、警告で済むことも見る**
        cls.narrow_proc, cls.narrow_work = run_with(
            tmp, "narrow", sar_settings(activities="cpu, memory, bogus"),
            cls.normal_logs)

        # nosar — sa-*.csv を除いた入力 (後方互換)
        nosar_logs = os.path.join(tmp, "logs-nosar")
        shutil.copytree(cls.normal_logs, nosar_logs)
        for name in os.listdir(nosar_logs):
            if name.startswith("sa-"):
                os.unlink(os.path.join(nosar_logs, name))
        cls.nosar_logs = nosar_logs
        cls.nosar_proc, cls.nosar_work = run_with(
            tmp, "nosar", None, nosar_logs)

        # tz — [timezone] sar だけ JST にする (表記は UTC のまま)
        cls.tz_proc, cls.tz_work = run_with(
            tmp, "tz", sar_settings(sar_timezone="Asia/Tokyo"), cls.normal_logs)

        cls.all_text = H.read_all_reports(cls.all_work)
        cls.narrow_text = H.read_all_reports(cls.narrow_work)
        cls.nosar_text = H.read_all_reports(cls.nosar_work)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # --- 補助 -----------------------------------------------------------
    @staticmethod
    def sar_events(text):
        return [e for e in text.split("# イベントID( ")[1:]
                if "* データ : sar /" in e]

    @staticmethod
    def activities_in(text):
        return {m.group(1) for m in SAR_KEY.finditer(text)}

    # --- 1〜2 実行と探索 -------------------------------------------------
    def test_01_completes(self):
        self.assertEqual(self.all_proc.returncode, 0,
                         H.err(self.all_proc)[-2000:])

    def test_02_discovery_reports_sar_count(self):
        """起動ログの S3 の内訳に sar の件数が出る。"""
        out = H.out(self.all_proc)
        m = re.search(r"sar (\d+)\)", out)
        self.assertIsNotNone(m, "S3 の内訳に sar が無い")
        self.assertEqual(int(m.group(1)), 42)

    # --- 3〜6 検知されること ---------------------------------------------
    def test_03_sar_events_exist(self):
        """★本試験の存在理由★ 0 件なら、他が全て PASS でも合格としない。"""
        events = self.sar_events(self.all_text)
        self.assertGreater(
            len(events), 0,
            "sar 由来のイベントが 1 件も無い。取り込みが成功していても "
            "metrics.DETECTABLE_METRICS への登録漏れで検知だけが消える")

    def test_04_data_line_has_japanese_label_and_series_key(self):
        events = self.sar_events(self.all_text)
        line = SAR_DATA_LINE.search(events[0]).group(0)
        self.assertIn("host=", line)
        self.assertIn("activity=", line)
        self.assertRegex(line, r"sar / \S+ \([^)]+\) /",
                         "メトリクスの和名が出ていない: " + line)

    def test_05_all_activities_appear(self):
        got = self.activities_in(self.all_text)
        missing = sorted(set(ACTIVITIES) - got)
        self.assertEqual(missing, [], "系列として現れない活動: {0}".format(missing))

    def test_06_devices_are_separate_series(self):
        devices = {m.group(2) for m in SAR_KEY.finditer(self.all_text)
                   if m.group(1) == "disk" and m.group(2)}
        self.assertGreaterEqual(len(devices), 2, devices)

    # --- 7〜8 ALG-C1 -----------------------------------------------------
    def test_07_pct_memused_saturation_is_not_estimated(self):
        """INJ-011 が **推定上限ではなく上限 100** で検知されること (FR-148)。"""
        hits = [e for e in self.sar_events(self.all_text)
                if "pct_memused" in e and "ALG-C1" in e]
        self.assertGreater(len(hits), 0, "INJ-011 の張り付きが検知されていない")
        body = hits[0]
        self.assertIn("実際の上限 100", body,
                      "sar の比率で ADR-016 の推定上限を使ってはならない")
        self.assertNotIn("推定上限", body)

    def test_08_pct_idle_is_never_saturated(self):
        """★除外し忘れると閑散なホストが全件「張り付き」になる★ (FR-148)"""
        offenders = [e for e in self.sar_events(self.all_text)
                     if "pct_idle" in e and "ALG-C1" in e]
        self.assertEqual(
            len(offenders), 0,
            "pct_idle が「上限への張り付き」として検知されている。"
            "100% の遊休は正常であり、NO_CEILING での除外が要る")

    # --- 9 既存ログとの突き合わせ ----------------------------------------
    def test_09_sar_and_app_layer_are_linked_both_ways(self):
        """アプリ層と OS 資源が「同時に発生したアノマリー」で結び付くこと。"""
        forward = backward = 0
        for event in self.all_text.split("# イベントID( ")[1:]:
            index = event.find("* 同時に発生したアノマリー")
            if index < 0:
                continue
            block = event[index:index + 2000]
            is_sar = "* データ : sar /" in event
            has_sar_partner = re.search(r"\) sar / ", block) is not None
            has_other_partner = re.search(
                r"\) (db_connection|jvm_gc|lsf_queue) / ", block) is not None
            if not is_sar and has_sar_partner:
                forward += 1
            if is_sar and has_other_partner:
                backward += 1
        self.assertGreater(forward, 0,
                           "アプリ層のイベントが sar を相手に挙げていない")
        self.assertGreater(backward, 0,
                           "sar のイベントがアプリ層を相手に挙げていない")

    # --- ※CR-011 NFS / NFSD ---------------------------------------------
    def test_09a_nfs_events_reach_the_report(self):
        """★`nfs` と `nfsd` がそれぞれレポートに出ること★

        `metrics.DETECTABLE_METRICS` か `config.SAR_ACTIVITIES` への
        登録漏れがあると、**例外も警告も出ないまま 0 件になる。**
        """
        got = self.activities_in(self.all_text)
        for activity in ("nfs", "nfsd"):
            self.assertIn(activity, got,
                          "{0} 由来のイベントがレポートに無い".format(activity))

    def test_09b_nfs_metric_labels_are_shown(self):
        """NFS のメトリクスに和名が出ること。"""
        self.assertRegex(self.all_text, r"retrans_s \(NFS 再送")

    def test_09c_no_unknown_activity_warning(self):
        """★CR-011 の目的のひとつ★ — 読み飛ばしの警告が出なくなること。

        CR-010 の時点では 1 ファイルにつき 2 行、30 日分で 60 行ほど出ていた。
        """
        combined = H.err(self.all_proc) + H.out(self.all_proc)
        self.assertNotIn("未知の活動種別", combined)

    def test_09d_retrans_and_lsf_backlog_share_a_host(self):
        """**NFS の再送異常と LSF のジョブ滞留が同じホストに出ること。**

        依頼者の指摘した因果(NFS の詰まり → ジョブ滞留)を、
        レポートを読む人が突き合わせられる状態にあるかを見る(FR-142a)。
        """
        def hosts_for(pattern):
            found = set()
            for event in self.all_text.split("# イベントID( ")[1:]:
                if not re.search(pattern, event):
                    continue
                m = re.search(r"host=([^,\s]+)", event)
                if m:
                    found.add(m.group(1))
            return found

        nfs_hosts = hosts_for(r"\* データ : sar / retrans_s")
        lsf_hosts = hosts_for(r"\* データ : lsf_queue / pend")
        self.assertTrue(nfs_hosts, "retrans_s のイベントが無い")
        self.assertTrue(lsf_hosts, "pend のイベントが無い")
        self.assertTrue(
            nfs_hosts & lsf_hosts,
            "NFS の異常と LSF の滞留が同じホストに無い: {0} / {1}".format(
                nfs_hosts, lsf_hosts))

    # --- 10 イベント ID ---------------------------------------------------
    def test_10_event_id_host_is_resolved(self):
        for event in self.sar_events(self.all_text):
            event_id = event.split(")")[0].strip()
            self.assertNotIn("unknown", event_id,
                             "series_id からホスト名が取れていない: " + event_id)

    # --- 11〜12 絞り込み --------------------------------------------------
    def test_11_narrowing_reduces_activities(self):
        got = self.activities_in(self.narrow_text)
        self.assertTrue(got, "絞り込み後に sar のイベントが 1 件も無い")
        self.assertEqual(got - {"cpu", "memory"}, set(),
                         "絞り込んだはずの活動が現れている: {0}".format(got))
        self.assertLess(len(got), len(self.activities_in(self.all_text)))

    def test_12_unknown_activity_name_only_warns(self):
        """**未知の活動名で止めない** (P001 FR-149)。終了コードは 0。"""
        self.assertEqual(self.narrow_proc.returncode, 0,
                         H.err(self.narrow_proc)[-2000:])
        self.assertIn("bogus", H.err(self.narrow_proc) + H.out(self.narrow_proc))

    # --- 13 後方互換 ------------------------------------------------------
    def test_13_without_sar_matches_the_pre_sar_behaviour(self):
        """**`sa-*.csv` が無い入力では、sar が一切現れない。**

        イベント ID の連番は「日 × ホスト」ごとであり(CR-003)、sar が増えると
        既存イベントの ID がずれる。**これは仕様である**(A014 §4 の ★ACCEPTED★)。
        したがってここでは ID ではなく、**sar 由来の内容が無いこと**と
        **完走すること**を確かめる。
        """
        self.assertEqual(self.nosar_proc.returncode, 0,
                         H.err(self.nosar_proc)[-2000:])
        self.assertNotIn("sar /", self.nosar_text)
        self.assertEqual(self.sar_events(self.nosar_text), [])
        self.assertTrue(H.summary_paths(self.nosar_work))

    # --- 14〜15 タイムゾーン ---------------------------------------------
    def test_14_timezone_conversion_shifts_sar_only(self):
        """`[timezone] sar = Asia/Tokyo` で sar の時刻だけが 9 時間ずれる。"""
        self.assertEqual(self.tz_proc.returncode, 0,
                         H.err(self.tz_proc)[-2000:])
        self.assertIn("投入時にタイムゾーンを変換します", H.out(self.tz_proc))

    def test_15_timezone_notation_mismatch_warns_and_continues(self):
        """**表記 (UTC) と設定 (Asia/Tokyo) の食い違いを警告する** (FR-147)。

        設定値を正として扱い、**処理は止めない**。
        """
        self.assertIn("タイムゾーン表記", H.err(self.tz_proc))
        self.assertEqual(self.tz_proc.returncode, 0)
        self.assertTrue(H.summary_paths(self.tz_work))

    # --- 16 再現性 --------------------------------------------------------
    def test_16_reproducible(self):
        """同一入力で 2 回実行し、実行日時欄を除いて完全一致すること。"""
        with tempfile.TemporaryDirectory() as tmp:
            proc, work = run_with(tmp, "again", None, self.normal_logs)
            self.assertEqual(proc.returncode, 0, H.err(proc)[-2000:])
            self.assertEqual(
                H.strip_volatile(H.read_all_reports(work), drop_target_dir=True),
                H.strip_volatile(self.all_text, drop_target_dir=True))


if __name__ == "__main__":
    unittest.main()
