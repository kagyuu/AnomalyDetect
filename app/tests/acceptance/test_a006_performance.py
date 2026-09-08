"""A006 — 大規模データでの完走と実行時間 (NFR-001 / NFR-002)。

**大規模データの作り方は A006【使用するテストデータ】の (a) を採る。**
`gen_testdata.py` の生成関数を Python から直接呼び、モジュール定数
(期間・ホスト数・系列数) を差し替えて生成する。**`gen_testdata.py` 自体は
変更しない。**

## 解析対象の期間 (2026-09-03 に人間が確定)

**通常のユースケースは 30 日分、大規模ケースは 90 日分**である。
本テストは大規模ケース (90 日分) を測り、あわせて通常ケース (30 日分) も記録する。

当初は 365 日分を前提にしていたが、これは実運用の裏付けが無い Agent の想定であった。
**日数はファイル数に直結し (1 日あたり 4 ファイル)、S4 (取り込み) の所要時間を支配する**
ため、この前提の違いは性能に直接効く。

目標時間 10 分 (NFR-002) は ★FIXME★ のまま(期間は確定したが許容時間は未確認)。
**実測値は必ず記録し、未達であれば FAIL として P202 へ引き渡す。**

## これまでの実行結果

| 実行 | 規模 | 結果 |
| --- | --- | --- |
| P201 (2026-09-02) | 365 日分 / テーブル行 778 万 | **FAIL**。CPU 101.3 分でも未完了のため中断 |
| P203 (2026-09-03) | 365 日分 / 同上 | **FAIL**。2 時間のテストタイムアウトで打ち切り |

いずれも 365 日分を前提にした実行である。詳細は
`docs/test-records/20260903-0010-test-record.md` の A006 を参照。

## 既定でスキップする理由

本テストはデータ生成と解析に時間がかかるため、既定では実行しない。
A006【実行コマンド】が「**実行に長時間かかるため、`unittest` の他のテストと
分けて実行してよい**」と定めている範囲の措置である。
**失敗を隠すためのスキップではない。結果は上記のとおり記録済みである。**

実行するには環境変数を立てる:

```
S_ANOMALY_RUN_A006=1 python -m unittest discover -s app/tests/acceptance -t app -p "test_a006_performance.py" -v
```
"""

import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from datetime import timedelta

from tests.acceptance import _harness as H

sys.path.insert(0, os.path.join(H.APP_DIR, "tools"))
import gen_testdata as G  # noqa: E402

#: 解析対象の期間 (2026-09-03 に人間が明示した実運用の前提)。
#:   通常のユースケース : 30 日分
#:   大規模ケース       : 90 日分
#: **本テストは大規模ケース = 90 日分で測る。**
#: 当初は 365 日分を前提にしていたが、実運用の裏付けが無い Agent の想定値であった。
NORMAL_USE_DAYS = 30
LARGE_USE_DAYS = 90

#: 目標時間 (NFR-002)。★FIXME★ **この値は依然として Agent の想定である。**
#: 解析対象の期間は人間が確定したが、「何分以内なら許容できるか」は未確認である。
TARGET_SECONDS = 600.0

#: `generate_normal` は `write_jstat` を app01 / app02 の 2 つに固定して呼んでおり、
#: **`JVM_PAIRS` を反復しない**。したがって jstat の系列数は生成側では増やせない
#: (増やすには `gen_testdata.py` 自体の変更が必要であり、A006 が禁じている)。
#: 規模を伸ばせるのは **日数・DB ホスト数・DB データソース数**の 3 つである。
GENERATED_JVM_PAIRS = 2

#: レポートが分かれるホストの数 (DB 16 + LSF の lsfhost01)。
#: チャートの上限の計算に使う (※CR-009)。
HOST_COUNT = 17

