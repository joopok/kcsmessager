# -*- coding: utf-8 -*-
"""AI 비서 작업 처리 (명세 §4.5 'AI 비서 작업 처리', 2026-11-17): 느린 답변이 다른 방 · 색인 · 다음 요청을 세우지 않는다"""

import base64
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from tests.test_kakao_server import ServerTestBase, FakeRag

from server.kakao import rag as rag_mod
from server.kakao.server import KakaoServer
from server.kakao.store import BOT_USERNAME

SLOW = 0.8


class SlowRag(FakeRag):
    """'느린' 이 들어간 질문과 문서 추가는 SLOW 초 걸린다"""

    def answer(self, question, asker, room_ids, history):
        if '느린' in question:
            time.sleep(SLOW)
        return super().answer(question, asker, room_ids, history)

    def add_document(self, owner, name, data, mime):
        time.sleep(SLOW)
        return super().add_document(owner, name, data, mime)


class RagWorkerTests(ServerTestBase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix='kakao_ragw_')
        cls.db_path = os.path.join(cls.tmpdir, 't.db')
        cls.server = KakaoServer(port=0, db_path=cls.db_path, tick_seconds=0.2, rag_factory=SlowRag)
        cls.server.serve_in_thread()
        cls.rag = cls.server.rag
        cls.port = cls.server.port

    def bot_room(self, client):
        client.send({'type': 'room_open_direct', 'target': BOT_USERNAME})
        return client.wait_for('room_updated', lambda m: BOT_USERNAME in m['room']['members'])['room']['room_id']

    def bot_answer(self, client, timeout=6.0):
        return client.wait_for('msg_new', lambda m: m['message']['sender'] == BOT_USERNAME, timeout=timeout)['message']

    def test_questions_in_different_rooms_do_not_wait_for_each_other(self):
        askers = [self.connect() for _ in range(3)]
        rooms = [self.bot_room(c) for c in askers]
        started = time.monotonic()
        for client, room in zip(askers, rooms):
            client.send({'type': 'send', 'room_id': room, 'kind': 'text', 'content': '느린 질문'})
        for client in askers:
            self.bot_answer(client)
        self.assertLess(time.monotonic() - started, SLOW * 2.2)                 # 차례로 하면 SLOW × 3

    def test_questions_in_one_room_are_answered_in_order(self):
        a = self.connect()
        room = self.bot_room(a)
        a.send({'type': 'send', 'room_id': room, 'kind': 'text', 'content': '느린 첫째'})
        a.send({'type': 'send', 'room_id': room, 'kind': 'text', 'content': '빠른 둘째'})
        first, second = self.bot_answer(a), self.bot_answer(a)
        self.assertEqual((first['content'], second['content']), ('답변: 느린 첫째', '답변: 빠른 둘째'))

    def test_indexing_is_not_held_up_by_a_slow_answer(self):
        a, b = self.connect(), self.connect()
        plain = self.group(a, [b])['room_id']
        room = self.bot_room(a)
        a.send({'type': 'send', 'room_id': room, 'kind': 'text', 'content': '느린 질문'})
        time.sleep(0.1)
        sent = time.monotonic()
        self.send_text(b, plain, '바로 색인될 글')
        while time.monotonic() - sent < SLOW:
            with self.rag.lock:
                if any(m['content'] == '바로 색인될 글' for m in self.rag.indexed):
                    break
            time.sleep(0.02)
        self.assertLess(time.monotonic() - sent, SLOW * 0.6)
        self.bot_answer(a)

    def test_document_is_read_outside_the_request(self):
        a = self.connect()
        payload = base64.b64encode('문서 내용입니다'.encode()).decode()
        started = time.monotonic()
        a.send({'type': 'rag_doc_add', 'name': 'memo.txt', 'mime': 'text/plain', 'data_b64': payload, 'req_id': 'doc'})
        a.request({'type': 'todo_list', 'req_id': 'next'}, 'todo_list', lambda m: m.get('req_id') == 'next')
        self.assertLess(time.monotonic() - started, SLOW * 0.6)                 # 문서를 읽는 동안에도 다음 요청은 바로
        added = a.wait_for('rag_doc_added', lambda m: m.get('req_id') == 'doc', timeout=6.0)
        self.assertEqual(added['doc']['name'], 'memo.txt')
        a.send({'type': 'rag_doc_add', 'name': 'tool.exe', 'data_b64': payload, 'req_id': 'bad'})
        err = a.wait_for('error', lambda m: m.get('req_id') == 'bad', timeout=6.0)
        self.assertEqual(err['req_type'], 'rag_doc_add')
        self.assertNotIn('code', err)

    def test_backlog_limit(self):
        gate = threading.Event()
        done = []
        with mock.patch.object(self.server, 'RAG_BACKLOG', 2):
            self.assertTrue(self.server._rag_slow_put('k1', lambda: (gate.wait(5), done.append(1))))
            self.assertTrue(self.server._rag_slow_put('k1', lambda: done.append(2)))
            self.assertFalse(self.server._rag_slow_put('k2', lambda: done.append(3)))     # 밀려 있으면 받지 않는다
            gate.set()
            deadline = time.monotonic() + 5
            while len(done) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(done, [1, 2])                                     # 같은 key 는 맡긴 순서대로
            self.assertTrue(self.server._rag_slow_put('k2', lambda: done.append(4)))


class DocumentLimitTests(unittest.TestCase):
    def test_pdf_reading_stops_at_the_limits(self):
        read = []

        class Page:
            def __init__(self, number):
                self.number = number

            def extract_text(self):
                read.append(self.number)
                return '가' * 1000

        fake = types.ModuleType('pypdf')
        fake.PdfReader = lambda stream: types.SimpleNamespace(pages=[Page(i) for i in range(5000)])
        with mock.patch.dict(sys.modules, {'pypdf': fake}):
            text = rag_mod.decode_document('big.pdf', b'%PDF-1.4')
            self.assertEqual(len(read), rag_mod.MAX_PDF_PAGES)
            read.clear()
            with mock.patch.object(rag_mod, 'MAX_DOC_CHARS', 2500):
                rag_mod.decode_document('big.pdf', b'%PDF-1.4')
            self.assertEqual(len(read), 3)                                     # 글자 수 한도에서 멈춘다
        self.assertGreater(len(text), 0)

    def test_long_document_is_cut_and_marked(self):
        tmp = tempfile.mkdtemp(prefix='kcs_rag_')
        self.addCleanup(shutil.rmtree, tmp, True)
        service = rag_mod.RagService(os.path.join(tmp, 'rag.db'))
        self.addCleanup(service.close)
        with mock.patch.object(rag_mod, 'MAX_DOC_CHARS', 3000):
            long_doc = service.add_document('a', 'long.txt', ('긴 문서 내용 ' * 2000).encode(), 'text/plain')
            short_doc = service.add_document('a', 'short.txt', '짧은 문서'.encode(), 'text/plain')
        self.assertTrue(long_doc['truncated'])
        self.assertNotIn('truncated', short_doc)
        self.assertLessEqual(long_doc['chunks'], 10)
