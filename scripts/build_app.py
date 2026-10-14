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
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

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
SIGN_ENV = 'KCS_SIGN_IDENTITY'      # "Developer ID Application: ..." (없으면 ad-hoc '-')
NOTARY_ENV = 'KCS_NOTARY_PROFILE'   # xcrun notarytool 키체인 프로필 이름
ENTITLEMENTS = os.path.join(ROOT, 'assets', 'macos', 'entitlements.plist')
DOWNLOAD_ENV = 'KCS_DOWNLOAD_URL'   # DMG 를 올리는 포털 폴더 주소
DOWNLOAD_URL = 'https://portal.kcins.co.kr/downloads/kcstalk/'
INSTALLER_NAME = 'install_kcstalk.sh'
# 공증 없이도 Gatekeeper 경고 없이 설치하는 스크립트. 브라우저로 받은 파일에만 다운로드 표시(com.apple.quarantine)가
# 붙고 Gatekeeper 는 그 표시가 있는 앱만 검사한다 → curl 로 받아 설치하면 'Apple은 ... 확인할 수 없습니다' 창이 뜨지 않는다.
# 사용:  curl -fsSL <포털>/install_kcstalk.sh | bash      (KCS_INSTALL_DIR 로 설치 폴더 변경 · 기본 /Applications)
INSTALLER = r"""#!/bin/bash
# KCS Talk macOS 설치 - kcs.py build --dmg 가 만든다 (DMG: {dmg})
set -euo pipefail
URL="${{KCS_DMG_URL:-{url}}}"
SHA256="{sha256}"
DEST="${{KCS_INSTALL_DIR:-/Applications}}"
APP="{app}"
WORK="$(mktemp -d /tmp/kcstalk.XXXXXX)"
cleanup() {{ hdiutil detach "$WORK/mnt" -quiet >/dev/null 2>&1 || true; rm -rf "$WORK"; }}
trap cleanup EXIT
echo "KCS Talk 받는 중... $URL"
curl -fL --progress-bar -o "$WORK/kcstalk.dmg" "$URL"
if [ "$(shasum -a 256 "$WORK/kcstalk.dmg" | cut -d' ' -f1)" != "$SHA256" ]; then
  echo "받은 파일이 손상되었거나 다른 파일입니다 (SHA-256 불일치). 설치를 멈춥니다." >&2; exit 1
fi
mkdir -p "$WORK/mnt"
hdiutil attach -nobrowse -readonly -quiet -mountpoint "$WORK/mnt" "$WORK/kcstalk.dmg"
SUDO=""
if [ ! -w "$DEST" ] || {{ [ -e "$DEST/$APP" ] && [ ! -w "$DEST/$APP" ]; }}; then SUDO="sudo"; echo "$DEST 에 설치하려면 Mac 암호가 필요합니다."; fi
pkill -x "{name}" >/dev/null 2>&1 || true
$SUDO rm -rf "$DEST/$APP"
$SUDO ditto "$WORK/mnt/$APP" "$DEST/$APP"
$SUDO xattr -dr com.apple.quarantine "$DEST/$APP" 2>/dev/null || true
echo "설치 완료 → $DEST/$APP"
[ -n "${{KCS_NO_OPEN:-}}" ] || open "$DEST/$APP"
"""
# PyInstaller 가 만든 Info.plist 에 더하는 항목 - 카메라 · 마이크 설명이 없으면 페이스톡에서 앱이 강제 종료된다
INFO_PLIST_EXTRA = {
    'CFBundleDisplayName': CLIENT_NAME,
    'NSHighResolutionCapable': True,
    'NSCameraUsageDescription': 'KCS Talk 페이스톡에서 카메라를 사용합니다.',
    'NSMicrophoneUsageDescription': 'KCS Talk 페이스톡 · 음성 메시지에서 마이크를 사용합니다.',
}
INSTALL_GUIDE = """KCS Talk 설치 (macOS)

1. 'KCS Talk' 아이콘을 옆의 'Applications(응용 프로그램)' 폴더로 끌어다 놓습니다.
2. 응용 프로그램 폴더(또는 Launchpad)에서 KCS Talk 을 엽니다.
{blocked}"""
BLOCKED_GUIDE = """
'Apple은 KCS Talk.app에 ... 악성 코드가 없음을 확인할 수 없습니다' 창이 뜨면 [완료]를 누르고:
  - 시스템 설정 → 개인정보 보호 및 보안 → 맨 아래 'KCS Talk.app이(가) 차단되었습니다' 옆 [그래도 열기]
    → Mac 암호 입력 → 다시 뜨는 창에서 [그래도 열기]  (처음 한 번만 하면 됩니다)
  - 또는 터미널에서:  xattr -dr com.apple.quarantine "/Applications/KCS Talk.app"
  - 다음부터는 터미널 한 줄로 경고 없이 설치/업데이트:  curl -fsSL {installer} | bash
"""


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


