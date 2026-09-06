あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U003 — testdata

> ★訂正 2026-09-06★ **本書は Plan Loop 時点(アルゴリズム 10 個)の計画である。**
> **現在は 11 個**である(CR-005 で観点3 の ALG-C1 を追加)。
> 追加分の実装指示は `docs/P007-impl-direction/U009-detectors-c.md` にある。
> 現行の仕様は `docs/P001-requirement.md` 10章 / `docs/P003-backend-spec.md` 7章 が正である。


**位置づけ**: U004 以降のすべてのテストの入力を作る。**検知アルゴリズムの正解が分かっているデータ**が無いと、後続スプリントの単体テストが「動くこと」しか確認できない。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。
* 中断からの再開時は、該当タスクの【完了条件】を実際に再実行して現状を確認すること。
* **先行実装の禁止**: `[ ]` の後続タスクが対象とするファイルには着手しない。

- [x] U003-T1 [平常系の系列生成](#u003-t1-平常系の系列生成) — 周期性を持つノイズ系列と 3 形式のファイル出力
- [x] U003-T2 [異常の埋め込みと正解ファイル](#u003-t2-異常の埋め込みと正解ファイル) — 7 種の異常と expected.json
- [x] U003-T3 [破損データの生成](#u003-t3-破損データの生成) — `--broken` オプション

---

## U003-T1: 平常系の系列生成

### 【目的】

* `tools/gen_testdata.py` の骨格と、**異常を含まない平常系の系列**を生成する部分を作る。
* 生成したファイルが U002 のローダで読めることを確認する。

### 【作成・編集対象ファイル】

* `app/tools/gen_testdata.py` (新規)
* `app/tests/unit/test_gen_testdata.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P002-frontend-spec.md` 8章全体(8.1 書式、8.2 生成物、UI-08-01、UI-08-02)
* `docs/P001-requirement.md` FR-110、4章(3 形式の入力仕様)
* `docs/P003-backend-spec.md` 5.2 〜 5.4(ローダが期待する形式)

### 【実装内容】

1. **引数**: `python tools/gen_testdata.py <出力先dir> [--broken]`(`docs/P002-frontend-spec.md` 8.1)。`<出力先dir>` が無ければ作る。書き込めなければ終了コード 1。
2. **シードを `20260601` に固定**する(UI-08-02)。`random.Random(20260601)` のインスタンスを作り、グローバルな `random` を使わない(他のテストと干渉しないため)。
3. **期間を固定する**: `2026-06-01 00:00:00` 〜 `2026-06-14 23:55:00`、**5 分間隔**(UI-08-01)。1 系列あたり 4,032 点。**実行日に依存させてはならない**(`docs/P006-test-plan.md` TP-19)。
4. **生成する系列**(`docs/P002-frontend-spec.md` 8.2)。
   * ① `DBConnection_{yyyymmdd}.csv` を 14 日分。ホスト 2 台(`host01`, `host02`)× ポート `7003` × データソース 3 種(`OraclePool_1`, `OraclePool_2`, `OraclePool_3`)= 6 系列。
   * ② `{container}_gc_{host}_{yyyymmdd}.txt` を 14 日分。**`app01` は `-gcutil` 形式、`app02` は `-gc` 形式(18 列)** で出力する(UI-08-01 の意図: FR-012 の両形式を確実に踏む)。ホストは各 1 台。
   * ③ `bqueues_{host}_{yyyymmdd}.txt` を 14 日分。ホスト `lsfhost01` × QUEUE 3 種(`normal`, `short`, `long`)。
   * すべて `{出力先}/logs/` に置く。
5. **平常系の値の作り方**。単なる一様乱数ではなく、**日次の周期性**を持たせる(ALG-A5 が意味を持つようにするため)。
   ```
   value = base + amplitude * sin(2π * (時刻の日内位置)) + 曜日係数 + ノイズ
   ```
   * 曜日係数は土日を低くする。
   * ノイズは `rng.gauss(0, sigma)`。
   * **負にならないよう下限 0 でクリップ**し、整数メトリクス(`active_connections`, `njobs`, `pend`, `run`, `susp`, `ygc`, `fgc`)は `int` に丸める。
6. **jstat の累積値**(`ygc`, `ygct`, `fgc`, `fgct`, `gct`)は**単調増加**させる。各点で増分を加算する。`gct = ygct + fgct` の関係を保つ。
7. **`-gcutil` の値は 0〜100 の百分率**、**`-gc` の値は KB** とする。`-gc` では容量列(`oc`, `ec`, `mc` 等)を固定値とし、使用量列がその範囲に収まるようにする。
8. 各ファイルの 1 行目に見出しを書く。書式は `docs/P001-requirement.md` 4.1 〜 4.3 に厳密に従う。**bqueues の時刻は `yyyy-MM-dd HH:mm:ss:SS`(1/100 秒付き)で書く**(実データの形状を再現するため)。
9. 改行は LF、文字コードは BOM なし UTF-8 とする。

### 【実装してはいけないこと】

* 異常の埋め込み(T2 の担当)。**このタスクの生成物は、いかなるアルゴリズムでも検知されない平常系であること**が理想である(実際には統計的にわずかな検知が出うるが、意図的な異常は入れない)。
* `expected.json` の出力(T2 の担当)。
* `numpy` の使用。`math` と `random` のみを使う。
* グローバルな `random.seed()` の呼び出し。

### 【Unit Test内容】

* **テスト対象**: `gen_testdata` の系列生成関数、ファイル出力
* **正常系テスト**
  * 一時ディレクトリに生成し、`logs/` 配下のファイル数が期待どおりであること(① 14 + ② 28 + ③ 14 = 56 ファイル)。
  * 各ファイルの行数が「見出し 1 + 288(1 日 = 5 分間隔)× 系列数」であること。① は 1 ファイルに 6 系列ぶんの行が入るため 1+288×6。
  * **生成したファイルを U002 のローダで実際に読み、`ok_rows` が期待値と一致し、`errors` が空であること**(これが本タスクの最重要の確認)。3 形式すべてについて行う。
  * `app01` のファイルが `gc_format == "gcutil"`、`app02` が `"gc"` として読まれること。
  * **2 回生成してバイト単位で完全一致すること**(UI-08-02、決定性)。同じ一時ディレクトリでない 2 箇所に生成し、全ファイルのハッシュを比較する。
  * 出力ファイルの改行が LF であること(バイナリで読んで `\r\n` を含まないこと)。
  * 先頭 3 バイトが BOM(`\xef\xbb\xbf`)でないこと。
* **主要な異常系テスト**
  * 出力先に書き込めないパス(既存ファイルと同名)を渡すと終了コード 1 になること。
  * 出力先が存在しない場合に作成されること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
python app/tools/gen_testdata.py app/tests/_work/normal
```

### 【完了条件】

* テストが `OK` で終わること。
* 手動生成した `app/tests/_work/normal/logs/` に 56 ファイルが存在し、`python app/s_anomaly.py app/tests/_work/normal/logs` が終了コード 0 で完走すること(この時点では検知は動かないが、取り込みまでは通る)。

### 【次タスクに進む前の停止条件】

* 生成したデータを U002 のローダが読めない場合、**生成側とローダ側のどちらが誤っているかを切り分けてから**直すこと。設計書(`docs/P001-requirement.md` 4章)を正とする。
* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U003-T2: 異常の埋め込みと正解ファイル

### 【目的】

* `docs/P001-requirement.md` FR-111 の 7 種の異常を、既知の位置・既知の大きさで埋め込む。
* `expected.json`(正解ファイル)を出力する。

### 【作成・編集対象ファイル】

* `app/tools/gen_testdata.py` (編集)
* `app/tests/unit/test_gen_testdata_injection.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P001-requirement.md` FR-111 の表(7 種の異常と期待するアルゴリズム)
* `docs/P002-frontend-spec.md` 8.3 `expected.json` の書式、UI-08-03
* `docs/P003-backend-spec.md` 7.4 〜 7.13(各アルゴリズムが何を検知するか)

### 【実装内容】

1. **埋め込む異常を 7 種、それぞれ別の系列に入れる**(相互に干渉させないため)。`docs/P001-requirement.md` FR-111 の表に対応させる。

   | ID | 埋め込む系列 | 内容 | `expect_algorithms` | `expect_min_severity` |
   | --- | --- | --- | --- | --- |
   | INJ-001 | `db_connection/host01:7003/OraclePool_1` の `active_connections` | 2026-06-05 14:00 の **1 点だけ平常値の 10 倍** | `["ALG-A1","ALG-A2","ALG-A3"]` | `WARN` |
   | INJ-002 | `db_connection/host01:7003/OraclePool_2` の `active_connections` | 2026-06-07 09:00〜11:00 の **連続点を平常 + 2.5σ** | `["ALG-A2","ALG-A4"]` | `WARN` |
   | INJ-003 | `db_connection/host02:7003/OraclePool_3` の `active_connections` | **水曜 03:00〜04:00 だけ平常の 4 倍**(他の曜日は平常) | `["ALG-A5"]` | `WARN` |
   | INJ-004 | `jvm_gc/app01@host01` の `ou` | **鋸歯状のまま下限が単調増加**(GC のたびに下がるが、下限が 40% → 92% へ 14 日かけて上昇)。同時に `fgc` の増分を後半で増やす | `["ALG-B1","ALG-B2","ALG-B3","ALG-B5"]` | `FATAL` |
   | INJ-005 | `jvm_gc/app02@host02` の `eu` | 2026-06-08 12:00 を境に**水準が階段状に 3σ 上がり、元に戻らない** | `["ALG-B4"]` | `WARN` |
   | INJ-006 | `lsf_queue/lsfhost01/long` の `pend` | **単調増加し `run` は横ばい**(0 → 200 へ 14 日かけて) | `["ALG-B1","ALG-B2","ALG-B5"]` | `FATAL` |
   | INJ-007 | `jvm_gc/app01@host01` の `mu` | Metaspace 使用率が **緩やかに単調増加**(クラスローダリーク相当) | `["ALG-B2","ALG-B5"]` | `WARN` |

   ★FIXME★ 上表の具体的な数値(10 倍、2.5σ、40%→92% など)は、`docs/P006-test-plan.md` 4.3 の方針にもとづく Agent の想定である。実装後に検知されない場合は、**アルゴリズム側を疑う前に、埋め込みの大きさが各アルゴリズムの既定パラメータ(`docs/P003-backend-spec.md` 10.4 相当)に対して十分かを確認すること。**

2. **`clean_series` を定義する。** `lsf_queue/lsfhost01/short` を「いかなる異常も埋め込まない系列」とし、`expected.json` の `clean_series` に入れる(誤検知の確認用)。この系列には平常系のノイズのみを入れる。
3. **INJ-004 の作り方が最重要である。** メモリリークの模擬は次のようにする。
   * `floor(t)` = 40 + (92 - 40) × (t の経過割合) … 下限の包絡線
   * `ou(t)` = `floor(t)` + 鋸歯(GC 周期ごとに 0 から `saw_amplitude` まで上昇し、Full GC で `floor(t)` に戻る)
   * こうすると、**6 時間バケットの最小値が単調増加する**(ALG-B3 が検知する)。同時に全体の傾きも正になる(ALG-B5・ALG-B2 が検知する)。
4. `expected.json` を `{出力先}/expected.json` に出力する。書式は `docs/P002-frontend-spec.md` 8.3 に厳密に従う。
   * `generated_at` は**固定値** `"2026-06-01T00:00:00"` とする(実行日時を入れると決定性が壊れる)。
   * `seed`, `period`, `injected`(7 件), `clean_series` を含む。
   * `injected[]` の各要素は `id`, `kind`, `series_id`, `metric`, `from`, `to`, `expect_algorithms`, `expect_min_severity`, `description`。
   * **`series_id` の書式は `docs/P002-frontend-spec.md` 6.3 に厳密に従う**(後続のテストがこの文字列で突き合わせるため)。
5. JSON は `json.dump(..., ensure_ascii=False, indent=2, sort_keys=False)` で出力し、末尾に改行を 1 つ入れる。改行は LF。

### 【実装してはいけないこと】

* 検知アルゴリズムの実装(U005・U006 の担当)。
* `expected.json` に、アルゴリズムの内部統計量(σ 倍率、p 値)を書くこと。**書くのは「どのアルゴリズムが検知すべきか」までである。**
* 埋め込み位置を乱数で決めること(**固定の日時を使う**。正解が実行ごとに変わってはならない)。

### 【Unit Test内容】

* **テスト対象**: 異常埋め込み関数、`expected.json` の生成
* **正常系テスト**
  * `expected.json` が生成され、JSON として読めること。
  * `injected` が 7 件、`clean_series` が 1 件以上であること。
  * 各 `injected[].series_id` が `docs/P002-frontend-spec.md` 6.3 の書式に一致すること(正規表現で検証)。
  * 各 `expect_min_severity` が `INFO`/`WARN`/`FATAL`/`SEVERE` のいずれかであること。
  * 各 `expect_algorithms` の要素が `ALG-A1`〜`ALG-B5` の 10 個のいずれかであること。
  * **INJ-001 の位置(2026-06-05 14:00)の値が、その系列の平常値の 5 倍以上であること**を、生成した CSV を読み直して確認する。
  * **INJ-004 について、6 時間バケットの最小値の系列が単調非減少であり、始点と終点の差が 40 以上であること**を、生成した jstat ファイルを読み直して確認する。これが ALG-B3 の検知可能性を担保する。
  * **INJ-006 について、`pend` が単調増加し `run` の傾きがほぼ 0 であること**を確認する。
  * **2 回生成して `expected.json` がバイト単位で一致すること。**
  * `clean_series` に指定した系列の値が、平均 ± 4σ の範囲に収まっていること(明らかな外れ値が無いこと)。
* **主要な異常系テスト**
  * `expected.json` の `period` が固定日付であり、実行日を含まないこと。
  * `generated_at` が固定値であること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
python app/tools/gen_testdata.py app/tests/_work/normal
```

### 【完了条件】

* テストが `OK` で終わること。
* `app/tests/_work/normal/expected.json` が生成され、7 件の `injected` を含むこと。
* 生成した `logs/` を U002 のローダが引き続きエラーなく読めること(異常を埋め込んでも形式は壊れていないこと)。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U003-T3: 破損データの生成

### 【目的】

* `--broken` オプションで、`docs/P001-requirement.md` FR-112 の破損ケースを含むデータを生成する。

### 【作成・編集対象ファイル】

* `app/tools/gen_testdata.py` (編集)
* `app/tests/unit/test_gen_testdata_broken.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P001-requirement.md` FR-112
* `docs/P003-backend-spec.md` DS-LD-05(理由コード 4 種)
* `docs/P002-frontend-spec.md` 8.1(`--broken` の引数定義)

### 【実装内容】

1. `--broken` 指定時は、**破損データのみ**を `{出力先}/logs/` に生成する(平常系は生成しない)。`expected.json` も出力しない。
2. 次のケースを、それぞれ独立したファイルとして生成する。ファイル名は必ず 3 パターンのいずれかに一致させる(そうでないと `discovery` が拾わない)。

   | ファイル | 破損内容 | 期待する理由コード |
   | --- | --- | --- |
   | `DBConnection_20260601.csv` | 正常 5 行 + 列数不足 2 行 + 日付不正 2 行 + 数値不正 2 行 | 3 種が各 2 件、`ok_rows == 5` |
   | `DBConnection_20260602.csv` | 0 バイトの空ファイル | エラーなし、`ok_rows == 0` |
   | `DBConnection_20260603.csv` | 見出しのみ | エラーなし、`ok_rows == 0` |
   | `DBConnection_20260604.csv` | **BOM 付き UTF-8** の正常 5 行 | エラーなし、`ok_rows == 5` |
   | `DBConnection_20260605.csv` | **CRLF 改行**の正常 5 行 | エラーなし、`ok_rows == 5` |
   | `broken01_gc_host1_20260601.txt` | 見出しもデータも判別不能(列数 5) | `見出し不明`、`ok_rows == 0` |
   | `broken02_gc_host1_20260601.txt` | 正常な `-gcutil` 5 行 + 列数の違う行 2 行 | `列数不一致` 2 件、`ok_rows == 5` |
   | `bqueues_host1_20260601.txt` | 見出しに `_NJOBS` 等が無い | `見出し不明`、`ok_rows == 0` |
   | `bqueues_host2_20260601.txt` | 正常 5 行 + 時刻書式不正 2 行 | `日付書式不正` 2 件、`ok_rows == 5` |

3. **各ファイルの期待値を、テストコードから参照できる形でモジュール内の定数として持つ**(`BROKEN_EXPECTATIONS: Dict[str, Dict]`)。テストがこの定数と実際の解析結果を突き合わせる。
4. 生成は決定的であること(シード固定。乱数を使わないほうが望ましい)。

### 【実装してはいけないこと】

* `expected.json` の生成(破損データには検知の正解が無い)。
* 平常系データの生成(`--broken` 時は破損のみ)。
* ファイル名を 3 パターン以外にすること(`discovery` に拾われず、テストの意味が無くなる)。

### 【Unit Test内容】

* **テスト対象**: `--broken` の生成物
* **正常系テスト**
  * `--broken` で生成したディレクトリに、上表の 9 ファイルが存在すること。
  * **各ファイルを対応するローダで読み、`BROKEN_EXPECTATIONS` の期待値(`ok_rows` と `errors` の内訳)と完全一致すること。** これが本タスクの中心的な確認である。
  * `--broken` 実行時に `expected.json` が生成されないこと。
  * 生成が決定的であること(2 回実行してバイト単位一致)。
* **主要な異常系テスト**
  * `--broken` なしで生成したディレクトリには破損ファイルが含まれないこと。
  * BOM 付きファイルの先頭 3 バイトが `\xef\xbb\xbf` であること(意図した破損が実際に作られていることの確認)。
  * CRLF ファイルが実際に `\r\n` を含むこと。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
python app/tools/gen_testdata.py app/tests/_work/broken --broken
```

### 【完了条件】

* テストが `OK` で終わること。
* **手動で確認する**: `python app/s_anomaly.py app/tests/_work/broken/logs` を実行し、**終了コード 0**(1 件でも読めているため。FR-091)で完走し、標準エラーに破損の WARNING が出ること。
* U003 の全タスク(T1〜T3)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U003 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件に該当しない限り、次のタスクに自動的に進んでください。
