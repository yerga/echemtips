"""Optional desktop integration must not affect acquisition or recording data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PySide6 import QtCore, QtWidgets
from echemtips.models import AppSettings
from echemtips.recent_recordings import RecentRecordings, recording_from_urls
from echemtips.desktop import DesktopStatus


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_drop_resolves_sidecar_and_rejects_remote_or_multiple(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder).resolve()/'one.csv'; a.touch()
            b=Path(folder).resolve()/'two.csv'; b.touch()
            url=QtCore.QUrl.fromLocalFile
            self.assertEqual(recording_from_urls([url(str(a))]),a)
            self.assertEqual(recording_from_urls([url(str(a.with_suffix('.json'))),url(str(a))]),a)
            self.assertIsNone(recording_from_urls([url(str(a)),url(str(b))]))
            self.assertIsNone(recording_from_urls([QtCore.QUrl('https://example.com/run.csv')]))
            self.assertIsNone(recording_from_urls([url(str(Path(folder)/'session.json'))]))

    def test_recent_bounded_shared_and_clear_never_deletes(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'ECHEMTIPS_SETTINGS_PATH':str(Path(folder)/'settings.json')}):
            window=QtWidgets.QMainWindow(); opener=Mock()
            recent=RecentRecordings(window,window.menuBar().addMenu('File'),opener)
            for n in range(15): recent.remember(Path(folder)/f'{n}.csv')
            self.assertEqual(len(recent.paths()),12)
            path=Path(folder).resolve()/'last.csv'; path.touch(); recent.remember(path); recent.remember(path)
            self.assertEqual(recent.paths().count(str(path)),1)
            recent.rebuild(); recent.menu.actions()[0].trigger(); opener.assert_called_once_with(path)
            self.assertFalse(recent.menu.actions()[1].isEnabled())
            recent.clear(); self.assertEqual(recent.paths(),[]); self.assertTrue(path.exists())
            window.close()

    def test_progress_clears_and_notifications_are_opt_in(self):
        window=QtWidgets.QMainWindow(); window.setWindowTitle('Control')
        window.settings=AppSettings()
        experiment=SimpleNamespace(active=True,progress=.425)
        window.experiments={'scan':experiment}
        status=DesktopStatus(window)
        with patch.object(status.tray,'showMessage') as message, patch.object(status.tray,'show'), patch.object(status.tray,'isSystemTrayAvailable',return_value=True), patch.object(status.tray,'supportsMessages',return_value=True):
            status.notify('Complete','Test'); message.assert_not_called()
            window.settings.desktop_notifications=True
            status.notify('Error','Test',error=True); message.assert_called_once()
        status.refresh(); self.assertEqual(window.windowTitle(),'Control — 42%')
        window.settings.desktop_progress=False; status.refresh(); self.assertEqual(window.windowTitle(),'Control')
        window.settings.desktop_progress=True; status.refresh()
        experiment.active=False; status.refresh(); self.assertEqual(window.windowTitle(),'Control')
        status.clear(); window.close()

    def test_control_drop_launch_never_opens_active_recording(self):
        from echemtips.ui import EChemTipsApp
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'run.csv'; path.touch()
            host=SimpleNamespace(settings=AppSettings(save_directory=folder),recorder=SimpleNamespace(active=True,output_path=path),show_error=Mock())
            with patch.object(QtCore.QProcess,'startDetached') as launch:
                EChemTipsApp.launch_analysis(host,path=path)
                launch.assert_not_called(); host.show_error.assert_called_once()
                host.recorder.active=False; launch.return_value=(True,123)
                EChemTipsApp.launch_analysis(host,path=path)
                self.assertIn(str(path.resolve()),launch.call_args.args[1])
