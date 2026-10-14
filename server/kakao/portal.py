# -*- coding: utf-8 -*-
"""
사내 포털(https://portal.kcins.co.kr) RAG 연동 - 'AI 비서'의 1차 두뇌 (명세 §7.6.1)

- 질문: AI 비서에게 온 질문을 포털 RAG API 로 보내 답을 받는다.
        기본 API 는 포털의 /api/chat/ask-as (2026-10-14): 공유 비밀 X-Service-Token 으로 인증하고 질문자의
        이메일(= 포털 계정)을 함께 보내면, 포털이 그 사람 권한(내 부서 문서 + 전사 공개 문서)으로 gemma4 가
        답하고 대화도 그 사람의 포털 대화 목록에 남긴다. 그래서 메신저가 따로 대화를 저장하지 않는다
        (save_conversations 기본 false).
        포털에 닿지 않거나 오류면 메신저 자체 RAG(RagService)로 답한다 (답 끝에 안내 한 줄).
- 저장: 질문·답변 한 쌍을 포털 대화 저장 API 로 보낸다. 보내기 전에 SQLite 보낼 함(outbox)에 먼저 넣고,
        실패하면 백그라운드 스레드가 다시 보낸다 (포털 장애 · 서버 재시작에도 대화 기록을 잃지 않게).
- 설정: server_data/portal.json 또는 환경변수 (환경변수가 우선). 포털 API 모양이 달라도 경로 · 필드 이름 ·
        인증 헤더 · 요청 방식(api_style)을 설정만으로 맞춘다.

portal.json 예 (모든 키 생략 가능)
  {"enabled": true, "base_url": "http://127.0.0.1:6031", "token": "<포털 .env 의 SERVICE_TOKEN>",
   "api_style": "kcs",                      # kcs | openai (OpenAI 호환 /chat/completions)
   "ask_path": "/api/chat/ask-as", "save_path": "/api/rag/conversations",
   "auth_header": "X-Service-Token", "auth_scheme": "",  "save_conversations": false,
   "model": "", "auth_header": "Authorization", "auth_scheme": "Bearer",
   "headers": {"X-Client": "kcs-talk"}, "timeout": 30, "verify_tls": true,
   "save_conversations": true, "retry_seconds": 60}

환경변수
  KCS_PORTAL=off              포털 연동 끄기 (메신저 자체 RAG 만)
  KCS_PORTAL_URL              포털 주소
  KCS_PORTAL_TOKEN            API 토큰 (Authorization: Bearer ...)
  KCS_PORTAL_ASK_PATH / KCS_PORTAL_SAVE_PATH / KCS_PORTAL_STYLE / KCS_PORTAL_MODEL / KCS_PORTAL_TIMEOUT
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger('kakao.portal')

DEFAULT_BASE_URL = 'https://portal.kcins.co.kr'
SOURCE_TAG = 'kcs-talk'
FALLBACK_NOTE = '\n\n(사내 포털에 연결하지 못해 메신저 자료로 답했어요)'
MAX_SAVE_ATTEMPTS = 1000             # 이보다 많이 실패한 기록은 더 보내지 않는다 (보낼 함에는 남김)
DOWN_COOLDOWN = 30.0                 # 질문이 연결 실패하면 이 시간 동안은 포털을 건너뛰고 바로 자체 RAG

DEFAULTS: dict[str, Any] = {
    'enabled': True,
    'base_url': DEFAULT_BASE_URL,
    'token': '',
    'api_style': 'kcs',
    'ask_path': '/api/chat/ask-as',            # 포털 kcs-rag 의 서비스 질문 API (사용자 대신 질문)
    'save_path': '/api/rag/conversations',
    'model': '',
    'auth_header': 'X-Service-Token',
    'auth_scheme': '',
    'headers': {},
    'timeout': 90.0,                           # 검색 + 리랭크 + gemma4 생성 (보통 5~15초, 웹 검색이 붙으면 더)
    'verify_tls': True,
    'save_conversations': False,               # ask-as 가 포털 쪽에 대화를 남긴다
    'retry_seconds': 60.0,
}

_ENV_KEYS = {
    'KCS_PORTAL_URL': 'base_url', 'KCS_PORTAL_TOKEN': 'token', 'KCS_PORTAL_ASK_PATH': 'ask_path',
    'KCS_PORTAL_SAVE_PATH': 'save_path', 'KCS_PORTAL_STYLE': 'api_style', 'KCS_PORTAL_MODEL': 'model',
    'KCS_PORTAL_TIMEOUT': 'timeout',
}


class PortalError(Exception):
    """포털 호출 실패 (연결 · HTTP 오류 · 응답에서 답을 찾지 못함)"""


def load_config(path: Optional[str] = None, env: Optional[Any] = None) -> Optional[dict]:
    """portal.json + 환경변수 → 설정 dict. 꺼져 있으면 None"""
    environ: Any = os.environ if env is None else env
    config = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                config.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError) as exc:
            logger.warning('portal.json 을 읽지 못했습니다 (%s) - 기본 설정 사용', exc)
    for key, name in _ENV_KEYS.items():
        if environ.get(key):
            config[name] = environ[key]
    if str(environ.get('KCS_PORTAL', '')).lower() in ('off', '0', 'false', 'no'):
        config['enabled'] = False
    if not config.get('enabled') or not str(config.get('base_url') or '').strip():
        return None
    config['base_url'] = str(config['base_url']).rstrip('/')
    try:
        config['timeout'] = max(1.0, float(config['timeout']))
        config['retry_seconds'] = max(1.0, float(config['retry_seconds']))
    except (TypeError, ValueError):
        config['timeout'], config['retry_seconds'] = DEFAULTS['timeout'], DEFAULTS['retry_seconds']
    if config['api_style'] not in ('kcs', 'openai'):
        config['api_style'] = 'kcs'
    if not isinstance(config.get('headers'), dict):
        config['headers'] = {}
    return config


def _dig(data: Any, *paths: str) -> Any:
    """'data.answer' 같은 점 경로 중 처음으로 값이 있는 것"""
    for path in paths:
        cur = data
        for part in path.split('.'):
            if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            elif isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, '', [], {}):
            return cur
    return None


def parse_answer(data: Any) -> tuple[str, list[str], Optional[str]]:
    """포털 응답 → (답변, 출처 목록, 대화 id). 흔한 응답 모양을 모두 받는다"""
    if isinstance(data, str):
        return data.strip(), [], None
    text = _dig(data, 'answer', 'data.answer', 'result.answer', 'response', 'data.response', 'output',
                'text', 'content', 'message.content', 'choices.0.message.content', 'data.content',
                'result', 'data.text', 'message')
    if not isinstance(text, str) or not text.strip():
        raise PortalError('포털 응답에서 답변을 찾지 못했습니다.')
    sources: list[str] = []
    raw = _dig(data, 'sources', 'data.sources', 'references', 'data.references', 'citations',
               'data.citations', 'documents', 'data.documents') or []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            label = item
        elif isinstance(item, dict):
            title = _dig(item, 'title', 'name', 'document', 'file_name', 'filename', 'source', 'metadata.title',
                         'metadata.source')
            url = _dig(item, 'url', 'link', 'href', 'metadata.url')
            label = ' '.join(str(x) for x in (title, url) if x)
        else:
            continue
        label = ' '.join(label.split())
        if label and label not in sources:
            sources.append(label)
    conv = _dig(data, 'conversation_id', 'conversationId', 'data.conversation_id', 'data.conversationId',
                'session_id', 'sessionId', 'chat_id', 'chatId', 'id')
    return text.strip(), sources[:8], str(conv) if conv is not None else None


class PortalClient:
    """포털 HTTP 호출 (표준 라이브러리 urllib 만 사용)"""

    def __init__(self, config: dict, opener: Optional[Callable[..., Any]] = None):
        self.config = config
        self._opener = opener or urllib.request.urlopen

    def _url(self, path: str) -> str:
        if path.startswith(('http://', 'https://')):
            return path
        return self.config['base_url'] + '/' + path.lstrip('/')

    def _headers(self) -> dict:
        headers = {'Content-Type': 'application/json; charset=utf-8', 'Accept': 'application/json',
                   'User-Agent': 'KCS-Talk/2.0'}
        token = str(self.config.get('token') or '')
        if token:
            scheme = str(self.config.get('auth_scheme') or '')
            headers[str(self.config.get('auth_header') or 'Authorization')] = f'{scheme} {token}'.strip()
        headers.update({str(k): str(v) for k, v in self.config.get('headers', {}).items()})
        return headers

    def post(self, path: str, payload: dict) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(self._url(path), data=body, headers=self._headers(), method='POST')
        kwargs: dict[str, Any] = {'timeout': self.config['timeout']}
        if self._url(path).startswith('https://') and not self.config.get('verify_tls', True):
            kwargs['context'] = ssl._create_unverified_context()  # 사내 사설 인증서용 (설정으로만)
        try:
            with self._opener(req, **kwargs) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise PortalError(f'포털 HTTP {exc.code}') from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PortalError(f'포털 연결 실패: {getattr(exc, "reason", exc)}') from None
        text = raw.decode('utf-8', errors='replace').strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return text   # 일반 텍스트 응답

    def ask(self, question: str, user: dict, history: list[dict], conversation_id: Optional[str]) -> tuple:
        if self.config['api_style'] == 'openai':
            messages = [{'role': 'system', 'content': f"사내 메신저 KCS Talk 사용자 {user['name']}({user['username']})의 질문입니다."}]
            messages += history[-10:] + [{'role': 'user', 'content': question}]
            payload: dict[str, Any] = {'messages': messages, 'stream': False, 'user': user['username']}
            if self.config.get('model'):
                payload['model'] = self.config['model']
            path = self.config['ask_path'] if self.config['ask_path'] != DEFAULTS['ask_path'] \
                else '/api/chat/completions'
        else:
            payload = {'question': question, 'query': question, 'user': user['username'],
                       'user_name': user['name'], 'email': user.get('email') or '', 'room_id': user.get('room_id'),
                       'conversation_id': conversation_id, 'history': history[-10:], 'source': SOURCE_TAG}
            if self.config.get('model'):
                payload['model'] = self.config['model']
            path = self.config['ask_path']
        return parse_answer(self.post(path, payload))

    def save(self, record: dict) -> Any:
        return self.post(self.config['save_path'], record)


class Outbox:
    """포털로 보낼 대화 기록 보관함 (SQLite). 보내면 sent_at 을 채우고, 실패하면 attempts 를 올린다"""

    def __init__(self, db_path: str):
        if db_path != ':memory:':
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS portal_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, created_at TEXT,
                attempts INTEGER DEFAULT 0, last_error TEXT, sent_at TEXT);
            CREATE INDEX IF NOT EXISTS portal_outbox_pending ON portal_outbox(sent_at);
            CREATE TABLE IF NOT EXISTS portal_conversations (
                room_id TEXT, username TEXT, conversation_id TEXT, PRIMARY KEY (room_id, username));
        """)
        self._db.commit()

    def add(self, payload: dict) -> int:
        with self._lock:
            cur = self._db.execute('INSERT INTO portal_outbox(payload, created_at) VALUES (?, ?)',
                                   (json.dumps(payload, ensure_ascii=False), _now()))
            self._db.commit()
            return cur.lastrowid or 0

    def pending(self, limit: int = 50) -> list[tuple[int, dict]]:
        with self._lock:
            rows = self._db.execute('SELECT id, payload FROM portal_outbox WHERE sent_at IS NULL AND attempts < ? '
                                    'ORDER BY id LIMIT ?', (MAX_SAVE_ATTEMPTS, limit)).fetchall()
        return [(r['id'], json.loads(r['payload'])) for r in rows]

    def mark_sent(self, row_id: int) -> None:
        with self._lock:
            self._db.execute('UPDATE portal_outbox SET sent_at=?, last_error=NULL WHERE id=?', (_now(), row_id))
            self._db.commit()

    def mark_failed(self, row_id: int, error: str) -> None:
        with self._lock:
            self._db.execute('UPDATE portal_outbox SET attempts=attempts+1, last_error=? WHERE id=?',
                             (error[:500], row_id))
            self._db.commit()

    def stats(self) -> dict:
        with self._lock:
            row = self._db.execute('SELECT COUNT(*) AS total, SUM(sent_at IS NULL) AS pending FROM portal_outbox') \
                .fetchone()
        return {'total': row['total'] or 0, 'pending': row['pending'] or 0}

    def conversation_id(self, room_id: str, username: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute('SELECT conversation_id FROM portal_conversations WHERE room_id=? AND username=?',
                                   (room_id, username)).fetchone()
        return row['conversation_id'] if row else None

    def set_conversation_id(self, room_id: str, username: str, conversation_id: str) -> None:
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO portal_conversations VALUES (?, ?, ?)',
                             (room_id, username, conversation_id))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


