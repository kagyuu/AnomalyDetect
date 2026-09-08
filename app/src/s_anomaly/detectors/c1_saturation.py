"""ALG-C1 上限への張り付き (P001 10.2a / P003 7.14)。※CR-005 により新設。

**観点1・観点2 がいずれも「変化」を捉えるのに対し、本アルゴリズムは「状態」を
捉える。** 「上限に達して頭打ちのまま平坦」という状態は、外れ値でも増加傾向でも
ないため、既存の 10 アルゴリズムでは検知できない。

依頼者の要求は「**DBConnection や Java のメモリが上限値に張りついていることを
知りたい**」である。

**上限値は入力に含まれていないため、解析期間中の実測最大値から推定する** (FR-056)。
入力に上限の列を足せるならそちらが本筋だが、運用管理側に設定値の変更を伝えられない
場合が多いという事情から、推定方式を採った。

**計算は SQL で行う** (FR-059)。窓関数で「上限比が閾値以上の状態が続いた長さ」を
求めるため、系列長に対して 1 パスで済む。
"""

from .base import (
    SHAPE_SUSTAINED, SKIP_FEW_POINTS, SKIP_WIDE_DISTRIBUTION,
    Detection, Detector, sample_values, score_to_hint,
)

#: 上限の概念が無いメトリクス (FR-058)。累積回数・時間や、上限が自明でないもの。
#:
#: ※CR-010 sar の 14 件を追加した。**とくに `pct_idle` の除外は必須である。**
#: 除外し忘れると、**閑散なホストが全件「上限に張り付き」として検知される**
#: (100% の遊休は正常であって異常ではない。P001 FR-148)。
#: `kbmemfree` は「小さいほど逼迫している」向きであり、観点3 が捉える
#: 「高い値で止まっている」状態とは向きが逆である。空きメモリの逼迫は
#: `pct_memused` の側で捉える。
NO_CEILING = {
    "jvm_gc": ("fgc_delta", "fgct_delta", "ygct_delta"),
    "lsf_queue": ("njobs", "run"),
    "sar": ("pct_idle", "kbmemfree", "tps", "bread_s", "bwrtn_s",
            "pswpin_s", "pswpout_s", "runq_sz", "ldavg_1", "ldavg_5",
            "blocked", "await", "rxkb_s", "txkb_s",
            # ※CR-011 NFS。**すべて毎秒のレート値であり上限が自明でない。**
            "call_s", "retrans_s", "read_s", "write_s", "access_s", "getatt_s",
            "scall_s", "badcall_s", "hit_s", "miss_s",
            "sread_s", "swrite_s", "saccess_s", "sgetatt_s"),
}

#: 値の解像度の下限 (FR-057 の条件 3)。これ未満の distinct 値しか持たない系列は、
#  推定上限が意味を持たないため対象外にする (0/1 のフラグ系列など)。
MIN_DISTINCT_VALUES = 10

#: 実際の容量が取れる場合に使う列 (推定より優先する。FR-056)。
#  `-gc` 形式のときだけ容量列があり、`*_pct` が導出されている。
RATIO_OF = {"ou": "ou_pct", "eu": "eu_pct", "mu": "mu_pct"}

#: ※CR-010 **それ自身が 0〜100 の比率であるメトリクス** (DS-08-C1-01a)。
#:
#: `ou` → `ou_pct` のような**別メトリクスへの読み替えを伴わない**点が
#: `RATIO_OF` と異なる。`RATIO_OF` に自己写像を入れると、「読み替え先の点数が
#: 足りるか」を確認する分岐を無意味に通るため、別の集合として持つ。
#:
#: **上限は 100 であり、ADR-016 の推定上限 (実測最大値) を使わない。**
#: **`pct_idle` は含めない** — 100% は遊休であり正常である (上の NO_CEILING)。
SELF_RATIO_METRICS = frozenset([
    "pct_user", "pct_system", "pct_iowait", "pct_memused",
    "pct_commit", "pct_swpused", "pct_util", "pct_ifutil",
])


