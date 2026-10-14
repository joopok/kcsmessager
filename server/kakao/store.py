# -*- coding: utf-8 -*-
"""
KCS Messenger 영속 계층 (SQLite).

- 모든 공개 메서드는 하나의 ``threading.Lock`` 으로 직렬화된다.
- 반환값은 프로토콜 명세(claudedocs/KAKAO_FEATURE_SPEC.md §3)의 데이터 모델과
  동일한 형태의 평범한 dict 이다.
- 입력 검증(권한·형식)은 서버 계층의 책임이며, 여기서는 이미 검증된 값을 받는다.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

ISO_FMT = '%Y-%m-%dT%H:%M:%S'

DELETED_CONTENT = '메시지가 삭제되었습니다'   # 카카오톡 2025 개편: 누가 지웠는지 드러내지 않는 중립 문구
REPLY_PREVIEW_LEN = 50
MAX_SEARCH_RESULTS = 100
MAX_OPEN_SEARCH_RESULTS = 50
DEFAULT_RETENTION_DAYS = 90
SECRET_PREVIEW = '비밀채팅 메시지'
DRAWER_KINDS = ('image', 'file', 'link')
# 차단한 사람이 보낸 메시지를 제외하는 SQL 조건 (매개변수: 조회자)
_NOT_BLOCKED = 'sender NOT IN (SELECT blocked FROM blocks WHERE owner=?)'

BOT_USERNAME = 'AI 비서'
GUEST_COMPANY = '게스트'
PRESENCES = ('online', 'away', 'busy', 'meeting', 'vacation')

# 기본 시드 사용자 (명세 §7.3). org.json 이 있으면 같은 username 의 값을 덮어쓰고 새 사용자를 추가한다.
DEFAULT_USERS = (
    {'username': '홍길동', 'company': 'KCS 홀딩스', 'department': '경영지원팀', 'title': '팀장',
     'birthday': '1982-01-15', 'status_message': '오늘도 화이팅!'},
    {'username': '김철수', 'company': 'KCS 홀딩스', 'department': '경영지원팀', 'title': '대리',
     'birthday': '1993-05-05', 'status_message': '회의 중'},
    {'username': '윤서준', 'company': 'KCS 홀딩스', 'department': '재무팀', 'title': '차장',
     'birthday': '1985-04-02', 'status_message': ''},
    {'username': '이영희', 'company': 'KCS 물류', 'department': '운영팀', 'title': '과장',
     'birthday': '1988-08-20', 'status_message': '여행 가고 싶다'},
    {'username': '박민수', 'company': 'KCS 물류', 'department': '운영팀', 'title': '사원',
     'birthday': '1997-11-11', 'status_message': ''},
    {'username': '강민지', 'company': 'KCS 물류', 'department': '고객지원팀', 'title': '대리',
     'birthday': '1994-06-30', 'status_message': ''},
    {'username': '최지우', 'company': 'KCS 커머스', 'department': '마케팅팀', 'title': '팀장',
     'birthday': '1983-12-25', 'status_message': '커피 한 잔의 여유'},
    {'username': '정하늘', 'company': 'KCS 커머스', 'department': '개발팀', 'title': '선임',
     'birthday': '1991-09-09', 'status_message': ''},
    {'username': BOT_USERNAME, 'company': 'KCS AI', 'department': '지식봇', 'title': '봇',
     'birthday': None, 'status_message': '무엇이든 물어보세요', 'is_bot': True},
)

# v3: secret(비밀채팅)·open(오픈채팅) 방 종류 + 오픈채팅 소개/태그
_ROOMS_DDL = """
CREATE TABLE IF NOT EXISTS {name} (
    room_id         TEXT PRIMARY KEY,
    type            TEXT NOT NULL CHECK (type IN ('direct', 'group', 'self', 'secret', 'open')),
    name            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    direct_key      TEXT UNIQUE,
    direct_pair     TEXT,
    notice_msg_id   INTEGER,
    description     TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]'
);
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    status_message  TEXT NOT NULL DEFAULT '',
    birthday        TEXT,
    created_at      TEXT NOT NULL,
    avatar_id       INTEGER,
    presence        TEXT NOT NULL DEFAULT 'online',
    company         TEXT NOT NULL DEFAULT '',
    department      TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    is_bot          INTEGER NOT NULL DEFAULT 0,
    public_key      TEXT
);
""" + _ROOMS_DDL.format(name='rooms') + """
CREATE TABLE IF NOT EXISTS room_members (
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    username        TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    joined_at       TEXT NOT NULL,
    nickname        TEXT,
    PRIMARY KEY (room_id, username)
);
CREATE INDEX IF NOT EXISTS idx_room_members_user ON room_members(username);
CREATE TABLE IF NOT EXISTS read_pointers (
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    username        TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    last_read_msg_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (room_id, username)
);
CREATE TABLE IF NOT EXISTS attachments (
    attachment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    mime            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    data            BLOB NOT NULL,
    uploader        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expired         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS polls (
    poll_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    creator         TEXT NOT NULL,
    question        TEXT NOT NULL,
    options         TEXT NOT NULL,
    multiple        INTEGER NOT NULL DEFAULT 0,
    closed          INTEGER NOT NULL DEFAULT 0,
    msg_id          INTEGER,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id         INTEGER NOT NULL REFERENCES polls(poll_id) ON DELETE CASCADE,
    option_index    INTEGER NOT NULL,
    username        TEXT NOT NULL,
    PRIMARY KEY (poll_id, option_index, username)
);
CREATE TABLE IF NOT EXISTS messages (
    msg_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    sender          TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    timestamp       TEXT NOT NULL,
    reply_to        TEXT,
    mentions        TEXT NOT NULL DEFAULT '[]',
    reactions       TEXT NOT NULL DEFAULT '{}',
    deleted         INTEGER NOT NULL DEFAULT 0,
    attachment_id   INTEGER REFERENCES attachments(attachment_id),
    event_id        INTEGER,
    poll_id         INTEGER REFERENCES polls(poll_id),
    edited          INTEGER NOT NULL DEFAULT 0,
    edited_at       TEXT,
    silent          INTEGER NOT NULL DEFAULT 0,
    link_preview    TEXT,
    call_id         INTEGER,
    ladder          TEXT,
    post_id         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_attachment ON messages(attachment_id);
CREATE TABLE IF NOT EXISTS events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT REFERENCES rooms(room_id) ON DELETE SET NULL,
    creator         TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    start           TEXT NOT NULL,
    "end"           TEXT NOT NULL,
    all_day         INTEGER NOT NULL DEFAULT 0,
    remind_minutes  INTEGER,
    color           TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_attendees (
    event_id        INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    username        TEXT NOT NULL,
    response        TEXT NOT NULL CHECK (response IN ('yes', 'no', 'maybe', 'pending')),
    PRIMARY KEY (event_id, username)
);
CREATE INDEX IF NOT EXISTS idx_event_attendees_user ON event_attendees(username);
CREATE TABLE IF NOT EXISTS reminders_sent (
    event_id        INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    remind_at       TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    PRIMARY KEY (event_id, remind_at)
);
CREATE TABLE IF NOT EXISTS todos (
    todo_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner           TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    due             TEXT,
    done            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduled_messages (
    scheduled_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    sender          TEXT NOT NULL,
    send_at         TEXT NOT NULL,
    payload         TEXT NOT NULL,
    delivered       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_messages(delivered, send_at);
CREATE TABLE IF NOT EXISTS room_bans (
    room_id         TEXT NOT NULL,
    username        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (room_id, username)
);
CREATE TABLE IF NOT EXISTS blocks (
    owner           TEXT NOT NULL,
    blocked         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (owner, blocked)
);
CREATE INDEX IF NOT EXISTS idx_blocks_blocked ON blocks(blocked);
CREATE TABLE IF NOT EXISTS stories (
    story_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    owner           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    attachment_id   INTEGER,
    bg_color        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_expires ON stories(expires_at);
CREATE TABLE IF NOT EXISTS story_views (
    story_id        INTEGER NOT NULL REFERENCES stories(story_id) ON DELETE CASCADE,
    viewer          TEXT NOT NULL,
    viewed_at       TEXT NOT NULL,
    PRIMARY KEY (story_id, viewer)
);
CREATE TABLE IF NOT EXISTS multi_profiles (
    profile_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    owner           TEXT NOT NULL,
    name            TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    status_message  TEXT NOT NULL DEFAULT '',
    avatar_id       INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_multi_profiles_owner ON multi_profiles(owner);
CREATE TABLE IF NOT EXISTS multi_profile_viewers (
    profile_id      INTEGER NOT NULL REFERENCES multi_profiles(profile_id) ON DELETE CASCADE,
    viewer          TEXT NOT NULL,
    PRIMARY KEY (profile_id, viewer)
);
CREATE INDEX IF NOT EXISTS idx_multi_profile_viewers ON multi_profile_viewers(viewer);
CREATE TABLE IF NOT EXISTS board_posts (
    post_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    msg_id          INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_board_posts_room ON board_posts(room_id, post_id);
CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash      TEXT PRIMARY KEY,
    username        TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_log (
    call_id         INTEGER PRIMARY KEY,
    room_id         TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    mode            TEXT NOT NULL,
    host            TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    duration        INTEGER NOT NULL DEFAULT 0,
    participants    TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS board_comments (
    comment_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL REFERENCES board_posts(post_id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS org_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reset_codes (
    username        TEXT PRIMARY KEY,
    code_hash       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    issued_by       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_requests (
    request_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL,
    room_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    attachment_id   INTEGER NOT NULL,
    name            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    sha256          TEXT NOT NULL,
    rule            TEXT NOT NULL,
    detail          TEXT NOT NULL,
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    decided_by      TEXT,
    decided_at      TEXT,
    note            TEXT NOT NULL DEFAULT '',
    msg_id          INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_requests_status ON file_requests(status, request_id);
CREATE TABLE IF NOT EXISTS file_exceptions (
    exception_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL,
    sha256          TEXT NOT NULL DEFAULT '',
    file_name       TEXT NOT NULL DEFAULT '',
    pattern         TEXT NOT NULL DEFAULT '',
    expires_at      TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    request_id      INTEGER,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS org_groups (
    group_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    members         TEXT NOT NULL DEFAULT '[]',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


def now_iso() -> str:
    """서버 로컬 시각(초 단위) ISO 문자열."""
    return datetime.now().strftime(ISO_FMT)


def _locked(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(self: 'Store', *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            try:
                return func(self, *args, **kwargs)
            except BaseException:
                # 여러 문장을 쓰다 중간에 실패하면 반쯤 쓴 내용을 되돌린다. 연결을 함께 쓰므로 그대로 두면
                # 다음 요청의 commit 이 엉뚱하게 저장해 버린다
                if self._db.in_transaction:
                    self._db.rollback()
                raise
    return wrapper


class Store:
    """SQLite 기반 스레드 안전 저장소."""

    def __init__(self, db_path: str, retention_days: float = DEFAULT_RETENTION_DAYS) -> None:
        directory = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(directory, exist_ok=True)
        self.db_path = db_path
        # 받은 파일 보관 기간 (명세 §8.10). 테스트를 위해 소수(일 단위)도 허용
        self.retention = timedelta(days=float(retention_days))
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute('PRAGMA foreign_keys=ON')
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.executescript(_SCHEMA)
            self._migrate()
            self.org_meta = {'holding': None, 'companies': []}
            self._seed_users(self._load_seed(directory))
            self._db.commit()

    # ------------------------------------------------------------------ 공통
    @_locked
    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass

    def _migrate(self) -> None:
        """v2 DB 에 v2.1 사용자 컬럼을 추가."""
        if 'sort_order' not in {r['name'] for r in self._all('PRAGMA table_info(org_groups)')}:
            # 조직도 그룹 순서 (§4.4, 끌어서 바꾸기) - 기존 그룹은 만든 순서대로
            self._db.execute('ALTER TABLE org_groups ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
            self._db.execute('UPDATE org_groups SET sort_order=group_id')
        existing = {r['name'] for r in self._all('PRAGMA table_info(users)')}
        columns = {
            'avatar_id': 'INTEGER',
            'presence': "TEXT NOT NULL DEFAULT 'online'",
            'company': "TEXT NOT NULL DEFAULT ''",
            'department': "TEXT NOT NULL DEFAULT ''",
            'title': "TEXT NOT NULL DEFAULT ''",
            'is_bot': 'INTEGER NOT NULL DEFAULT 0',
            # v2.2 조직도: 조직 경로(JSON 배열), 계정, 내선, 휴대폰, 겸직(JSON 배열)
            'org_path': "TEXT NOT NULL DEFAULT '[]'",
            'account': "TEXT NOT NULL DEFAULT ''",
            'extension': "TEXT NOT NULL DEFAULT ''",
            'mobile': "TEXT NOT NULL DEFAULT ''",
            'concurrent': "TEXT NOT NULL DEFAULT '[]'",
        }
        columns['last_briefing'] = 'TEXT'
        columns['email'] = "TEXT NOT NULL DEFAULT ''"   # 친구 추가(이메일)용
        self._db.execute('CREATE TABLE IF NOT EXISTS friends (owner TEXT NOT NULL, friend TEXT NOT NULL, '
                         'added_at TEXT NOT NULL, PRIMARY KEY (owner, friend))')
        for name, decl in columns.items():
            if name not in existing:
                self._db.execute(f'ALTER TABLE users ADD COLUMN {name} {decl}')
        # v2.3 메시지: 수정/조용히 보내기/링크 미리보기
        existing = {r['name'] for r in self._all('PRAGMA table_info(messages)')}
        for name, decl in (('edited', 'INTEGER NOT NULL DEFAULT 0'), ('edited_at', 'TEXT'),
                           ('silent', 'INTEGER NOT NULL DEFAULT 0'), ('link_preview', 'TEXT')):
            if name not in existing:
                self._db.execute(f'ALTER TABLE messages ADD COLUMN {name} {decl}')
        # v3: 비밀채팅/오픈채팅/통화/사다리/게시판/보관 기간
        self._add_columns('users', (('public_key', 'TEXT'),))
        self._add_columns('users', (('password_hash', 'TEXT'),))   # 아이디/비밀번호 로그인 (명세 §4.1)
        self._add_columns('users', (('employee_no', "TEXT NOT NULL DEFAULT ''"),))   # 사번 = 로그인 아이디 (§4.1)
        self._add_columns('users', (('is_admin', 'INTEGER NOT NULL DEFAULT 0'),     # 관리자 권한 (§4.2)
                                    ('disabled', 'INTEGER NOT NULL DEFAULT 0')))    # 사용 중지 (퇴사 등)
        self._add_columns('users', (('admin_company', "TEXT NOT NULL DEFAULT ''"),  # 회사 관리자 범위 ('' = 그룹 전체, §4.3)
                                    ('org_locked', 'INTEGER NOT NULL DEFAULT 0'),     # 관리자가 고친 조직 정보 (시드가 덮지 않음)
                                    ('removed', 'INTEGER NOT NULL DEFAULT 0')))       # 삭제된 구성원 (대화 기록은 남김)
        self._add_columns('users', (('org_avatar_id', 'INTEGER'),))   # 조직도 엑셀 사진 = '기본 이미지' (§7.2)
        self._add_columns('users', (('deputies', "TEXT NOT NULL DEFAULT '[]'"),))   # 대직자 username 목록 (§8.13)
        self._add_columns('messages', (('call_id', 'INTEGER'), ('ladder', 'TEXT'), ('post_id', 'INTEGER')))
        self._add_columns('room_members', (('nickname', 'TEXT'),))
        self._add_columns('attachments', (('expired', 'INTEGER NOT NULL DEFAULT 0'),))
        row = self._one("SELECT sql FROM sqlite_master WHERE type='table' AND name='rooms'")
        if row is not None and "'open'" not in (row['sql'] or ''):
            self._rebuild_rooms()
        self._add_columns('rooms', (('description', "TEXT NOT NULL DEFAULT ''"),
                                    ('tags', "TEXT NOT NULL DEFAULT '[]'")))
        self._add_columns('rooms', (('owner', 'TEXT'),))   # 방장 (§10.3, NULL 이면 가장 먼저 들어온 멤버)
        self._add_columns('messages', (('thread_root', 'INTEGER'),                       # 스레드 답글이면 원글 id (§10.4)
                                       ('thread_count', 'INTEGER NOT NULL DEFAULT 0'),   # 원글: 답글 수
                                       ('thread_last_at', 'TEXT')))                      # 원글: 마지막 답글 시각
        self._db.execute('CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_root, msg_id)')
        self._db.execute('CREATE INDEX IF NOT EXISTS idx_attachments_expiry ON attachments(expired, created_at)')
        self._db.execute('CREATE INDEX IF NOT EXISTS idx_messages_post ON messages(post_id)')

    def _add_columns(self, table: str, columns: Iterable[tuple[str, str]]) -> None:
        existing = {r['name'] for r in self._all(f'PRAGMA table_info({table})')}
        for name, decl in columns:
            if name not in existing:
                self._db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')

    def _rebuild_rooms(self) -> None:
        """rooms.type 의 CHECK 제약에 secret/open 추가.

        SQLite 는 CHECK 를 변경할 수 없으므로 표준 절차(새 테이블 생성 → 복사 → 교체)로 재생성한다.
        외래 키가 켜져 있으면 DROP TABLE 이 room_members 등을 연쇄 삭제하므로 반드시 끈 상태에서 수행한다.
        """
        cols = 'room_id, type, name, created_at, direct_key, direct_pair, notice_msg_id'
        old_cols = {r['name'] for r in self._all('PRAGMA table_info(rooms)')}
        extra = [c for c in ('description', 'tags') if c in old_cols]
        if extra:
            cols += ', ' + ', '.join(extra)
        self._db.commit()
        self._db.execute('PRAGMA foreign_keys=OFF')
        try:
            self._db.executescript(
                'BEGIN;' + _ROOMS_DDL.format(name='rooms_v3')
                + f'INSERT INTO rooms_v3({cols}) SELECT {cols} FROM rooms;'
                'DROP TABLE rooms; ALTER TABLE rooms_v3 RENAME TO rooms; COMMIT;')
        except sqlite3.Error:
            self._db.rollback()
            raise
        finally:
            self._db.execute('PRAGMA foreign_keys=ON')

    ORG_FIELDS = ('company', 'department', 'title', 'birthday', 'account', 'extension', 'mobile')

    def _load_seed(self, directory: str) -> list[dict]:
        """기본 시드 + (있다면) <db 디렉터리>/org.json.

        org.json 형식
        - v1: 사용자 객체 배열 → 기본 시드에 덮어쓰기/추가
        - v2: {"version": 2, "holding": {...}, "companies": [{name, business, homepage, phone, units}],
               "include_demo_users": bool, "users": [...]}  (scripts/import_org_xlsx.py 가 생성)
          지주사→계열사 구조를 self.org_meta 로 보관하고, include_demo_users=false 면 데모 사용자 대신
          실제 조직 구성원만 시드한다(AI 비서는 항상 포함).
        """
        seed = {u['username']: dict(u) for u in DEFAULT_USERS}
        self._demo_hidden = set()
        self._seed_admins = []
        org_path = os.path.join(directory, 'org.json')
        if not os.path.isfile(org_path):
            return list(seed.values())
        try:
            with open(org_path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(f'org.json 을 읽을 수 없습니다: {exc}') from exc
        if isinstance(data, dict):
            entries = data.get('users') or []
            holding = data.get('holding')
            self.org_meta = {
                'holding': holding if isinstance(holding, dict) else ({'name': holding} if holding else None),
                'companies': [c if isinstance(c, dict) else {'name': str(c)}
                              for c in (data.get('companies') or []) if c],
            }
            self._seed_admins = [a.strip() for a in data.get('admins') or [] if isinstance(a, str) and a.strip()]
            if data.get('include_demo_users') is False:
                self._demo_hidden = {u['username'] for u in DEFAULT_USERS if not u.get('is_bot')}
                seed = {u['username']: dict(u) for u in DEFAULT_USERS if u.get('is_bot')}
        elif isinstance(data, list):
            entries = data
        else:
            raise ValueError('org.json 은 사용자 배열 또는 v2 객체여야 합니다.')
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('username'), str) \
                    or not entry['username'].strip():
                continue
            name = entry['username'].strip()
            merged = seed.get(name, {'username': name, 'status_message': ''})
            for key in self.ORG_FIELDS:
                if key in entry and (entry[key] is None or isinstance(entry[key], str)):
                    merged[key] = entry[key]
            for key in ('org_path', 'concurrent'):
                if isinstance(entry.get(key), list):
                    merged[key] = entry[key]
            self._demo_hidden.discard(name)
            seed[name] = merged
        return list(seed.values())

    def _seed_users(self, seed: list[dict]) -> None:
        created = now_iso()
        for u in seed:
            self._db.execute(
                'INSERT OR IGNORE INTO users(username, display_name, status_message, birthday, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (u['username'], u['username'], u.get('status_message', ''), u.get('birthday'), created),
            )
            # 조직 정보는 시드가 정본이므로 매 시작 시 갱신
            org_path = u.get('org_path') or ([u['department']] if u.get('department') else [])
            self._db.execute(
                'UPDATE users SET company=?, department=?, title=?, is_bot=?, org_path=?, account=?, '
                "extension=?, mobile=COALESCE(NULLIF(?, ''), mobile), concurrent=? "
                'WHERE username=? AND org_locked=0 AND removed=0',      # 관리자가 고친·삭제한 구성원은 그대로 (§4.3)
                (u.get('company', ''), u.get('department', ''), u.get('title', ''), int(bool(u.get('is_bot'))),
                 json.dumps(org_path, ensure_ascii=False), u.get('account') or '', u.get('extension') or '',
                 u.get('mobile') or '', json.dumps(u.get('concurrent') or [], ensure_ascii=False), u['username']),
            )
            if 'birthday' in u and u['birthday'] is not None:
                # 비어 있거나, 같은 월·일인데 연도만 없는 예전 값(MM-DD)이면 채운다 - 사용자가 고친 날짜는 그대로
                self._db.execute('UPDATE users SET birthday=? WHERE username=? '
                                 'AND (birthday IS NULL OR birthday=substr(?, -5))',
                                 (u['birthday'], u['username'], u['birthday']))
        # 실제 조직도로 바뀌면 예전 데모 사용자는 '게스트'로 내린다(대화 기록 보존)
        for name in getattr(self, '_demo_hidden', ()):
            self._db.execute("UPDATE users SET company='게스트', department='', title='', org_path='[]' "
                             'WHERE username=? AND org_locked=0', (name,))
        # org.json 의 admins 는 시작할 때마다 관리자로 올린다 (내리지는 않는다 - 명세 §4.2)
        for name in getattr(self, '_seed_admins', ()):
            self._db.execute('UPDATE users SET is_admin=1 WHERE username=? AND is_bot=0', (name,))

    def _one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self._db.execute(sql, tuple(params)).fetchone()

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._db.execute(sql, tuple(params)).fetchall()

    # ------------------------------------------------------------------ 사용자
    @staticmethod
    def _user_dict(row: sqlite3.Row, online: Iterable[str]) -> dict:
        is_bot = bool(row['is_bot'])
        is_online = is_bot or row['username'] in online
        presence = (row['presence'] if row['presence'] in PRESENCES else 'online') if is_online else 'offline'
        if row['presence'] == 'vacation':
            presence = 'vacation'   # 휴가 중은 접속하지 않아도 조직도·친구 목록에 보인다
        if is_bot:
            presence = 'online'
        return {
            'username': row['username'],
            'display_name': row['display_name'],
            'status_message': row['status_message'],
            'birthday': row['birthday'],
            'online': is_online,
            'avatar_id': int(row['avatar_id']) if row['avatar_id'] is not None else None,
            'presence': presence,
            'company': row['company'],
            'department': row['department'],
            'title': row['title'],
            'is_bot': is_bot,
            'org_path': Store._json_list(row['org_path']),
            'account': row['account'],
            'employee_no': row['employee_no'] if 'employee_no' in row.keys() else '',
            'extension': row['extension'],
            'mobile': row['mobile'],
            'email': row['email'] if 'email' in row.keys() else '',
            'concurrent': Store._json_list(row['concurrent']),
            'deputies': Store._json_list(row['deputies']) if 'deputies' in row.keys() else [],
            'public_key': row['public_key'],
            'is_admin': bool(row['is_admin']) if 'is_admin' in row.keys() else False,
            'admin_company': (row['admin_company'] if 'admin_company' in row.keys() else '') if row['is_admin'] else '',
        }

    @staticmethod
    def _json_list(text) -> list:
        try:
            value = json.loads(text or '[]')
        except ValueError:
            return []
        return value if isinstance(value, list) else []

    @_locked
    def ensure_user(self, username: str) -> bool:
        """사용자가 없으면 생성. 새로 생성했으면 True."""
        cur = self._db.execute(
            'INSERT OR IGNORE INTO users(username, display_name, status_message, birthday, created_at, company) '
            'VALUES (?, ?, \'\', NULL, ?, ?)',
            (username, username, now_iso(), GUEST_COMPANY),
        )
        self._db.commit()
        return cur.rowcount == 1

    # ------------------------------------------------------------------ 로그인 (명세 §4.1)
    PASSWORD_ITERATIONS = 120_000

    @classmethod
    def hash_password(cls, password: str, salt: Optional[bytes] = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, cls.PASSWORD_ITERATIONS)
        return f'pbkdf2_sha256${cls.PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'

    @staticmethod
    def check_password(password: str, stored: str) -> bool:
        try:
            algo, iterations, salt_hex, digest_hex = stored.split('$')
            if algo != 'pbkdf2_sha256':
                return False
            digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex),
                                         int(iterations))
        except (ValueError, AttributeError):
            return False
        return hmac.compare_digest(digest.hex(), digest_hex)

    @_locked
    def resolve_login(self, login_id: str) -> Optional[str]:
        """로그인 아이디 → username. 사번(employee_no)이 우선이고, 없으면 username, 조직 account,
        그다음 email (사번·account·email 은 대소문자 무시)."""
        row = self._one("SELECT username FROM users WHERE employee_no<>'' AND lower(employee_no)=lower(?) "
                        'ORDER BY username LIMIT 1', (login_id,))
        if row is None:
            row = self._one('SELECT username FROM users WHERE username=?', (login_id,))
        if row is None:
            row = self._one("SELECT username FROM users WHERE account<>'' AND lower(account)=lower(?) "
                            'ORDER BY username LIMIT 1', (login_id,))
        if row is None:
            row = self._one("SELECT username FROM users WHERE email<>'' AND lower(email)=lower(?) "
                            'ORDER BY username LIMIT 1', (login_id,))
        return row['username'] if row else None

    @_locked
    def company_names(self) -> list[str]:
        """처음 사용 등록 때 고를 소속 회사 (명세 §4.1): 지주사 → 계열사 → 사용자 company 순, 중복 제외"""
        names = []
        holding = (self.org_meta or {}).get('holding') or {}
        names.append(holding.get('name') or '')
        names.extend(c.get('name') or '' for c in (self.org_meta or {}).get('companies') or [])
        names.extend(r['company'] for r in self._all(
            "SELECT DISTINCT company FROM users WHERE is_bot=0 AND removed=0 AND company NOT IN ('', ?) ORDER BY company",
            (GUEST_COMPANY,)))
        result = []
        for name in names:
            name = str(name).strip()
            if name and name != GUEST_COMPANY and name not in result:
                result.append(name)
        return result

    REGISTER_TAKEN_NAME = '이미 등록된 이름입니다. 아이디와 비밀번호로 로그인해 주세요.'
    REGISTER_TAKEN_EMAIL = '이미 가입된 이메일입니다.'
    REGISTER_TAKEN_EMPLOYEE_NO = '이미 등록된 사번입니다. 사번과 비밀번호로 로그인해 주세요.'
    REGISTER_OTHER_COMPANY = '선택한 소속에 같은 이름의 구성원이 없습니다. 소속을 다시 확인해 주세요.'
    RESET_ADMIN_BY_TOOL = '관리자 계정은 직접 재설정할 수 없습니다. 다른 그룹 관리자나 서버 PC 의 scripts/set_password.py 로 바꿔 주세요.'
    REGISTER_WRONG_EMPLOYEE_NO = '조직도에 등록된 사번과 다릅니다. 사번을 확인하거나 관리자에게 문의해 주세요.'
    REGISTER_WRONG_EMAIL = '조직도에 등록된 이메일과 다릅니다. 회사 이메일을 확인하거나 관리자에게 문의해 주세요.'
    REGISTER_NEEDS_ORG_EMAIL = '조직도에 이메일이 등록되지 않아 직접 등록할 수 없습니다. 관리자에게 이메일 등록을 요청해 주세요.'
    REGISTER_ADMIN_BY_TOOL = '관리자 계정은 직접 등록할 수 없습니다. 서버 PC 에서 scripts/set_password.py 로 비밀번호를 정해 주세요.'

    def register_user(self, username: str, company: str, email: str, password: str, employee_no: str = '') -> bool:
        """처음 사용 등록 (명세 §4.1). 새로 만들었으면 True. 안 되면 ValueError(사용자에게 보일 사유).
        employee_no 는 선택(예전 클라이언트·관리자 도구) — 없으면 이메일이 로그인 아이디가 된다"""
        return self._register_user(username, company, email, self.hash_password(password),   # 해싱은 잠금 밖
                                   employee_no.strip())

    @_locked
    def _register_user(self, username: str, company: str, email: str, password_hash: str,
                       employee_no: str = '') -> bool:
        if employee_no:
            # 사번은 로그인 아이디라 다른 사람의 사번·사내 계정·이름과 겹치면 안 된다
            taken = self._one("SELECT username FROM users WHERE username<>? AND (username=? OR "
                              "(employee_no<>'' AND lower(employee_no)=lower(?)) OR "
                              "(account<>'' AND lower(account)=lower(?)))",
                              (username, employee_no, employee_no, employee_no))
            if taken is not None:
                raise ValueError(self.REGISTER_TAKEN_EMPLOYEE_NO)
        taken = self._one("SELECT username FROM users WHERE email<>'' AND lower(email)=lower(?) AND username<>?",
                          (email, username))
        if taken is not None:
            raise ValueError(self.REGISTER_TAKEN_EMAIL)
        row = self._one('SELECT company, is_bot, password_hash, employee_no, email, is_admin FROM users WHERE username=?',
                        (username,))
        if row is None:
            self._db.execute(
                'INSERT INTO users(username, display_name, status_message, birthday, created_at, company, email, '
                "password_hash, employee_no) VALUES (?, ?, '', NULL, ?, ?, ?, ?, ?)",
                (username, username, now_iso(), company, email, password_hash, employee_no))
            self._db.commit()
            return True
        if row['is_bot']:
            raise ValueError('봇 계정으로는 등록할 수 없습니다.')
        if row['password_hash']:
            raise ValueError(self.REGISTER_TAKEN_NAME)
        if row['company'] not in ('', GUEST_COMPANY) and row['company'] != company:
            raise ValueError(self.REGISTER_OTHER_COMPANY)
        # 아직 등록하지 않은 조직도 계정은 이름 · 회사만 알면 누구나 가져갈 수 있었다 → 조직도에 적힌 값이 맞아야 한다.
        # 2026-10-14 부터 등록 창에 사번 칸이 없어 이메일로 확인한다(조직도 이메일 = 로그인 아이디). 사번을 보낸
        # 예전 클라이언트·도구는 사번도 맞아야 하고, 이메일이 없는 계정은 사번으로만 확인한다.
        if row['employee_no'] and employee_no and row['employee_no'].lower() != employee_no.lower():
            raise ValueError(self.REGISTER_WRONG_EMPLOYEE_NO)
        if row['email'] and row['email'].lower() != email.lower():
            raise ValueError(self.REGISTER_WRONG_EMAIL)
        if row['employee_no'] and not row['email'] and not employee_no:
            raise ValueError(self.REGISTER_NEEDS_ORG_EMAIL)
        if row['is_admin'] and not (row['employee_no'] or row['email']):   # 확인할 값이 없는 관리자 계정은 서버 도구로만
            raise ValueError(self.REGISTER_ADMIN_BY_TOOL)
        cur = self._db.execute(
            "UPDATE users SET password_hash=?, email=?, employee_no=CASE WHEN ?<>'' THEN ? ELSE employee_no END, "
            "company=CASE WHEN company IN ('', ?) THEN ? ELSE company END "
            'WHERE username=? AND password_hash IS NULL',
            (password_hash, email, employee_no, employee_no, GUEST_COMPANY, company, username))
        self._db.commit()
        if cur.rowcount != 1:     # 그 사이 다른 연결이 먼저 등록했다
            raise ValueError(self.REGISTER_TAKEN_NAME)
        return False

    @_locked
    def password_hash(self, username: str) -> Optional[str]:
        row = self._one('SELECT password_hash FROM users WHERE username=?', (username,))
        return row['password_hash'] if row else None

    def set_password(self, username: str, password: str) -> None:
        self._set_password_hash(username, self.hash_password(password))   # 해싱(느림)은 잠금 밖에서

    def claim_password(self, username: str, password: str) -> bool:
        """비밀번호가 아직 없을 때만 등록 (최초 로그인 두 개가 동시에 와도 한쪽만). 등록했으면 True"""
        return self._claim_password_hash(username, self.hash_password(password))

    @_locked
    def _claim_password_hash(self, username: str, password_hash: str) -> bool:
        cur = self._db.execute('UPDATE users SET password_hash=? WHERE username=? AND password_hash IS NULL',
                               (password_hash, username))
        self._db.commit()
        return cur.rowcount == 1

    @_locked
    def _set_password_hash(self, username: str, password_hash: str) -> None:
        self._db.execute('UPDATE users SET password_hash=? WHERE username=?', (password_hash, username))
        self._db.commit()

    LOGIN_TOKEN_DAYS = 30
    LOGIN_TOKENS_PER_USER = 5

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @_locked
    def issue_login_token(self, username: str) -> str:
        """자동 로그인 토큰 발급. 서버에는 해시만 남기고, 사용자당 최근 몇 개만 유지한다."""
        token = secrets.token_urlsafe(32)
        self._db.execute('INSERT INTO login_tokens(token_hash, username, created_at) VALUES (?, ?, ?)',
                         (self._token_hash(token), username, now_iso()))
        self._db.execute('DELETE FROM login_tokens WHERE username=? AND token_hash NOT IN '
                         '(SELECT token_hash FROM login_tokens WHERE username=? ORDER BY created_at DESC, rowid DESC '
                         'LIMIT ?)', (username, username, self.LOGIN_TOKENS_PER_USER))
        self._db.commit()
        return token

    @_locked
    def check_login_token(self, username: str, token: str) -> bool:
        row = self._one('SELECT username, created_at FROM login_tokens WHERE token_hash=?', (self._token_hash(token),))
        if row is None or row['username'] != username:
            return False
        created = datetime.fromisoformat(row['created_at'])
        if datetime.now() - created.replace(tzinfo=None) > timedelta(days=self.LOGIN_TOKEN_DAYS):
            self._db.execute('DELETE FROM login_tokens WHERE token_hash=?', (self._token_hash(token),))
            self._db.commit()
            return False
        return True

    RESET_CODE_MINUTES = 30
    _RESET_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # 헷갈리는 0/O · 1/I 없음

    @_locked
    def issue_reset_code(self, username: str, issued_by: str) -> tuple[str, str]:
        """관리자가 발급하는 일회용 재설정 코드 (명세 §4.1) → (코드 'ABCD-EFGH', 만료 시각). 새로 발급하면 예전 코드는 무효.
        메일 서버 없이 쓰는 본인 확인: 관리자가 전화 · 대면으로 본인에게만 알려 준다. 서버에는 해시만 둔다"""
        import secrets
        raw = ''.join(secrets.choice(self._RESET_CODE_ALPHABET) for _ in range(8))
        expires = (datetime.now() + timedelta(minutes=self.RESET_CODE_MINUTES)).strftime(ISO_FMT)
        self._db.execute('INSERT INTO reset_codes(username, code_hash, expires_at, issued_by) VALUES (?, ?, ?, ?) '
                         'ON CONFLICT(username) DO UPDATE SET code_hash=excluded.code_hash, '
                         'expires_at=excluded.expires_at, issued_by=excluded.issued_by',
                         (username, self._token_hash(raw), expires, issued_by))
        self._db.commit()
        return f'{raw[:4]}-{raw[4:]}', expires

    def _reset_code_ok(self, username: str, code: str) -> bool:
        normalized = ''.join(ch for ch in (code or '').upper() if ch.isalnum())
        row = self._one('SELECT code_hash, expires_at FROM reset_codes WHERE username=?', (username,))
        if row is None or row['expires_at'] <= now_iso():
            return False
        import hmac
        return hmac.compare_digest(row['code_hash'], self._token_hash(normalized))

    def reset_password(self, username: str, email: str, password: str, code: str = '') -> Optional[bool]:
        """비밀번호 재설정 (명세 §4.1): 등록된 계정이고 가입 이메일과 관리자가 발급한 재설정 코드가 맞으면
        새 비밀번호 + 토큰 모두 폐기 + 코드 사용 처리.
        → True 바꿈 / False 이메일 · 코드 불일치 · 없는 계정 / None 비밀번호를 등록한 적 없는 계정"""
        return self._reset_password(username, email, self.hash_password(password), code)   # 해싱은 잠금 밖

    @_locked
    def _reset_password(self, username: str, email: str, password_hash: str, code: str = '') -> Optional[bool]:
        row = self._one('SELECT email, password_hash, is_admin FROM users WHERE username=? AND is_bot=0', (username,))
        if row is None:
            return False
        if not row['password_hash']:
            return None
        if row['is_admin']:
            raise ValueError(self.RESET_ADMIN_BY_TOOL)
        if not row['email'] or row['email'].lower() != email.strip().lower():
            return False
        if not self._reset_code_ok(username, code):
            return False
        self._db.execute('UPDATE users SET password_hash=? WHERE username=?', (password_hash, username))
        self._db.execute('DELETE FROM login_tokens WHERE username=?', (username,))
        self._db.execute('DELETE FROM reset_codes WHERE username=?', (username,))   # 한 번만 쓴다
        self._db.commit()
        return True

    @_locked
    def revoke_all_login_tokens(self, username: str) -> None:
        self._db.execute('DELETE FROM login_tokens WHERE username=?', (username,))
        self._db.commit()

    # ------------------------------------------------------------------ 관리자 (명세 §4.2)
    @_locked
    def is_admin(self, username: str) -> bool:
        row = self._one('SELECT is_admin FROM users WHERE username=? AND is_bot=0', (username,))
        return bool(row and row['is_admin'])

    @_locked
    def is_disabled(self, username: str) -> bool:
        """로그인 막힘: 사용 중지 또는 삭제된 구성원 (삭제는 사용 중지를 함께 켜지만, 하나만 풀려도 막히게)"""
        row = self._one('SELECT disabled, removed FROM users WHERE username=?', (username,))
        return bool(row and (row['disabled'] or row['removed']))

    @_locked
    def admin_usernames(self) -> list[str]:
        return [r['username'] for r in self._all('SELECT username FROM users WHERE is_admin=1 AND removed=0')]

    @_locked
    def is_removed(self, username: str) -> bool:
        row = self._one('SELECT removed FROM users WHERE username=?', (username,))
        return bool(row and row['removed'])

    @_locked
    def set_admin(self, username: str, is_admin: bool, company: str = '') -> None:
        """관리자 지정/해제. company 가 있으면 그 회사의 회사 관리자, ''면 그룹 관리자 (§4.3).
        그룹 관리자가 한 명도 남지 않게 되면 ValueError"""
        company = (company or '').strip() if is_admin else ''
        current = self._one('SELECT is_admin, admin_company FROM users WHERE username=?', (username,))
        was_group_admin = bool(current and current['is_admin'] and not current['admin_company'])
        if was_group_admin and not (is_admin and not company):   # 그룹 관리자를 내리거나 회사 관리자로 바꿀 때만
            others = self._one("SELECT COUNT(*) AS n FROM users WHERE is_admin=1 AND admin_company='' AND is_bot=0 "
                               'AND removed=0 AND username<>?', (username,))['n']
            if others == 0:
                raise ValueError('관리자가 한 명은 있어야 합니다.')
        self._db.execute('UPDATE users SET is_admin=?, admin_company=? WHERE username=? AND is_bot=0',
                         (int(bool(is_admin)), company, username))
        self._db.commit()

    @_locked
    def admin_scope(self, username: str) -> Optional[str]:
        """관리 범위: None = 관리자 아님, '' = 그룹 전체, '회사' = 그 회사만"""
        row = self._one('SELECT is_admin, admin_company, disabled, removed FROM users WHERE username=? AND is_bot=0',
                        (username,))
        if not row or not row['is_admin'] or row['removed']:
            return None
        return row['admin_company'] or ''

    # ---- 조직도 그룹 (명세 §4.4) - 그룹 관리자가 만드는, 회사와 상관없이 사람을 묶은 그룹 ----
    def _group_dict(self, row: sqlite3.Row) -> dict:
        removed = {r['username'] for r in self._all('SELECT username FROM users WHERE removed=1')}
        return {'group_id': row['group_id'], 'name': row['name'], 'description': row['description'],
                'members': [m for m in json.loads(row['members'] or '[]') if m not in removed],
                'created_by': row['created_by'], 'created_at': row['created_at']}

    @_locked
    def org_groups(self) -> list[dict]:
        return [self._group_dict(r) for r in self._all('SELECT * FROM org_groups ORDER BY sort_order, group_id')]

    @_locked
    def org_group(self, group_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM org_groups WHERE group_id=?', (group_id,))
        return self._group_dict(row) if row else None

    def _check_group(self, name: str, members: list[str], group_id: Optional[int]) -> None:
        if self._one('SELECT 1 FROM org_groups WHERE name=? AND group_id IS NOT ?', (name, group_id)):
            raise ValueError('이미 있는 그룹 이름입니다.')
        for member in members:
            if not self._one('SELECT 1 FROM users WHERE username=? AND removed=0', (member,)):
                raise ValueError(f'구성원을 찾을 수 없습니다: {member}')

    @_locked
    def org_group_create(self, name: str, description: str, members: list[str], created_by: str) -> int:
        self._check_group(name, members, None)
        last = self._one('SELECT COALESCE(MAX(sort_order), 0) AS n FROM org_groups')['n']   # 새 그룹은 맨 뒤
        cur = self._db.execute('INSERT INTO org_groups(name, description, members, sort_order, created_by, '
                               'created_at) VALUES (?, ?, ?, ?, ?, ?)',
                               (name, description, json.dumps(members, ensure_ascii=False), last + 1,
                                created_by, now_iso()))
        self._db.commit()
        return cur.lastrowid

    @_locked
    def org_group_update(self, group_id: int, name: str, description: str, members: list[str]) -> None:
        self._check_group(name, members, group_id)
        self._db.execute('UPDATE org_groups SET name=?, description=?, members=? WHERE group_id=?',
                         (name, description, json.dumps(members, ensure_ascii=False), group_id))
        self._db.commit()

    @_locked
    def org_group_reorder(self, group_ids: list[int]) -> None:
        """그룹 순서를 group_ids 순서로. 지금 있는 그룹 전부가 한 번씩 있어야 한다 (그 사이 바뀌었으면 ValueError)"""
        current = {r['group_id'] for r in self._all('SELECT group_id FROM org_groups')}
        if len(group_ids) != len(set(group_ids)) or set(group_ids) != current:
            raise ValueError('그룹 목록이 바뀌었습니다. 조직도를 새로 보고 다시 시도해 주세요.')
        for order, group_id in enumerate(group_ids, 1):
            self._db.execute('UPDATE org_groups SET sort_order=? WHERE group_id=?', (order, group_id))
        self._db.commit()

    @_locked
    def org_group_delete(self, group_id: int) -> None:
        self._db.execute('DELETE FROM org_groups WHERE group_id=?', (group_id,))
        self._db.commit()

    # 조직도 아래쪽(KCS AI · 그룹사 아래) 덩어리 순서: ["group:<id>", "company:<회사>"] (명세 §4.4)
    @_locked
    def org_order(self) -> list[str]:
        row = self._one("SELECT value FROM org_settings WHERE key='lower_order'")
        try:
            order = json.loads(row['value']) if row else []
        except ValueError:
            order = []
        return [t for t in order if isinstance(t, str)]

    @_locked
    def set_org_order(self, order: list[str]) -> None:
        """순서 저장 + 그 안의 그룹 순서를 sort_order 에도 맞춘다 (없는 그룹이 있으면 ValueError)"""
        groups = [int(t.split(':', 1)[1]) for t in order if t.startswith('group:')]
        existing = {r['group_id'] for r in self._all('SELECT group_id FROM org_groups')}
        if not set(groups) <= existing:
            raise ValueError('그룹 목록이 바뀌었습니다. 조직도를 새로 보고 다시 시도해 주세요.')
        rest = sorted(existing - set(groups),
                      key=lambda g: self._one('SELECT sort_order FROM org_groups WHERE group_id=?', (g,))['sort_order'])
        for position, group_id in enumerate(groups + rest, 1):
            self._db.execute('UPDATE org_groups SET sort_order=? WHERE group_id=?', (position, group_id))
        self._db.execute("INSERT INTO org_settings(key, value) VALUES ('lower_order', ?) "
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (json.dumps(order, ensure_ascii=False),))
        self._db.commit()

    # ---- 파일 전송 보안 (명세 §10.10) ----
    @_locked
    def file_policy(self) -> Optional[dict]:
        """그룹 관리자가 저장한 정책. 한 번도 저장하지 않았으면 None (= 기본 정책)"""
        row = self._one("SELECT value FROM org_settings WHERE key='file_policy'")
        try:
            value = json.loads(row['value']) if row else None
        except ValueError:
            value = None
        return value if isinstance(value, dict) else None

    @_locked
    def set_file_policy(self, policy: dict) -> None:
        self._db.execute("INSERT INTO org_settings(key, value) VALUES ('file_policy', ?) "
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (json.dumps(policy, ensure_ascii=False),))
        self._db.commit()

    @_locked
    def group_admins(self) -> list[str]:
        """그룹 관리자 (승인 요청 알림을 받을 사람)"""
        return [r['username'] for r in self._all(
            "SELECT username FROM users WHERE is_admin=1 AND admin_company='' AND removed=0 AND is_bot=0")]

    @staticmethod
    def _file_request_dict(row: sqlite3.Row) -> dict:
        return {k: row[k] for k in ('request_id', 'username', 'room_id', 'kind', 'content', 'attachment_id', 'name',
                                    'size', 'sha256', 'rule', 'detail', 'reason', 'status', 'decided_by',
                                    'decided_at', 'note', 'msg_id', 'created_at')}

    @_locked
    def add_file_request(self, username: str, room_id: str, kind: str, content: str, attachment_id: int,
                         name: str, size: int, sha256: str, rule: str, detail: str, reason: str) -> dict:
        cur = self._db.execute(
            'INSERT INTO file_requests(username, room_id, kind, content, attachment_id, name, size, sha256, rule, '
            'detail, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (username, room_id, kind, content, attachment_id, name, size, sha256, rule, detail, reason, now_iso()))
        self._db.commit()
        return self._file_request_dict(self._one('SELECT * FROM file_requests WHERE request_id=?', (cur.lastrowid,)))

    @_locked
    def file_request(self, request_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM file_requests WHERE request_id=?', (request_id,))
        return self._file_request_dict(row) if row else None

    @_locked
    def file_requests(self, username: Optional[str] = None, status: Optional[str] = None,
                      limit: int = 200) -> list[dict]:
        """최근 것부터. username 이 있으면 그 사람이 낸 요청만"""
        sql, params = 'SELECT * FROM file_requests WHERE 1=1', []
        if username is not None:
            sql += ' AND username=?'
            params.append(username)
        if status is not None:
            sql += ' AND status=?'
            params.append(status)
        sql += ' ORDER BY request_id DESC LIMIT ?'
        params.append(limit)
        return [self._file_request_dict(r) for r in self._all(sql, params)]

    @_locked
    def pending_file_request_count(self, username: str) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM file_requests WHERE username=? AND status='pending'", (username,))
        return int(row['n'])

    @_locked
    def decide_file_request(self, request_id: int, status: str, decided_by: str, note: str,
                            msg_id: Optional[int] = None) -> dict:
        """대기 중인 요청만 결정한다 (두 관리자가 동시에 눌러도 한 번만). 반려 · 취소면 보관하던 파일을 지운다"""
        cur = self._db.execute(
            "UPDATE file_requests SET status=?, decided_by=?, decided_at=?, note=?, msg_id=? "
            "WHERE request_id=? AND status='pending'", (status, decided_by, now_iso(), note, msg_id, request_id))
        if cur.rowcount != 1:
            self._db.rollback()
            raise ValueError('이미 처리된 요청입니다.')
        row = self._one('SELECT * FROM file_requests WHERE request_id=?', (request_id,))
        if status != 'approved':
            self._db.execute("UPDATE attachments SET data=X'', expired=1 WHERE attachment_id=?", (row['attachment_id'],))
        self._db.commit()
        return self._file_request_dict(row)

    @_locked
    def set_file_request_msg(self, request_id: int, msg_id: int) -> dict:
        self._db.execute('UPDATE file_requests SET msg_id=? WHERE request_id=?', (msg_id, request_id))
        self._db.commit()
        return self._file_request_dict(self._one('SELECT * FROM file_requests WHERE request_id=?', (request_id,)))

    @_locked
    def file_request_for_attachment(self, attachment_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM file_requests WHERE attachment_id=? ORDER BY request_id DESC LIMIT 1',
                        (attachment_id,))
        return self._file_request_dict(row) if row else None

    @staticmethod
    def _file_exception_dict(row: sqlite3.Row) -> dict:
        return {k: row[k] for k in ('exception_id', 'username', 'sha256', 'file_name', 'pattern', 'expires_at',
                                    'note', 'request_id', 'created_by', 'created_at')}

    @_locked
    def add_file_exception(self, username: str, sha256: str, file_name: str, pattern: str, expires_at: str,
                           note: str, created_by: str, request_id: Optional[int] = None) -> dict:
        cur = self._db.execute(
            'INSERT INTO file_exceptions(username, sha256, file_name, pattern, expires_at, note, request_id, '
            'created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (username, sha256, file_name, pattern, expires_at, note, request_id, created_by, now_iso()))
        self._db.commit()
        return self._file_exception_dict(
            self._one('SELECT * FROM file_exceptions WHERE exception_id=?', (cur.lastrowid,)))

    @_locked
    def file_exceptions(self, username: Optional[str] = None, include_expired: bool = False) -> list[dict]:
        sql, params = 'SELECT * FROM file_exceptions WHERE 1=1', []
        if username is not None:
            sql += ' AND username=?'
            params.append(username)
        if not include_expired:
            sql += ' AND expires_at > ?'
            params.append(now_iso())
        return [self._file_exception_dict(r) for r in self._all(sql + ' ORDER BY exception_id DESC', params)]

    @_locked
    def delete_file_exception(self, exception_id: int) -> bool:
        cur = self._db.execute('DELETE FROM file_exceptions WHERE exception_id=?', (exception_id,))
        self._db.commit()
        return cur.rowcount == 1

    def org_payload(self) -> dict:
        """welcome 의 org: {holding, companies, groups, order}"""
        return {**(self.org_meta or {}), 'groups': self.org_groups(), 'order': self.org_order()}

    @_locked
    def user_company(self, username: str) -> Optional[str]:
        row = self._one('SELECT company FROM users WHERE username=?', (username,))
        return row['company'] if row else None

    # ---- 구성원 등록 · 수정 · 삭제 (명세 §4.3) ----
    MEMBER_FIELDS = ('company', 'department', 'title', 'account', 'employee_no', 'extension', 'mobile', 'email')

    def _member_conflict(self, username: str, fields: dict) -> None:
        for key, label in (('employee_no', '사번'), ('account', '사내 계정'), ('email', '이메일')):
            value = (fields.get(key) or '').strip()
            if value and self._one(f"SELECT 1 FROM users WHERE {key}<>'' AND lower({key})=lower(?) AND username<>?",
                                   (value, username)):
                raise ValueError(f'이미 다른 구성원이 쓰는 {label}입니다.')
        number = (fields.get('employee_no') or '').strip()
        if number and self._one('SELECT 1 FROM users WHERE username=? AND username<>?', (number, username)):
            raise ValueError('이미 다른 구성원이 쓰는 사번입니다.')

    @_locked
    def member_create(self, username: str, fields: dict) -> None:
        if self._one('SELECT 1 FROM users WHERE username=?', (username,)):
            raise ValueError('이미 있는 이름입니다.')
        self._member_conflict(username, fields)
        self._db.execute(
            'INSERT INTO users(username, display_name, status_message, birthday, created_at, company, department, '
            'title, account, employee_no, extension, mobile, email, org_path, org_locked) '
            "VALUES (?, ?, '', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (username, username, now_iso(), *(fields.get(k) or '' for k in self.MEMBER_FIELDS),
             self._member_org_path(fields.get('department') or '', fields.get('team') or '')))
        self._db.commit()

    @staticmethod
    def _member_org_path(department: str, team: str) -> str:
        """관리자가 정한 부서 · 팀 → org_path JSON ([부서, 팀] / [부서] / [])"""
        path = [department] + ([team] if team else []) if department else []
        return json.dumps(path, ensure_ascii=False)

    @_locked
    def member_update(self, username: str, fields: dict) -> None:
        self._member_conflict(username, fields)
        changes = {k: (fields[k] or '') for k in self.MEMBER_FIELDS if k in fields}
        if 'department' in changes or 'team' in fields:
            department = changes.get('department')
            if department is None:
                row = self._one('SELECT department FROM users WHERE username=?', (username,))
                department = (row['department'] if row else '') or ''
            changes['org_path'] = self._member_org_path(department, fields.get('team') or '')
        sets = ', '.join(f'{k}=?' for k in changes)
        self._db.execute(f'UPDATE users SET {sets + ", " if sets else ""}org_locked=1 WHERE username=?',
                         (*changes.values(), username))
        self._db.commit()

    @_locked
    def member_set_removed(self, username: str, removed: bool) -> None:
        """삭제(소프트) / 복원. 삭제하면 로그인도 막고 자동 로그인 토큰을 폐기한다"""
        self._db.execute('UPDATE users SET removed=?, disabled=?, org_locked=1 WHERE username=?',
                         (int(removed), int(removed), username))
        if removed:
            self._db.execute('DELETE FROM login_tokens WHERE username=?', (username,))
        self._db.commit()

    @_locked
    def set_disabled(self, username: str, disabled: bool) -> None:
        """사용 중지/해제. 중지하면 그 계정의 자동 로그인 토큰도 모두 폐기"""
        self._db.execute('UPDATE users SET disabled=? WHERE username=?', (int(bool(disabled)), username))
        if disabled:
            self._db.execute('DELETE FROM login_tokens WHERE username=?', (username,))
        self._db.commit()

    @staticmethod
    def _team_of(org_path_json) -> str:
        try:
            path = json.loads(org_path_json or '[]')
        except (TypeError, ValueError):
            return ''
        return path[1] if isinstance(path, list) and len(path) > 1 and isinstance(path[1], str) else ''

    @_locked
    def admin_user_list(self, online: Iterable[str] = ()) -> list[dict]:
        """관리자 계정 관리 화면용 (봇 제외, 이름순)"""
        online = set(online)
        return [{'username': r['username'], 'display_name': r['display_name'], 'company': r['company'],
                 'department': r['department'], 'team': self._team_of(r['org_path']),
                 'title': r['title'], 'account': r['account'],
                 'employee_no': r['employee_no'], 'email': r['email'], 'extension': r['extension'],
                 'mobile': r['mobile'], 'registered': r['password_hash'] is not None,
                 'disabled': bool(r['disabled']), 'is_admin': bool(r['is_admin']),
                 'admin_company': r['admin_company'] if r['is_admin'] else '', 'removed': bool(r['removed']),
                 'online': r['username'] in online}
                for r in self._all('SELECT * FROM users WHERE is_bot=0 ORDER BY username')]

    @_locked
    def clear_password(self, username: str) -> None:
        """관리자 초기화: 비밀번호·자동 로그인 토큰 삭제 → 그 사람은 [처음 사용 등록]을 다시 한다"""
        self._db.execute('UPDATE users SET password_hash=NULL WHERE username=?', (username,))
        self._db.execute('DELETE FROM login_tokens WHERE username=?', (username,))
        self._db.commit()

    @_locked
    def revoke_login_token(self, username: str, token: str) -> None:
        self._db.execute('DELETE FROM login_tokens WHERE token_hash=? AND username=?',
                         (self._token_hash(token), username))
        self._db.commit()

    @_locked
    def user_exists(self, username: str) -> bool:
        return self._one('SELECT 1 FROM users WHERE username=?', (username,)) is not None

    @_locked
    def get_user(self, username: str, online: Iterable[str] = ()) -> Optional[dict]:
        row = self._one('SELECT * FROM users WHERE username=?', (username,))
        return self._user_dict(row, set(online)) if row else None

    @_locked
    def list_users(self, online: Iterable[str] = ()) -> list[dict]:
        online_set = set(online)
        rows = self._all('SELECT * FROM users WHERE removed=0 ORDER BY created_at, username')   # 삭제된 구성원 제외
        return [self._user_dict(r, online_set) for r in rows]

    @_locked
    def can_be_deputy(self, username: str) -> bool:
        """대직자로 지정할 수 있는 사용자: 있는 사람 · 봇 아님 · 삭제 안 됨 (§8.13)"""
        row = self._one('SELECT is_bot, removed FROM users WHERE username=?', (username,))
        return bool(row) and not row['is_bot'] and not row['removed']

    @_locked
    def is_bot(self, username: str) -> bool:
        row = self._one('SELECT is_bot FROM users WHERE username=?', (username,))
        return bool(row and row['is_bot'])

    @_locked
    def bot_usernames(self) -> list[str]:
        return [r['username'] for r in self._all('SELECT username FROM users WHERE is_bot=1')]

    @_locked
    def display_name(self, username: str) -> str:
        row = self._one('SELECT display_name FROM users WHERE username=?', (username,))
        return row['display_name'] if row else username

    @_locked
    def org_avatar(self, username: str) -> Optional[int]:
        """조직도 엑셀에서 가져온 사진(attachment_id). 프로필 '기본 이미지'가 이 사진이다"""
        row = self._one('SELECT org_avatar_id FROM users WHERE username=?', (username,))
        return int(row['org_avatar_id']) if row and row['org_avatar_id'] is not None else None

    @_locked
    def set_org_avatar(self, username: str, attachment_id: Optional[int]) -> None:
        self._db.execute('UPDATE users SET org_avatar_id=? WHERE username=?', (attachment_id, username))
        self._db.commit()

    @_locked
    def update_profile(self, username: str, fields: dict) -> None:
        allowed = {'display_name', 'status_message', 'birthday', 'avatar_id', 'presence', 'mobile', 'email', 'deputies'}
        sets = [(k, json.dumps(v, ensure_ascii=False) if k == 'deputies' else v)
                for k, v in fields.items() if k in allowed]
        if not sets:
            return
        sql = 'UPDATE users SET ' + ', '.join(f'{k}=?' for k, _ in sets) + ' WHERE username=?'
        self._db.execute(sql, [v for _, v in sets] + [username])
        self._db.commit()

    # ------------------------------------------------------------------ 친구 등록 (전화번호/이메일)
    @staticmethod
    def phone_digits(text: str) -> str:
        return ''.join(ch for ch in str(text or '') if ch.isdigit())

    @_locked
    def find_user_by_contact(self, query: str) -> Optional[str]:
        """전화번호(숫자만 비교, 하이픈·공백·+82 무시) 또는 이메일(대소문자 무시)이 정확히 일치하는 사용자"""
        q = (query or '').strip()
        if '@' in q:
            row = self._one("SELECT username FROM users WHERE email != '' AND lower(email)=lower(?) AND is_bot=0 "
                            'AND removed=0', (q,))
            return row['username'] if row else None
        digits = self.phone_digits(q)
        if digits.startswith('82') and len(digits) >= 11:
            digits = '0' + digits[2:]        # +82 10-1234-5678 → 01012345678
        if len(digits) < 9:
            return None
        for row in self._all("SELECT username, mobile FROM users WHERE mobile != '' AND is_bot=0 AND removed=0"):
            if self.phone_digits(row['mobile']) == digits:
                return row['username']
        return None

    @_locked
    def add_friend(self, owner: str, friend: str) -> bool:
        cur = self._db.execute('INSERT OR IGNORE INTO friends(owner, friend, added_at) VALUES (?, ?, ?)',
                               (owner, friend, now_iso()))
        self._db.commit()
        return cur.rowcount > 0

    @_locked
    def remove_friend(self, owner: str, friend: str) -> None:
        self._db.execute('DELETE FROM friends WHERE owner=? AND friend=?', (owner, friend))
        self._db.commit()

    @_locked
    def friends_of(self, owner: str) -> list[str]:
        return [r['friend'] for r in self._all('SELECT friend FROM friends WHERE owner=? ORDER BY added_at', (owner,))]

    # ------------------------------------------------------------------ 채팅방
    def _last_msg_id(self, room_id: str) -> int:
        row = self._one('SELECT MAX(msg_id) AS m FROM messages WHERE room_id=?', (room_id,))
        return int(row['m'] or 0)

    def _add_member(self, room_id: str, username: str, pointer: int, nickname: Optional[str] = None) -> None:
        self._db.execute(
            'INSERT OR IGNORE INTO room_members(room_id, username, joined_at, nickname) VALUES (?, ?, ?, ?)',
            (room_id, username, now_iso(), nickname),
        )
        self._db.execute(
            'INSERT INTO read_pointers(room_id, username, last_read_msg_id) VALUES (?, ?, ?) '
            'ON CONFLICT(room_id, username) DO UPDATE SET last_read_msg_id=excluded.last_read_msg_id',
            (room_id, username, pointer),
        )

    def _owner(self, row: sqlite3.Row, members: list[str]) -> Optional[str]:
        """방장 (§10.3): group/open 방만. 저장된 방장이 나갔으면 가장 먼저 들어온 (봇이 아닌) 멤버"""
        if row['type'] not in ('group', 'open'):
            return None
        if row['owner'] in members:
            return row['owner']
        return next((u for u in members if u != BOT_USERNAME), None)

    def _members(self, room_id: str) -> list[str]:
        rows = self._all(
            'SELECT username FROM room_members WHERE room_id=? ORDER BY joined_at, rowid', (room_id,))
        return [r['username'] for r in rows]

    @_locked
    def get_or_create_direct(self, me: str, target: str, secret: bool = False) -> tuple[str, list[str]]:
        """1:1(또는 self, secret=True 면 비밀채팅) 방을 조회/생성. (room_id, 새로 (재)추가된 멤버 목록) 반환."""
        if me == target and not secret:
            room_type, pair, key = 'self', [me], f'self\x1f{me}'
        else:
            pair = sorted([me, target])
            room_type = 'secret' if secret else 'direct'
            key = room_type + '\x1f' + '\x1f'.join(pair)
        row = self._one('SELECT room_id FROM rooms WHERE direct_key=?', (key,))
        added: list[str] = []
        if row is None:
            room_id = uuid.uuid4().hex[:16]
            self._db.execute(
                'INSERT INTO rooms(room_id, type, name, created_at, direct_key, direct_pair) '
                'VALUES (?, ?, \'\', ?, ?, ?)',
                (room_id, room_type, now_iso(), key, json.dumps(pair, ensure_ascii=False)),
            )
            for u in pair:
                self._add_member(room_id, u, 0)
                added.append(u)
        else:
            room_id = row['room_id']
            current = set(self._members(room_id))
            last = self._last_msg_id(room_id)
            for u in pair:
                if u not in current:
                    self._add_member(room_id, u, last)
                    added.append(u)
        self._db.commit()
        return room_id, added

    @_locked
    def restore_direct_members(self, room_id: str) -> list[str]:
        """direct 방에서 나간 상대를 다시 추가(메시지 전송 시). 추가된 멤버 반환."""
        row = self._one('SELECT type, direct_pair FROM rooms WHERE room_id=?', (room_id,))
        if row is None or row['type'] not in ('direct', 'secret') or not row['direct_pair']:
            return []
        current = set(self._members(room_id))
        last = self._last_msg_id(room_id)
        added = []
        for u in json.loads(row['direct_pair']):
            if u not in current:
                self._add_member(room_id, u, last)
                added.append(u)
        self._db.commit()
        return added

    @_locked
    def create_group(self, creator: str, name: str, members: list[str]) -> str:
        room_id = uuid.uuid4().hex[:16]
        self._db.execute(
            'INSERT INTO rooms(room_id, type, name, created_at, owner) VALUES (?, \'group\', ?, ?, ?)',
            (room_id, name, now_iso(), creator),
        )
        for u in [creator] + [m for m in members if m != creator]:
            self._add_member(room_id, u, 0)
        self._db.commit()
        return room_id

    @_locked
    def add_members(self, room_id: str, usernames: list[str]) -> None:
        last = self._last_msg_id(room_id)
        for u in usernames:
            self._add_member(room_id, u, last)
        self._db.commit()

    @_locked
    def remove_member(self, room_id: str, username: str) -> None:
        self._db.execute('DELETE FROM room_members WHERE room_id=? AND username=?', (room_id, username))
        self._db.execute('DELETE FROM read_pointers WHERE room_id=? AND username=?', (room_id, username))
        self._db.commit()

    @_locked
    def rename_room(self, room_id: str, name: str) -> None:
        self._db.execute('UPDATE rooms SET name=? WHERE room_id=?', (name, room_id))
        self._db.commit()

    @_locked
    def set_notice(self, room_id: str, msg_id: Optional[int]) -> None:
        self._db.execute('UPDATE rooms SET notice_msg_id=? WHERE room_id=?', (msg_id, room_id))
        self._db.commit()

    @_locked
    def room_meta(self, room_id: str) -> Optional[dict]:
        row = self._one('SELECT * FROM rooms WHERE room_id=?', (room_id,))
        if row is None:
            return None
        members = self._members(room_id)
        return {
            'room_id': row['room_id'],
            'type': row['type'],
            'name': row['name'],
            'created_at': row['created_at'],
            'members': members,
            'owner': self._owner(row, members),
        }

    @_locked
    def ban_from_room(self, room_id: str, username: str) -> None:
        """방장이 내보낸 사람 - 오픈채팅방에 다시 들어올 수 없다 (§10.3)"""
        self._db.execute('INSERT OR IGNORE INTO room_bans(room_id, username, created_at) VALUES (?, ?, ?)',
                         (room_id, username, now_iso()))
        self._db.commit()

    @_locked
    def is_banned(self, room_id: str, username: str) -> bool:
        return self._one('SELECT 1 FROM room_bans WHERE room_id=? AND username=?', (room_id, username)) is not None

    @_locked
    def set_owner(self, room_id: str, username: str) -> None:
        self._db.execute('UPDATE rooms SET owner=? WHERE room_id=?', (username, room_id))
        self._db.commit()

    @_locked
    def is_member(self, room_id: str, username: str) -> bool:
        return self._one('SELECT 1 FROM room_members WHERE room_id=? AND username=?',
                         (room_id, username)) is not None

    @_locked
    def members(self, room_id: str) -> list[str]:
        return self._members(room_id)

    @_locked
    def room_ids_for_user(self, username: str) -> list[str]:
        return [r['room_id'] for r in self._all('SELECT room_id FROM room_members WHERE username=?', (username,))]

    def _room_dict(self, room_id: str, viewer: str) -> Optional[dict]:
        row = self._one('SELECT * FROM rooms WHERE room_id=?', (room_id,))
        if row is None:
            return None
        members = self._members(room_id)
        ptr_rows = self._all('SELECT username, last_read_msg_id FROM read_pointers WHERE room_id=?',
                             (room_id,))
        ptrs = {r['username']: int(r['last_read_msg_id']) for r in ptr_rows}
        read_pointers = {u: ptrs.get(u, 0) for u in members}
        # 차단한 사람의 메시지는 마지막 메시지/안 읽은 수에서 제외 (명세 §8.6)
        last = self._one(f'SELECT * FROM messages WHERE room_id=? AND {_NOT_BLOCKED} '
                         'ORDER BY msg_id DESC LIMIT 1', (room_id, viewer))
        notice = None
        if row['notice_msg_id'] is not None:
            nrow = self._one('SELECT * FROM messages WHERE msg_id=?', (row['notice_msg_id'],))
            notice = self._message_dict(nrow) if nrow else None
        unread = self._one(
            'SELECT COUNT(*) AS c FROM messages WHERE room_id=? AND msg_id>? AND sender!=? AND kind!=\'system\' '
            f'AND {_NOT_BLOCKED}',
            (room_id, read_pointers.get(viewer, 0), viewer, viewer),
        )['c']
        nick_rows = self._all('SELECT username, nickname FROM room_members WHERE room_id=? AND nickname IS NOT NULL',
                              (room_id,))
        return {
            'room_id': row['room_id'],
            'type': row['type'],
            'name': row['name'],
            'members': members,
            'owner': self._owner(row, members),
            'created_at': row['created_at'],
            'notice': notice,
            'last_message': self._message_dict(last) if last else None,
            'read_pointers': read_pointers,
            'unread_count': int(unread),
            'description': row['description'],
            'tags': self._json_list(row['tags']),
            'nicknames': {r['username']: r['nickname'] for r in nick_rows},
        }

    @_locked
    def room_dict(self, room_id: str, viewer: str) -> Optional[dict]:
        return self._room_dict(room_id, viewer)

    @_locked
    def rooms_for_user(self, username: str) -> list[dict]:
        rows = self._all('SELECT room_id FROM room_members WHERE username=?', (username,))
        rooms = [self._room_dict(r['room_id'], username) for r in rows]
        rooms = [r for r in rooms if r is not None]
        rooms.sort(key=lambda r: (r['last_message']['timestamp'] if r['last_message'] else r['created_at']),
                   reverse=True)
        return rooms

    # ------------------------------------------------------------------ 메시지
    def _poll_dict(self, poll_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM polls WHERE poll_id=?', (poll_id,))
        if row is None:
            return None
        texts = json.loads(row['options'])
        voters: list[list[str]] = [[] for _ in texts]
        for v in self._all('SELECT option_index, username FROM poll_votes WHERE poll_id=? ORDER BY rowid',
                           (poll_id,)):
            idx = int(v['option_index'])
            if 0 <= idx < len(voters):
                voters[idx].append(v['username'])
        return {
            'poll_id': int(row['poll_id']),
            'question': row['question'],
            'multiple': bool(row['multiple']),
            'closed': bool(row['closed']),
            'options': [{'text': t, 'voters': voters[i]} for i, t in enumerate(texts)],
        }

    def _expires_at(self, created_at: str) -> str:
        try:
            return (datetime.strptime(created_at, ISO_FMT) + self.retention).strftime(ISO_FMT)
        except (TypeError, ValueError):
            return created_at

    def _attachment_meta(self, attachment_id: int) -> Optional[dict]:
        row = self._one('SELECT attachment_id, name, mime, size, created_at, expired FROM attachments '
                        'WHERE attachment_id=?', (attachment_id,))
        if row is None:
            return None
        expires_at = self._expires_at(row['created_at'])
        return {'attachment_id': int(row['attachment_id']), 'name': row['name'],
                'mime': row['mime'], 'size': int(row['size']), 'expires_at': expires_at,
                'expired': bool(row['expired']) or expires_at <= now_iso()}

    def _message_dict(self, row: sqlite3.Row) -> dict:
        deleted = bool(row['deleted'])
        attachment = None
        poll = None
        if not deleted:
            if row['attachment_id'] is not None:
                attachment = self._attachment_meta(row['attachment_id'])
            if row['poll_id'] is not None:
                poll = self._poll_dict(row['poll_id'])
        return {
            'msg_id': int(row['msg_id']),
            'room_id': row['room_id'],
            'sender': row['sender'],
            'kind': row['kind'],
            'content': row['content'],
            'timestamp': row['timestamp'],
            'reply_to': json.loads(row['reply_to']) if row['reply_to'] else None,
            'mentions': json.loads(row['mentions']),
            'reactions': json.loads(row['reactions']),
            'deleted': deleted,
            'attachment': attachment,
            'event_id': int(row['event_id']) if row['event_id'] is not None else None,
            'poll': poll,
            'edited': bool(row['edited']) and not deleted,
            'edited_at': row['edited_at'] if not deleted else None,
            'silent': bool(row['silent']),
            'link_preview': (json.loads(row['link_preview']) if row['link_preview'] and not deleted else None),
            'call_id': int(row['call_id']) if row['call_id'] is not None and not deleted else None,
            'ladder': json.loads(row['ladder']) if row['ladder'] and not deleted else None,
            'post_id': int(row['post_id']) if row['post_id'] is not None and not deleted else None,
            'thread_root': int(row['thread_root']) if row['thread_root'] is not None else None,
            'thread_count': int(row['thread_count'] or 0),
            'thread_last_at': row['thread_last_at'],
        }

    @staticmethod
    def _preview(row: sqlite3.Row) -> str:
        if row['deleted']:
            return DELETED_CONTENT
        if row['kind'] == 'secret':
            return SECRET_PREVIEW   # 서버는 비밀채팅 내용을 해석하지 않는다
        if row['kind'] == 'image':
            return '사진'
        if row['kind'] == 'voice':
            return '음성메시지'
        if row['kind'] == 'contact':
            return '연락처'
        if row['kind'] == 'file' and not row['content']:
            return '파일'
        return (row['content'] or '')[:REPLY_PREVIEW_LEN]

    def _insert_message(self, room_id: str, sender: str, kind: str, content: str,
                        reply_to_id: Optional[int] = None, mentions: Iterable[str] = (),
                        attachment_id: Optional[int] = None, event_id: Optional[int] = None,
                        poll_id: Optional[int] = None, silent: bool = False, call_id: Optional[int] = None,
                        ladder: Optional[dict] = None, post_id: Optional[int] = None,
                        thread_root: Optional[int] = None) -> int:
        reply_to = None
        if reply_to_id is not None:
            orig = self._one('SELECT * FROM messages WHERE msg_id=? AND room_id=?', (reply_to_id, room_id))
            if orig is not None:
                reply_to = json.dumps({'msg_id': int(orig['msg_id']), 'sender': orig['sender'],
                                       'preview': self._preview(orig)}, ensure_ascii=False)
        cur = self._db.execute(
            'INSERT INTO messages(room_id, sender, kind, content, timestamp, reply_to, mentions, '
            'attachment_id, event_id, poll_id, silent, call_id, ladder, post_id, thread_root) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (room_id, sender, kind, content, now_iso(), reply_to,
             json.dumps(list(mentions), ensure_ascii=False), attachment_id, event_id, poll_id, int(bool(silent)),
             call_id, json.dumps(ladder, ensure_ascii=False) if ladder is not None else None, post_id, thread_root),
        )
        msg_id = int(cur.lastrowid)
        if thread_root is not None:
            self._db.execute('UPDATE messages SET thread_count=thread_count+1, thread_last_at=? WHERE msg_id=?',
                             (now_iso(), thread_root))
        if sender:
            # 발신자는 전송 즉시 자신의 read pointer 를 해당 메시지로 올린다.
            self._db.execute(
                'UPDATE read_pointers SET last_read_msg_id=MAX(last_read_msg_id, ?) '
                'WHERE room_id=? AND username=?', (msg_id, room_id, sender))
        return msg_id

    def _get_message(self, msg_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM messages WHERE msg_id=?', (msg_id,))
        return self._message_dict(row) if row else None

    @_locked
    def add_message(self, room_id: str, sender: str, kind: str, content: str,
                    reply_to_id: Optional[int] = None, mentions: Iterable[str] = (),
                    attachment_id: Optional[int] = None, event_id: Optional[int] = None,
                    silent: bool = False, call_id: Optional[int] = None, ladder: Optional[dict] = None,
                    post_id: Optional[int] = None, thread_root: Optional[int] = None) -> dict:
        msg_id = self._insert_message(room_id, sender, kind, content, reply_to_id, mentions,
                                      attachment_id, event_id, silent=silent, call_id=call_id,
                                      ladder=ladder, post_id=post_id, thread_root=thread_root)
        self._db.commit()
        return self._get_message(msg_id)

    @_locked
    def get_message(self, msg_id: int) -> Optional[dict]:
        return self._get_message(msg_id)

    @_locked
    def history(self, room_id: str, before_msg_id: Optional[int], limit: int,
                viewer: Optional[str] = None, after_msg_id: Optional[int] = None) -> tuple[list[dict], bool]:
        """viewer 를 주면 그 사람이 차단한 사용자의 메시지를 제외한다. after_msg_id 이후만 (대화 요약)"""
        sql, params = 'SELECT * FROM messages WHERE room_id=?', [room_id]
        if before_msg_id is not None:
            sql += ' AND msg_id<?'
            params.append(before_msg_id)
        if after_msg_id is not None:
            sql += ' AND msg_id>?'
            params.append(after_msg_id)
        if viewer is not None:
            sql += f' AND {_NOT_BLOCKED}'
            params.append(viewer)
        rows = self._all(sql + ' ORDER BY msg_id DESC LIMIT ?', params + [limit + 1])
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        return [self._message_dict(r) for r in rows], has_more

    @_locked
    def mentions_of(self, username: str, limit: int = 100) -> list[dict]:
        """내가 속한 방에서 나를 멘션한 메시지 (최신순, 삭제·차단 제외) - 멘션 모아보기 (§10.5)"""
        rows = self._all(
            'SELECT m.* FROM messages m JOIN room_members rm ON rm.room_id=m.room_id AND rm.username=? '
            'WHERE m.deleted=0 AND m.sender!=? AND m.mentions LIKE ? '
            'AND m.sender NOT IN (SELECT blocked FROM blocks WHERE owner=?) '
            'ORDER BY m.msg_id DESC LIMIT ?',
            (username, username, '%' + json.dumps(username, ensure_ascii=False) + '%', username, limit * 2))
        out = []
        for r in rows:   # LIKE 는 후보만 고르고, 실제 멘션 목록으로 다시 확인
            if username in json.loads(r['mentions'] or '[]'):
                out.append(self._message_dict(r))
            if len(out) >= limit:
                break
        return out

    @_locked
    def thread_replies(self, root_id: int, viewer: Optional[str] = None, limit: int = 500) -> list[dict]:
        """스레드 답글 (오래된 순, 최근 limit 개). viewer 가 차단한 사람 것은 뺀다 (§10.4)"""
        sql, params = 'SELECT * FROM messages WHERE thread_root=?', [root_id]
        if viewer is not None:
            sql += f' AND {_NOT_BLOCKED}'
            params.append(viewer)
        rows = self._all(sql + ' ORDER BY msg_id DESC LIMIT ?', params + [limit])
        return [self._message_dict(r) for r in reversed(rows)]

    @_locked
    def update_read(self, room_id: str, username: str, last_msg_id: int) -> int:
        """read pointer 를 올린다(감소 불가, 방의 최신 msg_id 로 상한). 최종 값 반환."""
        target = min(max(int(last_msg_id), 0), self._last_msg_id(room_id))
        self._db.execute(
            'INSERT INTO read_pointers(room_id, username, last_read_msg_id) VALUES (?, ?, ?) '
            'ON CONFLICT(room_id, username) DO UPDATE SET '
            'last_read_msg_id=MAX(last_read_msg_id, excluded.last_read_msg_id)',
            (room_id, username, target),
        )
        self._db.commit()
        row = self._one('SELECT last_read_msg_id FROM read_pointers WHERE room_id=? AND username=?',
                        (room_id, username))
        return int(row['last_read_msg_id'])

    @_locked
    def read_pointer(self, room_id: str, username: str) -> int:
        row = self._one('SELECT last_read_msg_id FROM read_pointers WHERE room_id=? AND username=?',
                        (room_id, username))
        return int(row['last_read_msg_id']) if row else 0

    @_locked
    def toggle_reaction(self, msg_id: int, username: str, emoji: str) -> dict:
        row = self._one('SELECT reactions FROM messages WHERE msg_id=?', (msg_id,))
        reactions: dict[str, list[str]] = json.loads(row['reactions'])
        users = reactions.get(emoji, [])
        if username in users:
            users.remove(username)
        else:
            users.append(username)
        if users:
            reactions[emoji] = users
        else:
            reactions.pop(emoji, None)
        self._db.execute('UPDATE messages SET reactions=? WHERE msg_id=?',
                         (json.dumps(reactions, ensure_ascii=False), msg_id))
        self._db.commit()
        return self._get_message(msg_id)

    @_locked
    def edit_message(self, msg_id: int, content: str) -> Optional[dict]:
        """메시지 수정 - 내용 교체, 수정됨 표시. 링크가 바뀌었을 수 있으므로 미리보기는 지운다.
        그 사이 삭제된 메시지면 None (삭제와 수정이 동시에 와도 삭제된 글이 되살아나지 않게)"""
        cur = self._db.execute('UPDATE messages SET content=?, edited=1, edited_at=?, link_preview=NULL '
                               'WHERE msg_id=? AND deleted=0', (content, now_iso(), msg_id))
        self._db.commit()
        return self._get_message(msg_id) if cur.rowcount else None

    @_locked
    def refresh_reply_previews(self, msg_id: int) -> list[dict]:
        """원문이 수정·삭제되면 그 메시지를 인용한 답장의 미리보기도 바꾼다 → 바뀐 답장들"""
        orig = self._one('SELECT * FROM messages WHERE msg_id=?', (msg_id,))
        if orig is None:
            return []
        rows = self._all("SELECT msg_id FROM messages WHERE room_id=? AND reply_to IS NOT NULL "
                         "AND json_extract(reply_to, '$.msg_id')=?", (orig['room_id'], msg_id))
        if not rows:
            return []
        self._db.execute("UPDATE messages SET reply_to=json_set(reply_to, '$.preview', ?) WHERE room_id=? "
                         "AND reply_to IS NOT NULL AND json_extract(reply_to, '$.msg_id')=?",
                         (self._preview(orig), orig['room_id'], msg_id))
        self._db.commit()
        return [self._get_message(r['msg_id']) for r in rows]

    @_locked
    def set_link_preview(self, msg_id: int, preview: dict) -> Optional[dict]:
        row = self._one('SELECT deleted FROM messages WHERE msg_id=?', (msg_id,))
        if row is None or row['deleted']:
            return None
        self._db.execute('UPDATE messages SET link_preview=? WHERE msg_id=?',
                         (json.dumps(preview, ensure_ascii=False), msg_id))
        self._db.commit()
        return self._get_message(msg_id)

    @_locked
    def take_briefing_slot(self, username: str, day: str) -> bool:
        """오늘 브리핑을 아직 안 보냈으면 표시하고 True (하루 1회)"""
        row = self._one('SELECT last_briefing FROM users WHERE username=?', (username,))
        if row is None or row['last_briefing'] == day:
            return False
        self._db.execute('UPDATE users SET last_briefing=? WHERE username=?', (day, username))
        self._db.commit()
        return True

    @_locked
    def unread_total(self, username: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM messages m JOIN room_members rm ON rm.room_id=m.room_id AND rm.username=? "
            "LEFT JOIN read_pointers rp ON rp.room_id=m.room_id AND rp.username=? "
            "WHERE m.sender NOT IN (?, '') AND m.kind!='system' AND m.msg_id > COALESCE(rp.last_read_msg_id, 0) "
            'AND m.sender NOT IN (SELECT blocked FROM blocks WHERE owner=?)',
            (username, username, username, username))
        return int(row['n']) if row else 0

    @_locked
    def delete_message(self, msg_id: int) -> dict:
        self._db.execute('UPDATE messages SET deleted=1, content=?, link_preview=NULL WHERE msg_id=?',
                         (DELETED_CONTENT, msg_id))
        self._db.commit()
        return self._get_message(msg_id)

    @_locked
    def search(self, username: str, query: str, room_id: Optional[str]) -> list[dict]:
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        sql = ('SELECT m.* FROM messages m JOIN room_members rm '
               'ON rm.room_id=m.room_id AND rm.username=? '
               'WHERE m.kind=\'text\' AND m.deleted=0 AND m.content LIKE ? ESCAPE \'\\\' '
               'AND m.sender NOT IN (SELECT blocked FROM blocks WHERE owner=?)')
        params: list[Any] = [username, f'%{escaped}%', username]
        if room_id is not None:
            sql += ' AND m.room_id=?'
            params.append(room_id)
        sql += ' ORDER BY m.msg_id DESC LIMIT ?'
        params.append(MAX_SEARCH_RESULTS)
        return [self._message_dict(r) for r in self._all(sql, params)]

    # ------------------------------------------------------------------ 첨부
    @_locked
    def get_attachment(self, attachment_id: int) -> Optional[dict]:
        """권한 검사 없이 첨부를 반환(서버 내부용)."""
        row = self._one('SELECT * FROM attachments WHERE attachment_id=?', (attachment_id,))
        if row is None:
            return None
        return {'attachment_id': int(row['attachment_id']), 'name': row['name'],
                'mime': row['mime'], 'size': int(row['size']), 'data': bytes(row['data'])}

    @_locked
    def save_attachment(self, name: str, mime: str, data: bytes, uploader: str) -> int:
        cur = self._db.execute(
            'INSERT INTO attachments(name, mime, size, data, uploader, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (name, mime, len(data), sqlite3.Binary(data), uploader, now_iso()),
        )
        self._db.commit()
        return int(cur.lastrowid)

    @_locked
    def get_attachment_for(self, attachment_id: int, username: str) -> Optional[dict]:
        """사용자가 접근 가능한(업로더이거나 참조 메시지가 있는 방의 멤버) 첨부만 반환.

        프로필 사진·진행 중인 펑(스토리) 사진은 누구나, 멀티프로필 사진은 소유자와 지정된 친구만 받을 수 있다.
        반환 dict 의 ``expired`` 가 True 면 보관 기간이 지난 파일이다(``data`` 는 비어 있을 수 있음).
        """
        row = self._one('SELECT * FROM attachments WHERE attachment_id=?', (attachment_id,))
        if row is None:
            return None
        is_avatar = (
            self._one('SELECT 1 FROM users WHERE avatar_id=? OR org_avatar_id=?',
                      (attachment_id, attachment_id)) is not None
            or self._one('SELECT 1 FROM stories WHERE attachment_id=?', (attachment_id,)) is not None
            or self._one('SELECT 1 FROM multi_profiles p LEFT JOIN multi_profile_viewers v '
                         'ON v.profile_id=p.profile_id WHERE p.avatar_id=? AND (p.owner=? OR v.viewer=?) LIMIT 1',
                         (attachment_id, username, username)) is not None)
        expired = bool(row['expired'])
        if not expired and not is_avatar and self._expires_at(row['created_at']) <= now_iso():
            expired = self._one('SELECT 1 FROM messages WHERE attachment_id=? LIMIT 1',
                                (attachment_id,)) is not None
        if row['uploader'] != username and not is_avatar:
            ok = self._one(
                'SELECT 1 FROM messages m JOIN room_members rm ON rm.room_id=m.room_id '
                'WHERE m.attachment_id=? AND m.deleted=0 AND rm.username=? LIMIT 1',
                (attachment_id, username))
            if ok is None:
                return None
        return {'attachment_id': int(row['attachment_id']), 'name': row['name'],
                'mime': row['mime'], 'size': int(row['size']), 'data': bytes(row['data']), 'expired': expired}

    # ------------------------------------------------------------------ 투표
    @_locked
    def create_poll(self, room_id: str, creator: str, question: str, options: list[str],
                    multiple: bool) -> dict:
        cur = self._db.execute(
            'INSERT INTO polls(room_id, creator, question, options, multiple, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (room_id, creator, question, json.dumps(options, ensure_ascii=False), int(multiple), now_iso()),
        )
        poll_id = int(cur.lastrowid)
        msg_id = self._insert_message(room_id, creator, 'poll', question, poll_id=poll_id)
        self._db.execute('UPDATE polls SET msg_id=? WHERE poll_id=?', (msg_id, poll_id))
        self._db.commit()
        return self._get_message(msg_id)

    @_locked
    def poll_meta(self, poll_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM polls WHERE poll_id=?', (poll_id,))
        if row is None:
            return None
        return {'poll_id': int(row['poll_id']), 'room_id': row['room_id'], 'creator': row['creator'],
                'closed': bool(row['closed']), 'multiple': bool(row['multiple']),
                'option_count': len(json.loads(row['options'])), 'msg_id': row['msg_id']}

    @_locked
    def vote(self, poll_id: int, username: str, option_indexes: list[int]) -> dict:
        self._db.execute('DELETE FROM poll_votes WHERE poll_id=? AND username=?', (poll_id, username))
        for idx in option_indexes:
            self._db.execute('INSERT OR IGNORE INTO poll_votes(poll_id, option_index, username) VALUES (?, ?, ?)',
                             (poll_id, idx, username))
        self._db.commit()
        msg_id = self._one('SELECT msg_id FROM polls WHERE poll_id=?', (poll_id,))['msg_id']
        return self._get_message(msg_id)

    @_locked
    def close_poll(self, poll_id: int) -> dict:
        self._db.execute('UPDATE polls SET closed=1 WHERE poll_id=?', (poll_id,))
        self._db.commit()
        msg_id = self._one('SELECT msg_id FROM polls WHERE poll_id=?', (poll_id,))['msg_id']
        return self._get_message(msg_id)

    # ------------------------------------------------------------------ 일정
    def _event_dict(self, event_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM events WHERE event_id=?', (event_id,))
        if row is None:
            return None
        att = self._all('SELECT username, response FROM event_attendees WHERE event_id=? ORDER BY rowid',
                        (event_id,))
        return {
            'event_id': int(row['event_id']),
            'room_id': row['room_id'],
            'creator': row['creator'],
            'title': row['title'],
            'description': row['description'],
            'location': row['location'],
            'start': row['start'],
            'end': row['end'],
            'all_day': bool(row['all_day']),
            'remind_minutes': row['remind_minutes'],
            'color': row['color'],
            'attendees': {a['username']: a['response'] for a in att},
            'created_at': row['created_at'],
        }

    @_locked
    def create_event(self, creator: str, fields: dict, attendees: list[str],
                     room_id: Optional[str]) -> tuple[dict, Optional[dict]]:
        """일정 생성. room_id 가 있으면 kind=event 메시지도 게시. (event, message|None) 반환."""
        cur = self._db.execute(
            'INSERT INTO events(room_id, creator, title, description, location, start, "end", all_day, '
            'remind_minutes, color, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (room_id, creator, fields['title'], fields['description'], fields['location'], fields['start'],
             fields['end'], int(fields['all_day']), fields['remind_minutes'], fields['color'], now_iso()),
        )
        event_id = int(cur.lastrowid)
        self._db.execute('INSERT INTO event_attendees(event_id, username, response) VALUES (?, ?, \'yes\')',
                         (event_id, creator))
        for u in attendees:
            if u != creator:
                self._db.execute(
                    'INSERT OR IGNORE INTO event_attendees(event_id, username, response) VALUES (?, ?, \'pending\')',
                    (event_id, u))
        message = None
        if room_id is not None:
            msg_id = self._insert_message(room_id, creator, 'event', fields['title'], event_id=event_id)
            message = self._get_message(msg_id)
        self._db.commit()
        return self._event_dict(event_id), message

    @_locked
    def get_event(self, event_id: int) -> Optional[dict]:
        return self._event_dict(event_id)

    @_locked
    def update_event(self, event_id: int, fields: dict) -> dict:
        allowed = ('title', 'description', 'location', 'start', 'end', 'all_day', 'remind_minutes', 'color')
        sets = [(k, fields[k]) for k in allowed if k in fields]
        if sets:
            sql = 'UPDATE events SET ' + ', '.join(f'"{k}"=?' for k, _ in sets) + ' WHERE event_id=?'
            self._db.execute(sql, [int(v) if k == 'all_day' else v for k, v in sets] + [event_id])
        if 'start' in fields or 'remind_minutes' in fields:
            # 알림 재무장
            self._db.execute('DELETE FROM reminders_sent WHERE event_id=?', (event_id,))
        self._db.commit()
        return self._event_dict(event_id)

    @_locked
    def delete_event(self, event_id: int) -> None:
        self._db.execute('DELETE FROM events WHERE event_id=?', (event_id,))
        self._db.commit()

    @_locked
    def rsvp(self, event_id: int, username: str, response: str) -> dict:
        self._db.execute('UPDATE event_attendees SET response=? WHERE event_id=? AND username=?',
                         (response, event_id, username))
        self._db.commit()
        return self._event_dict(event_id)

    @_locked
    def list_events(self, username: str, start: str, end: str) -> list[dict]:
        rows = self._all(
            'SELECT DISTINCT e.event_id FROM events e LEFT JOIN event_attendees a ON a.event_id=e.event_id '
            'WHERE (e.creator=? OR a.username=?) AND e.start<=? AND e."end">=? ORDER BY e.start, e.event_id',
            (username, username, end, start))
        return [self._event_dict(r['event_id']) for r in rows]

    @_locked
    def due_reminders(self, now: datetime, grace: timedelta) -> list[tuple[dict, int]]:
        """알림 시각이 지났고 아직 발송하지 않은 일정을 발송 처리 후 (event, minutes) 목록으로 반환."""
        lower = (now - grace).strftime(ISO_FMT)
        rows = self._all('SELECT event_id, start, remind_minutes FROM events '
                         'WHERE remind_minutes IS NOT NULL AND start>=?', (lower,))
        result = []
        for r in rows:
            try:
                start = datetime.strptime(r['start'], ISO_FMT)
            except ValueError:
                continue
            minutes = int(r['remind_minutes'])
            remind_at = start - timedelta(minutes=minutes)
            if not (remind_at <= now <= start + grace):
                continue
            cur = self._db.execute(
                'INSERT OR IGNORE INTO reminders_sent(event_id, remind_at, sent_at) VALUES (?, ?, ?)',
                (r['event_id'], remind_at.strftime(ISO_FMT), now_iso()))
            if cur.rowcount == 1:
                result.append((self._event_dict(r['event_id']), minutes))
        self._db.commit()
        return result

    # ------------------------------------------------------------------ 할 일
    def _todo_dict(self, todo_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM todos WHERE todo_id=?', (todo_id,))
        if row is None:
            return None
        return {'todo_id': int(row['todo_id']), 'owner': row['owner'], 'title': row['title'],
                'due': row['due'], 'done': bool(row['done']), 'created_at': row['created_at']}

    @_locked
    def create_todo(self, owner: str, title: str, due: Optional[str]) -> dict:
        cur = self._db.execute('INSERT INTO todos(owner, title, due, created_at) VALUES (?, ?, ?, ?)',
                               (owner, title, due, now_iso()))
        self._db.commit()
        return self._todo_dict(int(cur.lastrowid))

    @_locked
    def get_todo(self, todo_id: int) -> Optional[dict]:
        return self._todo_dict(todo_id)

    @_locked
    def update_todo(self, todo_id: int, fields: dict) -> dict:
        allowed = ('title', 'due', 'done')
        sets = [(k, fields[k]) for k in allowed if k in fields]
        if sets:
            sql = 'UPDATE todos SET ' + ', '.join(f'{k}=?' for k, _ in sets) + ' WHERE todo_id=?'
            self._db.execute(sql, [int(v) if k == 'done' else v for k, v in sets] + [todo_id])
            self._db.commit()
        return self._todo_dict(todo_id)

    @_locked
    def delete_todo(self, todo_id: int) -> None:
        self._db.execute('DELETE FROM todos WHERE todo_id=?', (todo_id,))
        self._db.commit()

    @_locked
    def list_todos(self, owner: str) -> list[dict]:
        rows = self._all('SELECT todo_id FROM todos WHERE owner=? ORDER BY done, COALESCE(due, \'9999\'), todo_id',
                         (owner,))
        return [self._todo_dict(r['todo_id']) for r in rows]

    # ------------------------------------------------------------------ 예약 메시지
    @_locked
    def add_scheduled(self, room_id: str, sender: str, send_at: str, payload: dict) -> int:
        cur = self._db.execute(
            'INSERT INTO scheduled_messages(room_id, sender, send_at, payload, created_at) VALUES (?, ?, ?, ?, ?)',
            (room_id, sender, send_at, json.dumps(payload, ensure_ascii=False), now_iso()))
        self._db.commit()
        return int(cur.lastrowid)

    @_locked
    def pop_due_scheduled(self, now: str) -> list[dict]:
        """발송 시각이 된 예약 메시지를 delivered 처리하고 반환(1회 보장)."""
        rows = self._all('SELECT * FROM scheduled_messages WHERE delivered=0 AND send_at<=? ORDER BY scheduled_id',
                         (now,))
        if rows:
            self._db.executemany('UPDATE scheduled_messages SET delivered=1 WHERE scheduled_id=?',
                                 [(r['scheduled_id'],) for r in rows])
            self._db.commit()
        return [{'scheduled_id': int(r['scheduled_id']), 'room_id': r['room_id'], 'sender': r['sender'],
                 'send_at': r['send_at'], 'payload': json.loads(r['payload'])} for r in rows]

    @_locked
    def discard_attachment(self, attachment_id: int) -> None:
        """보내지 못한 첨부(취소 · 실패한 예약 메시지)의 데이터를 지운다 - 참조하는 메시지가 없어 보관 기간 정리에도 안 걸린다"""
        self._db.execute("UPDATE attachments SET data=X'', expired=1 WHERE attachment_id=? AND attachment_id NOT IN "
                         '(SELECT attachment_id FROM messages WHERE attachment_id IS NOT NULL)', (attachment_id,))
        self._db.commit()

    # ------------------------------------------------------------------ v3: 비밀채팅 키
    @_locked
    def set_public_key(self, username: str, public_key: str) -> None:
        self._db.execute('UPDATE users SET public_key=? WHERE username=?', (public_key, username))
        self._db.commit()

    @_locked
    def public_key(self, username: str) -> Optional[str]:
        row = self._one('SELECT public_key FROM users WHERE username=?', (username,))
        return row['public_key'] if row else None

    # ------------------------------------------------------------------ v3: 오픈채팅
    @_locked
    def create_open_room(self, creator: str, name: str, description: str, tags: list[str],
                         nickname: str) -> str:
        room_id = uuid.uuid4().hex[:16]
        self._db.execute(
            "INSERT INTO rooms(room_id, type, name, created_at, description, tags, owner) "
            "VALUES (?, 'open', ?, ?, ?, ?, ?)",
            (room_id, name, now_iso(), description, json.dumps(tags, ensure_ascii=False), creator))
        self._add_member(room_id, creator, 0, nickname)
        self._db.commit()
        return room_id

    @_locked
    def join_open_room(self, room_id: str, username: str, nickname: str) -> None:
        self._add_member(room_id, username, self._last_msg_id(room_id), nickname)
        self._db.commit()

    @_locked
    def member_nickname(self, room_id: str, username: str) -> Optional[str]:
        row = self._one('SELECT nickname FROM room_members WHERE room_id=? AND username=?', (room_id, username))
        return row['nickname'] if row else None

    @_locked
    def search_open_rooms(self, query: str) -> list[dict]:
        """이름·소개·태그 LIKE 검색 (빈 검색어 = 전체). 참여 여부와 무관, 최신 방 우선."""
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like = f'%{escaped}%'
        rows = self._all(
            "SELECT r.room_id, r.name, r.description, r.tags, "
            "(SELECT COUNT(*) FROM room_members m WHERE m.room_id=r.room_id) AS member_count "
            "FROM rooms r WHERE r.type='open' AND (r.name LIKE ? ESCAPE '\\' OR r.description LIKE ? ESCAPE '\\' "
            "OR r.tags LIKE ? ESCAPE '\\') ORDER BY r.created_at DESC, r.rowid DESC LIMIT ?",
            (like, like, like, MAX_OPEN_SEARCH_RESULTS))
        return [{'room_id': r['room_id'], 'name': r['name'], 'description': r['description'],
                 'tags': self._json_list(r['tags']), 'member_count': int(r['member_count'])} for r in rows]

    # ------------------------------------------------------------------ v3: 차단
    @_locked
    def set_block(self, owner: str, target: str, blocked: bool) -> list[str]:
        if blocked:
            self._db.execute('INSERT OR IGNORE INTO blocks(owner, blocked, created_at) VALUES (?, ?, ?)',
                             (owner, target, now_iso()))
        else:
            self._db.execute('DELETE FROM blocks WHERE owner=? AND blocked=?', (owner, target))
        self._db.commit()
        return self._blocked_list(owner)

    def _blocked_list(self, owner: str) -> list[str]:
        return [r['blocked'] for r in self._all('SELECT blocked FROM blocks WHERE owner=? ORDER BY created_at, rowid',
                                                (owner,))]

    @_locked
    def blocked_list(self, owner: str) -> list[str]:
        return self._blocked_list(owner)

    @_locked
    def blockers_of(self, username: str) -> set[str]:
        """username 을 차단한 사람들."""
        return {r['owner'] for r in self._all('SELECT owner FROM blocks WHERE blocked=?', (username,))}

    # ------------------------------------------------------------------ v3: 톡서랍(전체)
    @_locked
    def drawer(self, username: str, kind: str, limit: int) -> list[dict]:
        cond = {'image': "m.kind='image'", 'file': "m.kind IN ('file', 'voice')",
                'link': "m.kind='text' AND (m.content LIKE '%http://%' OR m.content LIKE '%https://%')"}[kind]
        rows = self._all(
            'SELECT m.* FROM messages m JOIN room_members rm ON rm.room_id=m.room_id AND rm.username=? '
            f'WHERE {cond} AND m.deleted=0 AND m.sender NOT IN (SELECT blocked FROM blocks WHERE owner=?) '
            'ORDER BY m.msg_id DESC LIMIT ?', (username, username, limit))
        return [self._message_dict(r) for r in rows]

    # ------------------------------------------------------------------ v3: 보관 기간
    @_locked
    def purge_expired_attachments(self) -> list[dict]:
        """보관 기간이 지난 메시지 첨부의 데이터를 지우고 expired 표시. 영향받은 메시지 목록 반환.

        프로필/멀티프로필 사진은 대상이 아니다.
        """
        cutoff = (datetime.now() - self.retention).strftime(ISO_FMT)
        rows = self._all(
            'SELECT a.attachment_id FROM attachments a WHERE a.expired=0 AND a.created_at<=? '
            'AND EXISTS (SELECT 1 FROM messages m WHERE m.attachment_id=a.attachment_id) '
            'AND NOT EXISTS (SELECT 1 FROM users u WHERE a.attachment_id IN (u.avatar_id, u.org_avatar_id)) '
            'AND NOT EXISTS (SELECT 1 FROM multi_profiles p WHERE p.avatar_id=a.attachment_id)', (cutoff,))
        if not rows:
            return []
        ids = [int(r['attachment_id']) for r in rows]
        self._db.executemany("UPDATE attachments SET data=X'', expired=1 WHERE attachment_id=?",
                             [(i,) for i in ids])
        self._db.commit()
        marks = ','.join('?' * len(ids))
        msgs = self._all(f'SELECT * FROM messages WHERE deleted=0 AND attachment_id IN ({marks}) ORDER BY msg_id',
                         ids)
        return [self._message_dict(r) for r in msgs]

    # ------------------------------------------------------------------ v3: 펑(스토리)
    def _story_dict(self, row: sqlite3.Row, viewer: str) -> dict:
        viewers: list[str] = []
        if viewer == row['owner']:
            viewers = [r['viewer'] for r in self._all(
                'SELECT viewer FROM story_views WHERE story_id=? ORDER BY viewed_at, rowid', (row['story_id'],))]
        return {'story_id': int(row['story_id']), 'owner': row['owner'], 'kind': row['kind'],
                'content': row['content'],
                'attachment_id': int(row['attachment_id']) if row['attachment_id'] is not None else None,
                'bg_color': row['bg_color'], 'created_at': row['created_at'], 'expires_at': row['expires_at'],
                'viewers': viewers}

    @_locked
    def create_story(self, owner: str, kind: str, content: str, attachment_id: Optional[int], bg_color: str,
                     ttl_seconds: float) -> dict:
        now = datetime.now()
        cur = self._db.execute(
            'INSERT INTO stories(owner, kind, content, attachment_id, bg_color, created_at, expires_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (owner, kind, content, attachment_id, bg_color, now.strftime(ISO_FMT),
             (now + timedelta(seconds=ttl_seconds)).strftime(ISO_FMT)))
        self._db.commit()
        return self._story_dict(self._one('SELECT * FROM stories WHERE story_id=?', (cur.lastrowid,)), owner)

    @_locked
    def get_story(self, story_id: int, viewer: str) -> Optional[dict]:
        """만료되지 않은 스토리만 반환."""
        row = self._one('SELECT * FROM stories WHERE story_id=? AND expires_at>?', (story_id, now_iso()))
        return self._story_dict(row, viewer) if row else None

    @_locked
    def list_stories(self, viewer: str) -> list[dict]:
        rows = self._all('SELECT * FROM stories WHERE expires_at>? ORDER BY story_id DESC', (now_iso(),))
        return [self._story_dict(r, viewer) for r in rows]

    @_locked
    def add_story_view(self, story_id: int, viewer: str) -> None:
        self._db.execute('INSERT OR IGNORE INTO story_views(story_id, viewer, viewed_at) VALUES (?, ?, ?)',
                         (story_id, viewer, now_iso()))
        self._db.commit()

    def _delete_story(self, story_id: int) -> None:
        row = self._one('SELECT attachment_id FROM stories WHERE story_id=?', (story_id,))
        self._db.execute('DELETE FROM stories WHERE story_id=?', (story_id,))
        if row is not None and row['attachment_id'] is not None:
            self._db.execute('DELETE FROM attachments WHERE attachment_id=?', (row['attachment_id'],))

    @_locked
    def delete_story(self, story_id: int) -> None:
        self._delete_story(story_id)
        self._db.commit()

    @_locked
    def pop_expired_stories(self) -> list[int]:
        ids = [int(r['story_id']) for r in self._all('SELECT story_id FROM stories WHERE expires_at<=?',
                                                     (now_iso(),))]
        for sid in ids:
            self._delete_story(sid)
        if ids:
            self._db.commit()
        return ids

    # ------------------------------------------------------------------ v3: 멀티프로필
    def _multi_profiles(self, owner: str) -> list[dict]:
        rows = self._all('SELECT * FROM multi_profiles WHERE owner=? ORDER BY profile_id', (owner,))
        out = []
        for r in rows:
            viewers = [v['viewer'] for v in self._all(
                'SELECT viewer FROM multi_profile_viewers WHERE profile_id=? ORDER BY rowid', (r['profile_id'],))]
            out.append({'profile_id': int(r['profile_id']), 'name': r['name'], 'display_name': r['display_name'],
                        'status_message': r['status_message'],
                        'avatar_id': int(r['avatar_id']) if r['avatar_id'] is not None else None,
                        'viewers': viewers})
        return out

    @_locked
    def list_multi_profiles(self, owner: str) -> list[dict]:
        return self._multi_profiles(owner)

    @_locked
    def replace_multi_profiles(self, owner: str, profiles: list[dict]) -> list[dict]:
        """멀티프로필 전체 교체. 각 항목: {profile_id|None, name, display_name, status_message,
        avatar_id(새 값) 또는 avatar_keep:True, viewers}. 목록에 없는 기존 프로필은 삭제."""
        existing = {int(r['profile_id']): r for r in
                    self._all('SELECT * FROM multi_profiles WHERE owner=?', (owner,))}
        keep: set[int] = set()
        for p in profiles:
            pid = p.get('profile_id')
            if pid in existing:
                avatar = existing[pid]['avatar_id'] if p.get('avatar_keep') else p.get('avatar_id')
                self._db.execute('UPDATE multi_profiles SET name=?, display_name=?, status_message=?, avatar_id=? '
                                 'WHERE profile_id=?',
                                 (p['name'], p['display_name'], p['status_message'], avatar, pid))
                self._db.execute('DELETE FROM multi_profile_viewers WHERE profile_id=?', (pid,))
            else:
                cur = self._db.execute(
                    'INSERT INTO multi_profiles(owner, name, display_name, status_message, avatar_id, created_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (owner, p['name'], p['display_name'], p['status_message'],
                     None if p.get('avatar_keep') else p.get('avatar_id'), now_iso()))
                pid = int(cur.lastrowid)
            keep.add(pid)
            self._db.executemany('INSERT OR IGNORE INTO multi_profile_viewers(profile_id, viewer) VALUES (?, ?)',
                                 [(pid, v) for v in p['viewers']])
        for pid in set(existing) - keep:
            self._db.execute('DELETE FROM multi_profiles WHERE profile_id=?', (pid,))
        self._db.commit()
        return self._multi_profiles(owner)

    @staticmethod
    def _override(row: sqlite3.Row) -> dict:
        return {'display_name': row['display_name'], 'status_message': row['status_message'],
                'avatar_id': int(row['avatar_id']) if row['avatar_id'] is not None else None}

    @_locked
    def profile_overrides_for_viewer(self, viewer: str) -> dict[str, dict]:
        """viewer 에게 보여 줄 {소유자: 덮어쓸 필드} (쿼리 1회)."""
        rows = self._all('SELECT p.owner, p.display_name, p.status_message, p.avatar_id FROM multi_profiles p '
                         'JOIN multi_profile_viewers v ON v.profile_id=p.profile_id WHERE v.viewer=?', (viewer,))
        return {r['owner']: self._override(r) for r in rows}

    @_locked
    def profile_overrides_for_owner(self, owner: str) -> dict[str, dict]:
        """owner 의 멀티프로필이 지정된 {viewer: 덮어쓸 필드} (쿼리 1회)."""
        rows = self._all('SELECT v.viewer, p.display_name, p.status_message, p.avatar_id FROM multi_profiles p '
                         'JOIN multi_profile_viewers v ON v.profile_id=p.profile_id WHERE p.owner=?', (owner,))
        return {r['viewer']: self._override(r) for r in rows}

    # ------------------------------------------------------------------ v3: 톡게시판
    def _post_dict(self, post_id: int) -> Optional[dict]:
        row = self._one('SELECT * FROM board_posts WHERE post_id=?', (post_id,))
        if row is None:
            return None
        comments = [{'comment_id': int(c['comment_id']), 'author': c['author'], 'content': c['content'],
                     'created_at': c['created_at']}
                    for c in self._all('SELECT * FROM board_comments WHERE post_id=? ORDER BY comment_id', (post_id,))]
        return {'post_id': int(row['post_id']), 'room_id': row['room_id'], 'author': row['author'],
                'title': row['title'], 'content': row['content'], 'created_at': row['created_at'],
                'comments': comments}

    @_locked
    def create_post(self, room_id: str, author: str, title: str, content: str) -> tuple[dict, dict]:
        cur = self._db.execute('INSERT INTO board_posts(room_id, author, title, content, created_at) '
                               'VALUES (?, ?, ?, ?, ?)', (room_id, author, title, content, now_iso()))
        post_id = int(cur.lastrowid)
        msg_id = self._insert_message(room_id, author, 'board', title, post_id=post_id)
        self._db.execute('UPDATE board_posts SET msg_id=? WHERE post_id=?', (msg_id, post_id))
        self._db.commit()
        return self._post_dict(post_id), self._get_message(msg_id)

    @_locked
    def get_post(self, post_id: int) -> Optional[dict]:
        post = self._post_dict(post_id)
        if post is not None:
            post['msg_id'] = self._one('SELECT msg_id FROM board_posts WHERE post_id=?', (post_id,))['msg_id']
        return post

    @_locked
    def list_posts(self, room_id: str) -> list[dict]:
        rows = self._all('SELECT post_id FROM board_posts WHERE room_id=? ORDER BY post_id DESC', (room_id,))
        return [self._post_dict(r['post_id']) for r in rows]

    @_locked
    def add_comment(self, post_id: int, author: str, content: str) -> dict:
        self._db.execute('INSERT INTO board_comments(post_id, author, content, created_at) VALUES (?, ?, ?, ?)',
                         (post_id, author, content, now_iso()))
        self._db.commit()
        return self._post_dict(post_id)

    @_locked
    def delete_post(self, post_id: int) -> Optional[dict]:
        """게시글 삭제 + 게시 알림 메시지를 삭제 처리. 갱신된 메시지(없으면 None) 반환."""
        row = self._one('SELECT msg_id FROM board_posts WHERE post_id=?', (post_id,))
        self._db.execute('DELETE FROM board_posts WHERE post_id=?', (post_id,))
        message = None
        if row is not None and row['msg_id'] is not None:
            self._db.execute('UPDATE messages SET deleted=1, content=? WHERE msg_id=?', (DELETED_CONTENT, row['msg_id']))
            message = self._get_message(row['msg_id'])
        self._db.commit()
        return message

    # ------------------------------------------------------------------ v3: 통화
    @_locked
    def log_call(self, call_id: int, room_id: str, mode: str, host: str, started_at: str, duration: int,
                 participants: list[str]) -> None:
        """끝난 통화 기록 (채팅탭 '통화' 폴더, 명세 §9.4)"""
        if self._one('SELECT 1 FROM rooms WHERE room_id=?', (room_id,)) is None:
            return
        self._db.execute('INSERT OR REPLACE INTO call_log(call_id, room_id, mode, host, started_at, duration, '
                         'participants) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (call_id, room_id, mode, host, started_at, int(duration),
                          json.dumps(participants, ensure_ascii=False)))
        self._db.commit()

    @_locked
    def call_history(self, username: str, limit: int) -> list[dict]:
        """내가 속한 방의 통화 기록 (최근 순)"""
        rows = self._all(
            'SELECT c.* FROM call_log c JOIN room_members rm ON rm.room_id=c.room_id AND rm.username=? '
            'ORDER BY c.started_at DESC, c.call_id DESC LIMIT ?', (username, limit))
        return [{'call_id': int(r['call_id']), 'room_id': r['room_id'], 'mode': r['mode'], 'host': r['host'],
                 'started_at': r['started_at'], 'duration_seconds': int(r['duration']),
                 'participants': json.loads(r['participants'])} for r in rows]

    @_locked
    def max_call_id(self) -> int:
        row = self._one('SELECT MAX(call_id) AS m FROM messages')
        logged = self._one('SELECT MAX(call_id) AS m FROM call_log')
        return max(int(row['m'] or 0), int(logged['m'] or 0))
