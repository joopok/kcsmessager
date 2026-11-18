# -*- coding: utf-8 -*-
"""요청 값 공통 규칙 (명세 §4.5, 2026-11-17): 표식 · 글자 · 목록 길이 · 사진 형식 · 음성 조각 · 승인 실패 처리,
그리고 링크 미리보기 작업의 견고성"""

import base64
import json
import time
import unittest
import zlib
from unittest import mock

from tests.test_kakao_server import ServerTestBase, Client, PNG_1PX, uname

from server.kakao import protocol
from server.kakao.protocol import ProtocolError
from server.kakao.server import (KakaoServer, BAD_TEXT, MAX_AUDIO_FRAME_BYTES, MAX_INVITE, UNSUPPORTED_REQUEST,
                                 _scan_preview_tags)

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'


def b64(data):
    return base64.b64encode(data).decode('ascii')


class RelayRuleTests(ServerTestBase):
    def room(self):
        a, b = self.connect(), self.connect()
        return a, b, self.group(a, [b])['room_id']

    def texts(self, client, rid):
        return [m['content'] for m in client.request({'type': 'history', 'room_id': rid}, 'history')['messages']]

    def test_client_tmp_id_is_a_short_tag(self):
        a, b, rid = self.room()
        for bad in ('x' * 65, {'k': 1}, ['a'], True, 1.5):
            err = a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '표식 시험',
                                  'client_tmp_id': bad})
            self.assertIn('client_tmp_id', err['message'])
        self.assertNotIn('표식 시험', self.texts(b, rid))                       # 거절된 것은 저장도 전달도 안 된다
        for good in ('file-1', 'x' * 64, 7, None):
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': f'ok-{good}', 'client_tmp_id': good})
            got = b.wait_for('msg_new', lambda m, g=good: m['message']['content'] == f'ok-{g}')
            self.assertEqual(got['client_tmp_id'], good)

    def test_req_id_is_a_short_tag(self):
        a = self.connect()
        for bad in ({'k': 1}, 'x' * 65, [1]):
            a.send({'type': 'todo_list', 'req_id': bad})
            err = a.wait_for('error', lambda m: 'req_id' in m.get('message', ''))
            self.assertNotIn('req_id', err)
        for good in (3, 'r-1'):
            a.send({'type': 'todo_list', 'req_id': good})
            self.assertEqual(a.wait_for('todo_list')['req_id'], good)

    def test_text_that_cannot_be_sent_is_refused_up_front(self):
        a, b, rid = self.room()
        raw = json.dumps({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': 'bad\ud800', 'req_id': 'u1'})
        a.send_raw(raw.encode('ascii') + b'\n')
        err = a.wait_for('error', lambda m: m.get('req_id') == 'u1')
        self.assertEqual(err['message'], BAD_TEXT)
        self.assertNotIn('code', err)                                          # 서버 오류가 아니라 검증 오류
        raw = json.dumps({'type': 'profile_update', 'display_name': 'n\udfff', 'req_id': 'u2'})
        a.send_raw(raw.encode('ascii') + b'\n')
        self.assertIn('쓸 수 없는 문자', a.wait_for('error', lambda m: m.get('req_id') == 'u2')['message'])
        self.send_text(a, rid, '그 뒤에도 정상')                                # 연결과 방은 그대로
        b.wait_for('msg_new', lambda m: m['message']['content'] == '그 뒤에도 정상')

    def test_unsendable_payload_is_dropped_without_closing(self):
        a = self.connect()
        conn = next(c for c in self.server._all_joined_conns() if c.username == a.username)
        self.assertFalse(conn.send({'type': 'x', 'bad': {1, 2}}))
        self.assertTrue(conn.alive)
        self.assertTrue(conn.send({'type': 'note', 'text': 'half\ud800'}))      # 보낼 수 없는 글자는 바꿔서 보낸다
        self.assertEqual(a.wait_for('note')['text'], 'half?')

    def test_invite_and_list_limits(self):
        a, b, rid = self.room()
        err = a.expect_error({'type': 'room_invite', 'room_id': rid,
                              'usernames': [f'없는사람{i}' for i in range(MAX_INVITE + 1)]})
        self.assertIn('초과', err['message'])
        err = a.expect_error({'type': 'room_invite', 'room_id': rid,
                              'usernames': [f'n{i}' for i in range(protocol.MAX_LIST_ITEMS + 1)]})
        self.assertIn('너무 많습니다', err['message'])
        err = a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': 'x',
                              'mentions': [f'n{i}' for i in range(201)]})
        self.assertIn('너무 많습니다', err['message'])

    def test_name_list_helper(self):
        names = [f'n{i}' for i in range(protocol.MAX_LIST_ITEMS)]
        started = time.monotonic()
        out = protocol._str_list({'v': names + [' n1 ', '', 'n0'][:0]}, 'v')
        self.assertEqual(out, names)
        self.assertEqual(protocol._str_list({'v': [' b ', 'a', 'b', '', 'a']}, 'v'), ['b', 'a'])
        big = [f'n{i}' for i in range(40000)]
        self.assertEqual(len(protocol._str_list({'v': big}, 'v', max_items=40000)), 40000)
        self.assertLess(time.monotonic() - started, 1.0)                        # 길이에 비례하는 시간
        with self.assertRaises(ProtocolError):
            protocol._str_list({'v': names + ['x']}, 'v')
        with self.assertRaises(ProtocolError):
            protocol._str_list({'v': ['ok', 5]}, 'v')

    def send_file(self, client, watcher, rid, kind, name, mime, data):
        client.send({'type': 'send', 'room_id': rid, 'kind': kind, 'content': name,
                     'attachment': {'name': name, 'mime': mime, 'data_b64': b64(data)}})
        return watcher.wait_for('msg_new', lambda m: m['message']['content'] == name)['message']

    def test_photo_must_really_be_a_photo(self):
        a, b, rid = self.room()
        m = self.send_file(a, b, rid, 'image', 'drawing.svg', 'image/svg+xml', SVG)
        self.assertEqual((m['kind'], m['attachment']['mime']), ('file', 'application/octet-stream'))
        m = self.send_file(a, b, rid, 'image', 'photo.bin', 'image/svg+xml', PNG_1PX)
        self.assertEqual((m['kind'], m['attachment']['mime']), ('image', 'image/png'))
        m = self.send_file(a, b, rid, 'file', 'note.svg', 'image/svg+xml', SVG)
        self.assertEqual((m['kind'], m['attachment']['mime']), ('file', 'application/octet-stream'))
        m = self.send_file(a, b, rid, 'file', 'memo.txt', 'text/plain', b'hello')
        self.assertEqual((m['kind'], m['attachment']['mime']), ('file', 'text/plain'))

    def test_audio_frame_limit(self):
        ok = KakaoServer._audio_frame_ok
        self.assertTrue(ok(b64(b'\0' * MAX_AUDIO_FRAME_BYTES), 'pcm'))
        self.assertFalse(ok(b64(b'\0' * (MAX_AUDIO_FRAME_BYTES + 3)), 'pcm'))
        self.assertTrue(ok(b64(zlib.compress(b'\0' * MAX_AUDIO_FRAME_BYTES, 9)), 'zpcm'))
        self.assertTrue(ok(b64(zlib.compress(b'\1\2' * 640, 6)), 'zpcm'))       # 보통 조각 (40ms)
        self.assertFalse(ok(b64(zlib.compress(b'\0' * (MAX_AUDIO_FRAME_BYTES + 1), 9)), 'zpcm'))
        self.assertFalse(ok(b64(zlib.compress(b'\0' * 5_000_000, 9)), 'zpcm'))  # 작게 눌린 큰 조각
        self.assertFalse(ok(b64(b'not zlib'), 'zpcm'))
        self.assertFalse(ok('@@@', 'pcm'))
        a = self.connect()
        err = a.expect_error({'type': 'call_media', 'call_id': 1, 'kind': 'audio', 'seq': 1, 'codec': 'zpcm',
                              'data_b64': b64(zlib.compress(b'\0' * 5_000_000, 9))})
        self.assertIn('음성 조각', err['message'])


