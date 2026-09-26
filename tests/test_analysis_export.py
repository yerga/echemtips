"""Publication figures preserve full-resolution data and physical geometry."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
import unittest
import numpy as np
from PySide6 import QtWidgets as Q
from echemtips.analysis_export import FigureData, snapshot, plot_rows, render_figure, FigureExportDialog
from echemtips.qt_common import XYPlot, Heatmap


class PublicationTests(unittest.TestCase):
    """Check scientific values and file dimensions independently of window size."""
    @classmethod
    def setUpClass(cls):
        cls.app=Q.QApplication.instance() or Q.QApplication([])

    def test_full_resolution_and_units(self):
        plot=XYPlot('Potential (V)','Current (nA)'); plot.current_display_unit='pA'
        plot.set_data([('Selected CV',np.arange(50000),np.full(50000,.001),'#008b83')])
        data=snapshot(plot)
        self.assertEqual(data.ylabel,'Current (pA)')
        self.assertEqual(len(list(plot_rows(data))),50000)
        self.assertEqual(data.series[0][2][0],1)
        plot.deleteLater()

    def test_figure_size_and_vector_formats(self):
        plot=XYPlot('E (V)','i (nA)'); plot.set_data([('CV',[0,1,0],[0,1,-1],'#008b83')])
        dialog=FigureExportDialog(plot); options=dialog.options()
        figure=render_figure(dialog.data,options)
        np.testing.assert_allclose(figure.get_size_inches(),[150/25.4,110/25.4])
        self.assertEqual(len(figure.axes[0].lines[0].get_xdata()),3)
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('png','pdf','svg','tiff'):
                path=Path(folder)/('figure.'+ext); figure.savefig(path)
                self.assertGreater(path.stat().st_size,1000)
        figure.clear(); dialog.deleteLater(); plot.deleteLater()

    def test_map_missing_cells_and_equal_aspect(self):
        plot=Heatmap('nA','Current'); plot.current_display_unit='pA'
        plot.set_data({(0,0):.001,(1,1):.002},2,2,x_values=[10,20],y_values=[30,40])
        dialog=FigureExportDialog(plot); figure=render_figure(dialog.data,dialog.options())
        self.assertEqual(figure.axes[0].get_aspect(),1)
        self.assertEqual(np.ma.count_masked(figure.axes[0].collections[0].get_array()),2)
        self.assertEqual(dialog.data.cells[(0,0)],1)
        figure.clear(); dialog.deleteLater(); plot.deleteLater()

    def test_no_data(self):
        plot=XYPlot('x','y')
        with self.assertRaises(ValueError): snapshot(plot)
        plot.deleteLater()
