# Shared LabVIEW host parity

This audit covers the reusable functionality formerly spread across `FPGA Host.vi`, `Update_Host.vi`, `ScaleWayPoints.vi`, `FindVelScaleFactor.vi`, `Set_Setting.vi`, `SetFlySettings.vi`, `GetValuesFromRefs.vi`, and the plot-buffer/viewer helpers. Method state machines call these services rather than owning NI sessions, FIFO framing, scaling, or rendering buffers themselves.

## Implementation status

| LabVIEW responsibility | Reusable Python service | Status |
|---|---|---|
| Execution ownership and state | `WECSPMDriver`, `ExecutionSnapshot`, `ExecutionState` | One owner at a time; queued, executed, pending, drain, completion, abort, pause, and error state are reported. |
| FIFO submission/refill | `WaypointStreamer` | Complete 14-word frames; 512-frame initial fill and 128-frame bounded refills; failed/uncertain writes latch the session. The old 585-frame limit is removed. |
| Safe completion/cancellation | `WECSPMDriver.service`, `cancel_program`, `read_samples` | Completion requires line-count agreement, unpaused waiting state, and final data drain. Contact uses FPGA type-2 stop-on-feedback, not the session-wide one-shot EndCurrentLine gate. True cancellation retires the possibly partial stream and requires reconnection. |
| Waypoint scaling/compilation | `WaypointCompiler`, `PhysicalWaypoint` | X/Y/Z/V1/V2 targets and rates, concurrent axes, potential ramps/jumps, timed/indefinite holds, relative Z retracts, feedback actions, update interval, and hold-feedback. Picomotor commands are deliberately outside the current application scope. |
| Common CV generation | `cyclic_voltammetry_plan` | Standalone CV, Approach + CV, and Scan Hopping + CV use the same start/vertex 1/vertex 2/start jump-or-ramp plan builder and compiler. |
| Common I-t generation | `potential_step_plan` | Approach + I-t and Scan Hopping + I-t share initial/pulse/return steps; long holds are split into adjacent signed-I16 microsecond timer frames. |
| Reusable experimental methods | `CVExperiment`, `ApproachExperiment`, `ApproachITExperiment`, `ScanHoppingITExperiment`; driver `start_method/status/context` | Simulation and NI paths share validated parameters, lifecycle states, stop/final-drain behavior, and data tagging. Surface measurements are contact-gated. |
| Live controls | Backend `pause`, `resume`, `end_current_waypoint`, `set_voltage`, `set_live_potential` | Idle potentials use ordinary jump waypoints and are accepted only after line/waiting-state completion plus applied-output readback. ChangeOnFly is allowed only while the corresponding voltage axis loop is active; its request remains asserted until `Applied Voltage`/`Applied Voltage 2` acknowledges the raw target, then the host clears and verifies the trigger. This changes the current output but does not rewrite the queued waypoint endpoint. Generic resume cannot clear an FPGA-owned internal feedback pause. |
| Feedback configuration | `FeedbackConfiguration`, `WECSPMDriver.configure_feedback` | Current 1/2 contact signal, threshold, polarity, and a fixed safe update interval. Unused advanced registers are explicitly neutralized. Execution remains on FPGA. |
| Hardware selection | `AppSettings`, Settings UI, `BackendCapabilities` | A direct Simulation/NI FPGA backend choice, capability-gated controls, and separate measured/commanded position labels. Additional target profiles can be introduced when another FPGA target is actually supported. |
| Full-rate recording and viewing | `AcquisitionWorker`, `DataRecorder`, `DisplayBuffer`, `Plot` | Acquisition is independent of Qt rendering; backlog/errors are surfaced; final samples are barrier-drained; persistent files keep every sample while PyQtGraph displays are decimated independently. |

## Verified boundary

### Contact-pause protocol

Direct inspection of `FPGA Target.vi` shows that `EndCurrentLine` is guarded by
read-only `OnlyStopLineONCE` latches, initialized only at target startup. It
cannot be used to finish every contact in a scan. Approaches therefore use
**line type 2**, "STOP Current Move on SetPoint", which feeds **Feedback1 OR
Feedback2** directly into Z motion completion. This mode has no one-shot contact
gate. Unforced completion of the terminal approach waypoint proves a hardware
feedback event even if a brief transient was missed by host polling.

The UI now uses [either-polarity detection](BIPOLAR_CONTACT.md): both comparators
select the same current, with positive and negative magnitude thresholds.
For legacy signed-current API modes, the unused secondary comparator selects a signed-I16 current source,
an I32 threshold of 32768, and the greater-than direction. No signed-I16 input
can trigger it. The primary comparator remains the selected Current 1/2
threshold, including a translated baseline-relative threshold.

Type 2 ignores LinearMove's natural completion flag and holds at its bounded
endpoint without contact. The acquisition service detects the exact commanded
endpoint in Applied Z and requests completion through the secondary comparator
with threshold -32769 for greater-than, or +32768 for less-than (always true
for signed I16). That exit is explicitly
classified as **no contact** and cannot authorize CV/I–t. Explicit manual
acceptance uses the same reusable request but authorizes the continuation.
At an endpoint/contact race the host may conservatively classify no contact.

After target waiting acknowledges a requested exit, the secondary threshold is
restored to its configured value (negative contact threshold in magnitude mode,
32768 in legacy signed mode) and its register readback is verified before completion is
exposed. Feedback2 Boolean may stay latched while idle: the comparator loop
stops when all axes finish. The ordinary positioning/baseline waypoint before
each new approach refreshes it without feedback affecting motion. A missing
completion acknowledgement or failed threshold restore faults closed.
Follow-up waypoints wait for line-count agreement, unpaused waiting, verified
threshold restoration and final sample-frame drain.
Neither EndCurrentLine nor External Stop is used in these approach transitions.
Global End waypoint is rejected during an approach; use the experiment's
explicit manual-acceptance button instead.

An operator/external pause is not cleared automatically. The status asks for
Resume; Resume clears only External Pause, leaving the feedback latch for the
contact protocol. Contact evidence is retained across that pause. A pause
without primary evidence is reconciled against complete buffered samples for
at most `hardware_ready_timeout_s`, with motion still held. If unexplained,
the driver latches the fault and reports register values. `StopMoveZ` is an
axis-completion indication, not contact evidence. Neither Resume nor End
waypoint may bypass a fault latch.

These semantics apply to absolute and baseline-relative thresholds; the latter
still becomes an absolute primary threshold after the stationary baseline.
No changes to the compatible FPGA binary are needed. Offline tests exercise
two consecutive 3x3 scans per method and threshold mode in one session, with
the one-shot latch already consumed, transient contacts, partial sample frames,
manual acceptance, no-contact exits, latched idle indicators and missing
completion/threshold-restore acknowledgements. They
do not substitute for physical commissioning.

The compatible WEC-SPM `.lvbitx` contract is checked by datatype, access role, FIFO direction, and compiled target depth. Automated fake-session tests cover long-stream refill, every feedback action code and packed flag, simultaneous compilation, holds, relative Z, acknowledged contact completion, cancellation stream retirement, baseline-to-absolute feedback translation, acknowledged idle/live potential commands, pause-aware watchdog accounting, final acquisition drain, and plotting decimation. The offline checker validates a separately supplied instrument bitfile.

The generic driver limit is 65,535 waypoints in one submitted program. This is not a FIFO capacity limit. Scan plans use a stricter 32,767-tag guard until the target's U64-to-signed-I16 narrowing is confirmed, protecting unambiguous pixel assignment. Physical calibration, polarity, motion direction, and feedback response still require staged commissioning on each instrument PC and NI device.
