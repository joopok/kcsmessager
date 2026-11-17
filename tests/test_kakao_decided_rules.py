# -*- coding: utf-8 -*-
"""2026-11-17 결정 사항 (명세 §4.5): 나중에 들어온 사람이 보는 범위, 표시 이름은 실명으로 고정,
링크 미리보기는 공인 인터넷 주소만"""

import base64
import unittest

from tests.test_kakao_server import ServerTestBase, Client, uname

from server.kakao.rag import RagService
from server.kakao.server import NAME_FIXED, preview_host_allowed
from server.kakao.store import BOT_USERNAME


def b64(data):
    return base64.b64encode(data).decode('ascii')


class LateJoinerTests(ServerTestBase):
    def setUp(self):
        super().setUp()
        self.a, self.b, self.c = self.connect(), self.connect(), self.connect()
        self.rid = self.group(self.a, [self.b])['room_id']
        self.old = self.send_text(self.a, self.rid, '들어오기 전 이야기')
        self.a.send({'type': 'send', 'room_id': self.rid, 'kind': 'file', 'content': '예전 파일',
                     'attachment': {'name': 'old.txt', 'mime': 'text/plain', 'data_b64': b64(b'old')}})
        self.old_file = self.b.wait_for('msg_new', lambda m: m['message']['content'] == '예전 파일')['message']
        self.a.send({'type': 'poll_create', 'room_id': self.rid, 'question': '예전 투표', 'options': ['가', '나'],
                     'multiple': False})
        self.old_poll = self.b.wait_for('msg_new', lambda m: m['message']['kind'] == 'poll')['message']
        self.a.send({'type': 'room_invite', 'room_id': self.rid, 'usernames': [self.c.username]})
        self.c.wait_for('room_updated', lambda m: m['room']['room_id'] == self.rid)
        self.c.wait_for('msg_new', lambda m: m['message']['kind'] == 'system')
        self.new = self.send_text(self.a, self.rid, '들어온 뒤 이야기')
        self.c.wait_for('msg_new', lambda m: m['message']['msg_id'] == self.new['msg_id'])

    def contents(self, client, request='history', key='messages', **extra):
        reply = {'history': 'history', 'search': 'search_result', 'drawer_query': 'drawer_result'}[request]
        return [m['content'] for m in client.request({'type': request, 'room_id': self.rid, **extra}, reply)[key]]

    def test_history_starts_at_the_invitation(self):
        seen = self.contents(self.c)
        self.assertIn('들어온 뒤 이야기', seen)
        for hidden in ('들어오기 전 이야기', '예전 파일', '예전 투표'):
            self.assertNotIn(hidden, seen)
        self.assertIn('들어오기 전 이야기', self.contents(self.b))              # 처음부터 있던 사람은 그대로

    def test_search_drawer_export_and_room_list(self):
        self.assertEqual(self.contents(self.c, 'search', query='들어오기 전'), [])
        self.assertEqual(self.contents(self.b, 'search', query='들어오기 전'), ['들어오기 전 이야기'])
        self.assertEqual(self.c.request({'type': 'drawer_query', 'kind': 'file'}, 'drawer_result')['messages'], [])
        exported = self.c.request({'type': 'room_export', 'room_id': self.rid}, 'room_export')['messages']
        self.assertNotIn('들어오기 전 이야기', [m['content'] for m in exported])
        again = self.connect(self.c.username)
        room = [r for r in again.welcome['rooms'] if r['room_id'] == self.rid][0]
        self.assertEqual(room['last_message']['msg_id'], self.new['msg_id'])

    def test_requests_pointing_at_earlier_messages(self):
        c, old_id = self.c, self.old['msg_id']
        for payload in ({'type': 'react', 'msg_id': old_id, 'emoji': '👍'},
                        {'type': 'thread_get', 'root_id': old_id},
                        {'type': 'notice_set', 'room_id': self.rid, 'msg_id': old_id},
                        {'type': 'send', 'room_id': self.rid, 'kind': 'text', 'content': '답', 'reply_to_id': old_id},
                        {'type': 'send', 'room_id': self.rid, 'kind': 'text', 'content': '답', 'thread_root_id': old_id},
                        {'type': 'poll_vote', 'poll_id': self.old_poll['poll']['poll_id'], 'option_indexes': [0]},
                        {'type': 'attachment_get', 'attachment_id': self.old_file['attachment']['attachment_id']}):
            c.expect_error(payload)
        self.b.request({'type': 'attachment_get', 'attachment_id': self.old_file['attachment']['attachment_id']},
                       'attachment_data')

    def test_changes_to_earlier_messages_are_not_pushed(self):
        self.c.clear()
        self.b.send({'type': 'react', 'msg_id': self.old['msg_id'], 'emoji': '👍'})
        self.a.wait_for('msg_updated', lambda m: m['message']['msg_id'] == self.old['msg_id'])
        self.c.assert_no('msg_updated')
        self.b.send({'type': 'react', 'msg_id': self.new['msg_id'], 'emoji': '👍'})
        self.c.wait_for('msg_updated', lambda m: m['message']['msg_id'] == self.new['msg_id'])

    def test_thread_that_continues_shows_its_root(self):
        reply = self.send_text(self.a, self.rid, '이어진 답글', thread_root_id=self.old['msg_id'])
        self.c.wait_for('msg_new', lambda m: m['message']['msg_id'] == reply['msg_id'])
        thread = self.c.request({'type': 'thread_get', 'root_id': self.old['msg_id']}, 'thread')
        self.assertEqual(thread['root']['msg_id'], self.old['msg_id'])
        self.assertEqual([m['content'] for m in thread['messages']], ['이어진 답글'])

    def test_leaving_and_being_invited_again_starts_over(self):
        self.c.send({'type': 'room_leave', 'room_id': self.rid})
        self.c.wait_for('room_removed')
        later = self.send_text(self.a, self.rid, '없는 동안 한 이야기')
        self.a.send({'type': 'room_invite', 'room_id': self.rid, 'usernames': [self.c.username]})
        self.c.wait_for('room_updated', lambda m: m['room']['room_id'] == self.rid)
        seen = self.contents(self.c)
        self.assertNotIn(later['content'], seen)
        self.assertNotIn('들어온 뒤 이야기', seen)

    def test_ai_assistant_ignores_what_i_could_not_see(self):
        self.c.send({'type': 'room_open_direct', 'target': BOT_USERNAME})
        bot_room = self.c.wait_for('room_updated', lambda m: BOT_USERNAME in m['room']['members'])['room']['room_id']
        self.send_text(self.c, bot_room, '질문')
        self.c.wait_for('msg_new', lambda m: m['message']['sender'] == BOT_USERNAME)
        scope = self.server.store.room_since_for_user(self.c.username)
        self.assertGreaterEqual(scope[self.rid], self.old_poll['msg_id'])
        self.assertEqual(self.server.store.room_since_for_user(self.b.username)[self.rid], 0)


