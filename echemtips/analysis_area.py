"""Experimental, non-destructive retraction diagnostics and area normalization.

No hardware access. A current break is evidence of detachment, not proof of a
particular wetted geometry. The d=h model needs independent validation.
"""
from dataclasses import dataclass
from copy import deepcopy
import numpy as np
from scipy.ndimage import median_filter

from .analysis_core import AnalysisDataset, NumericRows, AnalysisError

DENSITY_COLUMNS = {'Current density 1': 'current_density1_ma_cm2',
                   'Current density 2': 'current_density2_ma_cm2'}


@dataclass(frozen=True)
class RetractionConfig:
    """Explicit detector tolerances in physical units, saved with every result."""
    channel: str = 'current1_na'
    window_samples: int = 7
    noise_multiple: float = 6.
    minimum_jump_pa: float = 1.
    z_tolerance_um: float = .05
    potential_tolerance_v: float = .005
    xy_tolerance_um: float = .2
    slope: float = 1.
    intercept_um: float = 0.

    def validate(self):
        """Reject malformed settings rather than producing plausible-looking areas."""
        if self.channel not in ('current1_na', 'current2_na'):
            raise AnalysisError('Select a recorded current channel.')
        if not isinstance(self.window_samples, int) or self.window_samples < 3 or self.window_samples > 501 or self.window_samples % 2 == 0:
            raise AnalysisError('Detector window must be an odd integer from 3 to 501.')
        values = [self.noise_multiple, self.minimum_jump_pa, self.z_tolerance_um,
                  self.potential_tolerance_v, self.xy_tolerance_um, self.slope, self.intercept_um]
        if not np.isfinite(values).all() or min(values[:-1]) <= 0:
            raise AnalysisError('Detector tolerances and calibration slope must be finite and positive.')


def _mad(values):
    return float(1.4826 * np.median(np.abs(values - np.median(values))))


