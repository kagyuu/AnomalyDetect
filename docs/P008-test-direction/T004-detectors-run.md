あなたはExecutor(実装担当)です。以下は1テストタスク分の作業範囲と完了条件を定義したものです。実施後は、結果(PASS/FAIL/BLOCKED/NOT RUNいずれであっても)を記録したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、`docs/P008-test-direction.md` のWBSに従って自動的に次のテストタスクへ進んでください。人間の指示を待って停止しないでください。

# 【テストID】T004 — 検知器の一括実行と失敗耐性

## 【目的】

* `metrics`(U004)を入力として、**10 個の検知器(U005/U006)を全系列に対して一括実行**したときの連携を確認する。
* **1 つの検知器が例外を投げても、他の検知器・他の系列の処理が継続する**こと(`docs/P001-requirement.md` FR-032)を、実データ相当の規模で確認する。単体テストでは 1 系列での確認に留まるため、ここで全系列規模の挙動を見る。
* 埋め込んだ異常(`expected.json` の `injected`)が **`Detection` のレベルで**検知されることを確認する(イベント統合前の段階)。

## 【参照テスト計画】

* `docs/P006-test-plan.md` 5.2 の T-04、観点 F-09、F-11、F-12
* `docs/P002-frontend-spec.md` 8.3(`expected.json`)、UI-08-03
* `docs/P003-backend-spec.md` 7.1 DS-08-01, DS-08-02、7.4 〜 7.13

## 【対象モジュール】

* `app/src/s_anomaly/detectors/`(`__init__.py`, `base.py`, A1〜A5, B1〜B5)
* (入力として)`app/src/s_anomaly/metrics.py`
* 対象スプリント: **U005 / U006**

## 【前提条件】対象スプリントの全モジュールビルドが成功していること

* `docs/P008-test-direction.md` 4.1 のビルド確認を実行し、成功していること。特に次を確認する。
  ```
  python -c "import sys; sys.path.insert(0,'app/src'); from s_anomaly.detectors import REGISTRY; print(len(REGISTRY))"
  ```
  * **出力が 10 であること。** 10 でなければ `BLOCKED` として記録する。
* U002〜U006 の全タスクが `[x]` であること。
* **失敗した場合、`BLOCKED` として記録し本テストへ進まない。**

## 【使用するテストデータ】

* `app/tests/_work/normal/logs/`(読み取り専用)
* `app/tests/_work/normal/expected.json`(読み取り専用)

## 【事前準備】

1. スイート初期化がこのスイートの実行で 1 回だけ済んでいること(T001 参照)。
2. DuckDB のインメモリ接続を作り、`schema.create_all` → `discovery` + `loaders` → `dedupe_all` → `build_jvm_gc_derived` → `build_metrics` まで実行する。
3. `list_series` で系列一覧を取得する。
4. **`expected.json` を読み込む。書き換えてはならない。**

## 【実行手順】

**手順 A: 正常な一括実行**

1. `enabled_detectors(cfg)`(既定設定 = 全 10 個有効)を取得する。
2. 全検知器 × 全系列で `run` を呼び、`Detection` と `SkipInfo` を集める。
3. 所要時間を計測する。
4. `expected.json` の `injected` 7 件それぞれについて、`expect_algorithms` のいずれかを含む `Detection` が、期間の重なりを持って存在するかを確認する。
   * **重なりの許容幅は前後 60 分**(`docs/P002-frontend-spec.md` 8.3 の ★FIXME★)。
5. `expected.json` の `clean_series` について、`Detection` が 0 件であることを確認する。

**手順 B: 失敗耐性の確認**

6. `REGISTRY` の中から 1 つ(`ALG-A1`)を選び、その `run` を**必ず例外を投げる関数に差し替える**(モンキーパッチ)。
7. 手順 1〜3 を再実行する。
8. 例外を投げた検知器以外の結果が、手順 A と**同一**であることを確認する。
9. 例外が WARNING として記録され、失敗の集計に含まれることを確認する。
10. **パッチを必ず元に戻す**(`try/finally` または `addCleanup`)。

## 【実行コマンド】

```
python -m unittest discover -s app/tests/integration -t app -p "test_detectors_run.py" -v
```

* テストファイルは `app/tests/integration/test_detectors_run.py` として**新規作成する**。

## 【期待結果】

