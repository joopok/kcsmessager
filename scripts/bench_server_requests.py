# -*- coding: utf-8 -*-
"""
서버 요청 처리 시간 측정 - 클라이언트가 화면을 열 때 보내는 요청 (화면 전환 0.5초 목표의 서버 몫)

    python3 scripts/bench_server_requests.py                  # 운영 규모 DB (처음 한 번 만든다, 몇 분)
    python3 scripts/bench_server_requests.py --scale small    # 작은 DB (테스트 · 빠른 확인)
    python3 scripts/bench_server_requests.py --write          # 결과를 claudedocs/SERVER_REQUEST_TIMES.md 에도 쓴다
    python3 scripts/bench_server_requests.py --rebuild        # DB 를 다시 만든다

규모 (full): 직원 500명 · 방 300개(큰 방 4개에 메시지 100만 건) · 일정 · 할 일 · 통화 기록 · 펑 · 사진 첨부.
측정하는 사용자(u0000, 그룹 관리자)는 큰 방 4개 · 1:1 120개 · 그룹 60개에 들어 있고, 큰 방 하나에는 안 읽은 메시지가 20만 건 있다.

'서버 처리' = 서버가 요청 한 줄을 받아 해석한 뒤부터 응답을 그 연결의 보내기 대기열에 넣을 때까지 (KakaoServer._handle_item).
'왕복' = 이 스크립트(같은 PC)가 보내고 응답 줄을 다 받을 때까지 - 응답이 크면 전송 · 해석 시간이 더해진다.
목표: 서버 처리 100ms 이하 (BUDGET_MS). 큰 DB 는 임시 폴더(kcs_bench)에 두고 다시 쓴다. 측정은 복사본에서 한다.
서버의 주기 작업(보관 기간 정리 등)은 측정을 흐리지 않게 따로 한 번씩 재서 '주기 작업' 표로 보여 준다
(이 작업들은 저장소 잠금을 잡으므로 그동안 다른 요청이 기다린다).

종료 코드: 0 모두 목표 이하 · 1 목표를 넘은 요청이 있음 · 2 실행 오류
"""

import argparse
import json
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.kakao.server import KakaoServer  # noqa: E402
from server.kakao.store import ISO_FMT, Store  # noqa: E402

SEED_VERSION = 1
BUDGET_MS = 100.0
ME = 'u0000'
PASSWORD = 'bench-pass-1234'
SCALES = {
    'full': {'users': 500, 'big_rooms': 4, 'big_messages': 1_000_000, 'directs': 120, 'my_groups': 60,
             'other_groups': 116, 'small_messages': 150, 'events': 3000, 'todos': 20, 'calls': 4000,
             'stories': 200, 'photos': 300, 'photo_kb': 120, 'friends': 40, 'unread_big': 200_000},
    'small': {'users': 40, 'big_rooms': 2, 'big_messages': 20_000, 'directs': 10, 'my_groups': 6,
              'other_groups': 6, 'small_messages': 30, 'events': 100, 'todos': 5, 'calls': 100,
              'stories': 10, 'photos': 20, 'photo_kb': 20, 'friends': 10, 'unread_big': 5_000},
}
WORDS = ('회의', '보고서', '일정', '확인', '부탁드립니다', '감사합니다', '자료', '공유', '검토', '내일', '오늘', '견적',
         '납품', '계약', '출장', '점심', '배송', '재고', '결재', '수정본', 'meeting', 'report', 'ok')
COMPANIES = ('KCS 홀딩스', 'KCS 물류', 'KCS 정보통신', 'KCS 무역')
DEPARTMENTS = ('경영지원', '영업', '물류운영', '정보기술', '재무', '구매', '품질')


