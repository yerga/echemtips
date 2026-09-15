# Python API reference

This reference is generated from the source docstrings. It covers the reusable
services and extension points rather than every Qt implementation detail. Start
with the [architecture guide](ARCHITECTURE.md) for ownership and data flow.

## Settings, samples, and method parameters

`models` defines the values that cross UI, experiment, backend, and recording
boundaries. Keep units in field names and preserve dataclass compatibility when
evolving persisted settings or metadata.

::: echemtips.models
    options:
      members:
        - AppSettings
        - SettingsStore
        - FeedbackConfiguration
        - Sample
        - CVParameters
        - ApproachParameters
        - ApproachCVParameters
        - ApproachITParameters
        - ScanHoppingCVParameters
        - ScanHoppingITParameters

## Instrument backends

Pages and experiment state machines depend on `InstrumentBackend`. A new
transport should implement that contract, report truthful capabilities, and
leave experiment sequencing outside the driver.

::: echemtips.backends
    options:
      members:
        - BackendError
        - BackendCapabilities
        - ExecutionStatus
        - InstrumentBackend
        - SimulationBackend
        - NIFPGABackend
        - create_backend

## Acquisition and host execution

The acquisition worker owns blocking reads away from the Qt thread. The host
executor owns exactly one submitted hardware program and its FIFO refill and
completion lifecycle.

::: echemtips.acquisition
    options:
      members:
        - AcquisitionDrain
        - AcquisitionWorker

::: echemtips.host
    options:
      members:
        - ProgramExecutionError
        - ProgramExecutionStatus
        - WaypointExecutionService

## Waypoint compilation

Waypoint builders express physical programs. `WaypointCompiler` performs the
common conversion into the fixed FPGA wire format.

::: echemtips.waypoints
    options:
      members:
        - Waypoint
        - CompiledWaypoint
        - CompiledProgram
        - WaypointCompiler
        - build_move_waypoints
        - build_cv_waypoints
        - build_approach_cv_waypoints

## Experiment state machines

State machines translate samples and backend status into method progress. They
do not render widgets or write files.

::: echemtips.experiments
    options:
      members:
        - ExperimentState
        - CVExperiment
        - ApproachExperiment
        - ApproachCVExperiment
        - ApproachITExperiment
        - ScanHoppingCVExperiment
        - ScanHoppingITExperiment

## Recording and analysis

`DataRecorder` is the only owner of recording file lifecycle. `AnalysisDataset`
normalizes current CSV, legacy TSV, and supported TDMS recordings for analysis.

::: echemtips.data
    options:
      members:
        - DataRecorder

::: echemtips.analysis_core
    options:
      members:
        - AnalysisError
        - AnalysisDataset
        - CVCycle
        - extract_cv_cycles

## Diagnostic workflows

Preflight and pipette characterization use small reusable workflow objects so
their safety state and results can be tested without Qt.

::: echemtips.diagnostics
    options:
      members:
        - WorkflowState
        - CheckResult
        - PreflightWorkflow
        - PipetteCharacterizationWorkflow

## NI FPGA protocol boundary

Protocol declarations and conversions are centralized here. Changes require
offline bitfile inspection and then controlled hardware commissioning; matching
names alone is not adequate evidence.

::: echemtips.ni_protocol
    options:
      members:
        - ProtocolError
        - FIFODescriptor
        - RegisterDescriptor
        - ProtocolManifest
        - inspect_bitfile
        - validate_manifest

::: echemtips.ni_driver
    options:
      members:
        - NIFPGADriver
