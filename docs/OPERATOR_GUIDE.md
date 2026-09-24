# Operator guide

## Laptop plot views

Use **Hide setup** on experiment pages to give the live plots more width.
Paired maps and traces stack vertically on narrow panels; scroll to see the
second plot. **Expand plots** opens the same live tabs in a maximized window,
with **Stop experiment** and **EMERGENCY STOP** still available. Use **Return to
experiment** or close that window to restore the tabs without clearing data.

This guide explains the normal eChemTips workflow and what each experiment
does. Use Simulation while learning the interface. For a physical instrument,
complete [Real hardware setup](REAL_HARDWARE_SETUP.md) before enabling any
positioner or electrochemical cell.

## Before every session

1. Inspect the probe, cell, cables, grounding, amplifier range, and positioner
   limits. Confirm that an unexpected output change cannot cause a collision or
   damaging potential.
2. Open **Settings**, select Simulation or NI FPGA, and verify all ranges,
   sensitivities, command scaling, bitfile, and data folder.
3. Press **Save as defaults and apply**. Changing settings disconnects the
   current backend; reconnect afterward.
4. Press **Connect**. On NI hardware, read the startup-output warning and
   proceed only if the instrument has been prepared for the stated outputs.
5. Verify the persistent bottom readback for X, Y, Z, E1, E2, i1, and i2. A
   displayed number is not proof of calibration; compare against independent
   measurements during commissioning.
6. Run **Guided preflight** when wiring, amplification, electrolyte, or the
   probe has changed.

Only one experiment, diagnostic, or independent recording owns the instrument
at a time. Controls that would create conflicting ownership are disabled.

## Global controls

| Control | Purpose |
| --- | --- |
| **Connect / Disconnect** | Open or close the selected backend and acquisition worker. Disconnecting an active method stops it and marks its recording aborted. |
| **Pause** | Ask the FPGA host service to pause execution. The command watchdog excludes an acknowledged pause. It does not replace a physical emergency interlock. |
| **Resume** | Continue an operator-paused FPGA program. It does not override a pause owned by FPGA feedback logic. |
| **End waypoint** | Low-level request to finish the current FPGA waypoint. It does **not** confirm surface contact. |
| **EMERGENCY STOP** | Assert the target stop controls, drain obtainable data, abort the recording, stop acquisition, and disconnect. After a hardware stop, reinitialize and reconnect before sending another command. |

Approach pages additionally provide **Accept current Z as contact and
continue** while an approach is active. This is an intentional manual contact
decision: it stops the approach waypoint and starts settling or the next method
stage. Use it only when the current position is known to be safe.

## Watch current

Use this page to observe i1 and i2 or record a time trace without running a
motion experiment.

- **Apply potentials** commands E1 on AO3 and E2 on AO4. Manual changes are
  blocked while an experiment owns the outputs.
- **Start live view** begins plotting only new samples. Merely opening the page
  does not load or render earlier experiment data.
- **Start recording** creates a full-rate Watch Current recording and also
  starts the live view.
- **Stop and save** drains pending acquisition data before finalizing the file.
- Each plot shows a rolling 30-second display window. This does not discard
  samples from an active recording.

## Watch position

This opt-in page shows separate rolling 30-second X, Y, and Z position plots.
**Start live view** affects display only. **Start recording** preserves the
full-rate measured position and electrochemical channels in the standard
recording schema. Stop the recording before starting another method.

## Guided preflight

The preflight produces a JSON report rather than an experiment CSV. Run its
steps in order and prepare the physical fixture described by each button:

1. **Measure zero-current noise** holds E1 at 0 V and reports mean, peak-to-peak
   noise, RMS noise, drift, and a conservative suggested Δi threshold.
2. **Measure open-input capacitance** runs a triangular potential sweep with
   the amplifier signal input disconnected and estimates stray capacitance.
3. **Verify with known resistor** sweeps through a stated precision resistor
   and compares the fitted resistance with the configured value.

The simulator changes its diagnostic fixture internally. Real hardware cannot
do that: the operator must make and verify each safe circuit before pressing
the corresponding button. A suggested threshold is a starting point, not an
automatic guarantee of safe or selective contact detection.

## Characterize pipette