def log(text):
    print(text, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- DB 만들기
def _sentence(rnd):
    return ' '.join(rnd.choice(WORDS) for _ in range(rnd.randint(2, 12)))


def build_db(path, cfg):
    """규모 cfg 의 DB 를 path 에 만든다 → 측정에 쓸 정보 dict"""
    rnd = random.Random(20261118)
    store = Store(path)
    users = [f'u{i:04d}' for i in range(cfg['users'])]
    for i, name in enumerate(users):
        store.member_create(name, {'company': COMPANIES[i % len(COMPANIES)], 'department': DEPARTMENTS[i % 7],
                                   'title': '사원', 'email': f'{name}@kcs.test', 'employee_no': f'E{i:05d}',
                                   'extension': f'{1000 + i}'})
    store.set_password(ME, PASSWORD)
    store.set_admin(ME, True)
    for g in range(10):
        store.org_group_create(f'TF {g}', '프로젝트', rnd.sample(users, 30), ME)
    for friend in users[1:1 + cfg['friends']]:
        store.add_friend(ME, friend)
    others = users[1:]
    big = [store.create_group(ME, f'전사 공지 {k}', others) for k in range(cfg['big_rooms'])]
    directs = [store.get_or_create_direct(ME, users[1 + k])[0] for k in range(cfg['directs'])]
    my_groups = [store.create_group(ME, f'팀방 {k}', rnd.sample(others, rnd.randint(3, 25)))
                 for k in range(cfg['my_groups'])]
    other_groups = []
    for k in range(cfg['other_groups']):
        owner, *rest = rnd.sample(others, rnd.randint(3, 20))
        other_groups.append(store.create_group(owner, f'다른방 {k}', rest))
    log(f'  방 {len(big) + len(directs) + len(my_groups) + len(other_groups)}개')

    members = {rid: store.members(rid) for rid in big + directs + my_groups + other_groups}
    photos = []
    blob_head = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00'
    for k in range(cfg['photos']):
        data = blob_head + rnd.randbytes(cfg['photo_kb'] * 1024)
        photos.append(store.save_attachment(f'사진{k}.jpg', 'image/jpeg', data, rnd.choice(users)))

    clock = datetime.now() - timedelta(days=300)
    step = timedelta(seconds=max(1, int(300 * 86400 / (cfg['big_messages'] + 1))))
    sql = ('INSERT INTO messages(room_id, sender, kind, content, timestamp, mentions, attachment_id) '
           'VALUES (?, ?, ?, ?, ?, ?, ?)')
    with store._lock:
        db = store._db
        per_big = cfg['big_messages'] // max(1, len(big))
        for rid in big:
            senders = members[rid]
            batch = []
            for n in range(per_big):
                clock += step / max(1, len(big))
                mention = n % 500 == 0
                batch.append((rid, rnd.choice(senders), 'text',
                              (f'@{ME} ' if mention else '') + _sentence(rnd), clock.strftime(ISO_FMT),
                              json.dumps([ME]) if mention else '[]', None))
                if len(batch) >= 50_000:
                    db.executemany(sql, batch)
                    batch = []
            db.executemany(sql, batch)
            log(f'  큰 방 메시지 {per_big:,}건')
        clock = datetime.now() - timedelta(days=30)
        for rid in directs + my_groups + other_groups:
            senders = members[rid]
            rows = []
            for n in range(cfg['small_messages']):
                clock += timedelta(seconds=7)
                if rid in my_groups and n % 10 == 0 and photos:
                    rows.append((rid, rnd.choice(senders), 'image', '사진.jpg', clock.strftime(ISO_FMT), '[]',
                                 rnd.choice(photos)))
                elif n % 37 == 0:
                    rows.append((rid, rnd.choice(senders), 'text', '참고 https://example.com/doc/' + str(n),
                                 clock.strftime(ISO_FMT), '[]', None))
                else:
                    rows.append((rid, rnd.choice(senders), 'text', _sentence(rnd), clock.strftime(ISO_FMT), '[]', None))
            db.executemany(sql, rows)
        # 읽은 위치: 대부분 거의 다 읽음, 측정 사용자는 큰 방 하나에 안 읽은 것이 많다
        last = {r['room_id']: r['m'] for r in db.execute('SELECT room_id, MAX(msg_id) AS m FROM messages GROUP BY room_id')}
        rows = db.execute('SELECT room_id, username FROM read_pointers').fetchall()
        updates = []
        for row in rows:
            top = last.get(row['room_id'], 0)
            behind = rnd.randint(0, 40) if row['username'] == ME else rnd.randint(0, 3000)
            if row['username'] == ME and row['room_id'] == big[0]:
                behind = cfg['unread_big']
            updates.append((max(0, top - behind), row['room_id'], row['username']))
        db.executemany('UPDATE read_pointers SET last_read_msg_id=? WHERE room_id=? AND username=?', updates)
        todo_rows = [(u, f'할 일 {k}', (datetime.now() + timedelta(days=k)).strftime('%Y-%m-%d'),
                      datetime.now().strftime(ISO_FMT)) for u in users for k in range(cfg['todos'])]
        db.executemany('INSERT INTO todos(owner, title, due, created_at) VALUES (?, ?, ?, ?)', todo_rows)
        db.commit()
    log('  메시지 · 읽음 · 할 일')
    for k in range(cfg['events']):
        start = datetime.now() + timedelta(days=rnd.randint(-60, 60), hours=rnd.randint(8, 18))
        attendees = rnd.sample(users, 6) + ([ME] if k % 3 == 0 else [])
        store.create_event(rnd.choice(users), {'title': f'일정 {k}', 'description': '', 'location': '회의실',
                                               'start': start.strftime(ISO_FMT),
                                               'end': (start + timedelta(hours=1)).strftime(ISO_FMT),
                                               'all_day': False, 'remind_minutes': 10, 'color': '#2A386C'},
                           attendees, None)
    mine = big + directs + my_groups
    for k in range(cfg['calls']):
        rid = rnd.choice(mine)
        joined = rnd.sample(members[rid], min(len(members[rid]), 3))
        store.log_call(k + 1, rid, rnd.choice(('voice', 'video')), rnd.choice(joined),
                       (datetime.now() - timedelta(hours=k)).strftime(ISO_FMT), rnd.randint(5, 900), joined)
    for k in range(cfg['stories']):
        store.create_story(rnd.choice(users), 'text', f'오늘의 소식 {k}', None, '#2A386C', 86400)
    thread_room = my_groups[0]
    root = store.add_message(thread_room, ME, 'text', '스레드 원글')
    for k in range(30):
        store.add_message(thread_room, rnd.choice(members[thread_room]), 'text', f'답글 {k}',
                          thread_root=root['msg_id'])
    store.create_post(thread_room, ME, '공지', '게시판 글')
    store.close()
    return {'big_room': big[0], 'big_room_2': big[1 % len(big)], 'direct_room': directs[0], 'group_room': thread_room,
            'thread_root': root['msg_id'], 'photo': photos[0] if photos else None,
            'new_direct_target': users[1 + cfg['directs']] if len(users) > 1 + cfg['directs'] else users[-1],
            'invitees': users[2:5]}


def ensure_db(scale, rebuild=False, cache_dir=None):
    cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), 'kcs_bench')
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f'{scale}_v{SEED_VERSION}.db')
    info_path = path + '.json'
    if rebuild or not (os.path.isfile(path) and os.path.isfile(info_path)):
        for leftover in (path, path + '-wal', path + '-shm', info_path):
            if os.path.exists(leftover):
                os.remove(leftover)
        log(f'측정용 DB 를 만듭니다 ({scale}): {path}')
        started = time.monotonic()
        info = build_db(path, SCALES[scale])
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(info, f)
        log(f'  {time.monotonic() - started:.0f}초')
    with open(info_path, encoding='utf-8') as f:
        return path, json.load(f)


