# -*- coding: utf-8 -*-
"""
서버 연결 계층 - 줄 단위 JSON(NDJSON) 소켓 통신
수신은 별도 스레드에서 처리하고, Qt 시그널(queued)로 GUI 스레드에 전달한다.
"""

import json
import queue
import select
import socket
import ssl
import threading
import time

from PyQt5.QtCore import QObject, QCoreApplication, QEventLoop, QThread, pyqtSignal

MAX_LINE_BYTES = 48 * 1024 * 1024       # 서버가 보내는 한 줄의 상한 (첨부 10MB 는 base64 로 약 14MB)
MAX_PRELOGIN_BYTES = 1024 * 1024        # 로그인 전 응답(회사 목록 · 재설정 결과)의 상한


def _tls_context():
    """서버 인증서를 OS 신뢰 저장소로 검증하는 기본 컨텍스트. PyInstaller 로 만든 macOS 앱은 OS 저장소를 못 보는
    경우가 있어 certifi 가 있으면 그 CA 묶음을 함께 신뢰한다 (requirements-kcs.txt 권장 항목)."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # certifi 없음 · 파일 문제 — OS 저장소만 사용
        pass
    return ctx


def open_socket(host, port, timeout=5.0, tls=False):
    """서버 소켓 하나를 연다. tls=True 면 TLS 로 감싼다 (SNI = host, 인증서 검증 포함).
    talk.kcins.co.kr:9550 처럼 nginx 가 TLS 를 종단하는 서버에 붙을 때 켠다. 실패는 OSError(ssl.SSLError 포함)."""
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)    # 죽은 연결을 OS 도 알아채게
    except OSError:
        pass
    if not tls:
        return sock
    try:
        return _tls_context().wrap_socket(sock, server_hostname=host)
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def open_socket_responsive(host, port, timeout=5.0, tls=False):
    """open_socket 을 다른 스레드에서 돌리고, 끝날 때까지 화면 이벤트를 계속 처리한다 → (소켓, '') 또는 (None, 오류 문구).
    서버가 응답하지 않아 연결(TCP · TLS)이 오래 걸려도 창이 멈추지 않는다. 화면 스레드가 아니거나 앱이 없으면 그냥 기다린다.
    기다리는 시간은 open_socket 의 timeout 으로 끝이 있다"""
    result = {}
    done = threading.Event()

    def work():
        try:
            result['sock'], result['error'] = open_socket(host, port, timeout=timeout, tls=tls), ''
        except OSError as e:
            result['sock'], result['error'] = None, str(e)
        finally:
            done.set()

    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        work()
    else:
        threading.Thread(target=work, daemon=True).start()
        while not done.wait(0.01):
            app.processEvents(QEventLoop.AllEvents, 20)
    return result['sock'], result['error']


def _request_before_login(host, port, payload, reply_type, timeout=3.0, rejected=OSError, tls=False):
    """로그인 전 요청 하나를 보내고 답(reply_type)을 받는다. 서버가 error 로 답하면 rejected(서버 문구),
    연결 문제는 OSError"""
    with open_socket(host, port, timeout=timeout, tls=tls) as sock:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(dict(payload, req_id=1), ensure_ascii=False) + '\n').encode('utf-8'))
        buf = b''
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                raise OSError('서버가 연결을 닫았습니다')
            buf += chunk
            if len(buf) > MAX_PRELOGIN_BYTES:
                raise OSError('서버 응답이 너무 깁니다')
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                try:
                    msg = json.loads(line.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(msg, dict) and msg.get('type') == reply_type:
                    return msg
                if isinstance(msg, dict) and msg.get('type') == 'error':
                    raise rejected(msg.get('message') or '요청을 처리할 수 없습니다')


class CompanyList(list):
    """소속 회사 이름 목록 + 서버가 알려 준 등록 방식.
    open_registration: True(열림) / False(닫힘 - 미등록 계정은 관리자가 준 등록 코드가 있어야 등록) / None(예전 서버 - 알려 주지 않음)"""
    open_registration = None


def fetch_companies(host, port, timeout=3.0, tls=False):
    """로그인 전에 소속 회사 목록을 받는다 (org_companies, 명세 §4.1). 실패하면 OSError"""
    msg = _request_before_login(host, port, {'type': 'org_companies'}, 'org_companies', timeout, tls=tls)
    names = CompanyList(c for c in msg.get('companies') or [] if isinstance(c, str))
    flag = msg.get('open_registration')
    names.open_registration = flag if isinstance(flag, bool) else None
    return names


class ResetRejected(Exception):
    """서버가 비밀번호 재설정을 거절함 (아이디·이메일 불일치, 횟수 제한 등) - 사용자에게 보일 문구"""


def reset_password(host, port, login_id, email, new_password, code='', timeout=5.0, tls=False):
    """비밀번호 재설정 (password_reset, 명세 §4.1) → 바뀐 계정 username. code: 관리자가 발급한 일회용 재설정 코드.
    서버가 거절하면 ResetRejected(문구), 연결이 안 되면 OSError"""
    payload = {'type': 'password_reset', 'username': login_id, 'email': email, 'new_password': new_password,
               'code': code}
    msg = _request_before_login(host, port, payload, 'password_reset_ok', timeout, rejected=ResetRejected, tls=tls)
    return msg.get('username') or login_id


class ChatConnection(QObject):
    """서버와의 TCP 연결 하나를 관리한다"""

    connected = pyqtSignal()
    disconnected = pyqtSignal(str)          # 사유
    event_received = pyqtSignal(str, dict)  # (type, payload)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._state_lock = threading.Lock()   # _sock 을 바꾸는 순간 (읽기 · 쓰기 · 화면 스레드가 함께 본다)
        self._reader = None
        self._writer = None
        self._outbox = None                   # 보낼 줄들 (쓰기 스레드가 순서대로 보낸다)
        self._closing = False
        self._req_seq = 0
        self._last_received = self._last_sent = time.monotonic()
        self._server_answers = False          # 서버가 pong 으로 답한 적이 있다 (연결 확인을 하는 서버)
        self._ping_refused = False            # 예전 서버: ping 을 모른다 → 더 보내지 않는다

    # 연결 확인: 조용하면 PING_INTERVAL 마다 ping 을 보내고, 서버의 ping 에는 pong 으로 답한다.
    # pong 으로 답한 적이 있는 서버에서 DEAD_AFTER 동안 아무것도 안 오면 끊긴 것으로 본다 (다시 연결로 이어진다).
    # ping 을 모르는 예전 서버는 답이 없어도 끊지 않는다
    PING_INTERVAL = 40.0
    DEAD_AFTER = 100.0
    HEARTBEAT_TICK = 5.0
    PING_LINE = b'{"type": "ping"}\n'
    PONG_LINE = b'{"type": "pong"}\n'

    @property
    def is_connected(self):
        return self._sock is not None

    def connect_to(self, host, port, username, timeout=5.0, password=None, token=None, remember=False,
                   registration=None, tls=False):
        """연결 후 join 요청을 보낸다(password가 있으면 아이디/비밀번호 로그인). 실패 시 OSError를 그대로 올린다.
        registration={company, name, email, password[, employee_no]} 이면 join 대신 처음 사용 등록(register)을 보낸다 (명세 §4.1)"""
        sock = open_socket(host, port, timeout=timeout, tls=tls)
        self.attach(sock, username, password=password, token=token, remember=remember, registration=registration)

    def attach(self, sock, username, password=None, token=None, remember=False, registration=None):
        """이미 열린 소켓으로 시작한다: 읽기 · 쓰기 스레드를 띄우고 join(또는 register)을 보낸다.
        소켓 열기는 open_socket_responsive 처럼 화면을 붙잡지 않는 쪽에서 하고 여기로 넘긴다"""
        sock.settimeout(None)
        self._sock = sock
        self._closing = False
        self._last_received = self._last_sent = time.monotonic()
        self._server_answers = self._ping_refused = False
        self._outbox = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, args=(sock,), daemon=True)
        self._writer = threading.Thread(target=self._write_loop, args=(sock, self._outbox), daemon=True)
        self._reader.start()
        self._writer.start()
        self.connected.emit()
        if registration:
            self.send('register', **registration, **({'remember': True} if remember else {}))
            return
        payload = {'username': username}
        if token:
            payload['token'] = token           # 자동 로그인 (명세 §4.1)
        elif password:
            payload['password'] = password
            if remember:
                payload['remember'] = True
        self.send('join', **payload)

    def send(self, msg_type, **payload):
        """요청을 보낼 줄에 넣는다. 요청 번호(req_id)를 돌려준다. 연결이 없으면 None.
        실제 전송은 쓰기 스레드가 순서대로 한다 - 큰 첨부를 보내거나 상대가 받지 않아 전송이 막혀도 화면은 멈추지 않는다.
        전송에 실패하면 끊김 신호(disconnected)로 알린다"""
        outbox = self._outbox
        if not self._sock or outbox is None:
            return None
        self._req_seq += 1
        payload['type'] = msg_type
        payload.setdefault('req_id', self._req_seq)
        outbox.put((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
        return payload['req_id']

    def _write_loop(self, sock, outbox):
        while True:
            try:
                data = outbox.get(timeout=self.HEARTBEAT_TICK)
            except queue.Empty:                   # 보낼 것이 없다: 오래 조용했으면 ping
                if self._ping_refused or time.monotonic() - self._last_sent < self.PING_INTERVAL:
                    continue
                data = self.PING_LINE
            if data is None:
                return
            try:
                sock.sendall(data)
                self._last_sent = time.monotonic()
            except OSError as e:
                if not self._closing:
                    self._handle_disconnect(f"전송 실패: {e}", sock)
                return

    def drop(self, reason):
        """응답 없는 연결을 이쪽에서 끊는다 - 끊김 신호가 나가 자동 재연결로 이어진다"""
        self._handle_disconnect(reason)

    def close(self):
        if not self._sock:
            return
        self._closing = True
        sock, outbox, writer = self._sock, self._outbox, self._writer
        try:
            self.send('leave')
        except Exception:
            pass
        if outbox is not None:
            outbox.put(None)                      # 남은 줄(leave 까지)을 보내고 쓰기 스레드를 끝낸다
        if writer is not None and writer is not threading.current_thread():
            writer.join(self.CLOSE_WAIT_SECONDS)  # 전송이 막혀 있으면 기다리지 않고 닫는다
        with self._state_lock:
            self._sock = None
        for step in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
            try:
                step()
            except OSError:
                pass

    CLOSE_WAIT_SECONDS = 1.0

    # ---- 내부 ----
    def _read_loop(self, sock=None):
        buf = bytearray()
        sock = sock or self._sock
        if sock is None:
            return
        reason = "서버 연결이 종료되었습니다"
        try:
            while True:
                # 받을 것이 올 때까지 HEARTBEAT_TICK 씩 기다리며 서버가 조용한지 본다 (TLS 는 이미 풀어 둔 데이터가 있을 수 있다)
                if not getattr(sock, 'pending', int)() and not select.select([sock], [], [], self.HEARTBEAT_TICK)[0]:
                    if self._server_answers and time.monotonic() - self._last_received > self.DEAD_AFTER:
                        reason = "서버 응답이 없어 연결을 다시 시도합니다"
                        break
                    continue
                chunk = sock.recv(65536)
                if not chunk:
                    break
                self._last_received = time.monotonic()
                buf += chunk
                if b'\n' not in chunk:              # 줄이 아직 안 끝남 - 끝없이 쌓이지 않게 상한만 본다
                    if len(buf) > MAX_LINE_BYTES:
                        reason = "서버가 너무 긴 데이터를 보내 연결을 끊었습니다"
                        break
                    continue
                *lines, rest = bytes(buf).split(b'\n')
                buf = bytearray(rest)
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode('utf-8'))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not (isinstance(msg, dict) and isinstance(msg.get('type'), str)):   # 종류가 글자가 아닌 줄은 버린다
                        continue
                    kind = msg['type']
                    if kind == 'ping':                       # 서버의 연결 확인 → 바로 답한다 (화면 쪽에는 올리지 않는다)
                        outbox = self._outbox
                        if outbox is not None:
                            outbox.put(self.PONG_LINE)
                    elif kind == 'pong':
                        self._server_answers = True
                    elif kind == 'error' and msg.get('req_type') == 'ping':
                        self._ping_refused = True            # 예전 서버 - 사용자에게 보일 오류가 아니다
                    else:
                        self.event_received.emit(kind, msg)
        except OSError as e:
            reason = f"연결 오류: {e}"
        except ValueError:          # 기다리는 사이 이쪽에서 소켓을 닫았다
            pass
        except Exception as e:      # 읽기 스레드가 조용히 끝나면 끊긴 줄 모르고 연결된 것처럼 남는다 → 끊김으로 알린다
            reason = f"수신 오류: {e}"
        if not self._closing:
            self._handle_disconnect(reason, sock)

    def _handle_disconnect(self, reason, only=None):
        """끊김 처리는 한 번만: 소켓을 닫아 읽기 · 쓰기 스레드도 끝낸다 (전송 실패로 끊겼을 때 예전에는 소켓 · 스레드가
        남아 서버가 그 연결을 접속 중으로 보고 있었다). only: 그 소켓이 지금 소켓일 때만 (지난 소켓의 스레드가 늦게 부른 경우)"""
        with self._state_lock:
            sock = self._sock
            if sock is None or (only is not None and only is not sock):
                return
            self._sock = None
        if self._outbox is not None:
            self._outbox.put(None)
        for close in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
            try:
                close()
            except OSError:
                pass
        if not self._closing:
            self.disconnected.emit(reason)
