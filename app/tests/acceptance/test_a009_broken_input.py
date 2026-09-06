"""A009 — 破損データでの完走 (FR-014 / FR-091)。

**運用ログは実際に壊れる。** 1 行の破損で数百ファイルの解析全体が失敗する実装は
運用に耐えない。読み飛ばした行が 4.1 節に集計されることを、
`gen_testdata.BROKEN_EXPECTATIONS` と突き合わせて確認する。
"""

import os
import re
import shutil
import sys
import tempfile
import unittest

from tests.acceptance import _harness as H

sys.path.insert(0, os.path.join(H.APP_DIR, "tools"))
import gen_testdata as G  # noqa: E402

_ROW = re.compile(r"^\|\s*(?P<file>[^|]+?)\s*\|\s*(?P<reason>[^|]+?)\s*\|"
                  r"\s*(?P<count>\d+)\s*\|$")

REASON_CODES = {"列数不一致", "日付書式不正", "数値変換不能", "見出し不明"}

#: 破損データに含まれる、エラーとして記録されてはならないファイル。
CLEAN_IN_BROKEN = (
    "DBConnection_20260602.csv",   # 空ファイル
    "DBConnection_20260603.csv",   # 見出しのみ
    "DBConnection_20260604.csv",   # BOM 付き UTF-8
    "DBConnection_20260605.csv",   # CRLF
)

#: 混在ケースへコピーする正常ファイル (3 ファイル程度)。
#: **破損データとファイル名が衝突するものを選んではならない。**
#: 正常系と破損系はどちらも DBConnection_20260601〜05.csv を持つため、
#: 単純に先頭から選ぶと破損側のコピーが正常側を上書きして混在にならない。
MIX_NORMAL_COUNT = 3


def parse_skipped_rows(report):
    """4.1 節を {ファイル名: {理由: 件数}} に解析する。"""
    body = H.section(report, "### 5.1 読み飛ばした行")
    found = {}
    for line in body.splitlines():
        if line.startswith("| --- ") or line.startswith("| ファイル "):
            continue
        match = _ROW.match(line.strip())
        if not match:
            continue
        name = os.path.basename(match.group("file"))
        found.setdefault(name, {})[match.group("reason")] = int(
            match.group("count"))
    return found


