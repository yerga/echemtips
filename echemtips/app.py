from __future__ import annotations

import argparse

from PySide6 import QtWidgets

from .backends import SimulationBackend
from .experiments import (
    ApproachCVExperiment, ApproachExperiment, ApproachITExperiment, CVExperiment,
    ExperimentState, ScanHoppingCVExperiment, ScanHoppingITExperiment,
)
from .models import (
    AppSettings, ApproachCVParameters, ApproachITParameters, ApproachParameters,
    CVParameters, Sample, ScanHoppingCVParameters, ScanHoppingITParameters,
)
from .ui import EChemTipsApp, create_application


def _smoke_test(app: EChemTipsApp) -> None:
    """Exercise every page and the simulation backend without user input."""
    app.resize(1080, 680)
    app.show()
    QtWidgets.QApplication.processEvents()
    # A smoke test must never inherit a persisted NI-FPGA selection and try to
    # open hardware. Replace only the runtime backend; do not write settings.
    app.settings = AppSettings(mode="Simulation")
    app.backend = SimulationBackend(app.settings)
    app.experiment = ApproachCVExperiment(app.backend, app.settings)
    app.scan_experiment = ScanHoppingCVExperiment(app.backend, app.settings)
    app.cv_experiment = CVExperiment(app.backend, app.settings)
    app.approach_experiment = ApproachExperiment(app.backend, app.settings)
    app.approach_it_experiment = ApproachITExperiment(app.backend, app.settings)
    app.scan_it_experiment = ScanHoppingITExperiment(app.backend, app.settings)
    app.experiments = {
        "approach_cv": app.experiment, "scan_cv": app.scan_experiment,
        "cv": app.cv_experiment, "approach": app.approach_experiment,
        "approach_it": app.approach_it_experiment, "scan_it": app.scan_it_experiment,
    }
    app._last_experiment_state = app.experiment.state
    app.backend.connect()
    app._set_connection_ui(True)
    samples = [app.backend.read_sample() for _ in range(5)]
    watch = app.pages["Watch current"]
    watch.set_live_view(True)
    for name, page in app.pages.items():
        app.show_page(name)
        page.on_samples(samples)
        QtWidgets.QApplication.processEvents()
        if page.sizeHint().width() <= 0 or page.sizeHint().height() <= 0:
            raise RuntimeError(f"Page did not lay out correctly: {name}")
    if len(watch.plot.x_values) < len(samples):
        raise RuntimeError("Watch Current discarded samples from an acquisition batch")
    cv = app.pages["CV"]
    app.cv_experiment.start(CVParameters(-.1, .2, -.2, 100, 1))
    for _ in range(5):
        app.cv_experiment._last_tick -= 1
        cv.on_samples([Sample(0, 50, 50, 50, 0, 0, 0, 0)])
        if app.cv_experiment.state == ExperimentState.COMPLETE:
            break
    if app.cv_experiment.state != ExperimentState.COMPLETE or not cv.cv_plot.x_values:
        raise RuntimeError("Standalone CV UI did not complete and populate its voltammogram")

    standalone_approach = app.pages["Approach"]
    app.approach_experiment.start(ApproachParameters(
        start_z_um=10,
        end_z_um=90,
        approach_rate_um_s=3,
        retract_rate_um_s=10,
        approach_voltage_v=.1,
        feedback_channel="Current 1",
        feedback_threshold=2,
        greater_than=True,
        retract_after=True,
    ))
    standalone_approach.on_samples([
        Sample(0, 50, 50, 10, .1, 0, 0, 0),
        Sample(1, 50, 50, 68, .1, 0, 3, 0),
        Sample(2, 50, 50, 10, .1, 0, 0, 0),
    ])
    if app.approach_experiment.state != ExperimentState.COMPLETE or not standalone_approach.z_plot.x_values:
        raise RuntimeError("Standalone Approach UI did not detect contact and retract")

    approach_it = app.pages["Approach + I-t"]
    app.approach_it_experiment.start(ApproachITParameters(
        start_z_um=10, end_z_um=90, approach_rate_um_s=3, retract_rate_um_s=10,
        feedback_threshold=2, initial_hold_s=.001, step_hold_s=.001, return_hold_s=.001,
    ))
    it_contact = Sample(1, 50, 50, 68, .1, 0, 3, 0)
    approach_it.on_samples([Sample(0, 50, 50, 10, .1, 0, 0, 0), it_contact])
    approach_it.on_samples([it_contact])
    for _ in range(3):
        app.approach_it_experiment._step_deadline -= 1
        approach_it.on_samples([it_contact])
    approach_it.on_samples([Sample(3, 50, 50, 10, .1, 0, 0, 0)])
    if app.approach_it_experiment.state != ExperimentState.COMPLETE or not approach_it.it_plot.x_values:
        raise RuntimeError("Approach + I-t UI did not run its contact-gated potential steps")
    approach = app.pages["Approach + CV"]
    if not all(hasattr(approach, name) for name in ("z_plot", "current_plot", "cv_plot", "approach_curve", "stop_button")):
        raise RuntimeError("Approach + CV is missing a required trace or stop control")
    approach_params = ApproachCVParameters(
        start_z_um=10, end_z_um=90, approach_rate_um_s=3, feedback_threshold_na=2,
        cv_start_v=0, cv_vertex1_v=.5, cv_vertex2_v=-.5, cv_scan_rate_v_s=100,
        cycles=1, retract_after=True,
    )
    approach.z_plot.clear()
    approach.current_plot.clear()
    approach.cv_plot.clear()
    app.experiment.start(approach_params)
    at_start = Sample(0, 50, 50, 10, 0, 0, 0, 0)
    contact = Sample(1, 50, 50, 68, 0, 0, 3, 0)
    approach.on_samples([at_start, contact])
    for _ in range(3):
        app.experiment._last_tick -= 1
        approach.on_samples([contact])
    approach.on_samples([at_start])
    if app.experiment.state != ExperimentState.COMPLETE or not approach.cv_plot.x_values:
        raise RuntimeError("Approach + CV simulation did not populate its separate CV plot")
    scan = app.pages["Scan hopping + CV"]
    if not all(hasattr(scan, name) for name in ("z_plot", "current_plot", "cv_plot", "z_map", "current_map")):
        raise RuntimeError("Scan Hopping + CV is missing a required trace or map")
    scan_params = ScanHoppingCVParameters(
        x_start_um=40, x_end_um=60, x_points=2, y_start_um=40, y_end_um=60, y_points=2,
        start_z_um=55, end_z_um=80, lateral_rate_um_s=100, approach_rate_um_s=20,
        retract_rate_um_s=100, cv_scan_rate_v_s=10, cycles=1, map_potential_v=.2,
    )
    scan.z_plot.clear()
    scan.current_plot.clear()
    scan.cv_plot.clear()
    app.scan_experiment.start(scan_params)
    scan_sample_count = 0
    for _ in range(300):
        app.backend._last_tick -= .25
        app.scan_experiment._last_tick -= .25
        scan_sample = app.backend.read_sample()
        scan.on_samples([scan_sample])
        scan_sample_count += 1
        if app.scan_experiment.state == ExperimentState.COMPLETE:
            break
    if app.scan_experiment.state != ExperimentState.COMPLETE:
        raise RuntimeError("Scan Hopping + CV UI simulation did not complete")
    if len(scan.z_plot.x_values) != scan_sample_count or len(scan.current_plot.x_values) != scan_sample_count:
        raise RuntimeError("Scan history plots do not preserve the full experiment")
    if not scan.cv_plot.x_values or len(app.scan_experiment.contact_z) != scan_params.point_count:
        raise RuntimeError("Scan Hopping + CV did not populate its CV or contact map")
    scan_it = app.pages["Scan hopping + I-t"]
    if not all(hasattr(scan_it, name) for name in ("z_plot", "current_plot", "it_plot", "z_map", "current_map")):
        raise RuntimeError("Scan Hopping + I-t is missing a required trace or map")
    app.scan_it_experiment.start(ScanHoppingITParameters(
        x_start_um=50, x_end_um=50, x_points=1, y_start_um=50, y_end_um=50, y_points=1,
        start_z_um=55, end_z_um=80, lateral_rate_um_s=100, approach_rate_um_s=20,
        retract_rate_um_s=100, feedback_threshold=2, initial_hold_s=.001,
        step_hold_s=.001, return_hold_s=.001,
    ))
    scan_it_samples = 0
    for _ in range(300):
        app.backend._last_tick -= .25
        if app.scan_it_experiment.state == ExperimentState.IT:
            app.scan_it_experiment._step_deadline -= 1
        sample = app.backend.read_sample()
        scan_it.on_samples([sample])
        scan_it_samples += 1
        if app.scan_it_experiment.state in (ExperimentState.COMPLETE, ExperimentState.ABORTED):
            break
    if app.scan_it_experiment.state != ExperimentState.COMPLETE:
        raise RuntimeError("Scan Hopping + I-t UI simulation did not complete")
    if len(scan_it.z_plot.x_values) != scan_it_samples or not app.scan_it_experiment.current_at_pulse:
        raise RuntimeError("Scan Hopping + I-t did not preserve traces or populate its pulse-current map")
    settings = app.pages["Settings"]
    app.show_page("Settings")
    QtWidgets.QApplication.processEvents()
    if settings.viewport.verticalScrollBar().maximum() <= 0:
        raise RuntimeError("Settings content did not exercise its vertical scrollbar at minimum window size")
    app.backend.move("Z", 12.0, 2.0)
    app.backend.emergency_stop()
    app.backend.disconnect()
    app.poll_timer.stop()
    app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="eChemTips scanning electrochemistry control interface")
    parser.add_argument("--smoke-test", action="store_true", help="build and exercise the UI, then exit")
    parser.add_argument(
        "--page",
        choices=(
            "Watch current", "CV", "Approach", "Approach + CV", "Approach + I-t",
            "Scan hopping + CV", "Scan hopping + I-t", "Move piezo", "Settings",
        ),
        help="page to show when the app opens",
    )
    args = parser.parse_args()
    qt_app = create_application()
    app = EChemTipsApp()
    if args.smoke_test:
        _smoke_test(app)
        print("eChemTips UI smoke test passed")
        return
    if args.page:
        app.show_page(args.page)
    app.show()
    raise SystemExit(qt_app.exec())


if __name__ == "__main__":
    main()
