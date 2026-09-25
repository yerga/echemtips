# Adaptive hopping + LSV — first version

Open **All experiments → Adaptive hopping + LSV**. Pin it to the sidebar if
needed. This is an experimental spatial-selection workflow, not a certified
collision-avoidance system. It uses the existing contact-gated LSV FPGA program;
no bitfile changes are required. Hardware operation still needs staged testing.

## What it does

1. Survey four corners and the center, or a 3 × 3 grid, at a conservative initial
   travel Z. Every survey landing includes the same LSV as later landings.
2. Fit a plane to **commanded contact Z**, captured by the native driver after
   confirmed contact and before the follow-up sweep. Sensor Z remains a separate
   measured trace; sensor offsets must not enter commanded clearance calculations.
3. Require explicit approval of the slopes and maximum residual. This approval
   is required even when individual-landing approval is disabled.
4. Fit a Gaussian process to usable electrochemical objectives off the GUI
   thread, then propose the next unvisited XY location.
5. Retract Z, wait for verified movement completion, move X then Y, then execute
   the existing approach → settling → LSV → retract sequence.
6. Stop proposing when the count/time budget is reached or no legal grid
   candidates remain. Return to the configured initial Z on normal completion.

**Decreasing Z must move away from the sample.** Confirm this physically. The
initial Z must clear the entire region **and the entry path from current XY**.
Being inside the piezo range is not sufficient. A sparse survey cannot rule out
particles, steps, protrusions, unexpected topography, tilt changes or drift.

## Settings and choices

- **Objective:** absolute value of the median *signed* selected current in a
  potential window. Thus a negative current can be optimal. This is not the
  median of absolute samples, which would bias a zero-mean noisy signal upward.
  At least three samples must fall inside the window; widen it or increase
  sampling rate if needed. No extrapolation is performed.
- **Hotspots:** choose the largest predicted mean plus two posterior standard
  deviations (an upper-confidence-bound acquisition function).
- **Mapping:** choose maximum posterior uncertainty. It does not promise to
  identify every narrow feature.
- **Balanced:** alternate uncertainty and upper-confidence-bound selection.
- **Minimum separation:** center-to-center exclusion around **every attempted
  landing**, including rejected/failed attempts. It is not automatically derived
  from the meniscus diameter. Choose it to allow for wetting and residue.
- **Travel clearance / relief allowance / plane deviation:** the commanded
  lateral travel Z is the lowest predicted contact Z on the full X-then-Y path,
  minus all three allowances. The intermediate corner is checked, not just the
  source and destination. If this requires Z below zero, the run fails closed;
  it does not silently reduce the clearance or clamp the command.
- **Maximum plane deviation:** rejects excessive survey residuals and later
  contacts inconsistent with the approved plane. It is a deterministic tolerance,
  not a confidence bound proving the surface safe.
- **Approval mode:** enabled by default. Approval only authorizes the displayed
  proposal; validation and motion checks still run afterwards.
- **Budgets:** survey attempts count toward the landing budget (maximum 500).
  Wall time includes planning, approval waits and pauses. Before starting a new
  landing the supervisor reserves an estimate for lateral motion, a full approach
  to the Z limit, LSV, settling and retraction. A running landing is not cut short
  by the time budget; pauses and hardware delays can extend actual elapsed time.

This version uses a bounded **31 × 31 candidate grid** and an isotropic squared
exponential GP in normalized XY coordinates. Marginal likelihood selects one of
five length scales; a fixed noise term regularizes the model. It is deliberately
small and auditable, not a general materials-discovery framework.

## Quality and safety behavior

Only confirmed, complete, finite, non-clipped sweeps with enough objective-window
samples train the model. Low current alone does not mean a failed landing.
Baseline median/MAD are recorded when pre-contact samples are available; the
objective-window MAD flags very noisy results. These tests cannot establish
constant contact area or distinguish all leakage/false-contact events.

