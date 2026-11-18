# -*- coding: utf-8 -*-
"""로그인 아이디 규칙 (명세 §4.1 · §4.2 · §4.3, 2026-11-17)
- 이름 · 사번 · 사내 계정 · 이메일은 서로 겹치지 않는다
- 등록이 닫힌 서버에서 이메일은 관리자 관리 항목
- 삭제한 관리자는 관리자 권한이 없어지고, org.json admins 는 처음 한 번만 올린다"""

import json
import os
import shutil
import tempfile
import unittest

from tests.test_kakao_server import ServerTestBase, Client, uname

from server.kakao.server import EMAIL_ADMIN_ONLY
from server.kakao.store import Store


class AccountTestBase(ServerTestBase):
    def setUp(self):
        super().setUp()
        self.store = self.server.store
        self.server._login_failures.clear()

    def fresh(self):
        c = Client(self.port, 'x', join=False)
        self.clients.append(c)
        return c

    def member(self, password='pw-1234', company='KCS 홀딩스', **fields):
        name = uname('acc')
        self.store.member_create(name, {'company': company, **fields})
        if password:
            self.store.set_password(name, password)
        return name

    def login(self, login_id, password='pw-1234'):
        c = self.fresh()
        c.welcome = c.request({'type': 'join', 'username': login_id, 'password': password}, 'welcome')
        c.username = c.welcome['me']['username']
        return c


class UniqueLoginIdTests(AccountTestBase):
    def test_new_name_cannot_equal_another_login_id(self):
        account = uname('id').lower()
        owner = self.member(account=account, email=f'{account}@kcs.test')
        for taken in (account, account.upper()):
            err = self.fresh().expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': taken,
                                             'email': f'x{taken}@kcs.test', 'password': 'pw-1234'})
            self.assertEqual(err['message'], Store.REGISTER_NAME_CONFLICT)
            self.assertFalse(self.store.user_exists(taken))
        self.assertEqual(self.login(account).username, owner)                  # 주인의 로그인은 그대로

    def test_register_email_and_employee_no_cannot_equal_another_login_id(self):
        account = uname('id').lower()
        self.member(account=account, email=f'{account}@kcs.test')
        name = uname('new')
        base = {'type': 'register', 'company': 'KCS 홀딩스', 'name': name, 'password': 'pw-1234'}
        err = self.fresh().expect_error({**base, 'email': f'{account}@KCS.test'})
        self.assertEqual(err['message'], Store.REGISTER_TAKEN_EMAIL)
        self.server._login_failures.clear()
        err = self.fresh().expect_error({**base, 'email': f'{name}@kcs.test', 'employee_no': account})
        self.assertEqual(err['message'], Store.REGISTER_TAKEN_EMPLOYEE_NO)

    def test_profile_email_must_be_free(self):
        owner = self.member(email=f'{uname("own")}@kcs.test')
        owner_email = self.store.get_user(owner)['email']
        other = self.login(self.member(email=f'{uname("oth")}@kcs.test'))
        before = other.welcome['me']['email']
        err = other.expect_error({'type': 'profile_update', 'email': owner_email.upper(), 'status_message': '바쁨'})
        self.assertEqual(err['message'], Store.EMAIL_TAKEN)
        mine = self.store.get_user(other.username)
        self.assertEqual((mine['email'], mine['status_message']), (before, ''))  # 아무것도 바뀌지 않는다
        self.assertEqual(self.login(owner_email).username, owner)
        free = f'{uname("free")}@kcs.test'
        other.send({'type': 'profile_update', 'email': free})
        other.wait_for('user_updated', lambda m: m['user']['username'] == other.username and m['user']['email'] == free)

    def test_member_ids_cannot_cross_fields(self):
        account = uname('id').lower()
        owner = self.member(account=account, email=f'{account}@kcs.test')
        with self.assertRaisesRegex(ValueError, '같은 이름은 쓸 수 없습니다'):
            self.store.member_create(account.upper(), {'company': 'KCS 홀딩스'})
        other = self.member()
        for fields, label in (({'employee_no': account}, '사번'), ({'account': owner}, '사내 계정'),
                              ({'email': f'{account}@kcs.test'}, '이메일')):
            with self.assertRaisesRegex(ValueError, f'이미 다른 구성원이 쓰는 {label}'):
                self.store.member_update(other, fields)
        self.store.member_update(owner, {'account': account, 'email': f'{account}@kcs.test'})   # 자기 값은 그대로 된다

    def test_welcome_tells_policy(self):
        c = self.login(self.member())
        self.assertEqual(c.welcome['policy'], {'open_registration': True, 'email_editable': True,
                                               'display_name_editable': True})
        self.assertTrue(self.fresh().request({'type': 'org_companies'}, 'org_companies')['open_registration'])


