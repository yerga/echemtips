# Changelog

All notable changes to eChemTips are recorded here. The project follows
[Semantic Versioning](https://semver.org/) while its public API is pre-1.0.

## Unreleased

### Changed

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
