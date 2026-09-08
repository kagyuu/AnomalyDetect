# P009 受け入れ結合テスト定義 兼 テスト指示書 — 目次

* 版: 初版
* 入力: `docs/P001-requirement.md`, `docs/P006-test-plan.md`
* 対象フェーズ: P009

## 1. この目次の使い方

* 本目次は OKF 形式である。状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の 3 種類。
* **1 項目 = 1 テストタスク**である。
* Reviewer Loop(P201)は、結果が **PASS / FAIL / BLOCKED のいずれであっても、記録を残した時点で** 該当行を `[x]` に更新する。
* **全テストが `[x]` になるまで P201 は完了しない。**
* **FAIL / BLOCKED が残っている場合は、P202(修正計画)への引き渡しが必要である。** P201 はその場で修正しない。

## 2. 本フェーズの対象範囲

`docs/P006-test-plan.md` 5.3 の A-01〜A-11 に対応する。**プロセスを実際に起動して、外側から観測する**テストである。

* **対象**: スプリントをまたぐ連携、システムテスト相当(`docs/P001-requirement.md` の要件・非機能要件の充足)、受け入れテスト相当(運用者の視点)、実行可能な範囲の性能・セキュリティ観点。
* **対象外**: 単体テスト(P007)、スプリント内の結合テスト(P008)。

**本アプリはネットワークを持たない単一プロセスの CLI である。** したがってクライアント・サーバ分離構成やリバースプロキシの論点は生じない。この判断は **ADR-006**(`docs/ADR.md`。`docs/P003-backend-spec.md` DS-16-01)として記録されており、本フェーズはその方針に従って**サブプロセス起動による実行**をテストハーネスの基本形とする。

## 3. テスト一覧

- [x] A001 [正常系の通し実行と正解突き合わせ](./P009-acceptance-direction/A001-normal-run.md) — 生成データ一式に対する実行と `expected.json` との照合 — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A002 [終了コード 9 種の再現](./P009-acceptance-direction/A002-exit-codes.md) — 0/1/2/3/4/5/6/7/99 をプロセスとして再現する — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A003 [再現性](./P009-acceptance-direction/A003-reproducibility.md) — 同一入力の 2 回実行でレポート群が一致する。**スレッド数を変えても一致する**(※CR-007) — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A004 [再起動耐性](./P009-acceptance-direction/A004-restart-resilience.md) — 前回の残骸がある状態での 2 回目の実行 — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A005 [両 OS での一致](./P009-acceptance-direction/A005-cross-os.md) — Windows と Linux(WSL2)で同一結果 — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A006 [大規模データでの完走と実行時間](./P009-acceptance-direction/A006-performance.md) — NFR-001 / NFR-002。**ALG-C1 の実行と S6 の並列度も記録する**(※CR-005 / CR-007) — **結果: PASS** — **★CR-001〜007により改訂。反映済み**
- [x] A007 [メモリ制限下での退避](./P009-acceptance-direction/A007-memory-spill.md) — `max_memory` を小さくしても完走する — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A008 [ネットワーク非通信の確認](./P009-acceptance-direction/A008-no-network.md) — NFR-008 の静的・動的確認 — **結果: PASS**
- [x] A009 [破損データでの完走](./P009-acceptance-direction/A009-broken-input.md) — 読み飛ばしの集計と終了コード 0 — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A010 [配布物としての可搬性](./P009-acceptance-direction/A010-portability.md) — コピー先での実行と `vendor/` 不在時の切り分け — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A011 [一般ユーザー権限での実行](./P009-acceptance-direction/A011-user-privilege.md) — 管理者権限を要求しないこと — **結果: PASS** — **★CR-001〜004により改訂。反映済み**
- [x] A012 [タイムゾーンの疎通確認](./P009-acceptance-direction/A012-timezone.md) — 入力JST/レポートUTC、入力UTC/レポートJST、sar が無い場合 — **結果: PASS** — **※CR-008で新設**
- [x] A013 [チャート出力の確認](./P009-acceptance-direction/A013-charts.md) — リンクとファイルの対応・重ね合わせが出ること・再現性・サマリ不変 — **結果: PASS** — **※CR-009で新設**
- [x] A014 [sar(sysstat)取り込みの確認](./P009-acceptance-direction/A014-sar.md) — sar 由来のイベントが実際に出ること・pct_idle を検知しないこと・後方互換 — **※CR-010で新設**

