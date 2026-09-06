"""M12 reporter — report.md の生成 (P002 4章 / P003 11章)。

report.md は後段の LLM が読む入力である。したがって**構造の安定性を最優先**とし、
検知件数が 0 件でも節構成を省略しない。値が得られない項目も行を省略せず、
定型文を書く (UI-04-02)。項目を省略すると、後段の LLM が「異常がない」と
誤読する。
"""

import os
from typing import List, Optional

from . import config
from .errors import ReportWriteError

STEP = "S9"

#: ※CR-001 により単一ファイルは廃止した。名前の接頭辞のみ残す。
REPORT_PREFIX = "report_"
TMP_SUFFIX = ".tmp"

#: メトリクスの和名 (report.md の「データ」欄で使う)
METRIC_LABELS = {
    "active_connections": "アクティブコネクション数",
    "ou": "Old 使用量",
    "eu": "Eden 使用量",
    "mu": "Metaspace 使用量",
    "ou_pct": "Old 使用率",
    "eu_pct": "Eden 使用率",
    "mu_pct": "Metaspace 使用率",
    "fgc_delta": "区間 Full GC 回数",
    "fgct_delta": "区間 Full GC 時間",
    "ygct_delta": "区間 Young GC 時間",
    "njobs": "総ジョブ数",
    "pend": "待機ジョブ数",
    "run": "実行中ジョブ数",
    "susp": "サスペンド中ジョブ数",
}

ALGORITHM_ASPECTS = {"観点1": "瞬間的な外れ値", "観点2": "持続的な増加",
                     "観点3": "上限への張り付き"}   # ※CR-005

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


class ReportContext(object):
    """レポート生成に必要な情報 (現在時刻は引数で受け取る。TP-11)。"""

    def __init__(self, now, target_dir, period, file_count, record_count,
                 series_count, events, algorithms, load_errors, skipped,
                 failures, vendor_note=None):
        self.now = now
        self.target_dir = target_dir
        self.period = period
        self.file_count = file_count
        self.record_count = record_count
        self.series_count = series_count
        self.events = events
        self.algorithms = algorithms
        self.load_errors = load_errors
        self.skipped = skipped
        self.failures = failures
        self.vendor_note = vendor_note


# ---------------------------------------------------------------------------
# 値の整形
# ---------------------------------------------------------------------------
def format_number(value) -> str:
    if value is None:
        return "不明"
    try:
        return "{0:.4g}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


def format_values(values) -> str:
    """値の並びを `→` で連結する。9 点を超えたら先頭 4 + … + 末尾 4。"""
    if not values:
        return "(値なし)"
    items = [format_number(v) for v in values]
    if len(items) > 9:
        items = items[:4] + ["…"] + items[-4:]
    return "→".join(items)


#: ※CR-008 レポートに併記するタイムゾーン。`set_timezone` で差し替える。
#: **DuckDB に格納された時刻がどのタイムゾーンのものかを示すだけ**であり、
#: 表示のために時刻を変換することはない (変換は投入時に済んでいる)。
_timezone = [config.DEFAULT_TIMEZONE]


#: ※CR-009 チャートが無いイベントに出す画像の相対パス。
#: **None ならチャート機能そのものが無効**であり、画像を一切出さない。
_chart_placeholder = [None]


def set_chart_placeholder(path):
    """「チャートなし」画像の相対パスを設定する (※CR-009)。

    **`None` を渡すと、チャートが無いイベントに画像を出さない。**
    チャート機能を無効にしている場合はそもそも誰も図を期待しないため、
    `[NO CHART]` を並べる必要がない。
    """
    _chart_placeholder[0] = path


def chart_placeholder():
    return _chart_placeholder[0]


def render_chart_points(points) -> list:
    """点数が足りずチャートにできなかったときの、生の点列の表 (※CR-009)。

    **図が出ない代わりに、元の値をそのまま見せる。**
    件数は定義上 `charts.MIN_POINTS` 未満(既定 5 点未満)であり、
    レポートの分量にはほとんど効かない。
    """
    lines = ["", "| 時刻 | 値 |", "| --- | --- |"]
    for ts, value in points:
        lines.append("| {0} | {1} |".format(format_ts(ts), format_number(value)))
    if not points:
        lines.append("| (データがありません) | — |")
    return lines


def set_timezone(name):
    """レポートに併記するタイムゾーンを設定する (※CR-008)。"""
    _timezone[0] = name or config.DEFAULT_TIMEZONE


def timezone_label() -> str:
    return _timezone[0]


def format_ts(ts) -> str:
    """時刻を「値 (タイムゾーン)」の形で返す (※CR-008)。

    **時刻そのものは変換しない。** 投入時に格納タイムゾーンへ直してあるため、
    ここでの役割は「どのタイムゾーンの時刻なのか」を読み手に示すことだけである。
    """
    if ts is None:
        return "不明"
    return "{0} ({1})".format(ts.strftime("%Y-%m-%d %H:%M:%S"), _timezone[0])


