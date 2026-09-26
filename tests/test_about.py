"""Shared About dialog identity, links and nonmodal menu behaviour."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtWidgets
from echemtips import __version__
from echemtips.about import AboutDialog, DOCUMENTATION_URL, SUPPORT_URL, install_help_menu, support_details
from echemtips.ui import create_application, EChemTipsApp
from echemtips.analysis_window import AnalysisWindow


class AboutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def test_dialog_content_and_clipboard(self):
        dialog = AboutDialog()
        try:
            self.assertFalse(dialog.isModal())
            self.assertIn("WEC-SPM", dialog.text.toPlainText())
            self.assertIn(DOCUMENTATION_URL, dialog.text.toHtml())
            self.assertIn(SUPPORT_URL, dialog.text.toHtml())
            self.assertIn(__version__, support_details())
            dialog.copy_button.click()
            self.assertEqual(self.app.clipboard().text(), support_details())
            dialog.show(); self.app.processEvents()
            dialog.grab().save("/private/tmp/echemtips-about.png")
        finally:
            dialog.close()

    def test_menu_reuses_dialog_and_opens_requested_link(self):
        window = QtWidgets.QMainWindow()
        try:
            menu = install_help_menu(window)
            with patch.object(QtGui.QDesktopServices, "openUrl", return_value=True) as open_url:
                menu.actions()[0].trigger()
                open_url.assert_called_once_with(QtCore.QUrl(DOCUMENTATION_URL))
            action = menu.actions()[-1]
            self.assertEqual(action.menuRole(), QtGui.QAction.MenuRole.AboutRole)
            action.trigger(); dialog = window._about_dialog
            action.trigger(); self.assertIs(window._about_dialog, dialog)
            self.assertFalse(dialog.isModal())
        finally:
            window.close()

    def test_both_applications_offer_about(self):
        with TemporaryDirectory() as folder:
            windows = (EChemTipsApp(), AnalysisWindow(data_folder=folder))
            try:
                windows[0].poll_timer.stop()
                for window in windows:
                    help_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Help")
                    self.assertTrue(any(a.text() == "About eChemTips…" for a in help_menu.actions()))
            finally:
                for window in windows: window.close()
