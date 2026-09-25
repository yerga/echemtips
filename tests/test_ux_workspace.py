"""Regression checks for operator UX without connected hardware."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from echemtips.ui import EChemTipsApp, create_application
from echemtips.experiments import ExperimentState


class WorkspaceTests(unittest.TestCase):
    """Exercise readiness, immutable setup, and display-only application."""
    @classmethod
    def setUpClass(cls):
        """Create the shared Qt application."""
        cls.qt = create_application([])

    def setUp(self):
        """Isolate settings and avoid background acquisition."""
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ECHEMTIPS_SETTINGS_PATH': str(Path(self.temp.name)/'settings.json')})
        self.env.start()
        self.w = EChemTipsApp(); self.w.poll_timer.stop()

    def tearDown(self):
        """Close only simulated resources."""
        for e in self.w.experiments.values(): e.state = ExperimentState.COMPLETE
        self.w.close(); self.env.stop(); self.temp.cleanup()

    def test_readiness_and_recovery(self):
        """Connected recovery-required targets cannot start an experiment."""
        x = self.w.operator_workspace
        self.assertIn('Connect', x.reason())
        self.w.backend.connect()
        x.recovery_required = True; x.refresh()
        self.assertIn('Recovery', x.reason())
        self.assertFalse(self.w.pages['Scan hopping + CV'].start_button.isEnabled())
        x.recovery_required = False; x.refresh()
        self.assertTrue(self.w.pages['Scan hopping + CV'].start_button.isEnabled())
        self.assertFalse(self.w.pages['Combinatorial scan + CV / LSV'].start_button.isEnabled())

    def test_setup_lock_and_display_changes(self):
        """Display preferences remain editable while run controls are locked."""
        page = self.w.pages['Scan hopping + CV']
        page.experiment.state = ExperimentState.APPROACHING
        self.w.operator_workspace.refresh()
        self.assertFalse(page.body.layout().itemAt(0).widget().isEnabled())
        self.w.apply_settings(replace(self.w.settings, trace_width_px=1.5))
        with self.assertRaisesRegex(ValueError, 'Stop'):
            self.w.apply_settings(replace(self.w.settings, sample_time_us=20))
        page.experiment.state = ExperimentState.COMPLETE
        self.w.operator_workspace.refresh()
        self.assertTrue(page.body.layout().itemAt(0).widget().isEnabled())

    def test_event_journal(self):
        """Operational events are retained for troubleshooting."""
        self.w.toast('UX test saved', 'success')
        self.assertIn('UX test saved', self.w.store.path.with_name('operator-events.jsonl').read_text())
