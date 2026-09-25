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
        self.assertFalse(page.setup_panel.isEnabled())
        self.w.apply_settings(replace(self.w.settings, trace_width_px=1.5))
        with self.assertRaisesRegex(ValueError, 'Stop'):
            self.w.apply_settings(replace(self.w.settings, sample_time_us=20))
        page.experiment.state = ExperimentState.COMPLETE
        self.w.operator_workspace.refresh()
        self.assertTrue(page.setup_panel.isEnabled())

    def test_event_journal(self):
        """Operational events are retained for troubleshooting."""
        self.w.toast('UX test saved', 'success')
        self.assertIn('UX test saved', self.w.store.path.with_name('operator-events.jsonl').read_text())

    def test_presets_and_numeric_validation(self):
        """Presets round-trip known controls and numeric errors identify fields."""
        from echemtips.workspace_layout import form_values, restore_form
        page = self.w.pages['Scan hopping + CV']
        before = form_values(page)
        page.start_z.entry.setText('not a number')
        with self.assertRaisesRegex(ValueError, 'Initial approach Z'):
            page.parameters()
        restore_form(page, before)
        self.assertEqual(page.parameters().start_z_um, float(before['start_z']))

    def test_freeze_is_display_only(self):
        """A frozen plot still receives data and can return to its latest buffer."""
        plot = self.w.pages['Watch current'].current1_plot
        plot.view_controls.set_frozen(True)
        plot.append(0, 1); plot.redraw()
        self.assertEqual(list(plot.x_values), [0])
        plot.view_controls.set_frozen(False)
        self.assertEqual(len(plot.curves[0].getData()[0]), 1)
