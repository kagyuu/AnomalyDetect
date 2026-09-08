# P008 結合テスト定義 兼 結合テスト指示書 — 目次

* 版: 初版
* 入力: `docs/P002-frontend-spec.md`, `docs/P003-backend-spec.md`, `docs/P005-impl-plan.md`, `docs/P006-test-plan.md`, `docs/P007-impl-direction.md`
* 対象フェーズ: P008

## 1. この目次の使い方

* 本目次は OKF 形式である。状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の 3 種類。
* **1 項目 = 1 テストタスク**である。
* Executor(P103)は、テストの結果が **PASS / FAIL / BLOCKED のいずれであっても、記録を残した時点で** 該当行を `[x]` に更新する。
* **全テストが `[x]` になるまで P103 は完了しない。**
* **FAIL / BLOCKED が残っている場合は、Reviewer Loop(P201〜)への引き渡しが必要である。** P103 はその場で修正しない。

## 2. 本フェーズの対象範囲

`docs/P006-test-plan.md` 5.2 の T-01〜T-09 に対応する。 ※CR-010 で T-09 を追加**いずれも同一スプリント内で完結する、モジュール間の連携テスト**である。

* **対象**: 同一スプリント内の複数モジュール間の連携、`metrics` を介したデータの受け渡し、検知器からレポート文字列生成までの連結。
* **対象外**: 単体テスト(`docs/P007-impl-direction.md` で指示済み)、プロセスを起動する確認・両 OS 確認・性能・再現性・再起動耐性(いずれも `docs/P009-acceptance-direction.md` の対象)。

## 3. テスト一覧

- [x] T001 [取り込みパイプラインの連携](./P008-test-direction/T001-ingest-pipeline.md) — discovery → 3 ローダ → テーブル格納 → 件数検証(U002)
- [x] T002 [生成データの読み取り可能性](./P008-test-direction/T002-testdata-loadable.md) — gen_testdata の出力を U002 のローダで読む(U003)
- [x] T003 [metrics 構築の連携](./P008-test-direction/T003-metrics-build.md) — ローダ → metrics → 11 メトリクスが揃うこと(U004)
- [x] T004 [検知器の一括実行と失敗耐性](./P008-test-direction/T004-detectors-run.md) — metrics → **11** 検知器、1 つ失敗しても継続。**並列実行が逐次と一致すること**(U005/U006/U009。※CR-005 / CR-007)
- [x] T005 [イベント処理の実行順序](./P008-test-direction/T005-events-order.md) — S7 → S8 → S7' の順序が守られること(U007) — **★CR-002・CR-003により実行順が S7→S7'→S8→S8' に変わった。反映済み**
- [x] T006 [レポート生成と正解の突き合わせ](./P008-test-direction/T006-report-expected.md) — 全モジュール連結 → expected.json との照合(U008) — **★CR-001・CR-004によりレポート群が対象になった。反映済み**
- [x] T007 [一時ファイルの後始末](./P008-test-direction/T007-tmpfile-cleanup.md) — report.md.tmp が残らないこと(U008) — **★CR-001により一時ファイルがファイル数分できる。反映済み**
- [x] T008 [進捗ログの出力先振り分け](./P008-test-direction/T008-progress-routing.md) — 標準出力と標準エラーの分離と書式(U008)
- [x] T009 [sar 取り込みパイプラインの連携](./P008-test-direction/T009-sar-pipeline.md) — sa-*.csv → sar → metrics → **sar 由来のイベントが出ること**(U010。※CR-010)

## 4. 全テスト共通の指示

### 4.1 テスト前のビルド

本アプリは Python であり、コンパイルを伴うビルド工程を持たない。**ビルドに相当する確認**として、各テストの実行前に次を必ず行う。

| 項目 | 内容 |
| --- | --- |
| ビルド対象 | `app/src/s_anomaly/` 配下の全モジュール、`app/tools/gen_testdata.py`、`app/s_anomaly.py` |
| ビルドコマンド | `python -m compileall -q app/src app/tools app/s_anomaly.py` |
| 成功条件 | 終了コード 0 で、構文エラーの出力が無いこと |
| 追加の確認 | `python -c "import sys; sys.path.insert(0,'app/src'); import s_anomaly.cli, s_anomaly.metrics, s_anomaly.detectors, s_anomaly.events, s_anomaly.causes, s_anomaly.correlate, s_anomaly.reporter"` が終了コード 0 で通ること(**import 時エラー・循環 import の検出**) |
| 失敗時の記録方法 | `docs/test-records/YYYYMMDD-HHMM-test-record.md` に、結果 `BLOCKED`、失敗ログ抜粋(構文エラーの全文)、影響がありそうなモジュール(該当ファイル)を記録する |
| **失敗時の扱い** | **ビルドが失敗した場合、テストへ進んではならない。** `BLOCKED` として記録し、次のテストタスクへ進む |

