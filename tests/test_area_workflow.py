"""Full analysis-worker and UI contracts for experimental diameter normalization."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from PySide6 import QtWidgets as Q
from echemtips.analysis_area import estimate_landings, normalize_dataset, RetractionConfig
from echemtips.analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles
from echemtips.analysis_tools import cv_selections, hop_selections
from echemtips.analysis_jobs import LoadRecording
from echemtips.analysis_frames import prepare_frames
from tests.test_analysis_frames import cv_dataset


def scan_fixture():
    """Three two-cycle CV hops with positive, negative and missing detachment signals."""
    base=cv_dataset(); parts=[]
    for pixel,group in enumerate(hop_selections(base)):
        m=group.rows.matrix.copy(); m[:,0]=np.arange(len(m))*.01
        t=np.arange(1200)*.01
        z=50-np.maximum(t-1,0)
        current=np.where(t<4,.03 if pixel==0 else -.03,0.)
        if pixel==2: current[:]=0
        tail=np.column_stack((t+m[-1,0]+.01,np.full(len(t),-.2),current,np.full(len(t),pixel),z))
        combined=np.vstack((m,tail)); combined[:,0]+=pixel*20
        parts.append(combined)
    return AnalysisDataset(base.path,base.columns,NumericRows(base.columns,np.vstack(parts)),base.metadata)


class AreaWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=Q.QApplication.instance() or Q.QApplication([])

    def test_hop_geometry_and_failed_landing(self):
        d=scan_fixture(); r=estimate_landings(d)
        self.assertEqual([x['status'] for x in r],['estimated','estimated','unavailable'],r)
        for x in r[:2]: self.assertAlmostEqual(x['diameter_um'],3,delta=.1)
        d.metadata['scan_grid']['pixels'][0]['contact_detected']=False
        self.assertEqual(estimate_landings(d)[0]['status'],'unavailable')

    def test_motion_contaminated_cycle_uses_stationary_fallback(self):
        d = scan_fixture()
        # Reproduce an extracted LSV interval that incorrectly includes motion.
        contaminated = SimpleNamespace(rows=d.rows)
        from echemtips.analysis_surface_z import hop_dataset
        surfaces = [extract_cv_cycles(hop_dataset(d,g))[-1].rows for g in hop_selections(d)]
        with patch('echemtips.analysis_core.extract_cv_cycles', return_value=[contaminated]), \
             patch('echemtips.analysis_surface_z.stationary_sweep_rows', side_effect=surfaces) as fallback:
            results = estimate_landings(d)
        self.assertEqual(fallback.call_count, 3)
        self.assertEqual([r['status'] for r in results], ['estimated','estimated','unavailable'])

    def test_recorded_sensitivity_supplies_saturation_limit(self):
        d = scan_fixture()
        d.metadata['settings'] = dict(mode='NI FPGA',current1_v_per_na=.5)
        with patch('echemtips.analysis_area.detect_retraction', return_value={}) as detector:
            estimate_landings(d)
        self.assertEqual(detector.call_count, 3)
        self.assertTrue(all(c.kwargs['current_limit_na']==20. for c in detector.call_args_list))

    def test_worker_raw_detection_smoothed_density_and_cycles(self):
        d=scan_fixture(); original=d.rows.matrix.copy()
        task=LoadRecording(1,d.path,source=d,smoothing_window=11,
                           area_config=dict(mode='retraction',diagnostics_enabled=True))
        output=[];task.signals.finished.connect(lambda *args:output.append(args));task.run()
        self.assertEqual(output[0][2],'')
        processed,cycles,groups=output[0][1]
        self.assertEqual(len(cycles),6)
        self.assertEqual([r['status'] for r in task.area_results],['estimated','estimated','unavailable'])
        self.assertIn('estimated_diameter_um',processed.columns)
        density=prepare_frames(processed,groups['cv'],channel='current_density1_ma_cm2',axis=[0.])
        self.assertEqual(density.recipe['value_unit'],'mA/cm²')
        self.assertTrue(np.isnan(density.values[:,2]).all())
        np.testing.assert_array_equal(d.rows.matrix,original)

    def test_ui_density_labels_maps_exports_and_diagnostic(self):
        from echemtips.analysis_window import AnalysisWindow
        from echemtips.analysis_export import snapshot
        from echemtips.figure_units import label_unit, unit_factor
        with TemporaryDirectory() as folder:
            w=AnalysisWindow(data_folder=folder)
            try:
                raw=scan_fixture(); results=estimate_landings(raw)
                d=normalize_dataset(raw,dict(mode='retraction'),results)
                w.dataset=d; w.source_dataset=raw; w.cycles=extract_cv_cycles(d)
                w.groups=dict(cv=cv_selections(d),hops=hop_selections(d)); w._refresh_all()
                w.cv_current.setCurrentText('Current density 1')
                self.assertIn('mA/cm²',w.cv_plot.y_label)
                fig=snapshot(w.cv_plot);self.assertIsNotNone(fig.context['normalization'])
                self.assertEqual(label_unit(fig.ylabel),'mA/cm²')
                self.assertAlmostEqual(unit_factor('mA/cm²','µA/cm²'),1000)
                panel=w.map_panel;panel.channel.setCurrentIndex(panel.channel.findData('current_density1_ma_cm2'))
                self.assertEqual(panel.map.base_unit,'mA/cm²');self.assertEqual(len(panel.points),2)
                w.area_panel.set_dataset(raw,results,dict(mode='none'))
                self.assertEqual(w.area_panel.table.rowCount(),3)
                self.assertEqual(w.area_panel.table.item(2,5).text(),results[2]['reason'])
                self.assertIn('Detected break',[s[0] for s in w.area_panel.plot.series])
                w.area_panel.table.setCurrentCell(2,0)
                self.assertNotIn('Detected break',[s[0] for s in w.area_panel.plot.series])
                w.area_panel.slope.setValue(1.2);self.assertTrue(w.area_panel.dirty)
            finally: w.close()

    def test_no_tags_and_invalid_calibration(self):
        d=cv_dataset(); keep=[i for i,c in enumerate(d.columns) if c!='scan_pixel']
        plain=AnalysisDataset(d.path,tuple(d.columns[i] for i in keep),NumericRows(tuple(d.columns[i] for i in keep),d.rows.matrix[:,keep]),d.metadata)
        self.assertEqual(estimate_landings(plain),[])
        task=LoadRecording(1,d.path,source=d,area_config=dict(mode='nominal',area_um2=-1))
        output=[];task.signals.finished.connect(lambda *a:output.append(a));task.run()
        self.assertIsNone(output[0][1]);self.assertIn('positive',output[0][2])

    def test_it_retraction_and_cancel(self):
        columns=('elapsed_s','voltage1_v','current1_na','scan_pixel','z_um')
        t=np.arange(1600)*.01
        e=np.where(t<1,-.1,np.where(t<2,.4,-.1))
        z=50-np.maximum(t-3,0)
        current=np.where(t<6,-.03,0.)
        rows=NumericRows(columns,np.column_stack((t,e,current,np.zeros(len(t)),z)))
        metadata={'parameters':dict(initial_potential_v=-.1,step_potential_v=.4,return_potential_v=-.1,
                                    initial_hold_s=1,step_hold_s=1,return_hold_s=1,cycles=1),
                  'scan_grid':{'pixels':[dict(scan_pixel=0,x_um=0,y_um=0)]}}
        d=AnalysisDataset(Path('it.csv'),columns,rows,metadata)
        r=estimate_landings(d)
        self.assertEqual(r[0]['status'],'estimated',r)
        self.assertAlmostEqual(r[0]['diameter_um'],3,delta=.1)
        from echemtips.analysis_core import AnalysisError
        with self.assertRaisesRegex(AnalysisError,'Cancelled'):
            estimate_landings(d,cancelled=lambda:True)


if __name__=='__main__':unittest.main()
