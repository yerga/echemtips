# Standalone Newport Z-picomotor tester

This is an **experimental commissioning program**, not an experiment added to
the main eChemTips sidebar. It uses the existing WEC-SPM FPGA target for piezo
approach and a separate USB connection for the Newport 8742. No bitfile changes
are required. X=port 1 and Y=port 2 are reserved but **never commanded**;
only Z=port 3 is used. The 8302 moves the linear stage carrying the piezo.

The Newport hardware/DLL path has not yet been verified on a real controller.
Simulation and fake-device tests do not establish physical safety. Do not leave
the first real tests unattended. Stop is a best-effort software command to both
controllers, not a hardware interlock; lost USB communication can prevent it.

## Windows installation

1. With the probe safely clear, install the **New Focus Picomotor Application**
   and its USB driver from Newport's [8742 product downloads](https://www.newport.com/p/8742).
   The manufacturer instructs installing the driver before the first USB connection.
   Follow the controller manual's power supply, mounting and grounding instructions.
   Controller startup/motor detection can make small motor movements.
2. Connect the 8302 to motor port **3**, power the controller using its intended
   supply, and connect USB. Follow the manual when connecting motor cables;
   do not change cables while outputs are energized.
3. In Newport's application, confirm an 8742 is discovered, Z is identified as a
   **Standard** motor, and motor 3 responds to a finite **one-step** move.
   Start with the pipette well away from the sample. Do not use indefinite jog.
4. Locate `UsbDllWrap.dll` and its native dependencies. The vendor's Python sample
   identifies the usual directory as
   `C:\Program Files\Newport\Newport USB Driver\Bin`. Your installation can differ.
   Use the actual driver directory, not the downloaded sample ZIP. The ZIP does
   not contain the DLLs. Do not copy vendor binaries into the Git repository.
5. Use the existing eChemTips environment, or create one with Python 3.11:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -e ".[fpga,picomotor]"
   ```

   The optional dependency is modern Python.NET, not the obsolete versions in
   Newport's sample. The DLL and its native dependencies must match the Python
   process architecture. If loading fails, check vendor-supported DLL architecture
   and .NET Framework prerequisites; do not replace the Newport USB driver with
   Zadig/libusb. NI-RIO drivers and a compatible existing bitfile remain necessary.
6. Verify ordinary eChemTips piezo motion/current calibration first. This tester
   loads the existing user settings **without modifying them**. Alternatively pass
   `--settings "path\settings.json"`. Review piezo ranges, bipolar flags, current
   gains, potential ratio, polarity and acquisition settings in that file before
   hardware connection. The tester shows a summary in its Connection tab.

## First try simulation

```powershell
python -m echemtips.picomotor_test
# After installing the editable package, this is equivalent:
echemtips-picomotor-test
```

1. Click **Connect devices**. Both devices are simulated unless `--hardware` is
   explicitly supplied. No Newport installation is needed for simulation.
2. Open **Approach**, then **Start repeated approach**. Synthetic calibration
   confirmations are preselected *only* in simulation: positive steps and
   0.01 µm/step. These are not values to transfer to your motor.
3. The synthetic surface starts beyond piezo reach. Watch the first no-contact
   approach retract, then a finite motor move, settling, and another approach.
   After enough coarse motion, contact produces a current response; the run ends
   with piezo Z back at initial Z. The coarse motor remains where it finished.
4. Repeat with maximum attempts = 1. It should finish with **budget reached**,
   no coarse movement, and piezo at initial Z.
5. Test **STOP BOTH DEVICES** during piezo approach and motor movement. Normal
   completion/no-contact withdrawal returns Z; an operator stop/fault holds/stops
   instead of blindly commanding a return from an uncertain state.

## Hardware connection and manual calibration

Close eChemTips control, LabVIEW and Newport's application so this program owns
both connections. Do not run two control programs simultaneously.

```powershell
python -m echemtips.picomotor_test --hardware `
  --dll-directory "C:\Program Files\Newport\Newport USB Driver\Bin" `
  --bitfile "C:\path\compatible-target.lvbitx" --resource RIO0
