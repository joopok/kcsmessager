# -*- coding: utf-8 -*-
"""
채팅 탭 - 채팅방 목록(고정·알림끔·안 읽은 수·상대 시간) + 그룹 채팅 만들기 다이얼로그
채팅방 폴더 칩(전체/안읽음/즐겨찾기/사용자 폴더), 읽지 않고 미리보기, AI 비서 버튼
오픈채팅(찾기·참여·만들기, 목록의 '오픈' 배지), 톡서랍(전체 채팅방 사진·파일·링크) 버튼
공용 목록 위젯(ElidedLabel, StatusPill, RowList, TabHeader)도 이 모듈에 둔다.
"""

import copy
from datetime import datetime, date, timedelta

from PyQt5.QtCore import Qt, QObject, QSize, QRectF, QPoint, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtGui import QPainter, QFontMetrics, QColor, QPen, QPixmap, QIcon, QPainterPath
from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
                             QLineEdit, QLabel, QMenu, QMessageBox, QDialog, QPushButton,
                             QSizePolicy, QAbstractItemView, QFrame, QScrollArea, QTabWidget,
                             QPlainTextEdit, QInputDialog)

from client.kakao import theme
from client.kakao.state import parse_dt, BOT_NAME
from client.kakao.widgets import (IconButton, Avatar, Badge, make_icon, avatar_pixmap, underline_tabs_qss,
                                  check_box_qss, set_shown)


# ================================================================ 공용 위젯
class ElidedLabel(QWidget):
    """폭이 모자라면 '…'로 줄여 그리는 한 줄 라벨 (테마 색상은 그릴 때마다 조회)"""

    def __init__(self, text='', size=13, color_key='text', bold=False, align=Qt.AlignLeft, parent=None):
        super().__init__(parent)
        self._text = text or ''
        self._size = size
        self._color_key = color_key
        self._bold = bold
        self._align = align
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._update_geometry()

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text or ''
        self._update_geometry()
        self.update()

    def set_color_key(self, key):
        self._color_key = key
        self.update()

    def _font(self):
        return theme.font(self._size, self._bold)

    def _update_geometry(self):
        self.setFixedHeight(QFontMetrics(self._font()).height() + 2)
        self.updateGeometry()

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + 2, fm.height() + 2)

    def minimumSizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(min(fm.horizontalAdvance(self._text) + 2, fm.horizontalAdvance('가…') + 2), fm.height() + 2)

    def refresh_theme(self):
        self._update_geometry()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = self._font()
        p.setFont(f)
        p.setPen(theme.qc(self._color_key))
        fm = QFontMetrics(f)
        text = fm.elidedText(self._text, Qt.ElideRight, max(0, self.width() - 1))
        p.drawText(self.rect(), self._align | Qt.AlignVCenter, text)


class StatusPill(QWidget):
    """카카오톡 친구 목록 오른쪽의 회색 테두리 상태메시지 말풍선"""

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._text = text or ''
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.refresh_theme()

    def text(self):
        return self._text

    def _font(self):
        return theme.font('caption')

    def refresh_theme(self):
        fm = QFontMetrics(self._font())
        self.setFixedHeight(fm.height() + theme.px(10))
        self.setMaximumWidth(theme.px(170))
        set_shown(self, bool(self._text))   # 부모 없는 상태에서 setVisible(True) 는 최상위 창으로 번쩍인다
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + theme.px(22) + 4, fm.height() + theme.px(10))

    def minimumSizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(min(self.sizeHint().width(), fm.horizontalAdvance('가가…') + theme.px(22)),
                     fm.height() + theme.px(10))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.qc('border'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        f = self._font()
        p.setFont(f)
        p.setPen(theme.qc('text_sub'))
        pad = theme.px(11)
        text = QFontMetrics(f).elidedText(self._text, Qt.ElideRight, max(0, self.width() - pad * 2))
        p.drawText(self.rect().adjusted(pad, 0, -pad, 0), Qt.AlignCenter, text)


class RowList(QListWidget):
    """행 위젯을 담는 목록. 더블클릭/Enter → row_activated"""

    row_activated = pyqtSignal(QListWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.itemDoubleClicked.connect(self.row_activated)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem() is not None:
            self.row_activated.emit(self.currentItem())
            return
        super().keyPressEvent(event)

    def _fit_rows(self):
        """행 위젯 폭을 뷰포트에 맞춘다. 숨겨진 탭에서 만들어진 행은 기본 폭(640)으로 남아
        보이기 시작한 뒤에도 오른쪽(시간·배지)이 잘리므로, 표시/크기 변경 때마다 다시 맞춘다."""
        width = self.viewport().width()
        for i in range(self.count()):
            widget = self.itemWidget(self.item(i))
            if widget is not None and widget.width() != width:
                widget.resize(width, widget.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_rows()

    def showEvent(self, event):
        super().showEvent(event)
        self.doItemsLayout()
        self._fit_rows()

    def add_row(self, widget, data, selectable=True):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, data)
        if not selectable:
            item.setFlags(Qt.ItemIsEnabled)
        item.setSizeHint(QSize(1, widget.sizeHint().height()))
        self.addItem(item)
        self.setItemWidget(item, widget)
        return item

    def items(self):
        return [self.item(i) for i in range(self.count())]

    def find_data(self, data):
        for item in self.items():
            if item.data(Qt.UserRole) == data:
                return item
        return None


class TabHeader(QWidget):
    """탭 상단 헤더: 브랜드 강조 막대 + 제목 + 요약 부제목 + 오른쪽 아이콘 버튼, 아래 구분선"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName('tabHeader')
        self.title = QLabel(title)
        self.title.setObjectName('tabTitle')
        # 좁은 창에서도 헤더 버튼을 밀어내지 않도록 '…'로 줄어드는 부제목
        self.subtitle = ElidedLabel('', 'meta', 'text_sub')
        self.subtitle.setObjectName('tabSubtitle')
        self.subtitle.hide()
        self.buttons = []
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l') + 4, theme.sp('l'), theme.sp('m'), theme.sp('m'))
        col.setSpacing(theme.sp('xxs'))
        self._row = QHBoxLayout()
        self._row.setSpacing(theme.sp('xs'))
        # 제목 앞 강조 막대는 없앴다 (KCS Ink: 제목은 굵기와 크기로 선다) - 속성은 호환용으로만 남김
        self.accent = QFrame()
        self.accent.setObjectName('tabAccent')
        self.accent.hide()
        self._row.addWidget(self.title, 0, Qt.AlignVCenter)
        self._row.addStretch(1)
        col.addLayout(self._row)
        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.addWidget(self.subtitle)
        col.addLayout(sub_row)
        self.apply_theme()

    def add_button(self, icon_name, tooltip):
        return self.add_widget_button(IconButton(icon_name, tooltip, size=22))

    def add_widget_button(self, btn):
        """직접 만든 아이콘 버튼(refresh_icon()을 가진 IconButton 계열)을 오른쪽에 추가"""
        self.buttons.append(btn)
        self._row.addWidget(btn, 0, Qt.AlignVCenter)
        return btn

    def add_search(self, edit):
        """검색 칸을 헤더 안(부제목 아래 · 구분선 위)에 둔다. 헤더 밖 바로 아래에 두면 칸 윗변이 구분선에
        붙어 겹쳐 보였다 (2026-10-10). 칸을 숨기면 띄움까지 함께 사라진다"""
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, theme.sp('s') + 2, 0, 0)
        wrap.addWidget(edit)
        self.layout().addLayout(wrap)
        return edit

    def set_subtitle(self, text):
        """제목 아래 요약 (예: '친구 8명 · 온라인 4명'). 빈 문자열이면 숨김"""
        self.subtitle.setText(text or '')
        self.subtitle.setVisible(bool(text))
        self.subtitle.setToolTip(text or '')

    def apply_theme(self):
        self.setStyleSheet(
            f"QWidget#tabHeader {{ background: {theme.c('bg')}; border-bottom: 1px solid {theme.c('border')}; }}"
            f" QLabel#tabTitle {{ font-size: {theme.fs('display')}px; font-weight: bold; color: {theme.c('text')};"
            f" letter-spacing: -0.4px; }}"
            f" QFrame#tabAccent {{ background: {theme.c('yellow')}; border-radius: 2px; }}")
        self.subtitle.refresh_theme()
        for btn in self.buttons:
            btn.refresh_icon()


def make_search_edit(placeholder):
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.hide()
    return edit


def search_box_qss():
    # 둥근 모서리(16)가 칸 높이의 절반을 넘으면 Qt 는 모서리를 아예 둥글게 그리지 않는다 → 높이를 34px 이상으로
    return (f"QLineEdit {{ background: {theme.c('bg_alt')}; border: 1px solid transparent; border-radius: 16px;"
            f" padding: 7px 14px; min-height: 18px; font-size: {theme.fs('body')}px; }}"
            f" QLineEdit:focus {{ border: 1px solid {theme.c('yellow')}; background: {theme.c('input_bg')}; }}")


def icon_label(name, color_key, size=14):
    label = QLabel()
    label.setPixmap(make_icon(name, theme.c(color_key), size).pixmap(size, size))
    label.setFixedSize(size, size)
    return label


# ================================================================ 프로필 사진·상태(presence) 호환 헬퍼
PRESENCE_CHOICES = [
    ('online', '온라인', '#3DD16B'),
    ('away', '자리비움', '#F5B400'),
    ('busy', '다른 용무 중', '#FF4B3E'),
    ('meeting', '회의 중', '#8E6CEF'),
    ('vacation', '휴가 중', '#1FA2B8'),
]
PRESENCE_COLORS = {key: color for key, _label, color in PRESENCE_CHOICES}
PRESENCE_COLORS['offline'] = '#BDBDBD'
PRESENCE_LABELS = {key: label for key, label, _color in PRESENCE_CHOICES}
PRESENCE_LABELS['offline'] = '오프라인'


def presence_of(state, username):
    """사용자 상태: online/away/busy/meeting/vacation/offline"""
    fn = getattr(state, 'presence_of', None)
    if callable(fn):
        return fn(username)
    user = state.user(username)
    if user.get('presence') == 'vacation':
        return 'vacation'
    if not user.get('online'):
        return 'offline'
    return user.get('presence') or 'online'


def set_presence(state, presence):
    fn = getattr(state, 'set_presence', None)
    if callable(fn):
        fn(presence)
    else:
        state._send('presence_set', presence=presence)


def presence_dot_pixmap(presence, size=10):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(PRESENCE_COLORS.get(presence, '#BDBDBD')))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return pm


def user_pixmap(state, username, size, show_presence=False):
    """프로필 사진(있으면) 아바타. widgets.user_avatar_pixmap이 없으면 이니셜 아바타 + 상태 점"""
    try:
        from client.kakao.widgets import user_avatar_pixmap
    except ImportError:
        user_avatar_pixmap = None
    if user_avatar_pixmap is not None:
        return user_avatar_pixmap(state, username, size, show_presence=show_presence)
    pm = avatar_pixmap(username, state.display_name(username), size)
    if show_presence:
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        d = max(8, size * 0.26)
        p.setBrush(QColor(PRESENCE_COLORS.get(presence_of(state, username), '#BDBDBD')))
        p.setPen(QPen(QColor(theme.c('bg')), max(1.5, size * 0.05)))
        p.drawEllipse(QRectF(size - d, size - d, d, d))
        p.end()
    return pm


def company_of(state, username):
    return (state.user(username).get('company') or '').strip()


def foreign_company(state, username):
    """내 회사와 다르면 회사명, 같거나 정보 없으면 ''"""
    mine = company_of(state, state.me)
    theirs = company_of(state, username)
    if theirs and mine and theirs != mine and not state.user(username).get('is_bot'):
        return theirs
    return ''


class CompanyChip(QWidget):
    """다른 회사 사용자 표시용 회색 회사명 칩 (좁으면 '…'로 줄어든다)"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.refresh_theme()

    def text(self):
        return self._text

    def _font(self):
        return theme.font('micro', True)

    def refresh_theme(self):
        fm = QFontMetrics(self._font())
        self.setFixedHeight(fm.height() + theme.px(4))
        self.setMaximumWidth(fm.horizontalAdvance(self._text) + theme.px(12) + 4)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + theme.px(12) + 4, fm.height() + theme.px(4))

    def minimumSizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(min(self.sizeHint().width(), fm.horizontalAdvance('KC…') + theme.px(12)),
                     fm.height() + theme.px(4))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qc('bg_selected'))
        r = QRectF(self.rect())
        p.drawRoundedRect(r, 4, 4)
        f = self._font()
        p.setFont(f)
        p.setPen(theme.qc('text_sub'))
        pad = theme.px(6)
        text = QFontMetrics(f).elidedText(self._text, Qt.ElideRight, max(0, self.width() - pad * 2 + 2))
        p.drawText(r, Qt.AlignCenter, text)


