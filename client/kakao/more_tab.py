# -*- coding: utf-8 -*-
"""
더보기 탭 - 프로필 카드, 상태, 다크 모드, 글자 크기, 알림, 배경화면, AI 지식베이스,
멀티프로필, 친구 관리(숨김·차단), 키워드 알림, 말풍선 꾸미기, 잠금모드, 기능 둘러보기, 정보
"""

from PyQt5.QtCore import Qt, QSize, QRectF, pyqtProperty, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
                             QScrollArea, QFrame, QAbstractButton, QDialog, QListWidget,
                             QListWidgetItem, QFileDialog, QSizePolicy, QMessageBox, QLineEdit)

from client.kakao import theme
from client.kakao.widgets import exec_and_release
from client.kakao.widgets import Avatar, make_icon, FlowWidget, disconnect_signals
from client.kakao.chats_tab import ElidedLabel, TabHeader, user_pixmap, connect_avatar_changed
from client.kakao.friends_tab import ProfileEditDialog, PresenceButton

APP_VERSION = '2.0'
FONT_SCALES = [('작게', 0.9), ('보통', 1.0), ('크게', 1.15), ('아주 크게', 1.3), ('최대 (200%)', 2.0)]   # 200% 확대까지 (WCAG 1.4.4)


class ToggleSwitch(QAbstractButton):
    """iOS/카카오 스타일 토글 스위치"""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b'offset', self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(44, 26)

    def _get_offset(self):
        return self._offset

    def _set_offset(self, value):
        self._offset = value
        self.update()

    offset = pyqtProperty(float, _get_offset, _set_offset)

    def _animate(self, checked):
        self._anim.stop()
        if self.isVisible():
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(1.0 if checked else 0.0)
            self._anim.start()
        else:
            self._set_offset(1.0 if checked else 0.0)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off = QColor('#5A5A5A') if theme.is_dark() else QColor('#D6D6D6')
        on = QColor(theme.c('yellow'))
        t = self._offset
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.x() + 2 + (r.width() - d - 4) * t
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(QRectF(x, r.y() + 2, d, d))


class ArrowComboBox(QComboBox):
    """스타일시트로 꾸며도 아래 화살표가 보이도록 직접 그린다"""

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt5.QtCore import QPointF
        from PyQt5.QtGui import QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(theme.qc('text_sub'), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        x, y = self.width() - 16, self.height() / 2
        p.drawPolyline(QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2))


class SettingRow(QWidget):
    """왼쪽 제목(+설명) / 오른쪽 컨트롤 한 줄"""

    def __init__(self, title, control=None, description='', parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.sp('m') + 2, theme.sp('s') + 2, theme.sp('m'), theme.sp('s') + 2)
        row.setSpacing(theme.sp('m'))
        col = QVBoxLayout()
        col.setSpacing(theme.sp('xxs'))
        self.title = ElidedLabel(title, 'emphasis', 'text')
        col.addWidget(self.title)
        self.description = None
        if description:
            self.description = ElidedLabel(description, 'meta', 'text_hint')
            col.addWidget(self.description)
        row.addLayout(col, 1)
        self.control = control
        if control is not None:
            if not control.accessibleName():   # 스크린리더: 토글/콤보에 줄 제목을 이름으로
                control.setAccessibleName(title)
            if description:
                control.setAccessibleDescription(description)
            row.addWidget(control, 0, Qt.AlignVCenter)
        self._col = col

    def set_description(self, text):
        if self.description is None:
            self.description = ElidedLabel(text, 'meta', 'text_hint')
            self._col.addWidget(self.description)
        else:
            self.description.setText(text)


