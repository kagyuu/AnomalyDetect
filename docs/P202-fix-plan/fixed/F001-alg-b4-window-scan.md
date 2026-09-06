あなたはReviewer Loop(修正担当)です。以下の1修正タスクを実施してください。

# 【修正タスクID】F001 — ALG-B4 の窓平均が系列全体を走査している

## 【対応する失敗テスト】A006

`docs/P009-acceptance-direction/A006-performance.md`(大規模データでの完走と実行時間)

* 対応するテスト記録: `docs/test-records/20260903-0010-test-record.md` の A006
* 違反した要件: **NFR-002**「1,000 万レコードで 10 分以内」

## 【障害記録】

* **原因区分: アプリケーションコードの欠陥**
* **症状**: 1,460 ファイル / 336MB のデータで実行し、**CPU 時間 101.3 分を消費した時点でも完了せず、中断した**。目標の 10 分を 10 倍以上超過している。中断時も 1 コアを専有して稼働しており、ハングではない。
  * ★訂正 2026-09-03 / P203★ 当初この規模を「10,091,520 レコード」と記載していたが誤りであった。**実際はテーブル行 7,778,880 行 / ファイル上のデータ行 6,622,560 行**であり、**目標の 1,000 万レコードには到達していなかった**。原因はテスト側の規模計算の誤りである(`docs/test-records/20260903-0010-test-record.md` の A006 末尾の訂正を参照)。**より小さい規模で超過していたため、FAIL の判定は変わらない。**
* **根本原因**: `app/src/s_anomaly/detectors/b4_cusum.py` の `_mean_after` / `_mean_before` が、**検知 1 件ごとに系列全体を走査している**。

  ```python
  def _mean_after(points, index, window_sec):
      start_ts = points[index][0]
      values = [v for ts, v in points[index:]                      # index から末尾まで全走査
                if (ts - start_ts).total_seconds() <= window_sec]  # そのうち窓内だけを残す
      if not values:
          return None
      return sum(values) / float(len(values))
  ```

  `_mean_before` も `points[:index + 1]` を同様に全走査する。
  窓幅は `baseline_minutes = 1440`(5 分間隔で 288 点)だが、91 日の系列は 26,208 点ある。
  **必要な 288 点を得るために毎回 26,000 点を走査している。**
  計算量は **O(検知件数 × 系列長)** である。

* **切り分けの根拠**: `docs/P202-fix-plan.md` 5章に詳細を記載した。要点は次の 2 つ。

  1. 規模別の実測で、**ALG-B4 が全体の 53%(S6 の 75%)**を占める。524K レコードで 132.8 秒。A006 の指示が「最初に疑う」よう挙げていた `ALG-B2`(6.6 秒)と `ALG-A2`(7.4 秒)は支配要因ではなかった。
  2. 分離ベンチマークで、実測条件(系列長 26,208 点 / 1 組あたり検知 143 件 / 窓 1440 分)における現行実装は 154 組換算で **113.1 秒**、`bisect` で窓の範囲だけを走査する案は **0.0 秒**であり、**戻り値は完全に一致する**。換算値 113.1 秒は実測 132.8 秒とよく一致し、**ALG-B4 の約 85% がこの 2 関数で説明できる**。

## 【参照ファイル】

| ファイル | 参照理由 |
| --- | --- |
| `app/src/s_anomaly/detectors/b4_cusum.py` | **修正対象。** `_mean_after` / `_mean_before` |
| `app/src/s_anomaly/detectors/base.py` | `seasonal_diffs` が既に 2 ポインタ走査で O(n) になっている。**同じ書き方に揃えるか、`bisect` を使うかを判断する材料** |
| `docs/P003-backend-spec.md` DS-08-B4 | ALG-B4 の設計。**アルゴリズムの意味を変えないことを確認する** |
| `docs/P001-requirement.md` NFR-002 | 目標値。**★FIXME★ が付された Agent の想定値である** |
| `docs/P009-acceptance-direction/A006-performance.md` | 期待結果と合否判定基準 |
| `app/tests/acceptance/test_a006_performance.py` | 検証に使うテスト。`SCALE` が生成規模を持つ |