# ---------------------------------------------------------------- 서버 · 클라이언트
class _NoRag:
    """AI 비서 없이 (측정에 끼어들지 않게)"""

    def __init__(self, _path):
        pass

    def list_documents(self, _owner):
        return []

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class TimedServer(KakaoServer):
    """요청마다 서버 처리 시간을 잰다. 측정이 막히지 않게 요청 빈도 한도는 넉넉히"""
    REQUEST_RATE, REQUEST_BURST = 1e6, 1e6
    ACTION_LIMITS = {key: (10 ** 6, 60) for key in KakaoServer.ACTION_LIMITS}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = {}
        self._timing_lock = threading.Lock()

    def _handle_item(self, conn, item):
        started = time.perf_counter()
        try:
            super()._handle_item(conn, item)
        finally:
            if isinstance(item, dict) and item.get('req_id') is not None:
                with self._timing_lock:
                    self.timings[item['req_id']] = time.perf_counter() - started


class BenchClient:
    def __init__(self, port):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=60)
        self.buf = b''
        self.counter = 0

    def call(self, payload, timeout=60.0, parse=True):
        """→ (응답 dict, 응답 줄 바이트 수, 왕복 초, req_id). req_id 가 같은 줄을 기다린다 (다른 알림은 지나친다).
        parse=False 면 응답을 해석하지 않는다 (같은 프로세스의 측정 대상 서버와 CPU 를 다투지 않게) - dict 는 {'type': ...}"""
        self.counter += 1
        req_id = f'b{self.counter}'
        started = time.perf_counter()
        self.sock.sendall((json.dumps({**payload, 'req_id': req_id}, ensure_ascii=False) + '\n').encode('utf-8'))
        deadline = time.monotonic() + timeout
        marker = f'"req_id": "{req_id}"'.encode()
        parts = [self.buf] if self.buf else []          # 받은 조각은 줄이 끝날 때만 잇는다 (큰 응답을 받을 때 매번 복사하지 않게)
        self.buf = b''
        while time.monotonic() < deadline:
            if parts and b'\n' in parts[-1]:
                lines = b''.join(parts).split(b'\n')
                rest = lines.pop()
                parts = [rest] if rest else []
            else:
                lines = []
            for index, line in enumerate(lines):
                if marker not in line:
                    continue
                if parse:
                    frame = json.loads(line.decode('utf-8'))
                    if frame.get('req_id') != req_id:
                        continue
                else:
                    frame = {'type': 'welcome' if line.startswith(b'{"type": "welcome"') else '?', 'req_id': req_id}
                self.buf = b'\n'.join(lines[index + 1:] + [b''.join(parts)])     # 남은 줄은 다음 요청 때 본다
                return frame, len(line) + 1, time.perf_counter() - started, req_id
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError('서버가 연결을 닫았습니다')
            parts.append(chunk)
        raise TimeoutError(payload.get('type'))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def screen_requests(info):
    """(이름, 설명, 요청) - 클라이언트(client/kakao/state.py)가 화면을 열 때 보내는 요청"""
    now = datetime.now()
    month = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    reqs = [
        ('history', '큰 방 처음 50개', {'type': 'history', 'room_id': info['big_room'], 'limit': 50}),
        ('history:이전', '큰 방 이전 대화 50개', {'type': 'history', 'room_id': info['big_room'], 'limit': 50,
                                            'before_msg_id': 'MIDDLE'}),
        ('history:1:1', '1:1 방 처음 50개', {'type': 'history', 'room_id': info['direct_room'], 'limit': 50}),
        ('search', '전체 검색', {'type': 'search', 'query': '견적 납품'}),
        ('search:방', '큰 방 안 검색', {'type': 'search', 'query': '계약', 'room_id': info['big_room']}),
        ('drawer_query:image', '서랍 사진', {'type': 'drawer_query', 'kind': 'image', 'limit': 200}),
        ('drawer_query:file', '서랍 파일', {'type': 'drawer_query', 'kind': 'file', 'limit': 200}),
        ('drawer_query:link', '서랍 링크', {'type': 'drawer_query', 'kind': 'link', 'limit': 200}),
        ('mention_list', '멘션 모아보기', {'type': 'mention_list', 'limit': 100}),
        ('thread_get', '스레드 열기', {'type': 'thread_get', 'root_id': info['thread_root']}),
        ('board_list', '톡게시판', {'type': 'board_list', 'room_id': info['group_room']}),
        ('event_list', '캘린더 (6주)', {'type': 'event_list', 'start': month.isoformat(timespec='seconds'),
                                     'end': (month + timedelta(days=42)).isoformat(timespec='seconds')}),
        ('todo_list', '할 일', {'type': 'todo_list'}),
        ('story_list', '펑', {'type': 'story_list'}),
        ('call_history', '통화 기록', {'type': 'call_history', 'limit': 100}),
        ('call_list', '진행 중 통화', {'type': 'call_list'}),
        ('multi_profile_list', '멀티프로필', {'type': 'multi_profile_list'}),
        ('rag_doc_list', 'AI 문서 목록', {'type': 'rag_doc_list'}),
        ('file_request_list', '내 파일 승인 요청', {'type': 'file_request_list'}),
        ('open_room_search', '오픈채팅 찾기', {'type': 'open_room_search', 'query': ''}),
        ('admin_users', '관리자 구성원 목록', {'type': 'admin_users'}),
        ('admin_file_requests', '관리자 승인 요청', {'type': 'admin_file_requests'}),
        ('admin_file_exceptions', '관리자 예외', {'type': 'admin_file_exceptions'}),
        ('room_open_direct', '있는 1:1 열기', {'type': 'room_open_direct', 'target': 'u0001'}),
        ('room_open_direct:새', '새 1:1 열기', {'type': 'room_open_direct', 'target': info['new_direct_target']}),
        ('room_create', '그룹방 만들기', {'type': 'room_create', 'name': '측정방', 'members': info['invitees']}),
        ('read', '큰 방 읽음 (처음엔 안 읽은 것이 많음)', {'type': 'read', 'room_id': info['big_room'],
                                                   'last_msg_id': 'LAST'}),
    ]
    if info.get('photo'):
        reqs.insert(5, ('attachment_get', '사진 받기', {'type': 'attachment_get', 'attachment_id': info['photo']}))
    return reqs


