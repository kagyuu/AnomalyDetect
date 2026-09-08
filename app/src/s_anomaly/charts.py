"""M15 charts — アノマリーの状況を SVG の折れ線チャートにする (P003 15章)。※CR-009 により新設。

**外部ライブラリを使わない。** SVG は単なるテキストであり、折れ線・軸・凡例は
文字列として組み立てられる。`docs/ADR.md` **ADR-020** に、matplotlib 等を
採らなかった理由がある(要点は「`vendor/` 経路がまだ実証されておらず、
C 拡張を持つ依存を増やせない」ことと「SVG なら日本語が閲覧側のフォントで出る」こと)。

**1 イベント = 1 ファイル。** 2 段構成にする。

| 段 | 内容 |
| --- | --- |
| 上 | **そのイベントの系列を実単位で描く。** 異常と判定された区間を網掛けする |
| 下 | **同時に発生したアノマリー**(CR-002)を重ねる。**各系列を 0〜100% に正規化する** |

**下段で正規化するのは、単位が違うものを 1 枚に重ねるためである。**
DB コネクション数(0〜200)とメモリ使用率(0〜100%)と待ちジョブ数(0〜2000)は
同じ縦軸に載らない。**正規化した結果、縦軸は無次元になる**ので、
凡例に各系列の実際の最小値・最大値を併記する。

**出力は決定的でなければならない**(NFR-009)。そのため

* 座標は固定の桁数で丸める(浮動小数点の下位ビットを出力に出さない)
* 日付や乱数を SVG に埋めない
* 系列の並び順を固定する
"""

import os
from datetime import timedelta

from .config import CHART_SEVERITIES
from .events import extract_host

#: 図の寸法 (px)。
WIDTH = 720
PANEL_HEIGHT = 180
MARGIN_LEFT = 64
MARGIN_RIGHT = 16
MARGIN_TOP = 28
MARGIN_BOTTOM = 34
#: 凡例 1 行の高さ。
LEGEND_LINE = 16

#: 折れ線に使う色。**色覚特性に配慮し、明度差のある並びにする。**
#: 1 本目 (そのイベント自身) は必ず濃い青にする。
COLORS = [
    "#1f4e9c",   # 濃い青   … このイベント
    "#c0392b",   # 赤
    "#117a65",   # 緑
    "#8e44ad",   # 紫
    "#b9770e",   # 茶
    "#2874a6",   # 青
]

#: 描画する点の上限。これを超える系列は等間隔に間引く。
#: **横 720px に対して 360 点あれば 2px に 1 点であり、これ以上は見た目が変わらない。**
#:
#: **極値を保持する間引き(min/max デシメーション)へは変えない。** 5 分間隔の
#: データでは間引きが働くのはイベント長が約 18〜20 時間を超えたときだけであり、
#: そこでの読み取り対象は個々の極値ではなく水準の推移だからである。
#: 判断の根拠と、この決定を覆すべき条件(採取間隔が細かくなった場合)は
#: `docs/ADR.md` **ADR-021** にある。
MAX_POINTS = 360

#: 重ね合わせに載せる他系列の上限。凡例が読める範囲に抑える。
MAX_OVERLAY_SERIES = 5

#: 異常区間の網掛けの色。
SPAN_FILL = "#f4d03f"

#: 描画に必要な最小の点数。これに満たない系列は図にしない (線に見えない)。
MIN_POINTS = 5

#: 前後に取る余白の下限・上限 (秒)。
MIN_PAD_SECONDS = 1800.0        # 30 分
MAX_PAD_SECONDS = 6 * 3600.0    # 6 時間

#: 出力するディレクトリ名 (レポートからの相対パス)。
CHART_DIR = "charts"


def _round(value) -> str:
    """座標を固定の桁数で丸める (NFR-009)。

    **浮動小数点の下位ビットを出力に出さない。** 出さないと、同じ入力でも
    実行環境によって末尾が揺れうる。
    """
    return "{0:.2f}".format(value + 0.0).rstrip("0").rstrip(".") or "0"


