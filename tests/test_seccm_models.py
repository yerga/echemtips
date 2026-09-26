"""Published benchmarks and physical limits of advisory SECCM models."""
import unittest
from dataclasses import replace
import numpy as np
from echemtips.seccm_models import ModelParameters, limiting_current, step_current, steady_wave


class AnalyticalModelsTest(unittest.TestCase):
    def test_report_benchmark(self):
        # Supplied COMSOL report: R2=1.5192 um, i=7.879 pA.
        p = ModelParameters(radius_model="R2")
        self.assertAlmostEqual(abs(limiting_current(p)) * 1e12, 7.879, places=3)

    def test_geometry_and_scaling(self):
        p = ModelParameters()
        self.assertAlmostEqual(p.equivalent_radius_m, 1.431397e-6, delta=1e-12)
        self.assertAlmostEqual(limiting_current(replace(p, concentration_mol_m3=2)) / limiting_current(p), 2)
        self.assertEqual(limiting_current(replace(p, oxidation=True)), -limiting_current(p))

    def test_transient_and_bin_average(self):
        p = ModelParameters()
        self.assertAlmostEqual(float(step_current(p, 1e15)) / limiting_current(p), 1, places=7)
        dt = 0.001
        expected = limiting_current(p) * (1 + 2*p.equivalent_radius_m / np.sqrt(np.pi*p.diffusion_m2_s*dt))
        self.assertAlmostEqual(float(step_current(p, dt, interval_s=dt))/expected, 1)
        with self.assertRaises(ValueError): step_current(p, 0)
        with self.assertRaises(ValueError): step_current(p, dt/2, interval_s=dt)

    def test_wave_limits_and_symmetry(self):
        p = ModelParameters()
        y = steady_wave(p, [-1000, 0, 1000])
        self.assertTrue(np.isfinite(y).all())
        self.assertAlmostEqual(y[0]/limiting_current(p), 1)
        self.assertEqual(y[-1], 0)
        np.testing.assert_allclose(steady_wave(replace(p, oxidation=True), [1,0,-1]), -steady_wave(p, [-1,0,1]))
        fast = replace(p, rate_m_s=1e15)
        self.assertAlmostEqual(float(steady_wave(fast, 0))/limiting_current(fast), 0.5)
        with self.assertRaises(ValueError): steady_wave(replace(p, electrons=2), [0])

    def test_invalid_parameters(self):
        for changes in ({'height_m': 1}, {'half_angle_deg': 0}, {'alpha': float('nan')},
                        {'diffusion_m2_s': -1}, {'concentration_mol_m3': float('inf')}):
            with self.assertRaises(ValueError): limiting_current(replace(ModelParameters(), **changes))
