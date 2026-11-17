# -*- coding: utf-8 -*-
"""연결 한도 · 하트비트 · 요청 빈도 (명세 §2, 2026-11-17)"""

import base64
import os
import socket
import tempfile
import threading
import time
import unittest
import unittest.mock

from tests.test_kakao_server import ServerTestBase, Client, FakeRag

from server.kakao import server as server_mod
from server.kakao.server import AI_BUSY, Connection, FrameDecoder, KakaoServer, TokenBucket, WindowLimiter
from server.kakao.store import BOT_USERNAME


class QuickLimitsServer(KakaoServer):
    PRELOGIN_SECONDS = 0.6
    PRELOGIN_MAX_CHARS = 2000
    HEARTBEAT_SECONDS = 0.3
    HEARTBEAT_TIMEOUT = 1.2


class SmallRateServer(KakaoServer):
    REQUEST_RATE, REQUEST_BURST = 5.0, 20.0
    MEDIA_RATE, MEDIA_BURST = 5.0, 10.0
    ACTION_LIMITS = {**KakaoServer.ACTION_LIMITS, 'friend_lookup': (3, 60), 'ai_question': (2, 60), 'search': (4, 60)}


class CustomServerBase(ServerTestBase):
    SERVER_CLASS = KakaoServer

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix='kakao_limits_')
        cls.db_path = os.path.join(cls.tmpdir, 't.db')
        cls.server = cls.SERVER_CLASS(port=0, db_path=cls.db_path, tick_seconds=0.2, rag_factory=FakeRag,
                                      **cls.SERVER_KWARGS)
        cls.server.serve_in_thread()
        cls.rag = cls.server.rag
        cls.port = cls.server.port

    def raw(self):
        c = Client(self.port, 'x', join=False)
        self.clients.append(c)
        return c

    @staticmethod
    def wait_closed(client, timeout=4.0):
        deadline = time.monotonic() + timeout
        with client.cond:
            while not client.closed and time.monotonic() < deadline:
                client.cond.wait(0.1)
        return client.closed


class PreLoginLimitTests(CustomServerBase):
    SERVER_CLASS = QuickLimitsServer

    def test_connection_that_never_logs_in_is_closed(self):
        c = self.raw()
        self.assertEqual(c.wait_for('error', timeout=4.0)['code'], 'login_timeout')
        self.assertTrue(self.wait_closed(c))

    def test_trickling_bytes_does_not_extend_the_time(self):
        c = self.raw()
        started = time.monotonic()
        while time.monotonic() - started < 2.5 and not c.closed:
            try:
                c.send_raw(b' ')
            except OSError:
                break
            time.sleep(0.1)
        self.assertTrue(self.wait_closed(c, 2.0))

    def test_logged_in_connection_is_not_closed(self):
        c = self.connect()
        time.sleep(self.server.PRELOGIN_SECONDS * 2.5)
        c.request({'type': 'todo_list'}, 'todo_list')

    def test_request_size_before_and_after_login(self):
        c = self.raw()
        c.send_raw(b'{"type":"join","username":"' + b'x' * 5000)
        self.assertEqual(c.wait_for('error')['code'], 'too_large')
        self.assertTrue(self.wait_closed(c))
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        self.send_text(a, rid, '긴 글 ' + '가' * 6000)                           # 로그인 뒤에는 큰 요청도 받는다

    def test_too_many_waiting_connections_from_one_address(self):
        self.server.MAX_PRELOGIN_PER_IP = 3
        try:
            waiting = [self.raw() for _ in range(3)]
            time.sleep(0.1)
            extra = self.raw()
            self.assertTrue(self.wait_closed(extra, 0.5))                       # 기다리지 않고 바로 닫는다
            self.assertFalse(waiting[0].closed)
        finally:
            del self.server.MAX_PRELOGIN_PER_IP

    def test_total_connection_cap(self):
        keep = self.connect()
        with self.server._conns_lock:
            self.server.MAX_CONNECTIONS = len(self.server._conns)
        try:
            self.assertTrue(self.wait_closed(self.raw(), 0.5))
            keep.request({'type': 'todo_list'}, 'todo_list')                    # 있던 연결은 그대로
        finally:
            del self.server.MAX_CONNECTIONS

    def test_accept_error_does_not_stop_the_server(self):
        real = self.server._listener

        class Flaky:
            failed = 0

            def accept(self):
                if Flaky.failed < 2:
                    Flaky.failed += 1
                    raise OSError(24, 'Too many open files')
                return real.accept()

            def close(self):
                real.close()
        self.server._listener = Flaky()
        try:
            deadline = time.monotonic() + 5
            while Flaky.failed < 2 and time.monotonic() < deadline:             # 받기 오류가 두 번 난 뒤에도
                time.sleep(0.05)
            self.assertEqual(Flaky.failed, 2)
            self.connect().request({'type': 'todo_list'}, 'todo_list')          # 새 연결을 계속 받는다
        finally:
            self.server._listener = real


