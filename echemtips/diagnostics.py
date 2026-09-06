"""Reusable signal-quality and pipette-characterization calculations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
from statistics import fmean
from typing import Any, Iterable

from .models import Sample


@dataclass(frozen=True, slots=True)
class SignalStatistics:
    channel: str
    count: int
    duration_s: float
    mean_na: float
    rms_noise_pa: float
    peak_to_peak_pa: float
    maximum_deviation_pa: float
    drift_pa_s: float


@dataclass(frozen=True, slots=True)
class LinearFit:
    slope: float
    intercept: float
    r_squared: float
    count: int


def current_value(sample: Sample, channel: str) -> float:
    if channel == "Current 1":
        return sample.current1_na
    if channel == "Current 2":
        return sample.current2_na
    raise ValueError("Current channel must be Current 1 or Current 2.")


def linear_fit(x_values: Iterable[float], y_values: Iterable[float]) -> LinearFit:
    pairs = [
        (float(x), float(y))
        for x, y in zip(x_values, y_values)
        if math.isfinite(float(x)) and math.isfinite(float(y))
    ]
    if len(pairs) < 2:
        raise ValueError("At least two finite points are required for a linear fit.")
    x_mean = fmean(x for x, _ in pairs)
    y_mean = fmean(y for _, y in pairs)
    denominator = sum((x - x_mean) ** 2 for x, _ in pairs)
    if denominator <= 0:
        raise ValueError("The fit requires more than one distinct X value.")
    slope = sum((x - x_mean) * (y - y_mean) for x, y in pairs) / denominator
    intercept = y_mean - slope * x_mean
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in pairs)
    total = sum((y - y_mean) ** 2 for _, y in pairs)
    r_squared = 1.0 if total <= 0 and residual <= 0 else 0.0 if total <= 0 else 1.0 - residual / total
    return LinearFit(slope, intercept, r_squared, len(pairs))


def signal_statistics(samples: Iterable[Sample], channel: str) -> SignalStatistics:
    rows = list(samples)
    if len(rows) < 2:
        raise ValueError("At least two samples are required.")
    currents = [current_value(sample, channel) for sample in rows]
    times = [sample.elapsed_s for sample in rows]
    mean = fmean(currents)
    rms = math.sqrt(fmean((value - mean) ** 2 for value in currents))
    duration = max(times) - min(times)
    drift = linear_fit(times, currents).slope * 1000.0
    return SignalStatistics(
        channel=channel,
        count=len(rows),
        duration_s=duration,
        mean_na=mean,
        rms_noise_pa=rms * 1000.0,
        peak_to_peak_pa=(max(currents) - min(currents)) * 1000.0,
        maximum_deviation_pa=max(abs(value - mean) for value in currents) * 1000.0,
        drift_pa_s=drift,
    )


def suggested_baseline_threshold_pa(statistics: SignalStatistics) -> float:
    """Conservative starting threshold above both RMS and observed extrema."""
    return max(5.0 * statistics.rms_noise_pa, 1.25 * statistics.maximum_deviation_pa)


def resistance_fit(samples: Iterable[Sample], channel: str) -> tuple[LinearFit, float]:
    rows = list(samples)
    fit = linear_fit(
        (sample.voltage1_v for sample in rows),
        (current_value(sample, channel) for sample in rows),
    )
    if math.isclose(fit.slope, 0.0, abs_tol=1e-12):
        raise ValueError("The fitted conductance is zero; check the circuit and selected current channel.")
    # i(nA) = 1000 * E(V) / R(MOhm)
    return fit, abs(1000.0 / fit.slope)


def stray_capacitance_pf(samples: Iterable[Sample], channel: str) -> float:
    """Estimate C from the forward/reverse current separation of a triangular sweep."""
    rows = list(samples)
    forward: list[float] = []
    reverse: list[float] = []
    rates: list[float] = []
    for previous, current in zip(rows, rows[1:]):
        dt = current.elapsed_s - previous.elapsed_s
        dv = current.voltage1_v - previous.voltage1_v
        if dt <= 0 or math.isclose(dv, 0.0, abs_tol=1e-12):
            continue
        rate = dv / dt
        rates.append(abs(rate))
        (forward if rate > 0 else reverse).append(current_value(current, channel))
    if not forward or not reverse or not rates:
        raise ValueError("A complete forward and reverse potential sweep is required.")
    scan_rate = fmean(rates)
    # nA/(V/s) is nF; multiply by 1000 for pF. Separation is 2*C*v.
    return abs(fmean(forward) - fmean(reverse)) / (2.0 * scan_rate) * 1000.0


def pipette_radius_nm(resistance_mohm: float, conductivity_s_m: float, half_angle_deg: float) -> float:
    """Estimate aperture radius using R = 1/(kappa*a*tan(alpha))."""
    if not math.isfinite(resistance_mohm) or resistance_mohm <= 0:
        raise ValueError("Pipette resistance must be positive.")
    if not math.isfinite(conductivity_s_m) or conductivity_s_m <= 0:
        raise ValueError("Electrolyte conductivity must be positive.")
    if not math.isfinite(half_angle_deg) or not 0 < half_angle_deg < 90:
        raise ValueError("Pipette half-angle must be between 0 and 90 degrees.")
    radius_m = 1.0 / (resistance_mohm * 1e6 * conductivity_s_m * math.tan(math.radians(half_angle_deg)))
    return radius_m * 1e9


def save_json_report(directory: str | Path, prefix: str, payload: dict[str, Any]) -> Path:
    folder = Path(directory).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", prefix.lower()).strip("_") or "diagnostic"
    timestamp = datetime.now()
    path = folder / f"{timestamp:%Y%m%d_%H%M%S}_{slug}.json"
    suffix = 1
    while path.exists():
        path = folder / f"{timestamp:%Y%m%d_%H%M%S}_{slug}_{suffix:03d}.json"
        suffix += 1
    serializable = {
        key: asdict(value) if hasattr(value, "__dataclass_fields__") else value
        for key, value in payload.items()
    }
    path.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")
    return path
