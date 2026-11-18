# -*- coding: utf-8 -*-
"""채팅방에서 여는 대화상자: 초대 · 투표 만들기 · 사진 보기 · 서랍 · 사다리 · 톡게시판 · 연락처 보내기 · 대화 요약
(chat_room.py 가 너무 커져 나눴다. chat_room 에서 그대로 import 할 수 있다)"""

import html
import os
import re
from datetime import datetime

from PyQt5.QtCore import (Qt, QRectF, QSize)
from PyQt5.QtGui import (QPainter, QPixmap, QTextCursor, QIcon, QTextBlockFormat)
from PyQt5.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea,
                             QPushButton, QTextEdit, QLineEdit, QListWidget, QListWidgetItem,
                             QCheckBox, QApplication, QFileDialog, QMessageBox, QTabWidget,
                             QListView, QAbstractItemView, QStackedWidget)

from client.kakao import theme, osenv
from client.kakao.widgets import exec_and_release

from client.kakao.state import (parse_dt)
from client.kakao.widgets import (paint_icon, user_avatar_pixmap, make_icon, decode_pixmap,
                                  human_size)
from client.kakao.room_base import (APP_NAME, AV, fmt_time, event_when, _label, icon_pixmap, _Dialog, dialog_qss, _buttons)


class PollCreateDialog(_Dialog):
    def __init__(self, parent=None):
        super().__init__('투표 만들기', parent)
        self.resize(360, 420)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.question = QLineEdit(self)
        self.question.setPlaceholderText('투표 제목')
        v.addWidget(self.question)
        v.addWidget(_label('항목', 'dlgHint', self))
        self.options_box = QVBoxLayout()
        self.options_box.setSpacing(6)
        v.addLayout(self.options_box)
        self.option_edits = []
        add = QPushButton('+ 항목 추가', self)
        add.clicked.connect(lambda: self.add_option())
        v.addWidget(add)
        self.multiple = QCheckBox('복수 선택 허용', self)
        v.addWidget(self.multiple)
        v.addStretch(1)
        box, self.ok, _ = _buttons(self, '완료')
        v.addWidget(box)
        for _ in range(2):
            self.add_option()
        self.question.textChanged.connect(self._validate)
        self._validate()

    def add_option(self, text=''):
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(text, row)
        edit.setPlaceholderText(f'항목 {len(self.option_edits) + 1}')
        edit.textChanged.connect(self._validate)
        rm = QPushButton(row)
        rm.setIcon(make_icon('close', theme.c('text_sub'), 14))
        rm.setAccessibleName('항목 삭제')
        rm.setFixedWidth(34)
        rm.clicked.connect(lambda: self._remove(row, edit))
        h.addWidget(edit, 1)
        h.addWidget(rm)
        self.options_box.addWidget(row)
        self.option_edits.append(edit)
        self._validate()
        return edit

    def _remove(self, row, edit):
        if len(self.option_edits) <= 2:
            return
        self.option_edits.remove(edit)
        row.deleteLater()
        self._validate()

    def values(self):
        opts = [e.text().strip() for e in self.option_edits if e.text().strip()]
        return self.question.text().strip(), opts, self.multiple.isChecked()

    def _validate(self):
        q, opts, _ = self.values()
        self.ok.setEnabled(bool(q) and len(opts) >= 2)