class HeartbeatTests(CustomServerBase):
    SERVER_CLASS = QuickLimitsServer

    def test_client_that_ignores_ping_stays_connected(self):
        c = self.connect()
        self.assertEqual(set(c.wait_for('ping', timeout=3.0)), {'type'})
        time.sleep(self.server.HEARTBEAT_TIMEOUT * 1.6)                         # 예전 클라이언트: 조용해도 끊지 않는다
        self.assertFalse(c.closed)
        c.request({'type': 'todo_list'}, 'todo_list')

    def test_ping_is_answered_even_before_login(self):
        c = self.raw()
        self.assertEqual(c.request({'type': 'ping', 'req_id': 7}, 'pong')['req_id'], 7)

    def test_heartbeat_client_is_dropped_when_silent(self):
        c = self.connect()
        c.request({'type': 'ping'}, 'pong')
        self.assertTrue(self.wait_closed(c, self.server.HEARTBEAT_TIMEOUT + 2.5))

    def test_heartbeat_client_that_answers_stays(self):
        c = self.connect()
        stop = time.monotonic() + self.server.HEARTBEAT_TIMEOUT * 2
        while time.monotonic() < stop:
            c.wait_for('ping', timeout=3.0)
            c.send({'type': 'pong'})
        self.assertFalse(c.closed)
        c.assert_no('error', wait=0.1)                                          # pong 은 오류가 아니다


