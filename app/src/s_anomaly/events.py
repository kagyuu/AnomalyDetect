"""M09 events — 検知点の統合と脅威度の判定 (P003 8章)。"""

import re
from typing import Dict, List

from .detectors.base import SHAPE_PRIORITY, SHAPE_SPIKE_DOWN, SHAPE_SPIKE_UP

STEP = "S7"

SEVERITY_ORDER = ["INFO", "WARN", "FATAL", "SEVERE"]

#: 観点2 の形状 (SEVERE 判定の前提)
TREND_SHAPES = ("floor_rise", "trend_up", "level_shift")

#: `ou` -> `ou_pct` の対応 (SEVERE 判定で使う)
RATIO_OF = {"ou": "ou_pct", "eu": "eu_pct", "mu": "mu_pct"}

#: 「末尾で最大値を更新中」で SEVERE を判定するメトリクス
TAIL_MAX_METRICS = {
    "lsf_queue": ("pend", "susp"),
    "db_connection": ("active_connections",),
}

#: 末尾とみなす区間の割合
TAIL_RATIO = 0.1

#: SEVERE の対象外とするメトリクス (DS-09-05)。
#
# 「SEVERE にしない(最大 FATAL)」は**上限の指定**であり、FR-062 の
# アルゴリズム数による引き上げでもこの上限を超えてはならない。
NEVER_SEVERE = {
    "jvm_gc": ("fgc_delta", "fgct_delta", "ygct_delta"),
    "lsf_queue": ("njobs", "run"),
}


def _never_severe(event):
    return event.metric in NEVER_SEVERE.get(event.source, ())


class Event(object):
    """レポートに載せる 1 件の異常イベント。"""

    def __init__(self, series_id, source, metric, algorithms, start_ts, end_ts,
                 values, score, shape, detail):
        self.event_id = ""
        self.series_id = series_id
        self.source = source
        self.metric = metric
        self.algorithms = algorithms
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.values = values
        self.score = score
        self.shape = shape
        self.detail = detail
        self.severity = ""
        self.causes = []
        #: 同時に発生したアノマリー (M11 co_anomaly が埋める。CR-002)。
        #  None = 観点2 を含むイベント (レポートは項目行ごと省略する)
        #  []   = 観点1 だが重なる相手が無い
        self.co_anomalies = None
        #: チャート (SVG) への相対パス (M15 charts が埋める。※CR-009)。
        #  None = チャートを付けなかったイベント
        self.chart_path = None
        #: 点数が足りずチャートにできなかったときの生の点列 (※CR-009)。
        #  None = 表を出さない / [] や短いリスト = 表として出す
        self.chart_points = None

    def __repr__(self):
        return "Event({0} {1} {2} {3} {4})".format(
            self.event_id, self.series_id, self.metric, self.severity, self.shape
        )


def merge_detections(detections, cfg) -> List[Event]:
    """検知点を (series_id, metric) 単位で統合し、イベントにする (DS-09-01)。"""
    gap_sec = float(cfg.param("events.merge_gap_minutes")) * 60.0
    groups = {}
    for det in detections:
        groups.setdefault((det.series_id, det.metric), []).append(det)

    events = []
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda d: (d.start_ts, d.end_ts, d.algorithm))
        cluster = [items[0]]
        for det in items[1:]:
            latest_end = max(d.end_ts for d in cluster)
            if (det.start_ts - latest_end).total_seconds() <= gap_sec:
                cluster.append(det)
            else:
                events.append(_build_event(cluster))
                cluster = [det]
        events.append(_build_event(cluster))

    assign_event_ids(events)
    return events


