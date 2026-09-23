"""Bounded scientific display reduction, never used for measurement or export."""
import numpy as np


def envelope_indices(x, y, limit=12000):
    """Keep ordered X/Y extrema and invalid-data breaks within display bins."""
    x, y = np.asarray(x), np.asarray(y)
    count = min(len(x), len(y))
    if count <= limit:
        return np.arange(count)
    bins = max(1, (limit - 2) // 5)
    edges = np.linspace(0, count, bins + 1, dtype=int)
    selected = {0, count - 1}
    for start, end in zip(edges[:-1], edges[1:]):
        valid = np.isfinite(x[start:end]) & np.isfinite(y[start:end])
        indices = np.flatnonzero(valid) + start
        if len(indices):
            for values in (x, y):
                selected.add(int(indices[np.argmin(values[indices])]))
                selected.add(int(indices[np.argmax(values[indices])]))
        invalid = np.flatnonzero(~valid)
        if len(invalid):
            selected.add(int(start + invalid[0]))
    return np.array(sorted(selected), dtype=int)
