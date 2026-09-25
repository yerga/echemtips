"""Adaptive selection, conservative clearance and end-to-end simulated landings."""
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import time

import numpy as np

from echemtips.adaptive import AdaptiveParameters, AdaptiveExperiment, TiltEnvelope, propose, score_lsv
from echemtips.backends import SimulationBackend
from echemtips.models import AppSettings
from echemtips.experiments import ExperimentState
from echemtips.data import DataRecorder


class AdaptiveTests(unittest.TestCase):
    def test_validation(self):
        p = AdaptiveParameters(region_confirmed=True)
        self.assertEqual(p.validate(AppSettings()),[])
        for changes in ({'region_confirmed':False},{'minimum_spacing_um':50},{'max_landings':4},
                        {'x_max_um':101},{'objective_window_v':2},{'waveform':'CV'}, {'clearance_um':0}):
            self.assertTrue(replace(p,**changes).validate(AppSettings()))

    def test_plane_path_and_no_clamping(self):
        p = AdaptiveParameters(region_confirmed=True)
        attempts = [{'xy':xy.tolist(),'contact_z_um':60+.1*xy[0]-.2*xy[1]} for xy in p.survey_points()]
        plane = TiltEnvelope.fit(attempts,1)
        self.assertAlmostEqual(plane.height([50,50]),55)
        z = plane.travel_z([20,80],[80,20],p)
        self.assertAlmostEqual(z,46-p.clearance_um-p.relief_allowance_um-p.plane_tolerance_um)
        with self.assertRaises(ValueError): plane.travel_z([20,80],[80,20],replace(p,clearance_um=100))
        attempts[-1]['contact_z_um'] += 20
        with self.assertRaises(ValueError): TiltEnvelope.fit(attempts,1)

    def test_signed_window_then_magnitude(self):
        p = AdaptiveParameters()
        e = np.linspace(-.2,.6,801); i = np.full(801,-.004)
        i[399]=8
        scored = score_lsv(e,i,p,1)
        self.assertTrue(scored['valid']); self.assertEqual(scored['objective_na'],.004)
        self.assertEqual(scored['signed_current_na'],-.004)
        self.assertFalse(score_lsv(e[:200],i[:200],p,1)['valid'])
        self.assertFalse(score_lsv(e,np.full(801,10),p,1)['valid'])

    def test_proposals_exclude_failed_landings(self):
        p = AdaptiveParameters(region_confirmed=True)
        attempts = [{'xy':xy.tolist(),'valid':True,'objective_na':float(n+1)} for n,xy in enumerate(p.survey_points())]
        attempts.append({'xy':[65,65],'valid':False})
        for strategy in ('Mapping','Hotspots','Balanced'):
            result = propose(replace(p,strategy=strategy),attempts)
            self.assertTrue(all(np.linalg.norm(np.array(result['xy'])-a['xy'])>=p.minimum_spacing_um for a in attempts))
            self.assertTrue(np.isfinite(result['mean_na']).all())

    def test_complete_simulated_run_and_audit(self):
        settings = AppSettings()
        backend = SimulationBackend(settings); backend.connect()
        experiment = AdaptiveExperiment(backend,settings)
        params = AdaptiveParameters(region_confirmed=True,approve_each=False,max_landings=7,
                                    approach_rate_um_s=60,xy_speed_um_s=100,settling_time_s=0,
                                    cv_scan_rate_v_s=.5,objective_window_v=.04)
        with tempfile.TemporaryDirectory() as folder:
            settings.save_directory=folder
            recorder=DataRecorder()
            recorder.start('Adaptive hopping + LSV',settings,params)
            experiment.configure_recording(recorder.output_path)
            experiment.start(params)
            for _ in range(20000):
                # Advance physical simulation and its LSV clock without sleeping.
                backend._last_tick -= .01
                sample = backend.read_sample()
                if experiment.child: experiment.child._last_tick -= .01
                experiment.tick_samples([sample])
                recorder.append(sample)
                if experiment.phase == 'tilt_approval': experiment.approve()
                if experiment.phase == 'model': time.sleep(.002)
                if not experiment.active: break
            self.assertEqual(experiment.state,ExperimentState.COMPLETE,experiment.detail)
            self.assertEqual(len(experiment.params.attempts),7,experiment.detail)
            self.assertTrue(all(a['valid'] for a in experiment.params.attempts))
            self.assertAlmostEqual(backend.commanded_position()['Z'],params.start_z_um,delta=.08)
            csv_path=recorder.finish(settings,experiment.params)
            experiment.finish_report(csv_path,'complete')
            self.assertTrue(csv_path.with_suffix('.report.md').exists())
            self.assertIn('tilt_approved',csv_path.with_suffix('.decisions.jsonl').read_text())
            from echemtips.analysis_core import AnalysisDataset,extract_cv_cycles
            dataset=AnalysisDataset.load(csv_path)
            self.assertEqual(len(dataset.metadata['scan_grid']['pixels']),7)
            self.assertEqual(len(extract_cv_cycles(dataset)),7)
            self.assertIn('scan_pixel',DataRecorder._fields_for_parameters(params))
        experiment.close()

    def test_operator_pause_and_abort_never_launch(self):
        backend = SimulationBackend(AppSettings()); backend.connect()
        e = AdaptiveExperiment(backend,backend.settings)
        with tempfile.TemporaryDirectory() as folder:
            e.configure_recording(Path(folder)/'run.csv')
            e.start(AdaptiveParameters(region_confirmed=True,approve_each=False))
            backend.pause()
            e.tick_samples([backend.read_sample()])
            self.assertEqual(len(e.params.attempts),0)
            backend.resume(); e.request_abort()
            e.tick_samples([backend.read_sample()])
            self.assertEqual(len(e.params.attempts),0)
        e.close()

    def test_native_contact_snapshot(self):
        from tests.test_ni_protocol import NativeDriverTests
        from echemtips.models import ApproachCVParameters
        from echemtips.ni_protocol import raw_to_position
        f = NativeDriverTests(); f.setUp()
        d,regs = f.driver,f.session.registers
        d.start_approach_cv(ApproachCVParameters(waveform='LSV',cycles=1))
        self.assertIsNone(d.approach_contact_z_um)
        regs['LineNumber'].value = d._program_baseline+d._program_total
        regs['WaitingForWayPoints'].value = False; d.service()
        regs['WaitingForWayPoints'].value = True; d.read_samples()
        d.approach_cv_status()
        self.assertEqual(d.approach_contact_z_um,raw_to_position(regs['Applied Z'].value,d.settings.z_range_um,d.settings.z_bipolar))

    def test_budget_prevents_first_movement(self):
        backend=SimulationBackend(AppSettings()); backend.connect()
        e=AdaptiveExperiment(backend,backend.settings)
        with tempfile.TemporaryDirectory() as folder:
            e.configure_recording(Path(folder)/'run.csv')
            e.start(AdaptiveParameters(region_confirmed=True,approve_each=False,max_duration_s=.001))
            e._started-=1
            e.tick_samples([backend.read_sample()])
            self.assertEqual(e.phase,'return')
            self.assertEqual(e.params.attempts,[])
            self.assertEqual(backend._targets['X'],backend._positions['X'])
        e.close()

    def test_failed_contact_stops_without_training(self):
        backend=SimulationBackend(AppSettings()); backend.connect()
        backend.adaptive_failure_pixel=0
        e=AdaptiveExperiment(backend,backend.settings)
        with tempfile.TemporaryDirectory() as folder:
            e.configure_recording(Path(folder)/'run.csv')
            e.start(AdaptiveParameters(region_confirmed=True,approve_each=False,approach_rate_um_s=60,xy_speed_um_s=100))
            for _ in range(5000):
                backend._last_tick-=.05
                sample=backend.read_sample()
                if e.child: e.child._last_tick-=.05
                e.tick_samples([sample])
                if not e.active: break
            self.assertEqual(e.state,ExperimentState.ABORTED,e.detail)
            self.assertEqual(len(e.params.attempts),1)
            self.assertFalse(e.params.attempts[0]['valid'])
            self.assertIsNone(e.model)
        e.close()

    def test_native_motion_requires_completion_even_when_ao_matches(self):
        from echemtips.host import ExecutionSnapshot,ExecutionState
        class HardwareFixture(SimulationBackend):
            hardware_approach_cv_required=True
            adaptive_contact_available=True
            def execution_status(self):
                return ExecutionSnapshot('move',ExecutionState.RUNNING,1,0,0,1)
        backend=HardwareFixture(AppSettings()); backend.connect()
        e=AdaptiveExperiment(backend,backend.settings)
        with tempfile.TemporaryDirectory() as folder:
            e.configure_recording(Path(folder)/'run.csv')
            e.start(AdaptiveParameters(region_confirmed=True,approve_each=False))
            e.tick_samples([backend.read_sample()])
            backend._positions['Z']=e.params.start_z_um
            e.tick_samples([backend.read_sample()])
            self.assertEqual(e.phase,'travel_z')
            self.assertEqual(backend._targets['X'],backend._positions['X'])
        e.close()


if __name__ == '__main__': unittest.main()
