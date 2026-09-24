"""Movie UI smoke tests and an actual optional FFmpeg encode/decode check."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from PySide6 import QtWidgets
from echemtips.analysis_frames import prepare_frames
from echemtips.analysis_movie import MoviePanel, export_movie, render_frame
from echemtips.analysis_tools import cv_selections, hop_selections
from echemtips.analysis_core import AnalysisError
from tests.test_analysis_frames import cv_dataset

APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

class MovieTests(unittest.TestCase):
    def test_prepare_scrub_and_manual_colour_controls(self):
        d=cv_dataset(); panel=MoviePanel(); panel.resize(960,700); panel.show()
        try:
            panel.set_dataset(d,dict(cv=cv_selections(d),hops=hop_selections(d)))
            panel.count.setValue(5); panel.build()
            deadline=time.monotonic()+5
            while panel.frames is None and time.monotonic()<deadline:
                APP.processEvents(); time.sleep(.01)
            self.assertIsNotNone(panel.frames,panel.notice.text())
            panel.slider.setValue(2); APP.processEvents()
            self.assertIn("Frame 3/5",panel.notice.text())
            panel.colour.setCurrentText("Manual"); panel.low.setValue(-2); panel.high.setValue(2)
            self.assertEqual(panel.map.fixed_limits,(-2,2))
            panel.toggle(); self.assertTrue(panel.timer.isActive())
            panel.kind.setCurrentText("CV time")
            self.assertFalse(panel.timer.isActive()); self.assertIsNone(panel.frames)
        finally: panel.shutdown(); panel.close()

    def test_render_has_fixed_even_frame_size(self):
        d=cv_dataset(); frames=prepare_frames(d,cv_selections(d),count=3)
        data=render_frame(frames,0,"viridis",frames.limits())
        self.assertEqual(len(data),960*720*3)

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg not installed")
    def test_real_mp4_frame_count_and_cancellation_preserves_existing_file(self):
        d=cv_dataset(); frames=prepare_frames(d,cv_selections(d),count=3)
        with TemporaryDirectory() as folder:
            path=Path(folder)/"movie.mp4"
            export_movie(frames,path,fps=10,hold_ms=200)
            self.assertGreater(path.stat().st_size,1000)
            if shutil.which("ffprobe"):
                info=json.loads(subprocess.check_output(["ffprobe","-v","error","-count_frames","-show_entries","stream=nb_read_frames,width,height","-of","json",str(path)]))
                self.assertEqual(info["streams"][0]["nb_read_frames"],"6")
            self.assertEqual(json.loads(path.with_suffix(".mp4.json").read_text())["fps"],10)
            original=path.read_bytes()
            with self.assertRaises(AnalysisError): export_movie(frames,path,cancelled=lambda:True)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(len(list(Path(folder).glob(".echemtips-movie-*"))),0)

    def test_cv_view_modes_and_explicit_segment_selection(self):
        from echemtips.analysis_window import AnalysisWindow
        with TemporaryDirectory() as folder:
            window=AnalysisWindow(data_folder=Path(folder))
            try:
                d=cv_dataset(); window.dataset=d
                from echemtips.analysis_core import extract_cv_cycles
                window.cycles=extract_cv_cycles(d)
                window.groups=dict(cv=cv_selections(d),hops=hop_selections(d))
                window._refresh_all()
                window.cv_view.setCurrentText("E vs t")
                self.assertEqual(window.cv_plot.x_label,"Time from cycle start (s)")
                self.assertEqual(window.cv_plot.y_label,"Potential E1 (V)")
                window.cv_view.setCurrentText("i vs t")
                self.assertEqual(window.cv_plot.y_label,"Current (nA)")
                panel=window.map_panel; panel.statistic.setCurrentText("CV at potential")
                panel.direction.setCurrentIndex(1); panel.potential.setValue(-.3)
                first=panel.points[0]["value"]
                panel.cycle.setCurrentIndex(panel.cycle.findData(2))
                self.assertAlmostEqual(panel.points[0]["value"]-first,10)
            finally: window.close()
