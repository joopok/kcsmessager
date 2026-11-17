# -*- coding: utf-8 -*-
"""
AppState - 클라이언트의 단일 데이터 저장소
서버 푸시를 받아 상태를 갱신하고, UI에는 고수준 시그널만 노출한다.
UI는 소켓을 직접 다루지 않고 이 클래스의 액션 메서드만 호출한다.
(프로토콜: claudedocs/KAKAO_FEATURE_SPEC.md)
"""

import base64
import mimetypes
import os
import re
from collections import OrderedDict
from datetime import datetime, date, timedelta

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

MAX_ATTACHMENT = 10 * 1024 * 1024
MAX_VOICE_SECONDS = 300   # 음성메시지 최대 5분 (명세 §9.3)
FAILED_ID_BASE = 10 ** 12  # 보내지 못한 메시지의 로컬 id (서버 id 보다 항상 커서 대화 맨 아래에 놓인다)
MAX_AVATAR = 512 * 1024
PRESENCES = ('online', 'away', 'busy', 'meeting', 'vacation')
PRESENCE_LABELS = {'online': '온라인', 'away': '자리비움', 'busy': '다른 용무 중',
                   'meeting': '회의 중', 'vacation': '휴가 중', 'offline': '오프라인'}
PRESENCE_COLORS = {'online': '#3DD16B', 'away': '#F5B400', 'busy': '#FF4B3E',
                   'meeting': '#8E6CEF', 'vacation': '#1FA2B8', 'offline': '#BDBDBD'}
BOT_NAME = 'AI 비서'
URL_RE = re.compile(r'https?://[^\s<>"]+')


WEEKDAYS = '월화수목금토일'
CALL_MODE_LABELS = {'voice': '보이스톡', 'video': '페이스톡', 'voiceroom': '보이스룸'}   # 통화 종류 이름 (앱 전체 하나)


def fmt_duration(seconds):
    """초 → 'm:ss', 1시간 이상은 'h:mm:ss' (음성메시지·통화 길이 · 통화 창 공용)"""
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        seconds = 0
    if seconds >= 3600:
        return f'{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'
    return f'{seconds // 60}:{seconds % 60:02d}'


def fmt_ampm(dt):
    """datetime → '오전 9:06' / '오후 2:30' (말풍선 시간 · 대화 내보내기 공용)"""
    return f"{'오전' if dt.hour < 12 else '오후'} {dt.hour % 12 or 12}:{dt.minute:02d}"


def fmt_day(d):
    """date → '2026년 10월 9일 금요일'"""
    return f'{d.year}년 {d.month}월 {d.day}일 {WEEKDAYS[d.weekday()]}요일'


def read_upload(path, too_big='10MB 이하의 파일만 보낼 수 있습니다.'):
    """보낼 파일 읽기 → (bytes, None) 또는 (None, 오류 문구). 권한 없음 · 지워짐 · 잠김도 오류 문구로
    (예전에는 open 이 슬롯 밖으로 예외를 던져 끌어다 놓은 나머지 파일도 못 보냈다)"""
    try:
        if os.path.getsize(path) > MAX_ATTACHMENT:
            return None, too_big
        with open(path, 'rb') as f:
            return f.read(), None
    except OSError as e:
        return None, f'파일을 열 수 없습니다: {e}'


def attachment_payload(name, mime, data):
    """send 요청의 첨부 형식 {name, mime, data_b64}"""
    return {'name': name, 'mime': mime, 'data_b64': base64.b64encode(data).decode('ascii')}


def parse_dt(value):
    """ISO 문자열 → naive datetime (타임존 표기는 무시)"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def sniff_image_mime(data):
    """내용 앞부분으로 본 사진 형식 → 'image/png' 등. 채팅 화면이 사진으로 보여 주는 형식이 아니면 ''"""
    head = bytes(data[:16])
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if head.startswith(b'BM'):
        return 'image/bmp'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image/webp'
    return ''


def _csv_safe(text):
    """엑셀이 수식으로 실행하지 않도록 = + - @ 로 시작하는 칸 앞에 ' 를 붙인다 (CSV 수식 주입 방지)"""
    text = text or ''
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def birthday_md(value):
    """생년월일 'YYYY-MM-DD' 또는 생일 'MM-DD' → 'MM-DD' (생일 비교용, 없으면 None)"""
    return value[-5:] if isinstance(value, str) and len(value) in (5, 10) else None


ATTACHMENT_CACHE_BYTES = 200 * 1024 * 1024


class AttachmentCache(OrderedDict):
    """받은 첨부 (attachment_id → (bytes, name, mime)). 전체 크기가 limit 을 넘으면 가장 오래 안 쓴 것부터 버린다.
    버린 첨부는 다시 필요할 때 서버에서 받는다 (화면에 떠 있는 사진 · 프로필 사진은 자기 바이트를 따로 들고 있다).
    방금 넣은 것은 limit 보다 커도 남긴다"""

    def __init__(self, limit=ATTACHMENT_CACHE_BYTES):
        super().__init__()
        self.limit = limit
        self.size = 0

    def __setitem__(self, key, value):
        if key in self:
            del self[key]
        super().__setitem__(key, value)
        self.size += len(value[0])
        while self.size > self.limit and len(self) > 1:
            del self[next(iter(self))]

    def __delitem__(self, key):
        self.size -= len(super().__getitem__(key)[0])
        super().__delitem__(key)

    def get(self, key, default=None):
        if key not in self:
            return default
        self.move_to_end(key)       # 방금 쓴 것은 나중에 버린다
        return super().__getitem__(key)

    def clear(self):
        super().clear()
        self.size = 0


