"""Standalone Z-picomotor commissioning UI; not part of the control-app sidebar."""
from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import replace
from pathlib import Path
import queue
import threading
import time

from PySide6 import QtCore, QtWidgets as Q
import pyqtgraph as pg

from .backends import NIFPGABackend
from .coarse_approach import CoarseApproach, CoarseApproachParameters, CoarseSimulationBackend
from .data import DataRecorder
from .models import ApproachParameters, SettingsStore
from .picomotor import NewportUSB, PicomotorZ, SimulatedPicomotor
from .qt_common import Field, Choice, Check, Card, label, scroll_area
from .ui import create_application


class TestWorker(QtCore.QThread):
    """Own both devices on one thread; USB/FIFO I/O never blocks the GUI thread."""
    updated = QtCore.Signal(object)

    def __init__(self,settings,hardware,dll,key,parent=None):
        super().__init__(parent)
        self.settings,self.hardware,self.dll,self.key=settings,hardware,dll,key
        self.commands=queue.Queue()
        self.stop_requested=threading.Event()
        self.shutdown=threading.Event()

    def run(self):
        """Connect once, serialize commands, record all samples, publish bounded plots."""
        backend=(NIFPGABackend(self.settings) if self.hardware else CoarseSimulationBackend(self.settings))
        motor=(PicomotorZ(NewportUSB(self.dll,self.key)) if self.hardware else SimulatedPicomotor(backend))
        supervisor=CoarseApproach(backend,motor,self.settings)
        recorder=DataRecorder()
        def journal(record):
            """Persist each motion decision before transmitting the motor command."""
            if recorder.output_path is not None:
                with recorder.output_path.with_suffix('.motion.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(record,allow_nan=False)+'\n')
        supervisor.event_sink=journal
        identity=""
        jog_deadline=None
        connected=False
        last_emit=0.
        plot_samples=[]
        try:
            identity=motor.connect()
            backend.connect(allow_startup_actuation=self.hardware)
            connected=True
            while not self.shutdown.is_set():
                if self.stop_requested.is_set():
                    supervisor.stop()
                    self.stop_requested.clear()
                    jog_deadline=None
                    if recorder.active:
                        recorder.finish(self.settings,supervisor.params,status="aborted")
                    self.updated.emit(dict(connected=True,detail=supervisor.detail,active=False,phase=supervisor.phase,stopped=True))
                    if self.hardware:
                        # NI main-branch emergency stop is deliberately latched.
                        break
                try: command,value=self.commands.get_nowait()
                except queue.Empty: command=None
                if command=="start":
                    if jog_deadline is not None: raise RuntimeError("Wait for the manual motor move to finish.")
                    value.motor_identity=identity
                    errors=value.validate(self.settings)
                    if errors: raise ValueError("\n".join(errors))
                    recorder.start("Piezo + picomotor Z commissioning",self.settings,value)
                    supervisor.start(value)
                elif command=="jog":
                    if supervisor.active or jog_deadline is not None:
                        raise RuntimeError("Manual motion is disabled while a move or approach is active.")
                    steps,rate=value
                    motor.move(steps,rate)
                    jog_deadline=time.monotonic()+abs(steps)/rate+15
                samples=backend.read_samples()
                for sample in samples: recorder.append(sample)
                plot_samples.extend(samples)
                plot_samples=plot_samples[-500:]
                if supervisor.active: supervisor.tick(samples)
                if jog_deadline is not None:
                    if motor.motion_done():
                        motor.check_error(); jog_deadline=None
                    elif time.monotonic()>jog_deadline:
                        raise TimeoutError("Manual motor move timed out.")
                if recorder.active and not supervisor.active:
                    recorder.finish(self.settings,supervisor.params,status="complete" if supervisor.phase=="complete" else "aborted")
                if time.monotonic()-last_emit >= .1:
                    self.updated.emit(dict(connected=True,identity=identity,detail=supervisor.detail,
                        active=supervisor.active or jog_deadline is not None,phase=supervisor.phase,
                        count=motor.position(),attempts=supervisor.attempts,steps=supervisor.total_steps,
                        samples=plot_samples,output=str(recorder.output_path or "")))
                    plot_samples=[]; last_emit=time.monotonic()
                self.msleep(10)
        except Exception as exc:
            supervisor.stop(str(exc))
            try:
                if recorder.active: recorder.finish(self.settings,supervisor.params,status="error")
            except Exception as record_error:
                supervisor.detail+=f"; saving failed: {record_error}"
            self.updated.emit(dict(error=supervisor.detail,active=False,connected=False))
        finally:
            # On close, also stop an active manual move. A communication failure
            # cannot guarantee either device stopped; report it, never hide it.
            if connected and (supervisor.active or jog_deadline is not None):
                supervisor.stop("Tester closing; stopping both devices")
                self.updated.emit(dict(detail=supervisor.detail,active=False))
            try:
                if recorder.active: recorder.finish(self.settings,supervisor.params,status="aborted")
            except Exception as exc: self.updated.emit(dict(error=f"Recording finalization failed: {exc}"))
            for device in (backend,motor):
                try: device.disconnect() if device is backend else device.close()
                except Exception as exc: self.updated.emit(dict(error=f"Disconnect failed: {exc}"))


class PicomotorTestWindow(Q.QMainWindow):
    """Clean standalone tester with calibration separated from automatic approach."""

    def __init__(self,settings,hardware=False,dll="",key=""):
        super().__init__()
        self.settings,self.hardware=settings,hardware
        self.worker=None
        self.is_connected=False
        self.setWindowTitle("eChemTips — Z Picomotor Commissioning" + (" · HARDWARE" if hardware else " · SIMULATION"))
        self.resize(1200,800)
        root=Q.QWidget(); layout=Q.QVBoxLayout(root); self.setCentralWidget(root)
        layout.addWidget(label("Piezo + Z picomotor test","title"))
        layout.addWidget(label("Experimental standalone tester · X=1, Y=2 inactive · Z=3 · No FPGA bitfile changes",word_wrap=True))
        bar=Q.QHBoxLayout(); layout.addLayout(bar)
        self.connect_button=Q.QPushButton("Connect devices"); self.connect_button.clicked.connect(self.connect_devices); bar.addWidget(self.connect_button)
        self.start_button=Q.QPushButton("Start repeated approach"); self.start_button.setEnabled(False); self.start_button.clicked.connect(self.start_run); bar.addWidget(self.start_button)
        stop=Q.QPushButton("STOP BOTH DEVICES"); stop.setObjectName("dangerButton"); stop.clicked.connect(self.stop); bar.addWidget(stop)
        self.status=label("Not connected","muted",word_wrap=True); layout.addWidget(self.status)
        self.counter=label("Motor position: pulse count only, not measured distance","muted",word_wrap=True); layout.addWidget(self.counter)
        splitter=Q.QSplitter(); layout.addWidget(splitter,1)
        setup=Q.QWidget(); form=Q.QVBoxLayout(setup)
        self.setup=setup
        self.fields={}
        tabs=Q.QTabWidget(); form.addWidget(tabs)
        connection=Q.QWidget(); cg=Q.QVBoxLayout(connection)
        self.dll=Field("Newport USB Driver Bin directory",dll,""); cg.addWidget(self.dll)
        self.device_key=Field("Device key (blank only if one USB device)",key,""); cg.addWidget(self.device_key)
        cg.addWidget(label(f"NI settings loaded: {settings.resource}\nBitfile: {settings.bitfile or '(not selected)'}\nPiezo ranges: {settings.x_range_um:g}, {settings.y_range_um:g}, {settings.z_range_um:g} µm\nCurrent gains: {settings.current1_v_per_na:g}, {settings.current2_v_per_na:g} V/nA\nPolarity: {settings.polarity_convention}\nAcquisition: {settings.sample_time_us} µs × {settings.samples_per_point}\nOutput: {settings.save_directory}",word_wrap=True))
        cg.addWidget(label("Loads existing eChemTips settings without changing them. Close eChemTips/LabVIEW/Newport applications before connecting hardware. FPGA startup changes outputs; position the probe safely clear first.",word_wrap=True)); cg.addStretch()
        tabs.addTab(connection,"Connection")
        calibration=Q.QWidget(); g=Q.QVBoxLayout(calibration)
        self.manual_clear=Check("Probe safely clear; manual step test authorized",False); g.addWidget(self.manual_clear)
        self.manual_steps=Q.QSpinBox(); self.manual_steps.setRange(1,50); self.manual_steps.setValue(1)
        g.addWidget(label("Manual steps (start at 1)","muted")); g.addWidget(self.manual_steps)
        jogrow=Q.QHBoxLayout(); g.addLayout(jogrow)
        for sign in (-1,1):
            b=Q.QPushButton(f"Z {'−' if sign<0 else '+'} steps"); b.clicked.connect(lambda _,s=sign:self.jog(s)); jogrow.addWidget(b)
        self.direction=Choice(["Not verified","Positive steps approach","Negative steps approach"],"Positive steps approach" if not hardware else "Not verified")
        g.addWidget(label("Coarse direction toward surface","muted")); g.addWidget(self.direction)
        self._field(g,"upper_um_per_step","Upper displacement per step (calibrated bound)","0" if hardware else ".01","µm/step")
        self.direction_ok=Check("Direction verified on this assembly",not hardware); g.addWidget(self.direction_ok)
        self.calibration_ok=Check("Displacement bound verified under actual load",not hardware); g.addWidget(self.calibration_ok)
        g.addWidget(label("The 8742 has no encoder. Use a camera/displacement measurement for calibration. A pulse count is NOT an actuator position. Simulation uses +0.01 µm/step only as a synthetic example.",word_wrap=True)); g.addStretch()
        tabs.addTab(calibration,"Calibration")
        approach=Q.QWidget(); ag=Q.QVBoxLayout(approach)
        for name,title,value,unit in (("start_z","Initial piezo Z",10,"µm"),("end_z","Approach limit Z",80,"µm"),
            ("approach_speed","Piezo approach speed",15,"µm/s"),("retract_speed","Piezo retract speed",15,"µm/s"),
            ("potential","Approach potential E1",.1,"V"),("threshold","Contact current magnitude",5,"pA"),
            ("steps_per_move","Coarse steps per retry",500,"steps"),("rate_steps_s","Motor speed",500,"steps/s"),
            ("max_coarse_steps","Maximum total coarse steps",2000,"steps"),("max_attempts","Maximum piezo attempts",10,""),
            ("max_duration_s","Maximum run time",300,"s"),("settling_s","Settling before each approach",.5,"s")):
            self._field(ag,name,title,value,unit)
        self.current=Choice(["Current 1","Current 2"],"Current 1"); ag.addWidget(self.current)
        self.clearance_ok=Check("Initial Z, coarse travel and direction verified safe",not hardware); ag.addWidget(self.clearance_ok)
        ag.addWidget(label("Each normal no-contact approach withdraws before coarse motion. Contact ends the run and returns piezo to initial Z. Motor stays at its final count; it does not automatically return home.",word_wrap=True))
        tabs.addTab(scroll_area(approach),"Approach")
        splitter.addWidget(setup)
        graphs=Q.QWidget(); gl=Q.QVBoxLayout(graphs)
        self.curves=[]
        for title,y in (("Measured current","Current (pA)"),("Piezo Z (measured and commanded)","Z (µm)")):
            plot=pg.PlotWidget(title=title); plot.setBackground("w"); plot.setLabel("left",y); plot.setLabel("bottom","Recording time","s"); plot.showGrid(x=True,y=True,alpha=.15)
            self.curves.append(plot.plot(pen=pg.mkPen("#008b83",width=1.5)))
            if len(self.curves)==2: self.commanded_curve=plot.plot(pen=pg.mkPen("#e08a18",width=1.5))
            gl.addWidget(plot)
        self.saved=label("All acquired samples are recorded; plots show a bounded recent history.","muted",word_wrap=True); gl.addWidget(self.saved)
        splitter.addWidget(graphs); splitter.setSizes([430,770])
        self.history=deque(maxlen=4000)
        self.plot_origin=None

    def _field(self,layout,name,title,value,unit):
        widget=Field(title,str(value),unit); self.fields[name]=widget; layout.addWidget(widget)

    def connect_devices(self):
        """Require explicit startup actuation consent before NI hardware connection."""
        if self.worker is not None:
            return
        if self.hardware and Q.QMessageBox.question(self,"Authorize hardware connection",
                "FPGA connection resets/runs the existing target and changes X/Y/Z and potentials immediately. Newport startup may perform motor detection steps. Close all other instrument apps and position the probe safely clear. Proceed?") != Q.QMessageBox.StandardButton.Yes:
            return
        self.worker=TestWorker(replace(self.settings,auto_save=True),self.hardware,self.dll.entry.text(),self.device_key.entry.text(),self)
        self.worker.updated.connect(self.update_status)
        self.worker.finished.connect(self.disconnected)
        self.connect_button.setEnabled(False)
        self.worker.start()

    def parameters(self):
        """Parse one immutable run draft; calibration confirmations are not persisted."""
        f=self.fields
        a=ApproachParameters(start_z_um=f['start_z'].float(),end_z_um=f['end_z'].float(),
            approach_rate_um_s=f['approach_speed'].float(),retract_rate_um_s=f['retract_speed'].float(),
            approach_voltage_v=f['potential'].float(),feedback_threshold=f['threshold'].float()/1000,
            feedback_channel=self.current.get(),feedback_mode="magnitude",retract_after=True,settling_time_s=.1)
        p=CoarseApproachParameters(approach=a,direction={0:0,1:1,2:-1}[self.direction.currentIndex()],
            upper_um_per_step=f['upper_um_per_step'].float(),direction_verified=self.direction_ok.get(),
            calibration_verified=self.calibration_ok.get(),clearance_verified=self.clearance_ok.get())
        for key in ('steps_per_move','rate_steps_s','max_coarse_steps','max_attempts'): setattr(p,key,f[key].integer())
        for key in ('max_duration_s','settling_s'): setattr(p,key,f[key].float())
        return p

    def start_run(self):
        """Validate commissioning confirmations and queue a recorded approach."""
        try:
            p=self.parameters()
            errors=p.validate(self.settings)
            if errors: raise ValueError("\n".join(errors))
            if not self.is_connected: raise RuntimeError("Connect both devices first.")
            self.history.clear(); self.plot_origin=None
            self.worker.commands.put(("start",p)); self.start_button.setEnabled(False); self.setup.setEnabled(False)
        except (ValueError,RuntimeError) as exc: Q.QMessageBox.warning(self,"Cannot start",str(exc))

    def jog(self,sign):
        """Allow only small finite calibration moves with explicit clearance consent."""
        if not self.is_connected or not self.manual_clear.get():
            Q.QMessageBox.warning(self,"Manual move blocked","Connect devices and confirm manual-movement clearance first.")
            return
        self.worker.commands.put(("jog",(sign*self.manual_steps.value(),100)))

    def stop(self):
        """Prioritize a stop flag over queued commands; stop remains always accessible."""
        if self.worker:
            self.worker.stop_requested.set()
            # Drain commands that were queued before Stop; never restart afterwards.
            while True:
                try: self.worker.commands.get_nowait()
                except queue.Empty: break
        self.start_button.setEnabled(False)

    def update_status(self,state):
        """Apply worker status and bounded plot updates on the GUI thread."""
        if 'connected' in state: self.is_connected=state['connected']
        if 'error' in state:
            self.status.setText(state['error']); self.start_button.setEnabled(False); self.setup.setEnabled(True)
            Q.QMessageBox.warning(self,"Tester stopped",state['error'])
            return
        if 'detail' in state: self.status.setText(state['detail'])
        self.start_button.setEnabled(self.is_connected and not state.get('active',False) and not state.get('stopped',False))
        self.setup.setEnabled(not state.get('active',False))
        if 'count' in state:
            self.counter.setText(f"{'HARDWARE' if self.hardware else 'SIMULATION'} · Z port 3 · pulse count {state['count']} (not measured µm) · attempts {state['attempts']} · coarse steps {state['steps']}")
        for sample in state.get('samples',[]):
            if self.plot_origin is None: self.plot_origin=sample.elapsed_s
            current=sample.current1_na if self.current.get()=='Current 1' else sample.current2_na
            self.history.append((sample.elapsed_s-self.plot_origin,current*1000,sample.z_um,sample.commanded_z_um))
        if self.history:
            t,i,z,c=zip(*self.history); self.curves[0].setData(t,i); self.curves[1].setData(t,z); self.commanded_curve.setData(t,c)
        if state.get('output'): self.saved.setText("Recording: "+state['output'])

    def disconnected(self):
        """Require a fresh connection after faults; never silently reset the FPGA."""
        self.is_connected=False
        self.start_button.setEnabled(False); self.connect_button.setEnabled(True)
        self.worker.deleteLater(); self.worker=None

    def closeEvent(self,event):
        """Wait asynchronously for device cleanup; never destroy a running QThread."""
        if self.worker is not None:
            self.worker.shutdown.set(); self.worker.stop_requested.set()
            event.ignore()
            self.worker.finished.connect(self.close)
            self.status.setText("Stopping and closing devices… if USB has failed, use physical stop/power controls.")
        else: event.accept()


def main(argv=None):
    """Launch in simulation unless --hardware is explicitly requested."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware',action='store_true')
    parser.add_argument('--settings',type=Path,help='Existing eChemTips settings JSON (never modified)')
    parser.add_argument('--dll-directory',default='')
    parser.add_argument('--device-key',default='')
    parser.add_argument('--bitfile'); parser.add_argument('--resource')
    args=parser.parse_args(argv)
    settings=SettingsStore(args.settings).load()
    settings.mode='NI FPGA' if args.hardware else 'Simulation'
    if args.bitfile: settings.bitfile=args.bitfile
    if args.resource: settings.resource=args.resource
    app=create_application([])
    window=PicomotorTestWindow(settings,args.hardware,args.dll_directory,args.device_key); window.show()
    return app.exec()


if __name__=='__main__':
    raise SystemExit(main())
