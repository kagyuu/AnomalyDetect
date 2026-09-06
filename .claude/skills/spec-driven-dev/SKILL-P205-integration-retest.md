---
name: integration-retest-dev
description: 仕様駆動でアプリケーションを開発するときに、結合テストを再実施する。
---

# 結合テスト再実施

## 目的

* P203の修正後、`docs/P008-test-direction.md` と `docs/P009-acceptance-direction.md` のうち、これまで失敗していたテスト、および P204 で影響が疑われたテストを再実行する。
* 再実行結果をもとに、P201(実装横断レビュー)の判定に戻る。

## インプット文書

* `docs/P008-test-direction.md` / `docs/P009-acceptance-direction.md`
* `docs/P202-fix-plan/fixed/F000-{fix-name}.md`
* `docs/P204-impact-analysis.md`

## アウトプット文書

* `docs/test-records/YYYYMMDD-HHMM-test-record.md` (追記)
* `{ソースツリー}/INDEX.md` (更新。P204までの修正により記載内容が古くなっている場合)

### 動作内容

* P103・P201と同じテスト実行ルール(修正禁止・期待値改ざん禁止・記録必須)に従う。記録形式は `TEMPLATE-test-record.md` に定める共通形式に従う。「期待値改ざん禁止」は、P202で「テスト指示側の誤り」と判定され証拠とともに是正された期待値の反映を妨げない(改ざん=失敗回避目的の弱体化と、訂正=上位仕様への追随は別物。`SKILL-P202-fix-plan.md` 参照)。
* 再実行の対象は、直近のテスト記録でFAIL/BLOCKEDだったテスト、および `docs/P204-impact-analysis.md` で影響が疑われたテストとする。全件を無条件に再実行する必要はない。
* `{ソースツリー}/INDEX.md`(P104で作成済み)に、今回の修正で古くなった記載(例: 「特定テストが手順N でFAIL」のような、P104時点の状態への言及)がある場合は、`SKILL-P104-source-index-update.md` と同じ形式で本フェーズが更新する。
* 再実行結果を記録したら、P201(実装横断レビュー)を再実行し、全件PASSかどうかを再判定する。
* 全件PASSでなければ、再度P202(修正計画)に差し戻す。Reviewer Loopの停止条件(P201を3回実行してもなお全件PASSにならない)に該当した場合は、処理を停止して人間に報告する。

## 動作

* 共通指示に加えて、上記動作内容に従う。
