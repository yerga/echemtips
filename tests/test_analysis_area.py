"""Regression coverage for conservative detachment and reversible normalization."""
import unittest
from pathlib import Path
from dataclasses import replace
import numpy as np
from echemtips.analysis_core import AnalysisDataset, NumericRows, AnalysisError
from echemtips.analysis_area import RetractionConfig, detect_retraction, normalize_dataset


def fixture(sign=1, noise=.0001):
    """Return a stationary surface followed by constant-potential withdrawal."""
    t=np.arange(1200)*.01
    z=50-np.maximum(t-1,0)
    i=np.where(t<4, sign*.03, 0.)+np.random.default_rng(1).normal(0,noise,len(t))
    columns=('elapsed_s','z_um','voltage1_v','current1_na','scan_pixel','x_um','y_um')
    matrix=np.column_stack((t,z,np.full(len(t),.2),i,np.zeros(len(t)),np.ones(len(t)),np.ones(len(t))))
    return NumericRows(columns,matrix)


class AreaTests(unittest.TestCase):
    """Reject deceptive traces and preserve the physical units of original data."""
    def test_positive_negative_and_offset(self):
        for sign in (-1,1):
            rows=fixture(sign)
            m=rows.matrix.copy(); m[:,3]+=.1
            result=detect_retraction(NumericRows(rows.columns,m),.8,50)
            self.assertEqual(result['status'],'estimated',result)
            self.assertAlmostEqual(result['diameter_um'],3,delta=.1)
            self.assertGreater(result['baseline_na'],.09)

    def test_failed_flat_and_unbroken(self):
        rows=fixture()
        for value in (0.,.03):
            m=rows.matrix.copy(); m[:,3]=value
            self.assertEqual(detect_retraction(NumericRows(rows.columns,m),.8,50)['status'],'unavailable')

    def test_potential_motion_time_and_missing(self):
        rows=fixture()
        for col, index, value in ((2,600,.4),(5,600,2.),(1,900,50.),(0,600,0.),(3,600,np.nan)):
            m=rows.matrix.copy(); m[index:,col]=value
            result=detect_retraction(NumericRows(rows.columns,m),.8,50)
            self.assertEqual(result['status'],'unavailable',result)

    def test_spike_and_recrossing(self):
        rows=fixture()
        m=rows.matrix.copy(); m[:,3]=0; m[250:252,3]=1
        self.assertEqual(detect_retraction(NumericRows(rows.columns,m),.8,50)['status'],'unavailable')
        m=rows.matrix.copy(); m[600:900,3]=.03
        self.assertEqual(detect_retraction(NumericRows(rows.columns,m),.8,50)['status'],'unavailable')

    def test_calibration(self):
        base=detect_retraction(fixture(),.8,50)
        result=detect_retraction(fixture(),.8,50,RetractionConfig(slope=2,intercept_um=.5))
        self.assertAlmostEqual(result['diameter_um'],2*base['stretch_um']+.5)
        self.assertAlmostEqual(result['area_um2'],np.pi*result['diameter_um']**2/4)
        with self.assertRaises(AnalysisError): RetractionConfig(slope=0).validate()

    def test_density_preserves_raw_and_gaps(self):
        rows=fixture(); m=rows.matrix.copy(); m[600:,4]=1
        data=AnalysisDataset(Path('test.csv'),rows.columns,NumericRows(rows.columns,m),{})
        original=data.rows.matrix.copy()
        out=normalize_dataset(data,dict(mode='per_landing',areas_um2={'0':2.}))
        np.testing.assert_array_equal(original,data.rows.matrix)
        np.testing.assert_array_equal(out.column('current1_na'),data.column('current1_na'))
        np.testing.assert_allclose(out.column('current_density1_ma_cm2')[:600],data.column('current1_na')[:600]*50)
        self.assertTrue(np.isnan(out.column('current_density1_ma_cm2')[600:]).all())
        self.assertNotIn('analysis_normalization',data.metadata)

    def test_invalid_area(self):
        rows=fixture(); data=AnalysisDataset(Path('test.csv'),rows.columns,rows,{})
        for value in (0,-1,np.inf,np.nan):
            with self.assertRaises(AnalysisError): normalize_dataset(data,dict(mode='nominal',area_um2=value))
        out=normalize_dataset(data,dict(mode='retraction'),[dict(scan_pixel=0,status='unavailable',area_um2=None)])
        self.assertTrue(np.isnan(out.column('current_density1_ma_cm2')).all())


if __name__=='__main__': unittest.main()
