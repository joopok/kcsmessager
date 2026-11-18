# -*- coding: utf-8 -*-
"""
펑 (24시간 스토리, 명세 §8.3) - 올리기 다이얼로그와 전체 높이 카드 뷰어

    StoryComposeDialog(state).exec_()            # 글(배경색) 또는 사진 → state.post_story
    StoryViewer(state, '김철수').exec_()          # 진행 막대, 왼쪽/오른쪽 탭 = 이전/다음, 5초 자동 넘김
"""

import os
from datetime import datetime

from PyQt5.QtCore import Qt, QRectF, QTimer, QElapsedTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QImage, QPainterPath, QFontMetrics, QLinearGradient
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QPlainTextEdit, QFileDialog, QListWidget, QListWidgetItem, QAbstractButton,
                             QMessageBox, QSizePolicy)

from client.kakao import theme
from client.kakao.state import parse_dt
from client.kakao.widgets import decode_image

STORY_COLORS = ['#2A386C', '#FF7A59', '#F2B705', '#6DCC7B', '#4CB6F2', '#B184F2', '#F27AB0', '#3C3C3C']
STORY_TEXT_MAX = 200
STORY_SECONDS = 5
IMAGE_FILTER = '이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)'


def time_ago(value, now=None):
    """'방금' / 'N분 전' / 'N시간 전'"""
    dt = parse_dt(value) if value else None
    if dt is None:
        return ''
    if now is None:
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return '방금'
    if seconds < 3600:
        return f'{seconds // 60}분 전'
    return f'{seconds // 3600}시간 전'


def _text_color_for(bg):
    c = QColor(bg)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return '#191919' if lum > 186 else '#FFFFFF'


def paint_story_card(p, rect, bg_color, text, image=None, radius=16, text_px=22):
    """펑 카드 한 장 - 배경색(또는 사진 꽉 채우기) + 가운데 글"""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    p.fillRect(rect, QColor(bg_color or STORY_COLORS[0]))
    fg = _text_color_for(bg_color or STORY_COLORS[0])
    if image is not None and not image.isNull():
        scaled = image.scaled(int(rect.width() * 2), int(rect.height() * 2), Qt.KeepAspectRatioByExpanding,
                              Qt.SmoothTransformation)
        sx = (scaled.width() - rect.width() * 2) / 2
        sy = (scaled.height() - rect.height() * 2) / 2
        p.drawImage(rect, scaled, QRectF(sx, sy, rect.width() * 2, rect.height() * 2))
        fg = '#FFFFFF'
        if text:
            # 사진 위 글은 아래쪽에 어두운 그라데이션을 깔고 쓴다
            grad = QLinearGradient(0, rect.bottom() - rect.height() * 0.4, 0, rect.bottom())
            grad.setColorAt(0, QColor(0, 0, 0, 0))
            grad.setColorAt(1, QColor(0, 0, 0, 170))
            p.fillRect(rect, grad)
    # 이모지 변형 선택자(U+FE0F)가 섞이면 Qt 가 줄 전체를 못 그리는 문제가 있어 그릴 때만 뺀다
    text = (text or '').replace('\ufe0f', '')
    if text:
        f = theme.font(text_px, bold=True)
        f.setPixelSize(text_px)
        p.setFont(f)
        p.setPen(QColor(fg))
        margin = rect.width() * 0.1
        if image is not None and not image.isNull():
            tr = QRectF(rect.left() + margin, rect.bottom() - rect.height() * 0.32,
                        rect.width() - 2 * margin, rect.height() * 0.26)
            p.drawText(tr, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, text)
        else:
            tr = rect.adjusted(margin, margin, -margin, -margin)
            p.drawText(tr, Qt.AlignCenter | Qt.TextWordWrap, text)
    p.restore()


# ================================================================ 올리기
class ColorSwatch(QAbstractButton):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(30, 30)
        self.setToolTip(color)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc('text'))
            p.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
            p.setBrush(theme.qc('bg'))
            p.drawEllipse(QRectF(self.rect()).adjusted(2, 2, -2, -2))
        p.setPen(QColor(0, 0, 0, 30))
        p.setBrush(QColor(self.color))
        p.drawEllipse(r)


class StoryPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_color = STORY_COLORS[0]
        self.text = ''
        self.image = None
        self.setFixedSize(200, 320)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        text = self.text
        if not text and self.image is None:
            text = '오늘의 이야기를\n남겨 보세요'
        paint_story_card(p, QRectF(self.rect()), self.bg_color, text, self.image, radius=14, text_px=17)


