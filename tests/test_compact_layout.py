"""Regression checks for compact control chrome and usable laptop plot viewports."""
import os
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from PySide6 import QtWidgets
from echemtips.ui import EChemTipsApp, create_application
from echemtips.qt_common import PlotPanel, application_stylesheet
from echemtips.models import Sample


class CompactLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or create_application([])

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, {"ECHEMTIPS_SETTINGS_PATH": str(Path(self.folder.name) / "settings.json")})
        env.start()
        self.addCleanup(env.stop)
        self.window = EChemTipsApp()
        self.window.poll_timer.stop()
        self.addCleanup(self.window.close)

    def settle(self):
        for _ in range(8):
            self.app.processEvents()

    def test_laptop_has_room_for_a_complete_plot_card(self):
        w = self.window
        w.resize(1280, 720)
        w.show()
        for name in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
            w.show_page(name)
            page = w.pages[name]
            page.state_label.setText("Settling at contact")
            page.detail_label.setText("Point 9 of 9 · Settling")
            page.detail_label.show()
            tabs = page.body.findChild(QtWidgets.QTabWidget)
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                self.settle()
                panel = tabs.widget(index)
                if isinstance(panel, PlotPanel):
                    self.assertGreaterEqual(panel.viewport().height(), panel._cards[0].minimumHeight(), name)
            self.assertLessEqual(page.status.height(), 125)
        self.assertLessEqual(w.instrument_readout.height(), 40)
        self.assertLessEqual(w.findChild(QtWidgets.QFrame, "topbar").height(), 70)

    def test_readback_remains_visible_at_small_size_and_larger_fonts(self):
        w = self.window
        w.show()
        for font, width in ((10, 1080), (12, 1280)):
            w.setStyleSheet(application_stylesheet(font))
            w.resize(width, 720)
            w.instrument_readout.current_display_unit = "pA"
            w.instrument_readout.set_sample(Sample(0, 199.999, 199.999, 199.999, -2, -2, -10.123, -10.123))
            self.settle()
            self.assertEqual(w.width(), width)
            for value in w.instrument_readout.value_labels.values():
                self.assertGreaterEqual(value.width(), value.fontMetrics().horizontalAdvance(value.text()))
                self.assertGreaterEqual(value.height(), value.fontMetrics().height())

    def test_footer_only_occupies_space_when_needed(self):
        w = self.window
        w.show()
        with patch.object(w, "_active_page_name", return_value="Scan hopping + CV"):
            w.show_page("Scan hopping + CV")
            self.settle()
            self.assertTrue(w.statusBar().isHidden())
            w.show_page("Settings")
            self.settle()
            self.assertFalse(w.return_button.isHidden())
            self.assertFalse(w.statusBar().isHidden())
            w.return_button.click()
            self.settle()
            self.assertIs(w.stack.currentWidget(), w.pages["Scan hopping + CV"])
            self.assertTrue(w.statusBar().isHidden())
        w.toast("Saved")
        self.assertFalse(w.statusBar().isHidden())
        w.statusBar().clearMessage()
        self.assertTrue(w.statusBar().isHidden())
