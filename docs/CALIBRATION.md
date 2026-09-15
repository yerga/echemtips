# Calibration and sign conventions

eChemTips converts raw FPGA values into µm, V, and nA using Settings. Those
conversions are only as accurate as the values entered by the operator. The
application does not contain a hidden factory calibration and does not infer
wiring polarity.

Perform calibration with the probe and electrochemical cell removed from risk,
using independently calibrated measurement equipment. Record instrument IDs,
software/bitfile versions, environmental conditions, raw observations,
calculations, acceptance tolerances, operator, and date in the laboratory's
instrument log. The UI intentionally does not maintain a separate calibration-
provenance database.

## Coordinate and electrical conventions

- X/Y/Z are physical positions in µm after the configured range and bipolar
  conversion. Coordinate sign belongs to the installed controller/stage.
- Start Z and approach-limit Z define the approach direction. A hopping retract
  is a positive distance applied in the opposite direction from that approach.
- E1 is the requested electrochemical potential represented through AO3 and the
  command-voltage ratio. E2 is represented through AO4 without that ratio.
- i1 and i2 are calculated from AI3 and AI4 using positive V/nA sensitivities.
  Current sign is the sign produced by the connected amplifier; eChemTips does
  not add a user-selectable sign inversion.
- Commanded position is the requested AO trajectory. Measured position is the
  AI0/AI1/AI2 feedback stream. They must not be treated as identical.

## Required equipment

- calibrated high-impedance DMM and, for transients, oscilloscope;
- current or voltage reference, or precision resistors appropriate to the
  intended potential/current range;
- controller-compatible dummy loads and physical stage observation;
- physical motion reference if quantitative stage travel is being calibrated;
- documented grounding/reference arrangement;
- physical positioner and amplifier inhibits.

Choose site-specific acceptance tolerances before collecting results. Do not
change tolerances after seeing a discrepancy merely to make the test pass.

## E1 command-voltage ratio

The configured ratio `R` means:

```text
AO3 voltage = requested E1 × R
maximum requested |E1| = 10 V / R
```

To determine and verify it:

1. Disconnect the electrochemical cell and connect AO3 through the intended
   amplifier/controller input path to a suitable meter or dummy load.
2. Confirm zero command and record any offset.
3. Apply at least two small positive and two small negative requested E1 values
   with Watch Current. Stay well inside both NI and external-device limits.
4. Record AO3 and the external controller's corresponding E1 representation.
5. Fit/check slope, sign, offset, repeatability, and saturation. The appropriate
   ratio is based on the documented external command span, confirmed by these
   measurements.
6. Enter the ratio, save defaults, reconnect, and repeat the check. Confirm the
   UI's calculated requested-E1 limit.

Example: when an external ±2 V E1 span is intentionally mapped to the NI
±10 V command span, `R = 5`. A requested +1 V E1 produces +5 V on AO3. This is
an example, not evidence that every VA-10M revision/configuration uses that
mapping.

## Current amplifier sensitivity

Enter sensitivity as amplifier output volts per nanoampere:

```text
current (nA) = AI voltage (V) / sensitivity (V/nA)
```

For each connected current channel:

1. Record amplifier model/revision, headstage, gain/range, bandwidth/filter,
   command mode, and output connector.
2. With zero input, measure output offset and noise independently and compare
   with the eChemTips readback.
3. Apply multiple known positive and negative currents, or create them with a
   verified voltage and precision resistance.
4. Compare the DMM/scope output voltage, expected current, and eChemTips i1/i2.
   Check slope, sign, offset, linearity, and clipping.
5. Enter the positive V/nA magnitude. If sign is wrong, correct wiring or the
   documented amplifier configuration rather than entering a negative
   sensitivity, which validation rejects.
6. Repeat Guided preflight at the exact gain/bandwidth used for experiments.

Changing amplifier gain or headstage invalidates the saved sensitivity until
the relevant check is repeated. Never define a feedback threshold from data
that are clipped at the amplifier or NI input limit.

