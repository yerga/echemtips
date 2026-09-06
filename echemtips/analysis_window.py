"""PySide6/PyQtGraph recording-analysis window."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .analysis_core import AnalysisDataset, AnalysisError, CVCycle, CURRENT_COLUMNS, PLOT_COLORS, RAW_SIGNALS, extract_cv_cycles
from .qt_common import COLORS, Card, XYPlot, button, label


class AnalysisWindow(QtWidgets.QMainWindow):
    def __init__(self, initial_path: Path | str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("eChemTips — Data Analysis")
        self.resize(1440, 900)
        self.setMinimumSize(1040, 680)
        self.dataset: AnalysisDataset | None = None
        self.cycles: list[CVCycle] = []
        self.data_folder = self._default_data_folder()
        self.file_paths: list[Path] = []
        self._build_ui()
        self.refresh_files()
        if initial_path:
            self.load_recording(Path(initial_path))
        elif self.file_paths:
            self.file_list.setCurrentRow(0)

    @staticmethod
    def _default_data_folder() -> Path:
        settings_path = Path(".echemtips/settings.json")
        if settings_path.exists():
            try:
                raw = json.loads(settings_path.read_text(encoding="utf-8"))
                return Path(raw.get("save_directory", "data")).expanduser().resolve()
            except (OSError, ValueError, TypeError):
                pass
        return Path("data").resolve()

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root.setObjectName("window")
        self.setCentralWidget(root)
        shell = QtWidgets.QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(285)
        side = QtWidgets.QVBoxLayout(sidebar)
        side.setContentsMargins(18, 22, 18, 18)
        side.setSpacing(8)
        side.addWidget(label("eChemTips", "brand"))
        side.addWidget(label("DATA ANALYSIS", "sidebarMuted"))
        side.addSpacing(14)
        side.addWidget(label("RECORDINGS", "sidebarMuted"))
        self.folder_label = label("", "sidebarMuted", word_wrap=True)
        self.folder_label.setToolTip(str(self.data_folder))
        side.addWidget(self.folder_label)
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setAccessibleName("Available recordings")
        self.file_list.currentRowChanged.connect(self._load_selected_file)
        side.addWidget(self.file_list, 1)
        side.addWidget(button("Open recording…", self.open_file, "primary"))
        side.addWidget(button("Choose folder…", self.choose_folder))
        side.addWidget(button("Refresh", self.refresh_files))
        shell.addWidget(sidebar)

        main = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main)
        main_layout.setContentsMargins(24, 18, 24, 20)
        main_layout.setSpacing(12)
        self.title_label = label("Select a recording", "pageTitle")
        self.subtitle_label = label("Raw traces and separately extracted voltammograms", "pageDescription", word_wrap=True)
        main_layout.addWidget(self.title_label)
        main_layout.addWidget(self.subtitle_label)
        metrics = QtWidgets.QHBoxLayout()
        metrics.setSpacing(8)
        self.metric_labels: dict[str, QtWidgets.QLabel] = {}
        for key, caption in (("samples", "SAMPLES"), ("duration", "DURATION"), ("voltage", "POTENTIAL RANGE"), ("z", "Z RANGE"), ("cycles", "CV CYCLES")):
            card = Card(caption)
            value = label("—", "statusStrong")
            card.body.setLayout(QtWidgets.QVBoxLayout())
            card.body.layout().setContentsMargins(0, 2, 0, 0)
            card.body.layout().addWidget(value)
            self.metric_labels[key] = value
            metrics.addWidget(card, 1)
        main_layout.addLayout(metrics)
        self.tabs = QtWidgets.QTabWidget()
        main_layout.addWidget(self.tabs, 1)
        shell.addWidget(main, 1)
        self._build_raw_tab()
        self._build_cv_tab()
        self._build_table_tab()
        self._build_metadata_tab()

    def _build_raw_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(label("Signal", "muted"))
        self.raw_signal = QtWidgets.QComboBox()
        self.raw_signal.addItems(("All currents", *RAW_SIGNALS))
        self.raw_signal.currentTextChanged.connect(self._refresh_raw_plot)
        controls.addWidget(self.raw_signal)
        controls.addStretch(1)
        controls.addWidget(label("Full recording · interactive pan and zoom", "muted"))
        layout.addLayout(controls)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.raw_current_plot = XYPlot("Elapsed time (s)", "Current (nA)")
        self.raw_context_plot = XYPlot("Elapsed time (s)", "Applied potential (V)")
        splitter.addWidget(self.raw_current_plot)
        splitter.addWidget(self.raw_context_plot)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.tabs.addTab(tab, "Raw traces")

    def _build_cv_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(label("Current channel", "muted"))
        self.cv_current = QtWidgets.QComboBox()
        self.cv_current.addItems(tuple(CURRENT_COLUMNS))
        self.cv_current.currentTextChanged.connect(self._refresh_cv_view)
        controls.addWidget(self.cv_current)
        controls.addWidget(button("Set CV program…", self._edit_cv_program))
        controls.addStretch(1)
        self.export_button = button("Export separated CVs…", self.export_cycles, "primary")
        controls.addWidget(self.export_button)
        layout.addLayout(controls)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        summary = Card("Detected cycles", "Select a cycle or overlay every completed cycle.")
        summary.setMinimumWidth(285)
        summary.setMaximumWidth(380)
        summary_layout = QtWidgets.QVBoxLayout(summary.body)
        summary_layout.setContentsMargins(0, 4, 0, 0)
        self.cycle_tree = QtWidgets.QTreeWidget()
        self.cycle_tree.setHeaderLabels(("Cycle", "Max / nA", "Min / nA"))
        self.cycle_tree.setRootIsDecorated(False)
        self.cycle_tree.currentItemChanged.connect(self._refresh_cv_plot)
        summary_layout.addWidget(self.cycle_tree, 1)
        self.cv_detail = label("", "muted", word_wrap=True)
        summary_layout.addWidget(self.cv_detail)
        splitter.addWidget(summary)
        self.cv_plot = XYPlot("Applied potential (V)", "Current (nA)")
        splitter.addWidget(self.cv_plot)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.tabs.addTab(tab, "Voltammograms")

    def _build_table_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.table_status = label("", "muted")
        layout.addWidget(self.table_status)
        self.data_table = QtWidgets.QTableWidget()
        self.data_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setSortingEnabled(False)
        layout.addWidget(self.data_table, 1)
        self.tabs.addTab(tab, "Raw data table")

    def _build_metadata_tab(self) -> None:
        self.metadata_text = QtWidgets.QPlainTextEdit()
        self.metadata_text.setReadOnly(True)
        self.metadata_text.setFont(QtGui.QFont("Menlo", 10))
        self.tabs.addTab(self.metadata_text, "Metadata")

    def refresh_files(self) -> None:
        self.folder_label.setText(str(self.data_folder))
        self.folder_label.setToolTip(str(self.data_folder))
        supported = {".csv", ".tdms", ".tsv", ".set"}
        self.file_paths = sorted(
            (path for path in self.data_folder.iterdir() if path.is_file() and path.suffix.casefold() in supported),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if self.data_folder.exists() else []
        self.file_list.blockSignals(True)
        self.file_list.clear()
        self.file_list.addItems(path.stem.replace("_", " ") for path in self.file_paths)
        self.file_list.blockSignals(False)

    def choose_folder(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose eChemTips data folder", str(self.data_folder))
        if chosen:
            self.data_folder = Path(chosen).resolve()
            self.refresh_files()

    def open_file(self) -> None:
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open eChemTips recording", str(self.data_folder),
            "eChemTips recordings (*.csv *.tdms *.tsv *.set);;All files (*)",
        )
        if chosen:
            self.load_recording(Path(chosen))

    def _load_selected_file(self, row: int) -> None:
        if 0 <= row < len(self.file_paths):
            self.load_recording(self.file_paths[row])

    def load_recording(self, path: Path) -> None:
        try:
            self.dataset = AnalysisDataset.load(path)
            self.cycles = extract_cv_cycles(self.dataset)
            self._refresh_all()
        except AnalysisError as exc:
            QtWidgets.QMessageBox.critical(self, "eChemTips Data Analysis", str(exc))

    def _refresh_all(self) -> None:
        assert self.dataset is not None
        dataset = self.dataset
        self.title_label.setText(dataset.path.stem.replace("_", " "))
        self.subtitle_label.setText(f"{dataset.experiment} · {dataset.metadata.get('started_at', 'Start time unavailable')} · {dataset.path}")
        voltage, z_values = dataset.values("voltage1_v"), dataset.values("z_um")
        self.metric_labels["samples"].setText(f"{len(dataset.rows):,}")
        self.metric_labels["duration"].setText(f"{dataset.duration_s:.2f} s")
        self.metric_labels["voltage"].setText(f"{min(voltage):.3g} to {max(voltage):.3g} V")
        self.metric_labels["z"].setText(f"{min(z_values):.3g} to {max(z_values):.3g} µm" if z_values else "—")
        self.metric_labels["cycles"].setText(str(len(self.cycles)) if self.cycles else "—")
        self._refresh_raw_plot()
        self._refresh_cv_view()
        self._refresh_table()
        self.metadata_text.setPlainText(json.dumps(dataset.metadata, indent=2, default=str) if dataset.metadata else "No matching JSON metadata file was found.")

    def _refresh_raw_plot(self, *_args: object) -> None:
        if self.dataset is None:
            return
        times, selected = self.dataset.values("elapsed_s"), self.raw_signal.currentText()
        series: list[tuple[str, list[float], list[float], str]] = []
        if selected == "All currents":
            for index, (name, column) in enumerate(CURRENT_COLUMNS.items()):
                if column in self.dataset.columns:
                    series.append((name, times, self.dataset.values(column), PLOT_COLORS[index]))
            self.raw_current_plot.y_label = "Current (nA)"
        else:
            column = RAW_SIGNALS[selected]
            series.append((selected, times, self.dataset.values(column), COLORS["accent"]))
            self.raw_current_plot.y_label = "Current (nA)"
        self.raw_current_plot.set_data(series)
        self.raw_context_plot.set_data([("Voltage 1", times, self.dataset.values("voltage1_v"), COLORS["blue"])])

    def _refresh_cv_view(self, *_args: object) -> None:
        self.cycle_tree.clear()
        if not self.cycles:
            self.cv_plot.set_message("No complete CV cycles detected in this recording")
            self.cv_detail.setText("Raw traces remain available. Add the voltage program for legacy files without CV metadata.")
            self.export_button.setEnabled(False)
            return
        self.export_button.setEnabled(True)
        column = CURRENT_COLUMNS[self.cv_current.currentText()]
        all_item = QtWidgets.QTreeWidgetItem(("All", "", ""))
        all_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, -1)
        self.cycle_tree.addTopLevelItem(all_item)
        for index, cycle in enumerate(self.cycles):
            maximum, _max_v, minimum, _min_v = cycle.peak_summary(column)
            item = QtWidgets.QTreeWidgetItem((cycle.label, f"{maximum:.3g}", f"{minimum:.3g}"))
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, index)
            self.cycle_tree.addTopLevelItem(item)
        self.cycle_tree.resizeColumnToContents(0)
        self.cycle_tree.setCurrentItem(all_item)

    def _refresh_cv_plot(self, *_args: object) -> None:
        if not self.cycles:
            return
        item = self.cycle_tree.currentItem()
        index = int(item.data(0, QtCore.Qt.ItemDataRole.UserRole)) if item else -1
        cycles = self.cycles if index < 0 else [self.cycles[index]]
        column = CURRENT_COLUMNS[self.cv_current.currentText()]
        self.cv_plot.set_data([(cycle.label, cycle.potential_v, cycle.current_na(column), PLOT_COLORS[i % len(PLOT_COLORS)]) for i, cycle in enumerate(cycles)])
        if len(cycles) == 1:
            maximum, max_v, minimum, min_v = cycles[0].peak_summary(column)
            self.cv_detail.setText(f"Anodic maximum\n{maximum:+.4g} nA at {max_v:+.4g} V\n\nCathodic minimum\n{minimum:+.4g} nA at {min_v:+.4g} V")
        else:
            self.cv_detail.setText(f"Overlaying {len(cycles)} completed cycles.\n\nSelect one cycle for its peak summary.")

    def _refresh_table(self) -> None:
        if self.dataset is None:
            return
        columns = self.dataset.columns
        rows = self.dataset.rows[:5_000]
        self.data_table.clear()
        self.data_table.setColumnCount(len(columns))
        self.data_table.setHorizontalHeaderLabels(columns)
        self.data_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                item = QtWidgets.QTableWidgetItem(f"{row[column]:.8g}")
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                self.data_table.setItem(row_index, column_index, item)
        self.data_table.resizeColumnsToContents()
        self.table_status.setText(
            f"Showing the first 5,000 of {len(self.dataset.rows):,} rows; the source remains complete."
            if len(self.dataset.rows) > 5_000 else f"Showing all {len(self.dataset.rows):,} rows from the source."
        )

    def _edit_cv_program(self) -> None:
        if self.dataset is None:
            QtWidgets.QMessageBox.information(self, "eChemTips Data Analysis", "Open a recording before setting its CV program.")
            return
        existing = self.dataset.metadata.get("parameters")
        existing = existing if isinstance(existing, dict) else {}
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Set CV program")
        form = QtWidgets.QFormLayout(dialog)
        fields = (("Start voltage", "cv_start_v", "V"), ("First vertex", "cv_vertex1_v", "V"), ("Second vertex", "cv_vertex2_v", "V"), ("Approach voltage", "approach_voltage_v", "V"), ("Number of cycles", "cycles", ""))
        edits: dict[str, QtWidgets.QLineEdit] = {}
        for caption, key, unit in fields:
            value = existing.get(key, existing.get("cv_start_v", "") if key == "approach_voltage_v" else "")
            edit = QtWidgets.QLineEdit(str(value))
            edits[key] = edit
            form.addRow(f"{caption} ({unit})" if unit else caption, edit)
        status = label("", "muted", word_wrap=True)
        status.setStyleSheet(f"color:{COLORS['danger']}")
        form.addRow(status)
        actions = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel | QtWidgets.QDialogButtonBox.StandardButton.Apply)
        form.addRow(actions)

        def apply_values() -> None:
            try:
                values = {key: float(edits[key].text().strip()) for _caption, key, _unit in fields if key != "cycles"}
                cycle_value = float(edits["cycles"].text().strip())
                if not all(math.isfinite(value) for value in values.values()) or not math.isfinite(cycle_value) or not cycle_value.is_integer() or cycle_value < 1:
                    raise ValueError
            except ValueError:
                status.setText("Enter finite voltages and a positive whole number of cycles.")
                return
            values["cycles"] = int(cycle_value)
            assert self.dataset is not None
            self.dataset.metadata["parameters"] = values
            self.dataset.metadata.setdefault("experiment", "Manual CV")
            self.cycles = extract_cv_cycles(self.dataset)
            dialog.accept()
            self._refresh_all()

        actions.rejected.connect(dialog.reject)
        actions.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(apply_values)
        dialog.exec()

    def export_cycles(self) -> None:
        if self.dataset is None or not self.cycles:
            return
        suggested = self.dataset.path.with_name(f"{self.dataset.path.stem}_separated_cvs.csv")
        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export separated CVs", str(suggested), "CSV (*.csv)")
        if not chosen:
            return
        columns = ("pixel", "cycle", "point", *self.dataset.columns)
        try:
            with Path(chosen).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                for cycle in self.cycles:
                    for point, row in enumerate(cycle.rows):
                        writer.writerow({"pixel": cycle.pixel, "cycle": cycle.number, "point": point, **row})
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "eChemTips Data Analysis", f"Could not export CVs: {exc}")
            return
        QtWidgets.QMessageBox.information(self, "eChemTips Data Analysis", f"Separated CVs saved to:\n{chosen}")
