# -*- coding: utf-8 -*-
"""
공용 그래픽 위젯 - 외부 이미지 없이 QPainter로 그리는 아이콘/아바타/배지
아이콘은 24x24 좌표계의 선(stroke) 아이콘이며 어떤 크기·색상으로도 렌더링된다.
"""

import hashlib
import math

from PyQt5.QtCore import Qt, QRectF, QPoint, QPointF, QSize, QEvent, QObject, QTimer, pyqtSignal
from PyQt5.QtGui import (QPainter, QPainterPath, QPen, QColor, QIcon, QPixmap, QBrush, QLinearGradient)
from PyQt5.QtWidgets import (QWidget, QPushButton, QLabel, QSizePolicy, QAbstractButton, QComboBox,
                             QStyledItemDelegate, QDialog, QApplication)

from client.kakao import theme

# KCS Ink 아바타: 채도를 눌러 네이비 CI 와 어울리는 잉크 톤 (흰 굵은 이니셜 4.5:1 이상)
AVATAR_COLORS = ['#3E5BA9', '#2F767D', '#5B4FA0', '#A2533C', '#3B7353',
                 '#8A4A78', '#5F6680', '#8C6420', '#2D6A99']


# ---------------------------------------------------------------- 아이콘
def _icon_path(name):
    """name → (QPainterPath, filled:bool). 24x24 기준 좌표"""
    p = QPainterPath()
    filled = False
    if name == 'friends':
        p.addEllipse(QRectF(8, 3.5, 8, 8))
        p.moveTo(4, 20.5)
        p.cubicTo(4, 15.5, 8, 13.5, 12, 13.5)
        p.cubicTo(16, 13.5, 20, 15.5, 20, 20.5)
        p.closeSubpath()
    elif name == 'chat':
        p.addEllipse(QRectF(3, 4, 18, 14))
        p.moveTo(7, 16.5)
        p.lineTo(5.5, 20.5)
        p.lineTo(10.5, 17.6)
    elif name == 'calendar':
        p.addRoundedRect(QRectF(3.5, 5, 17, 15.5), 2.5, 2.5)
        p.moveTo(3.5, 10)
        p.lineTo(20.5, 10)
        p.moveTo(8, 3)
        p.lineTo(8, 7)
        p.moveTo(16, 3)
        p.lineTo(16, 7)
    elif name == 'more':
        for x in (5, 12, 19):
            p.addEllipse(QPointF(x, 12), 1.6, 1.6)
        filled = True
    elif name == 'search':
        p.addEllipse(QRectF(4, 4, 12, 12))
        p.moveTo(14.5, 14.5)
        p.lineTo(20, 20)
    elif name == 'plus':
        p.moveTo(12, 5)
        p.lineTo(12, 19)
        p.moveTo(5, 12)
        p.lineTo(19, 12)
    elif name == 'add_chat':
        p.addEllipse(QRectF(3, 4, 16, 13))
        p.moveTo(6.5, 15.5)
        p.lineTo(5, 19.5)
        p.lineTo(9.5, 16.6)
        p.moveTo(18.5, 2.5)
        p.lineTo(18.5, 8.5)
        p.moveTo(15.5, 5.5)
        p.lineTo(21.5, 5.5)
    elif name == 'bell':
        p.moveTo(6, 17)
        p.lineTo(6, 11)
        p.cubicTo(6, 7, 8.5, 4.5, 12, 4.5)
        p.cubicTo(15.5, 4.5, 18, 7, 18, 11)
        p.lineTo(18, 17)
        p.lineTo(20, 18.5)
        p.lineTo(4, 18.5)
        p.closeSubpath()
        p.moveTo(10, 21)
        p.lineTo(14, 21)
    elif name == 'bell_off':
        p.addPath(_icon_path('bell')[0])
        p.moveTo(4, 4)
        p.lineTo(20, 20)
    elif name == 'pin':
        p.moveTo(9, 3.5)
        p.lineTo(15, 3.5)
        p.lineTo(14, 9.5)
        p.lineTo(17.5, 13.5)
        p.lineTo(6.5, 13.5)
        p.lineTo(10, 9.5)
        p.closeSubpath()
        p.moveTo(12, 13.5)
        p.lineTo(12, 20.5)
    elif name == 'clip':
        p.moveTo(16.5, 7)
        p.lineTo(8.5, 15)
        p.cubicTo(7.5, 16, 7.5, 17.5, 8.5, 18.3)
        p.cubicTo(9.5, 19.2, 11, 19.2, 12, 18.2)
        p.lineTo(19, 11)
        p.cubicTo(21, 9, 21, 6, 19, 4.3)
        p.cubicTo(17, 2.5, 14, 2.8, 12.3, 4.7)
        p.lineTo(5, 12)
        p.cubicTo(2.5, 14.5, 2.7, 18.5, 5, 20.5)
    elif name == 'smile':
        p.addEllipse(QRectF(3.5, 3.5, 17, 17))
        p.moveTo(8, 14)
        p.cubicTo(9.5, 16.5, 14.5, 16.5, 16, 14)
        p.addEllipse(QPointF(9, 9.8), 0.6, 0.6)
        p.addEllipse(QPointF(15, 9.8), 0.6, 0.6)
    elif name == 'image':
        p.addRoundedRect(QRectF(3.5, 4.5, 17, 15), 2, 2)
        p.addEllipse(QPointF(9, 9.5), 1.6, 1.6)
        p.moveTo(3.5, 17)
        p.lineTo(9, 12.5)
        p.lineTo(13, 16)
        p.lineTo(16, 13.5)
        p.lineTo(20.5, 17.5)
    elif name == 'file':
        p.moveTo(6, 3.5)
        p.lineTo(14, 3.5)
        p.lineTo(18.5, 8)
        p.lineTo(18.5, 20.5)
        p.lineTo(6, 20.5)
        p.closeSubpath()
        p.moveTo(14, 3.5)
        p.lineTo(14, 8)
        p.lineTo(18.5, 8)
    elif name == 'close':
        p.moveTo(6, 6)
        p.lineTo(18, 18)
        p.moveTo(18, 6)
        p.lineTo(6, 18)
    elif name == 'menu':
        for y in (6.5, 12, 17.5):
            p.moveTo(4.5, y)
            p.lineTo(19.5, y)
    elif name == 'drawer':
        p.addRoundedRect(QRectF(3.5, 8, 17, 12), 2, 2)
        p.moveTo(5.5, 8)
        p.lineTo(7.5, 4)
        p.lineTo(16.5, 4)
        p.lineTo(18.5, 8)
        p.moveTo(9.5, 12.5)
        p.lineTo(14.5, 12.5)
    elif name == 'poll':
        p.addRoundedRect(QRectF(3.5, 3.5, 17, 17), 3, 3)
        p.moveTo(8, 16.5)
        p.lineTo(8, 12)
        p.moveTo(12, 16.5)
        p.lineTo(12, 7.5)
        p.moveTo(16, 16.5)
        p.lineTo(16, 10)
    elif name == 'check':
        p.moveTo(5, 12.5)
        p.lineTo(10, 17.5)
        p.lineTo(19, 7)
    elif name == 'send':
        p.moveTo(4, 11.5)
        p.lineTo(20, 4)
        p.lineTo(13.5, 20)
        p.lineTo(11, 13.5)
        p.closeSubpath()
        p.moveTo(11, 13.5)
        p.lineTo(20, 4)
    elif name == 'clock':
        p.addEllipse(QRectF(3.5, 3.5, 17, 17))
        p.moveTo(12, 7.5)
        p.lineTo(12, 12)
        p.lineTo(15.5, 14)
    elif name == 'notice':
        p.moveTo(4, 10)
        p.lineTo(4, 14)
        p.lineTo(8, 14)
        p.lineTo(16, 18.5)
        p.lineTo(16, 5.5)
        p.lineTo(8, 10)
        p.closeSubpath()
        p.moveTo(19, 9.5)
        p.cubicTo(20.5, 11, 20.5, 13, 19, 14.5)
    elif name == 'gear':
        p.addEllipse(QRectF(9, 9, 6, 6))
        for i in range(8):
            a = i * math.pi / 4
            p.moveTo(12 + 6 * math.cos(a), 12 + 6 * math.sin(a))
            p.lineTo(12 + 8.5 * math.cos(a), 12 + 8.5 * math.sin(a))
        p.addEllipse(QRectF(6, 6, 12, 12))
    elif name == 'moon':
        p.moveTo(17.5, 15.5)
        p.cubicTo(12, 17, 7, 12, 8.5, 6)
        p.cubicTo(5, 7.5, 3.5, 11.5, 5, 15)
        p.cubicTo(7, 19.5, 13.5, 20.5, 17.5, 15.5)
    elif name == 'reply':
        p.moveTo(9.5, 5.5)
        p.lineTo(4, 11)
        p.lineTo(9.5, 16.5)
        p.moveTo(4, 11)
        p.lineTo(14, 11)
        p.cubicTo(18, 11, 20, 13.5, 20, 18.5)
    elif name == 'trash':
        p.moveTo(4.5, 6.5)
        p.lineTo(19.5, 6.5)
        p.moveTo(9.5, 6.5)
        p.lineTo(10, 3.5)
        p.lineTo(14, 3.5)
        p.lineTo(14.5, 6.5)
        p.moveTo(6.5, 6.5)
        p.lineTo(7.5, 20.5)
        p.lineTo(16.5, 20.5)
        p.lineTo(17.5, 6.5)
    elif name == 'gift':
        p.addRect(QRectF(4, 9, 16, 4))
        p.addRect(QRectF(5.5, 13, 13, 7.5))
        p.moveTo(12, 9)
        p.lineTo(12, 20.5)
        p.moveTo(12, 9)
        p.cubicTo(9, 3, 5, 6, 8.5, 9)
        p.moveTo(12, 9)
        p.cubicTo(15, 3, 19, 6, 15.5, 9)
    elif name == 'star':
        for i in range(10):
            r = 9 if i % 2 == 0 else 4
            a = -math.pi / 2 + i * math.pi / 5
            pt = QPointF(12 + r * math.cos(a), 12.5 + r * math.sin(a))
            if i == 0:
                p.moveTo(pt)
            else:
                p.lineTo(pt)
        p.closeSubpath()
    elif name == 'edit':
        # 연필: 몸통(비스듬한 사각) + 촉 + 밑줄
        p.moveTo(15.5, 4.5)
        p.lineTo(19.5, 8.5)
        p.lineTo(9, 19)
        p.lineTo(4.5, 19.5)
        p.lineTo(5, 15)
        p.closeSubpath()
        p.moveTo(13, 7)
        p.lineTo(17, 11)
        p.moveTo(12.5, 20.5)
        p.lineTo(19.5, 20.5)
    elif name == 'back':
        p.moveTo(14.5, 5)
        p.lineTo(7.5, 12)
        p.lineTo(14.5, 19)
    elif name in ('eye', 'eye_off'):
        p.moveTo(2.5, 12)
        p.cubicTo(5, 7, 8.5, 5, 12, 5)
        p.cubicTo(15.5, 5, 19, 7, 21.5, 12)
        p.cubicTo(19, 17, 15.5, 19, 12, 19)
        p.cubicTo(8.5, 19, 5, 17, 2.5, 12)
        p.closeSubpath()
        p.addEllipse(QRectF(9, 9, 6, 6))
        if name == 'eye_off':
            p.moveTo(4, 4)
            p.lineTo(20, 20)
    else:
        p.addRect(QRectF(5, 5, 14, 14))
    return p, filled


