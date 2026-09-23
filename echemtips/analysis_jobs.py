"""Background file loading and provider extraction with Qt-safe results."""
from PySide6 import QtCore
from .analysis_core import AnalysisDataset, extract_cv_cycles
from .analysis_tools import PROVIDERS, cv_selections


class LoadSignals(QtCore.QObject):
    """One terminal result per request, including a stale-request identifier."""
    finished = QtCore.Signal(int, object, str)


class LoadRecording(QtCore.QRunnable):
    """Load outside the event loop; the receiver alone updates UI widgets."""

    def __init__(self, token, path):
        super().__init__()
        self.token, self.path = token, path
        self.signals = LoadSignals()
        self.cancelled = False

    def run(self):
        """Normalize and prepare selectors, reporting errors without GUI calls."""
        try:
            if self.cancelled:
                self.signals.finished.emit(self.token, None, "")
                return
            dataset = AnalysisDataset.load(self.path)
            if self.cancelled:
                self.signals.finished.emit(self.token, None, "")
                return
            cycles = extract_cv_cycles(dataset)
            groups = {key: (cv_selections(dataset, cycles) if key == "cv" else provider.extract(dataset))
                      for key, provider in PROVIDERS.items() if provider.supports(dataset)}
            self.signals.finished.emit(self.token, (dataset, cycles, groups), "")
        except Exception as exc:
            self.signals.finished.emit(self.token, None, f"Could not load {self.path}: {exc}")
