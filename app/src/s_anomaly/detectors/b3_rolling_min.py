"""ALG-B3 ローリング最小値の単調増加 (P003 7.11)。

**メモリリーク検出の定番手法。** GC のたびに値は下がるが、Full GC 後の
下限(下限包絡線)が下がらなくなる現象を捉える。P000 が観点2 の例として
挙げた「メモリリークなど」に直接対応する、本アプリで最も重要なアルゴリズム。
"""

from .base import (
    SHAPE_FLOOR_RISE, SKIP_FEW_POINTS,
    Detection, Detector, effective_sigma, fetch_series, fetch_segments,
    mean_and_sd, sample_values, score_to_hint,
)


class B3RollingMin(Detector):
    id = "ALG-B3"
    name = "ローリング最小値の単調増加"
    aspect = "観点2"

    def run(self, con, cfg, series, progress):
        window_minutes = int(cfg.param("ALG-B3.window_minutes"))
        min_increases = int(cfg.param("ALG-B3.min_increases"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        mu, sd = mean_and_sd([p[1] for p in points])
        sigma = effective_sigma(sd, mu, cfg)

        detections = []
        for segment in fetch_segments(con, series):
            detections.extend(
                self._scan(series, segment, window_minutes, min_increases, sigma)
            )
        return detections, []

    def _scan(self, series, points, window_minutes, min_increases, sigma):
        buckets = _bucket_min(points, window_minutes)
        if len(buckets) < min_increases:
            return []

        out = []
        run_start = 0
        for i in range(1, len(buckets)):
            key_prev, _, min_prev = buckets[i - 1]
            key_cur, _, min_cur = buckets[i]
            # 欠測バケットで run を分断する (DS-08-B3-04)
            broken = (key_cur != key_prev + 1) or (min_cur < min_prev)
            if broken:
                out.extend(self._emit(series, points, buckets, run_start, i - 1,
                                      min_increases, sigma, window_minutes))
                run_start = i
        out.extend(self._emit(series, points, buckets, run_start,
                              len(buckets) - 1, min_increases, sigma,
                              window_minutes))
        return out

    def _emit(self, series, points, buckets, i0, i1, min_increases, sigma,
              window_minutes):
        length = i1 - i0 + 1
        if length < min_increases:
            return []
        rise = buckets[i1][2] - buckets[i0][2]
        # ほぼ横ばいの系列で「非減少が長く続いただけ」を除く (DS-08-B3-02 条件2)
        if rise <= sigma:
            return []
        score = length / float(min_increases)
        floors = [buckets[j][2] for j in range(i0, i1 + 1)]
        return [Detection(
            series_id=series.series_id, source=series.source,
            metric=series.metric, algorithm=self.id,
            start_ts=buckets[i0][1], end_ts=buckets[i1][1],
            values=sample_values(floors),
            score=score, severity_hint=score_to_hint(score),
            detail={
                "run_length": length, "min_increases": min_increases,
                "floor_start": buckets[i0][2], "floor_end": buckets[i1][2],
                "total_rise": rise, "effective_sigma": sigma,
                "window_minutes": window_minutes,
            },
            shape=SHAPE_FLOOR_RISE,
        )]


def _bucket_min(points, window_minutes):
    """(バケット番号, 開始時刻, 最小値) のリストを返す。非重複バケット。"""
    width = window_minutes * 60
    base_epoch = points[0][0].timestamp()
    out = []
    current_key = None
    current_ts = None
    current_min = None
    for ts, value in points:
        key = int((ts.timestamp() - base_epoch) // width)
        if current_key is None:
            current_key, current_ts, current_min = key, ts, value
        elif key != current_key:
            out.append((current_key, current_ts, current_min))
            current_key, current_ts, current_min = key, ts, value
        else:
            if value < current_min:
                current_min = value
    if current_key is not None:
        out.append((current_key, current_ts, current_min))
    return out
