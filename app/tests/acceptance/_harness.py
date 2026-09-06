"""受け入れテスト共通のハーネス (P009 4.2)。

**`main()` を直接呼ばず、必ずサブプロセスとして起動する。**
プロセスとしての終了コードと、標準出力・標準エラーの分離を確認することが
本フェーズの目的の一部であるため (P009 4.2)。
"""

import json
import os
import re
import subprocess
import sys
import unittest

from tests.integration import _setup_baseline, report_parser

APP_DIR = _setup_baseline.APP_DIR
REPO_DIR = os.path.dirname(APP_DIR)
ENTRY = os.path.join(APP_DIR, "s_anomaly.py")
FIXTURES = os.path.join(APP_DIR, "tests", "fixtures")
TOOL = os.path.join(APP_DIR, "tools", "gen_testdata.py")

SEVERITY_RANK = {"INFO": 0, "WARN": 1, "FATAL": 2, "SEVERE": 3}

#: 比較から除外してよい行は「実行日時」と「対象ディレクトリ」だけである (A003/A005)。
TIMESTAMP_LINE = re.compile(r"^\| 実行日時 \| .* \|$")
TARGET_DIR_LINE = re.compile(r"^\| 対象ディレクトリ \| .* \|$")

#: 標準出力の先頭時刻と所要時間は実行ごとに変わる (A003 手順 5)。
_STDOUT_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_ELAPSED = re.compile(r"所要 [\d.]+s|総所要時間 [\d.]+s|[\d.]+ 秒")

#: 配布物のコピーで除外するもの。
DIST_IGNORE = ("_work", "__pycache__", "*.pyc")


def run_app(logs_dir, cwd, entry=None, env_extra=None, timeout=1800):
    """アプリをサブプロセスとして起動する。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, entry or ENTRY, str(logs_dir)],
        cwd=str(cwd), capture_output=True, env=env, timeout=timeout,
    )


def run_raw(args, cwd, env_extra=None, timeout=1800):
    """引数をそのまま渡して起動する (引数不正の確認用)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, ENTRY] + [str(a) for a in args],
        cwd=str(cwd), capture_output=True, env=env, timeout=timeout,
    )


def dec(raw):
    return raw.decode("utf-8", errors="replace")


def out(proc):
    return dec(proc.stdout)


def err(proc):
    return dec(proc.stderr)


def report_paths(work):
    """出力されたレポート群のパス一覧 (※CR-001)。

    ホスト別ファイルとサマリの両方を、名前順で返す。
    """
    import glob

    return sorted(glob.glob(os.path.join(str(work), "report_*.md")))


def host_report_paths(work):
    """ホスト別ファイルだけ (イベント本文を持つもの)。"""
    return [p for p in report_paths(work)
            if not os.path.basename(p).startswith("report_summary_")]


def summary_paths(work):
    """サマリファイルだけ。"""
    return [p for p in report_paths(work)
            if os.path.basename(p).startswith("report_summary_")]


def report_path(work):
    """後方互換。**1 つ目のホスト別ファイル**を返す。

    ※CR-001 で出力がファイル群になったため、単一のパスを前提にする呼び出しは
    原則として report_paths() へ移すこと。
    """
    paths = host_report_paths(work) or report_paths(work)
    if not paths:
        return os.path.join(str(work), "report_summary_missing.md")
    return paths[0]


def read_report(work):
    """全ホスト別ファイルを連結して返す (※CR-001)。

    `expected.json` との突き合わせは**全ファイルを合わせた集合**に対して
    行うため (P006 TP-15)、解析用にはこの連結を使う。
    """
    parts = []
    for path in host_report_paths(work):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parts.append(handle.read())
    return "\n".join(parts)


def read_summary(work):
    """サマリファイルを連結して返す。"""
    parts = []
    for path in summary_paths(work):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parts.append(handle.read())
    return "\n".join(parts)


def read_all_reports(work):
    """サマリも含めた全ファイルを連結して返す。"""
    parts = []
    for path in report_paths(work):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parts.append(handle.read())
    return "\n".join(parts)


