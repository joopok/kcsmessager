# -*- coding: utf-8 -*-
"""로그인 실패 제한과 오류 구분 코드 (명세 §4.1 '보안' · '오류 구분 코드', 2026-11-17)
주소(IP)는 PROXY protocol 첫 줄로 정해 준다"""

from unittest import mock

from tests.test_kakao_server import ServerTestBase, Client, uname

from server.kakao.server import KakaoServer

IP_A, IP_B = '203.0.113.10', '203.0.113.20'


class ThrottleTestBase(ServerTestBase):
    SERVER_KWARGS = {'proxy_protocol': True}

    def setUp(self):
        super().setUp()
        self.store = self.server.store
        self.server._login_failures.clear()

    def at(self, ip):
        c = Client(self.port, 'x', join=False)
        self.clients.append(c)
        c.send_raw(f'PROXY TCP4 {ip} 10.0.0.1 40000 9550\r\n'.encode('ascii'))
        return c

    def user(self, password='pw-1234'):
        name = uname('thr')
        self.store.register_user(name, 'KCS 홀딩스', f'{name}@kcs.test', password)
        return name

    def wrong_logins(self, name, ip, times):
        c = self.at(ip)
        for _ in range(times):
            c.expect_error({'type': 'join', 'username': name, 'password': 'wrong-pw'})
        return c

    @staticmethod
    def good(name):
        return {'type': 'join', 'username': name, 'password': 'pw-1234'}


class LoginThrottleTests(ThrottleTestBase):
    def test_lock_is_per_address_so_the_owner_elsewhere_gets_in(self):
        name = self.user()
        limit = self.server.LOGIN_MAX_FAILURES['id']
        err = self.wrong_logins(name, IP_A, limit).expect_error(self.good(name))
        self.assertEqual(err['code'], 'throttled')
        self.assertIn('너무 많습니다', err['message'])
        self.assertTrue(0 < err['retry_after'] <= self.server.LOGIN_LOCK_SECONDS + 1)
        self.at(IP_B).request(self.good(name), 'welcome')

    def test_success_clears_only_this_address_and_id(self):
        name = self.user()
        c = self.wrong_logins(name, IP_A, self.server.LOGIN_MAX_FAILURES['id'] - 1)
        c.request(self.good(name), 'welcome')
        self.assertNotIn(f'at:{name.lower()}|{IP_A}', self.server._login_failures)
        self.assertTrue(self.server._login_failures.get(f'ip:{IP_A}'))          # 주소 전체 횟수는 시간이 지나야 풀린다

    def test_id_is_locked_everywhere_after_spread_out_failures(self):
        name = self.user()
        per_ip = self.server.LOGIN_MAX_FAILURES['id'] - 1
        total, n = 0, 0
        while total < self.server.LOGIN_MAX_FAILURES['id_all']:
            n += 1
            self.wrong_logins(name, f'198.51.100.{n}', per_ip)
            total += per_ip
        err = self.at(IP_B).expect_error(self.good(name))
        self.assertEqual(err['code'], 'throttled')

    def test_saved_login_token_passes_a_locked_id(self):
        name = self.user()
        token = self.at(IP_A).request({**self.good(name), 'remember': True}, 'welcome')['login_token']
        locked = self.wrong_logins(name, IP_A, self.server.LOGIN_MAX_FAILURES['id'])
        self.assertEqual(locked.expect_error(self.good(name))['code'], 'throttled')
        self.at(IP_A).request({'type': 'join', 'username': name, 'token': token}, 'welcome')
        bad = self.at(IP_A).expect_error({'type': 'join', 'username': name, 'token': 'x' * 43})
        self.assertEqual(bad['code'], 'token_expired')
        self.assertNotIn(f'id:{name.lower()}x', self.server._login_failures)
        self.assertEqual(len(self.server._login_failures[f'id:{name.lower()}']), self.server.LOGIN_MAX_FAILURES['id'])

    def test_whole_address_limit(self):
        name = self.user()
        token = self.at(IP_A).request({**self.good(name), 'remember': True}, 'welcome')['login_token']
        with mock.patch.dict(KakaoServer.LOGIN_MAX_FAILURES, {'ip': 6}):
            for _ in range(6):
                self.wrong_logins(self.user(), IP_A, 1)
            self.assertEqual(self.at(IP_A).expect_error(self.good(name))['code'], 'throttled')
            self.assertEqual(self.at(IP_A).expect_error({'type': 'join', 'username': name, 'token': token})['code'],
                             'throttled')
            self.at(IP_B).request(self.good(name), 'welcome')

    def test_default_address_limit_fits_a_shared_office(self):
        limits = KakaoServer.LOGIN_MAX_FAILURES
        self.assertGreaterEqual(limits['ip'], 100)
        self.assertGreater(limits['id_all'], limits['id'])

    def test_old_failure_records_are_dropped(self):
        old = -10.0 * self.server.LOGIN_LOCK_SECONDS
        with self.server._login_failures_lock:
            for i in range(self.server.LOGIN_FAILURE_KEYS_MAX + 10):
                self.server._login_failures[f'id:gone{i}'] = [old]
        self.wrong_logins(self.user(), IP_A, 1)
        self.assertLess(len(self.server._login_failures), 10)