class PortalRag:
    """RagService 를 감싸 포털을 먼저 쓰는 AI 비서. RagService 와 같은 메서드를 그대로 제공한다
    (index_message · add_document 등은 메신저 자체 RAG 로 위임 - 포털이 꺼졌을 때의 대체 답변용)"""

    accepts_context = True       # 서버가 answer() 에 방 · 이름 정보를 넘겨 준다

    def __init__(self, local: Any, config: dict, outbox_path: str,
                 client: Optional[PortalClient] = None, start_worker: bool = True):
        self.local = local
        self.config = config
        self.client = client or PortalClient(config)
        self.outbox = Outbox(outbox_path)
        self._down_until = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._worker: Optional[threading.Thread] = None
        if start_worker and config.get('save_conversations', True):
            self._worker = threading.Thread(target=self._flush_loop, daemon=True, name='kakao-portal-outbox')
            self._worker.start()

    # 메신저 자체 RAG 로 위임 -------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name == 'local':          # __init__ 전에 불리면 무한 재귀 방지
            raise AttributeError(name)
        return getattr(self.local, name)

    def index_message(self, message: dict) -> None:
        if self.local is not None:
            self.local.index_message(message)

    def remove_message(self, msg_id: int) -> None:
        if self.local is not None:
            self.local.remove_message(msg_id)

    @property
    def _llm_failed(self) -> bool:
        return getattr(self.local, '_llm_failed', False)

    @_llm_failed.setter
    def _llm_failed(self, value: bool) -> None:
        if self.local is not None:
            self.local._llm_failed = value

    # 질문 -------------------------------------------------------------------------
    def answer(self, question: str, asker: str, room_ids: Any, history: list, room_id: Optional[str] = None,
               asker_name: str = '', room_name: str = '', bot_name: str = '', asker_email: str = '') -> str:
        user = {'username': asker, 'name': asker_name or asker, 'room_id': room_id, 'email': asker_email}
        turns = _history_turns(history, bot_name)
        if turns and turns[-1]['role'] == 'user' and question.strip() in turns[-1]['content']:
            turns = turns[:-1]       # 방 기록의 마지막은 지금 질문 자체
        source, text = 'portal', None
        conv = self.outbox.conversation_id(room_id, asker) if room_id else None
        if time.monotonic() >= self._down_until:
            try:
                text, sources, new_conv = self.client.ask(question, user, turns, conv)
                if sources:
                    text += '\n\n📎 출처 (사내 포털)\n' + '\n'.join(f'[{i}] {s}' for i, s in enumerate(sources, 1))
                if new_conv and room_id and new_conv != conv:
                    self.outbox.set_conversation_id(room_id, asker, new_conv)
                    conv = new_conv
            except PortalError as exc:
                logger.warning('포털 RAG 질문 실패, 메신저 자체 RAG 사용: %s', exc)
                self._down_until = time.monotonic() + DOWN_COOLDOWN
                text = None
            except Exception:
                logger.exception('포털 RAG 질문 오류, 메신저 자체 RAG 사용')
                text = None
        if text is None:
            source = 'local'
            text = (self.local.answer(question, asker, room_ids, history) if self.local is not None
                    else '지금은 답변할 수 없어요. 잠시 후 다시 물어봐 주세요.')
            if self.local is not None:
                text += FALLBACK_NOTE
        self.record(question, text, user, room_name=room_name, conversation_id=conv, source=source)
        return text

    # 대화 저장 ----------------------------------------------------------------------
    def record(self, question: str, answer: str, user: dict, room_name: str = '',
               conversation_id: Optional[str] = None, source: str = 'portal') -> None:
        if not self.config.get('save_conversations', True):
            return
        now = _now()
        payload = {
            'source': SOURCE_TAG, 'conversation_id': conversation_id,
            'user': user['username'], 'user_name': user.get('name') or user['username'],
            'room_id': user.get('room_id'), 'room_name': room_name,
            'answered_by': source,          # portal: 포털 RAG 답변 / local: 메신저 자체 RAG 답변
            'messages': [{'role': 'user', 'content': question, 'timestamp': now},
                         {'role': 'assistant', 'content': answer, 'timestamp': now}],
            'created_at': now,
        }
        self.outbox.add(payload)
        self._wake.set()

    def flush(self) -> int:
        """보낼 함의 기록을 포털로 보낸다. 보낸 개수. 하나라도 연결 실패면 그 뒤는 다음 차례로 미룬다"""
        sent = 0
        for row_id, payload in self.outbox.pending():
            if self._stop.is_set():
                break
            try:
                self.client.save(payload)
            except PortalError as exc:
                self.outbox.mark_failed(row_id, str(exc))
                logger.info('포털 대화 저장 실패 (나중에 다시 보냄): %s', exc)
                break
            except Exception as exc:
                self.outbox.mark_failed(row_id, repr(exc))
                logger.exception('포털 대화 저장 오류')
                break
            self.outbox.mark_sent(row_id)
            sent += 1
        return sent

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.flush()
            except Exception:
                logger.exception('포털 보낼 함 처리 오류')
            self._wake.wait(self.config['retry_seconds'])
            self._wake.clear()

    def status(self) -> dict:
        return {'base_url': self.config['base_url'], 'api_style': self.config['api_style'],
                'reachable': time.monotonic() >= self._down_until, **self.outbox.stats()}

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
        self.outbox.close()          # 못 보낸 기록은 보낼 함에 남아 다음 시작 때 보낸다
        close_local = getattr(self.local, 'close', None)
        if callable(close_local):
            close_local()


def _history_turns(history: list, bot_name: str) -> list[dict]:
    """방의 최근 메시지 → [{role, content}] (포털이 앞 대화 맥락을 알 수 있게)"""
    turns = []
    for m in history or []:
        if m.get('kind') != 'text' or m.get('deleted') or not (m.get('content') or '').strip():
            continue
        role = 'assistant' if bot_name and m.get('sender') == bot_name else 'user'
        turns.append({'role': role, 'content': m['content']})
    return turns[-10:]
