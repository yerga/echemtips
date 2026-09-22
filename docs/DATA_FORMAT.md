# Recording data format

On Windows, atomic JSON replacement retries access-denied and sharing-lock
errors (WinError 5, 32, 33) up to five times, waiting a total of 0.62 seconds.
This covers brief file locks without marking an otherwise healthy recording
as failed. The acquisition worker continues reading during these waits.
Persistent failures still abort recording and preserve the CSV and previous
JSON; a `.json.tmp` file may contain the latest metadata awaiting replacement.
Other disk errors are reported immediately.

eChemTips writes one CSV measurement stream and one JSON metadata sidecar for
each recorded experiment. This document defines recording schema version 2,
which is the format written by the current application.

## File naming and lifecycle

Files use the stem `YYYYMMDD_HHMMSS_experiment_name`. A numeric suffix is added
when that name already exists, so starting a recording never overwrites an
older result. The CSV header and JSON sidecar are created before the first
sample. CSV rows are flushed as they arrive and synchronized periodically;
therefore an interrupted run normally leaves a readable partial CSV.

The JSON `status` is one of:

| Status | Meaning |
| --- | --- |
| `running` | The recording started but has not reached a terminal update. After a process or power failure, treat this as a partial recording. |
| `complete` | The experiment and final acquisition drain completed normally. |
| `aborted` | The operator or application ended the experiment before normal completion. Retained rows are valid up to the stop. |
| `error` | Acquisition or recording failed. The optional `error` member explains the detected failure. |
| `discarded` | The operator discarded the run. The files remain as a trace of the operation, but analysis should exclude them by default. |

Do not decide that a recording is complete merely because the CSV ends cleanly;
read the JSON status. During a run, its checkpointed `sample_count` can lag the
CSV by up to a small batch. The terminal metadata update supplies the final
count.

## CSV columns

Every value is numeric. Column order is stable for schema version 2.

| Column | Unit | Meaning |
| --- | --- | --- |
| `elapsed_s` | s | Time since the first sample accepted by this recording. The first row is zero; it is independent of earlier experiments and the FPGA's absolute counter. |
| `x_um` | µm | Measured/applied X position reported for the acquired sample. |
| `y_um` | µm | Measured/applied Y position reported for the acquired sample. |
| `z_um` | µm | Measured/applied Z position reported for the acquired sample. |
| `voltage1_v` | V | Applied electrochemical potential E1 after command-ratio conversion. |
| `voltage2_v` | V | Applied electrochemical potential E2. |
| `current1_na` | nA | Current i1 after Current 1 amplifier-sensitivity conversion. |
| `current2_na` | nA | Current i2 after Current 2 amplifier-sensitivity conversion. |
| `line_number` | count | FPGA-provided waypoint/sample tag used by runtime protocol logic. See the warning below. |
| `scan_pixel` | zero-based index | Present only for hopping scans. Links a row to one entry in `scan_grid.pixels`; negative values are setup or otherwise outside a mapped pixel. |

The recorder intentionally omits runtime-only `feedback_type`, repeated
`scan_row`/`scan_column`, and commanded X/Y/Z snapshots. In particular, the
hardware adapter cannot assign a separately read position-register snapshot to
every sample in a FIFO batch without inventing timing precision.

### Line-number warning

`line_number` is emitted by the deployed FPGA protocol. It is retained for
compatibility and diagnostics, but eChemTips does not currently guarantee that
it restarts at zero for each experiment or that fixed values identify the same
experiment stage across all methods. Do not use it as the sole approach/CV/
retract segmentation rule. Prefer the saved experiment parameters, potential
waveform, `scan_pixel`, and future explicit stage metadata. Any change to these
semantics requires hardware validation and a recording-schema decision.

## JSON sidecar

The sidecar contains at least:

| Member | Meaning |
| --- | --- |
| `experiment` | Human-readable experiment name. |
| `started_at` | Local ISO-8601 start date and time, to seconds. |
| `finished_at` | Local ISO-8601 terminal time when a normal finalization path was reached. It may be absent after a write failure or process interruption. |
| `sample_count` | Number of CSV data rows known at the last metadata update. |
| `status` | Recording lifecycle value defined above. |
| `recording_schema_version` | Integer schema identifier; currently `2`. |
| `csv_columns` | Exact column list written to the paired CSV. Consumers should inspect this rather than assume scan columns. |
| `settings` | Snapshot of application settings used for the run. |
| `parameters` | Snapshot of experiment-specific parameters. |
| `source_elapsed_origin_s` | Original backend time of the first accepted sample. `elapsed_s` has this value subtracted. |
| `error` | Optional detected error text for an `error` recording. |

Hopping scans also contain `scan_grid`:

```json
{
  "coordinate_unit": "um",
  "path": "serpentine",
  "pixel_count": 4,
  "pixels": [
    {"scan_pixel": 0, "scan_row": 0, "scan_column": 0, "x_um": 10.0, "y_um": 20.0},
    {"scan_pixel": 1, "scan_row": 0, "scan_column": 1, "x_um": 11.0, "y_um": 20.0}
  ]
}
```

The `pixels` array is in acquisition-path order. In a serpentine scan, the
column order reverses on alternating rows. In a raster scan, each row begins
from the same X side after flyback. Join CSV rows to this array using
`scan_pixel`, not array position assumptions made from X/Y sorting.

## Sign conventions and interpretation

- X, Y, and Z use the coordinate convention configured for the instrument.
  Increasing Z is not universally equivalent to moving toward the surface;
  verify direction during commissioning.
- E1 and E2 are the potentials reported after application calibration. Verify
  their polarity and scale at the connector before electrochemical use.
- Current signs follow the connected amplifier and configured sensitivity.
  eChemTips does not silently invert current.
- `settings` and `parameters` preserve the requested configuration. CSV
  position and potential columns are the measurement/applied-data stream and
  should be used to determine what the target reported.

## Compatibility rules for readers

Readers should first inspect `recording_schema_version` and `csv_columns`,
ignore unknown JSON members, and reject unsupported future major schemas rather
than guessing. A missing or unreadable sidecar can still permit raw CSV viewing,
but experiment-aware extraction may be incomplete. Legacy LabVIEW TSV and TDMS
imports are normalized by the analysis loader and are not schema-version-2
recordings unless explicitly converted.
