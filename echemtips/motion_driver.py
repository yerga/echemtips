"""Framed FPGA transitions for opt-in approach and withdrawal speed profiles."""
from dataclasses import replace
import math
import statistics
from collections import deque
from .motion_profiles import approach_switch, RetractionProfile
from .ni_protocol import (FEEDBACK_ACTION_CODES, raw_to_position, position_to_raw,
    raw_position_velocity_per_tick, select_velocity_exponent, scale_velocity)
from .waypoints import PhysicalWaypoint


class MotionProfileDriver:
    """Shared implementation for the native driver's three experiment runners."""

    def _begin_motion_profiles(self, params):
        self._profile_params = params
        self._profile_contacts = []
        self._profile_baseline = deque(maxlen=256)
        self._profile_approach_next = None
        self._profile_retraction = None
        self._profile_internal = False
        if hasattr(params, 'motion_events'):
            params.motion_events.clear()
        if (getattr(params, 'retract_profile_enabled', False) or
                getattr(params, 'approach_profile_enabled', False)):
            # Reserve a worst-case full-range, slow, segmented return per hop.
            # This is deliberately conservative and runs before any output.
            distance = (getattr(params, 'retract_distance_um', abs(params.end_z_um-params.start_z_um))
                        + getattr(params, 'raster_line_retract_um', 0.))
            per_hop = (math.ceil(distance /
                (params.retract_slow_um_s * RetractionProfile.segment_s)) + 5
                if params.retract_profile_enabled else 3)
            points = getattr(params, 'execution_point_count', 1)
            final = (math.ceil(abs(params.end_z_um-params.start_z_um) /
                              (params.retract_slow_um_s * RetractionProfile.segment_s))
                     if params.retract_profile_enabled else 0)
            base = 0
            for point in range(points):
                program = params.for_point(point) if hasattr(params, 'for_point') else params
                holds = sum(max(1,math.ceil(t*1e6/32767)) for _,t,_ in program.it_steps()) if hasattr(program,'it_steps') else 0
                cv = 1+3*getattr(program,'total_cv_cycles',getattr(program,'cycles',0)) if hasattr(program,'cv_start_v') else 0
                conditioning = program.conditioning_frames() if hasattr(program,'conditioning_frames') and not holds else 0
                base += 5+holds+cv+conditioning+math.ceil(getattr(program,'settling_time_s',0)*1e6/32767)
            if int(self._read_register('LineNumber')) + per_hop * points + final + base > 30000:
                raise ValueError('Motion profiles exceed the available FPGA line tags. Increase the slow rate, reduce the scan, or reconnect.')

    def _profile_approach_waypoints(self, waypoints):
        p = getattr(self, '_profile_params', None)
        if getattr(self, '_profile_internal', False) or not getattr(p, 'approach_profile_enabled', False):
            return waypoints
        if not waypoints or waypoints[-1].line_type != FEEDBACK_ACTION_CODES['advance_on_contact']:
            return waypoints
        s = self.settings
        point = getattr(self, '_scan_point', -1) if self._owner == 'scan-hopping-cv' else getattr(self, '_method_point', -1)
        w = waypoints[-1]
        x = raw_to_position(w.x_position, s.x_range_um, s.x_bipolar)
        y = raw_to_position(w.y_position, s.y_range_um, s.y_bipolar)
        start_raw = waypoints[-2].z_position if len(waypoints) > 1 else self._current_targets()['Z']
        start = raw_to_position(start_raw, s.z_range_um, s.z_bipolar)
        switch = approach_switch(p, self._profile_contacts, x, y, start)
        old_exponent = self._pending_scalers[2]
        velocities = [a.z_velocity / 2**old_exponent for a in waypoints]
        slow = raw_position_velocity_per_tick(p.approach_slow_um_s, s.z_range_um, s.z_bipolar)
        fast = raw_position_velocity_per_tick(p.approach_fast_um_s, s.z_range_um, s.z_bipolar)
        exponent = select_velocity_exponent(velocities + [slow, fast])
        result = [replace(a, z_velocity=scale_velocity(v, exponent)) for a, v in zip(waypoints, velocities)]
        self._pending_scalers = (*self._pending_scalers[:2], exponent, *self._pending_scalers[3:])
        result[-1] = replace(result[-1], z_velocity=scale_velocity(slow, exponent))
        self._profile_approach_next = None
        if switch is not None:
            self._profile_approach_next = PhysicalWaypoint(z_um=p.end_z_um,
                z_rate_um_s=p.approach_slow_um_s, feedback_action='advance_on_contact',
                update_interval_us=self._feedback_update_interval_us)
            result[-1] = replace(result[-1], z_position=position_to_raw(switch, s.z_range_um, s.z_bipolar),
                                 z_velocity=scale_velocity(fast, exponent))
        p.motion_events.append(dict(kind='approach', point=point, switch_z_um=switch,
            reason='predicted contact minus clearance' if switch is not None else 'no usable prediction: slow throughout',
            slow_um_s=p.approach_slow_um_s, fast_um_s=p.approach_fast_um_s))
        return result

    def _profile_followup(self, plan, contexts, point=-1):
        p = getattr(self, '_profile_params', None)
        current = self._current_targets()
        s = self.settings
        xyz = [raw_to_position(current[k], getattr(s, k.lower()+'_range_um'), getattr(s, k.lower()+'_bipolar')) for k in ('X','Y','Z')]
        if p is not None:
            self._profile_contacts.append(xyz)
        if not getattr(p, 'retract_profile_enabled', False) or not contexts or contexts[-1] != 'retract':
            return plan, contexts
        final = plan[-1]
        if final.z_um >= xyz[2]:
            # A return towards the surface is not a withdrawal; preserve its
            # ordinary bounded move rather than invoking detachment logic.
            return plan, contexts
        values = list(self._profile_baseline)
        baseline = statistics.median(values) if len(values) >= 8 else None
        noise = statistics.median(abs(v-baseline) for v in values)*1.4826 if baseline is not None else 0.
        self._profile_retraction = RetractionProfile(p, xyz[2], final.z_um, baseline, noise, point=point)
        plan, contexts = plan[:-1], contexts[:-1]
        if not plan:
            plan, contexts = [PhysicalWaypoint(hold=True, hold_us=10000)], ['settling']
        return plan, contexts

    def _profile_observe_samples(self, samples):
        p = getattr(self, '_profile_params', None)
        if p is None or not hasattr(p, 'feedback_channel'):
            return
        profile = getattr(self, '_profile_retraction', None)
        # FIFO frames contain measured Z, but no commanded trajectory. Use a
        # single current AO readback per batch for the buffer origin. It is
        # later than the acquired break, conservatively extending slow travel.
        command_z = (raw_to_position(self._current_targets()['Z'], self.settings.z_range_um,
                     self.settings.z_bipolar) if profile is not None else None)
        for sample in samples:
            if self._owner == 'approach-cv':
                context = self.approach_context(sample.line_number)
            elif self._owner == 'scan-hopping-cv':
                _, context = self.scan_context(sample.line_number)
            else:
                _, context = self.method_context(sample.line_number)
            current = sample.current1_na if p.feedback_channel == 'Current 1' else sample.current2_na
            if (context in ('preposition', 'positioning', 'baseline') or
                    (context == 'approach' and len(self._profile_baseline) < 64
                     and abs(current) < abs(getattr(p, 'feedback_threshold_na',
                                                   getattr(p, 'feedback_threshold', 0.))))) and math.isfinite(current):
                self._profile_baseline.append(current)
            if profile is not None and context == 'retract':
                sensitivity = (self.settings.current1_v_per_na if p.feedback_channel == 'Current 1'
                               else self.settings.current2_v_per_na)
                if not math.isfinite(current) or abs(current * sensitivity) >= 9.5:
                    profile.monitor.invalid = True
                if math.isfinite(command_z) and command_z < profile.start - .01:
                    profile.observe(sample, command_z)

    def _profile_submit(self, plan, contexts, point):
        compiled = self.compiler.compile(plan, self._current_targets())
        self._pending_scalers = tuple(compiled.scaler_exponents[k] for k in ('X','Y','Z','V','V2'))
        self._profile_internal = True
        try:
            self._enqueue(compiled.waypoints, compiled.expected_duration_s)
        finally:
            self._profile_internal = False
        if self._owner == 'approach-cv':
            from .ni_driver import _Sequence
            self._approach_history.append((self._program_baseline, contexts))
            retract = 0 if contexts[-1] == 'retract' else None
            self._sequence = _Sequence(self._program_baseline, len(contexts), len(contexts), 0, retract)
        else:
            from .ni_driver import _ScanSequence
            sequence = _ScanSequence(self._program_baseline, [(point,c) for c in contexts])
            if self._owner == 'scan-hopping-cv':
                self._scan_sequence = sequence
                self._scan_history.append(sequence)
            else:
                self._method_sequence = sequence
                self._method_history.append(sequence)

    def _service_motion_profiles(self):
        if self._submitted or not self._hardware_complete or self._stopped or self._cancelled:
            return
        if self._operator_paused or bool(self._read_register('External Pause')):
            return
        pending = getattr(self, '_profile_approach_next', None)
        point = self._scan_point if self._owner == 'scan-hopping-cv' else getattr(self, '_method_point', -1)
        if pending is not None:
            self._profile_approach_next = None
            if self._contact_finish_reason == 'limit':
                self._profile_submit([PhysicalWaypoint(hold=True, hold_us=10000), pending],
                                     ['speed_settle','approach'], point)
                return
        profile = getattr(self, '_profile_retraction', None)
        if profile is not None:
            current = self._current_targets()
            z = raw_to_position(current['Z'], self.settings.z_range_um, self.settings.z_bipolar)
            if not hasattr(profile, '_started'):
                from .ni_protocol import raw_to_voltage1
                profile.monitor.potential = raw_to_voltage1(current['V'], self.settings.command_voltage_ratio) * self.settings.polarity_factor
                profile._started = True
            segment = profile.next_segment(z)
            if segment is None:
                self._profile_retraction = None
                return
            target, speed = segment
            self._profile_submit([PhysicalWaypoint(z_um=target, z_rate_um_s=speed)], ['retract'], profile.point)
