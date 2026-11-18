# -*- coding: utf-8 -*-
"""
ChatRoomWindow - 카카오톡 PC 스타일 채팅방 창
말풍선(연속 메시지 프로필 생략, 꼬리), 안 읽은 사람 수, 답장, 공감, 삭제, 공지 배너,
투표/일정/파일/사진/이모티콘 카드, 대화 검색, 서랍, 이모티콘 팝업, 예약 메시지, @멘션 자동완성.

데이터와 액션은 오직 AppState(client/kakao/state.py)를 통해서만 다룬다.
"""

import html
import math
import os
import re
from datetime import datetime

from PyQt5.QtCore import (Qt, QTimer, QUrl, QRectF, QPointF, QPoint, QDateTime, QSize, QEvent, pyqtSignal,
                          QVariantAnimation, QEasingCurve)
from PyQt5.QtGui import (QPainter, QPainterPath, QPen, QColor, QPixmap, QTextDocument, QFontMetrics, QTextCursor, QKeySequence, QIcon)
from PyQt5.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
                             QPushButton, QToolButton, QTextEdit, QLineEdit, QListWidget, QListWidgetItem,
                             QMenu, QDialog, QCheckBox, QRadioButton, QButtonGroup,
                             QApplication, QFileDialog, QMessageBox, QInputDialog, QDateTimeEdit, QSizePolicy, QShortcut, QActionGroup, QAbstractButton, QBoxLayout)

from client.kakao import theme, osenv

try:  # 일부 PyQt5 빌드(macOS 휠 등)에는 접근성 이벤트 API가 없다 → 이름만 바꾸고 알림 이벤트는 생략
    from PyQt5.QtGui import QAccessible, QAccessibleEvent
except ImportError:  # pragma: no cover
    QAccessible = QAccessibleEvent = None
from client.kakao.state import (parse_dt, URL_RE, PRESENCE_COLORS, PRESENCE_LABELS, BOT_NAME, fmt_day, CALL_MODE_LABELS)
from client.kakao.widgets import (IconButton, Avatar, avatar_pixmap, paint_icon, user_avatar_pixmap, make_icon, decode_pixmap,
                                  exec_and_release,
                                  human_size)
from client.kakao.file_guard import FILE_BLOCKED

# 공용 바탕 · 대화상자는 room_base / room_dialogs 로 나눴다 (예전 import 경로 chat_room.X 도 그대로 된다)
from client.kakao.room_base import (APP_NAME, AV, fmt_time, fmt_short_date, event_when, to_qcolor, _label, icon_pixmap, _Dialog, dialog_qss, _buttons)  # noqa: E402,F401
from client.kakao.room_dialogs import (InviteRow, InviteDialog, PollCreateDialog, ImageViewer, DrawerDialog, LadderDialog, BoardDialog, ContactPickDialog, SUMMARY_TAGS, summary_html, SummaryDialog)  # noqa: E402,F401


DELETED_TEXT = '메시지가 삭제되었습니다'
EXPIRED_TEXT = '보관 기간이 지난 파일입니다'
SECRET_SUBTITLE = '종단간 암호화된 비밀채팅'
SECRET_BANNER = '🔒 메시지는 이 기기에서만 해독됩니다'
CALL_MODES = CALL_MODE_LABELS
CALL_ICONS = {'voice': 'phone', 'video': 'video', 'voiceroom': 'mic'}
CALL_GREEN = '#22B04B'
KEYWORD_ACCENT = '#F08A24'


def keyword_text_color():
    """키워드 칩 글자·종 아이콘 색: 배경 대비 4.5:1 이상 (주황 #F08A24 은 흰 배경에서 2.5:1)"""
    return '#FFB366' if theme.is_dark() else '#A85200'
LADDER_ROWS = 10
LADDER_COLORS = ['#E8504A', '#2F7DE1', '#2DB36B', '#F0A019', '#8E5FE0',
                 '#E2549B', '#17A6B8', '#7A8B23', '#C2652E', '#5866C9']
SILENT_KEYS = osenv.shortcut_text('Ctrl+Shift+Enter')   # macOS '⇧⌘Enter'


def _wallpaper_api():
    """wallpaper 모듈을 지연 로드 (없으면 None → 테마 기본 배경)"""
    try:
        from client.kakao import wallpaper
        return wallpaper
    except ImportError:
        return None


def paint_icon_invite(painter, rect, color):
    """'대화상대 초대' 아이콘: 사람 + 플러스"""
    w = rect.width()
    paint_icon(painter, 'friends', QRectF(rect.x() - w * 0.08, rect.y() + w * 0.06, w * 0.86, w * 0.86), color)
    paint_icon(painter, 'plus', QRectF(rect.x() + w * 0.52, rect.y() - w * 0.02, w * 0.5, w * 0.5), color, stroke=2.2)


class InviteButton(IconButton):
    def refresh_icon(self, color=None):
        size = self._icon_size
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        paint_icon_invite(p, QRectF(0, 0, size, size), color or theme.c('text_sub'))
        p.end()
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(size, size))

def _presence_dot(color, size=13):
    """헤더 부제목 앞 상태 점"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6))
    p.end()
    return pm


TAIL = 6           # 말풍선 꼬리 폭
PAD_H = 10         # 말풍선 좌우 여백
PAD_V = 7          # 말풍선 상하 여백
RADIUS = 17        # 말풍선 모서리 (KCS Ink: 더 둥글게, 첫 말풍선은 말한 쪽 위만 4px)
CARD_W = 250       # 투표/일정/파일 카드 기본 폭
REACTIONS = ['❤️', '👍', '😆', '😮', '😢', '✅']
# 더 많은 공감 (카카오톡 v26.4.0: 6종 → 114종, 말풍선당 최대 30종). 자주 쓰는 업무·감정 위주 72종
MORE_REACTIONS = [
    '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💯', '✨', '🔥', '🎉',
    '👍', '👎', '👏', '🙌', '🙏', '💪', '🤝', '👌', '✌️', '🫡', '👀', '🙋',
    '😀', '😆', '😂', '🤣', '😊', '😍', '🥰', '😘', '😎', '🤩', '🥳', '😇',
    '😮', '😲', '🤔', '🧐', '😅', '😳', '🥹', '😢', '😭', '😤', '😡', '🤯',
    '😴', '🤒', '😷', '🫠', '🙄', '😬', '✅', '☑️', '❌', '⭕', '❗', '❓',
    '📌', '📎', '📅', '⏰', '☕', '🍀', '🎂', '🎁', '🚀', '💡', '📢', '🆗']
SUMMARY_CHIP_MIN = 20     # 이만큼 안 읽고 들어오면 '안 읽은 대화 요약' 칩 표시
REACTION_BAR_MAX = 6      # 말풍선 아래에 바로 보이는 공감 종류 (나머지는 +N)
RENDER_PAGE = 150         # 한 번에 화면에 만드는 메시지 수 (위로 올리면 150개씩 더, 오래 쓴 큰 방도 바로 열림)
EMOTICONS = ['😀', '😂', '🤣', '😊', '😍', '🥰', '😘', '😎', '🤔', '😮',
             '😢', '😭', '😡', '🥳', '😴', '🤗', '🙄', '😅', '🙏', '👏',
             '👍', '👎', '👌', '✌️', '💪', '❤️', '💔', '🔥', '🎉', '🎂',
             '🌸', '☕', '🍺', '🍗', '🐶', '🐱', '⭐', '✅', '❌', '💯']
RSVP_LABELS = [('yes', '참석'), ('no', '불참'), ('maybe', '미정')]
LONG_TOKEN_RE = re.compile(r'\S{22,}')
EMOJI_FAMILIES = osenv.emoji_families()   # 이 OS 이모지 글꼴 먼저
# 이모지 표현 선택자(U+FE0F)가 붙은 글자는 한글 폰트 체인에서 대체 글꼴을 못 찾아 □로 보이므로
# 이모지 폰트를 명시한다
VS16_RE = re.compile('(.\ufe0f(?:\u200d.\ufe0f?)*)')


def emoji_css():
    chain = theme.installed(EMOJI_FAMILIES) + theme.font_families()   # 설치된 글꼴만 (theme 이 한 번만 조회)
    return 'font-family: ' + ', '.join(f'"{n}"' for n in chain) + ';'



_time_col_cache = {}


def _time_column_width():
    """말풍선 옆 시간 열 폭('오후 12:59' + 여백). 글자 크기 배율별로 한 번만 잰다"""
    key = theme.px(10)
    if key not in _time_col_cache:
        _time_col_cache[key] = QFontMetrics(theme.font('micro')).horizontalAdvance('오후 12:59') + 8
    return _time_col_cache[key]


def fmt_date(d):
    return fmt_day(d)



def _breakable(text):
    """줄바꿈 지점이 없는 긴 토큰(URL 등)에 폭 0 공백을 넣어 말풍선 밖으로 넘치지 않게 한다"""
    return LONG_TOKEN_RE.sub(lambda mo: '\u200b'.join(mo.group(0)[i:i + 12]
                                                       for i in range(0, len(mo.group(0)), 12)), text)


def _escape(text):
    out = html.escape(_breakable(text), quote=False)
    out = out.replace('\r\n', '\n').replace('\n', '<br>')
    out = VS16_RE.sub(lambda mo: '<span style="font-family:' + ','.join(f"'{n}'" for n in EMOJI_FAMILIES)
                      + f'">{mo.group(1)}</span>', out)
    return out.replace('  ', '&nbsp; ')


def message_html(text, mention_names, link_color, mention_color):
    """본문 → 리치텍스트: URL 링크 + @멘션 강조 + 줄바꿈 유지"""
    parts = []
    pos = 0
    mention_re = None
    if mention_names:
        names = sorted({n for n in mention_names if n}, key=len, reverse=True)
        mention_re = re.compile('@(' + '|'.join(re.escape(html.escape(n)) for n in names) + ')')

    def plain(chunk):
        esc = _escape(chunk)
        if mention_re:
            esc = mention_re.sub(
                lambda mo: f'<span style="color:{mention_color}; font-weight:bold;">{mo.group(0)}</span>', esc)
        return esc

    for mo in URL_RE.finditer(text):
        parts.append(plain(text[pos:mo.start()]))
        url = mo.group(0)
        parts.append(f'<a href="{html.escape(url)}" style="color:{link_color}; text-decoration:underline;">'
                     f'{html.escape(_breakable(url))}</a>')
        pos = mo.end()
    parts.append(plain(text[pos:]))
    return ''.join(parts)


def fit_text(label, max_w, pad_x=0, pad_y=0):
    """리치텍스트 QLabel을 내용 폭(최대 max_w)에 맞추고 정확한 높이로 고정
    pad_x/pad_y: 스타일시트 padding(좌우 합/상하 합)"""
    label.ensurePolished()
    doc = QTextDocument()
    doc.setDefaultFont(label.font())
    doc.setDocumentMargin(0)
    if label.textFormat() == Qt.PlainText:
        doc.setPlainText(label.text())
    else:
        doc.setHtml(label.text())
    ideal = int(math.ceil(doc.idealWidth())) + 2 + pad_x
    w = max(8, min(ideal, int(max_w)))
    h = label.heightForWidth(w)
    if h <= 0 or pad_x:
        doc.setTextWidth(max(8, w - pad_x))
        h = int(math.ceil(doc.size().height())) + pad_y
    label.setFixedSize(w, h)
    return w, h


def fit_wrap(label, width):
    """고정 폭에서 줄바꿈되는 QLabel 높이를 정확히 고정"""
    label.ensurePolished()
    width = max(8, int(width))
    h = label.heightForWidth(width)
    if h <= 0:
        h = label.sizeHint().height()
    label.setFixedSize(width, h)


def elide(label, text, width):
    label.ensurePolished()
    label.setText(QFontMetrics(label.font()).elidedText(text, Qt.ElideRight, max(10, int(width))))



def elide_lines(font, text, width, max_lines):
    """최대 max_lines 줄로 줄바꿈하고 넘치면 마지막 줄을 '…'로 자른다 (plain text 반환)"""
    fm = QFontMetrics(font)
    width = max(10, int(width))
    rest = ' '.join((text or '').split())
    lines = []
    while rest and len(lines) < max_lines - 1:
        if fm.horizontalAdvance(rest) <= width:
            break
        lo, hi = 1, len(rest)
        while lo < hi:                      # 폭에 들어가는 가장 긴 앞부분
            mid = (lo + hi + 1) // 2
            if fm.horizontalAdvance(rest[:mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        cut = lo
        space = rest.rfind(' ', 0, cut + 1)
        if space > cut // 2:                # 가능하면 단어 경계에서 끊는다
            cut = space
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        lines.append(fm.elidedText(rest, Qt.ElideRight, width))
    return '\n'.join(lines)



class _ClickLabel(QLabel):
    """누를 수 있는 글자 (읽음 숫자 → 안 읽은 사람 보기)"""
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)



def paint_extra_icon(painter, name, rect, color, stroke=1.7):
    """widgets 아이콘 세트에 없는 아이콘 (24x24 좌표): phone / video / mic / lock / at / ladder / board"""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    pen = QPen(to_qcolor(color), stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if name == 'phone':
        path.moveTo(5.2, 4.6)
        path.cubicTo(5.2, 13.6, 10.6, 19.2, 19.4, 19.2)
        path.lineTo(20.2, 15.6)
        path.lineTo(16.2, 13.9)
        path.lineTo(14.2, 16.0)
        path.cubicTo(11.6, 15.0, 9.1, 12.5, 8.0, 9.9)
        path.lineTo(10.1, 7.9)
        path.lineTo(8.5, 3.9)
        path.closeSubpath()
    elif name == 'video':
        path.addRoundedRect(QRectF(2.8, 6.3, 12.6, 11.4), 2.6, 2.6)
        path.moveTo(15.4, 10.6)
        path.lineTo(21.2, 7.4)
        path.lineTo(21.2, 16.6)
        path.lineTo(15.4, 13.4)
    elif name == 'mic':
        path.addRoundedRect(QRectF(9, 2.8, 6, 11.4), 3, 3)
        path.moveTo(5.6, 11)
        path.cubicTo(5.6, 19.4, 18.4, 19.4, 18.4, 11)
        path.moveTo(12, 17.3)
        path.lineTo(12, 21)
        path.moveTo(8.6, 21)
        path.lineTo(15.4, 21)
    elif name == 'lock':
        path.addRoundedRect(QRectF(5, 10.5, 14, 10), 2.2, 2.2)
        path.moveTo(8.2, 10.5)
        path.lineTo(8.2, 7.6)
        path.arcTo(QRectF(8.2, 3.6, 7.6, 7.6), 180, -180)
        path.lineTo(15.8, 10.5)
        path.moveTo(12, 14.4)
        path.lineTo(12, 16.8)
    elif name == 'ladder':
        path.moveTo(7, 3)
        path.lineTo(7, 21)
        path.moveTo(17, 3)
        path.lineTo(17, 21)
        for y in (7.5, 12, 16.5):
            path.moveTo(7, y)
            path.lineTo(17, y)
    elif name == 'board':
        path.addRoundedRect(QRectF(5, 4.5, 14, 16.5), 2.2, 2.2)
        path.addRoundedRect(QRectF(9, 2.8, 6, 3.4), 1.2, 1.2)
        path.moveTo(8.6, 11)
        path.lineTo(15.4, 11)
        path.moveTo(8.6, 15)
        path.lineTo(13.4, 15)
    elif name == 'at':
        path.addEllipse(QRectF(8.6, 8.6, 6.8, 6.8))
        path.moveTo(15.4, 9)
        path.lineTo(15.4, 13.6)
        path.cubicTo(15.4, 16.6, 20.2, 16.6, 20.2, 12)
        path.cubicTo(20.2, 7.2, 16.4, 3.8, 12, 3.8)
        path.cubicTo(7.2, 3.8, 3.8, 7.4, 3.8, 12)
        path.cubicTo(3.8, 16.8, 7.4, 20.2, 12, 20.2)
        path.cubicTo(13.8, 20.2, 15.2, 19.8, 16.4, 19.0)
    painter.drawPath(path)
    painter.restore()


def extra_icon_pixmap(name, color, size, bg=None):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pad = 0
    if bg:
        p.setPen(Qt.NoPen)
        p.setBrush(to_qcolor(bg))
        p.drawEllipse(QRectF(0, 0, size, size))
        pad = size * 0.24
    paint_extra_icon(p, name, QRectF(pad, pad, size - 2 * pad, size - 2 * pad), color)
    p.end()
    return pm


class ExtraIconButton(IconButton):
    """chat_room 전용 아이콘(통화/사다리/게시판)을 쓰는 납작한 버튼"""

    def refresh_icon(self, color=None):
        size = self._icon_size
        self.setIcon(QIcon(extra_icon_pixmap(self.icon_name, color or theme.c('text_sub'), size)))
        self.setIconSize(QSize(size, size))


def _luminance(color):
    q = to_qcolor(color)
    return (0.299 * q.red() + 0.587 * q.green() + 0.114 * q.blue()) / 255.0


def mine_colors(style):
    """말풍선 꾸미기 스타일 → 내 말풍선 색 세트. 보조색/링크색은 글자색(fg)에서 파생해 항상 읽히게 한다"""
    fg = style.get('fg') or theme.c('bubble_mine_text')
    q = to_qcolor(fg)
    rgb = f'{q.red()},{q.green()},{q.blue()}'
    dark_text = _luminance(fg) < 0.5
    return {'bg': style.get('bg') or theme.c('bubble_mine'), 'fg': fg,
            'sub': f'rgba({rgb},0.62)', 'line': f'rgba({rgb},0.22)',
            'link': '#1F4FB8' if dark_text else '#C9D6FF',
            'radius': int(style.get('radius') or RADIUS)}


def retention_text(att):
    """첨부 보관 기간 문구 ('' = 표시 안 함)"""
    att = att or {}
    if att.get('expired'):
        return EXPIRED_TEXT
    dt = parse_dt(att.get('expires_at'))
    return f'~{dt:%Y.%m.%d}까지 보관' if dt else ''


def chevron_pixmap(color, size, up=False):
    """펼치기(∨)/접기(∧) 꺾쇠 — 글자 기호(▾▴) 대신 직접 그린 선"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(to_qcolor(color), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = size / 24.0
    y0, y1 = (15 * s, 9 * s) if up else (9 * s, 15 * s)
    path = QPainterPath()
    path.moveTo(6 * s, y0)
    path.lineTo(12 * s, y1)
    path.lineTo(18 * s, y0)
    p.drawPath(path)
    p.end()
    return pm


def pencil_pixmap(color, size):
    """'메시지 수정' 연필 아이콘 (widgets 아이콘 세트에 없어서 직접 그림)"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    pen = QPen(to_qcolor(color), 1.9)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    body = QPainterPath()
    body.moveTo(15.5, 4.5)
    body.lineTo(19.5, 8.5)
    body.lineTo(8.5, 19.5)
    body.lineTo(4, 20)
    body.lineTo(4.5, 15.5)
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(13, 7), QPointF(17, 11))
    p.end()
    return pm


# ---------------------------------------------------------------- 기본 위젯
class Clickable(QFrame):
    """왼쪽 클릭 시 clicked 시그널"""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """키보드 포커스를 받는 Clickable(공지 배너·인용·링크 카드)은 Enter/Space로 누른다"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class Bubble(Clickable):
    """QPainter로 그리는 둥근 말풍선(+ 연속 메시지 첫 말풍선의 꼬리)"""

    def __init__(self, mine, parent=None, fill=None, radius=RADIUS):
        super().__init__(parent)
        self.mine = mine
        self.tail = False
        self.fill = fill            # 말풍선 꾸미기 배경색 (None = 테마 기본)
        self.radius = radius
        self.outline = None         # 키워드 알림 강조 테두리 색
        self.setCursor(Qt.ArrowCursor)
        if mine:
            self.setContentsMargins(PAD_H, PAD_V, PAD_H + TAIL, PAD_V)
        else:
            self.setContentsMargins(PAD_H + TAIL, PAD_V, PAD_H, PAD_V)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    @staticmethod
    def hpad():
        return 2 * PAD_H + TAIL

    def set_tail(self, on):
        if self.tail != on:
            self.tail = on
            self.update()

    def paintEvent(self, event):
        # KCS Ink: 카카오 꼬리 대신, 묶음 첫 말풍선은 말한 사람 쪽 위 모서리만 작게 꺾는다 (말하는 방향)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        body = r.adjusted(0, 0, -TAIL, 0) if self.mine else r.adjusted(TAIL, 0, 0, 0)
        body = body.adjusted(0, 0, 0, -1)                     # 아래 1px 은 그림자 자리
        big = min(self.radius, body.height() / 2, body.width() / 2)
        small = min(4.0, big) if self.tail else big
        path = corner_path(body, small if not self.mine else big, small if self.mine else big, big, big)
        fill = to_qcolor(self.fill) if self.fill else theme.qc('bubble_mine' if self.mine else 'bubble_other')
        if not self.mine and not self.fill:
            p.fillPath(path.translated(0, 1), theme.qc('bubble_shadow'))   # 받은 말풍선만 종이 위로 살짝 뜬다
        p.fillPath(path, fill)
        if self.outline:
            line = to_qcolor(self.outline)
            line.setAlphaF(0.75)                       # 칩이 주 신호, 테두리는 보조 (impeccable quieter)
            p.strokePath(path, QPen(line, 1.0))


def corner_path(rect, tl, tr, br, bl):
    """모서리마다 반지름이 다른 둥근 사각형 경로"""
    path = QPainterPath()
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    path.arcTo(QRectF(x + w - 2 * tr, y, 2 * tr, 2 * tr), 90, -90)
    path.lineTo(x + w, y + h - br)
    path.arcTo(QRectF(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br), 0, -90)
    path.lineTo(x + bl, y + h)
    path.arcTo(QRectF(x, y + h - 2 * bl, 2 * bl, 2 * bl), 270, -90)
    path.lineTo(x, y + tl)
    path.arcTo(QRectF(x, y, 2 * tl, 2 * tl), 180, -90)
    path.closeSubpath()
    return path


class VoteBar(QWidget):
    """투표 항목 비율 막대"""

    def __init__(self, ratio, highlight, parent=None):
        super().__init__(parent)
        self.ratio = max(0.0, min(1.0, ratio))
        self.highlight = highlight
        self.setFixedHeight(5)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        p.setBrush(theme.qc('border'))
        p.drawRoundedRect(r, 2.5, 2.5)
        if self.ratio > 0:
            p.setBrush(theme.qc('yellow') if self.highlight else theme.qc('text_hint'))
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(5.0, r.width() * self.ratio), r.height()), 2.5, 2.5)


class ImageThumb(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('imageThumb')
        self.setAlignment(Qt.AlignCenter)
        self.setText('사진 불러오는 중…')
        self.setFixedSize(180, 135)
        self.setCursor(Qt.PointingHandCursor)
        self._data = None
        self._limit = None      # 지금 썸네일을 만든 기준 폭 (같으면 다시 풀지 않는다)

    def set_image(self, data, max_w):
        self._data = data
        self._limit = min(220, max(80, max_w))
        pm = decode_pixmap(data)
        if pm is None:
            self.setText('사진을 표시할 수 없습니다')
            return
        limit = self._limit
        scaled = pm.scaled(limit * 2, limit * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(2.0)
        w, h = int(scaled.width() / 2), int(scaled.height() / 2)
        out = QPixmap(scaled.size())
        out.setDevicePixelRatio(2.0)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, scaled)
        p.end()
        self.setText('')
        self.setStyleSheet('background: transparent;')
        self.setPixmap(out)
        self.setFixedSize(max(1, w), max(1, h))

    def relayout(self, max_w):
        if self._data is not None and min(220, max(80, max_w)) != self._limit:   # 창 폭만 바뀌면 그대로
            self.set_image(self._data, max_w)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------- 카드
class PollCard(QFrame):
    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.win, self.state, self.msg = win, win.state, msg
        poll = msg.get('poll') or {}
        self.poll = poll
        me = self.state.me
        closed = bool(poll.get('closed'))
        multiple = bool(poll.get('multiple'))
        options = poll.get('options') or []
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(5)
        ic = QLabel(self)
        ic.setPixmap(icon_pixmap('poll', theme.c('text_sub'), 16))
        head.addWidget(ic)
        tags = '투표' + (' · 복수 선택' if multiple else '') + (' · 마감됨' if closed else '')
        head.addWidget(_label(tags, 'cardSub', self))
        head.addStretch(1)
        v.addLayout(head)
        self.question = _label(poll.get('question') or msg.get('content', ''), 'cardTitle', self, wrap=True)
        v.addWidget(self.question)
        total = len({u for o in options for u in o.get('voters', [])})
        top = max([len(o.get('voters', [])) for o in options] or [0])
        self.group = QButtonGroup(self)
        self.group.setExclusive(not multiple)
        self.option_buttons = []
        self._option_texts = []
        for i, opt in enumerate(options):
            voters = opt.get('voters', [])
            box = QWidget(self)
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(4)
            line = QHBoxLayout()
            line.setSpacing(6)
            btn = (QCheckBox if multiple else QRadioButton)(box)
            btn.setObjectName('pollOpt')
            btn.setChecked(me in voters)
            btn.setEnabled(not closed)
            self.group.addButton(btn, i)
            self.option_buttons.append(btn)
            self._option_texts.append(opt.get('text', ''))
            line.addWidget(btn, 1)
            cnt = _label(f'{len(voters)}', 'pollCountTop' if voters and len(voters) == top else 'cardSub', box)
            line.addWidget(cnt)
            bv.addLayout(line)
            bv.addWidget(VoteBar(len(voters) / total if total else 0.0, me in voters, box))
            names = ', '.join(self.state.display_name(u) for u in voters)
            box.setToolTip(names or '아직 투표한 사람이 없습니다.')
            v.addWidget(box)
        v.addWidget(_label(f'{total}명 참여', 'cardSub', self))
        self.vote_button = None
        self.close_button = None
        if closed:
            v.addWidget(_label('투표가 마감되었습니다.', 'cardHint', self))
        else:
            row = QHBoxLayout()
            row.setSpacing(6)
            self.vote_button = QPushButton('투표하기', self)
            self.vote_button.setObjectName('cardPrimary')
            self.vote_button.setCursor(Qt.PointingHandCursor)
            self.vote_button.clicked.connect(self.vote)
            row.addWidget(self.vote_button, 1)
            if msg.get('sender') == me:
                self.close_button = QPushButton('마감', self)
                self.close_button.setObjectName('cardBtn')
                self.close_button.setCursor(Qt.PointingHandCursor)
                self.close_button.clicked.connect(self.close_poll)
                row.addWidget(self.close_button)
            v.addLayout(row)

    def selected_indexes(self):
        return [i for i, b in enumerate(self.option_buttons) if b.isChecked()]

    def vote(self):
        self.state.vote_poll(self.poll.get('poll_id'), self.selected_indexes())

    def close_poll(self):
        if self.win.confirm('투표 마감', '투표를 마감할까요? 마감 후에는 투표할 수 없습니다.'):
            self.state.close_poll(self.poll.get('poll_id'))

    def relayout(self, max_w):
        w = min(CARD_W, max_w)
        self.setFixedWidth(w)
        inner = w - 28
        fit_wrap(self.question, inner)
        for btn, text in zip(self.option_buttons, self._option_texts):
            elide(btn, text, inner - 60)
            btn.setToolTip(text)


class EventCard(QFrame):
    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.win, self.state, self.msg = win, win.state, msg
        self.event = self.state.events.get(msg.get('event_id'))
        ev = self.event
        h = QHBoxLayout(self)
        h.setContentsMargins(1, 1, 0, 1)
        h.setSpacing(0)
        # 일정 색은 옆 줄무늬 대신 '일정' 글자 앞 점 하나로 (KCS Ink: 카드 옆 색 막대 금지)
        color = (ev or {}).get('color') or theme.c('yellow')
        body = QVBoxLayout()
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(6)
        h.addLayout(body, 1)
        head = QHBoxLayout()
        head.setSpacing(5)
        ic = QLabel(self)
        ic.setPixmap(icon_pixmap('calendar', color, 16))
        head.addWidget(ic)
        head.addWidget(_label('일정', 'cardSub', self))
        head.addStretch(1)
        body.addLayout(head)
        self._wraps = []
        self.rsvp_buttons = {}
        if ev is None:
            lab = _label(msg.get('content') or '일정', 'cardTitle', self, wrap=True)
            hint = _label('삭제되었거나 볼 수 없는 일정입니다.', 'cardHint', self, wrap=True)
            body.addWidget(lab)
            body.addWidget(hint)
            self._wraps += [lab, hint]
            return
        title = _label(ev.get('title', ''), 'cardTitle', self, wrap=True)
        when = _label(event_when(ev), 'cardText', self, wrap=True)
        body.addWidget(title)
        body.addWidget(when)
        self._wraps += [title, when]
        if ev.get('location'):
            # 장소: 이모지 📍 대신 다른 카드 머리와 같은 선 아이콘 (보조 글자색)
            loc_row = QHBoxLayout()
            loc_row.setSpacing(4)
            loc_ic = QLabel(self)
            loc_ic.setPixmap(icon_pixmap('location', theme.c('text_sub'), 14))
            loc_row.addWidget(loc_ic, 0, Qt.AlignTop)
            loc = _label(ev['location'], 'cardSub', self, wrap=True)
            loc_row.addWidget(loc, 1)
            body.addLayout(loc_row)
            self._wraps.append(loc)
            self._indented = {loc}     # 아이콘(14) + 간격(4) 만큼 좁게 줄바꿈
        attendees = ev.get('attendees') or {}
        counts = {k: 0 for k in ('yes', 'no', 'maybe', 'pending')}
        for resp in attendees.values():
            counts[resp if resp in counts else 'pending'] += 1
        summary = f"참석 {counts['yes']} · 불참 {counts['no']} · 미정 {counts['maybe']}"
        if counts['pending']:
            summary += f" · 미응답 {counts['pending']}"
        cnt = _label(summary, 'cardSub', self, wrap=True)
        names = []
        for key, text in RSVP_LABELS + [('pending', '미응답')]:
            who = [self.state.display_name(u) for u, r in attendees.items() if r == key]
            if who:
                names.append(f"{text}: {', '.join(who)}")
        cnt.setToolTip('\n'.join(names))
        body.addWidget(cnt)
        self._wraps.append(cnt)
        mine = self.state.my_rsvp(ev)
        if mine is not None:
            row = QHBoxLayout()
            row.setSpacing(5)
            for key, text in RSVP_LABELS:
                b = QPushButton(text, self)
                b.setObjectName('cardBtnOn' if mine == key else 'cardBtn')
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=key: self.state.rsvp(ev['event_id'], k))
                self.rsvp_buttons[key] = b
                row.addWidget(b, 1)
            body.addLayout(row)

    def relayout(self, max_w):
        w = min(CARD_W, max_w)
        self.setFixedWidth(w)
        indented = getattr(self, '_indented', ())
        for lab in self._wraps:
            fit_wrap(lab, w - 33 - (18 if lab in indented else 0))


