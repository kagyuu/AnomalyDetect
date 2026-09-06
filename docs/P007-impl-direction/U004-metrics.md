あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U004 — metrics

> ★訂正 2026-09-06★ **本書は Plan Loop 時点(アルゴリズム 10 個)の計画である。**
> **現在は 11 個**である(CR-005 で観点3 の ALG-C1 を追加)。
> 追加分の実装指示は `docs/P007-impl-direction/U009-detectors-c.md` にある。
> 現行の仕様は `docs/P001-requirement.md` 10章 / `docs/P003-backend-spec.md` 7章 が正である。


**位置づけ**: 3 種のテーブルを、10 個のアルゴリズムが**ログ種別を意識せず一様に扱える形**へ変換する。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。
* 中断からの再開時は、該当タスクの【完了条件】を実際に再実行して現状を確認すること。
* **先行実装の禁止**: `[ ]` の後続タスクが対象とするファイルには着手しない。

- [x] U004-T1 [系列分割と累積値の差分](#u004-t1-系列分割と累積値の差分) — segment の算出と GC カウンタの差分化
- [x] U004-T2 [縦持ち変換と使用率の導出](#u004-t2-縦持ち変換と使用率の導出) — metrics テーブルの構築
- [x] U004-T3 [系列メタ情報と cli への結線](#u004-t3-系列メタ情報と-cli-への結線) — サンプリング間隔の推定と S5 の進捗ログ

---

## U004-T1: 系列分割と累積値の差分

### 【目的】

* JVM 再起動を検出して系列を分割し(`segment`)、累積値(GC カウンタ)を区間差分に変換する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/metrics.py` (新規)
* `app/tests/unit/test_metrics_segment.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 6.1 DS-07-01, DS-07-02、6.2 DS-07-03, DS-07-04
* `docs/P001-requirement.md` FR-021、4.2(`Timestamp` 列の用途)
* `docs/P002-frontend-spec.md` 6.2.2, 6.2.4(`segment` 列)

### 【実装内容】

1. `build_jvm_gc_derived(con) -> None` を作り、中間テーブル `jvm_gc_derived` を作成する。
2. **`segment` の算出**(DS-07-01)。`jvm_uptime_sec` が直前レコードより小さくなった点を JVM 再起動とみなす。
   ```sql
   CREATE OR REPLACE TABLE jvm_gc_derived AS
   WITH lagged AS (
     SELECT *,
       lag(jvm_uptime_sec) OVER w AS prev_uptime,
       lag(gct)            OVER w AS prev_gct,
       lag(ygct)           OVER w AS prev_ygct,
       lag(fgc)            OVER w AS prev_fgc,
       lag(fgct)           OVER w AS prev_fgct
     FROM jvm_gc
     WINDOW w AS (PARTITION BY container, host ORDER BY ts)
   ), seg AS (
     SELECT *,
       sum(CASE WHEN {restart_expr} THEN 1 ELSE 0 END)
         OVER (PARTITION BY container, host ORDER BY ts
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS segment
     FROM lagged
   )
   SELECT * FROM seg
   ```
   * `{restart_expr}` は次とする。`jvm_uptime_sec` が NULL の系列では `gct` の減少を代替の判定に使う(DS-07-01 の ★FIXME★)。
     ```sql
     (jvm_uptime_sec IS NOT NULL AND prev_uptime IS NOT NULL AND jvm_uptime_sec < prev_uptime)
     OR (jvm_uptime_sec IS NULL AND prev_gct IS NOT NULL AND gct < prev_gct)
     ```
3. **累積値の差分**(DS-07-03、DS-07-04)。`jvm_gc_derived` に次の 3 列を追加する。**`ygc_delta` と `gct_delta` は作らない**(DS-07-04 で監視対象外と決定済み)。
   * `fgc_delta`, `fgct_delta`, `ygct_delta`
   * 計算は「**同じ segment 内**であり、かつ**値が減少していない**」ときのみ差分を取り、それ以外は NULL。
   ```sql
   CASE WHEN segment = lag(segment) OVER w AND fgc >= lag(fgc) OVER w
        THEN fgc - lag(fgc) OVER w ELSE NULL END AS fgc_delta
   ```
   * **`lag` の窓は `PARTITION BY container, host ORDER BY ts` である**(segment で PARTITION しないこと。segment 境界を検出するために直前の segment 値が必要なため)。
4. `db_connection` と `lsf_queue` については `segment` を常に 0 とする(DS-07-02)。中間テーブルは作らず、T2 の縦持ち変換で定数 0 を入れる。

### 【実装してはいけないこと】

* `metrics` テーブルの作成(T2 の担当)。
* 使用率(`*_pct`)の導出(T2 の担当)。
* 再起動の検出に `ts` の間隔を使うこと(**`jvm_uptime_sec` の減少を使う**。採取が一時的に止まっただけの欠測を再起動と誤認しないため)。

### 【Unit Test内容】

* **テスト対象**: `metrics.build_jvm_gc_derived`
* **正常系テスト**
  * 再起動のない 10 点の系列で、`segment` が全点 0 であること。
  * `fgc_delta` が「今回の fgc − 前回の fgc」であること。**先頭点は NULL** であること。
  * `ygct_delta`, `fgct_delta` も同様であること。
  * **`ygc_delta` と `gct_delta` の列が存在しないこと**(DS-07-04 の確認)。
* **主要な異常系テスト**
  * **`jvm_uptime_sec` が途中で減少する系列**(例: 1000, 2000, 3000, **50**, 150)で、`segment` が `0,0,0,1,1` になること。
  * 再起動点(4 点目)の `fgc_delta` が **NULL** であること(前回より小さい値になるため)。再起動後 2 点目(5 点目)は正常に差分が出ること。
  * `jvm_uptime_sec` が全点 NULL で `gct` が途中で減少する系列でも `segment` が分かれること(代替判定)。
  * 累積値が減少した(が再起動ではない)異常な点で `fgc_delta` が NULL になること。
  * 系列が 1 点しかない場合に例外にならないこと。
  * 複数の `(container, host)` が混在するテーブルで、系列ごとに独立して `segment` が計算されること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。先行スプリントのテストも合格していること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* DuckDB の窓関数の構文がバージョン差で通らない場合は、**同じ意味を保つ別の書き方**に置き換えてよいが、その旨をコメントに残すこと。

---

## U004-T2: 縦持ち変換と使用率の導出

### 【目的】

* 3 テーブルを `metrics` テーブル(系列 × 時刻 × 値)へ統合する。
* `-gc`(KB)と `-gcutil`(%)の単位差を、使用率メトリクスの導出で解決する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/metrics.py` (編集)
* `app/tests/unit/test_metrics_build.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 6.3 DS-07-05 〜 DS-07-07、6.5 DS-07-09
* `docs/P002-frontend-spec.md` 6.2.4(`metrics` の列)、6.3(`series_id` の書式)
* `docs/P001-requirement.md` FR-022, FR-023

### 【実装内容】

1. `build_metrics(con) -> None` を作り、**`metrics` を TABLE として物理化する**(DS-07-09。VIEW にしない。10 個のアルゴリズムが繰り返し走査するため)。
2. 列は `series_id, source, metric, ts, value, segment`(`docs/P002-frontend-spec.md` 6.2.4)。
3. **`series_id` の書式を厳守する**(6.3)。文字列連結で作る。
   * `db_connection`: `'db_connection/' || host || ':' || port || '/' || datasource`
   * `jvm_gc`: `'jvm_gc/' || container || '@' || host`
   * `lsf_queue`: `'lsf_queue/' || host || '/' || queue`
   * **この書式は U003 の `expected.json` と、U007 の相関先の絞り込み(ホスト名の抽出)が依存する。** 変えてはならない。
4. **投入するメトリクス**(DS-07-06)。`UNION ALL` で積み上げる。

   | source | metric | 元 |
   | --- | --- | --- |
   | `db_connection` | `active_connections` | そのまま |
   | `jvm_gc` | `ou`, `eu`, `mu` | `jvm_gc_derived` の同名列 |
   | `jvm_gc` | `ou_pct`, `eu_pct`, `mu_pct` | 下記 5 の規則で導出 |
   | `jvm_gc` | `fgc_delta`, `fgct_delta`, `ygct_delta` | `jvm_gc_derived` の同名列 |
   | `lsf_queue` | `njobs`, `pend`, `run`, `susp` | そのまま |

5. **使用率の導出**(DS-07-05)。
   ```sql
   CASE WHEN gc_format = 'gcutil' THEN ou
        WHEN oc IS NOT NULL AND oc > 0 THEN ou / oc * 100.0
        ELSE NULL END AS ou_pct
   ```
   * `eu_pct` は `ec`、`mu_pct` は `mc` を分母にする。
   * **分母が NULL または 0 のときは NULL**(ゼロ除算を起こさないこと)。
6. **`value` が NULL の行は `metrics` に入れない**(アルゴリズムの対象外であるため。`docs/P002-frontend-spec.md` 6.2.4 の注)。`WHERE value IS NOT NULL` を付ける。
7. **物理化後に `series_id, metric, ts` でソートしておく**(DS-07-09)。
8. `DETECTABLE_METRICS: List[str]` を定義する。**`ou_pct`, `eu_pct`, `mu_pct` を含めない**(DS-07-07。検知対象から除外し、脅威度判定と原因候補の材料としてのみ使う)。
   ```python
   DETECTABLE_METRICS = ["active_connections", "ou", "eu", "mu",
                         "fgc_delta", "fgct_delta", "ygct_delta",
                         "njobs", "pend", "run", "susp"]
   ```

### 【実装してはいけないこと】

* `ou_pct` などを検知アルゴリズムの対象に含めること(**DS-07-07 で明示的に除外されている**。同じ異常が 2 件出てしまう)。
* `gc_format` によって検知対象のメトリクス名を切り替えること(DS-07-07 の ★ACCEPTED★ で不採用と決定済み)。
* `metrics` を VIEW にすること。
* `series_id` の書式を変えること。

### 【Unit Test内容】

* **テスト対象**: `metrics.build_metrics`, `DETECTABLE_METRICS`
* **正常系テスト**
  * 3 テーブルにデータを入れて `build_metrics` を実行し、`metrics` に 11 + 3 = **14 種の `metric` 値**が現れること(検知対象 11 + `*_pct` 3)。
  * `DETECTABLE_METRICS` の要素数が 11 で、`*_pct` を含まないこと。
  * `series_id` が `docs/P002-frontend-spec.md` 6.3 の書式と一致すること(3 種すべてを正規表現で検証)。
  * **`gcutil` 形式のデータで `ou_pct == ou`** であること。
  * **`gc` 形式のデータで `ou_pct == ou / oc * 100`** であること(例: `ou=512, oc=2048` → `ou_pct=25.0`)。
  * `metrics` が TABLE であること(`SELECT table_type FROM information_schema.tables` または `duckdb_tables()` で確認)。
  * 行が `series_id, metric, ts` の順に並んでいること。
* **主要な異常系テスト**
  * **`oc` が NULL のとき `ou_pct` が NULL になり、`metrics` にその行が入らないこと**(ゼロ除算・NULL 混入の防止)。
  * **`oc` が 0 のとき `ou_pct` が NULL になり、例外にならないこと。**
  * `value` が NULL の元データが `metrics` に入らないこと。
  * `jvm_gc_derived` が空でも `build_metrics` が例外にならないこと。
  * 3 テーブルすべてが空のとき、`metrics` が 0 行で作られ、例外にならないこと。
  * `build_metrics` を 2 回続けて呼んでも行数が倍にならないこと(`CREATE OR REPLACE` の確認)。
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

## U004-T3: 系列メタ情報と cli への結線

### 【目的】

* 各系列のサンプリング間隔と点数を推定し、アルゴリズムが使えるメタ情報を用意する。
* `cli` に S5 を組み込む。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/metrics.py` (編集)
* `app/src/s_anomaly/cli.py` (編集)
* `app/tests/unit/test_metrics_meta.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 6.4 DS-07-08
* `docs/P002-frontend-spec.md` 3.3(S5 の進捗ログ)
* `docs/P001-requirement.md` FR-054(時刻ベースの窓), FR-031

### 【実装内容】

1. `SeriesMeta` を dataclass で定義する: `series_id: str`, `source: str`, `metric: str`, `n_points: int`, `interval_sec: float`, `t_min: datetime`, `t_max: datetime`。
2. `list_series(con) -> List[SeriesMeta]` を作る。**検知対象メトリクス(`DETECTABLE_METRICS`)のみ**を返す。
   * サンプリング間隔は**中央値**(DS-07-08)。`segment` 内での隣接点の差の中央値。
   ```sql
   SELECT series_id, metric, source,
          count(*) AS n_points, min(ts) AS t_min, max(ts) AS t_max,
          coalesce(median(diff_sec), 0) AS interval_sec
   FROM (
     SELECT series_id, metric, source, ts,
            epoch(ts) - epoch(lag(ts) OVER (PARTITION BY series_id, metric, segment ORDER BY ts)) AS diff_sec
     FROM metrics
   )
   GROUP BY series_id, metric, source
   ```
   * **`diff_sec` が NULL や 0 以下の行は中央値の計算から除く**(`median` は NULL を無視するが、0 以下は明示的に除外する)。
   * 結果を `series_id, metric` の昇順で返す(再現性 NFR-009)。
3. `cli.main` に S5 を組み込む。
   * `build_jvm_gc_derived` → `build_metrics` → `list_series` を呼ぶ。
   * INFO ログ(ステップ `S5`)に「系列数」「メトリクス数」「対象期間(最小時刻〜最大時刻)」「総レコード数」を出す(`docs/P002-frontend-spec.md` 3.3)。
   * この時点では S6 以降が未実装なので、S5 のあと 0 を返す。
4. **DuckDB の例外を `StorageError`(終了コード 6)に変換する**(DS-01-04)。`cli.main` の `try` で `duckdb` の例外基底クラスを捕捉し、`max_memory` / `temp_directory` の現在値と見直しの案内を含むメッセージにする。
   * DuckDB の例外基底クラス名はバージョンにより異なるため、`getattr(duckdb, "Error", Exception)` のように**存在確認をしてから使う**こと。

### 【実装してはいけないこと】

* 検知アルゴリズムの実行(U005 以降の担当)。
* 点数ベースの窓幅の計算(**時刻ベースの窓を使う**。FR-054)。`interval_sec` は点数不足の判定と、検知結果の区間まとめ(隣接判定)に使うためのものである。

### 【Unit Test内容】

* **テスト対象**: `metrics.list_series`, `cli.main`(S5 まで)
* **正常系テスト**
  * 5 分間隔の系列で `interval_sec == 300.0` になること。
  * 複数系列が混在するとき、系列ごとに独立して間隔が求まること。
  * `n_points`, `t_min`, `t_max` が正しいこと。
  * `list_series` が `*_pct` を返さないこと(`DETECTABLE_METRICS` のみ)。
  * 返り値が `series_id, metric` 昇順であること。2 回呼んで同じ順序であること。
  * `main` が S5 の INFO ログを出し、0 を返すこと。
* **主要な異常系テスト**
  * **1 点しかない系列**で `interval_sec` が 0(または既定値)になり、例外にならないこと。
  * `metrics` が空のとき `list_series` が空リストを返すこと。
  * **間隔が不規則な系列**(欠測を含む)で、中央値が支配的な間隔になること(例: 300 秒が 8 点、3600 秒が 1 点 → 中央値 300)。
  * DuckDB の例外を注入したとき `main` が **6** を返し、メッセージに `max_memory` と `temp_directory` が含まれること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。
* **手動で確認する**: `python app/s_anomaly.py app/tests/_work/normal/logs` を実行し、S5 の INFO ログ(系列数・対象期間)が表示され、終了コード 0 になること。**系列数が U003 の生成した系列数(① 6 + ② 2×6 + ③ 3×4 = 30 程度。実際の値は生成内容による)と整合すること**を目視する。
* U004 の全タスク(T1〜T3)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U004 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* 系列数が明らかに期待と異なる(0 系列、または桁違い)場合は、`series_id` の生成か `DETECTABLE_METRICS` の定義を疑い、切り分けてから進むこと。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件に該当しない限り、次のタスクに自動的に進んでください。
