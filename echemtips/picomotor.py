"""Optional Newport 8742 USB transport and bounded, open-loop Z commands.

No Newport library is imported until connecting hardware. The vendor driver is
retained; no libusb/Zadig driver replacement or controller reset is performed.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time


class NewportUSB:
    """Adapt Newport's UsbDllWrap.dll, as used in its supplied Python sample."""

    def __init__(self, dll_directory: str, device_key: str = ""):
        self.directory = Path(dll_directory).expanduser()
        self.key = device_key
        self.usb = None
        self._dll_handle = None

    @staticmethod
    def _check(status, operation):
        """Fail closed on a nonzero vendor communication status."""
        if int(status) != 0:
            raise RuntimeError(f"Newport {operation} returned status {status}; no automatic retry of motion commands.")

    def connect(self):
        """Discover one explicitly selected 8742; discovery sends no motion commands."""
        if sys.platform != "win32":
            raise RuntimeError("Newport's DLL transport requires Windows; use simulation here.")
        dll = self.directory / "UsbDllWrap.dll"
        if not dll.is_file():
            raise ValueError("Select the Newport USB Driver Bin directory containing UsbDllWrap.dll.")
        try:
            import clr
            from System.Text import StringBuilder
        except ImportError as exc:
            raise RuntimeError('Install the optional dependency: python -m pip install -e ".[picomotor]"') from exc
        self._builder = StringBuilder
        self._dll_handle = os.add_dll_directory(str(self.directory.resolve()))
        sys.path.append(str(self.directory.resolve()))
        clr.AddReference(str(dll.resolve()))
        from Newport.USBComm import USB
        self.usb = USB(True)
        try:
            if not self.usb.OpenDevices(0, True):
                raise RuntimeError("Newport USB discovery failed. Install its driver and close the vendor application.")
            table = self.usb.GetDeviceTable()
            keys = [str(k) for k in table.Keys]
            if self.key:
                if self.key not in keys:
                    raise RuntimeError(f"Device key not found. Discovered keys: {keys}")
            elif len(keys) == 1:
                self.key = keys[0]
            else:
                raise RuntimeError(f"Select a device key explicitly. Discovered keys: {keys}")
            identity = self.query("*IDN?")
            if "8742" not in identity:
                raise RuntimeError(f"This tester requires an 8742, not {identity!r}.")
            return identity
        except Exception:
            self.close()
            raise

    def query(self, command: str) -> str:
        """Send one LF-terminated query and validate its nonempty response."""
        if self.usb is None:
            raise RuntimeError("Newport USB is not connected.")
        result = self._builder(256)
        self._check(self.usb.Query(self.key, command.rstrip()+"\n", result), command)
        text = str(result.ToString()).strip()
        if not text:
            raise RuntimeError(f"Empty Newport response to {command}; communication is uncertain.")
        return text

    def write(self, command: str):
        """Write once; never resend a relative move after an uncertain USB result."""
        if self.usb is None:
            raise RuntimeError("Newport USB is not connected.")
        self._check(self.usb.Write(self.key, command.rstrip()+"\n"), command)

    def close(self):
        """Release USB and DLL resources without resetting or homing the motor."""
        try:
            if self.usb is not None:
                self.usb.CloseDevices()
        finally:
            self.usb = None
            if self._dll_handle is not None:
                self._dll_handle.close()
                self._dll_handle = None


class PicomotorZ:
    """Z-only driver, fixed to motor port 3; X=1 and Y=2 remain unused."""
    axis = 3

    def __init__(self, transport):
        self.transport = transport

    def connect(self):
        """Check identity, idle state, motor type and controller errors."""
        identity = self.transport.connect()
        if not self.motion_done():
            raise RuntimeError("Z motor is already moving; stop it in Newport's application first.")
        if int(self.transport.query("3QM?")) != 3:
            raise RuntimeError("Z port 3 must report a Standard motor (8302). Check cabling/type in Newport's application.")
        self.check_error()
        return identity

    def check_error(self):
        """Surface controller faults instead of treating command acceptance as motion."""
        code = int(self.transport.query("TE?"))
        if code:
            raise RuntimeError(f"Newport controller error {code}; inspect with the vendor application.")

    def position(self) -> int:
        """Return controller pulse count, NOT encoder position or micrometres."""
        return int(self.transport.query("3TP?"))

    def motion_done(self) -> bool:
        """MD? confirms pulse-train completion, not physical clearance."""
        value = self.transport.query("3MD?")
        if value not in {"0", "1"}:
            raise RuntimeError(f"Invalid Newport motion response: {value!r}")
        return value == "1"

    def move(self, steps: int, rate: int):
        """Start a finite relative Z move after idle/error checks."""
        if not isinstance(steps, int) or isinstance(steps, bool) or not 0 < abs(steps) <= 5000:
            raise ValueError("One coarse move must be 1–5000 integer steps.")
        if not isinstance(rate, int) or isinstance(rate, bool) or not 1 <= rate <= 1000:
            raise ValueError("This test limits motor velocity to 1–1000 steps/s.")
        if not self.motion_done():
            raise RuntimeError("Z motor is not idle.")
        self.check_error()
        self.transport.write(f"3VA{rate}")
        self.check_error()
        self.transport.write(f"3PR{steps}")
        self.check_error()

    def abort(self):
        """Best-effort controller-wide abrupt stop, also stopping unexpected axes."""
        self.transport.write("AB")

    def close(self):
        """Close transport; callers must stop before closing an active motor."""
        self.transport.close()


class SimulatedPicomotor:
    """Synthetic pulse counter coupled to a movable surface for tester validation."""
    axis = 3

    def __init__(self, backend, clock=time.monotonic):
        self.backend, self.clock = backend, clock
        self.count = 0
        self.deadline = 0.
        self.pending = 0

    def connect(self):
        """Return the synthetic controller identity without accessing hardware."""
        return "Simulated Newport 8742 · Z=3 · 0.01 µm/step (synthetic)"

    def position(self):
        """Report completed synthetic pulses, not a measured stage position."""
        self.motion_done()
        return self.count

    def motion_done(self):
        """Apply a completed move to the synthetic surface and report idle state."""
        if self.pending and self.clock() >= self.deadline:
            self.count += self.pending
            self.backend.coarse_offset_um += self.pending * .01
            self.pending = 0
        return not self.pending

    def check_error(self):
        """Simulation has no controller error queue."""
        pass

    def move(self, steps, rate):
        """Schedule a finite synthetic pulse train."""
        if not self.motion_done():
            raise RuntimeError("Simulated motor is busy.")
        self.pending = steps
        self.deadline = self.clock() + abs(steps)/rate

    def abort(self):
        """Cancel pending synthetic motion without claiming partial displacement."""
        # Displacement of an interrupted real move is uncertain. The simulation
        # retains only completed moves rather than claiming a physical position.
        self.pending = 0

    def close(self):
        """Cancel any pending simulation movement."""
        self.abort()