class RagScopeTests(unittest.TestCase):
    def test_room_scope_can_start_at_a_message(self):
        rag = RagService(':memory:')
        self.addCleanup(rag.close)
        for msg_id, text in ((1, '회의실 예약은 총무팀에 요청합니다'), (5, '회의실 예약 양식이 바뀌었습니다')):
            rag.index_message({'msg_id': msg_id, 'room_id': 'r', 'sender': 'a', 'kind': 'text', 'content': text,
                               'timestamp': '2026-11-17T10:00:00', 'mentions': []})
        whole = {c['source_id'] for c in rag.search('회의실 예약', 'c', ['r'])}
        late = {c['source_id'] for c in rag.search('회의실 예약', 'c', {'r': 3})}
        self.assertEqual((whole, late), ({1, 5}, {5}))
        self.assertEqual({c['source_id'] for c in rag.search('회의실 예약', 'c', {'r': 0})}, {1, 5})


class FixedNameTests(ServerTestBase):
    SERVER_KWARGS = {'require_password': True}

    def login(self):
        name = uname('nm')
        store = self.server.store
        store.member_create(name, {'company': 'KCS 홀딩스'})
        store.set_password(name, 'pw-12345678')
        c = Client(self.port, name, join=False)
        self.clients.append(c)
        c.welcome = c.request({'type': 'join', 'username': name, 'password': 'pw-12345678'}, 'welcome')
        return c

    def test_display_name_stays_the_real_name(self):
        c = self.login()
        self.assertFalse(c.welcome['policy']['display_name_editable'])
        err = c.expect_error({'type': 'profile_update', 'display_name': '대표이사', 'status_message': '회의 중'})
        self.assertEqual((err['message'], err['code']), (NAME_FIXED, 'name_fixed'))
        mine = self.server.store.get_user(c.username)
        self.assertEqual((mine['display_name'], mine['status_message']), (c.username, '회의 중'))
        # 지금 클라이언트는 프로필을 저장할 때 이름을 늘 함께 보낸다: 실명 그대로면 오류가 아니다
        c.send({'type': 'profile_update', 'display_name': c.username, 'status_message': '자리 비움'})
        c.wait_for('user_updated', lambda m: m['user']['status_message'] == '자리 비움')
        c.send({'type': 'profile_update', 'display_name': c.username})
        c.wait_for('user_updated', lambda m: m['user']['username'] == c.username)
        c.assert_no('error')

    def test_multi_profile_keeps_the_real_name(self):
        c, viewer = self.login(), self.login()
        saved = c.request({'type': 'multi_profile_set', 'profiles': [
            {'name': '업무용', 'display_name': '대표이사', 'status_message': '업무 중', 'viewers': [viewer.username]}]},
            'multi_profile_list')['profiles']
        self.assertEqual((saved[0]['display_name'], saved[0]['status_message']), (c.username, '업무 중'))
        again = Client(self.port, viewer.username, join=False)
        self.clients.append(again)
        welcome = again.request({'type': 'join', 'username': viewer.username, 'password': 'pw-12345678'}, 'welcome')
        shown = [u for u in welcome['users'] if u['username'] == c.username][0]
        self.assertEqual((shown['display_name'], shown['status_message']), (c.username, '업무 중'))

    def test_earlier_changes_are_put_back(self):
        store = self.server.store
        name, other = uname('nm'), uname('nm')
        for user in (name, other):
            store.member_create(user, {'company': 'KCS 홀딩스'})
        store.update_profile(name, {'display_name': '다른 이름'})
        store.replace_multi_profiles(name, [{'profile_id': None, 'name': 'p', 'display_name': '또 다른 이름',
                                             'status_message': '', 'viewers': [other], 'avatar_keep': True}])
        bot_name = store.display_name(BOT_USERNAME)
        self.assertGreaterEqual(store.fix_display_names(), 2)
        self.assertEqual(store.display_name(name), name)
        self.assertEqual(store.list_multi_profiles(name)[0]['display_name'], name)
        self.assertEqual(store.display_name(BOT_USERNAME), bot_name)
        self.assertEqual(store.fix_display_names(), 0)


class OpenServerNameTests(ServerTestBase):
    def test_test_servers_can_still_rename(self):
        c = self.connect()
        self.assertTrue(c.welcome['policy']['display_name_editable'])
        c.send({'type': 'profile_update', 'display_name': '별명'})
        c.wait_for('user_updated', lambda m: m['user']['display_name'] == '별명')


class PreviewPublicOnlyTests(unittest.TestCase):
    def test_only_public_addresses(self):
        for url in ('http://10.1.2.3/groupware', 'http://192.168.0.5/', 'http://172.16.0.1/', 'http://172.31.255.1/',
                    'http://100.64.0.1/', 'http://127.0.0.1/', 'http://169.254.169.254/', 'http://[fd00::1]/',
                    'http://[::1]/', 'http://0.0.0.0/'):
            self.assertFalse(preview_host_allowed(url), url)
        self.assertTrue(preview_host_allowed('http://8.8.8.8/'))
        self.assertTrue(preview_host_allowed('https://1.1.1.1/page'))