def _build_event(cluster) -> Event:
    head = cluster[0]
    algorithms = sorted(set(d.algorithm for d in cluster))
    start_ts = min(d.start_ts for d in cluster)
    end_ts = max(d.end_ts for d in cluster)
    score = max(d.score for d in cluster)
    shape = _pick_shape([d.shape for d in cluster])
    # values は最も長い区間を持つ検知点のもの。同点なら algorithm の ID 順
    longest = sorted(
        cluster,
        key=lambda d: (-(d.end_ts - d.start_ts).total_seconds(), d.algorithm),
    )[0]
    detail = {}
    for det in cluster:
        detail.setdefault(det.algorithm, det.detail)
    return Event(
        series_id=head.series_id, source=head.source, metric=head.metric,
        algorithms=algorithms, start_ts=start_ts, end_ts=end_ts,
        values=list(longest.values), score=score, shape=shape, detail=detail,
    )


def _pick_shape(shapes):
    for candidate in SHAPE_PRIORITY:
        if candidate in shapes:
            return candidate
    return shapes[0] if shapes else SHAPE_SPIKE_UP


#: series_id からホスト名を取り出すための書式 (P002 6.3 の 3 書式)。
_DBCONN_RE = re.compile(r"^db_connection/([^/:]+):\d+/.+$")
_JVMGC_RE = re.compile(r"^jvm_gc/[^@]+@(.+)$")
_LSF_RE = re.compile(r"^lsf_queue/([^/]+)/.+$")

#: どの書式にも一致しなかったときのホスト名 (DS-09-08)。
UNKNOWN_HOST = "unknown"


def extract_host(series_id) -> str:
    """series_id からホスト名を取り出す (DS-09-08)。

    イベント ID の採番 (DS-09-07) とレポートの分割先 (DS-12-05) の両方が使う。
    **どの書式にも一致しない場合も実行を止めず** UNKNOWN_HOST を返す (FR-072)。
    """
    for pattern in (_DBCONN_RE, _JVMGC_RE, _LSF_RE):
        m = pattern.match(series_id or "")
        if m:
            return m.group(1)
    return UNKNOWN_HOST


def assign_event_ids(events):
    """`EVT-{YYYYMMDD}-{host}-{NNN}` を採番する (DS-09-07)。

    ソートキーに series_id と metric を含めるのは、同一時刻のイベントの順序を
    決定的にするため (NFR-009)。
    **連番は「日 x ホスト」ごとに 1 から振る** (CR-003)。日単位の全ホスト
    通し番号にすると、ID からホストが分からず、ホスト別に分割したレポートの
    どのファイルのイベントかを判別できない。
    """
    ordered = sorted(events, key=lambda e: (e.start_ts, e.series_id, e.metric))
    counters = {}
    for event in ordered:
        day = event.start_ts.strftime("%Y%m%d")
        host = extract_host(event.series_id)
        key = (day, host)
        counters[key] = counters.get(key, 0) + 1
        event.event_id = "EVT-{0}-{1}-{2:03d}".format(day, host, counters[key])


def sort_for_report(events) -> List[Event]:
    """脅威度の降順、次いで開始時刻の昇順で並べる (FR-073)。"""
    def key(event):
        rank = SEVERITY_ORDER.index(event.severity) if event.severity in SEVERITY_ORDER else 0
        return (-rank, event.start_ts, event.event_id)

    return sorted(events, key=key)


# ---------------------------------------------------------------------------
# 脅威度の判定 (DS-09-04 / DS-09-05)
# ---------------------------------------------------------------------------
def assign_severity(events, con, cfg):
    """各イベントの severity を決める。"""
    severe_pct = float(cfg.param("events.severe_pct"))
    stats = _collect_stats(events, con)

    for event in events:
        if _is_severe(event, stats, severe_pct):
            severity = "SEVERE"
        elif event.score >= 2.0:
            severity = "FATAL"
        elif event.score >= 1.0:
            severity = "WARN"
        else:
            severity = "INFO"
        # 3 つ以上のアルゴリズムが一致したら 1 段階引き上げる (FR-062)
        if len(event.algorithms) >= 3:
            index = min(SEVERITY_ORDER.index(severity) + 1, len(SEVERITY_ORDER) - 1)
            severity = SEVERITY_ORDER[index]
        # DS-09-05 が「SEVERE にしない(最大 FATAL)」と定めたメトリクスは、
        # 引き上げによっても SEVERE にしない
        if severity == "SEVERE" and _never_severe(event):
            severity = "FATAL"
        event.severity = severity