Prepare an immersed pipette with its normal amplifier connection. Enter a
traceable pipette ID, electrolyte conductivity, and estimated cone half-angle.

1. **Measure current stability** holds E1 at the selected potential and reports
   mean current, noise, and drift.
2. **Measure I–E response** performs a triangular sweep. Its fitted conductance
   gives a resistance estimate; resistance, conductivity, and cone angle give
   an approximate aperture radius.

The conical-pipette model is an estimate and should be recorded as such. Save
the JSON pipette profile alongside experimental records.

## Standalone CV

The commanded E1 program is:

`start → vertex 1 → vertex 2 → start`, repeated for the selected cycle count.

**Jump to start potential** changes how the initial level is reached; the three
legs of every cycle still use the configured scan rate. The time-domain tab
shows E1 and Current 1 throughout the run. The voltammogram tab plots completed
CV data as E1 versus Current 1. The analysis application later separates and
exports complete cycles using the parameters saved in JSON.

## Standalone Approach

The sequence is:

`optional XY preposition → start Z → approach toward limit Z → contact → settle → optional retract`

- Leave Target X or Target Y empty to keep that axis at its present position.
- Contact uses the selected Current 1 or Current 2 absolute threshold and
  above/below direction. Thresholds are entered in pA and converted internally.
- **Approach limit Z** is a travel limit, not evidence of contact. Reaching it
  without a threshold crossing aborts the contact result.
- **Settle after contact** may be zero.
- **Retract after approach** returns to Start Z after confirmed contact.

The traces show Z and selected feedback current versus time. **Approach curves**
contains the current approach as current versus Z and a rolling 60-second
history. The CSV remains full-rate.

## Approach then CV

This method performs the same optional XY preposition and contact-gated
approach, then settles and runs:

`CV start → vertex 1 → vertex 2 → CV start`

The sequence repeats for the selected cycle count and optionally retracts to
Start Z. CV never begins when the approach merely reaches its Z limit. Use the
CV tab for E1 versus Current 1 and the trace/approach tabs to diagnose motion
and contact.

## Approach then I–t

After optional XY positioning, confirmed contact, and settling, E1 follows:

`initial potential/hold → pulse potential/hold → return potential/hold`

The complete three-level program repeats for the selected cycle count. A hold
may be zero. The I–t tab resets its local time at the surface program and shows
both E1 and Current 1 versus time. If the Z limit is reached without contact,
the I–t program does not run. Optional retract returns to Start Z.

## Scan hopping + CV

At every grid point the method approaches, requires confirmed contact, settles,
runs a complete CV, samples Current 1 near the selected map potential, and
retracts by a positive distance away from the measured contact Z.

- X/Y start, end, and point count define physical grid coordinates. The page
  reports X and Y hop spacing.
- **Serpentine** reverses X order on alternating rows. **Raster** returns to the
  same X side before the next row.
- **Raster flyback extra retract** is added only before the longer line return.
- **Initial approach Z** applies only to the first hop. Later approaches start
  from contact-relative retract positions.
- After the final landing (the marker when enabled), both scan methods return Z directly to **Initial approach
  Z** at **Retract speed**, leaving X/Y at the final pixel. If Z is already
  farther retracted, it stays there instead of moving toward the surface.
  The scan stays active and records until that return completes. Intermediate
  hops still use the requested contact-relative distance. Approach-only,
  Approach + CV and Approach + I–t retain their existing return/retract options;
  standalone CV does not move Z. Stop/fault behaviour on the production branch
  is unchanged and does not automatically return Z.
- Both hopping CV and hopping I–t allow an initial Z of zero. Retraction is
  calculated at contact, then limited to the configured 0–Z maximum command
  range. For an increasing-Z approach, contact at 8 µm with a 10 µm retract
  results in a target of 0 µm; contact at 80 µm results in 70 µm. The same limit
  applies to raster flyback extra retraction and, in the opposite direction,
  to the upper Z boundary. Subsequent hops use the bounded retract position.
- A shortened retract is shown in the scan status and recorded in the JSON
  `parameters.retraction_events` list, using zero-based `scan_pixel` indices.
  Entries include contact/target Z, requested/available command travel, and a
  `no_travel` flag. Hardware calculations use the applied Z output at contact;
  the sensor Z in the CSV remains a separate measurement. These events describe
  commanded clearance, not independently verified physical movement.