class ChevronButton(QPushButton):
    """'열기 ›' 같은 오른쪽 이동 버튼"""

    def __init__(self, text='', parent=None):
        super().__init__(text + '  ›' if text else '›', parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty('flat', True)


# ================================================================ AI 지식베이스
class RagDocsDialog(QDialog):
    """AI 비서 지식베이스 문서 목록 + 추가/삭제"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle('AI 지식베이스')
        self.setMinimumSize(340, 380)
        self.resize(400, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        title = QLabel('AI 지식베이스')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel('AI 비서가 답변할 때 참고하는 문서입니다. 텍스트·마크다운·CSV·JSON·PDF 파일을 추가할 수 있어요.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setStyleSheet(f"QListWidget {{ border: 1px solid {theme.c('border')}; border-radius: 8px; }}"
                                f" QListWidget::item {{ padding: 8px; }}")
        layout.addWidget(self.list, 1)
        self.empty_label = QLabel('등록된 문서가 없습니다')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {theme.c('text_hint')};")
        layout.addWidget(self.empty_label)
        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.c('badge')}; font-size: {theme.fs('meta')}px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        self.add_button = QPushButton('추가')
        self.add_button.setProperty('primary', True)
        self.delete_button = QPushButton('삭제')
        close = QPushButton('닫기')
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self.choose_file)
        self.delete_button.clicked.connect(self.delete_selected)
        close.clicked.connect(self.accept)
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        sig = getattr(state, 'rag_docs_changed', None)
        if sig is not None:
            sig.connect(self.set_docs)
        self.set_docs(list(getattr(state, 'rag_docs', []) or []))
        if hasattr(state, 'request_rag_docs'):
            state.request_rag_docs()

    def set_docs(self, docs):
        self.list.clear()
        for doc in docs:
            size = int(doc.get('size') or 0)
            size_text = f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / 1048576:.1f}MB"
            owner = doc.get('owner') or '공유'
            item = QListWidgetItem(make_icon('file', theme.c('text_sub'), 20),
                                   f"{doc.get('name', '')}\n{size_text} · 조각 {doc.get('chunks', 0)}개 · {owner}")
            item.setData(Qt.UserRole, doc.get('doc_id'))
            item.setData(Qt.UserRole + 1, doc.get('owner') or '')
            self.list.addItem(item)
        self.empty_label.setVisible(not docs)
        self._update_buttons()

    def _update_buttons(self):
        item = self.list.currentItem()
        self.delete_button.setEnabled(item is not None and item.data(Qt.UserRole + 1) == self.state.me)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '문서 추가', '', '문서 (*.txt *.md *.csv *.json *.log *.py *.pdf);;모든 파일 (*)')
        if path:
            self.add_file(path)

    def add_file(self, path):
        err = self.state.add_rag_doc(path) if hasattr(self.state, 'add_rag_doc') else '지원되지 않습니다.'
        self.error_label.setText(err or '')
        self.error_label.setVisible(bool(err))
        return err

    def delete_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text().split('\n')[0]
        if QMessageBox.question(self, '문서 삭제', f"'{name}' 문서를 지식베이스에서 삭제할까요?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.state.delete_rag_doc(item.data(Qt.UserRole))


# ================================================================ 더보기 탭
class MoreTab(QWidget):
    logout_requested = pyqtSignal()

    def __init__(self, state, settings, server_address='', parent=None):
        super().__init__(parent)
        self.state = state
        self.settings = settings
        self.server_address = server_address
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = TabHeader('더보기')
        outer.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        self.body = QWidget()
        self.body.setObjectName('moreBody')
        self.scroll.setWidget(self.body)
        col = QVBoxLayout(self.body)
        col.setContentsMargins(0, 4, 0, 20)
        col.setSpacing(0)

        # ---- 프로필 카드 ----
        self.card = QFrame()
        self.card.setObjectName('profileCard')
        card_row = QHBoxLayout(self.card)
        card_row.setContentsMargins(16, 12, 16, 12)
        card_row.setSpacing(12)
        # 친구 탭 내 프로필과 같은 사진 크기 (예전 64px 은 이 화면에서만 커 보였다, 2026-10-10)
        self.avatar = Avatar(size=theme.px(theme.AVATAR_ROW))
        card_row.addWidget(self.avatar, 0, Qt.AlignVCenter)
        info = QVBoxLayout()
        info.setSpacing(3)
        info.addStretch(1)
        self.name_label = ElidedLabel('', 'emphasis', 'text', bold=True)
        self.status_label = ElidedLabel('', 'meta', 'text_sub')
        self.org_label = ElidedLabel('', 'meta', 'text_hint')
        info.addWidget(self.name_label)
        info.addWidget(self.status_label)
        info.addWidget(self.org_label)
        info.addStretch(1)
        card_row.addLayout(info, 1)
        self.edit_button = QPushButton('편집')
        self.edit_button.setCursor(Qt.PointingHandCursor)
        card_row.addWidget(self.edit_button, 0, Qt.AlignVCenter)
        card_wrap = QHBoxLayout()
        card_wrap.setContentsMargins(16, 8, 16, 8)
        card_wrap.addWidget(self.card)
        col.addLayout(card_wrap)

        # ---- 계정: 탭을 열자마자 보이도록 프로필 카드 바로 아래 ----
        self.section_labels = []
        col.addWidget(self._section('계정'))
        self.logout_button = ChevronButton('로그아웃')
        self.logout_button.setToolTip('로그아웃하고 로그인 화면으로 돌아갑니다')
        col.addWidget(SettingRow('현재 계정', self.logout_button, '로그아웃하면 저장된 로그인·자동 로그인도 해제'))
        self.password_button = ChevronButton('비밀번호 변경')
        col.addWidget(SettingRow('비밀번호', self.password_button, '로그인 비밀번호를 바꿉니다 (8자 이상)'))
        self.admin_button = ChevronButton('계정 관리')
        self.admin_button.setToolTip('관리자: 계정 비밀번호 재설정 · 사용 중지 · 관리자 지정 · 전체 공지 · 파일 전송 보안')
        self.admin_row = SettingRow('관리자', self.admin_button, '계정 관리 · 전체 공지 · 파일 전송 보안 (관리자에게만 보입니다)')
        col.addWidget(self.admin_row)

        # ---- 설정 ----
        col.addWidget(self._section('상태 · 화면'))
        self.presence_button = PresenceButton(state)
        col.addWidget(SettingRow('내 상태', self.presence_button, '10분간 마우스 움직임이 없으면 자리비움, 움직이면 온라인'))
        self.dark_toggle = ToggleSwitch(bool(settings.get('dark_mode')))
        col.addWidget(SettingRow('다크 모드', self.dark_toggle))
        self.font_combo = ArrowComboBox()
        for label, value in FONT_SCALES:
            self.font_combo.addItem(label, value)
        self.font_combo.setMinimumWidth(theme.px(96))
        col.addWidget(SettingRow('글자 크기', self.font_combo))
        self.motion_toggle = ToggleSwitch(bool(settings.get('reduce_motion')))
        col.addWidget(SettingRow('동작 줄이기', self.motion_toggle, '녹음 표시 깜빡임 · 진행 막대 움직임 멈추기'))
        col.addWidget(self._section('알림 · 채팅'))
        self.notify_toggle = ToggleSwitch(bool(settings.get('notifications')))
        col.addWidget(SettingRow('새 메시지 알림', self.notify_toggle, '데스크톱 알림 표시'))
        self.blink_toggle = ToggleSwitch(bool(settings.get('blink_unread')))
        col.addWidget(SettingRow('안 읽은 메시지 아이콘 깜박임', self.blink_toggle, '다 읽을 때까지 트레이·Dock 아이콘 깜박임'))
        self.record_toggle = ToggleSwitch(bool(settings.get('call_auto_record')))
        self.record_row = SettingRow('보이스톡 자동 녹음', self.record_toggle,
                                     '통화 녹음 파일은 recordings 폴더에 저장됩니다')
        self.record_row.description.setToolTip('통화 녹음 파일은 recordings 폴더에 저장됩니다')
        col.addWidget(self.record_row)
        self.rag_button = ChevronButton('관리')
        col.addWidget(SettingRow('AI 지식베이스', self.rag_button, 'AI 비서가 참고할 문서'))

        # ---- 프로필·친구 ----
        col.addWidget(self._section('프로필 · 친구'))
        self.multi_profile_button = ChevronButton('관리')
        col.addWidget(SettingRow('멀티프로필', self.multi_profile_button, '친구마다 다른 프로필 보여주기'))
        self.friend_manage_button = ChevronButton('관리')
        col.addWidget(SettingRow('친구 관리', self.friend_manage_button, '숨긴 친구 · 차단한 친구'))
        self.social_toggle = ToggleSwitch(bool(settings.get('social_features')))
        col.addWidget(SettingRow('소식 기능', self.social_toggle, '펑 · 생일인 친구 · 선물하기 보이기'))
        self.social_toggle.toggled.connect(lambda on: settings.set('social_features', bool(on)))

        # ---- 알림·꾸미기 ----
        col.addWidget(self._section('방해금지 · 키워드 · 꾸미기'))
        self.dnd_button = ChevronButton('설정')
        self.dnd_row = SettingRow('방해금지', self.dnd_button, '')
        col.addWidget(self.dnd_row)
        self.keyword_button = ChevronButton('설정')
        self.keyword_row = SettingRow('키워드 알림', self.keyword_button, '')
        col.addWidget(self.keyword_row)
        self.bubble_button = ChevronButton('변경')
        self.bubble_row = SettingRow('말풍선 꾸미기', self.bubble_button, '')
        col.addWidget(self.bubble_row)
        self.wallpaper_button = ChevronButton('변경')
        col.addWidget(SettingRow('채팅방 배경화면', self.wallpaper_button))

        # ---- 보안·도움말 ----
        col.addWidget(self._section('보안 · 도움말'))
        self.lock_button = ChevronButton('설정')
        self.lock_row = SettingRow('잠금모드', self.lock_button, '')
        col.addWidget(self.lock_row)
        self.tour_button = ChevronButton('보기')
        col.addWidget(SettingRow('기능 둘러보기', self.tour_button, 'KCS Talk 기능을 하나씩 해 보기'))

        # ---- 정보 ----
        col.addWidget(self._section('정보'))
        self.company_logo = QLabel()
        self.company_logo.setAlignment(Qt.AlignCenter)
        self.company_logo.setContentsMargins(20, 10, 20, 6)
        col.addWidget(self.company_logo)
        self.version_value = QLabel(APP_VERSION)
        col.addWidget(SettingRow('KCS Talk', self.version_value, '버전'))
        self.server_value = QLabel(server_address or '-')
        col.addWidget(SettingRow('서버 주소', self.server_value, '지금 연결된 서버'))
        col.addWidget(self._server_editor())
        self.info_section = self.section_labels[-1]
        col.addStretch(1)
        self._group_rows(col)
        self.info_group = self.groups[-1]       # 정보 묶음 카드 - 그룹 관리자에게만 보인다

        self.edit_button.clicked.connect(self.edit_profile)
        self.logout_button.clicked.connect(self._confirm_logout)
        self.admin_button.clicked.connect(lambda: self.open_admin())
        self.password_button.clicked.connect(lambda: self.open_password_change())
        state.users_changed.connect(self._refresh_admin_row)
        state.welcomed.connect(self._refresh_admin_row)
        self.server_save_button.clicked.connect(self.save_server_address)
        self.server_host_edit.returnPressed.connect(self.save_server_address)
        self.server_port_edit.returnPressed.connect(self.save_server_address)
        self._refresh_admin_row()
        self.avatar.clicked.connect(self.edit_profile)
        self.dark_toggle.toggled.connect(lambda on: settings.set('dark_mode', bool(on)))
        self.notify_toggle.toggled.connect(lambda on: settings.set('notifications', bool(on)))
        self.blink_toggle.toggled.connect(lambda on: settings.set('blink_unread', bool(on)))
        self.motion_toggle.toggled.connect(self._motion_changed)
        self.record_toggle.toggled.connect(lambda on: settings.set('call_auto_record', bool(on)))
        self.font_combo.currentIndexChanged.connect(self._font_changed)
        self.wallpaper_button.clicked.connect(self.open_wallpaper)
        self.rag_button.clicked.connect(self.open_rag_docs)
        self.multi_profile_button.clicked.connect(self.open_multi_profile)
        self.friend_manage_button.clicked.connect(self.open_friend_manage)
        self.keyword_button.clicked.connect(self.open_keywords)
        self.dnd_button.clicked.connect(self.open_dnd)
        self.bubble_button.clicked.connect(self.open_bubble_style)
        self.lock_button.clicked.connect(self.open_lock_settings)
        self.tour_button.clicked.connect(self.open_feature_tour)
        settings.changed.connect(self._sync_controls)
        state.user_changed.connect(lambda name: self.refresh_profile() if name == state.me else None)
        state.users_changed.connect(self.refresh_profile)
        connect_avatar_changed(state, self.refresh_profile)
        self._sync_controls()
        self.apply_theme()

    def _motion_changed(self, on):
        self.settings.set('reduce_motion', bool(on))
        theme.set_reduce_motion(bool(on))

    def _group_rows(self, col):
        """섹션 제목 아래 줄들을 둥근 묶음(카드 1장)으로 감싼다 - 묶음 안 촘촘, 묶음 사이 넉넉"""
        items = [col.takeAt(0) for _ in range(col.count())]
        self.groups = []
        group = None
        for item in items:
            w = item.widget()
            if w is not None and w in self.section_labels:
                col.addWidget(w)
                frame = QFrame()
                frame.setObjectName('settingsGroup')
                g = QVBoxLayout(frame)
                g.setContentsMargins(0, theme.sp('xs'), 0, theme.sp('xs'))
                g.setSpacing(0)
                wrap = QHBoxLayout()
                wrap.setContentsMargins(theme.GUTTER, 0, theme.GUTTER, 0)
                wrap.addWidget(frame)
                col.addLayout(wrap)
                group = g
                self.groups.append(frame)
            elif w is not None and group is not None:
                if group.count():
                    line = QFrame()
                    line.setObjectName('settingsDivider')
                    line.setFixedHeight(1)
                    group.addWidget(line)
                    if w is getattr(self, 'admin_row', None):   # 관리자 줄과 함께 숨긴다
                        self._admin_divider = line
                group.addWidget(w)
            elif w is not None:
                col.addWidget(w)
            elif item.layout() is not None:
                col.addLayout(item.layout())
            else:
                col.addItem(item)

    def _confirm_logout(self):
        from client.kakao import login
        from client.kakao.logout_dialog import LogoutDialog
        dlg = LogoutDialog(self.state, login.load_saved_login(), self.window())
        if dlg.exec_() == LogoutDialog.Accepted:
            self.logout_requested.emit()

    def _refresh_admin_row(self):
        """관리자에게만 '관리자' 줄을 보인다 (권한은 서버가 요청마다 다시 검사한다 - 명세 §4.2).
        정보 구역(로고 · 버전 · 서버 주소)은 그룹 관리자에게만 보인다 (명세 §4.3)"""
        visible = self.state.is_admin()
        self.admin_row.setVisible(visible)
        divider = getattr(self, '_admin_divider', None)
        if divider is not None:
            divider.setVisible(visible)
        info = self.can_edit_server()
        self.info_section.setVisible(info)
        self.info_group.setVisible(info)

    # ---- 서버 주소 (그룹 관리자만, 이 PC 의 server.json - 다음 로그인부터) ----
    def can_edit_server(self):
        check = getattr(self.state, 'is_group_admin', None)
        return bool(check and check())

    def _server_editor(self):
        """[서버 주소] [포트] [저장] + 결과 안내 한 줄"""
        from client.kakao.settings import load_server_address, DEFAULT_HOST, DEFAULT_PORT
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(theme.sp('m') + 2, 0, theme.sp('m'), theme.sp('s') + 2)
        col.setSpacing(theme.sp('xs'))
        row = QHBoxLayout()
        row.setSpacing(theme.sp('s'))
        current = load_server_address()
        if current is None and ':' in (self.server_address or ''):
            host, _, port = self.server_address.rpartition(':')
            current = (host, port)
        host, port = current or (DEFAULT_HOST, DEFAULT_PORT)
        self.server_host_edit = QLineEdit(str(host))
        self.server_host_edit.setPlaceholderText('서버 주소 (예: 192.168.0.10)')
        self.server_host_edit.setAccessibleName('서버 주소')
        self.server_host_edit.setMaxLength(253)
        self.server_port_edit = QLineEdit(str(port))
        self.server_port_edit.setPlaceholderText('포트')
        self.server_port_edit.setAccessibleName('포트')
        self.server_port_edit.setMaxLength(5)
        self.server_port_edit.setMaximumWidth(theme.px(72))
        self.server_save_button = QPushButton('저장')
        self.server_save_button.setCursor(Qt.PointingHandCursor)
        self.server_save_button.setAccessibleName('서버 주소 저장')
        row.addWidget(self.server_host_edit, 1)
        row.addWidget(self.server_port_edit)
        row.addWidget(self.server_save_button)
        col.addLayout(row)
        self.server_status = QLabel('바꾸면 이 PC 에서 다음 로그인부터 이 주소로 연결합니다.')
        self.server_status.setWordWrap(True)
        self.server_status.setObjectName('serverStatus')
        col.addWidget(self.server_status)
        return box

    def save_server_address(self):
        """그룹 관리자만 저장. 성공하면 True (안내 줄에 결과)"""
        from client.kakao.settings import save_server_address
        if not self.can_edit_server():
            self._server_message('그룹 관리자만 서버 주소를 바꿀 수 있습니다.', error=True)
            return False
        try:
            host, port = save_server_address(self.server_host_edit.text(), self.server_port_edit.text())
        except ValueError as exc:
            self._server_message(str(exc), error=True)
            return False
        except OSError as exc:
            self._server_message(f'저장하지 못했습니다: {exc}', error=True)
            return False
        self.server_host_edit.setText(host)
        self.server_port_edit.setText(str(port))
        self._server_message(f'저장했습니다. 다음 로그인부터 {host}:{port} 로 연결합니다.')
        return True

    def _server_message(self, text, error=False):
        self.server_status.setText(text)
        self.server_status.setProperty('error', bool(error))
        self.server_status.setStyleSheet(
            f"color: {theme.c('danger_text') if error else theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")

    def open_admin(self, exec_=True):
        from client.kakao.admin import AdminDialog
        dlg = AdminDialog(self.state, self.window())
        self.admin_dialog = dlg
        if exec_:
            exec_and_release(dlg)
        return dlg

    def open_password_change(self, exec_=True):
        dlg = PasswordChangeDialog(self.state, self.window())
        self.password_dialog = dlg
        if exec_:
            exec_and_release(dlg)
        return dlg

    def _section(self, title):
        label = QLabel(title)
        self.section_labels.append(label)
        return label

    # ---- 동작 ----
    def _font_changed(self, index):
        value = self.font_combo.itemData(index)
        if value is not None and abs(float(self.settings.get('font_scale') or 1.0) - value) > 1e-6:
            self.settings.set('font_scale', value)

    def set_font_scale_index(self, index):
        self.font_combo.setCurrentIndex(index)

    def _sync_controls(self, _key=None):
        if _key == 'reduce_motion':      # 기능 둘러보기 등 바깥에서 바꿔도 테마 동작에 반영
            theme.set_reduce_motion(bool(self.settings.get('reduce_motion')))
        for widget, value in ((self.dark_toggle, bool(self.settings.get('dark_mode'))),
                              (self.notify_toggle, bool(self.settings.get('notifications'))),
                              (self.blink_toggle, bool(self.settings.get('blink_unread'))),
                              (self.record_toggle, bool(self.settings.get('call_auto_record'))),
                              (self.motion_toggle, bool(self.settings.get('reduce_motion'))),
                              (self.social_toggle, bool(self.settings.get('social_features')))):
            if widget.isChecked() != value:
                widget.blockSignals(True)
                widget.setChecked(value)
                widget._set_offset(1.0 if value else 0.0)
                widget.blockSignals(False)
        scale = float(self.settings.get('font_scale') or 1.0)
        best = min(range(len(FONT_SCALES)), key=lambda i: abs(FONT_SCALES[i][1] - scale))
        if self.font_combo.currentIndex() != best:
            self.font_combo.blockSignals(True)
            self.font_combo.setCurrentIndex(best)
            self.font_combo.blockSignals(False)
        self._sync_descriptions()

    def _sync_descriptions(self):
        """설정 값 요약을 각 행 설명에 반영"""
        from client.kakao import lock
        words = normalize_keywords(self.settings.get('alert_keywords') or [])
        keyword_text = (', '.join(words[:3]) + (f' 외 {len(words) - 3}개' if len(words) > 3 else '')
                                             if words else '등록된 키워드 없음')
        style = self.settings.get('bubble_style') or 'basic'
        if isinstance(style, dict):
            style = style.get('preset') or 'basic'
        bubble_label = theme.BUBBLE_STYLES.get(style, theme.BUBBLE_STYLES['basic'])['label']
        lock_text = (f'사용 중 · {lock.lock_minutes(self.settings)}분 뒤 자동 잠금' if lock.has_pin(self.settings)
                     else '사용 안 함')
        from client.kakao import dnd
        for row, text in ((self.keyword_row, keyword_text), (self.dnd_row, dnd.status_text(self.settings)),
                          (self.bubble_row, bubble_label), (self.lock_row, lock_text)):
            row.set_description(text)

    def refresh_profile(self):
        state = self.state
        if not state.me:
            return
        me = state.user(state.me)
        self.header.set_subtitle(' · '.join(x for x in ('KCS Talk 2.0', me.get('company')) if x))
        self.avatar.setPixmap(user_pixmap(state, state.me, theme.px(theme.AVATAR_ROW)))
        self.name_label.setText(state.display_name(state.me))
        self.status_label.setText(me.get('status_message') or '상태메시지를 입력해 주세요')
        self.status_label.set_color_key('text_sub' if me.get('status_message') else 'text_hint')
        org = self.state.org_text(self.state.me)
        self.org_label.setText(org)
        self.org_label.setVisible(bool(org))
        self.presence_button.refresh()

    def edit_profile(self):
        exec_and_release(ProfileEditDialog(self.state, self.window()))
    def open_wallpaper(self):
        try:
            from client.kakao.wallpaper import WallpaperDialog
        except ImportError:
            QMessageBox.information(self, '배경화면', '배경화면 모듈을 불러올 수 없습니다.')
            return
        exec_and_release(WallpaperDialog(self.settings, parent=self.window()))
    def open_rag_docs(self):
        exec_and_release(RagDocsDialog(self.state, self.window()))
    def open_multi_profile(self):
        exec_and_release(MultiProfileDialog(self.state, self.window()))
    def open_friend_manage(self):
        exec_and_release(FriendManageDialog(self.state, self.window()))
    def open_keywords(self):
        exec_and_release(KeywordAlertDialog(self.settings, self.window()))
    def open_dnd(self):
        from client.kakao.dnd import DndDialog
        exec_and_release(DndDialog(self.state, self.settings, self.window()))
    def open_bubble_style(self):
        exec_and_release(BubbleStyleDialog(self.settings, self.window()))
    def open_lock_settings(self):
        exec_and_release(LockSettingsDialog(self.state, self.settings, self.window()))
    def open_feature_tour(self):
        win = self.window()
        if win is not self and hasattr(win, 'open_feature_tour'):
            return win.open_feature_tour()
        QMessageBox.information(self, '기능 둘러보기', '메인 창에서만 기능 둘러보기를 열 수 있습니다.')
        return None

    def apply_theme(self):
        from client.kakao import brand
        # 다크 모드에서는 네이비 로고가 안 보이므로 밝은 단색으로
        self.company_logo.setPixmap(brand.logo_pixmap('horizontal', 30, '#C9D2F2' if theme.is_dark() else None))
        self.company_logo.setToolTip(brand.COMPANY_NAME)
        self.header.apply_theme()
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"MoreTab, QWidget#moreBody, QScrollArea {{ background: {theme.c('bg')}; }}"
            f" QFrame#profileCard {{ background: {theme.c('bg_alt')}; border-radius: 14px; }}"
            f" QComboBox {{ padding: 5px 10px; border-radius: 6px; }}"
            f" QComboBox::drop-down {{ border: none; width: 22px; }}"
            f" QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}")
        for label in self.section_labels:
            label.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold;"
                                f" padding: {theme.sp('xl')}px {theme.GUTTER + 12}px {theme.sp('s')}px "
                                f"{theme.GUTTER + 12}px;")
        for frame in getattr(self, 'groups', []):
            frame.setStyleSheet(
                f"QFrame#settingsGroup {{ background: {theme.c('bg_alt')}; border-radius: 12px; }}"
                f" QFrame#settingsDivider {{ background: {theme.c('border')}; margin: 0 {theme.sp('m')}px; }}")
        for value in (self.version_value, self.server_value):
            value.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('body')}px;")
        self._server_message(self.server_status.text(), error=bool(self.server_status.property('error')))
        for btn in (self.wallpaper_button, self.rag_button, self.multi_profile_button, self.friend_manage_button,
                    self.keyword_button, self.dnd_button, self.bubble_button, self.lock_button, self.tour_button):
            btn.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('body')}px;")
        for w in self.findChildren(ElidedLabel):
            w.refresh_theme()
        self.font_combo.setMinimumWidth(theme.px(96))
        self.dark_toggle.update()
        self.notify_toggle.update()
        self.blink_toggle.update()
        self.record_toggle.update()
        self.refresh_profile()


# ================================================================ v3: 멀티프로필 (§8.5)
class MultiProfileDialog(QDialog):
    """멀티프로필 - 친구마다 다른 이름/상태메시지/사진을 보여준다. 저장 시 전체 교체(multi_profile_set)"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.profiles = []        # 편집 중인 사본 (원본 키 + '_avatar_b64' / '_avatar_bytes' / 'avatar_clear')
        self.current = -1
        self._loading = False
        self._dirty = False
        self.setWindowTitle('멀티프로필')
        self.setMinimumSize(560, 470)
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel('멀티프로필')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel('지정한 친구에게만 기본 프로필 대신 이 프로필이 보여요.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(6)
        self.list = QListWidget()
        self.list.setFixedWidth(170)
        self.list.setIconSize(QSize(30, 30))
        self.list.setStyleSheet(f"QListWidget {{ border: 1px solid {theme.c('border')}; border-radius: 8px; }}"
                                f" QListWidget::item {{ padding: 6px; }}")
        left.addWidget(self.list, 1)
        list_buttons = QHBoxLayout()
        self.add_button = QPushButton('추가')
        self.delete_button = QPushButton('삭제')
        list_buttons.addWidget(self.add_button)
        list_buttons.addWidget(self.delete_button)
        left.addLayout(list_buttons)
        body.addLayout(left)

        from PyQt5.QtWidgets import QFormLayout, QLineEdit
        self.editor = QWidget()
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setSpacing(8)
        photo_row = QHBoxLayout()
        self.avatar = Avatar(size=64)
        photo_row.addWidget(self.avatar)
        self.photo_button = QPushButton('사진 선택')
        self.clear_photo_button = QPushButton('기본 이미지')
        self.clear_photo_button.setToolTip('회사 조직도 사진으로 되돌립니다 (조직도 사진이 없으면 사진 없음)')
        photo_row.addWidget(self.photo_button)
        photo_row.addWidget(self.clear_photo_button)
        photo_row.addStretch(1)
        editor.addLayout(photo_row)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_edit = QLineEdit()
        if not state.display_name_editable():      # 이름을 실명으로 고정한 서버: 멀티프로필 이름도 실명 그대로
            self.name_edit.setReadOnly(True)
            self.name_edit.setToolTip('이름은 조직도에 등록된 이름으로 고정되어 바꿀 수 없습니다.')
        self.name_edit.setMaxLength(20)
        self.name_edit.setPlaceholderText('예: 회사용, 동호회')
        self.display_edit = QLineEdit()
        self.display_edit.setMaxLength(20)
        self.status_edit = QLineEdit()
        self.status_edit.setMaxLength(60)
        self.status_edit.setPlaceholderText('상태메시지')
        form.addRow('프로필 이름', self.name_edit)
        form.addRow('표시 이름', self.display_edit)
        form.addRow('상태메시지', self.status_edit)
        editor.addLayout(form)
        viewers_title = QLabel('이 프로필을 볼 친구')
        viewers_title.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        editor.addWidget(viewers_title)
        self.viewers_list = QListWidget()
        self.viewers_list.setStyleSheet(f"QListWidget {{ border: 1px solid {theme.c('border')}; border-radius: 8px; }}"
                                        f" QListWidget::item {{ padding: 4px; }}")
        editor.addWidget(self.viewers_list, 1)
        body.addWidget(self.editor, 1)
        self.empty_label = QLabel('멀티프로필이 없습니다.\n[추가]를 눌러 만들어 보세요.')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {theme.c('text_hint')};")
        body.addWidget(self.empty_label, 1)
        layout.addLayout(body, 1)

        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.c('badge')}; font-size: {theme.fs('meta')}px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        self.save_button = QPushButton('저장')
        self.save_button.setProperty('primary', True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self._fill_viewers()
        cancel.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        self.add_button.clicked.connect(lambda: self.add_profile())
        self.delete_button.clicked.connect(lambda: self.delete_profile(self.current))
        self.photo_button.clicked.connect(self.choose_photo)
        self.clear_photo_button.clicked.connect(self.clear_photo)
        self.list.currentRowChanged.connect(self.select)
        for edit in (self.name_edit, self.display_edit, self.status_edit):
            edit.textEdited.connect(lambda _t: self._write_back())
        self.viewers_list.itemChanged.connect(lambda _i: self._write_back())
        state.multi_profiles_changed.connect(self._on_loaded)
        state.attachment_loaded.connect(self._on_attachment_loaded)
        self.set_profiles(getattr(state, 'multi_profiles', []) or [])
        self._dirty = False
        state.request_multi_profiles()

    def _on_attachment_loaded(self, *_args):
        self._refresh_avatar()

    def done(self, result):
        # 창은 닫혀도 지워지지 않는다 - 끊지 않으면 열 때마다 숨은 창이 쌓여 첨부가 올 때마다 모두 다시 그렸다
        disconnect_signals((self.state.multi_profiles_changed, self._on_loaded),
                           (self.state.attachment_loaded, self._on_attachment_loaded))
        super().done(result)

    # ---- 데이터 ----
    def _fill_viewers(self):
        from PyQt5.QtGui import QIcon
        self.viewers_list.clear()
        for user in self.state.friends(include_hidden=True):
            name = user['username']
            item = QListWidgetItem(QIcon(user_pixmap(self.state, name, 22)), self.state.display_name(name))
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.viewers_list.addItem(item)

    def _on_loaded(self, profiles):
        if not self._dirty:   # 편집 중에 들어온 응답으로 작업을 덮어쓰지 않는다
            self.set_profiles(profiles)
            self._dirty = False

    def set_profiles(self, profiles):
        self.profiles = [dict(p, viewers=list(p.get('viewers') or [])) for p in profiles]
        self._rebuild_list(0 if self.profiles else -1)

    def _rebuild_list(self, select_row):
        self._loading = True
        self.list.clear()
        for p in self.profiles:
            self.list.addItem(QListWidgetItem(self._profile_icon(p), p.get('name') or '이름 없음'))
        self._loading = False
        self.current = -1
        if select_row >= 0 and self.profiles:
            self.list.setCurrentRow(min(select_row, len(self.profiles) - 1))
            self.select(self.list.currentRow())
        self._update_visibility()

    def _profile_icon(self, profile):
        from PyQt5.QtGui import QIcon
        return QIcon(self._profile_pixmap(profile, 30))

    def _profile_pixmap(self, profile, size):
        from client.kakao.widgets import photo_avatar_pixmap, avatar_pixmap
        data = profile.get('_avatar_bytes')
        if data is None and not profile.get('avatar_clear') and profile.get('avatar_id'):
            cached = self.state.attachments.get(profile['avatar_id'])
            if cached:
                data = cached[0]
            else:
                self.state.request_attachment(profile['avatar_id'])
        if data:
            pm = photo_avatar_pixmap(data, size)
            if pm is not None and not pm.isNull():
                return pm
        name = profile.get('display_name') or self.state.display_name(self.state.me)
        return avatar_pixmap(self.state.me + '#' + (profile.get('name') or ''), name, size)

    def _update_visibility(self):
        has = bool(self.profiles)
        self.editor.setVisible(has)
        self.empty_label.setVisible(not has)
        self.delete_button.setEnabled(has and self.current >= 0)

    def select(self, row):
        if self._loading or row < 0 or row >= len(self.profiles):
            return
        self.current = row
        p = self.profiles[row]
        self._loading = True
        self.name_edit.setText(p.get('name') or '')
        self.display_edit.setText(p.get('display_name') or '')
        self.status_edit.setText(p.get('status_message') or '')
        viewers = set(p.get('viewers') or [])
        for i in range(self.viewers_list.count()):
            item = self.viewers_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in viewers else Qt.Unchecked)
        self._loading = False
        self._refresh_avatar()
        self._update_visibility()

    def _write_back(self):
        if self._loading or not (0 <= self.current < len(self.profiles)):
            return
        p = self.profiles[self.current]
        p['name'] = self.name_edit.text().strip()
        p['display_name'] = self.display_edit.text().strip()
        p['status_message'] = self.status_edit.text().strip()
        p['viewers'] = [self.viewers_list.item(i).data(Qt.UserRole) for i in range(self.viewers_list.count())
                        if self.viewers_list.item(i).checkState() == Qt.Checked]
        self._dirty = True
        item = self.list.item(self.current)
        if item is not None:
            item.setText(p['name'] or '이름 없음')

    def _refresh_avatar(self):
        if 0 <= self.current < len(self.profiles):
            p = self.profiles[self.current]
            self.avatar.setPixmap(self._profile_pixmap(p, 64))
            item = self.list.item(self.current)
            if item is not None:
                item.setIcon(self._profile_icon(p))

    # ---- 테스트 가능한 API ----
    def add_profile(self, name=''):
        me = self.state.user(self.state.me)
        n = len(self.profiles) + 1
        self.profiles.append({'name': name or f'멀티프로필 {n}',
                              'display_name': me.get('display_name') or self.state.me,
                              'status_message': '', 'viewers': []})
        self._dirty = True
        self._rebuild_list(len(self.profiles) - 1)
        return self.profiles[-1]

    def delete_profile(self, row):
        if 0 <= row < len(self.profiles):
            self.profiles.pop(row)
            self._dirty = True
            self._rebuild_list(min(row, len(self.profiles) - 1))

    def set_viewers(self, usernames):
        wanted = set(usernames)
        self._loading = True
        for i in range(self.viewers_list.count()):
            item = self.viewers_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in wanted else Qt.Unchecked)
        self._loading = False
        self._write_back()

    def set_fields(self, name=None, display_name=None, status_message=None):
        for edit, value in ((self.name_edit, name), (self.display_edit, display_name),
                            (self.status_edit, status_message)):
            if value is not None:
                edit.setText(value)
        self._write_back()

    def choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, '프로필 사진 선택', '',
                                              '이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)')
        if path:
            self.set_photo_from_path(path)

    def set_photo_from_path(self, path):
        """정사각형 256px PNG로 잘라 현재 프로필에 지정. 오류 문자열 또는 None"""
        import base64
        from client.kakao.friends_tab import avatar_png_from_path
        if not (0 <= self.current < len(self.profiles)):
            return self._show_error('먼저 프로필을 추가해 주세요.')
        data, err = avatar_png_from_path(path)
        if err:
            return self._show_error(err)
        p = self.profiles[self.current]
        p['_avatar_bytes'] = data
        p['_avatar_b64'] = base64.b64encode(data).decode('ascii')
        p.pop('avatar_clear', None)
        self._dirty = True
        self._show_error('')
        self._refresh_avatar()
        return None

    def clear_photo(self):
        if 0 <= self.current < len(self.profiles):
            p = self.profiles[self.current]
            p.pop('_avatar_bytes', None)
            p.pop('_avatar_b64', None)
            p['avatar_clear'] = True
            self._dirty = True
            self._refresh_avatar()

    def payload(self):
        """multi_profile_set 으로 보낼 목록"""
        out = []
        for p in self.profiles:
            item = {'name': p.get('name') or '', 'display_name': p.get('display_name') or '',
                    'status_message': p.get('status_message') or '', 'viewers': list(p.get('viewers') or [])}
            if p.get('profile_id') is not None:
                item['profile_id'] = p['profile_id']
            if p.get('_avatar_b64'):
                item['avatar_b64'] = p['_avatar_b64']
            elif p.get('avatar_clear'):
                item['avatar_clear'] = True
            out.append(item)
        return out

    def validate(self):
        names = []
        for i, p in enumerate(self.profiles):
            if not (p.get('name') or '').strip():
                return i, '프로필 이름을 입력해 주세요.'
            if not (p.get('display_name') or '').strip():
                return i, '표시 이름을 입력해 주세요.'
            if p['name'].strip() in names:
                return i, f"'{p['name']}' 이름의 프로필이 이미 있어요."
            names.append(p['name'].strip())
        return -1, None

    def accept(self):
        self._write_back()
        row, err = self.validate()
        if err:
            if row >= 0:
                self.list.setCurrentRow(row)
            self._show_error(err)
            return
        self.state.set_multi_profiles(self.payload())
        self._dirty = False
        super().accept()

    def _show_error(self, text):
        self.error_label.setText(text or '')
        self.error_label.setVisible(bool(text))
        return text or None


