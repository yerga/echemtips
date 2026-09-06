# Three priority hardware-operation gaps

This review focused on failure modes that can affect the USB-7856R, piezo positioner, or electrode output rather than on adding more experiment types.

## 1. Emergency stop was not latched across host commands

Previously, the NI backend wrote `External Stop = True`, but the native driver did not enter a stopped state. A later sequence-start path could therefore clear `External Stop` and submit another command in the same session.

The emergency-stop path now:

- latches the native driver before performing I/O;
- asserts both `External Pause` and `External Stop` independently;
- reads both controls back and reports an uncertain stop if either cannot be verified; and
- rejects every later motion or voltage command until the target is reinitialized.

The stop does not claim to remove analog power or replace physical interlocks. FPGA outputs can retain their last value depending on the target design.

## 2. No target readiness, fault, or execution-time supervision

The connection previously returned immediately after `Session.run()`, and an approach could wait indefinitely if the FPGA stopped or failed to advance.

The native driver now waits for `WaitingForWayPoints` after starting the FPGA. During acquisition it monitors the NI FPGA VI state, `Internal Stop`, `External Stop`, and impossible line-counter advancement. Every generated movement program also has a deadline based on its maximum physical travel and CV sweep time plus a configurable margin. A readiness failure, target fault, protocol mismatch, malformed FIFO read, or expired watchdog pauses/latches the driver and causes the UI to save the partial recording as an error.

The Settings page exposes the startup timeout and command-watchdog margin. A timeout requires target reinitialization; it does not automatically retry movement.

## 3. Hardware values could be silently coerced

The NI API and the target use bounded integer registers. The earlier validation did not reject every non-finite calibration and allowed a feedback threshold whose amplifier output was outside the AI channel's ±10 V range. Converting such a threshold to I16 could saturate it, changing the requested contact condition.

Settings and experiment validation now reject non-finite ranges, Current 1/2 sensitivities, potentials, speeds, positions, timeouts, and thresholds. On the real-device backend, `feedback threshold × V/nA sensitivity` must fit the ±10 V ADC range and command potentials must fit AO3 after applying its configured ratio. These checks run before opening the NI session and again before building a hardware program.

## Verification boundary

Automated tests exercise these paths with a fake NI session, and a separately supplied USB-7856R bitfile can be checked offline for the required registers and FIFO contract. Physical stop timing, polarity, calibration, and motion still require commissioning on the instrument PC with the actuators initially disabled.
