あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U005 — detectors-a

> ★訂正 2026-09-06★ **本書は Plan Loop 時点(アルゴリズム 10 個)の計画である。**
> **現在は 11 個**である(CR-005 で観点3 の ALG-C1 を追加)。
> 追加分の実装指示は `docs/P007-impl-direction/U009-detectors-c.md` にある。
> 現行の仕様は `docs/P001-requirement.md` 10章 / `docs/P003-backend-spec.md` 7章 が正である。


**位置づけ**: 検知の中核その 1(観点1 = 瞬間的な外れ値)。検知器の共通契約もここで確定するため、**U006 の土台**でもある。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。
* 中断からの再開時は、該当タスクの【完了条件】を実際に再実行して現状を確認すること。
* **先行実装の禁止**: `[ ]` の後続タスクが対象とするファイルには着手しない。

- [x] U005-T1 [検知器の共通契約と分散の下限](#u005-t1-検知器の共通契約と分散の下限) — base.py。Detection / Detector / effective_sigma
- [x] U005-T2 [ALG-A1 移動平均乖離率](#u005-t2-alg-a1-移動平均乖離率) — 窓ベースの σ 判定と区間まとめ
- [x] U005-T3 [ALG-A2 Hampel と ALG-A3 Tukey](#u005-t3-alg-a2-hampel-と-alg-a3-tukey) — 頑健な外れ値検知
- [x] U005-T4 [ALG-A4 EWMA と ALG-A5 曜日時刻別ベースライン](#u005-t4-alg-a4-ewma-と-alg-a5-曜日時刻別ベースライン) — 逐次計算と周期性
- [x] U005-T5 [レジストリと cli への結線](#u005-t5-レジストリと-cli-への結線) — S6 の進捗ログと失敗耐性

---

## U005-T1: 検知器の共通契約と分散の下限

### 【目的】

* 10 個のアルゴリズムが従う共通のインタフェース(`Detection`, `Detector`)を定義する。
* **本アプリで最も誤検知を生みやすい箇所**である `effective_sigma`(分散の下限設定)を実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/__init__.py` (新規)
* `app/src/s_anomaly/detectors/base.py` (新規)
* `app/tests/unit/test_detector_base.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.1(`Detection` / `Detector` の定義)、7.2 DS-08-03(`effective_sigma`)、7.3 DS-08-04, DS-08-05(スコアの正規化)
* `docs/P001-requirement.md` FR-051, FR-053
* `docs/P006-test-plan.md` F-10

### 【実装内容】

1. **`Detection` を dataclass で定義する**(`docs/P003-backend-spec.md` 7.1 のとおり)。
   * `series_id: str`, `source: str`, `metric: str`, `algorithm: str`, `start_ts: datetime`, `end_ts: datetime`, `values: List[float]`, `score: float`, `severity_hint: str`, `detail: Dict[str, Any]`, `shape: str`
   * **`shape` の許容値を定数として定義する**: `spike_up`, `spike_down`, `sustained`, `floor_rise`, `trend_up`, `level_shift`, `seasonal_dev`。この 7 種以外を使ってはならない。
2. **`SkipInfo` を dataclass で定義する**(`docs/P003-backend-spec.md` 7.1、DS-08-00)。
   * `series_id: str`, `metric: str`, `algorithm: str`, `reason: str`
   * `reason` は `"点数不足"` / `"分布が広く適用不可"` / `"基準期間の不足"` などの短い日本語とする。**`report.md` の 4.2 節・4.3 節へそのまま出る。**
3. **`Detector` を抽象基底クラスとして定義する**。`typing.Protocol` ではなく `abc.ABC` を使う(DS-00-01。Python 3.9 での実行時チェックが素直なため)。
   * クラス属性 `id: str`, `name: str`, `aspect: str`(`"観点1"` / `"観点2"`)
   * `min_points(self, cfg) -> int`
   * **`run(self, con, cfg, series, progress) -> Tuple[List[Detection], List[SkipInfo]]`**
   * 戻り値が 2 要素のタプルであるのは、検知できなかった理由を `report.md` へ運ぶ経路が必要であるため(DS-08-00)。**全アルゴリズムがこのシグネチャに従う。**
4. **`effective_sigma(sd, mu, cfg) -> float`** を実装する(DS-08-03)。
   ```
   effective_sigma = max(sd, floor_ratio * abs(mu), floor_abs)
   ```
   * `floor_ratio` = `cfg.param("common.sigma_floor_ratio")`(既定 0.02)
   * `floor_abs` = `cfg.param("common.sigma_floor_abs")`(既定 0.5)
   * `sd` が `None` または `NaN` の場合は 0 として扱う。
   * **戻り値は必ず正**であること(`floor_abs` が 0 に設定された場合に備え、最終的に `max(結果, 1e-9)` を取る)。
5. `fetch_series(con, series: SeriesMeta) -> List[Tuple[datetime, float]]` を作る。`metrics` から該当系列の `(ts, value)` を `ts` 昇順・**`segment` も一緒に**返す。多くのアルゴリズムが Python 側で逐次計算するため、共通で使う。
6. `score_to_hint(score: float) -> str` を作る(DS-08-05)。`score < 2.0` なら `"WARN"`、`>= 2.0` なら `"FATAL"`。
7. `merge_adjacent(detections, interval_sec, factor=2.0) -> List[Detection]` を作る(DS-08-A1-02 で使う)。
   * 同一 `(series_id, metric, algorithm)` の検知点のうち、**間隔が `interval_sec * factor` 以内**のものを 1 つの区間にまとめる。
   * まとめた検知の `score` は最大値、`start_ts`/`end_ts` は最小/最大、`values` は区間の値の並び。
   * **区間長が 3 点以上なら `shape` を `sustained` にする**(DS-08-A1-02)。
8. `take_values_with_context(points, i_start, i_end, context=2) -> List[float]` を作る。検知区間の値に**前後 2 点を含めて**返す(`report.md` の「値」欄で前後関係が読めるようにするため。DS-08-A1-02)。

### 【実装してはいけないこと】

* 個別のアルゴリズム(T2 以降の担当)。
* `numpy` / `statistics.stdev` 以外の外部統計ライブラリの使用。
* `effective_sigma` を各アルゴリズムで個別に実装すること。**必ず本モジュールの共通関数を使う**(DS-08-03 は「全アルゴリズムで共通に使う」と定めている)。

### 【Unit Test内容】

* **テスト対象**: `effective_sigma`, `score_to_hint`, `merge_adjacent`, `take_values_with_context`, `Detection`
* **正常系テスト**
  * `effective_sigma(sd=5.0, mu=100.0, cfg=既定)` が 5.0 を返すこと(実測 σ が下限を上回る場合)。
  * `effective_sigma(sd=0.1, mu=100.0, cfg=既定)` が **2.0** を返すこと(`0.02 * 100 = 2.0` が効く)。
  * `effective_sigma(sd=0.0, mu=0.0, cfg=既定)` が **0.5** を返すこと(`floor_abs` が効く)。
  * `score_to_hint(1.5)` が `"WARN"`、`score_to_hint(2.0)` が `"FATAL"` であること。
  * `merge_adjacent` が、300 秒間隔の連続する 5 点の検知を 1 件にまとめ、`shape` が `sustained` になること。
  * 離れた 2 点(間隔 3600 秒、`interval_sec=300`)がまとめられず 2 件のままであること。
  * `take_values_with_context` が前後 2 点を含めた値を返すこと。系列の端では範囲外に出ないこと。
* **主要な異常系テスト(重要)**
  * **`sd=None` を渡しても例外にならず、下限が返ること。**
  * **`mu` が負のとき `abs(mu)` が使われること**(`effective_sigma(sd=0, mu=-100)` が 2.0)。
  * `floor_ratio=0, floor_abs=0` の設定でも戻り値が正であること(ゼロ除算の防止)。
  * `merge_adjacent` に空リストを渡して空リストが返ること。
  * `merge_adjacent` に 1 件だけ渡すと、`shape` が変更されずそのまま返ること。
  * `Detection` の `shape` に定義外の文字列を入れようとしたとき、**定数の集合に含まれるかを検証するヘルパで検出できること**(実行時にエラーにする必要はないが、テストで全 7 種を確認する)。
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

## U005-T2: ALG-A1 移動平均乖離率

### 【目的】

* `docs/P000-concept-analysis.md` が例示した「移動平均乖離率 (2σ、3σ)」を実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/a1_ma_sigma.py` (新規)
* `app/tests/unit/test_alg_a1.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.4 DS-08-A1-01, DS-08-A1-02、7.3(score の定義)
* `docs/P006-test-plan.md` 4.3 の表(ALG-A1 の人工系列)
* `docs/P002-frontend-spec.md` 2.2(`ALG-A1.window_minutes`, `k_warn`, `k_fatal`)

### 【実装内容】

1. **窓は時刻ベース**(FR-054)。各点 `t` の直前 `window_minutes` 分(**`t` 自身を含まない**)の平均 `mu` と標本標準偏差 `sd` を求める。SQL の窓関数を使う。
   ```sql
   WINDOW win AS (PARTITION BY series_id, metric, segment ORDER BY ts
                  RANGE BETWEEN INTERVAL '{window_minutes}' MINUTE PRECEDING
                            AND INTERVAL '1' SECOND PRECEDING)
   ```
   * `avg(value) OVER win AS mu`, `stddev_samp(value) OVER win AS sd`, `count(*) OVER win AS n`
   * **`RANGE BETWEEN INTERVAL` が DuckDB のバージョンで通らない場合**は、Python 側で時刻ベースの窓を自前で走査する実装に切り替えてよい(意味が同じであること)。切り替えた場合はコメントに理由を残すこと。
2. `n >= cfg.param("common.min_points")` の点のみを判定対象とする。
3. `dev = abs(value - mu) / effective_sigma(sd, mu, cfg)` を計算する。
4. `dev >= k_warn` で検知。`score = dev / k_warn`(DS-08-04)。`severity_hint` は `score_to_hint(score)`。
   * **`k_fatal` は score の正規化に直接は使わないが、`dev >= k_fatal` のとき score が 2.0 以上になるよう `k_fatal / k_warn` の比を確認すること。** 既定(2.0 / 3.0)では `dev = 3.0` のとき `score = 1.5` となり FATAL にならない。したがって **score は `dev / k_warn` ではなく、次の折れ線で正規化する**。
     ```
     score = dev / k_warn                                    (dev < k_fatal のとき、0〜2.0 未満に収まるよう)
     正しくは: score = 1.0 + (dev - k_warn) / (k_fatal - k_warn)   (k_warn <= dev のとき)
     ```
     * すなわち `dev == k_warn` で `score == 1.0`、`dev == k_fatal` で `score == 2.0` になる線形補間とする。`dev > k_fatal` では 2.0 を超えて伸びる。
     * **`k_fatal == k_warn` のときはゼロ除算になるため、その場合は `score = 1.0 if dev >= k_warn else 0.0` とする**(設定の相関検証で `k_fatal > k_warn` は保証されているが、防御的に書く)。
   * この正規化規則は **ALG-A2・A3・A5 でも同じ考え方で適用する**(WARN 閾値で 1.0、FATAL 閾値で 2.0)。
5. `shape` は `value > mu` なら `spike_up`、そうでなければ `spike_down`。
6. **隣接する検知点を `merge_adjacent` で区間にまとめる**(DS-08-A1-02)。`interval_sec` は `SeriesMeta` のものを使う。
7. `detail` に `{"mu": ..., "sd": ..., "effective_sigma": ..., "dev": ..., "k_warn": ..., "k_fatal": ...}` を入れる(`report.md` の「説明」欄で使う)。
8. `min_points(cfg)` は `cfg.param("common.min_points")` を返す。

### 【実装してはいけないこと】

* 窓に現在点を含めること(**自分自身が平均・分散を汚染するため除外する**)。
* 点数ベースの窓幅。
* `effective_sigma` を使わずに生の `sd` で割ること(**ゼロ除算と誤検知の温床**)。

### 【Unit Test内容】

* **テスト対象**: `A1MovingAverage.run`
* **正常系テスト(`docs/P006-test-plan.md` 4.3 の表に対応)**
  * **平均 10・σ 1 の系列 200 点の 100 番目だけ 20** にした人工系列(`metrics` へ直接 INSERT する)で、**100 番目のみが検知される**こと。`detail["dev"]` が 8 以上であること。
  * 検知された `shape` が `spike_up` であること。
  * 下振れ(100 番目を 0 にする)で `shape` が `spike_down` であること。
  * **平常な系列(σ 1 のノイズのみ)で検知が 0 件**、または全体の 1% 未満であること(統計的に 3σ 超は稀に出るため、0 件を厳密に要求しない)。
  * `dev == k_warn` 相当のとき `score` がおよそ 1.0、`dev == k_fatal` 相当のとき およそ 2.0 になること。
  * 連続する 5 点を外れ値にすると、**1 件の検知にまとまり `shape` が `sustained`** になること。
  * `values` に前後 2 点の文脈が含まれること。
* **主要な異常系テスト(重要)**
  * **全点が同じ値の系列(σ = 0)で、検知が 0 件であること**(`effective_sigma` の下限が効き、ゼロ除算も起きないこと)。
  * **全点が 0 の系列で例外にならないこと。**
  * **0 と 1 だけを取る系列**(`susp` のような小さな整数)で、検知が過剰(半数以上)にならないこと。これが `effective_sigma` を入れた理由である。
  * 点数が `min_points` 未満の系列で、検知 0 件かつ例外にならないこと。
  * 系列に `segment` が 2 つある場合、**segment をまたいで窓が形成されない**こと。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* **平常系で誤検知が多発する場合、閾値を勝手に緩めてはならない。** `effective_sigma` の実装が DS-08-03 のとおりかを先に確認すること。それでも解消しない場合は停止して報告する。

---

## U005-T3: ALG-A2 Hampel と ALG-A3 Tukey

### 【目的】

* ALG-A1 の弱点(外れ値自身によるマスキング)を補う頑健な手法を 2 つ実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/a2_hampel.py` (新規)
* `app/src/s_anomaly/detectors/a3_tukey.py` (新規)
* `app/tests/unit/test_alg_a2.py` (新規)
* `app/tests/unit/test_alg_a3.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.5 DS-08-A2-01, DS-08-A2-02(Hampel)、7.6 DS-08-A3-01 〜 DS-08-A3-03(Tukey)
* `docs/P006-test-plan.md` 4.3 の表

### 【実装内容】

**ALG-A2 Hampel フィルタ**

1. **Python 側で計算する**(DS-08-A2-01 の ★ACCEPTED★)。`fetch_series` で系列を取り出し、時刻ベースの窓(直前 `window_minutes` 分、現在点を含まない)を自前で走査する。
   * 窓は**両端インデックスを進める方式**(sliding window)で実装し、各点で窓全体を再走査しない工夫をする。ただし中央値・MAD の計算は窓内の値のソートが必要なため、`statistics.median` を窓ごとに呼んでよい(1 系列 4,032 点、窓 12 点程度であれば十分速い)。
2. `med = median(窓内の値)`、`mad = median(|x - med| for x in 窓内)`。
3. `effective_mad = max(1.4826 * mad, floor_ratio * abs(med), floor_abs)`。**`effective_sigma` と同じ下限の考え方**を適用する(base の関数を `sd=1.4826*mad, mu=med` として呼べばよい)。
4. `dev = abs(value - med) / effective_mad >= k` で検知。
   * `score` は T2 の規則に合わせ、`k` を WARN 閾値、`k * 1.5` を FATAL 閾値とみなして線形補間する。★FIXME★ (ALG-A2 には FATAL 閾値のパラメータが `docs/P002-frontend-spec.md` 2.2 に無いため、`k * 1.5` を FATAL 相当とするのは Agent の想定。設定キーを増やさずに済ませる判断である)
5. `shape` は A1 と同じ規則。`merge_adjacent` も適用する。
6. `detail` に `{"median": ..., "mad": ..., "effective_mad": ..., "dev": ..., "k": ...}`。

**ALG-A3 Tukey の外れ値境界**

1. **系列全体**(窓なし)の四分位数を SQL で求める。
   ```sql
   SELECT quantile_cont(value, 0.25) AS q1, quantile_cont(value, 0.75) AS q3,
          median(value) AS med FROM metrics WHERE series_id = ? AND metric = ?
   ```
2. `iqr_eff = max(q3 - q1, floor_ratio * abs(med), floor_abs)`(DS-08-A3-02)。
3. 上側境界 `q3 + iqr_warn * iqr_eff`(WARN)/ `q3 + iqr_fatal * iqr_eff`(FATAL)。下側も対称に `q1 - iqr_warn * iqr_eff` / `q1 - iqr_fatal * iqr_eff`。
4. 境界を超えた点を検知。`score` は境界からの超過量で線形補間(WARN 境界で 1.0、FATAL 境界で 2.0)。
5. **DS-08-A3-03 の誤検知抑制を必ず実装する。** 検知点数が系列全長の **5%** を超えた場合、**そのアルゴリズム・その系列の検知をすべて破棄**し、`detail` ではなく戻り値とは別に「破棄した」旨を記録する。
   * 記録方法: `run` が返す `List[Detection]` を空にしたうえで、**`SkipInfo(series_id, metric, "ALG-A3", "分布が広く適用不可")` を返し**、`progress.warning` で「分布が広いため ALG-A3 を {series_id}/{metric} に適用できませんでした」を出す。
   * この `SkipInfo` は `report.md` の 4.3 節に載る(`docs/P002-frontend-spec.md` 4.1)。**`run` の戻り値がタプルであるのは、まさにこの経路のためである**(`docs/P003-backend-spec.md` DS-08-00。T1 で定義済み)。
6. `shape` は `spike_up` / `spike_down`。`merge_adjacent` を適用する。

### 【実装してはいけないこと】

* A2 で窓ごとに O(n) の全走査を行い、全体を O(n²) にすること(1 系列 4,032 点なので実害は小さいが、`docs/P001-requirement.md` NFR-002 のため避ける)。
* A3 で窓を使うこと(**系列全体の分布を使うのが A3 の特徴**であり、窓を使うと A1 と区別がつかなくなる)。
* 5% 超過時の破棄を省略すること(**周期性のある系列で大量の誤検知を生む**)。

### 【Unit Test内容】

* **テスト対象**: `A2Hampel.run`, `A3Tukey.run`
* **正常系テスト(A2)**
  * 平均 10・σ 1 の系列の 1 点だけ 20 → 検知されること。
  * **`docs/P006-test-plan.md` 4.3 の「A1 がマスキングで見落とす一方、A2 は検知する」ケース**: 連続 3 点を大きな外れ値(50)にした系列で、**A2 が 3 点すべてを検知すること**。同じ系列に A1 をかけて、A2 より検知数が少ない(またはスコアが低い)ことを比較で示す。これが A2 を入れた理由の実証である。
  * 平常系で検知が 0 件〜わずかであること。
* **正常系テスト(A3)**
  * 一様分布の系列に 1 点だけ大きな値 → その点のみ検知されること。
  * WARN 境界ちょうどで `score ≈ 1.0`、FATAL 境界ちょうどで `score ≈ 2.0` であること。
  * 下側の外れ値も検知されること。
* **主要な異常系テスト**
  * A2・A3 とも、**全点同値の系列で検知 0 件・例外なし**。
  * A2・A3 とも、**0/1 だけの系列で過剰検知にならない**こと。
  * A3 で、**日次の周期性が強い系列**(正弦波、振幅が大きい)を与え、**検知点が 5% を超えて破棄され、検知 0 件かつ WARNING が出ること**。スキップ情報が戻り値に含まれること。
  * A3 で `q1 == q3`(IQR = 0)の系列でゼロ除算にならないこと。
  * 点数不足の系列で 0 件かつ例外なし。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。
* A1(T2)のテストも引き続き合格していること(退行がないこと)。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U005-T4: ALG-A4 EWMA と ALG-A5 曜日時刻別ベースライン

### 【目的】

* 緩やかな水準変化に感度を持つ EWMA と、周期性を考慮した季節ベースラインを実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/a4_ewma.py` (新規)
* `app/src/s_anomaly/detectors/a5_seasonal.py` (新規)
* `app/tests/unit/test_alg_a4.py` (新規)
* `app/tests/unit/test_alg_a5.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.7 DS-08-A4-01, DS-08-A4-02、7.8 DS-08-A5-01 〜 DS-08-A5-04
* `docs/P006-test-plan.md` 4.3 の表

### 【実装内容】

**ALG-A4 EWMA 管理図**

1. 系列全体の平均 `mu` と標準偏差 `sd` を求め、`sigma = effective_sigma(sd, mu, cfg)` とする。
2. **Python 側で逐次計算する**(再帰のため SQL では表現できない。DS-08-A4-02)。
   ```
   z_0 = mu
   z_t = lam * x_t + (1 - lam) * z_{t-1}
   sigma_z(t) = sigma * sqrt( lam/(2-lam) * (1 - (1-lam)**(2*t)) )
   検知条件: abs(z_t - mu) > k * sigma_z(t)
   ```
   * `t` は 1 から始まる点の通番。`segment` が変わったら `z` を `mu` にリセットする。
3. `score` = `abs(z_t - mu) / (k * sigma_z(t))`。**WARN 閾値で 1.0 になる定義**であり、FATAL は 2.0 以上。
4. 連続する検知区間を `merge_adjacent` でまとめる。区間長 3 点以上なら `shape` は `sustained`、単点なら `spike_up`/`spike_down`。
5. `detail` に `{"mu": ..., "sigma": ..., "lambda": ..., "k": ..., "z": ..., "sigma_z": ...}`。

**ALG-A5 曜日・時刻別ベースライン**

1. **バケットは `(dayofweek(ts), hour(ts))` の 168 通り**(DS-08-A5-01)。SQL で集計する。
   ```sql
   SELECT dayofweek(ts) AS dow, hour(ts) AS hr,
          avg(value) AS mu, stddev_samp(value) AS sd, count(*) AS n,
          count(DISTINCT date_trunc('week', ts)) AS weeks
   FROM metrics WHERE series_id = ? AND metric = ? GROUP BY 1, 2
   ```
2. `weeks >= cfg.param("ALG-A5.min_weeks")`(既定 2)のバケットのみを基準に使う(DS-08-A5-02)。
   * 有効バケットが 1 つも無ければ、**系列をスキップして `SkipInfo` を返す**(検知 0 件、例外なし)。
3. **leave-one-out 補正を必ず実装する**(DS-08-A5-04)。各点の判定で、その点自身をバケット統計から除く。
   ```
   mu_loo  = (n * mu - x) / (n - 1)
   var_loo = ((n - 1) * var - (n / (n - 1)) * (x - mu) ** 2) / (n - 2)
   ```
   * `var` は標本分散(`sd ** 2`)。
   * **`n < 3` のバケットでは補正が計算できない**(分母が 0 以下)ため、そのバケットは判定対象外とする。
   * `var_loo` が負になった場合(浮動小数点誤差)は 0 とする。
4. `z = abs(x - mu_loo) / effective_sigma(sqrt(var_loo), mu_loo, cfg) >= cfg.param("ALG-A5.z")` で検知。
5. `score` は `z / ALG-A5.z`(WARN 閾値で 1.0)。`shape` は `seasonal_dev`。
6. `detail` に `{"dow": ..., "hour": ..., "bucket_mu": mu_loo, "bucket_sigma": ..., "z": ..., "weeks": ...}`。

### 【実装してはいけないこと】

* A4 を SQL の窓関数で書こうとすること(**再帰的定義であり表現できない**)。
* A5 で leave-one-out を省略すること(**自分自身が平均を引き上げ、検知力が落ちる**)。
* A5 のバケットを `(hour)` だけにすること(**曜日の区別が A5 の本質**)。
* `dayofweek` の戻り値の基準(0 始まりか 1 始まりか、日曜始まりか)を仮定してハードコードすること。**DuckDB の実際の戻り値をテストで確認してから使う**こと。

### 【Unit Test内容】

* **テスト対象**: `A4Ewma.run`, `A5Seasonal.run`
* **正常系テスト(A4)**
  * **`docs/P006-test-plan.md` 4.3 の「途中から平均が 0.5σ ずれる系列」で A4 が検知し、A1 が検知しない**こと。これが A4 を入れた理由の実証である。
  * 平常系で検知が 0 件〜わずかであること。
  * `sigma_z(t)` が `t` の増加とともに漸近値 `sigma * sqrt(lam/(2-lam))` に近づくこと(数値で確認)。
  * `segment` が変わったところで `z` がリセットされること。
* **正常系テスト(A5)**
  * **平日と週末で水準が異なる 4 週間の系列**を作り、**平日の 1 点だけ週末相当の値**にしたとき、その点が検知されること。
  * **同じ系列で、週末の値が平常どおりのときは検知されない**こと(A1 なら週末の低い値を外れ値として拾ってしまう可能性がある。ここが A5 の価値である)。
  * `DuckDB` の `dayofweek` の戻り値の範囲を明示的に確認するテストを 1 件置くこと。
* **主要な異常系テスト**
  * **1 週間分しかない系列**(`weeks < min_weeks`)で、検知 0 件・`SkipInfo` が返り・例外なし。
  * バケットに 2 点しかない(`n < 3`)場合に leave-one-out がゼロ除算にならないこと。
  * 全点同値の系列で A4・A5 とも検知 0 件・例外なし。
  * `lambda` を 1.0(上限)にしたとき A4 が例外にならないこと。
  * `var_loo` が浮動小数点誤差で負になるケース(全点ほぼ同値)で `sqrt` が例外にならないこと。
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

## U005-T5: レジストリと cli への結線

### 【目的】

* 検知器のレジストリを作り、`cli` に S6 を組み込む。**1 つの検知器が失敗しても全体を止めない**耐性を実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/detectors/__init__.py` (編集)
* `app/src/s_anomaly/cli.py` (編集)
* `app/tests/unit/test_detector_registry.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 7.1 DS-08-01, DS-08-02
* `docs/P002-frontend-spec.md` 3.3(S6 の進捗ログ 3 種)
* `docs/P001-requirement.md` FR-032, FR-053

### 【実装内容】

1. `REGISTRY: Dict[str, Detector]` を定義する。この時点では A1〜A5 の 5 個。**U006 で B1〜B5 を追加するため、追加しやすい形にする**(モジュールを import して `{d.id: d for d in [...]}` を作る)。
2. `enabled_detectors(cfg) -> List[Detector]` を作る。`cfg.enabled_algorithms()` の ID を **ID 昇順**で引いて返す。存在しない ID は無視する(config が既に検証済み)。
3. `cli.main` に S6 を組み込む。
   * `enabled_detectors` を回し、各検知器について `list_series` の全系列を処理する。
   * **アルゴリズム開始時**に INFO `{ALG-ID} {名称} を実行します (対象 {系列数} 系列)`(ステップ `S6`)。
   * **系列 100 件ごとに** INFO `{ALG-ID}: {処理済}/{総数} 系列`。
   * **アルゴリズム完了時**に INFO `{ALG-ID} 完了: 検知 {件数} 点, スキップ {件数} 系列, 所要 {秒:.1f}s`。
   * **1 系列の処理が例外を投げたら、`try/except Exception` で捕捉して WARNING を出し、その系列だけ飛ばして次へ進む**(FR-032、DS-08-02)。失敗した `(algorithm, series_id, 理由)` を集計して保持する(U008 でレポート 4.3 節に出す)。
   * 点数不足でスキップした系列も集計する(U008 でレポート 4.2 節に出す)。
   * この時点では S7 以降が未実装なので、S6 のあと検知点の総数を INFO で出して 0 を返す。
4. 検知結果は `List[Detection]` として保持する。**`(series_id, metric, algorithm, start_ts)` で安定ソート**しておく(再現性 NFR-009)。

### 【実装してはいけないこと】

* イベント統合・脅威度判定(U007 の担当)。
* 検知器の例外を握りつぶして無言で続けること(**必ず WARNING を出して集計する**)。
* 例外を捕捉せずに全体を止めること。

### 【Unit Test内容】

* **テスト対象**: `detectors.REGISTRY`, `enabled_detectors`, `cli.main`(S6 まで)
* **正常系テスト**
  * `REGISTRY` に 5 個(A1〜A5)が登録されており、**キーと各 Detector の `id` 属性が一致**すること。
  * すべての Detector の `aspect` が `"観点1"` であること。
  * `enabled_detectors` が、設定で一部を `false` にしたときにそれを除外すること。ID 昇順であること。
  * 全 `false` のとき空リストが返ること。
  * `main` が S6 の 3 種の進捗ログ(開始・完了)を出すこと。
  * 検知結果が `(series_id, metric, algorithm, start_ts)` 昇順であること。2 回実行して同一順序であること。
* **主要な異常系テスト(重要)**
  * **1 つの検知器の `run` が必ず例外を投げるようにモンキーパッチし、`main` が 0 を返して完走すること**(FR-032)。標準エラーに WARNING が出ること。他の検知器の結果が失われないこと。
  * 系列が 0 件のとき S6 が 0 件で完了し、例外にならないこと。
  * 全アルゴリズムが `false` のとき、S6 が「実行するアルゴリズムがありません」の WARNING を出して進むこと(`docs/P002-frontend-spec.md` UI-02-V03)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。
* **手動で確認する**: `python app/s_anomaly.py app/tests/_work/normal/logs` を実行し、S6 で A1〜A5 の 5 個が順に実行され、検知件数が表示され、終了コード 0 で完走すること。**所要時間が 1 分以内**であること(そうでなければ性能上の問題があるため停止条件を確認する)。
* U005 の全タスク(T1〜T5)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U005 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* 手動確認で所要時間が 5 分を超える場合、`docs/P001-requirement.md` NFR-002 に対する重大なリスクであるため、停止して報告する。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件に該当しない限り、次のタスクに自動的に進んでください。

---

> **P012 修正記録**: P012 により、矛盾点 #5(`Detector.run` の戻り値を `Tuple[List[Detection], List[SkipInfo]]` に確定し、`SkipInfo` の定義を T1 へ移動)を修正した。 詳細は `docs/P011-impact-analysis.md` を参照。
