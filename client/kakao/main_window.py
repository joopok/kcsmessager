# -*- coding: utf-8 -*-
"""
메인 창 - 왼쪽 세로 사이드바(친구/채팅/조직도/캘린더/더보기) + 탭 스택
채팅방 창 관리, 트레이 알림, 연결 끊김 배너, 토스트, 테마 즉시 적용, 자동 자리비움,
잠금모드(무입력 N분 / 수동 잠금, 앱 시작 시 잠금), 기능 둘러보기
"""

import time

from PyQt5.QtCore import Qt, QRectF, QTimer, QEvent, QObject, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPalette, QKeySequence, QIcon, QPixmap, QCursor
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
                             QPushButton, QStackedWidget, QAbstractButton, QSystemTrayIcon, QMenu,
                             QSizePolicy, QFrame, QShortcut, QDialog, QLineEdit, QListWidget,
                             QListWidgetItem)

from client.kakao import theme
from client.kakao import osenv
from client.kakao.widgets import (paint_icon, Badge, install_focus_visible, input_controls_qss, install_input_controls,
                                  install_dialog_title_bars)
from client.kakao.login import talk_icon, talk_icon_pixmap
from client.kakao import brand, macos
from client.kakao.chats_tab import presence_of, set_presence, user_pixmap, connect_avatar_changed

APP_TITLE = 'KCS Talk'
ORG_DOCK_WIDTH = 340          # 왼쪽 조직도 패널 폭
ORG_DOCK_MIN_WINDOW = 860     # 이 폭 이상이면 조직도 패널 자동 표시
SPLIT_MIN_WINDOW = 1180       # 이 폭 이상이면 목록 옆에 대화창을 붙여 연다 (분할 화면)
LIST_WIDTH = 380              # 분할 화면에서 목록 열의 폭
IDLE_SECONDS = 10 * 60
TAB_KEYS = ['friends', 'chats', 'org', 'calendar', 'more']
TAB_LABELS = {'friends': '친구', 'chats': '채팅', 'org': '조직도', 'calendar': '캘린더', 'more': '더보기'}
TAB_ICONS = {'friends': 'friends', 'chats': 'chat', 'org': 'building', 'calendar': 'calendar', 'more': 'more'}


# ================================================================ 테마 적용
def apply_app_theme(settings=None):
    """설정(다크 모드, 글자 크기)을 테마·팔레트·전역 스타일시트에 반영"""
    if settings is not None:
        theme.set_dark(bool(settings.get('dark_mode')))
        theme.set_brand('kcs')   # 회사 CI(KCS 블루) 고정 - 테마 색상 선택은 제공하지 않는다
        theme.set_font_scale(float(settings.get('font_scale') or 1.0))
        theme.set_reduce_motion(bool(settings.get('reduce_motion')))
    app = QApplication.instance()
    if app is None:
        return
    pal = QPalette()
    roles = {
        QPalette.Window: 'bg', QPalette.WindowText: 'text', QPalette.Base: 'input_bg',
        QPalette.AlternateBase: 'bg_alt', QPalette.Text: 'text', QPalette.Button: 'bg_alt',
        QPalette.ButtonText: 'text', QPalette.ToolTipBase: 'bg', QPalette.ToolTipText: 'text',
        QPalette.PlaceholderText: 'text_hint',
    }
    for role, key in roles.items():
        pal.setColor(role, QColor(theme.c(key)))
    pal.setColor(QPalette.Highlight, QColor(theme.c('yellow')))
    pal.setColor(QPalette.HighlightedText, QColor(theme.c('on_accent')))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(theme.c('text_hint')))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(theme.c('text_hint')))
    app.setPalette(pal)
    app.setFont(theme.font())
    app.setStyleSheet(theme.base_qss() + input_controls_qss())
    install_focus_visible(app)        # 포커스 테두리는 키보드로 옮겼을 때만
    install_input_controls(app)       # 콤보 목록이 QSS 줄 높이 · 선택 색을 따르도록
    install_dialog_title_bars(app)    # 팝업 제목 표시줄을 본문과 같은 색으로 (macOS)


# ================================================================ 사이드바
class SidebarButton(QAbstractButton):
    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(TAB_LABELS[key])
        self.setFixedSize(64, 58)
        self._hover = False

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            color = theme.c('sidebar_icon_active')
            # 선택된 탭 뒤 은은한 하이라이트(브랜드 사이드바에서 위치를 분명히)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc('sidebar_active_bg'))
            p.drawRoundedRect(QRectF(6, 2, self.width() - 12, self.height() - 4), 12, 12)
        elif self._hover:
            color = theme.c('sidebar_icon_hover')
        else:
            color = theme.c('sidebar_icon')
        s = 24
        rect = QRectF((self.width() - s) / 2, 8, s, s)
        # 아이콘 아래 탭 이름 (왼쪽 메뉴에서 '조직도' 등을 바로 찾을 수 있게)
        f = theme.font('micro', bold=self.isChecked(), scaled=False)   # 고정 크기 사이드바 버튼 안
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 36, self.width(), 16), Qt.AlignHCenter | Qt.AlignVCenter, TAB_LABELS[self.key])
        if self.key == 'org':
            from client.kakao.org_tab import paint_building
            paint_building(p, rect, color, stroke=1.9 if self.isChecked() else 1.7)
        else:
            filled = self.isChecked() and self.key in ('friends', 'chat', 'chats')
            paint_icon(p, TAB_ICONS[self.key], rect, color, stroke=1.9 if self.isChecked() else 1.7,
                       filled=True if filled else None)


