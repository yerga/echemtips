"""Scientific and UI contracts for non-destructive current smoothing."""
from pathlib import Path
import unittest
import numpy as np

from echemtips.analysis_core import AnalysisDataset, NumericRows, CVCycle
from echemtips.analysis_processing import smooth_currents


class SmoothingTests(unittest.TestCase):
    def test_savgol_preserves_quadratic_shape_including_edges(self):
        x = np.arange(101, dtype=float)
        y = .0001 * (3 * x ** 2 - 2 * x + 10)
        columns = ("elapsed_s", "current1_na")
        source = AnalysisDataset(Path("test.csv"), columns,
                                 NumericRows(columns, np.column_stack((x, y))), {})
        filtered = smooth_currents(source, 11, method="savitzky_golay", polynomial_order=2)
        np.testing.assert_allclose(filtered.column("current1_na"), y, atol=1e-12)
        np.testing.assert_array_equal(source.column("current1_na"), y)
        self.assertEqual(filtered.metadata["analysis_processing"]["polynomial_order"], 2)

    def test_savgol_short_segments_and_gaps_are_safe(self):
        columns = ("elapsed_s", "current1_na", "scan_pixel")
        y = [1, 3, np.nan, 100, 103, 108, 115, 124]
        source = AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, [[i, value, int(i >= 3)] for i, value in enumerate(y)]), {})
        filtered = smooth_currents(source, 11, method="savitzky_golay", polynomial_order=2)
        np.testing.assert_allclose(filtered.column("current1_na"), y, atol=1e-10, equal_nan=True)
        for order in (0, 3, 6, 2.5):
            with self.assertRaises(ValueError):
                smooth_currents(source, 3, method="savitzky_golay", polynomial_order=order)

    def test_savgol_reduces_noise_without_moving_gaussian_peak(self):
        x = np.linspace(-5, 5, 1001)
        clean = np.exp(-x ** 2)
        noisy = clean + .05 * np.random.default_rng(12).normal(size=len(x))
        columns = ("elapsed_s", "current1_na")
        source = AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, np.column_stack((x, noisy))), {})
        result = smooth_currents(source, 31, method="savitzky_golay", polynomial_order=3).column("current1_na")
        self.assertLess(np.mean((result-clean)**2), np.mean((noisy-clean)**2) / 3)
        self.assertLess(abs(x[np.argmax(result)]), .2)

    def test_cycle_and_sweep_boundaries_do_not_blend_currents(self):
        columns = ("elapsed_s", "voltage1_v", "current1_na")
        source = AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, [[0, 0, 1], [1, 1, 1], [2, 2, 10], [3, 1, 10], [4, 0, 10]]), {})
        np.testing.assert_array_equal(smooth_currents(source, 11).column("current1_na"), [1, 1, 10, 10, 10])
        source = AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, [[0, 0, 1], [1, 0, 1], [2, 0, 10], [3, 0, 10]]), {})
        cycles = [CVCycle(1, source.rows[:2]), CVCycle(2, source.rows[2:])]
        np.testing.assert_array_equal(smooth_currents(source, 11, cycles).column("current1_na"), [1, 1, 10, 10])

    def test_mean_preserves_gaps_and_hop_boundaries_and_noncurrent_columns(self):
        columns = ("elapsed_s", "current1_na", "current2_na", "scan_pixel", "z_um")
        currents = [0, 9, 0, float("nan"), 20, 20, 100, 100, 100]
        matrix = np.array([[i, y, y / 1000, int(i >= 6), i] for i, y in enumerate(currents)])
        source = AnalysisDataset(Path("test.csv"), columns, NumericRows(columns, matrix), {})
        result = smooth_currents(source, 3)
        np.testing.assert_allclose(result.column("current1_na"), [4.5, 3, 4.5, np.nan, 20, 20, 100, 100, 100], equal_nan=True)
        np.testing.assert_allclose(source.column("current1_na"), currents, equal_nan=True)
        np.testing.assert_array_equal(result.column("z_um"), source.column("z_um"))
        self.assertNotIn("analysis_processing", source.metadata)
        self.assertIs(smooth_currents(source, 1), source)

    def test_waypoint_boundaries_and_invalid_windows(self):
        columns = ("elapsed_s", "current1_na", "line_number")
        source = AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, [[0, 1, 3], [1, 1, 3], [2, 10, 4], [3, 10, 4]]), {})
        np.testing.assert_array_equal(smooth_currents(source, 101).column("current1_na"), [1, 1, 10, 10])
        for window in (0, 2, 10003):
            with self.assertRaises(ValueError): smooth_currents(source, window)
