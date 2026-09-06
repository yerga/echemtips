from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from echemtips.models import (
    DEFAULT_BITFILE, AppSettings, ApproachCVParameters, ApproachITParameters, ApproachParameters,
    CVParameters, FeedbackConfiguration, Sample, ScanHoppingCVParameters, ScanHoppingITParameters,
)
from echemtips.ni_driver import WECSPMDriver
from echemtips.ni_protocol import (
    ANALOG_INPUT_CHANNELS,
    ANALOG_OUTPUT_CHANNELS,
    FEEDBACK_ACTION_CODES,
    FEEDBACK_SIGNAL_CODES,
    SAMPLE_WORDS,
    SampleDecoder,
    Waypoint,
    inspect_bitfile,
    position_to_raw,
    current_to_raw,
    raw_to_position,
    raw_to_voltage1,
    validate_wec_bitfile,
    voltage1_to_raw,
)
from echemtips.waypoints import PhysicalWaypoint


BITFILE = Path(DEFAULT_BITFILE)
LEGACY_BITFILE = BITFILE.with_name("FPGAProject_FPGATarget_FPGATarget2_ACEEEF6E.lvbitx")


class ProtocolTests(unittest.TestCase):
    @unittest.skipUnless(BITFILE.is_file() and LEGACY_BITFILE.is_file(), "private FPGA bitfiles are not installed")
    def test_usb_7856_bitfile_contract_and_legacy_guard(self) -> None:
        info = inspect_bitfile(BITFILE)
        self.assertEqual(info.target_class, "USB-7856R")
        self.assertEqual(info.signature, "8229BC0D5A4935D854D1286878CEE54A")
        self.assertEqual(validate_wec_bitfile(info, "USB R Series"), [])
        legacy = inspect_bitfile(LEGACY_BITFILE)
        self.assertTrue(any("not a USB" in error for error in validate_wec_bitfile(legacy, "USB R Series")))
        self.assertEqual(info.register_contract, legacy.register_contract)
        self.assertEqual(info.fifo_contract, legacy.fifo_contract)

    def test_deployed_semantic_channel_and_feedback_profile(self) -> None:
        self.assertEqual(ANALOG_OUTPUT_CHANNELS, {
            "X": "AO0", "Y": "AO1", "Z": "AO2", "Voltage 1": "AO3", "Voltage 2": "AO4",
        })
        self.assertEqual(ANALOG_INPUT_CHANNELS, {
            "X": "AI0", "Y": "AI1", "Z": "AI2", "Current 1": "AI3", "Current 2": "AI4",
        })
        self.assertEqual(FEEDBACK_SIGNAL_CODES, {"Current 1": 1, "Current 2": 2})
        self.assertEqual(FEEDBACK_ACTION_CODES["pause_on_contact"], 1)
        self.assertEqual(FEEDBACK_ACTION_CODES["advance_on_contact"], 2)

    def test_position_and_voltage_round_trip(self) -> None:
        for bipolar in (False, True):
            raw = position_to_raw(37.5, 100.0, bipolar)
            self.assertAlmostEqual(raw_to_position(raw, 100.0, bipolar), 37.5, places=2)
        raw_voltage = voltage1_to_raw(0.25, 4.0)
        self.assertAlmostEqual(raw_to_voltage1(raw_voltage, 4.0), 0.25, places=3)

    def test_waypoint_layout_and_flag_bits(self) -> None:
        waypoint = Waypoint(line_type=2, move_x=True, move_z=True, jump_v=True, hold_feedback1=True)
        words = waypoint.words()
        self.assertEqual(len(words), 14)
        self.assertEqual(words[0], 2)
        self.assertEqual(words[13], (1 << 0) | (1 << 2) | (1 << 4) | (1 << 8))

    def test_fifo_sample_decode(self) -> None:
        settings = AppSettings(command_voltage_ratio=2.0, current1_v_per_na=0.5)
        decoder = SampleDecoder(settings)
        # Biased U32 halves 0x8000/0x8000 travel as signed zeros.
        words = [0] * SAMPLE_WORDS
        words[3] = voltage1_to_raw(1.0, 2.0)
        words[5] = 16384
        first = decoder.decode(words)
        words[13] = 40  # low half becomes 0x8028: 40 ticks later
        second = decoder.decode(words)
        self.assertAlmostEqual(first.voltage1_v, 1.0, places=3)
        self.assertAlmostEqual(first.current1_na, 10.0, places=3)
        self.assertAlmostEqual(second.elapsed_s, 1e-6, places=9)


