from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
from typing import Any


TARGET_BITFILE_NAME = "wecspm_FPGATarget2_FPGATarget_MAn-McsWIiw.lvbitx"
LEGACY_BITFILE_NAMES = {"FPGAProject_FPGATarget_FPGATarget2_ACEEEF6E.lvbitx"}


def _default_bitfile() -> str:
    """Find a locally supplied target without ever packaging it.

    The sibling lookup supports a layout where the private LabVIEW archive and
    public eChemTips checkout share a parent directory. Other installations
    get a filename placeholder that must be selected before connecting.
    """
    configured = os.environ.get("ECHEMTIPS_BITFILE")
    if configured:
        return str(Path(configured).expanduser())
    project_root = Path(__file__).resolve().parent.parent
    candidates = (
        project_root.parent / "WEC_SPM" / "FPGA Bitfiles" / TARGET_BITFILE_NAME,
        project_root / "FPGA Bitfiles" / TARGET_BITFILE_NAME,
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return TARGET_BITFILE_NAME


DEFAULT_BITFILE = _default_bitfile()
def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


@dataclass(slots=True)
class AppSettings:
    mode: str = "Simulation"
    resource: str = "RIO0"
    bitfile: str = DEFAULT_BITFILE
    hardware_transport: str = "USB R Series"
    x_range_um: float = 100.0
    y_range_um: float = 100.0
    z_range_um: float = 100.0
    x_bipolar: bool = False
    y_bipolar: bool = False
    z_bipolar: bool = False
    command_voltage_ratio: float = 1.0
    current1_v_per_na: float = 1.0
    current2_v_per_na: float = 1.0
    sample_time_us: int = 4
    samples_per_point: int = 256
    hardware_ready_timeout_s: float = 5.0
    hardware_watchdog_margin_s: float = 30.0
    save_directory: str = "data"
    auto_save: bool = True
    display_max_points: int = 12_000
    feedback2_enabled: bool = False
    feedback2_channel: str = "Current 2"
    feedback2_threshold: float = 0.0
    feedback2_greater_than: bool = True
    feedback_p_gain: float = 0.0
    feedback_max_z_step_nm: int = 10
    feedback_update_interval_us: int = 2
    feedback_running_average_whole: int = 1
    feedback_running_average_minus: int = 0
    feedback_self_reference_on_hold: bool = False
    distance_to_bulk_um: float = 0.0
    distance_to_bulk2_um: float = 0.0
    distance_to_bulk3_um: float = 0.0

    @property
    def effective_period_s(self) -> float:
        return self.sample_time_us * (self.samples_per_point + 1) / 1_000_000.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not isinstance(self.mode, str) or self.mode not in {"Simulation", "NI FPGA"}:
            errors.append("Connection mode must be Simulation or NI FPGA.")
        if not isinstance(self.hardware_transport, str) or self.hardware_transport not in {"Auto", "USB R Series", "PCIe/PXI R Series"}:
            errors.append("Hardware transport must be Auto, USB R Series, or PCIe/PXI R Series.")
        for name, value in (
            ("X range", self.x_range_um),
            ("Y range", self.y_range_um),
            ("Z range", self.z_range_um),
        ):
            if not _finite_number(value) or value <= 0:
                errors.append(f"{name} must be positive.")
        if not isinstance(self.sample_time_us, int) or isinstance(self.sample_time_us, bool) or not 2 <= self.sample_time_us <= 1_000_000:
            errors.append("Sample time must be at least 2 us.")
        n = self.samples_per_point
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0 or n & (n - 1):
            errors.append("Samples per data point must be a power of two.")
        if not _finite_number(self.command_voltage_ratio) or not 0 < self.command_voltage_ratio <= 100:
            errors.append("Command voltage ratio must be between 0 and 100.")
        for name, value in (
            ("Current 1 sensitivity", self.current1_v_per_na),
            ("Current 2 sensitivity", self.current2_v_per_na),
        ):
            if not _finite_number(value) or value <= 0:
                errors.append(f"{name} must be positive.")
        if not _finite_number(self.hardware_ready_timeout_s) or not 0.5 <= self.hardware_ready_timeout_s <= 60:
            errors.append("FPGA ready timeout must be between 0.5 and 60 seconds.")
        if not _finite_number(self.hardware_watchdog_margin_s) or not 1 <= self.hardware_watchdog_margin_s <= 600:
            errors.append("FPGA watchdog margin must be between 1 and 600 seconds.")
        if not isinstance(self.display_max_points, int) or isinstance(self.display_max_points, bool) or not 500 <= self.display_max_points <= 100_000:
            errors.append("Display buffer must contain between 500 and 100,000 points.")
        feedback_channels = {"Current 1", "Current 2"}
        if self.feedback2_channel not in feedback_channels:
            errors.append("Secondary feedback signal is not supported.")
        if not _finite_number(self.feedback2_threshold):
            errors.append("Secondary feedback threshold must be finite.")
        if not _finite_number(self.feedback_p_gain):
            errors.append("Feedback proportional gain must be finite.")
        if not isinstance(self.feedback_max_z_step_nm, int) or isinstance(self.feedback_max_z_step_nm, bool) or self.feedback_max_z_step_nm < 0:
            errors.append("Maximum feedback Z movement must be a non-negative integer in nm.")
        if not isinstance(self.feedback_update_interval_us, int) or isinstance(self.feedback_update_interval_us, bool) or not 0 <= self.feedback_update_interval_us <= 32767:
            errors.append("Feedback update interval must be between 0 and 32767 us.")
        for name, value in (("Running-average whole window", self.feedback_running_average_whole),
                            ("Running-average subtraction window", self.feedback_running_average_minus)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"{name} must be a non-negative integer.")
        for name, value in (("Bulk distance 1", self.distance_to_bulk_um),
                            ("Bulk distance 2", self.distance_to_bulk2_um),
                            ("Bulk distance 3", self.distance_to_bulk3_um)):
            if not _finite_number(value) or abs(float(value)) > self.z_range_um:
                errors.append(f"{name} must be finite and within the configured Z span.")
        if self.mode == "NI FPGA" and (not isinstance(self.resource, str) or not self.resource.strip()):
            errors.append("NI FPGA resource must not be empty.")
        if self.mode == "NI FPGA" and (not isinstance(self.bitfile, str) or not self.bitfile.strip()):
            errors.append("NI FPGA bitfile must not be empty.")
        return errors

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppSettings":
        allowed = cls.__dataclass_fields__.keys()
        values = {key: value for key, value in raw.items() if key in allowed}
        return cls(**values)


@dataclass(slots=True)
class FeedbackConfiguration:
    primary_channel: str = "Current 1"
    primary_threshold: float = 2.0
    primary_greater_than: bool = True
    secondary_enabled: bool = False
    secondary_channel: str = "Current 2"
    secondary_threshold: float = 0.0
    secondary_greater_than: bool = True
    proportional_gain: float = 0.0
    max_z_step_nm: int = 10
    update_interval_us: int = 2
    running_average_whole: int = 1
    running_average_minus: int = 0
    self_reference_on_hold: bool = False
    distance_to_bulk_um: float = 0.0
    distance_to_bulk2_um: float = 0.0
    distance_to_bulk3_um: float = 0.0

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        *,
        primary_channel: str = "Current 1",
        primary_threshold: float = 2.0,
        primary_greater_than: bool = True,
    ) -> "FeedbackConfiguration":
        return cls(
            primary_channel, primary_threshold, primary_greater_than,
            settings.feedback2_enabled, settings.feedback2_channel, settings.feedback2_threshold,
            settings.feedback2_greater_than, settings.feedback_p_gain, settings.feedback_max_z_step_nm,
            settings.feedback_update_interval_us, settings.feedback_running_average_whole,
            settings.feedback_running_average_minus, settings.feedback_self_reference_on_hold,
            settings.distance_to_bulk_um, settings.distance_to_bulk2_um, settings.distance_to_bulk3_um,
        )