## 【調査方針】

1. **作業前に `docs/P202-fix-plan.md` 4.1 の退避を必ず行う**(git 管理されていないため)。
2. `b4_cusum.py` を読み、`_mean_after` / `_mean_before` の呼び出し箇所と戻り値の用途を確認する。戻り値は `detail["level_before"]` / `detail["level_after"]` に入り、`report.md` の「説明」欄と `causes.py` の原因ルールで使われる。**値が変われば検知結果が変わる。**
3. 修正前の基準値を取る。**この値と一致することが修正の合格条件である。**
   ```
   python app/tests/integration/_setup_baseline.py
   cd <一時ディレクトリ> && python <repo>/app/s_anomaly.py <repo>/app/tests/_work/normal/logs
   ```
   得られた `report.md` を退避しておく。
4. 修正後、同じ入力で `report.md` が**実行日時の行を除いて完全一致する**ことを確認する。

## 【修正方針】

**窓の範囲を二分探索で求め、その範囲だけを走査する。** アルゴリズムの意味は変えない。

* `points` は時刻昇順であることが保証されている(`fetch_series` が `ORDER BY ts` で取得する)。したがって窓の境界は二分探索で求められる。
* 実装案(`docs/P202-fix-plan.md` 5.3 で戻り値の一致を確認済み):

  ```python
  import bisect

  # _scan の冒頭で 1 度だけ作る
  times = [p[0].timestamp() for p in points]

  def _mean_after(points, times, index, window_sec):
      hi = bisect.bisect_right(times, times[index] + window_sec)
      values = [points[j][1] for j in range(index, hi)]
      ...

  def _mean_before(points, times, index, window_sec):
      lo = bisect.bisect_left(times, times[index] - window_sec)
      values = [points[j][1] for j in range(lo, index + 1)]
      ...
  ```

* `times` の作成は系列あたり 1 回に留めること(検知ごとに作り直すと元の木阿弥になる)。
* **境界の扱いを現行と一致させること。** 現行は `_mean_after` が `<= window_sec`、`_mean_before` が `0 <= (end_ts - ts) <= window_sec` である。`bisect_right` / `bisect_left` の選択がこれに対応する。
* `bisect` は Python 標準ライブラリであり、**FR-003(duckdb 以外の外部依存を持たない)に抵触しない**。ただし `app/tests/acceptance/test_a008_no_network.py` の `STDLIB_ALLOWED` に `bisect` が含まれていることを確認すること(含まれている)。

**同種の実装が他にないかも確認する。** `points[index:]` / `points[:index]` のような全走査が他の検知器にあれば、同じタスクの中で直してよい(同一の根本原因である)。ただし**確認は行い、無ければ「無かった」と記録する**こと。

## 【試行錯誤してよい範囲】

* `b4_cusum.py` および他の `detectors/*.py` を読む
* 一時的にログを足して呼び出し回数・走査量を確認する(**確認後は必ず外す**)
* 修正案を複数比較する(2 ポインタ走査 / `bisect` / 事前計算した累積和など)
* `app/tests/acceptance/test_a006_performance.py` の `SCALE` を**一時的に小さくして反復測定する**ことは許可する。**ただし最終確認は必ず目標規模(既定の `SCALE`)で行い、縮小した規模の結果を「達成した」と記録してはならない。**

**範囲外**: 検知アルゴリズムの意味の変更(閾値・季節差分・リセット規則)、`settings.properties` の既定値変更、他の検知器の最適化(上記の「同種の全走査」を除く)。

## 【修正成功時に更新するdocs】

