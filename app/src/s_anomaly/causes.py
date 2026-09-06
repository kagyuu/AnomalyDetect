"""M10 causes — 原因候補のルールベース判定 (P003 9章 / ADR-007)。

本アプリは閉域環境で動くため LLM を呼べない。原因候補は
「メトリクス × 形状 × 相関状況」の組み合わせに対する 9 個のルールから生成する。
確度は高/中/低の 3 段階とし、統計的な裏付けのない百分率は用いない。

**同時に発生したアノマリー (M11) が入力である。** co_anomaly より先に実行してはならない
(ADR-010)。
"""

from typing import List

STEP = "S7"

CONFIDENCE_HIGH = "高"
CONFIDENCE_MEDIUM = "中"
CONFIDENCE_LOW = "低"

_CONFIDENCE_RANK = {CONFIDENCE_HIGH: 0, CONFIDENCE_MEDIUM: 1, CONFIDENCE_LOW: 2}

TREND_SHAPES = ("trend_up", "floor_rise")


class Cause(object):
    """1 件の原因候補。"""

    def __init__(self, rule_id, cause, confidence, reason):
        self.rule_id = rule_id
        self.cause = cause
        self.confidence = confidence
        self.reason = reason

    def __repr__(self):
        return "Cause({0} {1} {2})".format(self.rule_id, self.cause, self.confidence)


class CauseRule(object):
    def __init__(self, rule_id, cause, confidence, match, reason):
        self.id = rule_id
        self.cause = cause
        self.confidence = confidence
        self.match = match
        self.reason = reason


# --- 同時アノマリーの照会ヘルパ (CR-002 / DS-10-06) -----------------------
#
# **旧実装は相関の統計値 (他系列の平常時との比較) を見ていた。**
# CR-002 で相関を廃止したため、「同時刻にその系列のアノマリーが検知されて
# いるか」を見る形へ読み替える。読み替えの対応は P003 DS-10-06 に定める。
#
# ★FIXME★ **この読み替えは条件の意味を保存しない。**
# 旧「有意な変化なし」は「見たが平常だった」を意味したが、新しい「イベントが
# 無い」は「異常として検知されなかった」を意味する。**後者のほうが緩い。**
# とくに CR-01 / CR-04 は「変化が無いこと」を条件にしており成立しやすくなる。
# 実データでの妥当性は要確認である。

#: 「上方向のイベント」とみなす shape (DS-10-06)。肯定条件に使う。
_UPWARD_SHAPES = ("spike_up", "trend_up", "floor_rise", "level_shift")

#: 「持続的に上昇している」とみなす shape。**否定条件に使う。**
#
# 旧実装の「有意な変化なし」を読み替えるとき、**スパイクや水準シフトまで
# 「変化あり」とみなすと条件が厳しくなりすぎる**。CR-01 (メモリリーク) の
# 「eu に変化なし」は「若い世代も一緒に増え続けてはいない = 単なる負荷増では
# ない」という意図であり、eu の単発スパイクはこの判断を覆さない。
#
# 実測: 14 日間の ou/floor_rise イベントには、期間中のあらゆるイベントが
# 区間として重なる。level_shift まで阻害要因に数えると **CR-01 が構造的に
# 発火しなくなる** (P903 の実装時に確認)。
_RISING_SHAPES = ("trend_up", "floor_rise")


def _co(event, metric):
    """同一ホストで区間が重なる、指定メトリクスのイベントを返す (DS-10-06)。

    **参照するのは `concurrent_by_metric` であり、レポートに出す
    `co_anomalies` ではない。** 原因候補ルールは観点2 のイベントを主体と
    するものが多く (CR-01 のメモリリークなど)、観点1 に限った `co_anomalies`
    を入力にすると構造的に発火しなくなるためである (P003 DS-10-06)。
    """
    return (getattr(event, "concurrent_by_metric", None) or {}).get(metric)


def _increased(event, metric):
    """同時刻にそのメトリクスの「上方向の」イベントがあるか (DS-10-06)。"""
    c = _co(event, metric)
    if c is None:
        return False
    return getattr(c, "shape", None) in _UPWARD_SHAPES


def _flat(event, metric):
    """同時刻にそのメトリクスの「持続的な上昇」が無いか (DS-10-06)。

    旧実装の「横ばい (有意な変化なし)」に対応する。**意味は同じではない**
    (上記 ★FIXME★ を参照)。判定に使うのは `_RISING_SHAPES` だけであり、
    単発のスパイクや水準シフトは「上昇している」とみなさない。
    """
    if getattr(event, "concurrent_by_metric", None) is None:
        return False
    c = _co(event, metric)
    if c is None:
        return True
    return getattr(c, "shape", None) not in _RISING_SHAPES


