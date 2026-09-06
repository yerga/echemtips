# Shared LabVIEW host parity

This audit covers the reusable functionality formerly spread across `FPGA Host.vi`, `Update_Host.vi`, `ScaleWayPoints.vi`, `FindVelScaleFactor.vi`, `Set_Setting.vi`, `SetFlySettings.vi`, `GetValuesFromRefs.vi`, and the plot-buffer/viewer helpers. Method state machines call these services rather than owning NI sessions, FIFO framing, scaling, or rendering buffers themselves.

## Implementation status

| LabVIEW responsibility | Reusable Python service | Status |
|---|---|---|
| Execution ownership and state | `WECSPMDriver`, `ExecutionSnapshot`, `ExecutionState` | One owner at a time; queued, executed, pending, drain, completion, abort, pause, and error state are reported. |
| FIFO submission/refill | `WaypointStreamer` | Complete 14-word frames; 512-frame initial fill and 128-frame bounded refills; failed/uncertain writes latch the session. The old 585-frame limit is removed. |
| Safe completion/cancellation | `WECSPMDriver.service`, `cancel_program`, `read_samples` | Completion requires line-count agreement, unpaused waiting state, and final data drain. Normal cancellation acknowledges the target and permits a later program. |
| Waypoint scaling/compilation | `WaypointCompiler`, `PhysicalWaypoint` | X/Y/Z/V1/V2 targets and rates, concurrent axes, potential ramps/jumps, timed/indefinite holds, relative Z retracts, feedback actions, update interval, hold-feedback, and picomotor flags. |
| Common CV generation | `cyclic_voltammetry_plan` | Standalone CV, Approach + CV, and Scan Hopping + CV use the same start/vertex 1/vertex 2/start jump-or-ramp plan builder and compiler. |
| Common I-t generation | `potential_step_plan` | Approach + I-t and Scan Hopping + I-t share initial/pulse/return steps; long holds are split into adjacent signed-I16 microsecond timer frames. |
| Reusable experimental methods | `CVExperiment`, `ApproachExperiment`, `ApproachITExperiment`, `ScanHoppingITExperiment`; driver `start_method/status/context` | Simulation and NI paths share validated parameters, lifecycle states, stop/final-drain behavior, and data tagging. Surface measurements are contact-gated. |
| Live controls | Backend `pause`, `resume`, `end_current_waypoint`, `set_live_potential` | Generic resume cannot clear an FPGA-owned internal feedback pause. ChangeOnFly writes are range checked and pulsed through the deployed registers. |
| Feedback configuration | `FeedbackConfiguration`, `WECSPMDriver.configure_feedback` | Primary/secondary signal and threshold, polarity, P gain, maximum Z step, update interval, running-average terms, self-reference hold, and three bulk distances. Execution remains on FPGA. |
| Instrument profiles/provenance | `AppSettings`, Settings UI, `BackendCapabilities` | Explicit Simulation and USB-7856R profiles, capability-gated controls, advanced settings, calibration source/date/operator/notes, and separate measured/commanded position labels. |
| Full-rate recording and viewing | `AcquisitionWorker`, `DataRecorder`, `DisplayBuffer`, `Plot` | Acquisition is independent of Qt rendering; backlog/errors are surfaced; final samples are barrier-drained; persistent files keep every sample while PyQtGraph displays are decimated independently. |

## Verified boundary

The deployed USB-7856R `.lvbitx` contract is checked by datatype, access role, FIFO direction, and compiled target depth. Automated fake-session tests cover long-stream refill, every feedback action code and packed flag, simultaneous compilation, holds, relative Z, completion/cancellation/reuse, feedback writes, final acquisition drain, and plotting decimation. The offline checker validates a separately supplied instrument bitfile.

The generic driver limit is 65,535 waypoints in one submitted program. This is not a FIFO capacity limit. Scan plans use a stricter 32,767-tag guard until the target's U64-to-signed-I16 narrowing is confirmed, protecting unambiguous pixel assignment. Physical calibration, polarity, motion direction, and feedback response still require staged commissioning on the USB-7856R instrument PC.
