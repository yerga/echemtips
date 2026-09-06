# eChemTips

**eChemTips** is an open Python control and analysis interface for scanning electrochemistry instruments. It provides a consistent operator UI for pipette-probe workflows while leaving deterministic, time-critical feedback on the NI FPGA.

The current application includes:

- Watch Current with full-rate recording and a stoppable, decimated live view.
- Standalone cyclic voltammetry with separate raw and CV plots.
- Standalone approach with contact detection, pause/stop, and optional retract.
- Approach followed by CV or potential-step current-time acquisition.
- Scan hopping with CV or current-time acquisition, full-experiment Z/current histories, contact-height maps, and current maps.
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

Recordings are streamed to uniquely named CSV and JSON metadata files in `data/`, or in the directory selected in Settings. The complete data rate is written to disk while plots retain a bounded, decimated display buffer.

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
- AO3: Voltage 1
- AI3: Current 1 amplifier output
- FPGA-side approach feedback and waypoint execution

Before enabling actuators, follow [the real-hardware commissioning guide](docs/REAL_HARDWARE_SETUP.md). Verify the bitfile target, register/FIFO contract, wiring, polarity, scaling, travel, current-amplifier sensitivity, and physical interlocks on the specific instrument.

The host supports reusable execution ownership, state reporting, safe cancellation, FIFO streaming/refilling beyond the target's initial FIFO capacity, pause/resume, complete-frame acquisition, display-only decimation, waypoint compilation, and advanced FPGA feedback configuration. See the [hardware integration](docs/HARDWARE_INTEGRATION.md) and [shared host services](docs/SHARED_HOST_SERVICES.md) documentation.

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
