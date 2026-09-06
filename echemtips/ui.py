from __future__ import annotations

import math
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .acquisition import AcquisitionDrain, AcquisitionWorker
from .backends import BackendError, InstrumentBackend, create_backend
from .data import DataRecorder
from .experiments import (
    ApproachCVExperiment, ApproachExperiment, ApproachITExperiment, CVExperiment,
    ExperimentState, ScanHoppingCVExperiment, ScanHoppingITExperiment,
)
from .host import DisplayBuffer
from .models import (
    HARDWARE_PROFILE,
    INSTRUMENT_PROFILES,
    AppSettings,
    ApproachCVParameters,
    ApproachITParameters,
    ApproachParameters,
    CVParameters,
    Sample,
    ScanHoppingCVParameters,
    ScanHoppingITParameters,
    SettingsStore,
)


COLORS = {
    "window": "#f3f6fa",
    "sidebar": "#17324d",
    "sidebar_active": "#244968",
    "sidebar_text": "#f8fbff",
    "sidebar_muted": "#bac8d6",
    "panel": "#ffffff",
    "panel_2": "#eef3f7",
    "border": "#d6e0e9",
    "text": "#172535",
    "muted": "#607386",
    "accent": "#138b83",
    "accent_dark": "#bcdedb",
    "blue": "#3578c4",
    "warning": "#b96c08",
    "danger": "#bf3d53",
    "success": "#20845d",
    "grid": "#dfe7ee",
}


def _font(size: int, weight: str = "normal") -> tuple[str, int, str]:
    return ("Helvetica Neue", size, weight)


class Card(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, subtitle: str = "", **kwargs: object) -> None:
        super().__init__(parent, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1, **kwargs)
        head = tk.Frame(self, bg=COLORS["panel"])
        head.pack(fill="x", padx=20, pady=(17, 10))
        title_label = tk.Label(head, text=title, bg=COLORS["panel"], fg=COLORS["text"], font=_font(13, "bold"), justify="left")
        title_label.pack(anchor="w")
        if subtitle:
            subtitle_label = tk.Label(
                head,
                text=subtitle,
                bg=COLORS["panel"],
                fg=COLORS["muted"],
                font=_font(10),
                justify="left",
            )
            subtitle_label.pack(anchor="w", pady=(4, 0))
            self.bind("<Configure>", lambda event: subtitle_label.configure(wraplength=max(160, event.width - 42)))
        self.body = tk.Frame(self, bg=COLORS["panel"])
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 18))


