"""検知器の共通契約と、分散の下限 (P003 7.1〜7.3 / ADR-003, ADR-008)。

全アルゴリズムは Detector を継承し、
`run(...) -> Tuple[List[Detection], List[SkipInfo]]` を実装する。
"""

import abc
import math
import threading
from typing import Any, Dict, List, Optional, Tuple

# --- shape の許容値 (この 7 種以外を使ってはならない) ----------------------
SHAPE_SPIKE_UP = "spike_up"
SHAPE_SPIKE_DOWN = "spike_down"
SHAPE_SUSTAINED = "sustained"
SHAPE_FLOOR_RISE = "floor_rise"
SHAPE_TREND_UP = "trend_up"
SHAPE_LEVEL_SHIFT = "level_shift"
SHAPE_SEASONAL_DEV = "seasonal_dev"

ALL_SHAPES = (
    SHAPE_SPIKE_UP, SHAPE_SPIKE_DOWN, SHAPE_SUSTAINED, SHAPE_FLOOR_RISE,
    SHAPE_TREND_UP, SHAPE_LEVEL_SHIFT, SHAPE_SEASONAL_DEV,
)

#: 統合時の優先順位 (上ほど優先。DS-09-03)。観点2 の形状を観点1 より優先する。
SHAPE_PRIORITY = [
    SHAPE_FLOOR_RISE, SHAPE_LEVEL_SHIFT, SHAPE_TREND_UP, SHAPE_SUSTAINED,
    SHAPE_SEASONAL_DEV, SHAPE_SPIKE_UP, SHAPE_SPIKE_DOWN,
]

# --- 窓内に必要な最小点数 ------------------------------------------------
# FR-053 と `common.min_points` は「**系列**の点数が必要最小点数に満たない場合は
# スキップする」という系列レベルの規定である。これを窓内の点数へそのまま適用すると、
# 既定値の組み合わせ (ALG-A1.window_minutes=60、サンプリング間隔 5 分 = 窓内 12 点、
# common.min_points=30) では **窓内の点数が 30 に到達しえず、A1/A2 が永久に
# 何も検知しない**。したがって窓内の最小点数は別に定め、平均・標準偏差が意味を
# 持つ下限とする。
WINDOW_MIN_POINTS = 5

# --- スキップ理由 --------------------------------------------------------
SKIP_FEW_POINTS = "点数不足"
SKIP_WIDE_DISTRIBUTION = "分布が広く適用不可"
SKIP_NO_BASELINE = "基準期間の不足"


class Detection(object):
    """1 つの検知点 (FR-051)。"""

    def __init__(self, series_id, source, metric, algorithm, start_ts, end_ts,
                 values, score, severity_hint, detail, shape):
        self.series_id = series_id
        self.source = source
        self.metric = metric
        self.algorithm = algorithm
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.values = values
        self.score = score
        self.severity_hint = severity_hint
        self.detail = detail
        self.shape = shape

    def __repr__(self):
        return "Detection({0} {1} {2} score={3:.2f} {4})".format(
            self.series_id, self.metric, self.algorithm, self.score, self.shape
        )


class SkipInfo(object):
    """検知できなかった理由 (ADR-008)。report.md の 4.2/4.3 節へ運ぶ。"""

    def __init__(self, series_id, metric, algorithm, reason):
        self.series_id = series_id
        self.metric = metric
        self.algorithm = algorithm
        self.reason = reason

    def __repr__(self):
        return "SkipInfo({0} {1} {2} {3})".format(
            self.series_id, self.metric, self.algorithm, self.reason
        )


class Detector(abc.ABC):
    """検知器の共通インタフェース。"""

    id = ""
    name = ""
    aspect = ""

    def min_points(self, cfg) -> int:
        return int(cfg.param("common.min_points"))

    @abc.abstractmethod
    def run(self, con, cfg, series, progress) -> Tuple[List[Detection], List[SkipInfo]]:
        """検知点とスキップ情報を返す。"""

    # --- 実装の共通ヘルパ -------------------------------------------------
    def skip(self, series, reason):
        return SkipInfo(series.series_id, series.metric, self.id, reason)


def effective_sigma(sd, mu, cfg) -> float:
    """実効的なばらつき (ADR-003)。ゼロ除算と微小分散での誤検知を防ぐ。"""
    if sd is None or (isinstance(sd, float) and math.isnan(sd)):
        sd = 0.0
    mu = 0.0 if mu is None else mu
    floor_ratio = float(cfg.param("common.sigma_floor_ratio"))
    floor_abs = float(cfg.param("common.sigma_floor_abs"))
    value = max(float(sd), floor_ratio * abs(float(mu)), floor_abs)
    return max(value, 1e-9)


def score_to_hint(score: float) -> str:
    """score から severity_hint を決める (DS-08-05)。"""
    return "FATAL" if score >= 2.0 else "WARN"


