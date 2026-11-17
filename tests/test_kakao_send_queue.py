# -*- coding: utf-8 -*-
"""연결별 보내기 대기열 (명세 §2, 2026-11-17): 한 연결이 멈춰도 다른 전달이 기다리지 않는다"""

import json
import socket
import threading
import time
import unittest
from unittest import mock

from tests.test_kakao_server import ServerTestBase

from server.kakao import server as server_mod
from server.kakao.server import Connection


class SmallQueueConnection(Connection):
    MAX_QUEUE_BYTES = 200_000
    MAX_QUEUE_FRAMES = 500
    REPLY_WAIT_BYTES = 50_000
    DROP_BYTES = 20_000
    DRAIN_SECONDS = 1.0


def read_frames(sock, until=None, timeout=5.0):
    """sock 에서 줄 단위 JSON 을 읽는다. until(frame) 이 참이거나 연결이 닫히면 끝"""
    sock.settimeout(timeout)
    frames, buf = [], b''
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                frames.append(json.loads(line))
                if until is not None and until(frames[-1]):
                    return frames
    except OSError:
        pass
    return frames


class QueueUnitTests(unittest.TestCase):
    def pair(self, cls=Connection, sndbuf=None):
        left, right = socket.socketpair()
        if sndbuf:
            left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
            right.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, sndbuf)
        left.settimeout(5.0)
        self.addCleanup(right.close)
        self.addCleanup(left.close)
        conn = cls(left, ('test', 0))
        self.addCleanup(conn.close, True)
        return conn, right

    def test_frames_arrive_in_the_order_they_were_sent(self):
        conn, peer = self.pair()
        for i in range(3000):
            self.assertTrue(conn.send({'type': 'n', 'i': i}))
        frames = read_frames(peer, until=lambda f: f['i'] == 2999)
        self.assertEqual([f['i'] for f in frames], list(range(3000)))

    def test_login_reply_goes_before_pushes_from_other_threads(self):
        conn, peer = self.pair()
        conn.hold_others()
        pushed = threading.Thread(target=lambda: [conn.send({'type': 'push', 'i': i}) for i in range(5)])
        pushed.start()
        pushed.join()
        conn.send({'type': 'welcome'})
        conn.send({'type': 'all_users_list'})
        conn.release_others()
        conn.send({'type': 'later'})
        frames = read_frames(peer, until=lambda f: f['type'] == 'later')
        self.assertEqual([f['type'] for f in frames],
                         ['welcome', 'all_users_list'] + ['push'] * 5 + ['later'])

    def test_sender_never_waits_for_a_reader_that_stopped(self):
        conn, _peer = self.pair(SmallQueueConnection, sndbuf=4096)
        started = time.monotonic()
        sent = [conn.send({'type': 'm', 'body': 'x' * 2000}) for _ in range(300)]
        self.assertLess(time.monotonic() - started, 1.0)                        # 받는 쪽이 멈춰도 보내는 쪽은 바로 끝난다
        self.assertIn(False, sent)                                             # 한도를 넘으면 그 연결을 닫는다
        self.assertFalse(conn.alive)

    def test_own_replies_wait_instead_of_overflowing(self):
        conn, peer = self.pair(SmallQueueConnection, sndbuf=8192)
        got = []
        reader = threading.Thread(target=lambda: got.extend(read_frames(peer, until=lambda f: f['i'] == 199)),
                                  daemon=True)
        reader.start()
        for i in range(200):                                                   # 대기열 한도(200KB)의 여러 배
            self.assertTrue(conn.send({'type': 'reply', 'i': i, 'body': 'x' * 5000}, wait=True))
        reader.join(20)
        self.assertEqual([f['i'] for f in got], list(range(200)))
        self.assertTrue(conn.alive)

    def test_droppable_frames_are_skipped_when_backed_up(self):
        conn, _peer = self.pair(SmallQueueConnection, sndbuf=4096)
        results = [conn.send({'type': 'call_media', 'data': 'x' * 3000}, droppable=True) for _ in range(100)]
        self.assertIn(True, results)
        self.assertIn(False, results)
        self.assertTrue(conn.alive)                                            # 버리기만 하고 닫지는 않는다
        self.assertLessEqual(conn.queued_bytes(), SmallQueueConnection.DROP_BYTES + 4000)

    def test_close_sends_what_was_already_queued(self):
        conn, peer = self.pair()
        conn.send({'type': 'error', 'code': 'login_timeout'})
        conn.close()
        self.assertFalse(conn.send({'type': 'late'}))
        frames = read_frames(peer)                                             # 닫힐 때까지 읽는다
        self.assertEqual([f['type'] for f in frames], ['error'])

    def test_forced_close_drops_the_queue(self):
        conn, peer = self.pair(SmallQueueConnection, sndbuf=4096)
        for _ in range(30):
            conn.send({'type': 'm', 'body': 'x' * 3000})
        conn.close(force=True)
        self.assertEqual(conn.queued_bytes(), 0) if not conn._writer.is_alive() else None
        frames = read_frames(peer, timeout=3.0)
        self.assertLess(len(frames), 30)

    def test_unsendable_payload_is_not_queued(self):
        conn, peer = self.pair()
        self.assertFalse(conn.send({'type': 'x', 'bad': {1, 2}}))
        self.assertTrue(conn.send({'type': 'ok'}))
        self.assertEqual(read_frames(peer, until=lambda f: True)[0]['type'], 'ok')


