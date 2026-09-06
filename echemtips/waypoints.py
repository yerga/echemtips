from __future__ import annotations

from dataclasses import dataclass
import math

from .models import AppSettings
from .ni_protocol import (
    FEEDBACK_ACTION_CODES,
    Waypoint,
    position_to_raw,
    raw_position_velocity_per_tick,
    raw_to_position,
    raw_to_voltage1,
    raw_voltage1_velocity_per_tick,
    scale_velocity,
    select_velocity_exponent,
    voltage1_to_raw,
)


@dataclass(slots=True)
class PhysicalWaypoint:
    """One device-independent waypoint expressed in operator units."""

    x_um: float | None = None
    y_um: float | None = None
    z_um: float | None = None
    voltage1_v: float | None = None
    voltage2_v: float | None = None
    x_rate_um_s: float | None = None
    y_rate_um_s: float | None = None
    z_rate_um_s: float | None = None
    voltage1_rate_v_s: float | None = None
    voltage2_rate_v_s: float | None = None
    relative_z_um: float | None = None
    feedback_action: str = "none"
    jump_voltage1: bool = False
    jump_voltage2: bool = False
    hold: bool = False
    hold_us: int = 0
    update_interval_us: int = 0
    hold_feedback1: bool = False


@dataclass(frozen=True, slots=True)
class CompiledWaypoints:
    waypoints: list[Waypoint]
    scaler_exponents: dict[str, int]
    expected_duration_s: float | None


def cyclic_voltammetry_plan(
    *,
    start_v: float,
    vertex1_v: float,
    vertex2_v: float,
    scan_rate_v_s: float,
    cycles: int,
    jump_at_start: bool = True,
    retract_z_um: float | None = None,
    retract_rate_um_s: float | None = None,
) -> list[PhysicalWaypoint]:
    """Build the shared jump/ramp CV program used by all FPGA methods."""
    if not isinstance(cycles, int) or cycles < 1:
        raise ValueError("CV cycles must be a positive integer.")
    plan = [PhysicalWaypoint(
        voltage1_v=start_v,
        voltage1_rate_v_s=None if jump_at_start else scan_rate_v_s,
        jump_voltage1=jump_at_start,
    )]
    for _ in range(cycles):
        plan.extend(
            PhysicalWaypoint(voltage1_v=voltage, voltage1_rate_v_s=scan_rate_v_s)
            for voltage in (vertex1_v, vertex2_v, start_v)
        )
    if retract_z_um is not None:
        if retract_rate_um_s is None:
            raise ValueError("A Z retract target requires a retract rate.")
        plan.append(PhysicalWaypoint(z_um=retract_z_um, z_rate_um_s=retract_rate_um_s))
    elif retract_rate_um_s is not None:
        raise ValueError("A retract rate cannot be supplied without a Z retract target.")
    return plan


def potential_step_plan(
    steps: list[tuple[float, float, str]],
) -> tuple[list[PhysicalWaypoint], list[str]]:
    """Compile potential holds into representable FPGA timer chunks.

    The deployed hold timer is signed I16 microseconds. Longer holds are split
    into adjacent identical jump-and-hold waypoints without losing duration.
    Labels retain the logical segment identity for I-t analysis.
    """
    plan: list[PhysicalWaypoint] = []
    labels: list[str] = []
    for potential, duration_s, label in steps:
        if not math.isfinite(potential) or not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("Potential-step values must be finite and hold durations positive.")
        remaining_us = max(1, round(duration_s * 1_000_000))
        while remaining_us:
            chunk_us = min(32767, remaining_us)
            plan.append(PhysicalWaypoint(
                voltage1_v=potential,
                jump_voltage1=True,
                hold=True,
                hold_us=chunk_us,
            ))
            labels.append(label)
            remaining_us -= chunk_us
    return plan, labels


def timed_hold_plan(duration_s: float) -> list[PhysicalWaypoint]:
    """Return exact FPGA-timed hold chunks; zero seconds intentionally emits nothing."""
    if not math.isfinite(duration_s) or duration_s < 0:
        raise ValueError("Hold duration must be finite and zero or greater.")
    remaining_us = round(duration_s * 1_000_000)
    plan: list[PhysicalWaypoint] = []
    while remaining_us > 0:
        chunk_us = min(32767, remaining_us)
        plan.append(PhysicalWaypoint(hold=True, hold_us=chunk_us))
        remaining_us -= chunk_us
    return plan