class LinkPreviewCard(Clickable):
    """URL 미리보기 카드: 사이트명 / 제목(최대 2줄) / 설명(최대 2줄). 클릭하면 브라우저로 연다"""
    MIN_W = 200

    def __init__(self, preview, mine, parent=None):
        super().__init__(parent)
        self.preview = preview or {}
        self.url = self.preview.get('url') or ''
        self.setObjectName('linkCardMine' if mine else 'linkCard')
        self.setToolTip(self.url)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(3)
        site = self.preview.get('site') or QUrl(self.url).host() or self.url
        self._texts = []
        self.site_label = _label(site, 'linkSiteMine' if mine else 'linkSite', self)
        v.addWidget(self.site_label)
        self._texts.append((self.site_label, site, 1))
        self.title_label = None
        self.desc_label = None
        title = self.preview.get('title') or ''
        desc = self.preview.get('description') or ''
        if title:
            self.title_label = _label(title, 'linkTitleMine' if mine else 'linkTitle', self)
            v.addWidget(self.title_label)
            self._texts.append((self.title_label, title, 2))
        if desc:
            self.desc_label = _label(desc, 'linkDescMine' if mine else 'linkDesc', self)
            v.addWidget(self.desc_label)
            self._texts.append((self.desc_label, desc, 2))
        self.clicked.connect(self.open)

    def open(self):
        if self.url:
            osenv.open_url(self.url)

    def relayout(self, width):
        width = max(80, int(width))
        self.setFixedWidth(width)
        inner = width - 20
        for lab, text, lines in self._texts:
            lab.ensurePolished()
            lab.setText(elide_lines(lab.font(), text, inner, lines))
            lab.setFixedWidth(inner)
            lab.setFixedHeight(lab.sizeHint().height())
        self.setFixedHeight(self.sizeHint().height())


class CallCard(QFrame):
    """통화 메시지 카드: 모드(보이스톡/페이스톡/보이스룸) + 진행 중이면 '참여하기', 끝났으면 '종료된 통화'"""

    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.win, self.state, self.msg = win, win.state, msg
        self.mode = msg.get('content') if msg.get('content') in CALL_MODES else 'voice'
        self.call_id = msg.get('call_id')
        call = self.state.calls.get(self.call_id) if self.call_id is not None else None
        self.active = call is not None
        joined = bool(call) and self.state.me in (call.get('participants') or {})
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(9)
        head = QHBoxLayout()
        head.setSpacing(10)
        ic = QLabel(self)
        ic.setFixedSize(34, 34)
        ic.setPixmap(extra_icon_pixmap(CALL_ICONS[self.mode], '#FFFFFF', 34,
                                       bg=CALL_GREEN if self.active else '#9AA3AB'))
        head.addWidget(ic, 0, Qt.AlignVCenter)
        tv = QVBoxLayout()
        tv.setSpacing(1)
        self.title = _label(CALL_MODES[self.mode], 'cardTitle', self)
        who = self.state.member_name(win.room_id, msg.get('sender'))
        self.sub = _label(f'{who}님이 시작 · {fmt_time(msg.get("timestamp"))}', 'cardSub', self)
        tv.addWidget(self.title)
        tv.addWidget(self.sub)
        head.addLayout(tv, 1)
        v.addLayout(head)
        self.join_button = None
        self.ended_label = None
        if self.active:
            self.join_button = QPushButton('돌아가기' if joined else '참여하기', self)
            self.join_button.setObjectName('cardPrimary')
            self.join_button.setCursor(Qt.PointingHandCursor)
            self.join_button.clicked.connect(
                (lambda: self.win.open_call_window(self.call_id)) if joined else (lambda: self.win.join_call(self.call_id)))
            v.addWidget(self.join_button)
        else:
            self.ended_label = _label('종료된 통화', 'cardHint', self)
            v.addWidget(self.ended_label)

    def relayout(self, max_w):
        w = min(220, max_w)
        self.setFixedWidth(w)
        elide(self.sub, self.sub.text(), w - 28 - 44)


class ContactCard(QFrame):
    """연락처 메시지 카드 (카카오톡 '+ → 연락처'): 프로필 · 이름 · 소속 + [1:1 채팅] [프로필]"""

    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.win, self.state, self.msg = win, win.state, msg
        self.username = msg.get('content') or ''
        user = self.state.user(self.username) or {}
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(9)
        self.caption = _label('연락처', 'cardHint', self)
        v.addWidget(self.caption)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.avatar = QLabel(self)
        self.avatar.setFixedSize(40, 40)
        self.avatar.setPixmap(user_avatar_pixmap(self.state, self.username, 40))
        head.addWidget(self.avatar, 0, Qt.AlignVCenter)
        tv = QVBoxLayout()
        tv.setSpacing(1)
        self.title = _label(self.state.display_name(self.username), 'cardTitle', self)
        sub = self.state.org_text(self.username) or user.get('status_message') or 'KCS Talk 사용자'
        self.sub = _label(sub, 'cardSub', self)
        self.sub_text = sub
        tv.addWidget(self.title)
        tv.addWidget(self.sub)
        head.addLayout(tv, 1)
        v.addLayout(head)
        btns = QBoxLayout(QBoxLayout.LeftToRight)
        btns.setSpacing(6)
        self.button_box = btns
        self.chat_button = QPushButton('1:1 채팅', self)
        self.chat_button.setObjectName('cardPrimary')
        self.profile_button = QPushButton('프로필 보기', self)
        self.profile_button.setObjectName('cardBtn')
        for b in (self.chat_button, self.profile_button):
            b.setCursor(Qt.PointingHandCursor)
            btns.addWidget(b, 1)
        self.chat_button.clicked.connect(lambda: self.state.open_direct(self.username))
        self.profile_button.clicked.connect(lambda: self.win.open_profile(self.username))
        if self.username == self.state.me:
            self.chat_button.setText('나와의 채팅')
        v.addLayout(btns)

    def relayout(self, max_w):
        w = min(230, max_w)
        self.setFixedWidth(w)
        elide(self.title, self.state.display_name(self.username), w - 28 - 50)
        elide(self.sub, self.sub_text, w - 28 - 50)
        # 좁은 창·큰 글자: 두 버튼이 한 줄에 안 들어가면 세로로 쌓는다 (글자가 잘리지 않게)
        need = self.chat_button.sizeHint().width() + self.profile_button.sizeHint().width() + 6
        self.button_box.setDirection(QBoxLayout.LeftToRight if need <= w - 28 else QBoxLayout.TopToBottom)


class Waveform(QWidget):
    """음성메시지 막대 파형 + 재생 진행률"""

    def __init__(self, bars, color, played_color, parent=None):
        super().__init__(parent)
        self.bars, self.color, self.played_color = bars, QColor(color), QColor(played_color)
        self.progress = 0.0
        self.setFixedHeight(26)

    def set_progress(self, value):
        self.progress = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        gap = 2.0
        # 폭에 들어가는 만큼만 막대를 그린다 (막대 최소 2px)
        fit = max(1, int((self.width() + gap) // (2.0 + gap)))
        bars = self.bars if len(self.bars) <= fit else [
            self.bars[int(i * len(self.bars) / fit)] for i in range(fit)]
        n = len(bars)
        if not n:
            return
        bw = max(2.0, (self.width() - gap * (n - 1)) / n)
        h = self.height()
        for i, b in enumerate(bars):
            bh = max(3.0, h * b)
            p.setBrush(self.played_color if (i + 0.5) / n <= self.progress else self.color)
            p.drawRoundedRect(QRectF(i * (bw + gap), (h - bh) / 2, bw, bh), bw / 2, bw / 2)


class RoundGlyphButton(QAbstractButton):
    """원 + 직접 그린 기호 버튼 공통: 눌림(15% 어둡게) · 비활성 · 2px 포커스 링을 한 곳에서 그린다.
    하위 클래스는 paint_glyph(p, center) 만 그린다 (글자 기호를 아이콘으로 쓰지 않음)"""
    MARGIN = 1

    def __init__(self, fill, glyph, size, parent=None):
        super().__init__(parent)
        self.fill, self.glyph = QColor(fill), QColor(glyph)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)

    def circle(self):
        m = self.MARGIN
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.circle()
        fill = QColor(self.fill)
        if not self.isEnabled():
            fill = theme.qc('bg_alt')
        elif self.isDown():
            fill = fill.darker(115)
        p.setPen(QPen(theme.qc('yellow_dark'), 2) if self.hasFocus() else Qt.NoPen)
        p.setBrush(fill)
        p.drawEllipse(r)
        p.setPen(Qt.NoPen)
        self.paint_extra(p, r)
        p.setBrush(self.glyph if self.isEnabled() else theme.qc('text_hint'))
        self.paint_glyph(p, r.center())

    def paint_extra(self, p, r):
        """원과 기호 사이에 그릴 것 (기본 없음)"""

    def paint_glyph(self, p, c):
        raise NotImplementedError


class FailButton(RoundGlyphButton):
    """보내지 못한 메시지 표시: 빨간 원 + 느낌표. 누르면 재전송/삭제 메뉴"""
    MARGIN = 3   # 원 18px + 알림 고리 여백

    def __init__(self, parent=None):
        super().__init__(theme.c('badge'), '#FFFFFF', 24, parent)
        self.setToolTip('전송 실패 · 눌러서 재전송 또는 삭제')
        self.setAccessibleName('보내지 못한 메시지: 재전송 또는 삭제')
        self.ring = 0.0          # 알림 고리 진행 (0 = 없음)
        self._anim = None

    def announce(self):
        """막 실패했을 때 한 번: 빨간 고리가 퍼지며 사라진다 (360ms, 감속). 동작 줄이기면 생략"""
        if theme.reduce_motion():
            return False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(360)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        # finished 는 쓰지 않는다: 버튼이 지워질 때 Qt 가 소멸자 안에서 애니메이션을 멈추며 finished 를 보내
        # 반쯤 지워진 위젯을 update() 하게 된다 (세그폴트). 끝 값(1.0)에서 고리를 지우는 것으로 충분하다.
        self._anim.valueChanged.connect(self._set_ring)
        self._anim.start()
        return True

    def _set_ring(self, value):
        value = float(value)
        self.ring = 0.0 if value >= 1.0 else value
        self.update()

    def paint_extra(self, p, r):
        if 0.0 < self.ring < 1.0:    # 알림 고리: 원 바깥 여백 안으로 퍼지며 흐려짐
            ring = theme.qc('badge')
            ring.setAlphaF(0.6 * (1.0 - self.ring))
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.NoBrush)
            grow = 2.5 * self.ring
            p.drawEllipse(r.adjusted(-grow, -grow, grow, grow))
            p.setPen(Qt.NoPen)

    def paint_glyph(self, p, c):
        p.drawRoundedRect(QRectF(c.x() - 1.2, c.y() - 5.5, 2.4, 7), 1.2, 1.2)
        p.drawEllipse(QPointF(c.x(), c.y() + 4.2), 1.3, 1.3)


class PlayButton(RoundGlyphButton):
    """음성 재생/정지 버튼: 삼각형 / 정지 막대"""

    def __init__(self, fill, glyph, parent=None):
        super().__init__(fill, glyph, 32, parent)
        self.playing = False

    def set_playing(self, playing):
        self.playing = bool(playing)
        self.setAccessibleName('음성메시지 정지' if playing else '음성메시지 재생')
        self.setToolTip('정지' if playing else '재생')
        self.update()

    def paint_glyph(self, p, c):
        if self.playing:
            for dx in (-4.2, 1.4):
                p.drawRoundedRect(QRectF(c.x() + dx, c.y() - 5.5, 2.8, 11), 1, 1)
            return
        path = QPainterPath()
        path.moveTo(c.x() - 3.2, c.y() - 6)
        path.lineTo(c.x() + 6, c.y())
        path.lineTo(c.x() - 3.2, c.y() + 6)
        path.closeSubpath()
        p.drawPath(path)


class VoiceCard(QFrame):
    """음성메시지 말풍선: [▶] 파형 0:07  (누르면 받아서 재생, 다시 누르면 정지)"""

    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        from client.kakao.voice import VoicePlayer, waveform_bars
        from client.kakao.state import fmt_duration
        mine = msg.get('sender') == win.state.me
        self.setObjectName('voiceMine' if mine else 'voice')
        self.win, self.state, self.msg = win, win.state, msg
        self.att = msg.get('attachment') or {}
        self.aid = self.att.get('attachment_id')
        self.player = VoicePlayer.instance()
        self.real_wave = False       # 실제 녹음 모양 파형으로 바꿨는지
        fg = win._mine['fg'] if mine else theme.c('text')
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 8, 14, 8)
        h.setSpacing(10)
        if mine:
            self.play_button = PlayButton(fg, win._mine['bg'], self)
        else:
            self.play_button = PlayButton(theme.c('yellow'), theme.c('on_accent'), self)
        h.addWidget(self.play_button, 0, Qt.AlignVCenter)
        # 재생된 막대 = 진한 색, 남은 막대 = 글자색 50% (어느 테마에서도 3:1 이상, WCAG 1.4.11)
        played = fg if (mine or theme.is_dark()) else theme.c('yellow')
        base = to_qcolor(fg)
        base.setAlpha(128)
        self.wave = Waveform(waveform_bars(msg.get('msg_id') or 0), base, played, self)
        self.wave.setMinimumWidth(36)   # 좁은 말풍선에서는 파형이 줄어든다 (길이 글자와 겹치지 않게)
        h.addWidget(self.wave, 1, Qt.AlignVCenter)
        self.duration = _label(fmt_duration(msg.get('content')), 'voiceTimeMine' if mine else 'voiceTime', self)
        h.addWidget(self.duration, 0, Qt.AlignVCenter)
        self.expired = bool(self.att.get('expired'))
        if self.expired:
            self.play_button.setEnabled(False)
            self.setToolTip(EXPIRED_TEXT)
        self.play_button.clicked.connect(self.toggle)
        self.play_button.set_playing(False)
        self._listening = False      # 재생 중일 때만 앱 전체 플레이어(초당 25회 진행 알림)를 듣는다

    def key(self):
        return ('voice', self.msg.get('msg_id'))

    def toggle(self):
        if self.player.is_playing(self.key()):
            self.player.stop()
            return
        cached = self.state.attachments.get(self.aid)
        if cached:
            self.play(cached[0])
        elif self.aid is not None:
            self.win._pending_play[self.aid] = self
            self.state.request_attachment(self.aid)

    def play(self, data):
        # delight: 받은 녹음의 실제 소리 모양으로 파형을 바꾼다 (처음엔 msg_id 로 만든 임시 모양)
        from client.kakao.voice import wav_info, waveform_bars
        pcm, _secs = wav_info(data)
        if pcm and not self.real_wave:
            self.wave.bars = waveform_bars(self.msg.get('msg_id') or 0, len(self.wave.bars), pcm)
            self.real_wave = True
            self.wave.update()
        if not self._listening:
            self.player.progress.connect(self._on_progress)
            self.player.stopped.connect(self._on_stopped)
            self._listening = True
        if self.player.play(self.key(), data):
            self.play_button.set_playing(True)
        else:
            self.win.toast('음성메시지 파일을 읽을 수 없어 재생하지 못했습니다. 보낸 사람에게 다시 보내 달라고 요청하세요.', 3500)

    def _on_progress(self, key, value):
        if key == self.key():
            self.wave.set_progress(value)

    def _on_stopped(self, key):
        if key == self.key():
            self.play_button.set_playing(False)
            self.wave.set_progress(0)
            if self._listening:
                self.player.progress.disconnect(self._on_progress)
                self.player.stopped.disconnect(self._on_stopped)
                self._listening = False

    def relayout(self, max_w):
        self.setFixedWidth(min(230, max_w))


def ladder_path_columns(start, rungs, rows=LADDER_ROWS):
    """사다리 타기: 시작 세로줄 → [(row, col_before, col_after)...], 도착 세로줄"""
    rung_set = {(int(r), int(c)) for r, c in rungs or []}
    col = start
    steps = []
    for row in range(rows):
        before = col
        if (row, col) in rung_set:
            col += 1
        elif (row, col - 1) in rung_set:
            col -= 1
        steps.append((row, before, col))
    return steps, col


class LadderCanvas(QWidget):
    """사다리 그림 (rows=10). progress 0→1 동안 각 참가자의 경로를 색으로 그린다"""

    def __init__(self, ladder, parent=None):
        super().__init__(parent)
        self.participants = list(ladder.get('participants') or [])
        self.results = list(ladder.get('results') or [])
        self.rungs = [tuple(r) for r in ladder.get('rungs') or []]
        self.progress = 0.0
        self.setFixedHeight(196)

    def _geometry(self):
        n = max(1, len(self.participants))
        w, h = self.width(), self.height()
        colw = w / n
        return n, colw, 24.0, h - 24.0

    def column_x(self, col):
        _, colw, _, _ = self._geometry()
        return colw * (col + 0.5)

    def path_points(self, i):
        n, colw, y0, y1 = self._geometry()
        step = (y1 - y0) / LADDER_ROWS
        pts = [QPointF(self.column_x(i), y0)]
        steps, _ = ladder_path_columns(i, self.rungs)
        for row, before, after in steps:
            y = y0 + (row + 0.5) * step
            pts.append(QPointF(self.column_x(before), y))
            if after != before:
                pts.append(QPointF(self.column_x(after), y))
        pts.append(QPointF(self.column_x(steps[-1][2] if steps else i), y1))
        return pts

    @staticmethod
    def _partial(pts, frac):
        lens = [math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(pts, pts[1:])]
        remain = sum(lens) * max(0.0, min(1.0, frac))
        path = QPainterPath(pts[0])
        for (a, b), ln in zip(zip(pts, pts[1:]), lens):
            if remain <= 0:
                break
            if ln <= remain:
                path.lineTo(b)
                remain -= ln
            else:
                t = remain / ln
                path.lineTo(QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t))
                remain = 0
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n, colw, y0, y1 = self._geometry()
        step = (y1 - y0) / LADDER_ROWS
        p.setPen(QPen(theme.qc('text_hint'), 2, Qt.SolidLine, Qt.RoundCap))
        for c in range(n):
            x = self.column_x(c)
            p.drawLine(QPointF(x, y0), QPointF(x, y1))
        for row, c in self.rungs:
            y = y0 + (row + 0.5) * step
            p.drawLine(QPointF(self.column_x(c), y), QPointF(self.column_x(c + 1), y))
        if self.progress > 0:
            for i in range(n):
                pen = QPen(QColor(LADDER_COLORS[i % len(LADDER_COLORS)]), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                p.setPen(pen)
                p.drawPath(self._partial(self.path_points(i), self.progress))
        fm_font = theme.font('caption')
        fm = QFontMetrics(fm_font)
        for row_y, items, bold in ((0, self.participants, False), (y1 + 4, self.results, True)):
            f = theme.font('caption', bold=bold)
            p.setFont(f)
            for c, text in enumerate(items):
                color = QColor(LADDER_COLORS[c % len(LADDER_COLORS)]) if not bold else theme.qc('bubble_text')
                p.setPen(color)
                rect = QRectF(colw * c + 1, row_y, colw - 2, 20)
                p.drawText(rect, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(colw - 4)))


class LadderCard(QFrame):
    """사다리 게임 카드: 사다리 그림 + '결과 보기'(경로 애니메이션 후 '참가자 → 결과' 목록)"""

    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.win, self.state, self.msg = win, win.state, msg
        self.ladder = msg.get('ladder') or {}
        parts = list(self.ladder.get('participants') or [])
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 11, 12, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(5)
        ic = QLabel(self)
        ic.setPixmap(extra_icon_pixmap('ladder', theme.c('text_sub'), 16))
        head.addWidget(ic)
        head.addWidget(_label(f'사다리 게임 · {len(parts)}명', 'cardSub', self))
        head.addStretch(1)
        v.addLayout(head)
        self.canvas = LadderCanvas(self.ladder, self)
        v.addWidget(self.canvas)
        self.result_label = _label(self.result_text(), 'cardText', self, wrap=True)
        self.result_label.hide()
        v.addWidget(self.result_label)
        self.reveal_button = QPushButton('결과 보기', self)
        self.reveal_button.setObjectName('cardPrimary')
        self.reveal_button.setCursor(Qt.PointingHandCursor)
        self.reveal_button.clicked.connect(self.reveal)
        v.addWidget(self.reveal_button)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._step)
        if msg.get('msg_id') in win._revealed_ladders:
            self._finish()

    def mapping(self):
        """참가자 → 결과 (서버 mapping 우선, 없으면 사다리를 직접 타서 계산)"""
        parts = list(self.ladder.get('participants') or [])
        results = list(self.ladder.get('results') or [])
        mapping = dict(self.ladder.get('mapping') or {})
        out = []
        for i, name in enumerate(parts):
            if name in mapping:
                out.append((name, mapping[name]))
            else:
                _, end = ladder_path_columns(i, self.ladder.get('rungs'))
                out.append((name, results[end] if 0 <= end < len(results) else '?'))
        return out

    def result_text(self):
        return '\n'.join(f'{name} → {res}' for name, res in self.mapping())

    def reveal(self):
        if self._timer.isActive() or self.result_label.isVisible():
            return
        self.reveal_button.setEnabled(False)
        self.reveal_button.setText('사다리 타는 중…')
        self.canvas.progress = 0.0
        self._timer.start()

    def _step(self):
        self.canvas.progress = min(1.0, self.canvas.progress + 0.035)
        self.canvas.update()
        if self.canvas.progress >= 1.0:
            self._timer.stop()
            self._finish()

    def _finish(self):
        self.canvas.progress = 1.0
        self.canvas.update()
        self.reveal_button.hide()
        self.result_label.show()
        self.win._revealed_ladders.add(self.msg.get('msg_id'))
        self.updateGeometry()

    def relayout(self, max_w):
        w = min(290, max_w)
        self.setFixedWidth(w)
        fit_wrap(self.result_label, w - 24)


class BoardCard(Clickable):
    """톡게시판 글 카드 (📌 제목). 누르면 게시판에서 그 글을 연다"""

    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.win, self.state, self.msg = win, win.state, msg
        self.post_id = msg.get('post_id')
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 11, 14, 12)
        v.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(5)
        ic = QLabel(self)
        ic.setPixmap(extra_icon_pixmap('board', theme.c('text_sub'), 16))
        head.addWidget(ic)
        head.addWidget(_label('톡게시판', 'cardSub', self))
        head.addStretch(1)
        v.addLayout(head)
        self.title = _label(msg.get('content', ''), 'cardTitle', self, wrap=True)   # 머리에 게시판 아이콘이 있어 📌 는 뺀다
        v.addWidget(self.title)
        v.addWidget(_label('눌러서 글과 댓글 보기', 'cardHint', self))
        self.clicked.connect(lambda: self.win.open_board(self.post_id))

    def relayout(self, max_w):
        w = min(CARD_W, max_w)
        self.setFixedWidth(w)
        fit_wrap(self.title, w - 28)