def _fill(payload, server, info):
    payload = dict(payload)
    if payload.get('before_msg_id') == 'MIDDLE' or payload.get('last_msg_id') == 'LAST':
        with server.store._lock:
            row = server.store._db.execute('SELECT MIN(msg_id) AS lo, MAX(msg_id) AS hi FROM messages WHERE room_id=?',
                                           (info['big_room'],)).fetchone()
        if payload.get('before_msg_id') == 'MIDDLE':
            payload['before_msg_id'] = (row['lo'] + row['hi']) // 2
        else:
            payload['last_msg_id'] = row['hi']
    return payload


def measure(scale='full', repeat=5, rebuild=False, cache_dir=None):
    """→ {'requests': [행], 'jobs': [행], 'scale': ...}"""
    source, info = ensure_db(scale, rebuild, cache_dir)
    work = tempfile.mkdtemp(prefix='kcs_bench_run_')
    try:
        db = os.path.join(work, 'kakao.db')
        shutil.copy2(source, db)
        server = TimedServer(port=0, db_path=db, tick_seconds=3600, rag_factory=_NoRag, require_password=True)
        server.serve_in_thread()
        try:
            return _measure_running(server, info, scale, repeat)
        finally:
            server.stop()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _server_time(server, req_id, wait=5.0):
    """서버가 그 요청의 처리 시간을 적을 때까지 기다린다 (응답은 처리 도중에 먼저 도착할 수 있다)"""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        with server._timing_lock:
            if req_id in server.timings:
                return server.timings[req_id]
        time.sleep(0.001)
    raise TimeoutError(f'서버 처리 시간이 기록되지 않았습니다: {req_id}')


