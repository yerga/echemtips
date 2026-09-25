"""Real-time simulated combinatorial contact and landing lifecycle regressions."""
import unittest
from unittest.mock import patch

from echemtips.backends import SimulationBackend
from echemtips.models import AppSettings, ScanHoppingCVParameters, ScanHoppingITParameters, CVParameters
from echemtips.experiments import ScanHoppingCVExperiment, ScanHoppingITExperiment, CVExperiment, ExperimentState
from test_combinatorial import configure


class CombinatorialContactTests(unittest.TestCase):
    def test_realistic_clock_all_hops_both_channels_and_polarities(self):
        for cls, runner in ((ScanHoppingCVParameters, ScanHoppingCVExperiment), (ScanHoppingITParameters, ScanHoppingITExperiment)):
            for convention, channel in [('IUPAC', 'Current 1'), ('Instrument-native', 'Current 2')]:
                with self.subTest(method=cls.__name__,convention=convention), patch('time.monotonic') as clock:
                    now=[1000.0]; clock.side_effect=lambda:now[0]
                    settings=AppSettings(z_range_um=200,polarity_convention=convention)
                    backend=SimulationBackend(settings); backend.connect()
                    p=configure(cls(x_points=2,y_points=2,settling_time_s=.5,feedback_channel=channel,feedback_mode='magnitude'))
                    experiment=runner(backend,settings); experiment.start(p)
                    landed=set(); measuring=set()
                    for _,_,x,y in p.execution_grid():
                        self.assertTrue(p.start_z_um<backend.surface_z_at(x,y)<p.end_z_um)
                    for _ in range(30000):
                        now[0]+=.02
                        before=experiment.state
                        point=experiment.point_index
                        sample=backend.read_sample(); experiment.tick_samples([sample])
                        if before==ExperimentState.APPROACHING and experiment.state==ExperimentState.SETTLING:
                            landed.add(point)
                            self.assertAlmostEqual(sample.z_um,backend.surface_z_at(sample.x_um,sample.y_um),delta=.4)
                            current=sample.current1_na if channel=='Current 1' else sample.current2_na
                            self.assertGreater(abs(current),.005)
                        if experiment.state in (ExperimentState.CV,ExperimentState.IT): measuring.add(experiment.point_index)
                        if not experiment.active: break
                    self.assertEqual(experiment.state,ExperimentState.COMPLETE,experiment.detail)
                    self.assertEqual(landed,set(range(5)))
                    self.assertEqual(measuring,set(range(5)))
                    self.assertAlmostEqual(backend.commanded_position()['Z'],p.start_z_um,delta=.08)

    def test_transient_latch_failed_contact_and_scene_reset(self):
        with patch('time.monotonic') as clock:
            now=[1000.0]; clock.side_effect=lambda:now[0]
            backend=SimulationBackend(AppSettings(z_range_um=200)); backend.connect()
            p=configure(ScanHoppingCVParameters(x_points=1,y_points=1,x_start_um=35,x_end_um=35,y_start_um=35,y_end_um=35,feedback_mode='magnitude'))
            backend.configure_hopping_scene(p); backend.begin_hopping_point(0)
            backend._positions.update(X=35,Y=35,Z=p.start_z_um)
            backend._targets=dict(backend._positions)
            self.assertLess(abs(backend.read_sample().current1_na),.005)
            surface=backend.surface_z_at(35,35)
            backend._positions['Z']=surface; backend._targets['Z']=p.end_z_um
            contact=backend.read_sample()
            self.assertGreater(abs(contact.current1_na),.04)
            backend.stop_motion(); now[0]+=1
            self.assertGreater(abs(backend.read_sample().current1_na),.005)
            backend._positions['Z']=backend._targets['Z']=surface-1
            self.assertLess(abs(backend.read_sample().current1_na),.005)
            backend.begin_hopping_point(1); backend.adaptive_failure_pixel=1
            backend._positions['Z']=surface; backend._targets['Z']=p.end_z_um
            self.assertLess(abs(backend.read_sample().current1_na),.005)
            self.assertEqual(p.feedback_threshold_na,.005)
            CVExperiment(backend,backend.settings).start(CVParameters())
            self.assertFalse(backend.adaptive_scene)

    def test_unreachable_threshold_does_not_force_contact(self):
        with patch('time.monotonic') as clock:
            now=[1000.0]; clock.side_effect=lambda:now[0]
            settings=AppSettings(z_range_um=200)
            backend=SimulationBackend(settings); backend.connect()
            p=configure(ScanHoppingCVParameters(x_points=1,y_points=1,marker_enabled=False,feedback_mode='magnitude',feedback_threshold_na=10))
            experiment=ScanHoppingCVExperiment(backend,settings); experiment.start(p)
            for _ in range(10000):
                now[0]+=.02; experiment.tick_samples([backend.read_sample()])
                self.assertNotEqual(experiment.state,ExperimentState.CV)
                if not experiment.active: break
            self.assertEqual(experiment.state,ExperimentState.ABORTED)


if __name__=='__main__': unittest.main()
