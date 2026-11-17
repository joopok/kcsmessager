# -*- coding: utf-8 -*-
"""무거운 조회는 읽기 전용 두 번째 연결에서 (명세 §5, 2026-11-17): 검색 한 번이 다른 요청을 세우지 않는다"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from server.kakao.store import Store


class ReadConnectionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kcs_read_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = Store(os.path.join(self.dir, 'kakao.db'))
        self.addCleanup(self.store.close)
        for user in ('a', 'b'):
            self.store.ensure_user(user)
        self.room = self.store.create_group('a', '방', ['b'])

    def say(self, text, **extra):
        return self.store.add_message(self.room, 'a', 'text', text, **extra)

    def in_thread(self, func, timeout=3.0):
        result = []
        worker = threading.Thread(target=lambda: result.append(func()), daemon=True)
        worker.start()
        worker.join(timeout)
        self.assertFalse(worker.is_alive(), '끝나지 않았습니다 (다른 잠금을 기다리는 중)')
        return result[0]

    def test_heavy_reads_do_not_take_the_main_lock(self):
        self.say('회의 일정 공유 https://example.com/a', mentions=['b'])
        with self.store._lock:                       # 다른 요청이 쓰기용 잠금을 쥐고 있어도
            found = self.in_thread(lambda: self.store.search('b', '일정', None))
            links = self.in_thread(lambda: self.store.drawer('b', 'link', 10))
            mine = self.in_thread(lambda: self.store.mentions_of('b'))
        self.assertEqual([len(found), len(links), len(mine)], [1, 1, 1])

    def test_a_long_read_does_not_hold_up_writes(self):
        with self.store._read_lock:                  # 검색이 오래 도는 중이라고 치고
            started = time.monotonic()
            self.in_thread(lambda: self.say('검색 중에도 저장된다'))
            self.in_thread(lambda: self.store.update_read(self.room, 'b', 1))
            self.assertLess(time.monotonic() - started, 1.0)

    def test_search_rules_are_unchanged(self):
        for text in ('한글 두 글자', 'Mixed CASE text', '100% 진행_완료', '다른 이야기'):
            self.say(text)
        contents = lambda query: [m['content'] for m in self.store.search('b', query, None)]  # noqa: E731
        self.assertEqual(contents('두 글'), ['한글 두 글자'])
        self.assertEqual(contents('글자'), ['한글 두 글자'])                     # 한글 2글자
        self.assertEqual(contents('mixed case'), ['Mixed CASE text'])          # 영문 대소문자 무시
        self.assertEqual(contents('100%'), ['100% 진행_완료'])                  # % 와 _ 는 글자 그대로
        self.assertEqual(contents('행_완'), ['100% 진행_완료'])
        self.assertEqual(contents('없는말'), [])
        self.assertEqual(contents('이야기'), ['다른 이야기'])
        self.assertEqual([m['content'] for m in self.store.search('b', '글자', self.room)], ['한글 두 글자'])

    def test_what_was_just_written_is_found_at_once(self):
        for i in range(50):
            self.say(f'방금 쓴 글 {i}')
            self.assertEqual(self.store.search('b', f'쓴 글 {i}', None)[0]['content'], f'방금 쓴 글 {i}')
        last = self.say('지울 글')
        self.store.delete_message(last['msg_id'])
        self.assertEqual(self.store.search('b', '지울 글', None), [])

    def test_read_connection_cannot_write(self):
        self.assertIsNotNone(self.store._read_db)
        with self.assertRaises(sqlite3.OperationalError):
            self.store._read_db.execute("DELETE FROM messages")

    def test_memory_database_still_works(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        self.assertIsNone(store._read_db)
        store.ensure_user('a')
        room = store.create_group('a', '방', [])
        store.add_message(room, 'a', 'text', '메모리에서도 검색')
        self.assertEqual(len(store.search('a', '검색', None)), 1)

    def test_close_and_reopen(self):
        self.say('다시 열어도 그대로')
        self.store.close()
        self.store.close()                           # 두 번 닫아도 된다
        again = Store(os.path.join(self.dir, 'kakao.db'))
        self.addCleanup(again.close)
        self.assertEqual(len(again.search('b', '다시 열어도', None)), 1)
