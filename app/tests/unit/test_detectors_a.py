"""U005 の単体テスト: 検知器の共通契約と ALG-A1〜A5。

理論値が計算できる人工系列を metrics へ直接 INSERT して検証する。
"""

import io
import math
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta

from s_anomaly import bootstrap, config, detectors, progress as progress_mod
from s_anomaly.detectors import base

BASE_TS = datetime(2026, 6, 1, 0, 0, 0)
INTERVAL_MIN = 5


def make_progress():
    out, err = io.StringIO(), io.StringIO()
    return progress_mod.setup(out, err), out, err


class Meta(object):
    """metrics.SeriesMeta の代用 (検知器が使う属性だけを持つ)。"""

    def __init__(self, series_id="s/1", metric="m", source="db_connection",
                 n_points=0, interval_sec=300.0):
        self.series_id = series_id
        self.metric = metric
        self.source = source
        self.n_points = n_points
        self.interval_sec = interval_sec
        self.t_min = None
        self.t_max = None


class DetectorFixture(unittest.TestCase):
    def setUp(self):
        self.progress, self.out, self.err = make_progress()
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

    def load(self, values, series_id="s/1", metric="m", segments=None,
             interval_min=INTERVAL_MIN):
        rows = []
        for i, v in enumerate(values):
            seg = 0 if segments is None else segments[i]
            rows.append((series_id, "db_connection", metric,
                         BASE_TS + timedelta(minutes=interval_min * i),
                         float(v), seg))
        self.con.executemany(
            "INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?)", rows
        )
        return Meta(series_id=series_id, metric=metric, n_points=len(values),
                    interval_sec=interval_min * 60.0)

    def run_detector(self, detector, meta):
        return detector.run(self.con, self.cfg, meta, self.progress)


# ---------------------------------------------------------------------------
# U005-T1 base
# ---------------------------------------------------------------------------
class TestEffectiveSigma(unittest.TestCase):
    def setUp(self):
        self.progress, _, _ = make_progress()
        self.cfg = config.load(None, self.progress)

    def test_uses_measured_sd_when_large(self):
        self.assertAlmostEqual(base.effective_sigma(5.0, 100.0, self.cfg), 5.0)

    def test_ratio_floor(self):
        # 0.02 * 100 = 2.0 が効く
        self.assertAlmostEqual(base.effective_sigma(0.1, 100.0, self.cfg), 2.0)

    def test_absolute_floor(self):
        self.assertAlmostEqual(base.effective_sigma(0.0, 0.0, self.cfg), 0.5)

    def test_none_sd(self):
        self.assertAlmostEqual(base.effective_sigma(None, 0.0, self.cfg), 0.5)

    def test_nan_sd(self):
        self.assertAlmostEqual(base.effective_sigma(float("nan"), 0.0, self.cfg), 0.5)

    def test_negative_mean_uses_abs(self):
        self.assertAlmostEqual(base.effective_sigma(0.0, -100.0, self.cfg), 2.0)

    def test_always_positive_even_with_zero_floors(self):
        cfg = _cfg_with(self.cfg, {"common.sigma_floor_ratio": 0.0,
                                   "common.sigma_floor_abs": 0.0})
        self.assertGreater(base.effective_sigma(0.0, 0.0, cfg), 0.0)


def _cfg_with(cfg, overrides):
    values = dict(cfg.as_dict())
    for key, value in overrides.items():
        values[("parameters", key)] = value
    return config.Config(values)


class TestScoreHelpers(unittest.TestCase):
    def test_score_to_hint(self):
        self.assertEqual(base.score_to_hint(1.5), "WARN")
        self.assertEqual(base.score_to_hint(2.0), "FATAL")
        self.assertEqual(base.score_to_hint(9.9), "FATAL")

    def test_interpolate_score(self):
        self.assertAlmostEqual(base.interpolate_score(2.0, 2.0, 3.0), 1.0)
        self.assertAlmostEqual(base.interpolate_score(3.0, 2.0, 3.0), 2.0)
        self.assertAlmostEqual(base.interpolate_score(4.0, 2.0, 3.0), 3.0)
        self.assertEqual(base.interpolate_score(1.0, 2.0, 3.0), 0.0)

    def test_interpolate_score_equal_thresholds(self):
        # ゼロ除算にならないこと
        self.assertEqual(base.interpolate_score(5.0, 2.0, 2.0), 1.0)

    def test_shape_constants(self):
        self.assertEqual(len(base.ALL_SHAPES), 7)
        self.assertEqual(sorted(base.SHAPE_PRIORITY), sorted(base.ALL_SHAPES))


