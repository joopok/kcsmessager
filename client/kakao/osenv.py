# -*- coding: utf-8 -*-
"""
운영체제 공통 계층 - Windows · macOS · Linux 차이를 이 파일 한 곳에 모은다.

다른 모듈은 sys.platform 을 직접 보지 말고 여기 함수를 쓴다.
- 어떤 OS 인가: current() / is_mac() / is_windows() / is_linux()
- 폴더: app_home() (사용자 데이터) · user_dir('downloads'|'documents'|'pictures'|'home') · resource_path() (앱에 든 파일)
- 열기: open_path() · open_url()
- 글자: ui_font_families() · emoji_families() - OS 마다 기본 한글 · 이모지 글꼴 순서
- 단축키 표기: shortcut_text('Ctrl+Shift+Enter') → macOS '⇧⌘Enter' (Apple 순서 ⌃⌥⇧⌘), 그 밖 'Ctrl+Shift+Enter'
  (Qt 는 QKeySequence 의 Ctrl 을 macOS 에서 ⌘ 로 자동으로 바꾸므로 키 연결은 어디서나 'Ctrl+…' 로 쓴다)
- 시작 보정: prepare_process() (QApplication 전) · configure_app(app) (QApplication 직후)
- 다시 실행: relaunch_command(argv) (빌드한 앱은 PYINSTALLER_RESET_ENVIRONMENT=1 로 새 임시 폴더)
- 영문 입력: prefer_latin_input(edit) - 사번 칸에 포커스가 오면 한글 대신 영문 입력 상태로

함수는 부를 때마다 sys.platform 을 읽는다 (테스트에서 OS 를 바꿔 끼울 수 있게).
"""

import os
import subprocess
import sys

APP_NAME = 'KCS Talk'
APP_ID = 'kr.co.kcs.talk'                 # macOS 번들 ID · Windows AppUserModelID · Linux 데스크톱 파일 이름
ORGANIZATION = '케이씨에스'
DESKTOP_FILE = 'kr.co.kcs.talk'           # Linux: ~/.local/share/applications/kr.co.kcs.talk.desktop
DATA_DIR_NAME = '.kcs_messenger'          # 모든 OS 공통 (예전 설치와 같은 폴더를 그대로 쓴다)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- 어떤 OS 인가
def current():
    """'mac' / 'windows' / 'linux' (그 밖의 유닉스도 'linux' 로 다룬다)"""
    if sys.platform == 'darwin':
        return 'mac'
    if sys.platform.startswith(('win', 'cygwin', 'msys')):
        return 'windows'
    return 'linux'


def is_mac():
    return current() == 'mac'


def is_windows():
    return current() == 'windows'


def is_linux():
    return current() == 'linux'


def os_label():
    return {'mac': 'macOS', 'windows': 'Windows', 'linux': 'Linux'}[current()]


# ---------------------------------------------------------------- 폴더
def app_home():
    """사용자 데이터 폴더 (설정 · 저장된 로그인 · 비밀채팅 키 · 녹음). KCS_MESSENGER_HOME 이 있으면 그 폴더"""
    return os.environ.get('KCS_MESSENGER_HOME') or os.path.join(os.path.expanduser('~'), DATA_DIR_NAME)


def app_path(*parts):
    """app_home() 아래 경로"""
    return os.path.join(app_home(), *parts)


_STANDARD = {'downloads': ('DownloadLocation', 'Downloads'), 'documents': ('DocumentsLocation', 'Documents'),
             'pictures': ('PicturesLocation', 'Pictures'), 'home': ('HomeLocation', '')}


def user_dir(kind):
    """사용자 폴더 (다운로드 · 문서 · 사진 · 홈). OS 가 알려 주는 위치(한글 이름 폴더 포함)를 쓰고,
    없으면 홈 아래 영문 이름, 그것도 없으면 홈"""
    location, fallback = _STANDARD[kind]
    home = os.path.expanduser('~')
    path = ''
    try:
        from PyQt5.QtCore import QStandardPaths
        path = QStandardPaths.writableLocation(getattr(QStandardPaths, location))
    except ImportError:
        pass
    if not path:
        path = os.path.join(home, fallback) if fallback else home
    return path if os.path.isdir(path) else home