class ScrollableFrame(tk.Frame):
    """A responsive vertical viewport used by pages with long forms."""

    def __init__(self, parent: tk.Misc, width: int | None = None, **kwargs: object) -> None:
        super().__init__(parent, bg=COLORS["window"], **kwargs)
        self.canvas = tk.Canvas(self, bg=COLORS["window"], highlightthickness=0, width=width or 1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content = tk.Frame(self.canvas, bg=COLORS["window"])
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._update_scrollregion)
        self.canvas.bind("<Configure>", self._resize_content)
        self.bind("<Enter>", self._enable_wheel, add="+")
        self.bind("<Leave>", self._disable_wheel, add="+")

    def _update_scrollregion(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _enable_wheel(self, _event: tk.Event[tk.Misc]) -> None:
        setattr(self.winfo_toplevel(), "_scroll_owner", self)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", lambda _event: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda _event: self.canvas.yview_scroll(3, "units"))

    def _disable_wheel(self, _event: tk.Event[tk.Misc]) -> None:
        self.after_idle(self._disable_wheel_if_outside)

    def _disable_wheel_if_outside(self) -> None:
        root = self.winfo_toplevel()
        if getattr(root, "_scroll_owner", None) is not self:
            return
        x, y = self.winfo_pointerxy()
        widget = self.winfo_containing(x, y)
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is self:
            return
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")
        setattr(root, "_scroll_owner", None)

    def _on_mousewheel(self, event: tk.Event[tk.Misc]) -> None:
        delta = int(getattr(event, "delta", 0))
        if delta:
            self.canvas.yview_scroll(-1 if delta > 0 else 1, "units")


class Field:
    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        value: str,
        unit: str = "",
        width: int = 8,
        row: int = 0,
        column: int = 0,
        pack: bool = False,
    ) -> None:
        frame = tk.Frame(parent, bg=COLORS["panel"])
        if pack:
            frame.pack(fill="x", pady=7)
        else:
            frame.grid(row=row, column=column, sticky="ew", padx=(0, 14), pady=7)
        label_widget = tk.Label(frame, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9), justify="left")
        label_widget.pack(anchor="w", pady=(0, 5))
        frame.bind("<Configure>", lambda event: label_widget.configure(wraplength=max(90, event.width - 4)))
        value_frame = tk.Frame(frame, bg=COLORS["panel_2"], highlightbackground=COLORS["border"], highlightthickness=1)
        value_frame.pack(fill="x")
        self.variable = tk.StringVar(value=value)
        self.entry = tk.Entry(
            value_frame,
            textvariable=self.variable,
            bg=COLORS["panel_2"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=_font(11),
            width=width,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=8)
        self.unit_label: tk.Label | None = None
        if unit:
            self.unit_label = tk.Label(value_frame, text=unit, bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(9))
            self.unit_label.pack(side="right", padx=(2, 9))

    def float(self) -> float:
        return float(self.variable.get().strip())

    def integer(self) -> int:
        return int(self.variable.get().strip())

    def optional_float(self) -> float | None:
        value = self.variable.get().strip()
        return float(value) if value else None

    def set_unit(self, unit: str) -> None:
        if self.unit_label is not None:
            self.unit_label.configure(text=unit)


class Plot(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        y_label: str,
        colors: tuple[str, ...],
        max_points: int = 8_000,
        x_label: str = "Elapsed time (s)",
    ) -> None:
        super().__init__(parent, bg=COLORS["panel"], highlightthickness=0, height=300)
        self.title = title
        self.y_label = y_label
        self.x_label = x_label
        self.line_colors = colors
        self.max_points = max(250, max_points)
        self.buffer = DisplayBuffer(len(colors), self.max_points)
        self.series = self.buffer.series
        self.x_values = self.buffer.x
        self.bind("<Configure>", lambda _event: self.redraw())

    def clear(self) -> None:
        self.buffer.clear()
        self.redraw()

    def append(self, x: float, *values: float, redraw: bool = True) -> None:
        if not self.buffer.append(x, values):
            return
        if redraw:
            self.redraw()

    def _compact(self) -> None:
        """Keep the whole time span while reducing old display resolution."""
        self.buffer.compact()

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(10, self.winfo_width()), max(10, self.winfo_height())
        left, top, right, bottom = 62, 40, width - 18, height - 38
        self.create_text(left, 15, text=self.title, fill=COLORS["text"], font=_font(11, "bold"), anchor="nw")
        self.create_text(18, top + (bottom - top) / 2, text=self.y_label, fill=COLORS["muted"], font=_font(9), angle=90)
        if right <= left or bottom <= top:
            return
        for i in range(5):
            y = top + i * (bottom - top) / 4
            self.create_line(left, y, right, y, fill=COLORS["grid"])
        self.create_line(left, bottom, right, bottom, fill=COLORS["muted"])
        self.create_text((left + right) / 2, height - 12, text=self.x_label, fill=COLORS["muted"], font=_font(9))
        if len(self.x_values) < 2:
            self.create_text((left + right) / 2, (top + bottom) / 2, text="Waiting for data", fill=COLORS["muted"], font=_font(11))
            return
        all_values = [value for series in self.series for value in series]
        low, high = min(all_values), max(all_values)
        padding = max((high - low) * 0.12, 0.05)
        low, high = low - padding, high + padding
        x0, x1 = min(self.x_values), max(self.x_values)
        xspan = max(x1 - x0, 1e-9)
        yspan = max(high - low, 1e-9)
        for i in range(5):
            value = high - i * yspan / 4
            y = top + i * (bottom - top) / 4
            self.create_text(left - 8, y, text=f"{value:.2f}", fill=COLORS["muted"], font=_font(8), anchor="e")
        render_limit = max(300, width * 2)
        stride = max(1, math.ceil(len(self.x_values) / render_limit))
        render_indices = list(range(0, len(self.x_values), stride))
        if render_indices[-1] != len(self.x_values) - 1:
            render_indices.append(len(self.x_values) - 1)
        for values, color in zip(self.series, self.line_colors):
            coords: list[float] = []
            for index in render_indices:
                x, value = self.x_values[index], values[index]
                coords.extend((left + (x - x0) / xspan * (right - left), bottom - (value - low) / yspan * (bottom - top)))
            if len(coords) >= 4:
                self.create_line(*coords, fill=color, width=2, smooth=True)


class Heatmap(tk.Canvas):
    def __init__(self, parent: tk.Misc, unit: str) -> None:
        super().__init__(parent, bg=COLORS["panel"], highlightthickness=0, height=220)
        self.unit = unit
        self.rows = 1
        self.columns = 1
        self.values: dict[tuple[int, int], float] = {}
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_data(self, values: dict[tuple[int, int], float], rows: int, columns: int) -> None:
        self.values = dict(values)
        self.rows = max(1, rows)
        self.columns = max(1, columns)
        self.redraw()

    @staticmethod
    def _color(fraction: float) -> str:
        fraction = max(0.0, min(1.0, fraction))
        low, high = (225, 238, 242), (19, 139, 131)
        rgb = tuple(round(a + (b - a) * fraction) for a, b in zip(low, high))
        return "#" + "".join(f"{value:02x}" for value in rgb)

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(20, self.winfo_width()), max(20, self.winfo_height())
        left, top, right, bottom = 38, 16, width - 14, height - 32
        finite = list(self.values.values())
        low, high = (min(finite), max(finite)) if finite else (0.0, 1.0)
        span = max(high - low, 1e-12)
        cell_w = (right - left) / self.columns
        cell_h = (bottom - top) / self.rows
        for row in range(self.rows):
            for column in range(self.columns):
                x0, y0 = left + column * cell_w, bottom - (row + 1) * cell_h
                value = self.values.get((row, column))
                fill = COLORS["panel_2"] if value is None else self._color((value - low) / span)
                self.create_rectangle(x0, y0, x0 + cell_w, y0 + cell_h, fill=fill, outline=COLORS["border"])
                if value is not None and cell_w > 36 and cell_h > 24:
                    fraction = (value - low) / span
                    text_color = "#ffffff" if fraction > 0.58 else COLORS["text"]
                    self.create_text(x0 + cell_w / 2, y0 + cell_h / 2, text=f"{value:.3g}", fill=text_color, font=_font(8))
        self.create_text(left, height - 10, text="X →", fill=COLORS["muted"], anchor="w", font=_font(8))
        self.create_text(8, top, text="Y", fill=COLORS["muted"], anchor="nw", font=_font(8))
        label = "Waiting for contact data" if not finite else f"{low:.3g} to {high:.3g} {self.unit}"
        self.create_text(right, height - 10, text=label, fill=COLORS["muted"], anchor="e", font=_font(8))


class BasePage(tk.Frame):
    def __init__(self, app: "EChemTipsApp", title: str, description: str) -> None:
        super().__init__(app.content, bg=COLORS["window"])
        self.app = app
        heading = tk.Frame(self, bg=COLORS["window"])
        heading.pack(fill="x", pady=(4, 20))
        tk.Label(heading, text=title, bg=COLORS["window"], fg=COLORS["text"], font=_font(24, "bold")).pack(anchor="w")
        description_label = tk.Label(
            heading, text=description, bg=COLORS["window"], fg=COLORS["muted"],
            font=_font(11), justify="left",
        )
        description_label.pack(anchor="w", pady=(5, 0))
        heading.bind("<Configure>", lambda event: description_label.configure(wraplength=max(240, event.width - 8)))

    def on_sample(self, _sample: Sample) -> None:
        pass

    def on_samples(self, samples: list[Sample]) -> None:
        if samples:
            self.on_sample(samples[-1])


class WatchPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Watch current", "Live current and position monitoring with voltage control.")
        body = tk.Frame(self, bg=COLORS["window"])
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        control = Card(body, "Output control", "Voltage changes are limited to +/-10 V and recorded with the trace.")
        control.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        self.v1 = Field(control.body, "Voltage 1 · AO3", "0.10", "V", row=0)
        self.v2 = Field(control.body, "Voltage 2 · AO4", "0.00", "V", row=1)
        ttk.Button(control.body, text="Apply voltages", style="Accent.TButton", command=self.apply_voltage).grid(row=2, column=0, sticky="ew", pady=(12, 6))
        self.start_recording_button = ttk.Button(control.body, text="Start recording", command=self.start_recording)
        self.start_recording_button.grid(row=3, column=0, sticky="ew", pady=6)
        self.stop_recording_button = ttk.Button(control.body, text="Stop & save", style="Danger.TButton", command=self.stop_recording)
        self.stop_recording_button.grid(row=4, column=0, sticky="ew", pady=6)
        self.stop_recording_button.state(["disabled"])
        self.live_enabled = True
        self.live_button = ttk.Button(control.body, text="Pause live view", style="Quiet.TButton", command=self.toggle_live_view)
        self.live_button.grid(row=5, column=0, sticky="ew", pady=6)
        ttk.Button(control.body, text="Clear graph", style="Quiet.TButton", command=lambda: self.plot.clear()).grid(row=6, column=0, sticky="ew", pady=6)

        readout = tk.Frame(control.body, bg=COLORS["panel"])
        readout.grid(row=7, column=0, sticky="ew", pady=(22, 0))
        tk.Label(readout, text="LIVE READOUT", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(8, "bold")).pack(anchor="w")
        self.current_label = tk.Label(readout, text="— nA", bg=COLORS["panel"], fg=COLORS["accent"], font=_font(22, "bold"))
        self.current_label.pack(anchor="w", pady=(5, 0))
        self.position_label = tk.Label(readout, text="Z  — um", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(10))
        self.position_label.pack(anchor="w", pady=(5, 0))

        chart_card = Card(body, "Current history", "Current 1 (teal), Current 2 (blue), Current 3 (pink), Current 4 (gold)")
        chart_card.grid(row=0, column=1, sticky="nsew")
        self.plot = Plot(
            chart_card.body,
            "Current channels",
            "Current (nA)",
            (COLORS["accent"], COLORS["blue"], COLORS["danger"], COLORS["warning"]),
            max_points=app.settings.display_max_points,
        )
        self.plot.pack(fill="both", expand=True)

    def apply_voltage(self) -> None:
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("Stop the experiment before changing manual outputs.")
            voltage1, voltage2 = self.v1.float(), self.v2.float()
            if not all(math.isfinite(value) and -10 <= value <= 10 for value in (voltage1, voltage2)):
                raise ValueError("Both voltage outputs must be finite values between -10 V and +10 V.")
            if self.app.settings.mode == "NI FPGA" and abs(voltage1 * self.app.settings.command_voltage_ratio) > 10:
                raise ValueError("Voltage 1 exceeds the AO3 range at the configured command ratio.")
            self.app.backend.set_live_potential(1, voltage1)
            self.app.backend.set_live_potential(2, voltage2)
            self.app.toast("Voltage outputs updated", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def _can_control_recording(self) -> bool:
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("The active experiment owns the recording. Use its Stop button.")
        except BackendError as exc:
            self.app.show_error(str(exc))
            return False
        return True

    def start_recording(self) -> None:
        if not self._can_control_recording():
            return
        try:
            if self.app.recorder.active:
                raise BackendError("A recording is already active.")
            self.app.recorder.start("Watch Current", self.app.settings)
            self.app._sync_action_states()
            self.app.toast("Recording current channels", "success")
        except (BackendError, OSError, ValueError) as exc:
            self.app.show_error(f"Could not start the recording: {exc}")

    def stop_recording(self) -> None:
        if not self._can_control_recording():
            return
        try:
            if not self.app.recorder.active or self.app.recorder.name != "Watch Current":
                raise BackendError("There is no Watch Current recording to stop.")
            self.app.flush_acquisition()
            path = self.app.finish_recording()
            self.app._sync_action_states()
            self.app.toast(f"Saved {path.name}" if path else "Recording stopped", "success")
        except (BackendError, OSError, ValueError) as exc:
            self.app.show_error(f"Could not stop the recording: {exc}")

    def toggle_recording(self) -> None:
        """Compatibility entry point for callers using the former toggle."""
        if self.app.recorder.active:
            self.stop_recording()
        else:
            self.start_recording()

    def toggle_live_view(self) -> None:
        self.live_enabled = not self.live_enabled
        self.live_button.configure(text="Pause live view" if self.live_enabled else "Resume live view")
        self.app.toast("Live view resumed" if self.live_enabled else "Live view paused")

    def on_sample(self, sample: Sample) -> None:
        self.current_label.configure(text=f"{sample.current1_na:+.3f} nA")
        self.position_label.configure(text=f"Z  {sample.z_um:.3f} um")
        if self.live_enabled:
            self.plot.append(sample.elapsed_s, sample.current1_na, sample.current2_na, sample.current3_na, sample.current4_na)

    def on_samples(self, samples: list[Sample]) -> None:
        if not samples:
            return
        latest = samples[-1]
        self.current_label.configure(text=f"{latest.current1_na:+.3f} nA")
        self.position_label.configure(text=f"Z  {latest.z_um:.3f} um")
        if self.live_enabled:
            for sample in samples:
                self.plot.append(
                    sample.elapsed_s, sample.current1_na, sample.current2_na,
                    sample.current3_na, sample.current4_na, redraw=False,
                )
            self.plot.redraw()


class ApproachCVPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach then CV", "Detect surface contact, run cyclic voltammetry, then retract safely.")
        body = tk.Frame(self, bg=COLORS["window"])
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        control_host = ScrollableFrame(body, width=370)
        control_host.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 16))
        inputs = control_host.content
        approach = Card(inputs, "1 · Approach", "Z motion stops when the selected signal crosses the threshold.")
        approach.pack(fill="x", pady=(0, 12))
        for column in range(2):
            approach.body.grid_columnconfigure(column, weight=1)
        self.start_z = Field(approach.body, "Start Z", "10.0", "um", row=0, column=0)
        self.end_z = Field(approach.body, "End Z", "90.0", "um", row=0, column=1)
        self.approach_rate = Field(approach.body, "Approach rate", "3.0", "um/s", row=1, column=0)
        self.approach_voltage = Field(approach.body, "Approach voltage", "0.10", "V", row=1, column=1)
        self.feedback_channel = tk.StringVar(value="Current 1")
        self._combo(
            approach.body,
            "Feedback signal",
            self.feedback_channel,
            ("Current 1", "Current 2", "Current 3", "Current 4", "Lock-in amplitude", "Lock-in phase"),
            2,
            0,
        )
        self.threshold = Field(approach.body, "Contact threshold", "2.0", "nA", row=2, column=1)
        self.feedback_channel.trace_add("write", lambda *_: self._update_threshold_unit())
        self.greater_than = tk.BooleanVar(value=True)
        ttk.Checkbutton(approach.body, text="Trigger when signal is greater than threshold", variable=self.greater_than).grid(row=3, column=0, columnspan=2, sticky="w", pady=7)

        cv = Card(inputs, "2 · Cyclic voltammetry", "Potential is swept start → vertex 1 → vertex 2 → start.")
        cv.pack(fill="x")
        for column in range(2):
            cv.body.grid_columnconfigure(column, weight=1)
        self.cv_start = Field(cv.body, "Start", "-0.20", "V", row=0, column=0)
        self.vertex1 = Field(cv.body, "Vertex 1", "0.60", "V", row=0, column=1)
        self.vertex2 = Field(cv.body, "Vertex 2", "-0.40", "V", row=1, column=0)
        self.scan_rate = Field(cv.body, "Scan rate", "0.25", "V/s", row=1, column=1)
        self.cycles = Field(cv.body, "Cycles", "2", row=2, column=0)
        self.retract = tk.BooleanVar(value=True)
        ttk.Checkbutton(cv.body, text="Retract to start Z after CV", variable=self.retract).grid(row=2, column=1, sticky="w", pady=7)

        status = Card(body, "Experiment status")
        status.grid(row=0, column=1, sticky="ew", pady=(0, 16))
        top = tk.Frame(status.body, bg=COLORS["panel"])
        top.pack(fill="x")
        self.state_label = tk.Label(top, text="Ready", bg=COLORS["panel"], fg=COLORS["accent"], font=_font(16, "bold"))
        self.state_label.pack(side="left")
        self.detail_label = tk.Label(
            status.body, text="Configure the approach and CV, then start.", bg=COLORS["panel"],
            fg=COLORS["muted"], font=_font(10), justify="left",
        )
        self.detail_label.pack(anchor="w", pady=(7, 12))
        status.body.bind("<Configure>", lambda event: self.detail_label.configure(wraplength=max(180, event.width - 8)))
        self.progress = ttk.Progressbar(status.body, maximum=1.0, style="Teal.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 14))
        actions = tk.Frame(status.body, bg=COLORS["panel"])
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Start approach + CV", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", style="Danger.TButton", command=self.stop)
        self.stop_button.pack(side="left", padx=10)
        self.stop_button.state(["disabled"])

        visuals = tk.Frame(body, bg=COLORS["window"])
        visuals.grid(row=1, column=1, sticky="nsew")
        for column in range(2):
            visuals.grid_columnconfigure(column, weight=1, uniform="approach_plots")
        for row in range(2):
            visuals.grid_rowconfigure(row, weight=1, uniform="approach_plots")
        z_card = Card(visuals, "Z position", "Full experiment history, including pre-positioning, approach, CV, and retraction.")
        z_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self.z_plot = Plot(z_card.body, "Z vs time", "Z (um)", (COLORS["accent"],), max_points=app.settings.display_max_points)
        self.z_plot.configure(height=170)
        self.z_plot.pack(fill="both", expand=True)
        current_card = Card(visuals, "Current 1", "Full experiment current trace.")
        current_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        self.current_plot = Plot(current_card.body, "Current vs time", "Current 1 (nA)", (COLORS["blue"],), max_points=app.settings.display_max_points)
        self.current_plot.configure(height=170)
        self.current_plot.pack(fill="both", expand=True)
        cv_card = Card(visuals, "Cyclic voltammogram", "Only samples acquired during the CV waypoint segment are shown.")
        cv_card.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        self.cv_plot = Plot(
            cv_card.body, "Potential 1 vs Current 1", "Current 1 (nA)",
            (COLORS["danger"],), max_points=app.settings.display_max_points, x_label="Potential 1 (V)",
        )
        self.cv_plot.configure(height=180)
        self.cv_plot.pack(fill="both", expand=True)
        # Former combined plot name retained for small external integrations.
        self.plot = self.current_plot

    def _combo(self, parent: tk.Misc, label: str, variable: tk.StringVar, values: tuple[str, ...], row: int, column: int) -> None:
        frame = tk.Frame(parent, bg=COLORS["panel"])
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 14), pady=7)
        tk.Label(frame, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(0, 5))
        ttk.Combobox(frame, textvariable=variable, values=values, state="readonly", width=18).pack(fill="x")

    def _update_threshold_unit(self) -> None:
        self.threshold.set_unit("deg" if self.feedback_channel.get() == "Lock-in phase" else "nA")

    def parameters(self) -> ApproachCVParameters:
        return ApproachCVParameters(
            start_z_um=self.start_z.float(),
            end_z_um=self.end_z.float(),
            approach_rate_um_s=self.approach_rate.float(),
            approach_voltage_v=self.approach_voltage.float(),
            feedback_channel=self.feedback_channel.get(),
            feedback_threshold_na=self.threshold.float(),
            greater_than=self.greater_than.get(),
            cv_start_v=self.cv_start.float(),
            cv_vertex1_v=self.vertex1.float(),
            cv_vertex2_v=self.vertex2.float(),
            cv_scan_rate_v_s=self.scan_rate.float(),
            cycles=self.cycles.integer(),
            retract_after=self.retract.get(),
        )

    def start(self) -> None:
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("An experiment is already running.")
            if self.app.recorder.active:
                raise BackendError("Stop the current recording before starting an approach.")
            params = self.parameters()
            self.app.recorder.start("Approach then CV", self.app.settings, params)
            try:
                self.app.experiment.start(params)
            except Exception:
                self.app.recorder.discard()
                raise
            self.z_plot.clear()
            self.current_plot.clear()
            self.cv_plot.clear()
            self.start_button.state(["disabled"])
            self.stop_button.state(["!disabled"])
            self.state_label.configure(text="Starting")
            self.detail_label.configure(text="Preparing the approach sequence.")
            self.progress["value"] = 0.0
            self.app.toast("Approach sequence started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def stop(self) -> None:
        if self.app.experiment.active:
            self.app.stop_experiment("approach")

    def _show_update(self, update: object | None) -> None:
        if update:
            self.state_label.configure(text=update.state.value)
            self.detail_label.configure(text=update.detail)
            self.progress["value"] = update.progress

    def poll_status(self, sample: Sample) -> None:
        update = self.app.experiment.tick(sample) if self.app.experiment.active else None
        self._show_update(update)

    def on_sample(self, sample: Sample) -> None:
        self.on_samples([sample])

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.app.experiment
        if not samples:
            return
        if not experiment.active:
            return
        hardware = experiment._hardware_sequence
        last_update = None
        cv_changed = False
        for sample in samples:
            state_before = experiment.state
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            stage = self.app.backend.hardware_approach_context(sample.line_number) if hardware else (
                "cv" if state_before == ExperimentState.CV else ""
            )
            if stage == "cv" or (not stage and state_before == ExperimentState.CV):
                self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False)
                cv_changed = True
            if not hardware and experiment.active:
                last_update = experiment.tick(sample)
        if hardware and experiment.active:
            last_update = experiment.tick(samples[-1])
        self.z_plot.redraw()
        self.current_plot.redraw()
        if cv_changed:
            self.cv_plot.redraw()
        self._show_update(last_update)
        if experiment.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
            self.start_button.state(["!disabled"])
            self.stop_button.state(["disabled"])


class ScanHoppingCVPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Scan hopping + CV", "Approach, acquire a CV, retract, and repeat over a serpentine XY grid.")
        body = tk.Frame(self, bg=COLORS["window"])
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        control_host = ScrollableFrame(body, width=410)
        control_host.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 14))
        controls = Card(control_host.content, "Experiment plan", "Current map values are selected from each measured CV at the requested potential.")
        controls.pack(fill="x")
        for column in range(2):
            controls.body.grid_columnconfigure(column, weight=1)
        self.x_start = Field(controls.body, "X start", "35", "um", row=0, column=0)
        self.x_end = Field(controls.body, "X end", "65", "um", row=0, column=1)
        self.x_points = Field(controls.body, "X points", "3", row=1, column=0)
        self.y_points = Field(controls.body, "Y points", "3", row=1, column=1)
        self.y_start = Field(controls.body, "Y start", "35", "um", row=2, column=0)
        self.y_end = Field(controls.body, "Y end", "65", "um", row=2, column=1)
        self.start_z = Field(controls.body, "Retracted Z", "55", "um", row=3, column=0)
        self.end_z = Field(controls.body, "Approach limit Z", "80", "um", row=3, column=1)
        self.lateral_rate = Field(controls.body, "XY rate", "50", "um/s", row=4, column=0)
        self.approach_rate = Field(controls.body, "Approach rate", "15", "um/s", row=4, column=1)
        self.retract_rate = Field(controls.body, "Retract rate", "50", "um/s", row=5, column=0)
        self.threshold = Field(controls.body, "Current 1 threshold", "2", "nA", row=5, column=1)
        self.approach_v = Field(controls.body, "Approach potential", "0.1", "V", row=6, column=0)
        self.map_v = Field(controls.body, "Current-map potential", "0.2", "V", row=6, column=1)
        self.cv_start = Field(controls.body, "CV start", "-0.2", "V", row=7, column=0)
        self.vertex1 = Field(controls.body, "CV vertex 1", "0.6", "V", row=7, column=1)
        self.vertex2 = Field(controls.body, "CV vertex 2", "-0.4", "V", row=8, column=0)
        self.scan_rate = Field(controls.body, "CV scan rate", "2", "V/s", row=8, column=1)
        self.cycles = Field(controls.body, "CV cycles", "1", row=9, column=0)
        self.serpentine = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls.body, text="Serpentine rows", variable=self.serpentine).grid(row=9, column=1, sticky="w", pady=7)
        actions = tk.Frame(controls.body, bg=COLORS["panel"])
        actions.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.start_button = ttk.Button(actions, text="Start scan", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(actions, text="Stop", style="Danger.TButton", command=self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.state(["disabled"])

        status = Card(body, "Scan status")
        status.grid(row=0, column=1, sticky="ew", pady=(0, 14))
        self.state_label = tk.Label(status.body, text="Ready", bg=COLORS["panel"], fg=COLORS["accent"], font=_font(15, "bold"))
        self.state_label.pack(anchor="w")
        self.detail_label = tk.Label(
            status.body, text="Configure a grid, then start.", bg=COLORS["panel"],
            fg=COLORS["muted"], font=_font(9), justify="left",
        )
        self.detail_label.pack(anchor="w", pady=(4, 8))
        status.body.bind("<Configure>", lambda event: self.detail_label.configure(wraplength=max(180, event.width - 8)))
        self.progress = ttk.Progressbar(status.body, maximum=1.0, style="Teal.Horizontal.TProgressbar")
        self.progress.pack(fill="x")

        self.visual_tabs = ttk.Notebook(body)
        self.visual_tabs.grid(row=1, column=1, sticky="nsew")

        traces = tk.Frame(self.visual_tabs, bg=COLORS["window"], padx=4, pady=4)
        traces.grid_columnconfigure(0, weight=1, uniform="scan_traces")
        traces.grid_columnconfigure(1, weight=1, uniform="scan_traces")
        traces.grid_rowconfigure(0, weight=1)
        self.visual_tabs.add(traces, text="Experiment traces")
        z_trace_card = Card(traces, "Z position", "Full scan history across every pixel, including approaches and retractions.")
        z_trace_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.z_plot = Plot(z_trace_card.body, "Z vs time", "Z (um)", (COLORS["accent"],), max_points=app.settings.display_max_points)
        self.z_plot.configure(height=160)
        self.z_plot.pack(fill="both", expand=True)
        current_trace_card = Card(traces, "Current 1", "Full scan current history across every pixel.")
        current_trace_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.current_plot = Plot(current_trace_card.body, "Current vs time", "Current 1 (nA)", (COLORS["blue"],), max_points=app.settings.display_max_points)
        self.current_plot.configure(height=160)
        self.current_plot.pack(fill="both", expand=True)

        cv_tab = tk.Frame(self.visual_tabs, bg=COLORS["window"], padx=4, pady=4)
        cv_tab.grid_columnconfigure(0, weight=1)
        cv_tab.grid_rowconfigure(1, weight=1)
        self.visual_tabs.add(cv_tab, text="CV at pixel")
        self.cv_pixel_label = tk.Label(
            cv_tab, text="Waiting for a CV", bg=COLORS["window"], fg=COLORS["muted"], font=_font(10, "bold")
        )
        self.cv_pixel_label.grid(row=0, column=0, sticky="w", pady=(4, 8))
        cv_card = Card(cv_tab, "Cyclic voltammogram", "Only CV samples from the latest pixel are displayed.")
        cv_card.grid(row=1, column=0, sticky="nsew")
        self.cv_plot = Plot(
            cv_card.body, "Potential 1 vs Current 1", "Current 1 (nA)",
            (COLORS["danger"],), max_points=app.settings.display_max_points, x_label="Potential 1 (V)",
        )
        self.cv_plot.pack(fill="both", expand=True)

        maps = tk.Frame(self.visual_tabs, bg=COLORS["window"], padx=4, pady=4)
        maps.grid_columnconfigure(0, weight=1, uniform="scan_maps")
        maps.grid_columnconfigure(1, weight=1, uniform="scan_maps")
        maps.grid_rowconfigure(0, weight=1)
        self.visual_tabs.add(maps, text="Maps")
        z_card = Card(maps, "Z contact map", "Feedback crossing height at each XY pixel.")
        z_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.z_map = Heatmap(z_card.body, "um")
        self.z_map.pack(fill="both", expand=True)
        current_card = Card(maps, "Current map", "Current 1 sampled from each CV at the fixed map potential.")
        current_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.current_map = Heatmap(current_card.body, "nA")
        self.current_map.pack(fill="both", expand=True)
        # Compatibility alias: this previously referred to a latest-approach plot.
        self.approach_plot = self.z_plot
        self._last_state = ExperimentState.IDLE
        self._cv_point = -1

    def parameters(self) -> ScanHoppingCVParameters:
        return ScanHoppingCVParameters(
            x_start_um=self.x_start.float(), x_end_um=self.x_end.float(), x_points=self.x_points.integer(),
            y_start_um=self.y_start.float(), y_end_um=self.y_end.float(), y_points=self.y_points.integer(),
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), lateral_rate_um_s=self.lateral_rate.float(),
            approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_threshold_na=self.threshold.float(),
            cv_start_v=self.cv_start.float(), cv_vertex1_v=self.vertex1.float(), cv_vertex2_v=self.vertex2.float(),
            cv_scan_rate_v_s=self.scan_rate.float(), cycles=self.cycles.integer(), map_potential_v=self.map_v.float(),
            serpentine=self.serpentine.get(),
        )

    def start(self) -> None:
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("Another experiment is already running.")
            if self.app.recorder.active:
                raise BackendError("Stop the current recording before starting a scan.")
            params = self.parameters()
            self.app.recorder.start("Scan Hopping CV", self.app.settings, params)
            try:
                self.app.scan_experiment.start(params)
            except Exception:
                self.app.recorder.discard()
                raise
            self.z_plot.clear()
            self.current_plot.clear()
            self.cv_plot.clear()
            self.cv_pixel_label.configure(text="Waiting for a CV")
            self.z_map.set_data({}, params.y_points, params.x_points)
            self.current_map.set_data({}, params.y_points, params.x_points)
            self.start_button.state(["disabled"])
            self.stop_button.state(["!disabled"])
            self.state_label.configure(text="Starting")
            self.detail_label.configure(text="Preparing the scan waypoint sequence.")
            self.progress["value"] = 0.0
            self._last_state = self.app.scan_experiment.state
            self._cv_point = -1
            self.app.toast(f"Scan started · {params.point_count} pixels", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def stop(self) -> None:
        if self.app.scan_experiment.active:
            self.app.stop_experiment("scan")

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.app.scan_experiment
        state_before = experiment.state
        point_before = experiment.point_index
        hardware = experiment._hardware
        update = experiment.tick_samples(samples)
        if update is None:
            return
        cv_changed = False
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            if hardware:
                point_index, stage = self.app.backend.hardware_scan_context(sample.line_number)
            else:
                point_index = sample.scan_pixel if sample.scan_pixel >= 0 else point_before
                stage = "cv" if state_before == ExperimentState.CV else ""
            if stage == "cv" and point_index >= 0:
                if point_index != self._cv_point:
                    self.cv_plot.clear()
                    self._cv_point = point_index
                    params = experiment.params
                    row, column = params.grid()[point_index][:2]
                    self.cv_pixel_label.configure(text=f"Pixel {point_index + 1} · row {row + 1}, column {column + 1}")
                self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False)
                cv_changed = True
        if samples:
            self.z_plot.redraw()
            self.current_plot.redraw()
        if cv_changed:
            self.cv_plot.redraw()
        params = experiment.params
        self.z_map.set_data(experiment.contact_z, params.y_points, params.x_points)
        self.current_map.set_data(experiment.current_at_potential, params.y_points, params.x_points)
        self.state_label.configure(text=update.state.value)
        self.detail_label.configure(text=update.detail)
        self.progress["value"] = update.progress
        if update.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
            self.start_button.state(["!disabled"])
            self.stop_button.state(["disabled"])
        self._last_state = update.state


class ManagedExperimentPage(BasePage):
    experiment_key = ""
    recording_name = ""

    def _build_status(self, parent: tk.Misc, title: str, start_text: str) -> None:
        status = Card(parent, title)
        status.grid(row=0, column=1, sticky="ew", pady=(0, 14))
        self.state_label = tk.Label(status.body, text="Ready", bg=COLORS["panel"], fg=COLORS["accent"], font=_font(15, "bold"))
        self.state_label.pack(anchor="w")
        self.detail_label = tk.Label(status.body, text="Configure the method, then start.", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9), justify="left")
        self.detail_label.pack(anchor="w", pady=(4, 8))
        status.body.bind("<Configure>", lambda event: self.detail_label.configure(wraplength=max(180, event.width - 8)))
        self.progress = ttk.Progressbar(status.body, maximum=1.0, style="Teal.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 10))
        actions = tk.Frame(status.body, bg=COLORS["panel"])
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text=start_text, style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", style="Danger.TButton", command=self.stop)
        self.stop_button.pack(side="left", padx=8)
        self.stop_button.state(["disabled"])

    @property
    def experiment(self):
        return self.app.experiments[self.experiment_key]

    def _begin(self, parameters: object) -> None:
        self.app.require_connection()
        if self.app.any_experiment_active:
            raise BackendError("Another experiment is already running.")
        if self.app.recorder.active:
            raise BackendError("Stop the current recording before starting an experiment.")
        self.app.recorder.start(self.recording_name, self.app.settings, parameters)
        try:
            self.experiment.start(parameters)
        except Exception:
            self.app.recorder.discard()
            raise
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.state_label.configure(text="Starting")
        self.detail_label.configure(text="Preparing the FPGA/simulation sequence.")
        self.progress["value"] = 0.0
        self.app._sync_action_states()

    def stop(self) -> None:
        if self.experiment.active:
            self.app.stop_experiment(self.experiment_key)

    def _show_update(self, update: object | None) -> None:
        if update is not None:
            self.state_label.configure(text=update.state.value)
            self.detail_label.configure(text=update.detail)
            self.progress["value"] = update.progress
            if update.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
                self.start_button.state(["!disabled"])
                self.stop_button.state(["disabled"])


class StandaloneCVPage(ManagedExperimentPage):
    experiment_key = "cv"
    recording_name = "CV"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "CV", "Run cyclic voltammetry independently of probe approach or scanning.")
        body = tk.Frame(self, bg=COLORS["window"]); body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1); body.grid_rowconfigure(1, weight=1)
        controls = Card(body, "Voltage program", "LabVIEW-compatible start → vertex 1 → vertex 2 → start waveform.")
        controls.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 14))
        controls.body.grid_columnconfigure(0, weight=1); controls.body.grid_columnconfigure(1, weight=1)
        self.start_v = Field(controls.body, "Start", "-0.2", "V", row=0, column=0)
        self.vertex1 = Field(controls.body, "Vertex 1", "0.6", "V", row=0, column=1)
        self.vertex2 = Field(controls.body, "Vertex 2", "-0.4", "V", row=1, column=0)
        self.rate = Field(controls.body, "Scan rate", "0.25", "V/s", row=1, column=1)
        self.cycles = Field(controls.body, "Cycles", "2", row=2, column=0)
        self.jump = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls.body, text="Jump to start potential", variable=self.jump).grid(row=2, column=1, sticky="w", pady=7)
        self._build_status(body, "CV status", "Start CV")
        tabs = ttk.Notebook(body); tabs.grid(row=1, column=1, sticky="nsew")
        cv_tab = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(cv_tab, text="Voltammogram")
        cv_tab.grid_columnconfigure(0, weight=1); cv_tab.grid_rowconfigure(0, weight=1)
        card = Card(cv_tab, "Cyclic voltammogram", "Current 1 versus measured Potential 1."); card.grid(row=0, column=0, sticky="nsew")
        self.cv_plot = Plot(card.body, "Potential 1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), max_points=app.settings.display_max_points, x_label="Potential 1 (V)")
        self.cv_plot.pack(fill="both", expand=True)
        raw = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(raw, text="Raw traces")
        raw.grid_columnconfigure(0, weight=1); raw.grid_columnconfigure(1, weight=1); raw.grid_rowconfigure(0, weight=1)
        c1 = Card(raw, "Potential", "Full time-domain voltage trace."); c1.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.voltage_plot = Plot(c1.body, "Potential vs time", "Potential 1 (V)", (COLORS["accent"],), max_points=app.settings.display_max_points); self.voltage_plot.pack(fill="both", expand=True)
        c2 = Card(raw, "Current", "Full time-domain Current 1 trace."); c2.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.current_plot = Plot(c2.body, "Current vs time", "Current 1 (nA)", (COLORS["blue"],), max_points=app.settings.display_max_points); self.current_plot.pack(fill="both", expand=True)

    def parameters(self) -> CVParameters:
        return CVParameters(self.start_v.float(), self.vertex1.float(), self.vertex2.float(), self.rate.float(), self.cycles.integer(), self.jump.get())

    def start(self) -> None:
        try:
            params = self.parameters(); self.cv_plot.clear(); self.voltage_plot.clear(); self.current_plot.clear(); self._begin(params)
            self.app.toast("Standalone CV started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not experiment.active:
            return
        for sample in samples:
            self.voltage_plot.append(sample.elapsed_s, sample.voltage1_v, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False)
        if samples:
            self.voltage_plot.redraw(); self.current_plot.redraw(); self.cv_plot.redraw()
        self._show_update(experiment.tick_samples(samples))


class StandaloneApproachPage(ManagedExperimentPage):
    experiment_key = "approach"
    recording_name = "Approach"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach", "Acquire an approach curve, stop on confirmed contact, and optionally retract.")
        body = tk.Frame(self, bg=COLORS["window"]); body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1); body.grid_rowconfigure(1, weight=1)
        controls = Card(body, "Approach program", "The FPGA pauses Z motion when Current 1 crosses the threshold.")
        controls.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 14))
        controls.body.grid_columnconfigure(0, weight=1); controls.body.grid_columnconfigure(1, weight=1)
        self.start_z = Field(controls.body, "Start Z", "10", "um", row=0, column=0); self.end_z = Field(controls.body, "End Z", "90", "um", row=0, column=1)
        self.approach_rate = Field(controls.body, "Approach rate", "3", "um/s", row=1, column=0); self.retract_rate = Field(controls.body, "Retract rate", "10", "um/s", row=1, column=1)
        self.potential = Field(controls.body, "Approach potential", "0.1", "V", row=2, column=0); self.threshold = Field(controls.body, "Current 1 threshold", "2", "nA", row=2, column=1)
        self.x_position = Field(controls.body, "Optional X position", "", "um", row=3, column=0)
        self.y_position = Field(controls.body, "Optional Y position", "", "um", row=3, column=1)
        self.greater = tk.BooleanVar(value=True); self.retract = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls.body, text="Trigger when greater", variable=self.greater).grid(row=4, column=0, sticky="w", pady=7)
        ttk.Checkbutton(controls.body, text="Retract after approach", variable=self.retract).grid(row=4, column=1, sticky="w", pady=7)
        self._build_status(body, "Approach status", "Start approach")
        plots = tk.Frame(body, bg=COLORS["window"]); plots.grid(row=1, column=1, sticky="nsew")
        plots.grid_columnconfigure(0, weight=1); plots.grid_columnconfigure(1, weight=1); plots.grid_rowconfigure(0, weight=1)
        zc = Card(plots, "Z approach", "Measured Z for the complete approach/retract."); zc.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.z_plot = Plot(zc.body, "Z vs time", "Z (um)", (COLORS["accent"],), max_points=app.settings.display_max_points); self.z_plot.pack(fill="both", expand=True)
        cc = Card(plots, "Approach current", "Current 1 approach curve in time."); cc.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.current_plot = Plot(cc.body, "Current vs time", "Current 1 (nA)", (COLORS["blue"],), max_points=app.settings.display_max_points); self.current_plot.pack(fill="both", expand=True)

    def parameters(self) -> ApproachParameters:
        return ApproachParameters(
            self.start_z.float(), self.end_z.float(), self.approach_rate.float(), self.retract_rate.float(),
            self.potential.float(), "Current 1", self.threshold.float(), self.greater.get(), self.retract.get(),
            self.x_position.optional_float(), self.y_position.optional_float(),
        )

    def start(self) -> None:
        try:
            params = self.parameters(); self.z_plot.clear(); self.current_plot.clear(); self._begin(params)
            self.app.toast("Approach started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not experiment.active:
            return
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False); self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
        if samples:
            self.z_plot.redraw(); self.current_plot.redraw()
        self._show_update(experiment.tick_samples(samples))


