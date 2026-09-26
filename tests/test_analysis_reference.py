"""Reference conversion is reproducible and never changes the source or current sign."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from echemtips.analysis_reference import conversion,convert_dataset,reference_potential,potential_label
from echemtips.analysis_core import AnalysisDataset,NumericRows,extract_cv_cycles
from tests.test_analysis_workbench import write_scan


class ReferenceTests(unittest.TestCase):
    """Check offsets, polarity, waveform segmentation, metadata and disable behaviour."""
    def config(self,**kwargs):
        return dict(enabled=True,mode='database',input_polarity='IUPAC',source='Ag/AgCl (saturated KCl)',target='RHE',source_ph=7,target_ph=7,custom_offset_v=.01,**kwargs)

    def test_offset_direction(self):
        sign,offset=conversion(self.config())
        self.assertEqual(sign,1);self.assertAlmostEqual(offset,.197+.0591593497*7+.01)
        self.assertAlmostEqual(reference_potential('RHE',14),-.8282308958)
        with self.assertRaises(ValueError):reference_potential('RHE',float('nan'))
        with self.assertRaises(ValueError):conversion({'input_polarity':'unknown'})

    def test_conversion_preserves_cycles_and_map_values(self):
        from echemtips.analysis_tools import cv_selections
        from echemtips.analysis_frames import prepare_frames
        with TemporaryDirectory() as folder:
            dataset=AnalysisDataset.load(write_scan(folder));before=dataset.rows.matrix.copy()
            config=self.config();converted=convert_dataset(dataset,config);offset=conversion(config)[1]
            np.testing.assert_array_equal(dataset.rows.matrix,before)
            np.testing.assert_allclose(converted.column('voltage1_v'),dataset.column('voltage1_v')+offset)
            np.testing.assert_array_equal(converted.column('current1_na'),dataset.column('current1_na'))
            self.assertEqual(len(extract_cv_cycles(dataset)),len(extract_cv_cycles(converted)))
            original=prepare_frames(dataset,cv_selections(dataset),axis=[.2]).values
            shifted=prepare_frames(converted,cv_selections(converted),axis=[.2+offset]).values
            np.testing.assert_allclose(original,shifted)
            self.assertEqual(dataset.metadata['parameters']['cv_start_v'],-.2)
            self.assertIn('vs RHE',potential_label('Potential E1 (V)',converted))
            self.assertEqual(potential_label('Potential E2 (V)',converted),'Potential E2 (V)')
            self.assertIs(convert_dataset(dataset,{}),dataset)
            with self.assertRaises(ValueError):convert_dataset(converted,config)

    def test_native_custom_and_nested_recipes(self):
        dataset=AnalysisDataset(Path('test.csv'),('elapsed_s','voltage1_v','voltage2_v','current1_na'),
            NumericRows(('elapsed_s','voltage1_v','voltage2_v','current1_na'),[[0,.5,.6,-.01]]),
            {'parameters':{'recipes':[{'cv_start_v':.5,'cv_scan_rate_v_s':.2}],'approach_voltage_v':.1}})
        config=dict(enabled=True,mode='custom',input_polarity='Instrument-native',custom_offset_v=.2,custom_label='Calibrated RHE')
        result=convert_dataset(dataset,config)
        np.testing.assert_allclose(result.rows.matrix,[[0,-.3,.6,-.01]])
        self.assertAlmostEqual(result.metadata['parameters']['recipes'][0]['cv_start_v'],-.3)
        self.assertEqual(result.metadata['parameters']['recipes'][0]['cv_scan_rate_v_s'],.2)
        self.assertAlmostEqual(result.metadata['parameters']['approach_voltage_v'],.1)
