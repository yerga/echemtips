"""Inline optional setup placement, disclosure and validation contracts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import PropertyMock, patch
from echemtips.ui import create_application, EChemTipsApp
from echemtips.qt_common import Card
from echemtips.optional_setup import OptionalSetup


class OptionalSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=create_application([])
        cls.window=EChemTipsApp(); cls.window.poll_timer.stop()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def test_embedded_collapsed_by_default(self):
        for page in self.window.pages.values():
            for editor in page.findChildren(OptionalSetup):
                parent=editor.parentWidget()
                while parent is not None and not isinstance(parent,Card): parent=parent.parentWidget()
                self.assertIsInstance(parent,Card)
                self.assertFalse(editor.header.isChecked())
                self.assertTrue(editor.body.isHidden())

    def test_disclosure_retains_enabled_values_and_updates_drafts(self):
        p=self.window.pages['Scan hopping + CV']; c=p.conditioning
        c.edit(); self.assertFalse(c.body.isHidden())
        c.checks['pre_hold_enabled'].setChecked(True)
        c.fields['pre_hold_s'].entry.setText('0.7')
        c.edit(); self.assertTrue(c.body.isHidden())
        self.assertTrue(p.parameters().pre_hold_enabled)
        self.assertEqual(p.parameters().pre_hold_s,.7)
        self.assertIn('Pre 0.7 s',c.text())
        c.edit(); c.fields['pre_hold_s'].entry.setText('bad')
        with self.assertRaises(ValueError): p.parameters()
        c.set_values({}); c.edit()

    def test_motion_mode_fields_and_notification(self):
        p=self.window.pages['Scan hopping + I-t']; c=p.motion_profile
        c.edit(); c.checks['retract_profile_enabled'].setChecked(True)
        before=c.change.text(); c.mode.setCurrentIndex(1)
        self.assertNotEqual(c.change.text(),before)
        self.assertFalse(c.fields['retract_switch_distance_um'].isEnabled())
        self.assertTrue(c.fields['retract_buffer_um'].isEnabled())
        self.assertFalse(p.retract_rate.isEnabled())
        c.fields['retract_buffer_um'].entry.setText('0.4'); c.edit()
        self.assertEqual(p.parameters().retract_buffer_um,.4)
        with patch.object(type(self.window),'any_experiment_active',new_callable=PropertyMock,return_value=True):
            c._lock_during_run(); self.assertFalse(c.body.isEnabled())
        c._lock_during_run(); c.set_values({})

    def test_invalid_inline_draft_reports_preset_error(self):
        from echemtips.workspace_layout import preset
        p=self.window.pages['Scan hopping + CV']; c=p.conditioning
        c.checks['pre_hold_enabled'].setChecked(True)
        c.fields['pre_hold_s'].entry.setText('invalid')
        with patch('echemtips.workspace_layout.Q.QFileDialog.getSaveFileName',return_value=('/tmp/unused-optional-preset.json','')), \
             patch.object(self.window,'show_error') as error:
            preset(p,True); error.assert_called_once()
        c.set_values({})