## 4. 全テスト共通の指示

### 4.1 テスト前のビルド

| 項目 | 内容 |
| --- | --- |
| ビルド対象 | `app/` 配下の全 Python ファイル(`src/`, `tools/`, `tests/`, `s_anomaly.py`) |
| ビルドコマンド | `python -m compileall -q app` |
| 追加の確認 | `python -c "import sys; sys.path.insert(0,'app/src'); import s_anomaly.cli"` が終了コード 0 |
| 成功条件 | 両コマンドが終了コード 0 で、構文エラーの出力が無いこと |
| 失敗時の記録方法 | `docs/test-records/YYYYMMDD-HHMM-test-record.md` に結果 `BLOCKED`、失敗ログ抜粋(構文エラー全文)、影響がありそうなモジュールを記録する |
| **失敗時の扱い** | **ビルドが失敗した場合、テストへ進んではならない。** `BLOCKED` として記録し、次のテストタスクへ進む |

**加えて、A001 以降のすべてのテストは、P103(結合テスト)が完了していることを前提とする。** P008 の全テストが `[x]` でない場合は `BLOCKED` として記録する。

### 4.2 テストハーネスの構成

* **サブプロセスとして起動する。**
  ```python
  subprocess.run([sys.executable, str(APP / "s_anomaly.py"), str(logs_dir)],
                 cwd=work_dir, capture_output=True, text=True, encoding="utf-8")
  ```
* **`cwd` は必ずテスト専用の一時ディレクトリにする。** `report.md` はカレントディレクトリに出力されるため(`docs/P001-requirement.md` FR-070)、リポジトリ直下で実行すると作業ツリーを汚す。
* 終了コードは `proc.returncode`、標準出力は `proc.stdout`、標準エラーは `proc.stderr` で得る。
* **`main()` を直接呼ぶ形にしない。** プロセスとしての終了コードと、標準出力・標準エラーの分離を確認することが本フェーズの目的の一部であるため。

### 4.3 テストデータのライフサイクル

`docs/P006-test-plan.md` 3章(TP-03 〜 TP-08)の方針に従う。**本フェーズで独自に方針を決めない。**

* **復元の単位は「テストスイートの実行ごと」。** 個々のテストに復元手順を書かない。
* **復元は、テスト対象アプリケーションを起動する前に、スイート全体で 1 回だけ行う。**
  ```
  python app/tests/integration/_setup_baseline.py
  ```
* **ベースライン**(TP-05): `app/tests/_work/normal/` と `app/tests/_work/broken/` に生成データがあり、カレントに `report.md` / `report.md.tmp` が無く、`temp_directory` が空の状態。
* 各テストは `tempfile.TemporaryDirectory` で自分専用の作業ディレクトリを作り、その中で起動する(TP-04)。`app/tests/_work/` は**読み取り専用**として扱う。

> **A004(再起動耐性)の例外**: `SKILL-P009-acceptance-direction.md` が指摘するとおり、**残骸が残っていること自体を確認するテスト**は、途中でベースライン復元が挟まると前提が壊れる。A004 は**他のテストとデータを共有せず**、自分の一時ディレクトリの中で「1 回目 → 残骸を残したまま 2 回目」を完結させる。スイートのベースライン復元は A004 の**開始前**にのみ行われ、A004 の 1 回目と 2 回目の間には挟まらない。

### 4.4 テスト記録

* 記録先: `docs/test-records/YYYYMMDD-HHMM-test-record.md`
* 記録形式: `.claude/skills/spec-driven-dev/TEMPLATE-test-record.md` の共通形式。
* **1 テストタスクにつき 1 見出しブロック。** テスト ID の接頭辞は `A` とする。
* **結果は必ず `PASS` / `FAIL` / `BLOCKED` / `NOT RUN` のいずれか 1 つ。**
* `FAIL` / `BLOCKED` のときは「失敗ログ抜粋」「再現手順」「影響がありそうなモジュール」を空欄にしない。

