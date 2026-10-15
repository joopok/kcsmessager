# -*- coding: utf-8 -*-
"""
KCS Talk(카카오톡 PC 스타일) 진입점

    python client/kakao/app.py [--host 127.0.0.1] [--port 5555] [--user 이름]

--user를 주지 않으면 로그인 창을 띄운다.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _attach_log_when_no_console():
    """콘솔 없이 실행된 빌드 앱(Windows 창 모드 exe 등)은 sys.stdout/stderr 가 None 이라 출력 · 오류가 사라진다.
    그럴 때는 데이터 폴더의 logs/kcs_talk.log 에 남긴다 (문제가 생긴 PC 에서 원인을 찾을 수 있게)"""
    for stream in (sys.stdout, sys.stderr):
        # Windows 빌드 exe 의 출력은 cp1252 라 '✓' · 한글을 쓰면 UnicodeEncodeError 로 앱이 멈췄다 (CI windows-latest)
        if stream is not None and hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):
                pass
    if sys.stdout is not None and sys.stderr is not None:
        return None
    from client.kakao import osenv as _osenv           # (PyQt 를 부르지 않는 가벼운 모듈)
    try:
        os.makedirs(_osenv.app_path('logs'), exist_ok=True)
        log = open(_osenv.app_path('logs', 'kcs_talk.log'), 'a', encoding='utf-8', buffering=1)
    except OSError:
        return None
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    return log


_LOG = _attach_log_when_no_console()

# High DPI 설정은 QApplication 생성 전에 해야 한다 (자세한 OS 별 보정은 osenv.prepare_process)
os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '1')

from PyQt5.QtCore import QTimer, QCoreApplication  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog  # noqa: E402

from client.kakao import theme, osenv  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='KCS Talk 클라이언트')
    parser.add_argument('--host', default=None,
                        help='서버 주소 (관리자 비상용 - 없으면 더보기 → 정보에서 그룹 관리자가 정한 주소, 기본 127.0.0.1)')
    parser.add_argument('--port', type=int, default=None, help='서버 포트 (기본 5555)')
    tls_group = parser.add_mutually_exclusive_group()
    tls_group.add_argument('--tls', dest='tls', action='store_true', default=None,
                           help='TLS 로 접속 (talk.kcins.co.kr:9550 처럼 nginx 가 TLS 를 종단하는 서버)')
    tls_group.add_argument('--no-tls', dest='tls', action='store_false',
                           help='평문 TCP 로 접속 (개발용 로컬 서버)')
    parser.add_argument('--user', default='', help='로그인 아이디 (생략 시 로그인 창)')
    parser.add_argument('--password', default='', help='비밀번호 (틀리거나 필요하면 로그인 창)')
    parser.add_argument('--no-auto-login', action='store_true',
                        help='저장된 자동 로그인을 쓰지 않고 항상 로그인 창 (런처에서 여러 사람으로 창을 열 때)')
    parser.add_argument('--self-check', action='store_true',
                        help='창을 띄우지 않고 OS · 글꼴 · 로고 · 데이터 폴더를 점검하고 끝낸다 (빌드한 앱 확인용)')
    return parser.parse_args(argv)


def self_check(out=print):
    """빌드한 앱(또는 소스 실행)이 이 OS 에서 필요한 것을 찾는지 점검. 문제가 없으면 0"""
    from client.kakao import brand
    families = theme.font_families()
    checks = [
        ('OS', True, f'{osenv.os_label()} ({osenv.current()})'),
        ('글꼴 폴더', os.path.isdir(theme.FONT_DIR), theme.FONT_DIR),
        ('앱 글꼴 등록 (Poppins · KoPub 돋움)', theme.bundled_fonts_ready(), ', '.join(families[:3])),
        ('회사 로고', brand.has_logo(), brand.BRAND_DIR),
        ('데이터 폴더', os.access(os.path.dirname(osenv.app_home()) or '.', os.W_OK), osenv.app_home()),
    ]
    for name, ok, detail in checks:
        out(f"{'✓' if ok else '✗'} {name}: {detail}")
    return 0 if all(ok for _n, ok, _d in checks) else 1


def setup_high_dpi():
    """QApplication 전 보정 - High DPI + OS 별 앱 이름 · 작업 표시줄 묶음 (osenv 한 곳에서)"""
    osenv.prepare_process()


def ask_connection_failure(host, port, error):
    """연결 실패 안내. 'retry' / 'quit' (서버 주소는 그룹 관리자만 더보기 → 정보에서 바꾼다)"""
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle('서버 연결 실패')
    box.setText('서버에 연결할 수 없습니다.')
    box.setInformativeText(f'{host}:{port}\n잠시 후 다시 시도해 보고, 계속 안 되면 관리자에게 문의해 주세요.')
    box.setDetailedText(str(error))
    retry = box.addButton('다시 시도', QMessageBox.AcceptRole)
    box.addButton('종료', QMessageBox.RejectRole)
    box.setDefaultButton(retry)
    box.exec_()
    return 'retry' if box.clickedButton() is retry else 'quit'


class Session:
    """한 번의 로그인 세션: 연결 · 설정 · 상태 · 메인 창"""

    def __init__(self, host, port, username, password='', token='', remember=False, registration=None,
                 tls=False):
        from client.kakao.net import ChatConnection
        from client.kakao.settings import LocalSettings
        from client.kakao.state import AppState
        self.host, self.port, self.username = host, port, username
        self.tls = tls             # 운영 서버(nginx TLS 종단)면 True — 재연결에도 같은 값으로 붙는다
        self.password = password   # 자동 재연결용으로 메모리에만 둔다
        self.token = token         # 자동 로그인 토큰 (저장된 로그인)
        self.remember = remember
        self.registration = registration   # 처음 사용 등록 {company, name, email, password[, employee_no]} (명세 §4.1)
        if registration:                   # 다음부터의 로그인 아이디: 사번(예전 클라이언트) → 이메일 → 이름
            self.username = registration.get('employee_no') or registration.get('email') or registration['name']
            self.password = registration['password']
        self.settings = LocalSettings(self.username)   # welcome 때 실제 사용자 이름 파일로 바뀐다
        self.conn = ChatConnection()
        self.state = AppState(self.conn, self.settings)
        self.window = None
        self.on_relogin_needed = None   # 재연결이 거절되면(비밀번호 변경 등) 부를 함수(사유)
        # 자동 재연결: 예기치 않게 끊기면 1·2·4·8·10초… 간격으로 다시 붙는다 (직접 닫은 연결은 제외)
        self._closing = False
        self._retry = 0
        self._retry_timer = QTimer()
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._auto_reconnect)
        self.state.connection_lost.connect(self._schedule_reconnect)
        self.state.login_failed.connect(self._on_login_failed)
        self.state.account_disabled.connect(self._on_login_failed)   # 관리자가 사용 중지 → 재연결하지 않고 로그인 창

    RETRY_DELAYS = (1, 2, 4, 8, 10)

    def retry_delay(self):
        return self.RETRY_DELAYS[min(self._retry, len(self.RETRY_DELAYS) - 1)]

    def _schedule_reconnect(self, _reason=''):
        if self._closing or self._retry_timer.isActive():
            return
        delay = self.retry_delay()
        if self.window is not None:
            self.window.show_reconnecting(delay)
        self._retry_timer.start(delay * 1000)

    def _auto_reconnect(self):
        if self._closing or self.conn.is_connected:
            return
        ok, _msg = self.reconnect()
        if ok:
            self._retry = 0
        else:
            self._retry += 1
            self._schedule_reconnect()

    def _credentials(self):
        """재연결은 받은 토큰 → 저장된 토큰 → 비밀번호 순으로 쓴다"""
        token = self.state.login_token or self.token
        return {'token': token} if token else {'password': self.password}

    def connect(self):
        if self.registration:      # 재연결은 등록된 비밀번호로 join 한다
            self.conn.connect_to(self.host, self.port, self.username, registration=self.registration,
                                 remember=self.remember, tls=self.tls)
        elif self.token:
            self.conn.connect_to(self.host, self.port, self.username, token=self.token, tls=self.tls)
        else:
            self.conn.connect_to(self.host, self.port, self.username, password=self.password,
                                 remember=self.remember, tls=self.tls)

    def reconnect(self):
        """같은 host/port/user로 새 연결을 만들어 상태에 붙인다 → (성공, 메시지)
        join 이 거절되면 login_failed → _on_login_failed 가 처리한다"""
        from client.kakao.net import ChatConnection
        from client.kakao.widgets import disconnect_signals
        conn = ChatConnection()
        old = self.conn
        # join 을 보내기 전에 붙인다: 읽기 스레드가 welcome 을 받는 순간 아무도 듣고 있지 않으면 그 응답은 사라진다.
        # welcome 처리 중 보내는 요청(일정 · 할 일 · 히스토리)도 새 연결로 가야 한다
        conn.event_received.connect(self.state.handle)
        conn.disconnected.connect(self.state.connection_lost)
        self.state.conn = conn
        self.state.history_requested.clear()  # 열려 있는 방은 다시 히스토리를 받는다
        try:
            conn.connect_to(self.host, self.port, self.state.me or self.username, tls=self.tls, **self._credentials())
        except OSError as e:
            disconnect_signals((conn.event_received, self.state.handle),
                               (conn.disconnected, self.state.connection_lost))
            self.state.conn = old
            return False, f'재연결 실패: {e}'
        disconnect_signals((old.event_received, self.state.handle), (old.disconnected, self.state.connection_lost))
        try:
            old.close()           # 수동 재연결이면 예전 연결이 아직 살아 있을 수 있다
        except Exception:
            pass
        self.conn = conn
        self._retry = 0
        self._retry_timer.stop()  # 수동 재연결이 먼저 성공했으면 예약된 자동 재연결이 새 연결을 끊지 않게
        return True, ''

    def _on_login_failed(self, reason):
        if self.window is None:      # 첫 로그인은 wait_for_login 이 처리
            return
        self._closing = True         # 다시 붙어도 또 거절된다 → 재시도 멈춤
        self._retry_timer.stop()
        try:
            self.conn.close()
        except Exception:
            pass
        if self.on_relogin_needed is not None:
            self.on_relogin_needed(reason)

    def close(self):
        self._closing = True
        self._retry_timer.stop()
        try:
            self.conn.close()
        except Exception:
            pass

    def show_main_window(self, logout=None):
        if self.window is not None:
            return
        from client.kakao.main_window import MainWindow
        self.window = MainWindow(self.state, self.settings, reconnect=self.reconnect,
                                 server_address=f'{self.host}:{self.port}', logout=logout)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()


def remember_login(session, login_id, auto_login):
    """로그인 성공 후: 최근 아이디 + (선택 시) 아이디·토큰 저장. 저장 안 함이면 저장 정보 삭제"""
    from client.kakao import login
    login.save_recent(login_id)
    token = session.state.login_token or session.token
    if session.remember and token:
        login.save_saved_login(login_id, token, auto_login, session.host, session.port)
    elif not session.remember:
        login.clear_saved_login()


def perform_logout(session, relaunch=None):
    """로그아웃: 서버의 자동 로그인 토큰 폐기 + 저장된 로그인 삭제 + 연결 종료 → 로그인 창부터 다시 시작"""
    from client.kakao import login
    saved_now = login.load_saved_login()
    session.state.revoke_login_token((saved_now or {}).get('token') or session.state.login_token)
    login.clear_saved_login()
    session.close()
    (relaunch or relaunch_to_login)()


def relaunch_to_login():
    """로그인 창부터 다시 시작 (로그아웃·재로그인 필요). --user/--password 인자는 빼고 다시 실행한다"""
    from PyQt5.QtCore import QProcess
    args, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ('--user', '--password'):
            skip = True
            continue
        if a.startswith(('--user=', '--password=')):
            continue
        args.append(a)
    program, program_args = osenv.relaunch_command([sys.argv[0]] + args)
    QProcess.startDetached(program, program_args)
    QApplication.quit()


def wait_for_login(state, timeout_ms=10000):
    """join 결과를 기다린다 → ('ok'|'failed'|'lost'|'timeout', 사유)
    중첩 QEventLoop 대신 이벤트를 직접 돌린다(앞서 quit()가 불렸으면 QEventLoop.exec_는 바로 끝난다)"""
    import time
    from PyQt5.QtCore import QEventLoop
    result = []
    hooks = ((state.welcomed, lambda: result or result.append(('ok', ''))),
             (state.login_failed, lambda reason: result or result.append(('failed', reason))),
             (state.connection_lost, lambda reason: result or result.append(('lost', reason))))
    for signal, slot in hooks:
        signal.connect(slot)
    deadline = time.monotonic() + timeout_ms / 1000
    app = QCoreApplication.instance()
    while not result and time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.01)
    for signal, slot in hooks:
        signal.disconnect(slot)
    return result[0] if result else ('timeout', '')


def install_korean_qt_translation(app):
    """Qt 기본 대화상자 버튼(Yes/No/Cancel, 파일 선택 창)을 한국어로 (예/아니요/취소).
    번역 파일이 없으면(일부 배포본) 조용히 영어로 둔다"""
    from PyQt5.QtCore import QLibraryInfo, QTranslator
    translator = QTranslator(app)
    for path in (QLibraryInfo.location(QLibraryInfo.TranslationsPath),
                 os.path.join(getattr(sys, '_MEIPASS', ''), 'PyQt5', 'Qt5', 'translations')):
        if path and translator.load('qtbase_ko', path):
            app.installTranslator(translator)
            app._kcs_translator = translator   # 가비지 컬렉션 방지
            return True
    return False


def install_crash_guard():
    """슬롯 · 타이머 · 이벤트 처리 중 잡지 못한 예외가 앱 전체를 끄지 않게 한다.
    PyQt5 는 기본 excepthook 이면 슬롯에서 빠져나온 예외에 qFatal 로 프로세스를 끝낸다 -
    서버가 보낸 예상 밖의 값 하나, 쓸 수 없는 저장 경로 하나로 메신저가 꺼졌다. 기록(stderr · 로그 파일)만 남기고 계속한다"""
    import traceback

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):   # Ctrl+C 가 처리기보다 먼저 슬롯에서 터졌다: 계속하지 말고 끝낸다
            QCoreApplication.exit(INTERRUPT_EXIT_CODE)
            return
        try:
            sys.stderr.write('[KCS Talk] 처리하지 못한 오류 (앱은 계속 실행):\n'
                             + ''.join(traceback.format_exception(exc_type, exc, tb)))
            sys.stderr.flush()
        except (OSError, ValueError, AttributeError):
            pass

    sys.excepthook = hook
    return hook


INTERRUPT_EXIT_CODE = 130   # 터미널 Ctrl+C 로 끝냄 (128 + SIGINT)


def install_interrupt_quit(app):
    """터미널에서 Ctrl+C 를 누르면 앱을 끝낸다. 파이썬 기본 처리기는 다음에 도는 슬롯 · eventFilter 안에서
    KeyboardInterrupt 를 일으키고, PyQt 가 그것을 excepthook 으로 넘겨 기록만 남고 앱은 계속 돌았다.
    Qt 이벤트 루프가 쉬는 동안에는 파이썬이 신호를 볼 수 없으므로 짧은 타이머로 깨운다.
    한 번 누르면 정상 종료, 종료가 막혀 다시 누르면 기본 동작(즉시 종료). 타이머를 돌려준다"""
    import signal

    def on_interrupt(_signum, _frame):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        QCoreApplication.exit(INTERRUPT_EXIT_CODE)

    try:
        signal.signal(signal.SIGINT, on_interrupt)
    except ValueError:                    # 주 스레드가 아니면 신호 처리기를 못 단다
        return None
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)   # 파이썬으로 잠깐 돌아와 대기 중인 신호를 처리
    timer.start(250)
    return timer


def main(argv=None):
    args = parse_args(argv)
    setup_high_dpi()                  # macOS 메뉴 막대 이름 · Windows 작업 표시줄 묶음 (QApplication 보다 먼저)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    osenv.configure_app(app)          # 이름 · Fusion 스타일 · macOS Dock 이름 · Linux .desktop 연결
    if args.self_check:
        try:
            return self_check()
        except Exception:                 # noqa: BLE001 - 점검 모드는 오류 창 없이 기록만 하고 끝낸다
            import traceback
            traceback.print_exc()
            return 1
    install_korean_qt_translation(app)
    install_crash_guard()
    install_interrupt_quit(app)

    from client.kakao.login import LoginDialog, talk_icon
    from client.kakao.main_window import apply_app_theme
    app.setWindowIcon(talk_icon())
    apply_app_theme(None)

    from client.kakao import login as login_mod
    from client.kakao.settings import load_server_address, load_server_tls, DEFAULT_HOST, DEFAULT_PORT
    # 접속 주소: 명령줄 → 이 PC 의 server.json(그룹 관리자가 정함) → 저장된 로그인 → 기본값 (명세 §4.3)
    configured = load_server_address()
    fixed = args.host is not None or args.port is not None or configured is not None
    host = args.host or (configured[0] if configured else DEFAULT_HOST)
    port = args.port or (configured[1] if configured else DEFAULT_PORT)
    # TLS 여부도 같은 순서: --tls/--no-tls → server.json 의 tls → DEFAULT_TLS
    tls = args.tls if args.tls is not None else load_server_tls()
    username, password = args.user.strip(), args.password
    saved = login_mod.load_saved_login() if not username else None

    def saved_address(host, port):
        if fixed or not saved:
            return host, port
        return saved['host'] or host, saved['port'] or port
    token, remember, auto_login = '', False, False
    if saved and saved['auto_login'] and not args.no_auto_login:   # 자동 로그인: 로그인 창 없이 저장된 토큰으로
        username, token, remember, auto_login = saved['login_id'], saved['token'], True, True
        host, port = saved_address(host, port)
    need_login = not username
    error = ''
    registration = None
    while True:
        if need_login:
            if saved and not args.user and not args.no_auto_login:
                host, port = saved_address(host, port)
            # 런처(--no-auto-login)로 연 창은 처음 로그인 상태: 아이디·비밀번호를 비우고 저장된 로그인을 쓰지 않는다
            dlg = LoginDialog(host, port, username, registration=registration, tls=tls,   # 등록 실패면 등록 화면 그대로
                              fresh=args.no_auto_login)
            dlg.show_error(error)
            if dlg.exec_() != QDialog.Accepted:
                return 0
            username, host, port = dlg.values()
            password, token = dlg.password(), dlg.saved_token()
            remember, auto_login = dlg.remember(), dlg.auto_login()
            registration = dlg.registration()
        session = Session(host, port, username, password, token=token, remember=remember,
                          registration=registration, tls=tls)
        try:
            session.connect()
        except OSError as e:
            choice = ask_connection_failure(host, port, e)
            if choice == 'quit':
                return 1
            error = ''
            continue
        outcome, detail = wait_for_login(session.state)
        if outcome == 'ok':
            break
        session.close()
        if outcome == 'failed':          # 아이디/비밀번호 오류·저장 정보 만료 → 로그인 창에 사유 표시
            if token:
                login_mod.clear_saved_login()
                saved, token = None, ''
            need_login, error = True, detail
            continue
        if outcome == 'timeout':
            QMessageBox.critical(None, '서버 응답 없음', '서버에 연결했지만 응답이 없습니다.\n잠시 후 다시 시도해 주세요.')
        else:
            QMessageBox.critical(None, '서버 연결 끊김', f'로그인 중 연결이 끊어졌습니다.\n{detail}')
        return 1

    remember_login(session, username, auto_login)

    def logout():
        perform_logout(session)

    def relogin_needed(reason):
        login_mod.clear_saved_login()
        QMessageBox.warning(session.window, '다시 로그인', f'다시 로그인해 주세요.\n{reason}')
        relaunch_to_login()
    session.on_relogin_needed = relogin_needed

    # 사용자별 설정(다크 모드, 글자 크기)을 적용
    apply_app_theme(session.settings)
    session.show_main_window(logout=logout)
    app.aboutToQuit.connect(session.close)
    app._kcs_session = session  # 가비지 컬렉션 방지
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
