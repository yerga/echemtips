from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from contextlib import redirect_stderr
import io
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
    BitfileInfo, REGISTER_CONTRACT, FIFO_CONTRACT,
    ANALOG_OUTPUT_CHANNELS,
    FEEDBACK_ACTION_CODES,
    FEEDBACK_SIGNAL_CODES,
    FPGA_TICKS_PER_US,
    DEPLOYED_STARTUP_RAW_OUTPUTS,
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


class ProtocolTests(unittest.TestCase):
    def test_checker_requires_a_selected_bitfile_and_defaults_to_auto_transport(self) -> None:
        from echemtips.hardware_check import _parser, main
        self.assertEqual(_parser().parse_args([]).transport, "auto")
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(main(["--bitfile", ""]), 2)
        self.assertIn("supply --bitfile", output.getvalue())

    @unittest.skipUnless(BITFILE.is_file(), "set ECHEMTIPS_BITFILE to test a private FPGA bitfile")
    def test_locally_supplied_bitfile_contract(self) -> None:
        info = inspect_bitfile(BITFILE)
        self.assertEqual(validate_wec_bitfile(info), [])

    def test_compatible_builds_are_not_pinned_to_filename_model_or_signature(self) -> None:
        for model in ("USB-7856R", "USB-7855R", "PCIe-7852R", "PXI-7852R"):
            info = BitfileInfo(Path("user-selected-name.lvbitx"), model, "new-build-signature",
                               frozenset(REGISTER_CONTRACT), frozenset(FIFO_CONTRACT),
                               dict(REGISTER_CONTRACT), dict(FIFO_CONTRACT))
            self.assertEqual(validate_wec_bitfile(info), [])
            transport = "USB R Series" if info.is_usb_target else "PCIe/PXI R Series"
            self.assertEqual(validate_wec_bitfile(info, transport), [])
            other = "PCIe/PXI R Series" if info.is_usb_target else "USB R Series"
            self.assertTrue(validate_wec_bitfile(info, other))
            name = next(iter(REGISTER_CONTRACT))
            self.assertTrue(validate_wec_bitfile(replace(info, registers=info.registers - {name})))
            self.assertTrue(validate_wec_bitfile(replace(info, register_contract={name: ("WrongType", False)})))
            fifo = next(iter(FIFO_CONTRACT))
            for contract in (("U16", "HostToTarget", 8197), ("I16", "WrongDirection", 8197), ("I16", "HostToTarget", 1)):
                self.assertTrue(validate_wec_bitfile(replace(info, fifo_contract={fifo: contract})))

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
        settings = AppSettings(polarity_convention="Instrument-native", command_voltage_ratio=2.0, current1_v_per_na=0.5)
        decoder = SampleDecoder(settings)
        # Each frame carries a biased U32 interval, not an absolute timestamp.
        words = [0] * SAMPLE_WORDS
        words[3] = voltage1_to_raw(1.0, 2.0)
        words[5] = 16384
        first = decoder.decode(words)
        words[12] = -32768
        words[13] = 40 - 32768  # interval of 40 ticks (one microsecond)
        second = decoder.decode(words)
        self.assertAlmostEqual(first.voltage1_v, 1.0, places=3)
        self.assertAlmostEqual(first.current1_na, 10.0, places=3)
        self.assertAlmostEqual(second.elapsed_s, 1e-6, places=9)

    def test_fifo_intervals_accumulate_for_constant_and_variable_sample_rates(self) -> None:
        decoder = SampleDecoder(AppSettings(polarity_convention="Instrument-native", ))
        expected_ticks = 0
        # Include signed-word boundaries and decreasing intervals: neither is
        # an absolute-counter wrap. The initial startup interval is excluded.
        for index, ticks in enumerate((123456789, 41120, 41120, 65535, 65536, 1, 0, 40000000)):
            words = [0] * SAMPLE_WORDS
            words[12] = (ticks >> 16) - 32768
            words[13] = (ticks & 0xFFFF) - 32768
            if index:
                expected_ticks += ticks
            self.assertAlmostEqual(decoder.decode(words).elapsed_s, expected_ticks / 40000000, places=12)

    def test_fifo_elapsed_time_exceeds_u32_clock_period(self) -> None:
        decoder = SampleDecoder(AppSettings(polarity_convention="Instrument-native", ))
        words = [0] * SAMPLE_WORDS
        ticks = 40000000
        words[12] = (ticks >> 16) - 32768
        words[13] = (ticks & 0xFFFF) - 32768
        for second in range(121):
            self.assertEqual(decoder.decode(words).elapsed_s, second)
        self.assertEqual(SampleDecoder(AppSettings(polarity_convention="Instrument-native", )).decode(words).elapsed_s, 0)


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
    def test_fifo_batch_boundaries_preserve_sample_time(self) -> None:
        from tempfile import TemporaryDirectory
        from echemtips.data import DataRecorder
        import csv

        frame = [0] * SAMPLE_WORDS
        frame[12] = -32768
        frame[13] = 40000 - 32768  # 1 ms between samples
        fifo = self.session.fifos["FPGA_To_Host_FIFO"]
        with TemporaryDirectory() as folder:
            recorder = DataRecorder()
            recorder.start("Watch Current", AppSettings(polarity_convention="Instrument-native", save_directory=folder))
            received = []
            for size in (3, 0, 2):
                fifo.data.extend(frame * size)
                samples = self.driver.read_samples()
                received.extend(samples)
                for sample in samples:
                    recorder.append(sample)
            path = recorder.finish()
            self.assertEqual([s.elapsed_s for s in received], [0, .001, .002, .003, .004])
            with path.open(newline="") as stream:
                self.assertEqual([float(row["elapsed_s"]) for row in csv.DictReader(stream)],
                                 [0, .001, .002, .003, .004])

    def setUp(self) -> None:
        names = {
            "Buffer Loop Wait Time (tICKS)", "HoldTimerScale", "2^(-n)", "External Stop", "External Pause", "Internal Pause",
            "Internal Stop",
            "Applied X", "Applied Y", "Applied Z", "Applied Voltage", "Applied Voltage 2", "ExpandVelScaller X",
            "ExpandVelScaller Y", "ExpandVelScaller Z", "ExpandVelScaller V", "FeedBackType", "Feedback_Threshold",
            "GreaterThan", "LineNumber", "WaitingForWayPoints",
            "V on Fly", "V2 on Fly", "Change V on Fly", "Change V on Fly 2",
            "EndCurrentLine", "LineType", "Feedback1 Boolean", "Feedback2 Boolean", "Feedback1 Error", "Z END", "StopMoveZ",
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
        self.settings = AppSettings(polarity_convention="Instrument-native", )
        self.driver = WECSPMDriver(self.session, self.settings)

    def test_configures_timing_and_manual_move_frame(self) -> None:
        self.assertEqual(self.session.registers["Buffer Loop Wait Time (tICKS)"].value, 160)
        self.assertEqual(self.session.registers["HoldTimerScale"].value, FPGA_TICKS_PER_US)
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
        self.driver.move("Z", 20, 1)
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1
        self.driver.end_current_waypoint()
        self.assertTrue(self.session.registers["EndCurrentLine"].value)
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertFalse(self.session.registers["EndCurrentLine"].value)
        config = FeedbackConfiguration(
            primary_channel="Current 1", primary_threshold=2.5,
            update_interval_us=7,
        )
        self.driver.configure_feedback(config)
        self.assertEqual(self.session.registers["Feedback_Threshold"].value, current_to_raw(2.5, 1.0))
        self.assertEqual(self.session.registers["FeedBackType 2"].value, 2)
        self.assertEqual(self.session.registers["Feedback_Threshold 2"].value, 32768)
        self.assertTrue(self.session.registers["GreaterThan 2"].value)
        self.assertEqual(self.session.registers["P"].value, 0.0)
        self.assertEqual(self.session.registers["Upper limit Of dZ"].value, 10)
        self.assertEqual(self.session.registers["P2AvgWhole"].value, 1)
        self.assertEqual(self.session.registers["P2AvgMinus"].value, 0)
        self.assertFalse(self.session.registers["Feedback1 on  Hold"].value)
        self.assertEqual(self.session.registers["DistanceToBulk"].value, 0)
        self.assertEqual(self.driver._feedback_update_interval_us, 7)

    def test_idle_potential_uses_jump_waypoint_and_applied_value_acknowledgement(self) -> None:
        target = voltage1_to_raw(0.25, self.settings.command_voltage_ratio)
        with patch.object(self.driver, "_wait_for_idle_potential") as wait:
            self.driver.set_voltage(1, 0.25)
        words = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(words), 14)
        self.assertEqual(words[9], target)
        self.assertTrue(words[13] & (1 << 3))
        self.assertTrue(words[13] & (1 << 4))
        self.assertFalse(self.session.registers["Change V on Fly"].value)
        wait.assert_called_once_with(1, target)

        self.session.registers["Applied Voltage"].value = target
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver._wait_for_idle_potential(1, target)
        self.driver.ensure_idle()

    def test_live_potential_holds_trigger_until_applied_value_acknowledges(self) -> None:
        self.driver.submit_waypoints([
            PhysicalWaypoint(voltage1_v=0.5, voltage1_rate_v_s=0.1)
        ], owner="live-potential-test")
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1
        target = voltage1_to_raw(0.2, self.settings.command_voltage_ratio)

        def acknowledge() -> bool:
            self.assertTrue(self.session.registers["Change V on Fly"].value)
            self.session.registers["Applied Voltage"].value = target
            return False

        with patch.object(self.driver, "service", side_effect=acknowledge):
            self.driver.set_live_potential(1, 0.2)
        self.assertEqual(self.session.registers["V on Fly"].value, target)
        self.assertFalse(self.session.registers["Change V on Fly"].value)

    def test_live_potential_rejects_inactive_voltage_axis(self) -> None:
        self.driver.move("Z", 20, 1)
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1
        with self.assertRaisesRegex(RuntimeError, "not active"):
            self.driver.set_live_potential(1, 0.2)

    def test_standalone_cv_respects_ramp_at_start_and_reports_context(self) -> None:
        self.driver.start_method("cv", CVParameters(cycles=1, jump_at_start=False))
        words = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(words), 4 * 14)
        first = words[:14]
        self.assertTrue(first[13] & (1 << 3))
        self.assertFalse(first[13] & (1 << 4))
        self.assertEqual(self.driver.method_context(0), (-1, ""))
        self.assertEqual(self.driver.method_context(1), (-1, "cv"))
        self.assertEqual(self.driver.method_status()["stage"], "cv")

    def test_standalone_approach_contact_can_finish_without_retract(self) -> None:
        self.driver.start_method("approach", ApproachParameters(retract_after=False))
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        self.assertEqual(self.driver.method_status()["stage"], "contact")
        self.assertFalse(self.session.registers["EndCurrentLine"].value)
        self.assertEqual(self.session.registers["Feedback_Threshold 2"].value, -32769)
        self.assertFalse(self.session.registers["External Stop"].value)
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.method_status()["stage"], "complete")

    def test_shared_method_cancel_retires_stream_until_reconnect(self) -> None:
        self.driver.start_method("approach", ApproachParameters())
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.stop_motion()
        self.assertEqual(self.driver.method_status()["stage"], "aborted")
        self.assertTrue(self.session.registers["External Stop"].value)
        with self.assertRaisesRegex(ValueError, "Reinitialize"):
            self.driver.start_method("cv", CVParameters(cycles=1))

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
        self.session.registers["WaitingForWayPoints"].value = False
        self.assertEqual(self.driver.method_status()["stage"], "contact")
        self.assertEqual(len(self.driver.positions_fifo.writes[-1]), 2 * 14)
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.method_status()["stage"], "it")
        self.assertEqual(len(self.driver.positions_fifo.writes[-1]), 4 * 14)
        self.assertEqual(self.driver.method_context(2), (-1, "approach"))
        self.assertEqual(self.driver.method_context(3), (-1, "it:initial"))
        self.assertEqual(self.driver.method_context(4), (-1, "it:pulse"))
        self.assertEqual(self.driver.method_context(6), (-1, "retract"))

    def test_approach_it_end_of_travel_retracts_without_surface_steps(self) -> None:
        params = ApproachITParameters(
            initial_hold_s=.001, step_hold_s=.001, return_hold_s=.001,
            retract_after=True,
        )
        self.driver.start_method("approach_it", params)
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["Applied Z"].value = self.driver._program_waypoints[-1].z_position
        self.driver.service()  # Type 2 holds at End Z; request a no-contact exit.
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.method_status()["stage"], "retracting")
        self.assertEqual(len(self.driver.positions_fifo.writes), 2)
        self.assertEqual(len(self.driver.positions_fifo.writes[-1]), 14)
        self.assertEqual(self.driver.method_context(3), (-1, "retract"))

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
        self.assertEqual(self.driver.method_context(0), (-1, ""))
        self.assertEqual(self.driver.method_context(1), (0, "retract"))
        self.assertEqual(self.driver.method_context(2), (0, "positioning"))
        self.assertEqual(self.driver.method_context(3), (0, "approach"))

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
            self.session.registers["WaitingForWayPoints"].value = False
            self.assertEqual(self.driver.method_status()["stage"], "contact")
            self.session.registers["WaitingForWayPoints"].value = True
            self.driver.read_samples()
            self.assertEqual(self.driver.method_status()["stage"], "it")
            self.assertEqual(self.driver.method_context(approach_end), (point, "approach"))
            self.assertEqual(self.driver.method_context(approach_end + 1), (point, "it:initial"))
            self.assertEqual(self.driver.method_context(approach_end + 2), (point, "it:pulse"))
            self.assertEqual(self.driver.method_context(approach_end + 4), (point, "retract"))
            retract = self.driver.positions_fifo.writes[-1][-14:]
            self.assertEqual(retract[8], position_to_raw(58 if point == 0 else params.start_z_um,
                                                       self.settings.z_range_um, self.settings.z_bipolar))

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
            Sample(0, 50, 50, 68, 0.1, 0, 9.0, 2.0, line_number=2),
        ])
        self.assertIn(-1, self.driver._method_contact_observed)

    def test_resume_does_not_override_an_fpga_feedback_pause(self) -> None:
        self.driver.pause()
        self.session.registers["Internal Pause"].value = True
        self.driver.resume()
        self.assertFalse(self.session.registers["External Pause"].value)
        self.assertTrue(self.session.registers["Internal Pause"].value)

    def _start_contact_case(self, name, mode="absolute"):
        """Start any contact-gated hardware method at its approach waypoint."""
        self.setUp()
        if name == "approach_cv":
            self.driver.start_approach_cv(ApproachCVParameters(feedback_mode=mode))
            status = self.driver.approach_cv_status
        elif name == "scan_cv":
            self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=1, y_points=1, feedback_mode=mode))
            status = self.driver.scan_hopping_cv_status
        else:
            params = {"approach": ApproachParameters(), "approach_it": ApproachITParameters(),
                      "scan_hopping_it": ScanHoppingITParameters(x_points=1, y_points=1)}[name]
            self.driver.start_method(name, replace(params, feedback_mode=mode))
            status = self.driver.method_status
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = False
        return status

    def test_bipolar_comparators_match_simulation_on_both_current_channels(self):
        from echemtips.experiments import contact_threshold_hit
        for channel in ("Current 1", "Current 2"):
            self.driver._configure_contact_feedback(channel, 2.0, True, "magnitude")
            regs = self.session.registers
            self.assertEqual(regs["FeedBackType"].value, FEEDBACK_SIGNAL_CODES[channel])
            self.assertEqual(regs["FeedBackType 2"].value, FEEDBACK_SIGNAL_CODES[channel])
            self.assertTrue(regs["GreaterThan"].value)
            self.assertFalse(regs["GreaterThan 2"].value)
            for current, expected in ((-3, True), (-1, False), (0, False), (1, False), (3, True)):
                raw = self.driver._feedback_value_to_raw(channel, current)
                hardware = raw >= regs["Feedback_Threshold"].value or raw <= regs["Feedback_Threshold 2"].value
                sample = Sample(0, 0, 0, 0, 0, 0, current if channel == "Current 1" else 0,
                                current if channel == "Current 2" else 0)
                self.assertEqual(hardware, expected)
                self.assertEqual(contact_threshold_hit(sample, channel, 2, True, "magnitude", None)[0], expected)
                self.assertEqual(self.driver._feedback_hit(current, 2, True, "magnitude"), expected)

    def test_bipolar_manual_and_limit_completion_restore_negative_threshold(self):
        for name in ("approach", "approach_it", "approach_cv", "scan_cv", "scan_hopping_it"):
            for manual in (False, True):
                with self.subTest(method=name, manual=manual):
                    status = self._start_contact_case(name, "magnitude")
                    d, regs = self.driver, self.session.registers
                    original = regs["Feedback_Threshold 2"].value
                    if manual:
                        d.accept_approach()
                    else:
                        regs["Applied Z"].value = d._program_waypoints[-1].z_position
                        d.service()
                    self.assertEqual(regs["Feedback_Threshold 2"].value, 32768)
                    regs["Feedback2 Boolean"].value = True
                    self.assertFalse(d._secondary_contact_confirmed("magnitude"))
                    regs["WaitingForWayPoints"].value = True
                    d.read_samples()
                    self.assertEqual(regs["Feedback_Threshold 2"].value, original)
                    result = status()
                    if manual:
                        self.assertNotEqual(result["stage"], "aborted")
                    else:
                        self.assertIn(result["stage"], ("aborted", "retracting"))

    def test_unused_comparator_cannot_pause_any_approach(self) -> None:
        # Target line type 1 wires Feedback1 OR Feedback2 to Internal Pause.
        # Emulate that OR, including positive/noisy/rail Current 2 values.
        for name in ("approach", "approach_it", "approach_cv", "scan_cv", "scan_hopping_it"):
            with self.subTest(method=name):
                status = self._start_contact_case(name)
                regs = self.session.registers
                for raw in (-32768, -1, 0, 1, 1000, 32767):
                    secondary_hit = raw >= regs["Feedback_Threshold 2"].value
                    regs["Feedback1 Boolean"].value = False
                    regs["Internal Pause"].value = secondary_hit
                    self.assertFalse(secondary_hit)
                    self.assertEqual(status()["stage"], "approaching")
                    self.assertFalse(regs["EndCurrentLine"].value)
                    self.assertEqual(len(self.driver.positions_fifo.writes), 1)

    def test_native_contact_scans_repeat_in_one_session_with_consumed_stop_latch(self) -> None:
        """Two 3x3 scans per mode; a consumed one-shot must never be needed."""
        for method in ("cv", "it"):
            for mode in ("absolute", "baseline_relative", "magnitude"):
                with self.subTest(method=method, mode=mode):
                    self.setUp()
                    d, regs = self.driver, self.session.registers
                    regs["OnlyStopLineONCE Z"] = _Register(True)
                    def forbid_end(value):
                        self.assertFalse(value, "Contact must not use the one-shot EndCurrentLine")
                        regs["EndCurrentLine"].value = value
                    regs["EndCurrentLine"].write = forbid_end
                    status = d.scan_hopping_cv_status if method == "cv" else d.method_status
                    def complete_program():
                        regs["LineNumber"].value = d._program_baseline + d._program_total
                        regs["WaitingForWayPoints"].value = True
                        frame = [0] * SAMPLE_WORDS
                        frame[9] = regs["LineNumber"].value
                        d.data_fifo.data.extend(frame)
                        d.read_samples()
                    for run in range(2):
                        if method == "cv":
                            d.start_scan_hopping_cv(ScanHoppingCVParameters(
                                x_points=3, y_points=3, cycles=1, feedback_mode=mode))
                        else:
                            d.start_method("scan_hopping_it", ScanHoppingITParameters(
                                x_points=3, y_points=3, feedback_mode=mode))
                        for point in range(9):
                            if mode == "baseline_relative":
                                complete_program()
                                self.assertEqual(status()["stage"], "approaching")
                            self.assertEqual(d._program_waypoints[-1].line_type, 2)
                            regs["LineNumber"].value = d._program_baseline + d._program_total
                            regs["Applied Z"].value = d._program_waypoints[-1].z_position - 200
                            regs["WaitingForWayPoints"].value = False
                            d.service()
                            # Type 2 completes on a brief feedback event. By the
                            # next host poll the primary comparator is false.
                            regs["Feedback1 Boolean"].value = False
                            regs["WaitingForWayPoints"].value = True
                            frame = [0] * SAMPLE_WORDS
                            frame[9] = regs["LineNumber"].value
                            d.data_fifo.data.extend(frame[:-1])
                            before = len(d.positions_fifo.writes)
                            d.read_samples()
                            status()
                            self.assertEqual(len(d.positions_fifo.writes), before)
                            d.data_fifo.data.append(frame[-1])
                            self.assertEqual(len(d.read_samples()), 1)
                            self.assertEqual(status()["stage"], "cv" if method == "cv" else "it")
                            expected = d._feedback_value_to_raw("Current 1", -0.005) if mode == "magnitude" else 32768
                            self.assertEqual(regs["Feedback_Threshold 2"].value, expected)
                            self.assertFalse(regs["External Stop"].value)
                            self.assertTrue(regs["OnlyStopLineONCE Z"].value)
                            complete_program()
                            update = status()
                        self.assertEqual(update["stage"], "complete")
                        d.ensure_idle()

    def test_no_contact_exit_and_manual_acceptance_restore_threshold_with_idle_indicator_latched(self) -> None:
        for name in ("approach", "approach_it", "approach_cv", "scan_cv", "scan_hopping_it"):
            for manual in (False, True):
                with self.subTest(method=name, manual=manual):
                    status = self._start_contact_case(name)
                    d, regs = self.driver, self.session.registers
                    if manual:
                        d.accept_approach()
                    else:
                        regs["Applied Z"].value = d._program_waypoints[-1].z_position
                        d.service()
                    self.assertEqual(regs["Feedback_Threshold 2"].value, -32769)
                    self.assertFalse(regs["EndCurrentLine"].value)
                    regs["Feedback2 Boolean"].value = True
                    regs["WaitingForWayPoints"].value = True
                    before = len(d.positions_fifo.writes)
                    d.read_samples()
                    self.assertEqual(len(d.positions_fifo.writes), before)
                    self.assertFalse(d._submitted)
                    self.assertEqual(regs["Feedback_Threshold 2"].value, 32768)
                    self.assertTrue(regs["Feedback2 Boolean"].value)
                    result = status()
                    if manual:
                        self.assertNotEqual(result["stage"], "aborted")
                    else:
                        self.assertIn(result["stage"], ("aborted", "retracting"))
                        self.assertNotIn(result["stage"], ("cv", "it", "contact"))

    def test_feedback_completion_timeout_and_failed_disarm_latch_safely(self) -> None:
        for failed_disarm in (False, True):
            with self.subTest(failed_disarm=failed_disarm):
                self._start_contact_case("scan_cv")
                d, regs = self.driver, self.session.registers
                d.accept_approach()
                regs["WaitingForWayPoints"].value = failed_disarm
                if failed_disarm:
                    regs["Feedback_Threshold 2"].write = lambda value: None
                with patch("echemtips.ni_driver.time.monotonic", return_value=d._contact_finish_deadline + 1):
                    with self.assertRaises(RuntimeError if failed_disarm else TimeoutError):
                        d.service()
                self.assertTrue(regs["External Pause"].value)
                self.assertTrue(d._stopped)
                self.assertEqual(len(d.positions_fifo.writes), 1)
                update = d.scan_hopping_cv_status()
                self.assertEqual(update["stage"], "aborted")
                self.assertNotIn("press Resume", update["detail"])

    def test_manual_acceptance_cannot_arm_secondary_after_completion(self) -> None:
        self._start_contact_case("scan_cv")
        self.session.registers["WaitingForWayPoints"].value = True
        with self.assertRaisesRegex(RuntimeError, "already completed"):
            self.driver.accept_approach()
        self.assertEqual(self.session.registers["Feedback_Threshold 2"].value, 32768)

    def test_contact_unpauses_only_after_end_request_and_drains_before_followup(self) -> None:
        for name in ("approach", "approach_it", "approach_cv", "scan_cv", "scan_hopping_it"):
            with self.subTest(method=name):
                status = self._start_contact_case(name)
                regs = self.session.registers
                regs["Feedback1 Boolean"].value = True
                regs["Internal Pause"].value = True
                original_write = regs["Internal Pause"].write
                def release(value):
                    # Model the paused axis loops: they cannot acknowledge an
                    # end request until the pause latch is explicitly released.
                    if not value:
                        self.assertFalse(regs["EndCurrentLine"].value)
                        self.assertEqual(regs["Feedback_Threshold 2"].value, -32769)
                        self.assertFalse(regs["External Pause"].value)
                        regs["WaitingForWayPoints"].value = True
                    original_write(value)
                with patch.object(regs["Internal Pause"], "write", side_effect=release):
                    self.assertEqual(status()["stage"], "contact")
                self.assertFalse(regs["Internal Pause"].value)
                self.assertFalse(regs["External Stop"].value)
                self.assertEqual(len(self.driver.positions_fifo.writes), 1)
                frame = [0] * SAMPLE_WORDS
                self.driver.data_fifo.data.extend(frame)
                self.assertEqual(len(self.driver.read_samples()), 1)
                self.assertFalse(regs["EndCurrentLine"].value)
                self.assertNotEqual(status()["stage"], "aborted")
                self.assertGreater(len(self.driver.positions_fifo.writes), 1)

    def test_external_and_feedback_pause_coexist_without_deadlock(self) -> None:
        for operator in (False, True):
            for name in ("approach", "approach_it", "approach_cv", "scan_cv", "scan_hopping_it"):
                with self.subTest(method=name, operator=operator):
                    status = self._start_contact_case(name)
                    regs = self.session.registers
                    regs["Feedback1 Boolean"].value = True
                    regs["Internal Pause"].value = True
                    if operator:
                        self.driver.pause()
                    else:
                        regs["External Pause"].value = True
                    update = status()
                    self.assertIn("press Resume", update["detail"])
                    self.assertFalse(regs["EndCurrentLine"].value)
                    self.assertTrue(regs["Internal Pause"].value)
                    regs["Feedback1 Boolean"].value = False  # Event must remain latched.
                    self.driver.resume()
                    self.assertTrue(regs["Internal Pause"].value)
                    self.assertEqual(status()["stage"], "contact")
                    self.assertFalse(regs["Internal Pause"].value)

    def test_unconfirmed_pause_waits_for_evidence_then_fails_closed(self) -> None:
        for name in ("approach", "approach_it", "approach_cv", "scan_cv", "scan_hopping_it"):
            with self.subTest(method=name):
                status = self._start_contact_case(name)
                regs = self.session.registers
                regs["Internal Pause"].value = True
                regs["StopMoveZ"].value = True  # Movement completion is NOT contact.
                with patch("echemtips.ni_driver.time.monotonic", return_value=10.0):
                    self.assertIn("checking contact evidence", status()["detail"])
                self.assertTrue(regs["Internal Pause"].value)
                self.assertFalse(regs["EndCurrentLine"].value)
                with patch("echemtips.ni_driver.time.monotonic", return_value=100.0):
                    result = status()
                self.assertEqual(result["stage"], "aborted")
                self.assertIn("Feedback1 Boolean=", result["detail"])
                self.assertTrue(regs["External Pause"].value)
                self.assertFalse(regs["External Stop"].value)
                self.assertEqual(len(self.driver.positions_fifo.writes), 1)
                with self.assertRaisesRegex(RuntimeError, "latched"):
                    self.driver.resume()
                with self.assertRaisesRegex(RuntimeError, "latched"):
                    self.driver.end_current_waypoint()
                self.assertTrue(regs["External Pause"].value)

    def test_end_acknowledgement_deadline_excludes_operator_pause(self) -> None:
        self._start_contact_case("approach_cv")
        regs = self.session.registers
        with patch("echemtips.ni_driver.time.monotonic", return_value=0.0):
            self.driver.accept_approach()
            self.driver.pause()
        with patch("echemtips.ni_driver.time.monotonic", return_value=100.0):
            self.driver.service()
            self.driver.resume()
            self.driver.service()
        self.assertFalse(self.driver._stopped)
        self.assertEqual(regs["Feedback_Threshold 2"].value, -32769)
        regs["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertFalse(regs["EndCurrentLine"].value)

    def test_delayed_contact_sample_is_recorded_exactly_once(self) -> None:
        status = self._start_contact_case("approach_cv")
        regs = self.session.registers
        regs["Internal Pause"].value = True
        self.assertIn("checking contact evidence", status()["detail"])
        # Sample layout: line tag at 9, current 1 at 5. Comparator can already
        # be false again by the time this buffered threshold crossing arrives.
        frame = [0] * SAMPLE_WORDS
        frame[9] = regs["LineNumber"].value
        frame[5] = current_to_raw(3.0, self.settings.current1_v_per_na)
        self.driver.data_fifo.data.extend(frame)
        self.assertEqual(status()["stage"], "contact")
        self.assertEqual(len(self.driver.read_samples()), 1)
        self.assertEqual(self.driver.read_samples(), [])

    def test_preposition_pause_is_never_accepted_as_contact(self) -> None:
        status = self._start_contact_case("approach_cv")
        regs = self.session.registers
        regs["LineNumber"].value = self.driver._program_baseline + 1
        regs["Internal Pause"].value = True
        regs["Feedback1 Boolean"].value = True
        self.assertIn("checking contact evidence", status()["detail"])
        self.assertFalse(regs["EndCurrentLine"].value)

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
        self.assertEqual(approach[0], 2)
        self.assertTrue(approach[13] & (1 << 2))
        self.assertEqual(self.session.registers["FeedBackType"].value, 1)
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "contact")
        self.assertFalse(self.session.registers["EndCurrentLine"].value)
        self.assertFalse(self.session.registers["External Stop"].value)
        self.assertEqual(len(writes), before + 1)
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "contact")
        self.assertEqual(len(writes), before + 2)
        self.assertEqual(len(writes[-1]), (1 + 3 * 2 + 1) * 14)

    def test_baseline_relative_contact_is_translated_to_verified_absolute_feedback(self) -> None:
        params = ApproachCVParameters(
            cycles=1, retract_after=False, feedback_mode="baseline_relative",
            feedback_threshold_na=0.5, settling_time_s=0.07,
        )
        self.driver.start_approach_cv(params)
        baseline_hold = self.driver.positions_fifo.writes[-1][14:28]
        self.assertEqual(baseline_hold[0], 0)
        self.assertTrue(baseline_hold[13] & (1 << 5))
        self.assertEqual(baseline_hold[12], self.driver.BASELINE_HOLD_US)
        self.driver._observe_contacts([
            Sample(0, 50, 50, 10, 0.1, 0, 1.0, 0, line_number=2),
            Sample(0.01, 50, 50, 10, 0.1, 0, 1.2, 0, line_number=2),
        ])
        self.session.registers["LineNumber"].value = 2
        self.session.registers["External Pause"].value = False
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "approaching")
        approach = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(approach), 14)
        self.assertEqual(approach[0], 2)
        self.assertEqual(self.session.registers["Feedback_Threshold"].value, current_to_raw(1.6, 1.0))
        self.assertEqual(self.session.registers["P2AvgWhole"].value, 1)
        self.assertEqual(self.session.registers["P2AvgMinus"].value, 0)
        self.assertFalse(self.session.registers["Feedback1 on  Hold"].value)

        self.session.registers["LineNumber"].value = 3
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        self.assertEqual(self.driver.approach_cv_status()["stage"], "contact")
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.approach_cv_status()["stage"], "contact")
        contexts = self.driver._approach_history[-1][1]
        self.assertEqual(contexts[:3], ["settling", "settling", "settling"])
        self.assertEqual(contexts[3:], ["cv", "cv", "cv", "cv"])

    def test_shared_approach_uses_stationary_baseline_then_type_two_feedback(self) -> None:
        params = ApproachParameters(
            feedback_mode="baseline_relative", feedback_threshold=0.4, retract_after=False,
        )
        self.driver.start_method("approach", params)
        initial = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(initial), 2 * 14)
        self.assertEqual(initial[14], 0)
        self.assertTrue(initial[27] & (1 << 5))
        self.driver._observe_contacts([
            Sample(0, 50, 50, 10, 0.1, 0, 2.0, 0, line_number=2),
            Sample(0.01, 50, 50, 10, 0.1, 0, 2.2, 0, line_number=2),
        ])
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = 2
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.method_status()["stage"], "approaching")
        self.assertEqual(self.driver.positions_fifo.writes[-1][0], 2)
        self.assertEqual(self.session.registers["Feedback_Threshold"].value, current_to_raw(2.5, 1.0))
        self.assertEqual(self.driver.method_context(3), (-1, "approach"))

    def test_hopping_cv_remeasures_baseline_before_each_contact_approach(self) -> None:
        params = ScanHoppingCVParameters(
            x_points=1, y_points=1, feedback_mode="baseline_relative", feedback_threshold_na=0.25,
        )
        self.driver.start_scan_hopping_cv(params)
        initial = self.driver.positions_fifo.writes[-1]
        self.assertEqual(len(initial), 3 * 14)
        self.assertEqual(initial[28], 0)
        self.assertTrue(initial[41] & (1 << 5))
        self.driver._observe_contacts([
            Sample(0, 50, 50, 55, 0.1, 0, 0.8, 0, line_number=3),
            Sample(0.01, 50, 50, 55, 0.1, 0, 1.0, 0, line_number=3),
        ])
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = 3
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.scan_hopping_cv_status()
        self.assertEqual(status["stage"], "approaching")
        self.assertEqual(self.driver.positions_fifo.writes[-1][0], 2)
        self.assertEqual(self.session.registers["Feedback_Threshold"].value, current_to_raw(1.15, 1.0))
        self.assertEqual(self.driver.scan_context(4), (0, "approach"))

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
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.driver.accept_approach()
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 2
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        status = self.driver.approach_cv_status()
        self.assertEqual(status["stage"], "contact")
        self.assertIn("Operator accepted", status["detail"])
        self.assertEqual(len(writes), before + 1)

    def test_operator_cannot_accept_contact_during_approach_cv_preposition(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1

        self.assertEqual(self.driver.approach_cv_status()["stage"], "preposition")
        with self.assertRaisesRegex(RuntimeError, "still positioning"):
            self.driver.accept_approach()

    def test_operator_cannot_accept_contact_during_scan_positioning(self) -> None:
        self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=1, y_points=1, cycles=1))
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1

        with self.assertRaisesRegex(RuntimeError, "still positioning"):
            self.driver.accept_approach()

    def test_operator_cannot_accept_contact_during_shared_method_preposition(self) -> None:
        self.driver.start_method("approach", ApproachParameters())
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1

        self.assertEqual(self.driver.method_status()["stage"], "preposition")
        with self.assertRaisesRegex(RuntimeError, "still positioning"):
            self.driver.accept_approach()

    def test_operator_can_accept_hardware_scan_approaches(self) -> None:
        self.driver.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=1, y_points=1, cycles=1))
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
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
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = False
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
        self.session.registers["External Pause"].value = False
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
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
        self.session.registers["Applied Z"].value = self.driver._program_waypoints[-1].z_position
        self.driver.service()
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
        self.assertEqual(self.driver.approach_context(40), "")
        self.assertEqual(self.driver.approach_context(41), "preposition")
        self.assertEqual(self.driver.approach_context(42), "approach")
        self.session.registers["LineNumber"].value = 42
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        self.driver.approach_cv_status()
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.driver.approach_cv_status()
        self.assertEqual(self.driver.approach_context(43), "cv")
        self.assertEqual(self.driver.approach_context(46), "cv")
        self.assertEqual(self.driver.approach_context(47), "retract")
        self.assertEqual(self.driver.approach_context(48), "")

    def test_final_scan_return_keeps_xy_and_waits_for_framed_completion(self) -> None:
        for method in ("cv", "it"):
            for start, end, contact, expected in ((0, 90, 68, 0), (10, 90, 8, 8), (90, 0, 30, 90)):
                with self.subTest(method=method, start=start, contact=contact):
                    self.setUp()
                    d, regs = self.driver, self.session.registers
                    cls = ScanHoppingCVParameters if method == "cv" else ScanHoppingITParameters
                    p = cls(start_z_um=start, end_z_um=end, x_points=1, y_points=1)
                    if method == "cv":
                        d.start_scan_hopping_cv(p)
                    else:
                        d.start_method("scan_hopping_it", p)
                    status = d.scan_hopping_cv_status if method == "cv" else d.method_status
                    regs["Applied Z"].value = position_to_raw(contact, 100, False)
                    # Final return must use applied Z even when no sensor
                    # sample arrived, or a stale sample reports another Z.
                    if method == "cv": d._scan_last_approach_z[0] = end
                    else: d._method_last_approach_z[0] = end
                    regs["LineNumber"].value = d._program_baseline + d._program_total
                    regs["WaitingForWayPoints"].value = True
                    d.read_samples()
                    self.assertEqual(status()["stage"], method)
                    wp = d._program_waypoints[-1]
                    self.assertEqual(wp.z_position, position_to_raw(expected, 100, False))
                    self.assertEqual(wp.x_position, regs["Applied X"].value)
                    self.assertEqual(wp.y_position, regs["Applied Y"].value)
                    self.assertNotEqual(status()["stage"], "complete")
                    regs["LineNumber"].value = d._program_baseline + d._program_total
                    frame = [0] * SAMPLE_WORDS
                    frame[9] = regs["LineNumber"].value
                    d.data_fifo.data.extend(frame[:-1])
                    d.read_samples()
                    self.assertNotEqual(status()["stage"], "complete")
                    d.data_fifo.data.append(frame[-1])
                    d.read_samples()
                    self.assertEqual(status()["stage"], "complete")

    def test_scan_bounded_retract_and_no_travel_interlock(self) -> None:
        for method in ("cv", "it"):
            for contact in (0.0, 8.0, 50.0):
                with self.subTest(method=method, contact=contact):
                    self.setUp()
                    d, regs = self.driver, self.session.registers
                    cls = ScanHoppingCVParameters if method == "cv" else ScanHoppingITParameters
                    p = cls(start_z_um=0, end_z_um=90, x_points=2, y_points=1)
                    if method == "cv":
                        d.start_scan_hopping_cv(p)
                    else:
                        d.start_method("scan_hopping_it", p)
                    status = d.scan_hopping_cv_status if method == "cv" else d.method_status
                    def finish():
                        regs["LineNumber"].value = d._program_baseline + d._program_total
                        regs["WaitingForWayPoints"].value = True
                        frame = [0] * SAMPLE_WORDS
                        frame[9] = regs["LineNumber"].value
                        d.data_fifo.data.extend(frame)
                        d.read_samples()
                    regs["Applied Z"].value = position_to_raw(contact, 100, False)
                    finish()
                    self.assertEqual(status()["stage"], method)
                    target = d._program_waypoints[-1].z_position
                    self.assertEqual(target, position_to_raw(max(0, contact - 10), 100, False))
                    regs["Applied Z"].value = target
                    finish()
                    writes = len(d.positions_fifo.writes)
                    result = status()
                    if contact == 0:
                        self.assertEqual(result["stage"], "aborted")
                        self.assertIn("no Z retraction", result["detail"])
                        status()
                        self.assertEqual(len(d.positions_fifo.writes), writes)
                        self.assertFalse(regs["External Stop"].value)
                    else:
                        self.assertNotEqual(result["stage"], "aborted")
                        self.assertEqual(len(d.positions_fifo.writes), writes + 1)
                    self.assertEqual(bool(p.retraction_events), contact < 10)

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
        self.assertEqual(frames[2][0], 2)
        self.assertTrue(frames[2][13] & (1 << 2))
        self.assertEqual(self.driver.scan_context(0), (-1, ""))
        self.assertEqual(self.driver.scan_context(1), (0, "retract"))
        self.assertEqual(self.driver.scan_context(3), (0, "approach"))
        self.session.registers["LineNumber"].value = 3
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        self.assertEqual(self.driver.scan_hopping_cv_status()["stage"], "contact")
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.scan_hopping_cv_status()["stage"], "cv")
        cv_words = self.session.fifos["Host_To_FPGA_Positions"].writes[-1]
        self.assertEqual(len(cv_words), 5 * 14)
        self.assertEqual(self.driver.scan_context(4), (0, "cv"))
        self.assertEqual(self.driver.scan_context(8), (0, "retract"))

    def test_fast_program_completion_requires_counter_and_final_drain(self) -> None:
        self.driver.start_approach_cv(ApproachCVParameters(cycles=1))
        self.session.registers["External Pause"].value = False
        # Contact pauses the FPGA. Only then is the CV/retract follow-up queued.
        self.session.registers["LineNumber"].value = 2
        self.session.registers["Feedback1 Boolean"].value = True
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        self.assertEqual(self.driver.approach_cv_status()["stage"], "contact")
        self.assertNotEqual(self.driver.approach_cv_status()["stage"], "complete")
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.approach_cv_status()["stage"], "contact")
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["Internal Pause"].value = False
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
        self.assertEqual(self.driver.scan_context(23), (0, "approach"))
        self.session.registers["LineNumber"].value = baseline + 3
        self.session.registers["Internal Pause"].value = True
        self.session.registers["WaitingForWayPoints"].value = False
        self.driver.scan_hopping_cv_status()
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.driver.scan_hopping_cv_status()
        self.session.registers["LineNumber"].value = self.driver._program_baseline + self.driver._program_total
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.read_samples()
        self.assertEqual(self.driver.scan_hopping_cv_status()["stage"], "complete")
        self.assertEqual(self.driver.scan_context(23), (0, "approach"))

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

    def test_cancel_preserves_complete_samples_and_retires_uncertain_frame(self) -> None:
        def forbidden_reset():
            self.fail("Stop must not reset analog outputs")
        self.session.reset = forbidden_reset
        self.driver.move("Z", 20, 1)
        valid_frame = [0] * SAMPLE_WORDS
        valid_frame[9] = 1
        self.driver.data_fifo.data = valid_frame + [101, 102, 103, 104, 105]
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.stop_motion()
        self.assertTrue(self.session.registers["External Pause"].value)
        self.assertTrue(self.session.registers["External Stop"].value)
        self.assertEqual(self.driver.data_fifo.data, [])
        retained = self.driver.read_samples()
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].line_number, 1)
        self.assertEqual(self.driver.read_samples(), [])
        with self.assertRaisesRegex(ValueError, "Reinitialize"):
            self.driver.move("Z", 30, 1)

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

    def test_acknowledged_operator_pause_is_excluded_from_watchdog_time(self) -> None:
        self.driver.move("Z", 20, 1)
        self.driver._program_deadline = 10.0
        with patch("echemtips.ni_driver.time.monotonic", return_value=5.0):
            self.driver.pause()
        with patch("echemtips.ni_driver.time.monotonic", return_value=20.0):
            self.driver.service()
        self.assertEqual(self.driver._program_deadline, 10.0)
        with patch("echemtips.ni_driver.time.monotonic", return_value=25.0):
            self.driver.resume()
        self.assertEqual(self.driver._program_deadline, 30.0)
        with patch("echemtips.ni_driver.time.monotonic", return_value=29.0):
            self.driver.service()
        self.assertFalse(self.driver._stopped)

    def test_internal_feedback_pause_is_excluded_from_watchdog_time(self) -> None:
        self.driver.move("Z", 20, 1)
        self.driver._program_deadline = 10.0
        self.session.registers["Internal Pause"].value = True
        with patch("echemtips.ni_driver.time.monotonic", return_value=5.0):
            self.driver.service()
        with patch("echemtips.ni_driver.time.monotonic", return_value=20.0):
            self.driver.service()
        self.session.registers["Internal Pause"].value = False
        with patch("echemtips.ni_driver.time.monotonic", return_value=25.0):
            self.driver.service()
        self.assertEqual(self.driver._program_deadline, 30.0)
        self.assertFalse(self.driver._stopped)

    def test_end_waypoint_missing_acknowledgement_latches_driver(self) -> None:
        self.driver.move("Z", 20, 1)
        self.session.registers["LineNumber"].value = self.driver._program_baseline + 1
        with patch("echemtips.ni_driver.time.monotonic", return_value=0.0):
            self.driver.end_current_waypoint()
        with patch("echemtips.ni_driver.time.monotonic", return_value=10.0):
            with self.assertRaisesRegex(TimeoutError, "did not acknowledge EndCurrentLine"):
                self.driver.service()
        self.assertTrue(self.session.registers["External Pause"].value)
        self.assertFalse(self.session.registers["EndCurrentLine"].value)
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.driver.ensure_idle()

    def test_ready_handshake_accepts_waiting_target(self) -> None:
        self.session.registers["WaitingForWayPoints"].value = True
        self.driver.wait_until_ready()

    def test_startup_state_matches_deployed_fpga_outputs(self) -> None:
        for name, value in DEPLOYED_STARTUP_RAW_OUTPUTS.items():
            self.session.registers[name].value = value
        self.session.registers["LineNumber"].value = 0
        self.session.registers["External Pause"].value = True
        self.session.registers["External Stop"].value = False
        self.session.registers["Internal Pause"].value = False
        self.session.registers["Internal Stop"].value = False
        self.session.registers["EndCurrentLine"].value = False
        self.session.registers["WaitingForWayPoints"].value = True

        observed = self.driver.verify_startup_state()

        self.assertEqual(observed["Applied X"], 0x3FFF)
        self.assertEqual(observed["Applied Y"], 0x3FFF)
        self.assertEqual(observed["Applied Z"], 0)

    def test_unexpected_startup_output_is_stopped_and_rejected(self) -> None:
        for name, value in DEPLOYED_STARTUP_RAW_OUTPUTS.items():
            self.session.registers[name].value = value
        self.session.registers["Applied X"].value = 0
        self.session.registers["LineNumber"].value = 0
        self.session.registers["External Pause"].value = True
        self.session.registers["External Stop"].value = False
        self.session.registers["Internal Pause"].value = False
        self.session.registers["Internal Stop"].value = False
        self.session.registers["EndCurrentLine"].value = False
        self.session.registers["WaitingForWayPoints"].value = True

        with self.assertRaisesRegex(RuntimeError, "Unexpected FPGA startup state"):
            self.driver.verify_startup_state()

        self.assertTrue(self.session.registers["External Pause"].value)
        self.assertTrue(self.session.registers["External Stop"].value)

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
