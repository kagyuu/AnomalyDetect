"""ALG-B2 Mann-Kendall 傾向検定 (P003 7.10)。

正規性を仮定しない非パラメトリック検定で、傾向の有無に統計的な裏付けを与える。
Theil-Sen 勾配で増加率も推定する。

P000 の観点2 は「持続的な数値の増加」であるため、**増加傾向のみを検知する**
(DS-08-B2-04)。
"""

import math
import statistics

from .base import (
    SHAPE_TREND_UP, SKIP_FEW_POINTS,
    Detection, Detector, fetch_series, fetch_segments, sample_values,
    score_to_hint,
)

#: Theil-Sen 勾配の計算に使う最大点数。超える場合は等間隔に間引く
#: (乱数抽出は使わない。NFR-009)。
SEN_MAX_POINTS = 500


class B2MannKendall(Detector):
    id = "ALG-B2"
    name = "Mann-Kendall 傾向検定"
    aspect = "観点2"

    def run(self, con, cfg, series, progress):
        bucket_minutes = int(cfg.param("ALG-B2.bucket_minutes"))
        max_buckets = int(cfg.param("ALG-B2.max_buckets"))
        p_warn = float(cfg.param("ALG-B2.p_warn"))
        p_fatal = float(cfg.param("ALG-B2.p_fatal"))
        min_points = self.min_points(cfg)

        raw = fetch_series(con, series)
        if len(raw) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        detections = []
        for segment in fetch_segments(con, series):
            if len(segment) < min_points:
                continue
            found = self._scan(
                series, segment, bucket_minutes, max_buckets, p_warn, p_fatal
            )
            detections.extend(found)
        return detections, []

    def _scan(self, series, points, bucket_minutes, max_buckets, p_warn, p_fatal):
        # バケット数が上限を超える場合は bucket_minutes を 2 倍ずつ拡大する
        buckets = _bucket(points, bucket_minutes)
        while len(buckets) > max_buckets:
            bucket_minutes *= 2
            buckets = _bucket(points, bucket_minutes)

        if len(buckets) < 3:
            return []

        values = [b[1] for b in buckets]
        s_stat, z_stat, p_value = mann_kendall(values)
        if s_stat <= 0 or p_value >= p_warn:
            return []

        slope = sen_slope(values)
        per_day = slope * (1440.0 / bucket_minutes)
        score = _score_from_p(p_value, p_warn, p_fatal)
        return [Detection(
            series_id=series.series_id, source=series.source,
            metric=series.metric, algorithm=self.id,
            start_ts=points[0][0], end_ts=points[-1][0],
            values=sample_values(values),
            score=score, severity_hint=score_to_hint(score),
            detail={
                "S": s_stat, "Z": z_stat, "p": p_value,
                "n_buckets": len(buckets), "bucket_minutes": bucket_minutes,
                "sen_slope_per_day": per_day,
                "p_warn": p_warn, "p_fatal": p_fatal,
            },
            shape=SHAPE_TREND_UP,
        )]


def _bucket(points, bucket_minutes):
    """バケット単位の平均へ集約する。"""
    width = bucket_minutes * 60
    out = []
    current_key = None
    total = 0.0
    count = 0
    first_ts = None
    base_epoch = points[0][0].timestamp()
    for ts, value in points:
        key = int((ts.timestamp() - base_epoch) // width)
        if current_key is None:
            current_key = key
            first_ts = ts
        elif key != current_key:
            out.append((first_ts, total / count))
            current_key = key
            first_ts = ts
            total = 0.0
            count = 0
        total += value
        count += 1
    if count:
        out.append((first_ts, total / count))
    return out


def mann_kendall(values):
    """(S, Z, p) を返す。p は両側。"""
    n = len(values)
    s = 0
    for i in range(n - 1):
        vi = values[i]
        for j in range(i + 1, n):
            diff = values[j] - vi
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1

    # 同値グループによる補正
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    tie_term = sum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
    var = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var <= 0:
        return s, 0.0, 1.0

    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return s, z, p


def sen_slope(values):
    """Theil-Sen 勾配 (バケットあたりの増加量)。"""
    data = values
    if len(data) > SEN_MAX_POINTS:
        step = (len(data) - 1) / float(SEN_MAX_POINTS - 1)
        data = [values[int(round(i * step))] for i in range(SEN_MAX_POINTS)]
    slopes = []
    n = len(data)
    for i in range(n - 1):
        for j in range(i + 1, n):
            slopes.append((data[j] - data[i]) / float(j - i))
    if not slopes:
        return 0.0
    return statistics.median(slopes)


def _score_from_p(p_value, p_warn, p_fatal):
    """p 値から score を対数補間する (DS-08-04)。"""
    p = max(p_value, 1e-300)
    if p >= p_warn:
        return 0.0
    span = math.log(p_warn) - math.log(p_fatal)
    if span <= 0:
        return 1.0
    return 1.0 + (math.log(p_warn) - math.log(p)) / span