def display_path(path, start=None):
    """보여 줄 경로: 저장소(또는 start) 안이면 상대 경로, 다른 드라이브(Windows C: / D:)면 그대로"""
    try:
        return os.path.relpath(path, start or ROOT)
    except ValueError:                                   # Windows: 드라이브가 다르면 상대 경로가 없다
        return os.path.abspath(path)


def resource_path(*parts):
    """앱에 든 파일(assets/…) 경로 - 소스 실행이면 저장소, PyInstaller 로 빌드한 앱이면 풀린 임시 폴더"""
    base = getattr(sys, '_MEIPASS', None) or ROOT
    return os.path.join(base, *parts)


def secure_file(path):
    """다른 사용자가 못 읽게 (POSIX 600). Windows 는 사용자 프로필 폴더 권한을 따르므로 하지 않는다"""
    if is_windows():
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


_WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def safe_filename(name, fallback='file'):
    """어느 OS 에서도 저장되는 파일 이름 - 받은 첨부 이름은 다른 OS 에서 왔을 수 있다.
    Windows 금지 글자(\\ / : * ? " < > |, 제어 문자) → '_', 끝의 점 · 공백 제거, CON · NUL 같은 예약 이름 피하기, 길이 제한"""
    import re
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', os.path.basename(str(name or '').replace('\\', '/'))).strip(' .')
    if not name:
        return fallback
    stem, dot, ext = name.partition('.')
    if stem.upper() in _WINDOWS_RESERVED:
        name = '_' + name
    if len(name) > 180:                                   # 경로 길이 한도(Windows 260자) 여유
        root, ext = os.path.splitext(name)
        name = root[:180 - len(ext)] + ext
    return name


# ---------------------------------------------------------------- 열기
def open_command(path_or_url):
    """Qt 없이 열 때 쓰는 명령 (Windows 는 None - os.startfile 사용)"""
    if is_mac():
        return ['open', path_or_url]
    if is_windows():
        return None
    return ['xdg-open', path_or_url]


def _open_fallback(target):
    try:
        if is_windows():
            os.startfile(target)          # noqa: S606 - 사용자가 고른 파일/주소를 기본 앱으로
        else:
            subprocess.Popen(open_command(target) or [], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, AttributeError):
        return False


def open_path(path):
    """파일 · 폴더를 OS 기본 앱으로 연다. 열었으면 True"""
    try:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        if QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path))):
            return True
    except ImportError:
        pass
    return _open_fallback(os.path.abspath(path))


def open_url(url):
    """웹 주소를 기본 브라우저로. 열었으면 True"""
    try:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        if QDesktopServices.openUrl(QUrl(url)):
            return True
    except ImportError:
        pass
    return _open_fallback(url)


# ---------------------------------------------------------------- 글자
_UI_FONTS = {
    'mac': ['Apple SD Gothic Neo', 'AppleGothic', 'Noto Sans KR', 'Arial'],
    'windows': ['맑은 고딕', 'Malgun Gothic', 'Noto Sans KR', 'Segoe UI', 'Arial'],
    'linux': ['Noto Sans CJK KR', 'Noto Sans KR', 'NanumGothic', 'Nanum Gothic', 'UnDotum', 'DejaVu Sans'],
}
_EMOJI_FONTS = {'mac': ['Apple Color Emoji'], 'windows': ['Segoe UI Emoji'], 'linux': ['Noto Color Emoji']}
# 앱에 든 글꼴(assets/fonts, theme.BUNDLED_FONTS) 의 글꼴 이름. 영문 · 숫자 · 기호는 Poppins(Indian Type Foundry, SIL OFL;
# SemiBold 를 기본 굵기로, 굵은 글씨는 Bold), 한글은 KoPub World 돋움. Poppins 에 없는 글자(한글)는 Qt 가 다음 글꼴(KoPub)로
# 내려 그린다. Windows 는 한글 이름('KoPubWorld돋움체')으로 등록될 수 있어 두 이름을 모두 둔다 (theme.installed 가 있는 것만).
BUNDLED_LATIN_FAMILY = 'Poppins'
BUNDLED_HANGUL_FAMILIES = ('KoPubWorldDotum', 'KoPubWorld돋움체')
BUNDLED_FAMILIES = (BUNDLED_LATIN_FAMILY,) + BUNDLED_HANGUL_FAMILIES


