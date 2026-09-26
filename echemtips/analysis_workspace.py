"""Portable analysis sessions and read-only recording opening helpers."""
import json
from pathlib import Path
from PySide6 import QtCore, QtWidgets as Q


class AnalysisWorkspace(QtCore.QObject):
    """Save analysis choices, not copied recordings or executable objects."""
    def __init__(self, window):
        super().__init__(window); self.window = window; self.pending = None
        menu = window.menuBar().addMenu('Workspace')
        menu.addAction('Save analysis session…', self.save)
        menu.addAction('Open analysis session…', self.open)
        window.setAcceptDrops(True); window.installEventFilter(self)
        from .models import SettingsStore
        self.preferences = QtCore.QSettings(str(SettingsStore().path.with_name('analysis-workspace.ini')), QtCore.QSettings.Format.IniFormat)
        geometry = self.preferences.value('geometry')
        if geometry: window.restoreGeometry(geometry)
        for plot in (window.map_panel.map, window.movie_panel.map):
            plot.current_display_unit = SettingsStore().load().current_display_unit

    def controls(self):
        """Allowlist persistent choices; data and output paths are not widgets."""
        w = self.window
        result = {name: getattr(w, name) for name in ('smoothing_enabled', 'smoothing_method', 'smoothing_window', 'smoothing_order', 'cv_current', 'cv_view')}
        for prefix, widget, names in (
            ('explorer', w.explorer, ('provider', 'selection', 'x_signal', 'y_signal')),
            ('map', w.map_panel, ('channel', 'statistic', 'potential', 'direction', 'cycle', 'palette')),
            ('movie', w.movie_panel, ('kind', 'channel', 'cycle', 'leg', 'count', 'stride', 'fps', 'hold', 'colour', 'palette', 'low', 'high', 'excluded'))):
            result.update({prefix + '.' + name: getattr(widget, name) for name in names})
        return result

    def snapshot(self):
        """Return a JSON-compatible session with explicit source identity."""
        w = self.window
        if w.source_dataset is None or w.loading: raise ValueError('Finish loading a recording first.')
        if 'not applied' in w.processing_pending.text(): raise ValueError('Apply processing edits before saving the analysis session.')
        values = {}
        for key, widget in self.controls().items():
            values[key] = widget.currentText() if isinstance(widget, Q.QComboBox) else widget.isChecked() if isinstance(widget, Q.QCheckBox) else widget.text() if isinstance(widget, Q.QLineEdit) else widget.value()
        stat = w.source_dataset.path.stat()
        return dict(schema=1, kind='echemtips-analysis-session', source=str(w.source_dataset.path),
                    source_size=stat.st_size, source_mtime_ns=stat.st_mtime_ns,
                    condition=w.condition_selector.currentData(), controls=values, tab=w.tabs.currentIndex(),
                    bounds=w.explorer.bounds, baseline=w.explorer.baseline)

    def save(self):
        """Write a session atomically, never replacing source data or metadata."""
        w = self.window
        try:
            payload = self.snapshot()
            path, _ = Q.QFileDialog.getSaveFileName(w, 'Save analysis session', str(w.source_dataset.path.with_suffix('.session.json')), 'Session (*.json)')
            if not path: return
            if Path(path).resolve() in (w.source_dataset.path.resolve(), w.source_dataset.path.with_suffix('.json').resolve()): raise ValueError('Choose a different file; source data and metadata are protected.')
            output = QtCore.QSaveFile(path)
            if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly): raise OSError(output.errorString())
            data = json.dumps(payload, indent=2, allow_nan=False).encode()
            if output.write(data) != len(data) or not output.commit(): raise OSError(output.errorString())
            w.statusBar().showMessage('Analysis session saved; source data unchanged')
        except (OSError, ValueError) as exc: Q.QMessageBox.warning(w, 'Session not saved', str(exc))

    def _restore(self, values, smoothing_only=False):
        for key, widget in self.controls().items():
            if key not in values or (smoothing_only and not key.startswith('smoothing_')): continue
            value = values[key]
            if isinstance(widget, Q.QComboBox):
                if widget.findText(str(value)) >= 0: widget.setCurrentText(str(value))
            elif isinstance(widget, Q.QCheckBox): widget.setChecked(bool(value))
            elif isinstance(widget, Q.QLineEdit): widget.setText(str(value))
            else: widget.setValue(value)

    def open(self):
        """Validate and reload a saved session through the normal background loader."""
        path, _ = Q.QFileDialog.getOpenFileName(self.window, 'Open analysis session', '', 'Session (*.json)')
        if not path: return
        try:
            payload = json.loads(Path(path).read_text())
            if payload.get('schema') != 1 or payload.get('kind') != 'echemtips-analysis-session': raise ValueError('Unsupported analysis session.')
            source = Path(payload['source']); stat = source.stat()
            if stat.st_size != payload['source_size'] or stat.st_mtime_ns != payload['source_mtime_ns']:
                raise ValueError('The source recording has changed. Open it normally and review the analysis instead.')
            self._restore(payload['controls'], smoothing_only=True)
            self.pending = payload; self.window.load_recording(source)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.pending = None; Q.QMessageBox.warning(self.window, 'Session not opened', str(exc))

    def loaded(self):
        """Restore dependent selectors after data, and then condition, are ready."""
        if self.pending is None: return
        w, payload = self.window, self.pending
        if w.source_dataset.path != Path(payload['source']): self.pending = None; return
        condition = payload.get('condition')
        if condition is not None and w.condition_selector.currentData() != condition:
            index = w.condition_selector.findData(condition)
            if index >= 0: w.condition_selector.setCurrentIndex(index); return
        self.pending = None
        self._restore(payload['controls'])
        w.explorer.bounds = payload.get('bounds'); w.explorer.baseline = float(payload.get('baseline', 0))
        w.explorer.refresh(); w.tabs.setCurrentIndex(int(payload.get('tab', 0)))
        w.processing_pending.clear()

    def eventFilter(self, watched, event):
        """Accept one supported recording dropped on the analysis window."""
        if event.type() in (QtCore.QEvent.Type.DragEnter, QtCore.QEvent.Type.Drop):
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].isLocalFile():
                path = Path(urls[0].toLocalFile())
                if path.suffix.lower() in ('.csv', '.tsv', '.tdms', '.set'):
                    event.acceptProposedAction()
                    if event.type() == QtCore.QEvent.Type.Drop: self.window.load_recording(path)
                    return True
        return super().eventFilter(watched, event)

    def close(self):
        """Save harmless window geometry separately from analysis sessions."""
        self.preferences.setValue('geometry', self.window.saveGeometry()); self.preferences.sync()
