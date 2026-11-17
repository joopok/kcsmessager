# -*- coding: utf-8 -*-
"""AppState 단위 테스트 - 가짜 연결로 서버 푸시/요청 흐름 검증"""

import os
import tempfile
import unittest
from datetime import datetime, date, timedelta

from tests.kakao_fakes import make_demo_state, app


class AppStateTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def collect(self, signal):
        got = []
        signal.connect(lambda *a: got.append(a))
        return got

    def test_welcome_populates(self):
        s = self.state
        self.assertEqual(s.me, '홍길동')
        self.assertEqual(len(s.users), 5)
        self.assertEqual(s.room_title('r1'), '프로젝트 팀')
        self.assertEqual(s.room_title('r2'), '박민수')
        self.assertEqual(s.room_title('r3'), '홍길동')
        self.assertEqual([f['username'] for f in s.birthday_friends()], ['김철수'])
        # welcome 직후 일정/할 일 요청을 보냈어야 함 (make_demo_state가 sent를 비우므로 재현)
        self.conn.sent.clear()
        self.conn.push('welcome', me=s.user('홍길동'), users=list(s.users.values()), rooms=list(s.rooms.values()))
        self.assertIsNotNone(self.conn.last('event_list'))
        self.assertIsNotNone(self.conn.last('todo_list'))

    def test_message_unread_counts(self):
        s = self.state
        # read_pointers: 홍길동 8, 김철수 11, 이영희 9
        self.assertEqual(s.message_unread('r1', s.find_message('r1', 11)), 2)
        self.assertEqual(s.message_unread('r1', s.find_message('r1', 9)), 1)
        self.assertEqual(s.message_unread('r1', s.find_message('r1', 5)), 0)
        self.assertEqual(s.message_unread('r1', s.find_message('r1', 1)), 0)  # system
        got = self.collect(s.read_changed)
        self.conn.push('read_update', room_id='r1', username='이영희', last_read_msg_id=11)
        self.assertEqual(s.message_unread('r1', s.find_message('r1', 11)), 1)
        self.conn.push('read_update', room_id='r1', username='이영희', last_read_msg_id=3)  # 감소 불가
        self.assertEqual(s.room('r1')['read_pointers']['이영희'], 11)
        self.assertEqual(len(got), 2)

    def test_my_read_resets_room_unread(self):
        s = self.state
        self.assertEqual(s.total_unread(), 3)
        s.mark_read('r1')
        self.assertEqual(self.conn.last('read'), {'room_id': 'r1', 'last_msg_id': 11})
        self.conn.push('read_update', room_id='r1', username='홍길동', last_read_msg_id=11)
        self.assertEqual(s.room_unread('r1'), 0)
        self.assertEqual(s.total_unread(), 1)

    def test_msg_new_updates_room_and_notifies(self):
        s = self.state
        notes = self.collect(s.notify)
        added = self.collect(s.message_added)
        m = dict(msg_id=20, room_id='r2', sender='박민수', kind='text', content='도착!', timestamp=datetime.now().isoformat(),
                 reply_to=None, mentions=[], reactions={}, deleted=False, attachment=None, event_id=None, poll=None)
        self.conn.push('msg_new', message=m)
        self.assertEqual(s.room('r2')['last_message']['content'], '도착!')
        self.assertEqual(s.room_unread('r2'), 2)
        self.assertEqual(len(added), 1)
        self.assertEqual(notes[0][0], '박민수')
        # 창이 열려 있거나 알림 끔이면 알림 없음
        s.active_rooms.add('r2')
        self.conn.push('msg_new', message=dict(m, msg_id=21))
        s.active_rooms.clear()
        s.settings.toggle_in('muted_rooms', 'r2')
        self.conn.push('msg_new', message=dict(m, msg_id=22))
        self.assertEqual(len(notes), 1)
        self.assertEqual(s.total_unread(), 2)  # 알림 끈 방은 합계 제외

    def test_my_message_advances_my_pointer(self):
        s = self.state
        m = dict(msg_id=30, room_id='r1', sender='홍길동', kind='text', content='hi', timestamp=datetime.now().isoformat(),
                 reply_to=None, mentions=[], reactions={}, deleted=False, attachment=None, event_id=None, poll=None)
        self.conn.push('msg_new', message=m)
        self.assertEqual(s.room('r1')['read_pointers']['홍길동'], 30)
        self.assertEqual(s.message_unread('r1', m), 2)

    def test_sorted_rooms_pinned_first(self):
        s = self.state
        self.assertEqual(s.sorted_rooms()[0]['room_id'], 'r1')
        s.settings.toggle_in('pinned_rooms', 'r3')
        self.assertEqual(s.sorted_rooms()[0]['room_id'], 'r3')

    def test_open_direct_existing_and_pending(self):
        s = self.state
        opened = self.collect(s.room_opened)
        s.open_direct('박민수')
        self.assertEqual(opened[-1], ('r2',))
        self.assertIsNone(self.conn.last('room_open_direct'))
        s.open_direct('최지우')
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '최지우'})
        self.conn.push('room_updated', room={'room_id': 'r9', 'type': 'direct', 'name': '', 'members': ['홍길동', '최지우'],
                                             'created_at': '', 'notice': None, 'last_message': None,
                                             'read_pointers': {}, 'unread_count': 0})
        self.assertEqual(opened[-1], ('r9',))

    def test_create_group_opens_when_created(self):
        s = self.state
        opened = self.collect(s.room_opened)
        s.create_group(['김철수', '최지우'], name='')
        self.assertEqual(self.conn.last('room_create'), {'name': '', 'members': ['김철수', '최지우']})
        self.conn.push('room_updated', room={'room_id': 'g2', 'type': 'group', 'name': '', 'members': ['홍길동', '김철수', '최지우'],
                                             'created_at': '', 'notice': None, 'last_message': None,
                                             'read_pointers': {}, 'unread_count': 0})
        self.assertEqual(opened, [('g2',)])
        self.assertEqual(s.room_title('g2'), '김철수, 최지우')

    def test_send_text_with_mentions_reply_and_schedule(self):
        s = self.state
        s.send_text('r1', '  @김철수 확인 부탁해요  ', reply_to_id=5)
        p = self.conn.last('send')
        self.assertEqual(p['content'], '@김철수 확인 부탁해요')
        self.assertEqual(p['mentions'], ['김철수'])
        self.assertEqual(p['reply_to_id'], 5)
        when = datetime(2030, 1, 1, 9, 0)
        s.send_text('r1', '새해', send_at=when)
        self.assertEqual(self.conn.last('send')['send_at'], '2030-01-01T09:00:00')
        n = len(self.conn.sent)
        s.send_text('r1', '   ')
        self.assertEqual(len(self.conn.sent), n)

    def test_send_file_limits(self):
        s = self.state
        d = tempfile.mkdtemp()
        small = os.path.join(d, '사진.png')
        with open(small, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'0' * 100)          # 사진인지는 내용 머리말로 본다
        self.assertIsNone(s.send_file('r1', small))
        p = self.conn.last('send')
        self.assertEqual(p['kind'], 'image')
        self.assertEqual(p['attachment']['mime'], 'image/png')
        big = os.path.join(d, 'big.bin')
        with open(big, 'wb') as f:
            f.write(b'0' * (2 * 1024 * 1024 + 1))
        self.assertIsNone(s.send_file('r1', big))

    def test_msg_updated_refreshes_last_and_notice(self):
        s = self.state
        m = dict(s.find_message('r1', 4), reactions={'❤️': ['김철수']})
        self.conn.push('msg_updated', message=m)
        self.assertEqual(s.room('r1')['notice']['reactions'], {'❤️': ['김철수']})

    def test_hide_for_me_and_delete_rules(self):
        s = self.state
        s.hide_for_me('r1', 3)
        self.assertNotIn(3, [m['msg_id'] for m in s.room_messages('r1')])
        self.assertTrue(s.can_delete_for_all(s.find_message('r1', 4)))
        self.assertFalse(s.can_delete_for_all(s.find_message('r1', 2)))  # 남의 메시지
        old = dict(s.find_message('r1', 4), timestamp=(datetime.now() - timedelta(days=2)).isoformat())
        self.assertFalse(s.can_delete_for_all(old))

    def test_drawer_items(self):
        photos, files, links = self.state.drawer_items('r1')
        self.assertEqual([m['msg_id'] for m in files], [9])
        self.assertEqual(links[0][0], 'https://example.com/docs')

    def test_events_queries(self):
        s = self.state
        today = date.today()
        self.assertEqual([e['title'] for e in s.events_on(today)], ['치과 예약'])
        self.assertEqual([e['title'] for e in s.events_on(today + timedelta(days=1))], ['주간 회의'])
        self.assertEqual([e['title'] for e in s.events_on(today + timedelta(days=4))], ['워크숍'])
        self.assertEqual(s.my_rsvp(s.events[1]), 'yes')
        s.create_event(title='t', start=datetime(2030, 1, 1, 9), end=datetime(2030, 1, 1, 10), room_id='r1')
        self.assertEqual(self.conn.last('event_create')['start'], '2030-01-01T09:00:00')
        self.conn.push('event_deleted', event_id=2)
        self.assertEqual(s.events_on(today), [])

    def test_event_reminder_notifies(self):
        s = self.state
        notes = self.collect(s.notify)
        reminders = self.collect(s.event_reminder)
        self.conn.push('event_reminder', event=s.events[1], minutes_before=10)
        self.assertEqual(reminders[0][1], 10)
        self.assertIn('주간 회의', notes[0][0])

    def test_todos(self):
        s = self.state
        # 미완료(마감 빠른 순, 마감 없음은 뒤) → 완료
        self.assertEqual([t['title'] for t in s.sorted_todos()], ['회의 자료 준비', '장보기', '보고서 제출'])
        s.create_todo('새 할 일', due=date(2030, 1, 2))
        self.assertEqual(self.conn.last('todo_create'), {'title': '새 할 일', 'due': '2030-01-02'})
        s.update_todo(1, done=True)
        self.assertEqual(self.conn.last('todo_update'), {'todo_id': 1, 'done': True})

    def test_attachment_cache(self):
        s = self.state
        got = self.collect(s.attachment_loaded)
        s.request_attachment(1)
        self.assertEqual(self.conn.last('attachment_get'), {'attachment_id': 1})
        self.conn.push('attachment_data', attachment_id=1, name='a.txt', mime='text/plain', data_b64='aGk=')
        self.assertEqual(got[0][1], b'hi')
        n = len(self.conn.sent)
        s.request_attachment(1)
        self.assertEqual(len(self.conn.sent), n)  # 캐시 사용
        self.assertEqual(len(got), 2)

    def test_invite_group_and_direct(self):
        s = self.state
        names = [u['username'] for u in s.invitable_users('r1')]
        self.assertEqual(sorted(names), sorted(['박민수', '최지우']))
        self.assertFalse(s.invite('r1', ['최지우', '김철수']))  # 이미 멤버인 김철수는 제외
        self.assertEqual(self.conn.last('room_invite'), {'room_id': 'r1', 'usernames': ['최지우']})
        opened = self.collect(s.room_opened)
        self.assertTrue(s.invite('r2', ['이영희']))  # 1:1 → 새 그룹방
        self.assertEqual(sorted(self.conn.last('room_create')['members']), ['박민수', '이영희'])
        self.conn.push('room_updated', room={'room_id': 'g7', 'type': 'group', 'name': '',
                                             'members': ['홍길동', '박민수', '이영희'], 'created_at': '',
                                             'notice': None, 'last_message': None, 'read_pointers': {},
                                             'unread_count': 0})
        self.assertEqual(opened, [('g7',)])
        n = len(self.conn.sent)
        self.assertFalse(s.invite('r1', []))
        self.assertEqual(len(self.conn.sent), n)

    def test_room_removed(self):
        s = self.state
        self.conn.push('room_removed', room_id='r2')
        self.assertIsNone(s.room('r2'))
        self.assertEqual(s.room_messages('r2'), [])

    def test_error_and_status(self):
        s = self.state
        errs = self.collect(s.error)
        self.conn.push('error', req_type='send', message='권한이 없습니다')
        self.assertEqual(errs, [('권한이 없습니다',)])
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.assertTrue(s.is_online('이영희'))