def format_duration(start, end) -> str:
    if start is None or end is None:
        return "不明"
    seconds = (end - start).total_seconds()
    if seconds < 3600:
        return "{0:.0f}分".format(seconds / 60.0)
    if seconds < 86400:
        return "{0:.1f}時間".format(seconds / 3600.0)
    return "{0:.1f}日".format(seconds / 86400.0)


def _series_key_text(event) -> str:
    """series_id を人間向けの表記へ分解する。"""
    sid = event.series_id
    if event.source == "db_connection" and "/" in sid:
        rest = sid.split("/", 1)[1]
        hostport, _, datasource = rest.partition("/")
        host, _, port = hostport.partition(":")
        return "host={0}, port={1}, datasource={2}".format(host, port, datasource)
    if event.source == "jvm_gc" and "@" in sid:
        rest = sid.split("/", 1)[1]
        container, _, host = rest.partition("@")
        return "container={0}, host={1}".format(container, host)
    if event.source == "lsf_queue":
        parts = sid.split("/")
        if len(parts) >= 3:
            return "host={0}, queue={1}".format(parts[1], parts[2])
    return sid


# ---------------------------------------------------------------------------
# 「現象」欄 (P002 4.3)
# ---------------------------------------------------------------------------
def render_phenomenon(event) -> str:
    detail = event.detail or {}
    shape = event.shape
    if shape == "spike_up":
        d = _first(detail, ("ALG-A1", "ALG-A2", "ALG-A3", "ALG-A4", "ALG-A5"))
        baseline = d.get("mu", d.get("median", d.get("bucket_mu")))
        value = d.get("value")
        ratio = _ratio(value, baseline)
        return ("平常時({0})に対し {1}倍({2})に跳ね上がった。"
                "前後の点は平常範囲".format(format_number(baseline), ratio,
                                            format_number(value)))
    if shape == "spike_down":
        d = _first(detail, ("ALG-A1", "ALG-A2", "ALG-A3", "ALG-A4", "ALG-A5"))
        baseline = d.get("mu", d.get("median", d.get("bucket_mu")))
        value = d.get("value")
        return ("平常時({0})に対し {1}({2})に落ち込んだ".format(
            format_number(baseline), _ratio(value, baseline),
            format_number(value)))
    if shape == "sustained":
        # ※CR-005: ALG-C1 (上限への張り付き) の場合は、上限に対する位置を示す。
        c1 = detail.get("ALG-C1")
        if c1:
            kind = "推定上限" if c1.get("ceiling_estimated") else "上限"
            return ("{0}にわたり{1}({2})の {3}% 以上に張り付いた"
                    "(期間中の平均 {4}, 最大 {5})".format(
                        format_duration(event.start_ts, event.end_ts), kind,
                        format_number(c1.get("ceiling")),
                        format_number(c1.get("ratio_pct")),
                        format_number(c1.get("avg_value")),
                        format_number(c1.get("max_value"))))
        return "{0}にわたり平常範囲を上回った状態が継続した".format(
            format_duration(event.start_ts, event.end_ts))
    if shape == "floor_rise":
        d = detail.get("ALG-B3", {})
        load = _load_note(event)
        return "最低値が{0}上昇し、回復しない、{1}".format(
            format_duration(event.start_ts, event.end_ts), load)
    if shape == "trend_up":
        d = detail.get("ALG-B5", detail.get("ALG-B2", {}))
        slope = d.get("slope_per_day", d.get("sen_slope_per_day"))
        start_v = event.values[0] if event.values else None
        end_v = event.values[-1] if event.values else None
        return "{0}で {1}から{2}へ増加した(平均 {3}/日)".format(
            format_duration(event.start_ts, event.end_ts),
            format_number(start_v), format_number(end_v),
            format_number(slope))
    if shape == "level_shift":
        d = detail.get("ALG-B4", {})
        return "{0}を境に水準が {1}から{2}へ移行し、元に戻らない".format(
            format_ts(event.end_ts),
            format_number(d.get("level_before")),
            format_number(d.get("level_after")))
    if shape == "seasonal_dev":
        d = detail.get("ALG-A5", {})
        dow = d.get("dow")
        hour = d.get("hour")
        weekday = _WEEKDAY_JA[dow] + "曜" if isinstance(dow, int) and 0 <= dow < 7 else "不明"
        hour_text = "{0}時台".format(hour) if hour is not None else "不明な時刻帯"
        return "{0}{1}の平常値({2})に対し {3}であった".format(
            weekday, hour_text, format_number(d.get("bucket_mu")),
            format_number(d.get("value")))
    return "平常範囲から外れた状態が検出された"


def _first(detail, keys):
    for key in keys:
        if key in detail:
            return detail[key]
    return {}


