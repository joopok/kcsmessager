# -*- coding: utf-8 -*-
"""
로그아웃 확인 창 - 사원증 모양 머리(CI 네이비 띠 + 로고 + 띠에 걸친 내 아바타) 아래에
무엇이 지워지고 무엇이 남는지를 이 PC의 실제 저장 상태대로 알려 준다.
색은 theme 토큰, 로고는 brand, 아바타는 user_pixmap 만 쓴다 (다크 모드 대응).
"""

from PyQt5.QtCore import Qt, QRectF, QSize
from PyQt5.QtGui import QPainter, QPainterPath
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QSizePolicy

from client.kakao import theme, brand
from client.kakao.chats_tab import user_pixmap
from client.kakao.widgets import set_shown


def logout_effects(saved):
    """저장된 로그인 상태({login_id, auto_login, ...} 또는 None) → 로그아웃하면 바뀌는 점 (문장 목록)"""
    if saved and saved.get('auto_login'):
        first = '이 PC에 저장된 로그인 정보와 자동 로그인이 해제됩니다.'
    elif saved:
        first = '이 PC에 저장된 로그인 정보가 지워집니다.'
    else:
        first = '다음에 들어올 때 아이디와 비밀번호를 다시 입력합니다.'
    return [first, '대화와 파일은 서버에 그대로 남아 있습니다.']


class BadgeHeader(QWidget):
    """사원증 머리: 위쪽 네이비 띠(오른쪽 위 흰 CI 로고) + 띠 아래 경계에 걸친 아바타(바탕색 테두리)"""

    BAND = 56          # 띠 높이
    AVATAR = 64        # 아바타 지름
    RING = 4           # 아바타 테두리(창 바탕색) 두께
    LEFT = 24          # 아바타 왼쪽 여백 = 아래 글자 왼쪽 여백

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.avatar = user_pixmap(state, state.me, self.AVATAR) if state.me else None
        self.logo = brand.logo_pixmap('horizontal', 14, '#FFFFFF') if brand.has_logo() else None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(theme.px(360), self.BAND + self.AVATAR // 2 + self.RING)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(QRectF(0, 0, self.width(), self.BAND), theme.qc('yellow'))     # 주 강조색 = CI 네이비
        if self.logo is not None:
            ratio = self.logo.devicePixelRatio() or 1
            w, h = self.logo.width() / ratio, self.logo.height() / ratio
            p.setOpacity(0.8)
            p.drawPixmap(QRectF(self.width() - 20 - w, (self.BAND - h) / 2, w, h), self.logo,
                         QRectF(self.logo.rect()))
            p.setOpacity(1.0)
        top = self.BAND - self.AVATAR / 2
        ring = QRectF(self.LEFT - self.RING, top - self.RING, self.AVATAR + 2 * self.RING, self.AVATAR + 2 * self.RING)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qc('bg'))
        p.drawEllipse(ring)
        if self.avatar is not None:
            clip = QPainterPath()
            clip.addEllipse(QRectF(self.LEFT, top, self.AVATAR, self.AVATAR))
            p.setClipPath(clip)
            p.drawPixmap(QRectF(self.LEFT, top, self.AVATAR, self.AVATAR), self.avatar, QRectF(self.avatar.rect()))
        p.end()


class LogoutDialog(QDialog):
    """로그아웃할까요? - [취소] / [로그아웃]. Enter = 로그아웃, Esc = 취소"""

    def __init__(self, state, saved=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('로그아웃')
        self.setObjectName('logoutDialog')
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        user = state.user(state.me) if state.me else {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, theme.sp('l') + theme.sp('xs'))
        root.setSpacing(0)
        self.header = BadgeHeader(state)
        root.addWidget(self.header)

        body = QVBoxLayout()
        side = BadgeHeader.LEFT
        body.setContentsMargins(side, theme.sp('s'), side, 0)
        body.setSpacing(theme.sp('xxs'))
        name = state.display_name(state.me) if state.me else ''
        self.name_label = QLabel(' '.join(x for x in (name, user.get('title')) if x))
        self.name_label.setObjectName('logoutName')
        body.addWidget(self.name_label)
        path = user.get('org_path') or ([user['department']] if user.get('department') else [])
        org = ' · '.join(x for x in (user.get('company'), path[0] if path else '') if x)
        login_id = (saved or {}).get('login_id') or user.get('account') or ''
        self.org_label = QLabel(' · '.join(x for x in (org, login_id if login_id != name else '') if x))
        self.org_label.setObjectName('logoutMeta')
        set_shown(self.org_label, bool(self.org_label.text()))   # 아직 창에 붙기 전이라 setVisible(True) 는 따로 창을 띄운다
        body.addWidget(self.org_label)

        body.addSpacing(theme.sp('xl'))
        self.title_label = QLabel('로그아웃할까요?')
        self.title_label.setObjectName('logoutTitle')
        body.addWidget(self.title_label)
        body.addSpacing(theme.sp('s'))
        self.effect_labels = []
        for i, text in enumerate(logout_effects(saved)):
            if i:
                body.addSpacing(theme.sp('xs'))
            label = QLabel(text)
            label.setObjectName('logoutEffect' if i == 0 else 'logoutKeep')   # 바뀌는 점 / 그대로 남는 점
            label.setWordWrap(True)
            body.addWidget(label)
            self.effect_labels.append(label)
        body.addSpacing(theme.sp('xl'))

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.sp('s'))
        buttons.addStretch(1)
        self.cancel_button = QPushButton('취소')
        self.cancel_button.setObjectName('logoutCancel')
        self.logout_button = QPushButton('로그아웃')
        self.logout_button.setObjectName('logoutConfirm')
        for button in (self.cancel_button, self.logout_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(theme.px(36))
            buttons.addWidget(button)
        self.logout_button.setDefault(True)
        self.logout_button.setAutoDefault(True)
        self.cancel_button.setAutoDefault(False)
        body.addLayout(buttons)
        root.addLayout(body)

        self.cancel_button.clicked.connect(self.reject)
        self.logout_button.clicked.connect(self.accept)
        self._apply_style()
        self.logout_button.setFocus()

    def _apply_style(self):
        self.setStyleSheet(
            f"QDialog#logoutDialog {{ background: {theme.c('bg')}; }}"
            f" QLabel#logoutName {{ color: {theme.c('text')}; font-size: {theme.fs('lead')}px; font-weight: bold; }}"
            f" QLabel#logoutMeta {{ color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; }}"
            f" QLabel#logoutTitle {{ color: {theme.c('text')}; font-size: {theme.fs('heading')}px;"
            f" font-weight: bold; }}"
            f" QLabel#logoutEffect {{ color: {theme.c('text_sub')}; font-size: {theme.fs('body')}px; }}"
            f" QLabel#logoutKeep {{ color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px; }}"
            + theme.button_qss('logoutCancel', 'outline', size='body', pad='6px 18px')
            + theme.button_qss('logoutConfirm', 'primary', size='body', pad='6px 18px'))