# ---------------------------------------------------------------- 행(row)
class ChipRow(QWidget):
    """날짜 구분선 / 시스템 메시지 (가운데 반투명 칩)"""

    def __init__(self, text, parent=None, msg=None):
        super().__init__(parent)
        self.msg = msg
        self.msg_id = msg['msg_id'] if msg else None
        self._flash = False
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 12, 4)
        self.label = _label(text, 'chip', self, wrap=True)
        self.label.setAlignment(Qt.AlignCenter)
        h.addStretch(1)
        h.addWidget(self.label)
        h.addStretch(1)

    def relayout(self, max_w, view_w=None):
        if view_w:
            w = max(120, int(view_w * 0.85))
            fit_text(self.label, w, pad_x=24, pad_y=8)

    def paintEvent(self, event):
        """날짜 구분선(KCS Ink 시그니처): 칩 양옆으로 가는 잉크 선이 종이를 가른다"""
        if self.msg is not None:
            return
        outer = getattr(self.window(), '_outer', None) or {}
        color = to_qcolor(outer.get('chip_bg') or theme.c('date_chip_bg'))
        color.setAlphaF(min(1.0, color.alphaF() * 1.6))
        geo = self.label.geometry()
        y = geo.center().y() + 0.5
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color, 1.0))
        left, right = 16, self.width() - 16
        if geo.left() - 10 > left:
            p.drawLine(QPointF(left, y), QPointF(geo.left() - 10, y))
        if geo.right() + 10 < right:
            p.drawLine(QPointF(geo.right() + 10, y), QPointF(right, y))

    def set_run(self, first, last):
        pass

    def refresh_unread(self):
        pass

    def flash(self):
        pass


class MessageRow(QWidget):
    """일반 메시지 한 줄 (프로필·이름·말풍선/카드·안 읽은 수·시간·공감)"""

    def __init__(self, win, msg, parent=None):
        super().__init__(parent)
        self.win, self.state, self.msg = win, win.state, msg
        self.msg_id = msg['msg_id']
        self.kind = msg.get('kind', 'text')
        self.deleted = bool(msg.get('deleted'))
        self.mine = msg.get('sender') == self.state.me
        self.first = self.last = None
        self._flash = False
        self.bubble = None
        self.text_label = None
        self.quote = None
        self.card = None
        self.thumb = None
        self.link_card = None
        self.edited_label = None
        self.silent_icon = None
        self._fit = []      # (label, 'text'|'elide', 원문)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.win.show_context_menu(self.msg, self.mapToGlobal(pos)))

        self.root = QHBoxLayout(self)
        self.root.setContentsMargins(12, 3, 12, 0)
        self.root.setSpacing(7)
        self.avatar = None
        self.name_label = None
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        if not self.mine:
            self.avatar = Clickable(self)
            self.avatar.setFixedSize(AV, AV)
            self.avatar.setAccessibleName(f"{self.state.display_name(msg.get('sender'))} 프로필")
            self._avatar_pm = QLabel(self.avatar)
            self._avatar_pm.setFixedSize(AV, AV)
            self.root.addWidget(self.avatar, 0, Qt.AlignTop)
            # 이름(+ AI 비서 표시, 다른 그룹사면 회사명)
            sender = msg.get('sender')
            self.name_label = QWidget(self)
            nh = QHBoxLayout(self.name_label)
            nh.setContentsMargins(0, 0, 0, 0)
            nh.setSpacing(5)
            self.sender_label = _label(self.state.member_name(win.room_id, sender), 'senderName', self.name_label)
            nh.addWidget(self.sender_label)
            if self.state.is_bot(sender):   # 이모지 대신 'AI' 태그 (아이콘을 글꼴 기호에 맡기지 않음)
                nh.addWidget(_label('AI', 'aiTag', self.name_label))
            self.company_label = None
            if self.state.is_external(sender):
                self.company_label = _label(self.state.company_of(sender), 'companyTag', self.name_label)
                nh.addWidget(self.company_label)
            nh.addStretch(1)
            col.addWidget(self.name_label)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(5)
        self.content = self._build_content()
        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(0)
        meta.addStretch(1)
        align = Qt.AlignRight if self.mine else Qt.AlignLeft
        self.unread_label = _ClickLabel(self)
        self.unread_label.setObjectName('unreadNum')
        self.unread_label.setCursor(Qt.PointingHandCursor)
        self.unread_label.setToolTip('안 읽은 사람 보기')
        self.unread_label.clicked.connect(lambda: self.win.show_unread_members(self.msg))
        self.time_label = _label(fmt_time(msg.get('timestamp')), 'timeLabel', self)
        # 아주 좁은 창 + 큰 글자(200%)에서만 '오후' / '4:30' 두 줄로 접혀 대화 영역이 옆으로 넘치지 않게
        # (평소에는 한 줄 - 자리가 있으면 레이아웃이 원래 폭을 준다)
        self.time_label.setWordWrap(True)
        # 시간 줄: [조용히 보낸 표시][수정됨][시간] (내 메시지) / [시간][수정됨] (상대 메시지)
        self.meta_line = QWidget(self)
        ml = QHBoxLayout(self.meta_line)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(3)
        if self.mine and msg.get('silent') and not self.deleted:
            self.silent_icon = QLabel(self.meta_line)
            self.silent_icon.setObjectName('silentIcon')
            self.silent_icon.setFixedSize(12, 12)
            self.silent_icon.setPixmap(make_icon('bell_off', to_qcolor(win._outer['time']), 12).pixmap(12, 12))
            self.silent_icon.setToolTip('조용히 보낸 메시지')
        if msg.get('edited') and not self.deleted:
            self.edited_label = _label('수정됨', 'timeLabel', self.meta_line)
            self.edited_label.setToolTip(f"{fmt_time(msg.get('edited_at'))} 수정" if msg.get('edited_at') else '수정됨')
        parts = [self.silent_icon, self.edited_label, self.time_label] if self.mine else \
            [self.time_label, self.edited_label]
        for w in parts:
            if w is not None:
                ml.addWidget(w, 0, Qt.AlignVCenter)
        self.fail_button = None
        if msg.get('failed'):
            # 보내지 못한 메시지: 시간 대신 빨간 ! + '전송 실패' (shape 09)
            self.time_label.setText('전송 실패')
            self.time_label.setObjectName('failText')
            self.fail_button = FailButton(self)
            self.fail_button.clicked.connect(
                lambda: self.win.failed_menu(self.msg).exec_(self.fail_button.mapToGlobal(QPoint(0, self.fail_button.height()))))
            meta.addWidget(self.fail_button, 0, align)
            if msg.get('fresh'):
                msg.pop('fresh', None)
                QTimer.singleShot(0, self.fail_button.announce)
        meta.addWidget(self.unread_label, 0, align)
        meta.addWidget(self.meta_line, 0, align)
        if self.mine:
            line.addStretch(1)
            line.addLayout(meta)
            line.addWidget(self.content, 0, Qt.AlignTop)
        else:
            line.addWidget(self.content, 0, Qt.AlignTop)
            line.addLayout(meta)
            line.addStretch(1)
        col.addLayout(line)
        # 키워드 알림 강조: 다른 사람이 보낸 메시지에 키워드가 있으면 테두리 + 칩
        self.keyword = '' if (self.mine or self.deleted) else self.state.keyword_hit(msg)
        self.keyword_chip = None
        if self.keyword:
            if self.bubble is not None:
                self.bubble.outline = KEYWORD_ACCENT
            self.keyword_chip = QWidget(self)
            self.keyword_chip.setObjectName('keywordChipBox')
            self.keyword_chip.setAttribute(Qt.WA_StyledBackground, True)
            kh = QHBoxLayout(self.keyword_chip)
            kh.setContentsMargins(6, 1, 6, 1)
            kh.setSpacing(3)
            bell = QLabel(self.keyword_chip)
            bell.setPixmap(make_icon('bell', keyword_text_color(), 11).pixmap(11, 11))
            kh.addWidget(bell)
            kh.addWidget(_label('키워드', 'keywordChip', self.keyword_chip))
            self.keyword_chip.setToolTip(f"키워드 알림: '{self.keyword}'")
            self.keyword_chip.setAccessibleName(f"키워드 알림: {self.keyword}")
            col.addWidget(self.keyword_chip, 0, align)
        self.reaction_bar = self._build_reactions()
        if self.reaction_bar is not None:
            col.addWidget(self.reaction_bar, 0, align)
        self.thread_link = None
        if int(msg.get('thread_count') or 0) > 0:   # 스레드 답글 (§10.4)
            last = f" · 마지막 {fmt_time(msg.get('thread_last_at'))}" if msg.get('thread_last_at') else ''
            self.thread_link = QPushButton(f"답글 {int(msg['thread_count'])}개{last}", self)
            self.thread_link.setObjectName('threadLink')
            self.thread_link.setCursor(Qt.PointingHandCursor)
            self.thread_link.setAccessibleName(f"스레드 답글 {int(msg['thread_count'])}개 보기")
            self.thread_link.setStyleSheet(
                f"QPushButton#threadLink {{ border: none; background: transparent; padding: 0 2px;"
                f" color: {theme.c('link')};"
                f" font-size: {theme.fs('caption')}px; font-weight: bold; text-align: left; }}"
                f" QPushButton#threadLink:hover {{ text-decoration: underline; }}")
            self.thread_link.clicked.connect(lambda: self.win.open_thread(self.msg))
            col.addWidget(self.thread_link, 0, align)
        self.root.addLayout(col, 1)
        self.refresh_unread()
        self._kb = False
        who = ('보내지 못한 메시지' if msg.get('failed') else '나') if self.mine else self.state.display_name(msg.get('sender'))
        preview = DELETED_TEXT if self.deleted else self.state.message_preview(msg)
        self.setAccessibleName(f"{who}: {preview}, {fmt_time(msg.get('timestamp'))}")
        if self.bubble is not None:
            self.bubble.setAccessibleName(self.accessibleName())
        if self.quote is not None:
            self.quote.setAccessibleName('답장한 원문으로 이동')
        if self.link_card is not None:
            self.link_card.setAccessibleName(f"링크 미리보기: {(msg.get('link_preview') or {}).get('title') or ''}")

    def set_kb_selected(self, on):
        self._kb = bool(on)
        self.update()

    # ---- 내용 구성
    def _text_names(self):
        if self.mine:
            return 'msgTextMine', 'quoteNameMine', 'quoteTextMine', 'quoteLineMine'
        return 'msgText', 'quoteName', 'quoteText', 'quoteLine'

    def _new_bubble(self):
        if self.mine:
            mc = self.win._mine
            return Bubble(True, self, fill=mc['bg'], radius=mc['radius'])
        return Bubble(False, self)

    def _build_content(self):
        m = self.msg
        kind = self.kind
        self.retention_label = None
        if self.deleted:
            self.bubble = self._new_bubble()
            v = QVBoxLayout(self.bubble)
            v.setContentsMargins(0, 0, 0, 0)
            lab = _label(DELETED_TEXT, 'deletedTextMine' if self.mine else 'deletedText', self.bubble, wrap=True)
            v.addWidget(lab)
            self._fit.append((lab, 'text', None))
            return self.bubble
        if kind == 'emoticon':
            lab = _label(m.get('content', ''), 'emoticon', self)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setContextMenuPolicy(Qt.NoContextMenu)
            self._fit.append((lab, 'text', None))
            return lab
        if kind == 'image':
            att = m.get('attachment') or {}
            self.thumb = ImageThumb(self)
            self.thumb.clicked.connect(lambda: self.win.open_image(self.msg))
            aid = att.get('attachment_id')
            cached = self.state.attachments.get(aid)
            if att.get('expired'):
                self.thumb.setText(EXPIRED_TEXT)
                self.thumb.setWordWrap(True)
                self.thumb.setFixedSize(180, 56)
                self.thumb.setCursor(Qt.ArrowCursor)
            elif cached:
                self.thumb.set_image(cached[0], self.win.max_bubble_width())
            elif aid is not None:
                self.win.request_thumb(aid)
            keep = retention_text(att)
            if not keep or att.get('expired'):
                return self.thumb
            box = QWidget(self)
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(3)
            bv.addWidget(self.thumb, 0, Qt.AlignRight if self.mine else Qt.AlignLeft)
            self.retention_label = _label(keep, 'retentionLabel', box)
            bv.addWidget(self.retention_label, 0, Qt.AlignRight if self.mine else Qt.AlignLeft)
            return box
        if kind == 'call':
            self.card = CallCard(self.win, m, self)
            return self.card
        if kind == 'ladder':
            self.card = LadderCard(self.win, m, self)
            return self.card
        if kind == 'board':
            self.card = BoardCard(self.win, m, self)
            return self.card
        if kind == 'poll':
            self.card = PollCard(self.win, m, self)
            return self.card
        if kind == 'contact':
            self.card = ContactCard(self.win, m, self)
            return self.card
        if kind == 'voice':
            self.card = VoiceCard(self.win, m, self)
            return self.card
        if kind == 'event':
            self.card = EventCard(self.win, m, self)
            return self.card
        text_name, qname, qtext, qline = self._text_names()
        self.bubble = self._new_bubble()
        v = QVBoxLayout(self.bubble)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        if kind == 'file':
            att = m.get('attachment') or {}
            h = QHBoxLayout()
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(10)
            ic = QLabel(self.bubble)
            if self.mine:   # 말풍선 꾸미기 색에서 파생 (어떤 스타일에서도 대비 유지)
                ic.setPixmap(icon_pixmap('file', to_qcolor(self.win._mine['bg']).name(), 38, bg=self.win._mine['sub']))
            else:
                ic.setPixmap(icon_pixmap('file', '#FFFFFF', 38, bg='#7A8A99'))
            ic.setFixedSize(38, 38)
            h.addWidget(ic, 0, Qt.AlignVCenter)
            info = QVBoxLayout()
            info.setSpacing(2)
            name = _label('', 'fileNameMine' if self.mine else 'fileName', self.bubble)
            sub = _label(f"{human_size(att.get('size'))} · {att.get('mime') or '파일'}", qtext, self.bubble)
            info.addWidget(name)
            info.addWidget(sub)
            keep = retention_text(att)
            if keep:
                self.retention_label = _label(keep, 'fileExpired' if att.get('expired') else qtext, self.bubble)
                info.addWidget(self.retention_label)
                self._fit.append((self.retention_label, 'elide_file', keep))
            h.addLayout(info)
            v.addLayout(h)
            btns = QHBoxLayout()
            btns.setSpacing(6)
            self.save_button = QPushButton('저장', self.bubble)
            self.open_button = QPushButton('열기', self.bubble)
            for b in (self.save_button, self.open_button):
                b.setObjectName('fileBtnMine' if self.mine else 'fileBtn')
                b.setCursor(Qt.PointingHandCursor)
                btns.addWidget(b, 1)
            self.save_button.clicked.connect(lambda: self.win.save_attachment(self.msg))
            self.open_button.clicked.connect(lambda: self.win.open_attachment(self.msg))
            if att.get('expired'):
                for b in (self.save_button, self.open_button):
                    b.setEnabled(False)
                    b.setToolTip(EXPIRED_TEXT)
            v.addLayout(btns)
            self._fit.append((name, 'elide_file', att.get('name') or m.get('content', '')))
            self._fit.append((sub, 'elide_file', sub.text()))
            return self.bubble
        reply = m.get('reply_to')
        if reply:
            self.quote = Clickable(self.bubble)
            qv = QVBoxLayout(self.quote)
            qv.setContentsMargins(0, 0, 0, 0)
            qv.setSpacing(2)
            qn = _label(f"{self.state.member_name(self.win.room_id, reply.get('sender'))}에게 답장", qname, self.quote)
            qp = _label('', qtext, self.quote)
            qv.addWidget(qn)
            qv.addWidget(qp)
            sep = QFrame(self.quote)
            sep.setObjectName(qline)
            sep.setFixedHeight(1)
            qv.addSpacing(4)
            qv.addWidget(sep)
            self.quote.clicked.connect(lambda: self.win.focus_message(reply.get('msg_id')))
            v.addWidget(self.quote)
            self._fit.append((qn, 'elide', qn.text()))
            self._fit.append((qp, 'elide', (reply.get('preview') or '').replace('\n', ' ')))
            self._quote_sep = sep
        if kind == 'secret' and not m.get('secret_ok', True):
            # 이 기기에서 해독하지 못한 비밀 메시지: 흐린 기울임꼴
            self.text_label = _label(m.get('secret_text') or '', 'secretFailMine' if self.mine else 'secretFail',
                                     self.bubble, wrap=True)
            v.addWidget(self.text_label)
            self._fit.append((self.text_label, 'text', None))
            return self.bubble
        body = m.get('secret_text', '') if kind == 'secret' else m.get('content', '')
        link = self.win._mine['link'] if self.mine else theme.c('link')
        mention = self.win._mine['link'] if self.mine else theme.c('mention')
        self.text_label = _label(message_html(body or '', self.win.mention_names(), link, mention),
                                 text_name, self.bubble, wrap=True, rich=True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.text_label.setContextMenuPolicy(Qt.NoContextMenu)
        self.text_label.setOpenExternalLinks(False)
        self.text_label.linkActivated.connect(osenv.open_url)
        self.text_label.setCursor(Qt.IBeamCursor)
        v.addWidget(self.text_label)
        self._fit.append((self.text_label, 'text', None))
        if m.get('link_preview') and (m['link_preview'] or {}).get('url'):
            self.link_card = LinkPreviewCard(m['link_preview'], self.mine, self.bubble)
            v.addWidget(self.link_card)
        return self.bubble

    def _build_reactions(self):
        reactions = {k: v for k, v in (self.msg.get('reactions') or {}).items() if v}
        if not reactions or self.deleted:
            return None
        bar = QWidget(self)
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.reaction_buttons = {}
        self._reaction_order = sorted(reactions.items(), key=lambda kv: -len(kv[1]))
        for emoji, users in self._reaction_order:
            b = QPushButton(f'{emoji}\u2009{len(users)}', bar)
            b.setObjectName('reactionMine' if self.state.me in users else 'reaction')
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(', '.join(self.state.display_name(u) for u in users))
            b.clicked.connect(lambda _=False, e=emoji: self.win.react(self.msg_id, e))
            self.reaction_buttons[emoji] = b
            h.addWidget(b)
        self.more_reactions = QPushButton('+0', bar)
        self.more_reactions.setObjectName('reaction')
        self.more_reactions.setCursor(Qt.PointingHandCursor)
        self.more_reactions.clicked.connect(lambda: self.win.show_reaction_picker(self.msg))
        h.addWidget(self.more_reactions)
        h.addStretch(1)
        self._fit_reactions(REACTION_BAR_MAX * 64)
        return bar

    def _fit_reactions(self, avail):
        """공감 칩은 avail 폭 안에 들어가는 만큼만(최대 REACTION_BAR_MAX), 나머지는 +N 으로 접는다
        (넘치면 대화 영역 전체가 넓어져 오른쪽 말풍선이 잘린다)"""
        order = getattr(self, '_reaction_order', None)
        if not order:
            return
        more_w = self.more_reactions.sizeHint().width() + 4
        used, shown = 0, 0
        for i, (emoji, _users) in enumerate(order):
            b = self.reaction_buttons[emoji]
            w = b.sizeHint().width() + 4
            need = w + (more_w if i < len(order) - 1 else 0)
            fits = shown < REACTION_BAR_MAX and used + need <= avail
            b.setVisible(fits)
            if fits:
                used += w
                shown += 1
        rest = order[shown:]
        self.more_reactions.setVisible(bool(rest))
        if rest:
            self.more_reactions.setText(f'+{len(rest)}')
            self.more_reactions.setToolTip('  '.join(f'{e} {len(u)}' for e, u in rest))
            self.more_reactions.setAccessibleName(f'공감 {len(rest)}종 더 보기')

    # ---- 갱신
    def set_run(self, first, last):
        if first != self.first:
            self.first = first
            if self.avatar is not None:
                self._avatar_pm.setPixmap(self.win.avatar_pm(self.msg.get('sender')) if first else QPixmap())
                self.avatar.setCursor(Qt.ArrowCursor)
                self.name_label.setVisible(first)
            if self.bubble is not None:
                self.bubble.set_tail(first)
            self.root.setContentsMargins(12, 10 if first else 3, 12, 0)
        if last != self.last:
            self.last = last
            self.time_label.setVisible(last)
            # 수정됨/조용히 표시는 시간이 숨겨진 연속 메시지에도 남긴다
            self.meta_line.setVisible(last or self.edited_label is not None or self.silent_icon is not None)

    def refresh_avatar(self):
        if self.avatar is not None and self.first:
            self._avatar_pm.setPixmap(self.win.avatar_pm(self.msg.get('sender')))

    def refresh_unread(self):
        n = self.state.message_unread(self.win.room_id, self.msg)
        self.unread_label.setText(str(n) if n > 0 else '')
        self.unread_label.setVisible(n > 0)
        self.unread_label.setAccessibleName(f'안 읽은 사람 {n}명 보기' if n > 0 else '')

    def relayout(self, max_w, view_w=None):
        inner = max_w - Bubble.hpad()
        if self.kind == 'file' and not self.deleted:
            inner = min(CARD_W, max_w) - Bubble.hpad() - 48
        quote_w = 0
        for lab, mode, text in self._fit:
            if mode == 'text':
                w, _ = fit_text(lab, inner)
                quote_w = max(quote_w, w)
            elif mode == 'elide_file':
                elide(lab, text, inner)
                lab.setFixedWidth(inner)
        if self.quote is not None:
            # 인용 블록 폭 = 본문 폭과 인용 미리보기 중 큰 쪽(최대 inner)
            for lab, mode, text in self._fit:
                if mode == 'elide':
                    lab.ensurePolished()
                    want = QFontMetrics(lab.font()).horizontalAdvance(text) + 2
                    quote_w = max(quote_w, min(inner, want))
            for lab, mode, text in self._fit:
                if mode == 'elide':
                    elide(lab, text, quote_w)
                    lab.setFixedWidth(quote_w)
            self._quote_sep.setFixedWidth(quote_w)
        if self.link_card is not None:
            # 카드 폭 = 본문 폭(최소 MIN_W), 말풍선 최대 폭을 넘지 않음
            self.link_card.relayout(min(inner, max(quote_w, LinkPreviewCard.MIN_W)))
        if self.card is not None:
            self.card.relayout(max_w)
        if self.thumb is not None:
            self.thumb.relayout(max_w)
        if self.reaction_bar is not None:
            self._fit_reactions(max_w)

    def set_image(self, data, max_w):
        if self.thumb is not None:
            self.thumb.set_image(data, max_w)

    def selected_text(self):
        if self.text_label is not None and self.text_label.hasSelectedText():
            return self.text_label.selectedText()
        return ''

    def flash(self):
        self._flash = True
        self.update()
        QTimer.singleShot(1600, self._unflash)

    def _unflash(self):
        self._flash = False
        self.update()

    def paintEvent(self, event):
        kb = getattr(self, '_kb', False)
        if not (self._flash or kb):
            return
        p = QPainter(self)   # 한 위젯에 painter 는 하나만 (강조 중에도 포커스 테두리가 그려지게)
        if self._flash:
            color = theme.qc('yellow')
            color.setAlpha(70)
            p.fillRect(self.rect(), color)
        if kb:   # 키보드로 고른 메시지: 2px 포커스 테두리
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(theme.qc('yellow_dark'), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(4, 2, -4, -2), 8, 8)


# ---------------------------------------------------------------- 입력창
class ComposeEdit(QTextEdit):
    """Enter 전송, Shift+Enter 줄바꿈, 최대 5줄까지 자동 높이"""
    submit = pyqtSignal()
    submit_silent = pyqtSignal()   # Ctrl+Shift+Enter (mac: Cmd+Shift+Enter)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('compose')
        self.setAcceptRichText(False)
        self.setPlaceholderText('메시지 입력')
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.popup_handler = None   # (key) -> bool : 멘션 팝업이 키를 처리하면 True
        self.mime_handler = None    # (QMimeData) -> bool : 파일/이미지를 전송으로 처리하면 True
        self.escape_handler = None  # () -> bool : Esc를 처리(수정/답장 취소)하면 True
        self.document().contentsChanged.connect(self.adjust_height)

    def canInsertFromMimeData(self, source):
        if source.hasImage() or (source.hasUrls() and any(u.isLocalFile() for u in source.urls())):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        # 붙여넣기/드롭한 사진·파일은 텍스트로 넣지 않고 바로 전송
        if self.mime_handler and self.mime_handler(source):
            return
        if source.hasText():
            self.textCursor().insertText(source.text())

    def keyPressEvent(self, event):
        key = event.key()
        if self.popup_handler and self.popup_handler(key):
            event.accept()
            return
        mods = event.modifiers()
        if key == Qt.Key_Escape and self.escape_handler and self.escape_handler():
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and (mods & Qt.ShiftModifier) and (mods & Qt.ControlModifier):
            event.accept()
            self.submit_silent.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not (mods & Qt.ShiftModifier):
            event.accept()
            self.submit.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.textCursor().insertText('\n')
            return
        super().keyPressEvent(event)

    def adjust_height(self):
        self.ensurePolished()
        fm = QFontMetrics(self.font())
        line = fm.lineSpacing()
        margin = int(self.document().documentMargin() * 2) + 4
        doc_h = int(math.ceil(self.document().size().height())) + 4
        h = max(line * 2 + margin, min(line * 5 + margin, doc_h))
        if self.height() != h or self.minimumHeight() != h:
            self.setFixedHeight(h)



def menu_qss():
    """채팅방 팝업 메뉴(말풍선·방·도구·전송 메뉴 공용) 스타일"""
    return dialog_qss() + f"""
        QMenu {{ background: {theme.c('bg')}; color: {theme.c('text')}; border: 1px solid {theme.c('border')};
                 padding: 4px; }}
        QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {theme.c('bg_hover')}; color: {theme.c('text')}; }}
        QMenu::item:disabled {{ color: {theme.c('text_hint')}; }}
    """



class ScheduleDialog(_Dialog):
    def __init__(self, text='', parent=None):
        super().__init__('예약 메시지', parent)
        self.resize(360, 280)
        v = QVBoxLayout(self)
        self.text = QTextEdit(self)
        self.text.setAcceptRichText(False)
        self.text.setPlainText(text)
        self.text.setPlaceholderText('예약할 메시지를 입력하세요')
        v.addWidget(self.text, 1)
        v.addWidget(_label('보낼 시각', 'dlgHint', self))
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600), self)
        self.when.setMinimumDateTime(QDateTime.currentDateTime())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat('yyyy-MM-dd  HH:mm')
        v.addWidget(self.when)
        box, self.ok, _ = _buttons(self, '예약')
        v.addWidget(box)


