# U010 — sar: sar(sysstat)の取り込み

* 対象CR: [CR-010](../P901-cr-direction/CR-010.md)
* 依存: U001(`schema` / `config` / `progress`)、U002(`discovery` / `loaders/__init__`)、U003(テストデータ生成)、U004(`metrics`)
* 入力: `docs/P001-requirement.md` 4.3a / 5.3a / FR-140〜FR-150、`docs/P003-backend-spec.md` 5.4a(DS-SAR-01〜08)/ 6.3a / DS-08-C1-01a / DS-02-05、`docs/P002-frontend-spec.md` 6.2.3a / 6.3 / UI-04-01a
* 関連ADR: **ADR-005**(`*_pct` は検知対象外 — **接頭 `pct_` には当てはまらない**)、**ADR-011**(一括挿入)、**ADR-016**(推定上限 — **sar では使わない**)、**ADR-019**(`ts` は `TIMESTAMP`)、ADR-022見込み / ADR-023見込み

**本書は「定義書 兼 実行指示書」である。** 記載の順に実装する。

---

## 1. タスク一覧

- [x] U010-T01 `schema.py` — `sar` テーブルの DDL と論理 PK
- [x] U010-T02 `discovery.py` — `KIND_SAR` と `^sa-(.+)-(\d{8})\.csv$`、件数ログ
- [x] U010-T03 `config.py` — `SOURCE_KINDS` に `sar`、`[sar] activities`
- [x] U010-T04 `loaders/sar.py`(新規) — ブロック判別・列名正規化・縦持ち展開・TZ 表記の検証
- [x] U010-T05 `metrics.py` — `sar` の合流と `DETECTABLE_METRICS` の拡張
- [x] U010-T06 `detectors/c1_saturation.py` — `SELF_RATIO_METRICS` と `NO_CEILING`
- [x] U010-T07 `events.py` — `extract_host` の書式追加
- [x] U010-T08 `reporter.py` — メトリクスの和名
- [x] U010-T09 `tools/gen_testdata.py` — `sa-{host}-{yyyymmdd}.csv` の生成と `expected.json`
- [x] U010-T10 単体テスト `tests/unit/test_loaders_sar.py` ほか

---

## 2. 実装するもの

| # | 対象 | 内容 |
| --- | --- | --- |
| 1 | `app/src/s_anomaly/loaders/sar.py`(**新規**) | M16。ブロック判別と縦持ち展開 |
| 2 | `app/src/s_anomaly/schema.py` | `sar` テーブルの DDL、`LOGICAL_KEYS`、列型 |
| 3 | `app/src/s_anomaly/discovery.py` | `KIND_SAR`、正規表現、件数ログの内訳 |
| 4 | `app/src/s_anomaly/config.py` | `SOURCE_KINDS` へ 1 行、`[sar] activities` |
| 5 | `app/src/s_anomaly/metrics.py` | `sar_long` CTE、`DETECTABLE_METRICS` |
| 6 | `app/src/s_anomaly/detectors/c1_saturation.py` | `SELF_RATIO_METRICS`、`NO_CEILING` に sar の 14 メトリクス |
| 7 | `app/src/s_anomaly/events.py` | `_SAR_RE` |
| 8 | `app/src/s_anomaly/reporter.py` | `METRIC_LABELS` に 22 件 |
| 9 | `app/src/s_anomaly/cli.py` | ローダの振り分けに `KIND_SAR` を追加 |
| 10 | `app/tools/gen_testdata.py` | ④ の生成 |
| 11 | `app/settings.properties` | `[timezone] sar`、`[sar] activities` |

---

## 3. ★最初に読む★ 静かに壊れる 3 箇所

**この 3 つは、実装しても例外が出ず、テストも「動いた」ように見えるまま機能が消える。**
`docs/CLAUDE.md` が記録するとおり、本リポジトリでは同じ型の欠陥が **既に 2 回**起きている
(CR-002 の原因候補 4 件、CR-009 の重ね合わせチャート)。

| # | 箇所 | 何が起きるか | 確認方法 |
| --- | --- | --- | --- |
| 1 | **`metrics.DETECTABLE_METRICS`** | ここに sar のメトリクスを入れ忘れると、**取り込みも `metrics` 構築も成功したまま、検知だけが 1 件も起きずに正常終了する。** `list_series` が `WHERE metric IN (...)` で絞っているため | **sar 由来のイベント件数が 1 件以上であることを検証する** |
| 2 | **`config.SOURCE_KINDS`** | ここに `sar` が無いと `[timezone] sar` が「未知のキー」として**警告だけ出して無視**される。時刻変換が行われず、**時差のある環境で全ての突き合わせが静かに外れる** | `[timezone] sar = Asia/Tokyo` を設定して、格納された `ts` が変換されていることを検証する |
| 3 | **`detectors/c1_saturation.py` の `NO_CEILING`** | `pct_idle` を除外し忘れると、**閑散なホストが全件「上限に張り付き」として検知される。** 誤検知は「動いている」ように見えるため気付きにくい | 99% 台で平坦な `pct_idle` の系列を入れて、**検知 0 件**であることを検証する |

