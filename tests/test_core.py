from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from echemtips.backends import BackendError, HardwareSequenceUpdate, NIFPGABackend, SafetyError, SimulationBackend
from echemtips.data import DataRecorder
from echemtips.experiments import (
    ApproachCVExperiment, ApproachExperiment, ApproachITExperiment, CVExperiment,
    ExperimentState, ScanHoppingCVExperiment, ScanHoppingITExperiment, contact_threshold_hit,
)
from echemtips.models import (
    DEFAULT_BITFILE, AppSettings, ApproachCVParameters, ApproachITParameters,
    ApproachParameters, CVParameters, Sample, ScanHoppingCVParameters,
    ScanHoppingITParameters, SettingsStore, default_settings_path,
)


class SettingsTests(unittest.TestCase):
    def test_default_settings_are_valid(self) -> None:
        settings = AppSettings()
        self.assertEqual(settings.validate(), [])
        self.assertAlmostEqual(settings.effective_period_s, 0.001028)

    def test_default_settings_path_is_user_level_and_absolute(self) -> None:
        path = default_settings_path()
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.name, "settings.json")
        self.assertEqual(path.parent.name, "eChemTips")

    def test_samples_per_point_must_be_power_of_two(self) -> None:
        settings = AppSettings(samples_per_point=250)
        self.assertTrue(any("power of two" in error for error in settings.validate()))

    def test_nonfinite_hardware_range_is_rejected(self) -> None:
        settings = AppSettings(mode="NI FPGA", z_range_um=float("nan"))
        self.assertTrue(any("Z range" in error for error in settings.validate()))

    def test_hardware_feedback_threshold_must_fit_adc(self) -> None:
        settings = AppSettings(mode="NI FPGA", current1_v_per_na=2.0)
        approach_errors = ApproachCVParameters(feedback_threshold_na=6.0).validate(settings)
        scan_errors = ScanHoppingCVParameters(feedback_threshold_na=6.0).validate(settings)
        self.assertTrue(any("ADC range" in error for error in approach_errors))
        self.assertTrue(any("ADC range" in error for error in scan_errors))

    def test_contact_mode_and_settling_time_are_validated(self) -> None:
        self.assertTrue(any("Contact criterion" in error for error in ApproachParameters(feedback_mode="unknown").validate(AppSettings())))
        self.assertTrue(any("Settling time" in error for error in ApproachParameters(settling_time_s=-0.1).validate(AppSettings())))
        self.assertEqual(ApproachParameters(feedback_mode="baseline_relative", settling_time_s=0).validate(AppSettings()), [])

    def test_baseline_relative_contact_uses_delta_current(self) -> None:
        first = Sample(0, 0, 0, 0, 0, 0, 5.0, 0)
        hit, baseline = contact_threshold_hit(first, "Current 1", 0.5, True, "baseline_relative", None)
        self.assertFalse(hit)
        second = Sample(1, 0, 0, 0, 0, 0, 5.6, 0)
        hit, _ = contact_threshold_hit(second, "Current 1", 0.5, True, "baseline_relative", baseline)
        self.assertTrue(hit)

    def test_settings_round_trip(self) -> None:
        with TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "settings.json")
            expected = AppSettings(
                z_range_um=38.0,
                mode="NI FPGA",
                current2_v_per_na=2.5,
            )
            store.save(expected)
            actual = store.load()
            self.assertEqual(actual.z_range_um, 38.0)
            self.assertEqual(actual.mode, "NI FPGA")
            self.assertEqual(actual.current2_v_per_na, 2.5)

    def test_legacy_default_bitfile_is_migrated_to_usb_target(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({
                "bitfile": "FPGA Bitfiles/FPGAProject_FPGATarget_FPGATarget2_ACEEEF6E.lvbitx",
                "hardware_transport": "PCIe/PXI R Series",
            }), encoding="utf-8")
            settings = SettingsStore(path).load()
            self.assertEqual(settings.bitfile, DEFAULT_BITFILE)
            self.assertEqual(settings.hardware_transport, "USB R Series")


class SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AppSettings()
        self.backend = SimulationBackend(self.settings, seed=1)
        self.backend.connect()

    def tearDown(self) -> None:
        self.backend.disconnect()

    def test_move_reaches_target(self) -> None:
        self.backend.move("Z", 12.0, 4.0)
        self.backend._last_tick -= 1.0
        sample = self.backend.read_sample()
        self.assertAlmostEqual(sample.z_um, 11.0, places=3)  # tick is capped at 250 ms
        for _ in range(4):
            self.backend._last_tick -= 0.25
            sample = self.backend.read_sample()
        self.assertAlmostEqual(sample.z_um, 12.0, places=3)

    def test_output_limits_are_enforced(self) -> None:
        with self.assertRaises(SafetyError):
            self.backend.move("Z", 101.0, 1.0)
        with self.assertRaises(SafetyError):
            self.backend.set_voltage(1, 10.1)

    def test_nonfinite_speed_is_rejected(self) -> None:
        with self.assertRaises(SafetyError):
            self.backend.move("Z", 20.0, float("nan"))

    def test_simulation_contact_signal_increases_near_surface(self) -> None:
        self.backend._positions["Z"] = 20.0
        far = self.backend.read_sample().current1_na
        self.backend._positions["Z"] = 90.0
        near = self.backend.read_sample().current1_na
        self.assertGreater(near, far + 1.0)


class ConversionTests(unittest.TestCase):
    def test_fpga_raw_scaling(self) -> None:
        backend = NIFPGABackend(AppSettings(z_range_um=100.0, current2_v_per_na=2.0))
        self.assertAlmostEqual(backend._raw_to_voltage(32767), 10.0, places=3)
        self.assertAlmostEqual(backend._raw_to_position(16384, "Z"), 50.0, places=2)
        self.assertAlmostEqual(backend._raw_to_current(32767, 1), 10.0, places=3)
        self.assertAlmostEqual(backend._raw_to_current(32767, 2), 5.0, places=3)