### 4.5 修正禁止事項(全テスト共通)

**P201 のテスト実行時点では、テストが失敗してもコードを修正してはならない。**

* **アプリケーションコードを修正しない。**
* **テストコードをその場で都合よく変更しない**(本フェーズの指示に従って新規作成することは除く)。
* **失敗したテストをスキップしない。**
* **期待値を変更して成功扱いにしない**(改ざん)。特に `app/tests/_work/normal/expected.json` と、各テストの期待値表を書き換えてはならない。
* **同じ失敗に対して場当たり的な再テストを繰り返さない。**
* 失敗内容をテスト記録に残し、**P202(修正計画)以降へ引き渡す。**

### 4.6 実行順序

A001 → A011 の番号順に実行する。先行するテストが `BLOCKED` でも、後続のテストは実行を試みる。

## 5. 実行コマンドの検証状況

**検証済み(P201、2026-09-03)。** 本目次および各テストファイルに記載した実行コマンドを実際に実行し、**いずれも件数が表示されること(0 件で静かに成功していないこと)を確認した**。

```
python -m unittest discover -s app/tests/acceptance -t app -p "{ファイル名}" -v
```

| テストID | ファイル | 件数 |
| --- | --- | --- |
| A001 | `test_a001_normal_run.py` | 15 |
| A002 | `test_a002_exit_codes.py` | 14 |
| A003 | `test_a003_reproducibility.py` | 10 |
| A004 | `test_a004_restart.py` | 9 |
| A005 | `test_a005_cross_os.py` | 12 |
| A006 | `test_a006_performance.py` | 9(既定ではスキップ。`S_ANOMALY_RUN_A006=1` で実行) |
| A007 | `test_a007_memory_spill.py` | 9 |
| A008 | `test_a008_no_network.py` | 9 |
| A009 | `test_a009_broken_input.py` | 12 |
| A010 | `test_a010_portability.py` | 10 |
| A011 | `test_a011_user_privilege.py` | 9 |

## 6. 未解決事項

| # | 内容 | 扱い |
| --- | --- | --- |
| 1 | A005(両 OS 一致)は WSL2 上の Ubuntu を Linux 環境として使う。**実運用の配布先 Linux とはディストリビューション・Python 版が異なる可能性がある** | P302 の「未整備事項・人間による確認事項」へ引き継ぐ |
| 2 | A006(性能)の目標値(1,000 万レコードで 10 分以内)は `docs/P001-requirement.md` NFR-002 の ★FIXME★ であり、Agent の想定である。**未達の場合、実装の問題か目標値の問題かを切り分ける必要がある** | 未達時は P202 で切り分ける。目標値の妥当性は人間の確認事項として P302 へ引き継ぐ |
| 3 | A008(非通信)は、`docs/P006-test-plan.md` 2.2 の ★ACCEPTED★ のとおり**静的確認 + 設定確認**で行い、通信遮断環境での実行は行わない | 閉域環境での初回実行時に人間が確認する項目として P302 へ引き継ぐ |
| 4 | A010(可搬性)は `vendor/` の実体が無い状態で行う。**`vendor/` からの読み込み経路の実証は P302 に委ねる**(`docs/P005-impl-plan.md` 7章の ★ACCEPTED★) | P302 の実行前チェックで必ず確認する |

---

## 7. P201 実行結果 (2026-09-03)

**全 11 テストが `[x]`。うち PASS 10 / FAIL 1 / BLOCKED 0 / NOT RUN 0。**

**A006 が FAIL であるため、P202(修正計画)への引き渡しが必要である。**
判定と引き渡し事項は `docs/P201-review-report.md`、個別の記録は
`docs/test-records/20260903-0010-test-record.md` を参照。

あわせて、**テスト指示側の問題を 6 件**(A001 の誤検知基準、UI-02-L01 と
食い違う設定ファイルの置き場所 3 件、vendor フォールバックの再現方法 2 件)
記録した。いずれもアプリケーションの欠陥ではない。詳細は
`docs/P201-review-report.md` 5章。
