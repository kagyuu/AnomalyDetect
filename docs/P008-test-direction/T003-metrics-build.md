あなたはExecutor(実装担当)です。以下は1テストタスク分の作業範囲と完了条件を定義したものです。実施後は、結果(PASS/FAIL/BLOCKED/NOT RUNいずれであっても)を記録したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、`docs/P008-test-direction.md` のWBSに従って自動的に次のテストタスクへ進んでください。人間の指示を待って停止しないでください。

# 【テストID】T003 — metrics 構築の連携

## 【目的】

* ローダ(U002)が格納した 3 テーブルから、`metrics`(U004)が正しく構築されることを確認する。
* 特に **`-gc`(KB)と `-gcutil`(%)の単位差が `*_pct` の導出で吸収されている**こと(`docs/P003-backend-spec.md` DS-07-05)を、実データ相当の入力で確認する。これは `docs/P002-frontend-spec.md` 9.1 #3 が指摘した問題への回答の実証である。

## 【参照テスト計画】

* `docs/P006-test-plan.md` 5.2 の T-03、観点 F-08
* `docs/P002-frontend-spec.md` 6.2.4(`metrics` の列)、6.3(`series_id` の書式)、9.1 #3
* `docs/P003-backend-spec.md` 6章 DS-07-01 〜 DS-07-09

## 【対象モジュール】

* `app/src/s_anomaly/metrics.py`
* (入力として)`app/src/s_anomaly/loaders/*`
* 対象スプリント: **U004**

## 【前提条件】対象スプリントの全モジュールビルドが成功していること

* `docs/P008-test-direction.md` 4.1 のビルド確認を実行し、成功していること。
* U002・U003・U004 の全タスクが `[x]` であること。
* **失敗した場合、`BLOCKED` として記録し本テストへ進まない。**

## 【使用するテストデータ】

* `app/tests/_work/normal/logs/`(読み取り専用)
* 加えて、**JVM 再起動を含む jstat ファイル**を本テスト専用に一時ディレクトリへ作る。生成データには再起動が含まれないため、`segment` 分割の確認にはこれが必要である。
  * `restart01_gc_hostR_20260601.txt`: `-gcutil` 形式、20 行。`Timestamp` を `1000,2000,...,10000` と増やしたあと、11 行目で **`500` に戻す**。累積値(`YGC`/`FGC`/`GCT`)も 11 行目で小さい値にリセットする。

## 【事前準備】

1. スイート初期化がこのスイートの実行で 1 回だけ済んでいること(T001 参照)。
2. `tempfile.TemporaryDirectory` に本テスト専用の作業ディレクトリを作り、上記の再起動入りファイルを置く。`app/tests/_work/` へは書き込まない。
3. DuckDB のインメモリ接続を作り、`schema.create_all` を実行する。
4. `app/tests/_work/normal/logs/` と、専用の再起動ファイルの**両方**を読み込む。

## 【実行手順】

1. `discovery` + `loaders` で 3 テーブルへデータを格納し、`dedupe_all` を実行する。
2. `metrics.build_jvm_gc_derived(con)` を実行する。
3. `metrics.build_metrics(con)` を実行する。
4. `metrics.list_series(con)` を実行する。
5. 次を SQL で取得して検証する。
   * `metrics` の `metric` の distinct 値
   * `series_id` のサンプル
   * `gcutil` 系列と `gc` 系列それぞれの `ou` と `ou_pct` の値
   * 再起動を含む系列の `segment` と `fgc_delta`

## 【実行コマンド】

```
python -m unittest discover -s app/tests/integration -t app -p "test_metrics_build.py" -v
```

* テストファイルは `app/tests/integration/test_metrics_build.py` として**新規作成する**。

## 【期待結果】

