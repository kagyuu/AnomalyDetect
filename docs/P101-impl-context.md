# P101 実装コンテキスト — Executor 向け申し送り

* 版: 初版(Executor Step 開始時)
* 入力: `app/INDEX.md`, `docs/ArchitectureHandbook.md`, `docs/ADR.md`, `docs/P007-impl-direction.md`
* 対象フェーズ: P101

> **この文書の使い方**
> Executor は、まず**本書**と、これから着手する **`docs/P007-impl-direction/U00N-*.md` の 1 スプリント分**だけを読めば着手できる。詳細仕様の全文(`docs/P002-frontend-spec.md` など)を最初から通読する必要はない。迷ったときの参照先は 5章にまとめてある。
> 本書は単一の文書であり、未完了スプリントが残る限り Executor 開始のたびに**同じ文書を最新の状態へ更新する**(新しい文書を都度作らない)。

---

## 1. 現在のソースツリーの状態

**`app/` にはまだ実装が存在しない(実装前)。** `app/INDEX.md`(P020 が作成したプレースホルダ)のみがある。

したがって **U001-T1 の最初の仕事は、ディレクトリ構成そのものを作ること**である。`docs/P003-backend-spec.md` 1.1 のツリーに従う。

```
app/
├── s_anomaly.py            ← U001-T1 で作る
├── settings.properties     ← U001-T4 で作る
├── VERSION                 ← U001-T1 で作る
├── INDEX.md                ← 既に存在(P020 のプレースホルダ)
├── src/s_anomaly/          ← U001〜U008 で埋めていく
├── vendor/                 ← 空ディレクトリでよい(実体は P302)
├── tools/gen_testdata.py   ← U003 で作る
└── tests/                  ← 各スプリントで足していく
```

**`pyproject.toml` / `setup.py` / `requirements.txt` は作らない。** 素のスクリプトツリーである(`docs/P007-impl-direction/U001-foundation.md` U001-T1 に理由あり)。

---

## 2. 遵守すべき技術的決定(`docs/ADR.md` より)

| ADR | 決定 | **実装時に守ること** |
| --- | --- | --- |
| ADR-001 | DuckDB を `vendor/` に展開同梱する | `vendor/{os}-{arch}-cp{X}{Y}/` を `sys.path` 先頭へ。無ければ環境の DuckDB へフォールバックし、**その事実を必ず INFO ログと `report.md` 1 章に残す** |
| ADR-002 | 外部パッケージは DuckDB のみ | `numpy` / `pandas` / `scipy` / `scikit-learn` / `statsmodels` / `pytest` / `coverage` を使わない。`socket` / `urllib` / `http` / `requests` も import しない |
| ADR-003 | `effective_sigma` を全アルゴリズム共通適用 | σ・MAD・IQR は必ず `detectors/base.py` の共通関数を通す。**各アルゴリズムで個別実装しない** |
| ADR-004 | jstat はデータ行の列数を見出しより優先して判別 | 値の個数 12 → `gcutil`、17/18 → `gc`。決まらなければ見出しの列名で判定 |
| ADR-005 | `*_pct` は検知対象に含めない | 検知は `ou` / `eu` / `mu` に対して行う。`*_pct` は脅威度判定と原因候補の材料のみ |
| ADR-006 | 単一プロセスの CLI | HTTP を持たない。テストはサブプロセス起動 |
| ADR-007 | 原因候補と脅威度はルールベース | 確度は**高/中/低の 3 段階**。百分率を出さない。LLM を呼ばない |
| ADR-008 | `Detector.run` は `SkipInfo` も返す | 戻り値は `Tuple[List[Detection], List[SkipInfo]]`。**10 個すべてがこのシグネチャに従う** |
| ADR-009 | Python 3.9 を動作下限とする | 下記 2.1 の禁止構文を使わない |
| ADR-010 | S7 → S8 → S7' の順で実行する | `cli` は `events` → `correlate` → `causes` の順に呼ぶ。**逆にすると原因候補が無言で出なくなる** |

### 2.1 使ってはいけない Python 構文(ADR-009)

