# UX implementation and acceptance checklist

This checklist tracks the September 2026 UX review. Test in **Simulation first**,
using a temporary data folder. Hardware checks require the normal commissioning
precautions. These changes do not modify the FPGA target, bitfile, feedback
protocol, or emergency-stop implementation. Headless tests are not a substitute
for checking the real Windows laptop at its usual display scaling.

**Status key:** Implemented = the described scope is available; Partial = a useful
subset is available with an explicit remaining limitation; Alternative = the
underlying need is addressed without the originally suggested structure.

## Recommended quick test

1. Open control at your laptop's normal display size. Collapse the sidebar with
   **View → Show sidebar** or Ctrl+B. Open the library with Ctrl+K.
2. Connect Simulation and run a small scan. Check readiness, locked setup,
   recording count, and the persistent readback. Pause/resume normally.
3. Drag the setup/plot divider, hide setup, and choose a single map. Close and
   reopen; check restored geometry, split sizes, chosen plot tab and hidden setup.
4. Use **Plot → Freeze display** during Watch current. Recording must continue.
   Select **Fit / return to live**. Pan/zoom manually and confirm incoming samples
   do not reset your view until you request Fit.
5. Save an experiment preset, change fields, then reload it. No movement should
   occur until Start. Try a malformed numeric value; the relevant field should
   receive focus and an error indication.
6. Change trace thickness through **Apply for session**, then restart. Startup
   defaults should be unchanged. Repeat using **Save as defaults and apply**;
   the setting should persist. Revert edits should restore the applied values.
7. Open analysis from control. Drop a scan CSV on the analysis window. Enable
   smoothing and press Apply; compare with **Overlay original** in CV / LSV.
8. Click a map hop, then view the selected cycles. Switch i–E, E–t and i–t.
   Test multiple CV cycles per hop and a scan-rate series recording.
9. Save an analysis session, change map/smoothing settings, then reopen it.
   Confirm settings restore and the original recording remains unchanged.
10. Prepare a whole-CV movie. Verify potential path/cursor, frame labels,
    playback duration and export. Inspect events under **Instrument** after
    testing a recoverable validation error.

## Control: readiness, setup and safety

| # | Status | What to test / behavior |
| --- | --- | --- |
| 1 | Implemented | The menu-bar indicator distinguishes disconnected, ready, running, paused, recording and recovery-required states. A stopped hardware session that requires recovery cannot start another experiment. Test the real hardware stop separately; this does not introduce recoverable stop into main. |
| 2 | Implemented | The operator's manual-contact action is labelled **Accept Z as contact**. The low-level **End current waypoint** is under Instrument → Advanced controls, separate from normal Pause/Resume, Stop and Emergency stop. Do not use it as routine contact detection. |
| 3 | Implemented | Starting a managed experiment on NI hardware opens a review with polarity, data folder, key motion/contact parameters and a detailed parameter snapshot. Cancel is the default. Simulation skips this prompt. Confirm Cancel sends no experiment command. |
| 4 | Partial | Readiness shows recording status and sample count; its tooltip shows the filename. Instrument → Show current recording in folder opens its directory. This counts samples accepted by the recorder, not a guarantee of physical disk flush. A separate disk-throughput/durability monitor is not implemented. |
| 5 | Implemented | Readback becomes visibly **STALE** after two seconds without a new sample and is dimmed; before any sample it reads **NO DATA**. Hover for sample age. This is a display freshness check, not a new hardware interlock. |
| 6 | Implemented | **SIMULATION** remains explicit in the readiness indicator and uses a distinct warning color; it is not hidden by changing experiment pages. |
| 7 | Implemented | Instrument → Measured / commanded position details shows a cached snapshot and differences per axis. Missing command values are reported unavailable, not invented or copied from the sensors. |
| 8 | Partial | Invalid common numeric fields identify the field, focus it and mark it in red. Correcting the text clears the indication. Cross-field and hardware validation still use a detailed error dialog rather than an inline summary for every constraint. |
| 9 | Implemented | Start is disabled while disconnected, another experiment/recording owns execution, recovery is required, or a combinatorial plan is missing. Its tooltip explains why. The setup of the running experiment is locked; plots use the active parameter snapshot. |
| 10 | Implemented | The **Preset…** menu saves/duplicates/loads validated JSON measurement presets, including supported scan-rate and recipe controls. Mismatched experiment types or polarity conventions are rejected. Failed loads roll back controls. Presets never issue motion commands. |
| 11 | Implemented | Settings separates unapplied edits, session application, saved startup defaults and Revert edits. Verify restart behavior. Instrument-setting changes still require reconnect; changes during an active experiment are rejected. |
| 12 | Implemented | Display-only preferences, including units, colormaps, rolling windows and trace styling, can be applied during a run without replacing the backend. Test that acquisition and recording continue. |
| 13 | Implemented | Grid scans provide **Preset… → Set scan center / size / spacing…**. It previews exact point count and actual spacing, preserving endpoints when counts are rounded. Bounds are validated. This helper sets increasing X/Y bounds; endpoint fields remain available for reverse directions. Reconfirm recipe assignments after a grid change. |
| 14 | Partial | Existing motion/waveform/scan previews, motion labels, polarity context and hardware-start review expose key consequences. Return/retract/marker semantics are not yet collected in a single permanently visible summary. |
| 15 | Partial | Existing Z/potential profiles remain, alongside the full hardware-start parameter review. One combined time-scaled diagram of XY, Z, settling and potential for every specialized method is not implemented: contact time is unknown and needs explicit estimated-versus-measured semantics. |