class Sidebar(QWidget):
    tab_selected = pyqtSignal(str)
    logout_requested = pyqtSignal()      # 왼쪽 아래 내 사진 우클릭 → 로그아웃 (간편 로그아웃)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedWidth(64)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 16, 0, 14)
        col.setSpacing(4)
        # 회사 CI 마크
        self.logo = QLabel()
        self.logo.setObjectName('sidebarLogo')
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setFixedHeight(36)
        self.logo.setToolTip(f'KCS Talk · {brand.COMPANY_NAME}')
        col.addWidget(self.logo)
        col.addSpacing(10)
        self._refresh_logo()
        self.buttons = {}
        for key in TAB_KEYS:
            btn = SidebarButton(key)
            btn.clicked.connect(lambda _c=False, k=key: self.tab_selected.emit(k))
            col.addWidget(btn)
            self.buttons[key] = btn
        col.addStretch(1)
        self.me_avatar = QLabel()
        self.me_avatar.setFixedSize(theme.AVATAR_ROW, theme.AVATAR_ROW)   # 모든 목록과 같은 사진 크기
        self.me_avatar.setToolTip('내 프로필')
        self.me_avatar.setAccessibleName('내 프로필')
        self.me_avatar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.me_avatar.customContextMenuRequested.connect(
            lambda pos: self._show_me_menu(self.me_avatar.mapToGlobal(pos)))
        self.can_logout = False          # 메인 창이 로그아웃 처리를 연결하면 True
        col.addWidget(self.me_avatar, 0, Qt.AlignHCenter)
        self.chat_badge = Badge(0, self.buttons['chats'])
        self._place_badge()
        connect_avatar_changed(state, self.refresh_me)
        state.users_changed.connect(self.refresh_me)

    def refresh_me(self):
        if self.state.me:
            self.me_avatar.setPixmap(user_pixmap(self.state, self.state.me, theme.AVATAR_ROW, show_presence=True))
            tip = f"{self.state.display_name(self.state.me)} · 내 프로필"
            self.me_avatar.setToolTip(tip + ('\n우클릭: 로그아웃' if self.can_logout else ''))

    def me_menu(self):
        """내 사진 우클릭 메뉴: 이름(표시만) · 로그아웃(확인 창 없이 바로)"""
        menu = QMenu(self)
        if self.state.me:
            title = menu.addAction(self.state.display_name(self.state.me))
            title.setEnabled(False)
            menu.addSeparator()
        logout = menu.addAction('로그아웃', self.logout_requested.emit)
        logout.setEnabled(self.can_logout)
        return menu

    def _show_me_menu(self, global_pos):
        self.me_menu().exec_(global_pos)

    def _place_badge(self):
        btn = self.buttons['chats']
        self.chat_badge.move(min(btn.width() - self.chat_badge.width() - 2, 36), 6)
        self.chat_badge.raise_()

    def set_unread(self, count):
        self.chat_badge.set_count(count)
        self._place_badge()
        # 스크린리더: 숫자만이 아니라 맥락 있는 문장으로 (ui-ux-pro-max: Contextual Live Badge Updates)
        self.buttons['chats'].setAccessibleName(f'채팅, 안 읽은 메시지 {count}개' if count else '채팅')

    def set_current(self, key):
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)

    def _refresh_logo(self):
        # 브랜드(네이비) 사이드바: 흰 타일 + 네이비 마크 / 밝은 사이드바: 네이비 타일 + 흰 마크
        if theme.c('sidebar_logo') == '#FFFFFF':
            self.logo.setPixmap(brand.badge_pixmap(46, 34, '#FFFFFF', theme.BRAND_NAVY))
        else:
            self.logo.setPixmap(brand.badge_pixmap(46, 34, theme.BRAND_NAVY, '#FFFFFF'))

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.c('sidebar')))

    def set_top_inset(self, height):
        """macOS 투명 제목 표시줄: 신호등 버튼 높이만큼 로고·탭 버튼을 내린다 (배경은 맨 위까지 칠한다)"""
        margins = self.layout().contentsMargins()
        self.layout().setContentsMargins(margins.left(), 16 + height, margins.right(), margins.bottom())

    def apply_theme(self):
        self._refresh_logo()
        self.chat_badge.set_count(self.chat_badge.count)
        self._place_badge()
        self.refresh_me()
        self.update()
        for btn in self.buttons.values():
            btn.update()


class QuickSwitcher(QDialog):
    """Ctrl+K - 채팅방·동료 이름(직함·부서)으로 찾아 Enter 로 바로 연다. ↑/↓ 로 고르고 Esc 로 닫는다"""

    chosen = pyqtSignal(str, str)   # 'room'|'person', room_id|username

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle('빠른 이동')
        self.setMinimumSize(420, 380)
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('채팅방 또는 동료 이름 · 부서 · 직함')
        self.search.setAccessibleName('빠른 이동 검색')
        col.addWidget(self.search)
        self.list = QListWidget()
        self.list.setAccessibleName('검색 결과')
        self.list.setStyleSheet(
            f"QListWidget::item {{ padding: {theme.sp('s')}px {theme.sp('s')}px; border-radius: 8px; }}"
            f" QListWidget::item:selected {{ background: {theme.c('yellow')}; color: {theme.c('on_accent')}; }}"
            f" QListWidget::item:hover:!selected {{ background: {theme.c('bg_hover')}; color: {theme.c('text')}; }}")
        col.addWidget(self.list, 1)
        hint = QLabel('↑↓ 이동 · Enter 열기 · Esc 닫기')
        hint.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")
        col.addWidget(hint)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.accept_current)
        self.list.itemActivated.connect(lambda _i: self.accept_current())
        self.search.installEventFilter(self)
        self.refresh()

    def entries(self, query=''):
        q = query.strip().lower()
        out = []
        for room in self.state.sorted_rooms():
            title = self.state.room_title(room['room_id'])
            if not q or q in title.lower():
                unread = self.state.room_unread(room['room_id'])
                out.append(('room', room['room_id'], title + (f"  ·  안 읽음 {unread}" if unread else '')))
        for u in self.state.friends(include_bots=True):
            name = self.state.display_name(u['username'])
            meta = ' · '.join(x for x in (u.get('title'), ' '.join(u.get('org_path') or []) or u.get('department'))
                              if x)
            hay = ' '.join([name, meta, u.get('account') or '']).lower()
            if not q or q in hay:
                out.append(('person', u['username'], name + (f"  ·  {meta}" if meta else '')))
        return out[:200]

    def refresh(self):
        self.list.clear()
        from client.kakao.widgets import make_icon
        icons = {'room': make_icon('chat', theme.c('text_sub'), 18), 'person': make_icon('friends', theme.c('text_sub'), 18)}
        for kind, value, label in self.entries(self.search.text()):
            item = QListWidgetItem(icons[kind], label)
            item.setData(Qt.UserRole, (kind, value))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Down, Qt.Key_Up):
            row = self.list.currentRow() + (1 if event.key() == Qt.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            return True
        return super().eventFilter(obj, event)

    def accept_current(self):
        item = self.list.currentItem()
        if item is None:
            return
        kind, value = item.data(Qt.UserRole)
        self.chosen.emit(kind, value)
        self.accept()


class RoomPlaceholder(QWidget):
    """분할 화면에서 대화를 고르기 전의 오른쪽 칸 - 회사 마크와 안내 한 줄"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme.qc('chat_bg'))
        mark = brand.logo_pixmap('mark', 44, theme.c('text_hint'))
        mw = mark.width() / mark.devicePixelRatio()
        mh = mark.height() / mark.devicePixelRatio()
        cx, cy = self.width() / 2, self.height() / 2
        p.setOpacity(0.55)
        p.drawPixmap(QRectF(cx - mw / 2, cy - mh - 14, mw, mh), mark, QRectF(mark.rect()))
        p.setOpacity(1.0)
        p.setPen(theme.qc('text_sub'))
        p.setFont(theme.font('emphasis', bold=True))
        p.drawText(QRectF(0, cy + 4, self.width(), 22), Qt.AlignCenter, '대화를 선택하세요')
        p.setPen(theme.qc('text_hint'))
        p.setFont(theme.font('meta'))
        p.drawText(QRectF(0, cy + 28, self.width(), 20), Qt.AlignCenter, '왼쪽 목록에서 채팅방이나 동료를 고르면 여기에 열립니다')


# ================================================================ 토스트 / 배너
class Toast(QLabel):
    """창 아래쪽에 잠깐 떴다 사라지는 안내"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text, msec=2600):
        self.setStyleSheet(
            f"background: rgba(30,30,30,0.88); color: #FFFFFF; border-radius: 10px;"
            f" padding: 10px 16px; font-size: {theme.fs('body')}px;")
        self.setText(text)
        parent = self.parentWidget()
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(theme.font('body'))
        max_w = max(160, parent.width() - 48)
        width = min(max_w, fm.horizontalAdvance(text) + 32 + 4)
        self.setFixedWidth(width)
        self.setFixedHeight(self.heightForWidth(width) if self.hasHeightForWidth() else self.sizeHint().height())
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 28)
        self.show()
        self.raise_()
        self._timer.start(msec)


