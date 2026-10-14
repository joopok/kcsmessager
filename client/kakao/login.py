# -*- coding: utf-8 -*-
"""
로그인 다이얼로그 - 회사 CI 네이비 배경 + ㈜케이씨에스 로고, 아이디/비밀번호 카드
(서버 주소는 로그인 창에서 바꾸지 않는다 - 그룹 관리자가 더보기 → 정보에서 정한다, 명세 §4.3)
처음 한 번은 [처음 사용 등록]: 소속 회사 선택(필수) + 이름 + 이메일 + 비밀번호 (명세 §4.1) — 사번 칸은 없다 (2026-10-14)
그다음부터는 로그인 아이디 + 비밀번호로 로그인한다 (운영은 이메일이 아이디 · 사번·사내 계정·이름도 통함 — 명세 §4.1). 마지막으로 로그인한 아이디만 미리 채운다
비밀번호는 저장하지 않는다
창 크기는 고정값이 아니라 레이아웃 sizeHint로 정해진다(잘림 방지).
"""

import json
import os
import re

from PyQt5.QtCore import Qt, QRectF, QPointF, QPoint, QSize
from PyQt5.QtGui import QPainter, QPainterPath, QColor, QPixmap, QIcon, QPen
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QWidget, QLayout, QSizePolicy, QCheckBox, QComboBox,
                             QApplication)

from client.kakao import theme, brand, widgets, macos, osenv
from client.kakao.net import fetch_companies, reset_password, ResetRejected

RECENT_PATH = None   # None = 실행 시점의 KCS_MESSENGER_HOME 아래 (테스트는 경로를 바꿔 끼운다)
SAVED_PATH = None


def _home_file(name):
    return osenv.app_path(name)


def recent_path():
    return RECENT_PATH or _home_file('recent_logins.json')


def saved_path():
    return SAVED_PATH or _home_file('saved_login.json')
MAX_RECENT = 6
PASSWORD_MIN, PASSWORD_MAX = 4, 64   # 서버와 같은 규칙 (명세 §4.1)
NAME_MAX, LOGIN_ID_MAX, EMAIL_MAX = 20, 120, 120
EMAIL_RE = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')
EMPLOYEE_NO_RE = re.compile(r'[0-9A-Za-z][0-9A-Za-z_-]{0,19}')   # 서버와 같은 규칙
FIELD_HINT = '#8A8F99'   # 흰 카드 위 보조 회색 (로그인 창은 다크 모드와 무관한 CI 색)


def load_recent(path=None):
    """최근 로그인한 아이디(최신순)"""
    try:
        with open(path or recent_path(), encoding='utf-8') as f:
            names = json.load(f)
        return [n for n in names if isinstance(n, str) and n.strip()][:MAX_RECENT]
    except (OSError, ValueError):
        return []


def save_recent(name, path=None):
    path = path or recent_path()
    names = [name] + [n for n in load_recent(path) if n != name]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(names[:MAX_RECENT], f, ensure_ascii=False)
        os.replace(path + '.tmp', path)
    except OSError:
        pass


def load_saved_login(path=None):
    """저장된 로그인 정보 {login_id, token, auto_login, host, port} - 없거나 깨졌으면 None.
    비밀번호 원문은 저장하지 않고 서버가 준 자동 로그인 토큰만 둔다 (명세 §4.1)"""
    try:
        with open(path or saved_path(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('login_id'), str) \
            or not isinstance(data.get('token'), str) or not data['login_id'] or not data['token']:
        return None
    port = data.get('port')
    return {'login_id': data['login_id'], 'token': data['token'], 'auto_login': data.get('auto_login') is True,
            'host': data.get('host') if isinstance(data.get('host'), str) else '',
            'port': port if isinstance(port, int) and 1 <= port <= 65535 else 0}


def save_saved_login(login_id, token, auto_login, host, port, path=None):
    path = path or saved_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # 본인만 읽기
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'login_id': login_id, 'token': token, 'auto_login': bool(auto_login),
                       'host': host, 'port': port}, f, ensure_ascii=False)
        osenv.secure_file(path)              # POSIX 600 (Windows 는 사용자 프로필 권한)
    except OSError:
        pass


def clear_saved_login(path=None):
    try:
        os.remove(path or saved_path())
    except OSError:
        pass


_CHECK_ICON_PATH = None


