"""Pre-approach potential transients must never be interpreted as contact."""
import tempfile
from pathlib import Path
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch
from echemtips.adaptive import AdaptiveExperiment, AdaptiveParameters
from echemtips.backends import SimulationBackend
from echemtips.models import AppSettings


class ConditioningTests(TestCase):
    def test_transient_settles_before_child_or_persistent_current_aborts(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent), tempfile.TemporaryDirectory() as folder:
                b=SimulationBackend(AppSettings()); b.connect()
                e=AdaptiveExperiment(b,b.settings); e.configure_recording(Path(folder)/'run.csv')
                e.start(AdaptiveParameters(region_confirmed=True,approve_each=False))
                e.tick_samples([b.read_sample()])
                e.phase='travel_y'
                with patch.object(e,'_motion_done',return_value=True): e.tick_samples([b.read_sample()])
                self.assertEqual(e.phase,'conditioning'); self.assertIsNone(e.child)
                s=b.read_sample(); origin=e._conditioning_origin
                with patch.object(e,'_start_child') as start:
                    for j,i in enumerate([3.5,.5,.04,0.,0.,0.,0.]):
                        sample=replace(s,elapsed_s=origin+.06*(j+1),current1_na=.03 if persistent else i)
                        e.tick_samples([sample])
                        if e.phase!='conditioning': break
                    self.assertEqual(start.called,not persistent)
                    if persistent:
                        self.assertEqual(e.phase,'return_rejected')
                        self.assertFalse(e.params.attempts[0]['valid'])
                e.close()

    def test_clearance_rejection_is_not_saved_as_valid(self):
        from types import SimpleNamespace
        from echemtips.experiments import ExperimentState
        b=SimulationBackend(AppSettings()); b.connect()
        e=AdaptiveExperiment(b,b.settings)
        with tempfile.TemporaryDirectory() as folder:
            e.configure_recording(Path(folder)/'run.csv')
            e.start(AdaptiveParameters(region_confirmed=True,approve_each=False,tilt_enabled=True))
            e.tick_samples([b.read_sample()])
            e._attempt['contact_z_um']=e.params.start_z_um+.02
            e.child=SimpleNamespace(state=ExperimentState.COMPLETE)
            with patch('echemtips.adaptive.score_lsv',return_value=dict(valid=True,objective_na=.01,reason='Complete')):
                e._landing_done()
            self.assertFalse(e.params.attempts[0]['valid'])
            self.assertIn('clearance',e.params.attempts[0]['reason'])
            self.assertIn('"valid": false',e.journal_path.read_text().split('"event": "landing_result"')[1])
            e.close()
