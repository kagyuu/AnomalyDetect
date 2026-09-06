あなたはExecutor(実装担当)です。以下は1テストタスク分の作業範囲と完了条件を定義したものです。実施後は、結果(PASS/FAIL/BLOCKED/NOT RUNいずれであっても)を記録したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、`docs/P008-test-direction.md` のWBSに従って自動的に次のテストタスクへ進んでください。人間の指示を待って停止しないでください。

# 【テストID】T001 — 取り込みパイプラインの連携

## 【目的】

* `discovery`(M03)→ 3 つのローダ(M04/M05/M06)→ DuckDB のテーブル格納 → 重複排除 までが、**モジュール間で正しく連携する**ことを確認する。
* 単体では通っている各モジュールが、実際のディレクトリ構造の上で組み合わさったときに、ファイル種別の振り分けとメタ情報(コンテナ名・ホスト名)の受け渡しが正しく行われることを確認する。

## 【参照テスト計画】

* `docs/P006-test-plan.md` 5.2 の T-01、観点 F-03 〜 F-07
* `docs/P002-frontend-spec.md` 6.2.1 〜 6.2.3(テーブル定義)、6.3(`series_id` の書式)
* `docs/P003-backend-spec.md` 4章(discovery)、5章(loaders)、5.5(重複排除)

## 【対象モジュール】

* `app/src/s_anomaly/discovery.py`
* `app/src/s_anomaly/loaders/__init__.py`, `dbconn.py`, `jstat.py`, `bqueues.py`
* `app/src/s_anomaly/schema.py`
* 対象スプリント: **U002**

## 【前提条件】対象スプリントの全モジュールビルドが成功していること

* `docs/P008-test-direction.md` 4.1 のビルド確認を実行し、成功していること。
  ```
  python -m compileall -q app/src app/tools app/s_anomaly.py
  python -c "import sys; sys.path.insert(0,'app/src'); import s_anomaly.discovery, s_anomaly.loaders.dbconn, s_anomaly.loaders.jstat, s_anomaly.loaders.bqueues, s_anomaly.schema"
  ```
* **失敗した場合、本テストへ進まず `BLOCKED` として記録する。**
* U002 の全タスク(U002-T1〜T5)が `[x]` であること。`[ ]` が残っている場合は `NOT RUN` として記録する。

## 【使用するテストデータ】

* `app/tests/fixtures/` の手書き極小ファイル(U002 で作成済み)。
* 本テスト専用に、**一時ディレクトリへ次の構造を組み立てる**。
  ```
  {tmp}/root/
    ├── a/DBConnection_20260601.csv          (見出し + 3 行)
    ├── a/sub/app01_gc_host1_20260601.txt    (-gcutil 形式、見出し + 3 行)
    ├── b/app02_gc_host2_20260601.txt        (-gc 形式 18 列、見出し + 3 行)
    ├── b/bqueues_grid01_20260601.txt        (QUEUE 2 個、見出し + 3 行)
    ├── b/readme.txt                          (対象外)
    └── c/DBConnection_20260601.csv          (a/ と同一内容 = 重複)
  ```
  * ファイル内容は `app/tests/fixtures/` のものを流用してよい。**同一内容のファイルを 2 箇所(`a/` と `c/`)に置く**ことが本テストの重複排除確認の要である。

## 【事前準備】

1. **スイート初期化を 1 回だけ実行する**(`docs/P008-test-direction.md` 4.2)。まだ存在しない場合は、次の内容で `app/tests/integration/_setup_baseline.py` を**新規作成する**。
   * `app/tests/_work/` を削除して作り直す。
   * `python app/tools/gen_testdata.py app/tests/_work/normal` を実行する。
   * `python app/tools/gen_testdata.py app/tests/_work/broken --broken` を実行する。
   * カレントディレクトリの `report.md` / `report.md.tmp` を削除する。
   * `./tmp` を削除して作り直す。
   ```
   python app/tests/integration/_setup_baseline.py
   ```
   * **この初期化は、テスト対象モジュールをロードする前に、スイート全体で 1 回だけ行う。** 個々のテストで繰り返さない。
2. 本テスト用の一時ディレクトリを `tempfile.TemporaryDirectory` で作り、【使用するテストデータ】の構造を組み立てる。
3. DuckDB のインメモリ接続を作り、`schema.create_all` を実行する。

## 【実行手順】

1. `discovery.discover(root)` を呼び、返された `LogFile` のリストを得る。
2. 種別ごとに対応するローダの `load` を呼ぶ。**`discovery` が返した順序どおりに呼ぶ**(重複排除の「先勝ち」が順序に依存するため)。
3. `loaders.dedupe_all(con)` を呼ぶ。
4. 各テーブルの行数と代表的な値を SQL で取得する。
5. `load_error` テーブルの内容を確認する。

