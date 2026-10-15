# -*- coding: utf-8 -*-
"""
회사 CI(㈜케이씨에스) 로고 - assets/brand/ci_horizontal.png, ci_vertical.png
(2026-10-15: CI 원본 ci_horizontal.ai · ci_vertical.ai 를 288dpi(4배)로 렌더한 투명 PNG 2079x649 · 1647x921.
 예전 521x163 · 413x231 팔레트 PNG 는 큰 로고(로그인 · 배경)에서 뭉개졌다)
- logo_pixmap('horizontal'|'vertical'|'mark', height, color): 원본/단색(흰색 등) 버전, HiDPI
- app_icon_pixmap(size): 네이비 둥근 사각형 + 흰 말풍선 안 KCS 마크 (창·트레이·exe 아이콘)
- paint_app_icon(painter, size): 같은 아이콘을 임의 크기로 그리기 (scripts/make_app_icon.py)
로고 파일이 없으면 'KCS' 글자로 대체 렌더링한다.
"""

import os

from PyQt5.QtCore import Qt, QRect, QRectF, QPointF
from PyQt5.QtGui import QPixmap, QPainter, QColor, QImage, QIcon, QLinearGradient, QPainterPath

from client.kakao import theme

from client.kakao import osenv  # noqa: E402

ROOT = osenv.ROOT
BRAND_DIR = osenv.resource_path('assets', 'brand')   # 빌드한 앱에서도 찾도록
COMPANY_NAME = '(주)케이씨에스'
# 세로형 로고에서 'KCS' 심볼 마크가 차지하는 영역 - 그림 크기에 대한 비율 (x, y, w, h). 해상도가 바뀌어도 같은 자리
MARK_FRACTION = (76 / 413, 27 / 231, 248 / 413, 123 / 231)
TRIM_SCAN_WIDTH = 260   # 투명 여백을 찾을 때 이 너비로 줄여 훑는다 (4배 PNG 를 픽셀마다 읽으면 수 초 걸린다)

_cache = {}


def _source(kind):
    key = ('src', kind)
    if key not in _cache:
        name = 'ci_vertical.png' if kind in ('vertical', 'mark') else 'ci_horizontal.png'
        img = QImage(os.path.join(BRAND_DIR, name))
        if not img.isNull() and kind == 'mark':
            img = img.copy(mark_rect(img))
        if not img.isNull():
            img = _trim(img.convertToFormat(QImage.Format_ARGB32))
        _cache[key] = img
    return _cache[key]


def mark_rect(img):
    """세로형 로고 그림에서 심볼 마크 영역"""
    fx, fy, fw, fh = MARK_FRACTION
    return QRect(round(img.width() * fx), round(img.height() * fy), round(img.width() * fw), round(img.height() * fh))


