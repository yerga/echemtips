"""Scientific map semantics, including the three-leg asymmetric CV program."""
from pathlib import Path
import unittest
import numpy as np
from echemtips.analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles, AnalysisError
from echemtips.analysis_tools import cv_selections, hop_selections
from echemtips.analysis_frames import prepare_frames, cv_leg, leg_labels, interpolate, MapFrames

def cv_dataset():
    columns=("elapsed_s","voltage1_v","current1_na","scan_pixel","z_um")
    rows=[]
    for pixel in range(3):
        for cycle in range(2):
            for leg,(low,high) in enumerate(((-.2,.6),(.6,-.4),(-.4,-.2))):
                for e in np.linspace(low,high,21):
                    rows.append([len(rows)*.01,e,10*cycle+leg+e,pixel,50])
    metadata={"parameters":{"cv_start_v":-.2,"cv_vertex1_v":.6,"cv_vertex2_v":-.4,"cycles":2},
              "scan_grid":{"pixels":[dict(scan_pixel=p,x_um=p*10,y_um=0) for p in range(3)]}}
    return AnalysisDataset(Path("scan.csv"),columns,NumericRows(columns,rows),metadata)

class FrameTests(unittest.TestCase):
    def test_whole_cv_keeps_reversals_cycle_selection_and_sweep_specific_current(self):
        d=cv_dataset(); groups=cv_selections(d)
        first=prepare_frames(d,groups,leg=3,cycle=1,count=41)
        second=prepare_frames(d,groups,leg=3,cycle=2,count=41)
        self.assertEqual(len(first.axis),41)
        self.assertAlmostEqual(first.axis[0],-.2); self.assertAlmostEqual(first.axis[-1],-.2)
        self.assertAlmostEqual(first.axis.max(),.6); self.assertAlmostEqual(first.axis.min(),-.4)
        self.assertLess(np.argmax(first.axis),np.argmin(first.axis))
        for leg in range(3):
            mask=np.asarray(first.recipe["frame_segments"])==leg
            reference=prepare_frames(d,groups,leg=leg,cycle=1,axis=first.axis[mask])
            np.testing.assert_allclose(first.values[mask],reference.values,equal_nan=True)
        np.testing.assert_allclose(second.values-first.values,10,atol=1e-10)
        skipped=prepare_frames(d,groups,leg=3,count=41,stride=3)
        np.testing.assert_allclose(skipped.axis,first.axis[::3])
        average=prepare_frames(d,groups,leg=3,cycle=None,count=41)
        np.testing.assert_allclose(average.values-first.values,5,atol=1e-10)

    def test_whole_cv_handles_zero_length_return_leg(self):
        d=cv_dataset(); d.metadata["parameters"]["cv_vertex2_v"]=-.2
        frames=prepare_frames(d,cv_selections(d),leg=3,count=20)
        self.assertNotIn(2,frames.recipe["frame_segments"])
        self.assertAlmostEqual(frames.axis[-1],-.2)

    def test_three_legs_and_cycle_numbers_have_distinct_crossings(self):
        d=cv_dataset(); groups=cv_selections(d)
        self.assertEqual(len(groups),6)
        self.assertEqual([s.cycle for s in groups],[1,2,1,2,1,2])
        # -0.3 occurs on the second and third legs, never on the first.
        with self.assertRaises(AnalysisError): prepare_frames(d,groups,leg=0,axis=[-.3])
        down=prepare_frames(d,groups,leg=1,axis=[-.3],cycle=1)
        back=prepare_frames(d,groups,leg=2,axis=[-.3],cycle=1)
        np.testing.assert_allclose(back.values-down.values,1)
        second=prepare_frames(d,groups,leg=1,axis=[-.3],cycle=2)
        np.testing.assert_allclose(second.values-down.values,10)
        average=prepare_frames(d,groups,leg=1,axis=[-.3],cycle=None)
        np.testing.assert_allclose(average.values-down.values,5)
        self.assertIn("+0.6",leg_labels(d)[0])

    def test_time_is_local_to_cycle_not_recording(self):
        d=cv_dataset(); groups=cv_selections(d)
        frames=prepare_frames(d,groups,kind="CV time",cycle=2,axis=[.1,.2])
        np.testing.assert_allclose(frames.values[:,0],frames.values[:,2])

    def test_excluded_failed_and_incomplete_hops_are_not_used(self):
        d=cv_dataset(); d.metadata["scan_grid"]["pixels"][1]["status"]="failed"
        frames=prepare_frames(d,cv_selections(d),axis=[0],excluded=[2])
        self.assertEqual(frames.pixels,[0]); self.assertEqual(frames.omitted,2)

    def test_interpolation_no_extrapolation_or_nan_bridging(self):
        y=interpolate([0,1,2,3],[0,np.nan,2,3],np.array([-.1,.5,1.5,2.5,4]))
        self.assertTrue(np.isnan(y[[0,1,2,4]]).all()); self.assertEqual(y[3],2.5)

    def test_robust_fixed_dynamic_and_manual_limits(self):
        values=np.arange(100,dtype=float).reshape(10,10)/100; values[-1,-1]=1e9
        f=MapFrames(np.arange(10),values,list(range(10)),{}, {})
        self.assertLess(f.limits("Auto")[1],1)
        self.assertEqual(f.limits("Dynamic",9)[1],1e9)
        self.assertEqual(f.limits("Manual",manual=(-2,2)),(-2,2))
        with self.assertRaises(AnalysisError): f.limits("Manual",manual=(2,-2))

    def test_skip_frames_preserves_requested_order(self):
        d=cv_dataset(); f=prepare_frames(d,cv_selections(d),leg=1,count=11,stride=2)
        self.assertEqual(len(f.axis),6); self.assertGreater(f.axis[0],f.axis[-1])

    def test_it_alignment_requires_complete_identifiable_stationary_program(self):
        columns=("elapsed_s","voltage1_v","current1_na","scan_pixel","z_um")
        levels=np.r_[np.full(20,.1),np.full(25,-.1),np.full(100,.4),np.full(25,-.1),np.full(5,.1)]
        rows=[[i*.01,e,i,0,50] for i,e in enumerate(levels)]
        p=dict(initial_potential_v=-.1,step_potential_v=.4,return_potential_v=-.1,
               initial_hold_s=.25,step_hold_s=1.,return_hold_s=.25,cycles=1)
        d=AnalysisDataset(Path("it.csv"),columns,NumericRows(columns,rows),
            dict(parameters=p,scan_grid=dict(pixels=[dict(scan_pixel=0,x_um=0,y_um=0)])))
        f=prepare_frames(d,hop_selections(d),kind="I–t time",axis=[.1,.3])
        np.testing.assert_allclose(f.values[:,0],[30,50])
        d.metadata["parameters"]["step_potential_v"]=-.1
        with self.assertRaises(AnalysisError): prepare_frames(d,hop_selections(d),kind="I–t time")
