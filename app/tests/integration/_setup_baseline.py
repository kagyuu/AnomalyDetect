#!/usr/bin/env python3
"""テストスイートのベースライン復元 (P006 TP-03 〜 TP-06)。

**スイート全体で 1 回だけ、テスト対象を起動する前に**実行する。
個々のテストで繰り返さない。

ベースラインとは次の状態を指す。

1. app/tests/_work/normal/ に gen_testdata.py の出力がある
2. app/tests/_work/broken/ に gen_testdata.py --broken の出力がある
3. カレントディレクトリに report.md / report.md.tmp が無い
4. temp_directory (既定 ./tmp) が空である
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(HERE))
WORK_DIR = os.path.join(APP_DIR, "tests", "_work")
NORMAL_DIR = os.path.join(WORK_DIR, "normal")
BROKEN_DIR = os.path.join(WORK_DIR, "broken")
TOOL = os.path.join(APP_DIR, "tools", "gen_testdata.py")


def _rmtree(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def restore(force=False):
    """ベースラインへ復元する。既にあれば force=True のときだけ作り直す。"""
    if force or not os.path.isdir(os.path.join(NORMAL_DIR, "logs")):
        _rmtree(WORK_DIR)
        os.makedirs(WORK_DIR, exist_ok=True)
        _run([sys.executable, TOOL, NORMAL_DIR])
        _run([sys.executable, TOOL, BROKEN_DIR, "--broken"])

    for name in ("report.md", "report.md.tmp"):
        path = os.path.join(os.getcwd(), name)
        if os.path.exists(path):
            os.unlink(path)
    tmp_dir = os.path.join(os.getcwd(), "tmp")
    _rmtree(tmp_dir)
    return {"normal": NORMAL_DIR, "broken": BROKEN_DIR}


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "ベースラインの生成に失敗しました: {0}\n{1}".format(
                " ".join(cmd), proc.stderr.decode("utf-8", errors="replace")
            )
        )


if __name__ == "__main__":
    paths = restore(force=True)
    sys.stdout.write("ベースラインを復元しました: {0}\n".format(paths))
