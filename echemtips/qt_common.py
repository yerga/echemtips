"""Shared PySide6 and PyQtGraph widgets used by both eChemTips applications."""

from __future__ import annotations

import math
import re
from html import escape
from bisect import bisect_left
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


def application_stylesheet(font_size_pt: float = 10.0) -> str:
    """Return a high-contrast light theme with predictable widget sizing."""
    c = COLORS
    stylesheet = f"""
    * {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: {c['text']}; }}
    QMainWindow, QWidget#window, QScrollArea#pageScroll, QStackedWidget {{ background: {c['window']}; }}
    QFrame#topbar {{ background: {c['panel']}; border: 0; border-bottom: 1px solid {c['border']}; }}
    QFrame#sidebar {{ background: {c['sidebar']}; border: 0; }}
    QFrame#instrumentStrip {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
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
    QLabel#stripHeading {{ color: {c['muted']}; font-size: 10px; font-weight: 700; }}
    QLabel#stripCaption {{ color: {c['muted']}; font-size: 10px; }}
    QLabel#stripValue {{ color: {c['text']}; font-size: 14px; font-weight: 700; }}
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
    stylesheet = re.sub(r"font-size: (\d+)px", lambda match: f"font-size: {float(match[1]) * font_size_pt / 10:g}px", stylesheet)
    return f"* {{ font-size: {font_size_pt:g}pt; }}\n" + stylesheet


def current_display_scale(mode: str, values: Iterable[float]) -> tuple[float, str]:
    """Choose display units while leaving source currents in nA."""
    if mode == "pA":
        return 1000.0, "pA"
    if mode != "Auto":
        return 1.0, "nA"
    peak = max((abs(value) for value in values if math.isfinite(value)), default=0.0)
    unit = "pA" if mode == "pA" or (mode == "Auto" and 0 < peak < 1) else "nA"
    return (1000.0 if unit == "pA" else 1.0), unit


def button(text: str, slot=None, role: str = "") -> QtWidgets.QPushButton:
    """Create a themed push button and optionally connect its click handler."""
    result = QtWidgets.QPushButton(text)
    if role:
        result.setProperty("role", role)
    if slot is not None:
        result.clicked.connect(slot)
    return result


class WrappedLabel(QtWidgets.QLabel):
    """Reserve enough height for wrapped text inside nested form layouts."""

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Recalculate the text height when the available width changes."""
        super().resizeEvent(event)
        required = self.heightForWidth(self.width())
        if required > 0 and self.minimumHeight() != required:
            self.setMinimumHeight(required)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        """Update wrapped height after a font or stylesheet change."""
        super().changeEvent(event)
        if event.type() in (QtCore.QEvent.Type.FontChange, QtCore.QEvent.Type.StyleChange):
            required = self.heightForWidth(self.width())
            if required > 0:
                self.setMinimumHeight(required)


def label(text: str = "", role: str = "", *, word_wrap: bool = False) -> QtWidgets.QLabel:
    """Create a themed label with an optional object role and wrapping."""
    result = WrappedLabel(text) if word_wrap else QtWidgets.QLabel(text)
    if role:
        result.setObjectName(role)
    result.setWordWrap(word_wrap)
    return result


class TextValue:
    """Small compatibility wrapper around QLineEdit."""

    def __init__(self, edit: QtWidgets.QLineEdit) -> None:
        self.edit = edit

    def get(self) -> str:
        """Return the current edit text."""
        return self.edit.text()

    def set(self, value: object) -> None:
        """Replace the edit text with a string representation."""
        self.edit.setText(str(value))


class Choice(QtWidgets.QComboBox):
    """Combo box with small get/set helpers used by parameter pages."""
    def __init__(self, values: Iterable[str], value: str) -> None:
        super().__init__()
        self.addItems(list(values))
        self.setCurrentText(value)

    def get(self) -> str:
        """Return the selected text."""
        return self.currentText()

    def set(self, value: str) -> None:
        """Select the matching text when present."""
        self.setCurrentText(value)


