# Releases, compatibility, and support status

eChemTips is currently alpha research software. Version 0.1.x APIs and saved
settings can change, but recording formats change only through an explicit
schema-version decision and documented compatibility behavior.

## Versioning

- Source releases use Semantic Versioning.
- Before 1.0, a minor release can contain reviewed API/configuration changes;
  patch releases are intended for compatible fixes and documentation.
- `recording_schema_version` is independent of the package version. Increment
  it whenever a reader cannot safely interpret the same CSV/JSON contract.
- Record target class and build signature for traceability. Compatibility
  requires the full register/FIFO contract, matching FPGA behavior and physical
  commissioning—not a particular signature or the eChemTips version alone.

## Current compatibility matrix

| Component | Declared/current status |
| --- | --- |
| Python package | CPython 3.10 or newer is declared. The developer suite currently passes in the repository's Python 3.13 environment. |
| Recommended first hardware environment | 64-bit CPython 3.11 on an NI-supported 64-bit Windows installation. Record the exact tested combination. |
| UI | PySide6 6.8–6.x and PyQtGraph 0.13.7–0.14.x as constrained by `pyproject.toml`. |
| NI Python API | Optional `nifpga >=22.0.0`; the native NI-RIO/FPGA Interface runtime is also required. Compatibility must be commissioned as a complete stack. |
| FPGA target | USB-7856R is the reference hardware used in development and reported real-device trials. Other NI R Series builds require the same protocol/behavior and per-device commissioning. Private bitfiles are not distributed; metadata tests are not physical qualification. |
| Physical instrument | Not called commissioned until the site record in `REAL_HARDWARE_SETUP.md` is completed with NanoDrive, VA-10M, wiring, load, motion, contact, and small-scan evidence. |
| Operating systems | Development/simulation is cross-platform where PySide6 supports it. Initial NI commissioning is documented for Windows. |

This table is a statement of evidence, not a promise about untested future
versions. When a new Python, NI-RIO, controller, amplifier, headstage, bitfile,
or operating-system version is qualified, update the row and attach the
instrument record or CI evidence.

## Release checklist

1. Start from a clean tree and review the complete diff for private files and
   hardware-affecting behavior.
2. Update `CHANGELOG.md`, package version, compatibility matrix, and any changed
   recording/settings semantics.
3. Run:

   ```bash
   python -m unittest discover -v
   python run_echemtips.py --smoke-test
   python run_analysis.py --smoke-test
   mkdocs build --strict
   ```

4. For protocol or hardware changes, complete the smallest staged physical
   procedure specified in the pull request and update the commissioning record.
5. Build/install in a clean virtual environment and verify console entry points.
6. Tag the reviewed commit as `vMAJOR.MINOR.PATCH` and publish release notes from
   the changelog. Do not attach private bitfiles or experiment data.
7. Verify the documentation site deployed from the tagged/main documentation.

## Deprecation and migration

When replacing a setting, environment variable, data field, or public service,
document its replacement and retain a migration path for at least one minor
release when safe. Never silently reinterpret a physical unit or channel.
Legacy LabVIEW imports remain a separate normalized input path and do not define
the native eChemTips recording schema.