def paint_icon(painter, name, rect, color, stroke=1.7, filled=None):
    path, default_filled = _icon_path(name)
    filled = default_filled if filled is None else filled
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    color = QColor(color)
    if filled:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
    else:
        pen = QPen(color, stroke)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.restore()


def make_icon(name, color=None, size=24, filled=None):
    """QIcon 생성 (HiDPI 대응: 2배 해상도로 렌더링)"""
    color = color or theme.c('text')
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    paint_icon(painter, name, QRectF(0, 0, size, size), color, filled=filled)
    painter.end()
    return QIcon(pm)


class IconButton(QPushButton):
    """아이콘만 있는 납작한 버튼"""

    def __init__(self, icon_name, tooltip='', size=22, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self._icon_size = size
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip.split('(')[0].strip() or icon_name)   # 화면낭독기용 이름
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty('flat', True)
        self.setFixedSize(size + 12, size + 12)
        self.refresh_icon()

    def refresh_icon(self, color=None):
        self.setIcon(make_icon(self.icon_name, color or theme.c('text_sub'), self._icon_size))
        self.setIconSize(QSize(self._icon_size, self._icon_size))


# ---------------------------------------------------------------- 보이기/숨기기
def set_shown(widget, shown):
    """setVisible() 대신 쓴다 (2026-10-15). 부모가 아직 없는 위젯(레이아웃에 넣기 전, 생성자 안)에 setVisible(True) 를
    부르면 Qt 가 그 위젯을 최상위 창으로 띄워 Windows 에서 작은 창들이 번쩍였다가 사라진다(친구 목록 상태메시지 칩 ·
    안 읽은 배지 · 조직도 인원 알약). 부모가 없으면 숨기기만 바로 적용하고, 보이기는 '명시적 숨김' 표시만 지워
    레이아웃에 들어가 부모가 보일 때 함께 보이게 둔다."""
    if shown and widget.parent() is None:
        if widget.testAttribute(Qt.WA_WState_ExplicitShowHide):
            widget.setAttribute(Qt.WA_WState_ExplicitShowHide, False)   # 새로 만든 위젯과 같은 상태로 되돌린다
        return
    widget.setVisible(shown)


