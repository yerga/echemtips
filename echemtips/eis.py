"""Experimental command-referenced EIS using the existing timed-hold protocol."""
from dataclasses import dataclass, field, replace
import math
import numpy as np

from .models import ApproachITParameters, ApproachParameters
from .experiments import ApproachITExperiment, ExperimentState


@dataclass
class EISParameters(ApproachITParameters):
    """Conservative sequential stepped-sine settings; no calibrated cell EIS claim."""
    frequencies_hz: tuple = (1., 3., 10.)
    dc_v: float = 0.1
    amplitude_v: float = 0.01
    settle_cycles: int = 2
    measure_cycles: int = 4
    current_channel: str = "Current 1"
    feedback_mode: str = "magnitude"
    settling_time_s: float = 1.
    eis_reference: str = "FPGA applied command E1, not measured cell potential"
    eis_results: list = field(default_factory=list)
    simulation_circuit: str = "Rs=100 Mohm; Rp=500 Mohm in parallel with C=100 pF"

    def actual_frequency(self, frequency):
        """Return nominal frequency after integer-microsecond hold quantization."""
        return 1e6 / (64 * round(1e6 / (64 * frequency)))

    def it_steps(self):
        """Build 64 potential holds/cycle with explicit frequency and phase labels."""
        steps = [(self.dc_v, max(.01, self.settling_time_s), "eis:bias")]
        for index, frequency in enumerate(self.frequencies_hz):
            dt = round(1e6 / (64 * frequency)) / 1e6
            for cycle in range(self.settle_cycles + self.measure_cycles):
                phase = "settle" if cycle < self.settle_cycles else "measure"
                for j in range(64):
                    steps.append((self.dc_v + self.amplitude_v * math.sin(2*math.pi*j/64),
                                  dt, f"eis:{index}:{phase}"))
        steps.append((self.dc_v, .01, "eis:return"))
        return steps

    def validate(self, settings):
        """Reject unsafe travel, unresolved waveforms and excessive tag budgets."""
        errors = ApproachParameters.validate(self, settings)
        if not self.retract_after:
            errors.append("Experimental EIS requires return to initial Z.")
        if self.current_channel not in {"Current 1", "Current 2"}:
            errors.append("Choose Current 1 or Current 2 for impedance.")
        if not (math.isfinite(self.dc_v) and math.isfinite(self.amplitude_v)
                and .001 <= self.amplitude_v <= .05):
            errors.append("AC amplitude must be 1–50 mV peak and DC must be finite.")
        elif (abs(self.dc_v) + self.amplitude_v) * max(1., settings.command_voltage_ratio) > 10:
            errors.append("DC ± AC exceeds the configured command range.")
        if not (isinstance(self.settle_cycles, int) and 1 <= self.settle_cycles <= 20
                and isinstance(self.measure_cycles, int) and 3 <= self.measure_cycles <= 20):
            errors.append("Use 1–20 settling cycles and 3–20 measurement cycles.")
        if not 1 <= len(self.frequencies_hz) <= 30 or any(
            not math.isfinite(f) or not .5 <= f <= 20 for f in self.frequencies_hz
        ):
            errors.append("This prototype supports 1–30 frequencies between 0.5 and 20 Hz.")
        elif any(self.actual_frequency(f)*settings.effective_period_s > 1/32 for f in self.frequencies_hz):
            errors.append("Use at least 32 recorded samples/cycle: reduce maximum frequency or averaging.")
        if not errors and any(self.measure_cycles/f/settings.effective_period_s > 200000 for f in self.frequencies_hz):
            errors.append("Too many samples per frequency; increase averaging (maximum 200,000).")
        if settings.mode == "NI FPGA" and self.amplitude_v*settings.command_voltage_ratio < 10*20/65536:
            errors.append("AC amplitude must span at least 10 DAC counts; increase amplitude.")
        if not errors:
            frames = sum(math.ceil(t*1e6/32767) for _,t,_ in self.it_steps())
            if frames > 24000:
                errors.append("EIS exceeds the conservative 24,000 waypoint budget; reduce frequencies/cycles.")
        return errors


def fit_impedance(times, potentials, currents_na, frequency):
    """Least-squares fundamental E/i with offset/drift terms and quality flags."""
    t, e, i = map(lambda a: np.asarray(a, dtype=float), (times, potentials, currents_na))
    if len(t) < 32 or not all(np.isfinite(a).all() for a in (t,e,i)):
        raise ValueError("Insufficient or nonfinite EIS samples")
    t = t-t[0]
    if np.any(np.diff(t) <= 0) or t[-1]*frequency < 2:
        raise ValueError("Need monotonic timestamps covering at least two cycles")
    phase = 2*np.pi*frequency*t
    design = np.column_stack((np.cos(phase), np.sin(phase), np.ones(len(t)), t/t[-1]))
    ec = np.linalg.lstsq(design,e,rcond=None)[0]
    ic = np.linalg.lstsq(design,i*1e-9,rcond=None)[0]
    current = complex(ic[0],-ic[1])
    if abs(current) < 1e-15:
        raise ValueError("Current fundamental below 1 fA; impedance is unresolved")
    z = complex(ec[0],-ec[1])/current
    er = float(np.sqrt(np.mean((e-design@ec)**2))/max(np.hypot(*ec[:2]),1e-15))
    ir = float(np.sqrt(np.mean((i*1e-9-design@ic)**2))/abs(current))
    warnings = []
    if max(er,ir) > .15: warnings.append("Large residual: noise, distortion, drift or timing mismatch")
    if max(np.diff(t))*frequency > .1: warnings.append("Sparse sampling / acquisition gap")
    if np.hypot(*ec[:2]) < .0005: warnings.append("Command fundamental is too small")
    return dict(frequency_hz=frequency, z_real_ohm=z.real, z_imag_ohm=z.imag,
                magnitude_ohm=abs(z), phase_deg=float(np.angle(z,deg=True)), samples=len(t),
                command_amplitude_v=float(np.hypot(*ec[:2])), current_amplitude_a=abs(current),
                potential_relative_residual=er, current_relative_residual=ir, warnings=warnings)


