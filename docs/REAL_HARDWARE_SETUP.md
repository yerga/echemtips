# eChemTips real-hardware setup

This Python host keeps `FPGA Target.vi` on the NI device. It implements the host side of the exact WEC-SPM FIFOs: 14 signed-I16 words per motion waypoint and 14 signed-I16 words per acquired sample. The fixed initial hardware configuration uses:

- X, Y, and Z piezo outputs on AO0, AO1, and AO2
- X, Y, and Z measured positions on AI0, AI1, and AI2
- Voltage 1 and Voltage 2 on AO3 and AO4; the command-voltage ratio applies to Voltage 1
- Current 1 and Current 2 on AI3 and AI4, with each amplifier sensitivity entered in V/nA
- AO5–AO7 and AI5–AI7 left unused; eChemTips has no picomotor controls
- FPGA-side feedback for the approach and FPGA-side waypoints for CV
- Scan Hopping + CV as staged waypoint programs: XY positioning at retracted Z and a selected Current 1 or Current 2 stop-on-feedback approach, followed by CV and a contact-relative retract only after contact is confirmed at each pixel

## Required instrument-PC software

Use a 64-bit Windows computer supported by the chosen NI-RIO release and the
exact NI R Series model. Python 3.11 (64-bit) is the recommended initial
commissioning environment because it is conservative and reproducible; a
newer interpreter is not considered hardware-supported here until it has
passed this entire procedure. Install and record:

1. A compatible NI-RIO driver with NI MAX and FPGA Interface support. Reboot
   when the NI installer requests it.
2. Python 3.11 from python.org. Enable the launcher during installation.
3. Git, then clone eChemTips into a permanent development folder.
4. In PowerShell, create an isolated environment and install the project:

   ```powershell
   cd C:\path\to\echemtips
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -e ".[fpga]"
   python -m unittest discover -v
   python run_echemtips.py --smoke-test
   ```

If PowerShell blocks environment activation, use
`.venv\Scripts\python.exe` explicitly rather than weakening the machine's
execution policy. PySide6 and PyQtGraph are installed by the project; the
`fpga` extra installs `nifpga`. The Python package still requires the native NI
driver and cannot replace it.

In NI MAX, confirm that the device appears under Devices and Interfaces and
note its RIO resource name, normally `RIO0`. Record the actual versions in the
commissioning record below. NI documents the FPGA Interface Python API as the
host interface for RIO devices and the USB R Series hardware setup at:

- <https://www.ni.com/en/support/documentation/supplemental/16/python-resources-for-ni-hardware-and-software.html>
- <https://knowledge.ni.com/KnowledgeArticleDetails?id=kA03q000000YHblCAG&l=en-US>
- <https://download.ni.com/support/manuals/374974a.pdf>

## Select a target image for your device

Use a `.lvbitx` compiled for the exact NI model and preserving the
[compatible target contract](HARDWARE_INTEGRATION.md). The filename is arbitrary;
there is no required build signature. USB-7856R is the development/test
reference, not the only permitted model. Other models require their own
source/build review and the physical checks below before use.

Select the bitfile in Settings and save defaults. `ECHEMTIPS_BITFILE` supplies
a default when no saved configuration overrides it. No adjacent private
LabVIEW directory is searched automatically. Existing saved paths and transport
choices are preserved, including PCIe/PXI configurations.

Copy the private image to a controlled local folder on the instrument PC. Do
not add it to Git. To select it without relying on a remembered UI value:

```powershell
$env:ECHEMTIPS_BITFILE = "C:\instrument\private\target.lvbitx"
python run_hardware_check.py --bitfile $env:ECHEMTIPS_BITFILE
```

Preserve the filename, signature printed by the checker, source, and date in
the laboratory configuration record.

## Cabling and channel worksheet

The supported logical channel map is fixed in Python, but AO/AI names are not
SCB screw-terminal numbers. Before wiring, identify the exact connector block
model and use your device's manual plus its labelled terminal diagram. For the
USB-7856R reference setup, NI's USB
R Series guide specifies the SHC68-68-RMIO cable and SCB-68A for the MIO
connector. If the installed block is labelled only SCB-68 or has a different
part number, stop and verify compatibility and terminal numbering.

Complete this worksheet from the actual manuals and continuity checks. Never
copy terminal numbers from another R Series model:

The NanoDrive positioner and VA-10M amplifier below are reference-setup
examples, not required brands. Substitute your own controllers and amplifier,
and verify their ranges, sensitivity, polarity and wiring independently.

| FPGA channel | Purpose | SCB terminal | Destination BNC/controller | Signal reference | Verified |
| --- | --- | --- | --- | --- | --- |
| AO0 | X piezo command | ___ | NanoDrive X command ___ | ___ | ☐ |
| AO1 | Y piezo command | ___ | NanoDrive Y command ___ | ___ | ☐ |
| AO2 | Z piezo command | ___ | NanoDrive Z command ___ | ___ | ☐ |
| AO3 | E1 command | ___ | VA-10M command ___ | ___ | ☐ |
| AO4 | E2 command | ___ | Reserved/current setup ___ | ___ | ☐ |
| AI0 | X measured position | ___ | NanoDrive X monitor ___ | ___ | ☐ |
| AI1 | Y measured position | ___ | NanoDrive Y monitor ___ | ___ | ☐ |
| AI2 | Z measured position | ___ | NanoDrive Z monitor ___ | ___ | ☐ |
| AI3 | i1 amplifier output | ___ | VA-10M current output ___ | ___ | ☐ |
| AI4 | i2 amplifier output | ___ | Second current output ___ | ___ | ☐ |

AO5–AO7 and AI5–AI7 remain disconnected and unused. eChemTips has no
picomotor behavior. Label both ends of every cable. Verify whether each input
is referenced single-ended, non-referenced single-ended, or differential and
wire AIGND/AISENSE exactly as the NI manual requires. Do not create multiple
unplanned ground paths through the computer, FPGA, positioner, amplifier, and
electrochemical cell.

For the Nano-3D200 mechanical range and controller connection, retain the exact
instrument manual with the setup; the manufacturer product sheet is
<https://www.madcitylabs.com/catalog/nano3d200.pdf>. For the VA-10M, use the
manual matching the installed amplifier revision to verify command input span,
current-output sensitivity, headstage limits, polarity, bandwidth, grounding,
and overload behavior.

## Connection changes physical outputs

Running the deployed FPGA executes an unconditional startup frame before its pause-controlled loops. It sets AO0/X and AO1/Y to raw `0x3FFF` (about +5 V), AO2/Z to 0 V, and AO3/E1 plus AO4/E2 to 0 V. The corresponding physical X/Y/Z positions shown by eChemTips are calculated from the configured piezo ranges and bipolar settings; measured positions can differ. `External Pause` cannot suppress these startup writes.

The GUI therefore describes the exact expected startup outputs and requires confirmation before running an NI target. The native driver then verifies the applied-output registers, zero line number, pause/stop controls, and empty waiting state before the application reports a successful connection. A mismatch asserts the emergency stop and rejects the connection. Put the probe in a safely retracted condition and ensure the stage can accept this movement before confirming. Programmatic callers must explicitly pass `allow_startup_actuation=True` to `NIFPGABackend.connect()`.

The Python app validates target family, required register datatypes/access roles, and FIFO datatypes, directions, and depths before opening a session. It will refuse a PCIe image while **USB R Series** is selected.

## Commissioning order

Perform each stage separately and record evidence. A successful software test
does not authorize the next physical stage unless the independent measurement
also matches expectation.

1. Back up the existing working LabVIEW configuration and record all controller
   and amplifier settings. Remove the probe from collision range. Disconnect or
   inhibit the piezo high-voltage outputs and disconnect the electrochemical
   cell from the E1 command while retaining only connections required for the
   current stage.