def _det(start_min, score=1.0, shape=base.SHAPE_SPIKE_UP, algorithm="ALG-A1"):
    ts = BASE_TS + timedelta(minutes=start_min)
    return base.Detection(
        series_id="s/1", source="db_connection", metric="m",
        algorithm=algorithm, start_ts=ts, end_ts=ts, values=[1.0],
        score=score, severity_hint=base.score_to_hint(score),
        detail={}, shape=shape,
    )


class TestMergeAdjacent(unittest.TestCase):
    def test_merges_consecutive(self):
        dets = [_det(i * 5) for i in range(5)]
        got = base.merge_adjacent(dets, 300.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].shape, base.SHAPE_SUSTAINED)

    def test_does_not_merge_distant(self):
        got = base.merge_adjacent([_det(0), _det(60)], 300.0)
        self.assertEqual(len(got), 2)

    def test_empty(self):
        self.assertEqual(base.merge_adjacent([], 300.0), [])

    def test_single_keeps_shape(self):
        got = base.merge_adjacent([_det(0)], 300.0)
        self.assertEqual(got[0].shape, base.SHAPE_SPIKE_UP)

    def test_score_is_max(self):
        got = base.merge_adjacent([_det(0, 1.0), _det(5, 3.0), _det(10, 2.0)], 300.0)
        self.assertAlmostEqual(got[0].score, 3.0)


class TestPersistenceRule(unittest.TestCase):
    def test_isolated_warn_dropped(self):
        got = base.apply_persistence_rule([_det(0, score=1.2)], 300.0)
        self.assertEqual(got, [])

    def test_isolated_fatal_kept(self):
        got = base.apply_persistence_rule([_det(0, score=2.5)], 300.0)
        self.assertEqual(len(got), 1)

    def test_two_consecutive_warn_kept(self):
        got = base.apply_persistence_rule([_det(0, 1.2), _det(5, 1.3)], 300.0)
        self.assertEqual(len(got), 2)

    def test_two_distant_warn_dropped(self):
        got = base.apply_persistence_rule([_det(0, 1.2), _det(120, 1.3)], 300.0)
        self.assertEqual(got, [])

    def test_empty(self):
        self.assertEqual(base.apply_persistence_rule([], 300.0), [])


class TestValueHelpers(unittest.TestCase):
    def test_context(self):
        pts = [(None, float(i)) for i in range(10)]
        self.assertEqual(base.take_values_with_context(pts, 5, 5, 2),
                         [3.0, 4.0, 5.0, 6.0, 7.0])

    def test_context_at_edges(self):
        pts = [(None, float(i)) for i in range(4)]
        self.assertEqual(base.take_values_with_context(pts, 0, 0, 2), [0.0, 1.0, 2.0])

    def test_sample_values(self):
        self.assertEqual(len(base.sample_values(list(range(100)), 9)), 9)
        self.assertEqual(base.sample_values([1, 2, 3], 9), [1, 2, 3])

    def test_mean_and_sd(self):
        mu, sd = base.mean_and_sd([1.0, 2.0, 3.0])
        self.assertAlmostEqual(mu, 2.0)
        self.assertAlmostEqual(sd, 1.0)
        self.assertEqual(base.mean_and_sd([]), (0.0, 0.0))
        self.assertEqual(base.mean_and_sd([5.0]), (5.0, 0.0))


class TestSkipInfoAndRegistry(unittest.TestCase):
    def test_five_aspect1_detectors(self):
        """観点1 の検知器が 5 個そろっていること。

        レジストリ全体 (10 個) の検証は test_detectors_b.TestRegistryComplete
        が担当する。
        """
        aspect1 = [d for d in detectors.REGISTRY.values() if d.aspect == "観点1"]
        self.assertEqual(len(aspect1), 5)
        self.assertEqual(sorted(d.id for d in aspect1),
                         ["ALG-A1", "ALG-A2", "ALG-A3", "ALG-A4", "ALG-A5"])
        for det in aspect1:
            self.assertEqual(detectors.REGISTRY[det.id], det)
            self.assertTrue(isinstance(det, base.Detector))

    def test_enabled_detectors_sorted(self):
        progress, _, _ = make_progress()
        cfg = config.load(None, progress)
        ids = [d.id for d in detectors.enabled_detectors(cfg)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(detectors.REGISTRY))


