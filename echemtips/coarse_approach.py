"""Isolated piezo-first / Newport-Z-second commissioning supervisor.

The main application does not import or enable this experimental workflow.
Every coarse movement follows completed piezo withdrawal; physical displacement
and clearance remain operator-verified because the 8742 is open-loop.
"""
from dataclasses import dataclass, field
import math
import time

from .backends import SimulationBackend
from .experiments import ApproachExperiment, ExperimentState
from .host import ExecutionState
from .models import ApproachParameters


@dataclass
class CoarseApproachParameters:
    """Auditable run limits; no direction or step calibration is guessed."""
    approach: ApproachParameters = field(default_factory=lambda: ApproachParameters(
        start_z_um=10, end_z_um=80, approach_rate_um_s=15,
        retract_rate_um_s=15, feedback_threshold=.005, feedback_mode="magnitude",
        retract_after=True, settling_time_s=.1))
    direction: int = 0
    steps_per_move: int = 500
    rate_steps_s: int = 500
    max_coarse_steps: int = 2000
    max_attempts: int = 10
    max_duration_s: float = 300.
    settling_s: float = .5
    upper_um_per_step: float = 0.
    direction_verified: bool = False
    calibration_verified: bool = False
    clearance_verified: bool = False
    motor_identity: str = ""
    axis_mapping: dict = field(default_factory=lambda: dict(X=1,Y=2,Z=3))
    events: list = field(default_factory=list)

    def validate(self, settings):
        """Require a verified coarse direction, displacement bound and clearance."""
        errors = self.approach.validate(settings)
        a = self.approach
        if a.end_z_um <= a.start_z_um or not a.retract_after or a.x_um is not None or a.y_um is not None:
            errors.append("Use increasing piezo Z toward surface, retract-after enabled, and no XY commands.")
        if not self.direction_verified or self.direction not in {-1,1}:
            errors.append("Verify which motor sign brings the pipette closer to the sample.")
        if not self.calibration_verified or not math.isfinite(self.upper_um_per_step) or self.upper_um_per_step <= 0:
            errors.append("Enter and verify a conservative upper displacement per motor step under actual load.")
        if not self.clearance_verified:
            errors.append("Confirm coarse direction, mechanical travel and initial-Z clearance.")
        for key,low,high in (("steps_per_move",1,5000),("rate_steps_s",1,1000),
                             ("max_coarse_steps",1,100000),("max_attempts",1,100)):
            v = getattr(self,key)
            if not isinstance(v,int) or isinstance(v,bool) or not low <= v <= high:
                errors.append(f"{key}: enter an integer in {low}–{high}.")
        if self.steps_per_move > self.max_coarse_steps:
            errors.append("One motor move exceeds the cumulative step budget.")
        if self.steps_per_move*self.upper_um_per_step > .5*(a.end_z_um-a.start_z_um):
            errors.append("For this first tester, one coarse increment must be no more than half the piezo approach span.")
        if not math.isfinite(self.settling_s) or self.settling_s < .25:
            errors.append("Settling between motion and approach must be at least 0.25 s.")
        if not math.isfinite(self.max_duration_s) or self.max_duration_s <= 0:
            errors.append("Maximum run time must be positive and finite.")
        return errors


class CoarseSimulationBackend(SimulationBackend):
    """A surface initially beyond piezo reach, brought closer by the coarse stage."""

    def __init__(self, settings):
        super().__init__(settings)
        self.coarse_offset_um = 0.
        self.initial_surface_um = 90.

    def surface_z_at(self,x_um,y_um):
        """Return synthetic surface height after completed coarse advances."""
        return self.initial_surface_um-self.coarse_offset_um