def _ratio(value, baseline):
    if value is None or baseline in (None, 0):
        return "不明"
    return "{0:.4g}".format(value / baseline)


def _load_note(event):
    """「現象」欄に添える負荷の注記 (P002 4.3)。

    ※CR-002 により、判定の入力が相関の統計値から同時アノマリーへ変わった。
    """
    for c in (event.co_anomalies or ()):
        if c.metric in ("active_connections", "run"):
            if getattr(c, "shape", None) in ("spike_up", "trend_up",
                                             "floor_rise", "level_shift"):
                return "負荷増を伴う"
    return "負荷増はない"


# ---------------------------------------------------------------------------
# 「説明」欄
# ---------------------------------------------------------------------------
def render_explanation(event) -> str:
    lines = []
    detail = event.detail or {}
    for alg_id in event.algorithms:
        d = detail.get(alg_id, {})
        lines.append("{0}: {1}".format(alg_id, _explain_one(alg_id, d)))
    if not lines:
        return "判定根拠を取得できませんでした"
    return "\n             ".join(lines)


def _explain_one(alg_id, d):
    if alg_id == "ALG-A1":
        return ("移動平均からの残差が {0}σ に達した (閾値 {1}σ/{2}σ)".format(
            format_number(d.get("dev")), format_number(d.get("k_warn")),
            format_number(d.get("k_fatal"))))
    if alg_id == "ALG-A2":
        return ("移動中央値からの残差が MAD の {0} 倍に達した (閾値 {1})".format(
            format_number(d.get("dev")), format_number(d.get("k"))))
    if alg_id == "ALG-A3":
        return ("系列全体の四分位範囲から外れた (Q1={0}, Q3={1}, 値={2})".format(
            format_number(d.get("q1")), format_number(d.get("q3")),
            format_number(d.get("value"))))
    if alg_id == "ALG-A4":
        return ("前日同時刻との差分の指数加重移動平均が管理限界を超えた "
                "(z={0}, 限界={1})".format(
                    format_number(d.get("z")),
                    format_number((d.get("k") or 0) * (d.get("sigma_z") or 0))))
    if alg_id == "ALG-A5":
        return ("同じ曜日・時刻帯の平常値から {0}σ 外れた (平常値={1})".format(
            format_number(d.get("z")), format_number(d.get("bucket_mu"))))
    if alg_id == "ALG-B1":
        return ("短期移動平均が長期移動平均を {0} 点連続で上回った "
                "(下限 {1} 点)".format(d.get("run_length"), d.get("min_run")))
    if alg_id == "ALG-B2":
        return ("Mann-Kendall 検定で有意な増加傾向を検出した "
                "(S={0}, p={1}, Sen勾配={2}/日)".format(
                    d.get("S"), format_number(d.get("p")),
                    format_number(d.get("sen_slope_per_day"))))
    if alg_id == "ALG-B3":
        return ("{0}分窓のローリング最小値が連続{1}窓にわたり非減少で、"
                "下限が {2} から {3} へ上昇した (総増加 {4})".format(
                    d.get("window_minutes"), d.get("run_length"),
                    format_number(d.get("floor_start")),
                    format_number(d.get("floor_end")),
                    format_number(d.get("total_rise"))))
    if alg_id == "ALG-B4":
        return ("前日同時刻との差分の累積和が閾値を超えた "
                "(S+={0}, h={1})".format(
                    format_number(d.get("s_plus")), format_number(d.get("h"))))
    if alg_id == "ALG-B5":
        return ("線形回帰の傾きが 1 日あたり {0}、決定係数 R²={1} "
                "(総増加 {2})".format(
                    format_number(d.get("slope_per_day")),
                    format_number(d.get("r2")),
                    format_number(d.get("total_increase"))))
    if alg_id == "ALG-C1":
        # ※CR-005: 推定した上限かどうかを必ず示す。利用者が妥当性を判断できるため。
        kind = "推定上限" if d.get("ceiling_estimated") else "実際の上限"
        held = d.get("held_seconds") or 0.0
        if held < 3600:
            span = "{0:.0f} 分".format(held / 60.0)
        elif held < 86400:
            span = "{0:.1f} 時間".format(held / 3600.0)
        else:
            span = "{0:.1f} 日".format(held / 86400.0)
        return ("{0} {1} の {2}% ({3}) 以上が {4}続いた "
                "(期間中の平均 {5}, 最大 {6}, {7} 点)".format(
                    kind, format_number(d.get("ceiling")),
                    format_number(d.get("ratio_pct")),
                    format_number(d.get("threshold")), span,
                    format_number(d.get("avg_value")),
                    format_number(d.get("max_value")),
                    d.get("points")))
    return "判定根拠の詳細はありません"


