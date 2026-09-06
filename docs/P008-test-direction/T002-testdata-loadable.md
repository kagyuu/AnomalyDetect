あなたはExecutor(実装担当)です。以下は1テストタスク分の作業範囲と完了条件を定義したものです。実施後は、結果(PASS/FAIL/BLOCKED/NOT RUNいずれであっても)を記録したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、`docs/P008-test-direction.md` のWBSに従って自動的に次のテストタスクへ進んでください。人間の指示を待って停止しないでください。

# 【テストID】T002 — 生成データの読み取り可能性

## 【目的】

* `tools/gen_testdata.py`(U003)が生成したファイルを、`loaders`(U002)が**エラーなく読める**ことを確認する。
* 生成ツールとローダは別スプリントで作られるため、**両者の形式解釈が一致していること**を確かめる。ここがずれていると、以降のすべてのテスト(T003 以降)が無意味になる。
* 破損データ(`--broken`)についても、**意図した理由コードで意図した件数だけ**読み飛ばされることを確認する。

## 【参照テスト計画】

* `docs/P006-test-plan.md` 5.2 の T-02、観点 F-04 〜 F-06、F-20
* `docs/P002-frontend-spec.md` 8.2(生成物)、UI-08-01
* `docs/P001-requirement.md` 4章(入力データ仕様)、FR-014、FR-112

## 【対象モジュール】

* `app/tools/gen_testdata.py`
* `app/src/s_anomaly/discovery.py`, `loaders/*`
* 対象スプリント: **U003**(生成ツール側)。U002 のローダを検証手段として使う。

## 【前提条件】対象スプリントの全モジュールビルドが成功していること

* `docs/P008-test-direction.md` 4.1 のビルド確認を実行し、成功していること。
* U002・U003 の全タスクが `[x]` であること。
* **失敗した場合、`BLOCKED` として記録し本テストへ進まない。**

## 【使用するテストデータ】

* `app/tests/_work/normal/logs/`(スイート初期化で生成済み。**読み取り専用として扱う**)
* `app/tests/_work/normal/expected.json`
* `app/tests/_work/broken/logs/`(スイート初期化で生成済み)
* `app/tools/gen_testdata.py` 内の `BROKEN_EXPECTATIONS` 定数(U003-T3 で定義済み)

## 【事前準備】

1. スイート初期化(`python app/tests/integration/_setup_baseline.py`)が**このスイートの実行で 1 回だけ**済んでいること。T001 で実行済みであれば再実行しない。
2. DuckDB のインメモリ接続を作り、`schema.create_all` を実行する。
3. `app/tests/_work/` へ書き込まない。読むだけとする。

## 【実行手順】

**正常系データ**

1. `discovery.discover(app/tests/_work/normal/logs)` を実行する。
2. 全ファイルを対応するローダで読み込む。
3. `LoadResult` の `ok_rows` / `skipped_rows` / `errors` を集計する。
4. `load_error` テーブルの行数を確認する。
5. 3 テーブルの行数と、`gc_format` の内訳を確認する。

**破損データ**

6. `discovery.discover(app/tests/_work/broken/logs)` を実行する。
7. 全ファイルを対応するローダで読み込む。
8. **ファイルごとの `LoadResult` を、`gen_testdata.BROKEN_EXPECTATIONS` の期待値と 1 件ずつ突き合わせる。**

## 【実行コマンド】

```
python -m unittest discover -s app/tests/integration -t app -p "test_testdata_loadable.py" -v
```

* テストファイルは `app/tests/integration/test_testdata_loadable.py` として**新規作成する**。

## 【期待結果】

