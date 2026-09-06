"""ALG-B4 CUSUM 累積和管理図 (P003 7.12)。

基準からの偏差を累積し、閾値超過で**水準シフト**を検知する。
リーク(徐々に増える)と、設定変更・負荷段階の変化(階段状に上がる)を
切り分けるために置いている。

**累積の対象は生値ではなく、前日同時刻との差分**(季節差分 `d_t = x_t − x_{t−24h}`)
とする。CUSUM も EWMA と同じく定常過程を前提としており、日次の周期を持つ系列に
生値を当てると、周期の立ち上がりで毎日累積が閾値を超える(実測で 1 系列あたり
数百件)。季節差分なら周期は前日と打ち消し合い、水準シフトと傾向だけが残る。

ALG-A4(EWMA)と入力は同じだが、統計量が異なる。EWMA は直近を重く見る指数
加重平均、CUSUM は偏差の累積和であり、小さな持続的シフトの検出が早い。
両者は管理図における小シフト検出の代表的な 2 手法である。

**シフト前後の水準を求める窓平均は、時刻昇順であることを利用して二分探索で
範囲を絞る (ADR-013)。** 検知のたびに系列全体を走査すると、計算量が
O(検知件数 x 系列長) になり、長期の系列で実行時間を支配する。
"""

import bisect

from .base import (
    SHAPE_LEVEL_SHIFT, SKIP_FEW_POINTS, SKIP_NO_BASELINE,
    Detection, Detector, effective_sigma, fetch_series, fetch_segments,
    mean_and_sd, sample_values, score_to_hint, seasonal_diffs,
)

#: CUSUM の slack。標準的な値である 0.5σ を用いる (DS-08-B4-01)。
SLACK_K = 0.5


class B4Cusum(Detector):
    id = "ALG-B4"
    name = "CUSUM"
    aspect = "観点2"

    def run(self, con, cfg, series, progress):
        baseline_sec = float(cfg.param("ALG-B4.baseline_minutes")) * 60.0
        h = float(cfg.param("ALG-B4.h"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        detections = []
        skips = []
        for segment in fetch_segments(con, series):
            found, skipped = self._scan(
                series, segment, baseline_sec, h, min_points, cfg
            )
            detections.extend(found)
            skips.extend(skipped)
        return detections, skips

    def _scan(self, series, points, baseline_sec, h, min_points, cfg):
        # 先頭からの経過秒。窓の境界を二分探索するために系列あたり 1 度だけ作る。
        # timedelta の減算と同じ値になるよう、timestamp() ではなく先頭との差で持つ
        # (timestamp() は夏時間の切り替えをまたぐと壁時計の差と一致しない)。
        origin = points[0][0]
        offsets = [(p[0] - origin).total_seconds() for p in points]

        diffs = seasonal_diffs(points)
        if len(diffs) < min_points:
            # 24 時間ぶんの履歴が無い
            return [], [self.skip(series, SKIP_NO_BASELINE)]

        start_ts = points[diffs[0][0]][0]
        baseline = [d for i, d in diffs
                    if (points[i][0] - start_ts).total_seconds() < baseline_sec]
        if len(baseline) < min_points:
            baseline = [d for _i, d in diffs]

        mu, sd = mean_and_sd(baseline)
        level = sum(p[1] for p in points) / float(len(points))
        sigma = effective_sigma(sd, level, cfg)
        limit = h * sigma
        slack = SLACK_K * sigma

        out = []
        s_plus = 0.0
        rise_start = None
        for pos, (i, diff) in enumerate(diffs):
            prev = s_plus
            s_plus = max(0.0, s_plus + (diff - mu - slack))
            if prev <= 0.0 and s_plus > 0.0:
                rise_start = i
            if s_plus > limit:
                i0 = rise_start if rise_start is not None else i
                shifted = _mean_after(points, offsets, i, baseline_sec)
                score = s_plus / limit if limit > 0 else 1.0
                out.append(Detection(
                    series_id=series.series_id, source=series.source,
                    metric=series.metric, algorithm=self.id,
                    start_ts=points[i0][0], end_ts=points[i][0],
                    values=sample_values([points[j][1] for j in range(i0, i + 1)]),
                    score=score, severity_hint=score_to_hint(score),
                    detail={
                        "baseline_mu_diff": mu, "baseline_sigma": sigma,
                        "h": h, "k": SLACK_K, "s_plus": s_plus,
                        "level_before": _mean_before(
                            points, offsets, i0, baseline_sec),
                        "level_after": shifted,
                    },
                    shape=SHAPE_LEVEL_SHIFT,
                ))
                # 信号が出たら累積を戻す。水準が移ったままの系列で
                # 同じ 1 つのシフトが繰り返し検知されるのを防ぐ。
                s_plus = 0.0
                rise_start = None
        return out, []


def _mean_after(points, offsets, index, window_sec):
    """検知後 window_sec 分の平均を返す (シフト後の水準)。

    `offsets` は先頭からの経過秒 (昇順)。窓の右端を二分探索で求め、
    **その範囲だけを走査する**。系列全体を走査してはならない (ADR-013)。
    """
    hi = bisect.bisect_right(offsets, offsets[index] + window_sec)
    if hi <= index:
        return None
    return sum(points[j][1] for j in range(index, hi)) / float(hi - index)


def _mean_before(points, offsets, index, window_sec):
    """検知前 window_sec 分の平均を返す (シフト前の水準)。

    窓の左端を二分探索で求め、**その範囲だけを走査する** (ADR-013)。
    """
    lo = bisect.bisect_left(offsets, offsets[index] - window_sec)
    if lo > index:
        return None
    return sum(points[j][1] for j in range(lo, index + 1)) / float(index + 1 - lo)
