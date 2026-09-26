"""Full-resolution plot snapshots and window-independent scientific figures."""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Q


@dataclass
class FigureData:
    """Immutable-by-convention snapshot in the displayed physical units."""
    xlabel: str
    ylabel: str
    series: list = field(default_factory=list)
    cells: dict = field(default_factory=dict)
    xs: list = field(default_factory=list)
    ys: list = field(default_factory=list)
    quantity: str = ""
    palette: str = "viridis"
    limits: tuple | None = None
    circular: bool = False
    diameter: float = 1.0
    spacing: tuple = (1.0, 1.0)
    context: dict = field(default_factory=dict)


def snapshot(plot):
    """Copy full-resolution selected curves or currently displayed map cells."""
    if hasattr(plot, 'getPlotItem'):
        item = plot.getPlotItem()
        series = [(curve.name() or f'Curve {i+1}', np.asarray(curve.xData).copy(), np.asarray(curve.yData).copy(), '#008b83')
                  for i,curve in enumerate(item.listDataItems()) if curve.xData is not None and len(curve.xData)]
        if not series: raise ValueError('This plot has no data to export.')
        return FigureData(item.getAxis('bottom').labelText, item.getAxis('left').labelText, series=series)
    if hasattr(plot, 'series'):
        from .qt_common import current_display_scale
        scale, unit = current_display_scale(plot.current_display_unit,
            (v for _, _, y, _ in plot.series for v in y)) if '(nA)' in plot.y_label else (1, '')
        series = [(name, np.asarray(x, dtype=float).copy(), np.asarray(y, dtype=float).copy()*scale, colour)
                  for name, x, y, colour in plot.series if len(x) and len(y)]
        if not series: raise ValueError('This plot has no data to export.')
        from .analysis_reference import potential_label
        return FigureData(potential_label(plot.x_label, _dataset(plot)),
            potential_label(plot.y_label.replace('(nA)', f'({unit})') if unit else plot.y_label, _dataset(plot)), series=series)
    if not plot.values: raise ValueError('This map has no data to export.')
    from .analysis_reference import potential_label
    return FigureData('X position (µm)', 'Y position (µm)',
        cells={key: value*plot.display_scale for key, value in plot.values.items()},
        xs=list(plot.x_values), ys=list(plot.y_values), quantity=potential_label(f'{plot.quantity} ({plot.unit})', _dataset(plot)),
        palette=plot.colormap_name, limits=tuple(v*plot.display_scale for v in plot.fixed_limits) if plot.fixed_limits else None,
        circular=plot.view_mode == 'circular', diameter=plot.footprint_diameter_um,
        spacing=getattr(plot, 'cell_spacing_um', (plot.footprint_diameter_um,plot.footprint_diameter_um)),
        context=dict(getattr(plot, 'export_context', {})))


def plot_rows(data):
    """Yield long-form data without interpolation or display decimation."""
    for index, (name, xs, ys, _) in enumerate(data.series, 1):
        for sample, (x, y) in enumerate(zip(xs, ys)):
            yield index, name, sample, float(x), float(y)


def _dataset(widget):
    while widget is not None:
        value = getattr(widget, 'dataset', None)
        if value is not None: return value
        widget = widget.parentWidget()
    return None


def export_plot_csv(plot):
    """Export exactly the curves selected for this plot, including full samples."""
    from .analysis_views import export_result
    try:
        data = snapshot(plot); dataset = _dataset(plot)
        if dataset is None: raise ValueError('Open a recording before exporting plot data.')
        recipe = dict(scope='Displayed series, full resolution; includes any original-data overlays',
                      x_label=data.xlabel, y_label=data.ylabel)
        export_result(plot, dataset, plot_rows(data), ('series_id', 'series', 'sample', 'x', 'y'), recipe, '_plot')
    except ValueError as exc: Q.QMessageBox.warning(plot, 'Plot export', str(exc))


def _edges(values, spacing=1.0):
    values = np.asarray(values, dtype=float)
    if len(values) == 1: return np.array([values[0]-spacing/2, values[0]+spacing/2])
    mid = (values[:-1]+values[1:])/2
    return np.r_[values[0]-(mid[0]-values[0]), mid, values[-1]+(values[-1]-mid[-1])]


