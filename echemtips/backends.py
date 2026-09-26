"""Thread-safe simulator and NI backends behind one instrument contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
import importlib
import math
from pathlib import Path
import random
import threading
import time
from typing import Any

from .host import ExecutionSnapshot, ExecutionState
from .models import AppSettings, ApproachCVParameters, FeedbackConfiguration, Sample, ScanHoppingCVParameters
from .ni_protocol import (
    DEPLOYED_STARTUP_RAW_OUTPUTS,
    inspect_bitfile,
    raw_to_adc_voltage,
    raw_to_current,
    raw_to_position,
    raw_to_voltage1,
    validate_wec_bitfile,
)


class BackendError(RuntimeError):
    """A device connection or I/O operation failed."""


class SafetyError(BackendError):
    """An output request violated a configured safety limit."""


@dataclass(frozen=True, slots=True)
class HardwareSequenceUpdate:
    """Normalized method stage, detail, progress, and optional scan context."""
    stage: str
    detail: str
    progress: float
    point_index: int = -1
    point_stage: str = ""


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Feature flags used to enable only controls supported by a backend."""
    pause_resume: bool
    end_current_waypoint: bool
    live_potential: bool
    measured_position: bool
    full_rate_acquisition: bool


def _synchronized_io(method):
    """Serialize commands with the background FIFO acquisition thread."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        """Invoke one backend operation while holding its re-entrant I/O lock."""
        with self.io_lock:
            return method(self, *args, **kwargs)
    return guarded


class InstrumentBackend(ABC):
    """Abstract physical-unit API consumed by experiments and acquisition."""
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.connected = False
        self.io_lock = threading.RLock()

    @property
    def motion_available(self) -> bool:
        """Return whether bounded X/Y/Z commands can be submitted."""
        return True

    @property
    def hardware_approach_cv_required(self) -> bool:
        """Return whether contact-gated methods must execute as FPGA programs."""
        return False

    @property
    def approach_cv_available(self) -> bool:
        """Return whether the complete Approach + CV contract is supported."""
        return True

    @property
    def scan_hopping_cv_available(self) -> bool:
        """Return whether hardware or host can execute hopping CV."""
        return not self.hardware_approach_cv_required

    @property
    def full_rate_data_available(self) -> bool:
        """Return whether FIFO batches rather than register snapshots are read."""
        return True

    @property
    def capabilities(self) -> BackendCapabilities:
        """Return UI-facing backend capabilities."""
        return BackendCapabilities(
            True, True, True, True,
            self.full_rate_data_available,
        )

    @property
    def position_readback_label(self) -> str:
        """Describe whether displayed position is measured or only commanded."""
        return "Measured/simulated position"

    @property
    def startup_notice(self) -> str:
        """Describe unavoidable output changes caused merely by connecting."""
        return ""

    def experiment_time(self) -> float:
        """Monotonic execution clock used by host-side experiment timers."""
        return time.monotonic()

    @property
    @abstractmethod
    def label(self) -> str:
        """Return the human-readable connected-device label."""
        raise NotImplementedError

    @abstractmethod
    def connect(self, *, allow_startup_actuation: bool = False) -> None:
        """Open the backend, requiring explicit authorization for startup output."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Close resources and make later I/O reject until reconnection."""
        raise NotImplementedError

    @abstractmethod
    def read_sample(self) -> Sample:
        """Return one current calibrated measurement."""
        raise NotImplementedError

    @_synchronized_io
    def read_samples(self) -> list[Sample]:
        """Return all newly available samples; simple backends return one."""
        return [self.read_sample()]

    @abstractmethod
    def move(self, axis: str, target: float, speed: float) -> None:
        """Command one physical axis to a bounded target at positive speed."""
        raise NotImplementedError

    @abstractmethod
    def stop_motion(self) -> None:
        """Stop current movement according to backend cancellation semantics."""
        raise NotImplementedError

    @abstractmethod
    def set_voltage(self, channel: int, voltage: float) -> None:
        """Set E1 or E2 in requested physical volts."""
        raise NotImplementedError

    def set_live_potential(self, channel: int, voltage: float) -> None:
        """Apply a bounded ChangeOnFly potential without taking motion ownership."""
        self.set_voltage(channel, voltage)

    def start_hardware_approach_cv(self, params: ApproachCVParameters) -> None:
        """Start the backend-resident staged Approach + CV implementation."""
        raise BackendError("This backend does not execute FPGA waypoint sequences.")

    def hardware_approach_cv_status(self) -> HardwareSequenceUpdate:
        """Return normalized status for hardware Approach + CV."""
        raise BackendError("This backend does not report FPGA waypoint sequence state.")

    def hardware_approach_context(self, line_number: int) -> str:
        """Identify which approach/CV waypoint produced a hardware sample."""
        return ""

    def start_hardware_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
        """Start backend-resident contact-gated hopping CV."""
        raise BackendError("This backend does not execute FPGA scan-hopping sequences.")

    def hardware_scan_hopping_cv_status(self) -> HardwareSequenceUpdate:
        """Return normalized status for hardware hopping CV."""
        raise BackendError("This backend does not report FPGA scan-hopping state.")

    def hardware_scan_context(self, line_number: int) -> tuple[int, str]:
        """Map an FPGA sample tag to hopping-CV pixel and stage."""
        return -1, ""

    def hardware_program_available(self, name: str) -> bool:
        """Return whether a named shared FPGA method contract is present."""
        return False

    def start_hardware_program(self, name: str, parameters: object) -> None:
        """Start a named shared FPGA method with validated parameters."""
        raise BackendError(f"This backend cannot execute the FPGA method {name!r}.")

    def hardware_program_status(self) -> HardwareSequenceUpdate:
        """Return normalized status for the active shared FPGA method."""
        raise BackendError("This backend does not report shared FPGA method state.")

    def hardware_program_context(self, line_number: int) -> tuple[int, str]:
        """Map an FPGA sample tag to shared-method pixel and stage."""
        return -1, ""

    def pause(self) -> None:
        """Pause execution when the backend exposes operator pause ownership."""
        raise BackendError("Pause is not supported by this backend.")

    def resume(self) -> None:
        """Resume a prior operator pause without overriding feedback ownership."""
        raise BackendError("Resume is not supported by this backend.")

    def end_current_waypoint(self) -> None:
        """Request acknowledged completion of the active FPGA waypoint."""
        raise BackendError("Ending the current waypoint is not supported by this backend.")

    def accept_approach(self) -> None:
        """Stop the active approach waypoint and treat its current Z as contact."""
        raise BackendError("Manual approach acceptance is not supported by this backend.")

    def configure_feedback(self, config: FeedbackConfiguration) -> None:
        """Apply supported feedback fields; simple backends need no action."""
        del config

    def set_diagnostic_circuit(self, mode: str, resistance_mohm: float | None = None) -> None:
        """Select a simulator fixture; real hardware is rewired by the operator."""
        if mode not in {"normal", "open", "resistor", "pipette"}:
            raise ValueError(f"Unknown diagnostic circuit: {mode}")
        del resistance_mohm

    def execution_status(self) -> ExecutionSnapshot:
        """Return a UI-safe execution snapshot."""
        return ExecutionSnapshot("", ExecutionState.IDLE, 0, 0, 0, 0, "No FPGA program")

    def emergency_stop(self) -> None:
        """Perform the backend's strongest software stop operation."""
        self.stop_motion()
        self.set_voltage(1, 0.0)
        self.set_voltage(2, 0.0)

    def _validate_output(self, axis: str, target: float, speed: float) -> None:
        limits = {
            "X": self.settings.x_range_um,
            "Y": self.settings.y_range_um,
            "Z": self.settings.z_range_um,
            "Voltage 1": 10.0,
            "Voltage 2": 10.0,
        }
        if axis not in limits:
            raise SafetyError(f"Unknown output axis: {axis}")
        lower = -10.0 if axis.startswith("Voltage") else 0.0
        if not math.isfinite(target) or not lower <= target <= limits[axis]:
            unit = "V" if axis.startswith("Voltage") else "um"
            raise SafetyError(f"{axis} target must be between {lower:g} and {limits[axis]:g} {unit}.")
        if not math.isfinite(speed) or speed <= 0:
            raise SafetyError("Movement speed must be positive.")


