"""M07 metrics — 3 テーブルを統一ビュー `metrics` へ変換する (P003 6章)。

10 個のアルゴリズムは `metrics` のみを入力とし、ログ種別を意識しない。
"""

from typing import List, Optional

STEP = "S5"

#: 検知対象のメトリクス (ADR-005: `*_pct` は含めない)。
DETECTABLE_METRICS = [
    "active_connections",
    "ou", "eu", "mu",
    "fgc_delta", "fgct_delta", "ygct_delta",
    "njobs", "pend", "run", "susp",
]

#: 脅威度判定・原因候補の材料としてのみ使う派生メトリクス。
RATIO_METRICS = ["ou_pct", "eu_pct", "mu_pct"]

#: `ou` -> `ou_pct` の対応 (DS-09-05 の SEVERE 判定で使う)。
RATIO_OF = {"ou": "ou_pct", "eu": "eu_pct", "mu": "mu_pct"}


class SeriesMeta(object):
    """1 系列 (series_id × metric) のメタ情報。"""

    def __init__(self, series_id, source, metric, n_points, interval_sec,
                 t_min, t_max):
        self.series_id = series_id
        self.source = source
        self.metric = metric
        self.n_points = n_points
        self.interval_sec = interval_sec
        self.t_min = t_min
        self.t_max = t_max

    def __repr__(self):
        return "SeriesMeta({0!r}, {1!r}, n={2}, interval={3})".format(
            self.series_id, self.metric, self.n_points, self.interval_sec
        )


def build_jvm_gc_derived(con):
    """JVM 再起動による系列分割と、累積値の差分を持つ中間テーブルを作る。"""
    con.execute(
        """
        CREATE OR REPLACE TABLE jvm_gc_derived AS
        WITH lagged AS (
            SELECT *,
                lag(jvm_uptime_sec) OVER w AS prev_uptime,
                lag(gct)  OVER w AS prev_gct
            FROM jvm_gc
            WINDOW w AS (PARTITION BY container, host ORDER BY ts)
        ), seg AS (
            SELECT *,
                sum(CASE WHEN
                        (jvm_uptime_sec IS NOT NULL AND prev_uptime IS NOT NULL
                         AND jvm_uptime_sec < prev_uptime)
                     OR (jvm_uptime_sec IS NULL AND prev_gct IS NOT NULL
                         AND gct < prev_gct)
                    THEN 1 ELSE 0 END)
                  OVER (PARTITION BY container, host ORDER BY ts
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS segment
            FROM lagged
        )
        SELECT *,
            CASE WHEN segment = lag(segment) OVER w2 AND fgc >= lag(fgc) OVER w2
                 THEN fgc - lag(fgc) OVER w2 END AS fgc_delta,
            CASE WHEN segment = lag(segment) OVER w2 AND fgct >= lag(fgct) OVER w2
                 THEN fgct - lag(fgct) OVER w2 END AS fgct_delta,
            CASE WHEN segment = lag(segment) OVER w2 AND ygct >= lag(ygct) OVER w2
                 THEN ygct - lag(ygct) OVER w2 END AS ygct_delta
        FROM seg
        WINDOW w2 AS (PARTITION BY container, host ORDER BY ts)
        """
    )


