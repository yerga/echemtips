# Glossary

This glossary defines terms as eChemTips uses them. Instrument manuals and
papers may use related words differently, so method-facing labels favor an
explicit physical meaning and unit.

## Electrochemistry and motion

**Approach**
: Controlled Z motion toward the sample while evaluating a contact rule.

**Baseline-relative contact**
: Contact when the selected current changes from a measured baseline by the
  configured magnitude and direction. This compensates for a nonzero starting
  current; it does not remove drift or noise.

**Contact**
: A software decision that the selected current crossed the configured rule.
  It is not inferred merely because the commanded end Z was reached.

**Contact Z**
: Measured Z position at confirmed contact. Scan maps store this value in
  micrometres.

**Current 1 / Current 2 (`i1` / `i2`)**
: Converted currents from AI3 and AI4. The UI and CSV use nanoamperes; contact
  thresholds are entered in picoamperes for convenient SECCM-scale values.

**Potential 1 / Potential 2 (`E1` / `E2`)**
: Electrochemical command/readback channels associated with AO3 and AO4.
  Method controls use potential or `E`; low-level channel descriptions may use
  voltage. Values are in volts.

**Retract distance**
: Positive distance away from contact, opposite the approach direction.
  Hopping scans limit the resulting command to the configured Z travel range
  and report shortened retractions. No available travel stops the scan before
  another lateral move.

**Settling time**
: An optional hold after positioning and before the electrochemical program.
  Samples may still be acquired, but the next method stage does not start until
  the hold expires.

**Cyclic voltammetry (CV)**
: Potential program from start to vertex 1, then vertex 2, and back to start for
  the requested number of cycles.

**Chronoamperometry / I-t**
: Current-versus-time acquisition during an initial potential, pulse potential,
  and return potential with configured hold durations.

## Scan geometry

**Hop / pixel**
: One lateral scan location containing an approach, contact-dependent method,
  and retract. `scan_pixel` is the zero-based acquisition-order identifier.

**Serpentine scan**
: Alternating X direction on successive rows, avoiding a full X flyback.

**Raster scan**
: Every measurement row runs in the same X direction. A retracted flyback
  returns to the row start before the next row.

**Square footprint**
: Include every point in the configured rectangular grid.

**Circular footprint**
: Include grid points whose physical coordinates fall within the configured
  ellipse/circle. Maps still use physical X and Y axes.

**Line-end retract**
: Additional conservative retract used before the longer raster flyback.

## Signals, execution, and files

**Commanded position**
: Position requested by the host program. It is useful for control and
  diagnostics but is intentionally omitted from compact per-sample CSV rows.

**Measured position**
: Position reconstructed from X/Y/Z input readback. This is what the persistent
  status bar and recording columns `x_um`, `y_um`, and `z_um` report.

**Waypoint**
: One FPGA motion/potential instruction containing targets, rates or duration,
  and control flags. A long program is streamed; it is not limited to one FIFO
  payload.

**Sample frame**
: One complete FIFO acquisition record. All values in a frame must remain
  aligned; cancellation and draining may not split a frame.

**`line_number`**
: FPGA-emitted execution tag retained for compatibility and later protocol
  analysis. It is not currently guaranteed to restart for each recording and
  must not be the sole method-stage identifier.

**Full-rate recording**
: Every acquired sample is streamed to the CSV. Plot decimation and rolling
  display windows do not reduce saved data.

**Display decimation**
: Reduction of plotted points to keep rendering responsive while preserving the
  full-rate recording.

**Recording status**
: JSON lifecycle value: `running`, `complete`, `aborted`, `error`, or
  `discarded`. A `running` sidecar after a crash identifies a partial recording.

## Hardware boundary

**Backend**
: Runtime adapter that implements the same instrument contract for either the
  simulator or NI FPGA hardware.

**Bitfile (`.lvbitx`)**
: Compiled LabVIEW FPGA image built for the specific NI target device. eChemTips drives it;
  Python does not replace the FPGA logic.

**FIFO**
: First-in, first-out DMA channel carrying waypoint words to the FPGA or sample
  words back to the host.

**Protocol manifest**
: Offline description extracted from the bitfile: signature, target class,
  registers, FIFOs, data types, and record widths.

**Startup authorization**
: Explicit operator confirmation required before a hardware connection runs the
  FPGA and changes physical outputs.

**Commissioning**
: Recorded validation on the exact NI host, bitfile, cabling, scaling, polarity,
  readback, and controlled load. Passing simulation and offline tests is not
  commissioning evidence.
