# -*- coding: utf-8 -*-
"""
톡캘린더 탭 - 월간 달력 + 선택한 날의 일정 목록 + 할 일

    tab = CalendarTab(state)
    tab.go_to_date(date(2026, 10, 8))
    tab.apply_theme()        # 다크 모드 전환 후 호출

AppState API만 사용하며 소켓은 직접 다루지 않는다.
"""

import calendar
from datetime import date, datetime, timedelta

from PyQt5.QtCore import Qt, QRectF, QPointF, QDate, QSize, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFontMetrics, QPainterPath
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QFrame, QLineEdit, QDateEdit, QMenu,
                             QStackedWidget, QButtonGroup, QSizePolicy, QToolButton, QDialog)

from client.kakao import theme
from client.kakao.widgets import exec_and_release
from client.kakao.state import parse_dt
from client.kakao.widgets import paint_icon, make_icon, IconButton, qss_icon_path

WEEKDAYS_SUN_FIRST = ['일', '월', '화', '수', '목', '금', '토']
WEEKDAYS_MON_FIRST = ['월', '화', '수', '목', '금', '토', '일']

RSVP_LABELS = {'yes': '참석', 'no': '불참', 'maybe': '미정', 'pending': '응답 전'}
RSVP_KEYS = {'yes': 'rsvp_yes', 'no': 'rsvp_no', 'maybe': 'rsvp_maybe', 'pending': 'rsvp_pending'}


# ---------------------------------------------------------------- 공용 유틸
def korean_weekday(d):
    return WEEKDAYS_MON_FIRST[d.weekday()]


def format_day(d, with_year=False):
    """10월 8일 (목)"""
    text = f'{d.month}월 {d.day}일 ({korean_weekday(d)})'
    return f'{d.year}년 {text}' if with_year else text


def event_dates(ev):
    """일정이 걸치는 (시작 날짜, 마지막 날짜). 자정 정각에 끝나면 전날까지로 본다"""
    s = parse_dt(ev.get('start'))
    e = parse_dt(ev.get('end')) or s
    if s is None:
        return None, None
    if e < s:
        e = s
    last = e.date()
    if e > s and e.time() == datetime.min.time() and last > s.date():
        last = last - timedelta(days=1)
    return s.date(), last


def time_text(ev, day=None):
    """목록용 시간 표시 - 종일 / 14:00 - 15:00 / 여러 날 일정은 해당 날짜 기준"""
    if ev.get('all_day'):
        return '종일'
    s = parse_dt(ev.get('start'))
    e = parse_dt(ev.get('end')) or s
    if s is None:
        return ''
    if day is not None and s.date() != e.date():
        if s.date() < day < e.date():
            return '종일'
        if day == s.date():
            return f'{s:%H:%M} -'
        if day == e.date():
            return f'- {e:%H:%M}'
    return f'{s:%H:%M} - {e:%H:%M}'


def range_text(ev):
    """상세 보기용 전체 기간 표시"""
    s = parse_dt(ev.get('start'))
    e = parse_dt(ev.get('end')) or s
    if s is None:
        return ''
    if ev.get('all_day'):
        first, last = event_dates(ev)
        if first == last:
            return f'{format_day(first)} · 종일'
        return f'{format_day(first)} - {format_day(last)} · 종일'
    if s.date() == e.date():
        return f'{format_day(s.date())}  {s:%H:%M} - {e:%H:%M}'
    return f'{format_day(s.date())} {s:%H:%M} - {format_day(e.date())} {e:%H:%M}'


