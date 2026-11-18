# -*- coding: utf-8 -*-
"""관리자 권한 (명세 §4.2): 계정 목록 · 비밀번호 재설정 · 등록 초기화 · 사용 중지 · 관리자 지정 · 전체 공지"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_server import ServerTestBase, Client, uname

DISABLED = '사용이 중지된 계정입니다. 관리자에게 문의해 주세요.'


class AdminServerTests(ServerTestBase):
    def member(self, password='pw-1234', admin=False):
        """비밀번호를 등록한 사용자 하나 → (client, username)"""
        name = uname('adm')
        store = self.server.store
        store.register_user(name, 'KCS 홀딩스', f'{name}@kcs.test', password, employee_no=f'E{name}'[:20])
        if admin:
            store.set_admin(name, True)
        c = Client(self.port, name, join=False)
        self.clients.append(c)
        c.request({'type': 'join', 'username': name, 'password': password}, 'welcome')
        return c, name

    def test_non_admin_is_refused_everything(self):
        c, name = self.member()
        _admin, target = self.member()
        for payload in ({'type': 'admin_users'}, {'type': 'admin_notice', 'content': '공지'},
                        {'type': 'admin_set_disabled', 'username': target, 'disabled': True},
                        {'type': 'admin_set_admin', 'username': name, 'is_admin': True},
                        {'type': 'admin_reset_password', 'username': target, 'new_password': 'hack'},
                        {'type': 'admin_clear_password', 'username': target}):
            err = c.expect_error(payload)
            self.assertEqual((err['req_type'], err['message']), (payload['type'], '관리자만 사용할 수 있습니다.'))
        self.assertFalse(self.server.store.is_disabled(target))

    def test_user_list_and_is_admin_flag(self):
        admin, name = self.member(admin=True)
        _c, other = self.member()
        users = admin.request({'type': 'admin_users'}, 'admin_users')['users']
        by_name = {u['username']: u for u in users}
        self.assertTrue(by_name[name]['is_admin'] and by_name[name]['online'] and by_name[name]['registered'])
        self.assertEqual(by_name[other]['employee_no'], f'E{other}'[:20])
        self.assertNotIn('AI 비서', by_name)                                   # 봇 제외
        self.assertNotIn('password_hash', json.dumps(users))
        self.assertTrue(self.server.store.get_user(name)['is_admin'])         # User 객체에도

    def test_reset_and_clear_password(self):
        admin, _name = self.member(admin=True)
        _c, target = self.member('old-pass')
        store = self.server.store
        token = store.issue_login_token(target)
        done = admin.request({'type': 'admin_reset_password', 'username': target, 'new_password': 'new-pass'},
                             'admin_done')
        self.assertEqual(done, {**done, 'action': 'reset_password', 'username': target})
        self.assertTrue(store.check_password('new-pass', store.password_hash(target)))
        self.assertFalse(store.check_login_token(target, token))              # 자동 로그인 해제
        self.assertIn('4~64자', admin.expect_error({'type': 'admin_reset_password', 'username': target,
                                                      'new_password': '1'})['message'])
        admin.request({'type': 'admin_clear_password', 'username': target}, 'admin_done')
        self.assertIsNone(store.password_hash(target))                         # 처음 사용 등록부터 다시
        self.assertTrue(store.get_user(target)['employee_no'])                 # 사번은 남긴다
        for bad, message in (({'username': '없는사람'}, '사용자를 찾을 수 없습니다.'),
                             ({'username': 'AI 비서'}, '봇 계정은 관리할 수 없습니다.')):
            err = admin.expect_error({'type': 'admin_clear_password', **bad})
            self.assertEqual(err['message'], message)

    def test_disable_kicks_and_blocks_login(self):
        admin, admin_name = self.member(admin=True)
        target_conn, target = self.member('tgt-pass')
        store = self.server.store
        token = store.issue_login_token(target)
        done = admin.request({'type': 'admin_set_disabled', 'username': target, 'disabled': True}, 'admin_done')
        self.assertEqual((done['action'], done['disabled']), ('set_disabled', True))
        self.assertEqual(target_conn.wait_for('account_disabled')['message'], DISABLED)
        self.assertFalse(store.check_login_token(target, token))
        again = Client(self.port, target, join=False)
        self.clients.append(again)
        self.assertEqual(again.expect_error({'type': 'join', 'username': target, 'password': 'tgt-pass'})['message'],
                         DISABLED)
        # 틀린 비밀번호에는 중지 여부를 드러내지 않는다 (계정 유무 감추기)
        self.assertEqual(again.expect_error({'type': 'join', 'username': target, 'password': 'wrong-pw'})['message'],
                         '아이디 또는 비밀번호가 올바르지 않습니다.')
        self.assertIn('중지', again.expect_error({'type': 'password_reset', 'username': target, 'code': 'ABCD-EFGH',
                                                  'email': f'{target}@kcs.test', 'new_password': 'x-pass'})['message'])
        self.assertIn('자기 계정', admin.expect_error({'type': 'admin_set_disabled', 'username': admin_name,
                                                       'disabled': True})['message'])
        admin.request({'type': 'admin_set_disabled', 'username': target, 'disabled': False}, 'admin_done')
        again.request({'type': 'join', 'username': target, 'password': 'tgt-pass'}, 'welcome')

    def test_set_admin_and_last_admin_guard(self):
        store = self.server.store
        with store._lock:                                      # 이 서버의 관리자를 비우고 시작
            store._db.execute('UPDATE users SET is_admin=0')
            store._db.commit()
        admin, admin_name = self.member(admin=True)
        watcher, other = self.member()
        done = admin.request({'type': 'admin_set_admin', 'username': other, 'is_admin': True}, 'admin_done')
        self.assertTrue(done['is_admin'])
        updated = watcher.wait_for('user_updated', lambda m: m['user']['username'] == other and m['user']['is_admin'])
        self.assertTrue(updated['user']['is_admin'])
        admin.request({'type': 'admin_set_admin', 'username': other, 'is_admin': False}, 'admin_done')
        err = admin.expect_error({'type': 'admin_set_admin', 'username': admin_name, 'is_admin': False})
        self.assertEqual(err['message'], '관리자가 한 명은 있어야 합니다.')
        self.assertTrue(store.is_admin(admin_name))

    def test_notice_reaches_everyone_online(self):
        admin, admin_name = self.member(admin=True)
        listener, _n = self.member()
        done = admin.request({'type': 'admin_notice', 'content': '  오늘 18시 서버 점검  '}, 'admin_done')
        self.assertGreaterEqual(done['delivered'], 2)
        got = listener.wait_for('admin_notice')
        self.assertEqual((got['sender'], got['content']), (admin_name, '오늘 18시 서버 점검'))
        admin.wait_for('admin_notice')                                         # 보낸 사람도 받는다
        self.assertIn('1000자', admin.expect_error({'type': 'admin_notice', 'content': 'x' * 1001})['message'])
        admin.expect_error({'type': 'admin_notice', 'content': '   '})


class AdminSeedAndToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_admin_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        with open(os.path.join(self.dir, 'org.json'), 'w', encoding='utf-8') as f:
            json.dump({'version': 2, 'holding': {'name': '한국지주'}, 'companies': [{'name': '케이씨에스'}],
                       'include_demo_users': False, 'admins': ['KCS관리자'],
                       'users': [{'username': 'KCS관리자', 'company': '케이씨에스', 'account': 'kcs_administrator'},
                                 {'username': '도승현', 'company': '케이씨에스', 'account': 'doshyun'}]},
                      f, ensure_ascii=False)
        self.db = os.path.join(self.dir, 'kakao.db')

    def test_org_admins_seeded(self):
        from server.kakao.store import Store
        store = Store(self.db)
        try:
            self.assertTrue(store.is_admin('KCS관리자'))
            self.assertFalse(store.is_admin('도승현'))
        finally:
            store.close()

    def test_set_admin_tool(self):
        from scripts import set_admin
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(set_admin.main(['doshyun', '--db', self.db]), 0)
            self.assertEqual(set_admin.main(['--list', '--db', self.db]), 0)
        self.assertIn('도승현: 관리자 지정했습니다 (그룹 전체).', out.getvalue())
        self.assertIn('KCS관리자', out.getvalue())
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(set_admin.main(['도승현', '--off', '--db', self.db]), 0)
            self.assertEqual(set_admin.main(['KCS관리자', '--off', '--db', self.db]), 1)   # 마지막 관리자
            self.assertEqual(set_admin.main(['없는사람', '--db', self.db]), 1)
        self.assertIn('관리자가 한 명은 있어야 합니다.', err.getvalue())


class AdminClientTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()
        self.users = [
            {'username': '홍길동', 'display_name': '홍길동', 'company': 'KCS', 'department': '개발팀', 'title': '',
             'account': 'hong', 'employee_no': 'E1', 'email': 'hong@kcs.test', 'registered': True,
             'disabled': False, 'is_admin': True, 'online': True},
            {'username': '김철수', 'display_name': '김철수', 'company': 'KCS', 'department': '영업팀', 'title': '대리',
             'account': 'kim', 'employee_no': 'E2', 'email': 'kim@kcs.test', 'registered': True,
             'disabled': False, 'is_admin': False, 'online': False},
            {'username': '이영희', 'display_name': '이영희', 'company': 'KCS', 'department': '', 'title': '',
             'account': '', 'employee_no': '', 'email': '', 'registered': False,
             'disabled': True, 'is_admin': False, 'online': False}]

    def make_admin(self, on=True):
        self.conn.push('user_updated', user={**self.state.user(self.state.me), 'is_admin': on})

    def test_state_actions_and_events(self):
        s, c = self.state, self.conn
        s.admin_reset_password('김철수', 'new-pass')
        self.assertEqual(c.last('admin_reset_password'), {'username': '김철수', 'new_password': 'new-pass'})
        s.admin_set_disabled('김철수', 1)
        self.assertEqual(c.last('admin_set_disabled'), {'username': '김철수', 'disabled': True})
        s.admin_set_admin('김철수', False)
        self.assertEqual(c.last('admin_set_admin'), {'username': '김철수', 'is_admin': False})
        s.admin_send_notice('점검')
        self.assertEqual(c.last('admin_notice'), {'content': '점검'})
        got = []
        s.account_disabled.connect(got.append)
        c.push('account_disabled', message=DISABLED)
        self.assertEqual(got, [DISABLED])
        self.assertFalse(s.is_admin())
        self.make_admin()
        self.assertTrue(s.is_admin())

    def test_more_tab_row_only_for_admin(self):
        from client.kakao.more_tab import MoreTab
        tab = MoreTab(self.state, self.state.settings)
        tab.show()
        self.addCleanup(tab.close)
        self.assertTrue(tab.admin_row.isHidden())
        self.make_admin()
        self.assertFalse(tab.admin_row.isHidden())
        dlg = tab.open_admin(exec_=False)
        self.addCleanup(dlg.close)
        self.assertEqual(self.conn.last('admin_users'), {})
        self.make_admin(False)
        self.assertTrue(tab.admin_row.isHidden())

    def test_admin_dialog_list_search_and_actions(self):
        from client.kakao.admin import AdminDialog, status_text
        dlg = AdminDialog(self.state)
        self.addCleanup(dlg.close)
        self.conn.push('admin_users', users=self.users)
        self.assertEqual(dlg.table.rowCount(), 3)
        self.assertIn('전체 3명 · 등록 2명', dlg.count_label.text())
        self.assertEqual(status_text(self.users[0]), '그룹 관리자 · 사용 중 · 접속 중')
        self.assertEqual(status_text(self.users[2]), '사용 중지')
        dlg.search.setText('영업')
        self.assertEqual([u['username'] for u in dlg.visible_users()], ['김철수'])
        dlg.search.setText('')
        self.assertFalse(dlg.reset_button.isEnabled())                       # 선택 전
        self.assertTrue(dlg.select('김철수'))
        self.assertEqual(dlg.disable_button.text(), '사용 중지')
        self.assertEqual(dlg.admin_button.text(), '관리자 지정')
        with mock.patch.object(dlg, 'confirm', return_value=False):
            dlg.toggle_disabled()
        self.assertIsNone(self.conn.last('admin_set_disabled'))               # 취소하면 보내지 않는다
        with mock.patch.object(dlg, 'confirm', return_value=True), \
                mock.patch.object(dlg, 'ask_admin_scope', return_value=''):
            dlg.toggle_disabled()
            dlg.toggle_admin()
            dlg.clear_registration()
        self.assertEqual(self.conn.last('admin_set_disabled'), {'username': '김철수', 'disabled': True})
        self.assertEqual(self.conn.last('admin_set_admin'), {'username': '김철수', 'is_admin': True})
        self.assertEqual(self.conn.last('admin_clear_password'), {'username': '김철수'})
        with mock.patch('client.kakao.admin.QInputDialog.getText', side_effect=[('abcd1234', True), ('abcd1234', True)]):
            dlg.reset_password()
        self.assertEqual(self.conn.last('admin_reset_password'), {'username': '김철수', 'new_password': 'abcd1234'})
        with mock.patch('client.kakao.admin.QInputDialog.getText', side_effect=[('abcd1234', True), ('other', True)]):
            dlg.reset_password()
        self.assertIn('다릅니다', dlg.status.text())
        dlg.select('이영희')
        self.assertEqual(dlg.disable_button.text(), '사용 중지 해제')
        self.assertFalse(dlg.clear_button.isEnabled())                       # 미등록은 초기화할 것이 없다
        dlg.select('홍길동')                                                   # 나 자신
        self.assertFalse(dlg.disable_button.isEnabled())
        self.conn.sent.clear()
        self.conn.push('admin_done', action='set_disabled', username='김철수', disabled=True)
        self.assertIn('김철수 님을 사용 중지했습니다', dlg.status.text())
        self.assertEqual(self.conn.last('admin_users'), {})                  # 결과 뒤 목록 다시 받기
        dlg.show()
        self.conn.push('error', req_type='admin_set_admin', message='관리자가 한 명은 있어야 합니다.')
        self.assertIn('한 명은', dlg.status.text())

    def test_notice_dialog_and_receiving(self):
        from client.kakao.admin import AdminDialog
        dlg = AdminDialog(self.state)
        self.addCleanup(dlg.close)
        notice = dlg.send_notice(exec_=False)
        self.assertFalse(notice.send_button.isEnabled())
        notice.edit.setPlainText('  내일 휴무입니다  ')
        self.assertTrue(notice.send_button.isEnabled())
        self.assertEqual(notice.content(), '내일 휴무입니다')
        notice.edit.setPlainText('x' * 1001)
        self.assertFalse(notice.send_button.isEnabled())
        from client.kakao.main_window import MainWindow
        win = MainWindow(self.state, self.state.settings)
        self.addCleanup(win.close)
        self.conn.push('admin_notice', sender='KCS관리자', content='18시 <b>서버</b> 점검', timestamp='2026-10-09T17:00:00')
        box = win.admin_notice_box
        self.assertEqual(box.text(), '관리자 공지')                          # macOS 는 창 제목을 숨긴다 → 본문 첫 줄
        self.assertIn('18시 &lt;b&gt;서버&lt;/b&gt; 점검', box.informativeText())   # 공지 글자는 서식으로 해석하지 않는다
        self.assertIn('KCS관리자', box.informativeText())
        self.assertFalse(box.isModal())
        box.close()


if __name__ == '__main__':
    unittest.main()