# ---------------------------------------------------------------------------
# イベント本文 (P002 4.2)
# ---------------------------------------------------------------------------
def render_event(event, metric_labels=None) -> str:
    labels = metric_labels if metric_labels is not None else METRIC_LABELS
    label = labels.get(event.metric)
    metric_text = "{0} ({1})".format(event.metric, label) if label else event.metric

    algorithm_names = ", ".join(
        "{0} {1}".format(alg_id, _ALGORITHM_NAMES.get(alg_id, ""))
        for alg_id in event.algorithms
    ) or "(不明)"

    lines = []
    lines.append("# イベントID( {0} )".format(event.event_id))
    lines.append("")
    lines.append("* イベント種別 : {0}".format(algorithm_names))
    lines.append("* 脅威度 : {0}".format(event.severity or "INFO"))
    lines.append("* データ : {0} / {1} / {2}".format(
        event.source, metric_text, _series_key_text(event)))
    lines.append("* 値     : {0}".format(format_values(event.values)))
    lines.append("* 開始   : {0}".format(format_ts(event.start_ts)))
    lines.append("* 終了   : {0}".format(format_ts(event.end_ts)))
    lines.append("* 現象   : {0}".format(render_phenomenon(event)))
    lines.append("* 説明   : {0}".format(render_explanation(event)))

    # 原因候補。該当なしでも行を省略しない (UI-04-02)
    if event.causes:
        first = event.causes[0]
        lines.append("* 原因候補 : {0} (確度: {1}) — {2}".format(
            first.cause, first.confidence, first.reason))
        for cause in event.causes[1:]:
            lines.append("             {0} (確度: {1}) — {2}".format(
                cause.cause, cause.confidence, cause.reason))
    else:
        lines.append("* 原因候補 : 該当する候補なし (確度: —)")

    # 同時に発生したアノマリー (P002 UI-04-C05/C06)。
    #
    # **観点2 を含むイベントでは、この項目行ごと出力しない** (UI-04-02 の唯一の
    # 例外)。co_anomalies が None なら観点2 を含む、空リストなら観点1 だが相手が
    # 無い、という区別である (DS-11-08)。
    if event.co_anomalies is not None:
        lines.append("* 同時に発生したアノマリー :")
        if event.co_anomalies:
            for c in event.co_anomalies:
                lines.append("  - {0}".format(_co_anomaly_text(c)))
        else:
            lines.append("  - (同じ時間帯に検知された他のアノマリーはありません)")

    # チャート (※CR-009)。**リンクは相対パスである**
    # (レポートは別の場所へ移されうるため)。
    if event.chart_path:
        lines.append("")
        lines.append("![{0} {1} の推移]({2})".format(
            event.event_id, event.metric, event.chart_path))
    elif chart_placeholder():
        # **チャートが無いイベントにも画像を出す**(2026-09-06 の指示)。
        # 図が無いと「ツールの不備」に見えるためである。
        # **理由は書かない。** 脅威度・件数の上限・点数不足と複数あり、
        # 1 行で正確に言えないうえ、レポートの分量に効く。
        lines.append("")
        lines.append("![{0} チャートなし]({1})".format(
            event.event_id, chart_placeholder()))
        # **点数不足が理由のときだけ、生の点列を表で出す**(2026-09-06 の指示)。
        if event.chart_points is not None:
            lines.extend(render_chart_points(event.chart_points))
    return "\n".join(lines)


def _co_anomaly_text(c):
    """同時に発生したアノマリー 1 件の行 (P002 UI-04-C05)。

    相手のイベント ID にはホスト名が含まれる (CR-003) ため、**どのファイルを
    開けばよいかがこの行だけで分かる**。
    """
    return "{0} ({1}) {2} / {3} : {4} / 重なり {5}".format(
        c.event_id,
        "同一ホスト" if c.same_host else "他ホスト",
        c.source, c.metric, c.severity,
        format_overlap(c.overlap_seconds),
    )


def format_overlap(seconds) -> str:
    """重なりの長さを読みやすい単位で表す (P007 U008-T5)。"""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "0 分"
    minutes = seconds / 60.0
    if minutes < 60:
        return "{0:.0f} 分".format(minutes)
    hours = minutes / 60.0
    if hours < 24:
        return "{0:.1f} 時間".format(hours)
    return "{0:.1f} 日".format(hours / 24.0)


_ALGORITHM_NAMES = {
    "ALG-A1": "移動平均乖離率",
    "ALG-A2": "Hampel フィルタ",
    "ALG-A3": "Tukey の外れ値境界",
    "ALG-A4": "EWMA 管理図",
    "ALG-A5": "曜日・時刻別ベースライン",
    "ALG-B1": "短期/長期移動平均のクロス継続",
    "ALG-B2": "Mann-Kendall 傾向検定",
    "ALG-B3": "ローリング最小値の単調増加",
    "ALG-B4": "CUSUM",
    "ALG-B5": "線形回帰の傾き",
    "ALG-C1": "上限への張り付き",   # ※CR-005
}


