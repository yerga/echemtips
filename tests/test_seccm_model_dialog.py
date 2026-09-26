"""Offscreen checks of optional, read-only model UI and original-data comparison."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
import json
import numpy as np
from PySide6 import QtWidgets as Q, QtCore
from echemtips.analysis_core import AnalysisDataset
from echemtips.seccm_model_dialog import ModelDialog


class ModelDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        self.d = ModelDialog()

    def tearDown(self):
        self.d.close()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_modes_and_stale_export(self):
        self.assertIsNotNone(self.d.result)
        self.d.kind.setCurrentIndex(1)
        self.assertIsNone(self.d.result)
        self.d.calculate()
        self.assertEqual(self.d.result['x_unit'], 's')
        self.d.fields['height_m'][0].setValue(1e8)
        self.d.calculate()
        self.assertIsNone(self.d.result)
        self.assertFalse(self.d.export_button.isEnabled())

    def test_original_comparison_and_polarity(self):
        dataset = AnalysisDataset(Path('example.csv'), ('elapsed_s','voltage1_v','current1_na'),
            [{'elapsed_s':i/10, 'voltage1_v':i/10, 'current1_na':i/100} for i in range(11)],
            {'settings':{'polarity_convention':'Instrument-native'}})
        original = dataset.rows.matrix.copy()
        self.d.close(); self.d = ModelDialog(dataset=dataset)
        self.d.source.setCurrentIndex(1); self.d.calculate()
        self.assertIsNotNone(self.d.result)
        self.assertEqual(self.d.result['x'][-1], -1)
        self.assertAlmostEqual(self.d.result['measured_current_a'][-1], -1e-10)
        np.testing.assert_array_equal(original, dataset.rows.matrix)
        self.d.kind.setCurrentIndex(1); self.d.origin.setValue(.5); self.d.calculate()
        self.assertAlmostEqual(self.d.result['x'][0], .1)
        self.assertEqual(len(self.d.result['x']), 5)

    def test_export_and_preset(self):
        with TemporaryDirectory() as folder:
            path = str(Path(folder)/'parameters.json')
            with patch.object(Q.QFileDialog, 'getSaveFileName', return_value=(path,'')):
                self.d.save_parameters()
            self.d.fields['radius_m'][0].setValue(350)
            with patch.object(Q.QFileDialog, 'getOpenFileName', return_value=(path,'')):
                self.d.load_parameters()
            self.assertEqual(self.d.fields['radius_m'][0].value(), 200)
            self.d.calculate()
            with patch.object(Q.QFileDialog, 'getSaveFileName', return_value=(str(Path(folder)/'result.json'),'')):
                self.d.export_result()
            result = json.loads((Path(folder)/'result.json').read_text())
            self.assertEqual(result['output_polarity'], 'IUPAC')
            self.assertEqual(len(result['x']), len(result['predicted_current_a']))

    def test_source_protection(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'recording.csv'; path.write_text('original')
            self.d.dataset = AnalysisDataset(path, ('elapsed_s',), [{'elapsed_s':0}], {})
            with patch.object(Q.QFileDialog, 'getSaveFileName', return_value=(str(path),'')), patch.object(Q.QMessageBox, 'warning') as warning:
                self.d.export_result()
            warning.assert_called_once()
            self.assertEqual(path.read_text(), 'original')
