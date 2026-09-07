from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
import time
from typing import Any

from .host import ExecutionSnapshot, ExecutionState, WaypointStreamer
from .models import (
    AppSettings, ApproachCVParameters, ApproachITParameters, ApproachParameters,
    CVParameters, FeedbackConfiguration, Sample, ScanHoppingCVParameters, ScanHoppingITParameters,
    hold_frame_count,
)
from .ni_protocol import (
    DEPLOYED_STARTUP_RAW_OUTPUTS,
    FPGA_TICKS_PER_US,
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
    raw_to_current,
    raw_voltage1_velocity_per_tick,
    scale_velocity,
    select_velocity_exponent,
    voltage1_to_raw,
)
from .waypoints import (
    CompiledWaypoints, PhysicalWaypoint, WaypointCompiler,
    cyclic_voltammetry_plan, potential_step_plan, timed_hold_plan,
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

    BASELINE_HOLD_US = 25_000
    BASELINE_SAMPLE_COUNT = 16

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
        self._ending_waypoint = False
        self._end_request_line = 0
        self._end_request_deadline: float | None = None
        self._framing_valid = True
        self._retained_samples: list[Sample] = []
        self._deferred_samples: list[Sample] = []
        self._last_snapshot_remainder = 0
        self._program_deadline: float | None = None
        self._pause_started_at: float | None = None
        self._expected_duration_s = 0.0
        self._pending_scalers: tuple[int, ...] = ()
        self._program_waypoints: list[Waypoint] = []
        self._cancelled = False
        self._cancel_detail = ""
        self._approach_threshold_na = 0.0
        self._approach_feedback_channel = "Current 1"
        self._approach_greater_than = True
        self._approach_feedback_mode = "absolute"
        self._approach_feedback_baseline: float | None = None
        self._approach_baseline_samples: list[float] = []
        self._approach_pending_waypoint: Waypoint | None = None
        self._approach_pending_duration_s = 0.0
        self._approach_end_z_raw: int | None = None
        self._approach_contact_observed = False
        self._approach_manually_accepted = False
        self._scan_threshold_na = 0.0
        self._scan_feedback_channel = "Current 1"
        self._scan_greater_than = True
        self._scan_feedback_mode = "absolute"
        self._scan_feedback_baseline: dict[int, float] = {}
        self._scan_baseline_samples: dict[int, list[float]] = {}
        self._scan_pending_waypoint: Waypoint | None = None
        self._scan_pending_duration_s = 0.0
        self._scan_end_z_raw: int | None = None
        self._scan_contact_observed: set[int] = set()
        self._scan_manually_accepted: set[int] = set()
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
        self._method_feedback_channel = "Current 1"
        self._method_greater_than = True
        self._method_feedback_mode = "absolute"
        self._method_feedback_baseline: dict[int, float] = {}
        self._method_baseline_samples: dict[int, list[float]] = {}
        self._method_pending_approach: PhysicalWaypoint | None = None
        self._method_contact_observed: set[int] = set()
        self._method_manually_accepted: set[int] = set()
        self._method_last_approach_z: dict[int, float] = {}
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
    def _sample_waypoint_index(sample_line_i16: int, baseline_u64: int) -> int:
        """Map an FPGA sample tag to its zero-based waypoint descriptor.

        FPGA Target.vi increments LineNumber after reading a waypoint and
        before capturing its samples.  The idle tag therefore equals the
        submission baseline, and the first waypoint is tagged baseline + 1.
        """
        delta = (int(sample_line_i16) - (int(baseline_u64) & 0xFFFF)) & 0xFFFF
        return delta - 1 if delta else -1

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
        self._write_register("Buffer Loop Wait Time (tICKS)", self.settings.sample_time_us * FPGA_TICKS_PER_US)
        # Waypoint hold values are encoded as microseconds.  The target
        # multiplies each I16 hold word by this U64 scale before starting its
        # 40 MHz tick timer, so never rely on the bitfile's serialized default.
        self._write_register("HoldTimerScale", FPGA_TICKS_PER_US)
        if int(self._read_register("HoldTimerScale")) != FPGA_TICKS_PER_US:
            raise RuntimeError("FPGA rejected the required 40 ticks/us hold-timer scale.")
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
            self._write_register("External Pause", False)
        except Exception:
            # A timed-out FIFO write can have transferred a prefix. Never
            # allow another program to resume that uncertain queue.
            self._stopped = True
            self._program_deadline = None
            self._pause_started_at = None
            raise
        self._program_total = len(waypoints)
        self._program_waypoints = list(waypoints)
        self._hardware_complete = False
        self._ending_waypoint = False
        self._end_request_deadline = None
        self._pause_started_at = None
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

    def _account_pause_state(self, paused: bool, *, now: float | None = None) -> None:
        """Exclude acknowledged target pauses from the program watchdog."""
        if not self._submitted:
            self._pause_started_at = None
            return
        observed_at = time.monotonic() if now is None else now
        if paused:
            if self._pause_started_at is None:
                self._pause_started_at = observed_at
            return
        if self._pause_started_at is None:
            return
        if self._program_deadline is not None:
            self._program_deadline += max(0.0, observed_at - self._pause_started_at)
        self._pause_started_at = None

    def _latch_command_timeout(self, detail: str) -> None:
        self._stopped = True
        self._program_deadline = None
        self._pause_started_at = None
        self._execution_state = ExecutionState.ERROR
        self._execution_detail = detail
        try:
            self._write_register("External Pause", True)
        finally:
            try:
                self._write_register("EndCurrentLine", False)
            except Exception:
                pass

    def _potential_target(self, channel: int, voltage: float) -> tuple[str, str, str, int]:
        if channel not in (1, 2) or not math.isfinite(voltage) or not -10.0 <= voltage <= 10.0:
            raise ValueError("Potential output must be channel 1 or 2 and within +/-10 V.")
        if channel == 1 and abs(voltage * self.settings.command_voltage_ratio) > 10.0:
            raise ValueError("Potential 1 exceeds AO3 after applying its command ratio.")
        raw = voltage1_to_raw(voltage, self.settings.command_voltage_ratio if channel == 1 else 1.0)
        return (
            "Applied Voltage" if channel == 1 else "Applied Voltage 2",
            "V on Fly" if channel == 1 else "V2 on Fly",
            "Change V on Fly" if channel == 1 else "Change V on Fly 2",
            raw,
        )

    def _wait_for_idle_potential(self, channel: int, target_raw: int) -> None:
        applied_name = "Applied Voltage" if channel == 1 else "Applied Voltage 2"
        deadline = time.monotonic() + self.settings.hardware_ready_timeout_s
        while time.monotonic() < deadline:
            self.service()
            try:
                self._deferred_samples.extend(self._read_complete_sample_snapshot())
            except Exception:
                self._latch_command_timeout("Potential command acquisition drain failed; reinitialize the target")
                raise
            if self._hardware_complete and int(self._read_register(applied_name)) == target_raw:
                self._submitted = False
                self._program_deadline = None
                self._pause_started_at = None
                self._execution_state = ExecutionState.COMPLETE
                self._execution_detail = f"Potential {channel} command acknowledged by applied-output readback"
                return
            time.sleep(0.002)
        detail = (
            f"FPGA did not acknowledge Potential {channel} at the requested applied value within "
            f"{self.settings.hardware_ready_timeout_s:g} s; reinitialize the target before continuing"
        )
        self._latch_command_timeout(detail)
        raise TimeoutError(detail)

    def set_voltage(self, channel: int, voltage: float) -> None:
        """Apply an idle potential through an acknowledged jump waypoint."""
        _applied_name, _value_name, _trigger_name, target_raw = self._potential_target(channel, voltage)
        self._prepare_command()
        current = self._current_targets()
        plan = PhysicalWaypoint(
            voltage1_v=voltage if channel == 1 else None,
            voltage2_v=voltage if channel == 2 else None,
            jump_voltage1=channel == 1,
            jump_voltage2=channel == 2,
        )
        compiled = self.compiler.compile([plan], current)
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        self._owner = f"potential-{channel}"
        self._sequence = None
        self._scan_sequence = None
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)
        self._wait_for_idle_potential(channel, target_raw)

    def set_live_potential(self, channel: int, voltage: float) -> None:
        """Apply and acknowledge ChangeOnFly only inside its active axis loop."""
        applied_name, value_name, trigger_name, target_raw = self._potential_target(channel, voltage)
        if not self._submitted:
            self.set_voltage(channel, voltage)
            return
        self._check_target_health()
        if bool(self._read_register("External Pause")) or bool(self._read_register("Internal Pause")):
            raise RuntimeError("Resume the FPGA program before applying an on-the-fly potential change.")
        current_line = int(self._read_register("LineNumber"))
        index = ((current_line - self._program_baseline) & ((1 << 64) - 1)) - 1
        if not 0 <= index < len(self._program_waypoints):
            raise RuntimeError("The FPGA has not acknowledged an active waypoint for an on-the-fly potential change.")
        waypoint = self._program_waypoints[index]
        moves_channel = waypoint.move_v if channel == 1 else waypoint.move_v2
        if not moves_channel:
            raise RuntimeError(f"Potential {channel} is not active in the current FPGA waypoint.")
        if int(self._read_register(applied_name)) == target_raw:
            return
        self._write_register(value_name, target_raw)
        self._write_register(trigger_name, True)
        if not bool(self._read_register(trigger_name)):
            raise RuntimeError(f"FPGA did not latch the Potential {channel} on-the-fly request.")
        deadline = time.monotonic() + self.settings.hardware_ready_timeout_s
        acknowledged = False
        try:
            while time.monotonic() < deadline:
                self.service()
                if int(self._read_register(applied_name)) == target_raw:
                    acknowledged = True
                    break
                time.sleep(0.002)
        finally:
            self._write_register(trigger_name, False)
        if bool(self._read_register(trigger_name)):
            self._latch_command_timeout(
                f"FPGA did not release the Potential {channel} on-the-fly request; reinitialize the target"
            )
            raise RuntimeError(self._execution_detail)
        if not acknowledged:
            detail = (
                f"FPGA did not acknowledge Potential {channel} through its applied-output readback within "
                f"{self.settings.hardware_ready_timeout_s:g} s; reinitialize the target before continuing"
            )
            self._latch_command_timeout(detail)
            raise TimeoutError(detail)

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

    def verify_startup_state(self) -> dict[str, int | bool]:
        """Verify the hard-coded FPGA startup frame before enabling commands."""
        expected: dict[str, int | bool] = {
            **DEPLOYED_STARTUP_RAW_OUTPUTS,
            "LineNumber": 0,
            "External Pause": True,
            "External Stop": False,
            "Internal Pause": False,
            "Internal Stop": False,
            "EndCurrentLine": False,
            "WaitingForWayPoints": True,
        }
        observed = {name: self._read_register(name) for name in expected}
        mismatches = [
            f"{name}={observed[name]!r} (expected {value!r})"
            for name, value in expected.items()
            if observed[name] != value
        ]
        if mismatches:
            detail = "Unexpected FPGA startup state: " + "; ".join(mismatches)
            try:
                self.emergency_stop()
            except Exception as exc:
                detail += f"; emergency-stop verification also failed: {exc}"
            raise RuntimeError(detail)
        return observed

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
        """Abort motion without ever decoding across an External Stop edge.

        The deployed target checks External Stop separately for every word in
        its 14-word acquisition frame. A stop can therefore leave a partial
        frame. Complete frames already available are retained, all words after
        the stop edge are discarded, and this driver is latched until a new
        FPGA session establishes a fresh frame boundary.
        """
        if self._stopped:
            raise ValueError("Motion was stopped by a fatal fault. Reinitialize the target before another command.")
        self._check_target_health()
        if not self._submitted:
            self._write_register("External Pause", True)
            return
        self._execution_state = ExecutionState.CANCELLING
        self._execution_detail = "Cancelling active FPGA program and retiring its acquisition stream"
        snapshot_error: Exception | None = None
        try:
            self._retained_samples.extend(self._read_complete_sample_snapshot())
        except Exception as exc:
            # Cancellation must still reach the physical target even when the
            # last pre-stop snapshot cannot be trusted.
            snapshot_error = exc
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
            self._discard_acquisition_words()
        except Exception:
            self._stopped = True
            self._program_deadline = None
            self._pause_started_at = None
            try:
                self._write_register("External Pause", True)
            except Exception:
                pass
            raise
        self._submitted = False
        self._hardware_complete = False
        self._ending_waypoint = False
        self._end_request_deadline = None
        self._program_deadline = None
        self._pause_started_at = None
        self._cancelled = True
        self._framing_valid = False
        self._stopped = True
        self._cancel_detail = "FPGA program cancelled safely; reconnect before another command"
        self._execution_state = ExecutionState.ABORTED
        self._execution_detail = self._cancel_detail
        if snapshot_error is not None:
            raise RuntimeError(
                f"FPGA motion was cancelled, but the final pre-stop snapshot was invalid: {snapshot_error}. "
                "Reconnect before another command."
            ) from snapshot_error

    def _read_complete_sample_snapshot(self) -> list[Sample]:
        available = int(self.data_fifo.read(number_of_elements=0, timeout_ms=0).elements_remaining)
        self._last_snapshot_remainder = available % SAMPLE_WORDS
        count = available - available % SAMPLE_WORDS
        if count <= 0:
            return []
        data = list(self.data_fifo.read(number_of_elements=count, timeout_ms=0).data)
        if len(data) != count:
            raise RuntimeError(
                f"FPGA data FIFO returned {len(data)} of {count} requested words; sample integrity is uncertain."
            )
        samples = [
            self.decoder.decode(data[offset : offset + SAMPLE_WORDS])
            for offset in range(0, len(data), SAMPLE_WORDS)
        ]
        self._observe_contacts(samples)
        return samples

    def _discard_acquisition_words(self) -> None:
        """Discard every post-stop word, including any incomplete frame."""
        while True:
            available = int(self.data_fifo.read(number_of_elements=0, timeout_ms=0).elements_remaining)
            if available <= 0:
                return
            self.data_fifo.read(number_of_elements=available, timeout_ms=0)

    def _service_end_request(self, current_line: int, waiting: bool) -> None:
        if not self._ending_waypoint:
            return
        advanced = ((current_line - self._end_request_line) & ((1 << 64) - 1)) > 0
        if not (advanced or waiting):
            if self._end_request_deadline is not None and time.monotonic() > self._end_request_deadline:
                detail = (
                    "FPGA did not acknowledge EndCurrentLine through line advancement or its waiting state; "
                    "reinitialize the target before continuing"
                )
                self._ending_waypoint = False
                self._end_request_deadline = None
                self._latch_command_timeout(detail)
                raise TimeoutError(detail)
            return
        # WaitingForWayPoints or a changed line number proves that the active
        # axis loops consumed the level-held request. Only now may it be reset.
        if waiting and bool(self._read_register("Internal Pause")):
            self._write_register("Internal Pause", False)
        self._write_register("EndCurrentLine", False)
        self._ending_waypoint = False
        self._end_request_deadline = None

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
                    self._pause_started_at = None
                    self._execution_state = ExecutionState.ERROR
                    self._execution_detail = f"Waypoint refill failed: {exc}"
                    self._write_register("External Pause", True)
                    raise RuntimeError(self._execution_detail) from exc
            current = int(self._read_register("LineNumber"))
            delta = (current - self._program_baseline) & ((1 << 64) - 1)
            if delta > self._program_total:
                self._stopped = True
                self._program_deadline = None
                self._pause_started_at = None
                self._write_register("External Pause", True)
                raise RuntimeError(
                    f"FPGA line counter advanced by {delta} for a {self._program_total}-waypoint program. "
                    "Motion was paused because target/host state is inconsistent."
                )
            waiting = bool(self._read_register("WaitingForWayPoints"))
            paused_before_ack = bool(self._read_register("External Pause")) or bool(self._read_register("Internal Pause"))
            self._account_pause_state(paused_before_ack)
            self._service_end_request(current, waiting)
            paused = bool(self._read_register("External Pause")) or bool(self._read_register("Internal Pause"))
            self._account_pause_state(paused)
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
                and not paused
                and time.monotonic() > self._program_deadline
            ):
                self._stopped = True
                try:
                    self._write_register("External Pause", True)
                finally:
                    self._program_deadline = None
                    self._pause_started_at = None
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
        if channel in {"Current 1", "Current 2"}:
            index = int(channel.rsplit(" ", 1)[1])
            sensitivity = getattr(self.settings, f"current{index}_v_per_na")
            if abs(value * sensitivity) > 10:
                raise ValueError(f"{channel} feedback threshold exceeds its +/-10 V ADC range.")
            return current_to_raw(value, sensitivity)
        raise ValueError(f"Unsupported feedback channel: {channel}")

    def configure_feedback(self, config: FeedbackConfiguration) -> None:
        """Configure the selected contact current and neutralize unused FPGA modes."""
        if config.primary_channel not in FEEDBACK_SIGNAL_CODES:
            raise ValueError("Unsupported feedback signal selection.")
        if not 0 <= config.update_interval_us <= 32767:
            raise ValueError("Feedback update interval must be 0..32767 us.")
        primary_raw = self._feedback_value_to_raw(config.primary_channel, config.primary_threshold)
        # The deployed bitfile still exposes legacy advanced-feedback
        # registers. Write fixed neutral values so stale target state cannot
        # activate a mode that eChemTips does not support.
        writes = (
            ("FeedBackType", FEEDBACK_SIGNAL_CODES[config.primary_channel]),
            ("Feedback_Threshold", primary_raw),
            ("GreaterThan", bool(config.primary_greater_than)),
            ("FeedBackType 2", FEEDBACK_SIGNAL_CODES["Current 2"]),
            ("Feedback_Threshold 2", 0),
            ("GreaterThan 2", True),
            ("P", 0.0),
            ("Upper limit Of dZ", 10),
            ("P2AvgWhole", 1),
            ("P2AvgMinus", 0),
            ("Feedback1 on  Hold", False),
            ("DistanceToBulk", 0),
            ("DistanceToBulk 2", 0),
            ("DistanceToBulk 3", 0),
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

    def _configure_contact_feedback(
        self,
        channel: str,
        threshold: float,
        greater_than: bool,
        mode: str,
        *,
        baseline: float | None = None,
    ) -> float:
        """Configure the target's verified absolute pause-on-contact path.

        Baseline-relative mode is implemented by measuring a stationary host
        baseline first and translating the requested delta into an absolute
        threshold. This intentionally avoids FPGA line type 8, whose deployed
        algorithm and completion behavior differ from eChemTips semantics.
        """
        effective_threshold = threshold
        if mode == "baseline_relative":
            signed_delta = abs(threshold) if greater_than else -abs(threshold)
            effective_threshold = signed_delta if baseline is None else baseline + signed_delta
        self.configure_feedback(FeedbackConfiguration.from_settings(
            self.settings,
            primary_channel=channel,
            primary_threshold=effective_threshold,
            primary_greater_than=greater_than,
        ))
        return effective_threshold

    def _stationary_baseline(self, channel: str, values: list[float]) -> float:
        if values:
            return statistics.fmean(values[-self.BASELINE_SAMPLE_COUNT :])
        index = int(channel[-1])
        register = "MeasuredCurrent" if index == 1 else "MeasuredCurrent 2"
        sensitivity = getattr(self.settings, f"current{index}_v_per_na")
        return raw_to_current(int(self._read_register(register)), sensitivity)

    def _baseline_hold_waypoint(self, targets: dict[str, int]) -> Waypoint:
        return Waypoint(
            x_position=targets["X"], y_position=targets["Y"], z_position=targets["Z"],
            v_position=targets["V"], v2_position=targets["V2"],
            hold=True, hold_timer=self.BASELINE_HOLD_US,
        )

    def start_approach_cv(self, params: ApproachCVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("; ".join(errors))
        if 1 + 3 * params.cycles + int(params.retract_after) > 65535:
            raise ValueError("Reduce CV cycles: the streamed CV exceeds the verified line-tag span.")
        feedback_index = int(params.feedback_channel[-1])
        if abs(params.feedback_threshold_na * getattr(self.settings, f"current{feedback_index}_v_per_na")) > 10:
            raise ValueError("Feedback threshold exceeds the selected current input's +/-10 V ADC range.")
        if any(
            abs(voltage * self.settings.command_voltage_ratio) > 10
            for voltage in (params.approach_voltage_v, params.cv_start_v, params.cv_vertex1_v, params.cv_vertex2_v)
        ):
            raise ValueError("A requested potential exceeds AO3 after applying the command-voltage ratio.")
        self._prepare_command()
        baseline_line = int(self._read_register("LineNumber"))
        total_tags = (
            2 + int(params.feedback_mode == "baseline_relative")
            + hold_frame_count(params.settling_time_s) + 1 + 3 * params.cycles + int(params.retract_after)
        )
        if baseline_line < 0 or baseline_line + total_tags > 32767:
            raise ValueError(
                "Approach + CV line tags would exceed the verified signed-I16 acquisition range. "
                "Reinitialize the target before running this method."
            )
        self._owner = "approach-cv"
        current = self._current_targets()
        s = self.settings
        xy_speed = max(10.0, params.approach_rate_um_s)
        x_speeds = [raw_position_velocity_per_tick(xy_speed, s.x_range_um, s.x_bipolar)] if params.x_um is not None else []
        y_speeds = [raw_position_velocity_per_tick(xy_speed, s.y_range_um, s.y_bipolar)] if params.y_um is not None else []
        z_speeds = [max(10.0, params.approach_rate_um_s), params.approach_rate_um_s]
        z_raw = [raw_position_velocity_per_tick(speed, s.z_range_um, s.z_bipolar) for speed in z_speeds]
        ex, ey, ez, ev = self._set_scalers(x_speeds, y_speeds, z_raw, [])
        x_velocity = scale_velocity(x_speeds[0], ex) if x_speeds else 0
        y_velocity = scale_velocity(y_speeds[0], ey) if y_speeds else 0
        z_fast = scale_velocity(z_raw[0], ez)
        z_approach = scale_velocity(z_raw[1], ez)
        target_x = position_to_raw(params.x_um, s.x_range_um, s.x_bipolar) if params.x_um is not None else current["X"]
        target_y = position_to_raw(params.y_um, s.y_range_um, s.y_bipolar) if params.y_um is not None else current["Y"]
        start_z = position_to_raw(params.start_z_um, s.z_range_um, s.z_bipolar)
        end_z = position_to_raw(params.end_z_um, s.z_range_um, s.z_bipolar)
        approach_v = voltage1_to_raw(params.approach_voltage_v, s.command_voltage_ratio)

        common = dict(x_position=target_x, y_position=target_y, v2_position=current["V2"])
        preposition = Waypoint(
                **common, z_position=start_z, v_position=approach_v,
                x_velocity=x_velocity, y_velocity=y_velocity, z_velocity=z_fast,
                move_x=params.x_um is not None, move_y=params.y_um is not None,
                move_z=True, move_v=True, jump_v=True,
            )
        approach = Waypoint(
            # Pause-on-contact is deliberately used instead of advance-on-contact:
            # Python validates the feedback/pause state before it ever submits CV.
                **common,
                line_type=FEEDBACK_ACTION_CODES["pause_on_contact"],
                z_position=end_z,
                v_position=approach_v,
                z_velocity=z_approach,
                update_wait_us=self._feedback_update_interval_us,
                move_z=True,
            )
        effective_threshold = self._configure_contact_feedback(
            params.feedback_channel, params.feedback_threshold_na, params.greater_than, params.feedback_mode,
        )
        current_z = raw_to_position(current["Z"], s.z_range_um, s.z_bipolar)
        preposition_duration = max(
            abs(current_z - params.start_z_um) / z_speeds[0],
            abs(raw_to_position(current["X"], s.x_range_um, s.x_bipolar) - params.x_um) / xy_speed if params.x_um is not None else 0.0,
            abs(raw_to_position(current["Y"], s.y_range_um, s.y_bipolar) - params.y_um) / xy_speed if params.y_um is not None else 0.0,
        )
        approach_duration = abs(params.end_z_um - params.start_z_um) / params.approach_rate_um_s
        if params.feedback_mode == "baseline_relative":
            baseline_targets = {"X": target_x, "Y": target_y, "Z": start_z, "V": approach_v, "V2": current["V2"]}
            waypoints = [preposition, self._baseline_hold_waypoint(baseline_targets)]
            contexts = ["preposition", "baseline"]
            expected_duration = preposition_duration + self.BASELINE_HOLD_US / 1_000_000.0
            phase = "baseline"
            self._approach_pending_waypoint = approach
            self._approach_pending_duration_s = approach_duration
        else:
            waypoints = [preposition, approach]
            contexts = ["preposition", "approach"]
            expected_duration = preposition_duration + approach_duration
            phase = "approach"
            self._approach_pending_waypoint = None
            self._approach_pending_duration_s = 0.0
        self._enqueue(waypoints, expected_duration)
        self._sequence = _Sequence(self._program_baseline, len(waypoints), 2, 1, None)
        self._approach_history = [(self._program_baseline, contexts)]
        self._approach_params = params
        self._approach_phase = phase
        self._scan_sequence = None
        self._approach_threshold_na = (
            effective_threshold if params.feedback_mode == "absolute"
            else (abs(params.feedback_threshold_na) if params.greater_than else -abs(params.feedback_threshold_na))
        )
        self._approach_feedback_channel = params.feedback_channel
        self._approach_greater_than = params.greater_than
        self._approach_feedback_mode = params.feedback_mode
        self._approach_feedback_baseline = None
        self._approach_baseline_samples.clear()
        self._approach_end_z_raw = end_z
        self._approach_contact_observed = False
        self._approach_manually_accepted = False

    def _submit_approach_cv_contact_motion(self) -> float:
        params = self._approach_params
        waypoint = self._approach_pending_waypoint
        if params is None or waypoint is None:
            raise RuntimeError("No baseline-relative approach is awaiting submission.")
        baseline = self._stationary_baseline(params.feedback_channel, self._approach_baseline_samples)
        self._configure_contact_feedback(
            params.feedback_channel, params.feedback_threshold_na, params.greater_than,
            params.feedback_mode, baseline=baseline,
        )
        self._approach_feedback_baseline = baseline
        self._enqueue([waypoint], self._approach_pending_duration_s)
        self._approach_history.append((self._program_baseline, ["approach"]))
        self._sequence = _Sequence(self._program_baseline, 1, 1, 0, None)
        self._approach_phase = "approach"
        self._approach_pending_waypoint = None
        return baseline

    def _submit_approach_cv_followup(self) -> None:
        params = self._approach_params
        if params is None:
            raise RuntimeError("Approach parameters were not retained for the CV follow-up")
        current = self._current_targets()
        z_speed = max(10.0, params.approach_rate_um_s)
        settle_plan = timed_hold_plan(params.settling_time_s)
        plan = settle_plan + cyclic_voltammetry_plan(
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
        settle_count = len(settle_plan)
        contexts = ["settling"] * settle_count + ["cv"] * (len(compiled.waypoints) - settle_count)
        if retract_index is not None:
            contexts[retract_index] = "retract"
        self._approach_history.append((self._program_baseline, contexts))
        self._sequence = _Sequence(self._program_baseline, len(compiled.waypoints), settle_count,
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
        if self._approach_phase == "baseline":
            if not self._submitted and self._hardware_complete:
                baseline = self._submit_approach_cv_contact_motion()
                return {
                    "stage": "approaching",
                    "detail": f"Stationary baseline {baseline:.4g} nA; FPGA absolute contact threshold armed",
                    "progress": 0.2,
                }
            return {
                "stage": "preposition",
                "detail": "Positioning and measuring a stationary contact baseline",
                "progress": 0.15,
            }
        if self._approach_phase == "approach":
            internal_pause = bool(self._read_register("Internal Pause"))
            if internal_pause:
                if not self._approach_contact_confirmed():
                    self._cancel_detail = "FPGA paused during approach without a confirmed feedback event"
                    self.cancel_program()
                    self._cancel_detail = "FPGA paused without confirmed contact; reconnect before another command"
                    return {"stage": "aborted", "detail": self._cancel_detail, "progress": 0.4}
                self._approach_contact_observed = True
                if not self._ending_waypoint:
                    self.end_current_waypoint()
                return {"stage": "contact", "detail": "Contact confirmed; ending approach at a framed boundary", "progress": 0.42}
            if not self._submitted and self._hardware_complete:
                if self._approach_contact_observed or self._approach_manually_accepted:
                    self._submit_approach_cv_followup()
                    detail = "Operator accepted contact" if self._approach_manually_accepted else "FPGA contact confirmed"
                    return {"stage": "contact", "detail": f"{detail}; CV program submitted", "progress": 0.42}
                self._cancelled = True
                self._cancel_detail = "End Z was reached without a confirmed contact; CV was not submitted"
                return {"stage": "aborted", "detail": self._cancel_detail, "progress": 0.4}
            context = self.approach_context(current_line)
            if context != "approach":
                return {"stage": "preposition", "detail": "Moving Z to the approach start", "progress": 0.2}
            return {
                "stage": "approaching",
                "detail": f"FPGA is watching {self._approach_feedback_channel} for contact",
                "progress": 0.4,
            }
        if not self._submitted and self._hardware_complete:
            return {"stage": "complete", "detail": "FPGA approach, CV, and retract complete", "progress": 1.0}
        index = min(max(0, completed), sequence.total - 1)
        progress = min(0.99, completed / max(1, sequence.total))
        if sequence.retract_index is not None and index >= sequence.retract_index:
            return {"stage": "retracting", "detail": "CV complete; retracting Z", "progress": progress}
        if index < sequence.cv_first:
            return {"stage": "settling", "detail": f"Holding contact for {self._approach_params.settling_time_s:g} s", "progress": progress}
        cycle = min((index - sequence.cv_first) // 3 + 1, max(1, (sequence.cv_last - sequence.cv_first) // 3 + 1))
        return {"stage": "cv", "detail": f"FPGA is running CV cycle {cycle}", "progress": progress}

    def approach_context(self, line_number: int) -> str:
        """Map a FIFO sample line number back to its programmed segment."""
        for baseline, contexts in reversed(self._approach_history):
            index = self._sample_waypoint_index(line_number, baseline)
            if 0 <= index < len(contexts):
                return contexts[index]
        return ""

    def start_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("; ".join(errors))
        feedback_index = int(params.feedback_channel[-1])
        if abs(params.feedback_threshold_na * getattr(self.settings, f"current{feedback_index}_v_per_na")) > 10:
            raise ValueError("Feedback threshold exceeds the selected current input's +/-10 V ADC range.")
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
        baseline_frames = int(params.feedback_mode == "baseline_relative")
        total = 1 + params.point_count * (
            4 + baseline_frames + 3 * params.cycles + hold_frame_count(params.settling_time_s)
        )
        if baseline < 0 or baseline + total > 32767:
            raise ValueError("Scan line tags would exceed the verified I16 range. Reinitialize the target before scanning.")
        s = self.settings
        effective_threshold = self._configure_contact_feedback(
            params.feedback_channel, params.feedback_threshold_na, params.greater_than, params.feedback_mode,
        )
        self._sequence = None
        self._scan_params = params
        self._scan_grid = params.grid()
        self._scan_history.clear()
        self._scan_point = 0
        self._scan_threshold_na = effective_threshold
        self._scan_feedback_channel = params.feedback_channel
        self._scan_greater_than = params.greater_than
        self._scan_feedback_mode = params.feedback_mode
        self._scan_feedback_baseline.clear()
        self._scan_baseline_samples.clear()
        self._scan_end_z_raw = position_to_raw(params.end_z_um, s.z_range_um, s.z_bipolar)
        self._scan_contact_observed.clear()
        self._scan_manually_accepted.clear()
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
        positioning = Waypoint(**common, z_position=start_z,
                     v_position=voltage1_to_raw(params.approach_voltage_v, s.command_voltage_ratio),
                     x_velocity=scale_velocity(x_raw, ex), y_velocity=scale_velocity(y_raw, ey),
                     move_x=True, move_y=True, move_v=True, jump_v=True)
        approach = Waypoint(**common, line_type=FEEDBACK_ACTION_CODES["pause_on_contact"],
                     z_position=position_to_raw(params.end_z_um, s.z_range_um, s.z_bipolar),
                     v_position=voltage1_to_raw(params.approach_voltage_v, s.command_voltage_ratio),
                     z_velocity=scale_velocity(za_raw, ez),
                     update_wait_us=self._feedback_update_interval_us, move_z=True)
        waypoints.append(positioning)
        stages.append("positioning")
        if params.feedback_mode == "baseline_relative":
            baseline_targets = {
                "X": common["x_position"], "Y": common["y_position"], "Z": start_z,
                "V": positioning.v_position, "V2": common["v2_position"],
            }
            waypoints.append(self._baseline_hold_waypoint(baseline_targets))
            stages.append("baseline")
            self._scan_pending_waypoint = approach
            self._scan_pending_duration_s = abs(params.end_z_um - params.start_z_um) / params.approach_rate_um_s
        else:
            waypoints.append(approach)
            stages.append("approach")
            self._scan_pending_waypoint = None
            self._scan_pending_duration_s = 0.0
        current_x = raw_to_position(current["X"], s.x_range_um, s.x_bipolar)
        current_y = raw_to_position(current["Y"], s.y_range_um, s.y_bipolar)
        current_z = raw_to_position(current["Z"], s.z_range_um, s.z_bipolar)
        expected = max(abs(x_um - current_x), abs(y_um - current_y)) / params.lateral_rate_um_s
        if params.feedback_mode == "baseline_relative":
            expected += self.BASELINE_HOLD_US / 1_000_000.0
        else:
            expected += abs(params.end_z_um - current_z) / params.approach_rate_um_s
        if initial:
            expected += abs(current_z - params.start_z_um) / params.retract_rate_um_s
        self._enqueue(waypoints, expected)
        descriptors = [(point, stage) for stage in stages]
        self._scan_sequence = _ScanSequence(self._program_baseline, descriptors)
        self._scan_history.append(self._scan_sequence)
        self._scan_phase = "baseline" if params.feedback_mode == "baseline_relative" else "approach"
        self._scan_point = point
        if resume:
            self._write_register("External Pause", False)

    def _submit_scan_contact_approach(self) -> float:
        params = self._scan_params
        waypoint = self._scan_pending_waypoint
        if params is None or waypoint is None:
            raise RuntimeError("No scan approach is awaiting baseline translation.")
        values = self._scan_baseline_samples.get(self._scan_point, [])
        baseline = self._stationary_baseline(params.feedback_channel, values)
        self._configure_contact_feedback(
            params.feedback_channel, params.feedback_threshold_na, params.greater_than,
            params.feedback_mode, baseline=baseline,
        )
        self._scan_feedback_baseline[self._scan_point] = baseline
        self._enqueue([waypoint], self._scan_pending_duration_s)
        self._scan_sequence = _ScanSequence(self._program_baseline, [(self._scan_point, "approach")])
        self._scan_history.append(self._scan_sequence)
        self._scan_phase = "approach"
        self._scan_pending_waypoint = None
        self._write_register("External Pause", False)
        return baseline

    def _submit_scan_cv(self, point: int) -> None:
        params = self._scan_params
        if params is None:
            raise RuntimeError("Scan CV phase has no parameters")
        current = self._current_targets()
        contact_z = raw_to_position(current["Z"], self.settings.z_range_um, self.settings.z_bipolar)
        low_z, high_z = sorted((params.start_z_um, params.end_z_um))
        if not low_z <= contact_z <= high_z:
            contact_z = self._scan_last_approach_z.get(point, params.end_z_um)
        settle_plan = timed_hold_plan(params.settling_time_s)
        plan = settle_plan + cyclic_voltammetry_plan(
            start_v=params.cv_start_v,
            vertex1_v=params.cv_vertex1_v,
            vertex2_v=params.cv_vertex2_v,
            scan_rate_v_s=params.cv_scan_rate_v_s,
            cycles=params.cycles,
            retract_z_um=params.retract_z_for_point(point, contact_z),
            retract_rate_um_s=params.retract_rate_um_s,
        )
        compiled = self.compiler.compile(plan, current)
        self._pending_scalers = tuple(compiled.scaler_exponents[name] for name in ("X", "Y", "Z", "V", "V2"))
        self._enqueue(compiled.waypoints, compiled.expected_duration_s)
        settle_count = len(settle_plan)
        descriptors = (
            [(point, "settling")] * settle_count
            + [(point, "cv")] * (len(compiled.waypoints) - settle_count - 1)
            + [(point, "retract")]
        )
        self._scan_sequence = _ScanSequence(self._program_baseline, descriptors)
        self._scan_history.append(self._scan_sequence)
        self._scan_phase = "cv"
        self._write_register("External Pause", False)

    def scan_context(self, line_number: int) -> tuple[int, str]:
        for sequence in reversed(self._scan_history):
            index = self._sample_waypoint_index(line_number, sequence.baseline_line)
            if 0 <= index < len(sequence.descriptors):
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
        if self._scan_phase == "baseline":
            if not self._submitted and self._hardware_complete:
                baseline = self._submit_scan_contact_approach()
                return {
                    "stage": "approaching",
                    "detail": f"Point {self._scan_point + 1} of {point_total} · baseline {baseline:.4g} nA; approach armed",
                    "progress": base_progress + 0.2 / max(1, point_total),
                    "point_index": self._scan_point,
                    "point_stage": "approach",
                }
            return {
                "stage": "preposition",
                "detail": f"Point {self._scan_point + 1} of {point_total} · measuring stationary baseline",
                "progress": base_progress + 0.15 / max(1, point_total),
                "point_index": self._scan_point,
                "point_stage": "baseline",
            }
        if self._scan_phase == "approach":
            if bool(self._read_register("Internal Pause")):
                if not self._scan_contact_confirmed(self._scan_point):
                    self._scan_failed_contact = self._scan_point
                    self._cancel_detail = f"Point {self._scan_point + 1} paused without a confirmed feedback event"
                    self.cancel_program()
                    self._cancel_detail = f"Point {self._scan_point + 1} paused without confirmed contact; reconnect required"
                    return {"stage": "aborted", "detail": self._cancel_detail, "progress": base_progress,
                            "point_index": self._scan_point, "point_stage": "no-contact"}
                self._scan_contact_observed.add(self._scan_point)
                if not self._ending_waypoint:
                    self.end_current_waypoint()
                return {"stage": "contact", "detail": f"Point {self._scan_point + 1} of {point_total} · ending approach at a framed boundary",
                        "progress": base_progress + 0.45 / max(1, point_total),
                        "point_index": self._scan_point, "point_stage": "contact"}
            if not self._submitted and self._hardware_complete:
                if self._scan_point in self._scan_contact_observed or self._scan_point in self._scan_manually_accepted:
                    self._submit_scan_cv(self._scan_point)
                    origin = "operator accepted" if self._scan_point in self._scan_manually_accepted else "FPGA confirmed"
                    return {"stage": "cv", "detail": f"Point {self._scan_point + 1}: {origin} contact; CV submitted",
                            "progress": base_progress + 0.45 / max(1, point_total),
                            "point_index": self._scan_point, "point_stage": "cv"}
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
        point_index, point_stage = self._scan_point, ""
        index = self._sample_waypoint_index(current_line, sequence.baseline_line)
        if 0 <= index < len(sequence.descriptors):
            point_index, point_stage = sequence.descriptors[index]
        stage = ("approaching" if point_stage == "approach" else "settling" if point_stage == "settling"
                 else "cv" if point_stage == "cv" else "retracting" if point_stage == "retract"
                 else "preposition" if self._scan_phase == "approach" else self._scan_phase)
        phase_fraction = min(0.9, completed / max(1, len(sequence.descriptors)))
        return {
            "stage": stage,
            "detail": f"Point {point_index + 1} of {point_total} · {point_stage or stage}",
            "progress": min(0.99, (max(0, point_index) + phase_fraction) / max(1, point_total)),
            "point_index": point_index,
            "point_stage": point_stage or stage,
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
        self._method_manually_accepted.clear()
        self._method_last_approach_z.clear()
        self._method_feedback_baseline.clear()
        self._method_baseline_samples.clear()
        self._method_grid = []
        self._method_point = -1
        self._method_no_contact = False
        self._method_pending_approach = None

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
                baseline_frames = int(parameters.feedback_mode == "baseline_relative")
                total_tags = 1 + parameters.point_count * (
                    3 + baseline_frames + hold_frames + hold_frame_count(parameters.settling_time_s)
                )
            else:
                total_tags = (
                    2 + int(parameters.feedback_mode == "baseline_relative")
                    + hold_frames + hold_frame_count(parameters.settling_time_s) + int(parameters.retract_after)
                )
            if baseline < 0 or baseline + total_tags > 32767:
                raise ValueError(
                    "I-t line tags would exceed the verified signed-I16 acquisition range. "
                    "Reinitialize the target before running this method."
                )
        elif isinstance(parameters, ApproachParameters):
            total_tags = (
                2 + int(parameters.feedback_mode == "baseline_relative")
                + hold_frame_count(parameters.settling_time_s) + int(parameters.retract_after)
            )
            if baseline < 0 or baseline + total_tags > 32767:
                raise ValueError(
                    "Approach line tags would exceed the verified signed-I16 acquisition range. "
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
            self._method_threshold = self._configure_contact_feedback(
                parameters.feedback_channel, parameters.feedback_threshold,
                parameters.greater_than, parameters.feedback_mode,
            )
            self._method_feedback_channel = parameters.feedback_channel
            self._method_greater_than = parameters.greater_than
            self._method_feedback_mode = parameters.feedback_mode
            self._method_feedback_baseline.clear()
            self._submit_method_scan_approach(initial=True)
            return

        if not isinstance(parameters, (ApproachParameters, ApproachITParameters)):
            raise TypeError(f"{name} requires approach parameters")
        self._method_point = -1
        self._method_threshold = self._configure_contact_feedback(
            parameters.feedback_channel, parameters.feedback_threshold,
            parameters.greater_than, parameters.feedback_mode,
        )
        self._method_feedback_channel = parameters.feedback_channel
        self._method_greater_than = parameters.greater_than
        self._method_feedback_mode = parameters.feedback_mode
        self._method_feedback_baseline.clear()
        xy_rate = max(10.0, parameters.approach_rate_um_s)
        preposition = PhysicalWaypoint(
            x_um=parameters.x_um, y_um=parameters.y_um, z_um=parameters.start_z_um,
            voltage1_v=parameters.approach_voltage_v,
            x_rate_um_s=xy_rate if parameters.x_um is not None else None,
            y_rate_um_s=xy_rate if parameters.y_um is not None else None,
            z_rate_um_s=parameters.retract_rate_um_s, jump_voltage1=True,
        )
        approach = PhysicalWaypoint(
            z_um=parameters.end_z_um, z_rate_um_s=parameters.approach_rate_um_s,
            feedback_action="pause_on_contact",
            update_interval_us=self._feedback_update_interval_us,
        )
        if parameters.feedback_mode == "baseline_relative":
            self._method_pending_approach = approach
            self._submit_method_plan(
                [preposition, PhysicalWaypoint(hold=True, hold_us=self.BASELINE_HOLD_US)],
                [(-1, "preposition"), (-1, "baseline")], "baseline",
            )
        else:
            self._submit_method_plan([preposition, approach], [(-1, "preposition"), (-1, "approach")], "approach")

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
        positioning = PhysicalWaypoint(
                x_um=x_um, y_um=y_um, voltage1_v=params.approach_voltage_v,
                x_rate_um_s=params.lateral_rate_um_s, y_rate_um_s=params.lateral_rate_um_s,
                jump_voltage1=True,
            )
        approach = PhysicalWaypoint(
                z_um=params.end_z_um, z_rate_um_s=params.approach_rate_um_s,
                feedback_action="pause_on_contact",
                update_interval_us=self._feedback_update_interval_us,
            )
        plan.append(positioning)
        descriptors.append((self._method_point, "positioning"))
        if params.feedback_mode == "baseline_relative":
            plan.append(PhysicalWaypoint(hold=True, hold_us=self.BASELINE_HOLD_US))
            descriptors.append((self._method_point, "baseline"))
            self._method_pending_approach = approach
            phase = "baseline"
        else:
            plan.append(approach)
            descriptors.append((self._method_point, "approach"))
            self._method_pending_approach = None
            phase = "approach"
        self._submit_method_plan(plan, descriptors, phase, resume=not initial)

    def _submit_method_contact_approach(self) -> float:
        params = self._method_params
        approach = self._method_pending_approach
        if not isinstance(params, (ApproachParameters, ApproachITParameters, ScanHoppingITParameters)) or approach is None:
            raise RuntimeError("No shared-method approach is awaiting baseline translation.")
        values = self._method_baseline_samples.get(self._method_point, [])
        baseline = self._stationary_baseline(params.feedback_channel, values)
        self._configure_contact_feedback(
            params.feedback_channel, params.feedback_threshold, params.greater_than,
            params.feedback_mode, baseline=baseline,
        )
        self._method_feedback_baseline[self._method_point] = baseline
        self._method_pending_approach = None
        self._submit_method_plan([approach], [(self._method_point, "approach")], "approach", resume=True)
        return baseline

    def _submit_method_it(self, params: ApproachITParameters | ScanHoppingITParameters, point: int) -> None:
        settle_plan = timed_hold_plan(params.settling_time_s)
        method_plan, labels = potential_step_plan(params.it_steps())
        plan = settle_plan + method_plan
        descriptors = [(point, "settling")] * len(settle_plan) + [(point, f"it:{label}") for label in labels]
        should_retract = params.retract_after if isinstance(params, ApproachITParameters) else True
        if should_retract:
            if isinstance(params, ScanHoppingITParameters):
                current = self._current_targets()
                contact_z = raw_to_position(current["Z"], self.settings.z_range_um, self.settings.z_bipolar)
                low_z, high_z = sorted((params.start_z_um, params.end_z_um))
                if not low_z <= contact_z <= high_z:
                    contact_z = self._method_last_approach_z.get(point, params.end_z_um)
                retract_z = params.retract_z_for_point(point, contact_z)
            else:
                retract_z = params.start_z_um
            plan.append(PhysicalWaypoint(z_um=retract_z, z_rate_um_s=params.retract_rate_um_s))
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
            if self._method_name == "approach":
                assert isinstance(params, ApproachParameters)
                plan = timed_hold_plan(params.settling_time_s)
                descriptors = [(point, "settling")] * len(plan)
                if params.retract_after:
                    plan.append(PhysicalWaypoint(z_um=params.start_z_um, z_rate_um_s=params.retract_rate_um_s))
                    descriptors.append((point, "retract"))
                    self._submit_method_plan(plan, descriptors, "contact_followup", resume=True)
                elif plan:
                    self._submit_method_plan(plan, descriptors, "contact_followup", resume=True)
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
            index = self._sample_waypoint_index(line_number, sequence.baseline_line)
            if 0 <= index < len(sequence.descriptors):
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
        if self._method_phase == "baseline":
            if not self._submitted and self._hardware_complete:
                baseline = self._submit_method_contact_approach()
                return {
                    "stage": "approaching",
                    "detail": f"Stationary baseline {baseline:.4g} nA; FPGA absolute contact threshold armed",
                    "progress": 0.2,
                    "point_index": self._method_point,
                    "point_stage": "approach",
                }
            return {
                "stage": "preposition",
                "detail": "Positioning and measuring a stationary contact baseline",
                "progress": 0.15,
                "point_index": self._method_point,
                "point_stage": "baseline",
            }
        if self._method_phase == "approach":
            if bool(self._read_register("Internal Pause")):
                if not self._method_contact_confirmed(self._method_point):
                    self.cancel_program()
                    self._method_terminal = "aborted"
                    self._method_detail = "FPGA paused without confirmed contact; reconnect required"
                else:
                    self._method_contact_observed.add(self._method_point)
                    if not self._ending_waypoint:
                        self.end_current_waypoint()
                    return {
                        "stage": "contact", "detail": "Contact confirmed; ending approach at a framed boundary",
                        "progress": 0.4, "point_index": self._method_point, "point_stage": "contact",
                    }
            elif not self._submitted and self._hardware_complete:
                self._method_finish_approach(
                    self._method_point in self._method_contact_observed
                    or self._method_point in self._method_manually_accepted
                )
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

        point, point_stage = self._method_point, ""
        if self._method_sequence is not None:
            index = self._sample_waypoint_index(
                int(self._read_register("LineNumber")), self._method_sequence.baseline_line
            )
            if 0 <= index < len(self._method_sequence.descriptors):
                point, point_stage = self._method_sequence.descriptors[index]
        total_points = max(1, len(self._method_grid))
        local = self.execution_status().progress
        progress = local if self._method_name != "scan_hopping_it" else min(0.99, (max(0, self._method_point) + local) / total_points)
        stage = "approaching" if self._method_phase == "approach" else "retracting" if self._method_phase == "retract" else self._method_phase
        if point_stage in {"preposition", "positioning"}:
            stage = "preposition"
        elif point_stage == "retract":
            stage = "retracting"
        elif point_stage == "settling":
            stage = "settling"
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
        if not self._framing_valid:
            deferred, self._deferred_samples = self._deferred_samples, []
            samples, self._retained_samples = self._retained_samples, []
            return deferred + samples
        self.service()
        try:
            samples = self._read_complete_sample_snapshot()
        except Exception:
            self._stopped = True
            self._program_deadline = None
            self._pause_started_at = None
            self._write_register("External Pause", True)
            raise
        deferred, self._deferred_samples = self._deferred_samples, []
        if not samples:
            available = int(self.data_fifo.read(number_of_elements=0, timeout_ms=0).elements_remaining)
            if self._hardware_complete and available == 0:
                self._submitted = False
                self._program_deadline = None
                self._pause_started_at = None
                self._execution_state = ExecutionState.COMPLETE
                self._execution_detail = "Program complete; final FIFO snapshot drained"
            return deferred
        if self._hardware_complete and self._last_snapshot_remainder == 0:
            self._submitted = False
            self._program_deadline = None
            self._pause_started_at = None
            self._execution_state = ExecutionState.COMPLETE
            self._execution_detail = "Program complete; final FIFO snapshot drained"
        return deferred + samples

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
        self._account_pause_state(True)
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
        self._account_pause_state(False)
        self._execution_state = ExecutionState.RUNNING
        self._execution_detail = "Resumed by operator"

    def end_current_waypoint(self) -> None:
        self._check_target_health()
        if not self._submitted:
            raise RuntimeError("No FPGA waypoint is active.")
        if self._ending_waypoint:
            return
        if bool(self._read_register("External Pause")):
            raise RuntimeError("Resume the operator pause before ending the current FPGA waypoint.")
        current_line = int(self._read_register("LineNumber"))
        delta = (current_line - self._program_baseline) & ((1 << 64) - 1)
        if not 1 <= delta <= self._program_total:
            raise RuntimeError("The FPGA has not acknowledged an executing waypoint to end.")
        self._end_request_line = current_line
        self._write_register("EndCurrentLine", True)
        if not bool(self._read_register("EndCurrentLine")):
            raise RuntimeError("FPGA did not latch the EndCurrentLine request.")
        self._ending_waypoint = True
        self._end_request_deadline = time.monotonic() + self.settings.hardware_ready_timeout_s
        self._execution_detail = "Ending the current FPGA waypoint; awaiting target acknowledgement"

    def accept_approach(self) -> None:
        """End only an active approach and explicitly authorize its follow-up."""
        self._check_target_health()
        if self._approach_phase == "approach" and self._owner == "approach-cv":
            accepted = "approach"
        elif self._scan_phase == "approach" and self._owner == "scan-hopping-cv" and self._scan_point >= 0:
            accepted = "scan"
        elif self._method_phase == "approach" and self._owner in {"approach", "approach-it", "scan-hopping-it"}:
            accepted = "method"
        else:
            raise RuntimeError("No approach movement is currently active.")
        current_line = int(self._read_register("LineNumber"))
        if accepted == "approach":
            executing_approach = self.approach_context(current_line) == "approach"
        elif accepted == "scan":
            point, stage = self.scan_context(current_line)
            executing_approach = point == self._scan_point and stage == "approach"
        else:
            point, stage = self.method_context(current_line)
            executing_approach = point == self._method_point and stage == "approach"
        if not executing_approach:
            raise RuntimeError("The FPGA is still positioning the probe; contact can be accepted only during Z approach.")
        self.end_current_waypoint()
        if accepted == "approach":
            self._approach_manually_accepted = True
            self._approach_contact_observed = True
        elif accepted == "scan":
            self._scan_manually_accepted.add(self._scan_point)
            self._scan_contact_observed.add(self._scan_point)
        else:
            self._method_manually_accepted.add(self._method_point)
            self._method_contact_observed.add(self._method_point)
        self._execution_detail = "Operator accepted the current Z as contact"

    def _feedback_hit(self, current_na: float, threshold_na: float, greater_than: bool) -> bool:
        return current_na >= threshold_na if greater_than else current_na <= threshold_na

    @staticmethod
    def _sample_current(sample: Sample, channel: str) -> float:
        return sample.current1_na if channel == "Current 1" else sample.current2_na

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
            if approach_stage == "baseline":
                self._approach_baseline_samples.append(
                    self._sample_current(sample, self._approach_feedback_channel)
                )
            elif approach_stage == "approach":
                current = self._sample_current(sample, self._approach_feedback_channel)
                if self._approach_feedback_baseline is None:
                    self._approach_feedback_baseline = current
                signal = current if self._approach_feedback_mode == "absolute" else current - self._approach_feedback_baseline
                if self._feedback_hit(signal, self._approach_threshold_na, self._approach_greater_than):
                    self._approach_contact_observed = True
            point, scan_stage = self.scan_context(sample.line_number)
            if point >= 0 and scan_stage == "baseline":
                self._scan_baseline_samples.setdefault(point, []).append(
                    self._sample_current(sample, self._scan_feedback_channel)
                )
            elif point >= 0 and scan_stage == "approach":
                self._scan_last_approach_z[point] = sample.z_um
                current = self._sample_current(sample, self._scan_feedback_channel)
                baseline = self._scan_feedback_baseline.setdefault(point, current)
                signal = current if self._scan_feedback_mode == "absolute" else current - baseline
                if self._feedback_hit(signal, self._scan_threshold_na, self._scan_greater_than):
                    self._scan_contact_observed.add(point)
            if previous_scan is not None and previous_scan[0] == point and previous_scan[1] == "approach" and scan_stage in {"settling", "cv"}:
                if not self._scan_contact_confirmed(point):
                    self._scan_failed_contact = point
            previous_scan = (point, scan_stage)
            method_point, method_stage = self.method_context(sample.line_number)
            if method_stage == "baseline":
                self._method_baseline_samples.setdefault(method_point, []).append(
                    self._sample_current(sample, self._method_feedback_channel)
                )
            elif method_stage == "approach":
                self._method_last_approach_z[method_point] = sample.z_um
                current = self._sample_current(sample, self._method_feedback_channel)
                baseline = self._method_feedback_baseline.setdefault(method_point, current)
                signal = current if self._method_feedback_mode == "absolute" else current - baseline
                if self._feedback_hit(signal, self._method_threshold, self._method_greater_than):
                    self._method_contact_observed.add(method_point)

    def stop_motion(self) -> None:
        """Safely cancel motion and retire any stream interrupted mid-frame."""
        self.cancel_program()

    def emergency_stop(self) -> None:
        """Latch both host-side and FPGA-side stops and verify their controls."""
        self._stopped = True
        self._program_deadline = None
        self._pause_started_at = None
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
