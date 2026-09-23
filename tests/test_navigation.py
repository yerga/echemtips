"""Search, sidebar persistence, layout and execution-independent navigation."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from echemtips.navigation import EXPERIMENTS, DEFAULT_FAVORITES, FavoriteStore
from echemtips.ui import create_application, EChemTipsApp
from echemtips.experiments import ExperimentState


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        environment = patch.dict(os.environ, {"ECHEMTIPS_SETTINGS_PATH": str(Path(self.folder.name) / "settings.json")})
        environment.start(); self.addCleanup(environment.stop)

    def test_store_handles_missing_invalid_and_empty_preferences(self):
        store = FavoriteStore(Path(self.folder.name) / "settings.json")
        self.assertEqual(store.load(), DEFAULT_FAVORITES)
        store.save([]); self.assertEqual(store.load(), [])
        store.save(["CV", "CV", "Unknown", "Settings"])
        self.assertEqual(store.load(), ["CV"])
        store.path.write_text("not JSON")
        self.assertEqual(store.load(), DEFAULT_FAVORITES)

    def test_library_contains_every_page_and_favorites_persist_in_order(self):
        window = EChemTipsApp(); window.poll_timer.stop()
        try:
            window.resize(1080, 680); window.show(); window.open_library()
            library = window.library
            self.assertEqual(library.results.topLevelItemCount(), len(EXPERIMENTS))
            library.search.setText("standalone cyclic")
            self.assertEqual(library.results.topLevelItemCount(), 1)
            self.assertEqual(library._name(), "CV")
            library._pin()
            self.assertEqual(window.favorites[-1], "CV")
            library._move(-1)
            self.assertEqual(window.favorites[-2], "CV")
            self.assertEqual(window.favorite_store.load(), window.favorites)
            library._open()
            self.assertIs(window.stack.currentWidget(), window.pages["CV"])
            library.search.setText("no-such-experiment")
            self.assertEqual(library.results.topLevelItemCount(), 0)
            self.assertFalse(library.open_button.isEnabled())
            window.set_favorites([])
            self.app.processEvents()
            self.assertTrue(window.nav_buttons["Move piezo"].isVisible())
            self.assertTrue(window.nav_buttons["Settings"].isVisible())
            self.assertFalse(window.nav_buttons["CV"].isVisible())
            self.assertEqual(window.width(), 1080)
        finally:
            window.close()
        reopened = EChemTipsApp()
        try:
            self.assertEqual(reopened.favorites, [])
        finally:
            reopened.close()

    def test_library_navigation_and_unpinning_do_not_stop_active_experiment(self):
        window = EChemTipsApp(); window.poll_timer.stop()
        try:
            window.cv_experiment.state = ExperimentState.CV
            with patch.object(window.backend, "stop_motion") as stop:
                window.show_page("Watch current")
                window.set_favorites([])
                window.open_library()
                self.assertEqual(window._active_page_name(), "CV")
                self.assertFalse(window.return_button.isHidden())
                window._return_to_active()
                self.assertIs(window.stack.currentWidget(), window.pages["CV"])
                self.assertEqual(window.cv_experiment.state, ExperimentState.CV)
                stop.assert_not_called()
        finally:
            window.cv_experiment.state = ExperimentState.IDLE
            window.close()

    def test_many_favorites_scroll_but_anchors_remain_visible(self):
        window = EChemTipsApp(); window.poll_timer.stop()
        try:
            window.set_favorites([entry.name for entry in EXPERIMENTS])
            window.resize(1080, 680); window.show(); self.app.processEvents()
            self.assertEqual(window.width(), 1080)
            self.assertTrue(window.nav_buttons["Move piezo"].isVisible())
            self.assertTrue(window.nav_buttons["Settings"].isVisible())
            self.assertTrue(window.library_button.isVisible())
            self.assertGreater(window.favorite_scroll.verticalScrollBar().maximum(), 0)
        finally:
            window.close()
