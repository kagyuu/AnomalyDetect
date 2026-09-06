"""A008 — ネットワーク非通信の確認 (NFR-008)。

配布先は閉域環境である。通信を試みると失敗するだけでなく、
**長時間のタイムアウト待ちで実行が止まる**。

確認方法は P006 2.2 / A008 の ★ACCEPTED★ に従い
**(a) 静的なソース確認 + (b) DuckDB の設定確認 + (c) 実行時の確認**で行う。
実際に遮断した環境での実行は行わない(閉域環境での初回実行時に人間が確認する
項目として P302 へ引き継ぐ)。

静的確認は**文字列の grep ではなく AST** で行う。
"""

import ast
import io
import os
import tempfile
import time
import unittest

from tests.acceptance import _harness as H

#: 通信に使われうる標準ライブラリとサードパーティ。
FORBIDDEN_NETWORK = {
    "socket", "ssl", "urllib", "http", "requests", "ftplib", "smtplib",
    "telnetlib", "xmlrpc", "asyncio", "select", "selectors", "webbrowser",
}

#: 外部プロセスの起動。**`app/tests/` は対象外**(テストはサブプロセスを起動する)。
FORBIDDEN_PROCESS = {"subprocess", "multiprocessing", "pty", "popen2"}

#: 許可されるサードパーティは duckdb だけ (FR-003)。
ALLOWED_THIRD_PARTY = {"duckdb"}

#: 標準ライブラリのうち本アプリが使うもの。これ以外はサードパーティとみなす。
STDLIB_ALLOWED = {
    "abc", "argparse", "bisect", "collections", "configparser", "csv",
    "dataclasses", "datetime", "decimal", "enum", "functools", "glob",
    "hashlib", "io", "itertools", "json", "logging", "math", "os", "pathlib",
    "platform", "random", "re", "shutil", "statistics", "string", "sys",
    "tempfile", "textwrap", "time", "traceback", "types", "typing",
    "unicodedata", "uuid", "warnings", "operator", "copy", "heapq",
    # ※CR-007 S6 の並列実行。いずれも標準ライブラリであり外部依存ではない。
    "concurrent", "threading",
}

SCAN_ROOTS = ("src", "tools")
SCAN_FILES = ("s_anomaly.py",)

#: 実行時の標準エラーに出てはならない通信系の文言。
NETWORK_NOISE = (
    "Connection refused", "Temporary failure in name resolution",
    "getaddrinfo", "timed out", "Timeout", "proxy", "Proxy",
    "SSLError", "URLError", "Failed to download",
)


def python_files():
    found = []
    for name in SCAN_ROOTS:
        root = os.path.join(H.APP_DIR, name)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    found.append(os.path.join(dirpath, filename))
    for name in SCAN_FILES:
        found.append(os.path.join(H.APP_DIR, name))
    return found


def imported_roots(path):
    """AST から import されたトップレベルのモジュール名を (名前, 行) で返す。"""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相対 import は自パッケージ
                continue
            if node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


class TestA008NoNetwork(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA008NoNetwork, cls).setUpClass()
        cls.files = python_files()
        cls.imports = {path: imported_roots(path) for path in cls.files}

        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = os.path.join(cls._tmp.name, "work")
        os.makedirs(cls.work)
        started = time.monotonic()
        cls.proc = H.run_app(cls.normal_logs, cls.work)
        cls.elapsed = time.monotonic() - started

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _offenders(self, banned):
        found = []
        for path, entries in self.imports.items():
            rel = os.path.relpath(path, H.REPO_DIR)
            for name, lineno in entries:
                if name in banned:
                    found.append("{0}:{1}: {2}".format(rel, lineno, name))
        return sorted(found)

    # 1
    def test_01_no_network_imports(self):
        self.assertEqual(self._offenders(FORBIDDEN_NETWORK), [])

    # 2
    def test_02_no_process_spawn_imports(self):
        self.assertEqual(self._offenders(FORBIDDEN_PROCESS), [])

    def test_02b_no_os_system_or_popen(self):
        offenders = []
        for path in self.files:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if node.attr not in ("system", "popen", "execv", "spawnv"):
                    continue
                base = node.value
                if isinstance(base, ast.Name) and base.id == "os":
                    offenders.append("{0}:{1}: os.{2}".format(
                        os.path.relpath(path, H.REPO_DIR), node.lineno,
                        node.attr))
        self.assertEqual(sorted(offenders), [])

    # 3
    def test_03_only_duckdb_as_third_party(self):
        known = STDLIB_ALLOWED | ALLOWED_THIRD_PARTY | {"s_anomaly"}
        unknown = []
        for path, entries in self.imports.items():
            rel = os.path.relpath(path, H.REPO_DIR)
            for name, lineno in entries:
                if name in known:
                    continue
                unknown.append("{0}:{1}: {2}".format(rel, lineno, name))
        self.assertEqual(sorted(unknown), [],
                         "duckdb 以外の外部依存 (FR-003 違反の疑い)")

    def test_03b_scan_covered_all_modules(self):
        """走査対象が実際にソース全体を覆っていること (0 件検査の防止)。"""
        self.assertGreaterEqual(len(self.files), 25)
        self.assertTrue(any(f.endswith("bootstrap.py") for f in self.files))
        self.assertTrue(any(f.endswith("gen_testdata.py") for f in self.files))

    # 4 / 5
    def test_04_05_extension_autoload_disabled(self):
        from s_anomaly import bootstrap, progress as progress_mod

        progress = progress_mod.setup(io.StringIO(), io.StringIO())
        with tempfile.TemporaryDirectory() as tmp:
            module, _note = bootstrap.load_duckdb(H.APP_DIR, progress)
            con = bootstrap.open_connection(
                module, "512MB", os.path.join(tmp, "tmp"), progress)
            try:
                for key in ("autoinstall_known_extensions",
                            "autoload_known_extensions"):
                    try:
                        value = con.execute(
                            "SELECT current_setting('{0}')".format(key)
                        ).fetchone()[0]
                    except Exception as exc:  # 設定が無いバージョン
                        self.assertIn("not", str(exc).lower(), key)
                        continue
                    self.assertIn(str(value).lower(), ("false", "0"),
                                  "{0} = {1} (DuckDB {2})".format(
                                      key, value,
                                      getattr(module, "__version__", "?")))
            finally:
                con.close()

    # 6
    def test_06_no_network_noise_in_stderr(self):
        message = H.err(self.proc)
        for token in NETWORK_NOISE:
            self.assertNotIn(token, message, token)

    # 7
    def test_07_runtime_not_inflated(self):
        """通信のタイムアウト待ちが起きていれば実行時間が大きく伸びる。"""
        self.assertLess(self.elapsed, 300.0,
                        "実行に {0:.1f} 秒かかっています".format(self.elapsed))

    # 8
    def test_08_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, H.err(self.proc))


if __name__ == "__main__":
    unittest.main()
