"""Reusable full-resolution analysis views and read-only table adapters."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import numpy as np
from PySide6 import QtCore, QtWidgets
from .analysis_tools import PROVIDERS, SIGNALS, signal_label, measure, hop_map, potential_map
from .analysis_core import AnalysisError
from .qt_common import COLORS, XYPlot, Heatmap, button, label


class RecordingTableModel(QtCore.QAbstractTableModel):
    """Virtual table: allocate cells only as Qt requests them, not per sample."""
    def __init__(self, dataset, parent=None):
        super().__init__(parent)
        self.dataset = dataset

    def rowCount(self, parent=QtCore.QModelIndex()):
        """Expose every source row."""
        return 0 if parent.isValid() else len(self.dataset.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        """Expose every source column."""
        return 0 if parent.isValid() else len(self.dataset.columns)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        """Format requested cells without changing source precision."""
        if index.isValid() and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return f"{self.dataset.rows.matrix[index.row(), index.column()]:.9g}"

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        """Show native column names or one-based row numbers."""
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self.dataset.columns[section] if orientation == QtCore.Qt.Orientation.Horizontal else str(section + 1)


def export_result(parent, dataset, rows, columns, recipe, suffix):
    """Write a derived CSV and provenance sidecar, protecting original recordings."""
    chosen, _ = QtWidgets.QFileDialog.getSaveFileName(parent, "Export analysis",
        str(dataset.path.with_name(dataset.path.stem + suffix + ".csv")), "CSV (*.csv)")
    if not chosen:
        return
    target = Path(chosen).with_suffix(".csv").resolve()
    if target == dataset.path.resolve() or target.with_suffix(".json") == dataset.path.with_suffix(".json").resolve():
        QtWidgets.QMessageBox.warning(parent, "Source protected", "Choose a different name; original data cannot be replaced.")
        return
    sidecar = target.with_suffix(".json")
    if sidecar.exists() and QtWidgets.QMessageBox.question(parent, "Replace metadata?", f"Replace {sidecar.name}?") != QtWidgets.QMessageBox.StandardButton.Yes:
        return
    try:
        stat = dataset.path.stat()
        payload = json.dumps({"analysis_schema": 1, "source": str(dataset.path),
            "source_size_bytes": stat.st_size, "source_mtime_ns": stat.st_mtime_ns,
            "source_status": dataset.metadata.get("status", "unknown"),
            "processing": dataset.metadata.get("analysis_processing", {"method": "none"}),
            "analysis": recipe}, indent=2, allow_nan=False)
        with target.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream); writer.writerow(columns); writer.writerows(rows)
        sidecar.write_text(payload + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        QtWidgets.QMessageBox.warning(parent, "Export incomplete", f"Check destination files: {exc}")
        return
    QtWidgets.QMessageBox.information(parent, "Export complete", f"Saved CSV and analysis JSON:\n{target}")


class ExplorerPanel(QtWidgets.QWidget):
    """Provider-driven channel exploration, time selection and quantitative analysis."""
    def __init__(self):
        super().__init__()
        self.dataset, self.result = None, None
        self.groups = {}
        self.reference = None
        self.bounds, self.baseline = None, 0.0
        layout = QtWidgets.QVBoxLayout(self)
        grid = QtWidgets.QGridLayout()
        self.provider, self.selection = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.x_signal, self.y_signal = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for combo in (self.provider, self.selection, self.x_signal, self.y_signal):
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        for i, (caption, widget) in enumerate((("Data scope", self.provider), ("Selection", self.selection),
                                              ("X axis", self.x_signal), ("Y axis", self.y_signal))):
            grid.addWidget(label(caption, "muted"), i // 2 * 2, i % 2)
            grid.addWidget(widget, i // 2 * 2 + 1, i % 2)
            grid.setColumnStretch(i % 2, 1)
        layout.addLayout(grid)
        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(button("Time range / baseline…", self._edit_range))
        actions.addWidget(button("Reset analysis", self._reset))
        compare = QtWidgets.QPushButton("Compare…")
        menu = QtWidgets.QMenu(compare)
        menu.addAction("Pin this trace as reference", self._pin_reference)
        menu.addAction("Clear reference", self._clear_reference)
        compare.setMenu(menu)
        actions.addWidget(compare)
        actions.addStretch(1)
        self.export_button = button("Export selection…", self._export)
        actions.addWidget(self.export_button); layout.addLayout(actions)
        self.scope = label("Open a recording to begin", "muted", word_wrap=True)
        layout.addWidget(self.scope)
        self.plot = XYPlot("Elapsed time (s)", "Current (nA)")
        layout.addWidget(self.plot, 1)
        self.summary = label("", "muted", word_wrap=True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.selection.currentIndexChanged.connect(self.refresh)
        self.x_signal.currentIndexChanged.connect(self.refresh)
        self.y_signal.currentIndexChanged.connect(self._reset)

    def set_dataset(self, dataset, groups):
        """Expose applicable providers and all numeric channels, including future ones."""
        self.dataset, self.groups = dataset, groups
        self.bounds, self.baseline = None, 0.0
        combos = (self.provider, self.selection, self.x_signal, self.y_signal)
        for combo in combos: combo.blockSignals(True); combo.clear()
        for key, subsets in groups.items():
            if subsets: self.provider.addItem(PROVIDERS[key].title, key)
        for column in dataset.columns:
            self.x_signal.addItem(signal_label(column), column)
            self.y_signal.addItem(signal_label(column), column)
        self.x_signal.setCurrentIndex(self.x_signal.findData("elapsed_s"))
        preferred = next((c for c in ("current1_na", "z_um", "voltage1_v") if c in dataset.columns), dataset.columns[-1])
        self.y_signal.setCurrentIndex(self.y_signal.findData(preferred))
        for combo in combos: combo.blockSignals(False)
        self._provider_changed()

    def sizeHint(self):
        """Avoid a plotting widget's oversized default stretching scroll contents."""
        return QtCore.QSize(640, 510)

    def heightForWidth(self, width):
        """Prefer the readable minimum over the plot's 480-pixel size hint."""
        return max(510, self.minimumSizeHint().height())

    def _provider_changed(self):
        self.selection.blockSignals(True); self.selection.clear()
        subsets = self.groups.get(self.provider.currentData(), [])
        self.selection.addItems(s.label for s in subsets)
        self.selection.blockSignals(False)
        if subsets:
            for combo in (self.x_signal, self.y_signal):
                previous = combo.currentData()
                combo.blockSignals(True); combo.clear()
                for column in subsets[0].rows.columns: combo.addItem(signal_label(column), column)
                combo.setCurrentIndex(max(0, combo.findData(previous)))
                combo.blockSignals(False)
        self.bounds = None
        self.refresh()

    def refresh(self, *_args):
        """Calculate on full-resolution samples and update the display separately."""
        self.result = None
        if self.dataset is None or self.selection.currentIndex() < 0: return
        subset = self.groups[self.provider.currentData()][self.selection.currentIndex()]
        xcol, ycol = self.x_signal.currentData(), self.y_signal.currentData()
        try:
            x, y, result = measure(subset, xcol, ycol, self.bounds, self.baseline)
        except (AnalysisError, ValueError) as exc:
            self.plot.set_message(str(exc)); self.summary.setText(str(exc)); self.export_button.setEnabled(False)
            return
        self.result = x, y, result
        self.export_button.setEnabled(True)
        self.plot.x_label, self.plot.y_label = signal_label(xcol), signal_label(ycol)
        series = [(subset.label, x, y, COLORS["accent"])]
        matching_reference = self.reference is not None and self.reference[0:2] == (xcol, ycol)
        if matching_reference:
            _xcol, _ycol, ref_x, ref_y, ref_name = self.reference
            series.append((ref_name, ref_x, ref_y, COLORS["warning"]))
        self.plot.set_data(series)
        self.scope.setText(subset.scope + (f" · {self.bounds[0]:g}–{self.bounds[1]:g} s" if self.bounds else " · all times") +
                           f" · baseline: {self.baseline:g} in native Y units")
        unit = SIGNALS.get(ycol, ("", ""))[1]
        text = (f"{result['samples']:,} valid samples · Mean {result['mean']:.5g} {unit} · SD {result['std']:.4g} {unit}\n"
                f"Min {result['minimum']:.5g} at X={result['x_at_minimum']:.5g} · Max {result['maximum']:.5g} at X={result['x_at_maximum']:.5g}")
        if "charge_nc" in result:
            text += f"\nSigned charge {result['charge_nc']:.6g} nC over {result['integrated_duration_s']:.5g} s (∫i dt)"
        if result["invalid_samples"]: text += f" · {result['invalid_samples']} invalid samples; gaps excluded"
        if self.reference is not None:
            text += "\nReference overlay only; statistics and export describe the active trace." if matching_reference else "\nReference hidden: choose matching X/Y channels to compare."
        self.summary.setText(text)

    def _pin_reference(self):
        if self.result is not None:
            x, y, result = self.result
            self.reference = (result["x_column"], result["y_column"], x.copy(), y.copy(),
                              f"Reference: {self.dataset.path.stem} · {result['selection']} · "
                              + (f"{self.dataset.metadata['analysis_processing']['method']} · "
                                 f"{self.dataset.metadata['analysis_processing']['window_samples']} samples"
                                 + (f" · order {self.dataset.metadata['analysis_processing']['polynomial_order']}"
                                    if 'polynomial_order' in self.dataset.metadata['analysis_processing'] else "")
                                 if self.dataset.metadata.get('analysis_processing') else "original"))
            self.refresh()

    def _clear_reference(self):
        self.reference = None
        self.refresh()

    def _edit_range(self):
        if self.dataset is None: return
        dialog = QtWidgets.QDialog(self); dialog.setWindowTitle("Time range and baseline")
        form = QtWidgets.QFormLayout(dialog)
        low, high, baseline = QtWidgets.QLineEdit(), QtWidgets.QLineEdit(), QtWidgets.QLineEdit(str(self.baseline))
        if self.bounds: low.setText(str(self.bounds[0])); high.setText(str(self.bounds[1]))
        form.addRow("Start time (s)", low); form.addRow("End time (s)", high)
        form.addRow("Constant baseline (native Y unit)", baseline)
        form.addRow(label("Use recorded elapsed times. Leave both limits blank for all times. Source samples are unchanged.", word_wrap=True))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted: return
        try:
            bounds = None if not low.text().strip() and not high.text().strip() else (float(low.text()), float(high.text()))
            value = float(baseline.text())
            if not np.isfinite(value) or bounds is not None and (not np.isfinite(bounds).all() or bounds[0] > bounds[1]): raise ValueError
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Invalid values", "Enter finite values, with start ≤ end, or leave both time limits blank.")
            return
        self.bounds, self.baseline = bounds, value
        self.refresh()

    def _reset(self, *_args):
        self.bounds, self.baseline = None, 0.0
        self.refresh()

    def _export(self):
        if self.result is None: return
        x, y, result = self.result
        export_result(self, self.dataset, zip(x, y), (result["x_column"], result["y_column"] + "_processed"), result, "_analysis")