| 使わない | 代わりに使う |
| --- | --- |
| `X \| None` 形式の型注釈(実行時評価されるもの) | `from typing import Optional` して `Optional[X]` |
| `match` 文 | `if` / `elif` |
| `tomllib` | `configparser` |
| `itertools.pairwise` | `zip(seq, seq[1:])` |
| `dataclasses` の `slots=True` | 指定しない |

**設計書のコード例に `Path | None` のような記法が現れたら、実装では `Optional[Path]` に読み替える。**

### 2.2 全スプリント共通の実装規則

* **終了コードは `cli.main()` の 1 箇所で決める。** 各モジュールは例外か戻り値で失敗を伝え、自分で `sys.exit` しない。
* **関数の内部で `datetime.now()` を暗黙に呼ばない。** 現在時刻は引数で受け取る。
* **乱数を使わない**(`tools/gen_testdata.py` を除く。そこもシード固定)。
* **順序が結果に影響する箇所は安定ソートにする。** タイブレーク用のキーを必ず含める。
* **行の解析失敗は例外にせず、理由コード(4 種のみ)で返す。**
* 出力ファイルは **BOM なし UTF-8・改行 LF**(Windows でも LF)。

---

## 3. これから着手するスプリント

`docs/P007-impl-direction.md` の目次で、**次に着手すべき未完了スプリント**は次のとおり。

| 状態 | スプリント | 指示書 |
| --- | --- | --- |
| **`[ ]` ← 次はこれ** | **U001 foundation** | `docs/P007-impl-direction/U001-foundation.md` |
| `[ ]` | U002 ingest | `docs/P007-impl-direction/U002-ingest.md` |
| `[ ]` | U003 testdata | `docs/P007-impl-direction/U003-testdata.md` |
| `[ ]` | U004 metrics | `docs/P007-impl-direction/U004-metrics.md` |
| `[ ]` | U005 detectors-a | `docs/P007-impl-direction/U005-detectors-a.md` |
| `[ ]` | U006 detectors-b | `docs/P007-impl-direction/U006-detectors-b.md` |
| `[ ]` | U007 events | `docs/P007-impl-direction/U007-events.md` |
| `[ ]` | U008 report | `docs/P007-impl-direction/U008-report.md` |

**番号順に実行する。** 先行スプリントが未完了の状態で後続に着手しない。

### 3.1 スプリント完了時にすること

1. スプリントファイル冒頭の「タスク一覧」の該当行を `[x]` に更新する。
2. 全タスクが `[x]` になったら、`docs/P007-impl-direction.md` の該当スプリント行を `[x]` に更新する。
3. 次のスプリントへ自動的に進む(人間の指示を待たない)。

### 3.2 停止条件(`SKILL.md` Executor Step)

* **単体テストを 3 回自己修正しても合格にできない場合**は、処理を停止して人間に報告する。
* 報告時は、問題の内容と**対処案**を `docs/.stop-report.md` に記録する。

---

## 4. テストの実行方法

```
# 単体テスト(各スプリントで実行する)
python -m unittest discover -s app/tests/unit -t app -v

# 単一のテストモジュールだけ
python -m unittest discover -s app/tests/unit -t app -p "test_config.py" -v
```

**`-t app` を省略しないこと。** 省略すると `tests` パッケージを import できず、テストが 0 件で静かに成功する。

**`Ran N tests` の N を必ず目視すること。** 0 件で `OK` が出ていないかを確認する。

`app/tests/__init__.py` には `app/src` を `sys.path` へ追加するブートストラップを置く(U001-T1)。空ファイルにしない。

---

## 5. 迷ったときの参照先

**まずここを見る。** 全文を通読する必要はない。