class Check(QtWidgets.QCheckBox):
    """Check box with explicit boolean get/set helpers."""
    def __init__(self, text: str, value: bool = False) -> None:
        super().__init__(text)
        self.setChecked(value)

    def get(self) -> bool:
        """Return the checked state."""
        return self.isChecked()

    def set(self, value: bool) -> None:
        """Set the checked state."""
        self.setChecked(value)


class Field(QtWidgets.QFrame):
    """Labelled line edit with optional physical-unit suffix and parsers."""
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
        row.addWidget(self.unit_label)
        layout.addLayout(row)
        # Installing the row assigns the field as parent. Showing an unparented
        # unit label first creates a temporary native window on Windows.
        self.unit_label.setVisible(bool(unit))

    def float(self) -> float:
        """Parse the current text as a floating-point value."""
        return float(self.entry.text().strip())

    def integer(self) -> int:
        """Parse the current text as an integer."""
        return int(self.entry.text().strip())

    def optional_float(self) -> float | None:
        """Parse a float, returning ``None`` when the field is blank."""
        value = self.entry.text().strip()
        return float(value) if value else None

    def set_unit(self, unit: str) -> None:
        """Change the suffix and hide it when empty."""
        self.unit_label.setText(unit)
        self.unit_label.setVisible(bool(unit))


def add_field(layout: QtWidgets.QGridLayout, field: Field, row: int, column: int, column_span: int = 1) -> Field:
    """Insert and return a field for compact page construction."""
    layout.addWidget(field, row, column, 1, column_span)
    return field


class InfoButton(QtWidgets.QToolButton):
    """Keyboard-accessible contextual help available on hover or click."""

    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setText("ⓘ")
        self.setAccessibleName(f"{title} help")
        self.setAccessibleDescription(text)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(28, 28)
        self.setToolTip(f'<div style="max-width: 340px; white-space: normal">{escape(text)}</div>')
        self.setWhatsThis(text)
        self._help_title, self._help_text = title, text
        self._help_dialog = None
        self.clicked.connect(self._show_help)

    def _show_help(self) -> None:
        if self._help_dialog is None:
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle(self._help_title)
            layout = QtWidgets.QVBoxLayout(dialog)
            message = label(self._help_text, word_wrap=True)
            message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            message.setMaximumWidth(380)
            layout.addWidget(message)
            close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
            close.rejected.connect(dialog.close)
            layout.addWidget(close)
            self._help_dialog = dialog
        self._help_dialog.show()
        self._help_dialog.raise_()
        self._help_dialog.activateWindow()


class Card(QtWidgets.QFrame):
    """Themed title/subtitle container exposing a child body frame."""
    def __init__(self, title: str, subtitle: str = "", *, help_text: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 17)
        outer.setSpacing(7)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label(title, "cardTitle", word_wrap=True), 1)
        if help_text:
            heading.addWidget(InfoButton(title, help_text, self))
        outer.addLayout(heading)
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
        rolling_window_s: float | None = None,
    ) -> None:
        super().__init__()
        self.max_points = max(250, max_points)
        self.rolling_window_s = rolling_window_s
        self.time_based = "(s)" in x_label or rolling_window_s is not None
        self.title, self.y_label, self.x_label = title, y_label, x_label
        self.current_display_unit = "nA"
        self.font_size_pt = 10.0
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
        """Clear this plot's display buffer and rendered curves."""
        self.buffer.clear()
        self.redraw()

    def append(self, x: float, *values: float, redraw: bool = True) -> None:
        """Append one display point and optionally redraw immediately."""
        if self.buffer.append(x, values) and redraw:
            self.redraw()

    def redraw(self) -> None:
        """Apply rolling-window pruning and update every graph curve."""
        if self.rolling_window_s is not None and self.x_values:
            cutoff = self.x_values[-1] - self.rolling_window_s
            first_visible = bisect_left(self.x_values, cutoff)
            if first_visible:
                del self.x_values[:first_visible]
                for values in self.series:
                    del values[:first_visible]
        x = np.asarray(self.x_values, dtype=float)
        scale, unit = current_display_scale(self.current_display_unit, (value for series in self.series for value in series)) if "(nA)" in self.y_label else (1.0, "")
        y_label = self.y_label.replace("(nA)", f"({unit})") if unit else self.y_label
        if unit and unit != getattr(self, "_rendered_unit", unit):
            self.graph.enableAutoRange(axis=pg.ViewBox.YAxis)
        self._rendered_unit = unit
        self.graph.setLabel("left", y_label, color=COLORS["muted"], **{"font-size": f"{self.font_size_pt:g}pt"})
        for curve, values in zip(self.curves, self.series):
            curve.setData(x, np.asarray(values, dtype=float) * scale, connect="finite")

    def set_display_style(self, unit: str, font_size_pt: float, trace_width_px: float) -> None:
        """Change render units, typography and pens without altering buffered data."""
        self.current_display_unit, self.font_size_pt = unit, font_size_pt
        self.graph.setTitle(self.title, color=COLORS["text"], size=f"{font_size_pt:g}pt")
        self.graph.setLabel("bottom", self.x_label, color=COLORS["muted"], **{"font-size": f"{font_size_pt:g}pt"})
        font = QtGui.QFont(); font.setPointSizeF(font_size_pt)
        for name in ("left", "bottom"):
            self.graph.getAxis(name).setTickFont(font)
        for curve in self.curves:
            pen = pg.mkPen(curve.opts["pen"]); pen.setWidthF(trace_width_px); curve.setPen(pen)
        self.redraw()

    def configure(self, *, height: int | None = None, **_kwargs: object) -> None:
        """Apply compatibility layout options used by experiment pages."""
        if height is not None:
            self.setMinimumHeight(height)


