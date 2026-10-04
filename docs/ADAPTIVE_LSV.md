# Adaptive hopping — CV, LSV and I–t

## Potential conditioning before approach

After positioning, the approach potential is applied and acknowledged while Z
stays at its safe travel height. **Settling before approach** (default 0.25 s,
minimum 0.05 s) precedes feedback arming. Three fresh final current samples must
be below the contact-magnitude threshold. An unsettled or above-threshold signal
rejects the location without advancing toward the surface, withdraws to initial
Z and continues at a fresh location in the default mode. Missing fresh samples
still stop the run; optional tilt-check mode also stops for unsettled current. This is separate from
settling after contact and requires no FPGA bitfile change. Clearance-rejected
landings are marked invalid in the decision log and metadata.

Open **All experiments → Adaptive hopping + CV / LSV** or **Adaptive hopping + I–t**. Pin either to the sidebar if
needed. This is an experimental spatial-selection workflow, not a certified
collision-avoidance system. It uses the existing contact-gated CV/LSV or I–t FPGA programs;
no bitfile changes are required. Hardware operation still needs staged testing.

## What it does

1. Survey four corners and the center, or a 3 × 3 grid, at a conservative initial
   travel Z. Every survey landing includes the same measurement as later landings.
2. By default, use initial Z for travel without fitting a surface plane. If
   **Use optional surface tilt checks** is enabled, fit a plane to **commanded contact Z**, captured by the native driver after
   confirmed contact and before the follow-up sweep. Sensor Z remains a separate
   measured trace; sensor offsets must not enter commanded clearance calculations.
3. Only with tilt checks enabled, require explicit approval of the slopes and maximum residual. This approval
   is required even when individual-landing approval is disabled.
4. Fit a Gaussian process to usable electrochemical objectives off the GUI
   thread, then propose the next unvisited XY location.
5. Retract Z, wait for verified movement completion, move X then Y, then execute
   potential conditioning → approach → settling → measurement → retract.
6. Stop proposing when the count/time budget is reached or no legal grid
   candidates remain. Return to the configured initial Z on normal completion.

**Decreasing Z must move away from the sample.** Confirm this physically. The
initial Z must clear the entire region **and the entry path from current XY**.
Being inside the piezo range is not sufficient. A sparse survey cannot rule out
particles, steps, protrusions, unexpected topography, tilt changes or drift.

## Settings and choices

- **CV / LSV:** select the waveform. LSV is a single start-to-end sweep; CV records
  start → vertex 1 → vertex 2 → start for the requested number of cycles. Select
  the **objective cycle** (one-based) and chronological **sweep segment**. For a
  −0.2 → +0.6 → −0.4 → −0.2 V CV, a +0.2 V objective on segment 1 is distinct
  from +0.2 V on segment 2. Only the selected branch contributes. The full
  objective window must fit inside that branch. All recorded cycles remain in
  the raw data and can be extracted in analysis.
- **I–t:** configure initial, pulse and return potentials and hold times, and
  cycle count. Select the objective cycle and a time window **from that cycle's
  start**, not from contact or the pulse start. With 0.25 / 1 / 0.25 s holds,
  0.75–1.00 s samples the pulse. A window cannot straddle a potential transition.
  The objective is the absolute median signed current, not charge or peak current.
  Acquired phase tags distinguish holds even when their potentials are equal.

- **Objective:** absolute value of the median *signed* selected current in a
  potential window (CV/LSV) or time window (I–t). Thus a negative current can be optimal. This is not the
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
- **Use optional surface tilt checks:** off by default. Initial points seed the
  electrochemical model, not a mandatory tilt determination. The operator verifies
  initial-Z clearance across the sample and entry path. When enabled, the following
  three fields become visible and the plane must be approved.
- **Travel clearance / relief allowance / plane deviation (optional):** the commanded
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
  to the Z limit, every measurement cycle, settling and retraction. A running landing is not cut short
  by the time budget; pauses and hardware delays can extend actual elapsed time.

This version uses a bounded **31 × 31 candidate grid** and an isotropic squared
exponential GP in normalized XY coordinates. Marginal likelihood selects one of
five length scales; a fixed noise term regularizes the model. It is deliberately
small and auditable, not a general materials-discovery framework.

## Quality and safety behavior

