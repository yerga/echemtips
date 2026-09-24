# Either-polarity contact detection

The control UI uses the selected Current 1 or Current 2 magnitude for all
approach and hopping methods. A threshold of 2000 pA detects either +2000 pA
or −2000 pA. A signal already outside that range triggers immediately; this
is not a baseline-relative change detector. Choose a threshold above the
pre-contact baseline magnitude and noise, and validate with a safe test load.

Python's `feedback_mode="magnitude"` configures the existing FPGA type-2
feedback OR: primary compares against +|threshold|, secondary against
−|threshold| on the same current channel. No bitfile modification is required.
The simulator and host sample checks use the same magnitude criterion.
Legacy signed `absolute` and `baseline_relative` API modes are retained.

Manual acceptance and no-contact travel-limit exits temporarily force the
secondary comparator true with an out-of-range I32 threshold appropriate to
its comparison direction. After framed completion, its configured threshold
is restored and read back. A forced travel-limit exit never authorizes CV/I–t.

Regression tests cover both currents/polarities, repeated 3×3 CV and I–t scans,
and manual/limit completion in all five approach methods. These tests use a
fake FPGA session; a controlled real-hardware polarity test remains necessary.
