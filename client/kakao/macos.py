# -*- coding: utf-8 -*-
"""
macOS 전용 보정 - python3 로 실행하면 Dock·메뉴 막대에 'Python' 으로 나오는 것을 앱 이름으로 바꾼다.
pyobjc 없이 ctypes 만 쓴다.
- run_in_app_bundle: '<이름>.app' 번들 안에서 다시 실행한다 (Dock 툴팁 · 앱 전환기). QApplication 을 만들기 전에 부른다.
  요즘 macOS 의 Dock 은 LaunchServices 표시 이름(set_dock_name)을 무시하고 실행 파일이 든 번들 이름만 보여 준다.
- set_app_name: main bundle 의 CFBundleName 을 덮어쓴다 (메뉴 막대). QApplication 을 만들기 전에 부른다.
- set_dock_name: LaunchServices 표시 이름을 바꾼다 (번들로 못 옮겼을 때 예전 macOS 의 Dock 툴팁). QApplication 다음에.
- select_ascii_input: 입력 소스를 영문(ABC 등 마지막으로 쓴 영문 자판)으로 바꾼다 - 사번 칸에 포커스가 올 때.
- unify_title_bar: 창의 제목 표시줄을 투명하게 해 본문 배경이 창 맨 위(신호등 버튼 뒤)까지 이어지게 한다.
- match_title_bar: 대화상자 제목 표시줄을 본문 색 · 앱 테마(밝게/어둡게)에 맞춘다.
  Dock 은 실행 파일 묶음(Python.app)의 이름을 LaunchServices 에서 읽으므로 CFBundleName 만으로는 'Python' 이 남는다.
macOS 가 아니거나 실패하면 아무것도 하지 않는다.
"""

import ctypes
import ctypes.util
import os
import plistlib
import sys

BUNDLE_DIR = 'macos'   # 데이터 폴더 아래 '<번들 ID>/<이름>.app' 을 두는 곳 (이름이 같아도 앱마다 따로)


def run_in_app_bundle(name, bundle_id, home):
    """소스 실행(python3 kcs.py)일 때 home/macos/<bundle_id>/<name>.app 번들 안에서 이 프로세스를 다시 실행해 Dock 툴팁 ·
    Cmd+Tab 에 'Python' 대신 name 이 보이게 한다. 번들 실행 파일은 지금 파이썬 실행 파일로의 심볼릭 링크라
    파이썬 · 패키지는 그대로다. 이미 번들 안이거나 할 수 없으면 아무것도 하지 않고 False (성공하면 돌아오지 않는다)"""
    if (sys.platform != 'darwin' or getattr(sys, 'frozen', False) or os.environ.get('KCS_NO_APP_BUNDLE')
            or os.environ.get('QT_QPA_PLATFORM', 'cocoa') != 'cocoa'):   # 테스트(offscreen)는 그대로
        return False
    running = _executable_path()
    exe = os.path.join(os.path.abspath(home), BUNDLE_DIR, bundle_id, f'{name}.app', 'Contents', 'MacOS', name)
    if not running or os.path.normpath(running) == exe:
        return False
    try:
        _write_bundle(exe, name, bundle_id, os.path.realpath(running))
        env = dict(os.environ)
        if sys.prefix != sys.base_prefix:          # 가상환경: 링크는 기본 파이썬을 가리키므로 패키지 폴더를 넘긴다
            import site
            env['PYTHONPATH'] = os.pathsep.join(site.getsitepackages() + [p for p in [env.get('PYTHONPATH')] if p])
        args = list(getattr(sys, 'orig_argv', None) or [sys.executable] + sys.argv)
        for stream in (sys.stdout, sys.stderr):
            if stream:
                stream.flush()
        os.execve(exe, [exe] + args[1:], env)
    except OSError:
        return False


def _write_bundle(exe, name, bundle_id, target):
    """name.app 을 만들거나 고친다 - 바뀐 것이 없으면 건드리지 않는다 (LaunchServices 재등록 줄이기)"""
    contents = os.path.dirname(os.path.dirname(exe))
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    if not os.path.islink(exe) or os.readlink(exe) != target:
        if os.path.lexists(exe):
            os.remove(exe)
        os.symlink(target, exe)
    info = {
        'CFBundleName': name, 'CFBundleDisplayName': name, 'CFBundleExecutable': name,
        'CFBundleIdentifier': bundle_id, 'CFBundlePackageType': 'APPL', 'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleShortVersionString': '2.0', 'CFBundleVersion': '2.0',
        'NSHighResolutionCapable': True, 'NSSupportsAutomaticGraphicsSwitching': True,
        'NSMicrophoneUsageDescription': '음성 · 영상 통화에 마이크를 사용합니다.',
        'NSCameraUsageDescription': '영상 통화에 카메라를 사용합니다.',
    }
    plist = os.path.join(contents, 'Info.plist')
    data = plistlib.dumps(info)
    try:
        with open(plist, 'rb') as f:
            if f.read() == data:
                return
    except OSError:
        pass
    with open(plist, 'wb') as f:
        f.write(data)


