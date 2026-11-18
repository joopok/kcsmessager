# -*- coding: utf-8 -*-
"""서버 규칙(2026-11-17 개정)에 맞춘 클라이언트 동작 - 새 필드가 없는 예전 서버에서도 그대로 동작해야 한다.
등록 코드 · 서버 정책 · 오류 구분 코드 · 연결 확인(ping/pong) · 안 읽은 수 · 비밀번호 변경 · 사진 종류 판단"""

import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_hardening import png_bytes

from client.kakao import net


def flush(ms=0):
    if ms:
        QTest.qWait(ms)
    QApplication.processEvents()


def serve_once(reply):
    """요청 한 줄을 받고 reply(dict) 한 줄로 답하는 서버 → 포트"""
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)

    def run():
        try:
            peer, _ = server.accept()
            peer.recv(65536)
            peer.sendall((json.dumps(reply, ensure_ascii=False) + '\n').encode('utf-8'))
            time.sleep(0.2)
            peer.close()
        except OSError:
            pass
        finally:
            server.close()
    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1]


# ================================================================== 처음 사용 등록: 등록 코드
class RegistrationCodeTests(unittest.TestCase):
    def setUp(self):
        app()

    def dialog(self, companies):
        from client.kakao import login
        dlg = login.LoginDialog(fresh=True)
        self.addCleanup(dlg.close)
        with mock.patch.object(login, 'fetch_companies', return_value=companies):
            dlg.set_mode('register')
            dlg.load_companies()
        dlg.company_combo.setCurrentIndex(1)
        dlg.reg_name_edit.setText('새사람')
        dlg.reg_email_edit.setText('new@kcs.test')
        dlg.reg_password_edit.setText('pw-12345678')
        return dlg

    def companies(self, open_registration):
        names = net.CompanyList(['KCS 홀딩스'])
        names.open_registration = open_registration
        return names

    def test_server_says_whether_registration_is_open(self):
        port = serve_once({'type': 'org_companies', 'companies': ['가', '나'], 'open_registration': False})
        names = net.fetch_companies('127.0.0.1', port)
        self.assertEqual(list(names), ['가', '나'])
        self.assertIs(names.open_registration, False)

    def test_older_server_does_not_say(self):
        port = serve_once({'type': 'org_companies', 'companies': ['가']})
        self.assertIsNone(net.fetch_companies('127.0.0.1', port).open_registration)

    def test_closed_server_asks_for_the_code_and_sends_it(self):
        dlg = self.dialog(self.companies(False))
        self.assertFalse(dlg.reg_code_edit.isHidden())
        self.assertIn('등록 코드', dlg._check_registration())
        dlg.reg_code_edit.setText(' ABCD-EF23 ')
        self.assertEqual(dlg._check_registration(), '')
        self.assertEqual(dlg.registration()['code'], 'ABCD-EF23')

    def test_open_server_hides_the_code(self):
        dlg = self.dialog(self.companies(True))
        self.assertTrue(dlg.reg_code_edit.isHidden())
        self.assertEqual(dlg._check_registration(), '')
        self.assertNotIn('code', dlg.registration())

    def test_older_server_registers_exactly_as_before(self):
        dlg = self.dialog(['KCS 홀딩스'])                      # 예전 서버: 목록만 온다
        self.assertTrue(dlg.reg_code_edit.isHidden())
        self.assertEqual(dlg.registration(), {'company': 'KCS 홀딩스', 'name': '새사람', 'email': 'new@kcs.test',
                                              'password': 'pw-12345678'})

    def test_new_passwords_need_eight_characters_but_login_still_takes_four(self):
        from client.kakao import login
        dlg = self.dialog(['KCS 홀딩스'])
        dlg.reg_password_edit.setText('1234567')
        self.assertIn('8', dlg._check_registration())
        self.assertEqual((login.PASSWORD_MIN, login.NEW_PASSWORD_MIN), (4, 8))