class C1Saturation(Detector):
    id = "ALG-C1"
    name = "上限への張り付き"
    aspect = "観点3"

    def run(self, con, cfg, series, progress):
        if series.metric in NO_CEILING.get(series.source, ()):
            return [], []

        ratio_pct = float(cfg.param("ALG-C1.ratio_pct"))
        min_minutes = float(cfg.param("ALG-C1.min_minutes"))
        min_spread = float(cfg.param("ALG-C1.min_spread"))
        min_points = self.min_points(cfg)

        # 使用率メトリクスが導出できている場合は、**推定ではなく実際の容量**を使う。
        # その場合の「上限」は 100(%) である。
        pct_metric = RATIO_OF.get(series.metric)
        target_metric = series.metric
        ceiling = None
        # ※CR-010 sar が直接出す比率は、それ自身が 0〜100 である。
        # **読み替えをせず、上限 100 をそのまま使う** (DS-08-C1-01a)。
        if series.metric in SELF_RATIO_METRICS:
            ceiling = 100.0
        elif pct_metric is not None:
            row = con.execute(
                "SELECT count(*) FROM metrics WHERE series_id = ? AND metric = ?",
                [series.series_id, pct_metric],
            ).fetchone()
            if row and row[0] >= min_points:
                target_metric = pct_metric
                ceiling = 100.0

        stat = con.execute(
            "SELECT count(*), min(value), max(value) FROM metrics "
            "WHERE series_id = ? AND metric = ?",
            [series.series_id, target_metric],
        ).fetchone()
        if not stat or stat[0] < min_points:
            return [], [self.skip(series, SKIP_FEW_POINTS)]
        n_points, v_min, v_max = stat
        if v_max is None or v_min is None or v_max <= 0:
            return [], []

        estimated = ceiling is None
        if ceiling is None:
            ceiling = float(v_max)

        # 誤検知を抑える 2 段のガード (FR-057 の条件 3)。
        #
        # **なぜ 2 段いるか (いずれも実測にもとづく)**
        #
        # (1) 値の解像度: `susp` のように 0/1 の 2 値しか取らない系列は、
        #     推定上限が 1 になり「上限 1 の 90% 以上が続いた」と必ず検知される。
        #     これは張り付きではなく、単に値域が狭いだけである。
        #     **distinct 値の数**で構造的に弾く。
        #
        # (2) 変動幅: 常時 9〜10 で推移する系列の上限を 10 と推定すると、
        #     平常の状態が張り付きに見える。**下側の水準に対する最大値の比**で弾く。
        #     ここで最小値ではなく 5 パーセンタイルを使うのは、一度でも 0 を
        #     取ると最小値が 0 になり比が無限大になってガードが素通りするため。
        guard = con.execute(
            "SELECT count(DISTINCT value), quantile_cont(value, 0.05) "
            "FROM metrics WHERE series_id = ? AND metric = ?",
            [series.series_id, target_metric],
        ).fetchone()
        distinct = int(guard[0]) if guard and guard[0] is not None else 0
        floor = float(guard[1]) if guard and guard[1] is not None else 0.0
        if distinct < MIN_DISTINCT_VALUES:
            return [], [self.skip(series, SKIP_WIDE_DISTRIBUTION)]
        if floor > 0 and (float(v_max) / floor) < min_spread:
            return [], [self.skip(series, SKIP_WIDE_DISTRIBUTION)]

        threshold = ceiling * ratio_pct / 100.0

        # 閾値以上の点が連続する区間を求める。
        # `grp` は「閾値未満だった回数の累積」であり、同じ値を持つ行が
        # ひとつながりの区間になる (gaps-and-islands)。
        rows = con.execute(
            """
            WITH pts AS (
                SELECT ts, value, segment,
                       CASE WHEN value >= ? THEN 1 ELSE 0 END AS hot
                FROM metrics WHERE series_id = ? AND metric = ?
            ), grp AS (
                SELECT ts, value, segment, hot,
                       sum(CASE WHEN hot = 1 THEN 0 ELSE 1 END)
                           OVER (PARTITION BY segment ORDER BY ts
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                           AS island
                FROM pts
            )
            SELECT min(ts) AS t_from, max(ts) AS t_to, count(*) AS n,
                   avg(value) AS avg_v, min(value) AS min_v, max(value) AS max_v
            FROM grp
            WHERE hot = 1
            GROUP BY segment, island
            HAVING date_diff('second', min(ts), max(ts)) >= ?
            ORDER BY t_from
            """,
            [threshold, series.series_id, target_metric, int(min_minutes * 60)],
        ).fetchall()
        if not rows:
            return [], []

        detections = []
        for t_from, t_to, n, avg_v, min_v, max_v in rows:
            held = (t_to - t_from).total_seconds()
            # スコアは「必要時間の何倍継続したか」。長いほど強い。
            score = held / (min_minutes * 60.0) if min_minutes > 0 else 1.0
            detections.append(Detection(
                series_id=series.series_id, source=series.source,
                metric=series.metric, algorithm=self.id,
                start_ts=t_from, end_ts=t_to,
                values=sample_values([min_v, avg_v, max_v]),
                score=score, severity_hint=score_to_hint(score),
                detail={
                    "ceiling": ceiling,
                    "ceiling_estimated": estimated,
                    "ratio_pct": ratio_pct,
                    "threshold": threshold,
                    "held_seconds": held,
                    "min_minutes": min_minutes,
                    "avg_value": avg_v,
                    "max_value": max_v,
                    "points": n,
                    "measured_on": target_metric,
                },
                shape=SHAPE_SUSTAINED,
            ))
        return detections, []