def escape(text) -> str:
    """SVG のテキストとして安全な形にする。"""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def thin_out(points, limit=MAX_POINTS):
    """点列を等間隔に間引く。**先頭と末尾は必ず残す。**"""
    if len(points) <= limit:
        return list(points)
    step = (len(points) - 1) / float(limit - 1)
    picked = [points[int(round(i * step))] for i in range(limit)]
    picked[-1] = points[-1]
    return picked


def format_value(value) -> str:
    """凡例に出す数値。有効数字 4 桁 (レポート本文と同じ規則)。"""
    if value is None:
        return "不明"
    return "{0:.4g}".format(value)


class Panel(object):
    """1 つの描画領域。"""

    def __init__(self, top, height, y_min, y_max, y_labels=None):
        self.top = top
        self.height = height
        self.y_min = y_min
        self.y_max = y_max
        self.y_labels = y_labels or (format_value(y_min), format_value(y_max))

    @property
    def plot_width(self):
        return WIDTH - MARGIN_LEFT - MARGIN_RIGHT

    def x_of(self, index, count):
        if count <= 1:
            return MARGIN_LEFT
        return MARGIN_LEFT + self.plot_width * index / float(count - 1)

    def y_of(self, value):
        span = (self.y_max - self.y_min) or 1.0
        ratio = (value - self.y_min) / span
        # 上が大きい値になるよう反転する
        return self.top + self.height - ratio * self.height


def _polyline(panel, values, color, count):
    pts = " ".join(
        "{0},{1}".format(_round(panel.x_of(i, count)), _round(panel.y_of(v)))
        for i, v in enumerate(values)
    )
    return ('<polyline fill="none" stroke="{0}" stroke-width="1.4" '
            'stroke-linejoin="round" points="{1}"/>'.format(color, pts))


def _axes(panel, title):
    """枠と縦軸の目盛りを描く。"""
    out = [
        '<rect x="{0}" y="{1}" width="{2}" height="{3}" fill="#ffffff" '
        'stroke="#cccccc" stroke-width="1"/>'.format(
            MARGIN_LEFT, _round(panel.top), panel.plot_width, panel.height),
        '<text x="{0}" y="{1}" font-size="12" fill="#333333">{2}</text>'.format(
            MARGIN_LEFT, _round(panel.top - 8), escape(title)),
        '<text x="{0}" y="{1}" font-size="10" fill="#666666" '
        'text-anchor="end">{2}</text>'.format(
            MARGIN_LEFT - 6, _round(panel.top + 9), escape(panel.y_labels[1])),
        '<text x="{0}" y="{1}" font-size="10" fill="#666666" '
        'text-anchor="end">{2}</text>'.format(
            MARGIN_LEFT - 6, _round(panel.top + panel.height),
            escape(panel.y_labels[0])),
    ]
    return out


def _shade_span(panel, count, i_from, i_to):
    """異常と判定された区間を網掛けする。"""
    if i_from is None or i_to is None or count <= 1:
        return []
    x1 = panel.x_of(i_from, count)
    x2 = panel.x_of(i_to, count)
    if x2 <= x1:
        x2 = x1 + 1.0
    return ['<rect x="{0}" y="{1}" width="{2}" height="{3}" fill="{4}" '
            'fill-opacity="0.25"/>'.format(
                _round(x1), _round(panel.top), _round(x2 - x1),
                panel.height, SPAN_FILL)]


def _time_labels(panel, stamps):
    """横軸の両端に時刻を出す。**中間は出さない**(読めれば足りる)。"""
    if not stamps:
        return []
    y = panel.top + panel.height + 14
    return [
        '<text x="{0}" y="{1}" font-size="10" fill="#666666">{2}</text>'.format(
            MARGIN_LEFT, _round(y), escape(stamps[0])),
        '<text x="{0}" y="{1}" font-size="10" fill="#666666" '
        'text-anchor="end">{2}</text>'.format(
            WIDTH - MARGIN_RIGHT, _round(y), escape(stamps[-1])),
    ]


