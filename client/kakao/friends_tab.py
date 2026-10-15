# -*- coding: utf-8 -*-
"""
친구 탭 - 펑(24시간 스토리) 줄, 내 프로필, AI 비서, 생일인 친구, 즐겨찾기, 온라인/오프라인 친구(접기 가능)
프로필 보기/편집 다이얼로그와 상태(presence) 선택 메뉴, 친구 숨김/차단도 이 모듈에 둔다.
"""

import base64

from PyQt5.QtCore import Qt, QBuffer, QByteArray, QIODevice, QRectF, QEvent, QPoint, QTimer
from PyQt5.QtGui import QImage, QPainter, QColor, QPen, QFontMetrics
from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QMenu, QDialog,
                             QLineEdit, QPushButton, QFileDialog, QFormLayout, QSizePolicy,
                             QMessageBox, QFrame, QGridLayout, QAbstractButton, QScrollArea, QApplication)

from client.kakao import theme
from client.kakao.widgets import Avatar, make_icon
from client.kakao.chats_tab import (ElidedLabel, StatusPill, RowList, TabHeader, CreateGroupDialog,
                                    make_search_edit, search_box_qss, user_pixmap, InviteToRoomDialog, presence_of,
                                    set_presence, foreign_company, CompanyChip, connect_avatar_changed,
                                    presence_dot_pixmap, PRESENCE_CHOICES, PRESENCE_LABELS,
                                    PRESENCE_COLORS)

AVATAR_MAX_BYTES = 512 * 1024
SECTION_TITLES = {
    'bot': 'AI 비서',
    'birthday': '생일인 친구',
    'team': '내 부서',
    'favorites': '즐겨찾기',
    'registered': '내가 추가한 친구',
    'online': '온라인',
    'offline': '오프라인',
}
BOT_HINT = '문서를 보내면 학습하고, 질문에 답해요'


def parse_birthday(text):
    """'YYYY-MM-DD'(생년월일) · 'MM-DD'(생일만) → date (연도 없으면 2000년), 틀리면 None"""
    from datetime import datetime, date
    text = (text or '').strip()
    if len(text) not in (5, 10):
        return None
    try:
        day = datetime.strptime(text if len(text) == 10 else '2000-' + text, '%Y-%m-%d').date()
    except ValueError:
        return None
    if len(text) == 10 and not (1900 <= day.year and day <= date.today()):
        return None
    return day


def normalize_birthday(text):
    """'198001-04' · '19800104' · '1980.01.04' · '1980/1/4' 처럼 입력해도 'YYYY-MM-DD' 로 맞춘다 (못 맞추면 그대로)"""
    import re
    text = (text or '').strip()
    m = re.fullmatch(r'(\d{4})[-./ ]?(\d{1,2})[-./ ]?(\d{1,2})', text)
    if not m or (not re.search(r'[-./ ]', text) and len(text) != 8):     # 구분자 없으면 8자리만 (짐작 금지)
        return text
    return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'


def birthday_missing_year(value):
    """월·일만 있는 생일(MM-DD)인가 - 프로필 편집에서 연도를 넣도록 안내한다"""
    text = (value or '').strip()
    return len(text) == 5 and parse_birthday(text) is not None


def birthday_text(value):
    """프로필 카드 문구 - '생년월일 1990년 3월 15일' / 연도가 없으면 '생일 3월 15일'"""
    day = parse_birthday(value)
    if day is None:
        return value or ''
    if len(value.strip()) == 10:
        return f'생년월일 {day.year}년 {day.month}월 {day.day}일'
    return f'생일 {day.month}월 {day.day}일'


# ================================================================ 상태(presence) 메뉴
def presence_menu(state, parent=None, on_selected=None):
    """온라인/자리비움/다른 용무 중/회의 중/휴가 중 선택 메뉴"""
    menu = QMenu(parent)
    current = presence_of(state, state.me)
    for key, label, _color in PRESENCE_CHOICES:
        action = menu.addAction(make_icon_from_pixmap(presence_dot_pixmap(key, 12)), label)
        action.setCheckable(True)
        action.setChecked(key == current)
        action.setData(key)

        def choose(_checked=False, k=key):
            set_presence(state, k)
            if on_selected:
                on_selected(k)
        action.triggered.connect(choose)
    return menu


def make_icon_from_pixmap(pm):
    from PyQt5.QtGui import QIcon
    return QIcon(pm)


