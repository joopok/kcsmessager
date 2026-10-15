#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KCS Talk 공통 실행기 - Windows · macOS · Linux 에서 똑같이 쓴다.

    python kcs.py client [--host 127.0.0.1 --port 5555 ...]   메신저 실행
    python kcs.py server [--host 0.0.0.0 --port 5555 ...]     채팅 서버 실행
    python kcs.py doctor                                       이 PC 에서 돌릴 준비가 됐는지 점검
    python kcs.py build [--server] [--dry-run]                 이 OS 용 실행 파일 만들기 (PyInstaller)
    python kcs.py shortcut [--dry-run]                         바탕화면/시작 메뉴/앱 목록에 바로가기 만들기

Windows 는 `py kcs.py …`, macOS · Linux 는 `python3 kcs.py …`. 더블클릭용 파일은 launch/ 폴더에 있다.
"""

import argparse
import importlib
import os
import importlib.util
import platform
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MIN_PYTHON = (3, 8)
# (모듈, 필수?, 없으면 안 되는 기능)
DEPENDENCIES = [
    ('PyQt5', True, '화면 전체'),
    ('cryptography', True, '비밀채팅 암호화'),
    ('PyQt5.QtMultimedia', False, '보이스톡 · 음성메시지'),
    ('numpy', False, '통화 소리 처리 (보이스톡 품질)'),
    ('pypdf', False, 'AI 비서의 PDF 학습 (서버)'),
    ('anthropic', False, 'AI 비서 답변 (서버, ANTHROPIC_API_KEY 필요)'),
    ('openpyxl', False, '조직도 엑셀 가져오기 (관리자)'),
]


def run_client(args):
    from client.kakao.app import main
    return main(args)


def run_server(args):
    from server.chat.chat_server import main
    return main(args) or 0


def doctor(_args=None, out=print):
    """실행 준비 점검. 필수 항목이 빠졌으면 1"""
    from client.kakao import osenv
    ok = True
    out(f'KCS Talk 점검 - {osenv.os_label()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}')
    if sys.version_info < MIN_PYTHON:
        out(f'  ✗ Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 이상이 필요합니다')
        ok = False
    for module, required, feature in DEPENDENCIES:
        try:
            importlib.import_module(module)
            out(f'  ✓ {module}')
        except ImportError:
            mark = '✗' if required else '·'
            out(f'  {mark} {module} 없음 - {feature}' + ('' if required else ' (선택)'))
            ok = ok and not required
    from client.kakao.theme import BUNDLED_FONTS
    fonts = [f for f in BUNDLED_FONTS if os.path.exists(osenv.resource_path('assets', 'fonts', f))]
    total = len(BUNDLED_FONTS)
    out(f"  {'✓' if len(fonts) == total else '✗'} 앱 글꼴 Poppins · KoPub 돋움 {len(fonts)}/{total}")
    ok = ok and len(fonts) == total
    home = osenv.app_home()
    try:
        os.makedirs(home, exist_ok=True)
        writable = os.access(home, os.W_OK)
    except OSError:
        writable = False
    out(f"  {'✓' if writable else '✗'} 데이터 폴더 {home}")
    ok = ok and writable
    if osenv.is_linux():
        missing = [lib for lib in ('libxcb-xinerama.so.0', 'libxkbcommon-x11.so.0') if not _linux_lib(lib)]
        if missing:
            out('  · Qt 화면 라이브러리가 없을 수 있습니다: ' + ', '.join(missing)
                + ' (Ubuntu: sudo apt install libxcb-xinerama0 libxkbcommon-x11-0)')
    out('준비 완료' if ok else '빠진 항목을 설치하세요: ' + install_hint())
    return 0 if ok else 1


def _linux_lib(name):
    import ctypes.util
    return bool(ctypes.util.find_library(name.split('.so')[0].replace('lib', '', 1))) or any(
        os.path.exists(os.path.join(d, name)) for d in ('/usr/lib', '/usr/lib64', '/usr/lib/x86_64-linux-gnu',
                                                        '/usr/lib/aarch64-linux-gnu'))


def install_hint():
    from client.kakao import osenv
    py = 'py' if osenv.is_windows() else 'python3'
    return f'{py} -m pip install -r requirements-kcs.txt'


def _script(name):
    """scripts/<name>.py 를 경로로 불러온다 (site-packages 의 다른 'scripts' 패키지에 가려지지 않게)"""
    spec = importlib.util.spec_from_file_location(f'kcs_{name}', os.path.join(ROOT, 'scripts', f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(args):
    return _script('build_app').main(args)


def shortcut(args):
    return _script('make_shortcut').main(args)


COMMANDS = {'client': run_client, 'server': run_server, 'doctor': doctor, 'build': build, 'shortcut': shortcut}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='kcs.py', description='KCS Talk 공통 실행기 (Windows · macOS · Linux)')
    parser.add_argument('command', nargs='?', default='client', choices=sorted(COMMANDS),
                        help='client(기본) · server · doctor · build · shortcut')
    if argv and argv[0] in ('-h', '--help'):
        parser.print_help()
        return 0
    command = argv[0] if argv and not argv[0].startswith('-') else 'client'
    rest = argv[1:] if argv and not argv[0].startswith('-') else argv
    if command not in COMMANDS:
        parser.parse_args([command])        # 알 수 없는 명령 → 사용법 출력 후 종료
    return COMMANDS[command](rest)


if __name__ == '__main__':
    sys.exit(main())