def build_svg(main, overlays=(), span=None, timezone="UTC"):
    """SVG の文字列を組み立てる。

    `main`     : {"label":..., "values":[...], "stamps":[...]}
    `overlays` : 同じ形の辞書のリスト (重ね合わせる他系列)
    `span`     : (開始の添字, 終了の添字) — 異常と判定された区間
    """
    values = main["values"]
    count = len(values)
    if count == 0:
        return None

    lo, hi = min(values), max(values)
    if hi == lo:
        # 完全に平坦な系列。**上下に余白を作らないと線が枠に張り付く。**
        pad = abs(hi) * 0.05 or 1.0
        lo, hi = lo - pad, hi + pad

    body = []
    top_panel = Panel(MARGIN_TOP, PANEL_HEIGHT, lo, hi)
    body.extend(_axes(top_panel, main["label"]))
    body.extend(_shade_span(top_panel, count, *(span or (None, None))))
    body.append(_polyline(top_panel, values, COLORS[0], count))
    body.extend(_time_labels(top_panel, main.get("stamps") or []))

    height = MARGIN_TOP + PANEL_HEIGHT + MARGIN_BOTTOM

    used = list(overlays)[:MAX_OVERLAY_SERIES]
    if used:
        # 下段: 正規化して重ねる。**単位が違うものを 1 枚に載せるため。**
        second_top = height + MARGIN_TOP
        panel = Panel(second_top, PANEL_HEIGHT, 0.0, 1.0,
                      y_labels=("0%", "100%"))
        body.extend(_axes(
            panel, "同時に発生したアノマリー (各系列を自身の最小〜最大で正規化)"))
        body.extend(_shade_span(panel, count, *(span or (None, None))))

        legend = []
        for index, item in enumerate([main] + used):
            vals = item["values"]
            if not vals:
                continue
            v_lo, v_hi = min(vals), max(vals)
            rng = (v_hi - v_lo) or 1.0
            normalized = [(v - v_lo) / rng for v in vals]
            color = COLORS[index % len(COLORS)]
            body.append(_polyline(panel, normalized, color, len(normalized)))
            legend.append((color, item["label"], v_lo, v_hi))

        height = second_top + PANEL_HEIGHT + MARGIN_BOTTOM
        legend_top = height
        for row, (color, label, v_lo, v_hi) in enumerate(legend):
            y = legend_top + row * LEGEND_LINE
            body.append(
                '<rect x="{0}" y="{1}" width="18" height="3" fill="{2}"/>'.format(
                    MARGIN_LEFT, _round(y), color))
            body.append(
                '<text x="{0}" y="{1}" font-size="11" fill="#333333">'
                '{2} ({3} 〜 {4})</text>'.format(
                    MARGIN_LEFT + 24, _round(y + 5), escape(label),
                    escape(format_value(v_lo)), escape(format_value(v_hi))))
        height = legend_top + len(legend) * LEGEND_LINE + 8

    header = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" '
        'viewBox="0 0 {0} {1}" font-family="sans-serif">'.format(
            WIDTH, int(height))
    )
    footer = (
        '<text x="{0}" y="{1}" font-size="9" fill="#999999" '
        'text-anchor="end">時刻は {2}</text>'.format(
            WIDTH - MARGIN_RIGHT, int(height) - 4, escape(timezone))
    )
    return "\n".join([header, '<rect width="100%" height="100%" fill="#ffffff"/>']
                     + body + [footer, "</svg>"]) + "\n"


#: チャートが無いことを示す画像のファイル名 (※CR-009。2026-09-06 の指示)。
#: **1 つだけ作り、全イベントから同じものを参照する。**
NO_CHART_NAME = "no-chart.svg"

#: プレースホルダの寸法。**折れ線の図より小さくする。**
#: 同じ大きさだと、一覧したときに図が有るのか無いのか紛らわしい。
NO_CHART_WIDTH = 360
NO_CHART_HEIGHT = 72