class RequestRateTests(CustomServerBase):
    SERVER_CLASS = SmallRateServer

    def test_request_burst_is_limited_then_recovers(self):
        c = self.connect()
        for i in range(60):
            c.send({'type': 'todo_list', 'req_id': i})
        deadline = time.monotonic() + 3
        limited = None
        while limited is None and time.monotonic() < deadline:
            with c.cond:
                limited = next((m for m in c.buffer if m.get('code') == 'rate_limited'), None)
            time.sleep(0.05)
        self.assertIsNotNone(limited)
        self.assertEqual((limited['req_type'], limited['retry_after']), ('todo_list', 1))
        time.sleep(1.2)
        c.clear()
        c.request({'type': 'todo_list', 'req_id': 'again'}, 'todo_list', lambda m: m.get('req_id') == 'again')

    def test_media_flood_is_dropped_quietly(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        a.send({'type': 'call_start', 'room_id': rid, 'mode': 'voice', 'req_id': 'cs'})
        call = a.wait_for('call_updated', lambda m: m.get('req_id') == 'cs')['call']
        b.send({'type': 'call_join', 'call_id': call['call_id']})
        a.wait_for('call_updated', lambda m: b.username in m['call']['participants'])
        frame = base64.b64encode(b'\0' * 1280).decode()
        for seq in range(80):
            a.send({'type': 'call_media', 'call_id': call['call_id'], 'kind': 'audio', 'seq': seq, 'data_b64': frame})
        time.sleep(0.6)
        with b.cond:
            got = [m for m in b.buffer if m.get('type') == 'call_media']
        self.assertTrue(0 < len(got) < 40)
        a.assert_no('error', wait=0.0)

    def test_missed_friend_lookups_are_limited(self):
        a, b = self.connect(), self.connect()
        b.send({'type': 'profile_update', 'email': f'{b.username}@kcs.test'})
        b.wait_for('user_updated', lambda m: m['user']['username'] == b.username and m['user']['email'])
        for i in range(3):
            self.assertNotIn('code', a.expect_error({'type': 'friend_add', 'query': f'guess{i}@kcs.test'}))
        err = a.expect_error({'type': 'friend_add', 'query': 'guess9@kcs.test'})
        self.assertEqual(err['code'], 'rate_limited')
        self.assertGreater(err['retry_after'], 0)
        a.request({'type': 'friend_add', 'username': b.username}, 'friend_added')   # 이름으로 추가하는 것은 그대로

    def test_search_limit(self):
        a = self.connect()
        for _ in range(4):
            a.request({'type': 'search', 'query': '없는말'}, 'search_result')
        self.assertEqual(a.expect_error({'type': 'search', 'query': '없는말'})['code'], 'rate_limited')

    def test_ai_questions_are_limited_with_one_notice(self):
        a = self.connect()
        a.send({'type': 'room_open_direct', 'target': BOT_USERNAME})
        rid = a.wait_for('room_updated', lambda m: BOT_USERNAME in m['room']['members'])['room']['room_id']
        for i in range(5):
            self.send_text(a, rid, f'질문 {i}')
        time.sleep(1.0)
        hist = a.request({'type': 'history', 'room_id': rid}, 'history')['messages']
        bot = [m['content'] for m in hist if m['sender'] == BOT_USERNAME]
        self.assertEqual(bot.count(AI_BUSY), 1)
        self.assertEqual(len(bot), 3)                                            # 답 2개 + 안내 1개
        self.assertEqual(len(self.rag.questions), 2)


class SendProgressTests(unittest.TestCase):
    def pair(self):
        left, right = socket.socketpair()
        left.settimeout(0.3)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        return Connection(left, ('test', 0)), right

    def test_slow_reader_still_gets_a_big_payload(self):
        conn, peer = self.pair()
        payload = {'type': 'big', 'data': 'x' * 3_000_000}
        got = []

        def read_slowly():
            total = 0
            while total < 3_000_000:
                chunk = peer.recv(65536)
                if not chunk:
                    break
                total += len(chunk)
                time.sleep(0.01)
            got.append(total)
        reader = threading.Thread(target=read_slowly, daemon=True)
        reader.start()
        with unittest.mock.patch.object(server_mod, 'SOCKET_TIMEOUT', 0.3):       # 전체로는 0.3초를 훌쩍 넘긴다
            self.assertTrue(conn.send(payload))
        reader.join(10)
        self.assertGreaterEqual(got[0], 3_000_000)

    def test_stuck_reader_is_dropped(self):
        conn, _peer = self.pair()
        started = time.monotonic()
        self.assertFalse(conn.send({'type': 'big', 'data': 'x' * 8_000_000}))
        self.assertFalse(conn.alive)
        self.assertLess(time.monotonic() - started, 3.0)


class HelperTests(unittest.TestCase):
    def test_token_bucket(self):
        bucket = TokenBucket(rate=1000.0, burst=3)
        self.assertEqual([bucket.take() for _ in range(4)], [True, True, True, False])
        time.sleep(0.01)
        self.assertTrue(bucket.take())

    def test_window_limiter(self):
        limiter = WindowLimiter()
        self.assertEqual([limiter.hit('u', 'a', 2, 60)[0] for _ in range(3)], [True, True, False])
        self.assertGreater(limiter.hit('u', 'a', 2, 60)[1], 0)
        self.assertTrue(limiter.hit('other', 'a', 2, 60)[0])
        self.assertTrue(limiter.hit('u', 'b', 2, 60)[0])

    def test_deeply_nested_request_is_a_format_error(self):
        items = FrameDecoder().feed(b'{"type":"x","a":' + b'[' * 100000 + b']' * 100000 + b'}\n')
        self.assertTrue(items and all(isinstance(item, ValueError) for item in items))
