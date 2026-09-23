"""EXPERIMENTAL reset-free Stop then Z return; not present on production main.

The caller must own acquisition exclusively. The scientific recording ends
before empirical frame recovery; recovery and return data belong to the trial
log, not a falsely continuous experimental time series.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict

from .backends import SimulationBackend
from .ni_driver import WECSPMDriver
from .ni_protocol import position_to_raw, raw_to_position
from .stop_recovery_trial import RecoveryTrial


def return_target(parameters, current_z: float, maximum_z: float) -> tuple[float, float] | None:
    """Choose a bounded initial-Z return, never reversing toward the surface.

    Methods without an initial Z (standalone CV) do not move the probe.
    """
    if not hasattr(parameters, "start_z_um"):
        return None
    start, end = parameters.start_z_um, parameters.end_z_um
    speed = parameters.retract_rate_um_s
    if not all(math.isfinite(v) for v in (start, end, speed, current_z, maximum_z)):
        raise ValueError("Non-finite stop-return configuration")
    if not 0 <= start <= maximum_z or not 0 <= current_z <= maximum_z or speed <= 0 or start == end:
        raise ValueError("Invalid stop-return configuration")
    return (min(start, current_z) if end > start else max(start, current_z)), speed


def stop_and_return(backend, parameters, log, on_cancelled, check_cancelled, progress) -> dict:
    """Cancel, close the scientific record, rearm, and await a Z-only return.

    ``on_cancelled`` receives only trustworthy pre-stop frames and must complete
    successfully before any recovery/return. Any error latches emergency stop.
    All I/O is performed by the caller's exclusive worker, never the GUI thread.
    """
    d = getattr(backend, "_driver", None)
    hardware = backend.hardware_approach_cv_required
    try:
        if hardware:
            if not isinstance(d, WECSPMDriver):
                raise RuntimeError("Experimental recovery requires the native NI driver")
            if d._stopped:
                raise RuntimeError("Fault-latched targets cannot use experimental recovery")
            d._check_target_health()
        elif not isinstance(backend, SimulationBackend):
            raise RuntimeError("Unsupported experimental stop backend")
        progress("Stopping experiment")
        backend.stop_motion()
        tail = backend.read_samples()
        on_cancelled(tail)
        check_cancelled()
        if hardware:
            trial = RecoveryTrial(d, log, check_cancelled=check_cancelled)
            expected = trial.outputs()
            trial.event("gui_stop", outputs=expected, parameters=asdict(parameters))
            progress("Verifying FPGA recovery — experimental")
            trial.recover()
            current_z = raw_to_position(expected["Applied Z"], backend.settings.z_range_um, backend.settings.z_bipolar)
        else:
            trial = None
            current_z = backend.read_sample().z_um
        target = return_target(parameters, current_z, backend.settings.z_range_um)
        check_cancelled()
        if target is None:
            return {"detail": "Stopped; no Z return for this method", "target_z_um": None}
        z, speed = target
        progress(f"Returning Z toward initial position: {z:g} µm at {speed:g} µm/s")
        if hardware:
            raw_target = position_to_raw(z, backend.settings.z_range_um, backend.settings.z_bipolar)
            if raw_target != expected["Applied Z"]:
                backend.move("Z", z, speed)
            deadline = time.monotonic() + abs(current_z - z) / speed + backend.settings.hardware_watchdog_margin_s
            while True:
                check_cancelled()
                samples = d.read_samples()
                if samples:
                    trial.event("return_samples", samples=[asdict(s) for s in samples])
                outputs = trial.outputs()
                if any(outputs[k] != expected[k] for k in expected if k != "Applied Z"):
                    raise RuntimeError("An X/Y or potential output changed during Z return")
                d._check_target_health()
                if not d._submitted:
                    if outputs["Applied Z"] != raw_target or not d._read_register("WaitingForWayPoints"):
                        raise RuntimeError("Initial-Z return was not acknowledged")
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Initial-Z return did not complete")
                time.sleep(.01)
            trial.event("gui_return_complete", outputs=outputs, target_z_um=z,
                        hardware_safety_validated=False)
        else:
            backend.resume()
            backend.move("Z", z, speed)
            deadline = time.monotonic() + abs(current_z - z) / speed + 5
            while True:
                check_cancelled()
                if abs(backend.read_sample().z_um - z) < 1e-6:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Simulated Z return did not complete")
                time.sleep(.01)
        return {"detail": "Experiment stopped; Z return complete", "target_z_um": z}
    except BaseException as exc:
        try:
            import json
            log.write(json.dumps({"event": "stop_return_failed", "error": str(exc),
                                  "monotonic_s": time.monotonic()}) + "\n")
            log.flush()
        except Exception:
            pass  # Logging must never suppress emergency stopping.
        backend.emergency_stop()
        raise
