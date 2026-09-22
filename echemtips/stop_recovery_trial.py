"""EXPERIMENTAL: isolated NI stop/rearm trial; never imported by the GUI.

Unique sample-tag alignment is an empirical diagnostic, not a firmware frame
acknowledgement. Use disconnected outputs first. No reset occurs during rearm.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import secrets
import time

from .backends import NIFPGABackend
from .models import AppSettings
from .ni_protocol import SAMPLE_WORDS
from .waypoints import PhysicalWaypoint, timed_hold_plan


def tag_offsets(words: list[int], tag: int, repeats: int = 6) -> set[int]:
    """Find frame-start residues with consecutive tags in word nine.

    All fourteen phases are considered, including a discarded partial prefix.
    More than one possible phase is an explicit failure, never a guess.
    """
    result = set()
    for phase in range(SAMPLE_WORDS):
        run = 0
        for index in range(phase + 9, len(words), SAMPLE_WORDS):
            run = run + 1 if words[index] == tag else 0
            if run >= repeats:
                result.add(phase)
                break
    return result


class RecoveryTrial:
    """Own diagnostic I/O exclusively and log every recovery decision."""

    def __init__(self, driver, log, timeout: float = 10.0):
        self.driver = driver
        self.log = log
        self.timeout = timeout

    def event(self, kind: str, **values) -> None:
        """Flush one timestamped event to the private diagnostic log."""
        self.log.write(json.dumps(dict(event=kind, monotonic_s=time.monotonic(), **values)) + "\n")
        self.log.flush()

    def outputs(self) -> dict[str, int]:
        """Read commanded output indicators, not independent electrical measurements."""
        return {name: int(self.driver._read_register(name)) for name in
                ("Applied X", "Applied Y", "Applied Z", "Applied Voltage", "Applied Voltage 2")}

    def require_idle(self, expected: dict[str, int], line: int | None = None) -> None:
        """Reject target faults, queued execution or changed output indicators."""
        d = self.driver
        d._check_target_health(allow_external_stop=True)
        if not d._read_register("WaitingForWayPoints"):
            raise RuntimeError("Target is not waiting: possible surviving command")
        if self.outputs() != expected:
            raise RuntimeError("Applied outputs changed during stationary recovery")
        if line is not None and int(d._read_register("LineNumber")) != line:
            raise RuntimeError("Idle line counter changed: possible surviving command")

    def capture_tag(self, tag: int, expected: dict[str, int]) -> tuple[list[int], int]:
        """Observe a marker with bounded reads; keep raw evidence in the log."""
        d = self.driver
        d._write_register("LineNumber", tag)
        words: list[int] = []
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.require_idle(expected, tag)
            available = int(d.data_fifo.read(0, timeout_ms=0).elements_remaining)
            if available:
                count = min(available, 4096)
                chunk = list(d.data_fifo.read(count, timeout_ms=0).data)
                if len(chunk) != count:
                    raise RuntimeError("Short FIFO read during alignment")
                self.event("raw_alignment_words", tag=tag, words=chunk)
                words.extend(chunk)
                candidates = tag_offsets(words, tag)
                if len(candidates) > 1:
                    raise RuntimeError("Ambiguous sample alignment")
                if len(candidates) == 1:
                    return words, candidates.pop()
                if len(words) > 65536:
                    raise RuntimeError("Alignment evidence limit exceeded")
            time.sleep(.005)
        raise TimeoutError("No unique sample alignment before deadline")

    def recover(self) -> None:
        """Trial rearm after native cancellation, with two independent markers.

        This intentionally touches only the experimental driver instance.
        Original line numbering is restored; marker data are never decoded as
        experiment samples. Any failure latches the ordinary emergency stop.
        """
        d = self.driver
        original_line = int(d._read_register("LineNumber"))
        try:
            if not 0 <= original_line <= 32767:
                raise RuntimeError("Trial supports line counters from 0 through 32767 only")
            if not d._stopped or d._framing_valid or not d._read_register("External Stop"):
                raise RuntimeError("Recovery requires a successfully cancelled, retired stream")
            expected = self.outputs()
            d.positions_fifo.stop()
            d.positions_fifo.start()
            d._write_register("External Pause", False)
            # Quiet time is NOT used as proof of framing. Marker tests follow.
            deadline = time.monotonic() + self.timeout
            quiet_since = None
            while time.monotonic() < deadline:
                self.require_idle(expected)
                available = int(d.data_fifo.read(0, timeout_ms=0).elements_remaining)
                if available:
                    count = min(available, 4096)
                    chunk = list(d.data_fifo.read(count, timeout_ms=0).data)
                    if len(chunk) != count:
                        raise RuntimeError("Short FIFO read while discarding stop tail")
                    self.event("discarded_stop_tail", words=chunk)
                    quiet_since = None
                elif quiet_since is None:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= .1:
                    break
                time.sleep(.005)
            else:
                raise TimeoutError("Acquisition did not become quiet under Stop")
            original_line = int(d._read_register("LineNumber"))
            d._write_register("External Pause", True)
            d._write_register("Internal Pause", False)
            d._write_register("EndCurrentLine", False)
            markers = [10000 + secrets.randbelow(5000), 20000 + secrets.randbelow(5000)]
            d._write_register("LineNumber", markers[0])
            d._write_register("External Stop", False)
            first, phase = self.capture_tag(markers[0], expected)
            second, second_phase = self.capture_tag(markers[1], expected)
            if (len(first) + second_phase) % SAMPLE_WORDS != phase:
                raise RuntimeError("Sample alignment changed between markers")
            restored, restored_phase = self.capture_tag(original_line, expected)
            consumed = len(first) + len(second)
            if (consumed + restored_phase) % SAMPLE_WORDS != phase:
                raise RuntimeError("Sample alignment changed after restoring line counter")
            consumed += len(restored)
            skip = (phase - consumed) % SAMPLE_WORDS
            if skip:
                discarded = list(d.data_fifo.read(skip, timeout_ms=int(self.timeout * 1000)).data)
                if len(discarded) != skip:
                    raise RuntimeError("Short boundary-alignment read")
                self.event("discarded_boundary_tail", words=discarded)
            self.require_idle(expected, original_line)
            d._retained_samples.clear()
            d._deferred_samples.clear()
            d._framing_valid = True
            d._stopped = False
            self.event("experimental_rearm", line=original_line, outputs=expected,
                       phase=phase, markers=markers, hardware_safety_validated=False)
        except BaseException:
            d._framing_valid = False
            d.emergency_stop()
            raise
        finally:
            d._write_register("LineNumber", original_line)

    def observe(self, seconds: float) -> None:
        """Drain at full rate while recording diagnostic samples, not GUI data."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            samples = self.driver.read_samples()
            if samples:
                self.event("samples", samples=[asdict(s) for s in samples])
            time.sleep(.005)

    def finish(self, seconds: float) -> None:
        """Require normal completion and final sample drain within a deadline."""
        deadline = time.monotonic() + seconds
        while self.driver._submitted and time.monotonic() < deadline:
            self.observe(.02)
        if self.driver._submitted:
            raise TimeoutError("Subsequent command did not complete")


