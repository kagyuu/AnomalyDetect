# INDEX — S_ANOMALY(ログ異常検知)プロジェクト全体の目次

DB 接続数 / JVM GC / LSF キューの運用ログを DuckDB に取り込み、11 個の検知アルゴリズムで
異常個所を見つけて **Markdown のレポート群**(`report_{HOST}_{yyyymm}.md` と `report_summary_{yyyymm}.md`)に
まとめる、単一の Python CLI である。

**まずここを読む**: 設計の全体像は `docs/ArchitectureHandbook.md`、
個々の設計判断とその理由は `docs/ADR.md` にある。**この 2 つで大半の疑問は解ける。**

---

## 配布資産・使い方

| パス | 概要 |
| --- | --- |
| [`README.md`](./README.md) | **使う人が最初に読む文書。** 実行方法・終了コード・**レポート群の構成**・**sar(sysstat)の前処理手順**・**11 個の異常検知アルゴリズムの解説(原理・弱点・補完関係)**・全パラメータの一覧・脅威度の判定規則・**閉域環境向け `vendor/` の準備手順**・既知の制約・性能の目安(FR-100 / FR-101)。**利用者向け説明はこのファイルに一本化してある**(`app/README.md` は削除した) |
| [`docs/P302-deliver.md`](./docs/P302-deliver.md) | **成果物まとめ。** 59 件の要求とテストの対応表、配布資産一覧、実行手順、**未整備事項と人間による確認事項(未解消の ★FIXME★ 103 件を含む)**、リリース判定 |
| [`BUILD_HISTORY.md`](./BUILD_HISTORY.md) | ビルド履歴(B-001〜B-004)とバージョンの管理方法 |
| `app/VERSION` | バージョン番号(`0.1.0`)。起動時の進捗ログに出る |

---

## ソースツリー

| パス | 概要 |
| --- | --- |
| [`app/INDEX.md`](./app/INDEX.md) | **アプリケーション本体の目次。** モジュールごとの責務はこちらに詳しい |

**改修時に「どこを触るか」の当たりをつけるための一覧**(詳細は `app/INDEX.md`)。

| モジュール | 責務 | 主に関わる CR / ADR |
| --- | --- | --- |
| `cli.py` | 引数解析・**S1〜S9 の全体制御**・終了コードの決定 | ADR-010(実行順序)/ ADR-018(S6 の並列) |
| `bootstrap.py` | `vendor/` の解決と DuckDB の初期化 | ADR-001 |
| `config.py` | `settings.properties` の読み込みと検証。**`PARAMS` が設定表そのもの** | CR-008 / CR-009 |
| `discovery.py` | ディレクトリの再帰走査とファイル名判定 | — |
| `loaders/` | 3 形式の解析と一括挿入。**投入時のタイムゾーン変換** | ADR-011 / ADR-019 |
| `metrics.py` | 3 テーブル → 統一テーブル `metrics` | ADR-004 |
| `detectors/` | **11 個の検知アルゴリズム**と系列単位のキャッシュ | ADR-012 / ADR-013 / **ADR-016 / ADR-017** |
| `events.py` | 検知点の統合・脅威度判定・**イベント ID の採番** | CR-003 |
| `co_anomaly.py` | **同時に発生したアノマリーの突き合わせ**(掃引法) | CR-002 |
| `causes.py` | 原因候補の 9 ルール | ADR-010 |
| `charts.py` | **SVG チャートの生成**(自前) | **ADR-020** |
| `reporter.py` | **レポート群の生成**(ホスト × 年月 + サマリ) | ADR-015 |

---

## 要求・設計(Require Development / Plan Loop)