def ui_font_families():
    """UI 글꼴 순서: 앱에 든 Poppins(영문·숫자) → KoPub 돋움(한글) → 이 OS 기본 한글 글꼴 → 다른 OS 글꼴(원격 데스크톱 등 대비)"""
    own = _UI_FONTS[current()]
    others = [f for key, names in _UI_FONTS.items() if key != current() for f in names]
    return list(dict.fromkeys(list(BUNDLED_FAMILIES) + own + others))


def emoji_families():
    own = _EMOJI_FONTS[current()]
    others = [f for key, names in _EMOJI_FONTS.items() if key != current() for f in names]
    return own + others


# ---------------------------------------------------------------- 단축키 표기
_MAC_KEYS = {'Ctrl': '⌘', 'Shift': '⇧', 'Alt': '⌥', 'Meta': '⌃'}


def mod_label():
    """주 수정 키 이름: macOS '⌘', 그 밖 'Ctrl'"""
    return '⌘' if is_mac() else 'Ctrl'


def shortcut_text(sequence):
    """'Ctrl+Shift+Enter' → macOS '⇧⌘Enter' (Qt 가 macOS 에서 Ctrl 을 ⌘ 로 연결하는 것과 같게), 그 밖은 그대로"""
    if not is_mac():
        return sequence
    parts = sequence.split('+')
    keys = [p for p in parts if p not in _MAC_KEYS]
    mods = ''.join(_MAC_KEYS[p] for p in ('Meta', 'Alt', 'Shift', 'Ctrl') if p in parts)
    return mods + '+'.join(keys)


# ---------------------------------------------------------------- 시작 보정
def prepare_process():
    """QApplication 을 만들기 전에 부른다: High DPI · OS 별 앱 이름/작업 표시줄 묶음"""
    os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '1')
    from PyQt5.QtCore import Qt, QCoreApplication
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        from PyQt5.QtGui import QGuiApplication
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if is_mac():
        from client.kakao.macos import run_in_app_bundle, set_app_name
        run_in_app_bundle(APP_NAME, APP_ID, app_home())   # Dock 툴팁에 'Python' 대신 (되면 번들에서 다시 시작)
        set_app_name(APP_NAME)                 # 메뉴 막대에 'Python' 대신
    elif is_windows():
        set_windows_app_id()                   # 작업 표시줄에 python.exe 아이콘 대신 앱 아이콘 · 한 묶음


def set_windows_app_id(app_id=APP_ID):
    """Windows: 작업 표시줄이 이 프로세스를 python.exe 가 아닌 KCS Talk 로 묶게 한다. 바꿨으면 True"""
    if not is_windows():
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0
    except (OSError, AttributeError):
        return False


def configure_app(app):
    """QApplication 을 만든 직후 부른다: 이름 · 스타일 · OS 별 연동"""
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORGANIZATION)
    app.setOrganizationDomain('kcs.co.kr')
    app.setStyle('Fusion')                     # 세 OS 에서 같은 모양 (기본 스타일은 OS 마다 다르다)
    if is_mac():
        from client.kakao.macos import set_dock_name
        set_dock_name(APP_NAME)
    elif is_linux() and hasattr(app, 'setDesktopFileName'):
        app.setDesktopFileName(DESKTOP_FILE)   # Wayland/GNOME 이 작업 표시줄 아이콘을 .desktop 과 잇는다


