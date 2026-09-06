from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Iterable

from .ui import COLORS, Card, _font
from .legacy_data import LegacyDataError, load_legacy


CURRENT_COLUMNS = {
    "Current 1": "current1_na",
    "Current 2": "current2_na",
    "Current 3": "current3_na",
    "Current 4": "current4_na",
}

RAW_SIGNALS = {
    **CURRENT_COLUMNS,
    "Lock-in amplitude": "lockin_amplitude_na",
    "Lock-in phase": "lockin_phase_deg",
}

PLOT_COLORS = (
    COLORS["accent"],
    COLORS["blue"],
    COLORS["danger"],
    COLORS["warning"],
    "#a78bfa",
    "#fb923c",
    "#22d3ee",
    "#f472b6",
)


class AnalysisError(RuntimeError):
    """A saved recording cannot be read or interpreted."""


@dataclass(slots=True)
class AnalysisDataset:
    path: Path
    columns: tuple[str, ...]
    rows: list[dict[str, float]]
    metadata: dict[str, Any]

    @classmethod
    def load(cls, path: Path | str) -> "AnalysisDataset":
        source_path = Path(path).expanduser().resolve()
        if not source_path.exists():
            raise AnalysisError(f"Recording not found: {source_path}")
        if source_path.suffix.casefold() != ".csv":
            try:
                recording = load_legacy(source_path)
            except LegacyDataError as exc:
                raise AnalysisError(str(exc)) from exc
            return cls(recording.path, recording.columns, recording.rows, recording.metadata)
        csv_path = source_path
        try:
            with csv_path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    raise AnalysisError("The CSV file has no header row.")
                columns = tuple(reader.fieldnames)
                required = {"elapsed_s", "voltage1_v", "current1_na"}
                missing = required.difference(columns)
                if missing:
                    raise AnalysisError(f"Missing required columns: {', '.join(sorted(missing))}")
                rows: list[dict[str, float]] = []
                for line_number, raw in enumerate(reader, 2):
                    try:
                        rows.append({name: float(raw[name]) for name in columns})
                    except (KeyError, TypeError, ValueError) as exc:
                        raise AnalysisError(f"Invalid numeric value on CSV line {line_number}.") from exc
        except OSError as exc:
            raise AnalysisError(f"Could not read {csv_path.name}: {exc}") from exc
        if not rows:
            raise AnalysisError("The recording contains no samples.")

        metadata_path = csv_path.with_suffix(".json")
        metadata: dict[str, Any] = {}
        if metadata_path.exists():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                metadata = {}
        return cls(csv_path, columns, rows, metadata)

    @property
    def experiment(self) -> str:
        value = self.metadata.get("experiment")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "Approach then CV" if "approach_then_cv" in self.path.stem.lower() else "Watch Current"

    @property
    def duration_s(self) -> float:
        times = [row["elapsed_s"] for row in self.rows]
        return max(times) - min(times)

    def values(self, column: str) -> list[float]:
        return [row[column] for row in self.rows if column in row]


@dataclass(slots=True)
class CVCycle:
    number: int
    rows: list[dict[str, float]]
    pixel: int = -1

    @property
    def label(self) -> str:
        return f"P{self.pixel + 1} · C{self.number}" if self.pixel >= 0 else str(self.number)

    @property
    def potential_v(self) -> list[float]:
        return [row["voltage1_v"] for row in self.rows]

    def current_na(self, column: str) -> list[float]:
        return [row[column] for row in self.rows]

    def peak_summary(self, column: str) -> tuple[float, float, float, float]:
        currents = self.current_na(column)
        potentials = self.potential_v
        high_index = max(range(len(currents)), key=currents.__getitem__)
        low_index = min(range(len(currents)), key=currents.__getitem__)
        return currents[high_index], potentials[high_index], currents[low_index], potentials[low_index]


