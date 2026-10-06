"""Bounded Z transport, stop handling and repeated approach commissioning tests."""
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from echemtips.picomotor import PicomotorZ, SimulatedPicomotor
from echemtips.coarse_approach import CoarseApproach, CoarseApproachParameters, CoarseSimulationBackend
from echemtips.models import AppSettings
from echemtips.experiments import ExperimentState


class FakeUSB:
    def __init__(self):
        self.commands=[]
        self.done=True
        self.error=0

    def connect(self): return 'New_Focus 8742 TEST'
    def query(self,cmd):
        return {'3MD?':str(int(self.done)),'3QM?':'3','TE?':str(self.error),'3TP?':'0'}[cmd]
    def write(self,cmd): self.commands.append(cmd)
    def close(self): pass


class PicomotorTests(TestCase):
    def params(self,**values):
        return replace(CoarseApproachParameters(direction=1,upper_um_per_step=.01,
            direction_verified=True,calibration_verified=True,clearance_verified=True),**values)

    def test_unknown_direction_and_calibration_block_automation(self):
        self.assertTrue(CoarseApproachParameters().validate(AppSettings()))
        self.assertEqual(self.params().validate(AppSettings()),[])
        self.assertTrue(self.params(upper_um_per_step=1).validate(AppSettings()))
        self.assertTrue(self.params(max_coarse_steps=10).validate(AppSettings()))

    def test_transport_uses_only_finite_z_moves_and_checks_errors(self):
        usb=FakeUSB(); motor=PicomotorZ(usb); motor.connect()
        motor.move(-20,100)
        self.assertEqual(usb.commands,['3VA100','3PR-20'])
        motor.abort(); self.assertEqual(usb.commands[-1],'AB')
        for steps in (0,5001,1.5):
            with self.assertRaises(ValueError): motor.move(steps,100)
        usb.done=False
        with self.assertRaises(RuntimeError): motor.move(1,100)
        usb.done=True; usb.error=208
        with self.assertRaises(RuntimeError): motor.move(1,100)

    def test_full_simulation_no_contact_coarse_retry_then_contact_and_retract(self):
        clock=[1000.]
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            settings=AppSettings(); backend=CoarseSimulationBackend(settings); backend.connect()
            motor=SimulatedPicomotor(backend,clock=lambda:clock[0]); motor.connect()
            e=CoarseApproach(backend,motor,settings,clock=lambda:clock[0]); e.start(self.params())
            for _ in range(20000):
                clock[0]+=.01
                e.tick(backend.read_samples())
                if not e.active: break
            self.assertEqual(e.phase,'complete',e.detail)
            self.assertGreater(e.attempts,1)
            self.assertGreater(e.total_steps,0)
            self.assertAlmostEqual(backend.commanded_position()['Z'],e.params.approach.start_z_um,delta=.08)
            self.assertIsNotNone(e.contact_z)
            kinds=[v['event'] for v in e.params.events]
            self.assertLess(kinds.index('no_contact'),kinds.index('coarse_command'))

    def test_budget_exhaustion_remains_withdrawn(self):
        clock=[1000.]
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            s=AppSettings(); b=CoarseSimulationBackend(s); b.connect()
            m=SimulatedPicomotor(b,clock=lambda:clock[0]); e=CoarseApproach(b,m,s,clock=lambda:clock[0])
            e.start(self.params(max_attempts=1))
            for _ in range(10000):
                clock[0]+=.01; e.tick(b.read_samples())
                if not e.active: break
            self.assertEqual(e.phase,'exhausted'); self.assertEqual(m.position(),0)
            self.assertAlmostEqual(b.commanded_position()['Z'],10,delta=.08)

    def test_native_no_contact_retract_allows_another_approach_without_reset(self):
        from tests.test_ni_protocol import NativeDriverTests
        from echemtips.experiments import ApproachParameters
        from echemtips.host import ExecutionState
        fixture=NativeDriverTests(); fixture.setUp()
        d=fixture.driver; regs=fixture.session.registers
        params=ApproachParameters(retract_after=True,feedback_mode='magnitude')
        with patch.object(fixture.session,'reset') as reset:
            d.start_method('approach',params)
            regs['LineNumber'].value=d._program_baseline+d._program_total
            regs['Applied Z'].value=d._program_waypoints[-1].z_position
            d.service()
            regs['WaitingForWayPoints'].value=True
            d.read_samples()
            self.assertEqual(d.method_status()['stage'],'retracting')
            regs['LineNumber'].value=d._program_baseline+d._program_total
            regs['Applied Z'].value=d._program_waypoints[-1].z_position
            d.read_samples()
            result=d.method_status()
            self.assertEqual(result['stage'],'aborted')
            self.assertIn('without contact; probe retracted',result['detail'])
            self.assertEqual(d.execution_status().state,ExecutionState.COMPLETE)
            d.start_method('approach',params)
            self.assertEqual(d.method_status()['stage'],'approaching')
            reset.assert_not_called()

    def test_stop_tries_both_devices_even_when_motor_usb_fails(self):
        b=CoarseSimulationBackend(AppSettings()); b.connect()
        m=SimulatedPicomotor(b); e=CoarseApproach(b,m,b.settings)
        with patch.object(m,'abort',side_effect=RuntimeError('USB unplugged')), patch.object(b,'emergency_stop') as stop:
            e.stop(); stop.assert_called_once()
        self.assertIn('stop unconfirmed',e.detail)

    def test_fault_or_unverified_withdrawal_never_permits_coarse_move(self):
        b=CoarseSimulationBackend(AppSettings()); b.connect()
        m=SimulatedPicomotor(b); e=CoarseApproach(b,m,b.settings); e.start(self.params())
        e.phase='approach'; e.child=SimpleNamespace(active=False,state=ExperimentState.ABORTED,
            detail='Operator stop',contact_z=None,_no_contact=False,tick_samples=lambda samples:None)
        with patch.object(e,'_withdrawn',return_value=True),patch.object(m,'move') as move:
            with self.assertRaises(RuntimeError): e.tick([])
            move.assert_not_called()

    def test_ui_simulation_defaults_and_hardware_confirmations_are_separate(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from echemtips.ui import create_application
        from echemtips.picomotor_test import PicomotorTestWindow
        app=create_application([])
        for hardware in (False,True):
            window=PicomotorTestWindow(AppSettings(),hardware)
            self.assertEqual(bool(window.parameters().validate(window.settings)),hardware)
            window.show(); app.processEvents()
            self.assertFalse(window.start_button.isEnabled())
            window.close()
