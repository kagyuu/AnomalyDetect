---
name: impl-context-dev
description: 仕様駆動でアプリケーションを開発するときに、Executorのための実装コンテキストを構築する。
---

# 実装コンテキスト構築

## 目的

* `{ソースツリー}/INDEX.md`(P020)・`docs/ADR.md`(P021)・`docs/ArchitectureHandbook.md`(P022)から、Executor(低性能モデル)が実装に着手する前に読んでおくべき情報を1つに要約する。
* Executorに、詳細仕様(`docs/P002-frontend-spec.md` など)をすべて読ませるのではなく、まずこのコンテキストと、実行対象の `docs/P007-impl-direction/U000-{sprint-name}.md` だけを読ませれば着手できる状態にする。

## インプット文書

* `{ソースツリー}/INDEX.md`
* `docs/ArchitectureHandbook.md`
* `docs/ADR.md`
* `docs/P007-impl-direction.md` (目次 OKF形式。次に着手すべき未完了スプリントを確認する)

## アウトプット文書

* `docs/P101-impl-context.md`

### アウトプットの記載内容

* 現在のソースツリーの状態(`INDEX.md` の要約)
* 遵守すべき技術的決定(`docs/ADR.md` の一覧表をそのまま転記、または要約)
* これから着手するスプリント(`docs/P007-impl-direction.md` の目次で次に `[ ]`/`[~]` になっている項目)
* Executorが迷ったときに参照すべき詳細仕様の場所(`docs/P002-frontend-spec.md` などの該当箇所へのリンク)

### アウトプットを参照する文書

* P102(プログラム実装)がExecutorへの申し送りとして使う。

## 動作

* 共通指示以外は特になし
* 本フェーズはExecutorのStepに入るたびに(未完了スプリントが残っている限り)実行し、直近の `INDEX.md`/`ArchitectureHandbook.md`/`ADR.md` の内容を反映するよう更新する。
