# Interface tour

The control application keeps method setup on the left, live results on the
right, connection and execution controls at the top, and measured instrument
values in a persistent strip at the bottom.

![Scan hopping maps in the control application](images/control-scan-maps.png)

## Live trace performance

Hardware and simulation use the same live renderer. Visible curves refresh at
most about 10 times per second using fast segmented lines; acquisition and
recording continue independently. Hidden plots retain their current history and
refresh when shown. Rolling time traces and rolling current-versus-Z histories
keep **every sample within the selected time window**, removing only expired
samples. They do not repeatedly thin older points. Very high sample rates or
long windows still require more memory and rendering work.

The **Non-rolling display buffer** setting only limits non-rolling curves such as
CVs; full-rate recording is unchanged. To test without hardware, select the
simulator, connect, and start **Watch current → Start live view**. Run longer than
the monitor window (30 seconds by default), then switch away and back. The trace
should advance smoothly and show the latest window. A simulated hopping scan
exercises the same renderer in **Experiment traces** and **Approach curves**.

## Navigation

**Move piezo** is always first; **Settings** is anchored at the bottom. Between
them are ordered favorites, initially Watch current/position, Approach + CV/I–t,
and Scan hopping + CV/I–t. Long favorite lists scroll without hiding the anchors.

Open **All experiments…** (or `Ctrl+K`) for the full searchable library, including
pages already pinned in the sidebar. Filter by category or search names and
descriptions. Select a result to **Open**, **Pin/Unpin**, or **Move up/down** in the
sidebar order. **Restore defaults** restores the original shortcuts. Move piezo
and Settings cannot be unpinned. Favorites are saved automatically in
`navigation.json` beside user settings, independently of hardware configuration.

Navigation never starts or stops a method. During an experiment, diagnostic, or
monitor recording, the global **Running: … · Return** button opens its page even
if it is not pinned. The library is non-modal, so execution and acquisition
continue while it is open. `Ctrl+1` opens Move piezo; subsequent numbered shortcuts
follow the current favorites order.

Experiment names and parameter labels are consistent across pages. Both hopping
methods use **Start scan** and **Stop experiment**. **Experiment traces** separates
time-domain data from **CV** or **I–t** results; scans show **CV at hop** or **I–t at hop**.

**ⓘ** is reserved for non-obvious details: command voltage ratio, acquisition
timing, display versus recording, pipette-radius assumptions and current-map
definitions. Ordinary plots and page headings have no help buttons. Hover for
a tooltip or click/keyboard-
activate it for a persistent, wrapped help dialog. Essential setup instructions,
units, recording state and hardware safety warnings remain visible. The normal
hardware stop/reconnect behavior is unchanged.

- Select an experiment from the left sidebar. `Ctrl+1` through `Ctrl+9` open
  the first nine pages in sidebar order.
- **Connect** opens the simulator immediately. In NI FPGA mode it first shows
  the output-changing startup warning described in the commissioning guide.
- **Pause**, **Resume**, and **End waypoint** are low-level host controls and are
  enabled only when the backend advertises those capabilities.
- **EMERGENCY STOP** remains available at the top of every page.
- On scan pages, use **Experiment traces**, the method-specific hop tab,
  **Approach curves**, and **Maps** to separate long-running traces from local
  electrochemical results.
- Watch pages acquire and render only after **Start live view**. Moving to one
  of those pages does not replay data from another experiment.

The persistent bottom strip reports measured X, Y, Z, E1, E2, i1, and i2. It
does not present commanded position as measured position.

## Data analysis

The analysis application opens current recordings and supported legacy files.
Raw traces remain separate from detected CV cycles.

![Separated voltammograms in the analysis application](images/analysis-cvs.png)

- **Raw traces** plots the entire source recording with selectable current
  channels and applied potential context.
- **Voltammograms** overlays completed CV cycles or shows one selected cycle and
  its current extrema.
- **Raw data table** previews up to 5,000 rows without changing the source.
- **Metadata** shows the JSON sidecar used to interpret method parameters and
  scan geometry.

For a legacy file without sufficient CV metadata, use **Set CV program…** and
enter the waveform that was actually commanded. This changes the in-memory
analysis interpretation; it does not rewrite the source file.

## Rebuilding the screenshots

Screenshots are deterministic simulator fixtures, not hardware evidence. From
the project environment run:

```bash
python scripts/capture_docs_screenshots.py
```

The script uses an isolated temporary settings path and the Qt offscreen
platform. It never opens the NI FPGA backend.