| # | 確認項目 | 期待値 |
| --- | --- | --- |
| 1 | 正常系: `discover` が拾うファイル数 | `app/tests/_work/normal/logs/` に生成されたファイル数と一致(**0 でないこと**) |
| 2 | 正常系: 全ファイルの `errors` の合計 | **0 件**(生成データに解析エラーがあってはならない) |
| 3 | 正常系: `skipped_rows` の合計 | **0 行** |
| 4 | 正常系: `load_error` テーブルの行数 | **0 行** |
| 5 | 正常系: `jvm_gc` の `gc_format` の内訳 | `gcutil` と `gc` の**両方が 1 行以上存在する**(UI-08-01 の意図: FR-012 の両形式を踏む) |
| 6 | 正常系: 3 テーブルすべてに行が入っていること | `db_connection` / `jvm_gc` / `lsf_queue` のいずれも 0 行でない |
| 7 | 正常系: 期間 | 全テーブルの `ts` の最小が 2026-06-01、最大が 2026-06-14 の範囲に収まる(`expected.json` の `period` と整合) |
| 8 | 破損系: ファイルごとの `ok_rows` | `BROKEN_EXPECTATIONS` の期待値と**全 9 ファイルについて一致** |
| 9 | 破損系: ファイルごとの `errors` の内訳 | `BROKEN_EXPECTATIONS` の期待値と**全 9 ファイルについて一致**(理由コードと件数の両方) |
| 10 | 破損系: 理由コードの種類 | 出現する理由コードが **4 種(`列数不一致` / `日付書式不正` / `数値変換不能` / `見出し不明`)のいずれか**であること。5 種目が現れないこと |
| 11 | 破損系: 有効レコードの存在 | 破損データ全体でも、有効レコードが**1 件以上**得られること(FR-091 の前提) |

## 【合否判定基準】

* **PASS**: 上記 11 項目すべてが期待値と一致する。
* **FAIL**: 1 項目でも一致しない。
* **BLOCKED**: ビルド確認の失敗、または U002/U003 のタスク未完了、またはスイート初期化に失敗して生成データが存在しない。
* **NOT RUN**: 実行環境の問題で実行自体ができなかった。

## 【失敗時に記録する内容】

* **失敗ログ抜粋**: 失敗した確認項目と、期待値・実際の値。項目 8・9 の失敗では**どのファイルのどの理由コードが何件ずれたか**を必ず書く。
* **再現手順**: スイート初期化のコマンドと、実行したテストコマンド。
* **影響がありそうなモジュール**:
  * 項目 2〜4 の失敗 → **生成側(`gen_testdata.py`)とローダ側のどちらが誤っているかを切り分けて書く。** 判断基準は `docs/P001-requirement.md` 4章(入力データ仕様)であり、**設計書に合っているほうが正しい**。
  * 項目 5 の失敗 → `gen_testdata.py`(両形式の出し分け)または `loaders/jstat.py`(形式判別)
  * 項目 8〜10 の失敗 → `gen_testdata.py` の `--broken` 生成、または `loaders/__init__.py` の理由コード判定
* **テスト指示との矛盾の疑い**: `BROKEN_EXPECTATIONS` の定義自体が `docs/P003-backend-spec.md` DS-LD-05 と矛盾すると判断した場合は「あり」とし、該当箇所を書く。

## 【修正禁止事項】

* **アプリケーションコードを修正しない。** 生成ツール(`gen_testdata.py`)も本テストの対象であり、修正対象ではない。
* **`app/tests/_work/` の生成物を手で編集しない。**
* **`BROKEN_EXPECTATIONS` の期待値を、失敗を回避する目的で書き換えない**(改ざん)。
* **失敗したテストをスキップしない。**
* 失敗は記録し、Reviewer Loop(P201〜)へ引き渡す。

## 【次タスクへ進む条件】

* 結果をテスト記録に残したこと。
* `docs/P008-test-direction.md` の T002 行を `[x]` に更新したこと。

## 重要:

* アプリケーションコードを修正しないでください。
* テスト失敗時に、その場で修正して再テストしないでください。
* テスト失敗時は、失敗内容をテスト記録に残してください。
* このテストタスクの結果を記録したら、Executor Stepの停止条件に該当しない限り、次のテストタスクに自動的に進んでください。1テストタスクごとに人間の指示を待つ必要はありません。

---

> **P012 修正記録**: 矛盾点 #2 にもとづき、【実行コマンド】を `python -m unittest app.tests.*` 形式から `discover -t app` 形式へ改めた。詳細は `docs/P011-impact-analysis.md` 2.2 を参照。

---

## 実行結果 (P103)

**結果: PASS**。詳細は `docs/test-records/20260902-0521-test-record.md` の T002 の欄を参照。
