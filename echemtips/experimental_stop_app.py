"""Opt-in GUI for the experimental/stop-recovery branch only.

Launch with ``python -m echemtips.experimental_stop_app``. The normal launcher
keeps production cancellation behaviour even in this checkout.
"""
from __future__ import annotations

import queue
import secrets
import threading
import time
import argparse
import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .acquisition import AcquisitionDrain
from .experiments import ExperimentState, ExperimentUpdate
from .stop_return import stop_and_return
from .ui import EChemTipsApp, create_application


class ExperimentalStopApp(EChemTipsApp):
    """Run Stop/recovery/return off the GUI thread with exclusive I/O ownership."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle(self.windowTitle() + " — EXPERIMENTAL STOP RETURN")
        self._stop_trial = None

    def stop_experiment(self, which: str) -> None:
        """Cancel the active method, close its recording, and trial a safe Z return."""
        experiment = self.experiments[which]
        if not experiment.active or self._stop_trial is not None:
            return
        # No GUI polling, experiment state transitions or other acquisition
        # reader may operate while marker alignment owns the hardware FIFO.
        self.poll_timer.stop()
        before = AcquisitionDrain([], None, 0)
        acquisition = self._acquisition
        if acquisition is not None:
            before = acquisition.stop()
            self._acquisition = None
        experiment.state = ExperimentState.ABORTED
        experiment.detail = "Stopping — experimental FPGA recovery and Z return"
        abort = threading.Event()
        recording_closed = threading.Event()
        messages = queue.Queue()
        self._stop_trial = abort
        dialog = QtWidgets.QProgressDialog("Stopping experiment", "Emergency stop", 0, 0, self)
        dialog.setWindowTitle("Experimental stop and Z return")
        dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        def request_abort():
            """Request emergency stopping without blocking the GUI on hardware I/O."""
            abort.set()
            dialog.setLabelText("Emergency stop requested — waiting for hardware worker")
            dialog.show()
        dialog.canceled.connect(request_abort)
        dialog.show()
        path = Path(self.settings.save_directory) / "stop-recovery" / (
            time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3) + "_gui.jsonl")

        def check_cancelled():
            """Prevent recovery or further return motion after an emergency request."""
            if abort.is_set():
                raise RuntimeError("Emergency stop requested; automatic return cancelled")

        def on_cancelled(tail):
            """Wait for the GUI to acknowledge saving the interrupted recording."""
            messages.put(("cancelled", tail))
            deadline = time.monotonic() + 30
            while not recording_closed.wait(.01):
                check_cancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Recording finalization did not acknowledge Stop")
            check_cancelled()

        def work():
            """Own hardware I/O exclusively throughout cancellation and recovery."""
            nonlocal before
            try:
                with self.backend.io_lock:
                    if acquisition is not None:
                        late = acquisition.stop()
                        before = AcquisitionDrain(before.samples + late.samples,
                                                  before.error or late.error,
                                                  max(before.peak_pending_samples, late.peak_pending_samples))
                    # A logging failure must not leave the original motion running.
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("x", encoding="utf-8") as log:
                            result = stop_and_return(self.backend, experiment.params, log,
                                                     on_cancelled, check_cancelled,
                                                     lambda text: messages.put(("progress", text)))
                    except BaseException:
                        self.backend.emergency_stop()
                        raise
                messages.put(("done", result))
            except BaseException as exc:
                messages.put(("failed", str(exc)))

        def save_partial(tail, status="aborted"):
            """Save trustworthy pre-stop data without advancing the experiment."""
            d = getattr(self.backend, "_driver", None)
            for sample in before.samples + tail:
                # Preserve pixel attribution before a return command replaces
                # the active program. Do not run the old experiment state machine.
                if d is not None and which in {"scan_cv", "scan_it"}:
                    context = d.scan_context if which == "scan_cv" else d.method_context
                    sample.scan_pixel = context(sample.line_number)[0]
                self.recorder.append(sample)
            if self.recorder.active:
                self.recorder._metadata["experimental_stop_return"] = {
                    "diagnostic_log": str(path), "recovery_and_return_excluded_from_csv": True,
                    "hardware_safety_validated": False,
                }
            self.finish_recording(experiment.params, status=status)

        timer = QtCore.QTimer(self)
        def poll():
            """Handle worker progress and recording requests on the GUI thread."""
            while True:
                try:
                    kind, value = messages.get_nowait()
                except queue.Empty:
                    return
                if kind == "progress":
                    dialog.setLabelText(value)
                elif kind == "cancelled":
                    try:
                        save_partial(value, "error" if before.error else "aborted")
                        if before.error:
                            raise RuntimeError(str(before.error))
                    except Exception as exc:
                        abort.set()
                        dialog.setLabelText(f"Recording failed; return cancelled: {exc}")
                    finally:
                        recording_closed.set()
                elif kind in {"done", "failed"}:
                    timer.stop()
                    dialog.canceled.disconnect(request_abort)
                    dialog.close()
                    dialog.deleteLater()
                    timer.deleteLater()
                    self._stop_trial = None
                    experiment.detail = value["detail"] if kind == "done" else f"Stop/return failed: {value}"
                    for page in self.pages.values():
                        if getattr(page, "experiment_key", None) == which:
                            page._show_update(ExperimentUpdate(experiment.state, experiment.detail, experiment.progress))
                    if kind == "done":
                        self._start_acquisition()
                        self.toast(f"{experiment.detail}. Diagnostic: {path}", "warning")
                    else:
                        try:
                            if self.recorder.active:
                                save_partial([], "error")
                        except Exception as exc:
                            experiment.detail += f"; recording finalization failed: {exc}"
                        finally:
                            try:
                                self.backend.disconnect()
                            except Exception as exc:
                                experiment.detail += f"; disconnect failed: {exc}"
                            self._set_connection_ui(False)
                        self.show_error(f"{experiment.detail}\nNo further return was attempted.\nDiagnostic: {path}")
                    self._sync_action_states()
                    self.poll_timer.start()
                    return
        timer.timeout.connect(poll)
        timer.start(50)
        threading.Thread(target=work, name="Experimental stop return", daemon=True).start()

    def emergency_stop(self) -> None:
        """Abort a running stop trial, or use production emergency handling."""
        if self._stop_trial is not None:
            self._stop_trial.set()
        else:
            super().emergency_stop()

    def closeEvent(self, event) -> None:
        """Request emergency stopping before closing a window with an active trial."""
        if self._stop_trial is not None:
            self._stop_trial.set()
            event.ignore()
        else:
            super().closeEvent(event)


def main(argv=None) -> int:
    """Launch the isolated test GUI using an explicitly supplied settings copy."""
    parser = argparse.ArgumentParser(description="Opt-in experimental Stop and initial-Z return GUI")
    parser.add_argument("--settings", type=Path, required=True, help="Private copy of settings, never the production defaults")
    args = parser.parse_args(argv)
    if not args.settings.is_file():
        parser.error("Create the isolated settings copy described in STOP_RECOVERY_TESTING.md first")
    os.environ["ECHEMTIPS_SETTINGS_PATH"] = str(args.settings.resolve())
    app = create_application()
    window = ExperimentalStopApp()
    QtWidgets.QMessageBox.warning(
        window, "Experimental hardware behaviour",
        "Stop experiment in this launcher attempts unvalidated FPGA recovery and a slow Z return. "
        "Complete STOP_RECOVERY_TESTING.md with outputs disconnected first. "
        "Keep the probe clear of the surface. Emergency Stop never starts a return.",
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
