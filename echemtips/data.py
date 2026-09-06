from __future__ import annotations

import csv
from dataclasses import asdict, fields, is_dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, TextIO

from .models import AppSettings, Sample, ScanHoppingCVParameters, ScanHoppingITParameters


class DataRecorder:
    """Record samples to disk while an experiment is running.

    Calling :meth:`start` without settings retains the original in-memory
    behaviour. Supplying settings enables runtime streaming: the CSV and its
    metadata sidecar are created before the first sample, so an interrupted
    process still leaves a readable partial recording behind.
    """

    FLUSH_EVERY = 16
    MAX_RECENT_SAMPLES = 1024
    _STATUSES = {"running", "complete", "aborted", "error", "discarded"}
    _PER_SAMPLE_OMISSIONS = {
        "feedback_type",
        "scan_row",
        "scan_column",
        "commanded_x_um",
        "commanded_y_um",
        "commanded_z_um",
    }

    def __init__(
        self,
        recent_sample_limit: int = MAX_RECENT_SAMPLES,
        flush_every: int = FLUSH_EVERY,
    ) -> None:
        if recent_sample_limit < 1:
            raise ValueError("recent_sample_limit must be positive")
        if flush_every < 1:
            raise ValueError("flush_every must be positive")
        self.samples: list[Sample] = []
        self.name = ""
        self.started_at: datetime | None = None
        self._recent_sample_limit = recent_sample_limit
        self._flush_every = flush_every
        self._csv_stream: TextIO | None = None
        self._csv_writer: csv.DictWriter[str] | None = None
        self._csv_path: Path | None = None
        self._metadata_path: Path | None = None
        self._settings: AppSettings | None = None
        self._parameters: Any = None
        self._metadata: dict[str, Any] = {}
        self._sample_count = 0
        self._writes_since_sync = 0
        self._elapsed_origin_s: float | None = None
        self._csv_fieldnames = self._fields_for_parameters(None)

    @property
    def active(self) -> bool:
        return self.started_at is not None

    @property
    def output_path(self) -> Path | None:
        return self._csv_path

    @property
    def sample_count(self) -> int:
        return self._sample_count if self._csv_path else len(self.samples)

    def start(
        self,
        name: str,
        settings: AppSettings | None = None,
        parameters: Any = None,
    ) -> None:
        """Start a recording, optionally streaming it to ``settings``' folder."""

        if self.active:
            self.finish(status="aborted")
        self.samples.clear()
        self.name = name
        self.started_at = datetime.now()
        self._settings = settings
        self._parameters = parameters
        self._sample_count = 0
        self._writes_since_sync = 0
        self._elapsed_origin_s = None
        self._csv_stream = None
        self._csv_writer = None
        self._csv_path = None
        self._metadata_path = None
        self._metadata = {}
        self._csv_fieldnames = self._fields_for_parameters(parameters)

        if settings is None:
            return

        try:
            folder = Path(settings.save_directory).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            csv_path = self._unique_csv_path(folder, self.started_at, name)
            metadata_path = csv_path.with_suffix(".json")
            stream = csv_path.open("x", newline="", encoding="utf-8", buffering=1)
            writer = csv.DictWriter(stream, fieldnames=self._csv_fieldnames)
            writer.writeheader()
            stream.flush()
            self._csv_stream = stream
            self._csv_writer = writer
            self._csv_path = csv_path
            self._metadata_path = metadata_path
            self._metadata = {
                "experiment": self.name,
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "sample_count": 0,
                "status": "running",
                "recording_schema_version": 2,
                "csv_columns": list(self._csv_fieldnames),
                "settings": self._json_value(settings),
                "parameters": self._json_value(parameters),
            }
            scan_grid = self._scan_grid_metadata(parameters)
            if scan_grid is not None:
                self._metadata["scan_grid"] = scan_grid
            self._write_metadata()
        except Exception as exc:
            self._close_csv()
            self._metadata["status"] = "error"
            self._metadata["error"] = str(exc)
            self._try_write_metadata()
            self.started_at = None
            raise

    def append(self, sample: Sample) -> None:
        if not self.active:
            return
        if self._elapsed_origin_s is None:
            self._elapsed_origin_s = sample.elapsed_s
            if self._metadata:
                self._metadata["source_elapsed_origin_s"] = self._elapsed_origin_s
        recorded = replace(sample, elapsed_s=max(0.0, sample.elapsed_s - self._elapsed_origin_s))
        if self._csv_writer is None:
            self.samples.append(recorded)
            return
        try:
            if len(self.samples) >= self._recent_sample_limit:
                self.samples.pop(0)
            self.samples.append(recorded)
            self._csv_writer.writerow(self._sample_row(recorded))
            self._sample_count += 1
            self._metadata["sample_count"] = self._sample_count
            stream = self._csv_stream
            assert stream is not None
            stream.flush()
            self._writes_since_sync += 1
            if self._writes_since_sync >= self._flush_every:
                self._sync_csv()
                self._writes_since_sync = 0
                self._write_metadata()
        except Exception as exc:
            self._handle_stream_error(exc)
            raise

    def discard(self) -> None:
        if self._csv_writer is not None:
            try:
                self._finish_stream("discarded")
            except Exception as exc:
                self._handle_stream_error(exc)
            self.samples.clear()
            self.name = ""
            self._settings = None
            self._parameters = None
            return
        self.samples.clear()
        self.name = ""
        self.started_at = None
        self._settings = None
        self._parameters = None

    def finish(
        self,
        settings: AppSettings | None = None,
        parameters: Any = None,
        status: str = "complete",
    ) -> Path | None:
        if not self.active:
            return None
        if status not in self._STATUSES - {"running"}:
            raise ValueError(f"invalid recording status: {status}")
        if self._csv_writer is not None:
            if settings is not None:
                self._settings = settings
                self._metadata["settings"] = self._json_value(settings)
            if parameters is not None:
                self._parameters = parameters
                self._metadata["parameters"] = self._json_value(parameters)
                scan_grid = self._scan_grid_metadata(parameters)
                if scan_grid is not None:
                    self._metadata["scan_grid"] = scan_grid
            try:
                self._finish_stream(status)
            except Exception as exc:
                self._handle_stream_error(exc)
                raise
            return self._csv_path

        started = self.started_at
        self.started_at = None
        if settings is None:
            raise ValueError("settings are required to finish an in-memory recording")
        if not self.samples:
            return None
        folder = Path(settings.save_directory).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        stem = self._unique_stem(folder, started, self.name)
        csv_path = folder / f"{stem}.csv"
        fieldnames = self._fields_for_parameters(parameters)
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self._sample_row(sample, fieldnames) for sample in self.samples)
        metadata = {
            "experiment": self.name,
            "started_at": started.isoformat(timespec="seconds"),
            "sample_count": len(self.samples),
            "status": status,
            "recording_schema_version": 2,
            "csv_columns": list(fieldnames),
            "settings": self._json_value(settings),
            "parameters": self._json_value(parameters),
        }
        scan_grid = self._scan_grid_metadata(parameters)
        if scan_grid is not None:
            metadata["scan_grid"] = scan_grid
        csv_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return csv_path

    def _finish_stream(self, status: str) -> None:
        self._metadata["sample_count"] = self._sample_count
        self._metadata["status"] = status
        self._metadata["finished_at"] = datetime.now().isoformat(timespec="seconds")
        stream = self._csv_stream
        if stream is not None:
            stream.flush()
            self._sync_csv()
        self._close_csv()
        self._write_metadata()
        self.started_at = None

    def _handle_stream_error(self, exc: Exception) -> None:
        self._metadata["status"] = "error"
        self._metadata["error"] = str(exc)
        self._metadata["sample_count"] = self._sample_count
        self._close_csv()
        self._try_write_metadata()
        self.started_at = None

    def _close_csv(self) -> None:
        stream = self._csv_stream
        self._csv_stream = None
        self._csv_writer = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def _sync_csv(self) -> None:
        stream = self._csv_stream
        if stream is None:
            return
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except (AttributeError, ValueError):
            pass

    def _write_metadata(self) -> None:
        path = self._metadata_path
        if path is None:
            return
        payload = json.dumps(self._metadata, indent=2) + "\n"
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except (AttributeError, ValueError):
                pass
        os.replace(temporary, path)

    def _try_write_metadata(self) -> None:
        try:
            self._write_metadata()
        except Exception:
            pass

    @staticmethod
    def _json_value(value: Any) -> Any:
        return asdict(value) if is_dataclass(value) else value

    @classmethod
    def _fields_for_parameters(cls, parameters: Any) -> tuple[str, ...]:
        omitted = set(cls._PER_SAMPLE_OMISSIONS)
        if not isinstance(parameters, (ScanHoppingCVParameters, ScanHoppingITParameters)):
            omitted.add("scan_pixel")
        return tuple(field.name for field in fields(Sample) if field.name not in omitted)

    def _sample_row(self, sample: Sample, fieldnames: tuple[str, ...] | None = None) -> dict[str, float | int]:
        values = sample.as_row()
        return {name: values[name] for name in (fieldnames or self._csv_fieldnames)}

    @staticmethod
    def _scan_grid_metadata(parameters: Any) -> dict[str, Any] | None:
        if not isinstance(parameters, (ScanHoppingCVParameters, ScanHoppingITParameters)):
            return None
        pixels = [
            {
                "scan_pixel": pixel,
                "scan_row": row,
                "scan_column": column,
                "x_um": x_um,
                "y_um": y_um,
            }
            for pixel, (row, column, x_um, y_um) in enumerate(parameters.grid())
        ]
        return {
            "coordinate_unit": "um",
            "path": "serpentine" if parameters.serpentine else "raster",
            "pixel_count": len(pixels),
            "pixels": pixels,
        }

    @classmethod
    def _unique_stem(cls, folder: Path, started: datetime, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "recording"
        base = f"{started:%Y%m%d_%H%M%S}_{slug}"
        stem = base
        suffix = 1
        while (folder / f"{stem}.csv").exists() or (folder / f"{stem}.json").exists():
            stem = f"{base}_{suffix:03d}"
            suffix += 1
        return stem

    @classmethod
    def _unique_csv_path(cls, folder: Path, started: datetime, name: str) -> Path:
        return folder / f"{cls._unique_stem(folder, started, name)}.csv"