class WaypointCompiler:
    """Shared physical-unit equivalent of ScaleWayPoints/FindVelScaleFactor."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def compile(self, plan: list[PhysicalWaypoint], current_raw: dict[str, int]) -> CompiledWaypoints:
        if not plan:
            raise ValueError("A waypoint program cannot be empty.")
        required = {"X", "Y", "Z", "V", "V2"}
        if required - current_raw.keys():
            raise ValueError("Current raw X/Y/Z/V/V2 outputs are required.")
        s = self.settings
        raw_rates: dict[str, list[float]] = {name: [] for name in required}
        resolved: list[dict[str, float | int | bool | str]] = []
        positions = {
            "X": raw_to_position(current_raw["X"], s.x_range_um, s.x_bipolar),
            "Y": raw_to_position(current_raw["Y"], s.y_range_um, s.y_bipolar),
            "Z": raw_to_position(current_raw["Z"], s.z_range_um, s.z_bipolar),
            "V": raw_to_voltage1(current_raw["V"], s.command_voltage_ratio),
            "V2": current_raw["V2"] * 10.0 / 32768.0,
        }
        total_duration = 0.0
        indefinite_hold = False
        for item in plan:
            if item.feedback_action not in FEEDBACK_ACTION_CODES:
                raise ValueError(f"Unknown FPGA feedback action: {item.feedback_action}")
            if item.relative_z_um is not None and item.z_um is not None:
                raise ValueError("Specify either absolute Z or relative Z, not both.")
            targets = dict(positions)
            supplied = {
                "X": item.x_um, "Y": item.y_um, "Z": item.z_um,
                "V": item.voltage1_v, "V2": item.voltage2_v,
            }
            if item.relative_z_um is not None:
                supplied["Z"] = positions["Z"] + item.relative_z_um
            rates = {
                "X": item.x_rate_um_s, "Y": item.y_rate_um_s, "Z": item.z_rate_um_s,
                "V": item.voltage1_rate_v_s, "V2": item.voltage2_rate_v_s,
            }
            spans = {"X": s.x_range_um, "Y": s.y_range_um, "Z": s.z_range_um}
            bipolar = {"X": s.x_bipolar, "Y": s.y_bipolar, "Z": s.z_bipolar}
            move: dict[str, bool] = {}
            raw_rate: dict[str, float] = {}
            duration = 0.0
            for axis in required:
                value = supplied[axis]
                move[axis] = value is not None
                if value is None:
                    raw_rate[axis] = 0.0
                    continue
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError(f"{axis} target must be finite.")
                lower, upper = ((-10.0, 10.0) if axis in {"V", "V2"} else (0.0, spans[axis]))
                if not lower <= value <= upper:
                    raise ValueError(f"{axis} target is outside {lower:g} to {upper:g}.")
                if axis == "V" and abs(value * s.command_voltage_ratio) > 10:
                    raise ValueError("Voltage 1 exceeds AO3 after applying its command ratio.")
                rate = rates[axis]
                is_jump = (axis == "V" and item.jump_voltage1) or (axis == "V2" and item.jump_voltage2)
                if is_jump:
                    raw_rate[axis] = 0.0
                else:
                    if rate is None or not math.isfinite(rate) or rate <= 0:
                        raise ValueError(f"{axis} movement requires a finite positive rate.")
                    raw_rate[axis] = (
                        raw_voltage1_velocity_per_tick(rate, s.command_voltage_ratio if axis == "V" else 1.0)
                        if axis in {"V", "V2"}
                        else raw_position_velocity_per_tick(rate, spans[axis], bipolar[axis])
                    )
                    duration = max(duration, abs(value - positions[axis]) / rate)
                targets[axis] = value
                raw_rates[axis].append(raw_rate[axis])
            if not isinstance(item.hold_us, int) or not 0 <= item.hold_us <= 32767:
                raise ValueError("Waypoint hold must be between 0 and 32767 us.")
            if not isinstance(item.update_interval_us, int) or not 0 <= item.update_interval_us <= 32767:
                raise ValueError("Waypoint update interval must be between 0 and 32767 us.")
            if item.hold and item.hold_us:
                duration = max(duration, item.hold_us / 1_000_000.0)
            elif item.hold:
                indefinite_hold = True
            total_duration += duration
            resolved.append({"item": item, "targets": targets, "move": move, "raw_rate": raw_rate})
            positions = targets

        exponents = {axis: select_velocity_exponent(raw_rates[axis]) for axis in ("X", "Y", "Z", "V", "V2")}
        output: list[Waypoint] = []
        for row in resolved:
            item = row["item"]
            targets = row["targets"]
            move = row["move"]
            raw_rate = row["raw_rate"]
            assert isinstance(item, PhysicalWaypoint)
            assert isinstance(targets, dict) and isinstance(move, dict) and isinstance(raw_rate, dict)
            output.append(Waypoint(
                line_type=FEEDBACK_ACTION_CODES[item.feedback_action],
                x_velocity=scale_velocity(raw_rate["X"], exponents["X"]),
                y_velocity=scale_velocity(raw_rate["Y"], exponents["Y"]),
                z_velocity=scale_velocity(raw_rate["Z"], exponents["Z"]),
                v_velocity=scale_velocity(raw_rate["V"], exponents["V"]),
                v2_velocity=scale_velocity(raw_rate["V2"], exponents["V2"]),
                x_position=position_to_raw(targets["X"], s.x_range_um, s.x_bipolar),
                y_position=position_to_raw(targets["Y"], s.y_range_um, s.y_bipolar),
                z_position=position_to_raw(targets["Z"], s.z_range_um, s.z_bipolar),
                v_position=voltage1_to_raw(targets["V"], s.command_voltage_ratio),
                v2_position=voltage1_to_raw(targets["V2"], 1.0),
                update_wait_us=item.update_interval_us,
                hold_timer=item.hold_us,
                move_x=move["X"], move_y=move["Y"], move_z=move["Z"],
                move_v=move["V"], move_v2=move["V2"],
                jump_v=item.jump_voltage1, jump_v2=item.jump_voltage2,
                hold=item.hold, hold_feedback1=item.hold_feedback1,
            ))
        return CompiledWaypoints(output, exponents, None if indefinite_hold else total_duration)
