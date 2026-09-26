"""Advisory analytical models from Anderson & Edwards (2023).

Equations 1–3, 10–11 and Table 1, DOI 10.1021/acs.analchem.3c00216.
SI units internally. Currents follow IUPAC (reduction negative). These
diffusion-only models do not classify landings or control hardware.
"""
from dataclasses import dataclass
import math
import numpy as np

F = 96485.33212
RGAS = 8.314462618
MODEL_VERSION = 1
DOI = "10.1021/acs.analchem.3c00216"


@dataclass(frozen=True)
class ModelParameters:
    """Explicit physical geometry; never inferred from map styling or contact Z."""
    radius_m: float = 200e-9
    half_angle_deg: float = 7.5
    height_m: float = 100e-9
    footprint_radius_m: float = 224e-9
    diffusion_m2_s: float = 1e-9
    concentration_mol_m3: float = 1.0
    electrons: int = 1
    radius_model: str = "R3"
    oxidation: bool = False
    formal_potential_v: float = 0.0
    temperature_k: float = 298.15
    rate_m_s: float = 1e-5
    alpha: float = 0.5

    def validate(self):
        """Reject nonphysical inputs rather than silently clipping predictions."""
        positive = (self.radius_m, self.footprint_radius_m, self.diffusion_m2_s,
                    self.concentration_mol_m3, self.temperature_k, self.rate_m_s)
        if not all(math.isfinite(v) and v > 0 for v in positive):
            raise ValueError("Radius, footprint, diffusion, concentration, temperature and rate must be positive and finite.")
        if not math.isfinite(self.height_m) or self.height_m < 0:
            raise ValueError("Meniscus height must be finite and nonnegative.")
        if not math.isfinite(self.half_angle_deg) or not 0 < self.half_angle_deg < 90:
            raise ValueError("Pipette half-angle must be between 0 and 90 degrees.")
        if not isinstance(self.electrons, int) or self.electrons < 1:
            raise ValueError("Electron count must be a positive integer.")
        if not math.isfinite(self.formal_potential_v) or not 0 < self.alpha < 1:
            raise ValueError("Formal potential must be finite and alpha between 0 and 1.")
        if self.radius_model not in ("R1", "R2", "R3"):
            raise ValueError("Select R1, R2 or R3.")
        if self.equivalent_radius_m <= 0:
            raise ValueError("This geometry gives a nonpositive equivalent radius; reduce height or review geometry.")

    @property
    def equivalent_radius_m(self):
        """Return the paper's effective spherical radius, not footprint radius."""
        g = math.radians(self.half_angle_deg)
        if self.radius_model == "R2":
            return self.radius_m / math.tan(g)
        r = self.radius_m / math.sin(g)
        return r - self.height_m / math.cos(g) if self.radius_model == "R3" else r

    @property
    def sign(self):
        """IUPAC sign of the chosen reaction."""
        return 1 if self.oxidation else -1


def limiting_current(p: ModelParameters) -> float:
    """Signed diffusion-limited steady current, A (Table 1)."""
    p.validate()
    fraction = 2 * math.sin(math.radians(p.half_angle_deg) / 2)**2
    return p.sign * 2 * math.pi * p.electrons * F * p.diffusion_m2_s * p.concentration_mol_m3 * p.equivalent_radius_m * fraction


def step_current(p: ModelParameters, time_s, *, interval_s=0.0):
    """Ideal transport-limited step from uniform concentration, in A.

    With interval_s > 0, time_s denotes bin END times and the exact mean
    over [t-interval_s,t] is returned, including a bin beginning at zero.
    This excludes charging, filters, prior depletion and pulse history.
    """
    limit = limiting_current(p)
    t = np.asarray(time_s, dtype=float)
    if not np.isfinite(t).all() or np.any(t <= 0):
        raise ValueError("Step times must be positive and finite.")
    if not math.isfinite(interval_s) or interval_s < 0 or np.any(t < interval_s):
        raise ValueError("Averaging interval must be nonnegative and no larger than the first step time.")
    inverse_root = 1 / np.sqrt(t) if interval_s == 0 else 2 / (np.sqrt(t) + np.sqrt(t - interval_s))
    return limit * (1 + p.equivalent_radius_m / math.sqrt(math.pi * p.diffusion_m2_s) * inverse_root)


def steady_wave(p: ModelParameters, potential_v):
    """Eq. 11 for a one-electron, equal-diffusivity couple, zero bulk product.

    Oxidation is the corresponding reverse reaction with bulk Red only.
    No transient CV history or ohmic-drop correction is included.
    Log-domain evaluation prevents overflow at extreme potentials.
    """
    limit = limiting_current(p)
    if p.electrons != 1:
        raise ValueError("The kinetic wave is restricted to the paper's one-electron model.")
    e = np.asarray(potential_v, dtype=float)
    if not np.isfinite(e).all():
        raise ValueError("Potentials must be finite.")
    m = abs(limit) / (F * math.pi * p.footprint_radius_m**2 * p.concentration_mol_m3)
    x = F * (e - p.formal_potential_v) / (RGAS * p.temperature_k)
    a = (1 - p.alpha) * x if p.oxidation else -p.alpha * x
    b = -p.alpha * x if p.oxidation else (1 - p.alpha) * x
    denominator = np.logaddexp(np.logaddexp(math.log(m / p.rate_m_s), a), b)
    return limit * np.exp(a - denominator)


def model_warnings(p: ModelParameters) -> list[str]:
    """Applicability notes; never acquisition or landing-quality decisions."""
    notes = ["Diffusion only; excess supporting electrolyte, fixed geometry and a single dissolved redox couple.",
             "Step: uniform initial concentration, no charging or prior depletion. Wave: steady state, equal diffusivities, negligible iR drop.",
             "Geometry is assumed, not measured by this calculator. Prediction mismatch is not a failed landing."]
    if not 5 <= p.half_angle_deg <= 15:
        notes.append("Half-angle is outside the paper's 5–15° comparison range; accuracy is unvalidated here.")
    return notes
