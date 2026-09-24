"""PySide6 operator interface for eChemTips.

The experiment, acquisition, recording, and FPGA services intentionally stay
independent of Qt. This module owns only operator interaction and rendering.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .acquisition import AcquisitionDrain, AcquisitionWorker
from .branding import application_icon, configure_application_identity, logo_label
from .backends import BackendError, InstrumentBackend, create_backend
from .data import DataRecorder
from .navigation import EXPERIMENTS, ANCHORED, FavoriteStore, ExperimentLibrary
from .diagnostics import (
    pipette_radius_nm,
    resistance_fit,
    save_json_report,
    signal_statistics,
    stray_capacitance_pf,
    suggested_baseline_threshold_pa,
)
from .experiments import (
    ApproachCVExperiment,
    ApproachExperiment,
    ApproachITExperiment,
    CVExperiment,
    ExperimentState,
    ScanHoppingCVExperiment,
    ScanHoppingITExperiment,
)
from .models import (
    MAP_COLORMAPS,
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
from .qt_common import (
    COLORS,
    Card,
    Check,
    Choice,
    Field,
    Heatmap,
    InfoButton,
    Plot,
    PlotPanel,
    ProgramDiagram,
    TimedXYPlot,
    add_field,
    application_stylesheet,
    button,
    configure_pyqtgraph,
    current_display_scale,
    label,
    scroll_area,
)


FEEDBACK_CHANNELS = ("Current 1", "Current 2")
PA_PER_NA = 1000.0


def _feedback_current(sample: Sample, channel: str) -> float:
    return sample.current1_na if channel == "Current 1" else sample.current2_na


def _contact_help() -> QtWidgets.QLabel:
    text = label(
        "Contact: current reaches +threshold or −threshold.\n"
        "The approach ends and the next step starts automatically.",
        "muted",
        word_wrap=True,
    )
    text.setObjectName("contactHelp")
    text.setToolTip(
        "Select Current 1 or Current 2 and a threshold magnitude in pA. "
        "For example, 2000 pA triggers at +2000 or −2000 pA. Keep the pre-contact baseline inside this range. "
        "After contact, the experiment continues automatically. Settling time may be zero."
    )
    text.setMaximumWidth(330)
    text.setMinimumHeight(text.heightForWidth(text.maximumWidth()))
    text.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Minimum)
    return text


def _vbox(widget: QtWidgets.QWidget, margins: tuple[int, int, int, int] = (0, 0, 0, 0), spacing: int = 10) -> QtWidgets.QVBoxLayout:
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def _hbox(widget: QtWidgets.QWidget, margins: tuple[int, int, int, int] = (0, 0, 0, 0), spacing: int = 8) -> QtWidgets.QHBoxLayout:
    layout = QtWidgets.QHBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def _grid(widget: QtWidgets.QWidget, columns: int = 2) -> QtWidgets.QGridLayout:
    layout = QtWidgets.QGridLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(12)
    layout.setVerticalSpacing(10)
    for column in range(columns):
        layout.setColumnStretch(column, 1)
    return layout


def _plot_card(title: str, plot: QtWidgets.QWidget, *, help_text: str = "") -> Card:
    card = Card(title, help_text=help_text)
    layout = _vbox(card.body)
    layout.addWidget(plot, 1)
    return card


def _approach_curves_view(latest: Plot, history: TimedXYPlot) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    layout = _vbox(page)
    content = QtWidgets.QWidget()
    content_layout = _vbox(content)
    splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
    latest.configure(height=125); history.configure(height=125)
    latest_section = QtWidgets.QWidget(); latest_layout = _vbox(latest_section, spacing=3)
    heading = QtWidgets.QHBoxLayout()
    heading.addWidget(label("Latest approach", "cardTitle"), 1)
    latest_layout.addLayout(heading); latest_layout.addWidget(latest, 1)
    history_section = QtWidgets.QWidget(); history_layout = _vbox(history_section, spacing=3)
    heading = QtWidgets.QHBoxLayout()
    heading.addWidget(label("Approach history · 60 s", "cardTitle"), 1)
    history_layout.addLayout(heading); history_layout.addWidget(history, 1)
    splitter.addWidget(latest_section); splitter.addWidget(history_section)
    splitter.setSizes([175, 175])
    content_layout.addWidget(splitter)
    content.setMinimumHeight(360)
    layout.addWidget(scroll_area(content))
    return page


def _program_card(
    title: str,
    y_label: str,
    fields: tuple[Field, ...],
    names: tuple[str, ...],
    *,
    stepped: bool = False,
) -> tuple[Card, ProgramDiagram]:
    card = Card(title)
    diagram = ProgramDiagram(y_label)
    _vbox(card.body).addWidget(diagram)

    def refresh(*_args: object) -> None:
        """Rebuild the explanatory profile from its current field values."""
        try:
            values = [field.float() for field in fields]
        except ValueError:
            diagram.set_profile([], [])
            return
        diagram.set_profile(values, list(names), stepped=stepped)

    for field in fields:
        field.entry.textChanged.connect(refresh)
    refresh()
    return card, diagram


def _format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = ([f"{hours} h"] if hours else []) + ([f"{minutes} min"] if minutes else []) + ([f"{seconds} s"] if seconds or not (hours or minutes) else [])
    return " ".join(parts)


def _scan_summary_card(parameter_factory, triggers: list[QtCore.QObject]) -> tuple[Card, QtWidgets.QLabel, QtWidgets.QLabel]:
    card = Card("Calculated scan")
    layout = _vbox(card.body, spacing=5)
    spacing_label = label("Hop spacing —", "statusStrong")
    duration_label = label("Estimated duration —", "muted", word_wrap=True)
    layout.addWidget(spacing_label)
    layout.addWidget(duration_label)

    def refresh(*_args: object) -> None:
        """Recalculate scan spacing and deterministic-duration summary."""
        try:
            parameters = parameter_factory()
            dx, dy = parameters.spacing_um
            spacing_label.setText(f"Hop spacing  X {dx:g} µm · Y {dy:g} µm")
            duration_label.setText(
                f"Estimated known time ≈ {_format_duration(parameters.estimated_known_duration_s())}, "
                "plus the first approach and the initial move from the current position."
            )
        except (ValueError, ZeroDivisionError, OverflowError):
            spacing_label.setText("Hop spacing —")
            duration_label.setText("Enter valid scan parameters to calculate duration.")

    for trigger in triggers:
        if isinstance(trigger, QtWidgets.QLineEdit):
            trigger.textChanged.connect(refresh)
        elif isinstance(trigger, QtWidgets.QCheckBox):
            trigger.stateChanged.connect(refresh)
        elif isinstance(trigger, QtWidgets.QComboBox):
            trigger.currentTextChanged.connect(refresh)
    refresh()
    return card, spacing_label, duration_label


def _left_scroll(widget: QtWidgets.QWidget, width: int = 390) -> QtWidgets.QScrollArea:
    area = scroll_area(widget, minimum_width=330)
    area.setMaximumWidth(max(width + 90, 440))
    return area


class BasePage(QtWidgets.QWidget):
    """Common page shell with a title, wrapped description, and body area."""
    def __init__(self, app: "EChemTipsApp", title: str, description: str) -> None:
        super().__init__()
        self.app = app
        outer = _vbox(self, spacing=8)
        title_widget = label(title, "pageTitle")
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(title_widget, 1)
        outer.addLayout(heading)
        self.body = QtWidgets.QWidget()
        self.body.setObjectName("window")
        outer.addWidget(self.body, 1)

    def on_sample(self, _sample: Sample) -> None:
        """Receive the newest sample; subclasses override when interested."""
        pass

    def on_samples(self, samples: list[Sample]) -> None:
        """Default batch handler forwards only the newest sample."""
        if samples:
            self.on_sample(samples[-1])


class DiagnosticWorkflowPage(BasePage):
    """Guided, non-recording diagnostic capture with a dedicated CV runner."""

    def __init__(self, app: "EChemTipsApp", title: str, description: str) -> None:
        super().__init__(app, title, description)
        self.is_busy = False
        self._capture_kind = ""
        self._samples: list[Sample] = []
        self._capture_start: float | None = None
        self._duration_s = 0.0
        self._cv_runner = CVExperiment(app.backend, app.settings)
        self.report: dict[str, Any] = {"workflow": title, "results": []}

    def _ready(self) -> None:
        self.app.require_connection()
        if self.app.any_experiment_active or self.app.recorder.active:
            raise RuntimeError("Stop the active experiment or recording before starting diagnostics.")

    def _begin_timed(self, kind: str, duration_s: float, potential_v: float, circuit: str, resistance_mohm: float | None = None) -> None:
        self._ready()
        if not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("Capture duration must be greater than zero.")
        self.app.backend.set_diagnostic_circuit(circuit, resistance_mohm)
        self.app.backend.set_voltage(1, potential_v)
        self._capture_kind, self._duration_s = kind, duration_s
        self._samples, self._capture_start, self.is_busy = [], None, True
        self.trace_plot.clear(); self.response_plot.clear(); self._set_busy(True)

    def _begin_sweep(self, kind: str, start_v: float, end_v: float, rate_v_s: float, circuit: str, resistance_mohm: float | None = None) -> None:
        self._ready()
        self.app.backend.set_diagnostic_circuit(circuit, resistance_mohm)
        self._cv_runner = CVExperiment(self.app.backend, self.app.settings)
        self._cv_runner.start(CVParameters(start_v=start_v, vertex1_v=end_v, vertex2_v=start_v, scan_rate_v_s=rate_v_s, cycles=1))
        self._capture_kind, self._samples, self._capture_start, self.is_busy = kind, [], None, True
        self.trace_plot.clear(); self.response_plot.clear(); self._set_busy(True)

    def _set_busy(self, busy: bool) -> None:
        for action in self.action_buttons:
            action.setEnabled(not busy and self.app.backend.connected)
        self.stop_button.setEnabled(busy)
        self.save_button.setEnabled(not busy and bool(self.report["results"]))
        self.app._sync_action_states()

    def sync_actions(self, connected: bool, another_busy: bool) -> None:
        """Enable diagnostic actions only when this workflow may own hardware."""
        for action in self.action_buttons:
            action.setEnabled(connected and not self.is_busy and not another_busy and not self.app.recorder.active)
        self.stop_button.setEnabled(self.is_busy)
        self.save_button.setEnabled(not self.is_busy and bool(self.report["results"]))

    def stop(self) -> None:
        """Abort the current diagnostic and restore the normal fixture mode."""
        if not self.is_busy:
            return
        if self._cv_runner.active:
            self._cv_runner.abort()
        self.app.backend.set_diagnostic_circuit("normal")
        self.is_busy = False
        self.status_label.setText("Stopped by operator")
        self._set_busy(False)

    def _complete(self) -> None:
        try:
            result = self.analyze_capture(self._capture_kind, self._samples)
            self.report["results"].append(result)
            self.results.setPlainText(self.format_report())
            self.status_label.setText(f"Complete · {len(self._samples):,} samples")
        except ValueError as exc:
            self.status_label.setText(f"Could not calculate result: {exc}")
        finally:
            self.app.backend.set_diagnostic_circuit("normal")
            self.is_busy = False
            self._set_busy(False)

    def on_samples(self, samples: list[Sample]) -> None:
        """Accumulate, plot, and complete the active timed/sweep diagnostic."""
        if not self.is_busy:
            return
        if samples:
            if self._capture_start is None:
                self._capture_start = samples[0].elapsed_s
            self._samples.extend(samples)
            channel = self.channel.get()
            for sample in samples:
                elapsed = sample.elapsed_s - self._capture_start
                current = _feedback_current(sample, channel)
                self.trace_plot.append(elapsed, current, redraw=False)
                self.response_plot.append(sample.voltage1_v, current, redraw=False)
            self.trace_plot.request_redraw(); self.response_plot.request_redraw()
        if self._cv_runner.active:
            self._cv_runner.tick_samples(samples)
            self.status_label.setText(self._cv_runner.detail)
            if not self._cv_runner.active:
                self._complete()
        elif self._capture_start is not None and samples and samples[-1].elapsed_s - self._capture_start >= self._duration_s:
            self._complete()

    def save_report(self) -> None:
        """Save accumulated diagnostic results with the current settings."""
        try:
            payload = {**self.report, "settings": asdict(self.app.settings)}
            path = save_json_report(self.app.settings.save_directory, self.report_prefix, payload)
            self.app.toast(f"Saved {path.name}", "success")
        except OSError as exc:
            self.app.show_error(str(exc))

    def analyze_capture(self, kind: str, samples: list[Sample]) -> dict[str, Any]:
        """Convert one capture to report data; concrete workflows implement it."""
        raise NotImplementedError

    def format_report(self) -> str:
        """Return human-readable accumulated results for the page."""
        raise NotImplementedError


class PreflightPage(DiagnosticWorkflowPage):
    """Guided noise, open-input, and known-resistor electrical checks."""
    report_prefix = "guided_preflight"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Guided preflight", "Verify noise, wiring, response, and current scaling before an experiment. Follow the fixture instruction shown for each step.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = QtWidgets.QWidget(); left = _vbox(controls)
        setup = Card("Test configuration", "On real hardware, prepare the stated circuit before pressing a step button. Simulation changes fixture automatically.")
        g = _grid(setup.body)
        channel_box = QtWidgets.QWidget(); cl = _vbox(channel_box, spacing=5); cl.addWidget(label("Current input", "muted")); self.channel = Choice(FEEDBACK_CHANNELS, "Current 1"); cl.addWidget(self.channel); g.addWidget(channel_box, 0, 0)
        self.duration = add_field(g, Field("Noise duration", "5", "s"), 0, 1)
        self.resistor = add_field(g, Field("Known resistance", "100", "MΩ"), 1, 0)
        self.sweep_start = add_field(g, Field("Sweep start", "-0.5", "V"), 2, 0); self.sweep_end = add_field(g, Field("Sweep end", "0.5", "V"), 2, 1)
        self.sweep_rate = add_field(g, Field("Sweep rate", "0.2", "V/s"), 3, 0)
        left.addWidget(setup)
        steps = Card("Guided checks", "Run in order. The suggested Δi threshold is a conservative starting value, not an automatic safety guarantee.")
        sl = _vbox(steps.body)
        self.noise_button = button("1 · Measure zero-current noise", self.run_noise, "primary"); self.noise_button.setToolTip("Real device: hold E1 at 0 V with the normal input wiring and no electrochemical event.")
        self.open_button = button("2 · Measure open-input capacitance", self.run_open); self.open_button.setToolTip("Real device: disconnect the amplifier signal input before running the triangular sweep.")
        self.resistor_button = button("3 · Verify with known resistor", self.run_resistor); self.resistor_button.setToolTip("Real device: connect the stated precision resistor in the current path.")
        for widget in (self.noise_button, self.open_button, self.resistor_button): sl.addWidget(widget)
        actions = QtWidgets.QWidget(); al = _hbox(actions); self.stop_button = button("Stop test", self.stop, "danger"); self.save_button = button("Save report", self.save_report); al.addWidget(self.stop_button); al.addWidget(self.save_button); sl.addWidget(actions)
        self.action_buttons = [self.noise_button, self.open_button, self.resistor_button]
        left.addWidget(steps); left.addStretch(1); root.addWidget(_left_scroll(controls))
        right = QtWidgets.QWidget(); rl = _vbox(right); self.status_label = label("Connect, prepare the first fixture, then begin.", "statusStrong"); rl.addWidget(self.status_label)
        plots = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.trace_plot = Plot("Diagnostic current vs time", "Current (nA)", (COLORS["blue"],), app.settings.display_max_points)
        self.response_plot = Plot("Current vs potential E1", "Current (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential E1 (V)")
        plots.addWidget(_plot_card("Live current", self.trace_plot)); plots.addWidget(_plot_card("Electrical response", self.response_plot)); plots.setSizes([280, 280]); rl.addWidget(plots, 1)
        self.results = QtWidgets.QPlainTextEdit(); self.results.setReadOnly(True); self.results.setPlaceholderText("Calculated checks will appear here."); self.results.setMaximumHeight(150); rl.addWidget(self.results); root.addWidget(right, 1)
        self._set_busy(False)

    def run_noise(self) -> None:
        """Begin the configured zero-current noise capture."""
        try: self._begin_timed("noise", self.duration.float(), 0.0, "normal"); self.status_label.setText("Step 1/3 · measuring baseline noise at E1 = 0 V")
        except (ValueError, RuntimeError, BackendError) as exc: self.app.show_error(str(exc))

    def run_open(self) -> None:
        """Begin the configured open-input triangular sweep."""
        try: self._begin_sweep("open", self.sweep_start.float(), self.sweep_end.float(), self.sweep_rate.float(), "open"); self.status_label.setText("Step 2/3 · open-input triangular sweep")
        except (ValueError, RuntimeError, BackendError) as exc: self.app.show_error(str(exc))

    def run_resistor(self) -> None:
        """Begin the known-resistor triangular sweep."""
        try: self._begin_sweep("resistor", self.sweep_start.float(), self.sweep_end.float(), self.sweep_rate.float(), "resistor", self.resistor.float()); self.status_label.setText("Step 3/3 · known-resistor response")
        except (ValueError, RuntimeError, BackendError) as exc: self.app.show_error(str(exc))

    def analyze_capture(self, kind: str, samples: list[Sample]) -> dict[str, Any]:
        """Calculate noise, capacitance, or resistor metrics for one capture."""
        channel = self.channel.get()
        if kind == "noise":
            stats = signal_statistics(samples, channel)
            return {"test": kind, "statistics": asdict(stats), "suggested_delta_i_threshold_pa": suggested_baseline_threshold_pa(stats)}
        if kind == "open":
            return {"test": kind, "stray_capacitance_pf": stray_capacitance_pf(samples, channel), "statistics": asdict(signal_statistics(samples, channel))}
        fit, measured = resistance_fit(samples, channel); expected = self.resistor.float()
        return {"test": kind, "expected_resistance_mohm": expected, "measured_resistance_mohm": measured, "error_percent": 100 * (measured - expected) / expected, "fit": asdict(fit)}

    def format_report(self) -> str:
        """Format completed preflight checks for the result panel."""
        lines = []
        for result in self.report["results"]:
            if result["test"] == "noise": lines.append(f"Noise · RMS {result['statistics']['rms_noise_pa']:.2f} pA · suggested Δi threshold {result['suggested_delta_i_threshold_pa']:.1f} pA")
            elif result["test"] == "open": lines.append(f"Open input · estimated stray capacitance {result['stray_capacitance_pf']:.2f} pF")
            else: lines.append(f"Known resistor · measured {result['measured_resistance_mohm']:.3g} MΩ · error {result['error_percent']:+.2f}% · R² {result['fit']['r_squared']:.5f}")
        return "\n".join(lines)


class PipetteCharacterizationPage(DiagnosticWorkflowPage):
    """Guided stability and I–E workflow for one immersed pipette."""
    report_prefix = "pipette_characterization"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Characterize pipette", "Document pipette stability and estimate resistance and aperture radius before scanning.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = QtWidgets.QWidget(); left = _vbox(controls)
        setup = Card("Pipette and electrolyte", help_text="The radius estimate uses a conical-pipette approximation and should be reported as an estimate.")
        g = _grid(setup.body)
        self.pipette_id = add_field(g, Field("Pipette ID", "pipette-001"), 0, 0)
        channel_box = QtWidgets.QWidget(); cl = _vbox(channel_box, spacing=5); cl.addWidget(label("Current input", "muted")); self.channel = Choice(FEEDBACK_CHANNELS, "Current 1"); cl.addWidget(self.channel); g.addWidget(channel_box, 0, 1)
        self.conductivity = add_field(g, Field("Conductivity", "1.0", "S/m"), 1, 0); self.half_angle = add_field(g, Field("Pipette half-angle", "10", "°"), 1, 1)
        self.stability_potential = add_field(g, Field("Stability potential E1", "0.1", "V"), 2, 0); self.duration = add_field(g, Field("Stability duration", "10", "s"), 2, 1)
        self.sweep_start = add_field(g, Field("I–E sweep start", "-0.2", "V"), 3, 0); self.sweep_end = add_field(g, Field("I–E sweep end", "0.2", "V"), 3, 1); self.sweep_rate = add_field(g, Field("I–E sweep rate", "0.1", "V/s"), 4, 0)
        left.addWidget(setup)
        steps = Card("Characterization", "Real device: immerse the pipette in the characterization electrolyte with the normal amplifier connection.")
        sl = _vbox(steps.body)
        self.stability_button = button("1 · Measure current stability", self.run_stability, "primary"); self.sweep_button = button("2 · Measure I–E response", self.run_sweep)
        sl.addWidget(self.stability_button); sl.addWidget(self.sweep_button)
        actions = QtWidgets.QWidget(); al = _hbox(actions); self.stop_button = button("Stop test", self.stop, "danger"); self.save_button = button("Save pipette profile", self.save_report); al.addWidget(self.stop_button); al.addWidget(self.save_button); sl.addWidget(actions)
        self.action_buttons = [self.stability_button, self.sweep_button]
        left.addWidget(steps); left.addStretch(1); root.addWidget(_left_scroll(controls))
        right = QtWidgets.QWidget(); rl = _vbox(right); self.status_label = label("Connect and prepare the immersed pipette.", "statusStrong"); rl.addWidget(self.status_label)
        plots = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.trace_plot = Plot("Pipette current vs time", "Current (nA)", (COLORS["blue"],), app.settings.display_max_points)
        self.response_plot = Plot("Pipette current vs potential E1", "Current (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential E1 (V)")
        plots.addWidget(_plot_card("Stability", self.trace_plot)); plots.addWidget(_plot_card("I–E response", self.response_plot)); plots.setSizes([280, 280]); rl.addWidget(plots, 1)
        self.results = QtWidgets.QPlainTextEdit(); self.results.setReadOnly(True); self.results.setPlaceholderText("Characterization results will appear here."); self.results.setMaximumHeight(150); rl.addWidget(self.results); root.addWidget(right, 1)
        self.report["pipette_id"] = self.pipette_id.entry.text(); self._set_busy(False)

    def run_stability(self) -> None:
        """Begin the immersed-pipette stability capture."""
        try: self._begin_timed("stability", self.duration.float(), self.stability_potential.float(), "pipette"); self.status_label.setText("Measuring immersed-pipette stability")
        except (ValueError, RuntimeError, BackendError) as exc: self.app.show_error(str(exc))

    def run_sweep(self) -> None:
        """Begin the immersed-pipette I–E sweep."""
        try: self._begin_sweep("pipette_sweep", self.sweep_start.float(), self.sweep_end.float(), self.sweep_rate.float(), "pipette"); self.status_label.setText("Measuring pipette I–E response")
        except (ValueError, RuntimeError, BackendError) as exc: self.app.show_error(str(exc))

    def analyze_capture(self, kind: str, samples: list[Sample]) -> dict[str, Any]:
        """Calculate stability or resistance/aperture estimates."""
        self.report["pipette_id"] = self.pipette_id.entry.text().strip() or "unnamed"
        channel = self.channel.get()
        if kind == "stability": return {"test": kind, "potential_v": self.stability_potential.float(), "statistics": asdict(signal_statistics(samples, channel))}
        fit, resistance = resistance_fit(samples, channel)
        radius = pipette_radius_nm(resistance, self.conductivity.float(), self.half_angle.float())
        return {"test": kind, "resistance_mohm": resistance, "estimated_radius_nm": radius, "conductivity_s_m": self.conductivity.float(), "half_angle_deg": self.half_angle.float(), "fit": asdict(fit)}

    def format_report(self) -> str:
        """Format the pipette identity and completed characterization results."""
        lines = [f"Pipette · {self.report['pipette_id']}"]
        for result in self.report["results"]:
            if result["test"] == "stability": lines.append(f"Stability · mean {result['statistics']['mean_na']:.4g} nA · RMS {result['statistics']['rms_noise_pa']:.2f} pA · drift {result['statistics']['drift_pa_s']:+.3f} pA/s")
            else: lines.append(f"I–E · {result['resistance_mohm']:.3g} MΩ · estimated radius {result['estimated_radius_nm']:.1f} nm · R² {result['fit']['r_squared']:.5f}")
        return "\n".join(lines)


class StatusCard(Card):
    """Shared method state, detail, progress, start, and stop controls."""
    def __init__(self, title: str, detail: str, start_text: str, start_slot, stop_slot) -> None:
        super().__init__(title)
        self.setProperty("compactControls", True)
        self.layout().setContentsMargins(12, 9, 12, 9)
        self.layout().setSpacing(5)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
        layout = _vbox(self.body, spacing=5)
        self.state_label = label("Ready", "statusStrong")
        self.layout().itemAt(0).layout().addWidget(self.state_label)
        self.detail_label = label(detail, "muted", word_wrap=True)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        actions = QtWidgets.QWidget()
        action_layout = _hbox(actions)
        self.action_layout = action_layout
        self.start_button = button(start_text, start_slot, "primary")
        self.stop_button = button("Stop experiment", stop_slot, "danger")
        self.stop_button.setEnabled(False)
        action_layout.addWidget(self.start_button)
        action_layout.addWidget(self.stop_button)
        action_layout.addStretch(1)
        layout.addWidget(self.detail_label)
        self.detail_label.setVisible(bool(detail))
        layout.addWidget(self.progress)
        layout.addWidget(actions)

    def update_status(self, update: object | None) -> None:
        """Render a state-machine update and restore actions at terminal state."""
        if update is None:
            return
        self.state_label.setText(update.state.value)
        self.detail_label.setText(update.detail.replace("I-t", "I–t"))
        self.detail_label.setVisible(bool(update.detail))
        self.progress.setValue(round(update.progress * 1000))
        terminal = update.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED)
        if terminal:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)


class InstrumentReadoutBar(QtWidgets.QFrame):
    """Compact, always-visible readback of the active instrument channels."""

    _CHANNELS = (
        ("x_um", "X", "µm"),
        ("y_um", "Y", "µm"),
        ("z_um", "Z", "µm"),
        ("voltage1_v", "E1", "V"),
        ("voltage2_v", "E2", "V"),
        ("current1_na", "i1", "nA"),
        ("current2_na", "i2", "nA"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("instrumentStrip")
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
        row = _hbox(self, (12, 8, 12, 8), 8)
        heading = label("LIVE", "stripHeading")
        heading.setToolTip("Live measured channels (not commanded positions)")
        row.addWidget(heading)

        self.value_labels: dict[str, QtWidgets.QLabel] = {}
        for attribute, caption, unit in self._CHANNELS:
            cell = QtWidgets.QWidget()
            cell_layout = _hbox(cell, spacing=4)
            cell_layout.addWidget(label(caption, "stripCaption"))
            value_label = label(f"— {unit}", "stripValue")
            value_label.setAccessibleName(f"{caption} measured value")
            cell_layout.addWidget(value_label)
            row.addWidget(cell, 1)
            self.value_labels[attribute] = value_label

    def set_sample(self, sample: Sample) -> None:
        """Update every persistent readback cell from the newest sample."""
        for attribute, _caption, unit in self._CHANNELS:
            value = getattr(sample, attribute)
            if unit == "nA":
                scale, unit = current_display_scale(getattr(self, "current_display_unit", "nA"), [value])
                value *= scale
            sign = "+" if attribute in {"voltage1_v", "voltage2_v", "current1_na", "current2_na"} else ""
            self.value_labels[attribute].setText(f"{value:{sign}.3f} {unit}")

    def clear(self) -> None:
        """Replace every persistent readback value with an unavailable marker."""
        for attribute, _caption, unit in self._CHANNELS:
            if unit == "nA" and getattr(self, "current_display_unit", "nA") == "pA":
                unit = "pA"
            self.value_labels[attribute].setText(f"— {unit}")


class WatchPage(BasePage):
    """Opt-in Current 1/2 live view, potential control, and recording page."""
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Watch current", "Monitor Current 1 and Current 2, control the potential outputs, and record without blocking the display.")
        layout = QtWidgets.QHBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        controls = Card("Output control", "Potential changes are limited to ±10 V and recorded with the trace.")
        controls.setMinimumWidth(285)
        controls.setMaximumWidth(350)
        form = _vbox(controls.body)
        self.v1 = Field("Potential E1 · AO3", "0.10", "V")
        self.v2 = Field("Potential E2 · AO4", "0.00", "V")
        form.addWidget(self.v1)
        form.addWidget(self.v2)
        form.addWidget(button("Apply potentials", self.apply_voltage, "primary"))
        self.start_recording_button = button("Start recording", self.start_recording)
        self.stop_recording_button = button("Stop and save", self.stop_recording, "danger")
        self.stop_recording_button.setEnabled(False)
        form.addWidget(self.start_recording_button)
        form.addWidget(self.stop_recording_button)
        self.live_enabled = False
        self.live_button = button("Start live view", self.toggle_live_view)
        form.addWidget(self.live_button)
        form.addWidget(button("Clear graphs", self.clear_plots))
        self.live_status_label = label("Live view off", "muted")
        self.live_status_label.setWordWrap(True)
        form.addWidget(self.live_status_label)
        form.addSpacing(12)
        form.addWidget(label("LIVE READOUT", "muted"))
        self.current_label = label("i1  — nA", "readout")
        self.current2_label = label("i2  — nA", "readout")
        self.position_label = label("Z  — µm", "muted")
        form.addWidget(self.current_label)
        form.addWidget(self.current2_label)
        form.addWidget(self.position_label)
        form.addStretch(1)
        control_scroll = scroll_area(controls, minimum_width=285)
        control_scroll.setMaximumWidth(350)
        layout.addWidget(control_scroll)

        history = Card("Current history · 30 s")
        history_layout = _vbox(history.body)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.current1_plot = Plot("Current 1 vs time", "Current 1 (nA)", (COLORS["accent"],), app.settings.display_max_points, rolling_window_s=30)
        self.current2_plot = Plot("Current 2 vs time", "Current 2 (nA)", (COLORS["blue"],), app.settings.display_max_points, rolling_window_s=30)
        self.current1_plot.configure(height=145); self.current2_plot.configure(height=145)
        splitter.addWidget(self.current1_plot); splitter.addWidget(self.current2_plot)
        splitter.setSizes([1, 1]); history_layout.addWidget(splitter)
        layout.addWidget(history, 1)
        self.plot = self.current1_plot

    def clear_plots(self) -> None:
        """Clear both current display buffers without affecting recording."""
        self.current1_plot.clear()
        self.current2_plot.clear()

    def apply_voltage(self) -> None:
        """Validate and apply idle E1/E2 commands through the backend."""
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("Stop the experiment before changing manual outputs.")
            voltage1, voltage2 = self.v1.float(), self.v2.float()
            if not all(math.isfinite(value) and -10 <= value <= 10 for value in (voltage1, voltage2)):
                raise ValueError("Both potential outputs must be finite values between -10 V and +10 V.")
            if self.app.settings.mode == "NI FPGA" and abs(voltage1 * self.app.settings.command_voltage_ratio) > 10:
                raise ValueError("Voltage 1 exceeds the AO3 range at the configured command ratio.")
            self.app.backend.set_live_potential(1, voltage1)
            self.app.backend.set_live_potential(2, voltage2)
            self.app.toast("Potential outputs updated", "success")
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
        """Claim the recorder for Watch Current and start plotting new data."""
        if not self._can_control_recording():
            return
        try:
            if self.app.recorder.active:
                raise BackendError("A recording is already active.")
            self.app.recorder.start("Watch Current", self.app.settings)
            self.set_live_view(True)
            self.app._sync_action_states()
            self.app.toast("Recording current channels", "success")
        except (BackendError, OSError, ValueError) as exc:
            self.app.show_error(f"Could not start the recording: {exc}")

    def stop_recording(self) -> None:
        """Barrier-drain acquisition and finalize the Watch Current files."""
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
        """Compatibility action that starts or stops Watch Current recording."""
        self.stop_recording() if self.app.recorder.active else self.start_recording()

    def toggle_live_view(self) -> None:
        """Start or stop opt-in plotting without changing recorder ownership."""
        if self.live_enabled:
            self.set_live_view(False)
            self.app.toast("Live view stopped")
            return
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("Use the active experiment page to view its data while the experiment is running.")
            self.set_live_view(True)
            self.app.toast("Live view started; plotting new samples", "success")
        except BackendError as exc:
            self.app.show_error(str(exc))

    def set_live_view(self, enabled: bool, *, clear_on_start: bool = True) -> None:
        """Set opt-in current rendering and reset its local time origin."""
        enabled = bool(enabled)
        if enabled and not self.live_enabled and clear_on_start:
            self.clear_plots()
            self._live_time_origin_s: float | None = None
            self.current_label.setText(f"i1  — {'pA' if self.app.settings.current_display_unit == 'pA' else 'nA'}")
            self.current2_label.setText(f"i2  — {'pA' if self.app.settings.current_display_unit == 'pA' else 'nA'}")
            self.position_label.setText("Z  — µm")
        self.live_enabled = enabled
        self.live_button.setText("Stop live view" if enabled else "Start live view")
        self.live_status_label.setText(
            f"Live view on · {self.app.settings.monitor_window_s:g} s"
            if enabled
            else "Live view off"
        )

    def on_sample(self, sample: Sample) -> None:
        """Forward one sample through the batch-aware current renderer."""
        self.on_samples([sample])

    def on_samples(self, samples: list[Sample]) -> None:
        """Plot new current samples only while the explicit live view is active."""
        if not samples or not self.live_enabled:
            return
        latest = samples[-1]
        scale, unit = current_display_scale(self.app.settings.current_display_unit, [latest.current1_na])
        self.current_label.setText(f"i1  {latest.current1_na * scale:+.3f} {unit}")
        scale, unit = current_display_scale(self.app.settings.current_display_unit, [latest.current2_na])
        self.current2_label.setText(f"i2  {latest.current2_na * scale:+.3f} {unit}")
        self.position_label.setText(f"Z  {latest.z_um:.3f} µm")
        for sample in samples:
            origin = getattr(self, "_live_time_origin_s", None)
            if origin is None:
                origin = sample.elapsed_s; self._live_time_origin_s = origin
            elapsed = max(0.0, sample.elapsed_s - origin)
            self.current1_plot.append(elapsed, sample.current1_na, redraw=False)
            self.current2_plot.append(elapsed, sample.current2_na, redraw=False)
        self.current1_plot.request_redraw(); self.current2_plot.request_redraw()


class WatchPositionPage(BasePage):
    """Opt-in independent X/Y/Z position live view and recording page."""
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Watch position", "Monitor the measured X, Y, and Z piezo positions as a dedicated time trace.")
        layout = QtWidgets.QHBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        controls = Card("Position monitor")
        controls.setMinimumWidth(285)
        controls.setMaximumWidth(350)
        form = _vbox(controls.body)
        self.start_recording_button = button("Start recording", self.start_recording)
        self.stop_recording_button = button("Stop and save", self.stop_recording, "danger")
        self.stop_recording_button.setEnabled(False)
        self.live_enabled = False
        self.live_button = button("Start live view", self.toggle_live_view)
        form.addWidget(self.start_recording_button)
        form.addWidget(self.stop_recording_button)
        form.addWidget(self.live_button)
        form.addWidget(button("Clear graphs", self.clear_plots))
        self.live_status_label = label("Live view off", "muted", word_wrap=True)
        form.addWidget(self.live_status_label)
        form.addStretch(1)
        control_scroll = scroll_area(controls, minimum_width=285)
        control_scroll.setMaximumWidth(350)
        layout.addWidget(control_scroll)

        history = Card("Position history · 30 s")
        history_layout = _vbox(history.body)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.x_plot = Plot("X position vs time", "X position (µm)", (COLORS["blue"],), app.settings.display_max_points, "", rolling_window_s=30)
        self.y_plot = Plot("Y position vs time", "Y position (µm)", (COLORS["warning"],), app.settings.display_max_points, "", rolling_window_s=30)
        self.z_plot = Plot("Z position vs time", "Z position (µm)", (COLORS["accent"],), app.settings.display_max_points, rolling_window_s=30)
        for plot in (self.x_plot, self.y_plot, self.z_plot):
            plot.configure(height=100); splitter.addWidget(plot)
        splitter.setSizes([1, 1, 1]); history_layout.addWidget(splitter)
        layout.addWidget(history, 1)
        self.plot = self.x_plot

    def clear_plots(self) -> None:
        """Clear X, Y, and Z display buffers without affecting recording."""
        for plot in (self.x_plot, self.y_plot, self.z_plot):
            plot.clear()

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
        """Claim the recorder for Watch Position and start plotting new data."""
        if not self._can_control_recording():
            return
        try:
            if self.app.recorder.active:
                raise BackendError("A recording is already active.")
            self.app.recorder.start("Watch Position", self.app.settings)
            self.set_live_view(True)
            self.app._sync_action_states()
            self.app.toast("Recording measured piezo positions", "success")
        except (BackendError, OSError, ValueError) as exc:
            self.app.show_error(f"Could not start the recording: {exc}")

    def stop_recording(self) -> None:
        """Barrier-drain and finalize the Watch Position recording."""
        if not self._can_control_recording():
            return
        try:
            if not self.app.recorder.active or self.app.recorder.name != "Watch Position":
                raise BackendError("There is no Watch Position recording to stop.")
            self.app.flush_acquisition()
            path = self.app.finish_recording()
            self.app._sync_action_states()
            self.app.toast(f"Saved {path.name}" if path else "Recording stopped", "success")
        except (BackendError, OSError, ValueError) as exc:
            self.app.show_error(f"Could not stop the recording: {exc}")

    def toggle_live_view(self) -> None:
        """Start or stop opt-in position plotting."""
        if self.live_enabled:
            self.set_live_view(False)
            self.app.toast("Position live view stopped")
            return
        try:
            self.app.require_connection()
            if self.app.any_experiment_active:
                raise BackendError("Use the active experiment page to view its data while the experiment is running.")
            self.set_live_view(True)
            self.app.toast("Position live view started", "success")
        except BackendError as exc:
            self.app.show_error(str(exc))

    def set_live_view(self, enabled: bool, *, clear_on_start: bool = True) -> None:
        """Set position rendering and reset the view-local time origin."""
        enabled = bool(enabled)
        if enabled and not self.live_enabled and clear_on_start:
            self.clear_plots()
            self._live_time_origin_s: float | None = None
        self.live_enabled = enabled
        self.live_button.setText("Stop live view" if enabled else "Start live view")
        self.live_status_label.setText(f"Live view on · {self.app.settings.monitor_window_s:g} s" if enabled else "Live view off")

    def on_samples(self, samples: list[Sample]) -> None:
        """Append new X/Y/Z samples only while live position view is active."""
        if not samples or not self.live_enabled:
            return
        for sample in samples:
            origin = getattr(self, "_live_time_origin_s", None)
            if origin is None:
                origin = sample.elapsed_s; self._live_time_origin_s = origin
            elapsed = max(0.0, sample.elapsed_s - origin)
            self.x_plot.append(elapsed, sample.x_um, redraw=False)
            self.y_plot.append(elapsed, sample.y_um, redraw=False)
            self.z_plot.append(elapsed, sample.z_um, redraw=False)
        for plot in (self.x_plot, self.y_plot, self.z_plot):
            plot.request_redraw()


class ManagedExperimentPage(BasePage):
    """Base for pages that own one state machine and one recorder lifecycle."""
    experiment_key = ""
    recording_name = ""
    manual_approach = False

    def __init__(self, app: "EChemTipsApp", title: str, description: str) -> None:
        super().__init__(app, title, description)
        self.setup_toggle = button("Hide setup", self._toggle_setup)
        self.setup_toggle.setProperty("compact", True)
        self.setup_toggle.setCheckable(True)
        self.setup_toggle.setToolTip("Give the plots the full page width; show setup again to edit parameters.")
        self.layout().itemAt(0).layout().addWidget(self.setup_toggle)
        self.expand_plots = button("Expand plots", self._expand_plots)
        self.expand_plots.setProperty("compact", True)
        self.layout().itemAt(0).layout().addWidget(self.expand_plots)
        self._plots_dialog = None

    def _toggle_setup(self) -> None:
        controls = self.body.layout().itemAt(0).widget()
        controls.setVisible(not self.setup_toggle.isChecked())
        self.setup_toggle.setText("Show setup" if self.setup_toggle.isChecked() else "Hide setup")

    def _expand_plots(self) -> None:
        if self._plots_dialog is not None:
            self._plots_dialog.raise_()
            return
        tabs = self.body.findChild(QtWidgets.QTabWidget)
        if tabs is None:
            return
        parent = tabs.parentWidget()
        layout = parent.layout()
        index = layout.indexOf(tabs)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("eChemTips — Experiment plots")
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        content = _vbox(dialog, (12, 12, 12, 12))
        close = button("Return to experiment", dialog.close)
        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(close)
        actions.addStretch(1)
        actions.addWidget(button("Stop experiment", self.stop, "danger"))
        actions.addWidget(button("EMERGENCY STOP", self.app.emergency_stop, "danger"))
        content.addLayout(actions)
        content.addWidget(tabs, 1)
        def restore(_result: int) -> None:
            """Restore the live plot tabs without recreating or clearing data."""
            layout.insertWidget(index, tabs, 1)
            self._plots_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(restore)
        self._plots_dialog = dialog
        dialog.showMaximized()

    @property
    def experiment(self):
        """Return the state machine registered for this page's key."""
        return self.app.experiments[self.experiment_key]

    def build_status(self, title: str, start_text: str) -> StatusCard:
        """Build shared method actions and optional manual-contact control."""
        self.status = StatusCard(title, "", start_text, self.start, self.stop)
        self.state_label = self.status.state_label
        self.detail_label = self.status.detail_label
        self.progress = self.status.progress
        self.start_button = self.status.start_button
        self.stop_button = self.status.stop_button
        self.accept_approach_button: QtWidgets.QPushButton | None = None
        if self.manual_approach:
            self.accept_approach_button = button("Accept contact", self.accept_approach)
            self.accept_approach_button.setToolTip(
                "Stops the current approach waypoint and deliberately starts the method's next step without waiting for the current threshold."
            )
            self.accept_approach_button.setEnabled(False)
            self.status.action_layout.insertWidget(2, self.accept_approach_button)
        return self.status

    def _begin(self, parameters: object) -> None:
        self.app.require_connection()
        if self.app.any_experiment_active:
            raise BackendError("Another experiment is already running.")
        if self.app.recorder.active:
            raise BackendError("Stop the current recording before starting an experiment.")
        watch = self.app.pages.get("Watch current")
        if isinstance(watch, WatchPage):
            watch.set_live_view(False)
        self._elapsed_origin_s: float | None = None
        self.app.recorder.start(self.recording_name, self.app.settings, parameters)
        try:
            self.experiment.start(parameters)
        except Exception:
            self.app.recorder.discard()
            raise
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.state_label.setText("Starting")
        self.detail_label.setText("Preparing the FPGA/simulation sequence.")
        self.progress.setValue(0)
        self.app._sync_action_states()

    def elapsed_from_start(self, sample: Sample) -> float:
        """Return a display timestamp local to the current experiment."""
        origin = getattr(self, "_elapsed_origin_s", None)
        if origin is None:
            origin = sample.elapsed_s
            self._elapsed_origin_s = origin
        return max(0.0, sample.elapsed_s - origin)

    def accept_approach(self) -> None:
        """Forward explicit current-Z contact acceptance to the state machine."""
        try:
            if not self.manual_approach or self.accept_approach_button is None:
                raise BackendError("This method does not contain an approach step.")
            if self.app._sample is None:
                raise BackendError("No current position sample is available yet.")
            self.experiment.accept_approach(self.app._sample)
            self.accept_approach_button.setEnabled(False)
            self.detail_label.setText("Operator accepted the current Z as contact; continuing.")
            self.app.toast("Current Z accepted as contact", "warning")
        except (BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def stop(self) -> None:
        """Request controlled application-level cancellation when active."""
        if self.experiment.active:
            self.app.stop_experiment(self.experiment_key)

    def _show_update(self, update: object | None) -> None:
        self.status.update_status(update)
        params = self.experiment.params
        if update is not None and isinstance(params, (ScanHoppingCVParameters, ScanHoppingITParameters)):
            notice = params.retraction_notice()
            if notice:
                self.status.detail_label.setText((update.detail + notice).replace("I-t", "I–t"))
                self.status.detail_label.setVisible(True)
        if self.accept_approach_button is not None:
            state = update.state if update is not None else self.experiment.state
            self.accept_approach_button.setEnabled(state == ExperimentState.APPROACHING)


class StandaloneCVPage(ManagedExperimentPage):
    """Standalone CV controls with separate time and voltammogram displays."""
    experiment_key = "cv"
    recording_name = "CV"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "CV", "Run cyclic voltammetry independently, with voltammograms separated from complete time-domain data.")
        root = QtWidgets.QHBoxLayout(self.body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        controls = Card("Potential program", "Start → vertex 1 → vertex 2 → start.")
        form = _grid(controls.body)
        self.start_v = add_field(form, Field("Start potential", "-0.2", "V"), 0, 0)
        self.vertex1 = add_field(form, Field("Vertex 1", "0.6", "V"), 0, 1)
        self.vertex2 = add_field(form, Field("Vertex 2", "-0.4", "V"), 1, 0)
        self.rate = add_field(form, Field("Scan rate", "0.25", "V/s"), 1, 1)
        self.cycles = add_field(form, Field("Cycles", "2"), 2, 0)
        self.jump = Check("Jump to start potential", True)
        form.addWidget(self.jump, 2, 1)
        left = QtWidgets.QWidget()
        left_layout = _vbox(left)
        left_layout.addWidget(controls)
        preview, self.program_preview = _program_card(
            "CV profile", "Potential E1 (V)",
            (self.start_v, self.vertex1, self.vertex2, self.start_v),
            ("Start", "Vertex 1", "Vertex 2", "Return"),
        )
        left_layout.addWidget(preview)
        left_layout.addStretch(1)
        root.addWidget(_left_scroll(left, 370))

        right = QtWidgets.QWidget()
        right_layout = _vbox(right)
        right_layout.addWidget(self.build_status("CV status", "Start CV"))
        tabs = QtWidgets.QTabWidget()
        self.cv_plot = Plot("Potential E1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential E1 (V)")
        tabs.addTab(_plot_card("Cyclic voltammogram", self.cv_plot), "CV")
        raw = QtWidgets.QWidget()
        raw_layout = QtWidgets.QHBoxLayout(raw)
        self.voltage_plot = Plot("Potential vs time", "Potential E1 (V)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        raw_layout.addWidget(_plot_card("Potential E1", self.voltage_plot), 1)
        raw_layout.addWidget(_plot_card("Current 1", self.current_plot), 1)
        tabs.addTab(PlotPanel(raw), "Experiment traces")
        right_layout.addWidget(tabs, 1)
        root.addWidget(right, 1)

    def parameters(self) -> CVParameters:
        """Parse the standalone CV fields into a validated parameter model."""
        return CVParameters(self.start_v.float(), self.vertex1.float(), self.vertex2.float(), self.rate.float(), self.cycles.integer(), self.jump.get())

    def start(self) -> None:
        """Clear CV displays, claim recording, and start standalone CV."""
        try:
            params = self.parameters()
            for plot in (self.cv_plot, self.voltage_plot, self.current_plot):
                plot.clear()
            self._begin(params)
            self.app.toast("Standalone CV started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        """Advance standalone CV and append time/CV points from the batch."""
        if not self.experiment.active:
            return
        for sample in samples:
            self.voltage_plot.append(sample.elapsed_s, sample.voltage1_v, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False)
        if samples:
            for plot in (self.voltage_plot, self.current_plot, self.cv_plot):
                plot.request_redraw()
        self._show_update(self.experiment.tick_samples(samples))


class StandaloneApproachPage(ManagedExperimentPage):
    """Standalone approach controls, contact status, traces, and curves."""
    experiment_key = "approach"
    recording_name = "Approach"
    manual_approach = True

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach", "Acquire an approach curve, distinguish confirmed contact from travel limit, and optionally retract.")
        root = QtWidgets.QHBoxLayout(self.body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        left = QtWidgets.QWidget(); left_layout = _vbox(left)
        movement = Card("1 · Z movement", "Define the approach range and optional return movement.")
        form = _grid(movement.body)
        self.start_z = add_field(form, Field("Start Z", "10", "µm"), 0, 0)
        self.end_z = add_field(form, Field("Approach limit Z", "90", "µm"), 0, 1)
        self.approach_rate = add_field(form, Field("Approach speed", "3", "µm/s"), 1, 0)
        self.retract_rate = add_field(form, Field("Retract speed", "10", "µm/s"), 1, 1)
        self.retract = Check("Retract after approach", True); form.addWidget(self.retract, 2, 0, 1, 2)
        left_layout.addWidget(movement)

        contact = Card("2 · Contact detection", "The approach ends automatically when the selected current crosses the threshold.")
        form = _grid(contact.body)
        self.potential = add_field(form, Field("Approach potential E1", "0.1", "V"), 0, 0)
        feedback_box = QtWidgets.QWidget(); feedback_layout = _vbox(feedback_box, spacing=5)
        feedback_layout.addWidget(label("Feedback current", "muted")); self.feedback_channel = Choice(FEEDBACK_CHANNELS, "Current 1"); feedback_layout.addWidget(self.feedback_channel); form.addWidget(feedback_box, 0, 1)
        self.threshold = add_field(form, Field("Contact threshold |i|", "5", "pA"), 1, 0)
        self.greater = label("Contact at either current polarity", "muted", word_wrap=True)
        form.addWidget(self.greater, 1, 1)
        self.settling_time = add_field(form, Field("Settling time after contact", "0.5", "s"), 2, 0)
        form.addWidget(_contact_help(), 3, 0, 1, 2)
        left_layout.addWidget(contact)

        position = Card("3 · Optional XY preposition", "Leave either field empty to keep that axis at its current position.")
        form = _grid(position.body)
        self.x_position = add_field(form, Field("Target X", "", "µm"), 0, 0)
        self.y_position = add_field(form, Field("Target Y", "", "µm"), 0, 1)
        left_layout.addWidget(position)
        preview, self.program_preview = _program_card(
            "Z movement profile", "Z (µm)",
            (self.start_z, self.end_z, self.start_z), ("Start Z", "Approach limit", "Retract"),
        )
        left_layout.addWidget(preview); left_layout.addStretch(1)
        root.addWidget(_left_scroll(left))
        right = QtWidgets.QWidget(); right_layout = _vbox(right)
        right_layout.addWidget(self.build_status("Approach status", "Start approach"))
        tabs = QtWidgets.QTabWidget(); plots = QtWidgets.QWidget(); plots_layout = QtWidgets.QHBoxLayout(plots)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Feedback current (nA)", (COLORS["blue"],), app.settings.display_max_points)
        plots_layout.addWidget(_plot_card("Z position", self.z_plot), 1)
        plots_layout.addWidget(_plot_card("Feedback current", self.current_plot), 1)
        tabs.addTab(PlotPanel(plots), "Experiment traces")
        self.approach_curve = Plot("Current vs Z", "Feedback current (nA)", (COLORS["danger"],), app.settings.display_max_points, "Z position (µm)")
        self.approach_history = TimedXYPlot("Rolling current vs Z", "Feedback current (nA)", COLORS["blue"], app.settings.display_max_points, "Z position (µm)")
        tabs.addTab(_approach_curves_view(self.approach_curve, self.approach_history), "Approach curves")
        right_layout.addWidget(tabs, 1); root.addWidget(right, 1)

    def parameters(self) -> ApproachParameters:
        """Parse approach, contact, retract, and optional XY fields."""
        return ApproachParameters(
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), approach_rate_um_s=self.approach_rate.float(),
            retract_rate_um_s=self.retract_rate.float(), approach_voltage_v=self.potential.float(),
            feedback_channel=self.feedback_channel.get(), feedback_threshold=self.threshold.float() / PA_PER_NA,
            greater_than=True, feedback_mode="magnitude",
            settling_time_s=self.settling_time.float(), retract_after=self.retract.get(),
            x_um=self.x_position.optional_float(), y_um=self.y_position.optional_float(),
        )

    def start(self) -> None:
        """Clear approach displays, claim recording, and start the method."""
        try:
            params = self.parameters(); self.z_plot.clear(); self.current_plot.clear(); self.approach_curve.clear(); self.approach_history.clear(); self._begin(params)
            self.app.toast("Approach started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        """Advance standalone approach and render traces/approach curves."""
        if not self.experiment.active:
            return
        update = None
        for sample in samples:
            stage = self.app.backend.hardware_program_context(sample.line_number)[1] if self.experiment._hardware else ("approach" if self.experiment.state == ExperimentState.APPROACHING else "")
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            current = _feedback_current(sample, self.experiment.params.feedback_channel)
            self.current_plot.append(sample.elapsed_s, current, redraw=False)
            if stage == "approach":
                self.approach_curve.append(sample.z_um, current, redraw=False)
                self.approach_history.append_timed(sample.elapsed_s, sample.z_um, current, redraw=False)
            if not self.experiment._hardware: update = self.experiment.tick_samples([sample])
        if self.experiment._hardware: update = self.experiment.tick_samples(samples)
        if samples:
            self.z_plot.request_redraw(); self.current_plot.request_redraw(); self.approach_curve.request_redraw(); self.approach_history.request_redraw()
        self._show_update(update)


class ApproachCVPage(ManagedExperimentPage):
    """Contact-gated Approach + CV controls and stage-specific plots."""
    experiment_key = "approach_cv"
    recording_name = "Approach + CV"
    manual_approach = True

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach + CV", "Detect contact, run a cyclic voltammogram only after confirmation, and retract safely.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls_host = QtWidgets.QWidget(); controls_layout = _vbox(controls_host)
        approach = Card("1 · Approach", "Z motion stops when the selected signal crosses the threshold.")
        ag = _grid(approach.body)
        self.start_z = add_field(ag, Field("Start Z", "10.0", "µm"), 0, 0)
        self.end_z = add_field(ag, Field("Approach limit Z", "90.0", "µm"), 0, 1)
        self.approach_rate = add_field(ag, Field("Approach speed", "3.0", "µm/s"), 1, 0)
        self.approach_voltage = add_field(ag, Field("Approach potential E1", "0.10", "V"), 1, 1)
        self.feedback_channel = Choice(FEEDBACK_CHANNELS, "Current 1")
        choice_frame = QtWidgets.QWidget(); choice_layout = _vbox(choice_frame, spacing=5)
        choice_layout.addWidget(label("Feedback current", "muted")); choice_layout.addWidget(self.feedback_channel)
        ag.addWidget(choice_frame, 2, 0)
        self.threshold = add_field(ag, Field("Contact threshold |i|", "5", "pA"), 2, 1)
        self.settling_time = add_field(ag, Field("Settling time after contact", "0.5", "s"), 3, 0)
        self.greater_than = label("Contact at either current polarity", "muted", word_wrap=True)
        ag.addWidget(self.greater_than, 3, 1)
        ag.addWidget(_contact_help(), 4, 0, 1, 2)
        controls_layout.addWidget(approach)
        position = Card("2 · Optional XY preposition", "Leave either field empty to keep that axis at its current position.")
        pg = _grid(position.body)
        self.x_position = add_field(pg, Field("Target X", "", "µm"), 0, 0)
        self.y_position = add_field(pg, Field("Target Y", "", "µm"), 0, 1)
        controls_layout.addWidget(position)
        cv = Card("3 · Cyclic voltammetry", "Potential E1 is swept start → vertex 1 → vertex 2 → start.")
        cg = _grid(cv.body)
        self.cv_start = add_field(cg, Field("Start potential", "-0.20", "V"), 0, 0)
        self.vertex1 = add_field(cg, Field("Vertex 1", "0.60", "V"), 0, 1)
        self.vertex2 = add_field(cg, Field("Vertex 2", "-0.40", "V"), 1, 0)
        self.scan_rate = add_field(cg, Field("Scan rate", "0.25", "V/s"), 1, 1)
        self.cycles = add_field(cg, Field("Cycles", "2"), 2, 0)
        self.retract = Check("Retract to start Z after CV", True); cg.addWidget(self.retract, 2, 1)
        controls_layout.addWidget(cv)
        preview, self.program_preview = _program_card(
            "CV profile", "Potential E1 (V)",
            (self.cv_start, self.vertex1, self.vertex2, self.cv_start),
            ("CV start", "Vertex 1", "Vertex 2", "Return"),
        )
        controls_layout.addWidget(preview); controls_layout.addStretch(1)
        root.addWidget(_left_scroll(controls_host))
        right = QtWidgets.QWidget(); right_layout = _vbox(right)
        right_layout.addWidget(self.build_status("Experiment status", "Start approach + CV"))
        tabs = QtWidgets.QTabWidget(); traces = QtWidgets.QWidget(); plots = QtWidgets.QVBoxLayout(traces); plots.setSpacing(10); traces.setMinimumHeight(540)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Feedback current (nA)", (COLORS["blue"],), app.settings.display_max_points)
        self.cv_plot = Plot("Potential E1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential E1 (V)")
        plots.addWidget(_plot_card("Z position", self.z_plot), 1)
        plots.addWidget(_plot_card("Feedback current", self.current_plot), 1)
        tabs.addTab(PlotPanel(traces), "Experiment traces")
        tabs.addTab(_plot_card("Cyclic voltammogram", self.cv_plot), "CV")
        self.approach_curve = Plot("Current vs Z", "Feedback current (nA)", (COLORS["warning"],), app.settings.display_max_points, "Z position (µm)")
        self.approach_history = TimedXYPlot("Rolling current vs Z", "Feedback current (nA)", COLORS["blue"], app.settings.display_max_points, "Z position (µm)")
        tabs.addTab(_approach_curves_view(self.approach_curve, self.approach_history), "Approach curves")
        right_layout.addWidget(tabs, 1); root.addWidget(right, 1)
        self.plot = self.current_plot

    def parameters(self) -> ApproachCVParameters:
        """Parse positioning, contact, settling, CV, and retract controls."""
        return ApproachCVParameters(
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), approach_rate_um_s=self.approach_rate.float(),
            approach_voltage_v=self.approach_voltage.float(), feedback_channel=self.feedback_channel.get(),
            feedback_threshold_na=self.threshold.float() / PA_PER_NA, greater_than=True,
            feedback_mode="magnitude", settling_time_s=self.settling_time.float(), cv_start_v=self.cv_start.float(),
            cv_vertex1_v=self.vertex1.float(), cv_vertex2_v=self.vertex2.float(), cv_scan_rate_v_s=self.scan_rate.float(),
            cycles=self.cycles.integer(), retract_after=self.retract.get(),
            x_um=self.x_position.optional_float(), y_um=self.y_position.optional_float(),
        )

    def start(self) -> None:
        """Clear stage plots, claim recording, and start Approach + CV."""
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.cv_plot, self.approach_curve, self.approach_history): plot.clear()
            self._begin(params); self.app.toast("Approach + CV started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def poll_status(self, sample: Sample) -> None:
        """Poll a hardware sequence when a cycle yields no FIFO samples."""
        self._show_update(self.experiment.tick(sample) if self.experiment.active else None)

    def on_sample(self, sample: Sample) -> None:
        """Forward one sample through the batch-aware Approach + CV handler."""
        self.on_samples([sample])

    def on_samples(self, samples: list[Sample]) -> None:
        """Advance stages and route samples to trace, CV, and approach plots."""
        experiment = self.experiment
        if not samples or not experiment.active: return
        hardware = experiment._hardware_sequence; update = None; cv_changed = False
        for sample in samples:
            state_before = experiment.state
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            current = _feedback_current(sample, experiment.params.feedback_channel)
            self.current_plot.append(sample.elapsed_s, current, redraw=False)
            stage = self.app.backend.hardware_approach_context(sample.line_number) if hardware else ("cv" if state_before == ExperimentState.CV else "")
            if stage == "approach" or (not hardware and state_before == ExperimentState.APPROACHING):
                self.approach_curve.append(sample.z_um, current, redraw=False)
                self.approach_history.append_timed(sample.elapsed_s, sample.z_um, current, redraw=False)
            if stage == "cv" or (not stage and state_before == ExperimentState.CV):
                self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False); cv_changed = True
            if not hardware and experiment.active: update = experiment.tick(sample)
        if hardware and experiment.active: update = experiment.tick(samples[-1])
        self.z_plot.request_redraw(); self.current_plot.request_redraw(); self.approach_curve.request_redraw(); self.approach_history.request_redraw()
        if cv_changed: self.cv_plot.request_redraw()
        self._show_update(update)


class ApproachITPage(ManagedExperimentPage):
    """Contact-gated Approach + I–t controls and stage-specific plots."""
    experiment_key = "approach_it"
    recording_name = "Approach then IT"
    manual_approach = True

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach + I–t", "Detect contact, apply timed potential steps, acquire current versus time, and optionally retract.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        holder = QtWidgets.QWidget(); hl = _vbox(holder)
        movement = Card("1 · Z movement", "Define the approach range and optional return movement.")
        g = _grid(movement.body)
        self.start_z = add_field(g, Field("Start Z", "10", "µm"), 0, 0); self.end_z = add_field(g, Field("Approach limit Z", "90", "µm"), 0, 1)
        self.approach_rate = add_field(g, Field("Approach speed", "3", "µm/s"), 1, 0); self.retract_rate = add_field(g, Field("Retract speed", "10", "µm/s"), 1, 1)
        self.retract = Check("Retract after I–t", True); g.addWidget(self.retract, 2, 0, 1, 2)
        hl.addWidget(movement)
        contact = Card("2 · Contact detection", "The approach ends automatically when the selected current crosses the threshold.")
        g = _grid(contact.body)
        self.approach_v = add_field(g, Field("Approach potential E1", "0.1", "V"), 0, 0)
        feedback_box = QtWidgets.QWidget(); feedback_layout = _vbox(feedback_box, spacing=5); feedback_layout.addWidget(label("Feedback current", "muted")); self.feedback_channel = Choice(FEEDBACK_CHANNELS, "Current 1"); feedback_layout.addWidget(self.feedback_channel); g.addWidget(feedback_box, 0, 1)
        self.threshold = add_field(g, Field("Contact threshold |i|", "5", "pA"), 1, 0)
        self.greater = label("Contact at either current polarity", "muted", word_wrap=True); g.addWidget(self.greater, 1, 1)
        self.settling_time = add_field(g, Field("Settling time after contact", "0.5", "s"), 2, 0)
        g.addWidget(_contact_help(), 3, 0, 1, 2)
        hl.addWidget(contact)
        position = Card("3 · Optional XY preposition", "Leave either field empty to keep that axis at its current position.")
        g = _grid(position.body)
        self.x_position = add_field(g, Field("Target X", "", "µm"), 0, 0); self.y_position = add_field(g, Field("Target Y", "", "µm"), 0, 1)
        hl.addWidget(position)
        electrochemistry = Card("4 · I–t potential program", "Potential E1 follows initial → pulse → return for each cycle.")
        g = _grid(electrochemistry.body)
        self.initial_v = add_field(g, Field("Initial potential", "-0.1", "V"), 0, 0); self.initial_t = add_field(g, Field("Initial duration", "0.25", "s"), 0, 1)
        self.step_v = add_field(g, Field("Pulse potential", "0.4", "V"), 1, 0); self.step_t = add_field(g, Field("Pulse duration", "1.0", "s"), 1, 1)
        self.return_v = add_field(g, Field("Return potential", "-0.1", "V"), 2, 0); self.return_t = add_field(g, Field("Return duration", "0.25", "s"), 2, 1)
        self.cycles = add_field(g, Field("Cycles", "1"), 3, 0)
        hl.addWidget(electrochemistry)
        preview, self.program_preview = _program_card(
            "I–t potential profile", "Potential E1 (V)",
            (self.initial_v, self.step_v, self.return_v), ("Initial", "Pulse", "Return"), stepped=True,
        )
        hl.addWidget(preview); hl.addStretch(1); root.addWidget(_left_scroll(holder))
        right = QtWidgets.QWidget(); rl = _vbox(right); rl.addWidget(self.build_status("Approach + I–t status", "Start approach + I–t"))
        tabs = QtWidgets.QTabWidget(); full = QtWidgets.QWidget(); fl = QtWidgets.QHBoxLayout(full)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Feedback current (nA)", (COLORS["blue"],), app.settings.display_max_points)
        fl.addWidget(_plot_card("Z position", self.z_plot), 1); fl.addWidget(_plot_card("Feedback current", self.current_plot), 1)
        tabs.addTab(PlotPanel(full), "Experiment traces"); it = QtWidgets.QWidget(); il = QtWidgets.QHBoxLayout(it)
        self.voltage_plot = Plot("Potential vs I–t time", "Potential E1 (V)", (COLORS["accent"],), app.settings.display_max_points, "I–t elapsed (s)")
        self.it_plot = Plot("Current vs I–t time", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "I–t elapsed (s)")
        il.addWidget(_plot_card("Potential E1", self.voltage_plot), 1); il.addWidget(_plot_card("I–t response", self.it_plot), 1)
        tabs.addTab(PlotPanel(it), "I–t"); rl.addWidget(tabs, 1); root.addWidget(right, 1); self._it_t0: float | None = None
        self.approach_curve = Plot("Current vs Z", "Feedback current (nA)", (COLORS["warning"],), app.settings.display_max_points, "Z position (µm)")
        self.approach_history = TimedXYPlot("Rolling current vs Z", "Feedback current (nA)", COLORS["blue"], app.settings.display_max_points, "Z position (µm)")
        tabs.addTab(_approach_curves_view(self.approach_curve, self.approach_history), "Approach curves")

    def parameters(self) -> ApproachITParameters:
        """Parse positioning, contact, settling, I–t, and retract controls."""
        return ApproachITParameters(
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_channel=self.feedback_channel.get(), feedback_threshold=self.threshold.float() / PA_PER_NA, greater_than=True,
            feedback_mode="magnitude", settling_time_s=self.settling_time.float(),
            retract_after=self.retract.get(), x_um=self.x_position.optional_float(), y_um=self.y_position.optional_float(), initial_potential_v=self.initial_v.float(),
            initial_hold_s=self.initial_t.float(), step_potential_v=self.step_v.float(), step_hold_s=self.step_t.float(), return_potential_v=self.return_v.float(),
            return_hold_s=self.return_t.float(), cycles=self.cycles.integer(),
        )

    def start(self) -> None:
        """Clear stage plots, claim recording, and start Approach + I–t."""
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot, self.approach_curve, self.approach_history): plot.clear()
            self._it_t0 = None; self._begin(params); self.app.toast("Approach + I–t started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        """Advance stages and route samples to traces, I–t, and approach plots."""
        experiment = self.experiment
        if not experiment.active: return
        update = None
        for sample in samples:
            current = _feedback_current(sample, experiment.params.feedback_channel)
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False); self.current_plot.append(sample.elapsed_s, current, redraw=False)
            state_before = experiment.state
            if experiment._hardware: stage = self.app.backend.hardware_program_context(sample.line_number)[1]
            else:
                was_it = experiment.state == ExperimentState.IT; update = experiment.tick_samples([sample]); stage = "it" if was_it or experiment.state == ExperimentState.IT else ""
            if stage == "approach" or (not experiment._hardware and state_before == ExperimentState.APPROACHING):
                self.approach_curve.append(sample.z_um, current, redraw=False)
                self.approach_history.append_timed(sample.elapsed_s, sample.z_um, current, redraw=False)
            if stage.startswith("it"):
                self._it_t0 = sample.elapsed_s if self._it_t0 is None else self._it_t0; elapsed = sample.elapsed_s - self._it_t0
                self.voltage_plot.append(elapsed, sample.voltage1_v, redraw=False); self.it_plot.append(elapsed, sample.current1_na, redraw=False)
        if experiment._hardware: update = experiment.tick_samples(samples)
        elif not samples: update = experiment.tick_samples([])
        if samples:
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot, self.approach_curve, self.approach_history): plot.request_redraw()
        self._show_update(update)


class ScanHoppingCVPage(ManagedExperimentPage):
    """Hopping-CV grid controls, traces, per-hop CV, and physical maps."""
    experiment_key = "scan_cv"
    recording_name = "Scan Hopping CV"
    manual_approach = True

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Scan hopping + CV", "Approach, acquire a CV, retract, and repeat over the selected XY scan path.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls_host = QtWidgets.QWidget(); hl = _vbox(controls_host)
        area = Card("1 · Scan area and path", "Define grid bounds and whether rows alternate or use a unidirectional flyback.")
        g = _grid(area.body)
        self.x_start = add_field(g, Field("X start", "35", "µm"), 0, 0); self.x_end = add_field(g, Field("X end", "65", "µm"), 0, 1)
        self.y_start = add_field(g, Field("Y start", "35", "µm"), 1, 0); self.y_end = add_field(g, Field("Y end", "65", "µm"), 1, 1)
        self.x_points = add_field(g, Field("X points", "3"), 2, 0); self.y_points = add_field(g, Field("Y points", "3"), 2, 1)
        pattern_box = QtWidgets.QWidget(); pattern_layout = _vbox(pattern_box, spacing=5); pattern_layout.addWidget(label("Scan pattern", "muted")); self.scan_pattern = Choice(("Serpentine", "Raster"), "Serpentine"); pattern_layout.addWidget(self.scan_pattern); g.addWidget(pattern_box, 3, 0)
        self.line_retract = add_field(g, Field("Raster flyback extra retract", "5", "µm"), 3, 1)
        hl.addWidget(area)
        movement = Card("2 · Motion and contact", "Initial Z is used once; later hops retract by the configured distance from measured contact.")
        g = _grid(movement.body)
        self.start_z = add_field(g, Field("Initial approach Z", "55", "µm"), 0, 0); self.end_z = add_field(g, Field("Approach limit Z", "80", "µm"), 0, 1)
        self.lateral_rate = add_field(g, Field("XY speed", "50", "µm/s"), 1, 0); self.approach_rate = add_field(g, Field("Approach speed", "15", "µm/s"), 1, 1)
        self.retract_rate = add_field(g, Field("Retract speed", "50", "µm/s"), 2, 0); self.approach_v = add_field(g, Field("Approach potential E1", "0.1", "V"), 2, 1)
        self.retract_distance = add_field(g, Field("Retract distance from contact", "10", "µm"), 3, 0)
        self.threshold = add_field(g, Field("Contact threshold |i|", "5", "pA"), 3, 1)
        feedback_box = QtWidgets.QWidget(); feedback_layout = _vbox(feedback_box, spacing=5); feedback_layout.addWidget(label("Feedback current", "muted")); self.feedback_channel = Choice(FEEDBACK_CHANNELS, "Current 1"); feedback_layout.addWidget(self.feedback_channel); g.addWidget(feedback_box, 4, 0, 1, 2)
        self.settling_time = add_field(g, Field("Settling time after contact", "0.5", "s"), 5, 0)
        self.greater = label("Contact at either current polarity", "muted", word_wrap=True); g.addWidget(self.greater, 5, 1)
        g.addWidget(_contact_help(), 6, 0, 1, 2)
        hl.addWidget(movement)
        electrochemistry = Card("3 · Cyclic voltammetry", "Select the per-hop potential E1 waveform and current-map sampling potential.")
        g = _grid(electrochemistry.body)
        self.map_v = add_field(g, Field("Current-map potential E1", "0.2", "V"), 0, 0); self.cv_start = add_field(g, Field("Start potential", "-0.2", "V"), 0, 1)
        self.vertex1 = add_field(g, Field("Vertex 1", "0.6", "V"), 1, 0); self.vertex2 = add_field(g, Field("Vertex 2", "-0.4", "V"), 1, 1)
        self.scan_rate = add_field(g, Field("Scan rate", "2", "V/s"), 2, 0); self.cycles = add_field(g, Field("Cycles", "1"), 2, 1)
        hl.addWidget(electrochemistry)
        self.scan_pattern.currentTextChanged.connect(self._sync_scan_pattern); self._sync_scan_pattern()
        summary, self.spacing_label, self.duration_label = _scan_summary_card(
            self.parameters, [*controls_host.findChildren(QtWidgets.QLineEdit), self.scan_pattern]
        )
        hl.addWidget(summary)
        preview, self.program_preview = _program_card(
            "CV at each hop", "Potential E1 (V)",
            (self.cv_start, self.vertex1, self.vertex2, self.cv_start),
            ("CV start", "Vertex 1", "Vertex 2", "Return"),
        )
        hl.addWidget(preview); hl.addStretch(1); root.addWidget(_left_scroll(controls_host, 410))
        right = QtWidgets.QWidget(); rl = _vbox(right); rl.addWidget(self.build_status("Scan status", "Start scan"))
        self.visual_tabs = QtWidgets.QTabWidget()
        traces = QtWidgets.QWidget(); tl = QtWidgets.QVBoxLayout(traces); traces.setMinimumHeight(540)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points, rolling_window_s=60); self.current_plot = Plot("Current vs time", "Feedback current (nA)", (COLORS["blue"],), app.settings.display_max_points, rolling_window_s=60)
        tl.addWidget(_plot_card("Z position", self.z_plot), 1); tl.addWidget(_plot_card("Feedback current", self.current_plot), 1); self.visual_tabs.addTab(PlotPanel(traces), "Experiment traces")
        cv_page = QtWidgets.QWidget(); cvl = _vbox(cv_page); self.cv_pixel_label = label("Waiting for a CV", "muted")
        self.cv_plot = Plot("Potential E1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential E1 (V)")
        cvl.addWidget(self.cv_pixel_label); cvl.addWidget(_plot_card("Cyclic voltammogram", self.cv_plot), 1); self.visual_tabs.addTab(cv_page, "CV at hop")
        self.approach_curve = Plot("Current vs Z", "Feedback current (nA)", (COLORS["warning"],), app.settings.display_max_points, "Z position (µm)")
        self.approach_history = TimedXYPlot("Rolling current vs Z", "Feedback current (nA)", COLORS["blue"], app.settings.display_max_points, "Z position (µm)")
        self.visual_tabs.addTab(_approach_curves_view(self.approach_curve, self.approach_history), "Approach curves")
        maps = QtWidgets.QWidget(); ml = QtWidgets.QHBoxLayout(maps); ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(10); self.z_map = Heatmap("µm", "Contact Z"); self.current_map = Heatmap("nA", "Current 1")
        ml.addWidget(_plot_card("Contact Z map", self.z_map), 1); ml.addWidget(_plot_card("Current at selected potential", self.current_map, help_text='Uses Current 1 at the selected E1 potential, regardless of the current channel selected for contact detection.'), 1); self.visual_tabs.addTab(PlotPanel(maps), "Maps")
        rl.addWidget(self.visual_tabs, 1); root.addWidget(right, 1); self.approach_plot = self.z_plot; self._cv_point = -1; self._approach_point = -1

    def parameters(self) -> ScanHoppingCVParameters:
        """Parse grid, path, contact, CV, retraction, and map controls."""
        return ScanHoppingCVParameters(
            x_start_um=self.x_start.float(), x_end_um=self.x_end.float(), x_points=self.x_points.integer(), y_start_um=self.y_start.float(), y_end_um=self.y_end.float(), y_points=self.y_points.integer(),
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), lateral_rate_um_s=self.lateral_rate.float(), approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_channel=self.feedback_channel.get(), feedback_threshold_na=self.threshold.float() / PA_PER_NA,
            greater_than=True, feedback_mode="magnitude", settling_time_s=self.settling_time.float(),
            cv_start_v=self.cv_start.float(), cv_vertex1_v=self.vertex1.float(), cv_vertex2_v=self.vertex2.float(),
            cv_scan_rate_v_s=self.scan_rate.float(), cycles=self.cycles.integer(), map_potential_v=self.map_v.float(), serpentine=self.scan_pattern.get() == "Serpentine", raster_line_retract_um=self.line_retract.float(), retract_distance_um=self.retract_distance.float(), footprint_diameter_um=self.app.settings.map_footprint_diameter_um,
        )

    def _refresh_maps(self, *_args: object) -> None:
        params = self.parameters()
        xs = params._axis_values(params.x_start_um, params.x_end_um, params.x_points)
        ys = params._axis_values(params.y_start_um, params.y_end_um, params.y_points)
        mode = self.app.settings.map_view_mode
        self.z_map.set_data(self.experiment.contact_z, params.y_points, params.x_points, x_values=xs, y_values=ys, view_mode=mode, footprint_diameter_um=params.footprint_diameter_um)
        self.current_map.set_data(self.experiment.current_at_potential, params.y_points, params.x_points, x_values=xs, y_values=ys, view_mode=mode, footprint_diameter_um=params.footprint_diameter_um)

    def _sync_scan_pattern(self, *_args: object) -> None:
        self.line_retract.entry.setEnabled(self.scan_pattern.get() == "Raster")

    def start(self) -> None:
        """Reset maps/plots, claim recording, and start hopping CV."""
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.cv_plot, self.approach_curve, self.approach_history): plot.clear()
            self.cv_pixel_label.setText("Waiting for a CV"); self._refresh_maps()
            self._cv_point = -1; self._begin(params); self.app.toast(f"Scan started · {params.point_count} hops", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        """Advance/tag scan data and refresh traces, latest CV, and maps."""
        experiment = self.experiment; hardware = experiment._hardware
        if not experiment.active or not samples: return
        point_before = experiment.point_index
        if hardware:
            update = experiment.tick_samples(samples)
        else:
            # One simulator acquisition drain was measured before this UI
            # update. Tag it at the current pixel and advance the simulated
            # waveform only from its newest measurement.
            for sample in samples: experiment._tag(sample, point_before)
            state_before = experiment.state
            update = experiment.tick_samples([samples[-1]])
        cv_changed = False
        for sample in samples:
            current = _feedback_current(sample, experiment.params.feedback_channel)
            elapsed = self.elapsed_from_start(sample)
            self.z_plot.append(elapsed, sample.z_um, redraw=False); self.current_plot.append(elapsed, current, redraw=False)
            if hardware:
                point_index, stage = self.app.backend.hardware_scan_context(sample.line_number)
            else:
                point_index, stage = point_before, ("cv" if state_before == ExperimentState.CV and sample is samples[-1] else "")
                if state_before == ExperimentState.APPROACHING: stage = "approach"
            if stage == "approach" and point_index >= 0:
                if point_index != self._approach_point:
                    if self.approach_history.x_values:
                        self.approach_history.add_gap(elapsed)
                    self.approach_curve.clear(); self._approach_point = point_index
                self.approach_curve.append(sample.z_um, current, redraw=False)
                self.approach_history.append_timed(elapsed, sample.z_um, current, redraw=False)
            if stage == "cv" and point_index >= 0:
                if point_index != self._cv_point:
                    self.cv_plot.clear(); self._cv_point = point_index; row, column = experiment.params.grid()[point_index][:2]
                    self.cv_pixel_label.setText(f"Hop {point_index + 1} · row {row + 1}, column {column + 1}")
                self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False); cv_changed = True
        if samples: self.z_plot.request_redraw(); self.current_plot.request_redraw(); self.approach_curve.request_redraw(); self.approach_history.request_redraw()
        if cv_changed: self.cv_plot.request_redraw()
        self._refresh_maps()
        self._show_update(update)


class ScanHoppingITPage(ManagedExperimentPage):
    """Hopping-I–t grid controls, traces, per-hop response, and maps."""
    experiment_key = "scan_it"
    recording_name = "Scan Hopping IT"
    manual_approach = True

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Scan hopping + I–t", "Approach, run timed potential steps, retract, and repeat over an XY grid.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls_host = QtWidgets.QWidget(); hl = _vbox(controls_host)
        area = Card("1 · Scan area and path", "Define grid bounds and whether rows alternate or use a unidirectional flyback.")
        g = _grid(area.body)
        self.x_start = add_field(g, Field("X start", "35", "µm"), 0, 0); self.x_end = add_field(g, Field("X end", "65", "µm"), 0, 1)
        self.y_start = add_field(g, Field("Y start", "35", "µm"), 1, 0); self.y_end = add_field(g, Field("Y end", "65", "µm"), 1, 1)
        self.x_points = add_field(g, Field("X points", "3"), 2, 0); self.y_points = add_field(g, Field("Y points", "3"), 2, 1)
        pattern_box = QtWidgets.QWidget(); pattern_layout = _vbox(pattern_box, spacing=5); pattern_layout.addWidget(label("Scan pattern", "muted")); self.scan_pattern = Choice(("Serpentine", "Raster"), "Serpentine"); pattern_layout.addWidget(self.scan_pattern); g.addWidget(pattern_box, 3, 0)
        self.line_retract = add_field(g, Field("Raster flyback extra retract", "5", "µm"), 3, 1)
        hl.addWidget(area)
        movement = Card("2 · Motion and contact", "Initial Z is used once; later hops retract by the configured distance from measured contact.")
        g = _grid(movement.body)
        self.start_z = add_field(g, Field("Initial approach Z", "55", "µm"), 0, 0); self.end_z = add_field(g, Field("Approach limit Z", "80", "µm"), 0, 1)
        self.xy_rate = add_field(g, Field("XY speed", "50", "µm/s"), 1, 0); self.approach_rate = add_field(g, Field("Approach speed", "15", "µm/s"), 1, 1)
        self.retract_rate = add_field(g, Field("Retract speed", "50", "µm/s"), 2, 0); self.approach_v = add_field(g, Field("Approach potential E1", "0.1", "V"), 2, 1)
        self.retract_distance = add_field(g, Field("Retract distance from contact", "10", "µm"), 3, 0)
        self.threshold = add_field(g, Field("Contact threshold |i|", "5", "pA"), 3, 1)
        feedback_box = QtWidgets.QWidget(); feedback_layout = _vbox(feedback_box, spacing=5); feedback_layout.addWidget(label("Feedback current", "muted")); self.feedback_channel = Choice(FEEDBACK_CHANNELS, "Current 1"); feedback_layout.addWidget(self.feedback_channel); g.addWidget(feedback_box, 4, 0, 1, 2)
        self.settling_time = add_field(g, Field("Settling time after contact", "0.5", "s"), 5, 0)
        self.greater = label("Contact at either current polarity", "muted", word_wrap=True); g.addWidget(self.greater, 5, 1)
        g.addWidget(_contact_help(), 6, 0, 1, 2)
        hl.addWidget(movement)
        electrochemistry = Card("3 · I–t potential program", "Potential E1 follows initial → pulse → return at every hop.")
        g = _grid(electrochemistry.body)
        self.initial_v = add_field(g, Field("Initial potential", "-0.1", "V"), 0, 0); self.initial_t = add_field(g, Field("Initial duration", "0.25", "s"), 0, 1)
        self.step_v = add_field(g, Field("Pulse potential", "0.4", "V"), 1, 0); self.step_t = add_field(g, Field("Pulse duration", "1.0", "s"), 1, 1)
        self.return_v = add_field(g, Field("Return potential", "-0.1", "V"), 2, 0); self.return_t = add_field(g, Field("Return duration", "0.25", "s"), 2, 1)
        self.cycles = add_field(g, Field("Cycles", "1"), 3, 0)
        hl.addWidget(electrochemistry)
        self.scan_pattern.currentTextChanged.connect(self._sync_scan_pattern); self._sync_scan_pattern()
        summary, self.spacing_label, self.duration_label = _scan_summary_card(
            self.parameters, [*controls_host.findChildren(QtWidgets.QLineEdit), self.scan_pattern]
        )
        hl.addWidget(summary)
        preview, self.program_preview = _program_card(
            "I–t profile at each hop", "Potential E1 (V)",
            (self.initial_v, self.step_v, self.return_v), ("Initial", "Pulse", "Return"), stepped=True,
        )
        hl.addWidget(preview); hl.addStretch(1); root.addWidget(_left_scroll(controls_host, 410))
        right = QtWidgets.QWidget(); rl = _vbox(right); rl.addWidget(self.build_status("Scan status", "Start scan")); self.visual_tabs = tabs = QtWidgets.QTabWidget()
        traces = QtWidgets.QWidget(); tl = QtWidgets.QVBoxLayout(traces); traces.setMinimumHeight(540); self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points, rolling_window_s=60); self.current_plot = Plot("Current vs time", "Feedback current (nA)", (COLORS["blue"],), app.settings.display_max_points, rolling_window_s=60)
        tl.addWidget(_plot_card("Z position", self.z_plot), 1); tl.addWidget(_plot_card("Feedback current", self.current_plot), 1); tabs.addTab(PlotPanel(traces), "Experiment traces")
        it = QtWidgets.QWidget(); il = QtWidgets.QHBoxLayout(it); self.voltage_plot = Plot("Potential vs local time", "Potential E1 (V)", (COLORS["accent"],), app.settings.display_max_points, "Hop I–t elapsed (s)"); self.it_plot = Plot("Current vs local time", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Hop I–t elapsed (s)")
        il.addWidget(_plot_card("Potential E1", self.voltage_plot), 1); il.addWidget(_plot_card("Current 1", self.it_plot), 1); tabs.addTab(PlotPanel(it), "I–t at hop")
        self.approach_curve = Plot("Current vs Z", "Feedback current (nA)", (COLORS["warning"],), app.settings.display_max_points, "Z position (µm)")
        self.approach_history = TimedXYPlot("Rolling current vs Z", "Feedback current (nA)", COLORS["blue"], app.settings.display_max_points, "Z position (µm)")
        tabs.addTab(_approach_curves_view(self.approach_curve, self.approach_history), "Approach curves")
        maps = QtWidgets.QWidget(); ml = QtWidgets.QHBoxLayout(maps); ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(10); self.z_map = Heatmap("µm", "Contact Z"); self.current_map = Heatmap("nA", "Pulse current")
        ml.addWidget(_plot_card("Contact Z map", self.z_map), 1); ml.addWidget(_plot_card("Mean pulse current", self.current_map, help_text='Uses the mean of Current 1 during the pulse hold, not the instantaneous current or the average over the whole hop.'), 1); tabs.addTab(PlotPanel(maps), "Maps")
        rl.addWidget(tabs, 1); root.addWidget(right, 1); self._it_point = -1; self._it_t0: float | None = None; self._approach_point = -1

    def parameters(self) -> ScanHoppingITParameters:
        """Parse grid, path, contact, I–t, retraction, and map controls."""
        return ScanHoppingITParameters(
            x_start_um=self.x_start.float(), x_end_um=self.x_end.float(), x_points=self.x_points.integer(), y_start_um=self.y_start.float(), y_end_um=self.y_end.float(), y_points=self.y_points.integer(),
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), lateral_rate_um_s=self.xy_rate.float(), approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_channel=self.feedback_channel.get(), feedback_threshold=self.threshold.float() / PA_PER_NA, greater_than=True,
            feedback_mode="magnitude", settling_time_s=self.settling_time.float(), initial_potential_v=self.initial_v.float(), initial_hold_s=self.initial_t.float(),
            step_potential_v=self.step_v.float(), step_hold_s=self.step_t.float(), return_potential_v=self.return_v.float(), return_hold_s=self.return_t.float(), cycles=self.cycles.integer(), serpentine=self.scan_pattern.get() == "Serpentine", raster_line_retract_um=self.line_retract.float(), retract_distance_um=self.retract_distance.float(), footprint_diameter_um=self.app.settings.map_footprint_diameter_um,
        )

    def _refresh_maps(self, *_args: object) -> None:
        params = self.parameters()
        xs = ScanHoppingCVParameters._axis_values(params.x_start_um, params.x_end_um, params.x_points)
        ys = ScanHoppingCVParameters._axis_values(params.y_start_um, params.y_end_um, params.y_points)
        mode = self.app.settings.map_view_mode
        self.z_map.set_data(self.experiment.contact_z, params.y_points, params.x_points, x_values=xs, y_values=ys, view_mode=mode, footprint_diameter_um=params.footprint_diameter_um)
        self.current_map.set_data(self.experiment.current_at_pulse, params.y_points, params.x_points, x_values=xs, y_values=ys, view_mode=mode, footprint_diameter_um=params.footprint_diameter_um)

    def _sync_scan_pattern(self, *_args: object) -> None:
        self.line_retract.entry.setEnabled(self.scan_pattern.get() == "Raster")

    def start(self) -> None:
        """Reset maps/plots, claim recording, and start hopping I–t."""
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot, self.approach_curve, self.approach_history): plot.clear()
            self._refresh_maps(); self._it_point, self._it_t0 = -1, None
            self._begin(params); self.app.toast("Hopping I–t scan started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        """Advance/tag scan data and refresh traces, latest I–t, and maps."""
        experiment = self.experiment
        if not experiment.active: return
        update = experiment.tick_samples(samples) if experiment._hardware else None
        for sample in samples:
            current = _feedback_current(sample, experiment.params.feedback_channel)
            elapsed = self.elapsed_from_start(sample)
            self.z_plot.append(elapsed, sample.z_um, redraw=False); self.current_plot.append(elapsed, current, redraw=False)
            state_before = experiment.state
            if experiment._hardware: point, stage = self.app.backend.hardware_program_context(sample.line_number)
            else:
                point_before = experiment.point_index; was_it = experiment.state == ExperimentState.IT; update = experiment.tick_samples([sample])
                point, stage = (sample.scan_pixel if sample.scan_pixel >= 0 else point_before), ("it" if was_it or experiment.state == ExperimentState.IT else "approach" if state_before == ExperimentState.APPROACHING else "")
            if stage == "approach" and point >= 0:
                if point != self._approach_point:
                    if self.approach_history.x_values:
                        self.approach_history.add_gap(elapsed)
                    self.approach_curve.clear(); self._approach_point = point
                self.approach_curve.append(sample.z_um, current, redraw=False)
                self.approach_history.append_timed(elapsed, sample.z_um, current, redraw=False)
            if stage.startswith("it") and point >= 0:
                if point != self._it_point: self._it_point, self._it_t0 = point, sample.elapsed_s; self.voltage_plot.clear(); self.it_plot.clear()
                elapsed = sample.elapsed_s - (self._it_t0 if self._it_t0 is not None else sample.elapsed_s)
                self.voltage_plot.append(elapsed, sample.voltage1_v, redraw=False); self.it_plot.append(elapsed, sample.current1_na, redraw=False)
        if samples:
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot, self.approach_curve, self.approach_history): plot.request_redraw()
        self._refresh_maps()
        self._show_update(update)


class MovePiezoPage(BasePage):
    """Bounded X/Y/Z movement with separate command and measured readback."""
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Move piezo", "Command bounded X/Y/Z piezo moves, with commanded and measured positions shown separately.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = Card("Motion command")
        form = _vbox(controls.body)
        form.addWidget(label("Piezo axis", "muted")); self.axis = Choice(("X", "Y", "Z"), "Z"); form.addWidget(self.axis)
        self.target = Field("Target", "50", "µm"); self.speed = Field("Speed", "5", "µm/s"); form.addWidget(self.target); form.addWidget(self.speed)
        action_row = QtWidgets.QWidget(); al = _hbox(action_row); al.addWidget(button("Move piezo", self.move, "primary")); al.addWidget(button("Stop movement", self.stop, "danger")); form.addWidget(action_row); form.addStretch(1)
        controls.setMinimumWidth(300); controls.setMaximumWidth(380); root.addWidget(controls)
        position = Card("Position readback"); pl = _vbox(position.body)
        panes = QtWidgets.QWidget(); pg = QtWidgets.QGridLayout(panes); pg.setContentsMargins(0, 0, 0, 0); pg.setSpacing(10)
        self.position_labels: dict[str, QtWidgets.QLabel] = {}; self.commanded_position_labels: dict[str, QtWidgets.QLabel] = {}
        for column, axis in enumerate(("X", "Y", "Z")):
            pane = Card(axis); pane_layout = _vbox(pane.body)
            pane_layout.addWidget(label("MEASURED", "muted")); measured = label("—", "readout"); pane_layout.addWidget(measured)
            pane_layout.addWidget(label("µm", "muted")); pane_layout.addSpacing(6); pane_layout.addWidget(label("COMMANDED", "muted")); commanded = label("— µm", "muted"); pane_layout.addWidget(commanded)
            self.position_labels[axis] = measured; self.commanded_position_labels[axis] = commanded; pg.addWidget(pane, 0, column)
        pl.addWidget(panes); self.readback_source_label = label(app.backend.position_readback_label, "muted", word_wrap=True); self.status_label = label("No move in progress", "muted", word_wrap=True)
        pl.addWidget(self.readback_source_label); pl.addWidget(self.status_label); pl.addStretch(1); root.addWidget(position, 1)

    def move(self) -> None:
        """Validate and submit the selected X, Y, or Z move."""
        try:
            self.app.require_connection()
            if self.app.any_experiment_active: raise BackendError("Stop the experiment before commanding a manual move.")
            axis, target, speed = self.axis.get(), self.target.float(), self.speed.float()
            self.app.backend.move(axis, target, speed)
            self.status_label.setText(f"Moving {axis} to {target:g} µm at {speed:g} µm/s")
            self.app.toast("Command accepted", "success")
        except (ValueError, BackendError) as exc: self.app.show_error(str(exc))

    def stop(self) -> None:
        """Stop piezo movement and explain hardware reconnection semantics."""
        try:
            self.app.backend.stop_motion()
            message = (
                "Motion stopped safely; reinitialize and reconnect the FPGA before another command"
                if self.app.backend.hardware_approach_cv_required else "Motion stopped"
            )
            self.status_label.setText(message)
        except BackendError as exc: self.app.show_error(str(exc))

    def on_sample(self, sample: Sample) -> None:
        """Refresh measured and commanded position panels."""
        for axis, value in (("X", sample.x_um), ("Y", sample.y_um), ("Z", sample.z_um)): self.position_labels[axis].setText(f"{value:.3f}")
        for axis, value in (("X", sample.commanded_x_um), ("Y", sample.commanded_y_um), ("Z", sample.commanded_z_um)): self.commanded_position_labels[axis].setText(f"{value:.3f} µm" if math.isfinite(value) else "—")
        self.readback_source_label.setText(self.app.backend.position_readback_label)


class SettingsPage(BasePage):
    """Validated persisted connection, calibration, acquisition, and UI options."""
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Settings", "A capability-aware, validated configuration shared by every experiment.")
        connection = Card("Connection"); cl = _vbox(connection.body)
        cl.addWidget(label("Backend", "muted")); self.mode = Choice(("Simulation", "NI FPGA"), app.settings.mode); cl.addWidget(self.mode)
        self.resource = Field("NI resource", app.settings.resource); cl.addWidget(self.resource)
        cl.addWidget(label("FPGA hardware", "muted")); self.transport = Choice(("USB R Series", "PCIe/PXI R Series", "Auto"), app.settings.hardware_transport); cl.addWidget(self.transport)
        cl.addWidget(label("Compiled bitfile", "muted")); bitrow = QtWidgets.QWidget(); br = _hbox(bitrow); self.bitfile = QtWidgets.QLineEdit(app.settings.bitfile); br.addWidget(self.bitfile, 1); br.addWidget(button("Browse…", self.browse_bitfile)); cl.addWidget(bitrow)
        acquisition = Card("Acquisition", help_text="Effective interval includes the FPGA transfer iteration."); ag = _grid(acquisition.body)
        self.sample_time = add_field(ag, Field("Sample time", str(app.settings.sample_time_us), "µs"), 0, 0); self.samples_per_point = add_field(ag, Field("Samples averaged per data point", str(app.settings.samples_per_point)), 0, 1)
        self.ready_timeout = add_field(ag, Field("FPGA ready timeout", str(app.settings.hardware_ready_timeout_s), "s"), 1, 0); self.watchdog_margin = add_field(ag, Field("Command watchdog margin", str(app.settings.hardware_watchdog_margin_s), "s"), 1, 1)
        self.period_label = label("", "statusStrong"); ag.addWidget(self.period_label, 2, 0, 1, 2)
        piezos = Card("Piezo ranges", "Match the calibrated positioner and controller."); pg = _grid(piezos.body, 3)
        self.x_range = add_field(pg, Field("X maximum", str(app.settings.x_range_um), "µm"), 0, 0); self.y_range = add_field(pg, Field("Y maximum", str(app.settings.y_range_um), "µm"), 0, 1); self.z_range = add_field(pg, Field("Z maximum", str(app.settings.z_range_um), "µm"), 0, 2)
        self.x_bipolar = Check("X: −10 to +10 V", app.settings.x_bipolar); self.y_bipolar = Check("Y: −10 to +10 V", app.settings.y_bipolar); self.z_bipolar = Check("Z: −10 to +10 V", app.settings.z_bipolar)
        pg.addWidget(self.x_bipolar, 1, 0); pg.addWidget(self.y_bipolar, 1, 1); pg.addWidget(self.z_bipolar, 1, 2)
        amplifier = Card("Current amplifiers and potential command", "Configure the two current conversions and the AO3-to-E1 command scaling."); amp = _grid(amplifier.body)
        self.sensitivity1 = add_field(amp, Field("Current 1 · AI3", str(app.settings.current1_v_per_na), "V/nA"), 0, 0); self.sensitivity2 = add_field(amp, Field("Current 2 · AI4", str(app.settings.current2_v_per_na), "V/nA"), 0, 1)
        ratio_row = QtWidgets.QWidget(); ratio_layout = _hbox(ratio_row); self.command_ratio = Field("Command voltage ratio · AO3", str(app.settings.command_voltage_ratio), ":1"); ratio_layout.addWidget(self.command_ratio, 1)
        ratio_help_text = (
            "eChemTips multiplies requested E1 by this ratio before writing AO3. "
            "At 1:1, the requested E1 range is ±10 V. At 5:1, a requested 1 V magnitude sends 5 V magnitude on AO3 (sign depends on polarity convention) "
            "and the maximum requested E1 range is ±2 V. Use 5:1 when the external controller's "
            "±2 V potential span is represented by the NI output's ±10 V command span."
        )
        self.command_ratio_help = InfoButton("Command voltage ratio", ratio_help_text, self); ratio_layout.addWidget(self.command_ratio_help, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        amp.addWidget(ratio_row, 1, 0, 1, 2)
        self.command_ratio_summary = label("", "muted", word_wrap=True); amp.addWidget(self.command_ratio_summary, 2, 0, 1, 2)
        polarity_row = QtWidgets.QWidget(); polarity_layout = _vbox(polarity_row)
        polarity_layout.addWidget(label("Electrochemical polarity convention", "muted"))
        self.polarity = Choice(("IUPAC", "Instrument-native"), app.settings.polarity_convention)
        self.polarity.setToolTip("IUPAC reverses E1/E2 and i1/i2 relative to native WEC-SPM wiring: positive current is anodic. This changes hardware commands, not just plot labels. Reverse old potential-program signs to reproduce an old experiment. Verify polarity with your electrode wiring. Apply while idle and reconnect.")
        polarity_layout.addWidget(self.polarity); amp.addWidget(polarity_row, 3, 0, 1, 2)
        polarity_layout.addWidget(label("IUPAC reverses native current and potential signs, including output commands. Check old potential programs before running.", "muted", word_wrap=True))
        saving = Card("Saving", "Choose a permanent folder for full-rate experiment files."); sv = _vbox(saving.body)
        data_row = QtWidgets.QWidget(); data_layout = _hbox(data_row); self.save_directory = Field("Data folder", app.settings.save_directory); data_layout.addWidget(self.save_directory, 1); data_layout.addWidget(button("Browse…", self.browse_data_folder), 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        self.auto_save = Check("Automatically save completed experiments", app.settings.auto_save)
        sv.addWidget(data_row); sv.addWidget(self.auto_save)
        display = Card("Display", help_text="Plot buffers are decimated for responsive viewing; recordings retain every acquired sample.")
        dg = _grid(display.body)
        self.display_max_points = add_field(dg, Field("Non-rolling display buffer", str(app.settings.display_max_points), "points/plot"), 0, 0)
        self.monitor_window = add_field(dg, Field("Monitor rolling window", str(app.settings.monitor_window_s), "s"), 0, 1)
        self.experiment_window = add_field(dg, Field("Experiment rolling window", str(app.settings.experiment_window_s), "s"), 1, 0)
        units_row = QtWidgets.QWidget(); units_layout = _vbox(units_row)
        units_layout.addWidget(label("Current display units", "muted"))
        self.current_units = Choice(("nA", "pA", "Auto"), app.settings.current_display_unit)
        self.current_units.setToolTip("Auto uses pA when the visible current magnitude is below 1 nA. Recording and parameter units are unchanged.")
        units_layout.addWidget(self.current_units); dg.addWidget(units_row, 1, 1)
        self.font_size = add_field(dg, Field("Font size", str(app.settings.font_size_pt), "pt"), 2, 0)
        self.trace_width = add_field(dg, Field("Trace thickness", str(app.settings.trace_width_px), "px"), 2, 1)

        maps = Card("Scan maps"); mg = _grid(maps.body)
        shape_row = QtWidgets.QWidget(); shape_layout = _vbox(shape_row)
        shape_layout.addWidget(label("Scan map shape", "muted"))
        self.map_view = Choice(("Square cells", "Circular footprints"), "Circular footprints" if app.settings.map_view_mode == "circular" else "Square cells")
        shape_layout.addWidget(self.map_view); mg.addWidget(shape_row, 0, 0)
        self.map_footprint = add_field(mg, Field("Meniscus footprint diameter", str(app.settings.map_footprint_diameter_um), "µm"), 0, 1)
        self.map_footprint.setToolTip("Display only: circle diameter, also used as the cell width for single-row/column maps. Does not change hop spacing or control the meniscus.")
        self.map_z_auto = Check("Automatic contact Z limits", app.settings.map_z_auto_limits)
        self.map_current_auto = Check("Automatic current limits", app.settings.map_current_auto_limits)
        for column, (prefix, title, unit, auto, low, high, palette) in enumerate((
            ("map_z", "Contact Z", "µm", self.map_z_auto, app.settings.map_z_min_um, app.settings.map_z_max_um, app.settings.map_z_colormap),
            ("map_current", "Current", "nA", self.map_current_auto, app.settings.map_current_min_na, app.settings.map_current_max_na, app.settings.map_current_colormap),
        )):
            row = QtWidgets.QWidget(); layout = _vbox(row)
            layout.addWidget(label(f"{title} colormap", "muted"))
            selector = Choice(tuple(MAP_COLORMAPS), next(name for name, value in MAP_COLORMAPS.items() if value == palette))
            setattr(self, prefix + "_colormap", selector); layout.addWidget(selector)
            layout.addWidget(auto)
            minimum = Field(f"{title} minimum", str(low), unit)
            maximum = Field(f"{title} maximum", str(high), unit)
            layout.addWidget(minimum); layout.addWidget(maximum)
            setattr(self, prefix + "_min", minimum); setattr(self, prefix + "_max", maximum)
            minimum.setEnabled(not auto.get()); maximum.setEnabled(not auto.get())
            auto.toggled.connect(lambda checked, a=minimum, b=maximum: (a.setEnabled(not checked), b.setEnabled(not checked)))
            mg.addWidget(row, 1, column)
        self.map_current_colormap.setToolTip("For a blue–white–red scale centered on zero, use symmetric fixed current limits, such as −1 and +1 nA.")
        self.map_current_min.setToolTip("Enter limits in nA, regardless of display units. 1 nA = 1000 pA.")
        self.map_current_max.setToolTip(self.map_current_min.toolTip())

        self.settings_path_label = label(f"Loaded automatically at startup from {app.store.path}", "muted", word_wrap=True)
        sv.addWidget(self.settings_path_label)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(QtCore.Qt.TextElideMode.ElideNone)
        self.tabs.setAccessibleName("Settings categories")
        self.tab_scrolls = {}
        for title, card in (("Connection", connection), ("Acquisition", acquisition), ("Piezos", piezos),
                            ("Amplifiers", amplifier), ("Saving", saving), ("Plots", display), ("Maps", maps)):
            content = QtWidgets.QWidget(); layout = _vbox(content, (4, 4, 4, 4))
            layout.addWidget(card); layout.addStretch(1)
            viewport = scroll_area(content)
            self.tab_scrolls[title] = viewport
            self.tabs.addTab(viewport, title)
        actions = QtWidgets.QWidget(); al = _hbox(actions)
        self.save_defaults_button = button("Save as defaults and apply", self.save, "primary")
        self.save_defaults_button.setToolTip("Save and apply settings from every tab.")
        al.addWidget(self.save_defaults_button); al.addStretch(1)
        body_layout = _vbox(self.body)
        body_layout.addWidget(self.tabs, 1); body_layout.addWidget(actions)
        self.sample_time.entry.textChanged.connect(self._refresh_period); self.samples_per_point.entry.textChanged.connect(self._refresh_period); self.command_ratio.entry.textChanged.connect(self._refresh_command_ratio); self.mode.currentTextChanged.connect(self._sync_mode)
        self._refresh_period(); self._refresh_command_ratio(); self._sync_mode()

    def _refresh_period(self, *_args: object) -> None:
        try: self.period_label.setText(f"Effective data interval  {self.sample_time.integer() * (self.samples_per_point.integer() + 1) / 1000:.3f} ms")
        except ValueError: self.period_label.setText("Effective data interval  —")

    def _refresh_command_ratio(self, *_args: object) -> None:
        try:
            ratio = self.command_ratio.float()
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError
            limit = 10.0 / ratio
            self.command_ratio_summary.setText(
                f"{ratio:g}:1 → requested E1 is limited to ±{limit:g} V; 1 V E1 magnitude commands {ratio:g} V magnitude on AO3; sign follows the polarity setting."
            )
        except ValueError:
            self.command_ratio_summary.setText("Enter a positive ratio to calculate the E1 range.")

    def _sync_mode(self, *_args: object) -> None:
        hardware = self.mode.get() == "NI FPGA"
        for field in (self.resource, self.ready_timeout, self.watchdog_margin):
            field.entry.setEnabled(hardware)
        self.transport.setEnabled(hardware)
        self.bitfile.setEnabled(hardware)

    def browse_bitfile(self) -> None:
        """Choose a local `.lvbitx` path without opening the target."""
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose NI FPGA bitfile", str(Path(self.bitfile.text()).expanduser().parent), "LabVIEW FPGA bitfile (*.lvbitx);;All files (*)")
        if chosen: self.bitfile.setText(str(Path(chosen).resolve()))

    def browse_data_folder(self) -> None:
        """Choose the persistent experiment/report destination."""
        current = Path(self.save_directory.variable.get().strip() or ".").expanduser()
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose eChemTips data folder", str(current))
        if chosen:
            self.save_directory.variable.set(Path(chosen).resolve())

    def values(self) -> AppSettings:
        """Parse every visible control into an `AppSettings` instance."""
        data_folder = Path(self.save_directory.variable.get().strip()).expanduser()
        if not data_folder.is_absolute():
            data_folder = data_folder.resolve()
        return AppSettings(
            mode=self.mode.get(), resource=self.resource.variable.get().strip(), bitfile=self.bitfile.text().strip(), hardware_transport=self.transport.get(),
            x_range_um=self.x_range.float(), y_range_um=self.y_range.float(), z_range_um=self.z_range.float(), x_bipolar=self.x_bipolar.get(), y_bipolar=self.y_bipolar.get(), z_bipolar=self.z_bipolar.get(),
            polarity_convention=self.polarity.get(), command_voltage_ratio=self.command_ratio.float(), current1_v_per_na=self.sensitivity1.float(), current2_v_per_na=self.sensitivity2.float(), sample_time_us=self.sample_time.integer(), samples_per_point=self.samples_per_point.integer(),
            hardware_ready_timeout_s=self.ready_timeout.float(), hardware_watchdog_margin_s=self.watchdog_margin.float(), save_directory=str(data_folder), auto_save=self.auto_save.get(),
            display_max_points=self.display_max_points.integer(),
            map_view_mode="circular" if self.map_view.get() == "Circular footprints" else "square",
            map_footprint_diameter_um=self.map_footprint.float(),
            map_z_auto_limits=self.map_z_auto.get(), map_z_min_um=self.map_z_min.float(), map_z_max_um=self.map_z_max.float(),
            map_z_colormap=MAP_COLORMAPS[self.map_z_colormap.get()],
            map_current_colormap=MAP_COLORMAPS[self.map_current_colormap.get()],
            map_current_auto_limits=self.map_current_auto.get(), map_current_min_na=self.map_current_min.float(), map_current_max_na=self.map_current_max.float(),
            monitor_window_s=self.monitor_window.float(), experiment_window_s=self.experiment_window.float(),
            current_display_unit=self.current_units.get(), font_size_pt=self.font_size.float(), trace_width_px=self.trace_width.float(),
        )

    def save(self) -> None:
        """Persist settings; instrument changes require reconnect, display changes do not."""
        try:
            settings = self.values(); errors = settings.validate()
            if errors: raise ValueError("\n".join(errors))
            self.app.apply_settings(settings); self.app.toast("Defaults saved and applied; they will load at next startup", "success")
        except ValueError as exc: self.app.show_error(str(exc))


class EChemTipsApp(QtWidgets.QMainWindow):
    """Top-level owner of backend, acquisition, experiments, recorder, and pages."""
    PAGE_NAMES = tuple(entry.name for entry in EXPERIMENTS)

    def __init__(self) -> None:
        super().__init__(); configure_pyqtgraph(); self.setWindowTitle("eChemTips — Instrument Control"); self.setWindowIcon(application_icon()); self.resize(1440, 900); self.setMinimumSize(1080, 680)
        self.store = SettingsStore(); self.settings = self.store.load(); self.driver_module = os.environ.get("ECHEMTIPS_DRIVER_MODULE") or os.environ.get("WECSPM_DRIVER_MODULE")
        self.favorite_store = FavoriteStore(self.store.path)
        self.favorites = self.favorite_store.load()
        self.library = None
        self.backend: InstrumentBackend = create_backend(self.settings, self.driver_module); self._acquisition: AcquisitionWorker | None = None; self.recorder = DataRecorder(); self._sample: Sample | None = None
        self._make_experiments(); self._build_shell(); self._build_pages(); self.show_page("Watch current"); self._set_connection_ui(False)
        self._apply_display_settings()
        analysis_menu = self.menuBar().addMenu("Analysis")
        open_analysis = analysis_menu.addAction("Open analysis app…")
        open_analysis.setShortcut(QtGui.QKeySequence("F6"))
        open_analysis.triggered.connect(lambda: self.launch_analysis())
        last_recording = analysis_menu.addAction("Analyze last saved recording")
        last_recording.triggered.connect(lambda: self.launch_analysis(last_recording=True))
        analysis_menu.aboutToShow.connect(lambda: last_recording.setEnabled(
            not self.recorder.active and self.recorder.output_path is not None and self.recorder.output_path.exists()))
        self.poll_timer = QtCore.QTimer(self); self.poll_timer.setInterval(80); self.poll_timer.timeout.connect(self._poll); self.poll_timer.start()

    def launch_analysis(self, *, last_recording: bool = False) -> None:
        """Launch an independent analysis process without touching instrument state."""
        arguments = ["-m", "echemtips.analysis", "--data-folder", str(Path(self.settings.save_directory).expanduser().resolve())]
        if last_recording:
            path = self.recorder.output_path
            if self.recorder.active or path is None or not path.exists():
                self.show_error("Finish the recording before opening its saved data in analysis.")
                return
            arguments.append(str(path.resolve()))
        executable = Path(sys.executable)
        if sys.platform == "win32" and executable.with_name("pythonw.exe").exists():
            executable = executable.with_name("pythonw.exe")
        started, _pid = QtCore.QProcess.startDetached(str(executable), arguments, str(Path(__file__).resolve().parent.parent))
        if not started:
            self.show_error("Could not launch analysis. Run python -m echemtips.analysis in this environment.")

    def _make_experiments(self) -> None:
        self.experiment = ApproachCVExperiment(self.backend, self.settings); self.scan_experiment = ScanHoppingCVExperiment(self.backend, self.settings); self.cv_experiment = CVExperiment(self.backend, self.settings)
        self.approach_experiment = ApproachExperiment(self.backend, self.settings); self.approach_it_experiment = ApproachITExperiment(self.backend, self.settings); self.scan_it_experiment = ScanHoppingITExperiment(self.backend, self.settings)
        self.experiments = {"approach_cv": self.experiment, "scan_cv": self.scan_experiment, "cv": self.cv_experiment, "approach": self.approach_experiment, "approach_it": self.approach_it_experiment, "scan_it": self.scan_it_experiment}

    def _build_shell(self) -> None:
        root = QtWidgets.QWidget(); root.setObjectName("window"); self.setCentralWidget(root); layout = QtWidgets.QHBoxLayout(root); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        sidebar = QtWidgets.QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(225); side = _vbox(sidebar, (14, 20, 14, 18), 5)
        brand_row = QtWidgets.QWidget(); br = _hbox(brand_row); mark = logo_label()
        brand = label("eChemTips", "brand"); br.addWidget(mark); br.addWidget(brand); br.addStretch(1); side.addWidget(brand_row); side.addWidget(label("SCANNING ELECTROCHEMISTRY", "sidebarMuted")); side.addSpacing(18)
        self.nav_buttons: dict[str, QtWidgets.QPushButton] = {}
        self.nav_group = QtWidgets.QButtonGroup(self); self.nav_group.setExclusive(True)
        for name in self.PAGE_NAMES:
            nav = button(name.replace('I-t', 'I–t'), lambda checked=False, page=name: self.show_page(page))
            nav.setParent(sidebar); nav.setProperty("role", "nav"); nav.setCheckable(True)
            self.nav_group.addButton(nav); self.nav_buttons[name] = nav
        side.addWidget(self.nav_buttons["Move piezo"])
        favorite_content = QtWidgets.QWidget()
        favorite_content.setObjectName("sidebarFavorites")
        favorite_content.setStyleSheet(f"QWidget#sidebarFavorites {{ background: {COLORS['sidebar']}; }}")
        self.favorite_layout = _vbox(favorite_content, (0, 0, 0, 0), 4)
        self.favorite_scroll = scroll_area(favorite_content)
        self.favorite_scroll.setStyleSheet(f"QScrollArea {{ background: {COLORS['sidebar']}; border: none; }}")
        side.addWidget(self.favorite_scroll, 1)
        self.library_button = button("All experiments…", self.open_library)
        side.addWidget(self.library_button)
        side.addWidget(self.nav_buttons["Settings"])
        self._refresh_favorites()
        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+K"), self)
        shortcut.activated.connect(self.open_library)
        for index in range(1, 10):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda position=index: self._favorite_shortcut(position))
        layout.addWidget(sidebar)
        main = QtWidgets.QWidget(); ml = _vbox(main, spacing=0)
        topbar = QtWidgets.QFrame(); topbar.setObjectName("topbar")
        topbar.setProperty("compactControls", True)
        tl = QtWidgets.QGridLayout(topbar); tl.setContentsMargins(14, 5, 14, 6); tl.setSpacing(4)
        self.connection_dot = label("●")
        self.connection_label = label(f"Disconnected · {self.backend.label}", "muted")
        self.execution_label = label("Offline", "muted")
        self.mode_badge = label(self.settings.mode.upper(), "muted")
        status_row = QtWidgets.QWidget(); status_layout = _hbox(status_row)
        for widget in (self.connection_dot, self.connection_label, self.execution_label, self.mode_badge):
            status_layout.addWidget(widget)
        status_layout.addStretch(1); tl.addWidget(status_row, 0, 0, 1, 5)
        self.pause_button = button("Pause", self.pause_host); self.resume_button = button("Resume", self.resume_host)
        self.next_waypoint_button = button("End waypoint", self.end_current_waypoint)
        self.next_waypoint_button.setToolTip("Low-level FPGA control only; this does not confirm contact. Use the approach page's accept-contact button to continue an approach.")
        self.connect_button = button("Connect", self.toggle_connection, "primary")
        for column, widget in enumerate((self.pause_button, self.resume_button, self.next_waypoint_button, self.connect_button)):
            tl.addWidget(widget, 1, column)
        emergency = button("EMERGENCY STOP", self.emergency_stop, "danger")
        tl.addWidget(emergency, 1, 4)
        ml.addWidget(topbar)
        self.stack = QtWidgets.QStackedWidget(); container = QtWidgets.QWidget(); container_layout = _vbox(container, (16, 10, 16, 6)); container_layout.addWidget(self.stack); ml.addWidget(container, 1)
        self.instrument_readout = InstrumentReadoutBar(); readout_container = QtWidgets.QWidget(); readout_layout = _vbox(readout_container, (16, 0, 16, 6)); readout_layout.addWidget(self.instrument_readout); ml.addWidget(readout_container)
        layout.addWidget(main, 1)
        self.statusBar().setSizeGripEnabled(False)
        self.return_button = button("Return to experiment", self._return_to_active)
        self.return_button.setProperty("compact", True)
        self.statusBar().addPermanentWidget(self.return_button)
        self.return_button.hide()
        self.statusBar().messageChanged.connect(self._sync_status_bar)
        self._sync_status_bar()

    def _build_pages(self) -> None:
        self.pages: dict[str, BasePage] = {entry.name: globals()[entry.page_factory](self) for entry in EXPERIMENTS}
        for page in self.pages.values(): self.stack.addWidget(page)

    def show_page(self, name: str) -> None:
        """Select a known page and synchronize its navigation button."""
        if not hasattr(self, "pages") or name not in self.pages: return
        self.stack.setCurrentWidget(self.pages[name]); self.nav_buttons[name].setChecked(True)
        self.library_button.setText("All experiments…" if name in self.favorites or name in ANCHORED else "All experiments… •")
        self._update_active_navigation()

    def open_library(self) -> None:
        """Open the searchable catalog without affecting acquisition or execution."""
        if self.library is None:
            self.library = ExperimentLibrary(self)
        self.library.refresh()
        self.library.show(); self.library.raise_(); self.library.activateWindow()
        self.library.search.setFocus()

    def set_favorites(self, favorites: list[str]) -> None:
        """Persist sidebar order independently from validated instrument settings."""
        values = list(dict.fromkeys(name for name in favorites if name in self.PAGE_NAMES and name not in ANCHORED))
        try:
            self.favorite_store.save(values)
        except OSError as exc:
            self.show_error(f"Could not save navigation preferences: {exc}")
            return
        self.favorites = values
        self._refresh_favorites()

    def _refresh_favorites(self) -> None:
        while self.favorite_layout.count():
            self.favorite_layout.takeAt(0)
        for name, nav in self.nav_buttons.items():
            if name not in ANCHORED:
                nav.hide()
        for name in self.favorites:
            self.favorite_layout.addWidget(self.nav_buttons[name])
            self.nav_buttons[name].show()
        self.favorite_layout.addStretch(1)

    def _favorite_shortcut(self, index: int) -> None:
        names = ["Move piezo", *self.favorites]
        if index <= len(names):
            self.show_page(names[index - 1])

    def _active_page_name(self) -> str | None:
        for entry in EXPERIMENTS:
            experiment = self.experiments.get(entry.experiment_key)
            if experiment is not None and experiment.active:
                return entry.name
            page = self.pages.get(entry.name)
            if getattr(page, "is_busy", False):
                return entry.name
        if self.recorder.active:
            return {"Watch Current": "Watch current", "Watch Position": "Watch position"}.get(self.recorder.name)
        return None

    def _return_to_active(self) -> None:
        name = self._active_page_name()
        if name:
            self.show_page(name)

    def _update_active_navigation(self) -> None:
        name = self._active_page_name()
        self.return_button.setVisible(name is not None and self.stack.currentWidget() is not self.pages[name])
        if name:
            self.return_button.setText(f"Running: {name.replace('I-t', 'I–t')} · Return")
        self._sync_status_bar()

    def _sync_status_bar(self, _message: str = "") -> None:
        """Reserve footer space only for a notification or navigation back to a run."""
        self.statusBar().setVisible(bool(self.statusBar().currentMessage()) or not self.return_button.isHidden())

    def require_connection(self) -> None:
        """Raise a user-facing backend error when offline."""
        if not self.backend.connected: raise BackendError("Connect to the simulator or NI FPGA first.")

    def pause_host(self) -> None:
        """Request acknowledged operator pause and show the outcome."""
        try: self.require_connection(); self.backend.pause(); self.toast("Host execution paused", "warning")
        except (BackendError, RuntimeError, OSError) as exc: self.show_error(str(exc))

    def resume_host(self) -> None:
        """Request resume without overriding FPGA feedback pause."""
        try: self.require_connection(); self.backend.resume(); self.toast("Host execution resumed", "success")
        except (BackendError, RuntimeError, OSError) as exc: self.show_error(str(exc))

    def end_current_waypoint(self) -> None:
        """Request low-level waypoint completion, never contact acceptance."""
        try: self.require_connection(); self.backend.end_current_waypoint(); self.toast("Requested the next FPGA waypoint", "warning")
        except (BackendError, RuntimeError, OSError) as exc: self.show_error(str(exc))

    @property
    def any_experiment_active(self) -> bool:
        """Return whether any method or diagnostic currently owns the device."""
        experiments_active = any(experiment.active for experiment in EChemTipsApp._experiments_for(self).values())
        diagnostics_active = hasattr(self, "pages") and any(
            getattr(self.pages.get(name), "is_busy", False) for name in ("Preflight", "Characterize pipette")
        )
        return experiments_active or diagnostics_active

    @staticmethod
    def _experiments_for(app: object) -> dict[str, object]:
        registry = getattr(app, "experiments", None)
        if isinstance(registry, dict): return registry
        result: dict[str, object] = {}
        if hasattr(app, "experiment"): result["approach_cv"] = getattr(app, "experiment")
        if hasattr(app, "scan_experiment"): result["scan_cv"] = getattr(app, "scan_experiment")
        return result

    @property
    def active_parameters(self) -> object | None:
        """Return parameters associated with the recorder's active method."""
        key = {"CV": "cv", "Approach": "approach", "Approach + CV": "approach_cv", "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv", "Scan Hopping IT": "scan_it"}.get(self.recorder.name)
        return self.experiments[key].params if key is not None else None

    def toggle_connection(self) -> None:
        """Connect with startup authorization or safely disconnect all owners."""
        if self.backend.connected:
            for page_name in ("Preflight", "Characterize pipette"):
                page = self.pages.get(page_name)
                if isinstance(page, DiagnosticWorkflowPage) and page.is_busy: page.stop()
            for key, experiment in self.experiments.items():
                if experiment.active: self.stop_experiment(key)
            self.flush_acquisition()
            if self.recorder.active: self.finish_recording(self.active_parameters, status="aborted")
            self._stop_acquisition(); self.backend.disconnect(); self._set_connection_ui(False); self.toast("Device disconnected", "warning"); return
        try:
            startup_notice = self.backend.startup_notice
            allow_startup_actuation = False
            if startup_notice:
                answer = QtWidgets.QMessageBox.warning(
                    self,
                    "FPGA startup changes physical outputs",
                    startup_notice + "\n\nRun the FPGA and connect now?",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                    QtWidgets.QMessageBox.StandardButton.Cancel,
                )
                if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                    self.toast("FPGA connection cancelled; outputs were not changed", "warning")
                    return
                allow_startup_actuation = True
            self.backend.connect(allow_startup_actuation=allow_startup_actuation)
            self._start_acquisition()
            self._set_connection_ui(True)
            suffix = " · startup outputs verified" if getattr(self.backend, "startup_verified", False) else ""
            self.toast(f"Connected to {self.backend.label}{suffix}", "success")
        except BackendError as exc: self._set_connection_ui(False); self.show_error(str(exc))

    def _set_connection_ui(self, connected: bool) -> None:
        self.connection_dot.setStyleSheet(f"color:{COLORS['success'] if connected else COLORS['muted']};")
        notes: list[str] = []
        if connected and not self.backend.motion_available: notes.append("monitoring only")
        elif connected and self.backend.hardware_approach_cv_required and not self.backend.approach_cv_available: notes.append("manual motion only")
        if connected and not self.backend.full_rate_data_available: notes.append("register readback")
        suffix = f" · {' · '.join(notes)}" if notes else ""; self.connection_label.setText(f"{'Connected' if connected else 'Disconnected'} · {self.backend.label}{suffix}"); self.execution_label.setText("Idle" if connected else "Offline")
        self.connect_button.setText("Disconnect" if connected else "Connect"); caps = self.backend.capabilities; self.pause_button.setEnabled(connected and caps.pause_resume); self.resume_button.setEnabled(connected and caps.pause_resume); self.next_waypoint_button.setEnabled(connected and caps.end_current_waypoint)
        if not connected and hasattr(self, "pages"):
            for page_name in ("Watch current", "Watch position"):
                watch = self.pages.get(page_name)
                if isinstance(watch, (WatchPage, WatchPositionPage)): watch.set_live_view(False)
            self.instrument_readout.clear()
        self._sync_action_states()

    def _sync_action_states(self) -> None:
        if not hasattr(self, "pages"): return
        connected = self.backend.connected
        for page_name, recording_name in (("Watch current", "Watch Current"), ("Watch position", "Watch Position")):
            watch = self.pages[page_name]
            watch_owned = self.recorder.active and self.recorder.name == recording_name
            watch.start_recording_button.setEnabled(connected and not self.any_experiment_active and not self.recorder.active)
            watch.stop_recording_button.setEnabled(watch_owned)
            watch.live_button.setEnabled(connected and not self.any_experiment_active)
        for page_name, key in {"CV": "cv", "Approach": "approach", "Approach + CV": "approach_cv", "Approach + I-t": "approach_it", "Scan hopping + CV": "scan_cv", "Scan hopping + I-t": "scan_it"}.items():
            page = self.pages[page_name]; page.start_button.setEnabled(connected and not self.any_experiment_active and not self.recorder.active); page.stop_button.setEnabled(self.experiments[key].active)
        diagnostic_busy = any(getattr(self.pages.get(name), "is_busy", False) for name in ("Preflight", "Characterize pipette"))
        for name in ("Preflight", "Characterize pipette"):
            page = self.pages[name]
            if isinstance(page, DiagnosticWorkflowPage): page.sync_actions(connected, diagnostic_busy and not page.is_busy)

    def apply_settings(self, settings: AppSettings) -> None:
        """Persist preferences, rebuilding the backend only for non-display changes."""
        if self.any_experiment_active: raise ValueError("Stop the experiment before changing instrument settings.")
        display_keys = {"display_max_points", "map_view_mode", "map_footprint_diameter_um",
                        "map_z_colormap", "map_current_colormap",
                        "map_z_auto_limits", "map_z_min_um", "map_z_max_um", "map_current_auto_limits",
                        "map_current_min_na", "map_current_max_na", "monitor_window_s", "experiment_window_s",
                        "current_display_unit", "font_size_pt", "trace_width_px"}
        changed = {key for key, value in asdict(settings).items() if value != getattr(self.settings, key)}
        if Path(settings.save_directory).expanduser().resolve() == Path(self.settings.save_directory).expanduser().resolve():
            changed.discard("save_directory")
        if changed <= display_keys:
            self.store.save(settings)
            self.settings = settings
            self._apply_display_settings()
            return
        was_connected = self.backend.connected
        if was_connected: self.flush_acquisition()
        if self.recorder.active: self.finish_recording(self.active_parameters)
        if was_connected: self._stop_acquisition(); self.backend.disconnect()
        self.store.save(settings); self.settings = settings; self.backend = create_backend(settings, self.driver_module); self._make_experiments()
        for page_name in ("Preflight", "Characterize pipette"):
            page = self.pages.get(page_name)
            if isinstance(page, DiagnosticWorkflowPage): page._cv_runner = CVExperiment(self.backend, self.settings)
        self._apply_display_settings()
        self.mode_badge.setText(settings.mode.upper()); self._set_connection_ui(False)
        if was_connected: self.toast("Settings applied; reconnect to use the new backend", "warning")

    def _apply_display_settings(self) -> None:
        settings = self.settings
        self.setStyleSheet(application_stylesheet(settings.font_size_pt))
        font = QtGui.QFont(); font.setPointSizeF(settings.font_size_pt)
        sidebar = self.findChild(QtWidgets.QFrame, "sidebar")
        sidebar.setFixedWidth(max(225, max(nav.sizeHint().width() for nav in self.nav_buttons.values()) + 28))
        for area in self.findChildren(QtWidgets.QScrollArea):
            area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded if settings.font_size_pt > 10 else QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        monitors = {plot for key in ("Watch current", "Watch position") for plot in self.pages[key].findChildren(Plot)}
        for plot in self.findChildren(Plot):
            plot.max_points = max(250, settings.display_max_points)
            plot.buffer.max_points = plot.max_points
            if isinstance(plot, TimedXYPlot):
                plot.history_window_s = settings.experiment_window_s
            else:
                if plot.time_based:
                    plot.rolling_window_s = settings.monitor_window_s if plot in monitors else settings.experiment_window_s
                if plot.rolling_window_s is None:
                    plot.buffer.compact()
            plot.set_display_style(settings.current_display_unit, settings.font_size_pt, settings.trace_width_px)
        for diagram in self.findChildren(ProgramDiagram):
            diagram.font_size_pt = settings.font_size_pt
            diagram.setFixedHeight(round(180 * max(1, settings.font_size_pt / 10)))
            pen = QtGui.QPen(diagram.curve.opts["pen"]); pen.setWidthF(settings.trace_width_px); diagram.curve.setPen(pen)
            for axis in ("left", "bottom"):
                diagram.graph.getAxis(axis).setTickFont(font)
            diagram.graph.getAxis("left").setLabel(diagram.graph.getAxis("left").labelText, **{"font-size": f"{settings.font_size_pt:g}pt"})
            for item in diagram.labels:
                item.setFont(font)
            diagram._fit_annotations()
        for heatmap in self.findChildren(Heatmap):
            is_current = heatmap.base_unit == "nA"
            heatmap.colormap_name = settings.map_current_colormap if is_current else settings.map_z_colormap
            heatmap.current_display_unit = settings.current_display_unit
            heatmap.font_size_pt = settings.font_size_pt
            auto = settings.map_current_auto_limits if is_current else settings.map_z_auto_limits
            limits = (settings.map_current_min_na, settings.map_current_max_na) if is_current else (settings.map_z_min_um, settings.map_z_max_um)
            heatmap.fixed_limits = None if auto else limits
            for axis in ("left", "bottom"):
                heatmap.plot_item.getAxis(axis).setTickFont(font)
            heatmap.color_bar.axis.setTickFont(font)
            heatmap.footer.setFixedHeight(max(32, round(settings.font_size_pt * 3.2)))
            heatmap.set_data(heatmap.values, heatmap.rows, heatmap.columns,
                             x_values=heatmap.x_values, y_values=heatmap.y_values,
                             view_mode=settings.map_view_mode,
                             footprint_diameter_um=settings.map_footprint_diameter_um)
        for text in self.findChildren(QtWidgets.QLabel):
            for prefix, window in (("Current history", settings.monitor_window_s), ("Position history", settings.monitor_window_s), ("Approach history", settings.experiment_window_s)):
                if text.text().startswith(prefix + " ·"):
                    text.setText(f"{prefix} · {window:g} s")
            if text.text().startswith("Live view on ·"):
                text.setText(f"Live view on · {settings.monitor_window_s:g} s")
        self.instrument_readout.current_display_unit = settings.current_display_unit
        watch = self.pages["Watch current"]
        for name, readout, plot in (("i1", watch.current_label, watch.current1_plot), ("i2", watch.current2_label, watch.current2_plot)):
            values = plot.series[0]
            scale, unit = current_display_scale(settings.current_display_unit, values[-1:])
            readout.setText(f"{name}  {values[-1] * scale:+.3f} {unit}" if values else f"{name}  — {unit}")
        if self._sample is not None:
            self.instrument_readout.set_sample(self._sample)
        else:
            self.instrument_readout.clear()

    def finish_recording(self, parameters: object = None, status: str = "complete") -> Path | None:
        """Finalize the shared recorder and synchronize action availability."""
        path = self.recorder.finish(self.settings, parameters, status=status); self._sync_action_states(); return path

    def _start_acquisition(self) -> None: self._stop_acquisition(); self._acquisition = AcquisitionWorker(self.backend); self._acquisition.start()
    def _stop_acquisition(self) -> AcquisitionDrain:
        worker = self._acquisition; self._acquisition = None; return worker.stop() if worker is not None else AcquisitionDrain([], None, 0)

    def flush_acquisition(self) -> None:
        """Barrier-drain the worker and surface errors without stopping it."""
        worker = self._acquisition
        if worker is None: return
        drained = worker.pause_and_snapshot()
        try:
            self._consume_acquired(drained.samples, finalize=False)
            if drained.error is not None: raise BackendError(f"Acquisition flush failed: {drained.error}") from drained.error
        finally: worker.resume()

    def stop_experiment(self, which: str) -> None:
        """Stop physical execution first, final-drain, and save an aborted run."""
        experiment = self.experiments[which]
        if not experiment.active: return
        worker = self._acquisition
        try:
            before = AcquisitionDrain([], None, 0)
            if worker is not None:
                before = worker.pause_and_snapshot()
            # Stopping the physical program takes priority over plotting and
            # recording the snapshot: either operation may fail independently.
            hardware = self.backend.hardware_approach_cv_required
            self.backend.stop_motion()
            self._consume_acquired(before.samples, finalize=False)
            if before.error is not None: raise BackendError(f"Acquisition failed before cancellation: {before.error}") from before.error
            if not hardware: experiment.state = ExperimentState.ABORTED; experiment.detail = "Experiment stopped by operator"
            if worker is not None:
                after = worker.pause_and_snapshot(); self._consume_acquired(after.samples, finalize=False)
                if after.error is not None: raise BackendError(f"Final acquisition drain failed: {after.error}") from after.error
            if experiment.active:
                experiment.state = ExperimentState.ABORTED
                experiment.detail = (
                    "Experiment stopped safely; reinitialize and reconnect the FPGA before another command"
                    if hardware else "Experiment stopped by operator"
                )
            self.finish_recording(experiment.params, status="aborted")
            message = (
                "Stop acknowledged; partial recording saved. Reinitialize and reconnect the FPGA before another command"
                if hardware else "Stop acknowledged; final data drained and partial recording saved"
            )
            self.toast(message, "warning")
        except (BackendError, OSError, ValueError, RuntimeError) as exc:
            if experiment.active: experiment.state = ExperimentState.ABORTED
            if self.recorder.active: self.finish_recording(experiment.params, status="error")
            self.show_error(str(exc))
        finally:
            if worker is not None: worker.resume()
            self._sync_action_states()

    def emergency_stop(self) -> None:
        """Assert strongest backend stop before data/UI work, then disconnect."""
        worker = self._acquisition
        before = AcquisitionDrain([], None, 0)
        try:
            if worker is not None:
                before = worker.pause_and_snapshot()
            # Assert the emergency controls before touching data or widgets.
            # A disk or rendering error must never suppress the stop request.
            if self.backend.connected: self.backend.emergency_stop()
            self._consume_acquired(before.samples, finalize=False)
            after = worker.pause_and_snapshot() if worker is not None else AcquisitionDrain([], None, 0); self._consume_acquired(after.samples, finalize=False)
            if before.error or after.error: raise BackendError(f"Emergency-stop acquisition drain failed: {before.error or after.error}")
            for experiment in self.experiments.values():
                if experiment.active: experiment.state = ExperimentState.ABORTED
            self.finish_recording(self.active_parameters, status="aborted"); self._stop_acquisition()
            if self.backend.connected: self.backend.disconnect()
            self._set_connection_ui(False); self.toast("Emergency stop sent", "danger")
        except (BackendError, OSError, ValueError, RuntimeError) as exc: self.show_error(str(exc))

    def _consume_acquired(self, samples: list[Sample], *, finalize: bool = True) -> None:
        for name in ("Scan hopping + CV", "Scan hopping + I-t"):
            page = self.pages.get(name)
            if page is not None: page.on_samples(samples)
        if samples:
            self._sample = samples[-1]
            readout = getattr(self, "instrument_readout", None)
            if readout is not None:
                readout.set_sample(self._sample)
            for name, page in self.pages.items():
                if name in {"Scan hopping + CV", "Scan hopping + I-t"}: continue
                if name in {"Watch current", "Watch position"} and not page.live_enabled: continue
                page.on_samples(samples)
        else:
            for name in ("CV", "Approach", "Approach + I-t"):
                page = self.pages.get(name)
                if page is not None: page.on_samples([])
            if self.experiment.active and self.backend.hardware_approach_cv_required and self._sample is not None: self.pages["Approach + CV"].poll_status(self._sample)
        for sample in samples: self.recorder.append(sample)
        if finalize: self._finalize_experiments(bool(samples))

    def _finalize_experiments(self, had_samples: bool) -> None:
        del had_samples
        sync = getattr(self, "_sync_action_states", None)
        if not self.recorder.active:
            if callable(sync): sync()
            return
        key = {"CV": "cv", "Approach": "approach", "Approach + CV": "approach_cv", "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv", "Scan Hopping IT": "scan_it"}.get(self.recorder.name)
        if key is None:
            if callable(sync): sync()
            return
        experiment = EChemTipsApp._experiments_for(self)[key]
        if experiment.state not in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
            if callable(sync): sync()
            return
        if experiment.state == ExperimentState.COMPLETE:
            settings = getattr(self, "settings", None); should_save = settings is None or settings.auto_save
            if not should_save and isinstance(self, QtWidgets.QWidget): should_save = QtWidgets.QMessageBox.question(self, "Experiment complete", "Save the recorded experiment now?") == QtWidgets.QMessageBox.StandardButton.Yes
            path = self.finish_recording(experiment.params) if should_save else None
            if not should_save: self.recorder.discard()
            self.toast(f"Experiment complete · saved {path.name}" if path else "Experiment complete", "success")
        else:
            path = self.finish_recording(experiment.params, status="aborted"); self.toast(f"Experiment stopped · saved {path.name}" if path else "Experiment stopped", "warning")
        if callable(sync): sync()

    def _poll(self) -> None:
        if self.backend.connected:
            try:
                worker = getattr(self, "_acquisition", None)
                if worker is None: samples, acquisition_error = self.backend.read_samples(), None
                else: drained = worker.drain(); samples, acquisition_error = drained.samples, drained.error
                EChemTipsApp._consume_acquired(self, samples, finalize=False)
                key = {"CV": "cv", "Approach": "approach", "Approach + CV": "approach_cv", "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv", "Scan Hopping IT": "scan_it"}.get(self.recorder.name)
                terminal = bool(self.recorder.active and key is not None and EChemTipsApp._experiments_for(self)[key].state in (ExperimentState.COMPLETE, ExperimentState.ABORTED))
                if terminal and worker is not None:
                    final = worker.pause_and_snapshot(); EChemTipsApp._consume_acquired(self, final.samples, finalize=False); samples += final.samples; acquisition_error = acquisition_error or final.error; worker.resume()
                # A batch may contain valid final samples and an acquisition
                # error. Preserve those samples, but never mark that recording
                # complete before handling the error.
                if acquisition_error is not None: raise BackendError(str(acquisition_error)) from acquisition_error
                EChemTipsApp._finalize_experiments(self, bool(samples)); status_fn = getattr(self.backend, "execution_status", None)
                if callable(status_fn) and hasattr(self, "execution_label"):
                    status = status_fn(); self.execution_label.setText(f"{status.owner or 'host'} · {status.state.value} · {status.executed_waypoints}/{status.total_waypoints}")
            except (BackendError, OSError, ValueError, RuntimeError) as exc:
                worker = getattr(self, "_acquisition", None)
                if worker is not None: worker.stop(); self._acquisition = None
                try: self.backend.stop_motion()
                except (BackendError, OSError, ValueError): pass
                try:
                    if self.recorder.active: self.finish_recording(self.active_parameters, status="error")
                except (BackendError, OSError, ValueError): pass
                for experiment in EChemTipsApp._experiments_for(self).values():
                    if experiment.active: experiment.state = ExperimentState.ABORTED
                self.backend.disconnect(); self._set_connection_ui(False); self.show_error(str(exc))
        # Compatibility for the existing headless ordering tests.
        if isinstance(self, EChemTipsApp): self._update_active_navigation()
        if not isinstance(self, EChemTipsApp) and hasattr(self, "after"): self.after(80, self._poll)

    def toast(self, message: str, level: str = "info") -> None:
        """Show a short color-coded status-bar message."""
        color = {"success": COLORS["success"], "warning": COLORS["warning"], "danger": COLORS["danger"]}.get(level, COLORS["text"]); self.statusBar().setStyleSheet(f"QStatusBar {{ color:{color}; background:{COLORS['panel']}; font-weight:600; }}"); self.statusBar().showMessage(message, 4200)

    def show_error(self, message: str) -> None:
        """Display a modal operator error without changing hardware state."""
        QtWidgets.QMessageBox.critical(self, "eChemTips", message)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Confirm active work, stop hardware, drain data, and close resources."""
        if self.any_experiment_active and QtWidgets.QMessageBox.question(self, "eChemTips", "An experiment is running. Stop it and close?") != QtWidgets.QMessageBox.StandardButton.Yes: event.ignore(); return
        try:
            worker = self._acquisition
            if worker is not None: before = worker.pause_and_snapshot(); self._consume_acquired(before.samples, finalize=False)
            if self.backend.connected: self.backend.emergency_stop()
            if worker is not None: after = worker.pause_and_snapshot(); self._consume_acquired(after.samples, finalize=False)
            if self.recorder.active: self.finish_recording(self.active_parameters, status="aborted")
        finally:
            self._stop_acquisition()
            if self.backend.connected: self.backend.disconnect()
        event.accept()


def create_application(argv: list[str] | None = None) -> QtWidgets.QApplication:
    """Return the process QApplication configured with eChemTips styling."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or [])
    configure_application_identity(app)
    app.setStyle("Fusion"); app.setStyleSheet(application_stylesheet()); return app
