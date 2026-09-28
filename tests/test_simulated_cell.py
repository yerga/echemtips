"""Signal-shape regressions for illustrative, low-noise electrochemistry."""
import unittest
from unittest.mock import patch
import numpy as np
from echemtips.simulated_cell import SimulatedCell
from echemtips.backends import SimulationBackend
from echemtips.models import AppSettings


class CellTests(unittest.TestCase):
    def test_contact_spike_and_retraction(self):
        cell=SimulatedCell()
        self.assertEqual(cell.current(0,.1,False),0)
        contact=cell.current(1,.1,True)
        settled=cell.current(2,.1,True)
        self.assertGreater(contact,.08)
        self.assertLess(settled,contact/3)
        self.assertEqual(cell.current(3,.1,False),0)

    def test_redox_peaks_and_scan_rate(self):
        cell=SimulatedCell(); cell.mode='sweep'; cell.wet=True; cell.contact_t=-100
        forward=[cell.current(10,float(e),True) for e in np.linspace(-.2,.6,801)]
        reverse=[cell.current(20,float(e),True) for e in np.linspace(.6,-.4,1001)]
        self.assertAlmostEqual(np.linspace(-.2,.6,801)[np.argmax(forward)],.18,delta=.01)
        self.assertAlmostEqual(np.linspace(.6,-.4,1001)[np.argmin(reverse)],.04,delta=.01)
        self.assertGreater(max(forward),.15); self.assertLess(min(reverse),-.15)
        cell.rate=1
        cell.current(30,-.2,True)
        self.assertGreater(cell.current(31,.18,True),max(forward)*1.8)

    def test_potential_step_decays_in_both_directions(self):
        for sign in (1,-1):
            cell=SimulatedCell(); cell.wet=True; cell.contact_t=-100
            cell.current(0,0,True)
            currents=[cell.current(t,.4*sign,True) for t in (1,1.1,1.5,3)]
            self.assertTrue(all(sign*(a-b)>0 for a,b in zip(currents,currents[1:])),currents)

    def test_dry_noise_stays_well_below_contact_threshold(self):
        b=SimulationBackend(AppSettings()); b.connect()
        values=[b.read_sample().current1_na for _ in range(1000)]
        self.assertLess(np.std(values),.0002)
        self.assertLess(max(np.abs(values)),.001)

    def test_polarity_equivalence(self):
        samples=[]
        for convention,potential in [('IUPAC',.18),('Instrument-native',-.18)]:
            b=SimulationBackend(AppSettings(polarity_convention=convention)); b.connect()
            b.simulated_waveform('sweep',standalone=True)
            b._cell.wet=True; b._cell.contact_t=-100; b._cell.last_e=-.2
            b.set_voltage(1,potential)
            samples.append(b.read_sample().current1_na)
        self.assertGreater(samples[0],0)
        self.assertAlmostEqual(samples[0],-samples[1],places=6)

    def test_reconnect_resets_cell_history(self):
        b=SimulationBackend(AppSettings()); b.connect()
        b.simulated_waveform('sweep',standalone=True)
        b.read_sample()
        self.assertTrue(b._cell.wet)
        b.disconnect(); b.connect()
        self.assertFalse(b._cell.wet)
        self.assertFalse(b._standalone_cell)
        self.assertIsNone(b._cell.last_e)
        self.assertLess(abs(b.read_sample().current1_na),.001)

    def test_each_regular_hop_has_contact(self):
        from echemtips.experiments import ScanHoppingCVExperiment, ScanHoppingITExperiment, ExperimentState
        from echemtips.models import ScanHoppingCVParameters, ScanHoppingITParameters
        for cls,model in [(ScanHoppingCVExperiment,ScanHoppingCVParameters),(ScanHoppingITExperiment,ScanHoppingITParameters)]:
            with self.subTest(model=model.__name__):
                clock=[1000.]
                with patch('time.monotonic',side_effect=lambda:clock[0]):
                    b=SimulationBackend(AppSettings(z_range_um=200)); b.connect()
                    e=cls(b,b.settings)
                    p=model(x_points=2,y_points=2,marker_enabled=False)
                    e.start(p)
                    for _ in range(60000):
                        clock[0]+=.01
                        sample=b.read_sample()
                        e.tick_samples([sample])
                        if not e.active: break
                    self.assertEqual(e.state,ExperimentState.COMPLETE,e.detail)
