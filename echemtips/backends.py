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
    inspect_bitfile,
    raw_to_adc_voltage,
    raw_to_current,
    raw_to_position,
    raw_to_voltage1,
    validate_wec_bitfile,
    voltage1_to_raw,
)


class BackendError(RuntimeError):
    """A device connection or I/O operation failed."""


class SafetyError(BackendError):
    """An output request violated a configured safety limit."""


@dataclass(frozen=True, slots=True)
class HardwareSequenceUpdate:
    stage: str
    detail: str
    progress: float
    point_index: int = -1
    point_stage: str = ""


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    profile: str
    pause_resume: bool
    end_current_waypoint: bool
    live_potential: bool
    secondary_feedback: bool
    proportional_feedback: bool
    measured_position: bool
    full_rate_acquisition: bool


def _synchronized_io(method):
    """Serialize commands with the background FIFO acquisition thread."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.io_lock:
            return method(self, *args, **kwargs)
    return guarded


class InstrumentBackend(ABC):
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.connected = False
        self.io_lock = threading.RLock()

    @property
    def motion_available(self) -> bool:
        return True

    @property
    def hardware_approach_cv_required(self) -> bool:
        return False

    @property
    def approach_cv_available(self) -> bool:
        return True

    @property
    def scan_hopping_cv_available(self) -> bool:
        return not self.hardware_approach_cv_required

    @property
    def full_rate_data_available(self) -> bool:
        return True

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            self.settings.instrument_profile, True, True, True, False, False, True,
            self.full_rate_data_available,
        )

    @property
    def position_readback_label(self) -> str:
        return "Measured/simulated position"

    @property
    @abstractmethod
    def label(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_sample(self) -> Sample:
        raise NotImplementedError

    @_synchronized_io
    def read_samples(self) -> list[Sample]:
        """Return all newly available samples; simple backends return one."""
        return [self.read_sample()]

    @abstractmethod
    def move(self, axis: str, target: float, speed: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop_motion(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_voltage(self, channel: int, voltage: float) -> None:
        raise NotImplementedError

    def set_live_potential(self, channel: int, voltage: float) -> None:
        """Apply a bounded ChangeOnFly potential without taking motion ownership."""
        self.set_voltage(channel, voltage)

    def start_hardware_approach_cv(self, params: ApproachCVParameters) -> None:
        raise BackendError("This backend does not execute FPGA waypoint sequences.")

    def hardware_approach_cv_status(self) -> HardwareSequenceUpdate:
        raise BackendError("This backend does not report FPGA waypoint sequence state.")

    def hardware_approach_context(self, line_number: int) -> str:
        """Identify which approach/CV waypoint produced a hardware sample."""
        return ""

    def start_hardware_scan_hopping_cv(self, params: ScanHoppingCVParameters) -> None:
        raise BackendError("This backend does not execute FPGA scan-hopping sequences.")

    def hardware_scan_hopping_cv_status(self) -> HardwareSequenceUpdate:
        raise BackendError("This backend does not report FPGA scan-hopping state.")

    def hardware_scan_context(self, line_number: int) -> tuple[int, str]:
        return -1, ""

    def hardware_program_available(self, name: str) -> bool:
        return False

    def start_hardware_program(self, name: str, parameters: object) -> None:
        raise BackendError(f"This backend cannot execute the FPGA method {name!r}.")

    def hardware_program_status(self) -> HardwareSequenceUpdate:
        raise BackendError("This backend does not report shared FPGA method state.")

    def hardware_program_context(self, line_number: int) -> tuple[int, str]:
        return -1, ""

    def pause(self) -> None:
        raise BackendError("Pause is not supported by this backend.")

    def resume(self) -> None:
        raise BackendError("Resume is not supported by this backend.")

    def end_current_waypoint(self) -> None:
        raise BackendError("Ending the current waypoint is not supported by this backend.")

    def configure_feedback(self, config: FeedbackConfiguration) -> None:
        if config.secondary_enabled or config.proportional_gain:
            raise BackendError("Advanced feedback is not executed by this backend.")

    def execution_status(self) -> ExecutionSnapshot:
        return ExecutionSnapshot("", ExecutionState.IDLE, 0, 0, 0, 0, "No FPGA program")

    def emergency_stop(self) -> None:
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

    def surface_z_at(self, x_um: float, y_um: float) -> float:
        sx = (x_um / self.settings.x_range_um - 0.5) * math.tau
        sy = (y_um / self.settings.y_range_um - 0.5) * math.tau
        return self.settings.z_range_um * 0.68 + 2.4 * math.sin(sx) * math.cos(sy) + 0.7 * math.sin(2 * sy)

    @property
    def label(self) -> str:
        return "Simulator"

    @_synchronized_io
    def connect(self) -> None:
        self.connected = True
        self._started = self._last_tick = time.monotonic()

    @_synchronized_io
    def disconnect(self) -> None:
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
    def read_sample(self) -> Sample:
        if not self.connected:
            raise BackendError("Simulator is not connected.")
        self._tick()
        elapsed = time.monotonic() - self._started
        z = self._positions["Z"]
        v = self._voltage[1]
        surface_z = self.surface_z_at(self._positions["X"], self._positions["Y"])
        contact = 1.0 / (1.0 + math.exp(-(z - surface_z) / 0.38))
        faradaic = 1.8 * math.tanh((v - 0.08) * 3.2)
        capacitive = 0.12 * math.sin(elapsed * 8.0)
        drift = 0.12 * math.sin(elapsed / 9.0)
        noise = self._rng.gauss(0.0, 0.035)
        current1 = 0.22 + drift + contact * (2.7 + faradaic) + capacitive + noise
        current2 = -0.15 + contact * 0.7 + self._rng.gauss(0.0, 0.025)
        current3 = 0.08 + 0.18 * math.sin(elapsed * 1.7) + self._rng.gauss(0.0, 0.02)
        return Sample(
            elapsed_s=elapsed,
            x_um=self._positions["X"],
            y_um=self._positions["Y"],
            z_um=z,
            voltage1_v=v,
            voltage2_v=self._voltage[2],
            current1_na=current1,
            current2_na=current2,
            current3_na=current3,
            current4_na=0.3 * current2,
            lockin_amplitude_na=abs(current1) * 0.08,
            lockin_phase_deg=25.0 + 8.0 * math.sin(elapsed),
            line_number=self._line_number,
            commanded_x_um=self._positions["X"],
            commanded_y_um=self._positions["Y"],
            commanded_z_um=self._positions["Z"],
        )

    @_synchronized_io
    def move(self, axis: str, target: float, speed: float) -> None:
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
        self._tick()
        self._targets = dict(self._positions)
        self._speeds = {"X": 0.0, "Y": 0.0, "Z": 0.0}

    @_synchronized_io
    def set_voltage(self, channel: int, voltage: float) -> None:
        if channel not in (1, 2) or not -10 <= voltage <= 10:
            raise SafetyError("Voltage output must be channel 1 or 2 and within +/-10 V.")
        self._voltage[channel] = voltage

    @_synchronized_io
    def pause(self) -> None:
        self._paused = True

    @_synchronized_io
    def resume(self) -> None:
        self._paused = False
        self._last_tick = time.monotonic()

    @_synchronized_io
    def end_current_waypoint(self) -> None:
        self._positions.update(self._targets)
        self._speeds = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._line_number += 1


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
        self.driver_module = driver_module

    @property
    def label(self) -> str:
        return f"NI FPGA · {self.settings.resource}"

    @property
    def motion_available(self) -> bool:
        return self._driver is not None and all(
            callable(getattr(self._driver, name, None)) for name in ("move", "stop_motion")
        )

    @property
    def hardware_approach_cv_required(self) -> bool:
        return True

    @property
    def approach_cv_available(self) -> bool:
        return self._driver is not None and all(
            callable(getattr(self._driver, name, None))
            for name in ("start_approach_cv", "approach_cv_status", "stop_motion")
        )

    @property
    def scan_hopping_cv_available(self) -> bool:
        return self._driver is not None and all(
            callable(getattr(self._driver, name, None))
            for name in ("start_scan_hopping_cv", "scan_hopping_cv_status", "scan_context", "stop_motion")
        )

    @property
    def full_rate_data_available(self) -> bool:
        return self._driver is not None and callable(getattr(self._driver, "read_samples", None))

    def hardware_program_available(self, name: str) -> bool:
        return self._driver is not None and all(
            callable(getattr(self._driver, method, None))
            for method in ("start_method", "method_status", "method_context")
        ) and name in {"cv", "approach", "approach_it", "scan_hopping_it"}

    @property
    def capabilities(self) -> BackendCapabilities:
        driver = self._driver
        supports = lambda name: driver is not None and callable(getattr(driver, name, None))
        return BackendCapabilities(
            self.settings.instrument_profile,
            supports("pause") and supports("resume"),
            supports("end_current_waypoint"),
            True,
            supports("configure_feedback"),
            supports("configure_feedback"),
            self.full_rate_data_available and not self.settings.read_current4_instead_y,
            self.full_rate_data_available,
        )

    @property
    def position_readback_label(self) -> str:
        if not self.full_rate_data_available:
            return "Commanded output register (not measured position)"
        return "Measured AI0/AI1/AI2" if not self.settings.read_current4_instead_y else "Measured AI0/AI2; Y input is Current 4"

    @_synchronized_io
    def connect(self) -> None:
        settings_errors = self.settings.validate()
        if settings_errors:
            raise BackendError("Invalid instrument settings: " + "; ".join(settings_errors))
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
            # no_run is essential: controls and FIFOs are configured before the
            # FPGA loop can drive an analog output.
            self._session = Session(str(bitfile), self.settings.resource, no_run=True)
            if self._session.fpga_vi_state.name != "NotRunning":
                self._session.close()
                self._session = None
                raise BackendError("FPGA is already running or previously stopped. Reinitialize it in NI MAX/LabVIEW with actuators disabled before connecting.")
            self._session.registers["External Stop"].write(False)
            self._session.registers["External Pause"].write(True)
            if self.driver_module:
                module = importlib.import_module(self.driver_module)
                self._driver = module.create_driver(self._session, self.settings)
            else:
                from .ni_driver import create_driver

                self._driver = create_driver(self._session, self.settings)
            self._session.run()
            if callable(getattr(self._driver, "wait_until_ready", None)):
                self._driver.wait_until_ready()
            self.connected = True
            self._started = time.monotonic()
        except Exception as exc:
            self.disconnect()
            raise BackendError(f"Could not open {self.settings.resource}: {exc}") from exc

    @_synchronized_io
    def disconnect(self) -> None:
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
        if channel not in (1, 2, 3, 4):
            raise BackendError(f"Unknown current channel: {channel}")
        return raw_to_current(raw, getattr(self.settings, f"current{channel}_v_per_na"))

    def _raw_to_lockin_amplitude(self, raw: int) -> float:
        normalized = self._raw_to_voltage(raw) / 10.0
        normalized -= self.settings.lockin_offset_pct / 100.0
        return normalized * self.settings.lockin_sensitivity_na / self.settings.lockin_expand

    @_synchronized_io
    def read_sample(self) -> Sample:
        read = lambda name: int(self._register(name).read())
        return Sample(
            elapsed_s=time.monotonic() - self._started,
            x_um=self._raw_to_position(read("Applied X"), "X"),
            y_um=self._raw_to_position(read("Applied Y"), "Y"),
            z_um=self._raw_to_position(read("Applied Z"), "Z"),
            voltage1_v=raw_to_voltage1(read("Applied Voltage"), self.settings.command_voltage_ratio),
            voltage2_v=self._raw_to_voltage(read("Applied Voltage 2")),
            current1_na=self._raw_to_current(read("MeasuredCurrent"), 1),
            current2_na=self._raw_to_current(read("MeasuredCurrent 2"), 2),
            current3_na=self._raw_to_current(read("MeasuredCurrent 3"), 3),
            current4_na=self._raw_to_current(read("MeasuredCurrent 4"), 4),
            lockin_amplitude_na=self._raw_to_lockin_amplitude(read("Ext amp")),
            lockin_phase_deg=self._raw_to_voltage(read("Ext Phase")) * 18.0,
            line_number=read("LineNumber"),
        )

    @_synchronized_io
    def read_samples(self) -> list[Sample]:
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
        if self._driver is None or not callable(getattr(self._driver, "pause", None)):
            raise BackendError("This FPGA driver does not support pause.")
        self._driver.pause()

    @_synchronized_io
    def resume(self) -> None:
        if self._driver is None or not callable(getattr(self._driver, "resume", None)):
            raise BackendError("This FPGA driver does not support resume.")
        self._driver.resume()

    @_synchronized_io
    def end_current_waypoint(self) -> None:
        if self._driver is None or not callable(getattr(self._driver, "end_current_waypoint", None)):
            raise BackendError("This FPGA driver does not support EndCurrentLine.")
        self._driver.end_current_waypoint()

    @_synchronized_io
    def configure_feedback(self, config: FeedbackConfiguration) -> None:
        if self._driver is None or not callable(getattr(self._driver, "configure_feedback", None)):
            raise BackendError("This FPGA driver does not expose advanced feedback controls.")
        self._driver.configure_feedback(config)

    @_synchronized_io
    def execution_status(self) -> ExecutionSnapshot:
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
        if not self.approach_cv_available:
            raise BackendError(
                "Approach + CV requires a site driver with FPGA waypoint sequence support "
                "(start_approach_cv, approach_cv_status, and stop_motion)."
            )
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
        if not self.approach_cv_available:
            raise BackendError("The site driver cannot report approach + CV sequence state.")
        try:
            raw = self._driver.approach_cv_status()
        except Exception as exc:
            raise BackendError(f"Could not read FPGA approach + CV state: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise BackendError("Site driver approach_cv_status() must return a mapping.")
        stage = str(raw.get("stage", "")).strip().lower()
        allowed = {"preposition", "approaching", "contact", "cv", "retracting", "complete", "aborted"}
        if stage not in allowed:
            raise BackendError(f"Site driver returned an invalid approach + CV stage: {stage!r}.")
        try:
            progress = max(0.0, min(1.0, float(raw.get("progress", 0.0))))
        except (TypeError, ValueError) as exc:
            raise BackendError("Site driver returned an invalid sequence progress value.") from exc
        return HardwareSequenceUpdate(stage, str(raw.get("detail", stage.title())), progress)

    @_synchronized_io
    def hardware_approach_context(self, line_number: int) -> str:
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
        if not self.scan_hopping_cv_available:
            raise BackendError("Scan Hopping + CV requires the native eChemTips scan waypoint interface.")
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
        if not self.scan_hopping_cv_available:
            return -1, ""
        try:
            point, stage = self._driver.scan_context(line_number)
            return int(point), str(stage)
        except Exception as exc:
            raise BackendError(f"Could not identify FPGA scan sample: {exc}") from exc

    @_synchronized_io
    def start_hardware_program(self, name: str, parameters: object) -> None:
        if not self.hardware_program_available(name):
            raise BackendError(f"The FPGA driver does not expose the {name!r} shared method.")
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
        if self._driver is None or not callable(getattr(self._driver, "method_context", None)):
            return -1, ""
        try:
            point, stage = self._driver.method_context(line_number)
            return int(point), str(stage)
        except Exception as exc:
            raise BackendError(f"Could not identify shared FPGA method sample: {exc}") from exc

    @_synchronized_io
    def set_voltage(self, channel: int, voltage: float) -> None:
        if channel not in (1, 2) or not -10 <= voltage <= 10:
            raise SafetyError("Voltage output must be channel 1 or 2 and within +/-10 V.")
        raw = voltage1_to_raw(voltage, self.settings.command_voltage_ratio) if channel == 1 else int(
            max(-32768, min(32767, round(voltage * 32768 / 10)))
        )
        if channel == 1 and abs(voltage * self.settings.command_voltage_ratio) > 10:
            raise SafetyError("Voltage 1 exceeds the amplifier command range at this command ratio.")
        value_name = "V on Fly" if channel == 1 else "V2 on Fly"
        trigger_name = "Change V on Fly" if channel == 1 else "Change V on Fly 2"
        self._register(value_name).write(raw)
        trigger = self._register(trigger_name)
        trigger.write(True)
        trigger.write(False)

    @_synchronized_io
    def emergency_stop(self) -> None:
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
    if settings.mode == "NI FPGA":
        return NIFPGABackend(settings, driver_module=driver_module)
    return SimulationBackend(settings)
