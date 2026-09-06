# eChemTips

**eChemTips** is an open Python control and analysis interface for scanning electrochemistry instruments. It provides a consistent operator UI for pipette-probe workflows while leaving deterministic, time-critical feedback on the NI FPGA.

The current application includes:

- Watch Current and Watch Position as separate, explicitly started monitors with full-rate recording and stoppable, decimated live views.
- Standalone cyclic voltammetry with separate raw and CV plots.
- Standalone approach with contact detection, pause/stop, and optional retract.
- Approach followed by CV or potential-step current-time acquisition, with optional X/Y prepositioning.
- Scan hopping with CV or current-time acquisition, serpentine or raster paths, contact-height/current maps, contact-relative Z retraction, and live spacing and duration estimates.
- Bounded X/Y/Z piezo movement and controlled potential output.
- Simulation and NI USB-7856R operation with a fixed, explicit channel map.
- A separate data-analysis UI for eChemTips CSV recordings and identified legacy LabVIEW exports.

The simulator requires no laboratory hardware. Real-device support uses the existing compiled FPGA target; eChemTips does not replace FPGA-side feedback or timing logic.

The supported UI on `main` uses PySide6 and PyQtGraph. The final pre-migration Tkinter implementation is preserved in the `tk-legacy` branch and the immutable `tk-v0.1` tag.

## Run the simulator

Python 3.10 or newer is required. Installing eChemTips installs its PySide6 and PyQtGraph UI dependencies.

```bash
python run_echemtips.py
```

Or install the project in editable mode:

```bash
python -m pip install -e .
echemtips
```

Select **Simulation**, connect, and choose an experiment. The simulated surface is around 68% of the configured Z range, allowing approach-based methods and hopping scans to complete without hardware.

Use **Settings → Save as defaults and apply** to persist the selected backend, piezo ranges, bitfile, data folder, Current 1/2 sensitivities, command-voltage ratio, and acquisition options. Defaults are stored in the operating system's per-user application-settings folder and load regardless of the directory used to start eChemTips.

Recordings are streamed to uniquely named CSV and JSON metadata files in `data/`, or in the directory selected in Settings. The complete data rate is written to disk while plots retain a bounded, decimated display buffer. Standard CSV rows contain measured time, X/Y/Z, E1/E2, i1/i2, and the FPGA line number. Scan recordings additionally contain `scan_pixel`; their ordered pixel-to-row/column/XY mapping is stored once in the JSON sidecar. Per-sample feedback type, redundant row/column values, and non-time-aligned commanded-position snapshots are intentionally omitted to keep long recordings smaller.

Approach thresholds are entered in pA in the operator UI. Every approach-based page includes a dedicated **Accept current Z as contact and continue** action for intentional manual acceptance; the global **End waypoint** control only advances the active FPGA waypoint and is not contact confirmation. Each approach page shows both the latest approach curve and a rolling 60-second current-versus-Z history. Long scan traces use the same 60-second display window while the recorder continues to preserve the complete acquisition.

For hopping scans, **Initial approach Z** is an absolute position used only before the first hop. **Retract distance from contact** is a positive distance: after each confirmed contact at Z, the next retract target is calculated away from the surface by that distance. Raster scans add **Raster flyback extra retract** at the end of each line before the longer X flyback.

## Analyze data

```bash
python run_analysis.py
```

The analysis UI displays raw time-domain channels and experiment metadata. CV recordings are also separated into individual cycles and plotted as potential versus Current 1. Completed cycles can be inspected, overlaid, and exported independently.

Optional TDMS import is available for files with identified and scaled channels:

```bash
python -m pip install -e ".[analysis]"
```

## Connect to an NI FPGA

The compiled `.lvbitx` target is deliberately **not included in this public repository**. On the instrument computer, obtain the target image through the instrument owner and select it in Settings or pass it to the hardware checker.

Install the NI software supported by the device and the Python FPGA dependency:

```bash
python -m pip install -e ".[fpga]"
python run_hardware_check.py --bitfile "/path/to/target.lvbitx"
python run_hardware_check.py --bitfile "/path/to/target.lvbitx" --connect --resource RIO0
```

The commissioned profile is an NI USB-7856R with:

- AO0, AO1, AO2: X, Y, Z piezo commands
- AO3, AO4: Voltage 1 and Voltage 2
- AO5, AO6, AO7: reserved and unused (no picomotor control)
- AI0, AI1, AI2: measured X, Y, Z piezo positions
- AI3, AI4: Current 1 and Current 2 amplifier outputs
- AI5, AI6, AI7: unused
- FPGA-side approach feedback and waypoint execution

Before enabling actuators, follow [the real-hardware commissioning guide](docs/REAL_HARDWARE_SETUP.md). Verify the bitfile target, register/FIFO contract, wiring, polarity, scaling, travel, current-amplifier sensitivity, and physical interlocks on the specific instrument.

The host supports reusable execution ownership, state reporting, safe cancellation, FIFO streaming/refilling beyond the target's initial FIFO capacity, pause/resume, complete-frame acquisition, display-only decimation, waypoint compilation, and Current 1/2 contact feedback. See the [hardware integration](docs/HARDWARE_INTEGRATION.md) and [shared host services](docs/SHARED_HOST_SERVICES.md) documentation.

An advanced custom host adapter can be selected with `ECHEMTIPS_DRIVER_MODULE`. For backward compatibility, `WECSPM_DRIVER_MODULE` is also recognized when the new variable is unset.

## Safety

This is research control software, not a certified safety system.

- Opening the UI does not send an output.
- Real sessions configure the target while externally paused.
- Position, potential, calibration, feedback, and timing settings are validated before submission.
- Contact-gated methods do not begin surface electrochemistry after an end-of-travel event.
- Normal stop waits for target acknowledgement and drains remaining data.
- Emergency stop latches the active driver; reconnection/reinitialization is required.
- Software checks do not replace a physical emergency stop, safe amplifier state, travel limits, or instrument commissioning.

## Tests

```bash
python -m unittest discover -v
python run_echemtips.py --smoke-test
python run_analysis.py --smoke-test
```

The suite covers settings, simulation dynamics, experiment state machines, recording, acquisition backlog/drain behavior, waypoint scaling and streaming, FPGA protocol validation, hardware fault paths, data analysis, and UI workflows.

## Architecture

- `echemtips/ui.py`, `qt_common.py`: PySide6 operator interface and PyQtGraph rendering
- `echemtips/experiments.py`: device-independent experiment state machines
- `echemtips/backends.py`: simulator and NI-FPGA backend boundary
- `echemtips/host.py`, `waypoints.py`: shared host services and waypoint compiler
- `echemtips/ni_driver.py`, `ni_protocol.py`: deployed FPGA host protocol
- `echemtips/acquisition.py`, `data.py`: full-rate acquisition and persistence
- `echemtips/analysis.py`, `analysis_window.py`, `legacy_data.py`: Qt analysis UI and validated legacy import

The historical LabVIEW project, compiled FPGA binaries, local settings, and experimental data are intentionally excluded from this repository.

## License

[MIT](LICENSE)
