from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from .host import ExecutionSnapshot, ExecutionState, WaypointStreamer
from .models import (
    AppSettings, ApproachCVParameters, ApproachITParameters, ApproachParameters,
    CVParameters, FeedbackConfiguration, Sample, ScanHoppingCVParameters, ScanHoppingITParameters,
)
from .ni_protocol import (
    FPGA_CLOCK_HZ,
    FEEDBACK_ACTION_CODES,
    FEEDBACK_SIGNAL_CODES,
    HOST_TO_TARGET_FIFO,
    SAMPLE_WORDS,
    TARGET_TO_HOST_FIFO,
    SampleDecoder,
    Waypoint,
    clamp_i16,
    current_to_raw,
    position_to_raw,
    raw_to_position,
    raw_position_velocity_per_tick,
    raw_voltage1_velocity_per_tick,
    scale_velocity,
    select_velocity_exponent,
    voltage1_to_raw,
)
from .waypoints import (
    CompiledWaypoints, PhysicalWaypoint, WaypointCompiler,
    cyclic_voltammetry_plan, potential_step_plan,
)


@dataclass(slots=True)
class _Sequence:
    baseline_line: int
    total: int
    cv_first: int
    cv_last: int
    retract_index: int | None


@dataclass(slots=True)
class _ScanSequence:
    baseline_line: int
    descriptors: list[tuple[int, str]]


