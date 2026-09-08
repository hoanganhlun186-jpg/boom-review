"""Qt scene preview: native video playback, independently editable overlays.

Scene coordinates are output pixels. No video decoding or PIL composition
runs in mouse handlers. The UI and renderer share normalized design values.

FIX (Nuitka standalone): QGraphicsVideoItem dùng Direct3D hardware rendering,
không hoạt động trong bản build. Thay bằng QVideoSink + QGraphicsPixmapItem:
mỗi frame được convert sang QImage rồi vẽ lên scene — hoạt động mọi nơi.
"""
import os
from bisect import bisect_right

from PySide6.QtCore import Qt, QRectF, QPointF, QSizeF, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QImage
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsObject,
                               QGraphicsItem, QGraphicsRectItem, QGraphicsSimpleTextItem,
                               QGraphicsPixmapItem)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame
from core.preview_design import normalized_box, resize_box, title_padding


class OverlayItem(QGraphicsObject):
    """Movable object with screen-sized handles, independent of video repaint."""
    def __init__(self, view, kind, index=0):
        super().__init__()
        self.view, self.kind, self.index = view, kind, index
        self.rect = QRectF(0, 0, 100, 40)
        self.bounds = QRectF(0, 0, 1920, 1080)
        self.pixmap = QPixmap()
        self.text_path = QPainterPath()
        self.natural_size = QSizeF(100,40)
        self.color = QColor('white')
        self.outline = 2
        self.drag = None
        self.edit_resized = False
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeAllCursor)
        self.setZValue({'blur': 2, 'sub': 5, 'logo': 6}[kind])
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)

    def boundingRect(self):
        return self.rect.adjusted(-80, -80, 80, 80)

    def shape(self):
        path = QPainterPath()
        margin = 6 / max(.02, self.view.transform().m11())
        path.addRect(self.rect.adjusted(-margin,-margin,margin,margin))
        return path

    def handles(self):
        r = self.rect
        points = {'tl':r.topLeft(), 'tr':r.topRight(), 'bl':r.bottomLeft(), 'br':r.bottomRight()}
        if self.kind == 'blur':
            points.update(tm=QPointF(r.center().x(),r.top()), bm=QPointF(r.center().x(),r.bottom()),
                          ml=QPointF(r.left(),r.center().y()), mr=QPointF(r.right(),r.center().y()))
        size = min(8/max(.02, self.view.transform().m11()),r.width()/4,r.height()/3)
        return {key: QRectF(p.x()-size/2, p.y()-size/2, size, size) for key,p in points.items()}

    def set_geometry(self, rect):
        self.prepareGeometryChange()
        self.rect = QRectF(0,0,max(1,rect.width()),max(1,rect.height()))
        self.setScale(1)
        self.setPos(rect.topLeft())
        self.update()

    def set_caption(self, text, size, color, outline=2, family='Arial', background=False, background_color='#000000', opacity=70):
        self.background = QColor(background_color) if background else QColor(Qt.transparent)
        if background: self.background.setAlpha(round(255*max(0,min(100,opacity))/100))
        font = QFont(family)
        font.setPixelSize(int(size))
        metrics = QFontMetricsF(font)
        # Like render_tab's unconstrained QGraphicsTextItem: one line only.
        text = ' '.join(text.split())
        available = self.bounds.width()*.9
        while metrics.horizontalAdvance(text)>available and font.pixelSize()>1:
            font.setPixelSize(font.pixelSize()-1)
            metrics = QFontMetricsF(font)
        lines = [text]
        self.caption_font_size = font.pixelSize()
        width = max(1, max(metrics.horizontalAdvance(line) for line in lines))
        path = QPainterPath()
        for i,line in enumerate(lines):
            path.addText((width-metrics.horizontalAdvance(line))/2,
                         metrics.ascent()+i*metrics.lineSpacing(),font,line)
        self.prepareGeometryChange()
        self.setScale(1)
        self.text_path = path
        self.rect = QRectF(0,0,width,max(1,metrics.lineSpacing()*len(lines)))
        self.natural_size = self.rect.size()
        self.color = QColor(color)
        self.outline = max(0,min(12,int(outline)))
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        if self.kind == 'logo':
            painter.drawPixmap(self.rect, self.pixmap, QRectF(self.pixmap.rect()))
        elif self.kind == 'sub':
            painter.save()
            painter.scale(self.rect.width()/self.natural_size.width(),self.rect.height()/self.natural_size.height())
            painter.fillRect(self.rect.adjusted(-4,-4,4,4),self.background)
            if self.outline:
                painter.strokePath(self.text_path,QPen(QColor('black'),2*self.outline/max(.01,self.scale()),
                                   Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
            painter.fillPath(self.text_path,self.color)
            painter.restore()
        else:
            painter.fillRect(self.rect, QColor(255,70,70,35))
            painter.save()
            painter.setClipRect(self.rect)
            scale = max(.02, self.view.transform().m11())
            font = QFont('Arial')
            font.setPixelSize(max(8,round(11/scale)))
            painter.setFont(font)
            painter.setPen(QColor('white'))
            painter.drawText(self.rect.adjusted(4/scale,3/scale,0,0),Qt.AlignTop | Qt.AlignLeft,f'Mờ #{self.index+1}')
            painter.restore()
        color = {'blur':'#f87171','sub':'#4ade80','logo':'#38bdf8'}[self.kind]
        pen = QPen(QColor('#facc15' if self.isSelected() else color), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect)
        if self.isSelected():
            painter.setBrush(QColor('#facc15'))
            for rect in self.handles().values():
                painter.drawRect(rect)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            w,h = self.rect.width()*self.scale(),self.rect.height()*self.scale()
            return QPointF(max(self.bounds.left(),min(self.bounds.right()-w,value.x())),
                           max(self.bounds.top(),min(self.bounds.bottom()-h,value.y())))
        return super().itemChange(change,value)

    def visible_geometry(self):
        return self.mapRectToScene(self.rect)

    def mousePressEvent(self, event):
        if event.button()!=Qt.LeftButton:
            super().mousePressEvent(event)
            return
        # Match render_tab: a first click selects/moves; only an already
        # selected object's visible corner can start a resize.
        handle = None
        if self.isSelected():
            handle = next((key for key,r in self.handles().items() if r.contains(event.pos())),None)
        self.edit_resized = handle is not None
        rect = self.visible_geometry()
        self.drag = (event.scenePos(),rect,handle or 'move',self.scale())
        if handle and self.kind!='blur':
            corners = {'tl':self.rect.bottomRight(),'tr':self.rect.bottomLeft(),
                       'bl':self.rect.topRight(),'br':self.rect.topLeft()}
            self._resize_anchor_local = corners[handle]
            self._resize_anchor = self.mapToScene(self._resize_anchor_local)
            self._resize_distance = max(1,(event.scenePos()-self._resize_anchor).manhattanLength())
        self.view.interactionStarted.emit()
        if handle:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.drag or self.drag[2]=='move':
            # Native Qt item movement, as in render_tab. No redraw, text
            # layout, configuration writes or timers in the pointer path.
            super().mouseMoveEvent(event)
            return
        origin,rect,handle,start_scale = self.drag
        delta = event.scenePos()-origin
        if self.kind=='blur':
            b = dict(x=(rect.x()-self.bounds.x())/self.bounds.width(),
                     y=(rect.y()-self.bounds.y())/self.bounds.height(),
                     w=rect.width()/self.bounds.width(),h=rect.height()/self.bounds.height())
            b = resize_box(b,handle,delta.x()/self.bounds.width(),delta.y()/self.bounds.height())
            self.set_geometry(QRectF(self.bounds.x()+b['x']*self.bounds.width(),
                                    self.bounds.y()+b['y']*self.bounds.height(),
                                    b['w']*self.bounds.width(),b['h']*self.bounds.height()))
        else:
            ratio = max(.01,(event.scenePos()-self._resize_anchor).manhattanLength()/self._resize_distance)
            self._set_edit_scale(start_scale*ratio)
            self.setPos(self._resize_anchor-self._resize_anchor_local*self.scale())
        event.accept()

    def _set_edit_scale(self,value):
        if self.kind=='logo':
            low = self.bounds.width()*.01/self.rect.width()
            high = self.bounds.width()*.5/self.rect.width()
        else:
            size = max(1,self.caption_font_size)
            low,high = 8/size,200/size
        high = min(high,self.bounds.width()*.9/self.rect.width(),self.bounds.height()/self.rect.height())
        self.setScale(max(low,min(high,value)))

    def mouseReleaseEvent(self, event):
        if self.drag:
            # Movement changes position only; export dimensions already include
            # the scale transform, just as render_tab's sceneBoundingRect does.
            self.drag = None
            super().mouseReleaseEvent(event)
            self.view.geometryEdited.emit(self.kind,self.index,self.visible_geometry(),True)
            self.view.interactionFinished.emit()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self,event):
        if not self.isSelected():
            event.ignore()
            return
        self.edit_resized = True
        center = self.visible_geometry().center()
        factor = 1.1 if event.delta()>0 else .9
        if self.kind=='blur':
            r = self.visible_geometry()
            w,h = min(self.bounds.width(),r.width()*factor),min(self.bounds.height(),r.height()*factor)
            self.set_geometry(QRectF(center.x()-w/2,center.y()-h/2,w,h))
        else:
            self._set_edit_scale(self.scale()*factor)
            self.setPos(center-QPointF(self.rect.width()*self.scale()/2,self.rect.height()*self.scale()/2))
        self.view.geometryEdited.emit(self.kind,self.index,self.visible_geometry(),True)
        event.accept()


class GraphicsPreview(QGraphicsView):
    geometryEdited = Signal(str,int,QRectF,bool)
    interactionStarted = Signal()
    interactionFinished = Signal()
    sourceSizeChanged = Signal(int,int)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setMinimumSize(280, 320)
        self.setMaximumHeight(560)
        self.setStyleSheet('background:black;border:0;')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)

        # ── Video rendering: QVideoSink + QGraphicsPixmapItem ──────────────────
        # QGraphicsVideoItem dùng Direct3D hardware, không hoạt động trong Nuitka
        # standalone builds. QVideoSink nhận từng frame → convert QImage → pixmap,
        # đảm bảo hoạt động trên mọi máy không cần driver đặc biệt.
        self.video = QGraphicsPixmapItem()
        self.video.setAcceptedMouseButtons(Qt.NoButton)
        self.video.setZValue(0)
        self.scene().addItem(self.video)
        self._video_sink = QVideoSink(self)
        self._video_sink.videoFrameChanged.connect(self._on_video_frame)
        self._video_frame_size = (0, 0)
        # ──────────────────────────────────────────────────────────────────────

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(False)
        self.audio.setVolume(.7)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self._video_sink)
        self.player.positionChanged.connect(self.update_caption)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.logo = OverlayItem(self,'logo')
        self.subtitle = OverlayItem(self,'sub')
        self.scene().addItem(self.logo)
        self.scene().addItem(self.subtitle)
        self.blurs = []
        self.title_items = []
        self.width_px,self.height_px = 1080,1920
        self.content_rect = QRectF(0,0,1080,1920)
        self.design,self.titles = {},{}
        self._logo_stamp = None
        self._caption_key = None
        self.cues,self.starts = [],[]
        self._pending_seek = None
        self._source = ''
        self.sync({}, {})

    def _on_video_frame(self, frame: QVideoFrame):
        """Nhận frame từ QVideoSink, convert sang QImage rồi hiển thị lên scene."""
        try:
            if not frame.isValid():
                return
            img = frame.toImage()
            if img.isNull():
                return
            # Detect native size thay cho nativeSizeChanged của QGraphicsVideoItem
            w, h = img.width(), img.height()
            if w > 0 and h > 0 and (w, h) != self._video_frame_size:
                self._video_frame_size = (w, h)
                try:
                    self.set_source_size(w, h)
                    self.sourceSizeChanged.emit(w, h)
                except Exception:
                    pass
            # Scale pixmap về kích thước scene (width_px x height_px) để fit đúng
            target_w, target_h = max(1, self.width_px), max(1, self.height_px)
            if w != target_w or h != target_h:
                img = img.scaled(target_w, target_h, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            pixmap = QPixmap.fromImage(img)
            if pixmap.isNull():
                return
            self.video.setPixmap(pixmap)
            # Căn giữa pixmap trong scene nếu có black bars
            px_w = pixmap.width()
            px_h = pixmap.height()
            self.video.setPos((target_w - px_w) / 2, (target_h - px_h) / 2)
        except Exception:
            pass  # Không crash app khi frame lỗi

    def set_source(self,path):
        path = os.path.abspath(path)
        if path == self._source:
            return
        self.player.stop()
        self._source = path
        self._pending_seek = 0
        self.player.setSource(QUrl.fromLocalFile(path))

    def _media_status(self,status):
        if status in (QMediaPlayer.LoadedMedia,QMediaPlayer.BufferedMedia) and self._pending_seek is not None:
            position,self._pending_seek = self._pending_seek,None
            self.player.setPosition(position)
            if self.player.playbackState()!=QMediaPlayer.PlayingState:
                self.player.pause()

    def _native_size(self,size):
        # Kept for compatibility — size detection now done in _on_video_frame
        if size.width()>0 and size.height()>0:
            self.set_source_size(round(size.width()),round(size.height()))
            self.sourceSizeChanged.emit(self.width_px,self.height_px)

    def set_source_size(self,width,height):
        if (width,height)!=(self.width_px,self.height_px):
            self.width_px,self.height_px = width,height
            self.sync(self.design,self.titles)

    def set_cues(self,cues):
        self.cues = sorted(cues,key=lambda cue:cue[0])
        self.starts = [cue[0] for cue in self.cues]
        self._caption_key = None
        self.update_caption(self.player.position())

    def sync(self,design,titles):
        self.design,self.titles = dict(design),dict(titles)
        w,h = self.width_px,self.height_px
        top_band,bottom_band = title_padding(w, titles.get('header',''), titles.get('footer',''))
        frame = QRectF(0,0,w,h)
        self.content_rect = QRectF(0,0,w,h)
        changed = frame != self.sceneRect()
        self.scene().setSceneRect(frame)
        self.video.setPos(0,0)
        for item in self.title_items:
            self.scene().removeItem(item)
        self.title_items.clear()
        from engine.video_engine import VideoEngine
        for name,band,y in [('header',top_band,0),('footer',bottom_band,h-bottom_band)]:
            text = titles.get(name,'')
            if not text:
                continue
            lines,fs = VideoEngine._prepare_title_layout(text,titles.get(name+'_size',60),
                         min_font_size=20,frame_width=w,max_lines=2,auto_fit=True)
            gap = max(4,int(fs*.12))
            text_h = len(lines)*fs+max(0,len(lines)-1)*gap
            bar_h = min(band,text_h+2*max(12,int(fs*.22)))
            y = 0 if name=='header' else h-bar_h
            bar = QGraphicsRectItem(0,y,w,bar_h)
            bar.setPen(Qt.NoPen)
            bar.setBrush(QColor(*titles.get(name+'_bar',(0,0,0))))
            bar.setAcceptedMouseButtons(Qt.NoButton)
            bar.setZValue(3)
            self.scene().addItem(bar)
            self.title_items.append(bar)
            for i,line in enumerate(lines):
                label = QGraphicsSimpleTextItem(line)
                font = QFont('Arial'); font.setPixelSize(fs)
                label.setFont(font)
                label.setBrush(QColor(*titles.get(name+'_color',(255,255,255))))
                label.setPos((w-label.boundingRect().width())/2,y+(bar_h-text_h)/2+i*(fs+gap))
                label.setAcceptedMouseButtons(Qt.NoButton)
                label.setZValue(4)
                self.scene().addItem(label)
                self.title_items.append(label)
        boxes = design.get('blur_boxes') or []
        while len(self.blurs)>len(boxes):
            self.scene().removeItem(self.blurs.pop())
        while len(self.blurs)<len(boxes):
            item = OverlayItem(self,'blur',len(self.blurs))
            self.scene().addItem(item)
            self.blurs.append(item)
        for item,b in zip(self.blurs,boxes):
            b = normalized_box(b)
            item.bounds = self.content_rect
            item.set_geometry(QRectF(b['x']*w,b['y']*h,b['w']*w,b['h']*h))
        path = design.get('logo','')
        stamp = (path,os.stat(path).st_mtime_ns) if path and os.path.isfile(path) else None
        if stamp != self._logo_stamp:
            self.logo.pixmap = QPixmap(path) if stamp else QPixmap()
            self._logo_stamp = stamp
        self.logo.setVisible(not self.logo.pixmap.isNull())
        self.logo.bounds = frame
        if not self.logo.pixmap.isNull():
            lw = w*max(1,min(50,design.get('logo_size',10)))/100
            lh = lw*self.logo.pixmap.height()/self.logo.pixmap.width()
            x = max(0,min(w-lw,design.get('logo_x',.85)*w-lw/2))
            y = max(0,min(frame.height()-lh,design.get('logo_y',.05)*frame.height()-lh/2))
            self.logo.set_geometry(QRectF(x,y,lw,lh))
        self.subtitle.bounds = frame
        self.subtitle.setVisible(bool(design.get('burn')))
        self._caption_key = None
        self.update_caption(self.player.position())
        if changed:
            self.fit_frame()

    def update_caption(self,position):
        if not self.design.get('burn') or self.subtitle.drag:
            return
        seconds = position/1000
        idx = bisect_right(self.starts,seconds)-1
        text = self.cues[idx][2] if idx>=0 and seconds<self.cues[idx][1] else 'Chữ mẫu'
        key = (text,self.design.get('sub_size',36),self.design.get('sub_color','white'),
               self.design.get('sub_x',.5),self.design.get('sub_y',.78),self.design.get('sub_outline',2),
               self.design.get('sub_font','Arial'),self.design.get('sub_background',False),
               self.design.get('sub_background_color','#000000'),self.design.get('sub_background_opacity',70))
        if key == self._caption_key:
            return
        self._caption_key = key
        self.subtitle.set_caption(text,max(8,min(200,key[1])),key[2],*key[5:])
        r = self.subtitle.rect
        self.subtitle.setPos(key[3]*self.sceneRect().width()-r.width()/2,
                             key[4]*self.sceneRect().height()-r.height()/2)

    def fit_frame(self):
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fit_frame()
