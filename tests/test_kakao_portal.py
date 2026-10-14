# -*- coding: utf-8 -*-
"""
사내 포털(portal.kcins.co.kr) RAG 연동 테스트 (명세 §7.6.1) - 가짜 포털 HTTP 서버를 띄워 확인한다.
실행: python3 -m unittest tests.test_kakao_portal -v
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ['KCS_LINK_PREVIEW'] = 'off'

from server.kakao import portal as portal_mod  # noqa: E402
from server.kakao.portal import (FALLBACK_NOTE, PortalClient, PortalError, PortalRag, load_config,  # noqa: E402
                                 parse_answer)
from server.kakao.server import KakaoServer  # noqa: E402
from tests.test_kakao_server import BOT, Client, FakeRag, uname  # noqa: E402

logging.getLogger('kakao.portal').setLevel(logging.CRITICAL)
logging.getLogger('kakao.server').setLevel(logging.CRITICAL)


class FakePortal:
    """포털 대역: /api/rag/ask 는 답변, /api/rag/conversations 는 저장. mode='down' 이면 503"""

    def __init__(self):
        self.requests = []
        self.mode = 'ok'
        self.cond = threading.Condition()
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                with portal.cond:
                    portal.requests.append({'path': self.path, 'body': body, 'headers': dict(self.headers),
                                            'ok': portal.mode != 'down'})
                    portal.cond.notify_all()
                if portal.mode == 'down':
                    self.send_response(503)
                    self.end_headers()
                    return
                if self.path.endswith(('/ask', '/ask-as')):
                    out = {'data': {'answer': f"포털 답변: {body['question']}",
                                    'sources': [{'title': '인사규정.pdf', 'url': 'https://portal.kcins.co.kr/d/1'}],
                                    'conversation_id': 'conv-42'}}
                elif self.path.endswith('/chat/completions'):
                    out = {'choices': [{'message': {'role': 'assistant',
                                                    'content': '오픈AI형 답변: ' + body['messages'][-1]['content']}}]}
                else:
                    out = {'ok': True, 'id': len(portal.requests)}
                raw = json.dumps(out, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def saved(self):
        with self.cond:   # Condition 은 RLock 기반이라 wait_saved 안에서 다시 잡아도 된다
            return [r['body'] for r in self.requests if r['path'].endswith('/conversations') and r['ok']]

    def wait_saved(self, count=1, predicate=None, timeout=5.0):
        """성공한 저장 중 predicate 에 맞는 것이 count 건 이상 될 때까지 기다려 그 목록을 돌려준다"""
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                hits = [b for b in self.saved() if predicate is None or predicate(b)]
                if len(hits) >= count:
                    return hits
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f'포털 저장 {count}건 대기 시간 초과: {self.requests}')
                self.cond.wait(left)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LocalRag:
    """메신저 자체 RAG 대역"""
    def __init__(self):
        self.indexed, self._llm_failed, self.closed = [], False, False

    def answer(self, question, asker, room_ids, history):
        return f'로컬 답변: {question}'

    def index_message(self, message):
        self.indexed.append(message)

    def list_documents(self, owner):
        return [{'doc_id': 1, 'owner': owner}]

    def close(self):
        self.closed = True


class ConfigTest(unittest.TestCase):
    def test_defaults_point_to_company_portal(self):
        config = load_config(None, env={})
        self.assertEqual(config['base_url'], 'https://portal.kcins.co.kr')
        self.assertEqual((config['ask_path'], config['save_path']), ('/api/chat/ask-as', '/api/rag/conversations'))
        self.assertEqual((config['auth_header'], config['auth_scheme']), ('X-Service-Token', ''))
        self.assertFalse(config['save_conversations'])                 # ask-as 가 포털 쪽에 대화를 남긴다

    def test_off_and_env_override(self):
        self.assertIsNone(load_config(None, env={'KCS_PORTAL': 'off'}))
        config = load_config(None, env={'KCS_PORTAL_URL': 'http://10.0.0.5:8080/', 'KCS_PORTAL_TOKEN': 'tk',
                                        'KCS_PORTAL_STYLE': 'openai', 'KCS_PORTAL_TIMEOUT': '5'})
        self.assertEqual((config['base_url'], config['token'], config['api_style'], config['timeout']),
                         ('http://10.0.0.5:8080', 'tk', 'openai', 5.0))

    def test_json_file_and_bad_values(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, 'portal.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'base_url': 'https://p.example', 'ask_path': '/rag/query', 'api_style': '???',
                           'timeout': 'x', 'unknown': 1}, f)
            config = load_config(path, env={})
            self.assertEqual((config['base_url'], config['ask_path'], config['api_style'], config['timeout']),
                             ('https://p.example', '/rag/query', 'kcs', 90.0))
            self.assertNotIn('unknown', config)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'enabled': False}, f)
            self.assertIsNone(load_config(path, env={}))
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{broken')
            self.assertIsNotNone(load_config(path, env={}))      # 깨진 파일은 기본값으로
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ParseAnswerTest(unittest.TestCase):
    def test_common_shapes(self):
        self.assertEqual(parse_answer({'answer': '가'})[0], '가')
        self.assertEqual(parse_answer({'data': {'answer': '나'}})[0], '나')
        self.assertEqual(parse_answer({'response': '다'})[0], '다')
        self.assertEqual(parse_answer({'choices': [{'message': {'content': '라'}}]})[0], '라')
        self.assertEqual(parse_answer('그냥 텍스트')[0], '그냥 텍스트')
        text, sources, conv = parse_answer({'answer': '마', 'references': ['규정.pdf', {'name': '공지', 'link': 'u'},
                                                                          '규정.pdf'], 'sessionId': 7})
        self.assertEqual((text, sources, conv), ('마', ['규정.pdf', '공지 u'], '7'))

    def test_no_answer_raises(self):
        with self.assertRaises(PortalError):
            parse_answer({'status': 'ok'})


class PortalRagTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakePortal()
        self.dir = tempfile.mkdtemp()
        self.config = load_config(None, env={'KCS_PORTAL_URL': self.fake.url, 'KCS_PORTAL_TOKEN': 'secret-token'})
        self.config['save_conversations'] = True      # 저장 흐름도 검사 (기본값은 false — ask-as 가 포털에 남김)
        self.local = LocalRag()
        self.rag = PortalRag(self.local, self.config, os.path.join(self.dir, 'outbox.db'), start_worker=False)

    def tearDown(self):
        self.rag.close()
        self.fake.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def ask(self, q='연차 며칠이야?'):
        history = [{'kind': 'text', 'sender': BOT, 'content': '안녕하세요'},
                   {'kind': 'text', 'sender': 'kim', 'content': q}]
        return self.rag.answer(q, 'kim', ['r1'], history, room_id='r1', asker_name='김철수', room_name='AI 비서',
                               bot_name=BOT, asker_email='kim@kcs.test')

    def test_portal_answers_with_sources_and_token(self):
        text = self.ask()
        self.assertTrue(text.startswith('포털 답변: 연차 며칠이야?'))
        self.assertIn('📎 출처 (사내 포털)\n[1] 인사규정.pdf https://portal.kcins.co.kr/d/1', text)
        req = self.fake.requests[0]
        self.assertEqual(req['path'], '/api/chat/ask-as')
        headers = {k.lower(): v for k, v in req['headers'].items()}
        self.assertEqual(headers.get('x-service-token'), 'secret-token')          # 공유 비밀, 스킴 없음
        self.assertNotIn('authorization', headers)
        body = req['body']
        self.assertEqual((body['question'], body['user'], body['user_name'], body['email'], body['source']),
                         ('연차 며칠이야?', 'kim', '김철수', 'kim@kcs.test', 'kcs-talk'))
        self.assertEqual(body['history'], [{'role': 'assistant', 'content': '안녕하세요'}])  # 질문 자체는 빼고
        # 포털이 준 대화 id 를 기억해 다음 질문에 넘긴다
        self.ask('그럼 반차는?')
        self.assertEqual(self.fake.requests[-1]['body']['conversation_id'], 'conv-42')

    def test_conversation_saved_to_portal(self):
        text = self.ask()
        self.assertEqual(self.rag.outbox.stats(), {'total': 1, 'pending': 1})
        self.assertEqual(self.rag.flush(), 1)
        saved = self.fake.saved()[0]
        self.assertEqual((saved['user'], saved['user_name'], saved['room_id'], saved['room_name'],
                          saved['answered_by'], saved['conversation_id']),
                         ('kim', '김철수', 'r1', 'AI 비서', 'portal', 'conv-42'))
        self.assertEqual([(m['role'], m['content']) for m in saved['messages']],
                         [('user', '연차 며칠이야?'), ('assistant', text)])
        self.assertEqual(self.rag.outbox.stats()['pending'], 0)
        self.assertEqual(self.rag.flush(), 0)          # 이미 보낸 기록은 다시 보내지 않는다

    def test_portal_down_falls_back_and_keeps_record(self):
        self.fake.mode = 'down'
        text = self.ask()
        self.assertEqual(text, '로컬 답변: 연차 며칠이야?' + FALLBACK_NOTE)
        self.assertEqual(self.rag.flush(), 0)           # 저장도 실패 → 보낼 함에 남는다
        self.assertEqual(self.rag.outbox.stats()['pending'], 1)
        asks = len([r for r in self.fake.requests if r['path'].endswith('/ask')])
        self.ask('두 번째 질문')                         # 잠시 동안은 포털을 건너뛴다 (응답 지연 방지)
        self.assertEqual(len([r for r in self.fake.requests if r['path'].endswith('/ask')]), asks)
        self.fake.mode = 'ok'                           # 포털 복구 → 밀린 기록을 순서대로 보낸다
        self.assertEqual(self.rag.flush(), 2)
        saved = self.fake.saved()
        self.assertEqual([s['answered_by'] for s in saved], ['local', 'local'])
        self.assertEqual(saved[0]['messages'][0]['content'], '연차 며칠이야?')

    def test_outbox_survives_restart(self):
        self.fake.mode = 'down'
        self.ask()
        self.rag.close()
        self.fake.mode = 'ok'
        self.rag = PortalRag(LocalRag(), self.config, os.path.join(self.dir, 'outbox.db'), start_worker=False)
        self.assertEqual(self.rag.flush(), 1)

    def test_delegates_local_features(self):
        self.rag.index_message({'msg_id': 1})
        self.assertEqual(self.local.indexed, [{'msg_id': 1}])
        self.assertEqual(self.rag.list_documents('kim'), [{'doc_id': 1, 'owner': 'kim'}])
        self.rag._llm_failed = True
        self.assertTrue(self.local._llm_failed)
        self.assertIsNone(getattr(self.rag, '_llm', None))   # 없는 속성은 getattr 기본값

    def test_save_off(self):
        rag = PortalRag(LocalRag(), {**self.config, 'save_conversations': False},
                        os.path.join(self.dir, 'o2.db'), start_worker=False)
        try:
            rag.answer('질문', 'kim', [], [], room_id='r1')
            self.assertEqual(rag.outbox.stats()['total'], 0)
        finally:
            rag.close()

    def test_openai_style(self):
        client = PortalClient({**self.config, 'api_style': 'openai', 'model': 'kcs-rag'})
        text, _sources, _conv = client.ask('예산은?', {'username': 'kim', 'name': '김철수'}, [], None)
        self.assertEqual(text, '오픈AI형 답변: 예산은?')
        req = self.fake.requests[-1]
        self.assertEqual((req['path'], req['body']['model']), ('/api/chat/completions', 'kcs-rag'))

    def test_without_local_rag(self):
        rag = PortalRag(None, self.config, os.path.join(self.dir, 'o3.db'), start_worker=False)
        try:
            rag.index_message({'msg_id': 1})            # 자체 RAG 가 없어도 오류 없이 넘어간다
            self.fake.mode = 'down'
            self.assertIn('잠시 후', rag.answer('질문', 'kim', [], [], room_id='r1'))
        finally:
            rag.close()

    def test_worker_sends_in_background(self):
        rag = PortalRag(LocalRag(), {**self.config, 'retry_seconds': 1}, os.path.join(self.dir, 'o4.db'))
        try:
            rag.answer('배경 저장', 'kim', [], [], room_id='r9')
            saved = self.fake.wait_saved(1)
            self.assertEqual(saved[0]['room_id'], 'r9')
        finally:
            rag.close()


class ServerPortalTest(unittest.TestCase):
    """실제 KakaoServer + 가짜 포털: 메신저에서 AI 비서에게 바로 물으면 포털 답이 오고 대화가 포털에 저장된다"""

    @classmethod
    def setUpClass(cls):
        cls.fake = FakePortal()
        cls.tmpdir = tempfile.mkdtemp(prefix='kakao_portal_')
        config = load_config(None, env={'KCS_PORTAL_URL': cls.fake.url, 'KCS_PORTAL_TOKEN': 't'})
        config['save_conversations'] = True
        config['retry_seconds'] = 1
        cls.server = KakaoServer(port=0, db_path=os.path.join(cls.tmpdir, 't.db'), tick_seconds=0.2,
                                 rag_factory=FakeRag, portal=config)
        cls.server.serve_in_thread()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.fake.close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()

    def connect(self):
        c = Client(self.server.port, uname('p'))
        self.clients.append(c)
        return c

    def bot_answer(self, client, rid):
        return client.wait_for('msg_new', lambda m: m['message']['room_id'] == rid
                               and m['message']['sender'] == BOT, timeout=5)['message']

    def test_direct_question_goes_to_portal_and_is_saved(self):
        self.assertIsInstance(self.server.rag, PortalRag)
        a = self.connect()
        a.send({'type': 'room_open_direct', 'target': BOT})
        rid = a.wait_for('room_updated', lambda m: BOT in m['room']['members'])['room']['room_id']
        a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '출장비 규정 알려줘'})
        ans = self.bot_answer(a, rid)
        self.assertTrue(ans['content'].startswith('포털 답변: 출장비 규정 알려줘'))
        saved = self.fake.wait_saved(predicate=lambda s: s['user'] == a.username)
        self.assertEqual(saved[0]['messages'][0]['content'], '출장비 규정 알려줘')
        self.assertEqual(saved[0]['messages'][1]['content'], ans['content'])
        self.assertEqual(saved[0]['room_id'], rid)

    def test_group_mention_goes_to_portal(self):
        a, b = self.connect(), self.connect()
        a.send({'type': 'room_create', 'name': '포털방', 'members': [b.username, BOT]})
        rid = a.wait_for('room_updated', lambda m: m['room']['name'] == '포털방')['room']['room_id']
        b.wait_for('room_updated', lambda m: m['room']['room_id'] == rid)
        a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '@AI 비서 보안 점검 일정은?'})
        ans = self.bot_answer(b, rid)
        self.assertTrue(ans['content'].startswith('포털 답변: 보안 점검 일정은?'))
        saved = self.fake.wait_saved(predicate=lambda s: s['room_id'] == rid)
        self.assertEqual(saved[0]['room_name'], '포털방')


class ChatServerEntryTest(unittest.TestCase):
    def test_chat_server_enables_portal_by_default(self):
        from server.chat import chat_server
        args = chat_server.parse_args([])
        self.assertFalse(args.no_portal)
        self.assertTrue(chat_server.parse_args(['--no-portal']).no_portal)
        self.assertEqual(portal_mod.DEFAULT_BASE_URL, 'https://portal.kcins.co.kr')


if __name__ == '__main__':
    unittest.main()
