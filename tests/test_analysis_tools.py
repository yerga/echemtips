"""Numerical and extension contracts independent of the analysis UI."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from echemtips.analysis_core import AnalysisDataset, AnalysisError, NumericRows, pixel_groups
from echemtips.analysis_tools import Selection, AnalysisProvider, PROVIDERS, register_provider, measure, hop_map


class AnalysisToolsTests(unittest.TestCase):
    def test_contiguous_hops_share_storage_and_repeated_tags_keep_order(self):
        dataset = self.dataset()
        groups = pixel_groups(dataset)
        self.assertTrue(np.shares_memory(groups[0][1].matrix, dataset.rows.matrix))
        columns = ("elapsed_s", "scan_pixel")
        dataset = AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, [[0, 0], [1, -1], [2, 1], [3, 0], [4, .5], [5, np.nan]]), {})
        groups = pixel_groups(dataset)
        self.assertEqual([pixel for pixel, _ in groups], [0, 1])
        self.assertEqual(groups[0][1].matrix[:, 0].tolist(), [0, 3])

    def test_numeric_loader_rejects_bad_width_and_missing_values(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "bad.csv"
            for body in ("0\n1\n", "0,1,2\n", "0,\n", "0,1\n1,2,3\n"):
                path.write_text("elapsed_s,current1_na\n" + body)
                with self.assertRaises(AnalysisError):
                    AnalysisDataset.load(path)

    def dataset(self):
        columns = ("elapsed_s", "current1_na", "voltage1_v", "scan_pixel")
        return AnalysisDataset(Path("test.csv"), columns,
            NumericRows(columns, [[0, 2, 0, 0], [1, 4, 1, 0], [2, 2, 0, 1], [3, 4, -1, 1]]),
            {"scan_grid": {"pixels": [{"scan_pixel": 0, "x_um": 20, "y_um": 10},
                                     {"scan_pixel": 1, "x_um": 10, "y_um": 10}]}})

    def test_columns_and_slices_share_immutable_storage(self):
        d = self.dataset()
        self.assertTrue(np.shares_memory(d.rows[:2].matrix, d.rows.matrix))
        self.assertTrue(np.shares_memory(d.column("current1_na"), d.rows.matrix))
        with self.assertRaises(ValueError): d.column("current1_na")[0] = 3

    def test_charge_uses_time_even_when_plotting_potential(self):
        d = self.dataset()
        x, y, result = measure(Selection("test", d.rows, "test"), "voltage1_v", "current1_na", baseline=1)
        self.assertAlmostEqual(result["charge_nc"], 6)
        self.assertEqual(result["samples"], 4)
        self.assertEqual(d.column("current1_na")[0], 2)

    def test_time_range_and_invalid_samples_do_not_bridge_gaps(self):
        columns = ("elapsed_s", "current1_na")
        rows = NumericRows(columns, [[0, 1], [1, np.nan], [2, 3], [3, 3]])
        _, _, result = measure(Selection("test", rows, "test"), "elapsed_s", "current1_na")
        self.assertEqual(result["charge_nc"], 3)
        self.assertEqual(result["invalid_samples"], 1)
        with self.assertRaises(AnalysisError):
            measure(Selection("test", rows, "test"), "elapsed_s", "current1_na", (5, 6))

    def test_reversed_time_rejected(self):
        columns = ("elapsed_s", "current1_na")
        with self.assertRaises(AnalysisError):
            measure(Selection("test", NumericRows(columns, [[1, 2], [0, 3]]), "test"),
                    "elapsed_s", "current1_na")

    def test_maps_respect_recorded_geometry_not_pixel_sort_order(self):
        points = hop_map(self.dataset(), "current1_na", "Mean")
        self.assertEqual([p["x_um"] for p in points], [20, 10])
        self.assertEqual([p["value"] for p in points], [3, 3])

    def test_missing_grid_is_reported_not_guessed(self):
        dataset = self.dataset()
        dataset.metadata["scan_grid"] = None
        with self.assertRaisesRegex(AnalysisError, "coordinates"):
            hop_map(dataset, "current1_na", "Mean")

    def test_new_provider_can_be_registered_without_ui_changes(self):
        provider = AnalysisProvider("test_extension", "New method", lambda d: True,
                                    lambda d: [Selection("custom", d.rows[:2], "custom")])
        try:
            register_provider(provider)
            self.assertEqual(len(PROVIDERS[provider.key].extract(self.dataset())[0].rows), 2)
            with self.assertRaises(ValueError): register_provider(provider)
        finally:
            PROVIDERS.pop(provider.key, None)

    def test_future_channel_file_does_not_require_electrochemistry(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "new.csv"
            path.write_text("elapsed_s,temperature_c\n0,20\n1,21\n")
            d = AnalysisDataset.load(path)
            self.assertEqual(d.values("temperature_c"), [20, 21])
            self.assertEqual(d.experiment, "Unspecified experiment")

    def test_duplicate_headers_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "bad.csv"
            path.write_text("elapsed_s,current1_na,current1_na\n0,1,2\n")
            with self.assertRaises(AnalysisError): AnalysisDataset.load(path)
