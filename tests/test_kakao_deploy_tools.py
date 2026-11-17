# -*- coding: utf-8 -*-
"""배포 도구: 배포 전 점검(읽기 전용)과 백업 (docs/DEPLOY_CHECKLIST.md)"""

import hashlib
import io
import os
import shutil
import sqlite3
import tempfile
import unittest

from scripts import backup_db, predeploy_check
from server.kakao.store import Store


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class DeployToolBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_deploy_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, 'kakao.db')

    def store(self):
        store = Store(self.db)
        self.addCleanup(store.close)
        return store


class PredeployCheckTests(DeployToolBase):
    def prepare(self):
        store = self.store()
        store.member_create('가나다', {'company': 'KCS 홀딩스', 'account': 'gana', 'email': 'gana@kcs.test'})
        store.member_create('라마바', {'company': 'KCS 홀딩스', 'email': 'rama@kcs.test'})
        store.member_create('사아자', {'company': 'KCS 홀딩스'})
        store.member_create('차카타', {'company': 'KCS 물류'})
        for name in ('가나다', '라마바'):
            store.set_password(name, 'pw-12345678')
        store.set_admin('가나다', True)
        store.set_admin('라마바', True, 'KCS 홀딩스')
        store.update_profile('라마바', {'display_name': '별명'})
        store.replace_multi_profiles('가나다', [{'profile_id': None, 'name': 'p', 'display_name': '다른 이름',
                                              'status_message': '', 'viewers': ['라마바'], 'avatar_keep': True}])
        with store._lock:                       # 예전 서버가 남긴 상태: 겹치는 값, 삭제됐지만 관리자 표시
            store._db.execute("UPDATE users SET email='GANA@kcs.test' WHERE username='사아자'")
            store._db.execute("UPDATE users SET removed=1, disabled=1, is_admin=1 WHERE username='차카타'")
            store._db.commit()
            store._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return store

    def run_check(self, *extra):
        out = io.StringIO()
        db = predeploy_check.open_read_only(self.db)
        try:
            result = predeploy_check.collect(db)
        finally:
            db.close()
        code = predeploy_check.report(result, show_names='--names' in extra, out=out)
        return code, result, out.getvalue()

    def test_reports_what_a_restart_will_meet(self):
        self.prepare()
        code, result, text = self.run_check('--names')
        self.assertEqual(code, 1)                                              # 겹치는 로그인 아이디가 있다
        self.assertEqual(result['duplicates'], [('gana@kcs.test', [('가나다', '이메일'), ('사아자', '이메일')])])
        self.assertEqual(result['renamed'], ['라마바'])
        self.assertEqual((result['renamed_profile_count'], result['renamed_profiles']), (1, ['가나다']))
        self.assertIn('사아자', result['unregistered'])
        self.assertNotIn('가나다', result['unregistered'])
        self.assertNotIn('차카타', result['unregistered'])                      # 삭제된 구성원은 세지 않는다
        self.assertNotIn('사아자', result['unregistered_without_email'])
        self.assertEqual(result['group_admins'], ['가나다'])
        self.assertEqual(result['company_admins'], ['라마바 (KCS 홀딩스)'])
        self.assertEqual(result['removed_admins'], ['차카타'])
        for expected in ('겹치는 로그인 아이디', '가나다의 이메일', '프로필 1개', '멀티프로필 1개', '그대로 로그인'):
            self.assertIn(expected, text)

    def test_never_writes_to_the_database(self):
        store = self.prepare()
        store.close()
        before = digest(self.db)
        self.assertEqual(predeploy_check.main([self.db, '--names']), 1)
        self.assertEqual(digest(self.db), before)
        db = predeploy_check.open_read_only(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("UPDATE users SET display_name='x'")
        finally:
            db.close()

    def test_clean_database_passes(self):
        store = self.store()
        store.member_create('가나다', {'company': 'KCS 홀딩스'})
        store.set_admin('가나다', True)
        code, result, text = self.run_check()
        self.assertEqual((code, result['duplicates'], result['renamed']), (0, [], []))
        self.assertIn('✓ 없음', text)

    def test_old_database_and_bad_paths(self):
        raw = sqlite3.connect(self.db)
        raw.execute('CREATE TABLE users (username TEXT PRIMARY KEY, display_name TEXT NOT NULL, is_bot INTEGER DEFAULT 0)')
        raw.execute("INSERT INTO users VALUES ('도승현', '도승현', 0)")
        raw.commit()
        raw.close()
        code, result, text = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn('email', result['missing_columns'])
        self.assertIn('예전 버전 DB', text)
        self.assertEqual(predeploy_check.main([os.path.join(self.dir, 'nothing.db')]), 2)
        with open(os.path.join(self.dir, 'not_a_db.db'), 'w') as f:
            f.write('hello')
        self.assertEqual(predeploy_check.main([os.path.join(self.dir, 'not_a_db.db')]), 2)


class BackupTests(DeployToolBase):
    def test_backup_while_the_server_has_the_database_open(self):
        store = self.store()
        store.ensure_user('a')
        store.ensure_user('b')
        room = store.create_group('a', '방', ['b'])
        for i in range(30):
            store.add_message(room, 'a', 'text', f'메시지 {i}')                   # WAL 에만 있는 내용도
        with open(os.path.join(self.dir, 'org.json'), 'w', encoding='utf-8') as f:
            f.write('[]')
        with open(os.path.join(self.dir, 'portal.json'), 'w', encoding='utf-8') as f:
            f.write('{"token": "secret"}')
        out = os.path.join(self.dir, 'backups')
        folder = backup_db.backup(self.db, out, keep=5)
        self.assertEqual(sorted(os.listdir(folder)), ['kakao.db', 'org.json'])  # 포털 토큰 파일은 복사하지 않는다
        copy = sqlite3.connect(os.path.join(folder, 'kakao.db'))
        try:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM messages WHERE kind='text'").fetchone()[0], 30)
            self.assertEqual(copy.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        finally:
            copy.close()
        self.assertEqual(os.stat(os.path.join(folder, 'kakao.db')).st_mode & 0o777, 0o600)
        store.add_message(room, 'a', 'text', '백업 뒤에도 서버는 그대로 쓴다')

    def test_keeps_only_the_latest_folders(self):
        self.store().close()
        out = os.path.join(self.dir, 'backups')
        os.makedirs(os.path.join(out, 'notes'))                                 # 날짜 이름이 아닌 폴더는 건드리지 않는다
        for stamp in ('20261101-030000', '20261102-030000', '20261103-030000', '20261104-030000'):
            backup_db.backup(self.db, out, keep=2, stamp=stamp)
        self.assertEqual(sorted(os.listdir(out)), ['20261103-030000', '20261104-030000', 'notes'])

    def test_failure_leaves_no_half_made_folder(self):
        with open(self.db, 'w') as f:
            f.write('not a database at all')
        out = os.path.join(self.dir, 'backups')
        self.assertEqual(backup_db.main(['--db', self.db, '--out', out]), 1)
        self.assertEqual(os.listdir(out), [])
        self.assertEqual(backup_db.main(['--db', os.path.join(self.dir, 'none.db')]), 1)
        self.assertEqual(backup_db.main(['--db', self.db, '--keep', '0']), 1)

    def test_main_backs_up_the_other_databases_too(self):
        self.store().close()
        for name in backup_db.EXTRA_DBS:
            extra = sqlite3.connect(os.path.join(self.dir, name))
            extra.execute('CREATE TABLE t (x)')
            extra.commit()
            extra.close()
        self.assertEqual(backup_db.main(['--db', self.db]), 0)
        folder = os.path.join(self.dir, 'backups', os.listdir(os.path.join(self.dir, 'backups'))[0])
        self.assertEqual(sorted(os.listdir(folder)), ['kakao.db', 'portal_outbox.db', 'rag.db'])