class ConnectionBanner(QFrame):
    reconnect_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 10, 8)
        row.setSpacing(8)
        self.label = QLabel('서버 연결이 끊어졌습니다')
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.button = QPushButton('재연결')
        self.button.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.label, 1)
        row.addWidget(self.button)
        self.button.clicked.connect(self.reconnect_clicked)
        self.apply_theme()
        self.hide()

    def apply_theme(self):
        self.setStyleSheet(
            # 경고 글자 danger_text 는 bg_alt 위에서 라이트·다크 모두 4.5:1 이상, 아래 빨간 줄로 경고임을 표시
            f"ConnectionBanner {{ background: {theme.c('bg_alt')};"
            f" border-bottom: 2px solid {theme.c('badge')}; }}"
            f" QLabel {{ color: {theme.c('danger_text')}; font-size: {theme.fs('body')}px; font-weight: bold; }}"
            f" QPushButton {{ background: {theme.c('bg')}; color: {theme.c('text')}; border: 1px solid {theme.c('border')};"
            f" border-radius: 12px; padding: 4px 12px; font-size: {theme.fs('meta')}px; }}")


# ================================================================ 안 읽은 메시지 아이콘 깜박임
def faded_icon(opacity, sizes=(16, 32, 64, 128, 256)):
    """앱 아이콘 전체를 흐리게 (깜박임의 '꺼진' 프레임). 빨간 점 같은 덧그림은 없다"""
    icon = QIcon()
    for size in sizes:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setOpacity(opacity)
        p.drawPixmap(0, 0, QPixmap(talk_icon_pixmap(size)))
        p.end()
        icon.addPixmap(pm)
    return icon


class UnreadBlinker(QObject):
    """안 읽은 메시지가 남아 있는 동안 앱 아이콘 전체를 깜박인다 (설정 'blink_unread').
    Dock·창·트레이 아이콘이 모두 평소 아이콘 ↔ 거의 사라진 아이콘을 오간다.
    '동작 줄이기'가 켜져 있으면 깜박이지 않고 흐린 아이콘으로 계속 표시한다. 다 읽으면 평소 아이콘으로 돌아온다."""

    INTERVAL_MS = 600
    OFF_OPACITY = 0.12       # 깜박임 '꺼짐' 프레임 - Dock 칸이 비어 보이지 않을 만큼만 남긴다
    STEADY_OPACITY = 0.45    # 동작 줄이기: 깜박이지 않고 흐리게만

    def __init__(self, window, settings, parent=None):
        super().__init__(parent)
        self.window = window
        self.settings = settings
        self.normal_icon = talk_icon()
        self.off_icon = faded_icon(self.OFF_OPACITY)
        self.steady_icon = faded_icon(self.STEADY_OPACITY)
        self.lit = True          # True = 평소 아이콘이 보이는 프레임
        self.alerting = False    # 안 읽음 표시 중(깜박임 또는 고정 흐린 아이콘)
        self.timer = QTimer(self)
        self.timer.setInterval(self.INTERVAL_MS)
        self.timer.timeout.connect(self._tick)

    @property
    def blinking(self):
        return self.timer.isActive()

    def update(self, unread):
        want = unread > 0 and bool(self.settings.get('blink_unread'))
        steady = bool(self.settings.get('reduce_motion'))
        if want and steady:
            self.timer.stop()
            self._show(False, steady=True)
        elif want:
            if not self.timer.isActive():
                self.timer.start()
                self._show(False)          # 바로 한 번 바뀌어 보이게
        elif self.alerting or self.timer.isActive():
            self.stop()
            return
        self.alerting = want

    def stop(self):
        self.timer.stop()
        self.alerting = False
        self._show(True)

    def _tick(self):
        self._show(not self.lit)

    def _show(self, lit, steady=False):
        self.lit = lit
        icon = self.normal_icon if lit else (self.steady_icon if steady else self.off_icon)
        tray = getattr(self.window, 'tray', None)
        if tray is not None:
            tray.setIcon(icon)
        self.window.setWindowIcon(icon)
        QApplication.setWindowIcon(icon)     # macOS Dock 아이콘 · 작업 표시줄


