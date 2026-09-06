---
name: architecture-handbook-dev
description: 仕様駆動でアプリケーションを開発するときに、ArchitectureHandbook.mdを作成・更新する。
---

# ArchitectureHandbook作成

## 目的

* アプリケーションの技術的側面(構成・技術スタック・ディレクトリ構成方針・横断的関心事など)を1つのハンドブックにまとめ、後続のExecutor・Reviewer Loopが `docs/P001-requirement.md` 〜 `docs/P009-acceptance-direction.md` を毎回すべて読み直さなくても実装・レビューを進められるようにする。

## インプット文書

* `docs/P001-requirement.md` 〜 `docs/P009-acceptance-direction.md`
* `docs/ADR.md` (P021の成果物。本フェーズはP021の後に実行するため、この時点で必ず作成済みである)
* `{ソースツリー}/INDEX.md` (P020)

## アウトプット文書

* `docs/ArchitectureHandbook.md`

### アウトプットの記載内容

* `TEMPLATE-ArchitectureHandbook.md` の構成に則る。
* 初回作成時は、`docs/P001-requirement.md` 〜 `docs/P009-acceptance-direction.md` の内容から該当箇所を要約して埋める。
* 2回目以降(Refactorステップからの再入時など)は、既存の `docs/ArchitectureHandbook.md` を読み、変更のあった箇所だけを差分更新する。無関係な既存記述は残す。
* 詳細な仕様そのものは書き写さず、要約と原本(`docs/P00N-*.md`)への参照リンクにとどめる。

### アウトプットを参照する文書

* P101(実装コンテキスト構築)、Executor・Reviewer Loop全般が、詳細仕様の代わりにまず参照する文書として使う。

## 動作

* 共通指示以外は特になし