def _target_index(
    voltages: list[float], start: int, end: int, target: float, direction: int, noise_tolerance: float
) -> int | None:
    """Find a target crossing, or the turning point if a real scan falls just short."""
    if direction == 0:
        return min(range(start, end + 1), key=lambda index: abs(voltages[index] - target))
    previous = voltages[start]
    if direction * (previous - target) >= 0:
        return start
    for index in range(start + 1, end + 1):
        value = voltages[index]
        if direction * (value - target) >= 0:
            before = index - 1
            return min((before, index), key=lambda candidate: abs(voltages[candidate] - target))
        if direction * (value - previous) < -noise_tolerance:
            return index - 1
        previous = value
    return None


def extract_cv_cycles(dataset: AnalysisDataset) -> list[CVCycle]:
    """Extract completed CV cycles using the voltage program saved in metadata."""
    if "scan_pixel" in dataset.columns:
        pixel_ids = sorted({int(row["scan_pixel"]) for row in dataset.rows if row.get("scan_pixel", -1) >= 0})
        if pixel_ids:
            separated: list[CVCycle] = []
            for pixel in pixel_ids:
                pixel_rows = [row for row in dataset.rows if int(row.get("scan_pixel", -1)) == pixel]
                subset = AnalysisDataset(dataset.path, tuple(name for name in dataset.columns if name != "scan_pixel"), pixel_rows, dataset.metadata)
                for cycle in extract_cv_cycles(subset):
                    cycle.pixel = pixel
                    separated.append(cycle)
            return separated
    parameters = dataset.metadata.get("parameters")
    if not isinstance(parameters, dict):
        return []
    try:
        # Approach/scan CVs retain the original prefixed field names, while
        # standalone CV uses the shorter names from CVParameters.
        cv_start = float(parameters["cv_start_v"] if "cv_start_v" in parameters else parameters["start_v"])
        vertex1 = float(parameters["cv_vertex1_v"] if "cv_vertex1_v" in parameters else parameters["vertex1_v"])
        vertex2 = float(parameters["cv_vertex2_v"] if "cv_vertex2_v" in parameters else parameters["vertex2_v"])
        requested_cycles = int(parameters["cycles"])
        approach_voltage = float(parameters.get("approach_voltage_v", cv_start))
    except (KeyError, TypeError, ValueError):
        return []
    if requested_cycles < 1:
        return []

    voltages = dataset.values("voltage1_v")
    if len(voltages) < 4:
        return []
    span = max(voltages) - min(voltages)
    active_tolerance = max(1e-9, span * 1e-6)
    active = [
        index
        for index in range(1, len(voltages))
        if abs(voltages[index] - voltages[index - 1]) > active_tolerance
    ]
    if not active:
        return []

    departure_tolerance = max(0.01, abs(vertex1 - vertex2) * 0.03)
    start_index = active[0]
    for index in active:
        if (
            abs(voltages[index - 1] - approach_voltage) <= departure_tolerance
            and abs(voltages[index] - approach_voltage) > departure_tolerance
        ):
            start_index = index
            break

    end_index = active[-1]
    cycles: list[CVCycle] = []
    cursor = start_index
    cycle_start = start_index
    for cycle_number in range(1, requested_cycles + 1):
        previous_target = cv_start
        cycle_end: int | None = None
        for target in (vertex1, vertex2, cv_start):
            direction = 1 if target > previous_target else -1 if target < previous_target else 0
            found = _target_index(voltages, cursor, end_index, target, direction, active_tolerance)
            if found is None:
                return cycles
            cycle_end = found
            cursor = min(found + 1, end_index)
            previous_target = target
        if cycle_end is None or cycle_end <= cycle_start:
            return cycles
        cycles.append(CVCycle(cycle_number, dataset.rows[cycle_start : cycle_end + 1]))
        cycle_start = cycle_end
    return cycles


