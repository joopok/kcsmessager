# -*- coding: utf-8 -*-
"""프로필 편집: 맨 위 사번 · 대직자(명세 §8.13) · 모든 팝업은 메신저 창 가운데"""

import unittest
from unittest import mock

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QMainWindow, QMessageBox

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_server import ServerTestBase

from client.kakao import main_window
from client.kakao.deputies import (DeputyEditor, DeputyPickerDialog, deputy_summary, deputy_title,
                                   find_candidates)
from client.kakao.friends_tab import ProfileDialog, ProfileEditDialog
from client.kakao.widgets import center_on_messenger


def org(state, conn, name, title='', path=(), company='케이씨에스'):
    conn.push('user_updated', user=dict(state.user(name), title=title, org_path=list(path), company=company,
                                        department=path[0] if path else ''))


class ProfileEditTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()               # 나 = 홍길동
        org(self.state, self.conn, '김철수', '책임', ['기술연구소1실', 'AI/AX 솔루션사업팀'])
        org(self.state, self.conn, '김민지', '선임', ['기술연구소1실', '1팀(Tandem)'])
        org(self.state, self.conn, '이영희', '수석', ['기술연구소1실', 'AI/AX 솔루션사업팀'])
        org(self.state, self.conn, '박민수', '책임', ['영업1부'])

    # ---- 사번
    def test_employee_no_shown_at_top(self):
        self.conn.push('user_updated', user=dict(self.state.user('홍길동'), employee_no='K2019001'))
        dlg = ProfileEditDialog(self.state)
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.employee_no_label.text(), 'K2019001')
        body = dlg.employee_card.parentWidget()                                 # 본문(스크롤 영역 안)의 맨 위
        self.assertIs(body.layout().itemAt(0).widget(), dlg.employee_card)
        self.assertNotIn('employee_no', dlg.values())                           # 보기만 (보내지 않음)

    def test_employee_no_missing(self):
        dlg = ProfileEditDialog(self.state)
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.employee_no_label.text(), '등록된 사번 없음')

    # ---- 대직자
    def test_candidates_search_name_team_title(self):
        self.assertEqual(find_candidates(self.state, '김'), ['김민지', '김철수'])
        self.assertEqual(find_candidates(self.state, 'ai/ax'), ['김철수', '이영희'])
        self.assertEqual(find_candidates(self.state, '영업1부'), ['박민수'])
        self.assertNotIn('홍길동', find_candidates(self.state, ''))             # 나는 빼고
        self.assertEqual(deputy_title(self.state, '김철수'), '김철수 책임')

    def test_single_match_added_directly(self):
        ed = DeputyEditor(self.state)
        ed.search_edit.setText('박민수')
        ed.search()
        self.assertEqual(ed.deputies(), ['박민수'])
        self.assertEqual(ed.search_edit.text(), '')
        self.assertEqual([c.label.text() for c in ed.flow.items], ['박민수 책임'])

    def test_multiple_matches_open_checklist(self):
        ed = DeputyEditor(self.state, ['이영희'])
        dlg = ed.search('ai/ax', exec_=False)                                  # 김철수 · 이영희
        self.assertIsInstance(dlg, DeputyPickerDialog)
        self.assertEqual(dlg.list.count(), 2)
        self.assertFalse(dlg.add_button.isEnabled())                            # 고르기 전에는 추가 못 함
        disabled = [dlg.list.item(i) for i in range(2) if dlg.list.item(i).data(Qt.UserRole) == '이영희'][0]
        self.assertFalse(disabled.flags() & Qt.ItemIsUserCheckable)             # 이미 대직자
        dlg.set_checked('김철수')
        self.assertEqual(dlg.selected(), ['김철수'])
        self.assertTrue(dlg.add_button.isEnabled())
        self.assertEqual(dlg.count_label.text(), '1명 선택')
        with mock.patch.object(DeputyPickerDialog, 'exec_', return_value=QDialog.Accepted), \
                mock.patch.object(DeputyPickerDialog, 'selected', return_value=['김철수', '김민지']):
            ed.search('김')
        self.assertEqual(ed.deputies(), ['이영희', '김철수', '김민지'])

    def test_row_click_toggles_and_filter(self):
        dlg = DeputyPickerDialog(self.state, find_candidates(self.state, ''))
        self.addCleanup(dlg.close)
        item = dlg.list.item(0)
        dlg._toggle(item)
        self.assertEqual(item.checkState(), Qt.Checked)
        dlg._remember(item)
        item.setCheckState(Qt.Unchecked)                                        # 네모를 직접 누른 경우
        dlg._toggle(item)
        self.assertEqual(item.checkState(), Qt.Unchecked)                       # 두 번 뒤집지 않는다
        dlg.filter_edit.setText('영업')
        visible = [dlg.list.item(i).data(Qt.UserRole) for i in range(dlg.list.count())
                   if not dlg.list.item(i).isHidden()]
        self.assertEqual(visible, ['박민수'])

    def test_empty_search_lists_everyone_and_no_match_message(self):
        ed = DeputyEditor(self.state)
        dlg = ed.search('', exec_=False)
        self.assertEqual(dlg.list.count(), len(find_candidates(self.state, '')))
        self.assertIsNone(ed.search('없는사람'))
        self.assertIn('찾은 사람이 없어요', ed.message.text())

    def test_remove_chip_and_limit(self):
        ed = DeputyEditor(self.state, ['김철수', '이영희'])
        ed.flow.items[0].remove_button.click()
        self.assertEqual(ed.deputies(), ['이영희'])
        with mock.patch('client.kakao.deputies.MAX_DEPUTIES', 2):
            ed.add(['김철수', '박민수'])
        self.assertEqual(ed.deputies(), ['이영희', '김철수'])
        self.assertIn('2명까지', ed.message.text())

    def test_save_sends_deputies_only_when_changed(self):
        dlg = ProfileEditDialog(self.state)
        self.addCleanup(dlg.close)
        self.assertNotIn('deputies', dlg.values())
        dlg.deputy_editor.add(['김철수', '이영희'])
        dlg.accept()
        self.assertEqual(self.conn.last('profile_update')['deputies'], ['김철수', '이영희'])

    def test_profile_view_shows_deputies(self):
        self.conn.push('user_updated', user=dict(self.state.user('박민수'), deputies=['김철수', '이영희']))
        profile = ProfileDialog(self.state, '박민수')
        self.addCleanup(profile.close)
        self.assertEqual(profile.contact_labels['대직자'].text(), '김철수 책임 외 1명')
        self.assertEqual(deputy_summary(self.state, ['김철수']), '김철수 책임')


