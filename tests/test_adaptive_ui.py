"""Adaptive page registration, laptop layout, and invalid-hop analysis handling."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

import numpy as np

from echemtips.analysis_core import AnalysisDataset, NumericRows, pixel_groups


class AdaptiveUITests(unittest.TestCase):
    def test_invalid_objectives_excluded_but_raw_rows_retained(self):
        columns=['elapsed_s','scan_pixel','current1_na']
        rows=NumericRows(columns,np.array([[0,0,1],[1,1,2],[2,2,3]],dtype=float))
        dataset=AnalysisDataset(Path('run.csv'),columns,rows,{'scan_grid':{'path':'adaptive','pixels':[
            {'scan_pixel':0,'valid':True},{'scan_pixel':1,'valid':False},{'scan_pixel':2,'valid':True}]}})
        self.assertEqual([p for p,_ in pixel_groups(dataset)],[0,2])
        self.assertEqual(len(dataset.rows),3)

    def test_library_and_laptop_map(self):
        from echemtips.ui import create_application, EChemTipsApp
        app=create_application([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'ECHEMTIPS_SETTINGS_PATH':str(Path(folder)/'settings.json')}):
            window=EChemTipsApp(); window.resize(1280,800); window.show()
            window.show_page('Adaptive hopping + LSV')
            app.processEvents()
            page=window.pages['Adaptive hopping + LSV']
            self.assertIn('adaptive',window.experiments)
            self.assertGreater(page.maps['measured'].height(),300)
            page.experiment.params.attempts=[{'scan_pixel':0,'xy':[20,20],'valid':True,'objective_na':.01,
                                            'contact_z_um':60,'reason':'valid','selection_reason':'survey'}]
            page._refresh_maps(); app.processEvents()
            self.assertEqual(page.decisions.rowCount(),1)
            self.assertTrue(page.approval.get())
            self.assertFalse(page.confirm.get())
            window.settings.save_directory=folder
            window.backend.connect(); window._set_connection_ui(True)
            page.confirm.set(True)
            with patch.object(window,'show_error',side_effect=AssertionError): page.start()
            self.assertTrue(window.recorder.active)
            self.assertEqual(page.experiment.phase,'approval')
            self.assertFalse(window.next_waypoint_button.isEnabled())
            output=window.recorder.output_path
            window.stop_experiment('adaptive')
            self.assertFalse(window.recorder.active)
            self.assertTrue(output.with_suffix('.report.md').exists())
            window.close()


if __name__=='__main__': unittest.main()
