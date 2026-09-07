from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .models import AppSettings, Sample


FPGA_CLOCK_HZ = 40_000_000
FPGA_TICKS_PER_US = FPGA_CLOCK_HZ // 1_000_000
WAYPOINT_WORDS = 14
SAMPLE_WORDS = 14
HOST_TO_TARGET_FIFO = "Host_To_FPGA_Positions"
TARGET_TO_HOST_FIFO = "FPGA_To_Host_FIFO"
DEPLOYED_USB_TARGET_CLASS = "USB-7856R"
DEPLOYED_USB_SIGNATURE = "8229BC0D5A4935D854D1286878CEE54A"

# FPGA Target.vi writes these values directly to AO0-AO4 and their Applied
# indicators in its unconditional startup frame. External Pause is evaluated
# later and therefore cannot prevent these startup output changes.
DEPLOYED_STARTUP_RAW_OUTPUTS = {
    "Applied X": 0x3FFF,
    "Applied Y": 0x3FFF,
    "Applied Z": 0,
    "Applied Voltage": 0,
    "Applied Voltage 2": 0,
}

# Developer Guide tables 1/2 and ChangeOnFly.ctl. Keeping these mappings in
# one tested profile avoids scattering numeric hardware enums through drivers.
ANALOG_OUTPUT_CHANNELS = {"X": "AO0", "Y": "AO1", "Z": "AO2", "Voltage 1": "AO3", "Voltage 2": "AO4"}
ANALOG_INPUT_CHANNELS = {
    "X": "AI0", "Y": "AI1", "Z": "AI2", "Current 1": "AI3", "Current 2": "AI4",
}
FEEDBACK_SIGNAL_CODES = {"Current 1": 1, "Current 2": 2}
FEEDBACK_ACTION_CODES = {
    "none": 0,
    "pause_on_contact": 1,
    "advance_on_contact": 2,
    "relative_retract": 3,
    "proportional": 4,
    "trace_store": 5,
    "trace_replay": 6,
    "proportional_secondary_pause": 7,
    "pause_on_running_average": 8,
    "development": 9,
}

REQUIRED_REGISTERS = frozenset(
    {
        "External Stop",
        "External Pause",
        "Internal Pause",
        "Internal Stop",
        "Buffer Loop Wait Time (tICKS)",
        "HoldTimerScale",
        "2^(-n)",
        "ExpandVelScaller X",
        "ExpandVelScaller Y",
        "ExpandVelScaller Z",
        "ExpandVelScaller V",
        "ExpandVelScaller V2",
        "FeedBackType",
        "Feedback_Threshold",
        "GreaterThan",
        "Applied X",
        "Applied Y",
        "Applied Z",
        "Applied Voltage",
        "Applied Voltage 2",
        "MeasuredCurrent",
        "MeasuredCurrent 2",
        "MeasuredCurrent 3",
        "MeasuredCurrent 4",
        "Ext amp",
        "Ext Phase",
        "V on Fly",
        "V2 on Fly",
        "Change V on Fly",
        "Change V on Fly 2",
        "LineNumber",
        "WaitingForWayPoints",
        "EndCurrentLine",
        "LineType",
        "Feedback1 Boolean",
        "Feedback1 Error",
        "Z END",
        "StopMoveZ",
        "FeedBackType 2",
        "Feedback_Threshold 2",
        "GreaterThan 2",
        "P",
        "Upper limit Of dZ",
        "P2AvgWhole",
        "P2AvgMinus",
        "Feedback1 on  Hold",
        "DistanceToBulk",
        "DistanceToBulk 2",
        "DistanceToBulk 3",
    }
)

