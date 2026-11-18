# -*- coding: utf-8 -*-
"""
KCS Messenger 서버 (카카오톡 기능 프로토콜 v2).

프로토콜 계약: claudedocs/KAKAO_FEATURE_SPEC.md §2~§5
- TCP + NDJSON(UTF-8). 줄바꿈 없이 이어 붙은 JSON(구버전 클라이언트)도 허용.
- 클라이언트마다 수신 스레드 1개, 소켓마다 송신 Lock.
- 스케줄러 스레드가 예약 메시지와 일정 알림을 처리.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import collections
import functools
import json
import logging
import os
import sys
import queue
import random
import re
import select
import socket
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from . import file_policy
from .admin_handlers import AdminHandlers
from .protocol import (ACCOUNT_DISABLED, ADMIN_ONLY, EMAIL_RE, EMPLOYEE_NO_RE, INT_LIMIT,  # noqa: F401 (예전 import 경로 호환)
                       ProtocolError, _bool, _norm_iso, _opt_int, _opt_name, _opt_str, _opt_tag, _req_int, _req_name,
                       _req_str, _str_list, _tag_ok, _utf8_ok, clean_file_name, parse_iso)
from . import summary as chat_summary
from .store import BOT_USERNAME, ISO_FMT, PRESENCES, Store, now_iso

logger = logging.getLogger('kakao.server')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 빌드한 서버(PyInstaller)는 소스가 임시 폴더에 풀리므로, 데이터는 실행 파일 옆 server_data 에 둔다 (모든 OS 공통)
DATA_ROOT = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else PROJECT_ROOT
DEFAULT_DB_PATH = os.path.join(DATA_ROOT, 'server_data', 'kakao.db')

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_AVATAR_BYTES = 512 * 1024
MAX_DEPUTIES = 10                      # 대직자 최대 인원 (§8.13)
MAX_BUFFER_CHARS = 16 * 1024 * 1024
MAX_CONTENT_LEN = 10000
MAX_HISTORY_LIMIT = 200
DELETE_WINDOW = timedelta(hours=24)
REMINDER_GRACE = timedelta(seconds=60)
SOCKET_TIMEOUT = 30.0
RECV_SIZE = 65536
SEND_CHUNK = 256 * 1024
MIN_SEND_BYTES_PER_SEC = 32 * 1024     # 이보다 느린 회선은 큰 응답을 끝내 받지 못한다고 보고 끊는다
RATE_LIMITED = '요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.'
UPLOAD_QUOTA = '하루에 올릴 수 있는 파일 용량을 넘었습니다. 내일 다시 시도해 주세요.'
AI_BUSY = '질문이 너무 잦아요. 잠시 후 다시 물어봐 주세요.'

SEND_KINDS = ('text', 'emoticon', 'image', 'file', 'secret', 'contact', 'voice')
ATTACHMENT_KINDS = ('image', 'file', 'voice')   # 첨부 데이터를 함께 보내는 종류
MAX_VOICE_SECONDS = 300                     # 음성메시지 최대 5분 (§9.3)
MAX_REACTION_KINDS = 30                     # 말풍선 하나에 달 수 있는 공감 종류 (카카오톡 v26.4.0, §9.2)
MAX_EXPORT_MESSAGES = 20000                 # 대화 내보내기 상한 (§9.5)
EXPORT_PAGE = 500
EXPORT_CHUNK = 2000                         # 내보내기 응답 한 조각의 메시지 수
MAX_SUMMARY_MESSAGES = 300                  # 대화 요약 대상 최근 메시지 수 (§9.1)
MAX_CALL_HISTORY = 200
SECRET_SEND_KINDS = ('secret', 'emoticon')   # 비밀채팅방에서 허용되는 send 종류 (평문 text 불가)
MAX_SECRET_CONTENT = 64 * 1024               # base64(nonce12 + AES-GCM 암호문)
SECRET_MIN_BYTES = 12 + 16                   # nonce + GCM 태그
SECRET_PEER_NO_KEY = '상대방이 아직 비밀채팅을 사용할 수 없습니다'
SECRET_FORBIDDEN = '비밀채팅방에서는 사용할 수 없는 기능입니다.'
LOGIN_FAILED = '아이디 또는 비밀번호가 올바르지 않습니다.'
# 등록이 닫힌 서버의 로그인 실패 문구 (§4.1): 계정이 있든 없든 같다. 클라이언트는 문구에 '처음 사용 등록' 이 있으면 등록 화면으로
# 바꾸므로(틀린 비밀번호에도 화면이 넘어간다) 그 말을 넣지 않고 화면의 링크 이름으로 안내한다
LOGIN_FAILED_CLOSED = (LOGIN_FAILED + ' 처음 쓰는 분은 관리자에게 등록 코드를 받아 '
                       '아래 [처음 사용하시나요?]에서 등록해 주세요.')
LOGIN_TOKEN_EXPIRED = '저장된 로그인 정보가 만료되었습니다. 비밀번호를 입력해 주세요.'
NAME_FIXED = '이름은 조직도에 등록된 이름으로 고정되어 바꿀 수 없습니다.'
EMAIL_ADMIN_ONLY = '이메일은 로그인 아이디라 직접 바꿀 수 없습니다. 관리자에게 변경을 요청해 주세요.'
LOGIN_REQUIRED = '먼저 로그인(join)해야 합니다.'
PASSWORD_REQUIRED = '비밀번호를 입력해 주세요.'
ALREADY_LOGGED_IN = '이미 로그인된 연결입니다.'
SERVER_ERROR = '서버 오류가 발생했습니다.'
RESET_MISMATCH = '아이디 · 가입한 이메일 · 재설정 코드가 맞지 않습니다. 코드는 관리자에게 받고 30분 동안 한 번만 쓸 수 있습니다.'
NEEDS_REGISTER = '처음 사용하는 아이디입니다. [처음 사용 등록]에서 소속 회사·이름·이메일·비밀번호를 등록해 주세요.'

# 오류 구분 코드 (명세 §4.1): 문구가 정해진 오류는 여기서 code 를 붙인다. 클라이언트는 문구가 아니라 이 값으로 가른다
ERROR_CODES = {
    LOGIN_FAILED: 'auth_failed', LOGIN_FAILED_CLOSED: 'auth_failed', LOGIN_TOKEN_EXPIRED: 'token_expired',
    ACCOUNT_DISABLED: 'account_disabled',
    NEEDS_REGISTER: 'needs_register', PASSWORD_REQUIRED: 'password_required', ALREADY_LOGGED_IN: 'already_logged_in',
    LOGIN_REQUIRED: 'login_required', RESET_MISMATCH: 'reset_mismatch', EMAIL_ADMIN_ONLY: 'email_admin_only',
    NAME_FIXED: 'name_fixed',
    Store.REGISTER_CLOSED: 'register_closed', Store.RESET_ADMIN_BY_TOOL: 'admin_account', ADMIN_ONLY: 'admin_only',
}

CALL_MODES = ('voice', 'video', 'voiceroom')
CALL_ROLES = ('host', 'speaker', 'listener')
MEDIA_KINDS = ('audio', 'video', 'screen')
AUDIO_CODECS = ('pcm', 'zpcm')
MAX_VIDEO_PARTICIPANTS = 10
MAX_VOICEROOM_PARTICIPANTS = 1500
MAX_VOICEROOM_SPEAKERS = 10                  # host + speaker
MAX_MEDIA_B64 = 2 * 1024 * 1024
MAX_AUDIO_FRAME_BYTES = 32000                # 음성 한 조각(PCM)의 상한 = 16kHz mono s16le 1초 (보내는 쪽은 40ms 씩)
MAX_AUDIO_B64 = MAX_AUDIO_FRAME_BYTES * 4 // 3 + 8
MAX_INVITE = 100                             # 한 번에 초대할 수 있는 인원 (방 만들기 · 초대 공통)
MAX_MENTIONS = 200
BAD_TEXT = '메시지에 쓸 수 없는 문자가 있습니다.'
UNSUPPORTED_REQUEST = '지원하지 않는 요청입니다.'
CALL_GONE = '종료되었거나 존재하지 않는 통화입니다.'
CALL_NOT_PARTICIPANT = '통화 참여자가 아닙니다.'

DEFAULT_STORY_TTL = 24 * 3600
DEFAULT_VOICEROOM_MAX = 48 * 3600
DEFAULT_STORY_COLOR = '#FEE500'
ATTACHMENT_EXPIRED = '보관 기간이 지난 파일입니다'
MAX_MULTI_PROFILES = 10
LADDER_ROWS = 10
LADDER_MIN, LADDER_MAX = 2, 10
MAX_DRAWER_LIMIT = 200
RSVP_RESPONSES = ('yes', 'no', 'maybe')
COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
DEFAULT_EVENT_COLOR = '#FEE500'

RAG_UNAVAILABLE = 'AI 기능을 사용할 수 없습니다'
RAG_FAILED = '답변을 생성하는 중 오류가 발생했습니다.'
BOT_MENTION = '@' + BOT_USERNAME
URL_RE = re.compile(r'https?://[^\s<>"]+')
PREVIEW_TIMEOUT = 3.0
PREVIEW_MAX_BYTES = 256 * 1024


def preview_host_allowed(url: str) -> bool:
    """링크 미리보기로 서버가 가져가도 되는 주소인가. 공인 인터넷 주소만 된다: 서버 자신(127.x · ::1) · 링크 로컬
    (169.254.x - 클라우드 메타데이터) · 0.0.0.0 · 멀티캐스트 · 사내망(10.x · 172.16~31.x · 192.168.x 등)은 막는다
    (메시지에 주소만 넣어 서버 안쪽 · 사내 서비스 내용을 대화방으로 빼 가지 못하게 - 명세 §4.5)"""
    return bool(_preview_addresses(url))


def _preview_addresses(url: str) -> list[str]:
    """미리보기로 가져가도 되는 주소면 그 호스트의 IP 목록, 아니면 [] (하나라도 막힌 주소면 전부 안 된다)"""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return []
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80),
                                   proto=socket.IPPROTO_TCP)
    except (OSError, ValueError, UnicodeError):
        return []
    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split('%', 1)[0])
        if not ip.is_global or ip.is_multicast:        # 공인 주소가 아니면(사내망 포함) 가져가지 않는다
            return []
        addresses.append(str(info[4][0]))
    return addresses


def _pinned_connection(conn_class: Any, scheme: str) -> Callable:
    """확인한 바로 그 IP 로만 접속하는 HTTP(S) 연결을 만든다 - 확인할 때와 접속할 때 이름 풀이가 달라지지 않게.
    (TLS 인증서 확인 · SNI 는 원래 호스트 이름으로 한다)"""
    def make(host: str, **kwargs: Any) -> Any:
        addresses = _preview_addresses(f'{scheme}://{host}/')
        if not addresses:
            raise OSError('미리보기로 가져갈 수 없는 주소입니다.')
        conn = conn_class(host, **kwargs)
        conn._create_connection = lambda address, *args, **kw: socket.create_connection(
            (addresses[0], address[1]), *args, **kw)
        return conn
    return make


def fetch_link_preview(url: str, block_internal: bool = True) -> Optional[dict]:
    """URL 의 og:title / og:description / og:site_name (없으면 <title>). 실패하면 None.
    block_internal: 서버 자신 · 링크 로컬 주소는 가져오지 않는다 (리다이렉트로 옮겨 가는 주소도 다시 검사)"""
    import html as html_mod
    import http.client
    import urllib.request

    class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if block_internal and not preview_host_allowed(newurl):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class _PinnedHTTP(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_pinned_connection(http.client.HTTPConnection, 'http'), req)

    class _PinnedHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_pinned_connection(http.client.HTTPSConnection, 'https'), req,
                                context=getattr(self, '_context', None))

    if block_internal and not preview_host_allowed(url):
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'KCSTalk-LinkPreview/1.0'})
        handlers = [_CheckedRedirect] + ([_PinnedHTTP, _PinnedHTTPS] if block_internal else [])
        opener = urllib.request.build_opener(*handlers)
        with opener.open(req, timeout=PREVIEW_TIMEOUT) as resp:
            ctype = resp.headers.get('Content-Type', '')
            if 'html' not in ctype:
                return None
            charset = resp.headers.get_content_charset() or 'utf-8'
            raw = resp.read(PREVIEW_MAX_BYTES)
    except Exception:
        return None
    text = raw.decode(charset, errors='replace')

    found, page_title = _scan_preview_tags(text)

    def meta(prop: str) -> str:
        return html_mod.unescape(found.get(prop, '')).strip()
    title = meta('og:title') or html_mod.unescape(re.sub(r'\s+', ' ', page_title)).strip()
    if not title:
        return None
    return {'url': url, 'title': title[:200], 'description': (meta('og:description') or meta('description'))[:300],
            'site': meta('og:site_name')[:80]}


_PREVIEW_META = ('og:title', 'og:description', 'og:site_name', 'description')
_PREVIEW_TAG_MAX = 2000        # <meta ...> 태그 하나에서 보는 길이
_PREVIEW_TITLE_MAX = 2000
_META_ATTR_RE = re.compile(r'''([a-zA-Z_:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))''')


def _scan_preview_tags(text: str) -> tuple[dict, str]:
    """HTML 에서 미리보기용 meta 값({이름: content})과 <title> 글자를 찾는다.
    태그를 하나씩 정해진 길이 안에서만 보므로 걸리는 시간이 문서 크기에 비례한다 (어떤 문서가 와도)"""
    lower = text.lower()
    found: dict[str, str] = {}
    pos = 0
    while len(found) < len(_PREVIEW_META):
        start = lower.find('<meta', pos)
        if start < 0:
            break
        end = lower.find('>', start, start + _PREVIEW_TAG_MAX)
        if end < 0:
            pos = start + 5
            continue
        attrs = {m.group(1).lower(): m.group(2) or m.group(3) or m.group(4) or ''
                 for m in _META_ATTR_RE.finditer(text, start + 5, end)}
        key = (attrs.get('property') or attrs.get('name') or '').lower()
        if key in _PREVIEW_META and key not in found and 'content' in attrs:
            found[key] = attrs['content']
        pos = end + 1
    title = ''
    start = lower.find('<title')
    if start >= 0:
        open_end = lower.find('>', start, start + _PREVIEW_TAG_MAX)
        if open_end >= 0:
            close = lower.find('</title', open_end + 1, open_end + 1 + _PREVIEW_TITLE_MAX)
            if close >= 0:
                title = text[open_end + 1:close]
    return found, title
RAG_HISTORY_LIMIT = 20

_IMAGE_SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'image/png', 'png'),
    (b'\xff\xd8\xff', 'image/jpeg', 'jpg'),
    (b'GIF87a', 'image/gif', 'gif'),
    (b'GIF89a', 'image/gif', 'gif'),
    (b'BM', 'image/bmp', 'bmp'),
)


def _sniff_image(data: bytes) -> Optional[tuple[str, str]]:
    """이미지 시그니처로 (mime, 확장자) 판별. 이미지가 아니면 None."""
    for sig, mime, ext in _IMAGE_SIGNATURES:
        if data.startswith(sig):
            return mime, ext
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    return None


def _decode_b64(data_b64: Any, max_bytes: int, too_big: str) -> bytes:
    if not isinstance(data_b64, str) or not data_b64:
        raise ProtocolError('첨부 파일 데이터가 없습니다.')
    if len(data_b64) > (max_bytes * 4) // 3 + 8:
        raise ProtocolError(too_big)
    try:
        data = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ProtocolError('첨부 파일 데이터(base64)가 올바르지 않습니다.') from None
    if len(data) > max_bytes:
        raise ProtocolError(too_big)
    return data


# ---------------------------------------------------------------------- 사다리 게임 (명세 §8.7)
def generate_ladder_rungs(columns: int, rows: int = LADDER_ROWS, rng: Optional[random.Random] = None) -> list[list[int]]:
    """무작위 가로줄 [[row, col], ...] 생성. col 은 왼쪽 세로줄 인덱스, 같은 row 에 인접한 가로줄은 없다."""
    rng = rng or random.SystemRandom()
    rungs: list[list[int]] = []
    for row in range(rows):
        prev = False
        for col in range(columns - 1):
            if not prev and rng.random() < 0.5:
                rungs.append([row, col])
                prev = True
            else:
                prev = False
    return rungs


def compute_ladder_mapping(participants: list[str], results: list[str], rungs: list) -> dict[str, str]:
    """위에서부터 내려가며 가로줄을 만나면 옆 세로줄로 이동 → {participant: result}.

    가로줄이 범위를 벗어나거나 같은 row 에 인접해 있으면 ValueError.
    """
    n = len(participants)
    if n != len(results):
        raise ValueError('참가자와 결과의 개수가 같아야 합니다.')
    rung_set: set[tuple[int, int]] = set()
    for rung in rungs:
        row, col = int(rung[0]), int(rung[1])
        if row < 0 or not 0 <= col < n - 1:
            raise ValueError('가로줄 위치가 올바르지 않습니다.')
        if (row, col - 1) in rung_set or (row, col + 1) in rung_set:
            raise ValueError('같은 줄에 인접한 가로줄이 있습니다.')
        rung_set.add((row, col))
    rows = max([LADDER_ROWS] + [r + 1 for r, _ in rung_set])
    mapping: dict[str, str] = {}
    for start, name in enumerate(participants):
        col = start
        for row in range(rows):
            if (row, col) in rung_set:
                col += 1
            elif (row, col - 1) in rung_set:
                col -= 1
        mapping[name] = results[col]
    return mapping


def _default_rag_factory(db_path: str) -> Any:
    from .rag import RagService  # 지연 import: rag.py 가 없어도 서버는 동작
    return RagService(db_path)




def _validate_birthday(value: Any) -> Optional[str]:
    """생년월일 'YYYY-MM-DD' 또는 (연도 없이) 생일 'MM-DD'"""
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'(\d{4}-)?\d{2}-\d{2}', value):
        raise ProtocolError('생년월일은 YYYY-MM-DD(또는 MM-DD) 형식이어야 합니다.')
    try:
        day = datetime.strptime(value if len(value) == 10 else '2000-' + value, '%Y-%m-%d').date()
    except ValueError:
        raise ProtocolError('존재하지 않는 날짜입니다.') from None
    if len(value) == 10 and not (1900 <= day.year and day <= datetime.now().date()):
        raise ProtocolError('생년월일이 올바르지 않습니다.')
    return value


def _validate_due(value: Any) -> Optional[str]:
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ProtocolError('마감일은 YYYY-MM-DD 형식이어야 합니다.')
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise ProtocolError('마감일은 YYYY-MM-DD 형식이어야 합니다.') from None


# ---------------------------------------------------------------------- 연결
def encode_frame(payload: dict) -> Optional[bytes]:
    """보낼 한 줄(JSON + 줄바꿈). 보낼 수 없는 값이 섞였으면 None - 그 한 건만 버린다 (보낼 수 없는 글자는 '?' 로)"""
    try:
        return (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8', errors='replace')
    except (TypeError, ValueError, RecursionError) as exc:
        logger.error('send: 보낼 수 없는 내용 (%s): %s', payload.get('type') if isinstance(payload, dict) else '?', exc)
        return None


class Connection:
    """클라이언트 연결 1개. 보낼 것은 대기열에 줄 세우고 이 연결 전용 스레드가 차례로 보낸다 (명세 §2 '연결별 보내기 대기열')
    - 한 연결이 받지 않고 멈춰도 send() 를 부른 쪽(다른 사람에게도 보내는 스레드)은 기다리지 않는다."""

    MAX_QUEUE_BYTES = 32 * 1024 * 1024     # 이보다 많이 밀리면 그 연결을 닫는다
    MAX_QUEUE_FRAMES = 20000
    REPLY_WAIT_BYTES = 8 * 1024 * 1024     # 자기 요청의 응답(wait=True)은 이만큼 쌓여 있으면 줄어들 때까지 기다린다
    DROP_BYTES = 2 * 1024 * 1024           # 버려도 되는 조각(통화 미디어)은 이만큼 쌓인 연결에는 보내지 않는다
    DRAIN_SECONDS = 2.0                    # 닫을 때 줄 서 있던 것을 마저 보내는 시간

    def __init__(self, sock: socket.socket, address: Any) -> None:
        self.sock = sock
        self.address = address
        self.username: Optional[str] = None
        self.alive = True
        now = time.monotonic()
        self.connected_at = now
        self.last_recv = now
        self.last_sent = now
        self.heartbeat = False          # ping/pong 을 하는 클라이언트 (그런 연결만 조용하면 끊는다 - 명세 §2)
        self.bucket: Optional[TokenBucket] = None        # 요청 빈도 (로그인하면 채운다)
        self.media_bucket: Optional[TokenBucket] = None  # 통화 미디어 조각 빈도
        self._cond = threading.Condition()
        self._queue: 'collections.deque[bytes]' = collections.deque()
        self._queued_bytes = 0                           # 줄 서 있는 것 + 지금 보내는 것
        self._writer: Optional[threading.Thread] = None
        self._force = False                              # True 면 줄 서 있는 것을 버리고 바로 닫는다
        self._drain_until = 0.0
        self._shut = False
        self._gate: Optional[int] = None                 # 이 스레드가 보내는 것만 먼저 줄 세운다 (로그인 응답)
        self._gated: list[bytes] = []
        self._poller: Any = None                         # 지금 바로 보낼 수 있는지 보는 용도 (없는 OS 는 select)
        if hasattr(select, 'poll'):
            try:
                self._poller = select.poll()
                self._poller.register(sock.fileno(), select.POLLOUT)
            except (OSError, ValueError):
                self._poller = None

    @property
    def ip(self) -> str:
        return self.address[0] if isinstance(self.address, tuple) and self.address else str(self.address)

    # ---- 줄 세우기 ----
    def send(self, payload: dict, wait: bool = False, droppable: bool = False) -> bool:
        """payload 를 보낼 줄에 세운다 → 세웠으면 True (실제 전송은 전용 스레드가 한다)"""
        if not self.alive:
            return False
        data = encode_frame(payload)
        return data is not None and self.send_frame(data, wait=wait, droppable=droppable)

    def send_frame(self, data: bytes, wait: bool = False, droppable: bool = False) -> bool:
        """이미 만든 한 줄을 세운다 (여러 사람에게 같은 내용을 보낼 때 한 번만 만든다).
        wait: 이 연결 자신의 요청에 대한 응답 - 많이 밀려 있으면 줄어들 때까지 기다린다 (자기 다음 요청만 늦어진다)
        droppable: 통화 미디어처럼 버려도 되는 조각 - 밀려 있는 연결에는 보내지 않는다"""
        with self._cond:
            if not self.alive:
                return False
            if self._gate is not None and threading.get_ident() != self._gate:
                self._gated.append(data)            # 로그인 응답이 먼저 가도록 잠깐 따로 모아 둔다
                return True
            if droppable and self._queued_bytes > self.DROP_BYTES:
                return False
            if wait and self._queued_bytes > self.REPLY_WAIT_BYTES:
                deadline = time.monotonic() + SOCKET_TIMEOUT * 4
                while self.alive and self._queued_bytes > self.REPLY_WAIT_BYTES:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self._cond.wait(min(left, 1.0))
                if not self.alive:
                    return False
            if self._queued_bytes == 0:     # 밀린 것이 없으면 기다리지 않는 한도 안에서 바로 보낸다 (스레드를 건너가지 않는다)
                sent = self._send_now(data)
                if sent == len(data):
                    self.last_sent = time.monotonic()
                    return True
                if sent > 0:
                    data = bytes(memoryview(data)[sent:])      # 못 보낸 나머지만 줄에 세운다
                elif sent < 0:
                    data = b''
            if data and self._enqueue(data):
                return True
            failed = not data
        if failed:
            self.close(force=True)
            return False
        logger.info('받는 쪽이 밀려 연결을 닫습니다 (%s, %s)', self.username, self.address)
        self.close(force=True)
        return False

    def _send_now(self, data: bytes) -> int:
        """기다리지 않고 보낼 수 있는 만큼만 보낸다 → 보낸 바이트 수 (지금은 못 보내면 0, 연결 오류면 -1). _cond 안에서 호출"""
        try:
            if os.name == 'posix' and self.sock.gettimeout():
                # 시간 제한이 있는 소켓은 안쪽이 '기다리지 않는' 상태라, 바로 쓰면 들어가는 만큼만 들어간다
                try:
                    return os.write(self.sock.fileno(), data)
                except BlockingIOError:
                    return 0
            if self._poller is not None:
                ready = self._poller.poll(0)
                if ready and ready[0][1] & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    return -1
            else:
                ready = select.select([], [self.sock], [], 0)[1]
            return self.sock.send(data) if ready else 0
        except (OSError, ValueError):
            return -1

    def _enqueue(self, data: bytes) -> bool:
        """_cond 를 잡은 채 호출. 한도를 넘으면 False"""
        if self._queue and (self._queued_bytes + len(data) > self.MAX_QUEUE_BYTES
                            or len(self._queue) >= self.MAX_QUEUE_FRAMES):
            return False
        self._queue.append(data)
        self._queued_bytes += len(data)
        if self._writer is None:
            self._writer = threading.Thread(target=self._write_loop, daemon=True, name=f'kakao-send-{self.address}')
            self._writer.start()
        elif len(self._queue) == 1:         # 비어 있다가 생겼을 때만 깨운다 (전용 스레드는 빌 때만 잔다)
            self._cond.notify_all()
        return True

    def hold_others(self) -> None:
        """지금부터 release_others() 까지는 이 스레드가 보내는 것만 줄에 세우고, 다른 스레드의 것은 그 뒤로 미룬다"""
        with self._cond:
            self._gate = threading.get_ident()

    def release_others(self) -> None:
        with self._cond:
            self._gate = None
            held, self._gated = self._gated, []
            for data in held:
                if self.alive and not self._enqueue(data):
                    break

    def queued_bytes(self) -> int:
        with self._cond:
            return self._queued_bytes

    # ---- 전용 스레드 ----
    def _write_loop(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._queue and self.alive:
                        self._cond.wait(1.0)
                    if not self._queue or self._force or (not self.alive and time.monotonic() > self._drain_until):
                        break
                    data = self._queue.popleft()
                    if self._queue and len(data) < SEND_CHUNK:      # 줄 서 있는 작은 것들은 묶어서 한 번에 보낸다
                        parts, size = [data], len(data)
                        while self._queue and size + len(self._queue[0]) <= SEND_CHUNK:
                            parts.append(self._queue.popleft())
                            size += len(parts[-1])
                        data = b''.join(parts)
                self._write(data)
                with self._cond:
                    self._queued_bytes -= len(data)
                    self.last_sent = time.monotonic()
                    self._cond.notify_all()
        except OSError as exc:
            logger.info('send failed to %s: %s', self.address, exc)
        except Exception:
            logger.exception('보내기 스레드 오류 (%s)', self.address)
        self._finish()

    def _write(self, data: bytes) -> None:
        # 한 번에 보낸 만큼씩 시간 제한을 다시 센다 (느린 회선도 큰 첨부를 받는다). 전체로는 최소 속도만 본다
        view = memoryview(data)
        deadline = time.monotonic() + SOCKET_TIMEOUT + len(data) / MIN_SEND_BYTES_PER_SEC
        while view:
            view = view[self.sock.send(view[:SEND_CHUNK]):]
            if view:
                now = time.monotonic()
                if now > deadline or self._force or (not self.alive and now > self._drain_until):
                    raise socket.timeout('너무 느린 연결')

    def _finish(self) -> None:
        with self._cond:
            self.alive = False
            self._queue.clear()
            self._gated = []
            self._queued_bytes = 0
            self._cond.notify_all()
        self._shutdown()

    # ---- 닫기 ----
    def close(self, force: bool = False) -> None:
        """연결을 닫는다. 줄 서 있던 것(오류 안내 등)은 DRAIN_SECONDS 까지 마저 보낸다. force 면 버리고 바로 닫는다"""
        with self._cond:
            if self.alive:
                self._drain_until = time.monotonic() + self.DRAIN_SECONDS
            self.alive = False
            self._force = self._force or force
            draining = (not self._force and bool(self._queue) and self._writer is not None
                        and self._writer.is_alive() and threading.current_thread() is not self._writer)
            self._cond.notify_all()
        if not draining:
            self._shutdown()
            return
        try:
            self.sock.settimeout(self.DRAIN_SECONDS)     # 전용 스레드가 남은 것을 보내고 닫는다
        except OSError:
            self._shutdown()

    def _shutdown(self) -> None:
        with self._cond:
            if self._shut:
                return
            self._shut = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class TokenBucket:
    """요청 빈도 제한: 초당 rate 개씩 채워지고 burst 개까지 모아 쓴다 (연결마다 하나, 그 연결의 스레드만 쓴다)"""
    __slots__ = ('rate', 'burst', 'tokens', 'stamp')

    def __init__(self, rate: float, burst: float) -> None:
        self.rate, self.burst = float(rate), float(burst)
        self.tokens = float(burst)
        self.stamp = time.perf_counter()      # 촘촘한 시계 (Windows 의 monotonic 은 약 16ms 단위)

    def take(self) -> bool:
        now = time.perf_counter()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


class WindowLimiter:
    """사용자별 · 동작별 횟수 제한 (최근 window 초 안에 limit 번)"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = {}

    def hit(self, user: str, action: str, limit: int, window: float) -> tuple[bool, int]:
        """한 번 센다 → (허용인가, 다시 해도 되는 때까지 남은 초). 넘은 뒤의 시도는 세지 않는다"""
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 20000:        # 오래된 기록 정리
                for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= 3600]:
                    del self._hits[key]
            times = [t for t in self._hits.get((user, action), ()) if now - t < window]
            if len(times) >= limit:
                self._hits[(user, action)] = times
                return False, max(1, int(window - (now - times[0])) + 1)
            times.append(now)
            self._hits[(user, action)] = times
            return True, 0