def _executable_path():
    """이 프로세스의 실제 실행 파일 경로 (dyld 기준 - sys.executable 은 bin/python3 런처일 수 있다). 모르면 ''"""
    try:
        libc = ctypes.CDLL(None)
        size = ctypes.c_uint32(0)
        libc._NSGetExecutablePath(None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value + 1)
        if libc._NSGetExecutablePath(buf, ctypes.byref(size)) != 0:
            return ''
        return os.path.abspath(buf.value.decode('utf-8'))
    except (OSError, AttributeError, UnicodeDecodeError):
        return ''


def set_app_name(name):
    """Dock 툴팁 · 메뉴 막대 앱 이름을 name 으로. 바꿨으면 True"""
    if sys.platform != 'darwin' or getattr(sys, 'frozen', False):   # 빌드한 .app 은 Info.plist 이름을 쓴다
        return False
    objc_path, foundation_path = ctypes.util.find_library('objc'), ctypes.util.find_library('Foundation')
    if not objc_path or not foundation_path:
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(objc_path)
        ctypes.cdll.LoadLibrary(foundation_path)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend

        def call(obj, selector, *args, argtypes=()):
            send.restype = ctypes.c_void_p
            send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
            return send(obj, objc.sel_registerName(selector.encode()), *args)

        def nsstring(text):
            return call(objc.objc_getClass(b'NSString'), 'stringWithUTF8String:', text.encode('utf-8'),
                        argtypes=(ctypes.c_char_p,))

        bundle = call(objc.objc_getClass(b'NSBundle'), 'mainBundle')
        info = call(bundle, 'infoDictionary') if bundle else None
        if not info:
            return False
        for key in ('CFBundleName', 'CFBundleDisplayName'):
            call(info, 'setObject:forKey:', nsstring(name), nsstring(key), argtypes=(ctypes.c_void_p, ctypes.c_void_p))
        return True
    except (OSError, AttributeError, TypeError):
        return False


DOCK_NAME = 'KCS Talk'   # Dock 아이콘에 마우스를 올리면 보이는 이름 (사용자에게 보이는 제품 이름)
_LAUNCH_SERVICES = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/LaunchServices'
_CORE_FOUNDATION = '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
_UTF8 = 0x08000100              # kCFStringEncodingUTF8
_DEFAULT_SESSION = -2           # kLSDefaultSessionID


def set_dock_name(name=DOCK_NAME):
    """Dock 툴팁 · Cmd+Tab 앱 전환기 이름을 name 으로 (QApplication 생성 뒤). 바꿨으면 True"""
    if sys.platform != 'darwin' or getattr(sys, 'frozen', False):
        return False
    try:
        ls = ctypes.cdll.LoadLibrary(_LAUNCH_SERVICES)
        cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION)
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        ls._LSGetCurrentApplicationASN.restype = ctypes.c_void_p
        ls._LSSetApplicationInformationItem.restype = ctypes.c_int32
        ls._LSSetApplicationInformationItem.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                                        ctypes.c_void_p, ctypes.c_void_p]
        key = ctypes.c_void_p.in_dll(ls, '_kLSDisplayNameKey').value
        asn = ls._LSGetCurrentApplicationASN()
        if not key or not asn:
            return False
        value = cf.CFStringCreateWithCString(None, name.encode('utf-8'), _UTF8)
        return bool(value) and ls._LSSetApplicationInformationItem(_DEFAULT_SESSION, asn, key, value, None) == 0
    except (OSError, AttributeError, ValueError):
        return False


_HITOOLBOX = '/System/Library/Frameworks/Carbon.framework/Frameworks/HIToolbox.framework/HIToolbox'


def select_ascii_input():
    """입력 소스가 한글 등 영문이 아닌 것이면 영문 자판(마지막으로 쓴 ABC 등)으로 바꾼다. 바꿨으면 True"""
    if sys.platform != 'darwin':
        return False
    try:
        hi = ctypes.cdll.LoadLibrary(_HITOOLBOX)
        cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION)
        for fn in (hi.TISCopyCurrentKeyboardInputSource, hi.TISCopyCurrentASCIICapableKeyboardInputSource):
            fn.restype = ctypes.c_void_p
            fn.argtypes = []
        hi.TISSelectInputSource.restype = ctypes.c_int32
        hi.TISSelectInputSource.argtypes = [ctypes.c_void_p]
        cf.CFEqual.restype = ctypes.c_bool
        cf.CFEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        current = hi.TISCopyCurrentKeyboardInputSource()
        ascii_source = hi.TISCopyCurrentASCIICapableKeyboardInputSource()
        try:
            if not ascii_source or (current and cf.CFEqual(current, ascii_source)):
                return False
            return hi.TISSelectInputSource(ascii_source) == 0
        finally:
            for ref in (current, ascii_source):
                if ref:
                    cf.CFRelease(ref)
    except (OSError, AttributeError):
        return False


