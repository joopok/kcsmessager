# -*- coding: utf-8 -*-
"""
코드 리뷰(메시지 · 설정 · 로그인) 지적 사항 회귀 테스트
- 서버: 삭제된 원문의 답장 미리보기, 삭제 후 수정 거부, 예약 메시지 한 건 실패 격리,
        로그인 실패 제한, 비밀번호 필수 모드, 최초 비밀번호 동시 등록
- 클라이언트: 재연결 후 빈 구간 채우기, 나갔다 다시 들어온 방 히스토리, 중복 msg_new 안 읽은 수,
        잠금 PIN 저장 실패 안내, 바깥에서 바꾼 설정과 스위치 동기화
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_server import ServerTestBase, Client, uname, iso, _counter


class ServerReviewFixTests(ServerTestBase):
    def test_deleted_original_hidden_in_replies(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        orig = self.send_text(a, rid, '비밀 내용')
        reply = self.send_text(b, rid, '답장', reply_to_id=orig['msg_id'])
        a.send({'type': 'delete', 'msg_id': orig['msg_id']})
        upd = b.wait_for('msg_updated', lambda m: m['message']['msg_id'] == reply['msg_id'])['message']
        self.assertEqual(upd['reply_to']['preview'], '메시지가 삭제되었습니다')
        hist = b.request({'type': 'history', 'room_id': rid}, 'history')
        stored = [m for m in hist['messages'] if m['msg_id'] == reply['msg_id']][0]
        self.assertNotIn('비밀', stored['reply_to']['preview'])

    def test_edit_updates_reply_preview_and_deleted_cannot_be_edited(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        orig = self.send_text(a, rid, '처음')
        reply = self.send_text(b, rid, '답장', reply_to_id=orig['msg_id'])
        a.send({'type': 'edit', 'msg_id': orig['msg_id'], 'content': '고친 글'})
        upd = b.wait_for('msg_updated', lambda m: m['message']['msg_id'] == reply['msg_id'])['message']
        self.assertEqual(upd['reply_to']['preview'], '고친 글')
        self.assertIsNone(self.server.store.edit_message(orig['msg_id'] + 10 ** 6, 'x'))
        a.send({'type': 'delete', 'msg_id': orig['msg_id']})
        a.wait_for('msg_updated', lambda m: m['message']['msg_id'] == orig['msg_id'] and m['message']['deleted'])
        self.assertIsNone(self.server.store.edit_message(orig['msg_id'], '되살리기'))   # 경쟁 상황
        self.assertEqual(self.server.store.get_message(orig['msg_id'])['content'], '메시지가 삭제되었습니다')

    def test_one_failing_scheduled_message_does_not_drop_others(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        send_at = iso(datetime.now() + timedelta(seconds=1))
        for text in ('예약-하나', '예약-둘'):
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': text, 'send_at': send_at})
            a.wait_for('scheduled_ok', lambda m, t=text: m['content'] == t)
        real = self.server._deliver_message
        calls = []

        def flaky(*args, **kwargs):
            calls.append(args[3])
            if args[3] == '예약-하나':
                raise RuntimeError('첫 건 실패')
            return real(*args, **kwargs)
        with mock.patch.object(self.server, '_deliver_message', side_effect=flaky):
            b.wait_for('msg_new', lambda m: m['message']['content'] == '예약-둘', timeout=6)
        self.assertIn('예약-하나', calls)


class LoginHardeningTests(ServerTestBase):
    def test_failed_logins_are_throttled_per_id(self):
        name = uname('thr')
        c, _ = self.register(name, 'right-pw')
        c.close()
        bad = self.connect(name, join=False)
        for _ in range(self.server.LOGIN_MAX_FAILURES['id']):
            bad.expect_error({'type': 'join', 'username': name, 'password': 'wrong-pw'})
        err = bad.expect_error({'type': 'join', 'username': name, 'password': 'right-pw'})
        self.assertIn('너무 많습니다', err['message'])          # 맞는 비밀번호도 잠시 막힘
        self.server._login_failures.clear()
        bad.request({'type': 'join', 'username': name, 'password': 'right-pw'}, 'welcome')

    def test_concurrent_first_logins_only_one_password_wins(self):
        name = uname('race')
        self.connect(name).close()
        results = []

        def claim(pw):
            results.append((pw, self.server.store.claim_password(name, pw)))
        threads = [threading.Thread(target=claim, args=(f'pw-{i}',)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [pw for pw, ok in results if ok]
        self.assertEqual(len(winners), 1)
        self.assertTrue(self.server.store.check_password(winners[0], self.server.store.password_hash(name)))


class RequirePasswordTests(ServerTestBase):
    SERVER_KWARGS = {'require_password': True, 'open_registration': True}   # 등록 방식은 test_kakao_registration 에서

    def test_passwordless_join_rejected(self):
        name = uname('req')
        c = self.connect(name, join=False)
        self.assertEqual(c.expect_error({'type': 'join', 'username': name})['message'], '비밀번호를 입력해 주세요.')
        self.register(name, 'pw-ok-123')
        c.request({'type': 'join', 'username': name, 'password': 'pw-ok-123'}, 'welcome')

    def test_admin_reset_script(self):
        from scripts.set_password import main as set_password
        name = uname('adm')
        self.register(name, 'old-pw-12', remember=True)
        self.assertEqual(set_password([name, '--db', self.db_path, '--password', 'new-pw']), 0)
        self.assertTrue(self.server.store.check_password('new-pw', self.server.store.password_hash(name)))
        self.assertEqual(set_password([name, '--db', self.db_path, '--clear']), 0)
        self.assertIsNone(self.server.store.password_hash(name))
        self.assertEqual(set_password(['없는사람', '--db', self.db_path, '--password', 'abcd']), 1)


class ClientReviewFixTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()
        self.rid = next(iter(self.state.rooms))

    def msg(self, msg_id, sender='김철수'):
        return {'msg_id': msg_id, 'room_id': self.rid, 'sender': sender, 'kind': 'text', 'content': f'm{msg_id}',
                'timestamp': '2026-10-09T10:00:00', 'reply_to': None, 'mentions': [], 'reactions': {},
                'deleted': False, 'attachment': None, 'event_id': None, 'poll': None}

    def test_duplicate_msg_new_counts_once(self):
        before = self.state.room_unread(self.rid)
        m = self.msg(900001)
        self.conn.push('msg_new', message=m, client_tmp_id=None)
        self.conn.push('msg_new', message=dict(m), client_tmp_id=None)
        self.assertEqual(self.state.room_unread(self.rid), before + 1)

    def test_rejoined_room_requests_history_again(self):
        self.state.load_history(self.rid)
        self.conn.push('room_removed', room_id=self.rid)
        self.assertNotIn(self.rid, self.state.history_requested)
        self.conn.sent.clear()
        self.state.rooms[self.rid] = {'room_id': self.rid, 'type': 'group', 'name': 'x', 'members': [],
                                      'unread_count': 0, 'read_pointers': {}, 'last_message': None}
        self.state.load_history(self.rid)
        self.assertEqual(self.conn.last('history'), {'room_id': self.rid, 'limit': 50})

    def history_requests(self):
        return [p for t, p in self.conn.sent if t == 'history' and p['room_id'] == self.rid]

    def test_reconnect_fills_gap(self):
        store = self.state._messages.setdefault(self.rid, {})
        store.clear()
        for i in range(1, 21):
            store[i] = self.msg(i)
        self.state.has_more[self.rid] = False
        welcome = {'me': self.state.users[self.state.me], 'users': list(self.state.users.values()),
                   'rooms': list(self.state.rooms.values())}
        self.state.open_rooms.add(self.rid)                          # 창이 떠 있는 방 (나머지 방은 차례로 받는다)
        self.conn.sent.clear()
        self.conn.push('welcome', **welcome)                         # 재연결
        self.assertEqual(self.history_requests(), [{'room_id': self.rid, 'limit': 50}])
        self.conn.push('history', room_id=self.rid, messages=[self.msg(i) for i in range(71, 121)], has_more=True)
        self.assertEqual(self.history_requests()[-1], {'room_id': self.rid, 'before_msg_id': 71, 'limit': 50})
        self.conn.push('history', room_id=self.rid, messages=[self.msg(i) for i in range(21, 71)], has_more=True)
        self.assertNotIn(self.rid, self.state._gap_fill)
        self.assertEqual(sorted(self.state._messages[self.rid])[:3], [1, 2, 3])
        self.assertEqual(len(self.state._messages[self.rid]), 120)
        self.assertFalse(self.state.has_more[self.rid])               # 예전 캐시 기준 유지

    def test_offline_history_request_not_marked(self):
        with mock.patch.object(self.state, '_send', return_value=None):
            self.assertFalse(self.state.load_history('새방'))
        self.assertNotIn('새방', self.state.history_requested)


class SettingsUiReviewFixTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, _ = make_demo_state()

    def test_external_setting_changes_sync_toggles(self):
        from client.kakao.more_tab import MoreTab
        tab = MoreTab(self.state, self.state.settings)
        self.state.settings.set('social_features', not tab.social_toggle.isChecked())
        self.assertEqual(tab.social_toggle.isChecked(), self.state.settings.get('social_features'))
        self.state.settings.set('reduce_motion', True)
        self.assertTrue(tab.motion_toggle.isChecked())
        self.state.settings.set('reduce_motion', False)

    def test_pin_save_failure_reported(self):
        from client.kakao.more_tab import LockSettingsDialog
        dlg = LockSettingsDialog(self.state, self.state.settings)
        with mock.patch.object(self.state.settings, '_write', return_value=False):
            msg = dlg.set_new_pin('1234', '1234')
        self.assertIn('저장하지 못했습니다', msg)


if __name__ == '__main__':
    unittest.main()
