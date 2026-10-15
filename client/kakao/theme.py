# -*- coding: utf-8 -*-
"""
테마 - KCS Ink 팔레트(라이트/다크), 한글 폰트 체인, 공용 QSS
모든 위젯은 색상을 하드코딩하지 말고 theme.c('키')로 가져온다.

KCS Ink (2026-10-10): 회사 CI 네이비를 '잉크', 채팅 배경을 차가운 '종이'로 본다.
중립색은 모두 네이비 쪽으로 살짝 기운 회색이고, 다크 모드는 검정이 아닌 깊은 남색이다.
글꼴은 앱에 들어 있는 Poppins SemiBold(영문 · 숫자, SIL OFL) + KoPub World 돋움(한글, assets/fonts) 을 먼저 쓴다.
"""

import os

from PyQt5.QtGui import QFont, QColor

from client.kakao import osenv

FONT_DIR = osenv.resource_path('assets', 'fonts')
# 앱 글꼴 (2026-10-15, Pretendard 대체):
# - Poppins SemiBold · Bold (Indian Type Foundry, SIL OFL 1.1 - Poppins-OFL.txt, Google Fonts 배포본) 영문 · 숫자 · 기호.
#   영문은 SemiBold 한 굵기로 보이게 한다: 300~600 은 SemiBold 파일, 700~900 은 Bold 파일 (Qt 가 가장 가까운 굵기를 고른다)
# - KoPubWorld 돋움 Light · Medium · Bold (한국출판인회의, 무료 배포 · 임베딩 허용 - KoPubWorld-LICENSE.txt)
#   한글 · 한자 · KS 기호. Poppins 에 없는 글자는 Qt 가 이 글꼴로 내려 그린다
BUNDLED_FONTS = ('Poppins-SemiBold.ttf', 'Poppins-Bold.ttf',
                 'KoPubWorldDotum-Light.ttf', 'KoPubWorldDotum-Medium.ttf', 'KoPubWorldDotum-Bold.ttf')
BUNDLED_FONT_LICENSES = ('Poppins-OFL.txt', 'KoPubWorld-LICENSE.txt')
BUNDLED_LATIN_FAMILY = osenv.BUNDLED_LATIN_FAMILY
BUNDLED_HANGUL_FAMILIES = osenv.BUNDLED_HANGUL_FAMILIES
FONT_FAMILIES = osenv.ui_font_families()   # Poppins → KoPub 돋움 → 이 OS 기본 한글 글꼴 → 다른 OS 글꼴

LIGHT = {
    'bg': '#FFFFFF',
    'bg_alt': '#F5F7FB',
    'bg_hover': '#EEF1F8',
    'bg_selected': '#E4E9F5',
    'chat_bg': '#BACEE0',          # 카카오톡 채팅방 배경 ('kakao' 강조색 전용, KCS 는 아래 덮어쓰기)
    'sidebar': '#ECECED',
    'sidebar_icon': '#A9A9AA',
    'sidebar_icon_active': '#1E1E1E',
    'text': '#171B2B',             # 잉크
    'text_sub': '#586079',
    'text_hint': '#626A80',        # AA 4.5:1 이상 (흰 배경 5.5)
    'border': '#E3E7F0',
    'yellow': '#FEE500',           # 카카오 옐로
    'yellow_dark': '#F5D800',
    'bubble_mine': '#FEE500',
    'bubble_other': '#FFFFFF',
    'bubble_text': '#171B2B',
    'bubble_shadow': 'rgba(23,32,72,0.10)',   # 받은 말풍선·카드의 그림자 (테두리 대신 높이 한 가지)
    'avatar_ring': 'rgba(23,32,72,0.16)',     # 사진 아바타 가장자리 (흰 배경 사진도 칸 크기가 보이게)
    'unread_num': '#E5B800',       # 안 읽은 사람 수 숫자
    'badge': '#D93025',            # 안 읽은 메시지 배지 (흰 글자 4.6:1)
    'on_badge': '#FFFFFF',         # 배지(빨강) 위 글자·아이콘
    'danger_text': '#B3261E',      # 경고 글자(부재중·전송 실패) - 흰 배경·채팅 배경 모두 4.5:1 이상
    'success_text': '#1D6B37',     # 통과·완료 글자 (success_bg 위 4.5:1 이상)
    'success_bg': '#E3F3E8',
    'danger_bg': '#FBE7E5',
    'link': '#2E6FD8',
    'notice_bg': '#FFFFFF',
    'system_bg': 'rgba(42,56,108,0.08)',      # 시스템 안내 · 날짜 칩: 종이 위에 옅은 잉크
    'system_text': '#3B4462',
    'date_chip_bg': 'rgba(42,56,108,0.08)',
    'mention': '#2E6FD8',
    'reaction_bg': '#FFFFFF',
    'calendar_today': '#FEE500',
    'calendar_sun': '#D93B3B',
    'calendar_sat': '#3C78D8',
    'input_bg': '#FFFFFF',
}

