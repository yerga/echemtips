"""Non-destructive, full-resolution current processing shared by analysis views."""
from copy import deepcopy

import numpy as np

from .analysis_core import AnalysisDataset, NumericRows


def smooth_currents(dataset: AnalysisDataset, window: int, cycles=(), *,
                    method: str = "centered_moving_average", polynomial_order: int = 2) -> AnalysisDataset:
    """Filter currents in sample order without crossing acquisition boundaries.

    Savitzky–Golay uses polynomial edge fits and an odd window shortened to fit
    each segment; segments too short for the chosen order remain unchanged.
    The moving-average alternative uses available samples at edges.
    NaNs/infinities remain gaps. Potentials,
    positions, timestamps and tags are unchanged; the source array is never
    modified. Large windows can attenuate real peaks and must be chosen with care.
    """
    if not isinstance(window, (int, np.integer)) or window < 1 or window % 2 != 1 or window > 10001:
        raise ValueError("Smoothing window must be an odd integer from 1 to 10001.")
    if method not in {"centered_moving_average", "savitzky_golay"}:
        raise ValueError("Unknown smoothing method.")
    if window == 1:
        return dataset
    if method == "savitzky_golay":
        if not isinstance(polynomial_order, (int, np.integer)) or not 1 <= polynomial_order <= 5 or polynomial_order >= window:
            raise ValueError("Polynomial order must be 1–5 and smaller than the window.")
        from scipy.signal import savgol_filter
    matrix = dataset.rows.matrix.copy()
    count = len(matrix)
    breaks = np.zeros(count + 1, dtype=bool)
    breaks[0] = breaks[-1] = True
    for name in ("scan_pixel", "line_number", "cv_rate_index"):
        if name in dataset.columns:
            values = dataset.column(name)
            breaks[1:count] |= values[1:] != values[:-1]
    times = dataset.column("elapsed_s")
    breaks[1:count] |= np.diff(times) <= 0
    if "voltage1_v" in dataset.columns:
        voltage = dataset.column("voltage1_v")
        steps = np.diff(voltage)
        active = np.flatnonzero(np.isfinite(steps) & (steps != 0))
        if len(active) > 1:
            turns = active[1:][np.sign(steps[active[1:]]) != np.sign(steps[active[:-1]])]
            breaks[turns] = True
    # Extracted cycles are contiguous views into the original recording.
    stride = dataset.rows.matrix.strides[0]
    for cycle in cycles:
        if isinstance(cycle.rows, NumericRows) and np.shares_memory(cycle.rows.matrix, dataset.rows.matrix):
            start = (cycle.rows.matrix.ctypes.data - dataset.rows.matrix.ctypes.data) // stride
            breaks[start] = True
            breaks[min(count, start + len(cycle.rows))] = True
    for name in ("current1_na", "current2_na"):
        if name not in dataset.columns:
            continue
        column = dataset.columns.index(name)
        values = dataset.column(name)
        finite = np.isfinite(values)
        boundaries = breaks.copy()
        invalid = np.flatnonzero(~finite)
        boundaries[invalid] = boundaries[invalid + 1] = True
        edges = np.flatnonzero(boundaries)
        for start, end in zip(edges[:-1], edges[1:]):
            if start == end or not finite[start]:
                continue
            segment = values[start:end]
            if method == "savitzky_golay":
                effective_window = min(window, len(segment) if len(segment) % 2 else len(segment) - 1)
                if effective_window > polynomial_order:
                    matrix[start:end, column] = savgol_filter(segment, effective_window, polynomial_order, mode="interp")
                continue
            offsets = np.arange(len(segment))
            left = np.maximum(0, offsets - window // 2)
            right = np.minimum(len(segment), offsets + window // 2 + 1)
            sums = np.r_[0., np.cumsum(segment)]
            matrix[start:end, column] = (sums[right] - sums[left]) / (right - left)
    metadata = deepcopy(dataset.metadata)
    metadata["analysis_processing"] = {
        "method": method, "window_samples": window,
        "channels": [c for c in ("current1_na", "current2_na") if c in dataset.columns],
        "edges": "polynomial interpolation" if method == "savitzky_golay" else "available samples only", "gaps": "preserved",
        "boundaries": "hop, waypoint, non-increasing time, CV cycle and E1 sweep reversal",
    }
    if method == "savitzky_golay":
        metadata["analysis_processing"].update(
            polynomial_order=polynomial_order, sampling="sample index; no resampling",
            short_segments="largest fitting odd window; unchanged if window <= polynomial order")
    return AnalysisDataset(dataset.path, dataset.columns, NumericRows(dataset.columns, matrix), metadata)