**「例外が出ない」だけのテストを書かないこと。** 件数を検証する。

---

## 4. `loaders/sar.py` の実装

### 4.1 全体の形

```python
def load(con, logfile, progress, cfg=None) -> LoadResult:
    """`sa-{host}-{yyyymmdd}.csv` を読む (P003 5.4a)。"""
```

* **ホスト名は `logfile.host`(ファイル名由来)を使う。** データ行の第 1 列は読み捨てる(DS-SAR-06)。
* 既存 3 ローダと同じ `LoadResult`(`ok_rows` / `skipped_rows` / `errors`)を返す。
* 文字コード・改行の扱いは `loaders/__init__` の共通ユーティリティに従う(DS-LD-02 / DS-LD-03)。

### 4.2 ブロックの判別(DS-SAR-02)

**行が `#` で始まったら見出しである。** 見出しを解析して「今どの活動を読んでいるか」を切り替える。

```python
ACTIVITY_RULES = [
    # (activity, デバイス列名 or None, 判別に使う特徴列)
    ("cpu",        "CPU",   "%user"),
    ("memory",     None,    "kbmemfree"),
    ("swap_space", None,    "kbswpfree"),
    ("io",         None,    "rtps"),
    ("load",       None,    "runq-sz"),
    ("swapping",   None,    "pswpin/s"),
    ("disk",       "DEV",   "%util"),
    ("network",    "IFACE", "rxpck/s"),
]
```

* **デバイス列を持つ活動は、第 4 列がその名前であることも条件にする**(`disk` と `network` は
  どちらも `%util` に似た列を持ちうるため、デバイス列名で分ける)。
* **どれにも一致しない見出しは、そのブロックを読み飛ばす。**
  **同じ見出し文字列につき WARNING は 1 回だけ**にする(1 日分で 288 回現れるため)。
* **`[sar] activities` に含まれない活動も、見出しの時点で読み飛ばす**(DS-SAR-07)。
  データ行を解析してから捨てない。

### 4.3 列名の正規化(DS-SAR-04)

```python
def normalize(col):
    col = col.strip()
    if col.startswith("%"):
        col = "pct_" + col[1:]
    return col.replace("/", "_").replace("-", "_").lower()
```

**接頭 `pct_` と接尾 `_pct` を取り違えないこと。** 接尾 `_pct`(`ou_pct` など)は
本アプリが導出した使用率で**検知対象外**、接頭 `pct_`(`pct_util` など)は
sar が直接出す比率で**一次の検知対象**である(P001 FR-143)。

### 4.4 時刻とタイムゾーン(DS-SAR-05)

```python
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\s+(\S+))?$")
```

* 第 2 群が取れて `[timezone] sar` と**文字列として**異なれば、**ファイル単位で 1 回** WARNING。
* **表記が無ければ警告を出さない。**
* **変換は設定値を正として行う**(CR-008 の既存経路に載せる。ローダ側で変換しない)。

### 4.5 取り込む列(P001 FR-142)

```python
WANTED = {
    "cpu":        ["pct_user", "pct_system", "pct_iowait", "pct_idle"],
    "memory":     ["pct_memused", "pct_commit", "kbmemfree"],
    "swap_space": ["pct_swpused"],
    "io":         ["tps", "bread_s", "bwrtn_s"],
    "load":       ["runq_sz", "ldavg_1", "ldavg_5", "blocked"],
    "swapping":   ["pswpin_s", "pswpout_s"],
    "disk":       ["pct_util", "await", "tps"],
    "network":    ["pct_ifutil", "rxkb_s", "txkb_s"],
}
```

**見出しに無い列は静かに飛ばす**(版差で存在しないことがある)。**WARNING は出さない。**

**★`await` は Python の予約語である(3.7 以降)。★** メトリクス名としては
**文字列としてしか現れない**(辞書のキー・SQL の値)ため実害は無いが、
**識別子・属性名に使うと構文エラーになる**。`metrics.await` のような書き方をしないこと。
名前を `io_await` などに変えない理由は `docs/P011-impact-analysis-5.md` 2.2 の ★ACCEPTED★ にある。

