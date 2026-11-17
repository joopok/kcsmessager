# -*- coding: utf-8 -*-
"""
클라이언트 테스트 하네스 - 서버 없이 AppState/UI를 구동하기 위한 가짜 연결과 데모 데이터

    from tests.kakao_fakes import make_demo_state
    state, conn = make_demo_state()      # welcome + 히스토리 + 일정 + 할 일이 채워진 상태
    conn.sent                            # [(type, payload), ...] UI가 보낸 요청 기록
    conn.push('msg_new', message={...})  # 서버 푸시 흉내
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 테스트가 사용자 홈(~/.kcs_messenger)의 최근 로그인 등을 건드리지 않도록 임시 폴더 사용
os.environ.setdefault('KCS_MESSENGER_HOME', tempfile.mkdtemp(prefix='kcs_home_'))

from PyQt5.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from client.kakao.settings import LocalSettings  # noqa: E402
from client.kakao.state import AppState  # noqa: E402


_APP = []


def app():
    """QApplication 싱글턴 (참조를 보관해 GC로 사라지지 않게 함)"""
    if not _APP:
        _APP.append(QApplication.instance() or QApplication(sys.argv[:1]))
        # 실제 앱(app.main)과 같게: 메모리 정리(GC)는 화면 스레드의 이벤트 사이에서만 돌린다.
        # 자동 GC 가 Qt 가 위젯을 다루는 도중에 앞 테스트의 위젯을 지우면 테스트 프로세스가 죽는다 (가끔만 나는 세그폴트)
        from client.kakao.widgets import install_safe_gc
        install_safe_gc(_APP[0])
    return _APP[0]


def expand_org(tab):
    """조직도 탭의 회사 · 조직 · 그룹을 모두 펼친다 (기본은 내 소속만 펼침) - 배치를 보는 테스트용"""
    tab.refresh()
    tab.collapsed.clear()
    tab.collapsed_nodes.clear()
    tab.collapsed_groups.clear()
    tab.collapsed_teams.clear()
    tab.refresh()


class FakeConnection(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal(str)
    event_received = pyqtSignal(str, dict)

    def __init__(self):
        super().__init__()
        self.sent = []
        self._seq = 0

    is_connected = True          # 테스트에서 False 로 바꿔 연결 끊김을 흉내

    def send(self, msg_type, **payload):
        self._seq += 1
        self.sent.append((msg_type, payload))
        return self._seq

    def close(self):
        pass

    def push(self, msg_type, **payload):
        payload['type'] = msg_type
        self.event_received.emit(msg_type, payload)

    def last(self, msg_type):
        for t, p in reversed(self.sent):
            if t == msg_type:
                return p
        return None


def iso(dt):
    return dt.isoformat(timespec='seconds')


def _user(name, online, status='', birthday=None):
    return {'username': name, 'display_name': name, 'status_message': status,
            'birthday': birthday, 'online': online}


def demo_messages(now):
    t = lambda minutes: iso(now - timedelta(minutes=minutes))  # noqa: E731
    msgs = [
        dict(msg_id=1, room_id='r1', sender='', kind='system', content='홍길동님이 김철수, 이영희님을 초대했습니다.', timestamp=t(60 * 26)),
        dict(msg_id=2, room_id='r1', sender='김철수', kind='text', content='다들 내일 회의 시간 괜찮아요?', timestamp=t(60 * 25)),
        dict(msg_id=3, room_id='r1', sender='이영희', kind='text', content='저는 오후 2시 이후면 좋아요 😀', timestamp=t(60 * 25 - 1)),
        dict(msg_id=4, room_id='r1', sender='홍길동', kind='text', content='그럼 2시로 잡을게요. 자료는 https://example.com/docs 참고해 주세요', timestamp=t(50)),
        dict(msg_id=5, room_id='r1', sender='김철수', kind='text', content='@홍길동 네 확인했습니다!', timestamp=t(40),
             mentions=['홍길동'], reply_to={'msg_id': 4, 'sender': '홍길동', 'preview': '그럼 2시로 잡을게요.'},
             reactions={'👍': ['홍길동', '이영희'], '❤️': ['이영희']}),
        dict(msg_id=6, room_id='r1', sender='이영희', kind='emoticon', content='🎉', timestamp=t(30)),
        dict(msg_id=7, room_id='r1', sender='홍길동', kind='poll', content='점심 메뉴', timestamp=t(20),
             poll={'poll_id': 1, 'question': '회의 후 점심 메뉴는?', 'multiple': False, 'closed': False,
                   'options': [{'text': '김치찌개', 'voters': ['김철수']}, {'text': '돈까스', 'voters': ['이영희', '홍길동']},
                               {'text': '샐러드', 'voters': []}]}),
        dict(msg_id=8, room_id='r1', sender='홍길동', kind='event', content='주간 회의', timestamp=t(10), event_id=1),
        dict(msg_id=9, room_id='r1', sender='김철수', kind='file', content='회의자료.pdf', timestamp=t(5),
             attachment={'attachment_id': 1, 'name': '회의자료.pdf', 'mime': 'application/pdf', 'size': 248000}),
        dict(msg_id=10, room_id='r1', sender='이영희', kind='text', content='', timestamp=t(3), deleted=True),
        dict(msg_id=11, room_id='r1', sender='김철수', kind='text',
             content='긴 메시지 테스트입니다. 카카오톡 말풍선은 최대 너비를 넘으면 자동으로 줄바꿈되어야 하고, 한글과 English가 섞여 있어도 깨지지 않아야 합니다.',
             timestamp=t(1)),
        dict(msg_id=12, room_id='r2', sender='박민수', kind='text', content='주말에 시간 돼?', timestamp=t(90)),
        dict(msg_id=13, room_id='r2', sender='홍길동', kind='text', content='응 토요일 괜찮아', timestamp=t(80)),
        dict(msg_id=14, room_id='r2', sender='박민수', kind='text', content='좋아 그럼 토요일에 보자!', timestamp=t(2)),
    ]
    for m in msgs:
        m.setdefault('reply_to', None)
        m.setdefault('mentions', [])
        m.setdefault('reactions', {})
        m.setdefault('deleted', False)
        m.setdefault('attachment', None)
        m.setdefault('event_id', None)
        m.setdefault('poll', None)
        m.setdefault('edited', False)
        m.setdefault('edited_at', None)
        m.setdefault('silent', False)
        m.setdefault('link_preview', None)
        if m['deleted']:
            m['content'] = '메시지가 삭제되었습니다'
    return msgs


def make_demo_state(me='홍길동', settings_dir=None):
    """데모 데이터가 채워진 (AppState, FakeConnection)"""
    app()
    now = datetime.now().replace(microsecond=0)
    conn = FakeConnection()
    settings_dir = settings_dir or tempfile.mkdtemp(prefix='kcs_test_')
    settings = LocalSettings(me, base_dir=settings_dir)
    settings.set('social_features', True)   # 펑·생일 기능 테스트용 (실제 기본값은 꺼짐 - 업무 우선)
    state = AppState(conn, settings)
    today = date.today()
    users = [
        _user('홍길동', True, '오늘도 화이팅!'),
        _user('김철수', True, '회의 중', today.strftime('%m-%d')),
        _user('이영희', False, '여행 가고 싶다 ✈️'),
        _user('박민수', True, ''),
        _user('최지우', False, '바쁨'),
    ]
    msgs = demo_messages(now)
    rooms = [
        {'room_id': 'r1', 'type': 'group', 'name': '프로젝트 팀', 'members': ['홍길동', '김철수', '이영희'],
         'created_at': iso(now - timedelta(days=2)), 'notice': msgs[3],
         'last_message': msgs[10], 'read_pointers': {'홍길동': 8, '김철수': 11, '이영희': 9}, 'unread_count': 2},
        {'room_id': 'r2', 'type': 'direct', 'name': '', 'members': ['홍길동', '박민수'],
         'created_at': iso(now - timedelta(days=5)), 'notice': None,
         'last_message': msgs[13], 'read_pointers': {'홍길동': 13, '박민수': 14}, 'unread_count': 1},
        {'room_id': 'r3', 'type': 'self', 'name': '', 'members': ['홍길동'],
         'created_at': iso(now - timedelta(days=9)), 'notice': None,
         'last_message': None, 'read_pointers': {'홍길동': 0}, 'unread_count': 0},
    ]
    conn.push('welcome', me=users[0], users=users, rooms=rooms)
    conn.push('history', room_id='r1', messages=[m for m in msgs if m['room_id'] == 'r1'], has_more=False)
    conn.push('history', room_id='r2', messages=[m for m in msgs if m['room_id'] == 'r2'], has_more=False)
    state.history_requested.update({'r1', 'r2'})
    base = datetime(today.year, today.month, today.day)
    events = [
        {'event_id': 1, 'room_id': 'r1', 'creator': '홍길동', 'title': '주간 회의', 'description': '안건: 진행 상황 공유',
         'location': '3층 회의실', 'start': iso(base + timedelta(days=1, hours=14)), 'end': iso(base + timedelta(days=1, hours=15)),
         'all_day': False, 'remind_minutes': 10, 'color': '#4CB6F2',
         'attendees': {'홍길동': 'yes', '김철수': 'yes', '이영희': 'pending'}, 'created_at': iso(now)},
        {'event_id': 2, 'room_id': None, 'creator': '홍길동', 'title': '치과 예약', 'description': '', 'location': '',
         'start': iso(base + timedelta(hours=18)), 'end': iso(base + timedelta(hours=19)), 'all_day': False,
         'remind_minutes': 30, 'color': '#FF7A59', 'attendees': {'홍길동': 'yes'}, 'created_at': iso(now)},
        {'event_id': 3, 'room_id': None, 'creator': '홍길동', 'title': '워크숍', 'description': '', 'location': '양평',
         'start': iso(base + timedelta(days=3)), 'end': iso(base + timedelta(days=4, hours=23, minutes=59)), 'all_day': True,
         'remind_minutes': None, 'color': '#6DCC7B', 'attendees': {'홍길동': 'yes'}, 'created_at': iso(now)},
    ]
    conn.push('event_list', start=iso(base - timedelta(days=365)), end=iso(base + timedelta(days=365)), events=events)
    todos = [
        {'todo_id': 1, 'owner': '홍길동', 'title': '회의 자료 준비', 'due': (today + timedelta(days=1)).isoformat(), 'done': False, 'created_at': iso(now)},
        {'todo_id': 2, 'owner': '홍길동', 'title': '보고서 제출', 'due': today.isoformat(), 'done': True, 'created_at': iso(now)},
        {'todo_id': 3, 'owner': '홍길동', 'title': '장보기', 'due': None, 'done': False, 'created_at': iso(now)},
    ]
    conn.push('todo_list', todos=todos)
    conn.sent.clear()
    return state, conn