def _row(name, desc, server_s, round_s, sizes, error=''):
    return {'name': name, 'desc': desc, 'server_ms': [s * 1000 for s in server_s],
            'round_ms': [s * 1000 for s in round_s], 'bytes': max(sizes) if sizes else 0, 'error': error}


def _measure_running(server, info, scale, repeat):
    rows = []
    token = server.store.issue_login_token(ME)
    for label, desc, join in (('join (자동 로그인)', '로그인 → welcome · 메인 창',
                               {'type': 'join', 'username': ME, 'token': token}),
                              ('join (비밀번호)', '비밀번호 로그인 (PBKDF2 포함)',
                               {'type': 'join', 'username': ME, 'password': PASSWORD})):
        server_s, round_s, sizes, error = [], [], [], ''
        for _ in range(repeat):
            client = BenchClient(server.port)
            try:
                frame, size, took, req_id = client.call(join)
                if frame.get('type') != 'welcome':
                    error = frame.get('message', frame.get('type'))
                server_s.append(_server_time(server, req_id))
                round_s.append(took)
                sizes.append(size)
            finally:
                client.close()
        rows.append(_row(label, desc, server_s, round_s, sizes, error))
    client = BenchClient(server.port)
    try:
        frame, _size, _took, _rid = client.call({'type': 'join', 'username': ME, 'token': token})
        welcome_parts = {key: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                         for key, value in frame.items() if key not in ('type', 'req_id')}
        for name, desc, payload in screen_requests(info):
            server_s, round_s, sizes, error = [], [], [], ''
            for _ in range(repeat):
                frame, size, took, req_id = client.call(_fill(payload, server, info))
                if frame.get('type') == 'error':
                    error = frame.get('message', '')
                server_s.append(_server_time(server, req_id))
                round_s.append(took)
                sizes.append(size)
            rows.append(_row(name, desc, server_s, round_s, sizes, error))
    finally:
        client.close()
    storm_count = min(STORM_LOGINS, SCALES[scale]['users'] - 1)
    storm = _login_storm(server, info, storm_count) if storm_count > 0 else None
    jobs = []
    for name, job in (('보관 기간 정리 (_run_retention)', server._run_retention),
                      ('안 쓰는 첨부 정리 (_run_orphan_sweep)', server._run_orphan_sweep),
                      ('일정 알림 (_run_reminders)', server._run_reminders),
                      ('펑 만료 (_run_story_expiry)', server._run_story_expiry),
                      ('예약 메시지 (_run_scheduled)', server._run_scheduled)):
        started = time.perf_counter()
        job()
        jobs.append({'name': name, 'ms': (time.perf_counter() - started) * 1000})
    return {'scale': scale, 'repeat': repeat, 'requests': rows, 'jobs': jobs, 'welcome_parts': welcome_parts,
            'storm': storm}