## Workspace and plots

| # | Status | What to test / behavior |
| --- | --- | --- |
| 16 | Alternative | Keep one experiment page with setup locking, Hide setup, Expand plots and an adjustable divider; open the separate analysis workbench for review. Separate Setup/Run/Review routes were not added, avoiding duplicated form/runtime state. |
| 17 | Implemented | Collapse/restore the sidebar from View or Ctrl+B. Its width depends on pinned navigation rather than the longest library experiment. Sidebar visibility is remembered. The searchable library and favorites remain available. |
| 18 | Implemented | Drag the horizontal divider on two-pane experiment pages. Setup and plot sizes are remembered independently per experiment. |
| 19 | Implemented | The Maps tab offers **Both maps / Contact Z only / Current only**. Single-map mode expands the selected map without deleting the other map's data. |
| 20 | Implemented | Shared trace controls expose Fit, live display freeze, cursor inspection and PNG/SVG export. PNG width is configurable. Freeze affects rendering only, not recording. Heatmaps retain their own map controls rather than the trace toolbar. |
| 21 | Implemented | Manual pan/zoom is preserved during redraw. **Fit / return to live** explicitly restores automatic ranges and unfreezes live display. |
| 22 | Partial | Time-domain live plots share a cursor within the experiment page. Analysis map clicks select the corresponding hop and CVs. Arbitrary linked range brushing across every plot and quantitative multi-signal cursor readouts are not implemented. |
| 23 | Implemented | Control window geometry, split widths, setup visibility, selected plot tab and sidebar visibility persist in workspace.ini, separate from instrument defaults. Analysis window geometry is stored separately. Test closing and reopening. |
| 24 | Partial | Numeric inputs have accessible names and label buddies; library/sidebar shortcuts and standard keyboard navigation are available. A complete screen-reader/high-contrast certification and customized shortcuts for every command remain untested. No shortcut was added that could accidentally trigger hardware motion. |

## Analysis

| # | Status | What to test / behavior |
| --- | --- | --- |
| 25 | Implemented | Persistent context states recording, selected condition, processing and source status. Loading/cancellation/failure messages explicitly identify when previous data remain displayed. |
| 26 | Partial | CVs are grouped by hop or scan-rate series with cycle children and searchable labels; condition selection remains global. Selecting a parent selects its visible children. Fully nested condition → hop → rate → cycle combinations are not all represented in one tree. |
| 27 | Implemented | Large selections display up to 50 evenly spaced representative cycles rather than the first 50. The displayed/selected count is stated; exports retain all selected data according to their export scope. |
| 28 | Implemented | CV maps default to **CV at potential**, with cycle/segment selectors. I–t scans offer **Surface I–t mean**, using identified surface rows rather than travel/approach data. The mean covers the validated surface program, not automatically only its pulse. Legacy/general whole-hop statistics remain explicitly selectable. |
| 29 | Partial | Map coverage states usable versus total locations and explains omissions. Older files cannot reliably distinguish missing contact, excluded cycles, absent potential coverage and other failure causes per pixel; no unsupported diagnosis is assigned. A versioned per-landing quality record would be required for exhaustive failure labels. |
| 30 | Implemented | Click a hop map: the explorer moves to that hop and corresponding CV rows are selected. Inspect the CV tab to compare them. Source values remain unchanged. |
| 31 | Implemented | Editing smoothing shows **not applied** until Apply finishes. Context reports method/window and approximate time span, with a warning when sampled timing is irregular. **Overlay original** compares CV currents. Disable smoothing and Apply to reset. Time span is estimated from the first 10,001 samples, not a promise of uniform sampling across a long file. |
| 32 | Partial | Plots and maps respect display-current preferences. Raw tables, some numerical summaries, baseline inputs, movie limit inputs and exported numeric data retain explicitly labelled native nA units. Conversion of every analysis control to display units is not complete; source units are never silently changed. |
| 33 | Partial | Recording list includes a metadata preview and file size without parsing all data. Drag-and-drop opening is supported. A database-backed recording catalogue, recursive search and recent-folder browser are not included. |
| 34 | Implemented | Background loading has a Cancel action; old plots are inactive while loading. Late results from cancelled/replaced requests cannot overwrite the displayed recording. Cancellation abandons results, not an unsafe forced termination of in-progress disk I/O. |
| 35 | Implemented | Workspace → Save/Open analysis session persists processing, selected condition, channel/map/movie controls, range/baseline and selected tab. JSON stores source path, size and modification time and refuses changed sources. Source CSV/JSON cannot be overwritten by session saving. Moved files require opening normally; automatic relocation is not implemented. |
| 36 | Partial | Derived CSV exports retain provenance sidecars; sessions record analysis choices; trace figures export PNG/SVG at a selected width. There is not yet one unified publication-export dialog or provenance sidecar for every image format. |

