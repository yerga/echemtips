"""Offscreen test of the opt-in GUI's asynchronous simulation stop path."""
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from echemtips.experimental_stop_app import ExperimentalStopApp
from echemtips.experiments import ExperimentState
from echemtips.models import ApproachParameters
from echemtips.ui import create_application


class StopAppTests(unittest.TestCase):
    def test_stop_saves_aborted_record_and_returns_z_without_freezing_gui(self):
        qt = create_application()
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ECHEMTIPS_SETTINGS_PATH": str(Path(directory) / "settings.json"),
        }):
            window = ExperimentalStopApp()
            try:
                window.settings.save_directory = directory
                window.backend.connect()
                window.backend._positions["Z"] = .05
                p = ApproachParameters(start_z_um=0, end_z_um=90, retract_rate_um_s=100)
                exp = window.experiments["approach"]
                exp.params, exp.state = p, ExperimentState.APPROACHING
                window.recorder.start("Approach", window.settings, p)
                window._start_acquisition()
                window.stop_experiment("approach")
                self.assertIsNotNone(window._stop_trial)
                self.assertFalse(window.poll_timer.isActive())
                deadline = time.monotonic() + 5
                while window._stop_trial is not None and time.monotonic() < deadline:
                    qt.processEvents()
                    time.sleep(.01)
                self.assertIsNone(window._stop_trial)
                self.assertTrue(window.poll_timer.isActive())
                self.assertFalse(window.recorder.active)
                self.assertEqual(exp.state, ExperimentState.ABORTED)
                self.assertEqual(window.backend.read_sample().z_um, 0)
                import json
                saved = json.loads(next(Path(directory).glob("*.json")).read_text())
                self.assertEqual(saved["status"], "aborted")
                self.assertTrue(saved["experimental_stop_return"]["recovery_and_return_excluded_from_csv"])
            finally:
                window.close()
