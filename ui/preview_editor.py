"""Direct preview editing controls shared by the main window."""
import os
from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtMultimedia import QMediaPlayer


class PreviewEditorMixin:
    def closeEvent(self, event):
        editor = getattr(self, '_active_script_editor', None)
        if editor is not None and editor._ui_alive():
            editor._on_cancel()
        bridge = getattr(self, '_tk_editor_bridge', None)
        if bridge is not None:
            bridge.close()
        self._flush_preview_save()
        self._preview_seek_timer.stop()
        self.preview_canvas.player.stop()
        self.preview_canvas.player.setSource(QUrl())
        super().closeEvent(event)

    def _preview_choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Chọn logo', '', 'Hình ảnh (*.png *.webp *.jpg *.jpeg)')
        if path:
            self.logo_path_var = path
            self._logo_entry.setText(path)
            self.save_config({'logo_path': path})
            self.update_preview()

    def _preview_choose_srt(self):
        # The full pipeline generates narration SRT later; no file is needed
        # to position the caption sample in the editor.
        self.burn_srt_enabled = True
        self._burn_srt_var.set(True)
        self._burn_checkbox.setChecked(True)
        self.save_config({'burn_srt_enabled': True})
        self.update_preview()
        self.preview_canvas.subtitle.setSelected(True)

    def _burn_srt_path_edited(self):
        self.burn_srt_path_var = self._burn_srt_entry.text().strip().strip('"')
        self.save_config({'burn_srt_path': self.burn_srt_path_var})
        self.update_preview()

    def _preview_add_blur(self):
        if self.delogo_enabled:
            self.delogo_boxes.append({'x': .1, 'y': .7, 'w': .8, 'h': .12})
        elif not self.delogo_boxes:
            self.delogo_boxes = [{'x': .1, 'y': .75, 'w': .8, 'h': .12}]
        self.delogo_enabled = True
        self._delogo_var.set(True)
        self._delogo_checkbox.setChecked(True)
        self.save_config({'delogo_enabled': True, 'delogo_boxes': self.delogo_boxes})
        self.update_preview()

    def _preview_design_settings(self):
        return {'logo': self.logo_path_var, 'logo_x': self.logo_x_ratio,
                'logo_y': self.logo_y_ratio, 'logo_size': self.logo_size_pct_var,
                'blur_boxes': [dict(b) for b in self.delogo_boxes] if self.delogo_enabled else [],
                'burn': self.burn_srt_enabled, 'srt': self.burn_srt_path_var,
                'sub_size': self.burn_sub_fontsize_var, 'sub_color': self.burn_sub_color_var,
                'sub_outline': self.burn_sub_outline_var,
                'sub_font': self.burn_sub_font_var,
                'sub_background': self.burn_sub_background_var,
                'sub_background_color': self.burn_sub_background_color_var,
                'sub_background_opacity': self.burn_sub_background_opacity_var,
                'sub_x': self.burn_sub_x_ratio, 'sub_y': self.burn_sub_y_ratio}

    def _preview_cues(self):
        from core.preview_design import read_cues
        path = self.burn_srt_path_var
        stamp = (path, os.stat(path).st_mtime_ns) if path and os.path.isfile(path) else None
        if self._preview_cues_cache[0] != stamp:
            self._preview_cues_cache = (stamp, read_cues(path))
        return self._preview_cues_cache[1]

    def _save_sub_position(self):
        self.save_config({'burn_sub_x_ratio': self.burn_sub_x_ratio,
                          'burn_sub_y_ratio': self.burn_sub_y_ratio,
                          'burn_sub_fontsize': self.burn_sub_fontsize_var})

    def _subtitle_style_changed(self, key, value):
        setattr(self,'burn_sub_'+key+'_var',value)
        self.save_config({'burn_sub_'+key:value})
        self.update_preview()

    def _choose_sub_background(self):
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.burn_sub_background_color_var),self,'Màu nền chữ')
        if color.isValid():
            self._subtitle_style_changed('background_color',color.name())
            self._sub_background_color.setStyleSheet('background:'+color.name())

    def _burn_outline_changed(self, value):
        self.burn_sub_outline_var = int(value)
        self.save_config({'burn_sub_outline': self.burn_sub_outline_var})
        self.update_preview()

    def _visible_title(self, name):
        checkbox = getattr(self, '_'+name+'_visible', None)
        entry = getattr(self, name+'_text', None)
        return entry.get().strip() if entry and (checkbox is None or checkbox.isChecked()) else ''

    def _save_title_text(self):
        self.save_config({'header_text': self.header_text.get(),
                          'footer_text': self.footer_text.get()})

    def _preview_audio_changed(self, enabled):
        self.preview_canvas.audio.setMuted(not enabled)
        self.save_config({'preview_audio_enabled': bool(enabled)})

    def _title_visibility_changed(self, _checked):
        if not hasattr(self, '_footer_visible'):
            return
        self.save_config({'header_visible': self._header_visible.isChecked(),
                          'footer_visible': self._footer_visible.isChecked()})
        self.update_preview()

    def _logo_path_edited(self):
        self.logo_path_var = self._logo_entry.text().strip().strip('"')
        self.save_config({'logo_path': self.logo_path_var})
        self.update_preview()

    def _init_native_preview(self):
        view = self.preview_canvas
        view.geometryEdited.connect(self._preview_geometry_edited)
        view.interactionStarted.connect(self._preview_drag_started)
        view.interactionFinished.connect(self._preview_drag_finished)
        view.sourceSizeChanged.connect(self._preview_native_size)
        view.player.positionChanged.connect(self._preview_position)
        view.player.durationChanged.connect(lambda _: self._preview_position(view.player.position()))
        view.player.playbackStateChanged.connect(self._preview_play_state)
        view.player.errorOccurred.connect(lambda error,message: self.preview_info.configure(
            text='Không mở được preview: '+message,text_color='#ef4444'))
        self._preview_resume_after_drag = False
        self._preview_cues_loaded = None
        self._preview_sync_pending = False
        self._pending_preview_save = {}
        self._preview_save_timer = QTimer(self)
        self._preview_save_timer.setSingleShot(True)
        self._preview_save_timer.setInterval(250)
        self._preview_save_timer.timeout.connect(self._flush_preview_save)
        self._preview_seek_timer = QTimer(self)
        self._preview_seek_timer.setSingleShot(True)
        self._preview_seek_timer.setInterval(80)
        self._preview_seek_timer.timeout.connect(self._apply_preview_seek)

    def _preview_native_size(self,width,height):
        self.preview_source_size = (width,height)

    def _sync_graphics_preview(self):
        if not hasattr(self,'preview_canvas'):
            return
        view = self.preview_canvas
        if any(item.drag for item in [view.logo,view.subtitle,*view.blurs]):
            self._preview_sync_pending = True
            return
        titles = {}
        for name in ('header','footer'):
            titles[name] = self._visible_title(name)
            titles[name+'_size'] = getattr(self,name+'_font_size',60)
            titles[name+'_color'] = getattr(self,name+'_color',(255,255,255))
            titles[name+'_bar'] = getattr(self,name+'_bar_color',(0,0,0))
        self.preview_canvas.sync(self._preview_design_settings(),titles)
        cues = self._preview_cues()
        if cues is not self._preview_cues_loaded:
            self._preview_cues_loaded = cues
            self.preview_canvas.set_cues(cues)
        if hasattr(self,'preview_info'):
            if self.logo_path_var and self.preview_canvas.logo.pixmap.isNull():
                self.preview_info.configure(text='Không đọc được ảnh logo. Bấm ＋ Logo để chọn lại ảnh.',text_color='#ef4444')
            else:
                self.preview_info.configure(text='Kéo logo/sub để di chuyển; kéo góc hoặc cuộn chuột để đổi cỡ. '
                    'Khung Mờ # dùng che sub cũ; khung xanh lá là sub mới. Mờ áp dụng khi xuất.',text_color='#94a3b8')

    def _load_native_preview(self,path):
        if not path or not os.path.isfile(path):
            self.preview_info.configure(text='Video không tồn tại.',text_color='#ef4444')
            return
        self.preview_canvas.set_source(path)
        self.update_preview()

    def _ensure_native_source(self):
        path = self.video_path.get().strip() or getattr(self,'current_video_path','')
        if not path or not os.path.isfile(path):
            return False
        self.preview_canvas.set_source(path)
        return True

    def _start_vplay(self):
        if self._ensure_native_source():
            self.preview_canvas.player.play()

    def _stop_vplay(self):
        self.preview_canvas.player.pause()

    def _preview_play_state(self,state):
        playing = state == QMediaPlayer.PlayingState
        self._vplay_paused = not playing
        self._vplay_running = playing
        self._vplay_btn.configure(text='⏸ Pause' if playing else '▶ Play')

    def _preview_position(self,position):
        duration = self.preview_canvas.player.duration()
        self._preview_seconds = position/1000
        if not self._vplay_slider.isSliderDown() and not self._preview_seek_timer.isActive():
            self._vplay_slider.blockSignals(True)
            self._vplay_slider.set(position/max(1,duration)*1000)
            self._vplay_slider.blockSignals(False)
        def stamp(ms):
            seconds = int(ms/1000)
            return f'{seconds//60:02}:{seconds%60:02}'
        self._preview_time_label.setText(f'{stamp(position)} / {stamp(duration)}')

    def _on_vplay_scrub(self,value):
        self._preview_seek_value = value
        self._preview_seek_timer.start()

    def _apply_preview_seek(self):
        if self._ensure_native_source():
            player = self.preview_canvas.player
            player.setPosition(round(self._preview_seek_value/1000*player.duration()))

    def _preview_drag_started(self):
        # Native Qt movement leaves video playback alone, as in render_tab.
        self._preview_resume_after_drag = False

    def _preview_drag_finished(self):
        if self._preview_sync_pending:
            self._preview_sync_pending = False
            self.update_preview()
        if self._preview_resume_after_drag:
            self._preview_resume_after_drag = False
            self.preview_canvas.player.play()

    def _flush_preview_save(self):
        self._preview_save_timer.stop()
        if self._pending_preview_save:
            data,self._pending_preview_save = self._pending_preview_save,{}
            data.update(logo_x_ratio=self.logo_x_ratio,logo_y_ratio=self.logo_y_ratio,
                        logo_size_pct=self.logo_size_pct_var,delogo_boxes=self.delogo_boxes,
                        burn_sub_x_ratio=self.burn_sub_x_ratio,burn_sub_y_ratio=self.burn_sub_y_ratio,
                        burn_sub_fontsize=self.burn_sub_fontsize_var)
            self.save_config(data)

    def _preview_geometry_edited(self,kind,index,rect,finished):
        view = self.preview_canvas
        frame = view.sceneRect()
        if kind=='logo':
            self.logo_x_ratio = rect.center().x()/frame.width()
            self.logo_y_ratio = rect.center().y()/frame.height()
            self.logo_size_pct_var = max(1,min(50,rect.width()/frame.width()*100))
            if finished:
                self._logo_size_spin.setText(f'{self.logo_size_pct_var:.2f}')
        elif kind=='sub':
            self.burn_sub_x_ratio = rect.center().x()/frame.width()
            self.burn_sub_y_ratio = rect.center().y()/frame.height()
            if view.subtitle.edit_resized:
                self.burn_sub_fontsize_var = max(8,min(200,round(view.subtitle.caption_font_size*view.subtitle.scale())))
            if finished:
                self._burn_fs_entry.setText(str(self.burn_sub_fontsize_var))
        else:
            content = view.content_rect
            self.delogo_boxes[index] = dict(x=(rect.x()-content.x())/content.width(),
                                            y=(rect.y()-content.y())/content.height(),
                                            w=rect.width()/content.width(),h=rect.height()/content.height())
        if finished:
            self._pending_preview_save.update({'logo_x_ratio':self.logo_x_ratio,'logo_y_ratio':self.logo_y_ratio,
                              'logo_size_pct':self.logo_size_pct_var,'delogo_boxes':self.delogo_boxes,
                              'burn_sub_x_ratio':self.burn_sub_x_ratio,'burn_sub_y_ratio':self.burn_sub_y_ratio,
                              'burn_sub_fontsize':self.burn_sub_fontsize_var})
            self._preview_save_timer.start()
            # Keep the scene/video and other overlays intact at mouse release.
            view.design = self._preview_design_settings()
            if kind=='sub':
                # Preserve the item's transform at release. Rebuilding the text
                # here caused size jumps/reflow after otherwise simple moves.
                key = view._caption_key
                if key:
                    view._caption_key = (key[0],self.burn_sub_fontsize_var,key[2],
                                        self.burn_sub_x_ratio,self.burn_sub_y_ratio,self.burn_sub_outline_var,*key[6:])

