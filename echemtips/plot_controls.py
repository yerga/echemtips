"""Small, shared plot controls; freezing changes rendering, never acquisition."""
from pathlib import Path
from PySide6 import QtCore, QtWidgets as Q
import pyqtgraph as pg


class PlotControls(QtCore.QObject):
    """Expose fit, follow, freeze, cursor and figure export consistently."""
    def __init__(self, plot, live=False):
        super().__init__(plot); self.plot = plot; self.live = live
        self.frozen = False; self.manual = False
        bar = Q.QWidget(); row = Q.QHBoxLayout(bar); row.setContentsMargins(2, 0, 2, 0)
        self.mode = Q.QLabel('Following' if live else 'Auto fit'); row.addWidget(self.mode)
        menu = Q.QMenu(plot)
        menu.addAction('Fit / return to live' if live else 'Fit data', self.fit)
        if live:
            self.freeze = menu.addAction('Freeze display (recording continues)')
            self.freeze.setCheckable(True); self.freeze.toggled.connect(self.set_frozen)
        menu.addAction('Export figure…', self.export)
        toggle = Q.QToolButton(); toggle.setText('Plot ▾'); toggle.setMenu(menu)
        toggle.setPopupMode(Q.QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addStretch(); row.addWidget(toggle); plot.layout().insertWidget(0, bar)
        plot.graph.getViewBox().sigRangeChangedManually.connect(self._manual)
        self.cursor = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#607386', width=1))
        self.cursor.setVisible(False); plot.graph.addItem(self.cursor, ignoreBounds=True)
        self.proxy = pg.SignalProxy(plot.graph.scene().sigMouseMoved, rateLimit=15, slot=self._hover)

    def _manual(self, *_):
        self.manual = True
        self.mode.setText('Manual zoom · Plot → Fit to follow')

    def _hover(self, event):
        pos = event[0]
        view = self.plot.graph.getViewBox()
        if not view.sceneBoundingRect().contains(pos): return
        x = view.mapSceneToView(pos).x()
        self.cursor.setPos(x); self.cursor.show()
        self.mode.setToolTip(f'Cursor X: {x:.6g}')
        if self.live and getattr(self.plot, 'time_based', False):
            from .qt_common import Plot
            page = self.plot.parentWidget()
            while page is not None and not hasattr(page, 'experiment_key'): page = page.parentWidget()
            if page is not None:
                for other in page.findChildren(Plot):
                    control = getattr(other, 'view_controls', None)
                    if control and other.time_based:
                        control.cursor.setPos(x); control.cursor.show()

    def fit(self):
        """Restore automatic ranges and render the newest retained display data."""
        self.manual = False
        if self.live:
            self.frozen = False
            self.freeze.setChecked(False)
        self.plot.graph.enableAutoRange()
        self.mode.setText('Following' if self.live else 'Auto fit')
        self.plot.redraw()

    def set_frozen(self, frozen):
        """Stop painting only; underlying buffers continue receiving samples."""
        self.frozen = frozen
        self.mode.setText('Display frozen · recording continues' if frozen else 'Following')
        if not frozen: self.plot.redraw()

    def export(self):
        """Export the current plotted view as PNG or scalable SVG."""
        from pyqtgraph.exporters import ImageExporter, SVGExporter
        path, selected = Q.QFileDialog.getSaveFileName(self.plot, 'Export plotted view', '', 'PNG (*.png);;SVG (*.svg)')
        if not path: return
        try:
            suffix = '.svg' if 'SVG' in selected else '.png'
            target = Path(path).with_suffix(suffix)
            exporter = (SVGExporter if suffix == '.svg' else ImageExporter)(self.plot.graph.plotItem)
            if suffix == '.png':
                width, ok = Q.QInputDialog.getInt(self.plot, 'Figure width', 'Width in pixels', 1600, 400, 10000)
                if not ok: return
                exporter.parameters()['width'] = width
            exporter.export(str(target))
        except (OSError, ValueError, RuntimeError) as exc:
            Q.QMessageBox.warning(self.plot, 'Figure export failed', str(exc))
