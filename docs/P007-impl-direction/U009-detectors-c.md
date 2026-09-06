# U009 — detectors-c: 観点3(上限への張り付き)と S6 の高速化・並列化

* 対象CR: [CR-005](../P901-cr-direction/CR-005.md) / [CR-006](../P901-cr-direction/CR-006.md) / [CR-007](../P901-cr-direction/CR-007.md)
* 依存: U005(検知器の共通契約)、U006、U008(レポート)
* 入力: `docs/P001-requirement.md` 10.2a / FR-055〜FR-060a、`docs/P003-backend-spec.md` 7.1a / 7.1b / 7.14 / 12章
* 関連ADR: **ADR-016**(上限の推定)、**ADR-017**(SQL 化しない)、**ADR-018**(スレッド並列)

**本書は「定義書 兼 実行指示書」である。** 記載の順に実装する。

---

## 1. 作るもの

| # | 対象 | 内容 |
| --- | --- | --- |
| 1 | `app/src/s_anomaly/detectors/c1_saturation.py`(新規) | ALG-C1。**SQL 実装** |
| 2 | `app/src/s_anomaly/detectors/__init__.py` | レジストリへ登録(10 → 11) |
| 3 | `app/src/s_anomaly/config.py` | `ALG-C1` と 3 パラメータ、`common.detect_threads` |
| 4 | `app/src/s_anomaly/reporter.py` | 名称・観点・説明文 |
| 5 | `app/src/s_anomaly/detectors/base.py` | **系列単位のキャッシュ** |
| 6 | 検知器 7 ファイル | `group_by_segment` → `fetch_segments` |
| 7 | `app/src/s_anomaly/cli.py` | **`detect` のループの向きとスレッド並列** |

---

## 2. ALG-C1(※CR-005)

**実装は `docs/P003-backend-spec.md` 7.14(DS-08-C1-01〜06)のとおり。**
ここでは実装時に間違えやすい点だけを挙げる。

### 2.1 「変化」ではなく「状態」を見る

観点1 は外れ値、観点2 は増加を見る。**観点3 は「上限に達して頭打ちのまま平坦」という、
変化していない状態を見る。** したがって

* **期間の最初から張り付いていても検知する。** 立ち上がりを必要としない。
* **`shape` は `sustained`** であり `trend_up` ではない。増えていないのだから。

### 2.2 誤検知のガードを外さない

**2 段のガードは、どちらも実際の誤検知を受けて入れたものである**(CR-005 対処記録 3.1)。

```python
if distinct < MIN_DISTINCT_VALUES:          # 0/1 のフラグ系列
    return [], [self.skip(series, SKIP_WIDE_DISTRIBUTION)]
if floor > 0 and (float(v_max) / floor) < min_spread:   # 常時ほぼ一定
    return [], [self.skip(series, SKIP_WIDE_DISTRIBUTION)]
```

**`floor` は最小値ではなく 5 パーセンタイルである。** 一度でも 0 を取ると
比が無限大になりガードが素通りする。**ここを最小値に「単純化」しないこと。**

### 2.3 新しいアルゴリズムを足すときに触る箇所は 3 つ

**実装したのに名称が空欄で出力された不具合があった。** 次の 3 箇所を必ず揃える。

| # | 箇所 |
| --- | --- |
| 1 | `detectors/__init__.py` の `_DETECTORS` |
| 2 | `config.py` の `ALGORITHM_IDS` とパラメータ |
| 3 | **`reporter.py` の `_ALGORITHM_NAMES` / `ALGORITHM_ASPECTS` / `_explain_one`** |

---

## 3. 系列単位のキャッシュ(※CR-006)

**`docs/P003-backend-spec.md` 7.1a のとおり。** 実装上の要点は 2 つ。

1. **スレッドローカルにする。** CR-007 で系列がスレッドに割り当てられるため、
   グローバルに持つと別の系列の点列を返しうる。
2. **`begin_series` を呼ばなければ従来どおり毎回問い合わせる。**
   単体テストは検知器を直接呼ぶため、この経路を壊さない。

**SQL 窓関数へは移さない。理由は `docs/ADR.md` ADR-017 にある。**
「SQL にすれば速くなるはず」と考えて着手する前に、必ず ADR-017 の実測値を読むこと。

---

## 4. S6 の並列実行(※CR-007)

**`docs/P003-backend-spec.md` 12章 DS-12-09 / DS-12-10 のとおり。** 要点は 4 つ。

1. **ループの向きを「検知器ごと」から「系列ごと」へ変える。** これが CR-006 と共通の変更。
2. **各スレッドが `con.cursor()` を持つ。** 共有接続へ戻すと**動くが 5 分の 1 の並列度になる**
   (ADR-018)。
3. **`failures` を末尾で `sort()` する。** 並列化で順序が変わる唯一の出力である(NFR-009)。
4. **FR-032 を保つ。** 例外は (検知器, 系列) 単位で捕まえ、他は続行する。
   **スレッド内で `try` を張り、スレッドごと落とさない。**

### 進捗ログの意味が変わる

**アルゴリズムごとの「所要」は全スレッドの合計であり、壁時計ではない。**
GIL を待つ時間を含むため、合計は壁時計より大きくなる(8 スレッドで 5〜7 倍になる)。
**壁時計は最後の `検知の総所要` 行に出す。** ここを取り違えると
「並列化して遅くなった」と誤読する。

---

## 5. 完了の判定

| # | 条件 |
| --- | --- |
| 1 | `python -m compileall -q app` が終了コード 0 |
| 2 | 単体テスト `tests/unit/test_detectors_c.py` が全件 PASS |
| 3 | 結合テスト T004 の `TestParallelDetect` が全件 PASS |
| 4 | **30 日規模で 1 スレッドと 8 スレッドの出力が、「実行日時」の行を除いて完全一致** |
| 5 | A006 が 30 日 / 90 日とも終了コード 0 |