class ErrorCodeTests(ThrottleTestBase):
    def code(self, payload, client=None):
        return (client or self.at(IP_A)).expect_error(payload).get('code')

    def test_login_errors_carry_codes(self):
        name = self.user()
        self.assertEqual(self.code({'type': 'join', 'username': name, 'password': 'wrong-pw'}), 'auth_failed')
        self.assertEqual(self.code({'type': 'join', 'username': name}), 'password_required')
        self.assertEqual(self.code({'type': 'join', 'username': name, 'token': 'nope'}), 'token_expired')
        self.assertEqual(self.code({'type': 'join', 'username': uname('nobody'), 'password': 'pw-1234'}),
                         'needs_register')
        self.assertEqual(self.code({'type': 'history', 'room_id': 'x'}), 'login_required')
        self.store.set_disabled(name, True)
        self.assertEqual(self.code(self.good(name)), 'account_disabled')
        self.assertEqual(self.code({'type': 'password_reset', 'username': uname('nobody'), 'email': 'a@kcs.test',
                                    'new_password': 'pw-1234', 'code': 'ABCD-EFGH'}), 'reset_mismatch')

    def test_register_and_session_errors_carry_codes(self):
        name = self.user()
        reg = {'type': 'register', 'company': 'KCS 홀딩스', 'name': name, 'email': f'{name}@kcs.test',
               'password': 'pw-1234'}
        self.assertEqual(self.code(reg), 'register_rejected')                   # 이미 등록된 이름
        c = self.at(IP_A)
        c.request(self.good(name), 'welcome')
        self.assertEqual(self.code(self.good(name), c), 'already_logged_in')
        self.assertEqual(self.code({'type': 'admin_users'}, c), 'admin_only')

    def test_plain_validation_errors_have_no_code(self):
        name = self.user()
        c = self.at(IP_A)
        c.request(self.good(name), 'welcome')
        err = c.expect_error({'type': 'profile_update'})
        self.assertNotIn('code', err)
        self.assertEqual(set(err), {'type', 'req_type', 'message', 'req_id'})    # 예전과 같은 모양

    def test_server_error_code(self):
        name = self.user()
        c = self.at(IP_A)
        c.request(self.good(name), 'welcome')
        with mock.patch.object(self.server.store, 'list_todos', side_effect=RuntimeError('boom')):
            self.assertEqual(c.expect_error({'type': 'todo_list'})['code'], 'server_error')


class ClosedServerCodeTests(ThrottleTestBase):
    SERVER_KWARGS = {'proxy_protocol': True, 'require_password': True}

    def test_closed_server_codes(self):
        name = uname('out')
        err = self.at(IP_A).expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': name,
                                          'email': f'{name}@kcs.test', 'password': 'pw-123456'})
        self.assertEqual(err['code'], 'register_closed')
        self.server._login_failures.clear()
        err = self.at(IP_A).expect_error({'type': 'join', 'username': name, 'password': 'pw-1234'})
        self.assertEqual(err['code'], 'auth_failed')
