"""PySide6/PyQtGraph recording-analysis window."""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .analysis_core import AnalysisDataset, CVCycle, CURRENT_COLUMNS, PLOT_COLORS
from .analysis_jobs import LoadRecording
from .analysis_views import ExplorerPanel, MapPanel, RecordingTableModel
from .models import SettingsStore
from .branding import application_icon, logo_label
from .qt_common import COLORS, Card, XYPlot, application_stylesheet, button, label, scroll_area, configure_pyqtgraph


class AnalysisWindow(QtWidgets.QMainWindow):
    """Browse recordings, inspect raw data, separate CVs, and export cycles."""
    def __init__(self, initial_path: Path | str | None = None, *, data_folder: Path | str | None = None) -> None:
        super().__init__()
        configure_pyqtgraph()
        self.setWindowTitle("eChemTips — Data Analysis")
        self.setWindowIcon(application_icon())
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.dataset: AnalysisDataset | None = None
        self.source_dataset: AnalysisDataset | None = None
        self.cycles: list[CVCycle] = []
        self.data_folder = Path(data_folder).expanduser().resolve() if data_folder else self._default_data_folder()
        self.file_paths: list[Path] = []
        self._load_token = 0
        self._load_tasks = {}
        self.loading = False
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._build_ui()
        preferences = SettingsStore().load()
        self.setStyleSheet(application_stylesheet(preferences.font_size_pt))
        for plot in self.findChildren(XYPlot):
            plot.current_display_unit = preferences.current_display_unit
            plot.font_size_pt = preferences.font_size_pt
            plot.trace_width_px = preferences.trace_width_px
            plot.redraw()
        self.refresh_files()
        if initial_path:
            self.load_recording(Path(initial_path))

    @staticmethod
    def _default_data_folder() -> Path:
        return Path(SettingsStore().load().save_directory).expanduser().resolve()

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root.setObjectName("window")
        self.setCentralWidget(root)
        shell = QtWidgets.QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(190)
        sidebar.setMaximumWidth(300)
        side = QtWidgets.QVBoxLayout(sidebar)
        side.setContentsMargins(18, 22, 18, 18)
        side.setSpacing(8)
        brand_row = QtWidgets.QWidget(); brand_layout = QtWidgets.QHBoxLayout(brand_row)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.addWidget(logo_label()); brand_layout.addWidget(label("eChemTips", "brand")); brand_layout.addStretch(1)
        side.addWidget(brand_row)
        side.addWidget(label("DATA ANALYSIS", "sidebarMuted"))
        side.addSpacing(14)
        side.addWidget(label("RECORDINGS", "sidebarMuted"))
        self.folder_label = label("", "sidebarMuted", word_wrap=True)
        self.folder_label.setToolTip(str(self.data_folder))
        side.addWidget(self.folder_label)
        self.file_filter = QtWidgets.QLineEdit()
        self.file_filter.setPlaceholderText("Filter recordings…")
        self.file_filter.textChanged.connect(self._filter_files)
        side.addWidget(self.file_filter)
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setAccessibleName("Available recordings")
        self.file_list.currentRowChanged.connect(self._load_selected_file)
        side.addWidget(self.file_list, 1)
        side.addWidget(button("Open recording…", self.open_file, "primary"))
        side.addWidget(button("Choose folder…", self.choose_folder))
        side.addWidget(button("Refresh", self.refresh_files))
        self.browser = sidebar
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(sidebar)
        shell.addWidget(splitter)

        main = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main)
        main_layout.setContentsMargins(24, 18, 24, 20)
        main_layout.setSpacing(12)
        self.title_label = label("Select a recording", "pageTitle")
        self.subtitle_label = label("Raw traces and separately extracted voltammograms", "pageDescription", word_wrap=True)
        main_layout.addWidget(self.title_label)
        main_layout.addWidget(self.subtitle_label)
        browse_toggle = button("Show / hide file browser", lambda: sidebar.setVisible(not sidebar.isVisible()))
        main_layout.removeWidget(self.title_label)
        title_row = QtWidgets.QHBoxLayout()
        title_row.addWidget(self.title_label, 1)
        title_row.addWidget(browse_toggle)
        main_layout.insertLayout(0, title_row)
        metrics = QtWidgets.QGridLayout()
        metrics.setSpacing(8)
        self.metric_labels: dict[str, QtWidgets.QLabel] = {}
        for index, (key, caption) in enumerate((("samples", "Samples"), ("duration", "Duration"), ("voltage", "E1 range"), ("z", "Z range"), ("cycles", "CV cycles"))):
            value = label("—", "muted")
            value.setToolTip(caption)
            self.metric_labels[key] = value
            metrics.addWidget(value, index // 3, index % 3)
        main_layout.addLayout(metrics)
        processing = QtWidgets.QHBoxLayout()
        self.smoothing_enabled = QtWidgets.QCheckBox("Smooth currents")
        self.smoothing_window = QtWidgets.QSpinBox()
        self.smoothing_window.setRange(3, 10001)
        self.smoothing_window.setSingleStep(2)
        self.smoothing_window.setValue(11)
        self.smoothing_window.setSuffix(" samples")
        self.smoothing_window.setToolTip("Centered moving average. Use an odd window; large windows can suppress real peaks. Applies to currents in traces, measurements, CVs and maps. Originals are unchanged.")
        self.smoothing_apply = button("Apply", self._apply_smoothing)
        processing.addWidget(self.smoothing_enabled)
        processing.addWidget(self.smoothing_window)
        processing.addWidget(self.smoothing_apply)
        processing.addStretch(1)
        main_layout.addLayout(processing)
        self.tabs = QtWidgets.QTabWidget()
        main_layout.addWidget(self.tabs, 1)
        splitter.addWidget(main)
        splitter.setStretchFactor(1, 1)
        self._build_raw_tab()
        self._build_cv_tab()
        self.map_panel = MapPanel()
        self.map_panel.hop_selected.connect(self._inspect_hop)
        self.tabs.addTab(self.map_panel, "Hop maps")
        self._build_table_tab()
        self._build_metadata_tab()
        self.statusBar().showMessage("Open a recording. Source files are never edited by analysis.")

    def _build_raw_tab(self) -> None:
        self.explorer = ExplorerPanel()
        self.explorer.setMinimumHeight(510)
        self.raw_current_plot = self.explorer.plot
        self.explorer_tab = scroll_area(self.explorer)
        self.tabs.addTab(self.explorer_tab, "Explore and measure")

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
        summary = Card("Detected cycles")
        summary.setMinimumWidth(285)
        summary.setMaximumWidth(380)
        summary_layout = QtWidgets.QVBoxLayout(summary.body)
        summary_layout.setContentsMargins(0, 4, 0, 0)
        self.cycle_tree = QtWidgets.QTreeWidget()
        self.cycle_tree.setHeaderLabels(("Cycle", "Max / nA", "Min / nA"))
        self.cycle_tree.setRootIsDecorated(False)
        self.cycle_tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.cycle_tree.itemSelectionChanged.connect(self._refresh_cv_plot)
        summary_layout.addWidget(self.cycle_tree, 1)
        self.cv_detail = label("", "muted", word_wrap=True)
        summary_layout.addWidget(self.cv_detail)
        splitter.addWidget(summary)
        self.cv_plot = XYPlot("Potential E1 (V)", "Current (nA)")
        splitter.addWidget(self.cv_plot)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.tabs.addTab(tab, "CV")

    def _build_table_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.table_status = label("", "muted")
        layout.addWidget(self.table_status)
        self.data_table = QtWidgets.QTableView()
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
        """Rescan the selected folder for supported recording formats."""
        self.folder_label.setText(str(self.data_folder))
        self.folder_label.setToolTip(str(self.data_folder))
        supported = {".csv", ".tdms", ".tsv", ".set"}
        try:
            self.file_paths = sorted(
                (path for path in self.data_folder.iterdir() if path.is_file() and path.suffix.casefold() in supported),
                key=lambda path: path.stat().st_mtime, reverse=True,
            ) if self.data_folder.exists() else []
        except OSError as exc:
            self.file_paths = []
            self.statusBar().showMessage(f"Could not browse folder: {exc}")
        self.file_list.blockSignals(True)
        self.file_list.clear()
        self.file_list.addItems(path.stem.replace("_", " ") for path in self.file_paths)
        self.file_list.blockSignals(False)
        self._filter_files()

    def _filter_files(self, *_args) -> None:
        term = self.file_filter.text().casefold()
        for index in range(self.file_list.count()):
            self.file_list.item(index).setHidden(term not in self.file_list.item(index).text().casefold())

    def choose_folder(self) -> None:
        """Prompt for a recording folder and refresh its file list."""
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose eChemTips data folder", str(self.data_folder))
        if chosen:
            self.data_folder = Path(chosen).resolve()
            self.refresh_files()

    def open_file(self) -> None:
        """Prompt for one recording and load it into every analysis tab."""
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open eChemTips recording", str(self.data_folder),
            "eChemTips recordings (*.csv *.tdms *.tsv *.set);;All files (*)",
        )
        if chosen:
            self.load_recording(Path(chosen))

    def _load_selected_file(self, row: int) -> None:
        if 0 <= row < len(self.file_paths):
            self.load_recording(self.file_paths[row])

    def load_recording(self, path: Path, *, source=None) -> None:
        """Queue a background import; only the newest selection may update views."""
        self._load_token += 1
        for previous in self._load_tasks.values():
            previous.cancelled = True
        self.loading = True
        self.statusBar().showMessage(f"Loading {Path(path).name}…")
        window = self.smoothing_window.value() if self.smoothing_enabled.isChecked() else 1
        if window % 2 == 0:
            window += 1
            self.smoothing_window.setValue(window)
        task = LoadRecording(self._load_token, path, source=source, smoothing_window=window)
        task.signals.finished.connect(self._loaded)
        self._load_tasks[self._load_token] = task
        self._pool.start(task)

    def _apply_smoothing(self):
        if self.source_dataset is not None and not self.loading:
            self.load_recording(self.source_dataset.path, source=self.source_dataset)

    def _loaded(self, token, bundle, error):
        task = self._load_tasks.pop(token, None)
        if token != self._load_token:
            return
        self.loading = False
        if error:
            self.statusBar().showMessage(error)
            QtWidgets.QMessageBox.warning(self, "Recording could not be loaded", error)
            return
        self.dataset, self.cycles, self.groups = bundle
        self.source_dataset = task.source
        controls = (self.explorer.provider, self.explorer.selection, self.explorer.x_signal,
                    self.explorer.y_signal, self.map_panel.channel, self.cv_current)
        previous = [control.currentText() for control in controls] if task.reprocessing else []
        bounds, baseline = self.explorer.bounds, self.explorer.baseline
        if task.reprocessing:
            self.explorer.reference = None
        self._refresh_all()
        for control, text in zip(controls, previous):
            control.setCurrentText(text)
        if task.reprocessing:
            self.explorer.bounds, self.explorer.baseline = bounds, baseline
            self.explorer.refresh()
        mode = self.dataset.metadata.get("analysis_processing", {})
        description = (f"Smoothed currents · {mode['window_samples']}-sample centered mean"
                       if mode else "Original currents · smoothing off")
        self.statusBar().showMessage(f"{len(self.dataset.rows):,} samples · {description}")

    def _inspect_hop(self, pixel):
        self.tabs.setCurrentWidget(self.explorer_tab)
        self.explorer.provider.setCurrentIndex(self.explorer.provider.findData("hops"))
        selections = self.groups.get("hops", [])
        index = next((i for i, group in enumerate(selections) if group.pixel == pixel), -1)
        self.explorer.selection.setCurrentIndex(index)

    def closeEvent(self, event):
        """Discard stale load results when closing; never terminate worker I/O."""
        self._load_token += 1
        self.loading = False
        super().closeEvent(event)

    def _refresh_all(self) -> None:
        assert self.dataset is not None
        dataset = self.dataset
        self.title_label.setText("Recording analysis")
        self.subtitle_label.setText(f"{dataset.path.name} · {dataset.experiment} · {dataset.metadata.get('status', 'status unknown')}")
        processing = dataset.metadata.get("analysis_processing")
        if processing:
            self.subtitle_label.setText(self.subtitle_label.text() + f" · SMOOTHED ({processing['window_samples']} samples)")
        self.subtitle_label.setToolTip(str(dataset.path))
        voltage, z_values = dataset.values("voltage1_v"), dataset.values("z_um")
        self.metric_labels["samples"].setText(f"Samples: {len(dataset.rows):,}")
        self.metric_labels["duration"].setText(f"Duration: {dataset.duration_s:.2f} s")
        self.metric_labels["voltage"].setText(f"E1: {min(voltage):.3g} to {max(voltage):.3g} V" if voltage else "E1: —")
        self.metric_labels["z"].setText(f"Z: {min(z_values):.3g} to {max(z_values):.3g} µm" if z_values else "Z: —")
        self.metric_labels["cycles"].setText(f"Complete CVs: {len(self.cycles)}")
        self.explorer.set_dataset(dataset, self.groups)
        self.map_panel.set_dataset(dataset, self.groups.get("cv", []), self.groups.get("hops", []))
        self.cv_current.blockSignals(True); self.cv_current.clear()
        self.cv_current.addItems([name for name, column in CURRENT_COLUMNS.items() if column in dataset.columns])
        self.cv_current.blockSignals(False)
        self._refresh_cv_view()
        self._refresh_table()
        self.metadata_text.setPlainText(json.dumps(dataset.metadata, indent=2, default=str) if dataset.metadata else "No matching JSON metadata file was found.")

    def _refresh_raw_plot(self, *_args: object) -> None:
        self.explorer.refresh()

    def _refresh_cv_view(self, *_args: object) -> None:
        self.cycle_tree.clear()
        if not self.cycles or not self.cv_current.currentText():
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
        if not self.cycles or not self.cv_current.currentText():
            return
        item = self.cycle_tree.currentItem()
        index = int(item.data(0, QtCore.Qt.ItemDataRole.UserRole)) if item else -1
        selected = [int(item.data(0, QtCore.Qt.ItemDataRole.UserRole)) for item in self.cycle_tree.selectedItems()]
        cycles = self.cycles if not selected or -1 in selected else [self.cycles[i] for i in selected]
        total = len(cycles)
        cycles = cycles[:50]
        column = CURRENT_COLUMNS[self.cv_current.currentText()]
        self.cv_plot.set_data([(cycle.label, cycle.potential_v, cycle.current_na(column), PLOT_COLORS[i % len(PLOT_COLORS)]) for i, cycle in enumerate(cycles)])
        if len(cycles) == 1:
            maximum, max_v, minimum, min_v = cycles[0].peak_summary(column)
            kind = "Smoothed" if self.dataset.metadata.get("analysis_processing") else "Raw"
            self.cv_detail.setText(f"Maximum current\n{maximum:+.4g} nA at {max_v:+.4g} V\n\nMinimum current\n{minimum:+.4g} nA at {min_v:+.4g} V\n\n{kind} extrema; no peak fitting or baseline correction.")
        else:
            self.cv_detail.setText(f"Overlaying {len(cycles)} of {total} selected cycles (display limit 50).\nCtrl/Cmd-click to select cycles. Exports retain all cycles.")

    def _refresh_table(self) -> None:
        if self.dataset is None:
            return
        old = self.data_table.model()
        self.data_table.setModel(RecordingTableModel(self.dataset, self.data_table))
        if old is not None:
            old.deleteLater()
        self.data_table.horizontalHeader().setDefaultSectionSize(145)
        kind = "smoothed currents" if self.dataset.metadata.get("analysis_processing") else "original values"
        self.table_status.setText(f"All {len(self.dataset.rows):,} rows · {kind} · native units · read-only")

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
            """Validate the manual legacy waveform and rerun cycle extraction."""
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
            self.dataset.metadata["parameters"] = {**existing, **values}
            self.dataset.metadata.setdefault("experiment", "Manual CV")
            if self.source_dataset is not None:
                self.source_dataset.metadata["parameters"] = {**existing, **values}
            dialog.accept()
            self._apply_smoothing()

        actions.rejected.connect(dialog.reject)
        actions.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(apply_values)
        dialog.exec()

    def export_cycles(self) -> None:
        """Export every separated CV row with explicit pixel/cycle/point IDs."""
        if self.dataset is None or not self.cycles:
            return
        from .analysis_views import export_result
        columns = ("pixel", "cycle", "point", *self.dataset.columns)
        rows = ((cycle.pixel, cycle.number, point, *(row[name] for name in self.dataset.columns))
                for cycle in self.cycles for point, row in enumerate(cycle.rows))
        export_result(self, self.dataset, rows, columns,
                      {"scope": "All complete CV cycles", "cycles": len(self.cycles)}, "_separated_cvs")