class ApproachITPage(ManagedExperimentPage):
    experiment_key = "approach_it"
    recording_name = "Approach then IT"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach + I-t", "Detect contact, apply timed potential steps, record current versus time, then optionally retract.")
        body = tk.Frame(self, bg=COLORS["window"]); body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1); body.grid_rowconfigure(1, weight=1)
        scroll = ScrollableFrame(body, width=390); scroll.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 14))
        controls = Card(scroll.content, "Approach and I-t program", "Each potential is jumped, then held for its configured duration."); controls.pack(fill="x")
        controls.body.grid_columnconfigure(0, weight=1); controls.body.grid_columnconfigure(1, weight=1)
        self.start_z = Field(controls.body, "Start Z", "10", "um", row=0, column=0); self.end_z = Field(controls.body, "End Z", "90", "um", row=0, column=1)
        self.approach_rate = Field(controls.body, "Approach rate", "3", "um/s", row=1, column=0); self.retract_rate = Field(controls.body, "Retract rate", "10", "um/s", row=1, column=1)
        self.approach_v = Field(controls.body, "Approach potential", "0.1", "V", row=2, column=0); self.threshold = Field(controls.body, "Current 1 threshold", "2", "nA", row=2, column=1)
        self.initial_v = Field(controls.body, "Initial potential", "-0.1", "V", row=3, column=0); self.initial_t = Field(controls.body, "Initial hold", "0.25", "s", row=3, column=1)
        self.step_v = Field(controls.body, "Pulse potential", "0.4", "V", row=4, column=0); self.step_t = Field(controls.body, "Pulse hold", "1.0", "s", row=4, column=1)
        self.return_v = Field(controls.body, "Return potential", "-0.1", "V", row=5, column=0); self.return_t = Field(controls.body, "Return hold", "0.25", "s", row=5, column=1)
        self.x_position = Field(controls.body, "Optional X position", "", "um", row=6, column=0)
        self.y_position = Field(controls.body, "Optional Y position", "", "um", row=6, column=1)
        self.cycles = Field(controls.body, "Cycles", "1", row=7, column=0)
        self.retract = tk.BooleanVar(value=True); self.greater = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls.body, text="Retract after I-t", variable=self.retract).grid(row=7, column=1, sticky="w", pady=7)
        ttk.Checkbutton(controls.body, text="Trigger when greater", variable=self.greater).grid(row=8, column=0, columnspan=2, sticky="w", pady=7)
        self._build_status(body, "Approach + I-t status", "Start approach + I-t")
        tabs = ttk.Notebook(body); tabs.grid(row=1, column=1, sticky="nsew")
        traces = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(traces, text="Full traces")
        traces.grid_columnconfigure(0, weight=1); traces.grid_columnconfigure(1, weight=1); traces.grid_rowconfigure(0, weight=1)
        zc = Card(traces, "Z", "Complete approach and retract."); zc.grid(row=0, column=0, sticky="nsew", padx=(0, 5)); self.z_plot = Plot(zc.body, "Z vs time", "Z (um)", (COLORS["accent"],), max_points=app.settings.display_max_points); self.z_plot.pack(fill="both", expand=True)
        cc = Card(traces, "Current", "Complete Current 1 trace."); cc.grid(row=0, column=1, sticky="nsew", padx=(5, 0)); self.current_plot = Plot(cc.body, "Current vs time", "Current 1 (nA)", (COLORS["blue"],), max_points=app.settings.display_max_points); self.current_plot.pack(fill="both", expand=True)
        it = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(it, text="I-t data")
        it.grid_columnconfigure(0, weight=1); it.grid_columnconfigure(1, weight=1); it.grid_rowconfigure(0, weight=1)
        vc = Card(it, "Potential steps", "Potential during the post-contact I-t program."); vc.grid(row=0, column=0, sticky="nsew", padx=(0, 5)); self.voltage_plot = Plot(vc.body, "Potential vs I-t time", "Potential 1 (V)", (COLORS["accent"],), max_points=app.settings.display_max_points, x_label="I-t elapsed (s)"); self.voltage_plot.pack(fill="both", expand=True)
        ic = Card(it, "I-t response", "Current 1 acquired only during timed holds."); ic.grid(row=0, column=1, sticky="nsew", padx=(5, 0)); self.it_plot = Plot(ic.body, "Current vs I-t time", "Current 1 (nA)", (COLORS["danger"],), max_points=app.settings.display_max_points, x_label="I-t elapsed (s)"); self.it_plot.pack(fill="both", expand=True)
        self._it_t0: float | None = None

    def parameters(self) -> ApproachITParameters:
        return ApproachITParameters(
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), approach_rate_um_s=self.approach_rate.float(),
            retract_rate_um_s=self.retract_rate.float(), approach_voltage_v=self.approach_v.float(), feedback_channel="Current 1",
            feedback_threshold=self.threshold.float(), greater_than=self.greater.get(), retract_after=self.retract.get(),
            x_um=self.x_position.optional_float(), y_um=self.y_position.optional_float(), initial_potential_v=self.initial_v.float(),
            initial_hold_s=self.initial_t.float(), step_potential_v=self.step_v.float(), step_hold_s=self.step_t.float(),
            return_potential_v=self.return_v.float(), return_hold_s=self.return_t.float(), cycles=self.cycles.integer(),
        )

    def start(self) -> None:
        try:
            params = self.parameters(); [plot.clear() for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot)]; self._it_t0 = None; self._begin(params)
            self.app.toast("Approach + I-t started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not experiment.active:
            return
        update = None
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            if experiment._hardware:
                stage = self.app.backend.hardware_program_context(sample.line_number)[1]
            else:
                was_it = experiment.state == ExperimentState.IT
                update = experiment.tick_samples([sample])
                stage = "it" if was_it or experiment.state == ExperimentState.IT else ""
            if stage.startswith("it"):
                self._it_t0 = sample.elapsed_s if self._it_t0 is None else self._it_t0
                elapsed = sample.elapsed_s - self._it_t0
                self.voltage_plot.append(elapsed, sample.voltage1_v, redraw=False)
                self.it_plot.append(elapsed, sample.current1_na, redraw=False)
        if experiment._hardware:
            update = experiment.tick_samples(samples)
        elif not samples:
            update = experiment.tick_samples([])
        if samples:
            self.z_plot.redraw(); self.current_plot.redraw(); self.voltage_plot.redraw(); self.it_plot.redraw()
        self._show_update(update)


class ScanHoppingITPage(ManagedExperimentPage):
    experiment_key = "scan_it"
    recording_name = "Scan Hopping IT"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Scan hopping + I-t", "Approach, run timed potential steps at the surface, retract, and repeat over XY.")
        body = tk.Frame(self, bg=COLORS["window"]); body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1); body.grid_rowconfigure(1, weight=1)
        scroll = ScrollableFrame(body, width=400); scroll.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 14))
        controls = Card(scroll.content, "Hopping I-t program", "Current map is the mean Current 1 during the pulse hold."); controls.pack(fill="x")
        controls.body.grid_columnconfigure(0, weight=1); controls.body.grid_columnconfigure(1, weight=1)
        self.x_start = Field(controls.body, "X start", "35", "um", row=0, column=0); self.x_end = Field(controls.body, "X end", "65", "um", row=0, column=1)
        self.x_points = Field(controls.body, "X points", "3", row=1, column=0); self.y_points = Field(controls.body, "Y points", "3", row=1, column=1)
        self.y_start = Field(controls.body, "Y start", "35", "um", row=2, column=0); self.y_end = Field(controls.body, "Y end", "65", "um", row=2, column=1)
        self.start_z = Field(controls.body, "Retracted Z", "55", "um", row=3, column=0); self.end_z = Field(controls.body, "Approach limit Z", "80", "um", row=3, column=1)
        self.xy_rate = Field(controls.body, "XY rate", "50", "um/s", row=4, column=0); self.approach_rate = Field(controls.body, "Approach rate", "15", "um/s", row=4, column=1)
        self.retract_rate = Field(controls.body, "Retract rate", "50", "um/s", row=5, column=0); self.threshold = Field(controls.body, "Current 1 threshold", "2", "nA", row=5, column=1)
        self.approach_v = Field(controls.body, "Approach potential", "0.1", "V", row=6, column=0); self.cycles = Field(controls.body, "I-t cycles", "1", row=6, column=1)
        self.initial_v = Field(controls.body, "Initial potential", "-0.1", "V", row=7, column=0); self.initial_t = Field(controls.body, "Initial hold", "0.25", "s", row=7, column=1)
        self.step_v = Field(controls.body, "Pulse potential", "0.4", "V", row=8, column=0); self.step_t = Field(controls.body, "Pulse hold", "1.0", "s", row=8, column=1)
        self.return_v = Field(controls.body, "Return potential", "-0.1", "V", row=9, column=0); self.return_t = Field(controls.body, "Return hold", "0.25", "s", row=9, column=1)
        self.serpentine = tk.BooleanVar(value=True); self.greater = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls.body, text="Serpentine rows", variable=self.serpentine).grid(row=10, column=0, sticky="w", pady=7)
        ttk.Checkbutton(controls.body, text="Trigger when greater", variable=self.greater).grid(row=10, column=1, sticky="w", pady=7)
        self._build_status(body, "Hopping I-t status", "Start hopping I-t")
        tabs = ttk.Notebook(body); tabs.grid(row=1, column=1, sticky="nsew")
        traces = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(traces, text="Experiment traces"); traces.grid_columnconfigure(0, weight=1); traces.grid_columnconfigure(1, weight=1); traces.grid_rowconfigure(0, weight=1)
        zc = Card(traces, "Z", "Full scan Z history."); zc.grid(row=0, column=0, sticky="nsew", padx=(0, 5)); self.z_plot = Plot(zc.body, "Z vs time", "Z (um)", (COLORS["accent"],), max_points=app.settings.display_max_points); self.z_plot.pack(fill="both", expand=True)
        cc = Card(traces, "Current", "Full scan Current 1 history."); cc.grid(row=0, column=1, sticky="nsew", padx=(5, 0)); self.current_plot = Plot(cc.body, "Current vs time", "Current 1 (nA)", (COLORS["blue"],), max_points=app.settings.display_max_points); self.current_plot.pack(fill="both", expand=True)
        it = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(it, text="I-t at pixel"); it.grid_columnconfigure(0, weight=1); it.grid_columnconfigure(1, weight=1); it.grid_rowconfigure(0, weight=1)
        vc = Card(it, "Potential", "Timed potential steps at the latest pixel."); vc.grid(row=0, column=0, sticky="nsew", padx=(0, 5)); self.voltage_plot = Plot(vc.body, "Potential vs local time", "Potential 1 (V)", (COLORS["accent"],), max_points=app.settings.display_max_points, x_label="Pixel I-t elapsed (s)"); self.voltage_plot.pack(fill="both", expand=True)
        ic = Card(it, "Current", "I-t response at the latest pixel."); ic.grid(row=0, column=1, sticky="nsew", padx=(5, 0)); self.it_plot = Plot(ic.body, "Current vs local time", "Current 1 (nA)", (COLORS["danger"],), max_points=app.settings.display_max_points, x_label="Pixel I-t elapsed (s)"); self.it_plot.pack(fill="both", expand=True)
        maps = tk.Frame(tabs, bg=COLORS["window"], padx=4, pady=4); tabs.add(maps, text="Maps"); maps.grid_columnconfigure(0, weight=1); maps.grid_columnconfigure(1, weight=1); maps.grid_rowconfigure(0, weight=1)
        zm = Card(maps, "Z contact map", "Confirmed feedback crossing at each pixel."); zm.grid(row=0, column=0, sticky="nsew", padx=(0, 5)); self.z_map = Heatmap(zm.body, "um"); self.z_map.pack(fill="both", expand=True)
        cm = Card(maps, "Pulse-current map", "Mean Current 1 during the pulse-potential hold."); cm.grid(row=0, column=1, sticky="nsew", padx=(5, 0)); self.current_map = Heatmap(cm.body, "nA"); self.current_map.pack(fill="both", expand=True)
        self._it_point = -1; self._it_t0: float | None = None

    def parameters(self) -> ScanHoppingITParameters:
        return ScanHoppingITParameters(
            x_start_um=self.x_start.float(), x_end_um=self.x_end.float(), x_points=self.x_points.integer(),
            y_start_um=self.y_start.float(), y_end_um=self.y_end.float(), y_points=self.y_points.integer(),
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), lateral_rate_um_s=self.xy_rate.float(),
            approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_threshold=self.threshold.float(), greater_than=self.greater.get(),
            initial_potential_v=self.initial_v.float(), initial_hold_s=self.initial_t.float(), step_potential_v=self.step_v.float(),
            step_hold_s=self.step_t.float(), return_potential_v=self.return_v.float(), return_hold_s=self.return_t.float(),
            cycles=self.cycles.integer(), serpentine=self.serpentine.get(),
        )

    def start(self) -> None:
        try:
            params = self.parameters(); [plot.clear() for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot)]
            self.z_map.set_data({}, params.y_points, params.x_points); self.current_map.set_data({}, params.y_points, params.x_points)
            self._it_point, self._it_t0 = -1, None; self._begin(params); self.app.toast("Hopping I-t scan started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not experiment.active:
            return
        update = None
        if experiment._hardware:
            update = experiment.tick_samples(samples)
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            if experiment._hardware:
                point, stage = self.app.backend.hardware_program_context(sample.line_number)
            else:
                point_before = experiment.point_index
                was_it = experiment.state == ExperimentState.IT
                update = experiment.tick_samples([sample])
                point = sample.scan_pixel if sample.scan_pixel >= 0 else point_before
                stage = "it" if was_it or experiment.state == ExperimentState.IT else ""
            if stage.startswith("it") and point >= 0:
                if point != self._it_point:
                    self._it_point, self._it_t0 = point, sample.elapsed_s; self.voltage_plot.clear(); self.it_plot.clear()
                elapsed = sample.elapsed_s - (self._it_t0 or sample.elapsed_s)
                self.voltage_plot.append(elapsed, sample.voltage1_v, redraw=False); self.it_plot.append(elapsed, sample.current1_na, redraw=False)
        if samples:
            self.z_plot.redraw(); self.current_plot.redraw(); self.voltage_plot.redraw(); self.it_plot.redraw()
        params = experiment.params
        self.z_map.set_data(experiment.contact_z, params.y_points, params.x_points)
        self.current_map.set_data(experiment.current_at_pulse, params.y_points, params.x_points)
        self._show_update(update)


class MovePiezoPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Move piezo", "Command a bounded, constant-rate move on one axis or voltage output.")
        body = tk.Frame(self, bg=COLORS["window"])
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        control = Card(body, "Movement", "Targets are checked against the ranges in Settings before any command is sent.")
        control.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        self.axis = tk.StringVar(value="Z")
        tk.Label(control.body, text="Output", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(0, 5))
        ttk.Combobox(control.body, textvariable=self.axis, values=("X", "Y", "Z", "Voltage 1", "Voltage 2"), state="readonly").pack(fill="x", pady=(0, 8))
        self.target = Field(control.body, "Target position", "25.0", "um", pack=True)
        self.speed = Field(control.body, "Movement speed", "5.0", "um/s", pack=True)
        self.axis.trace_add("write", lambda *_: self._update_units())
        ttk.Button(control.body, text="Move", style="Accent.TButton", command=self.move).pack(fill="x", pady=(16, 7))
        ttk.Button(control.body, text="Stop motion", style="Danger.TButton", command=self.stop).pack(fill="x", pady=7)

        position = Card(body, "Position feedback", "Measured input and commanded FPGA output are shown separately.")
        position.grid(row=0, column=1, sticky="nsew")
        grid = tk.Frame(position.body, bg=COLORS["panel"])
        grid.pack(fill="both", expand=True)
        for column in range(3):
            grid.grid_columnconfigure(column, weight=1)
        self.position_labels: dict[str, tk.Label] = {}
        self.commanded_position_labels: dict[str, tk.Label] = {}
        for column, axis in enumerate(("X", "Y", "Z")):
            pane = tk.Frame(grid, bg=COLORS["panel_2"], highlightbackground=COLORS["border"], highlightthickness=1)
            pane.grid(row=0, column=column, sticky="nsew", padx=7, pady=7)
            tk.Label(pane, text=axis, bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(11, "bold")).pack(pady=(15, 5))
            tk.Label(pane, text="MEASURED", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(7, "bold")).pack()
            label = tk.Label(pane, text="—", bg=COLORS["panel_2"], fg=COLORS["text"], font=_font(22, "bold"))
            label.pack()
            tk.Label(pane, text="um", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(9)).pack(pady=(2, 7))
            tk.Label(pane, text="COMMANDED", bg=COLORS["panel_2"], fg=COLORS["muted"], font=_font(7, "bold")).pack()
            commanded = tk.Label(pane, text="— um", bg=COLORS["panel_2"], fg=COLORS["blue"], font=_font(10, "bold"))
            commanded.pack(pady=(2, 14))
            self.position_labels[axis] = label
            self.commanded_position_labels[axis] = commanded
        self.readback_source_label = tk.Label(
            position.body, text=app.backend.position_readback_label,
            bg=COLORS["panel"], fg=COLORS["muted"], font=_font(8), justify="left",
        )
        self.readback_source_label.pack(anchor="w", pady=(10, 0))
        self.status_label = tk.Label(position.body, text="No move in progress", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(10))
        self.status_label.pack(anchor="w", pady=(16, 0))
        position.body.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(180, event.width - 8)))

    def _update_units(self) -> None:
        voltage = self.axis.get().startswith("Voltage")
        self.target.set_unit("V" if voltage else "um")
        self.speed.set_unit("not used" if voltage else "um/s")

    def move(self) -> None:
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("Stop the experiment before commanding a manual move.")
            axis, target, speed = self.axis.get(), self.target.float(), self.speed.float()
            self.app.backend.move(axis, target, speed)
            if axis.startswith("Voltage"):
                self.status_label.configure(text=f"Set {axis} to {target:g} V (applied immediately)")
            else:
                self.status_label.configure(text=f"Moving {axis} to {target:g} um at {speed:g} um/s")
            self.app.toast("Movement command accepted", "success")
        except (ValueError, BackendError) as exc:
            self.app.show_error(str(exc))

    def stop(self) -> None:
        try:
            self.app.backend.stop_motion()
            self.status_label.configure(text="Motion stopped")
        except BackendError as exc:
            self.app.show_error(str(exc))

    def on_sample(self, sample: Sample) -> None:
        for axis, value in (("X", sample.x_um), ("Y", sample.y_um), ("Z", sample.z_um)):
            self.position_labels[axis].configure(text=f"{value:.3f}")
        for axis, value in (("X", sample.commanded_x_um), ("Y", sample.commanded_y_um), ("Z", sample.commanded_z_um)):
            text = f"{value:.3f} um" if math.isfinite(value) else "—"
            self.commanded_position_labels[axis].configure(text=text)
        self.readback_source_label.configure(text=self.app.backend.position_readback_label)


class SettingsPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Settings", "One validated configuration shared by every experiment.")
        self.viewport = ScrollableFrame(self)
        self.viewport.pack(fill="both", expand=True)
        columns = self.viewport.content
        for column in range(2):
            columns.grid_columnconfigure(column, weight=1)
        left = tk.Frame(columns, bg=COLORS["window"])
        right = tk.Frame(columns, bg=COLORS["window"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        connection = Card(left, "Connection", "Simulation needs no drivers. NI FPGA uses the compiled target already in this project.")
        connection.pack(fill="x", pady=(0, 14))
        self.mode = tk.StringVar(value=app.settings.mode)
        tk.Label(connection.body, text="Backend", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(0, 5))
        ttk.Combobox(connection.body, textvariable=self.mode, values=("Simulation", "NI FPGA"), state="readonly").pack(fill="x", pady=(0, 8))
        self.profile = tk.StringVar(value=app.settings.instrument_profile)
        tk.Label(connection.body, text="Instrument profile", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(7, 5))
        ttk.Combobox(connection.body, textvariable=self.profile, values=tuple(INSTRUMENT_PROFILES), state="readonly").pack(fill="x")
        self.capability_label = tk.Label(connection.body, text="", bg=COLORS["panel"], fg=COLORS["blue"], font=_font(8), justify="left")
        self.capability_label.pack(anchor="w", fill="x", pady=(5, 3))
        self.resource = Field(connection.body, "NI resource", app.settings.resource, pack=True)
        self.transport = tk.StringVar(value=app.settings.hardware_transport)
        tk.Label(connection.body, text="FPGA hardware", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(7, 5))
        ttk.Combobox(
            connection.body,
            textvariable=self.transport,
            values=("USB R Series", "PCIe/PXI R Series", "Auto"),
            state="readonly",
        ).pack(fill="x")
        bitfile_row = tk.Frame(connection.body, bg=COLORS["panel"])
        bitfile_row.pack(fill="x", pady=(7, 0))
        tk.Label(bitfile_row, text="Compiled bitfile", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(0, 5))
        line = tk.Frame(bitfile_row, bg=COLORS["panel"])
        line.pack(fill="x")
        self.bitfile = tk.StringVar(value=app.settings.bitfile)
        ttk.Entry(line, textvariable=self.bitfile).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="Browse", style="Quiet.TButton", command=self.browse_bitfile).pack(side="left", padx=(8, 0))

        acquisition = Card(left, "Acquisition", "Effective interval includes the FPGA transfer iteration used by WEC-SPM.")
        acquisition.pack(fill="x")
        for column in range(2):
            acquisition.body.grid_columnconfigure(column, weight=1)
        self.sample_time = Field(acquisition.body, "Sample time", str(app.settings.sample_time_us), "us", row=0, column=0)
        self.samples_per_point = Field(acquisition.body, "Samples per point", str(app.settings.samples_per_point), row=0, column=1)
        self.ready_timeout = Field(acquisition.body, "FPGA ready timeout", str(app.settings.hardware_ready_timeout_s), "s", row=1, column=0)
        self.watchdog_margin = Field(acquisition.body, "Command watchdog margin", str(app.settings.hardware_watchdog_margin_s), "s", row=1, column=1)
        self.period_label = tk.Label(acquisition.body, text="", bg=COLORS["panel"], fg=COLORS["accent"], font=_font(10, "bold"))
        self.period_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=8)

        lockin = Card(left, "External lock-in", "SR830 analog-output scaling used for AI5 amplitude and AI7 phase.")
        lockin.pack(fill="x", pady=(14, 0))
        for column in range(3):
            lockin.body.grid_columnconfigure(column, weight=1)
        self.lockin_sensitivity = Field(lockin.body, "Sensitivity", str(app.settings.lockin_sensitivity_na), "nA", row=0, column=0)
        self.lockin_expand = Field(lockin.body, "Expand", str(app.settings.lockin_expand), row=0, column=1)
        self.lockin_offset = Field(lockin.body, "Offset", str(app.settings.lockin_offset_pct), "%", row=0, column=2)

        feedback = Card(left, "Advanced FPGA feedback", "Host configuration only; feedback execution remains on the FPGA target.")
        feedback.pack(fill="x", pady=(14, 0))
        for column in range(2):
            feedback.body.grid_columnconfigure(column, weight=1)
        self.feedback2_enabled = tk.BooleanVar(value=app.settings.feedback2_enabled)
        ttk.Checkbutton(feedback.body, text="Enable secondary feedback for compatible waypoint modes", variable=self.feedback2_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=7
        )
        self.feedback2_channel = tk.StringVar(value=app.settings.feedback2_channel)
        feedback2_signal = tk.Frame(feedback.body, bg=COLORS["panel"])
        feedback2_signal.grid(row=1, column=0, sticky="ew", padx=(0, 14), pady=7)
        tk.Label(feedback2_signal, text="Secondary signal", bg=COLORS["panel"], fg=COLORS["muted"], font=_font(9)).pack(anchor="w", pady=(0, 5))
        self.feedback2_channel_box = ttk.Combobox(
            feedback2_signal, textvariable=self.feedback2_channel,
            values=("Current 1", "Current 2", "Current 3", "Current 4", "Lock-in amplitude", "Lock-in phase"),
            state="readonly",
        )
        self.feedback2_channel_box.pack(fill="x")
        self.feedback2_threshold = Field(feedback.body, "Secondary threshold", str(app.settings.feedback2_threshold), row=1, column=1)
        self.feedback2_greater = tk.BooleanVar(value=app.settings.feedback2_greater_than)
        ttk.Checkbutton(feedback.body, text="Secondary triggers when greater than threshold", variable=self.feedback2_greater).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=7
        )
        self.feedback_p = Field(feedback.body, "Proportional gain P", str(app.settings.feedback_p_gain), row=3, column=0)
        self.feedback_max_z = Field(feedback.body, "Maximum Z update", str(app.settings.feedback_max_z_step_nm), "nm", row=3, column=1)
        self.feedback_update = Field(feedback.body, "Z update interval", str(app.settings.feedback_update_interval_us), "us", row=4, column=0)
        self.feedback_avg_whole = Field(feedback.body, "Running-average whole", str(app.settings.feedback_running_average_whole), row=4, column=1)
        self.feedback_avg_minus = Field(feedback.body, "Running-average subtract", str(app.settings.feedback_running_average_minus), row=5, column=0)
        self.feedback_self_reference = tk.BooleanVar(value=app.settings.feedback_self_reference_on_hold)
        ttk.Checkbutton(feedback.body, text="Self-reference feedback while holding", variable=self.feedback_self_reference).grid(
            row=5, column=1, sticky="w", pady=7
        )
        self.bulk1 = Field(feedback.body, "Bulk retract distance 1", str(app.settings.distance_to_bulk_um), "um", row=6, column=0)
        self.bulk2 = Field(feedback.body, "Bulk retract distance 2", str(app.settings.distance_to_bulk2_um), "um", row=6, column=1)
        self.bulk3 = Field(feedback.body, "Bulk retract distance 3", str(app.settings.distance_to_bulk3_um), "um", row=7, column=0)

        piezos = Card(right, "Piezo ranges", "Match these values to the calibrated positioner and controller on the instrument.")
        piezos.pack(fill="x", pady=(0, 14))
        for column in range(3):
            piezos.body.grid_columnconfigure(column, weight=1)
        self.x_range = Field(piezos.body, "X maximum", str(app.settings.x_range_um), "um", row=0, column=0)
        self.y_range = Field(piezos.body, "Y maximum", str(app.settings.y_range_um), "um", row=0, column=1)
        self.z_range = Field(piezos.body, "Z maximum", str(app.settings.z_range_um), "um", row=0, column=2)
        self.x_bipolar = tk.BooleanVar(value=app.settings.x_bipolar)
        self.y_bipolar = tk.BooleanVar(value=app.settings.y_bipolar)
        self.z_bipolar = tk.BooleanVar(value=app.settings.z_bipolar)
        for col, (axis, variable) in enumerate((("X", self.x_bipolar), ("Y", self.y_bipolar), ("Z", self.z_bipolar))):
            ttk.Checkbutton(piezos.body, text=f"{axis}: -10 to +10 V", variable=variable).grid(row=1, column=col, sticky="w", pady=8)

        amplifier = Card(right, "Current amplifiers", "Sensitivity is amplifier output volts per nanoamp of measured current.")
        amplifier.pack(fill="x", pady=(0, 14))
        for column in range(2):
            amplifier.body.grid_columnconfigure(column, weight=1)
        self.sensitivity1 = Field(amplifier.body, "Current 1 · AI3", str(app.settings.current1_v_per_na), "V/nA", row=0, column=0)
        self.sensitivity2 = Field(amplifier.body, "Current 2 · AI4", str(app.settings.current2_v_per_na), "V/nA", row=0, column=1)
        self.sensitivity3 = Field(amplifier.body, "Current 3 · AI6", str(app.settings.current3_v_per_na), "V/nA", row=1, column=0)
        self.sensitivity4 = Field(amplifier.body, "Current 4 · AI1", str(app.settings.current4_v_per_na), "V/nA", row=1, column=1)
        self.read_current4 = tk.BooleanVar(value=app.settings.read_current4_instead_y)
        ttk.Checkbutton(
            amplifier.body,
            text="Read Current 4 on AI1 instead of Y position input",
            variable=self.read_current4,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=8)

        saving = Card(right, "Saving")
        saving.pack(fill="x")
        self.save_directory = Field(saving.body, "Data folder", app.settings.save_directory, pack=True)
        self.auto_save = tk.BooleanVar(value=app.settings.auto_save)
        ttk.Checkbutton(saving.body, text="Automatically save completed experiments", variable=self.auto_save).pack(anchor="w", pady=8)
        self.command_ratio = Field(saving.body, "AO3 command voltage ratio", str(app.settings.command_voltage_ratio), pack=True)

        calibration = Card(right, "Calibration provenance", "Saved with every recording so numerical scaling remains traceable.")
        calibration.pack(fill="x", pady=(14, 0))
        self.calibration_source = Field(calibration.body, "Calibration source / certificate", app.settings.calibration_source, pack=True)
        self.calibration_date = Field(calibration.body, "Calibration date", app.settings.calibration_date, pack=True)
        self.calibration_operator = Field(calibration.body, "Operator", app.settings.calibration_operator, pack=True)
        self.calibration_notes = Field(calibration.body, "Notes", app.settings.calibration_notes, pack=True)
        self.display_max_points = Field(calibration.body, "Display buffer", str(app.settings.display_max_points), "points/plot", pack=True)

        action = tk.Frame(columns, bg=COLORS["window"])
        action.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 20))
        ttk.Button(action, text="Save & apply settings", style="Accent.TButton", command=self.save).pack(side="left")
        action_note = tk.Label(
            action, text="Changing backend settings disconnects the current device.",
            bg=COLORS["window"], fg=COLORS["muted"], font=_font(9), justify="left",
        )
        action_note.pack(side="left", padx=14)
        action.bind("<Configure>", lambda event: action_note.configure(wraplength=max(180, event.width - 220)))
        self._refresh_period()
        self._sync_profile()
        self.mode.trace_add("write", lambda *_: self._mode_changed())
        self.profile.trace_add("write", lambda *_: self._sync_profile())
        self.sample_time.variable.trace_add("write", lambda *_: self._refresh_period())
        self.samples_per_point.variable.trace_add("write", lambda *_: self._refresh_period())

    def _refresh_period(self) -> None:
        try:
            period = self.sample_time.integer() * (self.samples_per_point.integer() + 1) / 1000
            self.period_label.configure(text=f"Effective data interval  {period:.3f} ms")
        except ValueError:
            self.period_label.configure(text="Effective data interval  —")

    def _mode_changed(self) -> None:
        self.profile.set(HARDWARE_PROFILE if self.mode.get() == "NI FPGA" else "Simulation")

    def _sync_profile(self) -> None:
        self.capability_label.configure(text=INSTRUMENT_PROFILES.get(self.profile.get(), "Unknown profile"))
        hardware = self.profile.get() == HARDWARE_PROFILE
        fields = (
            self.resource, self.ready_timeout, self.watchdog_margin,
            self.feedback2_threshold, self.feedback_p, self.feedback_max_z,
            self.feedback_update, self.feedback_avg_whole, self.feedback_avg_minus,
            self.bulk1, self.bulk2, self.bulk3,
        )
        for field in fields:
            field.entry.configure(state="normal" if hardware else "disabled")
        self.feedback2_channel_box.configure(state="readonly" if hardware else "disabled")

    def browse_bitfile(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose NI FPGA bitfile", filetypes=(("LabVIEW FPGA bitfile", "*.lvbitx"), ("All files", "*")))
        if chosen:
            try:
                self.bitfile.set(str(Path(chosen).resolve().relative_to(Path.cwd())))
            except ValueError:
                self.bitfile.set(chosen)

    def values(self) -> AppSettings:
        return AppSettings(
            mode=self.mode.get(),
            instrument_profile=self.profile.get(),
            resource=self.resource.variable.get().strip(),
            bitfile=self.bitfile.get().strip(),
            hardware_transport=self.transport.get(),
            x_range_um=self.x_range.float(),
            y_range_um=self.y_range.float(),
            z_range_um=self.z_range.float(),
            x_bipolar=self.x_bipolar.get(),
            y_bipolar=self.y_bipolar.get(),
            z_bipolar=self.z_bipolar.get(),
            command_voltage_ratio=self.command_ratio.float(),
            current1_v_per_na=self.sensitivity1.float(),
            current2_v_per_na=self.sensitivity2.float(),
            current3_v_per_na=self.sensitivity3.float(),
            current4_v_per_na=self.sensitivity4.float(),
            read_current4_instead_y=self.read_current4.get(),
            lockin_sensitivity_na=self.lockin_sensitivity.float(),
            lockin_expand=self.lockin_expand.float(),
            lockin_offset_pct=self.lockin_offset.float(),
            sample_time_us=self.sample_time.integer(),
            samples_per_point=self.samples_per_point.integer(),
            hardware_ready_timeout_s=self.ready_timeout.float(),
            hardware_watchdog_margin_s=self.watchdog_margin.float(),
            save_directory=self.save_directory.variable.get().strip(),
            auto_save=self.auto_save.get(),
            calibration_source=self.calibration_source.variable.get().strip(),
            calibration_date=self.calibration_date.variable.get().strip(),
            calibration_operator=self.calibration_operator.variable.get().strip(),
            calibration_notes=self.calibration_notes.variable.get().strip(),
            display_max_points=self.display_max_points.integer(),
            feedback2_enabled=self.feedback2_enabled.get(),
            feedback2_channel=self.feedback2_channel.get(),
            feedback2_threshold=self.feedback2_threshold.float(),
            feedback2_greater_than=self.feedback2_greater.get(),
            feedback_p_gain=self.feedback_p.float(),
            feedback_max_z_step_nm=self.feedback_max_z.integer(),
            feedback_update_interval_us=self.feedback_update.integer(),
            feedback_running_average_whole=self.feedback_avg_whole.integer(),
            feedback_running_average_minus=self.feedback_avg_minus.integer(),
            feedback_self_reference_on_hold=self.feedback_self_reference.get(),
            distance_to_bulk_um=self.bulk1.float(),
            distance_to_bulk2_um=self.bulk2.float(),
            distance_to_bulk3_um=self.bulk3.float(),
        )

    def save(self) -> None:
        try:
            settings = self.values()
            errors = settings.validate()
            if errors:
                raise ValueError("\n".join(errors))
            self.app.apply_settings(settings)
            self.app.toast("Settings saved and applied", "success")
        except ValueError as exc:
            self.app.show_error(str(exc))


class EChemTipsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("eChemTips — Scanning Electrochemistry")
        self.geometry("1440x900")
        self.minsize(1080, 680)
        self.configure(bg=COLORS["window"])
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.store = SettingsStore()
        self.settings = self.store.load()
        self.driver_module = os.environ.get("ECHEMTIPS_DRIVER_MODULE") or os.environ.get("WECSPM_DRIVER_MODULE")
        self.backend: InstrumentBackend = create_backend(self.settings, self.driver_module)
        self._acquisition: AcquisitionWorker | None = None
        self.recorder = DataRecorder()
        self.experiment = ApproachCVExperiment(self.backend, self.settings)
        self.scan_experiment = ScanHoppingCVExperiment(self.backend, self.settings)
        self.cv_experiment = CVExperiment(self.backend, self.settings)
        self.approach_experiment = ApproachExperiment(self.backend, self.settings)
        self.approach_it_experiment = ApproachITExperiment(self.backend, self.settings)
        self.scan_it_experiment = ScanHoppingITExperiment(self.backend, self.settings)
        self.experiments = {
            "approach_cv": self.experiment,
            "scan_cv": self.scan_experiment,
            "cv": self.cv_experiment,
            "approach": self.approach_experiment,
            "approach_it": self.approach_it_experiment,
            "scan_it": self.scan_it_experiment,
        }
        self._last_experiment_state = self.experiment.state
        self._sample: Sample | None = None
        self._configure_styles()
        self._build_shell()
        self.pages: dict[str, BasePage] = {
            "Watch current": WatchPage(self),
            "CV": StandaloneCVPage(self),
            "Approach": StandaloneApproachPage(self),
            "Approach + CV": ApproachCVPage(self),
            "Approach + I-t": ApproachITPage(self),
            "Scan hopping + CV": ScanHoppingCVPage(self),
            "Scan hopping + I-t": ScanHoppingITPage(self),
            "Move piezo": MovePiezoPage(self),
            "Settings": SettingsPage(self),
        }
        self.show_page("Watch current")
        self._set_connection_ui(False)
        self.after(120, self._poll)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", background=COLORS["panel_2"], foreground=COLORS["text"], borderwidth=0, padding=(14, 9), font=_font(10, "bold"))
        style.map("TButton", background=[("active", COLORS["border"]), ("disabled", COLORS["panel"])], foreground=[("disabled", COLORS["muted"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#ffffff")
        style.map(
            "Accent.TButton",
            background=[("active", "#0d766f"), ("disabled", COLORS["accent_dark"])],
            foreground=[("disabled", COLORS["muted"])],
        )
        style.configure("Danger.TButton", background="#fdecef", foreground=COLORS["danger"])
        style.map("Danger.TButton", background=[("active", "#f8d7de"), ("disabled", COLORS["panel_2"])], foreground=[("disabled", COLORS["muted"])])
        style.configure("Quiet.TButton", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("TEntry", fieldbackground=COLORS["panel_2"], foreground=COLORS["text"], bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], padding=7)
        style.configure("TCombobox", fieldbackground=COLORS["panel_2"], background=COLORS["panel_2"], foreground=COLORS["text"], arrowcolor=COLORS["muted"], bordercolor=COLORS["border"], padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel_2"])], foreground=[("readonly", COLORS["text"])])
        style.configure("TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"], font=_font(9))
        style.map("TCheckbutton", background=[("active", COLORS["panel"])], indicatorcolor=[("selected", COLORS["accent"]), ("!selected", COLORS["panel_2"])])
        style.configure("Teal.Horizontal.TProgressbar", troughcolor=COLORS["panel_2"], background=COLORS["accent"], borderwidth=0)
        style.configure("TNotebook", background=COLORS["window"], borderwidth=0, tabmargins=(0, 0, 0, 6))
        style.configure("TNotebook.Tab", background=COLORS["panel_2"], foreground=COLORS["muted"], padding=(14, 8), font=_font(9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", COLORS["panel"])], foreground=[("selected", COLORS["text"])])
        style.configure(
            "Sidebar.TButton", background=COLORS["sidebar"], foreground=COLORS["sidebar_muted"],
            borderwidth=0, padding=(20, 12), font=_font(10, "bold"), anchor="w",
        )
        style.map("Sidebar.TButton", background=[("active", COLORS["sidebar_active"])], foreground=[("active", COLORS["sidebar_text"])])
        style.configure(
            "SidebarActive.TButton", background=COLORS["sidebar_active"], foreground=COLORS["sidebar_text"],
            borderwidth=0, padding=(20, 12), font=_font(10, "bold"), anchor="w",
        )

    def _build_shell(self) -> None:
        sidebar = tk.Frame(self, width=220, bg=COLORS["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=COLORS["sidebar"])
        brand.pack(fill="x", padx=20, pady=(24, 28))
        mark = tk.Label(brand, text="e", width=2, bg=COLORS["accent"], fg="#ffffff", font=_font(16, "bold"))
        mark.pack(side="left")
        words = tk.Frame(brand, bg=COLORS["sidebar"])
        words.pack(side="left", padx=10)
        tk.Label(words, text="eChemTips", bg=COLORS["sidebar"], fg=COLORS["sidebar_text"], font=_font(13, "bold")).pack(anchor="w")
        tk.Label(words, text="CONTROL", bg=COLORS["sidebar"], fg=COLORS["sidebar_muted"], font=_font(8, "bold")).pack(anchor="w")
        self.nav_buttons: dict[str, ttk.Button] = {}
        for index, (label, glyph) in enumerate((
            ("Watch current", "◉"), ("CV", "⌁"), ("Approach", "↓"),
            ("Approach + CV", "↧"), ("Approach + I-t", "↧"),
            ("Scan hopping + CV", "▦"), ("Scan hopping + I-t", "▦"),
            ("Move piezo", "⌖"), ("Settings", "⚙"),
        ), 1):
            button = ttk.Button(
                sidebar,
                text=f"{glyph}   {label}",
                command=lambda name=label: self.show_page(name),
                style="Sidebar.TButton",
            )
            button.pack(fill="x", pady=2)
            self.nav_buttons[label] = button
            self.bind_all(f"<Command-Key-{index}>", lambda _event, name=label: self.show_page(name))
            self.bind_all(f"<Control-Key-{index}>", lambda _event, name=label: self.show_page(name))
        footer = tk.Frame(sidebar, bg=COLORS["sidebar"])
        footer.pack(side="bottom", fill="x", padx=20, pady=20)
        tk.Label(footer, text="FPGA logic preserved", bg=COLORS["sidebar"], fg=COLORS["sidebar_muted"], font=_font(8)).pack(anchor="w")
        tk.Label(footer, text="Python UI · v0.1", bg=COLORS["sidebar"], fg=COLORS["sidebar_muted"], font=_font(8)).pack(anchor="w", pady=(3, 0))

        main = tk.Frame(self, bg=COLORS["window"])
        main.pack(side="left", fill="both", expand=True)
        topbar = tk.Frame(main, height=68, bg=COLORS["window"], highlightbackground=COLORS["border"], highlightthickness=1)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        self.connection_dot = tk.Label(topbar, text="●", bg=COLORS["window"], fg=COLORS["muted"], font=_font(12))
        self.connection_dot.pack(side="left", padx=(22, 8))
        self.connection_label = tk.Label(topbar, text=f"Disconnected · {self.backend.label}", bg=COLORS["window"], fg=COLORS["muted"], font=_font(10, "bold"))
        self.connection_label.pack(side="left")
        self.execution_label = tk.Label(topbar, text="Idle", bg=COLORS["window"], fg=COLORS["blue"], font=_font(8))
        self.execution_label.pack(side="left", padx=(12, 0))
        ttk.Button(topbar, text="EMERGENCY STOP", style="Danger.TButton", command=self.emergency_stop).pack(side="right", padx=(8, 20), pady=14)
        self.connect_button = ttk.Button(topbar, text="Connect", style="Accent.TButton", command=self.toggle_connection)
        self.connect_button.pack(side="right", padx=8, pady=14)
        self.next_waypoint_button = ttk.Button(topbar, text="Next", style="Quiet.TButton", command=self.end_current_waypoint)
        self.next_waypoint_button.pack(side="right", padx=2, pady=14)
        self.resume_button = ttk.Button(topbar, text="Resume", style="Quiet.TButton", command=self.resume_host)
        self.resume_button.pack(side="right", padx=2, pady=14)
        self.pause_button = ttk.Button(topbar, text="Pause", style="Quiet.TButton", command=self.pause_host)
        self.pause_button.pack(side="right", padx=2, pady=14)
        self.mode_badge = tk.Label(topbar, text=self.settings.mode.upper(), bg=COLORS["panel_2"], fg=COLORS["blue"], font=_font(8, "bold"), padx=10, pady=6)
        self.mode_badge.pack(side="right", padx=8)

        self.content = tk.Frame(main, bg=COLORS["window"])
        self.content.pack(fill="both", expand=True, padx=26, pady=22)
        self.toast_label = tk.Label(main, text="", bg=COLORS["panel_2"], fg=COLORS["text"], font=_font(9), padx=12, pady=7)

    def show_page(self, name: str) -> None:
        for page in getattr(self, "pages", {}).values():
            page.pack_forget()
        if hasattr(self, "pages"):
            self.pages[name].pack(fill="both", expand=True)
        for label, button in self.nav_buttons.items():
            active = label == name
            button.configure(style="SidebarActive.TButton" if active else "Sidebar.TButton")

    def require_connection(self) -> None:
        if not self.backend.connected:
            raise BackendError("Connect to the simulator or NI FPGA first.")

    def pause_host(self) -> None:
        try:
            self.require_connection()
            self.backend.pause()
            self.toast("Host execution paused", "warning")
        except (BackendError, RuntimeError, OSError) as exc:
            self.show_error(str(exc))

    def resume_host(self) -> None:
        try:
            self.require_connection()
            self.backend.resume()
            self.toast("Host execution resumed", "success")
        except (BackendError, RuntimeError, OSError) as exc:
            self.show_error(str(exc))

    def end_current_waypoint(self) -> None:
        try:
            self.require_connection()
            self.backend.end_current_waypoint()
            self.toast("Requested the next FPGA waypoint", "warning")
        except (BackendError, RuntimeError, OSError) as exc:
            self.show_error(str(exc))

    @property
    def any_experiment_active(self) -> bool:
        return any(experiment.active for experiment in EChemTipsApp._experiments_for(self).values())

    @staticmethod
    def _experiments_for(app: object) -> dict[str, object]:
        """Return the shared registry, with compatibility for headless hosts."""
        registry = getattr(app, "experiments", None)
        if isinstance(registry, dict):
            return registry
        result: dict[str, object] = {}
        if hasattr(app, "experiment"):
            result["approach_cv"] = getattr(app, "experiment")
        if hasattr(app, "scan_experiment"):
            result["scan_cv"] = getattr(app, "scan_experiment")
        return result

    @property
    def active_parameters(self) -> object | None:
        key = {
            "CV": "cv",
            "Approach": "approach",
            "Approach then CV": "approach_cv",
            "Approach then IT": "approach_it",
            "Scan Hopping CV": "scan_cv",
            "Scan Hopping IT": "scan_it",
        }.get(self.recorder.name)
        return self.experiments[key].params if key is not None else None

    def toggle_connection(self) -> None:
        if self.backend.connected:
            for key, experiment in self.experiments.items():
                if experiment.active:
                    self.stop_experiment(key)
            self.flush_acquisition()
            if self.recorder.active:
                self.finish_recording(self.active_parameters, status="aborted")
            self._stop_acquisition()
            self.backend.disconnect()
            self._set_connection_ui(False)
            self.toast("Device disconnected", "warning")
            return
        try:
            self.backend.connect()
            self._start_acquisition()
            self._set_connection_ui(True)
            self.toast(f"Connected to {self.backend.label}", "success")
        except BackendError as exc:
            self._set_connection_ui(False)
            self.show_error(str(exc))

    def _set_connection_ui(self, connected: bool) -> None:
        self.connection_dot.configure(fg=COLORS["success"] if connected else COLORS["muted"])
        capabilities: list[str] = []
        if connected and not self.backend.motion_available:
            capabilities.append("monitoring only")
        elif connected and self.backend.hardware_approach_cv_required and not self.backend.approach_cv_available:
            capabilities.append("manual motion only")
        if connected and not self.backend.full_rate_data_available:
            capabilities.append("register readback")
        capability = f" · {' · '.join(capabilities)}" if capabilities else ""
        self.connection_label.configure(text=f"{'Connected' if connected else 'Disconnected'} · {self.backend.label}{capability}")
        self.execution_label.configure(text="Idle" if connected else "Offline")
        self.connect_button.configure(text="Disconnect" if connected else "Connect", style="Quiet.TButton" if connected else "Accent.TButton")
        capabilities = self.backend.capabilities
        self.pause_button.state(["!disabled"] if connected and capabilities.pause_resume else ["disabled"])
        self.resume_button.state(["!disabled"] if connected and capabilities.pause_resume else ["disabled"])
        self.next_waypoint_button.state(["!disabled"] if connected and capabilities.end_current_waypoint else ["disabled"])
        self._sync_action_states()

    def _sync_action_states(self) -> None:
        """Keep Start/Stop affordances consistent with connection and ownership."""
        if not hasattr(self, "pages"):
            return
        connected = self.backend.connected
        watch = self.pages["Watch current"]
        if isinstance(watch, WatchPage):
            watch_owned = self.recorder.active and self.recorder.name == "Watch Current"
            watch.start_recording_button.state(["!disabled"] if connected and not self.any_experiment_active and not self.recorder.active else ["disabled"])
            watch.stop_recording_button.state(["!disabled"] if watch_owned else ["disabled"])
        experiment_pages = {
            "CV": "cv",
            "Approach": "approach",
            "Approach + CV": "approach_cv",
            "Approach + I-t": "approach_it",
            "Scan hopping + CV": "scan_cv",
            "Scan hopping + I-t": "scan_it",
        }
        can_start = connected and not self.any_experiment_active and not self.recorder.active
        for page_name, key in experiment_pages.items():
            page = self.pages[page_name]
            page.start_button.state(["!disabled"] if can_start else ["disabled"])
            page.stop_button.state(["!disabled"] if self.experiments[key].active else ["disabled"])

    def apply_settings(self, settings: AppSettings) -> None:
        if self.any_experiment_active:
            raise ValueError("Stop the experiment before changing instrument settings.")
        was_connected = self.backend.connected
        if was_connected:
            self.flush_acquisition()
        if self.recorder.active:
            self.finish_recording(self.active_parameters)
        if was_connected:
            self._stop_acquisition()
            self.backend.disconnect()
        self.store.save(settings)
        self.settings = settings
        self.backend = create_backend(settings, self.driver_module)
        self.experiment = ApproachCVExperiment(self.backend, settings)
        self.scan_experiment = ScanHoppingCVExperiment(self.backend, settings)
        self.cv_experiment = CVExperiment(self.backend, settings)
        self.approach_experiment = ApproachExperiment(self.backend, settings)
        self.approach_it_experiment = ApproachITExperiment(self.backend, settings)
        self.scan_it_experiment = ScanHoppingITExperiment(self.backend, settings)
        self.experiments = {
            "approach_cv": self.experiment,
            "scan_cv": self.scan_experiment,
            "cv": self.cv_experiment,
            "approach": self.approach_experiment,
            "approach_it": self.approach_it_experiment,
            "scan_it": self.scan_it_experiment,
        }
        self._last_experiment_state = self.experiment.state
        for page in self.pages.values():
            for widget in vars(page).values():
                if isinstance(widget, Plot):
                    widget.max_points = max(250, settings.display_max_points)
                    widget.buffer.max_points = widget.max_points
                    widget.buffer.compact()
                    widget.redraw()
        self.mode_badge.configure(text=settings.mode.upper())
        self._set_connection_ui(False)
        if was_connected:
            self.toast("Settings applied; reconnect to use the new backend", "warning")

    def finish_recording(self, parameters: object = None, status: str = "complete") -> Path | None:
        path = self.recorder.finish(self.settings, parameters, status=status)
        self._sync_action_states()
        return path

    def _start_acquisition(self) -> None:
        self._stop_acquisition()
        self._acquisition = AcquisitionWorker(self.backend)
        self._acquisition.start()

    def _stop_acquisition(self) -> AcquisitionDrain:
        worker = self._acquisition
        self._acquisition = None
        return worker.stop() if worker is not None else AcquisitionDrain([], None, 0)

    def flush_acquisition(self) -> None:
        """Process every queued sample plus a final FIFO snapshot."""
        worker = self._acquisition
        if worker is None:
            return
        drained = worker.pause_and_snapshot()
        try:
            self._consume_acquired(drained.samples, finalize=False)
            if drained.error is not None:
                raise BackendError(f"Acquisition flush failed: {drained.error}") from drained.error
        finally:
            worker.resume()

    def stop_experiment(self, which: str) -> None:
        """Barrier acquisition, cancel motion, ingest the final snapshot, save."""
        experiment = self.experiments[which]
        if not experiment.active:
            return
        worker = self._acquisition
        try:
            if worker is not None:
                before = worker.pause_and_snapshot()
                self._consume_acquired(before.samples, finalize=False)
                if before.error is not None:
                    raise BackendError(f"Acquisition failed before cancellation: {before.error}") from before.error
            hardware = self.backend.hardware_approach_cv_required
            self.backend.stop_motion()
            if not hardware:
                experiment.state = ExperimentState.ABORTED
                experiment.detail = "Experiment stopped by operator"
            if worker is not None:
                after = worker.pause_and_snapshot()
                self._consume_acquired(after.samples, finalize=False)
                if after.error is not None:
                    raise BackendError(f"Final acquisition drain failed: {after.error}") from after.error
            else:
                self._consume_acquired([], finalize=False)
            if experiment.active:
                experiment.state = ExperimentState.ABORTED
                experiment.detail = "Experiment stopped by operator"
            self.finish_recording(experiment.params, status="aborted")
            self.toast("Stop acknowledged; final data drained and partial recording saved", "warning")
        except (BackendError, OSError, ValueError, RuntimeError) as exc:
            if experiment.active:
                experiment.state = ExperimentState.ABORTED
            if self.recorder.active:
                self.finish_recording(experiment.params, status="error")
            self.show_error(str(exc))
        finally:
            if worker is not None:
                worker.resume()
            self._sync_action_states()

    def emergency_stop(self) -> None:
        try:
            worker = self._acquisition
            if worker is not None:
                before = worker.pause_and_snapshot()
                self._consume_acquired(before.samples, finalize=False)
            if self.backend.connected:
                self.backend.emergency_stop()
            if worker is not None:
                after = worker.pause_and_snapshot()
                self._consume_acquired(after.samples, finalize=False)
                acquisition_error = before.error or after.error
                if acquisition_error is not None:
                    raise BackendError(f"Emergency-stop acquisition drain failed: {acquisition_error}") from acquisition_error
            for experiment in self.experiments.values():
                if experiment.active:
                    experiment.state = ExperimentState.ABORTED
            self.finish_recording(self.active_parameters, status="aborted")
            self._stop_acquisition()
            if self.backend.connected:
                self.backend.disconnect()
            self._set_connection_ui(False)
            self._sync_action_states()
            self.toast("Emergency stop sent", "danger")
        except (BackendError, OSError, ValueError, RuntimeError) as exc:
            self.show_error(str(exc))

    def _consume_acquired(self, samples: list[Sample], *, finalize: bool = True) -> None:
        # Scan tagging happens before persistence. Empty batches still poll the
        # hardware state machine, which is independent of data acquisition.
        scan_cv_page = self.pages.get("Scan hopping + CV")
        if scan_cv_page is not None:
            scan_cv_page.on_samples(samples)
        scan_it_page = self.pages.get("Scan hopping + I-t")
        if scan_it_page is not None:
            scan_it_page.on_samples(samples)
        if samples:
            self._sample = samples[-1]
            for name, page in self.pages.items():
                if name not in {"Scan hopping + CV", "Scan hopping + I-t"}:
                    page.on_samples(samples)
        else:
            # FPGA execution can change state even when no acquisition packet
            # arrives in this GUI interval, so every active method is polled.
            for name in ("CV", "Approach", "Approach + I-t"):
                page = self.pages.get(name)
                if page is not None:
                    page.on_samples([])
            if self.experiment.active and self.backend.hardware_approach_cv_required and self._sample is not None:
                approach_page = self.pages["Approach + CV"]
                if isinstance(approach_page, ApproachCVPage):
                    approach_page.poll_status(self._sample)
        for sample in samples:
            self.recorder.append(sample)
        if finalize:
            self._finalize_experiments(bool(samples))

    def _finalize_experiments(self, had_samples: bool) -> None:
        del had_samples  # state, rather than GUI packet cadence, owns finalization
        if not self.recorder.active:
            sync = getattr(self, "_sync_action_states", None)
            if callable(sync):
                sync()
            return
        key = {
            "CV": "cv", "Approach": "approach", "Approach then CV": "approach_cv",
            "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv",
            "Scan Hopping IT": "scan_it",
        }.get(self.recorder.name)
        if key is None:
            self._sync_action_states()
            return
        experiment = EChemTipsApp._experiments_for(self)[key]
        if experiment.state not in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
            sync = getattr(self, "_sync_action_states", None)
            if callable(sync):
                sync()
            return
        if experiment.state == ExperimentState.COMPLETE:
            settings = getattr(self, "settings", None)
            should_save = settings is None or settings.auto_save or messagebox.askyesno(
                "Experiment complete", "Save the recorded experiment now?", parent=self,
            )
            path = self.finish_recording(experiment.params) if should_save else None
            if not should_save:
                self.recorder.discard()
            self.toast(f"Experiment complete · saved {path.name}" if path else "Experiment complete", "success")
        else:
            path = self.finish_recording(experiment.params, status="aborted")
            self.toast(f"Experiment stopped · saved {path.name}" if path else "Experiment stopped", "warning")
        sync = getattr(self, "_sync_action_states", None)
        if callable(sync):
            sync()

    def _poll(self) -> None:
        if self.backend.connected:
            try:
                worker = getattr(self, "_acquisition", None)
                if worker is None:
                    samples = self.backend.read_samples()
                    acquisition_error = None
                else:
                    drained = worker.drain()
                    samples = drained.samples
                    acquisition_error = drained.error
                EChemTipsApp._consume_acquired(self, samples, finalize=False)
                key = {
                    "CV": "cv", "Approach": "approach", "Approach then CV": "approach_cv",
                    "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv",
                    "Scan Hopping IT": "scan_it",
                }.get(self.recorder.name)
                terminal_recording = bool(
                    self.recorder.active and key is not None
                    and EChemTipsApp._experiments_for(self)[key].state in (ExperimentState.COMPLETE, ExperimentState.ABORTED)
                )
                # Establish a real acquisition barrier before closing a file:
                # pause the reader, consume everything it queued, then take one
                # last FIFO snapshot after experiment completion/cancellation.
                if terminal_recording and worker is not None:
                    final = worker.pause_and_snapshot()
                    EChemTipsApp._consume_acquired(self, final.samples, finalize=False)
                    samples = samples + final.samples
                    acquisition_error = acquisition_error or final.error
                    worker.resume()
                EChemTipsApp._finalize_experiments(self, bool(samples))
                status_fn = getattr(self.backend, "execution_status", None)
                if callable(status_fn) and hasattr(self, "execution_label"):
                    status = status_fn()
                    self.execution_label.configure(
                        text=f"{status.owner or 'host'} · {status.state.value} · {status.executed_waypoints}/{status.total_waypoints}"
                    )
                if acquisition_error is not None:
                    raise BackendError(str(acquisition_error)) from acquisition_error
            except (BackendError, OSError, ValueError, RuntimeError) as exc:
                worker = getattr(self, "_acquisition", None)
                if worker is not None:
                    worker.stop()
                    self._acquisition = None
                try:
                    self.backend.stop_motion()
                except (BackendError, OSError, ValueError):
                    pass
                try:
                    if self.recorder.active:
                        self.finish_recording(self.active_parameters, status="error")
                except (BackendError, OSError, ValueError):
                    pass
                for experiment in EChemTipsApp._experiments_for(self).values():
                    if experiment.active:
                        experiment.state = ExperimentState.ABORTED
                self.backend.disconnect()
                self._set_connection_ui(False)
                self.show_error(str(exc))
        self.after(80, self._poll)

    def toast(self, message: str, level: str = "info") -> None:
        foreground = {"success": COLORS["success"], "warning": COLORS["warning"], "danger": COLORS["danger"]}.get(level, COLORS["text"])
        self.toast_label.configure(text=message, fg=foreground)
        self.toast_label.place(relx=1.0, rely=1.0, anchor="se", x=-22, y=-18)
        self.after(4200, self.toast_label.place_forget)

    def show_error(self, message: str) -> None:
        messagebox.showerror("eChemTips", message, parent=self)

    def close(self) -> None:
        if self.any_experiment_active and not messagebox.askyesno("eChemTips", "An experiment is running. Stop it and close?", parent=self):
            return
        try:
            worker = self._acquisition
            if worker is not None:
                before = worker.pause_and_snapshot()
                self._consume_acquired(before.samples, finalize=False)
            if self.backend.connected:
                self.backend.emergency_stop()
            if worker is not None:
                after = worker.pause_and_snapshot()
                self._consume_acquired(after.samples, finalize=False)
            if self.recorder.active:
                self.finish_recording(self.active_parameters, status="aborted")
        finally:
            self._stop_acquisition()
            try:
                if self.backend.connected:
                    self.backend.disconnect()
            finally:
                self.destroy()