class _AvatarRelay(QObject):
    """avatar_changed(str) → 인자 없는 slot() 중계. slot 의 주인(QObject)의 자식이라
    주인이 사라지면 함께 사라지고 연결도 자동으로 끊긴다 (짧게 열리는 대화상자 누수 방지).

    로그인 직후에는 구성원 사진 수십 장이 한꺼번에 도착한다. 사진마다 탭 전체를 다시 만들면
    (조직도 · 친구 · 채팅 목록 …) 메인 스레드가 1초 넘게 막혀 빈 창에 로딩 커서만 돌았다 (2026-10-10).
    그래서 COALESCE_MS 안에 몰려온 알림은 모아서 한 번만 slot() 을 부른다."""

    COALESCE_MS = 50

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        self._slot = slot
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.COALESCE_MS)
        self._timer.timeout.connect(self._slot)

    @pyqtSlot(str)
    def fire(self, _username=''):
        if not self._timer.isActive():
            self._timer.start()

    def flush(self):
        """기다리는 갱신이 있으면 지금 바로 (테스트 · 닫기 직전용)"""
        if self._timer.isActive():
            self._timer.stop()
            self._slot()


def connect_avatar_changed(state, slot):
    sig = getattr(state, 'avatar_changed', None)
    if sig is None:
        return
    owner = getattr(slot, '__self__', None)
    if isinstance(owner, QObject):
        relay = _AvatarRelay(slot, owner)
        sig.connect(relay.fire)
    else:
        sig.connect(lambda *_: slot())


# ================================================================ 시간 표기
def format_room_time(value, now=None):
    """오늘 → '오후 3:12', 어제 → '어제', 올해 → '10월 8일', 그 외 → '2025. 3. 1.'"""
    dt = parse_dt(value) if not isinstance(value, datetime) else value
    if dt is None:
        return ''
    now = now or datetime.now()
    today = now.date()
    d = dt.date()
    if d == today:
        hour = dt.hour % 12 or 12
        return f"{'오전' if dt.hour < 12 else '오후'} {hour}:{dt.minute:02d}"
    if d == today - timedelta(days=1):
        return '어제'
    if d.year == today.year:
        return f"{d.month}월 {d.day}일"
    return f"{d.year}. {d.month}. {d.day}."


# ================================================================ 채팅방 행
class RoomBadge(Badge):
    """알림 끈 방은 회색 배지"""

    def __init__(self, count=0, muted=False, parent=None):
        self.muted = muted
        super().__init__(count, parent)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        # 알림 끈 방: 회색 배지 (라이트 #6A6A6A 위 흰 글자 5.3:1, 다크 #ABABAB 위 어두운 글자 7:1 이상)
        p.setBrush(theme.qc('text_hint') if self.muted else theme.qc('badge'))
        r = QRectF(self.rect())
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(theme.qc('bg') if self.muted else theme.qc('on_badge'))
        p.setFont(theme.font('caption', bold=True))
        p.drawText(r, Qt.AlignCenter, self.text)


class RoomRow(QWidget):
    def __init__(self, state, room_id, parent=None):
        super().__init__(parent)
        self.room_id = room_id
        room = state.room(room_id) or {}
        avatar_size = theme.px(theme.AVATAR_ROW)   # 친구 탭 · 조직도 · 채팅방과 같은 사진 크기
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 9, 16, 9)
        row.setSpacing(12)

        self.avatar = Avatar(size=avatar_size)
        members = room.get('members', [])
        if room.get('type') in ('group', 'open'):
            others = [m for m in members if m != state.me] or members
            self.avatar.set_group(others, {m: state.display_name(m) for m in others})
        else:
            others = [m for m in members if m != state.me]
            who = others[0] if others else state.me
            self.avatar.setPixmap(user_pixmap(state, who, avatar_size))
        row.addWidget(self.avatar, 0, Qt.AlignVCenter)
        self.company = ''
        if room.get('type') == 'direct':
            others = [m for m in members if m != state.me]
            self.company = foreign_company(state, others[0]) if others else ''

        center = QVBoxLayout()
        center.setSpacing(3)
        center.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.setSpacing(5)
        title_row.setContentsMargins(0, 0, 0, 0)
        self.title = ElidedLabel(state.room_title(room_id), 'emphasis', 'text', bold=True)
        self.title.setMinimumWidth(min(self.title.sizeHint().width(), theme.px(44)))
        title_row.addWidget(self.title)
        self.count_label = None
        self.open_badge = None
        if room.get('type') == 'open':
            self.open_badge = OpenBadge()
            title_row.addWidget(self.open_badge)
        if room.get('type') in ('group', 'open'):
            self.count_label = ElidedLabel(str(len(members)), 13, 'text_hint')
            self.count_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.count_label.setFixedWidth(self.count_label.sizeHint().width())
            title_row.addWidget(self.count_label)
        if self.company:
            title_row.addWidget(CompanyChip(self.company))
        self.pinned = state.is_pinned(room_id)
        self.muted = state.is_muted(room_id)
        if self.pinned:
            title_row.addWidget(icon_label('pin', 'text_hint', theme.px(13)))
        if self.muted:
            title_row.addWidget(icon_label('bell_off', 'text_hint', theme.px(13)))
        title_row.addStretch(1)
        center.addLayout(title_row)
        last = room.get('last_message')
        preview = state.message_preview(last) if last else ''
        failed = getattr(state, 'room_has_failed', lambda _r: False)(room_id)
        if failed:   # 보내지 못한 메시지가 있으면 목록에서도 바로 보이게
            preview = '보내지 못한 메시지가 있습니다'
        draft = '' if failed else getattr(state, 'room_draft', lambda _r: '')(room_id)
        if draft and room_id not in getattr(state, 'active_rooms', ()):   # 쓰다 만 글 (§10.8)
            preview = f'[임시저장] {draft}'
        self.preview = ElidedLabel(preview.replace('\n', ' '), 'meta', 'danger_text' if failed else 'text_sub', bold=failed)
        center.addWidget(self.preview)
        row.addLayout(center, 1)

        right = QVBoxLayout()
        right.setSpacing(5)
        right.setContentsMargins(0, 0, 0, 0)
        ts = (last or {}).get('timestamp') or ''
        self.time = ElidedLabel(format_room_time(ts) if last else '', 'caption', 'text_hint', align=Qt.AlignRight)
        self.time.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.time.setFixedWidth(self.time.sizeHint().width())
        right.addWidget(self.time, 0, Qt.AlignRight)
        unread = state.room_unread(room_id)
        self.badge = RoomBadge(unread, self.muted)
        self.setAccessibleName(', '.join(x for x in (
            self.title.text(), f'안 읽은 메시지 {unread}개' if unread else '',
            preview.replace('\n', ' '), self.time.text()) if x))
        right.addWidget(self.badge, 0, Qt.AlignRight)
        right.addStretch(1)
        row.addLayout(right)