class Kakao2025StateTest(unittest.TestCase):
    """메시지 수정 · 조용히 보내기 · 채팅방 폴더 · 미리보기 · 일정 변경 알림 · 서랍 일정"""

    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def collect(self, signal):
        got = []
        signal.connect(lambda *a: got.append(a))
        return got

    def test_edit_rules_and_request(self):
        s = self.state
        mine = s.find_message('r1', 4)
        self.assertTrue(s.can_edit(mine))
        self.assertFalse(s.can_edit(s.find_message('r1', 2)))      # 남의 메시지
        self.assertFalse(s.can_edit(s.find_message('r1', 7)))      # 투표(비텍스트)
        s.edit_message(4, '  수정된 내용 ')
        self.assertEqual(self.conn.last('edit'), {'msg_id': 4, 'content': '수정된 내용'})

    def test_silent_send_and_no_notification(self):
        s = self.state
        s.send_text('r2', '조용히', silent=True)
        self.assertTrue(self.conn.last('send')['silent'])
        s.send_text('r2', '보통')
        self.assertNotIn('silent', self.conn.last('send'))
        notes = self.collect(s.notify)
        m = dict(s.find_message('r2', 14), msg_id=40, silent=True, content='쉿')
        self.conn.push('msg_new', message=m)
        self.assertEqual(notes, [])
        self.assertEqual(s.room_unread('r2'), 2)   # 안 읽은 수에는 반영

    def test_folders(self):
        s = self.state
        names = [n for n, _ in s.chat_folders()]
        self.assertEqual(names, ['전체', '안읽음', '즐겨찾기', '통화'])
        self.assertEqual({r['room_id'] for r in s.rooms_in_folder('안읽음')}, {'r1', 'r2'})
        self.assertEqual(s.rooms_in_folder('즐겨찾기'), [])
        s.settings.toggle_in('favorites', '박민수')
        self.assertEqual([r['room_id'] for r in s.rooms_in_folder('즐겨찾기')], ['r2'])
        self.assertIn('기본 폴더', s.save_chat_folder('안읽음', ['r1']))
        self.assertIsNone(s.save_chat_folder('직장', ['r1']))
        self.assertEqual([r['room_id'] for r in s.rooms_in_folder('직장')], ['r1'])
        s.toggle_room_in_folder('직장', 'r3')
        self.assertEqual({r['room_id'] for r in s.rooms_in_folder('직장')}, {'r1', 'r3'})
        self.assertIsNone(s.save_chat_folder('업무', ['r1'], old_name='직장'))
        self.assertEqual([n for n, _ in s.chat_folders()][-1], '업무')
        s.delete_chat_folder('업무')
        self.assertEqual(len(s.chat_folders()), 4)

    def test_peek_does_not_mark_read(self):
        s = self.state
        peek = s.peek_messages('r1')
        self.assertEqual([m['msg_id'] for m in peek], [9, 11])     # msg 10(삭제)·자기 메시지 제외 후 최근
        self.assertIsNone(self.conn.last('read'))

    def test_event_change_notification(self):
        s = self.state
        notes = self.collect(s.notify)
        ev = dict(s.events[1], creator='김철수', location='5층 대회의실')
        self.conn.push('event_updated', event=ev)
        self.assertIn('일정이 변경되었습니다', notes[0][0])
        self.assertIn('장소', notes[0][1])
        # 내가 만든 일정이나 처음 받는 일정은 알림 없음
        self.conn.push('event_updated', event=dict(ev, event_id=99, title='새 일정'))
        self.assertEqual(len(notes), 1)

    def test_events_in_room(self):
        self.assertEqual([e['title'] for e in self.state.events_in_room('r1')], ['주간 회의'])


if __name__ == '__main__':
    unittest.main()
