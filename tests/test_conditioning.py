"""Optional hold timing, phase provenance, extraction and compact UI contracts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest
import numpy as np
from echemtips.models import (AppSettings, ApproachCVParameters, ApproachITParameters,
                             ScanHoppingCVParameters, ScanHoppingITParameters)
from echemtips.experiments import (ApproachCVExperiment, ApproachITExperiment,
    ScanHoppingCVExperiment, ScanHoppingITExperiment, ExperimentState)
from echemtips.backends import SimulationBackend, NIFPGABackend, BackendError
from echemtips.conditioning import PHASES
from echemtips.waypoints import approach_cv_followup_plan, potential_step_plan
from echemtips.analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles
from echemtips.analysis_frames import it_surface_rows
from echemtips.analysis_tools import hop_selections
from echemtips.data import DataRecorder

HOLDS=dict(pre_hold_enabled=True,pre_hold_v=.15,pre_hold_s=.12,
           post_hold_enabled=True,post_hold_v=.12,post_hold_s=.16)


def run_sim(cls,params):
    """Run actual experiment code against deterministic acquisition time."""
    clock=[1000.]; samples=[]
    with patch('time.monotonic',side_effect=lambda:clock[0]):
        b=SimulationBackend(AppSettings()); b.connect(); e=cls(b,b.settings); e.start(params)
        for _ in range(60000):
            clock[0]+=.01; s=b.read_sample()
            if cls is ApproachCVExperiment: e.tick(s)
            else: e.tick_samples([s])
            samples.append(s)
            if not e.active: break
    return e,samples


class ConditioningTests(unittest.TestCase):
    def test_disabled_compatibility_and_validation(self):
        for model in (ApproachCVParameters,ApproachITParameters,ScanHoppingCVParameters,ScanHoppingITParameters):
            p=model()
            self.assertEqual(p.conditioning_duration(),0)
            self.assertFalse(p.validate(AppSettings()))
            for changes in ({'pre_hold_s':0},{'pre_hold_s':float('nan')},{'pre_hold_v':float('inf')}):
                bad=replace(p,**(dict(pre_hold_enabled=True)|changes))
                self.assertTrue(bad.validate(AppSettings()))
            self.assertTrue(replace(p,pre_hold_enabled=True,pre_hold_v=3).validate(AppSettings(mode='NI FPGA',command_voltage_ratio=5)))

    def test_fpga_series_bracketed_once_and_exact_duration(self):
        p=ApproachCVParameters(**HOLDS,scan_rates_v_s=[.5,1.],cycles=2)
        plan,contexts=approach_cv_followup_plan(p)
        self.assertEqual(contexts[-1],'retract')
        for phase,duration in [('pre_hold',p.pre_hold_s),('post_hold',p.post_hold_s)]:
            selected=[w for w,c in zip(plan,contexts) if c==phase]
            self.assertEqual(sum(w.hold_us for w in selected),round(duration*1e6))
        self.assertLess(contexts.index('pre_hold'),contexts.index('cv:0'))
        self.assertGreater(contexts.index('post_hold'),max(i for i,c in enumerate(contexts) if c=='cv:1'))
        self.assertTrue(all(w.hold_us<=32767 for w in plan))

    def test_scan_time_and_marker_and_recipes(self):
        for model in (ScanHoppingCVParameters,ScanHoppingITParameters):
            base=model(x_points=2,y_points=2,marker_enabled=True)
            p=replace(base,**HOLDS)
            self.assertAlmostEqual(p.estimated_known_duration_s()-base.estimated_known_duration_s(),5*.28)
            from echemtips.combinatorial import program_fields
            recipe={k:getattr(p,k) for k in program_fields(p)}|{'name':'A'}
            p.recipes=[recipe]; p.recipe_assignment=[0]*4
            self.assertFalse(p.validate(AppSettings()))
            self.assertTrue(p.for_point(4).post_hold_enabled)
            self.assertAlmostEqual(p.estimated_known_duration_s()-base.estimated_known_duration_s(),5*.28)

    def test_simulation_phases_and_analysis(self):
        pairs=[(ApproachCVExperiment,ApproachCVParameters), (ApproachITExperiment,ApproachITParameters),
               (ScanHoppingCVExperiment,ScanHoppingCVParameters),(ScanHoppingITExperiment,ScanHoppingITParameters)]
        for cls,model in pairs:
            with self.subTest(model=model.__name__):
                options=dict(HOLDS)
                if cls in (ScanHoppingCVExperiment,ScanHoppingITExperiment):
                    options.update(x_points=2,y_points=1,marker_enabled=False,retract_rate_um_s=5.)
                p=model(**options)
                e,samples=run_sim(cls,p)
                self.assertEqual(e.state,ExperimentState.COMPLETE,e.detail)
                self.assertEqual({s.measurement_phase for s in samples},{0,1,2,3})
                for phase,value in [(1,.15),(3,.12)]:
                    self.assertTrue(all(abs(s.voltage1_v-value)<1e-9 for s in samples if s.measurement_phase==phase))
                self.assertAlmostEqual(samples[-1].voltage1_v,.12)
                with TemporaryDirectory() as folder:
                    recorder=DataRecorder(); recorder.start('test',AppSettings(save_directory=folder),p)
                    for s in samples: recorder.append(s)
                    path=recorder.finish(); data=AnalysisDataset.load(path)
                    self.assertEqual(data.metadata['measurement_phases'],{str(k):v for k,v in PHASES.items()})
                    if hasattr(p,'cv_start_v'):
                        cycles=extract_cv_cycles(data)
                        self.assertEqual(len(cycles),p.cycles*(p.point_count if hasattr(p,'point_count') else 1))
                        self.assertTrue(all(np.all(c.rows.matrix[:,c.rows.columns.index('measurement_phase')]==2) for c in cycles))
                    elif 'scan_pixel' in data.columns:
                        for group in hop_selections(data):
                            result=it_surface_rows(data,group)
                            self.assertIsNotNone(result)
                            self.assertTrue(np.all(result[0].matrix[:,data.columns.index('measurement_phase')]==2))

    def test_disabled_csv_has_no_phase_column(self):
        self.assertNotIn('measurement_phase',DataRecorder._fields_for_parameters(ScanHoppingCVParameters()))

    def test_pause_and_abort_during_hold(self):
        clock=[1000.]
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            b=SimulationBackend(AppSettings()); b.connect()
            e=ApproachCVExperiment(b,b.settings); p=ApproachCVParameters(**HOLDS)
            e.start(p)
            for _ in range(10000):
                clock[0]+=.01; s=b.read_sample(); e.tick(s)
                if s.measurement_phase==1: break
            self.assertEqual(s.measurement_phase,1)
            b.pause(); clock[0]+=10; e.tick(b.read_sample())
            self.assertIsNotNone(e._conditioning_pending)
            b.resume(); e.abort(); self.assertFalse(e.active)
            e.start(replace(p,pre_hold_enabled=False,post_hold_enabled=False))
            self.assertIsNone(e._conditioning_pending)

    def test_simulated_lsv_rate_series_holds_once(self):
        p=ApproachCVParameters(**HOLDS,waveform='LSV',cycles=1,scan_rates_v_s=[1.,2.],reset_settling_s=.05)
        e,samples=run_sim(ApproachCVExperiment,p)
        self.assertEqual(e.state,ExperimentState.COMPLETE)
        phases=[s.measurement_phase for s in samples]
        runs=[v for i,v in enumerate(phases) if i==0 or v!=phases[i-1]]
        self.assertEqual(runs.count(1),1); self.assertEqual(runs.count(3),1)
        self.assertEqual({s.cv_rate_index for s in samples if s.measurement_phase==2},{0,1})

    def test_site_driver_must_opt_in(self):
        b=NIFPGABackend(AppSettings()); b._driver=SimpleNamespace()
        with self.assertRaisesRegex(BackendError,'pre/post holds'):
            b.start_hardware_approach_cv(ApproachCVParameters(**HOLDS))

    def test_no_contact_never_conditions(self):
        for cls,model in ((ApproachCVExperiment,ApproachCVParameters),
                          (ScanHoppingCVExperiment,ScanHoppingCVParameters)):
            options=dict(HOLDS,feedback_threshold_na=1000.)
            if model is ScanHoppingCVParameters:
                options.update(x_points=1,y_points=1,marker_enabled=False)
            e,samples=run_sim(cls,model(**options))
            self.assertFalse(e.active)
            self.assertFalse(any(s.measurement_phase in (1,2,3) for s in samples))

    def test_hardware_phase_is_sample_context_not_latest_status(self):
        from tests.test_ni_protocol import NativeDriverTests
        from echemtips.ni_protocol import SAMPLE_WORDS
        f=NativeDriverTests(); f.setUp(); d=f.driver
        d._owner='scan-hopping-cv'
        d.data_fifo.data.extend([0]*(4*SAMPLE_WORDS))
        with patch.object(d,'_observe_contacts'), patch.object(d,'scan_context',side_effect=[
                (0,'pre_hold'),(0,'cv'),(0,'post_hold'),(0,'retract')]):
            self.assertEqual([s.measurement_phase for s in d._read_complete_sample_snapshot()],[1,2,3,0])

    def test_fpga_scan_and_it_contexts(self):
        from tests.test_ni_protocol import NativeDriverTests
        for kind in ('cv','it'):
            f=NativeDriverTests(); f.setUp(); d=f.driver
            p=(ScanHoppingCVParameters if kind=='cv' else ScanHoppingITParameters)(**HOLDS,x_points=1,y_points=1,marker_enabled=False)
            if kind=='cv':
                d.start_scan_hopping_cv(p)
                with patch.object(d,'_enqueue'): d._submit_scan_cv(0)
                descriptors=d._scan_sequence.descriptors
            else:
                d.start_method('scan_hopping_it',p)
                with patch.object(d,'_enqueue'): d._submit_method_it(p,0)
                descriptors=d._method_sequence.descriptors
            contexts=[c.removeprefix('it:') for _,c in descriptors]
            self.assertIn('pre_hold',contexts); self.assertIn('post_hold',contexts)
            self.assertEqual(contexts[-1],'retract')

    def test_compact_ui_and_preset_round_trip(self):
        from echemtips.ui import create_application,EChemTipsApp
        from echemtips.workspace_layout import form_values,restore_form
        app=create_application([]); window=EChemTipsApp(); window.poll_timer.stop()
        try:
            for name in ('Approach + CV','Approach + CV scan-rate series','Approach + I-t','Scan hopping + CV','Scan hopping + I-t'):
                page=window.pages[name]
                self.assertIn('Off',page.conditioning.text())
                self.assertTrue(page.conditioning.change.isHidden())
                page.conditioning.set_values(HOLDS)
                saved=form_values(page)
                page.conditioning.set_values({}); restore_form(page,saved)
                self.assertTrue(page.parameters().pre_hold_enabled)
                self.assertEqual(page.parameters().post_hold_v,.12)
        finally: window.close()