def build_no_chart_svg() -> str:
    """「チャートなし」を示す画像を組み立てる (※CR-009)。

    **理由は書かない。** レポートの分量に効くうえ、理由は脅威度・上限・点数と
    複数あり、1 行で正確に言えない (2026-09-06 の指示)。
    """
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" '
        'viewBox="0 0 {0} {1}" font-family="sans-serif">'.format(
            NO_CHART_WIDTH, NO_CHART_HEIGHT),
        '<rect width="100%" height="100%" fill="#f5f5f5" stroke="#cccccc" '
        'stroke-width="1"/>',
        '<text x="{0}" y="{1}" font-size="20" fill="#999999" '
        'text-anchor="middle" letter-spacing="2">[NO CHART]</text>'.format(
            NO_CHART_WIDTH // 2, NO_CHART_HEIGHT // 2 + 7),
        "</svg>",
    ]) + "\n"


def no_chart_path() -> str:
    """レポートから参照する相対パス。**全イベントで同じものを指す。**"""
    return "{0}/{1}".format(CHART_DIR, NO_CHART_NAME)


def write_no_chart(out_dir) -> str:
    """「チャートなし」の画像を 1 つだけ書き出す (※CR-009)。"""
    directory = os.path.join(out_dir, CHART_DIR)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    path = os.path.join(directory, NO_CHART_NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(build_no_chart_svg())
    return no_chart_path()


def chart_filename(event_id) -> str:
    """イベント ID からファイル名を作る。

    **イベント ID は既に安全な文字だけで構成されている**
    (`EVT-{yyyymmdd}-{host}-{連番}`。ホスト名は `safe_host` を通っている)。
    """
    return "{0}.svg".format(event_id)


def relative_path(event_id) -> str:
    """レポートから参照する相対パス。**絶対パスにしてはならない。**

    レポートは別の場所へ移されうるため(既存のホスト別ファイル同士も相対リンク)。
    """
    return "{0}/{1}".format(CHART_DIR, chart_filename(event_id))


def write_svg(out_dir, event_id, svg_text) -> str:
    """SVG を書き出し、レポートから参照する相対パスを返す。"""
    directory = os.path.join(out_dir, CHART_DIR)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    path = os.path.join(directory, chart_filename(event_id))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg_text)
    return relative_path(event_id)


# ---------------------------------------------------------------------------
# データの取り出しと、チャートを付けるイベントの選別
# ---------------------------------------------------------------------------
def select_events(events, cfg):
    """チャートを付けるイベントを選ぶ (※CR-009)。

    **全件には付けない。** 30 日規模でイベントは 7,229 件あり、
    全件に付けるとレポート一式が倍増する。

    **枠を 2 つに分ける。** 1 つの並び順で選ぶと、重ね合わせのチャートが
    一度も出なくなるためである。

    | 枠 | 選び方 | 何のためか |
    | --- | --- | --- |
    | 1 | 脅威度の高い順 | 「どういう形で異常だったのか」を見る |
    | 2 | **同時アノマリーを持つイベント**から脅威度の高い順 | 「同時に何が起きていたか」を見る |

    **枠 2 が必要な理由。** `SEVERE` は定義上「観点2 の検知」であり
    (`README.md` 5章)、**観点2 のイベントには同時アノマリーが付かない**
    (CR-002。`co_anomalies` が None)。したがって脅威度順だけで選ぶと、
    **重ね合わせのパネルが構造的に一度も描かれない。**

    並び順は決定的である。**スコアは浮動小数点だが、最後にイベント ID を
    置くため順序は一意に定まる**(NFR-009)。
    """
    if not cfg.chart_enabled:
        return []
    floor = CHART_SEVERITIES.index(cfg.chart_min_severity)
    eligible = [
        e for e in events
        if e.severity in CHART_SEVERITIES
        and CHART_SEVERITIES.index(e.severity) >= floor
    ]
    if not eligible:
        return []

    def rank(event):
        return (-CHART_SEVERITIES.index(event.severity), -event.score,
                event.event_id)

    picked = {}
    _take(sorted(eligible, key=rank), cfg.chart_max_per_host, picked)
    _take(sorted((e for e in eligible if e.co_anomalies), key=rank),
          cfg.chart_max_overlay_per_host, picked)

    # **出力順もイベント ID で決定的にする** (NFR-009)。
    return sorted(picked.values(), key=lambda e: e.event_id)


