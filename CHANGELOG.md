# Changelog

All notable changes to eChemTips are recorded here. The project follows
[Semantic Versioning](https://semver.org/) while its public API is pre-1.0.

## Unreleased

### Changed

- Reduced contextual help to six genuinely useful explanations; removed info
  buttons from ordinary plots, page titles and waveform previews.

- Standardized experiment actions, electrochemical potential terminology,
  movement speeds and plot tabs across the control interface.
- Removed redundant implementation notes and moved background plot/setup
  explanations into accessible hover/click info buttons. Measurement controls,
  file formats and hardware behavior are unchanged.

### Fixed

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
