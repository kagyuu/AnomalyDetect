"""M11 co_anomaly — 同時に発生したアノマリーの突き合わせ (P003 10章)。

**旧 `correlate` を置き換えるモジュールである (CR-002)。**

旧実装は `metrics` テーブル (30 日規模で 135 万行) を範囲結合し、他系列の
「平常時と比べてどうだったか」を集めていた。しかしその出力はレポートの 63.4% を
占めながら大半が「平常時と有意差なし」であり、処理時間も全体の 58% を占めていた。

本モジュールは **`metrics` を一切参照せず、検知済みイベント同士を突き合わせる**。
運用者が知りたいのは「DB コネクションの高止まりと利用メモリの高止まりが同時に
起きている」というアノマリー同士の同時発生だからである。

この結果は report の「同時に発生したアノマリー」欄になると同時に、
**原因候補ルール (M10) の入力**でもある。したがって causes より先に実行する
(ADR-010)。
"""

import heapq
from typing import List, Optional

STEP = "S8"

#: 1 イベントあたりに載せる相手の上限 (P002 UI-04-C04)。
#  旧 correlate.MAX_CORRELATIONS を名称のみ変えて引き継いだ (DS-11-08)。
MAX_CO_ANOMALIES = 10

#: 脅威度の強さ。並び順の第 2 キーに使う (DS-11-05)。
_SEVERITY_RANK = {"SEVERE": 4, "FATAL": 3, "WARN": 2, "INFO": 1}


class CoAnomaly(object):
    """同時に発生した 1 件の相手アノマリー。"""

    def __init__(self, event_id, host, source, metric, severity, shape,
                 overlap_seconds, same_host):
        self.event_id = event_id
        self.host = host
        self.source = source
        self.metric = metric
        self.severity = severity
        #: 相手の shape。原因候補ルールが「上方向か」を見る (DS-10-06)
        self.shape = shape
        self.overlap_seconds = overlap_seconds
        self.same_host = same_host

    def __repr__(self):
        return "CoAnomaly({0} {1}/{2} {3}s)".format(
            self.event_id, self.host, self.metric, self.overlap_seconds
        )


def is_point_anomaly(event) -> bool:
    """観点1 (瞬間的な外れ値) のイベントか (P002 UI-04-C03, DS-11-02)。

    **ALG-A* だけで構成されるイベントのみが真**である。観点2 (ALG-B*) を
    1 つでも含むものは、混在であっても偽とする。

    混在を含めると、観点2 の長い区間 (実測で最大 30 日) が持ち込まれ、
    解析期間の全体を覆うイベントが他の全件と「同時」になってしまう。
    実測では「相手が 1 件も無いイベント」が 0 件になり弁別力を失った。
    """
    algorithms = getattr(event, "algorithms", None)
    if not algorithms:
        return False
    return all(str(a).startswith("ALG-A") for a in algorithms)


def _overlap_seconds(a, b) -> float:
    """2 つのイベントの重複区間の長さ (秒)。重ならなければ負になる。"""
    start = a.start_ts if a.start_ts > b.start_ts else b.start_ts
    end = a.end_ts if a.end_ts < b.end_ts else b.end_ts
    return (end - start).total_seconds()


def _sweep(items, on_pair):
    """開始時刻順に走査し、区間が重なる組だけを on_pair へ渡す (DS-11-04)。

    全ペアの総当たりは O(n^2) になるため使わない。
    計算量は O(n log n + 重なりペア数) である。
    """
    ordered = sorted(items, key=lambda e: (e.start_ts, e.event_id))
    active = []  # (end_ts, 連番, event) の最小ヒープ
    for seq, event in enumerate(ordered):
        while active and active[0][0] < event.start_ts:
            heapq.heappop(active)
        for _end_ts, _seq, other in active:
            # ここに来る組は必ず区間が重なっている (DS-11-03)
            on_pair(event, other, _overlap_seconds(event, other))
        heapq.heappush(active, (event.end_ts, seq, event))


