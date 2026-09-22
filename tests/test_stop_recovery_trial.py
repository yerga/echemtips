"""Synthetic stream tests; do not establish safety of NI hardware recovery."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from echemtips.stop_recovery_trial import RecoveryTrial, tag_offsets


def frame(tag):
    return [1, 2, 3, 4, 5, 6, 7, 8, 0, tag, 10, 11, -32768, 7232]


class MarkerTests(unittest.TestCase):
    def test_all_partial_prefix_lengths(self):
        for prefix in range(14):
            self.assertEqual(tag_offsets([99] * prefix + frame(12345) * 8, 12345), {prefix})

    def test_short_missing_and_ambiguous(self):
        self.assertEqual(tag_offsets(frame(12345) * 5, 12345), set())
        self.assertEqual(tag_offsets(frame(9) * 8, 12345), set())
        self.assertEqual(len(tag_offsets([12345] * 140, 12345)), 14)

    def test_second_marker_requires_same_global_phase(self):
        first = [99] * 5 + frame(12345) * 8 + frame(12345)[:3]
        second = frame(12345)[3:] + frame(23456) * 8
        phase = tag_offsets(first, 12345).pop()
        second_phase = tag_offsets(second, 23456).pop()
        self.assertEqual((len(first) + second_phase) % 14, phase)


class Fifo:
    def __init__(self, driver):
        self.driver = driver
        self.words = []
        self.stops = self.starts = 0

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1

    def read(self, count, timeout_ms=0):
        if not self.words and not self.driver.values['External Stop']:
            self.words.extend(frame(self.driver.values['LineNumber']) * 8)
        data, self.words = self.words[:count], self.words[count:]
        return SimpleNamespace(data=data, elements_remaining=len(self.words))


class Driver:
    def __init__(self):
        self.values = dict(LineNumber=12, WaitingForWayPoints=True)
        self.values.update({'External Stop': True, 'External Pause': True})
        self._stopped, self._framing_valid = True, False
        self._retained_samples, self._deferred_samples = [], []
        self.positions_fifo = Fifo(self)
        self.data_fifo = Fifo(self)
        self.emergencies = 0

    def _read_register(self, name):
        return self.values.get(name, 0)

    def _write_register(self, name, value):
        self.values[name] = value

    def _check_target_health(self, **kwargs):
        pass

    def emergency_stop(self):
        self.emergencies += 1
        self._stopped = True
        self.values['External Stop'] = True
        self.values['External Pause'] = True


class RecoveryTests(unittest.TestCase):
    def test_rearm_preserves_line_and_aligns_stream(self):
        for prefix in range(14):
            d = Driver()
            original_write = d._write_register
            def write(name, value):
                original_write(name, value)
                if name == 'External Stop' and value is False:
                    d.data_fifo.words.extend([99] * prefix)
            d._write_register = write
            with patch('echemtips.stop_recovery_trial.time.sleep'), patch('echemtips.stop_recovery_trial.time.monotonic', side_effect=[i * .01 for i in range(1000)]):
                RecoveryTrial(d, io.StringIO()).recover()
            self.assertFalse(d._stopped)
            self.assertTrue(d._framing_valid)
            self.assertEqual(d.values['LineNumber'], 12)
            self.assertEqual(d.positions_fifo.stops, 1)
            self.assertEqual(d.positions_fifo.starts, 1)
            self.assertEqual(d.data_fifo.read(14).data[9], 12)

    def test_surviving_command_latches_failure(self):
        d = Driver()
        d.values['WaitingForWayPoints'] = False
        with self.assertRaisesRegex(RuntimeError, 'surviving command'):
            RecoveryTrial(d, io.StringIO()).recover()
        self.assertEqual(d.emergencies, 1)
        self.assertTrue(d._stopped)
        self.assertFalse(d._framing_valid)

    def test_output_change_rejected(self):
        d = Driver()
        trial = RecoveryTrial(d, io.StringIO())
        expected = trial.outputs()
        d.values['Applied Z'] = 100
        with self.assertRaisesRegex(RuntimeError, 'outputs changed'):
            trial.require_idle(expected)

    def test_missing_stream_times_out_and_latches(self):
        d = Driver()
        d.data_fifo.read = lambda *args, **kwargs: SimpleNamespace(data=[], elements_remaining=0)
        with patch('echemtips.stop_recovery_trial.time.sleep'), patch('echemtips.stop_recovery_trial.time.monotonic', side_effect=[i * .01 for i in range(1000)]):
            with self.assertRaises(TimeoutError):
                RecoveryTrial(d, io.StringIO(), timeout=.3).recover()
        self.assertTrue(d._stopped)
        self.assertFalse(d._framing_valid)
        self.assertEqual(d.values['LineNumber'], 12)

    def test_phase_change_cannot_rearm(self):
        d = Driver()
        trial = RecoveryTrial(d, io.StringIO())
        with patch.object(trial, 'capture_tag', side_effect=[(frame(12345) * 8, 0), ([99] + frame(23456) * 8, 1)]), patch('echemtips.stop_recovery_trial.time.sleep'), patch('echemtips.stop_recovery_trial.time.monotonic', side_effect=[i * .01 for i in range(1000)]):
            with self.assertRaisesRegex(RuntimeError, 'alignment changed'):
                trial.recover()
        self.assertTrue(d._stopped)
        self.assertFalse(d._framing_valid)
        self.assertEqual(d.values['LineNumber'], 12)


if __name__ == '__main__':
    unittest.main()