| パス | 概要 |
| --- | --- |
| [`docs/P000-concept-analysis.md`](./docs/P000-concept-analysis.md) | **人間が書いた要求の原文。** なぜこの仕様になっているかを辿る起点。ここに書かれた指定(例: 「DuckDB インメモリ + メモリからあふれたら temp ファイルを使う」)は Agent の想定ではなく人間の指示である |
| [`docs/P001-requirement.md`](./docs/P001-requirement.md) | **システム要件定義書。** FR-001〜 / NFR-001〜011。15章の非機能要件には「本章は全面的に Agent の想定である」という包括的な ★FIXME★ がある |
| [`docs/P002-frontend-spec.md`](./docs/P002-frontend-spec.md) | ユーザインタフェース設計書。CLI の引数・終了コード 9 種・`settings.properties`・`report.md` の書式(UI-xx) |
| [`docs/P003-backend-spec.md`](./docs/P003-backend-spec.md) | システム詳細設計書。モジュール M01〜M12、11 個の検知アルゴリズム、データストア設計(DS-xx) |
| [`docs/P004-traceability-matrix.md`](./docs/P004-traceability-matrix.md) | 要求トレーサビリティマトリクス。要求 → 設計 → 実装 → テストの対応 |
| [`docs/P005-impl-plan.md`](./docs/P005-impl-plan.md) | 実装計画書。スプリント分割 |
| [`docs/P006-test-plan.md`](./docs/P006-test-plan.md) | テスト計画書。単体 / 結合 / 受け入れの方針とテストデータの前提(TP-xx) |
| [`docs/P007-impl-direction.md`](./docs/P007-impl-direction.md) | プログラム実装定義 兼 実装指示書の**目次**(OKF形式)。個別指示は [`docs/P007-impl-direction/`](./docs/P007-impl-direction/) |
| [`docs/P008-test-direction.md`](./docs/P008-test-direction.md) | 結合テスト定義 兼 実行指示書の**目次**(OKF形式)。個別指示は [`docs/P008-test-direction/`](./docs/P008-test-direction/) |
| [`docs/P009-acceptance-direction.md`](./docs/P009-acceptance-direction.md) | 受け入れ結合テスト定義 兼 実行指示書の**目次**(OKF形式)。個別指示 A001〜A014 は [`docs/P009-acceptance-direction/`](./docs/P009-acceptance-direction/) |
| [`docs/P010-design-review.md`](./docs/P010-design-review.md) / [`docs/P010-design-review-2.md`](./docs/P010-design-review-2.md) | 設計横断レビュー(1 回目 / 2 回目) |
| [`docs/P011-impact-analysis.md`](./docs/P011-impact-analysis.md) | 設計修正の影響分析 |

---

## 全体像(Overview)

| パス | 概要 |
| --- | --- |
| [`docs/ArchitectureHandbook.md`](./docs/ArchitectureHandbook.md) | **アーキテクチャハンドブック。** 全資産を読み直さずに全体像をつかむための文書。**9章「既知の制約・技術的負債」は着手前に必ず読むこと**(意識的に受け入れた制約、実測で判明した性能・メモリ・再現性の状況、人間の確認を要する未解決事項) |
| [`docs/ADR.md`](./docs/ADR.md) | **アーキテクチャ決定記録(現在有効なもの)。ADR-001〜020。** 各決定の背景・理由・検討したが採らなかった案・残存リスク。**同じ誤りを繰り返さないための記録**であり、実装を変える前にここを見る |
| [`docs/ADR_master.md`](./docs/ADR_master.md) | **廃止された決定の保管先。** 現在 1 件(ADR-014。CR-002 により対象が消滅)。**削除せず、なぜ覆ったかを残す** |
| [`docs/P101-impl-context.md`](./docs/P101-impl-context.md) | 実装コンテキスト(Executor 向けの入力) |

---

## テストと修正の記録(Executor / Reviewer Loop)