## Piezo command range and bipolar mode

The configured maximum is the physical travel represented by the full supported
raw range. Bipolar mode changes the raw/voltage interpretation; it does not
reverse motion by itself.

For X, then Y, then Z:

1. Put the stage and probe in a mechanically safe state. Inhibit the other axes.
2. Verify the controller command-input range and monitor-output range from the
   manual matching the installed hardware.
3. Select the proposed maximum and bipolar mode. Connect with downstream motion
   inhibited and measure the target's startup AO level.
4. Enable only the tested axis. Move to several conservative positions across
   the usable range at low speed.
5. At every point record commanded µm, AO voltage, controller monitor voltage,
   eChemTips measured µm, and independent physical displacement.
6. Verify monotonicity, direction, scale, offset, repeatability, hysteresis, and
   that no command can exceed the safe mechanical range.
7. For Z, explicitly demonstrate which numeric direction approaches the sample
   and that the calculated contact-relative retract moves away from it.

If the controller mapping is materially nonlinear, a single full-scale range
is not a sufficient calibration model. Do not conceal that limitation by
tuning the maximum at one point; open an issue to add a reviewed calibration
curve before quantitative mapping.

## Acquisition timing and averaging

The displayed effective interval is:

```text
sample_time_us × (samples_averaged_per_data_point + 1)
```

in microseconds. Verify acquisition timing with a known periodic electrical
signal if timing matters to kinetics or scan-rate interpretation. Compare edge
spacing over many samples, not a single interval. Increasing averaging reduces
the exported sample rate and can suppress fast transients; it is not merely a
plot-smoothing option.

CV potential ramps and I–t holds are executed by FPGA waypoints. Validate one
small waveform with an oscilloscope and compare commanded duration, applied
potential, CSV time, and final command acknowledgement before relying on scan
rate or hold duration scientifically.

## Contact threshold qualification

Thresholds are entered in pA in the UI and converted to the nA protocol unit.
For the selected channel and amplifier configuration:

1. Measure stationary baseline, RMS/peak-to-peak noise, and drift with Guided
   preflight.
2. Verify whether physical contact moves current upward or downward.
3. Select an absolute threshold and comparison direction that cannot already be
   satisfied by normal baseline variation.
4. Test with a dummy or sacrificial configuration over a short, conservative Z
   interval.
5. Verify automatic crossing, no-contact end-of-travel, manual acceptance,
   settling, and retract independently.

The suggested Δi value in the preflight report is diagnostic guidance. The
current operator pages use a simple absolute Current 1/2 threshold. A future
change to expose baseline-relative contact must be separately documented and
commissioned; do not assume the suggested Δi is automatically applied.

## Calibration record template

| Check | Configuration | Raw observations | Fitted scale/offset/sign | Acceptance criterion | Result |
| --- | --- | --- | --- | --- | --- |
| AO3 to E1 | ___ | ___ | ___ | ___ | Pass / Fail |
| AI3 to i1 | ___ | ___ | ___ | ___ | Pass / Fail |
| AI4 to i2 | ___ | ___ | ___ | ___ | Pass / Fail / Not installed |
| AO0/AI0 to X | ___ | ___ | ___ | ___ | Pass / Fail |
| AO1/AI1 to Y | ___ | ___ | ___ | ___ | Pass / Fail |
| AO2/AI2 to Z | ___ | ___ | ___ | ___ | Pass / Fail |
| Acquisition interval | ___ | ___ | ___ | ___ | Pass / Fail |
| CV ramp/hold timing | ___ | ___ | ___ | ___ | Pass / Fail |
| Contact/retract direction | ___ | ___ | ___ | ___ | Pass / Fail |

Recommission the affected section after cable changes, controller replacement,
amplifier/headstage or gain changes, bitfile changes, driver upgrades, or any
software change to protocol conversion code.