2. With the NI device disconnected or outputs physically inhibited, verify the Python environment:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python -c "import PySide6, pyqtgraph, nifpga; print('Python dependencies OK')"
   python -m unittest discover -v
   python run_echemtips.py --smoke-test
   ```

3. Run the offline contract check with the private target path:

   ```powershell
   py run_hardware_check.py --bitfile "C:\path\to\target.lvbitx"
   ```

   Confirm the target model and signature against your laboratory configuration
   record, required register types/access, FIFO
   directions, I16 element types, and frame divisibility. Do not continue on a
   warning you do not understand.
4. Connect the device and verify the device/resource in NI MAX. With physical outputs
   inhibited, check that NI-RIO can open the target without running the FPGA VI:

   ```powershell
   py run_hardware_check.py --bitfile "C:\path\to\target.lvbitx" --connect --resource RIO0
   ```

   `--connect` downloads/opens the bitfile using `no_run=True`; it does not run a waypoint.

5. With controller inputs still physically disconnected, start
   `python run_echemtips.py`. In Settings select **NI FPGA** and confirm **USB R
   Series**, the NI MAX resource, and the supported target. Enter the measured
   X/Y/Z full travel, bipolar mode, Current 1/2 sensitivity in V/nA, E1 command
   ratio, acquisition values, and a permanent data folder. Save defaults.
   The FPGA-ready timeout controls the startup handshake. The command-watchdog margin is added to the duration calculated from the requested motion distances, rates, and CV sweep.
6. Prepare a meter/oscilloscope and accept the connection warning. Verify the
   unavoidable startup levels at AO0–AO4 before attaching downstream devices.
   Compare raw connector voltage with the GUI readback and expected command
   conversion. Disconnect and investigate any wrong channel, sign, or level.
7. Connect the VA-10M command and output with a controlled electrical load in
   place of the cell. Begin at E1 = 0 V. Verify AO3:E1 command ratio at several
   small positive and negative points, AI3 current scale/sign using a known
   resistance or test signal, zero noise, saturation behavior, and safe Stop.
   Repeat AI4 only if Current 2 is installed.
8. Connect the NanoDrive command/monitor wiring while keeping its high-voltage
   outputs mechanically safe. Enable one axis at a time. Command a small, slow
   move and independently measure AO voltage, monitor-input direction, physical
   direction, scale, and endpoint. Test X and Y first; test Z last with the
   probe far from the surface.
9. Run Guided preflight with the documented open and resistor fixtures. Save
   its report. Characterize a sacrificial/known pipette before using a critical
   probe.
10. With a dummy contact signal or other controlled electrical load, test a
    standalone Approach using a conservative Z interval and slow speed. Verify
    both threshold directions, automatic contact, manual acceptance, no-contact
    end-of-travel, settling, retract, controlled Stop, recording status, and the
    requirement to reconnect after hardware cancellation.
11. Only after the dummy-load protocol succeeds, prepare the electrochemical
    cell and run an Approach + CV with independently justified potential limits,
    approach direction, threshold, and physical travel. Confirm that CV never
    begins after end-of-travel without contact.
12. Commission hopping CV first as 1×1, then 2×2 over a small XY range.
    **Initial approach Z** is used only for the first hop. Verify each retract
    is away from measured contact by **Retract distance from contact**. Confirm
    CSV `scan_pixel` and JSON `scan_grid` agree with observed movement. Test
    serpentine before raster; verify extra end-of-line retraction before X
    flyback. Repeat the same staged process for hopping I–t.

## Commissioning record

Copy this table into the instrument log and attach checker output, screenshots,
scope/meter captures, preflight JSON, and representative recordings.

| Item | Recorded value |
| --- | --- |
| Date, operator, instrument ID | ___ |
| Windows edition/build, 64-bit | ___ |
| NI-RIO and NI MAX versions | ___ |
| NI model, serial and resource | ___ |
| Python version and architecture | ___ |
| eChemTips Git commit | ___ |
| `nifpga`, PySide6, PyQtGraph versions | ___ |
| Bitfile filename and reported signature | ___ |
| SCB model/part number and cable/connector | ___ |
| NanoDrive/Nano-3D200 IDs and ranges | ___ |
| VA-10M/headstage IDs, revision, sensitivity, command span | ___ |
| Grounding/reference configuration | ___ |
| Startup AO0–AO4 measured values | ___ |
| E1 command ratio/polarity evidence | ___ |
| X/Y/Z command, readback, direction, scale evidence | ___ |
| i1/i2 scale, sign, noise, overload evidence | ___ |
| Stop/interlock evidence | ___ |
| Dummy approach/contact result | ___ |
| 1×1 and 2×2 scan recording names | ___ |
| Deviations or unresolved restrictions | ___ |

## Commissioning limitations and stop behavior

Completed programs can be followed by another program in the same connection. Completion requires the entire submitted waypoint count and the target's waiting indication, then a final snapshot of available FIFO samples. The host retains the pixel mapping until that batch is processed. This behavior is tested with a fake NI interface; physical commissioning is still required.

Programs are no longer limited to the target FIFO's 585 complete frames. The host configures 1,048,576 elements of host-side DMA memory, initially submits 512 complete 14-word frames, and refills in 128-frame chunks while tracking submitted and executed lines. The generic driver ceiling is 65,535 frames. Scan plans retain a stricter 32,767-tag guard because acquired samples expose a signed-I16 line tag and target narrowing above its positive range has not been physically confirmed. The total Scan Hopping + CV plan uses one initial Z-only retract plus `4 + 3 × cycles` waypoints per pixel, and still stages approach separately from CV so end-of-travel can never start electrochemistry without confirmed contact.

Normal contact completion uses FPGA type 2 (stop on feedback), not `External Stop` or the session-wide one-shot `EndCurrentLine` gate. Python waits for `WaitingForWayPoints` and the expected line count, drains the completed acquisition snapshot, and only then submits the follow-up. Manual acceptance and a no-contact endpoint hold use a separately classified, reusable secondary-comparator request; its threshold is restored and verified before proceeding. A true Stop can interrupt the target between fields of its 14-word acquisition frame. Python therefore retains complete pre-stop samples, discards all post-stop residual words, leaves `External Stop` asserted, and requires disconnection plus FPGA reinitialization before another command. This conservative cancellation boundary avoids silently decoding misaligned measurements without requiring a new bitfile.

The experiment-local **Accept current Z as contact and continue** button is an explicit operator override for an active approach. It records the current Z as manually accepted contact and submits the gated CV or I–t continuation only after the approach FIFO is fully drained. The toolbar **End waypoint** button does not confirm contact and must not be used as a substitute. Use manual acceptance only while independently observing a safe probe state.

Hopping retraction is deliberately expressed as a positive distance rather than a signed Z offset. The host determines the away-from-surface direction from the configured initial and limit Z values, reads the FPGA's `Applied Z` at confirmed contact, and compiles the continuation waypoint from that contact position. Acquired approach samples provide a guarded fallback if an applied-position readback is outside the configured approach interval.

Emergency stop asserts both `External Pause` and `External Stop`, verifies their register values, and permanently latches the current Python driver instance. FPGA `Internal Stop`, an unexpected NI VI state, impossible line-counter progress, or an expired command watchdog also latches the driver. None of these software checks replaces a physical emergency stop or guarantees that analog outputs are de-energized.

After startup authorization, the application opens the session with `no_run=True`, resets the FPGA, verifies `NotRunning`, then configures and runs it. This supports reconnecting to a target left running by a previous session. Reset ends previous execution and can change outputs; close other NI/LabVIEW controllers and keep actuators disabled during this step. No bitfile change is required. The command-line `--connect` checker only opens the session and reports state; it does not perform this reset/run workflow.

An idle Potential 1/2 change is sent as a one-waypoint FPGA jump, not as a host pulse: success requires the target line counter and waiting state to complete that waypoint and the corresponding `Applied Voltage` indicator to equal the requested raw value. An on-the-fly change is accepted only while that voltage axis is executing, remains asserted until the same applied-value acknowledgement, and is then cleared by the host. A missing acknowledgement pauses and latches the session for reinitialization. Operator and FPGA feedback pauses continue to receive health checks, but their acknowledged duration is excluded from the physical-motion watchdog deadline.

- The NI session is opened with `no_run=True` and reset; the target is then configured and paused before it is run. Reset failure prevents startup and closes the session.
- FIFO waypoints are initially filled while paused; long programs continue through bounded, complete-frame host-side refills after execution starts.
- Contact completion uses the existing type-2 waypoint, `WaitingForWayPoints` and `LineNumber`, without stopping acquisition or relying on the one-shot `EndCurrentLine` gate.
- Stop safely cancels physical motion but deliberately retires that acquisition stream; reinitialize and reconnect before another submission. It does not promise to zero outputs.
- Emergency Stop asserts the FPGA `External Stop` control.
- The host checks all position ranges, voltages, and positive velocities before encoding a waypoint.

This is research control software. The source protocol and conversions have automated tests, but the physical calibration, wiring polarity, FPGA compilation for the exact USB model, and hardware interlocks must be verified on the instrument.

For symptom-based diagnosis and recovery, see
[Hardware troubleshooting](TROUBLESHOOTING.md).
