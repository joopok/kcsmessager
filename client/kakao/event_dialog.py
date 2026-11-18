# -*- coding: utf-8 -*-
"""
일정 대화상자
- EventDialog: 일정 만들기/수정 (event=None → 생성, event=dict → 수정)
- EventDetailDialog: 일정 상세(읽기 전용) + 참석 응답 + 수정 진입

exec_() 없이도 테스트할 수 있도록 build_payload()/changed_fields()/accept()/request_delete()를 노출한다.
"""

from datetime import datetime, date, time, timedelta

from PyQt5.QtCore import Qt, QDate, QDateTime, QTime, QLocale, QSize, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
                             QCheckBox, QDateTimeEdit, QPlainTextEdit, QComboBox, QPushButton,
                             QListWidget, QListWidgetItem, QAbstractButton, QButtonGroup,
                             QMessageBox, QWidget, QScrollArea, QFrame, QSizePolicy)

from client.kakao import theme
from client.kakao.state import parse_dt
from client.kakao import widgets
from client.kakao.widgets import make_icon, avatar_pixmap, paint_icon
from client.kakao.calendar_tab import (range_text, RSVP_LABELS, rsvp_colors, readable_text_on,
                                       dropdown_qss, checkbox_qss, icon_pixmap)

REMIND_OPTIONS = [('없음', None), ('정시', 0), ('5분 전', 5), ('10분 전', 10),
                  ('30분 전', 30), ('1시간 전', 60), ('1일 전', 1440)]

KOREAN = QLocale(QLocale.Korean, QLocale.SouthKorea)


def _dialog_qss():
    c = theme.c
    return f"""
        QDialog {{ background: {c('bg')}; }}
        QLabel {{ color: {c('text')}; background: transparent; }}
        QLabel[role="field"] {{ color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
        QLabel[role="heading"] {{ font-size: {theme.fs('heading')}px; font-weight: bold; }}
        QLabel[role="error"] {{ color: {c('calendar_sun')}; font-size: {theme.fs('caption')}px; }}
        QLabel[role="sub"] {{ color: {c('text_sub')}; }}
        QLineEdit, QPlainTextEdit, QDateTimeEdit, QComboBox {{
            background: {c('input_bg')}; color: {c('text')}; border: 1px solid {c('border')};
            border-radius: 6px; padding: 5px 8px; selection-background-color: {c('yellow')}; selection-color: {theme.c('on_accent')}; }}
        QLineEdit:focus, QPlainTextEdit:focus, QDateTimeEdit:focus, QComboBox:focus {{ border: 1px solid {c('yellow_dark')}; }}
        QLineEdit#titleEdit {{ font-size: {theme.fs('title')}px; padding: 8px 10px; }}
        QLineEdit#titleEdit[invalid="true"] {{ border: 1px solid {c('danger_text')}; }}
        QComboBox QAbstractItemView {{ background: {c('bg')}; color: {c('text')}; selection-background-color: {c('bg_selected')}; }}
        QCheckBox {{ color: {c('text')}; spacing: 6px; }}
        QPushButton {{ background: {c('bg_alt')}; color: {c('text')}; border: 1px solid {c('border')};
                       border-radius: 6px; padding: 7px 16px; }}
        QPushButton:hover {{ background: {c('bg_hover')}; }}
        QPushButton:disabled {{ color: {c('text_hint')}; }}
        QPushButton[primary="true"] {{ background: {c('yellow')}; border: none; color: {theme.c('on_accent')}; font-weight: bold; }}
        QPushButton[primary="true"]:hover {{ background: {c('yellow_dark')}; }}
        QPushButton[primary="true"]:disabled {{ background: {c('bg_selected')}; color: {c('text_hint')}; }}
        QPushButton[danger="true"] {{ background: transparent; border: none; color: {c('calendar_sun')}; padding: 7px 6px; }}
        QPushButton[danger="true"]:hover {{ text-decoration: underline; }}
        QPushButton[rsvp="true"] {{ border-radius: 16px; min-height: 32px; padding: 0 14px; }}
        QPushButton[rsvp="true"]:checked {{ background: {c('yellow')}; border-color: {c('yellow')}; color: {theme.c('on_accent')}; font-weight: bold; }}
        QListWidget {{ background: {c('input_bg')}; color: {c('text')}; border: 1px solid {c('border')}; border-radius: 6px; padding: 2px; }}
        QListWidget::item {{ padding: 3px 4px; border-radius: 4px; }}
        QListWidget::item:hover {{ background: {c('bg_hover')}; }}
        QListWidget::item:selected {{ background: {c('bg_selected')}; color: {c('text')}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c('bg')}; border: none; }}
        QFrame[role="sep"] {{ background: {c('border')}; max-height: 1px; min-height: 1px; border: none; }}
        {dropdown_qss()}
        {checkbox_qss()}
        {theme.scrollbar_qss()}
    """


