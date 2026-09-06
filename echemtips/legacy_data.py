"""Readers for the files written by the original WEC-SPM LabVIEW program.

The LabVIEW save dialog writes a row-oriented TSV (one channel per line and
one sample per column), a companion ``.set`` text file, and a TDMS/TDMS index
pair.  The channel names and units below are taken from the WEC-SPM user guide
section *Data Format*; they are deliberately explicit.  In particular, an
unlabelled ``ai0``/``channel 0`` column is not guessed to be a position or a
current.  Such a file must be exported again with identified headers.

Canonical columns use the same names as the Python recorder:

* X, Y, Z and AI-0/1/2 are measured positions in micrometres;
* V1/V2 are volts;
* Current1/2/3 (AI-3/4/6) and lock-in amplitude (AI-5) are amperes in the
  LabVIEW file and are converted to nA;
* lock-in phase is degrees; Feedback Type and Line Number are dimensionless;
* dt (s) is elapsed time in seconds.

``nptdms`` is intentionally optional.  TSV/SET import has no third-party
dependency, while loading a TDMS file gives an actionable error when the
optional dependency is not installed.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


class LegacyDataError(RuntimeError):
    """A legacy file is unavailable, malformed, or has ambiguous channels."""


@dataclass(slots=True)
class LegacyRecording:
    """Normalized numeric data returned by a legacy reader."""

    path: Path
    columns: tuple[str, ...]
    rows: list[dict[str, float]]
    metadata: dict[str, Any]


# Source names are intentionally not inferred from position in a file.  The
# aliases are the channel names documented by the original user guide plus
# the canonical names used by the Python recorder.
_CHANNELS: dict[str, tuple[str, str]] = {
    "x": ("x_um", "um"),
    "y": ("y_um", "um"),
    "z": ("z_um", "um"),
    "v1": ("voltage1_v", "v"),
    "v2": ("voltage2_v", "v"),
    "current1": ("current1_na", "a"),
    "current2": ("current2_na", "a"),
    "current3": ("current3_na", "a"),
    "current4": ("current4_na", "a"),
    "feedbacktype": ("feedback_type", "dimensionless"),
    "linenumber": ("line_number", "dimensionless"),
    "lockinamplitude": ("lockin_amplitude_na", "a"),
    "lockinphase": ("lockin_phase_deg", "deg"),
    "dt": ("elapsed_s", "s"),
    "dts": ("elapsed_s", "s"),
    "voltage1": ("voltage1_v", "v"),
    "voltage2": ("voltage2_v", "v"),
    # Python recorder names are accepted when a legacy export has already
    # been renamed.  Their suffix is an explicit unit declaration.
    "elapseds": ("elapsed_s", "s"),
    "voltage1v": ("voltage1_v", "v"),
    "voltage2v": ("voltage2_v", "v"),
    "current1na": ("current1_na", "na"),
    "current2na": ("current2_na", "na"),
    "current3na": ("current3_na", "na"),
    "current4na": ("current4_na", "na"),
    "z um": ("z_um", "um"),
}

# The physical mapping in the guide identifies these raw names, including
# their units, so they are safe aliases (unlike an arbitrary AI channel).
_RAW_CHANNELS: dict[str, str] = {
    "ai0": "x_um",
    # AI1 is documented as Y *or* Current4, depending on the instrument
    # wiring.  It must therefore be exported as an identified Y or Current4
    # channel, never guessed from the raw connector name.
    "ai2": "z_um",
    "ai3": "current1_na",
    "ai4": "current2_na",
    "ai5": "lockin_amplitude_na",
    "ai6": "current3_na",
    "ai7": "lockin_phase_deg",
    "ao3": "voltage1_v",
    "ao4": "voltage2_v",
}

_EXPECTED_UNITS = {
    "um": {"um", "µm", "μm", "nm", "m"},
    "v": {"v", "mv"},
    "a": {"a", "ma", "ua", "µa", "μa", "na", "pa"},
    "na": {"a", "ma", "ua", "µa", "μa", "na", "pa"},
    "s": {"s", "ms", "us", "µs", "μs", "ns"},
    "deg": {"deg", "degree", "degrees"},
    "dimensionless": {"", "index", "count", "counts"},
}

# Public, documentation-friendly view of the supported LabVIEW names.  Values
# are canonical Python column names; unit and scaling rules are described in
# the module docstring and enforced by ``_channel_spec``.
LEGACY_CHANNEL_MAP = {
    "X": "x_um",
    "Y": "y_um",
    "Z": "z_um",
    "V1": "voltage1_v",
    "V2": "voltage2_v",
    "Current1": "current1_na",
    "Current2": "current2_na",
    "Current3": "current3_na",
    "Feedback Type": "feedback_type",
    "Line Number": "line_number",
    "Lock-in Amplitude": "lockin_amplitude_na",
    "Lock-in Phase": "lockin_phase_deg",
    "dt (s)": "elapsed_s",
}

_UNIT_RE = re.compile(r"\s*[\[(]\s*([^\])]+?)\s*[\])]\s*$")


def _key(value: str) -> str:
    return value.strip().casefold().replace("−", "-")


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9µμ ]", "", _key(value)).replace(" ", "")


def _unit_factor(unit: str, expected: str) -> float:
    unit = unit.strip().casefold().replace("μ", "µ").replace("²", "2")
    if unit not in _EXPECTED_UNITS[expected]:
        raise LegacyDataError(f"Unit {unit!r} is not valid for a {expected} channel.")
    if expected == "um":
        return {"um": 1.0, "µm": 1.0, "nm": 1e-3, "m": 1e6}[unit]
    if expected == "v":
        return {"v": 1.0, "mv": 1e-3}[unit]
    if expected in {"a", "na"}:
        return {"a": 1e9, "ma": 1e6, "ua": 1e3, "µa": 1e3, "na": 1.0, "pa": 1e-3}[unit]
    if expected == "s":
        return {"s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}[unit]
    return 1.0


def _channel_spec(label: str) -> tuple[str, float]:
    """Return canonical column and conversion factor for an identified label."""
    original = label.strip().lstrip("#").strip()
    if not original:
        raise LegacyDataError("A legacy data channel has an empty name.")
    unit_match = _UNIT_RE.search(original)
    unit = unit_match.group(1).strip() if unit_match else None
    base = original[: unit_match.start()].strip() if unit_match else original
    compact = _compact(base)
    raw = compact.replace("-", "")
    if raw in _RAW_CHANNELS:
        canonical = _RAW_CHANNELS[raw]
        expected = {
            "x_um": "um", "y_um": "um", "z_um": "um", "voltage1_v": "v", "voltage2_v": "v",
            "current1_na": "a", "current2_na": "a", "current3_na": "a", "lockin_amplitude_na": "a",
            "lockin_phase_deg": "deg",
        }.get(canonical, "dimensionless")
        # Raw AI/AO aliases are documented with units.  If a unit is present,
        # still validate it rather than silently accepting a conflict.
        return canonical, _unit_factor(unit, expected) if unit else (1.0 if expected != "a" else 1e9)
    if compact in {"current", "voltage", "v", "ai", "ao", "channel", "signal"}:
        raise LegacyDataError(f"Ambiguous legacy channel {label!r}; use a documented channel name and unit.")
    if compact not in _CHANNELS:
        raise LegacyDataError(f"Unknown legacy channel {label!r}; no documented unit mapping exists.")
    canonical, expected = _CHANNELS[compact]
    # Exact documented names carry the units from the user guide.  A renamed
    # or decorated channel must identify its unit explicitly.
    documented = compact in {
        "x", "y", "z", "v1", "v2", "voltage1", "voltage2", "current1", "current2", "current3", "current4",
        "feedbacktype", "linenumber", "lockinamplitude", "lockinphase", "dt", "dts",
        "elapseds", "voltage1v", "voltage2v", "current1na", "current2na", "current3na", "current4na",
    }
    if unit is None and not documented and expected != "dimensionless":
        raise LegacyDataError(f"Legacy channel {label!r} must include its unit (for example 'Current1 (A)').")
    default_unit = "" if expected == "dimensionless" else expected
    return canonical, _unit_factor(unit or default_unit, expected)


def _numeric_values(values: Iterable[str], line_number: int) -> list[float]:
    cleaned = [value.strip() for value in values]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if any(not value for value in cleaned):
        raise LegacyDataError(f"Missing numeric value on legacy TSV line {line_number}.")
    if not cleaned:
        raise LegacyDataError(f"A legacy data channel has no samples on TSV line {line_number}.")
    result: list[float] = []
    for value in cleaned:
        try:
            result.append(float(value))
        except ValueError as exc:
            raise LegacyDataError(f"Invalid numeric value on legacy TSV line {line_number}.") from exc
    return result


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def parse_set(path: Path | str) -> dict[str, Any]:
    """Read a LabVIEW ``.set`` key/value file without guessing its schema."""
    source = Path(path)
    if not source.exists():
        return {}
    metadata: dict[str, Any] = {}
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LegacyDataError(f"Could not read SET file {source.name}: {exc}") from exc
    for line in lines:
        text = line.strip()
        if not text or text.startswith(("#", ";")):
            continue
        cells = [cell.strip() for cell in (text.split("\t") if "\t" in text else text.split("=", 1))]
        if len(cells) < 2 or not cells[0]:
            continue
        key, value = cells[0], cells[1]
        try:
            metadata[key] = float(value)
        except ValueError:
            metadata[key] = value
    return metadata


def _metadata_value(metadata: Mapping[str, Any], names: set[str]) -> float | None:
    for key, value in metadata.items():
        if _compact(str(key)) in names:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _elapsed_from_metadata(count: int, metadata: Mapping[str, Any]) -> list[float] | None:
    interval = _metadata_value(metadata, {"sampletimeus"})
    if interval is not None:
        interval *= 1e-6
    else:
        rate = _metadata_value(metadata, {"sampleratehz", "samplerate"})
        if rate is not None and rate > 0:
            interval = 1.0 / rate
    if interval is None or interval <= 0:
        return None
    samples = _metadata_value(metadata, {"samplesperdatapoint", "samplesperpoint"})
    if samples is not None:
        # This is the formula printed in the original WEC-SPM user guide.
        interval *= samples + 1
    return [index * interval for index in range(count)]


def _cv_parameters(settings: Mapping[str, Any]) -> dict[str, float]:
    """Map only the CV setting names documented in the original user guide."""
    aliases: dict[str, tuple[str, ...]] = {
        "cv_start_v": ("startvoltagev", "cvstartvoltagev", "cvstartv"),
        "cv_vertex1_v": ("firstvoltagev", "cvfirstvoltagev", "cvvertex1v"),
        "cv_vertex2_v": ("secondvoltagev", "cvsecondvoltagev", "cvvertex2v"),
        "cycles": ("numberofcycles", "cycles"),
        "scan_rate_v_s": ("scanratevs", "scanrate"),
        "approach_voltage_v": ("approachvoltagev",),
    }
    parameters: dict[str, float] = {}
    for output, names in aliases.items():
        value = _metadata_value(settings, set(names))
        if value is not None:
            parameters[output] = value
    return parameters


def _rows_from_columns(path: Path, columns: Mapping[str, list[float]], metadata: dict[str, Any]) -> LegacyRecording:
    if not columns:
        raise LegacyDataError(f"No identified channels were found in {path.name}.")
    lengths = {len(values) for values in columns.values()}
    if len(lengths) != 1:
        raise LegacyDataError(f"Legacy channels in {path.name} have different sample counts.")
    count = next(iter(lengths))
    if count == 0:
        raise LegacyDataError(f"The legacy recording {path.name} contains no samples.")
    if "elapsed_s" not in columns:
        elapsed = _elapsed_from_metadata(count, metadata)
        if elapsed is None:
            raise LegacyDataError("Legacy data has no identified 'dt (s)' channel and no usable sample-time setting.")
        columns = {**columns, "elapsed_s": elapsed}
    required = {"elapsed_s", "voltage1_v", "current1_na"}
    missing = required.difference(columns)
    if missing:
        raise LegacyDataError(f"Legacy recording is missing required channels: {', '.join(sorted(missing))}.")
    ordered = tuple(columns)
    rows = [{name: float(columns[name][index]) for name in ordered} for index in range(count)]
    return LegacyRecording(path, ordered, rows, metadata)


def load_tsv(path: Path | str, metadata: Mapping[str, Any] | None = None) -> LegacyRecording:
    """Load both the documented row-oriented TSV and identified table TSVs."""
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise LegacyDataError(f"Legacy recording not found: {source}")
    merged_metadata: dict[str, Any] = dict(metadata or {})
    try:
        with source.open(newline="", encoding="utf-8-sig") as stream:
            records = [row for row in csv.reader(stream, delimiter="\t") if any(cell.strip() for cell in row)]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise LegacyDataError(f"Could not read TSV file {source.name}: {exc}") from exc
    if not records:
        raise LegacyDataError(f"The legacy TSV {source.name} is empty.")

    # Row-oriented LabVIEW export: label<TAB>sample<TAB>sample...
    first = records[0]
    row_label_ok = False
    try:
        _channel_spec(first[0])
        row_label_ok = len(first) > 1 and any(
            bool(cell.strip()) and _is_number(cell.strip()) for cell in first[1:]
        )
    except LegacyDataError:
        pass
    if row_label_ok:
        columns: dict[str, list[float]] = {}
        for line_number, record in enumerate(records, 1):
            label = record[0].strip()
            try:
                canonical, factor = _channel_spec(label)
            except LegacyDataError:
                if not any(cell.strip() for cell in record[1:]):
                    continue
                raise
            if canonical in columns:
                raise LegacyDataError(f"Duplicate legacy channel {label!r} in {source.name}.")
            columns[canonical] = [value * factor for value in _numeric_values(record[1:], line_number)]
        return _rows_from_columns(source, columns, merged_metadata)

    # Column-oriented exports are accepted only when every header is an
    # identified channel (and, for non-documented aliases, includes a unit).
    try:
        mapped = [_channel_spec(label) for label in first]
    except LegacyDataError as exc:
        raise LegacyDataError(f"TSV {source.name} has no identified channel headers: {exc}") from exc
    canonical_headers = [item[0] for item in mapped]
    if len(set(canonical_headers)) != len(canonical_headers):
        raise LegacyDataError(f"TSV {source.name} repeats an identified channel header.")
    columns = {canonical: [] for canonical in canonical_headers}
    for line_number, record in enumerate(records[1:], 2):
        if len(record) != len(canonical_headers):
            raise LegacyDataError(f"TSV {source.name} row {line_number} has the wrong number of columns.")
        for (canonical, factor), value in zip(mapped, record):
            columns[canonical].append(_numeric_values([value], line_number)[0] * factor)
    return _rows_from_columns(source, columns, merged_metadata)


def _companion_set(path: Path) -> Path | None:
    candidates = [path.with_suffix(".set"), path.with_suffix(".SET")]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def load_tdms(path: Path | str) -> LegacyRecording:
    """Read TDMS channels using optional nptdms and documented channel names."""
    source = Path(path).expanduser().resolve()
    try:
        from nptdms import TdmsFile  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LegacyDataError("TDMS support requires optional dependency 'nptdms'. Install with: pip install nptdms") from exc
    try:
        tdms = TdmsFile.read(source)
    except Exception as exc:  # nptdms exposes several backend-specific errors
        raise LegacyDataError(f"Could not read TDMS file {source.name}: {exc}") from exc
    columns: dict[str, list[float]] = {}
    metadata: dict[str, Any] = {"legacy_format": "TDMS"}
    try:
        groups = tdms.groups()
    except Exception as exc:
        raise LegacyDataError(f"TDMS file {source.name} has no readable channel groups.") from exc
    for group in groups:
        try:
            channels = group.channels()
        except Exception as exc:
            raise LegacyDataError(f"TDMS group in {source.name} has no readable channels.") from exc
        for channel in channels:
            name = str(getattr(channel, "name", ""))
            props = getattr(channel, "properties", {}) or {}
            unit = props.get("unit_string") or props.get("units") or props.get("Unit")
            label = f"{name} ({unit})" if unit and "(" not in name else name
            try:
                canonical, factor = _channel_spec(label)
            except LegacyDataError as exc:
                raise LegacyDataError(f"TDMS channel {name!r} is not an identified WEC-SPM channel: {exc}") from exc
            if canonical in columns:
                raise LegacyDataError(f"TDMS contains duplicate identified channel {name!r}.")
            try:
                values = [float(value) for value in channel[:]]
            except Exception as exc:
                raise LegacyDataError(f"Could not read values from TDMS channel {name!r}.") from exc
            columns[canonical] = [value * factor for value in values]
            for key in ("wf_increment", "wf_start_offset", "wf_start_time"):
                if key in props:
                    value = props[key]
                    item = getattr(value, "item", None)
                    if callable(item):
                        try:
                            value = item()
                        except (TypeError, ValueError):
                            pass
                    metadata[key] = value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
    if "elapsed_s" not in columns:
        increment = metadata.get("wf_increment")
        if increment is not None:
            try:
                count = len(next(iter(columns.values())))
                columns["elapsed_s"] = [index * float(increment) for index in range(count)]
            except (StopIteration, TypeError, ValueError):
                pass
    return _rows_from_columns(source, columns, metadata)


def load_legacy(path: Path | str) -> LegacyRecording:
    """Dispatch a TDMS, TSV, or SET path; reject unsupported formats."""
    source = Path(path).expanduser().resolve()
    suffix = source.suffix.casefold()
    if suffix == ".tdms":
        return load_tdms(source)
    if suffix == ".tsv":
        companion = _companion_set(source)
        settings = parse_set(companion) if companion else {}
        result = load_tsv(source, settings)
        result.metadata = {"legacy_format": "TSV", "settings": settings, **result.metadata}
        parameters = _cv_parameters(settings)
        if {"cv_start_v", "cv_vertex1_v", "cv_vertex2_v", "cycles"}.issubset(parameters):
            result.metadata["parameters"] = parameters
            result.metadata.setdefault("experiment", "Approach then CV")
        return result
    if suffix == ".set":
        settings = parse_set(source)
        tsv = source.with_suffix(".tsv")
        if not tsv.exists():
            raise LegacyDataError(f"SET file {source.name} has no companion TSV file ({tsv.name}).")
        result = load_tsv(tsv, settings)
        result.path = source
        result.metadata = {"legacy_format": "SET + TSV", "settings": settings, **result.metadata}
        parameters = _cv_parameters(settings)
        if {"cv_start_v", "cv_vertex1_v", "cv_vertex2_v", "cycles"}.issubset(parameters):
            result.metadata["parameters"] = parameters
            result.metadata.setdefault("experiment", "Approach then CV")
        return result
    raise LegacyDataError(f"Unsupported legacy format {source.suffix or '(no extension)'!r}; expected TDMS, TSV, or SET.")


# Descriptive aliases keep the small reader convenient for callers that do
# not need to know about the dispatch function.
load_legacy_data = load_legacy
load_tsv_file = load_tsv
load_tdms_file = load_tdms


__all__ = [
    "LEGACY_CHANNEL_MAP",
    "LegacyDataError",
    "LegacyRecording",
    "load_legacy",
    "load_legacy_data",
    "load_tdms",
    "load_tdms_file",
    "load_tsv",
    "load_tsv_file",
    "parse_set",
]