DARK = {
    'bg': '#161A28',               # 깊은 남색 (중립 검정 대신)
    'bg_alt': '#1B2030',
    'bg_hover': '#222839',
    'bg_selected': '#2A3147',
    'chat_bg': '#2B2F33',
    'sidebar': '#151515',
    'sidebar_icon': '#6E6E6E',
    'sidebar_icon_active': '#F2F2F2',
    'text': '#E9ECF5',
    'text_sub': '#A5ACC2',
    'text_hint': '#A0A7BD',        # 다크 말풍선(#242B40) 위에서도 4.5:1
    'border': '#2A3145',
    'yellow': '#FEE500',
    'yellow_dark': '#F5D800',
    'bubble_mine': '#FEE500',
    'bubble_other': '#242B40',
    'bubble_text': '#E9ECF5',
    'bubble_shadow': 'rgba(0,0,0,0.28)',
    'avatar_ring': 'rgba(255,255,255,0.14)',
    'unread_num': '#FEE500',
    'badge': '#D93025',
    'on_badge': '#FFFFFF',
    'danger_text': '#FF8A80',      # 다크 배경 위 경고 글자 4.5:1 이상
    'success_text': '#86D9A1',
    'success_bg': '#1E3328',
    'danger_bg': '#3A2226',
    'link': '#7AA7F0',
    'notice_bg': '#20263A',
    'system_bg': 'rgba(255,255,255,0.08)',
    'system_text': '#C9CEDD',
    'date_chip_bg': 'rgba(255,255,255,0.08)',
    'mention': '#86AEF4',          # 다크 말풍선(#242B40) 위 4.5:1 이상
    'reaction_bg': '#242B40',
    'calendar_today': '#FEE500',
    'calendar_sun': '#FF7070',
    'calendar_sat': '#7AA7F0',
    'input_bg': '#1B2030',
}

# 간격 체계 (4px 단위). 묶음 안은 S/M, 묶음 사이는 L/XL, 화면 가장자리는 L(16)
SPACE = {'xxs': 2, 'xs': 4, 's': 8, 'm': 12, 'l': 16, 'xl': 24, 'xxl': 32}
GUTTER = 16      # 탭 화면 좌우 여백


def sp(name):
    return SPACE[name]


# 글자 단계 (px, 글자 크기 배율 적용 전). 새 코드는 숫자 대신 이름을 쓴다: theme.fs('meta')
TYPE_SCALE = {
    'micro': 10,     # 배지 숫자, 공감 선택 창 수
    'caption': 11,   # 시간, 보조 칩
    'meta': 12,      # 부제, 안내, 카드 보조 글자
    'body': 13,      # 말풍선 본문, 목록 미리보기
    'emphasis': 14,  # 목록 제목, 카드 제목
    'title': 15,     # 대화상자 제목
    'lead': 16,      # 섹션·카드 큰 제목
    'heading': 17,   # 대화상자·카드 큰 제목
    'headline': 19,  # 탭 제목 · 프로필 이름
    'display': 22,   # 로그인 · 통화 화면 큰 글자
    'numeral': 26,   # 큰 숫자 (녹음 시간 · 잠금 숫자판)
}
# 목록·카드의 사람 사진 크기 (친구 탭 행 · 내 프로필 · 더보기 프로필 카드가 모두 같은 크기, 2026-10-10)
AVATAR_ROW = 44
# 모서리 단계
RADII = {'xs': 4, 's': 6, 'm': 8, 'card': 12, 'bubble': 16, 'pill': 15}


