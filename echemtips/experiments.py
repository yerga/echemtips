from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from .backends import InstrumentBackend
from .models import (
    AppSettings, ApproachCVParameters, ApproachITParameters, ApproachParameters,
    CVParameters, Sample, ScanHoppingCVParameters, ScanHoppingITParameters,
)


class ExperimentState(str, Enum):
    IDLE = "Ready"
    PREPOSITION = "Moving to start"
    APPROACHING = "Approaching surface"
    CONTACT = "Contact detected"
    CV = "Running CV"
    IT = "Running I-t"
    RETRACTING = "Retracting"
    COMPLETE = "Complete"
    ABORTED = "Stopped"


@dataclass(slots=True)
class ExperimentUpdate:
    state: ExperimentState
    detail: str
    progress: float


class ApproachCVExperiment:
    def __init__(self, backend: InstrumentBackend, settings: AppSettings) -> None:
        self.backend = backend
        self.settings = settings
        self.params = ApproachCVParameters()
        self.state = ExperimentState.IDLE
        self.detail = "Configure the approach and CV, then start."
        self.progress = 0.0
        self._last_tick = time.monotonic()
        self._cv_voltage = 0.0
        self._segments: list[float] = []
        self._segment_index = 0
        self._hardware_sequence = False
        self._no_contact_after_retract = False

    @property
    def active(self) -> bool:
        return self.state in {
            ExperimentState.PREPOSITION,
            ExperimentState.APPROACHING,
            ExperimentState.CONTACT,
            ExperimentState.CV,
            ExperimentState.RETRACTING,
        }

    def start(self, params: ApproachCVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        if self.backend.hardware_approach_cv_required and not self.backend.approach_cv_available:
            raise RuntimeError(
                "This FPGA connection cannot run Approach + CV because its FPGA waypoint sequence interface is unavailable."
            )
        if not self.backend.hardware_approach_cv_required and not self.backend.motion_available:
            raise RuntimeError("This backend cannot move the probe.")
        self.params = params
        self._last_tick = time.monotonic()
        self._segments = []
        self._segment_index = 0
        self._hardware_sequence = self.backend.hardware_approach_cv_required
        self._no_contact_after_retract = False
        if self._hardware_sequence:
            self.backend.start_hardware_approach_cv(params)
            self.state = ExperimentState.APPROACHING
            self.detail = "FPGA waypoint sequence started"
            self.progress = 0.0
            return
        self.backend.set_voltage(1, params.approach_voltage_v)
        self.backend.move("Z", params.start_z_um, max(10.0, params.approach_rate_um_s))
        self.state = ExperimentState.PREPOSITION
        self.detail = f"Moving Z to {params.start_z_um:.2f} um"
        self.progress = 0.02

    def abort(self) -> None:
        self.backend.stop_motion()
        self.state = ExperimentState.ABORTED
        self.detail = "Experiment stopped by operator"

    def _feedback_value(self, sample: Sample) -> float:
        return {
            "Current 1": sample.current1_na,
            "Current 2": sample.current2_na,
        }[self.params.feedback_channel]

    def _begin_cv(self) -> None:
        p = self.params
        self.backend.stop_motion()
        self.backend.set_voltage(1, p.cv_start_v)
        self._cv_voltage = p.cv_start_v
        self._segments = []
        for _ in range(p.cycles):
            self._segments.extend((p.cv_vertex1_v, p.cv_vertex2_v, p.cv_start_v))
        self._segment_index = 0
        self.state = ExperimentState.CV
        self.detail = f"CV cycle 1 of {p.cycles}"

    def tick(self, sample: Sample) -> ExperimentUpdate:
        if self._hardware_sequence and self.active:
            update = self.backend.hardware_approach_cv_status()
            state_by_stage = {
                "preposition": ExperimentState.PREPOSITION,
                "approaching": ExperimentState.APPROACHING,
                "contact": ExperimentState.CONTACT,
                "cv": ExperimentState.CV,
                "retracting": ExperimentState.RETRACTING,
                "complete": ExperimentState.COMPLETE,
                "aborted": ExperimentState.ABORTED,
            }
            self.state = state_by_stage[update.stage]
            self.detail = update.detail
            self.progress = update.progress
            return ExperimentUpdate(self.state, self.detail, self.progress)

        now = time.monotonic()
        dt = min(now - self._last_tick, 0.25)
        self._last_tick = now
        p = self.params

        if self.state == ExperimentState.PREPOSITION and abs(sample.z_um - p.start_z_um) < 0.08:
            self.backend.move("Z", p.end_z_um, p.approach_rate_um_s)
            self.state = ExperimentState.APPROACHING
            self.detail = f"Watching {p.feedback_channel} for the contact threshold"

        if self.state == ExperimentState.APPROACHING:
            value = self._feedback_value(sample)
            hit = value >= p.feedback_threshold_na if p.greater_than else value <= p.feedback_threshold_na
            travel = abs(p.end_z_um - p.start_z_um) or 1.0
            self.progress = min(0.42, 0.05 + 0.35 * abs(sample.z_um - p.start_z_um) / travel)
            endpoint = abs(sample.z_um - p.end_z_um) < 0.08
            if hit:
                self.state = ExperimentState.CONTACT
                self.detail = f"Feedback threshold reached at Z = {sample.z_um:.3f} um"
                self._begin_cv()
                # This contact sample was acquired at the approach potential.
                # The next sample is the first one measured at the CV start.
                return ExperimentUpdate(self.state, self.detail, self.progress)
            elif endpoint:
                self.backend.stop_motion()
                self.backend.move("Z", p.start_z_um, max(10.0, p.approach_rate_um_s))
                self._no_contact_after_retract = True
                self.state = ExperimentState.RETRACTING
                self.detail = "End Z reached without contact; retracting without running CV"
                self.progress = 0.9

        if self.state == ExperimentState.CV and self._segments:
            target = self._segments[self._segment_index]
            delta = target - self._cv_voltage
            step = p.cv_scan_rate_v_s * dt
            if abs(delta) <= step:
                self._cv_voltage = target
                self._segment_index += 1
                if self._segment_index >= len(self._segments):
                    if p.retract_after:
                        self.backend.move("Z", p.start_z_um, max(10.0, p.approach_rate_um_s))
                        self.state = ExperimentState.RETRACTING
                        self.detail = "CV complete; retracting to start Z"
                    else:
                        self.state = ExperimentState.COMPLETE
                        self.detail = "Approach and CV complete"
                    self.progress = 0.96 if p.retract_after else 1.0
                else:
                    cycle = min(p.cycles, self._segment_index // 3 + 1)
                    self.detail = f"CV cycle {cycle} of {p.cycles}"
            else:
                self._cv_voltage += step if delta > 0 else -step
            self.backend.set_voltage(1, self._cv_voltage)
            self.progress = 0.42 + 0.52 * self._segment_index / max(1, len(self._segments))

        if self.state == ExperimentState.RETRACTING and abs(sample.z_um - p.start_z_um) < 0.08:
            if self._no_contact_after_retract:
                self.state = ExperimentState.ABORTED
                self.detail = "No contact detected before End Z; CV was not run"
            else:
                self.state = ExperimentState.COMPLETE
                self.detail = "Approach, CV, and retract complete"
                self.progress = 1.0

        return ExperimentUpdate(self.state, self.detail, self.progress)


class ScanHoppingCVExperiment:
    """One feedback approach and one complete CV at each point of an XY grid."""

    def __init__(self, backend: InstrumentBackend, settings: AppSettings) -> None:
        self.backend = backend
        self.settings = settings
        self.params = ScanHoppingCVParameters()
        self.state = ExperimentState.IDLE
        self.detail = "Configure a hopping grid, approach, and CV."
        self.progress = 0.0
        self.point_index = -1
        self.contact_z: dict[tuple[int, int], float] = {}
        self.current_at_potential: dict[tuple[int, int], float] = {}
        self.contact_detected: dict[tuple[int, int], bool] = {}
        self.approach_trace: list[tuple[float, float, float]] = []
        self._grid: list[tuple[int, int, float, float]] = []
        self._last_tick = time.monotonic()
        self._segments: list[float] = []
        self._segment_index = 0
        self._cv_voltage = 0.0
        self._current_candidate: tuple[float, float] | None = None
        self._last_approach_z: float | None = None
        self._hardware = False
        self._hardware_point = -1
        self._hardware_stage = ""
        self._positioning_z = False
        self._no_contact_after_retract = False
        self._hardware_contact_seen: set[int] = set()

    @property
    def active(self) -> bool:
        return self.state in {
            ExperimentState.PREPOSITION,
            ExperimentState.APPROACHING,
            ExperimentState.CONTACT,
            ExperimentState.CV,
            ExperimentState.RETRACTING,
        }

    def start(self, params: ScanHoppingCVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        if self.backend.hardware_approach_cv_required and not self.backend.scan_hopping_cv_available:
            raise RuntimeError("This FPGA connection does not expose the Scan Hopping + CV waypoint interface.")
        self.params = params
        self._grid = params.grid()
        self.contact_z.clear()
        self.current_at_potential.clear()
        self.contact_detected.clear()
        self.approach_trace.clear()
        self.point_index = 0
        self._hardware_point = -1
        self._hardware_stage = ""
        self._current_candidate = None
        self._last_approach_z = None
        self._no_contact_after_retract = False
        self._hardware_contact_seen.clear()
        self._hardware = self.backend.hardware_approach_cv_required
        if self._hardware:
            self.backend.start_hardware_scan_hopping_cv(params)
            self.state = ExperimentState.PREPOSITION
            self.detail = f"FPGA scan started · {params.point_count} points"
        else:
            self._start_simulated_point()

    def abort(self) -> None:
        self.backend.stop_motion()
        self.state = ExperimentState.ABORTED
        self.detail = "Scan stopped by operator"

    def _point_key(self, index: int | None = None) -> tuple[int, int]:
        row, column, _x, _y = self._grid[self.point_index if index is None else index]
        return row, column

    def _tag(self, sample: Sample, point: int) -> None:
        if not 0 <= point < len(self._grid):
            return
        row, column, _x, _y = self._grid[point]
        sample.scan_pixel = point
        sample.scan_row = row
        sample.scan_column = column

    def _start_simulated_point(self) -> None:
        row, column, x, y = self._grid[self.point_index]
        p = self.params
        self.backend.set_voltage(1, p.approach_voltage_v)
        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
        self._positioning_z = True
        self._current_candidate = None
        self._last_approach_z = None
        self.approach_trace.clear()
        self.state = ExperimentState.PREPOSITION
        self.detail = f"Point {self.point_index + 1}/{p.point_count} · positioning ({row + 1}, {column + 1})"

    def _begin_simulated_cv(self) -> None:
        p = self.params
        self.backend.stop_motion()
        self.backend.set_voltage(1, p.cv_start_v)
        self._cv_voltage = p.cv_start_v
        self._segments = []
        for _ in range(p.cycles):
            self._segments.extend((p.cv_vertex1_v, p.cv_vertex2_v, p.cv_start_v))
        self._segment_index = 0
        self.state = ExperimentState.CV
        self.detail = f"Point {self.point_index + 1}/{p.point_count} · CV"

    def _track_current(self, sample: Sample, point: int) -> None:
        error = abs(sample.voltage1_v - self.params.map_potential_v)
        if self._current_candidate is None or error < self._current_candidate[0]:
            self._current_candidate = error, sample.current1_na

    def _finish_point_metrics(self, point: int) -> None:
        if not 0 <= point < len(self._grid):
            return
        key = self._point_key(point)
        if self._last_approach_z is not None and key not in self.contact_z:
            self.contact_z[key] = self._last_approach_z
            self.contact_detected.setdefault(key, True)
        if self._current_candidate is not None:
            self.current_at_potential[key] = self._current_candidate[1]

    def _tick_simulated(self, sample: Sample) -> None:
        p = self.params
        self._tag(sample, self.point_index)
        row, column, x, y = self._grid[self.point_index]
        tolerance = 0.08
        if self.state == ExperimentState.PREPOSITION and self._positioning_z:
            if abs(sample.z_um - p.start_z_um) >= tolerance:
                return
            self.backend.move("X", x, p.lateral_rate_um_s)
            self.backend.move("Y", y, p.lateral_rate_um_s)
            self._positioning_z = False
        if self.state == ExperimentState.PREPOSITION and all(
            abs(actual - target) < tolerance
            for actual, target in ((sample.x_um, x), (sample.y_um, y), (sample.z_um, p.start_z_um))
        ):
            self.backend.move("Z", p.end_z_um, p.approach_rate_um_s)
            self.state = ExperimentState.APPROACHING
            self.detail = f"Point {self.point_index + 1}/{p.point_count} · approaching"
        if self.state == ExperimentState.APPROACHING:
            self.approach_trace.append((sample.elapsed_s, sample.z_um, sample.current1_na))
            self._last_approach_z = sample.z_um
            value = feedback_value(sample, p.feedback_channel)
            hit = value >= p.feedback_threshold_na if p.greater_than else value <= p.feedback_threshold_na
            endpoint = abs(sample.z_um - p.end_z_um) < tolerance
            if hit:
                key = (row, column)
                self.contact_z[key] = sample.z_um
                self.contact_detected[key] = True
                self._begin_simulated_cv()
                # Do not advance the sweep using a sample that was acquired at
                # the approach potential. The next sample is at cv_start_v.
                return
            elif endpoint:
                self.contact_detected[(row, column)] = False
                self.backend.stop_motion()
                self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                self._no_contact_after_retract = True
                self.state = ExperimentState.RETRACTING
                self.detail = f"Point {self.point_index + 1}/{p.point_count} · no contact; retracting and aborting scan"
        if self.state == ExperimentState.CV:
            self._track_current(sample, self.point_index)
            now = time.monotonic()
            dt = min(now - self._last_tick, 0.25)
            self._last_tick = now
            target = self._segments[self._segment_index]
            step = p.cv_scan_rate_v_s * dt
            delta = target - self._cv_voltage
            if abs(delta) <= step:
                self._cv_voltage = target
                self._segment_index += 1
                if self._segment_index >= len(self._segments):
                    self._finish_point_metrics(self.point_index)
                    self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                    self.state = ExperimentState.RETRACTING
                    self.detail = f"Point {self.point_index + 1}/{p.point_count} · retracting"
                else:
                    self.backend.set_voltage(1, self._cv_voltage)
            else:
                self._cv_voltage += step if delta > 0 else -step
                self.backend.set_voltage(1, self._cv_voltage)
        if self.state == ExperimentState.RETRACTING and abs(sample.z_um - p.start_z_um) < tolerance:
            if self._no_contact_after_retract:
                self.state = ExperimentState.ABORTED
                self.detail = f"Scan stopped at point {self.point_index + 1}: End Z reached without contact"
                return
            self.point_index += 1
            if self.point_index >= len(self._grid):
                self.state = ExperimentState.COMPLETE
                self.detail = f"Scan complete · {p.point_count} points"
                self.progress = 1.0
            else:
                self._start_simulated_point()
        if self.active:
            self.progress = min(0.99, self.point_index / max(1, p.point_count))

    def _ingest_hardware_sample(self, sample: Sample) -> None:
        point, stage = self.backend.hardware_scan_context(sample.line_number)
        if not 0 <= point < len(self._grid):
            return
        self._tag(sample, point)
        if point != self._hardware_point:
            self._finish_point_metrics(self._hardware_point)
            self._current_candidate = None
            self._last_approach_z = None
            self.approach_trace.clear()
            self._hardware_point = point
        if stage == "approach":
            self._last_approach_z = sample.z_um
            self.approach_trace.append((sample.elapsed_s, sample.z_um, sample.current1_na))
            value = feedback_value(sample, self.params.feedback_channel)
            hit = value >= self.params.feedback_threshold_na if self.params.greater_than else value <= self.params.feedback_threshold_na
            if hit:
                self._hardware_contact_seen.add(point)
        elif stage == "cv":
            early_stop = self._last_approach_z is not None and abs(self._last_approach_z - self.params.end_z_um) >= 0.08
            if self._hardware_stage == "approach" and self._last_approach_z is not None and (
                point in self._hardware_contact_seen or early_stop
            ):
                key = self._point_key(point)
                self.contact_z[key] = self._last_approach_z
                self.contact_detected[key] = True
            self._track_current(sample, point)
        elif stage == "retract":
            self._finish_point_metrics(point)
        self._hardware_stage = stage

    def tick_samples(self, samples: list[Sample]) -> ExperimentUpdate | None:
        if not self.active:
            return None
        if self._hardware:
            for sample in samples:
                self._ingest_hardware_sample(sample)
            update = self.backend.hardware_scan_hopping_cv_status()
            self.point_index = update.point_index
            state_by_stage = {
                "preposition": ExperimentState.PREPOSITION,
                "approaching": ExperimentState.APPROACHING,
                "cv": ExperimentState.CV,
                "retracting": ExperimentState.RETRACTING,
                "complete": ExperimentState.COMPLETE,
                "aborted": ExperimentState.ABORTED,
            }
            self.state = state_by_stage.get(update.stage, ExperimentState.ABORTED)
            self.detail = update.detail
            self.progress = update.progress
            if self.state == ExperimentState.COMPLETE:
                self._finish_point_metrics(self._hardware_point)
        else:
            for sample in samples:
                if self.active:
                    self._tick_simulated(sample)
        return ExperimentUpdate(self.state, self.detail, self.progress)


def feedback_value(sample: Sample, channel: str) -> float:
    return {
        "Current 1": sample.current1_na,
        "Current 2": sample.current2_na,
    }[channel]


class CVExperiment:
    def __init__(self, backend: InstrumentBackend, settings: AppSettings) -> None:
        self.backend, self.settings = backend, settings
        self.params = CVParameters()
        self.state = ExperimentState.IDLE
        self.detail = "Configure a cyclic voltammogram, then start."
        self.progress = 0.0
        self._hardware = False
        self._targets: list[float] = []
        self._target_index = 0
        self._voltage: float | None = None
        self._last_tick = time.monotonic()

    @property
    def active(self) -> bool:
        return self.state == ExperimentState.CV

    def start(self, params: CVParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        self.params = params
        self._hardware = self.backend.hardware_approach_cv_required
        self._last_tick = time.monotonic()
        self._target_index = 0
        self.progress = 0.0
        if self._hardware:
            if not self.backend.hardware_program_available("cv"):
                raise RuntimeError("This FPGA driver does not expose standalone CV.")
            self.backend.start_hardware_program("cv", params)
            self._targets = []
        else:
            self._targets = []
            for _ in range(params.cycles):
                self._targets.extend((params.vertex1_v, params.vertex2_v, params.start_v))
            if params.jump_at_start:
                self.backend.set_voltage(1, params.start_v)
                self._voltage = params.start_v
            else:
                self._targets.insert(0, params.start_v)
                self._voltage = None
        self.state = ExperimentState.CV
        self.detail = f"CV cycle 1 of {params.cycles}"

    def abort(self) -> None:
        self.backend.stop_motion()
        self.state = ExperimentState.ABORTED
        self.detail = "CV stopped by operator"

    def tick_samples(self, samples: list[Sample]) -> ExperimentUpdate | None:
        if not self.active:
            return None
        if self._hardware:
            update = self.backend.hardware_program_status()
            self.state = {"cv": ExperimentState.CV, "complete": ExperimentState.COMPLETE, "aborted": ExperimentState.ABORTED}.get(
                update.stage, ExperimentState.CV
            )
            self.detail, self.progress = update.detail, update.progress
            return ExperimentUpdate(self.state, self.detail, self.progress)
        if not samples:
            return ExperimentUpdate(self.state, self.detail, self.progress)
        if self._voltage is None:
            self._voltage = samples[-1].voltage1_v
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.25)
        self._last_tick = now
        target = self._targets[self._target_index]
        delta = target - self._voltage
        step = self.params.scan_rate_v_s * dt
        if abs(delta) <= step:
            self._voltage = target
            self._target_index += 1
            if self._target_index >= len(self._targets):
                self.backend.set_voltage(1, self._voltage)
                self.state = ExperimentState.COMPLETE
                self.detail = "Standalone CV complete"
                self.progress = 1.0
                return ExperimentUpdate(self.state, self.detail, self.progress)
        else:
            self._voltage += step if delta > 0 else -step
        self.backend.set_voltage(1, self._voltage)
        self.progress = min(0.99, self._target_index / max(1, len(self._targets)))
        self.detail = f"CV cycle {min(self.params.cycles, self._target_index // 3 + 1)} of {self.params.cycles}"
        return ExperimentUpdate(self.state, self.detail, self.progress)


class ApproachExperiment:
    def __init__(self, backend: InstrumentBackend, settings: AppSettings) -> None:
        self.backend, self.settings = backend, settings
        self.params = ApproachParameters()
        self.state = ExperimentState.IDLE
        self.detail = "Configure an approach, then start."
        self.progress = 0.0
        self.contact_z: float | None = None
        self._hardware = False
        self._no_contact = False

    @property
    def active(self) -> bool:
        return self.state in {ExperimentState.PREPOSITION, ExperimentState.APPROACHING, ExperimentState.CONTACT, ExperimentState.RETRACTING}

    def start(self, params: ApproachParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        self.params, self.contact_z, self._no_contact = params, None, False
        self._hardware = self.backend.hardware_approach_cv_required
        if self._hardware:
            if not self.backend.hardware_program_available("approach"):
                raise RuntimeError("This FPGA driver does not expose standalone Approach.")
            self.backend.start_hardware_program("approach", params)
            self.state = ExperimentState.APPROACHING
        else:
            self.backend.set_voltage(1, params.approach_voltage_v)
            if params.x_um is not None:
                self.backend.move("X", params.x_um, max(10.0, params.approach_rate_um_s))
            if params.y_um is not None:
                self.backend.move("Y", params.y_um, max(10.0, params.approach_rate_um_s))
            self.backend.move("Z", params.start_z_um, params.retract_rate_um_s)
            self.state = ExperimentState.PREPOSITION
        self.detail, self.progress = "Moving to approach start", 0.0

    def abort(self) -> None:
        self.backend.stop_motion()
        self.state, self.detail = ExperimentState.ABORTED, "Approach stopped by operator"

    def tick_samples(self, samples: list[Sample]) -> ExperimentUpdate | None:
        if not self.active:
            return None
        if self._hardware:
            update = self.backend.hardware_program_status()
            mapping = {"preposition": ExperimentState.PREPOSITION, "approaching": ExperimentState.APPROACHING,
                       "contact": ExperimentState.CONTACT, "retracting": ExperimentState.RETRACTING,
                       "complete": ExperimentState.COMPLETE, "aborted": ExperimentState.ABORTED}
            self.state = mapping.get(update.stage, self.state)
            self.detail, self.progress = update.detail, update.progress
            return ExperimentUpdate(self.state, self.detail, self.progress)
        p = self.params
        for sample in samples:
            positioned = (
                abs(sample.z_um - p.start_z_um) < .08
                and (p.x_um is None or abs(sample.x_um - p.x_um) < .08)
                and (p.y_um is None or abs(sample.y_um - p.y_um) < .08)
            )
            if self.state == ExperimentState.PREPOSITION and positioned:
                self.backend.move("Z", p.end_z_um, p.approach_rate_um_s)
                self.state, self.detail = ExperimentState.APPROACHING, f"Watching {p.feedback_channel} for contact"
            if self.state == ExperimentState.APPROACHING:
                hit = feedback_value(sample, p.feedback_channel) >= p.feedback_threshold if p.greater_than else feedback_value(sample, p.feedback_channel) <= p.feedback_threshold
                self.progress = min(.9, abs(sample.z_um - p.start_z_um) / max(.001, abs(p.end_z_um - p.start_z_um)))
                if hit:
                    self.backend.stop_motion()
                    self.contact_z = sample.z_um
                    if p.retract_after:
                        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                        self.state, self.detail = ExperimentState.RETRACTING, f"Contact at {sample.z_um:.3f} um; retracting"
                    else:
                        self.state, self.detail, self.progress = ExperimentState.COMPLETE, f"Contact at {sample.z_um:.3f} um", 1.0
                elif abs(sample.z_um - p.end_z_um) < .08:
                    self.backend.stop_motion()
                    self._no_contact = True
                    if p.retract_after:
                        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                        self.state, self.detail = ExperimentState.RETRACTING, "End Z reached without contact; retracting"
                    else:
                        self.state, self.detail = ExperimentState.ABORTED, "End Z reached without contact"
            if self.state == ExperimentState.RETRACTING and abs(sample.z_um - p.start_z_um) < .08:
                self.state = ExperimentState.ABORTED if self._no_contact else ExperimentState.COMPLETE
                self.detail = "No contact; retract complete" if self._no_contact else "Approach and retract complete"
                self.progress = 1.0
        return ExperimentUpdate(self.state, self.detail, self.progress)


class ApproachITExperiment:
    def __init__(self, backend: InstrumentBackend, settings: AppSettings) -> None:
        self.backend, self.settings = backend, settings
        self.params = ApproachITParameters()
        self.state = ExperimentState.IDLE
        self.detail = "Configure the approach and potential steps, then start."
        self.progress = 0.0
        self.contact_z: float | None = None
        self._hardware = False
        self._steps: list[tuple[float, float, str]] = []
        self._step_index = 0
        self._step_deadline = 0.0
        self.it_label = ""
        self._no_contact = False

    @property
    def active(self) -> bool:
        return self.state in {ExperimentState.PREPOSITION, ExperimentState.APPROACHING, ExperimentState.CONTACT,
                              ExperimentState.IT, ExperimentState.RETRACTING}

    def start(self, params: ApproachITParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        self.params, self.contact_z, self._no_contact = params, None, False
        self._hardware = self.backend.hardware_approach_cv_required
        self._steps = params.it_steps()
        self._step_index, self.it_label, self.progress = 0, "", 0.0
        if self._hardware:
            if not self.backend.hardware_program_available("approach_it"):
                raise RuntimeError("This FPGA driver does not expose Approach then I-t.")
            self.backend.start_hardware_program("approach_it", params)
            self.state = ExperimentState.APPROACHING
        else:
            self.backend.set_voltage(1, params.approach_voltage_v)
            if params.x_um is not None:
                self.backend.move("X", params.x_um, max(10.0, params.approach_rate_um_s))
            if params.y_um is not None:
                self.backend.move("Y", params.y_um, max(10.0, params.approach_rate_um_s))
            self.backend.move("Z", params.start_z_um, params.retract_rate_um_s)
            self.state = ExperimentState.PREPOSITION
        self.detail = "Moving to approach start"

    def abort(self) -> None:
        self.backend.stop_motion()
        self.state, self.detail = ExperimentState.ABORTED, "Approach + I-t stopped by operator"

    def _start_it(self) -> None:
        potential, duration, label = self._steps[0]
        self.backend.set_voltage(1, potential)
        self._step_index, self.it_label = 0, label
        self._step_deadline = time.monotonic() + duration
        self.state, self.detail = ExperimentState.IT, f"I-t segment 1/{len(self._steps)} · {label}"

    def tick_samples(self, samples: list[Sample]) -> ExperimentUpdate | None:
        if not self.active:
            return None
        if self._hardware:
            update = self.backend.hardware_program_status()
            mapping = {"preposition": ExperimentState.PREPOSITION, "approaching": ExperimentState.APPROACHING,
                       "contact": ExperimentState.CONTACT, "it": ExperimentState.IT, "retracting": ExperimentState.RETRACTING,
                       "complete": ExperimentState.COMPLETE, "aborted": ExperimentState.ABORTED}
            self.state = mapping.get(update.stage, self.state)
            self.detail, self.progress, self.it_label = update.detail, update.progress, update.point_stage.removeprefix("it:")
            return ExperimentUpdate(self.state, self.detail, self.progress)
        p = self.params
        for sample in samples:
            positioned = (
                abs(sample.z_um - p.start_z_um) < .08
                and (p.x_um is None or abs(sample.x_um - p.x_um) < .08)
                and (p.y_um is None or abs(sample.y_um - p.y_um) < .08)
            )
            if self.state == ExperimentState.PREPOSITION and positioned:
                self.backend.move("Z", p.end_z_um, p.approach_rate_um_s)
                self.state, self.detail = ExperimentState.APPROACHING, f"Watching {p.feedback_channel} for contact"
            if self.state == ExperimentState.APPROACHING:
                value = feedback_value(sample, p.feedback_channel)
                hit = value >= p.feedback_threshold if p.greater_than else value <= p.feedback_threshold
                if hit:
                    self.backend.stop_motion()
                    self.contact_z = sample.z_um
                    self._start_it()
                elif abs(sample.z_um - p.end_z_um) < .08:
                    self.backend.stop_motion()
                    self._no_contact = True
                    if p.retract_after:
                        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                        self.state, self.detail = ExperimentState.RETRACTING, "End Z reached without contact; retracting"
                    else:
                        self.state, self.detail = ExperimentState.ABORTED, "End Z reached without contact; I-t not run"
            if self.state == ExperimentState.IT and time.monotonic() >= self._step_deadline:
                self._step_index += 1
                if self._step_index >= len(self._steps):
                    if p.retract_after:
                        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                        self.state, self.detail = ExperimentState.RETRACTING, "I-t complete; retracting"
                    else:
                        self.state, self.detail, self.progress = ExperimentState.COMPLETE, "Approach + I-t complete", 1.0
                else:
                    potential, duration, label = self._steps[self._step_index]
                    self.backend.set_voltage(1, potential)
                    self.it_label = label
                    self._step_deadline = time.monotonic() + duration
                    self.detail = f"I-t segment {self._step_index + 1}/{len(self._steps)} · {label}"
                self.progress = .4 + .5 * self._step_index / max(1, len(self._steps))
            if self.state == ExperimentState.RETRACTING and abs(sample.z_um - p.start_z_um) < .08:
                self.state = ExperimentState.ABORTED if self._no_contact else ExperimentState.COMPLETE
                self.detail = "No contact; I-t not run" if self._no_contact else "Approach + I-t and retract complete"
                self.progress = 1.0
        return ExperimentUpdate(self.state, self.detail, self.progress)


class ScanHoppingITExperiment:
    def __init__(self, backend: InstrumentBackend, settings: AppSettings) -> None:
        self.backend, self.settings = backend, settings
        self.params = ScanHoppingITParameters()
        self.state = ExperimentState.IDLE
        self.detail = "Configure a hopping I-t scan, then start."
        self.progress = 0.0
        self.point_index = -1
        self.contact_z: dict[tuple[int, int], float] = {}
        self.current_at_pulse: dict[tuple[int, int], float] = {}
        self._pulse_samples: dict[int, list[float]] = {}
        self._grid: list[tuple[int, int, float, float]] = []
        self._hardware = False
        self._positioning_z = False
        self._steps: list[tuple[float, float, str]] = []
        self._step_index = 0
        self._step_deadline = 0.0
        self.it_label = ""
        self._last_approach_z: dict[int, float] = {}

    @property
    def active(self) -> bool:
        return self.state in {ExperimentState.PREPOSITION, ExperimentState.APPROACHING, ExperimentState.CONTACT,
                              ExperimentState.IT, ExperimentState.RETRACTING}

    def start(self, params: ScanHoppingITParameters) -> None:
        errors = params.validate(self.settings)
        if errors:
            raise ValueError("\n".join(errors))
        self.params, self._grid = params, params.grid()
        self.contact_z.clear(); self.current_at_pulse.clear(); self._pulse_samples.clear(); self._last_approach_z.clear()
        self.point_index, self.progress = 0, 0.0
        self._hardware = self.backend.hardware_approach_cv_required
        self._steps = params.it_steps()
        if self._hardware:
            if not self.backend.hardware_program_available("scan_hopping_it"):
                raise RuntimeError("This FPGA driver does not expose Scan Hopping + I-t.")
            self.backend.start_hardware_program("scan_hopping_it", params)
            self.state, self.detail = ExperimentState.PREPOSITION, f"FPGA hopping I-t scan · {params.point_count} points"
        else:
            self._start_point()

    def abort(self) -> None:
        self.backend.stop_motion()
        self.state, self.detail = ExperimentState.ABORTED, "Hopping I-t scan stopped by operator"

    def _key(self, point: int) -> tuple[int, int]:
        return self._grid[point][0], self._grid[point][1]

    def _tag(self, sample: Sample, point: int) -> None:
        row, column, _x, _y = self._grid[point]
        sample.scan_pixel, sample.scan_row, sample.scan_column = point, row, column

    def _start_point(self) -> None:
        p = self.params
        self.backend.set_voltage(1, p.approach_voltage_v)
        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
        self._positioning_z = True
        self.state, self.detail = ExperimentState.PREPOSITION, f"Point {self.point_index + 1}/{p.point_count} · positioning"

    def _start_it(self) -> None:
        potential, duration, label = self._steps[0]
        self.backend.stop_motion(); self.backend.set_voltage(1, potential)
        self._step_index, self.it_label = 0, label
        self._step_deadline = time.monotonic() + duration
        self.state, self.detail = ExperimentState.IT, f"Point {self.point_index + 1}/{self.params.point_count} · I-t {label}"

    def _finish_pulse_map(self, point: int) -> None:
        values = self._pulse_samples.get(point, [])
        if values:
            self.current_at_pulse[self._key(point)] = sum(values) / len(values)

    def _ingest_hardware(self, sample: Sample) -> None:
        point, stage = self.backend.hardware_program_context(sample.line_number)
        if not 0 <= point < len(self._grid):
            return
        self._tag(sample, point)
        if stage == "approach":
            self._last_approach_z[point] = sample.z_um
            value = sample.current1_na
            hit = value >= self.params.feedback_threshold if self.params.greater_than else value <= self.params.feedback_threshold
            if hit:
                self.contact_z.setdefault(self._key(point), sample.z_um)
        elif stage.startswith("it:"):
            # The FPGA may have observed the threshold between host samples.
            # Entering the I-t program itself proves that contact was confirmed.
            if point in self._last_approach_z:
                self.contact_z.setdefault(self._key(point), self._last_approach_z[point])
            if stage == "it:pulse":
                self._pulse_samples.setdefault(point, []).append(sample.current1_na)
        elif stage == "retract":
            if point in self._last_approach_z and point not in self._pulse_samples:
                self.contact_z.setdefault(self._key(point), self._last_approach_z[point])
            self._finish_pulse_map(point)

    def tick_samples(self, samples: list[Sample]) -> ExperimentUpdate | None:
        if not self.active:
            return None
        if self._hardware:
            for sample in samples:
                self._ingest_hardware(sample)
            update = self.backend.hardware_program_status()
            mapping = {"preposition": ExperimentState.PREPOSITION, "approaching": ExperimentState.APPROACHING,
                       "contact": ExperimentState.CONTACT, "it": ExperimentState.IT, "retracting": ExperimentState.RETRACTING,
                       "complete": ExperimentState.COMPLETE, "aborted": ExperimentState.ABORTED}
            self.state = mapping.get(update.stage, self.state)
            self.point_index, self.detail, self.progress = update.point_index, update.detail, update.progress
            self.it_label = update.point_stage.removeprefix("it:")
            if self.state == ExperimentState.COMPLETE:
                for point in range(len(self._grid)):
                    self._finish_pulse_map(point)
            return ExperimentUpdate(self.state, self.detail, self.progress)

        p = self.params
        for sample in samples:
            self._tag(sample, self.point_index)
            _row, _column, x, y = self._grid[self.point_index]
            if self.state == ExperimentState.PREPOSITION and self._positioning_z and abs(sample.z_um - p.start_z_um) < .08:
                self.backend.move("X", x, p.lateral_rate_um_s); self.backend.move("Y", y, p.lateral_rate_um_s)
                self._positioning_z = False
            if self.state == ExperimentState.PREPOSITION and not self._positioning_z and all(
                abs(actual - target) < .08 for actual, target in ((sample.x_um, x), (sample.y_um, y))
            ):
                self.backend.move("Z", p.end_z_um, p.approach_rate_um_s)
                self.state, self.detail = ExperimentState.APPROACHING, f"Point {self.point_index + 1}/{p.point_count} · approaching"
            if self.state == ExperimentState.APPROACHING:
                value = feedback_value(sample, p.feedback_channel)
                hit = value >= p.feedback_threshold if p.greater_than else value <= p.feedback_threshold
                if hit:
                    self.contact_z[self._key(self.point_index)] = sample.z_um
                    self._start_it()
                elif abs(sample.z_um - p.end_z_um) < .08:
                    self.backend.stop_motion(); self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                    self.state, self.detail = ExperimentState.RETRACTING, "End Z reached without contact; aborting after retract"
                    self.it_label = "no-contact"
            if self.state == ExperimentState.IT:
                if self.it_label == "pulse":
                    self._pulse_samples.setdefault(self.point_index, []).append(sample.current1_na)
                if time.monotonic() >= self._step_deadline:
                    self._step_index += 1
                    if self._step_index >= len(self._steps):
                        self._finish_pulse_map(self.point_index)
                        self.backend.move("Z", p.start_z_um, p.retract_rate_um_s)
                        self.state, self.detail = ExperimentState.RETRACTING, f"Point {self.point_index + 1}/{p.point_count} · retracting"
                    else:
                        potential, duration, label = self._steps[self._step_index]
                        self.backend.set_voltage(1, potential); self.it_label = label
                        self._step_deadline = time.monotonic() + duration
            if self.state == ExperimentState.RETRACTING and abs(sample.z_um - p.start_z_um) < .08:
                if self.it_label == "no-contact":
                    self.state, self.detail = ExperimentState.ABORTED, f"No contact at point {self.point_index + 1}; I-t not run"
                else:
                    self.point_index += 1
                    if self.point_index >= len(self._grid):
                        self.state, self.detail, self.progress = ExperimentState.COMPLETE, "Hopping I-t scan complete", 1.0
                    else:
                        self._start_point()
            if self.active:
                self.progress = min(.99, self.point_index / max(1, p.point_count))
        return ExperimentUpdate(self.state, self.detail, self.progress)
