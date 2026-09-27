# Experimental detachment and area normalization

The analysis workbench can estimate a landing diameter from the current break
during retraction and offer separate current-density channels. This is an
**experimental endpoint wetted-area proxy**, not a measurement of ECSA, proof of
successful contact, or a time-resolved droplet area. It never changes acquisition,
contact detection, motion, or the original recording.

## Inspect a recording

1. Open a saved hopping CV/LSV or I–t recording in the analysis app.
2. Open **Area / detachment**. Choose the recorded current channel and leave
   normalization off initially.
3. Choose **d = h** or enter an independently calibrated relationship
   **d = a·h + b**, with h and d in µm. The default a=1, b=0 is an assumption,
   not an instrument calibration.
4. Click **Detect / apply**. Work runs in the background. Select a hop to inspect
   its current trace and detected break. The table shows diameter, area,
   withdrawal distance and the reason for unavailable estimates.
5. Inspect the break and Z calibration before using estimated areas. Export
   landing diagnostics for a CSV table and JSON settings/provenance.
6. If appropriate, select **Retraction estimate** and apply again. Select
   **Current density 1/2** in CV plots, the explorer, maps or movies. Their units
   are mA/cm²; figure export also supports other current-density units.

Estimated diameter and area are also available as derived map/explorer channels.
For a diameter map, choose a whole-hop statistic such as Mean with the experimental
landing-diameter channel. Each accepted landing has one value, and unavailable
landings remain gaps. Raw current channels remain available alongside density.

This first implementation requires `scan_pixel` identifiers and recognizable
stationary surface-program data for automatic per-landing detection. It supports
multiple CV cycles and uses the last complete surface cycle before retraction.
Incomplete or ambiguous programs, untagged standalone recordings, and recordings
with too few retraction samples cannot provide automatic diameter estimates.
Orientation-marker landings are excluded by the usual analysis selectors.

## What the detector checks

The algorithm works on the original, unsmoothed recorded current, before reference
electrode conversion. It finds decreasing measured Z after a stationary surface
program and requires approximately constant recorded E1 and X/Y. It does not
require OCP: it analyzes the actual retraction potential already recorded.

A robust median/MAD estimate of the final current baseline defines the noise
band. Short median filtering rejects isolated spikes. Persistent windows must
show a substantial current departure from that baseline followed by a sharp,
sustained return into the noise band. Both current signs and nonzero baseline
offsets are supported. Current recovery, unstable baselines, Z reversals,
potential changes, invalid timestamps and insufficient data are rejected.
The detector settings are adjustable, but loosening them does not improve the
physical accuracy of the area model.

Withdrawal is h = Z_surface − Z_break. The exported Z sampling bracket describes
local sampling resolution only: it does not include analog-filter delay,
mechanical response, calibration uncertainty or model error. Area is πd²/4.
Current density uses j[mA/cm²] = 100 i[nA] / A[µm²].

## Alternative normalization sources

- **No normalization:** inspect diagnostics and retain currents in nA.
- **Nominal area:** explicitly enter one positive area in µm². This also works
  for recordings without hop identifiers.
- **Retraction estimate:** use accepted estimates only; unavailable areas become
  NaN, never silently replaced with a nominal area.
- **Imported per-landing areas:** supply measured or otherwise independently
  derived areas keyed by the zero-based CSV `scan_pixel`, not displayed hop number.
  This is the extension point for future SEM footprint measurements.

The import format is:

```json
{
  "units": "um2",
  "id_column": "scan_pixel",
  "areas": {"0": 0.7854, "1": 1.1310, "3": 0.9503}
}
```

Only positive finite areas and existing landing IDs are accepted. Missing IDs
remain unnormalized gaps. The imported path and values are saved in the analysis
workspace and export provenance. Applying an area assigns one endpoint area to
the entire landing; it cannot reconstruct area changes within a CV or pulse.
Smoothing, if selected, affects the currents used for density, but not detection.

## Passive control-app diagnostic

After a recording closes, use **Analysis → Experimental landing diameters (last
recording)…**. This reads the completed/aborted saved file in a background worker
and opens the same inspection table/plot. It is disabled while a recording is
active, does not access the FPGA, and never changes the next landing or any safety
decision. A stopped recording may have no complete retraction to analyze.

## Physical limitations and validation

The idea is motivated by [Saxena et al., *In Situ Quantification of a Wetted
Surface Area during Scanning Electrochemical Cell Microscopy Using Retraction
Curves*](https://doi.org/10.1021/acsmeasuresciau.4c00042). The implementation at a
recorded experimental retraction potential is an adaptation, not a reproduction
of the paper's complete protocol.

- A near-zero attached current cannot reveal a break reliably. A failed landing
  or a clipped/noisy signal can also be uninformative. “Unavailable” is not a
  failed-landing classification, and an accepted electrical break is not proof
  of a particular droplet geometry.
- Retraction must record enough data before and after separation. Fast or short
  retractions may yield no estimate with existing acquisition settings.
- Potential steps and electrochemical transients can resemble detachment. The
  stability and persistence checks reduce this ambiguity but cannot eliminate it.
- Verify measured-Z gain, direction, linearity and dead zones. A constant offset
  cancels in h, but scale errors and actuator saturation do not.
- Analog filtering delays current changes; withdrawal speed times delay gives
  a corresponding distance bias. Increasing the software persistence window
  cannot remove that bias.
- Validate d(h) against independent measurements over the relevant range of
  pipettes, surfaces, electrolyte and retraction conditions before quantitative
  comparisons. An approximate 10% diameter error implies about 20% area error.
- Normalizing by wetted geometric area does not by itself distinguish catalytic
  activity, roughness, transport limitations, changing chemistry or active area.

## Acceptance checklist

1. Inspect a clear positive-current and negative-current break; verify the marked
   transition and plausible Z displacement.
2. Check a no-break/noisy or incomplete landing remains unavailable.
3. Compare d=h with a calibrated slope/intercept and verify πd²/4 scaling.
4. Confirm density = 100 × current / area and that raw current is unchanged.
5. Import a partial area table; verify missing hops remain gaps in maps/movies.
6. Save/reopen an analysis workspace and inspect exported figure/data provenance.
7. Open the control diagnostic after a recording; confirm it sends no commands.

Automated tests cover synthetic signed breaks, offsets, isolated spikes, rejected
traces, normalization units, missing areas, worker integration and UI/export
contracts. These do not substitute for validation with real retraction data.
