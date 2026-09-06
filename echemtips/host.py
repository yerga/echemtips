from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Iterable

from .ni_protocol import WAYPOINT_WORDS


class ExecutionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    DRAINING = "draining"
    COMPLETE = "complete"
    ABORTED = "aborted"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    owner: str
    state: ExecutionState
    submitted_waypoints: int
    executed_waypoints: int
    pending_waypoints: int
    total_waypoints: int
    detail: str = ""

    @property
    def progress(self) -> float:
        return min(1.0, self.executed_waypoints / max(1, self.total_waypoints))


class WaypointStreamer:
    """Bounded producer for the target's host-to-FPGA waypoint FIFO.

    Writes always contain complete 14-word frames. The initial fill leaves
    headroom in the target FIFO, and later calls refill in bounded chunks.
    A chunk is removed from host memory only after the NI write succeeds.
    """

    def __init__(
        self,
        fifo: Any,
        *,
        fifo_words: int = 8197,
        initial_waypoints: int = 512,
        refill_waypoints: int = 128,
    ) -> None:
        maximum = fifo_words // WAYPOINT_WORDS
        if not 1 <= initial_waypoints < maximum:
            raise ValueError("Initial waypoint fill must leave at least one complete FIFO frame free.")
        if not 1 <= refill_waypoints <= initial_waypoints:
            raise ValueError("Refill waypoint count is invalid.")
        self.fifo = fifo
        self.initial_waypoints = initial_waypoints
        self.refill_waypoints = refill_waypoints
        self._pending: list[int] = []
        self.total_waypoints = 0
        self.submitted_waypoints = 0

    @property
    def pending_waypoints(self) -> int:
        return len(self._pending) // WAYPOINT_WORDS

    @property
    def complete(self) -> bool:
        return not self._pending

    def start(self, payload: Iterable[int], *, timeout_ms: int = 100) -> None:
        words = list(payload)
        if not words or len(words) % WAYPOINT_WORDS:
            raise ValueError("Waypoint stream must contain complete, non-empty 14-word frames.")
        self._pending = words
        self.total_waypoints = len(words) // WAYPOINT_WORDS
        self.submitted_waypoints = 0
        self._write(self.initial_waypoints, timeout_ms)

    def refill(self, *, timeout_ms: int = 20) -> int:
        if not self._pending:
            return 0
        return self._write(self.refill_waypoints, timeout_ms)

    def _write(self, waypoint_limit: int, timeout_ms: int) -> int:
        count = min(len(self._pending), waypoint_limit * WAYPOINT_WORDS)
        chunk = self._pending[:count]
        self.fifo.write(chunk, timeout_ms=timeout_ms)
        del self._pending[:count]
        written = count // WAYPOINT_WORDS
        self.submitted_waypoints += written
        return written

    def cancel(self) -> None:
        self._pending.clear()


class DisplayBuffer:
    """Memory-bounded, whole-duration display data separate from recording."""

    def __init__(self, series_count: int, max_points: int) -> None:
        if series_count < 1 or max_points < 2:
            raise ValueError("Display buffer dimensions are invalid.")
        self.max_points = max_points
        self.x: list[float] = []
        self.series: list[list[float]] = [[] for _ in range(series_count)]

    def clear(self) -> None:
        self.x.clear()
        for values in self.series:
            values.clear()

    def append(self, x: float, values: tuple[float, ...]) -> bool:
        if len(values) != len(self.series) or not math.isfinite(x) or any(not math.isfinite(v) for v in values):
            return False
        self.x.append(x)
        for target, value in zip(self.series, values):
            target.append(value)
        if len(self.x) > self.max_points * 2:
            self.compact()
        return True

    def compact(self) -> None:
        count = len(self.x)
        if count <= self.max_points:
            return
        indices = [round(index * (count - 1) / (self.max_points - 1)) for index in range(self.max_points)]
        self.x[:] = [self.x[index] for index in indices]
        for values in self.series:
            values[:] = [values[index] for index in indices]
