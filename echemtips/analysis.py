"""Public analysis API and command-line entry point."""

from __future__ import annotations

import argparse

from PySide6 import QtWidgets

from .analysis_core import AnalysisDataset, AnalysisError, CVCycle, extract_cv_cycles
from .analysis_window import AnalysisWindow
from .qt_common import application_stylesheet, configure_pyqtgraph

AnalysisApp = AnalysisWindow

__all__ = (
    "AnalysisApp", "AnalysisDataset", "AnalysisError", "AnalysisWindow",
    "CVCycle", "extract_cv_cycles", "main",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze eChemTips current and CV recordings")
    parser.add_argument("recording", nargs="?", help="recording to open")
    parser.add_argument("--smoke-test", action="store_true", help="build and exercise the analysis UI, then exit")
    args = parser.parse_args()
    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    qt_app.setApplicationName("eChemTips Data Analysis")
    qt_app.setStyle("Fusion")
    qt_app.setStyleSheet(application_stylesheet())
    configure_pyqtgraph()
    window = AnalysisWindow(args.recording)
    if args.smoke_test:
        window.resize(1080, 680)
        window.show()
        qt_app.processEvents()
        for tab_index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(tab_index)
            qt_app.processEvents()
        if window.dataset is not None and window.dataset.experiment == "Approach then CV" and not window.cycles:
            raise RuntimeError("Analysis UI did not extract any CV cycles from the supplied recording.")
        window.close()
        print("eChemTips analysis UI smoke test passed")
        return
    window.show()
    raise SystemExit(qt_app.exec())


if __name__ == "__main__":
    main()