REGISTER_CONTRACT = {
    "External Stop": ("Boolean", False),
    "External Pause": ("Boolean", False),
    "Internal Pause": ("Boolean", False),
    "Internal Stop": ("Boolean", True),
    "Buffer Loop Wait Time (tICKS)": ("U32", False),
    "HoldTimerScale": ("U64", False),
    "2^(-n)": ("I16", False),
    "ExpandVelScaller X": ("I16", False),
    "ExpandVelScaller Y": ("I16", False),
    "ExpandVelScaller Z": ("I16", False),
    "ExpandVelScaller V": ("I16", False),
    "ExpandVelScaller V2": ("I16", False),
    "FeedBackType": ("U8", False),
    "Feedback_Threshold": ("I32", False),
    "GreaterThan": ("Boolean", False),
    "Applied X": ("I16", True),
    "Applied Y": ("I16", True),
    "Applied Z": ("I16", True),
    "Applied Voltage": ("I16", True),
    "Applied Voltage 2": ("I16", True),
    "MeasuredCurrent": ("I16", True),
    "MeasuredCurrent 2": ("I16", True),
    "MeasuredCurrent 3": ("I16", True),
    "MeasuredCurrent 4": ("I16", True),
    "Ext amp": ("I16", True),
    "Ext Phase": ("I16", True),
    "V on Fly": ("I16", False),
    "V2 on Fly": ("I16", False),
    "Change V on Fly": ("Boolean", False),
    "Change V on Fly 2": ("Boolean", False),
    "LineNumber": ("U64", True),
    "WaitingForWayPoints": ("Boolean", True),
    "EndCurrentLine": ("Boolean", False),
    "LineType": ("I16", True),
    "Feedback1 Boolean": ("Boolean", True),
    "Feedback1 Error": ("I32", True),
    "Z END": ("I16", True),
    "StopMoveZ": ("Boolean", True),
    "FeedBackType 2": ("U8", False),
    "Feedback_Threshold 2": ("I32", False),
    "GreaterThan 2": ("Boolean", False),
    "P": ("FXP", False),
    "Upper limit Of dZ": ("I32", False),
    "P2AvgWhole": ("I32", False),
    "P2AvgMinus": ("I32", False),
    "Feedback1 on  Hold": ("Boolean", False),
    "DistanceToBulk": ("I16", False),
    "DistanceToBulk 2": ("I16", False),
    "DistanceToBulk 3": ("I16", False),
}

FIFO_CONTRACT = {
    HOST_TO_TARGET_FIFO: ("I16", "HostToTarget", 8197),
    TARGET_TO_HOST_FIFO: ("I16", "TargetToHost", 32767),
}


@dataclass(frozen=True, slots=True)
class BitfileInfo:
    path: Path
    target_class: str
    signature: str
    registers: frozenset[str]
    fifos: frozenset[str]
    register_contract: dict[str, tuple[str, bool]]
    fifo_contract: dict[str, tuple[str, str, int]]

    @property
    def is_usb_target(self) -> bool:
        return "USB" in self.target_class.upper()


def inspect_bitfile(path: str | Path) -> BitfileInfo:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"FPGA bitfile not found: {resolved}")
    try:
        root = ET.parse(resolved).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Could not parse FPGA bitfile {resolved}: {exc}") from exc
    target = (root.findtext("./Project/TargetClass") or root.findtext(".//TargetClass") or "").strip()
    signature = (root.findtext("./SignatureRegister") or "").strip()
    registers = frozenset(
        name.strip()
        for node in root.findall("./VI/RegisterList/Register/Name")
        if (name := node.text)
    )
    register_contract: dict[str, tuple[str, bool]] = {}
    for node in root.findall("./VI/RegisterList/Register"):
        name = (node.findtext("Name") or "").strip()
        datatype = node.find("Datatype")
        if name and datatype is not None and len(datatype):
            register_contract[name] = (datatype[0].tag, (node.findtext("Indicator") or "").strip().lower() == "true")
    fifos = frozenset(
        node.attrib["name"].strip()
        for node in root.findall(".//DmaChannelAllocationList/Channel")
        if node.attrib.get("name")
    )
    fifo_contract: dict[str, tuple[str, str, int]] = {}
    for node in root.findall(".//DmaChannelAllocationList/Channel"):
        name = (node.attrib.get("name") or "").strip()
        if name:
            fifo_contract[name] = (
                (node.findtext("DataType/SubType") or "").strip(),
                (node.findtext("Direction") or "").strip(),
                int(node.findtext("NumberOfElements") or 0),
            )
    if not target or not signature:
        raise ValueError(f"{resolved} is not a complete NI FPGA .lvbitx file.")
    return BitfileInfo(resolved, target, signature, registers, fifos, register_contract, fifo_contract)


