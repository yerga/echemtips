"""Session, provenance and original-data UX regression tests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from echemtips.ui import create_application
from echemtips.analysis_window import AnalysisWindow
from tests.test_analysis_workbench import write_scan


class AnalysisSessionTests(unittest.TestCase):
    """Ensure saved sessions reproduce choices without editing recordings."""
    @classmethod
    def setUpClass(cls):
        """Create the Qt event loop."""
        cls.qt = create_application([])

    def wait(self, window):
        """Process worker results with a bounded timeout."""
        deadline = time.monotonic() + 15
        while window.loading and time.monotonic() < deadline:
            self.qt.processEvents(); time.sleep(.01)
        self.assertFalse(window.loading)
        self.assertIsNotNone(window.dataset)

    def test_original_table_and_session(self):
        """Retain source table and restore smoothing/map/movie settings."""
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ECHEMTIPS_SETTINGS_PATH': str(Path(folder)/'settings.json')}):
            path = write_scan(folder); original = path.read_bytes()
            window = AnalysisWindow(path)
            try:
                self.wait(window)
                self.assertEqual(window.map_panel.statistic.currentText(), 'CV at potential')
                self.assertIs(window.data_table.model().dataset, window.source_dataset)
                window.smoothing_enabled.setChecked(True)
                with self.assertRaisesRegex(ValueError, 'Apply'): window.workspace.snapshot()
                window._apply_smoothing(); self.wait(window)
                self.assertIs(window.data_table.model().dataset, window.source_dataset)
                window.table_original.setChecked(False)
                self.assertIs(window.data_table.model().dataset, window.dataset)
                window.map_panel.potential.setValue(.3)
                saved = Path(folder)/'test.session.json'
                with patch('echemtips.analysis_workspace.Q.QFileDialog.getSaveFileName', return_value=(str(saved), '')):
                    window.workspace.save()
                self.assertTrue(saved.exists())
                window.map_panel.potential.setValue(.1)
                with patch('echemtips.analysis_workspace.Q.QFileDialog.getOpenFileName', return_value=(str(saved), '')):
                    window.workspace.open()
                self.wait(window)
                self.assertEqual(window.map_panel.potential.value(), .3)
                self.assertEqual(path.read_bytes(), original)
                self.assertGreater(window.cycle_tree.topLevelItem(1).childCount(), 0)
                window._inspect_hop(2)
                self.assertTrue(window.cycle_tree.selectedItems())
            finally: window.close()

    def test_geometry_rounding(self):
        """Center/size entry preserves requested bounds and reports usable counts."""
        from echemtips.scan_geometry_ui import grid_geometry
        result = grid_geometry(50, 50, 30, 0, 8)
        self.assertEqual((result['x_start'], result['x_end']), (35,65))
        self.assertEqual(result['y_points'],1)
        self.assertEqual(result['x_points'],5)
        with self.assertRaises(ValueError): grid_geometry(0,0,100,100,.001)
        with self.assertRaises(ValueError): grid_geometry(0,0,10,10,0)