class ClosedServerEmailTests(AccountTestBase):
    SERVER_KWARGS = {'require_password': True}

    def test_email_is_changed_by_admin_only(self):
        email = f'{uname("me")}@kcs.test'
        c = self.login(self.member(email=email))
        self.assertEqual(c.welcome['policy'], {'open_registration': False, 'email_editable': False,
                                               'display_name_editable': False})
        self.assertFalse(self.fresh().request({'type': 'org_companies'}, 'org_companies')['open_registration'])
        self.assertEqual(c.expect_error({'type': 'profile_update', 'email': 'else@kcs.test'})['message'],
                         EMAIL_ADMIN_ONLY)
        # 다른 항목은 저장하고 이메일만 그대로 둔다 (지금 클라이언트는 프로필 저장 때 함께 보낸다)
        err = c.expect_error({'type': 'profile_update', 'email': '', 'status_message': '회의 중'})
        self.assertEqual(err['message'], EMAIL_ADMIN_ONLY)
        mine = self.store.get_user(c.username)
        self.assertEqual((mine['email'], mine['status_message']), (email, '회의 중'))
        # 지금 값을 그대로 보내는 것은 오류가 아니다
        c.send({'type': 'profile_update', 'email': email, 'status_message': '자리 비움'})
        c.wait_for('user_updated', lambda m: m['user']['status_message'] == '자리 비움')
        c.assert_no('error')
        self.store.member_update(c.username, {'email': 'fixed@kcs.test'})       # 관리자 수정은 된다
        self.assertEqual(self.login('fixed@kcs.test').username, c.username)


class RemovedAdminTests(AccountTestBase):
    def admin(self, company=''):
        name = self.member()
        self.store.set_admin(name, True, company)
        return name

    def test_removed_admin_comes_back_as_plain_member(self):
        self.admin()                                                           # 그룹 관리자가 하나는 남게
        gone = self.admin()
        self.store.issue_reset_code(self.member(), '관리자')
        self.store.member_set_removed(gone, True)
        self.assertFalse(self.store.is_admin(gone))
        self.store.member_set_removed(gone, False)
        self.assertIsNone(self.store.admin_scope(gone))
        self.assertFalse(self.login(gone).welcome['me']['is_admin'])

    def test_company_admin_cannot_touch_removed_admin_account(self):
        target = self.admin()
        with self.store._lock:                                                 # 예전 서버가 남긴 행: 삭제됐지만 관리자 표시
            self.store._db.execute('UPDATE users SET removed=1, disabled=1 WHERE username=?', (target,))
            self.store._db.commit()
        company_admin = self.login(self.admin('KCS 홀딩스'))
        for payload in ({'type': 'admin_reset_password', 'username': target, 'new_password': 'other-pw'},
                        {'type': 'admin_member_restore', 'username': target},
                        {'type': 'admin_clear_password', 'username': target}):
            err = company_admin.expect_error(payload)
            self.assertEqual(err['message'], '관리자 계정은 그룹 관리자만 관리할 수 있습니다.', payload['type'])
        self.assertTrue(self.store.check_password('pw-1234', self.store.password_hash(target)))
        self.assertTrue(self.store.is_removed(target))


class SeedAdminOnceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_seed_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        with open(os.path.join(self.dir, 'org.json'), 'w', encoding='utf-8') as f:
            json.dump({'version': 2, 'holding': {'name': '한국지주'}, 'companies': [{'name': '케이씨에스'}],
                       'include_demo_users': False, 'admins': ['시드관리자'],
                       'users': [{'username': '시드관리자', 'company': '케이씨에스'},
                                 {'username': '새관리자', 'company': '케이씨에스'}]}, f, ensure_ascii=False)
        self.db = os.path.join(self.dir, 'kakao.db')

    def reopen(self, action=None):
        store = Store(self.db)
        try:
            if action:
                action(store)
            return store.is_admin('시드관리자'), store.is_admin('새관리자')
        finally:
            store.close()

    def test_seed_admin_is_promoted_once(self):
        self.assertEqual(self.reopen(), (True, False))

        def hand_over(store):
            store.set_admin('새관리자', True)
            store.set_admin('시드관리자', False)
        self.assertEqual(self.reopen(hand_over), (False, True))
        self.assertEqual(self.reopen(), (False, True))                         # 다시 켜도 되돌아오지 않는다

    def test_removed_seed_admin_stays_removed(self):
        def remove(store):
            store.set_admin('새관리자', True)
            store.member_set_removed('시드관리자', True)
        self.assertEqual(self.reopen(remove), (False, True))
        self.assertEqual(self.reopen(), (False, True))

    def test_seed_admin_returns_when_no_group_admin_is_left(self):
        def lose_all(store):
            with store._lock:
                store._db.execute('UPDATE users SET is_admin=0')
                store._db.commit()
        self.assertEqual(self.reopen(lose_all), (False, False))
        self.assertEqual(self.reopen(), (True, False))                         # 복구
