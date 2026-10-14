# -*- coding: utf-8 -*-
"""
PROXY protocol v1 (2026-10-14): nginx stream 이 TLS 를 종단하고 loopback 으로 넘길 때 첫 줄에 실린
'PROXY TCP4 <src> <dst> <sport> <dport>\\r\\n' 로 실제 클라이언트 주소를 복원한다 (명세 §4.3).
IP 기준 로그인 스로틀이 127.0.0.1 한 덩어리로 묶이지 않아야 하므로 복원된 주소가 스로틀 키에 쓰이는지까지 본다.

실행: python3 -m unittest tests.test_kakao_proxy_protocol -v
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ['KCS_LINK_PREVIEW'] = 'off'
from server.kakao.server import KakaoServer  # noqa: E402
from tests.test_kakao_server import FakeRag, uname  # noqa: E402

TIMEOUT = 3.0


def _wait_for(sock, msg_type):
    """type 이 msg_type 인 첫 메시지. join 직후에는 welcome 보다 먼저 접속 상태 브로드캐스트(user_updated)가
    올 수 있으므로 다른 종류는 건너뛴다."""
    buf = b''
    while True:
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            if not line.strip():
                continue
            msg = json.loads(line.decode('utf-8'))
            if msg.get('type') == msg_type:
                return msg
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError(f'{msg_type} 를 받기 전에 연결이 닫힘')
        buf += chunk


class ProxyProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix='kakao_proxy_')
        cls.server = KakaoServer(port=0, db_path=os.path.join(cls.tmpdir, 't.db'), tick_seconds=0.2,
                                 rag_factory=FakeRag, proxy_protocol=True)
        cls.server._bind()
        cls.server._spawn(cls.server._accept_loop, name='accept')
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _conn(self):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=TIMEOUT)
        self.addCleanup(sock.close)
        return sock

    def _server_conn_for(self, username):
        for c in list(self.server._conns):
            if c.username == username:
                return c
        return None

    def test_header_restores_client_address_and_passes_rest_through(self):
        """헤더 + 첫 요청이 한 패킷에 같이 와도 둘 다 처리된다"""
        name = uname('px')
        sock = self._conn()
        join = json.dumps({'type': 'join', 'username': name}) + '\n'
        sock.sendall(b'PROXY TCP4 203.0.113.7 127.0.0.1 51234 9551\r\n' + join.encode('utf-8'))
        welcome = _wait_for(sock, 'welcome')
        self.assertEqual(welcome.get('type'), 'welcome')
        conn = self._server_conn_for(name)
        self.assertIsNotNone(conn)
        self.assertEqual(conn.address, ('203.0.113.7', 51234))

    def test_header_in_separate_packet(self):
        name = uname('px')
        sock = self._conn()
        sock.sendall(b'PROXY TCP4 198.51.100.9 127.0.0.1 40000 9551\r\n')
        sock.sendall((json.dumps({'type': 'join', 'username': name}) + '\n').encode('utf-8'))
        self.assertEqual(_wait_for(sock, 'welcome').get('type'), 'welcome')
        self.assertEqual(self._server_conn_for(name).address, ('198.51.100.9', 40000))

    def test_plain_connection_without_header_still_works(self):
        """loopback 으로 직접 붙는 운영 도구(헤더 없음)도 막히지 않는다"""
        name = uname('px')
        sock = self._conn()
        sock.sendall((json.dumps({'type': 'join', 'username': name}) + '\n').encode('utf-8'))
        self.assertEqual(_wait_for(sock, 'welcome').get('type'), 'welcome')
        self.assertEqual(self._server_conn_for(name).address[0], '127.0.0.1')

    def test_unknown_header_keeps_socket_address(self):
        name = uname('px')
        sock = self._conn()
        sock.sendall(b'PROXY UNKNOWN\r\n' + (json.dumps({'type': 'join', 'username': name}) + '\n').encode('utf-8'))
        self.assertEqual(_wait_for(sock, 'welcome').get('type'), 'welcome')
        self.assertEqual(self._server_conn_for(name).address[0], '127.0.0.1')

    def test_login_throttle_uses_restored_ip(self):
        """복원된 IP 가 스로틀 키에 들어간다 — 모든 접속이 127.0.0.1 로 뭉치는 회귀 방지"""
        from server.kakao.server import Connection
        fake_sock = socket.socket()
        self.addCleanup(fake_sock.close)
        conn = Connection(fake_sock, ('127.0.0.1', 1))
        conn.address = ('203.0.113.7', 51234)
        keys = self.server._login_keys(conn, 'Someone')
        self.assertIn('ip:203.0.113.7', keys)
        self.assertNotIn('ip:127.0.0.1', keys)


if __name__ == '__main__':
    unittest.main()
