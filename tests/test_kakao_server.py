# -*- coding: utf-8 -*-
"""
KakaoServer 프로토콜 통합 테스트 (stdlib unittest).

실행: python3 -m unittest tests.test_kakao_server -v
"""

import base64
import itertools
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import uuid
import time
import unittest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ['KCS_LINK_PREVIEW'] = 'off'   # 테스트는 외부 네트워크에 접속하지 않는다
from server.kakao.server import (KakaoServer, compute_ladder_mapping, fetch_link_preview, preview_host_allowed,  # noqa: E402
                                 generate_ladder_rungs)

import logging  # noqa: E402

logging.getLogger('kakao.server').setLevel(logging.ERROR)  # 의도된 경고(RAG 비활성화 등) 출력 억제

TIMEOUT = 3.0
_counter = itertools.count(1)


def uname(prefix='u'):
    """테스트 간 간섭을 막기 위한 고유 사용자 이름."""
    return f'{prefix}{next(_counter)}_{os.getpid() % 1000}'


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S')


class Client:
    """NDJSON 테스트 클라이언트. 일치하지 않은 메시지는 버퍼에 보관한다."""

    def __init__(self, port, username=None, join=True):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=TIMEOUT)
        self.sock.settimeout(None)
        self.buffer = []
        self.cond = threading.Condition()
        self.closed = False
        self.username = username
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.welcome = None
        if join and username:
            self.send({'type': 'join', 'username': username})
            self.welcome = self.wait_for('welcome')

    def _read_loop(self):
        data = b''
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                data += chunk
                while b'\n' in data:
                    line, data = data.split(b'\n', 1)
                    if line.strip():
                        msg = json.loads(line.decode('utf-8'))
                        with self.cond:
                            self.buffer.append(msg)
                            self.cond.notify_all()
        except OSError:
            pass
        finally:
            with self.cond:
                self.closed = True
                self.cond.notify_all()

    def send(self, payload):
        self.sock.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))

    def send_raw(self, data):
        self.sock.sendall(data)

    def wait_for(self, mtype, predicate=None, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for i, msg in enumerate(self.buffer):
                    if msg.get('type') == mtype and (predicate is None or predicate(msg)):
                        return self.buffer.pop(i)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    types = [m.get('type') for m in self.buffer]
                    raise AssertionError(f'{self.username}: {mtype} 수신 대기 시간 초과 (버퍼: {types})')
                self.cond.wait(remaining)

    def request(self, payload, mtype, predicate=None, timeout=TIMEOUT):
        self.send(payload)
        return self.wait_for(mtype, predicate, timeout)

    def expect_error(self, payload, timeout=TIMEOUT):
        req_id = f'r{next(_counter)}'
        self.send({**payload, 'req_id': req_id})
        err = self.wait_for('error', lambda m: m.get('req_id') == req_id, timeout)
        assert err['req_type'] == payload['type'], err
        assert isinstance(err['message'], str) and err['message'], err
        return err

    def assert_no(self, mtype, predicate=None, wait=0.3):
        time.sleep(wait)
        with self.cond:
            for msg in self.buffer:
                if msg.get('type') == mtype and (predicate is None or predicate(msg)):
                    raise AssertionError(f'예상치 못한 {mtype}: {msg}')

    def clear(self):
        with self.cond:
            self.buffer.clear()

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class ServerTestBase(unittest.TestCase):
    SERVER_KWARGS = {}   # 하위 클래스에서 서버 옵션(짧은 만료 시간 등) 지정

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix='kakao_test_')
        cls.db_path = os.path.join(cls.tmpdir, 't.db')
        cls.server = KakaoServer(port=0, db_path=cls.db_path, tick_seconds=0.2, rag_factory=FakeRag,
                                 **cls.SERVER_KWARGS)
        cls.server.serve_in_thread()
        cls.rag = cls.server.rag
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()

    def connect(self, username=None, join=True):
        c = Client(self.port, username or uname(), join=join)
        self.clients.append(c)
        return c

    def register(self, name=None, password='pw-1234', company='KCS 홀딩스', email=None, employee_no=None, **extra):
        """처음 사용 등록(명세 §4.1) → (client, welcome)"""
        c = self.connect(name or uname('reg'), join=False)
        w = c.request({'type': 'register', 'company': company, 'name': c.username,
                       'employee_no': employee_no or f'E{uuid.uuid4().hex[:10]}',
                       'email': email or f'{c.username}@kcs.test', 'password': password, **extra}, 'welcome')
        return c, w

    def group(self, owner, others, name='테스트방'):
        """그룹 방 생성 후 모든 참여자가 room_updated 를 받을 때까지 대기. room 반환."""
        owner.send({'type': 'room_create', 'name': name, 'members': [o.username for o in others]})
        room = owner.wait_for('room_updated', lambda m: m['room']['type'] == 'group'
                              and m['room']['name'] == name)['room']
        for o in others:
            o.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])
        for c in [owner] + others:
            c.wait_for('msg_new', lambda m: m['message']['room_id'] == room['room_id']
                       and m['message']['kind'] == 'system')
        return room

    def send_text(self, client, room_id, content, **extra):
        tmp = f'tmp{next(_counter)}'
        client.send({'type': 'send', 'room_id': room_id, 'kind': 'text', 'content': content,
                     'client_tmp_id': tmp, **extra})
        return client.wait_for('msg_new', lambda m: m['client_tmp_id'] == tmp)['message']


MESSAGE_KEYS = {'msg_id', 'room_id', 'sender', 'kind', 'content', 'timestamp', 'reply_to', 'mentions',
                'reactions', 'deleted', 'attachment', 'event_id', 'poll', 'edited', 'edited_at', 'silent',
                'link_preview', 'call_id', 'ladder', 'post_id', 'thread_root', 'thread_count', 'thread_last_at'}
ROOM_KEYS = {'room_id', 'type', 'name', 'members', 'created_at', 'notice', 'last_message',
             'read_pointers', 'unread_count', 'description', 'tags', 'nicknames', 'owner'}
USER_KEYS = {'username', 'display_name', 'status_message', 'birthday', 'online', 'avatar_id', 'presence',
             'company', 'department', 'title', 'is_bot', 'org_path', 'account', 'employee_no', 'extension', 'mobile', 'concurrent', 'email',
             'public_key', 'is_admin', 'admin_company', 'deputies'}
BOT = 'AI 비서'
MB = 1024 * 1024
PNG_1PX = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==')


