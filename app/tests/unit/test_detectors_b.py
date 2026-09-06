"""U006 の単体テスト: ALG-B1〜B5(観点2 = 持続的な増加)。"""

import io
import math
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import bootstrap, config, detectors, progress as progress_mod
from s_anomaly.detectors import base
from s_anomaly.detectors.b2_mann_kendall import mann_kendall, sen_slope

BASE_TS = datetime(2026, 6, 1, 0, 0, 0)
INTERVAL_MIN = 5
DAY_POINTS = 288


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


class BFixture(unittest.TestCase):
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

    def load(self, values, segments=None, interval_min=INTERVAL_MIN):
        self._counter += 1
        series_id = "s/{0}".format(self._counter)
        rows = []
        for i, v in enumerate(values):
            seg = 0 if segments is None else segments[i]
            rows.append((series_id, "jvm_gc", "m",
                         BASE_TS + timedelta(minutes=interval_min * i),
                         float(v), seg))
        self.con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)
        return Meta(series_id=series_id, n_points=len(values),
                    interval_sec=interval_min * 60.0)

    def run_detector(self, alg_id, meta, cfg=None):
        return detectors.REGISTRY[alg_id].run(
            self.con, cfg or self.cfg, meta, self.progress
        )


def flat(n, mean=50.0, sigma=1.0, seed=1):
    rng = random.Random(seed)
    return [mean + rng.gauss(0.0, sigma) for _ in range(n)]


def ramp(n, start=10.0, end=200.0, sigma=1.0, seed=2):
    rng = random.Random(seed)
    return [start + (end - start) * i / float(n - 1) + rng.gauss(0.0, sigma)
            for i in range(n)]


def sawtooth(n, floor_start, floor_end, amplitude=6.0, period=24, sigma=0.3,
             seed=3):
    """鋸歯状。下限が floor_start -> floor_end へ動く。"""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        floor = floor_start + (floor_end - floor_start) * i / float(n - 1)
        out.append(floor + amplitude * ((i % period) / float(period))
                   + rng.gauss(0.0, sigma))
    return out


def daily_cycle(n, seed=4, extra=None):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        v = 30.0 + 20.0 * (1.0 + math.sin(2 * math.pi * (i % DAY_POINTS)
                                          / DAY_POINTS - math.pi / 2.0))
        v += rng.gauss(0.0, 1.5)
        if extra is not None:
            v += extra(i)
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# ALG-B1
# ---------------------------------------------------------------------------
class TestB1(BFixture):
    def test_detects_second_half_rise(self):
        n = DAY_POINTS * 8
        half = n // 2
        values = flat(n, seed=11)
        for i in range(half, n):
            values[i] += 0.08 * (i - half)
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B1", meta)
        self.assertTrue(dets)
        self.assertEqual(dets[0].shape, base.SHAPE_TREND_UP)
        self.assertGreaterEqual(len(dets[0].values), 2)

    def test_flat_series_no_detection(self):
        meta = self.load(flat(DAY_POINTS * 5, seed=12))
        dets, _ = self.run_detector("ALG-B1", meta)
        self.assertEqual(dets, [])

    def test_daily_cycle_alone_no_detection(self):
        """周期だけでは検知しないこと(連続点数の下限が長期窓を超える)。"""
        meta = self.load(daily_cycle(DAY_POINTS * 7, seed=13))
        dets, _ = self.run_detector("ALG-B1", meta)
        self.assertEqual(dets, [], dets[:2])

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * (DAY_POINTS * 3))
        dets, _ = self.run_detector("ALG-B1", meta)
        self.assertEqual(dets, [])

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector("ALG-B1", meta)
        self.assertEqual(len(skips), 1)

    def test_series_shorter_than_long_window(self):
        meta = self.load(ramp(100))  # 100 点 = 約 8 時間 < 24 時間
        dets, _ = self.run_detector("ALG-B1", meta)
        self.assertEqual(dets, [])

    def test_values_sampled_to_nine(self):
        n = DAY_POINTS * 8
        meta = self.load(ramp(n, 10.0, 300.0, sigma=1.0, seed=14))
        dets, _ = self.run_detector("ALG-B1", meta)
        self.assertTrue(dets)
        self.assertLessEqual(len(dets[0].values), 9)


