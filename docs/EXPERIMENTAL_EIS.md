# Experimental Approach + EIS

This feature exists on **`experimental/approach-eis`**, not `main`. It is a
low-frequency feasibility prototype, **not a validated impedance analyzer**.
No FPGA source or bitfile changes are introduced: it reuses the native driver's
confirmed-contact sequence and timed potential-hold waypoints.

## Install separately on the test computer

Keep your normal checkout unchanged. From its directory:

```powershell
git fetch origin
git worktree add ../echemtips-eis-test origin/experimental/approach-eis
cd ../echemtips-eis-test
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e .
$env:ECHEMTIPS_SETTINGS_PATH = "$PWD\eis-test-settings.json"
.venv\Scripts\python -m echemtips
```

The separate settings path prevents test defaults from changing your normal
installation. Select the same compatible bitfile and NI resource when eventually
testing hardware. The existing NI driver installation is still needed.
On macOS/Linux use `python3 -m venv .venv`, `.venv/bin/python`, and
`export ECHEMTIPS_SETTINGS_PATH="$PWD/eis-test-settings.json"` instead.

## Simulation walkthrough

1. Choose **Simulation** in Settings and an empty test recording directory.
2. Connect. Open **All experiments → Approach + EIS**. You can pin it to the sidebar.
3. Leave the default 10 mV **peak** amplitude, 0.1 V DC bias, frequency list,
   2 settling cycles and 4 measured cycles. The current selector in the sine
   section chooses the impedance channel; the one in Approach chooses contact feedback.
4. Start. Simulated contact occurs midway between initial Z and approach limit,
   with a 30 pA signal above a small noise baseline. No manual noise injection is needed.
5. Check **Experiment traces** for potential, current and Z; **Approach** shows i–Z.
   Spectrum points appear when each frequency finishes. **Nyquist** plots Z′ vs
   −Z″; **Bode** shows magnitude and phase against logarithmic frequency.
   **Results** shows numerical values and fit warnings.
6. Normal completion returns Z to initial Z at the selected return speed and
   returns E1 to the DC bias. X/Y are not repositioned by this experiment.
7. Check the CSV and JSON. Repeat with a different frequency list/amplitude or
   Current 2. Stop midway and verify that the recording is marked aborted.

Simulation synthesizes the fundamental response of
`100 MΩ + (500 MΩ || 100 pF)` with small independent current noise. It uses
sample-clock waveform generation rather than GUI-timer potential updates.
It is an idealized demonstration: it does not model electrode nonlinearities,
amplifier filters, FPGA overhead, hardware noise, or RC startup transients.

## What is measured and saved

The waveform comprises **64 equal-duration potential holds per cycle**. Hold
durations are rounded to integer microseconds and split into compatible FPGA
timer frames where necessary. No host-timed sine is sent to real hardware.
After contact settling there is a separate DC hold, then each frequency has
discarded settling cycles followed by measured cycles. All samples, including
settling, remain in the raw recording.

The ordinary full-rate CSV gains two columns **only for EIS**:

- `eis_frequency_index`: zero-based index into the requested frequency list;
  −1 outside the sine sequence.
- `eis_phase`: `settle` or `measure`, empty otherwise.

The JSON `parameters` includes the frequency list, DC/AC values, cycle counts,
reference limitation and `eis_results`. Each result contains frequency, real and
imaginary impedance in Ω, magnitude, phase in degrees, sample count, fitted
potential/current amplitudes and relative fit residuals. Failed fits retain an
error rather than silently inventing a spectrum point. The CSV retains actual
sample timestamps and both current channels for independent reprocessing.
The dedicated spectrum viewer is currently in the control page; saved spectra
are available in JSON, not a new EIS workspace in the analysis application.

The fit uses sine/cosine coefficients plus offset and linear drift, then
`Z = E_fundamental / i_fundamental`. It does **not** use DC E/i. Simultaneous
potential/current sign reversal leaves this ratio unchanged. Large residuals or
sampling gaps produce warnings, not automatic rejection of the experiment.

## Limits and important qualifications

- Initial software range: **0.5–20 Hz**, up to 30 frequencies, 1–50 mV peak.
  Hardware amplitude must also resolve at least 10 DAC counts.
- At least **32 recorded samples/cycle** are required. With 10 µs and averaging
  256, the recorded period is 2.57 ms and the maximum allowed frequency is about
  **12.2 Hz**, not 20 Hz. Lower frequencies do not require changing acquisition.
- A 24,000-frame budget and 200,000 measured-samples/frequency limit bound memory
  and tags. The native driver additionally checks remaining session line tags;
  a fresh connection may be needed between long runs.
- This is **command-referenced apparent impedance**. Recorded E1 is the applied
  FPGA command, not an independent voltage measurement across the electrochemical
  cell. Command scaling, current-amplifier filtering, averaging and latency can
  bias gain and phase. No filter/phase correction is supplied.
- Reported frequency includes hold quantization, but not unmeasured execution
  overhead between waypoints. Real frequency/phase must be checked electrically.
- Two-electrode SECCM includes the pipette/electrolyte and other electrode
  interface, not just the sample interface. Contact area and drift also matter.
- Pause and low-level End waypoint are disabled while EIS is active because they
  interrupt the waveform. **Stop experiment and EMERGENCY STOP remain available**.
  Stop uses this branch's existing stop protocol; hardware may require reconnection.
  It does not promise a controlled retract after a hardware stop.
- No equivalent-circuit fitting, high-frequency EIS, multisine, calibration,
  Kramers–Kronig validation, or automatic contact-quality classification is included.

## Hardware validation — before a real landing

1. Keep the pipette safely clear of the sample and verify Z direction/range.
   Check command voltage ratio and amplifier gain; do not change protective earth.
2. Use an appropriate known resistor/dummy cell within the amplifier's permitted
   connections and current range. Follow the amplifier manual; never connect a
   voltage source to its current input.
3. Start at 1 Hz and 5–10 mV peak with at least 32 samples/cycle. Keep the DC bias
   safe for the load. With the load attached, contact may trigger immediately;
   use a short, safe approach range. Manual Accept Z is also available.
4. Verify the **actual command waveform** using suitable measurement equipment:
   amplitude, offset, frequency, discontinuities and missing holds. E1 in the CSV
   alone cannot establish the voltage across the cell.
5. Confirm a resistor gives the expected real impedance and near-zero phase.
   Repeat at 2, 3, 5 and 10 Hz. Do not accept a systematic phase error as chemistry.
6. Test a known RC network and compare with `Rs + Rp/(1 + j·2πf·Rp·C)`.
   Record amplifier filter and gain. Repeat with changed filter/averaging to
   reveal instrumental phase bias. The software does not compensate it.
7. Repeat with half the amplitude. Impedance should agree within experimental
   uncertainty; disagreement suggests nonlinearity or inadequate signal-to-noise.
8. Test Stop with the dummy load before using a pipette. Confirm outputs and
   recovery behaviour. Reconnect if the existing stop protocol requires it.
9. Only after satisfactory load tests, try one stable droplet contact with a
   narrow, safe Z range. Confirm return to initial Z after normal completion.
10. Preserve raw files, filter/gain settings and the reference-load results when
    reporting problems. Do not treat this prototype's spectra as calibrated data.