# ================================================================ 통화 기록 행 ('통화' 폴더)


def call_log_text(state, call):
    """(제목 줄 부가 정보, 아래 줄) - 예: ('보이스톡', '부재중 · 김철수님이 건 통화') / ('페이스톡', '발신 · 3:05')"""
    from client.kakao.state import fmt_duration
    from client.kakao.chat_room import CALL_MODES
    mode = CALL_MODES.get(call.get('mode'), '통화')
    length = fmt_duration(call.get('duration_seconds'))
    if call.get('missed'):
        return mode, f"부재중 · {state.display_name(call.get('host'))}님이 건 통화"
    if call.get('mode') == 'voiceroom':
        return mode, f"{len(call.get('participants') or [])}명 참여 · {length}"
    return mode, f"{'발신' if call.get('host') == state.me else '수신'} · {length}"


_call_icon_cache = {}


def call_icon(mode, missed, size):
    """통화 기록 원형 아이콘 (모드·부재중·크기·테마별로 한 번만 그린다)"""
    from client.kakao.chat_room import extra_icon_pixmap, CALL_ICONS   # 통화 아이콘은 chat_room 아이콘 세트
    color = theme.c('on_badge') if missed else theme.c('on_accent')
    bg = theme.c('badge') if missed else theme.c('yellow')
    key = (mode, missed, size, color, bg)
    if key not in _call_icon_cache:
        _call_icon_cache[key] = extra_icon_pixmap(CALL_ICONS.get(mode, 'phone'), color, size, bg=bg)
    return _call_icon_cache[key]


class CallLogRow(QWidget):
    """통화 기록 한 줄: [모드 아이콘] 채팅방 이름 / 부재중·발신·수신 · 길이 / 시각"""

    def __init__(self, state, call, parent=None):
        super().__init__(parent)
        self.call = call
        self.room_id = call['room_id']
        self.missed = bool(call.get('missed'))
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 9, 16, 9)
        row.setSpacing(12)
        size = theme.px(44)
        self.icon = QLabel()
        self.icon.setFixedSize(size, size)
        self.icon.setPixmap(call_icon(call.get('mode'), self.missed, size))
        row.addWidget(self.icon, 0, Qt.AlignVCenter)
        mode, sub = call_log_text(state, call)
        center = QVBoxLayout()
        center.setSpacing(3)
        self.title = ElidedLabel(state.room_title(self.room_id), 'emphasis', 'text', bold=True)
        center.addWidget(self.title)
        self.sub = ElidedLabel(f'{mode} · {sub}', 'meta', 'danger_text' if self.missed else 'text_sub', bold=self.missed)
        center.addWidget(self.sub)
        row.addLayout(center, 1)
        self.time = ElidedLabel(format_room_time(call.get('started_at') or ''), 11, 'text_hint', align=Qt.AlignRight)
        self.time.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.time.setFixedWidth(self.time.sizeHint().width())
        row.addWidget(self.time, 0, Qt.AlignTop | Qt.AlignRight)
        self.setAccessibleName(f"{state.room_title(self.room_id)} {mode} {sub}")


# ================================================================ 채팅방 폴더 칩 바
FOLDER_ALL = '전체'


def folder_chip_label(name, count):
    """'전체'는 개수 없이, 나머지는 '이름 N' (안읽음은 안 읽은 채팅방 수, 통화는 부재중이 있을 때만 그 수)"""
    if name == FOLDER_ALL or (name == '통화' and not count):
        return name
    return f'{name} {count}'


def chip_height():
    return theme.px(30)


class FolderChip(QPushButton):
    """알약 모양 폴더 칩. 선택(checked)되면 강조색 배경 + on_accent 글자"""

    def __init__(self, name, count, parent=None):
        super().__init__(folder_chip_label(name, count), parent)
        self.folder = name
        self.count = count
        self.setObjectName('folderChip')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(chip_height())
        self.setContextMenuPolicy(Qt.CustomContextMenu)


class FolderBar(QScrollArea):
    """가로로 스크롤되는 폴더 칩 줄 + 맨 끝 '+'(새 폴더) 칩"""

    folder_selected = pyqtSignal(str)
    add_requested = pyqtSignal()
    menu_requested = pyqtSignal(str, QPoint)   # 폴더 이름, 전역 좌표 (우클릭)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('folderBar')
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._inner = QWidget()
        self._inner.setObjectName('folderBarInner')
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(16, 8, 16, 8)
        self._row.setSpacing(6)
        self.chips = []
        self.add_chip = QPushButton()
        self.add_chip.setObjectName('folderAdd')
        self.add_chip.setToolTip('폴더 만들기')
        self.add_chip.setCursor(Qt.PointingHandCursor)
        self.add_chip.setFocusPolicy(Qt.TabFocus)
        self.add_chip.setAccessibleName('폴더 만들기')
        self.add_chip.clicked.connect(self.add_requested)
        self._row.addWidget(self.add_chip)
        self._row.addStretch(1)
        self.setWidget(self._inner)
        self.apply_theme()

    def chip(self, name):
        return next((c for c in self.chips if c.folder == name), None)

    def labels(self):
        return [c.text() for c in self.chips]

    def set_folders(self, folders, current):
        """folders: [(이름, 개수)] - 칩을 다시 만들고 current를 선택 표시"""
        for chip in self.chips:
            self._row.removeWidget(chip)
            chip.deleteLater()
        self.chips = []
        for i, (name, count) in enumerate(folders):
            chip = FolderChip(name, count, self._inner)
            chip.setChecked(name == current)
            chip.clicked.connect(lambda _=False, n=name: self._on_clicked(n))
            chip.customContextMenuRequested.connect(
                lambda pos, c=chip: self.menu_requested.emit(c.folder, c.mapToGlobal(pos)))
            self._row.insertWidget(i, chip)
            self.chips.append(chip)
        selected = self.chip(current)
        if selected is not None and self.isVisible():
            self.ensureWidgetVisible(selected, 24, 0)

    def _on_clicked(self, name):
        # 이미 선택된 칩을 다시 눌러도 선택 해제되지 않게
        chip = self.chip(name)
        if chip is not None:
            chip.setChecked(True)
        self.folder_selected.emit(name)

    def wheelEvent(self, event):
        # 세로 휠도 가로 스크롤로
        delta = event.angleDelta()
        step = delta.x() or delta.y()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - step)
        event.accept()

    def apply_theme(self):
        h = chip_height()
        self.setFixedHeight(h + 16)
        self.add_chip.setFixedSize(h, h)
        self.add_chip.setIcon(make_icon('plus', theme.c('text_sub'), theme.px(14)))
        self.add_chip.setIconSize(QSize(theme.px(14), theme.px(14)))
        self.setStyleSheet(
            f"QScrollArea#folderBar, QWidget#folderBarInner {{ background: {theme.c('bg')}; border: none; }}"
            f" QPushButton#folderChip {{ background: {theme.c('bg_alt')}; color: {theme.c('text_sub')};"
            f" border: 1px solid {theme.c('border')}; border-radius: {h // 2}px; padding: 0 {theme.px(12)}px;"
            f" font-size: {theme.fs('meta')}px; }}"
            f" QPushButton#folderChip:hover {{ background: {theme.c('bg_hover')}; }}"
            f" QPushButton#folderChip:checked {{ background: {theme.c('yellow')}; color: {theme.c('on_accent')};"
            f" border: 1px solid {theme.c('yellow')}; font-weight: bold; }}"
            f" QPushButton#folderAdd {{ background: {theme.c('bg_alt')}; border: 1px dashed {theme.c('border')};"
            f" border-radius: {h // 2}px; padding: 0; }}"
            f" QPushButton#folderAdd:hover {{ background: {theme.c('bg_hover')}; }}")