def detect_retraction(rows, surface_end_s, surface_z_um, config=RetractionConfig(), *, current_limit_na=None):
    """Detect a persistent sharp return to a stable tail baseline after surface data.

    Uses measured Z (decreasing on withdrawal), raw signed current and recorded
    potential. No exact-zero comparison, smoothing of source data or extrapolated
    contact heights. All uncertainty values are sampling brackets, not accuracy.
    """
    config.validate()
    result = dict(status='unavailable', reason='', diameter_um=None, area_um2=None,
                  stretch_um=None, break_time_s=None, z_resolution_um=None)
    def reject(reason):
        """Return an unavailable estimate with an actionable diagnostic reason."""
        return {**result, 'reason': reason}
    needed = ('elapsed_s', 'z_um', 'voltage1_v', config.channel)
    if not set(needed).issubset(rows.columns):
        return reject('Missing time, measured Z, potential or current channel')
    t, z, e, current = (rows.matrix[:, rows.columns.index(k)] for k in needed)
    w = config.window_samples
    if len(t) < 4*w or not np.isfinite([surface_end_s, surface_z_um]).all():
        return reject('Insufficient surface/retraction samples')
    if not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        return reject('Non-increasing or invalid timestamps')
    after = np.flatnonzero((t > surface_end_s) & (z < surface_z_um-config.z_tolerance_um))
    if not len(after):
        return reject('No resolved downward retraction after the surface program')
    start = int(after[0])
    # Include a short pre-motion interval, but never the electrochemical sweep.
    start = max(int(np.searchsorted(t, surface_end_s, side='right')), start-w)
    t, z, e, current = (a[start:] for a in (t, z, e, current))
    if len(t) < 4*w or not all(np.isfinite(a).all() for a in (z, e, current)):
        return reject('Too few finite retraction samples')
    if current_limit_na is not None and np.any(np.abs(current) >= current_limit_na * .999):
        return reject('Current reaches the recorded input range; possible saturation')
    if np.max(np.abs(e - np.median(e))) > config.potential_tolerance_v:
        return reject('Potential changes during the candidate retraction')
    for channel in ('x_um', 'y_um'):
        if channel in rows.columns:
            v = rows.matrix[start:, rows.columns.index(channel)]
            if not np.isfinite(v).all() or np.ptp(v) > config.xy_tolerance_um:
                return reject('Lateral motion or invalid XY during the candidate retraction')
    zz = median_filter(z, size=w, mode='nearest')
    if np.max(zz - np.minimum.accumulate(zz)) > config.z_tolerance_um:
        return reject('Z reverses during the candidate retraction')
    tail = current[-max(w, len(current)//5):]
    baseline = float(np.median(tail))
    noise = max(_mad(tail), 1e-9)
    threshold = max(config.minimum_jump_pa/1000, config.noise_multiple*noise)
    if abs(float(np.median(tail[:len(tail)//2])) - float(np.median(tail[len(tail)//2:]))) > threshold:
        return reject('Unstable post-retraction baseline')
    residual = np.abs(median_filter(current, size=w, mode='nearest') - baseline)
    # Attached and detached windows must persist; a single spike is not a break.
    attached = residual > 2*threshold
    quiet = residual < threshold
    before = np.convolve(attached.astype(float), np.ones(w)/w, mode='valid')
    after_quiet = np.convolve(quiet.astype(float), np.ones(w)/w, mode='valid')
    candidates = np.flatnonzero((before[:len(t)-3*w] >= .85) &
                               (after_quiet[w:len(t)-2*w] >= .85)) + w
    if not len(candidates):
        return reject('No sharp, sustained attached-to-baseline transition')
    # A later recovery of current makes the first apparent break ambiguous.
    index = int(candidates[0])
    if np.mean(quiet[index+w:]) < .9:
        return reject('Current recovers after the candidate break; ambiguous detachment')
    if float(np.median(residual[:w])) <= 2*threshold:
        return reject('No sustained attached current at the start of retraction')
    # Reject weak, broad decays instead of interpreting electrical settling as distance.
    pre = float(np.median(residual[max(0,index-2*w):index-w+1]))
    if pre <= 2*threshold:
        return reject('Gradual decay or insufficient contrast')
    lo, hi = max(0,index-w), min(len(t)-1,index+w)
    stretch = float(surface_z_um-z[index])
    resolution = float(np.ptp(z[lo:hi+1]))
    if stretch <= max(config.z_tolerance_um, resolution):
        return reject('Break too close to motion onset for a resolved diameter')
    diameter = config.slope*stretch+config.intercept_um
    if diameter <= 0:
        return reject('Calibration produces a non-positive diameter')
    return dict(result, status='estimated', reason='Experimental endpoint-area estimate; verify geometry and Z calibration',
                stretch_um=stretch, diameter_um=float(diameter), area_um2=float(np.pi*diameter**2/4),
                break_time_s=float(t[index]), break_z_um=float(z[index]), surface_z_um=float(surface_z_um),
                retraction_start_s=float(t[0]), baseline_na=baseline, noise_na=noise,
                potential_v=float(np.median(e)), z_resolution_um=resolution,
                contrast_to_noise=float(pre/noise), calibration='d = a*h + b',
                warning='Filter delay and model error are not included in the sampling bracket')


def estimate_landings(dataset, config=RetractionConfig(), *, cancelled=lambda: False):
    """Analyze each recorded hop using surface-program evidence, never whole-hop maxima."""
    from .analysis_tools import hop_selections
    from .analysis_surface_z import hop_dataset, stationary_sweep_rows, stationary_pulse_rows
    from .analysis_frames import it_surface_rows
    from .analysis_core import extract_cv_cycles
    config.validate()
    if 'scan_pixel' not in dataset.columns:
        return []
    groups = hop_selections(dataset)
    info = {int(p['scan_pixel']): p for p in dataset.metadata.get('scan_grid', {}).get('pixels', [])}
    results = []
    for group in groups:
        if cancelled():
            raise AnalysisError('Cancelled')
        if group.pixel < 0:
            continue
        row = dict(scan_pixel=group.pixel, status='unavailable', reason='No recognizable stationary surface program',
                   diameter_um=None, area_um2=None, stretch_um=None, break_time_s=None, z_resolution_um=None)
        p = info.get(group.pixel, {})
        if p.get('contact_detected') is False or p.get('status') in ('failed', 'aborted', 'no_contact'):
            results.append(dict(row, reason='Landing marked failed or incomplete')); continue
        sub = hop_dataset(dataset, group)
        cycles = extract_cv_cycles(sub)
        def stationary(surface):
            """A waveform label alone does not prove that approach motion ended."""
            if surface is None or not len(surface) or 'z_um' not in surface.columns:
                return False
            z = surface.matrix[:, surface.columns.index('z_um')]
            return np.isfinite(z).all() and np.ptp(z) <= config.z_tolerance_um*2

        surface = cycles[-1].rows if cycles else None
        if not stationary(surface):
            surface = stationary_sweep_rows(sub, group)
        if surface is None:
            found = it_surface_rows(sub, group)
            surface = found[0] if found is not None else stationary_pulse_rows(sub, group)
        if surface is not None and len(surface) and 'z_um' in surface.columns:
            z = surface.matrix[:, surface.columns.index('z_um')]
            if np.isfinite(z).all() and np.ptp(z) <= config.z_tolerance_um*2:
                end = float(surface.matrix[-1, surface.columns.index('elapsed_s')])
                # Retraction begins after post-conditioning, not after the CV.
                # Keep the electrochemical holds out of the constant-E test.
                if 'measurement_phase' in group.rows.columns:
                    m=group.rows.matrix; cols=group.rows.columns
                    post=m[m[:,cols.index('measurement_phase')]==3]
                    if len(post):
                        end=max(end,float(post[-1,cols.index('elapsed_s')]))
                # The deployed recorder converts the ±10 V ADC input using
                # V/nA sensitivity. Do not infer a rail from the observed peak.
                settings = dataset.metadata.get('settings', {})
                sensitivity = settings.get(config.channel.replace('_na', '_v_per_na'))
                limit = None
                if settings.get('mode') == 'NI FPGA' and isinstance(sensitivity, (int, float)) and np.isfinite(sensitivity) and sensitivity > 0:
                    limit = 10. / sensitivity
                row.update(detect_retraction(group.rows, end, float(np.median(z)), config,
                                             current_limit_na=limit))
        results.append(row)
    return results


def normalize_dataset(dataset, config, results=()):
    """Append explicit mA/cm² channels; preserve nA columns and missing-area gaps.

    Providers: nominal area, retraction results, or per-landing area mapping. The
    mapping contract supports future SEM/other area sources without new math.
    """
    mode = config.get('mode', 'none')
    if mode == 'none':
        return dataset
    if mode not in ('nominal', 'retraction', 'per_landing'):
        raise AnalysisError('Unknown normalization source')
    areas = {}
    if mode == 'nominal':
        area = float(config.get('area_um2', 0))
        if not np.isfinite(area) or area <= 0:
            raise AnalysisError('Nominal area must be finite and positive')
    elif mode == 'retraction':
        areas = {int(r['scan_pixel']): r['area_um2'] for r in results if r['status'] == 'estimated'}
    else:
        for k, v in config.get('areas_um2', {}).items():
            if str(int(k)) != str(k):
                raise AnalysisError('Per-landing pixel IDs must be nonnegative integers')
            areas[int(k)] = float(v)
    if any(k < 0 or not np.isfinite(v) or v <= 0 for k,v in areas.items()):
        raise AnalysisError('Per-landing areas must have nonnegative pixel IDs and finite positive values')
    if mode != 'nominal' and 'scan_pixel' not in dataset.columns:
        raise AnalysisError('Per-landing normalization requires scan_pixel IDs')
    area_values = np.full(len(dataset.rows), area if mode == 'nominal' else np.nan)
    if mode != 'nominal':
        pixels = dataset.column('scan_pixel')
        # Vectorized lookup avoids scanning all samples separately for every hop.
        keys = np.array(sorted(areas), dtype=float)
        if len(keys):
            indices = np.searchsorted(keys, pixels)
            match = (indices < len(keys)) & np.isfinite(pixels)
            match &= keys[np.minimum(indices,len(keys)-1)] == pixels
            area_values[match] = np.array([areas[int(k)] for k in keys])[indices[match]]
    columns, arrays = list(dataset.columns), [dataset.rows.matrix]
    columns.append('normalization_area_um2'); arrays.append(area_values[:,None])
    for current, density in zip(('current1_na','current2_na'), DENSITY_COLUMNS.values()):
        if current in dataset.columns:
            columns.append(density)
            # 1 nA/µm² = 100 mA/cm².
            arrays.append((dataset.column(current)*100/area_values)[:,None])
    metadata = deepcopy(dataset.metadata)
    metadata['analysis_normalization'] = dict(config, schema=1, areas_um2={str(k):v for k,v in areas.items()},
        units='mA/cm²', scope='One endpoint area per landing; not time-resolved area or ECSA',
        missing='NaN; never replaced by nominal area', detector=config.get('detector'),
        results=list(results) if mode == 'retraction' else [])
    return AnalysisDataset(dataset.path, tuple(columns), NumericRows(columns, np.column_stack(arrays)), metadata)


def attach_diameters(dataset, results):
    """Expose diagnostic diameter/area maps as derived columns, preserving gaps."""
    if not results or 'scan_pixel' not in dataset.columns:
        return dataset
    good = {r['scan_pixel']:r for r in results if r['status']=='estimated'}
    pixels=dataset.column('scan_pixel')
    keys=np.array(sorted(good),dtype=float)
    columns=list(dataset.columns); arrays=[dataset.rows.matrix]
    for column,key in [('estimated_diameter_um','diameter_um'),('estimated_area_um2','area_um2')]:
        values=np.full(len(pixels),np.nan)
        if len(keys):
            idx=np.searchsorted(keys,pixels)
            match=(idx<len(keys)) & np.isfinite(pixels)
            match &= keys[np.minimum(idx,len(keys)-1)]==pixels
            values[match]=np.array([good[int(k)][key] for k in keys])[idx[match]]
        columns.append(column); arrays.append(values[:,None])
    return AnalysisDataset(dataset.path,tuple(columns),NumericRows(columns,np.column_stack(arrays)),
                           {**dataset.metadata,'analysis_retraction':list(results)})
