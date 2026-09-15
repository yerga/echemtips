# Contributing to eChemTips

eChemTips controls research hardware. Changes should remain easy to review,
reproduce in Simulation, and commission deliberately on the instrument. A unit
test passing against a fake FPGA session is necessary evidence, not permission
to energize real outputs.

## Development setup

Use a supported 64-bit CPython. Python 3.11 is recommended for consistency with
the initial hardware environment.

```bash
git clone https://github.com/yerga/echemtips.git
cd echemtips
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[analysis]"
python -m unittest discover -v
python run_echemtips.py --smoke-test
python run_analysis.py --smoke-test
```

Install `.[fpga]` only on a system with the required NI runtime when working on
the real backend. Normal tests must not require an NI device, private bitfile,
or legacy LabVIEW checkout.

## Repository boundaries

Never commit:

- LabVIEW `.vi`, `.lvproj`, or related private legacy sources;
- compiled `.lvbitx` images or FPGA binaries;
- local settings, calibration records, experimental data, or patient/sample
  identifiers;
- virtual environments, caches, generated build products, or editor state.

The `.gitignore` is a guard, not authorization to place sensitive material in
the working tree. Check `git status --short` and the staged diff before every
commit.

## Change workflow

1. Start from a clean branch based on the latest `main`.
2. Reproduce the behavior in Simulation or a fake-session test.
3. Make one coherent change. Preserve unrelated user changes.
4. Add tests at the lowest useful boundary: pure calculation, state machine,
   protocol/fake session, recording, or Qt smoke/layout.
5. Update operator, data, settings, integration, or commissioning documentation
   when behavior or semantics change.
6. Run the complete test and smoke-test commands.
7. Review the diff for unsafe output changes, stale names/units, private files,
   and unbounded recording/rendering behavior.
8. Use a focused commit message that describes the outcome.

## Design rules

- Keep experiment policy in `experiments.py` and parameter validation in
  `models.py`; do not duplicate protocol conversion in UI pages.
- Keep deterministic target framing/scaling in `ni_protocol.py`, common
  physical plans in `waypoints.py`, and NI execution ownership in
  `ni_driver.py`.
- Access hardware through `InstrumentBackend`. UI code must not open NI
  sessions or read/write registers directly.
- Preserve one owner per program, complete 14-word FIFO framing, final
  acquisition drain, and explicit terminal recording state.
- Full-rate recording and display decimation are separate responsibilities.
  Never improve plot responsiveness by dropping recorder input silently.
- Keep simulator and hardware stage semantics aligned, particularly contact,
  end-of-travel, settling, retract, cancellation, and completion.
- Treat Current 1/2, E1/E2, and commanded/measured position terminology and
  units consistently.
- Do not broaden hardware support by guessing register names or bitfile
  compatibility. Add a target profile only with its independently verified
  contract and commissioning plan.

## Safety review for hardware-affecting changes

Call out changes to startup, motion, voltage conversion, feedback, pause, stop,
watchdog, FIFO framing, or completion semantics in the pull request. Include:

- the old and new physical behavior;
- limits and failure state;
- simulator and fake-session evidence;
- whether the bitfile changes (normally it must not);
- the smallest staged hardware test required;
- rollback and recovery steps.

Do not make automated tests connect to `RIO0` by default. A physical test must
require an explicit operator action, exact target/resource, prepared hardware,
and the procedure in `docs/REAL_HARDWARE_SETUP.md`.

## Tests and evidence

| Area | Preferred evidence | What it does not prove |
| --- | --- | --- |
| Models/calculations | focused `unittest` cases | physical calibration |
| Experiment state machines | deterministic `SimulationBackend` tests | FPGA timing or wiring |
| NI protocol/driver | fake registers/FIFOs and `.lvbitx` inspection | NI driver compatibility or analog response |
| Acquisition/recording | backlog, drain, failure, round-trip tests | storage reliability for a particular long run |
| Qt UI | offscreen layout and smoke tests | every screen/DPI combination or operator comprehension |
| Physical commissioning | signed instrument record and saved traces | behavior after an unreviewed hardware/software change |

When fixing a bug, add a test that fails for the original behavior whenever it
can be expressed safely without hardware.

## Documentation style

Write for one of four audiences: simulator user, instrument operator, data
analyst, or developer. Lead with the action or contract, give every physical
quantity a unit, and label unverified hardware statements explicitly. Prefer
links to source definitions over copying values into multiple documents.

Historical audits must state their date and commit and must not be worded as
the current capability reference. Current behavior belongs in the README,
operator guide, data format, settings, architecture, or hardware integration
documents.

## Issues and pull requests

Bug reports should include the eChemTips commit, platform and Python version,
backend, experiment, exact message, minimal reproduction, and recording status.
For hardware reports use the template in `docs/TROUBLESHOOTING.md`, removing
private paths and data before posting publicly.
