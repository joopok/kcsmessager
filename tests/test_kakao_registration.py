# -*- coding: utf-8 -*-
"""등록 방식 (명세 §4.1 '등록 방식'): 등록이 닫힌 서버는 조직도 구성원 + 관리자가 발급한 등록 코드만 받는다"""

from tests.test_kakao_server import ServerTestBase, Client, uname

from server.chat.chat_server import parse_args
from server.kakao.server import KakaoServer, LOGIN_FAILED, LOGIN_FAILED_CLOSED, RESET_MISMATCH
from server.kakao.store import Store

CLOSED = Store.REGISTER_CLOSED
CODE_RE = r'^[A-Z2-9]{4}-[A-Z2-9]{4}$'


class RegistrationTestBase(ServerTestBase):
    def setUp(self):
        super().setUp()
        self.store = self.server.store
        self.server._login_failures.clear()       # 거절은 로그인 실패로 세므로 테스트끼리 섞이지 않게

    def fresh(self):
        c = Client(self.port, 'x', join=False)
        self.clients.append(c)
        return c

    def member(self, company='KCS 홀딩스', **fields):
        name = uname('mem')
        self.store.member_create(name, {'company': company, **fields})
        return name

    def code_for(self, name):
        return self.store.issue_reset_code(name, '관리자')[0]

    @staticmethod
    def reg(name, **extra):
        return {'type': 'register', 'company': 'KCS 홀딩스', 'name': name, 'email': f'{name}@kcs.test',
                'password': 'pw-123456', **extra}

    def refused(self, payload):
        message = self.fresh().expect_error(payload)['message']
        self.server._login_failures.clear()
        return message


