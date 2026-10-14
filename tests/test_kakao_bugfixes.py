# -*- coding: utf-8 -*-
"""전체 점검(2026-10-11)에서 찾아 고친 오류의 회귀 테스트"""

import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from collections import deque
from unittest import mock

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_server import ServerTestBase, Client, uname

from server.kakao.server import parse_iso, ProtocolError, _req_int


# ================================================================== 서버
class ServerInputTests(ServerTestBase):
    def member(self, admin=False, company=''):
        name = uname('bf')
        store = self.server.store
        store.register_user(name, 'KCS 홀딩스', f'{name}@kcs.test', 'pw-1234', employee_no=f'B{name}'[:20])
        if admin:
            store.set_admin(name, True, company)
        c = Client(self.port, name, join=False)
        self.clients.append(c)
        c.request({'type': 'join', 'username': name, 'password': 'pw-1234'}, 'welcome')
        return c

    def test_bad_values_are_protocol_errors_not_server_errors(self):
        a, b = self.member(), self.member()
        room = self.group(a, [b])
        err = a.expect_error({'type': 'send', 'room_id': room['room_id'], 'kind': 'voice', 'content': '²',
                              'attachment': {'name': 'v.m4a', 'mime': 'audio/mp4', 'data_b64': 'AAAA'}})
        self.assertIn('음성메시지', err['message'])                         # '²'.isdigit() 이지만 int() 는 실패
        err = a.expect_error({'type': 'history', 'room_id': room['room_id'], 'before_msg_id': 2 ** 70})
        self.assertIn('값이 올바르지 않습니다', err['message'])             # sqlite OverflowError 가 아니라
        err = a.expect_error({'type': 'profile_update', 'mobile': 5})
        self.assertNotIn('서버 오류', err['message'])
        err = a.expect_error({'type': 'profile_update', 'email': ['x']})
        self.assertNotIn('서버 오류', err['message'])
        with self.assertRaises(ProtocolError):
            parse_iso('0001-01-01T00:00:00+09:00', 'send_at')
        with self.assertRaises(ProtocolError):
            _req_int({'n': -2 ** 63 - 1}, 'n')

    def test_company_admin_cannot_manage_admins(self):
        group_admin = self.member(admin=True)                                # 회사: KCS 홀딩스
        company_admin = self.member(admin=True, company='KCS 홀딩스')
        other_company_admin = self.member(admin=True, company='KCS 홀딩스')
        for payload in ({'type': 'admin_reset_password', 'username': group_admin.username, 'new_password': 'hack1'},
                        {'type': 'admin_clear_password', 'username': group_admin.username},
                        {'type': 'admin_set_disabled', 'username': other_company_admin.username, 'disabled': True},
                        {'type': 'admin_member_delete', 'username': group_admin.username}):
            err = company_admin.expect_error(payload)
            self.assertEqual(err['message'], '관리자 계정은 그룹 관리자만 관리할 수 있습니다.', payload['type'])
        self.assertFalse(self.server.store.is_disabled(other_company_admin.username))
        plain = self.member()
        company_admin.request({'type': 'admin_set_disabled', 'username': plain.username, 'disabled': True},
                              'admin_done')                                  # 일반 구성원은 그대로 관리
        group_admin.request({'type': 'admin_reset_password', 'username': company_admin.username,
                             'new_password': 'reset-1'}, 'admin_done')        # 그룹 관리자는 된다

    def test_removed_member_stays_locked_out(self):
        admin = self.member(admin=True)
        target = self.member()
        admin.request({'type': 'admin_member_delete', 'username': target.username}, 'admin_done')
        err = admin.expect_error({'type': 'admin_set_disabled', 'username': target.username, 'disabled': False})
        self.assertIn('복원', err['message'])
        self.server.store.set_disabled(target.username, False)                # 저장소에서 직접 풀려도
        self.assertTrue(self.server.store.is_disabled(target.username))       # 삭제된 사람은 로그인 막힘
        c = Client(self.port, target.username, join=False)
        self.clients.append(c)
        self.assertIn('사용이 중지된', c.expect_error({'type': 'join', 'username': target.username,
                                                       'password': 'pw-1234'})['message'])

    def test_login_throttle_does_not_keep_empty_entries(self):
        before = len(self.server._login_failures)
        for i in range(30):
            c = Client(self.port, f'없는아이디{i}', join=False)
            self.clients.append(c)
            c.expect_error({'type': 'join', 'username': f'없는아이디{i}', 'password': 'x'})
        self.server._check_login_throttle([f'id:없는아이디{i}' for i in range(30)])
        self.assertTrue(all(self.server._login_failures.values()))
        self.assertLessEqual(len(self.server._login_failures) - before, 60)


