from __future__ import annotations

import builtins
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import patch

from echemtips.analysis import AnalysisDataset, AnalysisError, extract_cv_cycles
from echemtips.legacy_data import LegacyDataError, load_legacy, load_tsv


class LegacyDataTests(unittest.TestCase):
    def test_documented_row_oriented_tsv_converts_units(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "scan.tsv"
            path.write_text(
                "X\t10\t11\nV1\t-0.2\t0.3\nCurrent1\t1e-9\t2e-9\ndt (s)\t0\t0.01\n",
                encoding="utf-8",
            )
            dataset = AnalysisDataset.load(path)
            self.assertEqual(dataset.columns, ("x_um", "voltage1_v", "current1_na", "elapsed_s"))
            self.assertEqual(dataset.rows[1]["current1_na"], 2.0)
            self.assertEqual(dataset.rows[0]["elapsed_s"], 0.0)

    def test_identified_column_headers_are_supported(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "table.tsv"
            path.write_text("dt (s)\tV1 (V)\tCurrent1 (A)\n0\t0.1\t1e-9\n", encoding="utf-8")
            dataset = load_tsv(path)
            self.assertEqual(dataset.rows[0]["voltage1_v"], 0.1)
            self.assertEqual(dataset.rows[0]["current1_na"], 1.0)

    def test_canonical_nanoamp_header_keeps_its_declared_scale(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "canonical.tsv"
            path.write_text("elapsed_s\tvoltage1_v\tcurrent1_na\n0\t0.1\t1\n", encoding="utf-8")
            dataset = load_tsv(path)
            self.assertEqual(dataset.rows[0]["current1_na"], 1.0)

    def test_blank_interior_cell_is_rejected(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "blank.tsv"
            path.write_text("V1\t0.1\t\t0.2\nCurrent1\t1e-9\t2e-9\t3e-9\ndt (s)\t0\t0.1\t0.2\n", encoding="utf-8")
            with self.assertRaisesRegex(LegacyDataError, "Missing numeric"):
                load_tsv(path)

    def test_ambiguous_raw_header_is_rejected(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "unknown.tsv"
            path.write_text("Channel 0\tChannel 1\n1\t2\n", encoding="utf-8")
            with self.assertRaises(LegacyDataError):
                load_legacy(path)

    def test_set_companion_supplies_sample_time(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder) / "watch"
            root.with_suffix(".set").write_text("Sample Time (us)\t10\nSamples per Data Point\t1\n", encoding="utf-8")
            root.with_suffix(".tsv").write_text("V1\t0.1\t0.2\nCurrent1\t1e-9\t2e-9\n", encoding="utf-8")
            dataset = AnalysisDataset.load(root.with_suffix(".set"))
            self.assertAlmostEqual(dataset.rows[1]["elapsed_s"], 20e-6)

    def test_documented_cv_settings_are_exposed_for_cycle_extraction(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder) / "approach_then_cv"
            root.with_suffix(".set").write_text(
                "Start Voltage (V)\t-0.2\nFirst Voltage (V)\t0.6\nSecond Voltage (V)\t-0.4\nNumber of Cycles\t1\n",
                encoding="utf-8",
            )
            root.with_suffix(".tsv").write_text(
                "dt (s)\t0\t0.1\t0.2\t0.3\t0.4\nV1\t-0.2\t0.6\t-0.4\t-0.2\t-0.2\nCurrent1\t1e-9\t2e-9\t-1e-9\t1e-9\t1e-9\n",
                encoding="utf-8",
            )
            dataset = AnalysisDataset.load(root.with_suffix(".set"))
            self.assertEqual(dataset.metadata["parameters"]["cycles"], 1)

    def test_negative_scan_pixel_is_not_treated_as_pixel_partition(self) -> None:
        voltages = [-0.2, 0.6, -0.4, -0.2]
        dataset = AnalysisDataset(
            Path("approach_then_cv.csv"),
            ("elapsed_s", "voltage1_v", "current1_na", "scan_pixel"),
            [{"elapsed_s": float(i), "voltage1_v": voltage, "current1_na": float(i), "scan_pixel": -1.0} for i, voltage in enumerate(voltages)],
            {"parameters": {"cv_start_v": -0.2, "cv_vertex1_v": 0.6, "cv_vertex2_v": -0.4, "cycles": 1}},
        )
        self.assertEqual(len(extract_cv_cycles(dataset)), 1)

    def test_tdms_missing_optional_dependency_is_actionable(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "recording.tdms"
            path.write_bytes(b"not a real tdms")
            real_import = builtins.__import__

            def no_nptdms(name: str, *args: object, **kwargs: object):
                if name == "nptdms":
                    raise ImportError("test")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=no_nptdms):
                with self.assertRaisesRegex(AnalysisError, "nptdms"):
                    AnalysisDataset.load(path)

    def test_tdms_shape_can_be_read_with_small_fake_nptdms(self) -> None:
        class Channel:
            def __init__(self, name: str, values: list[float], properties: dict[str, object] | None = None):
                self.name, self._values, self.properties = name, values, properties or {}

            def __getitem__(self, key: object):
                return self._values[key]  # type: ignore[index]

        class Group:
            def channels(self):
                return [Channel("V1", [0.1, 0.2]), Channel("Current1", [1e-9, 2e-9]), Channel("dt (s)", [0, 0.1])]

        fake = types.SimpleNamespace(TdmsFile=types.SimpleNamespace(read=lambda _path: types.SimpleNamespace(groups=lambda: [Group()])))
        with TemporaryDirectory() as folder:
            path = Path(folder) / "recording.tdms"
            path.write_bytes(b"fake")
            with patch.dict("sys.modules", {"nptdms": fake}):
                dataset = AnalysisDataset.load(path)
            self.assertEqual(dataset.rows[1]["current1_na"], 2.0)


if __name__ == "__main__":
    unittest.main()
