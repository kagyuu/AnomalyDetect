"""ALG-A3 Tukey の外れ値境界 (P003 7.6)。

系列全体の四分位数から境界を決める。窓を持たないため、閾値の事前知識が要らない。
一方で日次の周期性を持つ系列ではピークを常に外れ値として拾うため、
検知点が系列全長の 5% を超えた場合は結果を全破棄する (DS-08-A3-03)。
"""

from .base import (
    SHAPE_SPIKE_DOWN, SHAPE_SPIKE_UP, SKIP_FEW_POINTS, SKIP_WIDE_DISTRIBUTION,
    Detection, Detector, apply_persistence_rule, effective_sigma, fetch_series,
    interpolate_score, merge_adjacent, score_to_hint, take_values_with_context,
)

#: 検知点がこの割合を超えたら、その系列の結果を全破棄する
MAX_DETECTION_RATIO = 0.05


class A3Tukey(Detector):
    id = "ALG-A3"
    name = "Tukey の外れ値境界"
    aspect = "観点1"

    def run(self, con, cfg, series, progress):
        iqr_warn = float(cfg.param("ALG-A3.iqr_warn"))
        iqr_fatal = float(cfg.param("ALG-A3.iqr_fatal"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        row = con.execute(
            "SELECT quantile_cont(value, 0.25), quantile_cont(value, 0.75),"
            " median(value) FROM metrics WHERE series_id = ? AND metric = ?",
            [series.series_id, series.metric],
        ).fetchone()
        q1, q3, med = row
        if q1 is None or q3 is None:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        iqr_eff = effective_sigma(q3 - q1, med, cfg)
        upper_warn = q3 + iqr_warn * iqr_eff
        upper_fatal = q3 + iqr_fatal * iqr_eff
        lower_warn = q1 - iqr_warn * iqr_eff
        lower_fatal = q1 - iqr_fatal * iqr_eff

        detections = []
        for i, (ts, value, _segment) in enumerate(points):
            if value > upper_warn:
                score = interpolate_score(value, upper_warn, upper_fatal)
                shape = SHAPE_SPIKE_UP
            elif value < lower_warn:
                score = interpolate_score(-value, -lower_warn, -lower_fatal)
                shape = SHAPE_SPIKE_DOWN
            else:
                continue
            detections.append(Detection(
                series_id=series.series_id, source=series.source,
                metric=series.metric, algorithm=self.id,
                start_ts=ts, end_ts=ts,
                values=take_values_with_context(points, i, i),
                score=score, severity_hint=score_to_hint(score),
                detail={
                    "q1": q1, "q3": q3, "iqr_eff": iqr_eff,
                    "upper_warn": upper_warn, "upper_fatal": upper_fatal,
                    "lower_warn": lower_warn, "lower_fatal": lower_fatal,
                    "value": value,
                },
                shape=shape,
            ))

        detections = apply_persistence_rule(detections, series.interval_sec)

        if len(detections) > len(points) * MAX_DETECTION_RATIO:
            progress.warning(
                "S6",
                "分布が広いため ALG-A3 を {0}/{1} に適用できませんでした "
                "(検知 {2} 点 / 全 {3} 点)".format(
                    series.series_id, series.metric, len(detections), len(points)
                ),
            )
            return [], [self.skip(series, SKIP_WIDE_DISTRIBUTION)]

        if not detections:
            return [], []
        return merge_adjacent(detections, series.interval_sec), []