# ---------------------------------------------------------------------------
# レポート全体 (P002 4.1)
# ---------------------------------------------------------------------------
def _render_algorithms(ctx):
    lines = ["## 2. 適用したアルゴリズム", "",
             "| ID | 名称 | 観点 | 状態 | 主なパラメータ |",
             "| --- | --- | --- | --- | --- |"]
    for alg_id, name, aspect, enabled, params in ctx.algorithms:
        lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
            alg_id, name, ALGORITHM_ASPECTS.get(aspect, aspect),
            "有効" if enabled else "無効", params or "-"))
    lines.append("")
    return "\n".join(lines)


def _render_notes(ctx):
    lines = ["## 4. 実行時の注意事項", "", "### 4.1 読み飛ばした行", ""]
    if ctx.load_errors:
        lines.append("| ファイル | 理由 | 件数 |")
        lines.append("| --- | --- | --- |")
        for path, reason, count in ctx.load_errors:
            lines.append("| {0} | {1} | {2} |".format(path, reason, count))
    else:
        lines.append("(なし)")
    lines.append("")

    lines.append("### 4.2 点数不足でスキップした系列")
    lines.append("")
    if ctx.skipped:
        lines.append("| アルゴリズム | 理由 | スキップ系列数 |")
        lines.append("| --- | --- | --- |")
        for alg_id, reason, count in ctx.skipped:
            lines.append("| {0} | {1} | {2} |".format(alg_id, reason, count))
    else:
        lines.append("(なし)")
    lines.append("")

    lines.append("### 4.3 失敗したアルゴリズム")
    lines.append("")
    if ctx.failures:
        lines.append("| アルゴリズム | 系列 | メトリクス | 理由 |")
        lines.append("| --- | --- | --- | --- |")
        for alg_id, series_id, metric, reason in ctx.failures:
            lines.append("| {0} | {1} | {2} | {3} |".format(
                alg_id, series_id, metric, reason))
    else:
        lines.append("(なし)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ファイル書き込み (P002 4.4 / DS-12-02)
# ---------------------------------------------------------------------------
def write_report(text, out_path):
    """一時ファイル経由で書き込む。失敗しても壊れた report.md を残さない。"""
    out_path = str(out_path)
    tmp_path = out_path + TMP_SUFFIX
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_path, out_path)
    except OSError as exc:
        raise ReportWriteError(
            "report.md を書き込めませんでした",
            "  出力先: {0}\n  理由: {1}".format(out_path, exc),
        )
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# レポート群の生成 (P002 4.1 / DS-12-05〜DS-12-08) ※CR-001・CR-004
# ---------------------------------------------------------------------------
#: ファイル名に使えない文字 (Windows の制約に合わせる。NFR-006 により両 OS で
#  同じ名前になる必要がある)。
_UNSAFE_CHARS = r'\/:*?"<>|'

#: 日別表を週別へ切り替える日数の閾値 (DS-12-08)。
DAILY_TABLE_MAX_DAYS = 31

#: サマリの「複数ホストで同時に発生したアノマリー」の行数上限 (DS-12-08)。
MAX_CROSS_HOST_ROWS = 8

#: 1 行に並べるホスト名・メトリクス名の上限 (DS-12-08)。
#
# 全件を列挙すると、16 ホストが関与する行だけで 200 文字を超え、
# **サマリが 4K トークンに収まらなくなる** (実測: 90 日規模で 7,254 バイト)。
# 超えた分は件数で示す。どのホストが関与したかの詳細は、
# 各ホスト別ファイルの「同時に発生したアノマリー」欄で辿れる。
MAX_LISTED_HOSTS = 3
MAX_LISTED_METRICS = 3

#: サマリ「2. ホスト別の集計」の行数上限 (DS-12-08)。
#
# ホスト数はレコード数に比例しないが、**監視対象が増えれば増える**。
# 4K に収め続けるため、脅威度の高い順に上位のみ載せる。
MAX_HOST_ROWS = 10

#: サマリ「3. 日別 / 週別のイベント数」の行数上限 (DS-12-08)。
MAX_BUCKET_ROWS = 10


def _abbrev(items, limit):
    """先頭 limit 件までを並べ、超えた分は「ほか N 件」と書く。"""
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return "{0} ほか {1} 件".format(", ".join(items[:limit]), len(items) - limit)


def safe_host(host) -> str:
    """ホスト名をファイル名に使える形にする (DS-12-06)。"""
    out = []
    for ch in str(host or "unknown"):
        out.append("_" if (ch in _UNSAFE_CHARS or ord(ch) < 32) else ch)
    return "".join(out) or "unknown"


