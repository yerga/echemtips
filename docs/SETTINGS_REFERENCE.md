# Settings reference

Settings are shared by every experiment. **Save as defaults and apply** first
validates the complete configuration, writes it to the per-user settings file,
disconnects any active backend, and rebuilds the experiment services. Reconnect
after changing settings.

## Persistence and precedence

The normal settings file is:

| Platform | Location |
| --- | --- |
| Windows | `%APPDATA%\eChemTips\settings.json` |
| macOS | `~/Library/Application Support/eChemTips/settings.json` |
| Linux | `$XDG_CONFIG_HOME/eChemTips/settings.json`, or `~/.config/eChemTips/settings.json` |

Set `ECHEMTIPS_SETTINGS_PATH` to use a different file. Set
`ECHEMTIPS_BITFILE` to choose the initial private bitfile path when no saved
value overrides it. A legacy private `.echemtips/settings.json` is read only as
a migration fallback when the normal user file does not yet exist.

The settings file can contain local paths and instrument configuration. It is
excluded from the repository and should not be copied between instruments
without reviewing every calibration value.

## Connection

| Setting | Default | Valid values | Effect |
| --- | --- | --- | --- |
| **Backend** | Simulation | Simulation, NI FPGA | Chooses the deterministic software simulator or real RIO connection. Opening the application alone does not connect. |
| **NI resource** | `RIO0` | Non-empty NI MAX resource in hardware mode | Passed to `nifpga.Session`. Use the exact alias shown by NI MAX. |
| **FPGA hardware** | Auto | USB R Series, PCIe/PXI R Series, Auto | Optionally constrains the bitfile transport family. Auto leaves exact device matching to NI-RIO; it does not bypass register/FIFO checks. |
| **Compiled bitfile** | Empty, or `ECHEMTIPS_BITFILE` | Non-empty path in hardware mode | Defines the FPGA image and its host-visible register/FIFO contract. The image is private and never packaged by eChemTips. |

The application refuses a non-USB image when USB R Series is selected, and
a USB image when PCIe/PXI R Series is selected. No filename or single build
signature is required. Saved paths are never replaced based on their names. Selecting a path does not make it safe; run the hardware checker and
complete physical commissioning.

## Acquisition and execution

| Setting | Default | Valid range | Effect |
| --- | --- | --- | --- |
| **Sample time** | 4 µs | integer 2–1,000,000 µs | FPGA base sampling interval used by the target acquisition loop. |
| **Samples averaged per data point** | 256 | positive power of two | FPGA averaging count. A higher value reduces output data rate but smooths/averages fast behavior. |
| **Effective data interval** | 1.028 ms at defaults | calculated | `sample_time_us × (samples_averaged + 1) / 1,000,000`. The `+1` reflects the deployed FPGA transfer iteration. |
| **FPGA ready timeout** | 5 s | 0.5–60 s | Maximum startup-handshake time before connection is rejected and stop controls are asserted. It is not an experiment duration. |
| **Command watchdog margin** | 30 s | 1–600 s | Extra time added to the host's predicted physical-program duration before a stalled target is treated as a fault. Acknowledged pauses are excluded. |

Full-rate data are sent to the recorder. Plot decimation does not change the
acquisition or saved CSV.

## Piezo ranges

| Setting | Default | Valid range | Effect |
| --- | --- | --- | --- |
| **X/Y/Z maximum** | 100 µm | finite and greater than zero | Converts between FPGA signed-I16 position values and physical µm and bounds every requested position. Enter calibrated travel for the connected controller/stage combination. |
| **X/Y/Z: −10 to +10 V** | off | on/off per axis | Selects bipolar AO conversion. Off means the supported unipolar interpretation; on means the range spans −10 to +10 V. It changes command and readback scaling, not merely the displayed label. |

Do not infer bipolar mode from the desired direction of motion. Determine it
from the controller interface and independent voltage/position measurements.
The UI always distinguishes commanded position from measured input readback.

## Current amplifiers and E1 command

| Setting | Default | Valid range | Effect |
| --- | --- | --- | --- |
| **Current 1 · AI3 sensitivity** | 1 V/nA | finite and greater than zero | Converts AI3 voltage to i1. Enter the voltage produced by the active amplifier/headstage range for one nA. |
| **Current 2 · AI4 sensitivity** | 1 V/nA | finite and greater than zero | Equivalent conversion for i2. It remains configured even if only Current 1 is connected. |
| **Command voltage ratio · AO3** | 1:1 | greater than 0 and at most 100 | eChemTips writes `requested E1 × ratio` to AO3 and limits requested E1 to `±10 V / ratio`. |

Examples for the command ratio:

| Ratio | Requested E1 | AO3 command | Maximum requested E1 |
| --- | ---: | ---: | ---: |
| 1:1 | +1 V | +1 V | ±10 V |
| 2:1 | +1 V | +2 V | ±5 V |
| 5:1 | +1 V | +5 V | ±2 V |

For a VA-10M configuration where its complete ±2 V electrochemical command
span is represented by the NI output's ±10 V span, the intended value is 5:1.
Verify this on the actual amplifier revision with a meter and controlled load;
do not choose 5:1 solely from this example. The ratio currently applies to E1/
AO3. E2/AO4 is not multiplied by it.

The ±10 V checks refer to the NI analog interface. They do not prove that the
external amplifier, cell, headstage, or electrodes tolerate the requested
potential or current.

## Saving

| Setting | Default | Valid values | Effect |
| --- | --- | --- | --- |
| **Data folder** | `data`, resolved when saved | Writable directory | Destination for full-rate CSV/JSON experiment pairs and diagnostic JSON reports. Use a permanent local folder with adequate free space. |
| **Automatically save completed experiments** | on | on/off | On saves normal completions without prompting. Off asks whether to retain each completed experiment. Aborted and error recordings remain for traceability. |

Starting a recording creates its files immediately. This protects partial data
from process interruption but means the destination must remain writable for
the entire experiment.

## Display

| Setting | Default | Valid range | Effect |
| --- | --- | --- | --- |
| **Display buffer** | 12,000 points/plot | integer 500–100,000 | Maximum retained plot-buffer resolution before decimation. It affects UI cost only, not saved data. Rolling time windows can remove older displayed points earlier. |

Use the default unless the display demonstrably lacks required shape detail.
Very large buffers increase rendering work during long experiments.

## Settings deliberately not exposed

The current fixed instrument scope has no lock-in, picomotor, selectable
instrument-profile, or general advanced-feedback UI. AO5–AO7 and AI5–AI7 are
unused. The deployed FPGA still owns deterministic feedback execution and uses
safe internal defaults that Python programs through the supported Current 1 or
Current 2 approach protocol.

## Validation and change control

Settings validation prevents non-finite ranges, unsupported modes, invalid
averaging counts, out-of-range timeouts, and commands that cannot fit the NI
analog conversion. It cannot validate wiring, calibration, chemical safety, or
mechanical clearance. After changing any physical scaling value:

1. save the setting;
2. reconnect with downstream outputs inhibited;
3. repeat the relevant section of [Calibration](CALIBRATION.md);
4. record the change in the instrument commissioning log;
5. run Guided preflight before resuming experiments.