class LegacyBroadcastTests(ServerTestBase):
    SERVER_KWARGS = {'require_password': True, 'open_registration': True}

    def test_broadcast_request_is_off_when_passwords_are_required(self):
        a, _ = self.register(password='pw-123456')
        b, _ = self.register(password='pw-123456')
        b.clear()
        self.assertEqual(a.expect_error({'type': 'message', 'content': '모두에게'})['message'], UNSUPPORTED_REQUEST)
        b.assert_no('message')


class PreviewWorkerTests(ServerTestBase):
    SERVER_KWARGS = {'link_preview': True,
                     'preview_fetcher': lambda url: {'url': url, 'title': '제목', 'description': '', 'site': ''}}

    def test_worker_keeps_running_after_a_store_error(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        real = self.server.store.set_link_preview
        calls = []

        def flaky(msg_id, preview):
            calls.append(msg_id)
            if len(calls) == 1:
                raise RuntimeError('저장 실패')
            return real(msg_id, preview)
        with mock.patch.object(self.server.store, 'set_link_preview', side_effect=flaky):
            self.send_text(a, rid, '첫 링크 https://example.com/1')
            second = self.send_text(a, rid, '둘째 링크 https://example.com/2')
            upd = b.wait_for('msg_updated', lambda m: m['message']['msg_id'] == second['msg_id']
                             and m['message']['link_preview'])
        self.assertEqual(upd['message']['link_preview']['title'], '제목')
        self.assertEqual(len(calls), 2)


class PreviewScanTests(unittest.TestCase):
    def test_reads_meta_in_any_attribute_order(self):
        found, title = _scan_preview_tags(
            '<html><head><META content=\'설명\' NAME=description>'
            '<meta property="og:title" content="제목"><meta content="사이트" property="og:site_name">'
            '<title>\n 페이지 </title></head>')
        self.assertEqual(found, {'description': '설명', 'og:title': '제목', 'og:site_name': '사이트'})
        self.assertEqual(title.strip(), '페이지')
        self.assertEqual(_scan_preview_tags('글자뿐'), ({}, ''))

    def test_time_grows_with_size_only(self):
        samples = ['<meta' + ' x' * 120000,
                   ('<meta property="') * 16000,
                   '<meta ' + 'a="b" ' * 40000 + '>',
                   '<title' + ' ' * 250000,
                   ('<meta content="v" ' + 'x=y ' * 400) * 150]
        started = time.monotonic()
        for text in samples:
            self.assertLessEqual(len(text), 260 * 1024)
            _scan_preview_tags(text)
        self.assertLess(time.monotonic() - started, 1.0)


class ApprovalDeliveryTests(ServerTestBase):
    def member(self, admin=False):
        name = uname('fa')
        self.server.store.register_user(name, 'KCS 홀딩스', f'{name}@kcs.test', 'pw-1234')
        if admin:
            self.server.store.set_admin(name, True)
        c = Client(self.port, name, join=False)
        self.clients.append(c)
        c.request({'type': 'join', 'username': name, 'password': 'pw-1234'}, 'welcome')
        return c

    def test_failed_delivery_returns_the_request_to_pending(self):
        admin, user, peer = self.member(admin=True), self.member(), self.member()
        rid = self.group(user, [peer])['room_id']
        user.send({'type': 'file_send_request', 'room_id': rid, 'kind': 'file', 'reason': '업무용',
                   'attachment': {'name': 'tool.exe', 'mime': 'application/octet-stream', 'data_b64': b64(b'MZ' + b'\0' * 64)}})
        request_id = user.wait_for('file_request_ok')['request']['request_id']
        decide = {'type': 'admin_file_request_decide', 'request_id': request_id, 'approve': True}
        with mock.patch.object(self.server, '_deliver_message', side_effect=RuntimeError('전송 실패')):
            err = admin.expect_error(decide)
        self.assertIn('보내지 못했습니다', err['message'])
        self.assertEqual(self.server.store.file_request(request_id)['status'], 'pending')
        admin.request(decide, 'admin_done')
        done = self.server.store.file_request(request_id)
        self.assertEqual(done['status'], 'approved')
        self.assertIsNotNone(done['msg_id'])
        peer.wait_for('msg_new', lambda m: m['message']['kind'] == 'file')
