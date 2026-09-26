"""Dedicated experimental EIS page, isolated from standard experiment controls."""
import numpy as np
import pyqtgraph as pg
from PySide6 import QtWidgets
from .eis import EISParameters
from .experiments import ExperimentState
from .qt_common import Card, Field, Choice, Plot, PlotPanel, COLORS, label


def create_eis_page(app):
    """Build the EIS page after shared control widgets have been defined."""
    from .ui import ManagedExperimentPage, _vbox, _grid, add_field, _left_scroll, _plot_card

    class EISPage(ManagedExperimentPage):
        """Contact-gated EIS setup and nonblocking live spectral/time-domain views."""
        experiment_key="eis"
        recording_name="Approach + EIS"
        manual_approach=True

        def __init__(self):
            super().__init__(app,"Approach + EIS","Experimental · command-referenced apparent impedance")
            root=QtWidgets.QHBoxLayout(self.body)
            holder=QtWidgets.QWidget(); left=_vbox(holder)
            movement=Card("1 · Approach and return","")
            g=_grid(movement.body)
            self.start_z=add_field(g,Field("Initial Z","10","µm"),0,0)
            self.end_z=add_field(g,Field("Approach limit Z","30","µm"),0,1)
            self.speed=add_field(g,Field("Approach speed","3","µm/s"),1,0)
            self.retract=add_field(g,Field("Return speed","10","µm/s"),1,1)
            self.threshold=add_field(g,Field("Contact threshold |i|","5","pA"),2,0)
            self.contact=Choice(("Current 1","Current 2"),"Current 1");g.addWidget(self.contact,2,1)
            self.approach_v=add_field(g,Field("Approach potential E1","0.1","V"),3,0)
            self.settle=add_field(g,Field("Contact / DC settling","1","s"),3,1)
            left.addWidget(movement)
            electro=Card("2 · Stepped-sine program","")
            g=_grid(electro.body)
            self.dc=add_field(g,Field("DC potential E1","0.1","V"),0,0)
            self.amplitude=add_field(g,Field("AC amplitude (peak)","10","mV"),0,1)
            self.freq=add_field(g,Field("Frequencies (Hz, comma separated)","1, 3, 10"),1,0)
            self.channel=Choice(("Current 1","Current 2"),"Current 1");g.addWidget(self.channel,1,1)
            self.discard=add_field(g,Field("Settling cycles / frequency","2"),2,0)
            self.cycles=add_field(g,Field("Measured cycles / frequency","4"),2,1)
            left.addWidget(electro)
            note=label("Experimental: E1 is the applied command, not measured cell potential. "
                "Validate amplifier gain/phase and timing with an R/RC load. Simulation uses "
                "100 MΩ + (500 MΩ ∥ 100 pF). Z returns to initial Z on completion.","muted",word_wrap=True)
            left.addWidget(note);left.addStretch(1);root.addWidget(_left_scroll(holder,360))
            right=QtWidgets.QWidget();rl=_vbox(right)
            rl.addWidget(self.build_status("EIS status","Start approach + EIS"))
            self.tabs=QtWidgets.QTabWidget();rl.addWidget(self.tabs,1);root.addWidget(right,1)
            self.nyquist=self._spectral("Nyquist","Z′ (Ω)","−Z″ (Ω)")
            self.nyquist.setAspectLocked(True)
            self.tabs.addTab(self.nyquist,"Nyquist")
            bode=QtWidgets.QWidget();bl=_vbox(bode)
            self.mag=self._spectral("Magnitude","Frequency (Hz)","|Z| (Ω)");self.mag.setLogMode(x=True,y=True)
            self.phase=self._spectral("Phase","Frequency (Hz)","Phase (°)");self.phase.setLogMode(x=True)
            bl.addWidget(self.mag);bl.addWidget(self.phase);self.tabs.addTab(PlotPanel(bode),"Bode")
            raw=QtWidgets.QWidget();rawlayout=_vbox(raw)
            self.eplot=Plot("Applied potential","E1 (V)",(COLORS['accent'],),12000)
            self.iplot=Plot("Measured current","Current (nA)",(COLORS['blue'],),12000)
            self.zplot=Plot("Z position","Z (µm)",(COLORS['accent'],),12000)
            for title,plot in (("Potential",self.eplot),("Current",self.iplot),("Z",self.zplot)):
                rawlayout.addWidget(_plot_card(title,plot))
            self.tabs.addTab(PlotPanel(raw),"Experiment traces")
            self.aplot=Plot("Approach","Current (nA)",(COLORS['blue'],),12000,"Z (µm)")
            self.tabs.addTab(_plot_card("Approach curve",self.aplot),"Approach")
            self.table=QtWidgets.QTableWidget(0,5)
            self.table.setHorizontalHeaderLabels(["Hz","Z′ / MΩ","Z″ / MΩ","Phase / °","Quality"])
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            self.tabs.addTab(self.table,"Results")
            self._result_count=-1

        def _spectral(self,title,x,y):
            plot=pg.PlotWidget(title=title,background="w")
            for axis,caption in (("bottom",x),("left",y)):
                name,unit=caption.rsplit(" (",1)
                plot.setLabel(axis,name,units=unit.rstrip(")"))
            plot.getAxis("left").setWidth(78)
            plot.getAxis("bottom").setHeight(45)
            plot.showGrid(x=True,y=True,alpha=.15);plot.setMinimumHeight(200)
            return plot

        def parameters(self):
            """Parse EIS settings without exposing irrelevant potential-step fields."""
            return EISParameters(start_z_um=self.start_z.float(),end_z_um=self.end_z.float(),
                approach_rate_um_s=self.speed.float(),retract_rate_um_s=self.retract.float(),
                approach_voltage_v=self.approach_v.float(),feedback_threshold=self.threshold.float()/1000,
                feedback_channel=self.contact.get(),settling_time_s=self.settle.float(),
                dc_v=self.dc.float(),amplitude_v=self.amplitude.float()/1000,
                frequencies_hz=tuple(float(v.strip()) for v in self.freq.entry.text().split(",")),
                settle_cycles=self.discard.integer(),measure_cycles=self.cycles.integer(),
                current_channel=self.channel.get())

        def start(self):
            """Validate before recording or motion, then clear the previous spectrum."""
            try:
                p=self.parameters();errors=p.validate(app.settings)
                if errors: raise ValueError("\n".join(errors))
                if app.settings.mode=="NI FPGA":
                    answer=QtWidgets.QMessageBox.warning(self,"Experimental EIS",
                        "Validate first with a known electrical load and the pipette safely clear. "
                        "No amplifier phase correction is applied. Stop may require reconnection. Continue?",
                        QtWidgets.QMessageBox.StandardButton.Yes|QtWidgets.QMessageBox.StandardButton.Cancel)
                    if answer!=QtWidgets.QMessageBox.StandardButton.Yes:return
                self._begin(p)
                for plot in (self.eplot,self.iplot,self.zplot,self.aplot):plot.clear()
                self._result_count=-1;self._update_spectra()
            except (ValueError,RuntimeError,OSError) as exc:app.show_error(str(exc))

        def on_samples(self,samples):
            """Tag full-rate samples before recorder writes; refresh only bounded plots."""
            ex=self.experiment
            if not ex.active:
                if app.recorder.active and app.recorder.name==self.recording_name:ex.ingest(samples)
                self._update_spectra()
                return
            ex.ingest(samples)
            for s in samples:
                current=s.current1_na if ex.params.current_channel=="Current 1" else s.current2_na
                self.eplot.append(s.elapsed_s,s.voltage1_v,redraw=False)
                self.iplot.append(s.elapsed_s,current,redraw=False)
                self.zplot.append(s.elapsed_s,s.z_um,redraw=False)
                if ex.state==ExperimentState.APPROACHING:self.aplot.append(s.z_um,current,redraw=False)
            update=ex.tick_samples(samples)
            self._show_update(update)
            if ex.state==ExperimentState.IT:self.state_label.setText("Measuring EIS")
            for plot in (self.eplot,self.iplot,self.zplot,self.aplot):plot.request_redraw()
            self._update_spectra()

        def _update_spectra(self):
            results=self.experiment.params.eis_results
            if len(results)==self._result_count:return
            self._result_count=len(results)
            valid=sorted((r for r in results if "error" not in r),key=lambda r:r['frequency_hz'])
            for plot,x,y in ((self.nyquist,[r['z_real_ohm'] for r in valid],[-r['z_imag_ohm'] for r in valid]),
                             (self.mag,[r['frequency_hz'] for r in valid],[r['magnitude_ohm'] for r in valid]),
                             (self.phase,[r['frequency_hz'] for r in valid],[r['phase_deg'] for r in valid])):
                plot.clear();plot.plot(x,y,pen=pg.mkPen(COLORS['accent'],width=2),symbol='o',symbolSize=7)
            self.table.setRowCount(len(results))
            for row,r in enumerate(results):
                values=([str(r['frequency_index']),"—","—","—",r['error']] if 'error' in r else
                    [f"{r['frequency_hz']:.4g}",f"{r['z_real_ohm']/1e6:.4g}",f"{r['z_imag_ohm']/1e6:.4g}",
                     f"{r['phase_deg']:.2f}","; ".join(r['warnings']) or "Fit OK; uncalibrated"])
                for col,value in enumerate(values):self.table.setItem(row,col,QtWidgets.QTableWidgetItem(value))
    return EISPage()
