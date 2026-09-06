"""ALG-B1 短期/長期移動平均のクロス継続 (P003 7.9)。

P000 が例示した「長期の移動平均よりも短期の移動平均が常に上にある」。
短期が長期を上回る状態が連続して続くことを、持続的な増加とみなす。
"""

from .base import (
    SHAPE_TREND_UP, SKIP_FEW_POINTS,
    Detection, Detector, effective_sigma, fetch_series, fetch_segments,
    mean_and_sd, noise_scale, sample_values, score_to_hint,
)

#: 微小なクロスを除くための、短期-長期差の下限係数 (DS-08-B1-03)
CROSS_MARGIN_RATIO = 0.25


class B1MaCross(Detector):
    id = "ALG-B1"
    name = "短期/長期移動平均のクロス継続"
    aspect = "観点2"

    def run(self, con, cfg, series, progress):
        short_sec = float(cfg.param("ALG-B1.short_minutes")) * 60.0
        long_sec = float(cfg.param("ALG-B1.long_minutes")) * 60.0
        min_run = int(cfg.param("ALG-B1.min_run"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        mu, _sd = mean_and_sd([p[1] for p in points])
        # マージンはノイズ規模から求める。系列全体の σ を使うと、増加傾向
        # そのものが σ を膨らませ、検知したい傾向を弾いてしまう。
        sigma = effective_sigma(noise_scale(points), mu, cfg)
        margin = sigma * CROSS_MARGIN_RATIO

        # 連続点数の下限は「長期窓を 1 周期ぶん超えること」を満たす必要がある。
        # 周期を持つ系列では、短期移動平均は 1 周期の約半分のあいだ必ず長期を
        # 上回る。長期窓より短い連続を「持続的な増加」とみなすと、日次の周期が
        # 毎日検知される(実測で 1 系列あたり数十件)。
        interval = series.interval_sec or 0.0
        long_points = int(long_sec / interval) if interval > 0 else min_run
        effective_min_run = max(min_run, long_points)

        detections = []
        for segment in fetch_segments(con, series):
            detections.extend(
                self._scan(series, segment, short_sec, long_sec,
                           effective_min_run, margin)
            )
        return detections, []

    def _scan(self, series, points, short_sec, long_sec, min_run, margin):
        out = []
        sma_s = _moving_average(points, short_sec)
        sma_l = _moving_average(points, long_sec)
        start_ts = points[0][0]

        run_start = None
        for i in range(len(points)):
            ts = points[i][0]
            # 長期窓が埋まるまでは判定しない (DS-08-B1-02)
            if (ts - start_ts).total_seconds() < long_sec:
                run_start = None
                continue
            if sma_s[i] is None or sma_l[i] is None:
                run_start = None
                continue
            above = (sma_s[i] - sma_l[i]) > margin
            if above:
                if run_start is None:
                    run_start = i
            else:
                if run_start is not None:
                    out.extend(self._emit(series, points, sma_s, sma_l,
                                          run_start, i - 1, min_run, margin))
                run_start = None
        if run_start is not None:
            out.extend(self._emit(series, points, sma_s, sma_l,
                                  run_start, len(points) - 1, min_run, margin))
        return out

    def _emit(self, series, points, sma_s, sma_l, i0, i1, min_run, margin):
        length = i1 - i0 + 1
        if length < min_run:
            return []
        score = length / float(min_run)
        values = sample_values([points[j][1] for j in range(i0, i1 + 1)])
        return [Detection(
            series_id=series.series_id, source=series.source,
            metric=series.metric, algorithm=self.id,
            start_ts=points[i0][0], end_ts=points[i1][0],
            values=values, score=score, severity_hint=score_to_hint(score),
            detail={
                "run_length": length, "min_run": min_run,
                "sma_short_end": sma_s[i1], "sma_long_end": sma_l[i1],
                "margin": margin,
            },
            shape=SHAPE_TREND_UP,
        )]


def _moving_average(points, window_sec):
    """各点について直前 window_sec 分 (現在点を含む) の平均を返す。"""
    out = []
    left = 0
    running = 0.0
    for i in range(len(points)):
        ts, value = points[i]
        running += value
        while left <= i and (ts - points[left][0]).total_seconds() > window_sec:
            running -= points[left][1]
            left += 1
        n = i - left + 1
        out.append(running / n if n > 0 else None)
    return out
