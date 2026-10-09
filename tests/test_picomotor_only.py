"""Motor-only commissioning without a DLL, FPGA or physical movement."""
import contextlib
import io
import unittest
from unittest.mock import Mock

from echemtips.picomotor_only_test import run_test


class PicomotorOnlyTests(unittest.TestCase):
    def motor(self):
        motor=Mock()
        motor.connect.return_value='Newport 8742 test'
        motor.position.side_effect=[20,21]
        motor.motion_done.return_value=True
        return motor

    def run_quiet(self,motor,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return run_test(motor,**kwargs)

    def test_check_only_never_moves_or_prompts(self):
        m=self.motor();prompt=Mock(side_effect=AssertionError)
        self.assertEqual(self.run_quiet(m,check_only=True,prompt=prompt),0)
        m.move.assert_not_called();m.abort.assert_not_called();m.close.assert_called_once()

    def test_move_requires_explicit_confirmation(self):
        m=self.motor()
        self.assertEqual(self.run_quiet(m,prompt=lambda _:''),0)
        m.move.assert_not_called();m.close.assert_called_once()

    def test_one_signed_move_no_reversal(self):
        for steps in (1,-1):
            m=self.motor();m.position.side_effect=[20,20+steps]
            self.assertEqual(self.run_quiet(m,steps=steps,prompt=lambda _:'MOVE'),0)
            m.move.assert_called_once_with(steps=steps,rate=100)
            m.abort.assert_not_called();m.close.assert_called_once()

    def test_timeout_aborts_without_retry(self):
        m=self.motor();m.motion_done.return_value=False
        with self.assertRaises(TimeoutError):
            self.run_quiet(m,prompt=lambda _:'MOVE',clock=Mock(side_effect=[0,11]))
        m.move.assert_called_once();m.abort.assert_called_once();m.close.assert_called_once()

    def test_uncertain_move_and_interrupt_abort_and_close(self):
        for error in (RuntimeError('USB write uncertain'),KeyboardInterrupt()):
            m=self.motor();m.move.side_effect=error
            with self.assertRaises(type(error)):
                self.run_quiet(m,prompt=lambda _:'MOVE')
            m.move.assert_called_once();m.abort.assert_called_once();m.close.assert_called_once()

    def test_connection_failure_does_not_abort_unowned_motion(self):
        m=self.motor();m.connect.side_effect=RuntimeError('Already busy')
        with self.assertRaises(RuntimeError):self.run_quiet(m)
        m.abort.assert_not_called();m.close.assert_called_once()

    def test_pulse_count_mismatch_fails(self):
        m=self.motor();m.position.side_effect=[20,20]
        with self.assertRaises(RuntimeError):self.run_quiet(m,prompt=lambda _:'MOVE')
        m.abort.assert_called_once();m.close.assert_called_once()

    def test_invalid_limits_never_connect(self):
        for kwargs in ({'steps':0},{'steps':51},{'rate':0},{'timeout':float('nan')},{'timeout':.001}):
            m=self.motor()
            with self.assertRaises(ValueError):self.run_quiet(m,**kwargs)
            m.connect.assert_not_called()