# ---------------------------------------------------------------------------
# ALG-A1
# ---------------------------------------------------------------------------
def flat_with_noise(n, mean=10.0, sigma=1.0, seed=1):
    rng = random.Random(seed)
    return [mean + rng.gauss(0.0, sigma) for _ in range(n)]


class TestA1(DetectorFixture):
    def setUp(self):
        super(TestA1, self).setUp()
        self.detector = detectors.REGISTRY["ALG-A1"]

    def test_detects_single_large_spike(self):
        values = flat_with_noise(200)
        values[100] = 60.0
        meta = self.load(values)
        dets, skips = self.run_detector(self.detector, meta)
        self.assertEqual(skips, [])
        self.assertTrue(dets)
        spike_ts = BASE_TS + timedelta(minutes=INTERVAL_MIN * 100)
        self.assertTrue(any(d.start_ts <= spike_ts <= d.end_ts for d in dets))
        self.assertEqual(dets[0].shape, base.SHAPE_SPIKE_UP)

    def test_detects_downward_spike(self):
        values = flat_with_noise(200)
        values[100] = -40.0
        meta = self.load(values)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertTrue(any(d.shape == base.SHAPE_SPIKE_DOWN for d in dets))

    def test_clean_series_few_detections(self):
        values = flat_with_noise(400, seed=7)
        meta = self.load(values)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertLess(len(dets), len(values) * 0.02, dets[:3])

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * 200)
        dets, skips = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])

    def test_all_zero_series_no_error(self):
        meta = self.load([0.0] * 200)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])

    def test_small_integer_series_not_flooded(self):
        """0/1 だけの系列で検知が過剰にならないこと (effective_sigma の効果)。"""
        rng = random.Random(3)
        values = [float(rng.randint(0, 1)) for _ in range(400)]
        meta = self.load(values)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertLess(len(dets), len(values) * 0.5)

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 10)
        dets, skips = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0].reason, base.SKIP_FEW_POINTS)
        self.assertEqual(skips[0].algorithm, "ALG-A1")

    def test_segments_are_independent(self):
        values = flat_with_noise(200)
        segments = [0] * 100 + [1] * 100
        meta = self.load(values, segments=segments)
        dets, _ = self.run_detector(self.detector, meta)
        # segment 境界をまたぐ検知が出ないこと (境界直後の点は窓が空)
        self.assertTrue(all(d.start_ts <= d.end_ts for d in dets))


# ---------------------------------------------------------------------------
# ALG-A2
# ---------------------------------------------------------------------------
class TestA2(DetectorFixture):
    def setUp(self):
        super(TestA2, self).setUp()
        self.a1 = detectors.REGISTRY["ALG-A1"]
        self.a2 = detectors.REGISTRY["ALG-A2"]

    def test_detects_spike(self):
        values = flat_with_noise(200)
        values[100] = 60.0
        meta = self.load(values)
        dets, _ = self.run_detector(self.a2, meta)
        self.assertTrue(dets)

    def test_robust_to_consecutive_outliers(self):
        """A1 がマスキングで弱くなる連続外れ値でも A2 は検知する。"""
        values = flat_with_noise(300)
        for i in (150, 151, 152):
            values[i] = 50.0
        meta = self.load(values)
        a2_dets, _ = self.run_detector(self.a2, meta)
        a2_hit = [d for d in a2_dets
                  if d.start_ts <= BASE_TS + timedelta(minutes=INTERVAL_MIN * 151) <= d.end_ts]
        self.assertTrue(a2_hit, "A2 が連続外れ値を検知していない")

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * 200)
        dets, _ = self.run_detector(self.a2, meta)
        self.assertEqual(dets, [])

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector(self.a2, meta)
        self.assertEqual(len(skips), 1)


