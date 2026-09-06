from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from echemtips.analysis_window import AnalysisWindow
from echemtips.models import Sample
from echemtips.qt_common import Heatmap
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
            for page_name in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                selector = window.pages[page_name].feedback_channel
                self.assertEqual(
                    tuple(selector.itemText(index) for index in range(selector.count())),
                    ("Current 1", "Current 2"),
                )
            watch = window.pages["Watch current"]
            self.assertEqual(watch.stop_recording_button.text(), "Stop and save")
            self.assertEqual(watch.live_button.text(), "Start live view")
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


if __name__ == "__main__":
    unittest.main()
