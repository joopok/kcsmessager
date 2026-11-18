# -*- coding: utf-8 -*-
"""친구 등록 - 전화번호 또는 이메일로 추가 (상태·화면·실제 서버)"""

import os
import shutil
import tempfile
import unittest

os.environ.setdefault('KCS_RAG_LLM', 'off')
os.environ.setdefault('KCS_LINK_PREVIEW', 'off')

from tests.kakao_fakes import make_demo_state, app

from client.kakao.state import AppState
from client.kakao.friends_tab import AddFriendDialog, FriendsTab, ProfileEditDialog, ProfileDialog


def rows(lst):
    return [lst.item(i).data(0x0100) for i in range(lst.count())]


class ContactKindTest(unittest.TestCase):
    def test_formats(self):
        k = AppState.contact_kind
        self.assertEqual(k('010-1234-5678'), 'phone')
        self.assertEqual(k('+82 10 1234 5678'), 'phone')
        self.assertEqual(k('01012345678'), 'phone')
        self.assertEqual(k('010-12'), '')
        self.assertEqual(k('name@company.com'), 'email')
        self.assertEqual(k('name@'), '')
        self.assertEqual(k('abc'), '')


class AddFriendUiTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_dialog_phone_and_email_flow(self):
        dlg = AddFriendDialog(self.state)
        self.assertEqual(dlg.mode, 'phone')
        dlg.set_query('010-12')
        self.assertFalse(dlg.add_button.isEnabled())
        self.assertFalse(dlg.submit())
        dlg.set_query('010-1234-5678')
        self.assertTrue(dlg.add_button.isEnabled())
        self.assertTrue(dlg.submit())
        self.assertEqual(self.conn.last('friend_add'), {'query': '010-1234-5678'})
        self.conn.push('friend_added', user=dict(self.state.user('최지우'), company='케이씨티'), already=False)
        self.assertFalse(dlg.card.isHidden())
        self.assertEqual(dlg.card_name.text(), '최지우')
        self.assertIn('추가했어요', dlg.message.text())
        dlg.set_mode('email')
        dlg.set_query('choi@kct.co.kr')
        self.assertTrue(dlg.submit())
        self.assertEqual(self.conn.last('friend_add'), {'query': 'choi@kct.co.kr'})
        self.conn.push('error', req_type='friend_add', message='해당 전화번호·이메일로 가입한 사용자가 없습니다.')
        self.assertIn('가입한 사용자가 없습니다', dlg.message.text())
        dlg._chat()
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '최지우'})

    def test_section_header_text_is_vertically_centered(self):
        """'온라인 0', '오프라인 54' 같은 묶음 머리글 글자가 줄(강조 영역) 가운데에 온다 - 아래로 처지지 않게"""
        tab = FriendsTab(self.state, self.state.settings)
        tab.resize(420, 700)
        tab.show()
        headers = [tab.list.itemWidget(tab.list.item(i)) for i in range(tab.list.count())
                   if (tab.list.item(i).data(0x0100) or ('',))[0] == 'section']
        self.assertTrue(headers)
        for h in headers:
            h.layout().activate()
            above = h.title.geometry().top()
            below = h.height() - h.title.geometry().bottom() - 1
            self.assertLessEqual(abs(above - below), 1, h.title.text())
        tab.hide()

    def test_registered_section_and_header_button(self):
        tab = FriendsTab(self.state, self.state.settings)
        self.assertIsNotNone(tab.add_friend_button)
        self.assertIsInstance(tab.show_add_friend(exec_=False), AddFriendDialog)
        # 머리글의 사람+ 아이콘을 실제로 누르면 친구 추가 창이 떠야 한다 (clicked 의 checked 인자 회귀)
        from unittest import mock
        with mock.patch.object(AddFriendDialog, 'exec_', return_value=0) as shown:
            tab.add_friend_button.click()
        shown.assert_called_once()
        self.assertNotIn(('section', 'registered'), rows(tab.list))
        self.conn.push('friend_list', friends=['박민수', '최지우'])
        self.assertIn(('section', 'registered'), rows(tab.list))
        self.assertEqual(tab.section_members('registered'), ['박민수', '최지우'])
        menu = tab.context_menu_for(('friend', '박민수', 'registered'))
        self.assertIn('친구 삭제', [a.text() for a in menu.actions()])
        [a for a in menu.actions() if a.text() == '친구 삭제'][0].trigger()
        self.assertEqual(self.conn.last('friend_remove'), {'username': '박민수'})

    def test_profile_edit_contacts_and_profile_button(self):
        dlg = ProfileEditDialog(self.state)
        dlg.mobile_edit.setText('010-5555-6666')
        dlg.email_edit.setText('hong@kcins.co.kr')
        dlg.accept()
        sent = self.conn.last('profile_update')
        self.assertEqual((sent['mobile'], sent['email']), ('010-5555-6666', 'hong@kcins.co.kr'))
        bad = ProfileEditDialog(self.state)
        bad.email_edit.setText('not-email')
        self.conn.sent.clear()
        bad.accept()
        self.assertIsNone(self.conn.last('profile_update'))
        self.assertIn('이메일', bad.error_label.text())
        self.conn.push('user_updated', user=dict(self.state.user('최지우'), mobile='010-7777-8888'))
        prof = ProfileDialog(self.state, '최지우')
        self.assertEqual(prof.friend_button.text(), '친구 추가')
        prof.toggle_friend()
        self.assertEqual(self.conn.last('friend_add'), {'username': '최지우'})   # 연락처를 몰라도 보고 있는 동료를 바로