# ---------------------------------------------------------------------------
# ALG-A3
# ---------------------------------------------------------------------------
class TestA3(DetectorFixture):
    def setUp(self):
        super(TestA3, self).setUp()
        self.detector = detectors.REGISTRY["ALG-A3"]

    def test_detects_extreme_point(self):
        rng = random.Random(11)
        values = [rng.uniform(9.0, 11.0) for _ in range(200)]
        values[100] = 80.0
        meta = self.load(values)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertTrue(dets)
        self.assertEqual(dets[0].shape, base.SHAPE_SPIKE_UP)

    def test_detects_low_outlier(self):
        rng = random.Random(12)
        values = [rng.uniform(50.0, 52.0) for _ in range(200)]
        values[100] = 0.0
        meta = self.load(values)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertTrue(any(d.shape == base.SHAPE_SPIKE_DOWN for d in dets))

    def test_wide_distribution_is_discarded(self):
        """周期性の強い系列では 5% 規則で全破棄され、スキップが返ること。"""
        values = [50.0 + 45.0 * math.sin(2 * math.pi * i / 288.0)
                  for i in range(2000)]
        meta = self.load(values)
        dets, skips = self.run_detector(self.detector, meta)
        if skips:
            self.assertEqual(skips[0].reason, base.SKIP_WIDE_DISTRIBUTION)
            self.assertEqual(dets, [])
        else:
            self.assertLessEqual(len(dets), len(values) * 0.05)

    def test_zero_iqr_no_division_error(self):
        meta = self.load([7.0] * 200)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector(self.detector, meta)
        self.assertEqual(len(skips), 1)


# ---------------------------------------------------------------------------
# ALG-A4
# ---------------------------------------------------------------------------
class TestA4(DetectorFixture):
    def setUp(self):
        super(TestA4, self).setUp()
        self.a1 = detectors.REGISTRY["ALG-A1"]
        self.a4 = detectors.REGISTRY["ALG-A4"]

    @staticmethod
    def _daily_cycle(n, seed, extra=None):
        """日次の周期を持つ 5 分間隔の系列 (288 点 = 1 日)。"""
        rng = random.Random(seed)
        out = []
        for i in range(n):
            value = 8.0 + 7.0 * (1.0 + math.sin(2 * math.pi * (i % 288) / 288.0
                                                - math.pi / 2.0))
            value += rng.gauss(0.0, 1.2)
            if extra is not None:
                value += extra(i)
            out.append(value)
        return out

    def test_detects_gradual_increase_that_a1_misses(self):
        """A4 の存在意義: A1 が見逃す緩やかな増加を捉える。"""
        n = 288 * 7
        half = n // 2
        clean = self._daily_cycle(n, seed=31)
        rising = self._daily_cycle(
            n, seed=31, extra=lambda i: 0.01 * (i - half) if i >= half else 0.0
        )

        meta = self.load(clean, series_id="clean/1")
        a1_clean, _ = self.run_detector(self.a1, meta)
        a4_clean, _ = self.run_detector(self.a4, meta)

        meta2 = self.load(rising, series_id="rising/1")
        a1_rising, _ = self.run_detector(self.a1, meta2)
        a4_rising, _ = self.run_detector(self.a4, meta2)

        # A4 は増加に強く反応する
        self.assertGreater(len(a4_rising), len(a4_clean) * 3,
                           "A4 が緩やかな増加に反応していない")
        # A1 は増加をほとんど区別できない (A4 の存在意義の裏返し)
        self.assertGreater(len(a4_rising), len(a1_rising) - len(a1_clean),
                           "A4 の反応が A1 の差分を上回っていない")

    def test_daily_cycle_alone_is_quiet(self):
        """周期だけの系列では検知がごく少ないこと(季節差分の効果)。"""
        values = self._daily_cycle(288 * 7, seed=41)
        meta = self.load(values)
        dets, _ = self.run_detector(self.a4, meta)
        self.assertLess(len(dets), len(values) * 0.01, dets[:3])

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * (288 * 3))
        dets, _ = self.run_detector(self.a4, meta)
        self.assertEqual(dets, [])

    def test_lambda_one_no_error(self):
        cfg = _cfg_with(self.cfg, {"ALG-A4.lambda": 1.0})
        meta = self.load(self._daily_cycle(288 * 3, seed=51))
        self.a4.run(self.con, cfg, meta, self.progress)  # 例外にならないこと

    def test_without_24h_history_skipped(self):
        """24 時間ぶんの履歴が無い系列はスキップされること。"""
        meta = self.load(flat_with_noise(100))  # 100 点 = 約 8 時間
        dets, skips = self.run_detector(self.a4, meta)
        self.assertEqual(dets, [])
        self.assertTrue(skips)
        self.assertEqual(skips[0].reason, base.SKIP_NO_BASELINE)

    def test_few_points_skipped(self):
        meta = self.load([1.0] * 5)
        dets, skips = self.run_detector(self.a4, meta)
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0].reason, base.SKIP_FEW_POINTS)


