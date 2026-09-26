"""Export unit conversion preserves physical data and map geometry."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
import numpy as np
from PySide6 import QtWidgets as Q
from echemtips.qt_common import XYPlot, Heatmap
from echemtips.analysis_export import FigureExportDialog, render_figure
from echemtips.figure_units import label_unit, replace_unit, unit_factor


class FigureUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=Q.QApplication.instance() or Q.QApplication([])

    def test_recognition_and_reference_labels(self):
        self.assertEqual(label_unit('Potential E1 (V vs RHE)'), 'V')
        self.assertEqual(replace_unit('Edited E (V vs RHE)', 'mV'),'Edited E (mV vs RHE)')
        self.assertEqual(replace_unit('Custom caption','nA'),'Custom caption (nA)')
        self.assertIsNone(label_unit('Rate (V/s)'))
        self.assertIsNone(label_unit('Frame number'))
        self.assertAlmostEqual(unit_factor('pA','nA'),.001)
        with self.assertRaises(ValueError): unit_factor('V','A')

    def test_curve_conversion_does_not_accumulate(self):
        plot=XYPlot('Potential E1 (V vs RHE)','Current (nA)'); plot.current_display_unit='pA'
        plot.set_data([('CV',[-.2,.6],[-.005,.01],'#008b83')])
        dialog=FigureExportDialog(plot)
        try:
            selectors=dialog.unit_controls.selectors
            selectors['xlabel'].setCurrentText('mV'); selectors['ylabel'].setCurrentText('nA')
            options=dialog.options(); fig=render_figure(dialog.data,options)
            np.testing.assert_allclose(fig.axes[0].lines[0].get_xdata(),[-200,600])
            np.testing.assert_allclose(fig.axes[0].lines[0].get_ydata(),[-.005,.01])
            self.assertEqual(fig.axes[0].get_xlabel(),'Potential E1 (mV vs RHE)')
            fig.clear()
            selectors['ylabel'].setCurrentText('A'); selectors['ylabel'].setCurrentText('pA')
            self.assertEqual(dialog.options()['units']['ylabel']['factor'],1)
            np.testing.assert_allclose(dialog.data.series[0][2],[-5,10])
            np.testing.assert_allclose(plot.series[0][2],[-.005,.01])
        finally:dialog.close();plot.close()

    def test_map_limits_geometry_and_circular_footprints(self):
        plot=Heatmap('nA','Current'); plot.current_display_unit='pA'; plot.fixed_limits=(-.01,.02)
        plot.set_data({(0,0):.005,(1,1):.01},2,2,x_values=[10,20],y_values=[30,40])
        dialog=FigureExportDialog(plot)
        try:
            dialog.unit_controls.selectors['quantity'].setCurrentText('nA')
            dialog.unit_controls.selectors['xlabel'].setCurrentText('mm')
            options=dialog.options()
            np.testing.assert_allclose(options['limits'],[-.01,.02])
            fig=render_figure(dialog.data,options); ax=fig.axes[0]
            self.assertAlmostEqual(ax.get_aspect(),.001)
            np.testing.assert_allclose(ax.get_xlim(),[.005,.025])
            np.testing.assert_allclose(ax.get_ylim(),[25,45])
            np.testing.assert_allclose(ax.collections[0].get_array().compressed(),[.005,.01])
            fig.clear()
            dialog.data.circular=True; dialog.data.diameter=2
            fig=render_figure(dialog.data,options)
            vertices=fig.axes[0].collections[0].get_paths()[0].vertices
            self.assertAlmostEqual(np.ptp(vertices[:,0]),.002)
            self.assertAlmostEqual(np.ptp(vertices[:,1]),2)
            fig.clear()
            dialog.unit_controls.selectors['quantity'].setCurrentText('A')
            self.assertAlmostEqual(dialog.low.value(),-1e-11,places=18)
            self.assertEqual(plot.fixed_limits,(-.01,.02))
        finally:dialog.close();plot.close()

    def test_time_units(self):
        plot=XYPlot('Time (s)','Potential E1 (V)');plot.set_data([('Step',[0,.01],[0,.1],'#008b83')])
        dialog=FigureExportDialog(plot)
        try:
            dialog.unit_controls.selectors['xlabel'].setCurrentText('ms')
            fig=render_figure(dialog.data,dialog.options())
            np.testing.assert_allclose(fig.axes[0].lines[0].get_xdata(),[0,10]);fig.clear()
        finally:dialog.close();plot.close()