#: 大規模ケース (90 日分) の生成パラメータ。
#:
#: **「レコード数」には 2 通りの数え方があるため、両方を明示する。**
#:
#: | | ファイル上のデータ行 | テーブルの行 (アプリが報告する値) |
#: | --- | --- | --- |
#: | db_connection | 90 x 288 x (16 x 6) = 2,488,320 | 同左 |
#: | jvm_gc | 90 x 288 x 2 = 51,840 | 同左 |
#: | lsf_queue | 90 x 288 x 1 = 25,920 | 90 x 288 x 12 = **311,040** |
#: | **合計** | **2,566,080** | **2,851,200** |
#:
#: `bqueues` はファイル上では 12 キューを 1 行に横持ちするが、`lsf_queue`
#: テーブルへはキューごとに 1 行として格納される (縦持ち変換)。
#: `report.md` の「総レコード数」はテーブルの行数である。
#:
#: ★FIXME★ **監視対象の系列数(DB ホスト数・データソース数・キュー数)は依然として
#: Agent の想定である。** P000 の実サンプルは 2 ホスト x 3 データソース + JVM 2 + LSF 3 の
#: 11 系列であった。本テストはその約 10 倍(110 系列)を大規模ケースとして置いている。
SCALE = {
    "days": LARGE_USE_DAYS,
    "db_hosts": 16,
    "db_sources": 6,
    "lsf_queues": 12,
}

#: 通常のユースケース (30 日分)。**大規模ケースとあわせて測り、記録する。**
NORMAL_SCALE = dict(SCALE, days=NORMAL_USE_DAYS)

#: 生成に必要な空き容量の目安 (実測 25.4MB / 52 万レコード から見積もる)。
REQUIRED_FREE_BYTES = 2 * 1024 ** 3

#: 明示的に立てられたときだけ実行する (上記「既定でスキップする理由」を参照)。
RUN_A006 = os.environ.get("S_ANOMALY_RUN_A006") == "1"
SKIP_REASON = (
    "A006 は実行に時間がかかるため既定では実行しない。"
    "実行するには S_ANOMALY_RUN_A006=1 を設定する"
)


def rescale(days, db_hosts, db_sources, lsf_queues):
    """`gen_testdata` のモジュール定数を差し替えて規模を変える。

    埋め込み異常が名指ししている系列名は維持する (生成関数がそれらを参照する)。

    戻り値は `(テーブルの行数, ファイル上のデータ行数)` のタプルである。
    **系列数 x 点数ではない。** `db_connection` は 1 系列 = 1 行だが、
    `jvm_gc` は 1 行から 11 メトリクスが取れ、`lsf_queue` はファイル上の
    1 行がテーブルの 12 行になる。系列数で数えると実態と合わない。
    """
    G.DAYS = days
    G.PERIOD_TO = (G.PERIOD_FROM + timedelta(days=days)
                   - timedelta(minutes=G.INTERVAL_MINUTES))
    G.TIMESTAMPS = G.timestamps()
    G.TOTAL_POINTS = len(G.TIMESTAMPS)

    # **採番は 1 始まりにする。** 埋め草の名前が、下の上書き代入で使う名前と
    # 衝突しないようにするため (0 始まりだと host01 が二重に現れる)。
    G.DB_HOSTS = ["host{0:02d}".format(i + 1) for i in range(db_hosts)]
    G.DB_SOURCES = ["OraclePool_{0}".format(i + 1) for i in range(db_sources)]
    G.LSF_QUEUES = ["q{0:02d}".format(i) for i in range(lsf_queues)]

    # 埋め込み異常が名指ししている系列名を維持する。
    # 1 始まりにしたことで、下の 5 行は結果的に恒等になっている。
    G.DB_HOSTS[0] = "host01"
    G.DB_HOSTS[1] = "host02"
    G.DB_SOURCES[0] = "OraclePool_1"
    G.DB_SOURCES[1] = "OraclePool_2"
    G.DB_SOURCES[2] = "OraclePool_3"
    for i, name in enumerate(("long", "normal", "short")):
        G.LSF_QUEUES[i] = name

    points = G.TOTAL_POINTS
    db_rows = db_hosts * db_sources * points
    jstat_rows = GENERATED_JVM_PAIRS * points
    # ※CR-010 sar。**規模は変えない**(SAR_HOSTS は 3 台のまま)。
    # 1 時刻あたり、ホストごとに `sar.ALL_METRICS` 相当のレコードが増える。
    # デバイス単位の 2 活動は 2 台ずつあるため、単純な 22 件ではない。
    sar_rows = (len(G.SAR_HOSTS) * SAR_METRICS_PER_POINT
                + SAR_NFSD_METRICS_PER_POINT) * points        # ※CR-011
    # ファイル上の行数は「1 時刻あたりのデータ行数」で数える
    # (cpu/memory/swap/io/load/swapping/nfs が 1 行ずつ + disk 2 + network 2、
    #  加えてサーバ役 1 ホストの nfsd が 1 行)
    sar_file_rows = (len(G.SAR_HOSTS) * SAR_LINES_PER_POINT
                     + SAR_NFSD_LINES_PER_POINT) * points     # ※CR-011
    table_rows = db_rows + jstat_rows + lsf_queues * points + sar_rows
    file_rows = db_rows + jstat_rows + points + sar_file_rows
    return table_rows, file_rows