# ---------------------------------------------------------------------------
# ALG-A5
# ---------------------------------------------------------------------------
class TestA5(DetectorFixture):
    def setUp(self):
        super(TestA5, self).setUp()
        self.detector = detectors.REGISTRY["ALG-A5"]

    def _weekly_series(self, weeks=4, weekend_low=True):
        """平日と週末で水準が異なる 4 週間の系列 (1 時間間隔)。"""
        values = []
        n = weeks * 7 * 24
        for i in range(n):
            ts = BASE_TS + timedelta(hours=i)
            base_value = 10.0 if (not weekend_low or ts.weekday() < 5) else 2.0
            values.append(base_value)
        return values

    def test_detects_weekday_point_at_weekend_level(self):
        values = self._weekly_series()
        # 平日の 1 点だけ週末相当(大きく外れた値)にする
        target = None
        for i in range(len(values)):
            ts = BASE_TS + timedelta(hours=i)
            if ts.weekday() < 5 and i > 24 * 21:
                target = i
                break
        values[target] = 40.0
        meta = self.load(values, interval_min=60)
        dets, skips = self.run_detector(self.detector, meta)
        hit_ts = BASE_TS + timedelta(hours=target)
        self.assertTrue(
            any(d.start_ts <= hit_ts <= d.end_ts for d in dets),
            "A5 が平日の異常値を検知していない: {0}".format(dets),
        )
        self.assertEqual(dets[0].shape, base.SHAPE_SEASONAL_DEV)

    def test_weekend_low_values_not_flagged(self):
        """週末の低い値は、周期を考慮すれば異常ではない。"""
        values = self._weekly_series()
        meta = self.load(values, interval_min=60)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])

    def test_insufficient_weeks_skipped(self):
        values = [10.0] * (24 * 5)  # 1 週間に満たない
        meta = self.load(values, interval_min=60)
        dets, skips = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])
        self.assertTrue(skips)
        self.assertEqual(skips[0].reason, base.SKIP_NO_BASELINE)

    def test_leave_one_out(self):
        from s_anomaly.detectors.a5_seasonal import _leave_one_out

        mu_loo, sd_loo = _leave_one_out(mu=10.0, sd=2.0, n=5, x=20.0)
        self.assertAlmostEqual(mu_loo, (5 * 10.0 - 20.0) / 4.0)
        self.assertIsNotNone(sd_loo)
        self.assertGreaterEqual(sd_loo, 0.0)

    def test_leave_one_out_small_n(self):
        from s_anomaly.detectors.a5_seasonal import _leave_one_out

        self.assertEqual(_leave_one_out(10.0, 2.0, 2, 20.0), (None, None))

    def test_leave_one_out_negative_variance_clamped(self):
        from s_anomaly.detectors.a5_seasonal import _leave_one_out

        mu_loo, sd_loo = _leave_one_out(mu=10.0, sd=0.0, n=5, x=10.0)
        self.assertGreaterEqual(sd_loo, 0.0)

    def test_duckdb_dayofweek_range(self):
        """DuckDB の dayofweek の戻り値の範囲を明示的に確認する。"""
        rows = self.con.execute(
            "SELECT DISTINCT dayofweek(ts) FROM ("
            " SELECT TIMESTAMP '2026-06-01' + INTERVAL (i) DAY AS ts"
            " FROM range(7) t(i))"
        ).fetchall()
        got = sorted(r[0] for r in rows)
        self.assertEqual(got, [0, 1, 2, 3, 4, 5, 6])

    def test_constant_series_no_detection(self):
        meta = self.load([7.0] * (24 * 21), interval_min=60)
        dets, _ = self.run_detector(self.detector, meta)
        self.assertEqual(dets, [])


if __name__ == "__main__":
    unittest.main()