class AddFriendE2ETest(unittest.TestCase):
    """실제 서버: 상대가 프로필에 등록한 전화번호/이메일로 찾아 친구 추가"""

    def test_real_server(self):
        from tests.test_kakao_e2e import pump
        from client.kakao.net import ChatConnection
        from client.kakao.settings import LocalSettings
        from server.kakao.server import KakaoServer
        app()
        d = tempfile.mkdtemp()
        srv = KakaoServer(port=0, db_path=os.path.join(d, 'k.db'))
        srv.serve_in_thread()
        clients = []

        def login(name):
            conn = ChatConnection()
            st = AppState(conn, LocalSettings(name, base_dir=os.path.join(d, 's')))
            conn.connect_to('127.0.0.1', srv.port, name)
            self.assertTrue(pump(lambda: st.me == name))
            clients.append(conn)
            return st
        try:
            hong, outsider = login('홍길동'), login('외부파트너')
            outsider.update_profile(mobile='010-4321-8765', email='partner@kct.co.kr')
            self.assertTrue(pump(lambda: hong.user('외부파트너').get('mobile') == '010-4321-8765'))
            self.assertEqual(hong.user('외부파트너').get('email'), '')          # 다른 사람의 이메일은 오지 않는다
            # 본인에게는 온다 (두 연결의 도착 순서는 정해져 있지 않으므로 본인 쪽도 기다린다)
            self.assertTrue(pump(lambda: outsider.user('외부파트너').get('email') == 'partner@kct.co.kr'))
            added = []
            hong.friend_added.connect(lambda u, already: added.append((u['username'], already)))
            self.assertIsNone(hong.add_friend('010 4321 8765'))
            self.assertTrue(pump(lambda: added))
            self.assertEqual(added[0], ('외부파트너', False))
            self.assertTrue(pump(lambda: hong.is_registered_friend('외부파트너')))
            self.assertIsNone(hong.add_friend('PARTNER@kct.co.kr'))
            self.assertTrue(pump(lambda: len(added) == 2))
            self.assertEqual(added[1], ('외부파트너', True))
            errors = []
            hong.error.connect(errors.append)
            hong.add_friend('010-0000-1111')
            self.assertTrue(pump(lambda: errors))
            self.assertIn('가입한 사용자가 없습니다', errors[0])
        finally:
            for c in clients:
                c.close()
            srv.stop()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