def main(argv=None) -> int:
    """Run only after explicit confirmation; no GUI settings are loaded/saved."""
    parser = argparse.ArgumentParser(description="EXPERIMENTAL stop/recovery diagnostic (not normal GUI)")
    parser.add_argument("--settings", type=Path, required=True, help="Private COPY of known-good settings JSON")
    parser.add_argument("--output", type=Path, default=Path("data/stop-recovery"))
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--z-test-um", type=float, default=0, help="Optional 0..5 um travel; requires second confirmation")
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 20 or not math.isfinite(args.z_test_um) or not 0 <= args.z_test_um <= 5:
        parser.error("cycles must be 1..20 and z-test-um must be 0..5")
    settings = AppSettings.from_dict(json.loads(args.settings.read_text(encoding="utf-8")))
    errors = settings.validate()
    if errors:
        parser.error("; ".join(errors))
    if args.z_test_um and (settings.z_bipolar or args.z_test_um > settings.z_range_um):
        parser.error("Limited Z test requires unipolar Z and a target within the calibrated range")
    print("EXPERIMENTAL — NOT VALIDATED FOR EXPERIMENTS. Close LabVIEW and the normal GUI.")
    print("Initial connection RESETS the FPGA: X/Y about +5 V, Z/E1/E2 zero. Disable/disconnect actuators and amplifier commands NOW.")
    if input("Type OUTPUTS DISCONNECTED to authorize initial connection: ") != "OUTPUTS DISCONNECTED":
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / (time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3) + ".jsonl")
    backend = NIFPGABackend(settings)
    with path.open("x", encoding="utf-8") as log:
        trial = None
        try:
            backend.connect(allow_startup_actuation=True)
            trial = RecoveryTrial(backend._driver, log)
            trial.event("start", settings=asdict(settings), z_test_um=args.z_test_um)
            if args.z_test_um:
                print("First complete disconnected electrical tests. Clear the probe from the surface.")
                print("Verify current X/Y and zero Z outputs before enabling the piezo controller; leave amplifier commands disabled.")
                if input("Type ENABLE LIMITED Z TEST to allow up to 5 um Z at 1 um/s: ") != "ENABLE LIMITED Z TEST":
                    raise RuntimeError("Movement stage not authorized")
            d = trial.driver
            for cycle in range(args.cycles):
                print(f"Trial {cycle + 1}/{args.cycles}: submit, interrupt, rearm, submit again")
                if args.z_test_um:
                    d.move("Z", args.z_test_um, 1.0)
                    trial.observe(min(.25, args.z_test_um / 2))
                else:
                    d.submit_waypoints(timed_hold_plan(20), owner="recovery-trial")
                    trial.observe(.15 + cycle * .013)
                d.cancel_program()
                trial.event("retained_pre_stop_samples", samples=[asdict(s) for s in d._retained_samples])
                trial.event("cancelled", outputs=trial.outputs())
                trial.recover()
                if args.z_test_um:
                    d.move("Z", 0.0, 1.0)
                else:
                    d.submit_waypoints(timed_hold_plan(.2), owner="recovery-probe")
                trial.finish(10)
                trial.event("cycle_completed", cycle=cycle + 1, outputs=trial.outputs())
            print("Diagnostic sequence completed. This is NOT a safety certification. Review logs and electrical traces.")
            return 0
        except (Exception, KeyboardInterrupt) as exc:
            if trial is not None:
                trial.event("failure", error=str(exc), error_type=type(exc).__name__)
                trial.driver.emergency_stop()
            print(f"FAILED / STOPPED: {exc}. Disable actuators before any reconnect.")
            return 1
        finally:
            backend.disconnect()
            print(f"Diagnostic log: {path.resolve()}")


if __name__ == "__main__":
    raise SystemExit(main())