class SettingsStore:
    def __init__(self, path: Path | str = ".echemtips/settings.json") -> None:
        self.path = Path(path)

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                saved_bitfile = raw.get("bitfile")
                if isinstance(saved_bitfile, str) and Path(saved_bitfile).name in LEGACY_BITFILE_NAMES:
                    raw["bitfile"] = DEFAULT_BITFILE
                    raw["hardware_transport"] = "USB R Series"
            return AppSettings.from_dict(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        errors = settings.validate()
        if errors:
            raise ValueError("\n".join(errors))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")


@dataclass(slots=True)
class Sample:
    elapsed_s: float
    x_um: float
    y_um: float
    z_um: float
    voltage1_v: float
    voltage2_v: float
    current1_na: float
    current2_na: float
    feedback_type: int = 0
    line_number: int = 0
    scan_pixel: int = -1
    scan_row: int = -1
    scan_column: int = -1
    commanded_x_um: float = math.nan
    commanded_y_um: float = math.nan
    commanded_z_um: float = math.nan

    def as_row(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(slots=True)
class ApproachCVParameters:
    start_z_um: float = 10.0
    end_z_um: float = 90.0
    approach_rate_um_s: float = 3.0
    approach_voltage_v: float = 0.1
    feedback_channel: str = "Current 1"
    feedback_threshold_na: float = 2.0
    greater_than: bool = True
    cv_start_v: float = -0.2
    cv_vertex1_v: float = 0.6
    cv_vertex2_v: float = -0.4
    cv_scan_rate_v_s: float = 0.25
    cycles: int = 2
    retract_after: bool = True

    @property
    def feedback_unit(self) -> str:
        return "nA"

    def validate(self, settings: AppSettings) -> list[str]:
        errors: list[str] = []
        if not math.isfinite(self.start_z_um) or not 0 <= self.start_z_um <= settings.z_range_um:
            errors.append("Start Z is outside the configured Z range.")
        if not math.isfinite(self.end_z_um) or not 0 <= self.end_z_um <= settings.z_range_um:
            errors.append("End Z is outside the configured Z range.")
        if math.isfinite(self.start_z_um) and math.isfinite(self.end_z_um) and self.start_z_um == self.end_z_um:
            errors.append("Start Z and end Z must be different.")
        if not math.isfinite(self.approach_rate_um_s) or self.approach_rate_um_s <= 0:
            errors.append("Approach rate must be positive.")
        if self.feedback_channel not in {"Current 1", "Current 2"}:
            errors.append("Feedback signal is not supported.")
        if not math.isfinite(self.feedback_threshold_na):
            errors.append("Feedback threshold must be finite.")
        elif settings.mode == "NI FPGA" and self.feedback_channel.startswith("Current "):
            channel = int(self.feedback_channel.rsplit(" ", 1)[1])
            sensitivity = getattr(settings, f"current{channel}_v_per_na")
            if math.isfinite(sensitivity) and abs(self.feedback_threshold_na * sensitivity) > 10:
                errors.append("Feedback threshold exceeds the selected current input's +/-10 V ADC range.")
        if not math.isfinite(self.cv_scan_rate_v_s) or self.cv_scan_rate_v_s <= 0:
            errors.append("CV scan rate must be positive.")
        if not 1 <= self.cycles <= 10_000:
            errors.append("CV cycles must be between 1 and 10,000.")
        for name, value in (
            ("Approach voltage", self.approach_voltage_v),
            ("CV start", self.cv_start_v),
            ("CV vertex 1", self.cv_vertex1_v),
            ("CV vertex 2", self.cv_vertex2_v),
        ):
            if not math.isfinite(value) or not -10 <= value <= 10:
                errors.append(f"{name} must be between -10 V and +10 V.")
            elif settings.mode == "NI FPGA" and abs(value * settings.command_voltage_ratio) > 10:
                errors.append(f"{name} exceeds the AO3 range at the configured command ratio.")
        return errors


@dataclass(slots=True)
class CVParameters:
    start_v: float = -0.2
    vertex1_v: float = 0.6
    vertex2_v: float = -0.4
    scan_rate_v_s: float = 0.25
    cycles: int = 2
    jump_at_start: bool = True

    def validate(self, settings: AppSettings) -> list[str]:
        errors: list[str] = []
        if not math.isfinite(self.scan_rate_v_s) or self.scan_rate_v_s <= 0:
            errors.append("CV scan rate must be positive.")
        if not isinstance(self.cycles, int) or not 1 <= self.cycles <= 20_000:
            errors.append("CV cycles must be between 1 and 20,000.")
        for name, value in (("Start", self.start_v), ("Vertex 1", self.vertex1_v), ("Vertex 2", self.vertex2_v)):
            if not math.isfinite(value) or not -10 <= value <= 10:
                errors.append(f"CV {name.lower()} potential must be between -10 V and +10 V.")
            elif settings.mode == "NI FPGA" and abs(value * settings.command_voltage_ratio) > 10:
                errors.append(f"CV {name.lower()} exceeds the AO3 range at the configured command ratio.")
        if 1 + 3 * self.cycles > 65535:
            errors.append("CV program exceeds the 65,535-waypoint driver limit.")
        return errors


@dataclass(slots=True)
class ApproachParameters:
    start_z_um: float = 10.0
    end_z_um: float = 90.0
    approach_rate_um_s: float = 3.0
    retract_rate_um_s: float = 10.0
    approach_voltage_v: float = 0.1
    feedback_channel: str = "Current 1"
    feedback_threshold: float = 2.0
    greater_than: bool = True
    retract_after: bool = True
    x_um: float | None = None
    y_um: float | None = None

    @property
    def feedback_unit(self) -> str:
        return "nA"

    def validate(self, settings: AppSettings) -> list[str]:
        errors: list[str] = []
        for name, value in (("Start Z", self.start_z_um), ("End Z", self.end_z_um)):
            if not math.isfinite(value) or not 0 <= value <= settings.z_range_um:
                errors.append(f"{name} is outside the configured Z range.")
        if self.start_z_um == self.end_z_um:
            errors.append("Start Z and end Z must be different.")
        for name, value in (("Approach rate", self.approach_rate_um_s), ("Retract rate", self.retract_rate_um_s)):
            if not math.isfinite(value) or value <= 0:
                errors.append(f"{name} must be positive.")
        channels = {"Current 1", "Current 2"}
        if self.feedback_channel not in channels:
            errors.append("Feedback signal is not supported.")
        if not math.isfinite(self.feedback_threshold):
            errors.append("Feedback threshold must be finite.")
        if not math.isfinite(self.approach_voltage_v) or not -10 <= self.approach_voltage_v <= 10:
            errors.append("Approach potential must be between -10 V and +10 V.")
        elif settings.mode == "NI FPGA" and abs(self.approach_voltage_v * settings.command_voltage_ratio) > 10:
            errors.append("Approach potential exceeds the AO3 range at the configured command ratio.")
        for axis, value, limit in (("X", self.x_um, settings.x_range_um), ("Y", self.y_um, settings.y_range_um)):
            if value is not None and (not math.isfinite(value) or not 0 <= value <= limit):
                errors.append(f"{axis} position is outside the configured range.")
        if settings.mode == "NI FPGA" and self.feedback_channel.startswith("Current ") and math.isfinite(self.feedback_threshold):
            channel = int(self.feedback_channel.rsplit(" ", 1)[1])
            if abs(self.feedback_threshold * getattr(settings, f"current{channel}_v_per_na")) > 10:
                errors.append("Feedback threshold exceeds the selected current input's +/-10 V ADC range.")
        return errors


@dataclass(slots=True)
class ApproachITParameters(ApproachParameters):
    initial_potential_v: float = -0.1
    initial_hold_s: float = 0.25
    step_potential_v: float = 0.4
    step_hold_s: float = 1.0
    return_potential_v: float = -0.1
    return_hold_s: float = 0.25
    cycles: int = 1

    def it_steps(self) -> list[tuple[float, float, str]]:
        steps: list[tuple[float, float, str]] = []
        for _ in range(self.cycles):
            steps.extend((
                (self.initial_potential_v, self.initial_hold_s, "initial"),
                (self.step_potential_v, self.step_hold_s, "pulse"),
                (self.return_potential_v, self.return_hold_s, "return"),
            ))
        return steps

    def validate(self, settings: AppSettings) -> list[str]:
        errors = ApproachParameters.validate(self, settings)
        if not isinstance(self.cycles, int) or not 1 <= self.cycles <= 10_000:
            errors.append("IT cycles must be between 1 and 10,000.")
        for name, potential, duration in (
            ("Initial", self.initial_potential_v, self.initial_hold_s),
            ("Pulse", self.step_potential_v, self.step_hold_s),
            ("Return", self.return_potential_v, self.return_hold_s),
        ):
            if not math.isfinite(potential) or not -10 <= potential <= 10:
                errors.append(f"{name} IT potential must be between -10 V and +10 V.")
            elif settings.mode == "NI FPGA" and abs(potential * settings.command_voltage_ratio) > 10:
                errors.append(f"{name} IT potential exceeds the AO3 range at the configured command ratio.")
            if not math.isfinite(duration) or duration <= 0:
                errors.append(f"{name} IT hold must be positive.")
        if not errors and settings.mode == "NI FPGA":
            hold_frames = sum(
                max(1, math.ceil(duration * 1_000_000 / 32767))
                for _potential, duration, _label in self.it_steps()
            )
            total_tags = 2 + hold_frames + int(self.retract_after)
            if total_tags > 32767:
                errors.append(
                    f"This I-t method needs {total_tags} waypoint tags, beyond the verified signed-I16 acquisition tag range."
                )
        return errors


@dataclass(slots=True)
class ScanHoppingCVParameters:
    x_start_um: float = 35.0
    x_end_um: float = 65.0
    x_points: int = 3
    y_start_um: float = 35.0
    y_end_um: float = 65.0
    y_points: int = 3
    start_z_um: float = 55.0
    end_z_um: float = 80.0
    lateral_rate_um_s: float = 50.0
    approach_rate_um_s: float = 15.0
    retract_rate_um_s: float = 50.0
    approach_voltage_v: float = 0.1
    feedback_channel: str = "Current 1"
    feedback_threshold_na: float = 2.0
    greater_than: bool = True
    cv_start_v: float = -0.2
    cv_vertex1_v: float = 0.6
    cv_vertex2_v: float = -0.4
    cv_scan_rate_v_s: float = 2.0
    cycles: int = 1
    map_potential_v: float = 0.2
    serpentine: bool = True

    @property
    def point_count(self) -> int:
        return self.x_points * self.y_points

    @staticmethod
    def _axis_values(start: float, end: float, count: int) -> list[float]:
        if count == 1:
            return [start]
        return [start + index * (end - start) / (count - 1) for index in range(count)]

    def grid(self) -> list[tuple[int, int, float, float]]:
        xs = self._axis_values(self.x_start_um, self.x_end_um, self.x_points)
        ys = self._axis_values(self.y_start_um, self.y_end_um, self.y_points)
        points: list[tuple[int, int, float, float]] = []
        for row, y in enumerate(ys):
            columns = list(range(self.x_points))
            if self.serpentine and row % 2:
                columns.reverse()
            points.extend((row, column, xs[column], y) for column in columns)
        return points

    def validate(self, settings: AppSettings) -> list[str]:
        errors: list[str] = []
        for name, low, high, limit in (
            ("X", self.x_start_um, self.x_end_um, settings.x_range_um),
            ("Y", self.y_start_um, self.y_end_um, settings.y_range_um),
        ):
            if not all(math.isfinite(item) for item in (low, high, limit)) or not 0 <= low <= limit or not 0 <= high <= limit:
                errors.append(f"{name} scan bounds must be inside 0 to {limit:g} um.")
        if not 1 <= self.x_points <= 64 or not 1 <= self.y_points <= 64:
            errors.append("X and Y point counts must each be between 1 and 64.")
        if not all(math.isfinite(item) for item in (self.start_z_um, self.end_z_um)) or not 0 <= self.start_z_um <= settings.z_range_um or not 0 <= self.end_z_um <= settings.z_range_um:
            errors.append("Z approach bounds are outside the configured Z range.")
        if self.start_z_um == self.end_z_um:
            errors.append("Start Z and end Z must be different.")
        for name, value in (
            ("Lateral rate", self.lateral_rate_um_s),
            ("Approach rate", self.approach_rate_um_s),
            ("Retract rate", self.retract_rate_um_s),
            ("CV scan rate", self.cv_scan_rate_v_s),
        ):
            if not math.isfinite(value) or value <= 0:
                errors.append(f"{name} must be positive.")
        if not 1 <= self.cycles <= 100:
            errors.append("CV cycles must be between 1 and 100.")
        if self.feedback_channel not in {"Current 1", "Current 2"}:
            errors.append("Feedback signal must be Current 1 or Current 2.")
        if not math.isfinite(self.feedback_threshold_na):
            errors.append("Feedback threshold must be finite.")
        elif settings.mode == "NI FPGA" and self.feedback_channel in {"Current 1", "Current 2"}:
            channel = int(self.feedback_channel[-1])
            if abs(self.feedback_threshold_na * getattr(settings, f"current{channel}_v_per_na")) > 10:
                errors.append("Feedback threshold exceeds the selected current input's +/-10 V ADC range.")
        voltages = (self.approach_voltage_v, self.cv_start_v, self.cv_vertex1_v, self.cv_vertex2_v, self.map_potential_v)
        if any(not math.isfinite(value) or not -10 <= value <= 10 for value in voltages):
            errors.append("All potentials must be between -10 V and +10 V.")
        if settings.mode == "NI FPGA" and any(abs(value * settings.command_voltage_ratio) > 10 for value in voltages):
            errors.append("A potential exceeds the AO3 range at the configured command ratio.")
        if not min(self.cv_start_v, self.cv_vertex1_v, self.cv_vertex2_v) <= self.map_potential_v <= max(
            self.cv_start_v, self.cv_vertex1_v, self.cv_vertex2_v
        ):
            errors.append("Map potential must lie inside the CV potential range.")
        waypoints = 1 + self.point_count * (4 + 3 * self.cycles)
        if settings.mode == "NI FPGA" and waypoints > 32767:
            errors.append(
                f"This scan needs {waypoints} waypoint tags; the deployed FIFO streams them, but the "
                "sample line tag is only validated through signed I16 line 32767."
            )
        return errors


@dataclass(slots=True)
class ScanHoppingITParameters:
    x_start_um: float = 35.0
    x_end_um: float = 65.0
    x_points: int = 3
    y_start_um: float = 35.0
    y_end_um: float = 65.0
    y_points: int = 3
    start_z_um: float = 55.0
    end_z_um: float = 80.0
    lateral_rate_um_s: float = 50.0
    approach_rate_um_s: float = 15.0
    retract_rate_um_s: float = 50.0
    approach_voltage_v: float = 0.1
    feedback_channel: str = "Current 1"
    feedback_threshold: float = 2.0
    greater_than: bool = True
    initial_potential_v: float = -0.1
    initial_hold_s: float = 0.25
    step_potential_v: float = 0.4
    step_hold_s: float = 1.0
    return_potential_v: float = -0.1
    return_hold_s: float = 0.25
    cycles: int = 1
    serpentine: bool = True

    @property
    def point_count(self) -> int:
        return self.x_points * self.y_points

    def grid(self) -> list[tuple[int, int, float, float]]:
        xs = ScanHoppingCVParameters._axis_values(self.x_start_um, self.x_end_um, self.x_points)
        ys = ScanHoppingCVParameters._axis_values(self.y_start_um, self.y_end_um, self.y_points)
        points: list[tuple[int, int, float, float]] = []
        for row, y in enumerate(ys):
            columns = list(range(self.x_points))
            if self.serpentine and row % 2:
                columns.reverse()
            points.extend((row, column, xs[column], y) for column in columns)
        return points

    def it_steps(self) -> list[tuple[float, float, str]]:
        steps: list[tuple[float, float, str]] = []
        for _ in range(self.cycles):
            steps.extend((
                (self.initial_potential_v, self.initial_hold_s, "initial"),
                (self.step_potential_v, self.step_hold_s, "pulse"),
                (self.return_potential_v, self.return_hold_s, "return"),
            ))
        return steps

    def validate(self, settings: AppSettings) -> list[str]:
        approach = ApproachITParameters(
            start_z_um=self.start_z_um, end_z_um=self.end_z_um,
            approach_rate_um_s=self.approach_rate_um_s, retract_rate_um_s=self.retract_rate_um_s,
            approach_voltage_v=self.approach_voltage_v, feedback_channel=self.feedback_channel,
            feedback_threshold=self.feedback_threshold, greater_than=self.greater_than,
            initial_potential_v=self.initial_potential_v, initial_hold_s=self.initial_hold_s,
            step_potential_v=self.step_potential_v, step_hold_s=self.step_hold_s,
            return_potential_v=self.return_potential_v, return_hold_s=self.return_hold_s,
            cycles=self.cycles,
        )
        errors = approach.validate(settings)
        for name, low, high, limit in (
            ("X", self.x_start_um, self.x_end_um, settings.x_range_um),
            ("Y", self.y_start_um, self.y_end_um, settings.y_range_um),
        ):
            if not all(math.isfinite(item) for item in (low, high, limit)) or not 0 <= low <= limit or not 0 <= high <= limit:
                errors.append(f"{name} scan bounds must be inside 0 to {limit:g} um.")
        if not isinstance(self.x_points, int) or not isinstance(self.y_points, int) or not 1 <= self.x_points <= 64 or not 1 <= self.y_points <= 64:
            errors.append("X and Y point counts must each be between 1 and 64.")
        if not math.isfinite(self.lateral_rate_um_s) or self.lateral_rate_um_s <= 0:
            errors.append("Lateral rate must be positive.")
        hold_frames = sum(max(1, math.ceil(duration * 1_000_000 / 32767)) for _potential, duration, _label in self.it_steps())
        total_tags = 1 + self.point_count * (3 + hold_frames)
        if settings.mode == "NI FPGA" and total_tags > 32767:
            errors.append(f"This scan needs {total_tags} waypoint tags, beyond the conservative signed-I16 scan limit.")
        return errors
