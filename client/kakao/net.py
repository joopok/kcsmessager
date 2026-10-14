# -*- coding: utf-8 -*-
"""
서버 연결 계층 - 줄 단위 JSON(NDJSON) 소켓 통신
수신은 별도 스레드에서 처리하고, Qt 시그널(queued)로 GUI 스레드에 전달한다.
"""

import json
import socket
import ssl
import threading

from PyQt5.QtCore import QObject, pyqtSignal


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


def fetch_companies(host, port, timeout=3.0, tls=False):
    """로그인 전에 소속 회사 목록을 받는다 (org_companies, 명세 §4.1). 실패하면 OSError"""
    msg = _request_before_login(host, port, {'type': 'org_companies'}, 'org_companies', timeout, tls=tls)
    return [c for c in msg.get('companies') or [] if isinstance(c, str)]


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
        self._send_lock = threading.Lock()
        self._reader = None
        self._closing = False
        self._req_seq = 0

    @property
    def is_connected(self):
        return self._sock is not None

    def connect_to(self, host, port, username, timeout=5.0, password=None, token=None, remember=False,
                   registration=None, tls=False):
        """연결 후 join 요청을 보낸다(password가 있으면 아이디/비밀번호 로그인). 실패 시 OSError를 그대로 올린다.
        registration={company, name, email, password[, employee_no]} 이면 join 대신 처음 사용 등록(register)을 보낸다 (명세 §4.1)"""
        sock = open_socket(host, port, timeout=timeout, tls=tls)
        sock.settimeout(None)
        self._sock = sock
        self._closing = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
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
        """요청 전송. 요청 번호(req_id)를 돌려준다. 연결이 없으면 None."""
        if not self._sock:
            return None
        self._req_seq += 1
        payload['type'] = msg_type
        payload.setdefault('req_id', self._req_seq)
        data = (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
        try:
            with self._send_lock:
                self._sock.sendall(data)
        except OSError as e:
            self._handle_disconnect(f"전송 실패: {e}")
            return None
        return payload['req_id']

    def close(self):
        if not self._sock:
            return
        self._closing = True
        try:
            self.send('leave')
        except Exception:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None

    # ---- 내부 ----
    def _read_loop(self):
        buf = b''
        sock = self._sock
        if sock is None:
            return
        reason = "서버 연결이 종료되었습니다"
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode('utf-8'))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(msg, dict) and 'type' in msg:
                        self.event_received.emit(msg['type'], msg)
        except OSError as e:
            reason = f"연결 오류: {e}"
        if not self._closing:
            self._handle_disconnect(reason)

    def _handle_disconnect(self, reason):
        """끊김 처리는 한 번만: 소켓을 닫아 읽기 스레드도 끝낸다 (전송 실패로 끊겼을 때 예전에는 소켓 · 스레드가
        남아 서버가 그 연결을 접속 중으로 보고 있었다)"""
        sock, self._sock = self._sock, None
        if sock is None:
            return
        for close in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
            try:
                close()
            except OSError:
                pass
        if not self._closing:
            self.disconnected.emit(reason)
