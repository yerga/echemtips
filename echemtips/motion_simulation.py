"""Simulated acquisition exercises the same causal Z profile decisions as NI."""
from collections import deque
import math
import statistics
from .motion_profiles import approach_switch, RetractionProfile


class MotionProfileSimulation:
    """Apply profiles at acquisition time, independent of UI batch delivery."""

    def configure_motion_profiles(self, params):
        """Reset optional motion state at the start of an experiment."""
        self._profile_params = params
        self._profile_contacts = []
        self._profile_baseline = deque(maxlen=256)
        self._profile_approach_switch = None
        self._profile_retraction = None
        params.motion_events.clear()

    def _profile_move(self, axis, target, speed):
        p = getattr(self, '_profile_params', None)
        if p is None or axis != 'Z':
            return speed
        self._profile_approach_switch = None
        self._profile_retraction = None
        z = self._positions['Z']
        if p.approach_profile_enabled and target == p.end_z_um and target > z:
            switch = approach_switch(p, self._profile_contacts, self._positions['X'], self._positions['Y'], z)
            self._profile_approach_switch = switch
            p.motion_events.append(dict(kind='approach', point=getattr(self,'adaptive_pixel',-1),
                switch_z_um=switch, reason='predicted contact minus clearance' if switch is not None else 'no usable prediction: slow throughout',
                slow_um_s=p.approach_slow_um_s, fast_um_s=p.approach_fast_um_s))
            return p.approach_fast_um_s if switch is not None else p.approach_slow_um_s
        if p.retract_profile_enabled and target < z and self._sim_wet:
            self._profile_contacts.append([self._positions['X'], self._positions['Y'], z])
            values = list(self._profile_baseline)
            baseline = statistics.median(values) if len(values) >= 8 else None
            noise = statistics.median(abs(v-baseline) for v in values)*1.4826 if baseline is not None else 0.
            self._profile_retraction = RetractionProfile(p, z, target, baseline, noise,
                self._voltage[1], getattr(self,'adaptive_pixel',-1))
            return p.retract_slow_um_s
        if target < z and self._sim_wet:
            self._profile_contacts.append([self._positions['X'], self._positions['Y'], z])
        return speed

    def _profile_step_limit(self, axis, current, delta, step):
        if axis != 'Z':
            return step
        switch = getattr(self, '_profile_approach_switch', None)
        if switch is not None and delta > 0:
            if current >= switch - 1e-9:
                self._speeds['Z'] = self._profile_params.approach_slow_um_s
                self._profile_approach_switch = None
                return 0.  # Slow speed applies from the next integration interval.
            return min(step, switch-current)
        profile = getattr(self, '_profile_retraction', None)
        if profile is not None and not profile.fast:
            distance = profile.params.retract_switch_distance_um
            if profile.params.retract_automatic:
                detected = profile.monitor.detected_z
                distance = math.inf if detected is None else abs(detected-profile.start)+profile.params.retract_buffer_um
            endpoint = profile.start + profile.direction*distance
            return min(step, max(0., profile.direction*(endpoint-current)))
        return step

    def _profile_sample(self, sample):
        p = getattr(self, '_profile_params', None)
        if p is None:
            return
        current = sample.current1_na if p.feedback_channel == 'Current 1' else sample.current2_na
        if not self._sim_wet and math.isfinite(current) and getattr(self,'_profile_retraction',None) is None:
            self._profile_baseline.append(current)
        profile = getattr(self, '_profile_retraction', None)
        if profile is not None and not self._paused:
            profile.observe(sample, sample.commanded_z_um)
            segment = profile.next_segment(sample.commanded_z_um)
            if segment is None:
                self._profile_retraction = None
            elif profile.fast:
                self._speeds['Z'] = segment[1]