# ---------------------------------------------------------------- 줄바꿈 배치 (칩 목록)
class FlowWidget(QWidget):
    """자식 위젯을 줄바꿈하며 배치하는 간단한 흐름 레이아웃"""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.spacing = spacing

    def set_widgets(self, widgets):
        for w in self.items:
            w.hide()
            w.deleteLater()
        self.items = list(widgets)
        for w in self.items:
            w.setParent(self)
            w.show()
        self._relayout()

    def _relayout(self):
        x = y = 0
        line_h = 0
        width = max(self.width(), 200)
        for w in self.items:
            hint = w.sizeHint()
            if x and x + hint.width() > width:
                x = 0
                y += line_h + self.spacing
                line_h = 0
            w.setGeometry(x, y, hint.width(), hint.height())
            x += hint.width() + self.spacing
            line_h = max(line_h, hint.height())
        self.setMinimumHeight(y + line_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()


# ---------------------------------------------------------------- 키보드 포커스 표시 (웹의 :focus-visible)
FOCUS_VISIBLE = 'focusVisible'
KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)


class FocusVisibleFilter(QObject):
    """단추가 키보드(Tab · 단축키)로 포커스를 받았을 때만 focusVisible=true.
    마우스로 누르거나, 다른 창을 닫고 돌아와 포커스가 되돌아온 단추에는 포커스 테두리를 그리지 않는다
    (예: 더보기 [보기 ›]를 누른 뒤 남던 네이비 테두리). QSS 는 QPushButton[focusVisible="true"]:focus 로 그린다"""

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.FocusIn, QEvent.FocusOut) and isinstance(obj, QAbstractButton):
            visible = kind == QEvent.FocusIn and event.reason() in KEYBOARD_REASONS
            if bool(obj.property(FOCUS_VISIBLE)) != visible:
                obj.setProperty(FOCUS_VISIBLE, visible)
                obj.style().unpolish(obj)
                obj.style().polish(obj)
                obj.update()
        return False