# ---------------------------------------------------------------------------
# ALG-B2
# ---------------------------------------------------------------------------
class TestMannKendallMath(unittest.TestCase):
    def test_monotonic_increase(self):
        s, z, p = mann_kendall([1, 2, 3, 4, 5])
        self.assertEqual(s, 10)
        self.assertLess(p, 0.1)

    def test_monotonic_decrease(self):
        s, z, p = mann_kendall([5, 4, 3, 2, 1])
        self.assertEqual(s, -10)

    def test_all_equal(self):
        s, z, p = mann_kendall([3, 3, 3, 3, 3])
        self.assertEqual(s, 0)
        self.assertEqual(p, 1.0)

    def test_tie_correction_changes_variance(self):
        without_ties = mann_kendall([1, 2, 3, 4, 5, 6, 7, 8])
        with_ties = mann_kendall([1, 1, 1, 4, 5, 6, 7, 8])
        self.assertNotEqual(without_ties[1], with_ties[1])

    def test_sen_slope_linear(self):
        self.assertAlmostEqual(sen_slope([0, 2, 4, 6, 8]), 2.0)

    def test_sen_slope_empty(self):
        self.assertEqual(sen_slope([1.0]), 0.0)


class TestB2(BFixture):
    def test_known_slope(self):
        n = DAY_POINTS * 7
        # 1 日あたり +10 の直線
        values = [10.0 + 10.0 * (i / float(DAY_POINTS)) for i in range(n)]
        rng = random.Random(21)
        values = [v + rng.gauss(0.0, 0.5) for v in values]
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B2", meta)
        self.assertTrue(dets)
        got = dets[0].detail["sen_slope_per_day"]
        self.assertAlmostEqual(got, 10.0, delta=1.0)
        self.assertLess(dets[0].detail["p"], 0.01)

    def test_no_trend_not_detected(self):
        meta = self.load(flat(DAY_POINTS * 7, seed=22))
        dets, _ = self.run_detector("ALG-B2", meta)
        self.assertEqual(dets, [])

    def test_decreasing_not_detected(self):
        n = DAY_POINTS * 7
        values = [200.0 - 10.0 * (i / float(DAY_POINTS)) for i in range(n)]
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B2", meta)
        self.assertEqual(dets, [])

    def test_constant_no_division_error(self):
        meta = self.load([7.0] * (DAY_POINTS * 3))
        dets, _ = self.run_detector("ALG-B2", meta)
        self.assertEqual(dets, [])

    def test_bucket_expansion(self):
        """バケット数が上限を超えると bucket_minutes が自動的に拡大される。"""
        cfg = _cfg_with(self.cfg, {"ALG-B2.max_buckets": 100})
        n = DAY_POINTS * 14
        meta = self.load(ramp(n, 10.0, 200.0, seed=23))
        dets, _ = self.run_detector("ALG-B2", meta, cfg)
        self.assertTrue(dets)
        self.assertGreater(dets[0].detail["bucket_minutes"], 60)
        self.assertLessEqual(dets[0].detail["n_buckets"], 100)

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector("ALG-B2", meta)
        self.assertEqual(len(skips), 1)


def _cfg_with(cfg, overrides):
    values = dict(cfg.as_dict())
    for key, value in overrides.items():
        values[("parameters", key)] = value
    return config.Config(values)


