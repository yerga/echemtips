from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from echemtips.analysis_window import AnalysisWindow
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
            watch = window.pages["Watch current"]
            self.assertEqual(watch.stop_recording_button.text(), "Stop and save")
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


if __name__ == "__main__":
    unittest.main()