def install_focus_visible(app):
    """앱 전체에 한 번만 설치 (다시 불러도 그대로)"""
    if app is None or getattr(app, '_focus_visible_filter', None) is not None:
        return
    app._focus_visible_filter = FocusVisibleFilter(app)
    app.installEventFilter(app._focus_visible_filter)


# ---------------------------------------------------------------- 공용 도우미
def human_size(size, sep=' '):
    """바이트 수 → '512 B' · '1.5 MB' (sep='' 면 '1.5MB'). 숫자가 아니면 ''"""
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{int(size)}{sep}{unit}' if unit == 'B' else f'{size:.1f}{sep}{unit}'
        size /= 1024
    return ''


def disconnect_signals(*pairs):
    """(시그널, 슬롯) 쌍을 끊는다. 이미 끊겼거나 객체가 지워졌으면 조용히 넘어간다
    (창이 닫힌 뒤 오래 사는 AppState 시그널이 지워진 위젯을 부르지 않게)"""
    for signal, slot in pairs:
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass


_CHECK_IMAGES = {}


def check_mark_image(color):
    """체크박스 체크 표시 이미지 경로 (QSS image: url(...)용, 색상별로 한 번만 만든다)"""
    if color not in _CHECK_IMAGES:
        import os
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"kcs_check_{color.strip('#')}.png")
        make_icon('check', color, 14).pixmap(28, 28).save(path, 'PNG')
        _CHECK_IMAGES[color] = path.replace(os.sep, '/')
    return _CHECK_IMAGES[color]


def underline_tabs_qss():
    """밑줄 탭 (macOS 기본 회색 탭 판 대신): 선택된 탭만 굵게 + 강조색 밑줄"""
    c = theme.c
    return f"""
        QTabWidget::pane {{ border: none; border-top: 1px solid {c('border')}; }}
        QTabBar::tab {{ background: transparent; color: {c('text_sub')}; padding: 8px 18px; border: none;
            font-size: {theme.fs('body')}px; }}
        QTabBar::tab:selected {{ color: {c('text')}; border-bottom: 2px solid {c('yellow')}; font-weight: bold; }}
    """