def fs(name):
    """이름 있는 글자 단계 → 배율 적용 px"""
    return px(TYPE_SCALE[name])


def radius(name):
    return RADII[name]


# 버튼 변형 (shadcn/ui 원칙: 변형마다 기본·hover·pressed·focus·disabled 를 빠짐없이 같은 규칙으로)
BUTTON_VARIANTS = ('primary', 'secondary', 'outline', 'ghost', 'danger')


def button_qss(selector, variant='primary', size='meta', bold=None, pad='6px 8px', fam=''):
    """QPushButton 선택자 하나에 변형 규칙 전체(QSS)를 만든다"""
    if variant not in BUTTON_VARIANTS:
        raise ValueError(variant)
    bg, fg, border, hover, pressed = {
        'primary': (c('yellow'), c('on_accent'), c('yellow'), c('yellow_dark'), c('yellow_dark')),
        'secondary': (c('bg_alt'), c('text'), c('border'), c('bg_hover'), c('bg_selected')),
        'outline': ('transparent', c('text'), c('border'), c('bg_hover'), c('bg_selected')),
        'ghost': ('transparent', c('text_sub'), 'transparent', c('bg_hover'), c('bg_selected')),
        'danger': (c('badge'), '#FFFFFF', c('badge'), c('badge'), c('badge')),
    }[variant]
    weight = 'bold' if (bold if bold is not None else variant in ('primary', 'danger')) else 'normal'
    r = radius('s')
    return (f"QPushButton#{selector} {{ {fam} background: {bg}; color: {fg}; border: 1px solid {border};"
            f" border-radius: {r}px; padding: {pad}; font-size: {fs(size)}px; font-weight: {weight}; }}"
            f" QPushButton#{selector}:hover {{ background: {hover}; }}"
            f" QPushButton#{selector}:pressed {{ background: {pressed}; }}"
            f" QPushButton#{selector}[focusVisible=\"true\"]:focus {{ border: 2px solid {c('yellow_dark') if variant != 'primary' else c('text')}; }}"
            f" QPushButton#{selector}:disabled {{ background: {c('bg_alt')}; color: {c('text_hint')};"
            f" border: 1px solid {c('border')}; }}")


# 강조색(브랜드) 오버레이 - 'kcs'(기본, 회사 CI 네이비) / 'kakao'(클래식 옐로)
# 'yellow*' 키 이름은 호환을 위해 유지하며 의미는 '주 강조색'이다.
BRAND_NAVY = '#2A386C'
LIGHT.update({'on_accent': '#191919', 'bubble_mine_text': '#191919', 'bubble_mine_sub': 'rgba(0,0,0,0.55)',
              'bubble_mine_line': 'rgba(0,0,0,0.12)', 'bubble_mine_link': '#1F4FB8', 'brand': BRAND_NAVY,
              'sidebar_badge_ring': '#ECECED', 'sidebar_active_bg': 'rgba(0,0,0,0.06)',
              'sidebar_icon_hover': '#6B6B6B', 'sidebar_logo': BRAND_NAVY,
              'header_panel': 'rgba(255,255,255,0.82)', 'header_line': 'rgba(0,0,0,0.07)'})
DARK.update({'on_accent': '#191919', 'bubble_mine_text': '#191919', 'bubble_mine_sub': 'rgba(0,0,0,0.55)',
             'bubble_mine_line': 'rgba(0,0,0,0.12)', 'bubble_mine_link': '#1F4FB8', 'brand': '#8FA3E8',
             'sidebar_badge_ring': '#151515', 'sidebar_active_bg': 'rgba(255,255,255,0.08)',
             'sidebar_icon_hover': '#A6A6A6', 'sidebar_logo': '#8FA3E8',
             'header_panel': 'rgba(22,26,40,0.88)', 'header_line': 'rgba(255,255,255,0.07)'})