# ---------------------------------------------------------------- 영문 입력 (사번 칸)
def prefer_latin_input(edit):
    """edit(QLineEdit)에 포커스가 올 때마다 입력을 영문으로 - 한글 상태로 사번을 치면 'ㅏㅡ' 처럼 들어갔다.
    macOS: 입력 소스를 영문 자판으로, Windows: 그 창의 IME 를 영숫자 모드로, 그 밖: Qt 입력 힌트만"""
    from PyQt5 import sip
    from PyQt5.QtCore import QEvent, QObject, Qt, QTimer

    edit.setInputMethodHints(edit.inputMethodHints() | Qt.ImhLatinOnly | Qt.ImhPreferLatin
                             | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase)

    class _Latin(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.FocusIn:
                QTimer.singleShot(0, self.apply)  # Qt 의 IME 처리 다음에 (이 객체가 지워지면 불리지 않는다)
            return False

        def apply(self):
            if not sip.isdeleted(edit) and edit.hasFocus():   # 그사이 칸이 지워졌거나 포커스가 떠났으면 그대로
                select_latin_input(edit)

    edit._latin_filter = _Latin(edit)
    edit.installEventFilter(edit._latin_filter)
    if edit.hasFocus():
        select_latin_input(edit)
    return edit._latin_filter


def select_latin_input(widget=None):
    """지금 입력을 영문으로 바꾼다. 바꿨으면 True (이미 영문이거나 할 수 없으면 False)"""
    from PyQt5.QtGui import QGuiApplication
    if QGuiApplication.platformName() in ('offscreen', 'minimal'):   # 화면 없는 테스트: 이 PC 입력 소스는 그대로
        return False
    if is_mac():
        from client.kakao.macos import select_ascii_input
        return select_ascii_input()
    if is_windows() and widget is not None:
        return _windows_ime_alphanumeric(int(widget.window().winId()))
    return False


def _windows_ime_alphanumeric(hwnd):
    """Windows: 창 hwnd 의 IME 를 영숫자(한/영 키의 '영') 상태로"""
    try:
        import ctypes
        imm = ctypes.windll.imm32
        imm.ImmGetContext.restype = ctypes.c_void_p
        imm.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm.ImmSetConversionStatus.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        himc = imm.ImmGetContext(hwnd)
        if not himc:
            return False
        try:
            return bool(imm.ImmSetConversionStatus(himc, 0, 0))   # IME_CMODE_ALPHANUMERIC · IME_SMODE_NONE
        finally:
            imm.ImmReleaseContext(hwnd, himc)
    except (OSError, AttributeError):
        return False


# ---------------------------------------------------------------- 컴퓨터 전체 무입력 시간 (자동 자리비움)
_IDLE_CACHE = {}
# CGEventType: 왼 · 오른 버튼 누름(1, 3) · 이동(5) · 끌기(6, 7) · 휠(22) · 다른 버튼 누름 · 끌기(25, 27)
MAC_MOUSE_EVENT_TYPES = (1, 3, 5, 6, 7, 22, 25, 27)


def system_idle_seconds():
    """컴퓨터 전체에서 마지막 입력 뒤 지난 초 (다른 앱에서 움직여도 센다). macOS 는 마우스 입력만,
    Windows · Linux(X11) 는 OS 가 마우스 · 키보드를 나눠 주지 않아 둘 다.
    알 수 없으면 None (Wayland 등) - 그때는 부르는 쪽이 마우스 포인터 위치 변화로 대신 본다"""
    try:
        if is_mac():
            return _mac_idle_seconds()
        if is_windows():
            return _windows_idle_seconds()
        return _x11_idle_seconds()
    except (OSError, AttributeError, ValueError, TypeError):
        return None


def _mac_idle_seconds():
    import ctypes
    if 'cg' not in _IDLE_CACHE:
        cg = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
        cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        _IDLE_CACHE['cg'] = cg
    # 마우스 입력만 센다 (요청: 마우스를 움직이면 온라인 · 10분 안 움직이면 자리비움). 키보드는 원격 제어 ·
    # 입력 보조 프로그램이 쉬지 않고 키 이벤트를 만들어 자리비움이 영영 안 되는 일이 있었다 (2026-10-11 실측).
    # 상태 0 = kCGEventSourceStateCombinedSessionState
    get = _IDLE_CACHE['cg'].CGEventSourceSecondsSinceLastEventType
    return max(0.0, min(float(get(0, kind)) for kind in MAC_MOUSE_EVENT_TYPES))


def _windows_idle_seconds():
    import ctypes

    class LastInputInfo(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint32)]

    info = LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0     # 49일마다 0 으로 돌아가는 틱 보정