class _Register:
    def __init__(self, value=0):
        self.value = value

    def read(self):
        return self.value

    def write(self, value):
        self.value = value


class _FIFO:
    def __init__(self):
        self.writes: list[list[int]] = []
        self.data: list[int] = []

    def stop(self):
        pass

    def start(self):
        pass

    def configure(self, requested_depth):
        return requested_depth

    def write(self, data, timeout_ms=0):
        self.writes.append(list(data))
        return 0

    def read(self, number_of_elements, timeout_ms=0):
        data = self.data[:number_of_elements]
        self.data = self.data[number_of_elements:]
        return SimpleNamespace(data=data, elements_remaining=len(self.data))


class NativeDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        names = {
            "Buffer Loop Wait Time (tICKS)", "2^(-n)", "External Stop", "External Pause", "Internal Pause",
            "Internal Stop",
            "Applied X", "Applied Y", "Applied Z", "Applied Voltage", "Applied Voltage 2", "ExpandVelScaller X",
            "ExpandVelScaller Y", "ExpandVelScaller Z", "ExpandVelScaller V", "FeedBackType", "Feedback_Threshold",
            "GreaterThan", "LineNumber", "WaitingForWayPoints",
            "EndCurrentLine", "LineType", "Feedback1 Boolean", "Feedback1 Error", "Z END", "StopMoveZ",
            "ExpandVelScaller V2", "FeedBackType 2", "Feedback_Threshold 2", "GreaterThan 2", "P",
            "Upper limit Of dZ", "P2AvgWhole", "P2AvgMinus", "Feedback1 on  Hold",
            "DistanceToBulk", "DistanceToBulk 2", "DistanceToBulk 3",
        }
        self.session = SimpleNamespace(
            registers={name: _Register(False if "Pause" in name or name == "WaitingForWayPoints" else 0) for name in names},
            fifos={"Host_To_FPGA_Positions": _FIFO(), "FPGA_To_Host_FIFO": _FIFO()},
        )
        self.session.run = lambda: None
        self.session.reset = lambda: None
        self.settings = AppSettings()
        self.driver = WECSPMDriver(self.session, self.settings)

    def test_configures_timing_and_manual_move_frame(self) -> None:
        self.assertEqual(self.session.registers["Buffer Loop Wait Time (tICKS)"].value, 160)
        self.assertEqual(self.session.registers["2^(-n)"].value, 8)
        self.driver.move("Z", 25.0, 2.0)
        words = self.session.fifos["Host_To_FPGA_Positions"].writes[-1]
        self.assertEqual(len(words), 14)
        self.assertEqual(words[8], position_to_raw(25.0, 100.0, False))
        self.assertTrue(words[13] & (1 << 2))

    def test_shared_pause_resume_end_waypoint_and_feedback_controls(self) -> None:
        self.driver.resume()
        self.assertFalse(self.session.registers["External Pause"].value)
        self.driver.pause()
        self.assertTrue(self.session.registers["External Pause"].value)
        self.driver.end_current_waypoint()
        self.assertFalse(self.session.registers["EndCurrentLine"].value)
        config = FeedbackConfiguration(
            primary_channel="Current 1", primary_threshold=2.5,
            update_interval_us=7,
        )
        self.driver.configure_feedback(config)
        self.assertEqual(self.session.registers["Feedback_Threshold"].value, current_to_raw(2.5, 1.0))
        self.assertEqual(self.session.registers["FeedBackType 2"].value, 2)
        self.assertEqual(self.session.registers["Feedback_Threshold 2"].value, 0)
        self.assertTrue(self.session.registers["GreaterThan 2"].value)
        self.assertEqual(self.session.registers["P"].value, 0.0)
        self.assertEqual(self.session.registers["Upper limit Of dZ"].value, 10)
        self.assertEqual(self.session.registers["P2AvgWhole"].value, 1)
        self.assertEqual(self.session.registers["P2AvgMinus"].value, 0)
        self.assertFalse(self.session.registers["Feedback1 on  Hold"].value)
        self.assertEqual(self.session.registers["DistanceToBulk"].value, 0)
        self.assertEqual(self.driver._feedback_update_interval_us, 7)

    def test_standalone_cv_respects_ramp_at_start_and_reports_context(self) -> None:
        self.driver.start_method("cv", CVParameters(cycles=1, jump_at_start=False))
        words = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(words), 4 * 14)
        first = words[:14]
        self.assertTrue(first[13] & (1 << 3))
        self.assertFalse(first[13] & (1 << 4))
        self.assertEqual(self.driver.method_context(0), (-1, "cv"))
        self.assertEqual(self.driver.method_status()["stage"], "cv")

    def test_standalone_approach_contact_can_finish_without_retract(self) -> None:
        self.driver.start_method("approach", ApproachParameters(retract_after=False))
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        self.assertEqual(self.driver.method_status()["stage"], "contact")
        self.assertEqual(self.driver.method_status()["stage"], "complete")

    def test_shared_method_cancel_reports_abort_and_session_is_reusable(self) -> None:
        self.driver.start_method("approach", ApproachParameters())
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.stop_motion()
        self.assertEqual(self.driver.method_status()["stage"], "aborted")
        self.driver.start_method("cv", CVParameters(cycles=1))
        self.assertEqual(self.driver.method_status()["stage"], "cv")

    def test_approach_it_is_submitted_only_after_confirmed_contact(self) -> None:
        params = ApproachITParameters(
            initial_hold_s=.001, step_hold_s=.001, return_hold_s=.001,
            cycles=1, retract_after=True,
        )
        self.driver.start_method("approach_it", params)
        self.assertEqual(len(self.driver.positions_fifo.writes[-1]), 2 * 14)
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        self.assertEqual(self.driver.method_status()["stage"], "contact")
        self.assertEqual(len(self.driver.positions_fifo.writes[-1]), 4 * 14)
        self.assertEqual(self.driver.method_context(2), (-1, "it:initial"))
        self.assertEqual(self.driver.method_context(3), (-1, "it:pulse"))
        self.assertEqual(self.driver.method_context(5), (-1, "retract"))

    def test_approach_it_end_of_travel_retracts_without_surface_steps(self) -> None:
        params = ApproachITParameters(
            initial_hold_s=.001, step_hold_s=.001, return_hold_s=.001,
            retract_after=True,
        )
        self.driver.start_method("approach_it", params)
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.method_status()["stage"], "retracting")
        self.assertEqual(len(self.driver.positions_fifo.writes), 2)
        self.assertEqual(len(self.driver.positions_fifo.writes[-1]), 14)
        self.assertEqual(self.driver.method_context(2), (-1, "retract"))

        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.method_status()
        self.assertEqual(status["stage"], "aborted")
        self.assertIn("without contact", status["detail"])

    def test_hopping_it_starts_with_safe_retract_position_and_approach(self) -> None:
        params = ScanHoppingITParameters(
            x_points=1, y_points=1, initial_hold_s=.001,
            step_hold_s=.001, return_hold_s=.001,
        )
        self.driver.start_method("scan_hopping_it", params)
        words = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(words), 3 * 14)
        self.assertEqual(self.driver.method_context(0), (0, "retract"))
        self.assertEqual(self.driver.method_context(1), (0, "positioning"))
        self.assertEqual(self.driver.method_context(2), (0, "approach"))

    def test_it_methods_reject_signed_tag_wrap_before_fifo_write(self) -> None:
        self.session.registers["LineNumber"].value = 32765
        params = ScanHoppingITParameters(
            x_points=1, y_points=1, initial_hold_s=.001,
            step_hold_s=.001, return_hold_s=.001,
        )
        before = {name: register.value for name, register in self.session.registers.items()}
        with self.assertRaisesRegex(ValueError, "signed-I16"):
            self.driver.start_method("scan_hopping_it", params)
        self.assertEqual(before, {name: register.value for name, register in self.session.registers.items()})
        self.assertEqual(self.driver.positions_fifo.writes, [])

    def test_hopping_it_runs_contact_gated_surface_steps_at_every_point(self) -> None:
        params = ScanHoppingITParameters(
            x_points=2, y_points=1, initial_hold_s=.001,
            step_hold_s=.001, return_hold_s=.001,
        )
        self.driver.start_method("scan_hopping_it", params)
        for point in range(2):
            approach_end = self.driver._program_baseline + self.driver._program_total
            self.session.registers["LineNumber"].value = approach_end
            self.session.registers["Applied Z"].value = position_to_raw(68, self.settings.z_range_um, self.settings.z_bipolar)
            self.session.registers["Feedback1 Boolean"].value = True
            self.session.registers["Internal Pause"].value = True
            self.session.registers["WaitingForWayPoints"].value = True
            self.assertEqual(self.driver.method_status()["stage"], "contact")
            self.assertEqual(self.driver.method_context(approach_end), (point, "it:initial"))
            self.assertEqual(self.driver.method_context(approach_end + 1), (point, "it:pulse"))
            self.assertEqual(self.driver.method_context(approach_end + 3), (point, "retract"))
            retract = self.driver.positions_fifo.writes[-1][-14:]
            self.assertEqual(retract[8], position_to_raw(58, self.settings.z_range_um, self.settings.z_bipolar))

            self.session.registers["Feedback1 Boolean"].value = False
            self.session.registers["Internal Pause"].value = False
            self.session.registers["External Pause"].value = False
            self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
            self.session.registers["WaitingForWayPoints"].value = True
            self.driver.read_samples()

            status = self.driver.method_status()
            if point == 0:
                self.assertEqual(status["stage"], "approaching")
                self.assertEqual(status["point_index"], 1)
            else:
                self.assertEqual(status["stage"], "complete")

        # Successful terminal drain leaves the same USB session reusable.
        self.driver.start_method("cv", CVParameters(cycles=1))
        self.assertEqual(self.driver.method_status()["stage"], "cv")

    def test_hardware_approach_accepts_current_2_feedback(self) -> None:
        self.driver.start_method(
            "approach",
            ApproachParameters(feedback_channel="Current 2", feedback_threshold=1.5),
        )
        self.assertEqual(self.session.registers["FeedBackType"].value, 2)
        self.driver._observe_contacts([
            Sample(0, 50, 50, 68, 0.1, 0, 9.0, 2.0, line_number=1),
        ])
        self.assertIn(-1, self.driver._method_contact_observed)

    def test_resume_does_not_override_an_fpga_feedback_pause(self) -> None:
        self.session.registers["Internal Pause"].value = True
        with self.assertRaisesRegex(RuntimeError, "feedback pause"):
            self.driver.resume()

    def test_approach_cv_is_gated_by_confirmed_contact(self) -> None:
        params = ApproachCVParameters(cycles=2, retract_after=True, feedback_channel="Current 1")
        before = len(self.session.fifos["Host_To_FPGA_Positions"].writes)
        self.driver.start_approach_cv(params)
        writes = self.session.fifos["Host_To_FPGA_Positions"].writes
        self.assertEqual(len(writes), before + 1)
        # Only preposition + approach are initially submitted. CV cannot be
        # present in the FPGA queue until Python confirms the feedback pause.
        self.assertEqual(len(writes[-1]), 2 * 14)
        approach = writes[-1][14:28]
        self.assertEqual(approach[0], 1)
        self.assertTrue(approach[13] & (1 << 2))
        self.assertEqual(self.session.registers["FeedBackType"].value, 1)
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "contact")
        self.assertEqual(len(writes), before + 2)
        self.assertEqual(len(writes[-1]), (1 + 3 * 2 + 1) * 14)

    def test_approach_cv_optional_xy_is_in_preposition_waypoint(self) -> None:
        params = ApproachCVParameters(x_um=25.0, y_um=75.0, cycles=1)
        self.driver.start_approach_cv(params)
        first = self.session.fifos["Host_To_FPGA_Positions"].writes[-1][:14]
        self.assertEqual(first[6], position_to_raw(25.0, self.driver.settings.x_range_um, self.driver.settings.x_bipolar))
        self.assertEqual(first[7], position_to_raw(75.0, self.driver.settings.y_range_um, self.driver.settings.y_bipolar))
        self.assertTrue(first[13] & (1 << 0))
        self.assertTrue(first[13] & (1 << 1))

    def test_operator_accepted_hardware_approach_submits_followup(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        writes = self.session.fifos["Host_To_FPGA_Positions"].writes
        before = len(writes)
        self.driver.accept_approach()
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 2
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "contact")
        self.assertIn("Operator accepted", status["detail"])
        self.assertEqual(len(writes), before + 1)

    def test_operator_can_accept_hardware_scan_approaches(self) -> None:
        self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=1, y_points=1, cycles=1))
        self.driver.accept_approach()
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.scan_hopping_cv_status()["stage"], "cv")

        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.driver.scan_hopping_cv_status()
        self.driver.start_method("scan_hopping_it", ScanHoppingITParameters(x_points=1, y_points=1))
        self.driver.accept_approach()
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.method_status()["stage"], "it")

    def test_raster_cv_retracts_farther_before_line_flyback(self) -> None:
        params = ScanHoppingCVParameters(
            x_points=2, y_points=2, serpentine=False,
            start_z_um=55, end_z_um=80, raster_line_retract_um=7, cycles=1,
        )
        self.driver.start_scan_hopping_cv(params)
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.cancel_program()
        self.driver._cancelled = False
        self.session.registers["Applied Z"].value = position_to_raw(70, self.settings.z_range_um, self.settings.z_bipolar)
        self.driver._submit_scan_cv(1)
        final = self.session.fifos["Host_To_FPGA_Positions"].writes[-1][-14:]
        self.assertEqual(final[8], position_to_raw(53, self.settings.z_range_um, self.settings.z_bipolar))

    def test_approach_status_uses_recorded_line_baseline(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.session.registers["WaitingForWayPoints"].value = False
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "preposition")

    def test_hardware_end_of_travel_never_submits_cv(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "aborted")
        self.assertIn("CV was not submitted", status["detail"])
        self.assertEqual(len(self.driver.positions_fifo.writes), 1)

    def test_paused_counter_is_not_successful_completion(self) -> None:
        self.driver.move("Z", 20, 1)
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.session.registers["External Pause"].value = True
        self.driver.read_samples()
        with self.assertRaisesRegex(ValueError, "active"):
            self.driver.ensure_idle()

    def test_approach_context_identifies_fifo_sample_segments(self) -> None:
        self.session.registers["LineNumber"].value = 40
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1, retract_after=True))
        self.assertEqual(self.driver.approach_context(40), "preposition")
        self.assertEqual(self.driver.approach_context(41), "approach")
        self.session.registers["LineNumber"].value = 42
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.approach_cv_status()
        self.assertEqual(self.driver.approach_context(42), "cv")
        self.assertEqual(self.driver.approach_context(45), "cv")
        self.assertEqual(self.driver.approach_context(46), "retract")
        self.assertEqual(self.driver.approach_context(47), "")

    def test_scan_hopping_submits_cv_only_after_each_confirmed_contact(self) -> None:
        params = ScanHoppingCVParameters(x_points=2, y_points=2, cycles=1)
        self.driver.start_scan_hopping_cv(params)
        words = self.session.fifos["Host_To_FPGA_Positions"].writes[-1]
        # Initial safe retract, position, and approach only.
        self.assertEqual(len(words), 3 * 14)
        frames = [words[index : index + 14] for index in range(0, len(words), 14)]
        self.assertEqual(frames[0][13], 1 << 2)
        self.assertTrue(frames[1][13] & (1 << 0))
        self.assertTrue(frames[1][13] & (1 << 1))
        self.assertEqual(frames[2][0], 1)
        self.assertTrue(frames[2][13] & (1 << 2))
        self.assertEqual(self.driver.scan_context(0), (0, "retract"))
        self.assertEqual(self.driver.scan_context(2), (0, "approach"))
        self.session.registers["LineNumber"].value = 3
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        self.assertEqual(self.driver.scan_hopping_cv_status()["stage"], "cv")
        cv_words = self.session.fifos["Host_To_FPGA_Positions"].writes[-1]
        self.assertEqual(len(cv_words), 5 * 14)
        self.assertEqual(self.driver.scan_context(3), (0, "cv"))
        self.assertEqual(self.driver.scan_context(7), (0, "retract"))

    def test_fast_program_completion_requires_counter_and_final_drain(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.session.registers["External Pause"].value = False
        # Contact pauses the FPGA. Only then is the CV/retract follow-up queued.
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        self.assertEqual(self.driver.approach_cv_status()["stage"], "contact")
        self.assertNotEqual(self.driver.approach_cv_status()["stage"], "complete")
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["Feedback1 Boolean"].value = True
        self.assertNotEqual(self.driver.approach_cv_status()["stage"], "complete")
        self.session.fifos["FPGA_To_Host_FIFO"].data = [0] * SAMPLE_WORDS
        self.assertEqual(len(self.driver.read_samples()), 1)
        self.assertEqual(self.driver.approach_cv_status()["stage"], "complete")
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.assertNotEqual(self.driver.approach_cv_status()["stage"], "complete")

    def test_busy_then_waiting_does_not_prove_completion(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.driver.approach_cv_status()
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertNotEqual(self.driver.approach_cv_status()["stage"], "complete")
        with self.assertRaisesRegex(ValueError, "active"):
            self.driver.move("Z", 20, 1)
        with self.assertRaisesRegex(ValueError, "active"):
            self.driver.ensure_idle()

    def test_manual_move_can_repeat_after_polled_completion(self) -> None:
        self.driver.move("Z", 20, 1)
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = 1
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.driver.move("Z", 30, 1)
        self.assertEqual(len(self.session.fifos["Host_To_FPGA_Positions"].writes), 2)

    def test_scan_context_survives_completion(self) -> None:
        baseline = 20
        self.session.registers["LineNumber"].value = baseline
        self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=1, y_points=1, cycles=1))
        self.session.registers["External Pause"].value = False
        self.session.registers["Feedback1 Boolean"].value = True
        self.assertEqual(self.driver.scan_context(22), (0, "approach"))
        self.session.registers["LineNumber"].value = baseline + 3
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.scan_hopping_cv_status()
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.scan_hopping_cv_status()["stage"], "complete")
        self.assertEqual(self.driver.scan_context(22), (0, "approach"))

    def test_scan_rejects_unverified_i16_tag_overflow_without_writes(self) -> None:
        self.session.registers["LineNumber"].value = 32765
        before = {name: register.value for name, register in self.session.registers.items()}
        with self.assertRaisesRegex(ValueError, "I16"):
            self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=1, y_points=1))
        self.assertEqual(before, {name: register.value for name, register in self.session.registers.items()})
        self.assertEqual(self.driver.positions_fifo.writes, [])

    def test_manual_completion_handles_u64_wrap(self) -> None:
        self.session.registers["LineNumber"].value = (1 << 64) - 1
        self.driver.move("Z", 20, 1)
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = 0
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.driver.ensure_idle()

    def test_invalid_commands_do_not_write_registers(self) -> None:
        before = {name: register.value for name, register in self.session.registers.items()}
        for axis, target, speed in [("Q", 1, 1), ("Z", float("nan"), 1), ("Z", 101, 1), ("Z", 20, 0)]:
            with self.assertRaises(ValueError):
                self.driver.move(axis, target, speed)
        with self.assertRaises(ValueError):
            self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=64, y_points=64, cycles=100))
        self.assertEqual(before, {name: register.value for name, register in self.session.registers.items()})

    def test_partial_fifo_write_failure_latches_session(self) -> None:
        def fail_write(*args, **kwargs):
            raise TimeoutError("partial write")
        self.driver.positions_fifo.write = fail_write
        with self.assertRaises(TimeoutError):
            self.driver.move("Z", 20, 1)
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.driver.move("Z", 20, 1)

    def test_cancel_does_not_reset_outputs_and_allows_a_new_program(self) -> None:
        def forbidden_reset():
            self.fail("Stop must not reset analog outputs")
        self.session.reset = forbidden_reset
        self.driver.move("Z", 20, 1)
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.stop_motion()
        self.assertTrue(self.session.registers["External Pause"].value)
        self.assertFalse(self.session.registers["External Stop"].value)
        self.driver.move("Z", 30, 1)
        self.assertEqual(len(self.driver.positions_fifo.writes), 2)

    def test_emergency_stop_asserts_both_controls_and_latches_driver(self) -> None:
        self.driver.emergency_stop()
        self.assertTrue(self.session.registers["External Pause"].value)
        self.assertTrue(self.session.registers["External Stop"].value)
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.driver.move("Z", 20, 1)

    def test_internal_stop_blocks_command_before_fifo_write(self) -> None:
        self.session.registers["Internal Stop"].value = True
        with self.assertRaisesRegex(RuntimeError, "Internal Stop"):
            self.driver.move("Z", 20, 1)
        self.assertEqual(self.driver.positions_fifo.writes, [])
        self.assertTrue(self.session.registers["External Pause"].value)

    def test_idle_acquisition_detects_target_stop(self) -> None:
        self.session.registers["Internal Stop"].value = True
        with self.assertRaisesRegex(RuntimeError, "Internal Stop"):
            self.driver.read_samples()

    def test_idle_acquisition_detects_stopped_fpga_vi(self) -> None:
        self.session.fpga_vi_state = SimpleNamespace(name="NaturallyStopped")
        with self.assertRaisesRegex(RuntimeError, "NaturallyStopped"):
            self.driver.read_samples()

    def test_line_counter_overshoot_is_a_protocol_fault(self) -> None:
        self.driver.move("Z", 20, 1)
        self.session.registers["LineNumber"].value = 2
        with self.assertRaisesRegex(RuntimeError, "line counter"):
            self.driver.service()
        self.assertTrue(self.session.registers["External Pause"].value)

    def test_program_watchdog_pauses_and_latches_a_stalled_target(self) -> None:
        self.driver.move("Z", 20, 1)
        self.driver._program_deadline = 0.0
        with self.assertRaisesRegex(TimeoutError, "safety margin"):
            self.driver.service()
        self.assertTrue(self.session.registers["External Pause"].value)
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.driver.ensure_idle()

    def test_ready_handshake_accepts_waiting_target(self) -> None:
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.wait_until_ready()

    def test_ready_handshake_timeout_asserts_emergency_stop(self) -> None:
        self.driver.settings.hardware_ready_timeout_s = 0.5
        with patch("echemtips.ni_driver.time.monotonic", side_effect=(0.0, 1.0)):
            with self.assertRaisesRegex(TimeoutError, "ready state"):
                self.driver.wait_until_ready()
        self.assertTrue(self.session.registers["External Pause"].value)
        self.assertTrue(self.session.registers["External Stop"].value)

    def test_unrepresentable_feedback_threshold_is_rejected_before_writes(self) -> None:
        before = {name: register.value for name, register in self.session.registers.items()}
        with self.assertRaisesRegex(ValueError, "ADC range"):
            self.driver.start_approach_cv(ApproachCVParameters(feedback_threshold_na=11.0))
        self.assertEqual(before, {name: register.value for name, register in self.session.registers.items()})
        self.assertEqual(self.driver.positions_fifo.writes, [])

    def test_unrepresentable_ao3_program_is_rejected_before_writes(self) -> None:
        self.driver.settings.command_voltage_ratio = 20.0
        before = {name: register.value for name, register in self.session.registers.items()}
        with self.assertRaisesRegex(ValueError, "AO3"):
            self.driver.start_approach_cv(ApproachCVParameters(cv_vertex1_v=1.0))
        self.assertEqual(before, {name: register.value for name, register in self.session.registers.items()})
        self.assertEqual(self.driver.positions_fifo.writes, [])

    def test_long_program_is_refilled_in_complete_frame_chunks(self) -> None:
        self.driver.submit_waypoints(
            [PhysicalWaypoint(hold=True, hold_us=1) for _ in range(700)],
            owner="stream-test",
        )
        self.assertEqual(len(self.driver.positions_fifo.writes[0]), 512 * 14)
        self.assertEqual(self.driver.streamer.pending_waypoints, 188)
        self.driver.service()
        self.assertEqual(self.driver.streamer.pending_waypoints, 0)
        self.assertEqual([len(write) // 14 for write in self.driver.positions_fifo.writes], [512, 128, 60])
        self.assertEqual(self.driver.execution_status().owner, "stream-test")


if __name__ == "__main__":
    unittest.main()
