---
name: fix-execute-dev
description: 仕様駆動でアプリケーションを開発するときに、修正計画にもとづき修正を実施する。
---

# 修正実施

## 目的

* `docs/P202-fix-plan.md` の目次で未完了(`[ ]`/`[~]`)になっている修正指示を、先頭から順に実施する。

## インプット文書

* `docs/P202-fix-plan/F000-{fix-name}.md` (着手対象)
* `docs/ArchitectureHandbook.md` / `docs/ADR.md`

## アウトプット文書

* 修正されたソースコード
* `docs/P202-fix-plan/fixed/F000-{fix-name}.md` または `docs/P202-fix-plan/P202-fix-unresolved.md` への追記

### 動作内容

* `TEMPLATE-P202-fix-direction.md` の指示に従い、1件の修正タスクを実施する。
* 修正の過程で仕様の解釈を明確化した場合は、関連する設計書に明確化内容を追記する。仕様判断そのものを伴う場合は、修正を保留しP204(影響分析)に回す。
* 修正が完了したタスクは `docs/P202-fix-plan.md` の該当行を `[x]` にし、リンク先を `docs/P202-fix-plan/fixed/F000-{fix-name}.md` に更新する。
* 修正しきれなかったタスクは `docs/P202-fix-plan/P202-fix-unresolved.md` に記録し、試行錯誤で変更した未完了のソースコードは元に戻す。
* 未完了の修正タスクが他にもある場合は、続けて次のタスクを実施する。すべてのタスクが完了(`[x]`または未解決記録済み)になったら、P204(影響分析)に進む。

## 動作

* 共通指示に加えて、上記動作内容に従う。