class PresenceButton(QPushButton):
    """현재 상태를 색 점 + 글자로 보여주고 누르면 상태 메뉴를 띄운다"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.clicked.connect(self._open_menu)
        self.refresh()

    def refresh(self):
        presence = presence_of(self.state, self.state.me)
        if presence == 'offline':
            presence = 'online'
        self.setIcon(make_icon_from_pixmap(presence_dot_pixmap(presence, 10)))
        self.setText(PRESENCE_LABELS.get(presence, '온라인'))
        self.setStyleSheet(
            f"QPushButton {{ background: {theme.c('bg_alt')}; border: 1px solid {theme.c('border')};"
            f" border-radius: {theme.px(10)}px; padding: 3px 10px; font-size: {theme.fs('caption')}px;"
            f" color: {theme.c('text_sub')}; }}"
            f" QPushButton:hover {{ background: {theme.c('bg_hover')}; }}")

    def _open_menu(self):
        menu = presence_menu(self.state, self, lambda _k: None)
        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))



# ================================================================ 숨김 / 차단
def hide_friend(state, username, hidden=True):
    state.set_hidden(username, hidden)


def confirm_block(state, username, parent=None):
    """차단 확인 후 block_set 전송. 차단했으면 True"""
    name = state.display_name(username)
    answer = QMessageBox.question(
        parent, '친구 차단',
        f"{name}님을 차단할까요?\n차단하면 {name}님이 보내는 메시지를 받지 않으며, 상대방은 차단 사실을 알 수 없습니다.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if answer != QMessageBox.Yes:
        return False
    state.block_user(username, True)
    return True


# ================================================================ 펑 줄
class StoryBubble(QAbstractButton):
    """펑 아바타 - 안 본 펑이 있으면 강조색 고리, 다 봤으면 회색 고리. kind='add' 는 '+' 올리기 버튼"""

    def __init__(self, state, username, kind='story', parent=None):
        super().__init__(parent)
        self.state = state
        self.username = username
        self.kind = kind
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(theme.px(64), theme.px(80))
        if kind == 'add':
            self.setToolTip('펑 올리기')
        else:
            self.setToolTip(f'{state.display_name(username)}님의 펑')

    def unseen(self):
        return self.kind == 'story' and self.state.has_unseen_story(self.username)

    def ring_color(self):
        return theme.c('yellow') if self.unseen() else theme.c('border' if not theme.is_dark() else 'text_hint')

    def label(self):
        if self.kind == 'add':
            return '펑 올리기'
        if self.username == self.state.me:
            return '내 펑'
        return self.state.display_name(self.username)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = theme.px(52)
        x = (self.width() - d) / 2
        ring = QRectF(x, 3, d, d)
        if self.kind == 'add':
            pen = QPen(QColor(theme.c('text_hint')), 1.4, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(theme.qc('bg_alt'))
            p.drawEllipse(ring.adjusted(1, 1, -1, -1))
            # 내 사진 위 '+' 대신 단순한 + 기호(카카오 펑 첫 칸)
            p.setPen(QPen(theme.qc('text_sub'), 2.0, Qt.SolidLine, Qt.RoundCap))
            c = ring.center()
            s = d * 0.18
            p.drawLine(int(c.x() - s), int(c.y()), int(c.x() + s), int(c.y()))
            p.drawLine(int(c.x()), int(c.y() - s), int(c.x()), int(c.y() + s))
        else:
            p.setPen(QPen(QColor(self.ring_color()), 2.4 if self.unseen() else 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(ring.adjusted(1.2, 1.2, -1.2, -1.2))
            inner = int(d - 10)
            pm = user_pixmap(self.state, self.username, inner)
            p.drawPixmap(int(x + 5), 8, inner, inner, pm)
        f = theme.font('caption', bold=self.unseen())
        p.setFont(f)
        p.setPen(theme.qc('text' if self.kind != 'add' else 'text_sub'))
        fm = QFontMetrics(f)
        text = fm.elidedText(self.label(), Qt.ElideRight, self.width() - 2)
        p.drawText(QRectF(0, d + 8, self.width(), fm.height() + 2), Qt.AlignHCenter | Qt.AlignTop, text)


class StoryBar(QWidget):
    """친구 탭 위쪽 '펑' 가로 줄 - [+ 펑 올리기] [내 펑] [친구들 …]"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.bubbles = []
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 2, 0, 4)
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(16, 0, 16, 0)
        self.title = QLabel('펑')
        head.addWidget(self.title)
        self.count_label = QLabel('')
        head.addWidget(self.count_label, 1)
        self.compose_link = QPushButton('＋ 펑 올리기')
        self.compose_link.setProperty('flat', True)
        self.compose_link.setCursor(Qt.PointingHandCursor)
        self.compose_link.setToolTip('24시간 뒤 사라지는 소식을 올려요')
        self.compose_link.clicked.connect(lambda: self.compose())
        head.addWidget(self.compose_link)
        col.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.inner = QWidget()
        self.inner.setObjectName('storyInner')
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(10, 0, 10, 0)
        self.row.setSpacing(2)
        self.scroll.setWidget(self.inner)
        col.addWidget(self.scroll)
        state.stories_changed.connect(self.refresh)
        state.users_changed.connect(self.refresh)
        state.blocked_changed.connect(self.refresh)
        connect_avatar_changed(state, self.refresh)
        self.apply_theme()

    def owners(self):
        return list(self.state.story_owners())

    def bubble_menu(self, username):
        """펑 동그라미 우클릭: 소식 숨기기"""
        menu = QMenu(self)
        menu.addAction('펑 보기', lambda: self.open_story(username))
        menu.addAction(f"{self.state.display_name(username)}님 소식 숨기기",
                       lambda: self.state.set_story_hidden(username, True))
        return menu

    def bubble_for(self, username):
        for b in self.bubbles:
            if b.kind == 'story' and b.username == username:
                return b
        return None

    def refresh(self):
        for b in self.bubbles:
            b.hide()
            b.deleteLater()   # 클릭 처리 중 삭제될 수 있으므로 지연 삭제
        self.bubbles = []
        while self.row.count():
            self.row.takeAt(0)
        add = StoryBubble(self.state, self.state.me, 'add')
        add.clicked.connect(self.compose)
        self.bubbles.append(add)
        self.row.addWidget(add)
        owners = self.owners()
        for name in owners:
            b = StoryBubble(self.state, name, 'story')
            b.clicked.connect(lambda _c=False, n=name: self.open_story(n))
            if name != self.state.me:
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(
                    lambda pos, n=name, w=b: self.bubble_menu(n).exec_(w.mapToGlobal(pos)))
            self.bubbles.append(b)
            self.row.addWidget(b)
        self.row.addStretch(1)
        unseen = sum(1 for n in owners if self.state.has_unseen_story(n))
        self.count_label.setText(f'새 펑 {unseen}' if unseen else '')
        self.scroll.setFixedHeight(theme.px(80) + 12)
        # 올라온 펑이 없으면 가로 줄 대신 한 줄 링크만 (첫 화면 공간을 대화·친구에 양보)
        self.scroll.setVisible(bool(owners))
        self.compose_link.setVisible(not owners)
        self.compose_link.setStyleSheet(f"color: {theme.c('yellow')}; font-size: {theme.fs('meta')}px;"
                                        f" font-weight: bold; padding: 2px 6px;")

    def open_story(self, username):
        from client.kakao.story import StoryViewer
        viewer = StoryViewer(self.state, username, self.window())
        viewer.exec_()
        return viewer

    def compose(self):
        from client.kakao.story import StoryComposeDialog
        dlg = StoryComposeDialog(self.state, self.window())
        dlg.exec_()
        return dlg

    def apply_theme(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"StoryBar, QScrollArea, QWidget#storyInner {{ background: {theme.c('bg')}; }}")
        self.title.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold;")
        self.count_label.setStyleSheet(f"color: {theme.c('yellow') if not theme.is_dark() else theme.c('brand')};"
                                       f" font-size: {theme.fs('caption')}px; font-weight: bold; padding-left: 4px;")
        self.refresh()


# ================================================================ 프로필 사진
def avatar_png_from_path(path, size=256):
    """이미지 → 정사각형 가운데 자르기 → size px PNG bytes. (bytes, 오류문자열)"""
    image = QImage(path)
    if image.isNull():
        return None, '이미지 파일을 열 수 없습니다.'
    side = min(image.width(), image.height())
    image = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    image = image.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, 'PNG')
    buf.close()
    raw = bytes(data)
    if len(raw) > AVATAR_MAX_BYTES:
        return None, '프로필 사진은 512KB 이하만 사용할 수 있습니다.'
    return raw, None