ACCENTS = {
    'kakao': {'light': {}, 'dark': {}},
    'kcs': {
        'light': {
            'yellow': BRAND_NAVY, 'yellow_dark': '#1E2955', 'on_accent': '#FFFFFF',
            'bubble_mine': '#3450B4', 'bubble_mine_text': '#FFFFFF', 'bubble_mine_sub': 'rgba(255,255,255,0.72)',
            'bubble_mine_line': 'rgba(255,255,255,0.28)', 'bubble_mine_link': '#C9D6FF',
            'unread_num': '#3450B4', 'calendar_today': BRAND_NAVY, 'chat_bg': '#EBEFF6',
            'sidebar': BRAND_NAVY, 'sidebar_icon': '#C3CBE6', 'sidebar_icon_active': '#FFFFFF',
            'sidebar_badge_ring': BRAND_NAVY, 'link': '#2F55D4', 'mention': '#2F55D4',
            'sidebar_active_bg': 'rgba(255,255,255,0.14)', 'sidebar_icon_hover': '#D3DAF0', 'sidebar_logo': '#FFFFFF',
        },
        'dark': {
            'yellow': '#536ED4', 'yellow_dark': '#4A62C2', 'on_accent': '#FFFFFF',
            'bubble_mine': '#3A4E9C', 'bubble_mine_text': '#FFFFFF', 'bubble_mine_sub': 'rgba(255,255,255,0.70)',
            'bubble_mine_line': 'rgba(255,255,255,0.25)', 'bubble_mine_link': '#C9D6FF',
            'unread_num': '#8FA3E8', 'calendar_today': '#536ED4', 'chat_bg': '#10131D',
            'sidebar': '#141A30', 'sidebar_icon': '#9AA6CC', 'sidebar_icon_active': '#FFFFFF',
            'sidebar_badge_ring': '#141A30',
            'sidebar_active_bg': 'rgba(255,255,255,0.10)', 'sidebar_icon_hover': '#B4BEDF', 'sidebar_logo': '#FFFFFF',
        },
    },
}
BRANDS = {'kcs': 'KCS 블루', 'kakao': '옐로 (사용 안 함)'}   # 앱은 KCS 블루로 고정, 'kakao'는 내부 호환용

# 말풍선 꾸미기 (명세 §8.11) - 내 말풍선 배경/글자색/모서리. None 이면 현재 테마 값 사용
BUBBLE_STYLES = {
    'basic': {'label': '기본', 'bg': None, 'fg': None, 'radius': 17},
    'round': {'label': '동글동글', 'bg': None, 'fg': None, 'radius': 20},
    'square': {'label': '반듯하게', 'bg': None, 'fg': None, 'radius': 5},
    'yellow': {'label': '옐로', 'bg': '#FEE500', 'fg': '#191919', 'radius': 13},
    'pastel': {'label': '파스텔 핑크', 'bg': '#FFD6E0', 'fg': '#3B2730', 'radius': 16},
    'mint': {'label': '민트', 'bg': '#C9F2E4', 'fg': '#163B30', 'radius': 16},
    'mono': {'label': '모노', 'bg': '#3C3C3C', 'fg': '#FFFFFF', 'radius': 10},
}


def bubble_style(name):
    """말풍선 스타일 → {'bg', 'fg', 'radius', 'label'} (bg/fg 는 테마 값으로 채움)"""
    st = dict(BUBBLE_STYLES.get(name or 'basic') or BUBBLE_STYLES['basic'])
    st['bg'] = st['bg'] or c('bubble_mine')
    st['fg'] = st['fg'] or c('bubble_mine_text')
    return st


EVENT_COLORS = ['#2A386C', '#FEE500', '#FF7A59', '#4CB6F2', '#6DCC7B', '#B184F2', '#F27AB0']

_state = {'dark': False, 'scale': 1.0, 'brand': 'kcs', 'reduce_motion': False}


def set_brand(name):
    _state['brand'] = name if name in ACCENTS else 'kcs'


def brand():
    return _state['brand']


def set_dark(dark):
    _state['dark'] = bool(dark)


