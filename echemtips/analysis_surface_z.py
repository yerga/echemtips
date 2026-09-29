"""Conservative surface-Z recovery that does not require a complete CV cycle."""
import numpy as np
from .analysis_core import AnalysisDataset, NumericRows


def hop_dataset(dataset, selection):
    """Resolve per-hop combinatorial parameters without copying sample arrays."""
    from .combinatorial import pixel_parameters
    return AnalysisDataset(dataset.path, dataset.columns, selection.rows,
                           {**dataset.metadata, 'parameters': pixel_parameters(dataset.metadata, selection.pixel)})


def stationary_sweep_rows(dataset, selection):
    """Recover Z from a partial CV/LSV sweep, never from whole-hop extrema.

    Require at least four changing-potential edges, five distinct potentials,
    at least 20% of the programmed potential span, positive elapsed time and
    no more than 0.5 µm Z variation throughout the selected interval. Distinct
    stationary levels are ambiguous and rejected. This is a surface estimate,
    not independent proof of contact or validation of a complete voltammogram.
    """
    p = dataset.metadata.get('parameters') or {}
    try:
        start = float(p['cv_start_v'] if 'cv_start_v' in p else p['start_v'])
        vertex = float(p['cv_vertex1_v'] if 'cv_vertex1_v' in p else p['vertex1_v'])
        end = float(p.get('cv_vertex2_v', p.get('vertex2_v', start)))
    except (KeyError, TypeError, ValueError):
        return None
    targets = [start, vertex] if p.get('waveform') == 'LSV' else [start, vertex, end]
    span = float(np.ptp(targets))
    if not np.isfinite(targets).all() or span < 1e-6:
        return None
    rows = selection.rows
    if 'measurement_phase' in rows.columns:
        rows=NumericRows(rows.columns,rows.matrix[rows.matrix[:,rows.columns.index('measurement_phase')]==2])
    if not {'elapsed_s', 'voltage1_v', 'z_um'}.issubset(rows.columns):
        return None
    t, e, z = (rows.matrix[:, rows.columns.index(k)] for k in ('elapsed_s','voltage1_v','z_um'))
    if len(t) < 5 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        return None
    tolerance = max(1e-8, span*1e-5)
    finite = np.isfinite(e) & np.isfinite(z)
    active = np.flatnonzero(finite[:-1] & finite[1:] & (np.abs(np.diff(e)) > tolerance)) + 1
    if len(active) < 4:
        return None
    # Vectorized grouping: ignore setup jumps; validate the entire interval
    # below so gradual motion or unobserved excursions cannot become contact.
    active = active[np.abs(z[active]-z[active-1]) <= .5]
    groups = np.split(active, np.flatnonzero(np.abs(np.diff(z[active])) > .5)+1)
    accepted = []
    for group in groups:
        if len(group) < 4:
            continue
        a, b = group[0]-1, group[-1]+1
        zz, ee = z[a:b], e[a:b]
        if not np.isfinite(zz).all() or np.ptp(zz) > .5 or not np.isfinite(ee).all():
            continue
        if np.ptp(ee) < max(.02, span*.2) or len(np.unique(np.round(ee/tolerance))) < 5:
            continue
        if t[b-1]-t[a] < max(.02, 3*np.median(np.diff(t))):
            continue
        if ee.min() < min(targets)-.03*span-.005 or ee.max() > max(targets)+.03*span+.005:
            continue
        accepted.append(rows[a:b])
    if not accepted:
        return None
    combined = np.concatenate([r.matrix for r in accepted])
    if np.ptp(combined[:, rows.columns.index('z_um')]) > .5:
        return None
    return NumericRows(rows.columns, combined)


def stationary_pulse_rows(dataset, selection):
    """Recover surface Z across a resolved initial→pulse transition.

    Use actual recorded transitions, not exact nominal phase deadlines: older
    host-timed programs can overrun their requested hold durations. Require a
    distinct pair of programmed levels and finite stationary Z on both sides.
    This validates a surface estimate, not the timing/completeness of the I–t.
    """
    p = dataset.metadata.get('parameters') or {}
    try:
        initial, pulse = float(p['initial_potential_v']), float(p['step_potential_v'])
        ih, ph = float(p['initial_hold_s']), float(p['step_hold_s'])
    except (KeyError, TypeError, ValueError):
        return None
    if not np.isfinite([initial,pulse,ih,ph]).all() or abs(initial-pulse) < .01 or min(ih,ph) <= 0:
        return None
    rows = selection.rows
    if 'measurement_phase' in rows.columns:
        rows=NumericRows(rows.columns,rows.matrix[rows.matrix[:,rows.columns.index('measurement_phase')]==2])
    if not {'elapsed_s','voltage1_v','z_um'}.issubset(rows.columns):
        return None
    t,e,z = (rows.matrix[:,rows.columns.index(k)] for k in ('elapsed_s','voltage1_v','z_um'))
    if len(t) < 6 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        return None
    dt = float(np.median(np.diff(t)))
    before, after = np.abs(e-initial) < .005, np.abs(e-pulse) < .005
    edges = np.flatnonzero(before[:-1] & after[1:])+1
    accepted = []
    for edge in edges:
        start, end = edge-1, edge+1
        while start > 0 and before[start-1]: start -= 1
        while end < len(t) and after[end]: end += 1
        need_before, need_after = max(.02,min(.1,ih*.5)), max(.02,min(.1,ph*.5))
        if t[edge]-t[start] < need_before or t[end-1]-t[edge]+dt < need_after:
            continue
        # Only the tail of the initial hold is needed to establish stationary Z.
        a = max(start, int(np.searchsorted(t, t[edge]-need_before)))
        if edge-a < 2 or end-edge < 3:
            continue
        zz = z[a:end]
        if np.isfinite(zz).all() and np.ptp(zz) <= .5:
            accepted.append(rows[edge:end])
    if not accepted:
        return None
    combined = np.concatenate([r.matrix for r in accepted])
    if np.ptp(combined[:,rows.columns.index('z_um')]) > .5:
        return None
    return NumericRows(rows.columns,combined)


def contact_failure_reason(dataset, cv_groups, hop_groups):
    """Explain why completion status alone cannot establish a usable contact map."""
    pixels = dataset.metadata.get('scan_grid', {}).get('pixels', [])
    if not pixels:
        return 'Contact Z unavailable: physical scan-grid metadata is missing.'
    if 'z_um' not in dataset.columns:
        return 'Contact Z unavailable: no measured z_um column or usable explicit contact heights.'
    if not np.isfinite(dataset.column('z_um')).any():
        return 'Contact Z unavailable: the recorded Z samples are all missing or nonfinite.'
    if not hop_groups and not cv_groups:
        return 'Contact Z unavailable: no usable recorded hop IDs for this selection.'
    p = dataset.metadata.get('parameters') or {}
    detail = ('I–t needs distinct initial/pulse potentials, positive holds and enough stationary Z samples on both sides of the pulse transition.'
              if 'step_hold_s' in p else
              'No complete CV or sufficiently sampled stationary CV/LSV sweep could be identified; check the saved waveform and Z data.')
    return ('Contact Z unavailable: no usable explicit contact heights. '+detail+
            ' A “complete” recording status does not guarantee these samples were saved. Whole-hop Z extrema are not used.')