class TimedXYPlot(Plot):
    """X/Y trace whose visible history is bounded by a separate time clock.

    This is used for current-versus-Z approach histories: Z remains the
    horizontal axis while acquisition time decides when old samples leave the
    display. Full-rate persistence is handled independently by DataRecorder.
    """

    def __init__(
        self,
        title: str,
        y_label: str,
        color: str,
        max_points: int,
        x_label: str,
        history_window_s: float = 60.0,
    ) -> None:
        if not math.isfinite(history_window_s) or history_window_s <= 0:
            raise ValueError("History window must be positive.")
        super().__init__(title, y_label, (color,), max_points, x_label)
        self.history_window_s = history_window_s
        self.clock_values: list[float] = []

    def clear(self) -> None:
        """Clear plotted values and the independent acquisition-time clock."""
        self.clock_values.clear()
        super().clear()

    def append_timed(self, clock_s: float, x: float, y: float, *, redraw: bool = True) -> None:
        """Append XY data whose expiry is determined by a separate time value."""
        if not all(math.isfinite(value) for value in (clock_s, x, y)):
            return
        self.clock_values.append(clock_s)
        self.x_values.append(x)
        self.series[0].append(y)
        self._prune_and_compact()
        if redraw:
            self.redraw()

    def add_gap(self, clock_s: float) -> None:
        """Prevent a line joining two separate approaches."""
        if not math.isfinite(clock_s):
            return
        self.clock_values.append(clock_s)
        self.x_values.append(float("nan"))
        self.series[0].append(float("nan"))

    def _prune_and_compact(self) -> None:
        if self.clock_values:
            cutoff = self.clock_values[-1] - self.history_window_s
            first_visible = bisect_left(self.clock_values, cutoff)
            if first_visible:
                del self.clock_values[:first_visible]
                del self.x_values[:first_visible]
                del self.series[0][:first_visible]
        if len(self.clock_values) > self.max_points * 2:
            count = len(self.clock_values)
            indices = [round(index * (count - 1) / (self.max_points - 1)) for index in range(self.max_points)]
            self.clock_values[:] = [self.clock_values[index] for index in indices]
            self.x_values[:] = [self.x_values[index] for index in indices]
            self.series[0][:] = [self.series[0][index] for index in indices]

    def redraw(self) -> None:
        """Prune/compact against time before updating the XY curve."""
        self._prune_and_compact()
        super().redraw()