class ClosedRegistrationTests(RegistrationTestBase):
    SERVER_KWARGS = {'require_password': True}

    def test_closed_is_the_default_when_passwords_are_required(self):
        self.assertFalse(self.server.open_registration)
        self.assertTrue(KakaoServer().open_registration)                      # 테스트 · 데모 서버는 열림
        self.assertTrue(KakaoServer(require_password=True, open_registration=True).open_registration)
        self.assertFalse(parse_args([]).open_registration)
        self.assertTrue(parse_args(['--open-registration']).open_registration)

    def test_name_outside_the_org_is_not_created(self):
        name = uname('out')
        self.assertEqual(self.refused(self.reg(name)), CLOSED)
        self.assertEqual(self.refused(self.reg(name, code='ABCD-EFGH')), CLOSED)
        self.assertFalse(self.store.user_exists(name))

    def test_unregistered_member_registers_with_admin_code(self):
        name = self.member()
        self.assertEqual(self.refused(self.reg(name)), CLOSED)                # 코드 없음
        code = self.code_for(name)
        wrong = 'ZZZZ-ZZZZ' if code != 'ZZZZ-ZZZZ' else 'YYYY-YYYY'
        self.assertEqual(self.refused(self.reg(name, code=wrong)), CLOSED)
        self.assertEqual(self.refused(self.reg(name, code=12345)), CLOSED)    # 문자열이 아닌 코드
        self.assertIsNone(self.store.password_hash(name))
        welcome = self.fresh().request(self.reg(name, code=code.lower()), 'welcome')   # 대소문자 · 하이픈은 가리지 않는다
        self.assertEqual(welcome['me']['username'], name)
        self.assertEqual(welcome['me']['email'], f'{name}@kcs.test')
        self.fresh().request({'type': 'join', 'username': f'{name}@kcs.test', 'password': 'pw-123456'}, 'welcome')

    def test_code_is_single_use_and_cleared_with_the_registration(self):
        name = self.member()
        code = self.code_for(name)
        self.fresh().request(self.reg(name, code=code), 'welcome')
        self.assertEqual(self.refused(self.reg(name, code=code)), CLOSED)
        self.assertIsNone(self.store._one('SELECT 1 FROM reset_codes WHERE username=?', (name,)))
        other = self.member()
        code = self.code_for(other)
        self.store.set_password(other, 'temp-pass')
        self.store.clear_password(other)                                       # 등록 초기화는 발급된 코드도 지운다
        self.assertEqual(self.refused(self.reg(other, code=code)), CLOSED)

    def test_every_refusal_reads_the_same(self):
        registered = self.member()
        self.store.set_password(registered, 'pw-1234')
        elsewhere = self.member(company='KCS 물류')
        admin = self.member()
        self.store.set_admin(admin, True)
        stopped = self.member()
        self.store.set_disabled(stopped, True)
        expired = self.member()
        self.code_for(expired)
        with self.store._lock:
            self.store._db.execute("UPDATE reset_codes SET expires_at='2000-01-01T00:00:00' WHERE username=?", (expired,))
            self.store._db.commit()
        for name in (registered, elsewhere, admin, stopped):
            self.assertEqual(self.refused(self.reg(name, code=self.code_for(name))), CLOSED, name)
        self.assertEqual(self.refused(self.reg(expired, code='ABCD-EFGH')), CLOSED)
        for name in (elsewhere, admin, stopped, expired):
            self.assertIsNone(self.store.password_hash(name), name)

    def test_refusals_count_as_failed_logins(self):
        name = uname('out')
        c = self.fresh()
        for _ in range(self.server.LOGIN_MAX_FAILURES['id']):
            self.assertEqual(c.expect_error(self.reg(name))['message'], CLOSED)
        self.assertIn('너무 많습니다', c.expect_error(self.reg(name))['message'])

    def test_format_checks_still_explain_themselves(self):
        name = self.member()
        err = self.fresh().expect_error({**self.reg(name), 'company': '없는 회사'})
        self.assertEqual(err['message'], '소속 회사를 선택해 주세요.')
        err = self.fresh().expect_error({**self.reg(name), 'email': 'not-an-email'})
        self.assertIn('이메일', err['message'])

    def test_reset_screen_registers_with_the_same_code(self):
        name = self.member()                                                   # 조직도에 이메일 없음 → 보낸 이메일을 저장
        email = f'{name}@kcs.test'
        base = {'type': 'password_reset', 'username': name, 'email': email, 'new_password': 'first-pass'}
        self.assertEqual(self.refused({**base, 'code': 'ABCD-EFGH'}), RESET_MISMATCH)
        code = self.code_for(name)
        c = self.fresh()
        self.assertEqual(c.request({**base, 'code': code}, 'password_reset_ok')['username'], name)
        self.assertEqual(self.refused({**base, 'code': code}), RESET_MISMATCH)   # 한 번만
        welcome = c.request({'type': 'join', 'username': email, 'password': 'first-pass'}, 'welcome')
        self.assertEqual(welcome['me']['email'], email)

    def test_reset_screen_checks_the_email(self):
        listed = self.member(email='listed@kcs.test')                          # 조직도 이메일이 있으면 같아야 한다
        base = {'type': 'password_reset', 'username': listed, 'new_password': 'first-pass'}
        self.assertEqual(self.refused({**base, 'email': 'other@kcs.test', 'code': self.code_for(listed)}),
                         RESET_MISMATCH)
        self.fresh().request({**base, 'email': 'LISTED@kcs.test', 'code': self.code_for(listed)}, 'password_reset_ok')
        blank = self.member()                                                  # 다른 사람이 쓰는 이메일은 받지 않는다
        self.assertEqual(self.refused({'type': 'password_reset', 'username': blank, 'email': 'listed@kcs.test',
                                       'new_password': 'first-pass', 'code': self.code_for(blank)}), RESET_MISMATCH)
        self.assertEqual(self.refused({'type': 'password_reset', 'username': blank, 'email': 'not-an-email',
                                       'new_password': 'first-pass', 'code': self.code_for(blank)}), RESET_MISMATCH)
        self.assertIsNone(self.store.password_hash(blank))

    def test_login_and_reset_do_not_tell_account_state(self):
        unregistered, stopped, admin = self.member(), self.member(), self.member()
        self.store.register_user(stopped, 'KCS 홀딩스', f'{stopped}@kcs.test', 'pw-1234')
        self.store.set_disabled(stopped, True)
        self.store.register_user(admin, 'KCS 홀딩스', f'{admin}@kcs.test', 'pw-1234')
        self.store.set_admin(admin, True)
        for login_id in (unregistered, uname('nobody'), 'nobody@kcs.test'):
            self.assertEqual(self.refused({'type': 'join', 'username': login_id, 'password': 'pw-1234'}),
                             LOGIN_FAILED_CLOSED)
        for name in (unregistered, stopped, admin, uname('nobody')):
            self.assertEqual(self.refused({'type': 'password_reset', 'username': name, 'email': f'{name}@kcs.test',
                                           'new_password': 'new-pass', 'code': 'ABCD-EFGH'}), RESET_MISMATCH, name)
        self.assertTrue(self.store.check_password('pw-1234', self.store.password_hash(admin)))

    def test_unknown_id_logins_are_throttled(self):
        login_id = uname('nobody')
        c = self.fresh()
        for _ in range(self.server.LOGIN_MAX_FAILURES['id']):
            self.assertEqual(c.expect_error({'type': 'join', 'username': login_id, 'password': 'pw-1234'})['message'],
                             LOGIN_FAILED_CLOSED)
        self.assertIn('너무 많습니다', c.expect_error({'type': 'join', 'username': login_id,
                                                  'password': 'pw-1234'})['message'])

    def test_admin_issues_registration_code(self):
        admin, user, newcomer = self.member(), self.member(), self.member()
        for name in (admin, user):
            self.store.set_password(name, 'pw-1234')
        self.store.set_admin(admin, True)
        a = self.fresh()
        a.request({'type': 'join', 'username': admin, 'password': 'pw-1234'}, 'welcome')
        done = a.request({'type': 'admin_reset_code', 'username': newcomer}, 'admin_done')
        self.assertEqual((done['action'], done['username'], done['registered']), ('reset_code', newcomer, False))
        self.assertRegex(done['code'], CODE_RE)
        self.assertTrue(a.request({'type': 'admin_reset_code', 'username': user}, 'admin_done')['registered'])
        self.fresh().request(self.reg(newcomer, code=done['code']), 'welcome')

    def test_initial_password_set_by_admin_needs_no_code(self):
        name = self.member(email='first@kcs.test')
        self.store.set_password(name, 'initial-pw')                            # 관리자 [비밀번호 재설정…] 과 같은 저장
        self.fresh().request({'type': 'join', 'username': 'first@kcs.test', 'password': 'initial-pw'}, 'welcome')


    def test_failed_login_text_is_the_same_for_everyone_and_points_to_registration(self):
        registered = self.member()
        self.store.set_password(registered, 'right-password')
        seen = set()
        for login_id, password in ((registered, 'wrong-password'), (self.member(), 'any-password'),
                                   (uname('nobody'), 'any-password')):
            err = self.fresh().expect_error({'type': 'join', 'username': login_id, 'password': password})
            self.server._login_failures.clear()
            self.assertEqual(err['code'], 'auth_failed')
            seen.add(err['message'])
        self.assertEqual(seen, {LOGIN_FAILED_CLOSED})
        self.assertTrue(LOGIN_FAILED_CLOSED.startswith(LOGIN_FAILED))
        self.assertIn('등록 코드', LOGIN_FAILED_CLOSED)
        self.assertNotIn('처음 사용 등록', LOGIN_FAILED_CLOSED)    # 이 말이 있으면 클라이언트가 등록 화면으로 넘긴다