class AppState(QObject):
    welcomed = pyqtSignal()
    login_failed = pyqtSignal(str)             # join 거절(아이디/비밀번호 오류 등) 사유 - 구분 코드는 login_error 에
    session_expired = pyqtSignal(str)          # 비밀번호가 바뀌어 이 접속이 끝남 → 로그인 창으로 (다시 붙지 않는다)
    password_changed = pyqtSignal(dict)        # 내 비밀번호 변경 성공 {login_token?}
    password_change_failed = pyqtSignal(str, str)   # (문구, 구분 코드)
    connection_lost = pyqtSignal(str)
    users_changed = pyqtSignal()
    presence_changed = pyqtSignal(str)       # 접속 상태만 바뀐 사람 (users_changed 바로 앞에 나간다 - 목록이 그 사람 행만 다시 그리게)
    user_changed = pyqtSignal(str)
    rooms_changed = pyqtSignal()
    room_changed = pyqtSignal(str)
    room_removed = pyqtSignal(str)
    room_opened = pyqtSignal(str)              # 내가 요청한 방이 준비됨 → 창 열기
    message_added = pyqtSignal(str, dict)      # room_id, message
    message_updated = pyqtSignal(str, dict)
    history_loaded = pyqtSignal(str, list, bool)  # room_id, 이번에 받은 메시지, has_more
    thread_loaded = pyqtSignal(str, int)         # room_id, 스레드 원글 msg_id (§10.4)
    mentions_changed = pyqtSignal()              # 멘션 모아보기 목록이 바뀜 (§10.5)
    saved_changed = pyqtSignal()                 # 책갈피 목록이 바뀜 (§10.7)
    read_changed = pyqtSignal(str)
    events_changed = pyqtSignal()
    event_reminder = pyqtSignal(dict, int)
    todos_changed = pyqtSignal()
    search_results = pyqtSignal(str, list)
    attachment_loaded = pyqtSignal(int, bytes, str, str)  # id, data, name, mime
    admin_users_changed = pyqtSignal(list)   # 관리자 계정 목록 (명세 §4.2)
    admin_done = pyqtSignal(dict)            # 관리자 요청 성공 {action, username?, ...}
    org_changed = pyqtSignal()               # 조직도 그룹이 바뀜 (명세 §4.4)
    admin_notice = pyqtSignal(dict)          # 관리자 전체 공지 {sender, content, timestamp}
    account_disabled = pyqtSignal(str)       # 내 계정이 사용 중지됨 → 로그인 창으로
    attachment_failed = pyqtSignal(str)   # attachment_get 오류 사유 (대화 저장이 어느 첨부가 빠졌는지 안다)
    scheduled_ok = pyqtSignal(dict)
    error = pyqtSignal(str)
    notify = pyqtSignal(str, str, str)         # 제목, 본문, room_id (트레이 알림)
    avatar_changed = pyqtSignal(str)           # username - 프로필 사진 로드/변경
    rag_docs_changed = pyqtSignal(list)        # AI 지식베이스 문서 목록
    # ---- v3 (명세 §8) ----
    calls_changed = pyqtSignal()
    call_ended = pyqtSignal(dict)              # {call_id, room_id, duration_seconds}
    call_media = pyqtSignal(int, str, str, int, str, dict)  # call_id, from, kind, seq, data_b64, 기타 필드
    stories_changed = pyqtSignal()
    multi_profiles_changed = pyqtSignal(list)
    blocked_changed = pyqtSignal()
    board_changed = pyqtSignal(str)            # room_id
    open_rooms_found = pyqtSignal(list)
    drawer_loaded = pyqtSignal(str, list)      # kind, messages (전체 톡서랍)
    friend_added = pyqtSignal(dict, bool)      # 추가된 사용자, 이미 친구였는지
    my_friends_changed = pyqtSignal()
    # v4 (명세 §9)
    room_summary_ready = pyqtSignal(str, dict)   # room_id, {summary, source, count, unread_only}
    call_history_changed = pyqtSignal()
    room_export_ready = pyqtSignal(str, list, bool)  # room_id, messages, truncated
    send_failed = pyqtSignal(str, dict)          # room_id, 보내지 못한 메시지
    message_removed = pyqtSignal(str, object)    # room_id, msg_id (로컬 id 는 10^12 대라 int(32비트) 시그널로는 깨진다)
    # 파일 전송 보안 (명세 §10.10)
    file_policy_changed = pyqtSignal()
    file_blocked = pyqtSignal(dict)              # 막힌 파일 {room_id, name, detail, client_tmp_id, ...} → 승인 요청 창
    file_request_changed = pyqtSignal(dict)      # 내 승인 요청이 접수 · 승인 · 반려됨
    file_requests_loaded = pyqtSignal(list)      # 내 승인 요청 목록
    admin_file_requests_changed = pyqtSignal(list)
    admin_file_exceptions_changed = pyqtSignal(list)
    admin_file_request_new = pyqtSignal(dict)    # 그룹 관리자: 새 승인 요청 / 다른 관리자가 처리함

    def __init__(self, conn, settings=None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.settings = settings
        self.me = ''
        self.login_token = ''  # 서버가 준 자동 로그인 토큰 (remember 로그인 때만)
        self.users = {}       # username -> User
        self.mentions = []    # 나를 멘션한 메시지 (최신순, §10.5)
        self.rooms = {}       # room_id -> Room
        self._messages = {}   # room_id -> {msg_id: Message}
        self.has_more = {}    # room_id -> bool
        self.history_requested = set()
        self._gap_fill = {}   # room_id → (재연결 전 마지막 msg_id, 그때 has_more)
        self._history_oldest = {}   # room_id → history 로 받은 가장 오래된 msg_id (이전 대화 더 보기 기준)
        self.events = {}      # event_id -> Event
        self.todos = {}       # todo_id -> Todo
        self.attachments = AttachmentCache()  # attachment_id -> (bytes, name, mime), 크기 상한이 있다
        self.avatars = {}      # username -> (avatar_id, bytes)
        self.rag_docs = []
        self.org = {}
        self.policy = {}         # 서버 정책 (welcome.policy) - 예전 서버는 주지 않는다
        self.login_error = {'code': '', 'retry_after': 0}   # 마지막 join · register 거절의 구분 코드 (예전 서버는 '')
        self.session_password = None    # 이번 실행에서 바꾼 새 비밀번호 (다시 연결할 때 쓴다, 메모리에만)
        self._new_password = None
        self._ended_calls = set()   # 이번 연결에서 끝난 통화 번호 (늦게 온 call_updated 를 버린다)
        self.keyring = None      # 비밀채팅 키 (welcome 후 생성)
        self.peer_keys = None    # 상대 공개키 기억 (처음 본 키를 믿고, 바뀌면 확인을 거친다)
        self.calls = {}          # call_id -> Call
        self.stories = {}        # story_id -> Story
        self.seen_stories = set()
        self.multi_profiles = []
        self.blocked = set()
        self.boards = {}         # room_id -> {post_id: Post}
        self.locked = False      # 잠금모드 중이면 알림 내용 숨김
        self.my_friends = []     # 전화번호/이메일로 등록한 친구
        self.call_history = []   # 끝난 통화 기록 (채팅탭 '통화' 폴더)
        self._failed = None      # 보내지 못한 메시지 메모리 사본 (_failed_list)
        self._failed_rooms = {}  # room_id → 실패 메시지 수 (채팅 목록 행마다 O(1) 조회)
        self._export_parts = {}  # room_id → 받는 중인 내보내기 조각
        self.quiet_attachment_errors = False
        self.admin_users = []
        self.file_policy = {}       # 파일 전송 보안 정책 (§10.10, welcome · file_policy)
        self._outgoing_files = {}   # client_tmp_id → (room_id, kind, name, mime, data): 막히면 승인 요청에 다시 쓴다
        self.admin_scope = ''   # 대화 저장(zip)이 첨부를 모으는 동안: 만료 첨부마다 오류 알림을 띄우지 않는다
        self._failed_saved_at = -1e9
        self._failed_save_timer = None
        self.active_rooms = set()  # 지금 보고 있는 방 (창이 앞에 있다 - 알림 억제용)
        self.open_rooms = set()    # 창이 떠 있는 방 (앞이든 뒤든 - 다시 연결되면 먼저 대화를 받는다)
        self._refill = []          # 다시 연결된 뒤 차례로 대화를 받을 나머지 방
        self._refill_timer = QTimer(self)
        self._refill_timer.timeout.connect(self._refill_next)
        self._pending_direct = set()
        self.created_rooms = set()   # 내가 방금 만든 그룹방 - 처음 열 때 참여자 줄을 펼쳐 보여 준다
        self._pending_groups = []
        if conn is not None:
            conn.event_received.connect(self.handle)
            conn.disconnected.connect(self.connection_lost)
        self.connection_lost.connect(self._drop_unfinished)

    def _drop_unfinished(self, _reason=''):
        """연결이 끊기면 받다 만 것은 이어지지 않는다 - 다음 요청에 섞이지 않게 버린다"""
        self._export_parts.clear()
        self._refill = []
        self._refill_timer.stop()

    REFILL_INTERVAL_MS = 300

    def _refill_next(self):
        """다시 연결된 뒤 기다리던 방 하나의 최근 대화를 받는다 (그사이 창이 열려 이미 받은 방은 건너뛴다)"""
        while self._refill:
            rid = self._refill.pop(0)
            if rid in self.rooms and rid not in self.history_requested:
                if self._send('history', room_id=rid, limit=50) is not None:
                    self.history_requested.add(rid)
                break
        if not self._refill:
            self._refill_timer.stop()

    # ================= 수신 처리 =================
    def handle(self, msg_type, p):
        handler = getattr(self, f'_on_{msg_type}', None)
        if handler:
            handler(p)

    def _on_welcome(self, p):
        reconnected = bool(self.me)            # 두 번째 welcome = 다시 붙음
        self.me = p['me']['username']
        if p.get('login_token'):
            self.login_token = p['login_token']   # 자동 로그인 저장용 (명세 §4.1)
        if self.settings is not None and hasattr(self.settings, 'use_user'):
            self.settings.use_user(self.me)   # 계정(kmkim)으로 로그인해도 사용자 이름 설정 파일을 쓴다
        self.users = {u['username']: u for u in p.get('users', [])}
        self.users[self.me] = p['me']
        self.rooms = {r['room_id']: r for r in p.get('rooms', [])}
        self.org = p.get('org') or {}   # {'holding': {...}|None, 'companies': [...], 'groups': [...]}
        self.calls = {c['call_id']: c for c in p.get('calls', [])}
        self._ended_calls = set()        # 새 연결: 서버가 준 목록이 기준 (서버를 다시 켜면 통화 번호가 처음부터일 수 있다)
        self.blocked = set(p.get('blocked', []))
        self.my_friends = list(p.get('friends', []))
        self.file_policy = dict(p.get('file_policy') or {})
        self.policy = dict(p.get('policy') or {}) if isinstance(p.get('policy'), dict) else {}
        self._pending_direct.clear()           # 끊기기 전에 기다리던 방 열기는 다시 붙은 뒤에 오지 않는다
        self._pending_groups.clear()
        self._init_keyring()
        self.users_changed.emit()
        self.rooms_changed.emit()
        for name in self.users:
            self._sync_avatar(name)
        self.welcomed.emit()
        today = date.today()
        self.request_events(datetime(today.year - 1, 1, 1), datetime(today.year + 2, 1, 1))
        self.request_todos()
        self.request_stories()
        self.request_call_history()
        self._restore_failed()
        if reconnected:
            # 끊긴 사이 온 메시지: 받아 둔 방마다 최근 대화를 다시 받아 합친다 (열린 채팅방은 history_loaded 로 갱신)
            waiting = []
            self.history_requested.clear()      # 예전 연결에서 받은 표시는 새 연결에 통하지 않는다
            for rid in list(self._messages):
                if rid in self.rooms:
                    known = [i for i in self._messages[rid] if isinstance(i, int) and i < FAILED_ID_BASE]
                    if known:   # 최근 50개가 예전 캐시와 이어지지 않으면 이어질 때까지 더 받는다
                        self._gap_fill[rid] = (max(known), self.has_more.get(rid, True))
                    if rid in self.open_rooms or rid in self.active_rooms:
                        if self._send('history', room_id=rid, limit=50) is not None:   # 떠 있는 방은 바로
                            self.history_requested.add(rid)
                    else:
                        waiting.append(rid)
            # 나머지 방은 한꺼번에 요청하지 않고 최근에 대화가 있던 방부터 하나씩 (방이 많아도 서버 · 화면이 몰리지 않게)
            waiting.sort(key=lambda r: (self.rooms[r].get('last_message') or {}).get('msg_id') or 0, reverse=True)
            self._refill = waiting
            if waiting:
                self._refill_timer.start(self.REFILL_INTERVAL_MS)
        self.calls_changed.emit()

    def _on_user_status_changed(self, p):
        user = self.users.get(p['username'])
        if user is not None:
            user['online'] = p['status'] == 'online'
            self.presence_changed.emit(p['username'])
            self.user_changed.emit(p['username'])
            self.users_changed.emit()

    def _on_user_updated(self, p):
        user = p['user']
        self.users[user['username']] = user
        self._sync_avatar(user['username'])
        self.user_changed.emit(user['username'])
        self.users_changed.emit()
        self.rooms_changed.emit()

    def _on_room_updated(self, p):
        room = p['room']
        rid = room['room_id']
        is_new = rid not in self.rooms
        self.rooms[rid] = room
        self.room_changed.emit(rid)
        self.rooms_changed.emit()
        members = set(room.get('members', []))
        if room['type'] in ('direct', 'self', 'secret'):
            others = members - {self.me}
            target = next(iter(others)) if others else self.me
            if target in self._pending_direct:
                self._pending_direct.discard(target)
                self.room_opened.emit(rid)
        elif room['type'] in ('group', 'open') and is_new:
            for pending in list(self._pending_groups):
                if pending <= members:
                    self._pending_groups.remove(pending)
                    self.created_rooms.add(rid)
                    self.room_opened.emit(rid)
                    break

    def _on_room_removed(self, p):
        rid = p['room_id']
        if p.get('kicked'):
            title = self.room_title(rid) if rid in self.rooms else '채팅방'
            self.notify.emit('KCS Talk', f"'{title}' 채팅방에서 내보내졌습니다.", '')
        self.rooms.pop(rid, None)
        self._messages.pop(rid, None)
        self.history_requested.discard(rid)   # 다시 초대되면 히스토리를 새로 받는다
        self.has_more.pop(rid, None)
        self._gap_fill.pop(rid, None)
        self.room_removed.emit(rid)
        self.rooms_changed.emit()

    def _on_history(self, p):
        rid = p['room_id']
        store = self._messages.setdefault(rid, {})
        batch = p.get('messages', [])
        for m in batch:
            self._decrypt(m)
        for m in batch:
            store[m['msg_id']] = m
        if batch:   # 이전 대화 더 보기는 history 로 받은 가장 오래된 메시지부터 (스레드 창이 넣은 원글은 빼고)
            self._history_oldest[rid] = min([m['msg_id'] for m in batch]
                                            + ([self._history_oldest[rid]] if rid in self._history_oldest else []))
        gap = self._gap_fill.get(rid)
        if gap is not None:
            known_max, had_more = gap
            oldest = min((m['msg_id'] for m in batch), default=None)
            if oldest is not None and oldest > known_max + 1 and p.get('has_more'):
                self._send('history', room_id=rid, before_msg_id=oldest, limit=50)   # 끊긴 사이 빈 구간
            else:
                del self._gap_fill[rid]
            self.has_more[rid] = had_more      # 더 이전 쪽은 예전 캐시 기준 그대로
        else:
            self.has_more[rid] = bool(p.get('has_more'))
        self.history_loaded.emit(rid, batch, self.has_more[rid])

    def _on_msg_new(self, p):
        m = p['message']
        self._decrypt(m)
        rid = m['room_id']
        store = self._messages.setdefault(rid, {})
        duplicate = m['msg_id'] in store      # welcome 스냅숏에 이미 반영된 메시지가 push 로 또 올 수 있다
        store[m['msg_id']] = m
        room = self.rooms.get(rid)
        if room is not None:
            room['last_message'] = m
            if m['sender'] != self.me and m['kind'] != 'system' and not duplicate:
                room['unread_count'] = room.get('unread_count', 0) + 1
            if m['sender'] == self.me:
                room.setdefault('read_pointers', {})[self.me] = max(
                    room.get('read_pointers', {}).get(self.me, 0), m['msg_id'])
        self.message_added.emit(rid, m)
        if not duplicate:
            self._note_mention(m)
        self.rooms_changed.emit()
        if m['sender'] and m['sender'] != self.me and rid not in self.active_rooms and self.should_alert(rid, m):
            if self.locked:
                self.notify.emit('KCS Talk', '새 메시지가 있습니다', rid)   # 잠금모드: 내용 숨김
            else:
                self.notify.emit(self.room_title(rid), self.message_preview(m), rid)

    def _on_msg_updated(self, p):
        m = p['message']
        self._decrypt(m)
        rid = m['room_id']
        self._messages.setdefault(rid, {})[m['msg_id']] = m
        room = self.rooms.get(rid)
        if room is not None:
            if (room.get('last_message') or {}).get('msg_id') == m['msg_id']:
                room['last_message'] = m
                self.rooms_changed.emit()
            if (room.get('notice') or {}).get('msg_id') == m['msg_id']:
                room['notice'] = m
                self.room_changed.emit(rid)
        self.message_updated.emit(rid, m)

    def _on_read_update(self, p):
        rid = p['room_id']
        room = self.rooms.get(rid)
        if room is None:
            return
        pointers = room.setdefault('read_pointers', {})
        pointers[p['username']] = max(pointers.get(p['username'], 0), p['last_read_msg_id'])
        if p['username'] == self.me:
            count = p.get('unread_count')
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                room['unread_count'] = count        # 서버가 센 값 (다른 기기에서 일부만 읽은 경우도 맞는다)
            else:                                   # 예전 서버: 받아 둔 메시지로 센다
                mine = pointers[self.me]
                loaded = self._messages.get(rid, {})
                room['unread_count'] = sum(
                    1 for m in loaded.values()
                    if m['msg_id'] > mine and m['sender'] != self.me and m['kind'] != 'system')
            self.rooms_changed.emit()
        self.read_changed.emit(rid)

    def _on_search_result(self, p):
        self.search_results.emit(p.get('query', ''), p.get('messages', []))

    def _on_event_updated(self, p):
        ev = p['event']
        old = self.events.get(ev['event_id'])
        self.events[ev['event_id']] = ev
        self.events_changed.emit()
        # 다른 사람이 일정의 제목/시간/장소를 바꾸면 알림 (톡캘린더 일정 변경 알림)
        if old is not None and ev.get('creator') != self.me:
            changed = [label for key, label in (('title', '제목'), ('start', '시간'), ('end', '시간'),
                                                ('location', '장소')) if old.get(key) != ev.get(key)]
            if changed:
                start = parse_dt(ev['start'])
                when = start.strftime('%m/%d %H:%M') if start else ''
                self.notify.emit(f"📅 일정이 변경되었습니다: {ev['title']}",
                                 f"{'·'.join(dict.fromkeys(changed))} 변경 · {when}", ev.get('room_id') or '')

    def _on_event_deleted(self, p):
        self.events.pop(p['event_id'], None)
        self.events_changed.emit()

    def _on_event_list(self, p):
        for ev in p.get('events', []):
            self.events[ev['event_id']] = ev
        self.events_changed.emit()

    def _on_event_reminder(self, p):
        ev = p['event']
        minutes = int(p.get('minutes_before') or ev.get('remind_minutes') or 0)
        self.events[ev['event_id']] = ev
        self.event_reminder.emit(ev, minutes)
        start = parse_dt(ev['start'])
        when = start.strftime('%m/%d %H:%M') if start else ''
        body = f"{minutes}분 후 시작 · {when}" if minutes else f"지금 시작 · {when}"
        self.notify.emit(f"📅 {ev['title']}", body, ev.get('room_id') or '')

    def _on_todo_updated(self, p):
        todo = p['todo']
        self.todos[todo['todo_id']] = todo
        self.todos_changed.emit()

    def _on_todo_deleted(self, p):
        self.todos.pop(p['todo_id'], None)
        self.todos_changed.emit()

    def _on_todo_list(self, p):
        self.todos = {t['todo_id']: t for t in p.get('todos', [])}
        self.todos_changed.emit()

    def _on_attachment_data(self, p):
        data = base64.b64decode(p.get('data_b64') or '')
        aid = p['attachment_id']
        self.attachments[aid] = (data, p.get('name', ''), p.get('mime', ''))
        for name, user in self.users.items():
            if user.get('avatar_id') == aid:
                self.avatars[name] = (aid, data)
                self.avatar_changed.emit(name)
        self.attachment_loaded.emit(aid, data, p.get('name', ''), p.get('mime', ''))

    def _on_rag_doc_list(self, p):
        self.rag_docs = list(p.get('docs', []))
        self.rag_docs_changed.emit(list(self.rag_docs))

    def _on_rag_doc_added(self, p):
        doc = p['doc']
        self.rag_docs = [d for d in self.rag_docs if d['doc_id'] != doc['doc_id']] + [doc]
        self.rag_docs_changed.emit(list(self.rag_docs))

    def _on_rag_doc_deleted(self, p):
        self.rag_docs = [d for d in self.rag_docs if d['doc_id'] != p['doc_id']]
        self.rag_docs_changed.emit(list(self.rag_docs))

    def _sync_avatar(self, username):
        """사용자의 avatar_id가 바뀌었으면 사진을 요청하거나 캐시를 비운다"""
        aid = self.users.get(username, {}).get('avatar_id')
        cached = self.avatars.get(username)
        if not aid:
            if cached:
                del self.avatars[username]
                self.avatar_changed.emit(username)
            return
        if cached and cached[0] == aid:
            return
        if aid in self.attachments:
            self.avatars[username] = (aid, self.attachments[aid][0])
            self.avatar_changed.emit(username)
        else:
            self._send('attachment_get', attachment_id=aid)

    def _on_scheduled_ok(self, p):
        self.scheduled_ok.emit(p)

    # ================= 관리자 (명세 §4.2) =================
    def is_admin(self):
        return bool(self.me and self.users.get(self.me, {}).get('is_admin'))

    def admin_company(self):
        """관리 범위: '' = 그룹 관리자(모든 계열사), '회사' = 회사 관리자 (명세 §4.3)"""
        return (self.users.get(self.me, {}).get('admin_company') or '') if self.is_admin() else ''

    def admin_member_create(self, username, fields):
        self._send('admin_member_create', username=username, **fields)

    def admin_member_update(self, username, fields):
        self._send('admin_member_update', username=username, **fields)

    def admin_member_delete(self, username):
        self._send('admin_member_delete', username=username)

    def admin_member_restore(self, username):
        self._send('admin_member_restore', username=username)

    def _on_user_removed(self, p):
        """관리자가 구성원을 삭제: 사용자 목록·조직도·친구 목록에서 뺀다 (대화 기록의 이름은 그대로)"""
        name = p.get('username')
        if name in self.users:
            del self.users[name]
            self.avatars.pop(name, None)
            self.user_changed.emit(name)
            self.users_changed.emit()
            self.rooms_changed.emit()

    def admin_request_users(self):
        self._send('admin_users')

    def admin_reset_password(self, username, new_password):
        self._send('admin_reset_password', username=username, new_password=new_password)

    def admin_reset_code(self, username):
        """비밀번호 재설정 코드 발급 → admin_done {action:'reset_code', code, expires_at} (명세 §4.1)"""
        self._send('admin_reset_code', username=username)

    def admin_clear_password(self, username):
        self._send('admin_clear_password', username=username)

    def admin_set_disabled(self, username, disabled):
        self._send('admin_set_disabled', username=username, disabled=bool(disabled))

    def admin_set_admin(self, username, is_admin, company=''):
        """company 가 있으면 그 회사의 회사 관리자, 없으면 그룹 관리자"""
        extra = {'company': company} if is_admin and company else {}
        self._send('admin_set_admin', username=username, is_admin=bool(is_admin), **extra)

    def is_group_admin(self):
        """그룹 관리자(모든 계열사) - 조직도 그룹 만들기 · 수정 · 삭제 (명세 §4.4)"""
        return self.is_admin() and not self.admin_company()

    def admin_group_create(self, name, description='', members=()):
        self._send('admin_group_create', name=name, description=description, members=list(members))

    def admin_group_update(self, group_id, name, description='', members=()):
        self._send('admin_group_update', group_id=group_id, name=name, description=description,
                   members=list(members))

    def admin_group_reorder(self, group_ids):
        """조직도 그룹 순서 변경 (끌어서 놓기). 화면은 바로 바꾸고, 서버가 모두에게 org_groups 로 확정한다"""
        group_ids = [int(g) for g in group_ids]
        by_id = {g.get('group_id'): g for g in (self.org or {}).get('groups') or []}
        if set(group_ids) == set(by_id):
            self.org = dict(self.org, groups=[by_id[g] for g in group_ids])
            self.org_changed.emit()
        self._send('admin_group_reorder', group_ids=group_ids)

    def admin_group_delete(self, group_id):
        self._send('admin_group_delete', group_id=group_id)

    def admin_org_order(self, order):
        """조직도 아래쪽 덩어리 순서 ["group:1", "company:게스트"] (그룹 관리자, 끌어서 놓기).
        화면은 바로 바꾸고, 서버가 모두에게 org_groups 로 확정한다"""
        order = [str(t) for t in order]
        self.org = dict(self.org or {}, order=order)
        self.org_changed.emit()
        self._send('admin_org_order', order=order)

    def _on_org_groups(self, p):
        self.org = dict(self.org or {}, groups=list(p.get('groups') or []))
        if 'order' in p:
            self.org['order'] = list(p.get('order') or [])
        self.org_changed.emit()

    def admin_send_notice(self, content):
        self._send('admin_notice', content=content)

    def _on_admin_users(self, p):
        self.admin_scope = p.get('scope') or ''
        self.admin_users = list(p.get('users', []))
        self.admin_users_changed.emit(list(self.admin_users))

    def _on_admin_done(self, p):
        self.admin_done.emit(dict(p))

    def _on_admin_notice(self, p):
        self.admin_notice.emit(dict(p))

    def _on_account_disabled(self, p):
        self.account_disabled.emit(p.get('message') or '사용이 중지된 계정입니다.')

    def revoke_login_token(self, token):
        """로그아웃: 저장된 자동 로그인 토큰을 서버에서 폐기"""
        if token and self.conn is not None:
            self.conn.send('login_token_revoke', token=token)

    ROOM_OPEN_REQUESTS = ('room_open_direct', 'room_create', 'room_open_secret', 'open_room_join', 'open_room_create')

    def _on_error(self, p):
        if p.get('req_type') in self.ROOM_OPEN_REQUESTS:
            # 방 열기가 거절되면 기다리던 표시를 지운다 - 남아 있으면 나중에 관계없는 room_updated 가 창을 띄웠다
            self._pending_direct.clear()
            self._pending_groups.clear()
        code = p.get('code') if isinstance(p.get('code'), str) else ''
        if code == 'session_expired':          # 비밀번호가 바뀌었다 (다른 기기 · 재설정 · 관리자) - 서버가 곧 연결을 끊는다
            self.login_error = {'code': code, 'retry_after': 0}
            self.session_expired.emit(p.get('message') or '비밀번호가 바뀌어 다시 로그인해야 합니다.')
            return
        if p.get('req_type') in ('join', 'register'):
            try:
                retry_after = max(0, int(p.get('retry_after') or 0))
            except (TypeError, ValueError):
                retry_after = 0
            self.login_error = {'code': code, 'retry_after': retry_after}
            self.login_failed.emit(p.get('message', '로그인할 수 없습니다.'))
            return
        if p.get('req_type') == 'password_change':
            self._new_password = None
            self.password_change_failed.emit(p.get('message', '비밀번호를 바꾸지 못했습니다.'), code)
            return
        if p.get('req_type') == 'attachment_get':
            quiet = self.quiet_attachment_errors   # 먼저 읽는다: 신호를 받은 저장 작업이 마지막 첨부면 바로 끝내며 끈다
            self.attachment_failed.emit(p.get('message', '첨부 파일을 받을 수 없습니다.'))
            if quiet:
                return
        self.error.emit(p.get('message', '알 수 없는 오류'))

    # ================= 서버 정책 · 내 비밀번호 =================
    def email_editable(self):
        """프로필에서 이메일을 직접 바꿀 수 있는가 (서버가 막았을 때만 False - 예전 서버는 정책이 없어 True)"""
        return self.policy.get('email_editable') is not False

    def display_name_editable(self):
        """표시 이름을 직접 바꿀 수 있는가 (실명으로 고정한 서버만 False - 예전 서버는 정책이 없어 True)"""
        return self.policy.get('display_name_editable') is not False

    def change_password(self, old_password, new_password, remember=False):
        """내 비밀번호 변경 요청 → password_changed 또는 password_change_failed. 요청을 보냈으면 True"""
        payload = {'old_password': old_password, 'new_password': new_password}
        if remember:
            payload['remember'] = True          # 저장된 로그인이 있으면 새 자동 로그인 토큰을 받는다
        if self._send('password_change', **payload) is None:
            return False
        self._new_password = new_password
        return True

    def _on_password_changed(self, p):
        if p.get('login_token'):
            self.login_token = p['login_token']
        if self._new_password:
            self.session_password = self._new_password
        self._new_password = None
        self.password_changed.emit(dict(p))

    # ================= 조회 =================
    def user(self, username):
        return self.users.get(username) or {
            'username': username, 'display_name': username,
            'status_message': '', 'birthday': None, 'online': False}

    def display_name(self, username):
        if not username:
            return ''
        return self.user(username).get('display_name') or username

    def is_online(self, username):
        return bool(self.user(username).get('online'))

    def presence_of(self, username):
        user = self.user(username)
        if user.get('presence') == 'vacation':
            return 'vacation'   # 휴가 중은 접속하지 않아도 보인다 (명세 §7.4)
        if not user.get('online'):
            return 'offline'
        return user.get('presence') or 'online'

    def avatar_bytes(self, username):
        cached = self.avatars.get(username)
        return cached[1] if cached else None

    def is_bot(self, username):
        return bool(self.user(username).get('is_bot'))

    def company_of(self, username):
        return self.user(username).get('company') or ''

    def is_external(self, username):
        """나와 다른 그룹사 소속인지"""
        mine = self.company_of(self.me)
        return bool(mine) and self.company_of(username) not in ('', mine)

    def friends(self, include_bots=False, include_hidden=False):
        """나를 제외한 사용자 (이름순). 숨김·차단한 친구는 기본적으로 제외"""
        return sorted((u for name, u in self.users.items()
                       if name != self.me and (include_bots or not u.get('is_bot'))
                       and (include_hidden or (not self.is_hidden(name) and name not in self.blocked))),
                      key=lambda u: self.display_name(u['username']))

    def birthday_friends(self, on=None):
        on = on or date.today()
        key = on.strftime('%m-%d')
        return [u for u in self.friends() if birthday_md(u.get('birthday')) == key]

    def room(self, room_id):
        return self.rooms.get(room_id)

    def room_title(self, room_id):
        room = self.rooms.get(room_id)
        if room is None:
            return ''
        if room.get('name'):
            return room['name']
        if room['type'] == 'self':
            return self.display_name(self.me)
        if room['type'] == 'secret':
            others = [m for m in room.get('members', []) if m != self.me]
            return '🔒 ' + (self.display_name(others[0]) if others else '비밀채팅')
        others = [m for m in room.get('members', []) if m != self.me]
        if not others:
            return '대화상대 없음'
        names = ', '.join(self.display_name(m) for m in others)
        return names

    def room_member_count(self, room_id):
        room = self.rooms.get(room_id)
        return len(room.get('members', [])) if room else 0

    def direct_room_with(self, username):
        for room in self.rooms.values():
            members = set(room.get('members', []))
            if username == self.me and room['type'] == 'self':
                return room['room_id']
            if room['type'] == 'direct' and members == {self.me, username}:
                return room['room_id']
        return None

    # ---- 채팅방 폴더 (카카오톡 2025-09: 안읽음/즐겨찾기/직접 만든 폴더) ----
    BUILTIN_FOLDERS = ('전체', '안읽음', '즐겨찾기', '통화')   # '통화': 카카오톡 v26.4.0 통화 기록 폴더

    def chat_folders(self):
        """[(이름, 방 개수)] 기본 폴더 + 사용자 폴더"""
        custom = [f['name'] for f in (self.settings.get('chat_folders') if self.settings else []) or []]
        return [(name, self.missed_call_count() if name == '통화' else len(self.rooms_in_folder(name)))
                for name in list(self.BUILTIN_FOLDERS) + custom]

    def rooms_in_folder(self, name):
        rooms = self.sorted_rooms()
        if name in ('', '전체'):
            return rooms
        if name == '안읽음':
            return [r for r in rooms if self.room_unread(r['room_id']) > 0]
        if name == '통화':   # 최근 통화 순, 방마다 한 번
            seen = []
            for c in self.call_history:
                if c['room_id'] in self.rooms and c['room_id'] not in seen:
                    seen.append(c['room_id'])
            return [self.rooms[rid] for rid in seen]
        if name == '즐겨찾기':
            def fav(room):
                if self.is_pinned(room['room_id']):
                    return True
                others = [m for m in room.get('members', []) if m != self.me]
                return room['type'] == 'direct' and len(others) == 1 and self.is_favorite(others[0])
            return [r for r in rooms if fav(r)]
        for folder in (self.settings.get('chat_folders') if self.settings else []) or []:
            if folder.get('name') == name:
                ids = set(folder.get('room_ids') or [])
                return [r for r in rooms if r['room_id'] in ids]
        return []

    def save_chat_folder(self, name, room_ids, old_name=None):
        """사용자 폴더 생성/수정. 기본 폴더 이름은 쓸 수 없다. 오류 메시지 또는 None"""
        name = (name or '').strip()
        if not name:
            return '폴더 이름을 입력해 주세요.'
        if name in self.BUILTIN_FOLDERS:
            return f"'{name}'은(는) 기본 폴더 이름입니다."
        folders = [dict(f) for f in (self.settings.get('chat_folders') or [])]
        if any(f['name'] == name for f in folders) and name != old_name:
            return '같은 이름의 폴더가 있습니다.'
        entry = {'name': name, 'room_ids': list(dict.fromkeys(room_ids))}
        for i, f in enumerate(folders):
            if f['name'] == (old_name or name):
                folders[i] = entry
                break
        else:
            folders.append(entry)
        self.settings.set('chat_folders', folders)
        return None

    def delete_chat_folder(self, name):
        folders = [f for f in (self.settings.get('chat_folders') or []) if f.get('name') != name]
        self.settings.set('chat_folders', folders)

    def toggle_room_in_folder(self, name, room_id):
        folders = [dict(f) for f in (self.settings.get('chat_folders') or [])]
        for f in folders:
            if f['name'] == name:
                ids = list(f.get('room_ids') or [])
                if room_id in ids:
                    ids.remove(room_id)
                else:
                    ids.append(room_id)
                f['room_ids'] = ids
        self.settings.set('chat_folders', folders)

    def peek_messages(self, room_id, count=5):
        """읽지 않고 미리보기: 안 읽은 최근 메시지(읽음 처리 요청을 보내지 않음)"""
        room = self.rooms.get(room_id) or {}
        mine = room.get('read_pointers', {}).get(self.me, 0)
        msgs = [m for m in self.room_messages(room_id)
                if m['msg_id'] > mine and m['sender'] != self.me and m['kind'] != 'system'
                and not m.get('deleted')]
        if not msgs and room.get('last_message') and room['last_message'].get('sender') != self.me:
            msgs = [room['last_message']]
        return msgs[-count:]

    def events_in_room(self, room_id):
        """채팅방 사이드(서랍) 일정 모아보기"""
        return sorted((e for e in self.events.values() if e.get('room_id') == room_id),
                      key=lambda e: e['start'])

    def sorted_rooms(self):
        """고정된 방 먼저, 그 다음 최근 메시지 순"""
        pinned = set(self.settings.get('pinned_rooms')) if self.settings else set()

        def key(room):
            last = room.get('last_message') or {}
            ts = last.get('timestamp') or room.get('created_at') or ''
            return (room['room_id'] in pinned, ts)
        return sorted(self.rooms.values(), key=key, reverse=True)

    def room_messages(self, room_id):
        """로드된 메시지(오래된→최신, 나에게서 삭제한 것 제외)"""
        hidden = set(self.settings.get('hidden_messages')) if self.settings else set()
        store = self._messages.get(room_id, {})
        # 보내지 못한 메시지는 서버 메시지 저장소와 따로 두고, 화면에는 항상 맨 아래에 붙인다
        return [store[k] for k in sorted(store) if k not in hidden] + self.failed_messages(room_id)

    def timeline_messages(self, room_id):
        """대화창 본문에 보이는 메시지 - 스레드 답글은 원글 아래 '답글 N개'로만 보인다 (§10.4)"""
        return [m for m in self.room_messages(room_id) if m.get('thread_root') is None]

    def thread_messages(self, room_id, root_id):
        """받아 둔 스레드 답글 (오래된 순, 나에게서 삭제한 것 제외)"""
        return [m for m in self.room_messages(room_id) if m.get('thread_root') == root_id]

    def request_thread(self, root_id):
        return self._send('thread_get', root_id=root_id) is not None

    def _on_thread(self, p):
        rid = p['room_id']
        store = self._messages.setdefault(rid, {})
        for m in [p['root']] + list(p.get('messages', [])):
            self._decrypt(m)
            store[m['msg_id']] = m
        self.thread_loaded.emit(rid, int(p['root_id']))

    def find_message(self, room_id, msg_id):
        found = self._messages.get(room_id, {}).get(msg_id)
        if found is None and msg_id >= FAILED_ID_BASE:
            found = next((m for m in self.failed_messages(room_id) if m['msg_id'] == msg_id), None)
        return found

    def message_unread(self, room_id, message):
        """메시지 옆에 표시할 '안 읽은 사람 수'"""
        if message.get('kind') == 'system' or message.get('failed'):
            return 0
        room = self.rooms.get(room_id)
        if room is None:
            return 0
        pointers = room.get('read_pointers', {})
        return sum(1 for u in room.get('members', []) if pointers.get(u, 0) < message['msg_id'])

    def unread_members(self, room_id, message):
        """이 메시지를 아직 읽지 않은 사람 (이름순) - 읽음 숫자를 누르면 보인다 (§10.6)"""
        if message.get('kind') == 'system' or message.get('failed'):
            return []
        room = self.rooms.get(room_id) or {}
        pointers = room.get('read_pointers', {})
        names = [u for u in room.get('members', []) if pointers.get(u, 0) < message['msg_id']]
        return sorted(names, key=self.display_name)

    # ================= v5: 입력 임시저장 (§10.8, 클라이언트 전용) =================
    def room_draft(self, room_id):
        drafts = (self.settings.get('room_drafts') if self.settings else None) or {}
        return drafts.get(room_id, '')

    def set_room_draft(self, room_id, text):
        """쓰다 만 글 저장 (공백뿐이면 지움). 바뀌었으면 True"""
        if not self.settings:
            return False
        drafts = self.settings.get('room_drafts') or {}
        text = text if (text or '').strip() else ''
        if drafts.get(room_id, '') == text:
            return False
        if text:
            drafts[room_id] = text[:4000]
        else:
            drafts.pop(room_id, None)
        self.settings.set('room_drafts', drafts)
        self.rooms_changed.emit()
        return True

    # ================= v5: 멘션 모아보기 (§10.5) =================
    def request_mentions(self, limit=100):
        return self._send('mention_list', limit=limit) is not None

    def _on_mention_list(self, p):
        msgs = list(p.get('messages', []))
        for m in msgs:
            self._decrypt(m)
        self.mentions = msgs
        self.mentions_changed.emit()

    def _note_mention(self, m):
        """새로 받은 메시지가 나를 멘션하면 목록 맨 앞에 넣는다"""
        if m.get('sender') != self.me and self.me in (m.get('mentions') or []) \
                and all(x['msg_id'] != m['msg_id'] for x in self.mentions):
            self.mentions.insert(0, m)
            self.mentions_changed.emit()

    # ================= v5: 책갈피 (§10.7, 클라이언트 전용) =================
    SAVED_MAX = 200

    def saved_messages(self):
        """저장한 메시지 (최근에 저장한 것부터)"""
        return list(self.settings.get('saved_messages') or []) if self.settings else []

    def is_saved(self, room_id, msg_id):
        return any(x.get('room_id') == room_id and x.get('msg_id') == msg_id for x in self.saved_messages())

    def save_message(self, m):
        if not self.settings or m.get('failed') or m.get('kind') == 'system' or self.is_saved(m['room_id'], m['msg_id']):
            return False
        entry = {'room_id': m['room_id'], 'msg_id': m['msg_id'], 'sender': m.get('sender', ''),
                 'thread_root': m.get('thread_root'),
                 'preview': self.message_preview(m)[:200], 'timestamp': m.get('timestamp', ''),
                 'saved_at': datetime.now().isoformat(timespec='seconds')}
        self.settings.set('saved_messages', ([entry] + self.saved_messages())[:self.SAVED_MAX])
        self.saved_changed.emit()
        return True

    def unsave_message(self, room_id, msg_id):
        items = [x for x in self.saved_messages() if not (x.get('room_id') == room_id and x.get('msg_id') == msg_id)]
        if self.settings:
            self.settings.set('saved_messages', items)
        self.saved_changed.emit()

    def room_unread(self, room_id):
        room = self.rooms.get(room_id)
        return int(room.get('unread_count', 0)) if room else 0

    def total_unread(self):
        return sum(self.room_unread(rid) for rid in self.rooms if not self.is_muted(rid))

    def is_muted(self, room_id):
        return bool(self.settings and self.settings.contains('muted_rooms', room_id))

    def is_pinned(self, room_id):
        return bool(self.settings and self.settings.contains('pinned_rooms', room_id))

    def is_favorite(self, username):
        return bool(self.settings and self.settings.contains('favorites', username))

    def message_preview(self, m):
        if not m:
            return ''
        if m.get('thread_root') is not None:   # 스레드 답글 (§10.4)
            return '↳ ' + self.message_preview(dict(m, thread_root=None))
        if m.get('deleted'):
            return '메시지가 삭제되었습니다'
        kind = m.get('kind')
        if kind == 'image':
            return '사진'
        if kind == 'file':
            return f"파일: {(m.get('attachment') or {}).get('name', '')}"
        if kind == 'emoticon':
            return f"이모티콘 {m.get('content', '')}"
        if kind == 'poll':
            return f"투표: {(m.get('poll') or {}).get('question', '')}"
        if kind == 'event':
            return f"일정: {m.get('content', '')}"
        if kind == 'secret':
            return '🔒 비밀채팅 메시지'
        if kind == 'call':
            return {'voice': '📞 보이스톡', 'video': '📹 페이스톡', 'voiceroom': '🎙 보이스룸'}.get(
                m.get('content'), '📞 통화')
        if kind == 'ladder':
            return '🪜 사다리 게임'
        if kind == 'voice':
            return f"음성메시지 {fmt_duration(m.get('content'))}"
        if kind == 'contact':
            return f"연락처: {self.display_name(m.get('content', ''))}"
        if kind == 'board':
            return f"📌 게시판: {m.get('content', '')}"
        return m.get('content', '')

    def drawer_items(self, room_id):
        """채팅방 서랍: (사진, 파일, 링크) 리스트"""
        photos, files, links = [], [], []
        for m in self.room_messages(room_id):
            if m.get('deleted'):
                continue
            if m['kind'] == 'image':
                photos.append(m)
            elif m['kind'] == 'file':
                files.append(m)
            elif m['kind'] == 'text':
                for url in URL_RE.findall(m.get('content', '')):
                    links.append((url, m))
        return photos, files, links

    def events_between(self, start, end):
        """[start, end) 기간과 겹치는 일정, 시작 시각 순"""
        result = []
        for ev in self.events.values():
            s, e = parse_dt(ev['start']), parse_dt(ev['end'])
            if s is None:
                continue
            e = e or s
            if s < end and e >= start:
                result.append(ev)
        return sorted(result, key=lambda ev: ev['start'])

    def current_event_of(self, username, now=None):
        """username 이 참석(또는 주최)하는, 지금 진행 중인 일정 (종일 일정 제외). 없으면 None"""
        now = now or datetime.now()
        for ev in self.events.values():
            if ev.get('all_day'):
                continue
            s, e = parse_dt(ev['start']), parse_dt(ev['end'])
            if not s or not e or not (s <= now < e):
                continue
            rsvp = (ev.get('attendees') or {}).get(username)
            if ev.get('creator') == username or rsvp in ('yes', 'maybe', 'pending'):
                return ev
        return None

    def team_of(self, username):
        """(회사, 부서) - 조직 경로 첫 단계를 부서로 본다"""
        u = self.user(username)
        path = u.get('org_path') or ([u['department']] if u.get('department') else [])
        return (u.get('company') or '', path[0] if path else '')

    def events_on(self, day):
        start = datetime(day.year, day.month, day.day)
        return self.events_between(start, start + timedelta(days=1))

    def my_rsvp(self, event):
        return (event.get('attendees') or {}).get(self.me)

    def sorted_todos(self):
        return sorted(self.todos.values(),
                      key=lambda t: (t.get('done', False), t.get('due') or '9999', t['todo_id']))

    # ================= 액션 =================
    def _send(self, msg_type, **payload):
        if self.conn is None:
            return None
        return self.conn.send(msg_type, **payload)

    def open_direct(self, username):
        """1:1(또는 나와의) 채팅방 열기. 이미 있으면 바로 room_opened"""
        existing = self.direct_room_with(username)
        if existing:
            self.room_opened.emit(existing)
            return
        self._pending_direct.add(username)
        self._send('room_open_direct', target=username)

    def create_group(self, members, name=''):
        members = [m for m in members if m != self.me]
        self._pending_groups.append(set(members) | {self.me})
        self._send('room_create', name=name, members=members)

    def invitable_users(self, room_id):
        """초대 가능한 사용자(방에 없는 사람, AI 비서 포함) - 이름순"""
        room = self.rooms.get(room_id) or {}
        members = set(room.get('members', []))
        return [u for u in self.friends(include_bots=True) if u['username'] not in members]

    def invite(self, room_id, usernames):
        """대화상대 초대. 그룹방이면 그 방에 추가하고,
        1:1/나와의 채팅이면 카카오톡처럼 기존 멤버 + 초대한 사람으로 새 그룹방을 만든다
        (새 방은 만들어지면 room_opened로 열린다). 새 방을 만들면 True."""
        room = self.rooms.get(room_id)
        usernames = [u for u in usernames if room is None or u not in room.get('members', [])]
        if not usernames or room is None:
            return False
        if room['type'] == 'group':
            self._send('room_invite', room_id=room_id, usernames=list(usernames))
            return False
        self.create_group(list(room.get('members', [])) + list(usernames))
        return True

    def leave_room(self, room_id):
        self._send('room_leave', room_id=room_id)

    def rename_room(self, room_id, name):
        self._send('room_rename', room_id=room_id, name=name)

    def room_owner(self, room_id):
        """방장 (§10.3) - group/open 방만, 없으면 None"""
        return (self.rooms.get(room_id) or {}).get('owner')

    def kick_member(self, room_id, username):
        self._send('room_kick', room_id=room_id, username=username)

    def transfer_owner(self, room_id, username):
        self._send('room_owner_set', room_id=room_id, username=username)

    def load_history(self, room_id, older=False, limit=50):
        """처음 열 때 한 번 최신 메시지 로드, older=True면 더 이전 메시지"""
        if older:
            loaded = self._messages.get(room_id) or {}
            if not loaded or not self.has_more.get(room_id, True):
                return False
            before = self._history_oldest.get(room_id) or min(loaded)
            return self._send('history', room_id=room_id, before_msg_id=before, limit=limit) is not None
        if room_id in self.history_requested:
            self.history_loaded.emit(room_id, [], self.has_more.get(room_id, False))
            return True
        if self._send('history', room_id=room_id, limit=limit) is None:
            return False                      # 연결 없음 → 다음에 다시 요청할 수 있게 표시하지 않는다
        self.history_requested.add(room_id)
        return True

    def parse_mentions(self, room_id, text):
        room = self.rooms.get(room_id) or {}
        found = []
        for member in room.get('members', []):
            for name in {member, self.display_name(member)}:
                if f'@{name}' in text and member not in found:
                    found.append(member)
        return found

    def send_text(self, room_id, text, reply_to_id=None, send_at=None, silent=False, thread_root_id=None):
        text = text.strip()
        if not text:
            return
        payload = {'room_id': room_id, 'kind': 'text', 'content': text,
                   'mentions': self.parse_mentions(room_id, text)}
        if reply_to_id:
            payload['reply_to_id'] = reply_to_id
        if thread_root_id:
            payload['thread_root_id'] = thread_root_id
            return self._send('send', **payload) is not None   # 스레드 답글은 실패 보관 없이 창에서 안내
        if send_at:
            payload['send_at'] = send_at.isoformat(timespec='seconds')
        if silent:
            payload['silent'] = True
        if self._send('send', **payload) is None:   # 답장 · 예약 · 조용히 보내기는 다시 보낼 때도 그대로
            options = {k: payload[k] for k in ('reply_to_id', 'send_at', 'silent') if k in payload}
            self._record_failed(room_id, 'text', text, options)

    def send_emoticon(self, room_id, emoticon):
        if self._send('send', room_id=room_id, kind='emoticon', content=emoticon) is None:
            self._record_failed(room_id, 'emoticon', emoticon)

    def send_file(self, room_id, path):
        """파일/사진 전송. 오류 메시지를 돌려주거나 성공 시 None.
        보안 정책(§10.10)에 막히면 FILE_BLOCKED 를 돌려주고 file_blocked 로 승인 요청 창을 띄운다"""
        data, err = read_upload(path)
        if err:
            return err
        name = os.path.basename(path)
        # 사진인지는 이름이 아니라 내용으로 본다 (서버 · 받는 쪽 화면과 같은 기준: PNG · JPEG · GIF · BMP · WEBP)
        photo = sniff_image_mime(data)
        mime = photo or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        return self._send_attachment(room_id, 'image' if photo else 'file', name, mime, data)

    def _send_attachment(self, room_id, kind, name, mime, data):
        from client.kakao.file_guard import FILE_BLOCKED, precheck
        self._file_seq = getattr(self, '_file_seq', 0) + 1
        tmp = f'file-{self._file_seq}'
        self._outgoing_files[tmp] = (room_id, kind, name, mime, data)
        while len(self._outgoing_files) > 8:             # 승인 요청에 쓸 최근 파일만 기억한다
            self._outgoing_files.pop(next(iter(self._outgoing_files)))
        violation = precheck(self.file_policy, name)
        if violation is not None:                         # 이름 · 확장자로 막히면 올리지도 않는다
            self.file_blocked.emit({'room_id': room_id, 'kind': kind, 'name': name, 'size': len(data),
                                    'client_tmp_id': tmp, **violation})
            return FILE_BLOCKED
        sent = self._send('send', room_id=room_id, kind=kind, content=name, client_tmp_id=tmp,
                          attachment=attachment_payload(name, mime, data))
        if sent is None:
            return '서버 연결이 끊겨 파일을 보내지 못했습니다. 연결된 뒤 다시 보내 주세요.'
        return None

    # ---- 파일 전송 보안 (명세 §10.10) ----
    def _on_file_policy(self, p):
        self.file_policy = dict(p.get('policy') or {})
        self.file_policy_changed.emit()

    def _on_file_blocked(self, p):
        self.file_blocked.emit(dict(p))

    def can_request_file(self, client_tmp_id):
        return client_tmp_id in self._outgoing_files

    def request_file_send(self, client_tmp_id, reason):
        """막힌 파일을 그룹 관리자에게 승인 요청 (승인되면 그때 그 방으로 전송된다). 오류 문구 또는 None"""
        item = self._outgoing_files.get(client_tmp_id)
        if item is None:
            return '파일 정보가 없어요. 파일을 다시 골라 보내 주세요.'
        room_id, kind, name, mime, data = item
        sent = self._send('file_send_request', room_id=room_id, kind=kind, content=name, reason=reason,
                          attachment=attachment_payload(name, mime, data))
        if sent is None:
            return '서버 연결이 끊겨 요청하지 못했습니다. 연결된 뒤 다시 요청해 주세요.'
        self._outgoing_files.pop(client_tmp_id, None)
        return None

    def _on_scheduled_failed(self, p):
        """예약 메시지를 서버가 보내지 못함 (방을 나감 · 서버 오류) → 알림 + 토스트"""
        preview = (p.get('content') or '')[:30] if p.get('kind', 'text') == 'text' else '첨부'
        self.notify.emit('예약 메시지를 보내지 못했습니다', f"{preview} · {p.get('reason', '')}", p.get('room_id', ''))
        self.error.emit(p.get('reason') or '예약 메시지를 보내지 못했습니다.')

    def _on_file_request_ok(self, p):
        self.file_request_changed.emit(dict(p.get('request') or {}))

    def _on_file_request_updated(self, p):
        request = dict(p.get('request') or {})
        if request.get('username') == self.me:
            approved = request.get('status') == 'approved'
            note = f" · {request['note']}" if request.get('note') else ''
            self.notify.emit('파일 전송 승인' if approved else '파일 전송 반려',
                             f"'{request.get('name', '')}' " + ('전송이 승인되어 보냈어요.' if approved
                                                                 else f'요청이 반려됐어요{note}'),
                             request.get('room_id', '') if approved else '')
            self.file_request_changed.emit(request)
        if self.is_admin() and not self.admin_company():
            self.admin_file_request_new.emit(request)

    def _on_file_request_new(self, p):
        request = dict(p.get('request') or {})
        self.notify.emit('파일 전송 승인 요청', f"{request.get('username', '')}: {request.get('name', '')}", '')
        self.admin_file_request_new.emit(request)

    def request_my_file_requests(self):
        self._send('file_request_list')

    def _on_file_requests(self, p):
        self.file_requests_loaded.emit(list(p.get('requests') or []))

    def admin_file_policy_set(self, policy):
        self._send('admin_file_policy_set', policy=dict(policy))

    def admin_request_file_requests(self, status=None):
        payload = {'status': status} if status else {}
        self._send('admin_file_requests', **payload)

    def _on_admin_file_requests(self, p):
        self.admin_file_requests_changed.emit(list(p.get('requests') or []))

    def admin_decide_file_request(self, request_id, approve, note='', allow_days=0):
        self._send('admin_file_request_decide', request_id=int(request_id), approve=bool(approve),
                   note=note, allow_days=int(allow_days))

    def admin_request_file_exceptions(self):
        self._send('admin_file_exceptions')

    def _on_admin_file_exceptions(self, p):
        self.admin_file_exceptions_changed.emit(list(p.get('exceptions') or []))

    def admin_add_file_exception(self, username, days, pattern='', sha256='', file_name='', note=''):
        self._send('admin_file_exception_add', username=username, days=int(days), pattern=pattern,
                   sha256=sha256, file_name=file_name, note=note)

    def admin_delete_file_exception(self, exception_id):
        self._send('admin_file_exception_delete', exception_id=int(exception_id))

    def forward(self, message, room_id):
        """메시지 전달: 텍스트/이모티콘은 그대로, 첨부는 데이터가 있으면 재전송"""
        kind = message.get('kind')
        if kind in ('text', 'emoticon', 'contact'):
            self._send('send', room_id=room_id, kind=kind, content=message['content'])
        elif kind in ('image', 'file'):
            att = message.get('attachment') or {}
            cached = self.attachments.get(att.get('attachment_id'))
            if cached:
                data, name, mime = cached
                self._send_attachment(room_id, kind, name, mime, data)
            else:
                self.error.emit('첨부 파일을 먼저 열어본 뒤 전달해 주세요.')

    def mark_read(self, room_id):
        loaded = self._messages.get(room_id) or {}
        room = self.rooms.get(room_id)
        if not loaded or room is None:
            return
        last = max(loaded)
        if room.get('read_pointers', {}).get(self.me, 0) >= last and not room.get('unread_count'):
            return
        self._send('read', room_id=room_id, last_msg_id=last)

    def react(self, msg_id, emoji):
        self._send('react', msg_id=msg_id, emoji=emoji)

    def delete_for_all(self, msg_id):
        self._send('delete', msg_id=msg_id)

    def can_edit(self, message):
        """메시지 수정 가능: 내가 보낸 텍스트, 삭제 안 됨, 24시간 이내 (카카오톡 2025-09)"""
        if message.get('kind') != 'text':
            return False
        return self.can_delete_for_all(message)

    def edit_message(self, msg_id, content):
        content = (content or '').strip()
        if content:
            self._send('edit', msg_id=msg_id, content=content)

    def can_delete_for_all(self, message):
        if message.get('sender') != self.me or message.get('deleted'):
            return False
        ts = parse_dt(message.get('timestamp'))
        return ts is not None and datetime.now() - ts < timedelta(hours=24)

    def hide_for_me(self, room_id, msg_id):
        if self.settings and not self.settings.contains('hidden_messages', msg_id):
            self.settings.toggle_in('hidden_messages', msg_id)
        m = self.find_message(room_id, msg_id)
        if m:
            self.message_updated.emit(room_id, m)

    def set_notice(self, room_id, msg_id):
        self._send('notice_set', room_id=room_id, msg_id=msg_id)

    def search(self, query, room_id=None):
        payload = {'query': query}
        if room_id:
            payload['room_id'] = room_id
        self._send('search', **payload)

    def create_poll(self, room_id, question, options, multiple=False):
        self._send('poll_create', room_id=room_id, question=question,
                   options=list(options), multiple=bool(multiple))

    def vote_poll(self, poll_id, option_indexes):
        self._send('poll_vote', poll_id=poll_id, option_indexes=list(option_indexes))

    def close_poll(self, poll_id):
        self._send('poll_close', poll_id=poll_id)

    @staticmethod
    def _event_payload(fields):
        payload = {}
        for key, value in fields.items():
            if isinstance(value, datetime):
                value = value.isoformat(timespec='seconds')
            payload[key] = value
        return payload

    def create_event(self, **fields):
        self._send('event_create', **self._event_payload(fields))

    def update_event(self, event_id, **fields):
        self._send('event_update', event_id=event_id, **self._event_payload(fields))

    def delete_event(self, event_id):
        self._send('event_delete', event_id=event_id)

    def rsvp(self, event_id, response):
        self._send('event_rsvp', event_id=event_id, response=response)

    def request_events(self, start, end):
        self._send('event_list', start=start.isoformat(timespec='seconds'),
                   end=end.isoformat(timespec='seconds'))

    def create_todo(self, title, due=None):
        payload = {'title': title}
        if due:
            payload['due'] = due.isoformat() if isinstance(due, date) else due
        self._send('todo_create', **payload)

    def update_todo(self, todo_id, **fields):
        if isinstance(fields.get('due'), date):
            fields['due'] = fields['due'].isoformat()
        self._send('todo_update', todo_id=todo_id, **fields)

    def delete_todo(self, todo_id):
        self._send('todo_delete', todo_id=todo_id)

    def request_todos(self):
        self._send('todo_list')

    def update_profile(self, **fields):
        self._send('profile_update', **fields)

    def set_presence(self, presence):
        if presence in PRESENCES:
            self._send('presence_set', presence=presence)

    def request_rag_docs(self):
        self._send('rag_doc_list')

    def add_rag_doc(self, path):
        """AI 지식베이스에 문서 추가. 오류 메시지 또는 None"""
        data, err = read_upload(path, '10MB 이하의 문서만 추가할 수 있습니다.')
        if err:
            return err
        self._send('rag_doc_add', name=os.path.basename(path),
                   data_b64=base64.b64encode(data).decode('ascii'))
        return None

    def delete_rag_doc(self, doc_id):
        self._send('rag_doc_delete', doc_id=doc_id)

    # ================= v3: 알림 규칙 =================
    def room_alert_mode(self, room_id):
        """'all' | 'mention' | 'off' (off는 알림 끄기와 같다)"""
        if self.is_muted(room_id):
            return 'off'
        modes = (self.settings.get('room_alert_modes') if self.settings else None) or {}
        return modes.get(room_id, 'all')

    def set_room_alert_mode(self, room_id, mode):
        if not self.settings or mode not in ('all', 'mention', 'off'):
            return
        modes = self.settings.get('room_alert_modes') or {}
        modes[room_id] = mode
        self.settings.set('room_alert_modes', modes)
        if (mode == 'off') != self.is_muted(room_id):
            self.settings.toggle_in('muted_rooms', room_id)

    def keyword_hit(self, message):
        """키워드 알림에 걸린 키워드 (없으면 '')"""
        if not self.settings or message.get('kind') != 'text':
            return ''
        text = (message.get('content') or '').lower()
        for kw in self.settings.get('alert_keywords') or []:
            if kw and kw.lower() in text:
                return kw
        return ''

    def should_alert(self, room_id, message):
        if message.get('silent'):
            return False   # 조용히 보내기: 알림 없이 안 읽은 수에만 반영
        if self.settings is not None:
            from client.kakao import dnd
            if dnd.suppresses(self.settings, message.get('sender')):
                return False   # 방해금지 (§10.1): VIP 만 통과, 키워드·멘션도 막는다
        if self.keyword_hit(message):
            return True    # 키워드 알림은 알림 끈 방에서도 울린다
        mode = self.room_alert_mode(room_id)
        if mode == 'off':
            return False
        if mode == 'mention':
            return self.me in (message.get('mentions') or [])
        return True

    # ================= v3: 비밀채팅 (§8.1) =================
    def _init_keyring(self):
        try:
            from client.kakao.secret import SecretKeyring, PeerKeys
            self.keyring = SecretKeyring(self.me)
            self.peer_keys = PeerKeys(self.me)
        except Exception:
            self.keyring = None
            self.peer_keys = None
            return
        if self.user(self.me).get('public_key') != self.keyring.public_key_b64:
            self._send('key_publish', public_key_b64=self.keyring.public_key_b64)

    def secret_peer(self, room_id):
        room = self.rooms.get(room_id) or {}
        others = [m for m in room.get('members', []) if m != self.me]
        return others[0] if others else None

    def _decrypt(self, m):
        if m.get('kind') != 'secret' or m.get('deleted') or 'secret_text' in m:
            return
        text = None
        if self.keyring:
            for key in self._secret_read_keys(self.secret_peer(m['room_id'])):
                text = self.keyring.decrypt(m['room_id'], key, m.get('content', ''))
                if text is not None:
                    break
        if text is not None:
            m['secret_text'] = text
        elif self.secret_key_changed(m['room_id']):
            m['secret_text'] = '🔒 상대의 암호 키가 바뀌었습니다. 채팅방 위의 안내에서 확인하면 읽을 수 있습니다'
        else:
            m['secret_text'] = '🔒 이 기기에서는 읽을 수 없는 비밀 메시지입니다'
        m['secret_ok'] = text is not None

    def _peer_public_key(self, peer):
        return self.user(peer).get('public_key') if peer else None

    def _secret_read_keys(self, peer):
        """받은 비밀 메시지를 풀 때 써 볼 상대 공개키: 지금 믿는 키부터, 예전에 믿었던 키까지.
        처음 보는 상대의 키는 이때 기억한다. 믿는 키와 다른 새 키는 확인 전에는 쓰지 않는다"""
        key = self._peer_public_key(peer)
        if self.peer_keys is None:
            return [key] if key else []
        if key and self.peer_keys.status(peer, key) == 'new':
            self.peer_keys.remember(peer, key)
        return list(reversed(self.peer_keys.trusted(peer)))

    def secret_key_changed(self, room_id):
        """상대 공개키가 내가 믿는 키와 달라졌는가 (다른 PC 에서 접속 · 키 재발급, 또는 누군가 키를 바꿔치기)"""
        peer = self.secret_peer(room_id)
        key = self._peer_public_key(peer)
        return bool(key and self.peer_keys is not None and self.peer_keys.status(peer, key) == 'changed')

    def secret_fingerprint(self, room_id):
        """이 비밀채팅의 확인 번호 (나와 상대의 지금 키로 만든다, 키가 없으면 '')"""
        key = self._peer_public_key(self.secret_peer(room_id))
        if not (self.keyring and key):
            return ''
        from client.kakao.secret import fingerprint
        try:
            return fingerprint(self.keyring.public_key_b64, key)
        except ValueError:
            return ''

    def trust_secret_key(self, room_id):
        """사용자가 확인함: 상대의 새 키를 믿는다. 그 키로 와 있던 메시지를 다시 푼다"""
        peer = self.secret_peer(room_id)
        key = self._peer_public_key(peer)
        if not (key and self.peer_keys is not None):
            return
        self.peer_keys.remember(peer, key)
        for m in (self._messages.get(room_id) or {}).values():
            if m.get('kind') == 'secret' and not m.get('secret_ok') and not m.get('deleted'):
                m.pop('secret_text', None)
                self._decrypt(m)
                self.message_updated.emit(room_id, m)
        self.room_changed.emit(room_id)
        self.rooms_changed.emit()

    def open_secret(self, username):
        for room in self.rooms.values():
            if room['type'] == 'secret' and set(room.get('members', [])) == {self.me, username}:
                self.room_opened.emit(room['room_id'])
                return
        self._pending_direct.add(username)
        self._send('room_open_secret', target=username)

    def send_secret(self, room_id, text):
        """비밀채팅 전송. 오류 메시지 또는 None"""
        text = (text or '').strip()
        if not text:
            return None
        peer = self.secret_peer(room_id)
        key = self._peer_public_key(peer)
        if not (self.keyring and key):
            return '상대방이 아직 비밀채팅을 사용할 수 없습니다.'
        if self.peer_keys is not None:
            if self.peer_keys.status(peer, key) == 'new':
                self.peer_keys.remember(peer, key)
            elif self.secret_key_changed(room_id):
                return '상대의 암호 키가 바뀌었습니다. 채팅방 위의 안내에서 확인한 뒤 보내 주세요.'
        if self._send('send', room_id=room_id, kind='secret', content=self.keyring.encrypt(room_id, key, text)) is None:
            return '서버 연결이 끊겨 비밀 메시지를 보내지 못했습니다. 연결된 뒤 다시 보내 주세요.'
        return None

    # ================= v3: 통화 (§8.2) =================
    def _on_call_updated(self, p):
        call = p['call']
        if call['call_id'] in self._ended_calls:
            return          # 끝난 통화의 예전 상태가 늦게 도착함 - 받아들이면 끝난 통화가 진행 중으로 되살아난다
        self.calls[call['call_id']] = call
        self.calls_changed.emit()

    def _on_call_ended(self, p):
        self._ended_calls.add(p.get('call_id'))
        self.calls.pop(p.get('call_id'), None)
        self.calls_changed.emit()
        self.call_ended.emit(dict(p))
        self.request_call_history()

    def _on_call_list(self, p):
        self.calls = {c['call_id']: c for c in p.get('calls', [])}
        self.calls_changed.emit()

    def _on_call_media(self, p):
        extra = {k: v for k, v in p.items() if k not in ('type', 'call_id', 'from', 'kind', 'seq', 'data_b64')}
        self.call_media.emit(int(p['call_id']), p.get('from', ''), p.get('kind', ''), int(p.get('seq', 0)),
                             p.get('data_b64', ''), extra)

    def call_for_room(self, room_id):
        return next((c for c in self.calls.values() if c.get('room_id') == room_id), None)

    def my_call(self):
        return next((c for c in self.calls.values() if self.me in (c.get('participants') or {})), None)

    def start_call(self, room_id, mode):
        self._send('call_start', room_id=room_id, mode=mode)

    def join_call(self, call_id):
        self._send('call_join', call_id=call_id)

    def leave_call(self, call_id):
        self._send('call_leave', call_id=call_id)

    def raise_hand(self, call_id, raised=True):
        self._send('call_hand', call_id=call_id, raised=bool(raised))

    def set_call_role(self, call_id, username, role):
        self._send('call_role', call_id=call_id, username=username, role=role)

    def set_call_state(self, call_id, **fields):
        self._send('call_state', call_id=call_id, **{k: bool(v) for k, v in fields.items()})

    def send_media(self, call_id, kind, seq, data_b64, **extra):
        self._send('call_media', call_id=call_id, kind=kind, seq=seq, data_b64=data_b64, **extra)

    def request_calls(self):
        self._send('call_list')

    # ================= v3: 펑 (§8.3) =================
    def _on_story_new(self, p):
        story = p['story']
        self.stories[story['story_id']] = story
        self.stories_changed.emit()

    _on_story_updated = _on_story_new

    def _on_story_list(self, p):
        self.stories = {s['story_id']: s for s in p.get('stories', [])}
        self.stories_changed.emit()

    def _on_story_deleted(self, p):
        self.stories.pop(p.get('story_id'), None)
        self.stories_changed.emit()

    def stories_of(self, username):
        return sorted((s for s in self.stories.values() if s['owner'] == username), key=lambda s: s['created_at'])

    def story_owners(self):
        """펑을 올린 사람들 (안 본 펑 있는 사람 먼저)"""
        owners = {s['owner'] for s in self.stories.values()
                  if s['owner'] not in self.blocked and not self.is_story_hidden(s['owner'])}
        return sorted(owners, key=lambda u: (u != self.me, not self.has_unseen_story(u), self.display_name(u)))

    def has_unseen_story(self, username):
        return any(s['story_id'] not in self.seen_stories for s in self.stories_of(username)
                   if username != self.me)

    def post_story(self, kind='text', content='', path=None, bg_color='#2A386C'):
        payload = {'kind': kind, 'content': content, 'bg_color': bg_color}
        if path:
            data, err = read_upload(path, '10MB 이하의 사진만 올릴 수 있습니다.')
            if err:
                return err
            name = os.path.basename(path)
            payload['attachment'] = {'name': name, 'mime': mimetypes.guess_type(name)[0] or 'image/png',
                                     'data_b64': base64.b64encode(data).decode('ascii')}
            payload['kind'] = 'image'
        self._send('story_post', **payload)
        return None

    def view_story(self, story_id):
        if story_id not in self.seen_stories:
            self.seen_stories.add(story_id)
            story = self.stories.get(story_id)
            if story and story['owner'] != self.me:
                self._send('story_view', story_id=story_id)
            self.stories_changed.emit()

    def delete_story(self, story_id):
        self._send('story_delete', story_id=story_id)

    def request_stories(self):
        self._send('story_list')

    # ================= v3: 멀티프로필 (§8.5) =================
    def request_multi_profiles(self):
        self._send('multi_profile_list')

    def set_multi_profiles(self, profiles):
        self._send('multi_profile_set', profiles=list(profiles))

    def _on_multi_profile_list(self, p):
        self.multi_profiles = list(p.get('profiles', []))
        self.multi_profiles_changed.emit(list(self.multi_profiles))

    # ================= v3: 차단 / 숨김 (§8.6) =================
    def block_user(self, username, blocked=True):
        self._send('block_set', username=username, blocked=bool(blocked))

    def _on_block_list(self, p):
        self.blocked = set(p.get('blocked', []))
        self.blocked_changed.emit()
        self.users_changed.emit()
        self.rooms_changed.emit()

    def is_blocked(self, username):
        return username in self.blocked

    def is_hidden(self, username):
        return bool(self.settings and self.settings.contains('hidden_friends', username))

    def set_hidden(self, username, hidden=True):
        if self.settings and self.is_hidden(username) != hidden:
            self.settings.toggle_in('hidden_friends', username)
            self.users_changed.emit()

    # ================= v3: 사다리 / 게시판 / 오픈채팅 / 톡서랍 =================
    def create_ladder(self, room_id, participants, results):
        self._send('ladder_create', room_id=room_id, participants=list(participants), results=list(results))

    def post_board(self, room_id, title, content):
        self._send('board_post', room_id=room_id, title=title, content=content)

    def request_board(self, room_id):
        self._send('board_list', room_id=room_id)

    def comment_board(self, post_id, content):
        self._send('board_comment', post_id=post_id, content=content)

    def delete_board(self, post_id):
        self._send('board_delete', post_id=post_id)

    def board_posts(self, room_id):
        return sorted(self.boards.get(room_id, {}).values(), key=lambda b: b['created_at'], reverse=True)

    def _on_board_list(self, p):
        self.boards[p['room_id']] = {b['post_id']: b for b in p.get('posts', [])}
        self.board_changed.emit(p['room_id'])

    def _on_board_updated(self, p):
        post = p['post']
        self.boards.setdefault(post['room_id'], {})[post['post_id']] = post
        self.board_changed.emit(post['room_id'])

    def _on_board_deleted(self, p):
        self.boards.get(p['room_id'], {}).pop(p['post_id'], None)
        self.board_changed.emit(p['room_id'])

    def create_open_room(self, name, description='', tags=()):
        self._pending_groups.append({self.me})
        self._send('open_room_create', name=name, description=description, tags=list(tags))

    def search_open_rooms(self, query=''):
        self._send('open_room_search', query=query)

    def _on_open_room_search(self, p):
        self.open_rooms_found.emit(list(p.get('rooms', [])))

    def join_open_room(self, room_id, nickname):
        self._pending_groups.append({self.me})
        self._send('open_room_join', room_id=room_id, nickname=nickname)

    def member_name(self, room_id, username):
        """오픈채팅에서는 닉네임, 그 외에는 표시 이름"""
        room = self.rooms.get(room_id) or {}
        nick = (room.get('nicknames') or {}).get(username)
        return nick or self.display_name(username)

    def query_drawer(self, kind, limit=200):
        self._send('drawer_query', kind=kind, limit=limit)

    def _on_drawer_result(self, p):
        msgs = list(p.get('messages', []))
        for m in msgs:
            self._decrypt(m)
        self.drawer_loaded.emit(p.get('kind', ''), msgs)

    # ================= 친구 등록 (전화번호 / 이메일) =================
    EMAIL_RE = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')

    @staticmethod
    def contact_kind(text):
        """'email' | 'phone' | '' (형식이 맞지 않음)"""
        text = (text or '').strip()
        if '@' in text:
            return 'email' if AppState.EMAIL_RE.fullmatch(text) else ''
        digits = ''.join(ch for ch in text if ch.isdigit())
        return 'phone' if 9 <= len(digits) <= 13 and not re.search(r'[^\d\s+\-()]', text) else ''

    def add_friend_user(self, username):
        """보고 있는 동료를 바로 친구로 (연락처를 몰라도 - 다른 사람의 이메일은 서버가 보내지 않는다)"""
        self._send('friend_add', username=username)

    def add_friend(self, query):
        """전화번호 또는 이메일로 친구 추가 요청. 형식 오류 메시지 또는 None"""
        kind = self.contact_kind(query)
        if not kind:
            return '전화번호(예: 010-1234-5678) 또는 이메일(예: name@company.com)을 정확히 입력해 주세요.'
        self._send('friend_add', query=query.strip())
        return None

    def remove_friend(self, username):
        self._send('friend_remove', username=username)

    def is_registered_friend(self, username):
        return username in self.my_friends

    def _on_friend_added(self, p):
        user = p['user']
        if user['username'] not in self.users:
            self.users[user['username']] = user
            self.users_changed.emit()
        self.friend_added.emit(user, bool(p.get('already')))

    def _on_friend_list(self, p):
        self.my_friends = list(p.get('friends', []))
        self.my_friends_changed.emit()
        self.users_changed.emit()

    def request_attachment(self, attachment_id):
        cached = self.attachments.get(attachment_id)
        if cached:
            self.attachment_loaded.emit(attachment_id, *cached)
            return
        self._send('attachment_get', attachment_id=attachment_id)


    # ================= v4: 카카오톡 2026 업데이트 반영 (명세 §9) =================
    def request_summary(self, room_id, unread_only=False, since_msg_id=None):
        """채팅방 대화 요약 요청 → room_summary_ready.
        since_msg_id: 그 이후 메시지만 (채팅방을 열 때 안 읽었던 부분 요약)"""
        payload = {'room_id': room_id, 'unread_only': bool(unread_only)}
        if since_msg_id is not None:
            payload['since_msg_id'] = int(since_msg_id)
        self._send('room_summary', **payload)

    def _on_room_summary(self, p):
        self.room_summary_ready.emit(p.get('room_id', ''), dict(p))

    def request_call_history(self):
        self._send('call_history', limit=100)

    def _on_call_history(self, p):
        self.call_history = list(p.get('calls', []))
        self.call_history_changed.emit()

    def missed_call_count(self):
        return sum(1 for c in self.call_history if c.get('missed'))

    def send_contact(self, room_id, username):
        """연락처(프로필 카드) 보내기"""
        self._send('send', room_id=room_id, kind='contact', content=username)

    def send_voice(self, room_id, wav_bytes, seconds):
        """음성메시지 전송. 오류 메시지 또는 None"""
        seconds = int(round(seconds))
        if seconds < 1:
            return '1초 이상 녹음해 주세요.'
        if seconds > MAX_VOICE_SECONDS:
            return f'음성메시지는 최대 {MAX_VOICE_SECONDS // 60}분까지 보낼 수 있습니다.'
        if len(wav_bytes) > MAX_ATTACHMENT:
            return '음성메시지가 너무 깁니다.'
        name = datetime.now().strftime('voice_%Y%m%d_%H%M%S.wav')
        if self._send('send', room_id=room_id, kind='voice', content=str(seconds),
                      attachment=attachment_payload(name, 'audio/wav', wav_bytes)) is None:
            return '서버 연결이 끊겨 음성메시지를 보내지 못했습니다. 연결된 뒤 다시 보내 주세요.'
        return None

    def request_export(self, room_id, since=None, until=None):
        """대화 저장용 기록 요청 → room_export_ready. since/until(date, 그날 포함)을 주면 그 기간만 (명세 §9.5)"""
        extra = {}
        if since is not None:
            extra['since'] = since.isoformat()
        if until is not None:
            extra['until'] = until.isoformat()
        self._export_parts.pop(room_id, None)      # 앞선 내보내기가 끝나지 않고 남긴 조각
        self._send('room_export', room_id=room_id, **extra)

    def _on_room_export(self, p):
        """조각(part)을 모아 done 이면 한 번에 알린다 (조각이 없는 예전 서버 응답도 그대로 처리)"""
        rid = p.get('room_id', '')
        parts = self._export_parts.setdefault(rid, [])
        parts.extend(p.get('messages', []))
        if not p.get('done', True):
            return
        msgs = self._export_parts.pop(rid, [])
        for m in msgs:
            self._decrypt(m)
        hidden = set(self.settings.get('hidden_messages') or []) if self.settings is not None else set()
        msgs = [m for m in msgs if m.get('msg_id') not in hidden]
        self.room_export_ready.emit(rid, msgs, bool(p.get('truncated')))

    def export_body(self, room_id, m):
        """대화 저장 한 줄의 내용 (텍스트·CSV 공용)"""
        if m.get('deleted'):
            return '삭제된 메시지입니다.'
        if m.get('kind') == 'secret':
            return m.get('secret_text', '')
        if m.get('kind') in ('text', 'emoticon'):
            return m.get('content', '')
        return self.message_preview(m)

    def export_text(self, room_id, messages, saved_at=None, files=None, period=''):
        """카카오톡 PC '대화 내보내기' 형식의 텍스트.
        files={msg_id: 'files/…' 또는 '(포함되지 않음: 사유)'} 이면 첨부 줄 끝에 붙인다 (사진·파일 포함 저장)"""
        saved_at = saved_at or datetime.now()
        files = files or {}
        lines = [f'{self.room_title(room_id)} 님과 카카오톡 대화',
                 f"저장한 날짜 : {saved_at.strftime('%Y-%m-%d %H:%M:%S')}"]
        if period:
            lines.append(f'저장한 기간 : {period}')
        lines.append('')
        day = None
        for m in messages:
            ts = parse_dt(m.get('timestamp'))
            if ts is None:
                continue
            if ts.date() != day:
                day = ts.date()
                lines.append(f'--------------- {fmt_day(day)} ---------------')
            if m.get('kind') == 'system':
                lines.append(m.get('content', ''))
                continue
            when = fmt_ampm(ts)
            body = self.export_body(room_id, m)
            if m.get('msg_id') in files:
                body = f'{body} [{files[m["msg_id"]]}]'
            name = self.member_name(room_id, m.get('sender'))
            lines.append(f'[{name}] [{when}] {body}')
        return '\n'.join(lines) + '\n'

    EXPORT_KIND_NAMES = {'text': '텍스트', 'emoticon': '이모티콘', 'image': '사진', 'file': '파일',
                         'voice': '음성메시지', 'system': '알림', 'poll': '투표', 'event': '일정', 'call': '통화',
                         'secret': '비밀채팅', 'ladder': '사다리', 'contact': '연락처', 'board': '게시판'}

    def export_csv(self, room_id, messages, files=None):
        """엑셀에서 바로 열리는 표 (UTF-8 BOM + 쉼표 구분): 날짜, 시각, 보낸 사람, 종류, 내용, 첨부 파일"""
        import csv
        import io
        files = files or {}
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\r\n')
        writer.writerow(['날짜', '시각', '보낸 사람', '종류', '내용', '첨부 파일'])
        for m in messages:
            ts = parse_dt(m.get('timestamp'))
            if ts is None:
                continue
            kind = m.get('kind') or 'text'
            sender = '' if kind == 'system' else self.member_name(room_id, m.get('sender'))
            body = m.get('content', '') if kind == 'system' else self.export_body(room_id, m)
            attachment = (m.get('attachment') or {}).get('name', '') if not m.get('deleted') else ''
            writer.writerow([ts.strftime('%Y-%m-%d'), ts.strftime('%H:%M:%S'), _csv_safe(sender),
                             self.EXPORT_KIND_NAMES.get(kind, kind), _csv_safe(body),
                             _csv_safe(files.get(m.get('msg_id'), attachment))])
        return '\ufeff' + out.getvalue()

    def org_text(self, username, sep=' · '):
        """'회사 · 부서 · 직함' (비어 있으면 '')"""
        u = self.user(username) or {}
        return sep.join(x for x in (u.get('company'), u.get('department'), u.get('title')) if x)

    def is_story_hidden(self, username):
        return bool(self.settings and self.settings.contains('hidden_story_users', username))

    def set_story_hidden(self, username, hidden=True):
        """친구 소식 숨기기: 친구 관계는 유지하고 내 화면에서만 펑을 감춘다"""
        if self.settings and username != self.me and self.is_story_hidden(username) != hidden:
            self.settings.toggle_in('hidden_story_users', username)
            self.stories_changed.emit()

    # ================= 보내지 못한 메시지 (shape 09: 카카오톡처럼 빨간 ! + 재전송/삭제, 앱을 꺼도 보존) =================
    def is_connected(self):
        return self.conn is not None and bool(self.conn.is_connected)

    def _failed_list(self):
        """메모리 사본 (설정에서 한 번만 읽는다)"""
        if self._failed is None:
            self._failed = [dict(m) for m in ((self.settings.get('failed_messages') or []) if self.settings else [])]
            self._recount_failed()
        return self._failed

    def _recount_failed(self):
        counts = {}
        for m in self._failed or []:
            counts[m['room_id']] = counts.get(m['room_id'], 0) + 1
        self._failed_rooms = counts

    SAVE_THROTTLE_MS = 500

    def _save_failed(self):
        """첫 변경은 바로 저장(앱이 바로 꺼져도 남게), 0.5초 안에 이어지는 변경은 모아서 한 번 저장"""
        if self.settings is None:
            return
        import time
        now = time.monotonic()
        if now - self._failed_saved_at >= self.SAVE_THROTTLE_MS / 1000:
            self._flush_failed()
            return
        if self._failed_save_timer is None:
            from PyQt5.QtCore import QTimer, QCoreApplication
            self._failed_save_timer = QTimer(self)
            self._failed_save_timer.setSingleShot(True)
            self._failed_save_timer.timeout.connect(self._flush_failed)
            app = QCoreApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._flush_failed)   # 끄기 직전 남은 저장
        self._failed_save_timer.start(self.SAVE_THROTTLE_MS)

    def _flush_failed(self):
        import time
        if self._failed_save_timer is not None:
            self._failed_save_timer.stop()
        self._failed_saved_at = time.monotonic()
        if self.settings is not None and self._failed is not None:
            self.settings.set('failed_messages', [dict(m) for m in self._failed])

    def _record_failed(self, room_id, kind, content, options=None):
        """서버로 나가지 못한 메시지를 로컬 말풍선으로 남기고 저장. options: 다시 보낼 때 함께 보낼 send 필드"""
        failed = self._failed_list()
        m = {'msg_id': max([x['msg_id'] for x in failed] + [FAILED_ID_BASE]) + 1, 'room_id': room_id,
             'sender': self.me, 'kind': kind, 'content': content,
             'timestamp': datetime.now().isoformat(timespec='seconds'), 'failed': True,
             'reactions': {}, 'mentions': [], 'deleted': False}
        if options:
            m['send_options'] = dict(options)
        failed.append(dict(m))
        self._recount_failed()
        self._save_failed()
        m['fresh'] = True      # 이번에 막 실패함 → 말풍선 옆 ! 가 한 번 알린다 (저장본에는 없음)
        self.message_added.emit(room_id, m)
        self.send_failed.emit(room_id, m)
        self.room_changed.emit(room_id)
        return m

    def _restore_failed(self):
        """접속 직후: 저장해 둔 실패 메시지를 읽어 둔다 (room_messages 가 맨 아래에 붙인다)"""
        self._failed_list()

    def failed_messages(self, room_id=None):
        return [dict(m) for m in self._failed_list() if room_id is None or m.get('room_id') == room_id]

    def room_has_failed(self, room_id):
        self._failed_list()
        return self._failed_rooms.get(room_id, 0) > 0

    def _forget_failed(self, room_id, msg_id):
        self._failed = [m for m in self._failed_list() if m['msg_id'] != msg_id]
        self._recount_failed()
        self._save_failed()
        self.message_removed.emit(room_id, msg_id)
        self.room_changed.emit(room_id)

    def delete_failed(self, room_id, msg_id):
        """보내지 못한 메시지 삭제 (내 PC에만 있던 글)"""
        self._forget_failed(room_id, msg_id)

    def resend_failed(self, room_id, msg_id):
        """재전송. 아직 연결이 없거나 다시 실패하면 오류 문구(실패 말풍선은 그대로), 보냈으면 None"""
        m = next((x for x in self._failed_list() if x['msg_id'] == msg_id), None)
        if m is None:
            return '메시지를 찾을 수 없습니다.'
        if not self.is_connected():
            return '아직 서버에 연결되지 않았습니다. 연결이 돌아오면 다시 눌러 주세요.'
        payload = {'room_id': room_id, 'kind': m['kind'], 'content': m['content'], **(m.get('send_options') or {})}
        if m['kind'] == 'text':
            payload['mentions'] = self.parse_mentions(room_id, m['content'])
        if self._send('send', **payload) is None:
            return '전송에 실패했습니다. 연결 상태를 확인해 주세요.'
        self._forget_failed(room_id, msg_id)
        return None