Only confirmed, complete, finite, non-clipped programs with enough objective-window
samples train the model. Low current alone does not mean a failed landing.
Baseline median/MAD are recorded when pre-contact samples are available; the
objective-window MAD flags very noisy results. These tests cannot establish
constant contact area or distinguish all leakage/false-contact events.

A no-contact landing stops the scan; on real hardware the existing cancellation
may require reconnection. In default operator-led mode, invalid/noisy electrochemistry
is recorded as rejected, excluded from model fitting, and followed by a fresh
location; rejected attempts still consume budget and reserve their locations.
With fewer than three usable objectives, fresh-site space-filling exploration
continues until a model can be fitted. No contact-height tolerance is applied.
With optional tilt checks enabled, quality/clearance failures and contact heights
outside the approved tolerance stop further proposals. Operator **Stop experiment** and
**Emergency stop** retain their existing strong-stop behavior on `main`; the
experimental recoverable-stop branch is not included. The global **End waypoint**
action is disabled during adaptive acquisition to prevent bypassing motion gates.

## Displays and files

The page has separate tabs for measured objective, predicted objective, model
uncertainty, commanded contact height, latest CV/LSV or I–t, Z/current/potential time traces and the
decision log. The red cross is the next proposal; grey lines show landing order.
Before starting, the dashed survey path previews the selected region.

- CSV: full-rate measurements, including `scan_pixel` for each approach/measurement/
  retract landing. Between-landing travel and planning have no landing ID.
- JSON: parameters and an adaptive `scan_grid` mapping IDs to commanded XY,
  contact status and validity. Existing analysis can group individual
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
2. Use defaults with a budget of 7–10 landings and a **5 pA** contact threshold.
   The adaptive simulator places a tilted surface inside the selected initial-Z
   to approach-limit interval (58–72% of that span), independently of the full
   calibrated piezo range. Thus the default 10–90 µm approach works with either
   a 100 or 200 µm piezo range. It provides a spatial hotspot, approximately
   0.15 pA current noise, and an 80 pA charging transient with a 0.12 s decay time
   on Current 1 (Current 2 follows at approximately 80%). A sustained contact
   response remains until retraction. These are synthetic demonstration signals,
   not a quantitative meniscus model. Thresholds are never adjusted automatically;
   an excessively high threshold can still produce a genuine no-contact result.
   Approve each survey point. Inspect the LSV and contact-height result.
3. With the default tilt checks off, confirm no plane approval is requested.
   Repeat with tilt checks on and check/approve the fitted plane. Inspect the next proposal and uncertainty
   map. Complete the run and verify initial Z, CSV/JSON, journal and report.
4. Repeat with approval disabled, with Mapping and Hotspots, and with a small
   time budget. Tilt approval is mandatory only when tilt checks are enabled.
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

Other waveforms and objective types, drift/reference scheduling, contact-area estimation
or normalization, polygons/obstacle maps, non-planar navigation, automatic retry,
crash resume, orientation-marker landings, picomotors and microscopy registration
are not implemented here.
The objective functions (`score_lsv`, `score_cv`, `score_it`), `propose`, and the separate travel envelope
provide extension points without giving a model direct access to hardware.

## Testing the adaptive waveform extensions

1. Start in **Simulation**, with the default 5 pA threshold and 0.25 s settling
   before approach. Select a seven-landing budget: five survey points plus two
   adaptive proposals. Confirm the safe region, then approve landings. A tilt
   fit is requested only if optional surface tilt checks are enabled.
2. In **Adaptive hopping + CV / LSV**, select CV, two cycles, objective cycle 2,
   and segment 2. Watch the full CV in the measurement tab and verify seven
   valid results in the decision log. Repeat in LSV mode.
3. In **Adaptive hopping + I–t**, use the default holds, two cycles and objective
   cycle 2, window 0.75–1.00 s. Check the potential and current traces, objective
   maps and return to initial Z.
4. Inspect CSV, JSON, decision journal and report. Invalid results must not train
   the model. Confirm cycle selection in CV analysis and individual I–t landings.
5. On hardware, first use an inspected region with ample clearance and approval
   for every landing. Confirm the approach potential settles while Z is stationary.
   If the baseline remains above threshold, stop and investigate or increase
   pre-approach settling; do not mask it by arbitrarily increasing the threshold.

These extensions reuse the existing bitfile. Simulated and fake-session tests
do not replace verification on the NI instrument, including real phase timing,
electrical transients and safe motion.