class XYPlot(tk.Canvas):
    def __init__(self, parent: tk.Misc, x_label: str, y_label: str, **kwargs: object) -> None:
        super().__init__(parent, bg=COLORS["panel"], highlightthickness=0, **kwargs)
        self.x_label = x_label
        self.y_label = y_label
        self.series: list[tuple[str, list[float], list[float], str]] = []
        self.message = "Open a recording to begin"
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_data(self, series: Iterable[tuple[str, list[float], list[float], str]]) -> None:
        self.series = list(series)
        self.redraw()

    def set_message(self, message: str) -> None:
        self.series = []
        self.message = message
        self.redraw()

    @staticmethod
    def _ticks(low: float, high: float, count: int = 5) -> list[float]:
        if math.isclose(low, high):
            return [low]
        return [low + index * (high - low) / (count - 1) for index in range(count)]

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(10, self.winfo_width()), max(10, self.winfo_height())
        left, top, right, bottom = 76, 34, width - 24, height - 54
        if not self.series or right <= left or bottom <= top:
            self.create_text(width / 2, height / 2, text=self.message, fill=COLORS["muted"], font=_font(11))
            return
        points = [
            (x, y)
            for _, xs, ys, _ in self.series
            for x, y in zip(xs, ys)
            if math.isfinite(x) and math.isfinite(y)
        ]
        if not points:
            self.set_message("No finite values to display")
            return
        x_low, x_high = min(x for x, _ in points), max(x for x, _ in points)
        y_low, y_high = min(y for _, y in points), max(y for _, y in points)
        x_padding = max((x_high - x_low) * 0.04, 1e-9)
        y_padding = max((y_high - y_low) * 0.08, 1e-9)
        x_low, x_high = x_low - x_padding, x_high + x_padding
        y_low, y_high = y_low - y_padding, y_high + y_padding
        x_span, y_span = x_high - x_low, y_high - y_low

        for value in self._ticks(y_low, y_high):
            y = bottom - (value - y_low) / y_span * (bottom - top)
            self.create_line(left, y, right, y, fill=COLORS["grid"])
            self.create_text(left - 10, y, text=f"{value:.3g}", fill=COLORS["muted"], font=_font(8), anchor="e")
        for value in self._ticks(x_low, x_high):
            x = left + (value - x_low) / x_span * (right - left)
            self.create_line(x, top, x, bottom, fill=COLORS["grid"])
            self.create_text(x, bottom + 18, text=f"{value:.3g}", fill=COLORS["muted"], font=_font(8))
        self.create_line(left, bottom, right, bottom, fill=COLORS["muted"])
        self.create_line(left, top, left, bottom, fill=COLORS["muted"])
        self.create_text((left + right) / 2, height - 12, text=self.x_label, fill=COLORS["muted"], font=_font(9))
        self.create_text(18, (top + bottom) / 2, text=self.y_label, fill=COLORS["muted"], font=_font(9), angle=90)

        for series_index, (name, xs, ys, color) in enumerate(self.series):
            count = min(len(xs), len(ys))
            stride = max(1, math.ceil(count / 2500))
            coordinates: list[float] = []
            for index in range(0, count, stride):
                x_value, y_value = xs[index], ys[index]
                if not math.isfinite(x_value) or not math.isfinite(y_value):
                    continue
                coordinates.extend(
                    (
                        left + (x_value - x_low) / x_span * (right - left),
                        bottom - (y_value - y_low) / y_span * (bottom - top),
                    )
                )
            if len(coordinates) >= 4:
                self.create_line(*coordinates, fill=color, width=2)
            legend_x = left + series_index * 132
            self.create_line(legend_x, 17, legend_x + 20, 17, fill=color, width=3)
            self.create_text(legend_x + 26, 17, text=name, fill=COLORS["text"], font=_font(8), anchor="w")


