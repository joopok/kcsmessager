# -*- coding: utf-8 -*-
"""
세 OS 실제 실행 점검 (GitHub Actions 의 Windows · Ubuntu · macOS 러너에서, 또는 각 PC 에서)

    python scripts/ci_smoke.py source            소스 실행: 서버를 띄우고 접속 · 클라이언트 --self-check
    python scripts/ci_smoke.py built DIST_DIR    빌드한 메신저 --self-check + 빌드한 서버 실행 · 접속 · DB 위치
    python scripts/ci_smoke.py native            그 OS 의 실제 화면 플랫폼(Windows windows · macOS cocoa · Linux xcb)으로
                                                 창 · 알림 상자 · 받은 파일 · 중복 실행 방지 · 경로 권한 · TLS 를 확인하고
                                                 모든 화면이 열리는 시간을 잰다 (화면 반응, 0.5초 넘으면 경고)
                                                 (Linux 는 화면이 없으면 xvfb-run -a 로 감싸서 실행. 창이 잠깐 떴다 닫힌다)

실패하면 종료 코드 1.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from client.kakao import osenv  # noqa: E402

NAME = osenv.APP_NAME


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_connect(port, proc, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            socket.create_connection(('127.0.0.1', port), 1).close()
            return True
        except OSError:
            time.sleep(0.5)
    return False


def check(label, ok, detail=''):
    print(f"{'✓' if ok else '✗'} {label}" + (f' - {detail}' if detail else ''), flush=True)
    return ok


def run_server(cmd, port, env=None):
    proc = subprocess.Popen(cmd + ['--port', str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        ok = wait_connect(port, proc)
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=15)[0].decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0].decode('utf-8', 'replace')
    return ok, out


def env_with_home():
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8',
               KCS_MESSENGER_HOME=tempfile.mkdtemp(prefix='kcs_ci_home_'))
    return env


def source():
    env = env_with_home()
    results = []
    db = os.path.join(tempfile.mkdtemp(prefix='kcs_ci_db_'), 'kakao.db')
    ok, out = run_server([sys.executable, 'kcs.py', 'server', '--db', db], free_port(), env)
    results.append(check(f'[{osenv.os_label()}] kcs.py server 접속', ok, '' if ok else out[-800:]))
    r = subprocess.run([sys.executable, 'kcs.py', 'client', '--self-check'], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    print(r.stdout + r.stderr)
    results.append(check('kcs.py client --self-check', r.returncode == 0))
    r = subprocess.run([sys.executable, 'kcs.py', 'doctor'], cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding='utf-8', errors='replace', timeout=120)
    print(r.stdout + r.stderr)
    results.append(check('kcs.py doctor', r.returncode == 0))
    return all(results)


def built(dist):
    env = env_with_home()
    exe = {'mac': os.path.join(dist, f'{NAME}.app', 'Contents', 'MacOS', NAME),
           'windows': os.path.join(dist, NAME, f'{NAME}.exe'),
           'linux': os.path.join(dist, NAME, NAME)}[osenv.current()]
    results = [check('빌드한 메신저 파일', os.path.exists(exe), exe)]
    if results[0]:
        log = os.path.join(env['KCS_MESSENGER_HOME'], 'logs', 'kcs_talk.log')   # 콘솔 없는 창 모드 앱의 출력
        try:
            r = subprocess.run([exe, '--self-check'], env=env, capture_output=True, timeout=120)
            code, out = r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            code, out = None, '(120초 안에 끝나지 않음)'
        if os.path.exists(log):
            with open(log, encoding='utf-8', errors='replace') as f:
                out += '\n[logs/kcs_talk.log]\n' + f.read()
        print(out)
        results.append(check('빌드한 메신저 --self-check', code == 0, f'종료 코드 {code}'))
    server_dir = os.path.join(dist, f'{NAME} Server')
    server = os.path.join(server_dir, f'{NAME} Server' + ('.exe' if osenv.is_windows() else ''))
    results.append(check('빌드한 서버 파일', os.path.exists(server), server))
    if os.path.exists(server):
        ok, out = run_server([server], free_port(), env)
        results.append(check('빌드한 서버 실행 · 접속', ok, '' if ok else out[-800:]))
        db = os.path.join(server_dir, 'server_data', 'kakao.db')
        results.append(check('서버 DB 가 실행 파일 옆에', os.path.exists(db), db))
    return all(results)


# ---------------------------------------------------------------------------------------------------------------
# native: 실제 화면 플랫폼 점검
#   단위 테스트는 화면 없는 플랫폼(offscreen)으로 돈다. 여기서는 그 OS 가 실제로 쓰는 화면 플랫폼으로 QApplication 을 띄워
#   offscreen 으로는 볼 수 없는 것(알림 상자 · 창 · OS 별 파일 처리)을 확인한다. Windows · Linux 기기가 없을 때 CI 러너가 대신한다.
#   묶음마다 프로세스를 따로 띄운다: 한 묶음이 죽어도(Windows access violation 등) 나머지 결과가 남는다.
# ---------------------------------------------------------------------------------------------------------------
NATIVE_PLATFORMS = {'windows': 'windows', 'mac': 'cocoa', 'linux': 'xcb'}


def native_platform():
    return NATIVE_PLATFORMS[osenv.current()]


def _qt_app():
    """실제 화면 플랫폼의 QApplication + 실제 앱과 같은 앱 전역 설정 (글자 보호 · 콤보 · 팝업 위치 · 메모리 정리)"""
    from PyQt5.QtWidgets import QApplication
    from client.kakao import main_window, widgets
    app = QApplication.instance() or QApplication(sys.argv[:1])
    osenv.configure_app(app)
    widgets.install_safe_gc(app)
    main_window.apply_app_theme(None)
    return app


def _wait(ms):
    from PyQt5.QtTest import QTest
    QTest.qWait(ms)


def _answer_no():
    """떠 있는 확인 창에 [아니오]를 누른다 (사람 대신)"""
    from PyQt5.QtWidgets import QApplication, QMessageBox
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMessageBox) and w.isVisible():
            button = w.button(QMessageBox.No)
            if button is not None:
                button.click()
            else:
                w.reject()


def part_windows():
    """창 · 알림 상자: 부모가 있는 알림 상자 open(), 확인 창 exec, 관리자 구성원 수정의 서버 오류 알림, 메인 창"""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget
    app = _qt_app()
    results = [check('화면 플랫폼', app.platformName() == native_platform(), app.platformName())]

    parent = QWidget()
    parent.setWindowTitle('KCS Talk 점검')
    parent.resize(480, 320)
    parent.show()
    _wait(300)
    box = QMessageBox(QMessageBox.Warning, '점검', '알림 상자를 열었다 닫습니다.', QMessageBox.Ok, parent)
    box.open()
    _wait(400)
    results.append(check('알림 상자 open() - 부모 위젯 있음', box.isVisible()))
    box.close()
    _wait(100)
    results.append(check('알림 상자 닫기', not box.isVisible()))

    closer = QTimer()
    closer.timeout.connect(_answer_no)
    closer.start(300)
    answer = QMessageBox.question(parent, '점검', '확인 창을 띄웠다 닫습니다.', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    closer.stop()
    results.append(check('확인 창 exec (QMessageBox.question) → [아니오]', answer == QMessageBox.No, f'답 {int(answer)}'))
    parent.close()

    # 관리자가 구성원 정보를 고치다 서버 오류를 받는 경로 (client/kakao/admin.py 의 _EditResult._on_error → 알림 상자 open)
    from tests.kakao_fakes import make_demo_state
    from client.kakao.admin import submit_member_edit
    from client.kakao.org_tab import OrgTab
    state, conn = make_demo_state()
    state.org = {'holding': {'name': '한국지주'}, 'companies': [{'name': '케이씨에스'}]}
    for name, path in (('홍길동', []), ('김철수', ['기술연구소1실', '2팀(SI)'])):
        conn.push('user_updated', user=dict(state.user(name), company='케이씨에스', org_path=path,
                                            department=path[0] if path else ''))
    conn.push('user_updated', user=dict(state.user('홍길동'), is_admin=True, admin_company='', account='kcs_administrator'))
    tab = OrgTab(state, state.settings)
    tab.resize(420, 560)
    tab.show()
    _wait(300)
    dialog = tab.edit_member('김철수', exec_=False)
    results.append(check('구성원 정보 창 (편집 모드)', dialog is not None))
    if dialog is not None:
        dialog.edits['employee_no'].setText('E9')
        submit_member_edit(state, '김철수', dialog, tab)
        message = '이미 다른 구성원이 쓰는 사번입니다.'
        conn.push('error', message=message, req_type='admin_member_update')
        _wait(400)
        boxes = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
        results.append(check('구성원 수정 서버 오류 → 알림 상자', any(b.text() == message for b in boxes),
                             f'보이는 알림 상자 {len(boxes)}개'))
        for b in boxes:
            b.close()
        dialog.close()
    tab.close()

    # 메인 창: 실제 화면에서 뜨고, 탭을 돌고, 끝난다
    from client.kakao.main_window import MainWindow
    win = MainWindow(state, state.settings)
    win.show()
    _wait(500)
    shown = win.isVisible()
    for key in ('friends', 'chats', 'org', 'more'):
        if key in win.tabs:
            win.set_tab(key)
            _wait(150)
    results.append(check('메인 창 표시 · 탭 전환', shown, f'{win.width()}x{win.height()}'))
    win._quitting = True        # 창 닫기(X)가 트레이로 숨기는 대신 실제로 닫히게
    win.close()
    _wait(200)
    return all(results)


def part_files():
    """받은 파일: 본인만 보는 폴더 · 같은 이름 덮어쓰지 않기 · '받은 파일' 표시 · 폴더에서 보기/열기에 넘기는 명령"""
    from unittest import mock
    app = _qt_app()                         # open_path 가 Qt 의 데스크톱 서비스를 쓴다
    results = [check('화면 플랫폼', app.platformName() == native_platform(), app.platformName())]
    osenv.forget_opened_files_dir()
    folder = osenv.opened_files_dir()
    inside = os.path.abspath(folder).startswith(os.path.abspath(osenv.app_path('opened')) + os.sep)
    results.append(check('받은 파일 폴더 만들기 (데이터 폴더 안)', os.path.isdir(folder) and inside, folder))
    if osenv.is_windows():
        probe = os.path.join(folder, 'probe.txt')
        with open(probe, 'w', encoding='utf-8') as f:
            f.write('ok')
        results.append(check('받은 파일 폴더에 쓰기 (Windows: 사용자 프로필 권한을 따른다)', os.path.exists(probe)))
        os.remove(probe)
    else:
        mode = os.stat(folder).st_mode & 0o777
        results.append(check('받은 파일 폴더는 본인만 접근', mode & 0o077 == 0, oct(mode)))

    first = osenv.save_opened_file('보고서.pdf', b'one')
    second = osenv.save_opened_file('보고서.pdf', b'two')
    with open(first, 'rb') as f:
        kept = f.read() == b'one'
    results.append(check('같은 이름은 덮어쓰지 않고 따로 저장', first != second and kept,
                         f'{os.path.basename(first)} · {os.path.basename(second)}'))

    marked = osenv.mark_from_network(first)
    if osenv.is_windows():
        zone = ''
        try:
            with open(first + ':Zone.Identifier', encoding='ascii') as f:
                zone = f.read()
        except OSError as e:
            zone = f'읽지 못함: {e}'
        results.append(check("Windows '받은 파일' 표시 (Zone.Identifier)", marked and 'ZoneId=3' in zone,
                             zone.strip().replace('\r', '').replace('\n', ' / ')))
    else:
        results.append(check("'받은 파일' 표시는 Windows 만 (이 OS 에서는 하지 않음)", marked is False))

    # 폴더에서 보기 · 기본 프로그램으로 열기: 실제로 프로그램을 띄우지는 않고, 넘기는 명령과 주소만 확인한다
    command = osenv.reveal_command(os.path.abspath(first))
    expected = {'mac': ['open', '-R', os.path.abspath(first)],
                'windows': ['explorer', '/select,', os.path.abspath(first)], 'linux': None}[osenv.current()]
    results.append(check('폴더에서 보기에 넘길 명령', command == expected, str(command)))
    with mock.patch('subprocess.Popen') as popen, \
            mock.patch('PyQt5.QtGui.QDesktopServices.openUrl', return_value=True) as open_url:
        revealed = osenv.reveal_path(first)
        opened = osenv.open_path(first)
        results.append(check('reveal_path · open_path 가 예외 없이 돌아옴 (프로그램은 띄우지 않음)', revealed and opened,
                             f'명령 실행 {popen.call_count}번 · 주소 열기 {open_url.call_count}번'))
        url = open_url.call_args[0][0].toLocalFile() if open_url.call_args else ''
        same = os.path.normcase(os.path.abspath(url)) == os.path.normcase(os.path.abspath(first))
        results.append(check('열기에 넘긴 경로가 저장한 파일', same, url))
        blocked = osenv.open_url('file:///etc/hosts') is False and osenv.open_url('javascript:alert(1)') is False
        results.append(check('웹 · 메일 주소가 아니면 열지 않음', blocked))
    return all(results)


def part_single_probe(folder):
    """(part_single 이 띄우는 다른 프로세스) 같은 폴더의 잠금을 잡아 본다
    → 종료 코드 0: 잡음 / 3: 이미 실행 중이고 먼저 뜬 쪽에 알림 / 4: 이미 실행 중인데 알리지 못함"""
    from PyQt5.QtCore import QCoreApplication
    from client.kakao.single import SingleInstance
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])   # noqa: F841 - 로컬 소켓에 필요
    instance = SingleInstance(folder=folder)
    if instance.acquire():
        instance.release()
        return 0
    return 3 if instance.notify() else 4


def part_single():
    """중복 실행 방지: 같은 프로세스 · 다른 프로세스에서 두 번째가 거절되고, 첫 번째가 끝나면 다시 잡힌다"""
    from PyQt5.QtCore import QCoreApplication
    from client.kakao.single import SingleInstance
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    folder = tempfile.mkdtemp(prefix='kcs_ci_single_')
    first, second = SingleInstance(folder=folder), SingleInstance(folder=folder)
    woke = []
    first.activated.connect(lambda: woke.append(1))
    results = [check('첫 번째 실행이 잠금을 잡음', first.acquire(), folder)]
    results.append(check('첫 번째 실행이 알림을 받을 준비가 됨 (로컬 소켓 듣기)', first.listening, first.listen_error()))
    results.append(check('같은 프로세스의 두 번째는 거절', not second.acquire()))
    results.append(check('같은 프로세스의 두 번째가 알림을 보냄', second.notify()))

    def probe():
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), 'native-part', 'single-probe', folder], cwd=ROOT)
        end = time.time() + 30
        while proc.poll() is None and time.time() < end:
            app.processEvents()             # 다른 프로세스의 '창을 보여 달라' 알림을 받는다
            time.sleep(0.02)
        if proc.poll() is None:
            proc.kill()
        return proc.returncode
    code = probe()
    results.append(check('다른 프로세스의 두 번째 실행은 거절되고 알림을 보냄', code == 3,
                         f'종료 코드 {code} (3: 거절 + 알림, 4: 거절됐지만 알리지 못함)'))
    end = time.time() + 2
    while len(woke) < 2 and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    results.append(check('먼저 뜬 쪽이 두 알림을 모두 받음 (창을 앞으로)', len(woke) >= 2, f'알림 {len(woke)}번'))
    first.release()
    code = probe()
    results.append(check('첫 번째가 끝나면 다음 실행이 잡음', code == 0, f'종료 코드 {code}'))
    return all(results)


def part_paths():
    """데이터 폴더 · 설정 · 저장된 로그인 · 비밀채팅 키 파일을 이 OS 에서 쓸 수 있는가"""
    from client.kakao import login
    from client.kakao.settings import LocalSettings
    results = []
    saved_home = os.environ.pop('KCS_MESSENGER_HOME', None)       # 실제 기본 위치 (사용자 홈 아래)
    try:
        home = osenv.app_home()
        try:
            os.makedirs(home, exist_ok=True)
            probe = os.path.join(home, f'ci_probe_{os.getpid()}.tmp')
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('ok')
            os.remove(probe)
            ok, detail = True, home
        except OSError as e:
            ok, detail = False, f'{home}: {e}'
        results.append(check('기본 데이터 폴더(app_home)에 쓰기', ok, detail))
    finally:
        if saved_home is not None:
            os.environ['KCS_MESSENGER_HOME'] = saved_home

    work = tempfile.mkdtemp(prefix='kcs_ci_paths_')
    path = os.path.join(work, 'saved_login.json')
    login.save_saved_login('hong@kcs.test', 'token-1', True, '127.0.0.1', 5555, path=path)
    loaded = login.load_saved_login(path) or {}
    results.append(check('저장된 로그인 쓰기 · 읽기', loaded.get('token') == 'token-1' and loaded.get('auto_login') is True))
    osenv.secure_file(path)                                       # 예외 없이 돌아오는지
    if osenv.is_windows():
        results.append(check('저장된 로그인 파일 권한 처리 (Windows: 사용자 프로필 권한을 따른다)', os.path.exists(path)))
    else:
        mode = os.stat(path).st_mode & 0o777
        results.append(check('저장된 로그인 파일은 본인만 읽기', mode == 0o600, oct(mode)))
    results.append(check('저장된 토큰 바꾸기', login.replace_saved_token('token-2', path=path)
                         and (login.load_saved_login(path) or {}).get('token') == 'token-2'))

    settings = LocalSettings('점검', base_dir=os.path.join(work, 'settings'))
    wrote = settings.set('font_scale', 1.15) is not False
    again = LocalSettings('점검', base_dir=os.path.join(work, 'settings'))
    results.append(check('사용자 설정 저장 · 다시 읽기 (한글 이름 파일)', wrote and again.get('font_scale') == 1.15))

    try:
        from client.kakao.secret import SecretKeyring, PeerKeys
    except ImportError as e:
        results.append(check('비밀채팅 키 (cryptography)', False, str(e)))
    else:
        keys = os.path.join(work, 'keys')
        ring = SecretKeyring('점검', directory=keys)
        same = SecretKeyring('점검', directory=keys).public_key_b64 == ring.public_key_b64
        mode_ok = True if osenv.is_windows() else (os.stat(ring.path).st_mode & 0o077 == 0)
        results.append(check('비밀채팅 키 만들기 · 다시 읽기 · 본인만 읽기', same and mode_ok, ring.path))
        peers = PeerKeys('점검', directory=keys)
        peers.remember('상대', ring.public_key_b64)
        results.append(check('상대 키 기억 저장 · 다시 읽기',
                             PeerKeys('점검', directory=keys).status('상대', ring.public_key_b64) == 'same'))
    return all(results)


def part_tls():
    """TLS: 서버 인증서를 검증하는 컨텍스트가 만들어지고 CA 묶음이 들어 있는가 (네트워크 접속 없음)"""
    import ssl
    from client.kakao import net
    ctx = net._tls_context()
    results = [check('TLS 컨텍스트: 인증서 · 호스트 이름 검증', ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname)]
    try:
        import certifi
        bundle = certifi.where()
        results.append(check('certifi CA 묶음 파일', os.path.isfile(bundle) and os.path.getsize(bundle) > 10000, bundle))
    except ImportError:
        results.append(check('certifi CA 묶음 (requirements-kcs.txt)', False, 'certifi 가 설치되어 있지 않습니다'))
    count = ctx.cert_store_stats().get('x509_ca', 0)
    results.append(check('신뢰하는 CA 인증서가 들어 있음', count > 0, f'{count}개'))
    results.append(check('오래된 프로토콜을 쓰지 않음 (TLS 1.2 이상)', ctx.minimum_version >= ssl.TLSVersion.TLSv1_2,
                         str(ctx.minimum_version)))
    return all(results)


def part_screens():
    """화면 반응: 실제 화면 플랫폼에서 모든 화면(메인 창 · 탭 · 채팅방 · 통화 · 대화상자)을 열어 걸린 시간을 남긴다.
    규모는 축소판(직원 120 · 방 60 · 메시지 300). CI 러너는 회사 PC 보다 느리고 들쭉날쭉해서 0.5초를 넘는 화면은
    경고(::warning::)로만 알리고, 화면이 열리지 않거나 예외가 나면 실패로 한다. 기준 판정은 품질 하네스(13 화면 반응)가 한다"""
    from scripts.harness import screen_probe
    data = screen_probe.run(native=True, small=True, log=lambda line: print(line, flush=True))
    results = [check('화면 플랫폼', data['platform'] == native_platform(), data['platform'])]
    for line in screen_probe.summary_lines(data):
        print(line, flush=True)
    broken = [e for e in data['results'] if e['error']]
    slow = [e for e in data['results'] if not e['ok'] and not e['error']]
    for e in slow:
        print(f"::warning::[{osenv.os_label()} · {data['platform']}] {e['label']} 표시 {e['show_ms']}ms · "
              f"막힘 {e['block_ms']}ms (기준 {screen_probe.LIMIT_MS}ms)", flush=True)
    results.append(check(f"화면 {len(data['results'])}개 모두 열림", not broken,
                         '; '.join(f"{e['label']}: {e['error']}" for e in broken[:3])))
    results.append(check(f'0.5초 안에 뜬 화면 (넘으면 경고만)', True,
                         f"{len(data['results']) - len(slow)}/{len(data['results'])}"))
    return all(results)


NATIVE_PARTS = [('windows', '창 · 알림 상자', part_windows), ('files', '받은 파일', part_files),
                ('single', '중복 실행 방지', part_single), ('paths', '데이터 폴더 · 파일 권한', part_paths),
                ('tls', 'TLS', part_tls), ('screens', '화면 반응', part_screens)]


def native_part(argv):
    """묶음 하나를 이 프로세스에서 돌린다 (native 가 묶음마다 띄운다) → 종료 코드"""
    if argv and argv[0] == 'single-probe':
        return part_single_probe(argv[1])
    run = {name: fn for name, _title, fn in NATIVE_PARTS}.get(argv[0] if argv else '')
    if run is None:
        return 2
    return 0 if run() else 1


def native():
    platform = native_platform()
    env = dict(os.environ, QT_QPA_PLATFORM=platform, PYTHONIOENCODING='utf-8', PYTHONUTF8='1',
               KCS_MESSENGER_HOME=tempfile.mkdtemp(prefix='kcs_ci_home_'))
    results = []
    for name, title, _fn in NATIVE_PARTS:
        print(f'── {title}', flush=True)
        cmd = [sys.executable, '-X', 'faulthandler', os.path.abspath(__file__), 'native-part', name]
        try:
            r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=600 if name == 'screens' else 180)
            code, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            code = None
            out = e.stdout.decode('utf-8', 'replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
            err = '(180초 안에 끝나지 않음 - 닫히지 않은 창이 있을 수 있습니다)'
        print(out, end='', flush=True)
        if code != 0 and err:
            print(err[-4000:], flush=True)      # 비정상 종료면 faulthandler 가 남긴 위치가 여기 나온다
        results.append(check(f'[{osenv.os_label()} · {platform}] {title}', code == 0,
                             '' if code == 0 else f'종료 코드 {code}'))
    return all(results)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'native-part':
        return native_part(argv[1:])
    if not argv or argv[0] not in ('source', 'built', 'native'):
        print(__doc__)
        return 2
    if argv[0] == 'source':
        ok = source()
    elif argv[0] == 'native':
        ok = native()
    else:
        ok = built(argv[1] if len(argv) > 1 else os.path.join(ROOT, 'dist'))
    print('통과' if ok else '실패')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