class AccountSecurityTests(ServerTestBase):
    def fresh(self, name, **payload):
        c = Client(self.port, name, join=False)
        self.clients.append(c)
        return c

    def test_other_users_email_is_hidden_and_friend_add_by_username(self):
        a, b = self.register()[0], self.register()[0]
        admin_name = uname('ad')
        self.server.store.register_user(admin_name, 'KCS 홀딩스', f'{admin_name}@kcs.test', 'pw-1234',
                                        employee_no=f'A{admin_name}'[:20])
        self.server.store.set_admin(admin_name, True)
        admin = self.fresh(admin_name)
        welcome = admin.request({'type': 'join', 'username': admin_name, 'password': 'pw-1234'}, 'welcome')
        self.assertEqual({u['username']: u for u in welcome['users']}[a.username]['email'], f'{a.username}@kcs.test')
        b.clear()
        a.send({'type': 'profile_update', 'status_message': '바쁨'})
        seen = b.wait_for('user_updated', lambda m: m['user']['username'] == a.username)['user']
        self.assertEqual(seen['email'], '')                                     # 다른 사람에게는 이메일 없음
        mine = a.wait_for('user_updated', lambda m: m['user']['username'] == a.username)['user']
        self.assertEqual(mine['email'], f'{a.username}@kcs.test')               # 본인에게는 있음
        added = b.request({'type': 'friend_add', 'username': a.username}, 'friend_added')
        self.assertEqual((added['user']['username'], added['user']['email']), (a.username, ''))
        self.assertIn('찾을 수 없', b.expect_error({'type': 'friend_add', 'username': '없는사람xyz'})['message'])

    def test_unregistered_org_account_needs_matching_employee_no(self):
        name = uname('org')
        store = self.server.store
        store.member_create(name, {'company': 'KCS 홀딩스', 'employee_no': 'K777'})
        err = self.fresh(name).expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': name,
                                              'employee_no': 'X999', 'email': f'{name}@kcs.test', 'password': 'pw-1'})
        self.assertIn('조직도에 등록된 사번과 다릅니다', err['message'])
        # 사번 칸이 없는 클라이언트(2026-10-14): 조직도에 이메일도 없으면 확인할 값이 없어 거절
        err = self.fresh(name).expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': name,
                                              'email': f'{name}@kcs.test', 'password': 'pw-1'})
        self.assertEqual(err['message'], store.REGISTER_NEEDS_ORG_EMAIL)
        self.fresh(name).request({'type': 'register', 'company': 'KCS 홀딩스', 'name': name, 'employee_no': 'k777',
                                  'email': f'{name}@kcs.test', 'password': 'pw-1'}, 'welcome')
        # 조직도에 사번·이메일이 모두 있으면 사번 없이 이메일만 맞아도 된다
        both = uname('org')
        store.member_create(both, {'company': 'KCS 홀딩스', 'employee_no': 'K778', 'email': f'{both}@kcs.test'})
        err = self.fresh(both).expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': both,
                                              'email': f'not-{both}@kcs.test', 'password': 'pw-1'})
        self.assertEqual(err['message'], store.REGISTER_WRONG_EMAIL)
        w = self.fresh(both).request({'type': 'register', 'company': 'KCS 홀딩스', 'name': both,
                                      'email': f'{both}@kcs.test', 'password': 'pw-1'}, 'welcome')
        self.assertEqual(w['me']['employee_no'], 'K778')                           # 조직도 사번은 그대로

    def test_admin_accounts_cannot_be_claimed_or_reset_by_email(self):
        store = self.server.store
        name = uname('adm')
        store.member_create(name, {'company': 'KCS 홀딩스'})
        store.set_admin(name, True)
        err = self.fresh(name).expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': name,
                                              'employee_no': 'Z1', 'email': f'{name}@kcs.test', 'password': 'pw-1'})
        self.assertIn('관리자 계정은 직접 등록할 수 없습니다', err['message'])
        other = uname('adm')
        store.register_user(other, 'KCS 홀딩스', f'{other}@kcs.test', 'pw-1234', employee_no=f'Q{other}'[:20])
        store.set_admin(other, True)
        err = self.fresh('x').expect_error({'type': 'password_reset', 'username': other, 'code': 'ABCD-EFGH',
                                             'email': f'{other}@kcs.test', 'new_password': 'hacked'})
        self.assertIn('관리자 계정은 직접 재설정할 수 없습니다', err['message'])
        self.assertTrue(store.check_password('pw-1234', store.password_hash(other)))