class FakeRag:
    """결정적인 테스트용 RagService 대역."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.indexed = []
        self.questions = []
        self.docs = {}
        self.next_id = 1

    def index_message(self, message):
        with self.lock:
            self.indexed.append(message)

    def add_document(self, owner, name, data, mime):
        if not name.endswith(('.txt', '.md')):
            raise ValueError('지원하지 않는 파일 형식입니다.')
        if not data.strip():
            raise ValueError('빈 문서입니다.')
        with self.lock:
            doc = {'doc_id': self.next_id, 'name': name, 'owner': owner, 'size': len(data),
                   'chunks': max(1, len(data) // 10), 'created_at': '2030-01-01T00:00:00'}
            self.docs[self.next_id] = doc
            self.next_id += 1
            return dict(doc)

    def list_documents(self, owner):
        with self.lock:
            return [dict(d) for d in self.docs.values() if d['owner'] in (owner, '')]

    def delete_document(self, doc_id, owner):
        with self.lock:
            doc = self.docs.get(doc_id)
            if doc is None or doc['owner'] != owner:
                return False
            del self.docs[doc_id]
            return True

    def answer(self, question, asker, room_ids, history):
        with self.lock:
            self.questions.append((question, asker, list(room_ids), list(history)))
        return f'답변: {question}'
EVENT_KEYS = {'event_id', 'room_id', 'creator', 'title', 'description', 'location', 'start', 'end',
              'all_day', 'remind_minutes', 'color', 'attendees', 'created_at'}


class TestConnectionAndRooms(ServerTestBase):
    def test_join_welcome_shape(self):
        c = self.connect()
        w = c.welcome
        self.assertEqual(set(w['me']), USER_KEYS)
        self.assertEqual(w['me']['username'], c.username)
        self.assertTrue(w['me']['online'])
        names = {u['username'] for u in w['users']}
        self.assertTrue({'홍길동', '김철수', '이영희', '박민수', '최지우', '윤서준', '강민지', '정하늘', BOT} <= names)
        users = {u['username']: u for u in w['users']}
        self.assertEqual((users['윤서준']['company'], users['윤서준']['department'], users['윤서준']['title']),
                         ('KCS 홀딩스', '재무팀', '차장'))
        self.assertEqual(users['홍길동']['presence'], 'offline')
        bot = users[BOT]
        self.assertTrue(bot['is_bot'] and bot['online'])
        self.assertEqual((bot['presence'], bot['company']), ('online', 'KCS AI'))
        self.assertEqual(w['me']['company'], '게스트')
        self.assertEqual(w['me']['presence'], 'online')
        self.assertIsNone(w['me']['avatar_id'])
        self.assertTrue(any(u['birthday'] for u in w['users']))
        self.assertEqual(w['rooms'], [])
        legacy = c.wait_for('all_users_list')
        self.assertIn(c.username, legacy['users'])
        self.assertIn(c.username, legacy['online_users'])
        self.assertIn(BOT, legacy['online_users'])
        c.expect_error({'type': 'join', 'username': BOT})

    def test_join_notifies_others(self):
        a = self.connect()
        b_name = uname()
        b = self.connect(b_name)
        upd = a.wait_for('user_updated', lambda m: m['user']['username'] == b_name)
        self.assertEqual(set(upd['user']), USER_KEYS)
        a.wait_for('user_status_changed', lambda m: m['username'] == b_name and m['status'] == 'online')
        b.close()
        a.wait_for('user_status_changed', lambda m: m['username'] == b_name and m['status'] == 'offline')
        u = a.wait_for('user_updated', lambda m: m['user']['username'] == b_name
                       and m['user']['presence'] == 'offline')['user']
        self.assertFalse(u['online'])

    def test_req_id_echo_and_unknown_type(self):
        c = self.connect()
        r = c.request({'type': 'todo_list', 'req_id': 42}, 'todo_list')
        self.assertEqual(r['req_id'], 42)
        c.expect_error({'type': 'no_such_type'})
        # 잘못된 JSON 줄도 연결을 끊지 않는다
        c.send_raw(b'{not json\n')
        c.wait_for('error')
        c.request({'type': 'todo_list'}, 'todo_list')

    def test_request_before_join(self):
        c = self.connect(join=False)
        c.send({'type': 'todo_list', 'req_id': 1})
        err = c.wait_for('error')
        self.assertEqual(err['req_type'], 'todo_list')

    def test_legacy_unframed_join_and_message(self):
        a = self.connect()
        legacy_name = uname('legacy')
        b = self.connect(join=False)
        b.username = legacy_name
        # 구버전: 줄바꿈 없이 연속 전송
        b.send_raw(json.dumps({'type': 'join', 'username': legacy_name}).encode('utf-8'))
        b.wait_for('welcome')
        lst = b.wait_for('all_users_list')
        self.assertIn(legacy_name, lst['online_users'])
        a.wait_for('user_status_changed', lambda m: m['username'] == legacy_name)
        b.send_raw(json.dumps({'type': 'message', 'content': '안녕'}, ensure_ascii=False).encode('utf-8')
                   + json.dumps({'type': 'message', 'content': '둘째'}, ensure_ascii=False).encode('utf-8'))
        for client in (a, b):
            m1 = client.wait_for('message', lambda m: m['content'] == '안녕')
            self.assertEqual(m1['sender'], legacy_name)
            self.assertIn('timestamp', m1)
            client.wait_for('message', lambda m: m['content'] == '둘째')

    def test_multiple_connections_same_user(self):
        name = uname()
        c1 = self.connect(name)
        c2 = self.connect(name)
        other = self.connect()
        other.send({'type': 'room_open_direct', 'target': name})
        room = other.wait_for('room_updated')['room']
        c1.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])
        c2.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])
        self.send_text(other, room['room_id'], 'hi')
        c1.wait_for('msg_new')
        c2.wait_for('msg_new')

    def test_direct_and_self_room(self):
        a, b = self.connect(), self.connect()
        a.send({'type': 'room_open_direct', 'target': b.username, 'req_id': 'x'})
        r = a.wait_for('room_updated')
        self.assertEqual(r['req_id'], 'x')
        room = r['room']
        self.assertEqual(set(room), ROOM_KEYS)
        self.assertEqual(room['type'], 'direct')
        self.assertEqual(room['name'], '')
        self.assertEqual(sorted(room['members']), sorted([a.username, b.username]))
        self.assertEqual(room['read_pointers'], {a.username: 0, b.username: 0})
        b.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])
        # 다시 열어도 같은 방
        again = a.request({'type': 'room_open_direct', 'target': b.username}, 'room_updated')['room']
        self.assertEqual(again['room_id'], room['room_id'])
        me = a.request({'type': 'room_open_direct', 'target': a.username}, 'room_updated')['room']
        self.assertEqual(me['type'], 'self')
        self.assertEqual(me['members'], [a.username])
        a.expect_error({'type': 'room_open_direct', 'target': uname('ghost')})

    def test_group_create_invite_rename_leave(self):
        a, b, c, d = self.connect(), self.connect(), self.connect(), self.connect()
        a.send({'type': 'room_create', 'name': '모임', 'members': [b.username, c.username]})
        room = a.wait_for('room_updated')['room']
        self.assertEqual(room['type'], 'group')
        self.assertEqual(room['name'], '모임')
        self.assertEqual(set(room['members']), {a.username, b.username, c.username})
        for cl in (a, b, c):
            sysmsg = cl.wait_for('msg_new', lambda m: m['message']['kind'] == 'system')['message']
            self.assertEqual(sysmsg['content'], f'{a.username}님이 {b.username}, {c.username}님을 초대했습니다.')
            self.assertEqual(sysmsg['sender'], '')
        b.wait_for('room_updated')

        a.send({'type': 'room_invite', 'room_id': room['room_id'], 'usernames': [d.username]})
        upd = d.wait_for('room_updated')['room']
        self.assertIn(d.username, upd['members'])
        sysmsg = d.wait_for('msg_new')['message']
        self.assertEqual(sysmsg['content'], f'{a.username}님이 {d.username}님을 초대했습니다.')
        # 초대된 사람은 이전 메시지를 읽은 것으로 시작(안 읽은 수 0)
        self.assertEqual(upd['unread_count'], 0)
        b.wait_for('room_updated', lambda m: d.username in m['room']['members'])

        b.send({'type': 'room_rename', 'room_id': room['room_id'], 'name': '새 이름'})
        for cl in (a, b, c, d):
            cl.wait_for('room_updated', lambda m: m['room']['name'] == '새 이름')

        c.send({'type': 'room_leave', 'room_id': room['room_id']})
        self.assertEqual(c.wait_for('room_removed')['room_id'], room['room_id'])
        upd = a.wait_for('room_updated', lambda m: c.username not in m['room']['members'])['room']
        self.assertNotIn(c.username, upd['read_pointers'])
        a.wait_for('msg_new', lambda m: m['message']['content'] == f'{c.username}님이 나갔습니다.')
        c.expect_error({'type': 'history', 'room_id': room['room_id']})
        c.expect_error({'type': 'send', 'room_id': room['room_id'], 'kind': 'text', 'content': 'x'})

        # direct 방 초대 불가
        direct = a.request({'type': 'room_open_direct', 'target': b.username}, 'room_updated',
                           lambda m: m['room']['type'] == 'direct')['room']
        a.expect_error({'type': 'room_invite', 'room_id': direct['room_id'], 'usernames': [c.username]})
        a.expect_error({'type': 'room_create', 'name': 'x', 'members': []})


class LoginTests(ServerTestBase):
    """아이디/비밀번호 로그인 (명세 §4.1)"""

    def join(self, login_id, password=None):
        c = self.connect(login_id, join=False)
        payload = {'type': 'join', 'username': login_id}
        if password is not None:
            payload['password'] = password
        return c, payload

    def test_first_login_registers_password_then_checks_it(self):
        name = uname('pw')
        c, w = self.register(name, 'first-pass', company='KCS 물류', email=f'{name}@KCS.test')
        self.assertEqual(w['me']['username'], name)
        self.assertEqual((w['me']['company'], w['me']['email']), ('KCS 물류', f'{name}@KCS.test'))
        self.assertEqual(set(w['me']), USER_KEYS)                       # 해시는 노출하지 않는다
        stored = self.server.store.password_hash(name)
        self.assertTrue(stored.startswith('pbkdf2_sha256$') and 'first-pass' not in stored)
        c.close()
        # 다음부터는 아이디(이름 또는 이메일, 대소문자 무시)/비밀번호만
        by_email, payload = self.join(f'{name}@kcs.TEST', 'first-pass')
        self.assertEqual(by_email.request(payload, 'welcome')['me']['username'], name)
        bad, payload = self.join(name, 'wrong-pass')
        err = bad.expect_error(payload)
        self.assertEqual(err['message'], '아이디 또는 비밀번호가 올바르지 않습니다.')
        bad.assert_no('welcome')
        # 같은 연결에서 다시 시도할 수 있다
        payload['password'] = 'first-pass'
        self.assertEqual(bad.request(payload, 'welcome')['me']['username'], name)

    def test_register_with_employee_no_then_login_by_employee_no(self):
        name = uname('emp')
        emp = f'K{uuid.uuid4().hex[:8]}'
        c, w = self.register(name, 'emp-pass', email=f'{name}@kcs.test', employee_no=emp)
        self.assertEqual(w['me']['employee_no'], emp)
        c.close()
        # 다음부터는 사번(대소문자 무시) + 비밀번호만
        by_emp, payload = self.join(emp.lower(), 'emp-pass')
        self.assertEqual(by_emp.request(payload, 'welcome')['me']['username'], name)
        bad, payload = self.join(emp, 'wrong-pass')
        self.assertEqual(bad.expect_error(payload)['message'], '아이디 또는 비밀번호가 올바르지 않습니다.')
        # 같은 사번으로 다른 사람이 등록할 수 없다 (대소문자 무시)
        other = self.connect(uname('emp'), join=False)
        err = other.expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': other.username,
                                  'employee_no': emp.lower(), 'email': f'{other.username}@kcs.test',
                                  'password': 'pw-1234'})
        self.assertEqual(err['message'], '이미 등록된 사번입니다. 사번과 비밀번호로 로그인해 주세요.')
        self.assertIsNone(self.server.store.resolve_login(other.username))
        # 사번이 다른 사람의 이름·사내 계정과 같아도 안 된다 (로그인 아이디가 겹친다)
        err = other.expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': other.username,
                                  'employee_no': 'kmkim' if self.server.store.resolve_login('kmkim') else name,
                                  'email': f'{other.username}@kcs.test', 'password': 'pw-1234'})
        self.assertIn('이미 등록된 사번', err['message'])
        # 등록 안 된 아이디로 로그인하면 처음 사용 등록 안내 (소속·이름·이메일·비밀번호 — 사번 칸은 없다)
        new, payload = self.join(f'N{uuid.uuid4().hex[:8]}', 'some-pass')
        message = new.expect_error(payload)['message']
        self.assertIn('소속 회사·이름·이메일·비밀번호', message)
        self.assertNotIn('사번', message)

    def test_register_without_employee_no_then_login_by_email(self):
        """2026-10-14: 등록 창에 사번 칸이 없다 → employee_no 없이 등록하고 이메일로 로그인"""
        name = uname('mail')
        email = f'{name}@kcs.test'
        c = self.connect(name, join=False)
        w = c.request({'type': 'register', 'company': 'KCS 홀딩스', 'name': name, 'email': email,
                       'password': 'mail-pass'}, 'welcome')
        self.assertEqual((w['me']['username'], w['me']['employee_no'], w['me']['email']), (name, '', email))
        c.close()
        by_mail, payload = self.join(email.upper(), 'mail-pass')
        self.assertEqual(by_mail.request(payload, 'welcome')['me']['username'], name)
        # 조직도(관리자 등록)에 이메일이 적힌 미등록 계정은 그 이메일이 맞아야 가져갈 수 있다
        member = uname('org')
        self.server.store.member_create(member, {'company': 'KCS 홀딩스', 'email': f'{member}@kcs.test'})
        other = self.connect(member, join=False)
        err = other.expect_error({'type': 'register', 'company': 'KCS 홀딩스', 'name': member,
                                  'email': 'someone-else@kcs.test', 'password': 'pw-1234'})
        self.assertEqual(err['message'], self.server.store.REGISTER_WRONG_EMAIL)
        self.assertIsNone(self.server.store.password_hash(member))
        w = other.request({'type': 'register', 'company': 'KCS 홀딩스', 'name': member,
                           'email': f'{member}@KCS.test', 'password': 'pw-1234'}, 'welcome')   # 대소문자 무시
        self.assertEqual(w['me']['username'], member)

    def test_passwordless_join_blocked_once_password_set(self):
        name = uname('pw')
        self.register(name, 'abcd')
        legacy, payload = self.join(name)
        self.assertEqual(legacy.expect_error(payload)['message'], '비밀번호를 입력해 주세요.')
        # 비밀번호가 없는 계정은 예전처럼 이름만으로 로그인된다
        self.connect(uname('nopw'))

    def test_login_with_org_account(self):
        name = uname('acct')
        self.connect(name).close()
        store = self.server.store
        with store._lock:
            store._db.execute('UPDATE users SET account=? WHERE username=?', (f'ID{name}', name))
            store._db.commit()
        c, payload = self.join(f'id{name}'.lower(), 'acct-pass')      # 대소문자 무시
        self.assertIn('처음 사용 등록', c.expect_error(payload)['message'])  # 비밀번호 등록 전
        self.register(name, 'acct-pass', company='KCS 커머스')          # 게스트 → 고른 회사로
        me = c.request(payload, 'welcome')['me']
        self.assertEqual((me['username'], me['company']), (name, 'KCS 커머스'))

    def test_remember_token_login_and_revoke(self):
        name = uname('tok')
        _c, w = self.register(name, 'tok-pass', remember=True)
        token = w['login_token']
        self.assertGreaterEqual(len(token), 40)
        self.assertNotIn(token, json.dumps(w['me']))
        t, _ = self.join(name)
        self.assertEqual(t.request({'type': 'join', 'username': name, 'token': token}, 'welcome')['me']['username'], name)
        other, _ = self.join(uname('tok'))
        err = other.expect_error({'type': 'join', 'username': uname('tok'), 'token': token})   # 다른 아이디
        self.assertIn('만료', err['message'])
        t.request({'type': 'login_token_revoke', 'token': token}, 'login_token_revoked')
        again, _ = self.join(name)
        again.expect_error({'type': 'join', 'username': name, 'token': token})
        # 비밀번호 없이 remember 만으로는 토큰이 나오지 않는다
        legacy = self.connect(uname('nopw'), join=False)
        w = legacy.request({'type': 'join', 'username': legacy.username, 'remember': True}, 'welcome')
        self.assertNotIn('login_token', w)

    def test_login_tokens_expire_and_are_capped(self):
        from datetime import datetime, timedelta
        name = uname('tok')
        self.connect(name).close()
        store = self.server.store
        tokens = [store.issue_login_token(name) for _ in range(store.LOGIN_TOKENS_PER_USER + 2)]
        self.assertFalse(store.check_login_token(name, tokens[0]))             # 오래된 것부터 정리
        self.assertTrue(store.check_login_token(name, tokens[-1]))
        old = (datetime.now() - timedelta(days=store.LOGIN_TOKEN_DAYS + 1)).strftime('%Y-%m-%dT%H:%M:%S')
        with store._lock:
            store._db.execute('UPDATE login_tokens SET created_at=? WHERE token_hash=?',
                              (old, store._token_hash(tokens[-1])))
            store._db.commit()
        self.assertFalse(store.check_login_token(name, tokens[-1]))

    def test_join_with_password_needs_registration_first(self):
        c, payload = self.join(uname('new'), 'some-pass')
        err = c.expect_error(payload)
        self.assertEqual(err['req_type'], 'join')
        self.assertIn('처음 사용 등록', err['message'])
        c.assert_no('welcome')
        self.assertFalse(self.server._login_failures.get(f"id:{payload['username'].lower()}"))  # 실패로 세지 않음

    def test_org_companies_before_login(self):
        c = self.connect(join=False)
        companies = c.request({'type': 'org_companies'}, 'org_companies')['companies']
        self.assertTrue({'KCS 홀딩스', 'KCS 물류', 'KCS 커머스'} <= set(companies))
        self.assertNotIn('게스트', companies)
        self.assertNotIn('KCS AI', companies)                            # 봇 소속은 빼기
        c.request({'type': 'join', 'username': c.username}, 'welcome')   # 같은 연결로 이어서 로그인

    def test_register_validation(self):
        c = self.connect(uname('reg'), join=False)
        base = {'type': 'register', 'company': 'KCS 물류', 'name': c.username,
                'employee_no': f'V{uuid.uuid4().hex[:8]}', 'email': f'{c.username}@kcs.test', 'password': 'pw-1234'}
        for change, message in (({'company': ''}, '소속 회사를 선택해 주세요.'),
                                ({'company': '없는회사'}, '소속 회사를 선택해 주세요.'),
                                ({'company': '게스트'}, '소속 회사를 선택해 주세요.'),
                                ({'email': 'no-at-sign'}, '이메일 주소 형식이 올바르지 않습니다.'),
                                ({'password': '12'}, '4~64자'),
                                ({'name': 'a@b'}, '사용할 수 없는 문자'),
                                ({'name': BOT}, '봇'),
                                ({'employee_no': '12 34'}, '사번은 영문·숫자'),     # 보내면 규칙은 지켜야
                                ({'employee_no': 'x' * 21}, '사번은 영문·숫자')):
            err = c.expect_error({**base, **change})
            self.assertEqual(err['req_type'], 'register')
            self.assertIn(message, err['message'])
        self.assertIsNone(self.server.store.resolve_login(c.username))  # 아무것도 만들지 않았다
        c.request(base, 'welcome')
        # 같은 이름 / 같은 이메일로 다시 등록할 수 없다
        again = self.connect(c.username, join=False)
        self.assertIn('이미 등록된 이름', again.expect_error(base)['message'])
        other = self.connect(uname('reg'), join=False)
        err = other.expect_error({**base, 'name': other.username, 'employee_no': f'W{uuid.uuid4().hex[:8]}',
                                  'email': f'{c.username}@KCS.test'})
        self.assertEqual(err['message'], '이미 가입된 이메일입니다.')

    def test_register_existing_org_member_keeps_company(self):
        member = '정하늘'                                               # 시드: KCS 커머스 개발팀
        store = self.server.store
        if store.password_hash(member):
            store.clear_password(member)
        c = self.connect(uname('reg'), join=False)
        err = c.expect_error({'type': 'register', 'company': 'KCS 물류', 'name': member, 'employee_no': 'S20260001',
                              'email': 'sky@kcs.test', 'password': 'pw-1234'})
        self.assertIn('선택한 소속에 같은 이름의 구성원이 없습니다', err['message'])
        w = c.request({'type': 'register', 'company': 'KCS 커머스', 'name': member, 'employee_no': 'S20260001',
                       'email': 'sky@kcs.test', 'password': 'pw-1234'}, 'welcome')
        self.assertEqual((w['me']['company'], w['me']['department']), ('KCS 커머스', '개발팀'))
        self.assertEqual(w['me']['employee_no'], 'S20260001')          # 시드 계정에도 사번 등록
        self.assertEqual(store.resolve_login('s20260001'), member)
        store.clear_password(member)

    def test_password_reset_with_registered_email(self):
        name = uname('rst')
        _c, w = self.register(name, 'old-pass', email=f'{name}@kcs.test', remember=True)
        token = w['login_token']
        c = self.connect(join=False)
        base = {'type': 'password_reset', 'username': name, 'email': f'{name}@KCS.test', 'new_password': 'new-pass'}
        self.assertIn('재설정 코드', c.expect_error(base)['message'])           # 이메일만으로는 바꾸지 못한다
        code, _until = self.server.store.issue_reset_code(name, '관리자')
        base['code'] = code.lower().replace('-', ' ')                          # 대소문자 · 구분 기호 무시
        ok = c.request(base, 'password_reset_ok')                             # 이메일은 대소문자 무시
        self.assertEqual(ok['username'], name)
        c.assert_no('welcome')                                                # 재설정은 로그인하지 않는다
        store = self.server.store
        self.assertTrue(store.check_password('new-pass', store.password_hash(name)))
        self.assertFalse(store.check_login_token(name, token))                # 자동 로그인 토큰 폐기
        login, payload = self.join(name, 'new-pass')
        self.assertEqual(login.request(payload, 'welcome')['me']['username'], name)
        # 이메일·아이디가 틀리면 같은 문구 (계정이 있는지 드러내지 않는다) + 실패 횟수에 센다
        mismatch = '아이디 · 가입한 이메일 · 재설정 코드가 맞지 않습니다. 코드는 관리자에게 받고 30분 동안 한 번만 쓸 수 있습니다.'
        for bad in ({'email': 'other@kcs.test'}, {'username': uname('없는')}, {}):   # {} = 이미 쓴 코드
            err = c.expect_error({**base, **bad})
            self.assertEqual((err['req_type'], err['message']), ('password_reset', mismatch))
        self.assertTrue(store.check_password('new-pass', store.password_hash(name)))   # 그대로
        self.assertTrue(self.server._login_failures.get(f'id:{name.lower()}'))
        self.assertIn('4~64자', c.expect_error({**base, 'new_password': '12'})['message'])

    def test_password_reset_throttled_and_needs_registration(self):
        name = uname('rst')
        self.register(name, 'old-pass', email=f'{name}@kcs.test')
        c = self.connect(join=False)
        base = {'type': 'password_reset', 'username': name, 'email': 'wrong@kcs.test', 'new_password': 'new-pass',
                'code': 'ABCD-EFGH'}
        for _ in range(self.server.LOGIN_MAX_FAILURES['id']):
            c.expect_error(base)
        err = c.expect_error({**base, 'email': f'{name}@kcs.test'})
        self.assertIn('너무 많습니다', err['message'])                           # 맞는 이메일도 잠시 막힘
        self.server._login_failures.clear()
        guest = self.connect(uname('nopw'))                                   # 비밀번호를 등록한 적 없는 계정
        err = c.expect_error({**base, 'username': guest.username})
        self.assertIn('처음 사용 등록', err['message'])

    def test_password_length_and_bot(self):
        c, payload = self.join(uname('pw'), '12')
        self.assertIn('4~64자', c.expect_error(payload)['message'])
        c.expect_error({'type': 'join', 'username': uname('pw'), 'password': 1234})
        c.expect_error({'type': 'join', 'username': BOT, 'password': 'abcd'})


class TestMessaging(ServerTestBase):
    def test_send_read_unread(self):
        a, b = self.connect(), self.connect()
        room = self.group(a, [b])
        rid = room['room_id']
        m = self.send_text(a, rid, '안녕하세요')
        self.assertEqual(set(m), MESSAGE_KEYS)
        self.assertEqual(m['sender'], a.username)
        self.assertEqual(m['kind'], 'text')
        self.assertFalse(m['deleted'])
        self.assertEqual(m['reactions'], {})
        b_new = b.wait_for('msg_new', lambda x: x['message']['msg_id'] == m['msg_id'])
        self.assertEqual(b_new['message']['content'], '안녕하세요')
        for cl in (a, b):
            ru = cl.wait_for('read_update', lambda x: x['username'] == a.username)
            self.assertEqual(ru['last_read_msg_id'], m['msg_id'])
        m2 = self.send_text(a, rid, '두번째')

        # 재접속한 b 의 welcome 에서 unread_count=2
        b2 = self.connect(b.username)
        r = next(x for x in b2.welcome['rooms'] if x['room_id'] == rid)
        self.assertEqual(r['unread_count'], 2)
        self.assertEqual(r['last_message']['msg_id'], m2['msg_id'])
        self.assertEqual(r['read_pointers'][a.username], m2['msg_id'])
        self.assertEqual(r['read_pointers'][b.username], 0)

        b.send({'type': 'read', 'room_id': rid, 'last_msg_id': m2['msg_id']})
        ru = a.wait_for('read_update', lambda x: x['username'] == b.username)
        self.assertEqual(ru['last_read_msg_id'], m2['msg_id'])
        # 감소 불가
        b.send({'type': 'read', 'room_id': rid, 'last_msg_id': m['msg_id']})
        ru = a.wait_for('read_update', lambda x: x['username'] == b.username)
        self.assertEqual(ru['last_read_msg_id'], m2['msg_id'])
        # 방의 최신 메시지보다 큰 값은 최신 값으로 제한
        b.send({'type': 'read', 'room_id': rid, 'last_msg_id': 10 ** 9})
        ru = a.wait_for('read_update', lambda x: x['username'] == b.username)
        self.assertEqual(ru['last_read_msg_id'], m2['msg_id'])
        b3 = self.connect(b.username)
        r = next(x for x in b3.welcome['rooms'] if x['room_id'] == rid)
        self.assertEqual(r['unread_count'], 0)

        hist = b.request({'type': 'history', 'room_id': rid, 'limit': 2}, 'history')
        self.assertEqual([x['msg_id'] for x in hist['messages']], [m['msg_id'], m2['msg_id']])
        self.assertTrue(hist['has_more'])  # 시스템 메시지가 더 있다
        older = b.request({'type': 'history', 'room_id': rid, 'before_msg_id': m['msg_id']}, 'history')
        self.assertEqual(older['messages'][0]['kind'], 'system')
        self.assertFalse(older['has_more'])

    def test_reply_mentions_react_delete(self):
        a, b, outsider = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        orig = self.send_text(a, rid, '원본 메시지')
        reply = self.send_text(b, rid, '답장', reply_to_id=orig['msg_id'],
                               mentions=[a.username, outsider.username])
        self.assertEqual(reply['reply_to'], {'msg_id': orig['msg_id'], 'sender': a.username,
                                             'preview': '원본 메시지'})
        self.assertEqual(reply['mentions'], [a.username])
        b.expect_error({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': 'x', 'reply_to_id': 10 ** 9})

        a.send({'type': 'react', 'msg_id': reply['msg_id'], 'emoji': '👍'})
        upd = b.wait_for('msg_updated', lambda m: m['message']['reactions'])['message']
        self.assertEqual(upd['reactions'], {'👍': [a.username]})
        a.send({'type': 'react', 'msg_id': reply['msg_id'], 'emoji': '👍'})
        upd = b.wait_for('msg_updated', lambda m: m['message']['msg_id'] == reply['msg_id'])['message']
        self.assertEqual(upd['reactions'], {})
        outsider.expect_error({'type': 'react', 'msg_id': reply['msg_id'], 'emoji': '👍'})

        a.expect_error({'type': 'delete', 'msg_id': reply['msg_id']})  # 남의 메시지
        b.send({'type': 'delete', 'msg_id': reply['msg_id']})
        upd = a.wait_for('msg_updated', lambda m: m['message']['deleted'])['message']
        self.assertEqual(upd['content'], '메시지가 삭제되었습니다')
        self.assertIsNone(upd['attachment'])
        self.assertIsNone(upd['poll'])

    def test_notice_and_search(self):
        a, b, outsider = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        token = f'검색토큰{next(_counter)}'
        m1 = self.send_text(a, rid, f'첫 {token} 메시지')
        m2 = self.send_text(b, rid, f'두번째 {token}')
        self.send_text(a, rid, '무관한 내용')
        deleted = self.send_text(a, rid, f'삭제될 {token}')
        a.send({'type': 'delete', 'msg_id': deleted['msg_id']})
        a.wait_for('msg_updated')

        a.send({'type': 'notice_set', 'room_id': rid, 'msg_id': m1['msg_id']})
        upd = b.wait_for('room_updated', lambda m: m['room']['notice'] is not None)['room']
        self.assertEqual(upd['notice']['msg_id'], m1['msg_id'])
        b.wait_for('msg_new', lambda m: m['message']['content'] == f'{a.username}님이 공지를 등록했습니다.')
        a.send({'type': 'notice_set', 'room_id': rid, 'msg_id': None})
        upd = b.wait_for('room_updated', lambda m: m['room']['notice'] is None)['room']
        self.assertIsNone(upd['notice'])
        b.assert_no('msg_new', lambda m: m['message']['kind'] == 'system')
        outsider.expect_error({'type': 'notice_set', 'room_id': rid, 'msg_id': m1['msg_id']})

        res = b.request({'type': 'search', 'query': token}, 'search_result')
        self.assertEqual(res['query'], token)
        self.assertEqual([m['msg_id'] for m in res['messages']], [m2['msg_id'], m1['msg_id']])
        res = b.request({'type': 'search', 'query': token, 'room_id': rid}, 'search_result')
        self.assertEqual(res['room_id'], rid)
        self.assertEqual(len(res['messages']), 2)
        res = outsider.request({'type': 'search', 'query': token}, 'search_result')
        self.assertEqual(res['messages'], [])
        outsider.expect_error({'type': 'search', 'query': token, 'room_id': rid})
        # LIKE 와일드카드는 문자 그대로 취급
        res = b.request({'type': 'search', 'query': '%'}, 'search_result')
        self.assertEqual(res['messages'], [])

    def test_poll(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        a.send({'type': 'poll_create', 'room_id': rid, 'question': '점심 메뉴?', 'options': ['짜장', '짬뽕'],
                'multiple': False})
        msg = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'poll')['message']
        poll = msg['poll']
        self.assertEqual(poll['question'], '점심 메뉴?')
        self.assertFalse(poll['multiple'])
        self.assertFalse(poll['closed'])
        self.assertEqual(poll['options'], [{'text': '짜장', 'voters': []}, {'text': '짬뽕', 'voters': []}])
        pid = poll['poll_id']
        a.expect_error({'type': 'poll_create', 'room_id': rid, 'question': 'q', 'options': ['하나'],
                        'multiple': False})

        b.send({'type': 'poll_vote', 'poll_id': pid, 'option_indexes': [1]})
        upd = a.wait_for('msg_updated', lambda m: m['message']['poll'] is not None)['message']
        self.assertEqual(upd['poll']['options'][1]['voters'], [b.username])
        b.expect_error({'type': 'poll_vote', 'poll_id': pid, 'option_indexes': [0, 1]})  # 단일 선택
        b.expect_error({'type': 'poll_vote', 'poll_id': pid, 'option_indexes': [5]})
        b.send({'type': 'poll_vote', 'poll_id': pid, 'option_indexes': [0]})  # 교체
        upd = a.wait_for('msg_updated')['message']
        self.assertEqual(upd['poll']['options'][0]['voters'], [b.username])
        self.assertEqual(upd['poll']['options'][1]['voters'], [])
        b.send({'type': 'poll_vote', 'poll_id': pid, 'option_indexes': []})  # 취소
        upd = a.wait_for('msg_updated')['message']
        self.assertTrue(all(o['voters'] == [] for o in upd['poll']['options']))

        b.expect_error({'type': 'poll_close', 'poll_id': pid})  # 생성자만
        a.send({'type': 'poll_close', 'poll_id': pid})
        upd = b.wait_for('msg_updated', lambda m: m['message']['poll']['closed'])['message']
        self.assertTrue(upd['poll']['closed'])
        b.expect_error({'type': 'poll_vote', 'poll_id': pid, 'option_indexes': [0]})

        a.send({'type': 'poll_create', 'room_id': rid, 'question': '복수', 'options': ['a', 'b', 'c'],
                'multiple': True})
        pid2 = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'poll')['message']['poll']['poll_id']
        b.send({'type': 'poll_vote', 'poll_id': pid2, 'option_indexes': [0, 2]})
        upd = a.wait_for('msg_updated', lambda m: m['message']['poll']['poll_id'] == pid2)['message']
        self.assertEqual([o['voters'] for o in upd['poll']['options']], [[b.username], [], [b.username]])

    def test_attachments(self):
        a, b, outsider = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        raw = bytes(range(256)) * 40
        a.send({'type': 'send', 'room_id': rid, 'kind': 'image', 'content': '',
                'attachment': {'name': 'pic.png', 'mime': 'image/png',
                               'data_b64': base64.b64encode(raw).decode()}, 'client_tmp_id': 'img1'})
        msg = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'image')['message']
        att = msg['attachment']
        self.assertEqual(set(att), {'attachment_id', 'name', 'mime', 'size', 'expires_at', 'expired'})
        self.assertFalse(att['expired'])
        self.assertEqual((att['name'], att['mime'], att['size']), ('pic.png', 'image/png', len(raw)))
        got = b.request({'type': 'attachment_get', 'attachment_id': att['attachment_id']}, 'attachment_data')
        self.assertEqual(got['attachment_id'], att['attachment_id'])
        self.assertEqual(got['name'], 'pic.png')
        self.assertEqual(base64.b64decode(got['data_b64']), raw)
        outsider.expect_error({'type': 'attachment_get', 'attachment_id': att['attachment_id']})

        big = base64.b64encode(b'\0' * (10 * MB + 1)).decode()
        err = a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '',
                              'attachment': {'name': 'big.bin', 'mime': 'application/octet-stream',
                                             'data_b64': big}}, timeout=10)
        self.assertIn('10MB', err['message'])
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '',
                        'attachment': {'name': 'x', 'mime': 'a/b', 'data_b64': '###'}})
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': ''})
        # 정확히 10MB 는 허용, 연결은 계속 유지
        ok = base64.b64encode(b'\1' * (10 * MB)).decode()
        a.send({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': 'f',
                'attachment': {'name': 'ok.bin', 'mime': 'application/octet-stream', 'data_b64': ok}})
        m = a.wait_for('msg_new', lambda x: x['message']['kind'] == 'file', timeout=10)['message']
        self.assertEqual(m['attachment']['size'], 10 * MB)

    def test_scheduled_message(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        send_at = iso(datetime.now() + timedelta(seconds=2))
        a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '예약됨', 'send_at': send_at,
                'client_tmp_id': 'sch1', 'req_id': 'q'})
        ok = a.wait_for('scheduled_ok')
        self.assertEqual((ok['room_id'], ok['send_at'], ok['content'], ok['req_id']),
                         (rid, send_at, '예약됨', 'q'))
        self.assertIsInstance(ok['scheduled_id'], int)
        b.assert_no('msg_new', lambda m: m['message']['content'] == '예약됨', wait=0.5)
        got = b.wait_for('msg_new', lambda m: m['message']['content'] == '예약됨', timeout=6)
        self.assertEqual(got['message']['sender'], a.username)
        self.assertEqual(got['client_tmp_id'], 'sch1')
        a.wait_for('msg_new', lambda m: m['message']['content'] == '예약됨')
        b.assert_no('msg_new', lambda m: m['message']['content'] == '예약됨', wait=0.6)  # 1회만

    def test_non_member_errors(self):
        a, b, outsider = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        m = self.send_text(a, rid, 'secret')
        outsider.expect_error({'type': 'history', 'room_id': rid})
        outsider.expect_error({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': 'x'})
        outsider.expect_error({'type': 'read', 'room_id': rid, 'last_msg_id': m['msg_id']})
        outsider.expect_error({'type': 'room_invite', 'room_id': rid, 'usernames': [outsider.username]})
        outsider.expect_error({'type': 'room_rename', 'room_id': rid, 'name': 'hack'})
        outsider.expect_error({'type': 'room_leave', 'room_id': rid})
        outsider.expect_error({'type': 'delete', 'msg_id': m['msg_id']})
        outsider.expect_error({'type': 'poll_create', 'room_id': rid, 'question': 'q', 'options': ['a', 'b'],
                               'multiple': False})
        outsider.expect_error({'type': 'event_create', 'room_id': rid, 'title': 't',
                               'start': iso(datetime.now()), 'end': iso(datetime.now())})
        outsider.expect_error({'type': 'history', 'room_id': 'nope'})
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'bogus', 'content': 'x'})
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '   '})
        b.assert_no('msg_new', lambda x: x['message']['sender'] == outsider.username)


class TestCalendarTodoProfile(ServerTestBase):
    def test_event_in_room_rsvp_update_delete(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b, c])['room_id']
        start = datetime.now() + timedelta(days=2)
        b.send({'type': 'event_create', 'room_id': rid, 'title': '팀 회식', 'start': iso(start),
                'end': iso(start + timedelta(hours=2)), 'location': '강남', 'remind_minutes': 30,
                'color': '#ff0000', 'req_id': 7})
        msg = a.wait_for('msg_new', lambda m: m['message']['kind'] == 'event')['message']
        self.assertEqual(msg['content'], '팀 회식')
        self.assertEqual(msg['sender'], b.username)
        ev = b.wait_for('event_updated')
        self.assertEqual(ev['req_id'], 7)
        event = ev['event']
        self.assertEqual(set(event), EVENT_KEYS)
        self.assertEqual(msg['event_id'], event['event_id'])
        self.assertEqual(event['room_id'], rid)
        self.assertEqual(event['attendees'], {b.username: 'yes', a.username: 'pending', c.username: 'pending'})
        self.assertEqual(event['color'], '#FF0000')
        self.assertFalse(event['all_day'])
        self.assertEqual(event['start'], iso(start))
        a.wait_for('event_updated')
        c.wait_for('event_updated')

        a.send({'type': 'event_rsvp', 'event_id': event['event_id'], 'response': 'no'})
        upd = b.wait_for('event_updated', lambda m: m['event']['attendees'][a.username] == 'no')['event']
        self.assertEqual(upd['attendees'][c.username], 'pending')
        a.expect_error({'type': 'event_rsvp', 'event_id': event['event_id'], 'response': 'pending'})
        outsider = self.connect()
        outsider.expect_error({'type': 'event_rsvp', 'event_id': event['event_id'], 'response': 'yes'})

        a.expect_error({'type': 'event_update', 'event_id': event['event_id'], 'title': '해킹'})
        b.send({'type': 'event_update', 'event_id': event['event_id'], 'title': '팀 회식(변경)'})
        upd = c.wait_for('event_updated', lambda m: m['event']['title'] == '팀 회식(변경)')['event']
        self.assertEqual(upd['location'], '강남')
        b.expect_error({'type': 'event_update', 'event_id': event['event_id'],
                        'end': iso(start - timedelta(days=1))})

        c.expect_error({'type': 'event_delete', 'event_id': event['event_id']})
        b.send({'type': 'event_delete', 'event_id': event['event_id']})
        for cl in (a, b, c):
            self.assertEqual(cl.wait_for('event_deleted')['event_id'], event['event_id'])
        b.expect_error({'type': 'event_update', 'event_id': event['event_id'], 'title': 'x'})

    def test_event_list_range_and_attendees(self):
        a, b = self.connect(), self.connect()
        base = datetime(2030, 3, 10, 9, 0, 0)

        def create(client, title, start, hours=1, **extra):
            client.send({'type': 'event_create', 'title': title, 'start': iso(start),
                         'end': iso(start + timedelta(hours=hours)), **extra})
            return client.wait_for('event_updated', lambda m: m['event']['title'] == title)['event']

        e1 = create(a, '3월 일정', base)
        create(a, '4월 일정', base + timedelta(days=30))
        e3 = create(b, '초대 일정', base + timedelta(days=1), attendees=[a.username])
        self.assertIsNone(e3['room_id'])
        a.wait_for('event_updated', lambda m: m['event']['event_id'] == e3['event_id'])
        e4 = create(a, '걸친 일정', datetime(2030, 2, 28, 22), hours=6)
        allday = create(a, '종일', datetime(2030, 3, 20), hours=0, all_day=True)
        self.assertTrue(allday['all_day'])

        res = a.request({'type': 'event_list', 'start': '2030-03-01T00:00:00', 'end': '2030-03-31T23:59:59'},
                        'event_list')
        self.assertEqual(res['start'], '2030-03-01T00:00:00')
        ids = [e['event_id'] for e in res['events']]
        self.assertEqual(set(ids), {e1['event_id'], e3['event_id'], e4['event_id'], allday['event_id']})
        res_b = b.request({'type': 'event_list', 'start': '2030-03-01T00:00:00', 'end': '2030-03-31T23:59:59'},
                          'event_list')
        self.assertEqual([e['event_id'] for e in res_b['events']], [e3['event_id']])
        a.expect_error({'type': 'event_list', 'start': 'bad', 'end': '2030-01-01'})
        a.expect_error({'type': 'event_create', 'title': 'x', 'start': iso(base), 'end': iso(base),
                        'color': 'red'})
        a.expect_error({'type': 'event_create', 'title': 'x', 'start': iso(base), 'end': iso(base),
                        'attendees': [uname('ghost')]})

    def test_event_reminder(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b, c])['room_id']
        far = datetime.now() + timedelta(days=1)
        # 1일 뒤 일정 + 60분 전 알림 → 지금은 오지 않아야 함
        a.send({'type': 'event_create', 'room_id': rid, 'title': '나중', 'start': iso(far),
                'end': iso(far + timedelta(hours=1)), 'remind_minutes': 60})
        a.wait_for('event_updated', lambda m: m['event']['title'] == '나중')
        c.send({'type': 'event_create', 'room_id': rid, 'title': '곧 시작', 'start': '__placeholder__',
                'end': '__placeholder__'})
        c.wait_for('error')  # 잘못된 시각
        start = datetime.now() + timedelta(minutes=1)
        a.send({'type': 'event_create', 'room_id': rid, 'title': '곧 시작', 'start': iso(start),
                'end': iso(start + timedelta(hours=1)), 'remind_minutes': 1})
        ev = a.wait_for('event_updated', lambda m: m['event']['title'] == '곧 시작')['event']
        c.wait_for('event_updated', lambda m: m['event']['event_id'] == ev['event_id'])
        c.send({'type': 'event_rsvp', 'event_id': ev['event_id'], 'response': 'no'})
        c.wait_for('event_updated', lambda m: m['event']['attendees'][c.username] == 'no')
        for cl in (a, b):
            rem = cl.wait_for('event_reminder', lambda m: m['event']['event_id'] == ev['event_id'], timeout=3)
            self.assertEqual(rem['minutes_before'], 1)
            self.assertEqual(rem['event']['title'], '곧 시작')
        # c 는 불참이라 알림이 오면 안 될 수 있음(경쟁 조건 방지: rsvp 이전에 발송된 경우만 허용)
        a.assert_no('event_reminder', lambda m: m['event']['event_id'] == ev['event_id'], wait=0.6)
        a.assert_no('event_reminder', lambda m: m['event']['title'] == '나중', wait=0.1)

        # 시작 시각 변경 → 재무장
        b.clear()
        new_start = datetime.now() + timedelta(seconds=50)
        a.send({'type': 'event_update', 'event_id': ev['event_id'], 'start': iso(new_start),
                'end': iso(new_start + timedelta(hours=1))})
        b.wait_for('event_reminder', lambda m: m['event']['event_id'] == ev['event_id'], timeout=3)

    def test_reminder_respects_no(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        start = datetime.now() + timedelta(minutes=10)
        a.send({'type': 'event_create', 'room_id': rid, 'title': '무알림', 'start': iso(start),
                'end': iso(start + timedelta(hours=1)), 'remind_minutes': 5})
        ev = b.wait_for('event_updated')['event']
        b.send({'type': 'event_rsvp', 'event_id': ev['event_id'], 'response': 'no'})
        b.wait_for('event_updated', lambda m: m['event']['attendees'][b.username] == 'no')
        a.send({'type': 'event_update', 'event_id': ev['event_id'], 'remind_minutes': 10})
        a.wait_for('event_reminder', lambda m: m['event']['event_id'] == ev['event_id'], timeout=3)
        b.assert_no('event_reminder', wait=0.6)

    def test_todos(self):
        a, other = self.connect(), self.connect()
        a.send({'type': 'todo_create', 'title': '장보기', 'due': '2030-01-02', 'req_id': 1})
        t = a.wait_for('todo_updated')
        self.assertEqual(t['req_id'], 1)
        todo = t['todo']
        self.assertEqual(set(todo), {'todo_id', 'owner', 'title', 'due', 'done', 'created_at'})
        self.assertEqual((todo['owner'], todo['title'], todo['due'], todo['done']),
                         (a.username, '장보기', '2030-01-02', False))
        t2 = a.request({'type': 'todo_create', 'title': '운동'}, 'todo_updated')['todo']
        self.assertIsNone(t2['due'])
        a.expect_error({'type': 'todo_create', 'title': 'x', 'due': '2030-13-40'})
        upd = a.request({'type': 'todo_update', 'todo_id': todo['todo_id'], 'done': True, 'due': None},
                        'todo_updated')['todo']
        self.assertTrue(upd['done'])
        self.assertIsNone(upd['due'])
        self.assertEqual(upd['title'], '장보기')
        other.expect_error({'type': 'todo_update', 'todo_id': todo['todo_id'], 'done': False})
        other.expect_error({'type': 'todo_delete', 'todo_id': todo['todo_id']})
        lst = a.request({'type': 'todo_list'}, 'todo_list')['todos']
        self.assertEqual({x['todo_id'] for x in lst}, {todo['todo_id'], t2['todo_id']})
        self.assertEqual(other.request({'type': 'todo_list'}, 'todo_list')['todos'], [])
        d = a.request({'type': 'todo_delete', 'todo_id': t2['todo_id']}, 'todo_deleted')
        self.assertEqual(d['todo_id'], t2['todo_id'])
        lst = a.request({'type': 'todo_list'}, 'todo_list')['todos']
        self.assertEqual([x['todo_id'] for x in lst], [todo['todo_id']])

    def test_profile_update(self):
        a, b = self.connect(), self.connect()
        a.send({'type': 'profile_update', 'display_name': '새이름', 'status_message': '바쁨', 'birthday': '02-29'})
        for cl in (a, b):
            # 접속 직후의 (이전 이름) user_updated 와 섞이지 않도록 바뀐 값으로 기다린다
            u = cl.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                            and m['user']['display_name'] == '새이름')['user']
            self.assertEqual((u['display_name'], u['status_message'], u['birthday'], u['online']),
                             ('새이름', '바쁨', '02-29', True))
        a.expect_error({'type': 'profile_update', 'birthday': '13-01'})
        a.expect_error({'type': 'profile_update', 'display_name': '   '})
        a.expect_error({'type': 'profile_update'})
        a.send({'type': 'profile_update', 'birthday': None})
        u = b.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                       and m['user']['birthday'] is None and m['user']['display_name'] == '새이름')['user']
        self.assertIsNone(u['birthday'])
        self.assertEqual(u['display_name'], '새이름')
        # 시스템 메시지는 표시 이름을 사용
        rid = self.group(a, [b])['room_id']
        hist = b.request({'type': 'history', 'room_id': rid}, 'history')
        self.assertTrue(hist['messages'][0]['content'].startswith('새이름님이'))


class TestPresenceAvatar(ServerTestBase):
    def test_presence_set_and_restore(self):
        name = uname()
        a, watcher = self.connect(name), self.connect()
        a.send({'type': 'presence_set', 'presence': 'meeting', 'req_id': 3})
        mine = a.wait_for('user_updated', lambda m: m['user']['username'] == name)
        self.assertEqual((mine['user']['presence'], mine['req_id']), ('meeting', 3))
        watcher.wait_for('user_updated', lambda m: m['user']['username'] == name
                         and m['user']['presence'] == 'meeting')
        a.expect_error({'type': 'presence_set', 'presence': 'offline'})
        a.close()
        watcher.wait_for('user_updated', lambda m: m['user']['username'] == name
                         and m['user']['presence'] == 'offline')
        a2 = self.connect(name)
        self.assertEqual(a2.welcome['me']['presence'], 'meeting')  # 마지막 설정값 복원
        watcher.wait_for('user_updated', lambda m: m['user']['username'] == name
                         and m['user']['presence'] == 'meeting' and m['user']['online'])

    def test_avatar_upload_fetch_clear(self):
        a, stranger = self.connect(), self.connect()
        a.send({'type': 'profile_update', 'avatar_b64': base64.b64encode(PNG_1PX).decode()})
        u = stranger.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                              and m['user'].get('avatar_id'))['user']   # 접속 알림이 먼저 올 수 있다
        self.assertIsInstance(u['avatar_id'], int)
        # 방 멤버가 아니어도 프로필 사진은 받을 수 있다
        got = stranger.request({'type': 'attachment_get', 'attachment_id': u['avatar_id']}, 'attachment_data')
        self.assertEqual(base64.b64decode(got['data_b64']), PNG_1PX)
        self.assertEqual(got['mime'], 'image/png')
        a.expect_error({'type': 'profile_update', 'avatar_b64': base64.b64encode(b'not an image').decode()})
        too_big = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\0' * (512 * 1024)).decode()
        err = a.expect_error({'type': 'profile_update', 'avatar_b64': too_big})
        self.assertIn('512KB', err['message'])
        a.send({'type': 'profile_update', 'avatar_clear': True})
        u = stranger.wait_for('user_updated', lambda m: m['user']['username'] == a.username)['user']
        self.assertIsNone(u['avatar_id'])

    def test_avatar_clear_restores_org_photo(self):
        """[기본 이미지] = 조직도 엑셀 사진(org_avatar_id). 없으면 사진 없음 (명세 §7.2)"""
        a, stranger = self.connect(), self.connect()
        org_id = self.server.store.save_attachment('avatar.png', 'image/png', PNG_1PX, a.username)
        self.server.store.set_org_avatar(a.username, org_id)
        a.send({'type': 'profile_update', 'avatar_b64': base64.b64encode(PNG_1PX).decode()})
        u = stranger.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                              and m['user'].get('avatar_id'))['user']
        self.assertNotEqual(u['avatar_id'], org_id)
        a.send({'type': 'profile_update', 'avatar_clear': True})
        u = stranger.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                              and m['user']['avatar_id'] == org_id)['user']
        got = stranger.request({'type': 'attachment_get', 'attachment_id': u['avatar_id']}, 'attachment_data')
        self.assertEqual(base64.b64decode(got['data_b64']), PNG_1PX)
        a.send({'type': 'multi_profile_set', 'profiles': [{'name': '회사', 'display_name': '회사',
                                                            'avatar_clear': True, 'viewers': []}]})
        profiles = a.wait_for('multi_profile_list')['profiles']
        self.assertEqual(profiles[0]['avatar_id'], org_id)


class TestRagBot(ServerTestBase):
    def _bot_room(self, client):
        client.send({'type': 'room_open_direct', 'target': BOT})
        room = client.wait_for('room_updated', lambda m: BOT in m['room']['members'])['room']
        self.assertEqual(room['type'], 'direct')
        return room['room_id']

    def _bot_msg(self, client, rid, timeout=TIMEOUT):
        return client.wait_for('msg_new', lambda m: m['message']['room_id'] == rid
                               and m['message']['sender'] == BOT, timeout)['message']

    def test_direct_question_answered(self):
        a = self.connect()
        rid = self._bot_room(a)
        q = self.send_text(a, rid, '휴가 규정 알려줘')
        ans = self._bot_msg(a, rid)
        self.assertEqual((ans['kind'], ans['content']), ('text', '답변: 휴가 규정 알려줘'))
        ru = a.wait_for('read_update', lambda m: m['username'] == BOT)
        self.assertEqual(ru['last_read_msg_id'], ans['msg_id'])
        question, asker, room_ids, history = next(x for x in self.rag.questions if x[1] == a.username)
        self.assertIn(rid, room_ids)
        self.assertEqual(history[-1]['msg_id'], q['msg_id'])
        self.assertTrue(any(m['msg_id'] == q['msg_id'] for m in self.rag.indexed))
        self.assertFalse(any(m['msg_id'] == ans['msg_id'] for m in self.rag.indexed))  # 봇 답변은 색인 안 함

    def test_group_only_on_mention(self):
        a, b = self.connect(), self.connect()
        a.send({'type': 'room_create', 'name': 'AI방', 'members': [b.username, BOT]})
        rid = a.wait_for('room_updated', lambda m: m['room']['name'] == 'AI방')['room']['room_id']
        self.send_text(a, rid, '그냥 잡담')
        b.assert_no('msg_new', lambda m: m['message']['sender'] == BOT, wait=0.5)
        self.send_text(a, rid, '@AI 비서 회의록 요약해줘')
        ans = self._bot_msg(b, rid)
        self.assertEqual(ans['content'], '답변: 회의록 요약해줘')
        self.send_text(b, rid, '이것도 알려줘', mentions=[BOT])
        a.wait_for('msg_new', lambda m: m['message']['sender'] == BOT
                   and m['message']['content'] == '답변: 이것도 알려줘')
        # 봇이 없는 방에서는 멘션해도 답하지 않는다
        rid2 = self.group(a, [b], name='봇없는방')['room_id']
        self.send_text(a, rid2, '@AI 비서 안녕')
        a.assert_no('msg_new', lambda m: m['message']['sender'] == BOT and m['message']['room_id'] == rid2)

    def test_file_learning_and_doc_handlers(self):
        a, other = self.connect(), self.connect()
        rid = self._bot_room(a)
        a.send({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '',
                'attachment': {'name': 'guide.txt', 'mime': 'text/plain',
                               'data_b64': base64.b64encode('사내 규정 문서입니다.'.encode()).decode()}})
        ans = self._bot_msg(a, rid)
        self.assertTrue(ans['content'].startswith("📚 'guide.txt' 문서를 학습했습니다 ("), ans['content'])
        a.send({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '',
                'attachment': {'name': 'x.dwg', 'mime': 'application/octet-stream',   # 실행 파일은 보안 정책이 먼저 막는다 (§10.10)
                               'data_b64': base64.b64encode(b'AC1032').decode()}})
        self.assertEqual(self._bot_msg(a, rid)['content'], '지원하지 않는 파일 형식입니다.')

        docs = a.request({'type': 'rag_doc_list'}, 'rag_doc_list')['docs']
        self.assertEqual([d['name'] for d in docs], ['guide.txt'])
        self.assertEqual(set(docs[0]), {'doc_id', 'name', 'owner', 'size', 'chunks', 'created_at'})
        added = a.request({'type': 'rag_doc_add', 'name': 'faq.md',
                           'data_b64': base64.b64encode(b'# FAQ').decode()}, 'rag_doc_added')['doc']
        self.assertEqual((added['name'], added['owner']), ('faq.md', a.username))
        err = a.expect_error({'type': 'rag_doc_add', 'name': 'bad.bin', 'data_b64': base64.b64encode(b'x').decode()})
        self.assertEqual(err['message'], '지원하지 않는 파일 형식입니다.')
        self.assertEqual(other.request({'type': 'rag_doc_list'}, 'rag_doc_list')['docs'], [])
        other.expect_error({'type': 'rag_doc_delete', 'doc_id': added['doc_id']})
        d = a.request({'type': 'rag_doc_delete', 'doc_id': added['doc_id']}, 'rag_doc_deleted')
        self.assertEqual(d['doc_id'], added['doc_id'])


def _broken_rag_factory(db_path):
    raise ImportError('rag.py 없음')


class TestRagUnavailable(unittest.TestCase):
    def test_bot_reports_unavailable(self):
        tmpdir = tempfile.mkdtemp(prefix='kakao_norag_')
        server = KakaoServer(port=0, db_path=os.path.join(tmpdir, 't.db'), tick_seconds=0.2,
                             rag_factory=_broken_rag_factory)
        server.serve_in_thread()
        a = None
        try:
            self.assertIsNone(server.rag)
            a = Client(server.port, uname())
            a.send({'type': 'room_open_direct', 'target': BOT})
            rid = a.wait_for('room_updated')['room']['room_id']
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '질문'})
            ans = a.wait_for('msg_new', lambda m: m['message']['sender'] == BOT)['message']
            self.assertEqual(ans['content'], 'AI 기능을 사용할 수 없습니다')
            err = a.expect_error({'type': 'rag_doc_list'})
            self.assertEqual(err['message'], 'AI 기능을 사용할 수 없습니다')
        finally:
            if a:
                a.close()
            server.stop()
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestOrgJson(unittest.TestCase):
    def test_org_json_override(self):
        tmpdir = tempfile.mkdtemp(prefix='kakao_org_')
        with open(os.path.join(tmpdir, 'org.json'), 'w', encoding='utf-8') as fh:
            json.dump([{'username': '홍길동', 'company': 'KCS 신사업', 'department': '전략팀', 'title': '본부장'},
                       {'username': '신입사원', 'company': 'KCS 물류', 'department': '운영팀', 'title': '인턴',
                        'birthday': '03-03'}], fh, ensure_ascii=False)
        server = KakaoServer(port=0, db_path=os.path.join(tmpdir, 't.db'), rag_factory=FakeRag)
        server.serve_in_thread()
        a = None
        try:
            a = Client(server.port, uname())
            users = {u['username']: u for u in a.welcome['users']}
            self.assertEqual((users['홍길동']['company'], users['홍길동']['title']), ('KCS 신사업', '본부장'))
            self.assertEqual((users['신입사원']['department'], users['신입사원']['birthday']), ('운영팀', '03-03'))
            self.assertEqual(users['김철수']['company'], 'KCS 홀딩스')  # 기본 시드 유지
        finally:
            if a:
                a.close()
            server.stop()
            shutil.rmtree(tmpdir, ignore_errors=True)


    def test_org_json_v2_holding_tree(self):
        """v2: 지주사→계열사 구조 전달, 조직 경로/연락처/겸직, 데모 사용자는 게스트로"""
        tmpdir = tempfile.mkdtemp(prefix='kakao_org2_')
        # 이전 실행에서 데모 사용자가 이미 DB에 있던 상황 재현
        from server.kakao.store import Store
        Store(os.path.join(tmpdir, 't.db')).close()
        org = {'version': 2,
               'holding': {'name': '한국지주', 'business': '지주회사업'},
               'companies': [{'name': '한네트'}, {'name': '케이씨티'},
                             {'name': '케이씨에스', 'units': [['기술연구소'], ['기술연구소', '연구1실']]}],
               'include_demo_users': False,
               'users': [{'username': '도승현', 'company': '케이씨에스', 'department': '2팀(SI)',
                          'org_path': ['기술연구소', '연구1실', '2팀(SI)'], 'title': '수석', 'account': 'doshyun',
                          'extension': '5236', 'mobile': '010-0000-0000',
                          'concurrent': [{'org_path': ['신사업팀'], 'title': '수석'}]}]}
        with open(os.path.join(tmpdir, 'org.json'), 'w', encoding='utf-8') as fh:
            json.dump(org, fh, ensure_ascii=False)
        server = KakaoServer(port=0, db_path=os.path.join(tmpdir, 't.db'), rag_factory=FakeRag)
        server.serve_in_thread()
        a = None
        try:
            a = Client(server.port, uname())
            w = a.welcome
            self.assertEqual(w['org']['holding']['name'], '한국지주')
            self.assertEqual([c['name'] for c in w['org']['companies']], ['한네트', '케이씨티', '케이씨에스'])
            users = {u['username']: u for u in w['users']}
            me = users['도승현']
            self.assertEqual(me['org_path'], ['기술연구소', '연구1실', '2팀(SI)'])
            self.assertEqual((me['extension'], me['account']), ('5236', 'doshyun'))
            self.assertEqual(me['concurrent'][0]['org_path'], ['신사업팀'])
            self.assertEqual(users['홍길동']['company'], '게스트')     # 데모 사용자 → 게스트
            self.assertTrue(users[BOT]['is_bot'])                       # AI 비서는 유지
        finally:
            if a:
                a.close()
            server.stop()
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='kakao_persist_')
        self.db_path = os.path.join(self.tmpdir, 't.db')
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start(self):
        server = KakaoServer(port=0, db_path=self.db_path, tick_seconds=0.2, rag_factory=FakeRag)
        server.serve_in_thread()
        return server

    def test_persistence_across_restart(self):
        server = self._start()
        try:
            a = Client(server.port, '영속A')
            b = Client(server.port, '영속B')
            self.clients += [a, b]
            a.send({'type': 'room_open_direct', 'target': '영속B'})
            rid = a.wait_for('room_updated')['room']['room_id']
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '저장될 메시지',
                    'client_tmp_id': 't'})
            msg = a.wait_for('msg_new')['message']
            a.send({'type': 'react', 'msg_id': msg['msg_id'], 'emoji': '❤'})
            a.wait_for('msg_updated')
            a.request({'type': 'todo_create', 'title': '영속 할 일'}, 'todo_updated')
            a.request({'type': 'profile_update', 'status_message': '영속 상태'}, 'user_updated')
            a.request({'type': 'event_create', 'title': '영속 일정', 'start': '2031-01-01T10:00:00',
                       'end': '2031-01-01T11:00:00'}, 'event_updated')
            a.close()
            b.close()
        finally:
            server.stop()

        server = self._start()
        try:
            a = Client(server.port, '영속A')
            self.clients.append(a)
            self.assertEqual(a.welcome['me']['status_message'], '영속 상태')
            room = next(r for r in a.welcome['rooms'] if r['room_id'] == rid)
            self.assertEqual(room['last_message']['content'], '저장될 메시지')
            self.assertEqual(room['last_message']['reactions'], {'❤': ['영속A']})
            self.assertEqual(room['read_pointers']['영속A'], msg['msg_id'])
            hist = a.request({'type': 'history', 'room_id': rid}, 'history')
            self.assertEqual([m['content'] for m in hist['messages']], ['저장될 메시지'])
            todos = a.request({'type': 'todo_list'}, 'todo_list')['todos']
            self.assertEqual([t['title'] for t in todos], ['영속 할 일'])
            events = a.request({'type': 'event_list', 'start': '2031-01-01T00:00:00',
                                'end': '2031-01-02T00:00:00'}, 'event_list')['events']
            self.assertEqual([e['title'] for e in events], ['영속 일정'])
            names = {u['username'] for u in a.welcome['users']}
            self.assertIn('영속B', names)
            # 새 메시지 id 는 이전보다 커야 함(전역 증가)
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '재시작 후', 'client_tmp_id': 'z'})
            self.assertGreater(a.wait_for('msg_new')['message']['msg_id'], msg['msg_id'])
        finally:
            server.stop()


class TestKakao2025Features(ServerTestBase):
    """2025~2026 카카오톡 개편 반영 (명세 §7.8): 수정 · 중립 삭제 문구 · 조용히 보내기"""

    def test_edit_own_text_within_24h(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        m = self.send_text(a, rid, '내일 회의 3시')
        self.assertFalse(m['edited'])
        a.send({'type': 'edit', 'msg_id': m['msg_id'], 'content': '내일 회의 4시'})
        upd = b.wait_for('msg_updated', lambda x: x['message']['msg_id'] == m['msg_id'])['message']
        self.assertEqual((upd['content'], upd['edited']), ('내일 회의 4시', True))
        self.assertIsNotNone(upd['edited_at'])
        # 다른 사람 메시지 / 빈 내용 / 비텍스트는 거부
        b.send({'type': 'edit', 'msg_id': m['msg_id'], 'content': 'x'})
        self.assertIn('본인', b.wait_for('error')['message'])
        a.send({'type': 'edit', 'msg_id': m['msg_id'], 'content': '  '})
        self.assertIn('내용', a.wait_for('error')['message'])
        # 검색은 수정된 내용으로
        a.send({'type': 'search', 'query': '4시', 'room_id': rid})
        res = a.wait_for('search_result')
        self.assertEqual([x['msg_id'] for x in res['messages']], [m['msg_id']])

    def test_neutral_delete_text(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        m = self.send_text(a, rid, '지울 메시지')
        a.send({'type': 'delete', 'msg_id': m['msg_id']})
        upd = b.wait_for('msg_updated', lambda x: x['message']['msg_id'] == m['msg_id'])['message']
        self.assertEqual(upd['content'], '메시지가 삭제되었습니다')
        self.assertFalse(upd['edited'])

    def test_silent_send(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        m = self.send_text(a, rid, '밤늦게 죄송해요', silent=True)
        self.assertTrue(m['silent'])
        got = b.wait_for('msg_new', lambda x: x['message']['msg_id'] == m['msg_id'])['message']
        self.assertTrue(got['silent'])
        self.assertFalse(self.send_text(a, rid, '일반')['silent'])


class _PreviewHandler(__import__('http.server').server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = ('<html><head><title>대체 제목</title>'
                '<meta property="og:title" content="KCS 공지 &amp; 소식">'
                '<meta property="og:description" content="사내 공지사항입니다">'
                '<meta property="og:site_name" content="KCS"></head><body>hi</body></html>').encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestLinkPreviewAndBriefing(unittest.TestCase):
    def setUp(self):
        import http.server
        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), _PreviewHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.httpd.server_port}/notice'
        self.tmpdir = tempfile.mkdtemp(prefix='kakao_lp_')
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.server.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def start(self, **kw):
        self.server = KakaoServer(port=0, db_path=os.path.join(self.tmpdir, 't.db'), tick_seconds=0.2,
                                  rag_factory=FakeRag, **kw)
        self.server.serve_in_thread()

    def connect(self, name=None):
        c = Client(self.server.port, name or uname())
        self.clients.append(c)
        return c

    def test_fetch_link_preview_parses_og_tags(self):
        self.server = KakaoServer(port=0, db_path=os.path.join(self.tmpdir, 'x.db'))
        prev = fetch_link_preview(self.url, block_internal=False)
        self.assertEqual(prev, {'url': self.url, 'title': 'KCS 공지 & 소식',
                                'description': '사내 공지사항입니다', 'site': 'KCS'})
        self.assertIsNone(fetch_link_preview('http://127.0.0.1:9/nothing', block_internal=False))
        self.assertIsNone(fetch_link_preview(self.url))                       # 기본: 서버 자신은 가져가지 않는다
        for url in ('http://169.254.169.254/latest/meta-data/', 'http://[::1]/', 'http://0.0.0.0/', 'ftp://x/'):
            self.assertFalse(preview_host_allowed(url), url)
        self.assertTrue(preview_host_allowed('http://10.1.2.3/groupware'))    # 사내망 그룹웨어는 미리보기

    def test_link_preview_pushed_as_msg_updated(self):
        self.start(link_preview=True, preview_fetcher=lambda url: fetch_link_preview(url, block_internal=False))
        a, b = self.connect(), self.connect()
        a.send({'type': 'room_open_direct', 'target': b.username})
        rid = a.wait_for('room_updated')['room']['room_id']
        a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': f'공지 확인: {self.url}'})
        mid = a.wait_for('msg_new')['message']['msg_id']
        upd = b.wait_for('msg_updated', lambda x: x['message']['msg_id'] == mid and x['message']['link_preview'],
                         timeout=6)['message']
        self.assertEqual(upd['link_preview']['title'], 'KCS 공지 & 소식')

    def test_daily_briefing_once_per_day(self):
        self.start(daily_briefing=True)
        name = uname()
        a = self.connect(name)
        brief = a.wait_for('msg_new', lambda x: x['message']['sender'] == BOT, timeout=6)['message']
        self.assertIn('오늘의 브리핑', brief['content'])
        a.close()
        self.clients.remove(a)
        time.sleep(0.3)
        a2 = self.connect(name)
        with self.assertRaises(Exception):  # 같은 날 두 번째 접속에는 보내지 않음
            a2.wait_for('msg_new', lambda x: x['message']['sender'] == BOT, timeout=1.5)



# ====================================================================== v3 (명세 §8)
def secret_blob(n=40):
    """비밀채팅 암호문 대역 (서버는 형식만 확인)."""
    return base64.b64encode(os.urandom(n)).decode()


PUBKEY = base64.b64encode(b'\x01' * 32).decode()
CALL_KEYS = {'call_id', 'room_id', 'mode', 'host', 'started_at', 'participants'}
STORY_KEYS = {'story_id', 'owner', 'kind', 'content', 'attachment_id', 'bg_color', 'created_at', 'expires_at',
              'viewers'}
POST_KEYS = {'post_id', 'room_id', 'author', 'title', 'content', 'created_at', 'comments'}


class TestSecretChat(ServerTestBase):
    def _secret_room(self, a, b):
        b.request({'type': 'key_publish', 'public_key_b64': PUBKEY}, 'user_updated',
                  lambda m: m['user']['username'] == b.username)
        room = a.request({'type': 'room_open_secret', 'target': b.username}, 'room_updated',
                         lambda m: m['room']['type'] == 'secret')['room']
        b.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])
        return room

    def test_key_publish_and_open_secret(self):
        a, b = self.connect(), self.connect()
        err = a.expect_error({'type': 'room_open_secret', 'target': b.username})
        self.assertEqual(err['message'], '상대방이 아직 비밀채팅을 사용할 수 없습니다')
        a.expect_error({'type': 'key_publish', 'public_key_b64': base64.b64encode(b'short').decode()})
        b.send({'type': 'key_publish', 'public_key_b64': PUBKEY})
        u = a.wait_for('user_updated', lambda m: m['user']['username'] == b.username
                       and m['user']['public_key'])['user']
        self.assertEqual(u['public_key'], PUBKEY)
        room = a.request({'type': 'room_open_secret', 'target': b.username}, 'room_updated')['room']
        self.assertEqual(set(room), ROOM_KEYS)
        self.assertEqual((room['type'], sorted(room['members'])), ('secret', sorted([a.username, b.username])))
        b.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])
        again = a.request({'type': 'room_open_secret', 'target': b.username}, 'room_updated')['room']
        self.assertEqual(again['room_id'], room['room_id'])
        # 일반 1:1 방과는 다른 방
        direct = a.request({'type': 'room_open_direct', 'target': b.username}, 'room_updated',
                           lambda m: m['room']['type'] == 'direct')['room']
        self.assertNotEqual(direct['room_id'], room['room_id'])
        a.expect_error({'type': 'room_open_secret', 'target': a.username})

    def test_secret_messages_opaque_and_restricted(self):
        a, b = self.connect(), self.connect()
        rid = self._secret_room(a, b)['room_id']
        blob = secret_blob()
        tmp = 'sec1'
        a.send({'type': 'send', 'room_id': rid, 'kind': 'secret', 'content': blob, 'client_tmp_id': tmp})
        m = a.wait_for('msg_new', lambda x: x['client_tmp_id'] == tmp)['message']
        self.assertEqual((m['kind'], m['content']), ('secret', blob))
        got = b.wait_for('msg_new', lambda x: x['message']['msg_id'] == m['msg_id'])['message']
        self.assertEqual(got['content'], blob)   # 저장/전달은 그대로(해석 안 함)
        hist = b.request({'type': 'history', 'room_id': rid}, 'history')['messages']
        self.assertEqual(hist[-1]['content'], blob)
        # 답장 미리보기는 고정 문구
        b.send({'type': 'send', 'room_id': rid, 'kind': 'secret', 'content': secret_blob(),
                'reply_to_id': m['msg_id'], 'client_tmp_id': 'sec2'})
        reply = b.wait_for('msg_new', lambda x: x['client_tmp_id'] == 'sec2')['message']
        self.assertEqual(reply['reply_to']['preview'], '비밀채팅 메시지')
        # 검색·RAG 색인 제외
        res = a.request({'type': 'search', 'query': blob[:12]}, 'search_result')
        self.assertEqual(res['messages'], [])
        time.sleep(0.2)
        self.assertFalse(any(x['msg_id'] == m['msg_id'] for x in self.rag.indexed))
        # 형식/종류 제한
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'secret', 'content': '***'})
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'secret', 'content': secret_blob(10)})
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '평문'})
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '',
                        'attachment': {'name': 'x.txt', 'mime': 'text/plain',
                                       'data_b64': base64.b64encode(b'x').decode()}})
        a.expect_error({'type': 'poll_create', 'room_id': rid, 'question': 'q', 'options': ['a', 'b']})
        a.expect_error({'type': 'event_create', 'room_id': rid, 'title': 't', 'start': iso(datetime.now()),
                        'end': iso(datetime.now())})
        a.expect_error({'type': 'ladder_create', 'room_id': rid, 'participants': ['a', 'b'],
                        'results': ['1', '2']})
        a.expect_error({'type': 'board_post', 'room_id': rid, 'title': 't', 'content': 'c'})
        # 일반 방에서는 secret 종류 불가
        grp = self.group(a, [b])['room_id']
        a.expect_error({'type': 'send', 'room_id': grp, 'kind': 'secret', 'content': secret_blob()})


class TestCalls(ServerTestBase):
    def _start(self, starter, rid, mode):
        starter.send({'type': 'call_start', 'room_id': rid, 'mode': mode, 'req_id': 'cs'})
        upd = starter.wait_for('call_updated', lambda m: m.get('req_id') == 'cs')
        return upd['call']

    def _media(self, sender, call_id, kind='audio', seq=1, **extra):
        sender.send({'type': 'call_media', 'call_id': call_id, 'kind': kind, 'seq': seq,
                     'data_b64': base64.b64encode(b'frame%d' % seq).decode(), **extra})

    def test_video_call_lifecycle_and_relay(self):
        a, b, c, outsider = self.connect(), self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b, c])['room_id']
        call = self._start(a, rid, 'video')
        self.assertEqual(set(call), CALL_KEYS)
        self.assertEqual((call['mode'], call['host'], call['room_id']), ('video', a.username, rid))
        self.assertEqual(call['participants'][a.username],
                         {'role': 'host', 'muted': False, 'video': True, 'screen': False, 'hand': False})
        cmsg = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'call')['message']
        self.assertEqual((cmsg['call_id'], cmsg['content']), (call['call_id'], 'video'))
        b.wait_for('call_updated', lambda m: m['call']['call_id'] == call['call_id'])
        a.expect_error({'type': 'call_start', 'room_id': rid, 'mode': 'voice'})   # 이미 진행 중
        a.expect_error({'type': 'call_start', 'room_id': rid, 'mode': 'bogus'})
        outsider.expect_error({'type': 'call_join', 'call_id': call['call_id']})

        b.send({'type': 'call_join', 'call_id': call['call_id']})
        upd = a.wait_for('call_updated', lambda m: b.username in m['call']['participants'])['call']
        self.assertEqual(upd['participants'][b.username]['role'], 'speaker')
        # 진행 중 통화 목록 / welcome
        lst = c.request({'type': 'call_list', 'room_id': rid}, 'call_list')['calls']
        self.assertEqual([x['call_id'] for x in lst], [call['call_id']])
        self.assertEqual(outsider.request({'type': 'call_list'}, 'call_list')['calls'], [])
        c2 = self.connect(c.username)
        self.assertEqual([x['call_id'] for x in c2.welcome['calls']], [call['call_id']])

        # 미디어 중계: 보낸 사람 제외, 참여자에게만
        self._media(a, call['call_id'], 'audio', 1, codec='zpcm')
        got = b.wait_for('call_media')
        self.assertEqual((got['from'], got['kind'], got['seq'], got['codec'], got['call_id']),
                         (a.username, 'audio', 1, 'zpcm', call['call_id']))
        self.assertEqual(base64.b64decode(got['data_b64']), b'frame1')
        c.assert_no('call_media', wait=0.2)
        a.assert_no('call_media', wait=0.0)
        self._media(c, call['call_id'], 'video', 2)   # 비참여자 프레임은 버림
        b.assert_no('call_media', wait=0.3)

        c.send({'type': 'call_join', 'call_id': call['call_id']})
        b.wait_for('call_updated', lambda m: c.username in m['call']['participants'])
        self._media(b, call['call_id'], 'screen', 3)
        for cl in (a, c, c2):
            self.assertEqual(cl.wait_for('call_media')['kind'], 'screen')
        b.send({'type': 'call_state', 'call_id': call['call_id'], 'muted': True, 'screen': True})
        st = a.wait_for('call_updated', lambda m: m['call']['participants'].get(b.username, {}).get('muted'))['call']
        self.assertTrue(st['participants'][b.username]['screen'])
        b.expect_error({'type': 'call_hand', 'call_id': call['call_id'], 'raised': True})  # 보이스룸 아님

        for cl in (a, b):
            cl.send({'type': 'call_leave', 'call_id': call['call_id']})
            c.wait_for('call_updated', lambda m, u=cl.username: u not in m['call']['participants'])
        c.send({'type': 'call_leave', 'call_id': call['call_id']})
        ended = b.wait_for('call_ended')
        self.assertEqual((ended['call_id'], ended['room_id']), (call['call_id'], rid))
        self.assertIsInstance(ended['duration_seconds'], int)
        sysmsg = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'system'
                            and m['message']['content'].startswith('통화 종료 · '))['message']
        self.assertRegex(sysmsg['content'], r'^통화 종료 · \d+:\d\d$')
        self.assertEqual(a.request({'type': 'call_list'}, 'call_list')['calls'], [])
        a.expect_error({'type': 'call_join', 'call_id': call['call_id']})
        # 기록에는 call 메시지가 남는다
        hist = a.request({'type': 'history', 'room_id': rid}, 'history')['messages']
        self.assertTrue(any(m['kind'] == 'call' and m['call_id'] == call['call_id'] for m in hist))

    def test_video_limit_and_voiceroom_roles(self):
        host = self.connect()
        others = [self.connect() for _ in range(10)]
        rid = self.group(host, others, name='큰방')['room_id']
        call = self._start(host, rid, 'video')
        for o in others[:9]:
            o.request({'type': 'call_join', 'call_id': call['call_id']}, 'call_updated',
                      lambda m, u=o.username: u in m['call']['participants'])
        err = others[9].expect_error({'type': 'call_join', 'call_id': call['call_id']})
        self.assertIn('최대 10명까지 참여할 수 있습니다', err['message'])
        host.send({'type': 'call_leave', 'call_id': call['call_id']})
        for o in others[:9]:
            o.send({'type': 'call_leave', 'call_id': call['call_id']})
        host.wait_for('call_ended', lambda m: m['call_id'] == call['call_id'])

        # 보이스룸: 참여자는 listener, 손들기, host 가 speaker 지정 (host+speaker ≤ 10)
        vr = self._start(host, rid, 'voiceroom')
        self.assertEqual(vr['participants'][host.username]['role'], 'host')
        for o in others:
            o.request({'type': 'call_join', 'call_id': vr['call_id']}, 'call_updated',
                      lambda m, u=o.username: u in m['call']['participants'])
        l0 = others[0]
        self._media(l0, vr['call_id'], 'audio', 1)        # 청취자 오디오는 버림
        host.assert_no('call_media', wait=0.3)
        l0.send({'type': 'call_hand', 'call_id': vr['call_id'], 'raised': True})
        host.wait_for('call_updated', lambda m: m['call']['call_id'] == vr['call_id']
                      and m['call']['participants'].get(l0.username, {}).get('hand'))
        l0.expect_error({'type': 'call_role', 'call_id': vr['call_id'], 'username': l0.username, 'role': 'speaker'})
        for i, o in enumerate(others[:9]):
            host.request({'type': 'call_role', 'call_id': vr['call_id'], 'username': o.username, 'role': 'speaker',
                          'req_id': f'role{i}'}, 'call_updated', lambda m, i=i: m.get('req_id') == f'role{i}')
        upd = l0.wait_for('call_updated', lambda m: m['call']['call_id'] == vr['call_id'] and m['call']['participants']
                          .get(others[8].username, {}).get('role') == 'speaker')['call']
        self.assertEqual(upd['participants'][l0.username], {'role': 'speaker', 'muted': False, 'video': False,
                                                             'screen': False, 'hand': False})
        err = host.expect_error({'type': 'call_role', 'call_id': vr['call_id'], 'username': others[9].username,
                                 'role': 'speaker'})
        self.assertIn('10명', err['message'])
        host.expect_error({'type': 'call_role', 'call_id': vr['call_id'], 'username': host.username,
                           'role': 'listener'})   # 마지막 호스트
        host.clear()
        self._media(l0, vr['call_id'], 'audio', 2)        # 스피커가 되면 전달
        self.assertEqual(host.wait_for('call_media')['seq'], 2)
        # 호스트가 모두 나가면 보이스룸 종료
        host.send({'type': 'call_leave', 'call_id': vr['call_id']})
        self.assertEqual(l0.wait_for('call_ended', lambda m: m['call_id'] == vr['call_id'])['room_id'], rid)
        l0.wait_for('msg_new', lambda m: m['message']['content'] == '보이스룸이 종료되었습니다')

    def test_disconnect_is_leave(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b, c])['room_id']
        call = self._start(a, rid, 'voice')
        b.request({'type': 'call_join', 'call_id': call['call_id']}, 'call_updated',
                  lambda m: b.username in m['call']['participants'])
        b.close()
        upd = a.wait_for('call_updated', lambda m: b.username not in m['call']['participants'])['call']
        self.assertEqual(list(upd['participants']), [a.username])
        a.close()
        c.wait_for('call_ended', lambda m: m['call_id'] == call['call_id'])


class TestBlocking(ServerTestBase):
    def test_block_filters_push_history_unread_search(self):
        a_name = uname()
        a, b, c = self.connect(a_name), self.connect(), self.connect()
        res = a.request({'type': 'block_set', 'username': b.username, 'blocked': True}, 'block_list')
        self.assertEqual(res['blocked'], [b.username])
        a.expect_error({'type': 'block_set', 'username': a.username, 'blocked': True})
        rid = self.group(c, [a, b])['room_id']
        token = f'차단토큰{next(_counter)}'
        mb = self.send_text(b, rid, f'b가 보냄 {token}')       # b 는 평소처럼 echo 를 받는다
        c.wait_for('msg_new', lambda m: m['message']['msg_id'] == mb['msg_id'])
        a.assert_no('msg_new', lambda m: m['message']['msg_id'] == mb['msg_id'])
        mc = self.send_text(c, rid, f'c가 보냄 {token}')
        a.wait_for('msg_new', lambda m: m['message']['msg_id'] == mc['msg_id'])
        # 공감 등 msg_updated 도 전달 안 됨
        c.send({'type': 'react', 'msg_id': mb['msg_id'], 'emoji': '👍'})
        c.wait_for('msg_updated')
        a.assert_no('msg_updated', lambda m: m['message']['msg_id'] == mb['msg_id'])

        hist = a.request({'type': 'history', 'room_id': rid}, 'history')['messages']
        self.assertNotIn(mb['msg_id'], [m['msg_id'] for m in hist])
        self.assertIn(mc['msg_id'], [m['msg_id'] for m in hist])
        res = a.request({'type': 'search', 'query': token}, 'search_result')
        self.assertEqual([m['msg_id'] for m in res['messages']], [mc['msg_id']])
        mb2 = self.send_text(b, rid, '마지막은 b')
        a2 = self.connect(a_name)
        self.assertEqual(a2.welcome['blocked'], [b.username])
        room = next(r for r in a2.welcome['rooms'] if r['room_id'] == rid)
        self.assertEqual(room['unread_count'], 1)
        self.assertEqual(room['last_message']['msg_id'], mc['msg_id'])
        # 차단된 사람 쪽에서는 아무 차이가 없다
        hist_b = b.request({'type': 'history', 'room_id': rid}, 'history')['messages']
        self.assertIn(mb2['msg_id'], [m['msg_id'] for m in hist_b])
        self.assertEqual(b.welcome['blocked'], [])   # b 는 차단 사실을 모른다
        # 1:1 도 마찬가지, 해제하면 다시 보인다
        b.send({'type': 'room_open_direct', 'target': a_name})
        did = b.wait_for('room_updated', lambda m: m['room']['type'] == 'direct')['room']['room_id']
        md = self.send_text(b, did, '1:1 메시지')
        a.assert_no('msg_new', lambda m: m['message']['msg_id'] == md['msg_id'])
        res = a.request({'type': 'block_set', 'username': b.username, 'blocked': False}, 'block_list')
        self.assertEqual(res['blocked'], [])
        hist = a.request({'type': 'history', 'room_id': rid}, 'history')['messages']
        self.assertIn(mb['msg_id'], [m['msg_id'] for m in hist])


class TestMultiProfile(ServerTestBase):
    def test_per_viewer_substitution(self):
        a_name = uname()
        a, b, c = self.connect(a_name), self.connect(), self.connect()
        a.send({'type': 'multi_profile_set', 'req_id': 'mp',
                'profiles': [{'name': '회사용', 'display_name': '김대리', 'status_message': '근무 중',
                              'avatar_b64': base64.b64encode(PNG_1PX).decode(), 'viewers': [b.username]}]})
        lst = a.wait_for('multi_profile_list', lambda m: m.get('req_id') == 'mp')['profiles']
        self.assertEqual(len(lst), 1)
        prof = lst[0]
        self.assertEqual(set(prof), {'profile_id', 'name', 'display_name', 'status_message', 'avatar_id', 'viewers'})
        self.assertEqual((prof['display_name'], prof['viewers']), ('김대리', [b.username]))
        ub = b.wait_for('user_updated', lambda m: m['user']['username'] == a_name
                        and m['user']['display_name'] == '김대리')['user']   # 접속 알림이 먼저 올 수 있다
        self.assertEqual((ub['display_name'], ub['status_message'], ub['avatar_id']),
                         ('김대리', '근무 중', prof['avatar_id']))
        uc = c.wait_for('user_updated', lambda m: m['user']['username'] == a_name)['user']
        self.assertEqual((uc['display_name'], uc['avatar_id']), (a_name, None))
        # 멀티프로필 사진은 지정된 친구만
        b.request({'type': 'attachment_get', 'attachment_id': prof['avatar_id']}, 'attachment_data')
        c.expect_error({'type': 'attachment_get', 'attachment_id': prof['avatar_id']})
        # welcome 도 수신자별
        b2 = self.connect(b.username)
        self.assertEqual(next(u for u in b2.welcome['users'] if u['username'] == a_name)['display_name'], '김대리')
        a2 = self.connect(a_name)
        self.assertEqual(a2.welcome['me']['display_name'], a_name)
        self.assertEqual(next(u for u in a2.welcome['users'] if u['username'] == a_name)['display_name'], a_name)
        # 기본 프로필 수정 → 지정된 친구는 여전히 멀티프로필
        a.send({'type': 'presence_set', 'presence': 'busy'})
        ub = b.wait_for('user_updated', lambda m: m['user']['username'] == a_name
                        and m['user']['presence'] == 'busy')['user']
        self.assertEqual(ub['display_name'], '김대리')
        # 수정(아바타 유지) 후 viewers 변경, 중복 viewer 오류
        a.expect_error({'type': 'multi_profile_set', 'profiles': [
            {'name': '1', 'display_name': 'x', 'viewers': [b.username]},
            {'name': '2', 'display_name': 'y', 'viewers': [b.username]}]})
        a.expect_error({'type': 'multi_profile_set', 'profiles': [
            {'name': '1', 'display_name': 'x', 'viewers': [a_name]}]})
        b.clear()
        c.clear()
        lst = a.request({'type': 'multi_profile_set', 'profiles': [
            {'profile_id': prof['profile_id'], 'name': '회사용', 'display_name': '김과장', 'status_message': '',
             'viewers': [c.username]}]}, 'multi_profile_list')['profiles']
        self.assertEqual((lst[0]['profile_id'], lst[0]['avatar_id']), (prof['profile_id'], prof['avatar_id']))
        # clear() 뒤에도 앞선 변경 알림이 늦게 도착할 수 있다 → 바뀐 이름이 담긴 알림을 기다린다
        self.assertEqual(c.wait_for('user_updated', lambda m: m['user']['username'] == a_name
                                    and m['user']['display_name'] == '김과장')['user']['display_name'], '김과장')
        self.assertEqual(b.wait_for('user_updated', lambda m: m['user']['username'] == a_name)['user']
                         ['display_name'], a_name)
        self.assertEqual(a.request({'type': 'multi_profile_list'}, 'multi_profile_list')['profiles'], lst)
        a.request({'type': 'multi_profile_set', 'profiles': []}, 'multi_profile_list')
        self.assertEqual(c.wait_for('user_updated', lambda m: m['user']['username'] == a_name
                                    and m['user']['display_name'] == a_name)['user']['status_message'], '')


class TestLadder(ServerTestBase):
    def test_compute_mapping_known_ladder(self):
        # 0-1 가로줄(row0) 후 1-2 가로줄(row1): A→2, B→0, C→1
        m = compute_ladder_mapping(['A', 'B', 'C'], ['x', 'y', 'z'], [[0, 0], [1, 1]])
        self.assertEqual(m, {'A': 'z', 'B': 'x', 'C': 'y'})
        self.assertEqual(compute_ladder_mapping(['A', 'B'], ['x', 'y'], []), {'A': 'x', 'B': 'y'})
        with self.assertRaises(ValueError):
            compute_ladder_mapping(['A', 'B', 'C'], ['x', 'y', 'z'], [[0, 0], [0, 1]])   # 인접
        with self.assertRaises(ValueError):
            compute_ladder_mapping(['A', 'B'], ['x', 'y'], [[0, 1]])                     # 범위 밖
        with self.assertRaises(ValueError):
            compute_ladder_mapping(['A', 'B'], ['x'], [])

    def test_generated_ladders_are_permutations(self):
        import random as rnd
        rng = rnd.Random(1234)
        for n in range(2, 11):
            for _ in range(50):
                rungs = generate_ladder_rungs(n, rng=rng)
                cells = {(r, c) for r, c in rungs}
                self.assertTrue(all(0 <= r < 10 and 0 <= c < n - 1 for r, c in rungs))
                self.assertFalse(any((r, c + 1) in cells for r, c in cells))   # 같은 줄 인접 없음
                people = [f'p{i}' for i in range(n)]
                results = [f'r{i}' for i in range(n)]
                mapping = compute_ladder_mapping(people, results, rungs)
                self.assertEqual(sorted(mapping.values()), sorted(results))     # 모두 서로 다른 결과

    def test_ladder_create_message(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        a.send({'type': 'ladder_create', 'room_id': rid, 'participants': ['철수', '영희', '민수'],
                'results': ['커피', '꽝', '꽝'], 'client_tmp_id': 'l'})
        msg = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'ladder')['message']
        lad = msg['ladder']
        self.assertEqual(set(lad), {'participants', 'results', 'rungs', 'mapping'})
        self.assertEqual(lad['mapping'], compute_ladder_mapping(lad['participants'], lad['results'], lad['rungs']))
        self.assertEqual(sorted(lad['mapping'].values()), ['꽝', '꽝', '커피'])
        self.assertIsNone(msg['post_id'])
        a.expect_error({'type': 'ladder_create', 'room_id': rid, 'participants': ['a', 'b'], 'results': ['1']})
        a.expect_error({'type': 'ladder_create', 'room_id': rid, 'participants': ['a'], 'results': ['1']})
        a.expect_error({'type': 'ladder_create', 'room_id': rid, 'participants': ['a', 'a'], 'results': ['1', '2']})
        a.expect_error({'type': 'ladder_create', 'room_id': rid, 'participants': [str(i) for i in range(11)],
                        'results': [str(i) for i in range(11)]})


class TestBoardOpenDrawer(ServerTestBase):
    def test_board_crud(self):
        a, b, outsider = self.connect(), self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        a.send({'type': 'board_post', 'room_id': rid, 'title': '회의록', 'content': '내용', 'req_id': 'bp'})
        post = a.wait_for('board_updated', lambda m: m.get('req_id') == 'bp')['post']
        self.assertEqual(set(post), POST_KEYS)
        self.assertEqual((post['author'], post['title'], post['comments']), (a.username, '회의록', []))
        msg = b.wait_for('msg_new', lambda m: m['message']['kind'] == 'board')['message']
        self.assertEqual((msg['content'], msg['post_id']), ('회의록', post['post_id']))
        b.wait_for('board_updated')
        b.send({'type': 'board_comment', 'post_id': post['post_id'], 'content': '확인했습니다'})
        upd = a.wait_for('board_updated', lambda m: m['post']['comments'])['post']
        self.assertEqual(set(upd['comments'][0]), {'comment_id', 'author', 'content', 'created_at'})
        self.assertEqual(upd['comments'][0]['author'], b.username)
        lst = b.request({'type': 'board_list', 'room_id': rid}, 'board_list')
        self.assertEqual((lst['room_id'], [p['post_id'] for p in lst['posts']]), (rid, [post['post_id']]))
        outsider.expect_error({'type': 'board_list', 'room_id': rid})
        outsider.expect_error({'type': 'board_comment', 'post_id': post['post_id'], 'content': 'x'})
        b.expect_error({'type': 'board_delete', 'post_id': post['post_id']})
        a.send({'type': 'board_delete', 'post_id': post['post_id']})
        d = b.wait_for('board_deleted')
        self.assertEqual((d['post_id'], d['room_id']), (post['post_id'], rid))
        gone = b.wait_for('msg_updated', lambda m: m['message']['msg_id'] == msg['msg_id'])['message']
        self.assertTrue(gone['deleted'])
        self.assertEqual(b.request({'type': 'board_list', 'room_id': rid}, 'board_list')['posts'], [])

    def test_open_room_create_search_join(self):
        a, b = self.connect(), self.connect()
        tag = f'태그{next(_counter)}'
        a.send({'type': 'open_room_create', 'name': '파이썬 모임', 'description': '파이썬 이야기',
                'tags': [tag, '개발'], 'nickname': '방장'})
        room = a.wait_for('room_updated', lambda m: m['room']['type'] == 'open')['room']
        self.assertEqual(set(room), ROOM_KEYS)
        self.assertEqual((room['description'], room['tags'], room['nicknames']),
                         ('파이썬 이야기', [tag, '개발'], {a.username: '방장'}))
        res = b.request({'type': 'open_room_search', 'query': tag}, 'open_room_search')['rooms']
        self.assertEqual(res, [{'room_id': room['room_id'], 'name': '파이썬 모임', 'description': '파이썬 이야기',
                                'tags': [tag, '개발'], 'member_count': 1}])
        b.expect_error({'type': 'history', 'room_id': room['room_id']})
        b.expect_error({'type': 'open_room_join', 'room_id': room['room_id'], 'nickname': ' '})
        b.send({'type': 'open_room_join', 'room_id': room['room_id'], 'nickname': '익명곰'})
        joined = b.wait_for('room_updated', lambda m: m['room']['room_id'] == room['room_id'])['room']
        self.assertEqual(joined['nicknames'], {a.username: '방장', b.username: '익명곰'})
        for cl in (a, b):
            cl.wait_for('msg_new', lambda m: m['message']['content'] == '익명곰님이 들어왔습니다')
        b.expect_error({'type': 'open_room_join', 'room_id': room['room_id'], 'nickname': 'x'})
        grp = self.group(a, [b])['room_id']
        b.expect_error({'type': 'open_room_join', 'room_id': grp, 'nickname': 'x'})
        res = b.request({'type': 'open_room_search', 'query': tag}, 'open_room_search')['rooms']
        self.assertEqual(res[0]['member_count'], 2)
        b.send({'type': 'room_leave', 'room_id': room['room_id']})
        a.wait_for('msg_new', lambda m: m['message']['content'] == '익명곰님이 나갔습니다.')
        # 다른 방 종류에도 기본값
        self.assertEqual((self.group(a, [b], name='기본값')['tags']), [])

    def test_drawer_query_across_rooms(self):
        a, b, outsider = self.connect(), self.connect(), self.connect()
        r1 = self.group(a, [b], name='서랍1')['room_id']
        b.send({'type': 'room_open_direct', 'target': a.username})
        r2 = b.wait_for('room_updated', lambda m: m['room']['type'] == 'direct')['room']['room_id']
        a.send({'type': 'send', 'room_id': r1, 'kind': 'image', 'content': '', 'client_tmp_id': 'i1',
                'attachment': {'name': 'p.png', 'mime': 'image/png', 'data_b64': base64.b64encode(PNG_1PX).decode()}})
        img = a.wait_for('msg_new', lambda m: m['client_tmp_id'] == 'i1')['message']
        b.send({'type': 'send', 'room_id': r2, 'kind': 'file', 'content': '', 'client_tmp_id': 'f1',
                'attachment': {'name': 'a.txt', 'mime': 'text/plain', 'data_b64': base64.b64encode(b'x').decode()}})
        fil = b.wait_for('msg_new', lambda m: m['client_tmp_id'] == 'f1')['message']
        l1 = self.send_text(a, r1, '링크 https://example.com/a')
        l2 = self.send_text(b, r2, '또 링크 http://example.org')
        self.send_text(b, r2, '링크 아님')
        res = a.request({'type': 'drawer_query', 'kind': 'link'}, 'drawer_result')
        self.assertEqual((res['kind'], [m['msg_id'] for m in res['messages']]), ('link', [l2['msg_id'], l1['msg_id']]))
        self.assertEqual([m['msg_id'] for m in a.request({'type': 'drawer_query', 'kind': 'image'},
                                                         'drawer_result')['messages']], [img['msg_id']])
        self.assertEqual([m['msg_id'] for m in a.request({'type': 'drawer_query', 'kind': 'file'},
                                                         'drawer_result')['messages']], [fil['msg_id']])
        self.assertEqual(len(a.request({'type': 'drawer_query', 'kind': 'link', 'limit': 1},
                                       'drawer_result')['messages']), 1)
        self.assertEqual(outsider.request({'type': 'drawer_query', 'kind': 'link'}, 'drawer_result')['messages'], [])
        a.expect_error({'type': 'drawer_query', 'kind': 'video'})


class TestStories(ServerTestBase):
    def test_story_post_view_delete(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        a.send({'type': 'story_post', 'kind': 'text', 'content': '오늘 기분 좋음', 'bg_color': '#112233',
                'req_id': 's'})
        story = a.wait_for('story_new', lambda m: m.get('req_id') == 's')['story']
        self.assertEqual(set(story), STORY_KEYS)
        self.assertEqual((story['owner'], story['bg_color'], story['viewers'], story['attachment_id']),
                         (a.username, '#112233', [], None))
        self.assertGreater(story['expires_at'], story['created_at'])
        b.wait_for('story_new', lambda m: m['story']['story_id'] == story['story_id'])
        b.send({'type': 'story_view', 'story_id': story['story_id']})
        upd = a.wait_for('story_updated')['story']
        self.assertEqual(upd['viewers'], [b.username])
        c.send({'type': 'story_view', 'story_id': story['story_id']})
        self.assertEqual(a.wait_for('story_updated')['story']['viewers'], [b.username, c.username])
        b.assert_no('story_updated')
        mine = next(s for s in a.request({'type': 'story_list'}, 'story_list')['stories']
                    if s['story_id'] == story['story_id'])
        self.assertEqual(mine['viewers'], [b.username, c.username])
        theirs = next(s for s in b.request({'type': 'story_list'}, 'story_list')['stories']
                      if s['story_id'] == story['story_id'])
        self.assertEqual(theirs['viewers'], [])           # 조회자 목록은 소유자에게만
        # 사진 펑: 누구나 받을 수 있음
        a.send({'type': 'story_post', 'kind': 'image', 'content': '',
                'attachment': {'name': 's.png', 'mime': 'image/png', 'data_b64': base64.b64encode(PNG_1PX).decode()}})
        pic = c.wait_for('story_new', lambda m: m['story']['kind'] == 'image')['story']
        got = c.request({'type': 'attachment_get', 'attachment_id': pic['attachment_id']}, 'attachment_data')
        self.assertEqual(base64.b64decode(got['data_b64']), PNG_1PX)
        a.expect_error({'type': 'story_post', 'kind': 'text', 'content': ''})
        a.expect_error({'type': 'story_post', 'kind': 'image', 'attachment': {
            'name': 'x', 'mime': 'image/png', 'data_b64': base64.b64encode(b'not image').decode()}})
        b.expect_error({'type': 'story_delete', 'story_id': story['story_id']})
        a.send({'type': 'story_delete', 'story_id': story['story_id']})
        for cl in (a, b, c):
            self.assertEqual(cl.wait_for('story_deleted')['story_id'], story['story_id'])
        b.expect_error({'type': 'story_view', 'story_id': story['story_id']})


class TestTimedExpiry(ServerTestBase):
    """짧은 만료 값으로 스케줄러 동작 검증: 펑 만료, 보이스룸 자동 종료, 첨부 보관 기간."""
    SERVER_KWARGS = {'story_ttl_seconds': 1, 'voiceroom_max_seconds': 1, 'retention_days': 1 / 86400}

    def test_story_expires(self):
        a, b = self.connect(), self.connect()
        story = a.request({'type': 'story_post', 'kind': 'text', 'content': '펑!'}, 'story_new')['story']
        b.wait_for('story_deleted', lambda m: m['story_id'] == story['story_id'], timeout=5)
        self.assertNotIn(story['story_id'],
                         [s['story_id'] for s in b.request({'type': 'story_list'}, 'story_list')['stories']])

    def test_voiceroom_auto_end(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        call = a.request({'type': 'call_start', 'room_id': rid, 'mode': 'voiceroom'}, 'call_updated')['call']
        ended = b.wait_for('call_ended', lambda m: m['call_id'] == call['call_id'], timeout=5)
        self.assertEqual(ended['room_id'], rid)
        b.wait_for('msg_new', lambda m: m['message']['content'] == '보이스룸이 종료되었습니다')
        self.assertEqual(a.request({'type': 'call_list'}, 'call_list')['calls'], [])

    def test_attachment_retention(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        a.send({'type': 'profile_update', 'avatar_b64': base64.b64encode(PNG_1PX).decode()})
        avatar_id = b.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                               and m['user'].get('avatar_id'))['user']['avatar_id']
        a.send({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '', 'client_tmp_id': 'r1',
                'attachment': {'name': 'old.txt', 'mime': 'text/plain', 'data_b64': base64.b64encode(b'x').decode()}})
        msg = a.wait_for('msg_new', lambda m: m['client_tmp_id'] == 'r1')['message']
        self.assertIsInstance(msg['attachment']['expires_at'], str)
        upd = b.wait_for('msg_updated', lambda m: m['message']['msg_id'] == msg['msg_id']
                         and m['message']['attachment']['expired'], timeout=5)['message']
        self.assertEqual(upd['attachment']['name'], 'old.txt')
        err = b.expect_error({'type': 'attachment_get', 'attachment_id': msg['attachment']['attachment_id']})
        self.assertEqual(err['message'], '보관 기간이 지난 파일입니다')
        # 프로필 사진은 보관 기간 대상이 아님
        b.request({'type': 'attachment_get', 'attachment_id': avatar_id}, 'attachment_data')


class TestMigrationV3(unittest.TestCase):
    def test_legacy_rooms_check_rebuilt_without_losing_members(self):
        import sqlite3
        from server.kakao.store import Store
        tmpdir = tempfile.mkdtemp(prefix='kakao_mig_')
        path = os.path.join(tmpdir, 'old.db')
        try:
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE users (username TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    status_message TEXT NOT NULL DEFAULT '', birthday TEXT, created_at TEXT NOT NULL);
                CREATE TABLE rooms (room_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL CHECK (type IN ('direct', 'group', 'self')), name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, direct_key TEXT UNIQUE, direct_pair TEXT, notice_msg_id INTEGER);
                CREATE TABLE room_members (room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
                    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE, joined_at TEXT NOT NULL,
                    PRIMARY KEY (room_id, username));
                INSERT INTO users VALUES ('옛사용자', '옛사용자', '', NULL, '2020-01-01T00:00:00');
                INSERT INTO rooms(room_id, type, name, created_at) VALUES ('r1', 'group', '옛방', '2020-01-01T00:00:00');
                INSERT INTO room_members VALUES ('r1', '옛사용자', '2020-01-01T00:00:00');
            """)
            db.close()
            store = Store(path)
            try:
                self.assertEqual(store.members('r1'), ['옛사용자'])
                room = store.room_dict('r1', '옛사용자')
                self.assertEqual((room['description'], room['tags'], room['nicknames']), ('', [], {}))
                store.ensure_user('다른사람')
                rid = store.create_open_room('옛사용자', '오픈', '', [], '닉')
                self.assertEqual(store.room_meta(rid)['type'], 'open')
                sid, _ = store.get_or_create_direct('옛사용자', '다른사람', secret=True)
                self.assertEqual(store.room_meta(sid)['type'], 'secret')
            finally:
                store.close()
            Store(path).close()   # 두 번째 열기에서도 문제 없음
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

