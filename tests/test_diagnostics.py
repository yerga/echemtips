from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from echemtips.diagnostics import (
    pipette_radius_nm,
    resistance_fit,
    save_json_report,
    signal_statistics,
    stray_capacitance_pf,
    suggested_baseline_threshold_pa,
)
from echemtips.models import Sample


def sample(t: float, voltage: float, current: float) -> Sample:
    return Sample(t, 0, 0, 0, voltage, 0, current, 0)


class DiagnosticCalculationTests(unittest.TestCase):
    def test_noise_statistics_and_threshold_are_in_picoamps(self) -> None:
        rows = [sample(index, 0, value) for index, value in enumerate((1.000, 1.002, 0.998, 1.001))]
        stats = signal_statistics(rows, "Current 1")
        self.assertAlmostEqual(stats.mean_na, 1.00025)
        self.assertGreater(stats.rms_noise_pa, 1.0)
        self.assertGreaterEqual(suggested_baseline_threshold_pa(stats), 5 * stats.rms_noise_pa)

    def test_known_resistor_fit_returns_megohms(self) -> None:
        rows = [sample(index, voltage, 1000 * voltage / 200) for index, voltage in enumerate((-0.5, 0, 0.5))]
        fit, resistance = resistance_fit(rows, "Current 1")
        self.assertAlmostEqual(resistance, 200.0)
        self.assertAlmostEqual(fit.r_squared, 1.0)

    def test_capacitance_uses_forward_reverse_current_separation(self) -> None:
        # 50 pF at 1 V/s gives +/-0.05 nA capacitive current.
        rows = [sample(0, -1, 0), sample(1, 0, 0.05), sample(2, 1, 0.05),
                sample(3, 0, -0.05), sample(4, -1, -0.05)]
        self.assertAlmostEqual(stray_capacitance_pf(rows, "Current 1"), 50.0)

    def test_pipette_radius_and_json_report(self) -> None:
        radius = pipette_radius_nm(100, 1.0, 45)
        self.assertAlmostEqual(radius, 10.0)
        with tempfile.TemporaryDirectory() as folder:
            path = save_json_report(folder, "Pipette A", {"radius_nm": radius})
            self.assertTrue(path.is_file())
            self.assertIn("pipette_a", path.name)


if __name__ == "__main__":
    unittest.main()
