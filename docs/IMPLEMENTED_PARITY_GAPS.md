# Three priority LabVIEW-to-Python gaps

These were prioritized over additional specialized scan methods because they affect data integrity, repeat measurements, and access to existing experiments.

## 1. Recording during acquisition

The LabVIEW save workflow (`Sub_Vi/Save Data`) persists experiment data during acquisition. Python previously accumulated samples in memory and wrote them at the end.

The UI now opens unique CSV/JSON files before an experiment starts, writes samples as they arrive, keeps only a bounded recent-sample buffer, and records a terminal completion/abort/error status. Interrupted recordings retain their flushed rows. Scan pixel tags are applied before serialization and the final acquisition batch is saved before completion. Discarding a recording marks it discarded without deleting its partial file.

## 2. FPGA program completion and session reuse

The LabVIEW host (`Sub_Vi/FPGA/Update_Host.vi`) maintains an ongoing connection and retrieves final acquisition data. Python previously latched the session after the first submitted program.

The native driver now requires the full U64 line-counter advancement, an unpaused target waiting state, and a final FIFO snapshot before making the session available for another program. It rejects overlapping commands and preserves scan descriptors while final samples are tagged. A normal Stop cancels the active queue, verifies the waiting state, and permits a subsequent program; emergency stop, target faults, and uncertain FIFO writes still require deliberate target reinitialization. Hopping scans retract Z before their first XY move.

Approach + CV and Scan Hopping + CV are deliberately staged. The FPGA receives only positioning plus a type-1 pause-on-contact approach first. Python submits the CV/retract phase only after observing `Internal Pause` together with a feedback indication or acquired threshold crossing. Reaching End Z without that confirmation aborts without placing CV waypoints in the target queue.

The former 585-waypoint target-FIFO limit is removed by a reusable complete-frame streamer. The host-side DMA FIFO holds the generic driver's 65,535-frame ceiling, while bounded initial/refill writes keep target-side framing explicit. Scan plans retain a conservative 32,767-tag guard until the target's U64-to-signed-I16 narrowing behavior is confirmed on hardware.

## 3. Existing LabVIEW data in Python analysis

The original export workflow includes TSV and companion SET files. The Python analysis loader previously accepted only its own CSV/JSON recordings.

Analysis now accepts identified legacy channels with unit conversion, companion settings, and optional TDMS support through `nptdms`. Unknown/ambiguous channels are rejected. Opaque raw FPGA binary data is not treated as scaled measurements; convert it using the original LabVIEW workflow first.

When the recording lacks CV program metadata, the analysis UI lets the user supply the actual start voltage, vertices, approach voltage, and cycle count before separating its voltammograms. Python recordings with default `scan_pixel=-1` tags are also correctly treated as ordinary, non-scan CVs.

## Verification scope

Automated checks cover simulation, streaming persistence, sample ordering, legacy-format fixtures, and FPGA register/FIFO behavior using a fake session. Offscreen Qt smoke tests exercise the PySide6 control and analysis interfaces. These checks do not establish physical USB-7856R behavior; instrument commissioning remains necessary on the connected computer as described in `REAL_HARDWARE_SETUP.md`.
