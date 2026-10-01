# Two-speed Z motion profiles

Approach and retraction experiments offer a **Z motion profile…** button. Both
profiles are off by default. Their settings are kept in a dialog rather than
adding fields to the measurement page, and are included in experiment presets.
XY motion retains its existing constant speed.

## Approach

Enable two-speed approach and enter fast and slow rates. Without a usable
contact-height prediction, the complete approach uses the slow rate. Ordinary
scans need at least three non-collinear prior contacts to fit a plane; adaptive
scans can use their approved surface estimate. The fast segment ends before
predicted contact, with a configurable clearance (default 5 µm). Uncertainty
reduces the predicted safe fast-travel distance.

The clearance must cover unmeasured surface relief. A plane fit is not an
obstacle detector. Contact detection remains active during fast motion; an
early contact ends the approach rather than continuing into the slow segment.

## Retraction

Enable two-speed retraction and enter slow and fast rates. Choose either:

- **After a distance:** withdraw slowly by the specified distance from contact,
  then accelerate.
- **After detected detachment:** withdraw slowly until the selected feedback
  current has shown attached contrast and subsequently sustained collapse
  toward the dry baseline. Continue slowly by the additional withdrawal buffer,
  then accelerate. The fixed-distance field is disabled in this mode.

The detector uses either current sign, a rolling median and 40 ms confirmation,
with a noise-dependent threshold (at least 5 pA). It requires a usable dry
baseline and stable E1 during withdrawal. Low electrochemical contrast,
potential changes, saturated input or ambiguous data cause slow withdrawal to continue all the
way. This is experimental electrical evidence, not proof of physical detachment.

Neither mode changes the final configured retract Z. A buffer extending beyond
that endpoint does not extend travel. Normal completion still returns to initial
Z, and XY movement waits for withdrawal to finish. Existing stop/emergency-stop
semantics are unchanged.

## Hardware behavior and recording

No new bitfile is required. The host uses existing framed waypoints and feedback
comparators. Automatic withdrawal submits slow segments corresponding to up to
250 ms travel and switches at completed, drained waypoint boundaries. There can
be stationary gaps between segments. Host polling and FIFO latency extend the
slow distance; the buffer is a minimum, not an exact switch coordinate. FPGA AO
readback supplies commanded coordinates, avoiding mixing sensor offsets with
commanded contact Z. Pause prevents further profile submissions.

Very slow rates or large scans can exhaust the signed sample-tag budget; a
conservative preflight rejects such programs. Reconnect or reduce the program
size/increase the slow rate. Duration estimates conservatively assume slow
approach and, for automatic withdrawal, slow retraction; host overhead is not
included.

JSON parameters contain the selected profile settings and `motion_events`,
including withdrawal endpoints, detected and switch Z, rates and transition
reason. Approach switch coordinates describe the planned clearance boundary;
early contact may prevent that boundary being reached. CSV sample columns are
unchanged. Simulation exercises both modes, including detachment and buffer.

## Suggested verification

1. In simulation run a 2×2 hopping CV scan, first with fixed-distance retraction,
   then automatic retraction. A start/return potential of 0.3 V provides clear
   simulated attached-current contrast. Inspect `motion_events` in JSON.
2. Enable approach profiling; initial landings should stay slow until a usable
   surface estimate exists. Verify fast motion ends before contact.
3. Pause during slow withdrawal. Confirm motion stops, then resume and verify
   the endpoint and subsequent XY move. Stop behavior remains unchanged.
4. Commission on hardware away from a real surface first, with conservative
   rates and clearances. Check AO readback and segment transitions before using
   electrical detection on a real droplet. Compare fixed-distance and automatic
   withdrawal against camera observations; ambiguous evidence must remain slow.
5. Compare footprint images and electrochemistry before adopting a profile as a
   routine protocol. Keep rate and clearance settings with each recording.

Retraction speed can affect footprints, as discussed in
[this SECCM study](https://doi.org/10.1021/acsmeasuresciau.4c00042).
Two-speed motion is a practical hypothesis to test, not a validated universal
improvement or a universal choice of rates.