def check_box_qss():
    """테마 색 체크박스 (16px 둥근 네모, 체크되면 강조색 + 체크 표시)"""
    c = theme.c
    return f"""
        QCheckBox {{ color: {c('text')}; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid {c('text_hint')};
            background: {c('bg')}; }}
        QCheckBox::indicator:hover {{ border-color: {c('yellow')}; }}
        QCheckBox::indicator:checked {{ background: {c('yellow')}; border-color: {c('yellow')};
            image: url({check_mark_image(c('on_accent'))}); }}
    """


# ---------------------------------------------------------------- 입력 컨트롤 (콤보 · 시간 · 날짜 · 숫자)
_QSS_ICON_CACHE = {}


def qss_icon_path(name, color, rotate=0, stroke=2.2):
    """QSS image: url(...) 용 아이콘 PNG 경로 (임시 폴더에 캐시, 경로 구분자는 '/')"""
    import os
    import tempfile
    from PyQt5.QtGui import QTransform
    key = (name, color, rotate, stroke)
    cached = _QSS_ICON_CACHE.get(key)
    if cached and os.path.exists(cached):
        return cached
    folder = os.path.join(tempfile.gettempdir(), 'kcs_calendar_icons')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}_{color.strip('#')}_{rotate}_{stroke}.png")
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_icon(p, name, QRectF(0, 0, 48, 48), color, stroke=stroke)
    p.end()
    if rotate:
        pm = pm.transformed(QTransform().rotate(rotate))
    pm.save(path, 'PNG')
    _QSS_ICON_CACHE[key] = path.replace('\\', '/')
    return _QSS_ICON_CACHE[key]


def use_date_picker(widget):
    """달력 팝업을 쓰는 날짜 칸이면 앱 디자인 달력(date_picker.DatePickerCalendar)으로 바꾼다 (한 번만)"""
    from PyQt5.QtWidgets import QDateTimeEdit
    if not isinstance(widget, QDateTimeEdit) or not widget.calendarPopup():
        return False
    from client.kakao.date_picker import DatePickerCalendar
    if isinstance(widget.calendarWidget(), DatePickerCalendar):
        return False
    widget.setCalendarWidget(DatePickerCalendar(widget))
    return True


def blocks_wheel(widget):
    """마우스 휠로 값을 바꾸지 않을 입력 칸: 날짜 · 시간 칸은 항상(달력 · 키보드로 고른다),
    콤보 · 숫자 칸은 포커스가 없을 때 (누른 뒤에는 휠로 고를 수 있다)"""
    from PyQt5.QtWidgets import QAbstractSpinBox, QDateTimeEdit
    if isinstance(widget, QDateTimeEdit):
        return True
    if isinstance(widget, (QAbstractSpinBox, QComboBox)):
        return not widget.hasFocus()
    return False


def _scroll_area_of(widget):
    from PyQt5.QtWidgets import QAbstractScrollArea
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class ComboDelegateFilter(QObject):
    """콤보박스 목록을 앱 디자인대로: QSS(줄 높이 · 여백 · 선택 색)를 따르는 QStyledItemDelegate,
    macOS 검은 메뉴 테두리 대신 QSS 테두리, 칸을 덮지 않고 칸 바로 아래 같은 너비로 연다 (아래가 모자라면 위로)"""
    GAP = 4

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Wheel and blocks_wheel(obj):
            # 대화상자를 스크롤하다 커서가 지나간 날짜 칸이 값을 바꾸지 않게 - 스크롤은 바깥 목록이 받는다.
            # (일정 창에서 시작 날짜 위로 한 번 굴렸더니 2026년이 2027년이 되어 '등록한 일정이 달력에 없다')
            area = _scroll_area_of(obj)
            if area is not None:
                QApplication.sendEvent(area.viewport(), event)
            return True
        if kind == QEvent.Polish:
            use_date_picker(obj)
        if kind == QEvent.Polish and isinstance(obj, QComboBox):
            if not isinstance(obj.itemDelegate(), QStyledItemDelegate):
                obj.setItemDelegate(QStyledItemDelegate(obj))
            container = obj.view().parentWidget()
            if container is not None and not container.property('kcsPopup'):
                container.setProperty('kcsPopup', True)
                container.setFrameShape(container.NoFrame)
                container.setWindowFlag(Qt.NoDropShadowWindowHint, True)   # macOS 의 진한 창 윤곽선을 없앤다
                container.installEventFilter(self)
        elif kind == QEvent.Show and obj.property('kcsPopup') and isinstance(obj.parentWidget(), QComboBox):
            self.place_below(obj.parentWidget(), obj)
        return False

    def place_below(self, combo, popup):
        width = max(combo.width(), popup.width() if combo.view().width() > combo.width() else 0)
        height = popup.height()
        below = combo.mapToGlobal(QPoint(0, combo.height() + self.GAP))
        screen = combo.screen().availableGeometry() if hasattr(combo, 'screen') else None
        if screen is not None and below.y() + height > screen.bottom():
            above = combo.mapToGlobal(QPoint(0, -self.GAP - height))
            if above.y() >= screen.top():
                below = above
        popup.setGeometry(below.x(), below.y(), width, height)


