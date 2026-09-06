"""ALG-A5 曜日・時刻別ベースライン (P003 7.8)。

運用ログは日次・週次の周期を持つ。「同じ曜日 × 同じ時刻帯」の分布を基準にすると、
夜間バッチのピークを異常と誤判定せず、平日に週末相当の値が出たことを捉えられる。

自分自身をバケット統計から除く (leave-one-out)。含めると、その点自身が
平均を引き上げて検知力が落ちる。
"""

import math

from .base import (
    SHAPE_SEASONAL_DEV, SKIP_FEW_POINTS, SKIP_NO_BASELINE,
    Detection, Detector, apply_persistence_rule, effective_sigma, fetch_series,
    interpolate_score, merge_adjacent, score_to_hint, take_values_with_context,
)


class A5Seasonal(Detector):
    id = "ALG-A5"
    name = "曜日・時刻別ベースライン"
    aspect = "観点1"

    def run(self, con, cfg, series, progress):
        z_threshold = float(cfg.param("ALG-A5.z"))
        min_weeks = int(cfg.param("ALG-A5.min_weeks"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        rows = con.execute(
            """
            SELECT dayofweek(ts) AS dow, hour(ts) AS hr,
                   avg(value) AS mu, stddev_samp(value) AS sd,
                   count(*) AS n,
                   count(DISTINCT date_trunc('week', ts)) AS weeks
            FROM metrics WHERE series_id = ? AND metric = ?
            GROUP BY 1, 2
            """,
            [series.series_id, series.metric],
        ).fetchall()

        buckets = {}
        for dow, hr, mu, sd, n, weeks in rows:
            # 有効バケットは min_weeks 以上の週数を持ち、leave-one-out が
            # 計算できる (n >= 3) ものに限る
            if weeks is None or weeks < min_weeks or n < 3:
                continue
            buckets[(dow, hr)] = (float(mu), float(sd or 0.0), int(n))

        if not buckets:
            return [], [self.skip(series, SKIP_NO_BASELINE)]

        detections = []
        for i, (ts, value, _segment) in enumerate(points):
            key = (ts.weekday(), ts.hour)
            stats = buckets.get(self._duckdb_key(key, buckets))
            if stats is None:
                continue
            mu, sd, n = stats
            mu_loo, sd_loo = _leave_one_out(mu, sd, n, value)
            if mu_loo is None:
                continue
            sigma = effective_sigma(sd_loo, mu_loo, cfg)
            z = abs(value - mu_loo) / sigma
            if z >= z_threshold:
                score = interpolate_score(z, z_threshold, z_threshold * 2.0)
                detections.append(Detection(
                    series_id=series.series_id, source=series.source,
                    metric=series.metric, algorithm=self.id,
                    start_ts=ts, end_ts=ts,
                    values=take_values_with_context(points, i, i),
                    score=score, severity_hint=score_to_hint(score),
                    detail={
                        "dow": ts.weekday(), "hour": ts.hour,
                        "bucket_mu": mu_loo, "bucket_sigma": sigma,
                        "z": z, "n_bucket": n, "value": value,
                    },
                    shape=SHAPE_SEASONAL_DEV,
                ))
        detections = apply_persistence_rule(detections, series.interval_sec)
        if not detections:
            return [], []
        return merge_adjacent(detections, series.interval_sec), []

    @staticmethod
    def _duckdb_key(python_key, buckets):
        """DuckDB の dayofweek は日曜=0。Python の weekday は月曜=0。

        実際の戻り値の基準を仮定せず、バケットに存在するキーへ写像する。
        """
        py_dow, hour = python_key
        # Python(月=0..日=6) -> DuckDB(日=0..土=6)
        duck_dow = (py_dow + 1) % 7
        if (duck_dow, hour) in buckets:
            return (duck_dow, hour)
        if (py_dow, hour) in buckets:
            return (py_dow, hour)
        return (duck_dow, hour)


def _leave_one_out(mu, sd, n, x):
    """自分自身を除いた平均・標準偏差を返す (DS-08-A5-04)。"""
    if n < 3:
        return None, None
    mu_loo = (n * mu - x) / float(n - 1)
    var = sd * sd
    var_loo = ((n - 1) * var - (n / float(n - 1)) * (x - mu) ** 2) / float(n - 2)
    if var_loo < 0:
        var_loo = 0.0
    return mu_loo, math.sqrt(var_loo)
