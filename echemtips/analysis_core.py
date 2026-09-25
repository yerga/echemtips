"""UI-independent recording loading and cyclic-voltammetry extraction."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
import numpy as np

from .legacy_data import LegacyDataError, load_legacy


CURRENT_COLUMNS = {
    "Current 1": "current1_na",
    "Current 2": "current2_na",
}

RAW_SIGNALS = dict(CURRENT_COLUMNS)

PLOT_COLORS = (
    "#008b83", "#287ac2", "#d83b59", "#bc7627",
    "#7857b8", "#c65f20", "#1686a5", "#b84683",
)


class AnalysisError(RuntimeError):
    """A saved recording cannot be read or interpreted."""


class NumericRows(Sequence):
    """Read-only, compact numeric storage with a compatibility row mapping API.

    Slices share the underlying array. Dictionaries are created only for rows
    actually accessed, avoiding millions of persistent Python row objects.
    """

    def __init__(self, columns, matrix):
        self.columns = tuple(columns)
        self.matrix = np.asarray(matrix, dtype=float).reshape(-1, len(columns))
        self.matrix.flags.writeable = False

    def __len__(self):
        return len(self.matrix)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return NumericRows(self.columns, self.matrix[index])
        return dict(zip(self.columns, self.matrix[index]))


@dataclass(slots=True)
class AnalysisDataset:
    """Normalized numeric recording plus metadata used by the analysis UI."""
    path: Path
    columns: tuple[str, ...]
    rows: Sequence
    metadata: dict[str, Any]

    def __post_init__(self):
        if not isinstance(self.rows, NumericRows):
            self.rows = NumericRows(self.columns, [[row.get(c, float("nan")) for c in self.columns]
                                                   for row in self.rows])

        # -2 is reserved for orientation-marker landings, even without a sidecar.
        # Filter before segmentation, smoothing, summary statistics or plots.
        if 'scan_pixel' in self.columns:
            marker = self.rows.matrix[:, self.columns.index('scan_pixel')] == -2
            if marker.any():
                self.metadata = dict(self.metadata)
                self.metadata['orientation_marker_samples_excluded'] = int(marker.sum())
                self.rows = NumericRows(self.columns, self.rows.matrix[~marker])

    def column(self, name: str) -> np.ndarray:
        """Return a read-only full-resolution column; no display downsampling."""
        return self.rows.matrix[:, self.columns.index(name)]

    @classmethod
    def load(cls, path: Path | str) -> "AnalysisDataset":
        """Load eChemTips CSV/JSON or normalize one supported legacy file."""
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
            with source_path.open(newline="", encoding="utf-8-sig") as stream:
                header = next(csv.reader(stream), None)
                if not header:
                    raise AnalysisError("The CSV file has no header row.")
                columns = tuple(header)
                if len(set(columns)) != len(columns) or any(not name for name in columns):
                    raise AnalysisError("CSV column names must be nonempty and unique.")
                required = {"elapsed_s"}
                missing = required.difference(columns)
                if missing:
                    raise AnalysisError(f"Missing required columns: {', '.join(sorted(missing))}")
                try:
                    numbers = np.loadtxt(stream, delimiter=",", ndmin=2,
                                         comments=None, quotechar='"')
                except ValueError as exc:
                    raise AnalysisError(f"Invalid numeric CSV data: {exc}") from exc
                if numbers.size and numbers.shape[1] != len(columns):
                    raise AnalysisError("CSV row width does not match the header.")
        except OSError as exc:
            raise AnalysisError(f"Could not read {source_path.name}: {exc}") from exc
        rows = NumericRows(columns, numbers)
        if not len(rows):
            raise AnalysisError("The recording contains no samples.")
        if not np.isfinite(rows.matrix[:, columns.index("elapsed_s")]).all():
            raise AnalysisError("Elapsed timestamps must be finite.")

        metadata: dict[str, Any] = {}
        metadata_path = source_path.with_suffix(".json")
        if metadata_path.exists():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                metadata = {"analysis_warnings": [f"Metadata could not be read: {exc}"]}
        return cls(source_path, columns, rows, metadata)

    @property
    def experiment(self) -> str:
        """Return the metadata experiment name with a conservative legacy fallback."""
        value = self.metadata.get("experiment")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "Approach then CV" if "approach_then_cv" in self.path.stem.lower() else "Unspecified experiment"

    @property
    def duration_s(self) -> float:
        """Return the measured time span of the dataset in seconds."""
        times = self.column("elapsed_s")
        return float(np.ptp(times)) if len(times) else 0.0

    def values(self, column: str) -> list[float]:
        """Return values for a column from rows that contain it."""
        return self.column(column).tolist() if column in self.columns else []


@dataclass(slots=True)
class CVCycle:
    """One complete extracted voltammogram, optionally associated with a pixel."""
    number: int
    rows: list[dict[str, float]]
    pixel: int = -1
    scan_rate_v_s: float | None = None
    rate_index: int = -1

    @property
    def label(self) -> str:
        """Return a concise cycle label for selectors and exports."""
        if self.scan_rate_v_s is not None:
            return f"Rate {self.rate_index + 1} · {self.scan_rate_v_s:g} V/s · C{self.number}"
        return f"P{self.pixel + 1} · C{self.number}" if self.pixel >= 0 else str(self.number)

    @property
    def potential_v(self) -> list[float]:
        """Return E1 samples in volts for this cycle."""
        return [row["voltage1_v"] for row in self.rows]

    def current_na(self, column: str) -> list[float]:
        """Return the requested current channel in nanoamperes."""
        return [row[column] for row in self.rows]

    def peak_summary(self, column: str) -> tuple[float, float, float, float]:
        """Return maximum/minimum current and their corresponding potentials."""
        currents, potentials = self.current_na(column), self.potential_v
        high_index = max(range(len(currents)), key=currents.__getitem__)
        low_index = min(range(len(currents)), key=currents.__getitem__)
        return currents[high_index], potentials[high_index], currents[low_index], potentials[low_index]


def _target_index(
    voltages: list[float],
    start: int,
    end: int,
    target: float,
    direction: int,
    noise_tolerance: float,
    target_tolerance: float,
) -> int | None:
    """Find a commanded target crossing or a turning point close to it."""
    if direction == 0:
        # A zero-length leg ends here; searching ahead could steal a target
        # sample from a later cycle (especially with quantized potentials).
        return start if abs(voltages[start] - target) <= target_tolerance else None
    previous = voltages[start]
    if direction * (previous - target) >= 0:
        return start
    for index in range(start + 1, end + 1):
        value = voltages[index]
        if direction * (value - target) >= 0:
            return min((index - 1, index), key=lambda candidate: abs(voltages[candidate] - target))
        if direction * (value - previous) < -noise_tolerance:
            return index - 1 if abs(previous - target) <= target_tolerance else None
        previous = value
    return end if abs(voltages[end] - target) <= target_tolerance else None


def _cv_start_index(
    voltages: list[float], cv_start: float, vertex1: float,
    end: int, noise_tolerance: float, target_tolerance: float,
) -> int | None:
    """Find a start sample whose outgoing sweep reaches the first vertex.

    A hop may begin at the previous CV's end potential before switching to
    approach potential. That transition is not the next CV's first sweep.
    """
    direction = 1 if vertex1 > cv_start else -1 if vertex1 < cv_start else 0
    for index in range(end + 1):
        if index and voltages[index] == voltages[index - 1]:
            continue  # Evaluate a plateau once, not once for every held sample.
        if abs(voltages[index] - cv_start) > target_tolerance:
            continue
        for following in range(index + 1, end + 1):
            change = voltages[following] - voltages[index]
            if abs(change) <= noise_tolerance:
                continue
            if direction == 0 or direction * change > 0:
                if _target_index(voltages, index, end, vertex1, direction,
                                 noise_tolerance, target_tolerance) is not None:
                    return index
            break
    return None


def pixel_groups(dataset: AnalysisDataset) -> list[tuple[int, NumericRows]]:
    """Group valid hop tags using zero-copy slices for contiguous acquisitions.

    Repeated disjoint tags are combined in acquisition order. Invalid tags are
    excluded, not silently assigned to another hop.
    """
    tags = dataset.column("scan_pixel")
    boundaries = np.r_[0, np.flatnonzero(tags[1:] != tags[:-1]) + 1, len(tags)]
    groups = {}
    adaptive_invalid = set()
    grid = dataset.metadata.get('scan_grid') or {}
    if grid.get('path') == 'adaptive':
        adaptive_invalid = {int(p['scan_pixel']) for p in grid.get('pixels',[]) if not p.get('valid',False)}
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if start == end:
            continue
        pixel = tags[start]
        if np.isfinite(pixel) and pixel >= 0 and pixel == int(pixel) and int(pixel) not in adaptive_invalid:
            groups.setdefault(int(pixel), []).append(dataset.rows.matrix[start:end])
    return [(pixel, NumericRows(dataset.columns, parts[0] if len(parts) == 1
                               else np.concatenate(parts)))
            for pixel, parts in sorted(groups.items())]


def _legacy_series_end(dataset: AnalysisDataset, end: int, index: int,
                       cv_start: float, tolerance: float) -> int:
    """Recover a delayed simulator endpoint without inventing or retagging data.

    Old GUI-assigned tags can put the return endpoint in the next rate block.
    Follow only a contiguous monotonic return; never bridge an absent block.
    """
    voltages = dataset.column("voltage1_v")
    tags = dataset.column("cv_rate_index")
    direction = np.sign(cv_start - voltages[end - 1])
    for cursor in range(end, len(voltages)):
        if tags[cursor] not in (index + 1, -1):
            break
        previous, value = voltages[cursor - 1], voltages[cursor]
        if direction * (value - previous) < -1e-9:
            break
        if abs(value - cv_start) <= tolerance:
            return cursor + 1
        if direction * (value - cv_start) > 0:
            break
    return end


def extract_cv_cycles(dataset: AnalysisDataset) -> list[CVCycle]:
    """Extract completed CV cycles using the voltage program saved in metadata."""
    if "voltage1_v" not in dataset.columns:
        return []
    parameters = dataset.metadata.get("parameters")
    rates = parameters.get("scan_rates_v_s") if isinstance(parameters, dict) else None
    if isinstance(rates, list) and "cv_rate_index" in dataset.columns:
        cycles = []
        tags = dataset.column("cv_rate_index")
        settings = dataset.metadata.get("settings")
        legacy_simulation = (isinstance(settings, dict)
                             and settings.get("mode") == "Simulation"
                             and not dataset.metadata.get("acquisition_rate_tags"))
        for index, rate in enumerate(rates):
            selected = np.flatnonzero(tags == index)
            if not len(selected):
                continue
            end = int(selected[-1]) + 1
            # Compatibility for older simulator recordings, never hardware tags.
            if (legacy_simulation
                    and selected[-1] - selected[0] + 1 == len(selected)):
                start_v = float(parameters["cv_start_v"])
                tolerance = max(.005, abs(float(parameters["cv_vertex1_v"]) - float(parameters["cv_vertex2_v"])) * .03)
                end = _legacy_series_end(dataset, end, index, start_v, tolerance)
            rows = (dataset.rows[int(selected[0]):end]
                    if selected[-1] - selected[0] + 1 == len(selected)
                    else NumericRows(dataset.columns, dataset.rows.matrix[selected]))
            subset = AnalysisDataset(dataset.path, dataset.columns, rows, dataset.metadata)
            for cycle in _extract_single_cv_cycles(subset):
                cycle.rate_index, cycle.scan_rate_v_s = index, float(rate)
                cycles.append(cycle)
        return cycles
    if "scan_pixel" in dataset.columns:
        grouped = pixel_groups(dataset)
        if grouped:
            separated: list[CVCycle] = []
            for pixel, pixel_rows in grouped:
                subset = AnalysisDataset(dataset.path, dataset.columns, pixel_rows, dataset.metadata)
                for cycle in _extract_single_cv_cycles(subset):
                    cycle.pixel = pixel
                    separated.append(cycle)
            return separated
    return _extract_single_cv_cycles(dataset)


def _extract_single_cv_cycles(dataset: AnalysisDataset) -> list[CVCycle]:
    parameters = dataset.metadata.get("parameters")
    if not isinstance(parameters, dict):
        return []
    try:
        cv_start = float(parameters["cv_start_v"] if "cv_start_v" in parameters else parameters["start_v"])
        vertex1 = float(parameters["cv_vertex1_v"] if "cv_vertex1_v" in parameters else parameters["vertex1_v"])
        vertex2 = float(parameters["cv_vertex2_v"] if "cv_vertex2_v" in parameters else parameters["vertex2_v"])
        requested_cycles = int(parameters["cycles"])
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
    target_tolerance = max(0.005, abs(vertex1 - vertex2) * 0.03)
    end_index = active[-1]
    start_index = _cv_start_index(
        voltages, cv_start, vertex1, end_index, active_tolerance, target_tolerance,
    )
    if start_index is None:
        return []
    if parameters.get("waveform", "CV") == "LSV":
        direction = 1 if vertex1 > cv_start else -1
        endpoint = _target_index(voltages, start_index, end_index, vertex1,
                                 direction, active_tolerance, target_tolerance)
        if endpoint is None or endpoint <= start_index:
            return []
        return [CVCycle(1, dataset.rows[start_index:endpoint + 1])]
    cycles: list[CVCycle] = []
    cursor = cycle_start = start_index
    for cycle_number in range(1, requested_cycles + 1):
        previous_target = cv_start
        cycle_end: int | None = None
        for target in (vertex1, vertex2, cv_start):
            direction = 1 if target > previous_target else -1 if target < previous_target else 0
            found = _target_index(
                voltages, cycle_end if direction == 0 and cycle_end is not None else cursor,
                end_index, target, direction,
                active_tolerance, target_tolerance,
            )
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
