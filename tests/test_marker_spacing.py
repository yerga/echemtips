"""Marker spacing must follow scan geometry, never the map footprint diameter."""
import unittest
from echemtips.models import AppSettings, ScanHoppingCVParameters, ScanHoppingITParameters


class MarkerSpacingTests(unittest.TestCase):
    def test_dense_grid_ignores_footprint_clearance(self):
        for cls in (ScanHoppingCVParameters, ScanHoppingITParameters):
            for diameter in (.1, 10, 100):
                p = cls(x_points=10, y_points=10, footprint_diameter_um=diameter)
                self.assertAlmostEqual(p.marker_position()[1], 65 + 30/9)
                self.assertEqual(p.validate(AppSettings()), [])

    def test_single_row_uses_x_hop(self):
        for cls in (ScanHoppingCVParameters, ScanHoppingITParameters):
            p = cls(x_start_um=20, x_end_um=40, x_points=5, y_points=1,
                    y_start_um=30, y_end_um=30, footprint_diameter_um=100)
            self.assertEqual(p.marker_position(), (20, 35))
            self.assertEqual(p.marker_validation(AppSettings()), [])

    def test_travel_boundary_not_clamped(self):
        for cls in (ScanHoppingCVParameters, ScanHoppingITParameters):
            p = cls(y_start_um=80, y_end_um=90, y_points=2)
            self.assertEqual(p.marker_position()[1], 100)
            self.assertEqual(p.marker_validation(AppSettings()), [])
            p.y_end_um = 91
            self.assertEqual(p.marker_position()[1], 102)
            self.assertTrue(p.marker_validation(AppSettings()))
            p.y_start_um, p.y_end_um = 20, 5
            self.assertEqual(p.marker_position()[1], -10)
            self.assertTrue(p.marker_validation(AppSettings()))

    def test_single_point_needs_explicit_marker(self):
        for cls in (ScanHoppingCVParameters, ScanHoppingITParameters):
            p = cls(x_points=1, y_points=1)
            self.assertTrue(p.marker_validation(AppSettings()))
            p.marker_y_um = p.y_start_um + .1
            self.assertFalse(p.marker_validation(AppSettings()))
            p.marker_x_um = 101
            self.assertTrue(p.marker_validation(AppSettings()))