def validate_wec_bitfile(info: BitfileInfo, transport: str = "Auto") -> list[str]:
    errors: list[str] = []
    missing_registers = sorted(REQUIRED_REGISTERS - info.registers)
    missing_fifos = sorted({HOST_TO_TARGET_FIFO, TARGET_TO_HOST_FIFO} - info.fifos)
    if missing_registers:
        errors.append("missing registers: " + ", ".join(missing_registers))
    if missing_fifos:
        errors.append("missing FIFOs: " + ", ".join(missing_fifos))
    for name, expected in REGISTER_CONTRACT.items():
        actual = info.register_contract.get(name)
        if actual is not None and actual != expected:
            errors.append(
                f"register {name!r} has datatype/access {actual[0]}/"
                f"{'indicator' if actual[1] else 'control'}, expected {expected[0]}/"
                f"{'indicator' if expected[1] else 'control'}"
            )
    for name, expected in FIFO_CONTRACT.items():
        actual = info.fifo_contract.get(name)
        if actual is not None and actual != expected:
            errors.append(
                f"FIFO {name!r} has {actual[0]}/{actual[1]}/{actual[2]} elements, "
                f"expected {expected[0]}/{expected[1]}/{expected[2]}"
            )
    if transport == "USB R Series" and not info.is_usb_target:
        errors.append(
            f"target class is {info.target_class}, not a USB R Series target. "
            "Compile FPGA Target.vi for the connected USB model and select that .lvbitx file."
        )
    if transport == "PCIe/PXI R Series" and info.is_usb_target:
        errors.append(f"target class is {info.target_class}, but PCIe/PXI was selected")
    if info.is_usb_target and transport in {"Auto", "USB R Series"} and (
        info.target_class != DEPLOYED_USB_TARGET_CLASS or info.signature != DEPLOYED_USB_SIGNATURE
    ):
        errors.append(
            f"USB target identity is {info.target_class}/{info.signature}, expected "
            f"{DEPLOYED_USB_TARGET_CLASS}/{DEPLOYED_USB_SIGNATURE}"
        )
    return errors


def clamp_i16(value: float | int) -> int:
    return max(-32768, min(32767, int(round(value))))


def raw_to_adc_voltage(raw: int) -> float:
    # The LabVIEW host conversion uses a signed I16 with +/-10 V full scale.
    return max(-10.0, min(10.0, float(raw) * 10.0 / 32768.0))


def position_to_raw(position_um: float, span_um: float, bipolar: bool) -> int:
    if span_um <= 0:
        raise ValueError("Piezo span must be positive.")
    normalized = (position_um - span_um / 2.0) / (span_um / 2.0) if bipolar else position_um / span_um
    return clamp_i16(normalized * 32768.0)


def raw_to_position(raw: int, span_um: float, bipolar: bool) -> float:
    normalized = float(raw) / 32768.0
    return (normalized + 1.0) * span_um / 2.0 if bipolar else max(0.0, normalized * span_um)


def voltage1_to_raw(voltage_v: float, command_ratio: float) -> int:
    # WEC-SPM ScaleWayPoints passes (10 / command ratio) to V_to_I16Dim.
    return clamp_i16(voltage_v * command_ratio * 32768.0 / 10.0)


def raw_to_voltage1(raw: int, command_ratio: float) -> float:
    return raw_to_adc_voltage(raw) / command_ratio


def raw_to_current(raw: int, volts_per_na: float) -> float:
    return raw_to_adc_voltage(raw) / volts_per_na


def current_to_raw(current_na: float, volts_per_na: float) -> int:
    return clamp_i16(current_na * volts_per_na * 32768.0 / 10.0)


def raw_position_velocity_per_tick(speed_um_s: float, span_um: float, bipolar: bool) -> float:
    multiplier = 2.0 if bipolar else 1.0
    return abs(speed_um_s) * 32768.0 * multiplier / span_um / FPGA_CLOCK_HZ


def raw_voltage1_velocity_per_tick(speed_v_s: float, command_ratio: float) -> float:
    return abs(speed_v_s) * 32768.0 * command_ratio / 10.0 / FPGA_CLOCK_HZ


