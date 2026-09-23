"""Analysis workflows, async imports and laptop UI regression tests."""
import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets
from echemtips.ui import create_application, EChemTipsApp
from echemtips.analysis_window import AnalysisWindow
from echemtips.analysis_core import AnalysisDataset
from echemtips.analysis_tools import cv_selections, potential_map
from echemtips.analysis_display import envelope_indices
from echemtips.analysis_views import RecordingTableModel, export_result


def write_scan(folder, name="scan", points=4):
    """Build a synthetic serpentine scan with distinct current per hop."""
    path = Path(folder) / (name + ".csv")
    waveform = np.concatenate((np.linspace(-.2, .6, 101), np.linspace(.6, -.4, 101)[1:], np.linspace(-.4, -.2, 31)[1:]))
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("elapsed_s", "voltage1_v", "current1_na", "z_um", "scan_pixel"))
        index = 0
        for pixel in range(points):
            for potential in waveform:
                writer.writerow((index * .01, potential, potential * 2 + pixel, 50 + pixel, pixel))
                index += 1
    grid = [{"scan_pixel": i, "x_um": 10 + (i % 2 if i < 2 else 1 - i % 2) * 5, "y_um": 20 + (i // 2) * 5}
            for i in range(points)]
    path.with_suffix(".json").write_text(json.dumps({"experiment": "Scan Hopping CV", "status": "complete",
        "parameters": {"cv_start_v": -.2, "cv_vertex1_v": .6, "cv_vertex2_v": -.4, "cycles": 1},
        "scan_grid": {"pixels": grid}}))
    return path


class AnalysisWorkbenchTests(unittest.TestCase):
    def test_smoothing_updates_cv_and_current_maps_and_can_be_disabled(self):
        with TemporaryDirectory() as folder:
            path = write_scan(folder)
            with path.open() as stream:
                rows = list(csv.reader(stream))
            # Add a narrow current spike at E = +0.2 V in the first CV.
            rows[51][2] = "100"
            with path.open("w", newline="") as stream:
                csv.writer(stream).writerows(rows)
            original_bytes = path.read_bytes()
            window = AnalysisWindow(path)
            try:
                self.wait_loaded(window)
                window.map_panel.statistic.setCurrentText("CV at potential")
                window.map_panel.potential.setValue(.2)
                raw_map = window.map_panel.points[0]["value"]
                window.smoothing_enabled.setChecked(True)
                window.smoothing_window.setValue(11)
                window._apply_smoothing(); self.wait_loaded(window)
                self.assertLess(window.map_panel.points[0]["value"], raw_map / 2)
                self.assertLess(max(window.cycles[0].current_na("current1_na")), 30)
                self.assertEqual(max(window.source_dataset.column("current1_na")), 100)
                self.assertIn("SMOOTHED", window.subtitle_label.text())
                self.assertEqual(window.dataset.metadata["analysis_processing"]["window_samples"], 11)
                export_path = Path(folder) / "smoothed_cv.csv"
                with patch("echemtips.analysis_views.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(export_path), "")), patch("echemtips.analysis_views.QtWidgets.QMessageBox.information"):
                    window.export_cycles()
                exported = json.loads(export_path.with_suffix(".json").read_text())
                self.assertEqual(exported["processing"]["window_samples"], 11)
                self.assertEqual(exported["processing"]["method"], "savitzky_golay")
                self.assertEqual(exported["processing"]["polynomial_order"], 2)
                # Reapply from originals: never progressively smooth the result.
                once = window.dataset.column("current1_na").copy()
                window._apply_smoothing(); self.wait_loaded(window)
                np.testing.assert_array_equal(window.dataset.column("current1_na"), once)
                window.smoothing_enabled.setChecked(False)
                window._apply_smoothing(); self.wait_loaded(window)
                self.assertEqual(window.map_panel.points[0]["value"], raw_map)
                self.assertEqual(path.read_bytes(), original_bytes)
            finally:
                window.close()

    def test_dense_trace_uses_fast_rendering_without_changing_source(self):
        from echemtips.qt_common import XYPlot, current_display_scale
        def unused_values():
            raise AssertionError("Fixed display units must not scan every sample")
            yield 0
        self.assertEqual(current_display_scale("pA", unused_values()), (1000.0, "pA"))
        plot = XYPlot("Time (s)", "Current (nA)")
        xs = np.arange(100000, dtype=float)
        ys = np.sin(xs)
        ys[12345] = 100
        original = ys.copy()
        try:
            plot.set_data([("test", xs, ys, "#008b83")])
            curve = plot.graph.listDataItems()[0]
            self.assertFalse(curve.opts["antialias"])
            self.assertEqual(curve.curve.opts["segmentedLineMode"], "on")
            self.assertLessEqual(len(curve.xData), 12000)
            self.assertEqual(max(curve.yData), 100)
            np.testing.assert_array_equal(ys, original)
        finally:
            plot.close()

    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def wait_loaded(self, window):
        deadline = time.monotonic() + 10
        while window.loading and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.01)
        self.assertFalse(window.loading)
        self.assertIsNotNone(window.dataset)

    def test_async_scan_maps_measurements_and_full_table(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {"ECHEMTIPS_SETTINGS_PATH": str(Path(folder) / "settings.json")}):
            path = write_scan(folder)
            window = AnalysisWindow(path)
            window.resize(1080, 680); window.show()
            try:
                self.wait_loaded(window)
                self.assertEqual(len(window.cycles), 4)
                self.assertEqual(window.data_table.model().rowCount(), len(window.dataset.rows))
                self.assertEqual(len(window.map_panel.points), 4)
                window.map_panel.statistic.setCurrentText("CV at potential")
                window.map_panel.potential.setValue(.2)
                self.assertAlmostEqual(window.map_panel.points[2]["value"], 2.4)
                window._inspect_hop(2)
                self.assertEqual(window.explorer.selection.currentText(), "Hop 3")
                self.assertIn("Whole hop", window.explorer.scope.text())
                for i in range(window.tabs.count()):
                    window.tabs.setCurrentIndex(i); self.app.processEvents()
                    self.assertLessEqual(window.width(), 1080)
                self.assertIsNone(window.backend if hasattr(window, "backend") else None)
            finally:
                window.close()

    def test_latest_async_selection_wins(self):
        with TemporaryDirectory() as folder:
            first, second = write_scan(folder, "one"), write_scan(folder, "two")
            window = AnalysisWindow()
            try:
                window.load_recording(first); window.load_recording(second)
                self.wait_loaded(window)
                self.assertEqual(window.dataset.path, second.resolve())
            finally: window.close()

    def test_future_numeric_channel_can_be_explored(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "future.csv"
            path.write_text("elapsed_s,temperature_c\n0,20\n1,21\n")
            window = AnalysisWindow(path)
            try:
                self.wait_loaded(window)
                self.assertEqual(window.explorer.y_signal.currentData(), "temperature_c")
                self.assertAlmostEqual(window.explorer.result[2]["mean"], 20.5)
            finally: window.close()

    def test_current_potential_map_does_not_extrapolate(self):
        with TemporaryDirectory() as folder:
            dataset = AnalysisDataset.load(write_scan(folder))
            cycles = cv_selections(dataset)
            self.assertEqual(potential_map(dataset, cycles, "current1_na", 2), [])
            self.assertAlmostEqual(potential_map(dataset, cycles, "current1_na", .2, False)[0]["value"], .4)

    def test_display_reduction_retains_spikes_and_gaps(self):
        x = np.arange(100000); y = np.zeros(100000)
        y[50123], y[70234], y[80000] = 100, -20, np.nan
        indices = envelope_indices(x, y)
        self.assertLessEqual(len(indices), 12000)
        for i in (0, 50123, 70234, 80000, 99999): self.assertIn(i, indices)

    def test_source_export_is_protected(self):
        with TemporaryDirectory() as folder:
            path = write_scan(folder)
            dataset = AnalysisDataset.load(path); before = path.read_bytes()
            with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(path), "CSV")), patch.object(QtWidgets.QMessageBox, "warning") as warning:
                export_result(None, dataset, [(1, 2)], ("x", "y"), {}, "_analysis")
                warning.assert_called_once()
            self.assertEqual(path.read_bytes(), before)

    def test_export_retains_analysis_recipe_and_full_selected_rows(self):
        with TemporaryDirectory() as folder:
            path = write_scan(folder)
            dataset = AnalysisDataset.load(path)
            target = Path(folder) / "derived.csv"
            with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "CSV")), patch.object(QtWidgets.QMessageBox, "information"):
                export_result(None, dataset, [(0, 1), (1, 2)], ("elapsed_s", "processed_current_na"),
                              {"baseline": 2, "scope": "test"}, "_analysis")
            self.assertEqual(len(target.read_text().splitlines()), 3)
            metadata = json.loads(target.with_suffix(".json").read_text())
            self.assertEqual(metadata["analysis"]["baseline"], 2)
            self.assertEqual(metadata["source_status"], "complete")

    def test_virtual_table_has_no_5000_row_limit(self):
        from echemtips.analysis_core import NumericRows
        data = AnalysisDataset(Path("large.csv"), ("elapsed_s", "i"), NumericRows(("elapsed_s", "i"), np.zeros((100000, 2))), {})
        model = RecordingTableModel(data)
        self.assertEqual(model.rowCount(), 100000)
        self.assertEqual(model.data(model.index(99999, 1)), "0")

    def test_control_launcher_uses_separate_process_and_no_hardware(self):
        window = EChemTipsApp()
        try:
            with patch.object(QtCore.QProcess, "startDetached", return_value=(True, 123)) as launch:
                window.launch_analysis()
                args = launch.call_args.args[1]
                self.assertIn("echemtips.analysis", args)
                self.assertIn("--data-folder", args)
                self.assertFalse(window.backend.connected)
            with patch.object(QtCore.QProcess, "startDetached") as launch, patch.object(window, "show_error") as error:
                window.launch_analysis(last_recording=True)
                launch.assert_not_called(); error.assert_called_once()
        finally: window.close()
