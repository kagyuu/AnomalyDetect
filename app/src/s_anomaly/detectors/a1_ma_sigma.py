"""ALG-A1 移動平均乖離率 (P003 7.4)。

P000 が例示した「移動平均乖離率 (2σ、3σ)」。直前 N 分の移動平均からの乖離
(残差)を求め、その **残差の分布の σ** で正規化する。

**窓の平均は Python で求める。SQL(窓関数)へは移していない** (※CR-006)。
`RANGE BETWEEN to_microseconds(n) PRECEDING AND CURRENT ROW` を使う SQL 版を
実装し、**出力が実行日時の行を除いて完全一致することまで確認したうえで差し戻した。**
30 日規模で 4.6 秒 → 8.9 秒と約 1.9 倍遅くなったためである。
原因は**問い合わせの粒度**にある。1 系列 8,640 点に対して 156 回問い合わせると、
DuckDB の 1 クエリあたりの固定費が窓計算そのものより大きい
(実測: 系列ごと 7.59 秒 / 全系列を 1 クエリ 0.88 秒 / 純 Python 1.54 秒)。
詳細と今後の方針は `docs/P903-cr-records/CR-006.md` にある。

**なぜ窓内の σ ではなく残差の σ を使うか**: 移動平均は系列の傾き・周期に
対して系統的に遅れるため、日次の周期を持つ運用ログでは残差に決定的な成分が
乗る。窓内の値の σ で割ると、この決定的な成分が「異常」として毎日繰り返し
検出される(実測で検知率 15%)。残差そのものの σ で正規化すると、この系統的
なずれが基準に織り込まれ、本当に外れた点だけが残る(同 0.1%@3σ)。
「移動平均乖離率が 2σ/3σ」という P000 の表現にも、こちらのほうが忠実である。
"""

import statistics

from .base import (
    SHAPE_SPIKE_DOWN, SHAPE_SPIKE_UP, SKIP_FEW_POINTS, WINDOW_MIN_POINTS,
    Detection, Detector, apply_persistence_rule, effective_sigma, fetch_series,
    fetch_segments, interpolate_score, merge_adjacent, score_to_hint,
    take_values_with_context,
)


class A1MovingAverage(Detector):
    id = "ALG-A1"
    name = "移動平均乖離率"
    aspect = "観点1"

    def run(self, con, cfg, series, progress):
        window_sec = float(cfg.param("ALG-A1.window_minutes")) * 60.0
        k_warn = float(cfg.param("ALG-A1.k_warn"))
        k_fatal = float(cfg.param("ALG-A1.k_fatal"))
        min_points = self.min_points(cfg)

        points = fetch_series(con, series)
        if len(points) < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        segments = fetch_segments(con, series)

        # 1 パス目: 全 segment の残差を集めて、残差の σ を求める
        residuals = []
        per_segment = []
        for segment in segments:
            res = _residuals(segment, window_sec)
            per_segment.append((segment, res))
            residuals.extend(r[2] for r in res)
        if len(residuals) < WINDOW_MIN_POINTS:
            return [], [self.skip(series, SKIP_FEW_POINTS)]

        mu_res = sum(residuals) / float(len(residuals))
        sd_res = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
        level = sum(p[1] for p in points) / float(len(points))
        sigma = effective_sigma(sd_res, level, cfg)

        # 2 パス目: 判定
        detections = []
        for segment, res in per_segment:
            for i, mu_window, residual in res:
                dev = abs(residual - mu_res) / sigma
                if dev < k_warn:
                    continue
                ts, value = segment[i]
                score = interpolate_score(dev, k_warn, k_fatal)
                detections.append(Detection(
                    series_id=series.series_id, source=series.source,
                    metric=series.metric, algorithm=self.id,
                    start_ts=ts, end_ts=ts,
                    values=take_values_with_context(segment, i, i),
                    score=score, severity_hint=score_to_hint(score),
                    detail={
                        "mu": mu_window, "residual": residual,
                        "residual_sigma": sigma, "dev": dev,
                        "k_warn": k_warn, "k_fatal": k_fatal, "value": value,
                    },
                    shape=SHAPE_SPIKE_UP if residual > mu_res else SHAPE_SPIKE_DOWN,
                ))

        detections = apply_persistence_rule(detections, series.interval_sec)
        if not detections:
            return [], []
        return merge_adjacent(detections, series.interval_sec), []


def _residuals(points, window_sec):
    """(index, 窓の平均, 残差) のリストを返す。窓は現在点を含まない。"""
    out = []
    left = 0
    running = 0.0
    for i in range(len(points)):
        ts, value = points[i]
        while left < i and (ts - points[left][0]).total_seconds() > window_sec:
            running -= points[left][1]
            left += 1
        n = i - left
        if n >= WINDOW_MIN_POINTS:
            mu = running / n
            out.append((i, mu, value - mu))
        running += value
    return out