def install_input_controls(app):
    """앱 전체에 한 번만 설치 (다시 불러도 그대로) - 스타일시트는 input_controls_qss()"""
    if app is None or getattr(app, '_combo_delegate_filter', None) is not None:
        return
    app._combo_delegate_filter = ComboDelegateFilter(app)
    app.installEventFilter(app._combo_delegate_filter)


class DialogTitleBarFilter(QObject):
    """모든 팝업(대화상자 · 알림 상자)이 뜰 때: 메신저 창 가운데에 두고(center_on_messenger),
    제목 표시줄을 본문과 같은 색으로 (macOS, 다른 OS 는 아무것도 안 함).
    본문 색은 theme.c('bg'), 다른 배경을 쓰는 창은 속성 titleBarColor 로 바꾼다 (QDialog 가 아닌 창도 이 속성이면 대상)"""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and wants_matched_title_bar(obj):
            match_dialog_title_bar(obj)
            if isinstance(obj, QDialog) and center_on_messenger(obj):
                # 알림 상자처럼 showEvent 에서 크기를 다시 정하는 창: 크기가 바뀌었으면 한 번 더 가운데로
                size = obj.size()
                QTimer.singleShot(0, lambda d=obj, s=size: _recenter_if_resized(d, s))
        return False


def _recenter_if_resized(dialog, size):
    try:
        if dialog.isVisible() and dialog.size() != size:
            center_on_messenger(dialog)
    except RuntimeError:          # 그 사이 닫혀 지워진 창
        pass


def messenger_window_for(dialog):
    """팝업을 가운데 둘 기준 창: 띄운 창(메신저 메인 창 · 대화방 창), 없으면 보이는 메신저 메인 창"""
    from PyQt5.QtWidgets import QApplication, QMainWindow
    parent = dialog.parentWidget()
    window = parent.window() if parent is not None else None
    if window is not None and window is not dialog and window.isVisible() and not window.isMinimized():
        return window
    mains = [w for w in QApplication.topLevelWidgets()
             if isinstance(w, QMainWindow) and w.isVisible() and not w.isMinimized()]
    return mains[0] if mains else None


def center_on_messenger(dialog):
    """모든 팝업은 메신저 창 가운데에 띄운다 (화면 밖으로 나가지 않게). 기준 창이 없으면(로그인 전) 그대로.
    속성 keepPosition=True 인 창 · Qt.Popup(버튼 옆 목록)은 건드리지 않는다. 옮겼으면 True"""
    if dialog.property('keepPosition') or (dialog.windowType() == Qt.Popup):
        return False
    host = messenger_window_for(dialog)
    if host is None:
        return False
    frame = dialog.frameGeometry()
    center = host.frameGeometry().center()
    x, y = center.x() - frame.width() // 2, center.y() - frame.height() // 2
    screen = host.screen().availableGeometry() if hasattr(host, 'screen') and host.screen() else None
    if screen is not None:
        x = max(screen.left(), min(x, screen.right() - frame.width() + 1))
        y = max(screen.top(), min(y, screen.bottom() - frame.height() + 1))
    dialog.move(x, y)
    return True


def wants_matched_title_bar(widget):
    if not isinstance(widget, QWidget) or not widget.isWindow():
        return False
    if widget.property('titleBarColor'):
        return True
    return isinstance(widget, QDialog) and not any(       # 이들은 macOS 기본 창이 직접 그린다
        widget.inherits(name) for name in ('QFileDialog', 'QColorDialog', 'QFontDialog'))


def match_dialog_title_bar(dialog):
    from client.kakao import macos
    return macos.match_title_bar(dialog, dialog.property('titleBarColor') or theme.c('bg'), theme.is_dark())


def install_dialog_title_bars(app):
    """앱 전체에 한 번만 설치 (다시 불러도 그대로). 테마를 바꾸면 열린 팝업에도 다시 입힌다"""
    if app is None:
        return
    if getattr(app, '_dialog_title_filter', None) is None:
        app._dialog_title_filter = DialogTitleBarFilter(app)
        app.installEventFilter(app._dialog_title_filter)
    for w in app.topLevelWidgets():
        if w.isVisible() and wants_matched_title_bar(w):
            match_dialog_title_bar(w)