- If no usable retract travel remains (including travel within one hardware
  output increment), the scan stops after the point's electrochemical program
  without submitting another XY move. Inspect clearance before manually moving
  or starting again; the generic Resume button does not restart this scan.
  A shortened, nonzero retract can continue, so choose scan bounds and flyback
  clearance conservatively. A command limit does not prove meniscus detachment.
- The duration estimate includes deterministic CV, settling, lateral, repeated
  approach, and retract time. It excludes the initial move and first approach.
- Square cells show the sampled grid; circular footprints show the configured
  meniscus diameter in physical coordinates. Choose the shared shape and
  diameter under **Settings → Maps**, not on individual scan pages. These
  preferences change rendering only, not the scan path or feedback.

**Settings → Maps** also offers independent Z/current colormaps and
automatic/fixed color limits. **Settings → Plots** contains rolling history
durations, current display units, font size and trace thickness. The save/apply
button below the tabs applies all categories. Saved recording units and
full-rate data are unaffected.

The maps contain only confirmed contacts. A no-contact hop cannot start a CV or
produce a valid contact/current map value. Start with 1×1, then 2×2, during
commissioning.

## Scan hopping + I–t

Motion, contact gating, path selection, retract behavior, map footprints, and
duration interpretation match hopping CV. At each confirmed contact the method
runs the initial/pulse/return program. The current map is the mean Current 1
during the pulse hold. Use the per-hop I–t tab to inspect the latest surface
program.

## Move piezo

Choose X, Y, or Z, a target in µm, and a positive speed in µm/s. Bounds come
from Settings. The page deliberately distinguishes **commanded** position from
**measured** input readback. Test small, slow moves with ample clearance before
approach work. Stopping a real FPGA move retires the uncertain program stream;
reinitialize and reconnect before another command.

## Recordings and analysis

Completed experiments are saved automatically when that setting is enabled.
When it is disabled, eChemTips asks whether to keep a completed experiment.
Aborted and failed runs are retained with their terminal status for traceability.
See [Recording data format](DATA_FORMAT.md) for exact semantics.

Choose **Analysis → Open analysis app** (F6), or **Analyze last saved recording**,
from control. Analysis runs in a separate process. You can also run
`python run_analysis.py` or `echemtips-analysis` independently.
The [Analysis workbench guide](ANALYSIS_GUIDE.md) covers signal exploration,
time-window measurements and charge, CV overlays, physical hop maps, and exports.

## Normal stop and recovery

1. Prefer the method page's **Stop** for a controlled abort.
2. Confirm that the UI reports the partial recording saved.
3. On NI FPGA hardware, disconnect/reinitialize and reconnect before another
   command after cancellation.
4. Use **EMERGENCY STOP** when continued output or motion may be unsafe.
5. If software cannot establish a safe state, use the positioner/controller's
   physical inhibit or power controls according to the laboratory procedure.
   Never depend on the GUI as the only safety layer.


## Scan orientation marker

Both hopping scans enable **Add final orientation landing** by default. After
all array points succeed, the probe retracts, moves to the marker, approaches
using the same contact settings, repeats the same CV or I–t program (including
cycles and settling), and returns toward Initial approach Z. An aborted scan
does not start a marker. The marker itself requires confirmed or operator-accepted
contact; reaching the approach limit does not authorize the electrochemistry.

Automatic placement is the first X coordinate, one Y spacing beyond the final
row in the scan direction. For a single Y row the offset is the larger of 5 µm
and three footprint diameters. This extends one corner rather than symmetrically
extending the whole array. Enter explicit Marker X/Y coordinates to override it,
or untick the option. Invalid/out-of-travel or overlapping positions block start;
they are never silently clamped. Verify the preview and sample area before running.
The trip to the marker uses normal retract distance plus the **Long-move extra retract**, even for serpentine scans. Duration estimates include the marker.

The scan preview and exported `.orientation.svg` show landing order and X/Y
orientation for later microscopy. Their positions are planned commanded values;
consult the JSON for actual marker completion. Live traces still show marker
activity for operator oversight, but the analysis app excludes it from all
treatments, plots, maps and movies. A visible footprint is not guaranteed on
every sample; confirm a suitable imaging contrast in a small test array first.