class ProgramDiagram(QtWidgets.QWidget):
    """Small parameter-linked line profile used to explain a method program."""

    def __init__(self, y_label: str) -> None:
        super().__init__()
        self.font_size_pt = 10.0
        self.graph = pg.PlotWidget(background=COLORS["panel"])
        self.graph.setLabel("left", y_label, color=COLORS["muted"])
        self.graph.getAxis("left").enableAutoSIPrefix(False)
        self.graph.showGrid(y=True, alpha=0.12)
        self.graph.setMouseEnabled(x=False, y=False)
        self.graph.hideButtons()
        self.graph.setMenuEnabled(False)
        self.graph.getViewBox().setDefaultPadding(0.15)
        self.curve = self.graph.plot([], [], pen=pg.mkPen(COLORS["accent"], width=3), symbol="o", symbolSize=7)
        self.labels: list[pg.TextItem] = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.graph)
        self.setFixedHeight(180)
        self.graph.getViewBox().sigResized.connect(self._fit_annotations)

    def _fit_annotations(self) -> None:
        if not self.labels:
            return
        values = [item.pos().y() for item in self.labels]
        low, high = min(values), max(values)
        span = high - low or max(abs(high) * 0.2, 1.0)
        # TextItems use screen pixels, not data units. Reserve their real
        # height above the peak rather than relying on curve auto-ranging.
        height = max(self.graph.getViewBox().height(), 1.0)
        top_pixels = max(item.boundingRect().height() * 1.35 for item in self.labels) + 8
        top_fraction = min(top_pixels / height, 0.65)
        bottom_fraction = 0.1
        extent = span / (1 - top_fraction - bottom_fraction)
        self.graph.setYRange(low - extent * bottom_fraction, high + extent * top_fraction, padding=0)
        self.graph.setXRange(-0.4, max(len(values) - 1, 1) + 0.4, padding=0)

    def set_profile(self, values: list[float], names: list[str], *, stepped: bool = False) -> None:
        """Render labelled ramped or stepped parameter values."""
        for item in self.labels:
            self.graph.removeItem(item)
        self.labels.clear()
        if len(values) != len(names) or not values:
            self.curve.setData([], [])
            return
        if stepped:
            x: list[float] = [0.0]
            y: list[float] = [values[0]]
            for index, value in enumerate(values[1:], 1):
                x.extend((float(index), float(index)))
                y.extend((y[-1], value))
            label_x = [float(index) for index in range(len(values))]
        else:
            x = [float(index) for index in range(len(values))]
            y = values
            label_x = x
        self.curve.setData(x, y)
        self.graph.getAxis("bottom").setTicks([[(position, name) for position, name in zip(label_x, names)]])
        for position, value in zip(label_x, values):
            item = pg.TextItem(f"{value:g}", color=COLORS["text"], anchor=(0.5, 1.35))
            font = QtGui.QFont(); font.setPointSizeF(self.font_size_pt); item.setFont(font)
            item.setPos(position, value)
            self.graph.addItem(item)
            self.labels.append(item)
        self._fit_annotations()