---

## 5. 各所への追記

### 5.1 `schema.py`

```sql
CREATE TABLE IF NOT EXISTS sar (
    ts TIMESTAMP, host VARCHAR, activity VARCHAR,
    device VARCHAR, metric VARCHAR, value DOUBLE, _load_seq BIGINT
)
```

`LOGICAL_KEYS["sar"] = ["ts", "host", "activity", "device", "metric"]`(**5 列**。DS-LD-06a)

### 5.2 `metrics.py`

```sql
sar_long AS (
    SELECT 'sar/' || host || '/' || activity || '/' || device AS series_id,
           'sar' AS source, metric, ts, value, 0 AS segment
    FROM sar
)
```

`DETECTABLE_METRICS` に **22 件**を追加する(`WANTED` の全メトリクスの重複を除いた集合)。

### 5.3 `c1_saturation.py`

```python
SELF_RATIO_METRICS = frozenset([
    "pct_user", "pct_system", "pct_iowait", "pct_memused",
    "pct_commit", "pct_swpused", "pct_util", "pct_ifutil",
])
NO_CEILING["sar"] = ("pct_idle", "kbmemfree", "tps", "bread_s", "bwrtn_s",
                     "pswpin_s", "pswpout_s", "runq_sz", "ldavg_1", "ldavg_5",
                     "blocked", "await", "rxkb_s", "txkb_s")
```

`SELF_RATIO_METRICS` に入る系列は **`ceiling = 100.0`、`target_metric = 自分自身`、`estimated = False`**。
**`RATIO_OF` の経路(別メトリクスへの読み替え)は通さない。**

### 5.4 `events.py`

```python
_SAR_RE = re.compile(r"^sar/([^/]+)/.+$")
```

`extract_host` のループに加える。**`lsf_queue` と同じ形**であるため、
既存パターンより**後ろ**に置いても前に置いても結果は変わらない(接頭辞が違うため)。

---

## 6. 完了の判定