def _trim(img):
    """투명 여백 제거. 큰 그림은 TRIM_SCAN_WIDTH 로 줄인 사본에서 경계를 찾고 원본 좌표로 되돌린다(축소 단위만큼 여유)"""
    w, h = img.width(), img.height()
    step = max(1, -(-w // TRIM_SCAN_WIDTH))   # 올림 나눗셈: 축소 배율
    probe = img if step == 1 else img.scaled(-(-w // step), -(-h // step), Qt.IgnoreAspectRatio, Qt.FastTransformation)
    xs, ys = [], []
    for y in range(probe.height()):
        for x in range(probe.width()):
            if probe.pixelColor(x, y).alpha() > 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return img
    x0, y0 = max(0, min(xs) * step - step), max(0, min(ys) * step - step)
    x1, y1 = min(w, (max(xs) + 2) * step), min(h, (max(ys) + 2) * step)
    return img.copy(QRect(x0, y0, x1 - x0, y1 - y0))


def has_logo():
    return not _source('horizontal').isNull()


def logo_pixmap(kind='horizontal', height=32, color=None):
    """로고 픽스맵. color를 주면 형태(알파)는 유지하고 단색으로 칠한다(예: 네이비 배경 위 흰색)."""
    key = (kind, height, color, theme.brand())
    if key in _cache:
        return _cache[key]
    src = _source(kind)
    ratio = 2.0
    if src.isNull():
        pm = _fallback(kind, height, color or theme.BRAND_NAVY)
    else:
        width = max(1, round(src.width() * height / src.height()))
        img = src.scaled(int(width * ratio), int(height * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if color:
            tinted = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
            tinted.fill(Qt.transparent)
            p = QPainter(tinted)
            p.drawImage(0, 0, img)
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(tinted.rect(), QColor(color))
            p.end()
            img = tinted
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(ratio)
    _cache[key] = pm
    return pm


def _fallback(kind, height, color):
    """로고 파일이 없을 때: 굵은 'KCS' (+ 회사명) 텍스트"""
    text = 'KCS' if kind == 'mark' else f'KCS {COMPANY_NAME}'
    f = theme.font('micro', bold=True)
    f.setPixelSize(max(8, int(height * 0.8)))
    f.setItalic(True)
    from PyQt5.QtGui import QFontMetrics
    width = QFontMetrics(f).horizontalAdvance(text) + 4
    pm = QPixmap(width * 2, height * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, width, height), Qt.AlignCenter, text)
    p.end()
    return pm


def _bubble_path(size):
    """둥근 말풍선 + 왼쪽 아래 꼬리 (아이콘 좌표계 0..size)"""
    u = size / 100.0
    body = QPainterPath()
    body.addRoundedRect(QRectF(16 * u, 22 * u, 68 * u, 50 * u), 20 * u, 20 * u)
    tail = QPainterPath()
    tail.moveTo(QPointF(27 * u, 62 * u))
    tail.lineTo(QPointF(22 * u, 84 * u))
    tail.quadTo(QPointF(36 * u, 76 * u), QPointF(46 * u, 66 * u))
    tail.closeSubpath()
    return body.united(tail).simplified()


def paint_app_icon(p, size):
    """앱 아이콘: 네이비 그라데이션 둥근 사각형 + 흰 말풍선 + 말풍선 안 네이비 KCS 마크.
    48px 이하에서는 마크가 뭉개지므로 말풍선 안에 점 세 개(메신저 기호)를 그린다."""
    u = size / 100.0
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor('#3D52A6'))
    grad.setColorAt(1, QColor('#1E2853'))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(0, 0, size, size), 22.5 * u, 22.5 * u)
    p.setBrush(QColor('#FFFFFF'))
    p.drawPath(_bubble_path(size))
    navy = QColor(theme.BRAND_NAVY)
    if size <= 48:
        p.setBrush(navy)
        r = 5.2 * u
        for cx in (35, 50, 65):
            p.drawEllipse(QPointF(cx * u, 47 * u), r, r)
        return
    src = _source('mark')
    target_w, cy = 42 * u, 47 * u
    if src.isNull():
        mark = _fallback('mark', max(8, int(20 * u)), theme.BRAND_NAVY)
        sw, sh = mark.width() / mark.devicePixelRatio(), mark.height() / mark.devicePixelRatio()
    else:
        mark = logo_pixmap('mark', max(8, round(target_w * src.height() / src.width())), theme.BRAND_NAVY)
        sw = target_w
        sh = target_w * src.height() / src.width()
    p.drawPixmap(QRectF(50 * u - sw / 2, cy - sh / 2, sw, sh), mark, QRectF(mark.rect()))


def app_icon_pixmap(size=64):
    """앱 아이콘 (HiDPI 2배 픽스맵)"""
    key = ('icon', size)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_app_icon(p, size)
    p.end()
    _cache[key] = pm
    return pm


def badge_pixmap(width=46, height=34, bg='#FFFFFF', fg=None):
    """둥근 타일 위 KCS 마크 (사이드바 상단 로고용)"""
    fg = fg or theme.BRAND_NAVY
    key = ('badge', width, height, bg, fg)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(width * 2, height * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(QRectF(0, 0, width, height), 10, 10)
    mark = logo_pixmap('mark', int(height * 0.5), fg)
    mw = mark.width() / mark.devicePixelRatio()
    mh = mark.height() / mark.devicePixelRatio()
    p.drawPixmap(QRectF((width - mw) / 2, (height - mh) / 2, mw, mh), mark, QRectF(mark.rect()))
    p.end()
    _cache[key] = pm
    return pm


def app_icon():
    icon = QIcon()
    for s in (16, 32, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(s))
    return icon