class SimulationBackend(InstrumentBackend):
    """Deterministic, stateful virtual microscope with a simulated surface."""

    def __init__(self, settings: AppSettings, seed: int = 8102) -> None:
        super().__init__(settings)
        self._rng = random.Random(seed)
        self._started = time.monotonic()
        self._last_tick = self._started
        self._positions = {"X": settings.x_range_um / 2, "Y": settings.y_range_um / 2, "Z": 10.0}
        self._targets = dict(self._positions)
        self._speeds = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._voltage = {1: 0.0, 2: 0.0}
        self._line_number = 0
        self._paused = False
        self._paused_at: float | None = None
        self._paused_duration_s = 0.0
        self._diagnostic_mode = "normal"
        self._diagnostic_resistance_mohm = 100.0
        self._last_sample_voltage = 0.0
        self._last_sample_elapsed = 0.0
        self._cv_rate_index = -1

    def surface_z_at(self, x_um: float, y_um: float) -> float:
        """Return deterministic simulated surface height at physical XY."""
        if getattr(self, "adaptive_scene", False):
            start, end, x0, x1, y0, y1 = self._adaptive_bounds
            x = max(0.0, min(1.0, (x_um-x0)/(x1-x0)))
            y = max(0.0, min(1.0, (y_um-y0)/(y1-y0)))
            return start + (end-start)*(.65 + .08*(x-.5) + .06*(y-.5))
        sx = (x_um / self.settings.x_range_um - 0.5) * math.tau
        sy = (y_um / self.settings.y_range_um - 0.5) * math.tau
        return self.settings.z_range_um * 0.68 + 2.4 * math.sin(sx) * math.cos(sy) + 0.7 * math.sin(2 * sy)

    @_synchronized_io
    def configure_adaptive_scene(self, params) -> None:
        """Place a synthetic tilted surface inside the requested approach span.

        This affects simulation only, never changes thresholds, and keeps the
        spatial hotspot fixed for the duration of a run. Failed-landings injected
        by tests still produce only the open-circuit baseline.
        """
        self._hopping_scene = False
        self._adaptive_bounds = (params.start_z_um,params.end_z_um,
                                 params.x_min_um,params.x_max_um,params.y_min_um,params.y_max_um)
        self._adaptive_wet_pixel = None
        self._adaptive_contact_time = 0.0
        self.adaptive_scene = True
        self.set_diagnostic_circuit("normal")

    @_synchronized_io
    def configure_hopping_scene(self, params) -> None:
        """Reuse the low-noise contact model for combinatorial scans only.

        Place the surface within the approach interval, independent of the
        instrument's full piezo range. Single-row/column scans remain valid.
        Never change feedback thresholds or any hardware configuration.
        """
        from types import SimpleNamespace
        x0, x1 = sorted((params.x_start_um, params.x_end_um))
        y0, y1 = sorted((params.y_start_um, params.y_end_um))
        self.configure_adaptive_scene(SimpleNamespace(
            start_z_um=params.start_z_um, end_z_um=params.end_z_um,
            x_min_um=x0, x_max_um=max(x1, x0 + 1),
            y_min_um=y0, y_max_um=max(y1, y0 + 1)))
        self._hopping_scene = True
        self.adaptive_pixel = -1

    @_synchronized_io
    def begin_hopping_point(self, point: int) -> None:
        """Reset contact memory before moving to a fresh simulated landing."""
        if getattr(self, "_hopping_scene", False):
            self.adaptive_pixel = point
            self._adaptive_wet_pixel = None

    @_synchronized_io
    def clear_hopping_scene(self) -> None:
        """Prevent a combinatorial scene leaking into another experiment."""
        if getattr(self, "_hopping_scene", False):
            self.adaptive_scene = False
            self.adaptive_pixel = -1
            self._hopping_scene = False

    @_synchronized_io
    def commanded_position(self) -> dict[str, float]:
        """Return current simulated AO positions, not pending motion targets."""
        return dict(self._positions)

    @property
    def operator_paused(self) -> bool:
        """Expose simulator operator pause to higher-level supervisors."""
        return self._paused

    @property
    def label(self) -> str:
        """Identify this backend as the deterministic simulator."""
        return "Simulator"

    @_synchronized_io
    def connect(self, *, allow_startup_actuation: bool = False) -> None:
        """Reset simulator timing and mark it connected without physical output."""
        self.connected = True
        self._started = self._last_tick = time.monotonic()
        self._paused = False
        self._paused_at = None
        self._paused_duration_s = 0.0

    def experiment_time(self) -> float:
        """Return a monotonic clock with simulator pause durations removed."""
        now = time.monotonic()
        current_pause = now - self._paused_at if self._paused_at is not None else 0.0
        return now - self._paused_duration_s - current_pause

    @_synchronized_io
    def disconnect(self) -> None:
        """Mark the simulator offline without changing its last values."""
        self.connected = False

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.25)
        self._last_tick = now
        if self._paused:
            return
        for axis in ("X", "Y", "Z"):
            current, target = self._positions[axis], self._targets[axis]
            delta = target - current
            step = self._speeds[axis] * dt
            if abs(delta) <= step:
                self._positions[axis] = target
                self._speeds[axis] = 0.0
            elif step > 0:
                self._positions[axis] += math.copysign(step, delta)

    @_synchronized_io
    def read_samples(self) -> list[Sample]:
        """Use sample-clock RC synthesis only during the experimental EIS method."""
        source = getattr(self, "_eis_source", None)
        return source.read_batch() if source is not None else [self.read_sample()]

    @_synchronized_io
    def read_sample(self) -> Sample:
        """Advance motion and synthesize calibrated position/current channels."""
        if not self.connected:
            raise BackendError("Simulator is not connected.")
        self._tick()
        elapsed = time.monotonic() - self._started
        z = self._positions["Z"]
        v = self.settings.polarity_factor * self._voltage[1]
        surface_z = self.surface_z_at(self._positions["X"], self._positions["Y"])
        contact = 1.0 / (1.0 + math.exp(-(z - surface_z) / 0.38))
        faradaic = 1.8 * math.tanh((v - 0.08) * 3.2)
        capacitive = 0.12 * math.sin(elapsed * 8.0)
        drift = 0.12 * math.sin(elapsed / 9.0)
        noise = self._rng.gauss(0.0, 0.035)
        current1 = 0.22 + drift + contact * (2.7 + faradaic) + capacitive + noise
        current2 = -0.15 + contact * 0.7 + self._rng.gauss(0.0, 0.025)
        if getattr(self, "adaptive_scene", False):
            start,end,x0,x1,y0,y1 = self._adaptive_bounds
            x,y = (self._positions['X']-x0)/(x1-x0),(self._positions['Y']-y0)/(y1-y0)
            activity = .025 + .15*math.exp(-((x-.7)**2+(y-.65)**2)/.025)
            pixel = getattr(self, "adaptive_pixel", -1)
            failed = getattr(self, "adaptive_failure_pixel", -99) == pixel
            direction = 1 if end > start else -1
            depth = direction * (z - surface_z)
            approaching = (not getattr(self, "_hopping_scene", False)
                           or self._targets["Z"] == end)
            # Latch meniscus contact until retract: stopping Z on the transient
            # must not immediately turn the cell back into an open circuit.
            if pixel < 0 or failed or depth < -.2 or self._adaptive_wet_pixel != pixel:
                self._adaptive_wet_pixel = None
            if pixel >= 0 and not failed and depth >= 0 and approaching and self._adaptive_wet_pixel is None:
                self._adaptive_wet_pixel = pixel
                self._adaptive_contact_time = elapsed
            wet = self._adaptive_wet_pixel is not None
            transient = .04*math.exp(-max(0,elapsed-self._adaptive_contact_time)/.15) if wet else 0.0
            # 40 pA charging transient + 15 pA sustained contact baseline;
            # 0.15 pA noise cannot normally trigger the default 5 pA criterion.
            current1 = .00015 + wet*(.015 + activity*(v+.25)) + transient + self._rng.gauss(0,.00015)
            current2 = current1*.8 + self._rng.gauss(0,.0001)
        dt = elapsed - self._last_sample_elapsed
        scan_rate = (v - self._last_sample_voltage) / dt if dt > 0 else 0.0
        self._last_sample_voltage = v
        self._last_sample_elapsed = elapsed
        if self._diagnostic_mode == "open":
            current1 = 0.018 + 0.075 * scan_rate + self._rng.gauss(0.0, 0.004)
            current2 = -0.012 + 0.050 * scan_rate + self._rng.gauss(0.0, 0.004)
        elif self._diagnostic_mode in {"resistor", "pipette"}:
            resistance = self._diagnostic_resistance_mohm
            capacitance_nf = 0.025 if self._diagnostic_mode == "resistor" else 0.12
            current1 = 1000.0 * v / resistance + capacitance_nf * scan_rate + self._rng.gauss(0.0, 0.01)
            current2 = 0.5 * current1 + self._rng.gauss(0.0, 0.01)
        return Sample(
            elapsed_s=elapsed,
            x_um=self._positions["X"],
            y_um=self._positions["Y"],
            z_um=z,
            voltage1_v=self._voltage[1],
            voltage2_v=self._voltage[2],
            current1_na=self.settings.polarity_factor * current1,
            current2_na=self.settings.polarity_factor * current2,
            line_number=self._line_number,
            cv_rate_index=self._cv_rate_index,
            commanded_x_um=self._positions["X"],
            commanded_y_um=self._positions["Y"],
            commanded_z_um=self._positions["Z"],
            scan_pixel=(-1 if getattr(self, "_hopping_scene", False) else getattr(self, "adaptive_pixel", -1)),
        )

    @_synchronized_io
    def move(self, axis: str, target: float, speed: float) -> None:
        """Set a validated simulator target or delegate a potential command."""
        self._validate_output(axis, target, speed)
        if axis == "Voltage 1":
            self.set_voltage(1, target)
            return
        if axis == "Voltage 2":
            self.set_voltage(2, target)
            return
        self._targets[axis] = target
        self._speeds[axis] = speed
        self._line_number += 1

    @_synchronized_io
    def stop_motion(self) -> None:
        """Freeze all simulated axes at their current positions."""
        self._tick()
        self._targets = dict(self._positions)
        self._speeds = {"X": 0.0, "Y": 0.0, "Z": 0.0}

    @_synchronized_io
    def set_voltage(self, channel: int, voltage: float) -> None:
        """Set one simulated potential output after channel/range checks."""
        if channel not in (1, 2) or not -10 <= voltage <= 10:
            raise SafetyError("Voltage output must be channel 1 or 2 and within +/-10 V.")
        self._voltage[channel] = voltage
        if channel == 1:
            self._cv_rate_index = -1

    @_synchronized_io
    def set_cv_voltage(self, voltage: float, rate_index: int) -> None:
        """Atomically associate simulated E1 output with its acquisition rate block."""
        self.set_voltage(1, voltage)
        self._cv_rate_index = rate_index

    @_synchronized_io
    def pause(self) -> None:
        """Freeze simulated movement and experiment-time progression."""
        if not self._paused:
            self._paused = True
            self._paused_at = time.monotonic()

    @_synchronized_io
    def resume(self) -> None:
        """Resume simulated time while accounting for paused duration."""
        now = time.monotonic()
        if self._paused_at is not None:
            self._paused_duration_s += max(0.0, now - self._paused_at)
            self._paused_at = None
        self._paused = False
        self._last_tick = now

    @_synchronized_io
    def end_current_waypoint(self) -> None:
        """Complete every simulated axis target and increment its line tag."""
        self._positions.update(self._targets)
        self._speeds = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._line_number += 1

    @_synchronized_io
    def set_diagnostic_circuit(self, mode: str, resistance_mohm: float | None = None) -> None:
        """Select deterministic open/resistor/pipette response synthesis."""
        super().set_diagnostic_circuit(mode, resistance_mohm)
        if resistance_mohm is not None:
            if not math.isfinite(resistance_mohm) or resistance_mohm <= 0:
                raise ValueError("Diagnostic resistance must be positive.")
            self._diagnostic_resistance_mohm = resistance_mohm
        elif mode == "pipette":
            self._diagnostic_resistance_mohm = 120.0
        self._diagnostic_mode = mode