class OpenRegistrationTests(RegistrationTestBase):
    def test_open_server_login_failure_has_no_registration_hint(self):
        name = self.member()
        self.store.set_password(name, 'right-password')
        err = self.fresh().expect_error({'type': 'join', 'username': name, 'password': 'wrong-password'})
        self.assertEqual((err['message'], err['code']), (LOGIN_FAILED, 'auth_failed'))

    def test_open_server_keeps_self_registration(self):
        self.assertTrue(self.server.open_registration)
        name = uname('new')
        welcome = self.fresh().request(self.reg(name), 'welcome')
        self.assertEqual(welcome['me']['company'], 'KCS 홀딩스')
        self.assertIn('처음 사용 등록', self.refused({'type': 'join', 'username': uname('nobody'), 'password': 'pw-1234'}))

    def test_reset_screen_code_registers_on_open_server_too(self):
        name = self.member()
        base = {'type': 'password_reset', 'username': name, 'email': f'{name}@kcs.test', 'new_password': 'first-pass'}
        self.assertIn('처음 사용 등록', self.refused({**base, 'code': 'ABCD-EFGH'}))
        self.fresh().request({**base, 'code': self.code_for(name)}, 'password_reset_ok')
        self.assertTrue(self.store.check_password('first-pass', self.store.password_hash(name)))