def input_controls_qss():
    """앱 전역: 콤보박스 화살표 · 시간/날짜/숫자 칸의 위아래 단추를 테두리 없는 쉐브론으로.
    base_qss 가 칸에 둥근 테두리를 주면 Qt 가 화살표 칸을 기본 모양(네모 칸 · 끊긴 선)으로 그리던 것을 막는다"""
    c = theme.c
    down = qss_icon_path('back', c('text_sub'), rotate=-90, stroke=3.0)
    up = qss_icon_path('back', c('text_sub'), rotate=90, stroke=3.0)
    down_off = qss_icon_path('back', c('text_hint'), rotate=-90, stroke=3.0)
    up_off = qss_icon_path('back', c('text_hint'), rotate=90, stroke=3.0)
    spins = 'QAbstractSpinBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit, QDateTimeEdit'
    return f"""
        QComboBox {{ padding-right: 34px; combobox-popup: 0; }}
        QComboBox:hover, QAbstractSpinBox:hover {{ border-color: {c('text_hint')}; }}
        QComboBox:focus, QComboBox:on, QAbstractSpinBox:focus {{ border: 1px solid {c('yellow')}; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
            width: 30px; border: none; background: transparent; }}
        QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
        QComboBox::down-arrow:disabled {{ image: url("{down_off}"); }}
        QComboBox QAbstractItemView {{ background: {c('bg')}; color: {c('text')}; border: 1px solid {c('border')};
            padding: 4px; outline: 0; selection-background-color: {c('bg_selected')};
            selection-color: {c('text')}; }}
        QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 6px; }}
        QComboBox QAbstractItemView::item:hover {{ background: {c('bg_hover')}; }}
        QComboBox QAbstractItemView::item:selected {{ background: {c('bg_selected')}; color: {c('text')}; }}
        {spins} {{ padding-right: 30px; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 26px;
            border: none; background: transparent; }}
        QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin: 4px 4px 0 0; }}
        QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 4px 4px 0; }}
        QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
            background: {c('bg_hover')}; border-radius: 4px; }}
        QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
        QAbstractSpinBox::down-arrow {{ image: url("{down}"); width: 9px; height: 9px; }}
        QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url("{up_off}"); }}
        QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: url("{down_off}"); }}
    """


# ---------------------------------------------------------------- 아바타
def label_buttons_for_accessibility(root):
    """이름이 없는 버튼에 툴팁/글자를 화면낭독기 이름으로 붙인다 (아이콘만 있는 버튼 대비)"""
    from PyQt5.QtWidgets import QAbstractButton
    for btn in root.findChildren(QAbstractButton):
        if not btn.accessibleName():
            name = (btn.text() or btn.toolTip() or '').replace('&', '').strip()
            if name:
                btn.setAccessibleName(name.split('\n')[0])


def avatar_color(username):
    digest = hashlib.md5(username.encode('utf-8')).digest()
    return AVATAR_COLORS[digest[0] % len(AVATAR_COLORS)]