def _x11_idle_seconds():
    import ctypes
    import ctypes.util
    if not os.environ.get('DISPLAY') or os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
        return None
    if 'xss' not in _IDLE_CACHE:
        x11_path, xss_path = ctypes.util.find_library('X11'), ctypes.util.find_library('Xss')
        if not x11_path or not xss_path:
            _IDLE_CACHE['xss'] = None
        else:
            x11, xss = ctypes.cdll.LoadLibrary(x11_path), ctypes.cdll.LoadLibrary(xss_path)
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverAllocInfo.restype = ctypes.c_void_p
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
            display = x11.XOpenDisplay(None)
            _IDLE_CACHE['xss'] = (x11, xss, display, xss.XScreenSaverAllocInfo()) if display else None
    cached = _IDLE_CACHE['xss']
    if not cached:
        return None
    x11, xss, display, info = cached

    class ScreenSaverInfo(ctypes.Structure):
        _fields_ = [('window', ctypes.c_ulong), ('state', ctypes.c_int), ('kind', ctypes.c_int),
                    ('til_or_since', ctypes.c_ulong), ('idle', ctypes.c_ulong), ('eventMask', ctypes.c_ulong)]

    if not xss.XScreenSaverQueryInfo(display, x11.XDefaultRootWindow(display), info):
        return None
    return ScreenSaverInfo.from_address(info).idle / 1000.0


# ---------------------------------------------------------------- 다시 실행
# PyInstaller 6 단일 실행파일(onefile)은 sys.executable 로 띄운 새 복사본을 '같은 앱의 하위 프로세스'로 보고 부모가
# 풀어 둔 임시 폴더(_MEIxxxx)를 그대로 쓰게 한다. 로그아웃 → 다시 로그인처럼 새 인스턴스를 띄우고 원래 인스턴스가
# 종료하면 그 임시 폴더가 지워져 새 인스턴스가 "No such file or directory: ..._MEIxxxx\base_library.zip" 으로 죽는다.
# 이 변수를 1 로 주면 부트로더가 환경을 초기화하고 자기 임시 폴더를 새로 푼다 (bootloader pyi_main.c).
PYINSTALLER_RESET_ENV = 'PYINSTALLER_RESET_ENVIRONMENT'


def relaunch_command(argv=None):
    """(프로그램, 인자) - 지금 앱을 같은 방식으로 다시 띄우는 명령. 빌드한 앱이면 실행 파일 그대로.
    빌드한 앱에서는 새 인스턴스가 독립된 임시 폴더를 쓰도록 환경 변수 PYINSTALLER_RESET_ENVIRONMENT=1 도 둔다
    (startDetached 는 지금 프로세스 환경을 물려준다)"""
    argv = list(sys.argv if argv is None else argv)
    if getattr(sys, 'frozen', False):
        os.environ[PYINSTALLER_RESET_ENV] = '1'
        return sys.executable, argv[1:]
    return sys.executable, [os.path.abspath(argv[0])] + argv[1:]
