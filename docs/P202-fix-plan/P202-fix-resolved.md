# 解決済み修正障害一覧

* 版: 第 2 版(2026-09-04 に Reviewer Loop 2 回目分を追記)
* **Reviewer Loop 1 回目** = F001 / F002、**2 回目** = F003
* 記録フェーズ: P203(修正実施)

## 概要

* 修正完了した障害数: **3**(F001 / F002 / F003)
* 未解決の障害数: **1**(NFR-009 再現性。**テストの失敗としては顕在化していない**)
* 全体状態: **PARTIAL**

**A006 は PASS になった。** 単体 376 件・結合 105 件・受け入れ 122 件(A006 の 13 件を含む)が
すべて成功している。

残る未解決は **NFR-009(再現性)** である。同一入力・同一設定で `report.md` が一致しない。
**F001 / F003 が持ち込んだものではない既存の欠陥**であり、**A003 が 14 日規模でしか
確認していないため現行のテストでは捉えられていない**。詳細と対処案は
`P202-fix-unresolved.md` に記録した。

## 解決済障害一覧

| 修正タスク | 対応障害 | 結果 | テスト日付 | 修正日付 |
|---|---|---|---|---|
| F001 | A006(**本タスクが対象とした欠陥は解消。A006 自体は別原因で依然 FAIL**) | RESOLVED | 2026/09/04 | 2026/09/04 |
| F002 | A001, A002, A004, A007, A010, A011(いずれもテスト指示側の誤り。単一の結果区分によりまとめて修正) | RESOLVED | 2026/09/04 | 2026/09/04 |
| F003 | A006(**これにより A006 が PASS になった**) | RESOLVED | 2026/09/04 | 2026/09/04 |

## 解決済障害

### F001: ALG-B4 の窓平均が系列全体を走査している

* **対応するテストID**: A006
* **対応するテスト記録**: `docs/test-records/20260903-0010-test-record.md` の A006(修正前)、`docs/P202-fix-plan/fixed/F001-alg-b4-window-scan.md` 3 章(修正後の実測)
* **失敗していたテストコマンド**: `S_ANOMALY_RUN_A006=1 python -m unittest discover -s app/tests/acceptance -t app -p "test_a006_performance.py" -v`
* **修正内容**: `_scan` の冒頭で系列あたり 1 度だけ「先頭からの経過秒」の配列を作り、`_mean_after` / `_mean_before` は `bisect_right` / `bisect_left` で窓の境界を求めて**その範囲だけを走査する**ようにした。計算量が **O(検知件数 × 系列長) から O(検知件数 × 窓幅)** になった。経過秒は夏時間の切り替えで壁時計の差とずれる `timestamp()` ではなく、先頭との `timedelta` 差で求めた(`docs/ADR.md` ADR-013)
* **変更したソースコード**: `app/src/s_anomaly/detectors/b4_cusum.py`(`_scan` / `_mean_after` / `_mean_before`)。**同種の全走査が他に無いことを確認した**(`points[index:]` / `points[:index]` 形の走査は本ファイルの 2 箇所のみ)
* **更新したdocs**:
  * `docs/P202-fix-plan/fixed/F001-alg-b4-window-scan.md`(本ファイルの詳細)
  * `docs/P202-fix-plan/P202-fix-unresolved.md`(A006 が依然 FAIL であることと新規欠陥)
  * `docs/ADR.md` ADR-013(「残存リスク」を確定規模での実測値に置き換え)
  * `docs/ArchitectureHandbook.md`(「既知の制約・技術的負債」)
  * `docs/P202-fix-plan.md`(目次を `[x]` に更新)
  * `docs/P003-backend-spec.md` DS-08-B4 は**変更していない**(実装の意味が変わっていないため)
  * `docs/P001-requirement.md` NFR-002 は**変更していない**(本タスクの禁止事項)
* **実行したテスト**: ビルド、単体 376 件 × 2 回、結合 105 件 × 2 回、受け入れ 109 件 × 2 回、A006 を確定規模(通常 30 日 / 大規模 90 日)で実測
* **テスト結果**:
  * `python -m compileall -q app` 終了コード 0
  * 単体 `Ran 376 tests` / `OK (skipped=1)` を 2 回(629.860s / 608.501s)
  * 結合 `Ran 105 tests` / `OK` を 2 回(1057.719s / 676.081s)
  * 受け入れ `Ran 122 tests` / `OK (skipped=13)` を 2 回(501.553s / 510.945s)。skipped=13 は A006 の 2 クラス
  * **検知結果が変わっていないこと**: 退避 zip に残っていた修正前ツリーと現行ツリーを別々の一時ディレクトリへ展開し、同一入力で実行したところ、`report.md` は 477,751 バイト・**9,107 行が実行日時の行を除いて完全一致**した
  * **効果**: ALG-B4 は S6 の 75%(524K レコードで 132.8s)から、**S6 45 秒のうち 6.3 秒(14%)**になり、支配要因ではなくなった