# ================================================================ v3: 친구 관리 (숨김·차단, §8.6)
class ManagedFriendRow(QWidget):
    def __init__(self, state, username, button_text, on_click, parent=None):
        super().__init__(parent)
        self.username = username
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(10)
        avatar = Avatar(size=36)
        avatar.setPixmap(user_pixmap(state, username, 36))
        row.addWidget(avatar)
        row.addWidget(ElidedLabel(state.display_name(username), 'emphasis', 'text'), 1)
        self.button = QPushButton(button_text)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(lambda: on_click(username))
        row.addWidget(self.button)


class FriendManageDialog(QDialog):
    """숨긴 친구 / 차단한 친구 목록과 해제"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QTabWidget
        self.state = state
        self.setWindowTitle('친구 관리')
        self.setMinimumSize(340, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        title = QLabel('친구 관리')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.hidden_list = QListWidget()
        self.blocked_list = QListWidget()
        for lst in (self.hidden_list, self.blocked_list):
            lst.setSelectionMode(QListWidget.NoSelection)
            lst.setStyleSheet(f"QListWidget {{ border: none; background: {theme.c('bg')}; }}")
        self.tabs.addTab(self._page(self.hidden_list, '숨긴 친구는 친구 목록에 보이지 않아요. 대화는 그대로 할 수 있어요.'),
                         '숨긴 친구')
        self.tabs.addTab(self._page(self.blocked_list, '차단한 친구의 메시지는 받지 않아요. 상대방은 차단 사실을 알 수 없어요.'),
                         '차단한 친구')
        layout.addWidget(self.tabs, 1)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton('닫기')
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        layout.addLayout(close_row)
        state.blocked_changed.connect(self.refresh)
        state.users_changed.connect(self.refresh)
        if state.settings is not None:
            state.settings.changed.connect(self._on_setting_changed)
        self.refresh()

    def _on_setting_changed(self, key):
        if key == 'hidden_friends':
            self.refresh()

    def done(self, result):
        disconnect_signals((self.state.blocked_changed, self.refresh), (self.state.users_changed, self.refresh))
        if self.state.settings is not None:
            disconnect_signals((self.state.settings.changed, self._on_setting_changed))
        super().done(result)

    def _page(self, lst, hint_text):
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(6)
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; padding: 0 6px;")
        col.addWidget(hint)
        col.addWidget(lst, 1)
        return page

    def hidden_names(self):
        settings = self.state.settings
        names = list(settings.get('hidden_friends') or []) if settings is not None else []
        return sorted(names, key=self.state.display_name)

    def blocked_names(self):
        return sorted(self.state.blocked, key=self.state.display_name)

    def _fill(self, lst, names, button_text, on_click, empty_text):
        lst.clear()
        if not names:
            item = QListWidgetItem(empty_text)
            item.setFlags(Qt.NoItemFlags)
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(QColor(theme.c('text_hint')))
            item.setSizeHint(QSize(0, 80))
            lst.addItem(item)
            return
        for name in names:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            widget = ManagedFriendRow(self.state, name, button_text, on_click)
            item.setSizeHint(widget.sizeHint())
            lst.addItem(item)
            lst.setItemWidget(item, widget)

    def refresh(self):
        self._fill(self.hidden_list, self.hidden_names(), '숨김 해제', self.unhide, '숨긴 친구가 없습니다')
        self._fill(self.blocked_list, self.blocked_names(), '차단 해제', self.unblock, '차단한 친구가 없습니다')
        self.tabs.setTabText(0, f'숨긴 친구 {len(self.hidden_names())}')
        self.tabs.setTabText(1, f'차단한 친구 {len(self.blocked_names())}')

    def unhide(self, username):
        self.state.set_hidden(username, False)
        self.refresh()

    def unblock(self, username):
        self.state.block_user(username, False)


# ================================================================ v3: 키워드 알림 (§8.11)
KEYWORD_MAX = 20
KEYWORD_MAX_LEN = 20


def normalize_keywords(words):
    """앞뒤 공백 제거, 빈 값·중복(대소문자 무시) 제거, 최대 20개"""
    out, seen = [], set()
    for w in words or []:
        w = str(w).strip()[:KEYWORD_MAX_LEN]
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:KEYWORD_MAX]


class KeywordChip(QFrame):
    def __init__(self, word, on_remove, parent=None):
        super().__init__(parent)
        self.word = word
        self.setObjectName('keywordChip')
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 3, 4, 3)
        row.setSpacing(2)
        label = QLabel(word)
        label.setStyleSheet(f"font-size: {theme.fs('meta')}px; background: transparent;")
        row.addWidget(label)
        self.remove_button = QPushButton()
        self.remove_button.setIcon(make_icon('close', theme.c('text_sub'), 12))
        self.remove_button.setAccessibleName('키워드 삭제')
        self.remove_button.setCursor(Qt.PointingHandCursor)
        self.remove_button.setToolTip(f"'{word}' 삭제")
        self.remove_button.setFixedSize(20, 20)
        self.remove_button.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {theme.c('text_sub')};"
            f" font-size: {theme.fs('caption')}px; padding: 0; }} QPushButton:hover {{ color: {theme.c('badge')}; }}")
        self.remove_button.clicked.connect(lambda: on_remove(word))
        row.addWidget(self.remove_button)
        self.setStyleSheet(f"QFrame#keywordChip {{ background: {theme.c('bg_alt')};"
                           f" border: 1px solid {theme.c('border')}; border-radius: 13px; }}")


class KeywordAlertDialog(QDialog):
    """알림 키워드 - 알림을 끈 방이어도 키워드가 들어간 메시지는 알려 준다"""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QLineEdit
        self.settings = settings
        self.setWindowTitle('키워드 알림')
        self.setMinimumSize(360, 340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel('키워드 알림')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel(f'알림을 꺼 둔 채팅방이라도 키워드가 들어간 메시지는 알림을 받고 말풍선이 강조돼요.'
                      f' (최대 {KEYWORD_MAX}개)')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setMaxLength(KEYWORD_MAX_LEN)
        self.edit.setPlaceholderText('키워드 입력 (예: 긴급, 회의)')
        self.add_button = QPushButton('추가')
        self.add_button.setProperty('primary', True)
        row.addWidget(self.edit, 1)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self.error_label = QLabel('')
        self.error_label.setStyleSheet(f"color: {theme.c('badge')}; font-size: {theme.fs('meta')}px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.count_label = QLabel('')
        self.count_label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(self.count_label)
        self.flow = FlowWidget()
        layout.addWidget(self.flow)
        self.empty_label = QLabel('등록된 키워드가 없습니다')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {theme.c('text_hint')}; padding: 20px;")
        layout.addWidget(self.empty_label)
        layout.addStretch(1)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton('닫기')
        close_row.addWidget(close)
        layout.addLayout(close_row)
        close.clicked.connect(self.accept)
        self.add_button.clicked.connect(self._add_from_edit)
        self.edit.returnPressed.connect(self._add_from_edit)
        self.refresh()

    def keywords(self):
        return normalize_keywords(self.settings.get('alert_keywords') or [])

    def _add_from_edit(self):
        if self.add_keyword(self.edit.text()) is None:
            self.edit.clear()

    def add_keyword(self, text):
        """키워드 추가 후 즉시 저장. 오류 문자열 또는 None"""
        word = str(text or '').strip()
        words = self.keywords()
        if not word:
            return self._show_error('키워드를 입력해 주세요.')
        if len(word) > KEYWORD_MAX_LEN:
            return self._show_error(f'키워드는 {KEYWORD_MAX_LEN}자까지 입력할 수 있어요.')
        if word.lower() in (w.lower() for w in words):
            return self._show_error('이미 등록된 키워드예요.')
        if len(words) >= KEYWORD_MAX:
            return self._show_error(f'키워드는 최대 {KEYWORD_MAX}개까지 등록할 수 있어요.')
        self.settings.set('alert_keywords', normalize_keywords(words + [word]))
        self._show_error('')
        self.refresh()
        return None

    def remove_keyword(self, word):
        self.settings.set('alert_keywords', [w for w in self.keywords() if w != word])
        self.refresh()

    def chip_words(self):
        return [w.word for w in self.flow.items]

    def refresh(self):
        words = self.keywords()
        self.flow.set_widgets([KeywordChip(w, self.remove_keyword) for w in words])
        self.flow.setVisible(bool(words))
        self.empty_label.setVisible(not words)
        self.count_label.setText(f'{len(words)}/{KEYWORD_MAX}')
        self.add_button.setEnabled(len(words) < KEYWORD_MAX)

    def _show_error(self, text):
        self.error_label.setText(text or '')
        self.error_label.setVisible(bool(text))
        return text or None


# ================================================================ v3: 말풍선 꾸미기 (§8.11)
class BubblePreview(QAbstractButton):
    """프리셋 카드 - 채팅방 배경 위 상대/내 말풍선 미리보기 + 이름"""

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(176, 140)
        self.setToolTip(theme.BUBBLE_STYLES[key]['label'])

    def paintEvent(self, event):
        from PyQt5.QtGui import QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        outer = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(theme.c('yellow')), 2.4) if self.isChecked() else QPen(QColor(theme.c('border')), 1))
        p.setBrush(QColor(theme.c('chat_bg')))
        p.drawRoundedRect(outer, 12, 12)
        st = theme.bubble_style(self.key)
        r = st['radius']
        f = theme.font('meta', scaled=False)   # 고정 크기 미리보기 카드 안
        p.setFont(f)
        # 상대 말풍선
        other = QRectF(outer.left() + 10, outer.top() + 10, 92, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.c('bubble_other')))
        p.drawRoundedRect(other, 13, 13)
        p.setPen(QColor(theme.c('bubble_text')))
        p.drawText(other, Qt.AlignCenter, '안녕하세요!')
        # 내 말풍선
        # 내 말풍선 - 두 줄 높이로 그려 모서리 차이가 보이게
        mine = QRectF(outer.right() - 10 - 104, outer.top() + 46, 104, 44)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(st['bg']))
        p.drawRoundedRect(mine, min(r, 22), min(r, 22))
        p.setPen(QColor(st['fg']))
        p.drawText(mine, Qt.AlignCenter, '반가워요 😀\n잘 지냈어요?')
        # 이름
        fb = theme.font('meta', bold=self.isChecked(), scaled=False)
        p.setFont(fb)
        label_rect = QRectF(outer.left(), outer.bottom() - 32, outer.width(), 28)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.c('bg')))
        path_rect = QRectF(outer.left() + 1, outer.bottom() - 34, outer.width() - 2, 33)
        p.drawRoundedRect(path_rect, 11, 11)
        p.drawRect(QRectF(path_rect.left(), path_rect.top(), path_rect.width(), 12))
        p.setPen(QColor(theme.c('text')))
        p.drawText(label_rect, Qt.AlignCenter, ('✓ ' if self.isChecked() else '') + st['label'])


class BubbleStyleDialog(QDialog):
    """말풍선 꾸미기 - 프리셋 격자, 저장 시 settings['bubble_style']"""

    COLUMNS = 3

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QGridLayout
        self.settings = settings
        self.setWindowTitle('말풍선 꾸미기')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel('말풍선 꾸미기')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel('내가 보낸 말풍선의 색과 모양을 골라 보세요.')
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards = {}
        for i, key in enumerate(theme.BUBBLE_STYLES):
            card = BubblePreview(key)
            card.clicked.connect(lambda _c=False, k=key: self.select(k))
            self.cards[key] = card
            grid.addWidget(card, i // self.COLUMNS, i % self.COLUMNS)
        layout.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        self.save_button = QPushButton('저장')
        self.save_button.setProperty('primary', True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        current = settings.get('bubble_style') or 'basic'
        if isinstance(current, dict):     # 명세 표기 {preset} 형태도 허용
            current = current.get('preset') or 'basic'
        self._selected = current if current in theme.BUBBLE_STYLES else 'basic'
        self.select(self._selected)

    def select(self, key):
        if key not in theme.BUBBLE_STYLES:
            return
        self._selected = key
        for k, card in self.cards.items():
            card.setChecked(k == key)
            card.update()

    def selected(self):
        return self._selected

    def accept(self):
        if self.settings.get('bubble_style') != self._selected:
            self.settings.set('bubble_style', self._selected)
        super().accept()


# ================================================================ v3: 잠금모드 설정 (§8.11)
class PasswordChangeDialog(QDialog):
    """로그인 비밀번호 변경 (명세 §4.1 password_change). 바꾸면 다른 기기의 접속은 다시 로그인해야 한다.
    이 요청을 모르는 예전 서버에서는 안내만 한다"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QFormLayout, QLineEdit
        from client.kakao.login import NEW_PASSWORD_MIN, PASSWORD_MAX
        self.state = state
        self.min_length, self.max_length = NEW_PASSWORD_MIN, PASSWORD_MAX
        self.setWindowTitle('비밀번호 변경')
        self.setMinimumWidth(theme.px(360))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)
        title = QLabel('비밀번호 변경')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel('바꾸면 다른 PC 에 로그인되어 있던 접속은 다시 로그인해야 합니다.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(hint)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.current_edit, self.new_edit, self.confirm_edit = QLineEdit(), QLineEdit(), QLineEdit()
        for edit, placeholder in ((self.current_edit, '지금 쓰는 비밀번호'),
                                  (self.new_edit, f'{self.min_length}~{self.max_length}자'),
                                  (self.confirm_edit, '한 번 더 입력')):
            edit.setEchoMode(QLineEdit.Password)
            edit.setMaxLength(self.max_length)
            edit.setPlaceholderText(placeholder)
        form.addRow('현재 비밀번호', self.current_edit)
        form.addRow('새 비밀번호', self.new_edit)
        form.addRow('비밀번호 확인', self.confirm_edit)
        layout.addLayout(form)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        self.ok_button = QPushButton('변경')
        self.ok_button.setProperty('primary', True)
        self.ok_button.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.submit)
        state.password_changed.connect(self._on_changed)
        state.password_change_failed.connect(self._on_failed)

    def _say(self, text, error=True):
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {theme.c('danger_text') if error else theme.c('text_sub')};"
                                   f" font-size: {theme.fs('meta')}px;")

    def submit(self):
        old, new, again = self.current_edit.text(), self.new_edit.text(), self.confirm_edit.text()
        if not old:
            self.current_edit.setFocus()
            return self._say('현재 비밀번호를 입력해 주세요.')
        if not self.min_length <= len(new) <= self.max_length:
            self.new_edit.setFocus()
            return self._say(f'새 비밀번호는 {self.min_length}~{self.max_length}자로 입력해 주세요.')
        if new != again:
            self.confirm_edit.setFocus()
            return self._say('새 비밀번호가 서로 다릅니다.')
        if new == old:
            self.new_edit.setFocus()
            return self._say('새 비밀번호가 현재 비밀번호와 같습니다.')
        from client.kakao import login
        if not self.state.change_password(old, new, remember=login.load_saved_login() is not None):
            return self._say('서버에 연결되어 있지 않습니다. 연결된 뒤 다시 시도해 주세요.')
        self.ok_button.setEnabled(False)
        self._say('바꾸는 중…', error=False)

    def _on_changed(self, _p):
        self.accept()

    def _on_failed(self, message, _code):
        self.ok_button.setEnabled(True)
        if '알 수 없는 요청' in (message or ''):        # 예전 서버: 이 요청을 모른다
            message = '이 서버는 메신저에서 비밀번호 변경을 지원하지 않습니다. 관리자에게 문의해 주세요.'
        self._say(message)

    def done(self, result):
        disconnect_signals((self.state.password_changed, self._on_changed),
                           (self.state.password_change_failed, self._on_failed))
        super().done(result)


