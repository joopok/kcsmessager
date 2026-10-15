# -*- coding: utf-8 -*-
"""
조직도 탭 - KCS AI 지식봇(맨 위) → 그룹사(지주사 · 계열사가 같은 단계) → 그룹 → 게스트
- 서버 welcome 의 org(지주사·계열사 순서·조직 순서)가 있으면 그 구조를, 없으면 회사 → 부서 2단계로 보여준다.
- 그룹 관리자는 [그룹 만들기]로 회사와 상관없이 사람을 묶은 그룹을 만들고 고치고 지울 수 있다 (명세 §4.4).
- 모든 머리글은 접을 수 있다. 겸직자는 겸직 조직에도 '겸직' 표시와 함께 나온다.
- 구성원 더블클릭 → 1:1 채팅, 우클릭 → 1:1 채팅 / 채팅방에 초대 / 프로필 보기
"""

import re

from PyQt5.QtCore import Qt, QRectF, QPointF, QPoint, QEvent, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QPainter, QPainterPath, QPen, QPixmap, QIcon, QColor, QFontMetrics, QRegion
from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QMenu, QSizePolicy, QDialog, QLineEdit,
                             QListWidget, QListWidgetItem, QDialogButtonBox, QMessageBox, QApplication, QFrame,
                             QPushButton)

from client.kakao import theme
from client.kakao.widgets import Avatar, set_shown
from client.kakao.chats_tab import (ElidedLabel, RowList, TabHeader, make_search_edit, search_box_qss, InviteToRoomDialog,
                                    user_pixmap, presence_of, connect_avatar_changed, PRESENCE_LABELS,
                                    PRESENCE_COLORS)

GUEST_COMPANY = '게스트'
INDENT = 14          # 조직 단계별 들여쓰기(px)
TITLE_RANK = ['회장', '대표이사', '사장', '부사장', '전무', '상무이사', '상무', '이사', '실장', '본부장', '팀장',
              '부장', '차장', '과장', '수석', '책임', '대리', '선임', '주임', '매니져', '매니저', '연구', '사원', '봇']


# ================================================================ 건물 아이콘
def building_path():
    p = QPainterPath()
    p.addRoundedRect(QRectF(4.5, 3.5, 10, 17), 1.2, 1.2)
    p.moveTo(14.5, 9.5)
    p.lineTo(19.5, 9.5)
    p.lineTo(19.5, 20.5)
    p.lineTo(14.5, 20.5)
    for y in (7.5, 11.5, 15.5):
        p.moveTo(7.5, y)
        p.lineTo(8.5, y)
        p.moveTo(10.5, y)
        p.lineTo(11.5, y)
    p.moveTo(3, 20.5)
    p.lineTo(21, 20.5)
    return p


def people_path():
    """두 사람 아이콘 (조직도 그룹)"""
    p = QPainterPath()
    p.addEllipse(QRectF(5, 4.5, 6, 6))
    p.moveTo(2.5, 19.5)
    p.cubicTo(2.5, 15, 5, 13, 8, 13)
    p.cubicTo(11, 13, 13.5, 15, 13.5, 19.5)
    p.addEllipse(QRectF(14, 6, 5, 5))
    p.moveTo(15.5, 13.5)
    p.cubicTo(18.5, 13.5, 21, 15.5, 21, 19.5)
    return p


def paint_building(painter, rect, color, stroke=1.7, path=None):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path if path is not None else building_path())
    painter.restore()


def building_icon(color, size=24, kind='building'):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_building(p, QRectF(0, 0, size, size), color, path=people_path() if kind == 'people' else None)
    p.end()
    return QIcon(pm)


# ================================================================ 카드 디자인 (2026-10-10)
# 연회색 바닥(bg_alt) 위에 덩어리(KCS AI · 그룹사 · 그룹 · 게스트)마다 흰 카드. 카드 · 마우스 올림 · 선택 · 계층선은
# OrgList 가 그리고, 행 위젯은 배경을 칠하지 않는다.
CARD_INSET = 10      # 카드 좌우 여백
CARD_RADIUS = 10
TILE_X = CARD_INSET + 12             # 머리글 아이콘 타일 왼쪽 (모든 덩어리 같은 세로선)
TILE = 28
TITLE_X = TILE_X + TILE + 10         # 머리글 글자 시작 - 그 아래 구성원 사진도 여기서 시작
RAIL_X = TILE_X + TILE // 2          # 그룹사 계층선 x (그룹사 아이콘 타일 가운데)


def icon_tile(kind, accent=False, size=28):
    """둥근 네모 타일 안 아이콘. accent = 그룹사 · 내 회사(네이비 옅은 바탕 + 네이비 아이콘)"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    tint = QColor(theme.c('yellow'))
    tint.setAlpha(26)
    p.setPen(Qt.NoPen)
    p.setBrush(tint if accent else theme.qc('bg_alt'))
    p.drawRoundedRect(QRectF(0, 0, size, size), 8, 8)
    inner = size * 0.6
    paint_building(p, QRectF((size - inner) / 2, (size - inner) / 2, inner, inner),
                   theme.c('yellow') if accent else theme.c('text_sub'), stroke=1.8,
                   path=people_path() if kind == 'people' else None)
    p.end()
    return pm


def count_pill(text):
    """'14곳' · '8명' 개수 알약"""
    label = QLabel(text)
    label.setStyleSheet(f"color: {theme.c('text_sub')}; background: {theme.c('bg_alt')}; border-radius: 8px;"
                        f" padding: 1px 7px; font-size: {theme.fs('meta')}px; font-weight: bold;")
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    set_shown(label, bool(text))   # 부모 없는 상태에서 setVisible(True) 는 최상위 창으로 번쩍인다
    return label


class OrgList(RowList):
    """조직도 목록 - 덩어리 카드 · 계층선 · 마우스 올림/선택 강조를 행 뒤에 그린다"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_item = None

    def blocks(self):
        """[[item, ...]] - 구분 간격(divider) 사이 행 묶음"""
        out, current = [], []
        for item in self.items():
            data = item.data(Qt.UserRole) or ()
            if data[:1] == ('divider',):
                if current:
                    out.append(current)
                current = []
            else:
                current.append(item)
        if current:
            out.append(current)
        return out

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.viewport().rect(), theme.qc('bg_alt'))
        width = self.viewport().width()
        for block in self.blocks():
            if (block[0].data(Qt.UserRole) or ())[:1] == ('slot',):
                continue                                   # 끄는 중 빈칸은 점선만
            top = self.visualItemRect(block[0]).top()
            bottom = self.visualItemRect(block[-1]).bottom() + 1
            if bottom < 0 or top > self.viewport().height():
                continue
            card = QRectF(CARD_INSET + 0.5, top + 0.5, width - 2 * CARD_INSET - 1, bottom - top - 1)
            p.setPen(QPen(theme.qc('border'), 1))
            p.setBrush(theme.qc('bg'))
            p.drawRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
            first = block[0].data(Qt.UserRole) or ()
            if first == ('section', '그룹사'):
                self._paint_rails(p, block)
        for item, role in ((self.hover_item, 'bg_hover'), (self.currentItem(), 'bg_selected')):
            if item is None or self.row(item) < 0 or not (item.flags() & Qt.ItemIsEnabled):
                continue
            if role == 'bg_selected' and not item.isSelected():
                continue
            data = item.data(Qt.UserRole) or ()
            if data[:1] in (('divider',), ('slot',), ('empty',), ('section',)) and data != ('section', '그룹사'):
                continue
            r = QRectF(self.visualItemRect(item)).adjusted(CARD_INSET + 4, 2, -CARD_INSET - 4, -2)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qc(role))
            p.drawRoundedRect(r, 7, 7)
        p.end()
        super().paintEvent(event)

    def _paint_rails(self, p, block):
        """그룹사 계층선: 그룹사 아이콘 아래에서 각 계열사 머리글로 ├ 가지"""
        children = [it for it in block[1:] if (it.data(Qt.UserRole) or ())[:1] == ('company',)]
        if not children:
            return
        start = self.visualItemRect(block[0]).bottom() - 6
        ys = [self.visualItemRect(it).center().y() for it in children]
        pen = QPen(theme.qc('border'), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(RAIL_X, start), QPointF(RAIL_X, ys[-1] - 6))
        path = QPainterPath()                                   # 마지막 가지는 └ 둥글게
        path.moveTo(RAIL_X, ys[-1] - 6)
        path.quadTo(RAIL_X, ys[-1], RAIL_X + 6, ys[-1])
        path.lineTo(RAIL_X + 9, ys[-1])
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        for y in ys[:-1]:
            p.drawLine(QPointF(RAIL_X, y), QPointF(RAIL_X + 9, y))


# ================================================================ 행 위젯
class _Chevron(QWidget):
    def __init__(self, collapsed, parent=None, color=None):
        super().__init__(parent)
        self.collapsed = collapsed
        self.color = color
        self.setFixedSize(12, 12)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(self.color) if self.color else theme.qc('text_hint'), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        pts = [(4, 2.5), (8, 6), (4, 9.5)] if self.collapsed else [(2.5, 4), (6, 8), (9.5, 4)]
        p.drawPolyline(*[QPointF(x, y) for x, y in pts])


