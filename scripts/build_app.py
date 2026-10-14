# -*- coding: utf-8 -*-
"""
이 OS 용 KCS Talk 실행 파일 만들기 (PyInstaller) - 같은 명령이 세 OS 에서 각자 맞는 결과를 낸다.

    python kcs.py build              메신저:  macOS dist/KCS Talk.app · Windows dist/KCS Talk/KCS Talk.exe
                                              · Linux dist/KCS Talk/KCS Talk (+ kr.co.kcs.talk.desktop)
    python kcs.py build --server     채팅 서버(콘솔): dist/KCS Talk Server/
    python kcs.py build --dry-run    실행하지 않고 PyInstaller 명령만 출력
    python kcs.py build --onefile --deploy talk.kcins.co.kr:9550
                                     배포용 단일 실행 파일 + 운영 서버 주소(TLS) 를 앱 안에 번들
                                     (deploy/deploy.json → client.kakao.settings 가 server.json 다음 순위로 읽음)

PyInstaller 는 교차 빌드를 하지 않는다: Windows 용은 Windows 에서, macOS 용은 macOS 에서, Linux 용은 Linux 에서 만든다.
필요: python -m pip install pyinstaller  (requirements-kcs.txt 의 빌드 항목)
"""

import argparse
import os
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from client.kakao import osenv  # noqa: E402

CLIENT_NAME = osenv.APP_NAME
SERVER_NAME = f'{osenv.APP_NAME} Server'
# 앱에 함께 넣는 파일 (원본 경로, 앱 안 경로) - osenv.resource_path 가 같은 상대 경로로 찾는다
CLIENT_DATA = [('assets/fonts', 'assets/fonts'), ('assets/brand', 'assets/brand'), ('assets/icon', 'assets/icon')]
SERVER_DATA = []     # 서버 데이터(DB · org.json)는 실행 파일 옆 server_data/ 에 둔다 - 앱 안에 넣지 않는다
HIDDEN = ['PyQt5.QtMultimedia', 'cryptography.hazmat.primitives.ciphers.aead']


def icon_for(os_name):
    """이 OS 아이콘 파일 (없으면 None) - scripts/make_app_icon.py 가 만든다"""
    name = {'mac': 'icon.icns', 'windows': 'icon.ico', 'linux': os.path.join('icon', 'app_icon_256.png')}[os_name]
    path = os.path.join(ROOT, 'assets', name)
    return path if os.path.exists(path) else None


def parse_deploy(value):
    """'host:port' → {'host', 'port'}  (틀리면 ValueError 메시지)"""
    host, _, port = (value or '').rpartition(':')
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(f'--deploy 는 호스트:포트 형식이어야 합니다 (예: talk.kcins.co.kr:9550): {value!r}')
    return {'host': host, 'port': int(port)}


