"""Shared PySide6 and PyQtGraph widgets used by both eChemTips applications."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .host import DisplayBuffer


COLORS = {
    "window": "#f4f7fa",
    "sidebar": "#17324d",
    "sidebar_active": "#27536f",
    "sidebar_text": "#f8fbff",
    "sidebar_muted": "#b9c8d5",
    "panel": "#ffffff",
    "panel_2": "#eef3f7",
    "border": "#d6e0e9",
    "text": "#172535",
    "muted": "#607386",
    "accent": "#12877f",
    "accent_hover": "#0f716b",
    "accent_soft": "#d8efed",
    "blue": "#3578c4",
    "warning": "#a95e06",
    "danger": "#bb3850",
    "success": "#20845d",
    "grid": "#dfe7ee",
}


def application_stylesheet() -> str:
    """Return a high-contrast light theme with predictable widget sizing."""
    c = COLORS
    return f"""
    * {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: {c['text']}; }}
    QMainWindow, QWidget#window, QScrollArea#pageScroll, QStackedWidget {{ background: {c['window']}; }}
    QFrame#topbar {{ background: {c['panel']}; border: 0; border-bottom: 1px solid {c['border']}; }}
    QFrame#sidebar {{ background: {c['sidebar']}; border: 0; }}
    QFrame#card {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
    QFrame#cardBody, QFrame#transparent {{ background: transparent; border: 0; }}
    QLabel#pageTitle {{ font-size: 25px; font-weight: 700; }}
    QLabel#pageDescription {{ color: {c['muted']}; font-size: 13px; }}
    QLabel#cardTitle {{ font-size: 15px; font-weight: 650; }}
    QLabel#cardSubtitle, QLabel#muted {{ color: {c['muted']}; font-size: 12px; }}
    QLabel#brand {{ color: {c['sidebar_text']}; font-size: 18px; font-weight: 700; }}
    QLabel#sidebarMuted {{ color: {c['sidebar_muted']}; font-size: 11px; }}
    QLabel#statusStrong {{ color: {c['accent']}; font-size: 18px; font-weight: 700; }}
    QLabel#readout {{ color: {c['accent']}; font-size: 25px; font-weight: 700; }}
    QPushButton {{
        background: {c['panel_2']}; border: 1px solid {c['border']}; border-radius: 7px;
        min-height: 34px; padding: 3px 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: #e3ebf1; }}
    QPushButton:disabled {{ color: #9aabb9; background: #f4f6f8; }}
    QPushButton[role='primary'] {{ color: white; background: {c['accent']}; border-color: {c['accent']}; }}
    QPushButton[role='primary']:hover {{ background: {c['accent_hover']}; }}
    QPushButton[role='danger'] {{ color: {c['danger']}; background: #fff0f2; border-color: #f0c8d0; }}
    QPushButton[role='nav'] {{
        color: {c['sidebar_muted']}; background: transparent; border: 0; border-radius: 7px;
        min-height: 39px; text-align: left; padding-left: 16px;
    }}
    QPushButton[role='nav']:hover {{ color: white; background: #20445f; }}
    QPushButton[role='nav']:checked {{ color: white; background: {c['sidebar_active']}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {c['panel_2']}; border: 1px solid {c['border']}; border-radius: 6px;
        min-height: 32px; padding: 1px 8px; selection-background-color: {c['accent']};
    }}
    QLineEdit:disabled, QComboBox:disabled {{ color: #96a5b2; background: #f5f7f9; }}
    QComboBox QAbstractItemView {{ background: white; selection-background-color: {c['accent_soft']}; }}
    QCheckBox {{ spacing: 8px; min-height: 26px; }}
    QCheckBox::indicator {{ width: 17px; height: 17px; }}
    QProgressBar {{ border: 0; background: {c['panel_2']}; border-radius: 4px; min-height: 8px; max-height: 8px; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['panel']}; }}
    QTabBar::tab {{ background: {c['panel_2']}; color: {c['muted']}; padding: 9px 16px; margin-right: 2px; }}
    QTabBar::tab:selected {{ background: {c['panel']}; color: {c['text']}; font-weight: 650; }}
    QHeaderView::section {{ background: {c['panel_2']}; padding: 7px; border: 0; border-right: 1px solid {c['border']}; font-weight: 650; }}
    QTableWidget, QTreeWidget, QListWidget, QPlainTextEdit {{ background: white; border: 1px solid {c['border']}; alternate-background-color: #f7f9fb; }}
    QSplitter::handle {{ background: {c['border']}; width: 1px; height: 1px; }}
    QToolTip {{ color: {c['text']}; background: white; border: 1px solid {c['border']}; }}
    """


def button(text: str, slot=None, role: str = "") -> QtWidgets.QPushButton:
    result = QtWidgets.QPushButton(text)
    if role:
        result.setProperty("role", role)
    if slot is not None:
        result.clicked.connect(slot)
    return result


def label(text: str = "", role: str = "", *, word_wrap: bool = False) -> QtWidgets.QLabel:
    result = QtWidgets.QLabel(text)
    if role:
        result.setObjectName(role)
    result.setWordWrap(word_wrap)
    return result


class TextValue:
    """Small compatibility wrapper around QLineEdit."""

    def __init__(self, edit: QtWidgets.QLineEdit) -> None:
        self.edit = edit

    def get(self) -> str:
        return self.edit.text()

    def set(self, value: object) -> None:
        self.edit.setText(str(value))


class Choice(QtWidgets.QComboBox):
    def __init__(self, values: Iterable[str], value: str) -> None:
        super().__init__()
        self.addItems(list(values))
        self.setCurrentText(value)

    def get(self) -> str:
        return self.currentText()

    def set(self, value: str) -> None:
        self.setCurrentText(value)


class Check(QtWidgets.QCheckBox):
    def __init__(self, text: str, value: bool = False) -> None:
        super().__init__(text)
        self.setChecked(value)

    def get(self) -> bool:
        return self.isChecked()

    def set(self, value: bool) -> None:
        self.setChecked(value)


class Field(QtWidgets.QFrame):
    def __init__(self, label_text: str, value: str, unit: str = "") -> None:
        super().__init__()
        self.setObjectName("transparent")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        caption = label(label_text, "muted", word_wrap=True)
        layout.addWidget(caption)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.entry = QtWidgets.QLineEdit(value)
        self.entry.setMinimumWidth(80)
        self.variable = TextValue(self.entry)
        row.addWidget(self.entry, 1)
        self.unit_label = label(unit, "muted")
        self.unit_label.setMinimumWidth(0)
        self.unit_label.setVisible(bool(unit))
        row.addWidget(self.unit_label)
        layout.addLayout(row)

    def float(self) -> float:
        return float(self.entry.text().strip())

    def integer(self) -> int:
        return int(self.entry.text().strip())

    def optional_float(self) -> float | None:
        value = self.entry.text().strip()
        return float(value) if value else None

    def set_unit(self, unit: str) -> None:
        self.unit_label.setText(unit)
        self.unit_label.setVisible(bool(unit))


def add_field(layout: QtWidgets.QGridLayout, field: Field, row: int, column: int, column_span: int = 1) -> Field:
    layout.addWidget(field, row, column, 1, column_span)
    return field


class Card(QtWidgets.QFrame):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 17)
        outer.setSpacing(7)
        outer.addWidget(label(title, "cardTitle"))
        if subtitle:
            outer.addWidget(label(subtitle, "cardSubtitle", word_wrap=True))
        self.body = QtWidgets.QFrame()
        self.body.setObjectName("cardBody")
        outer.addWidget(self.body, 1)


class Plot(QtWidgets.QWidget):
    """Display-buffered PyQtGraph plot; full-rate persistence remains external."""

    def __init__(
        self,
        title: str,
        y_label: str,
        colors: tuple[str, ...],
        max_points: int = 8_000,
        x_label: str = "Elapsed time (s)",
        names: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self.max_points = max(250, max_points)
        self.buffer = DisplayBuffer(len(colors), self.max_points)
        self.series = self.buffer.series
        self.x_values = self.buffer.x
        self.graph = pg.PlotWidget(background=COLORS["panel"])
        self.graph.setTitle(title, color=COLORS["text"], size="11pt")
        self.graph.setLabel("left", y_label, color=COLORS["muted"])
        self.graph.setLabel("bottom", x_label, color=COLORS["muted"])
        self.graph.getAxis("left").enableAutoSIPrefix(False)
        self.graph.getAxis("bottom").enableAutoSIPrefix(False)
        self.graph.showGrid(x=True, y=True, alpha=0.16)
        self.graph.setMouseEnabled(x=True, y=True)
        self.graph.getPlotItem().getViewBox().setDefaultPadding(0.04)
        if names:
            self.graph.addLegend(offset=(8, 8), brush=pg.mkBrush(255, 255, 255, 220))
        self.curves = [
            self.graph.plot([], [], pen=pg.mkPen(color, width=2), name=names[i] if names else None)
            for i, color in enumerate(colors)
        ]
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.graph)
        self.setMinimumHeight(190)

    def clear(self) -> None:
        self.buffer.clear()
        self.redraw()

    def append(self, x: float, *values: float, redraw: bool = True) -> None:
        if self.buffer.append(x, values) and redraw:
            self.redraw()

    def redraw(self) -> None:
        x = np.asarray(self.x_values, dtype=float)
        for curve, values in zip(self.curves, self.series):
            curve.setData(x, np.asarray(values, dtype=float), connect="finite")

    def configure(self, *, height: int | None = None, **_kwargs: object) -> None:
        if height is not None:
            self.setMinimumHeight(height)


class Heatmap(QtWidgets.QWidget):
    """Interactive map with numeric hover values and a visible color scale."""

    def __init__(self, unit: str) -> None:
        super().__init__()
        self.unit = unit
        self.rows = 1
        self.columns = 1
        self.values: dict[tuple[int, int], float] = {}
        self.plot_item = pg.PlotItem()
        self.view = pg.ImageView(view=self.plot_item)
        self.view.ui.roiBtn.hide()
        self.view.ui.menuBtn.hide()
        self.plot_item.setLabel("bottom", "X pixel")
        self.plot_item.setLabel("left", "Y pixel")
        self.view.setMinimumHeight(230)
        self.summary = label("Waiting for contact data", "muted")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.summary)
        self.set_data({}, 1, 1)

    def set_data(self, values: dict[tuple[int, int], float], rows: int, columns: int) -> None:
        self.values = dict(values)
        self.rows = max(1, rows)
        self.columns = max(1, columns)
        data = np.full((self.rows, self.columns), np.nan, dtype=float)
        for (row, column), value in values.items():
            if 0 <= row < self.rows and 0 <= column < self.columns and math.isfinite(value):
                data[row, column] = value
        finite = data[np.isfinite(data)]
        display = data if finite.size else np.zeros_like(data)
        levels = None
        if finite.size:
            low, high = float(finite.min()), float(finite.max())
            if math.isclose(low, high):
                padding = max(abs(low) * 0.01, 1e-9)
                levels = (low - padding, high + padding)
            else:
                levels = (low, high)
            self.summary.setText(f"{low:.4g} to {high:.4g} {self.unit} · {finite.size}/{data.size} pixels")
        else:
            self.summary.setText("Waiting for contact data")
        self.view.setImage(display, autoRange=False, autoLevels=levels is None, levels=levels, axes={"x": 1, "y": 0})
        self.plot_item.getViewBox().setRange(xRange=(-0.5, self.columns - 0.5), yRange=(-0.5, self.rows - 0.5), padding=0.03)


class XYPlot(QtWidgets.QWidget):
    """General multi-series analysis plot."""

    def __init__(self, x_label: str, y_label: str) -> None:
        super().__init__()
        self.x_label = x_label
        self.y_label = y_label
        self.series: list[tuple[str, list[float], list[float], str]] = []
        self.message = "Open a recording to begin"
        self.graph = pg.PlotWidget(background=COLORS["panel"])
        self.graph.showGrid(x=True, y=True, alpha=0.16)
        self.graph.getAxis("left").enableAutoSIPrefix(False)
        self.graph.getAxis("bottom").enableAutoSIPrefix(False)
        self.graph.addLegend(offset=(8, 8), brush=pg.mkBrush(255, 255, 255, 220))
        self.empty = pg.TextItem(self.message, color=COLORS["muted"], anchor=(0.5, 0.5))
        self.graph.addItem(self.empty)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.graph)
        self.setMinimumHeight(190)
        self.redraw()

    def set_data(self, series: Iterable[tuple[str, list[float], list[float], str]]) -> None:
        self.series = list(series)
        self.redraw()

    def set_message(self, message: str) -> None:
        self.series = []
        self.message = message
        self.redraw()

    def redraw(self) -> None:
        self.graph.clear()
        self.graph.addLegend(offset=(8, 8), brush=pg.mkBrush(255, 255, 255, 220))
        self.graph.setLabel("bottom", self.x_label, color=COLORS["muted"])
        self.graph.setLabel("left", self.y_label, color=COLORS["muted"])
        if not self.series:
            self.empty = pg.TextItem(self.message, color=COLORS["muted"], anchor=(0.5, 0.5))
            self.empty.setPos(0, 0)
            self.graph.addItem(self.empty)
            return
        for name, xs, ys, color in self.series:
            count = min(len(xs), len(ys))
            if count <= 0:
                continue
            stride = max(1, math.ceil(count / 12_000))
            x = np.asarray(xs[:count:stride], dtype=float)
            y = np.asarray(ys[:count:stride], dtype=float)
            self.graph.plot(x, y, pen=pg.mkPen(color, width=2), name=name, connect="finite")
        self.graph.enableAutoRange()


def scroll_area(widget: QtWidgets.QWidget, *, minimum_width: int = 0) -> QtWidgets.QScrollArea:
    area = QtWidgets.QScrollArea()
    area.setObjectName("pageScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    if minimum_width:
        area.setMinimumWidth(minimum_width)
    return area


def configure_pyqtgraph() -> None:
    pg.setConfigOptions(antialias=True, foreground=COLORS["text"], background=COLORS["panel"])