# ================================================================ AI 비서 버튼
def sparkle_path(cx, cy, r):
    """네 갈래 반짝이(✦) 경로 - 변이 안쪽으로 오목하게 휜다"""
    k = r * 0.16
    path = QPainterPath()
    path.moveTo(cx, cy - r)
    path.quadTo(cx + k, cy - k, cx + r, cy)
    path.quadTo(cx + k, cy + k, cx, cy + r)
    path.quadTo(cx - k, cy + k, cx - r, cy)
    path.quadTo(cx - k, cy - k, cx, cy - r)
    path.closeSubpath()
    return path


def ai_icon_color():
    """어두운 강조색(브랜드 네이비 등)이면 강조색, 밝은 강조색(카카오 옐로)이면 보조 글자색"""
    accent = QColor(theme.c('yellow'))
    return theme.c('yellow') if accent.lightness() < 170 else theme.c('text_sub')


def make_ai_icon(size=22, color=None):
    """AI 비서 아이콘: 큰 반짝이 + 작은 반짝이 두 개 (24 그리드 기준)"""
    color = QColor(color or ai_icon_color())
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(sparkle_path(10, 13.5, 8.5))
    p.drawPath(sparkle_path(18.5, 5.5, 4))
    faded = QColor(color)
    faded.setAlphaF(0.7)
    p.setBrush(faded)
    p.drawPath(sparkle_path(19.5, 17.5, 2.4))
    p.end()
    return QIcon(pm)


class AiButton(IconButton):
    """채팅 탭 상단 AI 비서 버튼 (QPainter로 직접 그린 반짝이 아이콘)"""

    def __init__(self, tooltip='AI 비서', size=22, parent=None):
        super().__init__('ai', tooltip, size, parent)

    def refresh_icon(self, color=None):
        self.setIcon(make_ai_icon(self._icon_size, color))
        self.setIconSize(QSize(self._icon_size, self._icon_size))


# ================================================================ 오픈채팅
class OpenBadge(QWidget):
    """채팅 목록의 '오픈' 배지 (오픈채팅방 표시)"""

    TEXT = '오픈'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.refresh_theme()

    def text(self):
        return self.TEXT

    def _font(self):
        return theme.font('micro', True)

    def refresh_theme(self):
        fm = QFontMetrics(self._font())
        self.setFixedSize(fm.horizontalAdvance(self.TEXT) + theme.px(10) + 2, fm.height() + theme.px(3))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.qc('yellow'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 4, 4)
        p.setFont(self._font())
        p.setPen(theme.qc('brand') if theme.c('yellow').upper() == '#FEE500' else theme.qc('yellow'))
        p.drawText(r, Qt.AlignCenter, self.TEXT)


def panel_dialog_qss():
    """오픈채팅·톡서랍·기능 둘러보기 다이얼로그 공용 스타일 (라이트/다크 테마 반영)"""
    c = theme.c
    return f"""
        QDialog {{ background: {c('bg')}; }}
        QLabel {{ color: {c('text')}; background: transparent; }}
        QLabel#panelTitle {{ font-size: {theme.fs('heading')}px; font-weight: bold; }}
        QLabel#panelHint, QLabel#cardMeta {{ color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
        QLabel#cardTags {{ color: {c('link')}; font-size: {theme.fs('meta')}px; }}
        QLabel#cardTitle {{ font-size: {theme.fs('emphasis')}px; font-weight: bold; }}
        QLabel#cardDesc {{ color: {c('text')}; font-size: {theme.fs('meta')}px; }}
        QLabel#emptyLabel {{ color: {c('text_hint')}; font-size: {theme.fs('body')}px; padding: 28px 12px; }}
        QLabel#infoBanner {{ background: {c('bg_alt')}; border: 1px solid {c('border')}; border-radius: 8px;
                            color: {c('text')}; padding: 8px 12px; font-size: {theme.fs('meta')}px; }}
        QFrame#card {{ background: {c('bg_alt')}; border: 1px solid {c('border')}; border-radius: 10px; }}
        QWidget#scrollInner {{ background: {c('bg')}; }}
        QLineEdit, QPlainTextEdit {{ background: {c('input_bg')}; color: {c('text')};
            border: 1px solid {c('border')}; border-radius: 8px; padding: 7px 10px; }}
        QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c('yellow')}; }}
        QPushButton {{ background: {c('bg_alt')}; color: {c('text')}; border: 1px solid {c('border')};
            border-radius: 7px; padding: 6px 14px; }}
        QPushButton:hover {{ background: {c('bg_hover')}; }}
        QPushButton:disabled {{ color: {c('text_hint')}; }}
        QPushButton#primary {{ background: {c('yellow')}; color: {c('on_accent')}; border: none; font-weight: bold; }}
        QPushButton#primary:hover {{ background: {c('yellow_dark')}; }}
        QPushButton#primary:disabled {{ background: {c('bg_selected')}; color: {c('text_hint')}; }}
        {underline_tabs_qss()}
        QListWidget {{ background: {c('bg')}; color: {c('text')}; border: none; outline: 0; }}
        QListWidget::item {{ padding: 4px; border-radius: 6px; }}
        QListWidget::item:selected {{ background: {c('bg_selected')}; color: {c('text')}; }}
        QScrollArea {{ background: {c('bg')}; border: none; }}
        {check_box_qss()}
        QTextBrowser {{ background: {c('bg')}; color: {c('text')}; border: none; }}
        {theme.scrollbar_qss()}
    """


def parse_tags(text):
    """'#여행, 맛집 ,여행' → ['여행', '맛집'] (중복 제거, 최대 10개, 각 20자)"""
    tags = []
    for raw in (text or '').replace('#', ',').split(','):
        tag = raw.strip()[:20]
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:10]


class OpenRoomCard(QFrame):
    """오픈채팅 검색 결과 카드: 이름 · 인원 / 설명 / #태그 / [참여]"""

    def __init__(self, info, joined, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.info = info
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 12, 12)
        row.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.name_label = QLabel(info.get('name') or '(이름 없음)')
        self.name_label.setObjectName('cardTitle')
        top.addWidget(self.name_label, 1)
        self.count_label = QLabel(f"{int(info.get('member_count') or 0)}명")
        self.count_label.setObjectName('cardMeta')
        top.addWidget(self.count_label)
        col.addLayout(top)
        self.desc_label = QLabel(info.get('description') or '소개가 없습니다.')
        self.desc_label.setObjectName('cardDesc')
        self.desc_label.setWordWrap(True)
        self.desc_label.setTextFormat(Qt.PlainText)
        col.addWidget(self.desc_label)
        tags = info.get('tags') or []
        self.tags_label = QLabel('  '.join(f'#{t}' for t in tags))
        self.tags_label.setObjectName('cardTags')
        self.tags_label.setVisible(bool(tags))
        col.addWidget(self.tags_label)
        row.addLayout(col, 1)
        self.join_button = QPushButton('열기' if joined else '참여')
        if not joined:
            self.join_button.setObjectName('primary')
        self.join_button.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.join_button, 0, Qt.AlignVCenter)