def interpolate_score(value, warn_threshold, fatal_threshold) -> float:
    """WARN 閾値で 1.0、FATAL 閾値で 2.0 になる線形補間 (DS-08-A1-01)。"""
    if value < warn_threshold:
        return 0.0
    span = fatal_threshold - warn_threshold
    if span <= 0:
        return 1.0
    return 1.0 + (value - warn_threshold) / span


#: 系列データのキャッシュ (※CR-006)。
#
# **同じ系列を検知器の数だけ読み直していた。** 実測 (30 系列 / 11 検知器) では
# S6 の 52% が fetch_series で、330 回の呼び出しのうち 300 回が同じ内容の
# 読み直しであった。系列ごとに 1 回読めば 10/11 が消える。
#
# **スレッドローカルにするのは意図的である** (※CR-007)。S6 は系列ごとに
# スレッドで並列実行されるため、辞書を共有するとスレッド間の競合が生じる。
# 1 スレッドが担当するのは 1 系列であり、その系列を全検知器が使い終われば
# 破棄してよい。したがって共有する利点が無い。
_cache = threading.local()


def begin_series(series):
    """1 系列の処理を始める (※CR-006)。キャッシュを差し替える。"""
    _cache.key = (series.series_id, series.metric)
    _cache.points = None
    _cache.segments = None


def end_series():
    """1 系列の処理を終える。キャッシュを解放する。"""
    _cache.key = None
    _cache.points = None
    _cache.segments = None


def fetch_series(con, series) -> List[tuple]:
    """(ts, value, segment) を ts 昇順で返す。

    同一系列の 2 回目以降はキャッシュを返す (※CR-006)。
    `begin_series` を呼んでいない場合は毎回問い合わせる (従来どおり)。
    """
    key = (series.series_id, series.metric)
    if getattr(_cache, "key", None) == key:
        cached = getattr(_cache, "points", None)
        if cached is not None:
            return cached
    rows = con.execute(
        "SELECT ts, value, segment FROM metrics "
        "WHERE series_id = ? AND metric = ? ORDER BY segment, ts",
        [series.series_id, series.metric],
    ).fetchall()
    if getattr(_cache, "key", None) == key:
        _cache.points = rows
    return rows


def fetch_segments(con, series) -> List[List[tuple]]:
    """`fetch_series` の結果を segment ごとに分けて返す (※CR-006)。

    **分割の結果もキャッシュする。** 系列あたり 11 個の検知器が同じ分割を
    作り直しており、30 日規模で計 1,092 回・約 1.7 秒を費やしていた。
    """
    points = fetch_series(con, series)
    key = (series.series_id, series.metric)
    if getattr(_cache, "key", None) == key:
        cached = getattr(_cache, "segments", None)
        if cached is not None:
            return cached
    groups = group_by_segment(points)
    if getattr(_cache, "key", None) == key:
        _cache.segments = groups
    return groups


def group_by_segment(points) -> List[List[tuple]]:
    """(ts, value, segment) のリストを segment ごとに分ける。"""
    groups = []
    current = []
    current_seg = None
    for ts, value, segment in points:
        if current_seg is None or segment != current_seg:
            if current:
                groups.append(current)
            current = []
            current_seg = segment
        current.append((ts, value))
    if current:
        groups.append(current)
    return groups


def take_values_with_context(points, i_start, i_end, context=2) -> List[float]:
    """検知区間の値に前後 context 点を含めて返す (DS-08-A1-02)。"""
    lo = max(0, i_start - context)
    hi = min(len(points), i_end + 1 + context)
    return [p[1] for p in points[lo:hi]]


def sample_values(values, count=9) -> List[float]:
    """値の並びを等間隔に count 点へ間引く (report.md の省略規則に合わせる)。"""
    if len(values) <= count:
        return list(values)
    step = (len(values) - 1) / float(count - 1)
    return [values[int(round(i * step))] for i in range(count)]


#: 季節差分のラグ(秒)。日次の周期を打ち消すため 24 時間とする。
SEASONAL_LAG_SEC = 24 * 3600
#: ラグの一致とみなす許容誤差(秒)。採取間隔の揺れを吸収する。
LAG_TOLERANCE_SEC = 150