| # | 確認項目 | 期待値 |
| --- | --- | --- |
| 1 | `REGISTRY` の要素数 | **10** |
| 2 | `aspect` の内訳 | `観点1` が 5、`観点2` が 5 |
| 3 | 全検知器の実行 | 例外を送出せず完了する |
| 4 | **検知漏れ** | `injected` **7 件すべて**について、`expect_algorithms` のいずれかを含む `Detection` が期間の重なりを持って存在する |
| 5 | **誤検知** | `clean_series` に属する系列の `Detection` が **0 件** |
| 6 | 点数不足のスキップ | `SkipInfo` が返る系列がある場合、その理由が記録されている(0 件でもよい) |
| 7 | 所要時間 | **3 分以内**(`docs/P001-requirement.md` NFR-002 の目安。超過した場合は FAIL とせず、値を記録して次工程へ申し送る) |
| 8 | 再現性 | 2 回実行して `Detection` の件数と `(series_id, metric, algorithm, start_ts)` の並びが同一 |
| 9 | **失敗耐性(手順 B)** | `ALG-A1` が全系列で例外を投げても、**処理全体が完了する** |
| 10 | 失敗耐性: 他検知器の結果 | 手順 A の結果から `ALG-A1` の `Detection` を除いたものと**完全一致** |
| 11 | 失敗耐性: 記録 | 失敗が WARNING として出力され、`(algorithm, series_id, 理由)` が集計されている |

## 【合否判定基準】

* **PASS**: 項目 1〜6 と 8〜11 が期待値と一致する。**項目 7(所要時間)は超過しても PASS を妨げない**が、値を必ず記録する。
* **FAIL**: 項目 1〜6、8〜11 のいずれかが一致しない。
* **BLOCKED**: ビルド確認の失敗(`REGISTRY` が 10 でない場合を含む)、または U002〜U006 のタスク未完了。
* **NOT RUN**: 実行環境の問題で実行自体ができなかった。

## 【失敗時に記録する内容】

* **失敗ログ抜粋**: 失敗した確認項目と、期待値・実際の値。
  * **項目 4(検知漏れ)の失敗では、どの `INJ-xxx` が検知されなかったか、その系列でどのアルゴリズムが何件検知したかを必ず書く。**
  * **項目 5(誤検知)の失敗では、`clean_series` のどの時刻をどのアルゴリズムが検知したか、その `score` と `detail` を書く。**
* **再現手順**: スイート初期化のコマンド、テストコマンド、失敗した `INJ-xxx` の `series_id` と期間。
* **影響がありそうなモジュール**:
  * 項目 4 の失敗 → 該当する `detectors/` の実装、または `metrics.py`(系列が正しく作られていない)、または `gen_testdata.py`(異常が意図どおり埋め込まれていない)。**3 者のどれかを切り分けて書く**(切り分けの順序は `docs/P007-impl-direction/U006-detectors-b.md` U006-T5 の停止条件を参照)。
  * 項目 5 の失敗 → 該当検知器の閾値判定、または `detectors/base.py` の `effective_sigma`
  * 項目 9〜11 の失敗 → `cli.py` の例外捕捉(DS-08-02)
  * 項目 8 の失敗 → ソートキーの不足(NFR-009)
* **テスト指示との矛盾の疑い**: `expected.json` の期待が設計書(`docs/P003-backend-spec.md` 7.4〜7.13 の既定パラメータ)に対して厳しすぎる/緩すぎると判断した場合は「あり」とし、該当箇所を書く。

## 【修正禁止事項】

* **アプリケーションコードを修正しない。** 検知されないからといって閾値やパラメータを変えてはならない。
* **`app/tests/_work/normal/expected.json` を書き換えない**(改ざん)。
* **`settings.properties` の既定値を変更しない。**
* **失敗したテストをスキップしない。** 特に項目 4・5 は本アプリの中核であり、スキップは許されない。
* **同じ失敗に対して場当たり的な再テストを繰り返さない。**
* 手順 B のモンキーパッチは、**テスト終了時に必ず元に戻す**こと(他のテストへの影響を防ぐため)。
* 失敗は記録し、Reviewer Loop(P201〜)へ引き渡す。

## 【次タスクへ進む条件】

* 結果をテスト記録に残したこと。**項目 7 の所要時間の実測値を必ず記録すること。**
* `docs/P008-test-direction.md` の T004 行を `[x]` に更新したこと。

## 重要:

* アプリケーションコードを修正しないでください。
* テスト失敗時に、その場で修正して再テストしないでください。
* テスト失敗時は、失敗内容をテスト記録に残してください。
* このテストタスクの結果を記録したら、Executor Stepの停止条件に該当しない限り、次のテストタスクに自動的に進んでください。1テストタスクごとに人間の指示を待つ必要はありません。

---

> **P012 修正記録**: 矛盾点 #2 にもとづき、【実行コマンド】を `python -m unittest app.tests.*` 形式から `discover -t app` 形式へ改めた。詳細は `docs/P011-impact-analysis.md` 2.2 を参照。

---

## 実行結果 (P103)

**結果: PASS**。詳細は `docs/test-records/20260902-0521-test-record.md` の T004 の欄を参照。
