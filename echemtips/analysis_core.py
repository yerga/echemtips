"""UI-independent recording loading and cyclic-voltammetry extraction."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .legacy_data import LegacyDataError, load_legacy
from .qt_common import COLORS


CURRENT_COLUMNS = {
    "Current 1": "current1_na",
    "Current 2": "current2_na",
}

RAW_SIGNALS = dict(CURRENT_COLUMNS)

PLOT_COLORS = (
    COLORS["accent"], COLORS["blue"], COLORS["danger"], COLORS["warning"],
    "#7857b8", "#c65f20", "#1686a5", "#b84683",
)


class AnalysisError(RuntimeError):
    """A saved recording cannot be read or interpreted."""


@dataclass(slots=True)
class AnalysisDataset:
    path: Path
    columns: tuple[str, ...]
    rows: list[dict[str, float]]
    metadata: dict[str, Any]

    @classmethod
    def load(cls, path: Path | str) -> "AnalysisDataset":
        source_path = Path(path).expanduser().resolve()
        if not source_path.exists():
            raise AnalysisError(f"Recording not found: {source_path}")
        if source_path.suffix.casefold() != ".csv":
            try:
                recording = load_legacy(source_path)
            except LegacyDataError as exc:
                raise AnalysisError(str(exc)) from exc
            return cls(recording.path, recording.columns, recording.rows, recording.metadata)
        try:
            with source_path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    raise AnalysisError("The CSV file has no header row.")
                columns = tuple(reader.fieldnames)
                required = {"elapsed_s", "voltage1_v", "current1_na"}
                missing = required.difference(columns)
                if missing:
                    raise AnalysisError(f"Missing required columns: {', '.join(sorted(missing))}")
                rows: list[dict[str, float]] = []
                for line_number, raw in enumerate(reader, 2):
                    try:
                        rows.append({name: float(raw[name]) for name in columns})
                    except (KeyError, TypeError, ValueError) as exc:
                        raise AnalysisError(f"Invalid numeric value on CSV line {line_number}.") from exc
        except OSError as exc:
            raise AnalysisError(f"Could not read {source_path.name}: {exc}") from exc
        if not rows:
            raise AnalysisError("The recording contains no samples.")

        metadata: dict[str, Any] = {}
        metadata_path = source_path.with_suffix(".json")
        if metadata_path.exists():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                metadata = {}
        return cls(source_path, columns, rows, metadata)

    @property
    def experiment(self) -> str:
        value = self.metadata.get("experiment")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "Approach then CV" if "approach_then_cv" in self.path.stem.lower() else "Watch Current"

    @property
    def duration_s(self) -> float:
        times = [row["elapsed_s"] for row in self.rows]
        return max(times) - min(times)

    def values(self, column: str) -> list[float]:
        return [row[column] for row in self.rows if column in row]


@dataclass(slots=True)
class CVCycle:
    number: int
    rows: list[dict[str, float]]
    pixel: int = -1

    @property
    def label(self) -> str:
        return f"P{self.pixel + 1} · C{self.number}" if self.pixel >= 0 else str(self.number)

    @property
    def potential_v(self) -> list[float]:
        return [row["voltage1_v"] for row in self.rows]

    def current_na(self, column: str) -> list[float]:
        return [row[column] for row in self.rows]

    def peak_summary(self, column: str) -> tuple[float, float, float, float]:
        currents, potentials = self.current_na(column), self.potential_v
        high_index = max(range(len(currents)), key=currents.__getitem__)
        low_index = min(range(len(currents)), key=currents.__getitem__)
        return currents[high_index], potentials[high_index], currents[low_index], potentials[low_index]


def _target_index(voltages: list[float], start: int, end: int, target: float, direction: int, noise_tolerance: float) -> int | None:
    """Find a target crossing, or the turning point if a real scan falls just short."""
    if direction == 0:
        return min(range(start, end + 1), key=lambda index: abs(voltages[index] - target))
    previous = voltages[start]
    if direction * (previous - target) >= 0:
        return start
    for index in range(start + 1, end + 1):
        value = voltages[index]
        if direction * (value - target) >= 0:
            return min((index - 1, index), key=lambda candidate: abs(voltages[candidate] - target))
        if direction * (value - previous) < -noise_tolerance:
            return index - 1
        previous = value
    return None


def extract_cv_cycles(dataset: AnalysisDataset) -> list[CVCycle]:
    """Extract completed CV cycles using the voltage program saved in metadata."""
    if "scan_pixel" in dataset.columns:
        pixel_ids = sorted({int(row["scan_pixel"]) for row in dataset.rows if row.get("scan_pixel", -1) >= 0})
        if pixel_ids:
            separated: list[CVCycle] = []
            for pixel in pixel_ids:
                pixel_rows = [row for row in dataset.rows if int(row.get("scan_pixel", -1)) == pixel]
                subset = AnalysisDataset(dataset.path, tuple(name for name in dataset.columns if name != "scan_pixel"), pixel_rows, dataset.metadata)
                for cycle in extract_cv_cycles(subset):
                    cycle.pixel = pixel
                    separated.append(cycle)
            return separated
    parameters = dataset.metadata.get("parameters")
    if not isinstance(parameters, dict):
        return []
    try:
        cv_start = float(parameters["cv_start_v"] if "cv_start_v" in parameters else parameters["start_v"])
        vertex1 = float(parameters["cv_vertex1_v"] if "cv_vertex1_v" in parameters else parameters["vertex1_v"])
        vertex2 = float(parameters["cv_vertex2_v"] if "cv_vertex2_v" in parameters else parameters["vertex2_v"])
        requested_cycles = int(parameters["cycles"])
        approach_voltage = float(parameters.get("approach_voltage_v", cv_start))
    except (KeyError, TypeError, ValueError):
        return []
    if requested_cycles < 1:
        return []
    voltages = dataset.values("voltage1_v")
    if len(voltages) < 4:
        return []
    span = max(voltages) - min(voltages)
    active_tolerance = max(1e-9, span * 1e-6)
    active = [index for index in range(1, len(voltages)) if abs(voltages[index] - voltages[index - 1]) > active_tolerance]
    if not active:
        return []
    departure_tolerance = max(0.01, abs(vertex1 - vertex2) * 0.03)
    start_index = active[0]
    for index in active:
        if abs(voltages[index - 1] - approach_voltage) <= departure_tolerance and abs(voltages[index] - approach_voltage) > departure_tolerance:
            start_index = index
            break
    end_index = active[-1]
    cycles: list[CVCycle] = []
    cursor = cycle_start = start_index
    for cycle_number in range(1, requested_cycles + 1):
        previous_target = cv_start
        cycle_end: int | None = None
        for target in (vertex1, vertex2, cv_start):
            direction = 1 if target > previous_target else -1 if target < previous_target else 0
            found = _target_index(voltages, cursor, end_index, target, direction, active_tolerance)
            if found is None:
                return cycles
            cycle_end = found
            cursor = min(found + 1, end_index)
            previous_target = target
        if cycle_end is None or cycle_end <= cycle_start:
            return cycles
        cycles.append(CVCycle(cycle_number, dataset.rows[cycle_start:cycle_end + 1]))
        cycle_start = cycle_end
    return cycles
