# -*- coding: utf-8 -*-
"""
톡서랍 (명세 §8.10) - 내가 속한 모든 채팅방의 사진 · 파일 · 링크 모아보기

    TalkDrawerDialog(state, parent)
      - 열리면 drawer_query(kind=image/file/link) 를 보내고 drawer_result(drawer_loaded 시그널)로 채운다
      - 사진: 썸네일 격자(request_attachment로 받아 온다)
      - 파일: 파일 이름 · 크기 · 채팅방 이름 + [저장]
      - 링크: URL(미리보기 제목) · 채팅방 이름 + [열기](브라우저)
      - 더블클릭(또는 우클릭 메뉴) → '채팅방에서 보기': 메인 창 open_room + focus_message
"""

import os

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QColor, QPainter
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QListWidget,
                             QListWidgetItem, QListView, QWidget, QPushButton, QMenu, QFileDialog,
                             QStackedWidget)

from client.kakao import theme
from client.kakao import osenv
from client.kakao.state import parse_dt, URL_RE
from client.kakao.widgets import make_icon, decode_pixmap, plain, human_size as _human_size

KINDS = ('image', 'file', 'link')
KIND_LABELS = {'image': '사진', 'file': '파일', 'link': '링크'}
THUMB = 96
MAX_THUMB_REQUESTS = 80     # 한 번에 받아 올 썸네일 수 (너무 많은 첨부 요청 방지)


def human_size(size):
    """톡서랍 표기 '1.5MB' (붙여 쓴다)"""
    return _human_size(size, sep='')


def short_time(value):
    dt = parse_dt(value)
    return dt.strftime('%Y.%m.%d %H:%M') if dt else ''


