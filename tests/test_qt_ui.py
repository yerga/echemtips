from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from echemtips.analysis_window import AnalysisWindow
from echemtips.models import Sample
from echemtips.qt_common import Heatmap, Plot
from echemtips.ui import EChemTipsApp, create_application


class QtLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = create_application([])

    def test_control_pages_fit_minimum_window_and_keep_actions_accessible(self) -> None:
        window = EChemTipsApp()
        window.resize(1080, 680)
        window.show()
        self.qt_app.processEvents()
        try:
            self.assertEqual(window.connect_button.text(), "Connect")
            self.assertEqual(tuple(window.pages), window.PAGE_NAMES)
            for name, page in window.pages.items():
                window.show_page(name)
                self.qt_app.processEvents()
                self.assertTrue(page.isVisible(), name)
                self.assertGreater(page.width(), 0, name)
                self.assertGreater(page.height(), 0, name)
                self.assertTrue(window.instrument_readout.isVisible(), name)
            settings = window.pages["Settings"]
            self.assertGreater(settings.viewport.verticalScrollBar().maximum(), 0)
            self.assertEqual(
                settings.viewport.horizontalScrollBarPolicy(),
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            settings_text = " ".join(
                item.text() for item in settings.findChildren(QtWidgets.QLabel)
            )
            self.assertNotIn("Instrument profile", settings_text)
            self.assertNotIn("External lock-in", settings_text)
            self.assertNotIn("Advanced FPGA feedback", settings_text)
            self.assertNotIn("Calibration provenance", settings_text)
            self.assertIn("Current amplifiers and potential command", settings_text)
            self.assertIn("5:1", settings.command_ratio_help.toolTip())
            settings.command_ratio.entry.setText("5")
            self.assertIn("±2 V", settings.command_ratio_summary.text())
            self.assertEqual(settings.save_defaults_button.text(), "Save as defaults and apply")
            self.assertIn(str(window.store.path), settings.settings_path_label.text())
            settings.save_directory.variable.set("relative-data")
            self.assertTrue(QtCore.QFileInfo(settings.values().save_directory).isAbsolute())
            for page_name in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                selector = window.pages[page_name].feedback_channel
                self.assertEqual(
                    tuple(selector.itemText(index) for index in range(selector.count())),
                    ("Current 1", "Current 2"),
                )
                threshold = window.pages[page_name].threshold
                self.assertEqual(threshold.unit_label.text(), "pA")
                self.assertAlmostEqual(float(threshold.variable.get()), 2000.0)
                self.assertEqual(
                    window.pages[page_name].accept_approach_button.text(),
                    "Accept current Z as contact and continue",
                )
            self.assertAlmostEqual(window.pages["Approach"].parameters().feedback_threshold, 2.0)
            self.assertAlmostEqual(window.pages["Approach + CV"].parameters().feedback_threshold_na, 2.0)
            approach_cv = window.pages["Approach + CV"]
            approach_cv.x_position.entry.setText("25")
            approach_cv.y_position.entry.setText("35")
            self.assertEqual((approach_cv.parameters().x_um, approach_cv.parameters().y_um), (25.0, 35.0))
            self.assertEqual(window.next_waypoint_button.text(), "End waypoint")
            self.assertIn("does not confirm contact", window.next_waypoint_button.toolTip())
            for page_name in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                curve = window.pages[page_name].approach_curve
                self.assertEqual(curve.graph.getAxis("bottom").labelText, "Z position (µm)")
            for page_name in ("CV", "Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                preview = window.pages[page_name].program_preview
                self.assertGreater(len(preview.labels), 0, page_name)
            approach_cv_tabs = approach_cv.findChildren(QtWidgets.QTabWidget)[0]
            self.assertEqual(
                tuple(approach_cv_tabs.tabText(index) for index in range(approach_cv_tabs.count())),
                ("Time traces", "Voltammogram", "Approach curve"),
            )
            watch = window.pages["Watch current"]
            self.assertEqual(watch.stop_recording_button.text(), "Stop and save")
            self.assertEqual(watch.live_button.text(), "Start live view")
            move = window.pages["Move piezo"]
            self.assertEqual(
                tuple(move.axis.itemText(index) for index in range(move.axis.count())),
                ("X", "Y", "Z"),
            )
        finally:
            window.poll_timer.stop()
            window.close()

    def test_analysis_uses_separate_raw_and_voltammogram_tabs(self) -> None:
        window = AnalysisWindow()
        window.resize(1080, 680)
        window.show()
        self.qt_app.processEvents()
        try:
            labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
            self.assertEqual(labels, ["Raw traces", "Voltammograms", "Raw data table", "Metadata"])
            self.assertIsNot(window.raw_current_plot, window.cv_plot)
            window.tabs.setCurrentIndex(1)
            self.qt_app.processEvents()
            self.assertTrue(window.export_button.isVisible())
        finally:
            window.close()

    def test_instrument_readout_tracks_latest_sample_and_clears_on_disconnect(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        sample = Sample(1.0, 12.3456, 23.4567, 34.5678, -0.2, 0.4, 1.25, -0.75)
        try:
            window._consume_acquired([sample], finalize=False)
            values = window.instrument_readout.value_labels
            self.assertEqual(values["x_um"].text(), "12.346 µm")
            self.assertEqual(values["y_um"].text(), "23.457 µm")
            self.assertEqual(values["z_um"].text(), "34.568 µm")
            self.assertEqual(values["voltage1_v"].text(), "-0.200 V")
            self.assertEqual(values["voltage2_v"].text(), "+0.400 V")
            self.assertEqual(values["current1_na"].text(), "+1.250 nA")
            self.assertEqual(values["current2_na"].text(), "-0.750 nA")

            window._set_connection_ui(False)
            self.assertEqual(values["x_um"].text(), "— µm")
            self.assertEqual(values["current2_na"].text(), "— nA")
        finally:
            window.close()

    def test_heatmap_has_compact_labelled_scale(self) -> None:
        heatmap = Heatmap("nA", "Current 1")
        heatmap.resize(500, 420)
        heatmap.show()
        heatmap.set_data({(0, 0): 1.5, (0, 1): 2.5}, 1, 2)
        self.qt_app.processEvents()
        try:
            self.assertEqual(heatmap.color_bar.getAxis("left").labelText, "Current 1 (nA)")
            self.assertEqual(heatmap.color_bar.levels(), (1.5, 2.5))
            self.assertFalse(hasattr(heatmap.view, "ui"))
        finally:
            heatmap.close()

    def test_rolling_plot_discards_only_old_display_points(self) -> None:
        plot = Plot("Rolling", "Value", ("#12877f",), max_points=1000, rolling_window_s=120)
        try:
            for second in range(301):
                plot.append(float(second), float(second), redraw=False)
            plot.redraw()
            self.assertGreaterEqual(plot.x_values[0], 180.0)
            self.assertEqual(plot.x_values[-1], 300.0)
            self.assertLessEqual(len(plot.x_values), 121)
        finally:
            plot.close()

    def test_watch_current_only_plots_during_an_explicit_live_session(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        watch = window.pages["Watch current"]
        first = Sample(1.0, 50, 50, 50, 0.1, 0, 1.25, 0.2)
        second = Sample(2.0, 50, 50, 50, 0.1, 0, 1.50, 0.2)
        try:
            self.assertFalse(watch.live_enabled)
            watch.on_samples([first])
            self.assertEqual(len(watch.plot.x_values), 0)

            watch.set_live_view(True)
            watch.on_samples([first])
            self.assertEqual(len(watch.plot.x_values), 1)
            self.assertEqual(watch.current_label.text(), "+1.250 nA")

            watch.set_live_view(False)
            watch.on_samples([second])
            self.assertEqual(len(watch.plot.x_values), 1)
            self.assertEqual(watch.current_label.text(), "+1.250 nA")

            watch.set_live_view(True)
            self.assertEqual(len(watch.plot.x_values), 0)
            self.assertEqual(watch.live_button.text(), "Stop live view")
        finally:
            window.close()

    def test_watch_position_is_separate_and_opt_in(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        monitor = window.pages["Watch position"]
        sample = Sample(1.0, 12.0, 23.0, 34.0, 0.1, 0.0, 1.25, 0.2)
        try:
            self.assertFalse(monitor.live_enabled)
            monitor.on_samples([sample])
            self.assertEqual(len(monitor.plot.x_values), 0)
            monitor.set_live_view(True)
            monitor.on_samples([sample])
            self.assertEqual(tuple(series[-1] for series in monitor.plot.series), (12.0, 23.0, 34.0))
            self.assertEqual(tuple(curve.name() for curve in monitor.plot.curves), ("X", "Y", "Z"))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