def _take(ranked, limit, picked):
    """ホストごとに `limit` 件まで `picked` へ入れる。既に入っている分は数えない。"""
    if limit <= 0:
        return
    per_host = {}
    for event in ranked:
        if event.event_id in picked:
            continue
        host = extract_host(event.series_id)
        used = per_host.get(host, 0)
        if used >= limit:
            continue
        per_host[host] = used + 1
        picked[event.event_id] = event


def window_of(event, pad_ratio):
    """描画する時間の範囲を決める。

    イベントの区間の前後に余白を取る。**余白が無いと「その前どうだったか」が
    分からず、異常かどうかを目で判断できない。**
    """
    span = (event.end_ts - event.start_ts).total_seconds()
    pad = max(span * pad_ratio, MIN_PAD_SECONDS)
    pad = min(pad, MAX_PAD_SECONDS)
    delta = timedelta(seconds=pad)
    return event.start_ts - delta, event.end_ts + delta


def fetch_window(con, series_id, metric, t_from, t_to):
    """指定した範囲の点列を返す。**ts 昇順。**"""
    return con.execute(
        "SELECT ts, value FROM metrics WHERE series_id = ? AND metric = ? "
        "AND ts BETWEEN ? AND ? ORDER BY ts",
        [series_id, metric, t_from, t_to],
    ).fetchall()


def series_label(series_id, metric) -> str:
    return "{0}  {1}".format(series_id, metric)


def _span_indices(stamps, start_ts, end_ts):
    """網掛けする区間の添字を返す。範囲外なら (None, None)。"""
    i_from = i_to = None
    for i, ts in enumerate(stamps):
        if i_from is None and ts >= start_ts:
            i_from = i
        if ts <= end_ts:
            i_to = i
    if i_from is None or i_to is None or i_to < i_from:
        return None, None
    return i_from, i_to


def build_for_event(con, event, cfg, by_id, timezone="UTC"):
    """1 イベントぶんの SVG を組み立てる。

    戻り値は `(SVG の文字列, 生の点列)`。

    **点数が `MIN_POINTS` に満たないときは SVG を None にして点列だけを返す。**
    折れ線として描いても線に見えないためだが、**図が出ないと「ツールの不備」に
    見える**ので、呼び出し側がその点列を表形式で出す (※CR-009。2026-09-06 の指示)。
    """
    t_from, t_to = window_of(event, cfg.chart_pad_ratio)
    rows = thin_out(fetch_window(con, event.series_id, event.metric,
                                 t_from, t_to))
    if len(rows) < MIN_POINTS:
        return None, rows

    stamps = [r[0] for r in rows]
    main = {
        "label": series_label(event.series_id, event.metric),
        "values": [float(r[1]) for r in rows],
        "stamps": [t.strftime("%Y-%m-%d %H:%M:%S") for t in (stamps[0], stamps[-1])],
    }

    # **重ね合わせは「同時に発生したアノマリー」(CR-002) の相手を使う。**
    # 相手は既に「同一ホスト優先 → 脅威度 → 重なり時間」で並んでいる。
    overlays = []
    for co in (event.co_anomalies or [])[:MAX_OVERLAY_SERIES]:
        other = by_id.get(co.event_id)
        if other is None:
            continue
        o_rows = fetch_window(con, other.series_id, other.metric, t_from, t_to)
        # **本体と同じ点数に揃える。** 揃えないと横軸がずれて重ならない。
        o_rows = _align(o_rows, stamps)
        if o_rows is None:
            continue
        overlays.append({
            "label": series_label(other.series_id, other.metric),
            "values": o_rows,
        })

    span = _span_indices(stamps, event.start_ts, event.end_ts)
    return build_svg(main, overlays, span=span, timezone=timezone), rows


def _align(rows, stamps):
    """相手の点列を本体の時刻列に合わせる。

    **同じ時刻の点が無ければ直前の値を使う** (階段状に保持する)。
    採取間隔が系列ごとに違うため、単純に並べると横軸がずれる。
    有効な点が 1 つも無ければ None を返す。
    """
    if not rows:
        return None
    out = []
    index = 0
    last = None
    for ts in stamps:
        while index < len(rows) and rows[index][0] <= ts:
            last = float(rows[index][1])
            index += 1
        out.append(last if last is not None else float(rows[0][1]))
    return out
