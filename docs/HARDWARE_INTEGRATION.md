# NI-FPGA integration notes

> The native host implementation is now in `echemtips/ni_driver.py`. See
> `REAL_HARDWARE_SETUP.md` for the instrument-PC procedure. The custom-driver
> material below is retained for laboratories that change the FPGA interface.

This document records the boundary between the Python host and the existing WEC-SPM FPGA target. It is intended for the instrument owner who validates a specific workstation; it is not a substitute for a dry-run and oscilloscope check.

## Supplied target

The commissioned target filename is shown below for identification. The binary
is private instrument firmware and is not included in this repository:

`wecspm_FPGATarget2_FPGATarget_MAn-McsWIiw.lvbitx`

Its metadata identifies a USB-7856R, signature `8229BC0D5A4935D854D1286878CEE54A`, a `Host_To_FPGA_Positions` I16 host-to-target FIFO with 8197 target elements, and an `FPGA_To_Host_FIFO` I16 target-to-host FIFO with 32767 target elements. Its 114-register interface, register datatypes/access roles, and FIFO contract exactly match the legacy PCIe-7852R WEC-SPM image. The Python adapter addresses registers by the names embedded in this file rather than hard-coded offsets.

The offline preflight validates the required register datatypes and whether each is a control or indicator. It also validates each FIFO's signed-I16 datatype, direction, and configured target depth; name-only compatibility is not accepted.

The active connection additionally requires `WaitingForWayPoints` after `Session.run()`. Runtime health checks include the NI FPGA VI state, the target's `Internal Stop` indicator, the `External Stop` control, line-counter consistency, exact FIFO read length, and a physical-duration watchdog for every submitted program.

Important host controls include:

- `External Stop` and `External Pause`
- `V on Fly` / `Change V on Fly`
- `V2 on Fly` / `Change V on Fly 2`
- `Feedback_Threshold`, `FeedBackType`, `GreaterThan`, and the corresponding second-feedback controls

Important readbacks include:

- `Applied X`, `Applied Y`, `Applied Z`
- `Applied Voltage`, `Applied Voltage 2`
- `MeasuredCurrent`, `MeasuredCurrent 2`, `MeasuredCurrent 3`, `MeasuredCurrent 4`
- `Ext amp`, `Ext Phase`, `LineNumber`, and `WaitingForWayPoints`

## Physical channels

The original user guide maps the breakout box as follows:

| Channel | Function |
|---|---|
| AO0 / AO1 / AO2 | X / Y / Z piezo command |
| AO3 / AO4 | Voltage 1 / Voltage 2 |
| AI0 / AI1 / AI2 | X / Y / Z readback |
| AI3 / AI4 | Current 1 / Current 2 |
| AI5 / AI6 / AI7 | Unused by eChemTips |

The developer guide is internally inconsistent about the electrode outputs: its breakout-box table and ChangeOnFly section use AO3/AO4, while the waypoint table says AO4/AO5. The application keeps AO3/AO4, which also agrees with the existing UI/project mapping. Confirm the physical breakout box during commissioning rather than relying on the stray waypoint-table entries.

## Native waypoint protocol

Read-only extraction of `WayPointCluster.ctl` found 24 logical arrays:

1. X, Y, Z, V positions
2. X, Y, Z, V velocities
3. Feedback type
4. Move X/Y/Z/V, Jump V, Hold, and Hold Time
5. V2 position, Move V2, V2 velocity, and Jump V2
6. Z-feedback update interval and Hold Feedback 1
7. Z Picomotor move and direction

Read-only rendering of `ScaleWayPoints.vi` shows that each waypoint is converted to I16 values and a packed boolean word before it is written to `Host_To_FPGA_Positions`. Position helpers apply range/polarity conversion, velocity helpers convert physical units per second to FPGA increments per tick, and a per-axis power-of-two velocity scaler is written to separate target registers.

The final frame was traced through the `Build Array` node. Each waypoint is 14 I16 words in this order:

1. line/feedback action type
2. X, Y, Z, Voltage 1, and Voltage 2 velocities (five words)
3. X, Y, Z, Voltage 1, and Voltage 2 target positions (five words)
4. position-loop wait, hold timer, and packed flags (three words)

