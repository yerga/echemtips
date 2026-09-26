"""End-to-end EIS UI simulation with the shared recording/finalization path."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch
from echemtips.ui import create_application, EChemTipsApp
from echemtips.models import AppSettings
from echemtips.eis import EISParameters


class EISUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=create_application()

    def test_complete_page_and_recording(self):
        with TemporaryDirectory() as folder:
            w=EChemTipsApp();w.poll_timer.stop()
            try:
                w.apply_settings(AppSettings(save_directory=folder),persist=False)
                page=w.pages['Approach + EIS']
                self.assertEqual(page.parameters().frequencies_hz,(1.,3.,10.))
                self.assertFalse(page.start_button.isEnabled())
                with patch('echemtips.backends.time.monotonic') as clock, patch.object(w.operator_workspace,'review',return_value=True):
                    clock.return_value=100.
                    w.backend.connect();w._set_connection_ui(True)
                    page._begin(EISParameters(start_z_um=0,end_z_um=1,approach_rate_um_s=2,
                        retract_rate_um_s=2,settling_time_s=.01,frequencies_hz=(3.,10.)))
                    self.assertFalse(w.pause_button.isEnabled())
                    self.assertFalse(w.next_waypoint_button.isEnabled())
                    for n in range(1000):
                        clock.return_value=100+n*.02
                        w._consume_acquired(w.backend.read_samples())
                        if not w.recorder.active:break
                    self.assertFalse(w.recorder.active)
                    page.on_samples([])
                    self.assertEqual(page.table.rowCount(),2)
                    files=list(Path(folder).glob('*.json'))
                    metadata=json.loads(files[0].read_text())
                    self.assertEqual(metadata['status'],'complete')
                    self.assertEqual(len(metadata['parameters']['eis_results']),2)
                    w.show_page('Approach + EIS');w.resize(1366,850);w.show();self.app.processEvents()
                    w.grab().save('/private/tmp/eis-complete.png')
            finally:
                w.backend.disconnect();w.close()
