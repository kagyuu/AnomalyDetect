あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U006 — detectors-b

**位置づけ**: 検知の中核その 2(観点2 = 持続的な数値の増加)。`docs/P000-concept-analysis.md` が例に挙げた**メモリリークの検出**を担う。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。
* 中断からの再開時は、該当タスクの【完了条件】を実際に再実行して現状を確認すること。
* **先行実装の禁止**: `[ ]` の後続タスクが対象とするファイルには着手しない。
* **前提**: U005-T1 / T3 で確定した `Detector.run` のシグネチャ(`Tuple[List[Detection], List[SkipInfo]]`)に従うこと。

- [x] U006-T1 [ALG-B1 移動平均クロスと ALG-B5 線形回帰](#u006-t1-alg-b1-移動平均クロスと-alg-b5-線形回帰) — SQL で完結する 2 つ
- [x] U006-T2 [ALG-B3 ローリング最小値の単調増加](#u006-t2-alg-b3-ローリング最小値の単調増加) — メモリリーク検出の主役
- [x] U006-T3 [ALG-B2 Mann-Kendall 傾向検定](#u006-t3-alg-b2-mann-kendall-傾向検定) — 非パラメトリック検定と Sen 勾配
- [x] U006-T4 [ALG-B4 CUSUM](#u006-t4-alg-b4-cusum) — 水準シフトの検出
- [x] U006-T5 [レジストリへの登録と検証](#u006-t5-レジストリへの登録と検証) — 10 個そろった状態での通し確認

---

## U006-T1: ALG-B1 移動平均クロスと ALG-B5 線形回帰

### 【目的】

* `docs/P000-concept-analysis.md` が例示した「長期の移動平均よりも短期の移動平均が常に上にある」(B1)と、増加率を人間が解釈しやすい形で示す線形回帰(B5)を実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/b1_ma_cross.py` (新規)
* `app/src/s_anomaly/detectors/b5_regression.py` (新規)
* `app/tests/unit/test_alg_b1.py` (新規)
* `app/tests/unit/test_alg_b5.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.9 DS-08-B1-01 〜 DS-08-B1-04、7.13 DS-08-B5-01 〜 DS-08-B5-04
* `docs/P006-test-plan.md` 4.3 の表

### 【実装内容】

**ALG-B1 短期/長期移動平均のクロス継続**

1. **時刻ベースの 2 つの移動平均**を SQL の窓関数で求める(DS-08-B1-01)。窓は `CURRENT ROW` まで含む。
   ```sql
   avg(value) OVER (PARTITION BY series_id, metric, segment ORDER BY ts
        RANGE BETWEEN INTERVAL '{short_minutes}' MINUTE PRECEDING AND CURRENT ROW) AS sma_s,
   avg(value) OVER (PARTITION BY series_id, metric, segment ORDER BY ts
        RANGE BETWEEN INTERVAL '{long_minutes}' MINUTE PRECEDING AND CURRENT ROW) AS sma_l
   ```
2. **長期窓が埋まるまでは判定しない**(DS-08-B1-02)。系列の先頭から `long_minutes` 分が経過するまでの点を除外する。
3. 検知条件は 2 つをともに満たすこと。
   * `sma_s > sma_l`
   * **`sma_s - sma_l > effective_sigma * 0.25`**(DS-08-B1-03。微小なクロスを除く)。`effective_sigma` は系列全体の平均・標準偏差から求める。
4. 上記が **`min_run` 点以上連続**する区間を 1 件の検知とする。
5. `score = 連続点数 / min_run`(DS-08-04)。`shape` は `trend_up`。
6. `values` は**区間を等間隔に 9 点サンプリング**したもの(DS-08-B1-04。`report.md` の省略規則に合わせる)。
7. `detail` に `{"run_length": ..., "short_minutes": ..., "long_minutes": ..., "min_run": ..., "sma_s_end": ..., "sma_l_end": ...}`。

**ALG-B5 線形回帰の傾き**

1. **系列全体**に対して DuckDB の回帰関数を使う(DS-08-B5-01)。
   ```sql
   SELECT regr_slope(value, epoch(ts)) AS slope_per_sec,
          regr_r2(value, epoch(ts)) AS r2,
          count(*) AS n, min(ts) AS t0, max(ts) AS t1,
          avg(value) AS mu, stddev_samp(value) AS sd
   FROM metrics WHERE series_id = ? AND metric = ?
   ```
   * **`regr_r2` が DuckDB のバージョンで使えない場合**は、`corr(value, epoch(ts)) ** 2` で代用してよい(単回帰では等価)。代用した場合はコメントに残すこと。
2. 検知条件は 3 つをすべて満たすこと(DS-08-B5-02)。
   * `slope_per_sec > 0`
   * `r2 >= cfg.param("ALG-B5.min_r2")`
   * `slope_per_sec * (epoch(t1) - epoch(t0)) > effective_sigma(sd, mu, cfg)`
3. `score = min(r2 / min_r2, 増加量 / effective_sigma)`(DS-08-B5-03)。**両方が十分に大きいときだけ高くなる**ようにする。
4. `shape` は `trend_up`。`start_ts`/`end_ts` は系列の両端。
5. `detail` に `{"slope_per_day": slope_per_sec * 86400, "r2": ..., "total_increase": ..., "effective_sigma": ...}`。**`slope_per_day` は `report.md` の説明欄で「1 日あたり N 増加」として使う**(DS-08-B5-04)。
6. `values` は系列を等間隔に 9 点サンプリングしたもの。

### 【実装してはいけないこと】

* B1 で長期窓が埋まる前の点を判定に含めること(**系列先頭で必ず誤検知する**)。
* B5 で `r2` だけを見て検知すること(**ほぼ横ばいの系列でも直線に乗っていれば R² は高くなる**。増加量の条件が必須)。
* 減少傾向(`slope < 0`)の検知(`docs/P000-concept-analysis.md` の観点2 は「増加」。`docs/P003-backend-spec.md` DS-08-B2-04 の方針を B5 にも適用する)。
* `segment` をまたいで移動平均・回帰を計算すること。

### 【Unit Test内容】

* **テスト対象**: `B1MaCross.run`, `B5Regression.run`
* **正常系テスト(B1)**
  * **前半 500 点が横ばい・後半 500 点が右肩上がり**の系列で、**後半のみ**が検知され、`run_length >= min_run` であること。
  * 完全に横ばいの系列で検知 0 件であること。
  * `values` の長さが 9 であること。
* **正常系テスト(B5)**
  * **傾き既知の直線**(例: 1 日あたり +10、5 分間隔で 14 日)+ 小ノイズ の系列で、**`detail["slope_per_day"]` が理論値 10 の ±10% 以内**であること。`r2` が 0.9 以上であること。
  * **横ばい + ノイズ**の系列で検知 0 件であること(`slope` がほぼ 0 で増加量の条件を満たさない)。
  * **減少する直線**で検知 0 件であること。
  * **R² は高いが増加量が微小**な系列(傾きが極めて小さい直線)で検知 0 件であること。これが増加量条件を入れた理由の実証である。
* **主要な異常系テスト**
  * 全点同値の系列で B1・B5 とも検知 0 件・例外なし(`regr_slope` が NULL を返す場合の扱いを確認)。
  * 点数不足(`min_points` 未満)の系列で 0 件・例外なし。
  * 2 点しかない系列で `regr_r2` が NULL/NaN になっても例外にならないこと。
  * `segment` が 2 つある系列で、segment をまたいだ移動平均が作られないこと。
  * `long_minutes` が系列の全期間より長い場合、B1 が検知 0 件で例外にならないこと。
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

## U006-T2: ALG-B3 ローリング最小値の単調増加

### 【目的】

* **メモリリーク検出の定番手法**を実装する。`docs/P000-concept-analysis.md` が観点2 の例として挙げた「メモリリークなど」に直接対応する、本アプリで最も重要なアルゴリズムである。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/b3_rolling_min.py` (新規)
* `app/tests/unit/test_alg_b3.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.11 DS-08-B3-01 〜 DS-08-B3-04
* `docs/P006-test-plan.md` 4.3 の表(B3 の 2 ケース)
* `docs/P001-requirement.md` 10.2(ALG-B3 の原理と弱点)

### 【実装内容】

1. 系列を `window_minutes`(既定 360 = 6 時間)の**非重複バケット**に分け、各バケットの最小値 `m_i` を求める(DS-08-B3-01)。
   ```sql
   SELECT time_bucket(INTERVAL '{window_minutes}' MINUTE, ts) AS b,
          min(value) AS m, count(*) AS n
   FROM metrics WHERE series_id = ? AND metric = ? AND segment = ?
   GROUP BY 1 ORDER BY 1
   ```
   * **`time_bucket` が DuckDB のバージョンで使えない場合**は、`epoch(ts) // (window_minutes*60)` による整数除算でバケット番号を作る方式に置き換えてよい。
2. **欠測バケットで run を分断する**(DS-08-B3-04)。バケット列を時刻の連番として展開し、**データが 1 点も無いバケットがあればそこで run を切る**。
   * 実装: 連続するバケットの開始時刻の差が `window_minutes` ちょうどでない箇所を欠測とみなす。
   * これを省略すると、採取が止まった前後で値が変わっただけのものを「単調増加」と誤認する。
3. **`m_{i+1} >= m_i` が続く最大の連続区間(run)**を求める(DS-08-B3-02)。**厳密な増加ではなく非減少**である点に注意する。
4. 検知条件は 2 つをともに満たすこと。
   * run の長さ >= `cfg.param("ALG-B3.min_increases")`(既定 10)
   * **run の総増加量 `m_end - m_start` > `effective_sigma`**(系列全体から算出)
   * 条件 2 は「ほぼ横ばいの系列で非減少が長く続いただけ」を除くためのもの。**省略してはならない。**
5. `score = run の長さ / min_increases`。`shape` は **`floor_rise`**。
6. `start_ts` は run 先頭バケットの開始時刻、`end_ts` は run 末尾バケットの終了時刻。
7. `values` は **`m_i` の並び**(バケット最小値の系列)を 9 点にサンプリングしたもの。**生の値ではなく下限包絡線を出す**こと。これが「最低値が上昇している」ことを人間に伝える。
8. `detail` に `{"run_length": ..., "floor_start": m_start, "floor_end": m_end, "total_rise": ..., "window_minutes": ..., "effective_sigma": ...}`。
9. `segment` ごとに独立して計算し、結果を結合する。

### 【実装してはいけないこと】

* 重複するスライディング窓を使うこと(**非重複バケット**である。DS-08-B3-01)。
* 欠測バケットの分断を省略すること。
* 総増加量の条件を省略すること。
* 厳密増加(`>`)を要求すること(**`>=` の非減少**。GC の実行タイミングにより同値が続くことがあるため)。

### 【Unit Test内容】

* **テスト対象**: `B3RollingMin.run`
* **正常系テスト(`docs/P006-test-plan.md` 4.3 の 2 ケース)**
  * **「鋸歯状で下限だけ上昇」(メモリリーク模擬)**: 5 分間隔 14 日の系列で、`floor(t) = 40 + 52*t割合`、その上に周期 2 時間・振幅 20 の鋸歯を乗せる。**全区間が 1 件として検知され、`shape == "floor_rise"`、`detail["total_rise"]` が 45 以上**であること。これが本タスクの中心的な確認である。
  * **「鋸歯状で下限が一定」**: 同じ鋸歯だが `floor` を一定にした系列で、**検知 0 件**であること。
  * `values` がバケット最小値の並びであり、単調非減少であること。
  * 検知された `start_ts` が系列先頭付近、`end_ts` が系列末尾付近であること。
* **主要な異常系テスト**
  * **途中に 1 日ぶんの欠測がある単調増加系列**で、**run が分断され 2 件になる(または短いほうが `min_increases` 未満で 1 件になる)**こと。1 件の長い run にならないこと。
  * **ほぼ横ばい + 微小ノイズ**の系列で、非減少が長く続いても**総増加量の条件により検知 0 件**であること。
  * 全点同値の系列で検知 0 件・例外なし。
  * バケット数が `min_increases` 未満になる短い系列で 0 件・例外なし。
  * `segment` が 2 つあり、それぞれで単調増加する系列で、**segment ごとに別々の検知**が出ること(またぐ 1 件にならないこと)。
  * 系列が 1 点のみの場合に例外にならないこと。
* **合格条件**: 上記すべてが合格すること。**特に「下限一定の鋸歯で検知 0 件」が合格すること**(これが失敗すると、正常な GC 動作をすべてメモリリークと報告してしまう)。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* **「下限一定の鋸歯」で検知が出てしまう場合、閾値を上げて逃げてはならない。** 総増加量の条件(条件 2)が正しく実装されているかを先に確認すること。

---

## U006-T3: ALG-B2 Mann-Kendall 傾向検定

### 【目的】

* 正規性を仮定しない非パラメトリック検定で、傾向の有無に統計的な裏付けを与える。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/b2_mann_kendall.py` (新規)
* `app/tests/unit/test_alg_b2.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.10 DS-08-B2-01 〜 DS-08-B2-05
* `docs/P006-test-plan.md` 4.3 の表(B2 の 2 ケース)

### 【実装内容】

1. **`bucket_minutes`(既定 60)で平均に集約してから適用する**(DS-08-B2-01)。
   ```sql
   SELECT time_bucket(INTERVAL '{bucket_minutes}' MINUTE, ts) AS b, avg(value) AS v
   FROM metrics WHERE series_id = ? AND metric = ? AND segment = ?
   GROUP BY 1 ORDER BY 1
   ```
2. **バケット数 `n` が `max_buckets`(既定 2000)を超える場合、超えないところまで `bucket_minutes` を 2 倍ずつ拡大する**(DS-08-B2-02)。計算量が O(n²) であるため。
3. **検定統計量を計算する**(DS-08-B2-03)。`math` のみを使う。
   ```
   S = Σ_{i<j} sign(x_j - x_i)
   同値グループの大きさ t_g を数え:
   Var = [ n(n-1)(2n+5) - Σ_g t_g(t_g-1)(2t_g+5) ] / 18
   Z = (S-1)/sqrt(Var)  (S>0),  0 (S=0),  (S+1)/sqrt(Var) (S<0)
   p = math.erfc(abs(Z) / math.sqrt(2))      # 両側
   ```
   * **`Var <= 0` のとき(全点同値など)は検知しない**(ゼロ除算を避ける)。
4. **`S > 0`(増加傾向)のみを検知する**(DS-08-B2-04)。`S <= 0` は検知しない。
5. 検知条件: `p < cfg.param("ALG-B2.p_warn")`(既定 0.05)。
6. `score` は p 値から対数補間する(DS-08-04)。
   ```
   p >= p_warn                 -> 0(検知しない)
   p_fatal <= p < p_warn       -> 1.0 + (log(p_warn) - log(p)) / (log(p_warn) - log(p_fatal))
   p < p_fatal                 -> 上式で 2.0 を超える(上限は設けない)
   ```
   * `p == 0` になった場合(極めて強い傾向)は `p` を `1e-300` にクリップしてから対数を取る。
7. **Theil-Sen 勾配**(DS-08-B2-05)。全ペアの `(x_j - x_i) / (j - i)` の中央値。
   * **`n > 500` のときは、等間隔に 500 点を選んで間引く**(乱数抽出は使わない。NFR-009)。
   * 勾配はバケット単位なので、`detail` には**1 日あたりの増加量**に換算して入れる: `sen_slope * (1440 / bucket_minutes)`。
8. `shape` は `trend_up`。`start_ts`/`end_ts` は系列(segment)の両端。
9. `detail` に `{"S": ..., "Z": ..., "p": ..., "n_buckets": ..., "bucket_minutes": ..., "sen_slope_per_day": ...}`。

### 【実装してはいけないこと】

* 集約せずに生の点数(4,032 点)で O(n²) を回すこと(**約 800 万ペアになり遅い**)。
* `scipy.stats` の使用(**`math.erfc` で代用する**)。
* 減少傾向の検知(DS-08-B2-04)。
* Sen 勾配の間引きに乱数を使うこと。

### 【Unit Test内容】

* **テスト対象**: `B2MannKendall.run`、および内部の `_mann_kendall(values) -> (S, Z, p)` と `_sen_slope(values)`
* **正常系テスト**
  * **`_mann_kendall([1,2,3,4,5])`** で `S == 10`(全ペアが正)、`p` が小さいこと。
  * **`_mann_kendall([5,4,3,2,1])`** で `S == -10`。
  * **`_mann_kendall([3,3,3,3,3])`** で `S == 0`、検知しないこと。
  * **`_sen_slope([0,2,4,6,8])`** が 2.0 であること(等間隔の直線)。
  * **`docs/P006-test-plan.md` 4.3 の「傾き既知の直線 + 小ノイズ」**: Sen 勾配(1 日換算)が理論値の ±10% 以内、`p < 0.01` であること。
  * **「傾向のない乱数系列(固定シード)」**: `p > 0.05` で**検知 0 件**であること。これが B2 を入れた理由の実証である。
  * 同値が多い系列で、tie 補正により `Var` が正しく小さくなること(補正なしの式と比較して値が異なることを確認)。
* **主要な異常系テスト**
  * **全点同値**の系列で `Var == 0` となり、**検知 0 件・ゼロ除算なし**。
  * **減少する直線**で検知 0 件であること(`S < 0`)。
  * バケット数が `max_buckets` を超える系列(細かい間隔で長期間)で、**`bucket_minutes` が自動的に拡大され、`detail["bucket_minutes"]` が既定値より大きくなる**こと。処理が現実的な時間(数秒)で終わること。
  * `p` が 0 になるほど強い傾向でも `math.log` が例外にならないこと。
  * 点数不足の系列で 0 件・例外なし。
  * `n = 1` のバケット数で例外にならないこと。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* テストの実行に 30 秒以上かかる場合、O(n²) の対策(集約・間引き)が効いていない可能性があるため確認すること。

---

## U006-T4: ALG-B4 CUSUM

### 【目的】

* 累積和管理図により、**水準シフト**(設定変更・負荷段階の変化)を検出する。リーク(徐々に増える)との切り分けを可能にする。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/b4_cusum.py` (新規)
* `app/tests/unit/test_alg_b4.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.12 DS-08-B4-01 〜 DS-08-B4-03
* `docs/P006-test-plan.md` 4.3 の表

### 【実装内容】

1. **系列の先頭 `baseline_minutes`(既定 1440 = 1 日)から基準 `mu`, `sd` を求める**(DS-08-B4-01)。`sigma = effective_sigma(sd, mu, cfg)`。
   * **基準区間の点数が `min_points` に満たない場合は系列をスキップ**し、`SkipInfo` を返す。
2. Python 側で逐次計算する。
   ```
   k = 0.5          # slack。固定値(DS-08-B4-01)
   S_plus = 0.0
   for each point x_t (基準区間の後の点も含め、系列の先頭から):
       S_plus = max(0.0, S_plus + (x_t - mu - k * sigma))
       if S_plus > h * sigma:   -> 検知
   ```
   * `h` は `cfg.param("ALG-B4.h")`(既定 5.0)。
3. **検知したら、`S_plus` が 0 から離れ始めた時刻を `start_ts`、閾値を超えた時刻を `end_ts` とする**(DS-08-B4-02)。`S_plus` が 0 になった直近の点を記録しておく。
4. **検知後は `S_plus` を 0 にリセットして走査を続ける**(複数の水準シフトを検出できるようにする)。
5. `score = S_plus / (h * sigma)`(検知時点の値。DS-08-04)。
6. `shape` は **`level_shift`**。
7. `detail` に `{"baseline_mu": mu, "baseline_sigma": sigma, "h": ..., "k": 0.5, "s_plus": ..., "shifted_mu": ...}`。
   * `shifted_mu` は**検知後 `baseline_minutes` 分の平均**(DS-08-B4-03)。系列の末尾に達している場合は得られる範囲で計算する。
8. `segment` ごとに独立して計算する。

### 【実装してはいけないこと】

* 下側の CUSUM(`S_minus`)の実装(観点2 は増加のみ)。
* 検知後にリセットせず走査を打ち切ること(**最初の 1 件しか出なくなる**)。
* `baseline` を系列全体から取ること(**シフト後の値が基準に混ざり、検出力が落ちる**)。

### 【Unit Test内容】

* **テスト対象**: `B4Cusum.run`
* **正常系テスト**
  * **`docs/P006-test-plan.md` 4.3 の「ある点で水準が 3σ 上がる階段」**: 前半 500 点が平均 10・σ 1、後半 500 点が平均 13 の系列で、**シフト点の近傍(前後 50 点以内)で検知される**こと。`shape == "level_shift"`。
  * `detail["shifted_mu"]` が 13 に近いこと。
  * `detail["baseline_mu"]` が 10 に近いこと。
  * **平常系(シフトなし)で検知 0 件**であること。
  * 2 回シフトする系列(10 → 13 → 16)で **2 件検知される**こと(リセットの確認)。
* **主要な異常系テスト**
  * **全点同値**の系列で検知 0 件・例外なし(`sigma` が下限で正、`S_plus` が増えない)。
  * **基準区間の点数が不足**する短い系列で、`SkipInfo` が返り検知 0 件・例外なし。
  * **緩やかな単調増加**(階段でない)の系列でも検知されること(CUSUM は緩やかな変化にも反応する)。ただしこれは誤りではないため、テストは「検知される」ことを確認するに留める。
  * `h` を極端に大きく(100)したとき検知 0 件になること。
  * `segment` が 2 つある場合、segment ごとに基準が取り直されること。
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

## U006-T5: レジストリへの登録と検証

### 【目的】

* B1〜B5 をレジストリへ登録し、**10 個そろった状態**で生成テストデータに対する通し確認を行う。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/__init__.py` (編集)
* `app/tests/unit/test_detector_registry.py` (編集)
* `app/tests/integration/__init__.py` (新規)
* `app/tests/integration/test_detect_injected.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.1 DS-08-01
* `docs/P002-frontend-spec.md` 8.3 `expected.json`、UI-08-03
* `docs/P001-requirement.md` FR-050, FR-113

### 【実装内容】

1. `REGISTRY` に B1〜B5 を追加し、**10 個**にする。
2. **`expected.json` を使った検証テストを書く**(結合テストの先取りだが、本スプリントの完了判定に必要)。
   * `app/tests/_work/normal/` の生成データを取り込み、`metrics` を構築し、10 個の検知器を全系列に走らせる。
   * `expected.json` の `injected` 7 件それぞれについて、**`expect_algorithms` のいずれかを含む検知が、期間の重なりを持って存在する**ことを確認する。
   * 期間の重なりの許容幅は**前後 1 窓幅(既定 60 分)**とする(`docs/P002-frontend-spec.md` 8.3 の ★FIXME★)。
   * `clean_series` に属する系列で、**検知が 0 件**であることを確認する。
3. **このテストが本スプリントの実質的な合格基準である。** 個々のアルゴリズムの単体テストが通っても、既定パラメータで実データ相当の異常を拾えなければ意味がない。

### 【実装してはいけないこと】

* イベント統合・脅威度判定(U007 の担当)。ここでは `Detection` のレベルで確認する。
* テストを通すためにパラメータの既定値を変更すること。**変更が必要と判断した場合は、変更内容と理由を報告してから行うこと**(`docs/P002-frontend-spec.md` 2.2 と `docs/P003-backend-spec.md` 10.4 の既定値は設計書の一部である)。

### 【Unit Test内容】

* **テスト対象**: `REGISTRY`、10 個そろった状態での検知
* **正常系テスト**
  * `REGISTRY` に 10 個が登録され、**キーと `id` 属性が一致**すること。
  * `aspect` が `"観点1"` 5 個・`"観点2"` 5 個であること。
  * **すべての Detector が `Detector` のサブクラスであり、`run` と `min_points` を実装していること。**
  * 全 Detector の `id` が `ALG-A1`〜`ALG-B5` の 10 種と完全一致すること(集合比較)。
* **結合テスト(`app/tests/integration/test_detect_injected.py`)**
  * **INJ-001 〜 INJ-007 の 7 件すべてについて、期待するアルゴリズムのいずれかが検知していること。**
  * **`clean_series` の検知が 0 件であること。**
  * 各 `injected` の検知が、期間の重なりを持つこと。
* **主要な異常系テスト**
  * 生成データが存在しない場合、テストが分かりやすく失敗する(または `skipUnless` でスキップする)こと。`app/tests/_work/normal` が無ければ `gen_testdata.py` を呼んで作ること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python app/tools/gen_testdata.py app/tests/_work/normal
python -m unittest discover -s app/tests/unit -t app -v
python -m unittest discover -s app/tests/integration -t app -v
```

### 【完了条件】

* 単体テスト・結合テストがともに `OK` で終わること。
* **手動で確認する**: `python app/s_anomaly.py app/tests/_work/normal/logs` を実行し、S6 で 10 個のアルゴリズムが実行され、検知件数が出て終了コード 0 になること。**所要時間が 3 分以内**であること。
* U006 の全タスク(T1〜T5)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U006 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* **`injected` の一部が検知されない場合**、次の順で切り分けること。(a) 生成データに意図した異常が実際に入っているか(U003-T2 のテストで確認済みのはず)、(b) そのアルゴリズムの単体テストが通っているか、(c) 既定パラメータが埋め込みの大きさに対して厳しすぎないか。**(c) と判断してパラメータを変える場合は、変更内容と理由を報告すること。**
* 所要時間が 10 分を超える場合は、`docs/P001-requirement.md` NFR-002 に対する重大なリスクであるため停止して報告する。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件に該当しない限り、次のタスクに自動的に進んでください。
