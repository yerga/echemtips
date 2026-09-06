from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from echemtips.analysis import AnalysisDataset, extract_cv_cycles


class AnalysisTests(unittest.TestCase):
    def _write_recording(self, folder: str, voltages: list[float], cycles: int = 2) -> Path:
        path = Path(folder) / "approach_then_cv.csv"
        columns = ("elapsed_s", "voltage1_v", "current1_na", "z_um")
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for index, voltage in enumerate(voltages):
                writer.writerow(
                    {
                        "elapsed_s": index * 0.1,
                        "voltage1_v": voltage,
                        "current1_na": 2 * voltage + index * 0.01,
                        "z_um": 68,
                    }
                )
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "experiment": "Approach then CV",
                    "parameters": {
                        "approach_voltage_v": 0.1,
                        "cv_start_v": -0.2,
                        "cv_vertex1_v": 0.6,
                        "cv_vertex2_v": -0.4,
                        "cycles": cycles,
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_loader_reads_csv_and_metadata(self) -> None:
        with TemporaryDirectory() as folder:
            path = self._write_recording(folder, [0.1, -0.2, 0.6, -0.4, -0.2], cycles=1)
            dataset = AnalysisDataset.load(path)
            self.assertEqual(dataset.experiment, "Approach then CV")
            self.assertEqual(len(dataset.rows), 5)
            self.assertAlmostEqual(dataset.duration_s, 0.4)

    def test_cv_cycles_are_separated_by_voltage_program(self) -> None:
        voltages = [
            0.1,
            0.1,
            -0.18,
            0.2,
            0.6,
            0.1,
            -0.4,
            -0.2,
            0.2,
            0.6,
            0.1,
            -0.4,
            -0.2,
            -0.2,
        ]
        with TemporaryDirectory() as folder:
            dataset = AnalysisDataset.load(self._write_recording(folder, voltages))
            cycles = extract_cv_cycles(dataset)
            self.assertEqual(len(cycles), 2)
            for cycle in cycles:
                self.assertEqual(max(cycle.potential_v), 0.6)
                self.assertEqual(min(cycle.potential_v), -0.4)
                self.assertEqual(cycle.potential_v[-1], -0.2)

    def test_watch_current_has_no_cv_cycles(self) -> None:
        dataset = AnalysisDataset(
            Path("watch.csv"),
            ("elapsed_s", "voltage1_v", "current1_na"),
            [{"elapsed_s": 0.0, "voltage1_v": 0.1, "current1_na": 1.0}],
            {"experiment": "Watch Current", "parameters": None},
        )
        self.assertEqual(extract_cv_cycles(dataset), [])

    def test_standalone_cv_uses_unprefixed_program_fields(self) -> None:
        voltages = (-0.2, 0.0, 0.6, 0.0, -0.4, -0.2)
        dataset = AnalysisDataset(
            Path("cv.csv"),
            ("elapsed_s", "voltage1_v", "current1_na"),
            [
                {"elapsed_s": float(index), "voltage1_v": voltage, "current1_na": voltage * 2}
                for index, voltage in enumerate(voltages)
            ],
            {"experiment": "CV", "parameters": {
                "start_v": -0.2, "vertex1_v": 0.6, "vertex2_v": -0.4, "cycles": 1,
            }},
        )
        cycles = extract_cv_cycles(dataset)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].potential_v[-1], -0.2)

    def test_scan_cvs_are_separated_per_pixel(self) -> None:
        rows = []
        for pixel in (0, 1):
            for index, voltage in enumerate((0.1, -0.2, 0.2, 0.6, 0.0, -0.4, -0.2)):
                rows.append({
                    "elapsed_s": float(len(rows)), "voltage1_v": voltage,
                    "current1_na": voltage + pixel, "scan_pixel": float(pixel),
                })
        dataset = AnalysisDataset(
            Path("scan.csv"),
            ("elapsed_s", "voltage1_v", "current1_na", "scan_pixel"),
            rows,
            {"experiment": "Scan Hopping CV", "parameters": {
                "approach_voltage_v": 0.1, "cv_start_v": -0.2,
                "cv_vertex1_v": 0.6, "cv_vertex2_v": -0.4, "cycles": 1,
            }},
        )
        cycles = extract_cv_cycles(dataset)
        self.assertEqual(len(cycles), 2)
        self.assertEqual([cycle.pixel for cycle in cycles], [0, 1])


if __name__ == "__main__":
    unittest.main()