# ================================================================ 프로필 편집
class ProfileEditDialog(QDialog):
    """내 프로필 편집 - 맨 위 사번(보기만), 사진, 이름, 상태메시지, 생년월일(YYYY-MM-DD), 전화번호 · 이메일, 대직자(§8.13)"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        me = state.user(state.me)
        self.setWindowTitle('프로필 편집')
        self.setMinimumWidth(theme.px(400))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        # 맨 위: 사번 (로그인 아이디 - 보기만, 바꾸려면 관리자)
        number = (me.get('employee_no') or '').strip()
        self.employee_card = QFrame()
        self.employee_card.setObjectName('employeeCard')
        self.employee_card.setStyleSheet(f"QFrame#employeeCard {{ background: {theme.c('bg_alt')}; border-radius: 10px; }}")
        card_row = QHBoxLayout(self.employee_card)
        card_row.setContentsMargins(14, 9, 14, 9)
        card_row.setSpacing(10)
        key = QLabel('사번')
        key.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold;"
                          f" background: transparent;")
        self.employee_no_label = QLabel(number or '등록된 사번 없음')
        self.employee_no_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.employee_no_label.setAccessibleName('사번')
        self.employee_no_label.setStyleSheet(
            f"color: {theme.c('text') if number else theme.c('text_hint')}; font-size: {theme.fs('emphasis')}px;"
            f" font-weight: bold; background: transparent;")
        note = QLabel('로그인 아이디 · 변경은 관리자에게')
        note.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('caption')}px; background: transparent;")
        card_row.addWidget(key)
        card_row.addWidget(self.employee_no_label)
        card_row.addStretch(1)
        card_row.addWidget(note)
        layout.addWidget(self.employee_card)

        self.avatar = Avatar(size=96)
        layout.addWidget(self.avatar, 0, Qt.AlignHCenter)
        photo_row = QHBoxLayout()
        photo_row.addStretch(1)
        self.photo_button = QPushButton('사진 변경')
        self.clear_photo_button = QPushButton('기본 이미지')
        self.clear_photo_button.setToolTip('회사 조직도 사진으로 되돌립니다 (조직도 사진이 없으면 사진 없음)')
        photo_row.addWidget(self.photo_button)
        photo_row.addWidget(self.clear_photo_button)
        photo_row.addStretch(1)
        layout.addLayout(photo_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)   # macOS 기본(칸이 좁게 고정)이면 글자가 잘렸다
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.name_edit = QLineEdit(me.get('display_name') or state.me)
        self.name_edit.setMaxLength(20)
        self.status_edit = QLineEdit(me.get('status_message') or '')
        self.status_edit.setMaxLength(60)
        self.status_edit.setPlaceholderText('상태메시지를 입력해 주세요')
        self.birthday_edit = QLineEdit(me.get('birthday') or '')
        self.birthday_edit.setPlaceholderText('YYYY-MM-DD (예: 1990-03-15)')
        self.birthday_edit.setMaxLength(10)
        self.birthday_edit.setInputMask('')
        form.addRow('이름', self.name_edit)
        form.addRow('상태메시지', self.status_edit)
        form.addRow('생년월일', self.birthday_edit)
        # 예전에 월·일만(MM-DD) 저장된 생일은 연도가 안 보인다 - 연도를 넣도록 칸 아래에 안내하고 그 칸으로 이동
        md = (me.get('birthday') or '').strip() if birthday_missing_year(me.get('birthday')) else '03-15'
        self.birthday_hint = QLabel(f'연도가 빠져 있어요. 1990-{md}처럼 앞에 연도를 넣어 주세요.')
        self.birthday_hint.setWordWrap(True)
        self.birthday_hint.setStyleSheet(f"color: {theme.c('danger_text')}; font-size: {theme.fs('caption')}px;")
        self.birthday_hint.setVisible(birthday_missing_year(me.get('birthday')))
        form.addRow('', self.birthday_hint)
        self.birthday_edit.textChanged.connect(
            lambda text: self.birthday_hint.setVisible(birthday_missing_year(text)))
        self.mobile_edit = QLineEdit(me.get('mobile') or '')
        self.mobile_edit.setPlaceholderText('010-1234-5678')
        self.mobile_edit.setMaxLength(20)
        self.email_edit = QLineEdit(me.get('email') or '')
        self.email_edit.setPlaceholderText('name@company.com')
        self.email_edit.setMaxLength(120)
        form.addRow('전화번호', self.mobile_edit)
        form.addRow('이메일', self.email_edit)
        from client.kakao.deputies import DeputyEditor
        self.deputy_editor = DeputyEditor(state, me.get('deputies') or [])
        form.addRow('대직자', self.deputy_editor)
        layout.addLayout(form)
        hint = QLabel('다른 사람이 전화번호나 이메일로 나를 친구로 추가할 수 있어요.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('caption')}px;")
        layout.addWidget(hint)

        self.error_label = QLabel('')
        self.error_label.setStyleSheet(f"color: {theme.c('danger_text')}; font-size: {theme.fs('meta')}px;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        self.ok_button = QPushButton('저장')
        self.ok_button.setProperty('primary', True)
        self.ok_button.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        cancel.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        self.photo_button.clicked.connect(self.choose_photo)
        self.clear_photo_button.clicked.connect(self.clear_photo)
        connect_avatar_changed(state, self._refresh_avatar)
        self._refresh_avatar()
        if self.birthday_hint.isVisibleTo(self):
            self.birthday_edit.setFocus()
            QTimer.singleShot(0, self._caret_before_birthday)   # 창이 뜬 뒤 (포커스가 글자를 전체 선택하므로)

    def _caret_before_birthday(self):
        """월·일은 남겨 두고 맨 앞에 연도를 바로 입력하게 - 전체 선택을 풀고 커서를 맨 앞으로"""
        self.birthday_edit.deselect()
        self.birthday_edit.setCursorPosition(0)

    def _refresh_avatar(self):
        self.avatar.setPixmap(user_pixmap(self.state, self.state.me, 96))

    def _show_error(self, text, field=None):
        """오류를 보여 주고 그 입력칸에 연결한다 (ui-styling: 오류는 입력칸과 연결해 스크린리더가 읽게,
        포커스를 그 칸으로)"""
        self.error_label.setText(text or '')
        self.error_label.setVisible(bool(text))
        for edit in (self.birthday_edit, self.mobile_edit, self.email_edit):
            edit.setAccessibleDescription(text if edit is field else '')
        if field is not None:
            field.setFocus()

    def choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, '프로필 사진 선택', '',
                                              '이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)')
        if path:
            self.set_photo_from_path(path)

    def set_photo_from_path(self, path):
        """사진을 정사각형 256px PNG로 변환해 바로 전송. 오류 문자열 또는 None"""
        data, err = avatar_png_from_path(path)
        if err:
            self._show_error(err)
            return err
        self._show_error('')
        self.state.update_profile(avatar_b64=base64.b64encode(data).decode('ascii'))
        return None

    def clear_photo(self):
        self.state.update_profile(avatar_clear=True)

    def values(self):
        return {'display_name': self.name_edit.text().strip() or self.state.me,
                'status_message': self.status_edit.text().strip(),
                'birthday': normalize_birthday(self.birthday_edit.text()) or None,
                **self._contact_changes(), **self._deputy_changes()}

    def _deputy_changes(self):
        """대직자는 바뀌었을 때만 보낸다 (§8.13)"""
        deputies = self.deputy_editor.deputies()
        before = [d for d in self.state.user(self.state.me).get('deputies') or []]
        return {'deputies': deputies} if deputies != before else {}

    def _contact_changes(self):
        """전화번호·이메일은 바뀐 것만 보낸다"""
        me = self.state.user(self.state.me)
        out = {}
        for key, edit in (('mobile', self.mobile_edit), ('email', self.email_edit)):
            value = edit.text().strip()
            if value != (me.get(key) or ''):
                out[key] = value
        return out

    def accept(self):
        values = self.values()
        birthday = values['birthday']
        if birthday and (parse_birthday(birthday) is None or birthday_missing_year(birthday)):
            self._show_error('생년월일은 연도까지 YYYY-MM-DD 형식으로 입력해 주세요. (예: 1990-03-15)',
                             self.birthday_edit)
            return
        if values.get('mobile') and self.state.contact_kind(values['mobile']) != 'phone':
            self._show_error('전화번호 형식이 올바르지 않습니다. (예: 010-1234-5678)', self.mobile_edit)
            return
        if values.get('email') and self.state.contact_kind(values['email']) != 'email':
            self._show_error('이메일 형식이 올바르지 않습니다. (예: name@company.com)', self.email_edit)
            return
        self.state.update_profile(**values)
        super().accept()


# ================================================================ 친구 추가 (전화번호 · 이메일)
def _set_add_friend_icon(button, size=22):
    """사람 + 플러스 아이콘"""
    from PyQt5.QtGui import QPixmap, QIcon
    from PyQt5.QtCore import QSize, QPointF
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(theme.c('text_sub')), 1.7)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    k = size / 24.0
    p.drawEllipse(QRectF(5 * k, 4 * k, 7.5 * k, 7.5 * k))
    p.drawArc(QRectF(2 * k, 13.5 * k, 13.5 * k, 12 * k), 0, 180 * 16)
    p.drawLine(QPointF(18.5 * k, 6 * k), QPointF(18.5 * k, 13 * k))
    p.drawLine(QPointF(15 * k, 9.5 * k), QPointF(22 * k, 9.5 * k))
    p.end()
    button.setIcon(QIcon(pm))
    button.setIconSize(QSize(size, size))


class AddFriendDialog(QDialog):
    """친구 추가 - [전화번호] / [이메일] 탭, 정확히 일치하는 사용자만 찾는다 (카카오톡 연락처·ID 추가와 같은 방식)"""

    MODES = {'phone': ('전화번호', '010-1234-5678', '상대방이 프로필에 등록한 휴대폰 번호를 입력하세요.'),
             'email': ('이메일', 'name@company.com', '상대방이 프로필에 등록한 이메일 주소를 입력하세요.')}

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.mode = 'phone'
        self.found = None
        self.setWindowTitle('친구 추가')
        self.setMinimumWidth(360)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 18)
        col.setSpacing(10)
        title = QLabel('친구 추가')
        title.setStyleSheet(f"font-size: {theme.fs('headline')}px; font-weight: bold;")
        col.addWidget(title)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.mode_buttons = {}
        for key, (label, _ph, _hint) in self.MODES.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            tabs.addWidget(b)
            self.mode_buttons[key] = b
        tabs.addStretch(1)
        col.addLayout(tabs)
        self.input = QLineEdit()
        self.input.setMinimumHeight(theme.px(38))
        self.input.returnPressed.connect(self.submit)
        col.addWidget(self.input)
        self.hint = QLabel('')
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")
        col.addWidget(self.hint)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.hide()
        col.addWidget(self.message)
        # 찾은 사람 카드
        self.card = QWidget()
        card_row = QHBoxLayout(self.card)
        card_row.setContentsMargins(0, 6, 0, 6)
        card_row.setSpacing(12)
        self.card_avatar = Avatar(size=theme.px(48))
        card_row.addWidget(self.card_avatar)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.card_name = QLabel('')
        self.card_name.setStyleSheet(f"font-size: {theme.fs('title')}px; font-weight: bold;")
        self.card_sub = QLabel('')
        self.card_sub.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        info.addWidget(self.card_name)
        info.addWidget(self.card_sub)
        card_row.addLayout(info, 1)
        self.chat_button = QPushButton('1:1 채팅')
        self.chat_button.clicked.connect(self._chat)
        card_row.addWidget(self.chat_button)
        self.card.hide()
        col.addWidget(self.card)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        self.add_button = QPushButton('친구 추가')
        self.add_button.setProperty('primary', True)
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self.submit)
        buttons.addWidget(close)
        buttons.addWidget(self.add_button)
        col.addLayout(buttons)
        state.friend_added.connect(self._on_added)
        state.error.connect(self._on_error)
        self.input.textChanged.connect(lambda _t: self._update_button())
        self.set_mode('phone')

    def set_mode(self, mode):
        self.mode = mode
        label, placeholder, hint = self.MODES[mode]
        for key, b in self.mode_buttons.items():
            b.setChecked(key == mode)
            b.setProperty('primary', key == mode)
            b.style().unpolish(b)
            b.style().polish(b)
        self.input.clear()
        self.input.setPlaceholderText(placeholder)
        self.hint.setText(hint)
        self._show_message('')
        self.card.hide()
        self._update_button()
        self.input.setFocus()

    def set_query(self, text):
        self.input.setText(text)

    def _update_button(self):
        self.add_button.setEnabled(self.state.contact_kind(self.input.text()) == self.mode)

    def _show_message(self, text, error=False):
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.input.setAccessibleDescription(text if error else '')   # 오류를 입력칸에 연결
        color = theme.c('danger_text') if error else theme.c('text_sub')
        self.message.setStyleSheet(f"color: {color}; font-size: {theme.fs('meta')}px;")

    def submit(self):
        query = self.input.text().strip()
        kind = self.state.contact_kind(query)
        if kind != self.mode:
            self._show_message('전화번호 형식이 올바르지 않습니다. (예: 010-1234-5678)' if self.mode == 'phone'
                               else '이메일 형식이 올바르지 않습니다. (예: name@company.com)', error=True)
            return False
        self.card.hide()
        self._show_message('찾는 중…')
        err = self.state.add_friend(query)
        if err:
            self._show_message(err, error=True)
            return False
        return True

    def _on_added(self, user, already):
        self.found = user['username']
        self.card_avatar.setPixmap(user_pixmap(self.state, user['username'], theme.px(48)))
        self.card_name.setText(self.state.display_name(user['username']))
        org = self.state.org_text(user['username'])
        self.card_sub.setText(org or user.get('status_message') or '')
        self.card.show()
        self._show_message('이미 친구로 등록되어 있어요.' if already else '친구로 추가했어요! 🎉')

    def _on_error(self, text):
        if self.isVisible() or self.message.text() == '찾는 중…':
            self._show_message(text, error=True)

    def _chat(self):
        if self.found:
            self.state.open_direct(self.found)
            self.accept()


# ================================================================ 프로필 보기
class ProfileDialog(QDialog):
    """친구 프로필 카드 - 큰 사진, 이름, 소속, 상태메시지, 생년월일, 1:1 채팅"""

    def __init__(self, state, username, parent=None):
        super().__init__(parent)
        self.state = state
        self.username = username
        user = state.user(username)
        self.setWindowTitle(f"{state.display_name(username)} 프로필")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(8)
        avatar = Avatar(size=104)
        avatar.setPixmap(user_pixmap(state, username, 104, show_presence=True))
        layout.addWidget(avatar, 0, Qt.AlignHCenter)
        layout.addSpacing(6)

        name = QLabel(state.display_name(username))
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(f"font-size: {theme.fs('headline')}px; font-weight: bold;")
        layout.addWidget(name)

        path = user.get('org_path') or ([user['department']] if user.get('department') else [])
        org = ' · '.join(x for x in (user.get('company'), ' › '.join(path), user.get('title')) if x)
        sub = []
        if org:
            sub.append(org)
        presence = presence_of(state, username)
        sub.append(PRESENCE_LABELS.get(presence, ''))
        info = QLabel('\n'.join(sub))
        info.setAlignment(Qt.AlignCenter)
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
        layout.addWidget(info)

        status = QLabel(user.get('status_message') or '상태메시지가 없습니다')
        status.setAlignment(Qt.AlignCenter)
        status.setWordWrap(True)
        status.setStyleSheet(f"color: {theme.c('text') if user.get('status_message') else theme.c('text_hint')};"
                             f" font-size: {theme.fs('body')}px; padding: 6px 0;")
        layout.addWidget(status)

        self.birthday_label = None
        if user.get('birthday'):
            bday = self.birthday_label = QLabel(f"🎂 {birthday_text(user['birthday'])}")
            bday.setAlignment(Qt.AlignCenter)
            bday.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
            layout.addWidget(bday)

        # 연락처 카드 (조직도에서 가져온 내선·휴대폰·계정, 겸직)
        contacts = [('내선', user.get('extension')), ('휴대폰', user.get('mobile')), ('계정', user.get('account'))]
        concurrent = [' › '.join(c.get('org_path') or []) + (f" ({c['title']})" if c.get('title') else '')
                      for c in (user.get('concurrent') or []) if c.get('org_path')]
        from client.kakao.deputies import deputy_summary
        deputies = deputy_summary(state, user.get('deputies'))
        rows = [(k, v) for k, v in contacts if v] + [('겸직', v) for v in concurrent] + \
            ([('대직자', deputies)] if deputies else [])
        if rows:
            card = QFrame()
            card.setObjectName('contactCard')
            card.setStyleSheet(f"QFrame#contactCard {{ background: {theme.c('bg_alt')}; border-radius: 10px; }}")
            grid = QGridLayout(card)
            grid.setContentsMargins(14, 10, 14, 10)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(5)
            self.contact_labels = {}
            for i, (key, value) in enumerate(rows):
                k = QLabel(key)
                k.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px; background: transparent;")
                v = QLabel(value)
                v.setWordWrap(True)
                v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                v.setStyleSheet(f"color: {theme.c('text')}; font-size: {theme.fs('meta')}px; background: transparent;")
                grid.addWidget(k, i, 0, Qt.AlignTop)
                grid.addWidget(v, i, 1)
                self.contact_labels.setdefault(key, v)
            grid.setColumnStretch(1, 1)
            layout.addSpacing(4)
            layout.addWidget(card)

        layout.addSpacing(10)
        self.chat_button = QPushButton('나와의 채팅' if username == state.me else '1:1 채팅')
        self.chat_button.setProperty('primary', True)
        self.chat_button.setIcon(make_icon('chat', theme.c('on_accent'), 18))
        self.chat_button.setMinimumHeight(theme.px(38))
        layout.addWidget(self.chat_button)
        self.chat_button.clicked.connect(self._chat)

        # 숨김 / 차단 (내 프로필·AI 비서 제외)
        self.hide_button = self.block_button = None
        if username != state.me and not user.get('is_bot'):
            actions = QHBoxLayout()
            actions.setSpacing(6)
            registered = getattr(state, 'is_registered_friend', lambda _n: False)(username)
            self.friend_button = QPushButton('친구 삭제' if registered else '친구 추가')
            self.friend_button.setProperty('flat', True)
            self.friend_button.setCursor(Qt.PointingHandCursor)
            self.friend_button.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
            actions.addWidget(self.friend_button, 1)
            self.friend_button.clicked.connect(self.toggle_friend)
            self.hide_button = QPushButton('숨김 해제' if state.is_hidden(username) else '숨기기')
            self.block_button = QPushButton('차단 해제' if state.is_blocked(username) else '차단')
            for btn in (self.hide_button, self.block_button):
                btn.setProperty('flat', True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
                actions.addWidget(btn, 1)
            layout.addLayout(actions)
            self.hide_button.clicked.connect(self.toggle_hidden)
            self.block_button.clicked.connect(self.toggle_blocked)

        # 관리자: 프로필 아무 곳이나 우클릭 → 정보 수정(편집 모드)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def context_menu(self):
        """관리 범위 안의 사람이면 '정보 수정 (편집 모드)…' 메뉴, 아니면 None"""
        from client.kakao.admin import can_edit_member, EDIT_ACTION_TEXT
        if not can_edit_member(self.state, self.username):
            return None
        menu = QMenu(self)
        menu.addAction(EDIT_ACTION_TEXT, self.start_edit)
        return menu

    def _show_context_menu(self, pos):
        menu = self.context_menu()
        if menu is not None:
            menu.exec_(self.mapToGlobal(pos))

    def start_edit(self, exec_=True):
        """편집 모드: 구성원 창을 열고, 저장하면 (바뀐 정보로 다시 보도록) 프로필을 닫는다"""
        from client.kakao.admin import edit_member_info, submit_member_edit
        dlg = edit_member_info(self.state, self.username, self, exec_=False)
        if dlg is None or not exec_:
            return dlg
        if dlg.exec_() == QDialog.Accepted and submit_member_edit(self.state, self.username, dlg, self.parent()):
            self.accept()
        return dlg

    def _chat(self):
        self.state.open_direct(self.username)
        self.accept()

    def toggle_hidden(self):
        hide_friend(self.state, self.username, not self.state.is_hidden(self.username))
        self.accept()

    def toggle_friend(self):
        """프로필에서 친구 추가/삭제 - 상대의 전화번호·이메일을 알 필요 없이 바로 등록"""
        if self.state.is_registered_friend(self.username):
            self.state.remove_friend(self.username)
        else:
            self.state.add_friend_user(self.username)
        self.accept()

    def toggle_blocked(self):
        if self.state.is_blocked(self.username):
            self.state.block_user(self.username, False)
            self.accept()
        elif confirm_block(self.state, self.username, self):
            self.accept()


# ================================================================ 행 위젯
ROW_AVATAR = theme.AVATAR_ROW      # 친구 탭 모든 행(내 프로필 · AI 비서 · 친구)의 아바타 크기 - 테마 한 곳에서 정한다


class MyProfileRow(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        me = state.user(state.me)
        row = QHBoxLayout(self)
        # 내 프로필도 아래 친구 · AI 비서 행과 같은 아바타 크기 · 간격 (예전 58px 은 혼자 커 보였다, 2026-10-10)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(12)
        size = theme.px(ROW_AVATAR)
        self.avatar = Avatar(size=size)
        self.avatar.setPixmap(user_pixmap(state, state.me, size))
        row.addWidget(self.avatar, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch(1)
        self.name = ElidedLabel(state.display_name(state.me), 'emphasis', 'text', bold=True)
        col.addWidget(self.name)
        self.status = ElidedLabel(me.get('status_message') or '상태메시지를 입력해 주세요', 'meta',
                                  'text_sub' if me.get('status_message') else 'text_hint')
        col.addWidget(self.status)
        col.addStretch(1)
        row.addLayout(col, 1)
        self.presence_button = PresenceButton(state)
        row.addWidget(self.presence_button, 0, Qt.AlignVCenter)


class FriendRow(QWidget):
    def __init__(self, state, username, kind='friend', on_gift=None, parent=None):
        super().__init__(parent)
        self.username = username
        user = state.user(username)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 7, 16, 7)
        row.setSpacing(12)
        size = theme.px(ROW_AVATAR)
        self.avatar = Avatar(size=size)
        self.avatar.setPixmap(user_pixmap(state, username, size, show_presence=(kind != 'bot')))
        row.addWidget(self.avatar, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch(1)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name = ElidedLabel(state.display_name(username), 'emphasis', 'text', bold=True)
        self.name.setMinimumWidth(min(self.name.sizeHint().width(), theme.px(70)))
        name_row.addWidget(self.name)
        # 내 부서: '강태영 책임' 처럼 이름 옆에 직함 (둘째 줄은 팀)
        self.title_label = None
        if kind == 'team' and user.get('title'):
            self.title_label = ElidedLabel(user['title'], 'emphasis', 'text_sub')
            self.title_label.setMinimumWidth(min(self.title_label.sizeHint().width(), theme.px(36)))
            name_row.addWidget(self.title_label)
        self.company = foreign_company(state, username)
        if self.company:
            name_row.addWidget(CompanyChip(self.company))
        name_row.addStretch(1)
        col.addLayout(name_row)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        presence = presence_of(state, username)
        self.presence_label = None
        if kind != 'bot' and presence in ('away', 'busy', 'meeting', 'vacation'):
            self.presence_label = QLabel(PRESENCE_LABELS[presence])
            self.presence_label.setStyleSheet(
                f"color: {PRESENCE_COLORS[presence]}; font-size: {theme.fs('caption')}px; font-weight: bold;")
            sub_row.addWidget(self.presence_label)
        self.sub = None
        if kind == 'bot':
            self.sub = ElidedLabel(BOT_HINT, 'meta', 'text_sub')
        elif kind == 'team':
            # 업무 정보: 팀(없으면 부서) · 지금 진행 중인 일정. 직함은 이름 옆
            path = user.get('org_path') or []
            parts = [path[-1] if path else (user.get('department') or '')]
            ev = state.current_event_of(username) if hasattr(state, 'current_event_of') else None
            if ev:
                end = (ev.get('end') or '')[11:16]
                parts.append(f"일정 중 ~{end}" if end else '일정 중')
            text = ' · '.join(p for p in parts if p)
            if text:
                self.sub = ElidedLabel(text, 'meta', 'text_sub')
        elif kind == 'birthday':
            self.sub = ElidedLabel('🎂 오늘 생일이에요', 'meta', 'text_sub')
        if self.sub is not None:
            sub_row.addWidget(self.sub, 1)
        else:
            sub_row.addStretch(1)
        if self.sub is not None or self.presence_label is not None:
            col.addLayout(sub_row)
        col.addStretch(1)
        row.addLayout(col, 1)

        self.status = None
        self.gift_button = None
        if kind == 'birthday':
            self.gift_button = QPushButton('선물하기')
            self.gift_button.setCursor(Qt.PointingHandCursor)
            self.gift_button.setStyleSheet(
                f"QPushButton {{ background: {theme.c('bg')}; border: 1px solid {theme.c('border')};"
                f" border-radius: {theme.px(10)}px; padding: 4px 10px; font-size: {theme.fs('meta')}px; }}"
                f" QPushButton:hover {{ background: {theme.c('bg_hover')}; }}")
            if on_gift:
                self.gift_button.clicked.connect(lambda: on_gift(username))
            row.addWidget(self.gift_button, 0, Qt.AlignVCenter)
        elif kind != 'bot' and user.get('status_message'):
            self.status = StatusPill(user['status_message'])
            row.addWidget(self.status, 0, Qt.AlignVCenter)
        self.picked = False
        self.setObjectName('friendRow')
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set_picked(self, picked):
        """Shift+클릭 · Shift+끌기로 대화상대에 고른 행 - 선택 바탕으로 칠한다"""
        if picked == self.picked:
            return
        self.picked = picked
        self.setStyleSheet(f"QWidget#friendRow {{ background: {theme.c('bg_selected')}; }}" if picked else '')
        self.setAccessibleDescription('대화상대로 선택됨' if picked else '')


class SectionHeader(QWidget):
    """'즐겨찾기 3 ▾' 형태의 접기 가능한 구역 머리글"""

    def __init__(self, title, count, collapsed, parent=None):
        super().__init__(parent)
        self.collapsed = collapsed
        row = QHBoxLayout(self)
        # 위아래 여백을 같게: 줄 전체가 눌림·마우스 강조 영역이므로 글자가 그 가운데에 와야 한다
        row.setContentsMargins(theme.GUTTER, theme.sp('m'), theme.GUTTER, theme.sp('m'))
        row.setSpacing(theme.sp('xs') + 1)
        self.title = ElidedLabel(title, 'meta', 'text_sub', bold=True)
        self.title.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.title.setFixedWidth(self.title.sizeHint().width())
        self.count = ElidedLabel(str(count), 'meta', 'text_hint')
        row.addWidget(self.title)
        row.addWidget(self.count, 1)
        self.chevron = QLabel()
        self.chevron.setPixmap(self._chevron_pixmap())
        row.addWidget(self.chevron)

    def _chevron_pixmap(self):
        from PyQt5.QtGui import QPixmap, QPen
        size = 12
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(theme.qc('text_hint'), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        if self.collapsed:
            p.drawPolyline(*_pts([(4, 2.5), (8, 6), (4, 9.5)]))
        else:
            p.drawPolyline(*_pts([(2.5, 4), (6, 8), (9.5, 4)]))
        p.end()
        return pm

def _pts(points):
    from PyQt5.QtCore import QPointF
    return [QPointF(x, y) for x, y in points]


# ================================================================ 친구 탭
class FriendsTab(QWidget):
    def __init__(self, state, settings, parent=None):
        super().__init__(parent)
        self.state = state
        self.settings = settings
        self.collapsed = {'offline'}
        self.sections = {}
        self.picked = {}            # Shift+클릭 · Shift+끌기로 고른 대화상대 (고른 순서 유지)
        self._pick_drag = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = TabHeader('친구')
        self.search_button = self.header.add_button('search', '이름, 상태메시지 검색')
        self.add_friend_button = self.header.add_button('friends', '친구 추가 (전화번호 · 이메일)')
        self.add_friend_button.refresh_icon = lambda color=None, b=None: _set_add_friend_icon(self.add_friend_button)
        _set_add_friend_icon(self.add_friend_button)
        self.group_button = self.header.add_button('add_chat', '그룹채팅 만들기')
        layout.addWidget(self.header)

        self.search_edit = self.header.add_search(make_search_edit('이름, 상태메시지 검색'))

        self.story_bar = StoryBar(state)
        layout.addWidget(self.story_bar)
        self.story_bar.setVisible(bool(settings and settings.get('social_features')))
        self.story_line = QFrame()
        self.story_line.setFixedHeight(1)
        layout.addWidget(self.story_line)

        self.list = RowList()
        layout.addWidget(self.list, 1)

        # 여러 명 고르기: 'N명 선택 · 이름…  [선택 해제] [대화방 만들기]' (고른 사람이 있을 때만)
        self.pick_bar = QFrame()
        self.pick_bar.setObjectName('pickBar')
        bar = QHBoxLayout(self.pick_bar)
        bar.setContentsMargins(theme.GUTTER, theme.sp('s'), theme.sp('m'), theme.sp('s'))
        bar.setSpacing(theme.sp('xs'))
        self.pick_count = ElidedLabel('', 'body', 'text', bold=True)
        self.pick_count.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.pick_names = ElidedLabel('', 'meta', 'text_sub')
        self.pick_clear_button = QPushButton('선택 해제')
        self.pick_clear_button.setObjectName('pickClear')
        self.pick_chat_button = QPushButton('대화방 만들기')
        self.pick_chat_button.setObjectName('pickChat')
        for btn in (self.pick_clear_button, self.pick_chat_button):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setAccessibleName(btn.text())
        bar.addWidget(self.pick_count, 0, Qt.AlignVCenter)
        bar.addWidget(self.pick_names, 1, Qt.AlignVCenter)
        bar.addWidget(self.pick_clear_button, 0, Qt.AlignVCenter)
        bar.addWidget(self.pick_chat_button, 0, Qt.AlignVCenter)
        self.pick_bar.hide()
        layout.addWidget(self.pick_bar)
        self.pick_clear_button.clicked.connect(lambda _c=False: self.clear_picks())
        self.pick_chat_button.clicked.connect(lambda _c=False: self.create_from_picks())
        # 끌다가 목록 위/아래 끝에 닿으면 저절로 넘겨 끝까지 고를 수 있게
        self._pick_scroll = QTimer(self)
        self._pick_scroll.setInterval(30)
        self._pick_scroll.timeout.connect(self._pick_autoscroll)
        self.list.viewport().installEventFilter(self)
        self.list.installEventFilter(self)

        self.search_button.clicked.connect(self.toggle_search)
        self.group_button.clicked.connect(self.show_create_dialog)
        # clicked 는 checked(False)를 넘기므로 감싸서 부른다 (그대로 연결하면 exec_=False 가 되어 창이 안 뜬다)
        self.add_friend_button.clicked.connect(lambda _checked=False: self.show_add_friend())
        if hasattr(state, 'my_friends_changed'):
            state.my_friends_changed.connect(self.refresh)
        self.search_edit.textChanged.connect(lambda _t: self.refresh())
        self.list.row_activated.connect(self._activate)
        self.list.itemClicked.connect(self._clicked)
        self.list.customContextMenuRequested.connect(self._show_context_menu)

        state.users_changed.connect(self.refresh)
        state.user_changed.connect(lambda _u: self.refresh())
        settings.changed.connect(self._on_setting)
        connect_avatar_changed(state, self.refresh)
        self.apply_theme()

    # ---- 데이터 ----
    def _matches(self, user, query):
        if not query:
            return True
        query = query.lower()
        name = self.state.display_name(user['username']).lower()
        return (query in name or query in user['username'].lower()
                or query in (user.get('status_message') or '').lower())

    def build_sections(self, query=''):
        """구역 키 → 사용자명 목록 (검색어 적용)"""
        state = self.state
        friends = [u for u in state.friends() if self._matches(u, query)]
        bots = [u['username'] for u in state.friends(include_bots=True)
                if u.get('is_bot') and self._matches(u, query)] if _supports_bots(state) else []
        favorites = [u['username'] for u in friends if state.is_favorite(u['username'])]
        mine = set(getattr(state, 'my_friends', []) or [])
        registered = [u['username'] for u in friends if u['username'] in mine and u['username'] not in favorites]
        # 업무 우선: 내 부서 동료를 맨 위에 (회사·부서가 같은 사람)
        my_team = state.team_of(state.me) if hasattr(state, 'team_of') else ('', '')
        team = [u['username'] for u in friends if my_team[1] and hasattr(state, 'team_of')
                and state.team_of(u['username']) == my_team
                and u['username'] not in favorites and u['username'] not in registered]
        others = [u for u in friends if u['username'] not in favorites and u['username'] not in registered
                  and u['username'] not in team]
        social = bool(self.settings and self.settings.get('social_features'))
        birthdays = [u['username'] for u in state.birthday_friends() if self._matches(u, query)] if social else []
        # '온라인' 은 지금 접속해 있는 모든 친구 (내 부서 · 즐겨찾기 · 내가 추가한 친구에도 있는 사람 포함 - 헤더의
        # '온라인 n명' 과 같은 사람들). 2026-10-15 전에는 다른 구역에 든 사람을 빼서 같은 부서 동료가 접속해도
        # '온라인' 에 보이지 않았다. '오프라인' 은 다른 구역에 없는 사람만
        return {
            'bot': bots,
            'team': team,
            'birthday': birthdays,
            'favorites': favorites,
            'registered': registered,
            'online': [u['username'] for u in friends if presence_of(state, u['username']) != 'offline'],
            'offline': [u['username'] for u in others if presence_of(state, u['username']) == 'offline'],
        }

    def section_members(self, key):
        return list(self.sections.get(key, []))

    def toggle_search(self):
        visible = self.search_edit.isHidden()
        self.search_edit.setVisible(visible)
        if visible:
            self.search_edit.setFocus()
        else:
            self.search_edit.clear()

    def toggle_section(self, key):
        if key in self.collapsed:
            self.collapsed.discard(key)
        else:
            self.collapsed.add(key)
        self.refresh()

    def refresh(self):
        if not self.state.me:
            return
        friends = self.state.friends()
        online = sum(1 for f in friends if self.state.is_online(f['username']))
        self.header.set_subtitle(f'친구 {len(friends)}명 · 온라인 {online}명')
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()
        query = self.search_edit.text().strip()
        self.sections = self.build_sections(query)
        if not query:
            self.list.add_row(MyProfileRow(self.state), ('me', self.state.me))
        for key in ('bot', 'team', 'favorites', 'registered', 'birthday', 'online', 'offline'):
            members = self.sections[key]
            if not members and (query or key in ('bot', 'team', 'birthday', 'favorites', 'registered')):
                continue
            collapsed = key in self.collapsed and not query
            self.list.add_row(SectionHeader(SECTION_TITLES[key], len(members), collapsed),
                              ('section', key), selectable=False)
            if collapsed:
                continue
            kind = {'bot': 'bot', 'birthday': 'birthday', 'team': 'team'}.get(key, 'friend')
            for name in members:
                self.list.add_row(FriendRow(self.state, name, kind, on_gift=self.send_gift),
                                  ('friend', name, key))
        self.list.verticalScrollBar().setValue(scroll)
        # 친구가 아니게 된 사람은 고른 목록에서 뺀다 (검색으로 가려진 사람은 그대로 둔다)
        friends_now = {f['username'] for f in friends}
        self.picked = {n: True for n in self.picked if n in friends_now}
        self._apply_picks()

    # ---- 여러 명 골라 대화방 만들기 (Shift+클릭 · Shift+끌기) ----
    @staticmethod
    def _pickable(data):
        """대화상대로 고를 수 있는 행이면 사용자명 (AI 비서 · 내 프로필 · 구역 머리글 제외)"""
        if data and data[0] == 'friend' and tuple(data[2:3]) != ('bot',):
            return data[1]
        return None

    def picked_members(self):
        return list(self.picked)

    def toggle_pick(self, username):
        if username in self.picked:
            del self.picked[username]
        else:
            self.picked[username] = True
        self._apply_picks()

    def clear_picks(self):
        self.picked = {}
        self._apply_picks()

    def pick_range(self, first, last, base=None):
        """목록 행 first~last(순서 무관) 사이 친구를 모두 고른다. 끌기 범위 안의 접힌 구역은
        구성원 전체를 넣는다 (기본으로 접힌 '오프라인'까지 끝까지 끌면 모두 선택)"""
        lo, hi = sorted((first, last))
        picked = dict(self.picked if base is None else base)
        query = self.search_edit.text().strip()
        for i in range(max(lo, 0), min(hi, self.list.count() - 1) + 1):
            data = self.list.item(i).data(Qt.UserRole)
            name = self._pickable(data)
            if name:
                picked[name] = True
            elif data and data[0] == 'section' and data[1] != 'bot' and data[1] in self.collapsed and not query:
                for member in self.sections.get(data[1], []):
                    picked[member] = True
        self.picked = picked
        self._apply_picks()

    def _apply_picks(self):
        for item in self.list.items():
            widget = self.list.itemWidget(item)
            if isinstance(widget, FriendRow):
                widget.set_picked(widget.username in self.picked)
        names = list(self.picked)
        self.pick_bar.setVisible(bool(names))
        if not names:
            return
        shown = [self.state.display_name(n) for n in names[:3]]
        rest = f' 외 {len(names) - 3}명' if len(names) > 3 else ''
        self.pick_count.setText(f'{len(names)}명 선택')
        self.pick_count.setFixedWidth(self.pick_count.sizeHint().width())
        self.pick_names.setText(', '.join(shown) + rest)
        self.pick_names.setToolTip(', '.join(self.state.display_name(n) for n in names))
        # 한 명이면 1:1 채팅, 두 명 이상이면 그룹 대화방 (CreateGroupDialog 와 같은 규칙)
        self.pick_chat_button.setText('1:1 채팅' if len(names) == 1 else '대화방 만들기')
        self.pick_chat_button.setAccessibleName(self.pick_chat_button.text())

    def create_from_picks(self, exec_=True, also=None):
        """고른 사람(+ also)이 미리 체크된 대화상대 선택 창 → 확인하면 방 이름을 정해 대화방을 만든다"""
        if also:
            self.picked[also] = True
            self._apply_picks()
        if not self.picked:
            return None
        dlg = CreateGroupDialog(self.state, self.window(), preselected=list(self.picked))
        if exec_ and dlg.exec_() == QDialog.Accepted:
            self.clear_picks()
        return dlg

    def _row_at(self, pos):
        """뷰포트 좌표의 행 번호. 목록 위/아래 밖이면 맨 처음/맨 끝 행"""
        count = self.list.count()
        if not count:
            return -1
        vp = self.list.viewport()
        y = min(max(pos.y(), 0), vp.height() - 1)
        item = self.list.itemAt(QPoint(max(0, min(pos.x(), vp.width() - 1)), y))
        if item is not None:
            return self.list.row(item)
        last = self.list.item(count - 1)
        return count - 1 if y >= self.list.visualItemRect(last).top() else 0

    def _pick_drag_to(self, pos):
        drag = self._pick_drag
        drag['pos'] = pos
        self.pick_range(drag['anchor'], self._row_at(pos), base=drag['base'])

    def _pick_autoscroll(self):
        drag = self._pick_drag
        if not drag or not drag.get('moved'):
            self._pick_scroll.stop()
            return
        bar = self.list.verticalScrollBar()
        y, h, edge = drag['pos'].y(), self.list.viewport().height(), theme.px(24)
        step = 0
        if y < edge:
            step = -max(4, (edge - y) // 2)
        elif y > h - edge:
            step = max(4, (y - (h - edge)) // 2)
        if not step:
            self._pick_scroll.stop()
            return
        bar.setValue(bar.value() + step)
        self._pick_drag_to(drag['pos'])

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self.list:
            if kind == QEvent.KeyPress and event.key() == Qt.Key_Escape and self.picked:
                self.clear_picks()                       # Esc → 고른 사람 모두 해제
                return True
            return False
        if obj is not self.list.viewport():
            return False
        # 빠르게 두 번 누르면 두 번째 누름은 DblClick 으로 온다 - Shift 중이면 1:1 방 대신 한 번 더 넣고 빼기
        if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick) and event.button() == Qt.LeftButton:
            item = self.list.itemAt(event.pos())
            name = self._pickable(item.data(Qt.UserRole) if item else None)
            mods = event.modifiers()
            if name and mods & (Qt.ShiftModifier | Qt.ControlModifier):
                # Shift(·Ctrl/⌘)+누름: 놓으면 한 명씩 넣고 빼기, 누른 채 끌면 그 사이 전부 선택
                self._pick_drag = {'anchor': self.list.row(item), 'name': name, 'start': event.pos(),
                                   'pos': event.pos(), 'base': dict(self.picked), 'moved': False}
                self.list.setFocus(Qt.MouseFocusReason)
                return True
            if name and self.picked:
                self.clear_picks()                       # 그냥 누르면 여러 명 선택은 풀린다
            return False
        if self._pick_drag is None:
            return False
        if kind == QEvent.MouseMove and event.buttons() & Qt.LeftButton:
            drag = self._pick_drag
            if not drag['moved']:
                if (event.pos() - drag['start']).manhattanLength() < QApplication.startDragDistance():
                    return True
                drag['moved'] = True
            self._pick_drag_to(event.pos())
            if not self._pick_scroll.isActive():
                self._pick_scroll.start()
            return True
        if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            drag, self._pick_drag = self._pick_drag, None
            self._pick_scroll.stop()
            if not drag['moved']:
                self.toggle_pick(drag['name'])
            return True
        return False

    # ---- 동작 ----
    def _on_setting(self, key):
        if key == 'favorites':
            self.refresh()
        elif key == 'social_features':
            self.story_bar.setVisible(bool(self.settings.get('social_features')))
            self.refresh()

    def _clicked(self, item):
        data = item.data(Qt.UserRole)
        if data[0] == 'section':
            self.toggle_section(data[1])
        elif data[0] == 'me':
            self.edit_profile()

    def _activate(self, item):
        data = item.data(Qt.UserRole)
        if data and data[0] == 'friend':
            self.state.open_direct(data[1])

    def send_gift(self, username):
        # 선물하기 결제는 범위 밖 - 1:1 채팅방만 연다
        self.state.open_direct(username)

    def edit_profile(self):
        ProfileEditDialog(self.state, self.window()).exec_()

    def show_profile(self, username):
        ProfileDialog(self.state, username, self.window()).exec_()

    def show_create_dialog(self):
        CreateGroupDialog(self.state, self.window()).exec_()

    def show_add_friend(self, exec_=True):
        dlg = AddFriendDialog(self.state, self.window())
        if exec_:
            dlg.exec_()
        return dlg

    def _add_pick_actions(self, menu, name=None):
        """우클릭으로 대화상대 고르기 · 고른 사람들과 대화방 만들기 (Shift+클릭과 같은 선택 목록)"""
        picked = list(self.picked)
        if name and name not in self.picked and picked:
            # 고른 사람들 + 우클릭한 사람으로 바로 방 만들기
            menu.addAction(f'선택한 {len(picked)}명 + {self.state.display_name(name)} 대화방 만들기…',
                           lambda: self.create_from_picks(also=name))
        elif picked:
            menu.addAction(f'선택한 {len(picked)}명과 대화방 만들기…' if len(picked) > 1
                           else f'{self.state.display_name(picked[0])}님과 1:1 채팅',
                           lambda: self.create_from_picks())
        if name:
            menu.addAction('선택에서 빼기' if name in self.picked else '대화상대로 선택 (Shift+클릭)',
                           lambda: self.toggle_pick(name))
        if picked:
            menu.addAction('선택 해제', self.clear_picks)
        if not menu.isEmpty():
            menu.addSeparator()

    def context_menu_for(self, data):
        menu = QMenu(self)
        if data is None or data[0] == 'section':
            # 빈 곳 · 구역 머리글 우클릭: 고른 사람이 있을 때만 대화방 만들기 메뉴
            if not self.picked:
                return None
            self._add_pick_actions(menu)
            return menu
        if data[0] == 'me':
            menu.addAction('프로필 편집', self.edit_profile)
            menu.addAction('나와의 채팅', lambda: self.state.open_direct(self.state.me))
            self._add_admin_edit(menu, self.state.me)
            return menu
        if data[0] != 'friend':
            return None
        name = data[1]
        if self._pickable(data) and not self.state.user(name).get('is_bot'):
            self._add_pick_actions(menu, name)
        menu.addAction('1:1 채팅', lambda: self.state.open_direct(name))
        if not self.state.user(name).get('is_bot'):
            fav = self.state.is_favorite(name)
            menu.addAction('즐겨찾기 해제' if fav else '즐겨찾기 추가',
                           lambda: self.settings.toggle_in('favorites', name))
        menu.addAction('채팅방에 초대…', lambda: InviteToRoomDialog(self.state, name, self.window()).exec_())
        menu.addAction('프로필 보기', lambda: self.show_profile(name))
        if self.state.stories_of(name) and not self.state.is_story_hidden(name):
            menu.addAction('펑 보기', lambda: self.story_bar.open_story(name))
        if self.settings.get('social_features') and not self.state.user(name).get('is_bot'):
            # 친구 소식 숨기기 (카카오톡 2026 2차 업데이트): 친구 관계는 그대로, 내 화면에서만 감춤
            hidden = self.state.is_story_hidden(name)
            menu.addAction('소식 다시 보기' if hidden else '소식 숨기기',
                           lambda: self.state.set_story_hidden(name, not hidden))
        if not self.state.user(name).get('is_bot'):
            menu.addSeparator()
            if getattr(self.state, 'is_registered_friend', lambda _n: False)(name):
                menu.addAction('친구 삭제', lambda: self.state.remove_friend(name))
            menu.addAction('숨기기', lambda: hide_friend(self.state, name, True))
            menu.addAction('차단', lambda: confirm_block(self.state, name, self.window()))
        self._add_admin_edit(menu, name)
        return menu

    def _add_admin_edit(self, menu, name):
        """관리자: 우클릭 → 정보 수정(편집 모드) (명세 §4.3)"""
        from client.kakao.admin import can_edit_member, edit_member_info, EDIT_ACTION_TEXT
        if can_edit_member(self.state, name):
            menu.addSeparator()
            menu.addAction(EDIT_ACTION_TEXT, lambda: edit_member_info(self.state, name, self.window()))

    def _show_context_menu(self, pos):
        item = self.list.itemAt(pos)
        menu = self.context_menu_for(item.data(Qt.UserRole) if item is not None else None)
        if menu is not None:
            menu.exec_(self.list.viewport().mapToGlobal(pos))

    def apply_theme(self):
        self.header.apply_theme()
        self.search_edit.setStyleSheet(search_box_qss())
        self.story_bar.apply_theme()
        self.story_line.setStyleSheet(f"background: {theme.c('border')}; margin: 0 16px;")
        self.pick_bar.setStyleSheet(
            f"QFrame#pickBar {{ background: {theme.c('bg')}; border-top: 1px solid {theme.c('border')}; }}"
            + theme.button_qss('pickClear', 'ghost', size='meta', pad='6px 10px')
            + theme.button_qss('pickChat', 'primary', size='meta', pad='6px 12px'))
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"FriendsTab {{ background: {theme.c('bg')}; }}")
        self.refresh()


def _supports_bots(state):
    try:
        state.friends(include_bots=True)
        return True
    except TypeError:
        return False


__all__ = ['FriendsTab', 'ProfileEditDialog', 'ProfileDialog', 'PresenceButton', 'presence_menu',
           'avatar_png_from_path', 'QMessageBox', 'StoryBar', 'StoryBubble', 'hide_friend', 'confirm_block']