class AnalysisApp(tk.Tk):
    def __init__(self, initial_path: Path | str | None = None) -> None:
        super().__init__()
        self.title("eChemTips Data Analysis")
        self.geometry("1380x860")
        self.minsize(1080, 700)
        self.configure(bg=COLORS["window"])
        self.dataset: AnalysisDataset | None = None
        self.cycles: list[CVCycle] = []
        self.data_folder = self._default_data_folder()
        self.file_paths: list[Path] = []
        self._configure_styles()
        self._build_ui()
        self.refresh_files()
        if initial_path:
            self.load_recording(Path(initial_path))
        elif self.file_paths:
            self.file_list.selection_set(0)
            self._load_selected_file()

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

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", background=COLORS["panel_2"], foreground=COLORS["text"], borderwidth=0, padding=(12, 8), font=_font(9, "bold"))
        style.map("TButton", background=[("active", COLORS["border"]), ("disabled", COLORS["panel"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#06251f")
        style.map("Accent.TButton", background=[("active", "#5eead4")])
        style.configure("TNotebook", background=COLORS["window"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["panel"], foreground=COLORS["muted"], padding=(18, 10), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", COLORS["panel_2"])], foreground=[("selected", COLORS["text"])])
        style.configure("Treeview", background=COLORS["panel_2"], fieldbackground=COLORS["panel_2"], foreground=COLORS["text"], rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=COLORS["panel"], foreground=COLORS["muted"], relief="flat", font=_font(8, "bold"))
        style.map("Treeview", background=[("selected", COLORS["accent_dark"])], foreground=[("selected", COLORS["text"])])
        style.configure("TCombobox", fieldbackground=COLORS["panel_2"], background=COLORS["panel_2"], foreground=COLORS["text"], padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel_2"])], foreground=[("readonly", COLORS["text"])])

    def _build_ui(self) -> None:
        sidebar = tk.Frame(self, width=285, bg=COLORS["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="eChemTips", bg=COLORS["sidebar"], fg=COLORS["accent"], font=_font(11, "bold")).pack(anchor="w", padx=20, pady=(23, 0))
        tk.Label(sidebar, text="DATA ANALYSIS", bg=COLORS["sidebar"], fg=COLORS["text"], font=_font(17, "bold")).pack(anchor="w", padx=20, pady=(2, 20))
        tk.Label(sidebar, text="RECORDINGS", bg=COLORS["sidebar"], fg=COLORS["muted"], font=_font(8, "bold")).pack(anchor="w", padx=20)
        self.folder_label = tk.Label(sidebar, text="", bg=COLORS["sidebar"], fg=COLORS["muted"], font=_font(8), wraplength=245, justify="left")
        self.folder_label.pack(anchor="w", padx=20, pady=(4, 9))
        list_frame = tk.Frame(sidebar, bg=COLORS["sidebar"])
        list_frame.pack(fill="both", expand=True, padx=14)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.file_list = tk.Listbox(
            list_frame,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            selectbackground=COLORS["accent_dark"],
            selectforeground=COLORS["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            font=_font(9),
            activestyle="none",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=self.file_list.yview)
        self.file_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_list.bind("<<ListboxSelect>>", lambda _event: self._load_selected_file())
        actions = tk.Frame(sidebar, bg=COLORS["sidebar"])
        actions.pack(fill="x", padx=14, pady=14)
        ttk.Button(actions, text="Open recording", style="Accent.TButton", command=self.open_file).pack(fill="x", pady=4)
        ttk.Button(actions, text="Choose folder", command=self.choose_folder).pack(fill="x", pady=4)
        ttk.Button(actions, text="Refresh", command=self.refresh_files).pack(fill="x", pady=4)

        main = tk.Frame(self, bg=COLORS["window"])
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=COLORS["window"])
        header.pack(fill="x", padx=26, pady=(20, 14))
        self.title_label = tk.Label(header, text="Select a recording", bg=COLORS["window"], fg=COLORS["text"], font=_font(22, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(header, text="Raw traces and separated electrochemical cycles", bg=COLORS["window"], fg=COLORS["muted"], font=_font(10))
        self.subtitle_label.pack(anchor="w", pady=(4, 0))

        self.metrics = tk.Frame(main, bg=COLORS["window"])
        self.metrics.pack(fill="x", padx=26, pady=(0, 14))
        self.metric_labels: dict[str, tk.Label] = {}
        for column, (key, label) in enumerate((("samples", "SAMPLES"), ("duration", "DURATION"), ("voltage", "POTENTIAL RANGE"), ("z", "Z RANGE"), ("cycles", "CV CYCLES"))):
            pane = tk.Frame(self.metrics, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
            pane.grid(row=0, column=column, sticky="ew", padx=(0, 9))
            self.metrics.grid_columnconfigure(column, weight=1)
            tk.Label(pane, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=_font(7, "bold")).pack(anchor="w", padx=12, pady=(9, 2))
            value = tk.Label(pane, text="—", bg=COLORS["panel"], fg=COLORS["text"], font=_font(14, "bold"))
            value.pack(anchor="w", padx=12, pady=(0, 9))
            self.metric_labels[key] = value

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, padx=26, pady=(0, 22))
        self._build_raw_tab()
        self._build_cv_tab()
        self._build_table_tab()
        self._build_metadata_tab()

    def _build_raw_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=COLORS["panel_2"])
        self.notebook.add(tab, text="Raw traces")
        controls = tk.Frame(tab, bg=COLORS["panel_2"])
        controls.pack(fill="x", padx=18, pady=(14, 7))
        tk.Label(controls, text="Signal", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(9)).pack(side="left")
        self.raw_signal = tk.StringVar(value="All currents")
        raw_combo = ttk.Combobox(controls, textvariable=self.raw_signal, values=("All currents", *RAW_SIGNALS), state="readonly", width=20)
        raw_combo.pack(side="left", padx=9)
        raw_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_raw_plot())
        tk.Label(controls, text="Unprocessed samples plotted against acquisition time", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(9)).pack(side="right")
        self.raw_current_plot = XYPlot(tab, "Elapsed time (s)", "Signal", height=300)
        self.raw_current_plot.pack(fill="both", expand=True, padx=12, pady=6)
        self.raw_context_plot = XYPlot(tab, "Elapsed time (s)", "Applied potential (V)", height=190)
        self.raw_context_plot.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_cv_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=COLORS["panel_2"])
        self.notebook.add(tab, text="Voltammograms")
        controls = tk.Frame(tab, bg=COLORS["panel_2"])
        controls.pack(fill="x", padx=18, pady=(14, 8))
        tk.Label(controls, text="Current channel", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(9)).pack(side="left")
        self.cv_current = tk.StringVar(value="Current 1")
        cv_combo = ttk.Combobox(controls, textvariable=self.cv_current, values=tuple(CURRENT_COLUMNS), state="readonly", width=16)
        cv_combo.pack(side="left", padx=9)
        cv_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_cv_view())
        ttk.Button(controls, text="Set CV program…", command=self._edit_cv_program).pack(side="left", padx=(14, 0))
        self.export_button = ttk.Button(controls, text="Export separated CVs", command=self.export_cycles)
        self.export_button.pack(side="right")
        body = tk.Frame(tab, bg=COLORS["panel_2"])
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        summary = Card(body, "Detected cycles", "Select one cycle or overlay all completed cycles.")
        summary.pack(side="left", fill="y", padx=(0, 10))
        self.cycle_tree = ttk.Treeview(summary.body, columns=("anodic", "cathodic"), show="tree headings", height=13)
        self.cycle_tree.heading("#0", text="Cycle")
        self.cycle_tree.heading("anodic", text="Max / nA")
        self.cycle_tree.heading("cathodic", text="Min / nA")
        self.cycle_tree.column("#0", width=75, anchor="w")
        self.cycle_tree.column("anodic", width=86, anchor="e")
        self.cycle_tree.column("cathodic", width=86, anchor="e")
        self.cycle_tree.pack(fill="both", expand=True)
        self.cycle_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_cv_plot())
        self.cv_detail = tk.Label(summary.body, text="", bg=COLORS["panel"], fg=COLORS["muted"], justify="left", wraplength=245, font=_font(9))
        self.cv_detail.pack(anchor="w", pady=(12, 0))
        self.cv_plot = XYPlot(body, "Applied potential (V)", "Current (nA)")
        self.cv_plot.pack(side="left", fill="both", expand=True)

    def _edit_cv_program(self) -> None:
        """Supply CV settings for a legacy recording whose SET lacks them."""
        if self.dataset is None:
            messagebox.showinfo("eChemTips Data Analysis", "Open a recording before setting its CV program.", parent=self)
            return
        existing = self.dataset.metadata.get("parameters")
        existing = existing if isinstance(existing, dict) else {}
        dialog = tk.Toplevel(self)
        dialog.title("Set CV program")
        dialog.configure(bg=COLORS["panel"])
        dialog.transient(self)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=20, pady=18)
        tk.Label(body, text="Enter the voltage program used for this recording.", bg=COLORS["panel"], fg=COLORS["text"], font=_font(10)).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 13))
        fields = (
            ("Start voltage", "cv_start_v", "V"),
            ("First vertex", "cv_vertex1_v", "V"),
            ("Second vertex", "cv_vertex2_v", "V"),
            ("Approach voltage", "approach_voltage_v", "V"),
            ("Number of cycles", "cycles", ""),
        )
        variables: dict[str, tk.StringVar] = {}
        for row, (label, key, unit) in enumerate(fields, 1):
            tk.Label(body, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).grid(row=row, column=0, sticky="w", pady=4)
            value = existing.get(key, "")
            if key == "approach_voltage_v" and value == "":
                value = existing.get("cv_start_v", "")
            variable = tk.StringVar(value=str(value))
            variables[key] = variable
            entry = tk.Entry(body, textvariable=variable, width=15, bg=COLORS["panel_2"], fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat")
            entry.grid(row=row, column=1, sticky="ew", padx=(16, 3), pady=4)
            if unit:
                tk.Label(body, text=unit, bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).grid(row=row, column=2, sticky="w", pady=4)
        status = tk.Label(body, text="", bg=COLORS["panel"], fg=COLORS["danger"], font=_font(9), wraplength=330, justify="left")
        status.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="w", pady=(9, 0))

        def apply() -> None:
            try:
                values = {key: float(variables[key].get().strip()) for _label, key, _unit in fields if key != "cycles"}
                if not all(math.isfinite(value) for value in values.values()):
                    raise ValueError("all voltage values must be finite")
                cycles_text = variables["cycles"].get().strip()
                cycle_value = float(cycles_text)
                if not math.isfinite(cycle_value) or not cycle_value.is_integer():
                    raise ValueError("number of cycles must be a whole number")
                cycles = int(cycle_value)
                if cycles < 1:
                    raise ValueError("number of cycles must be at least 1")
            except (TypeError, ValueError):
                status.configure(text="Enter finite voltages and a positive whole number of cycles.")
                return
            values["cycles"] = float(cycles)
            self.dataset.metadata["parameters"] = values
            self.dataset.metadata.setdefault("experiment", "Manual CV")
            self.cycles = extract_cv_cycles(self.dataset)
            dialog.destroy()
            self._refresh_all()

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.grid(row=len(fields) + 2, column=0, columnspan=3, sticky="e", pady=(15, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Apply", style="Accent.TButton", command=apply).pack(side="right")
        dialog.bind("<Return>", lambda _event: apply())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        next(iter(variables.values())).trace_add("write", lambda *_args: status.configure(text=""))

    def _build_table_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=COLORS["panel_2"])
        self.notebook.add(tab, text="Raw data table")
        self.table_status = tk.Label(tab, text="", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(9))
        self.table_status.pack(anchor="w", padx=14, pady=(12, 0))
        container = tk.Frame(tab, bg=COLORS["panel_2"])
        container.pack(fill="both", expand=True, padx=12, pady=(8, 12))
        self.data_table = ttk.Treeview(container, show="headings")
        vertical = ttk.Scrollbar(container, orient="vertical", command=self.data_table.yview)
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=self.data_table.xview)
        self.data_table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.data_table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

    def _build_metadata_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=COLORS["panel_2"])
        self.notebook.add(tab, text="Metadata")
        self.metadata_text = tk.Text(tab, bg=COLORS["panel"], fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat", font=("Menlo", 10), padx=16, pady=14)
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.metadata_text.yview)
        self.metadata_text.configure(yscrollcommand=scrollbar.set)
        self.metadata_text.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=12)

    def refresh_files(self) -> None:
        self.folder_label.configure(text=str(self.data_folder))
        supported = {".csv", ".tdms", ".tsv", ".set"}
        self.file_paths = (
            sorted((path for path in self.data_folder.iterdir() if path.is_file() and path.suffix.casefold() in supported), key=lambda path: path.stat().st_mtime, reverse=True)
            if self.data_folder.exists()
            else []
        )
        self.file_list.delete(0, "end")
        for path in self.file_paths:
            self.file_list.insert("end", path.stem.replace("_", " "))

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choose eChemTips data folder", initialdir=self.data_folder)
        if chosen:
            self.data_folder = Path(chosen).resolve()
            self.refresh_files()

    def open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Open eChemTips recording",
            initialdir=self.data_folder,
            filetypes=(("eChemTips recordings", "*.csv *.tdms *.tsv *.set"), ("CSV", "*.csv"), ("TDMS", "*.tdms"), ("LabVIEW TSV", "*.tsv"), ("LabVIEW SET + TSV", "*.set"), ("All files", "*")),
        )
        if chosen:
            self.load_recording(Path(chosen))

    def _load_selected_file(self) -> None:
        selection = self.file_list.curselection()
        if selection:
            self.load_recording(self.file_paths[selection[0]])

    def load_recording(self, path: Path) -> None:
        try:
            self.dataset = AnalysisDataset.load(path)
            self.cycles = extract_cv_cycles(self.dataset)
            self._refresh_all()
        except AnalysisError as exc:
            messagebox.showerror("eChemTips Data Analysis", str(exc), parent=self)

    def _refresh_all(self) -> None:
        assert self.dataset is not None
        dataset = self.dataset
        self.title_label.configure(text=dataset.path.stem.replace("_", " "))
        started = dataset.metadata.get("started_at", "Start time unavailable")
        self.subtitle_label.configure(text=f"{dataset.experiment} · {started} · {dataset.path}")
        voltage = dataset.values("voltage1_v")
        z_values = dataset.values("z_um")
        self.metric_labels["samples"].configure(text=f"{len(dataset.rows):,}")
        self.metric_labels["duration"].configure(text=f"{dataset.duration_s:.2f} s")
        self.metric_labels["voltage"].configure(text=f"{min(voltage):.3g} to {max(voltage):.3g} V")
        self.metric_labels["z"].configure(text=f"{min(z_values):.3g} to {max(z_values):.3g} um" if z_values else "—")
        self.metric_labels["cycles"].configure(text=str(len(self.cycles)) if self.cycles else "—")
        self._refresh_raw_plot()
        self._refresh_cv_view()
        self._refresh_table()
        self.metadata_text.configure(state="normal")
        self.metadata_text.delete("1.0", "end")
        self.metadata_text.insert("1.0", json.dumps(dataset.metadata, indent=2, default=str) if dataset.metadata else "No matching JSON metadata file was found.")
        self.metadata_text.configure(state="disabled")

    def _refresh_raw_plot(self) -> None:
        if self.dataset is None:
            return
        times = self.dataset.values("elapsed_s")
        selected = self.raw_signal.get()
        series: list[tuple[str, list[float], list[float], str]] = []
        if selected == "All currents":
            for index, (name, column) in enumerate(CURRENT_COLUMNS.items()):
                if column in self.dataset.columns:
                    series.append((name, times, self.dataset.values(column), PLOT_COLORS[index]))
            self.raw_current_plot.y_label = "Current (nA)"
        else:
            column = RAW_SIGNALS[selected]
            series.append((selected, times, self.dataset.values(column), COLORS["accent"]))
            self.raw_current_plot.y_label = "Phase (deg)" if column == "lockin_phase_deg" else "Signal (nA)"
        self.raw_current_plot.set_data(series)
        self.raw_context_plot.set_data([("Voltage 1", times, self.dataset.values("voltage1_v"), COLORS["blue"])])

    def _refresh_cv_view(self) -> None:
        for item in self.cycle_tree.get_children():
            self.cycle_tree.delete(item)
        if not self.cycles:
            self.cv_plot.set_message("No complete CV cycles detected in this recording")
            self.cv_detail.configure(text="Watch Current files retain their raw traces but do not contain a CV voltage program.")
            self.export_button.state(["disabled"])
            return
        self.export_button.state(["!disabled"])
        column = CURRENT_COLUMNS[self.cv_current.get()]
        self.cycle_tree.insert("", "end", iid="all", text="All", values=("", ""))
        for cycle in self.cycles:
            maximum, _max_v, minimum, _min_v = cycle.peak_summary(column)
            item_id = str(len(self.cycle_tree.get_children()))
            self.cycle_tree.insert("", "end", iid=item_id, text=cycle.label, values=(f"{maximum:.3g}", f"{minimum:.3g}"))
        self.cycle_tree.selection_set("all")
        self._refresh_cv_plot()

    def _refresh_cv_plot(self) -> None:
        if not self.cycles:
            return
        selection = self.cycle_tree.selection()
        selected = selection[0] if selection else "all"
        cycles = self.cycles if selected == "all" else [self.cycles[int(selected) - 1]]
        column = CURRENT_COLUMNS[self.cv_current.get()]
        series = [
            (cycle.label, cycle.potential_v, cycle.current_na(column), PLOT_COLORS[index % len(PLOT_COLORS)])
            for index, cycle in enumerate(cycles)
        ]
        self.cv_plot.set_data(series)
        if len(cycles) == 1:
            maximum, max_v, minimum, min_v = cycles[0].peak_summary(column)
            self.cv_detail.configure(text=f"Anodic maximum\n{maximum:+.4g} nA at {max_v:+.4g} V\n\nCathodic minimum\n{minimum:+.4g} nA at {min_v:+.4g} V")
        else:
            self.cv_detail.configure(text=f"Overlaying {len(cycles)} completed cycles.\n\nSelect a cycle for its peak summary.")

    def _refresh_table(self) -> None:
        if self.dataset is None:
            return
        table = self.data_table
        table.delete(*table.get_children())
        table.configure(columns=self.dataset.columns)
        for column in self.dataset.columns:
            table.heading(column, text=column)
            table.column(column, width=max(92, min(145, len(column) * 9)), anchor="e")
        display_limit = 5_000
        displayed_rows = self.dataset.rows[:display_limit]
        for row in displayed_rows:
            table.insert("", "end", values=tuple(f"{row[column]:.8g}" for column in self.dataset.columns))
        if len(self.dataset.rows) > display_limit:
            self.table_status.configure(
                text=f"Showing the first {display_limit:,} of {len(self.dataset.rows):,} rows. The source CSV remains complete."
            )
        else:
            self.table_status.configure(text=f"Showing all {len(self.dataset.rows):,} rows from the source CSV.")

    def export_cycles(self) -> None:
        if self.dataset is None or not self.cycles:
            return
        suggested = self.dataset.path.with_name(f"{self.dataset.path.stem}_separated_cvs.csv")
        chosen = filedialog.asksaveasfilename(
            title="Export separated CVs",
            initialdir=suggested.parent,
            initialfile=suggested.name,
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
        )
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
            messagebox.showerror("eChemTips Data Analysis", f"Could not export CVs: {exc}", parent=self)
            return
        messagebox.showinfo("eChemTips Data Analysis", f"Separated CVs saved to:\n{chosen}", parent=self)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Analyze eChemTips current and CV recordings")
    parser.add_argument("recording", nargs="?", help="CSV recording to open")
    parser.add_argument("--smoke-test", action="store_true", help="build and exercise the analysis UI, then exit")
    args = parser.parse_args()
    app = AnalysisApp(args.recording)
    if args.smoke_test:
        app.withdraw()
        app.update_idletasks()
        for tab_id in app.notebook.tabs():
            app.notebook.select(tab_id)
            app.update_idletasks()
        if app.dataset is not None and app.dataset.experiment == "Approach then CV" and not app.cycles:
            raise RuntimeError("Analysis UI did not extract any CV cycles from the supplied recording.")
        app.destroy()
        print("eChemTips analysis UI smoke test passed")
        return
    app.mainloop()


if __name__ == "__main__":
    main()