def _field_label(text):
    lbl = QLabel(text)
    lbl.setProperty('role', 'field')
    return lbl


def _to_qdt(dt):
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute, dt.second))


def _from_qdt(qdt):
    d, t = qdt.date(), qdt.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), 0)


# ---------------------------------------------------------------- 색상 견본
class ColorSwatch(QAbstractButton):
    """둥근 색상 견본 (선택되면 바깥 링 + 체크)"""

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(color)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked():
            ring = QColor(self.color)
            if not theme.is_dark() and readable_text_on(self.color).name() == '#191919':
                ring = ring.darker(118)  # 밝은 색은 흰 배경에서 링이 잘 보이도록
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(r.adjusted(5, 5, -5, -5))
        if self.isChecked():
            paint_icon(p, 'check', r.adjusted(8, 8, -8, -8), readable_text_on(self.color), stroke=2.6)
        p.end()


# ---------------------------------------------------------------- 일정 만들기/수정
class EventDialog(QDialog):
    def __init__(self, state, event=None, room_id=None, default_date=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.event_data = event
        self.room_id = (event or {}).get('room_id') if event else room_id
        self.edit_mode = event is not None
        self._last_start = None
        self.setWindowTitle('일정 수정' if self.edit_mode else '일정 만들기')
        self.setModal(True)
        self.resize(420, 690)
        self._build(default_date)
        self.setStyleSheet(_dialog_qss())
        self._validate()

    # ---- UI
    def _build(self, default_date):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(22, 18, 22, 10)
        heading = QLabel('일정 수정' if self.edit_mode else '일정 만들기')
        heading.setProperty('role', 'heading')
        head.addWidget(heading)
        head.addStretch(1)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(22, 4, 22, 12)
        form.setSpacing(6)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # 제목
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName('titleEdit')
        self.title_edit.setPlaceholderText('일정 제목을 입력하세요 (필수)')
        self.title_edit.setAccessibleName('일정 제목 (필수)')
        self.title_edit.setMaxLength(100)
        self.title_edit.textChanged.connect(self._validate)
        form.addWidget(self.title_edit)
        # 제목 없이 [저장]을 누르면 왜 저장되지 않는지 바로 아래에 알려 준다
        # (예전에는 저장 버튼만 말없이 꺼져 있어 장소 · 메모까지 쓰고도 '등록이 안 된다'고 느꼈다)
        self.title_error = QLabel('일정 제목을 입력해 주세요.')
        self.title_error.setProperty('role', 'error')
        self.title_error.hide()
        form.addWidget(self.title_error)

        # 색상
        color_row = QHBoxLayout()
        color_row.setSpacing(2)
        color_row.setContentsMargins(0, 2, 0, 6)
        self.color_group = QButtonGroup(self)
        self.color_group.setExclusive(True)
        self.swatches = []
        for col in theme.EVENT_COLORS:
            sw = ColorSwatch(col)
            self.color_group.addButton(sw)
            self.swatches.append(sw)
            color_row.addWidget(sw)
        color_row.addStretch(1)
        form.addLayout(color_row)

        # 채팅방
        if self.room_id:
            room_row = QHBoxLayout()
            room_row.setSpacing(6)
            icon = QLabel()
            icon.setPixmap(make_icon('chat', theme.c('text_sub'), 16).pixmap(16, 16))
            self.room_label = QLabel(f'채팅방: {self.state.room_title(self.room_id) or self.room_id}')
            self.room_label.setProperty('role', 'sub')
            room_row.addWidget(icon)
            room_row.addWidget(self.room_label, 1)
            form.addLayout(room_row)
        else:
            self.room_label = None

        # 일시
        form.addWidget(self._sep())
        self.all_day_check = QCheckBox('종일')
        self.all_day_check.toggled.connect(self._on_all_day)
        form.addWidget(self.all_day_check)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.start_edit = QDateTimeEdit()
        self.end_edit = QDateTimeEdit()
        for i, (name, edit) in enumerate((('시작', self.start_edit), ('종료', self.end_edit))):
            edit.setCalendarPopup(True)
            edit.setLocale(KOREAN)
            edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid.addWidget(_field_label(name), i, 0)
            grid.addWidget(edit, i, 1)
        self.error_label = QLabel('')
        self.error_label.setProperty('role', 'error')
        self.error_label.hide()
        grid.addWidget(self.error_label, 2, 1)

        # 알림
        self.remind_combo = QComboBox()
        for label, value in REMIND_OPTIONS:
            self.remind_combo.addItem(label, value)
        grid.addWidget(_field_label('알림'), 3, 0)
        grid.addWidget(self.remind_combo, 3, 1)
        grid.setColumnStretch(1, 1)
        form.addLayout(grid)

        form.addWidget(self._sep())
        form.addWidget(_field_label('장소'))
        self.location_edit = QLineEdit()
        self.location_edit.setPlaceholderText('장소 입력')
        form.addWidget(self.location_edit)
        form.addWidget(_field_label('메모'))
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText('메모 입력')
        self.desc_edit.setFixedHeight(70)
        form.addWidget(self.desc_edit)

        # 참석자 (채팅방 일정이 아닐 때)
        self.attendee_list = None
        if not self.room_id:
            label = '참석자' if not self.edit_mode else '참석자 (수정 불가)'
            form.addWidget(_field_label(label))
            self.attendee_list = QListWidget()
            self.attendee_list.setIconSize(QSize(24, 24))
            self.attendee_list.setFixedHeight(132)
            existing = set((self.event_data or {}).get('attendees', {}) or {})
            for u in self.state.friends():
                name = u['username']
                if self.state.user(name).get('is_bot'):
                    continue
                text = self.state.display_name(name)
                company = other_company(self.state, name)
                item = QListWidgetItem(f'{text}  · {company}' if company else text)
                item.setData(Qt.UserRole, name)
                if company:
                    item.setToolTip(company)
                item.setIcon(make_avatar_icon(self.state, name))
                flags = Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
                if self.edit_mode:
                    flags = Qt.ItemIsUserCheckable  # 서버가 참석자 변경을 지원하지 않으므로 읽기 전용
                item.setFlags(flags)
                item.setCheckState(Qt.Checked if name in existing else Qt.Unchecked)
                self.attendee_list.addItem(item)
            form.addWidget(self.attendee_list)
        form.addStretch(1)

        # 하단 버튼
        footer = QWidget()
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(22, 10, 22, 16)
        fl.setSpacing(8)
        self.delete_btn = None
        if self.edit_mode and self.event_data.get('creator') == self.state.me:
            self.delete_btn = QPushButton('삭제')
            self.delete_btn.setProperty('danger', True)
            self.delete_btn.setCursor(Qt.PointingHandCursor)
            self.delete_btn.clicked.connect(lambda: self.request_delete())
            fl.addWidget(self.delete_btn)
        fl.addStretch(1)
        self.cancel_btn = QPushButton('취소')
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton('저장')
        self.save_btn.setProperty('primary', True)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.accept)
        fl.addWidget(self.cancel_btn)
        fl.addWidget(self.save_btn)
        outer.addWidget(footer)

        self._fill(default_date)
        self.start_edit.dateTimeChanged.connect(self._on_start_changed)
        self.end_edit.dateTimeChanged.connect(self._validate)

    def _sep(self):
        f = QFrame()
        f.setProperty('role', 'sep')
        f.setFrameShape(QFrame.NoFrame)
        return f

    def _fill(self, default_date):
        if self.edit_mode:
            ev = self.event_data
            self.title_edit.setText(ev.get('title', ''))
            start = parse_dt(ev.get('start')) or datetime.now()
            end = parse_dt(ev.get('end')) or start
            self.all_day_check.setChecked(bool(ev.get('all_day')))
            self.location_edit.setText(ev.get('location') or '')
            self.desc_edit.setPlainText(ev.get('description') or '')
            remind = ev.get('remind_minutes')
            color = ev.get('color') or theme.EVENT_COLORS[0]
        else:
            d = default_date or date.today()
            now = datetime.now()
            if d == now.date():
                start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                if start.date() != d:
                    start = datetime.combine(d, time(23, 0))
            else:
                start = datetime.combine(d, time(9, 0))
            end = start + timedelta(hours=1)
            remind = 10
            color = theme.EVENT_COLORS[0]
        self.start_edit.setDateTime(_to_qdt(start))
        self.end_edit.setDateTime(_to_qdt(end))
        self._last_start = _from_qdt(self.start_edit.dateTime())
        idx = next((i for i, (_, v) in enumerate(REMIND_OPTIONS) if v == remind), 0)
        self.remind_combo.setCurrentIndex(idx)
        self.set_color(color)
        self._on_all_day(self.all_day_check.isChecked())

    # ---- 상태 변경
    def set_color(self, color):
        target = next((sw for sw in self.swatches if sw.color.lower() == str(color).lower()), None)
        if target is None:
            # 팔레트에 없는 색이면 견본을 하나 추가
            target = ColorSwatch(color)
            self.color_group.addButton(target)
            self.swatches.append(target)
            self.swatches[0].parentWidget().layout()
        target.setChecked(True)

    def selected_color(self):
        btn = self.color_group.checkedButton()
        return btn.color if btn else theme.EVENT_COLORS[0]

    def _on_all_day(self, checked):
        fmt = 'yyyy. M. d. (ddd)' if checked else 'yyyy. M. d. (ddd)  HH:mm'
        self.start_edit.setDisplayFormat(fmt)
        self.end_edit.setDisplayFormat(fmt)
        self._validate()

    def _on_start_changed(self, qdt):
        new_start = _from_qdt(qdt)
        if self._last_start is not None:
            end = _from_qdt(self.end_edit.dateTime())
            # 기존 간격을 유지하며 종료 시각을 함께 이동
            duration = max(timedelta(0), end - self._last_start)
            self.end_edit.blockSignals(True)
            self.end_edit.setDateTime(_to_qdt(new_start + duration))
            self.end_edit.blockSignals(False)
        self._last_start = new_start
        self._validate()

    def start_end(self):
        """저장될 (start, end) naive datetime"""
        s = _from_qdt(self.start_edit.dateTime())
        e = _from_qdt(self.end_edit.dateTime())
        if self.all_day_check.isChecked():
            s = datetime.combine(s.date(), time(0, 0))
            e = datetime.combine(e.date(), time(23, 59, 59))
        return s, e

    def validation_error(self):
        s, e = self.start_end()
        if e < s:
            return '종료 시간이 시작 시간보다 빠릅니다.'
        if not self.title_edit.text().strip():
            return '일정 제목을 입력해 주세요.'
        return None

    def _show_title_error(self, show):
        self.title_error.setVisible(show)
        self.title_edit.setProperty('invalid', show)
        self.title_edit.style().unpolish(self.title_edit)
        self.title_edit.style().polish(self.title_edit)

    def _validate(self, *args):
        if not hasattr(self, 'save_btn'):
            return
        if self.title_edit.text().strip() and self.title_error.isVisibleTo(self):
            self._show_title_error(False)
        s, e = self.start_end()
        self.save_btn.setEnabled(e >= s)   # 제목이 비어도 누를 수 있다 - 누르면 무엇이 빠졌는지 알려 준다
        msg = '종료 시간이 시작 시간보다 빠릅니다.' if e < s else ''
        self.error_label.setText(msg)
        self.error_label.setVisible(bool(msg))

    def is_valid(self):
        return self.validation_error() is None

    # ---- 데이터
    def selected_attendees(self):
        if self.attendee_list is None:
            return []
        out = []
        for i in range(self.attendee_list.count()):
            item = self.attendee_list.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.data(Qt.UserRole))
        return out

    def build_payload(self):
        """event_create용 필드 (datetime은 state가 ISO로 변환)"""
        s, e = self.start_end()
        payload = {
            'title': self.title_edit.text().strip(),
            'start': s,
            'end': e,
            'all_day': self.all_day_check.isChecked(),
            'description': self.desc_edit.toPlainText().strip(),
            'location': self.location_edit.text().strip(),
            'remind_minutes': self.remind_combo.currentData(),
            'color': self.selected_color(),
        }
        if self.room_id:
            payload['room_id'] = self.room_id
        else:
            payload['attendees'] = self.selected_attendees()
        return payload

    def changed_fields(self):
        """수정 모드에서 원본과 달라진 필드만"""
        if not self.edit_mode:
            return self.build_payload()
        ev = self.event_data
        new = self.build_payload()
        new.pop('room_id', None)
        new.pop('attendees', None)
        changed = {}
        for key in ('title', 'description', 'location', 'remind_minutes', 'color', 'all_day'):
            old = ev.get(key)
            if key in ('description', 'location'):
                old = old or ''
            if key == 'all_day':
                old = bool(old)
            if key == 'color':
                if str(old or '').lower() != str(new[key]).lower():
                    changed[key] = new[key]
                continue
            if old != new[key]:
                changed[key] = new[key]
        old_s, old_e = parse_dt(ev.get('start')), parse_dt(ev.get('end'))
        if new['all_day']:
            # 종일 일정은 날짜만 비교
            if not old_s or old_s.date() != new['start'].date() or not ev.get('all_day') or old_s.time() != time(0):
                changed['start'] = new['start']
            if not old_e or old_e.date() != new['end'].date() or not ev.get('all_day'):
                changed['end'] = new['end']
        else:
            if old_s != new['start']:
                changed['start'] = new['start']
            if old_e != new['end']:
                changed['end'] = new['end']
        return changed

    def accept(self):
        """저장 - 생성 또는 변경 필드만 수정 요청"""
        if not self.title_edit.text().strip():
            self._show_title_error(True)
            self.title_edit.setFocus()
            return False
        if not self.is_valid():
            return False
        if self.edit_mode:
            fields = self.changed_fields()
            if fields:
                self.state.update_event(self.event_data['event_id'], **fields)
        else:
            self.state.create_event(**self.build_payload())
        super().accept()
        return True

    def request_delete(self, confirm=None):
        """삭제 확인 후 state.delete_event. confirm(text)->bool 로 확인창 대체 가능(테스트용)"""
        if not self.edit_mode or self.event_data.get('creator') != self.state.me:
            return False
        text = f"'{self.event_data.get('title', '')}' 일정을 삭제할까요?"
        if confirm is None:
            ok = QMessageBox.question(self, '일정 삭제', text, QMessageBox.Yes | QMessageBox.No,
                                      QMessageBox.No) == QMessageBox.Yes
        else:
            ok = bool(confirm(text))
        if not ok:
            return False
        self.state.delete_event(self.event_data['event_id'])
        self.done(QDialog.Accepted)
        return True