NS_FULL_SIZE_CONTENT_VIEW = 1 << 15   # NSWindowStyleMaskFullSizeContentView
NS_TITLE_HIDDEN = 1                   # NSWindowTitleHidden
TITLE_BAR_HEIGHT = 28                 # 신호등 버튼이 차지하는 높이 - 이 아래부터 내용을 둔다


def unify_title_bar(widget):
    """macOS: 제목 표시줄을 투명하게 + 제목 글자 숨김 + 내용을 창 맨 위까지 (카카오톡 로그인 창처럼
    헤더와 본문이 한 배경). 창이 만들어진 뒤(show 이후) 부른다. 바꿨으면 True, macOS 가 아니면 False"""
    if sys.platform != 'darwin' or _qt_platform() != 'cocoa':
        return False
    objc_path = ctypes.util.find_library('objc')
    if not objc_path:
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(objc_path)
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend

        def call(obj, selector, *args, argtypes=(), restype=ctypes.c_void_p):
            send.restype = restype
            send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
            return send(obj, objc.sel_registerName(selector.encode()), *args)

        window = call(int(widget.winId()), 'window')
        if not window:
            return False
        pos = widget.pos()
        mask = call(window, 'styleMask', restype=ctypes.c_ulong)
        call(window, 'setStyleMask:', mask | NS_FULL_SIZE_CONTENT_VIEW, argtypes=(ctypes.c_ulong,))
        call(window, 'setTitlebarAppearsTransparent:', True, argtypes=(ctypes.c_bool,))
        call(window, 'setTitleVisibility:', NS_TITLE_HIDDEN, argtypes=(ctypes.c_long,))
        # showEvent 안(창이 아직 화면에 놓이기 전)에서 styleMask 를 바꾸면 macOS 가 창을 그 모니터 왼쪽 끝으로 옮긴다
        # (메인 창 오른쪽에 열어야 할 채팅방 창이 700px 넘게 떨어져 떴다) → 정해 둔 자리로 다시 놓는다
        if not widget.isMaximized() and not widget.isFullScreen():
            widget.move(pos)
        # setMovableByWindowBackground 는 켜지 않는다: 누른 채로 있으면 창 끌기로 잡혀 콤보 목록 등 팝업이 바로 닫힌다.
        # 창은 투명해진 제목 표시줄 줄(위 28px)을 끌어 옮길 수 있다
        return True
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def match_title_bar(widget, color, dark):
    """macOS 대화상자: 제목 표시줄을 본문과 같은 색으로. 시스템이 다크 모드면 앱이 밝은 테마여도 제목 표시줄만
    검게 그려지던 것을 막는다 - 창 모양(Aqua/DarkAqua)을 앱 테마에 맞추고, 투명 제목 표시줄 뒤 창 배경을 본문 색
    color('#RRGGBB')로 칠한다. 내용은 제목 표시줄 아래 그대로(제목 글자 · 신호등 버튼 유지). 바꿨으면 True"""
    if sys.platform != 'darwin' or _qt_platform() != 'cocoa':
        return False
    objc_path = ctypes.util.find_library('objc')
    if not objc_path or not ctypes.util.find_library('AppKit'):
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(objc_path)
        ctypes.cdll.LoadLibrary(ctypes.util.find_library('AppKit'))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend

        def call(obj, selector, *args, argtypes=(), restype=ctypes.c_void_p):
            send.restype = restype
            send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
            return send(obj, objc.sel_registerName(selector.encode()), *args)

        window = call(int(widget.winId()), 'window')
        if not window:
            return False
        name = call(objc.objc_getClass(b'NSString'), 'stringWithUTF8String:',
                    b'NSAppearanceNameDarkAqua' if dark else b'NSAppearanceNameAqua', argtypes=(ctypes.c_char_p,))
        appearance = call(objc.objc_getClass(b'NSAppearance'), 'appearanceNamed:', name, argtypes=(ctypes.c_void_p,))
        if appearance:
            call(window, 'setAppearance:', appearance, argtypes=(ctypes.c_void_p,))
        hexes = color.lstrip('#')
        r, g, b = (int(hexes[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        ns_color = call(objc.objc_getClass(b'NSColor'), 'colorWithSRGBRed:green:blue:alpha:', r, g, b, 1.0,
                        argtypes=(ctypes.c_double,) * 4)
        call(window, 'setTitlebarAppearsTransparent:', True, argtypes=(ctypes.c_bool,))
        if ns_color:
            call(window, 'setBackgroundColor:', ns_color, argtypes=(ctypes.c_void_p,))
        return True
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def _qt_platform():
    """Qt 플랫폼 이름 ('cocoa', 'offscreen' …) - QApplication 이 없으면 ''"""
    from PyQt5.QtGui import QGuiApplication
    return QGuiApplication.platformName() if QGuiApplication.instance() else ''
