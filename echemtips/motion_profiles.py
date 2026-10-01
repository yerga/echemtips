"""Optional Z speed profiles and causal, baseline-relative detachment evidence."""
from collections import deque
from dataclasses import dataclass, field
import math
import statistics


@dataclass(kw_only=True)
class MotionProfileParameters:
    """Opt-in motion settings; disabled profiles preserve existing motion."""
    approach_profile_enabled: bool = False
    approach_fast_um_s: float = 15.
    approach_slow_um_s: float = 2.
    approach_clearance_um: float = 5.
    retract_profile_enabled: bool = False
    retract_fast_um_s: float = 15.
    retract_slow_um_s: float = 1.
    retract_switch_distance_um: float = 5.
    retract_automatic: bool = False
    retract_buffer_um: float = 1.
    expected_contact_z_um: float | None = None
    motion_events: list = field(default_factory=list, repr=False, compare=False)

    def validate_motion_profiles(self, settings):
        """Validate enabled rates, separation and profile geometry before motion."""
        errors = []
        for side in ('approach', 'retract'):
            if not getattr(self, side + '_profile_enabled'):
                continue
            rates = [getattr(self, side + '_' + speed + '_um_s') for speed in ('slow', 'fast')]
            if any(not math.isfinite(v) or v <= 0 for v in rates) or rates[1] < rates[0]:
                errors.append(f'{side.title()} rates must be positive; fast must be at least slow.')
        distances = []
        if self.approach_profile_enabled:
            distances.append(('Approach clearance', self.approach_clearance_um))
            if self.end_z_um <= self.start_z_um:
                errors.append('Two-speed approach requires increasing Z toward the surface.')
        if self.retract_profile_enabled:
            distances.append(('Detachment buffer' if self.retract_automatic else 'Slow withdrawal distance',
                              self.retract_buffer_um if self.retract_automatic else self.retract_switch_distance_um))
        for name, value in distances:
            if not math.isfinite(value) or not 0 <= value <= settings.z_range_um:
                errors.append(f'{name} must lie between zero and the Z range.')
        if self.approach_profile_enabled and self.approach_clearance_um <= 0:
            errors.append('Approach clearance must be positive.')
        if self.expected_contact_z_um is not None and (not math.isfinite(self.expected_contact_z_um)
                or not 0 <= self.expected_contact_z_um <= settings.z_range_um):
            errors.append('Expected contact Z is outside the configured range.')
        return errors

    def motion_duration(self, distance, side, default_rate):
        """Estimate travel; unknown contact/detachment conservatively uses slow speed."""
        if not getattr(self,side+'_profile_enabled'):
            return distance/default_rate
        slow, fast = getattr(self,side+'_slow_um_s'), getattr(self,side+'_fast_um_s')
        if side=='approach' or self.retract_automatic:
            return distance/slow
        slow_distance = min(distance,self.retract_switch_distance_um)
        return slow_distance/slow + (distance-slow_distance)/fast


PROFILE_FIELDS = tuple(name for name in MotionProfileParameters.__dataclass_fields__
                      if name not in ('motion_events', 'expected_contact_z_um'))


def predicted_contact(contacts, x, y):
    """Fit a plane only after non-collinear contacts; include residual uncertainty.

    Use commanded coordinates consistently. The model cannot bound unseen
    relief; the operator-selected clearance must cover the expected relief.
    """
    if len(contacts) < 3:
        return None
    import numpy as np
    data = np.asarray(contacts, dtype=float)
    a = np.column_stack((data[:, 0] - x, data[:, 1] - y, np.ones(len(data))))
    if not np.isfinite(data).all() or np.linalg.matrix_rank(a) < 3:
        return None
    coefficients = np.linalg.lstsq(a, data[:, 2], rcond=None)[0]
    residual = float(np.max(np.abs(a @ coefficients - data[:, 2])))
    # Do not accelerate into a region predicted beyond the shallowest observed
    # contact without retaining an equally conservative clearance.
    return min(float(coefficients[2]), float(data[:, 2].min())) - 3 * residual


