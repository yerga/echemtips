"""Prompted Newport 8742 Z-only commissioning; never connects to the FPGA.

Run ``python -m echemtips.picomotor_only_test --help`` on the operating PC.
Newport's USB driver and Python.NET are required only for hardware connection.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

from .picomotor import NewportUSB, PicomotorZ


def run_test(motor, *, steps=1, rate=100, timeout=10., check_only=False,
             prompt=input, clock=time.monotonic, sleep=time.sleep):
    """Check one controller and optionally issue one confirmed finite Z move.

    A failed or interrupted move triggers a best-effort controller-wide abort.
    Relative moves are never retried, and transport closure is always attempted.
    Pulse counts are not physical displacement measurements.
    """
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= abs(steps) <= 50:
        raise ValueError("Commissioning moves must be 1–50 signed integer steps.")
    if not isinstance(rate, int) or isinstance(rate, bool) or not 1 <= rate <= 1000:
        raise ValueError("Rate must be 1–1000 integer steps/s.")
    if not math.isfinite(timeout) or timeout <= abs(steps)/rate:
        raise ValueError("Timeout must be finite and longer than the commanded pulse train.")
    move_attempted = False
    try:
        print("Connected:", motor.connect())
        before = motor.position()
        print(f"Z port 3 pulse count: {before} (not measured position or µm)")
        if check_only:
            print("Connection check complete. No movement commanded.")
            return 0
        print(f"Requested move: {steps:+d} steps at {rate} steps/s. X/Y remain unused.")
        print("Keep the probe safely clear. Step sign and displacement are uncalibrated.")
        print("Ctrl+C attempts a software stop; USB failure can prevent it. Keep physical controls accessible.")
        if prompt("Type MOVE to authorize this single move, or anything else to cancel: ").strip() != "MOVE":
            print("Cancelled. No movement commanded.")
            return 0
        move_attempted = True  # Set before USB write: acceptance may be uncertain.
        motor.move(steps=steps, rate=rate)
        deadline = clock() + timeout
        while not motor.motion_done():
            if clock() >= deadline:
                raise TimeoutError("Motor completion timed out; do not resend the move.")
            sleep(.05)
        motor.check_error()
        after = motor.position()
        print("Final pulse count:", after)
        if after-before != steps:
            raise RuntimeError("Pulse-count change differs from the requested move; inspect before continuing.")
        print("Pulse train completed. Verify physical movement independently.")
        return 0
    except BaseException:
        if move_attempted:
            try:
                motor.abort()
                print("Abort command sent (controller-wide); physical stop is not independently verified.", file=sys.stderr)
            except Exception as exc:
                print(f"STOP UNCONFIRMED: {exc}. Use physical stop/power controls.", file=sys.stderr)
        raise
    finally:
        try:
            motor.close()
        except Exception as exc:
            # Report cleanup failures without replacing the original motion error.
            print(f"USB cleanup failed: {exc}", file=sys.stderr)


def main(argv=None):
    """Launch the motor-only tester with explicit movement confirmation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll-directory", default=r"C:\Program Files\Newport\Newport USB Driver\Bin")
    parser.add_argument("--device-key", default="", help="Required when multiple Newport devices are discovered")
    parser.add_argument("--steps", type=int, default=1, help="Signed Z steps, 1–50 in magnitude; default +1")
    parser.add_argument("--rate", type=int, default=100, help="Pulse rate, 1–1000 steps/s")
    parser.add_argument("--timeout", type=float, default=10., help="Motion completion timeout in seconds")
    parser.add_argument("--check-only", action="store_true", help="Check connection/count without commanding motion")
    args = parser.parse_args(argv)
    motor = PicomotorZ(NewportUSB(args.dll_directory, args.device_key))
    try:
        return run_test(motor, steps=args.steps, rate=args.rate, timeout=args.timeout,
                        check_only=args.check_only)
    except KeyboardInterrupt:
        print("Interrupted. Inspect the stage before another test.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, TimeoutError, EOFError) as exc:
        print(f"Test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