A no-contact landing stops the scan; on real hardware the existing cancellation
may require reconnection. Invalid/noisy electrochemistry causes a controlled
return and an aborted result for operator review. New contact heights outside the
approved tolerance also stop further proposals. Operator **Stop experiment** and
**Emergency stop** retain their existing strong-stop behavior on `main`; the
experimental recoverable-stop branch is not included. The global **End waypoint**
action is disabled during adaptive acquisition to prevent bypassing motion gates.

## Displays and files

The page has separate tabs for measured objective, predicted objective, model
uncertainty, commanded contact height, latest LSV, measured time traces and the
decision log. The red cross is the next proposal; grey lines show landing order.
Before starting, the dashed survey path previews the selected region.

- CSV: full-rate measurements, including `scan_pixel` for each approach/LSV/
  retract landing. Between-landing travel and planning have no landing ID.
- JSON: parameters and an adaptive `scan_grid` mapping IDs to commanded XY,
  contact status and validity. Existing LSV analysis can group individual
  landings; invalid adaptive landings are excluded from derived hop/CV selections,
  but remain in the raw table/CSV.
- `*.decisions.jsonl`: incremental, flushed decision journal beside the CSV.
  Locations are reserved before movement. Contains proposals, their selection
  reasons, predicted value/uncertainty, plane fit/approval and landing results.
- `*.report.md`: final readable summary, positions, objectives and reasons.

Nonuniform sampling is intentional. Do not treat the missing positions as zero
current or assume equally sized analysis map cells reflect physical footprints.
The live adaptive maps display points in physical XY coordinates. The existing
analysis map viewer still uses its regular-cell rendering; use the actual XY
metadata for quantitative spatial analysis of adaptive data.

## Simulator and first hardware test

1. Select **Simulation**, connect and open this experiment. Enable automatic
   recording, leave approval mode enabled and confirm the safe-region checkbox.
2. Use defaults with a budget of 7–10 landings. The adaptive simulator provides
   a planar tilt, spatial hotspot and low-amplitude current noise. Approve each
   survey point. Inspect the LSV and contact-height result.
3. Check and approve the fitted plane. Inspect the next proposal and uncertainty
   map. Complete the run and verify initial Z, CSV/JSON, journal and report.
4. Repeat with approval disabled, with Mapping and Hotspots, and with a small
   time budget. Tilt approval remains mandatory.
5. For an offline equal-budget comparison (not instrument validation), run:

   ```sh
   python scripts/benchmark_adaptive.py --landings 25 --seed 7
   ```

   This compares all three strategies with random and space-filling sampling on
   a known synthetic landscape, reporting best true objective and model RMSE.
   Repeat seeds/landscapes; no strategy is guaranteed best on an unknown sample.
   Automated tests also inject a failed simulator contact.

6. On hardware, first commission the ordinary Approach + LSV and manual motion
   as described in the [hardware guide](REAL_HARDWARE_SETUP.md). Confirm calibration,
   polarity, contact threshold, decreasing-Z retraction, command limits and the
   conservative initial Z with the probe safely clear of the sample.
7. Start with a small, inspected flat region, large clearances and **approval for
   every landing**. Test the five-point survey before allowing further points.
   Verify actual AO/readback, chronological data, each retraction and the physical
   footprint separation. Never approve a plane merely because the fit looks good.
8. Test stop/fault handling under controlled conditions before enabling autonomous
   proposals. Hardware verification must be recorded on the actual instrument;
   fake-session tests and simulation do not establish physical safety.

## Deliberately deferred

Other waveforms/objectives, drift/reference scheduling, contact-area estimation
or normalization, polygons/obstacle maps, non-planar navigation, automatic retry,
crash resume, orientation-marker landings, picomotors and microscopy registration
are not implemented here.
The existing callbacks (`score_lsv`, `propose`, and the separate travel envelope)
provide extension points without giving a model direct access to hardware.
