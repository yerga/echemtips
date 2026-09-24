"""Orientation marker geometry, execution, recording and analysis contracts."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import xml.etree.ElementTree as ET

from echemtips.models import AppSettings, Sample, ScanHoppingCVParameters, ScanHoppingITParameters
from echemtips.backends import SimulationBackend
from echemtips.experiments import ScanHoppingCVExperiment, ScanHoppingITExperiment, ExperimentState
from echemtips.analysis_core import AnalysisDataset, pixel_groups
from echemtips.data import DataRecorder
from echemtips.scan_orientation import orientation_svg


class MarkerTests(unittest.TestCase):
    def test_geometry_defaults_overrides_limits_and_retraction(self):
        for cls in (ScanHoppingCVParameters, ScanHoppingITParameters):
            p = cls()
            self.assertEqual(p.marker_position(), (35, 80))
            self.assertEqual((p.point_count, p.execution_point_count), (9, 10))
            self.assertEqual(len(p.grid()), 9)
            self.assertEqual(p.execution_grid()[-1], (-1, -1, 35, 80))
            self.assertEqual(p.recorded_pixel(9), -2)
            self.assertEqual(p.recorded_pixel(8), 8)
            self.assertEqual(p.scan_retract_z(8, 70, 100), 55)  # 10 + extra 5 clearance
            self.assertEqual(p.scan_retract_z(9, 75, 100), 55)  # final initial Z
            self.assertFalse(p.validate(AppSettings()))
            p.y_start_um, p.y_end_um = 65, 35
            self.assertEqual(p.marker_position(), (35, 20))
            p.marker_y_um = 110
            self.assertTrue(p.marker_validation(AppSettings()))
            p.marker_y_um = 50
            self.assertTrue(p.marker_validation(AppSettings()))  # inside array
            p.marker_y_um = float('nan')
            self.assertTrue(p.marker_validation(AppSettings()))
            p.marker_enabled = False
            self.assertFalse(p.marker_validation(AppSettings()))
            self.assertEqual(p.execution_grid(), p.grid())

    def test_simulated_cv_and_it_repeat_program_at_marker_then_return(self):
        for params_cls, experiment_cls in ((ScanHoppingCVParameters, ScanHoppingCVExperiment),
                                           (ScanHoppingITParameters, ScanHoppingITExperiment)):
            with self.subTest(method=params_cls.__name__):
                settings = AppSettings(polarity_convention='Instrument-native')
                backend = SimulationBackend(settings, seed=4)
                backend.connect()
                p = params_cls(x_start_um=40, x_end_um=60, x_points=2,
                               y_start_um=40, y_end_um=60, y_points=2,
                               lateral_rate_um_s=100, approach_rate_um_s=20,
                               retract_rate_um_s=100, cycles=2)
                if isinstance(p, ScanHoppingCVParameters):
                    p.cv_scan_rate_v_s = 10
                    p.feedback_threshold_na = 2
                else:
                    p.feedback_threshold = 2
                    p.initial_hold_s = p.step_hold_s = p.return_hold_s = .01
                experiment = experiment_cls(backend, settings)
                experiment.start(p)
                marker_electrochemistry = False
                tags = set()
                for _ in range(1200):
                    backend._last_tick -= .25
                    if isinstance(experiment, ScanHoppingCVExperiment):
                        experiment._last_tick -= .25
                    elif experiment.state == ExperimentState.IT:
                        experiment._step_deadline = 0
                    before = experiment.state
                    sample = backend.read_sample()
                    experiment.tick_samples([sample])
                    tags.add(sample.scan_pixel)
                    if sample.scan_pixel == -2 and before in (ExperimentState.CV, ExperimentState.IT):
                        marker_electrochemistry = True
                        if isinstance(experiment, ScanHoppingCVExperiment):
                            self.assertEqual(experiment._segments, [p.cv_vertex1_v, p.cv_vertex2_v, p.cv_start_v] * 2)
                        else:
                            self.assertEqual(experiment._steps, p.it_steps())
                    if not experiment.active:
                        break
                self.assertEqual(experiment.state, ExperimentState.COMPLETE)
                self.assertEqual(tags, {0, 1, 2, 3, -2})
                self.assertTrue(marker_electrochemistry)
                self.assertEqual(p.marker_result['status'], 'complete')
                self.assertTrue(p.marker_result['contact_detected'])
                self.assertAlmostEqual(experiment._retract_target_z, p.start_z_um)
                backend.disconnect()

    def test_failed_array_approach_and_stop_never_start_marker(self):
        for params_cls, experiment_cls in ((ScanHoppingCVParameters, ScanHoppingCVExperiment),
                                           (ScanHoppingITParameters, ScanHoppingITExperiment)):
            for stop in (False, True):
                settings = AppSettings(polarity_convention='Instrument-native')
                backend = SimulationBackend(settings)
                backend.connect()
                p = params_cls(start_z_um=10, end_z_um=11, x_points=1, y_points=1)
                if isinstance(p, ScanHoppingCVParameters): p.feedback_threshold_na = 9
                else: p.feedback_threshold = 9
                e = experiment_cls(backend, settings)
                e.start(p)
                if stop: e.abort()
                else:
                    e.state = ExperimentState.APPROACHING
                    e.tick_samples([Sample(0, 35, 35, 11, 0, 0, 0, 0)])
                    e.tick_samples([Sample(1, 35, 35, 10, 0, 0, 0, 0)])
                self.assertEqual(e.state, ExperimentState.ABORTED)
                self.assertEqual(p.marker_result, {})
                self.assertEqual(e.point_index, 0)
                backend.disconnect()

    def test_recording_preserves_marker_but_analysis_excludes_it(self):
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            p = ScanHoppingCVParameters()
            recorder = DataRecorder()
            recorder.start('Scan Hopping CV', settings, p)
            recorder.append(Sample(0, 35, 35, 60, 0, 0, 1, 2, scan_pixel=0))
            recorder.append(Sample(1, 35, 80, 65, 0, 0, 10000, 20000, scan_pixel=-2))
            p.update_marker(9, 'contact')
            p.update_marker(9, 'complete')
            path = recorder.finish(settings, p)
            metadata = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(metadata['sample_count'], 2)
            self.assertEqual(metadata['scan_grid']['pixel_count'], 9)
            self.assertEqual(metadata['scan_grid']['orientation_marker']['status'], 'complete')
            self.assertTrue(metadata['scan_grid']['orientation_marker']['contact_detected'])
            diagram = path.parent / metadata['orientation_diagram']
            ET.fromstring(diagram.read_text())
            dataset = AnalysisDataset.load(path)
            self.assertEqual(dataset.column('current1_na').tolist(), [1])
            self.assertEqual(dataset.metadata['orientation_marker_samples_excluded'], 1)
            self.assertEqual([pixel for pixel, _ in pixel_groups(dataset)], [0])
            path.with_suffix('.json').unlink()
            self.assertEqual(len(AnalysisDataset.load(path).rows), 1)

    def test_diagram_and_skipped_or_incomplete_metadata(self):
        p = ScanHoppingITParameters()
        svg = orientation_svg(p)
        ET.fromstring(svg)
        self.assertIn('>M</text>', svg)
        self.assertEqual(DataRecorder._scan_grid_metadata(p, 'aborted')['orientation_marker']['status'], 'skipped')
        p.update_marker(9, 'running')
        self.assertEqual(DataRecorder._scan_grid_metadata(p, 'error')['orientation_marker']['status'], 'incomplete')
        p.marker_enabled = False
        self.assertNotIn('>M</text>', orientation_svg(p))


if __name__ == '__main__':
    unittest.main()
