"""Reversible surface and coordinate transformations."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from PySide6 import QtWidgets as Q
from echemtips.analysis_core import AnalysisDataset, NumericRows, AnalysisError
from echemtips.analysis_tools import Selection
from echemtips.analysis_topography import contact_points, transform_points, relative_frames, scan_origin
from echemtips.analysis_frames import MapFrames
from echemtips.analysis_views import MapPanel
from echemtips.analysis_export import snapshot, FigureExportDialog, render_figure


def fixture():
    columns = ('elapsed_s', 'scan_pixel', 'z_um', 'current1_na')
    pixels, groups, rows = [], [], []
    for i, (x, y) in enumerate(((10,20), (15,20), (10,25), (15,25))):
        z = 30+.2*x+.4*y
        pixels.append(dict(scan_pixel=i, x_um=x, y_um=y))
        values = [[0,i,z,.01], [1,i,z,.02], [2,i,z,.03]]
        groups.append(Selection(str(i), NumericRows(columns,values),'CV',i))
        rows.extend(dict(zip(columns,v)) for v in values)
    data = AnalysisDataset(Path('/tmp/scan.csv'), columns, rows,
        dict(scan_grid=dict(pixels=pixels),parameters=dict(x_start_um=10,y_start_um=20)))
    return data, groups


class TopographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def test_plane_height_origin_and_no_mutation(self):
        data, groups = fixture()
        points = contact_points(data,groups,[])
        original = [dict(p) for p in points]
        flat, recipe = transform_points(points,origin=scan_origin(data),flatten=True,height=True)
        np.testing.assert_allclose([p['value'] for p in flat],0,atol=1e-12)
        np.testing.assert_allclose(recipe['plane_coefficients'][:2],[.2,.4],atol=1e-12)
        self.assertEqual((flat[0]['x_um'],flat[0]['y_um']),(0,0))
        self.assertEqual(points,original)
        height,_=transform_points(points,height=True)
        self.assertEqual([p['value'] for p in height],[3,2,1,0])
        with self.assertRaisesRegex(AnalysisError,'noncollinear'):
            transform_points(points[:2],flatten=True)

    def test_missing_failed_and_explicit_contacts(self):
        data, groups = fixture()
        pixels=data.metadata['scan_grid']['pixels']
        pixels[0]['contact_z_um']=39.
        pixels[1]['contact_detected']=False
        result=contact_points(data,groups[:3],[])
        self.assertEqual([p['scan_pixel'] for p in result],[0,2])
        self.assertEqual(result[0]['value'],39.)
        self.assertIn('estimate',result[1]['source'])
        self.assertEqual(contact_points(data,[],[])[0]['source'],'recorded contact Z')

    def test_it_surface_estimate_uses_validated_rows(self):
        data, groups=fixture()
        with patch('echemtips.analysis_frames.it_surface_rows',return_value=(groups[0].rows,0)):
            result=contact_points(data,[],[groups[0]])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['value'],40.)

    def test_map_controls_crop_export_and_repeat_toggle(self):
        data, groups=fixture(); panel=MapPanel()
        panel.set_dataset(data,groups,groups)
        panel.statistic.setCurrentText('Contact Z (surface estimate)')
        panel.relative_xy.setChecked(True);panel.height.setChecked(True)
        self.assertEqual(panel.map.x_values,[0,5])
        self.assertEqual(panel.map.y_values,[0,5])
        self.assertEqual(panel.map.values[(1,1)],0)
        self.assertEqual(panel.map.values[(0,0)],3)
        panel.flatten.setChecked(True)
        np.testing.assert_allclose(list(panel.map.values.values()),0,atol=1e-12)
        panel.flatten.setChecked(False)
        panel.relative_xy.setChecked(False);panel.relative_xy.setChecked(True)
        self.assertEqual(panel.map.x_values,[0,5])
        panel.crop_bounds=(15,15,20,25);panel.refresh()
        self.assertEqual(panel.map.x_values,[5])
        exported=snapshot(panel.map)
        self.assertIn('scan start',exported.xlabel)
        self.assertEqual(exported.context['xy_origin_um'],[10.,20.])
        self.assertTrue(exported.context['topography_height'])
        self.assertEqual(exported.cells[(1,0)],0)
        panel.statistic.setCurrentText('Mean')
        self.assertEqual(panel.map.x_values,[5])
        self.assertEqual(panel.map.base_unit,'nA')
        self.assertFalse(panel.three_d.isEnabled())
        self.assertFalse(panel.map.export_context['topography_height'])
        panel.close()

    def test_3d_export_units_and_gaps(self):
        data,groups=fixture();panel=MapPanel();panel.set_dataset(data,groups,groups)
        panel.statistic.setCurrentText('Contact Z (surface estimate)')
        editor=FigureExportDialog(panel.map)
        editor.data.context['projection']='3d'
        editor.unit_controls.selectors['quantity'].setCurrentText('nm')
        fig=render_figure(editor.data,editor.options());fig.canvas.draw()
        self.assertEqual(fig.axes[0].name,'3d')
        self.assertIn('nm',fig.axes[0].get_zlabel())
        self.assertEqual(len(fig.axes[0].collections),2)
        del editor.data.cells[(0,0)]
        fig=render_figure(editor.data,editor.options());fig.canvas.draw()
        self.assertEqual(len(fig.axes[0].collections),1)
        editor.close();panel.close()

    def test_movie_translation_keeps_cache_and_signed_direction(self):
        source=MapFrames(np.array([0,1]),np.array([[1,2],[3,4]]),[1,2],{1:(10,20),2:(5,25)}, {})
        source.auto_limits=(1,4)
        moved=relative_frames(source,(10,20))
        self.assertEqual(moved.coordinates,{1:(0,0),2:(-5,5)})
        self.assertEqual(source.coordinates[1],(10,20))
        self.assertIs(moved.values,source.values)
        self.assertEqual(moved.auto_limits,source.auto_limits)
        self.assertEqual(moved.recipe['xy_coordinates'],'scan-relative')
