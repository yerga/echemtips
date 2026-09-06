from __future__ import annotations

import unittest

from echemtips.host import DisplayBuffer, WaypointStreamer
from echemtips.models import AppSettings
from echemtips.ni_protocol import FEEDBACK_ACTION_CODES, position_to_raw
from echemtips.waypoints import PhysicalWaypoint, WaypointCompiler, cyclic_voltammetry_plan, potential_step_plan


class _FIFO:
    def __init__(self) -> None:
        self.writes: list[list[int]] = []

    def write(self, data, timeout_ms=0):
        self.writes.append(list(data))
        return 1000


class HostServiceTests(unittest.TestCase):
    def test_streamer_refills_only_complete_frames(self) -> None:
        fifo = _FIFO()
        streamer = WaypointStreamer(fifo, fifo_words=140, initial_waypoints=5, refill_waypoints=3)
        streamer.start(range(11 * 14))
        self.assertEqual(streamer.pending_waypoints, 6)
        streamer.refill()
        streamer.refill()
        self.assertEqual([len(chunk) // 14 for chunk in fifo.writes], [5, 3, 3])
        self.assertTrue(streamer.complete)

    def test_display_decimation_preserves_duration_endpoints(self) -> None:
        buffer = DisplayBuffer(2, 5)
        for index in range(11):
            buffer.append(float(index), (float(index), float(-index)))
        self.assertLessEqual(len(buffer.x), 5)
        self.assertEqual((buffer.x[0], buffer.x[-1]), (0.0, 10.0))
        self.assertEqual(buffer.series[1][-1], -10.0)

    def test_common_compiler_handles_simultaneous_ramps_holds_relative_z_and_flags(self) -> None:
        settings = AppSettings()
        compiler = WaypointCompiler(settings)
        current = {"X": 0, "Y": 0, "Z": position_to_raw(10, 100, False), "V": 0, "V2": 0}
        compiled = compiler.compile([
            PhysicalWaypoint(
                x_um=20, y_um=10, z_um=30, voltage1_v=.5, voltage2_v=-.25,
                x_rate_um_s=10, y_rate_um_s=10, z_rate_um_s=5,
                voltage1_rate_v_s=.25, jump_voltage2=True,
                feedback_action="proportional_secondary_pause",
                hold=True, hold_us=1000, update_interval_us=12,
                hold_feedback1=True,
            ),
            PhysicalWaypoint(relative_z_um=-5, z_rate_um_s=5, feedback_action="relative_retract"),
        ], current)
        first, second = compiled.waypoints
        self.assertTrue(all((first.move_x, first.move_y, first.move_z, first.move_v, first.move_v2)))
        self.assertTrue(first.jump_v2 and first.hold and first.hold_feedback1)
        self.assertEqual(first.line_type, FEEDBACK_ACTION_CODES["proportional_secondary_pause"])
        self.assertEqual((first.update_wait_us, first.hold_timer), (12, 1000))
        self.assertEqual(second.line_type, FEEDBACK_ACTION_CODES["relative_retract"])
        self.assertEqual(second.z_position, position_to_raw(25, 100, False))
        self.assertAlmostEqual(compiled.expected_duration_s or 0, 5.0, places=3)
        self.assertEqual(set(compiled.scaler_exponents), {"X", "Y", "Z", "V", "V2"})

    def test_indefinite_hold_disables_duration_watchdog(self) -> None:
        compiled = WaypointCompiler(AppSettings()).compile(
            [PhysicalWaypoint(hold=True, hold_us=0)],
            {"X": 0, "Y": 0, "Z": 0, "V": 0, "V2": 0},
        )
        self.assertIsNone(compiled.expected_duration_s)

    def test_all_documented_feedback_actions_compile(self) -> None:
        compiler = WaypointCompiler(AppSettings())
        current = {"X": 0, "Y": 0, "Z": 0, "V": 0, "V2": 0}
        for name, code in FEEDBACK_ACTION_CODES.items():
            with self.subTest(name=name):
                result = compiler.compile([PhysicalWaypoint(feedback_action=name)], current)
                self.assertEqual(result.waypoints[0].line_type, code)

    def test_common_cv_plan_has_jump_ramps_and_optional_retract(self) -> None:
        plan = cyclic_voltammetry_plan(
            start_v=-0.2, vertex1_v=0.6, vertex2_v=-0.4,
            scan_rate_v_s=0.25, cycles=2, retract_z_um=10, retract_rate_um_s=5,
        )
        self.assertEqual(len(plan), 8)
        self.assertTrue(plan[0].jump_voltage1)
        self.assertEqual([item.voltage1_v for item in plan[1:7]], [0.6, -0.4, -0.2] * 2)
        self.assertEqual((plan[-1].z_um, plan[-1].z_rate_um_s), (10, 5))

    def test_cv_start_can_ramp_and_long_it_holds_are_chunked(self) -> None:
        cv = cyclic_voltammetry_plan(
            start_v=-0.2, vertex1_v=0.6, vertex2_v=-0.4,
            scan_rate_v_s=0.5, cycles=1, jump_at_start=False,
        )
        self.assertFalse(cv[0].jump_voltage1)
        self.assertEqual(cv[0].voltage1_rate_v_s, 0.5)
        plan, labels = potential_step_plan([(0.4, 0.1, "pulse")])
        self.assertEqual(len(plan), 4)
        self.assertEqual(sum(item.hold_us for item in plan), 100_000)
        self.assertTrue(all(item.hold and item.jump_voltage1 for item in plan))
        self.assertEqual(labels, ["pulse"] * 4)


if __name__ == "__main__":
    unittest.main()