def write_deploy_file(out, deploy, tls=True):
    """빌드에 번들할 deploy.json 을 만든다 → 파일 경로. PyInstaller --clean 이 지우는 workpath(build/pyinstaller)
    바깥(build/deploy)에 둔다 - 안에 두면 Analysis 전에 삭제되어 --add-data 가 실패한다."""
    import json
    path = os.path.join(os.path.dirname(out), 'build', 'deploy', 'deploy.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'host': deploy['host'], 'port': deploy['port'], 'tls': bool(tls)}, f, ensure_ascii=False)
    return path


def pyinstaller_args(server=False, os_name=None, out=None, onefile=False, deploy=None, deploy_tls=True):
    """PyInstaller 인자 목록 (OS 별 차이는 여기에만). out: 결과 폴더 (기본 저장소의 dist/)
    onefile: 단일 실행 파일(배포용). deploy: {'host','port'} 이면 deploy.json 을 번들해 운영 서버 주소를 심는다."""
    os_name = os_name or osenv.current()
    out = out or os.path.join(ROOT, 'dist')
    work = os.path.join(os.path.dirname(out), 'build', 'pyinstaller')
    sep = ';' if os_name == 'windows' else ':'          # --add-data 구분자
    name = SERVER_NAME if server else CLIENT_NAME
    entry = os.path.join(ROOT, 'server', 'chat', 'chat_server.py') if server else os.path.join(ROOT, 'client', 'kakao', 'app.py')
    args = ['--noconfirm', '--clean', '--name', name, '--paths', ROOT,
            '--distpath', out, '--workpath', work, '--specpath', work]
    if onefile:
        args.append('--onefile')
    args.append('--console' if server else '--windowed')  # 메신저는 콘솔 창 없이
    for src, dest in (SERVER_DATA if server else CLIENT_DATA):
        args += ['--add-data', f'{os.path.join(ROOT, src)}{sep}{dest}']
    if deploy and not server:
        args += ['--add-data', f'{write_deploy_file(out, deploy, deploy_tls)}{sep}deploy']
    if not server:
        for module in HIDDEN:
            args += ['--hidden-import', module]
    icon = icon_for(os_name)
    if icon and not (server and os_name == 'linux'):
        args += ['--icon', icon]
    if os_name == 'mac' and not server:
        args += ['--osx-bundle-identifier', osenv.APP_ID]
    args.append(entry)
    return args


def desktop_entry(exec_path, icon_path):
    """Linux 앱 목록용 .desktop 내용"""
    return '\n'.join([
        '[Desktop Entry]', 'Type=Application', f'Name={CLIENT_NAME}', 'Name[ko]=KCS Talk',
        'Comment=케이씨에스 사내 메신저', f'Exec="{exec_path}"', f'Icon={icon_path}',
        'Terminal=false', 'Categories=Network;InstantMessaging;Chat;', f'StartupWMClass={CLIENT_NAME}',
        'StartupNotify=true', '']) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kcs.py build', description='이 OS 용 KCS Talk 실행 파일 만들기')
    parser.add_argument('--server', action='store_true', help='채팅 서버를 만든다 (기본: 메신저)')
    parser.add_argument('--dry-run', action='store_true', help='PyInstaller 명령만 출력')
    parser.add_argument('--out', default=os.path.join(ROOT, 'dist'), help='결과 폴더 (기본 dist/)')
    parser.add_argument('--onefile', action='store_true',
                        help='단일 실행 파일로 (배포용. 시작 시 임시 폴더에 풀리므로 첫 실행이 조금 느리다)')
    parser.add_argument('--deploy', metavar='HOST:PORT', default=None,
                        help='운영 서버 주소를 앱 안에 번들 (예: talk.kcins.co.kr:9550). 설치형 배포본이 바로 접속하게 한다')
    parser.add_argument('--deploy-plain', action='store_true',
                        help='--deploy 주소를 평문 TCP 로 (기본은 TLS - nginx 가 TLS 를 종단하는 운영 서버)')
    args = parser.parse_args(argv)
    out_dir = os.path.abspath(args.out)
    try:
        deploy = parse_deploy(args.deploy) if args.deploy else None
    except ValueError as exc:
        parser.error(str(exc))
    cmd = [sys.executable, '-m', 'PyInstaller'] + pyinstaller_args(
        server=args.server, out=out_dir, onefile=args.onefile, deploy=deploy, deploy_tls=not args.deploy_plain)
    printable = subprocess.list2cmdline(cmd) if osenv.is_windows() else ' '.join(shlex.quote(c) for c in cmd)
    print(f'[{osenv.os_label()}] {printable}')
    if args.dry_run:
        return 0
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print('PyInstaller 가 없습니다: python -m pip install pyinstaller', file=sys.stderr)
        return 1
    code = subprocess.call(cmd, cwd=ROOT)
    if code == 0 and osenv.is_linux() and not args.server and not args.onefile:   # .desktop 은 onedir 구조 전제
        app_dir = os.path.join(out_dir, CLIENT_NAME)
        path = os.path.join(app_dir, f'{osenv.DESKTOP_FILE}.desktop')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(desktop_entry(os.path.join(app_dir, CLIENT_NAME),
                                  os.path.join(app_dir, '_internal', 'assets', 'icon', 'app_icon_256.png')))
        print(f'데스크톱 파일 → {path}  (python kcs.py shortcut 로 앱 목록에 등록)')
    if code == 0:
        if args.onefile:   # 단일 파일: dist/ 바로 아래 (macOS 는 --windowed 라 그래도 .app 번들)
            result = {'mac': f'{CLIENT_NAME}.app', 'windows': f'{CLIENT_NAME}.exe', 'linux': CLIENT_NAME}[osenv.current()]
        else:
            result = {'mac': f'{CLIENT_NAME}.app', 'windows': os.path.join(CLIENT_NAME, f'{CLIENT_NAME}.exe'),
                      'linux': os.path.join(CLIENT_NAME, CLIENT_NAME)}[osenv.current()]
        print('완료 → ' + os.path.join(out_dir, SERVER_NAME if args.server else result))
        if deploy:
            print(f'번들된 접속 기본값: {deploy["host"]}:{deploy["port"]} ({"평문" if args.deploy_plain else "TLS"})')
    return code


if __name__ == '__main__':
    sys.exit(main())