STORM_LOGINS = 100      # 출근 시간처럼 한꺼번에 로그인하는 사람 수


def _login_storm(server, info, count):
    """count 명이 한꺼번에 (자동) 로그인하는 동안, 이미 들어와 있는 사람이 메시지를 보내면 얼마나 걸리나"""
    users = [f'u{i:04d}' for i in range(1, count + 1)]
    tokens = {u: server.store.issue_login_token(u) for u in users}
    sender = BenchClient(server.port)
    sender.call({'type': 'join', 'username': ME, 'token': server.store.issue_login_token(ME)})
    done = threading.Event()
    sends = []

    def keep_sending():
        n = 0
        while not done.is_set():
            n += 1
            _frame, _size, took, _rid = sender.call({'type': 'send', 'room_id': info['direct_room'], 'kind': 'text',
                                                     'content': f'로그인 중 보내기 {n}', 'client_tmp_id': f's{n}'})
            sends.append(took * 1000)
            time.sleep(0.01)

    writer = threading.Thread(target=keep_sending, daemon=True)
    writer.start()
    time.sleep(0.2)
    before = len(sends)
    started = time.perf_counter()
    # 로그인하는 사람들은 다른 프로세스에서 (같은 프로세스면 그 스레드들이 서버와 CPU 를 다퉈 서버가 느려 보인다)
    child = subprocess.run([sys.executable, os.path.abspath(__file__), '--storm-client', str(server.port)],
                           input=json.dumps(tokens), capture_output=True, text=True, timeout=300)
    storm_ms = (time.perf_counter() - started) * 1000
    try:
        results = [tuple(item) for item in json.loads(child.stdout or '[]')]
    except ValueError:
        results = []
    time.sleep(0.1)
    done.set()
    writer.join(10)
    sender.close()
    during = sorted(sends[before:]) or [0.0]
    logins = sorted(ms for ms, _ok in results) or [0.0]
    return {'count': count, 'ok': sum(1 for _ms, ok in results if ok), 'storm_ms': storm_ms,
            'login_p50': statistics.median(logins), 'login_max': logins[-1],
            'login_p95': logins[max(0, int(len(logins) * 0.95) - 1)],
            'send_p50': statistics.median(during), 'send_p95': during[int(len(during) * 0.95) - 1 if len(during) > 1 else 0],
            'send_max': during[-1], 'sends': len(during)}


# ---------------------------------------------------------------- 결과
def over_budget(result, budget=BUDGET_MS):
    return [r['name'] for r in result['requests']
            if not r['name'].startswith('join (비밀번호)') and statistics.median(r['server_ms']) > budget]


