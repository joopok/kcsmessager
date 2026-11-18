# -*- coding: utf-8 -*-
"""
화면 반응 측정 — 실제 서버(별도 프로세스) + 실제 앱 세션(Session)으로 앱의 모든 화면을 하나씩 열어 잰다.

규모: 직원 500명 · 채팅방 300개 · 메시지 1,000개가 넘는 방 · 일정 · 할 일 · 사진 · 스토리 (SCALE)
화면: 메인 창 첫 표시 · 왼쪽 탭(처음 · 다시) · 채팅방 열기 · 이전 대화 더 보기 · 통화 창 · 앱의 모든 대화상자와 창
      (client/kakao 의 QDialog · QMainWindow 하위 클래스 전부 — tests/test_kakao_screen_catalog.py 가 빠진 것을 잡는다)

잰 값 (화면마다)
  표시: 여는 함수를 부른 순간부터 화면이 그려지고, 그 화면이 보낸 서버 요청의 응답이 모두 와서 반영될 때까지 (ms)
  막힘: 그 뒤 0.4초 동안 이벤트 한 번 처리가 가장 오래 걸린 시간 (나머지를 나눠 채우는 동안 입력이 밀리지 않는지, ms)
  서버: 그 화면이 보낸 요청별 왕복 시간 (요청 이름 → ms)
기준: 표시 · 막힘 모두 LIMIT_MS(0.5초) 이하. 회사 PC 가 더 느릴 수 있어 개발 PC 목표는 TARGET_MS(0.3초).
처음 여는 화면은 모듈을 처음 불러오는 시간까지 들어가도록 새 프로세스에서 잰다.

사용:
  python3 scripts/harness/screen_probe.py                 # offscreen (기본)
  python3 scripts/harness/screen_probe.py --native        # 이 OS 의 실제 화면 플랫폼 (cocoa · windows · xcb)
  python3 scripts/harness/screen_probe.py --small         # 축소 규모 (CI 실제 화면 점검용)
  python3 scripts/harness/screen_probe.py --json out.json
  python3 scripts/harness/screen_probe.py --profile room_open   # 그 화면을 cProfile 로 (누적 시간 상위)
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zlib
import struct
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LIMIT_MS = 500        # 사용자 기준: 누르고 0.5초 안에
TARGET_MS = 300       # 개발 PC 목표 (회사 PC 여유)
SCALE = {'users': 500, 'rooms': 300, 'messages': 1000, 'events': 40, 'todos': 20}
SMALL = {'users': 120, 'rooms': 60, 'messages': 300, 'events': 10, 'todos': 5}
ME = '측정자'
PEER = '직원001'
COMPANY = '케이씨에스'
BIG_ROOM = '측정 큰 방'
WAIT_S = 6.0          # 서버 응답을 기다리는 최대 시간
BUSY_S = 0.4          # 표시 뒤 막힘을 재는 시간
# 응답 없이 끝나는 요청 (읽음 표시 · 지금 보는 방 알림처럼 결과를 기다리지 않는 것)
NO_REPLY = {'read', 'typing', 'active_room', 'presence', 'pong', 'ping', 'story_view', 'call_media', 'call_state',
            'mark_read', 'leave_call', 'call_leave'}

# 화면이 아닌 공통 바탕 클래스 (직접 열지 않는다) — 목록 검사(tests/test_kakao_screen_catalog.py)에서 뺀다
NOT_SCREENS = {
    'room_base._Dialog': '채팅방 대화상자 공통 바탕',
    'inbox._InboxDialog': '멘션 · 책갈피 창 공통 바탕',
}


# ---------------------------------------------------------------- 데이터 만들기
def tiny_png(w=64, h=48, rgb=(42, 56, 108)):
    """외부 도구 없이 단색 PNG"""
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def seed(db_path, scale):
    """서버를 띄우기 전에 DB 에 규모 데이터를 넣는다 → 화면에서 쓸 이름표"""
    from server.kakao.store import Store
    st = Store(db_path)
    n = scale['users']
    names = [f'직원{i:03d}' for i in range(n)]
    for i, name in enumerate([ME] + names):
        st.member_create(name, {'company': COMPANY, 'department': f'부서{i % 25:02d}', 'team': f'{i % 3 + 1}팀',
                                'title': '책임' if i % 7 == 0 else '사원', 'employee_no': f'M{i:05d}',
                                'extension': f'{1000 + i}', 'mobile': f'010-55{i:02d}-{i:04d}'[:13],
                                'email': f'm{i}@kcs.example'})
    st.set_admin(ME, True)
    png = tiny_png()
    crew = names[:30]
    big = st.create_group(ME, BIG_ROOM, crew)
    root_id = None
    for i in range(scale['messages']):
        sender = ME if i % 3 == 0 else crew[i % len(crew)]
        if i % 90 == 45:
            aid = st.save_attachment(f'사진{i}.png', 'image/png', png, sender)
            st.add_message(big, sender, 'image', f'사진{i}.png', attachment_id=aid)
        elif i % 97 == 60:
            aid = st.save_attachment(f'보고서{i}.txt', 'text/plain', ('보고서 ' * 50).encode(), sender)
            st.add_message(big, sender, 'file', f'보고서{i}.txt', attachment_id=aid)
        else:
            text = f'메시지 {i} 오늘 회의 자료는 공유 폴더에 올려 두었습니다 ' * (1 + i % 3)
            m = st.add_message(big, sender, 'text', text, mentions=[ME] if i % 50 == 10 else ())
            if i == scale['messages'] - 40:
                root_id = m['msg_id']
    if root_id:
        for j in range(6):
            st.add_message(big, crew[j], 'text', f'스레드 답글 {j}', thread_root=root_id)
    st.update_read(big, ME, 0)
    rooms = [big]
    for i in range(scale['rooms'] - 1):
        other = names[(i + 1) % n]
        if i % 2:
            rid = st.create_group(ME, f'업무방 {i:03d}', [other, names[(i + 7) % n], names[(i + 13) % n]])
        else:
            rid, _members = st.get_or_create_direct(ME, other)
        st.add_message(rid, other, 'text', f'{i}번 방 마지막 메시지입니다')
        rooms.append(rid)
    today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    for i in range(scale['events']):
        start = today + timedelta(days=i % 28 - 10, hours=i % 6)
        st.create_event(ME, {'title': f'일정 {i}', 'description': '측정용 일정', 'location': '회의실',
                             'start': start.isoformat(timespec='seconds'),
                             'end': (start + timedelta(hours=1)).isoformat(timespec='seconds'),
                             'all_day': False, 'remind_minutes': 10, 'color': ''},
                        [ME, PEER], big if i % 5 == 0 else None)
    for i in range(scale['todos']):
        st.create_todo(ME, f'할 일 {i}', (today + timedelta(days=i % 7)).date().isoformat())
    st.create_story(PEER, 'text', '오늘의 소식', None, '#2A386C', 86400)
    st.org_group_create('측정 그룹', '화면 측정', names[:20], ME)
    st.close()
    return {'big': big, 'rooms': rooms, 'root_id': root_id, 'names': names, 'png': png}


# ---------------------------------------------------------------- 서버 (별도 프로세스)
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(db_path, port):
    env = dict(os.environ, KCS_RAG_LLM='off', KCS_LINK_PREVIEW='off', QT_QPA_PLATFORM='offscreen')
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, 'kcs.py'), 'server', '--port', str(port), '--db', db_path,
                             '--allow-passwordless', '--no-briefing', '--no-link-preview', '--no-portal'],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 30
    while time.time() < end:
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.5).close()
            return proc
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError('서버가 시작하지 못했습니다')
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError('서버 연결 시간 초과')


# ---------------------------------------------------------------- 측정
class Probe:
    def __init__(self, app, session, info, profile=None):
        self.app = app
        self.s = session
        self.state = session.state
        self.info = info
        self.results = []
        self.profile = profile
        self.profile_text = ''
        self._sent = None            # req_id → (요청 이름, 보낸 시각)
        self._replied = {}           # req_id → 화면 스레드가 응답을 처리한 시각
        self._arrived = {}           # req_id → 응답이 소켓에 도착한 시각 (읽기 스레드) - 서버 왕복 시간
        conn = session.conn
        original = conn.send

        def send(msg_type, **payload):
            req = original(msg_type, **payload)
            if self._sent is not None and req is not None:
                self._sent[req] = (msg_type, time.perf_counter())
            return req
        conn.send = send
        self._stray = None
        self._stray_filter = _StrayWindowFilter(self)
        app.installEventFilter(self._stray_filter)
        conn.event_received.connect(self._on_event)
        from PyQt5.QtCore import Qt
        conn.event_received.connect(self._on_arrival, Qt.DirectConnection)   # 읽기 스레드에서 바로 (화면이 바빠도)

    @property
    def w(self):
        return self.s.window

    def _on_arrival(self, _kind, payload):
        req = payload.get('req_id') if isinstance(payload, dict) else None
        sent = self._sent
        if sent is not None and req in sent and req not in self._arrived:
            self._arrived[req] = time.perf_counter()

    def _on_event(self, _kind, payload):
        req = payload.get('req_id') if isinstance(payload, dict) else None
        if self._sent is not None and req in self._sent and req not in self._replied:
            self._replied[req] = time.perf_counter()

    def pump(self, seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.app.processEvents()
            time.sleep(0.002)

    def _waiting(self):
        return [req for req, (kind, _t) in self._sent.items() if req not in self._replied and kind not in NO_REPLY]

    def measure(self, key, label, open_fn, ready=None, close=None):
        """open_fn() → 화면 객체. ready(obj) 가 True 이고 보낸 요청의 응답이 다 올 때까지를 잰다"""
        entry = {'key': key, 'label': label, 'show_ms': None, 'block_ms': None, 'server': {}, 'error': ''}
        self.pump(0.03)
        self._sent, self._replied, self._arrived = {}, {}, {}
        self._stray = []
        prof = None
        if self.profile == key:
            import cProfile
            prof = cProfile.Profile()
            prof.enable()
        obj = None
        t0 = time.perf_counter()
        try:
            obj = open_fn()
            self.app.processEvents()
            done = time.perf_counter()
            deadline = t0 + WAIT_S
            while self._waiting() or (ready is not None and not ready(obj)):
                if time.perf_counter() > deadline:
                    late = sorted({self._sent[r][0] for r in self._waiting()})
                    entry['error'] = '응답 없음: ' + ', '.join(late) if late else '화면이 준비되지 않음'
                    break
                self.app.processEvents()
                done = time.perf_counter()
                time.sleep(0.001)
            entry['show_ms'] = round((done - t0) * 1000)
            worst, end = 0.0, time.perf_counter() + BUSY_S
            while time.perf_counter() < end:
                t = time.perf_counter()
                self.app.processEvents()
                worst = max(worst, time.perf_counter() - t)
                time.sleep(0.002)
            entry['block_ms'] = round(worst * 1000)
        except Exception as e:      # 화면 하나가 터져도 나머지는 잰다
            import traceback
            entry['error'] = f'{type(e).__name__}: {e}'
            entry['trace'] = traceback.format_exc()[-600:]
        if prof is not None:
            import io
            import pstats
            prof.disable()
            out = io.StringIO()
            pstats.Stats(prof, stream=out).sort_stats('cumulative').print_stats(45)
            self.profile_text = out.getvalue()
        for req, (kind, sent) in self._sent.items():
            got = self._arrived.get(req) or self._replied.get(req)
            if got is not None:
                ms = round((got - sent) * 1000)
                entry['server'][kind] = max(ms, entry['server'].get(kind, 0))
        self._sent = None
        if self._stray:
            names = sorted(set(self._stray))
            entry['stray'] = names
            entry['error'] = (entry['error'] + ' · ' if entry['error'] else '') + '번쩍이는 창: ' + ', '.join(names)
        self._stray = None
        try:
            if close is not None:
                close(obj)
            elif obj is not None and hasattr(obj, 'close'):
                obj.close()
                if hasattr(obj, 'deleteLater'):
                    obj.deleteLater()
        except Exception:
            pass
        self.pump(0.05)
        entry['ok'] = (not entry['error'] and entry['show_ms'] is not None
                       and entry['show_ms'] <= LIMIT_MS and entry['block_ms'] <= LIMIT_MS)
        self.results.append(entry)
        return obj


def _stray_window_filter_class():
    from PyQt5.QtCore import QObject, QEvent, Qt
    from PyQt5.QtWidgets import QDialog, QMainWindow, QWidget, QMenu

    class StrayWindowFilter(QObject):
        """측정하는 동안 뜬 '창이 아닌 위젯의 최상위 창'을 모은다 - 부모 없는 위젯에 setVisible(True) 를 부르면
        Qt 가 그 위젯을 작은 최상위 창으로 띄운다 (Windows 에서 번쩍임, macOS 에서는 창을 만드느라 느려짐)"""

        def __init__(self, probe):
            super().__init__()
            self.probe = probe

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Show and self.probe._stray is not None and isinstance(obj, QWidget)                     and obj.isWindow() and not isinstance(obj, (QDialog, QMainWindow, QMenu))                     and obj.windowType() not in (Qt.Popup, Qt.ToolTip, Qt.ToolTip | Qt.Popup)                     and type(obj).__name__ not in WINDOW_CLASSES:
                self.probe._stray.append(type(obj).__name__)
            return False
    return StrayWindowFilter


# 원래 따로 뜨는 창 (QDialog · QMainWindow 가 아닌 것)
WINDOW_CLASSES = {'ChatRoomWindow', 'CallWindow', 'Toast', 'LockScreen', 'QWidget#window', 'TrayToast'}


def _StrayWindowFilter(probe):
    return _stray_window_filter_class()(probe)


def _make(module, name, *args, **kwargs):
    """모듈을 (처음이면) 불러와 창을 만들고 띄운다 — 사용자가 누른 순간부터의 일을 그대로"""
    import importlib
    cls = getattr(importlib.import_module('client.kakao.' + module), name)
    obj = cls(*args, **kwargs)
    obj.show()
    return obj


def _member_record(state, username):
    from client.kakao.admin import member_record
    return member_record(state, username)


def _top_level(cls_name):
    from PyQt5.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if type(w).__name__ == cls_name and w.isVisible():
            return w
    return None


def screens(p):
    """(key, 이름, 이 화면이 맡는 클래스들, 여는 함수, 준비 확인, 닫는 함수) — 앱이 여는 순서와 모양 그대로"""
    st = p.state
    big = p.info['big']
    peer = PEER
    other_room = p.info['rooms'][2]
    out = []

    def add(key, label, classes, open_fn, ready=None, close=None):
        out.append((key, label, tuple(classes), open_fn, ready, close))

    def room():
        return p.w.room_windows.get(big)

    # 메인 창 · 탭
    add('main', '메인 창 첫 표시 (로그인 직후)', ['main_window.MainWindow'],
        lambda: (p.s.show_main_window(), p.w)[1], close=lambda _w: None)
    from client.kakao.main_window import TAB_KEYS, TAB_LABELS
    for when in ('처음', '다시'):
        for tab in TAB_KEYS:
            add(f'tab_{tab}_{"first" if when == "처음" else "again"}', f'탭 전환 ({when}) {TAB_LABELS[tab]}', [],
                lambda t=tab: (p.w.set_tab(t), p.w)[1], close=lambda _w: None)
    add('tab_back', '탭 전환 (다시) 친구', [], lambda: (p.w.set_tab('friends'), p.w)[1], close=lambda _w: None)
    # 채팅방
    add('room_open', '채팅방 열기 (메시지 1,000개 방 · 서버에서 최근 대화)', ['chat_room.ChatRoomWindow'],
        lambda: p.w.open_room(big), ready=lambda win: bool(win.rows), close=lambda _w: None)

    def older():
        win = room()
        win.scroll.verticalScrollBar().setValue(0)
        win._load_older()
        return win
    for i in (1, 2, 3):
        add(f'room_older{i}', f'이전 대화 더 보기 ({i}번째)', [], older,
            ready=lambda win: not win._loading_older, close=lambda _w: None)

    def reopen():
        win = room()
        win.close()
        p.pump(0.1)
        return p.w.open_room(big)
    add('room_reopen', '채팅방 다시 열기 (받아 둔 대화)', [], reopen, ready=lambda win: bool(win.rows),
        close=lambda _w: None)
    add('room_other', '다른 채팅방 열기', [], lambda: p.w.open_room(other_room), ready=lambda win: bool(win.rows))

    def call():
        room().start_call('voice')
        return 'call'
    add('call', '통화 창 (보이스톡 걸기)', ['call.CallWindow'], call,
        ready=lambda _x: _top_level('CallWindow') is not None,
        close=lambda _x: (_top_level('CallWindow') or p.w).hide() if _top_level('CallWindow') is None
        else _top_level('CallWindow').hang_up())

    # 대화상자 · 창 (모듈, 클래스, 인자)
    def ev():
        return next(iter(sorted(st.events.values(), key=lambda e: e.get('event_id', 0))), None) or {}
    w = lambda: p.w        # noqa: E731
    r = lambda: room()     # noqa: E731
    dialogs = [
        ('login', 'LoginDialog', '로그인 창', lambda: ('127.0.0.1', 5555, ME), {}),
        ('main_window', 'QuickSwitcher', '빠른 이동 (Ctrl+K)', lambda: (st, w()), {}),
        ('friends_tab', 'ProfileDialog', '프로필 창', lambda: (st, peer, w()), {}),
        ('friends_tab', 'ProfileEditDialog', '내 프로필 편집', lambda: (st, w()), {}),
        ('friends_tab', 'AddFriendDialog', '친구 추가', lambda: (st, w()), {}),
        ('admin', 'AdminDialog', '관리자 창', lambda: (st, w()), {}),
        ('admin', 'MemberDialog', '관리자 · 구성원 편집', lambda: (st, _member_record(st, peer), w()), {}),
        ('admin', 'NoticeDialog', '관리자 · 공지 보내기', lambda: (w(),), {}),
        ('admin', 'AdminNoticeDialog', '받은 관리자 공지', lambda: (w(), {'content': '공지입니다', 'sender': ME,
                                                                    'timestamp': '2026-11-18T09:00:00'}), {}),
        ('chats_tab', 'CreateGroupDialog', '새 채팅 (대화상대 선택)', lambda: (st, w()), {}),
        ('chats_tab', 'InviteToRoomDialog', '채팅방에 초대 (친구에서)', lambda: (st, peer, w()), {}),
        ('chats_tab', 'ChatFolderDialog', '채팅 폴더 만들기', lambda: (st, None, w()), {}),
        ('chats_tab', 'OpenChatDialog', '오픈채팅', lambda: (st, w()), {}),
        ('chats_tab', 'PeekDialog', '읽지 않고 미리보기', lambda: (st, other_room, w()), {}),
        ('drawer', 'TalkDrawerDialog', '톡서랍', lambda: (st, w()), {'main_window': None}),
        ('inbox', 'MentionsDialog', '멘션 모아보기', lambda: (st, w()), {}),
        ('inbox', 'SavedDialog', '책갈피', lambda: (st, w()), {}),
        ('org_tab', 'GroupDialog', '조직도 그룹 만들기', lambda: (st, None, w()), {}),
        ('org_tab', 'MembersDialog', '부서 구성원 보기', lambda: (st, '부서01', p.info['names'][:40], w()), {}),
        ('event_dialog', 'EventDialog', '일정 만들기', lambda: (st,), {'parent': w()}),
        ('event_dialog', 'EventDetailDialog', '일정 보기', lambda: (st, ev(), w()), {}),
        ('story', 'StoryViewer', '스토리 보기', lambda: (st, peer, w()), {}),
        ('story', 'StoryComposeDialog', '스토리 올리기', lambda: (st, w()), {}),
        ('more_tab', 'MultiProfileDialog', '멀티프로필', lambda: (st, w()), {}),
        ('more_tab', 'FriendManageDialog', '친구 관리 (숨김 · 차단)', lambda: (st, w()), {}),
        ('more_tab', 'KeywordAlertDialog', '키워드 알림', lambda: (p.s.settings, w()), {}),
        ('more_tab', 'BubbleStyleDialog', '말풍선 모양', lambda: (p.s.settings, w()), {}),
        ('more_tab', 'PasswordChangeDialog', '비밀번호 바꾸기', lambda: (st, w()), {}),
        ('more_tab', 'LockSettingsDialog', '잠금 설정', lambda: (st, p.s.settings, w()), {}),
        ('more_tab', 'RagDocsDialog', 'AI 비서 문서', lambda: (st, w()), {}),
        ('dnd', 'DndDialog', '방해금지', lambda: (st, p.s.settings, w()), {}),
        ('deputies', 'DeputyPickerDialog', '대리인 고르기', lambda: (st, p.info['names'][:60]), {'parent': w()}),
        ('file_guard', 'FileSecurityDialog', '파일 보안 (관리자)', lambda: (st, w()), {}),
        ('file_guard', 'ExceptionDialog', '파일 예외 (관리자)', lambda: (st, w()), {}),
        ('file_guard', 'FileBlockedDialog', '막힌 파일 안내', lambda: (st, {'room_id': big, 'kind': 'file', 'name': '설치.exe',
                                                                       'size': 2048, 'detail': '실행 파일'}, w()), {}),
        ('feature_tour', 'FeatureTourDialog', '기능 둘러보기', lambda: (st, p.s.settings, w(), w()), {}),
        ('logout_dialog', 'LogoutDialog', '로그아웃 확인', lambda: (st, None, w()), {}),
        ('wallpaper', 'WallpaperDialog', '배경화면', lambda: (p.s.settings, big, w()), {}),
        ('voice', 'VoiceRecordDialog', '음성 메시지 녹음', lambda: (w(),), {'autostart': False}),
        # 채팅방 안에서 여는 것
        ('room_dialogs', 'InviteDialog', '채팅방 · 대화상대 초대', lambda: (st, big, r()), {}),
        ('room_dialogs', 'DrawerDialog', '채팅방 · 서랍 (사진 · 파일)', lambda: (r(), r()), {}),
        ('room_dialogs', 'SummaryDialog', '채팅방 · 대화 요약', lambda: (st, big, r()), {}),
        ('room_dialogs', 'BoardDialog', '채팅방 · 게시판', lambda: (st, big, r()), {}),
        ('room_dialogs', 'LadderDialog', '채팅방 · 사다리', lambda: (st, big, r()), {}),
        ('room_dialogs', 'PollCreateDialog', '채팅방 · 투표 만들기', lambda: (r(),), {}),
        ('room_dialogs', 'ContactPickDialog', '채팅방 · 연락처 보내기', lambda: (st, r()), {}),
        ('room_dialogs', 'ImageViewer', '채팅방 · 사진 보기', lambda: (p.info['png'], '사진.png', r()), {}),
        ('chat_room', 'ForwardDialog', '채팅방 · 전달', lambda: (st, r()), {}),
        ('chat_room', 'ScheduleDialog', '채팅방 · 예약 메시지', lambda: ('예약할 글', r()), {}),
        ('chat_room', 'ReactionPicker', '채팅방 · 공감 고르기', lambda: ((), None, r()), {}),
        ('chat_export', 'ExportDialog', '채팅방 · 대화 내보내기', lambda: (BIG_ROOM, r()), {}),
        ('thread_view', 'ThreadDialog', '채팅방 · 스레드', lambda: (r(), p.info['root_id'], r()), {}),
    ]
    for module, name, label, args, kwargs in dialogs:
        if name == 'TalkDrawerDialog':
            kwargs = {'main_window': None}
        add(f'dlg_{name}', label, [f'{module}.{name}'],
            lambda m=module, n=name, a=args, k=kwargs: _make(m, n, *a(), **(
                {**k, 'main_window': p.w} if n == 'TalkDrawerDialog' else k)))
    return out


def covered_classes():
    """측정 목록이 맡는 클래스 이름들 ('모듈.클래스') — 화면 목록 검사 테스트가 쓴다"""
    class _Stub:
        info = {'big': '', 'rooms': ['', '', ''], 'root_id': 0, 'names': [], 'png': b''}
        state = None
        s = None
        w = None
    return {cls for entry in screens(_Stub()) for cls in entry[2]}


QUIET_QT = ('propagateSizeHints', 'does not support raise', 'does not support setParent', 'requestActivate',
            'grabbing the keyboard', 'Populating font', 'font family aliases')


def _quiet_qt(_mode, _ctx, msg):
    if not any(s in msg for s in QUIET_QT):
        sys.stderr.write(msg + '\n')


def run(native=False, small=False, profile=None, only=None, log=print):
    """측정 실행 → {'results': [...], 'platform': ..., 'scale': ...}"""
    if not native:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    home = tempfile.mkdtemp(prefix='kcs_screens_')
    os.environ['KCS_MESSENGER_HOME'] = home
    scale = SMALL if small else SCALE
    db = os.path.join(home, 'server', 'screens.db')
    os.makedirs(os.path.dirname(db), exist_ok=True)
    t = time.perf_counter()
    info = seed(db, scale)
    log(f'데이터 준비 {time.perf_counter() - t:.1f}초 (직원 {scale["users"]} · 방 {scale["rooms"]} · 메시지 {scale["messages"]})')
    port = free_port()
    server = start_server(db, port)
    try:
        # 앱(client/kakao/app.py main · _run)과 같은 순서로 띄운다. 단 macOS 의 '앱 번들 안에서 다시 실행'은 끈다
        # (Dock 이름용으로 이 프로세스를 다시 시작한다 - 측정마다 데이터 폴더가 새로 생겨 끝없이 다시 시작된다)
        os.environ['KCS_NO_APP_BUNDLE'] = '1'
        from client.kakao import osenv
        from client.kakao.app import Session, setup_high_dpi, install_korean_qt_translation
        from PyQt5.QtWidgets import QApplication
        if QApplication.instance() is None:
            setup_high_dpi()
        app = QApplication.instance() or QApplication(sys.argv[:1])
        osenv.configure_app(app)
        from client.kakao.widgets import install_safe_gc
        install_safe_gc(app)
        install_korean_qt_translation(app)
        from PyQt5.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_quiet_qt)
        from client.kakao.main_window import apply_app_theme     # 앱도 로그인 창 전에 불러 둔다 (app.main)
        from client.kakao.login import talk_icon
        apply_app_theme(None)
        app.setWindowIcon(talk_icon())                            # 앱은 로그인 창을 띄우기 전에 아이콘을 만든다
        from client.kakao.login import LoginDialog                # 앱처럼 로그인 창을 먼저 그린다 (글꼴을 처음 읽는 시간)
        login = LoginDialog('127.0.0.1', port, ME)
        login.show()
        for _ in range(5):
            app.processEvents()
            time.sleep(0.01)
        login.close()
        login.deleteLater()
        s = Session('127.0.0.1', port, ME)
        s.connect()
        end = time.time() + 15
        while time.time() < end and s.state.me != ME:
            app.processEvents()
            time.sleep(0.005)
        if s.state.me != ME:
            raise RuntimeError('로그인 실패')
        apply_app_theme(s.settings)
        p = Probe(app, s, info, profile=profile)
        p.pump(0.3)
        for key, label, _classes, open_fn, ready, close in screens(p):
            if only and key not in only and key != 'main' and not key.startswith('room_open'):
                continue
            p.measure(key, label, open_fn, ready, close)
            e = p.results[-1]
            mark = '✓' if e['ok'] else '✗'
            log(f"   {mark} {label} — 표시 {e['show_ms']}ms · 막힘 {e['block_ms']}ms"
                + (f" · 서버 {e['server']}" if e['server'] else '') + (f" · {e['error']}" if e['error'] else ''))
        platform = app.platformName()
        try:
            s.close()
        except Exception:
            pass
        return {'results': p.results, 'platform': platform, 'scale': scale, 'profile': p.profile_text}
    finally:
        server.terminate()
        try:
            server.wait(5)
        except Exception:
            server.kill()
        shutil.rmtree(home, ignore_errors=True)


def summary_lines(data, top=12):
    res = data['results']
    lines = [f"화면 {len(res)}개 · 플랫폼 {data['platform']} · 기준 {LIMIT_MS}ms (목표 {TARGET_MS}ms)"]
    slow = sorted(res, key=lambda e: -max(e['show_ms'] or 0, e['block_ms'] or 0))[:top]
    lines.append('느린 화면 상위:')
    for e in slow:
        lines.append(f"  {max(e['show_ms'] or 0, e['block_ms'] or 0):5d}ms  {e['label']}"
                     f"  (표시 {e['show_ms']} · 막힘 {e['block_ms']})" + ('' if e['ok'] else '  ✗'))
    servers = sorted(((ms, kind, e['label']) for e in res for kind, ms in e['server'].items()), reverse=True)
    late = [x for x in servers if x[0] > LIMIT_MS]
    if late:
        lines.append('0.5초 넘은 서버 응답: ' + ', '.join(f'{k} {ms}ms ({label})' for ms, k, label in late))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description='KCS Talk 화면 반응 측정')
    ap.add_argument('--native', action='store_true', help='이 OS 의 실제 화면 플랫폼으로')
    ap.add_argument('--small', action='store_true', help='축소 규모')
    ap.add_argument('--json', help='결과를 JSON 파일로')
    ap.add_argument('--profile', help='이 key 의 화면을 cProfile 로')
    ap.add_argument('--only', nargs='*', help='이 key 들만')
    args = ap.parse_args(argv)
    data = run(native=args.native, small=args.small, profile=args.profile, only=set(args.only or ()))
    print('\n'.join(summary_lines(data)))
    if data['profile']:
        print(data['profile'])
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    sys.stdout.flush()
    os._exit(0 if all(e['ok'] for e in data['results']) else 1)


if __name__ == '__main__':
    main()
