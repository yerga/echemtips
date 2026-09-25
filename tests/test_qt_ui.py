from __future__ import annotations

import os
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from echemtips.analysis_window import AnalysisWindow
from echemtips.backends import SimulationBackend
from echemtips.branding import application_icon
from echemtips.models import AppSettings, MAP_COLORMAPS, SettingsStore
from echemtips.models import Sample
from echemtips.qt_common import Heatmap, InfoButton, Plot, ProgramDiagram, TimedXYPlot, XYPlot
from echemtips.ui import EChemTipsApp, create_application


class QtLayoutTests(unittest.TestCase):
    def test_lsv_controls_all_cv_pages(self):
        window = EChemTipsApp(); window.poll_timer.stop()
        try:
            for name in ('CV', 'Approach + CV', 'Approach + CV scan-rate series', 'Scan hopping + CV'):
                page = window.pages[name]
                page.waveform.setCurrentText('LSV')
                params = page.parameters()
                self.assertEqual(params.waveform, 'LSV')
                self.assertEqual(params.cycles, 1)
                self.assertTrue(page.vertex2.isHidden())
                self.assertTrue(page.cycles.isHidden())
                self.assertEqual(page.vertex1.findChildren(QtWidgets.QLabel)[0].text(), 'End potential')
                page.waveform.setCurrentText('CV')
                self.assertFalse(page.vertex2.isHidden())
                self.assertFalse(page.cycles.isHidden())
        finally:
            window.close()

    def test_scan_rate_series_editor_and_registration(self):
        window = EChemTipsApp()
        window.poll_timer.stop()
        page = window.pages['Approach + CV scan-rate series']
        editor = page.rate_editor
        self.assertEqual(editor.values(), [.1, .25, .5, 1])
        editor.set_rates([.2, 1, .2])
        editor.table.setCurrentCell(1, 0)
        editor.move_rate(-1)
        self.assertEqual(editor.values(), [1, .2, .2])
        editor.remove_rate()
        self.assertEqual(editor.values(), [.2, .2])
        self.assertEqual(page.parameters().scan_rates_v_s, [.2, .2])
        self.assertEqual(page.parameters().total_cv_cycles, 4)
        self.assertIs(page.experiment, window.experiments['approach_cv_series'])
        self.assertTrue(page.scan_rate.isHidden())
        self.assertEqual(page.start_button.text(), 'Start scan-rate series')
        window.close()

    def test_scan_marker_controls_and_preview(self):
        from PySide6 import QtSvgWidgets
        window = EChemTipsApp()
        window.poll_timer.stop()
        for name in ('Scan hopping + CV', 'Scan hopping + I-t'):
            page = window.pages[name]
            self.assertTrue(page.marker_enabled.isChecked())
            self.assertEqual(page.parameters().marker_position(), (35, 80))
            previews = page.findChildren(QtSvgWidgets.QSvgWidget)
            self.assertEqual(len(previews), 1)
            self.assertTrue(previews[0].renderer().isValid())
            page.marker_x.entry.setText('30')
            page.marker_y.entry.setText('85')
            self.assertEqual(page.parameters().marker_position(), (30, 85))
            page.marker_enabled.setChecked(False)
            self.assertFalse(page.marker_x.entry.isEnabled())
            self.assertEqual(page.parameters().execution_point_count, 9)
        window.close()

    def test_simulator_monitor_uses_full_resolution_rolling_path(self):
        window = EChemTipsApp()
        window.poll_timer.stop()
        backend = SimulationBackend(AppSettings())
        try:
            page = window.pages["Watch current"]
            page.set_live_view(True)
            page.current1_plot.buffer.max_points = 500
            with patch("echemtips.backends.time.monotonic", return_value=100) as clock:
                backend.connect()
                samples = []
                for i in range(5000):
                    clock.return_value = 100 + i * .002
                    samples.append(backend.read_sample())
                page.on_samples(samples)
            self.assertEqual(len(page.current1_plot.x_values), 5000)
            self.assertEqual(page.current1_plot.series[0], [s.current1_na for s in samples])
            self.assertFalse(page.current1_plot._render_timer.isActive())
        finally:
            backend.disconnect()
            window.close()

    def test_rolling_plot_keeps_all_visible_samples_despite_small_point_limit(self):
        plot = Plot("Dense", "Current (nA)", ("#12877f",), max_points=500, rolling_window_s=30)
        history = TimedXYPlot("History", "Current (nA)", "#12877f", 500, "Z (µm)", 30)
        try:
            for i in range(40001):
                value = (-1.0 if i % 2 else 1.0) * i
                plot.append(i / 1000, value, redraw=False)
                history.append_timed(i / 1000, i / 2000, value, redraw=False)
            plot.request_redraw(); history.request_redraw()
            self.assertEqual(len(plot.x_values), 30001)
            self.assertEqual(len(history.clock_values), 30001)
            self.assertEqual(plot.series[0], history.series[0])
            plot.redraw()
            self.assertEqual(len(plot.curves[0].xData), 30001)
            self.assertFalse(plot.curves[0].opts["antialias"])
            self.assertEqual(plot.curves[0].curve.opts["segmentedLineMode"], "on")
        finally:
            plot.close(); history.close()

    def test_hidden_live_plot_defers_render_and_coalesces_visible_updates(self):
        from PySide6.QtTest import QTest
        plot = Plot("Live", "Value", ("#12877f",), rolling_window_s=30)
        try:
            plot.append(0, 1)
            self.assertFalse(plot._render_timer.isActive())
            with patch.object(plot, "redraw", wraps=plot.redraw) as render:
                plot.show()
                for i in range(100):
                    plot.append(i / 100, i)
                self.assertEqual(render.call_count, 0)
                QTest.qWait(160)
                self.assertEqual(render.call_count, 1)
                self.assertEqual(len(plot.curves[0].xData), 101)
                plot.hide()
                plot.append(1, 123)
                QTest.qWait(120)
                self.assertEqual(render.call_count, 1)
                plot.show(); QTest.qWait(160)
                self.assertEqual(plot.curves[0].yData[-1], 123)
        finally:
            plot.close()

    def test_shared_logo_and_application_identity(self) -> None:
        self.assertEqual(self.qt_app.applicationDisplayName(), "eChemTips")
        self.assertFalse(self.qt_app.windowIcon().isNull())
        for size in (16, 32, 64, 256):
            self.assertFalse(application_icon().pixmap(size, size).isNull())
        windows = (EChemTipsApp(), AnalysisWindow())
        try:
            self.assertEqual(windows[0].windowTitle(), "eChemTips — Instrument Control")
            self.assertEqual(windows[1].windowTitle(), "eChemTips — Data Analysis")
            for window in windows:
                self.assertFalse(window.windowIcon().isNull())
                logos = window.findChildren(QtWidgets.QLabel, "applicationLogo")
                self.assertEqual(len(logos), 1)
                self.assertFalse(logos[0].pixmap().isNull())
                self.assertIn("pipette", logos[0].accessibleName())
        finally:
            for window in windows:
                window.close()

    def test_settings_tabs_keep_save_action_visible_at_laptop_sizes(self) -> None:
        window = EChemTipsApp()
        try:
            window.resize(1080, 680); window.show(); window.show_page("Settings")
            settings = window.pages["Settings"]
            names = ("Connection", "Acquisition", "Piezos", "Amplifiers", "Saving", "Plots", "Maps")
            self.assertEqual(tuple(settings.tabs.tabText(i) for i in range(settings.tabs.count())), names)
            for font_size in (10, 14):
                window.settings.font_size_pt = font_size
                window._apply_display_settings()
                for index, name in enumerate(names):
                    settings.tabs.setCurrentIndex(index)
                    for _ in range(4): self.qt_app.processEvents()
                    viewport = settings.tab_scrolls[name]
                    viewport.verticalScrollBar().setValue(viewport.verticalScrollBar().maximum())
                    self.qt_app.processEvents()
                    save = settings.save_defaults_button
                    self.assertTrue(save.isVisible(), name)
                    self.assertFalse(viewport.isAncestorOf(save), name)
                    rectangle = QtCore.QRect(save.mapTo(window, QtCore.QPoint()), save.size())
                    self.assertTrue(window.rect().contains(rectangle), (font_size, name, rectangle))
            settings.tabs.setCurrentIndex(5)
            settings.monitor_window.variable.set("15")
            settings.tabs.setCurrentIndex(6)
            settings.map_z_colormap.setCurrentText("Magma")
            settings.tabs.setCurrentIndex(0)
            self.assertEqual(settings.values().monitor_window_s, 15)
            self.assertEqual(settings.values().map_z_colormap, "magma")
        finally:
            window.close()

    def test_colormaps_match_images_footprints_and_scale(self) -> None:
        heatmap = Heatmap("nA", "Current 1")
        try:
            heatmap.fixed_limits = (-1, 1)
            data = {(0, 0): -1, (0, 1): 0, (0, 2): 1}
            for palette in MAP_COLORMAPS.values():
                heatmap.colormap_name = palette
                for mode in ("square", "circular"):
                    heatmap.set_data(data, 1, 3, view_mode=mode)
                    self.assertEqual(heatmap.values, data)
                    self.assertEqual(heatmap.color_bar.levels(), (-1, 1))
                    self.assertIs(heatmap.color_bar.colorMap(), heatmap.color_map)
                    self.assertIs(heatmap.image_item.getColorMap(), heatmap.color_map)
                    for point, fraction in zip(heatmap.footprint_item.points(), (0, 0.5, 1)):
                        self.assertEqual(point.brush().color(), heatmap.color_map.map(fraction, mode="qcolor"))
        finally:
            heatmap.close()

    def test_analysis_current_style_preserves_source_data(self) -> None:
        plot = XYPlot("Potential (V)", "Current (nA)")
        try:
            xs, ys = [-0.2, 0.3], [0.25, -0.5]
            plot.current_display_unit = "pA"; plot.trace_width_px = 4
            plot.set_data([("CV", xs, ys, "red")])
            curve = plot.graph.listDataItems()[0]
            self.assertEqual(list(curve.getData()[1]), [250, -500])
            self.assertEqual(ys, [0.25, -0.5])
            self.assertEqual(curve.opts["pen"].widthF(), 4)
        finally:
            plot.close()

    def setUp(self) -> None:
        self.settings_directory = TemporaryDirectory()
        self.addCleanup(self.settings_directory.cleanup)
        path = Path(self.settings_directory.name) / "settings.json"
        SettingsStore(path).save(AppSettings())
        environment = patch.dict(os.environ, {"ECHEMTIPS_SETTINGS_PATH": str(path)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_current_display_conversion_does_not_modify_buffer(self) -> None:
        plot = Plot("Current", "Current 1 (nA)", ("red",))
        try:
            plot.append(0, 0.25); plot.append(1, -0.5)
            plot.set_display_style("pA", 12, 3)
            self.assertEqual(plot.series[0], [0.25, -0.5])
            self.assertEqual(list(plot.curves[0].getData()[1]), [250, -500])
            self.assertEqual(plot.graph.getAxis("left").labelText, "Current 1 (pA)")
            self.assertEqual(plot.curves[0].opts["pen"].widthF(), 3)
            plot.set_display_style("Auto", 10, 2)
            self.assertEqual(list(plot.curves[0].getData()[1]), [250, -500])
            plot.append(2, 2)
            plot.redraw()  # Live updates are deferred; force this unit test's frame.
            self.assertEqual(plot.graph.getAxis("left").labelText, "Current 1 (nA)")
            self.assertEqual(list(plot.curves[0].getData()[1]), [0.25, -0.5, 2])
        finally:
            plot.close()

    def test_fixed_map_limits_scale_with_units_without_changing_values(self) -> None:
        heatmap = Heatmap("nA", "Current 1")
        try:
            heatmap.current_display_unit = "pA"
            heatmap.fixed_limits = (-0.5, 0.5)
            values = {(0, 0): -1, (0, 1): 0.25}
            for mode in ("square", "circular"):
                heatmap.set_data(values, 1, 2, view_mode=mode)
                self.assertEqual(heatmap.values, values)
                self.assertEqual(heatmap.color_bar.levels(), (-500, 500))
                self.assertEqual(heatmap.unit, "pA")
                self.assertEqual(list(heatmap.image_item.image[0]), [-1000, 250])
            heatmap.current_display_unit = "nA"
            heatmap.fixed_limits = None
            heatmap.set_data(values, 1, 2)
            self.assertEqual(heatmap.color_bar.levels(), (-1, 0.25))
        finally:
            heatmap.close()

    def test_display_controls_apply_to_windows_styles_and_readbacks(self) -> None:
        window = EChemTipsApp()
        try:
            controls = window.pages["Settings"]
            controls.monitor_window.variable.set("10")
            controls.experiment_window.variable.set("20")
            controls.current_units.setCurrentText("pA")
            controls.font_size.variable.set("14")
            controls.trace_width.variable.set("3")
            controls.map_current_auto.setChecked(False)
            controls.map_current_min.variable.set("-0.1")
            controls.map_current_max.variable.set("0.5")
            watch = window.pages["Watch current"].current1_plot
            for i in range(30): watch.append(i, 0.25, redraw=False)
            history = window.pages["Approach"].approach_history
            for i in range(30): history.append_timed(i, 30-i, 0.1, redraw=False)
            with patch.object(window.store, "save"):
                window.apply_settings(controls.values())
            self.assertEqual(watch.x_values[0], 19)
            self.assertEqual(history.clock_values[0], 9)
            self.assertEqual(len(history.clock_values), len(history.x_values))
            self.assertEqual(window.pages["CV"].cv_plot.rolling_window_s, None)
            self.assertEqual(window.pages["CV"].current_plot.rolling_window_s, 20)
            self.assertEqual(window.pages["Scan hopping + CV"].current_map.fixed_limits, (-0.1, 0.5))
            window.instrument_readout.set_sample(Sample(0, 0, 0, 0, 0, 0, 0.25, -0.5))
            self.assertIn("250.000 pA", window.instrument_readout.value_labels["current1_na"].text())
            for width in (1080, 1440):
                window.resize(width, 680); window.show(); window.show_page("Approach")
                for _ in range(4): self.qt_app.processEvents()
                text = window.pages["Approach"].findChild(QtWidgets.QLabel, "contactHelp")
                self.assertGreaterEqual(text.height(), text.heightForWidth(text.width()))
                for nav in window.nav_buttons.values():
                    self.assertGreaterEqual(nav.width(), nav.sizeHint().width())
                emergency = next(b for b in window.findChildren(QtWidgets.QPushButton) if b.text() == "EMERGENCY STOP")
                self.assertGreaterEqual(emergency.width(), emergency.sizeHint().width())
        finally:
            window.close()

    def test_map_preferences_are_shared_saved_and_do_not_reset_connection(self) -> None:
        window = EChemTipsApp()
        try:
            with TemporaryDirectory() as folder:
                window.store = SettingsStore(Path(folder) / "settings.json")
                settings_page = window.pages["Settings"]
                settings_page.map_view.setCurrentText("Circular footprints")
                settings_page.map_footprint.variable.set("2.5")
                settings_page.map_z_colormap.setCurrentText("Cividis")
                settings_page.map_current_colormap.setCurrentText("Blue–white–red")
                backend = window.backend
                backend.connect()
                experiment = window.scan_experiment
                scan = window.pages["Scan hopping + CV"]
                scan.z_map.set_data({(0, 0): 12}, 1, 1, x_values=[20], y_values=[30])
                window.apply_settings(settings_page.values())
                self.assertIs(window.backend, backend)
                self.assertTrue(backend.connected)
                self.assertIs(window.scan_experiment, experiment)
                saved = window.store.load()
                self.assertEqual(saved.map_view_mode, "circular")
                self.assertEqual(saved.map_footprint_diameter_um, 2.5)
                self.assertEqual(saved.map_z_colormap, "cividis")
                self.assertEqual(saved.map_current_colormap, "CET-D1")
                with patch.dict(os.environ, {"ECHEMTIPS_SETTINGS_PATH": str(window.store.path)}):
                    reopened = EChemTipsApp()
                    try:
                        self.assertEqual(reopened.pages["Settings"].map_view.get(), "Circular footprints")
                        self.assertEqual(reopened.pages["Scan hopping + I-t"].z_map.view_mode, "circular")
                        self.assertEqual(reopened.pages["Scan hopping + I-t"].z_map.colormap_name, "cividis")
                        self.assertEqual(reopened.pages["Scan hopping + CV"].current_map.colormap_name, "CET-D1")
                    finally:
                        reopened.close()
                self.assertEqual(scan.z_map.values, {(0, 0): 12})
                for key in ("Scan hopping + CV", "Scan hopping + I-t"):
                    page = window.pages[key]
                    self.assertFalse(hasattr(page, "footprint"))
                    self.assertFalse(hasattr(page, "map_view"))
                    self.assertEqual(page.parameters().footprint_diameter_um, 2.5)
                    self.assertEqual(page.z_map.view_mode, "circular")
                    self.assertEqual(page.current_map.footprint_diameter_um, 2.5)
                    self.assertEqual(page.z_map.colormap_name, "cividis")
                    self.assertEqual(page.current_map.colormap_name, "CET-D1")
                settings_page.map_view.setCurrentText("Square cells")
                window.apply_settings(settings_page.values())
                self.assertEqual(scan.z_map.view_mode, "square")
        finally:
            window.close()

    def test_wrapped_contact_text_fits_on_all_approach_pages(self) -> None:
        window = EChemTipsApp()
        try:
            window.show()
            for width in (1080, 1440):
                window.resize(width, 680)
                for key in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                    window.show_page(key)
                    for _ in range(4):
                        self.qt_app.processEvents()
                    text = window.pages[key].findChild(QtWidgets.QLabel, "contactHelp")
                    self.assertGreaterEqual(text.height(), text.heightForWidth(text.width()), key)
        finally:
            window.close()

    def test_profile_annotations_fit_inside_plot(self) -> None:
        diagram = ProgramDiagram("Potential E1 (V)")
        try:
            diagram.show()
            for width in (330, 600):
                diagram.resize(width, diagram.height())
                for values in ([10, 90, 10], [-0.2, 0.6, -0.4, -0.2], [0, 0, 0], [-10, -2, -5]):
                    for stepped in (False, True):
                        diagram.set_profile(values, [str(i) for i in range(len(values))], stepped=stepped)
                        for _ in range(4):
                            self.qt_app.processEvents()
                        bounds = diagram.graph.getViewBox().sceneBoundingRect()
                        for item in diagram.labels:
                            self.assertTrue(bounds.contains(item.sceneBoundingRect()), (values, bounds, item.sceneBoundingRect()))
        finally:
            diagram.close()

    def test_context_help_and_removed_clutter(self) -> None:
        window = EChemTipsApp()
        try:
            self.assertEqual(
                {info.accessibleName() for info in window.findChildren(InfoButton)},
                {"Pipette and electrolyte help", "Current at selected potential help",
                 "Mean pulse current help", "Acquisition help",
                 "Command voltage ratio help", "Display help"},
            )
            self.assertEqual(len(window.findChildren(InfoButton)), 8)
            for key in ("Approach", "Approach + CV", "Approach + I-t", "CV", "Watch current", "Watch position"):
                self.assertEqual(window.pages[key].findChildren(InfoButton), [])
            notes = [w.text() for w in window.findChildren(QtWidgets.QLabel)]
            for removed in ("FPGA logic preserved", "PySide6 · PyQtGraph", "Restarts when a new approach begins.", "All approach samples from the latest 60 seconds; complete data remain recorded."):
                self.assertNotIn(removed, notes)
            info = window.pages["Settings"].command_ratio_help
            self.assertIsInstance(info, InfoButton)
            self.assertEqual(info.focusPolicy(), QtCore.Qt.FocusPolicy.StrongFocus)
            info.click()
            self.qt_app.processEvents()
            self.assertTrue(info._help_dialog.isVisible())
            self.assertTrue(info.accessibleDescription())
            info._help_dialog.close()
        finally:
            window.close()

    def test_experiment_action_terminology(self) -> None:
        window = EChemTipsApp()
        try:
            for key in ("Scan hopping + CV", "Scan hopping + I-t"):
                self.assertEqual(window.pages[key].status.start_button.text(), "Start scan")
                self.assertEqual(window.pages[key].status.stop_button.text(), "Stop experiment")
            self.assertIn("I–t", window.nav_buttons["Approach + I-t"].text())
            self.assertEqual(window.pages["CV"].status.start_button.text(), "Start sweep")
        finally:
            window.close()

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = create_application([])

    def test_construction_does_not_show_temporary_windows(self) -> None:
        class WindowShows(QtCore.QObject):
            def __init__(self):
                super().__init__()
                self.shown = []

            def eventFilter(self, obj, event):
                if (event.type() == QtCore.QEvent.Type.Show
                        and isinstance(obj, QtWidgets.QWidget) and obj.isWindow()):
                    self.shown.append(type(obj).__name__)
                return False

        observer = WindowShows()
        self.qt_app.installEventFilter(observer)
        window = None
        try:
            window = EChemTipsApp()
            self.qt_app.processEvents()
            self.assertEqual(observer.shown, [])
            window.show()
            self.qt_app.processEvents()
            self.assertEqual(observer.shown, ["EChemTipsApp"])
        finally:
            self.qt_app.removeEventFilter(observer)
            if window is not None:
                window.close()

    def test_control_pages_fit_minimum_window_and_keep_actions_accessible(self) -> None:
        window = EChemTipsApp()
        window.resize(1080, 680)
        window.show()
        self.qt_app.processEvents()
        try:
            self.assertEqual(window.connect_button.text(), "Connect")
            self.assertEqual(tuple(window.pages), window.PAGE_NAMES)
            for name, page in window.pages.items():
                window.show_page(name)
                self.qt_app.processEvents()
                self.assertTrue(page.isVisible(), name)
                self.assertGreater(page.width(), 0, name)
                self.assertGreater(page.height(), 0, name)
                self.assertTrue(window.instrument_readout.isVisible(), name)
            settings = window.pages["Settings"]
            viewport = settings.tab_scrolls["Connection"]
            self.assertEqual(
                viewport.horizontalScrollBarPolicy(),
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            settings_text = " ".join(
                item.text() for item in settings.findChildren(QtWidgets.QLabel)
            )
            self.assertNotIn("Instrument profile", settings_text)
            self.assertNotIn("External lock-in", settings_text)
            self.assertNotIn("Advanced FPGA feedback", settings_text)
            self.assertNotIn("Calibration provenance", settings_text)
            self.assertIn("Current amplifiers and potential command", settings_text)
            self.assertEqual(settings.polarity.get(), "IUPAC")
            self.assertEqual(settings.values().polarity_convention, "IUPAC")
            settings.polarity.setCurrentText("Instrument-native")
            self.assertEqual(settings.values().polarity_convention, "Instrument-native")
            self.assertIn("Samples averaged per data point", settings_text)
            self.assertNotIn("Samples per point", settings_text)
            self.assertIn("5:1", settings.command_ratio_help.toolTip())
            settings.command_ratio.entry.setText("5")
            self.assertIn("±2 V", settings.command_ratio_summary.text())
            self.assertEqual(settings.save_defaults_button.text(), "Save as defaults and apply")
            self.assertIn(str(window.store.path), settings.settings_path_label.text())
            settings.save_directory.variable.set("relative-data")
            self.assertTrue(QtCore.QFileInfo(settings.values().save_directory).isAbsolute())
            for page_name in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                selector = window.pages[page_name].feedback_channel
                self.assertEqual(
                    tuple(selector.itemText(index) for index in range(selector.count())),
                    ("Current 1", "Current 2"),
                )
                threshold = window.pages[page_name].threshold
                self.assertEqual(threshold.unit_label.text(), "pA")
                self.assertAlmostEqual(float(threshold.variable.get()), 5.0)
                page = window.pages[page_name]
                self.assertFalse(hasattr(page, "feedback_mode"))
                self.assertEqual(page.parameters().feedback_mode, "magnitude")
                contact_help = page.findChild(QtWidgets.QLabel, "contactHelp")
                self.assertIsNotNone(contact_help)
                self.assertIn("next step starts automatically", contact_help.text())
                self.assertGreaterEqual(contact_help.minimumHeight(), contact_help.fontMetrics().lineSpacing() * 2)
                self.assertAlmostEqual(page.parameters().settling_time_s, 0.5)
                self.assertEqual(
                    window.pages[page_name].accept_approach_button.text(),
                "Accept contact",
                )
            self.assertAlmostEqual(window.pages["Approach"].parameters().feedback_threshold, 0.005)
            self.assertAlmostEqual(window.pages["Approach + CV"].parameters().feedback_threshold_na, 0.005)
            approach_cv = window.pages["Approach + CV"]
            approach_cv.x_position.entry.setText("25")
            approach_cv.y_position.entry.setText("35")
            self.assertEqual((approach_cv.parameters().x_um, approach_cv.parameters().y_um), (25.0, 35.0))
            self.assertEqual(window.next_waypoint_button.text(), "End waypoint")
            self.assertIn("does not confirm contact", window.next_waypoint_button.toolTip())
            for page_name in ("Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                curve = window.pages[page_name].approach_curve
                self.assertEqual(curve.graph.getAxis("bottom").labelText, "Z position (µm)")
                history = window.pages[page_name].approach_history
                self.assertEqual(history.graph.getAxis("bottom").labelText, "Z position (µm)")
                self.assertEqual(history.history_window_s, 60)
            for page_name in ("CV", "Approach", "Approach + CV", "Approach + I-t", "Scan hopping + CV", "Scan hopping + I-t"):
                preview = window.pages[page_name].program_preview
                self.assertGreater(len(preview.labels), 0, page_name)
            for page_name in ("Scan hopping + CV", "Scan hopping + I-t"):
                scan_page = window.pages[page_name]
                self.assertIn("X 15 µm · Y 15 µm", scan_page.spacing_label.text())
                self.assertIn("plus the first approach", scan_page.duration_label.text())
                self.assertEqual(
                    tuple(scan_page.scan_pattern.itemText(index) for index in range(scan_page.scan_pattern.count())),
                    ("Serpentine", "Raster"),
                )
                self.assertTrue(scan_page.line_retract.entry.isEnabled())
                scan_page.marker_enabled.setChecked(False)
                self.assertFalse(scan_page.line_retract.entry.isEnabled())
                scan_page.scan_pattern.setCurrentText("Raster")
                self.assertTrue(scan_page.line_retract.entry.isEnabled())
                self.assertFalse(scan_page.parameters().serpentine)
                self.assertEqual(scan_page.start_z.findChildren(QtWidgets.QLabel)[0].text(), "Initial approach Z")
                self.assertEqual(scan_page.retract_distance.findChildren(QtWidgets.QLabel)[0].text(), "Retract distance from contact")
                self.assertAlmostEqual(scan_page.parameters().retract_distance_um, 10.0)
                self.assertEqual(scan_page.z_plot.rolling_window_s, 60)
                self.assertEqual(scan_page.current_plot.rolling_window_s, 60)
                map_index = next(
                    index for index in range(scan_page.visual_tabs.count())
                    if scan_page.visual_tabs.tabText(index) == "Maps"
                )
                window.show_page(page_name)
                scan_page.visual_tabs.setCurrentIndex(map_index)
                self.qt_app.processEvents()
                self.assertIsNone(scan_page.visual_tabs.cornerWidget(QtCore.Qt.Corner.TopRightCorner))
            approach_cv_tabs = approach_cv.findChildren(QtWidgets.QTabWidget)[0]
            self.assertEqual(
                tuple(approach_cv_tabs.tabText(index) for index in range(approach_cv_tabs.count())),
                ("Experiment traces", "CV / LSV", "Approach curves"),
            )
            expected_sections = {
                "Approach": ("1 · Z movement", "2 · Contact detection", "3 · Optional XY preposition"),
                "Approach + CV": ("1 · Approach", "2 · Optional XY preposition", "3 · Voltammetry"),
                "Approach + I-t": ("1 · Z movement", "2 · Contact detection", "3 · Optional XY preposition", "4 · I–t potential program"),
                "Scan hopping + CV": ("1 · Scan area and path", "2 · Motion and contact", "3 · Voltammetry"),
                "Scan hopping + I-t": ("1 · Scan area and path", "2 · Motion and contact", "3 · I–t potential program"),
            }
            for page_name, sections in expected_sections.items():
                page_text = {item.text() for item in window.pages[page_name].findChildren(QtWidgets.QLabel)}
                for section in sections:
                    self.assertIn(section, page_text, f"{page_name}: {section}")
            watch = window.pages["Watch current"]
            self.assertEqual(watch.stop_recording_button.text(), "Stop and save")
            self.assertEqual(watch.live_button.text(), "Start live view")
            self.assertIsNot(watch.current1_plot, watch.current2_plot)
            self.assertEqual(watch.current1_plot.graph.getAxis("left").labelText, "Current 1 (nA)")
            self.assertEqual(watch.current2_plot.graph.getAxis("left").labelText, "Current 2 (nA)")
            self.assertEqual((watch.current1_plot.rolling_window_s, watch.current2_plot.rolling_window_s), (30, 30))
            monitor = window.pages["Watch position"]
            self.assertEqual(
                tuple(plot.graph.getAxis("left").labelText for plot in (monitor.x_plot, monitor.y_plot, monitor.z_plot)),
                ("X position (µm)", "Y position (µm)", "Z position (µm)"),
            )
            self.assertEqual(
                tuple(plot.rolling_window_s for plot in (monitor.x_plot, monitor.y_plot, monitor.z_plot)),
                (30, 30, 30),
            )
            move = window.pages["Move piezo"]
            self.assertEqual(
                tuple(move.axis.itemText(index) for index in range(move.axis.count())),
                ("X", "Y", "Z"),
            )
            self.assertIn("Preflight", window.pages)
            self.assertIn("Characterize pipette", window.pages)
        finally:
            window.poll_timer.stop()
            window.close()

    def test_analysis_uses_separate_raw_and_voltammogram_tabs(self) -> None:
        window = AnalysisWindow()
        window.resize(1080, 680)
        window.show()
        self.qt_app.processEvents()
        try:
            labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
            self.assertEqual(labels, ["Explore and measure", "CV / LSV", "Hop maps", "Map movie", "Raw data table", "Metadata"])
            self.assertIsNot(window.raw_current_plot, window.cv_plot)
            window.tabs.setCurrentIndex(1)
            self.qt_app.processEvents()
            self.assertTrue(window.export_button.isVisible())
        finally:
            window.close()

    def test_instrument_readout_tracks_latest_sample_and_clears_on_disconnect(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        sample = Sample(1.0, 12.3456, 23.4567, 34.5678, -0.2, 0.4, 1.25, -0.75)
        try:
            window._consume_acquired([sample], finalize=False)
            values = window.instrument_readout.value_labels
            self.assertEqual(values["x_um"].text(), "12.346 µm")
            self.assertEqual(values["y_um"].text(), "23.457 µm")
            self.assertEqual(values["z_um"].text(), "34.568 µm")
            self.assertEqual(values["voltage1_v"].text(), "-0.200 V")
            self.assertEqual(values["voltage2_v"].text(), "+0.400 V")
            self.assertEqual(values["current1_na"].text(), "+1.250 nA")
            self.assertEqual(values["current2_na"].text(), "-0.750 nA")

            window._set_connection_ui(False)
            self.assertEqual(values["x_um"].text(), "— µm")
            self.assertEqual(values["current2_na"].text(), "— nA")
        finally:
            window.close()

    def test_fpga_startup_warning_must_be_accepted_before_connecting(self) -> None:
        class StartupBackend(SimulationBackend):
            allow_startup_actuation = False

            @property
            def startup_notice(self) -> str:
                return "Starting the FPGA changes physical outputs."

            def connect(self, *, allow_startup_actuation: bool = False) -> None:
                self.allow_startup_actuation = allow_startup_actuation
                if allow_startup_actuation:
                    super().connect()

        window = EChemTipsApp()
        window.poll_timer.stop()
        backend = StartupBackend(AppSettings())
        window.backend = backend
        try:
            with patch.object(
                QtWidgets.QMessageBox,
                "warning",
                return_value=QtWidgets.QMessageBox.StandardButton.Cancel,
            ):
                window.toggle_connection()
            self.assertFalse(backend.connected)
            self.assertFalse(backend.allow_startup_actuation)

            with patch.object(
                QtWidgets.QMessageBox,
                "warning",
                return_value=QtWidgets.QMessageBox.StandardButton.Yes,
            ):
                window.toggle_connection()
            self.assertTrue(backend.connected)
            self.assertTrue(backend.allow_startup_actuation)
        finally:
            window._stop_acquisition()
            backend.disconnect()
            window.close()

    def test_heatmap_has_compact_labelled_scale(self) -> None:
        heatmap = Heatmap("nA", "Current 1")
        heatmap.resize(500, 420)
        heatmap.show()
        heatmap.set_data({(0, 0): 1.5, (0, 1): 2.5}, 1, 2)
        self.qt_app.processEvents()
        try:
            self.assertFalse(heatmap.color_bar.getAxis("left").label.isVisible())
            self.assertEqual(heatmap.color_bar.axis.labelText, "Current 1 (nA)")
            self.assertEqual(heatmap.color_bar.levels(), (1.5, 2.5))
            self.assertFalse(hasattr(heatmap.view, "ui"))
            self.assertIs(heatmap.summary.parentWidget(), heatmap.footer)
            self.assertIs(heatmap.hover.parentWidget(), heatmap.footer)
            self.assertGreaterEqual(heatmap.footer.geometry().top(), heatmap.view.geometry().bottom())
            self.assertTrue(heatmap.summary.text().startswith("Range 1.5–2.5 nA"))
            heatmap.set_data({(0, 0): 1.5, (0, 1): 2.5}, 1, 2,
                             x_values=[35.0, 40.0], y_values=[12.0],
                             view_mode="circular", footprint_diameter_um=1.5)
            self.assertEqual(heatmap.plot_item.getAxis("bottom").labelText, "X position")
            self.assertEqual(heatmap.plot_item.getAxis("left").labelText, "Y position")
            self.assertFalse(heatmap.image_item.isVisible())
            self.assertTrue(heatmap.footprint_item.isVisible())
            self.assertEqual(len(heatmap.footprint_item.points()), 2)
        finally:
            heatmap.close()

    def test_laptop_plot_panels_and_expanded_view(self) -> None:
        from echemtips.qt_common import PlotPanel
        window = EChemTipsApp()
        window.resize(1280, 720)
        window.show()
        try:
            for name in ("Approach", "Approach + CV", "Approach + I-t",
                         "Scan hopping + CV", "Scan hopping + I-t"):
                window.show_page(name)
                page = window.pages[name]
                tabs = page.body.findChild(QtWidgets.QTabWidget)
                for index in range(tabs.count()):
                    panel = tabs.widget(index)
                    if not isinstance(panel, PlotPanel):
                        continue
                    tabs.setCurrentIndex(index)
                    for _ in range(4):
                        self.qt_app.processEvents()
                    self.assertEqual(panel.widget().layout().direction(),
                                     QtWidgets.QBoxLayout.Direction.TopToBottom)
                    for card in panel._cards:
                        self.assertGreaterEqual(card.height(), card.minimumHeight())
                    self.assertGreater(panel.verticalScrollBar().maximum(), 0)
                page.setup_toggle.click()
                self.qt_app.processEvents()
                self.assertFalse(page.body.layout().itemAt(0).widget().isVisible())
                page.expand_plots.click()
                self.qt_app.processEvents()
                self.assertTrue(page._plots_dialog.isVisible())
                self.assertTrue(page._plots_dialog.isAncestorOf(tabs))
                self.assertIn("EMERGENCY STOP", [b.text() for b in
                              page._plots_dialog.findChildren(QtWidgets.QPushButton)])
                page._plots_dialog.close()
                self.qt_app.processEvents()
                self.assertTrue(page.body.isAncestorOf(tabs))
                page.setup_toggle.click()
        finally:
            window.close()

    def test_rolling_plot_discards_only_old_display_points(self) -> None:
        plot = Plot("Rolling", "Value", ("#12877f",), max_points=1000, rolling_window_s=120)
        try:
            for second in range(301):
                plot.append(float(second), float(second), redraw=False)
            plot.redraw()
            self.assertGreaterEqual(plot.x_values[0], 180.0)
            self.assertEqual(plot.x_values[-1], 300.0)
            self.assertLessEqual(len(plot.x_values), 121)
        finally:
            plot.close()

    def test_scan_trace_elapsed_time_restarts_with_each_scan(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        page = window.pages["Scan hopping + CV"]
        try:
            page._elapsed_origin_s = None
            first = Sample(712.5, 35, 35, 55, 0.1, 0, 1, 0)
            second = Sample(713.0, 35, 35, 56, 0.1, 0, 1.1, 0)
            self.assertEqual(page.elapsed_from_start(first), 0.0)
            self.assertEqual(page.elapsed_from_start(second), 0.5)
            page._elapsed_origin_s = None
            self.assertEqual(page.elapsed_from_start(Sample(900, 35, 35, 55, 0.1, 0, 1, 0)), 0.0)
        finally:
            window.close()

    def test_current_vs_z_history_uses_time_for_its_rolling_window(self) -> None:
        plot = TimedXYPlot("History", "Current (nA)", "#12877f", 1000, "Z position (µm)", 60)
        try:
            plot.append_timed(0, 50, 1, redraw=False)
            plot.append_timed(30, 55, 2, redraw=False)
            plot.add_gap(61)
            plot.append_timed(61, 60, 3, redraw=False)
            plot.redraw()
            self.assertEqual(plot.clock_values, [30, 61, 61])
            self.assertEqual(plot.x_values[0], 55)
            self.assertTrue(math.isnan(plot.x_values[1]))
            self.assertEqual(plot.series[0][-1], 3)
        finally:
            plot.close()

    def test_watch_current_only_plots_during_an_explicit_live_session(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        watch = window.pages["Watch current"]
        first = Sample(1.0, 50, 50, 50, 0.1, 0, 1.25, 0.2)
        second = Sample(2.0, 50, 50, 50, 0.1, 0, 1.50, 0.2)
        try:
            self.assertFalse(watch.live_enabled)
            watch.on_samples([first])
            self.assertEqual(len(watch.current1_plot.x_values), 0)

            watch.set_live_view(True)
            watch.on_samples([first])
            self.assertEqual(len(watch.current1_plot.x_values), 1)
            self.assertEqual(len(watch.current2_plot.x_values), 1)
            self.assertEqual(watch.current_label.text(), "i1  +1.250 nA")
            self.assertEqual(watch.current2_label.text(), "i2  +0.200 nA")

            watch.set_live_view(False)
            watch.on_samples([second])
            self.assertEqual(len(watch.current1_plot.x_values), 1)
            self.assertEqual(watch.current_label.text(), "i1  +1.250 nA")

            watch.set_live_view(True)
            self.assertEqual(len(watch.current1_plot.x_values), 0)
            self.assertEqual(len(watch.current2_plot.x_values), 0)
            self.assertEqual(watch.live_button.text(), "Stop live view")
        finally:
            window.close()

    def test_watch_position_is_separate_and_opt_in(self) -> None:
        window = EChemTipsApp()
        window.poll_timer.stop()
        monitor = window.pages["Watch position"]
        sample = Sample(1.0, 12.0, 23.0, 34.0, 0.1, 0.0, 1.25, 0.2)
        try:
            self.assertFalse(monitor.live_enabled)
            monitor.on_samples([sample])
            self.assertEqual(len(monitor.x_plot.x_values), 0)
            monitor.set_live_view(True)
            monitor.on_samples([sample])
            self.assertEqual(monitor.x_plot.series[0][-1], 12.0)
            self.assertEqual(monitor.y_plot.series[0][-1], 23.0)
            self.assertEqual(monitor.z_plot.series[0][-1], 34.0)
            self.assertEqual(monitor.x_plot.x_values[-1], 0.0)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