class ForwardDialog(_Dialog):
    def __init__(self, state, parent=None):
        super().__init__('전달할 채팅방 선택', parent)
        self.resize(320, 420)
        v = QVBoxLayout(self)
        self.list = QListWidget(self)
        self.list.setIconSize(QSize(32, 32))
        names = {u: state.display_name(u) for u in state.users}
        for room in state.sorted_rooms():
            rid = room['room_id']
            others = [m for m in room.get('members', []) if m != state.me] or [state.me]
            item = QListWidgetItem(state.room_title(rid))
            if room['type'] == 'group':
                from client.kakao.widgets import group_avatar_pixmap
                item.setIcon(QIcon(group_avatar_pixmap(others, names, 32)))
            else:
                item.setIcon(QIcon(avatar_pixmap(others[0], names.get(others[0], others[0]), 32)))
            item.setData(Qt.UserRole, rid)
            self.list.addItem(item)
        v.addWidget(self.list, 1)
        box, self.ok, _ = _buttons(self, '전달')
        self.ok.setEnabled(False)
        self.list.currentItemChanged.connect(lambda cur, _p: self.ok.setEnabled(cur is not None))
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        v.addWidget(box)

    def room_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None



class _StripHead(QWidget):
    """참여자 줄 머리: 누르면 펼치기/접기 (키보드 Space·Enter 도)"""

    def __init__(self, strip, parent=None):
        super().__init__(parent)
        self.strip = strip
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.strip.toggle()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.strip.toggle()
            return
        super().keyPressEvent(event)