class LockSettingsDialog(QDialog):
    """잠금 비밀번호 설정/변경/해제, 자동 잠금 시간, 지금 잠그기"""

    def __init__(self, state, settings, parent=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QFormLayout, QLineEdit
        from PyQt5.QtGui import QRegularExpressionValidator
        from PyQt5.QtCore import QRegularExpression
        from client.kakao import lock
        self.state = state
        self.settings = settings
        self.setWindowTitle('잠금모드')
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)
        title = QLabel('잠금모드')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        layout.addWidget(title)
        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        validator = QRegularExpressionValidator(QRegularExpression(r'\d{0,4}'), self)
        self.current_edit, self.new_edit, self.confirm_edit = QLineEdit(), QLineEdit(), QLineEdit()
        for edit, placeholder in ((self.current_edit, '현재 비밀번호'), (self.new_edit, '숫자 4자리'),
                                  (self.confirm_edit, '한 번 더 입력')):
            edit.setEchoMode(QLineEdit.Password)
            edit.setMaxLength(lock.PIN_LENGTH)
            edit.setValidator(validator)
            edit.setPlaceholderText(placeholder)
        self.current_row_label = QLabel('현재 비밀번호')
        form.addRow(self.current_row_label, self.current_edit)
        form.addRow('새 비밀번호', self.new_edit)
        form.addRow('비밀번호 확인', self.confirm_edit)
        self.minutes_combo = ArrowComboBox()
        for m in lock.LOCK_MINUTE_CHOICES:
            self.minutes_combo.addItem(f'{m}분', m)
        form.addRow('자동 잠금', self.minutes_combo)
        layout.addLayout(form)
        self.message_label = QLabel('')
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        layout.addWidget(self.message_label)

        actions = QHBoxLayout()
        self.set_button = QPushButton('비밀번호 설정')
        self.set_button.setProperty('primary', True)
        self.clear_button = QPushButton('잠금 해제')
        self.lock_now_button = QPushButton('지금 잠그기')
        actions.addWidget(self.set_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.lock_now_button)
        layout.addLayout(actions)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton('닫기')
        close_row.addWidget(close)
        layout.addLayout(close_row)

        close.clicked.connect(self.accept)
        self.set_button.clicked.connect(lambda: self.set_new_pin(self.new_edit.text(), self.confirm_edit.text(),
                                                                 self.current_edit.text()))
        self.clear_button.clicked.connect(lambda: self.clear(self.current_edit.text()))
        self.lock_now_button.clicked.connect(self.lock_now)
        self.minutes_combo.currentIndexChanged.connect(
            lambda i: self.set_minutes(self.minutes_combo.itemData(i)))
        self.refresh()

    def has_pin(self):
        from client.kakao import lock
        return lock.has_pin(self.settings)

    def refresh(self):
        from client.kakao import lock
        on = self.has_pin()
        minutes = lock.lock_minutes(self.settings)
        self.status_label.setText(f'사용 중 · {minutes}분 동안 입력이 없으면 잠겨요' if on
                                  else '사용 안 함 · 비밀번호 4자리를 설정하면 잠금모드가 켜져요')
        self.status_label.setStyleSheet(f"color: {theme.c('text') if on else theme.c('text_sub')};"
                                        f" font-size: {theme.fs('body')}px;")
        self.current_row_label.setVisible(on)
        self.current_edit.setVisible(on)
        self.set_button.setText('비밀번호 변경' if on else '비밀번호 설정')
        self.clear_button.setEnabled(on)
        self.lock_now_button.setEnabled(on)
        index = self.minutes_combo.findData(minutes)
        self.minutes_combo.blockSignals(True)
        self.minutes_combo.setCurrentIndex(index if index >= 0 else self.minutes_combo.findData(5))
        self.minutes_combo.blockSignals(False)

    def _message(self, text, error=True):
        self.message_label.setText(text or '')
        self.message_label.setStyleSheet(f"color: {theme.c('badge') if error else theme.c('link')};"
                                         f" font-size: {theme.fs('meta')}px;")
        self.message_label.setVisible(bool(text))
        return (text or None) if error else None

    def set_new_pin(self, pin, confirm, current=''):
        """새 PIN 저장. 오류 문자열 또는 None"""
        from client.kakao import lock
        if self.has_pin() and not lock.verify_pin(self.settings, current):
            return self._message('현재 비밀번호가 일치하지 않습니다.')
        if not lock.is_valid_pin(pin):
            return self._message('비밀번호는 숫자 4자리로 입력해 주세요.')
        if pin != confirm:
            return self._message('새 비밀번호가 서로 다릅니다.')
        saved = lock.set_pin(self.settings, pin)
        for edit in (self.current_edit, self.new_edit, self.confirm_edit):
            edit.clear()
        self.refresh()
        if not saved:
            return self._message('설정 파일에 저장하지 못했습니다. 앱을 다시 켜면 잠금이 풀릴 수 있습니다.')
        return self._message('잠금 비밀번호가 저장되었습니다.', error=False)

    def clear(self, current):
        from client.kakao import lock
        if not self.has_pin():
            return None
        if not lock.verify_pin(self.settings, current):
            return self._message('현재 비밀번호가 일치하지 않습니다.')
        lock.clear_pin(self.settings)
        self.current_edit.clear()
        self.refresh()
        return self._message('잠금모드를 껐습니다.', error=False)

    def set_minutes(self, minutes):
        from client.kakao import lock
        if minutes in lock.LOCK_MINUTE_CHOICES and self.settings.get('lock_minutes') != minutes:
            self.settings.set('lock_minutes', minutes)
        self.refresh()

    def main_window(self):
        """잠글 메인 창 - 부모 체인에서 lock()을 가진 창, 없으면 같은 설정을 쓰는 최상위 창"""
        from PyQt5.QtWidgets import QApplication
        w = self.parentWidget()
        while w is not None:
            if hasattr(w, 'lock') and hasattr(w, 'unlock'):
                return w
            w = w.parentWidget()
        for w in QApplication.topLevelWidgets():
            if hasattr(w, 'lock') and hasattr(w, 'unlock') and getattr(w, 'settings', None) is self.settings:
                return w
        return None

    def lock_now(self):
        if not self.has_pin():
            return self._message('먼저 잠금 비밀번호를 설정해 주세요.')
        win = self.main_window()
        if win is None:
            return self._message('잠글 창을 찾을 수 없습니다.')
        self.accept()
        win.lock()
        return None
