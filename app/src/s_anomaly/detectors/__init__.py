"""検知器のレジストリ (P003 7.1 DS-08-01)。"""

from .a1_ma_sigma import A1MovingAverage
from .a2_hampel import A2Hampel
from .a3_tukey import A3Tukey
from .a4_ewma import A4Ewma
from .a5_seasonal import A5Seasonal
from .b1_ma_cross import B1MaCross
from .b2_mann_kendall import B2MannKendall
from .b3_rolling_min import B3RollingMin
from .b4_cusum import B4Cusum
from .b5_regression import B5Regression
from .c1_saturation import C1Saturation  # ※CR-005
from .base import Detection, Detector, SkipInfo  # noqa: F401 (再エクスポート)

_DETECTORS = [
    A1MovingAverage(),
    A2Hampel(),
    A3Tukey(),
    A4Ewma(),
    A5Seasonal(),
    B1MaCross(),
    B2MannKendall(),
    B3RollingMin(),
    B4Cusum(),
    B5Regression(),
    C1Saturation(),   # ※CR-005 観点3
]

REGISTRY = {d.id: d for d in _DETECTORS}


def enabled_detectors(cfg):
    """設定で有効なアルゴリズムを ID 昇順で返す。"""
    return [REGISTRY[alg_id] for alg_id in cfg.enabled_algorithms()
            if alg_id in REGISTRY]
