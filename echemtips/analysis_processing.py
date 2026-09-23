"""Non-destructive, full-resolution current processing shared by analysis views."""
from copy import deepcopy

import numpy as np

from .analysis_core import AnalysisDataset, NumericRows


def smooth_currents(dataset: AnalysisDataset, window: int, cycles=()) -> AnalysisDataset:
    """Apply a centered sample-count mean without crossing acquisition boundaries.

    Edges use available samples only. NaNs/infinities remain gaps. Potentials,
    positions, timestamps and tags are unchanged; the source array is never
    modified. Large windows can attenuate real peaks and must be chosen with care.
    """
    if window < 1 or window % 2 != 1 or window > 10001:
        raise ValueError("Smoothing window must be an odd integer from 1 to 10001.")
    if window == 1:
        return dataset
    matrix = dataset.rows.matrix.copy()
    count = len(matrix)
    breaks = np.zeros(count + 1, dtype=bool)
    breaks[0] = breaks[-1] = True
    for name in ("scan_pixel", "line_number"):
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
            offsets = np.arange(len(segment))
            left = np.maximum(0, offsets - window // 2)
            right = np.minimum(len(segment), offsets + window // 2 + 1)
            sums = np.r_[0., np.cumsum(segment)]
            matrix[start:end, column] = (sums[right] - sums[left]) / (right - left)
    metadata = deepcopy(dataset.metadata)
    metadata["analysis_processing"] = {
        "method": "centered_moving_average", "window_samples": window,
        "channels": [c for c in ("current1_na", "current2_na") if c in dataset.columns],
        "edges": "available samples only", "gaps": "preserved",
        "boundaries": "hop, waypoint, non-increasing time, CV cycle and E1 sweep reversal",
    }
    return AnalysisDataset(dataset.path, dataset.columns, NumericRows(dataset.columns, matrix), metadata)
