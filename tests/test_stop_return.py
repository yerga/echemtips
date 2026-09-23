"""Experimental-only stop/return checks; not physical hardware validation."""
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from echemtips.models import AppSettings, ApproachParameters, CVParameters, ScanHoppingCVParameters
from echemtips.ni_protocol import position_to_raw
from echemtips.stop_return import return_target, stop_and_return
from tests import test_ni_protocol


class StopReturnTests(unittest.TestCase):
    def fixture(self, current=60, start=10):
        fixture = test_ni_protocol.NativeDriverTests()
        fixture.setUp()
        d, regs = fixture.driver, fixture.session.registers
        p = ScanHoppingCVParameters(start_z_um=start, end_z_um=90)
        d.start_scan_hopping_cv(p)
        regs["Applied X"].value, regs["Applied Y"].value = 1234, 2345
        regs["Applied Z"].value = position_to_raw(current, 100, False)
        regs["WaitingForWayPoints"].value = True
        def move(axis, z, speed):
            d.move(axis, z, speed)
            regs["Applied Z"].value = position_to_raw(z, 100, False)
            regs["LineNumber"].value = d._program_baseline + d._program_total
            regs["WaitingForWayPoints"].value = True
        backend = SimpleNamespace(
            _driver=d, settings=fixture.settings, hardware_approach_cv_required=True,
            stop_motion=d.stop_motion, read_samples=d.read_samples,
            move=Mock(side_effect=move), emergency_stop=Mock(side_effect=d.emergency_stop),
        )
        def recover(_trial):
            self.assertTrue(d._stopped)
            self.assertFalse(d._framing_valid)
            d._stopped = False
            d._framing_valid = True
            regs["External Stop"].value = False
            regs["Internal Pause"].value = False
        return backend, p, regs, recover

    def test_target_never_moves_toward_surface(self):
        p = ApproachParameters(start_z_um=10, end_z_um=90, retract_rate_um_s=5)
        self.assertEqual(return_target(p, 60, 100), (10, 5))
        self.assertEqual(return_target(p, 5, 100), (5, 5))
        p.start_z_um, p.end_z_um = 90, 0
        self.assertEqual(return_target(p, 30, 100), (90, 5))
        self.assertEqual(return_target(p, 95, 100), (95, 5))
        self.assertIsNone(return_target(CVParameters(), 60, 100))

    def test_success_returns_only_z_then_allows_another_program(self):
        b, p, regs, recover = self.fixture()
        saved = []
        with patch("echemtips.stop_return.RecoveryTrial.recover", autospec=True, side_effect=recover):
            result = stop_and_return(b, p, io.StringIO(), lambda tail: saved.append(tail), lambda: None, lambda _: None)
        self.assertEqual(len(saved), 1)
        self.assertEqual(result["target_z_um"], 10)
        b.move.assert_called_once_with("Z", 10, p.retract_rate_um_s)
        self.assertEqual((regs["Applied X"].value, regs["Applied Y"].value), (1234, 2345))
        b.emergency_stop.assert_not_called()
        b._driver.start_scan_hopping_cv(p)
        self.assertTrue(b._driver._submitted)

    def test_recording_failure_or_recovery_failure_never_returns(self):
        for where in ("recording", "recovery"):
            b, p, regs, recover = self.fixture()
            saved = Mock(side_effect=OSError("disk error") if where == "recording" else None)
            with patch("echemtips.stop_return.RecoveryTrial.recover", side_effect=RuntimeError("bad framing")):
                with self.assertRaises((OSError, RuntimeError)):
                    stop_and_return(b, p, io.StringIO(), saved, lambda: None, lambda _: None)
            b.move.assert_not_called()
            b.emergency_stop.assert_called_once()
            self.assertTrue(regs["External Stop"].value)

    def test_emergency_during_recovery_never_returns(self):
        b, p, regs, recover = self.fixture()
        abort = threading.Event()
        def check():
            if abort.is_set(): raise RuntimeError("emergency")
        with patch("echemtips.stop_return.RecoveryTrial.recover", side_effect=abort.set):
            with self.assertRaisesRegex(RuntimeError, "emergency"):
                stop_and_return(b, p, io.StringIO(), lambda _: None, check, lambda _: None)
        b.move.assert_not_called()
        b.emergency_stop.assert_called_once()

    def test_fault_latch_is_not_recovered(self):
        b, p, regs, recover = self.fixture()
        b._driver._stopped = True
        with patch("echemtips.stop_return.RecoveryTrial.recover") as recovery:
            with self.assertRaisesRegex(RuntimeError, "Fault-latched"):
                stop_and_return(b, p, io.StringIO(), lambda _: None, lambda: None, lambda _: None)
        recovery.assert_not_called()
        b.move.assert_not_called()

    def test_already_retracted_position_is_held(self):
        b, p, regs, recover = self.fixture(current=5, start=10)
        with patch("echemtips.stop_return.RecoveryTrial.recover", autospec=True, side_effect=recover):
            result = stop_and_return(b, p, io.StringIO(), lambda _: None, lambda: None, lambda _: None)
        b.move.assert_not_called()
        self.assertLess(result["target_z_um"], p.start_z_um)