* **残課題**: **A006 は依然 FAIL である。** 原因は F001 が想定していた実行時間ではなく、**S8(相関の集計)が既定の `max_memory = 4GB` で退避に失敗して終了コード 6 で異常終了すること**(通常 30 日分でも発生)、および**メモリを足して完走させても大規模 90 日で 876.2s となり NFR-002 の 600s に届かないこと**である。詳細と対処案は `P202-fix-unresolved.md` に記載した
* **修正経緯**: P201 の A006 が、1,460 ファイルの解析を CPU 時間 101.3 分でも完了できなかった。P202 が規模別に S6 のアルゴリズム別内訳を実測し、ALG-B4 だけが全体の 53% を占めていること、その約 85% が `_mean_after` / `_mean_before` の全走査で説明できることを分離ベンチマークで突き止めた。P203 が `bisect` による窓範囲走査へ置換し、戻り値が変わらないことを `report.md` の完全一致で確認した

### F002: テスト指示と上位文書の食い違い 6 件

* **対応するテストID**: A001 / A002 / A004 / A007 / A010 / A011
* **対応するテスト記録**: `docs/test-records/20260903-0010-test-record.md` の各テストの「テスト指示との矛盾の疑い」欄、および `docs/P201-review-report.md` 5 章
* **失敗していたテストコマンド**: **なし。** 6 件はいずれも A001〜A011 の FAIL を引き起こしていない。P201 の実行時に、テスト側で上位文書に従った実装を採ることで回避されていた。本タスクは**指示文書を実装・上位文書に追随させる**ものである
* **修正内容**:
  1. A001【期待結果】項目 10 の誤検知基準を UI-08-03(ADR-012)に合わせ、「イベント 0 件」から「`clean_series` の検知点が総点数の 1% 未満、かつ SEVERE が 0 件」へ改めた
  2. A002 / A004 / A007 の `settings.properties` の置き場所を UI-02-L01 に合わせ、「`s_anomaly.py` と同じディレクトリ」へ改めた(A004 は `temp_directory` の既定値 `./tmp` で意図が満たされる旨も追記)
  3. A002 / A010 の終了コード 7 の再現手順に「`PYTHONPATH` で環境側の DuckDB も隠す」を追記した。壊れた vendor だけではフォールバックが働くのが DS-14-02 の定めた正しい挙動である
  4. A011【事前準備】4 を、指示が併記していた代替手段(実行前後のファイル一覧の比較)へ確定した
* **変更したソースコード**: **なし(テスト指示側の誤りのため、`docs/P009-acceptance-direction/*.md` を修正)**
* **更新したdocs**:
  * `docs/P009-acceptance-direction/A001-normal-run.md`
  * `docs/P009-acceptance-direction/A002-exit-codes.md`
  * `docs/P009-acceptance-direction/A004-restart-resilience.md`
  * `docs/P009-acceptance-direction/A007-memory-spill.md`
  * `docs/P009-acceptance-direction/A010-portability.md`
  * `docs/P009-acceptance-direction/A011-user-privilege.md`
  * `docs/P202-fix-plan/fixed/F002-test-direction-corrections.md`(本ファイルの詳細)
  * `docs/P202-fix-plan.md`(目次を `[x]` に更新)
* **実行したテスト**: `python -m unittest discover -s app/tests/acceptance -t app` を**連続 2 回**
* **テスト結果**:
  * 1 回目 `Ran 122 tests in 501.553s` / `OK (skipped=13)`
  * 2 回目 `Ran 122 tests in 510.945s` / `OK (skipped=13)`
  * skipped=13 は A006 の 2 クラス(`S_ANOMALY_RUN_A006` 未設定のため既定でスキップ)。**実行されたのは 109 件**であり、0 件で静かに成功してはいない
* **残課題**: なし。訂正によるカバレッジの喪失がないことは、`fixed/F002-test-direction-corrections.md` の「訂正がカバレッジを失わせていないことの確認」に 6 件それぞれ記録した
* **修正経緯**: P201 が受け入れ結合テストを実施した際、テスト指示どおりの手順では確認が成立しない箇所が 6 件見つかった。いずれも実装は上位文書のとおりに動いており、テスト側が上位文書に従って回避していた。P202 がこれを「テスト指示側の誤り」区分としてまとめ、P203 が指示書を上位文書へ追随させた。**期待値の弱体化ではなく、根拠となる上位文書のセクション番号を各訂正に明記している**(`SKILL-P202-fix-plan.md` の結果区分「テスト指示側の誤り」の条件)


### F003: S8(相関の集計)がメモリ上限内で完走しない

