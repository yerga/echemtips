from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .models import DEFAULT_BITFILE
from .ni_protocol import (
    ANALOG_INPUT_CHANNELS,
    ANALOG_OUTPUT_CHANNELS,
    FEEDBACK_ACTION_CODES,
    FEEDBACK_SIGNAL_CODES,
    inspect_bitfile,
    validate_wec_bitfile,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect an eChemTips-compatible bitfile and optionally open an NI FPGA session without running it."
    )
    parser.add_argument("--bitfile", default=DEFAULT_BITFILE, help="Path to the compiled .lvbitx file")
    parser.add_argument("--resource", default="RIO0", help="NI MAX RIO resource name")
    parser.add_argument(
        "--transport",
        choices=("usb", "pcie", "auto"),
        default="usb",
        help="Expected FPGA connection type (default: usb)",
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Download/open the bitfile with no_run=True to verify the real NI-RIO connection",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    transport = {"usb": "USB R Series", "pcie": "PCIe/PXI R Series", "auto": "Auto"}[args.transport]
    try:
        info = inspect_bitfile(Path(args.bitfile))
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"Bitfile: {info.path}")
    print(f"Target class: {info.target_class}")
    print(f"Signature: {info.signature}")
    print(f"Registers: {len(info.registers)}; FIFOs: {', '.join(sorted(info.fifos))}")
    errors = validate_wec_bitfile(info, transport)
    if errors:
        print("FAIL: bitfile compatibility check", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2
    print("PASS: eChemTips register/FIFO contract and target family")
    print("Semantic hardware profile:")
    print("  Outputs: " + ", ".join(f"{name}={channel}" for name, channel in ANALOG_OUTPUT_CHANNELS.items()))
    print("  Inputs: " + ", ".join(f"{name}={channel}" for name, channel in ANALOG_INPUT_CHANNELS.items()))
    print("  Feedback signals: " + ", ".join(f"{name}={code}" for name, code in FEEDBACK_SIGNAL_CODES.items()))
    print("  Feedback actions: " + ", ".join(f"{name}={code}" for name, code in FEEDBACK_ACTION_CODES.items()))
    if not args.connect:
        print("Offline check only. Add --connect on the instrument PC to verify NI-RIO and the resource.")
        return 0
    try:
        from nifpga import Session
    except ImportError:
        print("FAIL: nifpga is not installed. Run: python -m pip install -e \".[fpga]\"", file=sys.stderr)
        return 3
    try:
        # no_run does not stop a VI already running on the device.
        with Session(str(info.path), args.resource, no_run=True) as session:
            print(f"PASS: opened {args.resource}; this check did not request Run")
            print(f"FPGA state: {session.fpga_vi_state.name}")
            print(f"Session exposes {len(session.registers)} registers and {len(session.fifos)} FIFOs")
    except Exception as exc:
        print(f"FAIL: could not open {args.resource}: {exc}", file=sys.stderr)
        print("Check NI-RIO installation, USB cable/power, NI MAX resource name, and bitfile target model.", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
