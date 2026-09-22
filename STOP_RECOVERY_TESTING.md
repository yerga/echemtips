# Experimental stop/recovery trial — NOT the production app

Branch: `experimental/stop-recovery`.

This checkout deliberately leaves the normal GUI and its Stop behavior unchanged.
Run the **separate terminal diagnostic** below, not `python -m echemtips`, to test
the new behavior. The diagnostic prints an experimental banner. It never reads
or writes the normal GUI settings automatically, and never starts a GUI worker.

The objective is to interrupt a program, discard uncertain data, identify a new
sample boundary, and submit another program **without resetting the FPGA during
recovery**. The FIRST connection still uses the normal reset/start sequence!

## Important limitations and risks

- No NI hardware test has been performed by the developer on this version.
- The alignment test is empirical, not an FPGA-provided frame acknowledgement.
  It temporarily writes two randomly selected, distinct `LineNumber` markers
  while idle, looks for six consecutive occurrences at a unique frame phase,
  checks that the same phase holds for both markers and the restored line
  number, and discards the residual partial frame. False alignment is still a
  risk until independently checked against known input signals.
- Marker/tail data are logged raw and not decoded as experiment data. Diagnostic
  sample elapsed times omit discarded intervals; use the log's host monotonic
  timestamps for stop latency. Do not use these logs as scientific recordings.
- Reset-free recovery stops/restarts the command FIFO, asserts Stop while
  draining data, then clears Stop. Unexpected queued output changes are a test
  failure. Readback checks cannot detect every short transient: external output
  measurements are essential.
- This does not yet validate interrupted CVs, contact/feedback pauses, scans,
  near-surface approaches, arbitrary calibrations or long-term reliable use.
- A failure latches the existing emergency stop; it does NOT reset and retry.
  Ctrl+C also requests the software emergency stop. Neither is a physical
  emergency interlock, and USB/driver failure can prevent software stopping.
- X/Y are not commanded by the trial after initial connection. The initial
  FPGA startup sets X/Y to about +5 V and Z/E1/E2 to 0 V. Disable actuator and
  amplifier command connections before EVERY invocation, including retries.

## 1. Install separately on the Windows instrument PC

Close LabVIEW and the stable eChemTips app. Keep the existing stable folder and
virtual environment. Use the same NI-RIO installation that already works.
In PowerShell, from your development directory:

```powershell
git clone --branch experimental/stop-recovery --single-branch https://github.com/yerga/echemtips.git echemtips-stop-recovery
cd echemtips-stop-recovery
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[fpga]"
git branch --show-current
```

Use an installed supported Python version if 3.11 is unavailable (the tests here
also run on 3.13). Do not install this checkout into the stable app's environment.
No activation or PowerShell execution-policy changes are needed with these paths.
The branch command must print `experimental/stop-recovery`.

Create an isolated settings copy:

```powershell
New-Item -ItemType Directory -Force .echemtips
Copy-Item "$env:APPDATA\eChemTips\settings.json" .echemtips\trial-settings.json
```

If the stable app uses `ECHEMTIPS_SETTINGS_PATH`, copy that file instead. If no
file exists, explicitly save defaults in the stable app first, then close it.
Inspect the COPY: resource, bitfile absolute path, ranges, bipolar configuration,
sample averaging, command-voltage ratio and amplifier gains must match your
known-good setup. The bitfile stays outside Git; do not copy legacy assets into
the checkout. The `.echemtips` and `data` folders are Git-ignored.

