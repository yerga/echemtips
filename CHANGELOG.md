# Changelog

All notable changes to eChemTips are recorded here. The project follows
[Semantic Versioning](https://semver.org/) while its public API is pre-1.0.

## Unreleased

### Added

- Analysis workbench with registered experiment selectors, arbitrary numeric
  channels, full-resolution time-window statistics and signed charge, constant
  baseline subtraction, reference overlays, and protected provenance exports.
- Physical whole-hop statistics maps and increasing/decreasing-sweep CV-current
  maps, linked to hop inspection. No contact or pulse phases are guessed.
- Compact numeric recording storage, background imports, a full virtual data
  table, and extrema-preserving display reduction with bounded CV overlays.
- Control **Analysis** menu and F6 launcher using a separate process, plus a
  shortcut for the last completed/aborted saved recording.

### Fixed

- Laptop experiment views now stack maps and paired traces when narrow and
  scroll instead of compressing axes. Hide setup gives plots more width;
  Expand plots opens the live tabs in a larger window with Stop and Emergency
  Stop available. Map color scales no longer show duplicate quantity/unit labels.
- Normal hopping CV/I–t completion now returns Z directly toward the initial
  approach position at the configured retract speed, without moving X/Y. If
  already farther retracted, Z is held rather than moved toward the surface.
  Completion and recording finalization wait for the return waypoint to finish.
  Production Stop and Emergency Stop behaviours are unchanged.
- Hopping CV and I–t now permit an initial Z at the travel boundary. Retraction
  is limited at runtime using the actual contact coordinate, including raster
  flyback distances. Shortened travel is displayed and saved in JSON parameters
  as `retraction_events`. If no usable retract travel remains, the scan stops
  before further XY motion. No bitfile change is required.
- Repeated hardware contacts now use the existing type-2 stop-on-feedback
  waypoint, avoiding EndCurrentLine's read-only, session-wide one-shot latch
  that caused the second scan point to stall. Added reusable, acknowledged
  manual/no-contact exits, verified secondary-threshold restoration and multi-scan
  regression coverage for CV/I–t and absolute/baseline-relative thresholds.
  No bitfile change is required; controlled hardware retesting is required.
- Approach, Approach + CV/I–t and hopping scans now neutralize the unused
  secondary FPGA comparator with an unreachable I32 threshold. The target
  ORs both comparators; Current 2 above zero previously caused unwanted pauses
  even when the selected current had not reached its contact threshold.
- Contact completion waits for acknowledgement and drains remaining samples
  before surface work, without relying on the one-shot EndCurrentLine latch.
  Resume releases only External Pause, allowing simultaneous operator and
  feedback pauses to be resolved without overriding contact ownership.
- Unknown contact pauses allow bounded sample/register reconciliation, then
  fail closed with register diagnostics. Z motion completion alone is no
  longer accepted as contact. Fault latches cannot be bypassed using Resume.
  These are host-only fixes; no FPGA binary change is required. Physical
  validation remains necessary on each instrument.

### Changed

- Added a packaged pipette/meniscus logo to both app sidebars and Qt window
  icons, with a shared application identity and clearer control-window title.

- Organized Settings into Connection, Acquisition, Piezos, Amplifiers, Saving,
  Plots and Maps tabs, with independent scrolling and an always-visible
  save/apply action for all categories.

- Added independent saved Z/current colormaps, synchronized across both scan
  methods, square cells, circular footprints and color bars.

- Added saved display preferences for independent Z/current map color limits,
  monitor/experiment rolling windows, pA/nA/automatic current units, font size
  and trace thickness. Unit conversion is rendering-only; recordings and
  exports retain their original units. Analysis loads typography/current-unit
  preferences on opening. Larger fonts reflow Settings and the toolbar.

- Moved scan map shape and meniscus footprint diameter into shared, persistent
  Display settings. Display-only changes retain the connection and existing maps.

- Bitfiles are explicitly selected or supplied through `ECHEMTIPS_BITFILE`;
  removed private filename discovery and filename-based settings migration.
  Preserved saved paths; new configurations default to Auto transport.
- NI compatibility checks retain the register/FIFO and selected transport
  contract without pinning USB hardware to one model/build signature. Other
  builds still require matching FPGA behavior and physical commissioning.
- Generalized hardware guides and added WEC-SPM origins, software-access link
  and the McKelvey et al. instrument tutorial citation.

- Reduced contextual help to six genuinely useful explanations; removed info
  buttons from ordinary plots, page titles and waveform previews.

- Standardized experiment actions, electrochemical potential terminology,
  movement speeds and plot tabs across the control interface.
- Removed redundant implementation notes and moved background plot/setup
  explanations into accessible hover/click info buttons. Measurement controls,
  file formats and hardware behavior are unchanged.

### Fixed

- Wrapped experiment descriptions now reserve their full text height; waveform
  previews leave screen-space headroom for peak-value annotations.

- Parent parameter-unit labels before making them visible, preventing brief
  standalone windows from flashing during application startup on Windows.

- Retry transient Windows locks during atomic recording-metadata replacement,
  preventing brief access-denied errors from aborting experiments. Persistent
  locks and other disk errors remain failures with partial data preserved.

- Hardware elapsed time now accumulates FPGA inter-sample tick intervals.
  Treating those intervals as absolute timestamps previously collapsed steady
  acquisitions into vertical time plots and incorrect CSV elapsed times.

- FPGA connections now reset and verify the target before configuration and
  startup, allowing reconnects to a previously running VI. The startup notice
  explains reinitialization; failed resets close the session without running it.

### Added

- Task-oriented operator guide for every current control and experiment.
- Versioned recording-schema, settings, calibration, real-hardware
  commissioning, troubleshooting, architecture, and contributor references.
- Public docstrings across application modules, models, services, protocol,
  backends, state machines, and UI components.
- Automated documentation-link, schema-reference, public-docstring, strict
  MkDocs, and GitHub Pages checks.

### Changed

- Resolved migration-gap reports are preserved as dated historical notes
  rather than presented as active backlog documents.
- Hardware terminology distinguishes an offline-supported target profile from
  a physically commissioned instrument.

## 0.1.0 - 2026-09-06

### Added

- Initial public eChemTips release with PySide6/PyQtGraph control and analysis
  applications, deterministic simulation, NI USB-7856R host protocol support,
  full-rate recording, CV/approach/I–t/hopping methods, and MIT license.