class OpenChatDialog(QDialog):
    """오픈채팅: '찾기'(검색 → 참여, 닉네임) / '만들기'(이름·소개·태그)"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.results = []
        self.cards = []
        self.searched = False
        self.setWindowTitle('오픈채팅')
        self.setMinimumSize(340, 460)
        self.resize(400, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        title = QLabel('오픈채팅')
        title.setObjectName('panelTitle')
        layout.addWidget(title)
        hint = QLabel('관심사가 같은 사람들과 닉네임으로 대화해 보세요.')
        hint.setObjectName('panelHint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # ---- 찾기
        find = QWidget()
        fcol = QVBoxLayout(find)
        fcol.setContentsMargins(0, 12, 0, 0)
        fcol.setSpacing(10)
        srow = QHBoxLayout()
        srow.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('방 이름, 소개, 태그로 검색')
        self.search_edit.setClearButtonEnabled(True)
        self.search_button = QPushButton('검색')
        srow.addWidget(self.search_edit, 1)
        srow.addWidget(self.search_button)
        fcol.addLayout(srow)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName('scrollInner')
        self.result_col = QVBoxLayout(inner)
        self.result_col.setContentsMargins(0, 0, 4, 0)
        self.result_col.setSpacing(8)
        self.empty_label = QLabel('검색하는 중…')
        self.empty_label.setObjectName('emptyLabel')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.result_col.addWidget(self.empty_label)
        self.result_col.addStretch(1)
        self.scroll.setWidget(inner)
        fcol.addWidget(self.scroll, 1)
        self.tabs.addTab(find, '찾기')

        # ---- 만들기
        make = QWidget()
        mcol = QVBoxLayout(make)
        mcol.setContentsMargins(0, 12, 0, 0)
        mcol.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText('오픈채팅방 이름 (필수)')
        self.name_edit.setMaxLength(30)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText('어떤 이야기를 나누는 방인지 소개해 주세요')
        self.desc_edit.setFixedHeight(theme.px(96))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText('태그 (쉼표로 구분, 예: 개발, 파이썬)')
        for label, widget in (('이름', self.name_edit), ('소개', self.desc_edit), ('태그', self.tags_edit)):
            lab = QLabel(label)
            lab.setObjectName('panelHint')
            mcol.addWidget(lab)
            mcol.addWidget(widget)
        self.create_error = QLabel('')
        self.create_error.setStyleSheet(f"color: {theme.c('badge')}; font-size: {theme.fs('meta')}px;")
        self.create_error.hide()
        mcol.addWidget(self.create_error)
        note = QLabel('오픈채팅방에서는 실명 대신 방마다 정한 닉네임이 표시됩니다.')
        note.setObjectName('panelHint')
        note.setWordWrap(True)
        mcol.addWidget(note)
        crow = QHBoxLayout()
        crow.addStretch(1)
        self.create_button = QPushButton('오픈채팅방 만들기')
        self.create_button.setObjectName('primary')
        self.create_button.setEnabled(False)
        crow.addWidget(self.create_button)
        mcol.addSpacing(4)
        mcol.addLayout(crow)
        mcol.addStretch(1)
        self.tabs.addTab(make, '만들기')

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_button = QPushButton('닫기')
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)

        self.search_button.clicked.connect(self.search)
        self.search_edit.returnPressed.connect(self.search)
        self.create_button.clicked.connect(self.create)
        self.name_edit.textChanged.connect(self._update_create_button)
        self.close_button.clicked.connect(self.reject)
        state.open_rooms_found.connect(self.set_results)
        self.setStyleSheet(panel_dialog_qss())
        self.search()   # 처음 열면 전체(인기) 목록

    def _update_create_button(self, _text=''):
        self.create_button.setEnabled(bool(self.name_edit.text().strip()))
        self.create_error.hide()

    def search(self, query=None):
        query = self.search_edit.text().strip() if query is None or isinstance(query, bool) else query
        self.searched = True
        self.empty_label.setText('검색하는 중…')
        self.state.search_open_rooms(query)

    def set_results(self, rooms):
        self.results = list(rooms or [])
        for card in self.cards:
            self.result_col.removeWidget(card)
            card.deleteLater()
        self.cards = []
        for i, info in enumerate(self.results):
            joined = info.get('room_id') in self.state.rooms
            card = OpenRoomCard(info, joined)
            card.join_button.clicked.connect(lambda _=False, rid=info.get('room_id'): self.join(rid))
            self.result_col.insertWidget(i, card)
            self.cards.append(card)
        self.empty_label.setText('검색 결과가 없습니다.\n\'만들기\' 탭에서 새 오픈채팅방을 열어 보세요.')
        self.empty_label.setVisible(not self.results)

    def ask_nickname(self, room_name=''):
        """참여할 닉네임 입력 (기본: 내 이름). 취소하면 None"""
        default = self.state.display_name(self.state.me) or self.state.me
        text, ok = QInputDialog.getText(self, '오픈채팅 참여', f"'{room_name}'에서 쓸 닉네임", QLineEdit.Normal, default)
        return text.strip() if ok and text.strip() else None

    def join(self, room_id, nickname=None):
        """참여 (이미 참여한 방이면 열기). 요청을 보냈으면 True"""
        if not room_id:
            return False
        if room_id in self.state.rooms:
            self.state.room_opened.emit(room_id)
            self.accept()
            return True
        info = next((r for r in self.results if r.get('room_id') == room_id), {})
        if nickname is None:
            nickname = self.ask_nickname(info.get('name', ''))
        nickname = (nickname or '').strip()[:20]
        if not nickname:
            return False
        self.state.join_open_room(room_id, nickname)
        self.accept()
        return True

    def create(self):
        name = self.name_edit.text().strip()
        if not name:
            self.create_error.setText('방 이름을 입력해 주세요.')
            self.create_error.show()
            return False
        self.state.create_open_room(name, self.desc_edit.toPlainText().strip(), parse_tags(self.tags_edit.text()))
        self.accept()
        return True

    def done(self, result):
        try:
            self.state.open_rooms_found.disconnect(self.set_results)
        except TypeError:
            pass
        super().done(result)


def room_avatar_pixmap(state, room, size):
    """채팅방 대표 이미지 (그룹이면 멤버 모자이크, 아니면 상대 프로필)"""
    members = room.get('members', [])
    if room.get('type') in ('group', 'open'):
        from client.kakao.widgets import group_avatar_pixmap
        others = [m for m in members if m != state.me] or members
        return group_avatar_pixmap(others, {m: state.display_name(m) for m in others}, size)
    others = [m for m in members if m != state.me]
    return user_pixmap(state, others[0] if others else state.me, size)


def confirm_delete_folder(parent, name):
    answer = QMessageBox.question(
        parent, '폴더 삭제', f"'{name}' 폴더를 삭제하시겠습니까?\n폴더 안의 채팅방은 삭제되지 않습니다.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    return answer == QMessageBox.Yes


# ================================================================ 채팅 탭
class ChatsTab(QWidget):
    open_room_requested = pyqtSignal(str)

    def __init__(self, state, settings, parent=None):
        super().__init__(parent)
        self.state = state
        self.settings = settings
        self.current_folder = FOLDER_ALL   # 이번 세션 동안 기억하는 선택 폴더
        self._lost_folder = None           # 삭제/이름 변경으로 사라진 직전 선택 폴더
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = TabHeader('채팅')
        self.ai_button = self.header.add_widget_button(AiButton('AI 비서'))
        self.search_button = self.header.add_button('search', '채팅방 이름, 참여자 검색')
        self.new_chat_button = self.header.add_button('add_chat', '새로운 채팅')
        # 가끔 쓰는 기능(오픈채팅·톡서랍)은 ⋯ 메뉴로 - 헤더 선택지를 4개 이하로
        self.more_button = self.header.add_button('more', '더보기 (오픈채팅 · 톡서랍)')
        self.more_button.clicked.connect(self._show_more_menu)
        layout.addWidget(self.header)

        self.folder_bar = FolderBar()
        layout.addWidget(self.folder_bar)

        self.search_edit = make_search_edit('채팅방 이름, 참여자 검색')
        search_wrap = QHBoxLayout()
        search_wrap.setContentsMargins(16, theme.sp('s'), 16, theme.sp('s'))   # 폴더 줄과 붙지 않게
        search_wrap.addWidget(self.search_edit)
        layout.addLayout(search_wrap)

        self.list = RowList()
        layout.addWidget(self.list, 1)

        self.empty_label = QLabel('채팅방이 없습니다')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        layout.addWidget(self.empty_label, 1)

        self.ai_button.clicked.connect(self.open_ai_assistant)
        self.search_button.clicked.connect(self.toggle_search)
        self.new_chat_button.clicked.connect(self.show_create_dialog)

        self.folder_bar.folder_selected.connect(self.select_folder)
        self.folder_bar.add_requested.connect(lambda: self.show_folder_dialog())
        self.folder_bar.menu_requested.connect(self._show_folder_menu)
        self.search_edit.textChanged.connect(lambda _: self.refresh())
        self.list.row_activated.connect(self._activate)
        self.list.customContextMenuRequested.connect(self._show_context_menu)

        self._fresh_row = None   # (room_id, 방 스냅샷): room_changed 로 막 새로 만든 행
        state.rooms_changed.connect(self._on_rooms_changed)
        state.room_changed.connect(self._on_room_changed)
        state.users_changed.connect(self.refresh)
        state.call_history_changed.connect(self.refresh)
        settings.changed.connect(self._on_setting)
        connect_avatar_changed(state, self.refresh)
        self.apply_theme()

    # ---- 목록 ----
    def _on_setting(self, key):
        if key in ('pinned_rooms', 'muted_rooms', 'chat_folders', 'favorites'):
            self.refresh()

    def _update_subtitle(self):
        if self.current_folder == '통화':
            missed = self.state.missed_call_count()
            self.header.set_subtitle(f'통화 기록 {len(self.state.call_history)}건'
                                     + (f' · 부재중 {missed}건' if missed else ''))
            return
        if self.current_folder != FOLDER_ALL:
            count = len(self.state.rooms_in_folder(self.current_folder))
            self.header.set_subtitle(f'{self.current_folder} 폴더 · 채팅방 {count}개')
            return
        rooms = len(self.state.rooms)
        unread = self.state.total_unread()
        self.header.set_subtitle(f'채팅방 {rooms}개' + (f' · 안 읽은 메시지 {unread}개' if unread else ' · 모두 읽음'))

    def toggle_search(self):
        visible = self.search_edit.isHidden()
        self.search_edit.setVisible(visible)
        if visible:
            self.search_edit.setFocus()
        else:
            self.search_edit.clear()

    def _matches(self, room, query):
        if not query:
            return True
        query = query.lower()
        if query in self.state.room_title(room['room_id']).lower():
            return True
        return any(query in self.state.display_name(m).lower() for m in room.get('members', []))

    def more_menu(self):
        menu = QMenu(self)
        menu.addAction(make_icon('chat', theme.c('text_sub'), 16), '오픈채팅 찾기 · 만들기',
                       lambda: self.show_open_chat_dialog())
        menu.addAction(make_icon('drawer', theme.c('text_sub'), 16), '톡서랍 (모든 채팅방의 사진·파일·링크)',
                       lambda: self.show_talk_drawer())
        menu.addSeparator()
        menu.addAction('@ 나를 멘션한 메시지', lambda: self.show_mentions())
        menu.addAction('책갈피 (저장한 메시지)', lambda: self.show_saved())
        return menu

    def show_mentions(self):
        """멘션 모아보기 (§10.5)"""
        from client.kakao.inbox import MentionsDialog
        dlg = MentionsDialog(self.state, self.window())
        dlg.show()
        return dlg

    def show_saved(self):
        """책갈피 (§10.7)"""
        from client.kakao.inbox import SavedDialog
        dlg = SavedDialog(self.state, self.window())
        dlg.show()
        return dlg

    def _show_more_menu(self):
        self.more_menu().exec_(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def room_ids(self):
        """현재 목록에 보이는 방 순서"""
        return [item.data(Qt.UserRole) for item in self.list.items()]

    # ---- 폴더 ----
    def folder_names(self):
        return [name for name, _count in self.state.chat_folders()]

    def is_custom_folder(self, name):
        return name in self.folder_names() and name not in self.state.BUILTIN_FOLDERS

    def select_folder(self, name):
        """폴더 칩 선택 → 목록을 그 폴더의 방으로 거른다 (없는 폴더면 전체)"""
        self.current_folder = name if name in self.folder_names() else FOLDER_ALL
        self.refresh()

    def show_folder_dialog(self, name=None, preselected=()):
        """새 폴더(name=None) 또는 사용자 폴더 편집. 새로 만들면 그 폴더를 선택한다"""
        dlg = ChatFolderDialog(self.state, name, self.window(), preselected=preselected)
        if dlg.exec_() == QDialog.Accepted:
            self.apply_folder_dialog_result(dlg)
        return dlg

    def apply_folder_dialog_result(self, dlg):
        was_current = dlg.original_name is not None and dlg.original_name in (self.current_folder, self._lost_folder)
        self._lost_folder = None
        if dlg.deleted:
            if was_current:
                self.select_folder(FOLDER_ALL)
        elif dlg.saved_name and (dlg.original_name is None or was_current):
            self.select_folder(dlg.saved_name)

    def folder_menu_for(self, name):
        """사용자 폴더 칩 우클릭 메뉴 (기본 폴더는 None)"""
        if not self.is_custom_folder(name):
            return None
        menu = QMenu(self)
        menu.addAction('폴더 편집', lambda: self.show_folder_dialog(name))
        menu.addAction('폴더 삭제', lambda: self.delete_folder(name))
        return menu

    def _show_folder_menu(self, name, global_pos):
        menu = self.folder_menu_for(name)
        if menu is not None:
            menu.exec_(global_pos)

    def delete_folder(self, name, confirm=True):
        if not self.is_custom_folder(name):
            return False
        if confirm and not confirm_delete_folder(self, name):
            return False
        self.state.delete_chat_folder(name)
        return True

    def _empty_text(self, query):
        if query:
            return '검색 결과가 없습니다'
        if self.current_folder == '안읽음':
            return '안 읽은 채팅방이 없습니다'
        if self.current_folder == '통화':
            return '통화 기록이 없습니다\n채팅방에서 보이스톡·페이스톡을 걸면 여기에 모입니다'
        if self.current_folder == '즐겨찾기':
            return '즐겨찾기한 채팅방이 없습니다\n채팅방을 상단 고정하거나 친구를 즐겨찾기에 추가해 보세요'
        if self.current_folder != FOLDER_ALL:
            return f"'{self.current_folder}' 폴더에 채팅방이 없습니다\n채팅방을 우클릭해 폴더에 추가해 보세요"
        return '채팅방이 없습니다'

    def _on_rooms_changed(self):
        self.refresh(reuse_fresh=True)

    def refresh(self, reuse_fresh=False):
        # room_changed 직후 이어지는 rooms_changed 에서는 방금 만든 행을 다시 만들지 않는다
        fresh = self._fresh_row if reuse_fresh else None
        self._fresh_row = None
        folders = self.state.chat_folders()
        if self.current_folder not in [name for name, _count in folders]:
            # 이름이 바뀌었을 수도 있으니 기억해 두었다가 편집 결과에서 따라간다
            self._lost_folder = self.current_folder
            self.current_folder = FOLDER_ALL
        self.folder_bar.set_folders(folders, self.current_folder)
        self._update_subtitle()
        query = self.search_edit.text().strip()
        if self.current_folder != '통화':
            rooms = [room for room in self.state.rooms_in_folder(self.current_folder) if self._matches(room, query)]
            if self._update_rows_in_place(rooms, fresh):
                return
        current = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()
        if self.current_folder == '통화':
            self._fill_call_log(query)
            rooms = []
        for room in rooms:
            item = self.list.add_row(RoomRow(self.state, room['room_id']), room['room_id'])
            if room['room_id'] == current:
                self.list.setCurrentItem(item)
        self.list._fit_rows()
        self.list.verticalScrollBar().setValue(scroll)
        has_rows = self.list.count() > 0
        self.list.setVisible(has_rows)
        self.empty_label.setText(self._empty_text(query))
        self.empty_label.setVisible(not has_rows)

    def _update_rows_in_place(self, rooms, fresh=None):
        """보이는 방 목록·순서가 그대로면 항목은 두고 행 위젯만 바꾼다 (선택·스크롤 유지, 목록 재생성 없음).
        순서·구성이 바뀌었으면 False → 전체를 다시 만든다"""
        items = self.list.items()
        if not rooms or len(items) != len(rooms):
            return False
        if [it.data(Qt.UserRole) for it in items] != [room['room_id'] for room in rooms]:
            return False
        if not all(isinstance(self.list.itemWidget(it), RoomRow) for it in items):
            return False
        for item, room in zip(items, rooms):
            rid = room['room_id']
            if fresh is not None and fresh[0] == rid and fresh[1] == room:
                continue   # room_changed 에서 막 만든 행이고 그 뒤로 방 정보가 그대로
            row = RoomRow(self.state, rid)
            item.setSizeHint(QSize(1, row.sizeHint().height()))
            self.list.setItemWidget(item, row)
        self.list._fit_rows()
        self.list.setVisible(True)
        self.empty_label.setVisible(False)
        return True

    def _fill_call_log(self, query):
        for call in self.state.call_history:
            room = self.state.room(call['room_id'])
            if room is None or not self._matches(room, query):
                continue
            self.list.add_row(CallLogRow(self.state, call), call['room_id'])

    def _on_room_changed(self, room_id):
        """방 하나만 바뀌면(이름·미리보기·전송 실패 표시) 그 행만 새로 만든다. 순서·폴더가 바뀌는 변화는
        rooms_changed 로 전체를 다시 그린다"""
        if self.current_folder == '통화' or self.search_edit.text().strip():
            self.refresh()
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == room_id and isinstance(self.list.itemWidget(item), RoomRow):
                if self.state.room(room_id) is None:
                    break
                row = RoomRow(self.state, room_id)
                self.list.setItemWidget(item, row)
                self.list._fit_rows()
                self._update_subtitle()
                self._fresh_row = (room_id, copy.deepcopy(self.state.room(room_id)))
                return
        self.refresh()

    def _activate(self, item):
        rid = item.data(Qt.UserRole)
        if rid:
            self.open_room_requested.emit(rid)
            self.state.room_opened.emit(rid)

    def open_ai_assistant(self):
        """AI 비서와의 1:1 채팅 열기 (없으면 서버에 만들어 달라고 요청)"""
        self.state.open_direct(BOT_NAME)

    # ---- 컨텍스트 메뉴 ----
    def folder_submenu_for(self, room_id, parent_menu):
        sub = QMenu('폴더에 추가', parent_menu)
        for name in self.folder_names():
            if name in self.state.BUILTIN_FOLDERS:
                continue
            ids = {r['room_id'] for r in self.state.rooms_in_folder(name)}
            action = sub.addAction(name)
            action.setCheckable(True)
            action.setChecked(room_id in ids)
            action.triggered.connect(lambda _=False, n=name: self.state.toggle_room_in_folder(n, room_id))
        if sub.actions():
            sub.addSeparator()
        sub.addAction('새 폴더…', lambda: self.show_folder_dialog(preselected=[room_id]))
        return sub

    def context_menu_for(self, room_id):
        menu = QMenu(self)
        menu.addAction('채팅방 열기', lambda: self.state.room_opened.emit(room_id))
        if self.state.room_unread(room_id) > 0:
            menu.addAction('읽지 않고 미리보기', lambda: self.show_peek_dialog(room_id))
        menu.addSeparator()
        pinned = self.state.is_pinned(room_id)
        menu.addAction('상단 고정 해제' if pinned else '채팅방 상단 고정',
                       lambda: self.settings.toggle_in('pinned_rooms', room_id))
        muted = self.state.is_muted(room_id)
        menu.addAction('채팅방 알림 켜기' if muted else '채팅방 알림 끄기',
                       lambda: self.settings.toggle_in('muted_rooms', room_id))
        menu.addMenu(self.folder_submenu_for(room_id, menu))
        room = self.state.room(room_id) or {}
        if room.get('type') in ('group', 'direct'):
            menu.addAction('대화상대 초대…', lambda: self.show_invite_dialog(room_id))
        menu.addAction('대화 내용 저장…', lambda: self.export_room(room_id))
        menu.addSeparator()
        menu.addAction('나가기', lambda: self.confirm_leave(room_id))
        return menu

    def export_room(self, room_id, exec_=True):
        """채팅 목록에서 바로 대화 내용 저장: 그 방을 열고 저장 옵션 창을 띄운다 (명세 §9.5)"""
        opener = getattr(self.window(), 'open_room', None)
        win = opener(room_id) if opener else None
        if win is None:
            return None
        return win.show_export_dialog(exec_=exec_)

    def _show_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        self.context_menu_for(item.data(Qt.UserRole)).exec_(self.list.viewport().mapToGlobal(pos))

    def show_peek_dialog(self, room_id):
        dlg = PeekDialog(self.state, room_id, self.window())
        dlg.exec_()
        return dlg

    def confirm_leave(self, room_id):
        title = self.state.room_title(room_id)
        answer = QMessageBox.question(
            self, '채팅방 나가기',
            f"'{title}' 채팅방을 나가시겠습니까?\n나가기를 하면 대화 내용이 모두 삭제되고\n채팅 목록에서도 삭제됩니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.state.leave_room(room_id)
            return True
        return False

    def show_create_dialog(self):
        CreateGroupDialog(self.state, self.window()).exec_()

    def show_open_chat_dialog(self, modal=True):
        dlg = OpenChatDialog(self.state, self.window())
        if modal:
            dlg.exec_()
        else:
            dlg.show()
        return dlg

    def show_talk_drawer(self, modal=False):
        """톡서랍은 채팅방을 오가며 보도록 모달리스로 띄운다"""
        from client.kakao.drawer import TalkDrawerDialog
        dlg = TalkDrawerDialog(self.state, self.window())
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        if modal:
            dlg.exec_()
        else:
            dlg.show()
        return dlg

    def show_invite_dialog(self, room_id):
        try:
            from client.kakao.chat_room import InviteDialog
        except ImportError:
            return None
        dlg = InviteDialog(self.state, room_id, self.window())
        dlg.exec_()
        return dlg

    def apply_theme(self):
        self.header.apply_theme()
        self.folder_bar.apply_theme()
        self.search_edit.setStyleSheet(search_box_qss())
        self.empty_label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('body')}px;"
                                       f" padding: 0 24px;")
        self.setStyleSheet(f"ChatsTab {{ background: {theme.c('bg')}; }}")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.refresh()


# ================================================================ 대화상대 선택
class CreateGroupDialog(QDialog):
    """대화상대 선택 → 1명이면 1:1 채팅, 2명 이상이면 그룹 채팅 생성"""

    def __init__(self, state, parent=None, preselected=()):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle('대화상대 선택')
        self.setMinimumSize(320, 420)
        self.resize(360, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.title = QLabel('대화상대 선택')
        self.title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        self.count_label = QLabel('')
        self.count_label.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('body')}px;")
        top.addWidget(self.title)
        top.addWidget(self.count_label)
        top.addStretch(1)
        layout.addLayout(top)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText('채팅방 이름 (선택)')
        layout.addWidget(self.name_edit)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('이름으로 검색')
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)

        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setIconSize(QSize(theme.px(40), theme.px(40)))
        self.list.setSpacing(2)
        self.list.setStyleSheet(
            f"QListWidget {{ background: {theme.c('bg')}; }}"
            f" QListWidget::item {{ padding: 6px 4px; border-radius: 8px; }}"
            f" QListWidget::item:selected {{ background: {theme.c('bg_hover')}; color: {theme.c('text')}; }}")
        pre = set(preselected)
        for user in state.friends():
            name = user['username']
            item = QListWidgetItem(QIcon_from_avatar(name, state.display_name(name)), '  ' + state.display_name(name))
            item.setData(Qt.UserRole, name)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if name in pre else Qt.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton('취소')
        self.ok_button = QPushButton('확인')
        self.ok_button.setProperty('primary', True)
        self.ok_button.setDefault(True)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        self.list.itemChanged.connect(lambda _item: self._update_count())
        self.list.itemClicked.connect(self._toggle_item)
        self.search_edit.textChanged.connect(self._filter)
        self._update_count()

    def item_for(self, username):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == username:
                return self.list.item(i)
        return None

    def set_checked(self, username, checked=True):
        item = self.item_for(username)
        if item is not None:
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _toggle_item(self, item):
        # 체크박스 밖(이름 부분)을 눌러도 선택되도록
        pos = self.list.viewport().mapFromGlobal(self.list.cursor().pos())
        rect = self.list.visualItemRect(item)
        if pos.x() > rect.x() + 24:
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def selected_members(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _update_count(self):
        n = len(self.selected_members())
        self.count_label.setText(str(n) if n else '')
        self.ok_button.setEnabled(n > 0)
        self.name_edit.setEnabled(n >= 2)

    def _filter(self, text):
        text = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def accept(self):
        members = self.selected_members()
        if not members:
            return
        if len(members) == 1:
            self.state.open_direct(members[0])
        else:
            self.state.create_group(members, self.name_edit.text().strip())
        super().accept()


# ================================================================ 채팅방에 초대
class InviteToRoomDialog(QDialog):
    """한 사람을 내 채팅방(그룹/1:1)에 초대 - 1:1 방이면 새 그룹방이 만들어진다"""

    def __init__(self, state, username, parent=None):
        super().__init__(parent)
        self.state = state
        self.username = username
        self.setWindowTitle('채팅방에 초대')
        self.setMinimumSize(320, 360)
        self.resize(360, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        title = QLabel(f"{state.display_name(username)}님을 초대할 채팅방")
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size: {theme.fs('title')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel('1:1 채팅방을 고르면 기존 대화상대와 함께 새 그룹 채팅방이 만들어집니다.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setIconSize(QSize(theme.px(36), theme.px(36)))
        self.list.setStyleSheet(
            f"QListWidget::item {{ padding: 6px 4px; border-radius: 8px; }}"
            f" QListWidget::item:selected {{ background: {theme.c('bg_selected')}; color: {theme.c('text')}; }}")
        for room in self.candidate_rooms():
            rid = room['room_id']
            label = state.room_title(rid)
            if room['type'] == 'group':
                label += f"  ({len(room.get('members', []))})"
                from client.kakao.widgets import group_avatar_pixmap
                members = [m for m in room.get('members', []) if m != state.me]
                pm = group_avatar_pixmap(members, {m: state.display_name(m) for m in members}, theme.px(36))
            else:
                others = [m for m in room.get('members', []) if m != state.me]
                pm = user_pixmap(state, others[0] if others else state.me, theme.px(36))
            from PyQt5.QtGui import QIcon
            item = QListWidgetItem(QIcon(pm), '  ' + label)
            item.setData(Qt.UserRole, rid)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        self.empty_label = QLabel('초대할 수 있는 채팅방이 없습니다')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {theme.c('text_hint')};")
        self.empty_label.setVisible(self.list.count() == 0)
        layout.addWidget(self.empty_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        self.ok_button = QPushButton('초대')
        self.ok_button.setProperty('primary', True)
        self.ok_button.setEnabled(False)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        self.list.currentItemChanged.connect(lambda cur, _prev: self.ok_button.setEnabled(cur is not None))
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())

    def candidate_rooms(self):
        return [r for r in self.state.sorted_rooms()
                if r['type'] in ('group', 'direct') and self.username not in r.get('members', [])]

    def select_room(self, room_id):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == room_id:
                self.list.setCurrentRow(i)
                return True
        return False

    def accept(self):
        item = self.list.currentItem()
        if item is None:
            return
        self.state.invite(item.data(Qt.UserRole), [self.username])
        super().accept()


# ================================================================ 채팅방 폴더 편집
class ChatFolderDialog(QDialog):
    """사용자 폴더 만들기/편집: 이름 + 채팅방 체크 목록. exec_() 없이도
    set_name() / set_checked() / accept() / delete_folder()로 조작할 수 있다."""

    def __init__(self, state, name=None, parent=None, preselected=()):
        super().__init__(parent)
        self.state = state
        self.original_name = name      # None이면 새 폴더
        self.saved_name = None
        self.deleted = False
        editing = name is not None
        self.setWindowTitle('폴더 편집' if editing else '새 폴더')
        self.setMinimumSize(300, 400)
        self.resize(360, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        self.title = QLabel('폴더 편집' if editing else '새 폴더 만들기')
        self.title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(self.title)

        self.name_edit = QLineEdit(name or '')
        self.name_edit.setPlaceholderText('폴더 이름 (예: 회사, 가족)')
        self.name_edit.setMaxLength(20)
        layout.addWidget(self.name_edit)

        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.c('badge')}; font-size: {theme.fs('meta')}px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        info = QHBoxLayout()
        hint = QLabel('폴더에 넣을 채팅방')
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        self.count_label = QLabel('')
        self.count_label.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold;")
        info.addWidget(hint)
        info.addStretch(1)
        info.addWidget(self.count_label)
        layout.addLayout(info)

        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setIconSize(QSize(theme.px(36), theme.px(36)))
        self.list.setSpacing(1)
        self.list.setStyleSheet(
            f"QListWidget {{ background: {theme.c('bg')}; }}"
            f" QListWidget::item {{ padding: 5px 4px; border-radius: 8px; }}"
            f" QListWidget::item:selected {{ background: {theme.c('bg_hover')}; color: {theme.c('text')}; }}"
            f" QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 8px;"
            f" border: 2px solid {theme.c('text_hint')}; background: {theme.c('bg')}; }}"
            f" QListWidget::indicator:checked {{ border: 5px solid {theme.c('yellow')};"
            f" background: {theme.c('on_accent')}; }}")
        checked = {r['room_id'] for r in state.rooms_in_folder(name)} if editing else set(preselected)
        for room in state.sorted_rooms():
            rid = room['room_id']
            label = state.room_title(rid)
            if room.get('type') == 'group':
                label += f"  {len(room.get('members', []))}"
            item = QListWidgetItem(QIcon(room_avatar_pixmap(state, room, theme.px(36))), '  ' + label)
            item.setData(Qt.UserRole, rid)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if rid in checked else Qt.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.delete_button = None
        if editing and name not in state.BUILTIN_FOLDERS:
            self.delete_button = QPushButton('삭제')
            self.delete_button.setStyleSheet(f"color: {theme.c('badge')};")
            self.delete_button.clicked.connect(lambda: self.delete_folder())
            buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton('취소')
        self.ok_button = QPushButton('저장')
        self.ok_button.setProperty('primary', True)
        self.ok_button.setDefault(True)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        self.name_edit.textChanged.connect(lambda _t: self.error_label.hide())
        self.list.itemChanged.connect(lambda _item: self._update_count())
        self.list.itemClicked.connect(self._toggle_item)
        self._update_count()

    def set_name(self, text):
        self.name_edit.setText(text)

    def set_checked(self, room_ids):
        """정확히 room_ids만 체크"""
        ids = set(room_ids)
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in ids else Qt.Unchecked)

    def checked_room_ids(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def error_text(self):
        return self.error_label.text() if not self.error_label.isHidden() else ''

    def _toggle_item(self, item):
        # 체크박스 밖(이름 부분)을 눌러도 체크되도록
        pos = self.list.viewport().mapFromGlobal(self.list.cursor().pos())
        rect = self.list.visualItemRect(item)
        if pos.x() > rect.x() + 24:
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def _update_count(self):
        n = len(self.checked_room_ids())
        self.count_label.setText(f'{n}개 선택' if n else '')

    def accept(self):
        name = self.name_edit.text().strip()
        error = self.state.save_chat_folder(name, self.checked_room_ids(), old_name=self.original_name)
        if error:
            self.error_label.setText(error)
            self.error_label.show()
            self.name_edit.setFocus()
            return
        self.saved_name = name
        super().accept()

    def delete_folder(self, confirm=True):
        if self.original_name is None or self.original_name in self.state.BUILTIN_FOLDERS:
            return False
        if confirm and not confirm_delete_folder(self, self.original_name):
            return False
        self.state.delete_chat_folder(self.original_name)
        self.deleted = True
        super().accept()
        return True


# ================================================================ 읽지 않고 미리보기
class PeekRow(QFrame):
    """미리보기 한 줄: 보낸 사람 · 시간 / 내용(최대 3줄)"""

    def __init__(self, state, message, parent=None):
        super().__init__(parent)
        self.setObjectName('peekRow')
        self.message = message
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 9, 12, 10)
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.sender = ElidedLabel(state.display_name(message.get('sender', '')), 12, 'text', bold=True)
        self.time = ElidedLabel(format_room_time(message.get('timestamp')), 11, 'text_hint', align=Qt.AlignRight)
        self.time.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.time.setFixedWidth(self.time.sizeHint().width())
        top.addWidget(self.sender, 1)
        top.addWidget(self.time)
        col.addLayout(top)
        text = state.message_preview(message).strip()
        if len(text) > 140:
            text = text[:139] + '…'
        self.text = QLabel(text)
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.PlainText)
        self.text.setStyleSheet(f"color: {theme.c('text')}; font-size: {theme.fs('body')}px; background: transparent;")
        self.text.setMaximumHeight(QFontMetrics(theme.font('body')).lineSpacing() * 3 + 4)
        col.addWidget(self.text)
        self.setStyleSheet(f"QFrame#peekRow {{ background: {theme.c('bg_alt')}; border-radius: 10px; }}")


class PeekDialog(QDialog):
    """안 읽은 최근 메시지(최대 5개)를 읽음 처리 없이 보여준다. mark_read/'read' 요청을 보내지 않는다."""

    def __init__(self, state, room_id, parent=None):
        super().__init__(parent)
        self.state = state
        self.room_id = room_id
        self.messages = state.peek_messages(room_id, 5)
        self.setWindowTitle('읽지 않고 미리보기')
        self.setMinimumSize(300, 320)
        self.resize(360, 400)
        room = state.room(room_id) or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        avatar = QLabel()
        size = theme.px(40)
        avatar.setPixmap(room_avatar_pixmap(state, room, size))
        avatar.setFixedSize(size, size)
        head.addWidget(avatar)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = ElidedLabel(state.room_title(room_id), 'title', 'text', bold=True)
        unread = state.room_unread(room_id)
        self.subtitle = ElidedLabel(f'안 읽은 메시지 {unread}개' if unread else '새 메시지 없음', 'meta', 'text_sub')
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        head.addLayout(texts, 1)
        layout.addLayout(head)

        note = QHBoxLayout()
        note.setSpacing(5)
        note.addWidget(icon_label('check', 'text_hint', theme.px(13)))
        self.note_label = QLabel('읽음으로 표시되지 않습니다')
        self.note_label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")
        note.addWidget(self.note_label)
        note.addStretch(1)
        layout.addLayout(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName('peekInner')
        inner.setStyleSheet(f"QWidget#peekInner {{ background: {theme.c('bg')}; }}")
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self.rows = []
        for m in self.messages:
            row = PeekRow(state, m)
            self.rows.append(row)
            col.addWidget(row)
        if not self.messages:
            empty = QLabel('미리 볼 메시지가 없습니다')
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color: {theme.c('text_hint')}; padding: 24px;")
            col.addWidget(empty)
        col.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton('닫기')
        self.open_button = QPushButton('채팅방 열기')
        self.open_button.setProperty('primary', True)
        self.open_button.setDefault(True)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.open_button)
        layout.addLayout(buttons)
        self.close_button.clicked.connect(self.reject)
        self.open_button.clicked.connect(self.open_room)

    def open_room(self):
        self.state.room_opened.emit(self.room_id)
        self.accept()


def QIcon_from_avatar(username, display_name):  # noqa: N802 - Qt 스타일 팩토리
    from PyQt5.QtGui import QIcon
    return QIcon(avatar_pixmap(username, display_name, theme.px(40)))


__all__ = ['ChatsTab', 'OpenChatDialog', 'OpenBadge', 'panel_dialog_qss', 'parse_tags',
           'CreateGroupDialog', 'InviteToRoomDialog', 'ChatFolderDialog', 'PeekDialog', 'FolderBar',
           'AiButton', 'format_room_time', 'ElidedLabel', 'StatusPill', 'RowList', 'TabHeader', 'date']