def _safe_host_map(hosts):
    """ホスト名 -> ファイル名用の名前。衝突したら連番を付す (DS-12-06)。"""
    used = {}
    mapping = {}
    for host in sorted(hosts):
        base = safe_host(host)
        n = used.get(base, 0) + 1
        used[base] = n
        mapping[host] = base if n == 1 else "{0}-{1}".format(base, n)
    return mapping


def group_events(events):
    """イベントを (ホスト名, 年月) で分類する (DS-12-05)。"""
    from .events import extract_host

    groups = {}
    for event in events:
        key = (extract_host(event.series_id), event.start_ts.strftime("%Y%m"))
        groups.setdefault(key, []).append(event)
    return groups


def _severity_counts(events):
    counts = {"SEVERE": 0, "FATAL": 0, "WARN": 0, "INFO": 0}
    for e in events:
        counts[e.severity] = counts.get(e.severity, 0) + 1
    return counts


def _breakdown(events) -> str:
    c = _severity_counts(events)
    return "{0} (SEVERE: {1}, FATAL: {2}, WARN: {3}, INFO: {4})".format(
        len(events), c["SEVERE"], c["FATAL"], c["WARN"], c["INFO"])


def render_host_digest(host, ym, events) -> str:
    """ホスト別ファイルの「1. このホストの集計」(FR-077 / DS-12-07)。

    **本章はメトリクスの種類数で行数が決まり、イベント数に比例しない。**
    したがって規模が伸びても 4K トークンに収まり続ける (DS-12-08)。
    """
    per = {}
    algs = {}
    for e in events:
        c = per.setdefault(e.metric, {"SEVERE": 0, "FATAL": 0, "WARN": 0,
                                      "INFO": 0, "計": 0})
        c[e.severity] = c.get(e.severity, 0) + 1
        c["計"] += 1
        for a in (e.algorithms or ()):
            algs.setdefault(e.metric, set()).add(a)

    lines = ["## 1. このホストの集計", "",
             "| 項目 | 値 |", "| --- | --- |",
             "| ホスト | {0} |".format(host),
             "| 対象年月 | {0}-{1} |".format(ym[:4], ym[4:]),
             "| 検知イベント数 | {0} |".format(_breakdown(events)), "",
             "| メトリクス | SEVERE | FATAL | WARN | 計 | 検知手法 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for metric in sorted(per, key=lambda m: (-per[m]["SEVERE"], -per[m]["計"], m)):
        c = per[metric]
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} |".format(
            metric, c["SEVERE"], c["FATAL"], c["WARN"], c["計"],
            ", ".join(sorted(algs.get(metric, ()))) or "-"))
    lines.append("")
    return "\n".join(lines)


def render_host_report(ctx, host, ym, events, summary_name) -> str:
    """report_{HOST}_{yyyymm}.md の全文 (P002 4.1.2)。"""
    parts = ["# {0} {1}-{2} の異常検知レポート".format(host, ym[:4], ym[4:]), ""]
    parts.append(render_host_digest(host, ym, events))
    parts.append(_render_host_summary(ctx, summary_name))
    parts.append(_render_algorithms(ctx).replace("## 2. 適用したアルゴリズム",
                                                 "## 3. 適用したアルゴリズム", 1))
    parts.append(_render_host_events(events))
    parts.append(_render_notes(ctx).replace("## 4. 実行時の注意事項",
                                            "## 5. 実行時の注意事項", 1)
                 .replace("### 4.1 ", "### 5.1 ").replace("### 4.2 ", "### 5.2 ")
                 .replace("### 4.3 ", "### 5.3 "))
    return "\n".join(parts).rstrip() + "\n"


def _render_host_summary(ctx, summary_name):
    period_from, period_to = ctx.period if ctx.period else (None, None)
    lines = [
        "## 2. 実行サマリ", "",
        "| 項目 | 値 |", "| --- | --- |",
        "| 実行日時 | {0} |".format(format_ts(ctx.now)),
        "| 対象ディレクトリ | {0} |".format(ctx.target_dir),
        "| 対象期間 | {0} 〜 {1} |".format(format_ts(period_from),
                                            format_ts(period_to)),
        "| 読み込んだファイル数 | {0} |".format(ctx.file_count),
        "| 総レコード数 | {0:,} |".format(ctx.record_count),
        "| 系列数(全体) | {0} |".format(ctx.series_count),
        # どのファイルを見れば全体像が分かるかを、各ファイル単体から辿れるように
        # する (UI-04-F08)。
        "| サマリ | {0} |".format(summary_name),
    ]
    if ctx.vendor_note:
        lines.append("| 実行環境の注記 | {0} |".format(ctx.vendor_note))
    lines.append("")
    return "\n".join(lines)


def _render_host_events(events):
    lines = ["## 4. 検知イベント", ""]
    if not events:
        lines.append("検知された異常はありません。")
        lines.append("")
        return "\n".join(lines)
    for event in events:
        lines.append(render_event(event))
        lines.append("")
    return "\n".join(lines)