| パス | 概要 |
| --- | --- |
| [`docs/test-records/`](./docs/test-records/) | **テスト実行の一次記録。** 実行日時ごとに 1 ファイル。過去の記録は上書きせず追加する。最新は `20260905-1530-test-record.md`(CR-005〜007。**全件 PASS**) |
| [`docs/P201-review-report.md`](./docs/P201-review-report.md) | 実装横断レビュー報告(1 回目) |
| [`docs/P202-fix-plan.md`](./docs/P202-fix-plan.md) | **修正計画の目次**(OKF形式)。Reviewer Loop 1 回目 = F001 / F002、2 回目 = F003。**全 3 件が `[x]`** |
| [`docs/P202-fix-plan/fixed/`](./docs/P202-fix-plan/fixed/) | 完了した個別修正指示と、その実施記録(F001 / F002 / F003) |
| [`docs/P202-fix-plan/P202-fix-resolved.md`](./docs/P202-fix-plan/P202-fix-resolved.md) | 解決済み障害の一覧と詳細 |
| [`docs/P202-fix-plan/P202-fix-unresolved.md`](./docs/P202-fix-plan/P202-fix-unresolved.md) | **未解決の一覧。** **現在 0 件。** NFR-009(再現性)は CR-002 + CR-007 で解消した(2026-09-05)。対処案と人間への確認事項を含む |
| [`docs/P204-impact-analysis.md`](./docs/P204-impact-analysis.md) | 修正の影響分析(1 回目 / 2 回目)。最新の判定が冒頭、過去の判定と実行履歴が後段 |
| [`docs/P010-design-review-3.md`](./docs/P010-design-review-3.md) | 設計横断レビュー(3 回目。CR-001〜004 対応) |
| [`docs/P010-design-review-4.md`](./docs/P010-design-review-4.md) | 設計横断レビュー(4 回目。**CR-005〜007 対応**) |
| [`docs/P010-design-review-5.md`](./docs/P010-design-review-5.md) / [`docs/P011-impact-analysis-5.md`](./docs/P011-impact-analysis-5.md) | 設計横断レビュー(5 回目。**CR-010 対応**)と、その影響分析 |
| [`docs/P010-design-review-6.md`](./docs/P010-design-review-6.md) | 設計横断レビュー(6 回目。**CR-011 対応**。不整合 0 件) |

---

## 変更要求(CR)

| パス | 概要 |
| --- | --- |
| [`docs/CR.md`](./docs/CR.md) | **CR 状態の台帳。** 状態(未対応/対応中/反映確認中/完了/却下)と優先度の唯一の正 |
| [`docs/P901-cr-direction/`](./docs/P901-cr-direction/) | 変更要求の本文(何をなぜ変えてほしいか)。CR-001〜009 |
| [`docs/P903-cr-records/`](./docs/P903-cr-records/) | 対処の記録(何をどう変えたか)。スコープ決定・変更内容・想定との差異・テスト結果 |
| [`docs/report-samples/`](./docs/report-samples/) | **実際に生成されたレポート群の置き場。** **中身はリポジトリに含めない**(`.gitignore`。100MB 超のため)。`README.md` に再生成の手順がある |
| [`docs/P310-sanitization-audit.md`](./docs/P310-sanitization-audit.md) | **公開前サニタイズ監査**(2026-09-06)。何を置換し、何を残し、なぜそう判断したかの記録 |
| [`docs/P001-requirement-old/`](./docs/P001-requirement-old/) | 過去の要件定義原本の退避先。**現在有効な要件定義は `docs/P001-requirement.md`** |

---

## 制御ファイル(`docs/` 直下)

成果物ではなく、ワークフローの状態を持つ。

| パス | 概要 |
| --- | --- |
| `docs/.inprogress` | 現在実行中のフェーズ番号。**フェーズの進捗判定にはこれのみを使う。P302 が完了したため現在は存在しない**(通常フローの完了を意味する) |
| `docs/.mode` | 実行モード。現在 `一気通貫`(Step 境界で人間のゲートを設けない) |
| `docs/CR.md` | CR 状態台帳。**変更要求が起票されると作られる。現在は存在しない** |

---

## 現在の状態(2026-09-08 時点。**バージョン 0.7.0**)

* **CR-001〜CR-011 を適用した。**

  | 回 | CR | 内容 |
  | --- | --- | --- |
  | 第 1 回 | CR-001〜004 | レポートの分割・同時アノマリー・イベント ID・集計サマリ |
  | 第 2 回 | **CR-005** | **観点3「上限への張り付き」(ALG-C1)を新設** |
  | 第 2 回 | **CR-006 / CR-007** | **検知(S6)の高速化と並列化** |
  | 第 3 回 | **CR-008** | **タイムゾーン**(入力種別ごとの設定・投入時の変換・レポートへの併記) |
  | 第 4 回 | **CR-009** | **SVG チャート**(アノマリーの状況の折れ線・同時アノマリーの重ね合わせ) |
  | 第 5 回 | **CR-010** | **sar(sysstat)を分析対象に追加**(入力種別が 3 → 4。OS 資源統計) |
  | 第 6 回 | **CR-011** | **sar の NFS / NFSD を追加**(活動種別が 8 → 10。**LSF グリッドが NFS を共有するため**) |