# ---------------------------------------------------------------------------
# ALG-B3
# ---------------------------------------------------------------------------
class TestB3(BFixture):
    def test_detects_rising_floor(self):
        """鋸歯状で下限が上昇する系列(メモリリーク模擬)を検知する。"""
        n = DAY_POINTS * 14
        meta = self.load(sawtooth(n, floor_start=40.0, floor_end=92.0, seed=31))
        dets, _ = self.run_detector("ALG-B3", meta)
        self.assertTrue(dets, "メモリリーク模擬を検知していない")
        self.assertEqual(dets[0].shape, base.SHAPE_FLOOR_RISE)
        self.assertGreater(dets[0].detail["total_rise"], 40.0)

    def test_flat_floor_not_detected(self):
        """同じ鋸歯でも下限が一定なら検知しないこと。"""
        n = DAY_POINTS * 14
        meta = self.load(sawtooth(n, floor_start=40.0, floor_end=40.0, seed=32))
        dets, _ = self.run_detector("ALG-B3", meta)
        self.assertEqual(dets, [], dets[:2])

    def test_values_are_floor_envelope(self):
        n = DAY_POINTS * 14
        meta = self.load(sawtooth(n, 40.0, 92.0, seed=33))
        dets, _ = self.run_detector("ALG-B3", meta)
        floors = dets[0].values
        self.assertTrue(all(floors[i] <= floors[i + 1] + 1e-9
                            for i in range(len(floors) - 1)), floors)

    def test_gap_breaks_run(self):
        """欠測バケットで run が分断されること。"""
        n = DAY_POINTS * 14
        values = sawtooth(n, 40.0, 92.0, seed=34)
        rows = []
        self._counter += 1
        series_id = "s/{0}".format(self._counter)
        for i, v in enumerate(values):
            # 中央の 1 日分を欠測させる
            if DAY_POINTS * 6 <= i < DAY_POINTS * 7:
                continue
            rows.append((series_id, "jvm_gc", "m",
                         BASE_TS + timedelta(minutes=INTERVAL_MIN * i),
                         float(v), 0))
        self.con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)
        meta = Meta(series_id=series_id, n_points=len(rows), interval_sec=300.0)
        dets, _ = self.run_detector("ALG-B3", meta)
        # 欠測で分断されるため、全期間 1 件にはならない
        for d in dets:
            span_days = (d.end_ts - d.start_ts).total_seconds() / 86400.0
            self.assertLess(span_days, 13.0)

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * (DAY_POINTS * 5))
        dets, _ = self.run_detector("ALG-B3", meta)
        self.assertEqual(dets, [])

    def test_short_series_no_detection(self):
        meta = self.load(sawtooth(100, 40.0, 92.0, seed=35))
        dets, _ = self.run_detector("ALG-B3", meta)
        self.assertEqual(dets, [])

    def test_segments_independent(self):
        n = DAY_POINTS * 14
        values = sawtooth(n, 40.0, 92.0, seed=36)
        segments = [0] * (n // 2) + [1] * (n - n // 2)
        meta = self.load(values, segments=segments)
        dets, _ = self.run_detector("ALG-B3", meta)
        for d in dets:
            span_days = (d.end_ts - d.start_ts).total_seconds() / 86400.0
            self.assertLess(span_days, 13.0)

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector("ALG-B3", meta)
        self.assertEqual(len(skips), 1)


# ---------------------------------------------------------------------------
# ALG-B4
# ---------------------------------------------------------------------------
class TestB4(BFixture):
    def test_detects_level_shift(self):
        n = DAY_POINTS * 8
        half = n // 2
        rng = random.Random(41)
        values = [50.0 + rng.gauss(0.0, 1.0) for _ in range(n)]
        for i in range(half, n):
            values[i] += 5.0
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B4", meta)
        self.assertTrue(dets)
        self.assertEqual(dets[0].shape, base.SHAPE_LEVEL_SHIFT)
        shift_ts = BASE_TS + timedelta(minutes=INTERVAL_MIN * half)
        nearest = min(dets, key=lambda d: abs((d.end_ts - shift_ts).total_seconds()))
        self.assertLess(abs((nearest.end_ts - shift_ts).total_seconds()), 6 * 3600)

    def test_flat_series_few_detections(self):
        meta = self.load(flat(DAY_POINTS * 7, seed=42))
        dets, _ = self.run_detector("ALG-B4", meta)
        self.assertLess(len(dets), DAY_POINTS * 7 * 0.05)

    def test_daily_cycle_not_flooded(self):
        """周期だけの系列で暴走しないこと(季節差分の効果)。"""
        n = DAY_POINTS * 7
        meta = self.load(daily_cycle(n, seed=43))
        dets, _ = self.run_detector("ALG-B4", meta)
        self.assertLess(len(dets), n * 0.05, len(dets))

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * (DAY_POINTS * 5))
        dets, _ = self.run_detector("ALG-B4", meta)
        self.assertEqual(dets, [])

    def test_larger_h_reduces_detections(self):
        """h を大きくすると検知が減ること(閾値が効いていることの確認)。

        水準シフトが続く限り季節差分は積み上がるため、h をいくら大きくしても
        0 件にはならない。閾値の効果は単調性で確認する。
        """
        n = DAY_POINTS * 8
        rng = random.Random(44)
        values = [50.0 + rng.gauss(0.0, 1.0) for _ in range(n)]
        for i in range(n // 2, n):
            values[i] += 5.0
        meta = self.load(values)
        low, _ = self.run_detector(
            "ALG-B4", meta, _cfg_with(self.cfg, {"ALG-B4.h": 2.0})
        )
        high, _ = self.run_detector(
            "ALG-B4", meta, _cfg_with(self.cfg, {"ALG-B4.h": 100.0})
        )
        self.assertGreater(len(low), len(high))

    def test_without_24h_history_skipped(self):
        meta = self.load(flat(100, seed=45))
        dets, skips = self.run_detector("ALG-B4", meta)
        self.assertEqual(dets, [])
        self.assertTrue(skips)
        self.assertEqual(skips[0].reason, base.SKIP_NO_BASELINE)

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector("ALG-B4", meta)
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0].reason, base.SKIP_FEW_POINTS)


