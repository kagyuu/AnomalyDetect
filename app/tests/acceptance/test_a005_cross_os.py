"""A005 — 両 OS での一致 (NFR-006)。

Windows(検証環境の Python)と WSL2 Ubuntu(Python 3.10)で同一の結果になること、
および `docs/P003-backend-spec.md` DS-00-01 の言語バージョン制約
(3.9 で動く構文のみ)が守られていることを確認する。

WSL2 が使えない場合は `NOT RUN` として skip し、P302 の「未整備事項」へ引き継ぐ。
"""

import difflib
import filecmp
import os
import re
import subprocess
import tempfile
import unittest

from tests.acceptance import _harness as H

WSL_DISTRO = "Ubuntu"


def to_wsl_path(path):
    """`C:\\x\\y` -> `/mnt/c/x/y`。リポジトリの位置に依存しない。"""
    path = os.path.abspath(str(path))
    drive, rest = os.path.splitdrive(path)
    return "/mnt/{0}{1}".format(drive[0].lower(), rest.replace("\\", "/"))


def wsl(command, timeout=1800):
    return subprocess.run(
        ["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", command],
        capture_output=True, timeout=timeout,
    )


def wsl_text(raw):
    # wsl.exe は環境によって UTF-16LE を返すため、NUL を落としてから解釈する。
    if raw[:1] == b"\x00" or b"\x00" in raw[:64]:
        try:
            return raw.decode("utf-16-le", errors="replace")
        except Exception:  # pragma: no cover
            pass
    return raw.decode("utf-8", errors="replace")


