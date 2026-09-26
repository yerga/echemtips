"""Adaptive LSV controls and bounded-cost physical-coordinate maps."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .adaptive import AdaptiveParameters
from .qt_common import Card, Field, Choice, Check, Plot, PlotPanel, COLORS, button, label, scroll_area


class AdaptiveMap(QtWidgets.QWidget):
    """One readable spatial map with sequence and next-location overlays."""
    def __init__(self, title, unit="nA"):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        self.canvas = pg.GraphicsLayoutWidget()
        self.canvas.setBackground("w")
        self.plot = self.canvas.addPlot(row=0,col=0,title=title)
        self.plot.setLabel("bottom","X position",units="µm")
        self.plot.setLabel("left","Y position",units="µm")
        self.plot.setAspectLocked(True)
        self.plot.showGrid(x=True,y=True,alpha=.15)
        self.palette = pg.colormap.get("viridis")
        self.bar = pg.ColorBarItem(values=(0,1),colorMap=self.palette,label=unit,interactive=False,width=14)
        self.canvas.addItem(self.bar,row=0,col=1)
        self.scatter = pg.ScatterPlotItem(size=11,pen=None)
        self.path = self.plot.plot(pen=pg.mkPen('#96a6b3',width=1))
        self.survey_path = self.plot.plot(pen=pg.mkPen('#b59952',style=QtCore.Qt.PenStyle.DashLine),symbol='o',symbolSize=5)
        self.plot.addItem(self.scatter)
        self.proposal = pg.ScatterPlotItem(size=18,symbol='+',pen=pg.mkPen('#d9463e',width=3))
        self.plot.addItem(self.proposal)
        self.plot.setMinimumHeight(260)
        layout.addWidget(self.canvas,1)
        layout.addWidget(label("Dots: sampled/candidate locations · grey line: landing order · red +: proposed landing", "muted",word_wrap=True))

    def update_map(self, xy, values, attempts, proposal, params):
        """Redraw only after a landing/model result, not on every acquired sample."""
        xy,values = np.asarray(xy),np.asarray(values)
        if len(values):
            lo,hi = float(np.min(values)),float(np.max(values))
            if hi<=lo: hi=lo+max(abs(lo)*.05,.001)
            colors = self.palette.map((values-lo)/(hi-lo),mode='qcolor')
            self.scatter.setData(pos=xy,brush=[pg.mkBrush(c) for c in colors])
            self.bar.setLevels((lo,hi))
        else: self.scatter.setData([])
        path = np.array([a['xy'] for a in attempts])
        survey = params.survey_points()
        self.survey_path.setData(survey[:,0],survey[:,1]) if not attempts else self.survey_path.setData([],[])
        self.path.setData(path[:,0],path[:,1]) if len(path) else self.path.setData([],[])
        self.proposal.setData(pos=[proposal['xy']] if proposal and proposal.get('xy') is not None else [])
        self.plot.setRange(xRange=(params.x_min_um,params.x_max_um),yRange=(params.y_min_um,params.y_max_um),padding=.08)


def create_adaptive_page(app):
    """Construct a managed page lazily, avoiding a UI/module import cycle."""
    from .ui import ManagedExperimentPage, _left_scroll, _vbox, _plot_card

    class AdaptivePage(ManagedExperimentPage):
        """Guided configuration, explicit approvals and uncluttered plot tabs."""
        experiment_key = "adaptive"
        recording_name = "Adaptive hopping + LSV"

        def __init__(self, app):
            super().__init__(app,self.recording_name,"")
            self.fields = {}
            root = QtWidgets.QHBoxLayout(self.body)
            root.setContentsMargins(0,0,0,0)
            left = QtWidgets.QWidget(); setup = _vbox(left)
            specs = [
                ("1 · Region and survey", [
                    ("x_min_um","X minimum","20","µm"),("x_max_um","X maximum","80","µm"),
                    ("y_min_um","Y minimum","20","µm"),("y_max_um","Y maximum","80","µm"),
                    ("minimum_spacing_um","Minimum landing separation","5","µm")]),
                ("2 · Motion and contact", [
                    ("start_z_um","Initial / survey travel Z","10","µm"),("end_z_um","Approach limit Z","90","µm"),
                    ("approach_rate_um_s","Approach speed","3","µm/s"),("xy_speed_um_s","XY travel speed","20","µm/s"),
                    ("clearance_um","Travel clearance","10","µm"),("relief_allowance_um","Unresolved relief allowance","5","µm"),
                    ("plane_tolerance_um","Maximum plane deviation","3","µm"),("feedback_threshold_na","Contact threshold magnitude","5","pA"),
                    ("approach_voltage_v","Approach potential E1","0.1","V"),("settling_time_s","Settling after contact","0.5","s")]),
                ("3 · LSV and objective", [
                    ("cv_start_v","Start potential","-0.2","V"),("cv_vertex1_v","End potential","0.6","V"),
                    ("cv_scan_rate_v_s","Scan rate","0.25","V/s"),("objective_potential_v","Objective potential","0.2","V"),
                    ("objective_window_v","Objective window width","0.02","V")]),
                ("4 · Search and limits", [
                    ("max_landings","Maximum landings (including survey)","30",""),
                    ("max_duration_s","Maximum elapsed time","3600","s")]),
            ]
            for title, entries in specs:
                card = Card(title); grid = QtWidgets.QGridLayout(card.body)
                grid.setColumnStretch(0,1); grid.setColumnStretch(1,1)
                for n,(key,caption,value,unit) in enumerate(entries):
                    field = Field(caption,value,unit); self.fields[key]=field; grid.addWidget(field,n//2,n%2)
                if title.startswith('1'):
                    self.survey = Choice(['Corners + center','3 × 3'],'Corners + center')
                    grid.addWidget(self.survey,3,0,1,2)
                if title.startswith('2'):
                    self.feedback = Choice(['Current 1','Current 2'],'Current 1')
                    grid.addWidget(label('Contact feedback current','muted'),5,0)
                    grid.addWidget(self.feedback,5,1)
                    grid.addWidget(label('Decreasing Z retracts. Initial Z must clear the entire region and entry path. A sparse survey cannot detect hidden obstacles.','muted',word_wrap=True),6,0,1,2)
                if title.startswith('3'):
                    self.objective_channel = Choice(['Current 1','Current 2'],'Current 1')
                    grid.addWidget(self.objective_channel,3,0,1,2)
                    grid.addWidget(label('Objective = |median signed current| in this potential window. Both current signs contribute; individual noise spikes do not define the objective.','muted',word_wrap=True),4,0,1,2)
                if title.startswith('4'):
                    self.strategy = Choice(['Balanced','Hotspots','Mapping'],'Balanced')
                    self.strategy.setToolTip('Hotspots: mean + 2σ. Mapping: largest model uncertainty. Balanced: alternate these objectives.')
                    grid.addWidget(self.strategy,1,0,1,2)
                    self.approval = Check('Approve every proposed landing',True)
                    grid.addWidget(self.approval,2,0,1,2)
                    self.confirm = Check('Safe region and entry path verified',False)
                    self.confirm.setToolTip('Initial Z must be safely retracted across the entire selected region and the path from the current XY position. Piezo limits alone do not establish a safe sample region.')
                    grid.addWidget(self.confirm,3,0,1,2)
                    grid.addWidget(label('The tilt estimate always requires approval. Budgets include survey landings; a running landing finishes and retracts safely. Stop uses the existing FPGA stop and may require reconnection.','muted',word_wrap=True),4,0,1,2)
                setup.addWidget(card)
            setup.addStretch(1)
            root.addWidget(_left_scroll(left,380))
            right = QtWidgets.QWidget(); content = _vbox(right)
            content.addWidget(self.build_status('Adaptive scan','Start adaptive scan'))
            self.approve_button = button('Approve next landing',self._approve)
            self.approve_button.setEnabled(False)
            content.addWidget(self.approve_button)
            self.approval_detail = label('', 'muted', word_wrap=True)
            content.addWidget(self.approval_detail)
            tabs = QtWidgets.QTabWidget()
            self.maps = {}
            for key,title,unit in [('measured','Measured objective','nA'),('predicted','Predicted objective','nA'),('uncertainty','Model uncertainty','nA'),('z','Contact Z (commanded)','µm')]:
                plot = AdaptiveMap(title,unit); self.maps[key]=plot; tabs.addTab(plot,title)
            self.lsv = Plot('Latest LSV','Current (nA)',(COLORS['accent'],),app.settings.display_max_points,'Potential E1 (V)')
            tabs.addTab(_plot_card('Latest LSV',self.lsv),'LSV')
            self.z_trace = Plot('Z vs time','Z (µm)',(COLORS['blue'],),app.settings.display_max_points)
            self.i_trace = Plot('Current vs time','Current (nA)',(COLORS['accent'],),app.settings.display_max_points)
            traces = QtWidgets.QWidget(); traces_layout = QtWidgets.QVBoxLayout(traces)
            traces_layout.addWidget(_plot_card('Measured Z',self.z_trace)); traces_layout.addWidget(_plot_card('Current',self.i_trace))
            tabs.addTab(PlotPanel(traces),'Traces')
            self.decisions = QtWidgets.QTableWidget(0,5)
            self.decisions.setHorizontalHeaderLabels(['Landing','X / µm','Y / µm','Objective / nA','Result / reason'])
            self.decisions.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.decisions.horizontalHeader().setSectionResizeMode(4,QtWidgets.QHeaderView.ResizeMode.Stretch)
            tabs.addTab(self.decisions,'Decision log')
            content.addWidget(tabs,1); root.addWidget(right,1)
            self._map_key = None
            self._lsv_count = 0
            self._last_pixel = -1
            self._refresh_maps()
            for name in ('x_min_um','x_max_um','y_min_um','y_max_um'):
                self.fields[name].entry.editingFinished.connect(self._preview)
            self.survey.currentIndexChanged.connect(self._preview)

        def _preview(self):
            if self.experiment.active or self.experiment.params.attempts: return
            try:
                values={name:self.fields[name].float() for name in ('x_min_um','x_max_um','y_min_um','y_max_um')}
                if not np.isfinite(list(values.values())).all(): return
                self.experiment.params=replace(self.experiment.params,**values,survey=self.survey.get())
                self._map_key=None
                self._refresh_maps()
            except ValueError: pass

        def start(self):
            """Parse settings and begin recording before authorizing any movement."""
            try:
                values = {key:field.float() for key,field in self.fields.items()}
                values['max_landings'] = self.fields['max_landings'].integer()
                values['feedback_threshold_na'] /= 1000
                values.update(survey=self.survey.get(),strategy=self.strategy.get(),feedback_channel=self.feedback.get(),
                              objective_channel=self.objective_channel.get(),approve_each=self.approval.get(),region_confirmed=self.confirm.get())
                params = AdaptiveParameters(**values)
                errors = params.validate(self.app.settings)
                if errors: raise ValueError('\n'.join(errors))
                for plot in (self.lsv,self.z_trace,self.i_trace): plot.clear()
                self._lsv_count = 0; self._last_pixel=-1; self._map_key=None
                self._begin(params)
            except (ValueError,RuntimeError,OSError) as exc: self.app.show_error(str(exc))

        def _approve(self):
            try:
                self.experiment.approve()
                self.approve_button.setEnabled(False)
            except (ValueError,RuntimeError,OSError) as exc: self.app.show_error(str(exc))

        def _refresh_maps(self):
            e = self.experiment; attempts=e.params.attempts
            key=(tuple((a['reason'],a.get('contact_z_um')) for a in attempts),id(e.model),id(e.proposal))
            if key==self._map_key: return
            self._map_key=key
            valid=[a for a in attempts if a.get('valid')]
            self.maps['measured'].update_map([a['xy'] for a in valid],[a['objective_na'] for a in valid],attempts,e.proposal,e.params)
            contacts=[a for a in attempts if a.get('contact_z_um') is not None]
            self.maps['z'].update_map([a['xy'] for a in contacts],[a['contact_z_um'] for a in contacts],attempts,e.proposal,e.params)
            model=e.model or {}
            for key,column in [('predicted','mean_na'),('uncertainty','sd_na')]:
                self.maps[key].update_map(model.get('candidate_xy',[]),model.get(column,[]),attempts,e.proposal,e.params)
            self.decisions.setRowCount(len(attempts))
            for row,a in enumerate(attempts):
                for col,value in enumerate([row+1,*[f'{v:g}' for v in a['xy']],f"{a['objective_na']:.5g}" if a.get('valid') else '—',a['reason']+'; '+a['selection_reason']]):
                    self.decisions.setItem(row,col,QtWidgets.QTableWidgetItem(str(value)))

        def on_samples(self,samples):
            """Route raw samples once and redraw maps only when decisions change."""
            e=self.experiment
            if not e.active: return
            update=e.tick_samples(samples)
            self._show_update(update)
            self.approve_button.setEnabled(e.active and e.phase in {'approval','tilt_approval'})
            self.approve_button.setText('Approve tilt and continue' if e.phase=='tilt_approval' else 'Approve next landing')
            if e.phase == 'tilt_approval':
                self.approval_detail.setText(e.detail + f'\nClearance {e.params.clearance_um:g} µm · assumed relief allowance {e.params.relief_allowance_um:g} µm. Sparse measurements cannot establish absence of obstacles.')
            elif e.phase == 'approval' and e.proposal:
                proposal = e.proposal
                detail = f"XY {proposal['xy']} µm · {proposal['reason']}"
                if 'predicted_na' in proposal:
                    detail += f"\nPredicted |i| {proposal['predicted_na']:.4g} nA · model σ {proposal['uncertainty_na']:.4g} nA (not measured)"
                detail += f"\n{max(0, e.params.max_landings - len(e.params.attempts))} landings remaining; motion is revalidated at launch."
                self.approval_detail.setText(detail)
            else: self.approval_detail.clear()
            self.approval_detail.setVisible(bool(self.approval_detail.text()))
            if e.params.attempts:
                pixel=e.params.attempts[-1]['scan_pixel']
                if pixel!=self._last_pixel:
                    self.lsv.clear(); self._lsv_count=0; self._last_pixel=pixel
                for x,y in zip(e._e[self._lsv_count:],e._i[self._lsv_count:]): self.lsv.append(x,y,redraw=False)
                self._lsv_count=len(e._e)
                self.lsv.request_redraw()
            for sample in samples:
                t=self.elapsed_from_start(sample)
                self.z_trace.append(t,sample.z_um,redraw=False)
                self.i_trace.append(t,sample.current1_na if e.params.objective_channel=='Current 1' else sample.current2_na,redraw=False)
            for plot in (self.z_trace,self.i_trace):
                plot.rolling_window_s=self.app.settings.experiment_window_s
                plot.request_redraw()
            self._refresh_maps()

    return AdaptivePage(app)
