"""ALG-B5 線形回帰の傾きと決定係数 (P003 7.13)。

増加率を「1 日あたり N 増加」という人間が解釈しやすい形で示す。
R² だけを見ると、ほぼ横ばいでも直線に乗っていれば高くなるため、
**増加量が実効的なばらつきを超えること**も条件に加える。
"""

from .base import (
    SHAPE_TREND_UP, SKIP_FEW_POINTS,
    Detection, Detector, effective_sigma, fetch_series, mean_and_sd,
    sample_values, score_to_hint,
)


class B5Regression(Detector):
    id = "ALG-B5"
    name = "線形回帰の傾き"
    aspect = "観点2"

    def run(self, con, cfg, series, progress):
        min_r2 = float(cfg.param("ALG-B5.min_r2"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        row = con.execute(
            """
            SELECT regr_slope(value, epoch(ts)) AS slope,
                   regr_r2(value, epoch(ts)) AS r2,
                   count(*) AS n, min(ts) AS t0, max(ts) AS t1
            FROM metrics WHERE series_id = ? AND metric = ?
            """,
            [series.series_id, series.metric],
        ).fetchone()
        slope, r2, n, t0, t1 = row
        if slope is None or r2 is None or t0 is None or t1 is None:
            return [], []
        if slope <= 0:
            return [], []
        if r2 < min_r2:
            return [], []

        span_sec = (t1 - t0).total_seconds()
        total_increase = slope * span_sec
        mu, sd = mean_and_sd([p[1] for p in points])
        sigma = effective_sigma(sd, mu, cfg)
        if total_increase <= sigma:
            return [], []

        score = min(r2 / min_r2, total_increase / sigma)
        return [Detection(
            series_id=series.series_id, source=series.source,
            metric=series.metric, algorithm=self.id,
            start_ts=t0, end_ts=t1,
            values=sample_values([p[1] for p in points]),
            score=score, severity_hint=score_to_hint(score),
            detail={
                "slope_per_day": slope * 86400.0, "r2": r2,
                "total_increase": total_increase,
                "effective_sigma": sigma, "min_r2": min_r2,
            },
            shape=SHAPE_TREND_UP,
        )], []