# ================================================================== 관리자 화면
class AdminCodeTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.admin import AdminDialog
        self.state, self.conn = make_demo_state()
        self.dlg = AdminDialog(self.state)
        self.addCleanup(self.dlg.close)
        users = [{'username': '미등록', 'registered': False, 'is_admin': False, 'company': 'KCS'},
                 {'username': '등록됨', 'registered': True, 'is_admin': False, 'company': 'KCS'},
                 {'username': '관리자', 'registered': True, 'is_admin': True, 'company': 'KCS'},
                 {'username': '지운사람', 'registered': True, 'is_admin': False, 'company': 'KCS', 'removed': True}]
        self.conn.push('admin_users', users=users)

    def select(self, name):
        self.dlg.show_removed.setChecked(True)
        self.assertTrue(self.dlg.select(name), name)

    def test_code_can_be_issued_for_someone_who_has_not_registered(self):
        self.select('미등록')
        self.assertTrue(self.dlg.code_button.isEnabled())
        self.assertIn('등록 코드', self.dlg.code_button.text())
        self.select('등록됨')
        self.assertTrue(self.dlg.code_button.isEnabled())
        self.assertIn('재설정 코드', self.dlg.code_button.text())
        self.select('관리자')
        self.assertFalse(self.dlg.code_button.isEnabled())

    def test_result_text_says_which_kind_of_code_it_is(self):
        done = {'action': 'reset_code', 'username': '미등록', 'code': 'ABCD-EF23', 'expires_at': '2026-11-17T12:30:00'}
        self.dlg._on_done(dict(done, registered=False))
        self.assertIn('등록 코드', self.dlg.status.text())
        self.assertIn('처음 사용 등록', self.dlg.status.text())
        self.dlg._on_done(dict(done, registered=True))
        self.assertIn('재설정 코드', self.dlg.status.text())
        self.dlg._on_done(done)                                # 예전 서버: registered 가 없다
        self.assertIn('재설정 코드', self.dlg.status.text())

    def test_new_member_text_mentions_the_code(self):
        self.dlg._on_done({'action': 'member_create', 'username': '새사람'})
        self.assertIn('등록 코드', self.dlg.status.text())

    def test_restoring_says_admin_rights_do_not_come_back(self):
        self.select('지운사람')
        with mock.patch.object(self.dlg, 'confirm', return_value=False) as asked:
            self.dlg.toggle_removed()
        self.assertIn('관리자 권한', asked.call_args[0][1])


# ================================================================== 서버 정책: 이메일은 관리자만 바꾼다
class ServerPolicyTests(unittest.TestCase):
    def welcome(self, state, conn, **extra):
        conn.push('welcome', me=state.users[state.me], users=list(state.users.values()),
                  rooms=list(state.rooms.values()), **extra)

    def test_policy_is_kept_and_defaults_to_the_old_behaviour(self):
        state, conn = make_demo_state()
        self.assertTrue(state.email_editable())
        self.welcome(state, conn, policy={'open_registration': False, 'email_editable': False})
        self.assertFalse(state.email_editable())
        self.welcome(state, conn)                               # 예전 서버로 다시 붙으면 정책이 없다
        self.assertTrue(state.email_editable())

    def test_profile_edit_locks_the_email_when_the_server_says_so(self):
        from client.kakao.friends_tab import ProfileEditDialog
        state, conn = make_demo_state()
        self.welcome(state, conn, policy={'email_editable': False})
        dlg = ProfileEditDialog(state)
        self.addCleanup(dlg.close)
        self.assertTrue(dlg.email_edit.isReadOnly())
        self.assertIn('관리자', dlg.email_hint.text())
        dlg.email_edit.setText('other@kcs.test')               # 칸이 바뀌어 있어도 보내지 않는다
        self.assertNotIn('email', dlg.values())

    def test_profile_edit_is_unchanged_without_a_policy(self):
        from client.kakao.friends_tab import ProfileEditDialog
        state, _conn = make_demo_state()
        dlg = ProfileEditDialog(state)
        self.addCleanup(dlg.close)
        self.assertFalse(dlg.email_edit.isReadOnly())
        self.assertTrue(dlg.email_hint.isHidden())
        dlg.email_edit.setText('new@kcs.test')
        self.assertEqual(dlg.values()['email'], 'new@kcs.test')