class CompanyHeader(QWidget):
    @staticmethod
    def title_left(depth):
        """회사 이름 글자가 시작하는 x (아래 직속 구성원 사진 · 부서 화살표를 여기에 맞춘다)"""
        return TILE_X + depth * 22 + (26 if depth else TILE) + 10

    def __init__(self, company, count, collapsed, mine=False, parent=None, depth=0, business='', chip=False,
                 icon_kind='building', grip=False):
        super().__init__(parent)
        self.company = company
        self.chip = None
        self.grip = None            # 점 모양 손잡이는 두지 않는다 (2026-10-10 요청) - 머리글 전체를 끈다
        self.movable = grip         # 그룹 관리자가 끌어서 순서를 바꿀 수 있는 머리글
        self.depth = depth
        row = QHBoxLayout(self)
        # 카드 안쪽: 들여쓰기 한 단계 = 그룹사 계층선 오른쪽 (RAIL_X + 가지)
        row.setContentsMargins(TILE_X + depth * 22, 9, CARD_INSET + 12, 9)
        row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(icon_tile(icon_kind, accent=mine, size=26 if depth else TILE))
        row.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = ElidedLabel(company, 'emphasis', 'text', bold=True)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top.addWidget(self.title)
        if mine:
            tag = QLabel('내 회사')
            tag.setStyleSheet(f"color: {theme.c('on_accent')}; background: {theme.c('yellow')}; border-radius: 4px;"
                              f" padding: 0px 5px; font-size: {theme.fs('micro')}px; font-weight: bold;")
            top.addWidget(tag)
        self.count = count_pill(f'{count}명' if count and not chip else '')
        top.addWidget(self.count)
        if chip:                    # 그룹웨어 체계 회사: '👤 52 ›' 칩을 누르면 구성원 목록
            self.chip = CountChip(count)
            top.addWidget(self.chip)
        top.addStretch(1)
        col.addLayout(top)
        if business:
            col.addWidget(ElidedLabel(business, 'caption', 'text_hint'))
        row.addLayout(col, 1)
        row.addWidget(_Chevron(collapsed))
        self.hovered = False

    def set_hovered(self, on):
        """끌 수 있는 머리글에 마우스를 올렸을 때 (강조 바탕은 OrgList 가, 손 모양 커서는 목록이)"""
        self.hovered = on


class DropSlotRow(QWidget):
    """끌고 있는 덩어리가 놓일 자리 - 강조색 점선 빈칸 (덩어리 머리글 높이)"""

    def __init__(self, height, parent=None):
        super().__init__(parent)
        self.setFixedHeight(max(24, height))

    def sizeHint(self):
        return QSize(1, self.height())     # 목록 항목 높이 = 빈칸 높이 (없으면 기본 20px 로 잡혀 아래 행을 덮는다)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = theme.qc('yellow')
        fill = QColor(accent)
        fill.setAlpha(22)
        pen = QPen(QColor(accent), 1.4, Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(8.5, 3.5, -8.5, -3.5), 8, 8)