def user_pixmap(state, username, size):
    """프로필 사진 지원 아바타(widgets.user_avatar_pixmap), 없으면 기본 아바타"""
    fn = getattr(widgets, 'user_avatar_pixmap', None)
    if fn is not None:
        try:
            return fn(state, username, size, show_presence=False)
        except Exception:  # noqa: BLE001 - 보조 기능이므로 기본 아바타로 대체
            pass
    return avatar_pixmap(username, state.display_name(username), size)


def make_avatar_icon(state, username, size=24):
    from PyQt5.QtGui import QIcon
    return QIcon(user_pixmap(state, username, size))


def other_company(state, username):
    """내 회사와 다른 경우에만 상대 회사명"""
    company = (state.user(username).get('company') or '').strip()
    mine = (state.user(state.me).get('company') or '').strip()
    return company if company and company != mine else ''


# ---------------------------------------------------------------- 일정 상세
class _ColorDot(QWidget):
    def __init__(self, color, size=12, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(self.rect()))
        p.end()


class _StatusChip(QLabel):
    def __init__(self, status, parent=None):
        super().__init__(RSVP_LABELS.get(status, status), parent)
        bg, fg = rsvp_colors(status)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f'background: {bg.name()}; color: {fg.name()}; border-radius: 9px; '
                           f'padding: 1px 9px; font-size: {theme.fs('caption')}px; font-weight: bold;')


