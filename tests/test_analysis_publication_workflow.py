"""End-to-end selection, crop, reference conversion and session regression tests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import numpy as np
from PySide6 import QtWidgets as Q
from echemtips.analysis_window import AnalysisWindow
from echemtips.analysis_export import export_plot_csv, snapshot, FigureExportDialog, render_figure
from tests.test_analysis_workbench import write_scan


class PublicationWorkflowTests(unittest.TestCase):
    """Exercise the public analysis UI paths on a four-hop synthetic recording."""
    @classmethod
    def setUpClass(cls):
        cls.app=Q.QApplication.instance() or Q.QApplication([])

    def wait(self,window):
        deadline=time.monotonic()+10
        while window.loading and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertFalse(window.loading)

    def test_selected_plot_export_and_empty_cv_visibility(self):
        with TemporaryDirectory() as folder,patch.dict(os.environ,{'ECHEMTIPS_SETTINGS_PATH':str(Path(folder)/'settings.json')}):
            window=AnalysisWindow(data_folder=folder)
            try:
                self.assertTrue(window.export_button.isHidden())
                path=write_scan(folder);window.load_recording(path);self.wait(window)
                self.assertFalse(window.export_button.isHidden())
                window.cycle_tree.clearSelection()
                window.cycle_tree.topLevelItem(1).child(0).setSelected(True)
                window._refresh_cv_plot()
                data=snapshot(window.cv_plot)
                self.assertEqual(len(data.series),1)
                target=Path(folder)/'selected.csv'
                with patch('echemtips.analysis_views.QtWidgets.QFileDialog.getSaveFileName',return_value=(str(target),'')),patch('echemtips.analysis_views.QtWidgets.QMessageBox.information'):
                    export_plot_csv(window.cv_plot)
                with target.open() as stream:rows=list(csv.DictReader(stream))
                self.assertEqual(len(rows),len(data.series[0][1]))
                self.assertEqual({r['series_id'] for r in rows},{'1'})
                self.assertGreater(path.stat().st_size,target.stat().st_size)
            finally:window.close()

    def test_reference_crop_and_session_round_trip(self):
        with TemporaryDirectory() as folder,patch.dict(os.environ,{'ECHEMTIPS_SETTINGS_PATH':str(Path(folder)/'settings.json')}):
            path=write_scan(folder);original=path.read_bytes()
            window=AnalysisWindow(path,data_folder=folder)
            try:
                self.wait(window)
                window.reference_config=dict(enabled=True,mode='database',input_polarity='IUPAC',source='Ag/AgCl (saturated KCl)',target='RHE',source_ph=7,target_ph=7,custom_offset_v=0)
                window.load_recording(path,source=window.source_dataset);self.wait(window)
                self.assertEqual(len(window.cycles),4)
                self.assertIn('vs RHE',window.cv_plot.graph.getAxis('bottom').labelText)
                converted=window.dataset.column('voltage1_v').copy()
                window._apply_smoothing();self.wait(window)
                np.testing.assert_array_equal(window.dataset.column('voltage1_v'),converted)
                window.map_panel.potential.setValue(.2+.197+.0591593497*7)
                window.map_panel.crop_bounds=(10,15,20,20);window.map_panel.refresh()
                self.assertEqual(len(window.map_panel.points),2)
                data=snapshot(window.map_panel.map)
                self.assertEqual(data.spacing,(5,5))
                dialog=FigureExportDialog(window.map_panel.map);fig=render_figure(data,dialog.options())
                self.assertEqual(tuple(fig.axes[0].get_ylim()),(17.5,22.5));fig.clear();dialog.deleteLater()
                session=window.workspace.snapshot()
                window.workspace.pending=session
                window.load_recording(path);self.wait(window)
                self.assertEqual(window.map_panel.crop_bounds,(10,15,20,20))
                self.assertEqual(len(window.map_panel.points),2)
                np.testing.assert_array_equal(window.dataset.column('voltage1_v'),converted)
                self.assertEqual(path.read_bytes(),original)
                window.load_recording(path);self.wait(window)
                self.assertNotIn('analysis_reference',window.dataset.metadata)
            finally:window.close()
