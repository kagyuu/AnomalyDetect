"""U008 の単体テスト: ALG-C1(観点3 = 上限への張り付き)と、
系列単位のキャッシュ(※CR-006)。

**ALG-C1 が捉えるのは「変化」ではなく「状態」である。** 観点1(外れ値)・
観点2(増加)がいずれも変化を見るのに対し、本アルゴリズムは
「上限に達して頭打ちのまま平坦」という、**変化していない状態**を検知する。
したがって「増えていないこと」を理由に検知されない、という試験が要になる。
"""

import io
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import bootstrap, config, detectors, progress as progress_mod
from s_anomaly.detectors import base
from s_anomaly.detectors.c1_saturation import MIN_DISTINCT_VALUES

BASE_TS = datetime(2026, 6, 1, 0, 0, 0)
INTERVAL_MIN = 5
#: 既定の min_minutes(360 分)を確実に超える点数。5 分間隔で 6 時間 = 72 点。
HOLD_POINTS = 90


def make_progress():
    return progress_mod.setup(io.StringIO(), io.StringIO())


class Meta(object):
    def __init__(self, series_id="s/1", metric="m", source="jvm_gc",
                 n_points=0, interval_sec=300.0):
        self.series_id = series_id
        self.metric = metric
        self.source = source
        self.n_points = n_points
        self.interval_sec = interval_sec
        self.t_min = None
        self.t_max = None


class CFixture(unittest.TestCase):
    def setUp(self):
        self.progress = make_progress()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        module, _ = bootstrap.load_duckdb(self._tmp.name, self.progress)
        self.con = bootstrap.open_connection(
            module, "512MB", os.path.join(self._tmp.name, "tmp"), self.progress
        )
        self.addCleanup(self.con.close)
        self.con.execute(
            "CREATE TABLE metrics (series_id VARCHAR, source VARCHAR,"
            " metric VARCHAR, ts TIMESTAMP, value DOUBLE, segment INTEGER)"
        )
        self.cfg = config.load(None, self.progress)
        self._counter = 0
        self.addCleanup(base.end_series)

    def load(self, values, metric="m", source="jvm_gc", series_id=None,
             segments=None):
        if series_id is None:
            self._counter += 1
            series_id = "s/{0}".format(self._counter)
        rows = []
        for i, v in enumerate(values):
            seg = 0 if segments is None else segments[i]
            rows.append((series_id, source, metric,
                         BASE_TS + timedelta(minutes=INTERVAL_MIN * i),
                         float(v), seg))
        self.con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)
        return Meta(series_id=series_id, metric=metric, source=source,
                    n_points=len(values), interval_sec=INTERVAL_MIN * 60.0)

    def run_c1(self, meta, cfg=None):
        return detectors.REGISTRY["ALG-C1"].run(
            self.con, cfg or self.cfg, meta, self.progress
        )


def wobble(n, mean, spread, seed=7):
    """distinct 値のガード(条件1)を通すための、細かく揺れる系列。"""
    rng = random.Random(seed)
    return [mean + rng.uniform(-spread, spread) for _ in range(n)]


# ---------------------------------------------------------------------------
# 検知する場合
# ---------------------------------------------------------------------------
class TestDetects(CFixture):
    def test_pinned_at_estimated_ceiling(self):
        """低い水準から立ち上がり、推定上限の 90% 以上で張り付き続ける。"""
        values = wobble(200, 20.0, 3.0) + wobble(HOLD_POINTS, 99.0, 1.0, seed=8)
        meta = self.load(values)
        dets, skips = self.run_c1(meta)
        self.assertEqual(skips, [])
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].algorithm, "ALG-C1")
        self.assertEqual(dets[0].shape, "sustained")
        self.assertTrue(dets[0].detail["ceiling_estimated"])
        self.assertEqual(dets[0].detail["measured_on"], "m")

    def test_pinned_from_the_very_beginning(self):
        """**期間の最初から張り付いている場合も検知する。**

        観点2 は「増加」を見るため、最初から平坦だと形状が出ず取りこぼす。
        これを拾えることが CR-005 の主目的である。
        """
        values = wobble(HOLD_POINTS, 99.0, 1.0) + wobble(200, 20.0, 3.0, seed=9)
        meta = self.load(values)
        dets, _ = self.run_c1(meta)
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].start_ts, BASE_TS)

    def test_uses_real_ceiling_when_pct_exists(self):
        """`ou_pct` が導出済みなら、推定ではなく実際の上限 100 を使う。"""
        meta = self.load(wobble(200, 300.0, 20.0) + wobble(HOLD_POINTS, 900.0, 5.0),
                         metric="ou")
        # 同じ系列に使用率メトリクスを入れる (容量 1000 相当)
        self.load(wobble(200, 30.0, 2.0) + wobble(HOLD_POINTS, 95.0, 1.0, seed=8),
                  metric="ou_pct", series_id=meta.series_id)
        dets, skips = self.run_c1(meta)
        self.assertEqual(skips, [])
        self.assertEqual(len(dets), 1)
        self.assertFalse(dets[0].detail["ceiling_estimated"])
        self.assertEqual(dets[0].detail["ceiling"], 100.0)
        self.assertEqual(dets[0].detail["measured_on"], "ou_pct")

    def test_score_grows_with_duration(self):
        short = self.load(wobble(200, 20.0, 3.0) + wobble(HOLD_POINTS, 99.0, 1.0, seed=8))
        long_ = self.load(wobble(200, 20.0, 3.0) + wobble(HOLD_POINTS * 3, 99.0, 1.0, seed=8))
        s_short = self.run_c1(short)[0][0].score
        s_long = self.run_c1(long_)[0][0].score
        self.assertGreater(s_long, s_short)


