from __future__ import annotations

import threading
import unittest

from echemtips.acquisition import AcquisitionBacklogError, AcquisitionWorker
from echemtips.models import Sample


def _sample(value: float) -> Sample:
    return Sample(value, 1, 2, 3, 0, 0, value, 0)


class _Backend:
    def __init__(self) -> None:
        self.connected = True
        self.first_read = threading.Event()
        self.calls = 0

    def read_samples(self) -> list[Sample]:
        self.calls += 1
        if threading.current_thread().name == "MainThread":
            return [_sample(2)]
        if self.calls == 1:
            self.first_read.set()
            return [_sample(1)]
        return []


class AcquisitionWorkerTests(unittest.TestCase):
    def test_pause_snapshot_drains_queue_then_reads_final_fifo_snapshot(self) -> None:
        backend = _Backend()
        worker = AcquisitionWorker(backend, poll_interval_s=0.001)
        worker.start()
        self.assertTrue(backend.first_read.wait(1))
        drained = worker.pause_and_snapshot()
        self.assertIsNone(drained.error)
        self.assertEqual([sample.current1_na for sample in drained.samples], [1, 2])
        worker.stop()

    def test_backlog_is_reported_without_discarding_crossing_batch(self) -> None:
        backend = _Backend()
        worker = AcquisitionWorker(backend, poll_interval_s=0.001, max_pending_samples=1)
        backend.read_samples = lambda: [_sample(1), _sample(2)]
        worker.start()
        thread = worker._thread
        assert thread is not None
        thread.join(1)
        drained = worker.stop()
        self.assertIsInstance(drained.error, AcquisitionBacklogError)
        self.assertEqual(len(drained.samples), 2)


if __name__ == "__main__":
    unittest.main()
