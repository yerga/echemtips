"""Contact maps tolerate incomplete waveforms, not arbitrary motion or failed hops."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
import numpy as np
from PySide6 import QtWidgets as Q
from echemtips.analysis_core import AnalysisDataset
from echemtips.analysis_tools import hop_selections
from echemtips.analysis_topography import contact_points, scan_origin
from echemtips.analysis_surface_z import stationary_sweep_rows, stationary_pulse_rows, contact_failure_reason
from echemtips.analysis_export import FigureExportDialog, render_figure, snapshot
from echemtips.analysis_movie import MoviePanel
from echemtips.analysis_frames import MapFrames
from tests.test_analysis_topography import fixture
from echemtips.analysis_views import MapPanel


def recording(e, z, parameters):
    columns = ('elapsed_s','voltage1_v','z_um','scan_pixel')
    rows = [dict(zip(columns,(i*.01,v,h,0))) for i,(v,h) in enumerate(zip(e,z))]
    return AnalysisDataset(Path('/tmp/recovery.csv'),columns,rows,
        dict(parameters=parameters,scan_grid=dict(pixels=[dict(scan_pixel=0,x_um=10.,y_um=20.)])))


class SurfaceRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=Q.QApplication.instance() or Q.QApplication([])

    def test_partial_cv_recovered_without_changing_cycle_extraction(self):
        from echemtips.analysis_core import extract_cv_cycles
        p=dict(cv_start_v=-.2,cv_vertex1_v=.6,cv_vertex2_v=-.4,cycles=1)
        e=np.r_[np.full(10,.1),np.linspace(-.1,.5,40)]
        z=np.r_[np.linspace(10,45,10),np.full(40,45)]
        d=recording(e,z,p);h=hop_selections(d)
        self.assertEqual(extract_cv_cycles(d),[])
        recovered=contact_points(d,[],h)
        self.assertEqual(len(recovered),1)
        self.assertEqual(recovered[0]['value'],45.)
        self.assertIn('full cycle not required',recovered[0]['source'])
        d.metadata['scan_grid']['pixels'][0]['contact_detected']=False
        self.assertEqual(contact_points(d,[],h),[])

    def test_no_fallback_for_moving_z_or_constant_potential(self):
        p=dict(cv_start_v=-.2,cv_vertex1_v=.6,cv_vertex2_v=-.4,cycles=1)
        for e,z in ((np.linspace(-.2,.6,40),np.linspace(10,30,40)),
                    (np.full(40,.1),np.full(40,45)),
                    (np.r_[np.full(20,-.2),np.full(20,.6)],np.full(40,45))):
            d=recording(e,z,p)
            self.assertIsNone(stationary_sweep_rows(d,hop_selections(d)[0]))

    def test_distinct_stationary_heights_are_ambiguous(self):
        e=np.r_[np.linspace(-.2,.6,40),np.linspace(.6,-.4,40)]
        z=np.r_[np.full(40,45.),np.full(40,55.)]
        d=recording(e,z,dict(cv_start_v=-.2,cv_vertex1_v=.6,cv_vertex2_v=-.4,cycles=1))
        self.assertIsNone(stationary_sweep_rows(d,hop_selections(d)[0]))

    def test_host_timed_pulse_overruns_and_combinatorial_recipe(self):
        from echemtips.analysis_frames import it_surface_rows
        p=dict(initial_potential_v=0.,step_potential_v=.4,return_potential_v=-.1,
               initial_hold_s=1.,step_hold_s=1.,return_hold_s=.25,cycles=1)
        e=np.r_[np.full(104,0.),np.full(104,.4),np.full(30,-.1)]
        d=recording(e,np.full(len(e),42.),p);h=hop_selections(d)[0]
        self.assertIsNone(it_surface_rows(d,h))
        self.assertIsNotNone(stationary_pulse_rows(d,h))
        d.metadata['parameters']={**p,'initial_potential_v':-.1,'recipes':[dict(p,name='A')],
                                  'recipe_assignment':[0], 'x_points':1,'y_points':1}
        d.metadata['scan_grid']['pixels'][0].update(condition_id=0,measurement_program=p)
        points=contact_points(d,[],[h])
        self.assertEqual(points[0]['value'],42.)
        self.assertIn('actual transition',points[0]['source'])

    def test_pulse_requires_distinct_levels_and_stationary_z(self):
        p=dict(initial_potential_v=0.,step_potential_v=.4,initial_hold_s=1.,step_hold_s=1.)
        d=recording(np.r_[np.zeros(100),np.full(100,.4)],np.linspace(0,10,200),p)
        self.assertIsNone(stationary_pulse_rows(d,hop_selections(d)[0]))
        d.metadata['parameters']['initial_potential_v']=.4
        self.assertIsNone(stationary_pulse_rows(d,hop_selections(d)[0]))

    def test_explanation_and_corner_edges_with_crop(self):
        d,groups=fixture();panel=MapPanel();panel.set_dataset(d,groups,groups)
        panel.statistic.setCurrentText('Contact Z (surface estimate)');panel.relative_xy.setChecked(True)
        editor=FigureExportDialog(panel.map);fig=render_figure(editor.data,editor.options())
        self.assertEqual(tuple(fig.axes[0].get_xlim()),(0.,10.))
        self.assertEqual(tuple(fig.axes[0].get_ylim()),(0.,10.))
        panel.crop_bounds=(15.,15.,20.,25.);panel.refresh()
        data=snapshot(panel.map);fig=render_figure(data,editor.options())
        self.assertEqual(tuple(fig.axes[0].get_xlim()),(0.,5.))
        self.assertEqual(data.context['xy_origin_um'],[12.5,17.5])
        reason=contact_failure_reason(d,[],groups)
        self.assertIn('“complete”',reason)
        self.assertIn('waveform',reason)
        panel.close();editor.close()

    def test_movie_corner_is_lower_left_not_first_landing(self):
        d,_=fixture();panel=MoviePanel();panel.dataset=d
        panel.frames=MapFrames(np.array([0.,.2]),np.array([[1.,2.],[3.,4.]]),[0,1],
            {0:(15.,20.),1:(10.,20.)},dict(kind='CV potential',leg=0,cycle=1,channel='current1_na',
                                         polarity='IUPAC',waveform_v=[-.2,.6,-.4],cell_spacing_um=(5.,5.)))
        panel.frames.auto_limits=(1.,4.)
        panel.relative_xy.setChecked(True)
        self.assertEqual(panel.map.x_values,[2.5,7.5]);self.assertEqual(panel.map.y_values,[2.5])
        self.assertEqual(panel.map.export_context['xy_origin_um'],[7.5,17.5])
        panel.relative_xy.setChecked(False)
        self.assertEqual(panel.map.x_values,[10.,15.])
        panel.shutdown();panel.close()