class DeliveryTests(ServerTestBase):
    def test_same_payload_is_built_once_for_everyone(self):
        clients = [self.connect() for _ in range(5)]
        rid = self.group(clients[0], clients[1:])['room_id']
        real = server_mod.encode_frame
        built = []

        def counting(payload):
            if isinstance(payload, dict) and payload.get('type') == 'msg_new':
                built.append(payload)
            return real(payload)
        with mock.patch.object(server_mod, 'encode_frame', side_effect=counting):
            clients[0].send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '한 번만'})
            for c in clients:
                c.wait_for('msg_new', lambda m: m['message']['content'] == '한 번만')
        self.assertEqual(len(built), 1)

    def test_stopped_reader_does_not_delay_the_room(self):
        a, b = self.connect(), self.connect()
        stuck_name = f'stuck{id(self) % 100000}'
        stuck = socket.socket()
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(('127.0.0.1', self.port))
        self.addCleanup(stuck.close)
        stuck.sendall((json.dumps({'type': 'join', 'username': stuck_name}) + '\n').encode())
        read_frames(stuck, until=lambda f: f.get('type') == 'all_users_list')
        a.send({'type': 'room_create', 'name': '멈춘방', 'members': [b.username, stuck_name]})
        rid = b.wait_for('room_updated', lambda m: m['room']['name'] == '멈춘방')['room']['room_id']
        body = '가' * 9000
        started = time.monotonic()
        for i in range(300):                                                   # 멈춘 연결의 버퍼를 넘기는 양 (약 8MB)
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': f'{i:04d}' + body})
        b.wait_for('msg_new', lambda m: m['message']['content'].startswith('0299'), timeout=20)
        self.assertLess(time.monotonic() - started, 15.0)                      # 예전에는 멈춘 연결 때문에 30초 넘게 밀렸다
        a.request({'type': 'todo_list'}, 'todo_list')                          # 보낸 사람의 다음 요청도 바로 처리된다

    def test_welcome_is_the_first_thing_a_new_connection_gets(self):
        a = self.connect()
        b_name = f'late{id(self) % 100000}'
        stop = threading.Event()

        def chatter():                                                         # 접속하는 동안에도 모두에게 가는 알림이 계속 생긴다
            while not stop.is_set():
                a.send({'type': 'presence_set', 'presence': 'busy'})
                a.send({'type': 'presence_set', 'presence': 'online'})
        noisy = threading.Thread(target=chatter, daemon=True)
        noisy.start()
        try:
            for round_ in range(15):
                sock = socket.create_connection(('127.0.0.1', self.port))
                sock.sendall((json.dumps({'type': 'join', 'username': f'{b_name}_{round_}'}) + '\n').encode())
                first_two = read_frames(sock, until=lambda f: f.get('type') == 'all_users_list')
                sock.close()
                self.assertEqual([f['type'] for f in first_two], ['welcome', 'all_users_list'])
        finally:
            stop.set()
            noisy.join(2)
