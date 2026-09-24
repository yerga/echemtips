"""One-way voltammetry across simulation, native plans and analysis."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
from echemtips.models import AppSettings, CVParameters, ApproachCVParameters, ScanHoppingCVParameters
from echemtips.backends import SimulationBackend
from echemtips.experiments import CVExperiment, ApproachCVExperiment, ScanHoppingCVExperiment, ExperimentState
from echemtips.waypoints import cyclic_voltammetry_plan, approach_cv_followup_plan
from echemtips.analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles
from echemtips.analysis_frames import prepare_frames, leg_labels
from tests import test_ni_protocol as fixtures
from echemtips.models import hold_frame_count


class LSVTests(unittest.TestCase):
    def test_large_reset_hold_is_rejected_before_allocation_or_motion(self):
        fixture = fixtures.NativeDriverTests(); fixture.setUp()
        before = len(fixture.driver.positions_fifo.writes)
        with self.assertRaisesRegex(ValueError, 'line-tag span'):
            fixture.driver.start_approach_cv(ApproachCVParameters(
                waveform='LSV', cycles=1, scan_rates_v_s=[.1, 1], reset_settling_s=3600))
        self.assertEqual(len(fixture.driver.positions_fifo.writes), before)

    def test_simulated_scan_holds_lsv_end_during_retract(self):
        backend = SimulationBackend(AppSettings()); backend.connect()
        experiment = ScanHoppingCVExperiment(backend, backend.settings)
        params = ScanHoppingCVParameters(waveform='LSV', cycles=1, marker_enabled=False)
        experiment.start(params)
        backend._positions['Z'] = backend._targets['Z'] = 68
        backend._speeds['Z'] = 0
        experiment.contact_z[experiment._point_key()] = 68
        experiment._begin_simulated_cv()
        values = []
        for _ in range(200):
            sample = backend.read_sample(); values.append(sample.voltage1_v)
            experiment._last_tick -= .05
            experiment.tick_samples([sample])
            if experiment.state == ExperimentState.RETRACTING: break
        self.assertEqual(experiment.state, ExperimentState.RETRACTING)
        self.assertAlmostEqual(values[-1], params.cv_vertex1_v)
        self.assertAlmostEqual(backend.read_sample().voltage1_v, params.cv_vertex1_v)
        self.assertTrue(np.all(np.diff(values) >= -1e-9))
        backend.disconnect()

    def test_models_and_one_way_plan(self):
        for params in (CVParameters(cycles=1, waveform='LSV'),
                       ApproachCVParameters(cycles=1, waveform='LSV'),
                       ScanHoppingCVParameters(cycles=1, waveform='LSV')):
            self.assertFalse(params.validate(AppSettings()))
            params.cycles = 2
            self.assertTrue(params.validate(AppSettings()))
        for start, end in ((-.2, .6), (.6, -.2)):
            plan = cyclic_voltammetry_plan(start_v=start, vertex1_v=end, vertex2_v=-.4,
                                           scan_rate_v_s=1, cycles=1, waveform='LSV')
            self.assertEqual([w.voltage1_v for w in plan], [start, end])

    def test_series_resets_are_not_cv_and_retract_is_last(self):
        params = ApproachCVParameters(cycles=1, waveform='LSV', scan_rates_v_s=[.1, .5, 1], reset_settling_s=.2)
        plan, contexts = approach_cv_followup_plan(params)
        ramps = [w for w in plan if w.voltage1_rate_v_s is not None]
        self.assertEqual([w.voltage1_v for w in ramps], [.6]*3)
        self.assertEqual([w.voltage1_rate_v_s for w in ramps], [.1, .5, 1])
        self.assertEqual(contexts.count('reset'), 2 * (1 + hold_frame_count(.2)))
        self.assertEqual(contexts[-1], 'retract')
        self.assertEqual(plan[-1].z_um, params.start_z_um)

    def test_native_standalone_and_contact_gated_series(self):
        fixture = fixtures.NativeDriverTests(); fixture.setUp()
        fixture.driver.start_method('cv', CVParameters(cycles=1, waveform='LSV'))
        self.assertEqual(len(fixture.driver._program_waypoints), 2)
        fixture = fixtures.NativeDriverTests(); fixture.setUp()
        d, regs = fixture.driver, fixture.session.registers
        params = ApproachCVParameters(cycles=1, waveform='LSV', scan_rates_v_s=[.1, 1], reset_settling_s=.2)
        d.start_approach_cv(params)
        self.assertEqual(len(d._program_waypoints), 2)  # No sweep before contact.
        regs['LineNumber'].value = d._program_baseline + d._program_total
        regs['WaitingForWayPoints'].value = False
        d.service()
        regs['WaitingForWayPoints'].value = True
        d.read_samples()
        self.assertEqual(d.approach_cv_status()['stage'], 'contact')
        expected = approach_cv_followup_plan(params)[1]
        self.assertEqual(d._approach_history[-1][1], expected)
        regs['LineNumber'].value = d._program_baseline + d._program_total
        d.read_samples()
        self.assertEqual(d.approach_cv_status()['stage'], 'complete')

    def test_native_scan_sweep_requires_contact_then_retracts(self):
        fixture = fixtures.NativeDriverTests(); fixture.setUp()
        d, regs = fixture.driver, fixture.session.registers
        d.start_scan_hopping_cv(ScanHoppingCVParameters(waveform='LSV', cycles=1, marker_enabled=False))
        self.assertEqual(len(d._program_waypoints), 3)
        regs['LineNumber'].value = 3
        regs['Feedback1 Boolean'].value = True
        regs['Internal Pause'].value = True
        regs['WaitingForWayPoints'].value = False
        self.assertEqual(d.scan_hopping_cv_status()['stage'], 'contact')
        regs['WaitingForWayPoints'].value = True
        d.read_samples()
        self.assertEqual(d.scan_hopping_cv_status()['stage'], 'cv')
        self.assertEqual(len(d._program_waypoints), 3)  # Jump, one ramp, retract.
        self.assertEqual(d._scan_history[-1].descriptors, [(0,'cv'), (0,'cv'), (0,'retract')])

    def test_simulated_standalone_reaches_end_without_reverse(self):
        backend = SimulationBackend(AppSettings()); backend.connect()
        experiment = CVExperiment(backend, backend.settings)
        for start, end in ((-.2, .6), (.6, -.2)):
            params = CVParameters(start_v=start, vertex1_v=end, cycles=1, waveform='LSV', scan_rate_v_s=1)
            experiment.start(params); values = []
            for _ in range(200):
                sample = backend.read_sample(); values.append(sample.voltage1_v)
                experiment._last_tick -= .05
                experiment.tick_samples([sample])
                if experiment.state == ExperimentState.COMPLETE: break
            self.assertEqual(experiment.state, ExperimentState.COMPLETE)
            self.assertAlmostEqual(values[-1], end)
            self.assertTrue(np.all(np.diff(values)*np.sign(end-start) >= -1e-9))
        backend.disconnect()

    def test_simulated_series_resets_and_extracts_every_rate(self):
        backend = SimulationBackend(AppSettings()); backend.connect()
        experiment = ApproachCVExperiment(backend, backend.settings)
        p = ApproachCVParameters(cycles=1, waveform='LSV', scan_rates_v_s=[.1, .5, 1], retract_after=False)
        experiment.start(p)
        stale = backend.read_sample()
        experiment._begin_cv()
        experiment._last_tick -= .1
        experiment.tick(stale)
        self.assertEqual(backend.read_sample().voltage1_v, p.cv_start_v)
        columns = ('elapsed_s', 'voltage1_v', 'current1_na', 'cv_rate_index'); rows=[]
        for _ in range(2000):
            batch=[backend.read_sample() for _ in range(5)]
            rows.extend([[len(rows)+i, s.voltage1_v, s.current1_na, s.cv_rate_index] for i,s in enumerate(batch)])
            experiment._last_tick -= .08
            experiment.tick(batch[-1])
            if experiment.state == ExperimentState.COMPLETE: break
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        dataset=AnalysisDataset(Path('lsv.csv'), columns, NumericRows(columns, rows),
                                {'parameters':asdict(p), 'acquisition_rate_tags':True})
        cycles=extract_cv_cycles(dataset)
        self.assertEqual([c.rate_index for c in cycles], [0,1,2])
        for c in cycles:
            e=c.rows.matrix[:,1]
            self.assertAlmostEqual(e[0],-.2); self.assertAlmostEqual(e[-1],.6)
            self.assertTrue(np.all(np.diff(e)>=-1e-9))
        self.assertIn(-1, dataset.column('cv_rate_index'))
        backend.disconnect()

    def test_lsv_maps_movies_and_incomplete_sweeps(self):
        for start, end in ((-.2,.6),(.6,-.2)):
            p=CVParameters(start_v=start, vertex1_v=end, waveform='LSV', cycles=1)
            columns=('elapsed_s','voltage1_v','current1_na','scan_pixel')
            rows=NumericRows(columns, [[i*.01,e,2*e,0] for i,e in enumerate(np.linspace(start,end,101))])
            metadata={'parameters':asdict(p),'scan_grid':{'pixels':[{'scan_pixel':0,'x_um':1,'y_um':2}]}}
            d=AnalysisDataset(Path('lsv.csv'),columns,rows,metadata)
            cycles=extract_cv_cycles(d); self.assertEqual(len(cycles),1)
            selections=[SimpleNamespace(rows=cycles[0].rows,pixel=0,cycle=1)]
            self.assertEqual(len(leg_labels(d)),1)
            for kind in ('CV potential','CV time'):
                frames=prepare_frames(d,selections,kind=kind,count=20)
                self.assertEqual(frames.values.shape,(20,1))
                self.assertTrue(np.isfinite(frames.values).all())
            partial=AnalysisDataset(d.path,columns,rows[:50],metadata)
            self.assertEqual(extract_cv_cycles(partial),[])


if __name__ == '__main__': unittest.main()