class FrameDecoder:
    """NDJSON + 줄바꿈 없는 연속 JSON 을 모두 처리하는 수신 버퍼."""

    def __init__(self) -> None:
        self.limit = MAX_BUFFER_CHARS     # 완성되지 않은 요청 한 건의 최대 크기 (로그인 전에는 서버가 작게 잡는다)
        self._utf8 = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._parts: list[str] = []   # 아직 완성되지 않은 프레임 조각 (대용량 첨부 시 재복사 방지)
        self._size = 0
        self._json = json.JSONDecoder()

    def _reset(self, rest: str) -> None:
        self._parts = [rest] if rest else []
        self._size = len(rest)

    def feed(self, data: bytes) -> list[Any]:
        """수신 바이트를 넣고 완성된 항목 목록을 반환. 파싱 실패 줄은 ``ValueError`` 인스턴스로 반환."""
        text = self._utf8.decode(data)
        if not text:
            return []
        self._parts.append(text)
        self._size += len(text)
        items: list[Any] = []
        if '\n' in text:
            lines = ''.join(self._parts).split('\n')
            self._reset(lines.pop())
            for line in lines:
                items.extend(self._decode_line(line))
        # 줄바꿈 없는 구버전 프레임: 끝이 '}' 일 때만 시도(부분 수신 재파싱 비용 절감)
        if self._parts and self._parts[-1].rstrip().endswith('}'):
            buf = ''.join(self._parts)
            decoded, rest = self._decode_prefix(buf.strip())
            if decoded:
                items.extend(decoded)
                self._reset(rest)
            else:
                self._parts = [buf]
        if self._size > self.limit:
            raise OverflowError('수신 버퍼 한도 초과')
        return items

    def _decode_prefix(self, text: str) -> tuple[list[Any], str]:
        out: list[Any] = []
        idx, n = 0, len(text)
        while idx < n:
            while idx < n and text[idx].isspace():
                idx += 1
            if idx >= n:
                break
            try:
                obj, idx = self._json.raw_decode(text, idx)
            except (json.JSONDecodeError, RecursionError):     # 너무 깊게 겹친 값도 잘못된 형식으로 본다
                break
            out.append(obj)
        return out, text[idx:]

    def _decode_line(self, line: str) -> list[Any]:
        decoded, rest = self._decode_prefix(line)
        if rest.strip():
            decoded.append(ValueError('JSON 형식이 올바르지 않습니다.'))
        return decoded


@dataclass
class Ctx:
    """요청 1건의 처리 문맥."""
    conn: Connection
    username: str
    req_type: str
    req_id: Any = None
    has_req_id: bool = False
    extra: dict = field(default_factory=dict)


def _call_ordered(method: Callable) -> Callable:
    """통화 상태를 바꾸고 그 알림을 대기열에 세우는 일을 통화 알림 순서 잠금(_call_order) 안에서 한다 (명세 §8.2).
    상태는 _calls_lock 안에서 뜨고 알림은 밖에서 세우므로, 잠금이 없으면 두 스레드의 순서가 뒤바뀌어
    call_ended 뒤에 그보다 앞선 상태의 call_updated 가 갈 수 있다. 알림은 대기열에 세우기만 하므로 오래 잡지 않는다"""
    @functools.wraps(method)
    def run(self: 'KakaoServer', *args: Any, **kwargs: Any) -> Any:
        with self._call_order:
            return method(self, *args, **kwargs)
    return run


@dataclass
class Call:
    """진행 중인 통화 1건 (메모리에만 존재, KakaoServer._calls_lock 으로 보호)."""
    call_id: int
    room_id: str
    mode: str
    host: str
    started_at: str
    started_mono: float
    participants: dict = field(default_factory=dict)
    joined: set = field(default_factory=set)        # 한 번이라도 참여한 사람 (통화 기록·부재중 판정)

    @staticmethod
    def new_participant(role: str, mode: str) -> dict:
        return {'role': role, 'muted': False, 'video': mode == 'video', 'screen': False, 'hand': False}

    def has_host(self) -> bool:
        return any(p['role'] == 'host' for p in self.participants.values())

    def stage_count(self) -> int:
        return sum(1 for p in self.participants.values() if p['role'] in ('host', 'speaker'))

    def to_dict(self) -> dict:
        return {'call_id': self.call_id, 'room_id': self.room_id, 'mode': self.mode, 'host': self.host,
                'started_at': self.started_at,
                'participants': {u: dict(p) for u, p in self.participants.items()}}