### What “original” means

The Data table defaults to **Original measurement values (all conditions; marker
excluded)**, before smoothing and condition filtering. Orientation-marker rows
remain intentionally excluded by the analysis importer; this is not a byte-for-byte
viewer of every line in the CSV. Switch the checkbox off to inspect processed,
condition-selected data. Original files are never modified by smoothing.

## Specialist methods, movies and diagnostics

| # | Status | What to test / behavior |
| --- | --- | --- |
| 37 | Implemented | Combinatorial planner → **Edit selected…** opens a labelled form for one condition; validating it updates the recipe table. Duplication, matrix generation and table editing remain available. |
| 38 | Implemented | The recipe form disables reverse vertex/cycle controls for LSV and sets one sweep. The compact table marks these shared fields as unused and non-editable for LSV; the validated LSV compiler ignores reverse-waveform parameters. |
| 39 | Partial | The planner shows colored assignments, condition counts and waveform preview, and can be reopened after accepting a plan. The main page retains its accepted-plan summary rather than duplicating the full assignment matrix. |
| 40 | Implemented | Assignment preview cells include chronological **Hop N** labels in physical row/column positions, including serpentine order. Compare against the scan path preview and raster mode. |
| 41 | Partial | Adaptive approval exposes XY, acquisition rationale, prediction/uncertainty when available, remaining landing count and tilt/clearance assumptions. Sparse tilt points explicitly do not prove absence of obstacles. A per-proposal travel-Z drawing and remaining-time confidence estimate are not added; the motion planner still validates travel before launch. |
| 42 | Implemented | Measured objective, predicted objective, uncertainty and contact Z retain distinct adaptive views; approval labels predictions as **not measured**. |
| 43 | Implemented | Movies distinguish **Encoding FPS** from **Playback time per frame**, report effective frame interval and total duration, and retain frame skipping. Verify preview and MP4 with a short recording before a large export. |
| 44 | Partial | Whole-CV movies show the potential path with a moving frame cursor, including return sweeps. Other movie types retain explicit frame potential/time labels; a synchronized waveform panel is not added for every type. |
| 45 | Partial | Hardware diagnostic starts ask for fixture confirmation before timed acquisition or sweeps. Existing guided preflight remains. This is not automatic wiring validation; a universal wiring diagram would be unsafe across different instruments. |
| 46 | Implemented | Instrument → Event history / support report provides searchable, copyable events; an on-disk rotating journal retains messages beside settings. Errors offer details instead of only transient toasts. The dialog lists current-session events; archived journal files can be attached separately. |

## Additional acceptance checks

- At 100%, 125% and 150% Windows scaling, inspect long recipe names, error dialogs,
  the readiness corner, single-map mode and the waveform profile. Headless Qt
  geometry tests do not validate native Windows font rendering.
- Run all watch/approach/scan methods in Simulation, including scan-rate series,
  combinatorial and adaptive methods. Verify recording continues while plots are
  frozen and changing display settings does not reset elapsed time.
- On a safe electrical fixture, cancel a real-device pre-run review, then start a
  small run and stop. Confirm existing recovery behavior is described accurately.
  **Do not test emergency stop with a fragile pipette near the sample.**
- Open a legacy dataset, a multi-cycle scan, a combinatorial recording and a
  scan-rate series. Check blank-map behavior and native/display unit labels.
- Treat Partial items as remaining work, not as features silently implemented.
  In particular, automated landing-quality classification requires additional
  metadata and experimental validation; the interface must not manufacture it.
