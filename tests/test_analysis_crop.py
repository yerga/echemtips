"""Map crops preserve scientific identity and apply before movie colour scaling."""
import unittest
import numpy as np
from echemtips.analysis_crop import inside,crop_frames
from echemtips.analysis_frames import MapFrames


class CropTests(unittest.TestCase):
    """Exercise inclusive bounds, empty crops and frame provenance."""
    def test_bounds(self):
        self.assertTrue(inside(1,2,(1,3,2,4)))
        self.assertFalse(inside(0,2,(1,3,2,4)))
        for bounds in ((3,1,2,4),(0,1,float('nan'),2)):
            with self.assertRaises(ValueError):inside(0,0,bounds)

    def test_frame_crop_keeps_ids_and_source(self):
        source=MapFrames(np.array([0,1]),np.array([[1,2,999],[2,3,1000]]),[5,7,9],{5:(0,0),7:(1,0),9:(2,0)}, {})
        result=crop_frames(source,(0,1,0,0))
        self.assertEqual(result.pixels,[5,7]);self.assertEqual(source.pixels,[5,7,9])
        self.assertEqual(result.values.shape,(2,2))
        self.assertEqual(result.limits(),(1,3))
        self.assertIn('crop_xy_um',result.recipe)
        with self.assertRaises(ValueError):crop_frames(source,(3,4,0,0))
