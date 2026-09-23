"""Extensible, Qt-free selection and measurement services for saved recordings.

All calculations use original samples. Display reduction is a separate concern;
the registry accepts new experiment selectors without changing the loader.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import numpy as np

from .analysis_core import AnalysisDataset, AnalysisError, NumericRows, extract_cv_cycles


@dataclass(frozen=True)
class Selection:
    """Named full-resolution subset with its explicit scientific scope."""
    label: str
    rows: NumericRows
    scope: str
    pixel: int = -1


@dataclass(frozen=True)
class AnalysisProvider:
    """Extension contract: stable key, label, applicability and subset extraction."""
    key: str
    title: str
    supports: Callable[[AnalysisDataset], bool]
    extract: Callable[[AnalysisDataset], list[Selection]]


PROVIDERS: dict[str, AnalysisProvider] = {}


def register_provider(provider: AnalysisProvider) -> None:
    """Register a selector explicitly; reject accidental replacement of a key."""
    if provider.key in PROVIDERS:
        raise ValueError(f"Analysis provider already registered: {provider.key}")
    PROVIDERS[provider.key] = provider


def hop_selections(dataset: AnalysisDataset) -> list[Selection]:
    """Group acquisition-order samples by recorded hop, including every phase."""
    groups = {}
    for index, pixel in enumerate(dataset.column("scan_pixel")):
        if np.isfinite(pixel) and pixel >= 0 and pixel == int(pixel):
            groups.setdefault(int(pixel), []).append(index)
    return [Selection(f"Hop {pixel + 1}", NumericRows(dataset.columns, dataset.rows.matrix[indices]),
                      "Whole hop: approach, electrochemistry and retraction where recorded", pixel)
            for pixel, indices in sorted(groups.items())]


def cv_selections(dataset: AnalysisDataset, cycles=None) -> list[Selection]:
    """Expose only complete, waveform-validated CV cycles as analysis subsets."""
    result = []
    for cycle in extract_cv_cycles(dataset) if cycles is None else cycles:
        rows = cycle.rows
        if not isinstance(rows, NumericRows):
            columns = tuple(rows[0])
            rows = NumericRows(columns, [[row[c] for c in columns] for row in rows])
        result.append(Selection(f"CV {cycle.label}", rows, "Complete CV cycle", cycle.pixel))
    return result


register_provider(AnalysisProvider("recording", "Whole recording", lambda d: True,
    lambda d: [Selection("Whole recording", d.rows, "All recorded phases")]))
register_provider(AnalysisProvider("hops", "Recorded hops", lambda d: "scan_pixel" in d.columns, hop_selections))
register_provider(AnalysisProvider("cv", "Complete CV cycles", lambda d: "voltage1_v" in d.columns, cv_selections))


SIGNALS = {
    "elapsed_s": ("Elapsed time", "s"),
    "voltage1_v": ("Potential E1", "V"), "voltage2_v": ("Potential E2", "V"),
    "current1_na": ("Current 1", "nA"), "current2_na": ("Current 2", "nA"),
    "x_um": ("Measured X", "µm"), "y_um": ("Measured Y", "µm"), "z_um": ("Measured Z", "µm"),
}


def signal_label(column: str) -> str:
    """Label known physical channels while exposing unknown future columns intact."""
    name, unit = SIGNALS.get(column, (column, ""))
    return f"{name} ({unit})" if unit else name


def measure(selection: Selection, x_column: str, y_column: str,
            time_bounds: tuple[float, float] | None = None,
            baseline: float = 0.0) -> tuple[np.ndarray, np.ndarray, dict]:
    """Measure a time-selected trace; integrate current in time, never potential.

    Signed trapezoidal charge is reported in nC for nA channels. Invalid samples
    break integration rather than joining across gaps. Time reversals are rejected.
    Baseline is a user-entered constant in the Y channel's native unit.
    """
    columns, matrix = selection.rows.columns, selection.rows.matrix
    time = matrix[:, columns.index("elapsed_s")]
    if not np.isfinite(time).all() or np.any(np.diff(time) < 0):
        raise AnalysisError("Analysis requires finite, nondecreasing timestamps within the selection.")
    if not np.isfinite(baseline):
        raise AnalysisError("Baseline must be finite.")
    mask = np.ones(len(time), dtype=bool)
    if time_bounds is not None:
        low, high = time_bounds
        if not np.isfinite([low, high]).all() or low > high:
            raise AnalysisError("Enter a finite time range with start ≤ end.")
        mask = (time >= low) & (time <= high)
    time = time[mask]
    x = matrix[mask, columns.index(x_column)]
    y = matrix[mask, columns.index(y_column)] - baseline
    finite = np.isfinite(x) & np.isfinite(y)
    if not finite.any():
        raise AnalysisError("No finite samples in this selection and time range.")
    valid_y, valid_x = y[finite], x[finite]
    result = {"selection": selection.label, "scope": selection.scope,
              "x_column": x_column, "y_column": y_column, "baseline": baseline,
              "time_bounds_s": time_bounds, "samples": int(finite.sum()),
              "invalid_samples": int((~finite).sum()),
              "mean": float(valid_y.mean()), "std": float(valid_y.std()),
              "minimum": float(valid_y.min()), "maximum": float(valid_y.max()),
              "x_at_minimum": float(valid_x[valid_y.argmin()]),
              "x_at_maximum": float(valid_x[valid_y.argmax()])}
    if y_column in ("current1_na", "current2_na") and len(time) > 1:
        valid_intervals = finite[:-1] & finite[1:]
        result["charge_nc"] = float(np.sum((np.diff(time) * (y[:-1] + y[1:]) / 2)[valid_intervals]))
        result["integrated_duration_s"] = float(np.diff(time)[valid_intervals].sum())
    return x, y, result


def hop_map(dataset: AnalysisDataset, channel: str, statistic: str, selections=None) -> list[dict]:
    """Return measured hop statistics at JSON-defined physical coordinates.

    These are whole-hop statistics, not inferred contact heights or pulse means.
    Missing/unvisited hops remain absent; no interpolation or zero-filling.
    """
    operations = {"Mean": np.mean, "Minimum": np.min, "Maximum": np.max, "Std deviation": np.std}
    if statistic not in operations:
        raise AnalysisError("Unknown map statistic")
    grid = dataset.metadata.get("scan_grid")
    if not isinstance(grid, dict) or not isinstance(grid.get("pixels"), list):
        raise AnalysisError("Physical hop coordinates are missing from scan_grid metadata.")
    pixels = grid["pixels"]
    coordinates = {int(p["scan_pixel"]): (float(p["x_um"]), float(p["y_um"])) for p in pixels}
    if not coordinates:
        raise AnalysisError("Physical hop coordinates are missing from scan_grid metadata.")
    result = []
    for selection in hop_selections(dataset) if selections is None else selections:
        if selection.pixel not in coordinates:
            continue
        x, y = coordinates[selection.pixel]
        values = selection.rows.matrix[:, selection.rows.columns.index(channel)]
        values = values[np.isfinite(values)]
        if values.size and np.isfinite([x, y]).all():
            result.append({"scan_pixel": selection.pixel, "x_um": x, "y_um": y,
                           "value": float(operations[statistic](values)), "samples": len(values)})
    return result


def potential_map(dataset: AnalysisDataset, selections: list[Selection], channel: str,
                  potential: float, increasing: bool = True) -> list[dict]:
    """Map current at the first requested-direction crossing per complete CV.

    Interpolate adjacent samples only; never extrapolate. Repeated complete
    cycles are averaged per hop, with the number of contributing cycles exported.
    """
    if not np.isfinite(potential):
        raise AnalysisError("Map potential must be finite.")
    grid = dataset.metadata.get("scan_grid")
    if not isinstance(grid, dict) or not isinstance(grid.get("pixels"), list):
        raise AnalysisError("Physical hop coordinates are missing from scan_grid metadata.")
    coordinates = {int(p["scan_pixel"]): (float(p["x_um"]), float(p["y_um"])) for p in grid["pixels"]}
    if not coordinates:
        raise AnalysisError("Physical hop coordinates are missing from scan_grid metadata.")
    groups = {}
    for selection in selections:
        if selection.pixel not in coordinates: continue
        matrix, columns = selection.rows.matrix, selection.rows.columns
        e, current = matrix[:, columns.index("voltage1_v")], matrix[:, columns.index(channel)]
        delta = np.diff(e)
        crossing = ((delta > 0) if increasing else (delta < 0)) & (np.minimum(e[:-1], e[1:]) <= potential) & (np.maximum(e[:-1], e[1:]) >= potential)
        crossing &= np.isfinite(current[:-1]) & np.isfinite(current[1:]) & np.isfinite(delta)
        indices = np.flatnonzero(crossing)
        if len(indices):
            i = indices[0]
            value = current[i] + (potential - e[i]) / delta[i] * (current[i + 1] - current[i])
            groups.setdefault(selection.pixel, []).append(value)
    return [{"scan_pixel": pixel, "x_um": coordinates[pixel][0], "y_um": coordinates[pixel][1],
             "value": float(np.mean(values)), "samples": len(values)} for pixel, values in sorted(groups.items())]