class NIBackendSafetyTests(unittest.TestCase):
    def test_invalid_settings_are_rejected_before_driver_import(self) -> None:
        backend = NIFPGABackend(AppSettings(mode="NI FPGA", z_range_um=float("nan")))
        with self.assertRaisesRegex(BackendError, "Invalid instrument settings"):
            backend.connect()

    def test_fpga_connection_requires_explicit_startup_actuation_permission(self) -> None:
        backend = NIFPGABackend(AppSettings(mode="NI FPGA"))
        self.assertIn("AO0/X", backend.startup_notice)
        self.assertIn("+5 V", backend.startup_notice)
        self.assertIn("X 50.0 µm", backend.startup_notice)
        with self.assertRaisesRegex(BackendError, "changes X/Y/Z and E1/E2 outputs"):
            backend.connect()

    def test_authorized_fpga_connection_requires_and_records_startup_verification(self) -> None:
        class Register:
            def __init__(self) -> None:
                self.value = False

            def write(self, value: object) -> None:
                self.value = value

        class Session:
            def __init__(self, _bitfile: str, _resource: str, *, no_run: bool) -> None:
                self.no_run = no_run
                self.fpga_vi_state = SimpleNamespace(name="NotRunning")
                self.registers = {"External Stop": Register(), "External Pause": Register()}
                self.ran = False
                self.closed = False

            def run(self) -> None:
                self.ran = True

            def close(self) -> None:
                self.closed = True

        class Driver:
            def __init__(self) -> None:
                self.ready = False
                self.verified = False

            def wait_until_ready(self) -> None:
                self.ready = True

            def verify_startup_state(self) -> None:
                self.verified = True

        with TemporaryDirectory() as folder:
            bitfile = Path(folder) / "target.lvbitx"
            bitfile.touch()
            settings = AppSettings(mode="NI FPGA", bitfile=str(bitfile))
            driver = Driver()
            module = SimpleNamespace(create_driver=lambda _session, _settings: driver)
            sessions: list[Session] = []

            def open_session(*args: object, **kwargs: object) -> Session:
                session = Session(*args, **kwargs)
                sessions.append(session)
                return session

            with (
                patch.dict(sys.modules, {"nifpga": SimpleNamespace(Session=open_session), "test_startup_driver": module}),
                patch("echemtips.backends.inspect_bitfile", return_value=object()),
                patch("echemtips.backends.validate_wec_bitfile", return_value=[]),
            ):
                backend = NIFPGABackend(settings, driver_module="test_startup_driver")
                backend.connect(allow_startup_actuation=True)

            self.assertTrue(sessions[0].no_run)
            self.assertTrue(sessions[0].ran)
            self.assertTrue(driver.ready)
            self.assertTrue(driver.verified)
            self.assertTrue(backend.startup_verified)
            self.assertTrue(backend.connected)

    def test_emergency_stop_uses_latching_driver_operation(self) -> None:
        calls: list[str] = []
        backend = NIFPGABackend(AppSettings(mode="NI FPGA"))
        backend.connected = True
        backend._driver = type("Driver", (), {"emergency_stop": lambda self: calls.append("stop")})()
        backend.emergency_stop()
        self.assertEqual(calls, ["stop"])

    def test_potential_commands_delegate_to_acknowledging_driver(self) -> None:
        calls: list[tuple[str, int, float]] = []

        class Driver:
            def set_voltage(self, channel: int, voltage: float) -> None:
                calls.append(("idle", channel, voltage))

            def set_live_potential(self, channel: int, voltage: float) -> None:
                calls.append(("live", channel, voltage))

        backend = NIFPGABackend(AppSettings(mode="NI FPGA"))
        backend.connected = True
        backend._driver = Driver()
        backend.set_voltage(1, 0.2)
        backend.set_live_potential(2, -0.3)
        self.assertEqual(calls, [("idle", 1, 0.2), ("live", 2, -0.3)])

    def test_approach_cv_accepts_the_driver_settling_stage(self) -> None:
        backend = NIFPGABackend(AppSettings(mode="NI FPGA"))
        backend.connected = True
        backend._driver = SimpleNamespace(
            start_approach_cv=lambda _params: None,
            stop_motion=lambda: None,
            approach_cv_status=lambda: {
                "stage": "settling",
                "detail": "Holding contact before CV",
                "progress": 0.45,
            },
        )

        update = backend.hardware_approach_cv_status()

        self.assertEqual(update.stage, "settling")
        self.assertEqual(update.detail, "Holding contact before CV")


class DataTests(unittest.TestCase):
    def test_csv_and_metadata_are_written(self) -> None:
        sample = Sample(0.1, 1, 2, 3, 0.1, 0.0, 1.2, 0.2)
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            recorder = DataRecorder()
            recorder.start("Watch Current")
            recorder.append(sample)
            output = recorder.finish(settings, ApproachCVParameters())
            self.assertIsNotNone(output)
            assert output is not None
            self.assertTrue(output.exists())
            metadata = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["sample_count"], 1)
            self.assertEqual(metadata["experiment"], "Watch Current")