class NIFPGABackend(InstrumentBackend):
    """NI-FPGA adapter for the compiled WEC-SPM target.

    The bundled native driver implements the FIFO layout used by the WEC-SPM
    FPGA Target.vi. A site module exposing ``create_driver(session, settings)``
    can still override it for a deliberately modified target build.
    """

    def __init__(self, settings: AppSettings, driver_module: str | None = None) -> None:
        super().__init__(settings)
        self._session: Any = None
        self._driver: Any = None
        self._started = 0.0
        self._startup_verified = False
        self.driver_module = driver_module

    @property
    def label(self) -> str:
        """Return the NI backend label including its resource alias."""
        return f"NI FPGA · {self.settings.resource}"

    @property
    def startup_notice(self) -> str:
        """Describe unavoidable AO startup values in raw and physical terms."""
        x_um = raw_to_position(
            DEPLOYED_STARTUP_RAW_OUTPUTS["Applied X"], self.settings.x_range_um, self.settings.x_bipolar
        )
        y_um = raw_to_position(
            DEPLOYED_STARTUP_RAW_OUTPUTS["Applied Y"], self.settings.y_range_um, self.settings.y_bipolar
        )
        z_um = raw_to_position(
            DEPLOYED_STARTUP_RAW_OUTPUTS["Applied Z"], self.settings.z_range_um, self.settings.z_bipolar
        )
        return (
            "Connecting resets and reinitializes the FPGA, ending any previous execution and potentially changing outputs. "
            "Close LabVIEW and any other application controlling this device before continuing. "
            "Running the deployed FPGA immediately sets AO0/X and AO1/Y to about +5 V "
            f"(approximately X {x_um:.1f} µm, Y {y_um:.1f} µm with the configured calibration), "
            f"AO2/Z to 0 V (approximately Z {z_um:.1f} µm), and E1/E2 to 0 V. "
            "External Pause does not block these startup writes. Ensure the probe is safely retracted "
            "and the stage can move before continuing."
        )

    @property
    def startup_verified(self) -> bool:
        """Return whether the current session passed applied-output checks."""
        return self._startup_verified

    @property
    def motion_available(self) -> bool:
        """Return whether the active driver exposes move and stop controls."""
        return self._driver is not None and all(
            callable(getattr(self._driver, name, None)) for name in ("move", "stop_motion")
        )

    @property
    def hardware_approach_cv_required(self) -> bool:
        """Declare that NI contact-gated methods must remain FPGA-resident."""
        return True

    @property
    def approach_cv_available(self) -> bool:
        """Return whether the driver implements the complete staged contract."""
        return self._driver is not None and all(
            callable(getattr(self._driver, name, None))
            for name in ("start_approach_cv", "approach_cv_status", "stop_motion")
        )

    @property
    def scan_hopping_cv_available(self) -> bool:
        """Return whether the driver implements hopping CV and sample context."""
        return self._driver is not None and all(
            callable(getattr(self._driver, name, None))
            for name in ("start_scan_hopping_cv", "scan_hopping_cv_status", "scan_context", "stop_motion")
        )

    @property
    def full_rate_data_available(self) -> bool:
        """Return whether the driver can drain complete FIFO sample batches."""
        return self._driver is not None and callable(getattr(self._driver, "read_samples", None))

    def hardware_program_available(self, name: str) -> bool:
        """Check the shared driver interface and supported method-name set."""
        return self._driver is not None and all(
            callable(getattr(self._driver, method, None))
            for method in ("start_method", "method_status", "method_context")
        ) and name in {"cv", "approach", "approach_it", "scan_hopping_it"}

    @property
    def capabilities(self) -> BackendCapabilities:
        """Derive UI controls from methods actually supplied by the driver."""
        driver = self._driver
        supports = lambda name: driver is not None and callable(getattr(driver, name, None))
        return BackendCapabilities(
            supports("pause") and supports("resume"),
            supports("end_current_waypoint"),
            supports("set_voltage") and supports("set_live_potential"),
            self.full_rate_data_available,
            self.full_rate_data_available,
        )

    @property
    def position_readback_label(self) -> str:
        """Describe AI readback or the limited applied-register fallback."""
        if not self.full_rate_data_available:
            return "Commanded output register (not measured position)"
        return "Measured AI0/AI1/AI2"

    @_synchronized_io
    def connect(self, *, allow_startup_actuation: bool = False) -> None:
        """Validate/open/run the target and verify its unavoidable startup state."""
        settings_errors = self.settings.validate()
        if settings_errors:
            raise BackendError("Invalid instrument settings: " + "; ".join(settings_errors))
        if not allow_startup_actuation:
            raise BackendError(
                "Connecting this FPGA changes X/Y/Z and E1/E2 outputs immediately. "
                "Use the application connection confirmation to authorize the documented startup outputs."
            )
        try:
            from nifpga import Session
        except ImportError as exc:
            raise BackendError("NI FPGA support is not installed. Run: pip install -e '.[fpga]'") from exc
        bitfile = Path(self.settings.bitfile).expanduser().resolve()
        if not bitfile.exists():
            raise BackendError(f"FPGA bitfile not found: {bitfile}")
        try:
            info = inspect_bitfile(bitfile)
            compatibility_errors = validate_wec_bitfile(info, self.settings.hardware_transport)
            if compatibility_errors:
                raise BackendError(
                    f"The selected bitfile is not compatible with this configuration ({info.target_class}): "
                    + "; ".join(compatibility_errors)
                )
            # no_run suppresses a new start but does not stop an existing VI.
            # Reset before configuring registers/FIFOs so reconnect starts from
            # fresh target state and does not erase the new configuration.
            self._session = Session(str(bitfile), self.settings.resource, no_run=True)
            self._session.reset()
            state = self._session.fpga_vi_state.name
            if state != "NotRunning":
                raise BackendError(
                    f"FPGA reset did not leave the target stopped (state: {state}). "
                    "Close other NI/LabVIEW controllers and check the device in NI MAX before reconnecting."
                )
            self._session.registers["External Stop"].write(False)
            self._session.registers["External Pause"].write(True)
            if self.driver_module:
                module = importlib.import_module(self.driver_module)
                self._driver = module.create_driver(self._session, self.settings)
            else:
                from .ni_driver import create_driver

                self._driver = create_driver(self._session, self.settings)
            self._session.run()
            wait_until_ready = getattr(self._driver, "wait_until_ready", None)
            verify_startup_state = getattr(self._driver, "verify_startup_state", None)
            if not callable(wait_until_ready) or not callable(verify_startup_state):
                raise RuntimeError(
                    "The FPGA driver does not implement the required startup-ready and output-verification handshake."
                )
            wait_until_ready()
            verify_startup_state()
            self._startup_verified = True
            self.connected = True
            self._started = time.monotonic()
        except Exception as exc:
            self.disconnect()
            raise BackendError(f"Could not open {self.settings.resource}: {exc}") from exc

    @_synchronized_io
    def disconnect(self) -> None:
        """Pause and close the NI session, clearing driver and verification state."""
        if self._session is not None:
            try:
                self._session.registers["External Pause"].write(True)
            except Exception:
                pass
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None
        self._driver = None
        self._startup_verified = False
        self.connected = False

    def _register(self, name: str) -> Any:
        if not self.connected or self._session is None:
            raise BackendError("NI FPGA is not connected.")
        try:
            return self._session.registers[name]
        except KeyError as exc:
            raise BackendError(f"The selected bitfile has no '{name}' register.") from exc

    @staticmethod
    def _raw_to_voltage(raw: int) -> float:
        return raw_to_adc_voltage(raw)

    def _raw_to_position(self, raw: int, axis: str) -> float:
        span = getattr(self.settings, f"{axis.lower()}_range_um")
        bipolar = getattr(self.settings, f"{axis.lower()}_bipolar")
        return raw_to_position(raw, span, bipolar)

    def _raw_to_current(self, raw: int, channel: int) -> float:
        if channel not in (1, 2):
            raise BackendError(f"Unknown current channel: {channel}")
        return self.settings.polarity_factor * raw_to_current(raw, getattr(self.settings, f"current{channel}_v_per_na"))

    @property
    def adaptive_contact_available(self) -> bool:
        """Require a driver that snapshots commanded Z at confirmed contact."""
        return bool(getattr(self._driver, "supports_contact_snapshot", False))

    @property
    def operator_paused(self) -> bool:
        """Distinguish operator intent from internal FPGA contact pauses."""
        return bool(getattr(self._driver, "_operator_paused", False))

    @_synchronized_io
    def commanded_position(self) -> dict[str, float]:
        """Read applied AO coordinates, explicitly distinct from position sensors."""
        return {axis:self._raw_to_position(int(self._register('Applied '+axis).read()),axis) for axis in 'XYZ'}

    @_synchronized_io
    def confirmed_contact_z(self) -> float | None:
        """Return the native driver's frozen approach contact, never a live AO value."""
        return getattr(self._driver, "approach_contact_z_um", None)

    @_synchronized_io
    def read_sample(self) -> Sample:
        """Read a low-rate calibrated snapshot from applied/register values."""
        read = lambda name: int(self._register(name).read())
        return Sample(
            elapsed_s=time.monotonic() - self._started,
            x_um=self._raw_to_position(read("Applied X"), "X"),
            y_um=self._raw_to_position(read("Applied Y"), "Y"),
            z_um=self._raw_to_position(read("Applied Z"), "Z"),
            voltage1_v=self.settings.polarity_factor * raw_to_voltage1(read("Applied Voltage"), self.settings.command_voltage_ratio),
            voltage2_v=self.settings.polarity_factor * self._raw_to_voltage(read("Applied Voltage 2")),
            current1_na=self._raw_to_current(read("MeasuredCurrent"), 1),
            current2_na=self._raw_to_current(read("MeasuredCurrent 2"), 2),
            line_number=read("LineNumber"),
        )

    @_synchronized_io
    def read_samples(self) -> list[Sample]:
        """Drain all complete FIFO frames and attach non-time-aligned AO snapshots."""
        if not self.full_rate_data_available:
            return [self.read_sample()]
        try:
            samples = list(self._driver.read_samples())
        except Exception as exc:
            raise BackendError(f"Could not drain FPGA data FIFO: {exc}") from exc
        if not all(isinstance(sample, Sample) for sample in samples):
            raise BackendError("Site driver read_samples() must return eChemTips Sample objects.")
        if samples:
            commanded = {
                "x": self._raw_to_position(int(self._register("Applied X").read()), "X"),
                "y": self._raw_to_position(int(self._register("Applied Y").read()), "Y"),
                "z": self._raw_to_position(int(self._register("Applied Z").read()), "Z"),
            }
            for sample in samples:
                sample.commanded_x_um = commanded["x"]
                sample.commanded_y_um = commanded["y"]
                sample.commanded_z_um = commanded["z"]
        return samples

    @_synchronized_io
    def pause(self) -> None:
        """Delegate acknowledged operator pause to the active driver."""
        if self._driver is None or not callable(getattr(self._driver, "pause", None)):
            raise BackendError("This FPGA driver does not support pause.")
        self._driver.pause()

    @_synchronized_io
    def resume(self) -> None:
        """Delegate resume while preserving FPGA feedback-owned pauses."""
        if self._driver is None or not callable(getattr(self._driver, "resume", None)):
            raise BackendError("This FPGA driver does not support resume.")
        self._driver.resume()

    @_synchronized_io
    def end_current_waypoint(self) -> None:
        """Delegate framed EndCurrentLine acknowledgement to the driver."""
        if self._driver is None or not callable(getattr(self._driver, "end_current_waypoint", None)):
            raise BackendError("This FPGA driver does not support EndCurrentLine.")
        self._driver.end_current_waypoint()

    @_synchronized_io
    def accept_approach(self) -> None:
        """Explicitly accept current Z only while an approach stage owns motion."""
        if self._driver is None or not callable(getattr(self._driver, "accept_approach", None)):
            raise BackendError("This FPGA driver does not support manual approach acceptance.")
        self._driver.accept_approach()

    @_synchronized_io
    def configure_feedback(self, config: FeedbackConfiguration) -> None:
        """Delegate supported Current 1/2 feedback configuration."""
        if self._driver is None or not callable(getattr(self._driver, "configure_feedback", None)):
            raise BackendError("This FPGA driver does not expose advanced feedback controls.")
        self._driver.configure_feedback(config)

    @_synchronized_io
    def execution_status(self) -> ExecutionSnapshot:
        """Return native driver counters or an idle fallback snapshot."""
        if self._driver is None or not callable(getattr(self._driver, "execution_status", None)):
            return super().execution_status()
        return self._driver.execution_status()

    def _require_idle_driver(self) -> None:
        if self._driver is not None and callable(getattr(self._driver, "ensure_idle", None)):
            try:
                self._driver.ensure_idle()
            except Exception as exc:
                raise BackendError(str(exc)) from exc

    @_synchronized_io
    def move(self, axis: str, target: float, speed: float) -> None:
        """Validate, claim idle driver ownership, and submit one piezo move."""
        self._validate_output(axis, target, speed)
        self._require_idle_driver()
        if axis == "Voltage 1":
            self.set_live_potential(1, target)
        elif axis == "Voltage 2":
            self.set_live_potential(2, target)
        elif self._driver is not None:
            try:
                self._driver.move(axis, target, speed)
                self._register("External Pause").write(False)
            except Exception as exc:
                self._register("External Pause").write(True)
                raise BackendError(f"Could not submit motion: {exc}") from exc
        else:
            raise BackendError(
                "Physical motion needs a site waypoint driver matching this FPGA build. "
                "Set ECHEMTIPS_DRIVER_MODULE after validating the cluster layout on the instrument."
            )

    @_synchronized_io
    def stop_motion(self) -> None:
        """Pause and cancel motion, retiring uncertain hardware framing."""
        if not self.connected:
            return
        self._register("External Pause").write(True)
        if self._driver is not None and callable(getattr(self._driver, "stop_motion", None)):
            try:
                self._driver.stop_motion()
            except Exception as exc:
                raise BackendError(f"Could not clear FPGA motion state: {exc}") from exc

    @_synchronized_io
    def start_hardware_approach_cv(self, params: ApproachCVParameters) -> None:
        """Claim the idle NI driver and start contact-gated Approach + CV."""
        if not self.approach_cv_available:
            raise BackendError(
                "Approach + CV requires a site driver with FPGA waypoint sequence support "
                "(start_approach_cv, approach_cv_status, and stop_motion)."
            )
        if params.scan_rates_v_s is not None and not (
            getattr(self._driver, "supports_cv_rate_series", False) is True
            and callable(getattr(self._driver, "approach_context", None))
        ):
            raise BackendError(
                "The selected FPGA driver does not support CV scan-rate series. "
                "Use the native driver or a site driver with explicit series support."
            )
        if getattr(params, "waveform", "CV") == "LSV" and getattr(self._driver, "supports_lsv", False) is not True:
            raise BackendError("The selected FPGA driver does not explicitly support LSV. Use the native driver.")
        self._require_idle_driver()
        self._register("External Stop").write(False)
        try:
            self._driver.start_approach_cv(params)
            self._register("External Pause").write(False)
        except Exception as exc:
            self._register("External Pause").write(True)
            raise BackendError(f"Could not start FPGA approach + CV sequence: {exc}") from exc

    @_synchronized_io
    def hardware_approach_cv_status(self) -> HardwareSequenceUpdate:
        """Validate and normalize native Approach + CV status fields."""
        if not self.approach_cv_available:
            raise BackendError("The site driver cannot report approach + CV sequence state.")
        try:
            raw = self._driver.approach_cv_status()
        except Exception as exc:
            raise BackendError(f"Could not read FPGA approach + CV state: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise BackendError("Site driver approach_cv_status() must return a mapping.")
        stage = str(raw.get("stage", "")).strip().lower()
        allowed = {"preposition", "approaching", "contact", "settling", "cv", "retracting", "complete", "aborted"}
        if stage not in allowed:
            raise BackendError(f"Site driver returned an invalid approach + CV stage: {stage!r}.")
        try:
            progress = max(0.0, min(1.0, float(raw.get("progress", 0.0))))
        except (TypeError, ValueError) as exc:
            raise BackendError("Site driver returned an invalid sequence progress value.") from exc
        return HardwareSequenceUpdate(stage, str(raw.get("detail", stage.title())), progress)

    @_synchronized_io
    def hardware_approach_context(self, line_number: int) -> str:
        """Return the native stage associated with an approach sample tag."""
        if not self.approach_cv_available:
            return ""
        context = getattr(self._driver, "approach_context", None)
        if not callable(context):
            return ""
        try:
            return str(context(line_number)).strip().lower()
        except Exception as exc:
            raise BackendError(f"Could not identify FPGA approach sample: {exc}") from exc

    @_synchronized_io
    def start_hardware_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
        """Claim the idle NI driver and start hopping CV."""
        if not self.scan_hopping_cv_available:
            raise BackendError("Scan Hopping + CV requires the native eChemTips scan waypoint interface.")
        if getattr(params, "waveform", "CV") == "LSV" and getattr(self._driver, "supports_lsv", False) is not True:
            raise BackendError("The selected FPGA driver does not explicitly support LSV. Use the native driver.")
        self._require_idle_driver()
        self._register("External Stop").write(False)
        try:
            self._driver.start_scan_hopping_cv(params)
            self._register("External Pause").write(False)
        except Exception as exc:
            self._register("External Pause").write(True)
            raise BackendError(f"Could not start FPGA Scan Hopping + CV sequence: {exc}") from exc

    @_synchronized_io
    def hardware_scan_hopping_cv_status(self) -> HardwareSequenceUpdate:
        """Normalize hopping-CV stage, point, detail, and progress."""
        if not self.scan_hopping_cv_available:
            raise BackendError("The FPGA driver cannot report Scan Hopping + CV state.")
        try:
            raw = self._driver.scan_hopping_cv_status()
            return HardwareSequenceUpdate(
                str(raw.get("stage", "aborted")),
                str(raw.get("detail", "Scan stopped")),
                max(0.0, min(1.0, float(raw.get("progress", 0.0)))),
                int(raw.get("point_index", -1)),
                str(raw.get("point_stage", "")),
            )
        except Exception as exc:
            raise BackendError(f"Could not read FPGA Scan Hopping + CV state: {exc}") from exc

    @_synchronized_io
    def hardware_scan_context(self, line_number: int) -> tuple[int, str]:
        """Return native hopping-CV pixel/stage context for a sample tag."""
        if not self.scan_hopping_cv_available:
            return -1, ""
        try:
            point, stage = self._driver.scan_context(line_number)
            return int(point), str(stage)
        except Exception as exc:
            raise BackendError(f"Could not identify FPGA scan sample: {exc}") from exc

    @_synchronized_io
    def start_hardware_program(self, name: str, parameters: object) -> None:
        """Start CV, Approach, Approach + I–t, or hopping I–t on the driver."""
        if not self.hardware_program_available(name):
            raise BackendError(f"The FPGA driver does not expose the {name!r} shared method.")
        if getattr(parameters, "waveform", "CV") == "LSV" and getattr(self._driver, "supports_lsv", False) is not True:
            raise BackendError("The selected FPGA driver does not explicitly support LSV. Use the native driver.")
        self._require_idle_driver()
        self._register("External Stop").write(False)
        try:
            self._driver.start_method(name, parameters)
            self._register("External Pause").write(False)
        except Exception as exc:
            self._register("External Pause").write(True)
            raise BackendError(f"Could not start FPGA {name}: {exc}") from exc

    @_synchronized_io
    def hardware_program_status(self) -> HardwareSequenceUpdate:
        """Normalize status for the currently active shared hardware method."""
        if self._driver is None or not callable(getattr(self._driver, "method_status", None)):
            raise BackendError("The FPGA driver cannot report shared method state.")
        try:
            raw = self._driver.method_status()
            return HardwareSequenceUpdate(
                str(raw.get("stage", "aborted")), str(raw.get("detail", "")),
                max(0.0, min(1.0, float(raw.get("progress", 0.0)))),
                int(raw.get("point_index", -1)), str(raw.get("point_stage", "")),
            )
        except Exception as exc:
            raise BackendError(f"Could not read shared FPGA method state: {exc}") from exc

    @_synchronized_io
    def hardware_program_context(self, line_number: int) -> tuple[int, str]:
        """Return native shared-method pixel/stage context for a sample tag."""
        if self._driver is None or not callable(getattr(self._driver, "method_context", None)):
            return -1, ""
        try:
            point, stage = self._driver.method_context(line_number)
            return int(point), str(stage)
        except Exception as exc:
            raise BackendError(f"Could not identify shared FPGA method sample: {exc}") from exc

    @_synchronized_io
    def set_voltage(self, channel: int, voltage: float) -> None:
        """Submit and wait for an idle potential jump acknowledgement."""
        if channel not in (1, 2) or not -10 <= voltage <= 10:
            raise SafetyError("Voltage output must be channel 1 or 2 and within +/-10 V.")
        if channel == 1 and abs(voltage * self.settings.command_voltage_ratio) > 10:
            raise SafetyError("Voltage 1 exceeds the amplifier command range at this command ratio.")
        if self._driver is None or not callable(getattr(self._driver, "set_voltage", None)):
            raise BackendError(
                "The FPGA driver cannot apply an acknowledged idle potential waypoint. "
                "Use the bundled eChemTips driver or a compatible site driver."
            )
        try:
            self._driver.set_voltage(channel, voltage)
        except Exception as exc:
            raise BackendError(f"Could not apply Potential {channel}: {exc}") from exc

    @_synchronized_io
    def set_live_potential(self, channel: int, voltage: float) -> None:
        """Request ChangeOnFly and wait for applied-value acknowledgement."""
        if channel not in (1, 2) or not -10 <= voltage <= 10:
            raise SafetyError("Potential output must be channel 1 or 2 and within +/-10 V.")
        if channel == 1 and abs(voltage * self.settings.command_voltage_ratio) > 10:
            raise SafetyError("Potential 1 exceeds the amplifier command range at this command ratio.")
        if self._driver is None or not callable(getattr(self._driver, "set_live_potential", None)):
            raise BackendError("The FPGA driver cannot acknowledge a controlled live-potential command.")
        try:
            self._driver.set_live_potential(channel, voltage)
        except Exception as exc:
            raise BackendError(f"Could not apply live Potential {channel}: {exc}") from exc

    @_synchronized_io
    def emergency_stop(self) -> None:
        """Latch the driver and verify External Pause plus External Stop."""
        if not self.connected:
            return
        if self._driver is not None and callable(getattr(self._driver, "emergency_stop", None)):
            try:
                self._driver.emergency_stop()
                return
            except Exception as exc:
                raise BackendError(f"Could not verify FPGA emergency stop: {exc}") from exc
        errors: list[str] = []
        for name in ("External Pause", "External Stop"):
            try:
                self._register(name).write(True)
            except Exception as exc:
                errors.append(f"could not assert {name}: {exc}")
        if self._driver is not None and callable(getattr(self._driver, "stop_motion", None)):
            try:
                self._driver.stop_motion()
            except Exception as exc:
                errors.append(f"could not latch the site driver: {exc}")
        if errors:
            raise BackendError("Emergency stop state is uncertain: " + "; ".join(errors))


def create_backend(settings: AppSettings, driver_module: str | None = None) -> InstrumentBackend:
    """Construct the selected backend without connecting or changing outputs."""
    if settings.mode == "NI FPGA":
        return NIFPGABackend(settings, driver_module=driver_module)
    return SimulationBackend(settings)