def select_velocity_exponent(raw_per_tick_values: list[float]) -> int:
    """Choose n for I16(raw/tick * 2**n), matching the FPGA 2**(-n) accumulator."""
    maximum = max((abs(value) for value in raw_per_tick_values), default=0.0)
    if maximum == 0:
        return 1
    if maximum > 32767:
        raise ValueError("Requested velocity exceeds the FPGA fixed-point range.")
    return max(0, min(30, int(math.floor(math.log2(32767.0 / maximum)))))


def scale_velocity(raw_per_tick: float, exponent: int) -> int:
    scaled = clamp_i16(abs(raw_per_tick) * (2**exponent))
    if raw_per_tick and not scaled:
        raise ValueError("Requested velocity is below the FPGA fixed-point resolution.")
    return scaled


@dataclass(slots=True)
class Waypoint:
    line_type: int = 0
    x_velocity: int = 0
    y_velocity: int = 0
    z_velocity: int = 0
    v_velocity: int = 0
    v2_velocity: int = 0
    x_position: int = 0
    y_position: int = 0
    z_position: int = 0
    v_position: int = 0
    v2_position: int = 0
    update_wait_us: int = 0
    hold_timer: int = 0
    move_x: bool = False
    move_y: bool = False
    move_z: bool = False
    move_v: bool = False
    jump_v: bool = False
    hold: bool = False
    move_v2: bool = False
    jump_v2: bool = False
    hold_feedback1: bool = False
    move_z_picomotor: bool = False
    z_picomotor_direction: bool = False

    def flags(self) -> int:
        values = (
            self.move_x,
            self.move_y,
            self.move_z,
            self.move_v,
            self.jump_v,
            self.hold,
            self.move_v2,
            self.jump_v2,
            self.hold_feedback1,
            self.move_z_picomotor,
            self.z_picomotor_direction,
        )
        return sum(1 << bit for bit, enabled in enumerate(values) if enabled)

    def words(self) -> list[int]:
        words = [
            self.line_type,
            self.x_velocity,
            self.y_velocity,
            self.z_velocity,
            self.v_velocity,
            self.v2_velocity,
            self.x_position,
            self.y_position,
            self.z_position,
            self.v_position,
            self.v2_position,
            self.update_wait_us,
            self.hold_timer,
            self.flags(),
        ]
        return [clamp_i16(value) for value in words]


class SampleDecoder:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._last_tick: int | None = None
        self._tick_epoch = 0
        self._first_tick: int | None = None

    @staticmethod
    def _timestamp_half(word: int) -> int:
        # convert_FIFO_data.vi adds 32768 to each signed transport word.
        return (int(word) + 32768) & 0xFFFF

    def _elapsed(self, high: int, low: int) -> float:
        tick = (self._timestamp_half(high) << 16) | self._timestamp_half(low)
        if self._last_tick is not None and tick < self._last_tick and self._last_tick - tick > 0x80000000:
            self._tick_epoch += 1 << 32
        self._last_tick = tick
        unwrapped = self._tick_epoch + tick
        if self._first_tick is None:
            self._first_tick = unwrapped
        return (unwrapped - self._first_tick) / FPGA_CLOCK_HZ

    def decode(self, words: list[int] | tuple[int, ...]) -> Sample:
        if len(words) != SAMPLE_WORDS:
            raise ValueError(f"FPGA sample requires {SAMPLE_WORDS} words; got {len(words)}.")
        s = self.settings
        return Sample(
            elapsed_s=self._elapsed(words[12], words[13]),
            x_um=raw_to_position(words[0], s.x_range_um, s.x_bipolar),
            y_um=raw_to_position(words[1], s.y_range_um, s.y_bipolar),
            z_um=raw_to_position(words[2], s.z_range_um, s.z_bipolar),
            voltage1_v=raw_to_voltage1(words[3], s.command_voltage_ratio),
            voltage2_v=raw_to_adc_voltage(words[4]),
            current1_na=raw_to_current(words[5], s.current1_v_per_na),
            current2_na=raw_to_current(words[6], s.current2_v_per_na),
            feedback_type=int(words[8]),
            line_number=int(words[9]),
        )