class ExperimentTests(unittest.TestCase):
    def test_standalone_cv_simulation_completes(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = CVExperiment(backend, settings)
        experiment.start(CVParameters(scan_rate_v_s=100, cycles=1, jump_at_start=False))
        for _ in range(8):
            experiment._last_tick -= 1
            experiment.tick_samples([backend.read_sample()])
            if experiment.state == ExperimentState.COMPLETE:
                break
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)

    def test_standalone_approach_detects_contact_and_can_remain_at_surface(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ApproachExperiment(backend, settings)
        params = ApproachParameters(retract_after=False)
        experiment.start(params)
        experiment.tick_samples([Sample(0, 50, 50, params.start_z_um, .1, 0, 0, 0)])
        experiment.tick_samples([Sample(1, 50, 50, 68, .1, 0, 3, 0)])
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        self.assertEqual(experiment.contact_z, 68)

    def test_standalone_approach_can_use_current_2_feedback(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ApproachExperiment(backend, settings)
        params = ApproachParameters(feedback_channel="Current 2", feedback_threshold=2.0, retract_after=False)
        experiment.start(params)
        experiment.tick_samples([Sample(0, 50, 50, params.start_z_um, .1, 0, 5, 0)])
        experiment.tick_samples([Sample(1, 50, 50, 68, .1, 0, 5, 3)])
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        self.assertEqual(experiment.contact_z, 68)

    def test_approach_it_simulation_runs_steps_only_after_contact(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ApproachITExperiment(backend, settings)
        params = ApproachITParameters(initial_hold_s=.01, step_hold_s=.01, return_hold_s=.01)
        experiment.start(params)
        start = Sample(0, 50, 50, params.start_z_um, .1, 0, 0, 0)
        contact = Sample(1, 50, 50, 68, .1, 0, 3, 0)
        experiment.tick_samples([start])
        self.assertEqual(experiment.state, ExperimentState.APPROACHING)
        experiment.tick_samples([contact])
        self.assertEqual(experiment.state, ExperimentState.IT)
        for _ in range(3):
            experiment._step_deadline = 0
            experiment.tick_samples([contact])
        self.assertEqual(experiment.state, ExperimentState.RETRACTING)
        experiment.tick_samples([start])
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)

    def test_scan_hopping_it_simulation_completes_contact_and_current_maps(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings, seed=4)
        backend.connect()
        experiment = ScanHoppingITExperiment(backend, settings)
        params = ScanHoppingITParameters(
            x_start_um=50, x_end_um=50, x_points=1, y_start_um=50, y_end_um=50, y_points=1,
            start_z_um=55, end_z_um=80, lateral_rate_um_s=100,
            approach_rate_um_s=20, retract_rate_um_s=100,
            initial_hold_s=.01, step_hold_s=.01, return_hold_s=.01,
        )
        experiment.start(params)
        for _ in range(200):
            backend._last_tick -= .25
            if experiment.state == ExperimentState.IT:
                experiment._step_deadline = 0
            experiment.tick_samples([backend.read_sample()])
            if experiment.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
                break
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        self.assertIn((0, 0), experiment.contact_z)
        self.assertIn((0, 0), experiment.current_at_pulse)

    def test_approach_cv_runs_through_all_stages(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ApproachCVExperiment(backend, settings)
        params = ApproachCVParameters(
            start_z_um=10,
            end_z_um=90,
            approach_rate_um_s=3,
            feedback_threshold_na=2,
            cv_start_v=0,
            cv_vertex1_v=0.5,
            cv_vertex2_v=-0.5,
            cv_scan_rate_v_s=100,
            cycles=1,
            retract_after=True,
        )
        experiment.start(params)
        at_start = Sample(0, 50, 50, 10, 0, 0, 0, 0)
        experiment.tick(at_start)
        self.assertEqual(experiment.state, ExperimentState.APPROACHING)
        contact = Sample(1, 50, 50, 68, 0, 0, 3, 0)
        experiment.tick(contact)
        self.assertEqual(experiment.state, ExperimentState.CV)
        for _ in range(3):
            experiment._last_tick -= 1
            experiment.tick(contact)
        self.assertEqual(experiment.state, ExperimentState.RETRACTING)
        experiment.tick(at_start)
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        self.assertEqual(experiment.progress, 1.0)
        backend.disconnect()

    def test_approach_cv_waits_for_optional_xy_preposition(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ApproachCVExperiment(backend, settings)
        params = ApproachCVParameters(x_um=25, y_um=35, start_z_um=10)
        experiment.start(params)
        experiment.tick(Sample(0, 20, 35, 10, 0.1, 0, 0, 0))
        self.assertEqual(experiment.state, ExperimentState.PREPOSITION)
        experiment.tick(Sample(1, 25, 35, 10, 0.1, 0, 0, 0))
        self.assertEqual(experiment.state, ExperimentState.APPROACHING)
        backend.disconnect()

    def test_operator_can_accept_current_z_during_simulated_approaches(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        sample = Sample(1, 50, 50, 42, 0.1, 0, 0, 0)

        approach_cv = ApproachCVExperiment(backend, settings)
        approach_cv.start(ApproachCVParameters())
        approach_cv.state = ExperimentState.APPROACHING
        approach_cv.accept_approach(sample)
        self.assertEqual(approach_cv.state, ExperimentState.CV)

        approach_it = ApproachITExperiment(backend, settings)
        approach_it.start(ApproachITParameters())
        approach_it.state = ExperimentState.APPROACHING
        approach_it.accept_approach(sample)
        self.assertEqual(approach_it.state, ExperimentState.IT)

        scan_cv = ScanHoppingCVExperiment(backend, settings)
        scan_cv.start(ScanHoppingCVParameters(x_points=1, y_points=1))
        scan_cv.state = ExperimentState.APPROACHING
        scan_cv.accept_approach(sample)
        self.assertEqual(scan_cv.state, ExperimentState.CV)
        self.assertEqual(scan_cv.contact_z[(0, 0)], 42)

        scan_it = ScanHoppingITExperiment(backend, settings)
        scan_it.start(ScanHoppingITParameters(x_points=1, y_points=1))
        scan_it.state = ExperimentState.APPROACHING
        scan_it.accept_approach(sample)
        self.assertEqual(scan_it.state, ExperimentState.IT)
        self.assertEqual(scan_it.contact_z[(0, 0)], 42)
        backend.disconnect()

    def test_approach_end_of_travel_aborts_without_cv(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ApproachCVExperiment(backend, settings)
        params = ApproachCVParameters(start_z_um=10, end_z_um=12, feedback_threshold_na=9, cycles=1)
        experiment.start(params)
        experiment.tick(Sample(0, 50, 50, 10, params.approach_voltage_v, 0, 0, 0))
        experiment.tick(Sample(1, 50, 50, 12, params.approach_voltage_v, 0, 0, 0))
        self.assertEqual(experiment.state, ExperimentState.RETRACTING)
        self.assertEqual(experiment._segments, [])
        experiment.tick(Sample(2, 50, 50, 10, params.approach_voltage_v, 0, 0, 0))
        self.assertEqual(experiment.state, ExperimentState.ABORTED)
        self.assertIn("CV was not run", experiment.detail)

    def test_approach_refuses_backend_without_motion_driver(self) -> None:
        backend = NIFPGABackend(AppSettings(mode="NI FPGA"))
        experiment = ApproachCVExperiment(backend, backend.settings)
        with self.assertRaisesRegex(RuntimeError, "FPGA waypoint sequence"):
            experiment.start(ApproachCVParameters())

    def test_hardware_approach_uses_atomic_driver_contract(self) -> None:
        class HardwareBackend(SimulationBackend):
            started = False

            @property
            def hardware_approach_cv_required(self) -> bool:
                return True

            def start_hardware_approach_cv(self, params: ApproachCVParameters) -> None:
                self.started = True

            def hardware_approach_cv_status(self) -> HardwareSequenceUpdate:
                return HardwareSequenceUpdate("cv", "FPGA is sweeping", 0.6)

        settings = AppSettings()
        backend = HardwareBackend(settings)
        backend.connect()
        experiment = ApproachCVExperiment(backend, settings)
        experiment.start(ApproachCVParameters())
        self.assertTrue(backend.started)
        sample = backend.read_sample()
        update = experiment.tick(sample)
        self.assertEqual(update.state, ExperimentState.CV)
        self.assertEqual(update.progress, 0.6)

    def test_scan_cv_acquires_its_first_point_at_the_configured_start(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ScanHoppingCVExperiment(backend, settings)
        params = ScanHoppingCVParameters(x_points=1, y_points=1, cv_start_v=-0.2)
        experiment.start(params)
        experiment.state = ExperimentState.APPROACHING
        contact = Sample(0, 35, 35, 68, params.approach_voltage_v, 0, 3, 0)
        experiment.tick_samples([contact])
        self.assertEqual(experiment.state, ExperimentState.CV)
        self.assertAlmostEqual(experiment._cv_voltage, params.cv_start_v)
        first_cv_sample = backend.read_sample()
        self.assertAlmostEqual(first_cv_sample.voltage1_v, params.cv_start_v)
        backend.disconnect()

    def test_scan_hopping_cv_simulation_completes_maps(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings, seed=4)
        backend.connect()
        experiment = ScanHoppingCVExperiment(backend, settings)
        params = ScanHoppingCVParameters(
            x_start_um=40, x_end_um=60, x_points=2,
            y_start_um=40, y_end_um=60, y_points=2,
            start_z_um=55, end_z_um=80,
            lateral_rate_um_s=100, approach_rate_um_s=20, retract_rate_um_s=100,
            cv_scan_rate_v_s=10, cycles=1, map_potential_v=0.2,
        )
        experiment.start(params)
        for _ in range(300):
            backend._last_tick -= 0.25
            experiment._last_tick -= 0.25
            sample = backend.read_sample()
            experiment.tick_samples([sample])
            if experiment.state == ExperimentState.COMPLETE:
                break
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        self.assertEqual(len(experiment.contact_z), 4)
        self.assertEqual(len(experiment.current_at_potential), 4)
        self.assertEqual(set(experiment.contact_z), {(0, 0), (0, 1), (1, 0), (1, 1)})
        self.assertTrue(all(55 < value < 80 for value in experiment.contact_z.values()))
        backend.disconnect()

    def test_scan_spacing_and_duration_are_derived_from_the_full_path(self) -> None:
        cv = ScanHoppingCVParameters(
            x_start_um=0, x_end_um=20, x_points=3,
            y_start_um=0, y_end_um=10, y_points=2,
            start_z_um=10, end_z_um=20,
            lateral_rate_um_s=10, approach_rate_um_s=5, retract_rate_um_s=10,
            cv_start_v=0, cv_vertex1_v=1, cv_vertex2_v=-1, cv_scan_rate_v_s=2, cycles=1,
        )
        self.assertEqual(cv.spacing_um, (10.0, 10.0))
        self.assertAlmostEqual(cv.estimated_known_duration_s(), 33.0)
        it = ScanHoppingITParameters(
            x_start_um=0, x_end_um=10, x_points=2, y_start_um=0, y_end_um=0, y_points=1,
            start_z_um=10, end_z_um=20, lateral_rate_um_s=10,
            approach_rate_um_s=5, retract_rate_um_s=10,
            initial_hold_s=1, step_hold_s=2, return_hold_s=1, cycles=1,
        )
        self.assertEqual(it.spacing_um, (10.0, 0.0))
        self.assertAlmostEqual(it.estimated_known_duration_s(), 13.0)

    def test_raster_scan_uses_extra_safe_z_at_line_flyback(self) -> None:
        params = ScanHoppingCVParameters(
            x_points=3, y_points=2, serpentine=False,
            start_z_um=55, end_z_um=80, raster_line_retract_um=8,
        )
        self.assertEqual([point[1] for point in params.grid()], [0, 1, 2, 0, 1, 2])
        self.assertEqual(params.retract_z_for_point(2, 68), 50)
        self.assertEqual(params.approach_start_z_for_point(3, 68), 50)
        self.assertEqual(params.retract_z_for_point(5, 68), 58)
        self.assertEqual(params.retract_distance_for_point(2), 18)
        self.assertEqual(params.retract_distance_for_point(5), 10)
        invalid = ScanHoppingITParameters(
            x_points=2, y_points=2, serpentine=False,
            start_z_um=2, end_z_um=80, raster_line_retract_um=5,
        )
        self.assertTrue(any("outside" in error for error in invalid.validate(AppSettings())))

    def test_scan_end_of_travel_aborts_pixel_without_cv(self) -> None:
        settings = AppSettings()
        backend = SimulationBackend(settings)
        backend.connect()
        experiment = ScanHoppingCVExperiment(backend, settings)
        params = ScanHoppingCVParameters(
            x_start_um=20, x_end_um=20, x_points=1,
            y_start_um=20, y_end_um=20, y_points=1,
            start_z_um=10, end_z_um=12, feedback_threshold_na=9,
        )
        experiment.start(params)
        experiment.tick_samples([Sample(0, 20, 20, 10, params.approach_voltage_v, 0, 0, 0)])
        experiment.tick_samples([Sample(1, 20, 20, 12, params.approach_voltage_v, 0, 0, 0)])
        self.assertEqual(experiment.state, ExperimentState.RETRACTING)
        self.assertEqual(experiment._segments, [])
        experiment.tick_samples([Sample(2, 20, 20, 10, params.approach_voltage_v, 0, 0, 0)])
        self.assertEqual(experiment.state, ExperimentState.ABORTED)
        self.assertFalse(experiment.contact_detected[(0, 0)])
        self.assertNotIn((0, 0), experiment.current_at_potential)

    def test_hardware_scan_samples_build_the_same_maps(self) -> None:
        class HardwareScanBackend(SimulationBackend):
            @property
            def hardware_approach_cv_required(self) -> bool:
                return True

            @property
            def scan_hopping_cv_available(self) -> bool:
                return True

            def start_hardware_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
                self.params = params

            def hardware_scan_context(self, line_number: int) -> tuple[int, str]:
                return {1: (0, "approach"), 2: (0, "cv"), 3: (0, "retract")}.get(line_number, (-1, ""))

            def hardware_scan_hopping_cv_status(self) -> HardwareSequenceUpdate:
                return HardwareSequenceUpdate("complete", "complete", 1.0, 0, "complete")

        settings = AppSettings()
        backend = HardwareScanBackend(settings)
        backend.connect()
        experiment = ScanHoppingCVExperiment(backend, settings)
        params = ScanHoppingCVParameters(x_points=1, y_points=1, map_potential_v=0.2)
        experiment.start(params)
        samples = [
            Sample(0, 35, 35, 67.4, 0.1, 0, 2.1, 0, line_number=1),
            Sample(1, 35, 35, 67.4, 0.2, 0, 3.25, 0, line_number=2),
            Sample(2, 35, 35, 60.0, -0.2, 0, 1.0, 0, line_number=3),
        ]
        experiment.tick_samples(samples)
        self.assertEqual(experiment.state, ExperimentState.COMPLETE)
        self.assertEqual(experiment.contact_z[(0, 0)], 67.4)
        self.assertEqual(experiment.current_at_potential[(0, 0)], 3.25)
        self.assertEqual(samples[1].scan_pixel, 0)

    def test_hardware_scan_contact_transition_remains_active_until_cv(self) -> None:
        class HardwareScanBackend(SimulationBackend):
            def __init__(self, settings: AppSettings) -> None:
                super().__init__(settings)
                self.updates = [
                    HardwareSequenceUpdate("contact", "contact confirmed", 0.1, 0, "contact"),
                    HardwareSequenceUpdate("cv", "CV submitted", 0.2, 0, "cv"),
                ]

            @property
            def hardware_approach_cv_required(self) -> bool:
                return True

            @property
            def scan_hopping_cv_available(self) -> bool:
                return True

            def start_hardware_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
                self.params = params

            def hardware_scan_hopping_cv_status(self) -> HardwareSequenceUpdate:
                return self.updates.pop(0)

        settings = AppSettings()
        backend = HardwareScanBackend(settings)
        backend.connect()
        experiment = ScanHoppingCVExperiment(backend, settings)
        experiment.start(ScanHoppingCVParameters(x_points=1, y_points=1))

        contact = experiment.tick_samples([])
        self.assertEqual(contact.state, ExperimentState.CONTACT)
        self.assertTrue(experiment.active)

        cv = experiment.tick_samples([])
        self.assertEqual(cv.state, ExperimentState.CV)
        self.assertTrue(experiment.active)


if __name__ == "__main__":
    unittest.main()
