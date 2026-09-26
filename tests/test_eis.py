"""Experimental EIS numerical, protocol, recording and simulated lifecycle checks."""
import json
import math
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

from echemtips.eis import EISParameters, EISExperiment, fit_impedance
from echemtips.models import AppSettings
from echemtips.backends import SimulationBackend
from echemtips.data import DataRecorder
from echemtips.experiments import ExperimentState
from echemtips.waypoints import potential_step_plan


class EISTests(unittest.TestCase):
    def test_rc_fit(self):
        for f in (.5,1,10,20):
            t=np.arange(0,4/f,.0005)
            z=1e8+5e8/(1+2j*np.pi*f*5e8*1e-10)
            e=.1+.01*np.cos(2*np.pi*f*t)
            i=.2+.01/abs(z)*np.cos(2*np.pi*f*t-np.angle(z))*1e9
            r=fit_impedance(t,e,i,f)
            self.assertAlmostEqual(r['z_real_ohm']/z.real,1,places=8)
            self.assertAlmostEqual(r['z_imag_ohm']/z.imag,1,places=8)

    def test_guards_and_waypoints(self):
        p=EISParameters()
        self.assertFalse(p.validate(AppSettings()))
        plan,labels=potential_step_plan(p.it_steps())
        self.assertTrue(all(w.hold and 0<w.hold_us<=32767 for w in plan))
        self.assertIn('eis:2:measure',labels)
        self.assertTrue(EISParameters(frequencies_hz=(100,)).validate(AppSettings()))
        self.assertTrue(EISParameters(amplitude_v=.5).validate(AppSettings()))
        self.assertTrue(p.validate(AppSettings(sample_time_us=100,samples_per_point=256)))
        with self.assertRaises(ValueError):fit_impedance([0]*40,[.1]*40,[0]*40,1)

    def test_full_simulation_and_recording(self):
        with TemporaryDirectory() as folder, patch('echemtips.backends.time.monotonic') as clock:
            clock.return_value=100.
            settings=AppSettings(save_directory=folder,sample_time_us=10,samples_per_point=99)
            backend=SimulationBackend(settings);backend.connect()
            ex=EISExperiment(backend,settings)
            p=EISParameters(start_z_um=0,end_z_um=1,approach_rate_um_s=2,retract_rate_um_s=2,
                            settling_time_s=.01,frequencies_hz=(1.,3.,10.))
            rec=DataRecorder();rec.start('Approach + EIS',settings,p);ex.start(p)
            for n in range(2000):
                clock.return_value=100+n*.02
                samples=backend.read_samples();ex.ingest(samples);ex.tick_samples(samples)
                for s in samples:rec.append(s)
                if not ex.active:break
            self.assertEqual(ex.state,ExperimentState.COMPLETE)
            ex.finish_results()
            self.assertLess(abs(backend._positions['Z']-p.start_z_um),.081)
            self.assertEqual(len(p.eis_results),3)
            for r in p.eis_results:
                self.assertNotIn('error',r)
                f=r['frequency_hz'];z=1e8+5e8/(1+2j*np.pi*f*5e8*1e-10)
                self.assertLess(abs(r['magnitude_ohm']/abs(z)-1),.02)
                self.assertLess(abs(r['phase_deg']-np.angle(z,deg=True)),4)
            path=rec.finish(settings,p)
            self.assertIn('eis_frequency_index',path.read_text().splitlines()[0])
            meta=json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(len(meta['parameters']['eis_results']),3)
            self.assertNotIn('eis_frequency_index',DataRecorder._fields_for_parameters(None))
            self.assertIsNone(backend._eis_source)

    def test_stop_inhibits_progression(self):
        b=SimulationBackend(AppSettings());b.connect();ex=EISExperiment(b,b.settings)
        ex.start(EISParameters());ex.request_abort()
        state=ex.state
        self.assertIsNone(ex.tick_samples(b.read_samples()))
        self.assertEqual(ex.state,state)
        ex.abort();self.assertFalse(ex.active);self.assertIsNone(b._eis_source)

    def test_native_contact_gate(self):
        from tests.test_ni_protocol import NativeDriverTests
        fixture=NativeDriverTests();fixture.setUp()
        d,s=fixture.driver,fixture.session
        p=EISParameters(frequencies_hz=(10.,),settling_time_s=0.)
        d.start_method('approach_it',p)
        self.assertEqual(len(d.positions_fifo.writes[-1]),28)
        s.registers['LineNumber'].value=2
        s.registers['Feedback1 Boolean'].value=True
        s.registers['Internal Pause'].value=True
        s.registers['WaitingForWayPoints'].value=False
        self.assertEqual(d.method_status()['stage'],'contact')
        s.registers['WaitingForWayPoints'].value=True
        d.read_samples();self.assertEqual(d.method_status()['stage'],'it')
        descriptors=d._method_sequence.descriptors
        self.assertTrue(any(stage=='it:eis:0:measure' for _,stage in descriptors))
        self.assertEqual(descriptors[-1][1],'retract')