class EISSimulation:
    """Sample-clock synthetic RC reference; never used by the hardware backend."""
    def __init__(self, params, backend):
        self.params, self.backend = params, backend
        self.started = None
        self.next_t = None
        self.ends = np.cumsum([s[1] for s in params.it_steps()])
        self.steps = params.it_steps()
        self.rng = np.random.default_rng(23)

    def read_batch(self):
        """Advance simulated motion and return all due waveform samples."""
        b,p = self.backend,self.params
        sample = b.read_sample()
        if self.started is None:
            contact = sample.z_um >= (p.start_z_um+p.end_z_um)/2
            sample.current1_na = sample.current2_na = (.03 if contact else 0.) + self.rng.normal(0,.0001)
            return [sample]
        now = b.experiment_time()
        if self.next_t is None: self.next_t = self.started
        result = []
        while self.next_t <= now:
            relative = self.next_t-self.started
            k = min(int(np.searchsorted(self.ends,relative,side="right")),len(self.steps)-1)
            potential,_,stage = self.steps[k]
            index,phase = -1,""
            current = 0.
            if stage.count(":") == 2:
                _,idx,phase = stage.split(":"); index=int(idx)
                f=p.actual_frequency(p.frequencies_hz[index])
                # Ideal fundamental of a series-R / parallel-RC test circuit.
                z=1e8 + 5e8/(1+2j*np.pi*f*5e8*100e-12)
                j = k-1-sum(64*(p.settle_cycles+p.measure_cycles) for _ in range(index))
                dt=1/(64*f)
                theta=2*np.pi*((j%64)/64 + (relative-(self.ends[k]-self.steps[k][1]))*f)
                current=(p.amplitude_v*np.sinc(1/64)/abs(z))*math.sin(theta-np.pi/64-np.angle(z))*1e9
            result.append(replace(sample, elapsed_s=sample.elapsed_s-(now-self.next_t),
                voltage1_v=potential, current1_na=current+self.rng.normal(0,.0001),
                current2_na=current+self.rng.normal(0,.0001),
                eis_frequency_index=index, eis_phase=phase))
            self.next_t += b.settings.effective_period_s
        return result


class EISExperiment(ApproachITExperiment):
    """Reuse contact, timed FPGA steps and normal retract; fit one frequency at a time."""
    def __init__(self, backend, settings):
        self._abort_requested=False
        super().__init__(backend,settings)
        self.params=EISParameters()
        self.buffers={}

    def start(self, params):
        """Start the shared hardware sequence or a dedicated sample-clock simulation."""
        if self.backend.hardware_approach_cv_required and not getattr(getattr(self.backend,"_driver",None),"supports_eis",False):
            raise RuntimeError("Experimental EIS requires the native EIS-enabled driver.")
        self._abort_requested=False
        params.simulation_circuit = None if self.backend.hardware_approach_cv_required else params.simulation_circuit
        params.eis_results.clear(); self.buffers={}
        super().start(params)
        if not self._hardware:
            self.backend._eis_source=EISSimulation(params,self.backend)

    def _start_it(self):
        source=self.backend._eis_source
        source.started=self.backend.experiment_time()
        self._steps=[(self.params.dc_v,float(source.ends[-1]),"EIS")]
        super()._start_it()

    def ingest(self, samples):
        """Attach recording tags and accumulate only measurement cycles."""
        for s in samples:
            if self._hardware:
                stage=self.backend.hardware_program_context(s.line_number)[1]
                if stage.startswith("it:eis:") and stage.count(":")==3:
                    _,_,idx,phase=stage.split(":")
                    s.eis_frequency_index=int(idx);s.eis_phase=phase
            index=s.eis_frequency_index
            if index>=0 and s.eis_phase=="measure":
                self.buffers.setdefault(index,[]).append((s.elapsed_s,s.voltage1_v,
                    s.current1_na if self.params.current_channel=="Current 1" else s.current2_na))
        # Refit only completed frequency blocks, not on every GUI refresh.
        latest=max((s.eis_frequency_index for s in samples),default=-1)
        for index in list(self.buffers):
            if index<latest: self._fit(index)

    def _fit(self,index):
        rows=self.buffers.pop(index)
        try:
            fit=fit_impedance(*zip(*rows),self.params.actual_frequency(self.params.frequencies_hz[index]))
            fit["frequency_index"]=index
            self.params.eis_results.append(fit)
        except ValueError as exc:
            self.params.eis_results.append(dict(frequency_index=index,error=str(exc)))

    def tick_samples(self,samples):
        """Advance the shared lifecycle and finalize spectra before recording closes."""
        if self._abort_requested: return None
        update=super().tick_samples(samples)
        if update is not None:
            self.detail=self.detail.replace("I-t","EIS").replace("I–t","EIS")
            update.detail=self.detail
        if not self.active:
            if not self._hardware: self.backend._eis_source=None
        return update

    def abort(self):
        """Preserve partial fits while honoring existing stop semantics."""
        super().abort()
        self.finish_results()

    def finish_results(self):
        """Flush partial frequency fits and retire simulation before metadata closes."""
        for index in list(self.buffers): self._fit(index)
        if not self._hardware: self.backend._eis_source=None

    def request_abort(self):
        """Inhibit simulated waveform generation before the stop/drain barrier."""
        if not self._hardware: self.backend._eis_source=None
        self._abort_requested=True
