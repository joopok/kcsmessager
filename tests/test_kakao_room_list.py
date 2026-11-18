# -*- coding: utf-8 -*-
"""방 목록 묶음 조회와 일정 알림 검사 (2026-11-17): 조회 횟수만 줄이고 결과는 예전과 같다"""

import base64
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from tests.test_kakao_server import PNG_1PX

from server.kakao.store import ISO_FMT, Store

GRACE = timedelta(seconds=60)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_rooms_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = Store(os.path.join(self.dir, 'kakao.db'))
        self.addCleanup(self.store.close)


class RoomListTests(StoreCase):
    def build(self):
        s = self.store
        for user in ('a', 'b', 'c', 'd', 'late'):
            s.ensure_user(user)
        plain = s.create_group('a', '보통 방', ['b', 'c'])
        for i in range(5):
            s.add_message(plain, 'b' if i % 2 else 'a', 'text', f'보통 {i}')
        s.update_read(plain, 'c', 3)
        noticed = s.create_group('b', '공지 방', ['a', 'c'])
        first = s.add_message(noticed, 'b', 'text', '공지할 글')
        s.set_notice(noticed, first['msg_id'])
        s.create_poll(noticed, 'b', '투표', ['가', '나'], False)
        attachment = s.save_attachment('p.png', 'image/png', PNG_1PX, 'c')
        s.add_message(noticed, 'c', 'image', 'p.png', attachment_id=attachment)
        direct, _ = s.get_or_create_direct('a', 'b')
        s.add_message(direct, 'b', 'text', '1:1 메시지')
        s.get_or_create_direct('a', 'a')                                        # 나와의 채팅 (메시지 없음)
        opened = s.create_open_room('c', '오픈방', '소개', ['태그'], '방장별명')
        s.join_open_room(opened, 'a', '내별명')
        s.add_message(opened, 'c', 'text', '오픈방 글')
        blocked = s.create_group('a', '차단 있는 방', ['d', 'b'])
        s.add_message(blocked, 'b', 'text', '보이는 글')
        s.add_message(blocked, 'd', 'text', '차단한 사람의 마지막 글')
        s.set_block('a', 'd', True)
        joined_late = s.create_group('b', '나중에 들어온 방', ['c'])
        for i in range(3):
            s.add_message(joined_late, 'b', 'text', f'들어오기 전 {i}')
        s.add_members(joined_late, ['late', 'a'])
        only_old = s.create_group('c', '들어온 뒤 조용한 방', ['b'])
        s.add_message(only_old, 'c', 'text', '예전 글')
        s.add_members(only_old, ['late'])
        s.add_message(joined_late, 'c', 'text', '들어온 뒤 글')
        s.create_group('d', '빈 방', ['a'])
        deleted = s.add_message(plain, 'a', 'text', '지울 글')
        s.delete_message(deleted['msg_id'])

    def one_by_one(self, user):
        s = self.store
        rooms = [s.room_dict(room_id, user) for room_id in s.room_ids_for_user(user)]
        rooms.sort(key=lambda r: (r['last_message']['timestamp'] if r['last_message'] else r['created_at']), reverse=True)
        return rooms

    def test_same_rooms_as_asking_one_by_one(self):
        self.build()
        for user in ('a', 'b', 'c', 'd', 'late'):
            listed = self.store.rooms_for_user(user)
            expected = self.one_by_one(user)
            self.assertEqual({r['room_id']: r for r in listed}, {r['room_id']: r for r in expected}, user)
            self.assertEqual(len(listed), len(expected))
        mine = {r['name']: r for r in self.store.rooms_for_user('a')}
        self.assertEqual(mine['차단 있는 방']['last_message']['content'], '보이는 글')
        self.assertEqual(mine['차단 있는 방']['unread_count'], 1)
        self.assertEqual(mine['공지 방']['notice']['content'], '공지할 글')
        self.assertEqual(mine['오픈방']['nicknames'], {'c': '방장별명', 'a': '내별명'})
        late = {r['name']: r for r in self.store.rooms_for_user('late')}
        self.assertEqual(late['나중에 들어온 방']['last_message']['content'], '들어온 뒤 글')
        self.assertIsNone(late['들어온 뒤 조용한 방']['last_message'])
        self.assertEqual(self.store.rooms_for_user('nobody'), [])

    def test_number_of_queries_does_not_grow_with_rooms(self):
        s = self.store
        s.ensure_user('me')
        s.ensure_user('you')

        def count(rooms):
            for i in range(rooms):
                room = s.create_group('me', f'방{i}', ['you'])
                s.add_message(room, 'you', 'text', '글')
            seen = []
            s._db.set_trace_callback(seen.append)
            try:
                s.rooms_for_user('me')
            finally:
                s._db.set_trace_callback(None)
            return len([q for q in seen if q.lstrip().upper().startswith('SELECT')])
        few = count(3)
        many = count(40)
        self.assertEqual(few, many)
        self.assertLessEqual(many, 6)


class ReminderScanTests(StoreCase):
    def event(self, start, remind):
        fields = {'title': '일정', 'start': start.strftime(ISO_FMT), 'end': (start + timedelta(hours=1)).strftime(ISO_FMT),
                  'all_day': False, 'description': '', 'location': '', 'remind_minutes': remind, 'color': '#FEE500'}
        return self.store.create_event('a', fields, [], None)[0]['event_id']

    def due(self, now):
        return [(event['event_id'], minutes) for event, minutes in self.store.due_reminders(now, GRACE)]

    def test_due_once_inside_the_window(self):
        self.store.ensure_user('a')
        now = datetime.now().replace(microsecond=0)
        soon = self.event(now + timedelta(minutes=5), 10)                       # 알림 시각이 이미 지남 → 지금
        later = self.event(now + timedelta(minutes=30), 10)                     # 아직 20분 남음
        at_start = self.event(now + timedelta(seconds=20), 0)                   # 시작할 때 알림 - 아직 아님
        just_started = self.event(now - timedelta(seconds=30), 0)               # 방금 시작 (유예 시간 안)
        self.event(now - timedelta(minutes=5), 10)                              # 이미 지난 일정
        self.event(now + timedelta(minutes=5), None)                            # 알림 없음
        far = self.event(now + timedelta(days=29, hours=23), 60 * 24 * 30)      # 30일 전 알림 → 지금
        self.assertEqual(sorted(self.due(now)), sorted([(soon, 10), (just_started, 0), (far, 60 * 24 * 30)]))
        self.assertEqual(self.due(now), [])                                     # 한 번만
        self.assertEqual(self.due(now + timedelta(seconds=25)), [(at_start, 0)])
        self.assertEqual(self.due(now + timedelta(minutes=21)), [(later, 10)])

    def test_scan_uses_the_start_index(self):
        plan = ' '.join(str(tuple(row)) for row in self.store._db.execute(
            'EXPLAIN QUERY PLAN SELECT event_id FROM events WHERE remind_minutes IS NOT NULL AND start>=? AND start<=?',
            ('2026-01-01T00:00:00', '2026-02-01T00:00:00')))
        self.assertIn('idx_events_remind', plan)

    def test_nothing_due_writes_nothing(self):
        self.store.ensure_user('a')
        now = datetime.now().replace(microsecond=0)
        self.event(now + timedelta(days=3), 10)
        self.assertEqual(self.due(now), [])
        self.assertFalse(self.store._db.in_transaction)