# ---------------------------------------------------------------------------
# 検知しない場合 (誤検知の抑止)
# ---------------------------------------------------------------------------
class TestSuppressed(CFixture):
    def test_flag_series_is_skipped(self):
        """**0/1 のフラグ系列を検知しない。**

        推定上限が 1 になり「上限 1 の 90% 以上が続いた」と必ず成立してしまう。
        実データ(`susp`)で実際に誤検知した事象に対する回帰試験である。
        """
        rng = random.Random(3)
        values = [float(rng.randint(0, 1)) for _ in range(400)]
        meta = self.load(values)
        dets, skips = self.run_c1(meta)
        self.assertEqual(dets, [])
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0].reason, "分布が広く適用不可")

    def test_narrow_range_series_is_skipped(self):
        """常時ほぼ一定の系列を「張り付き」と誤認しない(変動幅のガード)。"""
        meta = self.load(wobble(400, 100.0, 4.0))
        dets, skips = self.run_c1(meta)
        self.assertEqual(dets, [])
        self.assertEqual(len(skips), 1)

    def test_zero_floor_does_not_bypass_the_guard(self):
        """**一度でも 0 を取る系列でガードが素通りしないこと。**

        最小値で比を取ると 0 除算・無限大になりガードが効かなくなるため、
        5 パーセンタイルを使っている。その回帰試験である。
        """
        values = wobble(400, 100.0, 4.0)
        values[0] = 0.0
        meta = self.load(values)
        dets, skips = self.run_c1(meta)
        self.assertEqual(dets, [])
        self.assertEqual(len(skips), 1)

    def test_short_spike_is_not_detected(self):
        """継続時間が min_minutes に満たない山は検知しない(観点1 の領分)。"""
        values = wobble(300, 20.0, 3.0)
        for i in range(150, 160):     # 50 分だけ上限付近
            values[i] = 99.0
        meta = self.load(values)
        dets, _ = self.run_c1(meta)
        self.assertEqual(dets, [])

    def test_cumulative_metrics_are_out_of_scope(self):
        """上限の概念が無いメトリクスは対象外(FR-058)。スキップにもしない。"""
        values = wobble(200, 20.0, 3.0) + wobble(HOLD_POINTS, 99.0, 1.0, seed=8)
        for source, metric in [("jvm_gc", "fgc_delta"), ("lsf_queue", "njobs")]:
            meta = self.load(values, metric=metric, source=source)
            dets, skips = self.run_c1(meta)
            self.assertEqual((dets, skips), ([], []), metric)

    def test_too_few_points(self):
        meta = self.load(wobble(10, 50.0, 20.0))
        dets, skips = self.run_c1(meta)
        self.assertEqual(dets, [])
        self.assertEqual(skips[0].reason, "点数不足")

    def test_distinct_threshold_is_the_documented_one(self):
        self.assertEqual(MIN_DISTINCT_VALUES, 10)


# ---------------------------------------------------------------------------
# 系列単位のキャッシュ (※CR-006)
# ---------------------------------------------------------------------------
class TestSeriesCache(CFixture):
    def _count_queries(self):
        """`fetch_series` が実際に DB へ問い合わせた回数を数える。"""
        calls = []
        real = self.con.execute

        class Counting(object):
            def execute(inner, sql, *args, **kwargs):
                calls.append(sql)
                return real(sql, *args, **kwargs)

        return Counting(), calls

    def test_second_fetch_hits_the_cache(self):
        meta = self.load(wobble(100, 50.0, 5.0))
        con, calls = self._count_queries()
        base.begin_series(meta)
        try:
            first = base.fetch_series(con, meta)
            second = base.fetch_series(con, meta)
        finally:
            base.end_series()
        self.assertEqual(len(calls), 1)          # 2 回目は問い合わせない
        self.assertIs(first, second)

    def test_segments_are_cached_too(self):
        meta = self.load(wobble(100, 50.0, 5.0))
        con, calls = self._count_queries()
        base.begin_series(meta)
        try:
            a = base.fetch_segments(con, meta)
            b = base.fetch_segments(con, meta)
        finally:
            base.end_series()
        self.assertEqual(len(calls), 1)
        self.assertIs(a, b)

    def test_without_begin_series_it_still_queries(self):
        """**単体テストは検知器を直接呼ぶため、この経路が保たれること**(DS-08-01c)。"""
        meta = self.load(wobble(100, 50.0, 5.0))
        con, calls = self._count_queries()
        base.end_series()
        base.fetch_series(con, meta)
        base.fetch_series(con, meta)
        self.assertEqual(len(calls), 2)

    def test_cache_is_replaced_on_next_series(self):
        first = self.load(wobble(100, 10.0, 1.0))
        second = self.load(wobble(100, 900.0, 1.0))
        base.begin_series(first)
        got_first = base.fetch_series(self.con, first)
        base.begin_series(second)
        got_second = base.fetch_series(self.con, second)
        base.end_series()
        self.assertNotEqual(got_first[0][1], got_second[0][1])

    def test_segments_match_group_by_segment(self):
        """キャッシュ経路と従来経路が同じ分割を返すこと。"""
        meta = self.load(wobble(60, 50.0, 5.0) + wobble(60, 50.0, 5.0, seed=9),
                         segments=[0] * 60 + [1] * 60)
        base.begin_series(meta)
        try:
            cached = base.fetch_segments(self.con, meta)
        finally:
            base.end_series()
        plain = base.group_by_segment(base.fetch_series(self.con, meta))
        self.assertEqual(cached, plain)
        self.assertEqual(len(cached), 2)


if __name__ == "__main__":
    unittest.main()