class WECSPMDriver:
    """Native host driver for the FIFO protocol in WEC-SPM FPGA Target.vi."""

    def __init__(self, session: Any, settings: AppSettings) -> None:
        self.session = session
        self.settings = settings
        self.positions_fifo = session.fifos[HOST_TO_TARGET_FIFO]
        self.data_fifo = session.fifos[TARGET_TO_HOST_FIFO]
        self.streamer = WaypointStreamer(self.positions_fifo)
        self.decoder = SampleDecoder(settings)
        self._sequence: _Sequence | None = None
        self._scan_sequence: _ScanSequence | None = None
        self._approach_history: list[tuple[int, list[str]]] = []
        self._scan_history: list[_ScanSequence] = []
        self._approach_params: ApproachCVParameters | None = None
        self._approach_phase = ""
        self._scan_params: ScanHoppingCVParameters | None = None
        self._scan_grid: list[tuple[int, int, float, float]] = []
        self._scan_phase = ""
        self._scan_point = -1
        self._started = False
        self._stopped = False
        self._submitted = False
        self._program_baseline = 0
        self._program_total = 0
        self._hardware_complete = False
        self._program_deadline: float | None = None
        self._expected_duration_s = 0.0
        self._pending_scalers: tuple[int, ...] = ()
        self._cancelled = False
        self._cancel_detail = ""
        self._approach_threshold_na = 0.0
        self._approach_greater_than = True
        self._approach_end_z_raw: int | None = None
        self._approach_contact_observed = False
        self._scan_threshold_na = 0.0
        self._scan_greater_than = True
        self._scan_end_z_raw: int | None = None
        self._scan_contact_observed: set[int] = set()
        self._scan_last_approach_z: dict[int, float] = {}
        self._scan_failed_contact: int | None = None
        self._owner = ""
        self._execution_state = ExecutionState.IDLE
        self._execution_detail = "Ready"
        self._method_name = ""
        self._method_phase = ""
        self._method_params: object | None = None
        self._method_history: list[_ScanSequence] = []
        self._method_sequence: _ScanSequence | None = None
        self._method_terminal = ""
        self._method_detail = ""
        self._method_threshold = 0.0
        self._method_greater_than = True
        self._method_contact_observed: set[int] = set()
        self._method_grid: list[tuple[int, int, float, float]] = []
        self._method_point = -1
        self._method_no_contact = False
        self.compiler = WaypointCompiler(settings)
        self.configure()

    def _write_register(self, name: str, value: Any) -> None:
        self.session.registers[name].write(value)

    def _read_register(self, name: str) -> Any:
        return self.session.registers[name].read()

    @staticmethod
    def _sample_line_delta(current_i16: int, baseline_u64: int) -> int:
        """Map the FIFO's I16 line tag onto the low word of the U64 register."""
        return (int(current_i16) - (int(baseline_u64) & 0xFFFF)) & 0xFFFF

    def configure(self) -> None:
        for fifo in (self.positions_fifo, self.data_fifo):
            try:
                fifo.stop()
            except Exception:
                pass
        # NI configures the host-memory side here; the target-side compiled
        # depth remains 8197. One million I16 elements holds the generic
        # driver's complete frame ceiling while DMA flow control feeds target.
        self.positions_fifo.configure(requested_depth=1_048_576)
        self.data_fifo.configure(requested_depth=max(32768, SAMPLE_WORDS * 8192))
        self.positions_fifo.start()
        self.data_fifo.start()
        self._write_register("Buffer Loop Wait Time (tICKS)", self.settings.sample_time_us * 40)
        average_exponent = int(math.ceil(math.log2(self.settings.samples_per_point)))
        self._write_register("2^(-n)", average_exponent)
        self._write_register("External Stop", False)
        self._write_register("External Pause", True)
        self._write_register("Internal Pause", False)
        self._write_register("EndCurrentLine", False)
        # Method-specific feedback thresholds are written when that method is
        # submitted. Initialization uses a neutral, always-representable value.
        self.configure_feedback(FeedbackConfiguration.from_settings(self.settings, primary_threshold=0.0))
        self._cancelled = False
        self._cancel_detail = ""
        self._started = True

    def _current_targets(self) -> dict[str, int]:
        return {
            "X": int(self._read_register("Applied X")),
            "Y": int(self._read_register("Applied Y")),
            "Z": int(self._read_register("Applied Z")),
            "V": int(self._read_register("Applied Voltage")),
            "V2": int(self._read_register("Applied Voltage 2")),
        }

    def _set_scalers(self, x: list[float], y: list[float], z: list[float], v: list[float]) -> tuple[int, int, int, int]:
        exponents = tuple(select_velocity_exponent(values) for values in (x, y, z, v))
        self._pending_scalers = exponents
        return exponents

    def _enqueue(self, waypoints: list[Waypoint], expected_duration_s: float | None) -> None:
        payload = [word for waypoint in waypoints for word in waypoint.words()]
        if len(waypoints) > 65535:
            raise ValueError("A single streamed program cannot exceed the verified 16-bit sample-tag span.")
        if expected_duration_s is not None and (not math.isfinite(expected_duration_s) or expected_duration_s < 0):
            raise ValueError("The expected FPGA program duration is invalid.")
        self._program_baseline = int(self._read_register("LineNumber"))
        self._write_register("External Pause", True)
        try:
            for name, exponent in zip(
                ("ExpandVelScaller X", "ExpandVelScaller Y", "ExpandVelScaller Z", "ExpandVelScaller V", "ExpandVelScaller V2"),
                self._pending_scalers,
            ):
                self._write_register(name, -exponent)
            self.streamer.start(payload, timeout_ms=100)
        except Exception:
            # A timed-out FIFO write can have transferred a prefix. Never
            # allow another program to resume that uncertain queue.
            self._stopped = True
            self._program_deadline = None
            raise
        self._program_total = len(waypoints)
        self._hardware_complete = False
        self._cancelled = False
        self._cancel_detail = ""
        self._submitted = True
        self._expected_duration_s = expected_duration_s or 0.0
        self._program_deadline = (
            None if expected_duration_s is None
            else time.monotonic() + expected_duration_s + self.settings.hardware_watchdog_margin_s
        )
        self._execution_state = ExecutionState.RUNNING
        self._execution_detail = f"Submitted {self.streamer.submitted_waypoints}/{len(waypoints)} waypoints"

    def wait_until_ready(self) -> None:
        """Require an initialized, empty target loop before enabling commands."""
        deadline = time.monotonic() + self.settings.hardware_ready_timeout_s
        while time.monotonic() < deadline:
            self._check_target_health()
            if bool(self._read_register("WaitingForWayPoints")):
                return
            time.sleep(0.01)
        self.emergency_stop()
        raise TimeoutError(
            f"FPGA did not enter its ready state within {self.settings.hardware_ready_timeout_s:g} s; "
            "the target was stopped and must be reinitialized."
        )

    def _check_target_health(self, *, allow_external_stop: bool = False) -> None:
        state = getattr(self.session, "fpga_vi_state", None)
        state_name = getattr(state, "name", "Running")
        internal_stop = bool(self._read_register("Internal Stop"))
        external_stop = bool(self._read_register("External Stop"))
        if state_name != "Running" or internal_stop or (external_stop and not allow_external_stop):
            self._stopped = True
            self._program_deadline = None
            try:
                self._write_register("External Pause", True)
            except Exception:
                pass
            if state_name != "Running":
                reason = f"the FPGA VI state is {state_name}"
            elif internal_stop:
                reason = "the FPGA asserted Internal Stop"
            elif external_stop:
                reason = "External Stop is asserted"
            else:
                reason = "an unknown target fault was detected"
            raise RuntimeError(f"Hardware is not operational because {reason}. Reinitialize the target before continuing.")

    def ensure_idle(self) -> None:
        if self._stopped:
            raise ValueError("Motion was stopped. Reinitialize the target before another command.")
        self._check_target_health()
        if self._submitted:
            raise ValueError("An FPGA program is active or awaiting its final sample drain.")

    def _prepare_command(self) -> None:
        self.ensure_idle()

    def cancel_program(self) -> None:
        """Cancel queued motion, verify the FPGA is idle, and leave it re-armable.

        External Stop is the WEC-SPM host's per-action cancellation control;
        Internal Stop is the fatal FPGA indicator. Motion is allowed again only
        after the target reports that it is waiting for waypoints with External
        Stop cleared and External Pause asserted.
        """
        if self._stopped:
            raise ValueError("Motion was stopped by a fatal fault. Reinitialize the target before another command.")
        self._check_target_health()
        if not self._submitted:
            self._write_register("External Pause", True)
            return
        self._execution_state = ExecutionState.CANCELLING
        self._execution_detail = "Cancelling active FPGA program"
        self.streamer.cancel()
        self._write_register("External Pause", True)
        self._write_register("External Stop", True)
        # Let the control loop observe Stop; while Stop is asserted it must not
        # execute another queued waypoint.
        self._write_register("External Pause", False)
        deadline = time.monotonic() + self.settings.hardware_ready_timeout_s
        try:
            while time.monotonic() < deadline:
                self._check_target_health(allow_external_stop=True)
                if bool(self._read_register("WaitingForWayPoints")):
                    break
                time.sleep(0.005)
            else:
                raise TimeoutError("FPGA did not acknowledge cancellation by returning to WaitingForWayPoints")
            self._write_register("External Pause", True)
            self._write_register("EndCurrentLine", False)
            self._write_register("External Stop", False)
            if bool(self._read_register("Internal Pause")):
                self._write_register("Internal Pause", False)
            if not bool(self._read_register("WaitingForWayPoints")):
                raise RuntimeError("FPGA left its ready state while cancellation was being finalized")
        except Exception:
            self._stopped = True
            self._program_deadline = None
            try:
                self._write_register("External Pause", True)
            except Exception:
                pass
            raise
        self._submitted = False
        self._hardware_complete = False
        self._program_deadline = None
        self._cancelled = True
        self._cancel_detail = "FPGA program cancelled; final data snapshot is available"
        self._execution_state = ExecutionState.ABORTED
        self._execution_detail = self._cancel_detail

    def service(self) -> bool:
        """Observe completion; read_samples retires it after a final FIFO snapshot.

        Waiting alone can describe startup or an underfilled queue. Require
        every submitted line as well, including programs faster than polling.
        """
        if not self._stopped:
            self._check_target_health()
        if self._submitted and not self._stopped:
            for _ in range(4):
                if self.streamer.complete:
                    break
                try:
                    self.streamer.refill(timeout_ms=20)
                except Exception as exc:
                    self._stopped = True
                    self._program_deadline = None
                    self._execution_state = ExecutionState.ERROR
                    self._execution_detail = f"Waypoint refill failed: {exc}"
                    self._write_register("External Pause", True)
                    raise RuntimeError(self._execution_detail) from exc
            current = int(self._read_register("LineNumber"))
            delta = (current - self._program_baseline) & ((1 << 64) - 1)
            if delta > self._program_total:
                self._stopped = True
                self._program_deadline = None
                self._write_register("External Pause", True)
                raise RuntimeError(
                    f"FPGA line counter advanced by {delta} for a {self._program_total}-waypoint program. "
                    "Motion was paused because target/host state is inconsistent."
                )
            waiting = bool(self._read_register("WaitingForWayPoints"))
            paused = bool(self._read_register("External Pause")) or bool(self._read_register("Internal Pause"))
            # A paused target is not a successful completion even if its line
            # counter and queue happen to look finished. The host must observe
            # an unpaused, waiting target and then drain the final FIFO data.
            self._hardware_complete = (
                self.streamer.complete and waiting and delta == self._program_total and not paused
            )
            self._execution_state = ExecutionState.PAUSED if paused else (
                ExecutionState.DRAINING if self._hardware_complete else ExecutionState.RUNNING
            )
            self._execution_detail = (
                f"{delta}/{self._program_total} executed; {self.streamer.pending_waypoints} awaiting FIFO refill"
            )
            if (
                not self._hardware_complete
                and self._program_deadline is not None
                and time.monotonic() > self._program_deadline
            ):
                self._stopped = True
                try:
                    self._write_register("External Pause", True)
                finally:
                    self._program_deadline = None
                raise TimeoutError(
                    f"FPGA program exceeded its {self._expected_duration_s:g} s expected duration plus "
                    f"the {self.settings.hardware_watchdog_margin_s:g} s safety margin. Motion was paused; "
                    "reinitialize the target before continuing."
                )
        return not self._submitted

    def move(self, axis: str, target: float, speed: float) -> None:
        if axis not in {"X", "Y", "Z"}:
            raise ValueError(f"Native WEC-SPM motion only supports X, Y, and Z; got {axis}.")
        span = getattr(self.settings, f"{axis.lower()}_range_um")
        if not math.isfinite(target) or not 0 <= target <= span:
            raise ValueError("Motion target must be finite and within the piezo range.")
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("Motion speed must be finite and positive.")
        self._prepare_command()
        self._owner = "manual-move"
        current = self._current_targets()
        field = axis.lower()
        compiled = self.compiler.compile([
            PhysicalWaypoint(**{f"{field}_um": target, f"{field}_rate_um_s": speed})
        ], current)
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        self._sequence = None
        self._scan_sequence = None
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)

    def compile_waypoints(self, plan: list[PhysicalWaypoint]) -> CompiledWaypoints:
        """Compile an arbitrary shared physical-unit waypoint plan."""
        return self.compiler.compile(plan, self._current_targets())

    def submit_waypoints(self, plan: list[PhysicalWaypoint], *, owner: str = "custom") -> None:
        """Compile and stream a custom plan with the same ownership checks."""
        self._prepare_command()
        compiled = self.compile_waypoints(plan)
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        self._owner = owner
        self._sequence = None
        self._scan_sequence = None
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)

    def _feedback_value_to_raw(self, channel: str, value: float) -> int:
        if not math.isfinite(value):
            raise ValueError("Feedback thresholds must be finite.")
        if channel.startswith("Current "):
            index = int(channel.rsplit(" ", 1)[1])
            sensitivity = getattr(self.settings, f"current{index}_v_per_na")
            if abs(value * sensitivity) > 10:
                raise ValueError(f"{channel} feedback threshold exceeds its +/-10 V ADC range.")
            return current_to_raw(value, sensitivity)
        if channel == "Lock-in amplitude":
            normalized = value * self.settings.lockin_expand / self.settings.lockin_sensitivity_na
            normalized += self.settings.lockin_offset_pct / 100.0
            if abs(normalized) > 1:
                raise ValueError("Lock-in amplitude feedback threshold exceeds its configured analog range.")
            return clamp_i16(normalized * 32768.0)
        if channel == "Lock-in phase":
            if abs(value) > 180:
                raise ValueError("Lock-in phase feedback threshold must be within +/-180 degrees.")
            return clamp_i16(value * 32768.0 / 180.0)
        raise ValueError(f"Unsupported feedback channel: {channel}")

    def _distance_to_raw(self, distance_um: float) -> int:
        multiplier = 2.0 if self.settings.z_bipolar else 1.0
        return clamp_i16(distance_um * 32768.0 * multiplier / self.settings.z_range_um)

    def configure_feedback(self, config: FeedbackConfiguration) -> None:
        """Apply ChangeOnFly feedback controls; execution remains on FPGA."""
        if config.primary_channel not in FEEDBACK_SIGNAL_CODES or config.secondary_channel not in FEEDBACK_SIGNAL_CODES:
            raise ValueError("Unsupported feedback signal selection.")
        if not math.isfinite(config.proportional_gain):
            raise ValueError("Feedback proportional gain must be finite.")
        if not 0 <= config.update_interval_us <= 32767:
            raise ValueError("Feedback update interval must be 0..32767 us.")
        if config.max_z_step_nm < 0 or config.running_average_whole < 0 or config.running_average_minus < 0:
            raise ValueError("Feedback movement and running-average settings must be non-negative.")
        # Convert every value before touching the target so invalid input can
        # never leave a partially updated feedback configuration.
        primary_raw = self._feedback_value_to_raw(config.primary_channel, config.primary_threshold)
        secondary_raw = self._feedback_value_to_raw(config.secondary_channel, config.secondary_threshold)
        bulk_raw = tuple(self._distance_to_raw(value) for value in (
            config.distance_to_bulk_um, config.distance_to_bulk2_um, config.distance_to_bulk3_um,
        ))
        writes = (
            ("FeedBackType", FEEDBACK_SIGNAL_CODES[config.primary_channel]),
            ("Feedback_Threshold", primary_raw),
            ("GreaterThan", bool(config.primary_greater_than)),
            ("FeedBackType 2", FEEDBACK_SIGNAL_CODES[config.secondary_channel]),
            ("Feedback_Threshold 2", secondary_raw),
            ("GreaterThan 2", bool(config.secondary_greater_than)),
            ("P", float(config.proportional_gain)),
            ("Upper limit Of dZ", int(config.max_z_step_nm)),
            ("P2AvgWhole", int(config.running_average_whole)),
            ("P2AvgMinus", int(config.running_average_minus)),
            ("Feedback1 on  Hold", bool(config.self_reference_on_hold)),
            ("DistanceToBulk", bulk_raw[0]),
            ("DistanceToBulk 2", bulk_raw[1]),
            ("DistanceToBulk 3", bulk_raw[2]),
        )
        try:
            for name, value in writes:
                self._write_register(name, value)
        except Exception as exc:
            self._stopped = True
            self._execution_state = ExecutionState.ERROR
            self._execution_detail = f"Feedback configuration failed at {name}: {exc}"
            try:
                self._write_register("External Pause", True)
            except Exception:
                pass
            raise RuntimeError(self._execution_detail) from exc
        self._feedback_update_interval_us = int(config.update_interval_us)

    def start_approach_cv(self, params: ApproachCVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("; ".join(errors))
        if 1 + 3 * params.cycles + int(params.retract_after) > 65535:
            raise ValueError("Reduce CV cycles: the streamed CV exceeds the verified line-tag span.")
        if params.feedback_channel != "Current 1":
            raise ValueError("This first real-device profile supports Current 1 feedback only.")
        if abs(params.feedback_threshold_na * self.settings.current1_v_per_na) > 10:
            raise ValueError("Feedback threshold exceeds Current 1's +/-10 V ADC range.")
        if any(
            abs(voltage * self.settings.command_voltage_ratio) > 10
            for voltage in (params.approach_voltage_v, params.cv_start_v, params.cv_vertex1_v, params.cv_vertex2_v)
        ):
            raise ValueError("A requested potential exceeds AO3 after applying the command-voltage ratio.")
        self._prepare_command()
        self._owner = "approach-cv"
        current = self._current_targets()
        s = self.settings
        z_speeds = [max(10.0, params.approach_rate_um_s), params.approach_rate_um_s]
        z_raw = [raw_position_velocity_per_tick(speed, s.z_range_um, s.z_bipolar) for speed in z_speeds]
        ex, ey, ez, ev = self._set_scalers([], [], z_raw, [])
        z_fast = scale_velocity(z_raw[0], ez)
        z_approach = scale_velocity(z_raw[1], ez)
        start_z = position_to_raw(params.start_z_um, s.z_range_um, s.z_bipolar)
        end_z = position_to_raw(params.end_z_um, s.z_range_um, s.z_bipolar)
        approach_v = voltage1_to_raw(params.approach_voltage_v, s.command_voltage_ratio)

        common = dict(x_position=current["X"], y_position=current["Y"], v2_position=current["V2"])
        waypoints = [
            Waypoint(**common, z_position=start_z, v_position=approach_v, z_velocity=z_fast, move_z=True, move_v=True, jump_v=True),
            # Pause-on-contact is deliberately used instead of advance-on-contact:
            # Python validates the feedback/pause state before it ever submits CV.
            Waypoint(
                **common,
                line_type=FEEDBACK_ACTION_CODES["pause_on_contact"],
                z_position=end_z,
                v_position=approach_v,
                z_velocity=z_approach,
                update_wait_us=self._feedback_update_interval_us,
                move_z=True,
            ),
        ]
        self.configure_feedback(FeedbackConfiguration.from_settings(
            self.settings,
            primary_channel=params.feedback_channel,
            primary_threshold=params.feedback_threshold_na,
            primary_greater_than=params.greater_than,
        ))
        current_z = raw_to_position(current["Z"], s.z_range_um, s.z_bipolar)
        expected_duration = (
            abs(current_z - params.start_z_um) / z_speeds[0]
            + abs(params.end_z_um - params.start_z_um) / params.approach_rate_um_s
        )
        self._enqueue(waypoints, expected_duration)
        self._sequence = _Sequence(self._program_baseline, len(waypoints), 2, 1, None)
        self._approach_history = [(self._program_baseline, ["preposition", "approach"])]
        self._approach_params = params
        self._approach_phase = "approach"
        self._scan_sequence = None
        self._approach_threshold_na = params.feedback_threshold_na
        self._approach_greater_than = params.greater_than
        self._approach_end_z_raw = end_z
        self._approach_contact_observed = False

    def _submit_approach_cv_followup(self) -> None:
        params = self._approach_params
        if params is None:
            raise RuntimeError("Approach parameters were not retained for the CV follow-up")
        current = self._current_targets()
        z_speed = max(10.0, params.approach_rate_um_s)
        plan = cyclic_voltammetry_plan(
            start_v=params.cv_start_v,
            vertex1_v=params.cv_vertex1_v,
            vertex2_v=params.cv_vertex2_v,
            scan_rate_v_s=params.cv_scan_rate_v_s,
            cycles=params.cycles,
            retract_z_um=params.start_z_um if params.retract_after else None,
            retract_rate_um_s=z_speed if params.retract_after else None,
        )
        compiled = self.compiler.compile(plan, current)
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        retract_index = len(compiled.waypoints) - 1 if params.retract_after else None
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)
        contexts = ["cv"] * len(compiled.waypoints)
        if retract_index is not None:
            contexts[retract_index] = "retract"
        self._approach_history.append((self._program_baseline, contexts))
        self._sequence = _Sequence(self._program_baseline, len(compiled.waypoints), 0,
                                   retract_index - 1 if retract_index is not None else len(compiled.waypoints) - 1,
                                   retract_index)
        self._approach_phase = "cv"
        self._write_register("External Pause", False)

    def approach_cv_status(self) -> dict[str, str | float]:
        sequence = self._sequence
        if sequence is None:
            return {"stage": "aborted", "detail": "No active FPGA sequence", "progress": 0.0}
        if self._cancelled:
            return {"stage": "aborted", "detail": self._cancel_detail or "FPGA sequence cancelled", "progress": 0.0}
        self.service()
        current_line = int(self._read_register("LineNumber"))
        completed = (current_line - sequence.baseline_line) & ((1 << 64) - 1)
        if self._approach_phase == "approach":
            internal_pause = bool(self._read_register("Internal Pause"))
            if internal_pause:
                if not self._approach_contact_confirmed():
                    self._cancel_detail = "FPGA paused during approach without a confirmed feedback event"
                    self.cancel_program()
                    self._cancel_detail = "FPGA paused during approach without a confirmed feedback event"
                    return {"stage": "aborted", "detail": self._cancel_detail, "progress": 0.4}
                self.cancel_program()
                self._submit_approach_cv_followup()
                return {"stage": "contact", "detail": "Contact confirmed; CV program submitted", "progress": 0.42}
            if not self._submitted and self._hardware_complete:
                self._cancelled = True
                self._cancel_detail = "End Z was reached without a confirmed contact; CV was not submitted"
                return {"stage": "aborted", "detail": self._cancel_detail, "progress": 0.4}
            index = min(max(0, completed), sequence.total - 1)
            if index == 0:
                return {"stage": "preposition", "detail": "Moving Z to the approach start", "progress": 0.2}
            return {"stage": "approaching", "detail": "FPGA is watching Current 1 for contact", "progress": 0.4}
        if not self._submitted and self._hardware_complete:
            return {"stage": "complete", "detail": "FPGA approach, CV, and retract complete", "progress": 1.0}
        index = min(max(0, completed), sequence.total - 1)
        progress = min(0.99, completed / max(1, sequence.total))
        if sequence.retract_index is not None and index >= sequence.retract_index:
            return {"stage": "retracting", "detail": "CV complete; retracting Z", "progress": progress}
        cycle = min((index - sequence.cv_first) // 3 + 1, max(1, (sequence.cv_last - sequence.cv_first) // 3 + 1))
        return {"stage": "cv", "detail": f"FPGA is running CV cycle {cycle}", "progress": progress}

    def approach_context(self, line_number: int) -> str:
        """Map a FIFO sample line number back to its programmed segment."""
        for baseline, contexts in reversed(self._approach_history):
            index = self._sample_line_delta(line_number, baseline)
            if index < len(contexts):
                return contexts[index]
        return ""

    def start_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("; ".join(errors))
        if abs(params.feedback_threshold_na * self.settings.current1_v_per_na) > 10:
            raise ValueError("Feedback threshold exceeds Current 1's +/-10 V ADC range.")
        if any(
            abs(voltage * self.settings.command_voltage_ratio) > 10
            for voltage in (params.approach_voltage_v, params.cv_start_v, params.cv_vertex1_v, params.cv_vertex2_v)
        ):
            raise ValueError("A requested potential exceeds AO3 after applying the command-voltage ratio.")
        self._prepare_command()
        self._owner = "scan-hopping-cv"
        # The source exports a U64 counter but I16 sample tags. Until the
        # target's narrowing conversion is verified, do not rely on wrapping
        # tags to assign samples to pixels after the signed transport range.
        baseline = int(self._read_register("LineNumber"))
        total = 1 + params.point_count * (4 + 3 * params.cycles)
        if baseline < 0 or baseline + total > 32767:
            raise ValueError("Scan line tags would exceed the verified I16 range. Reinitialize the target before scanning.")
        s = self.settings
        self.configure_feedback(FeedbackConfiguration.from_settings(
            self.settings,
            primary_channel="Current 1",
            primary_threshold=params.feedback_threshold_na,
            primary_greater_than=params.greater_than,
        ))
        self._sequence = None
        self._scan_params = params
        self._scan_grid = params.grid()
        self._scan_history.clear()
        self._scan_point = 0
        self._scan_threshold_na = params.feedback_threshold_na
        self._scan_greater_than = params.greater_than
        self._scan_end_z_raw = position_to_raw(params.end_z_um, s.z_range_um, s.z_bipolar)
        self._scan_contact_observed.clear()
        self._scan_last_approach_z.clear()
        self._scan_failed_contact = None
        self._submit_scan_approach(0, initial=True, resume=False)

    def _submit_scan_approach(self, point: int, *, initial: bool, resume: bool = True) -> None:
        params = self._scan_params
        if params is None or not 0 <= point < len(self._scan_grid):
            raise RuntimeError("Scan approach phase has no valid point")
        s = self.settings
        current = self._current_targets()
        _row, _column, x_um, y_um = self._scan_grid[point]
        x_raw = raw_position_velocity_per_tick(params.lateral_rate_um_s, s.x_range_um, s.x_bipolar)
        y_raw = raw_position_velocity_per_tick(params.lateral_rate_um_s, s.y_range_um, s.y_bipolar)
        za_raw = raw_position_velocity_per_tick(params.approach_rate_um_s, s.z_range_um, s.z_bipolar)
        zr_raw = raw_position_velocity_per_tick(params.retract_rate_um_s, s.z_range_um, s.z_bipolar)
        ex, ey, ez, _ev = self._set_scalers([x_raw], [y_raw], [za_raw, zr_raw], [])
        start_z = position_to_raw(params.start_z_um, s.z_range_um, s.z_bipolar)
        common = dict(
            x_position=position_to_raw(x_um, s.x_range_um, s.x_bipolar),
            y_position=position_to_raw(y_um, s.y_range_um, s.y_bipolar),
            v2_position=current["V2"],
        )
        waypoints: list[Waypoint] = []
        stages: list[str] = []
        if initial:
            waypoints.append(
                Waypoint(x_position=current["X"], y_position=current["Y"], z_position=start_z,
                         v_position=current["V"], v2_position=current["V2"],
                         z_velocity=scale_velocity(zr_raw, ez), move_z=True)
            )
            stages.append("retract")
        waypoints.extend((
            Waypoint(**common, z_position=start_z,
                     v_position=voltage1_to_raw(params.approach_voltage_v, s.command_voltage_ratio),
                     x_velocity=scale_velocity(x_raw, ex), y_velocity=scale_velocity(y_raw, ey),
                     move_x=True, move_y=True, move_v=True, jump_v=True),
            Waypoint(**common, line_type=FEEDBACK_ACTION_CODES["pause_on_contact"],
                     z_position=position_to_raw(params.end_z_um, s.z_range_um, s.z_bipolar),
                     v_position=voltage1_to_raw(params.approach_voltage_v, s.command_voltage_ratio),
                     z_velocity=scale_velocity(za_raw, ez),
                     update_wait_us=self._feedback_update_interval_us, move_z=True),
        ))
        stages.extend(("positioning", "approach"))
        current_x = raw_to_position(current["X"], s.x_range_um, s.x_bipolar)
        current_y = raw_to_position(current["Y"], s.y_range_um, s.y_bipolar)
        current_z = raw_to_position(current["Z"], s.z_range_um, s.z_bipolar)
        expected = max(abs(x_um - current_x), abs(y_um - current_y)) / params.lateral_rate_um_s
        expected += abs(params.end_z_um - params.start_z_um) / params.approach_rate_um_s
        if initial:
            expected += abs(current_z - params.start_z_um) / params.retract_rate_um_s
        self._enqueue(waypoints, expected)
        descriptors = [(point, stage) for stage in stages]
        self._scan_sequence = _ScanSequence(self._program_baseline, descriptors)
        self._scan_history.append(self._scan_sequence)
        self._scan_phase = "approach"
        self._scan_point = point
        if resume:
            self._write_register("External Pause", False)

    def _submit_scan_cv(self, point: int) -> None:
        params = self._scan_params
        if params is None:
            raise RuntimeError("Scan CV phase has no parameters")
        current = self._current_targets()
        plan = cyclic_voltammetry_plan(
            start_v=params.cv_start_v,
            vertex1_v=params.cv_vertex1_v,
            vertex2_v=params.cv_vertex2_v,
            scan_rate_v_s=params.cv_scan_rate_v_s,
            cycles=params.cycles,
            retract_z_um=params.start_z_um,
            retract_rate_um_s=params.retract_rate_um_s,
        )
        compiled = self.compiler.compile(plan, current)
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)
        descriptors = [(point, "cv")] * (len(compiled.waypoints) - 1) + [(point, "retract")]
        self._scan_sequence = _ScanSequence(self._program_baseline, descriptors)
        self._scan_history.append(self._scan_sequence)
        self._scan_phase = "cv"
        self._write_register("External Pause", False)

    def scan_context(self, line_number: int) -> tuple[int, str]:
        for sequence in reversed(self._scan_history):
            index = self._sample_line_delta(line_number, sequence.baseline_line)
            if index < len(sequence.descriptors):
                return sequence.descriptors[index]
        return -1, ""

    def scan_hopping_cv_status(self) -> dict[str, str | float | int]:
        sequence = self._scan_sequence
        if sequence is None:
            return {"stage": "aborted", "detail": "No active FPGA scan", "progress": 0.0, "point_index": -1}
        if self._cancelled:
            return {
                "stage": "aborted", "detail": self._cancel_detail or "FPGA scan cancelled",
                "progress": 0.0,
                "point_index": self._scan_failed_contact if self._scan_failed_contact is not None else -1,
                "point_stage": "aborted",
            }
        self.service()
        current_line = int(self._read_register("LineNumber"))
        completed = (current_line - sequence.baseline_line) & ((1 << 64) - 1)
        point_total = len(self._scan_grid)
        base_progress = self._scan_point / max(1, point_total)
        if self._scan_phase == "approach":
            if bool(self._read_register("Internal Pause")):
                if not self._scan_contact_confirmed(self._scan_point):
                    self._scan_failed_contact = self._scan_point
                    self._cancel_detail = f"Point {self._scan_point + 1} paused without a confirmed feedback event"
                    self.cancel_program()
                    self._cancel_detail = f"Point {self._scan_point + 1} paused without a confirmed feedback event"
                    return {"stage": "aborted", "detail": self._cancel_detail, "progress": base_progress,
                            "point_index": self._scan_point, "point_stage": "no-contact"}
                self.cancel_program()
                self._submit_scan_cv(self._scan_point)
                return {"stage": "cv", "detail": f"Point {self._scan_point + 1} of {point_total} · contact confirmed; CV submitted",
                        "progress": base_progress + 0.45 / max(1, point_total),
                        "point_index": self._scan_point, "point_stage": "cv"}
            if not self._submitted and self._hardware_complete:
                self._scan_failed_contact = self._scan_point
                self._cancelled = True
                self._cancel_detail = f"Point {self._scan_point + 1} reached End Z without contact; CV was not submitted"
                return {"stage": "aborted", "detail": self._cancel_detail, "progress": base_progress,
                        "point_index": self._scan_point, "point_stage": "no-contact"}
        elif self._scan_phase == "cv" and not self._submitted and self._hardware_complete:
            if self._scan_point + 1 >= point_total:
                self._scan_phase = "complete"
                return {"stage": "complete", "detail": "FPGA Scan Hopping + CV complete", "progress": 1.0,
                        "point_index": self._scan_point, "point_stage": "complete"}
            self._submit_scan_approach(self._scan_point + 1, initial=False)
            sequence = self._scan_sequence
            current_line = int(self._read_register("LineNumber"))
            completed = 0
        point_index, point_stage = self.scan_context(current_line)
        stage = "approaching" if point_stage == "approach" else "cv" if point_stage == "cv" else "retracting" if point_stage == "retract" else "preposition"
        phase_fraction = min(0.9, completed / max(1, len(sequence.descriptors)))
        return {
            "stage": stage,
            "detail": f"Point {point_index + 1} of {point_total} · {point_stage}",
            "progress": min(0.99, (max(0, point_index) + phase_fraction) / max(1, point_total)),
            "point_index": point_index,
            "point_stage": point_stage,
        }

    # Shared method interface used by standalone CV/Approach/IT and hopping IT.
    def _reset_method(self, name: str, parameters: object) -> None:
        self._method_name = name
        self._method_phase = ""
        self._method_params = parameters
        self._method_history.clear()
        self._method_sequence = None
        self._method_terminal = ""
        self._method_detail = ""
        self._method_contact_observed.clear()
        self._method_grid = []
        self._method_point = -1
        self._method_no_contact = False

    def _submit_method_plan(
        self,
        plan: list[PhysicalWaypoint],
        descriptors: list[tuple[int, str]],
        phase: str,
        *,
        resume: bool = False,
    ) -> None:
        compiled = self.compiler.compile(plan, self._current_targets())
        if len(compiled.waypoints) != len(descriptors):
            raise RuntimeError("Shared method descriptors do not match compiled waypoints.")
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)
        self._method_sequence = _ScanSequence(self._program_baseline, descriptors)
        self._method_history.append(self._method_sequence)
        self._method_phase = phase
        if resume:
            self._write_register("External Pause", False)

    def start_method(self, name: str, parameters: object) -> None:
        """Start one of the reusable, device-resident experimental methods."""
        if name not in {"cv", "approach", "approach_it", "scan_hopping_it"}:
            raise ValueError(f"Unknown shared FPGA method: {name}")
        errors = parameters.validate(self.settings)  # type: ignore[attr-defined]
        if errors:
            raise ValueError("; ".join(errors))
        self._prepare_command()
        baseline = int(self._read_register("LineNumber"))
        if isinstance(parameters, (ApproachITParameters, ScanHoppingITParameters)):
            hold_frames = sum(
                max(1, math.ceil(duration * 1_000_000 / 32767))
                for _potential, duration, _label in parameters.it_steps()
            )
            if isinstance(parameters, ScanHoppingITParameters):
                total_tags = 1 + parameters.point_count * (3 + hold_frames)
            else:
                total_tags = 2 + hold_frames + int(parameters.retract_after)
            if baseline < 0 or baseline + total_tags > 32767:
                raise ValueError(
                    "I-t line tags would exceed the verified signed-I16 acquisition range. "
                    "Reinitialize the target before running this method."
                )
        self._reset_method(name, parameters)
        self._owner = name.replace("_", "-")

        if name == "cv":
            if not isinstance(parameters, CVParameters):
                raise TypeError("cv requires CVParameters")
            plan = cyclic_voltammetry_plan(
                start_v=parameters.start_v, vertex1_v=parameters.vertex1_v,
                vertex2_v=parameters.vertex2_v, scan_rate_v_s=parameters.scan_rate_v_s,
                cycles=parameters.cycles, jump_at_start=parameters.jump_at_start,
            )
            self._submit_method_plan(plan, [(-1, "cv")] * len(plan), "cv")
            return

        if name == "scan_hopping_it":
            if not isinstance(parameters, ScanHoppingITParameters):
                raise TypeError("scan_hopping_it requires ScanHoppingITParameters")
            self._method_grid = parameters.grid()
            self._method_point = 0
            self._method_threshold = parameters.feedback_threshold
            self._method_greater_than = parameters.greater_than
            self.configure_feedback(FeedbackConfiguration.from_settings(
                self.settings, primary_channel="Current 1",
                primary_threshold=parameters.feedback_threshold,
                primary_greater_than=parameters.greater_than,
            ))
            self._submit_method_scan_approach(initial=True)
            return

        if not isinstance(parameters, (ApproachParameters, ApproachITParameters)):
            raise TypeError(f"{name} requires approach parameters")
        if parameters.feedback_channel != "Current 1":
            raise ValueError("The deployed USB-7856R profile supports Current 1 feedback for hardware approaches.")
        self._method_point = -1
        self._method_threshold = parameters.feedback_threshold
        self._method_greater_than = parameters.greater_than
        self.configure_feedback(FeedbackConfiguration.from_settings(
            self.settings, primary_channel=parameters.feedback_channel,
            primary_threshold=parameters.feedback_threshold,
            primary_greater_than=parameters.greater_than,
        ))
        xy_rate = max(10.0, parameters.approach_rate_um_s)
        plan = [PhysicalWaypoint(
            x_um=parameters.x_um, y_um=parameters.y_um, z_um=parameters.start_z_um,
            voltage1_v=parameters.approach_voltage_v,
            x_rate_um_s=xy_rate if parameters.x_um is not None else None,
            y_rate_um_s=xy_rate if parameters.y_um is not None else None,
            z_rate_um_s=parameters.retract_rate_um_s, jump_voltage1=True,
        ), PhysicalWaypoint(
            z_um=parameters.end_z_um, z_rate_um_s=parameters.approach_rate_um_s,
            feedback_action="pause_on_contact", update_interval_us=self._feedback_update_interval_us,
        )]
        self._submit_method_plan(plan, [(-1, "preposition"), (-1, "approach")], "approach")

    def _submit_method_scan_approach(self, *, initial: bool) -> None:
        params = self._method_params
        if not isinstance(params, ScanHoppingITParameters):
            raise RuntimeError("No hopping IT parameters are active.")
        _row, _column, x_um, y_um = self._method_grid[self._method_point]
        plan: list[PhysicalWaypoint] = []
        descriptors: list[tuple[int, str]] = []
        if initial:
            plan.append(PhysicalWaypoint(z_um=params.start_z_um, z_rate_um_s=params.retract_rate_um_s))
            descriptors.append((self._method_point, "retract"))
        plan.extend((
            PhysicalWaypoint(
                x_um=x_um, y_um=y_um, voltage1_v=params.approach_voltage_v,
                x_rate_um_s=params.lateral_rate_um_s, y_rate_um_s=params.lateral_rate_um_s,
                jump_voltage1=True,
            ),
            PhysicalWaypoint(
                z_um=params.end_z_um, z_rate_um_s=params.approach_rate_um_s,
                feedback_action="pause_on_contact", update_interval_us=self._feedback_update_interval_us,
            ),
        ))
        descriptors.extend(((self._method_point, "positioning"), (self._method_point, "approach")))
        self._submit_method_plan(plan, descriptors, "approach", resume=not initial)

    def _submit_method_it(self, params: ApproachITParameters | ScanHoppingITParameters, point: int) -> None:
        plan, labels = potential_step_plan(params.it_steps())
        descriptors = [(point, f"it:{label}") for label in labels]
        should_retract = params.retract_after if isinstance(params, ApproachITParameters) else True
        if should_retract:
            plan.append(PhysicalWaypoint(z_um=params.start_z_um, z_rate_um_s=params.retract_rate_um_s))
            descriptors.append((point, "retract"))
        self._submit_method_plan(plan, descriptors, "it", resume=True)

    def _submit_method_retract(self, params: ApproachParameters | ApproachITParameters | ScanHoppingITParameters) -> None:
        point = self._method_point
        self._submit_method_plan(
            [PhysicalWaypoint(z_um=params.start_z_um, z_rate_um_s=params.retract_rate_um_s)],
            [(point, "retract")], "retract", resume=True,
        )

    def _method_contact_confirmed(self, point: int) -> bool:
        return (
            point in self._method_contact_observed
            or bool(self._read_register("Feedback1 Boolean"))
            or bool(self._read_register("StopMoveZ"))
        )

    def _method_finish_approach(self, contact: bool) -> None:
        params = self._method_params
        point = self._method_point
        if contact:
            self.cancel_program()
            self._cancelled = False
            if self._method_name == "approach":
                assert isinstance(params, ApproachParameters)
                if params.retract_after:
                    self._submit_method_retract(params)
                else:
                    self._method_terminal = "complete"
                    self._method_detail = "Contact detected; probe remains at the contact position"
                    self._execution_state = ExecutionState.COMPLETE
            elif self._method_name == "approach_it":
                assert isinstance(params, ApproachITParameters)
                self._submit_method_it(params, -1)
            else:
                assert isinstance(params, ScanHoppingITParameters)
                self._submit_method_it(params, point)
            return

        self._method_no_contact = True
        self._method_detail = "End Z reached without a confirmed feedback crossing"
        if isinstance(params, (ApproachParameters, ApproachITParameters)) and not params.retract_after:
            self._method_terminal = "aborted"
            return
        assert isinstance(params, (ApproachParameters, ApproachITParameters, ScanHoppingITParameters))
        self._submit_method_retract(params)

    def method_context(self, line_number: int) -> tuple[int, str]:
        for sequence in reversed(self._method_history):
            index = self._sample_line_delta(line_number, sequence.baseline_line)
            if index < len(sequence.descriptors):
                return sequence.descriptors[index]
        return -1, ""

    def method_status(self) -> dict[str, str | float | int]:
        if not self._method_name:
            return {"stage": "aborted", "detail": "No shared FPGA method is active", "progress": 0.0, "point_index": -1}
        if self._cancelled:
            self._method_terminal = "aborted"
            self._method_detail = self._cancel_detail or "FPGA method cancelled by operator"
        if self._method_terminal:
            return {
                "stage": self._method_terminal, "detail": self._method_detail,
                "progress": 1.0 if self._method_terminal == "complete" else 0.0,
                "point_index": self._method_point, "point_stage": self._method_terminal,
            }
        self.service()
        if self._method_phase == "approach":
            if bool(self._read_register("Internal Pause")):
                if not self._method_contact_confirmed(self._method_point):
                    self.cancel_program()
                    self._cancelled = False
                    self._method_terminal = "aborted"
                    self._method_detail = "FPGA paused without a confirmed feedback crossing"
                else:
                    self._method_finish_approach(True)
                    return {
                        "stage": "contact", "detail": "Contact confirmed; follow-up submitted",
                        "progress": 0.4, "point_index": self._method_point, "point_stage": "contact",
                    }
            elif not self._submitted and self._hardware_complete:
                self._method_finish_approach(False)
        elif not self._submitted and self._hardware_complete:
            if self._method_name == "scan_hopping_it" and self._method_phase == "it" and not self._method_no_contact:
                if self._method_point + 1 < len(self._method_grid):
                    self._method_point += 1
                    self._submit_method_scan_approach(initial=False)
                else:
                    self._method_terminal = "complete"
                    self._method_detail = f"Hopping I-t scan complete · {len(self._method_grid)} points"
            elif self._method_phase == "retract" and self._method_no_contact:
                self._method_terminal = "aborted"
                self._method_detail = "End Z reached without contact; probe retracted"
            else:
                self._method_terminal = "complete"
                label = {"cv": "CV complete", "approach": "Approach complete", "approach_it": "Approach + I-t complete"}.get(
                    self._method_name, "Method complete"
                )
                self._method_detail = label

        point, point_stage = self.method_context(int(self._read_register("LineNumber")))
        total_points = max(1, len(self._method_grid))
        local = self.execution_status().progress
        progress = local if self._method_name != "scan_hopping_it" else min(0.99, (max(0, self._method_point) + local) / total_points)
        stage = "approaching" if self._method_phase == "approach" else "retracting" if self._method_phase == "retract" else self._method_phase
        if point_stage == "retract":
            stage = "retracting"
        return {
            "stage": self._method_terminal or stage,
            "detail": self._method_detail or f"{self._method_name.replace('_', ' ').title()} · {point_stage or stage}",
            "progress": 1.0 if self._method_terminal == "complete" else progress,
            "point_index": point if point >= 0 else self._method_point,
            "point_stage": point_stage or stage,
        }

    def read_samples(self) -> list[Sample]:
        """Drain a FIFO snapshot before exposing a completed program.

        Acquisition is continuous: this is a bounded snapshot, not an assertion
        that the FPGA sampling loop has stopped. Keep descriptors for callers
        tagging the samples returned here and any subsequent idle samples.
        """
        self.service()
        available = int(self.data_fifo.read(number_of_elements=0, timeout_ms=0).elements_remaining)
        count = available - available % SAMPLE_WORDS
        if count <= 0:
            if self._hardware_complete and available == 0:
                self._submitted = False
                self._program_deadline = None
                self._execution_state = ExecutionState.COMPLETE
                self._execution_detail = "Program complete; final FIFO snapshot drained"
            return []
        data = list(self.data_fifo.read(number_of_elements=count, timeout_ms=0).data)
        if len(data) != count:
            self._stopped = True
            self._program_deadline = None
            self._write_register("External Pause", True)
            raise RuntimeError(f"FPGA data FIFO returned {len(data)} of {count} requested words; sample integrity is uncertain.")
        try:
            samples = [self.decoder.decode(data[offset : offset + SAMPLE_WORDS]) for offset in range(0, len(data), SAMPLE_WORDS)]
        except Exception:
            self._stopped = True
            self._program_deadline = None
            self._write_register("External Pause", True)
            raise
        self._observe_contacts(samples)
        if self._hardware_complete and available % SAMPLE_WORDS == 0:
            self._submitted = False
            self._program_deadline = None
            self._execution_state = ExecutionState.COMPLETE
            self._execution_detail = "Program complete; final FIFO snapshot drained"
        return samples

    def execution_status(self) -> ExecutionSnapshot:
        current = int(self._read_register("LineNumber")) if self._started else self._program_baseline
        executed = min(self._program_total, (current - self._program_baseline) & ((1 << 64) - 1))
        return ExecutionSnapshot(
            self._owner,
            ExecutionState.ERROR if self._stopped else self._execution_state,
            self.streamer.submitted_waypoints,
            executed,
            self.streamer.pending_waypoints,
            self._program_total,
            self._execution_detail,
        )

    def pause(self) -> None:
        self._check_target_health()
        self._write_register("External Pause", True)
        if not bool(self._read_register("External Pause")):
            raise RuntimeError("FPGA did not acknowledge pause")
        self._execution_state = ExecutionState.PAUSED
        self._execution_detail = "Paused by operator"

    def resume(self) -> None:
        self._check_target_health()
        # Internal Pause belongs to FPGA feedback logic and is never cleared by
        # a generic resume command; EndCurrentLine or the method state machine
        # must resolve it deliberately.
        if bool(self._read_register("Internal Pause")):
            raise RuntimeError("FPGA feedback pause is active; use End current waypoint or stop the method")
        self._write_register("External Pause", False)
        if bool(self._read_register("External Pause")):
            raise RuntimeError("FPGA did not acknowledge resume")
        self._execution_state = ExecutionState.RUNNING
        self._execution_detail = "Resumed by operator"

    def end_current_waypoint(self) -> None:
        self._check_target_health()
        self._write_register("EndCurrentLine", True)
        self._write_register("EndCurrentLine", False)
        self._execution_detail = "Requested transition to the next FPGA waypoint"

    def _feedback_hit(self, current_na: float, threshold_na: float, greater_than: bool) -> bool:
        return current_na >= threshold_na if greater_than else current_na <= threshold_na

    def _raw_z_tolerance(self) -> int:
        multiplier = 2.0 if self.settings.z_bipolar else 1.0
        return max(2, round(0.08 * 32768.0 * multiplier / self.settings.z_range_um))

    def _approach_contact_confirmed(self) -> bool:
        return (
            self._approach_contact_observed
            or bool(self._read_register("Feedback1 Boolean"))
            or bool(self._read_register("StopMoveZ"))
        )

    def _scan_contact_confirmed(self, point: int) -> bool:
        return (
            point in self._scan_contact_observed
            or bool(self._read_register("Feedback1 Boolean"))
            or bool(self._read_register("StopMoveZ"))
        )

    def _observe_contacts(self, samples: list[Sample]) -> None:
        previous_scan: tuple[int, str] | None = None
        for sample in samples:
            approach_stage = self.approach_context(sample.line_number)
            if approach_stage == "approach" and self._feedback_hit(
                sample.current1_na, self._approach_threshold_na, self._approach_greater_than
            ):
                self._approach_contact_observed = True
            point, scan_stage = self.scan_context(sample.line_number)
            if point >= 0 and scan_stage == "approach":
                self._scan_last_approach_z[point] = sample.z_um
                if self._feedback_hit(sample.current1_na, self._scan_threshold_na, self._scan_greater_than):
                    self._scan_contact_observed.add(point)
            if previous_scan is not None and previous_scan[0] == point and previous_scan[1] == "approach" and scan_stage == "cv":
                if not self._scan_contact_confirmed(point):
                    self._scan_failed_contact = point
            previous_scan = (point, scan_stage)
            method_point, method_stage = self.method_context(sample.line_number)
            if method_stage == "approach" and self._feedback_hit(
                sample.current1_na, self._method_threshold, self._method_greater_than
            ):
                self._method_contact_observed.add(method_point)

    def stop_motion(self) -> None:
        """Safely cancel the active program and keep the healthy session reusable."""
        self.cancel_program()

    def emergency_stop(self) -> None:
        """Latch both host-side and FPGA-side stops and verify their controls."""
        self._stopped = True
        self._program_deadline = None
        errors: list[str] = []
        for name in ("External Pause", "External Stop"):
            try:
                self._write_register(name, True)
            except Exception as exc:
                errors.append(f"could not assert {name}: {exc}")
        for name in ("External Pause", "External Stop"):
            try:
                if not bool(self._read_register(name)):
                    errors.append(f"{name} did not read back as asserted")
            except Exception as exc:
                errors.append(f"could not verify {name}: {exc}")
        self._sequence = None
        self._scan_sequence = None
        if errors:
            raise RuntimeError("Emergency stop state is uncertain: " + "; ".join(errors))


def create_driver(session: Any, settings: AppSettings) -> WECSPMDriver:
    return WECSPMDriver(session, settings)