def render_figure(data, options):
    """Build a publication figure using physical dimensions, never widget geometry."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Circle
    from matplotlib import rc_context
    with rc_context({'font.family': options['font'], 'font.size': options['font_size'],
                     'pdf.fonttype': 42, 'svg.fonttype': 'none', 'path.simplify': False}):
        figure = Figure(figsize=(options['width']/25.4, options['height']/25.4), dpi=options['dpi'], layout='constrained')
        FigureCanvasAgg(figure)
        ax = figure.add_subplot()
        if data.series:
            for name, x, y, colour in data.series:
                ax.plot(x, y, color=colour, lw=options['line_width'], label=name)
            if options['legend']: ax.legend(frameon=False, fontsize=options['font_size']*.85)
            ax.spines[['top', 'right']].set_visible(False)
        else:
            matrix = np.full((len(data.ys), len(data.xs)), np.nan)
            for (row, column), value in data.cells.items(): matrix[row, column] = value
            limits = options.get('limits')
            kw = dict(cmap=options['palette'])
            if limits: kw.update(vmin=limits[0], vmax=limits[1])
            if data.circular:
                patches = [Circle((data.xs[c], data.ys[r]), data.diameter/2) for r,c in data.cells]
                artist = PatchCollection(patches, cmap=kw['cmap'], edgecolor='none')
                artist.set_array(np.asarray(list(data.cells.values())))
                if limits: artist.set_clim(*limits)
                ax.add_collection(artist); ax.autoscale_view()
            else:
                artist = ax.pcolormesh(_edges(data.xs,data.spacing[0]), _edges(data.ys,data.spacing[1]), np.ma.masked_invalid(matrix), shading='flat', **kw)
            ax.set_aspect('equal', adjustable='box')
            figure.colorbar(artist, ax=ax, fraction=.055, pad=.04, label=options['quantity'])
        ax.set(xlabel=options['xlabel'], ylabel=options['ylabel'], title=options['title'])
        ax.tick_params(direction='out', width=.7)
        return figure


class _LimitSpin(Q.QDoubleSpinBox):
    """Keep colour limits compact even when the allowed range is very large."""
    def textFromValue(self, value):
        """Use significant figures instead of reserving dozens of digit positions."""
        return self.locale().toString(value, 'g', 8)


class FigureExportDialog(Q.QDialog):
    """Preview editable journal-sized figures and export raster or vector formats."""
    def __init__(self, plot):
        super().__init__(plot)
        self.plot = plot; self.data = snapshot(plot)
        self.setWindowTitle('Export publication figure'); self.resize(900, 600)
        outer = Q.QVBoxLayout(self); body = Q.QHBoxLayout(); outer.addLayout(body)
        settings = Q.QWidget(); form = Q.QFormLayout(settings)
        form.setFieldGrowthPolicy(Q.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroller = Q.QScrollArea(); scroller.setWidgetResizable(True); scroller.setWidget(settings)
        scroller.setFrameShape(Q.QFrame.Shape.NoFrame); scroller.setMinimumWidth(370)
        scroller.setMaximumWidth(420); body.addWidget(scroller)
        scroller.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(760, 500)
        self.fields = {}
        for key, caption, value in [('title','Title',''), ('xlabel','X label',self.data.xlabel),
                                    ('ylabel','Y label',self.data.ylabel), ('quantity','Colour-bar label',self.data.quantity)]:
            control=Q.QLineEdit(value); self.fields[key]=control; form.addRow(caption,control)
        for key, caption, value, low, high in [('width','Width (mm)',150,50,350), ('height','Height (mm)',110,40,350),
                ('font_size','Font size (pt)',10,6,24), ('line_width','Line width (pt)',1.2,.3,5), ('dpi','Raster DPI',300,100,1200)]:
            control=Q.QDoubleSpinBox(); control.setRange(low,high); control.setValue(value)
            control.setDecimals(0 if key=='dpi' else 1)
            self.fields[key]=control; form.addRow(caption,control)
        self.font=Q.QComboBox(); self.font.addItems(['DejaVu Sans','STIXGeneral']); form.addRow('Font',self.font)
        self.palette=Q.QComboBox(); self.palette.addItems(['viridis','cividis','plasma','inferno','coolwarm'])
        self.palette.setCurrentText(self.data.palette); form.addRow('Colormap',self.palette)
        self.legend=Q.QCheckBox('Show curve legend'); self.legend.setChecked(len(self.data.series)<=10); form.addRow(self.legend)
        self.manual=Q.QCheckBox('Manual colour limits'); form.addRow(self.manual)
        self.low=_LimitSpin(); self.high=_LimitSpin()
        for control in (self.low,self.high): control.setRange(-1e12,1e12); control.setDecimals(8)
        finite=np.asarray(list(self.data.cells.values())); finite=finite[np.isfinite(finite)]
        automatic=(float(finite.min()),float(finite.max())) if len(finite) else (0,1)
        low,high=self.data.limits or automatic
        if low==high:
            delta=max(abs(low)*.01,1e-6);low-=delta;high+=delta
        self.low.setValue(low); self.high.setValue(high)
        self.manual.setChecked(self.data.limits is not None)
        form.addRow('Minimum',self.low); form.addRow('Maximum',self.high)
        for control in (self.palette,self.manual,self.low,self.high,self.fields['quantity']): control.setEnabled(not bool(self.data.series))
        for control in (self.palette,self.manual,self.low,self.high,self.fields['quantity']): form.setRowVisible(control,not bool(self.data.series))
        form.setRowVisible(self.legend,bool(self.data.series)); form.setRowVisible(self.fields['line_width'],bool(self.data.series))
        self.manual.toggled.connect(lambda enabled: (self.low.setEnabled(enabled),self.high.setEnabled(enabled)))
        self.low.setEnabled(self.manual.isChecked());self.high.setEnabled(self.manual.isChecked())
        self.preview=Q.QLabel('Select Preview to inspect your figure.'); self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(350,350); body.addWidget(self.preview,1)
        note=Q.QLabel('Full-resolution selected data. PNG/TIFF: selected DPI. PDF/SVG: vector output.\nMap distances retain equal X/Y scale. Labels are export-only; original data remain unchanged.')
        note.setWordWrap(True); outer.addWidget(note)
        actions=Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Close)
        actions.addButton('Preview',Q.QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.preview_figure)
        actions.addButton('Save figure…',Q.QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.save_figure)
        actions.rejected.connect(self.reject); outer.addWidget(actions)

    def options(self):
        """Validate settings before rendering or opening an output file."""
        result={key:widget.text() if isinstance(widget,Q.QLineEdit) else widget.value() for key,widget in self.fields.items()}
        result.update(font=self.font.currentText(), palette=self.palette.currentText(), legend=self.legend.isChecked(),
                      limits=(self.low.value(),self.high.value()) if self.manual.isChecked() and not self.data.series else None)
        if result['limits'] and result['limits'][0]>=result['limits'][1]: raise ValueError('Minimum colour limit must be below maximum.')
        if result['width']*result['height']*(result['dpi']/25.4)**2 > 40_000_000: raise ValueError('Raster size exceeds 40 megapixels; reduce size or DPI.')
        return result

    def preview_figure(self):
        """Render a modest-resolution preview without changing export resolution."""
        try:
            options=self.options(); options['dpi']=100
            figure=render_figure(self.data,options); figure.canvas.draw()
            buffer=np.asarray(figure.canvas.buffer_rgba()); h,w,_=buffer.shape
            image=QtGui.QImage(buffer.data,w,h,4*w,QtGui.QImage.Format.Format_RGBA8888).copy()
            self.preview.setPixmap(QtGui.QPixmap.fromImage(image).scaled(self.preview.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))
            figure.clear()
        except (ValueError,ImportError,RuntimeError) as exc: Q.QMessageBox.warning(self,'Figure preview',str(exc))

    def save_figure(self):
        """Export a fixed-size figure and reproducible formatting/provenance sidecar."""
        import json
        # Export the snapshot's provenance, even if another window changes selection.
        try:
            options=self.options()
            chosen,selected=Q.QFileDialog.getSaveFileName(self,'Save publication figure','','PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tiff)')
            if not chosen:return
            extension={'PNG':'.png','PDF':'.pdf','SVG':'.svg','TIFF':'.tiff'}[selected.split()[0]]
            target=Path(chosen).with_suffix(extension); sidecar=target.with_suffix(extension+'.json')
            if any(path.exists() for path in (target,sidecar)) and Q.QMessageBox.question(self,'Replace export?','Replace the existing figure and its export metadata?') != Q.QMessageBox.StandardButton.Yes:return
            dataset=_dataset(self.plot)
            recipe=dict(options=options, source=str(dataset.path) if dataset else None,
                        selection=self.data.context, series=[s[0] for s in self.data.series],
                        cell_spacing_um=self.data.spacing if self.data.cells else None,
                        processing=dataset.metadata.get('analysis_processing') if dataset else None,
                        reference=dataset.metadata.get('analysis_reference') if dataset else None)
            from matplotlib import rc_context
            with rc_context({'pdf.fonttype':42,'svg.fonttype':'none','path.simplify':False}):
                figure=render_figure(self.data,options); figure.savefig(target,dpi=options['dpi']); figure.clear()
            sidecar.write_text(json.dumps(recipe,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            Q.QMessageBox.information(self,'Figure saved',str(target))
        except (ValueError,OSError,ImportError,RuntimeError) as exc: Q.QMessageBox.warning(self,'Figure export failed',str(exc))


def export_figure(plot):
    """Open a modal formatting dialog on a stable snapshot of plotted data."""
    try: FigureExportDialog(plot).exec()
    except ValueError as exc: Q.QMessageBox.warning(plot,'Figure export',str(exc))


def install_map_export(plot):
    """Add publication export to an analysis heatmap without crowding its axes."""
    bar=Q.QHBoxLayout(); bar.addStretch()
    control=Q.QPushButton('Export figure…'); control.clicked.connect(lambda: export_figure(plot))
    bar.addWidget(control); plot.layout().insertLayout(0,bar)