class StoryComposeDialog(QDialog):
    """펑 올리기 - 글 + 배경색, 또는 사진(+짧은 글)"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.bg_color = STORY_COLORS[0]
        self.image_path = None
        self.setWindowTitle('펑 올리기')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)
        title = QLabel('펑 올리기')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel('24시간 뒤에 펑! 하고 사라져요')
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.preview = StoryPreview()
        body.addWidget(self.preview, 0, Qt.AlignTop)
        side = QVBoxLayout()
        side.setSpacing(10)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText('무슨 일이 있었나요?')
        self.text_edit.setFixedWidth(220)
        self.text_edit.setMinimumHeight(110)
        side.addWidget(self.text_edit)
        self.count_label = QLabel(f'0/{STORY_TEXT_MAX}')
        self.count_label.setAlignment(Qt.AlignRight)
        self.count_label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('caption')}px;")
        side.addWidget(self.count_label)
        color_title = QLabel('배경색')
        color_title.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        side.addWidget(color_title)
        swatch_rows = QVBoxLayout()
        swatch_rows.setSpacing(4)
        self.swatches = []
        for i in range(0, len(STORY_COLORS), 4):
            row = QHBoxLayout()
            row.setSpacing(6)
            for color in STORY_COLORS[i:i + 4]:
                sw = ColorSwatch(color)
                sw.clicked.connect(lambda _c=False, col=color: self.set_color(col))
                self.swatches.append(sw)
                row.addWidget(sw)
            row.addStretch(1)
            swatch_rows.addLayout(row)
        side.addLayout(swatch_rows)
        photo_row = QHBoxLayout()
        self.photo_button = QPushButton('사진 선택')
        self.clear_photo_button = QPushButton('사진 빼기')
        photo_row.addWidget(self.photo_button)
        photo_row.addWidget(self.clear_photo_button)
        photo_row.addStretch(1)
        side.addLayout(photo_row)
        side.addStretch(1)
        body.addLayout(side)
        layout.addLayout(body)

        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.c('badge')}; font-size: {theme.fs('meta')}px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        self.post_button = QPushButton('올리기')
        self.post_button.setProperty('primary', True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.post_button)
        layout.addLayout(buttons)

        cancel.clicked.connect(self.reject)
        self.post_button.clicked.connect(self.accept)
        self.photo_button.clicked.connect(self.choose_photo)
        self.clear_photo_button.clicked.connect(lambda: self.set_image(None))
        self.text_edit.textChanged.connect(self._text_changed)
        self.set_color(STORY_COLORS[0])
        self._update()

    # ---- 테스트 가능한 API ----
    def set_text(self, text):
        self.text_edit.setPlainText(text)

    def text(self):
        return self.text_edit.toPlainText().strip()

    def set_color(self, color):
        self.bg_color = color
        for sw in self.swatches:
            sw.setChecked(sw.color == color)
        self._update()

    def set_image(self, path):
        """사진 지정(None이면 해제). 오류 문자열 또는 None"""
        if not path:
            self.image_path = None
            self.preview.image = None
            self._update()
            return None
        image = QImage(path)
        if image.isNull():
            return self._show_error('이미지 파일을 열 수 없습니다.')
        if os.path.getsize(path) > 10 * 1024 * 1024:
            return self._show_error('10MB 이하의 사진만 올릴 수 있습니다.')
        self.image_path = path
        self.preview.image = image
        self._show_error('')
        self._update()
        return None

    def choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, '사진 선택', '', IMAGE_FILTER)
        if path:
            self.set_image(path)

    def accept(self):
        text = self.text()
        if not self.image_path and not text:
            self._show_error('내용을 입력하거나 사진을 선택해 주세요.')
            return
        if len(text) > STORY_TEXT_MAX:
            self._show_error(f'글은 {STORY_TEXT_MAX}자까지 쓸 수 있어요.')
            return
        if self.image_path:
            err = self.state.post_story('image', text, self.image_path, self.bg_color)
        else:
            err = self.state.post_story('text', text, None, self.bg_color)
        if err:
            self._show_error(err)
            return
        super().accept()

    # ---- 내부 ----
    def _show_error(self, text):
        self.error_label.setText(text or '')
        self.error_label.setVisible(bool(text))
        return text or None

    def _text_changed(self):
        self._update()

    def _update(self):
        text = self.text_edit.toPlainText()
        self.count_label.setText(f'{len(text.strip())}/{STORY_TEXT_MAX}')
        self.count_label.setStyleSheet(
            f"color: {theme.c('badge') if len(text.strip()) > STORY_TEXT_MAX else theme.c('text_hint')};"
            f" font-size: {theme.fs('caption')}px;")
        self.preview.bg_color = self.bg_color
        self.preview.text = text.strip()
        self.clear_photo_button.setEnabled(self.image_path is not None)
        self.preview.update()


# ================================================================ 보기
class StoryCard(QWidget):
    """뷰어 본문: 진행 막대 + 머리글(사진·이름·시간) + 카드. 왼쪽 1/3 탭 = 이전, 나머지 = 다음"""

    tapped = pyqtSignal(str)   # 'prev' | 'next'

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tapped.emit('prev' if event.pos().x() < self.width() / 3 else 'next')

    def paintEvent(self, event):
        v = self.viewer
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor('#000000'))
        story = v.current_story()
        if story is None:
            return
        rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        image = v.image_for(story)
        paint_story_card(p, rect, story.get('bg_color'), story.get('content') or '', image, radius=14,
                         text_px=theme.px(22))
        # 머리글을 읽을 수 있게 위쪽 그림자
        grad = QLinearGradient(0, rect.top(), 0, rect.top() + 90)
        grad.setColorAt(0, QColor(0, 0, 0, 110))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14, 14)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 90), grad)
        p.restore()
        # 진행 막대
        n = len(v.stories)
        gap = 4
        bar_w = (rect.width() - 24 - gap * (n - 1)) / max(1, n)
        for i in range(n):
            r = QRectF(rect.left() + 12 + i * (bar_w + gap), rect.top() + 10, bar_w, 3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 90))
            p.drawRoundedRect(r, 1.5, 1.5)
            fill = 1.0 if i < v.index else (v.progress() if i == v.index else 0.0)
            if fill > 0:
                p.setBrush(QColor('#FFFFFF'))
                p.drawRoundedRect(QRectF(r.left(), r.top(), r.width() * fill, r.height()), 1.5, 1.5)
        # 머리글
        from client.kakao.chats_tab import user_pixmap
        pm = user_pixmap(v.state, v.username, 34)
        p.drawPixmap(int(rect.left() + 12), int(rect.top() + 22), pm)
        f = theme.font('body', bold=True)
        p.setFont(f)
        p.setPen(QColor('#FFFFFF'))
        name = v.state.display_name(v.username)
        fm = QFontMetrics(f)
        p.drawText(QRectF(rect.left() + 54, rect.top() + 22, rect.width() - 110, 17),
                   Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(name, Qt.ElideRight, int(rect.width() - 110)))
        f2 = theme.font('caption')
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(QRectF(rect.left() + 54, rect.top() + 39, rect.width() - 110, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, time_ago(story.get('created_at')))


class StoryViewer(QDialog):
    """한 사람의 펑을 순서대로 보여준다. 내 펑이면 'N명이 봤어요' + 본 사람 목록 + 삭제"""

    def __init__(self, state, username, parent=None):
        super().__init__(parent)
        self.state = state
        self.username = username
        self.mine = username == state.me
        self.stories = state.stories_of(username)
        self.index = 0
        self._images = {}
        self._elapsed = QElapsedTimer()
        self._paused_ms = 0
        self.setWindowTitle(f'{state.display_name(username)}님의 펑')
        self.resize(380, 680)
        self.setMinimumSize(320, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.card = StoryCard(self)
        layout.addWidget(self.card, 1)
        from client.kakao.widgets import make_icon
        self.close_button = QPushButton(self.card)
        self.close_button.setIcon(make_icon('close', '#FFFFFF', 22))
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setToolTip('닫기')
        self.close_button.setFixedSize(32, 32)
        self.close_button.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            " QPushButton:hover { background: rgba(255,255,255,0.16); border-radius: 16px; }")
        self.close_button.clicked.connect(self.reject)

        # 내 펑: 본 사람 + 삭제
        self.viewers_panel = QWidget()
        self.viewers_panel.setObjectName('storyViewersPanel')
        panel = QVBoxLayout(self.viewers_panel)
        panel.setContentsMargins(16, 10, 16, 12)
        panel.setSpacing(6)
        top = QHBoxLayout()
        self.viewers_label = QLabel('')
        top.addWidget(self.viewers_label, 1)
        self.delete_button = QPushButton('삭제')
        self.delete_button.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.delete_button)
        panel.addLayout(top)
        self.viewers_list = QListWidget()
        self.viewers_list.setFixedHeight(96)
        panel.addWidget(self.viewers_list)
        layout.addWidget(self.viewers_panel)
        self.viewers_panel.setVisible(self.mine)
        # clicked 의 checked(False)가 confirm 으로 들어가 확인 없이 지우지 않도록 감싼다
        self.delete_button.clicked.connect(lambda _checked=False: self.delete_current())

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.card.tapped.connect(lambda where: self.prev() if where == 'prev' else self.next())
        state.stories_changed.connect(self._on_stories_changed)
        state.attachment_loaded.connect(self._on_attachment)
        self.apply_theme()
        self.show_index(0)

    # ---- 데이터 ----
    def current_story(self):
        if 0 <= self.index < len(self.stories):
            return self.stories[self.index]
        return None

    def image_for(self, story):
        aid = story.get('attachment_id')
        if story.get('kind') != 'image' or not aid:
            return None
        if aid not in self._images:
            cached = self.state.attachments.get(aid)
            if cached:
                self._images[aid] = decode_image(cached[0]) or QImage()
            else:
                self._images[aid] = None
                self.state.request_attachment(aid)
        return self._images.get(aid)

    def _on_attachment(self, aid, data, _name, _mime):
        if aid in self._images:
            self._images[aid] = decode_image(data) or QImage()
            self.card.update()

    def progress(self):
        if not self._elapsed.isValid():
            return 0.0
        return max(0.0, min(1.0, self._elapsed.elapsed() / (STORY_SECONDS * 1000.0)))

    # ---- 이동 ----
    def show_index(self, index):
        if not self.stories:
            self.timer.stop()
            return
        self.index = max(0, min(index, len(self.stories) - 1))
        story = self.stories[self.index]
        self.image_for(story)    # 사진 펑이면 미리 요청
        self.state.view_story(story['story_id'])
        self._elapsed.start()
        self.timer.start()
        self._refresh_viewers()
        self.card.update()

    def next(self):
        if self.index + 1 < len(self.stories):
            self.show_index(self.index + 1)
        else:
            self.timer.stop()
            self.accept()

    def prev(self):
        self.show_index(self.index - 1 if self.index > 0 else 0)

    def _tick(self):
        if self.progress() >= 1.0:
            self.next()
        else:
            self.card.update()

    # ---- 내 펑 ----
    def viewer_names(self):
        story = self.current_story()
        return list((story or {}).get('viewers') or [])

    def _refresh_viewers(self):
        if not self.mine:
            return
        names = self.viewer_names()
        self.viewers_label.setText(f'{len(names)}명이 봤어요' if names else '아직 본 사람이 없어요')
        self.viewers_list.clear()
        from client.kakao.chats_tab import user_pixmap
        from PyQt5.QtGui import QIcon
        for name in names:
            item = QListWidgetItem(QIcon(user_pixmap(self.state, name, 24)), self.state.display_name(name))
            item.setData(Qt.UserRole, name)
            self.viewers_list.addItem(item)
        self.viewers_list.setVisible(bool(names))

    def delete_current(self, confirm=True):
        story = self.current_story()
        if story is None or not self.mine:
            return False
        if confirm and QMessageBox.question(self, '펑 삭제', '이 펑을 삭제할까요?',
                                            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        self.state.delete_story(story['story_id'])
        return True

    def _on_stories_changed(self):
        current = self.current_story()
        cid = current['story_id'] if current else None
        self.stories = self.state.stories_of(self.username)
        if not self.stories:
            self.timer.stop()
            self.accept()
            return
        ids = [s['story_id'] for s in self.stories]
        if cid in ids:
            self.index = ids.index(cid)     # 본 사람 목록 갱신 등 - 진행 유지
            self._refresh_viewers()
            self.card.update()
        else:
            self.show_index(min(self.index, len(self.stories) - 1))

    # ---- 표시 ----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.close_button.move(self.card.width() - 46, 26)
        self.close_button.raise_()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Right, Qt.Key_Space):
            self.next()
        elif event.key() == Qt.Key_Left:
            self.prev()
        else:
            super().keyPressEvent(event)

    def done(self, result):
        self.timer.stop()
        super().done(result)

    def apply_theme(self):
        self.setStyleSheet(
            f"QDialog {{ background: #000000; }}"
            f" QWidget#storyViewersPanel {{ background: {theme.c('bg')}; }}")
        self.viewers_label.setStyleSheet(f"color: {theme.c('text')}; font-size: {theme.fs('body')}px; font-weight: bold;")
        self.viewers_list.setStyleSheet(f"QListWidget {{ background: {theme.c('bg')}; border: none; }}"
                                        f" QListWidget::item {{ padding: 3px; }}")


__all__ = ['StoryComposeDialog', 'StoryViewer', 'STORY_COLORS', 'time_ago', 'paint_story_card']