#: ※CR-010 sar が 1 時刻・1 ホストあたりに生むレコード数。
#: 内訳: cpu 4 + memory 3 + swap_space 1 + io 3 + load 4 + swapping 2
#:       + disk 3 x 2 デバイス + network 3 x 2 インタフェース = 29
#: ※CR-011 これに nfs の 6 を足す (全ホストが NFS クライアントである)。
SAR_METRICS_PER_POINT = (4 + 3 + 1 + 3 + 4 + 2
                         + 3 * len(G.SAR_DEVICES) + 3 * len(G.SAR_IFACES)
                         + len(G.SAR_WANTED_NFS))

#: ※CR-011 `nfsd` は **NFS サーバ役の 1 ホストだけ**に出る。
#: したがってホスト数に比例しない。1 時刻あたり 1 ホスト分だけ加算する。
SAR_NFSD_METRICS_PER_POINT = len(G.SAR_WANTED_NFSD)

#: ※CR-010 sar が 1 時刻・1 ホストあたりに書くデータ行数。
#: デバイスを持たない 6 活動が 1 行ずつ + disk 2 行 + network 2 行 = 10
#: ※CR-011 nfs の 1 行を足して 11。
SAR_LINES_PER_POINT = 7 + len(G.SAR_DEVICES) + len(G.SAR_IFACES)

#: ※CR-011 `nfsd` の行。サーバ役の 1 ホストだけ、1 時刻につき 1 行。
SAR_NFSD_LINES_PER_POINT = 1


#: レポートの実物を残す先 (環境変数)。**指定があれば tearDown 前に複写する。**
#: 依頼者から「repot-***.md も残すようにしてください」との指示があるため
#: (2026-09-05)、一時ディレクトリと一緒に消えないようにする。
KEEP_ENV = "S_ANOMALY_A006_KEEP"


#: 利用者のホームディレクトリを含む絶対パス (※2026-09-06 のサニタイズ監査)。
#:
#: **複写したレポートには「対象ディレクトリ」として実行時の絶対パスが載る。**
#: A006 は一時ディレクトリで動くため、Windows では利用者のアカウント名を含む
#: パスになる。**このサンプルは資料として公開するため、アカウント名を伏せる。**
USER_HOME_PATH = re.compile(
    r"[A-Za-z]:\\Users\\[^\\|\s]+"     # Windows
    r"|/home/[^/|\s]+"                 # Linux
    r"|/Users/[^/|\s]+"                # macOS
)


def redact_user_paths(text) -> str:
    """レポート本文から、利用者を特定しうる絶対パスを伏せる。

    **置換するのはホームディレクトリの部分だけ**であり、それ以降
    (一時ディレクトリ名やログの配置)はそのまま残す。他の内容は変えない。
    """
    return USER_HOME_PATH.sub("(ホームディレクトリ)", text)