def _any_significant(event):
    """同時刻に他のアノマリーが 1 件でもあるか (DS-10-06 の CR-07 用)。

    CR-07 (一時的な負荷スパイク) は観点1 のイベントにのみ当たるルールである
    ため、**レポートに出す co_anomalies を見る**のが素直である。
    """
    return bool(event.co_anomalies)


def _avg_above(event, metric, threshold):
    """同時刻にそのメトリクスの SEVERE なイベントがあるか (DS-10-06)。

    旧実装の「平均が threshold を超えて高止まり」に対応する。SEVERE の判定条件
    そのものが「使用率が severe_pct を超えていること」を含む (P001 11章) ため、
    脅威度で代替できる。引数 threshold は互換のために残す。
    """
    c = _co(event, metric)
    if c is None:
        return False
    return c.severity == "SEVERE"


# --- 9 ルール (DS-10-03) --------------------------------------------------
def build_rules(cfg) -> List[CauseRule]:
    severe_pct = float(cfg.param("events.severe_pct"))
    return [
        CauseRule(
            "CR-01", "メモリリーク", CONFIDENCE_HIGH,
            lambda e: (e.metric == "ou" and e.shape == "floor_rise"
                       and _increased(e, "fgc_delta") and _flat(e, "eu")),
            "ou の下限が上がり続け、同時刻に fgc_delta の増加があり、eu のアノマリーが無いため",
        ),
        CauseRule(
            "CR-02", "負荷増に伴う正常な増加", CONFIDENCE_MEDIUM,
            lambda e: (e.metric in ("ou", "eu") and e.shape in TREND_SHAPES
                       and (_increased(e, "active_connections")
                            or _increased(e, "run"))),
            "同時刻に負荷指標(active_connections または run)のアノマリーも出ているため",
        ),
        CauseRule(
            "CR-03", "コネクションリーク(クローズ漏れ)", CONFIDENCE_HIGH,
            lambda e: e.source == "db_connection" and e.shape == "floor_rise",
            "コネクション数の下限が上がり続け、回復していないため",
        ),
        CauseRule(
            "CR-04", "計算資源の枯渇、またはジョブのスタック", CONFIDENCE_HIGH,
            lambda e: (e.metric == "pend" and e.shape in TREND_SHAPES
                       and _flat(e, "run")),
            "待機ジョブが増え続ける一方、同時刻に run のアノマリーが無いため",
        ),
        CauseRule(
            "CR-05", "クラスローダリーク(動的クラス生成)", CONFIDENCE_MEDIUM,
            lambda e: e.metric == "mu" and e.shape in TREND_SHAPES,
            "Metaspace の使用量が増え続けているため",
        ),
        CauseRule(
            "CR-06", "ヒープ不足による Full GC 頻発", CONFIDENCE_HIGH,
            lambda e: (e.metric in ("fgct_delta", "fgc_delta")
                       and _avg_above(e, "ou_pct", severe_pct)),
            "Full GC が増える一方、同時刻に Old 世代使用率の SEVERE なアノマリーが"
            "出ているため(閾値 {0}%)".format(severe_pct),
        ),
        CauseRule(
            "CR-07", "一時的な負荷スパイク、または採取タイミングの揺れ",
            CONFIDENCE_LOW,
            lambda e: (e.shape in ("spike_up", "spike_down")
                       and not _any_significant(e)),
            "単発の逸脱であり、同時刻に他のアノマリーが検知されていないため",
        ),
        CauseRule(
            "CR-08", "設定変更・負荷段階の変化", CONFIDENCE_MEDIUM,
            lambda e: e.shape == "level_shift",
            "ある時刻を境に水準が移り、元に戻っていないため",
        ),
        CauseRule(
            "CR-09", "定期的な処理による周期的な負荷", CONFIDENCE_LOW,
            lambda e: e.shape == "seasonal_dev",
            "同じ曜日・時刻帯の平常値から外れているため",
        ),
    ]


def assign_causes(events, cfg):
    """各イベントの causes を埋める。相関情報が埋まっている前提 (ADR-010)。"""
    rules = build_rules(cfg)
    for event in events:
        found = []
        for rule in rules:
            try:
                hit = rule.match(event)
            except Exception:
                hit = False
            if hit:
                found.append(Cause(rule.id, rule.cause, rule.confidence,
                                   rule.reason))
        found.sort(key=lambda c: (_CONFIDENCE_RANK[c.confidence], c.rule_id))
        event.causes = found
