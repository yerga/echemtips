"""App-styled inline editors that preserve settings when collapsed."""
import json
from PySide6 import QtWidgets as Q, QtCore
from .qt_common import Field, Check, label


class OptionalSetup(Q.QWidget):
    """Disclosure header and hidden form using the main application's controls."""

    def __init__(self, page, title):
        super().__init__(page)
        self.setSizePolicy(Q.QSizePolicy.Policy.Expanding, Q.QSizePolicy.Policy.Maximum)
        self.page, self.title = page, title
        self.fields, self.checks = {}, {}
        self._loading = True
        layout = Q.QVBoxLayout(self); layout.setContentsMargins(0, 8, 0, 0); layout.setSpacing(8)
        self.header = Q.QPushButton(self); self.header.setCheckable(True)
        self.header.setAccessibleName(title); layout.addWidget(self.header)
        self.body = Q.QWidget(self); self.body.hide(); layout.addWidget(self.body)
        self.body.setSizePolicy(Q.QSizePolicy.Policy.Expanding, Q.QSizePolicy.Policy.Maximum)
        self.form = Q.QGridLayout(self.body); self.form.setContentsMargins(0,0,0,0)
        self.form.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.form.setHorizontalSpacing(14); self.form.setVerticalSpacing(10)
        self.change = Q.QLineEdit(self); self.change.hide()
        self.header.toggled.connect(self._expanded)

    def _lock_during_run(self):
        self.body.setEnabled(not self.page.app.any_experiment_active)

    def _expanded(self, expanded):
        self._lock_during_run()
        self.body.setVisible(expanded); self._refresh()

    def _field(self, key, caption, default, unit, row, column):
        widget = Field(caption, str(default), unit)
        self.fields[key] = widget; self.form.addWidget(widget, row, column)
        widget.entry.textChanged.connect(self._refresh)
        return widget

    def _check(self, key, caption, row):
        widget = Check(caption, False)
        self.checks[key] = widget; self.form.addWidget(widget, row, 0, 1, 2)
        widget.toggled.connect(self._refresh)
        return widget

    def _note(self, text, row):
        self.form.addWidget(label(text, 'muted', word_wrap=True), row, 0, 1, 2)

    def text(self):
        """Expose the disclosure summary for UI checks and accessibility."""
        return self.header.text()

    def edit(self):
        """Expand or collapse inline fields without opening a dialog."""
        self.header.toggle()

    def _read(self):
        values = dict(self._defaults)
        values.update({k:w.isChecked() for k,w in self.checks.items()})
        for key, widget in self.fields.items():
            try: values[key] = float(widget.entry.text().strip())
            except ValueError:
                if widget.isEnabled(): raise ValueError(f'{widget.caption_text}: enter a number.') from None
        return values

    def _refresh(self, *_):
        if self._loading: return
        self._sync_enabled()
        try: summary = self._summary(self.values)
        except ValueError: summary = 'Check values'
        self.header.setText(f'{"▾" if self.header.isChecked() else "▸"} {self.title} · {summary}')
        self.change.setText(json.dumps({k:w.entry.text() for k,w in self.fields.items()})
                            + str({k:w.isChecked() for k,w in self.checks.items()}) + summary
                            + (str(self.mode.currentIndex()) if hasattr(self, 'mode') else ''))

    @property
    def values(self):
        """Read the current inline draft for normal experiment validation."""
        return self._read()

    def _restore(self, values):
        self._defaults = dict(values); self._loading = True
        for key, widget in self.fields.items(): widget.entry.setText(str(values[key]))
        for key, widget in self.checks.items(): widget.setChecked(values[key])
        self._loading = False; self._refresh()
