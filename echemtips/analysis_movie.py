"""Responsive map movies and cancellable MP4 export; never touches source files."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import threading
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
from .analysis_core import AnalysisError
from .analysis_frames import prepare_frames, leg_labels
from .qt_common import Heatmap, button, label

def ffmpeg_executable():
    """Prefer an installed FFmpeg, then the optional portable wheel."""
    executable=shutil.which("ffmpeg")
    if executable: return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError,RuntimeError):
        raise AnalysisError('MP4 export needs FFmpeg. Install with: python -m pip install "echemtips[movies]" (or pip install imageio-ffmpeg).')

def render_frame(frames, index, palette, limits, width=960, height=720):
    """Render an independent QImage with physical coordinates and a labelled scale.

    QImage/QPainter are reentrant; no GUI widget is touched by the encoder thread.
    """
    image=QtGui.QImage(width,height,QtGui.QImage.Format.Format_RGB888); image.fill(QtGui.QColor("white"))
    p=QtGui.QPainter(image); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(QtGui.QColor("#18323e")); p.setFont(QtGui.QFont("Arial",16))
    unit="V" if frames.recipe["kind"]=="CV potential" else "s"
    cycle=frames.recipe["cycle"]
    p.drawText(40,38,f'eChemTips · {frames.recipe["kind"]} · {frames.axis[index]:.4g} {unit}')
    p.setFont(QtGui.QFont("Arial",11))
    p.drawText(40,64,f'Current {frames.recipe["channel"][7]} (nA) · cycle {cycle if cycle is not None else "average"} · {frames.recipe["polarity"]}')
    if frames.recipe["kind"]=="CV potential":
        p.drawText(40,86,leg_labels_from_recipe(frames))
    xs=sorted({v[0] for v in frames.coordinates.values()}); ys=sorted({v[1] for v in frames.coordinates.values()})
    dx=min(np.diff(xs)) if len(xs)>1 else 1.; dy=min(np.diff(ys)) if len(ys)>1 else 1.
    xmin,xmax=xs[0]-dx/2,xs[-1]+dx/2; ymin,ymax=ys[0]-dy/2,ys[-1]+dy/2
    scale=min(650/(xmax-xmin),480/(ymax-ymin)); w=(xmax-xmin)*scale; h=(ymax-ymin)*scale
    left=85+(650-w)/2; top=115+(480-h)/2
    lut=pg.colormap.get(palette).getLookupTable(nPts=256,alpha=False)
    low,high=limits
    p.fillRect(QtCore.QRectF(left,top,w,h),QtGui.QColor("#e6eaec"))
    for point in frames.points(index):
        idx=int(np.clip((point["value"]-low)/(high-low)*255,0,255))
        color=QtGui.QColor(*[int(v) for v in lut[idx][:3]])
        x=left+(point["x_um"]-dx/2-xmin)*scale
        y=top+(ymax-point["y_um"]-dy/2)*scale
        p.fillRect(QtCore.QRectF(x,y,dx*scale,dy*scale),color)
    p.setPen(QtGui.QColor("#18323e")); p.drawRect(QtCore.QRectF(left,top,w,h))
    for fraction in (0.,.5,1.):
        p.drawText(QtCore.QPointF(left+fraction*w-14,top+h+23),f"{xmin+fraction*(xmax-xmin):.3g}")
        p.drawText(QtCore.QPointF(left-60,top+(1-fraction)*h+4),f"{ymin+fraction*(ymax-ymin):.3g}")
    p.drawText(QtCore.QPointF(left+w/2-35,top+h+50),"X (µm)")
    p.drawText(12,100,"Y (µm)")
    for i in range(256):
        p.fillRect(QtCore.QRectF(790,150+(255-i)*1.5,22,1.6),QtGui.QColor(*[int(v) for v in lut[i][:3]]))
    p.drawText(825,158,f"{high:.4g}"); p.drawText(825,535,f"{low:.4g}"); p.drawText(785,130,"i (nA)")
    p.drawText(40,height-38,f"Frame {index+1}/{len(frames.axis)} · grey = missing/excluded · omitted hops: {frames.omitted}")
    p.end()
    return np.frombuffer(image.constBits(),dtype=np.uint8).reshape(height,image.bytesPerLine())[:,:width*3].copy().tobytes()

def leg_labels_from_recipe(frames):
    """Describe the selected chronological CV leg for the exported caption."""
    from .analysis_frames import LEG_NAMES
    return LEG_NAMES[frames.recipe["leg"]]

def export_movie(frames, path, *, fps=20, hold_ms=100, palette="viridis", mode="Auto",
                 manual=None, cancelled=lambda:False, progress=lambda x:None):
    """Stream bounded RGB frames to FFmpeg; atomically publish only successful MP4s."""
    executable=ffmpeg_executable(); path=Path(path)
    if not 1<=fps<=120 or not 1<=hold_ms<=10000: raise AnalysisError("Invalid movie timing")
    repeats=max(1,round(fps*hold_ms/1000)); fixed=frames.limits(mode,manual=manual) if mode!="Dynamic" else None
    handle,name=tempfile.mkstemp(prefix=".echemtips-movie-",suffix=".mp4",dir=path.parent); os.close(handle)
    process=None
    try:
        with tempfile.TemporaryFile() as errors:
            process=subprocess.Popen([executable,"-y","-loglevel","error","-f","rawvideo","-pix_fmt","rgb24",
                "-s","960x720","-r",str(fps),"-i","-","-an","-c:v","libx264","-pix_fmt","yuv420p",
                "-movflags","+faststart",name],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            for index in range(len(frames.axis)):
                if cancelled(): raise AnalysisError("Movie export cancelled")
                data=render_frame(frames,index,palette,fixed or frames.limits("Dynamic",index))
                for _ in range(repeats):
                    if cancelled(): raise AnalysisError("Movie export cancelled")
                    process.stdin.write(data)
                progress(round(100*(index+1)/len(frames.axis)))
            process.stdin.close()
            while process.poll() is None:
                if cancelled(): raise AnalysisError("Movie export cancelled")
                try: process.wait(timeout=.1)
                except subprocess.TimeoutExpired: pass
            if process.returncode:
                errors.seek(0); raise AnalysisError("FFmpeg: "+errors.read().decode(errors="replace")[-1500:])
        if cancelled(): raise AnalysisError("Movie export cancelled")
        os.replace(name,path)
        recipe={**frames.recipe,"axis":frames.axis.tolist(),"fps":fps,"hold_ms":repeats/fps*1000,
                "palette":palette,"colour_mode":mode,"fixed_limits_na":fixed,"robust_auto":"8 scaled MAD; 1st–99th percentiles"}
        # Use a movie-specific sidecar, never a recording's existing JSON.
        path.with_suffix(".mp4.json").write_text(json.dumps(recipe,indent=2)+"\n",encoding="utf-8")
    finally:
        if process is not None and process.poll() is None:
            process.kill(); process.wait()
        if process is not None and process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if os.path.exists(name): os.unlink(name)

class Signals(QtCore.QObject):
    """Thread-safe worker completion and encoding-progress notifications."""
    done=QtCore.Signal(int,object,str)
    progress=QtCore.Signal(int)

class Task(QtCore.QRunnable):
    """Cooperatively cancellable worker; result installation stays on the UI thread."""
    def __init__(self,token,function):
        super().__init__(); self.token=token; self.function=function; self.signals=Signals(); self.cancel=threading.Event()
    def run(self):
        """Execute work off-thread and emit exactly one terminal result."""
        try: self.signals.done.emit(self.token,self.function(self),"")
        except Exception as exc: self.signals.done.emit(self.token,None,str(exc))

class MoviePanel(QtWidgets.QWidget):
    """Prepare once, scrub/play cheaply, and export without blocking acquisition/UI."""
    def __init__(self):
        super().__init__(); self.dataset=None; self.frames=None; self.tasks={}; self.token=0; self.exporting=False
        layout=QtWidgets.QVBoxLayout(self); controls=QtWidgets.QGridLayout(); layout.addLayout(controls)
        def combo(items):
            """Create a labelled-choice control with the supplied options."""
            w=QtWidgets.QComboBox(); w.addItems(items); return w
        def spin(low,high,value):
            """Create an integer control with explicit bounds and initial value."""
            w=QtWidgets.QSpinBox(); w.setRange(low,high); w.setValue(value); return w
        self.kind=combo(("CV potential","CV time","I–t time")); self.channel=combo(("current1_na","current2_na"))
        self.cycle=combo(("Cycle 1",)); self.leg=combo(leg_labels(type("D",(),{"metadata":{}})()))
        self.count=spin(2,2000,120); self.stride=spin(1,100,1)
        self.fps=spin(1,120,20); self.hold=spin(1,10000,100); self.hold.setSuffix(" ms")
        self.colour=combo(("Auto","Dynamic","Manual")); self.palette=combo(("viridis","plasma","cividis","inferno"))
        self.low=QtWidgets.QDoubleSpinBox(); self.high=QtWidgets.QDoubleSpinBox()
        for w,value in ((self.low,-1),(self.high,1)):
            w.setRange(-1e9,1e9); w.setDecimals(6); w.setValue(value); w.setSuffix(" nA"); w.setKeyboardTracking(False)
        self.excluded=QtWidgets.QLineEdit(); self.excluded.setPlaceholderText("e.g. 2, 5 (hop numbers)")
        self.options=QtWidgets.QDialog(self); self.options.setWindowTitle("Movie playback and colour options")
        advanced=QtWidgets.QGridLayout(self.options)
        self.excluded.setToolTip("Exclude known failed landings using the displayed one-based hop numbers. Exclusions affect frames and automatic colour limits, never source data.")
        self.colour.setToolTip("Auto: fixed robust limits across all prepared frames. Dynamic: each frame's finite minimum/maximum. Manual: entered nA limits. Outlier clipping changes only colour limits, not values.")
        self.stride.setToolTip("Use every Nth frame from the requested frame grid.")
        items=(("Frame axis",self.kind),("Current",self.channel),("Cycle per hop",self.cycle),("CV segment",self.leg),
               ("Frames",self.count),("Every Nth frame",self.stride),("Output FPS",self.fps),("Hold per frame",self.hold),
               ("Colour limits",self.colour),("Palette",self.palette),("Minimum",self.low),("Maximum",self.high),("Exclude hops",self.excluded))
        for index,(title,w) in enumerate(items):
            if isinstance(w,QtWidgets.QComboBox):
                w.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                w.setMinimumContentsLength(12)
            cell=QtWidgets.QWidget(); box=QtWidgets.QVBoxLayout(cell); box.setContentsMargins(0,0,0,0); box.setSpacing(2)
            box.addWidget(label(title,"muted")); box.addWidget(w)
            if index<6: controls.addWidget(cell,index//4,index%4)
            else: advanced.addWidget(cell,(index-6)//4,(index-6)%4)
        self.prepare=button("Prepare frames",self.build,"primary"); controls.addWidget(self.prepare,1,2)
        self.options_toggle=button("Movie options…",self.options.open); controls.addWidget(self.options_toggle,1,3)
        advanced.addWidget(button("Done",self.options.accept,"primary"),2,3)
        self.options.hide()
        self.notice=label("Open a scan recording, select a cycle/segment, then prepare frames.","muted",word_wrap=True); layout.addWidget(self.notice)
        self.map=Heatmap("nA","Current"); self.map.setMinimumHeight(250); layout.addWidget(self.map,1)
        bar=QtWidgets.QHBoxLayout(); layout.addLayout(bar)
        self.play=button("Play",self.toggle); bar.addWidget(self.play)
        self.slider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); bar.addWidget(self.slider,1)
        self.loop=QtWidgets.QCheckBox("Loop"); self.loop.setChecked(True); bar.addWidget(self.loop)
        self.save=button("Save MP4…",self.save_movie); bar.addWidget(self.save)
        self.cancel_button=button("Cancel",self.cancel_tasks); bar.addWidget(self.cancel_button)
        self.progress=QtWidgets.QProgressBar(); self.progress.setMaximumHeight(12); self.progress.setTextVisible(False); layout.addWidget(self.progress)
        self.timer=QtCore.QTimer(self); self.timer.timeout.connect(self.advance); self.slider.valueChanged.connect(self.show_frame)
        for w in (self.kind,self.channel,self.cycle,self.leg): w.currentIndexChanged.connect(self.invalidate)
        for w in (self.count,self.stride): w.valueChanged.connect(self.invalidate)
        self.excluded.textChanged.connect(self.invalidate)
        for w in (self.colour,self.palette): w.currentIndexChanged.connect(self.show_frame)
        for w in (self.low,self.high,self.fps,self.hold): w.valueChanged.connect(self.show_frame)
        self.invalidate()

    def cancel_tasks(self):
        """Request cooperative cancellation and stop preview playback."""
        for task in self.tasks.values(): task.cancel.set()
        self.timer.stop(); self.play.setText("Play")

    def shutdown(self):
        """Cancel pending work when the parent analysis window closes."""
        self.cancel_tasks()

    def invalidate(self,*args):
        """Discard stale prepared frames after a scientific selection changes."""
        self.cancel_tasks(); self.token+=1; self.frames=None
        self.progress.setRange(0,100); self.progress.setValue(0)
        self.play.setEnabled(False); self.save.setEnabled(False)
        self.cycle.setEnabled(self.kind.currentText().startswith("CV")); self.leg.setEnabled(self.kind.currentText()=="CV potential")
        self.notice.setText("Selection changed — prepare frames to update the movie.")

    def set_dataset(self,dataset,groups):
        """Install new data and rebuild current, cycle and segment choices."""
        self.invalidate(); self.dataset=dataset; self.groups=groups
        for w in (self.channel,self.cycle,self.leg): w.blockSignals(True); w.clear()
        for c in ("current1_na","current2_na"):
            if c in dataset.columns: self.channel.addItem("Current "+c[7],c)
        for number in sorted({s.cycle for s in groups.get("cv",[])}): self.cycle.addItem(f"Cycle {number}",number)
        self.cycle.addItem("Average complete cycles",None); self.leg.addItems(leg_labels(dataset))
        for index in range(self.leg.count()): self.leg.setItemData(index,self.leg.itemText(index),QtCore.Qt.ItemDataRole.ToolTipRole)
        for w in (self.channel,self.cycle,self.leg): w.blockSignals(False)
        self.kind.setCurrentText("CV potential" if groups.get("cv") else "I–t time")

    def launch(self,function,callback):
        """Retain a worker until its token-tagged result reaches the GUI."""
        self.token+=1; token=self.token; task=Task(token,function); self.tasks[token]=task
        task.signals.done.connect(callback); task.signals.progress.connect(self.progress.setValue)
        QtCore.QThreadPool.globalInstance().start(task)

    def build(self):
        """Snapshot selections and prepare bounded frames outside the GUI thread."""
        if self.dataset is None: return
        self.invalidate()
        try:
            excluded=[int(x.strip())-1 for x in self.excluded.text().split(",") if x.strip()]
            if any(x<0 for x in excluded): raise ValueError
        except ValueError:
            self.notice.setText("Enter positive hop numbers separated by commas."); return
        dataset=self.dataset; groups=self.groups.get("cv" if self.kind.currentText().startswith("CV") else "hops",[])
        kwargs=dict(channel=self.channel.currentData(),kind=self.kind.currentText(),cycle=self.cycle.currentData(),
                    leg=self.leg.currentIndex(),count=self.count.value(),stride=self.stride.value(),excluded=excluded)
        self.progress.setRange(0,0); self.notice.setText("Preparing validated surface frames…")
        self.launch(lambda task:prepare_frames(dataset,groups,**kwargs,cancelled=task.cancel.is_set),self.built)

    def built(self,token,frames,error):
        """Install only the current preparation result, ignoring stale workers."""
        self.tasks.pop(token,None)
        if token!=self.token: return
        self.progress.setRange(0,100); self.progress.setValue(0)
        if error: self.notice.setText(error); return
        self.frames=frames; self.slider.setRange(0,len(frames.axis)-1); self.slider.setValue(0)
        self.play.setEnabled(True); self.save.setEnabled(True); self.show_frame()

    def show_frame(self,*args):
        """Render the selected frame using cached automatic limits and chosen timing."""
        manual=self.colour.currentText()=="Manual"; self.low.setEnabled(manual); self.high.setEnabled(manual)
        repeats=max(1,round(self.fps.value()*self.hold.value()/1000)); interval=round(repeats/self.fps.value()*1000)
        self.timer.setInterval(interval)
        if self.frames is None: return
        index=self.slider.value(); frames=self.frames
        try:
            limits=(frames.auto_limits if self.colour.currentText()=="Auto" and hasattr(frames,"auto_limits")
                    else frames.limits(self.colour.currentText(),index,(self.low.value(),self.high.value())))
        except AnalysisError as exc: self.notice.setText(str(exc)); self.timer.stop(); return
        # Auto limits are cached after preparation, avoiding a whole-movie scan on playback.
        if self.colour.currentText()=="Auto":
            if not hasattr(frames,"auto_limits"): frames.auto_limits=limits
            limits=frames.auto_limits
        points=frames.points(index); xs=sorted({v[0] for v in frames.coordinates.values()}); ys=sorted({v[1] for v in frames.coordinates.values()})
        xi={x:i for i,x in enumerate(xs)}; yi={y:i for i,y in enumerate(ys)}
        self.map.fixed_limits=limits; self.map.colormap_name=self.palette.currentText()
        self.map.quantity="Current "+frames.recipe["channel"][7]
        self.map.set_data({(yi[p["y_um"]],xi[p["x_um"]]):p["value"] for p in points},len(ys),len(xs),x_values=xs,y_values=ys)
        unit="V" if frames.recipe["kind"]=="CV potential" else "s from surface-program start" if frames.recipe["kind"]=="I–t time" else "s from cycle start"
        self.notice.setText(f"Frame {index+1}/{len(frames.axis)} · {frames.axis[index]:.5g} {unit} · {len(points)} valid hops · {frames.omitted} omitted · {interval} ms/frame. Auto limits suppress extreme outliers; values are unchanged.")

    def toggle(self):
        """Toggle preview playback without modifying prepared samples."""
        if self.timer.isActive(): self.timer.stop(); self.play.setText("Play")
        elif self.frames is not None: self.timer.start(); self.play.setText("Pause")

    def advance(self):
        """Move one frame forward, looping or stopping at the end."""
        value=self.slider.value()+1
        if value>self.slider.maximum():
            if not self.loop.isChecked(): self.timer.stop(); self.play.setText("Play"); return
            value=0
        self.slider.setValue(value)

    def save_movie(self):
        """Choose an output path and launch background MP4 encoding."""
        if self.frames is None or self.exporting: return
        try:
            ffmpeg_executable(); self.frames.limits(self.colour.currentText(),manual=(self.low.value(),self.high.value()))
        except AnalysisError as exc: self.notice.setText(str(exc)); return
        name,_=QtWidgets.QFileDialog.getSaveFileName(self,"Save map movie",str(self.dataset.path.with_suffix(".mp4")),"MP4 movie (*.mp4)")
        if not name: return
        path=Path(name).with_suffix(".mp4"); frames=self.frames
        kwargs=dict(fps=self.fps.value(),hold_ms=self.hold.value(),palette=self.palette.currentText(),mode=self.colour.currentText(),manual=(self.low.value(),self.high.value()))
        self.exporting=True; self.save.setEnabled(False); self.progress.setRange(0,100); self.progress.setValue(0)
        self.notice.setText("Encoding MP4 in the background…")
        def export(task):
            """Encode the immutable selection snapshot and return its destination."""
            export_movie(frames,path,**kwargs,cancelled=task.cancel.is_set,progress=task.signals.progress.emit)
            return str(path)
        self.launch(export,self.exported)

    def exported(self,token,path,error):
        """Report encoding completion and restore the export action."""
        self.tasks.pop(token,None); self.exporting=False
        self.save.setEnabled(self.frames is not None)
        if token==self.token: self.notice.setText(error or f"Saved {path} and movie recipe.")