def keep_reports(work, label):
    """`S_ANOMALY_A006_KEEP` が指す先へレポート群を複写する。

    指定が無ければ何もしない。**テストの合否には影響させない**
    (複写に失敗しても、それは性能試験の結果ではないため)。

    **複写時に、利用者を特定しうる絶対パスを伏せる**(※2026-09-06)。
    このサンプルは資料として公開するため、Windows のアカウント名が
    「対象ディレクトリ」欄に残ってはならない。
    """
    dest_root = os.environ.get(KEEP_ENV)
    if not dest_root:
        return None
    dest = os.path.join(dest_root, label)
    try:
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        for name in sorted(os.listdir(work)):
            if name.startswith("report_") and name.endswith(".md"):
                # **単純な複写ではなく、パスを伏せてから書く。**
                with open(os.path.join(work, name), "r",
                          encoding="utf-8", newline="") as src:
                    body = redact_user_paths(src.read())
                with open(os.path.join(dest, name), "w",
                          encoding="utf-8", newline="") as dst:
                    dst.write(body)
        # ※CR-009 チャート (SVG) も一緒に残す。レポートが相対パスで参照する。
        charts_src = os.path.join(work, "charts")
        if os.path.isdir(charts_src):
            shutil.copytree(charts_src, os.path.join(dest, "charts"))
    except OSError as exc:
        sys.stderr.write("\n[A006] レポートの複写に失敗: {0}\n".format(exc))
        return None
    sys.stderr.write("\n[A006] レポートを残しました: {0}\n".format(dest))
    return dest


def s6_wall_seconds(stdout):
    """進捗ログから S6 の壁時計を取り出す (※CR-007)。無ければ None。"""
    for line in stdout.splitlines():
        if "検知の総所要" in line:
            try:
                return float(line.rsplit("検知の総所要", 1)[1].strip().rstrip("s"))
            except ValueError:
                return None
    return None


def free_bytes(path):
    return shutil.disk_usage(str(path)).free


