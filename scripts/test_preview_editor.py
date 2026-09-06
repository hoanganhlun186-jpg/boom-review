"""Native preview integration checks, without reading/writing user settings."""
import os
os.environ['QT_QPA_PLATFORM'] = 'windows' if os.name=='nt' else 'offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QMouseEvent, QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtMultimedia import QMediaPlayer
from utils.helpers import FFmpegUtils
import main


class PreviewApp(main.App):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_DontShowOnScreen,True)
    def load_config(self): return {}
    def save_config(self,data): self.saved = dict(data)
    def _load_saved_config(self): pass


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_run_button_logs_and_dispatches_pipeline(self):
        app = PreviewApp()
        try:
            self.assertIsNone(app._make_script_review_callback())
            self.assertEqual(app._selected_capcut_srt_mode(),'auto')
            app._on_capcut_srt_mode_change(app.CAPCUT_SRT_MANUAL)
            self.assertEqual(app._selected_capcut_srt_mode(),'manual')
            app._on_capcut_srt_mode_change(app.CAPCUT_SRT_AUTO)
            self.assertEqual(app._selected_capcut_srt_mode(),'auto')
            app._manual_script_review.setChecked(True)
            self.assertTrue(callable(app._make_script_review_callback()))
            app._manual_script_review.setChecked(False)
            app.log.insert('end','Dòng đầu\n')
            app.log.moveCursor(main.QTextCursor.MoveOperation.Start)
            app.log.insert('end','Dòng cuối\n')
            self.assertTrue(app.log.toPlainText().endswith('Dòng đầu\nDòng cuối\n'))
            with tempfile.TemporaryDirectory(dir=ROOT) as folder:
                video = Path(folder)/'input.mp4'
                video.touch()
                app.video_path.setText(str(video))
                with patch.object(app,'_ensure_video_output_dir',return_value=folder), \
                     patch('main.threading.Thread') as thread:
                    app.btn_run.click()
                    thread.assert_called_once()
                    self.assertEqual(thread.call_args.kwargs['target'],app._full_pipeline_worker)
                    self.assertEqual(thread.call_args.kwargs['args'],(str(video),folder))
                    thread.return_value.start.assert_called_once()
                self.assertIn('Full Pipeline: METADATA',app.log.toPlainText())
        finally:
            app.close()

    def drag(self, view, item, delta, handle=None):
        if handle: item.setSelected(True)
        point = item.rect.center() if handle is None else item.handles()[handle].center()
        start = view.mapFromScene(item.mapToScene(point))
        QTest.mousePress(view.viewport(),Qt.LeftButton,pos=start)
        self.assertIsNotNone(item.drag)
        target = QPointF(start)+QPointF(*delta)
        event = QMouseEvent(QEvent.MouseMove,target,target,Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
        self.qt.sendEvent(view.viewport(),event)
        QTest.mouseRelease(view.viewport(),Qt.LeftButton,pos=target.toPoint())

    def test_initial_titles_sample_and_audio(self):
        app = PreviewApp()
        try:
            self.assertEqual(app._visible_title('header'),'CHƯA CÓ TIÊU ĐỀ')
            self.assertEqual(app._visible_title('footer'),'XEM NGAY KẾT CỤC')
            self.assertTrue(app.preview_canvas.title_items)
            with patch('ui.preview_editor.QFileDialog.getOpenFileName',side_effect=AssertionError('No picker for sample')):
                app._preview_choose_srt()
            self.assertTrue(app.preview_canvas.subtitle.isVisible())
            self.assertEqual(app.burn_srt_path_var,'')
            self.assertIn('Chữ mẫu',app.preview_canvas._caption_key[0])
            app._burn_outline_spin.setValue(6)
            self.assertEqual(app.preview_canvas.subtitle.outline,6)
            self.assertEqual(app._preview_design_settings()['sub_outline'],6)
            self.assertEqual(app.saved['burn_sub_outline'],6)
            app._burn_outline_spin.setValue(0)
            self.assertEqual(app.preview_canvas.subtitle.outline,0)
            app._sub_font_combo.setCurrentText('Tahoma')
            self.assertEqual(app._preview_design_settings()['sub_font'],'Tahoma')
            app._sub_background_opacity.setValue(80)
            self.assertEqual(app.preview_canvas.subtitle.background.alpha(),204)
            app._sub_background_check.setChecked(False)
            self.assertEqual(app.preview_canvas.subtitle.background.alpha(),0)
            app._sub_background_check.setChecked(True)
            self.assertFalse(app.preview_canvas.audio.isMuted())
            app._preview_audio_checkbox.setChecked(False)
            self.assertTrue(app.preview_canvas.audio.isMuted())
            app._preview_audio_checkbox.setChecked(True)
            self.assertFalse(app.preview_canvas.audio.isMuted())
            with patch.object(app,'load_config',return_value={'header_text':'Đã lưu','footer_text':'',
                              'header_visible':True,'footer_visible':False,'preview_audio_enabled':False,
                              'burn_sub_outline':4}):
                main.App._load_saved_config(app)
            app.update_preview()
            self.assertEqual(app._visible_title('header'),'Đã lưu')
            self.assertEqual(app.footer_text.get(),'')
            self.assertFalse(app._footer_visible.isChecked())
            self.assertTrue(app.preview_canvas.audio.isMuted())
            self.assertEqual(app.preview_canvas.subtitle.outline,4)
        finally:
            app.close()

    def test_drag_logo_sub_blur_and_titles(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = Path(tmp)/'logo.png'
            image = QImage(200,100,QImage.Format_ARGB32)
            image.fill(QColor('red'))
            image.save(str(path))
            app = PreviewApp()
            try:
                app.show()
                app.logo_path_var = str(path)
                app.burn_srt_enabled = True
                app.delogo_enabled = True
                app.header_text.setText('Tiêu đề trên')
                app.footer_text.setText('Tiêu đề dưới')
                view = app.preview_canvas
                for w,h in [(1920,1080),(1080,1920)]:
                    view.set_source_size(w,h)
                    app.delogo_boxes = [dict(x=.1,y=.3,w=.4,h=.1)]
                    app.update_preview()
                    self.qt.processEvents()
                    view.fit_frame()
                    self.assertTrue(view.logo.isVisible())
                    self.assertFalse(view.logo.pixmap.isNull())
                    before = app.logo_x_ratio
                    self.drag(view,view.logo,(-25,20))
                    self.assertLess(app.logo_x_ratio,before)
                    old = app.logo_size_pct_var
                    self.drag(view,view.logo,(12,8),'br')
                    self.assertGreater(app.logo_size_pct_var,old)
                    old = app.burn_sub_y_ratio
                    self.drag(view,view.subtitle,(0,-25))
                    self.assertLess(app.burn_sub_y_ratio,old)
                    old = app.burn_sub_fontsize_var
                    self.drag(view,view.subtitle,(15,10),'br')
                    self.assertGreater(app.burn_sub_fontsize_var,old)
                    self.drag(view,view.blurs[0],(15,15),'br')
                    self.assertGreater(app.delogo_boxes[0]['w'],.4)
                    self.assertGreater(app.delogo_boxes[0]['h'],.1)
                    self.assertGreaterEqual(view.mapFromScene(view.sceneRect().topLeft()).y(),-1)
                for top,bottom in [(False,False),(True,False),(False,True),(True,True)]:
                    before_logo = view.logo.pos()
                    before_sub = view.subtitle.pos()
                    before_blur = view.blurs[0].pos()
                    app._header_visible.setChecked(top)
                    app._footer_visible.setChecked(bottom)
                    self.assertEqual(bool(app._visible_title('header')),top)
                    self.assertEqual(bool(app._visible_title('footer')),bottom)
                    self.assertEqual(app.header_text.get(),'Tiêu đề trên')
                    self.assertEqual(app.footer_text.get(),'Tiêu đề dưới')
                    self.assertEqual(view.content_rect.top(),0)
                    self.assertEqual(view.sceneRect(),view.content_rect)
                    self.assertEqual(view.sceneRect().height(),1920)
                    self.assertLess((view.logo.pos()-before_logo).manhattanLength(),1)
                    self.assertLess((view.subtitle.pos()-before_sub).manhattanLength(),2)
                    self.assertLess((view.blurs[0].pos()-before_blur).manhattanLength(),1)
                    self.assertEqual(bool(view.title_items),top or bottom)
                    bars = [item.rect() for item in view.title_items if hasattr(item,'rect')]
                    if top: self.assertEqual(bars[0].top(),0)
                    if bottom: self.assertEqual(bars[-1].bottom(),view.height_px)
                actual = Path('D:/hongguo/ChatGPT Image 08_37_32 11 thg 8, 2026.png')
                if actual.is_file():
                    app.logo_path_var = str(actual)
                    app.update_preview()
                    self.assertTrue(view.logo.isVisible())
                    self.assertFalse(view.logo.pixmap.isNull())
                out = ROOT/'exports'/'preview_native_check.png'
                out.parent.mkdir(exist_ok=True)
                view.grab().save(str(out))
            finally:
                app.close()

    def test_native_drag_keeps_latest_position_without_layout_or_save(self):
        app = PreviewApp()
        try:
            app.show()
            app._preview_choose_srt()
            self.qt.processEvents()
            view,item = app.preview_canvas,app.preview_canvas.subtitle
            start = view.mapFromScene(item.mapToScene(item.rect.center()))
            expected = view.mapToScene(start)-item.pos()
            QTest.mousePress(view.viewport(),Qt.LeftButton,pos=start)
            with patch.object(view,'sync',wraps=view.sync) as sync, patch.object(app,'save_config') as save:
                old_size,old_scale = app.burn_sub_fontsize_var,item.scale()
                with patch.object(item,'set_caption',wraps=item.set_caption) as layout:
                    for offset in range(1,201):
                        target = QPointF(start)+QPointF(offset*.1,-offset*.1)
                        self.qt.sendEvent(view.viewport(),QMouseEvent(QEvent.MouseMove,target,target,
                                          Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
                    self.assertNotEqual(item.pos(),view.mapToScene(start)-expected)
                    QTest.mouseRelease(view.viewport(),Qt.LeftButton,pos=target.toPoint())
                    self.assertEqual(layout.call_count,0)
                    self.assertEqual(app.burn_sub_fontsize_var,old_size)
                    self.assertEqual(item.scale(),old_scale)
                actual = view.mapToScene(target.toPoint())-expected
                self.assertAlmostEqual(item.pos().x(),actual.x(),delta=2)
                self.assertAlmostEqual(item.pos().y(),actual.y(),delta=2)
                self.assertEqual(sync.call_count,0)
                self.assertEqual(save.call_count,0,'No disk writes inside mouse release')
                app._flush_preview_save()
                self.assertEqual(save.call_count,1)
        finally:
            app.close()

    def test_native_playback_does_not_rebuild_scene(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            video = Path(tmp)/'portrait.mp4'
            subprocess.run([FFmpegUtils.ffmpeg_executable(),'-y','-v','error','-f','lavfi',
                            '-i','testsrc2=s=1080x1920:r=25:d=3','-f','lavfi','-i','sine=duration=3',
                            '-c:a','aac','-c:v','libx264','-preset','ultrafast',str(video)],
                           check=True,**FFmpegUtils.subprocess_kwargs(capture_output=True))
            app = PreviewApp()
            try:
                app.show()
                app.video_path.setText(str(video))
                app._preview_audio_checkbox.setChecked(False)
                app.burn_srt_enabled = True
                app.update_preview()
                view = app.preview_canvas
                app._load_native_preview(str(video))
                deadline=time.monotonic()+5
                while view.player.duration()==0 and time.monotonic()<deadline:
                    QTest.qWait(50)
                self.assertGreater(view.player.duration(),0,view.player.errorString())
                self.assertTrue(view.player.hasAudio())
                app._start_vplay()
                QTest.qWait(350)
                self.assertTrue(view.video.videoSink().videoFrame().isValid(),view.player.errorString())
                with patch.object(view,'sync',wraps=view.sync) as sync:
                    app._start_vplay()
                    QTest.qWait(450)
                    self.assertGreater(view.player.position(),0)
                    self.assertEqual(sync.call_count,0,'Playback must not rebuild overlays')
                    self.drag(view,view.subtitle,(0,-20))
                    self.assertLessEqual(sync.call_count,1,'Only release may synchronize settings')
                    self.assertEqual(view.player.playbackState(),QMediaPlayer.PlayingState)
                app._on_vplay_scrub(700)
                QTest.qWait(250)
                self.assertGreater(view.player.position(),1800)
                app._stop_vplay()
                self.assertEqual(view.player.playbackState(),QMediaPlayer.PausedState)
            finally:
                app.close()
                QTest.qWait(50)


if __name__=='__main__':
    unittest.main()