* **対応するテストID**: A006
* **対応するテスト記録**: `docs/test-records/20260904-0335-test-record.md` の A006(修正前)、`docs/P202-fix-plan/fixed/F003-s8-correlate-memory.md` 3 章(修正後の実測)
* **失敗していたテストコマンド**: `S_ANOMALY_RUN_A006=1 python -m unittest discover -s app/tests/acceptance -t app -p "test_a006_performance.py" -v`(修正前は `FAILED (failures=7)`)
* **修正内容**: `correlate.py` の `collect` が `_event_windows` へ全イベントを一度に入れるのをやめ、**`EVENT_BATCH`(既定 500)件ずつ入れ替えて集計する**ようにした。**バッチ分の相関を組み立てたら集計結果を破棄する**ことで、DuckDB 側と Python 側の双方で中間結果を有界にした。`_aggregate_current` / `_aggregate_baseline` の **SQL 自体は変更していない**。集計は `GROUP BY` の第 1 キーが `event_id` でイベントごとに閉じているため、**バッチに分けても結果は変わらない**(`docs/ADR.md` **ADR-014**)
* **変更したソースコード**: `app/src/s_anomaly/correlate.py`(`collect`、および新設した定数 `EVENT_BATCH`)。**これ 1 ファイルのみ**
* **更新したdocs**:
  * `docs/P202-fix-plan/fixed/F003-s8-correlate-memory.md`(本ファイルの詳細)
  * `docs/P202-fix-plan/P202-fix-unresolved.md`(前ループの「F001 の残課題」を削除し、NFR-009 の欠陥を記載)
  * `docs/P003-backend-spec.md` DS-11-05(「全イベントを 1 回の結合で処理する」をバッチ分割の方式へ訂正)
  * `docs/ADR.md` **ADR-014 を追加**
  * `docs/ArchitectureHandbook.md` 9.1a(制約 #11・#12 を解消済みに更新、#15 を追加)
  * `app/INDEX.md`
  * `docs/P202-fix-plan.md`(目次を `[x]` に更新)
  * `docs/P001-requirement.md` NFR-002 / NFR-003 と `settings.properties` の既定値、検知アルゴリズムは**いずれも変更していない**(本タスクの禁止事項)
* **実行したテスト**: ビルド、単体 376 件 × 2 回、結合 105 件 × 2 回、受け入れ 109 件 × 2 回、A006 を確定規模で 2 回
* **テスト結果**:
  * `python -m compileall -q app` 終了コード 0
  * 単体 `Ran 376 tests` / `OK (skipped=1)` を 2 回(203.434s / 202.087s)
  * 結合 `Ran 105 tests` / `OK` を 2 回(551.113s / 528.939s)
  * 受け入れ `Ran 122 tests` / `OK (skipped=13)` を 2 回(503.575s / 512.691s)
  * **A006 全 13 件 PASS を連続 2 回**: `Ran 13 tests in 706.568s` / `OK`、`Ran 13 tests in 711.382s` / `OK`

  **既定の `max_memory = 4GB` のままでの実測**

  | ケース | テーブル行 | 修正前 | **修正後** |
  | --- | --- | --- | --- |
  | 通常 30 日 | 950,400 | 終了コード 6(363.6s で OOM) | **終了コード 0 / 147.4s・152.4s** |
  | 大規模 90 日 | 2,851,200 | 終了コード 6(828.7s / 847.2s で OOM) | **終了コード 0 / 531.3s・526.3s**(目標 600s) |

  S8 は大規模 90 日で **635 秒(`max_memory` 16GB 時)から 299 秒(4GB)**になった。**メモリ上限を上げるより速い。**
* **残課題**: **NFR-009(再現性)を満たしていない。** 同一入力・同一設定の 2 回実行で `report.md` が一致しない(F003 前 308 行差 / F003 後 34 行差)。原因は `_build` の `rank()` が、比例するメトリクス(`mu` と `mu_pct` など)の数学的に同値な変化率を、DuckDB の並列集計由来の浮動小数点の下位ビットで比較していることである。**F003 が持ち込んだものではない既存の欠陥**であり、F003 の範囲外のため着手していない。詳細と対処案 4 件は `P202-fix-unresolved.md` に記録した
* **修正経緯**: F001 が ALG-B4 の全走査を解消した結果、はじめて S8 に到達し、S8 が既定の 4GB で退避に失敗して異常終了することが判明した(通常 30 日分でも発生)。P204 が「F001/F002 は他機能を壊していない」と判定し、P205 の再判定で A006 が依然 FAIL であることを確認して P202 へ差し戻した。P202(2 回目)が対処案 1(バッチ分割)を採る F003 を起票し、P203 が実施した。**対処案 1 を選んだのは、NFR-003「あふれたら退避して継続する」(P000 2章の人間の指定)を満たす唯一の案であり、検知結果を変えないためである。** メモリ上限の引き上げは回避策にすぎず、中間結果が青天井である以上、規模が伸びれば再発する
