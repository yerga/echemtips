"""Optional start annotations and immutable reproduction metadata."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from echemtips.ui import create_application, EChemTipsApp
from echemtips.experiment_details import DetailsDialog, DetailsStore, request_details
from echemtips.data import DataRecorder
from echemtips.models import AppSettings


class DetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = create_application([])

    def test_defaults_last_and_blank_fields(self):
        with TemporaryDirectory() as folder:
            store = DetailsStore(Path(folder) / 'settings.json')
            self.assertEqual(store.load(), {})
            store.save({'electrolyte': 'KCl'}, defaults=True)
            store.save({'electrolyte': ''})
            self.assertEqual(store.load()['defaults']['electrolyte'], 'KCl')
            self.assertEqual(store.load()['last']['electrolyte'], '')
            dialog = DetailsDialog(None, {})
            self.assertEqual(dialog.values()['temperature_c'], '')
            self.assertEqual(dialog.values()['pipette'], 'Not specified')
            dialog.close()

    def test_recording_snapshot_excludes_display_and_survives_edits(self):
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            recorder = DataRecorder()
            recorder.operator_details = {'electrolyte': '1 mM mediator; water', 'notes': ''}
            recorder.start('test', settings)
            path = recorder.output_path.with_suffix('.json')
            before = json.loads(path.read_text())
            self.assertEqual(before['operator_metadata']['electrolyte'], '1 mM mediator; water')
            self.assertNotIn('trace_width_px', before['settings'])
            self.assertNotIn('map_z_colormap', before['settings'])
            self.assertEqual(before['settings']['samples_per_point'], 256)
            self.assertIn('version', before['software'])
            recorder.operator_details['electrolyte'] = 'changed'
            settings.samples_per_point = 512
            recorder.finish(settings)
            after = json.loads(path.read_text())
            self.assertEqual(after['settings'], before['settings'])
            self.assertEqual(after['operator_metadata'], before['operator_metadata'])

    def test_atmosphere_choices_default_and_legacy_custom_values(self):
        for previous, expected in [('', 'Air'), ('air', 'Air'), ('Ar', 'Ar'),
                                   ('N2', 'N2'), ('O2', 'O2'), ('CO2', 'CO2'),
                                   ('Other', 'Other'), ('humidified nitrogen', 'humidified nitrogen')]:
            with self.subTest(previous=previous):
                dialog = DetailsDialog(None, {'atmosphere': previous})
                self.assertEqual(dialog.values()['atmosphere'], expected)
                self.assertEqual(dialog.atmosphere.count(), 6)
                dialog.atmosphere.setCurrentText('Other')
                dialog.other_atmosphere.setText('Ar + 5% H2')
                self.assertEqual(dialog.values()['atmosphere'], 'Ar + 5% H2')
                dialog.atmosphere.setCurrentText('N2')
                self.assertEqual(dialog.values()['atmosphere'], 'N2')
                dialog.close()

    def test_cancel_prevents_recording_and_motion_for_experiments_and_watch(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'ECHEMTIPS_SETTINGS_PATH': str(Path(folder)/'settings.json')}):
            window = EChemTipsApp(); window.poll_timer.stop(); window.backend.connect()
            try:
                with patch('echemtips.experiment_details.request_details', return_value=None), patch.object(window.cv_experiment, 'start') as start:
                    with self.assertRaisesRegex(ValueError, 'cancelled'):
                        window.pages['CV']._begin(window.pages['CV'].parameters())
                    window.pages['Watch current'].start_recording()
                    window.pages['Watch position'].start_recording()
                    start.assert_not_called()
                    self.assertFalse(window.recorder.active)
                with patch.object(DetailsDialog, 'exec', return_value=DetailsDialog.DialogCode.Rejected):
                    self.assertIsNone(request_details(window))
                    self.assertFalse(DetailsStore(window.store.path).path.exists())
            finally:
                window.close()
