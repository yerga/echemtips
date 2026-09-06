"""PySide6 operator interface for eChemTips.

The experiment, acquisition, recording, and FPGA services intentionally stay
independent of Qt. This module owns only operator interaction and rendering.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .acquisition import AcquisitionDrain, AcquisitionWorker
from .backends import BackendError, InstrumentBackend, create_backend
from .data import DataRecorder
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
    Plot,
    add_field,
    application_stylesheet,
    button,
    configure_pyqtgraph,
    label,
    scroll_area,
)


FEEDBACK_CHANNELS = (
    "Current 1",
    "Current 2",
    "Current 3",
    "Current 4",
    "Lock-in amplitude",
    "Lock-in phase",
)


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


def _plot_card(title: str, subtitle: str, plot: QtWidgets.QWidget) -> Card:
    card = Card(title, subtitle)
    layout = _vbox(card.body)
    layout.addWidget(plot, 1)
    return card


def _left_scroll(widget: QtWidgets.QWidget, width: int = 390) -> QtWidgets.QScrollArea:
    area = scroll_area(widget, minimum_width=330)
    area.setMaximumWidth(max(width + 90, 440))
    return area


class BasePage(QtWidgets.QWidget):
    def __init__(self, app: "EChemTipsApp", title: str, description: str) -> None:
        super().__init__()
        self.app = app
        outer = _vbox(self, spacing=14)
        title_widget = label(title, "pageTitle")
        description_widget = label(description, "pageDescription", word_wrap=True)
        outer.addWidget(title_widget)
        outer.addWidget(description_widget)
        self.body = QtWidgets.QWidget()
        self.body.setObjectName("window")
        outer.addWidget(self.body, 1)

    def on_sample(self, _sample: Sample) -> None:
        pass

    def on_samples(self, samples: list[Sample]) -> None:
        if samples:
            self.on_sample(samples[-1])


class StatusCard(Card):
    def __init__(self, title: str, detail: str, start_text: str, start_slot, stop_slot) -> None:
        super().__init__(title)
        layout = _vbox(self.body, spacing=8)
        self.state_label = label("Ready", "statusStrong")
        self.detail_label = label(detail, "muted", word_wrap=True)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        actions = QtWidgets.QWidget()
        action_layout = _hbox(actions)
        self.start_button = button(start_text, start_slot, "primary")
        self.stop_button = button("Stop", stop_slot, "danger")
        self.stop_button.setEnabled(False)
        action_layout.addWidget(self.start_button)
        action_layout.addWidget(self.stop_button)
        action_layout.addStretch(1)
        layout.addWidget(self.state_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.progress)
        layout.addWidget(actions)

    def update_status(self, update: object | None) -> None:
        if update is None:
            return
        self.state_label.setText(update.state.value)
        self.detail_label.setText(update.detail)
        self.progress.setValue(round(update.progress * 1000))
        terminal = update.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED)
        if terminal:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)


class WatchPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Watch current", "Monitor live current and position, control the potential outputs, and record without blocking the display.")
        layout = QtWidgets.QHBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        controls = Card("Output control", "Potential changes are limited to ±10 V and recorded with the trace.")
        controls.setMinimumWidth(285)
        controls.setMaximumWidth(350)
        form = _vbox(controls.body)
        self.v1 = Field("Voltage 1 · AO3", "0.10", "V")
        self.v2 = Field("Voltage 2 · AO4", "0.00", "V")
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
        form.addWidget(button("Clear graph", lambda: self.plot.clear()))
        self.live_status_label = label("Live view is off. Start it to plot new samples.", "muted")
        self.live_status_label.setWordWrap(True)
        form.addWidget(self.live_status_label)
        form.addSpacing(12)
        form.addWidget(label("LIVE READOUT", "muted"))
        self.current_label = label("— nA", "readout")
        self.position_label = label("Z  — µm", "muted")
        form.addWidget(self.current_label)
        form.addWidget(self.position_label)
        form.addStretch(1)
        control_scroll = scroll_area(controls, minimum_width=285)
        control_scroll.setMaximumWidth(350)
        layout.addWidget(control_scroll)

        self.plot = Plot(
            "Current channels",
            "Current (nA)",
            (COLORS["accent"], COLORS["blue"], COLORS["danger"], COLORS["warning"]),
            app.settings.display_max_points,
            names=("Current 1", "Current 2", "Current 3", "Current 4"),
        )
        layout.addWidget(_plot_card("Current history", "Opt-in live view; recordings remain full-rate while this display is decimated.", self.plot), 1)

    def apply_voltage(self) -> None:
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
        self.stop_recording() if self.app.recorder.active else self.start_recording()

    def toggle_live_view(self) -> None:
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
        enabled = bool(enabled)
        if enabled and not self.live_enabled and clear_on_start:
            self.plot.clear()
            self.current_label.setText("— nA")
            self.position_label.setText("Z  — µm")
        self.live_enabled = enabled
        self.live_button.setText("Stop live view" if enabled else "Start live view")
        self.live_status_label.setText(
            "Plotting samples acquired from now."
            if enabled
            else "Live view is off. Start it to plot new samples."
        )

    def on_sample(self, sample: Sample) -> None:
        self.on_samples([sample])

    def on_samples(self, samples: list[Sample]) -> None:
        if not samples or not self.live_enabled:
            return
        latest = samples[-1]
        self.current_label.setText(f"{latest.current1_na:+.3f} nA")
        self.position_label.setText(f"Z  {latest.z_um:.3f} µm")
        for sample in samples:
            self.plot.append(sample.elapsed_s, sample.current1_na, sample.current2_na, sample.current3_na, sample.current4_na, redraw=False)
        self.plot.redraw()


class ManagedExperimentPage(BasePage):
    experiment_key = ""
    recording_name = ""

    @property
    def experiment(self):
        return self.app.experiments[self.experiment_key]

    def build_status(self, title: str, start_text: str) -> StatusCard:
        self.status = StatusCard(title, "Configure the method, then start.", start_text, self.start, self.stop)
        self.state_label = self.status.state_label
        self.detail_label = self.status.detail_label
        self.progress = self.status.progress
        self.start_button = self.status.start_button
        self.stop_button = self.status.stop_button
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

    def stop(self) -> None:
        if self.experiment.active:
            self.app.stop_experiment(self.experiment_key)

    def _show_update(self, update: object | None) -> None:
        self.status.update_status(update)


class StandaloneCVPage(ManagedExperimentPage):
    experiment_key = "cv"
    recording_name = "CV"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Standalone CV", "Run cyclic voltammetry independently, with voltammograms separated from complete time-domain data.")
        root = QtWidgets.QHBoxLayout(self.body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        controls = Card("Potential program", "Start → vertex 1 → vertex 2 → start, matching the deployed waypoint convention.")
        form = _grid(controls.body)
        self.start_v = add_field(form, Field("Start", "-0.2", "V"), 0, 0)
        self.vertex1 = add_field(form, Field("Vertex 1", "0.6", "V"), 0, 1)
        self.vertex2 = add_field(form, Field("Vertex 2", "-0.4", "V"), 1, 0)
        self.rate = add_field(form, Field("Scan rate", "0.25", "V/s"), 1, 1)
        self.cycles = add_field(form, Field("Cycles", "2"), 2, 0)
        self.jump = Check("Jump to start potential", True)
        form.addWidget(self.jump, 2, 1)
        left = QtWidgets.QWidget()
        left_layout = _vbox(left)
        left_layout.addWidget(controls)
        left_layout.addStretch(1)
        root.addWidget(_left_scroll(left, 370))

        right = QtWidgets.QWidget()
        right_layout = _vbox(right)
        right_layout.addWidget(self.build_status("CV status", "Start CV"))
        tabs = QtWidgets.QTabWidget()
        self.cv_plot = Plot("Potential 1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential 1 (V)")
        tabs.addTab(_plot_card("Cyclic voltammogram", "Only CV data: Current 1 versus measured Potential 1.", self.cv_plot), "Voltammogram")
        raw = QtWidgets.QWidget()
        raw_layout = QtWidgets.QHBoxLayout(raw)
        self.voltage_plot = Plot("Potential vs time", "Potential 1 (V)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        raw_layout.addWidget(_plot_card("Potential", "Complete time-domain potential trace.", self.voltage_plot), 1)
        raw_layout.addWidget(_plot_card("Current", "Complete time-domain Current 1 trace.", self.current_plot), 1)
        tabs.addTab(raw, "Raw traces")
        right_layout.addWidget(tabs, 1)
        root.addWidget(right, 1)

    def parameters(self) -> CVParameters:
        return CVParameters(self.start_v.float(), self.vertex1.float(), self.vertex2.float(), self.rate.float(), self.cycles.integer(), self.jump.get())

    def start(self) -> None:
        try:
            params = self.parameters()
            for plot in (self.cv_plot, self.voltage_plot, self.current_plot):
                plot.clear()
            self._begin(params)
            self.app.toast("Standalone CV started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc:
            self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        if not self.experiment.active:
            return
        for sample in samples:
            self.voltage_plot.append(sample.elapsed_s, sample.voltage1_v, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False)
        if samples:
            for plot in (self.voltage_plot, self.current_plot, self.cv_plot):
                plot.redraw()
        self._show_update(self.experiment.tick_samples(samples))


class StandaloneApproachPage(ManagedExperimentPage):
    experiment_key = "approach"
    recording_name = "Approach"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Standalone approach", "Acquire an approach curve, distinguish confirmed contact from travel limit, and optionally retract.")
        root = QtWidgets.QHBoxLayout(self.body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        controls = Card("Approach program", "FPGA feedback pauses Z motion when Current 1 crosses the configured threshold.")
        form = _grid(controls.body)
        self.start_z = add_field(form, Field("Start Z", "10", "µm"), 0, 0)
        self.end_z = add_field(form, Field("End Z", "90", "µm"), 0, 1)
        self.approach_rate = add_field(form, Field("Approach rate", "3", "µm/s"), 1, 0)
        self.retract_rate = add_field(form, Field("Retract rate", "10", "µm/s"), 1, 1)
        self.potential = add_field(form, Field("Approach potential", "0.1", "V"), 2, 0)
        self.threshold = add_field(form, Field("Current 1 threshold", "2", "nA"), 2, 1)
        self.x_position = add_field(form, Field("Optional X position", "", "µm"), 3, 0)
        self.y_position = add_field(form, Field("Optional Y position", "", "µm"), 3, 1)
        self.greater = Check("Trigger when greater", True)
        self.retract = Check("Retract after approach", True)
        form.addWidget(self.greater, 4, 0)
        form.addWidget(self.retract, 4, 1)
        left = QtWidgets.QWidget(); left_layout = _vbox(left); left_layout.addWidget(controls); left_layout.addStretch(1)
        root.addWidget(_left_scroll(left))
        right = QtWidgets.QWidget(); right_layout = _vbox(right)
        right_layout.addWidget(self.build_status("Approach status", "Start approach"))
        plots = QtWidgets.QWidget(); plots_layout = QtWidgets.QHBoxLayout(plots)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        plots_layout.addWidget(_plot_card("Z approach", "Measured Z for the complete approach and retract.", self.z_plot), 1)
        plots_layout.addWidget(_plot_card("Approach current", "Current 1 during the complete approach.", self.current_plot), 1)
        right_layout.addWidget(plots, 1); root.addWidget(right, 1)

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
        if not self.experiment.active:
            return
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
        if samples:
            self.z_plot.redraw(); self.current_plot.redraw()
        self._show_update(self.experiment.tick_samples(samples))


class ApproachCVPage(ManagedExperimentPage):
    experiment_key = "approach_cv"
    recording_name = "Approach then CV"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach then CV", "Detect contact, run a cyclic voltammogram only after confirmation, and retract safely.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls_host = QtWidgets.QWidget(); controls_layout = _vbox(controls_host)
        approach = Card("1 · Approach", "Z motion stops when the selected signal crosses the threshold.")
        ag = _grid(approach.body)
        self.start_z = add_field(ag, Field("Start Z", "10.0", "µm"), 0, 0)
        self.end_z = add_field(ag, Field("End Z", "90.0", "µm"), 0, 1)
        self.approach_rate = add_field(ag, Field("Approach rate", "3.0", "µm/s"), 1, 0)
        self.approach_voltage = add_field(ag, Field("Approach potential", "0.10", "V"), 1, 1)
        self.feedback_channel = Choice(FEEDBACK_CHANNELS, "Current 1")
        choice_frame = QtWidgets.QWidget(); choice_layout = _vbox(choice_frame, spacing=5)
        choice_layout.addWidget(label("Feedback signal", "muted")); choice_layout.addWidget(self.feedback_channel)
        ag.addWidget(choice_frame, 2, 0)
        self.threshold = add_field(ag, Field("Contact threshold", "2.0", "nA"), 2, 1)
        self.feedback_channel.currentTextChanged.connect(self._update_threshold_unit)
        self.greater_than = Check("Trigger when signal is greater than threshold", True)
        ag.addWidget(self.greater_than, 3, 0, 1, 2)
        controls_layout.addWidget(approach)
        cv = Card("2 · Cyclic voltammetry", "Potential is swept start → vertex 1 → vertex 2 → start.")
        cg = _grid(cv.body)
        self.cv_start = add_field(cg, Field("Start", "-0.20", "V"), 0, 0)
        self.vertex1 = add_field(cg, Field("Vertex 1", "0.60", "V"), 0, 1)
        self.vertex2 = add_field(cg, Field("Vertex 2", "-0.40", "V"), 1, 0)
        self.scan_rate = add_field(cg, Field("Scan rate", "0.25", "V/s"), 1, 1)
        self.cycles = add_field(cg, Field("Cycles", "2"), 2, 0)
        self.retract = Check("Retract to start Z after CV", True); cg.addWidget(self.retract, 2, 1)
        controls_layout.addWidget(cv); controls_layout.addStretch(1)
        root.addWidget(_left_scroll(controls_host))
        right = QtWidgets.QWidget(); right_layout = _vbox(right)
        right_layout.addWidget(self.build_status("Experiment status", "Start approach + CV"))
        plots = QtWidgets.QGridLayout(); plots.setSpacing(10)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        self.cv_plot = Plot("Potential 1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential 1 (V)")
        plots.addWidget(_plot_card("Z position", "Full experiment history.", self.z_plot), 0, 0)
        plots.addWidget(_plot_card("Current 1", "Full experiment history.", self.current_plot), 0, 1)
        plots.addWidget(_plot_card("Cyclic voltammogram", "Only samples acquired during CV waypoints.", self.cv_plot), 1, 0, 1, 2)
        right_layout.addLayout(plots, 1); root.addWidget(right, 1)
        self.plot = self.current_plot

    def _update_threshold_unit(self, *_args: object) -> None:
        self.threshold.set_unit("deg" if self.feedback_channel.get() == "Lock-in phase" else "nA")

    def parameters(self) -> ApproachCVParameters:
        return ApproachCVParameters(
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), approach_rate_um_s=self.approach_rate.float(),
            approach_voltage_v=self.approach_voltage.float(), feedback_channel=self.feedback_channel.get(),
            feedback_threshold_na=self.threshold.float(), greater_than=self.greater_than.get(), cv_start_v=self.cv_start.float(),
            cv_vertex1_v=self.vertex1.float(), cv_vertex2_v=self.vertex2.float(), cv_scan_rate_v_s=self.scan_rate.float(),
            cycles=self.cycles.integer(), retract_after=self.retract.get(),
        )

    def start(self) -> None:
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.cv_plot): plot.clear()
            self._begin(params); self.app.toast("Approach + CV started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def poll_status(self, sample: Sample) -> None:
        self._show_update(self.experiment.tick(sample) if self.experiment.active else None)

    def on_sample(self, sample: Sample) -> None:
        self.on_samples([sample])

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not samples or not experiment.active: return
        hardware = experiment._hardware_sequence; update = None; cv_changed = False
        for sample in samples:
            state_before = experiment.state
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False)
            self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            stage = self.app.backend.hardware_approach_context(sample.line_number) if hardware else ("cv" if state_before == ExperimentState.CV else "")
            if stage == "cv" or (not stage and state_before == ExperimentState.CV):
                self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False); cv_changed = True
            if not hardware and experiment.active: update = experiment.tick(sample)
        if hardware and experiment.active: update = experiment.tick(samples[-1])
        self.z_plot.redraw(); self.current_plot.redraw()
        if cv_changed: self.cv_plot.redraw()
        self._show_update(update)


class ApproachITPage(ManagedExperimentPage):
    experiment_key = "approach_it"
    recording_name = "Approach then IT"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Approach then I–t", "Detect contact, apply timed potential steps, acquire current versus time, and optionally retract.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = Card("Approach and I–t program", "Each potential is jumped, then held for its configured duration.")
        g = _grid(controls.body)
        self.start_z = add_field(g, Field("Start Z", "10", "µm"), 0, 0); self.end_z = add_field(g, Field("End Z", "90", "µm"), 0, 1)
        self.approach_rate = add_field(g, Field("Approach rate", "3", "µm/s"), 1, 0); self.retract_rate = add_field(g, Field("Retract rate", "10", "µm/s"), 1, 1)
        self.approach_v = add_field(g, Field("Approach potential", "0.1", "V"), 2, 0); self.threshold = add_field(g, Field("Current 1 threshold", "2", "nA"), 2, 1)
        self.initial_v = add_field(g, Field("Initial potential", "-0.1", "V"), 3, 0); self.initial_t = add_field(g, Field("Initial hold", "0.25", "s"), 3, 1)
        self.step_v = add_field(g, Field("Pulse potential", "0.4", "V"), 4, 0); self.step_t = add_field(g, Field("Pulse hold", "1.0", "s"), 4, 1)
        self.return_v = add_field(g, Field("Return potential", "-0.1", "V"), 5, 0); self.return_t = add_field(g, Field("Return hold", "0.25", "s"), 5, 1)
        self.x_position = add_field(g, Field("Optional X position", "", "µm"), 6, 0); self.y_position = add_field(g, Field("Optional Y position", "", "µm"), 6, 1)
        self.cycles = add_field(g, Field("Cycles", "1"), 7, 0)
        self.retract = Check("Retract after I–t", True); self.greater = Check("Trigger when greater", True)
        g.addWidget(self.retract, 7, 1); g.addWidget(self.greater, 8, 0, 1, 2)
        holder = QtWidgets.QWidget(); hl = _vbox(holder); hl.addWidget(controls); hl.addStretch(1); root.addWidget(_left_scroll(holder))
        right = QtWidgets.QWidget(); rl = _vbox(right); rl.addWidget(self.build_status("Approach + I–t status", "Start approach + I–t"))
        tabs = QtWidgets.QTabWidget(); full = QtWidgets.QWidget(); fl = QtWidgets.QHBoxLayout(full)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points)
        self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        fl.addWidget(_plot_card("Z", "Complete approach and retract.", self.z_plot), 1); fl.addWidget(_plot_card("Current", "Complete Current 1 trace.", self.current_plot), 1)
        tabs.addTab(full, "Full traces"); it = QtWidgets.QWidget(); il = QtWidgets.QHBoxLayout(it)
        self.voltage_plot = Plot("Potential vs I–t time", "Potential 1 (V)", (COLORS["accent"],), app.settings.display_max_points, "I–t elapsed (s)")
        self.it_plot = Plot("Current vs I–t time", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "I–t elapsed (s)")
        il.addWidget(_plot_card("Potential steps", "Post-contact potential program.", self.voltage_plot), 1); il.addWidget(_plot_card("I–t response", "Current acquired during timed holds.", self.it_plot), 1)
        tabs.addTab(it, "I–t data"); rl.addWidget(tabs, 1); root.addWidget(right, 1); self._it_t0: float | None = None

    def parameters(self) -> ApproachITParameters:
        return ApproachITParameters(
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_channel="Current 1", feedback_threshold=self.threshold.float(), greater_than=self.greater.get(),
            retract_after=self.retract.get(), x_um=self.x_position.optional_float(), y_um=self.y_position.optional_float(), initial_potential_v=self.initial_v.float(),
            initial_hold_s=self.initial_t.float(), step_potential_v=self.step_v.float(), step_hold_s=self.step_t.float(), return_potential_v=self.return_v.float(),
            return_hold_s=self.return_t.float(), cycles=self.cycles.integer(),
        )

    def start(self) -> None:
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot): plot.clear()
            self._it_t0 = None; self._begin(params); self.app.toast("Approach + I–t started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not experiment.active: return
        update = None
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False); self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            if experiment._hardware: stage = self.app.backend.hardware_program_context(sample.line_number)[1]
            else:
                was_it = experiment.state == ExperimentState.IT; update = experiment.tick_samples([sample]); stage = "it" if was_it or experiment.state == ExperimentState.IT else ""
            if stage.startswith("it"):
                self._it_t0 = sample.elapsed_s if self._it_t0 is None else self._it_t0; elapsed = sample.elapsed_s - self._it_t0
                self.voltage_plot.append(elapsed, sample.voltage1_v, redraw=False); self.it_plot.append(elapsed, sample.current1_na, redraw=False)
        if experiment._hardware: update = experiment.tick_samples(samples)
        elif not samples: update = experiment.tick_samples([])
        if samples:
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot): plot.redraw()
        self._show_update(update)


class ScanHoppingCVPage(ManagedExperimentPage):
    experiment_key = "scan_cv"
    recording_name = "Scan Hopping CV"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Scan hopping + CV", "Approach, acquire a CV, retract, and repeat over a serpentine XY grid.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = Card("Experiment plan", "Current-map values are selected from each measured CV at the requested potential.")
        g = _grid(controls.body)
        specs = (
            ("x_start", "X start", "35", "µm"), ("x_end", "X end", "65", "µm"), ("x_points", "X points", "3", ""), ("y_points", "Y points", "3", ""),
            ("y_start", "Y start", "35", "µm"), ("y_end", "Y end", "65", "µm"), ("start_z", "Retracted Z", "55", "µm"), ("end_z", "Approach limit Z", "80", "µm"),
            ("lateral_rate", "XY rate", "50", "µm/s"), ("approach_rate", "Approach rate", "15", "µm/s"), ("retract_rate", "Retract rate", "50", "µm/s"), ("threshold", "Current 1 threshold", "2", "nA"),
            ("approach_v", "Approach potential", "0.1", "V"), ("map_v", "Current-map potential", "0.2", "V"), ("cv_start", "CV start", "-0.2", "V"), ("vertex1", "CV vertex 1", "0.6", "V"),
            ("vertex2", "CV vertex 2", "-0.4", "V"), ("scan_rate", "CV scan rate", "2", "V/s"), ("cycles", "CV cycles", "1", ""),
        )
        for index, (name, caption, value, unit) in enumerate(specs):
            setattr(self, name, add_field(g, Field(caption, value, unit), index // 2, index % 2))
        self.serpentine = Check("Serpentine rows", True); g.addWidget(self.serpentine, 9, 1)
        holder = QtWidgets.QWidget(); hl = _vbox(holder); hl.addWidget(controls); hl.addStretch(1); root.addWidget(_left_scroll(holder, 410))
        right = QtWidgets.QWidget(); rl = _vbox(right); rl.addWidget(self.build_status("Scan status", "Start scan"))
        self.visual_tabs = QtWidgets.QTabWidget()
        traces = QtWidgets.QWidget(); tl = QtWidgets.QHBoxLayout(traces)
        self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points); self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        tl.addWidget(_plot_card("Z position", "Full scan history across every pixel.", self.z_plot), 1); tl.addWidget(_plot_card("Current 1", "Full scan Current 1 history.", self.current_plot), 1); self.visual_tabs.addTab(traces, "Experiment traces")
        cv_page = QtWidgets.QWidget(); cvl = _vbox(cv_page); self.cv_pixel_label = label("Waiting for a CV", "muted")
        self.cv_plot = Plot("Potential 1 vs Current 1", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Potential 1 (V)")
        cvl.addWidget(self.cv_pixel_label); cvl.addWidget(_plot_card("Cyclic voltammogram", "Only CV samples from the latest pixel.", self.cv_plot), 1); self.visual_tabs.addTab(cv_page, "CV at pixel")
        maps = QtWidgets.QWidget(); ml = QtWidgets.QHBoxLayout(maps); self.z_map = Heatmap("µm", "Contact Z"); self.current_map = Heatmap("nA", "Current 1")
        ml.addWidget(_plot_card("Z contact map", "Confirmed feedback crossing height.", self.z_map), 1); ml.addWidget(_plot_card("Current map", "Current 1 at the selected fixed potential.", self.current_map), 1); self.visual_tabs.addTab(maps, "Maps")
        rl.addWidget(self.visual_tabs, 1); root.addWidget(right, 1); self.approach_plot = self.z_plot; self._cv_point = -1

    def parameters(self) -> ScanHoppingCVParameters:
        return ScanHoppingCVParameters(
            x_start_um=self.x_start.float(), x_end_um=self.x_end.float(), x_points=self.x_points.integer(), y_start_um=self.y_start.float(), y_end_um=self.y_end.float(), y_points=self.y_points.integer(),
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), lateral_rate_um_s=self.lateral_rate.float(), approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_threshold_na=self.threshold.float(), cv_start_v=self.cv_start.float(), cv_vertex1_v=self.vertex1.float(), cv_vertex2_v=self.vertex2.float(),
            cv_scan_rate_v_s=self.scan_rate.float(), cycles=self.cycles.integer(), map_potential_v=self.map_v.float(), serpentine=self.serpentine.get(),
        )

    def start(self) -> None:
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.cv_plot): plot.clear()
            self.cv_pixel_label.setText("Waiting for a CV"); self.z_map.set_data({}, params.y_points, params.x_points); self.current_map.set_data({}, params.y_points, params.x_points)
            self._cv_point = -1; self._begin(params); self.app.toast(f"Scan started · {params.point_count} pixels", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
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
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False); self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            if hardware:
                point_index, stage = self.app.backend.hardware_scan_context(sample.line_number)
            else:
                point_index, stage = point_before, ("cv" if state_before == ExperimentState.CV and sample is samples[-1] else "")
            if stage == "cv" and point_index >= 0:
                if point_index != self._cv_point:
                    self.cv_plot.clear(); self._cv_point = point_index; row, column = experiment.params.grid()[point_index][:2]
                    self.cv_pixel_label.setText(f"Pixel {point_index + 1} · row {row + 1}, column {column + 1}")
                self.cv_plot.append(sample.voltage1_v, sample.current1_na, redraw=False); cv_changed = True
        if samples: self.z_plot.redraw(); self.current_plot.redraw()
        if cv_changed: self.cv_plot.redraw()
        params = experiment.params; self.z_map.set_data(experiment.contact_z, params.y_points, params.x_points); self.current_map.set_data(experiment.current_at_potential, params.y_points, params.x_points)
        self._show_update(update)


class ScanHoppingITPage(ManagedExperimentPage):
    experiment_key = "scan_it"
    recording_name = "Scan Hopping IT"

    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Scan hopping + I–t", "Approach, run timed potential steps, retract, and repeat over an XY grid.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = Card("Hopping I–t program", "The current map is the mean Current 1 during the pulse hold.")
        g = _grid(controls.body)
        specs = (
            ("x_start", "X start", "35", "µm"), ("x_end", "X end", "65", "µm"), ("x_points", "X points", "3", ""), ("y_points", "Y points", "3", ""),
            ("y_start", "Y start", "35", "µm"), ("y_end", "Y end", "65", "µm"), ("start_z", "Retracted Z", "55", "µm"), ("end_z", "Approach limit Z", "80", "µm"),
            ("xy_rate", "XY rate", "50", "µm/s"), ("approach_rate", "Approach rate", "15", "µm/s"), ("retract_rate", "Retract rate", "50", "µm/s"), ("threshold", "Current 1 threshold", "2", "nA"),
            ("approach_v", "Approach potential", "0.1", "V"), ("cycles", "I–t cycles", "1", ""), ("initial_v", "Initial potential", "-0.1", "V"), ("initial_t", "Initial hold", "0.25", "s"),
            ("step_v", "Pulse potential", "0.4", "V"), ("step_t", "Pulse hold", "1.0", "s"), ("return_v", "Return potential", "-0.1", "V"), ("return_t", "Return hold", "0.25", "s"),
        )
        for index, (name, caption, value, unit) in enumerate(specs): setattr(self, name, add_field(g, Field(caption, value, unit), index // 2, index % 2))
        self.serpentine = Check("Serpentine rows", True); self.greater = Check("Trigger when greater", True); g.addWidget(self.serpentine, 10, 0); g.addWidget(self.greater, 10, 1)
        holder = QtWidgets.QWidget(); hl = _vbox(holder); hl.addWidget(controls); hl.addStretch(1); root.addWidget(_left_scroll(holder, 410))
        right = QtWidgets.QWidget(); rl = _vbox(right); rl.addWidget(self.build_status("Hopping I–t status", "Start hopping I–t")); tabs = QtWidgets.QTabWidget()
        traces = QtWidgets.QWidget(); tl = QtWidgets.QHBoxLayout(traces); self.z_plot = Plot("Z vs time", "Z (µm)", (COLORS["accent"],), app.settings.display_max_points); self.current_plot = Plot("Current vs time", "Current 1 (nA)", (COLORS["blue"],), app.settings.display_max_points)
        tl.addWidget(_plot_card("Z", "Full scan Z history.", self.z_plot), 1); tl.addWidget(_plot_card("Current", "Full scan Current 1 history.", self.current_plot), 1); tabs.addTab(traces, "Experiment traces")
        it = QtWidgets.QWidget(); il = QtWidgets.QHBoxLayout(it); self.voltage_plot = Plot("Potential vs local time", "Potential 1 (V)", (COLORS["accent"],), app.settings.display_max_points, "Pixel I–t elapsed (s)"); self.it_plot = Plot("Current vs local time", "Current 1 (nA)", (COLORS["danger"],), app.settings.display_max_points, "Pixel I–t elapsed (s)")
        il.addWidget(_plot_card("Potential", "Timed steps at the latest pixel.", self.voltage_plot), 1); il.addWidget(_plot_card("Current", "I–t response at the latest pixel.", self.it_plot), 1); tabs.addTab(it, "I–t at pixel")
        maps = QtWidgets.QWidget(); ml = QtWidgets.QHBoxLayout(maps); self.z_map = Heatmap("µm", "Contact Z"); self.current_map = Heatmap("nA", "Pulse current")
        ml.addWidget(_plot_card("Z contact map", "Confirmed feedback crossing.", self.z_map), 1); ml.addWidget(_plot_card("Pulse-current map", "Mean Current 1 during pulse hold.", self.current_map), 1); tabs.addTab(maps, "Maps")
        rl.addWidget(tabs, 1); root.addWidget(right, 1); self._it_point = -1; self._it_t0: float | None = None

    def parameters(self) -> ScanHoppingITParameters:
        return ScanHoppingITParameters(
            x_start_um=self.x_start.float(), x_end_um=self.x_end.float(), x_points=self.x_points.integer(), y_start_um=self.y_start.float(), y_end_um=self.y_end.float(), y_points=self.y_points.integer(),
            start_z_um=self.start_z.float(), end_z_um=self.end_z.float(), lateral_rate_um_s=self.xy_rate.float(), approach_rate_um_s=self.approach_rate.float(), retract_rate_um_s=self.retract_rate.float(),
            approach_voltage_v=self.approach_v.float(), feedback_threshold=self.threshold.float(), greater_than=self.greater.get(), initial_potential_v=self.initial_v.float(), initial_hold_s=self.initial_t.float(),
            step_potential_v=self.step_v.float(), step_hold_s=self.step_t.float(), return_potential_v=self.return_v.float(), return_hold_s=self.return_t.float(), cycles=self.cycles.integer(), serpentine=self.serpentine.get(),
        )

    def start(self) -> None:
        try:
            params = self.parameters()
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot): plot.clear()
            self.z_map.set_data({}, params.y_points, params.x_points); self.current_map.set_data({}, params.y_points, params.x_points); self._it_point, self._it_t0 = -1, None
            self._begin(params); self.app.toast("Hopping I–t scan started", "success")
        except (ValueError, BackendError, RuntimeError, OSError) as exc: self.app.show_error(str(exc))

    def on_samples(self, samples: list[Sample]) -> None:
        experiment = self.experiment
        if not experiment.active: return
        update = experiment.tick_samples(samples) if experiment._hardware else None
        for sample in samples:
            self.z_plot.append(sample.elapsed_s, sample.z_um, redraw=False); self.current_plot.append(sample.elapsed_s, sample.current1_na, redraw=False)
            if experiment._hardware: point, stage = self.app.backend.hardware_program_context(sample.line_number)
            else:
                point_before = experiment.point_index; was_it = experiment.state == ExperimentState.IT; update = experiment.tick_samples([sample])
                point, stage = (sample.scan_pixel if sample.scan_pixel >= 0 else point_before), ("it" if was_it or experiment.state == ExperimentState.IT else "")
            if stage.startswith("it") and point >= 0:
                if point != self._it_point: self._it_point, self._it_t0 = point, sample.elapsed_s; self.voltage_plot.clear(); self.it_plot.clear()
                elapsed = sample.elapsed_s - (self._it_t0 if self._it_t0 is not None else sample.elapsed_s)
                self.voltage_plot.append(elapsed, sample.voltage1_v, redraw=False); self.it_plot.append(elapsed, sample.current1_na, redraw=False)
        if samples:
            for plot in (self.z_plot, self.current_plot, self.voltage_plot, self.it_plot): plot.redraw()
        params = experiment.params; self.z_map.set_data(experiment.contact_z, params.y_points, params.x_points); self.current_map.set_data(experiment.current_at_pulse, params.y_points, params.x_points)
        self._show_update(update)


class MovePiezoPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Move piezo", "Command bounded X/Y/Z moves or set an analog potential, with commanded and measured positions shown separately.")
        root = QtWidgets.QHBoxLayout(self.body); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        controls = Card("Motion command", "Position bounds come from Settings. Potential commands are applied immediately.")
        form = _vbox(controls.body)
        form.addWidget(label("Axis", "muted")); self.axis = Choice(("X", "Y", "Z", "Voltage 1", "Voltage 2"), "Z"); form.addWidget(self.axis)
        self.target = Field("Target", "50", "µm"); self.speed = Field("Speed", "5", "µm/s"); form.addWidget(self.target); form.addWidget(self.speed)
        action_row = QtWidgets.QWidget(); al = _hbox(action_row); al.addWidget(button("Move / apply", self.move, "primary")); al.addWidget(button("Stop", self.stop, "danger")); form.addWidget(action_row); form.addStretch(1)
        self.axis.currentTextChanged.connect(self._update_units); controls.setMinimumWidth(300); controls.setMaximumWidth(380); root.addWidget(controls)
        position = Card("Position readback", "Measured inputs are never presented as commanded output values."); pl = _vbox(position.body)
        panes = QtWidgets.QWidget(); pg = QtWidgets.QGridLayout(panes); pg.setContentsMargins(0, 0, 0, 0); pg.setSpacing(10)
        self.position_labels: dict[str, QtWidgets.QLabel] = {}; self.commanded_position_labels: dict[str, QtWidgets.QLabel] = {}
        for column, axis in enumerate(("X", "Y", "Z")):
            pane = Card(axis); pane_layout = _vbox(pane.body)
            pane_layout.addWidget(label("MEASURED", "muted")); measured = label("—", "readout"); pane_layout.addWidget(measured)
            pane_layout.addWidget(label("µm", "muted")); pane_layout.addSpacing(6); pane_layout.addWidget(label("COMMANDED", "muted")); commanded = label("— µm", "muted"); pane_layout.addWidget(commanded)
            self.position_labels[axis] = measured; self.commanded_position_labels[axis] = commanded; pg.addWidget(pane, 0, column)
        pl.addWidget(panes); self.readback_source_label = label(app.backend.position_readback_label, "muted", word_wrap=True); self.status_label = label("No move in progress", "muted", word_wrap=True)
        pl.addWidget(self.readback_source_label); pl.addWidget(self.status_label); pl.addStretch(1); root.addWidget(position, 1)

    def _update_units(self, *_args: object) -> None:
        voltage = self.axis.get().startswith("Voltage"); self.target.set_unit("V" if voltage else "µm"); self.speed.set_unit("not used" if voltage else "µm/s"); self.speed.entry.setEnabled(not voltage)

    def move(self) -> None:
        try:
            self.app.require_connection()
            if self.app.any_experiment_active: raise BackendError("Stop the experiment before commanding a manual move.")
            axis, target, speed = self.axis.get(), self.target.float(), self.speed.float() if self.speed.entry.isEnabled() else 1.0
            self.app.backend.move(axis, target, speed)
            self.status_label.setText(f"Set {axis} to {target:g} V" if axis.startswith("Voltage") else f"Moving {axis} to {target:g} µm at {speed:g} µm/s")
            self.app.toast("Command accepted", "success")
        except (ValueError, BackendError) as exc: self.app.show_error(str(exc))

    def stop(self) -> None:
        try: self.app.backend.stop_motion(); self.status_label.setText("Motion stopped")
        except BackendError as exc: self.app.show_error(str(exc))

    def on_sample(self, sample: Sample) -> None:
        for axis, value in (("X", sample.x_um), ("Y", sample.y_um), ("Z", sample.z_um)): self.position_labels[axis].setText(f"{value:.3f}")
        for axis, value in (("X", sample.commanded_x_um), ("Y", sample.commanded_y_um), ("Z", sample.commanded_z_um)): self.commanded_position_labels[axis].setText(f"{value:.3f} µm" if math.isfinite(value) else "—")
        self.readback_source_label.setText(self.app.backend.position_readback_label)


class SettingsPage(BasePage):
    def __init__(self, app: "EChemTipsApp") -> None:
        super().__init__(app, "Settings", "A capability-aware, validated configuration shared by every experiment.")
        content = QtWidgets.QWidget(); columns = QtWidgets.QHBoxLayout(content); columns.setContentsMargins(2, 2, 12, 18); columns.setSpacing(14)
        left = QtWidgets.QWidget(); right = QtWidgets.QWidget(); ll = _vbox(left); rl = _vbox(right); columns.addWidget(left, 1); columns.addWidget(right, 1)
        connection = Card("Connection", "Simulation needs no driver. NI FPGA uses a separately supplied compiled target."); cl = _vbox(connection.body)
        cl.addWidget(label("Backend", "muted")); self.mode = Choice(("Simulation", "NI FPGA"), app.settings.mode); cl.addWidget(self.mode)
        self.resource = Field("NI resource", app.settings.resource); cl.addWidget(self.resource)
        cl.addWidget(label("FPGA hardware", "muted")); self.transport = Choice(("USB R Series", "PCIe/PXI R Series", "Auto"), app.settings.hardware_transport); cl.addWidget(self.transport)
        cl.addWidget(label("Compiled bitfile", "muted")); bitrow = QtWidgets.QWidget(); br = _hbox(bitrow); self.bitfile = QtWidgets.QLineEdit(app.settings.bitfile); br.addWidget(self.bitfile, 1); br.addWidget(button("Browse…", self.browse_bitfile)); cl.addWidget(bitrow); ll.addWidget(connection)
        acquisition = Card("Acquisition", "Effective interval includes the FPGA transfer iteration."); ag = _grid(acquisition.body)
        self.sample_time = add_field(ag, Field("Sample time", str(app.settings.sample_time_us), "µs"), 0, 0); self.samples_per_point = add_field(ag, Field("Samples per point", str(app.settings.samples_per_point)), 0, 1)
        self.ready_timeout = add_field(ag, Field("FPGA ready timeout", str(app.settings.hardware_ready_timeout_s), "s"), 1, 0); self.watchdog_margin = add_field(ag, Field("Command watchdog margin", str(app.settings.hardware_watchdog_margin_s), "s"), 1, 1)
        self.period_label = label("", "statusStrong"); ag.addWidget(self.period_label, 2, 0, 1, 2); ll.addWidget(acquisition)
        ll.addStretch(1)
        piezos = Card("Piezo ranges", "Match the calibrated positioner and controller."); pg = _grid(piezos.body, 3)
        self.x_range = add_field(pg, Field("X maximum", str(app.settings.x_range_um), "µm"), 0, 0); self.y_range = add_field(pg, Field("Y maximum", str(app.settings.y_range_um), "µm"), 0, 1); self.z_range = add_field(pg, Field("Z maximum", str(app.settings.z_range_um), "µm"), 0, 2)
        self.x_bipolar = Check("X: −10 to +10 V", app.settings.x_bipolar); self.y_bipolar = Check("Y: −10 to +10 V", app.settings.y_bipolar); self.z_bipolar = Check("Z: −10 to +10 V", app.settings.z_bipolar)
        pg.addWidget(self.x_bipolar, 1, 0); pg.addWidget(self.y_bipolar, 1, 1); pg.addWidget(self.z_bipolar, 1, 2); rl.addWidget(piezos)
        amplifier = Card("Current amplifiers", "Sensitivity is output volts per nanoamp of measured current."); amp = _grid(amplifier.body)
        self.sensitivity1 = add_field(amp, Field("Current 1 · AI3", str(app.settings.current1_v_per_na), "V/nA"), 0, 0); self.sensitivity2 = add_field(amp, Field("Current 2 · AI4", str(app.settings.current2_v_per_na), "V/nA"), 0, 1)
        self.sensitivity3 = add_field(amp, Field("Current 3 · AI6", str(app.settings.current3_v_per_na), "V/nA"), 1, 0); self.sensitivity4 = add_field(amp, Field("Current 4 · AI1", str(app.settings.current4_v_per_na), "V/nA"), 1, 1)
        self.read_current4 = Check("Read Current 4 on AI1 instead of Y position input", app.settings.read_current4_instead_y); amp.addWidget(self.read_current4, 2, 0, 1, 2); rl.addWidget(amplifier)
        saving = Card("Saving"); sv = _vbox(saving.body); self.save_directory = Field("Data folder", app.settings.save_directory); self.auto_save = Check("Automatically save completed experiments", app.settings.auto_save); self.command_ratio = Field("AO3 command potential ratio", str(app.settings.command_voltage_ratio))
        sv.addWidget(self.save_directory); sv.addWidget(self.auto_save); sv.addWidget(self.command_ratio); rl.addWidget(saving)
        display = Card("Display", "Plot buffers are decimated for responsive viewing; recordings retain every acquired sample."); dv = _vbox(display.body)
        self.display_max_points = Field("Display buffer", str(app.settings.display_max_points), "points/plot"); dv.addWidget(self.display_max_points); rl.addWidget(display); rl.addStretch(1)
        actions = QtWidgets.QWidget(); al = _hbox(actions); al.addWidget(button("Save and apply settings", self.save, "primary")); al.addWidget(label("Changing backend settings disconnects the current device.", "muted", word_wrap=True), 1)
        full = QtWidgets.QWidget(); full_layout = _vbox(full); full_layout.addWidget(content); full_layout.addWidget(actions); self.viewport = scroll_area(full); body_layout = _vbox(self.body); body_layout.addWidget(self.viewport)
        self.sample_time.entry.textChanged.connect(self._refresh_period); self.samples_per_point.entry.textChanged.connect(self._refresh_period); self.mode.currentTextChanged.connect(self._sync_mode)
        self._refresh_period(); self._sync_mode()

    def _refresh_period(self, *_args: object) -> None:
        try: self.period_label.setText(f"Effective data interval  {self.sample_time.integer() * (self.samples_per_point.integer() + 1) / 1000:.3f} ms")
        except ValueError: self.period_label.setText("Effective data interval  —")

    def _sync_mode(self, *_args: object) -> None:
        hardware = self.mode.get() == "NI FPGA"
        for field in (self.resource, self.ready_timeout, self.watchdog_margin):
            field.entry.setEnabled(hardware)
        self.transport.setEnabled(hardware)
        self.bitfile.setEnabled(hardware)

    def browse_bitfile(self) -> None:
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose NI FPGA bitfile", str(Path(self.bitfile.text()).expanduser().parent), "LabVIEW FPGA bitfile (*.lvbitx);;All files (*)")
        if chosen: self.bitfile.setText(str(Path(chosen).resolve()))

    def values(self) -> AppSettings:
        return AppSettings(
            mode=self.mode.get(), resource=self.resource.variable.get().strip(), bitfile=self.bitfile.text().strip(), hardware_transport=self.transport.get(),
            x_range_um=self.x_range.float(), y_range_um=self.y_range.float(), z_range_um=self.z_range.float(), x_bipolar=self.x_bipolar.get(), y_bipolar=self.y_bipolar.get(), z_bipolar=self.z_bipolar.get(),
            command_voltage_ratio=self.command_ratio.float(), current1_v_per_na=self.sensitivity1.float(), current2_v_per_na=self.sensitivity2.float(), current3_v_per_na=self.sensitivity3.float(), current4_v_per_na=self.sensitivity4.float(), read_current4_instead_y=self.read_current4.get(),
            lockin_sensitivity_na=self.app.settings.lockin_sensitivity_na, lockin_expand=self.app.settings.lockin_expand, lockin_offset_pct=self.app.settings.lockin_offset_pct, sample_time_us=self.sample_time.integer(), samples_per_point=self.samples_per_point.integer(),
            hardware_ready_timeout_s=self.ready_timeout.float(), hardware_watchdog_margin_s=self.watchdog_margin.float(), save_directory=self.save_directory.variable.get().strip(), auto_save=self.auto_save.get(),
            display_max_points=self.display_max_points.integer(),
            feedback2_enabled=self.app.settings.feedback2_enabled, feedback2_channel=self.app.settings.feedback2_channel, feedback2_threshold=self.app.settings.feedback2_threshold, feedback2_greater_than=self.app.settings.feedback2_greater_than, feedback_p_gain=self.app.settings.feedback_p_gain,
            feedback_max_z_step_nm=self.app.settings.feedback_max_z_step_nm, feedback_update_interval_us=self.app.settings.feedback_update_interval_us, feedback_running_average_whole=self.app.settings.feedback_running_average_whole, feedback_running_average_minus=self.app.settings.feedback_running_average_minus, feedback_self_reference_on_hold=self.app.settings.feedback_self_reference_on_hold,
            distance_to_bulk_um=self.app.settings.distance_to_bulk_um, distance_to_bulk2_um=self.app.settings.distance_to_bulk2_um, distance_to_bulk3_um=self.app.settings.distance_to_bulk3_um,
        )

    def save(self) -> None:
        try:
            settings = self.values(); errors = settings.validate()
            if errors: raise ValueError("\n".join(errors))
            self.app.apply_settings(settings); self.app.toast("Settings saved and applied", "success")
        except ValueError as exc: self.app.show_error(str(exc))


class EChemTipsApp(QtWidgets.QMainWindow):
    PAGE_NAMES = ("Watch current", "CV", "Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t", "Move piezo", "Settings")

    def __init__(self) -> None:
        super().__init__(); configure_pyqtgraph(); self.setWindowTitle("eChemTips — Scanning Electrochemistry"); self.resize(1440, 900); self.setMinimumSize(1080, 680)
        self.store = SettingsStore(); self.settings = self.store.load(); self.driver_module = os.environ.get("ECHEMTIPS_DRIVER_MODULE") or os.environ.get("WECSPM_DRIVER_MODULE")
        self.backend: InstrumentBackend = create_backend(self.settings, self.driver_module); self._acquisition: AcquisitionWorker | None = None; self.recorder = DataRecorder(); self._sample: Sample | None = None
        self._make_experiments(); self._build_shell(); self._build_pages(); self.show_page("Watch current"); self._set_connection_ui(False)
        self.poll_timer = QtCore.QTimer(self); self.poll_timer.setInterval(80); self.poll_timer.timeout.connect(self._poll); self.poll_timer.start()

    def _make_experiments(self) -> None:
        self.experiment = ApproachCVExperiment(self.backend, self.settings); self.scan_experiment = ScanHoppingCVExperiment(self.backend, self.settings); self.cv_experiment = CVExperiment(self.backend, self.settings)
        self.approach_experiment = ApproachExperiment(self.backend, self.settings); self.approach_it_experiment = ApproachITExperiment(self.backend, self.settings); self.scan_it_experiment = ScanHoppingITExperiment(self.backend, self.settings)
        self.experiments = {"approach_cv": self.experiment, "scan_cv": self.scan_experiment, "cv": self.cv_experiment, "approach": self.approach_experiment, "approach_it": self.approach_it_experiment, "scan_it": self.scan_it_experiment}

    def _build_shell(self) -> None:
        root = QtWidgets.QWidget(); root.setObjectName("window"); self.setCentralWidget(root); layout = QtWidgets.QHBoxLayout(root); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        sidebar = QtWidgets.QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(225); side = _vbox(sidebar, (14, 20, 14, 18), 5)
        brand_row = QtWidgets.QWidget(); br = _hbox(brand_row); mark = label("e", "brand"); mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); mark.setFixedSize(36, 36); mark.setStyleSheet(f"background:{COLORS['accent']}; border-radius:8px; color:white;")
        brand = label("eChemTips", "brand"); br.addWidget(mark); br.addWidget(brand); br.addStretch(1); side.addWidget(brand_row); side.addWidget(label("SCANNING ELECTROCHEMISTRY", "sidebarMuted")); side.addSpacing(18)
        self.nav_buttons: dict[str, QtWidgets.QPushButton] = {}; group = QtWidgets.QButtonGroup(self); group.setExclusive(True)
        glyphs = ("◉", "⌁", "↓", "↧", "↧", "▦", "▦", "⌖", "⚙")
        for index, (name, glyph) in enumerate(zip(self.PAGE_NAMES, glyphs), 1):
            nav = button(f"{glyph}   {name}", lambda checked=False, page=name: self.show_page(page)); nav.setProperty("role", "nav"); nav.setCheckable(True); group.addButton(nav); side.addWidget(nav); self.nav_buttons[name] = nav
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{index}"), self); shortcut.activated.connect(lambda page=name: self.show_page(page))
        side.addStretch(1); side.addWidget(label("FPGA logic preserved", "sidebarMuted")); side.addWidget(label("PySide6 · PyQtGraph", "sidebarMuted")); layout.addWidget(sidebar)
        main = QtWidgets.QWidget(); ml = _vbox(main, spacing=0); topbar = QtWidgets.QFrame(); topbar.setObjectName("topbar"); topbar.setFixedHeight(70); tl = _hbox(topbar, (18, 10, 18, 10), 8)
        self.connection_dot = label("●"); self.connection_label = label(f"Disconnected · {self.backend.label}", "muted"); self.execution_label = label("Offline", "muted"); tl.addWidget(self.connection_dot); tl.addWidget(self.connection_label); tl.addWidget(self.execution_label); tl.addStretch(1)
        self.mode_badge = label(self.settings.mode.upper(), "muted"); self.mode_badge.setStyleSheet(f"background:{COLORS['panel_2']}; padding:7px 10px; border-radius:6px; font-weight:650;"); tl.addWidget(self.mode_badge)
        self.pause_button = button("Pause", self.pause_host); self.resume_button = button("Resume", self.resume_host); self.next_waypoint_button = button("Next", self.end_current_waypoint); self.connect_button = button("Connect", self.toggle_connection, "primary")
        for widget in (self.pause_button, self.resume_button, self.next_waypoint_button, self.connect_button): tl.addWidget(widget)
        tl.addWidget(button("EMERGENCY STOP", self.emergency_stop, "danger")); ml.addWidget(topbar)
        self.stack = QtWidgets.QStackedWidget(); container = QtWidgets.QWidget(); container_layout = _vbox(container, (22, 18, 22, 18)); container_layout.addWidget(self.stack); ml.addWidget(container, 1); layout.addWidget(main, 1)
        self.statusBar().setSizeGripEnabled(False)

    def _build_pages(self) -> None:
        self.pages: dict[str, BasePage] = {"Watch current": WatchPage(self), "CV": StandaloneCVPage(self), "Approach": StandaloneApproachPage(self), "Approach + CV": ApproachCVPage(self), "Approach + I-t": ApproachITPage(self), "Scan hopping + CV": ScanHoppingCVPage(self), "Scan hopping + I-t": ScanHoppingITPage(self), "Move piezo": MovePiezoPage(self), "Settings": SettingsPage(self)}
        for page in self.pages.values(): self.stack.addWidget(page)

    def show_page(self, name: str) -> None:
        if not hasattr(self, "pages") or name not in self.pages: return
        self.stack.setCurrentWidget(self.pages[name]); self.nav_buttons[name].setChecked(True)

    def require_connection(self) -> None:
        if not self.backend.connected: raise BackendError("Connect to the simulator or NI FPGA first.")

    def pause_host(self) -> None:
        try: self.require_connection(); self.backend.pause(); self.toast("Host execution paused", "warning")
        except (BackendError, RuntimeError, OSError) as exc: self.show_error(str(exc))

    def resume_host(self) -> None:
        try: self.require_connection(); self.backend.resume(); self.toast("Host execution resumed", "success")
        except (BackendError, RuntimeError, OSError) as exc: self.show_error(str(exc))

    def end_current_waypoint(self) -> None:
        try: self.require_connection(); self.backend.end_current_waypoint(); self.toast("Requested the next FPGA waypoint", "warning")
        except (BackendError, RuntimeError, OSError) as exc: self.show_error(str(exc))

    @property
    def any_experiment_active(self) -> bool: return any(experiment.active for experiment in EChemTipsApp._experiments_for(self).values())

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
        key = {"CV": "cv", "Approach": "approach", "Approach then CV": "approach_cv", "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv", "Scan Hopping IT": "scan_it"}.get(self.recorder.name)
        return self.experiments[key].params if key is not None else None

    def toggle_connection(self) -> None:
        if self.backend.connected:
            for key, experiment in self.experiments.items():
                if experiment.active: self.stop_experiment(key)
            self.flush_acquisition()
            if self.recorder.active: self.finish_recording(self.active_parameters, status="aborted")
            self._stop_acquisition(); self.backend.disconnect(); self._set_connection_ui(False); self.toast("Device disconnected", "warning"); return
        try: self.backend.connect(); self._start_acquisition(); self._set_connection_ui(True); self.toast(f"Connected to {self.backend.label}", "success")
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
            watch = self.pages.get("Watch current")
            if isinstance(watch, WatchPage): watch.set_live_view(False)
        self._sync_action_states()

    def _sync_action_states(self) -> None:
        if not hasattr(self, "pages"): return
        connected = self.backend.connected; watch = self.pages["Watch current"]
        watch_owned = self.recorder.active and self.recorder.name == "Watch Current"; watch.start_recording_button.setEnabled(connected and not self.any_experiment_active and not self.recorder.active); watch.stop_recording_button.setEnabled(watch_owned)
        watch.live_button.setEnabled(connected and not self.any_experiment_active)
        for page_name, key in {"CV": "cv", "Approach": "approach", "Approach + CV": "approach_cv", "Approach + I-t": "approach_it", "Scan hopping + CV": "scan_cv", "Scan hopping + I-t": "scan_it"}.items():
            page = self.pages[page_name]; page.start_button.setEnabled(connected and not self.any_experiment_active and not self.recorder.active); page.stop_button.setEnabled(self.experiments[key].active)

    def apply_settings(self, settings: AppSettings) -> None:
        if self.any_experiment_active: raise ValueError("Stop the experiment before changing instrument settings.")
        was_connected = self.backend.connected
        if was_connected: self.flush_acquisition()
        if self.recorder.active: self.finish_recording(self.active_parameters)
        if was_connected: self._stop_acquisition(); self.backend.disconnect()
        self.store.save(settings); self.settings = settings; self.backend = create_backend(settings, self.driver_module); self._make_experiments()
        for plot in self.findChildren(Plot): plot.max_points = max(250, settings.display_max_points); plot.buffer.max_points = plot.max_points; plot.buffer.compact(); plot.redraw()
        self.mode_badge.setText(settings.mode.upper()); self._set_connection_ui(False)
        if was_connected: self.toast("Settings applied; reconnect to use the new backend", "warning")

    def finish_recording(self, parameters: object = None, status: str = "complete") -> Path | None:
        path = self.recorder.finish(self.settings, parameters, status=status); self._sync_action_states(); return path

    def _start_acquisition(self) -> None: self._stop_acquisition(); self._acquisition = AcquisitionWorker(self.backend); self._acquisition.start()
    def _stop_acquisition(self) -> AcquisitionDrain:
        worker = self._acquisition; self._acquisition = None; return worker.stop() if worker is not None else AcquisitionDrain([], None, 0)

    def flush_acquisition(self) -> None:
        worker = self._acquisition
        if worker is None: return
        drained = worker.pause_and_snapshot()
        try:
            self._consume_acquired(drained.samples, finalize=False)
            if drained.error is not None: raise BackendError(f"Acquisition flush failed: {drained.error}") from drained.error
        finally: worker.resume()

    def stop_experiment(self, which: str) -> None:
        experiment = self.experiments[which]
        if not experiment.active: return
        worker = self._acquisition
        try:
            if worker is not None:
                before = worker.pause_and_snapshot(); self._consume_acquired(before.samples, finalize=False)
                if before.error is not None: raise BackendError(f"Acquisition failed before cancellation: {before.error}") from before.error
            hardware = self.backend.hardware_approach_cv_required; self.backend.stop_motion()
            if not hardware: experiment.state = ExperimentState.ABORTED; experiment.detail = "Experiment stopped by operator"
            if worker is not None:
                after = worker.pause_and_snapshot(); self._consume_acquired(after.samples, finalize=False)
                if after.error is not None: raise BackendError(f"Final acquisition drain failed: {after.error}") from after.error
            if experiment.active: experiment.state = ExperimentState.ABORTED; experiment.detail = "Experiment stopped by operator"
            self.finish_recording(experiment.params, status="aborted"); self.toast("Stop acknowledged; final data drained and partial recording saved", "warning")
        except (BackendError, OSError, ValueError, RuntimeError) as exc:
            if experiment.active: experiment.state = ExperimentState.ABORTED
            if self.recorder.active: self.finish_recording(experiment.params, status="error")
            self.show_error(str(exc))
        finally:
            if worker is not None: worker.resume()
            self._sync_action_states()

    def emergency_stop(self) -> None:
        try:
            worker = self._acquisition; before = worker.pause_and_snapshot() if worker is not None else AcquisitionDrain([], None, 0); self._consume_acquired(before.samples, finalize=False)
            if self.backend.connected: self.backend.emergency_stop()
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
            for name, page in self.pages.items():
                if name in {"Scan hopping + CV", "Scan hopping + I-t"}: continue
                if name == "Watch current" and not page.live_enabled: continue
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
        key = {"CV": "cv", "Approach": "approach", "Approach then CV": "approach_cv", "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv", "Scan Hopping IT": "scan_it"}.get(self.recorder.name)
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
                key = {"CV": "cv", "Approach": "approach", "Approach then CV": "approach_cv", "Approach then IT": "approach_it", "Scan Hopping CV": "scan_cv", "Scan Hopping IT": "scan_it"}.get(self.recorder.name)
                terminal = bool(self.recorder.active and key is not None and EChemTipsApp._experiments_for(self)[key].state in (ExperimentState.COMPLETE, ExperimentState.ABORTED))
                if terminal and worker is not None:
                    final = worker.pause_and_snapshot(); EChemTipsApp._consume_acquired(self, final.samples, finalize=False); samples += final.samples; acquisition_error = acquisition_error or final.error; worker.resume()
                EChemTipsApp._finalize_experiments(self, bool(samples)); status_fn = getattr(self.backend, "execution_status", None)
                if callable(status_fn) and hasattr(self, "execution_label"):
                    status = status_fn(); self.execution_label.setText(f"{status.owner or 'host'} · {status.state.value} · {status.executed_waypoints}/{status.total_waypoints}")
                if acquisition_error is not None: raise BackendError(str(acquisition_error)) from acquisition_error
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
        if not isinstance(self, EChemTipsApp) and hasattr(self, "after"): self.after(80, self._poll)

    def toast(self, message: str, level: str = "info") -> None:
        color = {"success": COLORS["success"], "warning": COLORS["warning"], "danger": COLORS["danger"]}.get(level, COLORS["text"]); self.statusBar().setStyleSheet(f"QStatusBar {{ color:{color}; background:{COLORS['panel']}; font-weight:600; }}"); self.statusBar().showMessage(message, 4200)

    def show_error(self, message: str) -> None: QtWidgets.QMessageBox.critical(self, "eChemTips", message)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
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
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or [])
    app.setApplicationName("eChemTips"); app.setOrganizationName("eChemTips"); app.setStyle("Fusion"); app.setStyleSheet(application_stylesheet()); return app
