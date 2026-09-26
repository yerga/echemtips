# Figures, plot data, map crops and potential references

These are **offline analysis tools**. They never change instrument commands or
rewrite acquisition CSV/JSON files. After updating, run
`python -m pip install -e .` in the project environment: publication rendering
uses Matplotlib, now a required dependency.

## Export the selected plot's data

Use **Plot → Export plotted data as CSV…** on an Explore/measure or CV plot.
Select the desired cycle(s), channel and view first. This exports every sample
of each **displayed series**, not the reduced points drawn for interactive speed.
Panning/zooming alone does not select samples; use the explorer time selection
to limit a trace. The CV display's 50-curve cap also applies to this plot export.

The long-form columns are `series_id`, `series`, `sample`, `x`, `y`.
`sample` is zero-based within the series. The JSON sidecar gives X/Y labels and
units, source identity, current processing and reference conversion. Displayed
current units are respected. Pinned references and original-data overlays, when
visible, are separate named series in this export.

**Export all separated CVs…** remains the bulk export, including cycles beyond
the display cap. It is hidden when no complete CV/LSV sweeps are available.
**Export selection…** retains its quantitative-analysis export of the active
trace only; **Export map…** saves the currently selected map cells and selection
recipe. Derived files are not acquisition recordings.

## Publication figures

Use **Plot → Export figure…** for traces, or **Export figure…** above a map.
For the movie waveform timeline, right-click it. Movie-map export takes the
currently displayed frame; it is separate from MP4 export.

The dialog takes a snapshot so formatting does not modify the live analysis:

1. Set the title, axis labels and (for maps) colour-bar label.
2. Choose physical width/height in millimetres. Default: 150 × 110 mm,
   independent of the application window. For a single-column journal figure,
   try 85 × 65 mm and inspect the preview before saving.
3. Choose font family, font size in points and trace width in points; toggle
   the legend. Long legends can need a wider figure or shorter trace labels.
4. For maps choose a perceptually uniform colormap such as viridis or cividis,
   and automatic or manual colour limits in the displayed units. A diverging
   map is useful for signed data when accompanied by appropriate symmetric limits.
5. **Preview**, then save PNG/TIFF (default 300 DPI, adjustable up to 1200),
   or PDF/SVG for vector graphics. PDF embeds TrueType fonts; SVG keeps text
   editable. Export preserves physical size rather than using tight-crop output.

Maps keep equal physical X/Y scaling, missing cells remain blank, and cropping
does not alter cell values or hop spacing. Circular views retain their physical
diameter. No spatial smoothing, interpolation between landings or replacement of
missing measurements is performed by figure export. Full-resolution vector
figures of very long traces can be large and take time to render. Use a deliberate
time selection if only a section is needed. Check the figure against the target
journal's requirements; “publication-ready” does not imply one universal style.

Each figure has a separate `<figure extension>.json` sidecar containing formatting,
source, processing, reference conversion and map/frame selection information.
CSV and figure exports do not overwrite source recordings. A destination failure
can leave an incomplete export; check both output files before distributing them.

## Crop map rows and columns

Choose **Crop XY…** in Hop maps or Movies. Enter inclusive minimum/maximum **cell
centre coordinates** in µm. To remove a first row, move Y minimum to the next
row's coordinate; to remove a last column, move X maximum to the previous column.
**Reset** restores the full grid. This is a rectangular view/analysis selection,
not deletion, coordinate translation or resampling.

Static-map CSV and figure exports use the cropped selection. In Movies, prepare
frames again after changing the crop; the crop is applied before computing
automatic colour limits and is included in MP4 export metadata. Crops are
independent for static maps and movies and are saved in analysis sessions.
New recordings reset crops; a crop containing no usable measurements produces
an explanation instead of a stale map.

## Optional potential-reference conversion

Open **Potential reference…** beside the analysis processing controls.
Conversion is off by default, applies to **E1 only**, and is reset when a recording
is opened normally. Save an analysis session to retain the choices for that file.
The source-data table still provides unconverted values; processed views and
exports carry the converted scale. Source CSV and JSON remain unchanged.

Confirm the *recorded potential polarity* explicitly. The output E1 uses the
IUPAC potential direction. Current signs and E2 are **not** changed. In particular,
native WEC-SPM current remains native current even when E1 is converted; this
feature does not claim to normalize an entire recording's polarity convention.

The database contains nominal aqueous potentials at **25 °C**:

| Reference | Potential versus SHE |
| --- | --- |
| SHE | 0 V |
| RHE | −0.05915935 × pH V |
| Ag/AgCl, saturated KCl | +0.197 V |
| Ag/AgCl, 3 mol/L KCl | +0.207 V |
| Saturated calomel (SCE) | +0.241 V |

The transformation is:

`Eout = sign × Erecorded + Esource(SHE) − Etarget(SHE) + custom_offset`

Here `sign` is +1 for recorded IUPAC potential and −1 for instrument-native WEC-SPM
potential. A positive custom offset adds to the resulting potential. Source and
target RHE each have a pH field when relevant. At pH 7, converting IUPAC potential
from saturated Ag/AgCl to RHE adds approximately 0.6111 V.

**Custom additive offset** bypasses the database: supply your calibrated offset
and name the output reference. Database mode also permits an additional offset.
A bare Ag/AgCl quasi-reference in a SECCM pipette is **not automatically** a
saturated-KCl reference: its potential depends on electrolyte composition and
activity. For such electrodes, prefer an experimental calibration. Temperature,
liquid-junction potentials, nonaqueous reference scales, drift and uncompensated
resistance are not corrected by these nominal values. The database fixes
temperature at 25 °C rather than extrapolating unsupported temperature coefficients.

For reference values and conversion context see the
[McAuley research group's explanation](https://mcauleygroup.net/article/1/),
[Metrohm reference-electrode table](https://www.metrohm.cn/content/dam/metrohm/shared/documents/fact-sheets/81098050EN.pdf)
(3 mol/L is not the same concentration convention as 3 mol/kg), and the
[RHE convention used in this electrochemical study](https://doi.org/10.1002/anie.202102803).

Converted waveform endpoints follow the same transformation as E1 samples, so
CV extraction, chronological segments, potential-map selection and movie axes
remain consistent. **Set CV program** always edits the *original recorded scale*.
Offsets are always applied to original data, never repeatedly to converted data.
Pinned explorer references with different conversion settings are hidden to avoid
overlaying incompatible potential scales.

## Suggested acceptance test

1. Open a multi-hop CV recording. Select one cycle, export its plotted CSV, and
   check that only that series is present. Repeat with E–t and i–t views.
2. Open Watch current data: the bulk separated-CV export should be absent.
3. Export the same trace at different application-window sizes: saved physical
   dimensions should stay unchanged. Inspect PNG and SVG/PDF text and line quality.
4. Crop one edge row/column, export the map, then reset. Check original hop IDs,
   coordinates and cell spacing, including a crop leaving a single row.
5. Repeat the crop in Movies and prepare again. Inspect frame and MP4 exports.
6. Convert an IUPAC saturated-Ag/AgCl recording to RHE at pH 7. Select a map
   potential 0.6111 V higher than before: map currents should be the same within
   the potential control's rounding. Current signs must not change.
7. Apply smoothing again: the reference offset must not accumulate. Disable
   conversion to restore the original potentials.
8. Save/open an analysis session containing conversion and crops. Verify restored
   choices and unchanged original recording files.