| 文書 | 更新内容 |
| --- | --- |
| `docs/P202-fix-plan/fixed/F001-alg-b4-window-scan.md` | 本ファイルを移し、修正内容の詳細を追記する |
| `docs/P202-fix-plan/P202-fix-resolved.md` | `TEMPLATE-P202-fix-resolved.md` に従って概要を記載 |
| `docs/P202-fix-plan.md` | F001 の行を `[x]` にし、リンク先を `fixed/` へ書き換える |
| `docs/ArchitectureHandbook.md` | 「既知の制約・技術的負債」に、**残存する性能上の制約**(NFR-002 に対する実測値)を記録する |
| `docs/ADR.md` | **窓平均の求め方を「時刻昇順を利用した二分探索」に定める ADR を追加するかを検討する。** ADR-011(一括挿入方式)と同じく、後続 Agent が同じ誤りを繰り返さないための記録として有用である |
| `docs/P003-backend-spec.md` DS-08-B4 | **実装の意味が変わらない場合は変更しない。** 計算量に関する記述があれば追記する |

**`docs/P001-requirement.md` NFR-002 の目標値は、本タスクでは変更しない。**

## 【ロールバック条件】

次のいずれかに該当した場合、**F001 で変更したファイルのみ**を退避物から戻す。

* 修正後の `report.md` が修正前と一致しない(検知結果が変わった)
* 単体テスト 376 件・結合テスト 105 件のいずれかが失敗する
* A001〜A011(A006 を除く)のいずれかが失敗する

**既に成功した別タスクの変更まで戻してはならない。**

## 【検証コマンド】

```
# 1. ビルド
python -m compileall -q app

# 2. 単体テスト (376 件)
python -m unittest discover -s app/tests/unit -t app

# 3. 結合テスト (105 件)
python -m unittest discover -s app/tests/integration -t app

# 4. 受け入れテスト (A006 を除く 109 件)
python -m unittest discover -s app/tests/acceptance -t app

# 5. A006 本体 (目標規模。数十分〜数時間かかる)
S_ANOMALY_RUN_A006=1 python -m unittest discover -s app/tests/acceptance -t app -p "test_a006_performance.py" -v
```

**5 の測定にあたっての注意(A006 の記録から引き継いだ手順上の欠陥)**

* `test_a006_performance.py` は `time.monotonic()` で壁時計を測るが、**実行中にマシンがスリープすると休止時間が算入され、測定値が使えなくなる**(P201 の実行で、経過 1,079.9 分に対し CPU は 101.3 分となった)。`subprocess.run(timeout=7200)` も発火しなかった。
* **CPU 時間も併せて記録すること。** 外部から次で取得できる。
  ```
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Select-Object ProcessId, @{n='CPU秒';e={[math]::Round($_.UserModeTime/1e7,1)}}, CommandLine
  ```
* 測定中はマシンをスリープさせないこと。

## 【完了条件】

1. `_mean_after` / `_mean_before` の計算量が **O(検知件数 × 系列長)から O(検知件数 × 窓幅)** になっている。
2. 修正後の `report.md` が修正前と**実行日時の行を除いて完全一致する**(検知結果が変わっていない)。
3. 検証コマンド 1〜4 が全件成功する。
4. **A006 を目標規模(テーブル行 11,563,200 行 / ファイル上のデータ行 10,406,880 行)で実行し、実測値を記録した。**
   * 記録すべき値: 総レコード数 / ファイル数 / **壁時計と CPU 時間の両方** / 終了コード / `report.md` の生成有無 / S6 のアルゴリズム別内訳
5. 上記【修正成功時に更新するdocs】の更新が済んでいる。

## 【未解決時の扱い】NFR-002 に届かなかった場合

**本タスクは、修正しても A006 が PASS にならない可能性が高い。**
`docs/P202-fix-plan.md` 5.4 の見積もりでは、F001 の効果を織り込んでも目標規模で
**約 44 分**であり、目標の 10 分には届かない。

この場合は**無理にコードで解決しようとせず**、次のとおり記録する。