class EventDetailDialog(QDialog):
    """일정 상세 보기 (읽기 전용) - 참석 응답·수정 진입"""

    STATUS_ORDER = {'yes': 0, 'maybe': 1, 'pending': 2, 'no': 3}

    def __init__(self, state, event, parent=None):
        super().__init__(parent)
        self.state = state
        self.event_data = event
        self.event_id = event['event_id']
        self.rsvp_buttons = {}
        self.attendee_rows = {}
        self.setWindowTitle('일정 상세')
        self.setModal(True)
        self.resize(400, 560)
        self.setStyleSheet(_dialog_qss())
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._content = None
        self._render()
        state.events_changed.connect(self._on_events_changed)

    def _on_events_changed(self):
        ev = self.state.events.get(self.event_id)
        if ev is None:
            self.reject()  # 삭제됨
            return
        self.event_data = ev
        self._render()

    def _render(self):
        if self._content is not None:
            self._root.removeWidget(self._content)
            self._content.hide()
            self._content.deleteLater()
        self.rsvp_buttons = {}
        self.attendee_rows = {}
        ev = self.event_data
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._content = content
        self._root.addWidget(content)

        # 상단 색 띠
        band = QFrame()
        band.setFixedHeight(6)
        band.setStyleSheet(f"background: {ev.get('color') or theme.EVENT_COLORS[0]}; border: none;")
        lay.addWidget(band)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 18, 22, 12)
        bl.setSpacing(10)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        # 제목
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        dot = _ColorDot(ev.get('color') or theme.EVENT_COLORS[0], 14)
        title_row.addWidget(dot, 0, Qt.AlignTop)
        self.title_label = widgets.plain(QLabel(ev.get('title', '')))
        self.title_label.setProperty('role', 'heading')
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        bl.addLayout(title_row)
        dot.setContentsMargins(0, 0, 0, 0)

        # 정보 줄
        self.time_label = self._info_row(bl, 'clock', range_text(ev))
        if ev.get('remind_minutes') is not None:
            m = ev['remind_minutes']
            txt = {0: '정시 알림', 60: '1시간 전 알림', 1440: '1일 전 알림'}.get(m, f'{m}분 전 알림')
            self._info_row(bl, 'bell', txt)
        if ev.get('location'):
            self._info_row(bl, 'location', ev['location'])
        if ev.get('room_id'):
            self._info_row(bl, 'chat', self.state.room_title(ev['room_id']) or '채팅방')
        if ev.get('description'):
            self._info_row(bl, 'file', ev['description'], wrap=True)

        # 참석자
        attendees = ev.get('attendees') or {}
        if attendees:
            sep = QFrame()
            sep.setProperty('role', 'sep')
            bl.addWidget(sep)
            counts = {k: sum(1 for v in attendees.values() if v == k) for k in ('yes', 'no', 'maybe')}
            head = QLabel(f"참석자 {len(attendees)}명  ·  참석 {counts['yes']} · 불참 {counts['no']} · 미정 {counts['maybe']}")
            head.setProperty('role', 'field')
            bl.addWidget(head)
            names = sorted(attendees, key=lambda u: (u != ev.get('creator'),
                                                     self.STATUS_ORDER.get(attendees[u], 9),
                                                     self.state.display_name(u)))
            for name in names:
                row = QHBoxLayout()
                row.setSpacing(10)
                av = QLabel()
                av.setFixedSize(32, 32)
                av.setPixmap(user_pixmap(self.state, name, 32))
                row.addWidget(av)
                label = self.state.display_name(name)
                if name == self.state.me:
                    label += ' (나)'
                nl = QLabel(label)
                row.addWidget(nl)
                company = other_company(self.state, name)
                if company:
                    cl = QLabel(company)
                    cl.setProperty('role', 'sub')
                    row.addWidget(cl)
                row.addStretch(1)
                if name == ev.get('creator'):
                    owner = QLabel('주최자')
                    owner.setProperty('role', 'sub')
                    row.addWidget(owner)
                chip = _StatusChip(attendees[name])
                chip.setFixedHeight(20)
                row.addWidget(chip, 0, Qt.AlignVCenter)
                self.attendee_rows[name] = chip.text()
                bl.addLayout(row)
        bl.addStretch(1)

        # 내 응답
        my = self.state.my_rsvp(ev)
        footer = QWidget()
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(22, 8, 22, 16)
        fl.setSpacing(10)
        if my is not None and (len(attendees) > 1 or ev.get('room_id')):
            ask = QLabel('참석하시나요?')
            ask.setProperty('role', 'field')
            fl.addWidget(ask)
            rr = QHBoxLayout()
            rr.setSpacing(8)
            for key in ('yes', 'no', 'maybe'):
                b = QPushButton(RSVP_LABELS[key])
                b.setProperty('rsvp', True)
                b.setCheckable(True)
                b.setChecked(my == key)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=key: self.rsvp(k))
                self.rsvp_buttons[key] = b
                rr.addWidget(b, 1)
            fl.addLayout(rr)
        br = QHBoxLayout()
        br.addStretch(1)
        self.edit_btn = None
        if ev.get('creator') == self.state.me:
            self.edit_btn = QPushButton('수정')
            self.edit_btn.setCursor(Qt.PointingHandCursor)
            self.edit_btn.clicked.connect(self.open_edit)
            br.addWidget(self.edit_btn)
        close = QPushButton('닫기')
        close.setProperty('primary', True)
        close.clicked.connect(self.accept)
        br.addWidget(close)
        fl.addLayout(br)
        lay.addWidget(footer)

    def _info_row(self, layout, icon, text, wrap=False):
        row = QHBoxLayout()
        row.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icon_pixmap(icon, theme.c('text_sub'), 16))
        ic.setFixedSize(16, 20)
        row.addWidget(ic, 0, Qt.AlignTop)
        lbl = widgets.plain(QLabel(text))
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(lbl, 1)
        layout.addLayout(row)
        return lbl

    # ---- 동작
    def rsvp(self, response):
        """내 참석 응답 전송 (서버의 event_updated로 최종 반영)"""
        self.state.rsvp(self.event_id, response)
        for k, b in self.rsvp_buttons.items():
            b.setChecked(k == response)

    def make_edit_dialog(self):
        return EventDialog(self.state, event=self.event_data, parent=self)

    def open_edit(self):
        dlg = self.make_edit_dialog()
        if dlg.exec_() == QDialog.Accepted:
            self.accept()