class DragGhost(QLabel):
    """끌고 있는 머리글 - 들어 올린 카드처럼 그림자 · 테두리 · 왼쪽 강조색 띠를 두르고 커서를 따라온다.
    그림자는 그림 안에 직접 그린다 (QGraphicsDropShadowEffect 는 macOS 화면에서 카드를 검게 덮었다)"""

    INSET = 8          # 좌우로 들여 놓아 '들어 올린' 카드로 보이게
    SHADOW = 10        # 그림자 여백 - 카드는 그림 안에서 이만큼 안쪽

    def __init__(self, header, parent):
        super().__init__(parent)
        old = header.size()
        w, h = max(120, parent.width() - 2 * self.INSET), old.height()
        header.resize(w, h)                            # 목록 폭 안에 들어오게 다시 그린다
        dpr = parent.devicePixelRatioF() or 1.0
        m = self.SHADOW
        pm = QPixmap(int((w + 2 * m) * dpr), int((h + 2 * m) * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(m, 0, -1):                      # 아래로 조금 치우친 부드러운 그림자
            shade = QColor(0, 0, 0, int(26 * (1 - i / (m + 1)) ** 2))
            p.setBrush(shade)
            p.drawRoundedRect(QRectF(m - i, m - i + 4, w + 2 * i, h + 2 * i), 8 + i, 8 + i)
        card = QRectF(m, m, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(card, 8, 8)
        p.setClipPath(clip)
        p.fillRect(card, theme.qc('bg'))
        # grab() 대신 흰 카드 위에 직접 - 머리글은 배경을 칠하지 않아 macOS grab() 은 그 부분을 검게 채운다
        header.render(p, QPoint(m, m), QRegion(), QWidget.DrawChildren)
        header.resize(old)
        p.fillRect(QRectF(m, m, 3, h), theme.qc('yellow'))
        p.setClipping(False)
        p.setPen(QPen(theme.qc('border'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.end()
        self.setPixmap(pm)
        self.resize(w + 2 * m, h + 2 * m)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet('background: transparent;')

    def place(self, x, y):
        """카드 왼쪽 위가 (x, y) 에 오게 (그림자 여백 빼고)"""
        self.move(x - self.SHADOW, y - self.SHADOW)


class GroupSectionHeader(QWidget):
    """'그룹사' 머리글 - 지주사 · 계열사가 모두 이 아래 한 단계 들여쓰기로 (접기 가능)"""

    def __init__(self, title, count, collapsed, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(TILE_X, 11, CARD_INSET + 12, 11)
        row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(icon_tile('building', accent=True, size=TILE))
        row.addWidget(icon)
        self.title = ElidedLabel(title, 'title', 'text', bold=True)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.title)
        self.count = count_pill(f'{count}곳')
        row.addWidget(self.count)
        row.addStretch(1)
        row.addWidget(_Chevron(collapsed))


class UnitHeader(QWidget):
    """조직(부·본부·실·팀) 머리글 - 단계만큼 들여쓰기, 접기 가능"""

    def __init__(self, name, count, collapsed, depth=0, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(CARD_INSET + 12 + depth * 16, 8, CARD_INSET + 12, 4)
        row.setSpacing(6)
        row.addWidget(_Chevron(collapsed))
        self.title = ElidedLabel(name or '소속 없음', 13 if depth == 0 else 12, 'text' if depth == 0 else 'text_sub',
                                 bold=True)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.title)
        self.count = ElidedLabel(str(count), 'meta', 'text_hint')
        self.count.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.count.setFixedWidth(self.count.sizeHint().width())
        row.addWidget(self.count)
        row.addStretch(1)


DepartmentHeader = UnitHeader  # 이전 이름 호환


# ================================================================ 그룹웨어 스타일 (사내 조직도 화면과 동일한 구성)
class CountChip(QWidget):
    """'👤 52 ›' 인원 칩 - 누르면 구성원 목록"""

    clicked = pyqtSignal()

    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.count = count
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('구성원 보기')
        f = theme.font('meta', bold=True)
        w = QFontMetrics(f).horizontalAdvance(str(count))
        self.setFixedSize(w + 44, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qc('bg_selected'))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        sub = theme.qc('text_sub')
        # 사람 아이콘
        p.setBrush(sub)
        p.drawEllipse(QRectF(9, 5.5, 5.5, 5.5))
        path = QPainterPath()
        path.addRoundedRect(QRectF(7, 11.5, 9.5, 5.5), 2.5, 2.5)
        p.drawPath(path)
        p.setPen(theme.qc('text_sub'))
        p.setFont(theme.font('meta', bold=True))
        p.drawText(QRectF(20, 0, self.width() - 32, self.height()), Qt.AlignVCenter | Qt.AlignLeft, str(self.count))
        pen = QPen(theme.qc('text_hint'), 1.3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        x = self.width() - 10
        p.drawPolyline(QPointF(x - 2.5, 8), QPointF(x, 11), QPointF(x - 2.5, 14))

    def mousePressEvent(self, event):
        self.clicked.emit()


class TreeNodeRow(QWidget):
    """⌄/› + 이름 (+ 숫자 또는 인원 칩) 한 줄 - 들여쓰기 단계별"""

    LEFT, STEP, CHEVRON, GAP = 20, 22, 12, 8
    INK = 3         # 화살표 칸 왼쪽 끝 → 실제 › 획이 시작하는 곳

    @classmethod
    def row_left(cls, depth, base=None):
        """화살표 획이 시작하는 x - base(위 머리글 글자 시작)가 있으면 그 줄에서 한 단계씩"""
        return (cls.LEFT if base is None else base) + depth * cls.STEP

    @classmethod
    def text_left(cls, depth, base=None):
        """이름 글자가 시작하는 x (아래 구성원 행을 여기에 맞춘다)"""
        return cls.row_left(depth, base) - cls.INK + cls.CHEVRON + cls.GAP

    def __init__(self, name, count, collapsed, depth=0, chip=False, bold=True, parent=None, base=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(self.row_left(depth, base) - self.INK, 9, CARD_INSET + 12, 9)
        row.setSpacing(self.GAP)
        row.addWidget(_Chevron(collapsed))
        self.title = ElidedLabel(name, 'emphasis', 'text', bold=bold)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.title)
        self.chip = None
        if chip:
            self.chip = CountChip(count)
            row.addWidget(self.chip, 0, Qt.AlignVCenter)
        else:
            self.count = ElidedLabel(str(count), 'body', 'text_sub', bold=True)
            self.count.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.count.setFixedWidth(self.count.sizeHint().width())
            row.addWidget(self.count)
        row.addStretch(1)


def concurrent_tag(user):
    """겸직 표시: 이름 옆 작은 알약 (연한 네이비 바탕 + 네이비 글자). 마우스를 올리면 주소속"""
    tag = QLabel('겸직')
    tag.setObjectName('concurrentTag')
    tag.setAlignment(Qt.AlignCenter)
    tag.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    ink = theme.c('link') if theme.is_dark() else theme.c('yellow')   # 대비 4.5:1 이상 (다크: 네이비 2.8:1 이라 밝은 파랑)
    tag.setStyleSheet(f"QLabel#concurrentTag {{ color: {ink}; background: {theme.c('bg_selected')};"
                      f" border: none; border-radius: {theme.px(9)}px; padding: 2px 8px;"
                      f" font-size: {theme.fs('caption')}px; font-weight: bold; }}")
    home = ' › '.join(user.get('org_path') or []) or user.get('department') or ''
    tag.setToolTip(f"겸직 · 주소속: {home}" if home else '겸직')
    tag.setAccessibleName('겸직')
    return tag


class CompactMemberRow(QWidget):
    """아바타 + '이름 직위' 한 줄 (그룹웨어 조직도 구성원 행)"""

    def __init__(self, state, username, depth=0, concurrent_title=None, parent=None, left=None):
        super().__init__(parent)
        self.username = username
        self.concurrent_title = concurrent_title     # None = 주소속, 문자열 = 겸직 직위
        user = state.user(username)
        row = QHBoxLayout(self)
        row.setContentsMargins(26 + depth * 22 if left is None else left, 5, CARD_INSET + 12, 5)
        row.setSpacing(10)
        size = theme.px(theme.AVATAR_ROW)      # 모든 목록 같은 사진 크기 (2026-10-10)
        self.avatar = Avatar(size=size)
        self.avatar.setPixmap(user_pixmap(state, username, size, show_presence=not user.get('is_bot')))
        row.addWidget(self.avatar)
        title = concurrent_title if concurrent_title is not None else (user.get('title') or '')
        name = state.display_name(username) + (' (나)' if username == state.me else '')
        self.name = ElidedLabel(f'{name} {title}'.strip(), 'emphasis', 'text')
        self.tag = None
        if concurrent_title is not None:
            # '엄태식 실장 [겸직]' - 이름 바로 옆 작은 알약 (예전: 줄 끝에 줄 높이만큼 늘어난 테두리 상자)
            row.addWidget(self.name, 0, Qt.AlignVCenter)
            self.tag = concurrent_tag(user)
            row.addWidget(self.tag, 0, Qt.AlignVCenter)
            row.addStretch(1)
        else:
            row.addWidget(self.name, 1)
        tips = [x for x in (' › '.join(user.get('org_path') or []),
                            f"내선 {user['extension']}" if user.get('extension') else '',
                            user.get('mobile') or '', user.get('account') or '') if x]
        self.setToolTip('\n'.join(tips))


class MembersDialog(QDialog):
    """인원 칩을 누르면 뜨는 구성원 목록 (검색 · 더블클릭 1:1 채팅)"""

    def __init__(self, state, title, usernames, parent=None):
        super().__init__(parent)
        self.state = state
        self.usernames = list(usernames)
        self.setWindowTitle(f'{title} 구성원')
        self.setMinimumSize(340, 460)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 14, 0, 10)
        head = QLabel(f'{title}  {len(self.usernames)}명')
        head.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold; padding: 0 20px;")
        col.addWidget(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText('이름, 직위, 부서, 전화, 아이디')
        self.search.setStyleSheet(search_box_qss())
        wrap = QHBoxLayout()
        wrap.setContentsMargins(16, 8, 16, 8)
        wrap.addWidget(self.search)
        col.addLayout(wrap)
        self.list = RowList()
        col.addWidget(self.list, 1)
        self.search.textChanged.connect(self.refresh)
        self.list.row_activated.connect(self._open)
        self.refresh()

    def refresh(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for name in self.usernames:
            u = self.state.user(name)
            fields = [self.state.display_name(name), u.get('title'), ' '.join(u.get('org_path') or []),
                      u.get('extension'), u.get('mobile'), u.get('account')]
            if q and not any(q in (f or '').lower() for f in fields):
                continue
            self.list.add_row(CompactMemberRow(self.state, name), ('member', name))

    def rows(self):
        return [self.list.item(i).data(Qt.UserRole)[1] for i in range(self.list.count())]

    def _open(self, item):
        self.state.open_direct(item.data(Qt.UserRole)[1])
        self.accept()


class HintRow(QWidget):
    def __init__(self, text, depth=0, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(TITLE_X + depth * 22, 6, CARD_INSET + 12, 8)        # 머리글 글자 줄에서
        row.addWidget(ElidedLabel(text, 'meta', 'text_hint'))


class DividerRow(QWidget):
    """덩어리(지식봇 · 그룹사 · 그룹 하나하나 · 게스트) 사이 구분선"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)          # 카드 사이 간격 (바닥색이 보인다)

    def sizeHint(self):
        return QSize(1, 10)


class MemberRow(QWidget):
    def __init__(self, state, username, parent=None, depth=0, concurrent_title=None):
        super().__init__(parent)
        self.username = username
        user = state.user(username)
        row = QHBoxLayout(self)
        row.setContentsMargins(TITLE_X + depth * 22, 6, CARD_INSET + 12, 6)        # 사진이 머리글 글자 줄에서
        row.setSpacing(12)
        size = theme.px(theme.AVATAR_ROW)      # 모든 목록 같은 사진 크기 (2026-10-10)
        self.avatar = Avatar(size=size)
        self.avatar.setPixmap(user_pixmap(state, username, size, show_presence=not user.get('is_bot')))
        row.addWidget(self.avatar, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = state.display_name(username) + (' (나)' if username == state.me else '')
        self.name = ElidedLabel(name, 'emphasis', 'text', bold=True)
        top.addWidget(self.name)
        self.title = None
        title = concurrent_title if concurrent_title is not None else user.get('title')
        if title:
            self.title = ElidedLabel(title, 'meta', 'text_sub')
            self.title.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.title.setFixedWidth(self.title.sizeHint().width())
            top.addWidget(self.title)
        self.tag = None
        if concurrent_title is not None:
            self.tag = concurrent_tag(user)
            top.addWidget(self.tag, 0, Qt.AlignVCenter)
        presence = presence_of(state, username)
        if not user.get('is_bot') and presence in ('away', 'busy', 'meeting', 'vacation'):
            label = QLabel(PRESENCE_LABELS[presence])
            label.setStyleSheet(f"color: {PRESENCE_COLORS[presence]}; font-size: {theme.fs('caption')}px; font-weight: bold;")
            top.addWidget(label)
        top.addStretch(1)
        col.addLayout(top)
        self.status = None
        sub = ' · '.join(x for x in (user.get('status_message'),
                                     f"내선 {user['extension']}" if user.get('extension') else '') if x)
        if sub:
            self.status = ElidedLabel(sub, 'meta', 'text_hint')
            col.addWidget(self.status)
        col.addStretch(1)
        row.addLayout(col, 1)


# ================================================================ 그룹 만들기 · 수정 (명세 §4.4)
class GroupDialog(QDialog):
    """그룹 이름 · 설명 + 구성원 고르기 (검색, 체크). 회사와 상관없이 누구나 넣을 수 있다"""

    def __init__(self, state, group=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.group = group
        self.setWindowTitle('그룹 수정' if group else '그룹 만들기')
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 16, 20, 14)
        col.setSpacing(8)
        self.name_edit = QLineEdit((group or {}).get('name', ''))
        self.name_edit.setPlaceholderText('그룹 이름 (예: 경영위원회, 양자암호 TF)')
        self.name_edit.setMaxLength(40)
        self.name_edit.setAccessibleName('그룹 이름')
        self.desc_edit = QLineEdit((group or {}).get('description', ''))
        self.desc_edit.setPlaceholderText('설명 (선택)')
        self.desc_edit.setMaxLength(100)
        self.desc_edit.setAccessibleName('그룹 설명')
        self.search = QLineEdit()
        self.search.setPlaceholderText('구성원 검색: 이름, 회사, 부서, 직위')
        self.search.setAccessibleName('구성원 검색')
        self.search.setStyleSheet(search_box_qss())
        self.count_label = QLabel()
        self.list = QListWidget()
        self.list.setAccessibleName('그룹 구성원 선택')
        for widget in (QLabel('그룹 이름'), self.name_edit, QLabel('설명'), self.desc_edit,
                       QLabel('구성원'), self.search):
            col.addWidget(widget)
        col.addWidget(self.list, 1)
        col.addWidget(self.count_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText('저장' if group else '만들기')
        self.buttons.button(QDialogButtonBox.Cancel).setText('취소')
        col.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        chosen = set((group or {}).get('members') or [])
        users = [u for u in state.users.values() if not u.get('is_bot') and not u.get('removed')]
        users.sort(key=lambda u: (u.get('company') == '게스트', u.get('company') or '', state.display_name(u['username'])))
        for u in users:
            name = u['username']
            label = ' · '.join(x for x in (f"{state.display_name(name)} {u.get('title') or ''}".strip(),
                                           u.get('company'), u.get('department')) if x)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in chosen else Qt.Unchecked)
            self.list.addItem(item)
        self.search.textChanged.connect(self._filter)
        self.name_edit.textChanged.connect(self._update)
        self.list.itemChanged.connect(lambda _i: self._update())
        self._update()
        self.resize(theme.px(380), theme.px(560))

    def _items(self):
        return [self.list.item(i) for i in range(self.list.count())]

    def _filter(self, text):
        q = text.strip().lower()
        for item in self._items():
            item.setHidden(bool(q) and q not in item.text().lower())

    def _update(self):
        self.count_label.setText(f'선택한 구성원 {len(self.members())}명')
        self.ok_button.setEnabled(bool(self.name()))

    def set_checked(self, usernames):
        wanted = set(usernames)
        for item in self._items():
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in wanted else Qt.Unchecked)

    def name(self):
        return self.name_edit.text().strip()

    def description(self):
        return self.desc_edit.text().strip()

    def members(self):
        return [i.data(Qt.UserRole) for i in self._items() if i.checkState() == Qt.Checked]


# ================================================================ 조직도 탭
class OrgTab(QWidget):
    def __init__(self, state, settings, parent=None):
        super().__init__(parent)
        self.state = state
        self.settings = settings
        self.collapsed = set()          # 접힌 회사 이름
        self.collapsed_nodes = set()    # ('section', '그룹사') / ('unit', company, path_tuple)
        self.expanded_empty = set()     # 사용자가 펼친 빈 계열사
        self.expanded_depts = set()     # 그룹웨어 화면에서 펼친 부서 (기본: 모두 접힘)
        self.collapsed_teams = set()    # 그룹웨어 화면에서 접은 팀 (dept, team) (기본: 펼침)
        self.collapsed_groups = set()   # 접은 조직도 그룹 (group_id)
        self._defaulted = set()         # 기본 접힘(내 소속만 펼침)을 이미 정한 항목 - 그 뒤로는 사용자가 접고 편다
        self._defaulted_for = None
        self._drag = None              # 그룹 끌기 {'gid', 'start', 'active', 'target'}
        self._skip_click = False        # 끌기를 마친 놓기는 접기/펼치기로 치지 않는다
        self.bot_companies = set()      # 봇만 있는 회사(KCS AI) - 맨 위에
        self.tree = {}
        self._lower = []                # 아래쪽 덩어리 [(token, kind, 내용)]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = TabHeader('조직도')
        self.search_button = self.header.add_button('search', '이름, 부서, 직함 검색')
        self.group_button = self.header.add_button('plus', '그룹 만들기')     # 그룹 관리자만 (명세 §4.4)
        self.group_button.setAccessibleName('그룹 만들기')
        self.group_button.setVisible(False)
        self.edit_button = self.header.add_button('edit', '편집 모드 (구성원 정보 수정)')   # 관리자만
        self.edit_button.setAccessibleName('편집 모드')
        self.edit_button.setCheckable(True)
        self.edit_button.setVisible(False)
        self.edit_mode = False
        layout.addWidget(self.header)
        # 편집 모드 안내 줄: 켜져 있는 동안 헤더 아래에
        self.edit_bar = QFrame()
        self.edit_bar.setObjectName('orgEditBar')
        bar = QHBoxLayout(self.edit_bar)
        bar.setContentsMargins(16, 6, 10, 6)
        self.edit_label = QLabel('편집 모드 · 구성원을 누르면 정보를 고칠 수 있습니다')
        self.edit_label.setWordWrap(True)
        bar.addWidget(self.edit_label, 1)
        self.edit_done_button = QPushButton('끝내기')
        self.edit_done_button.setProperty('flat', True)
        self.edit_done_button.setCursor(Qt.PointingHandCursor)
        bar.addWidget(self.edit_done_button, 0, Qt.AlignVCenter)
        self.edit_bar.hide()
        layout.addWidget(self.edit_bar)
        self.search_edit = self.header.add_search(make_search_edit('이름, 직위, 직책, 직급, 부서, 전화, 아이디'))
        self.search_edit.setAccessibleName('조직도 검색')
        self.search_edit.show()
        self.list = OrgList()
        self.list.setViewportMargins(0, 8, 0, 8)
        layout.addWidget(self.list, 1)
        self.ghost = None                                  # 끌고 있는 머리글 카드 (DragGhost)
        self._hovered = None                               # 마우스를 올린 끌 수 있는 머리글
        self._autoscroll = QTimer(self)                    # 목록 위 · 아래 끝에서 끌면 저절로 스크롤
        self._autoscroll.setInterval(16)
        self._autoscroll.timeout.connect(self._autoscroll_tick)
        self.list.viewport().installEventFilter(self)
        self.list.installEventFilter(self)                 # 끄는 중 Esc → 취소
        self.empty_label = QLabel('검색 결과가 없습니다')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label, 1)

        self.search_button.clicked.connect(lambda: self.search_edit.setFocus())
        self.group_button.clicked.connect(lambda: self.create_group())
        self.edit_button.clicked.connect(lambda: self.set_edit_mode(not self.edit_mode))
        self.edit_done_button.clicked.connect(lambda: self.set_edit_mode(False))
        self.search_edit.textChanged.connect(lambda _t: self.refresh())
        self.list.row_activated.connect(self._activate)
        self.list.itemClicked.connect(self._clicked)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        state.users_changed.connect(self.refresh)
        state.user_changed.connect(lambda _u: self.refresh())
        if hasattr(state, 'org_changed'):
            state.org_changed.connect(self.refresh)
        connect_avatar_changed(state, self.refresh)
        self.apply_theme()

    # ---- 데이터 ----
    def _all_users(self):
        try:
            users = list(self.state.friends(include_bots=True))
        except TypeError:
            users = list(self.state.friends())
        if self.state.me:
            users.append(self.state.user(self.state.me))
        return users

    def _matches(self, user, query):
        if not query:
            return True
        query = query.lower()
        fields = [self.state.display_name(user['username']), user['username'], user.get('title'),
                  user.get('department'), user.get('company'), user.get('status_message'), user.get('extension'),
                  user.get('mobile'), (user.get('mobile') or '').replace('-', ''), user.get('account')]
        fields += list(user.get('org_path') or [])
        return any(query in (f or '').lower() for f in fields)

    def org(self):
        return getattr(self.state, 'org', None) or {}

    def org_meta(self, company):
        return self.company_meta().get(company, {})

    def company_meta(self):
        """그룹사 목록 {이름: 정보} - 지주사도 계열사와 같은 단계의 회사 하나로 (지주사 → 계열사 순서)"""
        org = self.org()
        meta = {}
        holding = org.get('holding') or {}
        if holding.get('name'):
            meta[holding['name']] = dict(holding, business=holding.get('business') or '지주회사')
        for c in org.get('companies', []):
            if c.get('name') and c['name'] not in meta:
                meta[c['name']] = c
        return meta

    def hidden_members(self):
        """조직도에서 숨길 계정(관리자). 관리자 본인으로 로그인했으면 숨기지 않고 전체를 보여 준다"""
        hidden = {n for c in self.org().get('companies', []) for n in (c.get('hidden_members') or [])}
        return set() if self.state.me in hidden else hidden

    @staticmethod
    def _member_key(user):
        title = user.get('title') or ''
        rank = TITLE_RANK.index(title) if title in TITLE_RANK else len(TITLE_RANK)
        return (rank, user.get('display_name') or user['username'])

    def _company_order(self, names):
        """봇 회사(KCS AI 지식봇) → 그룹사(지주사 · 계열사 순서) → 그 밖의 회사(내 회사 우선) → 게스트"""
        listed = [n for n in self.company_meta() if n not in self.bot_companies]
        mine = (self.state.user(self.state.me).get('company') or '').strip()
        bots = sorted(n for n in names if n in self.bot_companies)
        extra = sorted((n for n in names if n not in listed and n not in self.bot_companies),
                       key=lambda n: (n != mine, n == GUEST_COMPANY, n))
        return bots + listed + extra

    def build_tree(self, query=''):
        """{company: {'units': {path_tuple: [(username, concurrent_title|None)]}}} (순서 보장)"""
        raw = {}
        hidden = self.hidden_members()
        humans = set()
        companies = set()
        for user in self._all_users():
            company = (user.get('company') or GUEST_COMPANY).strip() or GUEST_COMPANY
            companies.add(company)
            if not user.get('is_bot'):
                humans.add(company)
        self.bot_companies = companies - humans
        for user in self._all_users():
            if user['username'] in hidden or not self._matches(user, query):
                continue
            company = (user.get('company') or GUEST_COMPANY).strip() or GUEST_COMPANY
            path = tuple(user.get('org_path') or ([user['department']] if user.get('department') else []))
            units = raw.setdefault(company, {})
            units.setdefault(path, []).append((user, None))
            for extra in user.get('concurrent') or []:
                extra_path = tuple(extra.get('org_path') or [])
                if extra_path and extra_path != path:
                    units.setdefault(extra_path, []).append((user, extra.get('title') or user.get('title') or ''))
        meta = self.company_meta()
        ordered = {}
        for company in self._company_order(set(raw)):
            units = raw.get(company, {})
            if query and not units:
                continue
            order = [tuple(u) for u in (meta.get(company, {}).get('units') or [])]

            def unit_key(path, order=order):
                # 조직 순서 목록에 있으면 그 순서, 없으면 뒤에 이름순. 소속 없음('')은 맨 뒤
                prefix_rank = []
                for depth in range(1, len(path) + 1):
                    node = path[:depth]
                    prefix_rank.append(order.index(node) if node in order else len(order) + 1)
                return (not path, prefix_rank, path)
            ordered[company] = {path: [(u['username'], ct) for u, ct in
                                       sorted(units[path], key=lambda x: (x[1] is not None, self._member_key(x[0])))]
                                for path in sorted(units, key=unit_key)}
        return ordered

    def companies(self):
        return list(self.tree)

    def members_of(self, company, department=None):
        """회사(또는 그 안의 조직: 마지막 단계 이름 또는 경로 튜플)의 주소속 구성원"""
        units = self.tree.get(company, {})
        names = []
        for path, members in units.items():
            if department is not None:
                target = tuple(department) if isinstance(department, (tuple, list)) else None
                if not (path == target or (target is None and path and path[-1] == department)
                        or (target is None and department == '' and not path)):
                    continue
            names += [u for u, ct in members if ct is None and u not in names]
        return names

    def _subtree_count(self, units, prefix):
        people = set()
        for path, members in units.items():
            if path[:len(prefix)] == prefix:
                people.update(u for u, _ct in members)
        return len(people)

    def refresh(self):
        if not self.state.me:
            return
        query = self.search_edit.text().strip()
        self.tree = self.build_tree(query)
        self.group_button.setVisible(self._can_manage_groups())
        self.edit_button.setVisible(self.state.is_admin())
        if self.edit_mode and not self.state.is_admin():   # 관리자 권한이 빠지면 편집 모드도 끝
            self.set_edit_mode(False)
        meta = self.company_meta()
        if not query:
            self._apply_default_collapse(meta)
        people = {u for units in self.tree.values() for ms in units.values() for u, _ in ms}
        if meta:
            self.header.set_subtitle(f'그룹사 {len(meta)}곳 · 구성원 {len(people)}명')
        else:
            self.header.set_subtitle(f'그룹사 {len(self.tree)}곳 · 구성원 {len(people)}명')
        scroll = self.list.verticalScrollBar().value()
        self.list.hover_item = None                    # 지울 항목을 가리키지 않게
        self.list.clear()
        mine = (self.state.user(self.state.me).get('company') or '').strip()
        # KCS AI 지식봇(봇만 있는 회사) → 그룹사(지주사 · 계열사가 모두 같은 단계) → 그룹 → 그 밖의 회사 · 게스트
        top = [c for c in self.tree if c in self.bot_companies]
        listed = [c for c in self.tree if c in meta and c not in self.bot_companies]
        rest = [c for c in self.tree if c not in top and c not in listed]
        for company in top:
            self._divider()
            self._render_company(company, self.tree[company], {}, query, mine)
        if listed:
            self._divider()
            # '그룹사' 머리글 아래 지주사 · 계열사 전부를 한 단계 들여쓰기로
            collapsed = ('section', '그룹사') in self.collapsed_nodes and not query
            self.list.add_row(GroupSectionHeader('그룹사', len(listed), collapsed), ('section', '그룹사'),
                              selectable=False)
            if not collapsed:
                for company in listed:
                    self._render_company(company, self.tree[company], meta[company], query, mine, depth=1)
        # 그 아래(만든 그룹 · 게스트 등)는 그룹 관리자가 끌어서 정한 순서 (KCS AI · 그룹사는 고정)
        self._lower = self.lower_blocks(rest, query)
        movable = self._can_manage_groups() and not query and len(self._lower) > 1
        drag = self._drag if self._drag and self._drag.get('active') else None
        blocks = self._lower
        if drag:                                       # 끄는 중: 미리 보기 순서, 끄는 덩어리 자리는 점선 빈칸
            by_token = {b[0]: b for b in self._lower}
            blocks = [by_token[t] for t in drag['preview'] if t in by_token]
        self._hovered = None
        self.list.hover_item = None
        for token, kind, payload in blocks:
            self._divider()
            if drag and token == drag['token']:
                self.list.add_row(DropSlotRow(drag['height']), ('slot', token), selectable=False)
                continue
            if kind == 'group':
                self._render_org_group(payload[0], payload[1], query, movable)
            else:
                self._render_company(payload, self.tree[payload], {}, query, mine, grip=movable)
        self._finish(scroll)

    def my_paths(self):
        """내 소속 {회사: {조직 경로 튜플}} - 주소속 + 겸직"""
        me = self.state.user(self.state.me)
        company = (me.get('company') or GUEST_COMPANY).strip() or GUEST_COMPANY
        paths = {tuple(me.get('org_path') or ([me['department']] if me.get('department') else []))}
        paths |= {tuple(x.get('org_path') or []) for x in me.get('concurrent') or []}
        return {company: {p for p in paths if p} | {()}}

    def _apply_default_collapse(self, meta):
        """처음 보는 회사 · 조직 · 그룹은 내가 속한 곳만 펼치고 나머지는 접는다.
        한 번 정한 뒤로는 사용자가 접고 편 상태를 그대로 둔다"""
        if self._defaulted_for != self.state.me:
            self._defaulted_for = self.state.me
            self._defaulted.clear()
        mine = self.my_paths()

        def first(key):
            if key in self._defaulted:
                return False
            self._defaulted.add(key)
            return True

        if first(('section', '그룹사')) and not any(c in meta for c in mine):
            self.collapsed_nodes.add(('section', '그룹사'))
        for company, units in self.tree.items():
            my_units = mine.get(company)
            if first(('company', company)) and my_units is None:
                self.collapsed.add(company)
            if my_units is None:
                continue                       # 다른 회사는 회사째 접혀 있다 - 펼치면 안은 그대로 보인다
            # 내 경로 위의 조직은 펼치고, 그 옆 조직(같은 상위 아래 다른 조직)만 접는다
            on_path = {p[:d] for p in my_units for d in range(0, len(p) + 1)}
            groupware = meta.get(company, {}).get('depth') == 1
            for path in units:
                for d in range(1, len(path) + 1):
                    node = path[:d]
                    if not first(('unit', company, node)):
                        continue
                    if node in on_path:
                        if groupware and d == 1:   # 그룹웨어 화면의 부서는 기본이 접힘이라 내 부서를 펼친다
                            self.expanded_depts.add(node[0])
                    elif node[:-1] in on_path:
                        if not groupware:
                            self.collapsed_nodes.add(('unit', company, node))
                        elif d == 2:               # 내 부서의 다른 팀
                            self.collapsed_teams.add(node)
        for group in self.org_groups():
            gid = group.get('group_id')
            if first(('org_group', gid)) and self.state.me not in (group.get('members') or []):
                self.collapsed_groups.add(gid)

    def lower_blocks(self, rest=None, query=''):
        """아래쪽 덩어리 [(token, 'group'|'company', 내용)] - 서버 order 순서, order 에 없는 것은 기본 순서로 뒤에"""
        if rest is None:
            meta = self.company_meta()
            rest = [c for c in self.tree if c not in self.bot_companies and c not in meta]
        blocks = []
        for group in self.org_groups():
            members = self.group_members(group, query)
            if not query or members:
                blocks.append((f"group:{group.get('group_id')}", 'group', (group, members)))
        blocks += [(f'company:{c}', 'company', c) for c in rest]
        order = [t for t in self.org().get('order') or [] if isinstance(t, str)]
        rank = {t: i for i, t in enumerate(order)}
        return sorted(blocks, key=lambda b: (b[0] not in rank, rank.get(b[0], 0)))

    def lower_tokens(self):
        """지금 확정된 아래쪽 덩어리 순서 (끄는 중 미리 보기가 아니라)"""
        return [b[0] for b in getattr(self, '_lower', [])]

    def _divider(self):
        """덩어리 사이 구분선 (맨 위에는 넣지 않는다)"""
        if self.list.count():
            self.list.add_row(DividerRow(), ('divider', self.list.count()), selectable=False)

    def _render_company(self, company, units, info, query, mine, depth=0, grip=False):
        if info.get('depth') == 1:
            # 그룹웨어 체계(depth=1)인 회사(케이씨에스)는 사내 조직도 화면 모양으로
            self._render_groupware(company, units, info, query, depth=depth)
            return
        count = self._subtree_count(units, ())
        # 구성원이 없는 계열사는 처음엔 접어 둔다(펼치면 안내 문구)
        if company not in self.expanded_empty and not units and company not in self.collapsed:
            self.collapsed.add(company)
        collapsed = (company in self.collapsed) and not query
        header = CompanyHeader(company, count, collapsed, mine=(company == mine), depth=depth,
                               business=info.get('business', ''), grip=grip)
        # 계열사 정보(사업 · 홈페이지 · 대표전화) - org.json, 한국지주 사업보고서 기준
        header.setToolTip('\n'.join(x for x in (company, info.get('business'), info.get('homepage'),
                                                 f"대표전화 {info['phone']}" if info.get('phone') else '') if x))
        if grip:
            header.setToolTip(header.toolTip() + '\n끌어서 순서를 바꿀 수 있습니다')
        item = self.list.add_row(header, ('company', company), selectable=False)
        if grip:
            self._make_draggable(item, header)
        if collapsed:
            return
        if not units:
            self.list.add_row(HintRow('등록된 구성원이 없습니다', depth + 1), ('empty', company),
                              selectable=False)
            return
        if company in self.bot_companies:
            # KCS AI: 'KCS AI 1명' 바로 아래 봇 (지식봇 같은 부서 단계 없이)
            for members in units.values():
                for name, _ct in members:
                    self.list.add_row(MemberRow(self.state, name, depth=depth), ('member', name))
            return
        shown = set()
        for path, members in units.items():
            # 이 경로의 상위 조직 머리글(아직 안 나온 것)부터
            hidden = False
            for d in range(1, len(path) + 1):
                node = path[:d]
                key = ('unit', company, node)
                if any(key[2][:k] in self._collapsed_units(company) for k in range(1, d)):
                    hidden = True
                    break
                if node not in shown:
                    shown.add(node)
                    unit_collapsed = node in self._collapsed_units(company) and not query
                    self.list.add_row(UnitHeader(node[-1], self._subtree_count(units, node), unit_collapsed,
                                                 depth=depth + d - 1), key, selectable=False)
                if node in self._collapsed_units(company) and not query:
                    hidden = True
                    break
            if hidden:
                continue
            if not path and len(units) > 1:
                self.list.add_row(UnitHeader('', len(members), False, depth=depth), ('unit', company, ()),
                                  selectable=False)
            for name, concurrent_title in members:
                self.list.add_row(MemberRow(self.state, name, depth=depth + len(path),
                                            concurrent_title=concurrent_title), ('member', name))

    # ---- 조직도 그룹 (명세 §4.4) ----
    def _can_manage_groups(self):
        check = getattr(self.state, 'is_group_admin', None)
        return bool(check and check())

    def org_groups(self):
        return [g for g in self.org().get('groups') or [] if g.get('name')]

    def group_members(self, group, query=''):
        """그룹 구성원 (삭제된 사람 · 숨긴 관리자 계정 제외, 직위 → 이름 순). 검색어가 그룹 이름과 맞으면 모두"""
        hidden = self.hidden_members()
        known = set(self.state.users) | {self.state.me}
        names = [n for n in dict.fromkeys(group.get('members') or []) if n in known and n not in hidden]
        if query and query.lower() not in group['name'].lower():
            names = [n for n in names if self._matches(self.state.user(n), query)]
        return sorted(names, key=lambda n: self._member_key(self.state.user(n)))

    def _render_org_group(self, group, members, query, movable):
        gid = group.get('group_id')
        collapsed = gid in self.collapsed_groups and not query
        header = CompanyHeader(group['name'], len(members), collapsed, business=group.get('description', ''),
                               icon_kind='people', grip=movable)
        header.setToolTip('\n'.join(x for x in (group['name'], group.get('description'), f'구성원 {len(members)}명',
                                                 '끌어서 순서를 바꿀 수 있습니다' if movable else '') if x))
        item = self.list.add_row(header, ('org_group', gid), selectable=False)
        if movable:
            self._make_draggable(item, header)
        if collapsed:
            return
        if not members:
            self.list.add_row(HintRow('구성원이 없습니다', 0), ('empty', ('org_group', gid)), selectable=False)
        for name in members:
            self.list.add_row(MemberRow(self.state, name, depth=0), ('member', name))

    # ---- 아래쪽 덩어리(그룹 · 게스트) 순서 바꾸기: 머리글을 끌어서 놓기 / 우클릭 → 위로 · 아래로 이동 ----
    # KCS AI · 그룹사는 고정. 그룹 관리자만 바꿀 수 있고 서버도 다시 검사한다 (명세 §4.4)
    def group_ids(self):
        return [g.get('group_id') for g in self.org_groups()]

    @staticmethod
    def token_of(data):
        """행 데이터 → 순서 토큰 ('group:1' / 'company:게스트'), 옮길 수 없는 행이면 None"""
        if data and data[0] == 'org_group':
            return f'group:{data[1]}'
        if data and data[0] == 'company':
            return f'company:{data[1]}'
        return None

    def can_move(self, token):
        return (self._can_manage_groups() and not self.search_edit.text().strip()
                and token in self.lower_tokens() and len(self.lower_tokens()) > 1)

    def move_block(self, token, index):
        """덩어리를 index 자리(0부터, 옮기기 전 목록 기준 '이 자리 앞')로. 바뀌었으면 True"""
        tokens = self.lower_tokens()
        if not self.can_move(token):
            return False
        order = [t for t in tokens if t != token]
        index -= sum(1 for t in tokens[:index] if t == token)
        order.insert(max(0, min(index, len(order))), token)
        if order == tokens:
            return False
        self.state.admin_org_order(order)
        return True

    def move_block_by(self, token, step):
        tokens = self.lower_tokens()
        if token not in tokens:
            return False
        pos = tokens.index(token)
        return self.move_block(token, pos + (step + 1 if step > 0 else step))

    def move_group(self, group_id, index):
        return self.move_block(f'group:{group_id}', index)

    def move_group_by(self, group_id, step):
        return self.move_block_by(f'group:{group_id}', step)

    def _group_blocks(self):
        """화면의 아래쪽 덩어리 [(token, 위 y, 아래 y)] - 머리글(또는 점선 빈칸)부터 구분선 전 마지막 행까지"""
        lower = set(self.lower_tokens())
        blocks, current = [], None
        for item in self.list.items():
            data = item.data(Qt.UserRole) or ()
            rect = self.list.visualItemRect(item)
            token = data[1] if data[:1] == ('slot',) else self.token_of(data)
            if token in lower:
                current = [token, rect.top(), rect.bottom()]
                blocks.append(current)
            elif current is not None and data[:1] != ('divider',):
                current[2] = rect.bottom()
            else:
                current = None
        return [tuple(b) for b in blocks]

    @staticmethod
    def _make_draggable(item, header):
        """끌 수 있는 머리글: 마우스를 행 위젯이 아니라 목록(뷰포트)이 받게 한다.
        행 위젯이 누름을 받으면 Qt 는 이어지는 이동을 그 위젯에 보내므로 목록이 끌기를 놓칠 수 있다.
        툴팁은 목록 항목이 대신 보여 준다"""
        item.setToolTip(header.toolTip())
        header.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _preview_order(self, y):
        """커서 y → 미리 보기 순서. 끄는 카드가 이웃과 절반 넘게 겹치면 이웃이 비켜 선다
        (위 이웃: 카드 윗변이 이웃 가운데보다 위로 / 아래 이웃: 카드 아랫변이 이웃 가운데보다 아래로)"""
        drag = self._drag
        card_top = y - drag['grab_dy']
        card_bottom = card_top + drag['height']
        blocks = self._group_blocks()
        slot = next((i for i, b in enumerate(blocks) if b[0] == drag['token']), len(blocks))
        index = 0
        for i, (token, top, bottom) in enumerate(blocks):
            if token == drag['token']:
                continue
            mid = (top + bottom) / 2
            if (i < slot and mid <= card_top) or (i > slot and mid < card_bottom):
                index += 1
        order = [b[0] for b in blocks if b[0] != drag['token']]
        order.insert(index, drag['token'])
        return order

    def _header_item(self, token):
        kind, _, value = token.partition(':')
        return self.list.find_data(('org_group', int(value)) if kind == 'group' else ('company', value))

    def _start_drag(self, pos):
        drag = self._drag
        item = self._header_item(drag['token'])
        header = self.list.itemWidget(item) if item is not None else None
        if header is None:
            self._drag = None
            return
        rect = self.list.visualItemRect(item)
        # 잡은 지점은 '누른 곳' 기준 (끌기가 시작된 곳이 아니라) - 카드가 커서 아래에서 튀지 않게
        drag.update(active=True, preview=self.lower_tokens(), height=rect.height(),
                    grab_dy=drag['start'].y() - rect.top())
        self._set_hovered(None)
        self.ghost = DragGhost(header, self.list.viewport())
        self.list.viewport().setCursor(Qt.ClosedHandCursor)
        self.refresh()                                 # 원래 자리를 점선 빈칸으로
        self.ghost.show()
        self._move_drag(pos)
        self._autoscroll.start()

    def _move_drag(self, pos):
        drag = self._drag
        drag['pos'] = pos
        vp = self.list.viewport()
        top = max(-drag['height'] // 2, min(pos.y() - drag['grab_dy'], vp.height() - drag['height'] // 2))
        self.ghost.place(DragGhost.INSET, top)         # 좌우로 들여 놓은 카드 - 들어 올린 느낌
        self.ghost.raise_()
        preview = self._preview_order(pos.y())
        if preview != drag['preview']:
            drag['preview'] = preview
            self.refresh()                             # 다른 덩어리가 비켜 선다
            self.ghost.raise_()

    def _autoscroll_tick(self):
        drag = self._drag
        if not drag or not drag.get('active') or 'pos' not in drag:
            self._autoscroll.stop()
            return
        y, h, edge = drag['pos'].y(), self.list.viewport().height(), 36
        speed = -(edge - y) if y < edge else (y - (h - edge)) if y > h - edge else 0
        if speed:
            bar = self.list.verticalScrollBar()
            before = bar.value()
            bar.setValue(before + max(-18, min(18, speed // 2 or (1 if speed > 0 else -1))))
            if bar.value() != before:
                self._move_drag(drag['pos'])

    def _end_drag(self, commit=True):
        drag, self._drag = self._drag, None
        self._autoscroll.stop()
        if self.ghost is not None:
            self.ghost.hide()
            self.ghost.deleteLater()
            self.ghost = None
        self.list.viewport().unsetCursor()
        if not drag or not drag.get('active'):
            return
        # 같은 머리글 위에서 놓으면 바로 뒤따르는 클릭(접기)을 무시. 다른 곳에 놓으면 클릭이 오지 않으므로
        # 이 놓기 처리가 끝나면 표시를 지운다 (다음 진짜 클릭을 먹지 않게)
        self._skip_click = True
        QTimer.singleShot(0, lambda: setattr(self, '_skip_click', False))
        if commit and drag['preview'] != self.lower_tokens() and self._can_manage_groups():
            self.state.admin_org_order(drag['preview'])   # 화면은 바로, 서버가 모두에게 확정
        else:
            self.refresh()                                 # 그대로거나 취소 → 원래 모양으로

    def _set_hovered(self, item):
        widget = self.list.itemWidget(item) if item is not None else None
        if widget is self._hovered:
            return
        for w in (self._hovered, widget):
            if w is not None and hasattr(w, 'set_hovered'):
                w.set_hovered(w is widget)
        self._hovered = widget

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self.list:
            if kind == QEvent.KeyPress and event.key() == Qt.Key_Escape and self._drag and self._drag.get('active'):
                self._end_drag(commit=False)               # Esc → 끌기 취소
                return True
            return False
        if obj is not self.list.viewport():
            return False
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            item = self.list.itemAt(event.pos())
            token = self.token_of(item.data(Qt.UserRole) if item else None)
            self._drag = None
            if token and self.can_move(token):          # KCS AI · 그룹사 · 계열사는 끌 수 없다
                self._drag = {'token': token, 'start': event.pos(), 'active': False}
        elif kind == QEvent.MouseMove and not self._drag and not event.buttons():
            item = self.list.itemAt(event.pos())
            if item is not self.list.hover_item:         # 행 마우스 올림 강조 (카드 안 둥근 바탕)
                self.list.hover_item = item
                self.list.viewport().update()
            if item is not None and self.can_move(self.token_of(item.data(Qt.UserRole))):   # 손 모양 커서
                self.list.viewport().setCursor(Qt.OpenHandCursor)
                self._set_hovered(item)
            else:
                self.list.viewport().unsetCursor()
                self._set_hovered(None)
        elif kind == QEvent.Leave:
            self._set_hovered(None)
            self.list.hover_item = None
            self.list.viewport().update()
        elif kind == QEvent.MouseMove and self._drag and event.buttons() & Qt.LeftButton:
            if not self._drag['active']:
                if (event.pos() - self._drag['start']).manhattanLength() < QApplication.startDragDistance():
                    return False
                self._start_drag(event.pos())
            elif self._drag:
                self._move_drag(event.pos())
            return True
        elif kind == QEvent.MouseButtonRelease and self._drag:
            self._end_drag()
        return False

    def find_group(self, group_id):
        return next((g for g in self.org_groups() if g.get('group_id') == group_id), None)

    def create_group(self, exec_=True):
        """그룹 만들기 (그룹 관리자)"""
        if not self._can_manage_groups():
            return None
        dlg = GroupDialog(self.state, parent=self.window())
        if exec_ and dlg.exec_() == QDialog.Accepted:
            self.state.admin_group_create(dlg.name(), dlg.description(), dlg.members())
        return dlg

    def edit_group(self, group_id, exec_=True):
        group = self.find_group(group_id)
        if group is None or not self._can_manage_groups():
            return None
        dlg = GroupDialog(self.state, group, parent=self.window())
        if exec_ and dlg.exec_() == QDialog.Accepted:
            self.state.admin_group_update(group_id, dlg.name(), dlg.description(), dlg.members())
        return dlg

    def delete_group(self, group_id, confirm=True):
        group = self.find_group(group_id)
        if group is None or not self._can_manage_groups():
            return False
        if confirm and QMessageBox.question(
                self, '그룹 삭제', f"'{group['name']}' 그룹을 삭제할까요?\n구성원 계정은 그대로 남습니다.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        self.state.admin_group_delete(group_id)
        return True

    def _render_groupware(self, company, units, info, query, depth=1):
        """케이씨에스 [👤52 ›] › 직속 구성원 + 부서(접힘, 인원 칩) - 그룹사 아래 계열사 자리에"""
        group = info.get('group_name') or company
        members = {}   # 부서 → [(username, 겸직직위|None)]
        for path, ms in units.items():
            dept = path[0] if path else ''
            for name, ct in ms:
                if all(name != n for n, _ in members.setdefault(dept, [])):
                    members[dept].append((name, ct))
        everyone = list(dict.fromkeys(n for ms in members.values() for n, ct in ms if ct is None))
        if info.get('count_excludes_root'):
            # 그룹웨어처럼 회사 인원은 부서에 속한 사람 수 (대표이사·관리자 계정 같은 직속 계정 제외)
            in_depts = {n for d, ms in members.items() if d for n, _ in ms}
            everyone = [n for n in everyone if n in in_depts]
        company_collapsed = company in self.collapsed and not query
        # 다른 계열사와 같은 회사 머리글(건물 아이콘 · 사업 설명) + 인원 칩
        mine = (self.state.user(self.state.me).get('company') or '').strip() == company
        row = CompanyHeader(group, len(everyone), company_collapsed, mine=mine, depth=depth,
                            business=info.get('business', ''), chip=True)
        row.setToolTip('\n'.join(x for x in (group, info.get('business'), info.get('homepage'),
                                               f"대표전화 {info['phone']}" if info.get('phone') else '') if x))
        row.chip.clicked.connect(lambda: self.show_members(group, everyone))
        self.list.add_row(row, ('company', company), selectable=False)
        if company_collapsed:
            return
        root_order = info.get('root_members') or []
        root = sorted(members.get('', []), key=lambda x: (root_order.index(x[0]) if x[0] in root_order
                                                          else len(root_order), x[0]))
        # 직속 구성원 사진 · 부서 화살표는 회사 이름 글자 시작 줄에 맞춘다 (2026-10-10 요청)
        base = CompanyHeader.title_left(depth)
        for name, ct in root:
            self.list.add_row(CompactMemberRow(self.state, name, concurrent_title=ct, left=base), ('member', name))
        order = [u[0] for u in (info.get('units') or []) if u]
        depts = [d for d in order if d in members] + sorted(d for d in members if d and d not in order)
        if not query:
            depts = [d for d in order if d] + [d for d in depts if d not in order]
        for dept in depts:
            direct, teams = self._dept_layout(units, dept)
            names = list(dict.fromkeys([n for n, _ in members.get(dept, [])]))
            open_ = dept in self.expanded_depts or bool(query)
            node = TreeNodeRow(dept, len(names), not open_, chip=True, bold=False, base=base)
            node.chip.clicked.connect(lambda d=dept, ns=names: self.show_members(d, ns))
            self.list.add_row(node, ('unit', company, (dept,)), selectable=False)
            if not open_:
                continue
            if not names:
                self.list.add_row(HintRow('구성원이 없습니다', depth + 2), ('empty', dept), selectable=False)
            # 부서 바로 아래(부서장 · 팀 없는 부서원) → 팀(팀장 · 팀원). 겸직한 곳에는 '겸직' 표시
            # 아바타 왼쪽 끝은 바로 위 부서·팀 이름 글자 시작 위치에 맞춘다
            for name, ct in direct:
                self.list.add_row(CompactMemberRow(self.state, name, concurrent_title=ct,
                                                   left=TreeNodeRow.text_left(0, base)), ('member', name))
            for team, team_members in teams:
                team_open = (dept, team) not in self.collapsed_teams or bool(query)
                team_node = TreeNodeRow(team, len(team_members), not team_open, depth=1, chip=True,
                                        bold=False, base=base)
                team_node.chip.clicked.connect(lambda t=team, ns=[n for n, _ in team_members]: self.show_members(t, ns))
                self.list.add_row(team_node, ('unit', company, (dept, team)), selectable=False)
                if team_open:
                    for name, ct in team_members:
                        self.list.add_row(CompactMemberRow(self.state, name, concurrent_title=ct,
                                                           left=TreeNodeRow.text_left(1, base)), ('member', name))

    def _dept_layout(self, units, dept):
        """부서 한 곳 → (부서 바로 아래 [(이름, 겸직직위|None)], [(팀, [(이름, 겸직직위|None)])])
        같은 사람이 부서장과 팀원을 겸하면 두 곳 모두에 나온다. 직위 순 → 이름 순"""
        def ordered(entries):
            unique = {}
            for name, ct in entries:
                if name not in unique or unique[name] is not None:   # 주소속이 있으면 주소속으로
                    unique[name] = ct
            return sorted(unique.items(), key=lambda x: self._member_key(self.state.user(x[0])))
        direct, teams = [], {}
        for path, ms in units.items():
            if not path or path[0] != dept:
                continue
            if len(path) == 1:
                direct += ms
            else:
                teams.setdefault(path[1], []).extend(ms)
        team_order = sorted(teams, key=lambda t: (int(re.match(r'\d*', t).group() or 0) or 99, t))
        return ordered(direct), [(t, ordered(teams[t])) for t in team_order]

    def show_members(self, title, usernames, exec_=True):
        dlg = MembersDialog(self.state, title, usernames, self.window())
        if exec_:
            dlg.exec_()
        return dlg

    def _collapsed_units(self, company):
        return {node[2] for node in self.collapsed_nodes if node[0] == 'unit' and node[1] == company}

    def _finish(self, scroll):
        self.list.verticalScrollBar().setValue(scroll)
        has_rows = self.list.count() > 0
        self.list.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)

    # ---- 동작 ----
    def toggle_company(self, company):
        if company in self.collapsed:
            self.collapsed.discard(company)
            self.expanded_empty.add(company)
        else:
            self.collapsed.add(company)
        self.refresh()

    def toggle_node(self, node):
        if node in self.collapsed_nodes:
            self.collapsed_nodes.discard(node)
        else:
            self.collapsed_nodes.add(node)
        self.refresh()

    def toggle_unit(self, company, path):
        self.toggle_node(('unit', company, tuple(path)))

    def _clicked(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        if data[0] == 'member' and self.edit_mode:
            self.edit_member(data[1])
        elif data[0] == 'company':
            self.toggle_company(data[1])
        elif data == ('section', '그룹사'):
            self.toggle_node(('section', '그룹사'))
        elif data[0] == 'org_group':
            if self._skip_click:            # 끌어서 놓은 것 - 접지 않는다
                self._skip_click = False
                return
            self.collapsed_groups ^= {data[1]}
            self.refresh()
        elif data[0] == 'unit' and data[2] and self.org_meta(data[1]).get('depth') == 1:
            if len(data[2]) >= 2:                      # 팀: 처음엔 펼쳐져 있고 누르면 접힌다
                self.collapsed_teams ^= {tuple(data[2][:2])}
            else:
                self.expanded_depts ^= {data[2][0]}
            self.refresh()
        elif data[0] == 'unit' and data[2]:
            self.toggle_unit(data[1], data[2])

    def _activate(self, item):
        data = item.data(Qt.UserRole)
        if data and data[0] == 'member':
            if self.edit_mode:
                self.edit_member(data[1])
            else:
                self.state.open_direct(data[1])

    # ---- 편집 모드 (관리자가 조직도에서 구성원 정보를 바로 고친다, 명세 §4.3) ----
    def set_edit_mode(self, on):
        on = bool(on) and self.state.is_admin()
        self.edit_mode = on
        self.edit_button.setChecked(on)
        self.edit_button.refresh_icon(theme.c('yellow') if on else None)
        self.edit_button.setToolTip('편집 모드 끝내기' if on else '편집 모드 (구성원 정보 수정)')
        self.edit_bar.setVisible(on)
        if on:
            self.state.admin_request_users()           # 사번 · 이메일 · 팀까지 채우려고

    def can_edit(self, username):
        from client.kakao.admin import can_edit_member
        return can_edit_member(self.state, username)

    def edit_member(self, username, exec_=True):
        """구성원 정보 창(편집 모드). 권한 · 범위 밖이면 None"""
        from client.kakao.admin import edit_member_info
        return edit_member_info(self.state, username, self.window(), exec_=exec_)

    def start_edit(self, username, exec_=True):
        """우클릭 → 정보 수정: 편집 모드를 켜고 그 사람 창을 연다"""
        self.set_edit_mode(True)
        return self.edit_member(username, exec_=exec_)

    def context_menu_for(self, data):
        token = self.token_of(data)
        if token and self._can_manage_groups() and (data[0] == 'org_group' or self.can_move(token)):
            menu = QMenu(self)
            if data[0] == 'org_group':
                menu.addAction('그룹 수정…', lambda: self.edit_group(data[1]))
            if self.can_move(token):
                tokens = self.lower_tokens()
                up = menu.addAction('위로 이동', lambda: self.move_block_by(token, -1))
                down = menu.addAction('아래로 이동', lambda: self.move_block_by(token, 1))
                up.setEnabled(tokens.index(token) > 0)
                down.setEnabled(tokens.index(token) < len(tokens) - 1)
            if data[0] == 'org_group':
                menu.addAction('그룹 삭제', lambda: self.delete_group(data[1]))
            return menu
        if not data or data[0] != 'member':
            return None
        name = data[1]
        menu = QMenu(self)
        menu.addAction('나와의 채팅' if name == self.state.me else '1:1 채팅',
                       lambda: self.state.open_direct(name))
        if name != self.state.me:
            menu.addAction('채팅방에 초대…', lambda: InviteToRoomDialog(self.state, name, self.window()).exec_())
        menu.addAction('프로필 보기', lambda: self.show_profile(name))
        menu.addAction('연락처 보내기…', lambda: self.send_contact_to_room(name))
        if self.can_edit(name):
            from client.kakao.admin import EDIT_ACTION_TEXT
            menu.addSeparator()
            menu.addAction(EDIT_ACTION_TEXT, lambda: self.start_edit(name))
        if self.edit_mode:
            menu.addAction('편집 모드 끝내기', lambda: self.set_edit_mode(False))
        return menu

    def send_contact_to_room(self, username, room_id=None):
        """조직도에서 찾은 사람의 연락처를 채팅방에 보낸다 ('누구에게 물어볼지' 찾은 뒤 바로 공유)"""
        if room_id is None:
            from client.kakao.chat_room import ForwardDialog
            dlg = ForwardDialog(self.state, self.window())
            dlg.setWindowTitle(f'{self.state.display_name(username)}님 연락처를 보낼 채팅방')
            if dlg.exec_() != dlg.Accepted or not dlg.room_id():
                return None
            room_id = dlg.room_id()
        self.state.send_contact(room_id, username)
        return room_id

    def show_profile(self, username):
        from client.kakao.friends_tab import ProfileDialog
        ProfileDialog(self.state, username, self.window()).exec_()

    def _show_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = self.context_menu_for(item.data(Qt.UserRole))
        if menu is not None:
            menu.exec_(self.list.viewport().mapToGlobal(pos))

    def apply_theme(self):
        self.header.apply_theme()
        self.search_edit.setStyleSheet(search_box_qss())
        self.empty_label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('body')}px;")
        self.edit_bar.setStyleSheet(
            f"QFrame#orgEditBar {{ background: {theme.c('bg_selected')}; border-top: 1px solid {theme.c('border')}; }}"
            f" QLabel {{ color: {theme.c('text')}; font-size: {theme.fs('meta')}px; background: transparent; }}"
            f" QPushButton {{ color: {theme.c('text')}; font-size: {theme.fs('meta')}px; font-weight: bold; }}")
        if self.edit_mode:
            self.edit_button.refresh_icon(theme.c('yellow'))
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"OrgTab {{ background: {theme.c('bg')}; }}")
        # 카드 · 강조는 OrgList 가 그린다 - 기본 항목 바탕(행 전체 폭 회색)은 끈다
        self.list.setStyleSheet(
            f"QListWidget {{ background: {theme.c('bg_alt')}; border: none; border-top: 1px solid {theme.c('border')}; }}"
            " QListWidget::item, QListWidget::item:hover, QListWidget::item:selected"
            " { background: transparent; border: none; }")
        self.refresh()