def is_dark():
    return _state['dark']


def set_reduce_motion(on):
    """동작 줄이기: 켜면 깜빡임·움직이는 표시를 멈추고 정지 상태로 보여 준다"""
    _state['reduce_motion'] = bool(on)


def reduce_motion():
    return _state['reduce_motion']


def set_font_scale(scale):
    _state['scale'] = max(0.8, min(2.0, float(scale)))   # 200% 까지 (WCAG 1.4.4 글자 크기 조정)


def font_scale():
    return _state['scale']


def c(key):
    """현재 테마(밝기 + 브랜드 강조색)의 색상 문자열"""
    overlay = ACCENTS[_state['brand']]['dark' if _state['dark'] else 'light']
    if key in overlay:
        return overlay[key]
    return (DARK if _state['dark'] else LIGHT)[key]


def qc(key):
    """현재 테마의 QColor ('rgba(...)' 문자열도 처리)"""
    value = c(key)
    if value.startswith('rgba'):
        r, g, b, a = [x.strip() for x in value[5:-1].split(',')]
        color = QColor(int(r), int(g), int(b))
        color.setAlphaF(float(a))
        return color
    return QColor(value)


def px(size):
    """글자 크기 배율을 적용한 픽셀 크기"""
    return max(8, int(round(size * _state['scale'])))


_available = None


def load_bundled_fonts():
    """assets/fonts 의 Poppins · KoPub 돋움을 앱 글꼴로 등록 (앱마다 한 번). 등록된 글꼴 이름 목록"""
    from PyQt5.QtGui import QFontDatabase
    names = []
    for name in BUNDLED_FONTS:
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                names += QFontDatabase.applicationFontFamilies(fid)
    return names


def _available_families():
    """이 PC에 설치된 글꼴 이름 집합 (한 번만 조회, 앱에 든 글꼴 포함). 앱이 없으면 None"""
    global _available
    if _available is None:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QFontDatabase
        if QApplication.instance() is None:
            return None
        load_bundled_fonts()
        _available = set(QFontDatabase().families())
    return _available


def installed(names):
    """names 중 설치된 글꼴만, 순서 유지 (앱이 없으면 그대로).
    없는 글꼴('맑은 고딕' on macOS 등)을 넘기면 Qt 가 별칭 표를 만드느라 첫 그리기가 약 130ms 늦어진다"""
    available = _available_families()
    return list(names) if available is None else [n for n in names if n in available]


def font_families():
    """FONT_FAMILIES 중 설치된 것 (하나도 없으면 원래 목록)"""
    return installed(FONT_FAMILIES) or list(FONT_FAMILIES)


def bundled_fonts_ready():
    """앱에 든 글꼴이 둘 다 등록됐는가 (영문 Poppins + 한글 KoPub 돋움, 한글 쪽은 영문 · 한글 이름 중 하나)"""
    families = set(font_families())
    return BUNDLED_LATIN_FAMILY in families and any(f in families for f in BUNDLED_HANGUL_FAMILIES)


def size_of(size):
    """글자 크기: 단계 이름('meta') 또는 px 숫자 → px 숫자 (배율 적용 전)"""
    return TYPE_SCALE[size] if isinstance(size, str) else size


# CSS 굵기(300~900) → Qt5 QFont 굵기. 앱 글꼴은 Poppins SemiBold · Bold, KoPub 돋움 Light · Medium · Bold 로,
# Qt 가 글꼴마다 가장 가까운 굵기 파일을 고른다 (영문 300~600 → SemiBold, 700~900 → Bold)
FONT_WEIGHTS = {300: QFont.Light, 400: QFont.Normal, 500: QFont.Medium, 600: QFont.DemiBold,
                700: QFont.Bold, 800: QFont.ExtraBold, 900: QFont.Black}
# 굵기 → 앱 글꼴(Poppins) 스타일 이름
BUNDLED_STYLES = {300: 'SemiBold', 400: 'SemiBold', 500: 'SemiBold', 600: 'SemiBold', 700: 'Bold', 800: 'Bold', 900: 'Bold'}


