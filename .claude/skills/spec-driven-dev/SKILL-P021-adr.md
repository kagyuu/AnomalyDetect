---
name: adr-dev
description: 仕様駆動でアプリケーションを開発するときに、ADR.mdを整理する。
---

# ADR整理

## 目的

* 設計上の重要な決定(技術選定、代替案の却下理由など)を `docs/ADR.md` に整理し、最終的にその仕様になった理由をLLMが読み込める程度に簡潔にまとめる。
* 廃止された決定は `docs/ADR_master.md` に退避し、`docs/ADR.md` には現在有効な決定のみを残す。
* 初回実行時は、これより前のフェーズ(P002・P003・P005・P007など)が暫定番号でADRを参照している箇所(★FIXME★付き)をすべて洗い出し、実際に確定したADR番号と一致するよう参照元の記述を更新する(`SKILL.md` 各フェーズ共通指示「ADR番号の暫定参照について」参照)。

## インプット文書

* `docs/P001-requirement.md` 〜 `docs/P009-acceptance-direction.md`
* `docs/P010-design-review.md` / `docs/P011-impact-analysis.md` (Plan Loop Stepで設計変更があった場合)
* 既存の `docs/ADR.md` / `docs/ADR_master.md` (あれば)

## アウトプット文書

* `docs/ADR.md`
* `docs/ADR_master.md` (廃止されたADRがある場合)
* `docs/P002-frontend-spec.md`・`docs/P003-backend-spec.md`・`docs/P005-impl-plan.md`・`docs/P007-impl-direction.md` 等(暫定ADR番号を確定番号に更新する箇所のみ。初回実行時、該当箇所がある場合)

### アウトプットの記載内容

* `TEMPLATE-ADR.md` の構成に則る。
* 初回作成時は、`docs/P002-frontend-spec.md` `docs/P003-backend-spec.md` `docs/P005-impl-plan.md` などから、後戻りできない/コストの高い技術的決定(フレームワーク選定、認証方式、データベース選定、デプロイ方式など)を抽出し、ADRとして1件ずつ整理する。
* 2回目以降(Plan Loop Stepでの設計修正後、またはRefactorステップからの再入時)は、次を行う。
  * 新たに決定した事項があれば、新しいADR-NNNとして追記する。
  * 既存のADRを覆す決定があった場合、そのADRの状態を「廃止」にし、本文を `docs/ADR_master.md` に移動する。移動時に廃止日・廃止理由(どのADRに置き換わったか)を追記する。
  * 内容が変わらないADRは変更しない。
* ADR一覧表(`docs/ADR.md` 冒頭)は、常に現在有効なADRのみを反映するよう更新する。
* ADRの粒度(1決定=1ADRにするか、関連する決定をまとめるか)は、次の基準で判断する。
  * 実務上切り離せない決定(例: 「フレームワークXを採用した結果、それに付随してORM Yも採用する」のように、一方を選べば他方がほぼ自動的に決まる関係)は、1つのADRにまとめてよい。`TEMPLATE-ADR.md` のADR-001(Spring Boot採用にMyBatis採用を含める例)は、この「切り離せないためまとめる」ケースの例示である。
  * 独立に覆される可能性がある決定(例: フレームワーク選定とは別に、後から単独で見直されうる認証方式やデータベース選定)は、別々のADRに分ける。
  * 判断に迷う場合は、まとめた場合の粒度に固定せず、後から一方だけを覆す変更要求が来たときに1つのADRの一部だけを「廃止」にできるかを基準に考える。それができないほど密結合ならまとめてよい。

### アウトプットを参照する文書

* `docs/ArchitectureHandbook.md` (P022) が各ADRを技術スタック表から参照する。
* P002・P003・P012 (設計・設計修正) が、既存の決定を確認する際に参照する。
* P901〜P905 (Refactor) が、変更要求にともなうADR更新の要否を確認する際に参照する。

## 動作

* 共通指示以外は特になし
