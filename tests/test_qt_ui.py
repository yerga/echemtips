from __future__ import annotations

import os
import math
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from echemtips.analysis_window import AnalysisWindow
from echemtips.models import Sample
from echemtips.qt_common import Heatmap, Plot, TimedXYPlot
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
            self.assertIn("Samples averaged per data point", settings_text)
            self.assertNotIn("Samples per point", settings_text)
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
                history = window.pages[page_name].approach_history
                self.assertEqual(history.graph.getAxis("bottom").labelText, "Z position (µm)")
                self.assertEqual(history.history_window_s, 60)
            for page_name in ("CV", "Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                preview = window.pages[page_name].program_preview
                self.assertGreater(len(preview.labels), 0, page_name)
            for page_name in ("Scan hopping + CV", "Scan hopping + I-t"):
                scan_page = window.pages[page_name]
                self.assertIn("X 15 µm · Y 15 µm", scan_page.spacing_label.text())
                self.assertIn("plus the first approach", scan_page.duration_label.text())
                self.assertEqual(
                    tuple(scan_page.scan_pattern.itemText(index) for index in range(scan_page.scan_pattern.count())),
                    ("Serpentine", "Raster"),
                )
                self.assertFalse(scan_page.line_retract.entry.isEnabled())
                scan_page.scan_pattern.setCurrentText("Raster")
                self.assertTrue(scan_page.line_retract.entry.isEnabled())
                self.assertFalse(scan_page.parameters().serpentine)
                self.assertEqual(scan_page.start_z.findChildren(QtWidgets.QLabel)[0].text(), "Initial approach Z")
                self.assertEqual(scan_page.retract_distance.findChildren(QtWidgets.QLabel)[0].text(), "Retract distance from contact")
                self.assertAlmostEqual(scan_page.parameters().retract_distance_um, 10.0)
                self.assertEqual(scan_page.z_plot.rolling_window_s, 60)
                self.assertEqual(scan_page.current_plot.rolling_window_s, 60)
            approach_cv_tabs = approach_cv.findChildren(QtWidgets.QTabWidget)[0]
            self.assertEqual(
                tuple(approach_cv_tabs.tabText(index) for index in range(approach_cv_tabs.count())),
                ("Time traces", "Voltammogram", "Approach curves"),
            )
            expected_sections = {
                "Approach": ("1 · Z movement", "2 · Contact detection", "3 · Optional XY preposition"),
                "Approach + CV": ("1 · Approach", "2 · Optional XY preposition", "3 · Cyclic voltammetry"),
                "Approach + I-t": ("1 · Z movement", "2 · Contact detection", "3 · Optional XY preposition", "4 · I–t potential program"),
                "Scan hopping + CV": ("1 · Scan area and path", "2 · Motion and contact", "3 · Cyclic voltammetry"),
                "Scan hopping + I-t": ("1 · Scan area and path", "2 · Motion and contact", "3 · I–t potential program"),
            }
            for page_name, sections in expected_sections.items():
                page_text = {item.text() for item in window.pages[page_name].findChildren(QtWidgets.QLabel)}
                for section in sections:
                    self.assertIn(section, page_text, f"{page_name}: {section}")
            watch = window.pages["Watch current"]
            self.assertEqual(watch.stop_recording_button.text(), "Stop and save")
            self.assertEqual(watch.live_button.text(), "Start live view")
            self.assertIsNot(watch.current1_plot, watch.current2_plot)
            self.assertEqual(watch.current1_plot.graph.getAxis("left").labelText, "Current 1 (nA)")
            self.assertEqual(watch.current2_plot.graph.getAxis("left").labelText, "Current 2 (nA)")
            monitor = window.pages["Watch position"]
            self.assertEqual(
                tuple(plot.graph.getAxis("left").labelText for plot in (monitor.x_plot, monitor.y_plot, monitor.z_plot)),
                ("X position (µm)", "Y position (µm)", "Z position (µm)"),
            )
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

    def test_scan_trace_elapsed_time_restarts_with_each_scan(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        page = window.pages["Scan hopping + CV"]
        try:
            page._elapsed_origin_s = None
            first = Sample(712.5, 35, 35, 55, 0.1, 0, 1, 0)
            second = Sample(713.0, 35, 35, 56, 0.1, 0, 1.1, 0)
            self.assertEqual(page.elapsed_from_start(first), 0.0)
            self.assertEqual(page.elapsed_from_start(second), 0.5)
            page._elapsed_origin_s = None
            self.assertEqual(page.elapsed_from_start(Sample(900, 35, 35, 55, 0.1, 0, 1, 0)), 0.0)
        finally:
            window.close()

    def test_current_vs_z_history_uses_time_for_its_rolling_window(self) -> None:
        plot = TimedXYPlot("History", "Current (nA)", "#12877f", 1000, "Z position (µm)", 60)
        try:
            plot.append_timed(0, 50, 1, redraw=False)
            plot.append_timed(30, 55, 2, redraw=False)
            plot.add_gap(61)
            plot.append_timed(61, 60, 3, redraw=False)
            plot.redraw()
            self.assertEqual(plot.clock_values, [30, 61, 61])
            self.assertEqual(plot.x_values[0], 55)
            self.assertTrue(math.isnan(plot.x_values[1]))
            self.assertEqual(plot.series[0][-1], 3)
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
            self.assertEqual(len(watch.current1_plot.x_values), 0)

            watch.set_live_view(True)
            watch.on_samples([first])
            self.assertEqual(len(watch.current1_plot.x_values), 1)
            self.assertEqual(len(watch.current2_plot.x_values), 1)
            self.assertEqual(watch.current_label.text(), "i1  +1.250 nA")
            self.assertEqual(watch.current2_label.text(), "i2  +0.200 nA")

            watch.set_live_view(False)
            watch.on_samples([second])
            self.assertEqual(len(watch.current1_plot.x_values), 1)
            self.assertEqual(watch.current_label.text(), "i1  +1.250 nA")

            watch.set_live_view(True)
            self.assertEqual(len(watch.current1_plot.x_values), 0)
            self.assertEqual(len(watch.current2_plot.x_values), 0)
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
            self.assertEqual(len(monitor.x_plot.x_values), 0)
            monitor.set_live_view(True)
            monitor.on_samples([sample])
            self.assertEqual(monitor.x_plot.series[0][-1], 12.0)
            self.assertEqual(monitor.y_plot.series[0][-1], 23.0)
            self.assertEqual(monitor.z_plot.series[0][-1], 34.0)
            self.assertEqual(monitor.x_plot.x_values[-1], 0.0)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
