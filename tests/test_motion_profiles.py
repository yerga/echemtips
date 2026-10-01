"""Profile geometry, causal detachment, framing and simulation regression checks."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from echemtips.models import AppSettings, ApproachCVParameters, ScanHoppingCVParameters, ScanHoppingITParameters, Sample
from echemtips.motion_profiles import RetractionProfile, DetachmentMonitor, predicted_contact, approach_switch
from tests.test_conditioning import run_sim
from echemtips.experiments import ScanHoppingCVExperiment, ScanHoppingITExperiment, ExperimentState


class MotionProfileTests(unittest.TestCase):
    def test_prediction_requires_surface_information(self):
        p=ApproachCVParameters(approach_profile_enabled=True)
        self.assertIsNone(approach_switch(p,[],20,20,10))
        self.assertIsNone(predicted_contact([(0,0,50),(1,0,50),(2,0,50)],1,1))
        self.assertAlmostEqual(approach_switch(p,[(0,0,50),(1,0,50),(0,1,50)],.5,.5,10),45)
        self.assertIsNone(approach_switch(p,[(0,0,50),(1,0,50),(0,1,50)],.5,.5,48))

    def test_fixed_distance_and_buffer_never_exceed_final_target(self):
        p=ApproachCVParameters(retract_profile_enabled=True,retract_switch_distance_um=2)
        profile=RetractionProfile(p,20,10)
        self.assertEqual(profile.next_segment(20), (19.75,1))
        self.assertEqual(profile.next_segment(18),(10,15))
        self.assertEqual(profile.event['switch_z_um'],18)
        p=replace(p,retract_automatic=True,retract_buffer_um=1)
        profile=RetractionProfile(p,20,10)
        profile.monitor.detected_z=16
        self.assertEqual(profile.next_segment(15.5),(15.25,1))
        self.assertEqual(profile.next_segment(15),(10,15))
        profile=RetractionProfile(p,20,19.5)
        profile.monitor.detected_z=19.7
        self.assertIsNone(profile.next_segment(19.5))
        self.assertFalse(profile.fast)

    def test_detachment_requires_sustained_collapse_for_either_sign(self):
        for sign in (-1,1):
            d=DetachmentMonitor(.001,.0001,.1,'Current 1')
            for n in range(30):
                d.observe(Sample(n*.01,0,0,20-n*.01,.1,0,.001+sign*.1,0,0),20-n*.01)
            self.assertTrue(d.attached)
            # A single quiet outlier cannot confirm a break.
            d.observe(Sample(.30,0,0,19.7,.1,0,.001,0,0),19.7)
            self.assertIsNone(d.detected_z)
            for n in range(31,50):
                d.observe(Sample(n*.01,0,0,20-n*.01,.1,0,.001,0,0),20-n*.01)
            self.assertIsNotNone(d.detected_z)
        d=DetachmentMonitor(0,.0001,.1,'Current 1')
        d.observe(Sample(0,0,0,20,.2,0,.1,0,0),20)
        self.assertTrue(d.invalid)

    def test_real_simulated_scan_profiles_and_repeated_hops(self):
        for cls,model in ((ScanHoppingCVExperiment,ScanHoppingCVParameters),(ScanHoppingITExperiment,ScanHoppingITParameters)):
            for auto in (False,True):
                p=model(x_points=2,y_points=2,marker_enabled=False,cycles=1,
                        approach_profile_enabled=True,retract_profile_enabled=True,
                        retract_automatic=auto,retract_switch_distance_um=2,retract_buffer_um=.5)
                e,samples=run_sim(cls,p)
                self.assertEqual(e.state,ExperimentState.COMPLETE,e.detail)
                events=[v for v in p.motion_events if v['kind']=='retraction']
                self.assertEqual(len(events),4)
                if not auto: self.assertTrue(all(v['switch_z_um'] is not None for v in events))
                self.assertAlmostEqual(samples[-1].commanded_z_um,p.start_z_um,delta=.1)
                self.assertTrue(all(s.z_um>=0 for s in samples))

    def test_native_profile_retraction_is_gated_by_final_drain_and_pause(self):
        from tests.test_ni_protocol import NativeDriverTests
        f=NativeDriverTests(); f.setUp(); d=f.driver
        p=ScanHoppingCVParameters(x_points=1,y_points=1,marker_enabled=False,
                                 retract_profile_enabled=True,retract_switch_distance_um=1)
        d.start_scan_hopping_cv(p)
        from echemtips.ni_protocol import position_to_raw
        d._write_register('Applied Z', position_to_raw(70, d.settings.z_range_um, d.settings.z_bipolar))
        with patch.object(d,'_enqueue'): d._submit_scan_cv(0)
        self.assertNotIn('retract',[c for _,c in d._scan_sequence.descriptors])
        self.assertIsNotNone(d._profile_retraction)
        with patch.object(d,'_profile_submit') as submit:
            d._submitted=True; d._hardware_complete=True; d._service_motion_profiles()
            submit.assert_not_called()
            d._submitted=False; d._operator_paused=True; d._service_motion_profiles()
            submit.assert_not_called()
            d._operator_paused=False; d._write_register('External Pause',False)
            d._service_motion_profiles()
            submit.assert_called_once()

    def test_validation_and_estimates(self):
        p=ScanHoppingCVParameters(retract_profile_enabled=True,retract_slow_um_s=2,retract_fast_um_s=10,retract_switch_distance_um=4)
        self.assertAlmostEqual(p.motion_duration(10,'retract',50),2.6)
        self.assertAlmostEqual(replace(p,retract_automatic=True).motion_duration(10,'retract',50),5)
        self.assertTrue(replace(p,retract_fast_um_s=1).validate(AppSettings()))
        self.assertTrue(replace(p,approach_profile_enabled=True,approach_clearance_um=0).validate(AppSettings()))

    def test_automatic_simulation_switches_after_buffer(self):
        p=ScanHoppingCVParameters(x_points=2,y_points=2,marker_enabled=False,cycles=1,
            cv_start_v=.3,retract_profile_enabled=True,retract_automatic=True,retract_buffer_um=.5)
        e,_=run_sim(ScanHoppingCVExperiment,p)
        self.assertEqual(e.state,ExperimentState.COMPLETE)
        for event in p.motion_events:
            self.assertEqual(event['reason'],'confirmed detachment + buffer')
            self.assertGreaterEqual(event['detected_z_um']-event['switch_z_um'],.5-1e-6)

    def test_native_fast_approach_waits_for_framed_boundary(self):
        from tests.test_ni_protocol import NativeDriverTests
        f=NativeDriverTests(); f.setUp(); d=f.driver
        p=ApproachCVParameters(approach_profile_enabled=True,expected_contact_z_um=70)
        d.start_approach_cv(p)
        self.assertIsNotNone(d._profile_approach_next)
        with patch.object(d,'_profile_submit') as submit:
            d._contact_finish_reason='limit'; d._hardware_complete=True; d._submitted=True
            d._service_motion_profiles(); submit.assert_not_called()
            d._submitted=False; d._service_motion_profiles()
            self.assertEqual(submit.call_args.args[1],['speed_settle','approach'])

    def test_compact_ui_and_profile_preset_round_trip(self):
        from echemtips.ui import create_application, EChemTipsApp
        from echemtips.workspace_layout import form_values, restore_form
        app=create_application([]); window=EChemTipsApp(); window.poll_timer.stop()
        try:
            for name in ('Approach','Approach + CV','Approach + CV scan-rate series',
                         'Approach + I-t','Scan hopping + CV','Scan hopping + I-t'):
                page=window.pages[name]
                self.assertIn('Off',page.motion_profile.text())
                page.motion_profile.set_values(dict(approach_profile_enabled=True,retract_profile_enabled=True,
                                                   retract_automatic=True,retract_buffer_um=.75))
                saved=form_values(page); page.motion_profile.set_values({}); restore_form(page,saved)
                p=page.parameters()
                self.assertTrue(p.approach_profile_enabled)
                self.assertTrue(p.retract_automatic)
                self.assertEqual(p.retract_buffer_um,.75)
        finally:
            window.close()