Packed flag bits 0–8 are used for Move X, Move Y, Move Z, Move V, Jump V, Hold, Move V2, Jump V2, and Hold Feedback 1. The deployed frame also reserves bits 9–10 for legacy picomotor commands, but eChemTips never sets them. Position and velocity helpers use `2^15`, a 40 MHz clock, and the corresponding `ExpandVelScaller` register. `ni_protocol.py` implements these conversions and `test_ni_protocol.py` verifies the frame and scaling independently of NI-RIO.

The acquisition FIFO is also 14 I16 words per sample. eChemTips decodes X/Y/Z, V1/V2, Current 1/2, feedback type, line number, and the timestamp. The deployed FPGA still supplies legacy values in the remaining words, but the Python application intentionally ignores them. The last two biased I16 words are joined high-word first into the 40 MHz U32 timestamp. The native decoder unwraps timestamp rollover and exposes the result as `Sample.elapsed_s`.

## Site driver contract

Set `ECHEMTIPS_DRIVER_MODULE` to the import name of a validated local module. The legacy
`WECSPM_DRIVER_MODULE` name remains a fallback for existing installations. The application calls:

```python
def create_driver(session, settings):
    return Driver(session, settings)


class Driver:
    def move(self, axis: str, target: float, speed: float) -> None:
        # Convert a single physical move to the workstation's confirmed frame.
        # Write it through session.fifos["Host_To_FPGA_Positions"].
        ...

    def start_approach_cv(self, parameters) -> None:
        # Submit positioning and a line-type-1 pause-on-contact approach.
        # Submit CV/retract separately only after the FPGA pause and feedback
        # event are confirmed. Current thresholds arrive in nA and must be
        # converted to the target's signed-I16 ADC scale.
        ...

    def approach_cv_status(self) -> dict:
        # Valid stages: preposition, approaching, contact, cv, retracting,
        # complete, aborted. Progress is a float from 0 to 1.
        return {"stage": "approaching", "detail": "...", "progress": 0.2}

    def stop_motion(self) -> None:
        ...

    def read_samples(self):
        # Optional: drain FPGA_To_Host_FIFO and return model.Sample objects.
        # The recorder persists every object; the UI renders only the newest.
        ...
```

The driver receives the already-open `nifpga.Session`; it must not download another bitfile or create a second session. The application validates axis, target, speed, voltage bounds, and global calibration before calling it. A driver with only `move()` and `stop_motion()` enables Move Piezo but not Approach + CV.

The bundled driver drains `FPGA_To_Host_FIFO` in batches and records every complete sample frame. A custom driver should do the same through `read_samples()`.

## Shared host services

The native implementation deliberately separates method logic from common host behavior:

- `host.py` owns execution state snapshots, complete-frame FIFO streaming/refilling, and the bounded display buffer.
- `waypoints.py` compiles physical-unit X/Y/Z/V1/V2 plans, concurrent axis movement, ramps, jumps, holds, relative Z moves, feedback actions, and all deployed flag bits.
- `ni_driver.py` owns exactly one active program, applies the compiled scalers, services refills, verifies line-count/waiting-state completion, drains final acquisition data, and exposes pause, resume, end-current-line, ChangeOnFly potential, and feedback configuration operations.
- `acquisition.py` continuously drains the hardware outside Qt rendering. The recorder receives every sample; PyQtGraph plots receive separately decimated buffers that preserve the experiment's first and last points.

Each approach method selects Current 1 or Current 2, a contact threshold, and comparison polarity. The driver writes fixed neutral values to the deployed target's unused secondary/proportional/running-average/bulk registers at initialization and before a method, preventing stale FPGA state from enabling unsupported feedback modes. Contact decisions and Z stopping remain FPGA-resident.

The current application always treats AI1 as Y-position readback. It does not expose the original Current 4 multiplexing mode.

## Commissioning checklist

1. Disconnect the piezo amplifiers and electrode output before the first software test.
2. Run `run_hardware_check.py` to confirm the target class, bitfile signature, and named interface.
3. Verify raw-to-voltage polarity and the command-voltage divider using a meter or oscilloscope.
4. Verify each configured piezo maximum and bipolar/unipolar setting.
5. Validate one low-speed, short-distance axis move at a time.
6. Confirm both UI stop and FPGA `External Stop` under motion.
7. Compare FIFO-decoded data against a short original LabVIEW acquisition before relying on Python files.
8. Only then enable Approach + CV on a sacrificial or well-separated setup.
