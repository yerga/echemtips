"""Ordered, editable scan-rate list for surface CV series."""
import math
from PySide6 import QtCore, QtWidgets


class ScanRateEditor(QtWidgets.QWidget):
    """Edit one rate per row with explicit add, remove and order controls."""
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QtWidgets.QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels(['Scan rate (V/s) — execution order'])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(165)
        self.table.setMaximumHeight(220)
        layout.addWidget(self.table)
        actions = QtWidgets.QHBoxLayout()
        for text, slot in [('Add rate', self.add_rate), ('Remove', self.remove_rate),
                           ('Move up', lambda: self.move_rate(-1)), ('Move down', lambda: self.move_rate(1))]:
            control = QtWidgets.QPushButton(text)
            control.clicked.connect(slot)
            actions.addWidget(control)
        layout.addLayout(actions)
        self.set_rates([0.1, 0.25, 0.5, 1.0])
        self.table.itemChanged.connect(lambda *_: self.changed.emit())

    def values(self) -> list[float]:
        """Parse finite positive values without sorting or dropping duplicates."""
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            try:
                value = float(item.text()) if item else float('nan')
            except ValueError:
                raise ValueError(f'Scan rate {row + 1}: enter a positive number in V/s.') from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'Scan rate {row + 1} must be finite and greater than zero.')
            result.append(value)
        if not result:
            raise ValueError('Add at least one scan rate.')
        return result

    def set_rates(self, values):
        """Replace the editable list while retaining the supplied order."""
        with QtCore.QSignalBlocker(self.table):
            self.table.setRowCount(len(values))
            for row, value in enumerate(values):
                self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(f'{value:g}'))
        self.changed.emit()

    def add_rate(self):
        """Append an editable row, duplicating the last valid rate if possible."""
        if self.table.rowCount() >= 1000:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        previous = self.table.item(row - 1, 0) if row else None
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(previous.text() if previous else '0.1'))
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def remove_rate(self):
        """Remove the selected row while keeping at least one rate."""
        row = self.table.currentRow()
        if row >= 0 and self.table.rowCount() > 1:
            self.table.removeRow(row)
            self.changed.emit()

    def move_rate(self, direction):
        """Move the selected rate up or down without changing its value."""
        row = self.table.currentRow()
        target = row + direction
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        with QtCore.QSignalBlocker(self.table):
            first, second = self.table.takeItem(row, 0), self.table.takeItem(target, 0)
            self.table.setItem(row, 0, second)
            self.table.setItem(target, 0, first)
            self.table.setCurrentCell(target, 0)
        self.changed.emit()