# ================================================================ 자동 자리비움
class IdleWatcher(QObject):
    """컴퓨터 전체 마우스/키보드 입력을 보고 10분 무입력이면 자동 자리비움, 다시 움직이면 곧바로 온라인.
    KCS Talk 창 밖(다른 앱)에서 마우스를 움직여도 입력으로 센다 - OS 무입력 시간(osenv.system_idle_seconds),
    알 수 없는 OS 에서는 마우스 포인터 위치 변화로 본다. 앱 안 입력은 이벤트로 바로 받는다.
    lock_seconds(초를 돌려주는 함수, 0/None = 꺼짐)와 on_lock 을 주면 무입력 잠금도 담당한다."""

    INPUT_EVENTS = {QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.Wheel,
                    QEvent.TouchBegin}
    POLL_MS = 3 * 1000          # 다른 앱에서의 움직임을 이 간격으로 확인 (자리비움 → 온라인이 몇 초 안에)

    def __init__(self, state, idle_seconds=IDLE_SECONDS, parent=None, lock_seconds=None, on_lock=None,
                 system_idle=None, cursor_pos=None):
        super().__init__(parent)
        self.state = state
        self.idle_seconds = idle_seconds
        self.lock_seconds = lock_seconds
        self.on_lock = on_lock
        self.system_idle = system_idle or osenv.system_idle_seconds
        self.cursor_pos = cursor_pos or (lambda: QCursor.pos())
        self._last_cursor = None
        self.last_input = time.monotonic()
        self.auto_away = False
        self.timer = QTimer(self)
        self.timer.setInterval(30 * 1000)
        self.timer.timeout.connect(self.check)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(self.POLL_MS)
        self.poll_timer.timeout.connect(self.poll)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.timer.start()
        self.poll_timer.start()

    def eventFilter(self, obj, event):
        if event.type() in self.INPUT_EVENTS:
            self.activity()
        return False

    def activity(self, now=None):
        self.last_input = time.monotonic() if now is None else now
        if self.auto_away:
            self.auto_away = False
            if presence_of(self.state, self.state.me) == 'away':
                set_presence(self.state, 'online')

    def poll(self, now=None):
        """앱 밖 입력 반영: OS 무입력 시간이 우리가 아는 마지막 입력보다 짧으면 그 사이에 움직인 것"""
        now = time.monotonic() if now is None else now
        idle = self.system_idle()
        if idle is not None:
            if now - idle > self.last_input + 0.5:
                self.activity(now - idle)
        else:
            pos = self.cursor_pos()
            pos = (pos.x(), pos.y()) if hasattr(pos, 'x') else tuple(pos)
            if self._last_cursor is not None and pos != self._last_cursor:
                self.activity(now)
            self._last_cursor = pos
        self.check(now)

    def check(self, now=None):
        now = time.monotonic() if now is None else now
        self.check_lock(now)
        if self.auto_away or not self.state.me:
            return
        if now - self.last_input >= self.idle_seconds and presence_of(self.state, self.state.me) == 'online':
            self.auto_away = True
            set_presence(self.state, 'away')

    def check_lock(self, now=None):
        """잠금 시간이 지났으면 on_lock 호출. 잠갔으면 True"""
        if self.on_lock is None or self.lock_seconds is None or getattr(self.state, 'locked', False):
            return False
        now = time.monotonic() if now is None else now
        seconds = self.lock_seconds()
        if seconds and now - self.last_input >= seconds:
            self.on_lock()
            return True
        return False

    def stop(self):
        self.timer.stop()
        self.poll_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)


# ================================================================ 메인 창
class PlaceholderTab(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        col.addWidget(self.label)
        self.apply_theme()

    def apply_theme(self):
        self.label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('body')}px;")