def font(size='body', bold=False, scaled=True, weight=None):
    """size: 단계 이름(권장, 예 'meta') 또는 단계에 없는 특수 크기 숫자.
    weight: CSS 굵기 300~900 (주면 bold 대신). QSS 에서는 font-weight: 300~900 을 그대로 쓴다.
    scaled=False: 크기가 고정된 직접 그린 부품(사이드바 버튼·잠금 숫자판·말풍선 미리보기)용 — 단계 크기 그대로,
    글자 크기 설정(최대 200%)을 적용하지 않아 고정된 틀 밖으로 넘치지 않는다"""
    f = QFont()
    f.setFamilies(font_families())
    n = size_of(size)
    f.setPixelSize(px(n) if scaled else n)
    if weight is not None:
        if weight not in FONT_WEIGHTS:
            raise ValueError(f'font weight must be one of {sorted(FONT_WEIGHTS)}: {weight}')
        f.setWeight(FONT_WEIGHTS[weight])
    else:
        f.setBold(bold)
    f.setStyleHint(QFont.SansSerif)
    return f


def font_css():
    return 'font-family: ' + ', '.join(f'"{name}"' for name in font_families()) + ';'


def scrollbar_qss():
    return """
        QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
        QScrollBar::handle:vertical { background: rgba(88,96,121,0.38); border-radius: 4px; min-height: 30px; }
        QScrollBar::handle:vertical:hover { background: rgba(88,96,121,0.60); }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
        QScrollBar::handle:horizontal { background: rgba(88,96,121,0.38); border-radius: 4px; min-width: 30px; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    """


def base_qss():
    """앱 전역 스타일시트(QApplication.setStyleSheet용)"""
    return f"""
        QWidget {{ {font_css()} font-size: {fs('body')}px; color: {c('text')}; }}
        QMainWindow, QDialog {{ background: {c('bg')}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QDateTimeEdit, QDateEdit, QTimeEdit, QSpinBox, QComboBox {{
            background: {c('input_bg')}; border: 1px solid {c('border')}; border-radius: 8px;
            padding: 7px 10px; selection-background-color: {c('yellow')}; selection-color: {c('on_accent')};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c('yellow')}; }}
        QPushButton {{
            background: {c('bg_alt')}; border: 1px solid {c('border')}; border-radius: 8px;
            padding: 7px 14px;
        }}
        QPushButton:hover {{ background: {c('bg_hover')}; }}
        QPushButton[focusVisible="true"]:focus {{ border: 2px solid {c('yellow_dark')}; }}
        QPushButton:disabled {{ color: {c('text_hint')}; }}
        QPushButton[primary="true"] {{ background: {c('yellow')}; border: none; color: {c('on_accent')}; font-weight: bold; }}
        QPushButton[primary="true"]:hover {{ background: {c('yellow_dark')}; }}
        QPushButton[flat="true"] {{ background: transparent; border: none; padding: 4px; border-radius: 8px; }}
        QPushButton[flat="true"]:hover {{ background: {c('bg_hover')}; }}
        QPushButton[flat="true"]:pressed {{ background: {c('bg_selected')}; }}
        QPushButton[flat="true"][focusVisible="true"]:focus {{ border: 2px solid {c('yellow_dark')}; }}
        QMenu {{ background: {c('bg')}; border: 1px solid {c('border')}; border-radius: 10px; padding: 6px; }}
        QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
        QMenu::separator {{ height: 1px; background: {c('border')}; margin: 5px 8px; }}
        QMenu::item:selected {{ background: {c('bg_hover')}; color: {c('text')}; }}
        QListWidget {{ background: {c('bg')}; border: none; outline: 0; }}
        QListWidget::item {{ border: none; }}
        QListWidget::item:selected {{ background: {c('bg_selected')}; }}
        QListWidget::item:hover {{ background: {c('bg_hover')}; }}
        QToolTip {{ background: {c('text')}; color: {c('bg')}; border: none; border-radius: 6px; padding: 5px 8px; }}
        QCheckBox, QRadioButton {{ spacing: 6px; }}
        {scrollbar_qss()}
    """
