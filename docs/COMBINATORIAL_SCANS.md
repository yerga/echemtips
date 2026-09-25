# Combinatorial scans

Use **Scan hopping + CV** (including LSV) or **Scan hopping + I–t** and open
**Combinatorial scan → Plan recipes and assignments…**. This extends the normal
scan runner rather than introducing a separate hardware protocol. No bitfile
change is required. Verify new recipes on a controlled electrical load before
using a valuable sample; simulation and fake-device tests are not hardware validation.

## Quick example: two rows per condition

1. Select Simulation and connect. Configure a small grid, for example three X
   points and six Y points. Choose a safe Z approach interval and contact settings
   as for a normal scan. With combinatorial recipes enabled, the simulator places
   a gently tilted surface inside the selected approach interval, independent of
   the full piezo range. Use the default 5 pA magnitude threshold: the simulated
   open-cell noise is approximately 0.15 pA, with a 40 pA contact transient and a
   sustained wet-cell response. Both feedback channels are supported. Thresholds
   are never adjusted automatically; an excessive threshold can still produce a
   genuine simulated no-contact abort. Ordinary scans retain their existing model.
2. Set the shared motion, feedback, settling and optional orientation-marker
   settings. These do not change between conditions.
3. Open the recipe planner. The first recipe copies the default measurement
   program. Rename it **Reference**.
4. Use **Add / duplicate** to create **Fast** and **Wide window**. Change the scan
   rate in Fast and the vertex potential in Wide window. Each recipe is complete;
   the program does not inherit edits from the preceding landing.
5. In **Assignment preview**, choose **Row blocks**, with **Rows per recipe = 2**.
   Rows 1–2 use Reference, 3–4 Fast, and 5–6 Wide window. Inspect replicate counts
   and hover over cells for the full program. The selected recipe's profile is
   plotted against program time.
6. Choose **Use this plan** and then **Start scan**. The status includes the active
   recipe name. Start/stop, contact acceptance, sample tagging, and final return
   to initial Z use the normal scan lifecycle.
7. Open the recording in Analysis. The **Condition** selector changes traces,
   extracted cycles, maps, smoothing and movies together. Different potential
   programs are not silently combined into one movie.

## Assignment and replication

- **Row blocks:** repeat the ordered recipe list in blocks of physical rows.
- **Interleaved:** repeat recipes over the physical row-major grid.
- **Randomized:** shuffle that balanced list using the displayed random seed.
  This randomizes conditions, not probe travel. Counts differ by at most one.
- Grid size determines the number of fresh-site replicates. The planner rejects
  plans in which any recipe has no landings. Choose a point count divisible by
  the recipe count for equal interleaved/randomized replication.
- Rows run from Y start toward Y end; columns from X start toward X end.
  Assignment does not reverse when serpentine acquisition reverses direction.
- The optional orientation marker repeats the last array landing's recipe and
  remains excluded from analysis.
- Changing grid dimensions requires reopening the planner to confirm assignments.
  Disabling combinatorial mode restores the ordinary shared measurement program.

**Two-factor matrix…** replaces the recipe list with all combinations of two
comma-separated numeric parameter lists (maximum 64 combinations). Other values
come from the selected recipe. Check the resulting programs and assignments
before accepting. **Save recipes…** exports a versioned JSON recipe library;
assignment and instrument settings are not part of that library. Actual run
assignments are saved with each recording.

## Supported variables

CV/LSV: start potential, vertex 1/LSV end, vertex 2, scan rate, cycles and waveform.
LSV requires one sweep; set cycles to 1. I–t: initial/pulse/return potentials and
durations, and cycle count. Potentials follow the selected instrument polarity
convention, just as in standard scans.

Motion, contact thresholds, settling time, acquisition timing, calibration and
amplifier settings remain shared. CV/LSV and I–t use separate scan families;
arbitrary mixed or multistage protocols, per-condition contact settings and
Bayesian optimization of conditions are not included in this version.

The live CV current map retains its shared sampling potential. A recipe whose
potential range does not contain it leaves that map cell unavailable. Live map
values may represent different protocols: use the condition selector in Analysis
for comparable subsets. The I–t live map remains mean pulse current.

## Recording and extension contract

The full-rate CSV format is unchanged. `scan_pixel` links each sample to JSON
`scan_grid.pixels`. Each combinatorial pixel records `condition_id`,
`condition_name`, and its full `measurement_program`. JSON parameters also retain
the recipes, physical-row-major assignment and design provenance (mode and seed).
This avoids repeating condition fields in every sample row.

`combinatorial.program_fields` is an explicit override allowlist.
`ScanHoppingCVParameters.for_point` and `ScanHoppingITParameters.for_point` resolve
recipes without mutating the shared scan. Both simulation and the native driver
use this resolver. All recipes are validated before starting; native line-tag
budgets account for per-hop program length. Retraction events remain shared with
the parent parameters for recording.

Analysis resolves the actual per-hop waveform for cycle extraction, including
multiple cycles per landing. Background loading selects one condition before
map/movie processing and retains the original unfiltered source in memory.
Interrupted recordings can contain planned conditions without data; selecting
one reports that no samples are available.

## Experimental interpretation

Changing conditions by row can confound protocol effects with spatial material
heterogeneity and drift. Prefer randomized/interleaved replicates and repeated
reference conditions when estimating parameter effects. Fresh sites avoid
cumulative cycling at one location, but introduce site-to-site variability.
Current alone is not a morphology, selectivity, or intrinsic-activity measurement.

## First hardware check

Start with two recipes that differ modestly in scan rate or pulse duration, a
2 × 2 grid, a controlled load, and safe clearance. Confirm contact gating, the
waveform at every hop, recipe-to-position assignment, final return, saved metadata
and condition selection in Analysis. Only then increase grid size or potential
range. Existing main-branch stop behavior remains unchanged.
