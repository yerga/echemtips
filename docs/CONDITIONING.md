# Optional pre/post measurement holds

Use **Pre/post holds… · Off** below the electrochemistry controls. The compact
button opens a dialog; no extra potential/time fields occupy the experiment page.
Enable either hold independently, choose its E1 potential and duration, and check
the complete sequence preview before pressing OK. Cancel leaves the setup unchanged.

Supported pages are Approach + CV/LSV, its scan-rate series, Approach + I–t,
Scan hopping + CV/LSV and Scan hopping + I–t, including combinatorial scans.
Standalone CV and adaptive scan configuration are not extended in this version.

The order is:

1. Approach and confirmed contact (or explicit operator acceptance).
2. Existing settling at the approach potential.
3. Optional pre-measurement hold.
4. The complete CV/LSV program or I–t cycles.
5. Optional post-measurement hold.
6. The existing retraction, if enabled.

Holds occur once per landing, not once per CV cycle or pulse. In scan-rate series,
they bracket the entire series once. Combinatorial recipes share the two hold
settings; the final orientation marker also receives them. Per-recipe hold
overrides and arbitrary sequences belong to future sequence-editor work.

**The post-hold potential remains applied during retraction and on completion.**
Without a post-hold, the measurement's final potential remains applied as before.
Without retraction, the probe stays at the existing final position. Verify that
the chosen final potential is appropriate for the sample and subsequent workflow.

Both holds default to off. Disabling a hold removes it completely. Enabled
durations must be positive and at most 300 s; all potentials are checked against
the configured output range and command ratio. Long holds consume FPGA waypoint
tags, so the total scan/tag budget can impose a smaller practical limit.
Known-duration estimates include the holds. A no-contact exit never runs them.

## Recording and analysis

Hold settings are stored in JSON `parameters` and measurement presets. When a
hold is enabled, CSV includes one compact integer `measurement_phase` column:

| Value | Meaning |
| --- | --- |
| 0 | Other: positioning, approach, settling, reset or retract |
| 1 | Pre-measurement hold |
| 2 | Analytical measurement |
| 3 | Post-measurement hold |

The JSON `measurement_phases` dictionary documents these codes. FPGA phase tags
come from the sample's waypoint context; simulation tags are attached when the
sample is acquired, not inferred from the later UI state. Raw traces keep every
sample. CV/I–t measurement extraction, associated maps/movies and surface-program
detection exclude conditioning. Raw-data exploration remains available for
examining the holds themselves. Retraction diagnostics begin after the post-hold.
Legacy recordings remain supported; holds-off recordings add no new CSV column.

## Validation checklist

1. With holds off, run an existing simulated experiment and confirm unchanged
   waveform and CSV columns.
2. Enable a 0.15 V / 1 s pre-hold and a 0.10 V / 2 s post-hold. Confirm the preview,
   total scan-time increase, stationary Z during holds and raw E-versus-time trace.
3. Run a small CV and I–t scan; inspect each landing, measurement-only plots and
   `measurement_phase` values in the recording.
4. Test a two-rate series and a combinatorial scan. Holds should bracket each
   whole landing program, not be repeated inside every cycle/rate.
5. Pause during a hold, resume, and verify remaining duration. Stop during each
   hold and verify that no later stage runs. Existing main-branch stop/reconnect
   limitations still apply; this change does not implement recoverable stop.
6. Repeat timing/output checks on a controlled electrical load before use on a
   sample. Verify post-hold/retract potential and no-contact behaviour.

The FPGA implementation uses the existing signed-I16 microsecond hold timer,
splitting long holds into chunks. No bitfile change is required. Automated tests
exercise simulation and the fake-register/FIFO driver, but real-hardware
acceptance remains required for issue #2.