### 4.2 テストデータのライフサイクル

`docs/P006-test-plan.md` 3章(TP-03 〜 TP-08)の方針に従う。**本フェーズで独自に方針を決めない。**

* **復元の単位は「テストスイートの実行ごと」**(TP-03)。個々のテストに復元手順を書かない。
* **復元は、テスト対象のモジュールをロードする前に、スイート全体で 1 回だけ行う。**
* **ベースライン**(TP-05)は次の状態を指す。
  1. `app/tests/_work/normal/` に `gen_testdata.py` の出力(`logs/` と `expected.json`)がある
  2. `app/tests/_work/broken/` に `gen_testdata.py --broken` の出力がある
  3. カレントディレクトリに `report.md` / `report.md.tmp` が無い
  4. `temp_directory`(既定 `./tmp`)が空である
* **スイート初期化の実行方法**: 結合テストの実行前に、次を 1 回だけ実行する。
  ```
  python app/tests/integration/_setup_baseline.py
  ```
  * このスクリプトは `app/tests/_work/` を削除して作り直し、`gen_testdata.py` を 2 回(通常・`--broken`)実行する。**T001 の【事前準備】で作成を指示する。**
* **各テストは `tempfile.TemporaryDirectory` で自分専用の作業ディレクトリを作り、その中で動く**(TP-04)。`app/tests/_work/` は**読み取り専用として扱う**(TP-04 の ★ACCEPTED★)。書き込みが必要なテストは自分の一時ディレクトリへコピーしてから使う。
* テストデータの値は固定日付(2026-06-01 〜 2026-06-14)であり、**実行日に依存しない**(TP-19)。件数を検証するテストがあるため、一意化ではなく復元で重複を避ける。

### 4.3 テスト記録

* 記録先: `docs/test-records/YYYYMMDD-HHMM-test-record.md`
* 記録形式: `.claude/skills/spec-driven-dev/TEMPLATE-test-record.md` の共通形式に従う。
* **1 テストタスクにつき 1 見出しブロック。** 同じ実行でまとめて実行した場合は同一ファイルに追記してよい。
* **結果は必ず `PASS` / `FAIL` / `BLOCKED` / `NOT RUN` のいずれか 1 つ**を書く。
* `FAIL` / `BLOCKED` のときは「失敗ログ抜粋」「再現手順」「影響がありそうなモジュール」を空欄にしない。

### 4.4 修正禁止事項(全テスト共通)

**テストが失敗しても、その場で修正してはならない。** これは P103(結合テスト実行)の規定である。

* **アプリケーションコードを修正しない。**
* **テストコードをその場で都合よく変更しない。** ただし、本フェーズの指示に従ってテストコードを**新規作成する**ことは修正に当たらない。
* **失敗したテストをスキップしない。**
* **期待値を変更して成功扱いにしない**(改ざん)。特に `app/tests/_work/normal/expected.json` を書き換えてはならない。
* **同じ失敗に対して場当たり的な再テストを繰り返さない。**
* 失敗内容をテスト記録に残し、**次工程(Reviewer Loop、P201〜)へ引き渡す。**

### 4.5 実行順序

T001 → T008 の番号順に実行する。先行するテストが `BLOCKED` でも、後続のテストは実行を試みる(依存関係で実行できない場合は `BLOCKED` として記録する)。

## 5. 未解決事項

| # | 内容 | 扱い |
| --- | --- | --- |
| 1 | `app/tests/integration/_setup_baseline.py` は `docs/P007-impl-direction.md` のどのスプリントでも作成が指示されていない。**本フェーズ(T001 の事前準備)で作成を指示する** | 記録のみ。P010 で P007 との整合を確認する |
| 2 | `docs/P006-test-plan.md` 5.2 の T-04 は「1 つが失敗しても他が続くこと」を含むが、これは `docs/P007-impl-direction.md` U005-T5 の単体テストでも確認している。**結合レベルでは「10 個すべてを実データに対して回したときの挙動」として確認する**ため、重複ではない | 記録のみ |