def is_developer_id(identity):
    """공증 가능한 Developer ID 인증서인가 (ad-hoc '-' · Apple Development 는 배포용이 아니다)"""
    return bool(identity) and identity.startswith('Developer ID Application')


def patch_info_plist(app):
    """앱 Info.plist 에 카메라 · 마이크 설명 등을 더한다 (서명 전에 - 고치면 서명이 깨진다)"""
    path = os.path.join(app, 'Contents', 'Info.plist')
    with open(path, 'rb') as f:
        info = plistlib.load(f)
    info.update(INFO_PLIST_EXTRA)
    with open(path, 'wb') as f:
        plistlib.dump(info, f)


def sign_command(target, identity, entitlements=None, deep=False):
    """codesign 명령. Developer ID 면 공증 조건(hardened runtime · 보안 타임스탬프)을 붙인다"""
    cmd = ['codesign', '--force', '--sign', identity or '-']
    if deep:
        cmd.append('--deep')
    if is_developer_id(identity):
        cmd += ['--timestamp', '--options', 'runtime']
        if entitlements:
            cmd += ['--entitlements', entitlements]
    return cmd + [target]


def dmg_name():
    """포털에 올리는 이름 규칙: 202610141250_kcsmessager.dmg"""
    return time.strftime('%Y%m%d%H%M') + '_kcsmessager.dmg'


def stage_dmg(app, staging, notarized):
    """DMG 안에 들어갈 폴더: 앱 · 응용 프로그램 바로가기 · 설치 안내"""
    target = os.path.join(staging, os.path.basename(app))
    subprocess.check_call(['ditto', app, target])          # 심볼릭 링크 · 확장 속성 · 서명 보존 복사
    os.symlink('/Applications', os.path.join(staging, 'Applications'))
    with open(os.path.join(staging, '설치 안내.txt'), 'w', encoding='utf-8') as f:
        base = (os.environ.get(DOWNLOAD_ENV) or DOWNLOAD_URL).rstrip('/') + '/'
        f.write(INSTALL_GUIDE.format(blocked='' if notarized else BLOCKED_GUIDE.format(installer=base + INSTALLER_NAME)))


def dmg_commands(staging, dmg, identity, notary_profile):
    """DMG 만들기 → (Developer ID) 서명 → (프로필) 공증 · 스테이플"""
    cmds = [['hdiutil', 'create', '-volname', CLIENT_NAME, '-srcfolder', staging, '-fs', 'HFS+',
             '-format', 'UDZO', '-ov', dmg]]
    if is_developer_id(identity):
        cmds.append(sign_command(dmg, identity))
        if notary_profile:
            cmds.append(['xcrun', 'notarytool', 'submit', dmg, '--keychain-profile', notary_profile, '--wait'])
            cmds.append(['xcrun', 'stapler', 'staple', dmg])
    return cmds