def render_summary(ctx, ym, groups, host_files) -> str:
    """report_summary_{yyyymm}.md の全文 (FR-078 / P002 4.1.1)。

    **4K トークンのコンテキストに収まること**が要件である (UI-04-F06)。
    そのため日別表は期間が長いと週別へ切り替え、複数ホスト同時発生は上位
    MAX_CROSS_HOST_ROWS 行までに絞る (DS-12-08)。
    """
    from . import co_anomaly

    month_events = [e for (h, m), evs in groups.items() if m == ym
                    for e in evs]
    period_from, period_to = ctx.period if ctx.period else (None, None)
    hosts = sorted({h for (h, m) in groups if m == ym})

    lines = ["# 異常検知サマリ {0}-{1}".format(ym[:4], ym[4:]), "",
             "## 1. 実行サマリ", "",
             "| 項目 | 値 |", "| --- | --- |",
             "| 実行日時 | {0} |".format(format_ts(ctx.now)),
             "| 対象ディレクトリ | {0} |".format(ctx.target_dir),
             "| 対象期間 | {0} 〜 {1} |".format(format_ts(period_from),
                                                 format_ts(period_to)),
             "| 読み込んだファイル数 | {0} |".format(ctx.file_count),
             "| 総レコード数 | {0:,} |".format(ctx.record_count),
             "| ホスト数 | {0} |".format(len(hosts)),
             "| 系列数 | {0} |".format(ctx.series_count),
             "| 検知イベント数 | {0} |".format(_breakdown(month_events))]
    if ctx.vendor_note:
        lines.append("| 実行環境の注記 | {0} |".format(ctx.vendor_note))
    lines.append("")

    # --- 2. ホスト別 ---------------------------------------------------
    lines += ["## 2. ホスト別の集計", ""]
    if not month_events:
        lines += ["検知された異常はありません。", ""]
    else:
        lines += ["| ホスト | SEVERE | FATAL | WARN | 計 | 主なメトリクス | レポート |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        rows = []
        for host in hosts:
            evs = groups.get((host, ym), [])
            c = _severity_counts(evs)
            freq = {}
            for e in evs:
                freq[e.metric] = freq.get(e.metric, 0) + 1
            top = ", ".join(m for m, _ in sorted(
                freq.items(), key=lambda kv: (-kv[1], kv[0]))[:3]) or "-"
            rows.append((c["SEVERE"], c["FATAL"], len(evs), host, c, top))
        ordered_rows = sorted(rows, key=lambda r: (-r[0], -r[1], -r[2], r[3]))
        for _sev, _fat, _tot, host, c, top in ordered_rows[:MAX_HOST_ROWS]:
            lines.append("| {0} | {1} | {2} | {3} | {4} | {5} | {6} |".format(
                host, c["SEVERE"], c["FATAL"], c["WARN"],
                c["SEVERE"] + c["FATAL"] + c["WARN"] + c["INFO"],
                top, host_files.get((host, ym), "-")))
        if len(ordered_rows) > MAX_HOST_ROWS:
            lines.append("")
            lines.append("(ほか {0} ホスト。脅威度の高い順に上位 {1} 件のみ表示。"
                         "全ホストのファイルは report_*_{2}.md にある)".format(
                             len(ordered_rows) - MAX_HOST_ROWS,
                             MAX_HOST_ROWS, ym))
        lines.append("")

    # --- 3. 日別 / 週別 -------------------------------------------------
    by_bucket = {}
    for e in month_events:
        by_bucket.setdefault(e.start_ts.strftime("%m-%d"), []).append(e)
    weekly = len(by_bucket) > DAILY_TABLE_MAX_DAYS
    if weekly:
        by_bucket = {}
        for e in month_events:
            iso = e.start_ts.isocalendar()
            by_bucket.setdefault("{0}-W{1:02d}".format(iso[0], iso[1]), []).append(e)
    lines += ["## 3. {0}のイベント数".format("週別" if weekly else "日別"), ""]
    if not by_bucket:
        lines += ["検知された異常はありません。", ""]
    else:
        lines += ["| {0} | SEVERE | FATAL | WARN | 計 |".format(
                      "週" if weekly else "日"),
                  "| --- | --- | --- | --- | --- |"]
        # 件数の多い順に上位のみ。**異常が集中した日を見つけるのが目的**であり、
        # 全日を並べる必要はない (DS-12-08)。
        top_keys = sorted(by_bucket,
                          key=lambda k: (-len(by_bucket[k]), k))[:MAX_BUCKET_ROWS]
        for key in sorted(top_keys):
            c = _severity_counts(by_bucket[key])
            lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
                key, c["SEVERE"], c["FATAL"], c["WARN"], len(by_bucket[key])))
        if len(by_bucket) > MAX_BUCKET_ROWS:
            lines.append("")
            lines.append("(全 {0} {1}中、イベント数の多い上位 {2} {1}のみ表示)".format(
                len(by_bucket), "週" if weekly else "日", MAX_BUCKET_ROWS))
        lines.append("")

    # --- 4. 複数ホストで同時に発生したアノマリー -------------------------
    lines += ["## 4. 複数ホストで同時に発生したアノマリー", ""]
    cross = co_anomaly.cross_host_groups(month_events)
    if not cross:
        lines += ["複数ホストにまたがる同時発生はありません。", ""]
    else:
        lines += ["| 時間帯 | ホスト数 | イベント数 | 関与したホスト | 主なメトリクス |",
                  "| --- | --- | --- | --- | --- |"]
        for g in cross[:MAX_CROSS_HOST_ROWS]:
            lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
                g["when"], len(g["hosts"]), g["count"],
                _abbrev(sorted(g["hosts"]), MAX_LISTED_HOSTS),
                _abbrev(sorted(g["metrics"]), MAX_LISTED_METRICS)))
        if len(cross) > MAX_CROSS_HOST_ROWS:
            lines.append("")
            lines.append("(ほか {0} 件。件数の多い順に上位 {1} 件のみ表示)".format(
                len(cross) - MAX_CROSS_HOST_ROWS, MAX_CROSS_HOST_ROWS))
        lines.append("")

    # --- 5. 実行時の注意事項 -------------------------------------------
    #
    # **サマリにも必ず載せる** (P002 UI-04-F10)。ホストに紐づけられない
    # 注意事項 (ファイル全体の読み込み失敗など) の受け皿であり、
    # **検知 0 件でホスト別ファイルが 1 つも作られない場合、ここが唯一の
    # 記録先になる**。読み飛ばした行の件数を失わないため (FR-014)。
    lines.append(_render_notes(ctx).replace("## 4. 実行時の注意事項",
                                            "## 5. 実行時の注意事項", 1)
                 .replace("### 4.1 ", "### 5.1 ")
                 .replace("### 4.2 ", "### 5.2 ")
                 .replace("### 4.3 ", "### 5.3 "))
    return "\n".join(lines).rstrip() + "\n"