class Heatmap(QtWidgets.QWidget):
    """Stage-coordinate map with square-cell or circular-footprint rendering."""

    def __init__(self, unit: str, quantity: str = "Value") -> None:
        super().__init__()
        self.unit = unit
        self.base_unit = unit
        self.font_size_pt = 10.0
        self.current_display_unit = "nA"
        self.display_scale = 1.0
        self.fixed_limits: tuple[float, float] | None = None
        self.colormap_name = "viridis"
        self._active_colormap_name = self.colormap_name
        self.color_map = pg.colormap.get(self.colormap_name)
        self.quantity = quantity
        self.rows = 1
        self.columns = 1
        self.values: dict[tuple[int, int], float] = {}
        self.x_values = [0.0]
        self.y_values = [0.0]
        self.view_mode = "square"
        self.footprint_diameter_um = 1.0
        self.plot_item = pg.PlotItem()
        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(COLORS["panel"])
        self.view.addItem(self.plot_item, row=0, col=0)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.plot_item.addItem(self.image_item)
        self.footprint_item = pg.ScatterPlotItem(pxMode=False)
        self.plot_item.addItem(self.footprint_item)
        self.plot_item.setLabel("bottom", "X position", units="µm")
        self.plot_item.setLabel("left", "Y position", units="µm")
        self.plot_item.showGrid(x=True, y=True, alpha=0.12)
        self.plot_item.getViewBox().setAspectLocked(True)
        self.plot_item.getViewBox().invertY(False)
        self.color_bar = pg.ColorBarItem(
            values=(0.0, 1.0),
            width=14,
            colorMap=self.color_map,
            label=f"{self.quantity} ({self.unit})",
            interactive=False,
            colorMapMenu=False,
            pen=pg.mkPen(COLORS["text"]),
        )
        self.color_bar.setImageItem(self.image_item, insert_in=self.plot_item)
        # ColorBarItem's constructor labels the left axis, but its active
        # scale axis is the right one. Keep only the live, unit-aware label.
        self.color_bar.getAxis("left").setLabel("")
        self.color_bar.getAxis("left").showLabel(False)
        self.color_bar.axis.enableAutoSIPrefix(False)
        # Keep a modest floor so constrained windows allocate space cleanly;
        # a large minimum makes Qt overlap the footer when the map tab is short.
        self.view.setMinimumHeight(140)
        self.view.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.summary = label("Range — · waiting for data", "muted")
        self.hover = label("Hover a footprint for its position and value", "muted")
        for readout in (self.summary, self.hover):
            readout.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
        self.footer = QtWidgets.QFrame()
        self.footer.setObjectName("mapFooter")
        self.footer.setStyleSheet(
            f"QFrame#mapFooter {{ background: {COLORS['panel']}; "
            f"border-top: 1px solid {COLORS['border']}; }}"
        )
        footer_layout = QtWidgets.QStackedLayout(self.footer)
        footer_layout.setContentsMargins(4, 4, 4, 2)
        footer_layout.addWidget(self.summary)
        footer_layout.addWidget(self.hover)
        footer_layout.setCurrentWidget(self.summary)
        self.footer_stack = footer_layout
        self.footer.setFixedHeight(32)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.footer)
        self.view.installEventFilter(self)
        self.view.scene().sigMouseMoved.connect(self._show_hover_value)
        self.set_data({}, 1, 1)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """Restore the range summary when the pointer leaves the map."""
        if watched is self.view and event.type() == QtCore.QEvent.Type.Leave:
            self.footer_stack.setCurrentWidget(self.summary)
        return super().eventFilter(watched, event)

    def _show_hover_value(self, scene_position: QtCore.QPointF) -> None:
        if not self.plot_item.sceneBoundingRect().contains(scene_position):
            return
        point = self.plot_item.getViewBox().mapSceneToView(scene_position)
        column = min(range(self.columns), key=lambda index: abs(self.x_values[index] - point.x()))
        row = min(range(self.rows), key=lambda index: abs(self.y_values[index] - point.y()))
        value = self.values.get((row, column))
        if value is None or not math.isfinite(value):
            self.hover.setText(f"X {self.x_values[column]:.5g} µm · Y {self.y_values[row]:.5g} µm · no data")
        else:
            self.hover.setText(f"X {self.x_values[column]:.5g} µm · Y {self.y_values[row]:.5g} µm · {value * self.display_scale:.5g} {self.unit}")
        self.footer_stack.setCurrentWidget(self.hover)

    def set_data(
        self,
        values: dict[tuple[int, int], float],
        rows: int,
        columns: int,
        *,
        x_values: Iterable[float] | None = None,
        y_values: Iterable[float] | None = None,
        view_mode: str | None = None,
        footprint_diameter_um: float | None = None,
    ) -> None:
        """Render finite grid values in physical coordinates and update range text."""
        self.values = dict(values)
        if self.colormap_name != self._active_colormap_name:
            self.color_map = pg.colormap.get(self.colormap_name)
            self.color_bar.setColorMap(self.color_map)
            self._active_colormap_name = self.colormap_name
        scale_values = self.fixed_limits if self.fixed_limits is not None else self.values.values()
        self.display_scale, self.unit = current_display_scale(self.current_display_unit, scale_values) if self.base_unit == "nA" else (1.0, self.base_unit)
        self.color_bar.axis.setLabel(f"{self.quantity} ({self.unit})", **{"font-size": f"{self.font_size_pt:g}pt"})
        self.rows = max(1, rows)
        self.columns = max(1, columns)
        xs = list(x_values) if x_values is not None else [float(index) for index in range(self.columns)]
        ys = list(y_values) if y_values is not None else [float(index) for index in range(self.rows)]
        if len(xs) != self.columns or len(ys) != self.rows:
            raise ValueError("Physical map coordinates must match the grid dimensions.")
        self.x_values, self.y_values = xs, ys
        if view_mode is not None:
            if view_mode not in {"square", "circular"}:
                raise ValueError("Map view must be square or circular.")
            self.view_mode = view_mode
        if footprint_diameter_um is not None:
            if not math.isfinite(footprint_diameter_um) or footprint_diameter_um <= 0:
                raise ValueError("Footprint diameter must be positive.")
            self.footprint_diameter_um = footprint_diameter_um
        data = np.full((self.rows, self.columns), np.nan, dtype=float)
        for (row, column), value in values.items():
            if 0 <= row < self.rows and 0 <= column < self.columns and math.isfinite(value):
                data[row, column] = value * self.display_scale
        finite = data[np.isfinite(data)]
        display = data if finite.size else np.zeros_like(data)
        if finite.size:
            low, high = float(finite.min()), float(finite.max())
            if math.isclose(low, high):
                padding = max(abs(low) * 0.01, 1e-9)
                levels = (low - padding, high + padding)
            else:
                levels = (low, high)
            self.summary.setText(f"Range {low:.4g}–{high:.4g} {self.unit} · {finite.size}/{data.size} positions")
        else:
            levels = (0.0, 1.0)
            self.summary.setText("Range — · waiting for data")
            self.hover.setText("Hover a footprint for its position and value")
        if self.fixed_limits is not None:
            levels = tuple(value * self.display_scale for value in self.fixed_limits)
        plot_xs, plot_ys, image = list(xs), list(ys), display
        if plot_xs[-1] < plot_xs[0]:
            plot_xs.reverse(); image = np.fliplr(image)
        if plot_ys[-1] < plot_ys[0]:
            plot_ys.reverse(); image = np.flipud(image)
        dx = abs(plot_xs[1] - plot_xs[0]) if len(plot_xs) > 1 else self.footprint_diameter_um
        dy = abs(plot_ys[1] - plot_ys[0]) if len(plot_ys) > 1 else self.footprint_diameter_um
        dx = dx or self.footprint_diameter_um; dy = dy or self.footprint_diameter_um
        self.image_item.setImage(image, autoLevels=False, levels=levels)
        self.image_item.setRect(QtCore.QRectF(plot_xs[0] - dx / 2, plot_ys[0] - dy / 2,
                                             plot_xs[-1] - plot_xs[0] + dx, plot_ys[-1] - plot_ys[0] + dy))
        color_map = self.color_map
        span = levels[1] - levels[0]
        spots = []
        for (row, column), value in self.values.items():
            if 0 <= row < self.rows and 0 <= column < self.columns and math.isfinite(value):
                normalized = min(1.0, max(0.0, (value * self.display_scale - levels[0]) / span)) if span else 0.5
                spots.append({"pos": (xs[column], ys[row]), "size": self.footprint_diameter_um,
                              "brush": pg.mkBrush(color_map.map(normalized, mode="qcolor")),
                              "pen": pg.mkPen(COLORS["border"], width=0.7)})
        self.footprint_item.setData(spots)
        self.image_item.setVisible(self.view_mode == "square")
        self.footprint_item.setVisible(self.view_mode == "circular")
        self.color_bar.setLevels(levels)
        radius_x = self.footprint_diameter_um / 2 if self.view_mode == "circular" else dx / 2
        radius_y = self.footprint_diameter_um / 2 if self.view_mode == "circular" else dy / 2
        self.plot_item.getViewBox().setRange(
            xRange=(min(xs) - radius_x, max(xs) + radius_x),
            yRange=(min(ys) - radius_y, max(ys) + radius_y), padding=0.04,
        )