def seasonal_diffs(points):
    """(index, x_t - x_{t-24h}) のリストを返す。対応点が無い点は含めない。

    日次の周期を持つ運用ログでは、周期成分は前日と打ち消し合って消え、
    水準シフトと傾向だけが差分に残る。時系列解析で標準的な前処理である。
    """
    out = []
    lag = SEASONAL_LAG_SEC
    left = 0
    n = len(points)
    # **epoch を先に一括で作る** (※CR-006)。従来は内側のループで
    # `datetime.timestamp()` を毎回呼んでおり、30 日規模で 1,600 万回・
    # 約 3.3 秒を費やしていた。値は同じであり結果は変わらない。
    epochs = [p[0].timestamp() for p in points]
    values = [p[1] for p in points]
    for i in range(n):
        target = epochs[i] - lag
        while left + 1 < n and epochs[left + 1] <= target:
            left += 1
        if left >= i:
            continue
        gap = abs(epochs[left] - target)
        pick = left
        if left + 1 < i:
            gap_next = abs(epochs[left + 1] - target)
            if gap_next < gap:
                pick = left + 1
                gap = gap_next
        if gap > LAG_TOLERANCE_SEC:
            continue
        out.append((i, values[i] - values[pick]))
    return out


def apply_persistence_rule(detections, interval_sec, factor=2.0):
    """観点1 の検知点に管理図の持続性規則を適用する。

    単発の 2σ 超えは信号として扱わない。管理図の標準的な判定規則
    (Western Electric rules) の考え方にもとづき、次のいずれかを満たす点のみ残す。

    * `score >= 2.0` (= FATAL 閾値相当。仕様上 k_fatal に対応) の単発点
    * 連続する 2 点以上が同時に閾値を超えている (持続している)

    4,000 点の系列に 2σ の判定を素朴に当てると、正規分布のノイズだけでも
    統計的に約 180 点が閾値を超える。単発の 2σ 超えを 1 件ずつ報告すると、
    レポートが偶然のゆらぎで埋まり、本来の異常が埋もれる。
    """
    if not detections:
        return []
    gap = max(float(interval_sec or 0.0) * factor, 1.0)
    ordered = sorted(detections, key=lambda d: (d.start_ts, d.end_ts))
    kept = []
    run = [ordered[0]]
    for det in ordered[1:]:
        if (det.start_ts - run[-1].end_ts).total_seconds() <= gap:
            run.append(det)
        else:
            kept.extend(_filter_run(run))
            run = [det]
    kept.extend(_filter_run(run))
    return kept


def _filter_run(run):
    if len(run) >= 2:
        return run
    return [d for d in run if d.score >= 2.0]


def merge_adjacent(detections, interval_sec, factor=2.0) -> List[Detection]:
    """同一系列・同一アルゴリズムの近接する検知点を区間へまとめる (DS-08-A1-02)。"""
    if not detections:
        return []
    gap = max(float(interval_sec or 0.0) * factor, 1.0)
    ordered = sorted(detections, key=lambda d: (d.start_ts, d.end_ts))
    merged = []
    group = [ordered[0]]
    for det in ordered[1:]:
        prev_end = group[-1].end_ts
        if (det.start_ts - prev_end).total_seconds() <= gap:
            group.append(det)
        else:
            merged.append(_collapse(group))
            group = [det]
    merged.append(_collapse(group))
    return merged


def _collapse(group) -> Detection:
    if len(group) == 1:
        return group[0]
    head = group[0]
    best = max(group, key=lambda d: d.score)
    values = []
    for det in group:
        for v in det.values:
            if not values or values[-1] != v:
                values.append(v)
    shape = head.shape
    if len(group) >= 3:
        shape = SHAPE_SUSTAINED
    detail = dict(head.detail)
    detail["merged_points"] = len(group)
    return Detection(
        series_id=head.series_id, source=head.source, metric=head.metric,
        algorithm=head.algorithm,
        start_ts=group[0].start_ts, end_ts=group[-1].end_ts,
        values=values, score=best.score,
        severity_hint=score_to_hint(best.score),
        detail=detail, shape=shape,
    )


def noise_scale(points):
    """系列のノイズ規模を、隣接点の差分から頑健に推定する。

    系列全体の標準偏差は、増加傾向そのものによって膨らむ。「微小な差を除く」
    ための下限にそれを使うと、傾向が強い系列ほど下限が高くなり、検知したい
    傾向自体を弾いてしまう。隣接点の差分の MAD は傾向の影響をほとんど受けない。
    """
    if len(points) < 3:
        return 0.0
    diffs = [abs(points[i][1] - points[i - 1][1]) for i in range(1, len(points))]
    diffs.sort()
    mid = len(diffs) // 2
    if len(diffs) % 2:
        mad = diffs[mid]
    else:
        mad = (diffs[mid - 1] + diffs[mid]) / 2.0
    # 差分の標準偏差は元系列の sqrt(2) 倍になる
    return mad * 1.4826 / math.sqrt(2.0)


def mean_and_sd(values) -> Tuple[float, float]:
    """平均と標本標準偏差を返す。要素が 1 個以下なら sd は 0。"""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mu = sum(values) / float(n)
    if n < 2:
        return mu, 0.0
    var = sum((v - mu) ** 2 for v in values) / float(n - 1)
    return mu, math.sqrt(max(var, 0.0))