class MapPanel(QtWidgets.QWidget):
    """Physical hop statistics and direction-specific CV potential maps."""
    hop_selected = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        self.dataset, self.points, self.cv_groups = None, [], []
        self.hop_groups = []
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QGridLayout()
        self.channel, self.statistic, self.palette = QtWidgets.QComboBox(), QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.statistic.addItems(("Mean", "Minimum", "Maximum", "Std deviation", "CV at potential"))
        self.palette.addItems(("viridis", "plasma", "cividis", "inferno"))
        self.potential = QtWidgets.QDoubleSpinBox(); self.potential.setRange(-100, 100); self.potential.setDecimals(4); self.potential.setSuffix(" V")
        self.potential.setKeyboardTracking(False)
        self.direction = QtWidgets.QComboBox(); self.direction.addItems(("Increasing E", "Decreasing E"))
        controls.addWidget(self.channel, 0, 0); controls.addWidget(self.statistic, 0, 1); controls.addWidget(self.palette, 0, 2)
        controls.addWidget(self.potential, 1, 0); controls.addWidget(self.direction, 1, 1)
        controls.addWidget(button("Export map…", self._export), 1, 2)
        layout.addLayout(controls)
        self.notice = label("Open a scan recording with physical grid metadata.", "muted", word_wrap=True)
        layout.addWidget(self.notice)
        self.map = Heatmap("nA", "Whole-hop mean"); layout.addWidget(self.map, 1)
        for combo in (self.channel, self.statistic, self.palette, self.direction): combo.currentIndexChanged.connect(self.refresh)
        self.potential.valueChanged.connect(self.refresh)
        self.map.view.scene().sigMouseClicked.connect(self._clicked)

    def set_dataset(self, dataset, cv_groups=(), hop_groups=()):
        """Discover numeric channels and use prepared CV subsets if available."""
        self.dataset, self.cv_groups = dataset, cv_groups
        self.hop_groups = hop_groups
        self.channel.blockSignals(True); self.channel.clear()
        for column in dataset.columns:
            if column not in {"elapsed_s", "scan_pixel", "line_number", "feedback_type"}:
                self.channel.addItem(signal_label(column), column)
        self.channel.setCurrentIndex(max(0, self.channel.findData("current1_na")))
        self.channel.blockSignals(False)
        self.refresh()

    def refresh(self, *_args):
        """Render visited physical cells; never fill missing samples with zero."""
        self.points = []
        cv = self.statistic.currentText() == "CV at potential"
        self.potential.setVisible(cv); self.direction.setVisible(cv)
        if self.dataset is None: return
        try:
            if "scan_pixel" not in self.dataset.columns: raise AnalysisError("This recording has no scan_pixel channel.")
            channel = self.channel.currentData()
            if cv:
                if channel not in {"current1_na", "current2_na"}: raise AnalysisError("Choose a current channel for CV potential maps.")
                self.points = potential_map(self.dataset, self.cv_groups, channel, self.potential.value(), self.direction.currentIndex() == 0)
            else:
                self.points = hop_map(self.dataset, channel, self.statistic.currentText(), self.hop_groups)
            if not self.points: raise AnalysisError("No usable hops for this selection. CV maps need complete cycles crossing the selected potential.")
            pixels = self.dataset.metadata["scan_grid"]["pixels"]
            xs, ys = sorted({float(p["x_um"]) for p in pixels}), sorted({float(p["y_um"]) for p in pixels})
            if not np.isfinite(xs + ys).all(): raise AnalysisError("Grid coordinates must be finite.")
            xi, yi = {x: i for i, x in enumerate(xs)}, {y: i for i, y in enumerate(ys)}
            values = {(yi[p["y_um"]], xi[p["x_um"]]): p["value"] for p in self.points}
            self.map.base_unit = SIGNALS.get(channel, ("", "native"))[1]
            self.map.quantity = self.statistic.currentText() + " · " + SIGNALS.get(channel, (channel, ""))[0]
            self.map.colormap_name = self.palette.currentText()
            self.map.set_data(values, len(ys), len(xs), x_values=xs, y_values=ys)
            self.map.setVisible(True)
            self.notice.setText(("First selected-direction crossing per complete CV, interpolated then averaged across cycles." if cv else
                "Whole-hop statistics include approach and retract; these are not isolated surface data or confirmed contact Z.") + " Click a visited cell to inspect its trace.")
        except (AnalysisError, KeyError, TypeError, ValueError) as exc:
            self.map.setVisible(False); self.notice.setText(str(exc))

    def _clicked(self, event):
        if not self.points or not self.map.plot_item.getViewBox().sceneBoundingRect().contains(event.scenePos()): return
        pos = self.map.plot_item.getViewBox().mapSceneToView(event.scenePos())
        column = min(range(len(self.map.x_values)), key=lambda i: abs(self.map.x_values[i] - pos.x()))
        row = min(range(len(self.map.y_values)), key=lambda i: abs(self.map.y_values[i] - pos.y()))
        x, y = self.map.x_values[column], self.map.y_values[row]
        match = next((p for p in self.points if p["x_um"] == x and p["y_um"] == y), None)
        if match is not None: self.hop_selected.emit(match["scan_pixel"])

    def _export(self):
        if not self.points: return
        columns = ("scan_pixel", "x_um", "y_um", "value", "samples")
        cv = self.statistic.currentText() == "CV at potential"
        export_result(self, self.dataset, ([p[c] for c in columns] for p in self.points), columns,
            {"scope": "First crossing per complete CV, averaged across cycles" if cv else "Whole hop, all phases",
             "channel": self.channel.currentData(), "statistic": self.statistic.currentText(),
             "potential_v": self.potential.value() if cv else None,
             "direction": self.direction.currentText() if cv else None,
             "samples_column": "contributing cycles" if cv else "finite samples", "unit": self.map.base_unit}, "_map")