```

1. Click **Connect devices** and read the startup warning. FPGA initialization
   changes piezo and potential outputs immediately; it is not a no-motion attach.
   The tester never automatically resets/reinitializes the FPGA between attempts.
2. If several Newport USB devices are discovered, connection refuses to choose
   arbitrarily and lists keys. Enter the intended **device key**, then reconnect.
   The tester checks identity, Standard motor type on port 3, idle state and errors.
   It does not issue motor detection, homing, factory-reset or persistent-save commands.
3. In **Calibration**, confirm manual clearance. With the pipette far from the
   surface, use **one positive step** and observe the linear-stage direction.
   Use a negative step if necessary to establish which sign decreases the gap.
   Set **Coarse direction toward surface** and confirm it. Do not infer this sign
   from the increasing-Z piezo convention; they are independent mechanisms.
4. Measure actual displacement using a calibrated microscope/camera or independent
   displacement measurement. Increase finite moves cautiously while safely clear.
   Divide displacement by step count, repeat under the real installed load,
   and test the direction that will be used for approach. Reverse steps may have
   different displacement and are not a reliable way to return exactly.
5. Enter a **conservative upper displacement per step**, covering repeatability
   and measurement uncertainty, and confirm calibration. This is a planning bound,
   not an encoder calibration. Use a starting coarse increment well below the
   piezo approach span. This version limits each increment to at most half the
   span using the entered bound, plus 5000 steps and 1000 steps/s hard ceilings.
   These software ceilings do not establish safe motion for an uncalibrated assembly.
6. Choose initial piezo Z, approach limit/rate, withdrawal rate, approach potential,
   selected current and contact **magnitude** threshold (both signs). Select
   small attempt and cumulative-step budgets and adequate mechanical/electrical
   settling. Confirm initial-Z clearance and remaining coarse mechanical travel.
   A coarse step is allowed only after a no-contact approach reaches its limit
   and the piezo withdrawal waypoint finishes.
7. First run with maximum attempts = 1 (no automatic coarse retry). Then test
   a small two-attempt run. Verify physical withdrawal, motor direction, current
   baseline and reapproach with the camera before increasing either budget.
8. On contact, the piezo automatically returns to initial Z and the run ends.
   The motor is **not** returned to its starting count. Count readback is the
   controller's internal pulse count, not measured position or homing status.

## Faults, stop and recording

- Normal no-contact completion is retryable; an operator stop, driver fault,
  missing completion, pulse-count mismatch or timeout is not. Coarse move commands
  are never automatically resent after an uncertain USB result.
- Current must settle below the threshold before arming approach. Persistent
  above-threshold current or missing data stops this first tester rather than
  interpreting an electrical transient as permission to move the coarse stage.
- **STOP BOTH DEVICES** sends Newport `AB` and the existing FPGA emergency stop
  independently. On hardware, reconnect explicitly before another run; reconnect
  again incurs the documented FPGA startup output changes. UI stays responsive,
  but a blocked/lost USB call can delay or defeat a software stop. Keep physical
  power/stop controls accessible; software cannot guarantee crash prevention.
- CSV contains all acquired samples; the bounded live plot is not the recording.
  JSON stores instrument settings, calibration confirmations/bounds, limits,
  approach parameters, attempt results and motion events. A `.motion.jsonl`
  companion appends events during the run, including coarse commands **before**
  sending them, so an interrupted run has an audit trail.
- Manual calibration moves display pulse counts but are not experiment recordings.
  Note the independently measured displacement and how it was measured.
- Keep the controller and motor cabling positioned appropriately to limit
  electrical pickup. The settling interval is adjustable; verify noise after
  every motor move before trusting contact detection.

The reusable USB transport and supervisor can support a later integration into
approach/scanning experiments, but that is intentionally outside this first test.
