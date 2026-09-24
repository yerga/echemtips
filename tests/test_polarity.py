"""Conventions change hardware boundaries, never saved-data interpretation."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from echemtips.models import AppSettings, SettingsStore, Sample
from echemtips.ni_protocol import SampleDecoder, SAMPLE_WORDS, current_to_raw, voltage1_to_raw
from echemtips.ni_driver import WECSPMDriver
from echemtips.backends import SimulationBackend
from echemtips.waypoints import PhysicalWaypoint, WaypointCompiler
from echemtips.data import DataRecorder
from tests import test_ni_protocol as fixtures

class PolarityTests(unittest.TestCase):
    def driver(self, convention):
        fixture = fixtures.NativeDriverTests()
        fixture.setUp()
        settings = AppSettings(polarity_convention=convention)
        return WECSPMDriver(fixture.session, settings), fixture.session.registers

    def test_default_validation_and_saved_preference(self):
        self.assertEqual(AppSettings().polarity_convention, "IUPAC")
        self.assertEqual(AppSettings.from_dict({}).polarity_factor, -1)
        self.assertTrue(AppSettings(polarity_convention="invalid").validate())
        with TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "settings.json")
            for convention in ("IUPAC", "Instrument-native"):
                settings = AppSettings(polarity_convention=convention)
                store.save(settings)
                self.assertEqual(store.load(), settings)

    def test_fifo_and_compiler_round_trip_both_potentials_and_currents(self):
        for convention in ("IUPAC", "Instrument-native"):
            settings = AppSettings(polarity_convention=convention, command_voltage_ratio=5)
            f = settings.polarity_factor
            current = dict(X=0, Y=0, Z=0, V=0, V2=0)
            plan = [PhysicalWaypoint(voltage1_v=.2, voltage2_v=-.3, jump_voltage1=True, jump_voltage2=True)]
            point = WaypointCompiler(settings).compile(plan, current).waypoints[0]
            self.assertEqual(point.v_position, voltage1_to_raw(f*.2, 5))
            self.assertEqual(point.v2_position, voltage1_to_raw(f*-.3, 1))
            words = [0]*SAMPLE_WORDS
            words[3:7] = [point.v_position, point.v2_position, current_to_raw(f*.5, 1), current_to_raw(f*-.5, 1)]
            sample = SampleDecoder(settings).decode(words)
            for actual, expected in ((sample.voltage1_v,.2),(sample.voltage2_v,-.3),(sample.current1_na,.5),(sample.current2_na,-.5)):
                self.assertAlmostEqual(actual, expected, places=3)
            # A hold must preserve native outputs, not invert them a second time.
            current.update(V=point.v_position, V2=point.v2_position)
            held = WaypointCompiler(settings).compile([PhysicalWaypoint(hold_us=1000)], current).waypoints[0]
            self.assertEqual((held.v_position, held.v2_position), (point.v_position, point.v2_position))

    def test_hardware_feedback_both_polarities_channels_and_signed_modes(self):
        for convention in ("IUPAC", "Instrument-native"):
            d, regs = self.driver(convention)
            for channel in ("Current 1", "Current 2"):
                for mode in ("magnitude", "absolute", "baseline_relative"):
                    for greater in (False, True):
                        threshold = d._configure_contact_feedback(channel, .5, greater, mode, baseline=.2)
                        for current in (-1., 0., 1.):
                            raw = current_to_raw(d.settings.polarity_factor*current, 1)
                            def hit(suffix):
                                t = regs["Feedback_Threshold"+suffix].value
                                return raw >= t if regs["GreaterThan"+suffix].value else raw <= t
                            expected = abs(current)>=.5 if mode=="magnitude" else (current>=threshold if greater else current<=threshold)
                            self.assertEqual(hit("") or hit(" 2"), expected)

    def test_iupac_forced_completion_restores_secondary_threshold(self):
        from echemtips.models import ApproachCVParameters
        for manual in (True, False):
            d, regs = self.driver("IUPAC")
            d.start_approach_cv(ApproachCVParameters(feedback_mode="magnitude"))
            regs["LineNumber"].value = d._program_baseline+d._program_total
            regs["WaitingForWayPoints"].value = False
            original = regs["Feedback_Threshold 2"].value
            if manual: d.accept_approach()
            else:
                regs["Applied Z"].value = d._program_waypoints[-1].z_position
                d.service()
            self.assertEqual(regs["Feedback_Threshold 2"].value, -32769)
            regs["WaitingForWayPoints"].value = True
            d.read_samples()
            self.assertEqual(regs["Feedback_Threshold 2"].value, original)
            state = d.approach_cv_status()["stage"]
            self.assertNotEqual(state, "aborted") if manual else self.assertIn(state, ("aborted", "retracting"))

    def test_iupac_repeated_hops_finish_in_both_scan_methods(self):
        from echemtips.models import ScanHoppingCVParameters, ScanHoppingITParameters
        for method in ("cv", "it"):
            d, regs = self.driver("IUPAC")
            if method == "cv":
                d.start_scan_hopping_cv(ScanHoppingCVParameters(x_points=3, y_points=1, feedback_mode="magnitude"))
                status=d.scan_hopping_cv_status
            else:
                d.start_method("scan_hopping_it", ScanHoppingITParameters(x_points=3, y_points=1, feedback_mode="magnitude"))
                status=d.method_status
            def complete():
                regs["LineNumber"].value=d._program_baseline+d._program_total
                regs["WaitingForWayPoints"].value=True
                d.read_samples()
                return status()
            for point in range(4):
                # Native type-2 completion proves contact even when the pulse is missed by polling.
                self.assertEqual(d._program_waypoints[-1].line_type, 2)
                self.assertEqual(complete()["stage"], method)
                result=complete()
            self.assertEqual(result["stage"], "complete")

    def test_iupac_idle_and_live_commands_use_inverted_raw_targets(self):
        d, regs = self.driver("IUPAC")
        for channel in (1, 2):
            applied, fly, trigger, raw = d._potential_target(channel, .2)
            self.assertLess(raw, 0)
        d.submit_waypoints([PhysicalWaypoint(voltage1_v=.5, voltage1_rate_v_s=.1)], owner="test")
        regs["LineNumber"].value = d._program_baseline+1
        target = voltage1_to_raw(-.2, 1)
        def acknowledge():
            regs["Applied Voltage"].value = target
            return False
        with patch.object(d, "service", side_effect=acknowledge):
            d.set_live_potential(1,.2)
        self.assertEqual(regs["V on Fly"].value, target)

    def test_simulation_same_native_condition_has_opposite_display_signs(self):
        with patch("echemtips.backends.time.monotonic", return_value=100.):
            samples=[]
            for convention, potential in (("IUPAC",-.2),("Instrument-native",.2)):
                b=SimulationBackend(AppSettings(polarity_convention=convention),seed=42)
                b.connect(); b.set_voltage(1,potential)
                samples.append(b.read_sample())
            self.assertEqual(samples[0].voltage1_v, -samples[1].voltage1_v)
            self.assertEqual(samples[0].current1_na, -samples[1].current1_na)
            self.assertEqual(samples[0].current2_na, -samples[1].current2_na)

    def test_recording_metadata_keeps_selected_convention(self):
        with TemporaryDirectory() as folder:
            recorder=DataRecorder()
            recorder.start("Watch Current", AppSettings(save_directory=folder))
            recorder.append(Sample(0,0,0,0,.1,0,-.2,0))
            path=recorder.finish()
            self.assertEqual(json.loads(path.with_suffix(".json").read_text())["settings"]["polarity_convention"],"IUPAC")