def write_reports(ctx, out_dir) -> list:
    """レポート群を書き出し、書いたパスの一覧を返す (P002 UI-04-F01)。

    **ファイルごとに一時ファイル経由で書く** (DS-12-02)。途中で失敗しても
    既に書き終えたファイルは残す (UI-04-04)。**前回実行時のファイルは
    削除しない** (UI-04-05)。
    """
    out_dir = str(out_dir)
    groups = group_events(ctx.events)
    names = _safe_host_map({h for (h, _m) in groups})

    months = sorted({m for (_h, m) in groups})
    if not months:
        # 検知 0 件でもサマリは必ず作る (UI-04-04)
        period_to = ctx.period[1] if ctx.period else None
        months = [(period_to or ctx.now).strftime("%Y%m")]

    host_files = {}
    for (host, ym) in groups:
        host_files[(host, ym)] = "report_{0}_{1}.md".format(names[host], ym)

    written = []
    for (host, ym) in sorted(groups, key=lambda k: (k[1], k[0])):
        events = sort_for_report_like(groups[(host, ym)])
        name = host_files[(host, ym)]
        path = os.path.join(out_dir, name)
        summary_name = "report_summary_{0}.md".format(ym)
        write_report(render_host_report(ctx, host, ym, events, summary_name),
                     path)
        written.append(path)

    for ym in months:
        path = os.path.join(out_dir, "report_summary_{0}.md".format(ym))
        write_report(render_summary(ctx, ym, groups, host_files), path)
        written.append(path)
    return written


def sort_for_report_like(events):
    """ctx.events の並び (脅威度降順・開始時刻昇順) を保ったまま抜き出す。"""
    from .events import sort_for_report

    return sort_for_report(events)


def render_all_for_fallback(ctx) -> str:
    """書き込みに失敗したときに標準出力へ出す全文 (UI-05-01)。

    ファイル群として書けなかったため、全ホスト分を 1 本のテキストに連結する。
    """
    groups = group_events(ctx.events)
    names = _safe_host_map({h for (h, _m) in groups})
    host_files = {(h, m): "report_{0}_{1}.md".format(names[h], m)
                  for (h, m) in groups}
    parts = []
    for ym in sorted({m for (_h, m) in groups}) or [ctx.now.strftime("%Y%m")]:
        parts.append(render_summary(ctx, ym, groups, host_files))
    for (host, ym) in sorted(groups, key=lambda k: (k[1], k[0])):
        parts.append(render_host_report(
            ctx, host, ym, sort_for_report_like(groups[(host, ym)]),
            "report_summary_{0}.md".format(ym)))
    return "\n".join(parts)