class ResetCodeTests(ServerTestBase):
    def test_admin_issues_code_and_user_resets_once(self):
        admin_name, user = uname('rc'), uname('rc')
        store = self.server.store
        store.register_user(admin_name, 'KCS 홀딩스', f'{admin_name}@kcs.test', 'pw-1234', employee_no=f'C{admin_name}'[:20])
        store.set_admin(admin_name, True)
        store.register_user(user, 'KCS 홀딩스', f'{user}@kcs.test', 'old-pass', employee_no=f'U{user}'[:20])
        admin = Client(self.port, admin_name, join=False)
        self.clients.append(admin)
        admin.request({'type': 'join', 'username': admin_name, 'password': 'pw-1234'}, 'welcome')
        first = admin.request({'type': 'admin_reset_code', 'username': user}, 'admin_done')
        done = admin.request({'type': 'admin_reset_code', 'username': user}, 'admin_done')   # 다시 발급하면 예전 코드 무효
        self.assertRegex(done['code'], r'^[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertIn('관리자 계정은', admin.expect_error({'type': 'admin_reset_code', 'username': admin_name})['message'])
        c = Client(self.port, 'x', join=False)
        self.clients.append(c)
        base = {'type': 'password_reset', 'username': user, 'email': f'{user}@kcs.test', 'new_password': 'new-pass'}
        if first['code'] != done['code']:
            self.assertIn('맞지 않습니다', c.expect_error({**base, 'code': first['code']})['message'])
        c.request({**base, 'code': done['code']}, 'password_reset_ok')
        self.assertTrue(store.check_password('new-pass', store.password_hash(user)))
        self.assertIn('맞지 않습니다', c.expect_error({**base, 'code': done['code'], 'new_password': 'again'})['message'])

    def test_expired_code_is_rejected(self):
        user = uname('rc')
        store = self.server.store
        store.register_user(user, 'KCS 홀딩스', f'{user}@kcs.test', 'old-pass', employee_no=f'E{user}'[:20])
        code, _ = store.issue_reset_code(user, 'admin')
        with store._lock:
            store._db.execute("UPDATE reset_codes SET expires_at='2000-01-01T00:00:00' WHERE username=?", (user,))
            store._db.commit()
        self.assertFalse(store.reset_password(user, f'{user}@kcs.test', 'new-pass', code))

    def test_admin_dialog_button_shows_code(self):
        app()
        state, conn = make_demo_state()
        from client.kakao.admin import AdminDialog
        dlg = AdminDialog(state)
        self.addCleanup(dlg.close)
        conn.push('admin_users', users=[{'username': '김철수', 'registered': True, 'company': 'KCS'}], scope='')
        dlg.select('김철수')
        self.assertTrue(dlg.code_button.isEnabled())
        dlg.code_button.click()
        self.assertEqual(conn.last('admin_reset_code'), {'username': '김철수'})
        conn.push('admin_done', action='reset_code', username='김철수', code='ABCD-EF23', expires_at='2026-10-11T10:30:00')
        self.assertIn('ABCD-EF23', dlg.status.text())
        self.assertIn('10:30까지', dlg.status.text())


class ScheduledFailureTests(ServerTestBase):
    def test_sender_is_told_and_attachment_discarded(self):
        a, b = self.register()[0], self.register()[0]
        room = self.group(a, [b])
        store = self.server.store
        attachment_id = store.save_attachment('a.txt', 'text/plain', b'data', a.username)
        store.add_scheduled(room['room_id'], a.username, '2000-01-01T00:00:00',
                            {'kind': 'file', 'content': 'a.txt', 'attachment_id': attachment_id})
        a.request({'type': 'room_leave', 'room_id': room['room_id']}, 'room_removed')
        failed = a.wait_for('scheduled_failed', timeout=5)
        self.assertEqual((failed['room_id'], failed['content']), (room['room_id'], 'a.txt'))
        self.assertIn('나가서', failed['reason'])
        self.assertEqual(store.get_attachment(attachment_id)['data'], b'')

    def test_client_shows_notice(self):
        app()
        state, conn = make_demo_state()
        notes, errors = [], []
        state.notify.connect(lambda t, body, rid: notes.append(t))
        state.error.connect(errors.append)
        conn.push('scheduled_failed', scheduled_id=1, room_id='r1', kind='text', content='내일 회의',
                  reason='채팅방을 나가서 예약 메시지가 취소되었습니다.')
        self.assertEqual(notes, ['예약 메시지를 보내지 못했습니다'])
        self.assertIn('나가서', errors[0])


class StoreRollbackTests(unittest.TestCase):
    def test_failed_method_does_not_leave_half_written_rows(self):
        from server.kakao.store import Store, _locked
        tmp = tempfile.mkdtemp()
        store = Store(os.path.join(tmp, 't.db'))
        self.addCleanup(store.close)

        @_locked
        def half_then_fail(self):
            self._db.execute("INSERT INTO org_settings(key, value) VALUES ('half', 'x')")
            raise ValueError('중간 실패')

        with self.assertRaises(ValueError):
            half_then_fail(store)
        store.set_org_order([])                                               # 다음 요청의 commit
        with store._lock:
            self.assertIsNone(store._one("SELECT 1 FROM org_settings WHERE key='half'"))


class FilePolicyBypassTests(ServerTestBase):
    EXE = b'MZ' + b'\0' * 62

    def test_name_exception_does_not_pass_disguised_executable(self):
        from server.kakao.file_policy import DEFAULT_POLICY
        store = self.server.store
        store.set_file_policy({**DEFAULT_POLICY, 'blocked_extensions': ['dwg']})
        self.addCleanup(store.set_file_policy, dict(DEFAULT_POLICY))
        name = uname('fx')
        store.register_user(name, 'KCS 홀딩스', f'{name}@kcs.test', 'pw-1234', employee_no=f'X{name}'[:20])
        store.add_file_exception(name, '', '', '설계도.dwg', '2999-01-01T00:00:00', '', 'admin')
        self.assertIsNone(self.server._file_violation(name, '설계도.dwg', b'AC1032'))     # 진짜 도면은 예외로 통과
        violation = self.server._file_violation(name, '설계도.dwg', self.EXE)             # 이름만 같은 실행 파일
        self.assertTrue(violation['disguised'])


class LinkPreviewGuardTests(unittest.TestCase):
    def test_redirect_to_internal_address_is_not_followed(self):
        from server.kakao import server as server_mod
        with mock.patch.object(server_mod, 'preview_host_allowed', side_effect=lambda u: '127.0.0.1' not in u):
            self.assertIsNone(server_mod.fetch_link_preview('http://127.0.0.1:1/'))


# ================================================================== 클라이언트
class SecretKeyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_unreadable_key_is_not_replaced(self):
        from client.kakao.secret import SecretKeyring
        ring = SecretKeyring('홍길동', self.dir)
        public = ring.public_key_b64
        with mock.patch('builtins.open', side_effect=PermissionError('잠김')):
            with self.assertRaises(PermissionError):
                SecretKeyring('홍길동', self.dir)
        self.assertEqual(SecretKeyring('홍길동', self.dir).public_key_b64, public)   # 예전 키 그대로

    def test_corrupt_key_is_kept_as_backup(self):
        from client.kakao.secret import SecretKeyring
        path = os.path.join(self.dir, '홍길동.key')
        with open(path, 'wb') as f:
            f.write(b'broken')
        SecretKeyring('홍길동', self.dir)
        with open(path + '.bak', 'rb') as f:
            self.assertEqual(f.read(), b'broken')
        self.assertFalse(os.path.exists(path + '.tmp'))


class ConnectionTests(unittest.TestCase):
    def test_send_failure_closes_socket_once(self):
        app()
        from client.kakao.net import ChatConnection
        conn = ChatConnection()
        sock = mock.Mock()
        sock.sendall.side_effect = OSError('broken pipe')
        conn._sock = sock
        lost = []
        conn.disconnected.connect(lost.append)
        self.assertIsNone(conn.send('ping'))
        conn._handle_disconnect('읽기 스레드도 끝남')                          # 두 번째는 무시
        self.assertEqual(len(lost), 1)
        sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)               # 소켓을 닫아 읽기 스레드도 끝낸다
        sock.close.assert_called_once()
        self.assertFalse(conn.is_connected)


