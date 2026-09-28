"""Cycle/branch objectives and complete CV/I–t adaptive simulation runs."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from echemtips.adaptive import AdaptiveParameters, AdaptiveITParameters, AdaptiveExperiment
from echemtips.adaptive_waveforms import score_cv, score_it
from echemtips.backends import SimulationBackend
from echemtips.experiments import ExperimentState
from echemtips.models import AppSettings
from echemtips.data import DataRecorder


class WaveformTests(unittest.TestCase):
    def test_native_it_commanded_contact_snapshot(self):
        from tests.test_ni_protocol import NativeDriverTests
        from echemtips.models import ApproachITParameters
        from echemtips.ni_protocol import raw_to_position
        f=NativeDriverTests(); f.setUp()
        d=f.driver
        d.start_method('approach_it',ApproachITParameters())
        self.assertIsNone(d.method_contact_z_um)
        with patch.object(d,'_submit_method_plan'):
            d._method_finish_approach(True)
        expected=raw_to_position(f.session.registers['Applied Z'].value,d.settings.z_range_um,d.settings.z_bipolar)
        self.assertEqual(d.method_contact_z_um,expected)
        self.assertTrue(d.supports_method_contact_snapshot)

    def test_cv_cycle_and_chronological_segment(self):
        p=AdaptiveParameters(waveform='CV',cycles=2,objective_cycle=2,objective_segment=1)
        e=[]; i=[]
        for cycle in range(2):
            for leg,(a,b) in enumerate([(-.2,.6),(.6,-.4),(-.4,-.2)]):
                e.extend(np.linspace(a,b,401)); i.extend([-(cycle*3+leg+1)*.01]*401)
        t=np.arange(len(e))*.005
        result=score_cv(t,e,i,p,1)
        self.assertTrue(result['valid'],result)
        self.assertAlmostEqual(result['objective_na'],.05)
        self.assertFalse(score_cv(t[:-800],e[:-800],i[:-800],p,1)['valid'])

    def test_it_selected_cycle_and_window(self):
        p=AdaptiveITParameters(cycles=2,objective_cycle=2)
        t=[]; e=[]; i=[]; tags=[]; start=0
        for n,(v,d,label) in enumerate(p.it_steps()):
            part=np.arange(0,d,.005)
            t.extend(start+part); e.extend([v]*len(part)); i.extend([-.01*(n+1)]*len(part)); tags.extend(['it:'+label]*len(part)); start+=d
        result=score_it(t,e,i,tags,p,1)
        self.assertTrue(result['valid'],result); self.assertAlmostEqual(result['objective_na'],.05)
        self.assertFalse(score_it(t[:-100],e[:-100],i[:-100],tags[:-100],p,1)['valid'])
        self.assertTrue(replace(p,objective_start_s=.2,objective_end_s=.4).validate(AppSettings()))

    def test_complete_simulated_waveforms_and_recordings(self):
        for p in [AdaptiveParameters(waveform='CV',cycles=2,objective_cycle=2,objective_segment=1,objective_window_v=.04),
                  AdaptiveITParameters(cycles=2,objective_cycle=2)]:
            with self.subTest(kind=type(p).__name__), tempfile.TemporaryDirectory() as folder:
                clock=[1000.]
                with patch('time.monotonic',side_effect=lambda:clock[0]):
                    settings=AppSettings(save_directory=folder,z_range_um=200)
                    b=SimulationBackend(settings); b.connect()
                    e=AdaptiveExperiment(b,settings)
                    p=replace(p,region_confirmed=True,approve_each=False,max_landings=7,approach_rate_um_s=30,xy_speed_um_s=100)
                    recorder=DataRecorder(); recorder.start('Adaptive hopping',settings,p)
                    e.configure_recording(recorder.output_path); e.start(p)
                    for _ in range(60000):
                        clock[0]+=.01
                        sample=b.read_sample(); e.tick_samples([sample]); recorder.append(sample)
                        if e.phase=='tilt_approval': e.approve()
                        if e.phase=='model': time.sleep(.001)
                        if not e.active: break
                    self.assertEqual(e.state,ExperimentState.COMPLETE,e.detail)
                    self.assertEqual(len(e.params.attempts),7)
                    self.assertTrue(all(a['valid'] for a in e.params.attempts))
                    self.assertAlmostEqual(b.commanded_position()['Z'],p.start_z_um,delta=.08)
                    path=recorder.finish(settings,e.params); e.finish_report(path,'complete')
                    from echemtips.analysis_core import AnalysisDataset,extract_cv_cycles,pixel_groups
                    dataset=AnalysisDataset.load(path)
                    self.assertEqual(len(pixel_groups(dataset)),7)
                    if isinstance(p,AdaptiveParameters): self.assertEqual(len(extract_cv_cycles(dataset)),14)
                    self.assertTrue(path.with_suffix('.report.md').exists())
                    e.close()
