"""Recipe planning, shared hardware compilation, simulation and analysis tests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
import tempfile
import unittest
import numpy as np

from echemtips.combinatorial import assign, factorial, program_fields
from echemtips.models import AppSettings, ScanHoppingCVParameters, ScanHoppingITParameters
from echemtips.experiments import ScanHoppingCVExperiment, ScanHoppingITExperiment, ExperimentState
from echemtips.backends import SimulationBackend
from echemtips.data import DataRecorder
from echemtips.analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles
from echemtips.analysis_conditions import condition_dataset


def configure(p):
    """Two complete recipes, assigned to alternating physical positions."""
    base = {f: getattr(p, f) for f in program_fields(p)}
    p.recipes = [{'name': 'Reference', **base}, {'name': 'Variant', **base}]
    if isinstance(p, ScanHoppingCVParameters):
        p.recipes[1].update(cv_scan_rate_v_s=5, cv_vertex1_v=.8, cycles=2)
    else:
        p.recipes[1].update(step_potential_v=.7, step_hold_s=.02, cycles=2)
    p.recipe_assignment = assign(p.x_points,p.y_points,2,'Interleaved')
    return p


class CombinatorialTests(unittest.TestCase):
    def test_assignment_and_no_mutation(self):
        p=configure(ScanHoppingCVParameters(x_points=2,y_points=2))
        self.assertEqual([p.for_point(i).cv_vertex1_v for i in range(4)],[.6,.8,.8,.6])
        self.assertEqual(p.for_point(4).cv_vertex1_v,.6)
        self.assertEqual(p.cv_vertex1_v,.6)
        self.assertIs(p.for_point(1).retraction_events,p.retraction_events)
        self.assertEqual(assign(2,4,2,'Row blocks',2),[0,0,0,0,1,1,1,1])
        self.assertEqual(assign(3,4,3,'Randomized',seed=42),assign(3,4,3,'Randomized',seed=42))
        self.assertEqual(len(factorial(p.recipes[0],'cv_scan_rate_v_s',[1,2],'cv_vertex1_v',[.3,.5])),4)

    def test_validation_all_recipes(self):
        for cls in (ScanHoppingCVParameters,ScanHoppingITParameters):
            p=configure(cls())
            self.assertEqual(p.validate(AppSettings()),[])
            p.recipes[1]['start_z_um']=90
            self.assertTrue(p.validate(AppSettings()))
            del p.recipes[1]['start_z_um']
            p.recipe_assignment.pop()
            self.assertTrue(p.validate(AppSettings()))
        p=configure(ScanHoppingCVParameters())
        p.recipes[1]['cv_vertex1_v']=3
        self.assertTrue(p.validate(AppSettings(mode='NI FPGA',command_voltage_ratio=5)))
        p.recipes[1]['cv_vertex1_v']=float('nan')
        self.assertTrue(p.validate(AppSettings()))

    def test_simulations_complete_with_different_programs(self):
        for cls, runner in ((ScanHoppingCVParameters,ScanHoppingCVExperiment),(ScanHoppingITParameters,ScanHoppingITExperiment)):
            p=configure(cls(x_points=2,y_points=2,marker_enabled=True,lateral_rate_um_s=100,approach_rate_um_s=20,retract_rate_um_s=100))
            settings=AppSettings(polarity_convention='Instrument-native')
            backend=SimulationBackend(settings,seed=4); backend.connect()
            if cls is ScanHoppingCVParameters:
                p.feedback_threshold_na=2
                p.recipes[0]['cv_scan_rate_v_s']=10
            else:
                p.feedback_threshold=2
                for recipe in p.recipes:
                    recipe['initial_hold_s']=recipe['return_hold_s']=.01
            experiment=runner(backend,settings); experiment.start(p)
            seen={}
            for _ in range(2000):
                backend._last_tick-=.25
                if cls is ScanHoppingCVParameters: experiment._last_tick-=.25
                elif experiment.state==ExperimentState.IT: experiment._step_deadline=0
                sample=backend.read_sample(); experiment.tick_samples([sample])
                if experiment.state in (ExperimentState.CV,ExperimentState.IT):
                    expected=p.for_point(experiment.point_index)
                    if cls is ScanHoppingCVParameters:
                        self.assertEqual(experiment._segments,[expected.cv_vertex1_v,expected.cv_vertex2_v,expected.cv_start_v]*expected.cycles)
                    else: self.assertEqual(experiment._steps,expected.it_steps())
                    seen[experiment.point_index]=True
                if not experiment.active: break
            self.assertEqual(experiment.state,ExperimentState.COMPLETE)
            self.assertEqual(len(seen),5)
            self.assertAlmostEqual(backend.commanded_position()['Z'],p.start_z_um,delta=.08)

    def test_native_compiles_selected_recipe(self):
        from test_ni_protocol import NativeDriverTests
        fixture=NativeDriverTests(); fixture.setUp()
        driver=fixture.driver
        p=configure(ScanHoppingCVParameters(x_points=2,y_points=2,marker_enabled=False))
        driver.start_scan_hopping_cv(p)
        with patch('echemtips.ni_driver.cyclic_voltammetry_plan', wraps=__import__('echemtips.waypoints',fromlist=['cyclic_voltammetry_plan']).cyclic_voltammetry_plan) as build, patch.object(driver,'_enqueue'):
            driver._submit_scan_cv(1)
            self.assertEqual(build.call_args.kwargs['vertex1_v'],.8)
            self.assertEqual(build.call_args.kwargs['cycles'],2)
        p=configure(ScanHoppingITParameters(x_points=2,y_points=2,marker_enabled=False))
        with patch.object(driver,'_submit_method_plan') as submit:
            driver._submit_method_it(p,1)
            stages=submit.call_args.args[1]
            self.assertEqual(sum(stage=='it:pulse' for _,stage in stages),2)

    def test_metadata_and_condition_analysis(self):
        p=configure(ScanHoppingCVParameters(x_points=2,y_points=2,marker_enabled=False))
        metadata={'parameters':{'recipes':p.recipes},'scan_grid':DataRecorder._scan_grid_metadata(p)}
        self.assertEqual([x['condition_id'] for x in metadata['scan_grid']['pixels']],[0,1,1,0])
        columns=['elapsed_s','scan_pixel','voltage1_v','current1_na']
        rows=[]
        for pixel in range(4):
            recipe=p.for_point(pixel)
            potentials=[]
            for _ in range(recipe.cycles):
                for a,b in ((recipe.cv_start_v,recipe.cv_vertex1_v),(recipe.cv_vertex1_v,recipe.cv_vertex2_v),(recipe.cv_vertex2_v,recipe.cv_start_v)):
                    potentials.extend(np.linspace(a,b,50))
            for e in potentials: rows.append([len(rows)*.01,pixel,e,e*2])
        data=AnalysisDataset(Path('test.csv'),columns,NumericRows(columns,np.asarray(rows)),metadata)
        self.assertEqual(len(extract_cv_cycles(data)),6)
        filtered=condition_dataset(data,1)
        self.assertEqual(set(filtered.column('scan_pixel')),{1,2})
        self.assertEqual(filtered.metadata['parameters']['cv_vertex1_v'],.8)
        self.assertEqual(len(extract_cv_cycles(filtered)),4)
        self.assertEqual(len(data.rows),900)

    def test_missing_map_potential_and_duration(self):
        p=configure(ScanHoppingCVParameters(x_points=2,y_points=1,marker_enabled=False))
        p.recipes[1].update(waveform='LSV',cycles=1,cv_start_v=-.8,cv_vertex1_v=-.4)
        self.assertEqual(p.validate(AppSettings()),[])
        self.assertGreater(p.estimated_known_duration_s(),0)
        backend=SimulationBackend(AppSettings()); backend.connect()
        experiment=ScanHoppingCVExperiment(backend,AppSettings()); experiment.params=p
        experiment._track_current(backend.read_sample(),1)
        self.assertIsNone(experiment._current_candidate)

    def test_background_condition_selection(self):
        from echemtips.analysis_jobs import LoadRecording
        p=configure(ScanHoppingITParameters(x_points=2,y_points=1,marker_enabled=False))
        columns=['elapsed_s','scan_pixel','current1_na']
        source=AnalysisDataset(Path('test.csv'),columns,NumericRows(columns,np.array([[0,1,2],[1,1,3]],float)),
            {'parameters':{'recipes':p.recipes},'scan_grid':DataRecorder._scan_grid_metadata(p)})
        results=[]
        task=LoadRecording(1,source.path,source=source)
        task.signals.finished.connect(lambda *args: results.append(args))
        task.run()
        self.assertEqual(results[0][2],'')
        self.assertEqual(results[0][1][0].metadata['analysis_condition']['id'],1)
        self.assertIs(task.source,source)

    def test_planner_laptop_and_existing_page(self):
        from echemtips.ui import create_application, EChemTipsApp
        from echemtips.combinatorial_ui import RecipeDialog
        app=create_application([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'ECHEMTIPS_SETTINGS_PATH':str(Path(folder)/'settings.json')}):
            window=EChemTipsApp(); window.resize(1280,800); window.show()
            page=window.pages['Scan hopping + CV']; window.show_page('Scan hopping + CV')
            app.processEvents()
            dialog=RecipeDialog(page.parameters(),window.settings,[],page)
            dialog.add(); dialog.mode.setCurrentText('Interleaved'); dialog.show(); app.processEvents()
            self.assertEqual(dialog.table.rowCount(),2)
            self.assertGreater(dialog.preview.height(),100)
            dialog.grab().save('/private/tmp/combinatorial-planner.png')
            from PySide6 import QtWidgets
            dialog.findChild(QtWidgets.QTabWidget).setCurrentIndex(1); app.processEvents()
            dialog.grab().save('/private/tmp/combinatorial-assignment.png')
            dialog.accept_plan()
            self.assertEqual(dialog.result(),dialog.DialogCode.Accepted)
            page._recipe_plan=(dialog.result_recipes,dialog.result_assignment,(3,3))
            page.recipe_enabled.setChecked(True)
            self.assertEqual(len(page.parameters().recipes),2)
            window.close()


if __name__=='__main__': unittest.main()