class SessionReconnectTests(ServerTestBase):
    def test_manual_reconnect_cancels_pending_auto_retry(self):
        from client.kakao.app import Session, wait_for_login
        name = uname('rc')
        self.server.store.register_user(name, 'KCS 홀딩스', f'{name}@kcs.test', 'pw-1234', employee_no=f'R{name}'[:20])
        s = Session('127.0.0.1', self.port, name, 'pw-1234')
        self.addCleanup(s.close)
        s.connect()
        self.assertEqual(wait_for_login(s.state, 5000)[0], 'ok')
        s._retry_timer.start(60000)                                           # 자동 재연결이 예약된 상태
        s._retry = 3
        ok, _ = s.reconnect()
        self.assertTrue(ok)
        self.assertFalse(s._retry_timer.isActive())
        self.assertEqual(s._retry, 0)
        self.assertIs(s.state.conn, s.conn)
        with mock.patch.object(s, 'reconnect') as again:
            s._auto_reconnect()                                               # 이미 붙어 있으면 다시 붙지 않는다
        again.assert_not_called()

    def test_failed_reconnect_keeps_old_connection(self):
        from client.kakao.app import Session
        s = Session('127.0.0.1', 1, uname('rc'), 'x')
        self.addCleanup(s.close)
        old = s.conn
        ok, message = s.reconnect()
        self.assertFalse(ok)
        self.assertIn('재연결 실패', message)
        self.assertIs(s.state.conn, old)


class StateTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()
        self.rid = next(iter(self.state.rooms))

    def test_older_history_pages_from_history_not_thread_root(self):
        self.state._messages[self.rid] = {}                                  # 처음 여는 방
        self.state._history_oldest.pop(self.rid, None)
        self.conn.push('history', room_id=self.rid, has_more=True, messages=[
            {'msg_id': 500, 'room_id': self.rid, 'sender': '홍길동', 'kind': 'text', 'content': 'a',
             'timestamp': '2026-10-11T09:00:00'}])
        self.conn.push('thread', room_id=self.rid, root_id=7, messages=[],
                       root={'msg_id': 7, 'room_id': self.rid, 'sender': '홍길동', 'kind': 'text', 'content': '원글',
                             'timestamp': '2026-01-01T09:00:00'})
        self.state.load_history(self.rid, older=True)
        self.assertEqual(self.conn.last('history')['before_msg_id'], 500)

    def test_pending_open_cleared_on_error(self):
        self.state._pending_direct.add('김철수')
        self.state._pending_groups.append({self.state.me})
        self.conn.push('error', req_type='room_open_secret', message='상대가 아직 키를 등록하지 않았습니다.')
        self.assertEqual((self.state._pending_direct, self.state._pending_groups), (set(), []))

    def test_failed_scheduled_message_keeps_options(self):
        from datetime import datetime, timedelta
        when = datetime.now() + timedelta(hours=1)
        self.conn.is_connected = False
        with mock.patch.object(self.conn, 'send', return_value=None):
            self.state.send_text(self.rid, '내일 회의', reply_to_id=3, send_at=when, silent=True)
        failed = self.state.failed_messages(self.rid)[-1]
        self.conn.is_connected = True
        self.assertIsNone(self.state.resend_failed(self.rid, failed['msg_id']))
        sent = self.conn.last('send')
        self.assertEqual((sent['reply_to_id'], sent['send_at'], sent['silent']),
                         (3, when.isoformat(timespec='seconds'), True))

    def test_unreadable_file_returns_message(self):
        with mock.patch('builtins.open', side_effect=PermissionError('권한 없음')), \
                mock.patch('os.path.getsize', return_value=10):
            self.assertIn('파일을 열 수 없습니다', self.state.send_file(self.rid, '/x/잠긴.txt'))
            self.assertIn('파일을 열 수 없습니다', self.state.add_rag_doc('/x/잠긴.pdf'))
            self.assertIn('파일을 열 수 없습니다', self.state.post_story('image', path='/x/잠긴.png'))


class WidgetTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_image_save_failure_is_reported(self):
        from client.kakao.chat_room import ImageViewer
        viewer = ImageViewer(b'img', 'a.png')
        self.addCleanup(viewer.close)
        with mock.patch('client.kakao.chat_room.QFileDialog.getSaveFileName', return_value=('/없는/폴더/a.png', '')), \
                mock.patch('client.kakao.chat_room.QMessageBox.warning') as warn:
            self.assertFalse(viewer.save())
        warn.assert_called_once()

    def test_export_collector_cancel_restores_error_reporting(self):
        from client.kakao.chat_export import AttachmentCollector
        msg = {'msg_id': 1, 'kind': 'file', 'attachment': {'attachment_id': 9, 'name': 'a.txt'}, 'deleted': False}
        collector = AttachmentCollector(self.state, [msg])
        done = []
        collector.finished.connect(lambda *a: done.append(a))
        collector.start()
        self.assertTrue(self.state.quiet_attachment_errors)
        collector.cancel()
        self.assertFalse(self.state.quiet_attachment_errors)
        self.assertEqual(done, [])

    def test_recording_write_error_stops_recording_only(self):
        from client.kakao.call import CallWindow
        toasts = []
        rec = mock.Mock()
        rec.write.side_effect = OSError('디스크 가득 참')
        fake = types.SimpleNamespace(recorder=rec, _rec_in=deque([b'\0' * 640]), _rec_out=deque(), closed=True,
                                     show_toast=toasts.append, _sync_buttons=lambda: None)
        fake._abort_recording = lambda e: CallWindow._abort_recording(fake, e)
        CallWindow._flush_recorder(fake)
        self.assertIsNone(fake.recorder)
        self.assertIn('녹음을 저장하지 못해', toasts[-1])
        self.assertIsNone(CallWindow.stop_recording(fake))

    def test_reopened_dialogs_do_not_pile_up_handlers(self):
        from client.kakao.more_tab import MultiProfileDialog, FriendManageDialog
        for cls in (MultiProfileDialog, FriendManageDialog):
            dlg = cls(self.state)
            dlg.done(0)
        with mock.patch.object(MultiProfileDialog, '_refresh_avatar') as refresh:
            self.state.attachment_loaded.emit(1, b'', 'a', 'image/png')
        refresh.assert_not_called()

    def test_member_edit_wait_has_timeout(self):
        from client.kakao import admin
        from PyQt5.QtWidgets import QWidget
        parent = QWidget()
        self.addCleanup(parent.close)
        waiter = admin._EditResult(self.state, '김철수', parent)
        self.assertTrue(waiter._timeout.isActive())
        self.assertEqual(waiter._timeout.interval(), admin._EditResult.WAIT_MS)


class CrashGuardTests(unittest.TestCase):
    def test_hook_logs_and_keeps_running(self):
        import io
        import sys
        from client.kakao.app import install_crash_guard
        old_hook, old_err = sys.excepthook, sys.stderr
        self.addCleanup(setattr, sys, 'excepthook', old_hook)
        self.addCleanup(setattr, sys, 'stderr', old_err)
        hook = install_crash_guard()
        sys.stderr = io.StringIO()
        try:
            raise KeyError('root')
        except KeyError:
            hook(*sys.exc_info())
        self.assertIn("KeyError: 'root'", sys.stderr.getvalue())

    def test_ctrl_c_quits_instead_of_traceback_loop(self):
        # 터미널 Ctrl+C: 예전에는 eventFilter 안 KeyboardInterrupt 트레이스백만 찍히고 앱이 계속 돌았다
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os, signal, sys\n"
                "from PyQt5.QtCore import QTimer\n"
                "from PyQt5.QtWidgets import QApplication, QLineEdit\n"
                "from client.kakao.app import install_crash_guard, install_interrupt_quit\n"
                "app = QApplication([])\n"
                "w = QLineEdit(); w.installEventFilter(w); w.show()\n"
                "install_crash_guard(); install_interrupt_quit(app)\n"
                "QTimer.singleShot(300, lambda: os.kill(os.getpid(), signal.SIGINT))\n"
                "QTimer.singleShot(5000, lambda: app.exit(7))\n"
                "sys.exit(app.exec_())\n")
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        done = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(done.returncode, 130, done.stderr)            # 5초 타임아웃(7)이 아니라 Ctrl+C 로 끝남
        self.assertNotIn('KeyboardInterrupt', done.stderr)

    def test_keyboard_interrupt_in_slot_quits(self):
        from PyQt5.QtCore import QCoreApplication
        from client.kakao import app as app_mod
        old_hook = sys.excepthook
        self.addCleanup(setattr, sys, 'excepthook', old_hook)
        hook = app_mod.install_crash_guard()
        with mock.patch.object(QCoreApplication, 'exit') as exit_:
            hook(KeyboardInterrupt, KeyboardInterrupt(), None)
        exit_.assert_called_once_with(app_mod.INTERRUPT_EXIT_CODE)


if __name__ == '__main__':
    unittest.main()
