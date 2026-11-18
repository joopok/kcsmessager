# -*- coding: utf-8 -*-
"""생년월일(YYYY-MM-DD) - 서버 검증, 시드 보강, 프로필 편집·카드, 생일인 친구·달력 (명세 §3 User.birthday)"""

import os
import tempfile
import unittest
from datetime import date, timedelta

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_server import ServerTestBase

from client.kakao.friends_tab import (ProfileDialog, ProfileEditDialog, birthday_missing_year, birthday_text,
                                     normalize_birthday, parse_birthday)
from client.kakao.state import birthday_md


class BirthDateServerTests(ServerTestBase):
    def test_birthday_profile_update_accepts_birth_date(self):
        a, b = self.connect(), self.connect()
        a.send({'type': 'profile_update', 'birthday': '1990-03-15'})
        u = b.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                       and m['user']['birthday'] == '1990-03-15')['user']
        self.assertEqual(u['birthday'], '1990-03-15')
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for bad in ('1990-02-29', '1899-12-31', tomorrow, '90-03-15', '1990-3-15'):
            a.expect_error({'type': 'profile_update', 'birthday': bad})
        a.send({'type': 'profile_update', 'birthday': '03-15'})               # 연도 없이도 된다 (예전 형식)
        b.wait_for('user_updated', lambda m: m['user']['username'] == a.username and m['user']['birthday'] == '03-15')


class BirthDateSeedTests(unittest.TestCase):
    def test_birthday_seed_fills_year_only_for_old_month_day(self):
        from server.kakao.store import Store
        path = os.path.join(tempfile.mkdtemp(), 't.db')
        store = Store(path)
        self.assertEqual(store.get_user('홍길동')['birthday'], '1982-01-15')  # 새 DB 는 생년월일로 시드
        with store._lock:
            store._db.execute("UPDATE users SET birthday='01-15' WHERE username='홍길동'")      # 예전 DB 값
            store._db.execute("UPDATE users SET birthday='1999-07-07' WHERE username='김철수'")  # 사용자가 고친 값
            store._db.commit()
        store.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store.get_user('홍길동')['birthday'], '1982-01-15')
        self.assertEqual(store.get_user('김철수')['birthday'], '1999-07-07')


class BirthDateClientTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_birthday_helpers(self):
        self.assertEqual(birthday_md('1990-03-15'), '03-15')
        self.assertEqual(birthday_md('03-15'), '03-15')
        self.assertIsNone(birthday_md(None))
        self.assertEqual(parse_birthday('1990-03-15'), date(1990, 3, 15))
        self.assertIsNone(parse_birthday('1990-02-29'))
        self.assertIsNone(parse_birthday((date.today() + timedelta(days=1)).isoformat()))
        self.assertEqual(birthday_text('1990-03-15'), '생년월일 1990년 3월 15일')
        self.assertEqual(birthday_text('03-15'), '생일 3월 15일')

    def test_birthday_profile_view_shows_birth_date(self):
        self.state.users['최지우']['birthday'] = '1983-12-25'
        dlg = ProfileDialog(self.state, '최지우')
        self.assertEqual(dlg.birthday_label.text(), '생년월일 1983년 12월 25일')
        self.state.users['최지우']['birthday'] = '12-25'
        dlg2 = ProfileDialog(self.state, '최지우')
        self.assertEqual(dlg2.birthday_label.text(), '생일 12월 25일')

    def test_birthday_profile_edit_sends_birth_date(self):
        dlg = ProfileEditDialog(self.state)
        self.assertEqual(dlg.birthday_edit.placeholderText(), 'YYYY-MM-DD (예: 1990-03-15)')
        dlg.birthday_edit.setText('1990-02-30')
        dlg.accept()
        self.assertIsNone(self.conn.last('profile_update'))
        self.assertIn('YYYY-MM-DD', dlg.error_label.text())
        dlg.birthday_edit.setText('1990-03-15')
        dlg.accept()
        self.assertEqual(self.conn.last('profile_update')['birthday'], '1990-03-15')

    def test_birthday_profile_edit_asks_year_when_missing(self):
        """예전에 월·일만 저장된 생일(01-04)이면 연도가 빠졌다고 안내하고, 연도 없이는 저장하지 않는다"""
        self.assertTrue(birthday_missing_year('01-04'))
        self.assertFalse(birthday_missing_year('1980-01-04'))
        self.assertFalse(birthday_missing_year(''))
        self.assertFalse(birthday_missing_year('13-40'))
        self.state.users[self.state.me]['birthday'] = '01-04'
        dlg = ProfileEditDialog(self.state)
        self.assertEqual(dlg.birthday_edit.text(), '01-04')
        self.assertTrue(dlg.birthday_hint.isVisibleTo(dlg))
        self.assertIn('연도', dlg.birthday_hint.text())
        dlg.accept()
        self.assertIsNone(self.conn.last('profile_update'))
        self.assertIn('연도까지', dlg.error_label.text())
        dlg.birthday_edit.setText('1980-01-04')
        self.assertFalse(dlg.birthday_hint.isVisibleTo(dlg))
        dlg.accept()
        self.assertEqual(self.conn.last('profile_update')['birthday'], '1980-01-04')
        self.state.users[self.state.me]['birthday'] = '1980-01-04'
        dlg2 = ProfileEditDialog(self.state)
        self.assertFalse(dlg2.birthday_hint.isVisibleTo(dlg2))
        self.state.users[self.state.me]['birthday'] = None        # 생일을 비우는 것은 된다
        dlg3 = ProfileEditDialog(self.state)
        dlg3.accept()
        self.assertIsNone(self.conn.last('profile_update')['birthday'])

    def test_birthday_type_year_in_front(self):
        """01-04 앞에 커서가 있어 연도만 치면(198001-04) 1980-01-04 로 저장된다"""
        from PyQt5.QtCore import QCoreApplication
        from PyQt5.QtTest import QTest
        self.assertEqual(normalize_birthday('19800104'), '1980-01-04')
        self.assertEqual(normalize_birthday('1980/1/4'), '1980-01-04')
        self.assertEqual(normalize_birthday('1980104'), '1980104')          # 짐작하지 않는다
        self.state.users[self.state.me]['birthday'] = '01-04'
        dlg = ProfileEditDialog(self.state)
        self.assertIn('1990-01-04', dlg.birthday_hint.text())
        dlg.show()
        QCoreApplication.processEvents()
        dlg._caret_before_birthday()
        self.assertFalse(dlg.birthday_edit.hasSelectedText())
        self.assertEqual(dlg.birthday_edit.cursorPosition(), 0)
        QTest.keyClicks(dlg.birthday_edit, '1980')
        self.assertEqual(dlg.birthday_edit.text(), '198001-04')
        dlg.accept()
        self.assertEqual(self.conn.last('profile_update')['birthday'], '1980-01-04')

    def test_birthday_friends_match_birth_date(self):
        today = date.today()
        self.state.users['최지우']['birthday'] = f'1983-{today:%m-%d}'
        self.assertIn('최지우', [u['username'] for u in self.state.birthday_friends()])


if __name__ == '__main__':
    unittest.main()
