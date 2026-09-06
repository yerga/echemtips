# eChemTips real-hardware setup

This Python host keeps `FPGA Target.vi` on the NI device. It implements the host side of the exact WEC-SPM FIFOs: 14 signed-I16 words per motion waypoint and 14 signed-I16 words per acquired sample. The first commissioned profile uses:

- Current 1 on AI3, with the amplifier sensitivity entered in V/nA
- Voltage 1 on AO3, with the WEC-SPM command-voltage ratio
- X, Y, and Z piezo outputs on AO0, AO1, and AO2
- FPGA-side feedback for the approach and FPGA-side waypoints for CV
- Scan Hopping + CV as staged waypoint programs: XY positioning at retracted Z and a Current 1 pause-on-contact approach, followed by CV/retraction only after contact is confirmed at each pixel

## Required instrument-PC software

Use a Windows computer supported by the installed NI-RIO release and the exact USB R Series model. Install:

1. The NI-RIO driver and NI MAX.
2. Python 3.10 or newer with Tk support.
3. This project and the NI FPGA Python API:

   ```powershell
   py -m pip install -e ".[fpga]"
   ```

In NI MAX, confirm that the device appears under Remote Systems/Devices and Interfaces and note its RIO resource name, normally `RIO0`.

## USB-7856R target image

The commissioned `wecspm_FPGATarget2_FPGATarget_MAn-McsWIiw.lvbitx` reports target class `USB-7856R` and signature `8229BC0D5A4935D854D1286878CEE54A`. It matches the native driver's WEC-SPM host interface but is not distributed in this repository. eChemTips uses `ECHEMTIPS_BITFILE` when set and also detects that filename in a sibling `WEC_SPM/FPGA Bitfiles` archive; otherwise select the private target explicitly in Settings. The older `FPGAProject_FPGATarget_FPGATarget2_ACEEEF6E.lvbitx` is a PCIe-7852R image and must not be selected for the USB device.

The Python app validates target family, required register datatypes/access roles, and FIFO datatypes, directions, and depths before opening a session. It will refuse a PCIe image while **USB R Series** is selected.

## Commissioning order

1. Disconnect or disable the piezo high-voltage amplifier and keep the probe clear of the surface.
2. Run the offline contract check with the private target path:

   ```powershell
   py run_hardware_check.py --bitfile "C:\path\to\target.lvbitx"
   ```

3. With the NI USB FPGA connected, verify that NI-RIO can open the target without running the FPGA VI:

   ```powershell
   py run_hardware_check.py --bitfile "C:\path\to\target.lvbitx" --connect --resource RIO0
   ```

   `--connect` downloads/opens the bitfile using `no_run=True`; it does not run a waypoint.

4. Start `py run_echemtips.py`. In Settings select **NI FPGA** and confirm **USB R Series**, the NI MAX resource, and `wecspm_FPGATarget2_FPGATarget_MAn-McsWIiw.lvbitx`.
5. Enter the measured X/Y/Z full travel, bipolar mode for each piezo controller, Current 1 sensitivity in V/nA, and Voltage 1 command ratio. Save and connect.
   The FPGA-ready timeout controls the startup handshake. The command-watchdog margin is added to the duration calculated from the requested motion distances, rates, and CV sweep.
6. With actuators still disabled, verify Watch Current and Voltage 1 scaling. A known amplifier test signal is strongly recommended.
7. Enable one piezo axis at a time. Command a small, slow move and verify direction and travel externally. Then commission Z with the probe far from the surface.
8. Only after those checks, run Approach + CV with a conservative Z end position, slow approach, and a verified Current 1 threshold/polarity.
9. Commission Scan Hopping + CV first as a 1 x 1 scan, then 2 x 2 with a small XY range. Confirm the saved `scan_pixel`, `scan_row`, and `scan_column` columns agree with physical movement before expanding the grid.

## Commissioning limitations and stop behavior

Completed programs can be followed by another program in the same connection. Completion requires the entire submitted waypoint count and the target's waiting indication, then a final snapshot of available FIFO samples. The host retains the pixel mapping until that batch is processed. This behavior is tested with a fake NI interface; physical commissioning is still required.

Programs are no longer limited to the target FIFO's 585 complete frames. The host configures 1,048,576 elements of host-side DMA memory, initially submits 512 complete 14-word frames, and refills in 128-frame chunks while tracking submitted and executed lines. The generic driver ceiling is 65,535 frames. Scan plans retain a stricter 32,767-tag guard because acquired samples expose a signed-I16 line tag and target narrowing above its positive range has not been physically confirmed. The total Scan Hopping + CV plan uses one initial Z-only retract plus `4 + 3 × cycles` waypoints per pixel, and still stages approach separately from CV so end-of-travel can never start electrochemistry without confirmed contact.

Normal Stop asserts the target stop control, waits for `WaitingForWayPoints`, clears the stop/pause handshake, drains final acquisition data, and leaves the initialized session reusable. If that acknowledgement times out, or after an uncertain FIFO write, target fault, or Emergency Stop, disable the actuators, disconnect Python, reset/reinitialize the FPGA using NI MAX/LabVIEW, then reconnect.

Emergency stop asserts both `External Pause` and `External Stop`, verifies their register values, and permanently latches the current Python driver instance. FPGA `Internal Stop`, an unexpected NI VI state, impossible line-counter progress, or an expired command watchdog also latches the driver. None of these software checks replaces a physical emergency stop or guarantees that analog outputs are de-energized.

The application refuses to attach to an already running FPGA. `no_run=True` prevents a new Run request but does not stop an existing VI. Opening a different bitfile can download it, and initialization can change outputs; keep actuators disabled during this step.

- The NI session is opened with `no_run=True`; the target is configured and paused before it is run.
- FIFO waypoints are initially filled while paused; long programs continue through bounded, complete-frame host-side refills after execution starts.
- Stop safely cancels and permits another submission only after the FPGA waiting-state acknowledgement. It does not reset the FPGA or promise to zero outputs.
- Emergency Stop asserts the FPGA `External Stop` control.
- The host checks all position ranges, voltages, and positive velocities before encoding a waypoint.

This is research control software. The source protocol and conversions have automated tests, but the physical calibration, wiring polarity, FPGA compilation for the exact USB model, and hardware interlocks must be verified on the instrument.
