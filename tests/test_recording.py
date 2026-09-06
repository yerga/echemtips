from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from echemtips.data import DataRecorder
from echemtips.models import AppSettings, ApproachCVParameters, Sample, ScanHoppingCVParameters


def sample(index: int, **tags: float | int) -> Sample:
    return Sample(
        elapsed_s=float(index),
        x_um=1.0,
        y_um=2.0,
        z_um=3.0,
        voltage1_v=0.1,
        voltage2_v=0.2,
        current1_na=10.0 + index,
        current2_na=20.0,
        **tags,
    )


class StreamingRecordingTests(unittest.TestCase):
    def settings(self, folder: str) -> AppSettings:
        return AppSettings(save_directory=folder)

    def test_files_exist_and_are_readable_before_finish(self) -> None:
        with TemporaryDirectory() as folder:
            recorder = DataRecorder()
            recorder.start("Live scan", self.settings(folder), ApproachCVParameters())
            path = recorder.output_path
            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.with_suffix(".json").read_text())["status"], "running")

            tagged = sample(
                1, line_number=7, scan_pixel=11, scan_row=2, scan_column=3,
                feedback_type=1, commanded_x_um=4, commanded_y_um=5, commanded_z_um=6,
            )
            recorder.append(tagged)
            with path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                self.assertNotIn("current3_na", reader.fieldnames or ())
                self.assertNotIn("lockin_amplitude_na", reader.fieldnames or ())
                for omitted in (
                    "feedback_type", "scan_pixel", "scan_row", "scan_column",
                    "commanded_x_um", "commanded_y_um", "commanded_z_um",
                ):
                    self.assertNotIn(omitted, reader.fieldnames or ())
            self.assertEqual(len(rows), 1)
            self.assertEqual(int(rows[0]["line_number"]), 7)
            recorder.finish(status="aborted")

    def test_unique_names_do_not_overwrite_recordings(self) -> None:
        with TemporaryDirectory() as folder:
            settings = self.settings(folder)
            paths: list[Path] = []
            for index in range(2):
                recorder = DataRecorder()
                recorder.start("Watch Current", settings)
                recorder.append(sample(index))
                output = recorder.finish()
                self.assertIsNotNone(output)
                assert output is not None
                paths.append(output)
            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual(len(list(Path(folder).glob("*.csv"))), 2)

    def test_partial_finish_and_discard_keep_terminal_metadata(self) -> None:
        with TemporaryDirectory() as folder:
            settings = self.settings(folder)
            recorder = DataRecorder()
            recorder.start("Approach", settings)
            recorder.append(sample(0))
            partial = recorder.finish(status="aborted")
            self.assertIsNotNone(partial)
            assert partial is not None
            metadata = json.loads(partial.with_suffix(".json").read_text())
            self.assertEqual(metadata["status"], "aborted")
            self.assertEqual(metadata["sample_count"], 1)

            recorder.start("Discard me", settings)
            recorder.append(sample(1))
            discarded = recorder.output_path
            recorder.discard()
            self.assertIsNotNone(discarded)
            assert discarded is not None
            self.assertTrue(discarded.exists())
            self.assertEqual(
                json.loads(discarded.with_suffix(".json").read_text())["status"],
                "discarded",
            )

    def test_recent_samples_are_bounded_but_count_is_complete(self) -> None:
        with TemporaryDirectory() as folder:
            recorder = DataRecorder(recent_sample_limit=3)
            recorder.start("Long run", self.settings(folder))
            for index in range(10):
                recorder.append(sample(index))
            self.assertEqual([item.elapsed_s for item in recorder.samples], [7.0, 8.0, 9.0])
            self.assertEqual(recorder.sample_count, 10)
            output = recorder.finish()
            self.assertIsNotNone(output)
            assert output is not None
            self.assertEqual(json.loads(output.with_suffix(".json").read_text())["sample_count"], 10)

    def test_each_recording_uses_its_own_elapsed_time_origin(self) -> None:
        with TemporaryDirectory() as folder:
            recorder = DataRecorder()
            recorder.start("Scan Hopping CV", self.settings(folder))
            recorder.append(sample(600))
            recorder.append(sample(603))
            output = recorder.finish()
            assert output is not None
            with output.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([float(row["elapsed_s"]) for row in rows], [0.0, 3.0])
            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["source_elapsed_origin_s"], 600.0)

    def test_csv_and_metadata_round_trip(self) -> None:
        with TemporaryDirectory() as folder:
            settings = self.settings(folder)
            params = ScanHoppingCVParameters(
                x_start_um=10, x_end_um=20, x_points=2,
                y_start_um=30, y_end_um=40, y_points=2,
                cycles=3, serpentine=True,
            )
            recorder = DataRecorder()
            recorder.start("Round trip", settings, params)
            recorder.append(sample(
                0, line_number=4, scan_pixel=2, scan_row=1, scan_column=1,
                feedback_type=1, commanded_x_um=20, commanded_y_um=40, commanded_z_um=58,
            ))
            output = recorder.finish()
            assert output is not None
            with output.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["sample_count"], len(rows))
            self.assertEqual(metadata["parameters"]["cycles"], 3)
            self.assertEqual(int(rows[0]["line_number"]), 4)
            self.assertEqual(int(rows[0]["scan_pixel"]), 2)
            self.assertEqual(metadata["recording_schema_version"], 2)
            self.assertEqual(metadata["csv_columns"], list(rows[0]))
            self.assertEqual(metadata["scan_grid"]["path"], "serpentine")
            self.assertEqual(metadata["scan_grid"]["pixel_count"], 4)
            self.assertEqual(
                metadata["scan_grid"]["pixels"][2],
                {"scan_pixel": 2, "scan_row": 1, "scan_column": 1, "x_um": 20.0, "y_um": 40.0},
            )
            for omitted in (
                "feedback_type", "scan_row", "scan_column",
                "commanded_x_um", "commanded_y_um", "commanded_z_um",
            ):
                self.assertNotIn(omitted, rows[0])

    def test_write_failure_preserves_rows_and_marks_error(self) -> None:
        with TemporaryDirectory() as folder:
            recorder = DataRecorder(flush_every=1)
            recorder.start("Write failure", self.settings(folder))
            recorder.append(sample(0))
            output = recorder.output_path
            assert output is not None
            writer = recorder._csv_writer
            assert writer is not None
            with patch.object(writer, "writerow", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    recorder.append(sample(1))

            with output.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["status"], "error")
            self.assertEqual(metadata["sample_count"], 1)
            self.assertFalse(recorder.active)

    def test_metadata_checkpoint_failure_preserves_csv_and_recoverable_status(self) -> None:
        with TemporaryDirectory() as folder:
            recorder = DataRecorder(flush_every=1)
            recorder.start("Checkpoint failure", self.settings(folder))
            recorder.append(sample(0))
            output = recorder.output_path
            assert output is not None
            original = recorder._write_metadata
            calls = 0

            def fail_once() -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("metadata unavailable")
                original()

            with patch.object(recorder, "_write_metadata", side_effect=fail_once):
                with self.assertRaises(OSError):
                    recorder.append(sample(1))

            with output.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["status"], "error")
            self.assertEqual(metadata["sample_count"], 2)


if __name__ == "__main__":
    unittest.main()