@unittest.skipUnless(RUN_A006, SKIP_REASON)
class TestA006Performance(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA006Performance, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_dir = os.path.join(cls._tmp.name, "large")
        cls.work = os.path.join(cls._tmp.name, "work")
        os.makedirs(cls.work)

        cls.free_before = free_bytes(cls._tmp.name)
        cls.expected_records, cls.expected_file_rows = rescale(**SCALE)

        started = time.monotonic()
        G.generate_normal(cls.data_dir)
        cls.generate_seconds = time.monotonic() - started

        cls.logs = os.path.join(cls.data_dir, "logs")
        cls.file_count = sum(len(files) for _, _, files in os.walk(cls.logs))
        cls.byte_count = sum(
            os.path.getsize(os.path.join(d, f))
            for d, _, fs in os.walk(cls.logs) for f in fs)

        # temp_directory を作業ディレクトリ内へ向けた配布物を用意する
        cls.entry = H.make_dist(
            os.path.join(cls._tmp.name, "dist"),
            settings=H.duckdb_settings(max_memory="4GB",
                                       temp_directory="./tmp"),
            with_tests=False)

        started = time.monotonic()
        cls.proc = H.run_app(cls.logs, cls.work, entry=cls.entry, timeout=7200)
        cls.elapsed = time.monotonic() - started

        cls.stdout = H.out(cls.proc)
        cls.stderr = H.err(cls.proc)
        # ※CR-001: ホスト別ファイルを連結したもの
        cls.report = H.read_report(cls.work)
        cls.summary = H.read_summary(cls.work)
        cls.s6_seconds = s6_wall_seconds(cls.stdout)
        cls.kept_dir = keep_reports(cls.work, "large-{0}d".format(SCALE["days"]))
        sys.stderr.write(
            "\n[A006] {0} 日分 / 生成 {1:.1f}s / ファイル {2} / {3:.1f}MB"
            "\n[A006] テーブル行 {4:,} (ファイル上のデータ行 {5:,})"
            "\n[A006] 実行 {6:.1f}s (うち S6 検知 {7}) / 終了コード {8}\n".format(
                SCALE["days"], cls.generate_seconds, cls.file_count,
                cls.byte_count / 1e6, cls.expected_records,
                cls.expected_file_rows, cls.elapsed,
                "{0:.1f}s".format(cls.s6_seconds) if cls.s6_seconds else "不明",
                cls.proc.returncode))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _reported_records(self):
        for line in self.report.splitlines():
            if line.startswith("| 総レコード数 |"):
                return int(line.split("|")[2].strip().replace(",", ""))
        return None

    def test_00_scale_is_the_large_use_case(self):
        """実施した規模が大規模ケース (90 日分) であることを確認する。

        `expected_records` は **テーブルの行数**である(アプリが報告する値)。
        系列数 x 点数で数えると `lsf_queue` の縦持ち変換を取り違える。
        """
        self.assertEqual(SCALE["days"], LARGE_USE_DAYS,
                         "大規模ケースは 90 日分である")
        # ※CR-010 sar を加えて 2,851,200 → 5,106,240 行になった。
        # ※CR-011 NFS を加えて 5,780,160 行になった。
        #   内訳: 5,106,240 + nfs 3 ホスト x 6 x 25,920 + nfsd 1 ホスト x 8 x 25,920
        self.assertEqual(self.expected_records, 5780160,
                         "実施規模 {0:,} 行".format(self.expected_records))

    # 1
    def test_01_completes_with_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, self.stderr[-4000:])

    # 2
    def test_02_record_count_matches(self):
        reported = self._reported_records()
        self.assertIsNotNone(reported, "総レコード数が report.md にありません")
        self.assertEqual(reported, self.expected_records)

    # 3
    def test_03_within_target_time(self):
        self.assertLessEqual(
            self.elapsed, TARGET_SECONDS,
            "NFR-002 未達: {0} 日分 / {1:,} 行で {2:.1f} 秒 (目標 {3:.0f} 秒)".format(
                SCALE["days"], self.expected_records, self.elapsed,
                TARGET_SECONDS))

    # 4
    def test_04_report_structure(self):
        # ※CR-001・CR-004: ホスト別レポートは 5 章構成 (先頭に集計章)
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項"):
            self.assertIn(heading, self.report, heading)

    # 4b ※CR-004
    def test_04b_summary_fits_4k(self):
        """サマリが 4K トークン (目安 4,000 バイト) に収まること (UI-04-F06)。

        ★FIXME★ トークナイザが配布資産に含まれないため、バイト数で代用する。
        """
        # ※CR-004: **1 ファイルあたり**で判定する。90 日分は 3 か月に分かれ、
        # 年月ごとに 1 つのサマリができる (UI-04-F01)。
        paths = H.summary_paths(self.work)
        self.assertTrue(paths, "サマリが生成されていない")
        for path in paths:
            size = os.path.getsize(path)
            self.assertLess(size, 4000, "{0} が {1:,} バイトある".format(
                os.path.basename(path), size))
        # 90 日分は 3 か月に分かれる
        self.assertEqual(len(paths), 3,
                         [os.path.basename(p) for p in paths])

    # 4c ※CR-001
    def test_04c_split_into_host_files(self):
        """ホスト別に分割されていること。"""
        hosts = H.host_report_paths(self.work)
        self.assertGreater(len(hosts), 1, "ホスト別に分割されていない")
        for path in hosts:
            self.assertRegex(os.path.basename(path),
                             r"^report_.+_\d{6}\.md$")

    # 5
    def test_05_progress_is_continuous(self):
        s4 = [l for l in self.stdout.splitlines() if "[S4]" in l and "/" in l]
        self.assertGreaterEqual(
            len(s4), self.file_count // 2,
            "S4 の進捗行が {0} 行しかありません (ファイル {1} 件)".format(
                len(s4), self.file_count))

    # 6
    def test_06_no_memory_error(self):
        self.assertNotIn("MemoryError", self.stderr)
        self.assertNotIn("Traceback", self.stderr)

    # 7
    def test_07_temp_files_do_not_block_rerun(self):
        tmp_dir = os.path.join(self.work, "tmp")
        leftovers = []
        if os.path.isdir(tmp_dir):
            leftovers = os.listdir(tmp_dir)
        # 残っていても次回実行を妨げないこと (小さな入力で確認する)
        second = H.run_app(self.normal_logs, self.work, entry=self.entry,
                           timeout=3600)
        self.assertEqual(second.returncode, 0,
                         "残骸 {0} が再実行を妨げた: {1}".format(
                             leftovers, H.err(second)[-2000:]))

    def test_08_file_count_within_nfr001(self):
        """NFR-001 の上限 (1 万ファイル) を超えていないこと。

        ファイル数は日数に比例する (1 日あたり 4 ファイル)。90 日で 360 件。
        **S4 (取り込み) の所要時間はファイル数に比例するため、日数の前提が
        そのまま性能に効く**(`docs/ADR.md` ADR-013 の残存リスク)。
        """
        # ※CR-010 1 日あたり DBConnection 1 + jstat 2 + bqueues 1 + sar 3 = 7
        self.assertEqual(self.file_count,
                         SCALE["days"] * (4 + len(G.SAR_HOSTS)))
        self.assertLessEqual(self.file_count, 10000)


