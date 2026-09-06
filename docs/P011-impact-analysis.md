# P011 影響分析 — P010(1 回目)の矛盾点

* 版: P010 1 回目に対する影響分析
* 入力: `docs/P010-design-review.md`
* 対象フェーズ: P011

## 1. 分析の方針

`docs/P010-design-review.md` 3章の 9 件について、次を明らかにする。

1. **正とすべき記述はどちらか**(判断の根拠を上位文書に求める)
2. **修正が必要な箇所**(文書・節・具体的な記述)
3. **波及先**(その修正により、あわせて直す必要が生じる箇所)
4. **P012 での修正担当文書**

---

## 2. 矛盾点ごとの分析

### 2.1 矛盾点 #1 — テスト実行コマンドのパス指定(重大度: 高)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | `docs/P007-impl-direction.md` 3.7 の `-s app/tests/unit -t app` |
| **根拠** | コードの格納先は `SKILL-P007-impl-direction.md` の規定により `app/` に確定している(`docs/P003-backend-spec.md` DS-01-00)。`docs/P006-test-plan.md` 4.1 の `-s tests -t .` は、格納先が `app/` に確定する前(P006 執筆時点)の記述であり、**格納先の確定に追随できていない** |
| **修正が必要な箇所** | `docs/P006-test-plan.md` 4.1 TP-09 のコマンド 4 種すべて |
| **波及先** | なし(P006 4.1 の ★FIXME★ は「実際に実行して確認してから最終版とする」と述べており、この修正はその趣旨に沿う) |
| **P012 の修正担当** | `docs/P006-test-plan.md` |

**補足**: この矛盾は `SKILL.md` 共通指示が明示的に警告する事象(「パス指定の誤りで 0 件が静かにスキップされていないか」)に該当する。**誤ったコマンドでも `unittest` は終了コード 0 を返す**ため、実装工程で気づかないまま「テスト合格」と誤認される危険が最も高い。