def paint_location(p, rect, color, stroke=1.6):
    """지도 핀(물방울) 모양 장소 아이콘"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(rect.x(), rect.y())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    path = QPainterPath()
    path.moveTo(12, 21.5)
    path.cubicTo(7, 16, 5, 12.5, 5, 9.5)
    path.cubicTo(5, 5.5, 8, 2.5, 12, 2.5)
    path.cubicTo(16, 2.5, 19, 5.5, 19, 9.5)
    path.cubicTo(19, 12.5, 17, 16, 12, 21.5)
    path.closeSubpath()
    path.addEllipse(QPointF(12, 9.5), 2.6, 2.6)
    pen = QPen(QColor(color), stroke * 24.0 / max(1.0, rect.width()))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()


def icon_pixmap(name, color, size=16):
    """아이콘 픽스맵 (name='location'이면 지도 핀)"""
    from PyQt5.QtGui import QPixmap
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    if name == 'location':
        paint_location(p, QRectF(0, 0, size, size), color)
    else:
        paint_icon(p, name, QRectF(0, 0, size, size), color)
    p.end()
    return pm


def arrow_image_path(color):
    return qss_icon_path('back', color, rotate=-90)


def checkbox_qss():
    """체크박스/체크 가능한 목록 표시기 - 라이트·다크 모두 잘 보이도록"""
    check = qss_icon_path('check', theme.c('on_accent'), stroke=3.0)
    c = theme.c
    return f"""
        QCheckBox::indicator, QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px;
            border: 1.5px solid {c('text_hint')}; background: {c('input_bg')}; }}
        QCheckBox::indicator:checked, QListWidget::indicator:checked {{ background: {c('yellow')};
            border-color: {c('yellow')}; image: url("{check}"); }}
        QListWidget::indicator:disabled {{ border-color: {c('border')}; }}
    """


def dropdown_qss():
    """QComboBox/QDateEdit 화살표 영역 스타일"""
    arrow = arrow_image_path(theme.c('text_sub'))
    return f"""
        QComboBox::drop-down, QDateEdit::drop-down, QDateTimeEdit::drop-down {{
            subcontrol-origin: padding; subcontrol-position: center right; width: 22px; border: none; }}
        QComboBox::down-arrow, QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{
            image: url("{arrow}"); width: 12px; height: 12px; }}
    """


def readable_text_on(color):
    """색 배경 위에 올릴 글자색 (밝은 배경은 진한 글자)"""
    c = QColor(color)
    luminance = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor('#191919') if luminance > 150 else QColor('#FFFFFF')


def rsvp_colors(status):
    """RSVP 칩 (배경, 글자) 색"""
    dark = theme.is_dark()
    if status == 'yes':
        return (QColor('#2F4A34') if dark else QColor('#E5F6E8')), QColor('#3FA65A' if not dark else '#7FD493')
    if status == 'no':
        return (QColor('#4A2C2C') if dark else QColor('#FDE8E8')), QColor('#D9453B' if not dark else '#FF8A80')
    if status == 'maybe':
        return (QColor('#4A4226') if dark else QColor('#FFF6D6')), QColor('#B88A00' if not dark else '#F2CF4A')
    return theme.qc('bg_selected'), theme.qc('text_sub')


def draw_chip(p, x, y, text, bg, fg, font, h=18, radius=None):
    """둥근 칩을 그리고 칩 너비를 돌려준다"""
    fm = QFontMetrics(font)
    w = fm.horizontalAdvance(text) + 12
    r = QRectF(x, y, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    rad = h / 2 if radius is None else radius
    p.drawRoundedRect(r, rad, rad)
    p.setPen(fg)
    p.setFont(font)
    p.drawText(r, Qt.AlignCenter, text)
    return w


# ---------------------------------------------------------------- 월간 달력
class MonthGrid(QWidget):
    """직접 그리는 월간 달력. 여러 날 일정은 막대가 칸을 가로질러 이어진다"""

    day_clicked = pyqtSignal(object)          # date
    day_double_clicked = pyqtSignal(object)   # date

    HEADER_H = 26
    MAX_LANES = 2

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        today = date.today()
        self.year, self.month = today.year, today.month
        self.selected = today
        self.setMouseTracking(True)
        self._hover = None
        self.setMinimumHeight(self.HEADER_H + 6 * 50)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    # ---- 계산
    def grid_start(self):
        first = date(self.year, self.month, 1)
        # 일요일 시작: weekday() 월=0 … 일=6
        return first - timedelta(days=(first.weekday() + 1) % 7)

    def week_count(self):
        first = date(self.year, self.month, 1)
        days = calendar.monthrange(self.year, self.month)[1]
        offset = (first.weekday() + 1) % 7
        return (offset + days + 6) // 7

    def days(self):
        start = self.grid_start()
        return [start + timedelta(days=i) for i in range(self.week_count() * 7)]

    def row_height(self):
        return (self.height() - self.HEADER_H) / self.week_count()

    def cell_rect(self, index):
        cw = self.width() / 7.0
        rh = self.row_height()
        row, col = divmod(index, 7)
        return QRectF(col * cw, self.HEADER_H + row * rh, cw, rh)

    def date_at(self, pos):
        if pos.y() < self.HEADER_H:
            return None
        cw = self.width() / 7.0
        col = int(pos.x() // cw)
        row = int((pos.y() - self.HEADER_H) // self.row_height())
        if not (0 <= col < 7 and 0 <= row < self.week_count()):
            return None
        return self.grid_start() + timedelta(days=row * 7 + col)

    def week_layout(self, week_start):
        """한 주에 걸친 일정을 레인에 배치 → ([(event, c0, c1, lane)], {date: 숨겨진 개수})"""
        week_end = week_start + timedelta(days=6)
        items = []
        for ev in self.state.events.values():
            first, last = event_dates(ev)
            if first is None or last < week_start or first > week_end:
                continue
            items.append((ev, first, last))
        # 긴 일정·종일 일정을 먼저, 그다음 시작 시각 순
        items.sort(key=lambda t: (t[1], -(t[2] - t[1]).days, not t[0].get('all_day'), t[0].get('start', '')))
        lanes = []           # lane -> set(col)
        placed, hidden = [], {}
        for ev, first, last in items:
            c0 = max(0, (first - week_start).days)
            c1 = min(6, (last - week_start).days)
            cols = set(range(c0, c1 + 1))
            lane = 0
            while lane < len(lanes) and lanes[lane] & cols:
                lane += 1
            if lane == len(lanes):
                lanes.append(set())
            lanes[lane] |= cols
            if lane < self.MAX_LANES:
                placed.append((ev, c0, c1, lane))
            else:
                for c in cols:
                    d = week_start + timedelta(days=c)
                    hidden[d] = hidden.get(d, 0) + 1
        return placed, hidden

    def _birthday_keys(self):
        from client.kakao.state import birthday_md
        return {birthday_md(u.get('birthday')) for u in self.state.friends() if birthday_md(u.get('birthday'))}

    # ---- 그리기
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w = self.width()
        cw = w / 7.0
        today = date.today()

        # 요일 머리글
        p.setFont(theme.font('caption'))
        for i, name in enumerate(WEEKDAYS_SUN_FIRST):
            color = theme.qc('calendar_sun') if i == 0 else theme.qc('calendar_sat') if i == 6 else theme.qc('text_hint')
            p.setPen(color)
            p.drawText(QRectF(i * cw, 0, cw, self.HEADER_H), Qt.AlignCenter, name)

        rh = self.row_height()
        birthdays = self._birthday_keys()
        days = self.days()
        num_font = theme.font('meta')
        num_font_bold = theme.font('meta', bold=True)
        small = theme.font('micro')
        bar_font = theme.font('micro')
        bar_h = max(13, theme.px(13))
        bar_top_offset = 31

        # 구분선
        p.setPen(QPen(theme.qc('border'), 1))
        for r in range(self.week_count()):
            y = self.HEADER_H + r * rh
            p.drawLine(QPointF(0, y + 0.5), QPointF(w, y + 0.5))

        for i, d in enumerate(days):
            rect = self.cell_rect(i)
            in_month = d.month == self.month
            col = i % 7
            if self._hover == d and d != self.selected:
                p.setPen(Qt.NoPen)
                p.setBrush(theme.qc('bg_hover'))
                p.drawRoundedRect(rect.adjusted(2, 3, -2, -2), 6, 6)
            if d == self.selected:
                p.setBrush(Qt.NoBrush)
                pen = QPen(theme.qc('text_sub') if not theme.is_dark() else theme.qc('text_hint'), 1.2)
                p.setPen(pen)
                p.drawRoundedRect(rect.adjusted(2, 3, -2, -2), 6, 6)

            # 날짜 숫자
            circle = QRectF(rect.center().x() - 11, rect.top() + 6, 22, 22)
            if d == today:
                p.setPen(Qt.NoPen)
                p.setBrush(theme.qc('calendar_today'))
                p.drawEllipse(circle)
                color = QColor(theme.c('on_accent'))
            elif col == 0:
                color = theme.qc('calendar_sun')
            elif col == 6:
                color = theme.qc('calendar_sat')
            else:
                color = theme.qc('text')
            if not in_month:
                color = QColor(color)
                color.setAlphaF(0.35)
            p.setPen(color)
            p.setFont(num_font_bold if d == today or d == self.selected else num_font)
            p.drawText(circle, Qt.AlignCenter, str(d.day))

            # 생일 표시
            if d.strftime('%m-%d') in birthdays:
                paint_icon(p, 'cake', QRectF(rect.left() + 4, rect.top() + 6, 12, 12), theme.qc('text_sub'), stroke=2.0)

        # 일정 막대
        for week in range(self.week_count()):
            week_start = days[week * 7]
            placed, hidden = self.week_layout(week_start)
            y0 = self.HEADER_H + week * rh + bar_top_offset
            for ev, c0, c1, lane in placed:
                x = c0 * cw + 2
                width = (c1 - c0 + 1) * cw - 4
                r = QRectF(x, y0 + lane * (bar_h + 2), width, bar_h)
                color = QColor(ev.get('color') or theme.EVENT_COLORS[0])
                faded = not any((week_start + timedelta(days=c)).month == self.month for c in range(c0, c1 + 1))
                if faded:
                    color.setAlphaF(0.45)
                path = QPainterPath()
                path.addRoundedRect(r, 3, 3)
                p.fillPath(path, color)
                p.save()
                p.setClipRect(r.adjusted(3, 0, -2, 0))
                p.setPen(readable_text_on(ev.get('color') or theme.EVENT_COLORS[0]))
                p.setFont(bar_font)
                title = ev.get('title', '')
                p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, title)
                p.restore()
            for d, count in hidden.items():
                if d not in days:
                    continue
                rect = self.cell_rect(days.index(d))
                p.setFont(small)
                p.setPen(theme.qc('text_sub'))
                p.drawText(QRectF(rect.left(), rect.top() + 6, rect.width() - 5, 14),
                           Qt.AlignRight | Qt.AlignVCenter, f'+{count}')
        p.end()

    # ---- 마우스
    def mouseMoveEvent(self, event):
        d = self.date_at(event.pos())
        if d != self._hover:
            self._hover = d
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            d = self.date_at(event.pos())
            if d is not None:
                self.day_clicked.emit(d)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            d = self.date_at(event.pos())
            if d is not None:
                self.day_double_clicked.emit(d)


# ---------------------------------------------------------------- 일정 목록 항목
class AgendaItem(QWidget):
    """선택한 날의 일정 한 줄(색 막대·시간·제목·장소·채팅방·내 응답) 또는 생일 줄"""

    clicked = pyqtSignal(object)

    def __init__(self, state, day, event=None, birthday_user=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.day = day
        self.event_data = event
        self.birthday_user = birthday_user
        self._hover = False
        self.setCursor(Qt.PointingHandCursor if event else Qt.ArrowCursor)
        self.setFixedHeight(theme.px(54) if event and self._has_sub() else theme.px(40))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _has_sub(self):
        ev = self.event_data
        return bool(ev.get('location') or ev.get('room_id'))

    def title_text(self):
        if self.birthday_user:
            return f"{self.state.display_name(self.birthday_user['username'])}님 생일"
        return self.event_data.get('title', '')

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.event_data is not None and self.rect().contains(e.pos()):
            self.clicked.emit(self.event_data)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        if self._hover and self.event_data is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc('bg_hover'))
            p.drawRoundedRect(r.adjusted(6, 1, -6, -1), 8, 8)
        left = 16
        if self.birthday_user:
            paint_icon(p, 'cake', QRectF(left + 2, (r.height() - 18) / 2, 18, 18), theme.qc('text_sub'), stroke=1.6)
            p.setPen(theme.qc('text'))
            p.setFont(theme.font('body'))
            p.drawText(QRectF(left + 30, 0, r.width() - left - 40, r.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, self.title_text())
            p.end()
            return

        ev = self.event_data
        color = QColor(ev.get('color') or theme.EVENT_COLORS[0])
        has_sub = self._has_sub()
        # 일정 색: 왼쪽 세로 줄무늬 대신 첫 줄 높이에 맞춘 8px 점 (KCS Ink - 카드에 색 옆줄을 두지 않는다)
        dot_cy = (8 + 10) if has_sub else r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(left, dot_cy - 4, 8, 8))

        # 시간 열
        time_w = theme.px(84)
        p.setFont(theme.font('meta'))
        p.setPen(theme.qc('text_sub'))
        top_line = QRectF(left + 14, 0, time_w, r.height() if not has_sub else r.height() / 2 + 4)
        txt = time_text(ev, self.day)
        p.drawText(top_line if not has_sub else QRectF(left + 14, 8, time_w, 20),
                   Qt.AlignVCenter | Qt.AlignLeft, txt)

        # 오른쪽: 내 응답 칩
        x_right = r.width() - 16
        status = self.state.my_rsvp(ev)
        chip_font = theme.font('micro', bold=True)
        if status and ev.get('room_id'):
            label = RSVP_LABELS.get(status, status)
            fm = QFontMetrics(chip_font)
            cw = fm.horizontalAdvance(label) + 12
            bg, fg = rsvp_colors(status)
            draw_chip(p, x_right - cw, (8 if has_sub else (r.height() - 18) / 2) + 1, label, bg, fg, chip_font)
            x_right -= cw + 8

        # 제목
        title_x = left + 14 + time_w
        title_font = theme.font('body', bold=True)
        p.setFont(title_font)
        p.setPen(theme.qc('text'))
        fm = QFontMetrics(title_font)
        title = fm.elidedText(ev.get('title', ''), Qt.ElideRight, int(x_right - title_x))
        if has_sub:
            p.drawText(QRectF(title_x, 8, x_right - title_x, 20), Qt.AlignVCenter | Qt.AlignLeft, title)
        else:
            p.drawText(QRectF(title_x, 0, x_right - title_x, r.height()), Qt.AlignVCenter | Qt.AlignLeft, title)

        # 둘째 줄: 장소 · 채팅방 칩
        if has_sub:
            y2 = 30
            x = title_x
            sub_font = theme.font('caption')
            if ev.get('room_id'):
                room = self.state.room_title(ev['room_id']) or '채팅방'
                fm2 = QFontMetrics(sub_font)
                room = fm2.elidedText(room, Qt.ElideRight, 110)
                chip_bg = theme.qc('bg_selected')
                w = draw_chip(p, x, y2, room, chip_bg, theme.qc('text_sub'), sub_font, h=17, radius=4)
                x += w + 6
            if ev.get('location'):
                paint_location(p, QRectF(x, y2 + 2, 13, 13), theme.c('text_hint'))
                p.setFont(sub_font)
                p.setPen(theme.qc('text_sub'))
                fm2 = QFontMetrics(sub_font)
                loc = fm2.elidedText(ev['location'], Qt.ElideRight, int(r.width() - x - 34))
                p.drawText(QRectF(x + 15, y2, r.width() - x - 30, 17), Qt.AlignVCenter | Qt.AlignLeft, loc)
        p.end()


# ---------------------------------------------------------------- 할 일 항목
class TodoItem(QWidget):
    """동그란 체크박스 · 제목(완료 시 취소선) · 기한 칩 · 마우스를 올리면 삭제 버튼"""

    toggled = pyqtSignal(dict)
    delete_requested = pyqtSignal(dict)

    CHECK = QRectF(16, 0, 20, 20)

    def __init__(self, todo, parent=None):
        super().__init__(parent)
        self.todo = todo
        self._hover = False
        self.setFixedHeight(theme.px(42))
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.delete_btn = IconButton('trash', '삭제', 16, self)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.todo))
        self.delete_btn.hide()

    def resizeEvent(self, e):
        b = self.delete_btn
        b.move(self.width() - b.width() - 10, (self.height() - b.height()) // 2)

    def enterEvent(self, e):
        self._hover = True
        self.delete_btn.refresh_icon()
        self.delete_btn.show()
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.delete_btn.hide()
        self.update()

    def _menu(self, pos):
        menu = QMenu(self)
        done = self.todo.get('done')
        menu.addAction('완료 취소' if done else '완료', lambda: self.toggled.emit(self.todo))
        menu.addAction('삭제', lambda: self.delete_requested.emit(self.todo))
        menu.exec_(self.mapToGlobal(pos))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and e.pos().x() < 60:
            self.toggled.emit(self.todo)

    def due_info(self):
        """(칩 문구, 지남 여부) 또는 None"""
        due = self.todo.get('due')
        if not due:
            return None
        try:
            d = date.fromisoformat(due)
        except ValueError:
            return None
        today = date.today()
        overdue = d < today and not self.todo.get('done')
        if d == today:
            label = '오늘'
        elif d == today + timedelta(days=1):
            label = '내일'
        elif d.year == today.year:
            label = f'{d.month}/{d.day} ({korean_weekday(d)})'
        else:
            label = f'{d.year}.{d.month}.{d.day}'
        return label, overdue

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc('bg_hover'))
            p.drawRoundedRect(r.adjusted(6, 1, -6, -1), 8, 8)
        done = bool(self.todo.get('done'))
        box = QRectF(18, (r.height() - 20) / 2, 20, 20)
        if done:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc('yellow'))
            p.drawEllipse(box)
            paint_icon(p, 'check', box.adjusted(3, 3, -3, -3), theme.c('on_accent'), stroke=2.6)
        else:
            p.setPen(QPen(theme.qc('text_hint'), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(box.adjusted(0.75, 0.75, -0.75, -0.75))

        right = r.width() - (52 if self._hover else 16)
        info = self.due_info()
        chip_font = theme.font('micro', bold=True)
        if info:
            label, overdue = info
            fm = QFontMetrics(chip_font)
            cw = fm.horizontalAdvance(label) + 12
            if overdue:
                bg, fg = rsvp_colors('no')
            else:
                bg, fg = theme.qc('bg_selected'), theme.qc('text_sub')
            draw_chip(p, right - cw, (r.height() - 18) / 2, label, bg, fg, chip_font)
            right -= cw + 8

        f = theme.font('body')
        f.setStrikeOut(done)
        p.setFont(f)
        p.setPen(theme.qc('text_hint') if done else theme.qc('text'))
        fm = QFontMetrics(f)
        x = 50
        title = fm.elidedText(self.todo.get('title', ''), Qt.ElideRight, int(right - x))
        p.drawText(QRectF(x, 0, right - x, r.height()), Qt.AlignVCenter | Qt.AlignLeft, title)
        p.end()


# ---------------------------------------------------------------- 캘린더 탭
class CalendarTab(QWidget):
    """메인 창의 톡캘린더 탭"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        today = date.today()
        self.year, self.month = today.year, today.month
        self.selected = today
        self.agenda_entries = []   # [('event', ev) | ('birthday', user)] - 현재 목록 내용
        self.todo_rows = {}        # todo_id -> TodoItem
        self._open_dialogs = []
        self._build()
        self.apply_theme()

        state.events_changed.connect(self.refresh)
        state.users_changed.connect(self.refresh)
        state.todos_changed.connect(self.refresh_todos)
        self.go_to_date(today)
        self.refresh_todos()

    # ---- UI 구성
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 상단 제목줄
        top = QWidget()
        top.setObjectName('calTop')
        tl = QHBoxLayout(top)
        # 다른 탭 머리글(TabHeader)과 같은 여백 · 같은 제목 크기. 제목 앞 강조 막대는 두지 않는다 (KCS Ink)
        tl.setContentsMargins(theme.sp('l') + 4, theme.sp('l'), theme.sp('m'), theme.sp('xs'))
        tl.setSpacing(theme.sp('xs'))
        self.title_label = QLabel('캘린더')
        self.title_label.setObjectName('calTitle')
        tl.addWidget(self.title_label)
        tl.addStretch(1)
        self.add_btn = QPushButton(' 일정 추가')
        self.add_btn.setObjectName('calAdd')
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(lambda: self.open_new_event(self.selected))
        tl.addWidget(self.add_btn)
        root.addWidget(top)

        # 월 이동 줄
        nav = QWidget()
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(14, 2, 14, 2)
        nl.setSpacing(2)
        self.prev_btn = IconButton('back', '이전 달', 16)
        self.prev_btn.clicked.connect(self.prev_month)
        self.month_label = QLabel()
        self.month_label.setObjectName('calMonth')
        self.next_btn = IconButton('back', '다음 달', 16)
        self.next_btn.clicked.connect(self.next_month)
        self.today_btn = QPushButton('오늘')
        self.today_btn.setObjectName('calToday')
        self.today_btn.setCursor(Qt.PointingHandCursor)
        self.today_btn.clicked.connect(self.go_today)
        nl.addWidget(self.prev_btn)
        nl.addWidget(self.month_label)
        nl.addWidget(self.next_btn)
        nl.addStretch(1)
        nl.addWidget(self.today_btn)
        root.addWidget(nav)

        # 달력
        self.grid = MonthGrid(self.state)
        self.grid.day_clicked.connect(self.select_date)
        self.grid.day_double_clicked.connect(self._on_double_click)
        gwrap = QWidget()
        gl = QVBoxLayout(gwrap)
        gl.setContentsMargins(8, 2, 8, 6)
        gl.addWidget(self.grid)
        root.addWidget(gwrap, 5)

        # 일정 | 할 일 세그먼트
        seg_wrap = QWidget()
        seg_wrap.setObjectName('calSegWrap')
        sl = QHBoxLayout(seg_wrap)
        sl.setContentsMargins(16, 8, 16, 4)
        sl.setSpacing(0)
        self.seg_group = QButtonGroup(self)
        self.seg_events = QPushButton('일정')
        self.seg_todos = QPushButton('할 일')
        for i, b in enumerate((self.seg_events, self.seg_todos)):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName('segLeft' if i == 0 else 'segRight')
            self.seg_group.addButton(b, i)
            sl.addWidget(b)
        self.seg_events.setChecked(True)
        self.seg_group.buttonClicked[int].connect(self.set_section)
        sl.addStretch(1)
        self.day_label = QLabel()
        self.day_label.setObjectName('calDay')
        sl.addWidget(self.day_label)
        root.addWidget(seg_wrap)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 4)

        # 일정 목록
        self.agenda_scroll = QScrollArea()
        self.agenda_scroll.setWidgetResizable(True)
        self.agenda_scroll.setFrameShape(QFrame.NoFrame)
        self.agenda_box = QWidget()
        self.agenda_box.setObjectName('calList')
        self.agenda_layout = QVBoxLayout(self.agenda_box)
        self.agenda_layout.setContentsMargins(0, 4, 0, 8)
        self.agenda_layout.setSpacing(0)
        self.agenda_scroll.setWidget(self.agenda_box)
        self.stack.addWidget(self.agenda_scroll)

        # 할 일
        todo_page = QWidget()
        tp = QVBoxLayout(todo_page)
        tp.setContentsMargins(0, 0, 0, 0)
        tp.setSpacing(0)
        self.todo_scroll = QScrollArea()
        self.todo_scroll.setWidgetResizable(True)
        self.todo_scroll.setFrameShape(QFrame.NoFrame)
        self.todo_box = QWidget()
        self.todo_box.setObjectName('calList')
        self.todo_layout = QVBoxLayout(self.todo_box)
        self.todo_layout.setContentsMargins(0, 4, 0, 4)
        self.todo_layout.setSpacing(0)
        self.todo_scroll.setWidget(self.todo_box)
        tp.addWidget(self.todo_scroll, 1)

        add_row = QWidget()
        add_row.setObjectName('todoAddRow')
        al = QHBoxLayout(add_row)
        al.setContentsMargins(14, 8, 14, 10)
        al.setSpacing(6)
        self.todo_input = QLineEdit()
        self.todo_input.setPlaceholderText('할 일 추가')
        self.todo_input.returnPressed.connect(self.add_todo)
        self.todo_due_btn = QToolButton()
        self.todo_due_btn.setCheckable(True)
        self.todo_due_btn.setToolTip('기한 설정')
        self.todo_due_btn.setCursor(Qt.PointingHandCursor)
        self.todo_due_btn.setObjectName('dueToggle')
        self.todo_due_btn.toggled.connect(self._on_due_toggled)
        self.todo_due = QDateEdit(QDate.currentDate())
        self.todo_due.setCalendarPopup(True)
        self.todo_due.setDisplayFormat('M/d')
        self.todo_due.setFixedWidth(theme.px(92))
        self.todo_due.hide()
        self.todo_add_btn = QPushButton('추가')
        self.todo_add_btn.setProperty('primary', True)
        self.todo_add_btn.setCursor(Qt.PointingHandCursor)
        self.todo_add_btn.clicked.connect(self.add_todo)
        al.addWidget(self.todo_input, 1)
        al.addWidget(self.todo_due_btn)
        al.addWidget(self.todo_due)
        al.addWidget(self.todo_add_btn)
        tp.addWidget(add_row)
        self.stack.addWidget(todo_page)

    # ---- 테마
    def apply_theme(self):
        c = theme.c
        self.setStyleSheet(f"""
            CalendarTab {{ background: {c('bg')}; }}
            QWidget#calList, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c('bg')}; }}
            QLabel {{ color: {c('text')}; background: transparent; }}
            QLabel#calTitle {{ font-size: {theme.fs('display')}px; font-weight: bold; letter-spacing: -0.4px; }}
            QLabel#calMonth {{ font-size: {theme.fs('lead')}px; font-weight: bold; padding: 0 2px; }}
            QLabel#calDay {{ color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#calEmpty {{ color: {c('text_hint')}; font-size: {theme.fs('meta')}px; padding: 18px; }}
            QPushButton#calAdd {{ background: {c('yellow')}; color: {theme.c('on_accent')}; border: none; border-radius: 15px;
                                  padding: 0 14px 0 10px; min-height: 30px; font-weight: bold; font-size: {theme.fs('meta')}px; }}
            QPushButton#calAdd:hover {{ background: {c('yellow_dark')}; }}
            QPushButton#calToday {{ background: transparent; color: {c('text')}; border: 1px solid {c('border')};
                                    border-radius: 13px; padding: 0 12px; min-height: 26px; font-size: {theme.fs('meta')}px; }}
            QPushButton#calToday:hover {{ background: {c('bg_hover')}; }}
            QPushButton#segLeft, QPushButton#segRight {{ background: {c('bg_alt')}; color: {c('text_sub')};
                border: 1px solid {c('border')}; padding: 0 16px; min-height: 28px; font-size: {theme.fs('meta')}px; }}
            QPushButton#segLeft {{ border-top-left-radius: 14px; border-bottom-left-radius: 14px;
                                   border-top-right-radius: 0; border-bottom-right-radius: 0; border-right: none; }}
            QPushButton#segRight {{ border-top-right-radius: 14px; border-bottom-right-radius: 14px;
                                    border-top-left-radius: 0; border-bottom-left-radius: 0; }}
            QPushButton#segLeft:checked, QPushButton#segRight:checked {{ background: {c('yellow')}; color: {c('on_accent')};
                border-color: {c('yellow')}; font-weight: bold; }}
            QWidget#calSegWrap {{ border-top: 1px solid {c('border')}; }}
            QWidget#todoAddRow {{ border-top: 1px solid {c('border')}; background: {c('bg')}; }}
            QLineEdit, QDateEdit {{ background: {c('input_bg')}; color: {c('text')}; border: 1px solid {c('border')};
                border-radius: 6px; padding: 5px 8px; }}
            QLineEdit:focus {{ border: 1px solid {c('yellow_dark')}; }}
            QToolButton#dueToggle {{ background: transparent; border: 1px solid {c('border')}; border-radius: 6px;
                                     padding: 4px; }}
            QToolButton#dueToggle:checked {{ background: {c('yellow')}; border-color: {c('yellow')}; }}
            QPushButton[primary="true"] {{ background: {c('yellow')}; color: {theme.c('on_accent')}; border: none; border-radius: 6px;
                                           padding: 6px 12px; font-weight: bold; }}
            QPushButton[primary="true"]:hover {{ background: {c('yellow_dark')}; }}
            {dropdown_qss()}
            {theme.scrollbar_qss()}
        """)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.add_btn.setIcon(make_icon('plus', theme.c('on_accent'), 14))
        self.add_btn.setIconSize(QSize(14, 14))
        self.prev_btn.refresh_icon(theme.c('text'))
        self._mirror_next_icon()
        self.today_btn.setFixedHeight(28)
        self._on_due_toggled(self.todo_due_btn.isChecked())
        self.grid.update()
        if hasattr(self, 'agenda_layout') and self.agenda_entries is not None:
            self.refresh()
        self.refresh_todos()

    def _mirror_next_icon(self):
        """'back' 아이콘을 좌우로 뒤집어 다음 달 버튼에 사용"""
        from PyQt5.QtGui import QIcon, QTransform
        pm = make_icon('back', theme.c('text'), 16).pixmap(QSize(16, 16))
        self.next_btn.setIcon(QIcon(pm.transformed(QTransform().scale(-1, 1))))

    def _on_due_toggled(self, checked):
        self.todo_due.setVisible(checked)
        color = theme.c('on_accent') if checked else theme.c('text_sub')
        self.todo_due_btn.setIcon(make_icon('calendar', color, 16))
        self.todo_due_btn.setIconSize(QSize(16, 16))

    # ---- 탐색
    def go_to_date(self, d):
        """d가 속한 달로 이동하고 d를 선택"""
        changed = (d.year, d.month) != (self.year, self.month) or not getattr(self, '_requested_once', False)
        self.year, self.month = d.year, d.month
        self.selected = d
        self.grid.year, self.grid.month = d.year, d.month
        self.grid.selected = d
        self.month_label.setText(f'{d.year}년 {d.month}월')
        if changed:
            self._requested_once = True
            self.request_month()
        self.refresh()

    def month_range(self):
        """(달 시작 - 7일, 달 끝 + 7일) - 달 끝은 다음 달 1일 0시(배타)"""
        start = datetime(self.year, self.month, 1)
        days = calendar.monthrange(self.year, self.month)[1]
        end = start + timedelta(days=days)
        return start - timedelta(days=7), end + timedelta(days=7)

    def request_month(self):
        start, end = self.month_range()
        self.state.request_events(start, end)

    def shift_month(self, delta):
        y, m = self.year, self.month + delta
        while m < 1:
            y, m = y - 1, m + 12
        while m > 12:
            y, m = y + 1, m - 12
        today = date.today()
        target = today if (y, m) == (today.year, today.month) else date(y, m, 1)
        self.go_to_date(target)

    def prev_month(self):
        self.shift_month(-1)

    def next_month(self):
        self.shift_month(1)

    def go_today(self):
        self.go_to_date(date.today())

    def select_date(self, d):
        if (d.year, d.month) != (self.year, self.month):
            self.go_to_date(d)
            return
        self.selected = d
        self.grid.selected = d
        self.refresh()

    def set_section(self, index):
        self.stack.setCurrentIndex(index)
        (self.seg_events if index == 0 else self.seg_todos).setChecked(True)
        self._update_day_label()

    def _update_day_label(self):
        if self.stack.currentIndex() == 0:
            self.day_label.setText(format_day(self.selected))
        else:
            remaining = sum(1 for t in self.state.todos.values() if not t.get('done'))
            self.day_label.setText(f'남은 할 일 {remaining}개')

    # ---- 일정 목록
    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def refresh(self):
        """달력·선택한 날 일정 목록 다시 그리기"""
        self.grid.update()
        self._update_day_label()
        self._clear(self.agenda_layout)
        day = self.selected
        entries = []
        for user in self.state.birthday_friends(day):
            entries.append(('birthday', user))
        events = self.state.events_on(day)
        # 종일 일정 먼저, 그다음 시작 시각 순
        events.sort(key=lambda ev: (not ev.get('all_day'), ev.get('start', '')))
        for ev in events:
            entries.append(('event', ev))
        self.agenda_entries = entries
        if not entries:
            empty = QLabel('일정이 없습니다')
            empty.setObjectName('calEmpty')
            empty.setAlignment(Qt.AlignCenter)
            self.agenda_layout.addWidget(empty)
        for kind, data in entries:
            if kind == 'birthday':
                item = AgendaItem(self.state, day, birthday_user=data)
            else:
                item = AgendaItem(self.state, day, event=data)
                item.clicked.connect(self.open_event_detail)
            self.agenda_layout.addWidget(item)
        self.agenda_layout.addStretch(1)

    def agenda_titles(self):
        """테스트·접근성용 - 현재 목록에 보이는 문구"""
        out = []
        for kind, data in self.agenda_entries:
            if kind == 'birthday':
                out.append(f"{self.state.display_name(data['username'])}님 생일")
            else:
                out.append(data.get('title', ''))
        return out

    # ---- 할 일
    def refresh_todos(self):
        self._clear(self.todo_layout)
        self.todo_rows = {}
        todos = self.state.sorted_todos()
        if not todos:
            empty = QLabel('할 일이 없습니다')
            empty.setObjectName('calEmpty')
            empty.setAlignment(Qt.AlignCenter)
            self.todo_layout.addWidget(empty)
        for todo in todos:
            row = TodoItem(todo)
            row.toggled.connect(self.toggle_todo)
            row.delete_requested.connect(self.delete_todo)
            self.todo_rows[todo['todo_id']] = row
            self.todo_layout.addWidget(row)
        self.todo_layout.addStretch(1)
        self._update_day_label()

    def add_todo(self):
        title = self.todo_input.text().strip()
        if not title:
            return
        due = self.todo_due.date().toPyDate() if self.todo_due_btn.isChecked() else None
        self.state.create_todo(title, due)
        self.todo_input.clear()
        self.todo_due_btn.setChecked(False)

    def toggle_todo(self, todo):
        self.state.update_todo(todo['todo_id'], done=not todo.get('done'))

    def delete_todo(self, todo):
        self.state.delete_todo(todo['todo_id'])

    # ---- 대화상자
    def _on_double_click(self, d):
        self.select_date(d)
        self.open_new_event(d)

    def open_new_event(self, d=None):
        from client.kakao.event_dialog import EventDialog
        dlg = EventDialog(self.state, default_date=d or self.selected, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self.show_created(dlg.start_end()[0])
        return dlg

    def show_created(self, start):
        """방금 등록한 일정의 날짜로 달력을 옮기고 알린다 - 다른 달 · 다른 해에 넣어도 바로 보이게"""
        self.go_to_date(start.date())
        win = self.window()
        if hasattr(win, 'show_toast'):
            win.show_toast(f'일정을 등록했습니다 · {start:%Y. %m. %d.} ({WEEKDAYS_MON_FIRST[start.weekday()]})')

    def open_event_detail(self, ev):
        from client.kakao.event_dialog import EventDetailDialog
        dlg = EventDetailDialog(self.state, ev, parent=self)
        exec_and_release(dlg)