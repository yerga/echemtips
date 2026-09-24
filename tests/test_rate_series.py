"""Rate-series waveform, hardware gating, recording and analysis regressions."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from echemtips.models import AppSettings, ApproachCVParameters, Sample
from echemtips.backends import SimulationBackend, NIFPGABackend, BackendError
from echemtips.experiments import ApproachCVExperiment, ExperimentState
from echemtips.waypoints import approach_cv_followup_plan
from echemtips.data import DataRecorder
from echemtips.analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles
from tests import test_ni_protocol as fixtures


class RateSeriesTests(unittest.TestCase):
    def test_legacy_site_driver_cannot_silently_ignore_series(self):
        backend = NIFPGABackend(AppSettings())
        backend._driver = SimpleNamespace(
            start_approach_cv=lambda p: self.fail("Must not submit motion"),
            approach_cv_status=lambda: {}, stop_motion=lambda: None,
        )
        with self.assertRaisesRegex(BackendError, "does not support CV scan-rate series"):
            backend.start_hardware_approach_cv(ApproachCVParameters(scan_rates_v_s=[.1, .5]))

    def test_rates_validate_and_keep_order_and_repeats(self):
        p = ApproachCVParameters(scan_rates_v_s=[.1, .5, .1], cycles=2)
        self.assertEqual(p.cv_rates, (.1, .5, .1))
        self.assertEqual(p.total_cv_cycles, 6)
        self.assertFalse(p.validate(AppSettings()))
        for rates in ([], [0], [-1], [float('nan')], [float('inf')], [1] * 1001):
            p.scan_rates_v_s = rates
            self.assertTrue(p.validate(AppSettings()))

    def test_plan_has_one_initial_jump_and_one_final_retract(self):
        p = ApproachCVParameters(scan_rates_v_s=[.1, .5, .1], cycles=2)
        plan, contexts = approach_cv_followup_plan(p)
        self.assertEqual(len(plan), 20)
        self.assertEqual(sum(bool(w.jump_voltage1) for w in plan), 1)
        self.assertEqual([w.z_um for w in plan if w.z_um is not None], [p.start_z_um])
        ramps = [w for w in plan if w.voltage1_rate_v_s is not None]
        self.assertEqual([w.voltage1_rate_v_s for w in ramps], [.1]*6 + [.5]*6 + [.1]*6)
        self.assertEqual(contexts, ['cv:0']*7 + ['cv:1']*6 + ['cv:2']*6 + ['retract'])

    def test_hardware_contact_gate_compiled_rates_context_and_completion(self):
        fixture = fixtures.NativeDriverTests(); fixture.setUp()
        d, regs = fixture.driver, fixture.session.registers
        p = ApproachCVParameters(scan_rates_v_s=[.1, .5, .1], cycles=2)
        d.start_approach_cv(p)
        self.assertEqual(len(d._program_waypoints), 2)
        regs['LineNumber'].value = d._program_baseline + d._program_total
        regs['WaitingForWayPoints'].value = False
        d.service()
        regs['WaitingForWayPoints'].value = True
        d.read_samples()
        self.assertEqual(d.approach_cv_status()['stage'], 'contact')
        self.assertEqual(len(d._program_waypoints), 20)
        self.assertGreater(d._program_waypoints[7].v_velocity, d._program_waypoints[1].v_velocity)
        for offset, context in ((1, 'cv:0'), (8, 'cv:1'), (14, 'cv:2'), (20, 'retract')):
            self.assertEqual(d.approach_context(d._program_baseline + offset), context)
        regs['LineNumber'].value = d._program_baseline + d._program_total
        regs['WaitingForWayPoints'].value = True
        d.read_samples()
        self.assertEqual(d.approach_cv_status()['stage'], 'complete')
        d.ensure_idle()

    def test_unrepresentable_series_fails_before_approach_submission(self):
        fixture = fixtures.NativeDriverTests(); fixture.setUp()
        d = fixture.driver
        with patch.object(d.compiler, 'compile', side_effect=ValueError('Unrepresentable velocity')):
            before = len(d.positions_fifo.writes)
            with self.assertRaises(ValueError):
                d.start_approach_cv(ApproachCVParameters(scan_rates_v_s=[1, 100]))
            self.assertEqual(len(d.positions_fifo.writes), before)

    def test_simulated_series_one_contact_all_rates_and_final_retract(self):
        settings = AppSettings(polarity_convention='Instrument-native')
        backend = SimulationBackend(settings); backend.connect()
        e = ApproachCVExperiment(backend, settings)
        p = ApproachCVParameters(scan_rates_v_s=[2, 4, 2], cycles=2, feedback_threshold_na=2)
        e.start(p)
        e.tick(Sample(0, 50, 50, 10, .1, 0, 0, 0))
        self.assertEqual(e.state, ExperimentState.APPROACHING)
        e.tick(Sample(.01, 50, 50, 68, .1, 0, 3, 0))
        self.assertEqual(e.state, ExperimentState.CV)
        seen = set()
        with patch.object(backend, 'move', wraps=backend.move) as moves:
            for index in range(4000):
                e._last_tick -= .01
                sample = Sample(index*.01, 50, 50, 68, e._cv_voltage, 0, 1, 0)
                e.tick(sample)
                seen.add(sample.cv_rate_index)
                if e.state == ExperimentState.RETRACTING:
                    break
            self.assertEqual(e.state, ExperimentState.RETRACTING)
            self.assertEqual(seen, {0, 1, 2})
            self.assertEqual(moves.call_count, 1)
            self.assertEqual(moves.call_args.args[:2], ('Z', p.start_z_um))
        e.tick(Sample(100, 50, 50, p.start_z_um, p.cv_start_v, 0, 0, 0))
        self.assertEqual(e.state, ExperimentState.COMPLETE)
        backend.disconnect()

    def test_analysis_groups_multiple_cycles_by_rate_even_with_missing_block(self):
        p = ApproachCVParameters(scan_rates_v_s=[.1, .5, .1], cycles=2)
        columns = ('elapsed_s', 'voltage1_v', 'current1_na', 'cv_rate_index')
        rows = []
        # Block 1 intentionally absent: rate identity must never be inferred by list order.
        for rate_index in (0, 2):
            for _ in range(2):
                for start, end in ((-.2, .6), (.6, -.4), (-.4, -.2)):
                    for value in np.linspace(start, end, 81):
                        rows.append([len(rows)*.01, value, value*2, rate_index])
        dataset = AnalysisDataset(Path('series.csv'), columns, NumericRows(columns, rows), {'parameters': asdict(p)})
        cycles = extract_cv_cycles(dataset)
        self.assertEqual([(c.rate_index, c.number, c.scan_rate_v_s) for c in cycles],
                         [(0, 1, .1), (0, 2, .1), (2, 1, .1), (2, 2, .1)])
        self.assertIn('Rate 3', cycles[-1].label)

    def test_only_series_recordings_include_rate_identifier(self):
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            p = ApproachCVParameters(scan_rates_v_s=[.1, .5])
            recorder = DataRecorder(); recorder.start('Approach + CV scan-rate series', settings, p)
            recorder.append(Sample(0, 0, 0, 10, -.2, 0, 1, 0, cv_rate_index=1))
            path = recorder.finish(settings, p, status='aborted')
            self.assertIn('cv_rate_index', path.read_text().splitlines()[0])
            self.assertEqual(json.loads(path.with_suffix('.json').read_text())['parameters']['scan_rates_v_s'], [.1, .5])
            self.assertNotIn('cv_rate_index', DataRecorder._fields_for_parameters(ApproachCVParameters()))


if __name__ == '__main__':
    unittest.main()
