"""ALG-A2 Hampel フィルタ (P003 7.5)。

移動中央値と MAD を使うため、ALG-A1 のマスキング(外れ値自身が平均・分散を
汚染して大きな外れ値ほど検知しにくくなる)に強い。

ALG-A1 と同じ理由で、**窓の中央値からの残差**を求め、その残差の MAD で
正規化する。移動中央値も系列の傾き・周期に対して系統的に遅れるため、
窓内の MAD で割ると日次の周期が毎日「異常」として検出されてしまう。
"""

import statistics

from .base import (
    SHAPE_SPIKE_DOWN, SHAPE_SPIKE_UP, SKIP_FEW_POINTS, WINDOW_MIN_POINTS,
    Detection, Detector, apply_persistence_rule, effective_sigma, fetch_series,
    fetch_segments, interpolate_score, merge_adjacent, score_to_hint,
    take_values_with_context,
)

MAD_SCALE = 1.4826
#: FATAL 相当とみなす k の倍率。ALG-A2 には FATAL 閾値のパラメータが無いため。
FATAL_RATIO = 1.5


class A2Hampel(Detector):
    id = "ALG-A2"
    name = "Hampel フィルタ"
    aspect = "観点1"

    def run(self, con, cfg, series, progress):
        window_sec = float(cfg.param("ALG-A2.window_minutes")) * 60.0
        k = float(cfg.param("ALG-A2.k"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        segments = fetch_segments(con, series)
        per_segment = []
        residuals = []
        for segment in segments:
            res = _median_residuals(segment, window_sec)
            per_segment.append((segment, res))
            residuals.extend(r[2] for r in res)
        if len(residuals) < WINDOW_MIN_POINTS:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        med_res = statistics.median(residuals)
        mad_res = statistics.median([abs(r - med_res) for r in residuals])
        level = statistics.median([p[1] for p in points])
        scale = effective_sigma(MAD_SCALE * mad_res, level, cfg)

        detections = []
        for segment, res in per_segment:
            for i, med_window, residual in res:
                dev = abs(residual - med_res) / scale
                if dev < k:
                    continue
                ts, value = segment[i]
                score = interpolate_score(dev, k, k * FATAL_RATIO)
                detections.append(Detection(
                    series_id=series.series_id, source=series.source,
                    metric=series.metric, algorithm=self.id,
                    start_ts=ts, end_ts=ts,
                    values=take_values_with_context(segment, i, i),
                    score=score, severity_hint=score_to_hint(score),
                    detail={
                        "median": med_window, "residual": residual,
                        "residual_mad": mad_res, "scale": scale,
                        "dev": dev, "k": k, "value": value,
                    },
                    shape=SHAPE_SPIKE_UP if residual > med_res else SHAPE_SPIKE_DOWN,
                ))

        detections = apply_persistence_rule(detections, series.interval_sec)
        if not detections:
            return [], []
        return merge_adjacent(detections, series.interval_sec), []


def _median_residuals(points, window_sec):
    """(index, 窓の中央値, 残差) のリストを返す。窓は現在点を含まない。"""
    out = []
    left = 0
    for i in range(len(points)):
        ts, value = points[i]
        while left < i and (ts - points[left][0]).total_seconds() > window_sec:
            left += 1
        window = [points[j][1] for j in range(left, i)]
        if len(window) >= WINDOW_MIN_POINTS:
            med = statistics.median(window)
            out.append((i, med, value - med))
    return out
