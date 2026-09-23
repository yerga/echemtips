"""Reference overlays must not change active quantitative measurements."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import unittest
from echemtips.ui import create_application
from echemtips.analysis_core import AnalysisDataset, NumericRows
from echemtips.analysis_tools import Selection
from echemtips.analysis_views import ExplorerPanel


class ReferenceTests(unittest.TestCase):
    def test_reference_is_an_independent_snapshot_and_not_a_measurement_input(self):
        app = create_application()
        panel = ExplorerPanel()
        columns = ("elapsed_s", "current1_na")
        first = AnalysisDataset(Path("a.csv"), columns, NumericRows(columns, [[0, 1], [1, 1]]), {})
        second = AnalysisDataset(Path("b.csv"), columns, NumericRows(columns, [[0, 3], [1, 3]]), {})
        try:
            panel.set_dataset(first, {"recording": [Selection("a", first.rows, "all")]})
            panel._pin_reference()
            panel.set_dataset(second, {"recording": [Selection("b", second.rows, "all")]})
            self.assertEqual(len(panel.plot.series), 2)
            self.assertEqual(panel.result[2]["mean"], 3)
            self.assertEqual(panel.result[2]["charge_nc"], 3)
            self.assertEqual(panel.reference[3].tolist(), [1, 1])
            panel._clear_reference()
            self.assertEqual(len(panel.plot.series), 1)
        finally:
            panel.close()
