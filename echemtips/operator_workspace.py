"""Operator-facing readiness, event history and run review; no device commands."""
from collections import deque
from datetime import datetime
import json
import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets as Q


class OperatorWorkspace(QtCore.QObject):
    """Observe control state without owning acquisition or FPGA execution."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.events = deque(maxlen=2000)
        self.last_sample = None
        self.last_update = None
        self.recovery_required = False
        self.state = Q.QLabel('Disconnected')
        self.state.setAccessibleName('Instrument readiness and recording status')
        app.menuBar().setCornerWidget(self.state)
        menu = app.menuBar().addMenu('Instrument')
        menu.addAction('Event history / support report…', self.history)
        menu.addAction('Measured / commanded position details…', self.readback_details)
        menu.addAction('Show current recording in folder', self.show_recording)
        menu.addAction('Open data folder', lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(app.settings.save_directory).expanduser().resolve()))))
        advanced = menu.addMenu('Advanced controls')
        action = advanced.addAction('End current waypoint', app.end_current_waypoint)
        menu.aboutToShow.connect(lambda: action.setEnabled(app.next_waypoint_button.isEnabled()))
        app.next_waypoint_button.hide()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)
        self.refresh()

    def record(self, message, level='info'):
        """Retain bounded diagnostic history; disk failure never interrupts motion."""
        event = dict(time=datetime.now().astimezone().isoformat(timespec='seconds'), level=level, message=str(message))
        self.events.append(event)
        try:
            path = self.app.store.path.with_name('operator-events.jsonl')
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix('.previous.jsonl'))
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event) + '\n')
        except OSError:
            pass

    def reason(self):
        """Return the current reason a new experiment cannot start."""
        a = self.app
        if not a.backend.connected: return 'Connect the instrument'
        if self.recovery_required: return 'Recovery required: disconnect and reconnect the FPGA'
        if a.any_experiment_active: return 'An experiment is running'
        if a.recorder.active: return 'Finish the active recording'
        return ''

    def refresh(self):
        """Keep controls consistent with execution ownership and readback freshness."""
        a = self.app
        reason = self.reason()
        state = ('Disconnected' if not a.backend.connected else 'Recovery required' if self.recovery_required
                 else 'Paused' if a.any_experiment_active and 'paused' in a.execution_label.text().lower() else 'Running' if a.any_experiment_active else 'Recording' if a.recorder.active else 'Ready')
        recording = a.recorder.output_path
        suffix = (' · Recording: ' + recording.name) if a.recorder.active and recording else ''
        recording_state = f' · REC {a.recorder.sample_count:,} samples' if a.recorder.active else ''
        self.state.setText(f'{a.settings.mode.upper()} · {state}{recording_state}')
        self.state.setToolTip((reason or 'Ready for a new experiment') + suffix)
        self.state.setStyleSheet('padding: 4px 10px; font-weight: bold;' + ('color: #a95e06;' if a.settings.mode == 'Simulation' else ''))
        for page in a.pages.values():
            if hasattr(page, 'experiment_key') and page.experiment_key:
                layout = page.body.layout()
                setup = getattr(page, "setup_panel", None) or (layout.itemAt(0).widget() if layout and layout.count() else None)
                if setup: setup.setEnabled(not page.experiment.active)
                if hasattr(page, 'start_button'):
                    problem = reason
                    if getattr(page, 'combinatorial', False) and not page._recipe_plan:
                        problem = problem or 'Configure the recipe plan first'
                    page.start_button.setEnabled(not problem)
                    page.start_button.setToolTip(problem or 'Start the configured experiment')
        sample = a._sample
        if sample is not None and sample is not self.last_sample:
            self.last_sample, self.last_update = sample, time.monotonic()
        age = time.monotonic() - self.last_update if self.last_update is not None else None
        stale = not a.backend.connected or age is None or age > 2
        a.instrument_readout.setToolTip('No current readback' if age is None else f'Last received {age:.1f} s ago. Values are measured, not commanded.')
        a.instrument_readout.setEnabled(not stale)
        a.instrument_readout.freshness_label.setText('STALE' if stale and age is not None else 'NO DATA' if age is None else 'LIVE')

    def show_recording(self):
        """Open the recording directory without trying to launch an incomplete CSV."""
        path = self.app.recorder.output_path
        if path is None:
            Q.QMessageBox.information(self.app, 'Recording', 'No recording has been created in this session.'); return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.parent.resolve())))
        self.app.toast(f'Recording: {path.name}', 'info')

    def history(self):
        """Open a searchable, copyable report without dismissing stored events."""
        dialog = Q.QDialog(self.app); dialog.setWindowTitle('Operator event history'); dialog.resize(800, 500)
        layout = Q.QVBoxLayout(dialog)
        search = Q.QLineEdit(); search.setPlaceholderText('Filter events…'); layout.addWidget(search)
        text = Q.QPlainTextEdit(); text.setReadOnly(True); layout.addWidget(text)
        def update():
            """Filter in-memory events without modifying the journal."""
            text.setPlainText('\n'.join(f"{e['time']} [{e['level']}] {e['message']}" for e in self.events if search.text().casefold() in e['message'].casefold()))
        search.textChanged.connect(update); update()
        copy = Q.QPushButton('Copy report'); copy.clicked.connect(lambda: Q.QApplication.clipboard().setText(text.toPlainText())); layout.addWidget(copy)
        dialog.exec()

    def readback_details(self):
        """Explain the latest cached readback without issuing a hardware request."""
        sample = self.app._sample
        if sample is None:
            Q.QMessageBox.information(self.app, 'Position readback', 'No acquired samples yet.'); return
        import math
        lines = []
        for axis in 'xyz':
            measured = getattr(sample, axis + '_um')
            commanded = getattr(sample, 'commanded_' + axis + '_um')
            command = f'{commanded:.4g} µm; difference {measured-commanded:+.4g} µm' if math.isfinite(commanded) else 'unavailable in latest sample'
            lines.append(f'{axis.upper()}: measured {measured:.4g} µm; commanded {command}')
        Q.QMessageBox.information(self.app, 'Latest position snapshot', '\n'.join(lines) + '\n\nA difference is not automatically a fault; check calibration and settling. This is a snapshot, not a live control.')

    def review(self, page, params):
        """Require an explicit output/recording review for real-device starts."""
        if self.recovery_required: raise ValueError(self.reason())
        if self.app.settings.mode == 'Simulation': return True
        from dataclasses import asdict
        values = asdict(params)
        summary = f'{page.recording_name}\n{self.app.backend.label} · {self.app.settings.polarity_convention}\nData folder: {self.app.settings.save_directory}\n'
        captions = {'start_z_um': 'Initial Z (µm)', 'end_z_um': 'Approach limit Z (µm)',
                    'feedback_channel': 'Feedback current', 'retract_distance_um': 'Contact-relative retract (µm)',
                    'marker_enabled': 'Orientation marker landing', 'retract_after': 'Retract after measurement'}
        for key, caption in captions.items():
            if key in values: summary += f"\n{caption}: {values[key]}"
        if 'feedback_threshold_na' in values:
            summary += f"\nContact threshold: {values['feedback_threshold_na'] * 1000:g} pA"
        summary += '\n\nFull parameter snapshot is available under Show Details.'
        box = Q.QMessageBox(Q.QMessageBox.Icon.Warning, 'Review hardware experiment', summary, parent=self.app)
        box.setInformativeText('Confirm the wiring, safe travel path and polarity before starting. Emergency stop remains available in the main window.')
        box.setDetailedText(json.dumps(values, indent=2, default=str))
        box.setStandardButtons(Q.QMessageBox.StandardButton.Ok | Q.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(Q.QMessageBox.StandardButton.Cancel)
        return box.exec() == Q.QMessageBox.StandardButton.Ok