# ---------------------------------------------------------------------- 서버
class KakaoServer(AdminHandlers):
    """카카오톡 기능 서버. ``start()``(블로킹) / ``serve_in_thread()`` / ``stop()``."""

    def __init__(self, host: str = '127.0.0.1', port: int = 5555, db_path: Optional[str] = None,
                 tick_seconds: float = 1.0, rag_factory: Optional[Callable[[str], Any]] = None,
                 link_preview: Optional[bool] = None, daily_briefing: bool = False,
                 preview_fetcher: Optional[Callable[[str], Optional[dict]]] = None,
                 retention_days: float = 90, story_ttl_seconds: float = DEFAULT_STORY_TTL,
                 voiceroom_max_seconds: float = DEFAULT_VOICEROOM_MAX,
                 require_password: bool = False, portal: Optional[dict] = None,
                 portal_client: Any = None, proxy_protocol: bool = False,
                 open_registration: Optional[bool] = None) -> None:
        self.host = host
        # True 면 각 연결의 첫 줄을 PROXY protocol v1 헤더로 읽어 실제 클라이언트 IP 를 복원한다.
        # nginx stream 이 TLS 를 종단하고 loopback 으로 넘기는 배치(.230)에서 켠다 — 안 켜면 모든
        # 접속이 127.0.0.1 로 보여 IP 기준 로그인 스로틀(LOGIN_MAX_FAILURES['ip'])이 전 사용자에게 한 덩어리로 걸린다.
        self.proxy_protocol = proxy_protocol
        # 사내 포털 RAG 연동 설정 (portal.load_config 결과, None 이면 메신저 자체 RAG 만 - 명세 §7.6.1)
        self.portal_config = portal
        self.portal_client = portal_client      # 테스트용 가짜 포털 클라이언트
        # True 면 비밀번호(또는 자동 로그인 토큰) 없는 join 을 받지 않는다 (배포용 chat_server.py 기본값)
        self.require_password = require_password
        # 등록 방식 (명세 §4.1): False(닫힘)면 조직도에 없는 이름은 만들지 않고, 비밀번호 없는 계정은 관리자가 발급한
        # 등록 코드가 있어야 등록된다. 기본값은 비밀번호 필수 서버면 닫힘, 테스트 · 데모(비밀번호 없는 join)면 열림
        self.open_registration = (not require_password) if open_registration is None else bool(open_registration)
        self._dummy_hash: Optional[str] = None
        self._login_failures: dict[str, list[float]] = {}   # 아이디/IP → 최근 실패 시각
        self._actions = WindowLimiter()
        self._login_failures_lock = threading.Lock()
        self.retention_days = float(retention_days)          # 받은 파일 보관 기간 (§8.10)
        self.story_ttl_seconds = float(story_ttl_seconds)    # 펑 유지 시간 (§8.3)
        self.voiceroom_max_seconds = float(voiceroom_max_seconds)  # 보이스룸 자동 종료 (§8.2)
        # 통화 레지스트리 (영속화하지 않음)
        self._calls_lock = threading.Lock()
        self._calls: dict[int, Call] = {}
        self._user_json_cache: tuple[int, dict] = (-1, {})   # welcome 의 사용자 JSON 조각 (_users_json_for)
        self._presence_lock = threading.Lock()
        self._presence_pending: dict[str, str] = {}          # 아직 알리지 않은 접속 상태 변화 {이름: 시각}
        self._presence_wake = threading.Event()
        self._welcome_gate = threading.BoundedSemaphore(max(1, int(self.WELCOME_BUILDS)))
        self._call_order = threading.RLock()     # 통화 상태 순서 = 알림 순서 (_call_ordered)
        self._summary_lock = threading.Lock()
        self._summaries_running: set = set()      # (username, room_id) 요약 진행 중
        self._room_calls: dict[str, int] = {}
        self._next_call_id = 1
        # URL 미리보기 (명세 §7.8) - 환경변수 KCS_LINK_PREVIEW=off 로 끌 수 있다
        if link_preview is None:
            link_preview = os.environ.get('KCS_LINK_PREVIEW', '').lower() not in ('off', '0', 'false')
        self.link_preview = bool(link_preview)
        self.preview_fetcher = preview_fetcher or fetch_link_preview
        self._preview_queue: 'queue.Queue[Optional[dict]]' = queue.Queue()
        self.daily_briefing = bool(daily_briefing)
        self.rag_factory = rag_factory or _default_rag_factory
        self.rag: Any = None
        self._rag_queue: 'queue.Queue[Optional[Callable[[], None]]]' = queue.Queue()
        self._rag_ready: 'queue.Queue[Optional[str]]' = queue.Queue()
        self._rag_pending: 'dict[str, collections.deque[Callable[[], None]]]' = {}   # key 별로 줄 서 있는 일
        self._rag_slow_count = 0
        self._rag_slow_lock = threading.Lock()
        self.port = port
        self.db_path = db_path or DEFAULT_DB_PATH
        self.tick_seconds = max(0.05, float(tick_seconds))
        self.store: Optional[Store] = None
        self._listener: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._conns_lock = threading.Lock()
        self._conns: set[Connection] = set()
        self._by_user: dict[str, set[Connection]] = {}
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()
        self._handlers: dict[str, Callable[[Ctx, dict], None]] = {
            # 관리자 (명세 §4.2)
            'admin_users': self._h_admin_users,
            'admin_reset_password': self._h_admin_reset_password,
            'admin_clear_password': self._h_admin_clear_password,
            'admin_set_disabled': self._h_admin_set_disabled,
            'admin_reset_code': self._h_admin_reset_code,
            'admin_set_admin': self._h_admin_set_admin,
            'admin_notice': self._h_admin_notice,
            'admin_member_create': self._h_admin_member_create,
            'admin_member_update': self._h_admin_member_update,
            'admin_member_delete': self._h_admin_member_delete,
            'admin_member_restore': self._h_admin_member_restore,
            'admin_group_create': self._h_admin_group_create,
            'admin_group_update': self._h_admin_group_update,
            'admin_group_delete': self._h_admin_group_delete,
            'admin_group_reorder': self._h_admin_group_reorder,
            'admin_org_order': self._h_admin_org_order,
            # 파일 전송 보안 (명세 §10.10)
            'admin_file_policy_set': self._h_admin_file_policy_set,
            'admin_file_requests': self._h_admin_file_requests,
            'admin_file_request_decide': self._h_admin_file_request_decide,
            'admin_file_exceptions': self._h_admin_file_exceptions,
            'admin_file_exception_add': self._h_admin_file_exception_add,
            'admin_file_exception_delete': self._h_admin_file_exception_delete,
            'file_send_request': self._h_file_send_request,
            'file_request_list': self._h_file_request_list,
            'message': self._h_legacy_message,
            'room_open_direct': self._h_room_open_direct,
            'room_create': self._h_room_create,
            'room_invite': self._h_room_invite,
            'room_leave': self._h_room_leave,
            'room_rename': self._h_room_rename,
            'room_kick': self._h_room_kick,
            'thread_get': self._h_thread_get,
            'mention_list': self._h_mention_list,
            'room_owner_set': self._h_room_owner_set,
            'history': self._h_history,
            'send': self._h_send,
            'read': self._h_read,
            'react': self._h_react,
            'delete': self._h_delete,
            'edit': self._h_edit,
            'notice_set': self._h_notice_set,
            'search': self._h_search,
            'poll_create': self._h_poll_create,
            'poll_vote': self._h_poll_vote,
            'poll_close': self._h_poll_close,
            'event_create': self._h_event_create,
            'event_update': self._h_event_update,
            'event_delete': self._h_event_delete,
            'event_rsvp': self._h_event_rsvp,
            'event_list': self._h_event_list,
            'todo_create': self._h_todo_create,
            'todo_update': self._h_todo_update,
            'todo_delete': self._h_todo_delete,
            'todo_list': self._h_todo_list,
            'login_token_revoke': self._h_login_token_revoke,
            'password_change': self._h_password_change,
            'profile_update': self._h_profile_update,
            'attachment_get': self._h_attachment_get,
            'presence_set': self._h_presence_set,
            'rag_doc_list': self._h_rag_doc_list,
            'rag_doc_add': self._h_rag_doc_add,
            'rag_doc_delete': self._h_rag_doc_delete,
            # v3 (명세 §8)
            'key_publish': self._h_key_publish,
            'room_open_secret': self._h_room_open_secret,
            'call_start': self._h_call_start,
            'call_join': self._h_call_join,
            'call_leave': self._h_call_leave,
            'call_hand': self._h_call_hand,
            'call_role': self._h_call_role,
            'call_state': self._h_call_state,
            'call_media': self._h_call_media,
            'call_list': self._h_call_list,
            'story_post': self._h_story_post,
            'story_list': self._h_story_list,
            'story_view': self._h_story_view,
            'story_delete': self._h_story_delete,
            'multi_profile_set': self._h_multi_profile_set,
            'multi_profile_list': self._h_multi_profile_list,
            'block_set': self._h_block_set,
            'friend_add': self._h_friend_add,
            'friend_remove': self._h_friend_remove,
            'friend_list': self._h_friend_list,
            'ladder_create': self._h_ladder_create,
            'board_post': self._h_board_post,
            'board_list': self._h_board_list,
            'board_comment': self._h_board_comment,
            'board_delete': self._h_board_delete,
            'open_room_create': self._h_open_room_create,
            'open_room_search': self._h_open_room_search,
            'open_room_join': self._h_open_room_join,
            'drawer_query': self._h_drawer_query,
            # v4: 2026 업데이트 (명세 §9)
            'room_summary': self._h_room_summary,
            'call_history': self._h_call_history,
            'room_export': self._h_room_export,
        }

    # ------------------------------------------------------------------ 생명주기
    def _bind(self) -> None:
        self._stop.clear()
        self.store = Store(self.db_path, retention_days=self.retention_days)
        if not self.open_registration:         # 표시 이름은 실명으로 고정 (§4.5): 예전에 바꿔 둔 이름을 되돌린다
            fixed = self.store.fix_display_names()
            if fixed:
                logger.info('표시 이름 %d개를 조직도 이름으로 되돌렸습니다', fixed)
        self._merge_file_policy_defaults()      # 새 기본 차단 형태를 저장된 정책에 한 번 (§10.10)
        with self._calls_lock:
            self._calls.clear()
            self._room_calls.clear()
            self._next_call_id = self.store.max_call_id() + 1   # 저장된 call 메시지와 id 충돌 방지
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(256)
        except OSError:
            sock.close()
            self.store.close()
            raise
        sock.settimeout(0.5)
        self._listener = sock
        self._init_rag()
        self.port = sock.getsockname()[1]
        logger.info('KCS Messenger 서버 시작: %s:%s (db=%s)', self.host, self.port, self.db_path)

    def _rag_db_path(self) -> str:
        if self.db_path == ':memory:':
            return ':memory:'
        return os.path.join(os.path.dirname(os.path.abspath(self.db_path)), 'rag.db')

    def _init_rag(self) -> None:
        try:
            self.rag = self.rag_factory(self._rag_db_path())
        except Exception as exc:  # ImportError 포함: AI 기능만 비활성화
            logger.warning('RAG 비활성화: %s', exc)
            self.rag = None
        if self.portal_config:
            try:
                from .portal import PortalRag
                outbox = ':memory:' if self.db_path == ':memory:' else \
                    os.path.join(os.path.dirname(os.path.abspath(self.db_path)), 'portal_outbox.db')
                self.rag = PortalRag(self.rag, self.portal_config, outbox, client=self.portal_client)
                logger.info('AI 비서: 사내 포털 RAG 연동 (%s)', self.portal_config.get('base_url'))
            except Exception as exc:
                logger.warning('포털 연동 비활성화 (메신저 자체 RAG 사용): %s', exc)
        self._rag_queue = queue.Queue()
        self._rag_ready = queue.Queue()         # 할 일이 있는 key (방 · 사용자) - 한 key 는 한 번에 하나만 들어 있다
        self._rag_pending = {}
        self._rag_slow_count = 0

    def _spawn(self, target: Callable, *args: Any, name: str = '') -> threading.Thread:
        t = threading.Thread(target=target, args=args, daemon=True, name=name or None)
        with self._threads_lock:
            self._threads = [x for x in self._threads if x.is_alive()]
            self._threads.append(t)
        t.start()
        return t

    def start(self) -> None:
        """서버를 시작하고 stop() 될 때까지 블로킹."""
        self._bind()
        self._spawn(self._scheduler_loop, name='kakao-scheduler')
        self._spawn(self._rag_worker, name='kakao-rag')
        self._spawn(self._preview_worker, name='kakao-preview')
        self._spawn(self._heartbeat_loop, name='kakao-heartbeat')
        self._spawn(self._presence_loop, name='kakao-presence')
        self._spawn_rag_workers()
        try:
            self._accept_loop()
        finally:
            self.stop()

    def serve_in_thread(self) -> threading.Thread:
        """listen 상태가 된 뒤 반환. 실제 포트는 ``self.port``."""
        self._bind()
        self._spawn(self._scheduler_loop, name='kakao-scheduler')
        self._spawn(self._rag_worker, name='kakao-rag')
        self._spawn(self._preview_worker, name='kakao-preview')
        self._spawn(self._heartbeat_loop, name='kakao-heartbeat')
        self._spawn(self._presence_loop, name='kakao-presence')
        self._spawn_rag_workers()
        return self._spawn(self._accept_loop, name='kakao-accept')

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        with self._conns_lock:
            conns = list(self._conns)
        for conn in conns:
            conn.close(force=True)
        self._rag_queue.put(None)
        for _ in range(max(1, int(self.RAG_WORKERS))):
            self._rag_ready.put(None)
        self._preview_queue.put(None)
        current = threading.current_thread()
        with self._threads_lock:
            threads = list(self._threads)
        for t in threads:
            if t is not current:
                t.join(timeout=2.0)
        if self.store is not None:
            self.store.close()
        close_rag = getattr(self.rag, 'close', None)
        if callable(close_rag):
            try:
                close_rag()
            except Exception:
                logger.exception('RAG 종료 오류')
        logger.info('KCS Messenger 서버 종료')

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    break
                logger.error('연결을 받지 못했습니다 (잠시 뒤 계속): %s', exc)   # 파일 개수 한도 등 - 서버를 내리지 않는다
                self._stop.wait(0.2)
                continue
            sock.settimeout(min(SOCKET_TIMEOUT, self.PRELOGIN_SECONDS))        # 로그인하면 SOCKET_TIMEOUT 으로
            self._enable_keepalive(sock)
            self._enable_nodelay(sock)
            conn = Connection(sock, address)
            with self._conns_lock:
                full = len(self._conns) >= self.MAX_CONNECTIONS
                if not full:
                    self._conns.add(conn)
            if full:
                logger.warning('연결 수 한도(%s)에 닿아 새 연결을 닫습니다: %s', self.MAX_CONNECTIONS, address)
                conn.close(force=True)
                continue
            self._spawn(self._client_loop, conn, name=f'kakao-client-{address}')

    @staticmethod
    def _enable_nodelay(sock: socket.socket) -> None:
        """작은 응답을 모으지 않고 바로 보낸다 (Nagle 끔). 응답이 여러 조각으로 나가거나 작은 요청 · 응답이 이어질 때
        상대의 지연 ACK(Windows 는 약 200ms)를 기다리느라 왕복마다 수백 ms 가 붙는 것을 막는다 (명세 §2, 2026-11-18)"""
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    @staticmethod
    def _enable_keepalive(sock: socket.socket) -> None:
        """끊긴 줄 모르는 연결을 운영체제가 찾아내게 한다 (클라이언트가 응답할 필요 없음)"""
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for name, value in (('TCP_KEEPIDLE', 60), ('TCP_KEEPALIVE', 60), ('TCP_KEEPINTVL', 20), ('TCP_KEEPCNT', 3)):
                option = getattr(socket, name, None)
                if option is not None:
                    sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            pass

    def _heartbeat_loop(self) -> None:
        """조용한 연결에 ping 을 보내고(끊긴 연결은 보내다 드러난다), ping/pong 을 하는 클라이언트가
        HEARTBEAT_TIMEOUT 동안 아무것도 보내지 않으면 끊는다. ping 을 모르는 예전 클라이언트는 조용해도 끊지 않는다"""
        while not self._stop.wait(max(0.05, min(5.0, self.HEARTBEAT_SECONDS / 3))):
            now = time.monotonic()
            for conn in self._all_joined_conns():
                try:
                    if conn.heartbeat and now - conn.last_recv > self.HEARTBEAT_TIMEOUT:
                        logger.info('%s 응답 없음 - 연결을 닫습니다 (%s)', conn.username, conn.address)
                        conn.close(force=True)  # 읽기 스레드가 깨어나 접속 목록에서 뺀다
                    elif now - conn.last_sent >= self.HEARTBEAT_SECONDS:
                        conn.send({'type': 'ping'})
                except Exception:
                    logger.exception('하트비트 오류')

    # ------------------------------------------------------------------ 연결 관리
    def _online_users(self) -> set[str]:
        with self._conns_lock:
            online = {u for u, cs in self._by_user.items() if cs}
        online.add(BOT_USERNAME)  # 봇은 항상 접속 중
        return online

    @staticmethod
    def _apply_profile(user: dict, override: Optional[dict]) -> dict:
        return {**user, **override} if override else user

    @staticmethod
    def _private_view(user: dict, viewer: Optional[str], viewer_is_admin: bool) -> dict:
        """다른 사람에게는 이메일을 보이지 않는다 (본인 · 관리자만). 이메일은 비밀번호 재설정 확인 값이고
        친구 추가도 '정확히 아는 사람만' 이메일로 찾게 했다 - 모두에게 보내면 둘 다 의미가 없어진다"""
        if viewer_is_admin or user.get('username') == viewer or not user.get('email'):
            return user
        return {**user, 'email': ''}

    def _users_json_for(self, viewer: str, online: set[str]) -> tuple[str, list[str]]:
        """welcome 의 users 를 JSON 배열 글자로 → (JSON, 이름 목록). _users_for 와 바이트까지 같다
        (tests/test_kakao_user_cache.py 가 비교한다). 사용자 목록은 store.user_directory 캐시에서, 한 사람의 JSON 조각은
        (버전, 이름, 접속 여부, 이메일 가림)마다 한 번만 만든다. 보는 사람마다 다른 것(멀티프로필 · 이메일 · 접속)만 덧씌운다"""
        version, entries = self.store.user_directory()
        overrides = self.store.profile_overrides_for_viewer(viewer)
        admin = self.store.admin_scope(viewer) is not None
        cache = self._user_json_cache
        if cache[0] != version or version < 0:
            cache = (version, {})
            self._user_json_cache = cache
        pieces = cache[1]
        parts: list[str] = []
        names: list[str] = []
        for on, off in entries:
            name = on['username']
            names.append(name)
            is_online = name in online or on['is_bot']
            base = on if is_online else off
            override = overrides.get(name)
            if override:                     # 멀티프로필: 이 사람에게만 다르게 보인다 - 조각을 나누어 쓰지 않는다
                parts.append(json.dumps(self._private_view(self._apply_profile(base, override), viewer, admin),
                                        ensure_ascii=False))
                continue
            hide = not (admin or name == viewer or not base.get('email'))
            key = (name, is_online, hide)
            piece = pieces.get(key)
            if piece is None:
                piece = json.dumps({**base, 'email': ''} if hide else base, ensure_ascii=False)
                pieces[key] = piece
            parts.append(piece)
        return '[' + ', '.join(parts) + ']', names

    def _users_for(self, viewer: str, online: set[str]) -> list[dict]:
        """viewer 에게 보낼 사용자 목록 (멀티프로필 적용, 조회 1회). welcome 은 같은 결과를 캐시로 만드는
        _users_json_for 를 쓴다 - 이 함수는 그 기준(테스트가 둘을 비교한다)"""
        overrides = self.store.profile_overrides_for_viewer(viewer)
        admin = self.store.admin_scope(viewer) is not None
        return [self._private_view(self._apply_profile(u, overrides.get(u['username'])), viewer, admin)
                for u in self.store.list_users(online)]

    def _broadcast_user(self, username: str, ctx: Optional[Ctx] = None, exclude_user: Optional[str] = None) -> None:
        """user_updated 를 수신자별(멀티프로필)로 만들어 모든 접속자에게 보낸다."""
        for conn, frame in self._user_updated_frames(username, self._all_joined_conns(), ctx, exclude_user):
            conn.send_frame(frame)

    def _user_updated_frames(self, username: str, conns: list[Connection], ctx: Optional[Ctx] = None,
                             exclude_user: Optional[str] = None) -> list[tuple[Connection, bytes]]:
        """user_updated 한 줄을 받는 연결마다 → [(연결, 줄)]. 보이는 모양(멀티프로필 · 이메일)이 같은 연결끼리는
        한 번 만든 줄을 함께 쓴다 (예전에는 연결마다 만들어 접속자 수만큼 직렬화했다)"""
        base = self.store.get_user(username, self._online_users())
        if base is None:
            return []
        overrides = self.store.profile_overrides_for_owner(username)
        admins = set(self.store.admin_usernames())
        made: dict[Any, Optional[bytes]] = {}
        out: list[tuple[Connection, bytes]] = []
        for conn in conns:
            viewer = conn.username
            if exclude_user is not None and viewer == exclude_user:
                continue
            override = overrides.get(viewer)
            user = None
            if override or (ctx is not None and conn is ctx.conn and ctx.has_req_id):
                user = self._private_view(self._apply_profile(base, override), viewer, viewer in admins)
                payload = {'type': 'user_updated', 'user': user}
                if ctx is not None and conn is ctx.conn and ctx.has_req_id:
                    payload['req_id'] = ctx.req_id
                frame = encode_frame(payload)
            else:
                show = viewer in admins or viewer == username or not base.get('email')
                if show not in made:
                    made[show] = encode_frame({'type': 'user_updated', 'user': base if show else {**base, 'email': ''}})
                frame = made[show]
            if frame is not None:
                out.append((conn, frame))
        return out

    # welcome 에서 캐시로 만든 users JSON 을 끼울 자리 표시 (실행마다 다른 값 - 사용자 글자와 겹치지 않게)
    _USERS_MARK = '\x00users-' + os.urandom(8).hex() + '\x00'
    _USERS_MARK_JSON = json.dumps(_USERS_MARK)

    # ---- 접속 · 접속 종료 알림 묶기 (명세 §2 '접속 알림')
    PRESENCE_BATCH_SECONDS = 0.15     # 이 간격으로 모아 보낸다 - 로그인이 몰려도 사람마다 즉시 전원에게 보내지 않게

    def _queue_presence(self, username: str) -> None:
        """username 의 접속 상태가 바뀌었다 → 다음 묶음에 (그 사이 여러 번 바뀌면 마지막 상태 한 번만)"""
        with self._presence_lock:
            self._presence_pending[username] = now_iso()
        self._presence_wake.set()

    def _presence_loop(self) -> None:
        while not self._stop.is_set():
            self._presence_wake.wait(1.0)
            if self._stop.is_set():
                break
            if not self._presence_wake.is_set():
                continue
            self._stop.wait(self.PRESENCE_BATCH_SECONDS)      # 첫 변화 뒤 잠깐 더 모은다
            self._presence_wake.clear()
            try:
                self._flush_presence()
            except Exception:
                logger.exception('접속 알림 오류')

    def _flush_presence(self) -> None:
        """모아 둔 접속 상태를 보낸다: 사람마다 user_status_changed → user_updated (명세 §2), 받는 연결마다 한 번에 쓴다"""
        with self._presence_lock:
            pending, self._presence_pending = self._presence_pending, {}
        if not pending or self._stop.is_set():
            return
        conns = self._all_joined_conns()
        online = self._online_users()
        blobs: dict[Connection, list[bytes]] = {conn: [] for conn in conns}
        for username, stamp in pending.items():
            status = 'online' if username in online else 'offline'
            frame = encode_frame({'type': 'user_status_changed', 'username': username, 'status': status,
                                  'timestamp': stamp})
            if frame is None:
                continue
            for conn in conns:
                if conn.username != username:            # 본인에게는 보내지 않는다 (예전과 같다)
                    blobs[conn].append(frame)
            for conn, line in self._user_updated_frames(username, conns, exclude_user=username):
                blobs[conn].append(line)
        for conn, lines in blobs.items():
            if lines:
                conn.send_frame(b''.join(lines))

    def _user_conns(self, usernames: Any) -> list[Connection]:
        with self._conns_lock:
            out: list[Connection] = []
            for u in usernames:
                out.extend(self._by_user.get(u, ()))
            return out

    def _all_joined_conns(self) -> list[Connection]:
        with self._conns_lock:
            return [c for cs in self._by_user.values() for c in cs]

    def _deliver(self, conns: list[Connection], payload: dict, ctx: Optional[Ctx] = None,
                 droppable: bool = False) -> None:
        """같은 내용을 여러 연결에 보낸다 - 한 번만 만들어 각 연결의 대기열에 세운다 (보내는 쪽은 기다리지 않는다)"""
        frame: Optional[bytes] = None
        for conn in conns:
            if ctx is not None and conn is ctx.conn and ctx.has_req_id:
                conn.send({**payload, 'req_id': ctx.req_id}, droppable=droppable)
                continue
            if frame is None:
                frame = encode_frame(payload)
                if frame is None:
                    return
            conn.send_frame(frame, droppable=droppable)

    def _push(self, usernames: Any, payload: dict, ctx: Optional[Ctx] = None) -> None:
        self._deliver(self._user_conns(set(usernames)), payload, ctx)

    def _broadcast(self, payload: dict, ctx: Optional[Ctx] = None, exclude_user: Optional[str] = None) -> None:
        conns = [c for c in self._all_joined_conns() if c.username != exclude_user]
        self._deliver(conns, payload, ctx)

    def _reply(self, ctx: Ctx, payload: dict) -> None:
        if ctx.has_req_id:
            payload = {**payload, 'req_id': ctx.req_id}
        ctx.conn.send(payload, wait=True)      # 자기 요청의 응답: 많이 밀려 있으면 이 연결의 다음 처리만 늦춘다

    def _limit_action(self, username: str, action: str) -> None:
        """사용자별 동작 횟수 제한 (ACTION_LIMITS). 넘으면 code:"rate_limited" 오류"""
        allowed, wait = self._actions.hit(username, action, *self.ACTION_LIMITS[action])
        if not allowed:
            raise ProtocolError(RATE_LIMITED, code='rate_limited', retry_after=wait)

    def _send_error(self, conn: Connection, req_type: str, message: str, request: Optional[dict],
                    code: str = '', extra: Optional[dict] = None) -> None:
        payload: dict = {'type': 'error', 'req_type': req_type, 'message': message, **(extra or {})}
        code = code or ERROR_CODES.get(message, '')
        if code:                               # 구분 코드가 있는 오류만 (없는 것은 예전과 같은 모양)
            payload['code'] = code
        if isinstance(request, dict) and 'req_id' in request:
            payload['req_id'] = request['req_id']
        conn.send(payload)

    # PROXY protocol v1 (haproxy 명세 §2.1): 'PROXY TCP4 <src> <dst> <sport> <dport>\r\n', 최대 107 바이트.
    PROXY_V1_MAX = 107

    def _read_proxy_header(self, conn: Connection) -> bytes:
        """연결 첫 줄이 PROXY v1 헤더면 읽어서 conn.address 를 실제 클라이언트 (ip, port) 로 바꾸고,
        헤더 뒤에 같이 도착한 나머지 바이트를 돌려준다. 헤더가 아니면(직접 접속) 읽은 바이트를 그대로 돌려준다."""
        buf = b''
        while b'\r\n' not in buf:
            if len(buf) >= self.PROXY_V1_MAX:
                return buf                     # 헤더가 아니다 — 평문 요청으로 취급
            try:
                chunk = conn.sock.recv(self.PROXY_V1_MAX - len(buf))
            except socket.timeout:
                if not conn.alive or self._stop.is_set() \
                        or time.monotonic() - conn.connected_at > self.PRELOGIN_SECONDS:
                    conn.close()
                    return b''
                continue
            except OSError:
                return b''
            if not chunk:
                return buf
            buf += chunk
            if not b'PROXY '.startswith(buf[:6]) and not buf.startswith(b'PROXY '):
                return buf                     # 시작부터 다르면 더 읽지 않는다
        line, rest = buf.split(b'\r\n', 1)
        parts = line.decode('ascii', 'replace').split(' ')
        if len(parts) == 6 and parts[0] == 'PROXY' and parts[1] in ('TCP4', 'TCP6'):
            try:
                conn.address = (parts[2], int(parts[4]))
            except ValueError:
                pass
        # 'PROXY UNKNOWN' 은 주소 없이 통과 (헬스체크 등)
        return rest

    def _client_loop(self, conn: Connection) -> None:
        decoder = FrameDecoder()
        decoder.limit = self.PRELOGIN_MAX_CHARS             # 로그인 전에는 작은 요청만 받는다
        try:
            if self.proxy_protocol:
                rest = self._read_proxy_header(conn)
                if rest:
                    for item in decoder.feed(rest):
                        if not conn.alive:
                            break
                        self._handle_item(conn, item)
            with self._conns_lock:                          # 주소가 정해진 뒤: 한 주소의 로그인 전 연결 수
                waiting = sum(1 for c in self._conns if c.username is None and c.ip == conn.ip)
            if waiting > self.MAX_PRELOGIN_PER_IP:
                logger.warning('로그인 전 연결이 너무 많은 주소: %s', conn.ip)
                return
            while conn.alive and not self._stop.is_set():
                if conn.username is None:
                    if time.monotonic() - conn.connected_at > self.PRELOGIN_SECONDS:
                        self._send_error(conn, '', '로그인하지 않아 연결을 닫습니다.', None, 'login_timeout')
                        break
                elif decoder.limit != MAX_BUFFER_CHARS:
                    decoder.limit = MAX_BUFFER_CHARS
                try:
                    data = conn.sock.recv(RECV_SIZE)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                conn.last_recv = time.monotonic()
                try:
                    items = decoder.feed(data)
                except OverflowError:
                    self._send_error(conn, '', '요청이 너무 큽니다.', None, 'too_large')
                    break
                for item in items:
                    if not conn.alive:
                        break
                    self._handle_item(conn, item)
        finally:
            self._disconnect(conn)

    def _handle_item(self, conn: Connection, item: Any) -> None:
        if isinstance(item, ValueError):
            self._send_error(conn, '', str(item), None)
            return
        if not isinstance(item, dict) or not isinstance(item.get('type'), str):
            self._send_error(conn, '', '요청 형식이 올바르지 않습니다.', item if isinstance(item, dict) else None)
            return
        req_type = item['type']
        if not _tag_ok(item.get('req_id')):        # 응답에 그대로 돌려주는 값이라 짧은 표식만 받는다 (§4)
            self._send_error(conn, req_type[:40], 'req_id 값이 올바르지 않습니다.', None)
            return
        try:
            if req_type == 'join':
                self._h_join(conn, item)
                return
            if req_type == 'register':
                self._h_register(conn, item)
                return
            if req_type == 'password_reset':   # 로그인 전에도 (로그인 창의 비밀번호 재설정)
                self._h_password_reset(conn, item)
                return
            if req_type == 'org_companies':     # 로그인 전에도 (처음 사용 등록 화면)
                payload = {'type': 'org_companies', 'companies': self.store.company_names(),
                           'open_registration': self.open_registration}
                if 'req_id' in item:
                    payload['req_id'] = item['req_id']
                conn.send(payload)
                return
            if req_type == 'leave':
                conn.close()
                return
            if req_type in ('ping', 'pong'):       # 하트비트 (명세 §2): ping 에는 pong 으로 답하고, pong 은 받기만 한다
                conn.heartbeat = True
                if req_type == 'ping':
                    conn.send({'type': 'pong', **({'req_id': item['req_id']} if 'req_id' in item else {})})
                return
            if conn.username is None:
                raise ProtocolError(LOGIN_REQUIRED)
            if req_type == 'call_media':
                if conn.media_bucket is not None and not conn.media_bucket.take():
                    return                         # 너무 잦은 미디어 조각은 조용히 버린다
            elif conn.bucket is not None and not conn.bucket.take():
                raise ProtocolError(RATE_LIMITED, code='rate_limited', retry_after=1)
            handler = self._handlers.get(req_type)
            if handler is None:
                raise ProtocolError(f'알 수 없는 요청입니다: {req_type}')
            ctx = Ctx(conn=conn, username=conn.username, req_type=req_type,
                      req_id=item.get('req_id'), has_req_id='req_id' in item)
            handler(ctx, item)
        except ProtocolError as exc:
            self._send_error(conn, req_type, str(exc), item, exc.code, exc.extra)
        except Exception:  # 한 요청의 오류가 연결을 끊지 않도록
            logger.exception('요청 처리 중 오류: %s', req_type)
            self._send_error(conn, req_type, SERVER_ERROR, item, 'server_error')

    def _disconnect(self, conn: Connection) -> None:
        conn.close()
        went_offline = False
        username = conn.username
        with self._conns_lock:
            self._conns.discard(conn)
            if username is not None:
                conns = self._by_user.get(username)
                if conns is not None:
                    conns.discard(conn)
                    if not conns:
                        del self._by_user[username]
                        went_offline = True
        if went_offline and not self._stop.is_set():
            logger.info('%s 오프라인', username)
            self._queue_presence(username)    # 접속 종료 알림도 묶어서 (_flush_presence)
            self._leave_all_calls(username)   # 접속 종료 = 통화 나가기

    # ------------------------------------------------------------------ 공용 헬퍼
    def _require_room_member(self, room_id: Any, username: str) -> dict:
        if not isinstance(room_id, str) or not room_id:
            raise ProtocolError('room_id 값이 올바르지 않습니다.')
        meta = self.store.room_meta(room_id)
        if meta is None:
            raise ProtocolError('존재하지 않는 채팅방입니다.')
        if username not in meta['members']:
            raise ProtocolError('이 채팅방의 대화상대가 아닙니다. 초대를 받은 뒤 이용할 수 있습니다.')
        return meta

    def _require_message_member(self, msg_id: int, username: str) -> dict:
        message = self.store.get_message(msg_id)
        if message is None:
            raise ProtocolError('존재하지 않는 메시지입니다.')
        if not self.store.is_member(message['room_id'], username):
            raise ProtocolError('이 채팅방의 대화상대가 아닙니다. 초대를 받은 뒤 이용할 수 있습니다.')
        if not self.store.message_visible(msg_id, username):     # 내가 들어오기 전의 메시지 (§4.5)
            raise ProtocolError('존재하지 않는 메시지입니다.')
        return message

    def _push_room_updated(self, room_id: str, usernames: Any, ctx: Optional[Ctx] = None) -> None:
        """room_updated 는 수신자별 unread_count 가 다르므로 사용자마다 계산해 보낸다."""
        for u in set(usernames):
            conns = self._user_conns([u])
            if not conns:
                continue
            room = self.store.room_dict(room_id, u)
            if room is not None:
                self._deliver(conns, {'type': 'room_updated', 'room': room}, ctx)

    def _post_system(self, room_id: str, text: str) -> None:
        message = self.store.add_message(room_id, '', 'system', text)
        self._push(self.store.members(room_id), {'type': 'msg_new', 'message': message, 'client_tmp_id': None})

    def _publish_new_message(self, message: dict, ctx: Optional[Ctx] = None,
                             client_tmp_id: Any = None) -> None:
        """msg_new + 발신자 read_update 를 방 멤버에게 전송."""
        room_id = message['room_id']
        members = self.store.members(room_id)
        self._push(self._message_recipients(members, message), {'type': 'msg_new', 'message': message,
                                                                 'client_tmp_id': client_tmp_id}, ctx)
        sender = message['sender']
        if sender:
            self._push(members, {'type': 'read_update', 'room_id': room_id, 'username': sender,
                                 'last_read_msg_id': self.store.read_pointer(room_id, sender)})

    def _message_recipients(self, members: list[str], message: dict) -> list[str]:
        """발신자를 차단한 멤버를 제외 (명세 §8.6 - 발신자는 차단 사실을 알 수 없다)."""
        sender = message.get('sender')
        if not sender:
            return list(members)
        blockers = self.store.blockers_of(sender)
        return [m for m in members if m not in blockers]

    def _push_msg_updated(self, message: dict, ctx: Optional[Ctx] = None) -> None:
        members = self.store.members(message['room_id'])
        hidden = self.store.hidden_from(message['room_id'], message['msg_id'])   # 그 메시지보다 나중에 들어온 사람
        recipients = [m for m in self._message_recipients(members, message) if m not in hidden]
        self._push(recipients, {'type': 'msg_updated', 'message': message}, ctx)

    def _require_not_secret(self, meta: dict) -> None:
        if meta['type'] == 'secret':
            raise ProtocolError(SECRET_FORBIDDEN)

    def _names(self, usernames: list[str]) -> str:
        return ', '.join(self.store.display_name(u) for u in usernames)

    def _require_users_exist(self, usernames: list[str]) -> None:
        missing = [u for u in usernames if not self.store.user_exists(u)]
        if missing:
            raise ProtocolError(f'존재하지 않는 사용자입니다: {", ".join(missing)}')

    # ------------------------------------------------------------------ join / 구버전
    # 연결 · 요청 한도 (명세 §2). 테스트에서 줄여 쓸 수 있게 클래스 값으로 둔다
    MAX_DAILY_UPLOAD_BYTES = 1024 * 1024 * 1024     # 한 사람이 24시간 동안 올릴 수 있는 첨부 용량
    ORPHAN_SWEEP_SECONDS = 3600.0     # 어디에서도 쓰지 않는 첨부를 치우는 간격
    RAG_WORKERS = 3                   # AI 비서의 오래 걸리는 일(답변 · 문서 학습)을 나눠 하는 작업 스레드 수
    RAG_BACKLOG = 100                 # 작업 스레드 하나에 밀려 있을 수 있는 일의 수
    MAX_CONNECTIONS = 3000            # 동시에 열려 있는 연결 수
    MAX_PRELOGIN_PER_IP = 100         # 한 주소에서 로그인하지 않고 열어 둔 연결 수
    MAX_CONNECTIONS_PER_USER = 20     # 한 계정의 동시 연결 수 - 넘으면 가장 오래된 연결을 닫는다 (2026-11-18, 명세 §2)
    PRELOGIN_SECONDS = 30.0           # 로그인하지 않은 연결을 열어 두는 시간
    PRELOGIN_MAX_CHARS = 64 * 1024    # 로그인 전 요청 한 건의 크기
    HEARTBEAT_SECONDS = 45.0          # 서버가 이만큼 아무것도 보내지 않은 연결에 ping 을 보낸다
    HEARTBEAT_TIMEOUT = 150.0         # ping/pong 을 하는 클라이언트가 이만큼 조용하면 끊는다
    REQUEST_RATE, REQUEST_BURST = 200.0, 2000.0     # 연결당 요청: 초당 200개, 한꺼번에 2,000개까지
    MEDIA_RATE, MEDIA_BURST = 150.0, 450.0          # 연결당 통화 미디어 조각 (넘으면 조용히 버린다)
    # 사용자별 동작 횟수 (동작: (횟수, 초)) - 넘으면 code:"rate_limited"
    ACTION_LIMITS = {'friend_lookup': (10, 60), 'ai_question': (20, 60), 'room_export': (6, 60),
                     'story_post': (20, 60), 'rag_doc_add': (20, 60), 'search': (120, 60), 'upload': (120, 60)}

    # 로그인 실패 제한 (명세 §4.1 '보안'). 5분(LOGIN_LOCK_SECONDS) 안의 실패 횟수로 본다
    #   id     : 한 주소(IP)에서 한 아이디 - 다른 주소에서 로그인하는 본인은 막히지 않는다
    #   id_all : 한 아이디 전체(여러 주소에서 나눠 시도하는 경우)
    #   ip     : 한 주소 전체 - 사무실이 주소 하나를 같이 쓰므로 넉넉하게
    LOGIN_MAX_FAILURES = {'id': 5, 'id_all': 20, 'ip': 100}
    LOGIN_LOCK_SECONDS = 300
    LOGIN_FAILURE_KEYS_MAX = 5000              # 기록이 이보다 많아지면 지난 것을 치운다
    _LOGIN_LIMIT_OF = {'at': 'id', 'id': 'id_all', 'ip': 'ip'}

    def _login_keys(self, conn: Connection, login_id: str) -> list[str]:
        """[이 주소에서 이 아이디, 아이디 전체, 주소 전체] - 성공하면 첫 번째만 지운다"""
        ip = conn.address[0] if isinstance(conn.address, tuple) and conn.address else str(conn.address)
        login_id = login_id.lower()
        return [f'at:{login_id}|{ip}', f'id:{login_id}', f'ip:{ip}']

    def _check_login_throttle(self, keys: list[str]) -> None:
        now = time.monotonic()
        with self._login_failures_lock:
            for key in keys:
                recent = [t for t in self._login_failures.get(key, []) if now - t < self.LOGIN_LOCK_SECONDS]
                if recent:
                    self._login_failures[key] = recent
                else:                    # 없는 아이디로 계속 시도해도 빈 목록이 쌓이지 않게
                    self._login_failures.pop(key, None)
                if len(recent) >= self.LOGIN_MAX_FAILURES[self._LOGIN_LIMIT_OF[key.split(':', 1)[0]]]:
                    seconds = max(1, int(self.LOGIN_LOCK_SECONDS - (now - recent[0])) + 1)
                    raise ProtocolError(f'로그인 실패가 너무 많습니다. {(seconds - 1) // 60 + 1}분 뒤에 다시 시도해 주세요.',
                                        code='throttled', retry_after=seconds)

    def _record_login_failure(self, keys: list[str]) -> None:
        now = time.monotonic()
        with self._login_failures_lock:
            for key in keys:
                self._login_failures.setdefault(key, []).append(now)
            if len(self._login_failures) > self.LOGIN_FAILURE_KEYS_MAX:
                for key in [k for k, times in self._login_failures.items()
                            if not times or now - times[-1] >= self.LOGIN_LOCK_SECONDS]:
                    del self._login_failures[key]

    def _clear_login_failures(self, keys: list[str]) -> None:
        with self._login_failures_lock:
            for key in keys:
                self._login_failures.pop(key, None)

    def _authenticate(self, login_id: str, password: Any, token: Any = None) -> tuple[str, bool]:
        """아이디/비밀번호(또는 자동 로그인 토큰) 확인 (명세 §4.1) → (username, 새로 만든 계정인지)"""
        if self.require_password and password is None and token is None:
            raise ProtocolError(PASSWORD_REQUIRED)
        if token is not None:
            username = self.store.resolve_login(login_id)
            if not isinstance(token, str) or not username or len(token) > 128 \
                    or not self.store.check_login_token(username, token):
                raise ProtocolError(LOGIN_TOKEN_EXPIRED)
            self._check_not_disabled(username)
            return username, False
        if password is not None and (not isinstance(password, str) or not 4 <= len(password) <= 64):
            raise ProtocolError('비밀번호는 4~64자로 입력해 주세요.')
        username = self.store.resolve_login(login_id)
        if username is not None and self.store.is_bot(username):
            raise ProtocolError('봇 계정으로는 로그인할 수 없습니다.')
        stored = self.store.password_hash(username) if username else None
        if stored:
            if password is None:
                raise ProtocolError(PASSWORD_REQUIRED)
            if not self.store.check_password(password, stored):
                raise ProtocolError(LOGIN_FAILED)
            self._check_not_disabled(username)      # 비밀번호가 맞을 때만 알린다 (계정 유무를 드러내지 않게)
            return username, False
        if password is not None:            # 비밀번호 등록은 [처음 사용 등록](register)에서 한 번만
            if self.open_registration:
                raise ProtocolError(NEEDS_REGISTER)
            # 등록이 닫힌 서버: 없는 아이디 · 비밀번호 없는 계정도 틀린 비밀번호와 똑같이 답한다 (걸리는 시간도 비슷하게)
            if self._dummy_hash is None:
                self._dummy_hash = Store.hash_password('-')
            self.store.check_password(password, self._dummy_hash)
            raise ProtocolError(LOGIN_FAILED)
        if username is None and (len(login_id) > 20 or '@' in login_id):
            raise ProtocolError('사용자 이름은 20자 이내로 입력해 주세요.')
        if username is None and not self.open_registration:
            raise ProtocolError(LOGIN_FAILED)
        if username is not None:
            self._check_not_disabled(username)
        created = self.store.ensure_user(username or login_id)   # 구버전·테스트 클라이언트 (비밀번호 없이)
        return username or login_id, created

    PASSWORD_MAX = 64

    @property
    def password_min(self) -> int:
        """새로 정하는 비밀번호의 최소 길이 (명세 §4.1): 비밀번호 필수 서버 8자, 구버전 · 테스트 서버 4자.
        로그인은 예전에 정한 짧은 비밀번호도 그대로 받는다"""
        return 8 if self.require_password else 4

    def _new_password(self, value: Any) -> str:
        if not isinstance(value, str) or not self.password_min <= len(value) <= self.PASSWORD_MAX:
            raise ProtocolError(f'비밀번호는 {self.password_min}~{self.PASSWORD_MAX}자로 입력해 주세요.')
        return value

    def _drop_sessions(self, username: str, keep: Optional[Connection] = None) -> None:
        """그 계정의 접속 중인 연결을 끊는다 (비밀번호가 바뀌었을 때). keep 은 남긴다"""
        for conn in self._user_conns([username]):
            if conn is not keep:
                conn.send({'type': 'error', 'req_type': '', 'code': 'session_expired',
                           'message': '비밀번호가 바뀌어 다시 로그인해야 합니다.'})
                self._disconnect(conn)

    def _h_password_change(self, ctx: Ctx, msg: dict) -> None:
        """본인 비밀번호 변경 (명세 §4.1): 지금 비밀번호 확인 → 새 비밀번호 + 자동 로그인 토큰 모두 폐기 + 다른 연결 끊기"""
        keys = self._login_keys(ctx.conn, ctx.username)
        self._check_login_throttle(keys)
        old = msg.get('old_password')
        stored = self.store.password_hash(ctx.username)
        if not isinstance(old, str) or not stored or not self.store.check_password(old, stored):
            self._record_login_failure(keys)
            raise ProtocolError('지금 비밀번호가 올바르지 않습니다.', code='auth_failed')
        password = self._new_password(msg.get('new_password'))
        self.store.set_password(ctx.username, password)
        self.store.revoke_all_login_tokens(ctx.username)
        self._clear_login_failures(keys[:1])
        self._drop_sessions(ctx.username, keep=ctx.conn)
        token = self.store.issue_login_token(ctx.username) if msg.get('remember') is True else None
        self._reply(ctx, {'type': 'password_changed', **({'login_token': token} if token else {})})
        logger.info('%s 비밀번호 변경 (%s)', ctx.username, ctx.conn.address)

    def _check_not_disabled(self, username: str) -> None:
        if self.store.is_disabled(username):
            raise ProtocolError(ACCOUNT_DISABLED)

    def _h_join(self, conn: Connection, msg: dict) -> None:
        username = _req_str(msg, 'username', 120, label='아이디')
        if any(ord(ch) < 32 for ch in username):
            raise ProtocolError('사용자 이름에 사용할 수 없는 문자가 있습니다.')
        if username == BOT_USERNAME or self.store.is_bot(username):
            raise ProtocolError('봇 계정으로는 로그인할 수 없습니다.')
        if conn.username is not None:
            raise ProtocolError(ALREADY_LOGGED_IN)
        keys = self._login_keys(conn, username)
        if msg.get('token') is not None:       # 자동 로그인 토큰은 맞혀 볼 수 없는 값이라 주소 전체 제한만 본다
            keys = keys[2:]                    # (아이디가 잠겨 있어도 로그인을 저장해 둔 본인은 들어온다)
        self._check_login_throttle(keys)
        try:
            username, created = self._authenticate(username, msg.get('password'), msg.get('token'))
        except ProtocolError as exc:
            if str(exc) in (LOGIN_FAILED, LOGIN_TOKEN_EXPIRED):
                self._record_login_failure(keys)
            if str(exc) == LOGIN_FAILED and not self.open_registration:
                raise ProtocolError(LOGIN_FAILED_CLOSED) from None      # 닫힌 서버: 등록 안내를 덧붙인다
            raise
        self._clear_login_failures(keys[:1])
        self._complete_login(conn, msg, 'join', username, created,
                             remember=msg.get('remember') is True and msg.get('password') is not None)

    def _h_register(self, conn: Connection, msg: dict) -> None:
        """처음 사용 등록: 소속 회사 + 이름 + 이메일 + 비밀번호 (명세 §4.1). 성공하면 join 과 같은 welcome.
        다음부터는 이메일과 비밀번호로 로그인한다. employee_no(사번)는 선택 — 2026-10-14 부터 클라이언트가 보내지 않는다"""
        if conn.username is not None:
            raise ProtocolError(ALREADY_LOGGED_IN)
        company = msg.get('company')
        if not isinstance(company, str) or company.strip() not in self.store.company_names():
            raise ProtocolError('소속 회사를 선택해 주세요.')
        name = _req_str(msg, 'name', 20, label='이름')
        if any(ord(ch) < 32 for ch in name) or '@' in name:
            raise ProtocolError('이름에 사용할 수 없는 문자가 있습니다.')
        if name == BOT_USERNAME or self.store.is_bot(name):
            raise ProtocolError('봇 계정으로는 등록할 수 없습니다.')
        employee_no = msg.get('employee_no').strip() if isinstance(msg.get('employee_no'), str) else ''
        if employee_no and not EMPLOYEE_NO_RE.fullmatch(employee_no):
            raise ProtocolError('사번은 영문·숫자 20자 이내로 입력해 주세요.')
        email = msg.get('email').strip() if isinstance(msg.get('email'), str) else ''
        if len(email) > 120 or not EMAIL_RE.fullmatch(email):
            raise ProtocolError('이메일 주소 형식이 올바르지 않습니다.')
        password = self._new_password(msg.get('password'))
        code = msg.get('code')                 # 관리자가 발급한 등록 코드 (등록이 닫힌 서버에서 필요)
        if not isinstance(code, str) or len(code) > 20:
            code = None
        keys = self._login_keys(conn, name)
        self._check_login_throttle(keys)
        if self.open_registration:             # 닫힌 서버는 사용 중지도 같은 문구로 거절한다 (store 가 함께 본다)
            self._check_not_disabled(name)
        try:
            created = self.store.register_user(name, company.strip(), email, password, employee_no, code=code,
                                               open_registration=self.open_registration)
        except ValueError as exc:
            self._record_login_failure(keys)   # 남의 조직도 계정 이메일·사번을 맞혀 보는 것도 로그인 실패처럼 막는다
            raise ProtocolError(str(exc), code=ERROR_CODES.get(str(exc), 'register_rejected')) from None
        self._complete_login(conn, msg, 'register', name, created, remember=msg.get('remember') is True)

    def _h_password_reset(self, conn: Connection, msg: dict) -> None:
        """비밀번호 재설정: 로그인 아이디 + 가입 이메일이 맞으면 새 비밀번호 (명세 §4.1). 로그인은 하지 않는다"""
        login_id = _req_str(msg, 'username', 120, label='아이디')
        email = msg.get('email').strip() if isinstance(msg.get('email'), str) else ''
        if not email or len(email) > 120:
            raise ProtocolError('가입한 이메일을 입력해 주세요.')
        password = self._new_password(msg.get('new_password'))
        code = msg.get('code')
        if not isinstance(code, str) or not code.strip() or len(code) > 20:
            # 이메일만으로는 바꾸지 못한다 - 메일 서버가 없어 관리자가 본인 확인 후 일회용 코드를 알려 준다
            raise ProtocolError('관리자에게 받은 재설정 코드를 입력해 주세요.')
        keys = self._login_keys(conn, login_id)
        self._check_login_throttle(keys)
        username = self.store.resolve_login(login_id)
        closed = not self.open_registration    # 등록이 닫힌 서버: 계정 상태(없음 · 미등록 · 사용 중지 · 관리자)를 알려 주지 않는다
        if username and not closed:
            self._check_not_disabled(username)
        try:
            usable = bool(username) and EMAIL_RE.fullmatch(email) is not None \
                and not (closed and self.store.is_disabled(username))
            changed = self.store.reset_password(username, email, password, code) if usable else False
        except ValueError as exc:          # 관리자 계정은 이메일만으로 바꾸지 못한다
            if not closed:
                raise ProtocolError(str(exc)) from None
            changed = False
        if changed is None and closed:
            changed = False
        if changed is None:
            raise ProtocolError(NEEDS_REGISTER)
        if not changed:
            self._record_login_failure(keys)   # 남의 계정을 이메일로 맞혀 보는 것을 막는다
            raise ProtocolError(RESET_MISMATCH)
        self._clear_login_failures(keys[:1])
        self._drop_sessions(username)          # 예전 비밀번호로 들어와 있던 연결은 끊는다
        payload = {'type': 'password_reset_ok', 'username': username}
        if 'req_id' in msg:
            payload['req_id'] = msg['req_id']
        conn.send(payload)
        logger.info('%s 비밀번호 재설정 (%s)', username, conn.address)

    def _complete_login(self, conn: Connection, msg: dict, req_type: str, username: str, created: bool,
                        remember: bool) -> None:
        """join/register 성공 뒤: welcome + 접속 알림"""
        login_token = self.store.issue_login_token(username) if remember else None   # 자동 로그인용 (명세 §4.1)
        conn.hold_others()                     # 로그인 응답이 이 연결로 가는 다른 알림보다 먼저 가게 (§2)
        try:
            first_conn = self._send_welcome(conn, msg, req_type, username, login_token)
        finally:
            conn.release_others()
        if created or first_conn:
            # 접속 알림(user_status_changed + user_updated)은 묶어서 보낸다 (_flush_presence, 명세 §2)
            self._queue_presence(username)
        if self.daily_briefing and first_conn and username != BOT_USERNAME \
                and self.store.take_briefing_slot(username, date.today().isoformat()):
            self._rag_queue.put(lambda: self._send_briefing(username))
        logger.info('%s 접속 (%s)', username, conn.address)

    def _send_welcome(self, conn: Connection, msg: dict, req_type: str, username: str,
                      login_token: Optional[str]) -> bool:
        """접속 목록에 넣고 welcome · all_users_list 를 보낸다 → 그 사용자의 첫 연결이었나"""
        with self._conns_lock:
            conns = self._by_user.setdefault(username, set())
            first_conn = not conns
            conns.add(conn)
            conn.username = username
            # 로그인한 계정이 연결을 계속 열어 받는 버퍼(연결당 최대 MAX_BUFFER_CHARS)로 메모리를 채우지 못하게
            excess = sorted((c for c in conns if c is not conn), key=lambda c: c.connected_at)
            excess = excess[:max(0, len(conns) - max(1, int(self.MAX_CONNECTIONS_PER_USER)))]
        for old in excess:
            logger.warning('%s 의 연결이 %d개를 넘어 가장 오래된 연결을 닫습니다 (%s)', username,
                           self.MAX_CONNECTIONS_PER_USER, old.address)
            old.close(force=True)               # 읽기 스레드가 깨어나 접속 목록에서 뺀다
        conn.bucket = TokenBucket(self.REQUEST_RATE, self.REQUEST_BURST)
        conn.media_bucket = TokenBucket(self.MEDIA_RATE, self.MEDIA_BURST)
        try:
            conn.sock.settimeout(SOCKET_TIMEOUT)
        except OSError:
            pass
        ctx = Ctx(conn=conn, username=username, req_type=req_type, req_id=msg.get('req_id'),
                  has_req_id='req_id' in msg)
        with self._welcome_gate:            # 로그인이 몰려도 만들기는 WELCOME_BUILDS 개씩 (보내기는 밖에서)
            online = self._online_users()
            text, usernames = self._welcome_text(ctx, username, online, login_token)
        conn.send_frame((text + '\n').encode('utf-8', errors='replace'), wait=True)
        conn.send({'type': 'all_users_list',
                   'users': usernames,                  # welcome 의 users 와 같은 목록 · 같은 순서 (다시 조회하지 않는다)
                   'online_users': sorted(online)})
        return first_conn

    # 동시에 만드는 welcome 수 (2026-11-18 측정): 파이썬 작업이라 여럿이 동시에 만들면 GIL 을 다퉈 모두 늦어진다 -
    # 100명 동시 로그인의 로그인 p95 가 제한 없음 0.53~0.62초 / 1개 0.47~0.50초 (scripts/bench_server_requests.py,
    # 방 목록 읽기를 줄인 뒤 1개로 0.34~0.40초)
    WELCOME_BUILDS = 1

    def _welcome_text(self, ctx: Ctx, username: str, online: set[str], login_token: Optional[str]) -> tuple[str, list[str]]:
        """welcome 한 줄(JSON 글자, 줄바꿈 없음)과 사용자 이름 목록"""
        users_json, usernames = self._users_json_for(username, online)
        welcome = {
            'type': 'welcome',
            'me': self.store.get_user(username, online),
            'users': self._USERS_MARK,          # 캐시로 만든 JSON 배열을 이 자리에 끼운다 (아래)
            'rooms': self.store.rooms_for_user(username),
            'org': self.store.org_payload(),
            'calls': self._calls_for_user(username),
            'blocked': self.store.blocked_list(username),
            'friends': self.store.friends_of(username),
            'file_policy': file_policy.public_policy(self.store.file_policy()),
            # 서버 정책 (§4.1): 등록 화면의 코드 칸 · 프로필의 이메일 칸을 여기에 맞춘다
            'policy': {'open_registration': self.open_registration, 'email_editable': self.open_registration,
                       'display_name_editable': self.open_registration},
            **({'login_token': login_token} if login_token else {}),
        }
        if ctx.has_req_id:
            welcome['req_id'] = ctx.req_id
        return json.dumps(welcome, ensure_ascii=False).replace(self._USERS_MARK_JSON, users_json, 1), usernames

    def _h_legacy_message(self, ctx: Ctx, msg: dict) -> None:
        if self.require_password:      # 모두에게 가는 구버전 요청은 비밀번호 없는 구버전 · 테스트 서버에서만 (§4)
            raise ProtocolError(UNSUPPORTED_REQUEST)
        content = msg.get('content')
        if not isinstance(content, str) or not _utf8_ok(content):
            raise ProtocolError('content 값이 올바르지 않습니다.')
        if len(content) > MAX_CONTENT_LEN:
            raise ProtocolError(f'메시지는 {MAX_CONTENT_LEN}자를 넘을 수 없습니다.')
        self._broadcast({'type': 'message', 'sender': ctx.username, 'content': content,
                         'timestamp': datetime.now().isoformat()})

    # ------------------------------------------------------------------ 채팅방
    def _h_room_open_direct(self, ctx: Ctx, msg: dict) -> None:
        target = _req_str(msg, 'target', 20, label='대상')
        self._require_users_exist([target])
        room_id, _added = self.store.get_or_create_direct(ctx.username, target)
        self._push_room_updated(room_id, {ctx.username, target}, ctx)

    def _h_room_create(self, ctx: Ctx, msg: dict) -> None:
        name = _opt_name(msg, 'name', 50)
        members = [m for m in _str_list(msg, 'members') if m != ctx.username]
        if not members:
            raise ProtocolError('초대할 친구를 1명 이상 선택하세요.')
        if len(members) > MAX_INVITE:
            raise ProtocolError('한 번에 초대할 수 있는 인원을 초과했습니다.')
        self._require_users_exist(members)
        room_id = self.store.create_group(ctx.username, name, members)
        self._push_room_updated(room_id, [ctx.username] + members, ctx)
        self._post_system(room_id, f'{self.store.display_name(ctx.username)}님이 {self._names(members)}님을 초대했습니다.')

    def _h_room_invite(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        if meta['type'] != 'group':
            raise ProtocolError('1:1 채팅방에는 초대할 수 없습니다.')
        usernames = [u for u in _str_list(msg, 'usernames') if u not in meta['members']]
        if not usernames:
            raise ProtocolError('새로 초대할 친구가 없습니다.')
        if len(usernames) > MAX_INVITE:
            raise ProtocolError('한 번에 초대할 수 있는 인원을 초과했습니다.')
        self._require_users_exist(usernames)
        self.store.add_members(meta['room_id'], usernames)
        self._push_room_updated(meta['room_id'], meta['members'] + usernames, ctx)
        self._post_system(meta['room_id'],
                          f'{self.store.display_name(ctx.username)}님이 {self._names(usernames)}님을 초대했습니다.')

    def _h_room_leave(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        room_id = meta['room_id']
        leaver = self._member_label(meta, ctx.username)
        self.store.remove_member(room_id, ctx.username)
        self._leave_all_calls(ctx.username, room_id)
        self._push([ctx.username], {'type': 'room_removed', 'room_id': room_id}, ctx)
        remaining = [u for u in meta['members'] if u != ctx.username]
        if remaining:
            new_owner = None
            if meta.get('owner') == ctx.username:   # 방장이 나가면 가장 먼저 들어온 멤버가 방장 (§10.3)
                new_owner = self.store.room_meta(room_id).get('owner')
                if new_owner:
                    self.store.set_owner(room_id, new_owner)
            self._push_room_updated(room_id, remaining)
            self._post_system(room_id, f'{leaver}님이 나갔습니다.')
            if new_owner:
                self._post_system(room_id, f'{self._member_label(meta, new_owner)}님이 방장이 되었습니다.')

    def _require_room_owner(self, msg: dict, username: str) -> dict:
        meta = self._require_room_member(msg.get('room_id'), username)
        if meta['type'] not in ('group', 'open'):
            raise ProtocolError('그룹 채팅방과 오픈채팅방에서만 할 수 있습니다.')
        if meta.get('owner') != username:
            raise ProtocolError('방장만 할 수 있습니다.')
        return meta

    def _h_room_kick(self, ctx: Ctx, msg: dict) -> None:
        """방장이 멤버를 내보낸다 (§10.3)"""
        meta = self._require_room_owner(msg, ctx.username)
        target = _req_str(msg, 'username', 20, label='대상')
        if target == ctx.username:
            raise ProtocolError('자기 자신은 내보낼 수 없습니다. 채팅방 나가기를 이용해 주세요.')
        if target not in meta['members']:
            raise ProtocolError('이 채팅방의 대화상대가 아닙니다.')
        room_id = meta['room_id']
        label = self._member_label(meta, target)
        self.store.remove_member(room_id, target)
        if meta['type'] == 'open':
            self.store.ban_from_room(room_id, target)
        self._leave_all_calls(target, room_id)
        self._push([target], {'type': 'room_removed', 'room_id': room_id, 'kicked': True})
        remaining = [u for u in meta['members'] if u != target]
        self._push_room_updated(room_id, remaining, ctx)
        self._post_system(room_id, f'{self._member_label(meta, ctx.username)}님이 {label}님을 내보냈습니다.')

    def _h_room_owner_set(self, ctx: Ctx, msg: dict) -> None:
        """방장 넘기기 (§10.3)"""
        meta = self._require_room_owner(msg, ctx.username)
        target = _req_str(msg, 'username', 20, label='대상')
        if target == ctx.username:
            raise ProtocolError('이미 방장입니다.')
        if target not in meta['members']:
            raise ProtocolError('이 채팅방의 대화상대가 아닙니다.')
        if target == BOT_USERNAME:
            raise ProtocolError('AI 비서에게는 방장을 넘길 수 없습니다.')
        self.store.set_owner(meta['room_id'], target)
        self._push_room_updated(meta['room_id'], meta['members'], ctx)
        self._post_system(meta['room_id'], f'{self._member_label(meta, target)}님이 방장이 되었습니다.')

    def _h_room_rename(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        if meta['type'] != 'group':
            raise ProtocolError('그룹 채팅방만 이름을 변경할 수 있습니다.')
        name = _req_name(msg, 'name', 50, allow_empty=True, label='채팅방 이름')
        self.store.rename_room(meta['room_id'], name)
        self._push_room_updated(meta['room_id'], meta['members'], ctx)

    def _h_history(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        before = _opt_int(msg, 'before_msg_id')
        limit = _opt_int(msg, 'limit')
        limit = 50 if limit is None else max(1, min(limit, MAX_HISTORY_LIMIT))
        messages, has_more = self.store.history(meta['room_id'], before, limit, viewer=ctx.username)
        self._reply(ctx, {'type': 'history', 'room_id': meta['room_id'], 'messages': messages,
                          'has_more': has_more})

    # ------------------------------------------------------------------ 메시지
    def _decode_attachment(self, att: Any) -> tuple[str, str, bytes]:
        if not isinstance(att, dict):
            raise ProtocolError('첨부 파일 정보가 올바르지 않습니다.')
        name = clean_file_name(_req_str(att, 'name', 200, label='파일 이름'))
        mime = _opt_str(att, 'mime', 100, 'application/octet-stream') or 'application/octet-stream'
        data = _decode_b64(att.get('data_b64'), MAX_ATTACHMENT_BYTES, '첨부 파일은 10MB를 초과할 수 없습니다.')
        return name, mime, data

    def _check_upload_quota(self, username: str, size: int) -> None:
        """하루에 올릴 수 있는 첨부 용량 (명세 §2)"""
        since = (datetime.now() - timedelta(days=1)).strftime(ISO_FMT)
        if self.store.uploaded_bytes_since(username, since) + size > self.MAX_DAILY_UPLOAD_BYTES:
            raise ProtocolError(UPLOAD_QUOTA, code='quota_exceeded')

    @staticmethod
    def _checked_image_kind(kind: str, mime: str, data: bytes) -> tuple[str, str]:
        """사진으로 다루는 것은 내용이 실제 사진(PNG/JPEG/GIF/BMP/WEBP)인 것뿐이다 (§7.1) → (kind, mime).
        사진 형식이 아닌 것을 사진으로 보내면 파일로 바꾸고, 내용과 다른 image/* 표시는 지운다"""
        sniffed = _sniff_image(data)
        if kind == 'image':
            return ('image', sniffed[0]) if sniffed else ('file', 'application/octet-stream')
        if kind == 'file' and mime.lower().startswith('image/'):
            return kind, sniffed[0] if sniffed else 'application/octet-stream'
        return kind, mime

    def _h_send(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        room_id = meta['room_id']
        kind = msg.get('kind', 'text')
        if kind not in SEND_KINDS:
            raise ProtocolError('지원하지 않는 메시지 종류입니다.')
        if meta['type'] == 'secret' and kind not in SECRET_SEND_KINDS:
            raise ProtocolError('비밀채팅방에서는 암호화된 메시지와 이모티콘만 보낼 수 있습니다.')
        if kind == 'secret' and meta['type'] != 'secret':
            raise ProtocolError('비밀채팅 메시지는 비밀채팅방에서만 보낼 수 있습니다.')
        content = msg.get('content', '')
        if content is None:
            content = ''
        if not isinstance(content, str):
            raise ProtocolError('content 값이 올바르지 않습니다.')
        if not _utf8_ok(content):
            raise ProtocolError(BAD_TEXT)
        client_tmp_id = _opt_tag(msg, 'client_tmp_id')     # 받는 사람 모두에게 그대로 가는 값
        if kind == 'secret':
            self._validate_secret_content(content)
        elif len(content) > MAX_CONTENT_LEN:
            raise ProtocolError(f'메시지는 {MAX_CONTENT_LEN}자를 넘을 수 없습니다.')
        if kind in ('text', 'emoticon') and not content.strip():
            raise ProtocolError('메시지 내용을 입력하세요.')
        if kind == 'contact' and not self.store.user_exists(content):
            raise ProtocolError('보낼 연락처를 찾을 수 없습니다. 연락처 목록에서 다시 골라 주세요.')
        if kind == 'voice':
            if not (content.isascii() and content.isdecimal()) or not 1 <= int(content) <= MAX_VOICE_SECONDS:
                raise ProtocolError(f'음성메시지는 1초에서 {MAX_VOICE_SECONDS // 60}분 사이로 녹음해 주세요.')

        reply_to_id = _opt_int(msg, 'reply_to_id')
        if reply_to_id is not None:
            orig = self.store.get_message(reply_to_id)
            if orig is None or orig['room_id'] != room_id or not self.store.message_visible(reply_to_id, ctx.username):
                raise ProtocolError('답장할 메시지를 찾을 수 없습니다.')

        thread_root_id = _opt_int(msg, 'thread_root_id')
        if thread_root_id is not None:
            self._check_thread_root(thread_root_id, meta)
            if not self.store.message_visible(thread_root_id, ctx.username):
                raise ProtocolError('스레드 원글을 찾을 수 없습니다.')
            if msg.get('send_at') is not None:
                raise ProtocolError('스레드 답글은 예약해서 보낼 수 없습니다.')

        mentions = [m for m in _str_list(msg, 'mentions', required=False, max_items=MAX_MENTIONS)
                    if m in meta['members']]

        attachment_id = None
        if kind in ATTACHMENT_KINDS:
            name, mime, data = self._decode_attachment(msg.get('attachment'))
            if kind == 'voice' and not mime.startswith('audio/'):
                raise ProtocolError('음성메시지는 오디오 파일이어야 합니다.')
            kind, mime = self._checked_image_kind(kind, mime, data)
            violation = self._file_violation(ctx.username, name, data)
            if violation is not None:          # 파일 전송 보안 (§10.10): 오류 대신 승인 요청을 낼 수 있게 알려 준다
                self._reply(ctx, {'type': 'file_blocked', 'room_id': room_id, 'kind': kind, 'name': name,
                                  'size': len(data), 'client_tmp_id': client_tmp_id, **violation,
                                  'message': f"보낼 수 없는 파일입니다 ({violation['detail']})."})
                logger.info('파일 전송 차단 %s: %s (%s)', ctx.username, name, violation['detail'])
                return
        elif msg.get('attachment') is not None:
            raise ProtocolError('텍스트 메시지에는 첨부 파일을 보낼 수 없습니다.')

        send_at = None
        if msg.get('send_at') is not None:
            send_at = _norm_iso(msg.get('send_at'), 'send_at')
            if send_at <= now_iso():
                send_at = None  # 과거/현재 시각이면 즉시 전송

        if kind in ATTACHMENT_KINDS:
            self._limit_action(ctx.username, 'upload')
            self._check_upload_quota(ctx.username, len(data))
            attachment_id = self.store.save_attachment(name, mime, data, ctx.username)

        silent = bool(msg.get('silent'))
        if send_at is not None:
            payload = {'kind': kind, 'content': content, 'reply_to_id': reply_to_id, 'mentions': mentions,
                       'attachment_id': attachment_id, 'client_tmp_id': client_tmp_id, 'silent': silent}
            scheduled_id = self.store.add_scheduled(room_id, ctx.username, send_at, payload)
            self._reply(ctx, {'type': 'scheduled_ok', 'scheduled_id': scheduled_id, 'room_id': room_id,
                              'send_at': send_at, 'content': content})
            return

        self._deliver_message(room_id, ctx.username, kind, content, reply_to_id, mentions,
                              attachment_id, client_tmp_id, ctx, silent=silent, thread_root_id=thread_root_id)

    def _check_thread_root(self, root_id: int, meta: dict) -> dict:
        """스레드를 달 수 있는 원글인지 (§10.4): 같은 방, 삭제·시스템·답글이 아님, 비밀채팅 아님"""
        if meta['type'] == 'secret':
            raise ProtocolError('비밀채팅방에서는 스레드를 쓸 수 없습니다.')
        root = self.store.get_message(root_id)
        if root is None or root['room_id'] != meta['room_id']:
            raise ProtocolError('스레드 원글을 찾을 수 없습니다.')
        if root.get('thread_root') is not None:
            raise ProtocolError('스레드 답글에는 다시 스레드를 달 수 없습니다.')
        if root['deleted'] or root['kind'] == 'system':
            raise ProtocolError('이 메시지에는 스레드를 달 수 없습니다.')
        return root

    def _h_mention_list(self, ctx: Ctx, msg: dict) -> None:
        """멘션 모아보기 (§10.5)"""
        limit = _opt_int(msg, 'limit')
        limit = 100 if limit is None else max(1, min(limit, 200))
        self._reply(ctx, {'type': 'mention_list', 'messages': self.store.mentions_of(ctx.username, limit)})

    def _h_thread_get(self, ctx: Ctx, msg: dict) -> None:
        root = self._require_message_member(_req_int(msg, 'root_id'), ctx.username)
        if root.get('thread_root') is not None:
            root = self._require_message_member(root['thread_root'], ctx.username)
        self._reply(ctx, {'type': 'thread', 'root_id': root['msg_id'], 'room_id': root['room_id'], 'root': root,
                          'messages': self.store.thread_replies(root['msg_id'], viewer=ctx.username)})

    @staticmethod
    def _validate_secret_content(content: str) -> None:
        """서버는 복호화하지 않고 형식(base64, 최소 길이)만 확인한다."""
        if not content or len(content) > MAX_SECRET_CONTENT:
            raise ProtocolError('비밀채팅 메시지 형식이 올바르지 않습니다.')
        try:
            raw = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            raise ProtocolError('비밀채팅 메시지 형식이 올바르지 않습니다.') from None
        if len(raw) < SECRET_MIN_BYTES:
            raise ProtocolError('비밀채팅 메시지 형식이 올바르지 않습니다.')

    def _deliver_message(self, room_id: str, sender: str, kind: str, content: str,
                         reply_to_id: Optional[int], mentions: list[str], attachment_id: Optional[int],
                         client_tmp_id: Any, ctx: Optional[Ctx], silent: bool = False,
                         thread_root_id: Optional[int] = None) -> dict:
        restored = self.store.restore_direct_members(room_id)
        if restored:
            self._push_room_updated(room_id, restored)
        message = self.store.add_message(room_id, sender, kind, content, reply_to_id, mentions, attachment_id,
                                         silent=silent, thread_root=thread_root_id)
        self._publish_new_message(message, ctx, client_tmp_id)
        if thread_root_id is not None:   # 원글의 답글 수·마지막 답글 시각 갱신 (§10.4)
            root = self.store.get_message(thread_root_id)
            if root is not None:
                self._push_msg_updated(root)
        self._after_user_message(message)
        return message

    def _h_read(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        last = _req_int(msg, 'last_msg_id')
        value = self.store.update_read(meta['room_id'], ctx.username, last)
        payload = {'type': 'read_update', 'room_id': meta['room_id'], 'username': ctx.username,
                   'last_read_msg_id': value}
        self._push([m for m in meta['members'] if m != ctx.username], payload)
        # 읽은 본인의 연결(다른 기기 포함)에는 그 방의 남은 안 읽은 수도 함께 (§4)
        self._push([ctx.username], {**payload, 'unread_count': self.store.unread_count(meta['room_id'], ctx.username)},
                   ctx)

    def _h_react(self, ctx: Ctx, msg: dict) -> None:
        message = self._require_message_member(_req_int(msg, 'msg_id'), ctx.username)
        emoji = _req_str(msg, 'emoji', 16, label='이모지')
        if message['deleted'] or message['kind'] == 'system':
            raise ProtocolError('이 메시지에는 공감할 수 없습니다.')
        current = message.get('reactions') or {}
        if emoji not in current and len(current) >= MAX_REACTION_KINDS:
            raise ProtocolError(f'공감은 메시지 하나에 {MAX_REACTION_KINDS}종류까지 달 수 있습니다. 이미 달린 공감을 누르면 함께 공감할 수 있습니다.')
        updated = self.store.toggle_reaction(message['msg_id'], ctx.username, emoji)
        self._push_msg_updated(updated, ctx)

    def _h_delete(self, ctx: Ctx, msg: dict) -> None:
        message = self._require_message_member(_req_int(msg, 'msg_id'), ctx.username)
        if message['sender'] != ctx.username:
            raise ProtocolError('본인이 보낸 메시지만 삭제할 수 있습니다.')
        if message['deleted']:
            raise ProtocolError('이미 삭제된 메시지입니다.')
        sent = datetime.strptime(message['timestamp'], ISO_FMT)
        if datetime.now() - sent > DELETE_WINDOW:
            raise ProtocolError('보낸 지 24시간이 지난 메시지는 모두에게서 삭제할 수 없습니다. 나에게서 삭제는 할 수 있습니다.')
        updated = self.store.delete_message(message['msg_id'])
        self._push_msg_updated(updated, ctx)
        for reply in self.store.refresh_reply_previews(updated['msg_id']):   # 답장에 남은 원문 미리보기도 지운다
            self._push_msg_updated(reply)
        if self.rag is not None and hasattr(self.rag, 'remove_message'):
            self._rag_queue.put(lambda: self.rag.remove_message(updated['msg_id']))

    def _h_edit(self, ctx: Ctx, msg: dict) -> None:
        """메시지 수정 (카카오톡 2025-09 개편) - 본인 텍스트 메시지, 24시간 이내"""
        message = self._require_message_member(_req_int(msg, 'msg_id'), ctx.username)
        if message['sender'] != ctx.username:
            raise ProtocolError('본인이 보낸 메시지만 수정할 수 있습니다.')
        if message['deleted']:
            raise ProtocolError('삭제된 메시지는 수정할 수 없습니다.')
        if message['kind'] != 'text':
            raise ProtocolError('텍스트 메시지만 수정할 수 있습니다.')
        content = msg.get('content')
        if not isinstance(content, str) or not content.strip():
            raise ProtocolError('수정할 내용을 입력하세요.')
        if not _utf8_ok(content):
            raise ProtocolError(BAD_TEXT)
        if len(content) > MAX_CONTENT_LEN:
            raise ProtocolError(f'메시지는 {MAX_CONTENT_LEN}자를 넘을 수 없습니다.')
        sent = datetime.strptime(message['timestamp'], ISO_FMT)
        if datetime.now() - sent > DELETE_WINDOW:
            raise ProtocolError('보낸 지 24시간이 지난 메시지는 수정할 수 없습니다.')
        if content == message['content']:
            self._reply(ctx, {'type': 'msg_updated', 'message': message})
            return
        updated = self.store.edit_message(message['msg_id'], content)
        if updated is None:
            raise ProtocolError('삭제된 메시지는 수정할 수 없습니다.')
        self._push_msg_updated(updated, ctx)
        for reply in self.store.refresh_reply_previews(updated['msg_id']):
            self._push_msg_updated(reply)
        if self.rag is not None and hasattr(self.rag, 'remove_message'):
            self._rag_queue.put(lambda: (self.rag.remove_message(updated['msg_id']),
                                         self.rag.index_message(updated)))
        self._queue_link_preview(updated)

    def _h_notice_set(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        msg_id = _opt_int(msg, 'msg_id')
        if msg_id is not None:
            target = self.store.get_message(msg_id)
            if target is None or target['room_id'] != meta['room_id'] \
                    or not self.store.message_visible(msg_id, ctx.username):
                raise ProtocolError('공지로 등록할 메시지를 찾을 수 없습니다.')
            if target['deleted'] or target['kind'] == 'system':
                raise ProtocolError('이 메시지는 공지로 등록할 수 없습니다.')
        self.store.set_notice(meta['room_id'], msg_id)
        self._push_room_updated(meta['room_id'], meta['members'], ctx)
        if msg_id is not None:
            self._post_system(meta['room_id'], f'{self.store.display_name(ctx.username)}님이 공지를 등록했습니다.')

    def _h_search(self, ctx: Ctx, msg: dict) -> None:
        query = _req_str(msg, 'query', 100, label='검색어')
        self._limit_action(ctx.username, 'search')
        room_id = msg.get('room_id')
        if room_id is not None:
            self._require_room_member(room_id, ctx.username)
        messages = self.store.search(ctx.username, query, room_id)
        self._reply(ctx, {'type': 'search_result', 'query': query, 'room_id': room_id, 'messages': messages})

    # ------------------------------------------------------------------ 투표
    def _h_poll_create(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        self._require_not_secret(meta)
        question = _req_str(msg, 'question', 200, label='질문')
        options = msg.get('options')
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise ProtocolError('options 값이 올바르지 않습니다.')
        options = [o.strip() for o in options]
        if any(not o for o in options):
            raise ProtocolError('빈 항목은 사용할 수 없습니다.')
        if len(options) < 2:
            raise ProtocolError('투표 항목은 2개 이상이어야 합니다.')
        if len(options) > 20 or any(len(o) > 100 for o in options):
            raise ProtocolError('투표 항목이 너무 많거나 깁니다.')
        multiple = _bool(msg, 'multiple')
        message = self.store.create_poll(meta['room_id'], ctx.username, question, options, multiple)
        self._publish_new_message(message, ctx)

    def _require_poll(self, poll_id: int, username: str) -> dict:
        poll = self.store.poll_meta(poll_id)
        if poll is None:
            raise ProtocolError('존재하지 않는 투표입니다.')
        if not self.store.is_member(poll['room_id'], username):
            raise ProtocolError('이 채팅방의 대화상대가 아닙니다. 초대를 받은 뒤 이용할 수 있습니다.')
        poll_msg = self.store.poll_message_id(poll_id)
        if poll_msg is not None and not self.store.message_visible(poll_msg, username):   # 들어오기 전의 투표
            raise ProtocolError('존재하지 않는 투표입니다.')
        return poll

    def _h_poll_vote(self, ctx: Ctx, msg: dict) -> None:
        poll = self._require_poll(_req_int(msg, 'poll_id'), ctx.username)
        if poll['closed']:
            raise ProtocolError('마감된 투표입니다.')
        indexes = msg.get('option_indexes')
        if not isinstance(indexes, list) or not all(isinstance(i, int) and not isinstance(i, bool)
                                                    for i in indexes):
            raise ProtocolError('option_indexes 값이 올바르지 않습니다.')
        indexes = sorted(set(indexes))
        if any(i < 0 or i >= poll['option_count'] for i in indexes):
            raise ProtocolError('존재하지 않는 투표 항목입니다.')
        if not poll['multiple'] and len(indexes) > 1:
            raise ProtocolError('하나의 항목만 선택할 수 있습니다.')
        updated = self.store.vote(poll['poll_id'], ctx.username, indexes)
        self._push_msg_updated(updated, ctx)

    def _h_poll_close(self, ctx: Ctx, msg: dict) -> None:
        poll = self._require_poll(_req_int(msg, 'poll_id'), ctx.username)
        if poll['creator'] != ctx.username:
            raise ProtocolError('투표를 만든 사람만 마감할 수 있습니다.')
        if poll['closed']:
            raise ProtocolError('이미 마감된 투표입니다.')
        updated = self.store.close_poll(poll['poll_id'])
        self._push_msg_updated(updated, ctx)

    # ------------------------------------------------------------------ 일정
    @staticmethod
    def _related(event: dict) -> set[str]:
        return {event['creator'], *event['attendees'].keys()}

    def _event_fields(self, msg: dict, partial: bool) -> dict:
        fields: dict = {}
        if not partial or 'title' in msg:
            fields['title'] = _req_str(msg, 'title', 100, label='일정 제목')
        if not partial or 'start' in msg:
            fields['start'] = _norm_iso(msg.get('start'), 'start')
        if not partial or 'end' in msg:
            fields['end'] = _norm_iso(msg.get('end'), 'end')
        if not partial or 'all_day' in msg:
            fields['all_day'] = _bool(msg, 'all_day')
        if not partial or 'description' in msg:
            fields['description'] = _opt_str(msg, 'description', 2000)
        if not partial or 'location' in msg:
            fields['location'] = _opt_str(msg, 'location', 200)
        if not partial or 'remind_minutes' in msg:
            remind = _opt_int(msg, 'remind_minutes')
            if remind is not None and not 0 <= remind <= 60 * 24 * 30:
                raise ProtocolError('알림 시간이 올바르지 않습니다.')
            fields['remind_minutes'] = remind
        if not partial or 'color' in msg:
            color = msg.get('color')
            if color is None:
                color = DEFAULT_EVENT_COLOR
            if not isinstance(color, str) or not COLOR_RE.match(color):
                raise ProtocolError('색상은 #RRGGBB 형식이어야 합니다.')
            fields['color'] = color.upper()
        return fields

    def _h_event_create(self, ctx: Ctx, msg: dict) -> None:
        fields = self._event_fields(msg, partial=False)
        if fields['end'] < fields['start']:
            raise ProtocolError('종료 시각이 시작 시각보다 빠릅니다.')
        room_id = msg.get('room_id')
        if room_id is not None:
            meta = self._require_room_member(room_id, ctx.username)
            self._require_not_secret(meta)
            attendees = meta['members']
        else:
            attendees = _str_list(msg, 'attendees', required=False)
            self._require_users_exist(attendees)
        event, message = self.store.create_event(ctx.username, fields, attendees, room_id)
        if message is not None:
            self._publish_new_message(message)
        self._push(self._related(event), {'type': 'event_updated', 'event': event}, ctx)

    def _require_event(self, event_id: int) -> dict:
        event = self.store.get_event(event_id)
        if event is None:
            raise ProtocolError('존재하지 않는 일정입니다.')
        return event

    def _h_event_update(self, ctx: Ctx, msg: dict) -> None:
        event = self._require_event(_req_int(msg, 'event_id'))
        if event['creator'] != ctx.username:
            raise ProtocolError('일정을 만든 사람만 수정할 수 있습니다.')
        fields = self._event_fields(msg, partial=True)
        start = fields.get('start', event['start'])
        end = fields.get('end', event['end'])
        if end < start:
            raise ProtocolError('종료 시각이 시작 시각보다 빠릅니다.')
        updated = self.store.update_event(event['event_id'], fields)
        self._push(self._related(updated), {'type': 'event_updated', 'event': updated}, ctx)

    def _h_event_delete(self, ctx: Ctx, msg: dict) -> None:
        event = self._require_event(_req_int(msg, 'event_id'))
        if event['creator'] != ctx.username:
            raise ProtocolError('일정을 만든 사람만 삭제할 수 있습니다.')
        self.store.delete_event(event['event_id'])
        self._push(self._related(event), {'type': 'event_deleted', 'event_id': event['event_id']}, ctx)

    def _h_event_rsvp(self, ctx: Ctx, msg: dict) -> None:
        event = self._require_event(_req_int(msg, 'event_id'))
        response = msg.get('response')
        if response not in RSVP_RESPONSES:
            raise ProtocolError('응답은 yes/no/maybe 중 하나여야 합니다.')
        if ctx.username not in event['attendees']:
            raise ProtocolError('일정 참석자가 아닙니다.')
        updated = self.store.rsvp(event['event_id'], ctx.username, response)
        self._push(self._related(updated), {'type': 'event_updated', 'event': updated}, ctx)

    def _h_event_list(self, ctx: Ctx, msg: dict) -> None:
        start = _norm_iso(msg.get('start'), 'start')
        end = _norm_iso(msg.get('end'), 'end')
        events = self.store.list_events(ctx.username, start, end)
        self._reply(ctx, {'type': 'event_list', 'start': msg.get('start'), 'end': msg.get('end'),
                          'events': events})

    # ------------------------------------------------------------------ 할 일
    def _require_todo(self, todo_id: int, username: str) -> dict:
        todo = self.store.get_todo(todo_id)
        if todo is None:
            raise ProtocolError('존재하지 않는 할 일입니다.')
        if todo['owner'] != username:
            raise ProtocolError('본인의 할 일만 변경할 수 있습니다.')
        return todo

    def _h_todo_create(self, ctx: Ctx, msg: dict) -> None:
        title = _req_str(msg, 'title', 200, label='할 일')
        todo = self.store.create_todo(ctx.username, title, _validate_due(msg.get('due')))
        self._push([ctx.username], {'type': 'todo_updated', 'todo': todo}, ctx)

    def _h_todo_update(self, ctx: Ctx, msg: dict) -> None:
        todo = self._require_todo(_req_int(msg, 'todo_id'), ctx.username)
        fields: dict = {}
        if 'title' in msg:
            fields['title'] = _req_str(msg, 'title', 200, label='할 일')
        if 'due' in msg:
            fields['due'] = _validate_due(msg.get('due'))
        if 'done' in msg:
            fields['done'] = _bool(msg, 'done')
        updated = self.store.update_todo(todo['todo_id'], fields)
        self._push([ctx.username], {'type': 'todo_updated', 'todo': updated}, ctx)

    def _h_todo_delete(self, ctx: Ctx, msg: dict) -> None:
        todo = self._require_todo(_req_int(msg, 'todo_id'), ctx.username)
        self.store.delete_todo(todo['todo_id'])
        self._push([ctx.username], {'type': 'todo_deleted', 'todo_id': todo['todo_id']}, ctx)

    def _h_todo_list(self, ctx: Ctx, msg: dict) -> None:
        self._reply(ctx, {'type': 'todo_list', 'todos': self.store.list_todos(ctx.username)})


    def _h_login_token_revoke(self, ctx: Ctx, msg: dict) -> None:
        token = msg.get('token')
        if isinstance(token, str) and token:
            self.store.revoke_login_token(ctx.username, token)
        self._reply(ctx, {'type': 'login_token_revoked'})

    # ------------------------------------------------------------------ 프로필 / 첨부
    def _h_profile_update(self, ctx: Ctx, msg: dict) -> None:
        fields: dict = {}
        refused: list[str] = []            # 바꿀 수 없어 그대로 둔 항목의 안내 (다른 항목은 저장한다)
        if msg.get('display_name') is not None:
            display_name = _req_name(msg, 'display_name', 20, label='이름')
            if self.open_registration:
                fields['display_name'] = display_name
            elif display_name != ctx.username:  # 닫힌 서버: 표시 이름은 조직도 이름으로 고정 (§4.5)
                refused.append(NAME_FIXED)
        if msg.get('status_message') is not None:
            fields['status_message'] = _req_str(msg, 'status_message', 60, allow_empty=True,
                                                label='상태메시지')
        if 'birthday' in msg:
            fields['birthday'] = _validate_birthday(msg.get('birthday'))
        if _bool(msg, 'avatar_clear'):   # '기본 이미지' = 조직도 사진 (없으면 사진 없음)
            fields['avatar_id'] = self.store.org_avatar(ctx.username)
        elif msg.get('avatar_b64') is not None:
            data = _decode_b64(msg.get('avatar_b64'), MAX_AVATAR_BYTES, '프로필 사진은 512KB를 초과할 수 없습니다.')
            sniffed = _sniff_image(data)
            if sniffed is None:
                raise ProtocolError('프로필 사진은 이미지 파일(PNG/JPEG/GIF/BMP/WEBP)이어야 합니다.')
            mime, ext = sniffed
            fields['avatar_id'] = self.store.save_attachment(f'avatar.{ext}', mime, data, ctx.username)
        if 'mobile' in msg:
            mobile = _opt_str(msg, 'mobile', 40).strip()
            if mobile and not (9 <= len(Store.phone_digits(mobile)) <= 13):
                raise ProtocolError('전화번호 형식이 올바르지 않습니다.')
            fields['mobile'] = mobile[:20]
        if 'email' in msg:
            email = _opt_str(msg, 'email', 120).strip()
            if email and not EMAIL_RE.fullmatch(email):
                raise ProtocolError('이메일 형식이 올바르지 않습니다.')
            if email != ((self.store.get_user(ctx.username) or {}).get('email') or ''):
                if self.open_registration:
                    fields['email'] = email[:120]
                else:                      # 등록이 닫힌 서버: 이메일(로그인 아이디)은 관리자가 고친다 (§4.1)
                    refused.append(EMAIL_ADMIN_ONLY)
        if 'deputies' in msg:
            fields['deputies'] = self._validate_deputies(ctx.username, msg.get('deputies'))
        if not fields:
            if not refused and msg.get('display_name') is not None:     # 지금 이름 그대로 보낸 것뿐 (바뀌는 것 없음)
                self._broadcast_user(ctx.username, ctx)
                return
            raise ProtocolError(refused[0] if refused else '변경할 항목이 없습니다.')
        try:
            self.store.update_profile(ctx.username, fields)
        except ValueError as exc:          # 다른 사람이 쓰는 이메일
            raise ProtocolError(str(exc)) from None
        self._broadcast_user(ctx.username, ctx)
        for message in refused:            # 다른 항목은 저장하고, 바꿀 수 없는 항목만 그대로 둔다
            self._send_error(ctx.conn, ctx.req_type, message, msg)

    def _validate_deputies(self, me: str, value) -> list:
        """대직자 목록 (§8.13): username 문자열 목록, 중복 제거 · 순서 유지 · 최대 MAX_DEPUTIES"""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ProtocolError('대직자는 사용자 이름 목록이어야 합니다.')
        out = []
        for name in value:
            name = name.strip()
            if name and name not in out:
                out.append(name)
        if len(out) > MAX_DEPUTIES:
            raise ProtocolError(f'대직자는 {MAX_DEPUTIES}명까지 지정할 수 있습니다.')
        for name in out:
            if name == me or not self.store.can_be_deputy(name):
                raise ProtocolError(f'대직자로 지정할 수 없는 사용자입니다: {name[:20]}')
        return out

    # ------------------------------------------------------------------ 친구 등록
    def _h_friend_add(self, ctx: Ctx, msg: dict) -> None:
        """전화번호 또는 이메일로 친구 추가 (정확히 일치할 때만 - 목록 조회 방지).
        username 을 주면 그 사람을 바로 (프로필 창의 [친구 추가] - 이미 보고 있는 동료라 연락처가 필요 없다)"""
        if msg.get('username') is not None:
            target = _req_str(msg, 'username', 120, label='사용자')
            if not self.store.user_exists(target) or self.store.is_removed(target) or self.store.is_bot(target):
                raise ProtocolError('사용자를 찾을 수 없습니다.')
        else:
            query = _req_str(msg, 'query', 120, label='전화번호 또는 이메일')
            if '@' in query and not EMAIL_RE.fullmatch(query.strip()):
                raise ProtocolError('이메일 형식이 올바르지 않습니다.')
            if '@' not in query and len(Store.phone_digits(query)) < 9:
                raise ProtocolError('전화번호를 정확히 입력해 주세요.')
            target = self.store.find_user_by_contact(query)
            if target is None:
                self._limit_action(ctx.username, 'friend_lookup')      # 못 찾은 조회만 센다
                raise ProtocolError('해당 전화번호·이메일로 가입한 사용자가 없습니다. 상대가 프로필에 등록한 번호·이메일인지 확인하세요.')
        if target == ctx.username:
            raise ProtocolError('나 자신은 친구로 추가할 수 없습니다.')
        added = self.store.add_friend(ctx.username, target)
        base = self.store.get_user(target, self._online_users())
        user = self._apply_profile(base, self.store.profile_overrides_for_viewer(ctx.username).get(target))
        user = self._private_view(user, ctx.username, self.store.admin_scope(ctx.username) is not None)
        self._reply(ctx, {'type': 'friend_added', 'user': user, 'already': not added})
        self._reply(ctx, {'type': 'friend_list', 'friends': self.store.friends_of(ctx.username)})

    def _h_friend_remove(self, ctx: Ctx, msg: dict) -> None:
        self.store.remove_friend(ctx.username, _req_str(msg, 'username', 64, label='친구'))
        self._reply(ctx, {'type': 'friend_list', 'friends': self.store.friends_of(ctx.username)})

    def _h_friend_list(self, ctx: Ctx, msg: dict) -> None:
        self._reply(ctx, {'type': 'friend_list', 'friends': self.store.friends_of(ctx.username)})

    def _h_presence_set(self, ctx: Ctx, msg: dict) -> None:
        presence = msg.get('presence')
        if presence not in PRESENCES:
            raise ProtocolError('상태는 online/away/busy/meeting/vacation 중 하나여야 합니다.')
        self.store.update_profile(ctx.username, {'presence': presence})
        self._broadcast_user(ctx.username, ctx)

    def _h_attachment_get(self, ctx: Ctx, msg: dict) -> None:
        attachment_id = _req_int(msg, 'attachment_id')
        att = self.store.get_attachment_for(attachment_id, ctx.username)
        if att is None and self.store.admin_scope(ctx.username) == '' \
                and self.store.file_request_for_attachment(attachment_id) is not None:
            att = self.store.get_attachment(attachment_id)        # 그룹 관리자: 승인 요청 파일을 받아 확인 (§10.10)
            if att is not None and not att['data']:
                att['expired'] = True
        if att is None:
            raise ProtocolError('첨부 파일을 찾을 수 없거나 접근 권한이 없습니다.')
        if att.get('expired'):
            raise ProtocolError(ATTACHMENT_EXPIRED)
        self._reply(ctx, {'type': 'attachment_data', 'attachment_id': att['attachment_id'],
                          'name': att['name'], 'mime': att['mime'],
                          'data_b64': base64.b64encode(att['data']).decode('ascii')})

    # ------------------------------------------------------------------ 스케줄러
    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                self._run_scheduled()
                self._run_reminders()
                self._run_voiceroom_timeouts()
                self._run_story_expiry()
                self._run_retention()
                self._run_orphan_sweep()
            except Exception:
                if self._stop.is_set():
                    break
                logger.exception('스케줄러 오류')

    def _run_orphan_sweep(self) -> None:
        """어디에서도 쓰지 않는 하루 넘은 첨부(바꾼 프로필 사진 · 보내지 못한 파일)의 데이터를 지운다"""
        now = time.monotonic()
        if now - getattr(self, '_orphan_swept_at', -1e18) < self.ORPHAN_SWEEP_SECONDS:
            return
        self._orphan_swept_at = now
        purged = self.store.purge_orphan_attachments()
        if purged:
            logger.info('쓰지 않는 첨부 %d개의 데이터를 지웠습니다', purged)

    def _run_scheduled(self) -> None:
        # 꺼낼 때 delivered 로 표시한다(두 번 보내지 않게). 대신 못 보낸 건은 조용히 사라지지 않게 보낸 사람에게 알린다
        for item in self.store.pop_due_scheduled(now_iso()):
            try:
                self._deliver_scheduled(item)
            except Exception:   # 한 건이 실패해도 같은 시각의 나머지 예약 메시지는 보낸다
                logger.exception('예약 메시지 %s 전송 실패', item.get('scheduled_id'))
                self._scheduled_failed(item, '서버 오류로 예약 메시지를 보내지 못했습니다. 다시 보내 주세요.')

    def _scheduled_failed(self, item: dict, reason: str) -> None:
        """예약 메시지를 못 보냄: 보낸 사람에게 scheduled_failed, 보관하던 첨부는 지운다"""
        p = item.get('payload') or {}
        if p.get('attachment_id'):
            try:
                self.store.discard_attachment(p['attachment_id'])
            except Exception:
                logger.exception('예약 메시지 %s 첨부 정리 실패', item.get('scheduled_id'))
        self._push([item['sender']], {'type': 'scheduled_failed', 'scheduled_id': item['scheduled_id'],
                                      'room_id': item['room_id'], 'kind': p.get('kind', 'text'),
                                      'content': p.get('content', ''), 'reason': reason})

    def _deliver_scheduled(self, item: dict) -> None:
        room_id, sender, p = item['room_id'], item['sender'], item['payload']
        if not self.store.is_member(room_id, sender):
            logger.info('예약 메시지 %s 취소: 발신자가 방을 나감', item['scheduled_id'])
            self._scheduled_failed(item, '채팅방을 나가서 예약 메시지가 취소되었습니다.')
            return
        reply_to_id = p.get('reply_to_id')
        if reply_to_id is not None:
            orig = self.store.get_message(reply_to_id)
            if orig is None or orig['room_id'] != room_id:
                reply_to_id = None
        members = set(self.store.members(room_id))
        mentions = [m for m in p.get('mentions', []) if m in members]
        self._deliver_message(room_id, sender, p['kind'], p['content'], reply_to_id, mentions,
                              p.get('attachment_id'), p.get('client_tmp_id'), None,
                              silent=bool(p.get('silent')))

    def _run_reminders(self) -> None:
        for event, minutes in self.store.due_reminders(datetime.now(), REMINDER_GRACE):
            targets = {u for u, r in event['attendees'].items() if r != 'no'}
            targets.add(event['creator'])
            self._push(targets, {'type': 'event_reminder', 'event': event, 'minutes_before': minutes})

    # ------------------------------------------------------------------ URL 미리보기
    def _queue_link_preview(self, message: dict) -> None:
        if not self.link_preview or message.get('kind') != 'text' or message.get('deleted'):
            return
        match = URL_RE.search(message.get('content') or '')
        if match:
            self._preview_queue.put({'msg_id': message['msg_id'], 'room_id': message['room_id'],
                                     'url': match.group(0)})

    def _preview_worker(self) -> None:
        while True:
            job = self._preview_queue.get()
            if job is None or self._stop.is_set():
                break
            try:
                preview = self.preview_fetcher(job['url'])
            except Exception:
                preview = None
            if not preview or self._stop.is_set():
                continue
            try:       # 한 건이 실패해도(저장소 오류 등) 미리보기 작업 스레드는 계속 돈다
                current = self.store.get_message(job['msg_id'])
                if current is None or current['deleted'] or job['url'] not in (current['content'] or ''):
                    continue  # 그 사이 수정/삭제됨
                updated = self.store.set_link_preview(job['msg_id'], preview)
                if updated is not None:
                    self._push_msg_updated(updated)
            except Exception:
                if self._stop.is_set():
                    break
                logger.exception('링크 미리보기 저장 오류 (msg %s)', job.get('msg_id'))

    # ------------------------------------------------------------------ 오늘의 브리핑 (선톡)
    def _send_briefing(self, username: str) -> None:
        """AI 비서가 그날 첫 접속한 사용자에게 먼저 보내는 요약 (카카오톡 '선톡 브리핑' 대응)"""
        if self._stop.is_set():
            return
        room_id, _members = self.store.get_or_create_direct(username, BOT_USERNAME)
        self._push_room_updated(room_id, [username])
        today = date.today()
        start = datetime(today.year, today.month, today.day)
        events = self.store.list_events(username, start.strftime(ISO_FMT),
                                        (start + timedelta(days=1)).strftime(ISO_FMT))
        pending = [e for e in self.store.list_events(username, start.strftime(ISO_FMT),
                                                     (start + timedelta(days=14)).strftime(ISO_FMT))
                   if e['attendees'].get(username) == 'pending']
        todos = [t for t in self.store.list_todos(username)
                 if not t['done'] and t.get('due') and t['due'] <= today.isoformat()]
        unread = self.store.unread_total(username)
        name = self.store.display_name(username)
        lines = [f'☀️ {name}님, 오늘의 브리핑이에요 ({today.month}월 {today.day}일)']
        if events:
            lines.append(f'📅 오늘 일정 {len(events)}개')
            for e in events[:5]:
                when = '종일' if e.get('all_day') else e['start'][11:16]
                lines.append(f"  · {when} {e['title']}")
        else:
            lines.append('📅 오늘 일정이 없어요')
        if pending:
            lines.append(f"🙋 참석 여부를 알려 주세요: {', '.join(e['title'] for e in pending[:3])}")
        if todos:
            lines.append(f"✅ 마감된 할 일 {len(todos)}개: {', '.join(t['title'] for t in todos[:3])}")
        if unread:
            lines.append(f'💬 안 읽은 메시지 {unread}개')
        lines.append('궁금한 건 언제든 저에게 물어보세요!')
        self._bot_reply(room_id, '\n'.join(lines))

    # ------------------------------------------------------------------ RAG (AI 비서)
    def _rag_worker(self) -> None:
        """금방 끝나는 RAG 작업(색인 · 색인 지우기 · 브리핑 · 안내)을 순서대로 처리한다.
        오래 걸리는 일(답변 · 문서 학습)은 _rag_slow_worker 들이 맡아, 색인이 느린 답변에 밀리지 않는다"""
        while True:
            job = self._rag_queue.get()
            if job is None or self._stop.is_set():
                break
            try:
                job()
            except Exception:
                logger.exception('RAG 작업 오류')

    def _rag_slow_worker(self) -> None:
        """오래 걸리는 일을 key 하나씩 집어 처리한다: 같은 key 의 일은 한 번에 하나씩 차례로, 다른 key 는 동시에"""
        while True:
            key = self._rag_ready.get()
            if key is None or self._stop.is_set():
                break
            with self._rag_slow_lock:
                job = self._rag_pending[key][0]
            try:
                job()
            except Exception:
                logger.exception('RAG 작업 오류')
            with self._rag_slow_lock:
                pending = self._rag_pending[key]
                pending.popleft()
                self._rag_slow_count -= 1
                if pending:
                    self._rag_ready.put(key)        # 같은 key 의 다음 일은 다른 key 들 뒤에 다시 줄 선다
                else:
                    del self._rag_pending[key]

    def _spawn_rag_workers(self) -> None:
        for index in range(max(1, int(self.RAG_WORKERS))):
            self._spawn(self._rag_slow_worker, name=f'kakao-rag-slow-{index}')

    def _rag_slow_put(self, key: str, job: Callable[[], None]) -> bool:
        """오래 걸리는 일을 작업 스레드들에 맡긴다 → 맡겼으면 True (너무 밀려 있으면 False).
        같은 key(방 · 사용자)의 일은 맡긴 순서대로 하나씩 하고, 다른 key 는 서로 기다리지 않는다"""
        with self._rag_slow_lock:
            if self._rag_slow_count >= self.RAG_BACKLOG:
                return False
            self._rag_slow_count += 1
            pending = self._rag_pending.get(key)
            if pending is None:
                self._rag_pending[key] = collections.deque([job])
                self._rag_ready.put(key)
            else:
                pending.append(job)
        return True

    def _bot_reply(self, room_id: str, text: str) -> None:
        if self._stop.is_set() or not self.store.is_member(room_id, BOT_USERNAME):
            return
        message = self.store.add_message(room_id, BOT_USERNAME, 'text', text[:MAX_CONTENT_LEN])
        self._publish_new_message(message)

    def _after_user_message(self, message: dict) -> None:
        """사용자 메시지 저장 후: 색인 + 봇 트리거 판정 (실제 작업은 RAG 워커에서)."""
        sender = message['sender']
        if not sender or sender == BOT_USERNAME or message['kind'] == 'system':
            return
        room_id = message['room_id']
        meta = self.store.room_meta(room_id)
        if meta is None:
            return
        bot_in_room = BOT_USERNAME in meta['members']
        is_bot_direct = bot_in_room and meta['type'] == 'direct'

        if message['kind'] == 'text':
            self._queue_link_preview(message)
            if self.rag is not None:
                self._rag_queue.put(lambda: self.rag.index_message(message))
            mentioned = BOT_USERNAME in message['mentions'] or BOT_MENTION in message['content']
            if is_bot_direct or (bot_in_room and meta['type'] == 'group' and mentioned):
                allowed, _wait = self._actions.hit(sender, 'ai_question', *self.ACTION_LIMITS['ai_question'])
                if allowed:
                    allowed = self._rag_slow_put(room_id, lambda: self._rag_answer(message))
                if not allowed and self._actions.hit(sender, 'ai_busy_notice', 1,
                                                     self.ACTION_LIMITS['ai_question'][1])[0]:
                    self._rag_queue.put(lambda: self._bot_reply(room_id, AI_BUSY))   # 안내는 한 번만
        elif message['kind'] in ('file', 'image') and is_bot_direct and message['attachment']:
            if not self._rag_slow_put(room_id, lambda: self._rag_learn(message)):
                self._rag_queue.put(lambda: self._bot_reply(room_id, AI_BUSY))

    def _rag_answer(self, message: dict) -> None:
        room_id, asker = message['room_id'], message['sender']
        if self.rag is None:
            self._bot_reply(room_id, RAG_UNAVAILABLE)
            return
        question = message['content'].replace(BOT_MENTION, '').strip() or message['content']
        history, _ = self.store.history(room_id, None, RAG_HISTORY_LIMIT)
        try:
            room_ids: Any = self.store.room_since_for_user(asker)   # {방: 보기 시작하는 msg_id} (들어오기 전 대화 제외)
            meta = self.store.room_meta(room_id) or {}
            if meta.get('type') != 'direct':
                # 여럿이 보는 방: 그 방의 대화만 근거로 삼고(질문자의 다른 방 내용이 방 사람들에게 보이지 않게),
                # 앞 대화도 질문자와 AI 비서가 한 말만 넘긴다 (§7.6)
                room_ids = {room_id: room_ids.get(room_id, 0)}
                history = [m for m in history if m.get('sender') in (asker, BOT_USERNAME)]
            if getattr(self.rag, 'accepts_context', False):     # 포털 연동: 방 · 이름을 함께 넘겨 대화 저장
                asker_row = self.store.get_user(asker) or {}
                answer = self.rag.answer(question, asker, room_ids, history, room_id=room_id,
                                         asker_name=self._name_of(asker), room_name=meta.get('name') or '',
                                         bot_name=BOT_USERNAME, asker_email=asker_row.get('email') or '')
            else:
                answer = self.rag.answer(question, asker, room_ids, history)
        except Exception:
            logger.exception('RAG 답변 생성 실패')
            answer = RAG_FAILED
        self._bot_reply(room_id, answer if isinstance(answer, str) and answer.strip() else RAG_FAILED)

    def _rag_learn(self, message: dict) -> None:
        room_id = message['room_id']
        if self.rag is None:
            self._bot_reply(room_id, RAG_UNAVAILABLE)
            return
        att = self.store.get_attachment(message['attachment']['attachment_id'])
        if att is None:
            return
        try:
            doc = self.rag.add_document(message['sender'], att['name'], att['data'], att['mime'])
        except ValueError as exc:
            self._bot_reply(room_id, str(exc) or RAG_FAILED)
            return
        except Exception:
            logger.exception('RAG 문서 학습 실패')
            self._bot_reply(room_id, RAG_FAILED)
            return
        note = ' · 너무 길어 앞부분만' if doc.get('truncated') else ''
        self._bot_reply(room_id, f"📚 '{att['name']}' 문서를 학습했습니다 ({doc.get('chunks', 0)}개 조각{note})")

    def _require_rag(self) -> Any:
        if self.rag is None:
            raise ProtocolError(RAG_UNAVAILABLE)
        return self.rag

    def _h_rag_doc_list(self, ctx: Ctx, msg: dict) -> None:
        docs = self._require_rag().list_documents(ctx.username)
        self._reply(ctx, {'type': 'rag_doc_list', 'docs': docs})

    def _h_rag_doc_add(self, ctx: Ctx, msg: dict) -> None:
        rag = self._require_rag()
        self._limit_action(ctx.username, 'rag_doc_add')
        name = clean_file_name(_req_str(msg, 'name', 200, label='문서 이름'))
        data = _decode_b64(msg.get('data_b64'), MAX_ATTACHMENT_BYTES, '문서는 10MB를 초과할 수 없습니다.')
        mime = _opt_str(msg, 'mime', 100, 'application/octet-stream') or 'application/octet-stream'

        def work() -> None:        # 문서 해석은 오래 걸릴 수 있어 요청 처리 밖에서 한다 - 끝나면 응답을 보낸다 (§4.5)
            try:
                doc = rag.add_document(ctx.username, name, data, mime)
            except ValueError as exc:
                self._send_error(ctx.conn, ctx.req_type, str(exc) or '문서를 추가할 수 없습니다.', msg)
                return
            except Exception:
                logger.exception('RAG 문서 추가 실패')
                self._send_error(ctx.conn, ctx.req_type, SERVER_ERROR, msg, 'server_error')
                return
            self._reply(ctx, {'type': 'rag_doc_added', 'doc': doc})

        if not self._rag_slow_put(ctx.username, work):
            raise ProtocolError(RATE_LIMITED, code='rate_limited', retry_after=10)

    def _h_rag_doc_delete(self, ctx: Ctx, msg: dict) -> None:
        rag = self._require_rag()
        doc_id = _req_int(msg, 'doc_id')
        if not rag.delete_document(doc_id, ctx.username):
            raise ProtocolError('문서를 찾을 수 없거나 삭제 권한이 없습니다.')
        self._reply(ctx, {'type': 'rag_doc_deleted', 'doc_id': doc_id})

    # ================================================================== v3 (명세 §8)
    def _member_label(self, meta: dict, username: str) -> str:
        """시스템 메시지용 이름 - 오픈채팅은 닉네임, 그 외는 표시 이름."""
        if meta['type'] == 'open':
            nickname = self.store.member_nickname(meta['room_id'], username)
            if nickname:
                return nickname
        return self.store.display_name(username)

    # ------------------------------------------------------------------ 8.1 비밀채팅
    def _h_key_publish(self, ctx: Ctx, msg: dict) -> None:
        key_b64 = msg.get('public_key_b64')
        if not isinstance(key_b64, str):
            raise ProtocolError('공개키 형식이 올바르지 않습니다.')
        try:
            raw = base64.b64decode(key_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ProtocolError('공개키 형식이 올바르지 않습니다.') from None
        if len(raw) != 32:
            raise ProtocolError('X25519 공개키는 32바이트여야 합니다.')
        self.store.set_public_key(ctx.username, key_b64)
        self._broadcast_user(ctx.username, ctx)

    def _h_room_open_secret(self, ctx: Ctx, msg: dict) -> None:
        target = _req_str(msg, 'target', 20, label='대상')
        if target == ctx.username:
            raise ProtocolError('나와의 비밀채팅은 만들 수 없습니다.')
        self._require_users_exist([target])
        if not self.store.public_key(target):
            raise ProtocolError(SECRET_PEER_NO_KEY)
        room_id, _added = self.store.get_or_create_direct(ctx.username, target, secret=True)
        self._push_room_updated(room_id, {ctx.username, target}, ctx)

    # ------------------------------------------------------------------ 8.2 통화
    def _calls_for_user(self, username: str, room_id: Optional[str] = None) -> list[dict]:
        rooms = set(self.store.room_ids_for_user(username))
        if room_id is not None:
            rooms &= {room_id}
        with self._calls_lock:
            return [c.to_dict() for c in self._calls.values() if c.room_id in rooms]

    def _get_call(self, call_id: int) -> Call:
        """_calls_lock 을 잡은 상태에서 호출."""
        call = self._calls.get(call_id)
        if call is None:
            raise ProtocolError(CALL_GONE)
        return call

    def _push_call(self, snapshot: dict, ctx: Optional[Ctx] = None) -> None:
        self._push(self.store.members(snapshot['room_id']), {'type': 'call_updated', 'call': snapshot}, ctx)

    @_call_ordered
    def _h_call_start(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        mode = msg.get('mode')
        if mode not in CALL_MODES:
            raise ProtocolError('통화 종류는 voice/video/voiceroom 중 하나여야 합니다.')
        with self._calls_lock:
            if meta['room_id'] in self._room_calls:
                raise ProtocolError('이 채팅방에서 이미 통화 중입니다. 채팅방의 통화 카드에서 참여하기를 누르세요.')
            call = Call(call_id=self._next_call_id, room_id=meta['room_id'], mode=mode, host=ctx.username,
                        started_at=now_iso(), started_mono=time.monotonic())
            call.participants[ctx.username] = Call.new_participant('host', mode)
            call.joined.add(ctx.username)
            self._next_call_id += 1
            self._calls[call.call_id] = call
            self._room_calls[call.room_id] = call.call_id
            snapshot = call.to_dict()
        message = self.store.add_message(meta['room_id'], ctx.username, 'call', mode, call_id=snapshot['call_id'])
        self._publish_new_message(message)
        self._push_call(snapshot, ctx)

    @_call_ordered
    def _h_call_join(self, ctx: Ctx, msg: dict) -> None:
        call_id = _req_int(msg, 'call_id')
        with self._calls_lock:
            call = self._get_call(call_id)
            if not self.store.is_member(call.room_id, ctx.username):
                raise ProtocolError('이 채팅방의 대화상대가 아닙니다. 초대를 받은 뒤 이용할 수 있습니다.')
            if ctx.username not in call.participants:
                if call.mode == 'video' and len(call.participants) >= MAX_VIDEO_PARTICIPANTS:
                    raise ProtocolError('페이스톡은 최대 10명까지 참여할 수 있습니다. 누군가 나간 뒤 다시 참여하세요.')
                if call.mode == 'voiceroom' and len(call.participants) >= MAX_VOICEROOM_PARTICIPANTS:
                    raise ProtocolError('보이스룸 정원(1500명)이 가득 찼습니다.')
                role = 'listener' if call.mode == 'voiceroom' else 'speaker'
                call.participants[ctx.username] = Call.new_participant(role, call.mode)
            call.joined.add(ctx.username)
            snapshot = call.to_dict()
        self._push_call(snapshot, ctx)

    def _remove_participant(self, call: Call, username: str) -> bool:
        """_calls_lock 안에서 호출. 통화가 끝나야 하면 레지스트리에서 빼고 True."""
        call.participants.pop(username, None)
        if not call.participants or (call.mode == 'voiceroom' and not call.has_host()):
            self._calls.pop(call.call_id, None)
            self._room_calls.pop(call.room_id, None)
            return True
        return False

    @_call_ordered
    def _h_call_leave(self, ctx: Ctx, msg: dict) -> None:
        call_id = _req_int(msg, 'call_id')
        with self._calls_lock:
            call = self._get_call(call_id)
            if ctx.username not in call.participants:
                raise ProtocolError(CALL_NOT_PARTICIPANT)
            ended = self._remove_participant(call, ctx.username)
            snapshot = call.to_dict()
        if ended:
            self._finish_call(call, ctx)
        else:
            self._push_call(snapshot, ctx)

    @_call_ordered
    def _leave_all_calls(self, username: str, room_id: Optional[str] = None) -> None:
        """접속 종료/방 나가기 시 그 사용자가 참여 중인 통화에서 뺀다."""
        updated: list[dict] = []
        ended: list[Call] = []
        with self._calls_lock:
            for call in list(self._calls.values()):
                if username not in call.participants or (room_id is not None and call.room_id != room_id):
                    continue
                if self._remove_participant(call, username):
                    ended.append(call)
                else:
                    updated.append(call.to_dict())
        for snapshot in updated:
            self._push_call(snapshot)
        for call in ended:
            self._finish_call(call)

    def _finish_call(self, call: Call, ctx: Optional[Ctx] = None) -> None:
        """레지스트리에서 이미 빠진 통화의 종료 알림 + 시스템 메시지."""
        if self._stop.is_set():
            return
        duration = max(0, int(time.monotonic() - call.started_mono))
        self.store.log_call(call.call_id, call.room_id, call.mode, call.host, call.started_at, duration,
                            sorted(call.joined))
        self._push(self.store.members(call.room_id), {'type': 'call_ended', 'call_id': call.call_id,
                                                       'room_id': call.room_id, 'duration_seconds': duration}, ctx)
        if call.mode == 'voiceroom':
            text = '보이스룸이 종료되었습니다'
        else:
            text = f'통화 종료 · {duration // 60}:{duration % 60:02d}'
        self._post_system(call.room_id, text)

    @_call_ordered
    def _h_call_hand(self, ctx: Ctx, msg: dict) -> None:
        call_id = _req_int(msg, 'call_id')
        raised = _bool(msg, 'raised', default=True)
        with self._calls_lock:
            call = self._get_call(call_id)
            me = call.participants.get(ctx.username)
            if me is None:
                raise ProtocolError(CALL_NOT_PARTICIPANT)
            if call.mode != 'voiceroom' or me['role'] != 'listener':
                raise ProtocolError('보이스룸 청취자만 손을 들 수 있습니다.')
            me['hand'] = raised
            snapshot = call.to_dict()
        self._push_call(snapshot, ctx)

    @_call_ordered
    def _h_call_role(self, ctx: Ctx, msg: dict) -> None:
        call_id = _req_int(msg, 'call_id')
        target = _req_str(msg, 'username', 20, label='대상')
        role = msg.get('role')
        if role not in CALL_ROLES:
            raise ProtocolError('역할은 host/speaker/listener 중 하나여야 합니다.')
        with self._calls_lock:
            call = self._get_call(call_id)
            me = call.participants.get(ctx.username)
            if me is None or me['role'] != 'host':
                raise ProtocolError('호스트만 역할을 바꿀 수 있습니다.')
            person = call.participants.get(target)
            if person is None:
                raise ProtocolError(CALL_NOT_PARTICIPANT)
            old = person['role']
            if old == 'host' and role != 'host' \
                    and sum(1 for p in call.participants.values() if p['role'] == 'host') <= 1:
                raise ProtocolError('호스트가 최소 1명 있어야 합니다.')
            if call.mode == 'voiceroom' and old == 'listener' and role != 'listener' \
                    and call.stage_count() >= MAX_VOICEROOM_SPEAKERS:
                raise ProtocolError('호스트와 스피커는 최대 10명까지 가능합니다.')
            person['role'] = role
            if role != 'listener':
                person['hand'] = False
            snapshot = call.to_dict()
        self._push_call(snapshot, ctx)

    @_call_ordered
    def _h_call_state(self, ctx: Ctx, msg: dict) -> None:
        call_id = _req_int(msg, 'call_id')
        changes = {k: _bool(msg, k) for k in ('muted', 'video', 'screen') if msg.get(k) is not None}
        if not changes:
            raise ProtocolError('변경할 상태가 없습니다.')
        with self._calls_lock:
            call = self._get_call(call_id)
            me = call.participants.get(ctx.username)
            if me is None:
                raise ProtocolError(CALL_NOT_PARTICIPANT)
            me.update(changes)
            snapshot = call.to_dict()
        self._push_call(snapshot, ctx)

    def _h_call_media(self, ctx: Ctx, msg: dict) -> None:
        """미디어 프레임 중계 (저장 안 함). 참여자가 아니거나 보이스룸 청취자의 오디오는 조용히 버린다."""
        call_id = _req_int(msg, 'call_id')
        kind = msg.get('kind')
        if kind not in MEDIA_KINDS:
            raise ProtocolError('미디어 종류가 올바르지 않습니다.')
        seq = _req_int(msg, 'seq')
        data_b64 = msg.get('data_b64')
        if not isinstance(data_b64, str) or not data_b64 or len(data_b64) > MAX_MEDIA_B64:
            raise ProtocolError('미디어 데이터가 올바르지 않습니다.')
        codec = msg.get('codec')
        if kind == 'audio' and codec is not None and codec not in AUDIO_CODECS:
            raise ProtocolError('오디오 코덱은 pcm/zpcm 중 하나여야 합니다.')
        if kind == 'audio' and not self._audio_frame_ok(data_b64, codec or 'pcm'):
            raise ProtocolError('음성 조각이 너무 큽니다.')
        with self._calls_lock:
            call = self._calls.get(call_id)
            if call is None:
                return
            me = call.participants.get(ctx.username)
            if me is None:
                return
            if call.mode == 'voiceroom' and kind == 'audio' and me['role'] == 'listener':
                return
            targets = [u for u in call.participants if u != ctx.username]
        payload = {'type': 'call_media', 'call_id': call_id, 'from': ctx.username, 'kind': kind, 'seq': seq,
                   'data_b64': data_b64}
        if kind == 'audio':
            payload['codec'] = codec or 'pcm'
        self._deliver(self._user_conns(targets), payload, droppable=True)   # 밀려 있는 연결에는 조각을 버린다

    @staticmethod
    def _audio_frame_ok(data_b64: str, codec: str) -> bool:
        """음성 한 조각은 풀었을 때 MAX_AUDIO_FRAME_BYTES 이하여야 한다 (§8.2) - 받는 쪽이 크게 부풀려 풀지 않게"""
        if len(data_b64) > MAX_AUDIO_B64:
            return False
        try:
            raw = base64.b64decode(data_b64, validate=True)
            if codec == 'zpcm':
                unpacker = zlib.decompressobj()
                raw = unpacker.decompress(raw, MAX_AUDIO_FRAME_BYTES + 1)
                if unpacker.unconsumed_tail:
                    return False
        except (binascii.Error, ValueError, zlib.error):
            return False
        return len(raw) <= MAX_AUDIO_FRAME_BYTES

    def _h_call_list(self, ctx: Ctx, msg: dict) -> None:
        room_id = msg.get('room_id')
        if room_id is not None:
            self._require_room_member(room_id, ctx.username)
        with self._call_order:      # 목록과 그 뒤의 call_updated 가 상태 순서대로 가게 (기다리지 않고 세우기만 한다)
            self._deliver([ctx.conn], {'type': 'call_list', 'calls': self._calls_for_user(ctx.username, room_id)}, ctx)

    @_call_ordered
    def _run_voiceroom_timeouts(self) -> None:
        now = time.monotonic()
        with self._calls_lock:
            expired = [c for c in self._calls.values()
                       if c.mode == 'voiceroom' and now - c.started_mono >= self.voiceroom_max_seconds]
            for call in expired:
                self._calls.pop(call.call_id, None)
                self._room_calls.pop(call.room_id, None)
        for call in expired:
            self._finish_call(call)

    # ------------------------------------------------------------------ 8.3 펑(스토리)
    def _h_story_post(self, ctx: Ctx, msg: dict) -> None:
        kind = msg.get('kind', 'text')
        if kind not in ('text', 'image'):
            raise ProtocolError('펑 종류는 text/image 중 하나여야 합니다.')
        self._limit_action(ctx.username, 'story_post')
        content = _opt_str(msg, 'content', 500)
        bg_color = msg.get('bg_color') or DEFAULT_STORY_COLOR
        if not isinstance(bg_color, str) or not COLOR_RE.match(bg_color):
            raise ProtocolError('색상은 #RRGGBB 형식이어야 합니다.')
        attachment_id = None
        if kind == 'text':
            if not content:
                raise ProtocolError('펑 내용을 입력하세요.')
            if msg.get('attachment') is not None:
                raise ProtocolError('텍스트 펑에는 첨부 파일을 보낼 수 없습니다.')
        else:
            name, _mime, data = self._decode_attachment(msg.get('attachment'))
            sniffed = _sniff_image(data)
            if sniffed is None:
                raise ProtocolError('사진 펑은 이미지 파일(PNG/JPEG/GIF/BMP/WEBP)이어야 합니다.')
            self._check_upload_quota(ctx.username, len(data))
            attachment_id = self.store.save_attachment(name, sniffed[0], data, ctx.username)
        story = self.store.create_story(ctx.username, kind, content, attachment_id, bg_color.upper(),
                                        self.story_ttl_seconds)
        self._broadcast({'type': 'story_new', 'story': story}, ctx)

    def _h_story_list(self, ctx: Ctx, msg: dict) -> None:
        self._reply(ctx, {'type': 'story_list', 'stories': self.store.list_stories(ctx.username)})

    def _h_story_view(self, ctx: Ctx, msg: dict) -> None:
        story_id = _req_int(msg, 'story_id')
        story = self.store.get_story(story_id, ctx.username)
        if story is None:
            raise ProtocolError('존재하지 않거나 만료된 펑입니다.')
        if story['owner'] == ctx.username:
            self._reply(ctx, {'type': 'story_updated', 'story': story})
            return
        self.store.add_story_view(story_id, ctx.username)
        owner_view = self.store.get_story(story_id, story['owner'])
        if owner_view is not None:
            self._push([story['owner']], {'type': 'story_updated', 'story': owner_view})

    def _h_story_delete(self, ctx: Ctx, msg: dict) -> None:
        story_id = _req_int(msg, 'story_id')
        story = self.store.get_story(story_id, ctx.username)
        if story is None:
            raise ProtocolError('존재하지 않거나 만료된 펑입니다.')
        if story['owner'] != ctx.username:
            raise ProtocolError('본인의 펑만 삭제할 수 있습니다.')
        self.store.delete_story(story_id)
        self._broadcast({'type': 'story_deleted', 'story_id': story_id}, ctx)

    def _run_story_expiry(self) -> None:
        for story_id in self.store.pop_expired_stories():
            self._broadcast({'type': 'story_deleted', 'story_id': story_id})

    # ------------------------------------------------------------------ 8.5 멀티프로필
    def _h_multi_profile_set(self, ctx: Ctx, msg: dict) -> None:
        profiles = msg.get('profiles')
        if not isinstance(profiles, list) or not all(isinstance(p, dict) for p in profiles):
            raise ProtocolError('profiles 값이 올바르지 않습니다.')
        if len(profiles) > MAX_MULTI_PROFILES:
            raise ProtocolError(f'멀티프로필은 최대 {MAX_MULTI_PROFILES}개까지 만들 수 있습니다.')
        existing = {p['profile_id'] for p in self.store.list_multi_profiles(ctx.username)}
        seen_viewers: set[str] = set()
        cleaned: list[dict] = []
        for p in profiles:
            pid = _opt_int(p, 'profile_id')
            if pid is not None and pid not in existing:
                raise ProtocolError('존재하지 않는 멀티프로필입니다.')
            viewers = [v for v in _str_list(p, 'viewers', required=False)]
            if ctx.username in viewers:
                raise ProtocolError('나 자신은 멀티프로필 대상이 될 수 없습니다.')
            self._require_users_exist(viewers)
            if seen_viewers & set(viewers):
                raise ProtocolError('한 친구는 하나의 멀티프로필에만 지정할 수 있습니다.')
            seen_viewers |= set(viewers)
            display_name = _req_name(p, 'display_name', 20, label='이름')
            if not self.open_registration:      # 닫힌 서버: 멀티프로필도 이름은 실명 (§4.5)
                display_name = ctx.username
            item = {'profile_id': pid, 'name': _req_name(p, 'name', 20, label='프로필 이름'),
                    'display_name': display_name,
                    'status_message': _opt_str(p, 'status_message', 60), 'viewers': viewers}
            if _bool(p, 'avatar_clear'):
                item['avatar_id'] = self.store.org_avatar(ctx.username)
            elif p.get('avatar_b64') is not None:
                data = _decode_b64(p.get('avatar_b64'), MAX_AVATAR_BYTES, '프로필 사진은 512KB를 초과할 수 없습니다.')
                sniffed = _sniff_image(data)
                if sniffed is None:
                    raise ProtocolError('프로필 사진은 이미지 파일(PNG/JPEG/GIF/BMP/WEBP)이어야 합니다.')
                item['avatar_data'] = (sniffed, data)
            else:
                item['avatar_keep'] = True
            cleaned.append(item)
        for item in cleaned:   # 검증이 모두 끝난 뒤에 사진 저장
            if 'avatar_data' in item:
                (mime, ext), data = item.pop('avatar_data')
                item['avatar_id'] = self.store.save_attachment(f'avatar.{ext}', mime, data, ctx.username)
        result = self.store.replace_multi_profiles(ctx.username, cleaned)
        self._reply(ctx, {'type': 'multi_profile_list', 'profiles': result})
        self._broadcast_user(ctx.username, exclude_user=ctx.username)

    def _h_multi_profile_list(self, ctx: Ctx, msg: dict) -> None:
        self._reply(ctx, {'type': 'multi_profile_list', 'profiles': self.store.list_multi_profiles(ctx.username)})

    # ------------------------------------------------------------------ 8.6 차단
    def _h_block_set(self, ctx: Ctx, msg: dict) -> None:
        target = _req_str(msg, 'username', 20, label='대상')
        if target == ctx.username:
            raise ProtocolError('나 자신은 차단할 수 없습니다.')
        if msg.get('blocked') is None:
            raise ProtocolError('blocked 값이 올바르지 않습니다.')
        blocked = _bool(msg, 'blocked')
        self._require_users_exist([target])
        self._reply(ctx, {'type': 'block_list', 'blocked': self.store.set_block(ctx.username, target, blocked)})

    # ------------------------------------------------------------------ 8.7 사다리 게임
    @staticmethod
    def _ladder_items(msg: dict, key: str) -> list[str]:
        value = msg.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ProtocolError(f'{key} 값이 올바르지 않습니다.')
        items = [v.strip() for v in value]
        if any(not v or len(v) > 30 for v in items):
            raise ProtocolError('사다리 항목은 1~30자여야 합니다.')
        return items

    def _h_ladder_create(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        self._require_not_secret(meta)
        participants = self._ladder_items(msg, 'participants')
        results = self._ladder_items(msg, 'results')
        if not LADDER_MIN <= len(participants) <= LADDER_MAX:
            raise ProtocolError('사다리 참가자는 2~10명이어야 합니다.')
        if len(participants) != len(results):
            raise ProtocolError('참가자와 결과의 개수가 같아야 합니다.')
        if len(set(participants)) != len(participants):
            raise ProtocolError('참가자 이름이 중복되었습니다.')
        rungs = generate_ladder_rungs(len(participants))
        ladder = {'participants': participants, 'results': results, 'rungs': rungs,
                  'mapping': compute_ladder_mapping(participants, results, rungs)}
        message = self.store.add_message(meta['room_id'], ctx.username, 'ladder', '사다리 게임', ladder=ladder)
        self._publish_new_message(message, ctx)

    # ------------------------------------------------------------------ 8.8 톡게시판
    def _require_post(self, post_id: int, username: str) -> dict:
        post = self.store.get_post(post_id)
        if post is None:
            raise ProtocolError('존재하지 않는 게시글입니다.')
        if not self.store.is_member(post['room_id'], username):
            raise ProtocolError('이 채팅방의 대화상대가 아닙니다. 초대를 받은 뒤 이용할 수 있습니다.')
        return post

    def _h_board_post(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        self._require_not_secret(meta)
        title = _req_str(msg, 'title', 100, label='제목')
        content = _opt_str(msg, 'content', MAX_CONTENT_LEN)
        post, message = self.store.create_post(meta['room_id'], ctx.username, title, content)
        self._publish_new_message(message)
        self._push(meta['members'], {'type': 'board_updated', 'post': post}, ctx)

    def _h_board_list(self, ctx: Ctx, msg: dict) -> None:
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        self._reply(ctx, {'type': 'board_list', 'room_id': meta['room_id'],
                          'posts': self.store.list_posts(meta['room_id'])})

    def _h_board_comment(self, ctx: Ctx, msg: dict) -> None:
        post = self._require_post(_req_int(msg, 'post_id'), ctx.username)
        content = _req_str(msg, 'content', 1000, label='댓글')
        updated = self.store.add_comment(post['post_id'], ctx.username, content)
        self._push(self.store.members(post['room_id']), {'type': 'board_updated', 'post': updated}, ctx)

    def _h_board_delete(self, ctx: Ctx, msg: dict) -> None:
        post = self._require_post(_req_int(msg, 'post_id'), ctx.username)
        if post['author'] != ctx.username:
            raise ProtocolError('작성자만 삭제할 수 있습니다.')
        message = self.store.delete_post(post['post_id'])
        if message is not None:
            self._push_msg_updated(message)
        self._push(self.store.members(post['room_id']),
                   {'type': 'board_deleted', 'post_id': post['post_id'], 'room_id': post['room_id']}, ctx)

    # ------------------------------------------------------------------ 8.9 오픈채팅
    def _h_open_room_create(self, ctx: Ctx, msg: dict) -> None:
        name = _req_name(msg, 'name', 50, label='채팅방 이름')
        description = _opt_str(msg, 'description', 500)
        tags = _str_list(msg, 'tags', required=False)
        if len(tags) > 10 or any(len(t) > 20 for t in tags):
            raise ProtocolError('태그는 20자 이하, 최대 10개까지 가능합니다.')
        nickname = _opt_name(msg, 'nickname', 20) or self.store.display_name(ctx.username)
        room_id = self.store.create_open_room(ctx.username, name, description, tags, nickname)
        self._push_room_updated(room_id, [ctx.username], ctx)

    def _h_open_room_search(self, ctx: Ctx, msg: dict) -> None:
        query = _opt_str(msg, 'query', 100)
        self._reply(ctx, {'type': 'open_room_search', 'query': query, 'rooms': self.store.search_open_rooms(query)})

    def _h_open_room_join(self, ctx: Ctx, msg: dict) -> None:
        room_id = msg.get('room_id')
        if not isinstance(room_id, str) or not room_id:
            raise ProtocolError('room_id 값이 올바르지 않습니다.')
        meta = self.store.room_meta(room_id)
        if meta is None or meta['type'] != 'open':
            raise ProtocolError('존재하지 않는 오픈채팅방입니다.')
        if ctx.username in meta['members']:
            raise ProtocolError('이미 참여한 오픈채팅방입니다.')
        if self.store.is_banned(room_id, ctx.username):
            raise ProtocolError('방장이 내보낸 오픈채팅방에는 다시 들어갈 수 없습니다.')
        nickname = _req_name(msg, 'nickname', 20, label='닉네임')
        self.store.join_open_room(room_id, ctx.username, nickname)
        self._push_room_updated(room_id, meta['members'] + [ctx.username], ctx)
        self._post_system(room_id, f'{nickname}님이 들어왔습니다')

    # ------------------------------------------------------------------ 8.10 보관 기간 / 톡서랍
    def _h_drawer_query(self, ctx: Ctx, msg: dict) -> None:
        kind = msg.get('kind')
        if kind not in ('image', 'file', 'link'):
            raise ProtocolError('서랍 종류는 image/file/link 중 하나여야 합니다.')
        limit = _opt_int(msg, 'limit')
        limit = 50 if limit is None else max(1, min(limit, MAX_DRAWER_LIMIT))
        self._reply(ctx, {'type': 'drawer_result', 'kind': kind,
                          'messages': self.store.drawer(ctx.username, kind, limit)})

    def _run_retention(self) -> None:
        for message in self.store.purge_expired_attachments():
            self._push_msg_updated(message)

    # ------------------------------------------------------------------ §9 v4 (2026 업데이트)
    def _summary_llm(self) -> Any:
        """요약용 Claude 클라이언트: RAG 서비스의 것을 재사용 (없거나 꺼져 있으면 None)"""
        getter = getattr(self.rag, '_llm', None)
        try:
            return getter() if getter else None
        except Exception:
            return None

    def _h_room_summary(self, ctx: Ctx, msg: dict) -> None:
        """채팅방 대화 요약. unread_only=True 면 내 읽음 위치 이후, since_msg_id 면 그 이후 메시지만"""
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        if meta['type'] == 'secret':
            raise ProtocolError('비밀채팅방의 대화는 서버가 읽을 수 없어 요약할 수 없습니다.')
        room_id = meta['room_id']
        unread_only = bool(msg.get('unread_only'))
        since = _opt_int(msg, 'since_msg_id')
        if unread_only and since is None:
            since = self.store.read_pointer(room_id, ctx.username)
        unread_only = unread_only or since is not None
        messages, _ = self.store.history(room_id, None, MAX_SUMMARY_MESSAGES, viewer=ctx.username,
                                         after_msg_id=since)          # '이후만'은 SQL 에서 거른다
        llm = self._summary_llm()

        def work() -> None:
            text, source = chat_summary.summarize(messages, self._name_of, llm, on_fatal=self._disable_summary_llm)
            self._reply(ctx, {'type': 'room_summary', 'room_id': room_id, 'summary': text, 'source': source,
                              'count': len(chat_summary._usable(messages)), 'unread_only': unread_only})

        if llm is None:              # 추출형은 몇 ms — 스레드 없이 바로
            work()
            return
        key = (ctx.username, room_id)
        with self._summary_lock:
            if key in self._summaries_running:      # 칩을 여러 번 눌러도 Claude 요약은 하나만
                return
            self._summaries_running.add(key)

        def run() -> None:
            try:
                work()
            finally:
                with self._summary_lock:
                    self._summaries_running.discard(key)

        self._spawn(run, name='room-summary')

    def _disable_summary_llm(self) -> None:
        """API 키 인증 실패: RAG 와 같은 표시를 세워 이후 요약·답변이 매번 실패 호출을 하지 않게"""
        if self.rag is not None:
            self.rag._llm_failed = True

    def _name_of(self, username: str) -> str:
        return self.store.display_name(username) if username else ''

    def _h_call_history(self, ctx: Ctx, msg: dict) -> None:
        limit = min(max(_opt_int(msg, 'limit') or 100, 1), MAX_CALL_HISTORY)
        calls = self.store.call_history(ctx.username, limit)
        for c in calls:
            c['joined'] = ctx.username in c['participants']
            c['missed'] = not c['joined']
        self._reply(ctx, {'type': 'call_history', 'calls': calls})

    def _h_room_export(self, ctx: Ctx, msg: dict) -> None:
        """대화 내보내기: 500개씩 읽고(저장소 잠금은 쪽마다) 2000개씩 여러 조각으로 보낸다.
        room_export {room_id, since?, until?} → room_export {room_id, messages(이 조각, 오래된 순), part, done,
        truncated, since, until}. since/until 은 'YYYY-MM-DD'(그날 포함) - 그 기간의 메시지만 (명세 §9.5)"""
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        room_id = meta['room_id']
        since, until = self._export_day(msg, 'since'), self._export_day(msg, 'until')
        if since and until and since > until:
            raise ProtocolError('시작 날짜가 끝 날짜보다 늦습니다.')
        self._limit_action(ctx.username, 'room_export')

        def in_range(m: dict) -> bool:
            day = (m.get('timestamp') or '')[:10]
            return (not since or day >= since) and (not until or day <= until)

        messages, before, truncated = [], None, False
        while True:
            page, more = self.store.history(room_id, before, EXPORT_PAGE, viewer=ctx.username)
            messages = [m for m in page if in_range(m)] + messages
            reached_start = bool(since and page and (page[0].get('timestamp') or '')[:10] < since)
            if not more or not page or reached_start:      # 기간 시작보다 오래된 쪽까지 왔으면 그만 읽는다
                break
            if len(messages) >= MAX_EXPORT_MESSAGES:
                truncated = True
                break
            before = page[0]['msg_id']
        messages = messages[-MAX_EXPORT_MESSAGES:]
        chunks = [messages[k:k + EXPORT_CHUNK] for k in range(0, len(messages), EXPORT_CHUNK)] or [[]]
        for part, chunk in enumerate(chunks):
            self._reply(ctx, {'type': 'room_export', 'room_id': room_id, 'messages': chunk, 'part': part,
                              'done': part == len(chunks) - 1, 'truncated': truncated,
                              'since': since, 'until': until})

    @staticmethod
    def _export_day(msg: dict, key: str) -> Optional[str]:
        """내보내기 기간 'YYYY-MM-DD' (없으면 None)"""
        value = msg.get(key)
        if value in (None, ''):
            return None
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ProtocolError('기간은 YYYY-MM-DD 형식으로 보내 주세요.')
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ProtocolError('기간은 YYYY-MM-DD 형식으로 보내 주세요.') from None
        return value