def collect(events):
    """各イベントに 2 つの結果を埋める (P003 10章)。

    **(1) `co_anomalies` — レポートに出す「同時に発生したアノマリー」**

    * 観点1 のイベント -> リスト (相手が無ければ空リスト)
    * 観点2 を含むイベント -> None (レポート側が項目行ごと省略する)

    **(2) `concurrent_by_metric` — 原因候補ルールの入力 (レポートには出さない)**

    同一ホストで区間が重なるイベントを、メトリクスごとに 1 件だけ持つ。
    **観点を問わない。** 観点2 のイベントにも設定する。

    (2) を (1) と分けるのは、**原因候補ルールが観点2 のイベントを主体とする**
    ためである。CR-01 (メモリリーク) は「`ou` が floor_rise (観点2) かつ
    同時刻に `fgc_delta` の増加がある」という条件であり、`fgc_delta` の増加
    自体も観点2 の傾向として検知される。(1) だけを入力にすると
    **CR-01 / CR-02 / CR-04 / CR-06 が構造的に発火しなくなる**。

    **DuckDB コネクションも設定も受け取らない。** 入力はイベント一覧だけである。
    """
    from .events import extract_host

    for event in events:
        event.co_anomalies = None
        event.concurrent_by_metric = {}

    if not events:
        return

    # --- (2) 原因候補ルール用: 同一ホストで重なるものをメトリクス別に 1 件 ---
    hosts = {id(e): extract_host(e.series_id) for e in events}

    def keep_same_host(a, b, _seconds):
        if hosts[id(a)] != hosts[id(b)]:
            return
        _remember(a, b)
        _remember(b, a)

    _sweep(events, keep_same_host)

    # --- (1) レポート用: 観点1 のイベントどうし ---
    targets = [e for e in events if is_point_anomaly(e)]
    if not targets:
        return
    for event in targets:
        event.co_anomalies = []

    def link_both(a, b, seconds):
        _link(a, b, seconds)
        _link(b, a, seconds)

    _sweep(targets, link_both)

    for event in targets:
        event.co_anomalies = _top(event, event.co_anomalies)


def _remember(event, other):
    """原因候補ルール用に、同一ホストの重なりをメトリクス別に 1 件だけ持つ。

    同じメトリクスが複数重なる場合は**脅威度の高いほうを残す**。同値なら
    イベント ID の昇順で決める (再現性。NFR-009)。
    """
    if other.metric == event.metric:
        return
    cur = event.concurrent_by_metric.get(other.metric)
    if cur is None:
        event.concurrent_by_metric[other.metric] = other
        return
    a = (_SEVERITY_RANK.get(other.severity or "INFO", 0), cur.event_id)
    b = (_SEVERITY_RANK.get(cur.severity or "INFO", 0), other.event_id)
    if a > b:
        event.concurrent_by_metric[other.metric] = other


def _link(event, other, seconds):
    """event の相手として other を記録する。"""
    from .events import extract_host

    host = extract_host(other.series_id)
    event.co_anomalies.append(CoAnomaly(
        event_id=other.event_id,
        host=host,
        source=other.source,
        metric=other.metric,
        severity=other.severity or "INFO",
        shape=getattr(other, "shape", None),
        overlap_seconds=seconds,
        same_host=(host == extract_host(event.series_id)),
    ))


def _top(event, candidates) -> List[CoAnomaly]:
    """並び順に従って上位 MAX_CO_ANOMALIES 件を採る (P002 UI-04-C04)。

    第 4 キーに event_id を置くのは必須である。上位 3 キーが同値のときの順序を
    決定的にし、NFR-009 (再現性) を満たすため。**浮動小数点の値だけで順序が
    決まる状態を作らない。**
    """
    def rank(item):
        return (
            0 if item.same_host else 1,                    # 1. 同一ホスト優先
            -_SEVERITY_RANK.get(item.severity, 0),         # 2. 脅威度の高い順
            -item.overlap_seconds,                         # 3. 重なりが長い順
            item.event_id,                                 # 4. 再現性のため
        )

    return sorted(candidates, key=rank)[:MAX_CO_ANOMALIES]


def cross_host_groups(events) -> List[dict]:
    """複数ホストにまたがる同時発生を、時間帯ごとにまとめる (P002 UI-04-F05)。

    `report_summary_{yyyymm}.md` の「4. 複数ホストで同時に発生したアノマリー」の
    材料になる。2 つ以上の異なるホストが関与する組だけを対象とする。
    """
    from .events import extract_host

    groups = {}
    for event in events:
        others = getattr(event, "co_anomalies", None)
        if not others:
            continue
        host = extract_host(event.series_id)
        outside = [o for o in others if not o.same_host]
        if not outside:
            continue
        key = event.start_ts.strftime("%Y-%m-%d %H:%M")
        g = groups.setdefault(key, {"when": key, "hosts": set(),
                                    "metrics": set(), "count": 0})
        g["hosts"].add(host)
        g["metrics"].add(event.metric)
        g["count"] += 1
        for o in outside:
            g["hosts"].add(o.host)
            g["metrics"].add(o.metric)

    rows = [g for g in groups.values() if len(g["hosts"]) >= 2]
    rows.sort(key=lambda g: (-g["count"], -len(g["hosts"]), g["when"]))
    return rows
