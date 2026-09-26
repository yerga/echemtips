"""Opt-in, nonmodal analytical calculator shared by control and analysis.

No backend reference or acquisition subscription: results are advisory only.
Explicit presets and exports never modify application defaults or recordings.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path
import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from .qt_common import XYPlot, label, button, scroll_area
from .seccm_models import ModelParameters, limiting_current, step_current, steady_wave, model_warnings, DOI, MODEL_VERSION

class CompactNumber(Q.QDoubleSpinBox):
    """Show significant digits without eight distracting trailing zeroes."""
    def textFromValue(self, value):
        """Format a value with the active locale and compact significant digits."""
        return self.locale().toString(value, 'g', 8)



class ModelDialog(Q.QDialog):
    """Calculate curves or compare a frozen original-data selection in IUPAC units."""
    def __init__(self, parent=None, dataset=None, cycles=()):
        super().__init__(parent)
        self.setWindowTitle("SECCM models — advisory calculator")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1050, 760)
        self.dataset = dataset
        self.result = None
        self.fields = {}
        self.setMinimumSize(820, 600)
        root = Q.QVBoxLayout(self)
        root.addWidget(label("Predictions only · no instrument settings or landing decisions are changed", word_wrap=True))
        self.tabs = Q.QTabWidget(); root.addWidget(self.tabs, 1)
        results = Q.QWidget(); body = Q.QVBoxLayout(results)
        if dataset is not None:
            root.addWidget(label("Source snapshot: " + dataset.path.name + " · close and reopen to change recording", word_wrap=True))
        self.tabs.addTab(results, "Calculate / compare")
        controls = Q.QGridLayout(); body.addLayout(controls)
        self.kind = Q.QComboBox(); self.kind.addItems(["Steady-state i–E", "Diffusion-limited step i–t"])
        self.source = Q.QComboBox(); self.source.addItem("Prediction only", None)
        self.source.setMinimumContentsLength(16)
        self.source.setSizeAdjustPolicy(Q.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        if dataset is not None:
            self.source.addItem("Original recording — choose time bounds", dataset.rows)
            for cycle in cycles:
                self.source.addItem("Original " + cycle.label, cycle.rows)
        self.channel = Q.QComboBox(); self.channel.addItems(["current1_na", "current2_na"])
        for col, (caption, widget) in enumerate((("Model", self.kind), ("Compare with", self.source), ("Current channel", self.channel))):
            controls.addWidget(label(caption), 0, col); controls.addWidget(widget, 1, col)
        self.e_start = self.number(-100, 100, -0.3)
        self.e_end = self.number(-100, 100, 0.3)
        self.t_start = self.number(0, 1e9, 0)
        self.t_end = self.number(0, 1e9, 1)
        self.origin = self.number(0, 1e9, 0)
        self.interval = self.number(0, 1e6, 0)
        options = Q.QWidget(); form = Q.QFormLayout(options)
        self.options_form = form
        form.addRow("Prediction E start / end (V)", self.pair(self.e_start, self.e_end))
        form.addRow("Recording time bounds / step preview bounds (s)", self.pair(self.t_start, self.t_end))
        form.addRow("Step origin in recording time (s)", self.origin)
        form.addRow("Step averaging interval (ms; 0 = instantaneous)", self.interval)
        self.recording_sign = Q.QComboBox(); self.recording_sign.addItems(["Select recording polarity…", "IUPAC", "Opposite to IUPAC"])
        if dataset is not None:
            convention = dataset.metadata.get("settings", {}).get("polarity_convention")
            if convention == "IUPAC": self.recording_sign.setCurrentIndex(1)
            elif convention == "Instrument-native": self.recording_sign.setCurrentIndex(2)
        form.addRow("Recorded E and i convention", self.recording_sign)
        body.addWidget(options)
        self.plot = XYPlot("Potential (V)", "Current (nA)")
        body.addWidget(self.plot, 1)
        self.summary = label("", word_wrap=True); body.addWidget(self.summary)
        actions = Q.QHBoxLayout(); body.addLayout(actions)
        actions.addWidget(button("Calculate", self.calculate, "primary"))
        self.plot.current_display_unit = "pA"
        self.export_button = button("Export result…", self.export_result); self.export_button.setEnabled(False)
        actions.addWidget(self.export_button)
        actions.addWidget(button("Model parameters…", lambda: self.tabs.setCurrentIndex(1)))
        parameters = Q.QWidget(); outer = Q.QVBoxLayout(parameters)
        outer.addWidget(label("Physical inputs — illustrative defaults, not measured instrument geometry", word_wrap=True))
        pf = Q.QFormLayout(); outer.addLayout(pf)
        specs = [
            ("radius_m", "Pipette inner radius (nm)", 200, 1e-9),
            ("half_angle_deg", "Pipette half-angle (°)", 7.5, 1),
            ("height_m", "Meniscus height (nm; not contact Z)", 100, 1e-9),
            ("footprint_radius_m", "Physical footprint radius (nm; kinetics only)", 224, 1e-9),
            ("diffusion_m2_s", "Diffusion coefficient (×10⁻⁹ m²/s)", 1, 1e-9),
            ("concentration_mol_m3", "Reactant concentration (mM)", 1, 1),
            ("electrons", "Electrons per reactant (kinetic model: 1)", 1, 1),
            ("formal_potential_v", "Formal potential (V; same reference as data)", 0, 1),
            ("temperature_k", "Temperature (K)", 298.15, 1),
            ("rate_m_s", "Standard rate constant k⁰ (cm/s)", 0.001, 0.01),
            ("alpha", "Transfer coefficient α", 0.5, 1)]
        for key, caption, value, factor in specs:
            widget = self.number(-1e9, 1e9, value)
            if key == "electrons": widget.setDecimals(0)
            self.fields[key] = (widget, factor); pf.addRow(caption, widget)
        self.radius_model = Q.QComboBox(); self.radius_model.addItems(["R3", "R2", "R1"])
        pf.addRow("Equivalent radius approximation", self.radius_model)
        self.reaction = Q.QComboBox(); self.reaction.addItems(["Reduction (negative current)", "Oxidation (positive current)"])
        pf.addRow("Reaction / IUPAC polarity", self.reaction)
        self.spread = self.number(0, 90, 0)
        pf.addRow("Pipette-radius sensitivity ± (%)", self.spread)
        outer.addWidget(label("Radius sensitivity varies only inner radius, with other inputs fixed; it is not a confidence interval. Footprint radius is independent of the map display setting.", word_wrap=True))
        outer.addWidget(label("Supported: ideal diffusion-limited step; steady-state one-electron wave with equal diffusivities and negligible iR. Not dynamic CV, capacitive contact, arbitrary pulse history or migration/HER kinetics.", word_wrap=True))
        row = Q.QHBoxLayout(); outer.addLayout(row)
        row.addWidget(button("Load parameters…", self.load_parameters))
        row.addWidget(button("Save parameters…", self.save_parameters))
        row.addWidget(button("Calculate / compare", lambda: self.tabs.setCurrentIndex(0)))
        outer.addStretch()
        self.tabs.addTab(scroll_area(parameters), "Model parameters")
        close = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close); root.addWidget(close)
        for widget in self.findChildren(Q.QDoubleSpinBox): widget.valueChanged.connect(self.invalidate)
        for widget in self.findChildren(Q.QComboBox): widget.currentIndexChanged.connect(self.invalidate)
        self.source.currentIndexChanged.connect(self.select_source)
        self.kind.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()
        self.calculate()

        self.select_source()
    @staticmethod
    def number(low, high, value):
        """Consistent editable scientific inputs, with explicit units in labels."""
        w = CompactNumber(); w.setDecimals(8); w.setRange(low, high); w.setValue(value)
        w.setKeyboardTracking(False); return w

    @staticmethod
    def pair(a, b):
        """Group two related bounds without widening the entire dialog."""
        w = Q.QWidget(); layout = Q.QHBoxLayout(w); layout.setContentsMargins(0,0,0,0)
        layout.addWidget(a); layout.addWidget(b); return w

    def parameters(self):
        """Translate explicitly entered display units into validated SI parameters."""
        values = {k: w.value()*factor for k, (w, factor) in self.fields.items()}
        values['electrons'] = int(values['electrons'])
        p = ModelParameters(**values, radius_model=self.radius_model.currentText(), oxidation=bool(self.reaction.currentIndex()))
        p.validate(); return p

    def invalidate(self, *_):
        """Never export stale curves under newly edited model settings."""
        self.result = None; self.export_button.setEnabled(False)
        self.summary.setText("Inputs changed — press Calculate to update.")

    def mode_changed(self, *_):
        """Disable irrelevant controls while keeping the layout stable."""
        step = self.kind.currentIndex() == 1
        self.origin.setEnabled(step); self.interval.setEnabled(step)
        self.e_start.setEnabled(not step); self.e_end.setEnabled(not step)
        rows = self.source.currentData() is not None
        self.options_form.setRowVisible(0, not step and not rows)
        self.options_form.setRowVisible(1, step or rows)
        self.options_form.setRowVisible(2, step and rows)
        self.options_form.setRowVisible(3, step)
        self.options_form.setRowVisible(4, rows)
        self.channel.setEnabled(rows)

    def select_source(self, *_):
        """Default bounds to the explicit selected original-data scope."""
        rows = self.source.currentData()
        if rows is not None:
            t = rows.matrix[:, rows.columns.index('elapsed_s')]
            finite = t[np.isfinite(t)]
            if len(finite): self.t_start.setValue(float(finite.min())); self.t_end.setValue(float(finite.max()))
        self.recording_sign.setEnabled(rows is not None)
        self.mode_changed()

    def calculate(self):
        """Evaluate selected full-resolution samples; plot reduction is display-only."""
        self.invalidate()
        try:
            p = self.parameters(); step = self.kind.currentIndex() == 1
            lo, hi = self.t_start.value(), self.t_end.value()
            if hi <= lo: raise ValueError("End time must exceed start time.")
            dt = self.interval.value()/1000
            rows = self.source.currentData(); measured = None
            if rows is not None:
                if self.recording_sign.currentIndex() == 0: raise ValueError("Confirm the recording's polarity convention before comparing.")
                t = rows.matrix[:, rows.columns.index('elapsed_s')]
                mask = np.isfinite(t) & (t >= lo) & (t <= hi)
                if step: mask &= (t - self.origin.value() > 0) & (t - self.origin.value() >= dt)
                factor = 1 if self.recording_sign.currentIndex() == 1 else -1
                measured = factor * rows.matrix[mask, rows.columns.index(self.channel.currentText())] * 1e-9
                x = t[mask] - self.origin.value() if step else factor * rows.matrix[mask, rows.columns.index('voltage1_v')]
                valid = np.isfinite(x) & np.isfinite(measured); x, measured = x[valid], measured[valid]
                if not len(x): raise ValueError("No valid samples in this interval; review step origin and bounds.")
            elif step:
                low = max(lo, dt, 1e-5)
                if hi <= low: raise ValueError("Step end time must exceed the averaging interval and start time.")
                x = np.geomspace(low, hi, 800)
            else:
                if self.e_end.value() <= self.e_start.value(): raise ValueError("End potential must exceed start potential.")
                x = np.linspace(self.e_start.value(), self.e_end.value(), 800)
            evaluate = lambda params: step_current(params, x, interval_s=dt) if step else steady_wave(params, x)
            predicted = evaluate(p)
            series = [("Model", x, predicted*1e9, "#008c87")]
            radius_curves = []
            if self.spread.value():
                for sign in (-1, 1):
                    varied = replace(p, radius_m=p.radius_m*(1+sign*self.spread.value()/100))
                    y = evaluate(varied); radius_curves.append(y.tolist())
                    series.append((f"Radius {sign*self.spread.value():+g}%", x, y*1e9, "#a1a8bc"))
            residual = None
            if measured is not None:
                series.insert(0, ("Original data (IUPAC)", x, measured*1e9, "#304d79"))
                residual = measured - predicted
            self.plot.x_label = "Time since step (s)" if step else "Potential (V, IUPAC convention)"
            self.plot.set_data(series)
            message = f"Predicted steady limiting current: {limiting_current(p)*1e12:.4g} pA. "
            if residual is not None: message += f"RMS residual: {np.sqrt(np.mean(residual**2))*1e12:.4g} pA ({len(x)} original samples). "
            if step and np.min(x) < 1e-5: message += "Very early times: meniscus geometry and instrument response can dominate. "
            self.summary.setText(message + "Approximation only; not a landing-quality score.")
            self.result = dict(schema=1, model_version=MODEL_VERSION, doi=DOI, model=self.kind.currentText(),
                parameters_si=asdict(p), assumptions=model_warnings(p), source=str(self.dataset.path) if rows is not None else None,
                selection=self.source.currentText(), channel=self.channel.currentText(),
                recording_polarity=self.recording_sign.currentText(), output_polarity="IUPAC",
                time_bounds_s=[lo,hi], step_origin_s=self.origin.value(), averaging_interval_s=dt if step else 0,
                radius_sensitivity_percent=self.spread.value(), radius_sensitivity_current_a=radius_curves,
                x_unit="s" if step else "V", x=x.tolist(), predicted_current_a=predicted.tolist(),
                measured_current_a=None if measured is None else measured.tolist(),
                residual_current_a=None if residual is None else residual.tolist())
            self.export_button.setEnabled(True)
        except (ValueError, KeyError, OverflowError) as exc:
            self.plot.set_message("Review model inputs")
            self.summary.setText(str(exc))

    def write_json(self, payload, title):
        """Atomic, explicit JSON export with source-file protection."""
        path, _ = Q.QFileDialog.getSaveFileName(self, title, "seccm-model.json", "JSON (*.json)")
        if not path: return
        try:
            target = Path(path).resolve()
            if self.dataset is not None and target in (self.dataset.path.resolve(), self.dataset.path.with_suffix('.json').resolve()):
                raise ValueError("Choose a different path; source recordings are protected.")
            data = json.dumps(payload, indent=2, allow_nan=False).encode()
            out = QtCore.QSaveFile(str(target))
            if not out.open(QtCore.QIODevice.OpenModeFlag.WriteOnly): raise OSError(out.errorString())
            if out.write(data) != len(data) or not out.commit(): raise OSError(out.errorString())
        except (OSError, ValueError) as exc: Q.QMessageBox.warning(self, "Not saved", str(exc))

    def export_result(self):
        """Save predictions, residuals and all assumptions without touching data."""
        if self.result is not None: self.write_json(self.result, "Export model result")

    def save_parameters(self):
        """Save an explicitly named preset; never replace instrument defaults."""
        try:
            self.write_json(dict(schema=1, kind="seccm-model-parameters", parameters=asdict(self.parameters()), radius_sensitivity_percent=self.spread.value()), "Save model parameters")
        except ValueError as exc: Q.QMessageBox.warning(self, "Invalid parameters", str(exc))

    def load_parameters(self):
        """Validate a complete preset before changing any widgets."""
        path, _ = Q.QFileDialog.getOpenFileName(self, "Load model parameters", "", "JSON (*.json)")
        if not path: return
        try:
            payload = json.loads(Path(path).read_text())
            if payload.get('schema') != 1 or payload.get('kind') != 'seccm-model-parameters': raise ValueError("Not a supported model preset.")
            p = ModelParameters(**payload['parameters']); p.validate()
            for k, (w, factor) in self.fields.items():
                value = getattr(p, k)/factor
                if not w.minimum() <= value <= w.maximum(): raise ValueError("Preset value exceeds UI range.")
            for k, (w, factor) in self.fields.items(): w.setValue(getattr(p, k)/factor)
            self.radius_model.setCurrentText(p.radius_model); self.reaction.setCurrentIndex(int(p.oxidation))
            self.spread.setValue(float(payload.get('radius_sensitivity_percent', 0)))
        except (OSError, ValueError, KeyError, TypeError) as exc: Q.QMessageBox.warning(self, "Preset not loaded", str(exc))


def open_model_dialog(parent, dataset=None, cycles=()):
    """Reuse a visible dialog; data are snapshotted when it is first opened."""
    old = getattr(parent, '_seccm_model_dialog', None)
    if old is not None:
        old.raise_(); old.activateWindow(); return old
    dialog = ModelDialog(parent, dataset, cycles)
    parent._seccm_model_dialog = dialog
    dialog.destroyed.connect(lambda: setattr(parent, '_seccm_model_dialog', None))
    dialog.show(); return dialog