class CoarseApproach:
    """Nonblocking state machine; a caller serializes I/O and records raw samples."""

    def __init__(self, backend, motor, settings, clock=time.monotonic):
        self.backend,self.motor,self.settings,self.clock = backend,motor,settings,clock
        self.phase = "idle"
        self.detail = "Connect, calibrate direction and displacement, then start."
        self.child = None
        self.params = None
        self.attempts = self.total_steps = 0
        self.contact_z = None
        self.event_sink = None

    @property
    def active(self):
        """Whether the supervisor is still executing a commissioned approach."""
        return self.phase not in {"idle","complete","exhausted","stopped","error"}

    def event(self,kind,**values):
        """Append a stage/motion audit record to the JSON parameters."""
        record=dict(event=kind,elapsed_s=self.clock()-self.started,**values)
        self.params.events.append(record)
        if self.event_sink is not None: self.event_sink(record)

    def start(self,params):
        """Authorize only piezo preparation; no coarse movement starts here."""
        if self.active:
            raise RuntimeError("An approach is already active.")
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        if not self.motor.motion_done():
            raise RuntimeError("Picomotor is not idle.")
        self.params=params
        params.events=[]
        self.started=self.clock()
        self.attempts=self.total_steps=0
        self.contact_z=None
        self.start_count=self.motor.position()
        self.child=None
        self.event("start",motor_count=self.start_count)
        if isinstance(self.backend,CoarseSimulationBackend):
            self.backend.configure_approach_scene(params.approach)
        initial_distance=abs(self.backend.commanded_position()['Z']-params.approach.start_z_um)
        self.backend.move("Z",params.approach.start_z_um,params.approach.retract_rate_um_s)
        self.phase="withdraw"
        self.deadline=self.clock()+initial_distance/params.approach.retract_rate_um_s+30
        self.detail="Moving piezo to initial Z before approach"

    def _withdrawn(self):
        """Check applied command and completed FPGA execution, not sensor offset."""
        if abs(self.backend.commanded_position()['Z']-self.params.approach.start_z_um) > .08:
            return False
        return self.backend.execution_status().state in {ExecutionState.IDLE,ExecutionState.COMPLETE}

    def _condition(self):
        self.backend.set_voltage(1,self.params.approach.approach_voltage_v)
        self.phase="condition"
        self.condition_start=self.clock()
        self.deadline=self.clock()+self.params.settling_s+10
        self.fresh=[]
        self.detail="Piezo stationary; waiting for settled approach potential/current"

    def tick(self,samples):
        """Advance at most one stage, with strict sequencing and finite budgets."""
        if not self.active:
            return
        if self.clock()-self.started > self.params.max_duration_s:
            raise TimeoutError("Run-time limit reached; stopping both devices without further coarse motion.")
        if self.clock() > self.deadline:
            raise TimeoutError(f"{self.phase} timed out; no automatic motor-command retry.")
        p,a=self.params,self.params.approach
        if self.phase=="withdraw":
            if self._withdrawn(): self._condition()
        elif self.phase=="condition":
            self.fresh.extend(samples)
            self.fresh=self.fresh[-3:]
            if samples and self.clock()-self.condition_start >= p.settling_s and len(self.fresh)==3:
                settled=all(math.isfinite(s.voltage1_v) and abs(s.voltage1_v-a.approach_voltage_v)<.002 and
                    math.isfinite(s.current1_na if a.feedback_channel=='Current 1' else s.current2_na) and
                    abs(s.current1_na if a.feedback_channel=='Current 1' else s.current2_na)<abs(a.feedback_threshold)
                    for s in self.fresh)
                if settled:
                    self.child=ApproachExperiment(self.backend,self.settings)
                    self.child.start(a)
                    self.attempts+=1
                    self.phase="approach"
                    self.deadline=self.clock()+2*(a.end_z_um-a.start_z_um)/min(a.approach_rate_um_s,a.retract_rate_um_s)+a.settling_time_s+60
                    self.event("piezo_attempt",attempt=self.attempts,coarse_steps=self.total_steps)
                else:
                    self.detail="Waiting: baseline above threshold or approach potential unsettled; coarse motion inhibited"
        elif self.phase=="approach":
            self.child.tick_samples(samples)
            self.detail=f"Attempt {self.attempts}: {self.child.detail}"
            if self.child.active: return
            if not self._withdrawn():
                raise RuntimeError("Approach ended without verified piezo withdrawal; coarse motion inhibited.")
            contact=(self.backend.confirmed_method_contact_z() if self.backend.hardware_approach_cv_required else self.child.contact_z)
            if self.child.state==ExperimentState.COMPLETE:
                if contact is None or not math.isfinite(contact):
                    raise RuntimeError("Completed approach has no confirmed contact coordinate.")
                self.contact_z=contact
                self.event("contact",attempt=self.attempts,contact_z_um=contact)
                self.phase="complete"
                self.detail=f"Contact at piezo Z {contact:.3f} µm; returned to initial Z"
                return
            # Only a normal no-contact endpoint is retryable. Operator stops and
            # arbitrary faults must never be interpreted as permission to descend.
            normal_no_contact=(self.child._no_contact if not self.backend.hardware_approach_cv_required
                else "without contact" in self.child.detail.lower() and "retracted" in self.child.detail.lower())
            if not normal_no_contact:
                raise RuntimeError("Approach aborted for a reason other than a completed no-contact withdrawal.")
            self.event("no_contact",attempt=self.attempts)
            if self.attempts>=p.max_attempts or self.total_steps+p.steps_per_move>p.max_coarse_steps:
                self.phase="exhausted"
                self.detail="No contact: attempt/coarse-step budget reached; piezo at initial Z"
                self.event("budget_reached")
                return
            self.move_start_count=self.motor.position()
            self.event("coarse_command",steps=p.direction*p.steps_per_move,motor_count=self.move_start_count)
            self.motor.move(p.direction*p.steps_per_move,p.rate_steps_s)
            self.total_steps+=p.steps_per_move
            self.phase="coarse"
            self.deadline=self.clock()+p.steps_per_move/p.rate_steps_s+15
            self.detail=f"Piezo withdrawn; moving Z picomotor by {p.direction*p.steps_per_move} steps"
        elif self.phase=="coarse":
            if self.motor.motion_done():
                self.motor.check_error()
                count=self.motor.position()
                if count-self.move_start_count != p.direction*p.steps_per_move:
                    raise RuntimeError("Motor pulse counter differs from requested move; inspect before continuing.")
                self.event("coarse_complete",motor_count=count,estimated_upper_displacement_um=p.steps_per_move*p.upper_um_per_step)
                self._condition()

    def stop(self,error=""):
        """Try both stops independently; never automatically retract after a fault."""
        failures=[]
        for name,call in (("Newport",self.motor.abort),("FPGA",self.backend.emergency_stop)):
            try: call()
            except Exception as exc: failures.append(f"{name}: {exc}")
        self.phase="error" if error or failures else "stopped"
        self.detail=error or "Stopped by operator; inspect and reconnect before a new hardware run"
        if failures: self.detail += "; stop unconfirmed: "+"; ".join(failures)
        if self.params is not None:
            try: self.event("stop",reason=self.detail)
            except Exception as exc: self.detail += f"; stop audit could not be saved: {exc}"