| # | 確認項目 | 期待値 |
| --- | --- | --- |
| 1 | `metrics` の `metric` の種類 | **14 種**(検知対象 11 + `ou_pct` / `eu_pct` / `mu_pct`)。`docs/P003-backend-spec.md` DS-07-06 の表と完全一致 |
| 2 | `metrics.DETECTABLE_METRICS` | **11 要素**で、`*_pct` を含まない(DS-07-07) |
| 3 | `list_series` の返す `metric` | すべて `DETECTABLE_METRICS` に含まれる(`*_pct` が系列一覧に出ない) |
| 4 | `series_id` の書式 | 3 種すべてが `docs/P002-frontend-spec.md` 6.3 の正規表現に一致 |
| 5 | **`gcutil` 系列**の `ou` と `ou_pct` | **値が等しい**(既に % であるため) |
| 6 | **`gc` 系列**の `ou_pct` | `ou / oc * 100` と一致し、**0〜100 の範囲**に収まる |
| 7 | **`gc` 系列**の `ou` | KB 値のまま(100 を大きく超える値が存在する) |
| 8 | `value` の NULL | `metrics` に `value IS NULL` の行が**存在しない** |
| 9 | 再起動系列の `segment` | 前半 10 点が `0`、後半 10 点が `1` |
| 10 | 再起動点の `fgc_delta` | 11 行目(再起動直後)の `fgc_delta` が **NULL**(`metrics` に行が入らない) |
| 11 | 通常点の `fgc_delta` | 再起動をまたがない隣接点で「今回 − 前回」と一致 |
| 12 | `list_series` の `interval_sec` | 生成データの系列で **300.0**(5 分間隔) |
| 13 | `metrics` が TABLE であること | VIEW ではない(DS-07-09) |
| 14 | 再現性 | `build_metrics` を 2 回実行しても行数が変わらず、`list_series` の順序が同一 |

## 【合否判定基準】

* **PASS**: 上記 14 項目すべてが期待値と一致する。
* **FAIL**: 1 項目でも一致しない。
* **BLOCKED**: ビルド確認の失敗、または U002/U003/U004 のタスク未完了。
* **NOT RUN**: 実行環境の問題で実行自体ができなかった。

## 【失敗時に記録する内容】

* **失敗ログ抜粋**: 失敗した確認項目、期待値、実際の値。項目 5〜7 の失敗では**具体的な `ou` / `oc` / `ou_pct` の数値**を書く。
* **再現手順**: 再起動入りファイルの内容(全行)、実行したテストコマンド。
* **影響がありそうなモジュール**:
  * 項目 1〜3 の失敗 → `metrics.py`(`DETECTABLE_METRICS` の定義、`build_metrics` の UNION)
  * 項目 4 の失敗 → `metrics.py`(`series_id` の文字列連結)。**この失敗は U003 の `expected.json` と U007 の相関にも波及する**ため、影響範囲として明記する
  * 項目 5〜7 の失敗 → `metrics.py`(DS-07-05 の `CASE WHEN gc_format` 分岐)
  * 項目 9〜11 の失敗 → `metrics.py`(DS-07-01 の `segment` 算出、DS-07-03 の差分)
  * 項目 13 の失敗 → `metrics.py`(`CREATE TABLE` ではなく `CREATE VIEW` になっている)
* **テスト指示との矛盾の疑い**: 期待値が `docs/P003-backend-spec.md` 6章 と食い違うと判断した場合は「あり」とし該当箇所を書く。

## 【修正禁止事項】

* **アプリケーションコードを修正しない。**
* **テストコードをその場で都合よく変更しない**(新規作成は除く)。
* **失敗したテストをスキップしない。**
* **期待値を変更して成功扱いにしない。** 特に項目 1(14 種)と項目 2(11 要素)は設計書の表そのものであり、実装に合わせて数を変えてはならない。
* 失敗は記録し、Reviewer Loop(P201〜)へ引き渡す。

## 【次タスクへ進む条件】

* 結果をテスト記録に残したこと。
* `docs/P008-test-direction.md` の T003 行を `[x]` に更新したこと。

## 重要:

* アプリケーションコードを修正しないでください。
* テスト失敗時に、その場で修正して再テストしないでください。
* テスト失敗時は、失敗内容をテスト記録に残してください。
* このテストタスクの結果を記録したら、Executor Stepの停止条件に該当しない限り、次のテストタスクに自動的に進んでください。1テストタスクごとに人間の指示を待つ必要はありません。

---

> **P012 修正記録**: 矛盾点 #2 にもとづき、【実行コマンド】を `python -m unittest app.tests.*` 形式から `discover -t app` 形式へ改めた。詳細は `docs/P011-impact-analysis.md` 2.2 を参照。

---

## 実行結果 (P103)

**結果: PASS**。詳細は `docs/test-records/20260902-0521-test-record.md` の T003 の欄を参照。