def build_metrics(con):
    """`metrics` を TABLE として物理化する (DS-07-09)。"""
    con.execute(
        """
        CREATE OR REPLACE TABLE metrics AS
        WITH db AS (
            SELECT
                'db_connection/' || host || ':' || CAST(port AS VARCHAR)
                    || '/' || datasource AS series_id,
                'db_connection' AS source,
                'active_connections' AS metric,
                ts, CAST(active_connections AS DOUBLE) AS value, 0 AS segment
            FROM db_connection
        ), gc_base AS (
            SELECT
                'jvm_gc/' || container || '@' || host AS series_id,
                ts, segment, gc_format,
                ou, eu, mu, oc, ec, mc,
                fgc_delta, fgct_delta, ygct_delta
            FROM jvm_gc_derived
        ), gc_long AS (
            SELECT series_id, 'jvm_gc' AS source, m.metric, ts,
                   m.value, segment
            FROM gc_base,
            LATERAL (VALUES
                ('ou', ou), ('eu', eu), ('mu', mu),
                ('fgc_delta', CAST(fgc_delta AS DOUBLE)),
                ('fgct_delta', fgct_delta),
                ('ygct_delta', ygct_delta),
                ('ou_pct', CASE WHEN gc_format = 'gcutil' THEN ou
                                WHEN oc IS NOT NULL AND oc > 0 THEN ou / oc * 100.0 END),
                ('eu_pct', CASE WHEN gc_format = 'gcutil' THEN eu
                                WHEN ec IS NOT NULL AND ec > 0 THEN eu / ec * 100.0 END),
                ('mu_pct', CASE WHEN gc_format = 'gcutil' THEN mu
                                WHEN mc IS NOT NULL AND mc > 0 THEN mu / mc * 100.0 END)
            ) AS m(metric, value)
        ), lsf AS (
            SELECT series_id, 'lsf_queue' AS source, m.metric, ts, m.value, 0 AS segment
            FROM (
                SELECT 'lsf_queue/' || host || '/' || queue AS series_id,
                       ts, njobs, pend, run, susp
                FROM lsf_queue
            ) t,
            LATERAL (VALUES
                ('njobs', CAST(njobs AS DOUBLE)),
                ('pend', CAST(pend AS DOUBLE)),
                ('run', CAST(run AS DOUBLE)),
                ('susp', CAST(susp AS DOUBLE))
            ) AS m(metric, value)
        )
        SELECT * FROM (
            SELECT * FROM db
            UNION ALL SELECT * FROM gc_long
            UNION ALL SELECT * FROM lsf
        )
        WHERE value IS NOT NULL
        ORDER BY series_id, metric, ts
        """
    )


def list_series(con) -> List[SeriesMeta]:
    """検知対象メトリクスの系列一覧を返す (series_id, metric の昇順)。"""
    placeholders = ", ".join(["?"] * len(DETECTABLE_METRICS))
    rows = con.execute(
        """
        SELECT series_id, metric, any_value(source) AS source,
               count(*) AS n_points,
               min(ts) AS t_min, max(ts) AS t_max,
               coalesce(median(diff_sec), 0.0) AS interval_sec
        FROM (
            SELECT series_id, metric, source, ts,
                   CASE WHEN d > 0 THEN d END AS diff_sec
            FROM (
                SELECT series_id, metric, source, ts,
                       epoch(ts) - epoch(lag(ts) OVER (
                           PARTITION BY series_id, metric, segment ORDER BY ts
                       )) AS d
                FROM metrics
                WHERE metric IN ({0})
            )
        )
        GROUP BY series_id, metric
        ORDER BY series_id, metric
        """.format(placeholders),
        list(DETECTABLE_METRICS),
    ).fetchall()
    return [
        SeriesMeta(
            series_id=r[0], metric=r[1], source=r[2], n_points=r[3],
            t_min=r[4], t_max=r[5], interval_sec=float(r[6] or 0.0),
        )
        for r in rows
    ]


def summarize(con, progress):
    """S5 の進捗ログを出し、要約を返す。"""
    row = con.execute(
        "SELECT count(*), count(DISTINCT series_id), count(DISTINCT metric),"
        " min(ts), max(ts) FROM metrics"
    ).fetchone()
    total, n_series, n_metrics, t_min, t_max = row
    progress.info(
        STEP,
        "系列 {0} 本 / メトリクス {1} 種 / 対象期間 {2} 〜 {3} / metrics {4} 行".format(
            n_series, n_metrics,
            t_min.strftime("%Y-%m-%d %H:%M:%S") if t_min else "-",
            t_max.strftime("%Y-%m-%d %H:%M:%S") if t_max else "-",
            total,
        ),
    )
    return {
        "series_count": n_series or 0,
        "metric_count": n_metrics or 0,
        "period": (t_min, t_max),
        "metrics_rows": total or 0,
    }