class DisplayNamePolicyTests(unittest.TestCase):
    """실명 고정 서버: 이름 칸은 보기만 한다 (정책이 없는 예전 서버에서는 지금처럼 고칠 수 있다)"""

    def state_with(self, policy=None):
        state, conn = make_demo_state()
        extra = {'policy': policy} if policy is not None else {}
        conn.push('welcome', me=state.users[state.me], users=list(state.users.values()),
                  rooms=list(state.rooms.values()), **extra)
        return state

    def test_profile_name_is_read_only_when_names_are_fixed(self):
        from client.kakao.friends_tab import ProfileEditDialog
        state = self.state_with({'display_name_editable': False})
        self.assertFalse(state.display_name_editable())
        dlg = ProfileEditDialog(state)
        self.addCleanup(dlg.close)
        self.assertTrue(dlg.name_edit.isReadOnly())
        self.assertIn('조직도', dlg.name_edit.toolTip())

    def test_multi_profile_name_is_read_only_when_names_are_fixed(self):
        from client.kakao.more_tab import MultiProfileDialog
        dlg = MultiProfileDialog(self.state_with({'display_name_editable': False}))
        self.addCleanup(dlg.close)
        self.assertTrue(dlg.name_edit.isReadOnly())

    def test_names_stay_editable_without_the_policy(self):
        from client.kakao.friends_tab import ProfileEditDialog
        from client.kakao.more_tab import MultiProfileDialog
        state = self.state_with()
        self.assertTrue(state.display_name_editable())
        profile, multi = ProfileEditDialog(state), MultiProfileDialog(state)
        self.addCleanup(profile.close)
        self.addCleanup(multi.close)
        self.assertFalse(profile.name_edit.isReadOnly())
        self.assertFalse(multi.name_edit.isReadOnly())


# ================================================================== 오류 구분 코드
class LoginErrorCodeTests(unittest.TestCase):
    def session(self):
        from client.kakao.app import Session
        app()
        s = Session('127.0.0.1', 1, 'tester', 'x')
        self.addCleanup(s.close)
        s.window = mock.Mock()                                  # 메인 창이 떠 있는 상태 (재연결 중)
        s.asked = []
        s.on_relogin_needed = s.asked.append
        return s

    def reject(self, s, **extra):
        s.state.handle('error', dict({'type': 'error', 'req_type': 'join', 'message': '거절'}, **extra))

    def test_too_many_attempts_keeps_the_login_and_waits(self):
        s = self.session()
        self.reject(s, code='throttled', retry_after=90)
        self.assertEqual(s.asked, [])
        self.assertTrue(s._retry_timer.isActive())
        self.assertGreaterEqual(s._retry_timer.interval(), 90000)
        self.assertFalse(s._closing)

    def test_server_trouble_keeps_the_login_and_retries(self):
        s = self.session()
        self.reject(s, code='server_error')
        self.assertEqual(s.asked, [])
        self.assertTrue(s._retry_timer.isActive())

    def test_wrong_credentials_go_back_to_the_login_window(self):
        for code in ('auth_failed', 'token_expired', 'account_disabled', 'password_required'):
            s = self.session()
            self.reject(s, code=code)
            self.assertEqual(s.asked, ['거절'], code)
            self.assertFalse(s._retry_timer.isActive())

    def test_older_server_without_codes_behaves_as_before(self):
        s = self.session()
        self.reject(s)
        self.assertEqual(s.asked, ['거절'])

    def test_expired_session_goes_to_login_and_does_not_reconnect(self):
        s = self.session()
        s.state.handle('error', {'type': 'error', 'code': 'session_expired', 'message': '비밀번호가 바뀌었습니다'})
        self.assertEqual(s.asked, ['비밀번호가 바뀌었습니다'])
        s._schedule_reconnect('끊김')                           # 서버가 곧이어 연결을 끊어도
        self.assertFalse(s._retry_timer.isActive())

    def test_saved_login_is_kept_when_the_first_attempt_is_only_delayed(self):
        from client.kakao.app import keeps_saved_login
        self.assertTrue(keeps_saved_login('throttled'))
        self.assertTrue(keeps_saved_login('server_error'))
        self.assertFalse(keeps_saved_login('token_expired'))
        self.assertFalse(keeps_saved_login(''))