class TestA009BrokenInput(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA009BrokenInput, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()

        # ケース1: 破損データのみ
        cls.work1 = os.path.join(cls._tmp.name, "case1")
        os.makedirs(cls.work1)
        cls.proc1 = H.run_app(cls.broken_logs, cls.work1)
        # ※CR-001: 破損データでは検知 0 件になりホスト別ファイルが作られない。
        # 読み飛ばした行と総レコード数の記録先はサマリである (UI-04-F10 / FR-014)。
        cls.report1 = H.read_all_reports(cls.work1)

        # ケース2: 正常 3 ファイル + 破損全部の混在
        cls.mixed = os.path.join(cls._tmp.name, "mixed-logs")
        os.makedirs(cls.mixed)
        broken_names = set(os.listdir(cls.broken_logs))
        cls.mixed_normal = [
            name for name in sorted(os.listdir(cls.normal_logs))
            if name not in broken_names][:MIX_NORMAL_COUNT]
        for name in cls.mixed_normal:
            shutil.copy(os.path.join(cls.normal_logs, name),
                        os.path.join(cls.mixed, name))
        for name in sorted(os.listdir(cls.broken_logs)):
            shutil.copy(os.path.join(cls.broken_logs, name),
                        os.path.join(cls.mixed, name))
        cls.work2 = os.path.join(cls._tmp.name, "case2")
        os.makedirs(cls.work2)
        cls.proc2 = H.run_app(cls.mixed, cls.work2)
        cls.report2 = H.read_all_reports(cls.work2)

        cls.rows1 = parse_skipped_rows(cls.report1)
        cls.rows2 = parse_skipped_rows(cls.report2)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def _record_count(report):
        for line in report.splitlines():
            if line.startswith("| 総レコード数 |"):
                return int(line.split("|")[2].strip().replace(",", ""))
        return None

    # 1
    def test_01_case1_exit_zero(self):
        self.assertEqual(self.proc1.returncode, 0, H.err(self.proc1))

    # 2
    def test_02_case1_report_structure(self):
        # ※CR-001: 破損データでは検知 0 件になりうる。サマリ側で確認する。
        for heading in ("# 異常検知サマリ", "## 1. 実行サマリ",
                        "## 2. ホスト別の集計", "## 5. 実行時の注意事項",
                        "### 5.1 読み飛ばした行"):
            self.assertIn(heading, self.report1, heading)

    # 3
    def test_03_case1_warnings_have_file_reason_count(self):
        message = H.err(self.proc1)
        self.assertIn("WARNING", message)
        for name, want in G.BROKEN_EXPECTATIONS.items():
            if not want["errors"]:
                continue
            self.assertIn(name, message, name)
        for reason in REASON_CODES:
            if any(reason in w["errors"] for w in G.BROKEN_EXPECTATIONS.values()):
                self.assertIn(reason, message, reason)
        # 書式は「理由 N件」(数と「件」の間に空白は入らない)
        self.assertRegex(message, r"\d+\s*件")

    # 4
    def test_04_case1_no_traceback(self):
        self.assertNotIn("Traceback", H.err(self.proc1))

    # 5
    def test_05_case1_matches_broken_expectations(self):
        want = {name: dict(spec["errors"])
                for name, spec in G.BROKEN_EXPECTATIONS.items()
                if spec["errors"]}
        self.assertEqual(self.rows1, want)

    # 6
    def test_06_reason_codes_within_four(self):
        seen = set()
        for reasons in self.rows1.values():
            seen.update(reasons)
        self.assertTrue(seen.issubset(REASON_CODES), seen)

    # 7
    def test_07_case2_exit_zero(self):
        self.assertEqual(self.proc2.returncode, 0, H.err(self.proc2))

    def test_07b_mixed_dir_really_mixes(self):
        """混在ディレクトリが実際に両方を含むこと (本テストの前提)。"""
        self.assertEqual(len(self.mixed_normal), MIX_NORMAL_COUNT)
        names = set(os.listdir(self.mixed))
        for name in self.mixed_normal:
            self.assertIn(name, names, name)
        for name in os.listdir(self.broken_logs):
            self.assertIn(name, names, name)

    # 8
    def test_08_case2_keeps_normal_records(self):
        broken_total = self._record_count(self.report1)
        mixed_total = self._record_count(self.report2)
        self.assertIsNotNone(broken_total)
        self.assertIsNotNone(mixed_total)
        self.assertGreater(mixed_total, broken_total,
                           "破損ファイルの存在で正常データが失われている疑い")

    # 9
    def test_09_case2_lists_only_broken_files(self):
        for name in self.mixed_normal:
            self.assertNotIn(name, self.rows2, name)
        want = {name: dict(spec["errors"])
                for name, spec in G.BROKEN_EXPECTATIONS.items()
                if spec["errors"]}
        self.assertEqual(self.rows2, want)

    # 10
    def test_10_bom_and_crlf_are_not_errors(self):
        for name in ("DBConnection_20260604.csv", "DBConnection_20260605.csv"):
            self.assertNotIn(name, self.rows1, name)
            self.assertNotIn(name, self.rows2, name)
            self.assertEqual(G.BROKEN_EXPECTATIONS[name]["ok_rows"], 5, name)

    # 11
    def test_11_empty_and_header_only_are_not_errors(self):
        for name in CLEAN_IN_BROKEN:
            self.assertNotIn(name, self.rows1, name)
            self.assertNotIn(name, self.rows2, name)


if __name__ == "__main__":
    unittest.main()
