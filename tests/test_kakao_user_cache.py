# -*- coding: utf-8 -*-
"""사용자 목록 캐시 · 접속 알림 묶기 (명세 §2 · §5, 2026-11-18)

- welcome 의 users 는 캐시(store.user_directory + 사람별 JSON 조각)로 만든다. 예전 방식(_users_for)과 바이트까지 같아야 한다.
- 사용자 정보가 바뀌는 모든 길(프로필 · 관리자 수정 · 관리자 지정 · 삭제 · 멀티프로필 · 접속 · presence)에서 틀리지 않아야 한다.
- 접속 · 접속 종료 알림은 짧게 모아 보내고, 사람마다 user_status_changed → user_updated 순서와 마지막 상태가 맞아야 한다.
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from server.kakao.server import KakaoServer
from server.kakao.store import Store
from tests.test_kakao_server import ServerTestBase, FakeRag


class DirectoryVersionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_dir_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = Store(os.path.join(self.dir, 'kakao.db'))
        self.addCleanup(self.store.close)
        self.store.member_create('가나다', {'company': 'KCS 홀딩스', 'email': 'gana@kcs.test'})
        self.store.member_create('라마바', {'company': 'KCS 홀딩스', 'email': 'rama@kcs.test'})

    def version(self):
        return self.store.user_directory()[0]

    def same_as_list_users(self):
        _version, entries = self.store.user_directory()
        online = {'가나다'}
        expected = self.store.list_users(online)
        got = [on if (on['username'] in online or on['is_bot']) else off for on, off in entries]
        self.assertEqual(got, expected)

    def test_every_visible_change_moves_the_version(self):
        changes = [
            lambda: self.store.update_profile('가나다', {'status_message': '회의 중'}),
            lambda: self.store.update_profile('가나다', {'presence': 'busy'}),
            lambda: self.store.member_update('라마바', {'department': '영업', 'title': '과장'}),
            lambda: self.store.set_admin('라마바', True),
            lambda: self.store.member_create('사아자', {'company': 'KCS 물류'}),
            lambda: self.store.member_set_removed('사아자', True),
            lambda: self.store.set_public_key('가나다', 'key-1'),
        ]
        for change in changes:
            before = self.version()
            first = self.store.user_directory()
            change()
            self.assertNotEqual(self.version(), before)
            self.assertIsNot(self.store.user_directory(), first)      # 다시 만든다
            self.same_as_list_users()

    def test_invisible_changes_keep_the_cache(self):
        cached = self.store.user_directory()
        self.store.take_briefing_slot('가나다', '2026-11-18')         # 목록에 안 보이는 칸
        self.store.set_password('가나다', 'pw-12345678')
        self.store.issue_login_token('가나다')
        self.store.set_disabled('라마바', True)                        # 사용 중지는 목록 모양을 바꾸지 않는다
        self.assertIs(self.store.user_directory(), cached)
        self.same_as_list_users()

    def test_a_direct_database_edit_is_seen_too(self):
        """관리자 도구 · 예전 서버처럼 이 코드 밖에서 바꿔도 DB 트리거가 버전을 올린다"""
        self.store.user_directory()
        with self.store._lock:
            self.store._db.execute("UPDATE users SET display_name='바뀐 이름' WHERE username='라마바'")
            self.store._db.commit()
        names = {on['username']: on['display_name'] for on, _off in self.store.user_directory()[1]}
        self.assertEqual(names['라마바'], '바뀐 이름')


class WelcomeUsersCacheTests(ServerTestBase):
    def payloads_match(self, viewer, online):
        text, names = self.server._users_json_for(viewer, online)
        expected = self.server._users_for(viewer, online)
        self.assertEqual(text, json.dumps(expected, ensure_ascii=False))          # 바이트까지 같다
        self.assertEqual(names, [u['username'] for u in expected])

    def test_cached_users_equal_the_plain_way_for_every_viewer(self):
        store = self.server.store
        for name in ('캐시가', '캐시나', '캐시다'):
            if not store.user_exists(name):
                store.member_create(name, {'company': 'KCS 홀딩스', 'email': f'{name}@kcs.test'})
        store.set_admin('캐시가', True)
        store.replace_multi_profiles('캐시나', [{'profile_id': None, 'name': 'p', 'display_name': '업무용',
                                               'status_message': '업무 중', 'viewers': ['캐시다'], 'avatar_keep': True}])
        scenarios = [set(), {'캐시가'}, {'캐시가', '캐시나', '캐시다'}]
        for online in scenarios:
            for viewer in ('캐시가', '캐시나', '캐시다'):
                self.payloads_match(viewer, online)
        store.update_profile('캐시다', {'status_message': '새 상태', 'presence': 'away'})
        store.member_update('캐시나', {'title': '차장'})
        store.set_admin('캐시다', True)
        store.set_admin('캐시가', False)
        for online in scenarios:
            for viewer in ('캐시가', '캐시나', '캐시다'):
                self.payloads_match(viewer, online)

    def test_welcome_carries_the_same_users(self):
        a = self.connect()
        b = self.connect()
        expected = self.server._users_for(b.username, self.server._online_users())
        got = {u['username']: u for u in b.welcome['users']}
        for user in expected:
            if user['username'] in (a.username, b.username):
                continue
            self.assertEqual(got[user['username']], user)
        self.assertTrue(got[a.username]['online'])                             # 먼저 들어온 사람은 접속 중으로
        names = b.wait_for('all_users_list')['users']
        self.assertEqual(names, [u['username'] for u in b.welcome['users']])
        a.send({'type': 'profile_update', 'status_message': '캐시 갱신 확인'})
        a.wait_for('user_updated', lambda m: m['user']['username'] == a.username)
        c = self.connect()                                                     # 바뀐 뒤 들어온 사람에게는 새 값
        self.assertEqual({u['username']: u for u in c.welcome['users']}[a.username]['status_message'], '캐시 갱신 확인')


class PresenceBatchTests(ServerTestBase):
    def presence_frames(self, client, username, wait=1.0):
        time.sleep(wait)
        with client.cond:
            return [(m['type'], m.get('status') or m.get('user', {}).get('presence'))
                    for m in client.buffer
                    if (m.get('type') == 'user_status_changed' and m.get('username') == username)
                    or (m.get('type') == 'user_updated' and m.get('user', {}).get('username') == username)]

    def test_login_and_logout_are_announced_in_order(self):
        watcher = self.connect()
        a = self.connect()
        frames = self.presence_frames(watcher, a.username)
        self.assertEqual(frames, [('user_status_changed', 'online'), ('user_updated', 'online')])
        self.assertEqual(self.presence_frames(a, a.username, wait=0), [])      # 본인에게는 보내지 않는다
        with watcher.cond:
            watcher.buffer.clear()
        a.close()
        frames = self.presence_frames(watcher, a.username)
        self.assertEqual(frames, [('user_status_changed', 'offline'), ('user_updated', 'offline')])

    def test_quick_flaps_end_in_the_last_state(self):
        watcher = self.connect()
        name = 'presence_flap'
        first = self.connect(name)
        first.close()
        second = self.connect(name)                                            # 0.15초 안에 나갔다가 다시 들어옴
        frames = self.presence_frames(watcher, name)
        self.assertEqual(frames[-2:], [('user_status_changed', 'online'), ('user_updated', 'online')])
        self.assertLessEqual(len(frames), 4)
        second.close()

    def test_many_logins_send_one_write_per_watcher_per_batch(self):
        watcher = self.connect()
        clients = [self.connect() for _ in range(8)]
        time.sleep(1.0)
        with watcher.cond:
            online = {m['username'] for m in watcher.buffer
                      if m.get('type') == 'user_status_changed' and m['status'] == 'online'}
        self.assertTrue({c.username for c in clients} <= online)


if __name__ == '__main__':
    unittest.main()