def gatekeeper_ok(dmg):
    """받은 PC 의 Gatekeeper 와 같은 판정 (공증된 DMG 만 accepted)"""
    result = subprocess.run(['spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature',
                             '-vv', dmg], capture_output=True, text=True)
    print((result.stdout + result.stderr).strip())
    return result.returncode == 0


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def installer_script(dmg, sha256, base_url=None):
    """DMG 를 curl 로 받아 설치하는 셸 스크립트 (포털에 DMG 와 함께 올린다)"""
    base = (base_url or os.environ.get(DOWNLOAD_ENV) or DOWNLOAD_URL).rstrip('/') + '/'
    return INSTALLER.format(dmg=os.path.basename(dmg), url=base + os.path.basename(dmg), sha256=sha256,
                            app=f'{CLIENT_NAME}.app', name=CLIENT_NAME)


def write_installer(dmg, base_url=None):
    path = os.path.join(os.path.dirname(dmg), INSTALLER_NAME)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(installer_script(dmg, sha256_of(dmg), base_url))
    os.chmod(path, 0o755)
    return path


def package_mac(out_dir, identity, notary_profile, dry_run=False):
    """dist/KCS Talk.app → 서명 → DMG → 공증. 결과 DMG 경로 (실패하면 None)"""
    app = os.path.join(out_dir, f'{CLIENT_NAME}.app')
    dmg = os.path.join(out_dir, dmg_name())
    notarize = is_developer_id(identity) and bool(notary_profile)
    plan = [sign_command(app, identity, ENTITLEMENTS, deep=True)] + dmg_commands('<staging>', dmg, identity, notary_profile)
    for cmd in plan:
        print('  ' + ' '.join(shlex.quote(c) for c in cmd), flush=True)
    if dry_run:
        return dmg
    patch_info_plist(app)
    subprocess.check_call(sign_command(app, identity, ENTITLEMENTS, deep=True))
    subprocess.check_call(['codesign', '--verify', '--deep', '--strict', app])
    staging = tempfile.mkdtemp(prefix='kcs-dmg-')
    try:
        stage_dmg(app, staging, notarize)
        for cmd in dmg_commands(staging, dmg, identity, notary_profile):
            subprocess.check_call(cmd)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f'DMG → {dmg}')
    installer = write_installer(dmg)
    base = (os.environ.get(DOWNLOAD_ENV) or DOWNLOAD_URL).rstrip('/') + '/'
    print(f'설치 스크립트 → {installer}\n  DMG 와 같은 포털 폴더에 올리고 Mac 사용자에게 터미널 한 줄 안내 (경고 창 없이 설치):\n'
          f'  curl -fsSL {base}{INSTALLER_NAME} | bash')
    if notarize and gatekeeper_ok(dmg):
        print('공증 완료: 다른 Mac 에서 받아도 바로 열립니다.')
    elif not is_developer_id(identity):
        print(f'주의: {SIGN_ENV} 에 Developer ID Application 인증서가 없어 ad-hoc 서명입니다. 포털 등에서 받은 Mac 은 '
              "'악성 코드가 없음을 확인할 수 없습니다'로 막힙니다 → 위 curl 설치 한 줄을 안내하세요.",
              file=sys.stderr)
    else:
        print(f'주의: {NOTARY_ENV} 가 없거나 공증이 끝나지 않아 받은 Mac 에서 막힐 수 있습니다.', file=sys.stderr)
    return dmg


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
    if args.dmg and (args.server or not osenv.is_mac()):
        print('--dmg 는 macOS 에서 메신저를 만들 때만 쓸 수 있습니다.', file=sys.stderr)
        return 2
    identity = os.environ.get(SIGN_ENV, '').strip() or '-'
    out_dir = os.path.abspath(args.out)
    try:
        deploy = parse_deploy(args.deploy) if args.deploy else None
    except ValueError as exc:
        parser.error(str(exc))
    cmd = [sys.executable, '-m', 'PyInstaller'] + pyinstaller_args(
        server=args.server, out=out_dir, onefile=args.onefile, deploy=deploy, deploy_tls=not args.deploy_plain)
    printable = subprocess.list2cmdline(cmd) if osenv.is_windows() else ' '.join(shlex.quote(c) for c in cmd)
    print(f'[{osenv.os_label()}] {printable}', flush=True)
    if args.dry_run:
        if args.dmg:
            package_mac(out_dir, identity, os.environ.get(NOTARY_ENV), dry_run=True)
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
