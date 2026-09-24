# Analysis workbench

The analysis app is independent of the instrument connection. It never sends
FPGA commands or modifies the original recording. No additional libraries are
needed for eChemTips CSV/JSON files; install `.[analysis]` for legacy TDMS import.

Dense traces use display-only extrema-preserving reduction and fast segmented
line rendering. Measurements and exports still use full-resolution samples.
Contiguous hop selections share the underlying read-only numeric array rather
than copying each hop. Imports run in a background worker; very large recordings
still require sufficient RAM (this is not an out-of-core file viewer).

## Optional current smoothing

Select **Smooth currents**, choose **Savitzky–Golay** (the default), an odd
window size and polynomial order, then press **Apply**. Start with 11 samples
and order 2; the order must be smaller than the window. **Moving average** remains
available as an alternative. Both methods affect current channels consistently
in Explore/measure, CVs, peak summaries, the numeric table, and current-based hop
maps (including current at a selected potential). It does not smooth potentials
or positions, or change CV identification. Uncheck it and press Apply to restore
the originals. Applying again always starts from original data, not an already
filtered trace. Processing runs in the background; the existing views remain
active until the new result is ready. The header identifies applied smoothing.

The window counts samples, not seconds. Start with 5–11 samples; use acquisition
interval and scan rate to assess how much potential/time range that spans.
Large windows can suppress real peaks and change peak currents, charge estimates,
and map values. This is a derived analysis, not a noise-free measurement.
Savitzky–Golay uses [SciPy's polynomial filter](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.savgol_filter.html)
with polynomial edge interpolation. For short segments, the window is reduced
to the largest fitting odd size; if it cannot exceed the selected order, that
segment stays unchanged. Moving averages use available samples at segment edges.
Invalid values remain gaps. Filtering is in sample index, assuming approximately
uniform acquisition spacing; it does not resample irregular timestamps or fit
against potential. Savitzky–Golay can overshoot or produce ringing near spikes;
compare against original data before interpreting features.
Filtering does not cross hop/waypoint changes, non-increasing timestamps,
extracted CV boundaries or E1 sweep reversals. It is temporal smoothing of current,
**not spatial smoothing between map pixels**.

Source files are never rewritten. Trace, map, and separated-CV exports include
the processing method, sample window, polynomial order (where applicable), edge
handling and channel list in their JSON sidecars.
Reprocessing clears a pinned reference to avoid retaining an outdated version.
Full-resolution smoothed data require an additional numeric array in memory.

SciPy is a required dependency. After updating an existing installation, run
`python -m pip install -e .` in the activated project environment to install it.

## Launching

- In control, choose **Analysis → Open analysis app**, or press **F6**.
- **Analysis → Analyze last saved recording** opens the last finished recording
  from the current control session. It is unavailable while recording.
- Independently: `python -m echemtips.analysis [recording.csv]`.
- Optionally add `--data-folder PATH` to set the recording browser folder.

Control launches analysis in a separate process using the same Python environment.
You can close either app independently. Keep large analyses off the acquisition
computer if CPU or memory pressure could interfere with instrument use.

Choose a file or folder, filter the browser by name, and select a recording.
Imports run in a background worker; a newer selection supersedes older results.
The header shows the source's completion status. Aborted recordings remain valid
for raw-data inspection but may contain fewer complete CVs or visited hops.
**Show / hide file browser** gives plots more width on a laptop.

## Explore and measure

1. Choose **Whole recording**, **Recorded hops**, or **Complete CV cycles**.
   Available scopes depend on the data, not the experiment's name.
2. Select a hop or cycle, then choose X and Y channels. Examples:
   - `Elapsed time` versus `Current 1`: chronoamperometry/current history.
   - `Measured Z` versus `Current 1`: current–position relationship.
   - `Potential E1` versus `Current 1`: voltammogram of a selected complete CV.
   - `Elapsed time` versus `Measured Z`: position history.
3. **Time range / baseline** selects an inclusive range using the recording's
   elapsed timestamps, not a clock restarted at each hop. Leave both limits
   blank to include all samples. The baseline is a constant subtracted in the
   native Y-channel unit (nA for current), regardless of displayed current units.
4. Inspect sample count, mean, population standard deviation, extrema and their
   X coordinates. These are raw/sample statistics, not fitted electrochemical peaks.
5. For current, inspect **signed charge**, calculated by trapezoidal integration
   against recorded time: nA × s = nC. Changing X to potential does not change
   the integration variable. The selected interval is not interpolated at its
   endpoints. A negative value represents net cathodic charge only for verified
   IUPAC-polarity recordings. Check the convention shown beside the dataset name;
   old native-polarity eChemTips files have the opposite interpretation for the
   WEC-SPM wiring. Analysis retains file values and never silently flips signs.

Calculations use full-resolution data. Invalid samples break charge integration;
the app reports excluded samples and integrated duration. Decreasing timestamps
are rejected rather than silently reordered. Unknown numeric channels are exposed
with their source names; the app does not invent their units.

**Compare → Pin this trace as reference** retains a snapshot of the current
processed trace. Select another hop, cycle or file to overlay it. A reference is
displayed only when both channel identifiers match. Statistics and exports always
describe the active trace, not a subtraction or average of the reference. Use
**Clear reference** to release it, or **Reset analysis** to clear the active time
range and baseline. No automatic smoothing or baseline fitting is applied.

Whole-hop current-versus-Z includes any recorded motion, holds and retraction.
It is **not** automatically classified as an approach-only curve. Use an explicit
time window if phase separation is needed.

## CVs

The CV tab separates complete cycles consistent with the saved start/vertex/return
program. Select one for extrema, or Ctrl/Cmd-click several for overlay. The display
is limited to 50 selected curves to remain responsive; the cycle export contains
all extracted cycles. Incomplete cycles remain in the source but are not labelled
as complete. Choose Current 1 or Current 2 when that channel exists.

For legacy data, **Set CV program** can supply missing waveform parameters. This
only changes the in-memory interpretation and preserves other metadata; it does
not rewrite the source JSON. Extraction is waveform-based and is not a general
phase classifier. A malformed or unrelated waveform should be inspected in raw
data before interpreting a derived CV.

## Hop maps

Maps use the physical coordinates in the recording's `scan_grid.pixels` metadata,
so serpentine acquisition order does not reverse every other displayed row.
Unvisited cells remain absent; no interpolation across hops or zero-filling is used.

- **Mean / Minimum / Maximum / Std deviation:** statistics over all finite samples
  in a recorded hop. This includes approach and retract where recorded. In
  particular, a Z statistic is **not confirmed contact topography**, and a mean
  current is **not a pulse-only/surface current**.
- **CV at potential:** select a current channel, potential, and increasing or
  decreasing E direction. The app linearly interpolates the first crossing of
  that direction in each complete CV, then averages contributing cycles per hop.
  It never extrapolates outside a cycle's measured potential range. Missing or
  incomplete cycles do not contribute. Exported `samples` means contributing
  cycles in this mode, and finite samples in whole-hop statistics mode.

Choose a colormap and click a visited cell to inspect its entire hop in the
explorer. Recordings lacking trustworthy physical grid metadata show an explanation
instead of an invented map. No contact/pulse phase is inferred from line numbers.

## Exports and traceability

**Export selection** writes the selected, baseline-adjusted XY samples at full
resolution, plus JSON containing the source path, size, modification timestamp,
terminal recording status, selection scope, channels, time bounds, baseline and
measurement results. **Export map** similarly records its statistic, units,
potential and direction. Source path/size/time are provenance aids, not a
cryptographic checksum. The reference overlay is not included in these exports.

**Export separated CVs** writes all extracted cycles with pixel/cycle/point IDs
and source channel columns. Keep the original recording and JSON alongside this
export to retain the complete instrument context. Derived XY/map exports are not
intended to be re-imported as acquisition recordings.

The original recording and its matching JSON cannot be selected as export targets.
An export failure is reported; inspect both destination files before using a
partially written export. The source is unaffected.

## Scaling and current limitations

- Numeric arrays replace persistent per-sample dictionaries; slices share storage.
- The raw table virtualizes cells and exposes all rows, not a 5,000-row preview.
- Display reduction retains ordered local X/Y extrema and invalid-data breaks.
  Measurements and exports never use those reduced display points.
- Hop grouping and waveform extraction run outside the GUI during import. One
  import runs at a time, with stale queued selections discarded before reading.
- Recordings and extracted subsets still reside in memory. This is not an
  out-of-core database: very large files can exceed available RAM. Later storage
  adapters can build on the independent selection/calculation interfaces.

Peak fitting, automatic surface-hold segmentation, smoothing, impedance analysis,
and compensation models are not silently enabled. WEC-AL workflows inspired the
hop/cycle selection, map and quantitative-analysis design; the implementations
here use eChemTips' saved schema and independent, tested calculation services.

## Add support for another experiment

Numerical extensions belong in `analysis_tools.py` or another Qt-free module.
Register an `AnalysisProvider` before constructing the analysis window:

```python
from echemtips.analysis_tools import AnalysisProvider, Selection, register_provider

def subsets(dataset):
    # Replace with a validated method-specific selection, retaining timestamps.
    return [Selection("New method", dataset.rows, "Explicit scientific scope")]

register_provider(AnalysisProvider(
    key="new_method", title="New method",
    supports=lambda dataset: "new_signal" in dataset.columns,
    extract=subsets,
))
```

Keys must be unique. Providers receive `AnalysisDataset` and return named
`Selection` objects backed by `NumericRows`. They must not access Qt widgets,
mutate source samples, or guess ambiguous phases. Startup registration is explicit;
the application does not execute plugins found beside a data file. The explorer
discovers the registered provider automatically. Add numerical tests, incomplete
recording tests and a UI test for each new provider.
