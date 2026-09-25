"""Auditable spatial active learning, deliberately independent of FPGA control.

The Gaussian process proposes XY only. Clearance is a separate, conservative
plane envelope, conditional on the operator's bound on unresolved relief.
Neither model establishes collision freedom on an unknown sample.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields, replace
import json
import math
import os
from pathlib import Path
import time

import numpy as np
from scipy.linalg import cho_factor, cho_solve

from .experiments import ApproachCVExperiment, ExperimentState, ExperimentUpdate
from .models import ApproachCVParameters


@dataclass
class AdaptiveParameters(ApproachCVParameters):
    """LSV settings plus a bounded rectangular spatial search and travel policy."""
    waveform: str = "LSV"
    cycles: int = 1
    feedback_mode: str = "magnitude"
    x_min_um: float = 20.0
    x_max_um: float = 80.0
    y_min_um: float = 20.0
    y_max_um: float = 80.0
    minimum_spacing_um: float = 5.0
    survey: str = "Corners + center"
    strategy: str = "Balanced"
    objective_potential_v: float = 0.2
    objective_window_v: float = 0.02
    objective_channel: str = "Current 1"
    max_landings: int = 30
    max_duration_s: float = 3600.0
    xy_speed_um_s: float = 20.0
    clearance_um: float = 10.0
    relief_allowance_um: float = 5.0
    plane_tolerance_um: float = 3.0
    approve_each: bool = True
    region_confirmed: bool = False
    attempts: list = field(default_factory=list)

    def validate(self, settings):
        """Reject unsafe/ambiguous configurations before any output is changed."""
        errors = super().validate(settings)
        names = ("x_min_um", "x_max_um", "y_min_um", "y_max_um", "minimum_spacing_um",
                 "objective_potential_v", "objective_window_v", "max_duration_s", "xy_speed_um_s",
                 "clearance_um", "relief_allowance_um", "plane_tolerance_um")
        if not all(math.isfinite(getattr(self, n)) for n in names):
            return errors + ["Adaptive settings must be finite."]
        for axis in "xy":
            low, high = getattr(self, axis + "_min_um"), getattr(self, axis + "_max_um")
            if not 0 <= low < high <= getattr(settings, axis + "_range_um"):
                errors.append(f"{axis.upper()} region must lie within the calibrated piezo range.")
        if self.waveform != "LSV" or self.scan_rates_v_s is not None or not self.retract_after:
            errors.append("Adaptive acquisition requires one LSV and retraction after every landing.")
        if self.x_um is not None or self.y_um is not None:
            errors.append("Adaptive XY is controlled by the travel planner, not the child approach.")
        if self.end_z_um <= self.start_z_um:
            errors.append("This workflow requires increasing Z toward the surface.")
        for n in ("minimum_spacing_um", "objective_window_v", "max_duration_s", "xy_speed_um_s", "clearance_um", "plane_tolerance_um"):
            if getattr(self, n) <= 0:
                errors.append(f"{n} must be positive.")
        if self.relief_allowance_um < 0:
            errors.append("Unresolved relief allowance cannot be negative.")
        if self.strategy not in {"Balanced", "Hotspots", "Mapping"} or self.survey not in {"Corners + center", "3 × 3"}:
            errors.append("Unknown strategy or survey.")
        if self.objective_channel not in {"Current 1", "Current 2"}:
            errors.append("Select Current 1 or Current 2 for the objective.")
        lo, hi = sorted((self.cv_start_v, self.cv_vertex1_v))
        half = self.objective_window_v / 2
        if not lo <= self.objective_potential_v - half < self.objective_potential_v + half <= hi:
            errors.append("The entire objective window must lie within the LSV sweep.")
        if not isinstance(self.max_landings, int) or not len(self.survey_points()) <= self.max_landings <= 500:
            errors.append("Landing budget must cover the survey and cannot exceed 500 in this version.")
        pts = self.survey_points()
        if len(pts) > 1 and any(np.linalg.norm(a - b) < self.minimum_spacing_um for i, a in enumerate(pts) for b in pts[i+1:]):
            errors.append("Survey points are closer than the minimum landing separation.")
        if not self.region_confirmed:
            errors.append("Confirm that the entire selected region and entry path are clear at initial Z.")
        if not settings.auto_save:
            errors.append("Enable automatic recording for the adaptive experiment audit trail.")
        return errors

    def survey_points(self):
        """Return a deterministic conservative survey, with no repeated center."""
        x0, x1, y0, y1 = self.x_min_um, self.x_max_um, self.y_min_um, self.y_max_um
        if self.survey == "3 × 3":
            return np.array([(x, y) for y in np.linspace(y0, y1, 3) for x in np.linspace(x0, x1, 3)])
        return np.array([(x0,y0), (x1,y0), (x1,y1), (x0,y1), ((x0+x1)/2,(y0+y1)/2)])

    def candidates(self):
        """Bound model cost with a 31-by-31 grid in physical coordinates."""
        return np.array([(x,y) for y in np.linspace(self.y_min_um,self.y_max_um,31)
                         for x in np.linspace(self.x_min_um,self.x_max_um,31)])


@dataclass
class TiltEnvelope:
    """Commanded-Z plane plus residual allowance; never inferred from sensor Z."""
    coefficients: list
    origin: list
    residual_um: float

    @classmethod
    def fit(cls, attempts, tolerance):
        """Require four non-collinear confirmed contacts and bounded residuals."""
        usable = [a for a in attempts if a.get("contact_z_um") is not None]
        if len(usable) < 4:
            raise ValueError("At least four confirmed commanded-Z contacts are required for the plane.")
        xy = np.array([a["xy"] for a in usable]); origin = xy.mean(axis=0)
        design = np.c_[xy-origin, np.ones(len(xy))]
        if np.linalg.matrix_rank(design) < 3:
            raise ValueError("Survey contacts are collinear.")
        z = np.array([a["contact_z_um"] for a in usable])
        coef = np.linalg.lstsq(design,z,rcond=None)[0]
        residual = float(np.max(np.abs(design @ coef-z)))
        if residual > tolerance:
            raise ValueError(f"Survey is not sufficiently planar: residual {residual:.3g} µm exceeds {tolerance:g} µm.")
        return cls(coef.tolist(),origin.tolist(),residual)

    def height(self, xy):
        """Predict commanded contact Z; smaller Z is farther from the surface."""
        return np.asarray(xy) @ np.array(self.coefficients[:2]) + self.coefficients[2] - np.dot(self.origin,self.coefficients[:2])

    def travel_z(self, source, target, params):
        """Check all endpoints of the X-then-Y path against the plane envelope."""
        path = [source, [target[0],source[1]], target]
        # Use the configured tolerance, not just a deceptively small fit residual.
        z = float(np.min(self.height(path))) - params.clearance_um - params.relief_allowance_um - params.plane_tolerance_um
        if z < 0:
            raise ValueError("Insufficient Z travel for clearance and relief allowance; do not reduce clearance automatically.")
        return min(z,params.end_z_um)


def propose(params, attempts):
    """Fit a small spatial GP off-thread; return predictions and one legal XY.

    Marginal likelihood selects a length scale from a bounded grid. Mapping uses
    posterior uncertainty; Hotspots uses UCB; Balanced alternates the two. All
    attempted locations (including failed ones) reserve their wetting radius.
    """
    candidates = params.candidates()
    old = np.array([a["xy"] for a in attempts])
    legal = np.ones(len(candidates),dtype=bool)
    if len(old):
        legal &= np.min(np.linalg.norm(candidates[:,None]-old[None,:],axis=2),axis=1) >= params.minimum_spacing_um
    if not legal.any():
        return {"xy": None, "reason": "No unvisited candidates satisfy minimum separation."}
    valid = [a for a in attempts if a.get("valid")]
    if len(valid) < 3:
        raise ValueError("Fewer than three usable LSV objectives; cannot train spatial model.")
    span = np.array([params.x_max_um-params.x_min_um,params.y_max_um-params.y_min_um])
    x = np.array([a["xy"] for a in valid])/span
    query = candidates/span
    y = np.array([a["objective_na"] for a in valid])
    center, scale = float(y.mean()), max(float(y.std()),1e-6)
    normalized = (y-center)/scale
    best = None
    distance = np.sum((x[:,None]-x[None,:])**2,axis=2)
    for length in (.08,.15,.3,.6,1.2):
        covariance = np.exp(-distance/(2*length**2)) + np.eye(len(x))*.03
        factor = cho_factor(covariance,lower=True)
        alpha = cho_solve(factor,normalized)
        loss = float(normalized @ alpha / 2 + np.log(np.diag(factor[0])).sum())
        if best is None or loss < best[0]: best = (loss,length,factor,alpha)
    _, length, factor, alpha = best
    cross = np.exp(-np.sum((query[:,None]-x[None,:])**2,axis=2)/(2*length**2))
    mean = center+scale*(cross @ alpha)
    sd = scale*np.sqrt(np.maximum(0,1-np.sum(cross*cho_solve(factor,cross.T).T,axis=1)))
    mapping = params.strategy == "Mapping" or (params.strategy == "Balanced" and len(attempts)%2 == 0)
    scores = sd if mapping else mean+2*sd
    selected = int(np.argmax(np.where(legal,scores,-np.inf)))
    return {"xy": candidates[selected].tolist(), "reason": "Highest uncertainty" if mapping else "Highest upper confidence bound (mean + 2σ)",
            "predicted_na": float(mean[selected]), "uncertainty_na": float(sd[selected]),
            "length_scale": length, "candidate_xy": candidates.tolist(),
            "mean_na": mean.tolist(), "sd_na": sd.tolist()}


def score_lsv(potential, current, params, sensitivity):
    """Robust signed-window median, then magnitude; reject incomplete/clipped data."""
    e, i = np.asarray(potential),np.asarray(current)
    if len(e) < 3 or not np.isfinite(e).all() or not np.isfinite(i).all():
        return {"valid":False,"reason":"Missing/nonfinite sweep data"}
    tolerance = max(.002,abs(params.cv_vertex1_v-params.cv_start_v)*.02)
    if min(abs(e-params.cv_start_v)) > tolerance or min(abs(e-params.cv_vertex1_v)) > tolerance:
        return {"valid":False,"reason":"Incomplete potential sweep"}
    if np.max(np.abs(i*sensitivity)) >= 9.8:
        return {"valid":False,"reason":"Current input near ADC clipping"}
    selected = i[np.abs(e-params.objective_potential_v) <= params.objective_window_v/2]
    if len(selected) < 3:
        return {"valid":False,"reason":"Fewer than three samples in objective window; widen it or acquire faster"}
    median = float(np.median(selected))
    mad = float(np.median(np.abs(selected-median)))
    return {"valid":True,"reason":"Complete contact-gated LSV", "objective_na":abs(median),
            "signed_current_na":median,"window_mad_na":mad,
            "quality_warning": "Noisy objective window" if mad > max(abs(median),.005) else ""}


class AdaptiveExperiment:
    """Sequential landing supervisor; optimization has no access to the backend."""
    def __init__(self, backend, settings):
        self.backend,self.settings = backend,settings
        self.params = AdaptiveParameters()
        self.state = ExperimentState.IDLE
        self.detail,self.progress = "Configure a region and conservative travel height.",0.0
        self.phase = "idle"
        self.child = None
        self.proposal = None
        self.model = None
        self.plane = None
        self.journal_path = None
        self._pool = None
        self._future = None
        self._stopping = False
        self._line_attempt = {}

    @property
    def active(self):
        """Own the session while planning, awaiting approval, or moving."""
        return self.state not in {ExperimentState.IDLE,ExperimentState.COMPLETE,ExperimentState.ABORTED}

    def configure_recording(self, csv_path):
        """Put the durable decision journal beside the full-rate recording."""
        self.journal_path = Path(csv_path).with_suffix(".decisions.jsonl")

    def _log(self, event, **values):
        payload = {"event":event,"elapsed_s":time.monotonic()-self._started,**values}
        with self.journal_path.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(payload,allow_nan=False)+"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def start(self, params):
        """Validate, reserve recording, and propose the first conservative survey landing."""
        errors = params.validate(self.settings)
        if errors: raise ValueError("\n".join(errors))
        if self.journal_path is None: raise ValueError("Adaptive runs require a disk recording.")
        if not callable(getattr(self.backend,"commanded_position",None)):
            raise ValueError("Backend cannot verify commanded travel position.")
        if self.backend.hardware_approach_cv_required and not getattr(self.backend,"adaptive_contact_available",False):
            raise ValueError("This driver cannot expose verified commanded contact Z for adaptive travel.")
        self.close()
        if not self.backend.hardware_approach_cv_required:
            self.backend.adaptive_scene = True
        self.params = replace(params,attempts=[])
        self._started = time.monotonic()
        self._stopping = False
        self._line_attempt = {}
        self.child,self.plane,self.model = None,None,None
        self._pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix="adaptive-model")
        self._future = None
        self._log("start",parameters=asdict(self.params),coordinate_system="commanded AO position; decreasing Z retracts")
        self.state = ExperimentState.PREPOSITION
        self._next_survey()

    def _next_survey(self):
        self.proposal = {"xy":self.params.survey_points()[len(self.params.attempts)].tolist(),"reason":"Conservative tilt survey"}
        self._present()

    def _present(self):
        self._log("proposal",**{k:v for k,v in self.proposal.items() if k not in {"candidate_xy","mean_na","sd_na"}})
        self.phase = "approval" if self.params.approve_each else "ready"
        self.detail = f"{self.proposal['reason']} · XY {self.proposal['xy']} µm" + (" · approve next landing" if self.phase == "approval" else "")

    def approve(self):
        """Approve a pending location or the conditional tilt envelope, never move here."""
        if self.phase == "tilt_approval":
            self._log("tilt_approved",plane=asdict(self.plane))
            self._plan()
        elif self.phase == "approval":
            self._log("location_approved",xy=self.proposal["xy"])
            self.phase = "ready"

    def request_abort(self):
        """Inhibit all new commands before application-level stop/drain begins."""
        self._stopping = True

    def close(self):
        """Cancel queued model work without blocking UI or touching hardware."""
        if self._pool is not None:
            self._pool.shutdown(wait=False,cancel_futures=True)
            self._pool = None
        if not self.backend.hardware_approach_cv_required:
            self.backend.adaptive_scene = False
            self.backend.adaptive_pixel = -1

    def _plan(self):
        self.phase = "model"
        self.state = ExperimentState.PREPOSITION
        self.detail = "Fitting spatial model; probe retracted"
        self._future = self._pool.submit(propose,replace(self.params),[dict(a) for a in self.params.attempts])
        self._model_started = time.monotonic()

    def _move(self, axis, target, speed):
        self.backend.move(axis,float(target),speed)
        self._motion_axis,self._motion_target = axis,float(target)
        self._motion_deadline = time.monotonic()+abs(self.backend.commanded_position()[axis]-target)/speed+60

    def _motion_done(self):
        status = self.backend.execution_status()
        if status.state.value in {"error","aborted"}:
            raise RuntimeError("Adaptive travel failed: "+status.detail)
        if time.monotonic()>self._motion_deadline:
            raise RuntimeError("Adaptive travel timed out; stop and inspect the instrument.")
        if self.backend.hardware_approach_cv_required and status.state.value not in {"idle","complete"}: return False
        return abs(self.backend.commanded_position()[self._motion_axis]-self._motion_target)<.08

    def _finish(self, reason, *, success=True):
        self._return_success = success
        self.detail = reason+" · returning to initial Z"
        self._log("finishing",reason=reason)
        self._move("Z",self.params.start_z_um,max(10,self.params.approach_rate_um_s))
        self.phase = "return"
        self.state = ExperimentState.RETRACTING

    def _launch(self):
        p = self.params
        xy = np.asarray(self.proposal["xy"],dtype=float)
        if not np.isfinite(xy).all() or not (p.x_min_um<=xy[0]<=p.x_max_um and p.y_min_um<=xy[1]<=p.y_max_um):
            raise ValueError("Planner proposed an out-of-region position.")
        if any(np.linalg.norm(xy-a["xy"]) < p.minimum_spacing_um-1e-8 for a in p.attempts):
            raise ValueError("Planner proposed a previously wetted/excluded location.")
        position = self.backend.commanded_position()
        source = [position["X"],position["Y"]]
        self._travel_z = p.start_z_um if self.plane is None else self.plane.travel_z(source,xy,p)
        estimate = (sum(abs(xy-np.array(source)))/p.xy_speed_um_s + abs(position["Z"]-self._travel_z)/max(10,p.approach_rate_um_s)
                    + (p.end_z_um-self._travel_z)/p.approach_rate_um_s
                    + abs(p.cv_vertex1_v-p.cv_start_v)/p.cv_scan_rate_v_s + p.settling_time_s
                    + 2*p.end_z_um/max(10,p.approach_rate_um_s)+5)
        if len(p.attempts)>=p.max_landings or time.monotonic()-self._started+estimate>p.max_duration_s:
            self._finish("Landing/time budget reached; no new approach started")
            return
        self._attempt = {"scan_pixel":len(p.attempts),"xy":xy.tolist(),"valid":False,"reason":"incomplete",
                         "travel_z_um":self._travel_z,"selection_reason":self.proposal["reason"]}
        self._log("landing_reserved",**self._attempt)
        p.attempts.append(self._attempt)
        self._e,self._i = [],[]
        self.child = None
        if not self.backend.hardware_approach_cv_required:
            self.backend.adaptive_pixel = -1
        self._move("Z",self._travel_z,max(10,p.approach_rate_um_s))
        self.phase = "travel_z"
        self.state = ExperimentState.PREPOSITION
        self.detail = f"Landing {len(p.attempts)}/{p.max_landings} · retract before XY travel"

    def _landing_done(self):
        p = self.params
        contact = self.backend.confirmed_contact_z() if self.backend.hardware_approach_cv_required else self._attempt.get("contact_z_um")
        if contact is not None and math.isfinite(contact): self._attempt["contact_z_um"] = contact
        if self.child.state == ExperimentState.ABORTED:
            self._attempt.update(valid=False,reason=self.child.detail)
            self._log("landing_result",**self._attempt)
            # Do not assume a native no-contact cancellation is reusable.
            self.state = ExperimentState.ABORTED
            self.detail = "No confirmed contact; scan stopped. "+self.child.detail
            self.close()
            return
        channel = 1 if p.objective_channel == "Current 1" else 2
        self._attempt.update(score_lsv(self._e,self._i,p,getattr(self.settings,f"current{channel}_v_per_na")))
        if contact is None: self._attempt.update(valid=False,reason="Missing commanded contact coordinate")
        self._log("landing_result",**self._attempt)
        self.child = None
        if not self._attempt["valid"] or self._attempt.get("quality_warning"):
            self._finish("Quality check requires operator review: "+(self._attempt.get("quality_warning") or self._attempt["reason"]),success=False)
            return
        if contact - p.start_z_um < p.clearance_um+p.relief_allowance_um:
            self._finish("Initial Z does not provide the requested clearance; inspect the sample",success=False)
            return
        if self.plane is not None and abs(contact-float(self.plane.height(self._attempt["xy"])))>p.plane_tolerance_um:
            self._finish("New contact lies outside the approved plane tolerance; survey must be revised",success=False)
            return
        if len(p.attempts)>=p.max_landings:
            self._finish("Landing budget reached")
        elif len(p.attempts)<len(p.survey_points()):
            self._next_survey()
        elif self.plane is None:
            self.plane = TiltEnvelope.fit(p.attempts,p.plane_tolerance_um)
            self._log("tilt_fit",plane=asdict(self.plane))
            self.phase = "tilt_approval"
            self.state = ExperimentState.PREPOSITION
            a,b,_ = self.plane.coefficients
            self.detail = f"Approve tilt: dZ/dX={a:.4g}, dZ/dY={b:.4g}; max residual {self.plane.residual_um:.3g} µm. Unmeasured relief remains an assumption."
        else: self._plan()

    def tick_samples(self,samples):
        """Tag every raw sample, then advance at most one supervisor transition."""
        p = self.params
        for sample in samples:
            if self.child is not None and self.phase == "landing":
                if self.backend.hardware_approach_cv_required:
                    context = self.backend.hardware_approach_context(sample.line_number)
                    sample.scan_pixel = self._attempt["scan_pixel"] if context else self._line_attempt.get(sample.line_number,-1)
                    is_lsv = context.startswith("cv")
                    if context: self._line_attempt[sample.line_number] = sample.scan_pixel
                else: is_lsv = sample.cv_rate_index == 0 and sample.scan_pixel == self._attempt["scan_pixel"]
                if is_lsv:
                    self._e.append(sample.voltage1_v)
                    self._i.append(sample.current1_na if p.objective_channel == "Current 1" else sample.current2_na)
            elif self.backend.hardware_approach_cv_required:
                sample.scan_pixel = self._line_attempt.get(sample.line_number,-1)
        if not self.active or self._stopping: return self._update()
        if getattr(self.backend,"operator_paused",False): return self._update()
        if self.phase in {"approval","tilt_approval","ready","model"} and time.monotonic()-self._started >= p.max_duration_s:
            self._finish("Time budget reached")
        elif self.phase == "ready": self._launch()
        elif self.phase == "model":
            if self._future.done():
                self.model = self._future.result()
                if self.model["xy"] is None: self._finish(self.model["reason"])
                else:
                    self.proposal = self.model
                    self._present()
            elif time.monotonic()-self._model_started>30: raise RuntimeError("Spatial model timed out; probe remains retracted.")
        elif self.phase in {"travel_z","travel_x","travel_y","return"} and self._motion_done():
            if self.phase == "return":
                self.phase = "done"
                self.state = ExperimentState.COMPLETE if self._return_success else ExperimentState.ABORTED
                self.progress = 1
                self.detail = self.detail.replace("returning to initial Z","at initial Z")
                self.close()
            elif self.phase == "travel_z":
                self._move("X",self._attempt["xy"][0],p.xy_speed_um_s); self.phase = "travel_x"
            elif self.phase == "travel_x":
                self._move("Y",self._attempt["xy"][1],p.xy_speed_um_s); self.phase = "travel_y"
            else:
                child_params = ApproachCVParameters(**{f.name:getattr(p,f.name) for f in fields(ApproachCVParameters)})
                child_params.start_z_um = self._travel_z
                self.child = ApproachCVExperiment(self.backend,self.settings)
                with self.backend.io_lock:
                    self.child.start(child_params)
                    if not self.backend.hardware_approach_cv_required:
                        self.backend.adaptive_pixel = self._attempt["scan_pixel"]
                self.phase = "landing"
        elif self.phase == "landing" and samples:
            before = self.child.state
            update = self.child.tick(samples[-1])
            if not self.backend.hardware_approach_cv_required and before == ExperimentState.APPROACHING and self.child.state in {ExperimentState.SETTLING,ExperimentState.CV}:
                self._attempt["contact_z_um"] = samples[-1].commanded_z_um
            self.state = update.state if self.child.active else ExperimentState.PREPOSITION
            self.detail = f"Landing {len(p.attempts)}/{p.max_landings} · {update.detail}"
            if not self.child.active: self._landing_done()
        self.progress = len(p.attempts)/p.max_landings if self.active else self.progress
        return self._update()

    def _update(self):
        return ExperimentUpdate(self.state,self.detail,self.progress)

    def finish_report(self,csv_path,status):
        """Finalize a standalone audit report; raw data and decisions remain separate."""
        if self.journal_path is None: return
        self._log("finished",status=status,detail=self.detail)
        path = Path(csv_path).with_suffix(".report.md")
        lines = ["# Adaptive hopping + LSV", "",f"Status: {status}",f"Outcome: {self.detail}",
                 "", "Objective: absolute value of the median signed current in the configured potential window.",
                 "Coordinates and tilt use commanded AO positions, not measured sensor positions.",
                 "The plane and relief allowance do not certify unmeasured terrain safe.",
                 "", "| Landing | X / µm | Y / µm | Contact Z / µm | Objective / nA | Result / reason |", "|---|---|---|---|---|---|"]
        for a in self.params.attempts:
            lines.append(f"| {a['scan_pixel']} | {a['xy'][0]:g} | {a['xy'][1]:g} | {a.get('contact_z_um','—')} | {a.get('objective_na','—')} | {a['reason']}; {a['selection_reason']} |")
        with path.open("w",encoding="utf-8") as stream: stream.write("\n".join(lines)+"\n")
        self.close()