def avatar_pixmap(username, display_name, size, online=None):
    """카카오톡 스타일 둥근 사각형(squircle) 프로필 이미지"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(0, 0, size, size)
    radius = size * 0.38
    base = QColor(avatar_color(username or '?'))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    grad = QLinearGradient(0, 0, 0, size)              # 위가 아주 조금 밝은 잉크 (광택 아닌 종이 위 농담)
    grad.setColorAt(0, base.lighter(112))
    grad.setColorAt(1, base)
    p.fillPath(path, QBrush(grad))
    # 이름 첫 글자
    initial = (display_name or username or '?')[0]
    f = theme.font(max(9, int(size * 0.36)), bold=True)
    f.setPixelSize(max(9, int(size * 0.36)))
    p.setFont(f)
    p.setPen(QColor('#FFFFFF'))
    p.drawText(rect, Qt.AlignCenter, initial)
    if online is not None:
        d = max(8, size * 0.26)
        dot = QRectF(size - d, size - d, d, d)
        p.setBrush(QColor('#3DD16B') if online else QColor('#BDBDBD'))
        p.setPen(QPen(QColor(theme.c('bg')), max(1.5, size * 0.05)))
        p.drawEllipse(dot)
    p.end()
    return pm


_photo_cache = {}


def _dot(p, size, color):
    d = max(8, size * 0.26)
    p.setBrush(QColor(color))
    p.setPen(QPen(QColor(theme.c('bg')), max(1.5, size * 0.05)))
    p.drawEllipse(QRectF(size - d, size - d, d, d))


def photo_avatar_pixmap(data, size):
    """사진 바이트 → 가운데 정사각형으로 잘라 squircle 마스크 적용. 실패 시 None"""
    from PyQt5.QtGui import QImage
    image = QImage()
    if not image.loadFromData(data):
        return None
    side = min(image.width(), image.height())
    image = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.38, size * 0.38)
    p.setClipPath(path)
    p.drawImage(QRectF(0, 0, size, size), image)
    # 증명사진은 대부분 흰 배경이라 흰 목록 위에서 테두리가 사라져 이니셜 아바타보다 작아 보인다
    # → 같은 모양의 가는 안쪽 선으로 사진 칸 크기를 드러낸다 (사진 · 빈 사진 같은 크기로 보이게, 2026-10-10)
    p.setClipping(False)
    edge = max(1.0, size / 44.0)
    p.setPen(QPen(theme.qc('avatar_ring'), edge))
    p.setBrush(Qt.NoBrush)
    inset = edge / 2
    r = size * 0.38 - inset
    p.drawRoundedRect(QRectF(inset, inset, size - edge, size - edge), r, r)
    p.end()
    return pm


def user_avatar_pixmap(state, username, size, show_presence=False):
    """프로필 사진(있으면) 또는 이니셜 아바타 + 선택적으로 상태(presence) 점"""
    from client.kakao.state import PRESENCE_COLORS
    data = state.avatar_bytes(username) if state is not None else None
    presence = state.presence_of(username) if (state is not None and show_presence) else None
    key = (username, hash(data) if data else None, size, presence, theme.is_dark(),
           state.display_name(username) if state is not None else username)
    cached = _photo_cache.get(key)
    if cached is not None:
        return cached
    base = photo_avatar_pixmap(data, size) if data else None
    if base is None:
        name = state.display_name(username) if state is not None else username
        base = avatar_pixmap(username, name, size)
    if presence:
        p = QPainter(base)
        p.setRenderHint(QPainter.Antialiasing)
        _dot(p, size, PRESENCE_COLORS.get(presence, '#BDBDBD'))
        p.end()
    if len(_photo_cache) > 500:
        _photo_cache.clear()
    _photo_cache[key] = base
    return base


def group_avatar_pixmap(members, names, size):
    """그룹 채팅방 아바타 - 최대 4명 프로필을 격자로 배치"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    members = list(members)[:4]
    if len(members) <= 1:
        p = QPainter(pm)
        name = members[0] if members else '?'
        p.drawPixmap(0, 0, avatar_pixmap(name, names.get(name, name), size))
        p.end()
        return pm
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    half = size // 2 - 1
    if len(members) == 2:
        spots = [(0, 0), (size - half, size - half)]
    elif len(members) == 3:
        spots = [((size - half) // 2, 0), (0, size - half), (size - half, size - half)]
    else:
        spots = [(0, 0), (size - half, 0), (0, size - half), (size - half, size - half)]
    for (x, y), member in zip(spots, members):
        p.drawPixmap(x, y, avatar_pixmap(member, names.get(member, member), half))
    p.end()
    return pm


class Avatar(QLabel):
    clicked = pyqtSignal()

    def __init__(self, username='', display_name='', size=44, online=None, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_user(username, display_name, online)

    def set_user(self, username, display_name='', online=None):
        self.setPixmap(avatar_pixmap(username, display_name, self._size, online))

    def set_state_user(self, state, username, show_presence=False):
        self.setPixmap(user_avatar_pixmap(state, username, self._size, show_presence))

    def set_group(self, members, names):
        self.setPixmap(group_avatar_pixmap(members, names, self._size))

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class Badge(QWidget):
    """빨간 안 읽은 수 배지 (0이면 숨김, 999+ 표기)"""

    def __init__(self, count=0, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.set_count(count)

    def set_count(self, count):
        self.count = int(count or 0)
        self.text = '999+' if self.count > 999 else str(self.count)
        h = theme.px(18)
        w = max(h, theme.px(8) * len(self.text) + theme.px(10))
        self.setFixedSize(w, h)
        set_shown(self, self.count > 0)   # 생성자에서 setVisible(True) 를 부르면 최상위 창으로 번쩍인다
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(theme.c('badge'))))
        r = QRectF(self.rect())
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor('#FFFFFF'))
        p.setFont(theme.font('caption', bold=True))
        p.drawText(r, Qt.AlignCenter, self.text)