def approach_switch(params, contacts, x, y, start):
    """Return a conservative fast-segment endpoint, or None for a slow approach."""
    if not params.approach_profile_enabled:
        return None
    expected = params.expected_contact_z_um
    if expected is None:
        expected = predicted_contact(contacts, x, y)
    if expected is None:
        return None
    switch = expected - params.approach_clearance_um
    return switch if start < switch < params.end_z_um else None


class DetachmentMonitor:
    """Confirm a sustained current collapse using only samples already acquired."""
    confirmation_s = .04

    def __init__(self, baseline, noise, potential, channel):
        self.baseline = baseline
        self.threshold = max(.005, 6 * noise)
        self.potential = potential
        self.channel = channel
        self.window = deque()
        self.attached = False
        self.quiet_since = None
        self.detected_z = None
        self.invalid = baseline is None
        self.last_time = None

    def observe(self, sample, z):
        """Require attached contrast, stable potential and persistent quiet current."""
        if self.invalid or self.detected_z is not None:
            return
        t = sample.elapsed_s
        current = sample.current1_na if self.channel == 'Current 1' else sample.current2_na
        if not all(math.isfinite(v) for v in (t, z, current, sample.voltage1_v)):
            self.invalid = True
            return
        if abs(sample.voltage1_v - self.potential) > .005:
            self.invalid = True
            return
        if self.last_time is not None and (t <= self.last_time or t - self.last_time > .1):
            self.window.clear()
            self.quiet_since = None
        self.last_time = t
        self.window.append((t, abs(current - self.baseline)))
        while self.window and t - self.window[0][0] > self.confirmation_s:
            self.window.popleft()
        if len(self.window) < 3:
            return
        residual = statistics.median(v for _, v in self.window)
        if residual > 2 * self.threshold and t - self.window[0][0] >= self.confirmation_s * .8:
            self.attached = True
        if self.attached and residual < self.threshold:
            if self.quiet_since is None:
                self.quiet_since = t
            if t - self.quiet_since >= self.confirmation_s:
                self.detected_z = z
        else:
            self.quiet_since = None


class RetractionProfile:
    """Choose slow segment endpoints and fast transition without changing final Z."""
    segment_s = .25

    def __init__(self, params, start, target, baseline=None, noise=0., potential=0., point=-1):
        self.params, self.start, self.target = params, start, target
        self.direction = -1 if target < start else 1
        self.point = point
        self.monitor = DetachmentMonitor(baseline, noise, potential, params.feedback_channel)
        self.fast = False
        self.event = dict(kind='retraction', point=point, start_z_um=start, target_z_um=target,
                          mode='detachment' if params.retract_automatic else 'distance',
                          switch_z_um=None, detected_z_um=None, reason='pending',
                          slow_um_s=params.retract_slow_um_s, fast_um_s=params.retract_fast_um_s)
        params.motion_events.append(self.event)

    def observe(self, sample, z):
        """Feed causal detector evidence only during retraction."""
        if self.params.retract_automatic:
            self.monitor.observe(sample, z)

    def next_segment(self, z):
        """Return the next endpoint/rate, conservatively rounding the buffer outward."""
        p = self.params
        remaining = self.direction * (self.target - z)
        if remaining <= 1e-6:
            if not self.fast:
                self.event['reason'] = 'endpoint reached before switch' if not p.retract_automatic else 'no confirmed detachment/buffer before endpoint'
            return None
        detected = self.monitor.detected_z
        distance = p.retract_switch_distance_um
        if p.retract_automatic:
            distance = math.inf if detected is None else abs(detected - self.start) + p.retract_buffer_um
        travelled = self.direction * (z - self.start)
        if not self.fast and travelled >= distance - 1e-6:
            self.fast = True
            self.event.update(switch_z_um=z, detected_z_um=detected,
                              reason='confirmed detachment + buffer' if p.retract_automatic else 'configured distance')
        if self.fast:
            return self.target, p.retract_fast_um_s
        length = min(remaining, p.retract_slow_um_s * self.segment_s, max(0., distance - travelled))
        return z + self.direction * length, p.retract_slow_um_s