| 知りたいこと | 参照先 |
| --- | --- |
| 引数のバリデーション規則、usage の文言 | `docs/P002-frontend-spec.md` 1章 |
| **設定キーの一覧(約 32 個)と許容範囲** | `docs/P002-frontend-spec.md` 2.2 の表 |
| 進捗ログの書式と、どのステップで何を出すか | `docs/P002-frontend-spec.md` 3章 |
| **`report.md` の書式(4 章構成、イベントの 9 項目)** | `docs/P002-frontend-spec.md` 4章 |
| 終了コード 9 種の意味と、標準エラーに出す内容 | `docs/P002-frontend-spec.md` 5章 |
| **テーブルの列定義、`series_id` の書式** | `docs/P002-frontend-spec.md` 6章 |
| テストデータ生成ツールの仕様、`expected.json` | `docs/P002-frontend-spec.md` 8章 |
| `vendor/` の解決手順、DuckDB の初期化 | `docs/P003-backend-spec.md` 2章 |
| 3 形式の解析手順(特に jstat の形式判別) | `docs/P003-backend-spec.md` 5章 |
| `metrics` の構築(segment・差分・使用率) | `docs/P003-backend-spec.md` 6章 |
| **10 アルゴリズムの計算式と SQL** | `docs/P003-backend-spec.md` 7.4〜7.13 |
| イベント統合と脅威度判定(SEVERE の条件) | `docs/P003-backend-spec.md` 8章 |
| 原因候補の 9 ルール | `docs/P003-backend-spec.md` 9章 DS-10-03 |
| 相関情報の収集 | `docs/P003-backend-spec.md` 10章 |
| 全体像・設計の理由 | `docs/ArchitectureHandbook.md`, `docs/ADR.md` |

### 5.1 特に間違えやすい 5 点

実装前に必ず頭に入れておくこと。

1. **`Detector.run` の戻り値は `Tuple[List[Detection], List[SkipInfo]]`**(ADR-008)。`List[Detection]` だけではない。
2. **`cli` は S7 → S8 → S7' の順に呼ぶ**(ADR-010)。`causes` は `correlate` の後。
3. **`effective_sigma` を必ず通す**(ADR-003)。生の σ で割るとゼロ除算と誤検知の温床になる。
4. **jstat はデータ行の列数を優先して判別する**(ADR-004)。見出しを信じるとファイルが丸ごと失われる。
5. **`report.md` の項目を省略しない**。値が無くても定型文(`該当する候補なし (確度: —)` 等)を書く。省略すると後段の LLM が「異常がない」と誤読する。

---

## 6. 実装してはいけないこと(全スプリント共通)

* `docs/P002-frontend-spec.md` / `docs/P003-backend-spec.md` に無いコマンド・オプション・設定キー・メトリクス・アルゴリズム・出力項目の追加。
* **特に禁止**: `--output` / `--from` / `--to` / `--config` オプション(`docs/P002-frontend-spec.md` 9.1 で「拡張しない」と決定済み)。
* 10 個以外のアルゴリズム。
* 4 種以外の解析エラー理由コード。

不足や矛盾を見つけた場合は、実装で埋めずに **`docs/P007-impl-direction.md` の 5章「未解決事項」へ追記して報告する。**


---

## ※CR-010 追記(2026-09-08) — U010 `sar` の実装コンテキスト

**実装指示は `docs/P007-impl-direction/U010-sar.md` にある。着手前に必ず §3「静かに壊れる 3 箇所」を読む。**

| 読む順 | 文書 | 何が分かるか |
| --- | --- | --- |
| 1 | `docs/P007-impl-direction/U010-sar.md` | **タスク一覧と、間違えやすい点** |
| 2 | `docs/P003-backend-spec.md` 5.4a(DS-SAR-01〜08) | ローダの内部仕様 |
| 3 | `docs/P003-backend-spec.md` 6.3a(DS-07-07a/b) | `metrics` への合流と**許可リストの罠** |
| 4 | `docs/P003-backend-spec.md` 7.14(DS-08-C1-01a/03a) | ALG-C1 と sar の比率 |
| 5 | `docs/ArchitectureHandbook.md` 5.2a | **接頭 `pct_` と接尾 `_pct` の違い** |

**新規ファイルは `app/src/s_anomaly/loaders/sar.py` の 1 つだけである。** 残りはすべて既存ファイルへの追記であり、
**既存の振る舞いを変えてはならない**(`sa-*.csv` が無ければ CR-009 までと同じ結果になること)。
