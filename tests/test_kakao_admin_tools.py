# -*- coding: utf-8 -*-
"""관리자 도구와 가동 중인 서버의 DB (명세 §4.2, 2026-11-17): 도구는 이미 있는 DB 의 구조 · 시드를 건드리지 않는다"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from scripts import set_admin, set_password
from server.kakao.store import Store


def quiet(func, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        return func(*args), out.getvalue()


class ToolStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_tools_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, 'kakao.db')

    def write_org(self, users, admins=()):
        with open(os.path.join(self.dir, 'org.json'), 'w', encoding='utf-8') as f:
            json.dump({'version': 2, 'holding': {'name': '한국지주'}, 'companies': [{'name': '케이씨에스'}],
                       'include_demo_users': False, 'admins': list(admins), 'users': users}, f, ensure_ascii=False)

    def test_existing_db_is_opened_without_schema_or_seed(self):
        self.write_org([{'username': '도승현', 'company': '케이씨에스', 'department': '개발팀'}], admins=['도승현'])
        Store(self.db).close()                                                  # 서버가 한 번 시작해 만든 DB
        self.write_org([{'username': '도승현', 'company': '케이씨에스', 'department': '바뀐팀'},
                        {'username': '새사람', 'company': '케이씨에스'}])
        store = Store.open_for_tools(self.db)
        try:
            self.assertEqual(store.get_user('도승현')['department'], '개발팀')    # 시드를 다시 쓰지 않았다
            self.assertFalse(store.user_exists('새사람'))
            self.assertIn('케이씨에스', store.company_names())                    # 회사 목록은 읽는다
        finally:
            store.close()
        server_start = Store(self.db)                                           # 서버가 다시 시작하면 그때 반영
        try:
            self.assertEqual(server_start.get_user('도승현')['department'], '바뀐팀')
            self.assertTrue(server_start.user_exists('새사람'))
        finally:
            server_start.close()

    def test_tool_does_not_change_the_structure(self):
        self.write_org([{'username': '도승현', 'company': '케이씨에스'}])
        Store(self.db).close()
        raw = sqlite3.connect(self.db)
        raw.execute('DROP INDEX IF EXISTS idx_attachments_uploader')            # 예전 서버가 만든 DB 라고 치고
        raw.commit()
        raw.close()
        self.assertEqual(quiet(set_password.main, ['도승현', '--db', self.db, '--password', 'new-password'])[0], 0)
        raw = sqlite3.connect(self.db)
        try:
            self.assertIsNone(raw.execute("SELECT 1 FROM sqlite_master WHERE name='idx_attachments_uploader'").fetchone())
        finally:
            raw.close()

    def test_missing_db_is_created(self):
        self.write_org([{'username': '도승현', 'company': '케이씨에스', 'account': 'doshyun'}])
        self.assertEqual(quiet(set_admin.main, ['doshyun', '--db', self.db])[0], 0)
        store = Store.open_for_tools(self.db)
        try:
            self.assertTrue(store.is_admin('도승현'))
        finally:
            store.close()

    def test_old_db_asks_for_a_server_restart(self):
        raw = sqlite3.connect(self.db)
        raw.execute('CREATE TABLE users (username TEXT PRIMARY KEY, display_name TEXT NOT NULL)')
        raw.execute("INSERT INTO users VALUES ('도승현', '도승현')")
        raw.commit()
        raw.close()
        for tool, args in ((set_password.main, ['도승현', '--db', self.db, '--password', 'new-password']),
                           (set_admin.main, ['도승현', '--db', self.db])):
            code, text = quiet(tool, args)
            self.assertEqual(code, 1)
            self.assertIn('다시 시작', text)

    def test_set_password_follows_the_server_length_rule(self):
        self.write_org([{'username': '도승현', 'company': '케이씨에스'}])
        Store(self.db).close()
        code, text = quiet(set_password.main, ['도승현', '--db', self.db, '--password', 'seven77'])
        self.assertEqual(code, 1)
        self.assertIn('8~64자', text)
        self.assertEqual(quiet(set_password.main, ['도승현', '--db', self.db, '--password', 'eight888'])[0], 0)
        self.assertEqual(quiet(set_password.main, ['도승현', '--db', self.db, '--password', 'abcd', '--allow-short'])[0], 0)
        self.assertEqual(quiet(set_password.main, ['도승현', '--db', self.db, '--password', 'abc', '--allow-short'])[0], 1)
