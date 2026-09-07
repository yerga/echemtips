"""Acquisition ordering tests without a GUI or NI hardware."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import csv
import json
import unittest

from echemtips.backends import BackendError
from echemtips.acquisition import AcquisitionDrain
from echemtips.data import DataRecorder
from echemtips.experiments import ExperimentState
from echemtips.models import AppSettings, Sample, ScanHoppingCVParameters
from echemtips.ui import EChemTipsApp


class AcquisitionOrderingTests(unittest.TestCase):
    def test_stop_is_sent_before_a_snapshot_processing_failure(self):
        calls: list[str] = []
        experiment = SimpleNamespace(active=True, state=ExperimentState.CV, detail="", params=None)
        worker = SimpleNamespace(
            pause_and_snapshot=lambda: AcquisitionDrain([], None, 0),
            resume=lambda: None,
        )
        app = SimpleNamespace(
            experiments={"scan_cv": experiment},
            _acquisition=worker,
            backend=SimpleNamespace(
                hardware_approach_cv_required=True,
                stop_motion=lambda: calls.append("stop"),
            ),
            _consume_acquired=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
            recorder=SimpleNamespace(active=False),
            finish_recording=lambda *_args, **_kwargs: None,
            show_error=lambda error: calls.append(str(error)),
            _sync_action_states=lambda: None,
        )

        EChemTipsApp.stop_experiment(app, "scan_cv")

        self.assertEqual(calls, ["stop", "disk full"])

    def test_emergency_stop_is_sent_before_a_snapshot_processing_failure(self):
        calls: list[str] = []
        worker = SimpleNamespace(pause_and_snapshot=lambda: AcquisitionDrain([], None, 0))
        app = SimpleNamespace(
            _acquisition=worker,
            backend=SimpleNamespace(
                connected=True,
                emergency_stop=lambda: calls.append("emergency-stop"),
            ),
            _consume_acquired=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
            show_error=lambda error: calls.append(str(error)),
        )

        EChemTipsApp.emergency_stop(app)

        self.assertEqual(calls, ["emergency-stop", "disk full"])

    def test_acquisition_error_finishes_recording_even_if_stop_fails(self):
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            recorder = DataRecorder()
            recorder.start("Watch Current", settings)
            recorder.append(Sample(1, 35, 35, 68, .2, 0, 3, 0))
            path = recorder.output_path

            def fail():
                raise BackendError("Device unavailable")

            app = SimpleNamespace(
                backend=SimpleNamespace(connected=True, read_samples=fail,
                                        stop_motion=fail, disconnect=lambda: None),
                recorder=recorder, active_parameters=None,
                experiment=SimpleNamespace(active=False),
                scan_experiment=SimpleNamespace(active=False),
                finish_recording=lambda parameters, status: recorder.finish(status=status),
                _set_connection_ui=lambda value: None, show_error=lambda error: None,
                after=lambda *args: None, _poll=lambda: None,
            )
            EChemTipsApp._poll(app)
            self.assertFalse(recorder.active)
            self.assertEqual(json.loads(path.with_suffix(".json").read_text())["status"], "error")

    def test_last_scan_batch_is_tagged_and_saved_before_finish(self):
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            recorder = DataRecorder()
            params = ScanHoppingCVParameters(x_points=1, y_points=1)
            recorder.start("Scan Hopping CV", settings, params)
            sample = Sample(1, 35, 35, 68, .2, 0, 3, 0)
            scan = SimpleNamespace(state=ExperimentState.CV, active=True, params=params)

            def process(samples):
                for row in samples:
                    row.scan_pixel = row.scan_row = row.scan_column = 0
                scan.state = ExperimentState.COMPLETE
                scan.active = False

            outputs = []
            def finish(parameters, status="complete"):
                path = recorder.finish(settings, parameters, status=status)
                outputs.append(path)
                return path

            app = SimpleNamespace(
                backend=SimpleNamespace(connected=True, read_samples=lambda: [sample]),
                pages={"Scan hopping + CV": SimpleNamespace(on_samples=process)},
                recorder=recorder, scan_experiment=scan,
                experiment=SimpleNamespace(state=ExperimentState.IDLE, active=False),
                _last_experiment_state=ExperimentState.IDLE,
                finish_recording=finish, toast=lambda *args: None, after=lambda *args: None,
                _poll=lambda: None,
            )
            EChemTipsApp._poll(app)
            self.assertEqual(len(outputs), 1)
            with Path(outputs[0]).open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scan_pixel"], "0")
            self.assertEqual(rows[0]["current1_na"], "3")

    def test_worker_is_barrier_drained_before_recording_finishes(self):
        with TemporaryDirectory() as folder:
            settings = AppSettings(save_directory=folder)
            recorder = DataRecorder()
            params = ScanHoppingCVParameters(x_points=1, y_points=1)
            recorder.start("Scan Hopping CV", settings, params)
            first = Sample(1, 35, 35, 68, .2, 0, 3, 0)
            final = Sample(2, 35, 35, 68, .1, 0, 4, 0)
            scan = SimpleNamespace(state=ExperimentState.CV, active=True, params=params)

            def process(samples):
                for row in samples:
                    row.scan_pixel = row.scan_row = row.scan_column = 0
                if first in samples:
                    scan.state = ExperimentState.COMPLETE
                    scan.active = False

            class Worker:
                resumed = False

                def drain(self):
                    return AcquisitionDrain([first], None, 1)

                def pause_and_snapshot(self):
                    return AcquisitionDrain([final], None, 1)

                def resume(self):
                    self.resumed = True

            outputs = []
            def finish(parameters, status="complete"):
                path = recorder.finish(settings, parameters, status=status)
                outputs.append(path)
                return path

            worker = Worker()
            app = SimpleNamespace(
                backend=SimpleNamespace(connected=True), _acquisition=worker,
                pages={"Scan hopping + CV": SimpleNamespace(on_samples=process)},
                recorder=recorder, scan_experiment=scan,
                experiment=SimpleNamespace(state=ExperimentState.IDLE, active=False),
                _last_experiment_state=ExperimentState.IDLE,
                finish_recording=finish, toast=lambda *args: None, after=lambda *args: None,
                _poll=lambda: None,
            )
            EChemTipsApp._poll(app)
            self.assertTrue(worker.resumed)
            self.assertEqual(len(outputs), 1)
            with Path(outputs[0]).open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["current1_na"] for row in rows], ["3", "4"])