def placeholder_pixmap(size=THUMB):
    pm = QPixmap(size, size)
    pm.fill(QColor(theme.c('bg_alt')))
    p = QPainter(pm)
    icon = make_icon('image', theme.c('text_hint'), size // 3).pixmap(size // 3, size // 3)
    p.drawPixmap((size - icon.width()) // 2, (size - icon.height()) // 2, icon)
    p.end()
    return pm


def links_of(message):
    """메시지에서 URL 목록 (link_preview의 url이 있으면 그것도 포함)"""
    urls = URL_RE.findall(message.get('content') or '')
    preview = message.get('link_preview') or {}
    if preview.get('url') and preview['url'] not in urls:
        urls.insert(0, preview['url'])
    return list(dict.fromkeys(u.rstrip('.,)') for u in urls))


class FileRow(QWidget):
    """파일 한 줄: 아이콘 | 이름 / 크기 · 채팅방 · 날짜 | [저장]"""

    def __init__(self, dialog, message, parent=None):
        super().__init__(parent)
        self.message = message
        att = message.get('attachment') or {}
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(make_icon('file', theme.c('text_sub'), 26).pixmap(26, 26))
        row.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name_label = plain(QLabel(att.get('name') or message.get('content') or '파일'))
        self.name_label.setObjectName('cardTitle')
        meta = [human_size(att.get('size')), dialog.room_name(message), short_time(message.get('timestamp'))]
        self.meta_label = QLabel(' · '.join(x for x in meta if x))
        self.meta_label.setObjectName('cardMeta')
        col.addWidget(self.name_label)
        col.addWidget(self.meta_label)
        row.addLayout(col, 1)
        self.save_button = QPushButton('저장')
        self.save_button.setCursor(Qt.PointingHandCursor)
        if att.get('expired'):
            self.save_button.setText('만료')
            self.save_button.setEnabled(False)
            self.save_button.setToolTip('보관 기간이 지난 파일입니다')
        self.save_button.clicked.connect(lambda: dialog.save_file(message))
        row.addWidget(self.save_button)


class LinkRow(QWidget):
    """링크 한 줄: 제목(있으면) / URL / 채팅방 · 보낸 사람 | [열기]"""

    def __init__(self, dialog, url, message, parent=None):
        super().__init__(parent)
        self.url = url
        self.message = message
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(make_icon('clip', theme.c('link'), 22).pixmap(22, 22))
        row.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        preview = message.get('link_preview') or {}
        title = preview.get('title') if preview.get('url') in (None, url) else ''
        self.title_label = QLabel(title or url)
        self.title_label.setObjectName('cardTitle')
        self.url_label = QLabel(url)
        self.url_label.setObjectName('cardTags')
        self.url_label.setVisible(bool(title))
        meta = [dialog.room_name(message), dialog.state.member_name(message.get('room_id'), message.get('sender', ''))
                if hasattr(dialog.state, 'member_name') else dialog.state.display_name(message.get('sender', '')),
                short_time(message.get('timestamp'))]
        self.meta_label = QLabel(' · '.join(x for x in meta if x))
        self.meta_label.setObjectName('cardMeta')
        for lab in (self.title_label, self.url_label, self.meta_label):
            lab.setTextFormat(Qt.PlainText)
            col.addWidget(lab)
        row.addLayout(col, 1)
        self.open_button = QPushButton('열기')
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.clicked.connect(lambda: dialog.open_link(url))
        row.addWidget(self.open_button)


class TalkDrawerDialog(QDialog):
    """전체 톡서랍: 사진 / 파일 / 링크 탭"""

    def __init__(self, state, parent=None, main_window=None):
        super().__init__(parent)
        self.state = state
        self.main_window = main_window
        self.messages = {kind: [] for kind in KINDS}
        self.loaded = set()
        self._photo_items = {}      # attachment_id -> [QListWidgetItem]
        self.thumbs_loaded = set()
        self._pending_saves = {}    # attachment_id -> 저장 경로
        self.saved = []             # 저장 완료한 경로 (상태 표시/테스트용)
        self.setWindowTitle('톡서랍')
        self.setMinimumSize(360, 460)
        self.resize(460, 600)

        from client.kakao.chats_tab import panel_dialog_qss
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(6)
        title = QLabel('톡서랍')
        title.setObjectName('panelTitle')
        layout.addWidget(title)
        hint = QLabel('모든 채팅방의 사진·파일·링크 · 더블클릭하면 채팅방에서 보기')
        hint.setObjectName('panelHint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.photos = QListWidget()
        self.photos.setViewMode(QListView.IconMode)
        self.photos.setIconSize(QSize(THUMB, THUMB))
        self.photos.setGridSize(QSize(THUMB + 10, THUMB + 10))
        self.photos.setResizeMode(QListView.Adjust)
        self.photos.setMovement(QListView.Static)
        self.photos.setSpacing(2)
        self.files = QListWidget()
        self.links = QListWidget()
        self.lists = {'image': self.photos, 'file': self.files, 'link': self.links}
        self.stacks = {}
        self.empty_labels = {}
        for kind in KINDS:
            stack = QStackedWidget()
            empty = QLabel('불러오는 중…')
            empty.setObjectName('emptyLabel')
            empty.setAlignment(Qt.AlignCenter)
            stack.addWidget(empty)
            stack.addWidget(self.lists[kind])
            self.stacks[kind] = stack
            self.empty_labels[kind] = empty
            self.tabs.addTab(stack, KIND_LABELS[kind])
            lst = self.lists[kind]
            lst.setContextMenuPolicy(Qt.CustomContextMenu)
            lst.customContextMenuRequested.connect(lambda pos, k=kind: self._show_menu(k, pos))
            lst.itemDoubleClicked.connect(lambda item: self.show_in_room(item.data(Qt.UserRole)))

        self.status_label = QLabel('')
        self.status_label.setObjectName('panelHint')
        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label, 1)
        self.refresh_button = QPushButton('새로고침')
        self.close_button = QPushButton('닫기')
        bottom.addWidget(self.refresh_button)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)
        self.refresh_button.clicked.connect(self.request_all)
        self.close_button.clicked.connect(self.reject)

        state.drawer_loaded.connect(self.on_loaded)
        state.attachment_loaded.connect(self._on_attachment)
        self.setStyleSheet(panel_dialog_qss())
        self.request_all()

    # ---- 요청 / 수신
    def request_all(self, _checked=False):
        for kind in KINDS:
            self.state.query_drawer(kind)

    def room_name(self, message):
        rid = message.get('room_id')
        return self.state.room_title(rid) if self.state.room(rid) else '(나간 채팅방)'

    def on_loaded(self, kind, messages):
        if kind not in self.lists:
            return
        self.loaded.add(kind)
        self.messages[kind] = [m for m in messages if not m.get('deleted')]
        getattr(self, f'_fill_{kind}')()
        lst = self.lists[kind]
        count = lst.count()
        self.tabs.setTabText(KINDS.index(kind), f'{KIND_LABELS[kind]} {count}' if count else KIND_LABELS[kind])
        self.empty_labels[kind].setText(f'{KIND_LABELS[kind]}이(가) 없습니다.' if kind != 'image' else '사진이 없습니다.')
        self.stacks[kind].setCurrentIndex(1 if count else 0)

    def _fill_image(self):
        self.photos.clear()
        self._photo_items = {}
        placeholder = QIcon(placeholder_pixmap())
        requested = 0
        for m in self.messages['image']:
            att = m.get('attachment') or {}
            aid = att.get('attachment_id')
            item = QListWidgetItem(placeholder, '')
            item.setData(Qt.UserRole, m)
            item.setToolTip(f"{self.room_name(m)} · {self.state.display_name(m.get('sender', ''))}"
                            f" · {short_time(m.get('timestamp'))}")
            item.setSizeHint(QSize(THUMB + 6, THUMB + 6))
            self.photos.addItem(item)
            if not aid or att.get('expired'):
                continue
            self._photo_items.setdefault(aid, []).append(item)
            cached = self.state.attachments.get(aid)
            if cached:
                self._set_thumb(aid, cached[0])
            elif requested < MAX_THUMB_REQUESTS:
                requested += 1
                self.state.request_attachment(aid)

    def _fill_file(self):
        self.files.clear()
        for m in self.messages['file']:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, m)
            row = FileRow(self, m)
            item.setSizeHint(row.sizeHint())
            self.files.addItem(item)
            self.files.setItemWidget(item, row)

    def _fill_link(self):
        self.links.clear()
        for m in self.messages['link']:
            for url in links_of(m):
                item = QListWidgetItem()
                item.setData(Qt.UserRole, m)
                item.setData(Qt.UserRole + 1, url)
                row = LinkRow(self, url, m)
                item.setSizeHint(row.sizeHint())
                self.links.addItem(item)
                self.links.setItemWidget(item, row)

    def _set_thumb(self, aid, data):
        pm = decode_pixmap(data)
        if pm is None:
            return
        pm = pm.scaled(THUMB, THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x, y = max(0, (pm.width() - THUMB) // 2), max(0, (pm.height() - THUMB) // 2)
        pm = pm.copy(x, y, THUMB, THUMB)
        for item in self._photo_items.get(aid, []):
            item.setIcon(QIcon(pm))
        self.thumbs_loaded.add(aid)

    def _on_attachment(self, aid, data, name, mime):
        if aid in self._photo_items:
            self._set_thumb(aid, data)
        path = self._pending_saves.pop(aid, None)
        if path:
            self._write(path, data)

    # ---- 동작
    def thumbnail_loaded(self, aid):
        """썸네일이 실제 이미지로 바뀌었는지 (테스트/디버그용)"""
        return aid in self.thumbs_loaded

    def save_file(self, message, path=None):
        """파일 저장. 데이터가 없으면 받아 온 뒤 저장한다. 저장(예약)했으면 True"""
        att = message.get('attachment') or {}
        aid = att.get('attachment_id')
        if not aid or att.get('expired'):
            self.status_label.setText('보관 기간이 지난 파일입니다.')
            return False
        if path is None:
            default = os.path.join(osenv.user_dir('downloads'), osenv.safe_filename(att.get('name')))
            path, _ = QFileDialog.getSaveFileName(self, '파일 저장', default)
            if not path:
                return False
        cached = self.state.attachments.get(aid)
        if cached:
            self._write(path, cached[0])
        else:
            self._pending_saves[aid] = path
            self.status_label.setText('파일을 받는 중…')
            self.state.request_attachment(aid)
        return True

    def _write(self, path, data):
        try:
            with open(path, 'wb') as f:
                f.write(data)
            osenv.mark_from_network(path)
        except OSError as e:
            self.status_label.setText(f'저장하지 못했습니다: {e}')
            return
        self.saved.append(path)
        self.status_label.setText(f'저장했습니다: {os.path.basename(path)}')

    def open_link(self, url):
        osenv.open_url(url)

    def _find_main_window(self):
        if self.main_window is not None:
            return self.main_window
        widget = self.parent()
        while widget is not None:
            if hasattr(widget, 'focus_message') or hasattr(widget, 'open_room'):
                return widget
            widget = widget.parent()
        return None

    def show_in_room(self, message):
        """채팅방에서 보기: 그 방을 열고 해당 메시지로 이동"""
        if not message:
            return False
        rid, mid = message.get('room_id'), message.get('msg_id')
        if not self.state.room(rid):
            self.status_label.setText('이미 나간 채팅방입니다.')
            return False
        win = self._find_main_window()
        if win is not None and hasattr(win, 'focus_message') and mid:
            win.focus_message(rid, mid)
        elif win is not None and hasattr(win, 'open_room'):
            win.open_room(rid)
        else:
            self.state.room_opened.emit(rid)
        return True

    def menu_for(self, kind, item):
        menu = QMenu(self)
        m = item.data(Qt.UserRole)
        menu.addAction('채팅방에서 보기', lambda: self.show_in_room(m))
        if kind == 'file':
            menu.addAction('저장…', lambda: self.save_file(m))
        elif kind == 'link':
            url = item.data(Qt.UserRole + 1)
            menu.addAction('브라우저에서 열기', lambda: self.open_link(url))
        return menu

    def _show_menu(self, kind, pos):
        lst = self.lists[kind]
        item = lst.itemAt(pos)
        if item is not None:
            self.menu_for(kind, item).exec_(lst.viewport().mapToGlobal(pos))

    def done(self, result):
        for sig, slot in ((self.state.drawer_loaded, self.on_loaded),
                          (self.state.attachment_loaded, self._on_attachment)):
            try:
                sig.disconnect(slot)
            except TypeError:
                pass
        super().done(result)


__all__ = ['TalkDrawerDialog', 'links_of', 'human_size']
