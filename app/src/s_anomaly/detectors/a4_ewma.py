"""ALG-A4 EWMA 管理図 (P003 7.7)。

指数加重移動平均は、単発の跳ねよりも**緩やかな水準変化**に感度を持つ。
ALG-A1 が見逃す「水準がずれて、そのまま続く」変化を捉える。
再帰的定義のため Python 側で逐次計算する。

**平滑化の対象は生値でも移動平均の残差でもなく、前日同時刻との差分**
(季節差分 `d_t = x_t − x_{t−24h}`)とする。理由は次のとおり。

* 生値を平滑化すると、EWMA 管理図が前提とする定常性が日次の周期によって
  破れ、周期そのものが毎日「管理限界外」になる(実測で検知率 10% 超)。
* 移動平均からの残差を平滑化すると周期には強くなるが、**水準シフトを
  移動平均が数十分で吸収してしまい、A4 が本来見るべき持続的な変化が
  残差から消える**。実測では ALG-A1 が検知するものしか検知できず、
  独立したアルゴリズムとしての価値を失った。
* 季節差分なら、周期成分は前日と打ち消し合って消え、**水準シフトと傾向は
  差分に残り続ける**。時系列解析で標準的な前処理でもある。

必要な履歴は 24 時間ぶんである。それに満たない系列はスキップする。
"""

import math
import statistics

from .base import (
    SEASONAL_LAG_SEC, SHAPE_SPIKE_DOWN, SHAPE_SPIKE_UP,
    SKIP_FEW_POINTS, SKIP_NO_BASELINE, WINDOW_MIN_POINTS,
    Detection, Detector, apply_persistence_rule, effective_sigma, fetch_series,
    fetch_segments, interpolate_score, merge_adjacent, score_to_hint,
    seasonal_diffs, take_values_with_context,
)


class A4Ewma(Detector):
    id = "ALG-A4"
    name = "EWMA 管理図"
    aspect = "観点1"

    def run(self, con, cfg, series, progress):
        lam = float(cfg.param("ALG-A4.lambda"))
        k = float(cfg.param("ALG-A4.k"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        segments = fetch_segments(con, series)
        per_segment = []
        diffs = []
        for segment in segments:
            d = seasonal_diffs(segment)
            per_segment.append((segment, d))
            diffs.extend(x[1] for x in d)
        if len(diffs) < WINDOW_MIN_POINTS:
            # 24 時間ぶんの履歴が無い
            return [], [self.skip(series, SKIP_NO_BASELINE)]

        mu_d = sum(diffs) / float(len(diffs))
        sd_d = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
        level = sum(p[1] for p in points) / float(len(points))
        sigma = effective_sigma(sd_d, level, cfg)

        detections = []
        for segment, d in per_segment:
            detections.extend(self._scan(series, segment, d, mu_d, sigma, lam, k))
        detections = apply_persistence_rule(detections, series.interval_sec)
        if not detections:
            return [], []
        return merge_adjacent(detections, series.interval_sec), []

    def _scan(self, series, points, diffs, mu_d, sigma, lam, k):
        out = []
        z = mu_d
        factor = lam / (2.0 - lam)
        for step, (i, diff) in enumerate(diffs, start=1):
            z = lam * diff + (1.0 - lam) * z
            sigma_z = sigma * math.sqrt(
                max(factor * (1.0 - (1.0 - lam) ** (2 * step)), 0.0)
            )
            if sigma_z <= 0:
                continue
            deviation = abs(z - mu_d)
            if deviation > k * sigma_z:
                score = interpolate_score(deviation / sigma_z, k, k * 2.0)
                ts, value = points[i]
                out.append(Detection(
                    series_id=series.series_id, source=series.source,
                    metric=series.metric, algorithm=self.id,
                    start_ts=ts, end_ts=ts,
                    values=take_values_with_context(points, i, i),
                    score=score, severity_hint=score_to_hint(score),
                    detail={
                        "mu_diff": mu_d, "sigma": sigma, "lambda": lam, "k": k,
                        "z": z, "sigma_z": sigma_z, "value": value,
                        "seasonal_lag_hours": SEASONAL_LAG_SEC / 3600,
                    },
                    shape=SHAPE_SPIKE_UP if z > mu_d else SHAPE_SPIKE_DOWN,
                ))
        return out
