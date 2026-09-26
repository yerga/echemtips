"""Export-only unit choices, independent of acquisition and reference conversion."""
import re
from PySide6 import QtCore, QtWidgets as Q

UNIT_FAMILIES = (
    {'A': 1., 'mA': 1e-3, 'µA': 1e-6, 'nA': 1e-9, 'pA': 1e-12, 'fA': 1e-15},
    {'V': 1., 'mV': 1e-3, 'µV': 1e-6},
    {'m': 1., 'mm': 1e-3, 'µm': 1e-6, 'nm': 1e-9},
    {'min': 60., 's': 1., 'ms': 1e-3, 'µs': 1e-6},
)
_PATTERN = re.compile(r'\((A|mA|µA|nA|pA|fA|V|mV|µV|m|mm|µm|nm|min|s|ms|µs)(?=\s|\))')


def label_unit(text):
    """Recognize a physical unit without interpreting reference names or prose."""
    match = _PATTERN.search(text)
    return match.group(1) if match else None


def unit_factor(source, target):
    """Return a multiplicative conversion, rejecting incompatible dimensions."""
    for family in UNIT_FAMILIES:
        if source in family and target in family:
            return family[source] / family[target]
    raise ValueError(f'Cannot convert {source} to {target}.')


def replace_unit(text, target):
    """Update unit notation while preserving edited captions and reference labels."""
    match = _PATTERN.search(text)
    if match:
        return text[:match.start(1)] + target + text[match.end(1):]
    return f'{text} ({target})'.strip()


class FigureUnitControls(QtCore.QObject):
    """Rescale axis and colour-bar units relative to an immutable figure snapshot."""
    def __init__(self, dialog, form):
        super().__init__(dialog)
        self.dialog = dialog
        self.selectors, self.sources, self.previous = {}, {}, {}
        for key, caption in (('xlabel', 'X units'), ('ylabel', 'Y units'), ('quantity', 'Colour-bar units')):
            text = getattr(dialog.data, key)
            source = label_unit(text)
            if not source or (key == 'quantity' and dialog.data.series):
                continue
            selector = Q.QComboBox()
            family = next(f for f in UNIT_FAMILIES if source in f)
            selector.addItems(family); selector.setCurrentText(source)
            selector.setToolTip('Rescale exported values and labels only; original data are unchanged.')
            self.sources[key] = source; self.previous[key] = source; self.selectors[key] = selector
            form.addRow(caption, selector)
            selector.currentTextChanged.connect(lambda target, key=key: self._changed(key, target))

    def _changed(self, key, target):
        dialog = self.dialog
        dialog.fields[key].setText(replace_unit(dialog.fields[key].text(), target))
        if key == 'quantity':
            ratio = unit_factor(self.previous[key], target)
            for control in (dialog.low, dialog.high):
                control.setValue(control.value() * ratio)
        self.previous[key] = target
        # A previously rendered preview must not appear to reflect new units.
        dialog.preview.clear(); dialog.preview.setText('Units changed — select Preview to update.')

    def options(self):
        """Record source units, output units and factors in the figure sidecar."""
        return {key: {'source': self.sources[key], 'target': selector.currentText(),
                      'factor': unit_factor(self.sources[key], selector.currentText())}
                for key, selector in self.selectors.items()}
