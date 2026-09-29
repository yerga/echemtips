"""Optional operator annotations, separate from verified acquisition settings."""
import json
from PySide6 import QtCore, QtWidgets as Q


FIELDS = {
    'pipette_diameter_um': 'Pipette opening diameter (µm)',
    'electrolyte': 'Electrolyte',
    'electrodes': 'Electrodes',
    'amplifier_filter': 'Amplifier filter',
    'temperature_c': 'Temperature (°C)',
    'humidity_percent': 'Relative humidity (%)',
    'atmosphere': 'Atmosphere',
}


class DetailsStore:
    """Persist defaults and last accepted annotations independently of hardware."""
    def __init__(self, settings_path):
        self.path = settings_path.with_name('experiment-details.json')

    def load(self):
        """Recover optional annotations without making missing files an error."""
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
            return {key: value for key, value in result.items()
                    if key in ('defaults', 'last') and isinstance(value, dict)}
        except (OSError, ValueError, AttributeError):
            return {}

    def save(self, values, *, defaults=False):
        """Atomically remember accepted values; defaults change only explicitly."""
        data = self.load()
        data['last'] = values
        if defaults:
            data['defaults'] = values
        self.path.parent.mkdir(parents=True, exist_ok=True)
        output = QtCore.QSaveFile(str(self.path))
        if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        payload = (json.dumps(data, indent=2) + '\n').encode()
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())


class DetailsDialog(Q.QDialog):
    """One compact start dialog with optional notes and an integrated run review."""
    def __init__(self, parent, values, summary='', parameters='', defaults=False):
        super().__init__(parent)
        self.setWindowTitle('Experiment details defaults' if defaults else 'Review and start experiment')
        self.resize(650, 610)
        layout = Q.QVBoxLayout(self)
        tabs = Q.QTabWidget(); layout.addWidget(tabs)
        form_widget = Q.QWidget(); form = Q.QFormLayout(form_widget)
        hint = Q.QLabel('All fields are optional and operator-entered. Leave unchanged or blank as needed.')
        hint.setWordWrap(True); form.addRow(hint)
        self.barrel = Q.QComboBox(); self.barrel.addItems(['Not specified', 'Single barrel', 'Double barrel'])
        form.addRow('Pipette', self.barrel)
        self.entries = {}
        for key, caption in FIELDS.items():
            entry = Q.QLineEdit(); self.entries[key] = entry
            form.addRow(caption, entry)
        self.entries['electrolyte'].setPlaceholderText('Redox species, supporting electrolyte, solvent, pH…')
        self.entries['amplifier_filter'].setPlaceholderText('e.g. i1: 1 kHz filtered; i2: 100 Hz')
        self.entries['pipette_diameter_um'].setToolTip('Opening diameter, not the droplet footprint. Descriptive only; does not change normalization.')
        self.notes = Q.QPlainTextEdit(); self.notes.setMaximumHeight(90); form.addRow('Notes', self.notes)
        scroll = Q.QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(form_widget)
        tabs.addTab(scroll, 'Experiment details')
        if summary:
            review = Q.QPlainTextEdit(); review.setReadOnly(True)
            review.setPlainText(summary + '\n\nParameter snapshot:\n' + parameters)
            tabs.addTab(review, 'Run review')
            # Hardware safety information remains visible before accepting.
            warning = Q.QLabel(summary); warning.setWordWrap(True)
            warning_scroll = Q.QScrollArea(); warning_scroll.setWidgetResizable(True)
            warning_scroll.setWidget(warning); warning_scroll.setMaximumHeight(130)
            layout.insertWidget(0, warning_scroll)
        self.save_defaults = Q.QCheckBox('Save these values as defaults')
        self.save_defaults.setChecked(defaults); layout.addWidget(self.save_defaults)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(Q.QDialogButtonBox.StandardButton.Ok).setText('Save' if defaults else 'Start experiment')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.barrel.setCurrentText(values.get('pipette', 'Not specified'))
        for key, entry in self.entries.items(): entry.setText(str(values.get(key, '')))
        self.notes.setPlainText(values.get('notes', ''))

    def values(self):
        """Return plain descriptive strings; no annotation changes instrument behavior."""
        return {'pipette': self.barrel.currentText(),
                **{key: entry.text().strip() for key, entry in self.entries.items()},
                'notes': self.notes.toPlainText().strip()}


def request_details(app, summary='', parameters='', *, defaults=False):
    """Review optional metadata before recording; cancellation has no side effects."""
    store = DetailsStore(app.store.path)
    saved = store.load()
    values = saved.get('defaults', {}) if defaults else saved.get('last', saved.get('defaults', {}))
    dialog = DetailsDialog(app, values, summary, parameters, defaults)
    if dialog.exec() != Q.QDialog.DialogCode.Accepted:
        return None
    values = dialog.values()
    try:
        store.save(values, defaults=defaults or dialog.save_defaults.isChecked())
    except OSError as exc:
        Q.QMessageBox.warning(app, 'Could not remember experiment details',
                              f'These values can still be recorded for this run, but could not be saved for next time.\n{exc}')
    return values