# ================================================================== 연결 확인 (ping / pong)
class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        app()
        self.ours, self.theirs = socket.socketpair()
        self.addCleanup(self.theirs.close)
        self.conn = net.ChatConnection()
        self.conn.PING_INTERVAL, self.conn.DEAD_AFTER, self.conn.HEARTBEAT_TICK = 0.15, 0.5, 0.05
        self.events, self.lost = [], []
        self.conn.event_received.connect(lambda kind, p: self.events.append(kind), Qt.DirectConnection)
        self.conn.disconnected.connect(self.lost.append, Qt.DirectConnection)
        self.conn.attach(self.ours, '홍길동', password='pw')
        self.addCleanup(self.conn.close)
        self.buf = b''

    def received(self, seconds):
        self.theirs.settimeout(0.05)
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.buf += self.theirs.recv(65536)
            except socket.timeout:
                pass
        return [json.loads(line)['type'] for line in self.buf.split(b'\n') if line]

    def say(self, **message):
        self.theirs.sendall((json.dumps(message) + '\n').encode('utf-8'))

    def test_quiet_connection_sends_ping(self):
        self.assertIn('ping', self.received(0.6))

    def test_server_ping_is_answered_with_pong(self):
        self.say(type='ping')
        self.assertIn('pong', self.received(0.3))
        self.assertEqual(self.events, [])                       # 화면 쪽에는 올리지 않는다

    def test_older_server_that_never_answers_is_not_dropped(self):
        self.say(type='error', req_type='ping', message='알 수 없는 요청입니다: ping')
        self.received(1.0)
        self.assertEqual(self.lost, [])
        self.assertEqual(self.events, [])                       # 그 오류도 사용자에게 보이지 않는다
        self.assertTrue(self.conn.is_connected)

    def test_server_that_answered_once_and_then_went_silent_is_dropped(self):
        self.say(type='pong')
        self.received(1.2)
        self.assertEqual(len(self.lost), 1)
        self.assertFalse(self.conn.is_connected)

    def test_server_that_keeps_answering_stays_connected(self):
        end = time.time() + 1.0
        while time.time() < end:
            self.say(type='pong')
            self.received(0.1)
        self.assertEqual(self.lost, [])


# ================================================================== 끝난 통화가 되살아나지 않는다
class EndedCallTests(unittest.TestCase):
    def call(self, call_id=1):
        return {'call_id': call_id, 'room_id': 'r2', 'mode': 'voice', 'host': '홍길동', 'started_at': '2026-11-17T12:00:00',
                'participants': {'홍길동': {'role': 'host'}, '박민수': {'role': 'speaker'}}}

    def test_late_update_for_an_ended_call_is_ignored(self):
        state, conn = make_demo_state()
        conn.push('call_updated', call=self.call())
        conn.push('call_ended', call_id=1, room_id='r2')
        conn.push('call_updated', call=self.call())               # 끝난 뒤에 늦게 도착한 예전 상태
        self.assertIsNone(state.call_for_room('r2'))
        self.assertEqual(state.calls, {})

    def test_other_calls_and_a_new_connection_are_not_affected(self):
        state, conn = make_demo_state()
        conn.push('call_ended', call_id=1, room_id='r2')
        conn.push('call_updated', call=self.call(2))
        self.assertIn(2, state.calls)
        conn.push('welcome', me=state.users[state.me], users=list(state.users.values()), rooms=list(state.rooms.values()),
                  calls=[self.call(1)])                            # 서버를 다시 켜 번호가 처음부터여도 받아들인다
        self.assertIn(1, state.calls)
        conn.push('call_updated', call=self.call(1))
        self.assertIn(1, state.calls)


# ================================================================== 안 읽은 수
class UnreadCountTests(unittest.TestCase):
    def test_server_count_is_used_when_given(self):
        state, conn = make_demo_state()
        conn.push('read_update', room_id='r1', username=state.me, last_read_msg_id=9, unread_count=7)
        self.assertEqual(state.rooms['r1']['unread_count'], 7)

    def test_without_it_the_count_is_worked_out_as_before(self):
        state, conn = make_demo_state()
        last = max(state._messages['r1'])
        conn.push('read_update', room_id='r1', username=state.me, last_read_msg_id=last)
        self.assertEqual(state.rooms['r1']['unread_count'], 0)

    def test_someone_elses_read_does_not_touch_my_count(self):
        state, conn = make_demo_state()
        before = state.rooms['r1']['unread_count']
        conn.push('read_update', room_id='r1', username='김철수', last_read_msg_id=11, unread_count=0)
        self.assertEqual(state.rooms['r1']['unread_count'], before)