| # | 条件 |
| --- | --- |
| 1 | `python -m compileall -q app` が終了コード 0 |
| 2 | 単体テストが全件 PASS(sar のローダ・正規化・C1 の除外) |
| 3 | 結合テスト T009 が PASS |
| 4 | 受け入れテスト A014 が PASS |
| 5 | **`sa-*.csv` を含むデータで実行し、sar 由来のイベントが 1 件以上レポートに出ること**(★3 章の #1) |
| 6 | **`sa-*.csv` を除いたデータで実行し、CR-009 までと同じ結果になること**(後方互換) |
| 7 | 既存の全テストが従来どおり PASS すること |
| 8 | **テストスイートを続けて 2 回実行して同じ結果になること** |

---

# 追補 — NFS / NFSD の取り込み(※CR-011。2026-09-08)

* 対象CR: [CR-011](../P901-cr-direction/CR-011.md)
* 入力: `docs/P001-requirement.md` FR-142(改訂)/ FR-142a / FR-142b / FR-142c、`docs/P003-backend-spec.md` DS-SAR-02 の表 / **DS-SAR-02a**

**★新しいファイルもテーブルも作らない。★** `sar` テーブルは縦持ちであり(ADR-023)、
**活動種別の対応表を増やすだけで済む。** これが ADR-023 の狙いである。

## 1. タスク一覧

- [x] U010-T11 `loaders/sar.py` — `ACTIVITY_RULES` に `nfs` / `nfsd`、`WANTED` に 14 メトリクス
- [x] U010-T12 `config.py` — `SAR_ACTIVITIES` に `nfs` / `nfsd`
- [x] U010-T13 `metrics.py` — `DETECTABLE_METRICS` に 14 件
- [x] U010-T14 `detectors/c1_saturation.py` — `NO_CEILING["sar"]` に 14 件
- [x] U010-T15 `reporter.py` — `METRIC_LABELS` に 14 件
- [x] U010-T16 `tools/gen_testdata.py` — nfs / nfsd の生成と異常の埋め込み
- [x] U010-T17 `settings.properties` — `[sar] activities` の既定値
- [x] U010-T18 テスト — `TestNfsIsSkippedForNow` の**置き換え**、新規テスト、T009 / A014 の拡張

---

## 2. ★静かに壊れる 2 箇所(CR-010 と同じ型)★

| # | 箇所 | 何が起きるか | 確認方法 |
| --- | --- | --- | --- |
| 1 | **`metrics.DETECTABLE_METRICS`** | 14 件を入れ忘れると、**取り込みは成功したまま nfs / nfsd の検知だけが 0 件になる** | **`nfs` と `nfsd` のイベントがそれぞれ 1 件以上**あることを件数で検証する |
| 2 | **`config.SAR_ACTIVITIES`** | ここに `nfs` / `nfsd` が無いと、**ローダが見出しを判別できても `[sar] activities` の既定に含まれず読み飛ばされる。警告も出ない**(設定どおりの絞り込みに見えるため) | 既定設定で `sar` テーブルに `nfs` / `nfsd` の行が入ることを確認する |

**#2 は CR-010 には無かった経路である。** CR-010 では 8 活動すべてが既定に入っていたため、
「判別できる = 取り込まれる」だったが、**いまは判別と取り込みの間に設定の関門がある。**

---

## 3. `ACTIVITY_RULES` への追加

```python
("nfs",  None, "retrans_s"),   # -n NFS  : retrans/s は NFS 固有
("nfsd", None, "scall_s"),     # -n NFSD : scall/s   は NFSD 固有
```

**★判別列を弱いものに変えないこと。★**(DS-SAR-02a)

`-n NFS` は `read/s` / `write/s` / `access/s` を、`-n NFSD` は `sread/s` / `swrite/s` /
`saccess/s` を持つ。**接頭の `s` があるかどうかだけの違いである。**
正規化した名前の**完全一致**で判別しているため取り違えないが、
`in` による部分一致に変えると `read_s` が `sread_s` に一致して壊れる。

**`retrans_s` は NFS にしか、`scall_s` は NFSD にしか現れない。**

## 4. `WANTED` への追加

```python
"nfs":  ("call_s", "retrans_s", "read_s", "write_s", "access_s", "getatt_s"),
"nfsd": ("scall_s", "badcall_s", "hit_s", "miss_s",
         "sread_s", "swrite_s", "saccess_s", "sgetatt_s"),
```

**`packet_s` / `udp_s` / `tcp_s` は入れない**(FR-142b)。見出しには現れるが、
**取り込み対象でない列は静かに飛ばす**のが既存の設計である(DS-SAR-03)。

## 5. テストデータ(`gen_testdata.py`)

| 活動 | 生成するホスト | 理由 |
| --- | --- | --- |
| `nfs` | **3 ホストすべて** | どのホストも NFS クライアントである |
| `nfsd` | **`host02` のみ** | **NFS サーバは 1 台という想定。** 全て 0 の系列を 2 ホスト分増やしても情報が無い(FR-142c の経路は単体テストで確認する) |

**埋め込む異常**

| ID | 内容 | 狙い |
| --- | --- | --- |
| **INJ-013** | `sar/lsfhost01/nfs/-` の `retrans_s` が 6/9 12:00 以降 0.05 → 8〜15 へ跳ね上がり戻らない | **`lsfhost01` は LSF のジョブ滞留(INJ-006)と同じホストである。** 「NFS の詰まり → ジョブ滞留」という、依頼者が指摘した因果がレポート上で突き合わせられる |
| **INJ-014** | `sar/host02/nfsd/-` の `miss_s` が 6/11 以降に段階的に上昇 | **`nfsd` からもイベントが出ることを保証する** |

**★水準そのものを上げる。スパイクにしない。★** ベースラインが 0.05 前後と小さいため、
スパイクでは `effective_sigma` の下限(ADR-003)に埋もれる。**INJ-012 と同じ理由である。**

## 6. 既存テストの置き換え

**`app/tests/unit/test_loaders_sar.py` の `TestNfsIsSkippedForNow` は落ちる。これは正しい。**

**削除せず、「読み込むこと」を確かめるテストに置き換える。**
同じ見出し定数(`NFS_HEADER` / `NFSD_HEADER`)を使い、
**「読み飛ばす」→「取り込む」へ期待を反転させる。**

## 7. 完了の判定

| # | 条件 |
| --- | --- |
| 1 | `python -m compileall -q app` が終了コード 0 |
| 2 | 単体テストが全件 PASS |
| 3 | **`nfs` と `nfsd` のイベントがそれぞれ 1 件以上出ること**(★2 章の #1) |
| 4 | 既定設定で `sar` テーブルに `nfs` / `nfsd` の行が入ること(★2 章の #2) |
| 5 | **全て 0 の `nfsd` 系列で検知 0 件**であること(FR-142c) |
| 6 | **「未知の活動種別」の WARNING が NFS / NFSD について出なくなること** |
| 7 | T009 / A014 が PASS |
| 8 | 既存の全テストが従来どおり PASS すること |
| 9 | **テストスイートを続けて 2 回実行して同じ結果になること** |
