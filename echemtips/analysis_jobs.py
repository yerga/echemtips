"""Background file loading and provider extraction with Qt-safe results."""
from PySide6 import QtCore
from .analysis_core import AnalysisDataset, extract_cv_cycles
from .analysis_tools import PROVIDERS, cv_selections
from .analysis_processing import smooth_currents


class LoadSignals(QtCore.QObject):
    """One terminal result per request, including a stale-request identifier."""
    finished = QtCore.Signal(int, object, str)


class LoadRecording(QtCore.QRunnable):
    """Load outside the event loop; the receiver alone updates UI widgets."""

    def __init__(self, token, path, *, source=None, smoothing_window=1,
                 smoothing_method="savitzky_golay", polynomial_order=2, condition=None, reference_config=None, area_config=None):
        super().__init__()
        self.token, self.path = token, path
        self.signals = LoadSignals()
        self.cancelled = False
        self.condition = condition
        self.source = source
        self.reprocessing = source is not None
        self.reference_config = dict(reference_config or {})
        self.area_config = dict(area_config or {})
        self.area_results = []
        self.smoothing_window = smoothing_window
        self.smoothing_method, self.polynomial_order = smoothing_method, polynomial_order

    def run(self):
        """Normalize and prepare selectors, reporting errors without GUI calls."""
        try:
            if self.cancelled:
                self.signals.finished.emit(self.token, None, "")
                return
            dataset = self.source if self.source is not None else AnalysisDataset.load(self.path)
            self.source = dataset
            from .analysis_conditions import condition_dataset
            dataset = condition_dataset(dataset, self.condition)
            self.area_source = dataset
            from .analysis_area import RetractionConfig, estimate_landings, normalize_dataset, attach_diameters
            if self.area_config.get('diagnostics_enabled'):
                self.area_results = estimate_landings(dataset, RetractionConfig(**self.area_config.get('detector', {})),
                                                     cancelled=lambda: self.cancelled)
            from .analysis_reference import convert_dataset
            source_cycles = extract_cv_cycles(dataset) if self.reference_config.get('enabled') else None
            dataset = convert_dataset(dataset, self.reference_config)
            if self.cancelled:
                self.signals.finished.emit(self.token, None, "")
                return
            cycles = extract_cv_cycles(dataset)
            self.original_cycles = cycles
            self.model_cycles = source_cycles if source_cycles is not None else cycles
            import numpy as np
            dt = np.diff(dataset.column('elapsed_s')[:10001])
            dt = dt[np.isfinite(dt) & (dt > 0)]
            self.sample_interval_s = float(np.median(dt)) if len(dt) else None
            self.irregular_sampling = bool(len(dt) and np.any(np.abs(dt - self.sample_interval_s) > self.sample_interval_s * 0.05))
            if self.smoothing_window > 1:
                dataset = smooth_currents(dataset, self.smoothing_window, cycles,
                                          method=self.smoothing_method, polynomial_order=self.polynomial_order)
                cycles = extract_cv_cycles(dataset)
            dataset = attach_diameters(dataset, self.area_results)
            if self.area_results:
                dataset.metadata = {**dataset.metadata, 'analysis_retraction_config': self.area_config.get('detector')}
            dataset = normalize_dataset(dataset, self.area_config, self.area_results)
            if self.area_results or self.area_config.get('mode', 'none') != 'none':
                cycles = extract_cv_cycles(dataset)
            groups = {key: (cv_selections(dataset, cycles) if key == "cv" else provider.extract(dataset))
                      for key, provider in PROVIDERS.items() if provider.supports(dataset)}
            self.signals.finished.emit(self.token, (dataset, cycles, groups), "")
        except Exception as exc:
            self.signals.finished.emit(self.token, None, f"Could not load {self.path}: {exc}")
