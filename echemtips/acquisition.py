from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading

from .backends import InstrumentBackend
from .models import Sample


class AcquisitionBacklogError(RuntimeError):
    """The renderer failed to consume acquisition data at a safe rate."""


@dataclass(frozen=True, slots=True)
class AcquisitionDrain:
    samples: list[Sample]
    error: Exception | None
    peak_pending_samples: int


class AcquisitionWorker:
    """Continuously acquire batches without blocking UI rendering.

    No samples are discarded. If the bounded backlog is exceeded, the batch
    that crossed the limit is retained, acquisition stops, and the UI receives
    an explicit error so it can pause motion and preserve the partial file.
    """

    def __init__(
        self,
        backend: InstrumentBackend,
        *,
        poll_interval_s: float = 0.01,
        max_pending_samples: int = 65_536,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if max_pending_samples < 1:
            raise ValueError("max_pending_samples must be positive")
        self.backend = backend
        self.poll_interval_s = poll_interval_s
        self.max_pending_samples = max_pending_samples
        self._batches: deque[list[Sample]] = deque()
        self._pending_samples = 0
        self._peak_pending_samples = 0
        self._error: Exception | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._pause.clear()
        self._paused.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="eChemTips acquisition", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._pause.is_set():
                self._paused.set()
                self._wake.wait(self.poll_interval_s)
                self._wake.clear()
                continue
            self._paused.clear()
            try:
                samples = self.backend.read_samples()
            except Exception as exc:
                with self._lock:
                    self._error = exc
                self._stop.set()
                break
            if samples:
                batch = list(samples)
                with self._lock:
                    self._batches.append(batch)
                    self._pending_samples += len(batch)
                    self._peak_pending_samples = max(self._peak_pending_samples, self._pending_samples)
                    if self._pending_samples > self.max_pending_samples:
                        self._error = AcquisitionBacklogError(
                            f"Acquisition backlog reached {self._pending_samples} samples "
                            f"(limit {self.max_pending_samples}); no samples were discarded."
                        )
                        self._stop.set()
                        break
            self._wake.wait(self.poll_interval_s)
            self._wake.clear()
        self._paused.set()

    def drain(self) -> AcquisitionDrain:
        with self._lock:
            samples = [sample for batch in self._batches for sample in batch]
            self._batches.clear()
            self._pending_samples = 0
            error = self._error
            self._error = None
            peak = self._peak_pending_samples
        return AcquisitionDrain(samples, error, peak)

    def pause_and_snapshot(self, timeout_s: float = 2.0) -> AcquisitionDrain:
        """Pause the worker and take one final FIFO snapshot at a known barrier."""
        if self.running:
            self._pause.set()
            self._wake.set()
            if not self._paused.wait(timeout_s):
                return AcquisitionDrain([], TimeoutError("Acquisition worker did not pause"), self._peak_pending_samples)
        initial = self.drain()
        final_samples: list[Sample] = []
        final_error = initial.error
        if final_error is None and self.backend.connected:
            try:
                final_samples = list(self.backend.read_samples())
            except Exception as exc:
                final_error = exc
        return AcquisitionDrain(initial.samples + final_samples, final_error, initial.peak_pending_samples)

    def resume(self) -> None:
        if self._stop.is_set():
            return
        self._pause.clear()
        self._paused.clear()
        self._wake.set()

    def stop(self, timeout_s: float = 2.0) -> AcquisitionDrain:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)
            if thread.is_alive():
                with self._lock:
                    self._error = self._error or TimeoutError("Acquisition worker did not stop")
        return self.drain()
