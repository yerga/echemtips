"""Regenerate deterministic simulator screenshots used by the documentation."""

from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIRECTORY = ROOT / "docs" / "images"


def _cycle_rows(cycle: int) -> list[dict[str, float]]:
    """Return one synthetic but electrochemically plausible CV cycle."""
    outward = [-0.2 + index * 0.04 for index in range(16)]
    return_leg = [0.4 - index * 0.04 for index in range(1, 21)]
    final_leg = [-0.4 + index * 0.04 for index in range(1, 6)]
    potentials = outward + return_leg + final_leg
    rows: list[dict[str, float]] = []
    for point, potential in enumerate(potentials):
        elapsed = cycle * 4.0 + point * 0.1
        anodic = 1.7 * math.exp(-((potential - 0.18) / 0.11) ** 2)
        cathodic = 1.15 * math.exp(-((potential + 0.12) / 0.10) ** 2)
        direction = 1.0 if point < len(outward) else -1.0
        current = 0.18 * potential + anodic - cathodic * (direction < 0) + cycle * 0.08
        rows.append({
            "elapsed_s": elapsed,
            "x_um": 50.0,
            "y_um": 50.0,
            "z_um": 68.4,
            "voltage1_v": potential,
            "voltage2_v": 0.0,
            "current1_na": current,
            "current2_na": current * 0.12,
            "line_number": float(point),
        })
    return rows


def main() -> None:
    """Capture the control map page and analysis CV page without hardware."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryDirectory(prefix="echemtips-docs-") as temporary:
        os.environ["ECHEMTIPS_SETTINGS_PATH"] = str(Path(temporary) / "settings.json")

        from PySide6 import QtWidgets

        from echemtips.analysis_core import AnalysisDataset, CVCycle
        from echemtips.analysis_window import AnalysisWindow
        from echemtips.models import Sample
        from echemtips.qt_common import application_stylesheet, configure_pyqtgraph
        from echemtips.ui import EChemTipsApp, create_application

        IMAGE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        application = create_application()

        control = EChemTipsApp()
        control.poll_timer.stop()
        control.backend.connect()
        control._set_connection_ui(True)
        control.resize(1440, 900)
        control.show_page("Scan hopping + CV")
        page = control.pages["Scan hopping + CV"]
        page.visual_tabs.setCurrentIndex(3)
        parameters = page.parameters()
        page.experiment.params = parameters
        page.experiment.contact_z = {
            (row, column): 67.8 + 0.42 * row + 0.18 * column + 0.12 * math.sin(row + column)
            for row in range(parameters.y_points)
            for column in range(parameters.x_points)
        }
        page.experiment.current_at_potential = {
            (row, column): 1.4 + 0.31 * row + 0.20 * column + 0.15 * math.cos(row - column)
            for row in range(parameters.y_points)
            for column in range(parameters.x_points)
        }
        page._refresh_maps()
        control.instrument_readout.set_sample(Sample(12.4, 65, 65, 68.9, 0.2, 0, 2.11, 0.16))
        control.show()
        application.processEvents()
        if not control.grab().save(str(IMAGE_DIRECTORY / "control-scan-maps.png")):
            raise RuntimeError("Could not save control screenshot")
        control.backend.disconnect()
        control.close()

        configure_pyqtgraph()
        application.setStyleSheet(application_stylesheet())
        analysis = AnalysisWindow()
        analysis.data_folder = Path("/data/echemtips-recordings")
        analysis.refresh_files()
        first, second = _cycle_rows(0), _cycle_rows(1)
        columns = tuple(first[0])
        analysis.dataset = AnalysisDataset(
            Path("20260915_120000_approach_then_cv.csv"),
            columns,
            first + second,
            {
                "experiment": "Approach then CV",
                "started_at": "2026-09-15T12:00:00",
                "recording_schema_version": 2,
                "status": "complete",
                "parameters": {
                    "cv_start_v": -0.2,
                    "cv_vertex1_v": 0.4,
                    "cv_vertex2_v": -0.4,
                    "cycles": 2,
                },
            },
        )
        analysis.cycles = [CVCycle(1, first), CVCycle(2, second)]
        analysis._refresh_all()
        analysis.tabs.setCurrentIndex(1)
        analysis.resize(1440, 900)
        analysis.show()
        application.processEvents()
        if not analysis.grab().save(str(IMAGE_DIRECTORY / "analysis-cvs.png")):
            raise RuntimeError("Could not save analysis screenshot")
        analysis.close()


if __name__ == "__main__":
    main()