def _collect_stats(events, con) -> Dict:
    """SEVERE 判定に必要な統計を 1 回のクエリでまとめて取得する (DS-11-05)。"""
    if not events:
        return {"pct_max": {}, "earlier_max": {}, "tail_max": {}}

    # 1) *_pct の期間最大
    pct_rows = []
    for event in events:
        ratio_metric = RATIO_OF.get(event.metric)
        if ratio_metric is None:
            continue
        pct_rows.append((event.event_id, event.series_id, ratio_metric,
                         event.start_ts, event.end_ts))
    pct_max = {}
    if pct_rows:
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _sev_windows"
            " (event_id VARCHAR, series_id VARCHAR, metric VARCHAR,"
            "  t_from TIMESTAMP, t_to TIMESTAMP)"
        )
        con.executemany(
            "INSERT INTO _sev_windows VALUES (?, ?, ?, ?, ?)", pct_rows
        )
        for event_id, value in con.execute(
            "SELECT w.event_id, max(m.value) FROM _sev_windows w"
            " JOIN metrics m ON m.series_id = w.series_id AND m.metric = w.metric"
            "   AND m.ts BETWEEN w.t_from AND w.t_to"
            " GROUP BY 1"
        ).fetchall():
            pct_max[event_id] = value

    # 2) 系列全体の最大値と、末尾 10% の最大値
    keys = sorted(set((e.series_id, e.metric) for e in events))
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _sev_series (series_id VARCHAR, metric VARCHAR)"
    )
    con.executemany("INSERT INTO _sev_series VALUES (?, ?)", keys)
    # earlier_max は「末尾を除く 90% の最大値」。tail_max と比べて
    # 「末尾で新しい最大値を更新しているか」を判定する。
    earlier_max = {}
    tail_max = {}
    for series_id, metric, emax, tmax in con.execute(
        """
        WITH bounds AS (
            SELECT m.series_id, m.metric,
                   min(m.ts) AS t0, max(m.ts) AS t1
            FROM metrics m JOIN _sev_series s
              ON s.series_id = m.series_id AND s.metric = m.metric
            GROUP BY 1, 2
        )
        SELECT b.series_id, b.metric,
               max(CASE WHEN m.ts <  b.t1 - (b.t1 - b.t0) * ? THEN m.value END),
               max(CASE WHEN m.ts >= b.t1 - (b.t1 - b.t0) * ? THEN m.value END)
        FROM bounds b JOIN metrics m
          ON m.series_id = b.series_id AND m.metric = b.metric
        GROUP BY 1, 2
        """,
        [TAIL_RATIO, TAIL_RATIO],
    ).fetchall():
        earlier_max[(series_id, metric)] = emax
        tail_max[(series_id, metric)] = tmax
    return {"pct_max": pct_max, "earlier_max": earlier_max, "tail_max": tail_max}


def _is_severe(event, stats, severe_pct) -> bool:
    """DS-09-05 の SEVERE 条件。観点2 の形状であることが前提。"""
    if event.shape not in TREND_SHAPES:
        return False

    if event.source == "jvm_gc":
        if event.metric in RATIO_OF:
            pct = stats["pct_max"].get(event.event_id)
            # 使用率が取得できない (-gc 形式で容量列が無い) 場合は SEVERE にしない
            return pct is not None and pct > severe_pct
        # GC 頻度・時間には上限の概念が無いため SEVERE にしない
        return False

    tail_metrics = TAIL_MAX_METRICS.get(event.source, ())
    if event.metric in tail_metrics:
        key = (event.series_id, event.metric)
        earlier = stats["earlier_max"].get(key)
        tail = stats["tail_max"].get(key)
        if earlier is None or tail is None:
            return False
        # **末尾で新しい最大値を更新している**ことを厳密に求める。
        # `>=` にすると、0〜2 のような小さな整数の系列では最大値が末尾にも
        # 現れるだけで SEVERE になり、平常な系列が SEVERE で埋まる。
        return tail > earlier
    return False
