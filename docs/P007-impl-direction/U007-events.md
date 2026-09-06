あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U007 — events

**位置づけ**: 検知点(`Detection`)を、レポートに載せられる「イベント」に仕立てる。統合・脅威度・相関・原因候補の 4 者が絡む、本アプリで最も入り組んだスプリントである。

**重要な実行順序**: `docs/P003-backend-spec.md` DS-10-02 のとおり、**S7(統合・脅威度) → S8(相関) → S7'(原因候補)** の順で実行する。原因候補の判定に相関情報が必要なためである。`docs/P001-requirement.md` 7章の図とは順序が異なる(既知の不整合。`docs/P007-impl-direction.md` 5章 #1)。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。
* 中断からの再開時は、該当タスクの【完了条件】を実際に再実行して現状を確認すること。
* **先行実装の禁止**: `[ ]` の後続タスクが対象とするファイルには着手しない。

- [~] U007-T1 [検知点の統合とイベント化](#u007-t1-検知点の統合とイベント化) — events.py。近接統合・shape 優先順位・ID 採番 — **★CR-003によりID採番の変更が必要(U007-T6 で行う)**
- [x] U007-T2 [脅威度の判定](#u007-t2-脅威度の判定) — SEVERE の 4 条件とアルゴリズム数による引き上げ
- [~] U007-T3 [相関情報の収集](#u007-t3-相関情報の収集) — correlate.py。同一ホスト優先・最大 10 件 — **★CR-002により廃止。U007-T7 が置き換える**
- [~] U007-T4 [原因候補の付与](#u007-t4-原因候補の付与) — causes.py。CR-01〜CR-09 のルール表 — **★CR-002により判定の入力が変わる(U007-T8 で行う)**
- [~] U007-T5 [cli への結線](#u007-t5-cli-への結線) — S7 → S8 → S7' の順序で組み込む — **★CR-002・CR-003により順序が変わる(U007-T9 で行う)**
- [ ] U007-T6 [イベント ID にホスト名を含める](#u007-t6-イベント-id-にホスト名を含める) — **※CR-003**。`extract_host` を events.py へ移し、採番を「日 × ホスト」単位にする
- [ ] U007-T7 [同時に発生したアノマリーの突き合わせ](#u007-t7-同時に発生したアノマリーの突き合わせ) — **※CR-002**。co_anomaly.py を新設し correlate.py を廃止する
- [ ] U007-T8 [原因候補の入力を同時アノマリーへ切り替える](#u007-t8-原因候補の入力を同時アノマリーへ切り替える) — **※CR-002**。DS-10-06 の読み替え表に従う
- [ ] U007-T9 [cli の処理順を S7→S7'→S8→S8' に組み替える](#u007-t9-cli-の処理順を-s7s7s8s8-に組み替える) — **※CR-002・CR-003**

**★CR-001〜CR-004 による追加タスクである。** U007-T1〜T5 は初版の実装として完了しているが、
上記の CR により追加の作業が必要になったものを `[~]` に戻した(OKF 形式の規定)。
**T1〜T5 の指示本文は書き換えない。** 変更点は T6〜T9 に書く。

---

## U007-T1: 検知点の統合とイベント化

### 【目的】

* 同じ箇所を複数のアルゴリズムが検知した場合に 1 つのイベントへまとめる(FR-061)。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/events.py` (新規)
* `app/tests/unit/test_events_merge.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 8.1 DS-09-01 〜 DS-09-03、8.3 DS-09-07
* `docs/P002-frontend-spec.md` 4.2(イベント本文の項目仕様)
* `docs/P001-requirement.md` FR-061, FR-072, FR-073

### 【実装内容】

1. **`Event` を dataclass で定義する。**
   * `event_id: str`, `series_id: str`, `source: str`, `metric: str`, `algorithms: List[str]`, `start_ts: datetime`, `end_ts: datetime`, `values: List[float]`, `score: float`, `severity: str`, `shape: str`, `detail: Dict[str, Dict]`(アルゴリズム ID → その detail), `causes: List[Cause]`(T4 で埋める), `correlations: List[Correlation]`(T3 で埋める)
2. **`merge_detections(detections, cfg) -> List[Event]`** を作る(DS-09-01)。
   * **統合の単位は `(series_id, metric)`。** 異なるメトリクスは統合しない。
   * 同じ単位の検知点を `start_ts` 昇順に並べ、**区間が重なるか、間隔が `events.merge_gap_minutes`(既定 60)分以内**のものを 1 つにまとめる。
   * 統合の属性(DS-09-02):
     * `start_ts` = 構成する検知点の最小、`end_ts` = 最大
     * `algorithms` = 重複を除き **ID 昇順**
     * `score` = 最大値
     * `shape` = 3 の優先順位で 1 つ
     * `values` = **最も長い区間を持つ検知点のもの**(同点なら `algorithm` の ID 順で先のもの。決定性のため)
     * `detail` = `{algorithm_id: detail}` の辞書(全アルゴリズム分を保持)
3. **`shape` の優先順位**(DS-09-03)。上ほど優先。
   ```
   floor_rise > level_shift > trend_up > sustained > seasonal_dev > spike_up > spike_down
   ```
   * 観点2 の形状を観点1 より優先する。**この順序を変えてはならない。** `report.md` の「現象」欄の定型文がこれで決まる。
4. **イベント ID の採番**(DS-09-07、FR-072)。
   * 全イベントを **`(start_ts, series_id, metric)` で安定ソート**する。
   * `start_ts` の日付ごとに 001 から連番を振り、`EVT-{YYYYMMDD}-{NNN}` とする。
   * **ソートキーに `series_id` と `metric` を含めるのは、同一時刻のイベントの順序を決定的にするため**(NFR-009)。省略してはならない。
5. **`sort_for_report(events) -> List[Event]`** を作る(FR-073)。**脅威度の降順、次いで開始時刻の昇順**で並べる。
   * 脅威度の順序は `SEVERE > FATAL > WARN > INFO`。
   * 同一脅威度・同一時刻のときは `event_id` 昇順でタイブレークする(決定性)。
   * **この関数はレポート出力用であり、ID 採番のソート(4)とは別物である。** ID は時刻順で採番し、表示は脅威度順にする。

### 【実装してはいけないこと】

* 脅威度の判定(T2 の担当)。この時点では `severity` を空文字または仮の値にしておく。
* 相関・原因候補(T3・T4 の担当)。
* 異なる `series_id` や異なる `metric` の検知点を統合すること。
* ID 採番を表示順(脅威度順)で行うこと(**時刻順で採番する**)。

### 【Unit Test内容】

* **テスト対象**: `merge_detections`, `sort_for_report`, `Event`
* **正常系テスト**
  * 同一 `(series_id, metric)` で **時刻が重なる 3 つの検知**(A1, A2, A3)が **1 件のイベント**にまとまり、`algorithms == ["ALG-A1","ALG-A2","ALG-A3"]`(ID 昇順)であること。
  * 間隔が 30 分(< 60)の 2 検知がまとまること。**間隔が 120 分(> 60)の 2 検知はまとまらず 2 件**であること。
  * 異なる `metric` の検知が統合されないこと。
  * `score` が構成検知点の最大値であること。
  * **`shape` の優先順位**: `spike_up` と `floor_rise` が統合されたとき `floor_rise` になること。7 種すべてについて表駆動で優先順位を確認すること。
  * イベント ID が `EVT-YYYYMMDD-NNN` の書式であり、同一日で 001, 002, ... と連番になること。
  * **同一時刻・異なる系列の 2 イベントで、ID の付き方が 2 回実行しても同一であること**(決定性)。
  * `sort_for_report` が脅威度降順・開始時刻昇順であること。
* **主要な異常系テスト**
  * 検知点が 0 件のとき空リストが返ること。
  * 検知点が 1 件のとき 1 イベントになり、`algorithms` が 1 要素であること。
  * `merge_gap_minutes = 0` のとき、区間が重なるものだけがまとまること。
  * `start_ts == end_ts`(単点検知)が複数あるときも正しく統合されること。
  * 日をまたぐイベント(`start_ts` が 6/1、`end_ts` が 6/3)の ID が **`start_ts` の日付**で採番されること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U007-T2: 脅威度の判定

### 【目的】

* `docs/P001-requirement.md` 11章の 4 段階(INFO/WARN/FATAL/SEVERE)を判定する。
* **`docs/P002-frontend-spec.md` 9.1 #3・#4 が指摘した「単位差」「上限不明」の問題に対する回答**を実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/events.py` (編集)
* `app/tests/unit/test_events_severity.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 8.2 DS-09-04 〜 DS-09-06(**特に DS-09-05 の SEVERE 条件の表**)
* `docs/P001-requirement.md` FR-060, FR-062、11章
* `docs/P002-frontend-spec.md` 9.1 #3, #4

### 【実装内容】

1. **`assign_severity(events, con, cfg) -> None`** を作る。各イベントの `severity` を決める。
2. **判定の順序**(DS-09-04)。最初に該当したものを採る。
   ```
   1. SEVERE 条件に該当           -> SEVERE
   2. score >= 2.0                -> FATAL
   3. score >= 1.0                -> WARN
   4. それ以外                    -> INFO
   その後: len(algorithms) >= 3 なら 1 段階引き上げ(上限 SEVERE)   [FR-062]
   ```
3. **SEVERE 条件**(DS-09-05)。**観点2 の形状(`floor_rise` / `trend_up` / `level_shift`)であることが前提**。

   | 対象 | SEVERE の条件 |
   | --- | --- |
   | `jvm_gc` の `ou`, `eu`, `mu` | **同一系列・同一時間帯の対応する `*_pct` メトリクスの最大値が `events.severe_pct`(既定 90)を超える** |
   | 同上で `*_pct` が取得できない(NULL のみ) | **SEVERE にしない**(最大 FATAL) |
   | `lsf_queue` の `pend`, `susp` | **期間の末尾 10% の区間で、系列全体の最大値を更新している** |
   | `db_connection` の `active_connections` | 同上 |
   | `jvm_gc` の `fgc_delta`, `fgct_delta`, `ygct_delta` | **SEVERE にしない** |
   | `lsf_queue` の `njobs`, `run` | **SEVERE にしない** |

4. `*_pct` の参照は、`metrics` テーブルへの SQL で行う。
   ```sql
   SELECT max(value) FROM metrics
   WHERE series_id = ? AND metric = ? AND ts BETWEEN ? AND ?
   ```
   * `metric` は `ou` → `ou_pct`、`eu` → `eu_pct`、`mu` → `mu_pct` のマッピング。
   * 結果が NULL(行が無い)なら SEVERE にしない。
5. 「末尾 10% で最大値を更新している」の判定は次で行う。
   ```sql
   SELECT max(value) FILTER (WHERE ts >= ?) AS tail_max,   -- 末尾 10% の開始時刻
          max(value) AS all_max
   FROM metrics WHERE series_id = ? AND metric = ?
   ```
   * `tail_max >= all_max` なら「更新中」とみなす(等号を含める。最大値が末尾にあれば増加が止まっていない)。
   * 末尾 10% の開始時刻は、系列全体の期間の 90% 経過点。
6. **効率**: イベントごとに個別クエリを投げると遅い。**イベント数が多い場合に備え、必要な情報(系列ごとの `all_max` と `tail_max`、`*_pct` の期間最大)を 1 回のクエリでまとめて取得**し、辞書に持ってから判定すること(DS-11-05 と同じ考え方)。
7. `SEVERITY_ORDER = ["INFO", "WARN", "FATAL", "SEVERE"]` を定義し、引き上げは添字 +1(上限あり)で行う。

### 【実装してはいけないこと】

* `docs/P003-backend-spec.md` DS-09-05 の表に無いメトリクスを SEVERE の対象にすること。
* `*_pct` が取得できないときに `ou`(KB 値)を 90 と比較すること(**単位が違う。KB 値が 90 を超えるのは常に真になり、全件 SEVERE になる**)。
* 引き上げ(FR-062)を SEVERE 判定の**前**に行うこと(順序は DS-09-04 のとおり後)。

### 【Unit Test内容】

* **テスト対象**: `assign_severity`
* **正常系テスト**
  * `score = 1.5`、アルゴリズム 1 個、`shape = spike_up` → `WARN`。
  * `score = 2.5`、アルゴリズム 1 個 → `FATAL`。
  * `score = 0.5` → `INFO`。
  * **アルゴリズム 3 個で `score = 1.5` → `FATAL`**(WARN から 1 段階引き上げ。FR-062)。
  * **アルゴリズム 3 個で `score = 2.5` → `SEVERE`**(FATAL から引き上げ)。
  * アルゴリズム 2 個では引き上げが起きないこと。
* **SEVERE 条件のテスト(DS-09-05 の 6 行すべてを表駆動で確認する)**
  * `jvm_gc`/`ou`、`shape = floor_rise`、`ou_pct` の最大が 95 → **SEVERE**。
  * 同条件で `ou_pct` の最大が 85 → SEVERE にならない(`FATAL` または `WARN`)。
  * **同条件で `ou_pct` の行が存在しない(`-gc` 形式で `oc` が NULL)→ SEVERE にならない**。これが `docs/P002-frontend-spec.md` 9.1 #3 への回答の実証である。
  * `lsf_queue`/`pend`、`shape = trend_up`、**末尾 10% に最大値がある** → **SEVERE**。
  * 同条件で最大値が系列の中央にある → SEVERE にならない。
  * `db_connection`/`active_connections`、`shape = floor_rise`、末尾に最大値 → **SEVERE**。これが `docs/P002-frontend-spec.md` 9.1 #4 への回答の実証である。
  * **`jvm_gc`/`fgc_delta` は、`shape = trend_up` で `score` が高くても SEVERE にならない**こと。
  * **`lsf_queue`/`run` も SEVERE にならない**こと。
  * **`shape = spike_up`(観点1)のときは、どのメトリクスでも SEVERE にならない**こと。
* **主要な異常系テスト**
  * `metrics` に該当系列が無いとき、例外にならず SEVERE にもならないこと。
  * 系列が 1 点のみのとき、末尾 10% の判定で例外にならないこと。
  * `severe_pct` を 0 に設定したとき、`*_pct` を持つ全イベントが SEVERE になること(設定が効いていることの確認)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U007-T3: 相関情報の収集

### 【目的】

* 各イベントについて「同時刻のその他のデータ」を収集する(FR-075、FR-076)。
* **この結果は T4 の原因候補の判定にも使われる**(DS-10-02)。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/correlate.py` (新規)
* `app/tests/unit/test_correlate.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 10章 DS-11-01 〜 DS-11-05
* `docs/P002-frontend-spec.md` 4.2(同時刻のその他のデータの行)、UI-04-02、6.3(`series_id` の書式)
* `docs/P001-requirement.md` FR-075, FR-076

### 【実装内容】

1. **`Correlation` を dataclass で定義する**: `series_id: str`, `metric: str`, `label: str`(表示用。例 `jvm_gc / fgc_delta (同一ホスト)`), `cur_avg: float`, `cur_max: float`, `base_avg: Optional[float]`, `change_ratio: Optional[float]`, `significant: bool`, `note: str`。
2. **`extract_host(series_id) -> Optional[str]`** を作る。`docs/P002-frontend-spec.md` 6.3 の 3 書式からホスト名を抜く。
   * `db_connection/{host}:{port}/{ds}` → `host`
   * `jvm_gc/{container}@{host}` → `host`
   * `lsf_queue/{host}/{queue}` → `host`
   * **書式が一致しない場合は `None`**(例外にしない)。
3. **`collect(events, con, cfg) -> None`** を作り、各イベントの `correlations` を埋める。
   * 対象期間は `[start_ts - W, end_ts + W]`、`W = events.merge_gap_minutes`(既定 60 分)(DS-11-01)。
   * **基準期間**は「対象期間の直前 7 日間の同じ時刻帯」(DS-11-02、FR-075)。実装は、対象期間を 1 日ずつ 7 回さかのぼった 7 つの区間の和集合とする。
     * 基準期間のデータが不足する(系列の先頭に近い)場合は、**得られる範囲で計算し、`note` に「基準期間のデータが N 日分のみ」と記録する**。1 日分も無ければ `base_avg = None`、`change_ratio = None` とし、`note` に「比較対象の平常時データがありません」と記録する。
   * **相関先の選択順**(DS-11-03、FR-076):
     1. 同一ホストの他メトリクス(`extract_host` が一致し、`(series_id, metric)` が自分自身でないもの)
     2. 同一時刻の他ホストの同一メトリクス
     3. 10 件に満たなければそこで打ち切る
   * 各相関先について、期間平均・期間最大・基準期間比の変化率を求める。
   * **`significant`** は「期間平均が基準期間平均から `effective_sigma` の 1 倍以上離れている」こと(DS-10-04)。`effective_sigma` は基準期間の平均・標準偏差から求める。
   * **変化率の絶対値が大きい順**に最大 10 件を採る(DS-11-04)。ただし `significant` なものを優先して 10 件を埋める。
   * **変化がないものも出力対象に含める**(`docs/P002-frontend-spec.md` UI-04-02。項目を省略しない)。ただし優先度は低い。
4. **効率**(DS-11-05): イベントごとに個別クエリを投げず、**全イベントの期間を一時テーブルにして 1 回の結合で集計**する。
   ```sql
   CREATE OR REPLACE TEMP TABLE _event_windows(event_id VARCHAR, t_from TIMESTAMP, t_to TIMESTAMP);
   -- 期間ごとの集計を 1 回の JOIN で取る
   SELECT w.event_id, m.series_id, m.metric, avg(m.value) AS cur_avg, max(m.value) AS cur_max
   FROM _event_windows w JOIN metrics m ON m.ts BETWEEN w.t_from AND w.t_to
   GROUP BY 1, 2, 3
   ```
   * 基準期間の集計も同様に 1 回で取る。
5. **`*_pct` メトリクスも相関先の候補に含める**(原因候補ルール CR-06 が `ou_pct` の高止まりを見るため)。

### 【実装してはいけないこと】

* 原因候補の判定(T4 の担当)。
* イベント数 × メトリクス数のクエリを発行すること(**NFR-002 を満たせない**)。
* 相関先に自分自身(同じ `series_id` かつ同じ `metric`)を含めること。
* `series_id` の書式を仮定せずに文字列を分割すること(**6.3 の 3 書式に従う**)。

### 【Unit Test内容】

* **テスト対象**: `extract_host`, `collect`
* **正常系テスト**
  * `extract_host` が 3 書式それぞれから正しいホスト名を返すこと。
    * `db_connection/host01:7003/OraclePool_1` → `host01`
    * `jvm_gc/app01@host01` → `host01`
    * `lsf_queue/lsfhost01/normal` → `lsfhost01`
  * 同一ホストに複数メトリクスがある `metrics` を作り、イベントの `correlations` に**同一ホストのものが先に**入ること。
  * `cur_avg` / `cur_max` が期間内の値と一致すること。
  * 基準期間(7 日前の同時刻帯)に平常値を置き、イベント期間で値を大きくしたとき、`change_ratio` が正しく計算され `significant == True` になること。
  * 変化のないメトリクスで `significant == False` になること。
  * 相関先が **最大 10 件**に制限されること(15 メトリクスあるデータで確認)。
  * 相関先に自分自身が含まれないこと。
  * `ou_pct` が相関先の候補に含まれること。
* **主要な異常系テスト**
  * `extract_host` が、書式に合わない文字列(`foo`, `bar/baz`)に対して `None` を返し、例外にならないこと。
  * **基準期間のデータが 1 日分も無い**(系列の先頭のイベント)とき、`base_avg is None`、`change_ratio is None`、`note` に説明が入り、**例外にならない**こと。
  * 基準期間が 3 日分しかないとき、`note` に「3 日分のみ」相当の記録が入ること。
  * 相関先が 1 つも無い(単一メトリクスしかない)とき、`correlations` が空リストで例外にならないこと。
  * イベントが 0 件のとき例外にならないこと。
  * 基準期間の標準偏差が 0 のとき、`effective_sigma` の下限が効いてゼロ除算にならないこと。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U007-T4: 原因候補の付与

### 【目的】

* `docs/P003-backend-spec.md` DS-10-03 の 9 ルール(CR-01〜CR-09)を実装する。
* **本アプリは LLM を呼べない**(閉域環境)ため、原因候補はルールベースで生成する(FR-074)。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/causes.py` (新規)
* `app/tests/unit/test_causes.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 9章 DS-10-01 〜 DS-10-05(**ルール表**)
* `docs/P002-frontend-spec.md` 4.2(原因候補の行)、UI-04-02
* `docs/P001-requirement.md` FR-074、12.1

### 【実装内容】

1. **`Cause` を dataclass で定義する**: `rule_id: str`, `cause: str`, `confidence: str`(`"高"`/`"中"`/`"低"`), `reason: str`。
2. **`CauseRule` を dataclass で定義する**(DS-10-01): `id`, `cause`, `confidence`, `reason_template`, `match`(関数)。
3. **9 ルールを宣言的に定義する。** `docs/P003-backend-spec.md` DS-10-03 の表をそのまま写す。ハードコードした `if` の羅列にしないこと。

   | ID | 原因候補 | 確度 | 条件 |
   | --- | --- | --- | --- |
   | CR-01 | メモリリーク | 高 | `metric == "ou"` かつ `shape == "floor_rise"` かつ 相関に `fgc_delta` の有意な増加あり かつ `eu` に有意な変化なし |
   | CR-02 | 負荷増に伴う正常な増加 | 中 | `metric in ("ou","eu")` かつ `shape in ("trend_up","floor_rise")` かつ 相関に `active_connections` または `run` の有意な増加あり |
   | CR-03 | コネクションリーク(クローズ漏れ) | 高 | `source == "db_connection"` かつ `shape == "floor_rise"` |
   | CR-04 | 計算資源の枯渇、またはジョブのスタック | 高 | `metric == "pend"` かつ `shape in ("trend_up","floor_rise")` かつ 相関の `run` が横ばい |
   | CR-05 | クラスローダリーク(動的クラス生成) | 中 | `metric == "mu"` かつ `shape in ("trend_up","floor_rise")` |
   | CR-06 | ヒープ不足による Full GC 頻発 | 高 | `metric in ("fgct_delta","fgc_delta")` かつ 相関の `ou_pct` の期間平均が `severe_pct` を超える |
   | CR-07 | 一時的な負荷スパイク、または採取タイミングの揺れ | 低 | `shape in ("spike_up","spike_down")` かつ 相関に有意な変化なし |
   | CR-08 | 設定変更・負荷段階の変化 | 中 | `shape == "level_shift"` |
   | CR-09 | 定期的な処理による周期的な負荷 | 低 | `shape == "seasonal_dev"` |

4. **「有意な変化」の定義**(DS-10-04): 相関先の `significant == True` かつ `change_ratio > 0`(増加の場合)。「横ばい」はその否定。**T3 が算出済みの `significant` をそのまま使う**こと。独自に再計算しない。
5. **`assign_causes(events, cfg) -> None`** を作る。
   * 各イベントについて全ルールを評価し、該当したものを `causes` に入れる。
   * **確度の高い順(高 → 中 → 低)、同確度なら ID 順**に並べる(DS-10-05)。
   * **該当が 0 件のとき、`causes` を空リストにする。** 定型文 `該当する候補なし (確度: —)` の出力は U008 `reporter` の責務とする(`docs/P002-frontend-spec.md` UI-04-01)。
6. `reason` は `reason_template` に実測値を埋めて作る。例: `"ou が単調増加し、fgc_delta も +1300% 増加、eu に有意な変化がないため"`。

### 【実装してはいけないこと】

* 確度を百分率で表現すること(**高/中/低の 3 段階**。`docs/P001-requirement.md` 12.1 で確定済み)。
* LLM の呼び出し(閉域環境で不可能)。
* DS-10-03 の 9 ルール以外のルールを追加すること。
* 相関情報を使わずに原因候補を判定すること(**T3 の結果が入力である**)。

### 【Unit Test内容】

* **テスト対象**: `CAUSE_RULES`, `assign_causes`
* **正常系テスト**
  * `CAUSE_RULES` の要素数が **9** であり、ID が `CR-01`〜`CR-09` と完全一致すること。
  * 各ルールの `confidence` が `高`/`中`/`低` のいずれかであること。
  * **9 ルールそれぞれについて、そのルールだけが発火するイベント + 相関の組み合わせを作り、期待するルールが引き当てられることを表駆動で確認する。** これが本タスクの中心的な確認である(`docs/P006-test-plan.md` F-15、TP-17)。
    * CR-01: `metric="ou"`, `shape="floor_rise"`, 相関に `fgc_delta`(significant, 増加)と `eu`(not significant)
    * CR-03: `source="db_connection"`, `shape="floor_rise"`
    * CR-04: `metric="pend"`, `shape="trend_up"`, 相関の `run` が not significant
    * CR-06: `metric="fgct_delta"`, 相関の `ou_pct` の `cur_avg` が 95
    * ...(9 件すべて)
  * **複数ルールが該当するとき、確度の高い順に並ぶこと。** 例: `metric="ou"`, `shape="floor_rise"` で CR-01(高)と CR-02(中)が両方該当するデータを作り、CR-01 が先に来ること。
  * 同確度が複数のとき ID 順であること。
* **主要な異常系テスト**
  * **どのルールにも該当しないイベント**(例: `metric="njobs"`, `shape="sustained"`, 相関なし)で `causes` が空リストになり、例外にならないこと。
  * `correlations` が空のイベントで、相関を参照するルール(CR-01, CR-02, CR-04, CR-06)が発火せず例外にもならないこと。
  * `change_ratio` が `None`(基準期間なし)の相関先があっても例外にならないこと。
  * イベントが 0 件のとき例外にならないこと。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。9 ルールすべてに対応するテストが存在すること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U007-T5: cli への結線

### 【目的】

* S7 → S8 → S7' の順序で `cli` に組み込む。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/cli.py` (編集)
* `app/tests/unit/test_cli_events.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` DS-10-02(**実行順序**)
* `docs/P002-frontend-spec.md` 3.3(S7・S8 の進捗ログ)
* `docs/P007-impl-direction.md` 5章 #1(P001 の図との既知の不整合)

### 【実装内容】

1. `cli.main` に次を組み込む。**順序を厳守すること。**
   ```
   S7  : events.merge_detections(detections, cfg)
         events.assign_severity(events, con, cfg)
   S8  : correlate.collect(events, con, cfg)
   S7' : causes.assign_causes(events, cfg)
   ```
   * **`assign_causes` を `collect` より前に呼んではならない。** 相関情報が空のまま判定され、CR-01/02/04/06 が永久に発火しなくなる。
2. 進捗ログ(`docs/P002-frontend-spec.md` 3.3)。
   * S7: INFO `検知点 {N} 点を {M} 件のイベントに統合しました (SEVERE: a, FATAL: b, WARN: c, INFO: d)`
   * S8: INFO `相関情報の収集が完了しました ({M} 件のイベント)`
3. イベントを `sort_for_report` で並べ替えて保持する。
4. この時点では S9(レポート生成)が未実装なので、S7' のあと 0 を返す。

### 【実装してはいけないこと】

* レポート生成(U008 の担当)。
* 実行順序を `docs/P001-requirement.md` 7章の図(S7 → S8)どおりにすること。**P003 DS-10-02 が正である。**

### 【Unit Test内容】

* **テスト対象**: `cli.main`(S7' まで)
* **正常系テスト**
  * 生成テストデータに対して `main` が 0 を返し、S7・S8 の進捗ログが出ること。
  * 脅威度別の内訳がログに含まれること。
  * **イベントの `causes` が 1 件以上あるイベントが存在すること**(相関が正しく先に埋まっている証拠)。
* **主要な異常系テスト(重要)**
  * **`causes.assign_causes` を、`correlations` が空の状態で呼んだ場合と、埋まった状態で呼んだ場合で結果が異なることを確認するテスト**を書く。これにより、順序が逆になったときにテストが落ちるようにする。
    * 具体的には、`correlate.collect` をモンキーパッチで無効化して `main` を実行し、CR-01 が 1 件も出ないことを確認する。通常実行では CR-01 相当が出ることと対比する。
  * 検知が 0 件のとき、イベント 0 件で `main` が 0 を返すこと。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
python -m unittest discover -s app/tests/integration -t app -v
```

### 【完了条件】

* 単体テスト・結合テストがともに `OK` で終わること。
* **手動で確認する**: `python app/s_anomaly.py app/tests/_work/normal/logs` を実行し、S7 でイベント統合の内訳(SEVERE/FATAL/WARN/INFO の件数)が表示され、S8 が完了し、終了コード 0 になること。
* U007 の全タスク(T1〜T5)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U007 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* **全イベントが同じ脅威度になる(例: 全件 SEVERE、全件 INFO)場合は、判定ロジックの誤りを疑って停止し報告する。** 特に「`*_pct` が取れないときに KB 値を 90 と比較していないか」を確認すること。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件に該当しない限り、次のタスクに自動的に進んでください。

---

## U007-T6: イベント ID にホスト名を含める

### 【目的】

イベント ID を `EVT-{YYYYMMDD}-{ホスト名}-{連番3桁}` にする(CR-003)。
レポートをホスト別に分割したあと、**ID からどのファイルのイベントかを判別できるようにする**ため。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/events.py`(編集。`assign_event_ids` と、新設する `extract_host`)
* `app/src/s_anomaly/correlate.py`(**参照のみ**。`extract_host` の実装を移す元)

### 【参照すべき仕様箇所】

* `docs/P001-requirement.md` FR-072
* `docs/P002-frontend-spec.md` UI-04-01(イベントIDの行)
* `docs/P003-backend-spec.md` DS-09-07、**DS-09-08**

### 【実装内容】

1. **`extract_host(series_id)` を `events.py` に移す。** 現在は `correlate.py` にある。
   * `correlate.py` は U007-T7 で廃止するため、**採番が廃止予定のモジュールに依存してはならない**。
   * 3 書式(`db_connection` / `jvm_gc` / `lsf_queue`)への対応は現行の実装をそのまま使う。
   * **いずれにも一致しない場合は `"unknown"` を返す。** 現行は `None` を返すため、**ここは変更する**。
2. `assign_event_ids` の採番キーを `day` から **`(day, host)`** に変える。

```python
def assign_event_ids(events):
    """EVT-{YYYYMMDD}-{host}-{連番3桁} を採番する (DS-09-07)。"""
    counters = {}
    for event in sorted(events, key=lambda e: (e.start_ts, e.series_id, e.metric)):
        day = event.start_ts.strftime("%Y%m%d")
        host = extract_host(event.series_id)
        key = (day, host)
        counters[key] = counters.get(key, 0) + 1
        event.event_id = "EVT-{0}-{1}-{2:03d}".format(day, host, counters[key])
```

* **ソートキーは変えない。** `(start_ts, series_id, metric)` の安定ソートが再現性(NFR-009)を担保している。
* **連番は「日 × ホスト」ごとに 1 から振る。**

### 【実装してはいけないこと】

* ホスト名の切り詰め・正規化。**ID には抽出したホスト名をそのまま入れる**(ファイル名側の安全化は U008 で行う)。
* `correlate.py` の `extract_host` をこの時点で消すこと(T7 で `correlate.py` ごと消す)。

### 【Unit Test内容】

`app/tests/unit/test_events.py` に追加する。

1. `extract_host` が 3 書式それぞれから正しいホスト名を返すこと。
2. `extract_host` が未知の書式に対して `"unknown"` を返すこと。
3. ID が `EVT-{YYYYMMDD}-{host}-{NNN}` の形式であること。
4. **同じ日の異なるホストで、連番がそれぞれ 001 から始まること。**
5. **同じ日の同じホストでは連番が 001, 002, … と続くこと。**
6. 日をまたぐと連番がリセットされること。
7. 同一入力を 2 回処理して ID が完全に一致すること(再現性)。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -p "test_events.py"
```

### 【完了条件】

* 上記の単体テストが全件成功する。
* `python -m compileall -q app` が終了コード 0。

---

## U007-T7: 同時に発生したアノマリーの突き合わせ

### 【目的】

`correlate.py`(`metrics` を参照して他系列の平常時統計を集める処理)を廃止し、
**検知済みイベント同士を区間の重なりで突き合わせる** `co_anomaly.py` に置き換える(CR-002)。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/co_anomaly.py`(**新規**)
* `app/src/s_anomaly/correlate.py`(**削除**)

### 【参照すべき仕様箇所】

* `docs/P001-requirement.md` FR-075、FR-076
* `docs/P002-frontend-spec.md` **4.5 全体**(UI-04-C01〜C07)
* `docs/P003-backend-spec.md` **10章全体**(DS-11-01〜DS-11-08)

### 【実装内容】

1. **`is_point_anomaly(event)`** — 統合されたアルゴリズムが `ALG-A*` のみなら真(DS-11-02)。
2. **`collect(events)`** — 観点1 のイベントだけを取り出し、掃引法で重なるペアを列挙する(DS-11-04)。
3. 各イベントについて、相手を **`(同一ホストか, -脅威度, -重なり秒数, 相手のevent_id)`** で整列し、**先頭 10 件**を採る(DS-11-05)。
4. 結果は `event.co_anomalies` に入れる。要素は相手のイベント ID・ホスト・source・metric・脅威度・重なり秒数を持つ。
5. **観点2 を含むイベントには `co_anomalies` を設定しない**(`None` のままにする)。
   レポート側が「項目行ごと省略する」か「該当なしと書く」かを区別できるようにするため、
   **「観点1 だが相手が 0 件」は空リスト、「観点2 を含む」は `None`** とする。

### 【実装してはいけないこと】

* **`metrics` テーブルへのクエリ。** 本モジュールは DuckDB コネクションを受け取らない。
* 全ペアの総当たり(O(n²))。**掃引法を使う**。
* 浮動小数点の値をソートキーに使うこと。**再現性を壊す**(DS-11-05)。
* `events.merge_gap_minutes` を判定に使うこと。**区間の重なりのみで判定する**(DS-11-03)。

### 【Unit Test内容】

`app/tests/unit/test_co_anomaly.py`(**新規**)。

1. 区間が重なる 2 件が互いの相手になること。
2. 区間が接しない 2 件が相手にならないこと。
3. **端点が一致するだけ(`a_end == b_start`)の場合は重なりと判定すること**(DS-11-03 の不等号)。
4. 単点どうしが同一時刻のとき重なりと判定され、重なり秒数が 0 になること。
5. 観点2 のみのイベントに `co_anomalies` が設定されない(`None` である)こと。
6. **観点1 と観点2 の混在イベントにも設定されない**こと。
7. 観点1 だが相手が無いイベントで、空リストになること。
8. 相手が 11 件以上あるとき 10 件に切られること。
9. 並び順が「同一ホスト優先 → 脅威度 → 重なり時間」であること。
10. 上位 3 キーが同値のとき、イベント ID の昇順で決まること(再現性)。
11. 同一入力を 2 回処理して結果が完全一致すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -p "test_co_anomaly.py"
```

### 【完了条件】

* 上記の単体テストが全件成功する。
* `app/src/s_anomaly/correlate.py` が存在しない。
* `grep -rn "correlate" app/src/` が 0 件。

---

## U007-T8: 原因候補の入力を同時アノマリーへ切り替える

### 【目的】

`causes.py` の判定条件を、相関の統計値から**同時に発生したアノマリー**へ読み替える(CR-002)。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/causes.py`(編集)

### 【参照すべき仕様箇所】

* `docs/P001-requirement.md` FR-074、12.1 のルール表
* `docs/P003-backend-spec.md` **DS-10-06 の読み替え対応表**(必読)、DS-10-02

### 【実装内容】

**DS-10-06 の表に従って、CR-01 / CR-02 / CR-04 / CR-06 / CR-07 の 5 ルールの条件を書き換える。**
CR-03 / CR-05 / CR-08 / CR-09 は相関を参照していないため**変更しない**。

* 「上方向のイベント」の判定は、`shape` が `spike_up` / `trend_up` / `floor_rise` / `level_shift` のいずれか。
* **`co_anomalies` が `None`(観点2 を含むイベント)の場合、同時アノマリーを参照するルールは成立しない**ものとして扱う。

### 【実装してはいけないこと】

* ルールの追加・削除。**5 ルールの条件の読み替えだけ**を行う。
* 確度(高/中/低)の変更。

### 【Unit Test内容】

`app/tests/unit/test_causes.py` を更新する。**CR-01〜CR-09 の 9 ルールすべてについて、
発火する場合と発火しない場合の両方**を確認する(既存の観点を維持する)。

### 【完了条件】

* 単体テストが全件成功する。

---

## U007-T9: cli の処理順を S7→S7'→S8→S8' に組み替える

### 【目的】

イベント ID の採番を、同時アノマリーの突き合わせより**前**に確定させる(CR-002・CR-003)。
同時アノマリー欄が相手の ID を出力するため。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/cli.py`(編集)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` **DS-11-06 の処理順の表**

### 【実装内容】

| ステップ | 内容 | 進捗ログのラベル |
| --- | --- | --- |
| S7 | 検知点をイベントへ統合し脅威度を決める | `S7` |
| S7' | **イベント ID を採番する** | `S7` |
| S8 | 同時に発生したアノマリーを突き合わせる | `S8` |
| S8' | 原因候補を付与する | `S8` |
| S9 | レポート群を出力する | `S9` |

* `correlate.collect(events, con, cfg)` の呼び出しを **`co_anomaly.collect(events)`** に置き換える。
  **DuckDB コネクションと設定を渡さない。**
* 進捗ログの文言を「相関情報の収集が完了しました」から「同時に発生したアノマリーを突き合わせました」に変える。

### 【実装してはいけないこと】

* 検知(S6)以前の処理順の変更。

### 【完了条件】

* `python -m unittest discover -s app/tests/unit -t app` が全件成功する。
* `python -m compileall -q app` が終了コード 0。