class InviteRow(QWidget):
    """초대 목록 한 줄: 프로필(상태 점) · 이름 · 회사/부서/직함 · 선택 체크"""

    def __init__(self, state, username, parent=None):
        super().__init__(parent)
        self.username = username
        self.checked = False
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 12, 6)
        h.setSpacing(10)
        av = QLabel(self)
        av.setFixedSize(AV, AV)
        av.setPixmap(user_avatar_pixmap(state, username, AV, show_presence=True))
        h.addWidget(av)
        v = QVBoxLayout()
        v.setSpacing(1)
        self.name = _label(state.display_name(username) + (' · AI 비서' if state.is_bot(username) else ''),
                           'inviteName', self)
        org = state.org_text(username)
        self.org = _label(org, 'inviteOrg', self, wrap=True)
        self.org.setVisible(bool(org))
        v.addWidget(self.name)
        v.addWidget(self.org)
        h.addLayout(v, 1)
        self.check = QLabel(self)
        self.check.setFixedSize(22, 22)
        h.addWidget(self.check)
        self.search_text = ' '.join([state.display_name(username), username, org]).lower()
        self.set_checked(False)

    def set_checked(self, on):
        self.checked = on
        size = 22
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, size - 2, size - 2)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc('yellow'))
            p.drawEllipse(r)
            paint_icon(p, 'check', QRectF(4, 4, size - 8, size - 8), theme.c('on_accent'), stroke=2.4)
        else:
            from PyQt5.QtGui import QPen
            p.setPen(QPen(theme.qc('text_hint'), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
        p.end()
        self.check.setPixmap(pm)


class _RowsOnDemand(dict):
    """이름 → 행 위젯. 아직 만들지 않은 행은 찾을 때 만든다 (rows[이름] · rows.get(이름))"""

    def __init__(self, build):
        super().__init__()
        self._build = build

    def __missing__(self, key):
        row = self._build(key)
        if row is None:
            raise KeyError(key)
        return row

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return dict.__contains__(self, key) or self._build(key) is not None


class InviteDialog(_Dialog):
    """대화상대 초대: 검색, 선택 칩, 초대 (n) 버튼. exec_() 없이도 select()/selected()/accept()로 사용 가능"""

    def __init__(self, state, room_id, parent=None):
        super().__init__(f'대화상대 초대 - {APP_NAME}', parent)
        self.state, self.room_id = state, room_id
        self.resize(360, 540)
        self.setStyleSheet(dialog_qss() + f"""
            QLabel#inviteName {{ color: {theme.c('text')}; font-size: {theme.fs('emphasis')}px; }}
            QLabel#inviteOrg {{ color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#inviteHint {{ color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;
                background: {theme.c('bg_alt')}; border-radius: 6px; padding: 7px 10px; }}
            QPushButton#chip {{ background: {theme.c('bg_alt')}; color: {theme.c('text')};
                border: 1px solid {theme.c('border')}; border-radius: 13px; padding: 4px 10px;
                font-size: {theme.fs('meta')}px; }}
            QPushButton#chip:hover {{ background: {theme.c('bg_hover')}; }}
            QScrollArea#chipArea, QWidget#chipViewport, QWidget#chipHost {{ background: transparent; }}
            QListWidget#inviteList {{ border: none; }}
            QListWidget#inviteList::item {{ padding: 0; }}
            QListWidget#inviteList::item:selected {{ background: transparent; }}
            QListWidget#inviteList::item:hover {{ background: {theme.c('bg_hover')}; }}
        """)
        room = state.room(room_id) or {}
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(8)
        title = _label('대화상대 초대', 'inviteName', self)
        title.setStyleSheet(f'font-size: {theme.fs('lead')}px; font-weight: bold;')
        v.addWidget(title)
        self.hint = None
        if room.get('type') in ('direct', 'self'):
            self.hint = _label('1:1 채팅방에서 초대하면 새 그룹 채팅방이 만들어집니다', 'inviteHint', self, wrap=True)
            v.addWidget(self.hint)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('이름, 회사, 부서 검색')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.set_filter)
        v.addWidget(self.search)
        # 선택한 사람 칩
        self.chip_area = QScrollArea(self)
        self.chip_area.setWidgetResizable(True)
        self.chip_area.setFrameShape(QFrame.NoFrame)
        self.chip_area.setFixedHeight(theme.px(40))   # 칩 글자 배율에 맞춘다
        self.chip_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chip_area.setObjectName('chipArea')
        self.chip_area.viewport().setObjectName('chipViewport')
        chip_host = QWidget()
        chip_host.setObjectName('chipHost')
        self.chip_layout = QHBoxLayout(chip_host)
        self.chip_layout.setContentsMargins(0, 2, 0, 2)
        self.chip_layout.setSpacing(6)
        self.chip_area.setWidget(chip_host)
        self.chip_area.hide()
        v.addWidget(self.chip_area)
        self.chips = {}
        # 목록: 직원이 수백 명이면 행 위젯을 다 만드는 데 0.3초가 넘는다 → 보이는 행만 만든다 (RowList).
        # 고른 사람 · 검색은 행 위젯이 아니라 이름으로 기억하고, rows[이름] 은 찾을 때 그 행을 만든다
        from client.kakao.chats_tab import RowList
        self.list = RowList(self)
        self.list.setObjectName('inviteList')
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setContextMenuPolicy(Qt.NoContextMenu)
        self.rows = _RowsOnDemand(self._row_for)
        self._items = {}
        self._search = {}
        self._checked = set()
        self._order = []
        for u in state.invitable_users(room_id):
            name = u['username']
            self._items[name] = self.list.add_lazy_row(lambda n=name: self._make_row(n), name, height=52)
            self._search[name] = ' '.join([state.display_name(name), name, state.org_text(name)]).lower()
        self.list.build_visible()
        self.list.itemClicked.connect(lambda it: self.toggle(it.data(Qt.UserRole)))
        self.empty = _label('초대할 수 있는 대화상대가 없습니다.', 'inviteOrg', self)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setVisible(not self._items)
        v.addWidget(self.empty)
        v.addWidget(self.list, 1)
        box, self.ok, _ = _buttons(self, '초대')
        v.addWidget(box)
        self._refresh()

    def _make_row(self, name):
        row = InviteRow(self.state, name, self.list)
        if name in self._checked:
            row.set_checked(True)
        dict.__setitem__(self.rows, name, row)
        return row

    def _row_for(self, name):
        item = self._items.get(name)
        return self.list.itemWidget(item) if item is not None else None

    def toggle(self, username):
        if username not in self._items:
            return
        on = username not in self._checked
        if on:
            self._checked.add(username)
            self._order.append(username)
        else:
            self._checked.discard(username)
            if username in self._order:
                self._order.remove(username)
        row = dict.get(self.rows, username)          # 아직 만들지 않은 행은 만들 때 표시한다
        if row is not None:
            row.set_checked(on)
        self._refresh()

    def select(self, usernames):
        for u in usernames:
            if u in self._items and u not in self._checked:
                self.toggle(u)

    def selected(self):
        return list(self._order)

    def set_filter(self, text):
        q = (text or '').strip().lower()
        visible = 0
        for name, item in self._items.items():
            hidden = bool(q) and q not in self._search[name]
            item.setHidden(hidden)
            visible += 0 if hidden else 1
        self.list.build_visible()
        self.empty.setText('검색 결과가 없습니다.' if q else '초대할 수 있는 대화상대가 없습니다.')
        self.empty.setVisible(visible == 0)
        return visible

    def visible_usernames(self):
        return [n for n, it in self._items.items() if not it.isHidden()]

    def _refresh(self):
        for chip in self.chips.values():
            chip.hide()
            chip.deleteLater()
        self.chips = {}
        while self.chip_layout.count():
            self.chip_layout.takeAt(0)
        for u in self._order:
            chip = QPushButton(f'{self.state.display_name(u)}  ✕', self.chip_area.widget())
            chip.setObjectName('chip')
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, name=u: self.toggle(name))
            self.chip_layout.addWidget(chip)
            self.chips[u] = chip
        self.chip_layout.addStretch(1)
        if self.chips:   # 칩 실제 높이(+위아래 여백)보다 낮아 잘리지 않게
            chip_h = max(ch.sizeHint().height() for ch in self.chips.values()) + 4
            self.chip_area.setFixedHeight(max(theme.px(40), chip_h))
        self.chip_area.setVisible(bool(self._order))
        n = len(self._order)
        self.ok.setText(f'초대 ({n})' if n else '초대')
        self.ok.setEnabled(n > 0)