### 2.2 矛盾点 #2 — 結合・受け入れテストの実行コマンド形式(重大度: 高)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | `-t app` を用いた `discover` 形式に統一する |
| **根拠** | `python -m unittest app.tests.integration.test_xxx` 形式は、`app` が Python パッケージであること(`app/__init__.py` の存在)を要求する。しかし `app/` は**配布単位のディレクトリ**であり(`docs/P003-backend-spec.md` DS-01-00)、Python パッケージではない。`app/__init__.py` を作ると、配布物に無意味なファイルが混ざるうえ、`-t app` 方式と二重の解決経路ができて混乱する |
| **追加で必要になること** | テストから `app/src/s_anomaly` を import できる必要がある。`-t app` 方式では `sys.path[0]` が `app/` になるだけで `app/src` は入らない。**`app/tests/__init__.py` に `sys.path` へ `app/src` を追加する処理を置く**ことで解決する(`tests` パッケージが import された時点で必ず実行されるため、全テストで確実に効く) |
| **修正が必要な箇所** | (a) `docs/P007-impl-direction.md` 3.7 に、単一モジュール・単一メソッドの実行形を追記<br>(b) `docs/P007-impl-direction/U001-foundation.md` U001-T1 の `app/tests/__init__.py` を「空ファイル」から「`sys.path` ブートストラップを含む」へ変更<br>(c) `docs/P008-test-direction/T001`〜`T008` の【実行コマンド】8 箇所<br>(d) `docs/P009-acceptance-direction/A001`〜`A011` の【実行コマンド】11 箇所 |
| **波及先** | `docs/P006-test-plan.md` 4.1(#1 と同時に直す) |
| **P012 の修正担当** | `docs/P006-test-plan.md`, `docs/P007-impl-direction.md`, `docs/P007-impl-direction/U001-foundation.md`, `docs/P008-test-direction/T00N-*.md`(8 件), `docs/P009-acceptance-direction/A0NN-*.md`(11 件) |

### 2.3 矛盾点 #3 — テストデータ ② の生成規模(重大度: 中)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | **2 組(28 ファイル)** |
| **根拠** | `docs/P007-impl-direction/U003-testdata.md` U003-T2 の `expected.json` の定義が、`jvm_gc/app01@host01` と `jvm_gc/app02@host02` の **2 系列のみ**を参照している。4 組(app01@host1, app01@host2, app02@host1, app02@host2)にすると、`expected.json` が参照しない系列が 2 つ増え、`clean_series` にも含まれないため**判定対象外の系列**になる。テストの意図が曖昧になるため、2 組が正しい |
| **修正が必要な箇所** | `docs/P002-frontend-spec.md` 8.2 の ② の行。「コンテナ 2 × ホスト 2」→「コンテナ 2 × ホスト 2 台(組み合わせは `app01@host01` と `app02@host02` の 2 組)」と明記する |
| **波及先** | `docs/P007-impl-direction/U003-testdata.md` U003-T1 の【完了条件】(56 ファイル = ① 14 + ② 28 + ③ 14)は**修正不要**。修正後の P002 と一致する |
| **P012 の修正担当** | `docs/P002-frontend-spec.md` |

### 2.4 矛盾点 #4 — 処理フロー図の実行順序(重大度: 中)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | `docs/P003-backend-spec.md` DS-10-02 の **S7 → S8 → S7'** |
| **根拠** | 原因候補のルール CR-01 / CR-02 / CR-04 / CR-06 は、いずれも相関情報(`significant`、`change_ratio`)を条件に含む(`docs/P003-backend-spec.md` DS-10-03)。相関を先に算出しなければ、これら 4 ルールは論理的に発火しえない。**P001 の図が実装可能な順序を表していない**のであり、図の側が誤りである |
| **修正が必要な箇所** | `docs/P001-requirement.md` 7章の Mermaid 図。`S7 イベント統合` を「S7 イベント統合(重複マージ・脅威度判定)」に、`S8 相関情報付与` の後に `S7' 原因候補付与` のノードを追加する。あわせて FR-032 の直前の本文にも順序を明記する |
| **波及先** | `docs/P004-traceability-matrix.md` 6章の追跡表(P003 17.1 #1 の「解決状況: 未解決」)を「解決済(P012 で P001 の図を修正)」に更新する。`docs/P007-impl-direction.md` 5章 #1(未解決事項)も同様 |
| **P012 の修正担当** | `docs/P001-requirement.md`, `docs/P004-traceability-matrix.md`, `docs/P007-impl-direction.md` |

**注**: `docs/P001-requirement.md` の修正は、**要求内容の変更ではなく図の誤りの是正**である。P001 の要求(FR-074 が原因候補を、FR-075 が相関情報を求めること)自体は変わらない。

### 2.5 矛盾点 #5 — `Detector.run` の戻り値(重大度: 中)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | `-> Tuple[List[Detection], List[SkipInfo]]` |
| **根拠** | `docs/P002-frontend-spec.md` 4.1 は `report.md` の 4.2 節(点数不足でスキップした系列)と 4.3 節(失敗したアルゴリズム)の出力を要求している。また `docs/P003-backend-spec.md` DS-08-A3-03 は ALG-A3 の「分布が広いため適用できませんでした」を 4.3 節に載せることを求めている。**これらの情報を検知器からレポートまで運ぶ経路が、`list[Detection]` だけでは存在しない**。したがって戻り値の拡張が必要であり、P003 7.1 の定義が不足している |
| **修正が必要な箇所** | `docs/P003-backend-spec.md` 7.1 の `Detector` 定義。`run` の戻り値を変更し、`SkipInfo` の dataclass 定義を追加する |
| **波及先** | `docs/P007-impl-direction/U005-detectors-a.md` U005-T3 の ★FIXME★(「P010 で P003 7.1 の記述を更新する必要がある」)を解消済みの記述に更新する。`docs/P007-impl-direction/U005-detectors-a.md` U005-T1 の【実装内容】2 にも `SkipInfo` を追加する |
| **P012 の修正担当** | `docs/P003-backend-spec.md`, `docs/P007-impl-direction/U005-detectors-a.md` |

### 2.6 矛盾点 #6 — 解析エラーの理由コードが 5 種目を持つ(重大度: 中)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | **4 種に限定する**(`列数不一致` / `日付書式不正` / `数値変換不能` / `見出し不明`) |
| **根拠** | `docs/P002-frontend-spec.md` 6.2.5 が `load_error.reason` の値域を 4 種と定めており、これは**外部契約**(`report.md` の 4.1 節に出力される)である。P003 DS-LD-02 の `文字コード不明` は内部設計の記述であり、外部契約に従うべきである。また `docs/P003-backend-spec.md` DS-LD-05 自身が「理由コードは P002 6.2.5 の 4 種に限定する」と明記しており、**同一文書内の自己矛盾**でもある |
| **どう解消するか** | 文字コードで読めないファイルは、**内容から列構成を判別できない**状態であるため、意味的に `見出し不明` に含まれる(`見出し不明` は「1 行目から列構成を判別できない(ファイル全体を読み飛ばす)」と定義されている)。DS-LD-02 の記述を `見出し不明` に変更し、**理由の詳細は WARNING ログに書く**ことで情報を失わないようにする |
| **修正が必要な箇所** | `docs/P003-backend-spec.md` DS-LD-02 |
| **波及先** | `docs/P007-impl-direction/U002-ingest.md` U002-T2 の【実装内容】3(`None` を返したときの扱い)と【Unit Test内容】。ただし U002-T2 は既に「4 種以外の理由コードを作ってはならない」と正しく指示しているため、**追記のみで足りる** |
| **P012 の修正担当** | `docs/P003-backend-spec.md`, `docs/P007-impl-direction/U002-ingest.md` |

### 2.7 矛盾点 #7 — `errors.py` がツリーと対応表に無い(重大度: 低)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | `errors.py` は**必要である** |
| **根拠** | `docs/P003-backend-spec.md` DS-01-01 が 7 つの例外クラスを定義しており、これらを置くファイルが必要である。`cli.py` に置くと、`loaders` や `bootstrap` が `cli` を import することになり、`docs/P003-backend-spec.md` DS-02(依存は一方向、循環を作らない)に反する |
| **修正が必要な箇所** | `docs/P003-backend-spec.md` 1.1 のディレクトリ構成ツリーに `errors.py` を追加。`docs/P005-impl-plan.md` 3.1 のモジュール × スプリント表に行を追加(U001) |
| **波及先** | `docs/P007-impl-direction/U001-foundation.md` U001-T1 の注記(「ツリーに明記されていないが必要」)を、解消済みの記述に更新する |
| **P012 の修正担当** | `docs/P003-backend-spec.md`, `docs/P005-impl-plan.md`, `docs/P007-impl-direction/U001-foundation.md` |

### 2.8 矛盾点 #8 — 設定キーの件数(重大度: 低)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | **28 キー**(`[parameters]` セクション) |
| **根拠** | `docs/P002-frontend-spec.md` 2.2 の表を数えると、ALG-A1:3, A2:2, A3:2, A4:2, A5:2, B1:3, B2:2, B3:2, B4:2, B5:1, common.min_points:1 = 22 に、P003 15章で追加した 5 キー(`common.sigma_floor_ratio`, `common.sigma_floor_abs`, `ALG-B2.max_buckets`, `events.severe_pct`, `events.merge_gap_minutes`)を加えて **27**。さらに `ALG-B2.bucket_minutes` を含めて **28** |
| **修正が必要な箇所** | `docs/P007-impl-direction/U001-foundation.md` U001-T4 の【実装内容】1 の「約 24」 |
| **どう直すか** | 具体的な数値を書くと表の変更に追随できず陳腐化する。**「`docs/P002-frontend-spec.md` 2.2 の表の全行」**という参照に書き換え、単体テストで「表のキー名の集合と `PARAMS` のキー名の集合が完全一致すること」を確認する方式(既に U001-T4 の【Unit Test内容】に記載済み)に委ねる |
| **波及先** | なし |
| **P012 の修正担当** | `docs/P007-impl-direction/U001-foundation.md` |

### 2.9 矛盾点 #9 — ER 図に `segment` 列が無い(重大度: 低)

| 項目 | 内容 |
| --- | --- |
| **正とすべき記述** | `metrics` に `segment` 列が**ある**(6.2.4 のテーブル定義書) |
| **根拠** | `segment` は `docs/P003-backend-spec.md` DS-07-01 が JVM 再起動の検出結果を保持するために必要とし、DS-08-A1-01 以降の全アルゴリズムが `PARTITION BY series_id, metric, segment` で参照している。実体として存在する列である |
| **修正が必要な箇所** | `docs/P002-frontend-spec.md` 6.1 の Mermaid ER 図の `metrics` エンティティ |
| **波及先** | なし |
| **P012 の修正担当** | `docs/P002-frontend-spec.md` |

---

## 3. 修正対象文書の一覧

| 文書 | 対応する矛盾点 | 修正の性質 |
| --- | --- | --- |
| `docs/P001-requirement.md` | #4 | 7章の Mermaid 図の是正(要求内容の変更ではない) |
| `docs/P002-frontend-spec.md` | #3, #9 | 8.2 の規模の明確化、6.1 の ER 図への列追加 |
| `docs/P003-backend-spec.md` | #5, #6, #7 | 7.1 の戻り値定義、DS-LD-02 の理由コード、1.1 のツリー |
| `docs/P004-traceability-matrix.md` | #4 | 6章の追跡表の状態更新 |
| `docs/P005-impl-plan.md` | #7 | 3.1 の表への行追加 |
| `docs/P006-test-plan.md` | #1, #2 | 4.1 のコマンドの是正 |
| `docs/P007-impl-direction.md` | #2, #4 | 3.7 のコマンド、5章の未解決事項の更新 |
| `docs/P007-impl-direction/U001-foundation.md` | #2, #7, #8 | `tests/__init__.py` の役割、`errors.py` の注記、キー件数 |
| `docs/P007-impl-direction/U002-ingest.md` | #6 | 文字コード不明時の扱いの追記 |
| `docs/P007-impl-direction/U003-testdata.md` | — | 修正不要(#3 の修正後の P002 と既に一致) |
| `docs/P007-impl-direction/U005-detectors-a.md` | #5 | `SkipInfo` の定義位置と ★FIXME★ の解消 |
| `docs/P008-test-direction/T001`〜`T008` | #2 | 【実行コマンド】8 箇所 |
| `docs/P009-acceptance-direction/A001`〜`A011` | #2 | 【実行コマンド】11 箇所 |

**修正対象は 12 種の文書、延べ 30 箇所である。**

## 4. 修正によって新たな矛盾が生じないことの確認

| 修正 | 新たな矛盾の可能性 | 確認結果 |
| --- | --- | --- |
| #2 で `app/tests/__init__.py` に処理を持たせる | `docs/P009-acceptance-direction/A010-portability.md` のケース C が `app/tests/` を丸ごと削除して動作を確認する。**`tests/__init__.py` にアプリの動作に必要な処理を置くと、削除したときに壊れる** | **問題なし**。置くのは**テストから `app/src` を import するための `sys.path` 追加**のみであり、アプリ本体(`s_anomaly.py` → `src/`)は `tests/` に依存しない。A010 ケース C はこの独立性を確認するテストであり、むしろ整合する |
| #4 で P001 の図を変更 | P001 は要求定義書であり、後続文書がこれを参照している | **問題なし**。図の是正のみで FR/NFR の内容は変わらない。P004 の追跡結果(全件 OK)にも影響しない |
| #5 で `Detector.run` の戻り値を変更 | `docs/P006-test-plan.md` 5.1 の U005/U006 の単体テスト観点、`docs/P008-test-direction/T004` の期待値 | **問題なし**。T004 は既に「`Detection` と `SkipInfo` を集める」と記述しており、修正後の定義と一致する |
| #6 で `文字コード不明` を廃止 | `docs/P007-impl-direction/U003-testdata.md` U003-T3 の破損データに文字コード破損のケースが含まれるか | **問題なし**。U003-T3 の 9 ファイルに文字コード破損のケースは含まれていない(BOM 付きと CRLF は**正常に読める**ケースとして定義されている) |