## 【実行コマンド】

```
python -m unittest discover -s app/tests/integration -t app -p "test_ingest_pipeline.py" -v
```

* テストファイルは `app/tests/integration/test_ingest_pipeline.py` として**新規作成する**。

## 【期待結果】

| # | 確認項目 | 期待値 |
| --- | --- | --- |
| 1 | `discover` が返す件数 | **5 件**(`readme.txt` を除く。`c/DBConnection_20260601.csv` を含む) |
| 2 | 種別の内訳 | `db_connection` 2、`jvm_gc` 2、`lsf_queue` 1 |
| 3 | `jvm_gc` のメタ情報 | `app01`/`host1` と `app02`/`host2` が正しく抽出されている |
| 4 | 重複排除**前**の `db_connection` の行数 | 6 行(3 行 × 2 ファイル) |
| 5 | 重複排除**後**の `db_connection` の行数 | **3 行** |
| 6 | 残った行の `_load_seq` | **小さいほう(`a/` 側で読み込んだもの)が残っている** |
| 7 | `jvm_gc` の `gc_format` | `app01` の行が `gcutil`、`app02` の行が `gc` |
| 8 | `jvm_gc` の容量列 | `app01`(gcutil)の `oc` が NULL、`app02`(gc)の `oc` が非 NULL |
| 9 | `lsf_queue` の行数 | 3 行 × QUEUE 2 個 = **6 行**(横持ち → 縦持ちが効いている) |
| 10 | `load_error` | **0 行**(すべて正常なデータであるため) |
| 11 | `discover` の再現性 | 2 回呼んで**同一の順序**で返る |

## 【合否判定基準】

* **PASS**: 上記 11 項目すべてが期待値と一致する。
* **FAIL**: 1 項目でも一致しない。
* **BLOCKED**: 【前提条件】のビルド確認が失敗した、または U002 のタスクが未完了。
* **NOT RUN**: 実行環境の問題(DuckDB が読み込めない等)で実行自体ができなかった。

## 【失敗時に記録する内容】

`docs/test-records/YYYYMMDD-HHMM-test-record.md` に、`TEMPLATE-test-record.md` の形式で次を記録する。

* **失敗ログ抜粋**: `unittest` の失敗出力(期待値と実際の値の対比を含む全文)。
* **再現手順**: 一時ディレクトリの構造(どのファイルをどこに置いたか)、実行したコマンド。
* **影響がありそうなモジュール**: 失敗した確認項目に対応するモジュール。
  * 項目 1〜3 の失敗 → `discovery.py`
  * 項目 4〜6 の失敗 → `loaders/__init__.py`(`dedupe_all`、`_load_seq`)、`discovery.py`(ソート順)
  * 項目 7・8 の失敗 → `loaders/jstat.py`(形式判別)
  * 項目 9 の失敗 → `loaders/bqueues.py`(横持ち→縦持ち)
  * 項目 11 の失敗 → `discovery.py`(DS-03-04 の安定ソート)
* **テスト指示との矛盾の疑い**: 期待値が `docs/P002-frontend-spec.md` 6.2 や `docs/P003-backend-spec.md` 5章 と食い違うと判断した場合は「あり」とし、該当箇所を書く。

## 【修正禁止事項】

* **アプリケーションコードを修正しない。**
* **テストコードをその場で都合よく変更しない**(本テスト用のテストコードを新規作成することは除く)。
* **失敗したテストをスキップしない。**
* **期待値を変更して成功扱いにしない。** 上表の 11 項目は設計書から導いたものである。
* **同じ失敗に対して場当たり的な再テストを繰り返さない。**
* 失敗は記録し、Reviewer Loop(P201〜)へ引き渡す。

## 【次タスクへ進む条件】

* 結果(PASS/FAIL/BLOCKED/NOT RUN)をテスト記録に残したこと。
* `docs/P008-test-direction.md` の T001 行を `[x]` に更新したこと。

## 重要:

* アプリケーションコードを修正しないでください。
* テスト失敗時に、その場で修正して再テストしないでください。
* テスト失敗時は、失敗内容をテスト記録に残してください。
* このテストタスクの結果を記録したら、Executor Stepの停止条件に該当しない限り、次のテストタスクに自動的に進んでください。1テストタスクごとに人間の指示を待つ必要はありません。

---

> **P012 修正記録**: 矛盾点 #2 にもとづき、【実行コマンド】を `python -m unittest app.tests.*` 形式から `discover -t app` 形式へ改めた。詳細は `docs/P011-impact-analysis.md` 2.2 を参照。

---

## 実行結果 (P103)

**結果: PASS**。詳細は `docs/test-records/20260902-0521-test-record.md` の T001 の欄を参照。