def render(result, budget=BUDGET_MS):
    lines = [f"규모: {result['scale']} · 요청마다 {result['repeat']}번 · 목표 서버 처리 {budget:.0f}ms 이하 "
             f"(비밀번호 로그인은 의도한 PBKDF2 시간이 들어 있어 목표에서 뺀다)", '',
             '| 요청 | 화면 | 서버 처리 ms (중앙값 / 최대) | 왕복 ms (중앙값) | 응답 크기 | 판정 |',
             '|---|---|---|---|---|---|']
    for r in result['requests']:
        med = statistics.median(r['server_ms'])
        if r['error']:
            mark = '오류: ' + r['error']
        elif med <= budget or r['name'].startswith('join (비밀번호)'):
            mark = '✓'
        else:
            mark = '✗ 넘음'
        lines.append(f"| {r['name']} | {r['desc']} | {med:.1f} / {max(r['server_ms']):.1f} | "
                     f"{statistics.median(r['round_ms']):.1f} | {r['bytes'] / 1024:,.1f} KB | {mark} |")
    parts = sorted(result['welcome_parts'].items(), key=lambda kv: -kv[1])
    lines += ['', 'welcome 항목별 크기: ' + ', '.join(f'{k} {v / 1024:,.1f}KB' for k, v in parts),
              '', '주기 작업 (한 번, 저장소 잠금을 잡는 동안 다른 요청이 기다린다)', '', '| 작업 | ms |', '|---|---|']
    lines += [f"| {j['name']} | {j['ms']:.1f} |" for j in result['jobs']]
    storm = result.get('storm')
    if storm:
        lines += ['', f"로그인 몰림: {storm['count']}명이 한꺼번에 자동 로그인 (welcome {storm['ok']}명 받음, "
                      f"모두 끝날 때까지 {storm['storm_ms']:,.0f}ms)", '',
                  '| 항목 | 중앙값 ms | p95 ms | 최대 ms |', '|---|---|---|---|',
                  f"| 로그인 한 건 (연결 → welcome) | {storm['login_p50']:.1f} | {storm['login_p95']:.1f} | "
                  f"{storm['login_max']:.1f} |",
                  f"| 그동안 다른 사람의 메시지 보내기 ({storm['sends']}건) | {storm['send_p50']:.1f} | "
                  f"{storm['send_p95']:.1f} | {storm['send_max']:.1f} |"]
    return '\n'.join(lines)


def storm_client(port):
    """--storm-client: 표준 입력의 {아이디: 토큰} 모두가 한꺼번에 로그인 → 표준 출력에 [[걸린 ms, welcome 받음]]"""
    tokens = json.loads(sys.stdin.read())
    results = []
    lock = threading.Lock()
    gate = threading.Event()

    def login(user):
        client = BenchClient(port)
        try:
            gate.wait()
            started = time.perf_counter()
            frame, _size, _took, _rid = client.call({'type': 'join', 'username': user, 'token': tokens[user]},
                                                    parse=False)
            with lock:
                results.append([(time.perf_counter() - started) * 1000, frame.get('type') == 'welcome'])
        finally:
            client.close()

    threads = [threading.Thread(target=login, args=(u,), daemon=True) for u in tokens]
    for t in threads:
        t.start()
    time.sleep(0.3)                 # 모두 연결해 둔 뒤 한꺼번에
    gate.set()
    for t in threads:
        t.join(120)
    print(json.dumps(results))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == '--storm-client':
        return storm_client(int(argv[1]))
    parser = argparse.ArgumentParser(description='서버 요청 처리 시간 측정 (화면을 여는 요청)')
    parser.add_argument('--scale', choices=sorted(SCALES), default='full')
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--rebuild', action='store_true', help='측정용 DB 를 다시 만든다')
    parser.add_argument('--cache-dir', help='측정용 DB 를 둘 폴더 (기본: 임시 폴더의 kcs_bench)')
    parser.add_argument('--write', action='store_true', help='claudedocs/SERVER_REQUEST_TIMES.md 에도 쓴다')
    args = parser.parse_args(argv)
    try:
        result = measure(args.scale, max(1, args.repeat), args.rebuild, args.cache_dir)
    except (OSError, ConnectionError, TimeoutError) as exc:
        print(f'측정 실패: {exc}', file=sys.stderr)
        return 2
    text = render(result)
    print(text)
    if args.write:
        out = os.path.join(ROOT, 'claudedocs', 'SERVER_REQUEST_TIMES.md')
        with open(out, 'w', encoding='utf-8') as f:
            f.write('# 서버 요청 처리 시간 (화면을 여는 요청)\n\n'
                    f'`python3 scripts/bench_server_requests.py --write` 로 만든다 ({datetime.now():%Y-%m-%d %H:%M}).\n\n'
                    + text + '\n')
    return 1 if over_budget(result) else 0


if __name__ == '__main__':
    sys.exit(main())