def wsl_available():
    try:
        proc = wsl('python3 -c "import duckdb; print(duckdb.__version__)"',
                   timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


AVAILABLE = wsl_available()
REASON = "WSL2 ({0}) が利用できないため NOT RUN。P302 の未整備事項へ引き継ぐ".format(
    WSL_DISTRO)


@unittest.skipUnless(AVAILABLE, REASON)
class TestA005CrossOs(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA005CrossOs, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()

        # --- Windows 側 ---
        cls.win_work = os.path.join(cls._tmp.name, "win")
        os.makedirs(cls.win_work)
        cls.win_proc = H.run_app(cls.normal_logs, cls.win_work)
        cls.win_report = (H.read_report(cls.win_work)
                          if os.path.isfile(H.report_path(cls.win_work)) else "")
        cls.win_raw = (H.read_report_bytes(cls.win_work)
                       if os.path.isfile(H.report_path(cls.win_work)) else b"")

        # --- Linux 側: 生成の決定性 ---
        cls.lin_gen_dir = os.path.join(cls._tmp.name, "lin-gen")
        os.makedirs(cls.lin_gen_dir)
        cls.gen_proc = wsl("cd {0} && python3 {1} {2}".format(
            to_wsl_path(H.REPO_DIR), to_wsl_path(H.TOOL),
            to_wsl_path(cls.lin_gen_dir)))

        # --- Linux 側: アプリ実行 (入力は Windows 側で生成したデータ) ---
        cls.lin_work = os.path.join(cls._tmp.name, "lin")
        os.makedirs(cls.lin_work)
        cls.lin_proc = wsl("cd {0} && python3 {1} {2}".format(
            to_wsl_path(cls.lin_work), to_wsl_path(H.ENTRY),
            to_wsl_path(cls.normal_logs)))
        cls.lin_report = (H.read_report(cls.lin_work)
                          if os.path.isfile(H.report_path(cls.lin_work)) else "")
        cls.lin_raw = (H.read_report_bytes(cls.lin_work)
                       if os.path.isfile(H.report_path(cls.lin_work)) else b"")

        cls.win_events = H.parse_events(cls.win_report)
        cls.lin_events = H.parse_events(cls.lin_report)

        cls.python_versions = {
            "windows": _local_python_version(),
            "linux": wsl_text(wsl(
                'python3 -c "import sys; print(sys.version.split()[0])"'
            ).stdout).strip(),
        }

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # 1
    def test_01_windows_exit_zero(self):
        self.assertEqual(self.win_proc.returncode, 0, H.err(self.win_proc))

    # 2
    def test_02_linux_exit_zero(self):
        self.assertEqual(self.lin_proc.returncode, 0,
                         wsl_text(self.lin_proc.stderr))

    # 3
    def test_03_generated_data_identical(self):
        """`gen_testdata.py` の出力が両 OS でバイト単位に一致すること。"""
        self.assertEqual(self.gen_proc.returncode, 0,
                         wsl_text(self.gen_proc.stderr))
        win_logs = self.normal_logs
        lin_logs = os.path.join(self.lin_gen_dir, "logs")
        win_names = sorted(os.listdir(win_logs))
        lin_names = sorted(os.listdir(lin_logs))
        self.assertEqual(win_names, lin_names)
        mismatch = []
        for name in win_names:
            left = os.path.join(win_logs, name)
            right = os.path.join(lin_logs, name)
            if not filecmp.cmp(left, right, shallow=False):
                mismatch.append(name)
        self.assertEqual(mismatch, [], "一致しないファイル: {0}".format(mismatch))
        for name in win_names[:5]:
            with open(os.path.join(lin_logs, name), "rb") as handle:
                self.assertNotIn(b"\r\n", handle.read(), name)

    # 4
    def test_04_reports_identical(self):
        left = H.strip_volatile(self.win_report, drop_target_dir=True)
        right = H.strip_volatile(self.lin_report, drop_target_dir=True)
        diff = "\n".join(list(difflib.unified_diff(
            left.splitlines(), right.splitlines(),
            fromfile="windows", tofile="linux", lineterm=""))[:50])
        self.assertEqual(left, right, diff)

    # 5
    def test_05_severity_counts_identical(self):
        self.assertEqual(H.severity_counts(self.win_events),
                         H.severity_counts(self.lin_events))

    # 6
    def test_06_event_ids_identical(self):
        self.assertEqual([e.event_id for e in self.win_events],
                         [e.event_id for e in self.lin_events])

    # 7
    def test_07_linux_report_uses_lf(self):
        self.assertNotIn(b"\r\n", self.lin_raw)

    # 8
    def test_08_windows_report_uses_lf(self):
        self.assertNotIn(b"\r\n", self.win_raw)
        self.assertFalse(self.win_raw.startswith(b"\xef\xbb\xbf"))

    # 9
    def test_09_no_syntax_error_on_python310(self):
        for name, message in (("windows", H.err(self.win_proc)),
                              ("linux", wsl_text(self.lin_proc.stderr))):
            self.assertNotIn("Traceback", message, name)
            self.assertNotIn("SyntaxError", message, name)

    def test_09b_compileall_on_linux(self):
        """DS-00-01: Linux 側 (Python 3.10) でも全ファイルが構文的に通ること。"""
        proc = wsl("cd {0} && python3 -m compileall -q app/src app/tools "
                   "app/s_anomaly.py".format(to_wsl_path(H.REPO_DIR)))
        self.assertEqual(proc.returncode, 0, wsl_text(proc.stdout)
                         + wsl_text(proc.stderr))

    # 10
    def test_10_japanese_not_garbled(self):
        for name, text in (("windows", self.win_report),
                           ("linux", self.lin_report)):
            self.assertIn("実行サマリ", text, name)
            self.assertIn("検知イベント", text, name)
            self.assertNotIn("\ufffd", text, name)

    def test_11_python_versions_differ(self):
        """検証環境が実際に異なる Python 版であること (本テストの前提)。"""
        self.assertTrue(self.python_versions["linux"])
        self.assertNotEqual(self.python_versions["windows"],
                            self.python_versions["linux"])


def _local_python_version():
    import sys
    return "{0}.{1}.{2}".format(*sys.version_info[:3])


_WSL_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

if __name__ == "__main__":
    unittest.main()
