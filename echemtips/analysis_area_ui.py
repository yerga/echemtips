"""Experimental post-recording diameter diagnostics; never commands an instrument."""
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from .analysis_area import RetractionConfig
from .analysis_core import AnalysisError
from .analysis_tools import hop_selections
from .qt_common import XYPlot, label, button


class AreaPanel(Q.QWidget):
    """Configure explicit area sources, inspect break points and export hop results."""
    def __init__(self, apply_callback, *, diagnostic_only=False):
        super().__init__()
        self.dataset=None; self.results=[]; self.imported={}; self.imported_source=''
        self.apply_callback=apply_callback
        self.dirty=False; self._restoring=False
        layout=Q.QVBoxLayout(self)
        layout.addWidget(label('Experimental wetted-area estimate — not ECSA or time-resolved area. '
            'Detection uses original currents and measured Z. Validate Z calibration and d(h) for your system.',word_wrap=True))
        controls=Q.QGridLayout(); layout.addLayout(controls)
        self.source=Q.QComboBox()
        for name,key in [('No normalization','none'),('Nominal area','nominal'),
                         ('Retraction estimate','retraction'),('Imported per-landing areas','per_landing')]:
            self.source.addItem(name,key)
        self.channel=Q.QComboBox(); self.channel.addItem('Current 1','current1_na'); self.channel.addItem('Current 2','current2_na')
        self.model=Q.QComboBox(); self.model.addItems(['d = h (unvalidated)','Calibrated: d = a·h + b'])
        def spin(value, low, high, suffix='', decimals=4):
            """Create a physical-unit control with enough precision for nanopipettes."""
            w=Q.QDoubleSpinBox(); w.setRange(low,high); w.setDecimals(decimals); w.setValue(value); w.setSuffix(suffix); return w
        self.nominal=spin(1,1e-8,1e9,' µm²',8)
        self.slope=spin(1,1e-6,1e6)
        self.intercept=spin(0,-1e6,1e6,' µm')
        self.window=Q.QSpinBox(); self.window.setRange(3,501); self.window.setSingleStep(2); self.window.setValue(7)
        self.noise=spin(6,1,100)
        self.jump=spin(1,.0001,1e9,' pA')
        self.ztol=spin(.05,.00001,10,' µm',5)
        self.etol=spin(.005,.00001,1,' V',5)
        fields=[('Normalize using',self.source),('Detection current',self.channel),('Diameter model',self.model),
                ('Nominal area',self.nominal),('Calibration a',self.slope),('Calibration b',self.intercept)]
        if diagnostic_only:
            self.source.hide(); self.nominal.hide()
            fields=[(name,w) for name,w in fields if w not in (self.source,self.nominal)]
        for i,(caption,widget) in enumerate(fields):
            controls.addWidget(label(caption),i//3*2,i%3); controls.addWidget(widget,i//3*2+1,i%3)
        advanced=Q.QGroupBox('Detector settings'); advanced.setCheckable(True); advanced.setChecked(False)
        form=Q.QFormLayout(advanced)
        for caption,widget in [('Persistence window (odd samples)',self.window),('Baseline noise multiplier',self.noise),
                               ('Minimum transition',self.jump),('Z motion tolerance',self.ztol),('Potential stability tolerance',self.etol)]:
            form.addRow(caption,widget)
        # Collapse rather than merely disable the advanced controls.
        for i in range(form.count()): form.itemAt(i).widget().setVisible(False)
        advanced.toggled.connect(lambda enabled: [form.itemAt(i).widget().setVisible(enabled) for i in range(form.count())])
        layout.addWidget(advanced)
        actions=Q.QHBoxLayout(); layout.addLayout(actions)
        actions.addWidget(button('Detect / apply',self._apply,'primary'))
        if not diagnostic_only: actions.addWidget(button('Import areas (JSON)…',self._import))
        actions.addWidget(button('Export landing diagnostics…',self._export))
        self.notice=label('Press Detect / apply to inspect recorded retractions. No acquisition settings are changed.',word_wrap=True)
        layout.addWidget(self.notice)
        split=Q.QSplitter(QtCore.Qt.Orientation.Horizontal); layout.addWidget(split,1)
        self.table=Q.QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(['Hop','d (µm)','Area (µm²)','h (µm)','Result','Reason'])
        self.table.setEditTriggers(Q.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(Q.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(Q.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(self._plot)
        split.addWidget(self.table)
        self.plot=XYPlot('Withdrawal from surface (µm)','Current (nA)'); split.addWidget(self.plot)
        split.setSizes([450,650])
        self.model.currentIndexChanged.connect(self._controls)
        self.source.currentIndexChanged.connect(self._controls); self._controls()
        for w in (self.source,self.channel,self.model,self.nominal,self.slope,self.intercept,self.window,self.noise,self.jump,self.ztol,self.etol):
            signal=w.currentIndexChanged if isinstance(w,Q.QComboBox) else w.valueChanged
            signal.connect(self._changed)

    def _changed(self,*_):
        if not self._restoring:
            self.dirty=True
            self.notice.setText('Settings changed — press Detect / apply. Displayed results still use the previous settings.')

    def _controls(self,*_):
        self.nominal.setEnabled(self.source.currentData()=='nominal')
        for widget in (self.slope,self.intercept): widget.setEnabled(self.model.currentIndex()==1)

    def config(self):
        """Return a portable, explicit configuration; do not infer import identities."""
        detector=RetractionConfig(channel=self.channel.currentData(),window_samples=self.window.value(),
            noise_multiple=self.noise.value(),minimum_jump_pa=self.jump.value(),z_tolerance_um=self.ztol.value(),
            potential_tolerance_v=self.etol.value(),slope=self.slope.value() if self.model.currentIndex() else 1.,
            intercept_um=self.intercept.value() if self.model.currentIndex() else 0.)
        detector.validate()
        return dict(mode=self.source.currentData(),diagnostics_enabled=True,detector=asdict(detector),
                    area_um2=self.nominal.value(),areas_um2=dict(self.imported),import_source=self.imported_source)

    def restore(self,config):
        """Restore session settings without triggering processing or source mutation."""
        self._restoring=True
        self.source.setCurrentIndex(max(0,self.source.findData(config.get('mode','none'))))
        self.nominal.setValue(config.get('area_um2',1))
        d=config.get('detector',{})
        self.channel.setCurrentIndex(max(0,self.channel.findData(d.get('channel','current1_na'))))
        self.window.setValue(d.get('window_samples',7)); self.noise.setValue(d.get('noise_multiple',6))
        self.jump.setValue(d.get('minimum_jump_pa',1)); self.ztol.setValue(d.get('z_tolerance_um',.05))
        self.etol.setValue(d.get('potential_tolerance_v',.005))
        self.slope.setValue(d.get('slope',1)); self.intercept.setValue(d.get('intercept_um',0))
        self.model.setCurrentIndex(int(self.slope.value()!=1 or self.intercept.value()!=0))
        self.imported=dict(config.get('areas_um2',{})); self.imported_source=config.get('import_source','')
        self._restoring=False; self.dirty=False

    def _apply(self):
        if self.dataset is None: return
        try: self.apply_callback(self.config())
        except (ValueError, AnalysisError) as exc: Q.QMessageBox.warning(self,'Area settings',str(exc))

    def _import(self):
        path,_=Q.QFileDialog.getOpenFileName(self,'Import per-landing areas','','JSON (*.json)')
        if not path: return
        try:
            data=json.loads(Path(path).read_text())
            if data.get('units')!='um2' or data.get('id_column')!='scan_pixel':
                raise ValueError('Use units="um2", id_column="scan_pixel" and areas={"0": area, ...}; IDs are zero-based.')
            areas=data['areas']
            if not isinstance(areas,dict) or not areas: raise ValueError('No areas supplied')
            for k,v in areas.items():
                if str(int(k))!=k or int(k)<0 or not np.isfinite(float(v)) or float(v)<=0: raise ValueError('Invalid pixel ID or area')
            valid={g.pixel for g in hop_selections(self.dataset)} if self.dataset else set()
            if not set(map(int,areas)).issubset(valid): raise ValueError('Area IDs include landings not present in this recording')
            self.imported={k:float(v) for k,v in areas.items()}; self.imported_source=str(Path(path).resolve())
            self.dirty=True
            self.source.setCurrentIndex(self.source.findData('per_landing'))
            self.notice.setText(f'{len(areas)} areas imported. Verify registration to this recording, then Detect / apply. Missing IDs remain unnormalized.')
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            Q.QMessageBox.warning(self,'Areas not imported',str(exc))

    def set_dataset(self,dataset,results,config):
        """Install original samples and finished worker results for inspection."""
        self.dataset=dataset; self.results=results; self.applied_config=dict(config)
        self.restore(config)
        self._groups={g.pixel:g for g in hop_selections(dataset)} if 'scan_pixel' in dataset.columns else {}
        self.table.setRowCount(len(results))
        for i,r in enumerate(results):
            values=[str(r['scan_pixel']+1),*(f'{r[k]:.5g}' if r.get(k) is not None else '—' for k in ('diameter_um','area_um2','stretch_um')),r['status'],r['reason']]
            for j,value in enumerate(values):
                item=Q.QTableWidgetItem(value); item.setToolTip(r['reason']); self.table.setItem(i,j,item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(5,260)
        self.table.resizeRowsToContents()
        ok=sum(r['status']=='estimated' for r in results)
        self.notice.setText(f'{ok}/{len(results)} retraction estimates. Unavailable estimates are not failed-landings verdicts. '
            'Density channels appear in CV, Explore, maps and movies when normalization is applied; original currents remain available.' if results else
            'No diagnostics computed. Press Detect / apply. Scan recordings need saved per-hop retraction samples.')
        if results: self.table.setCurrentCell(0,0); self._plot()
        else: self.plot.set_message('No retraction results')
        if not results and config.get('diagnostics_enabled'):
            self.notice.setText('No eligible tagged landings found. Automatic detection requires scan_pixel IDs and recorded surface/retraction samples; nominal-area normalization is still available.')

    def _plot(self,*_):
        index=self.table.currentRow()
        if not 0<=index<len(self.results): return
        r=self.results[index]; rows=self._groups[r['scan_pixel']].rows
        channel=self.applied_config.get('detector',{}).get('channel','current1_na')
        if not {'z_um',channel}.issubset(rows.columns): self.plot.set_message(r['reason']); return
        z=rows.matrix[:,rows.columns.index('z_um')]; i=rows.matrix[:,rows.columns.index(channel)]
        times=rows.matrix[:,rows.columns.index('elapsed_s')]
        if r.get('retraction_start_s') is not None:
            mask=times>=r['retraction_start_s']; z=z[mask]; i=i[mask]
        origin=r.get('surface_z_um')
        self.plot.x_label='Withdrawal from surface (µm)' if origin is not None else 'Measured Z (µm)'
        x=origin-z if origin is not None else z
        series=[('Original current',x,i,'#008b83')]
        if r.get('stretch_um') is not None:
            series.append(('Detected break',[r['stretch_um']]*2,[float(np.nanmin(i)),float(np.nanmax(i))],'#d83b59'))
        self.plot.set_data(series)
        self.plot.export_context={'experimental_retraction':r,'configuration':self.applied_config}
        self.notice.setText(f"Hop {r['scan_pixel']+1}: {r['reason']}. " +
            (f"Sampling Z bracket {r['z_resolution_um']:.4g} µm; not total uncertainty. E1 during retract {r['potential_v']:.4g} V (recorded scale)." if r.get('z_resolution_um') is not None else ''))

    def _export(self):
        if self.dataset is None or not self.results: return
        from .analysis_views import export_result
        fields=('scan_pixel','status','reason','diameter_um','area_um2','stretch_um','break_time_s','z_resolution_um','potential_v')
        export_result(self,self.dataset,([r.get(k) for k in fields] for r in self.results),fields,
                      dict(experimental=True,configuration=self.applied_config),'_retraction_diagnostics')


def open_saved_diagnostic(parent):
    """Show an on-demand control-app diagnostic using only a finished saved file."""
    if parent.recorder.active or parent.recorder.output_path is None:
        Q.QMessageBox.information(parent,'Retraction diagnostic','Finish a recording first. This diagnostic never controls motion.'); return
    from .analysis_movie import Task
    from .analysis_core import AnalysisDataset
    from .analysis_area import estimate_landings
    dialog=Q.QDialog(parent); dialog.setWindowTitle('Experimental landing diameters — saved recording only'); dialog.resize(1150,780)
    layout=Q.QVBoxLayout(dialog)
    pool=QtCore.QThreadPool.globalInstance(); tasks={}; token=[0]; source=[None]
    path=parent.recorder.output_path
    def apply(config):
        """Run file loading and detection outside the acquisition/UI event loop."""
        token[0]+=1; current=token[0]
        panel.setEnabled(False)
        def work(task):
            """Read a closed recording; do not access the backend or recorder."""
            data=source[0] or AnalysisDataset.load(path)
            return data,estimate_landings(data,RetractionConfig(**config['detector']),cancelled=task.cancel.is_set),config
        task=Task(current,work); tasks[current]=task
        def done(t,result,error):
            """Install only the newest result while retaining worker lifetime."""
            tasks.pop(t,None)
            if t!=token[0]: return
            panel.setEnabled(True)
            if error: panel.notice.setText(error); return
            source[0]=result[0]; panel.set_dataset(*result)
        task.signals.done.connect(done); pool.start(task)
    panel=AreaPanel(apply,diagnostic_only=True); layout.addWidget(panel)
    apply(panel.config())
    dialog.exec()
    token[0]+=1
    for task in tasks.values(): task.cancel.set()