# ---------------------------------------------------------------------------
# ALG-B5
# ---------------------------------------------------------------------------
class TestB5(BFixture):
    def test_known_slope_per_day(self):
        n = DAY_POINTS * 7
        values = [10.0 + 10.0 * (i / float(DAY_POINTS)) for i in range(n)]
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B5", meta)
        self.assertTrue(dets)
        self.assertAlmostEqual(dets[0].detail["slope_per_day"], 10.0, delta=0.5)
        self.assertGreater(dets[0].detail["r2"], 0.99)
        self.assertEqual(dets[0].shape, base.SHAPE_TREND_UP)

    def test_flat_not_detected(self):
        meta = self.load(flat(DAY_POINTS * 7, seed=51))
        dets, _ = self.run_detector("ALG-B5", meta)
        self.assertEqual(dets, [])

    def test_decreasing_not_detected(self):
        n = DAY_POINTS * 7
        values = [200.0 - 10.0 * (i / float(DAY_POINTS)) for i in range(n)]
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B5", meta)
        self.assertEqual(dets, [])

    def test_high_r2_but_tiny_increase_not_detected(self):
        """R² は高いが増加量が微小な系列は検知しないこと。"""
        n = DAY_POINTS * 7
        values = [50.0 + 0.00001 * i for i in range(n)]
        meta = self.load(values)
        dets, _ = self.run_detector("ALG-B5", meta)
        self.assertEqual(dets, [])

    def test_constant_no_error(self):
        meta = self.load([7.0] * (DAY_POINTS * 3))
        dets, _ = self.run_detector("ALG-B5", meta)
        self.assertEqual(dets, [])

    def test_two_points_no_error(self):
        meta = self.load([1.0, 2.0])
        dets, skips = self.run_detector("ALG-B5", meta)
        self.assertEqual(dets, [])
        self.assertEqual(len(skips), 1)


# ---------------------------------------------------------------------------
# レジストリ (11 個そろった状態。※CR-005 で ALG-C1 が加わった)
# ---------------------------------------------------------------------------
class TestRegistryComplete(unittest.TestCase):
    def test_eleven_detectors(self):
        self.assertEqual(len(detectors.REGISTRY), 11)
        expected = set(["ALG-A{0}".format(i) for i in range(1, 6)]
                       + ["ALG-B{0}".format(i) for i in range(1, 6)]
                       + ["ALG-C1"])
        self.assertEqual(set(detectors.REGISTRY), expected)

    def test_ids_match_keys(self):
        for alg_id, det in detectors.REGISTRY.items():
            self.assertEqual(alg_id, det.id)
            self.assertTrue(isinstance(det, base.Detector))
            self.assertTrue(det.name)

    def test_aspect_split(self):
        aspects = {}
        for det in detectors.REGISTRY.values():
            aspects[det.aspect] = aspects.get(det.aspect, 0) + 1
        # ※CR-005 観点3 (上限への張り付き) を追加した
        self.assertEqual(aspects, {"観点1": 5, "観点2": 5, "観点3": 1})

    def test_enabled_sorted(self):
        cfg = config.load(None, make_progress())
        ids = [d.id for d in detectors.enabled_detectors(cfg)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), 11)   # ※CR-005


class TestSeasonalDiffs(unittest.TestCase):
    def test_removes_daily_cycle(self):
        points = []
        for i in range(DAY_POINTS * 3):
            ts = BASE_TS + timedelta(minutes=INTERVAL_MIN * i)
            value = 50.0 + 20.0 * math.sin(2 * math.pi * (i % DAY_POINTS) / DAY_POINTS)
            points.append((ts, value))
        diffs = base.seasonal_diffs(points)
        self.assertTrue(diffs)
        self.assertLess(max(abs(d) for _i, d in diffs), 0.01)

    def test_keeps_level_shift(self):
        points = []
        for i in range(DAY_POINTS * 3):
            ts = BASE_TS + timedelta(minutes=INTERVAL_MIN * i)
            value = 50.0 + (10.0 if i >= DAY_POINTS * 2 else 0.0)
            points.append((ts, value))
        diffs = base.seasonal_diffs(points)
        self.assertAlmostEqual(max(d for _i, d in diffs), 10.0, delta=0.01)

    def test_short_series_returns_empty(self):
        points = [(BASE_TS + timedelta(minutes=5 * i), 1.0) for i in range(10)]
        self.assertEqual(base.seasonal_diffs(points), [])


class TestNoiseScale(unittest.TestCase):
    def test_insensitive_to_trend(self):
        rng = random.Random(61)
        flat_points = [(BASE_TS + timedelta(minutes=5 * i), 50.0 + rng.gauss(0, 1.0))
                       for i in range(1000)]
        rng2 = random.Random(61)
        trend_points = [(BASE_TS + timedelta(minutes=5 * i),
                         50.0 + 0.2 * i + rng2.gauss(0, 1.0))
                        for i in range(1000)]
        a = base.noise_scale(flat_points)
        b = base.noise_scale(trend_points)
        self.assertAlmostEqual(a, b, delta=max(a, b) * 0.5)

    def test_short_series(self):
        self.assertEqual(base.noise_scale([(BASE_TS, 1.0)]), 0.0)


if __name__ == "__main__":
    unittest.main()