def read_report_bytes(work):
    """物理仕様 (BOM なし UTF-8 / LF) の確認用。全ファイルを連結したバイト列。"""
    out = b""
    for path in report_paths(work):
        with open(path, "rb") as handle:
            out += handle.read()
    return out


def strip_volatile(text, drop_target_dir=False):
    """実行ごとに変わる行を落とす。落としてよい行は 2 種類だけである。"""
    kept = []
    for line in text.splitlines():
        if TIMESTAMP_LINE.match(line):
            continue
        if drop_target_dir and TARGET_DIR_LINE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def strip_stdout_volatile(text, work_dir=None):
    """標準出力から実行ごとに変わる部分を落とす。

    `work_dir` を渡すと、その絶対パスを `{WORK}` に置き換える。
    A003 は「1 回目用と 2 回目用に別々の一時ディレクトリ」を作ることを
    指示しており (A003 事前準備 2)、S9 の出力先パスはその指示の結果として
    必ず異なる。**アプリの非決定性ではないため置き換える。**
    """
    kept = []
    for line in text.splitlines():
        line = _STDOUT_TIME.sub("", line)
        line = _ELAPSED.sub("", line)
        if work_dir:
            line = line.replace(str(work_dir), "{WORK}")
        kept.append(line.rstrip())
    return "\n".join(kept)


def write_settings(work, body):
    path = os.path.join(str(work), "settings.properties")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return path


def duckdb_settings(max_memory="4GB", temp_directory="./tmp"):
    return "[duckdb]\nmax_memory = {0}\ntemp_directory = {1}\n".format(
        max_memory, temp_directory)


def make_dist(root, settings=None, with_tests=True):
    """`app/` を配布物としてコピーし、コピー先のエントリポイントを返す。

    **`settings.properties` は `s_anomaly.py` と同じディレクトリから読まれる**
    (P002 UI-02-L01)。作業ディレクトリに置いても効かないため、設定を変える
    テストは必ずこの複製を使う。
    """
    import shutil

    dist_app = os.path.join(str(root), "app")
    ignore = list(DIST_IGNORE)
    if not with_tests:
        ignore.append("tests")
    shutil.copytree(APP_DIR, dist_app, ignore=shutil.ignore_patterns(*ignore))
    if settings is not None:
        with open(os.path.join(dist_app, "settings.properties"),
                  "w", encoding="utf-8", newline="\n") as handle:
            handle.write(settings)
    return os.path.join(dist_app, "s_anomaly.py")


def severity_counts(events):
    counts = {}
    for event in events:
        counts[event.severity] = counts.get(event.severity, 0) + 1
    return counts


def parse_events(text):
    return report_parser.parse_events(text)


def section(text, heading):
    """`report.md` から 1 つの節の本文を取り出す。"""
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return ""
    body = []
    depth = heading.split(" ")[0]
    for line in lines[start + 1:]:
        if line.startswith("#") and len(line.split(" ")[0]) <= len(depth):
            break
        body.append(line)
    return "\n".join(body)


def snapshot_tree(root, ignore_names=("__pycache__",)):
    """ファイル一覧 (相対パス -> (サイズ, 更新日時)) を返す。"""
    found = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in ignore_names]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, str(root))
            stat = os.stat(path)
            found[rel.replace("\\", "/")] = (stat.st_size, int(stat.st_mtime))
    return found


class BaselineCase(unittest.TestCase):
    """ベースラインをスイートで 1 回だけ復元する土台 (P009 4.3)。"""

    @classmethod
    def setUpClass(cls):
        cls.paths = _setup_baseline.restore()
        cls.normal_logs = os.path.join(cls.paths["normal"], "logs")
        cls.broken_logs = os.path.join(cls.paths["broken"], "logs")
        with open(os.path.join(cls.paths["normal"], "expected.json"),
                  "r", encoding="utf-8") as handle:
            cls.expected = json.load(handle)