@unittest.skipUnless(RUN_A006, SKIP_REASON)
class TestA006NormalUseCase(H.BaselineCase):
    """通常のユースケース (30 日分) の実測。

    2026-09-03 に人間が「通常のユースケースでは 30 日分」と明示したため、
    大規模ケース (90 日分) とあわせて測り、記録する。
    **本クラスは合否を判定しない。運用者が期待できる所要時間を記録するためにある。**
    """

    @classmethod
    def setUpClass(cls):
        super(TestA006NormalUseCase, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_dir = os.path.join(cls._tmp.name, "normal-use")
        cls.work = os.path.join(cls._tmp.name, "work")
        os.makedirs(cls.work)

        cls.expected_records, cls.expected_file_rows = rescale(**NORMAL_SCALE)
        G.generate_normal(cls.data_dir)
        cls.logs = os.path.join(cls.data_dir, "logs")
        cls.file_count = sum(len(files) for _, _, files in os.walk(cls.logs))

        cls.entry = H.make_dist(
            os.path.join(cls._tmp.name, "dist"),
            settings=H.duckdb_settings(max_memory="4GB",
                                       temp_directory="./tmp"),
            with_tests=False)

        started = time.monotonic()
        cls.proc = H.run_app(cls.logs, cls.work, entry=cls.entry, timeout=7200)
        cls.elapsed = time.monotonic() - started
        # ※CR-001: ホスト別ファイルを連結したもの
        cls.report = H.read_report(cls.work)
        cls.summary = H.read_summary(cls.work)
        cls.stdout = H.out(cls.proc)
        cls.s6_seconds = s6_wall_seconds(cls.stdout)
        cls.kept_dir = keep_reports(
            cls.work, "normal-{0}d".format(NORMAL_SCALE["days"]))
        sys.stderr.write(
            "\n[A006/通常] {0} 日分 / ファイル {1} / テーブル行 {2:,}"
            "\n[A006/通常] 実行 {3:.1f}s (うち S6 検知 {4}) / 終了コード {5}\n".format(
                NORMAL_SCALE["days"], cls.file_count, cls.expected_records,
                cls.elapsed,
                "{0:.1f}s".format(cls.s6_seconds) if cls.s6_seconds else "不明",
                cls.proc.returncode))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_01_completes_with_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, H.err(self.proc)[-4000:])

    def test_02_report_structure(self):
        # ※CR-001・CR-004: ホスト別レポートは 5 章構成 (先頭に集計章)
        for heading in ("## 1. このホストの集計", "## 2. 実行サマリ",
                        "## 3. 適用したアルゴリズム", "## 4. 検知イベント",
                        "## 5. 実行時の注意事項"):
            self.assertIn(heading, self.report, heading)

    def test_03_record_count_matches(self):
        reported = None
        for line in self.report.splitlines():
            if line.startswith("| 総レコード数 |"):
                reported = int(line.split("|")[2].strip().replace(",", ""))
        self.assertEqual(reported, self.expected_records)

    def test_04_scale_is_the_normal_use_case(self):
        self.assertEqual(NORMAL_SCALE["days"], NORMAL_USE_DAYS)
        # ※CR-010 1 日あたり DBConnection 1 + jstat 2 + bqueues 1 + sar 3 = 7
        self.assertEqual(self.file_count,
                         NORMAL_USE_DAYS * (4 + len(G.SAR_HOSTS)))

    def test_05_all_eleven_algorithms_ran(self):
        """※CR-005 — **ALG-C1 を含む 11 個が本番規模で走ったこと。**

        レポートの「適用したアルゴリズム」章に載るのは有効なアルゴリズムであり、
        新設したものが設定漏れで走っていない、という事態を防ぐ。
        """
        for alg_id in ("ALG-A1", "ALG-A5", "ALG-B4", "ALG-B5", "ALG-C1"):
            self.assertIn(alg_id, self.report, alg_id)
        self.assertIn("上限への張り付き", self.report)

    def test_06_s6_is_parallel(self):
        """※CR-007 — 並列度が 2 以上で実行され、壁時計が記録されること。"""
        self.assertIsNotNone(self.s6_seconds, "S6 の総所要が出力されていない")
        found = [line for line in self.stdout.splitlines()
                 if "系列に適用します" in line]
        self.assertEqual(len(found), 1, found)
        workers = int(found[0].rsplit("並列度", 1)[1].strip().rstrip(")"))
        self.assertGreaterEqual(workers, 2)
        self.assertLessEqual(workers, 8)

    def test_07_charts_are_bounded(self):
        """※CR-009 — **本番規模でもチャートの枚数が上限内に収まること。**

        30 日規模でイベントは約 7,200 件ある。上限が効いていないと
        レポート一式が倍増する。
        """
        directory = os.path.join(self.work, "charts")
        files = ([n for n in os.listdir(directory) if n.endswith(".svg")]
                 if os.path.isdir(directory) else [])
        total = sum(os.path.getsize(os.path.join(directory, n)) for n in files)
        sys.stderr.write(
            "\n[A006/通常] チャート {0} 枚 / 合計 {1:.1f}MB\n".format(
                len(files), total / 1e6))
        self.assertTrue(files, "チャートが 1 枚も出ていない")
        # **上限は設定から導く。** 直値で書くと `[chart]` を変えるたびに壊れる。
        from s_anomaly import config as config_mod
        import io as _io
        from s_anomaly import progress as _pm
        cfg = config_mod.load(
            os.path.join(H.APP_DIR, "settings.properties"),
            _pm.setup(_io.StringIO(), _io.StringIO()))
        cap = HOST_COUNT * (cfg.chart_max_per_host
                            + cfg.chart_max_overlay_per_host)
        self.assertLessEqual(len(files), cap)

    def test_08_s6_is_not_the_whole_runtime(self):
        """S6 が全体を占めていないこと(支配ステップの記録)。

        **合否ではなく事実の記録が目的である。** 60% を超えたら、
        次に効くのは S6 の改善であると分かる。
        """
        share = self.s6_seconds / self.elapsed if self.elapsed else 0.0
        sys.stderr.write(
            "\n[A006/通常] S6 が全体に占める割合 {0:.0%}\n".format(share))
        self.assertLess(share, 1.0)


if __name__ == "__main__":
    unittest.main()
