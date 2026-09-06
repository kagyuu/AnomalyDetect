#!/usr/bin/env python3
"""s_anomaly — 運用ログから異常箇所を抽出して report.md を生成する。

このファイルは `src/` を sys.path に追加して cli.main() を呼ぶだけの
薄い起動スクリプトである (P003 DS-01)。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from s_anomaly.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