class TitleBand(QWidget):
    """사이드바 오른쪽 본문. macOS 에서 제목 표시줄을 투명하게 하면(로그인 창과 같은 모양) 위 28px 띠가
    비는데, 그 띠를 아래 열(조직도 · 목록 · 대화창)의 헤더 색으로 칠해 헤더가 창 맨 위까지 이어져 보이게 한다"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.band = 0
        self.columns = []    # [(widget, 칠할 색 토큰 목록을 돌려주는 함수)] - 반투명 색은 앞 색 위에 겹친다

    def set_band(self, height):
        self.band = height
        margins = self.layout().contentsMargins()
        self.layout().setContentsMargins(margins.left(), height, margins.right(), margins.bottom())
        self.update()

    def event(self, e):
        if e.type() == QEvent.LayoutRequest and self.band:   # 열이 생기거나 사라지면(분할 화면 · 조직도) 띠도 다시
            self.update(0, 0, self.width(), self.band)
        return super().event(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.band:
            self.update(0, 0, self.width(), self.band)

    def paintEvent(self, event):
        if not self.band:
            return
        p = QPainter(self)
        for widget, color in self.columns:
            if widget.isVisible():
                for key in color():
                    p.fillRect(widget.x(), 0, widget.width(), self.band, theme.qc(key))


def bring_window_to_front(win):
    """창을 맨 앞에 활성화. 최소화돼 있으면 되살리되 최대화 · 전체 화면은 그대로 둔다 -
    showNormal() 은 최대화를 풀고 처음 크기로 되돌려, 채팅 목록에서 연 방이 새 창으로 다시 열린 것처럼 보였다"""
    if win.isMinimized():
        win.setWindowState(win.windowState() & ~Qt.WindowMinimized)
    win.show()
    win.raise_()
    win.activateWindow()


class MainWindow(QMainWindow):
    def __init__(self, state, settings, reconnect=None, server_address='', parent=None, logout=None):
        super().__init__(parent)
        self.state = state
        self.settings = settings
        self.reconnect_cb = reconnect
        self.logout_cb = logout
        self.server_address = server_address
        self.room_windows = {}
        self._last_notify_room = ''
        self._quitting = False
        self._tray_hint_shown = False  # 창 닫기(X)로 트레이에 내려갈 때 안내 풍선은 한 번만
        self._hidden_rooms = []        # 잠금 중 숨긴 채팅방 창
        self._pending_rooms = []       # 잠금 중 열라고 요청된 방
        self.feature_tour = None

        self.setWindowIcon(talk_icon())
        self.resize(400, 680)
        central = QWidget()
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = Sidebar(state)
        row.addWidget(self.sidebar)
        self.body = TitleBand()
        body_row = QHBoxLayout(self.body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        row.addWidget(self.body, 1)
        row = body_row
        # 넓은 창에서는 조직도를 왼쪽에 항상 펼쳐 둔다(현재 탭이 조직도가 아닐 때)
        from client.kakao.org_tab import OrgTab
        self.org_dock = OrgTab(state, settings)
        self.org_dock.setObjectName('orgDock')
        self.org_dock.setFixedWidth(ORG_DOCK_WIDTH)
        self.org_dock.hide()
        row.addWidget(self.org_dock)
        self.dock_line = QWidget()
        self.dock_line.setFixedWidth(1)
        self.dock_line.hide()
        row.addWidget(self.dock_line)
        right = QWidget()
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        self.banner = ConnectionBanner()
        right_col.addWidget(self.banner)
        self.stack = QStackedWidget()
        right_col.addWidget(self.stack, 1)
        row.addWidget(right, 1)
        self.list_column = right
        # 분할 화면: 목록 | 대화창 (넓은 창에서 빈 공간 대신 대화를 바로 보여 준다)
        self.split_line = QWidget()
        self.split_line.setFixedWidth(1)
        self.split_line.hide()
        row.addWidget(self.split_line)
        self.room_pane = QStackedWidget()
        self.room_pane.hide()
        self.room_placeholder = RoomPlaceholder()
        self.room_pane.addWidget(self.room_placeholder)
        row.addWidget(self.room_pane, 1)
        self.toast = Toast(central)
        self.body.columns = [(self.org_dock, lambda: ['bg']), (self.dock_line, lambda: ['border']),
                             (self.list_column, lambda: ['bg']), (self.split_line, lambda: ['border']),
                             (self.room_pane, self._room_band_colors)]
        self.room_pane.currentChanged.connect(lambda _i: self.body.update())

        self.tabs = {}
        self._build_tabs()
        self.sidebar.tab_selected.connect(self.set_tab)
        self.set_tab('friends')

        # 상태 시그널
        state.room_opened.connect(self.open_room)
        state.rooms_changed.connect(self.refresh_unread)
        state.room_removed.connect(self._close_room_window)
        state.connection_lost.connect(self._on_connection_lost)
        state.welcomed.connect(self._on_welcomed)
        state.error.connect(self.show_toast)
        state.notify.connect(self.show_notification)
        state.admin_notice.connect(self.show_admin_notice)
        state.file_blocked.connect(self.show_file_blocked)
        state.file_request_changed.connect(self._on_file_request_changed)
        settings.changed.connect(self._on_setting_changed)
        self.banner.reconnect_clicked.connect(self.reconnect)

        self._setup_shortcuts()
        from client.kakao.widgets import label_buttons_for_accessibility
        label_buttons_for_accessibility(self)
        self.tray = None
        self._setup_tray()
        self.blinker = UnreadBlinker(self, settings, self)
        self.idle_watcher = IdleWatcher(state, parent=self, lock_seconds=self._lock_seconds, on_lock=self.lock)
        from client.kakao.lock import LockScreen, has_pin
        self.lock_screen = LockScreen(settings, self)
        self.lock_screen.unlocked.connect(self.unlock)
        self.sidebar.refresh_me()
        self.refresh_unread()
        if has_pin(settings):    # 비밀번호가 있으면 잠긴 상태로 시작
            self.lock()

    # ---- 잠금모드 ----
    def _lock_seconds(self):
        from client.kakao.lock import has_pin, lock_minutes
        return lock_minutes(self.settings) * 60 if has_pin(self.settings) else 0

    def is_locked(self):
        return bool(getattr(self.state, 'locked', False))

    def lock(self):
        """잠금 화면을 덮고 채팅방 창을 숨긴다. 비밀번호가 없으면 False"""
        from client.kakao.lock import has_pin
        if not has_pin(self.settings):
            self.show_toast('잠금 비밀번호를 먼저 설정해 주세요')
            return False
        if self.is_locked():
            return True
        self.state.locked = True
        self._hidden_rooms = []
        for rid, win in list(self.room_windows.items()):
            try:
                if win.isVisible():
                    win.hide()
                    self._hidden_rooms.append(rid)
            except RuntimeError:
                self.room_windows.pop(rid, None)
        for dlg in (self.feature_tour,):
            try:
                if dlg is not None and dlg.isVisible():
                    dlg.hide()
            except RuntimeError:
                self.feature_tour = None
        self.toast.hide()
        self.lock_screen.show_over()
        return True

    def unlock(self):
        """잠금 해제 - 숨겼던 채팅방 창과 잠금 중 요청된 방을 다시 보여준다"""
        self.state.locked = False
        self.lock_screen.hide()
        self.idle_watcher.activity()
        hidden, self._hidden_rooms = self._hidden_rooms, []
        for rid in hidden:
            win = self.room_windows.get(rid)
            if win is not None:
                try:
                    win.show()
                except RuntimeError:
                    self.room_windows.pop(rid, None)
        pending, self._pending_rooms = self._pending_rooms, []
        for rid in pending:
            self.open_room(rid)
        return True

    # ---- 기능 둘러보기 ----
    def open_feature_tour(self):
        try:
            from client.kakao.feature_tour import FeatureTourDialog
        except Exception:   # 모듈이 없거나 깨져도 앱은 계속 동작
            self.show_toast('기능 둘러보기를 불러올 수 없습니다')
            return None
        try:
            if self.feature_tour is not None and self.feature_tour.isVisible():
                self.feature_tour.raise_()
                self.feature_tour.activateWindow()
                return self.feature_tour
        except RuntimeError:
            self.feature_tour = None
        try:
            dlg = FeatureTourDialog(self.state, self.settings, self, self)
        except Exception as e:
            self.show_toast(f'기능 둘러보기를 열 수 없습니다 ({e.__class__.__name__})')
            return None
        self.feature_tour = dlg
        dlg.show()
        dlg.raise_()
        return dlg

    # ---- 탭 ----
    def _build_tabs(self):
        from client.kakao.friends_tab import FriendsTab
        from client.kakao.chats_tab import ChatsTab
        from client.kakao.org_tab import OrgTab
        from client.kakao.more_tab import MoreTab
        self.tabs['friends'] = FriendsTab(self.state, self.settings)
        self.tabs['chats'] = ChatsTab(self.state, self.settings)
        self.tabs['org'] = OrgTab(self.state, self.settings)
        try:
            from client.kakao.calendar_tab import CalendarTab
            self.tabs['calendar'] = CalendarTab(self.state)
        except Exception as e:  # 캘린더 모듈이 없거나 오류여도 앱은 동작해야 한다
            self.tabs['calendar'] = PlaceholderTab(f'캘린더를 불러올 수 없습니다\n({e.__class__.__name__})')
        self.tabs['more'] = MoreTab(self.state, self.settings, server_address=self.server_address)
        self.tabs['more'].logout_requested.connect(lambda: self.logout_cb and self.logout_cb())
        # 왼쪽 아래 내 사진 우클릭 → 로그아웃: 메뉴를 고른 것이 확인이라 확인 창 없이 바로 (간편 로그아웃)
        self.sidebar.can_logout = self.logout_cb is not None
        self.sidebar.logout_requested.connect(lambda: self.logout_cb and self.logout_cb())
        self.sidebar.refresh_me()
        for key in TAB_KEYS:
            self.stack.addWidget(self.tabs[key])

    def set_tab(self, key):
        if key not in self.tabs:
            return
        self.stack.setCurrentWidget(self.tabs[key])
        self.sidebar.set_current(key)
        self._update_dock()

    @staticmethod
    def _repolish_room(win):
        def apply():
            try:
                if hasattr(win, 'apply_theme'):
                    win.apply_theme()
                if hasattr(win, '_relayout_rows'):
                    win._relayout_rows()
            except RuntimeError:
                pass
        apply()
        QTimer.singleShot(0, apply)

    # ---- 키보드 ----
    # 키 연결은 어디서나 'Ctrl+…' (Qt 가 macOS 에서 ⌘ 로 바꾼다), 보이는 글자만 OS 표기로
    SHORTCUT_HELP = [(osenv.shortcut_text('Ctrl+1~5'), '친구 · 채팅 · 조직도 · 캘린더 · 더보기'),
                     (osenv.shortcut_text('Ctrl+K'), '채팅방·동료 빠른 이동'),
                     (osenv.shortcut_text('Ctrl+N'), '새 채팅'), (osenv.shortcut_text('Ctrl+Shift+A'), '친구 추가')]

    def _setup_shortcuts(self):
        self.shortcuts = []

        def add(seq, fn):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(fn)
            self.shortcuts.append(sc)
        for i, key in enumerate(TAB_KEYS[:5], start=1):
            add(f'Ctrl+{i}', lambda k=key: self.set_tab(k))
        add('Ctrl+K', self.open_quick_switcher)
        add('Ctrl+N', lambda: self.tabs['chats'].show_create_dialog() if hasattr(self.tabs['chats'], 'show_create_dialog')
            else None)
        add('Ctrl+Shift+A', lambda: self.tabs['friends'].show_add_friend()
            if hasattr(self.tabs['friends'], 'show_add_friend') else None)
        for key, btn in self.sidebar.buttons.items():
            idx = TAB_KEYS.index(key) + 1
            btn.setToolTip(f"{TAB_LABELS[key]}  ({osenv.shortcut_text(f'Ctrl+{idx}')})")
            btn.setAccessibleName(TAB_LABELS[key])

    def open_quick_switcher(self, exec_=True):
        dlg = QuickSwitcher(self.state, self)
        dlg.chosen.connect(self._quick_open)
        if exec_:
            dlg.exec_()
        return dlg

    def _quick_open(self, kind, value):
        if kind == 'room':
            self.open_room(value)
        else:
            self.state.open_direct(value)

    def is_split(self):
        return hasattr(self, 'room_pane') and self.room_pane.isVisible()

    def _update_split(self):
        """넓은 창이면 목록 폭을 고정하고 오른쪽에 대화창 칸을 연다. 좁아지면 대화창을 별도 창으로 꺼낸다"""
        if not hasattr(self, 'room_pane'):
            return
        split = self.width() >= SPLIT_MIN_WINDOW
        if split == self.room_pane.isVisible():
            return
        if split:
            self.list_column.setFixedWidth(LIST_WIDTH)
            self.room_pane.show()
            self.split_line.show()
            self.split_line.setStyleSheet(f"background: {theme.c('border')};")
        else:
            for rid, win in list(self.room_windows.items()):
                try:
                    if win.parent() is self.room_pane:
                        self.room_pane.removeWidget(win)
                        win.setParent(None)
                        win.resize(420, 680)
                        win.show()
                        self._repolish_room(win)
                except RuntimeError:
                    self.room_windows.pop(rid, None)
            self.room_pane.hide()
            self.split_line.hide()
            self.list_column.setMinimumWidth(0)
            self.list_column.setMaximumWidth(16777215)

    def _update_dock(self):
        """창 폭이 충분하면 왼쪽에 조직도 패널 표시 (조직도 탭을 보고 있을 땐 중복이므로 숨김)"""
        if not hasattr(self, 'org_dock'):
            return
        self._update_split()
        show = self.width() >= ORG_DOCK_MIN_WINDOW and self.current_tab() != 'org'
        self.org_dock.setVisible(show)
        self.dock_line.setVisible(show)
        self.dock_line.setStyleSheet(f"background: {theme.c('border')};")

    def current_tab(self):
        widget = self.stack.currentWidget()
        for key, tab in self.tabs.items():
            if tab is widget:
                return key
        return ''

    # ---- 안 읽은 수 ----
    def refresh_unread(self):
        total = self.state.total_unread()
        self.sidebar.set_unread(total)
        self.setWindowTitle(f"{APP_TITLE} ({total})" if total else APP_TITLE)
        if self.tray is not None:
            self.tray.setToolTip(f"{APP_TITLE} - 안 읽은 메시지 {total}개" if total else APP_TITLE)
        if getattr(self, 'blinker', None) is not None:
            self.blinker.update(total)

    # ---- 채팅방 창 ----
    def open_room(self, room_id):
        if not room_id or self.state.room(room_id) is None:
            return None
        if self.is_locked():     # 잠금 중에는 해제 후 연다
            if room_id not in self._pending_rooms:
                self._pending_rooms.append(room_id)
            return None
        win = self.room_windows.get(room_id)
        if win is not None:
            try:
                if self.is_split() and win.parent() is self.room_pane:
                    self.room_pane.setCurrentWidget(win)
                    win.setFocus()
                    return win
                bring_window_to_front(win)   # 이미 열린 창을 그대로 (최대화 · 전체 화면 · 크기 · 위치 유지)
                return win
            except RuntimeError:  # 이미 삭제된 C++ 객체
                self.room_windows.pop(room_id, None)
        try:
            from client.kakao.chat_room import ChatRoomWindow
        except Exception as e:
            self.show_toast(f'채팅방을 열 수 없습니다 ({e.__class__.__name__})')
            return None
        win = ChatRoomWindow(self.state, room_id)
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        # 같은 방을 다시 열어 새 창이 들어와 있으면 그 항목은 지우지 않는다 (이 창일 때만 제거)
        win.destroyed.connect(lambda _obj=None, rid=room_id, w=win:
                              self.room_windows.pop(rid, None) if self.room_windows.get(rid) is w else None)
        if self.is_split():
            # 분할 화면: 오른쪽 칸에 끼워 연다
            self.room_windows[room_id] = win
            self.room_pane.addWidget(win)
            self.room_pane.setCurrentWidget(win)
            win.show()
            # 부모가 바뀌면 Qt가 채팅방 자체 스타일시트(말풍선 안쪽 여백 등)를 자식에 바로 다시 적용하지 않는다
            # → 테마를 다시 입혀 스타일을 새로 계산하고, 새 폭으로 말풍선 줄바꿈을 다시 한다
            self._repolish_room(win)
            return win
        n = len(self.room_windows) % 6
        geo = self.frameGeometry()
        screen = QApplication.desktop().availableGeometry(self)
        # 메인 창 오른쪽(공간이 없으면 왼쪽)에 열고, 여러 개면 계단식으로 비켜 놓는다
        x = geo.right() + 8
        if x + win.width() > screen.right():
            x = max(screen.left(), geo.left() - win.width() - 8)
        y = geo.top()
        x = min(x + 26 * n, max(screen.left(), screen.right() - win.width()) + 26 * n)
        y = min(y + 26 * n, max(screen.top(), screen.bottom() - win.height()) + 26 * n)
        pos = QPoint(x, y)
        win.move(pos)
        self.room_windows[room_id] = win
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def focus_message(self, room_id, msg_id, hint=None):
        win = self.open_room(room_id)
        if win is not None and hasattr(win, 'focus_message'):
            if hint is not None and hint.get('thread_root') is not None:   # 스레드 답글이면 스레드 창 (§10.4)
                win.focus_message(msg_id, hint=hint)
            else:
                win.focus_message(msg_id)

    def _close_room_window(self, room_id):
        win = self.room_windows.pop(room_id, None)
        if win is not None:
            try:
                win.close()
            except RuntimeError:
                pass

    # ---- 알림 ----
    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(talk_icon(), self)
        # 메인 창을 트레이로 내려도(모든 창이 닫혀도) 앱이 끝나지 않게. 종료는 quit_app · 로그아웃(QApplication.quit) 으로만
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        menu = QMenu()
        menu.addAction('KCS Talk 열기', self.bring_to_front)
        menu.addSeparator()
        self.tray_dnd_menu = menu.addMenu('알림 일시중지')
        self.tray_dnd_menu.aboutToShow.connect(self._fill_tray_dnd_menu)
        menu.addSeparator()
        if self.logout_cb is not None:
            self.tray_logout_action = menu.addAction('로그아웃…', self.request_logout)
        menu.addAction('종료', self.quit_app)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self._notification_clicked)
        self.tray.show()

    def _fill_tray_dnd_menu(self):
        """트레이 → 알림 일시중지 (방해금지 §10.1)"""
        from client.kakao import dnd
        menu = self.tray_dnd_menu
        menu.clear()
        status = menu.addAction(dnd.status_text(self.settings))
        status.setEnabled(False)
        menu.addSeparator()
        for label, preset in dnd.PAUSE_PRESETS:
            menu.addAction(label, lambda _checked=False, p=preset: dnd.pause(self.settings, p))
        if dnd.paused_until(self.settings) is not None:
            menu.addSeparator()
            menu.addAction('일시중지 끄기', lambda _checked=False: dnd.resume(self.settings))

    def request_logout(self):
        """트레이 메뉴 등에서: 창을 앞으로 가져와 더보기 탭과 같은 확인 후 로그아웃"""
        self.bring_to_front()
        self.tabs['more']._confirm_logout()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.bring_to_front()

    def bring_to_front(self):
        if self.is_locked():
            self.lock_screen.show_over()
        bring_window_to_front(self)

    def _app_focused_on(self, room_id):
        active = QApplication.activeWindow()
        if active is None:
            return False
        win = self.room_windows.get(room_id)
        if win is not None and active is win:
            return True
        return active is self

    def should_notify(self, room_id):
        if not self.settings.get('notifications'):
            return False
        return not self._app_focused_on(room_id)

    def show_notification(self, title, body, room_id):
        if not self.should_notify(room_id):
            return False
        self._last_notify_room = room_id
        if self.tray is not None and self.tray.supportsMessages():
            self.tray.showMessage(title, body, talk_icon(), 4000)
        else:
            self.show_toast(f"{title}: {body}")
        return True

    def _notification_clicked(self):
        rid = self._last_notify_room
        self.bring_to_front()
        if rid:
            self.open_room(rid)

    def show_toast(self, text):
        self.toast.show_message(text)

    def show_admin_notice(self, notice):
        """관리자 전체 공지 (명세 §4.2): 비모달 '관리자 공지' 창 + 트레이 알림"""
        from client.kakao.admin import show_admin_notice
        if self.tray is not None and self.tray.supportsMessages():
            self.tray.showMessage('관리자 공지', notice.get('content', ''), talk_icon(), 6000)
        self.admin_notice_box = show_admin_notice(self, notice)
        return self.admin_notice_box

    def show_file_blocked(self, info):
        """파일 전송 보안 (명세 §10.10): 막힌 파일 → 승인 요청 창 (비모달)"""
        from client.kakao.file_guard import show_file_blocked
        self.file_blocked_dialog = show_file_blocked(self.state, info, self)
        return self.file_blocked_dialog

    def _on_file_request_changed(self, request):
        if request.get('status') == 'pending':
            self.show_toast(f"'{request.get('name', '')}' 승인 요청을 보냈어요. 관리자가 승인하면 전송돼요.")
        elif request.get('status') == 'approved':
            self.show_toast(f"'{request.get('name', '')}' 전송이 승인되어 보냈어요.")
        elif request.get('status') == 'rejected':
            note = f" ({request['note']})" if request.get('note') else ''
            self.show_toast(f"'{request.get('name', '')}' 요청이 반려됐어요{note}")

    # ---- 연결 ----
    def _on_connection_lost(self, reason=''):
        self.banner.label.setText('서버 연결이 끊어졌습니다')
        self.banner.setToolTip(reason or '')
        self.banner.button.setEnabled(self.reconnect_cb is not None)
        self.banner.show()

    def show_reconnecting(self, seconds):
        """자동 재연결 대기 중 안내 (지금 다시 시도 버튼은 그대로)"""
        self.banner.label.setText(f'서버 연결이 끊어졌습니다 · {seconds}초 뒤 자동으로 다시 연결합니다')
        self.banner.button.setEnabled(self.reconnect_cb is not None)
        self.banner.show()

    def _on_welcomed(self):
        self.banner.hide()
        self.sidebar.refresh_me()
        self.refresh_unread()

    def reconnect(self):
        if self.reconnect_cb is None:
            return
        self.banner.button.setEnabled(False)
        self.banner.label.setText('재연결 중…')
        QApplication.processEvents()
        try:
            result = self.reconnect_cb()
        except Exception as e:  # 재연결 콜백 오류도 배너로 보여준다
            result = (False, str(e))
        ok, message = result if isinstance(result, tuple) else (bool(result), '')
        self.banner.button.setEnabled(True)
        if ok:
            self.banner.hide()
            self.show_toast('서버에 다시 연결되었습니다')
        else:
            self.banner.label.setText('서버 연결이 끊어졌습니다')
            self.show_toast(message or '재연결에 실패했습니다')

    # ---- 테마 ----
    def _on_setting_changed(self, key):
        if key in ('dark_mode', 'font_scale', 'brand_theme'):
            self.apply_theme_all()
        elif key in ('muted_rooms', 'blink_unread', 'reduce_motion'):
            self.refresh_unread()

    MIN_FLOOR = (340, 520)   # 창 최소 크기 하한 (디자인 기준)

    def update_minimum_size(self):
        """최소 크기 = max(하한, 레이아웃이 필요한 크기) - 글자 200%에서도 창이 내용보다 좁아져 잘리지 않게 (§10.9)"""
        layout = self.centralWidget().layout() if self.centralWidget() else None
        if layout is None:
            return
        layout.activate()
        need = layout.totalMinimumSize()
        self.setMinimumSize(max(self.MIN_FLOOR[0], need.width()), max(self.MIN_FLOOR[1], need.height()))

    def apply_theme_all(self):
        apply_app_theme(self.settings)
        self.apply_theme()
        QTimer.singleShot(0, self.update_minimum_size)
        for win in list(self.room_windows.values()):
            try:
                if hasattr(win, 'apply_theme'):
                    win.apply_theme()
            except RuntimeError:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        self.update_minimum_size()
        # macOS: 회색 제목 표시줄 대신 사이드바 · 헤더 색이 창 맨 위(신호등 버튼 뒤)까지 (로그인 창과 같은 모양)
        if not getattr(self, '_title_bar_unified', False) and macos.unify_title_bar(self):
            self._title_bar_unified = True
            self.sidebar.set_top_inset(macos.TITLE_BAR_HEIGHT)
            self.body.set_band(macos.TITLE_BAR_HEIGHT)

    def _room_band_colors(self):
        """분할 화면 대화창 위 띠: 대화방이면 대화방 헤더와 같게(대화 배경 위 반투명 헤더), 빈 화면이면 그 배경색"""
        if self.room_pane.currentWidget() is self.room_placeholder:
            return ['chat_bg']
        return ['chat_bg', 'header_panel']

    def apply_theme(self):
        self.body.update()
        self.sidebar.apply_theme()
        self.banner.apply_theme()
        self.lock_screen.apply_theme()
        self.org_dock.apply_theme()
        self._update_dock()
        for tab in self.tabs.values():
            if hasattr(tab, 'apply_theme'):
                tab.apply_theme()
        self.refresh_unread()

    # ---- 종료 ----
    def quit_app(self):
        self._quitting = True
        self.close()

    def hide_to_tray(self):
        """메인 창만 숨기고 트레이에서 계속 실행 (채팅방 별도 창은 그대로). 처음 한 번 트레이 풍선으로 알려 준다"""
        self.hide()
        if not self._tray_hint_shown and self.tray is not None:
            self._tray_hint_shown = True
            try:
                self.tray.showMessage(APP_TITLE, 'KCS Talk 는 트레이에서 계속 실행됩니다. 트레이 아이콘을 누르면 다시 열리고,'
                                      ' 완전히 끝내려면 트레이 메뉴의 [종료]를 누르세요.', QSystemTrayIcon.Information, 4000)
            except Exception:
                pass

    def closeEvent(self, event):
        # 창 닫기(X) 는 종료가 아니라 트레이로 내려가기 (2026-10-15, 카카오톡과 같음). 트레이가 없는 환경이나
        # 트레이 메뉴 [종료] · 로그아웃(quit_app → _quitting) 때만 실제로 끝낸다
        if self.tray is not None and not self._quitting:
            event.ignore()
            self.hide_to_tray()
            return
        for win in list(self.room_windows.values()):
            try:
                win.close()
            except RuntimeError:
                pass
        self.idle_watcher.stop()
        self.blinker.stop()
        self.state.locked = False
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_dock()
        if self.toast.isVisible():
            self.toast.move((self.centralWidget().width() - self.toast.width()) // 2,
                            self.centralWidget().height() - self.toast.height() - 28)


__all__ = ['MainWindow', 'apply_app_theme', 'IdleWatcher', 'talk_icon_pixmap']