* **テストは全件合格している。** 単体 543 件・結合 136 件・受け入れ 180 件(A006 の 19 件を含む)が、いずれも連続 2 回とも成功。

* **アルゴリズムが 11 個になった。**

  | 観点 | 内容 | 個数 |
  | --- | --- | --- |
  | 観点1 | 瞬間的な外れ値 | 5 |
  | 観点2 | 持続的な数値の増加 | 5 |
  | **観点3** | **上限への張り付き**(※CR-005) | **1** |

  観点1・観点2 が「変化」を見るのに対し、**観点3 は「変化していないこと」を見る。**
  上限に達すると増加が止まるため、観点2 では原理的に捉えられない。

* **出力はレポート群である。**

  | 出力 | 内容 |
  | --- | --- |
  | `report_{HOST}_{yyyymm}.md` | ホスト × 年月ごとのイベント本文(先頭に集計章) |
  | `report_summary_{yyyymm}.md` | 全ホスト横断の集計サマリ |

  **実物は [`docs/report-samples/`](./docs/report-samples/) にある**(30 日分 18 ファイル / 90 日分 54 ファイル。**※CR-010 適用後のもの**)。

* **4K トークンのローカル LLM で扱える**(90 日規模の実測)。

  | 出力 | サイズ |
  | --- | --- |
  | `report_summary_{yyyymm}.md` | 最大 3,873 バイト |
  | ホスト別ファイルの「1. このホストの集計」 | 最大 674 バイト |

* **性能**(既定の `max_memory = 4GB`)

  | 規模 | 0.2.0 | 0.4.0 | 0.5.0 | 0.6.0 | **0.7.0** | うち S6 |
  | --- | --- | --- | --- | --- | --- | --- |
  | 通常 30 日 | 69.9s | 53.1s | 95.3s | 132.1s | **141.6s**(1,926,720 行) | 36.5s |
  | 大規模 90 日 | 230.9s | 162.8s | 302.4s | 359.3s | **367.3s**(5,780,160 行) | 106.1s |

  **0.5.0 の増加は CR-009 のチャート生成、0.6.0 は CR-010 の sar 追加、
  0.7.0 は CR-011 の NFS 追加による。**
  **NFR-002(600 秒)には収まっている**(90 日で 367 秒 / **余裕 39%**)。
  **NFS はホスト単位の系列であり、行が 13% 増えても所要は 2% しか増えていない。**

  **CR-008(タイムゾーン)による性能への影響は無い。** 既定はすべて UTC であり、
  変換の SQL が一度も発行されないためである(ADR-019)。

  * 0.2.0 の改善は **CR-002**(`metrics` への範囲結合をやめた)。
  * **0.3.0 の改善は CR-006**(同じ系列を検知器の数だけ読み直していたのをやめた)。
    S6 は 48.0s → 23.9s(**-51%**)、1 系列あたりの単価は 1.2 秒 → **0.16 秒**。
  * **支配ステップは S6 から S4(取り込み)へ移った。**

* **NFR-009(再現性)の積み残しを解消した。** 30 日規模で、スレッド数 1 と 8 の出力が
  「実行日時」の行を除いて全 18 ファイル完全一致することを確認した。

* **人間の判断を要する事項が残っている。** 全件は `docs/P302-deliver.md` 10 章にある。主なものは次の 4 つ。
  1. **4K の判定をバイト数(4,000 バイト)で代用している。** トークナイザが配布資産に含まれないため厳密な計測ができない。**実際のローカル LLM での確認が必要**
  2. **原因候補ルールの判定条件が変わった。** 相関の統計値から「同時刻に他のアノマリーが検知されているか」へ読み替えたため、引き当たる条件が変わる。実データでの妥当性は未確認
  3. **ALG-C1 の「推定上限」が実運用で妥当かどうか。** 上限値が入力に無いため実測最大値から推定している(ADR-016)。実データでの突き合わせが必要
  4. **非機能要件の数値(規模・時間・文字コード)が Agent の想定のままである**

* **次バージョンの予定**: **sar(sysstat)の分析対象への追加**(依頼者が明示)。本版の範囲外である。