class DeputyServerTests(ServerTestBase):
    def test_deputies_saved_and_validated(self):
        a, _ = self.register()
        b, _ = self.register()
        c, _ = self.register()
        user = a.request({'type': 'profile_update', 'deputies': [b.username, c.username, b.username]},
                         'user_updated', lambda m: m['user']['username'] == a.username)['user']
        self.assertEqual(user['deputies'], [b.username, c.username])           # 중복 제거 · 순서 유지
        self.assertEqual(self.server.store.get_user(a.username)['deputies'], [b.username, c.username])
        err = a.expect_error({'type': 'profile_update', 'deputies': [a.username]})
        self.assertIn('대직자로 지정할 수 없는', err['message'])               # 나 자신
        err = a.expect_error({'type': 'profile_update', 'deputies': ['없는사람xyz']})
        self.assertIn('대직자로 지정할 수 없는', err['message'])
        bots = self.server.store.bot_usernames()
        if bots:
            err = a.expect_error({'type': 'profile_update', 'deputies': [bots[0]]})
            self.assertIn('대직자로 지정할 수 없는', err['message'])           # 봇
        err = a.expect_error({'type': 'profile_update', 'deputies': 'nope'})
        self.assertIn('목록', err['message'])
        err = a.expect_error({'type': 'profile_update', 'deputies': [f'x{i}' for i in range(11)]})
        self.assertIn('10명까지', err['message'])
        user = a.request({'type': 'profile_update', 'deputies': []}, 'user_updated',
                         lambda m: m['user']['username'] == a.username)['user']
        self.assertEqual(user['deputies'], [])                                  # 해제


class CenterPopupTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, _ = make_demo_state()
        main_window.apply_app_theme(self.state.settings)
        self.main = QMainWindow()
        self.main.setGeometry(app().primaryScreen().availableGeometry())   # 화면 안 (밖으로 나가면 화면 안쪽으로 붙인다)
        self.main.show()
        self.addCleanup(self.main.close)

    def assert_centered(self, dlg, host):
        a, b = dlg.frameGeometry().center(), host.frameGeometry().center()
        self.assertLessEqual(abs(a.x() - b.x()), 2)
        self.assertLessEqual(abs(a.y() - b.y()), 2)

    def test_dialog_with_parent_opens_centered(self):
        dlg = QMessageBox(QMessageBox.Information, '안내', '가운데에 떠요', QMessageBox.Ok, self.main)
        dlg.show()
        self.addCleanup(dlg.close)
        app().processEvents()
        self.assert_centered(dlg, self.main)

    def test_dialog_without_parent_uses_messenger_window(self):
        dlg = ProfileEditDialog(self.state)
        dlg.move(0, 0)
        dlg.show()
        self.addCleanup(dlg.close)
        app().processEvents()
        self.assert_centered(dlg, self.main)

    def test_clamped_inside_screen(self):
        screen = app().primaryScreen().availableGeometry()
        self.main.move(screen.right() - 100, screen.top())                    # 메신저 창이 화면 오른쪽 끝에 걸침
        dlg = QDialog(self.main)
        dlg.resize(300, 200)
        dlg.show()
        self.addCleanup(dlg.close)
        app().processEvents()
        self.assertLessEqual(dlg.frameGeometry().right(), screen.right())       # 팝업은 화면 안에

    def test_keep_position_and_no_host(self):
        dlg = QDialog()
        dlg.setProperty('keepPosition', True)
        self.assertFalse(center_on_messenger(dlg))
        self.main.hide()
        self.assertFalse(center_on_messenger(QDialog()))                         # 로그인 전 등 기준 창 없음


if __name__ == '__main__':
    unittest.main()