# ================================================================== 비밀번호 변경
class PasswordChangeTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.more_tab import PasswordChangeDialog
        self.state, self.conn = make_demo_state()
        self.dlg = PasswordChangeDialog(self.state)
        self.addCleanup(self.dlg.close)

    def fill(self, old='old-pass-1', new='new-pass-12', again=None):
        self.dlg.current_edit.setText(old)
        self.dlg.new_edit.setText(new)
        self.dlg.confirm_edit.setText(new if again is None else again)

    def test_input_is_checked_before_sending(self):
        for old, new, again, word in (('', 'new-pass-12', None, '현재'), ('old-pass-1', 'short', None, '8'),
                                      ('old-pass-1', 'new-pass-12', 'different', '다릅니다'),
                                      ('same-pass-1', 'same-pass-1', None, '같습니다')):
            self.fill(old, new, again)
            self.dlg.submit()
            self.assertIn(word, self.dlg.message.text())
        self.assertIsNone(self.conn.last('password_change'))

    def test_request_is_sent_and_success_closes_the_dialog(self):
        self.fill()
        self.dlg.submit()
        sent = self.conn.last('password_change')
        self.assertEqual((sent['old_password'], sent['new_password']), ('old-pass-1', 'new-pass-12'))
        self.conn.push('password_changed', login_token='tok-2')
        self.assertEqual(self.state.login_token, 'tok-2')
        self.assertEqual(self.dlg.result(), self.dlg.Accepted)

    def test_refusal_from_the_server_is_shown(self):
        self.fill()
        self.dlg.submit()
        self.conn.push('error', req_type='password_change', message='현재 비밀번호가 맞지 않습니다.')
        self.assertIn('현재 비밀번호가 맞지 않습니다', self.dlg.message.text())
        self.assertNotEqual(self.dlg.result(), self.dlg.Accepted)

    def test_older_server_that_does_not_know_the_request(self):
        self.fill()
        self.dlg.submit()
        self.conn.push('error', req_type='password_change', message='알 수 없는 요청입니다: password_change')
        self.assertIn('지원하지 않습니다', self.dlg.message.text())

    def test_saved_login_follows_the_new_token(self):
        from client.kakao import login
        path = os.path.join(tempfile.mkdtemp(prefix='kcs_saved_'), 'saved.json')
        login.save_saved_login('hong@kcs.test', 'tok-1', True, '127.0.0.1', 5555, path=path)
        self.assertTrue(login.replace_saved_token('tok-2', path=path))
        self.assertEqual(login.load_saved_login(path)['token'], 'tok-2')
        self.assertTrue(login.load_saved_login(path)['auto_login'])
        os.remove(path)
        self.assertFalse(login.replace_saved_token('tok-3', path=path))      # 저장한 로그인이 없으면 만들지 않는다
        self.assertFalse(os.path.exists(path))


# ================================================================== 사진인지는 내용으로 본다
class AttachmentKindTests(unittest.TestCase):
    def send(self, name, data):
        state, conn = make_demo_state()
        folder = tempfile.mkdtemp(prefix='kcs_kind_')
        path = os.path.join(folder, name)
        with open(path, 'wb') as f:
            f.write(data)
        self.assertIsNone(state.send_file('r1', path))
        sent = conn.last('send')
        return sent['kind'], sent['attachment']['mime']

    def test_real_photo_is_sent_as_photo(self):
        self.assertEqual(self.send('사진.png', png_bytes()), ('image', 'image/png'))

    def test_photo_with_another_extension_is_still_a_photo(self):
        self.assertEqual(self.send('scan.dat', png_bytes())[0], 'image')

    def test_file_that_only_has_a_photo_name_is_sent_as_file(self):
        self.assertEqual(self.send('그림.png', b'not really a picture')[0], 'file')

    def test_vector_and_unsupported_pictures_are_sent_as_files(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        self.assertEqual(self.send('logo.svg', svg)[0], 'file')
        self.assertEqual(self.send('photo.heic', b'\x00\x00\x00\x18ftypheic' + b'\x00' * 32)[0], 'file')

    def test_known_formats_are_recognised(self):
        from client.kakao.state import sniff_image_mime
        self.assertEqual(sniff_image_mime(b'\xff\xd8\xff\xe0' + b'\x00' * 16), 'image/jpeg')
        self.assertEqual(sniff_image_mime(b'GIF89a' + b'\x00' * 16), 'image/gif')
        self.assertEqual(sniff_image_mime(b'BM' + b'\x00' * 30), 'image/bmp')
        self.assertEqual(sniff_image_mime(b'RIFF\x00\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertEqual(sniff_image_mime(b'plain text'), '')


if __name__ == '__main__':
    unittest.main()