class TestFriendRegistration(ServerTestBase):
    """친구 등록 - 전화번호 또는 이메일 (정확히 일치할 때만)"""

    def test_add_by_phone_and_email(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        b.send({'type': 'profile_update', 'mobile': '010-1234-5678', 'email': 'Kim@Example.com'})
        b.wait_for('user_updated', lambda m: m['user']['username'] == b.username and m['user']['email'])
        # 전화번호: 하이픈·공백·+82 형식 모두 같은 번호
        for q in ('01012345678', '010 1234 5678', '+82 10-1234-5678'):
            a.send({'type': 'friend_add', 'query': q})
            added = a.wait_for('friend_added')
            self.assertEqual(added['user']['username'], b.username)
        lst = a.wait_for('friend_list')
        self.assertIn(b.username, lst['friends'])
        # 이메일: 대소문자 무시
        c.send({'type': 'friend_add', 'query': 'kim@example.COM'})
        self.assertEqual(c.wait_for('friend_added')['user']['username'], b.username)
        # 재접속하면 welcome 에 친구 목록
        a2 = self.connect(a.username)
        self.assertIn(b.username, a2.welcome['friends'])
        # 삭제
        a.send({'type': 'friend_remove', 'username': b.username})
        self.assertNotIn(b.username, a.wait_for('friend_list', lambda m: b.username not in m['friends'])['friends'])

    def test_errors_and_no_enumeration(self):
        a, b = self.connect(), self.connect()
        b.send({'type': 'profile_update', 'mobile': '010-9999-0000'})
        b.wait_for('user_updated', lambda m: m['user']['username'] == b.username and m['user']['mobile'])
        a.expect_error({'type': 'friend_add', 'query': '010-9999'})           # 일부 번호로는 찾지 않음
        a.expect_error({'type': 'friend_add', 'query': '010-0000-0000'})      # 없는 번호
        a.expect_error({'type': 'friend_add', 'query': 'nobody@nowhere.kr'})
        a.expect_error({'type': 'friend_add', 'query': 'bad@'})
        a.send({'type': 'profile_update', 'mobile': '010-5555-6666'})
        a.wait_for('user_updated', lambda m: m['user']['username'] == a.username and m['user']['mobile'])
        a.expect_error({'type': 'friend_add', 'query': '01055556666'})        # 나 자신
        a.expect_error({'type': 'profile_update', 'email': 'not-an-email'})
        a.expect_error({'type': 'profile_update', 'mobile': '12'})


if __name__ == '__main__':
    unittest.main()