## 2. Run software tests first (no device needed)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_stop_recovery_trial.py -v
.\.venv\Scripts\python.exe -m echemtips.stop_recovery_trial --help
```

All tests must pass. These use synthetic streams, not an NI emulator or physical
device, and do not establish hardware safety.

## 3. Disconnected-output test — mandatory first hardware stage

1. Mechanically keep the pipette well clear of any surface. Disable/disconnect
   piezo command inputs and amplifier command inputs. Leave USB and FPGA powered.
2. Ensure LabVIEW and all other eChemTips processes are closed.
3. Connect suitable high-impedance measuring equipment to AO0–AO4, following your
   established wiring/grounding procedure. Never short outputs or connect two
   driven outputs together. Initially use a scope for transient checks if available.
4. Run:

   ```powershell
   .\.venv\Scripts\python.exe -m echemtips.stop_recovery_trial --settings .echemtips\trial-settings.json --cycles 3
   ```

5. Read the startup warning and type `OUTPUTS DISCONNECTED` only when true.
6. The test submits a 20-second stationary hold (over 585 hold waypoints), reads
   briefly, cancels it, attempts rearm, then submits a new 0.2-second hold. This
   deliberately tests a queue larger than the target-side FIFO. It repeats three
   times. No intentional piezo or potential changes occur after startup.
7. Verify all five outputs remain at their post-startup levels during stop/rearm.
   A completed console message is NOT enough: a pulse or step is a failure.
8. Collect the `data/stop-recovery/*.jsonl` file and the measured output traces.
   Confirm each cycle has `cancelled`, `experimental_rearm`, `cycle_completed`,
   and no `failure`. There is only one initial connection/reset per invocation.

## 4. Check channel identity, timing and repeated recovery

Keep actuators and amplifier command inputs disconnected. Use your already
validated electrical input setup to provide distinct known input levels on the
channels, within the device's permitted input limits. Do not change instrument
wiring without checking the NI terminal assignment and grounding.

Repeat stage 3 with `--cycles 20`. Check decoded `samples` against the known
inputs before and after recovery: no swapped channels, enormous discontinuities,
implausible positions, corrupted line tags or backwards elapsed times. Compare
current values with the amplifier's independently measured output and configured
gain; do not accept a factor-of-two difference as a recovery success.

Inspect `raw_alignment_words` for both marker values and their consistent phase.
If the test times out, reports ambiguity or detects surviving commands/output
changes, STOP testing. Keep the log and report it; do not remove checks or raise
timeouts until the cause is understood.

Before enabling motion, have these logs and electrical traces reviewed. Success
on a single run does not establish reliable recovery.

## 5. Optional limited Z-only test — after review of stages 3–4

This stage is restricted to unipolar Z calibration, up to 5 µm travel, at 1 µm/s.
Decreasing Z must retract (confirmed for this setup). Keep the pipette farther
from the surface than the entire commanded travel plus a generous safety margin.
Have the controller's physical disable immediately accessible.

1. Disable/disconnect actuators and amplifier commands again BEFORE launching:

   ```powershell
   .\.venv\Scripts\python.exe -m echemtips.stop_recovery_trial --settings .echemtips\trial-settings.json --cycles 1 --z-test-um 1
   ```

2. Confirm `OUTPUTS DISCONNECTED` for initial startup. The program then pauses
   at a SECOND confirmation. Verify actual X/Y outputs and zero Z; enabling a
   controller can itself move the stage to these levels. Do not proceed unless
   that is mechanically safe. Leave amplifier commands disabled.
3. Enable the piezo controller only when safe, then type `ENABLE LIMITED Z TEST`.
4. The test starts a slow move toward Z=1 µm, interrupts it after about 0.25 s,
   attempts rearm, and commands a slow return to Z=0 µm. X/Y must remain fixed.
5. Check the physical position readback and output traces. Verify no abrupt Z
   reset, no X/Y excursion, and no continuation toward the old target after Stop.
6. On ANY unexpected behavior use physical disable immediately. Ctrl+C requests
   software stopping, but does not guarantee stopping if communication fails.

Do not proceed to a pipette in contact, electrochemistry, or a hopping scan with
this diagnostic. Those require a subsequent reviewed implementation/test stage.

## 6. Report results / return to stable

Send: Git commit (`git rev-parse HEAD`), command used, settings COPY (remove any
personal paths if sharing publicly), JSONL log, scope/readback traces, observed
stop latency and whether the subsequent command completed. Logs/settings can
contain local paths and must not be committed publicly.

After a failure disable actuators before closing/reconnecting. To resume normal
work, close the diagnostic and open the original stable checkout with its own
Python environment. Its code/settings have not been changed. Connecting again
will run the normal startup sequence, so follow the normal startup precautions.

Local macOS experimental checkout: `/Users/yerga/My Drive/Development/echemtips-stop-recovery`.
This version is not merged into `main`. If rejected later, archive/remove this
checkout and branch using normal Git worktree management; no production-code
cleanup is required.