class XYPlot(QtWidgets.QWidget):
    """Interactive multi-series plot for already collected analysis data."""

    def __init__(self, x_label: str, y_label: str) -> None:
        super().__init__()
        self.x_label = x_label
        self.y_label = y_label
        self.current_display_unit = "nA"
        self.font_size_pt = 10.0
        self.trace_width_px = 2.0
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
        """Replace all named XY curves with the supplied series."""
        self.series = list(series)
        self.redraw()

    def set_message(self, message: str) -> None:
        """Clear curves and show a centered explanatory message."""
        self.series = []
        self.message = message
        self.redraw()

    def redraw(self) -> None:
        """Rebuild legend and curves from the most recently supplied series."""
        self.graph.clear()
        self.graph.addLegend(offset=(8, 8), brush=pg.mkBrush(255, 255, 255, 220))
        scale, unit = current_display_scale(self.current_display_unit, (value for _, _, ys, _ in self.series for value in ys)) if "(nA)" in self.y_label else (1.0, "")
        self.graph.setLabel("bottom", self.x_label, color=COLORS["muted"], **{"font-size": f"{self.font_size_pt:g}pt"})
        self.graph.setLabel("left", self.y_label.replace("(nA)", f"({unit})") if unit else self.y_label, color=COLORS["muted"], **{"font-size": f"{self.font_size_pt:g}pt"})
        font = QtGui.QFont(); font.setPointSizeF(self.font_size_pt)
        for axis in ("left", "bottom"):
            self.graph.getAxis(axis).setTickFont(font)
        if not self.series:
            self.empty = pg.TextItem(self.message, color=COLORS["muted"], anchor=(0.5, 0.5))
            self.empty.setPos(0, 0)
            self.graph.addItem(self.empty)
            return
        for name, xs, ys, color in self.series:
            count = min(len(xs), len(ys))
            if count <= 0:
                continue
            from .analysis_display import envelope_indices
            full_x, full_y = np.asarray(xs[:count], dtype=float), np.asarray(ys[:count], dtype=float)
            # Bound the total overlay work as well as each individual curve.
            indices = envelope_indices(full_x, full_y, limit=max(250, min(12000, 24000 // len(self.series))))
            x, y = full_x[indices], full_y[indices]
            curve = self.graph.plot(x, y * scale, pen=pg.mkPen(color, width=self.trace_width_px),
                                    name=name, connect="finite", antialias=False)
            # Dense, noisy traces make Qt's thick antialiased drawPath operation
            # take seconds, even for a small file. Segments preserve the data,
            # gaps and requested pen width without that expensive path stroking.
            curve.curve.setSegmentedLineMode("on")
        self.graph.enableAutoRange()


class PlotPanel(QtWidgets.QScrollArea):
    """Keep plot cards readable, stacking and scrolling on narrow displays."""

    def __init__(self, content: QtWidgets.QWidget) -> None:
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(content)
        layout = content.layout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._cards = [layout.itemAt(i).widget() for i in range(layout.count())
                       if layout.itemAt(i).widget() is not None]
        for card in self._cards:
            card.setMinimumHeight(360 if card.findChildren(Heatmap) else 290)
        self._reflow()

    def _reflow(self) -> None:
        layout = self.widget().layout()
        horizontal = self.viewport().width() >= 880 and len(self._cards) == 2
        if isinstance(layout, QtWidgets.QBoxLayout):
            layout.setDirection(QtWidgets.QBoxLayout.Direction.LeftToRight if horizontal
                                else QtWidgets.QBoxLayout.Direction.TopToBottom)
        heights = [card.minimumHeight() for card in self._cards]
        height = (max(heights, default=0) if horizontal else
                  sum(heights) + max(0, len(heights) - 1) * layout.spacing())
        self.widget().setMinimumHeight(height)
        self.widget().setMaximumHeight(max(height, self.viewport().height()))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Adapt the plot arrangement to the available viewport width."""
        super().resizeEvent(event)
        self._reflow()


def scroll_area(widget: QtWidgets.QWidget, *, minimum_width: int = 0) -> QtWidgets.QScrollArea:
    """Wrap a resizable widget in a frameless vertical scroll area."""
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
    """Apply process-wide foreground, background, and antialias defaults."""
    pg.setConfigOptions(antialias=True, foreground=COLORS["text"], background=COLORS["panel"])