class ImageViewer(_Dialog):
    def __init__(self, data, name, parent=None):
        super().__init__(name or '사진', parent)
        self.data, self.name = data, name
        v = QVBoxLayout(self)
        lab = QLabel(self)
        lab.setAlignment(Qt.AlignCenter)
        pm = decode_pixmap(data) or QPixmap()
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        mw = int(avail.width() * 0.8) if avail else 900
        mh = int(avail.height() * 0.75) if avail else 700
        if not pm.isNull() and (pm.width() > mw or pm.height() > mh):
            pm = pm.scaled(mw, mh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lab.setPixmap(pm)
        v.addWidget(lab, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        save = QPushButton('저장', self)
        save.setObjectName('primary')
        save.clicked.connect(self.save)
        close = QPushButton('닫기', self)
        close.clicked.connect(self.accept)
        h.addWidget(save)
        h.addWidget(close)
        v.addLayout(h)

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, '사진 저장', os.path.join(osenv.user_dir('pictures'),
                                                                         osenv.safe_filename(self.name, 'image.png')))
        if not path:
            return False
        try:
            with open(path, 'wb') as f:
                f.write(self.data)
        except OSError as e:          # 쓸 수 없는 폴더 · 디스크 가득 참 → 앱이 꺼지지 않고 알려 준다
            QMessageBox.warning(self, '사진 저장', f'저장하지 못했습니다: {e}')
            return False
        osenv.mark_from_network(path)        # 받은 파일 표시 (채팅방 저장 · 서랍과 같게)
        return True


class DrawerDialog(_Dialog):
    """채팅방 서랍: 사진 / 파일 / 링크 / 일정"""

    def __init__(self, win, parent=None):
        super().__init__(f'{win.state.room_title(win.room_id)} · 서랍', parent)
        self.win, self.state = win, win.state
        self.resize(380, 520)
        photos, files, links = self.state.drawer_items(win.room_id)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        v.addWidget(self.tabs)
        # 사진
        self.photos = QListWidget(self)
        self.photos.setViewMode(QListView.IconMode)
        self.photos.setIconSize(QSize(100, 100))
        self.photos.setGridSize(QSize(112, 112))
        self.photos.setResizeMode(QListView.Adjust)
        self.photos.setMovement(QListView.Static)
        self._photo_items = {}
        for m in reversed(photos):
            aid = (m.get('attachment') or {}).get('attachment_id')
            item = QListWidgetItem()
            item.setData(Qt.UserRole, m['msg_id'])
            item.setToolTip(f"{self.state.display_name(m['sender'])} · {fmt_time(m['timestamp'])}")
            item.setIcon(QIcon(icon_pixmap('image', theme.c('text_hint'), 100)))
            self.photos.addItem(item)
            self._photo_items.setdefault(aid, []).append(item)
            cached = self.state.attachments.get(aid)
            if cached:
                self._set_photo(aid, cached[0])
            else:
                self.state.request_attachment(aid)
        self.photos.itemDoubleClicked.connect(
            lambda it: self.win.open_image(self.state.find_message(self.win.room_id, it.data(Qt.UserRole))))
        self.state.attachment_loaded.connect(self._on_loaded)
        self.tabs.addTab(self._wrap(self.photos, photos, '사진이 없습니다.'), f'사진 {len(photos)}')
        # 파일
        self.files = QListWidget(self)
        for m in reversed(files):
            att = m.get('attachment') or {}
            dt = parse_dt(m['timestamp'])
            item = QListWidgetItem(QIcon(icon_pixmap('file', theme.c('text_sub'), 24)),
                                   f"{att.get('name', '')}\n{human_size(att.get('size'))} · "
                                   f"{dt.strftime('%Y.%m.%d') if dt else ''}")
            item.setData(Qt.UserRole, m['msg_id'])
            self.files.addItem(item)
        self.files.itemDoubleClicked.connect(
            lambda it: self.win.save_attachment(self.state.find_message(self.win.room_id, it.data(Qt.UserRole))))
        self.tabs.addTab(self._wrap(self.files, files, '파일이 없습니다.'), f'파일 {len(files)}')
        # 링크
        self.links = QListWidget(self)
        for url, m in reversed(links):
            item = QListWidgetItem(f"{url}\n{self.state.display_name(m['sender'])} · {fmt_time(m['timestamp'])}")
            item.setData(Qt.UserRole, url)
            self.links.addItem(item)
        self.links.itemDoubleClicked.connect(lambda it: osenv.open_url(it.data(Qt.UserRole)))
        self.tabs.addTab(self._wrap(self.links, links, '링크가 없습니다.'), f'링크 {len(links)}')
        # 일정 (이 채팅방에서 만든 일정, 지난 일정은 흐리게)
        events = self.state.events_in_room(win.room_id) if hasattr(self.state, 'events_in_room') else []
        self.events = QListWidget(self)
        now = datetime.now()
        for ev in events:
            end = parse_dt(ev.get('end')) or parse_dt(ev.get('start'))
            past = end is not None and end.replace(tzinfo=None) < now
            line2 = event_when(ev) + (f" · {ev['location']}" if ev.get('location') else '')
            icon_color = theme.c('text_hint') if past else (ev.get('color') or theme.c('text_sub'))
            item = QListWidgetItem(QIcon(icon_pixmap('calendar', icon_color, 24)),
                                   f"{ev.get('title', '')}\n{line2}")
            item.setData(Qt.UserRole, ev['event_id'])
            item.setData(Qt.UserRole + 1, past)
            if past:
                item.setForeground(theme.qc('text_hint'))
                item.setToolTip('지난 일정')
            self.events.addItem(item)
        self.events.itemDoubleClicked.connect(lambda it: self.open_event(it.data(Qt.UserRole)))
        self.tabs.addTab(self._wrap(self.events, events, '이 채팅방의 일정이 없습니다.'), f'일정 {len(events)}')
        v.addWidget(_label('더블클릭하면 열거나 저장합니다.', 'dlgHint', self))

    def open_event(self, event_id):
        ev = self.state.events.get(event_id)
        if ev is None:
            return
        try:
            from client.kakao.event_dialog import EventDetailDialog
        except ImportError:
            self.win.toast('일정 기능을 불러올 수 없습니다.')
            return
        exec_and_release(EventDetailDialog(self.state, ev, self))
    def _wrap(self, lst, items, empty):
        if items:
            return lst
        w = QWidget(self)
        lv = QVBoxLayout(w)
        lab = _label(empty, 'dlgHint', w)
        lab.setAlignment(Qt.AlignCenter)
        lv.addWidget(lab)
        lst.hide()
        return w

    def _set_photo(self, aid, data):
        pm = decode_pixmap(data)
        if pm is not None:
            pm = pm.scaled(100, 100, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            for item in self._photo_items.get(aid, []):
                item.setIcon(QIcon(pm))

    def _on_loaded(self, aid, data, name, mime):
        if aid in self._photo_items:
            self._set_photo(aid, data)

    def done(self, result):
        try:
            self.state.attachment_loaded.disconnect(self._on_loaded)
        except TypeError:
            pass
        super().done(result)


class LadderDialog(_Dialog):
    """사다리 게임 만들기: 참가자(기본 = 대화상대) · 결과, 2~10개이고 개수가 같아야 한다"""
    MIN, MAX = 2, 10

    def __init__(self, state, room_id, parent=None):
        super().__init__('사다리 게임', parent)
        self.state, self.room_id = state, room_id
        self.resize(380, 480)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.addWidget(_label('참가자와 결과를 입력하세요. (2~10개, 개수 동일)', 'dlgHint', self))
        head = QHBoxLayout()
        head.addWidget(_label('참가자', 'dlgHint', self), 1)
        head.addSpacing(18)
        head.addWidget(_label('결과', 'dlgHint', self), 1)
        head.addSpacing(40)
        v.addLayout(head)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        v.addLayout(self.rows_box)
        self.rows = []      # (row widget, 참가자 edit, 결과 edit, 삭제 버튼)
        self.add_button = QPushButton('+ 추가', self)
        self.add_button.clicked.connect(lambda: self.add_row())
        v.addWidget(self.add_button)
        self.error = _label('', 'dlgHint', self, wrap=True)
        v.addWidget(self.error)
        v.addStretch(1)
        box, self.ok, _ = _buttons(self, '사다리 만들기')
        v.addWidget(box)
        room = state.room(room_id) or {}
        names = [state.member_name(room_id, u) for u in room.get('members', [])][:self.MAX]
        while len(names) < self.MIN:
            names.append('')
        for i, name in enumerate(names):
            self.add_row(name, '당첨' if i == 0 else '꽝')
        self._validate()

    def add_row(self, participant='', result=''):
        if len(self.rows) >= self.MAX:
            return None
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        pe = QLineEdit(participant, row)
        pe.setPlaceholderText(f'참가자 {len(self.rows) + 1}')
        arrow = _label('→', 'dlgHint', row)
        re_ = QLineEdit(result, row)
        re_.setPlaceholderText(f'결과 {len(self.rows) + 1}')
        rm = QPushButton(row)
        rm.setIcon(make_icon('close', theme.c('text_sub'), 14))
        rm.setAccessibleName('항목 삭제')
        rm.setFixedWidth(34)
        entry = (row, pe, re_, rm)
        rm.clicked.connect(lambda: self.remove_row(entry))
        for e in (pe, re_):
            e.textChanged.connect(self._validate)
        h.addWidget(pe, 1)
        h.addWidget(arrow)
        h.addWidget(re_, 1)
        h.addWidget(rm)
        self.rows_box.addWidget(row)
        self.rows.append(entry)
        self._validate()
        return entry

    def remove_row(self, entry):
        if len(self.rows) <= self.MIN or entry not in self.rows:
            return
        self.rows.remove(entry)
        entry[0].hide()
        entry[0].deleteLater()
        self._validate()

    def values(self):
        parts = [pe.text().strip() for _, pe, _, _ in self.rows if pe.text().strip()]
        results = [re_.text().strip() for _, _, re_, _ in self.rows if re_.text().strip()]
        return parts, results

    def validation_error(self):
        parts, results = self.values()
        if len(parts) < self.MIN:
            return '참가자를 2명 이상 입력하세요.'
        if len(parts) > self.MAX:
            return '최대 10명까지 참여할 수 있습니다.'
        if len(parts) != len(results):
            return '참가자와 결과의 개수가 같아야 합니다.'
        if len(set(parts)) != len(parts):
            return '참가자 이름이 겹치지 않게 입력하세요.'
        return ''

    def _validate(self):
        if not hasattr(self, 'ok'):
            return
        err = self.validation_error()
        self.error.setText(err)
        self.ok.setEnabled(not err)
        self.add_button.setEnabled(len(self.rows) < self.MAX)
        for _, _, _, rm in self.rows:
            rm.setEnabled(len(self.rows) > self.MIN)


class BoardDialog(_Dialog):
    """톡게시판: 글 목록 / 글 보기(댓글·삭제) / 글쓰기"""
    LIST, POST, WRITE = 0, 1, 2

    def __init__(self, state, room_id, parent=None, post_id=None):
        super().__init__(f'{state.room_title(room_id)} · 톡게시판', parent)
        self.state, self.room_id = state, room_id
        self.current_post_id = None
        self._pending_post = post_id
        self.resize(380, 540)
        self.setStyleSheet(dialog_qss() + f"""
            QLabel#boardTitle {{ color: {theme.c('text')}; font-size: {theme.fs('lead')}px; font-weight: bold; }}
            QLabel#boardBody {{ color: {theme.c('text')}; font-size: {theme.fs('emphasis')}px; }}
            QListWidget#commentList::item {{ padding: 6px 4px; border-bottom: 1px solid {theme.c('border')}; }}
        """)
        v = QVBoxLayout(self)
        self.stack = QStackedWidget(self)
        v.addWidget(self.stack)
        # ---- 목록
        page = QWidget(self)
        lv = QVBoxLayout(page)
        lv.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(_label('톡게시판', 'boardTitle', page))
        top.addStretch(1)
        self.write_button = QPushButton('글쓰기', page)
        self.write_button.setObjectName('primary')
        self.write_button.clicked.connect(self.show_write)
        top.addWidget(self.write_button)
        lv.addLayout(top)
        self.post_list = QListWidget(page)
        self.post_list.itemClicked.connect(lambda it: self.show_post(it.data(Qt.UserRole)))
        lv.addWidget(self.post_list, 1)
        self.empty = _label('게시글을 불러오는 중…', 'dlgHint', page)
        self.empty.setAlignment(Qt.AlignCenter)
        lv.addWidget(self.empty)
        self.stack.addWidget(page)
        # ---- 글 보기
        page = QWidget(self)
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.back_button = QPushButton('‹ 목록', page)
        self.back_button.clicked.connect(self.show_list)
        bar.addWidget(self.back_button)
        bar.addStretch(1)
        self.delete_button = QPushButton('삭제', page)
        self.delete_button.clicked.connect(self.delete_post)
        bar.addWidget(self.delete_button)
        pv.addLayout(bar)
        self.post_title = _label('', 'boardTitle', page, wrap=True)
        self.post_meta = _label('', 'dlgHint', page)
        self.post_body = _label('', 'boardBody', page, wrap=True)
        self.post_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pv.addWidget(self.post_title)
        pv.addWidget(self.post_meta)
        pv.addWidget(self.post_body)
        self.comment_head = _label('댓글', 'dlgHint', page)
        pv.addWidget(self.comment_head)
        self.comment_list = QListWidget(page)
        self.comment_list.setObjectName('commentList')
        self.comment_list.setWordWrap(True)
        pv.addWidget(self.comment_list, 1)
        crow = QHBoxLayout()
        self.comment_edit = QLineEdit(page)
        self.comment_edit.setPlaceholderText('댓글을 입력하세요')
        self.comment_edit.returnPressed.connect(self.submit_comment)
        self.comment_button = QPushButton('등록', page)
        self.comment_button.setObjectName('primary')
        self.comment_button.clicked.connect(self.submit_comment)
        crow.addWidget(self.comment_edit, 1)
        crow.addWidget(self.comment_button)
        pv.addLayout(crow)
        self.stack.addWidget(page)
        # ---- 글쓰기
        page = QWidget(self)
        wv = QVBoxLayout(page)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.addWidget(_label('새 글', 'boardTitle', page))
        self.title_edit = QLineEdit(page)
        self.title_edit.setPlaceholderText('제목')
        self.content_edit = QTextEdit(page)
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setPlaceholderText('내용을 입력하세요')
        wv.addWidget(self.title_edit)
        wv.addWidget(self.content_edit, 1)
        wrow = QHBoxLayout()
        wrow.addStretch(1)
        cancel = QPushButton('취소', page)
        cancel.clicked.connect(self.show_list)
        self.post_button = QPushButton('등록', page)
        self.post_button.setObjectName('primary')
        self.post_button.clicked.connect(self.submit_post)
        self.title_edit.textChanged.connect(lambda t: self.post_button.setEnabled(bool(t.strip())))
        self.post_button.setEnabled(False)
        wrow.addWidget(cancel)
        wrow.addWidget(self.post_button)
        wv.addLayout(wrow)
        self.stack.addWidget(page)

        state.board_changed.connect(self._on_board_changed)
        self.refresh_list()
        if post_id is not None and self._post(post_id):
            self.show_post(post_id)
        state.request_board(room_id)

    def _post(self, post_id):
        return self.state.boards.get(self.room_id, {}).get(post_id)

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    @staticmethod
    def _when(ts):
        dt = parse_dt(ts)
        return f'{dt.year}.{dt.month:02d}.{dt.day:02d} {fmt_time(dt)}' if dt else ''

    def refresh_list(self):
        self.post_list.clear()
        posts = self.state.board_posts(self.room_id)
        for post in posts:
            n = len(post.get('comments') or [])
            item = QListWidgetItem(f"📌 {post.get('title', '')}\n"
                                   f"{self.state.member_name(self.room_id, post.get('author'))} · "
                                   f"{self._when(post.get('created_at'))} · 댓글 {n}")
            item.setData(Qt.UserRole, post['post_id'])
            self.post_list.addItem(item)
        if self.room_id in self.state.boards:
            self.empty.setText('아직 게시글이 없습니다. 첫 글을 써보세요.')
        self.empty.setVisible(not posts)
        self.post_list.setVisible(bool(posts))

    def show_list(self):
        self.current_post_id = None
        self.refresh_list()
        self.stack.setCurrentIndex(self.LIST)

    def show_post(self, post_id):
        post = self._post(post_id)
        if post is None:
            self.show_list()
            return
        self.current_post_id = post_id
        self.post_title.setText(post.get('title', ''))
        self.post_meta.setText(f"{self.state.member_name(self.room_id, post.get('author'))} · "
                               f"{self._when(post.get('created_at'))}")
        self.post_body.setText(post.get('content', ''))
        self.delete_button.setVisible(post.get('author') == self.state.me)
        comments = post.get('comments') or []
        self.comment_head.setText(f'댓글 {len(comments)}')
        self.comment_list.clear()
        for cm in comments:
            self.comment_list.addItem(f"{self.state.member_name(self.room_id, cm.get('author'))} · "
                                      f"{self._when(cm.get('created_at'))}\n{cm.get('content', '')}")
        self.stack.setCurrentIndex(self.POST)

    def show_write(self):
        self.title_edit.clear()
        self.content_edit.clear()
        self.stack.setCurrentIndex(self.WRITE)
        self.title_edit.setFocus()

    def submit_post(self):
        title = self.title_edit.text().strip()
        if not title:
            return
        self.state.post_board(self.room_id, title, self.content_edit.toPlainText().strip())
        self.show_list()

    def submit_comment(self):
        text = self.comment_edit.text().strip()
        if text and self.current_post_id is not None:
            self.state.comment_board(self.current_post_id, text)
            self.comment_edit.clear()

    def delete_post(self):
        post = self._post(self.current_post_id)
        if post and post.get('author') == self.state.me and self.confirm('게시글 삭제', '이 글을 삭제할까요?'):
            self.state.delete_board(self.current_post_id)

    def _on_board_changed(self, room_id):
        if room_id != self.room_id:
            return
        if self._pending_post is not None and self._post(self._pending_post):
            pid, self._pending_post = self._pending_post, None
            self.show_post(pid)
            return
        if self.stack.currentIndex() == self.POST:
            self.show_post(self.current_post_id)     # 댓글 갱신 / 삭제되면 목록으로
        else:
            self.refresh_list()

    def done(self, result):
        try:
            self.state.board_changed.disconnect(self._on_board_changed)
        except TypeError:
            pass
        super().done(result)


class ContactPickDialog(_Dialog):
    """연락처 보내기: 보낼 사람 한 명 선택 (이름·소속 검색)"""

    def __init__(self, state, parent=None):
        super().__init__('연락처 보내기', parent)
        self.state = state
        self.resize(340, 460)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('이름, 회사, 부서 검색')
        self.search.setClearButtonEnabled(True)
        v.addWidget(self.search)
        self.list = QListWidget(self)
        self.list.setIconSize(QSize(32, 32))
        v.addWidget(self.list, 1)
        box, self.ok, _ = _buttons(self, '보내기')
        self.ok.setEnabled(False)
        v.addWidget(box)
        self.search.textChanged.connect(self.refresh)
        self.list.currentItemChanged.connect(lambda cur, _p: self.ok.setEnabled(cur is not None))
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.refresh()

    def candidates(self):
        users = [u for u in self.state.friends(include_bots=False) if not self.state.is_blocked(u['username'])]
        me = self.state.user(self.state.me)
        return ([me] if me else []) + users

    def refresh(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for u in self.candidates():
            name = self.state.display_name(u['username'])
            org = self.state.org_text(u['username'])
            hay = f"{name} {org} {u['username']}".lower()
            if q and q not in hay:
                continue
            label = name + (' (나)' if u['username'] == self.state.me else '')
            item = QListWidgetItem(QIcon(user_avatar_pixmap(self.state, u['username'], 32)),
                                   f'{label}\n{org}' if org else label)
            item.setData(Qt.UserRole, u['username'])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def username(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None


# 대화 요약 유형 태그 색 (글자, 배경) — 모두 4.5:1 이상 (impeccable colorize)
SUMMARY_TAGS = {
    'light': {'결정': ('#1F2E6B', '#E3E8F7'), '요청': ('#7A4300', '#FCEBD2'),
              '일정': ('#0B5D52', '#D7F1EC'), '질문': ('#5B2C8A', '#EDE3F7')},
    'dark': {'결정': ('#C9D4FF', '#2A3560'), '요청': ('#FFD39A', '#4A3416'),
             '일정': ('#9FE6D8', '#163F39'), '질문': ('#E0C8FF', '#3B2856')},
}


def summary_html(text):
    """요약 문자열 → 위계 있는 HTML: 첫 줄 = 제목(bolder), [결정]/[요청]/[일정]/[질문] = 색 태그(colorize),
    키워드 = 칩, '공유:' = 보조색(layout). Claude 요약처럼 형식이 달라도 그대로 읽히게 한다."""
    tags = SUMMARY_TAGS['dark' if theme.is_dark() else 'light']
    sub, text_c = theme.c('text_sub'), theme.c('text')
    out = []
    lines = [l for l in (text or '').splitlines() if l.strip()]
    for i, raw in enumerate(lines):
        line = html.escape(raw.strip())
        if i == 0:
            out.append(f'<p style="margin:0 0 10px 0; font-size:{theme.fs("emphasis")}px; font-weight:700; '
                       f'color:{text_c};">{line}</p>')
            continue
        if raw.startswith('주요 키워드:'):
            words = [w.strip() for w in raw.split(':', 1)[1].split(',') if w.strip()]
            chips = ' '.join(f'<span style="background:{theme.c("bg_alt")}; color:{text_c};">&nbsp;{html.escape(w)}&nbsp;</span>'
                             for w in words)
            out.append(f'<p style="margin:0 0 10px 0; color:{sub};">키워드&nbsp; {chips}</p>')
            continue
        if raw.startswith('공유:'):
            out.append(f'<p style="margin:10px 0 0 0; color:{sub}; font-size:{theme.fs("meta")}px;">{line}</p>')
            continue
        m = re.match(r'^•\s*\[(결정|요청|일정|질문)\]\s*(.*)$', raw.strip())
        if m:
            fg, bg = tags[m.group(1)]
            out.append(f'<p style="margin:0 0 6px 0; color:{text_c};">'
                       f'<span style="background:{bg}; color:{fg}; font-weight:700;">&nbsp;{m.group(1)}&nbsp;</span>'
                       f'&nbsp; {html.escape(m.group(2))}</p>')
            continue
        out.append(f'<p style="margin:0 0 6px 0; color:{text_c};">{line}</p>')
    return ''.join(out)


class SummaryDialog(_Dialog):
    """대화 요약 결과 (카카오톡 v26.6.0 '대화 요약')"""

    def __init__(self, state, room_id, parent=None):
        super().__init__('대화 요약', parent)
        self.state, self.room_id = state, room_id
        self.resize(380, 420)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.heading = _label(f'{state.room_title(room_id)} · 대화 요약', 'dlgTitle', self)
        self.heading.setStyleSheet(f"font-size: {theme.fs('title')}px; font-weight: bold;")
        v.addWidget(self.heading)
        self._plain = ''
        self.meta = _label('', 'dlgHint', self)
        v.addWidget(self.meta)
        from client.kakao.voice import BusyBar
        self.busy = BusyBar(self)
        v.addWidget(self.busy)
        self.body = QTextEdit(self)
        self.body.setReadOnly(True)
        v.addWidget(self.body, 1)
        row = QHBoxLayout()
        self.copy_button = QPushButton('복사', self)
        self.copy_button.clicked.connect(self.copy)
        self.copy_button.setEnabled(False)
        close = QPushButton('닫기', self)
        close.setObjectName('primary')
        close.clicked.connect(self.accept)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        self.set_loading()

    def set_loading(self):
        self.meta.setText('대화를 요약하는 중입니다…')
        self.body.clear()
        self.copy_button.setEnabled(False)
        self.busy.start()

    def set_result(self, p):
        self.busy.stop()
        source = 'AI 요약' if p.get('source') == 'claude' else '자동 요약 (핵심 문장 추출)'
        scope = '안 읽은 대화' if p.get('unread_only') else '최근 대화'
        self.meta.setText(f"{scope} {p.get('count', 0)}개 · {source}")
        self._plain = p.get('summary') or ''
        self.body.setHtml(summary_html(self._plain))
        cursor = self.body.textCursor()
        cursor.select(QTextCursor.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(150, QTextBlockFormat.ProportionalHeight)
        fmt.setBottomMargin(2)
        cursor.mergeBlockFormat(fmt)
        self.copy_button.setEnabled(bool(p.get('summary')))

    def text(self):
        return self._plain or self.body.toPlainText()

    def copy(self):
        QApplication.clipboard().setText(self.text())
        self.meta.setText(self.meta.text().split(' · 복사')[0] + ' · 복사했습니다')