* `docs/P202-fix-plan/P202-fix-unresolved.md` に `TEMPLATE-P202-fix-unresolved.md` の形式で記載する。
* 「仕様矛盾の有無」欄には次を書く。
  * **NFR-002「1,000 万レコードで 10 分以内」は ★FIXME★ が付された Agent の想定値であり、実運用の規模・許容時間の裏付けがない**(`docs/P001-requirement.md` 15章、`docs/P009-acceptance-direction.md` 6章 #2)。
  * 実装側の明らかな欠陥(F001)は解消したうえで、なお目標に届かないこと。
* 「人間に確認してほしいこと」欄には、**実測にもとづく具体的な問いを書く**。例:
  * 実運用のログ規模は実際どの程度か(1 回の解析対象は何レコードか)
  * 1 回の解析にどれだけの時間を許容できるか(夜間バッチなら数時間でもよいのか)
  * 目標に合わせるなら、検知アルゴリズムを絞る(`settings.properties` で ON/OFF)ことは許容できるか
* **NFR-002 の目標値を、失敗を回避する目的で書き換えてはならない。** 見直しの要否は P204(影響分析)を経て人間が判断する事項であり、`docs/P302-deliver.md` の「未整備事項・人間による確認事項」へ引き継ぐ。

## 重要:

* 作業開始前に現在の変更状態を確認してください(`docs/P202-fix-plan.md` 4.1 の退避)。
* 必要な範囲でソースコード変更を試して構いません。
* 修正に成功した場合は、関連する docs/* も必要に応じて更新してください。
* 修正しきれなかった場合は、試行錯誤で変更した未完了のソースコードを元の状態に戻してください。
* 原因が仕様矛盾の場合は、コードで無理に解決せず、人間に判断を促す内容を `docs/P202-fix-plan/P202-fix-unresolved.md` に記録してください。

---

# 【P203 実施記録】

* 実施日: 2026-09-04
* 実施フェーズ: P203(修正実施)
* **修正そのものの結果: RESOLVED**(本タスクが対象とした欠陥は解消した)
* **A006 の結果: 依然 FAIL。ただし原因が入れ替わった**(`../P202-fix-plan/P202-fix-unresolved.md`)

## 1. 実施した修正

`app/src/s_anomaly/detectors/b4_cusum.py`

* `_scan` の冒頭で **系列あたり 1 度だけ**「先頭からの経過秒」の配列 `offsets` を作る。
* `_mean_after` / `_mean_before` は `bisect_right` / `bisect_left` で窓の境界を求め、**その範囲だけを走査する**。
* 経過秒は `datetime.timestamp()` ではなく **先頭との `timedelta` 差**で求めた。`timestamp()` の差は夏時間の切り替えをまたぐと壁時計の差と一致せず、境界の判定が変わりうるためである(`docs/ADR.md` ADR-013)。
* 境界の対応: 旧 `_mean_after` は `<= window_sec` → `bisect_right`、旧 `_mean_before` は `0 <= (end_ts - ts) <= window_sec` → `bisect_left`。

**同種の全走査が他に無いことを確認した。** `points[index:]` / `points[:index]` 形の走査は `b4_cusum.py` の 2 箇所のみであった(`app/src/s_anomaly/detectors/*.py` および `app/src/s_anomaly/*.py` を検索)。

## 2. 完了条件の判定

| # | 完了条件 | 判定 | 根拠 |
| --- | --- | --- | --- |
| 1 | 計算量が O(検知件数 × 系列長) → O(検知件数 × 窓幅) | **達成** | `bisect` で窓の境界を求めてから走査する実装に置換。`offsets` は系列あたり 1 度だけ構築 |
| 2 | `report.md` が修正前と実行日時の行を除いて完全一致 | **達成** | 下記 2.1 |
| 3 | 検証コマンド 1〜4 が全件成功 | **達成** | 下記 2.2 |
| 4 | A006 を目標規模で実行し実測値を記録 | **達成(記録した)** | 下記 3。**ただし A006 自体は FAIL** |
| 5 | 【修正成功時に更新するdocs】の更新 | **達成** | 下記 5 |

### 2.1 検知結果が変わっていないことの確認(完了条件 2)

**退避 zip(`%TEMP%\s_anomaly-20260903004204.zip`)に修正前の
`app/` ツリーが丸ごと残っていた**ため、修正前後を**別々の一時ディレクトリへ展開し、
同一の入力ログに対して実行して突き合わせた**。ソースツリーには一切触れていない。

```
入力: app/tests/_work/normal/logs (14 日分, 56 ファイル)
旧: 退避 zip の app/  -> 終了コード 0 / report.md 477,751 バイト
新: 現行ツリーの app/ -> 終了コード 0 / report.md 477,751 バイト
```

**結果: 実行日時の行(`| 実行日時 |`。`reporter.py` 361 行)を除いて 9,107 行が完全一致。**

`reporter.py` で実行時刻に依存するのはこの 1 行だけであることを、`ReportContext.now` の
利用箇所を確認して裏づけた。

### 2.2 検証コマンド 1〜4(完了条件 3)

CLAUDE.md の「テストスイートは続けて 2 回実行して同じ結果になることを確認する」規則に従い、
**2〜4 はいずれも連続 2 回**実行した。

| # | コマンド | 1 回目 | 2 回目 |
| --- | --- | --- | --- |
| 1 | `python -m compileall -q app` | 終了コード 0 | — |
| 2 | `python -m unittest discover -s app/tests/unit -t app` | `Ran 376 tests in 629.860s` / `OK (skipped=1)` | `Ran 376 tests in 608.501s` / `OK (skipped=1)` |
| 3 | `python -m unittest discover -s app/tests/integration -t app` | `Ran 105 tests in 1057.719s` / `OK` | `Ran 105 tests in 676.081s` / `OK` |
| 4 | `python -m unittest discover -s app/tests/acceptance -t app` | `Ran 122 tests in 501.553s` / `OK (skipped=13)` | `Ran 122 tests in 510.945s` / `OK (skipped=13)` |

* 件数(376 / 105 / 122)が出ており、パス指定の誤りで 0 件が静かにスキップされてはいない。
* 4 の `skipped=13` は A006 の 2 クラス(大規模 9 件 + 通常 4 件)であり、`S_ANOMALY_RUN_A006`
  が無いために既定でスキップされる。**実行されたのは 109 件**である。
* 2 回目が 1 回目と同じ結果になっており、永続化データの残留による失敗は起きていない。

## 3. A006 の実測(完了条件 4)

**2026-09-03 に人間が確定した規模**(通常 30 日分 / 大規模 90 日分)で測定した。
`max_memory` を変えた比較も行った(切り分けのための設定変更のみ。**アプリケーション
コードは変更していない**)。

### 3.1 測定結果

| ケース | テーブル行 | ファイル | max_memory | 終了コード | 所要 | イベント | `report.md` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 通常 30 日 | 950,400 | 120 | **4GB(既定)** | **6** | 363.6s で異常終了 | — | **生成されず** |
| 通常 30 日 | 950,400 | 120 | 16GB | 0 | **191.3s** | 7,229 | 14,393,784 バイト |
| 通常 30 日 | 950,400 | 120 | 32GB | 0 | 192.0s | 7,229 | 14,393,784 バイト |
| **大規模 90 日** | 2,851,200 | 360 | **4GB(既定)** | **6** | 828.7s / 847.2s で異常終了 | — | **生成されず** |
| **大規模 90 日** | 2,851,200 | 360 | 16GB | 0 | **876.2s** | 21,307 | 41,213,665 バイト |

* 4GB での 2 回(828.7s / 847.2s)は `test_a006_performance.py` による測定、それ以外は
  同条件を unittest を介さず再現したもの。**4GB での異常終了は 4 回とも再現した**
  (通常 30 日 1 回、大規模 90 日 2 回、通常 30 日の再現 1 回)。
* 壁時計と CPU 時間の双方を採取した。テスト全体(13 件)は壁時計 1,235.5s に対し
  `python.exe` の CPU 秒ピークは 1,656.5s であり、**マシンのスリープによる測定値の
  破綻は起きていない**(P201 の 1,079.9 分 / CPU 101.3 分のような乖離がない)。
* 16GB と 32GB が同値(191.3s / 192.0s)であることから、**16GB で足りている**。

### 3.2 ステップ別の内訳

| ステップ | 通常 30 日 / 4GB | 通常 30 日 / 16GB | 大規模 90 日 / 16GB |
| --- | --- | --- | --- |
| S4 取り込み | 27s | 33s | 80s |
| S6 検知 | 45s | 41s | 152s |
| S7 統合 | 約 1s | 約 1s | 約 1s |
| **S8 相関** | **290s 経過後に OOM** | **114s(60%)** | **635s(72%)** |
| S9 出力 | — | 約 1s | 約 1s |
| **合計** | 363.6s(失敗) | 191.3s | 876.2s |

### 3.3 S6 のアルゴリズム別内訳(通常 30 日 / 156 系列)

| アルゴリズム | 検知点 | 所要 |
| --- | --- | --- |
| ALG-A1 | 6,673 | 3.7s |
| ALG-A2 | 404 | 6.2s |
| ALG-A3 | 5 | 2.6s |
| ALG-A4 | 3,216 | 4.5s |
| ALG-A5 | 35 | 4.6s |
| ALG-B1 | 12 | 4.1s |
| ALG-B2 | 8 | 5.0s |
| ALG-B3 | 59 | 2.6s |
| **ALG-B4** | **33,456** | **6.3s** |
| ALG-B5 | 4 | 4.1s |
| 合計 | 43,872 | 45s |

**F001 の効果が確認できる。** 修正前、ALG-B4 は S6 の 75%・全体の 53% を占めていた
(524K レコードで 132.8s)。修正後は **S6 45 秒のうち 6.3 秒(14%)**であり、
ALG-A2(6.2s)や ALG-B2(5.0s)と同程度に収まっている。**支配要因ではなくなった。**

## 4. A006 が依然 FAIL である理由(F001 の想定と異なる)

`docs/P202-fix-plan.md` 5.4 は「F001 だけでは NFR-002 に届かない見込み」としていたが、
**実際の障害は時間ではなく、新たに判明した別の欠陥である。**

1. **既定の `max_memory = 4GB` で S8(相関の集計)が退避に失敗し、終了コード 6 で
   異常終了する。通常のユースケース(30 日分)でも落ちる。**
   * DuckDB のメッセージ: `Out of Memory Error: failed to pin block of size 256.0 KiB (3.7 GiB/3.7 GiB used)`
   * アプリは既に `SET preserve_insertion_order = false` を設定済み(`bootstrap.py` 155 行)であり、
     DuckDB が挙げる 3 つの対処のうち 1 つは適用済みである。
   * `threads` は `config.py` の既知キーではなく、`settings.properties` に書いても
     「未知のキーを無視しました」となって効かない。**設定では絞れない。**
   * マシンの物理メモリは 51.2GB あり、**当たったのはアプリ自身の既定値である**。
2. **メモリを足して完走させても、大規模 90 日で 876.2s となり NFR-002(600s)に届かない。**
   * S8 だけで 635s であり、**S8 単独で目標を超える。**

**したがって、A006 の FAIL は F001 の未達ではなく、S8 の実装に起因する別の欠陥である。**
本タスクの範囲外(【試行錯誤してよい範囲】の「範囲外」に「他の検知器の最適化」とあり、
S8 は検知器ですらない)であるため、**修正せず `P202-fix-unresolved.md` に記録して
P204(影響分析)へ引き渡す。**

## 5. 更新したdocs

| 文書 | 更新内容 |
| --- | --- |
| `docs/P202-fix-plan/fixed/F001-alg-b4-window-scan.md` | 本ファイル(移動 + 本記録の追記) |
| `docs/P202-fix-plan/P202-fix-resolved.md` | F001 の概要を記載 |
| `docs/P202-fix-plan/P202-fix-unresolved.md` | **A006 が依然 FAIL であることと、新たに判明した S8 の欠陥**を記載 |
| `docs/P202-fix-plan.md` | F001 の行を `[x]` にし、リンク先を `fixed/` へ変更 |
| `docs/ADR.md` ADR-013 | 「残存リスク」を、確定した規模での実測値に置き換え |
| `docs/ArchitectureHandbook.md` | 「既知の制約・技術的負債」に残存する性能・メモリ上の制約を追記 |
| `docs/P003-backend-spec.md` DS-08-B4 | **変更しない。** 実装の意味は変わっておらず、計算量の記述も無かった |
| `docs/P001-requirement.md` NFR-002 | **変更しない**(本タスクの禁止事項) |
