# Hardware troubleshooting and recovery

Use this guide only after the wiring and software baseline in
[Real hardware setup](REAL_HARDWARE_SETUP.md) has been recorded. Preserve the
CSV/JSON pair, exact error text, hardware-check output, eChemTips commit, and NI
software versions before changing the setup.

When motion, potential, or current is unexpectedly unsafe, stop diagnosis and
use the laboratory's physical inhibit or emergency-stop procedure. Software
cannot prove that a downstream high-voltage amplifier is de-energized.

## Connection and target

| Symptom | Likely causes | Safe checks and recovery |
| --- | --- | --- |
| `nifpga` cannot be imported | The Python FPGA extra is absent or a different environment is active. | Run `.venv\Scripts\python.exe -m pip show nifpga` and install the project with `-e ".[fpga]"`. Do not install packages into an unidentified system Python. |
| Native FPGA library/DLL cannot load | NI-RIO/FPGA Interface runtime is absent, mismatched, or its installation requires reboot. | Confirm NI MAX works, record NI software versions, reboot, and rerun the offline checker. Reinstall only from an NI release compatible with the device and Windows version. |
| `RIO0` not found | USB/power/cable problem, different alias, NI driver problem, or another process owns the target. | Disable actuators, inspect the device in NI MAX, use its exact resource, close LabVIEW/other hosts, reconnect USB, and rerun `run_hardware_check.py --connect`. |
| Target was left running by a previous session | Closing a session does not necessarily stop the FPGA. | Close other NI/LabVIEW controllers, secure the probe, and accept the startup confirmation. eChemTips resets the target before configuration and startup. |
| FPGA reset fails or target remains running | NI reset failed or the target did not reach `NotRunning`. | Startup is blocked and the session is closed. Close other controllers and check the device in NI MAX before reconnecting. Preserve the reported state/error if this repeats. |
| Bitfile target/contract rejected | Wrong target family or incompatible registers/FIFOs. | Run the offline checker. Select the device-specific build from the laboratory configuration record. A filename change cannot fix a protocol or device mismatch; NI-RIO must also accept the image for the actual device. |
| Register/FIFO contract rejected | Bitfile does not expose the expected names, types, access, directions, or frame-compatible depth. | Preserve checker output and compare with `HARDWARE_INTEGRATION.md`. Use the verified target; do not guess aliases or cast FIFO types. |
| Startup verification fails | Applied AO values, line state, pause/stop state, or waiting state does not match the deployed target behavior. | Keep downstream devices inhibited. Measure outputs, reset the device, ensure no other host is running, and retry once. Treat repetition as a bitfile/driver/configuration fault. |

## Signals and calibration

| Symptom | Likely causes | Safe checks and recovery |
| --- | --- | --- |
| E1 has wrong magnitude | Incorrect command-voltage ratio or controller input-span assumption. | Disconnect the cell, command small ± values into a meter/load, and verify `AO3 = requested E1 × ratio`. Correct Settings and save defaults. |
| E1 has wrong sign or appears offset | Reversed connection, grounding/reference error, controller mode, or wrong channel. | Return to zero, disconnect the cell, inspect AO3 and amplifier command separately, and confirm reference terminals from the manuals. |
| i1/i2 scale is wrong | Sensitivity entered in the wrong unit/range, amplifier gain changed, wrong headstage, or AI channel swapped. | Use a known resistor/test current and compare AI voltage with current. Enter V/nA for the active amplifier configuration. Repeat after every gain change. |
| Current clips near a fixed value | VA-10M/headstage or ±10 V FPGA input is saturated. | Remove the electrochemical stimulus, inspect raw amplifier output with a meter/scope, reduce gain or potential safely, and rerun preflight. Do not use clipped values as contact thresholds. |
| Excessive noise/drift | Open input, shielding/ground loop, vibration, contaminated pipette, bandwidth/gain setting, or unstable cell. | Stop approach work, run zero/open preflight in stages, inspect grounds and shielding, then characterize the pipette. Save results before and after changes. |
| Position readback differs from command | This can be real tracking error; range, bipolar mode, monitor scaling, or channel mapping may also be wrong. | Keep the probe clear, test one axis at small increments, measure AO and monitor voltage independently, and correct calibration only from documented controller behavior. |
| Axis moves opposite to expected surface direction | Wiring/controller sign or coordinate assumption differs. | Stop, retract using the physical controller if necessary, relabel the verified direction, and do not approach until Start Z, limit Z, and retract direction are all demonstrated. |

## Execution, contact, and recording

| Symptom | Likely causes | Safe checks and recovery |
| --- | --- | --- |
| Approach reaches its limit without contact | Threshold magnitude/sign is wrong, selected current channel is inactive, no meniscus/contact signal exists, or travel direction is wrong. | The method must not start CV/I–t. Verify the aborted JSON status, inspect current versus Z, retract physically, rerun preflight, and reduce the interval/speed before retrying. |
| Contact triggers immediately | Threshold is already crossed at baseline, current is saturated, transient settling is insufficient, or polarity is wrong. | Stop and reconnect, inspect stationary current/noise, choose a threshold beyond measured noise with justified direction, and test using a dummy signal. |
| Manual contact acceptance is unavailable | The method is not in its approach stage or no current sample exists. | Wait for the page to report Approaching. Do not use global End waypoint as a substitute. |
| Program appears paused | Operator pause or FPGA feedback pause is active. | Read the execution/status labels. Resume only an operator pause; do not repeatedly force Resume against FPGA feedback ownership. Verify current/contact state first. |
| Program/watchdog/progress fault | Target stopped, line count stalled/overshot, acknowledgement failed, or FIFO state became uncertain. | eChemTips pauses/stops and latches the driver. Save error evidence, physically secure the instrument, disconnect/reinitialize, and reduce to the smallest reproducing program. |
| Stop succeeds but another command is rejected | Cancellation intentionally retires a possibly misaligned acquisition stream. | This is expected on hardware. Disconnect, reinitialize the FPGA, and reconnect before another command. |
| Emergency Stop does not zero analog outputs | The deployed stop controls execution but do not promise zero AO levels or disable external amplifiers. | Use the physical controller/amplifier inhibit or power isolation. Treat the GUI stop as one layer, not the interlock. |
| UI plots slow during a long scan | Display-buffer setting is unusually high or another live view was explicitly started. | Stop unneeded live views and restore a bounded display buffer. Scan traces show rolling 60 s while disk recording remains full-rate. |
| JSON says `running` after a crash | The final metadata update was never reached. | Treat the recording as partial. Count readable CSV rows, preserve both files, and do not edit status to `complete`. |
| CSV exists but experiment-aware analysis is incomplete | JSON sidecar is missing/damaged or parameters do not describe a complete CV. | Preserve the raw CSV, restore sidecar only from backup, and analyze as raw data. The analysis tool intentionally rejects incomplete cycles. |
| Disk-write or acquisition-backlog error | Destination unavailable/full/slow, permissions changed, or rendering/processing could not keep up. | Secure hardware first. Preserve the error recording, check free space and destination permissions, use a local permanent data folder, and repeat in Simulation before hardware. |

## Minimal fault report

Include the following when opening an issue, but remove private paths, sample
identifiers, and experimental data that cannot be shared:

```text
eChemTips commit:
Windows / Python architecture:
NI-RIO / NI MAX / nifpga versions:
NI device model, resource and bitfile signature:
Backend and experiment:
Exact error text:
Last safe physical state:
Steps to reproduce with Simulation, if possible:
Recording status and sample count:
Hardware-check output:
```