def check_icon_path():
    """체크박스 체크 표시(흰 바탕 네이비 ✓) PNG - 스타일시트 image: 용. 이미지 파일 없이 그려서 임시 폴더에 둔다"""
    global _CHECK_ICON_PATH
    if _CHECK_ICON_PATH and os.path.exists(_CHECK_ICON_PATH):
        return _CHECK_ICON_PATH
    import tempfile
    size = 32
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(theme.BRAND_NAVY))
    pen.setWidthF(size * 0.14)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(size * 0.24, size * 0.52)
    path.lineTo(size * 0.43, size * 0.70)
    path.lineTo(size * 0.77, size * 0.32)
    p.drawPath(path)
    p.end()
    target = os.path.join(tempfile.gettempdir(), f'kcs_talk_check_{os.getpid()}.png')
    if pm.save(target, 'PNG'):
        _CHECK_ICON_PATH = target
    return _CHECK_ICON_PATH


KAKAO_BROWN = '#3C1E1E'
LOGIN_BUTTON = '#423630'


def paint_talk_logo(p, rect, bubble_color=KAKAO_BROWN, text_color='#FEE500', text='TALK', brand='KCS'):
    """둥근 말풍선 + 꼬리 + 'TALK' 글자"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    w, h = rect.width(), rect.height()
    body = QRectF(rect.x(), rect.y(), w, h * 0.82)
    path = QPainterPath()
    path.addEllipse(body)
    tail = QPainterPath()
    tail.moveTo(QPointF(rect.x() + w * 0.24, rect.y() + h * 0.66))
    tail.lineTo(QPointF(rect.x() + w * 0.17, rect.y() + h * 0.98))
    tail.lineTo(QPointF(rect.x() + w * 0.44, rect.y() + h * 0.77))
    tail.closeSubpath()
    path = path.united(tail)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(bubble_color))
    p.drawPath(path)
    if text:
        f = theme.font('micro', bold=True)
        f.setPixelSize(max(6, int(h * 0.26)))
        f.setLetterSpacing(f.AbsoluteSpacing, max(0.5, h * 0.01))
        main_rect = QRectF(body)
        if brand and h >= 40:
            # 'KCS'를 작게 위에, 'TALK'을 크게 아래에
            small = theme.font('micro', bold=True)
            small.setPixelSize(max(6, int(h * 0.13)))
            small.setLetterSpacing(small.AbsoluteSpacing, max(0.5, h * 0.02))
            p.setFont(small)
            p.setPen(QColor(text_color))
            p.drawText(QRectF(body.x(), body.y() + body.height() * 0.17, body.width(), body.height() * 0.2),
                       Qt.AlignCenter, brand)
            main_rect = QRectF(body.x(), body.y() + body.height() * 0.1, body.width(), body.height())
        p.setFont(f)
        p.setPen(QColor(text_color))
        p.drawText(main_rect, Qt.AlignCenter, text)
    p.restore()


def talk_icon_pixmap(size=64, with_background=True):
    """앱/트레이 아이콘: 회사 CI 아이콘(네이비 + 흰색 KCS 마크). 로고가 없으면 TALK 말풍선"""
    if with_background and brand.has_logo():
        return brand.app_icon_pixmap(size)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if with_background:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#FEE500'))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        m = size * 0.18
        paint_talk_logo(p, QRectF(m, m * 1.1, size - 2 * m, size - 2 * m), text='TALK' if size >= 48 else '')
    else:
        paint_talk_logo(p, QRectF(0, 0, size, size), bubble_color='#FEE500', text='')
    p.end()
    return pm


def talk_icon(size=64):
    if brand.has_logo():
        return brand.app_icon()
    return QIcon(talk_icon_pixmap(size))


class TalkLogo(QWidget):
    def __init__(self, size=96, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, int(size * 0.9))

    def paintEvent(self, event):
        p = QPainter(self)
        paint_talk_logo(p, QRectF(0, 0, self.width(), self.height()))


class CompanyCombo(QComboBox):
    """소속 회사 선택 - 스타일시트로 꾸며도 아래 화살표가 보이도록 직접 그린다.
    목록은 콤보 위에 겹치지 않고 바로 아래로 펼친다 (겹쳐 뜨면 누른 채 떼는 순간 손 밑 항목이 골라져
    목록이 '바로 닫히는' 것처럼 보인다). 아래 공간이 모자라면 위로 펼친다"""

    def showPopup(self):
        super().showPopup()
        popup = self.view().window()
        if popup is self.window():
            return
        screen = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        popup.resize(max(popup.width(), self.width()), popup.height())
        below = self.mapToGlobal(QPoint(0, self.height()))
        y = below.y() if below.y() + popup.height() <= screen.bottom() \
            else self.mapToGlobal(QPoint(0, 0)).y() - popup.height()
        popup.move(below.x(), max(screen.top(), y))

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(FIELD_HINT), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        x, y = self.width() - 20, self.height() / 2
        p.drawPolyline(QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2))


def _divider():
    line = QWidget()
    line.setObjectName('loginFormDivider')
    line.setFixedHeight(1)
    return line


class LoginDialog(QDialog):
    def __init__(self, host='127.0.0.1', port=5555, username='', parent=None, registration=None, fresh=False,
                 tls=False):
        """fresh=True: 처음 로그인하는 상태 - 최근 아이디·저장된 로그인을 채우지 않는다 (런처에서 연 창)"""
        super().__init__(parent)
        self.fresh = fresh
        self.setWindowTitle('KCS Talk 로그인')
        self.setWindowIcon(talk_icon())
        self.setObjectName('loginDialog')

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetFixedSize)  # 내용에 맞춰 자동 크기(등록 화면 전환 포함)
        root.setContentsMargins(40, 48, 40, 24)
        root.setSpacing(0)

        if brand.has_logo():
            self.logo = QLabel()
            self.logo.setPixmap(brand.logo_pixmap('vertical', 84, '#FFFFFF'))
            self.logo.setAlignment(Qt.AlignCenter)
        else:
            self.logo = TalkLogo(84)
        root.addWidget(self.logo, 0, Qt.AlignHCenter)
        root.addSpacing(20)
        title = QLabel('KCS Talk')
        title.setObjectName('loginTitle')
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)
        root.addSpacing(6)
        self.subtitle = QLabel('이메일과 비밀번호로 로그인하세요')
        self.subtitle.setObjectName('loginSubtitle')
        self.subtitle.setAlignment(Qt.AlignCenter)
        root.addWidget(self.subtitle)
        root.addSpacing(32)

        # 아이디 · 비밀번호를 한 덩어리 카드로 (위아래 칸 사이 구분선)
        self.login_form = QWidget()
        self.login_form.setObjectName('loginForm')
        form_layout = QVBoxLayout(self.login_form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(0)
        recent = [] if fresh else load_recent(getattr(self, 'recent_path', None))
        self.name_edit = QLineEdit(username or (recent or [''])[0])
        self.name_edit.setObjectName('idEdit')
        self.name_edit.setPlaceholderText('이메일')
        self.name_edit.setAccessibleName('이메일')
        self.name_edit.setMaxLength(LOGIN_ID_MAX)
        self.name_edit.setMinimumWidth(288)
        osenv.prefer_latin_input(self.name_edit)       # 창이 뜨면 바로 영문으로 이메일 입력 (한글 상태 X)
        form_layout.addWidget(self.name_edit)
        form_layout.addWidget(_divider())
        self.password_edit = self._password_field('비밀번호')
        self.reveal_action = self.password_edit.reveal_action
        form_layout.addWidget(self.password_edit)
        root.addWidget(self.login_form)

        # 처음 사용 등록 (한 번만): 소속 회사 선택은 필수
        self.register_form = QWidget()
        self.register_form.setObjectName('loginForm')
        reg_layout = QVBoxLayout(self.register_form)
        reg_layout.setContentsMargins(0, 0, 0, 0)
        reg_layout.setSpacing(0)
        self.company_combo = CompanyCombo()
        self.company_combo.setObjectName('companyCombo')
        self.company_combo.setCursor(Qt.PointingHandCursor)
        self.company_combo.setAccessibleName('소속 회사')
        self.company_combo.setMinimumWidth(288)
        self.company_combo.setMaxVisibleItems(12)              # 계열사가 많아도 화면 안에서 스크롤
        self._companies_loaded = False
        self._set_companies([])
        reg_layout.addWidget(self.company_combo)
        reg_layout.addWidget(_divider())
        self.reg_name_edit = QLineEdit()
        self.reg_name_edit.setObjectName('idEdit')
        self.reg_name_edit.setPlaceholderText('이름')
        self.reg_name_edit.setMaxLength(NAME_MAX)
        reg_layout.addWidget(self.reg_name_edit)
        reg_layout.addWidget(_divider())
        # 사번 칸은 없다 (2026-10-14): 운영 로그인 아이디가 이메일이라 등록 때 받은 이메일이 다음부터의 아이디
        self.reg_email_edit = QLineEdit()
        self.reg_email_edit.setObjectName('idEdit')
        self.reg_email_edit.setPlaceholderText('이메일 (다음부터 로그인 아이디)')
        self.reg_email_edit.setAccessibleName('이메일')
        osenv.prefer_latin_input(self.reg_email_edit)
        self.reg_email_edit.setMaxLength(EMAIL_MAX)
        reg_layout.addWidget(self.reg_email_edit)
        reg_layout.addWidget(_divider())
        self.reg_password_edit = self._password_field(f'비밀번호 ({PASSWORD_MIN}~{PASSWORD_MAX}자)')
        reg_layout.addWidget(self.reg_password_edit)
        root.addWidget(self.register_form)
        self.register_hint = QLabel('한 번만 등록하면 다음부터는 이메일과 비밀번호만으로 로그인합니다.')
        self.register_hint.setObjectName('loginHint')
        self.register_hint.setWordWrap(True)
        root.addWidget(self.register_hint)

        # 비밀번호 재설정: 아이디 + 가입한 이메일로 본인 확인 → 새 비밀번호 (명세 §4.1)
        self.reset_form = QWidget()
        self.reset_form.setObjectName('loginForm')
        reset_layout = QVBoxLayout(self.reset_form)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.setSpacing(0)
        self.reset_id_edit = QLineEdit()
        self.reset_id_edit.setObjectName('idEdit')
        self.reset_id_edit.setPlaceholderText('이메일')
        self.reset_id_edit.setAccessibleName('이메일')
        self.reset_id_edit.setMaxLength(LOGIN_ID_MAX)
        osenv.prefer_latin_input(self.reset_id_edit)
        self.reset_id_edit.setMinimumWidth(288)
        reset_layout.addWidget(self.reset_id_edit)
        reset_layout.addWidget(_divider())
        self.reset_email_edit = QLineEdit()
        self.reset_email_edit.setObjectName('idEdit')
        self.reset_email_edit.setPlaceholderText('가입할 때 등록한 이메일')
        self.reset_email_edit.setMaxLength(EMAIL_MAX)
        reset_layout.addWidget(self.reset_email_edit)
        reset_layout.addWidget(_divider())
        self.reset_code_edit = QLineEdit()
        self.reset_code_edit.setObjectName('idEdit')
        self.reset_code_edit.setPlaceholderText('관리자에게 받은 재설정 코드 (예: ABCD-EF23)')
        self.reset_code_edit.setAccessibleName('재설정 코드')
        self.reset_code_edit.setMaxLength(20)
        reset_layout.addWidget(self.reset_code_edit)
        reset_layout.addWidget(_divider())
        self.reset_password_edit = self._password_field(f'새 비밀번호 ({PASSWORD_MIN}~{PASSWORD_MAX}자)')
        reset_layout.addWidget(self.reset_password_edit)
        root.addWidget(self.reset_form)
        self.reset_hint = QLabel('관리자에게 본인 확인을 받고 재설정 코드(30분 동안 한 번)를 받아 가입 이메일과 함께 넣습니다. '
                                 '바꾸면 이 계정의 저장된 자동 로그인은 모두 해제됩니다.')
        self.reset_hint.setObjectName('loginHint')
        self.reset_hint.setWordWrap(True)
        root.addWidget(self.reset_hint)
        root.addSpacing(10)

        self.saved = None if fresh else load_saved_login(getattr(self, 'saved_path', None))
        if self.saved and not username:
            self.name_edit.setText(self.saved['login_id'])
        self.options_box = QWidget()          # 저장 · 자동 로그인 (재설정 화면에서는 숨김)
        options = QHBoxLayout(self.options_box)
        options.setContentsMargins(2, 0, 2, 0)
        self.remember_check = QCheckBox('이메일·비밀번호 저장')
        self.auto_check = QCheckBox('자동 로그인')
        for check in (self.remember_check, self.auto_check):
            check.setObjectName('loginOption')
            check.setCursor(Qt.PointingHandCursor)
        self.remember_check.setChecked(bool(self.saved))
        self.auto_check.setChecked(bool(self.saved and self.saved['auto_login']))
        options.addWidget(self.remember_check)
        options.addStretch(1)
        options.addWidget(self.auto_check)
        root.addWidget(self.options_box)
        root.addSpacing(12)

        self.error_label = QLabel('')
        self.error_label.setObjectName('loginError')
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)
        root.addSpacing(4)

        self.login_button = QPushButton('로그인')
        self.login_button.setObjectName('loginButton')
        self.login_button.setCursor(Qt.PointingHandCursor)
        self.login_button.setDefault(True)
        self.login_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root.addWidget(self.login_button)
        root.addSpacing(10)

        self.mode_toggle = QPushButton()
        self.mode_toggle.setObjectName('modeToggle')
        self.mode_toggle.setCursor(Qt.PointingHandCursor)
        self.mode_toggle.setAutoDefault(False)
        root.addWidget(self.mode_toggle, 0, Qt.AlignHCenter)
        self.reset_link = QPushButton('비밀번호를 잊으셨나요? · 비밀번호 재설정')
        self.reset_link.setObjectName('resetLink')
        self.reset_link.setCursor(Qt.PointingHandCursor)
        self.reset_link.setAutoDefault(False)
        root.addWidget(self.reset_link, 0, Qt.AlignHCenter)
        root.addSpacing(4)

        # 접속할 서버: 화면에 칸을 두지 않는다 (명령줄 · 이 PC 의 server.json · 저장된 로그인에서 정해 온다)
        self.host = str(host or '').strip() or '127.0.0.1'
        try:
            self.port = int(port)
        except (TypeError, ValueError):
            self.port = 5555
        self.tls = bool(tls)   # 로그인 전 요청(회사 목록 · 비밀번호 재설정)도 본 연결과 같은 방식으로 붙는다
        # TLS 가 켜졌을 때만 tls= 를 넘긴다 - 평문(기본) 경로의 호출 형태를 바꾸지 않는다
        self._net_kwargs = {'tls': True} if self.tls else {}
        root.addSpacing(36)
        footer = QLabel(f'KCS Talk v2.0 · {brand.COMPANY_NAME}')
        footer.setObjectName('loginFooter')
        footer.setAlignment(Qt.AlignCenter)
        root.addWidget(footer)

        self.login_button.clicked.connect(self.accept)
        for edit in (self.name_edit, self.password_edit, self.reg_name_edit,
                     self.reg_email_edit, self.reg_password_edit):
            edit.textChanged.connect(self._update_state)
        self.company_combo.currentIndexChanged.connect(self._update_state)
        self.mode_toggle.clicked.connect(lambda: self.set_mode('register' if self._mode == 'login' else 'login'))
        self.reset_link.clicked.connect(lambda: self.set_mode('reset'))
        for edit in (self.reset_id_edit, self.reset_email_edit, self.reset_code_edit, self.reset_password_edit):
            edit.textChanged.connect(self._update_state)
        self.reset_id_edit.returnPressed.connect(self.reset_email_edit.setFocus)
        self.reset_email_edit.returnPressed.connect(self.reset_code_edit.setFocus)
        self.reset_code_edit.returnPressed.connect(self.reset_password_edit.setFocus)
        self.reset_password_edit.returnPressed.connect(self.accept)
        self.reg_name_edit.returnPressed.connect(self.reg_email_edit.setFocus)
        self.reg_email_edit.returnPressed.connect(self.reg_password_edit.setFocus)
        self.reg_password_edit.returnPressed.connect(self.accept)
        self.auto_check.toggled.connect(lambda on: on and self.remember_check.setChecked(True))
        self.remember_check.toggled.connect(lambda on: on or self.auto_check.setChecked(False))
        self.name_edit.returnPressed.connect(self.password_edit.setFocus)
        self.password_edit.returnPressed.connect(self.accept)
        self._apply_style()
        self._mode = 'login'
        self.set_mode('login')
        if registration:      # 등록이 거절돼 다시 띄운 창: 입력한 값 그대로 등록 화면
            self.set_mode('register')
            index = self.company_combo.findText(registration.get('company') or '')
            if index > 0:
                self.company_combo.setCurrentIndex(index)
            self.reg_name_edit.setText(registration.get('name') or '')
            self.reg_email_edit.setText(registration.get('email') or '')
            self.reg_password_edit.setText(registration.get('password') or '')
        (self.password_edit if self.name_edit.text() else self.name_edit).setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        # macOS: 회색 제목 표시줄 대신 네이비 배경이 창 맨 위(신호등 버튼 뒤)까지 - 헤더와 본문을 한 면으로
        if not getattr(self, '_title_bar_unified', False) and macos.unify_title_bar(self):
            self._title_bar_unified = True
            margins = self.layout().contentsMargins()
            self.layout().setContentsMargins(margins.left(), margins.top() + macos.TITLE_BAR_HEIGHT,
                                             margins.right(), margins.bottom())

    def _password_field(self, placeholder):
        """비밀번호 칸 + 눈 아이콘(누르면 입력값을 보였다 숨겼다 한다)"""
        edit = QLineEdit()
        edit.setObjectName('passwordEdit')
        edit.setPlaceholderText(placeholder)
        edit.setMaxLength(PASSWORD_MAX)
        action = edit.addAction(QIcon(), QLineEdit.TrailingPosition)
        action.setCheckable(True)
        action.toggled.connect(lambda visible: self._set_password_visible(edit, visible))
        edit.reveal_action = action
        self._set_password_visible(edit, False)
        return edit

    # ---- 화면 전환: 로그인 ↔ 처음 사용 등록 ----
    def is_register(self):
        return self._mode == 'register'

    def is_reset(self):
        return self._mode == 'reset'

    def set_mode(self, mode):
        """'login' | 'register'(처음 사용 등록) | 'reset'(비밀번호 재설정)"""
        self._mode = mode
        register, reset = mode == 'register', mode == 'reset'
        self.login_form.setVisible(mode == 'login')
        self.register_form.setVisible(register)
        self.register_hint.setVisible(register)
        self.reset_form.setVisible(reset)
        self.reset_hint.setVisible(reset)
        self.options_box.setVisible(not reset)
        self.reset_link.setVisible(mode == 'login')
        self.subtitle.setText({'register': '처음 한 번만 소속과 정보를 등록하세요',
                               'reset': '새 비밀번호를 정하세요'}.get(mode, '이메일과 비밀번호로 로그인하세요'))
        self.login_button.setText({'register': '등록하고 시작하기', 'reset': '비밀번호 바꾸기'}.get(mode, '로그인'))
        self.mode_toggle.setText('처음 사용하시나요? · 처음 사용 등록' if mode == 'login'
                                 else '로그인 화면으로 돌아가기' if reset else '이미 등록했어요 · 로그인')
        self.show_error('')
        if reset:
            self.reset_id_edit.setText(self.reset_id_edit.text() or self.name_edit.text().strip())
            (self.reset_email_edit if self.reset_id_edit.text() else self.reset_id_edit).setFocus()
        elif register:
            if not self._companies_loaded:
                self.load_companies()
            (self.company_combo if self.company_combo.currentIndex() <= 0 else self.reg_name_edit).setFocus()
        else:
            (self.password_edit if self.name_edit.text() else self.name_edit).setFocus()
        self._update_state()

    def _set_companies(self, names):
        current = self.company_combo.currentText() if self.company_combo.currentIndex() > 0 else ''
        self.company_combo.blockSignals(True)
        self.company_combo.clear()
        self.company_combo.addItem('소속 회사 선택', None)      # 고르기 전 안내 (선택 필수)
        placeholder = self.company_combo.model().item(0)
        if placeholder is not None:                            # 안내 문구는 목록에서 고를 수 없게
            placeholder.setEnabled(False)
        for name in names:
            self.company_combo.addItem(name, name)
        index = self.company_combo.findText(current) if current else -1
        self.company_combo.setCurrentIndex(max(0, index))
        self.company_combo.blockSignals(False)
        self._refresh_combo_placeholder()

    def load_companies(self):
        """서버에서 소속 회사 목록을 받는다 (로그인 전 org_companies). 성공하면 True"""
        _name, host, port = self.values()
        try:
            names = fetch_companies(host, port, **self._net_kwargs)
        except OSError:
            self.show_error('서버에 연결할 수 없어 소속 회사 목록을 불러오지 못했습니다. 잠시 후 다시 시도하거나 관리자에게 문의해 주세요.')
            return False
        self._companies_loaded = bool(names)
        self._set_companies(names)
        if not names:
            self.show_error('서버에 등록된 소속 회사가 없습니다. 관리자에게 문의해 주세요.')
        return bool(names)

    def _refresh_combo_placeholder(self):
        self.company_combo.setProperty('placeholder', self.company_combo.currentIndex() <= 0)
        self.company_combo.style().unpolish(self.company_combo)
        self.company_combo.style().polish(self.company_combo)

    def registration(self):
        """처음 사용 등록 입력값 {company, name, email, password} - 로그인 화면이면 None (사번은 보내지 않는다)"""
        if not self.is_register():
            return None
        return {'company': self.company_combo.currentData() or '', 'name': self.reg_name_edit.text().strip(),
                'email': self.reg_email_edit.text().strip(), 'password': self.reg_password_edit.text()}

    def _apply_style(self):
        # 다크 모드와 무관하게 회사 CI 색(네이비)으로 고정한다
        check = (check_icon_path() or '').replace('\\', '/')
        self.setStyleSheet(f"""
            QDialog#loginDialog {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #34478F, stop:1 {theme.BRAND_NAVY}); }}
            QLabel {{ color: #FFFFFF; background: transparent; }}
            QLabel#loginTitle {{ font-size: 22px; font-weight: bold; letter-spacing: 1px; }}
            QLabel#loginSubtitle {{ font-size: 13px; color: rgba(255,255,255,0.72); }}
            QLabel#loginFooter {{ font-size: 11px; color: rgba(255,255,255,0.5); }}
            QLabel#loginError {{ font-size: 12px; color: #FFB4A8; padding: 0 2px 8px 2px; }}
            QLabel#loginError[tone="notice"] {{ color: #B9F0CF; }}
            QPushButton#resetLink {{ background: transparent; color: rgba(255,255,255,0.72); border: none;
                         padding: 2px 8px; font-size: 12px; }}
            QPushButton#resetLink:hover {{ color: #FFFFFF; text-decoration: underline; }}
            QWidget#loginForm {{ background: #FFFFFF; border-radius: 8px; }}
            QWidget#loginFormDivider {{ background: #E3E6EE; margin: 0 14px; }}
            QLineEdit#idEdit, QLineEdit#passwordEdit {{ background: transparent; color: #191919; border: none;
                         padding: 14px 14px; font-size: 14px; selection-background-color: {theme.BRAND_NAVY};
                         selection-color: #FFFFFF; }}
            QComboBox#companyCombo {{ background: transparent; color: #191919; border: none;
                         padding: 14px 34px 14px 14px; font-size: 14px; combobox-popup: 0; }}
            QComboBox#companyCombo[placeholder="true"] {{ color: {FIELD_HINT}; }}
            QComboBox#companyCombo::drop-down {{ border: none; width: 30px; }}
            QComboBox#companyCombo::down-arrow {{ image: none; }}
            QComboBox#companyCombo QAbstractItemView {{ background: #FFFFFF; color: #191919; border: 1px solid #E3E6EE;
                         selection-background-color: #E6EBFA; selection-color: {theme.BRAND_NAVY}; outline: 0;
                         padding: 4px 0; font-size: 14px; }}
            QComboBox#companyCombo QAbstractItemView::item {{ color: #191919; padding: 7px 14px; }}
            QComboBox#companyCombo QAbstractItemView::item:selected {{ background: #E6EBFA; color: {theme.BRAND_NAVY}; }}
            QComboBox#companyCombo QAbstractItemView::item:disabled {{ color: {FIELD_HINT}; }}
            QLabel#loginHint {{ font-size: 12px; color: rgba(255,255,255,0.72); padding: 8px 2px 0 2px; }}
            QPushButton#modeToggle {{ background: transparent; color: #FFFFFF; border: none; padding: 4px 8px;
                         font-size: 13px; text-decoration: underline; }}
            QPushButton#modeToggle:hover {{ color: #E6EBFA; }}
            QCheckBox#loginOption {{ color: rgba(255,255,255,0.85); font-size: 12px; spacing: 6px; }}
            QCheckBox#loginOption::indicator {{ width: 14px; height: 14px; border-radius: 3px;
                         border: 1px solid rgba(255,255,255,0.6); background: transparent; }}
            QCheckBox#loginOption::indicator:checked {{ background: #FFFFFF; border: 1px solid #FFFFFF;
                         image: url({check}); }}
            QCheckBox#loginOption:hover {{ color: #FFFFFF; }}
            QPushButton#loginButton {{ background: #FFFFFF; color: {theme.BRAND_NAVY}; border: none; border-radius: 8px;
                         padding: 13px; font-size: 15px; font-weight: bold; }}
            QPushButton#loginButton:hover {{ background: #E6EBFA; }}
            QPushButton#loginButton:disabled {{ background: rgba(255,255,255,0.18); color: rgba(255,255,255,0.55); }}
        """)

    @staticmethod
    def _set_password_visible(edit, visible):
        edit.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        edit.reveal_action.setIcon(widgets.make_icon('eye_off' if visible else 'eye', FIELD_HINT, 20))
        tip = '비밀번호 숨기기' if visible else '비밀번호 보기'
        edit.reveal_action.setToolTip(tip)
        edit.reveal_action.setText(tip)

    def _update_state(self):
        if not hasattr(self, '_mode'):
            return
        if self.is_reset():
            self.login_button.setEnabled(all(e.text().strip() for e in (self.reset_id_edit, self.reset_email_edit,
                                                                         self.reset_code_edit))
                                         and bool(self.reset_password_edit.text()))
            return
        if self.is_register():
            self._refresh_combo_placeholder()
            self.login_button.setEnabled(self.company_combo.currentIndex() > 0
                                         and bool(self.reg_name_edit.text().strip())
                                         and bool(self.reg_email_edit.text().strip())
                                         and bool(self.reg_password_edit.text()))
            return
        saved = bool(self.saved_token())
        self.password_edit.setPlaceholderText('저장된 비밀번호 (변경하려면 입력)' if saved else '비밀번호')
        self.login_button.setEnabled(bool(self.name_edit.text().strip()) and (bool(self.password_edit.text()) or saved))

    def show_error(self, text, notice=False):
        """오류 문구(빨강 계열). notice=True 면 안내 문구(초록 계열, 예: 비밀번호를 바꿨습니다)"""
        # 서버가 '처음 사용 등록' 하라고 하면 등록 화면으로 바꾸고 아이디를 옮겨 준다
        if text and '처음 사용 등록' in text and not self.is_register():
            login_id = (self.reset_id_edit if self.is_reset() else self.name_edit).text().strip()
            self.set_mode('register')
            if '@' in login_id:
                self.reg_email_edit.setText(self.reg_email_edit.text() or login_id)
            elif len(login_id) <= NAME_MAX and not EMPLOYEE_NO_RE.fullmatch(login_id):   # 사번 모양은 옮길 칸이 없다
                self.reg_name_edit.setText(self.reg_name_edit.text() or login_id)
        self.error_label.setText(text or '')
        self.error_label.setVisible(bool(text))
        self.error_label.setProperty('tone', 'notice' if notice else 'error')
        self.error_label.style().unpolish(self.error_label)
        self.error_label.style().polish(self.error_label)

    def password(self):
        if self.is_reset():
            return ''
        return self.reg_password_edit.text() if self.is_register() else self.password_edit.text()

    def saved_token(self):
        """비밀번호 칸을 비워 두고 저장된 아이디로 로그인하면 저장된 토큰을 쓴다"""
        if self.is_register():
            return ''
        if self.saved and not self.password() and self.name_edit.text().strip() == self.saved['login_id']:
            return self.saved['token']
        return ''

    def remember(self):
        return self.remember_check.isChecked()

    def auto_login(self):
        return self.auto_check.isChecked()

    def values(self):
        """(로그인 아이디, host, port) - 등록 화면이면 등록할 이메일이 다음부터의 로그인 아이디"""
        name = self.reg_email_edit.text().strip() if getattr(self, '_mode', '') == 'register' \
            else self.name_edit.text().strip()
        return name, self.host, self.port

    def _check_registration(self):
        """등록 화면 입력 확인 → 오류 문구 (없으면 '')"""
        if self.company_combo.currentIndex() <= 0:
            if not self._companies_loaded:
                self.load_companies()
            self.company_combo.setFocus()
            return '소속 회사를 선택해 주세요.'
        name = self.reg_name_edit.text().strip()
        if not name or '@' in name:
            self.reg_name_edit.setFocus()
            return '이름을 입력해 주세요.' if not name else '이름에는 @ 를 쓸 수 없습니다.'
        if not EMAIL_RE.fullmatch(self.reg_email_edit.text().strip()):
            self.reg_email_edit.setFocus()
            return '이메일 주소 형식이 올바르지 않습니다.'
        if not PASSWORD_MIN <= len(self.reg_password_edit.text()) <= PASSWORD_MAX:
            self.reg_password_edit.setFocus()
            return f'비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자로 입력해 주세요.'
        return ''

    def _submit_reset(self, host, port):
        """비밀번호 재설정 요청 → 성공하면 로그인 화면으로 돌아가 아이디를 채운다 (창은 닫지 않는다)"""
        login_id = self.reset_id_edit.text().strip()
        email = self.reset_email_edit.text().strip()
        new_password = self.reset_password_edit.text()
        if not EMAIL_RE.fullmatch(email):
            self.reset_email_edit.setFocus()
            self.show_error('이메일 주소 형식이 올바르지 않습니다.')
            return
        if not PASSWORD_MIN <= len(new_password) <= PASSWORD_MAX:
            self.reset_password_edit.setFocus()
            self.show_error(f'비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자로 입력해 주세요.')
            return
        try:
            reset_password(host, port, login_id, email, new_password, code=self.reset_code_edit.text().strip(),
                           **self._net_kwargs)
        except ResetRejected as exc:
            self.show_error(str(exc))
            return
        except OSError:
            self.show_error('서버에 연결할 수 없습니다. 잠시 후 다시 시도하거나 관리자에게 문의해 주세요.')
            return
        if self.saved and self.saved['login_id'] == login_id:   # 서버가 토큰을 폐기했다 → 저장된 로그인도 지운다
            clear_saved_login(getattr(self, 'saved_path', None))
            self.saved = None
            self.remember_check.setChecked(False)
        self.reset_password_edit.clear()
        self.reset_code_edit.clear()
        self.set_mode('login')
        self.name_edit.setText(login_id)
        self.password_edit.clear()
        self.password_edit.setFocus()
        self.show_error('비밀번호를 바꿨습니다. 새 비밀번호로 로그인하세요.', notice=True)

    def accept(self):
        name, host, port = self.values()
        if not (1 <= port <= 65535):
            self.show_error('포트 번호가 올바르지 않습니다.')
            return
        if self.is_reset():
            self._submit_reset(host, port)
            return
        if self.is_register():
            error = self._check_registration()
            if error:
                self.show_error(error)
                return
            if not (1 <= port <= 65535):
                self.show_error('포트 번호가 올바르지 않습니다.')
                return
            super().accept()
            return
        if not name:
            self.show_error('이메일을 입력해 주세요.')
            return
        if not self.saved_token() and not PASSWORD_MIN <= len(self.password()) <= PASSWORD_MAX:
            self.show_error(f'비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자로 입력해 주세요.')
            self.password_edit.setFocus()
            return
        if not (1 <= port <= 65535):
            self.show_error('포트 번호가 올바르지 않습니다.')
            return
        super().accept()

    def sizeHint(self):
        return super().sizeHint().expandedTo(QSize(352, 0))