def strip_chevron_pixmap(expanded, color, size=12):
    """'›'(접힘) / '⌄'(펼침) - 친구 탭 구역 머리글과 같은 모양"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    pts = [(2.5, 4), (6, 8), (9.5, 4)] if expanded else [(4, 2.5), (8, 6), (4, 9.5)]
    p.drawPolyline(*[QPointF(x * size / 12, y * size / 12) for x, y in pts])
    p.end()
    return pm


class MembersStrip(QFrame):
    """헤더 아래 참여자 줄 - 모든 채팅방(나와의 채팅 제외)에 있다.
    접힘: '대화상대 N명  김철수, 박민수 …  ›' 한 줄 / 펼침: + 사진·이름 칩 가로 목록, '⌄'.
    머리 줄(또는 › 버튼)을 누르면 열렸다 닫혔다 하고, 방마다 설정 members_strip_open 에 기억한다.
    내가 방금 만든 그룹방은 펼친 채로 연다. 초대 · 나가기로 인원이 바뀌면 refresh() 로 바로 고친다"""

    CHIP_AVATAR = 32

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win, self.state = win, win.state
        self.expanded = False
        self.setObjectName('membersStrip')
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = _StripHead(self, self)
        self.head.setObjectName('stripHead')
        self.head.setAttribute(Qt.WA_StyledBackground, True)
        top = QHBoxLayout(self.head)
        top.setContentsMargins(theme.sp('m'), theme.sp('xxs'), theme.sp('xs'), theme.sp('xxs'))
        top.setSpacing(theme.sp('xs'))
        self.title = _label('', 'stripTitle', self.head)
        top.addWidget(self.title)
        from client.kakao.chats_tab import ElidedLabel
        self.preview = ElidedLabel('', 'meta', 'text_sub', parent=self.head)   # 접혔을 때 이름 미리보기
        top.addWidget(self.preview, 1)
        self.all_button = QPushButton('전체 보기', self.head)
        self.all_button.setObjectName('stripAll')
        self.all_button.setCursor(Qt.PointingHandCursor)
        self.all_button.setAccessibleName('대화상대 전체 보기')
        self.all_button.clicked.connect(lambda _c=False: self.win.show_members_popup(anchor=self.all_button))
        top.addWidget(self.all_button)
        self.toggle_button = QToolButton(self.head)
        self.toggle_button.setObjectName('stripToggle')
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setIconSize(QSize(12, 12))
        self.toggle_button.setMinimumSize(theme.px(26), theme.px(26))
        self.toggle_button.clicked.connect(lambda _c=False: self.toggle())
        top.addWidget(self.toggle_button)
        v.addWidget(self.head)
        self.area = QScrollArea(self)
        self.area.setObjectName('stripArea')
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.setWidgetResizable(True)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.area.viewport().setObjectName('stripViewport')
        self.area.hide()
        wrap = QHBoxLayout()
        wrap.setContentsMargins(theme.sp('s'), 0, theme.sp('s'), theme.sp('xs'))
        wrap.addWidget(self.area)
        v.addLayout(wrap)
        self.chips = {}
        self.refresh()

    def members(self):
        room = self.state.room(self.win.room_id) or {}
        owner = room.get('owner')
        # 나 → 방장 → 이름순 (팝업과 같은 기준에 방장을 앞으로)
        return sorted(room.get('members', []), key=lambda u: (u != self.state.me, u != owner,
                                                               self.state.display_name(u)))

    def toggle(self):
        self.win.set_members_strip(not self.expanded)

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self.area.setVisible(self.expanded)
        # 펼치면 사진 칩이 이름을 보여 주므로 미리보기는 비운다 (숨기면 늘림 칸이 사라져 버튼이 가운데로 온다)
        self.preview.setText('' if self.expanded else self._preview_text)
        self._refresh_toggle()

    def _refresh_toggle(self):
        self.toggle_button.setIcon(QIcon(strip_chevron_pixmap(self.expanded, theme.c('text_sub'))))
        text = '대화상대 목록 접기' if self.expanded else '대화상대 목록 펼치기'
        self.toggle_button.setToolTip(text)
        self.toggle_button.setAccessibleName(text)
        self.head.setToolTip(text)
        self.head.setAccessibleName(f'{self.title.text()} - {text}')

    def refresh(self):
        c = theme.c
        self.setStyleSheet(f"""
            QFrame#membersStrip {{ background: {c('bg')}; border-bottom: 1px solid {c('border')}; }}
            QWidget#stripHead {{ background: transparent; }}
            QWidget#stripHead:hover {{ background: {c('bg_hover')}; }}
            QLabel#stripTitle {{ background: transparent; color: {c('text')}; font-size: {theme.fs('meta')}px;
                font-weight: bold; }}
            QToolButton#stripToggle {{ background: transparent; border: none; border-radius: 6px; }}
            QToolButton#stripToggle:hover {{ background: {c('bg_selected')}; }}
            QScrollArea#stripArea, QWidget#stripViewport, QWidget#stripHost {{ background: transparent; border: none; }}
            QToolButton#memChip {{ background: transparent; border: none; border-radius: 8px; padding: 2px 4px;
                color: {c('text')}; font-size: {theme.fs('caption')}px; }}
            QToolButton#memChip:hover {{ background: {c('bg_hover')}; }}
            {theme.button_qss('stripAll', 'ghost', size='meta', pad='3px 8px')}
            {theme.scrollbar_qss()}
        """)
        room = self.state.room(self.win.room_id) or {}
        members = self.members()
        self.title.setText(f'대화상대 {len(members)}명')
        names = [('나' if u == self.state.me else self.state.member_name(self.win.room_id, u)) for u in members]
        self._preview_text = ', '.join(names)
        self.preview.setToolTip(self._preview_text)
        host = QWidget()
        host.setObjectName('stripHost')
        h = QHBoxLayout(host)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.sp('xxs'))
        self.chips = {}
        size = theme.px(self.CHIP_AVATAR)
        fm = QFontMetrics(theme.font('caption'))
        name_w = theme.px(60)
        for u in members:
            name = self.state.member_name(self.win.room_id, u)
            label = '나' if u == self.state.me else fm.elidedText(name, Qt.ElideRight, name_w)
            chip = QToolButton(host)
            chip.setObjectName('memChip')
            chip.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            chip.setIcon(QIcon(user_avatar_pixmap(self.state, u, size, show_presence=True)))
            chip.setIconSize(QSize(size, size))
            chip.setText(label)
            chip.setCursor(Qt.PointingHandCursor)
            extra = [x for x in ('방장' if u == room.get('owner') else '', self.state.org_text(u)) if x]
            chip.setToolTip(' · '.join([name + (' (나)' if u == self.state.me else '')] + extra))
            chip.setAccessibleName(f'{name} 프로필 보기')
            chip.clicked.connect(lambda _c=False, name=u: self.win.open_profile(name))
            h.addWidget(chip)
            self.chips[u] = chip
        h.addStretch(1)
        self.area.setWidget(host)
        bar = self.area.horizontalScrollBar().sizeHint().height()
        self.area.setFixedHeight(host.sizeHint().height() + bar)
        self.set_expanded(self.expanded)


class MembersPopup(QFrame):
    """헤더의 대화상대 목록 팝업 (그룹방이면 초대 버튼 포함)"""

    def __init__(self, win, parent=None):
        super().__init__(parent, Qt.Popup)
        self.win, self.state = win, win.state
        self.setObjectName('membersPopup')
        c = theme.c
        self.setStyleSheet(f"""
            QFrame#membersPopup {{ background: {c('bg')}; border: 1px solid {c('border')}; border-radius: 8px; }}
            QLabel {{ background: transparent; }}
            QLabel#memName {{ color: {c('text')}; font-size: {theme.fs('body')}px; }}
            QLabel#memOrg {{ color: {c('text_sub')}; font-size: {theme.fs('caption')}px; }}
            QScrollArea#memArea, QWidget#memViewport, QWidget#memHost {{ background: transparent; border: none; }}
            QLabel#memHead {{ color: {c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QPushButton#memInvite {{ background: transparent; color: {c('text')}; border: none; text-align: left;
                padding: 8px 10px; font-size: {theme.fs('body')}px; border-radius: 6px; }}
            QPushButton#memInvite:hover {{ background: {c('bg_hover')}; }}
            QLabel#memOwner {{ color: {c('on_accent')}; background: {c('yellow')}; border-radius: 4px;
                padding: 0px 4px; font-size: {theme.fs('micro')}px; font-weight: bold; }}
            QToolButton#memMore {{ border: none; background: transparent; color: {c('text_sub')};
                font-size: {theme.fs('body')}px; padding: 0 4px; }}
            QToolButton#memMore:hover {{ color: {c('text')}; }}
            {theme.scrollbar_qss()}
        """)
        room = self.state.room(win.room_id) or {}
        members = sorted(room.get('members', []),
                         key=lambda u: (u != self.state.me, self.state.display_name(u)))
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(2)
        v.addWidget(_label(f'대화상대 {len(members)}', 'memHead', self))
        self.invite_button = None
        if room.get('type') == 'group':
            self.invite_button = QPushButton('  대화상대 초대', self)
            self.invite_button.setObjectName('memInvite')
            pm = QPixmap(40, 40)
            pm.setDevicePixelRatio(2.0)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            paint_icon_invite(p, QRectF(0, 0, 20, 20), c('text_sub'))
            p.end()
            self.invite_button.setIcon(QIcon(pm))
            self.invite_button.setCursor(Qt.PointingHandCursor)
            self.invite_button.clicked.connect(self._invite)
            v.addWidget(self.invite_button)
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setObjectName('memArea')
        area.viewport().setObjectName('memViewport')
        host = QWidget()
        host.setObjectName('memHost')
        hv = QVBoxLayout(host)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        self.member_rows = []
        self.owner = room.get('owner')
        self.manage_buttons = {}
        i_am_owner = self.owner == self.state.me and room.get('type') in ('group', 'open')
        for u in members:
            row = QWidget(host)
            h = QHBoxLayout(row)
            h.setContentsMargins(6, 5, 6, 5)
            h.setSpacing(9)
            av = QLabel(row)
            av.setFixedSize(32, 32)
            av.setPixmap(user_avatar_pixmap(self.state, u, 32, show_presence=True))
            h.addWidget(av)
            tv = QVBoxLayout()
            tv.setSpacing(0)
            me = ' (나)' if u == self.state.me else ''
            bot = '🤖 ' if self.state.is_bot(u) else ''
            name_row = QHBoxLayout()
            name_row.setSpacing(4)
            name_row.addWidget(_label(bot + self.state.display_name(u) + me, 'memName', row))
            if u == self.owner:
                name_row.addWidget(_label('방장', 'memOwner', row))
            name_row.addStretch(1)
            tv.addLayout(name_row)
            org = self.state.org_text(u)
            if org:
                tv.addWidget(_label(org, 'memOrg', row))
            h.addLayout(tv, 1)
            if i_am_owner and u != self.state.me:
                more = QToolButton(row)
                more.setObjectName('memMore')
                more.setText('⋯')
                more.setToolTip('방장 넘기기 · 내보내기')
                more.setAccessibleName(f'{self.state.display_name(u)} 관리')
                more.setCursor(Qt.PointingHandCursor)
                more.clicked.connect(lambda _c=False, name=u, btn=more: self._manage_menu(name, btn))
                h.addWidget(more)
                self.manage_buttons[u] = more
            hv.addWidget(row)
            self.member_rows.append(u)
        hv.addStretch(1)
        area.setWidget(host)
        area.setFixedHeight(min(320, 44 * len(members) + 4))
        v.addWidget(area)
        self.setFixedWidth(260)

    def _invite(self):
        self.close()
        self.win.open_invite_dialog()

    def _manage_menu(self, username, button):
        """방장 전용 (§10.3): 방장 넘기기 · 내보내기"""
        menu = QMenu(self)
        if not self.state.is_bot(username):
            menu.addAction('방장 넘기기', lambda: (self.close(), self.win.transfer_owner(username)))
        menu.addAction('내보내기', lambda: (self.close(), self.win.kick_member(username)))
        menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))


class UnreadMembersPopup(QFrame):
    """읽음 숫자를 누르면 뜨는 '안 읽은 사람' 목록 (§10.6)"""

    def __init__(self, win, message, parent=None):
        super().__init__(parent, Qt.Popup)
        self.win, self.state = win, win.state
        self.setObjectName('unreadPopup')
        c = theme.c
        self.setStyleSheet(f"""
            QFrame#unreadPopup {{ background: {c('bg')}; border: 1px solid {c('border')}; border-radius: 8px; }}
            QLabel {{ background: transparent; }}
            QLabel#memHead {{ color: {c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QLabel#memName {{ color: {c('text')}; font-size: {theme.fs('body')}px; }}
            QLabel#memOrg {{ color: {c('text_sub')}; font-size: {theme.fs('caption')}px; }}
        """)
        self.names = self.state.unread_members(win.room_id, message)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        v.addWidget(_label(f'안 읽은 사람 {len(self.names)}', 'memHead', self))
        for u in self.names[:50]:
            row = QHBoxLayout()
            row.setSpacing(8)
            av = QLabel(self)
            av.setFixedSize(28, 28)
            av.setPixmap(user_avatar_pixmap(self.state, u, 28, show_presence=True))
            row.addWidget(av)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_label(self.state.member_name(win.room_id, u), 'memName', self))
            org = self.state.org_text(u)
            if org:
                col.addWidget(_label(org, 'memOrg', self, wrap=True))
            row.addLayout(col, 1)
            v.addLayout(row)
        if len(self.names) > 50:
            v.addWidget(_label(f'외 {len(self.names) - 50}명', 'memOrg', self))
        if not self.names:
            v.addWidget(_label('모두 읽었습니다', 'memOrg', self))
        self.setMinimumWidth(theme.px(220))

    def member_names(self):
        return list(self.names)



class ReactionPicker(_Dialog):
    """공감 더 보기: 72종 그리드, 이미 단 공감은 강조 (다시 누르면 취소)"""
    picked = pyqtSignal(str)

    def __init__(self, mine=(), counts=None, parent=None):
        super().__init__('공감 선택', parent)
        counts = counts or {}
        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.addWidget(_label('말풍선 하나에 공감을 30종류까지 달 수 있습니다. 내가 단 공감을 다시 누르면 취소됩니다.', 'dlgHint', self))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.buttons = {}
        for i, emo in enumerate(MORE_REACTIONS):
            b = QPushButton(emo, self)
            if counts.get(emo):
                b.setToolTip(f'{counts[emo]}명이 공감')
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(theme.px(46), theme.px(38))
            b.setAccessibleName(f'공감 {emo}' + (f' {counts[emo]}명' if counts.get(emo) else ''))
            on = emo in mine
            b.setStyleSheet(f"{emoji_css()} font-size: {theme.fs('heading')}px; padding: 2px 6px;"
                            + (f" border: 2px solid {theme.c('yellow_dark')};" if on else ''))
            b.clicked.connect(lambda _=False, e=emo: self._pick(e))
            if counts.get(emo):
                badge = QLabel(str(counts[emo]), b)
                badge.setObjectName('pickerCount')
                badge.setAlignment(Qt.AlignCenter)
                badge.setStyleSheet(f"background: {theme.c('yellow')}; color: {theme.c('on_accent')};"
                                    f" border-radius: {theme.px(7)}px; font-size: {theme.fs('micro')}px; font-weight: bold; padding: 0 3px;")
                badge.adjustSize()
                bh = max(theme.px(14), badge.sizeHint().height())   # 글자 배율을 따라 높이도 커진다
                badge.setFixedHeight(bh)
                badge.move(b.width() - badge.width() - 2, b.height() - bh - 2)
            grid.addWidget(b, i // 8, i % 8)
            self.buttons[emo] = b
        v.addLayout(grid)

    def _pick(self, emoji):
        self.picked.emit(emoji)
        self.accept()


class EmoticonPopup(QFrame):
    """이모티콘 선택 팝업(그리드)"""
    picked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName('emoticonPopup')
        self.setStyleSheet(f"""
            QFrame#emoticonPopup {{ background: {theme.c('bg')}; border: 1px solid {theme.c('border')};
                                    border-radius: 8px; }}
            QToolButton {{ {emoji_css()} border: none; border-radius: 6px; font-size: {theme.fs('display')}px;
                background: transparent; }}
            QToolButton:hover {{ background: {theme.c('bg_hover')}; }}
        """)
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(2)
        self.buttons = []
        for i, emo in enumerate(EMOTICONS):
            b = QToolButton(self)
            b.setText(emo)
            b.setFixedSize(38, 38)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, e=emo: self._pick(e))
            grid.addWidget(b, i // 8, i % 8)
            self.buttons.append(b)

    def _pick(self, emo):
        self.picked.emit(emo)
        self.close()


# ---------------------------------------------------------------- 창
class ChatRoomWindow(QWidget):
    """카카오톡 채팅방 창 (최상위 창)"""

    HEADER_TOP = 10   # 헤더 위 여백 (macOS 별도 창이면 신호등 버튼 높이만큼 더한다)

    def __init__(self, state, room_id, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self.setAcceptDrops(True)
        self._wallpaper_spec = None
        self._pending_open = set()
        self.state = state
        self.room_id = room_id
        self.rows = {}          # msg_id -> MessageRow/ChipRow
        self._seps = {}         # date -> ChipRow
        self._order = []        # 화면에 표시된 메시지(오래된→최신)
        self._avatar_cache = {}
        self._requested_thumbs = set()
        self._pending_save = set()
        self._pending_view = set()
        self._pending_self_forward = []
        self._pending_focus = None
        self._reply_to = None
        self._editing = None        # 수정 중인 메시지
        self._edit_draft = ''       # 수정 시작 전 입력창 내용(취소/완료 시 복원)
        self._history_requested = False
        self._loading_older = False
        self._anchor = None
        self._stick_bottom = True
        self._last_max_w = None
        self._search_results = []
        self._search_idx = -1
        self._search_query = ''
        self._mention_names = None
        self._mention_candidates = []
        self._mine = mine_colors(theme.bubble_style('basic'))   # _apply_styles에서 설정값으로 갱신
        self._pending_call_start = None   # 시작 요청한 통화 모드 (calls_changed에서 내가 host인 통화가 오면 창 열기)
        self._revealed_ladders = set()    # 결과를 본 사다리 메시지 (행을 다시 만들어도 유지)
        self._board_dialog = None
        self._pending_play = {}           # attachment_id → VoiceCard (받으면 재생)
        self._render_count = RENDER_PAGE  # 화면에 만드는 최근 메시지 수 (받아 둔 메시지가 더 많으면 위로 올릴 때 더)
        self._summary_dialog = None
        self._export_path = None          # 내보내기 저장 경로 (None 이면 저장 창)
        self._export_requested = False
        # 대화 저장 시간 초과 감시: 창의 자식 타이머 하나, 슬롯은 창의 메서드 (창이 사라지면 연결도 함께 사라진다.
        # 예전에는 요청마다 타이머 + 람다를 만들었고, 창이 정리된 뒤 그 타이머가 울리면 앱이 죽을 수 있었다)
        self._export_timer = QTimer(self)
        self._export_timer.setSingleShot(True)
        self._export_timer.timeout.connect(self._export_timed_out)
        room0 = state.room(room_id) or {}
        # 열 때 안 읽은 메시지 수/위치: 많이 쌓였으면 '안 읽은 대화 요약' 칩
        self._unread_at_open = state.room_unread(room_id)
        self._read_pointer_at_open = (room0.get('read_pointers') or {}).get(state.me, 0)

        self.resize(400, 680)
        self.setMinimumSize(320, 460)
        self._build_ui()
        self._apply_styles()
        self._refresh_header()
        self._refresh_notice()
        self._ensure_members_strip()
        created = getattr(state, 'created_rooms', None)
        if created is not None and room_id in created:
            created.discard(room_id)
            self.set_members_strip(True)    # 방금 만든 방: 누가 들어왔는지 펼쳐서 바로 보이게
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(30)
        self._relayout_timer.timeout.connect(self._relayout_rows)
        self._rebuild()

        s = state
        s.message_added.connect(self._on_message_added)
        s.message_updated.connect(self._on_message_updated)
        s.history_loaded.connect(self._on_history_loaded)
        s.read_changed.connect(self._on_read_changed)
        s.room_changed.connect(self._on_room_changed)
        s.room_removed.connect(self._on_room_removed)
        s.room_opened.connect(self._on_room_opened)
        s.events_changed.connect(self._on_events_changed)
        s.attachment_loaded.connect(self._on_attachment_loaded)
        s.search_results.connect(self._on_search_results)
        s.scheduled_ok.connect(self._on_scheduled_ok)
        s.error.connect(self._on_error)
        s.users_changed.connect(self._refresh_header)
        s.calls_changed.connect(self._on_calls_changed)
        s.room_summary_ready.connect(self._on_summary_ready)
        s.message_removed.connect(self._on_message_removed)
        s.room_export_ready.connect(self._on_export_ready)
        if hasattr(s, 'connection_lost'):
            s.connection_lost.connect(self._on_connection_lost)
        if hasattr(s, 'welcomed'):
            s.welcomed.connect(self._on_welcomed)
        if hasattr(s, 'avatar_changed'):
            s.avatar_changed.connect(self._on_avatar_changed)
        if s.settings is not None:
            s.settings.changed.connect(self._on_settings_changed)

    # ================= UI 구성 =================
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 헤더
        self.header = QFrame(self)
        self.header.setObjectName('header')
        hh = QHBoxLayout(self.header)
        hh.setContentsMargins(14, self.HEADER_TOP, 10, 10)
        hh.setSpacing(10)
        self.avatar = Avatar(size=AV, parent=self.header)
        hh.addWidget(self.avatar)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        trow = QHBoxLayout()
        trow.setSpacing(5)
        self.title_label = _label('', 'roomTitle', self.header)
        self.lock_icon = QLabel(self.header)
        self.lock_icon.setToolTip(SECRET_SUBTITLE)
        self.mute_icon = QLabel(self.header)
        self.pin_icon = QLabel(self.header)
        trow.addWidget(self.lock_icon)
        trow.addWidget(self.title_label)
        trow.addWidget(self.mute_icon)
        trow.addWidget(self.pin_icon)
        trow.addStretch(1)
        tv.addStretch(1)
        tv.addLayout(trow)
        self.count_label = QPushButton(self.header)
        self.count_label.setObjectName('memberCount')
        self.count_label.setCursor(Qt.PointingHandCursor)
        self.count_label.setToolTip('대화상대 보기')
        self.count_label.clicked.connect(self.show_members_popup)
        tv.addWidget(self.count_label, 0, Qt.AlignLeft)
        tv.addStretch(1)
        hh.addLayout(tv, 1)
        # 통화는 카카오톡처럼 버튼 하나 + 메뉴(보이스톡/페이스톡/보이스룸)로 묶어 헤더 폭을 아낀다
        self.call_btn = ExtraIconButton('phone', '통화 (보이스톡 · 페이스톡 · 보이스룸)', 19, self.header)
        self.call_btn.clicked.connect(self._show_call_menu)
        self.call_buttons = [self.call_btn]
        self.invite_btn = InviteButton('friends', '대화상대 초대', 20, self.header)
        self.invite_btn.clicked.connect(self.open_invite_dialog)
        self.search_btn = IconButton('search', '대화 검색', 20, self.header)
        self.drawer_btn = IconButton('drawer', '채팅방 서랍', 20, self.header)
        self.menu_btn = IconButton('menu', '메뉴', 20, self.header)
        self.search_btn.clicked.connect(self.toggle_search)
        self.drawer_btn.clicked.connect(self.open_drawer)
        self.menu_btn.clicked.connect(self._show_room_menu)
        btns = QHBoxLayout()
        btns.setSpacing(0)
        self.header_buttons = self.call_buttons + [self.invite_btn, self.search_btn, self.drawer_btn, self.menu_btn]
        for b in self.header_buttons:
            btns.addWidget(b)
        hh.addLayout(btns)
        root.addWidget(self.header)

        # 참여자 줄 (방을 만든 직후 · 방 메뉴에서 켜기)
        self.members_strip = None
        self._strip_index = root.count()
        self._root_layout = root

        # 비밀채팅 안내 띠
        self.secret_banner = _label(SECRET_BANNER, 'secretBanner', self)
        self.secret_banner.setAlignment(Qt.AlignCenter)
        self.secret_banner.hide()
        root.addWidget(self.secret_banner)
        # 상대의 암호 키가 바뀌었을 때만 보이는 안내 (눌러서 확인 번호를 보고 새 키를 믿는다)
        self.secret_key_button = QPushButton('상대의 암호 키가 바뀌었습니다 · 눌러서 확인', self)
        self.secret_key_button.setObjectName('secretKeyButton')
        self.secret_key_button.setCursor(Qt.PointingHandCursor)
        self.secret_key_button.clicked.connect(self.confirm_secret_key)
        self.secret_key_button.hide()
        root.addWidget(self.secret_key_button)
        # 진행 중인 통화 띠
        self.call_banner = QFrame(self)
        self.call_banner.setObjectName('callBanner')
        cb = QHBoxLayout(self.call_banner)
        cb.setContentsMargins(12, 6, 8, 6)
        cb.setSpacing(8)
        self.call_banner_icon = QLabel(self.call_banner)
        cb.addWidget(self.call_banner_icon)
        self.call_banner_label = _label('', 'callBannerText', self.call_banner)
        cb.addWidget(self.call_banner_label, 1)
        self.call_banner_btn = QPushButton('참여하기', self.call_banner)
        self.call_banner_btn.setObjectName('callBannerBtn')
        self.call_banner_btn.setCursor(Qt.PointingHandCursor)
        self.call_banner_btn.clicked.connect(self._on_call_banner_clicked)
        cb.addWidget(self.call_banner_btn)
        self.call_banner.hide()
        root.addWidget(self.call_banner)

        # 검색 바
        self.search_bar = QFrame(self)
        self.search_bar.setObjectName('searchBar')
        sh = QHBoxLayout(self.search_bar)
        sh.setContentsMargins(10, 6, 8, 6)
        sh.setSpacing(4)
        self.search_edit = QLineEdit(self.search_bar)
        self.search_edit.setObjectName('searchEdit')
        self.search_edit.setPlaceholderText('대화 내용 검색')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._do_search)
        self.search_edit.installEventFilter(self)
        self.search_count = _label('', 'searchCount', self.search_bar)
        self.search_up = QToolButton(self.search_bar)
        self.search_up.setIcon(QIcon(chevron_pixmap(theme.c('text_sub'), 16, up=True)))
        self.search_up.setToolTip('이전(더 오래된) 결과')
        self.search_up.setAccessibleName('이전 검색 결과')
        self.search_down = QToolButton(self.search_bar)
        self.search_down.setIcon(QIcon(chevron_pixmap(theme.c('text_sub'), 16)))
        self.search_down.setToolTip('다음(더 최근) 결과')
        self.search_down.setAccessibleName('다음 검색 결과')
        self.search_close = QToolButton(self.search_bar)
        self.search_close.setIcon(make_icon('close', theme.c('text_sub'), 14))
        self.search_close.setAccessibleName('검색 닫기')
        self.search_up.clicked.connect(self.search_prev)
        self.search_down.clicked.connect(self.search_next)
        self.search_close.clicked.connect(self.close_search)
        for b in (self.search_up, self.search_down, self.search_close):
            b.setObjectName('searchNav')
            b.setCursor(Qt.PointingHandCursor)
        sh.addWidget(self.search_edit, 1)
        sh.addWidget(self.search_count)
        sh.addWidget(self.search_up)
        sh.addWidget(self.search_down)
        sh.addWidget(self.search_close)
        self.search_bar.hide()
        root.addWidget(self.search_bar)
        self.search_list = QListWidget(self)
        self.search_list.setObjectName('searchList')
        self.search_list.setMaximumHeight(150)
        self.search_list.itemClicked.connect(lambda it: self._go_search(self.search_list.row(it)))
        self.search_list.hide()
        root.addWidget(self.search_list)

        # 공지 배너
        self.notice_wrap = QFrame(self)
        self.notice_wrap.setObjectName('noticeWrap')
        nw = QVBoxLayout(self.notice_wrap)
        nw.setContentsMargins(10, 0, 10, 4)
        self.notice = Clickable(self.notice_wrap)
        self.notice.setObjectName('notice')
        self.notice.setFocusPolicy(Qt.TabFocus)
        self.notice.setAccessibleName('공지')
        self.notice.clicked.connect(self._on_notice_clicked)
        nh = QHBoxLayout(self.notice)
        nh.setContentsMargins(12, 9, 6, 9)
        nh.setSpacing(10)
        self.notice_icon = QLabel(self.notice)
        nh.addWidget(self.notice_icon, 0, Qt.AlignTop)
        nv = QVBoxLayout()
        nv.setSpacing(3)
        self.notice_text = _label('', 'noticeText', self.notice)
        self.notice_sub = _label('', 'noticeSub', self.notice)
        nv.addWidget(self.notice_text)
        nv.addWidget(self.notice_sub)
        nh.addLayout(nv, 1)
        self.notice_toggle = QToolButton(self.notice)
        self.notice_toggle.setObjectName('noticeBtn')
        self.notice_toggle.setToolTip('펼치기/접기')
        self.notice_toggle.clicked.connect(self._toggle_notice)
        self.notice_close = QToolButton(self.notice)
        self.notice_close.setObjectName('noticeBtn')
        self.notice_close.setIcon(make_icon('close', theme.c('text_sub'), 14))
        self.notice_close.setToolTip('공지 내리기')
        self.notice_close.setAccessibleName('공지 내리기')
        self.notice_close.clicked.connect(self._clear_notice)
        nh.addWidget(self.notice_toggle, 0, Qt.AlignTop)
        nh.addWidget(self.notice_close, 0, Qt.AlignTop)
        nw.addWidget(self.notice)
        self._notice_expanded = False
        root.addWidget(self.notice_wrap)

        # 안 읽은 대화 요약 칩 (카카오톡 v26.6.0 대화 요약)
        self.summary_chip = QPushButton(self)
        self.summary_chip.setObjectName('summaryChip')
        self.summary_chip.setCursor(Qt.PointingHandCursor)
        self.summary_chip.clicked.connect(self._summarize_unread)
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(10, 2, 10, 4)
        chip_row.addStretch(1)
        chip_row.addWidget(self.summary_chip)
        chip_row.addStretch(1)
        root.addLayout(chip_row)
        room_type = (self.state.room(self.room_id) or {}).get('type')
        if self._unread_at_open >= SUMMARY_CHIP_MIN and room_type != 'secret':
            self.summary_chip.setText(f'안 읽은 메시지 {self._unread_at_open}개 · 대화 요약 보기')
        else:
            self.summary_chip.hide()

        # 메시지 목록
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName('msgScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName('msgContainer')
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 4, 0, 12)
        self.list_layout.setSpacing(0)
        self.scroll.setWidget(self.container)
        self.scroll.viewport().setAutoFillBackground(False)
        self.container.setAutoFillBackground(False)
        self.scroll.viewport().installEventFilter(self)
        sb = self.scroll.verticalScrollBar()
        sb.rangeChanged.connect(self._on_range_changed)
        sb.valueChanged.connect(self._on_scroll)
        root.addWidget(self.scroll, 1)

        # 입력 영역
        self.input_area = QFrame(self)
        self.input_area.setObjectName('inputArea')
        iv = QVBoxLayout(self.input_area)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(0)
        self.reply_bar = QFrame(self.input_area)
        self.reply_bar.setObjectName('replyBar')
        rh = QHBoxLayout(self.reply_bar)
        rh.setContentsMargins(14, 8, 8, 8)
        rv = QVBoxLayout()
        rv.setSpacing(2)
        self.reply_title = _label('', 'replyTitle', self.reply_bar)
        self.reply_preview = _label('', 'replyPreview', self.reply_bar)
        rv.addWidget(self.reply_title)
        rv.addWidget(self.reply_preview)
        rh.addLayout(rv, 1)
        self.reply_cancel = QToolButton(self.reply_bar)
        self.reply_cancel.setObjectName('noticeBtn')
        self.reply_cancel.setIcon(make_icon('close', theme.c('text_sub'), 14))
        self.reply_cancel.setAccessibleName('답장 취소')
        self.reply_cancel.clicked.connect(self.cancel_reply)
        rh.addWidget(self.reply_cancel, 0, Qt.AlignTop)
        self.reply_bar.hide()
        iv.addWidget(self.reply_bar)
        # 메시지 수정 바
        self.edit_bar = QFrame(self.input_area)
        self.edit_bar.setObjectName('replyBar')
        eh = QHBoxLayout(self.edit_bar)
        eh.setContentsMargins(14, 8, 8, 8)
        eh.setSpacing(8)
        self.edit_bar_icon = QLabel(self.edit_bar)
        eh.addWidget(self.edit_bar_icon, 0, Qt.AlignTop)
        ebv = QVBoxLayout()
        ebv.setSpacing(2)
        self.edit_title = _label('메시지 수정', 'replyTitle', self.edit_bar)
        self.edit_preview = _label('', 'replyPreview', self.edit_bar)
        ebv.addWidget(self.edit_title)
        ebv.addWidget(self.edit_preview)
        eh.addLayout(ebv, 1)
        self.edit_cancel = QToolButton(self.edit_bar)
        self.edit_cancel.setObjectName('noticeBtn')
        self.edit_cancel.setIcon(make_icon('close', theme.c('text_sub'), 14))
        self.edit_cancel.setAccessibleName('수정 취소')
        self.edit_cancel.setToolTip('수정 취소 (Esc)')
        self.edit_cancel.clicked.connect(self.cancel_edit)
        eh.addWidget(self.edit_cancel, 0, Qt.AlignTop)
        self.edit_bar.hide()
        iv.addWidget(self.edit_bar)
        self.edit = ComposeEdit(self.input_area)
        self.edit.setAccessibleName('메시지 입력')
        self.edit.submit.connect(self.send_current)
        self.edit.submit_silent.connect(self.send_silent)
        self.edit.escape_handler = self._compose_escape
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.cursorPositionChanged.connect(self._update_mention_popup)
        self.edit.popup_handler = self._mention_key
        self.edit.mime_handler = self.handle_mime
        # KCS Ink 입력창: 종이(채팅 배경) 위에 뜬 둥근 카드 한 장 - 글자 칸 + 도구 줄 + 전송
        self.composer = QFrame(self.input_area)
        self.composer.setObjectName('composer')
        self.composer.setProperty('focused', False)
        cv = QVBoxLayout(self.composer)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cwrap = QVBoxLayout()
        cwrap.setContentsMargins(12, 4, 12, 12)
        cwrap.addWidget(self.composer)
        ev = QVBoxLayout()
        ev.setContentsMargins(12, 10, 12, 0)
        ev.addWidget(self.edit)
        cv.addLayout(ev)
        self.edit.installEventFilter(self)
        tb = QHBoxLayout()
        tb.setContentsMargins(6, 2, 8, 8)
        tb.setSpacing(0)
        self.emoji_btn = IconButton('smile', '이모티콘', 20, self.input_area)
        self.file_btn = IconButton('clip', '파일 보내기', 20, self.input_area)
        self.poll_btn = IconButton('poll', '투표', 20, self.input_area)
        self.event_btn = IconButton('calendar', '일정 공유', 20, self.input_area)
        self.schedule_btn = IconButton('clock', '예약 메시지', 20, self.input_area)
        self.ladder_btn = ExtraIconButton('ladder', '사다리 게임', 20, self.input_area)
        self.board_btn = ExtraIconButton('board', '톡게시판', 20, self.input_area)
        self.ladder_btn.clicked.connect(self.open_ladder_dialog)
        self.board_btn.clicked.connect(lambda: self.open_board())
        self.emoji_btn.clicked.connect(self.open_emoticons)
        self.file_btn.clicked.connect(self.pick_file)
        self.poll_btn.clicked.connect(self.open_poll_dialog)
        self.event_btn.clicked.connect(self.open_event_dialog)
        self.schedule_btn.clicked.connect(self.open_schedule_dialog)
        # 자주 쓰는 이모티콘·파일만 노출하고, 나머지는 ＋ 메뉴(이름이 보이는 목록)로 모은다
        self.plus_btn = IconButton('plus', '더보기 (투표 · 일정 · 예약 · 사다리 · 게시판)', 20, self.input_area)
        self.plus_btn.clicked.connect(self._show_tools_menu)
        self.menu_tools = [self.poll_btn, self.event_btn, self.schedule_btn, self.ladder_btn, self.board_btn]
        for b in self.menu_tools:
            b.hide()
        self.tool_buttons = [self.emoji_btn, self.file_btn, self.plus_btn, self.poll_btn, self.event_btn,
                             self.schedule_btn, self.ladder_btn, self.board_btn]
        # 비밀채팅에서 숨기는 도구 (서버도 secret 방에서는 file/poll/event 불가)
        self.secret_hidden_tools = [self.file_btn, self.plus_btn]
        for b in (self.emoji_btn, self.file_btn, self.plus_btn):
            tb.addWidget(b)
        tb.addStretch(1)
        self.send_btn = QPushButton('전송', self.input_area)
        self.send_btn.setObjectName('sendBtn')
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setEnabled(False)
        self.send_btn.setToolTip(f'Enter: 전송\n우클릭 또는 {SILENT_KEYS}: 조용히 보내기 (알림 없이)')
        self.send_btn.clicked.connect(lambda: self.send_current())
        self.send_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.send_btn.customContextMenuRequested.connect(self._show_send_menu)
        tb.addWidget(self.send_btn)
        cv.addLayout(tb)
        iv.addLayout(cwrap)
        root.addWidget(self.input_area)

        # 오버레이: 새 메시지 알림, 토스트, 멘션 자동완성
        self.new_pill = QPushButton(self)
        self.new_pill.setObjectName('newPill')
        self.new_pill.setCursor(Qt.PointingHandCursor)
        self.new_pill.clicked.connect(self.scroll_to_bottom)
        self.new_pill.hide()
        self.toast_label = _label('', 'toast', self)
        self.toast_label.setAlignment(Qt.AlignCenter)
        self.toast_label.setWordWrap(True)
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)
        self.mention_popup = QListWidget(self)
        self.mention_popup.setObjectName('mentionPopup')
        self.mention_popup.setFocusPolicy(Qt.NoFocus)
        self.mention_popup.setIconSize(QSize(24, 24))
        self.mention_popup.itemClicked.connect(lambda it: self._insert_mention(it.data(Qt.UserRole)))
        self.mention_popup.hide()

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._on_escape)
        QShortcut(QKeySequence.Find, self, activated=self.open_search)
        # 키보드로 메시지 고르기 (WCAG 2.1.1): Alt+↑/↓ 선택 → Shift+F10/메뉴 키 = 그 메시지의 메뉴
        self._kb_msg_id = None
        self.kb_shortcuts = [
            QShortcut(QKeySequence('Alt+Up'), self, activated=lambda: self.kb_select(-1)),
            QShortcut(QKeySequence('Alt+Down'), self, activated=lambda: self.kb_select(1)),
            QShortcut(QKeySequence('Shift+F10'), self, activated=self.kb_menu),
            QShortcut(QKeySequence(Qt.Key_Menu), self, activated=self.kb_menu),
            # 건너뛰기 (ui-styling: skip link): F6 → 어디에 있든 메시지 입력창으로
            QShortcut(QKeySequence(Qt.Key_F6), self, activated=self.focus_compose),
        ]
        self.kb_status = QLabel(self)       # 스크린리더용: 선택한 메시지를 이름으로 알림
        self.kb_status.setObjectName('kbStatus')
        self.kb_status.hide()

    def _outer_colors(self):
        """말풍선 밖(배경 위) 글자/칩 색: 배경화면이 어두우면 밝은 글자"""
        c = theme.c
        api = _wallpaper_api()
        spec = api.resolve_wallpaper(self.state.settings, self.room_id) if api else None
        self._wallpaper_spec = spec
        if spec is None:
            dark = theme.is_dark()
            return dict(title=c('text'), sub=c('text_sub'), icon=c('text_sub'),
                        name=c('text_sub') if dark else '#3B4462', time=c('text_sub') if dark else '#586079',
                        chip_bg=c('date_chip_bg'), chip_fg=c('system_text'))
        dark = api.is_dark_wallpaper(spec) if hasattr(api, 'is_dark_wallpaper') else True
        if dark:
            return dict(title='#FFFFFF', sub='rgba(255,255,255,0.80)', icon='#E8E8E8',
                        name='rgba(255,255,255,0.88)', time='rgba(255,255,255,0.75)',
                        chip_bg='rgba(0,0,0,0.38)', chip_fg='#FFFFFF')
        return dict(title='#191919', sub='#4A4F55', icon='#4A4F55', name='#3C4248', time='#4F5A64',
                    chip_bg='rgba(0,0,0,0.28)', chip_fg='#FFFFFF')

    def paintEvent(self, event):
        p = QPainter(self)
        api = _wallpaper_api()
        if api is not None:
            api.paint_wallpaper(p, QRectF(self.rect()), self._wallpaper_spec)
        else:
            p.fillRect(self.rect(), theme.qc('chat_bg'))

    def _apply_styles(self):
        c, px = theme.c, theme.px
        fam = theme.font_css()
        o = self._outer_colors()
        self._outer = o
        mc = self._mine = mine_colors(self.my_bubble_style())
        self.setStyleSheet(f"""
            QFrame#header {{ background: {c('header_panel')}; border: none;
                border-bottom: 1px solid {c('header_line')}; }}
            QFrame#noticeWrap {{ background: transparent; }}
            QWidget#msgContainer {{ background: transparent; }}
            QScrollArea#msgScroll, QWidget#msgViewport {{ background: transparent; border: none; }}
            QLabel#roomTitle {{ {fam} color: {c('text')}; font-size: {theme.fs('lead')}px; font-weight: bold; }}
            QPushButton#memberCount {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; background: transparent;
                border: none; padding: 1px 0px; text-align: left; }}
            QPushButton#memberCount:hover {{ color: {c('text')}; }}
            QLabel#companyTag {{ {fam} color: {c('text_sub')}; background: {c('bg_alt')}; border-radius: 4px;
                padding: 0px 5px; font-size: {theme.fs('micro')}px; }}
            QPushButton#fileBtn, QPushButton#fileBtnMine {{ {fam} background: transparent; color: {c('bubble_text')};
                border: 1px solid {c('border')}; border-radius: 6px; padding: 4px 6px; font-size: {theme.fs('meta')}px; }}
            QPushButton#fileBtnMine {{ color: {mc['fg']}; border: 1px solid {mc['line']}; }}
            QPushButton#fileBtn:hover {{ background: {c('bg_hover')}; }}
            QPushButton#fileBtnMine:hover {{ background: rgba(0,0,0,0.06); }}
            QPushButton[flat="true"] {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
            QPushButton[flat="true"]:hover {{ background: rgba(128,128,128,0.18); }}
            QFrame#searchBar {{ background: {c('bg')}; border-bottom: 1px solid {c('border')}; }}
            QLineEdit#searchEdit {{ {fam} background: {c('bg_alt')}; color: {c('text')}; border: 1px solid {c('border')};
                border-radius: 14px; padding: 5px 10px; font-size: {theme.fs('body')}px; }}
            QLabel#searchCount {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; padding: 0 4px; }}
            QToolButton#searchNav {{ border: none; color: {c('text_sub')}; background: transparent; padding: 4px 6px;
                font-size: {theme.fs('meta')}px; border-radius: 4px; }}
            QToolButton#searchNav:hover {{ background: {c('bg_hover')}; }}
            QListWidget#searchList {{ {fam} background: {c('bg')}; color: {c('text')}; border: none;
                border-bottom: 1px solid {c('border')}; font-size: {theme.fs('meta')}px; outline: 0; }}
            QListWidget#searchList::item {{ padding: 5px 10px; }}
            QListWidget#searchList::item:selected {{ background: {c('bg_selected')}; color: {c('text')}; }}
            QFrame#notice {{ background: {c('notice_bg')}; border-radius: 10px; }}
            QLabel#noticeText {{ {fam} color: {c('text')}; font-size: {theme.fs('body')}px; }}
            QLabel#noticeSub {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('caption')}px; }}
            QToolButton#noticeBtn {{ border: none; background: transparent; color: {c('text_sub')};
                font-size: {theme.fs('meta')}px; padding: 2px 5px; border-radius: 4px;
                min-width: 24px; min-height: 24px; }}
            QToolButton#noticeBtn:hover {{ background: {c('bg_hover')}; }}
            QLabel#chip {{ {fam} background: {o['chip_bg']}; color: {o['chip_fg']};
                border-radius: 11px; padding: 4px 12px; font-size: {theme.fs('meta')}px; }}
            QLabel#senderName {{ {fam} color: {o['name']}; font-size: {theme.fs('meta')}px; }}
            QLabel#timeLabel {{ {fam} color: {o['time']}; font-size: {theme.fs('micro')}px; }}
            QLabel#unreadNum {{ {fam} color: {c('unread_num')}; font-size: {theme.fs('caption')}px; font-weight: bold; }}
            QLabel#msgText {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('emphasis')}px; background: transparent; }}
            QLabel#msgTextMine {{ {fam} color: {mc['fg']}; font-size: {theme.fs('emphasis')}px; background: transparent; }}
            QLabel#deletedText {{ {fam} color: {c('text_hint')}; font-size: {theme.fs('body')}px; }}
            QLabel#deletedTextMine {{ {fam} color: {mc['sub']}; font-size: {theme.fs('body')}px; }}
            QLabel#emoticon {{ {emoji_css()} font-size: {px(46)}px; background: transparent; }}
            QLabel#quoteName {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QLabel#quoteNameMine {{ {fam} color: {mc['fg']}; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QLabel#quoteText {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#quoteTextMine {{ {fam} color: {mc['sub']}; font-size: {theme.fs('meta')}px; }}
            QFrame#quoteLine {{ background: {c('border')}; border: none; }}
            QFrame#quoteLineMine {{ background: {mc['line']}; border: none; }}
            QLabel#fileName {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('body')}px; font-weight: bold; }}
            QLabel#fileNameMine {{ {fam} color: {mc['fg']}; font-size: {theme.fs('body')}px; font-weight: bold; }}
            QLabel#imageThumb {{ {fam} background: {c('bubble_other')}; color: {c('text_hint')};
                border-radius: 10px; font-size: {theme.fs('meta')}px; }}
            QFrame#card {{ background: {c('bubble_other')}; border: 1px solid {c('border')}; border-radius: 12px; }}
            QLabel#cardTitle {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('emphasis')}px; font-weight: bold; }}
            QLabel#cardText {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('body')}px; }}
            QLabel#cardSub {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#cardHint {{ {fam} color: {c('text_hint')}; font-size: {theme.fs('meta')}px; }}
            QLabel#pollCountTop {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QCheckBox#pollOpt, QRadioButton#pollOpt {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('body')}px;
                background: transparent; spacing: 7px; }}
            QRadioButton#pollOpt::indicator {{ width: 14px; height: 14px; border-radius: 8px;
                border: 1px solid {c('text_hint')}; background: {c('bubble_other')}; }}
            QRadioButton#pollOpt::indicator:checked {{ border: 5px solid {c('yellow_dark')}; background: {theme.c('on_accent')};
                width: 6px; height: 6px; }}
            QCheckBox#pollOpt::indicator {{ width: 14px; height: 14px; border-radius: 4px;
                border: 1px solid {c('text_hint')}; background: {c('bubble_other')}; }}
            QCheckBox#pollOpt::indicator:checked {{ border: 1px solid {c('yellow_dark')}; background: {c('yellow')}; }}
            QRadioButton#pollOpt::indicator:disabled, QCheckBox#pollOpt::indicator:disabled {{
                border: 1px solid {c('border')}; }}
            {theme.button_qss('cardPrimary', 'primary', 'body', pad='7px 8px', fam=fam)}
            {theme.button_qss('cardBtn', 'secondary', 'meta', fam=fam)}
            {theme.button_qss('cardBtnOn', 'primary', 'meta', fam=fam)}
            QFrame#voice {{ background: {c('bubble_other')}; border: 1px solid {c('border')}; border-radius: {theme.radius('bubble')}px; }}
            QFrame#voiceMine {{ background: {mc['bg']}; border: none; border-radius: {theme.radius('bubble')}px; }}
            QLabel#voiceTime {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#voiceTimeMine {{ {fam} color: {mc['sub']}; font-size: {theme.fs('meta')}px; }}
            QPushButton#summaryChip {{ {fam} background: {c('bg')}; color: {c('text')}; border: 1px solid {c('border')};
                border-radius: {px(theme.radius('pill'))}px; padding: 0 {px(14)}px; font-size: {theme.fs('meta')}px; font-weight: bold;
                min-height: {px(30)}px; max-height: {px(30)}px; }}
            QPushButton#summaryChip[focusVisible="true"]:focus {{ border: 2px solid {c('yellow_dark')}; }}
            QPushButton#summaryChip:hover {{ background: {c('bg_hover')}; }}
            QPushButton#reaction, QPushButton#reactionMine {{ {emoji_css()} background: {c('reaction_bg')}; color: {c('text')};
                border: 1px solid {c('border')}; border-radius: 11px; padding: 2px 8px; font-size: {theme.fs('meta')}px; }}
            QPushButton#reactionMine {{ border: 1px solid {c('yellow_dark')}; font-weight: bold; }}
            QPushButton#reaction:hover, QPushButton#reactionMine:hover {{ background: {c('bg_hover')}; }}
            QFrame#linkCard {{ background: {'rgba(255,255,255,0.06)' if theme.is_dark() else 'rgba(0,0,0,0.035)'};
                border: 1px solid {c('border')}; border-radius: 8px; }}
            QFrame#linkCardMine {{ background: rgba(0,0,0,0.14); border: 1px solid {mc['line']};
                border-radius: 8px; }}
            QLabel#linkSite {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('caption')}px; background: transparent; }}
            QLabel#linkSiteMine {{ {fam} color: {mc['sub']}; font-size: {theme.fs('caption')}px; background: transparent; }}
            QLabel#linkTitle {{ {fam} color: {c('bubble_text')}; font-size: {theme.fs('body')}px; font-weight: bold;
                background: transparent; }}
            QLabel#linkTitleMine {{ {fam} color: {mc['fg']}; font-size: {theme.fs('body')}px; font-weight: bold;
                background: transparent; }}
            QLabel#linkDesc {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; background: transparent; }}
            QLabel#linkDescMine {{ {fam} color: {mc['sub']}; font-size: {theme.fs('meta')}px; background: transparent; }}
            QFrame#inputArea {{ background: transparent; border: none; }}
            QFrame#composer {{ background: {c('input_bg')}; border: 1px solid {c('border')}; border-radius: 16px; }}
            QFrame#composer[focused="true"] {{ border: 1px solid {c('yellow')}; }}
            QFrame#replyBar {{ background: {c('bg')}; border: 1px solid {c('border')}; border-radius: 12px;
                margin: 0px 12px 6px 12px; }}
            QLabel#replyTitle {{ {fam} color: {c('text')}; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QLabel#replyPreview {{ {fam} color: {c('text_sub')}; font-size: {theme.fs('meta')}px; }}
            QTextEdit#compose {{ {fam} background: transparent; color: {c('text')}; border: none;
                font-size: {theme.fs('emphasis')}px; selection-background-color: {c('yellow')}; selection-color: {theme.c('on_accent')}; }}
            QPushButton#sendBtn {{ {fam} background: {c('yellow')}; color: {theme.c('on_accent')}; border: none; border-radius: 10px;
                padding: 7px 18px; font-size: {theme.fs('body')}px; font-weight: bold; }}
            QPushButton#sendBtn:hover {{ background: {c('yellow_dark')}; }}
            QPushButton#sendBtn[focusVisible="true"]:focus {{ border: 2px solid {c('text')}; }}
            QPushButton#sendBtn:disabled {{ background: {c('bg_alt')}; color: {c('text_hint')}; border: none; }}
            QPushButton#newPill {{ {fam} background: {c('bg')}; color: {c('text')}; border: 1px solid {c('border')};
                border-radius: 15px; padding: 6px 14px; font-size: {theme.fs('meta')}px; }}
            QLabel#toast {{ {fam} background: rgba(30,30,30,0.85); color: #FFFFFF; border-radius: 14px;
                padding: 8px 16px; font-size: {theme.fs('meta')}px; }}
            QListWidget#mentionPopup {{ {fam} background: {c('bg')}; color: {c('text')}; border: 1px solid {c('border')};
                border-radius: 8px; font-size: {theme.fs('body')}px; outline: 0; padding: 4px; }}
            QListWidget#mentionPopup::item {{ padding: 4px 6px; border-radius: 4px; }}
            QListWidget#mentionPopup::item:selected {{ background: {c('bg_selected')}; color: {c('text')}; }}
            QLabel#secretFail {{ {fam} color: {c('text_hint')}; font-size: {theme.fs('body')}px;
                background: transparent; }}
            QLabel#secretFailMine {{ {fam} color: {mc['sub']}; font-size: {theme.fs('body')}px;
                background: transparent; }}
            QPushButton#secretKeyButton {{ {fam} background: {c('bg_alt')}; color: {c('danger_text')}; border: none;
                border-bottom: 1px solid {c('header_line')}; padding: 7px 8px; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QLabel#secretBanner {{ {fam} background: {c('bg_alt')}; color: {c('text_sub')}; font-size: {theme.fs('caption')}px;
                padding: 4px 8px; border-bottom: 1px solid {c('header_line')}; }}
            QFrame#callBanner {{ background: {CALL_GREEN}; border: none; }}
            QLabel#callBannerText {{ {fam} color: #FFFFFF; font-size: {theme.fs('body')}px; font-weight: bold; }}
            QPushButton#callBannerBtn {{ {fam} background: #FFFFFF; color: #17883A; border: none; border-radius: 12px;
                padding: 4px 12px; font-size: {theme.fs('meta')}px; font-weight: bold; }}
            QPushButton#callBannerBtn:hover {{ background: #E8F7EC; }}
            QWidget#keywordChipBox {{ background: {c('bg')}; border: 1px solid {KEYWORD_ACCENT}; border-radius: 8px; }}
            QLabel#failText {{ {fam} color: {c('danger_text')}; font-size: {theme.fs('micro')}px; font-weight: bold;
                background: transparent; }}
            QLabel#keywordChip {{ {fam} background: transparent; color: {keyword_text_color()}; border: none;
                font-size: {theme.fs('micro')}px; }}
            QLabel#aiTag {{ {fam} color: {theme.c('on_accent')}; background: {c('yellow')}; border-radius: 4px;
                padding: 0px 4px; font-size: {theme.fs('micro')}px; font-weight: bold; }}
            QLabel#retentionLabel {{ {fam} color: {o['time']}; font-size: {theme.fs('micro')}px; background: transparent; }}
            QLabel#fileExpired {{ {fam} color: {c('badge')}; font-size: {theme.fs('meta')}px; background: transparent; }}
            {theme.scrollbar_qss()}
        """)
        self.scroll.viewport().setObjectName('msgViewport')
        for b in self.header_buttons:
            b.refresh_icon(c('text_sub'))  # 헤더는 테마 패널 위이므로 배경화면과 무관
        for b in self.tool_buttons:
            b.refresh_icon(c('text_sub'))
        self.notice_icon.setPixmap(icon_pixmap('notice', c('text_sub'), 18))
        self.edit_bar_icon.setPixmap(pencil_pixmap(c('text_sub'), 16))
        self._refresh_bar_icons()
        self.edit.adjust_height()

    def _refresh_bar_icons(self):
        """검색·공지·답장·수정 바 아이콘을 현재 테마 색으로 다시 그린다 (다크 모드 전환 시)"""
        sub = theme.c('text_sub')
        self.search_up.setIcon(QIcon(chevron_pixmap(sub, 16, up=True)))
        self.search_down.setIcon(QIcon(chevron_pixmap(sub, 16)))
        for b in (self.search_close, self.notice_close, self.reply_cancel, self.edit_cancel):
            b.setIcon(make_icon('close', sub, 14))

    def apply_theme(self):
        """다크 모드 전환 등 테마 변경 시 색상을 다시 읽고 전체를 다시 그린다"""
        self._apply_styles()
        self._avatar_cache.clear()
        for row in list(self.rows.values()) + list(self._seps.values()):
            row.hide()
            row.deleteLater()
        self.rows = {}
        self._seps = {}
        self._refresh_header()
        self._refresh_notice()
        if self.members_strip is not None:
            self.members_strip.refresh()
        self._rebuild()
        self.update()

    # ================= 헤더 / 공지 =================
    def _refresh_header(self):
        room = self.state.room(self.room_id)
        if room is None:
            return
        members = room.get('members', [])
        names = {u: self.state.member_name(self.room_id, u) for u in members}
        if room['type'] == 'open':
            self.avatar.set_group([m for m in members if m != self.state.me] or members, names)
            sub = f'오픈채팅 · {len(members)}명'
            self.count_label.setIcon(make_icon('friends', theme.c('text_sub'), 13))
            tags = ' '.join(f'#{t}' for t in room.get('tags') or [])
            desc = '\n'.join(x for x in (room.get('description') or '', tags) if x)
            self.count_label.setToolTip(desc or ', '.join(names[m] for m in members))
        elif room['type'] == 'group':
            self.avatar.set_group([m for m in members if m != self.state.me] or members, names)
            companies = {self.state.company_of(m) for m in members if self.state.company_of(m)}
            sub = f'대화상대 {len(members)}명'
            if len(companies) > 1:
                sub += f' · 그룹사 {len(companies)}곳'
            self.count_label.setIcon(make_icon('friends', theme.c('text_sub'), 13))
            self.count_label.setToolTip(', '.join(names[m] for m in members))
        else:
            target = next((m for m in members if m != self.state.me), self.state.me)
            self.avatar.setPixmap(user_avatar_pixmap(self.state, target, AV, show_presence=room['type'] == 'direct'))
            info = self.state.user(target)
            if room['type'] == 'self':
                self.count_label.setIcon(QIcon())
                sub = '나와의 채팅 · 메모와 파일을 보관하세요'
            elif room['type'] == 'secret':
                self.count_label.setIcon(QIcon(extra_icon_pixmap('lock', theme.c('text_sub'), 13)))
                sub = SECRET_SUBTITLE
            elif info.get('is_bot'):
                self.count_label.setIcon(QIcon())
                sub = '문서를 보내면 학습하고, 질문에 답합니다'
            else:
                presence = self.state.presence_of(target)
                self.count_label.setIcon(QIcon(_presence_dot(PRESENCE_COLORS.get(presence, '#BDBDBD'))))
                org = self.state.org_text(target, sep=' ')
                sub = ' · '.join(x for x in (PRESENCE_LABELS.get(presence, ''), org) if x)
            self.count_label.setToolTip(info.get('status_message') or '대화상대 보기')
        self._subtitle_full = sub
        self.count_label.setIconSize(QSize(13, 13))
        self.count_label.show()
        title = self._title_text()
        self.setWindowTitle(f'{self.state.room_title(self.room_id)} - {APP_NAME}')
        self.title_label.setText(title)
        self.title_label.setToolTip(title)
        secret = room['type'] == 'secret'
        self.lock_icon.setPixmap(extra_icon_pixmap('lock', theme.c('text'), 15))
        self.lock_icon.setVisible(secret)
        # 알림 설정 표시: 끄기 = 종 꺼짐, 멘션만 = @
        mode = self.state.room_alert_mode(self.room_id)
        if mode == 'mention':
            self.mute_icon.setPixmap(extra_icon_pixmap('at', theme.c('text_hint'), 14))
            self.mute_icon.setToolTip('멘션 알림만 받기')
        else:
            self.mute_icon.setPixmap(icon_pixmap('bell_off', theme.c('text_hint'), 14))
            self.mute_icon.setToolTip('알림 끔')
        self.mute_icon.setVisible(mode in ('off', 'mention'))
        self.pin_icon.setPixmap(icon_pixmap('pin', theme.c('text_hint'), 14))
        self.pin_icon.setVisible(self.state.is_pinned(self.room_id))
        # 방 종류별 기능: 통화 버튼, 비밀채팅 안내/도구 제한
        rtype = room['type']
        self.call_btn.setVisible(bool(self.call_modes()))
        # 비밀채팅은 1:1 전용이라 초대할 수 없다
        self.invite_btn.setVisible(rtype != 'secret')
        self.secret_banner.setVisible(secret)
        self.secret_key_button.setVisible(secret and self.state.secret_key_changed(self.room_id))
        if secret:
            number = self.state.secret_fingerprint(self.room_id)
            self.lock_icon.setToolTip(SECRET_SUBTITLE + (f'\n확인 번호  {number}' if number else ''))
        for b in self.secret_hidden_tools:
            b.setVisible(not secret)
        self._refresh_call_banner()
        self._elide_title()


    def call_modes(self):
        """이 방에서 시작할 수 있는 통화 종류"""
        rtype = (self.state.room(self.room_id) or {}).get('type')
        modes = []
        if rtype in ('direct', 'group', 'secret', 'open'):
            modes += ['voice', 'video']
        if rtype in ('group', 'open'):
            modes.append('voiceroom')
        return modes

    def call_menu(self):
        menu = QMenu(self)
        for mode in self.call_modes():
            menu.addAction(CALL_MODES[mode], lambda m=mode: self.start_call(m))
        return menu

    def _show_call_menu(self):
        menu = self.call_menu()
        menu.exec_(self.call_btn.mapToGlobal(self.call_btn.rect().bottomLeft()))

    def _title_text(self):
        title = self.state.room_title(self.room_id)
        room = self.state.room(self.room_id) or {}
        if room.get('type') == 'secret' and title.startswith('🔒 '):
            title = title[2:]      # 헤더에는 자물쇠 아이콘을 따로 그린다
        return title

    def is_secret(self):
        return (self.state.room(self.room_id) or {}).get('type') == 'secret'

    def _header_buttons_width(self):
        return sum(b.width() for b in self.header_buttons if not b.isHidden())

    def header_overflow(self):
        """좁을 때 ☰ 메뉴로 옮길 헤더 아이콘, 덜 중요한 것부터 (버튼, 메뉴 이름, 동작)"""
        entries = [(self.drawer_btn, '채팅방 서랍', self.open_drawer)]
        if not self.is_secret():
            entries.append((self.invite_btn, '대화상대 초대', self.open_invite_dialog))
        return entries

    def _layout_header_overflow(self, title_room):
        """제목이 몇 글자밖에 안 보이면(84px 미만) 우선순위가 낮은 아이콘부터 하나씩 ☰ 메뉴로 옮긴다"""
        entries = self.header_overflow()
        movable = {id(e[0]) for e in entries}
        fixed = sum(b.sizeHint().width() for b in self.header_buttons
                    if id(b) not in movable and b not in (self.invite_btn, self.drawer_btn) and not b.isHidden())
        shown = list(entries)
        while shown and title_room(fixed + sum(e[0].sizeHint().width() for e in shown)) < theme.px(84):
            shown.pop(0)
        self.overflowed = [e for e in entries if e not in shown]
        self.compact_header = bool(self.overflowed)
        for btn, _text, _slot in entries:
            btn.setVisible(any(e[0] is btn for e in shown))
        if self.is_secret():
            self.invite_btn.setVisible(False)

    def _elide_title(self):
        icons = sum(18 for w in (self.lock_icon, self.mute_icon, self.pin_icon) if not w.isHidden())
        hw = self.header.width() if self.header.isVisible() else self.width()
        title_room = lambda buttons_w: hw - 86 - buttons_w - icons   # noqa: E731  제목에 남는 폭
        self._layout_header_overflow(title_room)
        avail = max(60, title_room(self._header_buttons_width()))
        self.title_label.ensurePolished()
        full = self._title_text()
        self.title_label.setText(QFontMetrics(self.title_label.font()).elidedText(full, Qt.ElideRight, avail))
        sub = getattr(self, '_subtitle_full', '')
        sub_avail = max(120, hw - 42 - self._header_buttons_width() - 50)
        self.count_label.setText(QFontMetrics(theme.font('meta')).elidedText(sub, Qt.ElideRight, sub_avail))

    def _refresh_notice(self):
        room = self.state.room(self.room_id) or {}
        notice = room.get('notice')
        if not notice:
            self.notice_wrap.hide()
            return
        self.notice_wrap.show()
        text = self.state.message_preview(notice).replace('\n', ' ' if not self._notice_expanded else '\n')
        dt = parse_dt(notice.get('timestamp'))
        when = f'{dt.year}. {dt.month}. {dt.day}.' if dt else ''
        self.notice_sub.setText(f"{self.state.display_name(notice.get('sender'))} · {when}")
        self.notice_toggle.setIcon(QIcon(chevron_pixmap(theme.c('text_sub'), 14, up=self._notice_expanded)))
        self.notice_toggle.setAccessibleName('공지 접기' if self._notice_expanded else '공지 펼치기')
        self.notice_text.setWordWrap(self._notice_expanded)
        self.notice_sub.setVisible(self._notice_expanded)
        # 글자 폭 = 공지 상자 안쪽 - 아이콘 - 펼치기·닫기 버튼 - 간격 (실제 위젯 크기로 계산, 확대해도 맞음)
        nh = self.notice.layout()
        m = nh.contentsMargins()
        used = (m.left() + m.right() + self.notice_icon.sizeHint().width() + self.notice_toggle.sizeHint().width()
                + self.notice_close.sizeHint().width() + 3 * nh.spacing() + 4)
        box = self.notice.width() if self.notice.width() > 50 else self.width() - 20
        width = max(80, box - used)
        if self._notice_expanded:
            self.notice_text.setText(text)
            self.notice_text.setMinimumWidth(0)
            self.notice_text.setMaximumWidth(16777215)
            fit_wrap(self.notice_text, width)
        else:
            self.notice_text.setMinimumSize(0, 0)
            self.notice_text.setMaximumSize(16777215, 16777215)
            elide(self.notice_text, text, width)

    def _toggle_notice(self):
        self._notice_expanded = not self._notice_expanded
        self._refresh_notice()

    def _on_notice_clicked(self):
        notice = (self.state.room(self.room_id) or {}).get('notice')
        if notice:
            self.focus_message(notice.get('msg_id'))

    def _clear_notice(self):
        if self.confirm('공지 내리기', '이 공지를 채팅방에서 내릴까요?'):
            self.state.set_notice(self.room_id, None)

    # ================= 렌더링 =================
    def max_bubble_width(self):
        vw = self.scroll.viewport().width()
        if vw < 150:
            vw = max(200, self.width() - 10)
        # 행 = 좌우 12 + 프로필 + 간격 7 + 말풍선 + 간격 5 + 시간 열. 시간 열은 글자 크기 배율에 따라 넓어지므로
        # 실제 글꼴로 '오후 12:59' 폭을 재서 남긴다 (고정 44px 이면 좁은 창·큰 글자에서 대화 영역이 옆으로 넘쳤다)
        meta = _time_column_width()
        return max(110, min(int(vw * 0.65), vw - 24 - AV - 7 - 5 - meta))

    def _view_width(self):
        vw = self.scroll.viewport().width()
        return vw if vw >= 150 else max(200, self.width() - 10)

    def avatar_pm(self, username):
        pm = self._avatar_cache.get(username)
        if pm is None:
            pm = user_avatar_pixmap(self.state, username, AV)
            self._avatar_cache[username] = pm
        return pm

    def mention_names(self):
        if self._mention_names is None:
            room = self.state.room(self.room_id) or {}
            names = set()
            for u in room.get('members', []):
                names.add(u)
                names.add(self.state.display_name(u))
                names.add(self.state.member_name(self.room_id, u))
            self._mention_names = names
        return self._mention_names

    def _make_row(self, m):
        if m.get('kind') == 'system':
            row = ChipRow(m.get('content', ''), self.container, msg=m)
        else:
            row = MessageRow(self, m, self.container)
        row.relayout(self.max_bubble_width(), self._view_width())
        return row

    def _date_row(self, d):
        row = self._seps.get(d)
        if row is None:
            row = ChipRow(fmt_date(d), self.container)
            row.relayout(self.max_bubble_width(), self._view_width())
        return row

    @staticmethod
    def _msg_date(m):
        dt = parse_dt(m.get('timestamp'))
        return dt.date() if dt else None

    def _rebuild(self):
        """받아 둔 메시지 중 최근 _render_count 개로 목록을 다시 배치(기존 행 위젯은 재사용)"""
        all_msgs = self.state.timeline_messages(self.room_id)
        msgs = all_msgs[-self._render_count:] if self._render_count < len(all_msgs) else all_msgs
        self._hidden_older = len(all_msgs) - len(msgs)
        lay = self.list_layout
        while lay.count():
            lay.takeAt(0)
        lay.addStretch(1)
        new_rows, new_seps, last_date = {}, {}, None
        for m in msgs:
            d = self._msg_date(m)
            if d != last_date and d is not None:
                sep = self._date_row(d)
                lay.addWidget(sep)
                new_seps[d] = sep
                last_date = d
            row = self.rows.get(m['msg_id'])
            if row is None or row.msg is not m:
                if row is not None:
                    row.hide()
                    row.deleteLater()
                row = self._make_row(m)
            lay.addWidget(row)
            row.show()
            new_rows[m['msg_id']] = row
        for mid, row in self.rows.items():
            if mid not in new_rows:
                row.hide()
                row.deleteLater()
        for d, sep in self._seps.items():
            if d not in new_seps:
                sep.hide()
                sep.deleteLater()
        self.rows, self._seps, self._order = new_rows, new_seps, msgs
        self._apply_runs()

    @staticmethod
    def _run_key(m):
        if m.get('kind') == 'system':
            return None
        if m.get('failed'):   # 보내지 못한 메시지는 각자 한 묶음 → 시간 자리('전송 실패')가 항상 보인다
            return ('failed', m['msg_id'])
        return (m.get('sender'), (m.get('timestamp') or '')[:16])

    def _apply_runs(self, start=0):
        """연속 메시지 규칙: 같은 사람·같은 분이면 첫 메시지만 프로필/이름, 마지막만 시간"""
        order = self._order
        n = len(order)
        for i in range(max(0, start), n):
            m = order[i]
            key = self._run_key(m)
            if key is None:
                continue
            prev_key = self._run_key(order[i - 1]) if i > 0 else None
            next_key = self._run_key(order[i + 1]) if i + 1 < n else None
            row = self.rows.get(m['msg_id'])
            if row is not None:
                row.set_run(prev_key != key, next_key != key)

    def _append(self, m):
        """마지막에 새 메시지 한 개를 빠르게 추가"""
        d = self._msg_date(m)
        last_date = self._msg_date(self._order[-1]) if self._order else None
        if d is not None and d != last_date:
            sep = self._date_row(d)
            self.list_layout.addWidget(sep)
            self._seps[d] = sep
        row = self._make_row(m)
        self.list_layout.addWidget(row)
        self.rows[m['msg_id']] = row
        self._order = self._order + [m]
        self._render_count += 1
        self._apply_runs(len(self._order) - 2)

    def _on_message_removed(self, rid, msg_id):
        """로컬 메시지(전송 실패) 하나 제거: 그 행만 지우고 앞뒤 묶음만 다시 계산 (방 전체를 다시 만들지 않음)"""
        if rid != self.room_id or msg_id not in self.rows:
            return
        idx = next((i for i, o in enumerate(self._order) if o['msg_id'] == msg_id), None)
        d = self._msg_date(self._order[idx]) if idx is not None else None
        same_day = [o for o in self._order if o['msg_id'] != msg_id and self._msg_date(o) == d]
        if d is not None and not same_day:      # 그날 유일한 메시지였다면 날짜 구분선도 정리
            self._rebuild()
            return
        row = self.rows.pop(msg_id)
        self.list_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._order = [o for o in self._order if o['msg_id'] != msg_id]
        self._apply_runs(max(0, (idx or 0) - 1))

    def _insert_before_failed(self, m):
        """맨 아래가 보내지 못한 메시지들이고 새 서버 메시지가 그 앞의 마지막보다 새것이면, 실패 말풍선 바로 위에
        한 행만 끼워 넣는다 (같은 날짜일 때). 아니면 False → 전체 다시 만들기"""
        if m.get('failed'):
            return False
        k = next((i for i, o in enumerate(self._order) if o.get('failed')), None)
        if k is None or k == 0 or not all(o.get('failed') for o in self._order[k:]):
            return False
        prev = self._order[k - 1]
        if m['msg_id'] <= prev['msg_id'] or self._msg_date(m) != self._msg_date(prev):
            return False
        row = self._make_row(m)
        pos = self.list_layout.indexOf(self.rows[self._order[k]['msg_id']])
        if pos < 0:
            return False
        self.list_layout.insertWidget(pos, row)
        self.rows[m['msg_id']] = row
        self._order = self._order[:k] + [m] + self._order[k:]
        self._render_count += 1
        self._apply_runs(k - 1)
        return True

    def _replace_row(self, m):
        old = self.rows.get(m['msg_id'])
        if old is None:
            return
        new = self._make_row(m)
        self.list_layout.replaceWidget(old, new)
        old.hide()
        old.deleteLater()
        self.rows[m['msg_id']] = new
        self._order = [m if o['msg_id'] == m['msg_id'] else o for o in self._order]
        idx = next((i for i, o in enumerate(self._order) if o['msg_id'] == m['msg_id']), 0)
        new.first = new.last = None
        self._apply_runs(max(0, idx - 1))

    def _relayout_rows(self):
        w = self.max_bubble_width()
        vw = self._view_width()
        if w == self._last_max_w:
            return
        self._last_max_w = w
        for row in list(self.rows.values()) + list(self._seps.values()):
            row.relayout(w, vw)

    # ================= 스크롤 =================
    def _near_bottom(self):
        sb = self.scroll.verticalScrollBar()
        return sb.maximum() - sb.value() < 60

    def _on_range_changed(self, _min, maximum):
        sb = self.scroll.verticalScrollBar()
        if self._stick_bottom:
            sb.setValue(maximum)
        elif self._anchor is not None:
            sb.setValue(maximum - self._anchor)

    def _on_scroll(self, value):
        sb = self.scroll.verticalScrollBar()
        if self._anchor is None:
            self._stick_bottom = sb.maximum() - value < 30
        if self._near_bottom():
            self.new_pill.hide()
        if value <= 40 and sb.maximum() > 0 and self._anchor is None:
            self._load_older()

    def _load_older(self):
        if getattr(self, '_hidden_older', 0) > 0 and self._order:
            # 이미 받아 둔 더 오래된 메시지를 150개 더 그린다 (서버 요청 없음)
            sb = self.scroll.verticalScrollBar()
            self._stick_bottom = False
            self._anchor = sb.maximum() - sb.value()
            self._render_count += RENDER_PAGE
            self._rebuild()
            self.container.layout().activate()
            QTimer.singleShot(150, self._clear_anchor)
            return True
        if self._loading_older or not self.state.has_more.get(self.room_id) or not self._order:
            return False
        if not self._online():
            # 끊긴 상태면 요청이 버려져 플래그가 계속 남으므로 걸지 않는다 (다시 연결되면 스크롤로 재시도)
            return False
        sb = self.scroll.verticalScrollBar()
        self._loading_older = True
        self._stick_bottom = False
        self._anchor = sb.maximum() - sb.value()
        self.state.load_history(self.room_id, older=True)
        return True

    def _online(self):
        conn = getattr(self.state, 'conn', None)
        return conn is not None and bool(getattr(conn, 'is_connected', False))

    def _on_connection_lost(self, *_):
        """연결이 끊기면 응답이 오지 않으므로 이전 대화 로딩 플래그를 풀어 둔다"""
        self._loading_older = False
        self._anchor = None

    def confirm_secret_key(self):
        """상대의 암호 키가 바뀌었을 때: 확인 번호를 보여 주고, 사용자가 맞다고 하면 새 키를 믿는다"""
        name = self.state.display_name(self.state.secret_peer(self.room_id))
        number = self.state.secret_fingerprint(self.room_id)
        if self.confirm('암호 키 확인',
                        f'{name}님의 암호 키가 바뀌었습니다.\n다른 PC 에서 접속했거나 키를 새로 만들면 바뀝니다.\n\n'
                        f'확인 번호  {number}\n\n'
                        '전화처럼 메신저가 아닌 방법으로, 상대 화면(자물쇠 아이콘에 마우스를 올리면 보입니다)의 '
                        '확인 번호와 같은지 맞춰 보세요.\n같으면 [예]를 눌러 새 키로 대화를 이어 갑니다.'):
            self.state.trust_secret_key(self.room_id)
            self._refresh_header()

    def _request_history(self):
        """처음 열 때 최근 대화를 한 번 받는다. 연결이 끊겨 요청이 나가지 못했으면 다음 기회(다시 보일 때 · 다시 연결됐을 때)에"""
        if not self._history_requested:
            self._history_requested = bool(self.state.load_history(self.room_id))

    def _on_welcomed(self):
        if self.isVisible():        # 끊긴 동안 연 방: 다시 연결되면 대화를 받는다
            self._request_history()

    def scroll_to_bottom(self):
        self._stick_bottom = True
        self._anchor = None
        self.new_pill.hide()
        self.container.layout().activate()
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _scroll_to_row(self, row):
        self._stick_bottom = False
        self._anchor = None
        self.container.layout().activate()

        def go():
            if row.parent() is None:
                return
            vh = self.scroll.viewport().height()
            y = row.y() + row.height() // 2 - vh // 2
            sb = self.scroll.verticalScrollBar()
            sb.setValue(max(0, min(sb.maximum(), y)))
        go()
        QTimer.singleShot(0, go)

    def focus_message(self, msg_id, hint=None):
        """메시지로 스크롤하고 잠시 강조. 아직 로드되지 않았으면 이전 대화를 더 불러온다.
        hint = 검색 결과 등 이미 받은 메시지 (아직 받지 않은 스레드 답글도 바로 스레드를 연다)"""
        if msg_id is None:
            return False
        row = self.rows.get(msg_id)
        target = (self.state.find_message(self.room_id, msg_id) or hint) if row is None else None
        if target is not None and target.get('thread_root') is not None:   # 스레드 답글이면 스레드를 연다
            root = self.state.find_message(self.room_id, target['thread_root']) or {'msg_id': target['thread_root']}
            self.open_thread(root)
            return self.focus_message(target['thread_root'])
        if row is None:
            # 받아 두었지만 아직 그리지 않은 오래된 메시지면 거기까지 그린다
            all_ids = [m['msg_id'] for m in self.state.timeline_messages(self.room_id)]
            if msg_id in all_ids:
                self._render_count = max(self._render_count, len(all_ids) - all_ids.index(msg_id))
                self._rebuild()
                row = self.rows.get(msg_id)
        if row is not None:
            self._pending_focus = None
            self._scroll_to_row(row)
            row.flash()
            return True
        oldest = self._order[0]['msg_id'] if self._order else None
        if self.state.has_more.get(self.room_id) and (oldest is None or msg_id < oldest):
            self._pending_focus = msg_id
            if not self._loading_older and self._online():
                self._loading_older = True
                self.state.load_history(self.room_id, older=True)
            return False
        self._pending_focus = None
        self.toast('메시지를 찾을 수 없습니다.')
        return False

    # ================= 상태 시그널 =================
    def _on_history_loaded(self, rid, batch, has_more):
        if rid != self.room_id:
            return
        if self._loading_older:
            self._render_count += len(batch)   # 서버에서 더 받은 오래된 메시지도 보이게
        self._loading_older = False
        self._rebuild()
        if self._anchor is not None:
            self.container.layout().activate()
            QTimer.singleShot(150, self._clear_anchor)
        if self._pending_focus is not None:
            target = self._pending_focus
            found = self.state.find_message(self.room_id, target)
            if target in self.rows or (found is not None and found.get('thread_root') is not None):
                self.focus_message(target)   # 스레드 답글이면 스레드 창을 연다 (§10.4)
            elif has_more and batch:
                self._loading_older = True
                self.state.load_history(self.room_id, older=True)
            else:
                self._pending_focus = None
                self.toast('메시지를 찾을 수 없습니다.')
        self._maybe_mark_read()

    def _clear_anchor(self):
        self._anchor = None

    def _on_message_added(self, rid, m):
        if rid != self.room_id:
            return
        if m.get('thread_root') is not None:   # 스레드 답글은 원글의 '답글 N개'로만 (§10.4)
            self._maybe_mark_read()
            return
        mine = m.get('sender') == self.state.me
        follow = self._stick_bottom or self._near_bottom() or mine
        if self._order and m['msg_id'] > self._order[-1]['msg_id']:
            self._append(m)
        elif not self._insert_before_failed(m):
            self._rebuild()
        if follow:
            self._stick_bottom = True
            self._anchor = None
            QTimer.singleShot(0, self.scroll_to_bottom)
        elif m.get('kind') != 'system':
            self._show_new_pill(m)
        self._maybe_mark_read()

    def _on_message_updated(self, rid, m):
        if rid != self.room_id or m.get('thread_root') is not None:
            return
        settings = self.state.settings
        hidden = settings is not None and settings.contains('hidden_messages', m['msg_id'])
        if hidden or m['msg_id'] not in self.rows:
            self._rebuild()
        else:
            self._replace_row(m)
        if self._reply_to and self._reply_to.get('msg_id') == m['msg_id'] and (hidden or m.get('deleted')):
            self.cancel_reply()
        if self._editing and self._editing.get('msg_id') == m['msg_id'] and (hidden or m.get('deleted')):
            self.cancel_edit()

    def _on_read_changed(self, rid):
        if rid != self.room_id:
            return
        for row in self.rows.values():
            row.refresh_unread()

    def _on_room_changed(self, rid):
        if rid != self.room_id:
            return
        self._mention_names = None
        self._refresh_header()
        self._refresh_notice()
        if self.members_strip is not None:
            self.members_strip.refresh()        # 초대 · 나가기 · 방장 변경을 참여자 줄에도
        for row in self.rows.values():
            row.refresh_unread()

    def _on_room_removed(self, rid):
        if rid == self.room_id:
            self.close()

    def _on_room_opened(self, rid):
        room = self.state.room(rid)
        if self._pending_self_forward and room and room.get('type') == 'self':
            pending, self._pending_self_forward = self._pending_self_forward, []
            for m in pending:
                self.state.forward(m, rid)
            self.toast('나와의 채팅방으로 보냈습니다.')

    def _on_events_changed(self):
        for m in [o for o in self._order if o.get('kind') == 'event']:
            if m['msg_id'] in self.rows:
                self._replace_row(m)

    def _on_avatar_changed(self, username):
        self._avatar_cache.pop(username, None)
        for row in self.rows.values():
            if isinstance(row, MessageRow) and row.msg.get('sender') == username:
                row.refresh_avatar()
        self._refresh_header()

    def _on_settings_changed(self, key):
        if key in ('chat_wallpaper', 'room_wallpapers'):
            self._apply_styles()
            self.update()
        elif key in ('muted_rooms', 'pinned_rooms', 'room_alert_modes'):
            self._refresh_header()
        elif key in ('bubble_style', 'alert_keywords'):
            self.apply_theme()      # 내 말풍선 색/모서리, 키워드 강조를 다시 그린다
        elif key == 'hidden_messages':
            self._rebuild()

    def _on_attachment_loaded(self, aid, data, name, mime):
        card = self._pending_play.pop(aid, None)
        if card is not None:
            try:
                card.play(data)
            except RuntimeError:   # 행이 다시 만들어져 카드가 사라짐
                pass
        mw = self.max_bubble_width()
        for row in self.rows.values():
            att = (row.msg or {}).get('attachment') or {}
            if att.get('attachment_id') == aid and getattr(row, 'thumb', None) is not None:
                row.set_image(data, mw)
        if aid in self._pending_view:
            self._pending_view.discard(aid)
            exec_and_release(ImageViewer(data, name, self))
        if aid in self._pending_open:
            self._pending_open.discard(aid)
            self._open_received(aid, data, name)
        if aid in self._pending_save:
            self._pending_save.discard(aid)
            path, _ = QFileDialog.getSaveFileName(self, '파일 저장',
                                                  os.path.join(osenv.user_dir('downloads'), osenv.safe_filename(name)))
            if path:
                try:
                    with open(path, 'wb') as f:
                        f.write(data)
                    osenv.mark_from_network(path)
                    self.toast('저장했습니다.')
                except OSError as e:
                    self.toast(f'저장하지 못했습니다: {e}')

    def _open_received(self, aid, data, name):
        """받은 첨부 [열기]: 보기만 하는 형식(문서 · 사진 · 소리 · 영상)은 본인만 보는 폴더에 저장해 기본 프로그램으로 연다.
        그 밖의 형식은 메신저가 직접 열지 않는다 - 물어본 뒤 저장하고 폴더에서 보여 주기만 한다"""
        shown = osenv.safe_filename(name, f'file_{aid}')        # 다른 OS 에서 온 이름도
        direct = osenv.can_open_directly(shown)
        if not direct and not self.confirm(
                '파일 열기', f"'{shown}'\n이 형식은 메신저에서 바로 열지 않습니다.\n"
                             '저장한 뒤 폴더에서 보여 드릴까요?\n\n보낸 사람과 내용을 확인한 파일만 직접 열어 주세요.'):
            return
        try:
            path = osenv.save_opened_file(shown, data)
        except OSError as e:
            self.toast(f'파일을 열 수 없습니다: {e}')
            return
        if direct:
            osenv.open_path(path)
        else:
            osenv.reveal_path(path)

    def _on_scheduled_ok(self, p):
        if p.get('room_id') in (None, self.room_id):
            self.toast('예약되었습니다')

    def _on_error(self, message):
        if self.isVisible():
            self.toast(message)

    def _fit_title_bar(self):
        """macOS 별도 창: 회색 제목 표시줄 대신 헤더가 창 맨 위(신호등 버튼 뒤)까지 (로그인·메인 창과 같은 모양).
        분할 화면(메인 창 안)에 들어가 있으면 원래 여백. 창을 꺼내면 새 네이티브 창이 생기므로 그때마다 다시 적용"""
        from client.kakao import macos
        top = self.HEADER_TOP
        if self.isWindow():
            native = int(self.winId())
            if getattr(self, '_unified_native', None) == native or macos.unify_title_bar(self):
                self._unified_native = native
                top += macos.TITLE_BAR_HEIGHT
        margins = self.header.layout().contentsMargins()
        if margins.top() != top:
            self.header.layout().setContentsMargins(margins.left(), top, margins.right(), margins.bottom())

    def _maybe_mark_read(self):
        if self.isVisible() and self.isActiveWindow() and not self.isMinimized():
            self.state.mark_read(self.room_id)

    # ================= 창 이벤트 =================
    def showEvent(self, event):
        super().showEvent(event)
        self._fit_title_bar()
        self.state.open_rooms.add(self.room_id)
        self._sync_active_room()
        self._request_history()
        if not getattr(self, '_draft_restored', False):
            self._draft_restored = True
            self.restore_draft()
        self._relayout_timer.start()
        QTimer.singleShot(0, self._position_overlays)
        self.edit.setFocus()

    def hideEvent(self, event):
        self.state.active_rooms.discard(self.room_id)
        self.state.open_rooms.discard(self.room_id)
        self.save_draft()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.state.active_rooms.discard(self.room_id)
        self.state.open_rooms.discard(self.room_id)
        self._export_timer.stop()
        collector = getattr(self, '_export_collector', None)
        if collector is not None:            # 사진·파일을 모으던 대화 저장은 멈춘다
            collector.cancel()
            self._export_collector = None
            self._export_requested = False
        self.mention_popup.hide()
        self.save_draft()
        super().closeEvent(event)

    # ================= 입력 임시저장 (§10.8) =================
    def save_draft(self):
        """쓰다 만 글을 방별로 저장 (수정 중이면 수정 전 입력 내용).
        기다리던 자동 저장 타이머는 멈춘다 - 닫힌 창의 타이머가 늦게 울려 다른 창에서 지운 임시저장을
        옛 글로 되살리지 않게"""
        timer = self.__dict__.get('_draft_timer')
        if timer is not None:
            timer.stop()
        text = self._edit_draft if self._editing else self.edit.toPlainText()
        self.state.set_room_draft(self.room_id, text)

    def restore_draft(self):
        draft = self.state.room_draft(self.room_id)
        if draft and not self.edit.toPlainText():
            self.edit.setPlainText(draft)
            cursor = self.edit.textCursor()
            cursor.movePosition(cursor.End)
            self.edit.setTextCursor(cursor)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange:
            self._sync_active_room()
            if self.isActiveWindow():
                self._maybe_mark_read()

    def _is_foreground(self):
        """이 채팅방을 지금 보고 있는가: 창이 보이고, 활성 창이 이 창이거나 이 창에서 띄운 대화상자 · 메뉴일 때.
        열려 있어도 다른 창 · 다른 프로그램 뒤에 있으면 보고 있는 것이 아니다 (그때 온 메시지는 알림을 띄운다)"""
        if not self.isVisible() or self.isMinimized():
            return False
        mine = self.window()
        active = QApplication.activeWindow()
        while active is not None:
            if active is mine:
                return True
            active = active.parentWidget()
        return False

    def _sync_active_room(self):
        if self._is_foreground():
            self.state.active_rooms.add(self.room_id)
        else:
            self.state.active_rooms.discard(self.room_id)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlays()
        self._elide_title()
        self._refresh_notice()

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._relayout_timer.start()
            self._position_overlays()
        elif obj is self.search_edit and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.close_search()
            return True
        elif obj is getattr(self, 'edit', None) and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self.composer.setProperty('focused', event.type() == QEvent.FocusIn)   # 입력 중이면 테두리가 잉크색
            self.composer.style().unpolish(self.composer)
            self.composer.style().polish(self.composer)
        return super().eventFilter(obj, event)

    def _position_overlays(self):
        geo = self.scroll.geometry()
        if self.new_pill.isVisible():
            self.new_pill.adjustSize()
            w = min(self.new_pill.sizeHint().width(), geo.width() - 40)
            self.new_pill.setGeometry(geo.x() + (geo.width() - w) // 2, geo.bottom() - 46, w, 32)
            self.new_pill.raise_()
        if self.toast_label.isVisible():
            w = min(geo.width() - 40, max(160, self.toast_label.sizeHint().width()))
            h = self.toast_label.heightForWidth(w) if self.toast_label.heightForWidth(w) > 0 else 34
            self.toast_label.setGeometry(geo.x() + (geo.width() - w) // 2, geo.bottom() - h - 60, w, h)
            self.toast_label.raise_()
        if self.mention_popup.isVisible():
            n = min(5, self.mention_popup.count())
            h = n * 32 + 10
            ia = self.input_area.geometry()
            self.mention_popup.setGeometry(ia.x() + 10, ia.y() - h - 4, min(240, ia.width() - 20), h)
            self.mention_popup.raise_()

    def _on_escape(self):
        if self._kb_msg_id is not None:
            self.kb_clear()
        elif self.mention_popup.isVisible():
            self.mention_popup.hide()
        elif self._editing:
            self.cancel_edit()
        elif self.search_bar.isVisible():
            self.close_search()
        elif self._reply_to:
            self.cancel_reply()

    def _compose_escape(self):
        """입력창에서 Esc: 수정 → 답장 순으로 취소 (처리했으면 True)"""
        if self._editing:
            self.cancel_edit()
            return True
        if self._reply_to:
            self.cancel_reply()
            return True
        return False

    # ================= 오버레이 =================
    def toast(self, text, msec=2000):
        self.toast_label.setText(text)
        # 라이브 영역: 스크린리더가 토스트 내용을 바로 읽도록 알림 이벤트 (ui-styling: Toast = live region)
        self.toast_label.setAccessibleName(text)
        if QAccessible is not None:
            QAccessible.updateAccessibility(QAccessibleEvent(self.toast_label, QAccessible.Alert))
        self.toast_label.show()
        self._position_overlays()
        self._toast_timer.start(msec)

    def _show_new_pill(self, m):
        name = self.state.member_name(self.room_id, m.get('sender'))
        preview = self.state.message_preview(m).replace('\n', ' ')
        self.new_pill.ensurePolished()
        fm = QFontMetrics(self.new_pill.font())
        text = fm.elidedText(f'{name}: {preview}', Qt.ElideRight, max(80, self.width() - 120))
        self.new_pill.setText(f'{text}  ↓')
        self.new_pill.show()
        self._position_overlays()

    def confirm(self, title, text):
        """확인 대화상자 (테스트에서 교체 가능)"""
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    # ================= 입력 / 전송 =================
    def _on_text_changed(self):
        self.send_btn.setEnabled(bool(self.edit.toPlainText().strip()))
        self._update_mention_popup()
        if getattr(self, '_draft_restored', False):   # 입력이 멈추면 임시저장 (앱이 갑자기 꺼져도 남게)
            timer = self.__dict__.get('_draft_timer')
            if timer is None:
                timer = self._draft_timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(800)
                timer.timeout.connect(self.save_draft)
            timer.start()

    def send_current(self, silent=False):
        text = self.edit.toPlainText()
        if not text.strip():
            return
        if self.mention_popup.isVisible():
            self.mention_popup.hide()
        if self._editing:
            self._save_edit(text)
            return
        if self.is_secret():
            # 비밀채팅: 이 기기에서 암호화해서 보낸다 (조용히 보내기/답장 없음)
            err = self.state.send_secret(self.room_id, text)
            if err:
                self.toast(err, 2600)
                return
            self.edit.clear()
            self.scroll_to_bottom()
            return
        reply_id = self._reply_to.get('msg_id') if self._reply_to else None
        self.state.send_text(self.room_id, text, reply_to_id=reply_id, silent=silent)
        self.edit.clear()
        self.cancel_reply()
        self.scroll_to_bottom()
        if silent:
            self.toast('조용히 보냈습니다. 상대에게 알림이 울리지 않아요.', 1600)

    def send_silent(self):
        """조용히 보내기: 받는 사람에게 소리/알림 없이 전송 (수정 중이면 그냥 저장)"""
        self.send_current(silent=True)

    def build_send_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss())
        act = menu.addAction(make_icon('bell_off', theme.c('text_sub'), 16), '조용히 보내기', self.send_silent)
        act.setEnabled(bool(self.edit.toPlainText().strip()) and not self._editing and not self.is_secret())
        return menu

    TOOL_MENU = [('poll', '투표', 'poll_btn'), ('calendar', '일정 공유', 'event_btn'),
                 ('clock', '예약 메시지', 'schedule_btn'), ('ladder', '사다리 게임', 'ladder_btn'),
                 ('board', '톡게시판', 'board_btn')]

    def tools_menu(self):
        menu = QMenu(self)
        for icon, label, attr in self.TOOL_MENU:
            btn = getattr(self, attr)
            act = menu.addAction(label, btn.click)
            try:
                act.setIcon(btn.icon())
            except Exception:
                pass
        if not self.is_secret():
            menu.addSeparator()
            menu.addAction(QIcon(extra_icon_pixmap('mic', theme.c('text_sub'), 16)), '음성메시지',
                           lambda: self.open_voice_recorder())
            menu.addAction(make_icon('friends', theme.c('text_sub'), 16), '연락처 보내기',
                           lambda: self.open_contact_picker())
        return menu

    def _show_tools_menu(self):
        menu = self.tools_menu()
        menu.exec_(self.plus_btn.mapToGlobal(self.plus_btn.rect().topLeft()) - QPoint(0, menu.sizeHint().height()))

    def _show_send_menu(self, pos):
        self.build_send_menu().exec_(self.send_btn.mapToGlobal(pos))

    # ---- 메시지 수정
    def start_edit(self, m):
        if not m or not self.state.can_edit(m):
            return
        if not self._editing:
            self._edit_draft = self.edit.toPlainText()
        self.cancel_reply()
        self._editing = m
        self.edit_bar.show()
        elide(self.edit_preview, (m.get('content') or '').replace('\n', ' '), max(80, self.width() - 90))
        self.edit.setPlainText(m.get('content') or '')
        self.edit.moveCursor(QTextCursor.End)
        self.send_btn.setText('수정')
        self.edit.setFocus()

    def _save_edit(self, text):
        m = self._editing
        if text.strip() != (m.get('content') or '').strip():
            self.state.edit_message(m['msg_id'], text)
        self.cancel_edit()

    def cancel_edit(self):
        """수정 모드 종료: 수정 바를 숨기고 수정 전에 쓰던 내용을 되돌린다"""
        if not self._editing:
            return
        self._editing = None
        self.edit_bar.hide()
        self.send_btn.setText('전송')
        self.edit.setPlainText(self._edit_draft)
        self.edit.moveCursor(QTextCursor.End)
        self._edit_draft = ''
        self._on_text_changed()

    def start_reply(self, m):
        self.cancel_edit()
        self._reply_to = m
        self.reply_title.setText(f"{self.state.member_name(self.room_id, m.get('sender'))}에게 답장")
        preview = self.state.message_preview(m).replace('\n', ' ')
        self.reply_bar.show()
        elide(self.reply_preview, preview, max(80, self.width() - 80))
        self.edit.setFocus()

    def cancel_reply(self):
        self._reply_to = None
        self.reply_bar.hide()

    def react(self, msg_id, emoji):
        self.state.react(msg_id, emoji)

    # ---- 멘션 자동완성
    def _mention_prefix(self):
        cur = self.edit.textCursor()
        block_text = cur.block().text()[:cur.positionInBlock()]
        mo = re.search(r'@([^\s@]*)$', block_text)
        return mo.group(1) if mo else None

    def _update_mention_popup(self):
        room = self.state.room(self.room_id) or {}
        prefix = self._mention_prefix()
        if prefix is None or room.get('type') not in ('group', 'open'):
            self.mention_popup.hide()
            return
        cands = []
        for u in room.get('members', []):
            if u == self.state.me:
                continue
            name = self.state.member_name(self.room_id, u)
            if name.startswith(prefix) or u.startswith(prefix) or (prefix and prefix in name):
                cands.append((u, name))
        if not cands:
            self.mention_popup.hide()
            return
        self.mention_popup.clear()
        for u, name in cands:
            item = QListWidgetItem(QIcon(avatar_pixmap(u, name, 24)), name)
            item.setData(Qt.UserRole, name)
            self.mention_popup.addItem(item)
        self.mention_popup.setCurrentRow(0)
        self.mention_popup.show()
        self._position_overlays()

    def _mention_key(self, key):
        if not self.mention_popup.isVisible():
            return False
        row = self.mention_popup.currentRow()
        if key == Qt.Key_Down:
            self.mention_popup.setCurrentRow(min(self.mention_popup.count() - 1, row + 1))
            return True
        if key == Qt.Key_Up:
            self.mention_popup.setCurrentRow(max(0, row - 1))
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            item = self.mention_popup.currentItem()
            if item is not None:
                self._insert_mention(item.data(Qt.UserRole))
            return True
        if key == Qt.Key_Escape:
            self.mention_popup.hide()
            return True
        return False

    def _insert_mention(self, name):
        prefix = self._mention_prefix()
        cur = self.edit.textCursor()
        if prefix is not None:
            cur.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix) + 1)
        cur.insertText(f'@{name} ')
        self.edit.setTextCursor(cur)
        self.mention_popup.hide()
        self.edit.setFocus()

    # ---- 툴바
    def open_emoticons(self):
        popup = EmoticonPopup(self)
        popup.picked.connect(lambda e: self.state.send_emoticon(self.room_id, e))
        popup.adjustSize()
        pos = self.emoji_btn.mapToGlobal(self.emoji_btn.rect().topLeft())
        popup.move(pos.x(), pos.y() - popup.height() - 4)
        popup.show()
        self._emoticon_popup = popup
        return popup

    def _secret_blocked(self):
        if self.is_secret():
            self.toast('비밀채팅에서는 파일을 보낼 수 없습니다.')
            return True
        return False

    def pick_file(self):
        if self._secret_blocked():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, '보낼 파일 선택 (여러 개 선택 가능)')
        if paths:
            self.send_files(paths)

    def send_file(self, path):
        err = self.state.send_file(self.room_id, path)
        if err and err != FILE_BLOCKED:          # 보안 정책에 막힌 파일은 승인 요청 창이 따로 뜬다 (§10.10)
            self.toast(f'{os.path.basename(path)}: {err}')
        return err

    def send_files(self, paths):
        """여러 파일 전송. 실패한 파일은 토스트로 알리고 오류 목록을 돌려준다"""
        if self._secret_blocked():
            return ['비밀채팅에서는 파일을 보낼 수 없습니다.']
        errors = []
        blocked = 0
        for path in paths:
            err = self.state.send_file(self.room_id, path)
            if err == FILE_BLOCKED:
                blocked += 1
            elif err:
                errors.append(f'{os.path.basename(path)}: {err}')
        sent = len(paths) - len(errors) - blocked
        if errors:
            self.toast('\n'.join(errors[:3]) + (f'\n외 {len(errors) - 3}개' if len(errors) > 3 else ''), 3500)
        elif sent:
            self.toast(f'{sent}개 파일을 보냈습니다.' if sent > 1 else '파일을 보냈습니다.', 1500)
        if sent:
            self.scroll_to_bottom()
        return errors

    def send_image(self, image):
        """클립보드/드롭 이미지 → 임시 PNG로 저장 후 전송"""
        if isinstance(image, QPixmap):
            image = image.toImage()
        if image is None or image.isNull():
            self.toast('이미지를 읽을 수 없습니다.')
            return '이미지를 읽을 수 없습니다.'
        try:
            folder = osenv.opened_files_dir()     # 본인만 보는 폴더 (보낸 뒤 지운다)
        except OSError:
            self.toast('이미지를 저장하지 못했습니다.')
            return '이미지를 저장하지 못했습니다.'
        path = os.path.join(folder, f"사진_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
        if not image.save(path, 'PNG'):
            self.toast('이미지를 저장하지 못했습니다.')
            return '이미지를 저장하지 못했습니다.'
        try:
            err = self.send_file(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if not err:
            self.scroll_to_bottom()
        return err

    def handle_mime(self, mime):
        """드롭/붙여넣기 데이터 처리: 로컬 파일 → 파일 전송, 이미지 → 사진 전송"""
        if mime is None:
            return False
        if self.is_secret() and (mime.hasImage() or (mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()))):
            self._secret_blocked()
            return True
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            paths = [p for p in paths if os.path.isfile(p)]
            if paths:
                self.send_files(paths)
                return True
        if mime.hasImage():
            self.send_image(mime.imageData())
            return True
        return False

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasImage() or (mime.hasUrls() and any(u.isLocalFile() for u in mime.urls())):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if self.handle_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def open_poll_dialog(self):
        dlg = PollCreateDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            q, opts, multiple = dlg.values()
            if q and len(opts) >= 2:
                self.state.create_poll(self.room_id, q, opts, multiple)

    def open_event_dialog(self):
        try:
            from client.kakao.event_dialog import EventDialog
        except ImportError:
            self.toast('일정 기능을 불러올 수 없습니다.')
            return
        dlg = EventDialog(self.state, event=None, room_id=self.room_id, parent=self)
        if dlg.exec_() == QDialog.Accepted:      # 등록한 날짜를 알려 준다 (다른 해로 잘못 넣었으면 바로 보이게)
            start = dlg.start_end()[0]
            self.toast(f"일정을 등록했습니다 · {start:%Y. %m. %d.} ({'월화수목금토일'[start.weekday()]})")
        return dlg

    def open_schedule_dialog(self):
        dlg = ScheduleDialog(self.edit.toPlainText(), self)
        if dlg.exec_() == QDialog.Accepted:
            text = dlg.text.toPlainText().strip()
            when = dlg.when.dateTime().toPyDateTime()
            if not text:
                self.toast('메시지를 입력하세요.')
                return
            if when <= datetime.now():
                self.toast('미래 시각을 선택하세요.')
                return
            self.state.send_text(self.room_id, text, send_at=when)
            if text == self.edit.toPlainText().strip():
                self.edit.clear()

    # ================= 첨부 =================
    def request_thumb(self, aid):
        if aid in self._requested_thumbs:
            return
        self._requested_thumbs.add(aid)
        self.state.request_attachment(aid)

    def open_image(self, m):
        if not m:
            return
        att = m.get('attachment') or {}
        if att.get('expired'):
            self.toast(EXPIRED_TEXT)
            return
        aid = att.get('attachment_id')
        cached = self.state.attachments.get(aid)
        if cached:
            exec_and_release(ImageViewer(cached[0], cached[1], self))
        elif aid is not None:
            self._pending_view.add(aid)
            self.state.request_attachment(aid)

    def open_attachment(self, m):
        """첨부를 받아 임시 폴더에 저장한 뒤 기본 프로그램으로 연다"""
        if not m or m.get('deleted'):
            return
        if (m.get('attachment') or {}).get('expired'):
            self.toast(EXPIRED_TEXT)
            return
        aid = (m.get('attachment') or {}).get('attachment_id')
        if aid is None:
            return
        self._pending_open.add(aid)
        self.state.request_attachment(aid)

    def save_attachment(self, m):
        if not m or m.get('deleted'):
            return
        if (m.get('attachment') or {}).get('expired'):
            self.toast(EXPIRED_TEXT)
            return
        aid = (m.get('attachment') or {}).get('attachment_id')
        if aid is None:
            return
        self._pending_save.add(aid)
        self.state.request_attachment(aid)

    # ================= 컨텍스트 메뉴 =================
    def build_context_menu(self, m):
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss())
        if not m or m.get('kind') == 'system':
            return menu
        if m.get('failed'):
            return self.failed_menu(m, menu)
        deleted = bool(m.get('deleted'))
        kind = m.get('kind')
        secret = self.is_secret()
        if not deleted:
            if not secret:
                menu.addAction('답장', lambda: self.start_reply(m))
            if self.state.can_edit(m):
                menu.addAction('수정', lambda: self.start_edit(m))
            if not secret and m.get('thread_root') is None and not m.get('failed'):
                menu.addAction('스레드로 답장', lambda: self.open_thread(m))
            sub = menu.addMenu('공감')
            sub.setStyleSheet(menu.styleSheet() + f'QMenu {{ {emoji_css()} }}')
            mine = {e for e, users in (m.get('reactions') or {}).items() if self.state.me in users}
            for emo in REACTIONS:
                act = sub.addAction(f'{emo}  취소' if emo in mine else emo,
                                    lambda e=emo: self.react(m['msg_id'], e))
                act.setData(emo)
            sub.addSeparator()
            sub.addAction('더 많은 공감…', lambda: self.show_reaction_picker(m))
            if kind in ('text', 'emoticon') or (kind == 'secret' and m.get('secret_ok')):
                menu.addAction('복사', lambda: self.copy_message(m))
            if kind in ('text', 'emoticon', 'image', 'file', 'contact') and not secret:
                menu.addAction('전달', lambda: self.forward_message(m))
                menu.addAction('나에게 보내기', lambda: self.send_to_me(m))
            if not secret:
                menu.addAction('공지 등록', lambda: self.state.set_notice(self.room_id, m['msg_id']))
            if not m.get('failed'):
                if self.state.is_saved(self.room_id, m['msg_id']):
                    menu.addAction('책갈피 해제', lambda: self.state.unsave_message(self.room_id, m['msg_id']))
                else:
                    menu.addAction('책갈피', lambda: self._save_bookmark(m))
            if m.get('sender') and m.get('sender') != self.state.me:
                menu.addAction('프로필 보기', lambda: self.open_profile(m['sender']))
        dsub = menu.addMenu('삭제')
        if self.state.can_delete_for_all(m):
            dsub.addAction('모든 대화 상대에게서 삭제', lambda: self.delete_for_all(m))
        dsub.addAction('나에게서 삭제', lambda: self.state.hide_for_me(self.room_id, m['msg_id']))
        return menu

    def show_context_menu(self, m, global_pos):
        if m.get('kind') == 'system':
            return
        menu = self.build_context_menu(m)
        menu.exec_(global_pos)

    def copy_message(self, m):
        row = self.rows.get(m['msg_id'])
        text = row.selected_text() if isinstance(row, MessageRow) else ''
        body = m.get('secret_text', '') if m.get('kind') == 'secret' else m.get('content', '')
        QApplication.clipboard().setText(text or body)
        self.toast('복사했습니다.', 1200)

    def forward_message(self, m):
        dlg = ForwardDialog(self.state, self)
        if dlg.exec_() == QDialog.Accepted and dlg.room_id():
            self.state.forward(m, dlg.room_id())
            self.toast('전달했습니다.')

    def send_to_me(self, m):
        rid = self.state.direct_room_with(self.state.me)
        if rid:
            self.state.forward(m, rid)
            self.toast('나와의 채팅방으로 보냈습니다.')
        else:
            self._pending_self_forward.append(m)
            self.state.open_direct(self.state.me)

    def delete_for_all(self, m):
        if self.confirm('메시지 삭제', '모든 대화 상대에게서 이 메시지를 삭제할까요?'):
            self.state.delete_for_all(m['msg_id'])

    # ================= 방 메뉴 =================
    def build_room_menu(self):
        room = self.state.room(self.room_id) or {}
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss())
        if room.get('type') != 'secret':
            menu.addAction('대화상대 초대', self.open_invite_dialog)
        present = {a.text() for a in menu.actions()}
        for _btn, label, slot in getattr(self, 'overflowed', []):   # 헤더에서 밀려난 아이콘
            if label not in present:
                menu.addAction(label, slot)
        if self.members_strip is not None:
            shown = self.members_strip_visible()
            menu.addAction('대화상대 목록 접기' if shown else '대화상대 목록 펼치기',
                           lambda: self.set_members_strip(not shown))
        if room.get('type') == 'group':
            menu.addAction('채팅방 이름 변경', self.rename_room)
        bot_action = self._bot_menu_action(room)
        if bot_action:
            menu.addAction(*bot_action)
        if self.state.settings is not None:
            menu.addAction('배경화면 설정', self.open_wallpaper_dialog)
        if room.get('type') in ('direct', 'group', 'open', 'self'):
            menu.addAction('게시판', lambda: self.open_board())
        if room.get('type') != 'secret':
            menu.addAction('대화 요약', lambda: self.show_summary())
        menu.addAction('대화 내용 저장… (텍스트 · 엑셀 · 사진 포함)', lambda: self.show_export_dialog())
        if self.state.settings is not None:
            alert = menu.addMenu('알림 설정')
            alert.setStyleSheet(menu.styleSheet())
            group = QActionGroup(alert)
            group.setExclusive(True)
            current = self.state.room_alert_mode(self.room_id)
            for mode, text in (('all', '모든 메시지'), ('mention', '멘션만'), ('off', '끄기')):
                act = alert.addAction(text, lambda md=mode: self.set_alert_mode(md))
                act.setCheckable(True)
                act.setChecked(mode == current)
                act.setData(mode)
                group.addAction(act)
            menu.addAction('상단 고정 해제' if self.state.is_pinned(self.room_id) else '채팅방 상단 고정',
                           self.toggle_pin)
        menu.addSeparator()
        menu.addAction('채팅방 나가기', self.leave_room)
        return menu

    def _bot_menu_action(self, room):
        """방 메뉴의 AI 비서 항목 (text, slot) 또는 None
        그룹: 봇이 없으면 '부르기'(초대), 있으면 '질문'(@멘션 입력). 1:1(상대가 사람): '부르기'(새 그룹)"""
        rtype = room.get('type')
        members = room.get('members', [])
        if rtype == 'group':
            if BOT_NAME in members:
                return ('AI 비서에게 질문', self.ask_bot)
            return ('AI 비서 부르기', self.invite_bot)
        if rtype == 'direct' and BOT_NAME not in members and not any(self.state.is_bot(u) for u in members):
            return ('AI 비서 부르기', self.invite_bot)
        return None

    def invite_bot(self):
        self.state.invite(self.room_id, [BOT_NAME])
        self.toast(f'AI 비서를 초대했습니다. @{BOT_NAME} 로 불러보세요', 2600)

    def ask_bot(self):
        cur = self.edit.textCursor()
        if f'@{BOT_NAME}' not in self.edit.toPlainText():
            cur.insertText(f'@{BOT_NAME} ')
            self.edit.setTextCursor(cur)
        self.mention_popup.hide()
        self.edit.setFocus()

    def _show_room_menu(self):
        menu = self.build_room_menu()
        menu.exec_(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def make_invite_dialog(self):
        return InviteDialog(self.state, self.room_id, self)

    def open_invite_dialog(self):
        dlg = self.make_invite_dialog()
        if dlg.exec_() == QDialog.Accepted:
            self.invite(dlg.selected())

    def invite(self, usernames):
        """초대 실행. 1:1/나와의 채팅이면 새 그룹방이 만들어진다(True 반환)"""
        usernames = list(usernames or [])
        if not usernames:
            return False
        created = self.state.invite(self.room_id, usernames)
        names = ', '.join(self.state.display_name(u) for u in usernames)
        self.toast(f'{names}님과 새 그룹 채팅방을 만듭니다.' if created else f'{names}님을 초대했습니다.')
        return created

    def _save_bookmark(self, m):
        if self.state.save_message(m):
            self.toast('책갈피에 저장했습니다. 채팅 탭 ⋯ → 책갈피에서 볼 수 있습니다.')

    def show_unread_members(self, message):
        """읽음 숫자 → 안 읽은 사람 목록 팝업 (§10.6)"""
        popup = UnreadMembersPopup(self, message, self)
        popup.adjustSize()
        row = self.rows.get(message.get('msg_id'))
        anchor = row.unread_label if row is not None and hasattr(row, 'unread_label') else self
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        popup.move(pos)
        popup.show()
        return popup

    def members_strip_visible(self):
        """참여자 줄이 펼쳐져(사진·이름 칩이 보여) 있나"""
        return self.members_strip is not None and self.members_strip.expanded

    def _ensure_members_strip(self):
        """나와의 채팅을 뺀 모든 방에 참여자 줄(접힘)을 둔다. 펼침 여부는 방마다 설정에서 읽는다"""
        room = self.state.room(self.room_id) or {}
        if room.get('type') in (None, 'self'):
            return None
        if self.members_strip is None:
            self.members_strip = MembersStrip(self, self)
            self._root_layout.insertWidget(self._strip_index, self.members_strip)
            settings = self.state.settings
            opened = settings is not None and self.room_id in (settings.get('members_strip_open') or [])
            self.members_strip.set_expanded(opened)
        return self.members_strip

    def set_members_strip(self, expanded):
        """참여자 줄 펼치기/접기 - 방마다 기억한다"""
        strip = self._ensure_members_strip()
        if strip is None:
            return
        strip.set_expanded(expanded)
        settings = self.state.settings
        if settings is not None and (self.room_id in (settings.get('members_strip_open') or [])) != bool(expanded):
            settings.toggle_in('members_strip_open', self.room_id)

    def build_members_popup(self):
        return MembersPopup(self, self)

    def show_members_popup(self, anchor=None):
        popup = self.build_members_popup()
        popup.adjustSize()
        anchor = anchor if anchor is not None else self.count_label
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        popup.move(pos.x(), pos.y() + 4)
        popup.show()
        self._members_popup = popup

    def open_wallpaper_dialog(self):
        api = _wallpaper_api()
        if api is None or self.state.settings is None:
            self.toast('배경화면 설정을 불러올 수 없습니다.')
            return
        dlg = api.WallpaperDialog(self.state.settings, room_id=self.room_id, parent=self)
        dlg.exec_()
        self._apply_styles()
        self.update()

    def rename_room(self):
        name, ok = QInputDialog.getText(self, '채팅방 이름 변경', '새 이름', text=self.state.room_title(self.room_id))
        if ok and name.strip():
            self.state.rename_room(self.room_id, name.strip())

    def toggle_mute(self):
        if self.state.settings is not None:
            on = self.state.settings.toggle_in('muted_rooms', self.room_id)
            self.toast('알림을 껐습니다.' if on else '알림을 켰습니다.')

    def toggle_pin(self):
        if self.state.settings is not None:
            on = self.state.settings.toggle_in('pinned_rooms', self.room_id)
            self.toast('채팅방을 상단에 고정했습니다.' if on else '상단 고정을 해제했습니다.')

    def set_alert_mode(self, mode):
        """채팅방 알림 설정: 'all' 모든 메시지 / 'mention' 멘션만 / 'off' 끄기"""
        self.state.set_room_alert_mode(self.room_id, mode)
        self._refresh_header()
        self.toast({'all': '모든 메시지 알림을 받습니다.', 'mention': '멘션된 메시지만 알림을 받습니다.',
                    'off': '알림을 껐습니다.'}.get(mode, ''), 1500)

    def my_bubble_style(self):
        """말풍선 꾸미기 설정 → theme.bubble_style ('basic' | {'preset': ...} 둘 다 허용)"""
        value = self.state.settings.get('bubble_style') if self.state.settings is not None else None
        if isinstance(value, dict):
            value = value.get('preset')
        return theme.bubble_style(value)

    # ================= 통화 =================
    def _refresh_call_banner(self):
        call = self.state.call_for_room(self.room_id)
        if call is None:
            self.call_banner.hide()
            return
        parts = call.get('participants') or {}
        mode = call.get('mode') if call.get('mode') in CALL_MODES else 'voice'
        joined = self.state.me in parts
        self.call_banner_icon.setPixmap(extra_icon_pixmap(CALL_ICONS[mode], '#FFFFFF', 16))
        self.call_banner_label.setText(f'{CALL_MODES[mode]} · 통화 중 · {len(parts)}명')
        self.call_banner_btn.setText('돌아가기' if joined else '참여하기')
        self.call_banner.show()

    def _on_call_banner_clicked(self):
        call = self.state.call_for_room(self.room_id)
        if call is None:
            return
        if self.state.me in (call.get('participants') or {}):
            self.open_call_window(call['call_id'])
        else:
            self.join_call(call['call_id'])

    def _on_calls_changed(self):
        self._refresh_call_banner()
        call = self.state.call_for_room(self.room_id)
        if self._pending_call_start and call is not None and call.get('host') == self.state.me:
            self._pending_call_start = None
            self.open_call_window(call['call_id'])
        for m in [o for o in self._order if o.get('kind') == 'call']:
            if m['msg_id'] in self.rows:
                self._replace_row(m)

    def start_call(self, mode):
        """보이스톡/페이스톡/보이스룸 시작. 이미 통화 중이면 그 통화에 참여한다"""
        call = self.state.call_for_room(self.room_id)
        if call is not None:
            if self.state.me in (call.get('participants') or {}):
                self.open_call_window(call['call_id'])
            else:
                self.toast('이미 진행 중인 통화에 참여합니다.', 1500)
                self.join_call(call['call_id'])
            return
        self._pending_call_start = mode
        self.state.start_call(self.room_id, mode)

    def join_call(self, call_id):
        call = self.state.calls.get(call_id)
        if call is None:
            self.toast('종료된 통화입니다.')
            return
        if self.state.me not in (call.get('participants') or {}):
            self.state.join_call(call_id)
        self.open_call_window(call_id)

    def open_call_window(self, call_id):
        try:
            from client.kakao.call import show_call
        except ImportError:
            self.toast('통화 기능을 불러올 수 없습니다.')
            return None
        return show_call(self.state, call_id)

    # ================= 사다리 / 게시판 =================
    def make_ladder_dialog(self):
        return LadderDialog(self.state, self.room_id, self)

    def open_ladder_dialog(self):
        dlg = self.make_ladder_dialog()
        if dlg.exec_() == QDialog.Accepted and not dlg.validation_error():
            parts, results = dlg.values()
            self.state.create_ladder(self.room_id, parts, results)

    def open_board(self, post_id=None):
        dlg = BoardDialog(self.state, self.room_id, self, post_id=post_id)
        self._board_dialog = dlg
        dlg.exec_()
        return dlg

    def leave_room(self):
        if self.confirm('채팅방 나가기', '채팅방을 나가면 대화 내용이 모두 삭제됩니다. 나갈까요?'):
            self.state.leave_room(self.room_id)

    def open_thread(self, m):
        """스레드 창 (§10.4) - 원글마다 하나, 이미 열려 있으면 앞으로"""
        from client.kakao.thread_view import ThreadDialog
        root_id = m.get('thread_root') or m['msg_id']
        dialogs = self.__dict__.setdefault('_thread_dialogs', {})
        dlg = dialogs.get(root_id)
        try:
            if dlg is not None and dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return dlg
        except RuntimeError:   # 닫혀서 지워진 창
            pass
        dlg = ThreadDialog(self, root_id, self)
        dialogs[root_id] = dlg
        dlg.show()
        return dlg

    def kick_member(self, username):
        """방장: 대화상대 내보내기 (§10.3)"""
        name = self.state.display_name(username)
        if self.confirm('내보내기', f'{name}님을 이 채팅방에서 내보낼까요?'):
            self.state.kick_member(self.room_id, username)

    def transfer_owner(self, username):
        """방장: 방장 넘기기 (§10.3)"""
        name = self.state.display_name(username)
        if self.confirm('방장 넘기기', f'{name}님에게 방장을 넘길까요? 넘기면 내보내기·방장 넘기기를 할 수 없습니다.'):
            self.state.transfer_owner(self.room_id, username)

    def open_drawer(self):
        dlg = DrawerDialog(self, self)
        dlg.exec_()

    # ================= 검색 =================
    def toggle_search(self):
        if self.search_bar.isVisible():
            self.close_search()
        else:
            self.open_search()

    def open_search(self):
        self.search_bar.show()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def close_search(self):
        self.search_bar.hide()
        self.search_list.hide()
        self.search_list.clear()
        self._search_results = []
        self._search_idx = -1
        self._search_query = ''
        self.search_count.setText('')
        self.edit.setFocus()

    def _do_search(self):
        q = self.search_edit.text().strip()
        if not q:
            return
        self._search_query = q
        self.search_count.setText('검색 중…')
        self.state.search(q, self.room_id)

    def _on_search_results(self, query, messages):
        if not self.search_bar.isVisible() or (self._search_query and query != self._search_query):
            return
        results = [m for m in messages if m.get('room_id') == self.room_id]
        results.sort(key=lambda m: m['msg_id'], reverse=True)   # 최신 결과가 0번
        self._search_results = results
        self.search_list.clear()
        if not results:
            self.search_count.setText('0/0')
            self.search_list.hide()
            self.toast('검색 결과가 없습니다.')
            return
        for m in results:
            dt = parse_dt(m.get('timestamp'))
            when = f'{dt.month}/{dt.day} {fmt_time(dt)}' if dt else ''
            text = m.get('content', '').replace('\n', ' ')
            item = QListWidgetItem(f"{self.state.display_name(m.get('sender'))} · {when}\n{text}")
            item.setToolTip(m.get('content', ''))
            self.search_list.addItem(item)
        self.search_list.show()
        self._go_search(0)

    def _go_search(self, idx):
        if not self._search_results:
            return
        idx = max(0, min(len(self._search_results) - 1, idx))
        self._search_idx = idx
        self.search_count.setText(f'{idx + 1}/{len(self._search_results)}')
        self.search_list.blockSignals(True)
        self.search_list.setCurrentRow(idx)
        self.search_list.blockSignals(False)
        self.search_up.setEnabled(idx < len(self._search_results) - 1)
        self.search_down.setEnabled(idx > 0)
        found = self._search_results[idx]
        if found.get('thread_root') is not None:   # 아직 받지 않은 스레드 답글도 바로 스레드를 연다
            self.focus_message(found['msg_id'], hint=found)
        else:
            self.focus_message(found['msg_id'])

    def search_prev(self):
        """▲ 더 오래된 결과"""
        self._go_search(self._search_idx + 1)

    def search_next(self):
        """▼ 더 최근 결과"""
        self._go_search(self._search_idx - 1)

    # ================= v4: 카카오톡 2026 업데이트 (명세 §9) =================
    def open_profile(self, username):
        from client.kakao.friends_tab import ProfileDialog
        dlg = ProfileDialog(self.state, username, self)
        dlg.show()
        return dlg

    def open_contact_picker(self, exec_=True):
        """+ → 연락처 보내기"""
        dlg = ContactPickDialog(self.state, self)
        if exec_ and dlg.exec_() == QDialog.Accepted and dlg.username():
            self.state.send_contact(self.room_id, dlg.username())
        return dlg

    def open_voice_recorder(self, recorder=None, exec_=True):
        """+ → 음성메시지: 녹음 창을 띄우고, 보내기를 누르면 전송"""
        from client.kakao.voice import VoiceRecordDialog
        dlg = VoiceRecordDialog(self, recorder=recorder, autostart=exec_)
        dlg.setStyleSheet(dialog_qss())
        if exec_ and dlg.exec_() == QDialog.Accepted:
            self.send_voice_from(dlg)
        return dlg

    def send_voice_from(self, dlg):
        err = self.state.send_voice(self.room_id, dlg.wav_bytes(), dlg.seconds())
        if err:
            self.toast(err)
        return err

    def show_summary(self, unread_only=False, since_msg_id=None, modal=False):
        """대화 요약 요청 + 결과 창 (결과는 room_summary_ready 로 채워짐)"""
        if self.is_secret():
            self.toast('비밀채팅방은 내용이 암호화되어 요약할 수 없습니다.')
            return None
        self._summary_dlg().set_loading()
        self.state.request_summary(self.room_id, unread_only=unread_only, since_msg_id=since_msg_id)
        if modal:
            self._summary_dialog.exec_()
        else:
            self._summary_dialog.show()
            self._summary_dialog.raise_()
        return self._summary_dialog

    def _summarize_unread(self):
        self.summary_chip.hide()
        return self.show_summary(unread_only=True, since_msg_id=self._read_pointer_at_open)

    def _on_summary_ready(self, room_id, p):
        if room_id != self.room_id:
            return
        self._summary_dlg().set_result(p)

    def _summary_dlg(self):
        if self._summary_dialog is None:
            self._summary_dialog = SummaryDialog(self.state, self.room_id, self)
        return self._summary_dialog

    def show_export_dialog(self, exec_=True):
        """대화 내용 저장: 형식(텍스트/엑셀용 표/사진·파일 포함)과 기간을 고른 뒤 저장 위치를 묻는다 (명세 §9.5)"""
        from client.kakao.chat_export import ExportDialog
        dlg = ExportDialog(self.state.room_title(self.room_id), self)
        self.export_dialog = dlg
        if exec_ and dlg.exec_() == ExportDialog.Accepted:
            self.export_chat(**dlg.options())
        return dlg

    def export_chat(self, path=None, fmt='txt', since=None, until=None, period=''):
        """대화 저장: 서버에서 (기간의) 기록을 받아 fmt('txt'|'csv'|'zip')로 저장. path 가 없으면 받은 뒤 저장 창"""
        if self._export_requested:
            self.toast('대화 내용을 저장하는 중입니다. 잠시만 기다려 주세요.')
            return
        if not self.state.is_connected():
            self.toast('서버에 연결되어 있지 않아 대화 내용을 저장할 수 없습니다.')
            return
        self._export_path = path
        self._export_options = {'fmt': fmt, 'period': period}
        self._export_requested = True
        self._export_timer.start(self.EXPORT_TIMEOUT_MS)
        self.state.request_export(self.room_id, since=since, until=until)
        self.toast('대화 내용을 모으는 중입니다…', 1500)

    EXPORT_TIMEOUT_MS = 60000   # 서버가 기록을 보내지 않으면(오류·끊김) 저장 중 상태를 푼다

    def _export_timed_out(self):
        if self._export_requested and getattr(self, '_export_collector', None) is None:
            self._export_requested = False
            self.toast('서버가 응답하지 않아 대화 내용을 저장하지 못했습니다. 다시 시도해 주세요.')

    def default_export_name(self, fmt='txt'):
        from client.kakao.chat_export import default_file_name
        return default_file_name(self.state.room_title(self.room_id), fmt, datetime.now())

    def _on_export_ready(self, room_id, messages, truncated):
        if room_id != self.room_id or not self._export_requested:
            return
        self._export_timer.stop()
        from client.kakao.chat_export import format_info, ensure_extension, AttachmentCollector
        options = getattr(self, '_export_options', None) or {'fmt': 'txt', 'period': ''}
        fmt = options['fmt']
        path = self._export_path
        if path is None:
            start = os.path.join(osenv.user_dir('documents'), self.default_export_name(fmt))
            path, _ = QFileDialog.getSaveFileName(self, '대화 내용 저장', start, format_info(fmt)[3])
            if not path:
                self._export_requested = False
                return
            path = ensure_extension(path, fmt)
        if fmt == 'zip' and messages:
            collector = AttachmentCollector(self.state, messages, self)
            self._export_collector = collector
            collector.progress.connect(lambda done, total: total and self.toast(
                f'사진·파일을 받는 중입니다… {done}/{total}', 1500))
            collector.finished.connect(lambda files, notes: self._finish_export(
                path, fmt, messages, truncated, options['period'], files, notes))
            collector.start()
            return
        self._finish_export(path, fmt, messages, truncated, options['period'], {}, {})

    def _finish_export(self, path, fmt, messages, truncated, period, files, notes):
        from client.kakao.chat_export import write_export
        self._export_requested = False
        collector, self._export_collector = getattr(self, '_export_collector', None), None
        try:
            write_export(path, fmt, self.state, self.room_id, messages, period=period, files=files, notes=notes)
        except OSError as e:
            self.toast(f'저장하지 못했습니다: {e}')
            return
        finally:
            if collector is not None:
                collector.cleanup()           # 임시로 받아 둔 원본을 지운다
        note = ' (최근 2만 개까지)' if truncated else ''
        extra = f' · 사진·파일 {len(files)}개' if fmt == 'zip' else ''
        missing = f' · 빠진 첨부 {len(notes)}개' if notes else ''
        self.toast(f'대화 {len(messages)}개를 저장했습니다{extra}{missing}{note}.')
        self.last_export_path = path

    def show_reaction_picker(self, m, exec_=True):
        """공감 더 보기 (72종)"""
        reactions = m.get('reactions') or {}
        mine = {e for e, users in reactions.items() if self.state.me in users}
        dlg = ReactionPicker(mine, {e: len(u) for e, u in reactions.items()}, self)
        dlg.picked.connect(lambda e: self.react(m['msg_id'], e))
        if exec_:
            dlg.exec_()
        return dlg

    def focus_compose(self):
        self.kb_clear()
        self.edit.setFocus()

    # ================= 키보드로 메시지 고르기 (접근성) =================
    def _kb_rows(self):
        ids = [m['msg_id'] if isinstance(m, dict) else m for m in self._order]
        return [mid for mid in ids if isinstance(self.rows.get(mid), MessageRow)]

    def kb_select(self, step):
        """Alt+↑ 이전 / Alt+↓ 다음 메시지 선택 (처음엔 가장 최근 메시지). 입력 포커스는 그대로"""
        ids = self._kb_rows()
        if not ids:
            return None
        if self._kb_msg_id in ids:
            idx = max(0, min(len(ids) - 1, ids.index(self._kb_msg_id) + step))
        else:
            idx = len(ids) - 1
        old = self.rows.get(self._kb_msg_id)
        if isinstance(old, MessageRow):
            old.set_kb_selected(False)
        self._kb_msg_id = ids[idx]
        row = self.rows[self._kb_msg_id]
        row.set_kb_selected(True)
        self.scroll.ensureWidgetVisible(row, 0, 40)
        self.kb_status.setText(row.accessibleName())
        self.kb_status.setAccessibleName(row.accessibleName())
        return self._kb_msg_id

    def kb_clear(self):
        row = self.rows.get(self._kb_msg_id)
        if isinstance(row, MessageRow):
            row.set_kb_selected(False)
        self._kb_msg_id = None

    def kb_menu(self, exec_=True):
        """선택한 메시지(없으면 가장 최근 메시지)의 메뉴를 그 말풍선 옆에 연다"""
        if self._kb_msg_id is None and self.kb_select(0) is None:
            return None
        row = self.rows.get(self._kb_msg_id)
        if not isinstance(row, MessageRow):
            return None
        menu = self.build_context_menu(row.msg)
        if exec_:
            menu.exec_(row.content.mapToGlobal(QPoint(8, row.content.height() // 2)))
        return menu

    # ================= 보내지 못한 메시지 (shape 09) =================
    def failed_menu(self, m, menu=None):
        if menu is None:
            menu = QMenu(self)
            menu.setStyleSheet(menu_qss())
        menu.addAction('재전송', lambda: self.resend_failed(m))
        if m.get('kind') == 'text':
            menu.addAction('복사', lambda: self.copy_message(m))
        menu.addAction('삭제', lambda: self.state.delete_failed(self.room_id, m['msg_id']))
        return menu

    def resend_failed(self, m):
        err = self.state.resend_failed(self.room_id, m['msg_id'])
        if err:
            self.toast(err, 3000)
        return err
