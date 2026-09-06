"""テストパッケージ。

`app/src` を `sys.path` に追加するブートストラップを持つ(P006 TP-09a)。
`tests` パッケージが import された時点で必ず実行されるため、
`unittest discover -t app` でも単一メソッド指定でも確実に効く。

これはテスト専用の仕組みであり、アプリ本体(app/s_anomaly.py -> app/src/)は
app/tests/ に依存しない(A010 のケース C がこの独立性を確認する)。
"""

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
