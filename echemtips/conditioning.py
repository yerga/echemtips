"""Optional measurement conditioning shared by contact-based experiments."""
from dataclasses import dataclass
from functools import wraps
import math

HOLD_FIELDS = ('pre_hold_enabled','pre_hold_v','pre_hold_s','post_hold_enabled','post_hold_v','post_hold_s')
PHASES = {0: 'other', 1: 'pre_hold', 2: 'measurement', 3: 'post_hold'}


@dataclass(kw_only=True)
class ConditioningParameters:
    """Disabled defaults preserve old programs and positional constructors."""
    pre_hold_enabled: bool = False
    pre_hold_v: float = 0.
    pre_hold_s: float = 1.
    post_hold_enabled: bool = False
    post_hold_v: float = 0.
    post_hold_s: float = 1.

    def conditioning_steps(self, which):
        """Return zero or one physical potential-duration step."""
        return [(getattr(self,which+'_hold_v'),getattr(self,which+'_hold_s'),which+'_hold')] if getattr(self,which+'_hold_enabled') else []

    def conditioning_duration(self):
        """Return additional programmed time in seconds per landing."""
        return sum(t for side in ('pre','post') for _,t,_ in self.conditioning_steps(side))

    def conditioning_frames(self):
        """Count existing signed-I16 timer frames needed for enabled holds."""
        return sum(max(1,math.ceil(t*1e6/32767)) for side in ('pre','post') for _,t,_ in self.conditioning_steps(side))

    def validate_conditioning(self, settings):
        """Validate enabled holds before commands, including AO scaling limits."""
        errors=[]
        for side in ('pre','post'):
            for e,t,_ in self.conditioning_steps(side):
                if not math.isfinite(e) or abs(e)>10 or (settings.mode=='NI FPGA' and abs(e*settings.command_voltage_ratio)>10):
                    errors.append(f'{side.title()}-hold potential exceeds the configured output range.')
                if not math.isfinite(t) or not 0<t<=300:
                    errors.append(f'{side.title()}-hold duration must be positive and at most 300 s.')
        return errors


def phase_code(context):
    """Translate exact FPGA contexts, never a polled UI state, into saved tags."""
    context=context.removeprefix('it:')
    if context=='pre_hold': return 1
    if context=='post_hold': return 3
    if context in ('cv','initial','pulse','return') or context.startswith('cv:'): return 2
    return 0


def set_phase(backend, phase):
    """Tag simulator samples at acquisition time, not when a UI batch arrives."""
    setter=getattr(backend,'set_measurement_phase',None)
    if setter: setter(phase)


def conditioned(side):
    """Bracket a simulated CV block with a pause-aware hold and continuation."""
    def decorate(function):
        """Wrap a simulator measurement or retraction transition."""
        @wraps(function)
        def begin(self):
            """Start conditioning, deferring the transition until its deadline."""
            params=self.params.for_point(self.point_index) if hasattr(self.params,'for_point') else self.params
            def resume():
                """Restore sample tagging and enter the deferred transition."""
                set_phase(self.backend,2 if side=='pre' else 0)
                self._last_tick=self.backend.experiment_time()
                function(self)
            steps=params.conditioning_steps(side)
            if not steps:
                resume(); return
            e,t,_=steps[0]
            self.backend.set_voltage(1,e)
            if hasattr(self.backend,'simulated_waveform'): self.backend.simulated_waveform('step')
            set_phase(self.backend,1 if side=='pre' else 3)
            self._conditioning_pending=(self.backend.experiment_time()+t,resume)
            self.state=type(self.state).SETTLING
            self.detail=f'{side.title()}-measurement hold: {e:g} V for {t:g} s'
        return begin
    return decorate


def tick_conditioning(experiment):
    """Return true when this sample belongs to a hold, including its boundary."""
    if not experiment.active:
        experiment._conditioning_pending=None
        return False
    pending=getattr(experiment,'_conditioning_pending',None)
    if pending is None: return False
    deadline,resume=pending
    if experiment.backend.experiment_time()>=deadline:
        experiment._conditioning_pending=None
        resume()
    return True
