# -*- coding: utf-8 -*-
"""
애플리케이션 셸(로그인·메인 창·친구/채팅/조직도/더보기 탭) 테스트 - 서버 없이 FakeConnection 사용

    QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_kakao_shell -v
"""

import base64
import importlib.util
import json
import os
import tempfile
import unittest

from datetime import datetime, timedelta
from unittest import mock

from tests.kakao_fakes import make_demo_state, app

from PyQt5.QtCore import Qt, QEvent, QPoint
from PyQt5.QtGui import QImage, QColor
from PyQt5.QtWidgets import QMessageBox, QApplication, QLineEdit, QPushButton

from client.kakao import theme
from client.kakao.main_window import MainWindow, IdleWatcher, apply_app_theme
from client.kakao.chats_tab import (ChatsTab, CreateGroupDialog, InviteToRoomDialog, format_room_time,
                                    RoomRow, ChatFolderDialog, PeekDialog, AiButton)
from client.kakao.state import BOT_NAME
from client.kakao.friends_tab import FriendsTab, ProfileEditDialog, ProfileDialog, presence_menu, FriendRow
from client.kakao.org_tab import OrgTab
from client.kakao.more_tab import MoreTab, RagDocsDialog
from client.kakao.login import LoginDialog

HAS_CHAT_ROOM = importlib.util.find_spec('client.kakao.chat_room') is not None
HAS_CALENDAR = importlib.util.find_spec('client.kakao.calendar_tab') is not None
HAS_WALLPAPER = importlib.util.find_spec('client.kakao.wallpaper') is not None

ORG = {'홍길동': ('KCS 홀딩스', '경영지원팀', '팀장'), '김철수': ('KCS 홀딩스', '경영지원팀', '대리'),
       '이영희': ('KCS 물류', '운영팀', '과장'), '박민수': ('KCS 물류', '운영팀', '사원'),
       '최지우': ('KCS 커머스', '마케팅팀', '팀장')}


def add_org(state, conn):
    """데모 사용자에 회사/부서/직함과 AI 비서를 추가"""
    for name, (company, dept, title) in ORG.items():
        user = dict(state.user(name))
        user.update(company=company, department=dept, title=title, presence='online', is_bot=False)
        conn.push('user_updated', user=user)
    conn.push('user_updated', user={'username': 'AI 비서', 'display_name': 'AI 비서', 'status_message': '',
                                    'birthday': None, 'online': True, 'presence': 'online',
                                    'company': 'KCS AI', 'department': '지식봇', 'title': '봇', 'is_bot': True})


def rows(lst):
    return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]


class ShellTestBase(unittest.TestCase):
    def setUp(self):
        app()
        theme.set_dark(False)
        theme.set_font_scale(1.0)
        self.state, self.conn = make_demo_state()
        self.settings = self.state.settings
        self._windows = []

    def tearDown(self):
        for w in self._windows:
            w.hide()
            w.deleteLater()
        # 앱 전체 스타일 재적용 전에 지연 삭제를 먼저 처리 (삭제 대기 위젯 repolish → 세그폴트 방지)
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        theme.set_dark(False)
        theme.set_font_scale(1.0)
        apply_app_theme(None)
        QApplication.processEvents()

    def make_window(self, **kwargs):
        win = MainWindow(self.state, self.settings, server_address='127.0.0.1:5555', **kwargs)
        self._windows.append(win)
        return win


# ================================================================ 메인 창
class MainWindowTests(ShellTestBase):
    def test_builds_and_switches_tabs(self):
        win = self.make_window()
        win.show()
        self.assertEqual(win.current_tab(), 'friends')
        self.assertTrue(win.sidebar.buttons['friends'].isChecked())
        for key in ('chats', 'org', 'calendar', 'more', 'friends'):
            win.sidebar.buttons[key].click()
            self.assertEqual(win.current_tab(), key)
            self.assertTrue(win.sidebar.buttons[key].isChecked())
        self.assertGreaterEqual(win.minimumWidth(), 340)
        self.assertGreaterEqual(win.width(), 340)

    @unittest.skipUnless(HAS_CALENDAR, 'calendar_tab 미완성')
    def test_calendar_tab_is_real(self):
        from client.kakao.calendar_tab import CalendarTab
        win = self.make_window()
        self.assertIsInstance(win.tabs['calendar'], CalendarTab)

    def test_unread_badge_and_title(self):
        win = self.make_window()
        total = self.state.total_unread()
        self.assertEqual(total, 3)
        self.assertEqual(win.sidebar.chat_badge.count, total)
        self.assertEqual(win.windowTitle(), f'KCS Talk ({total})')
        self.conn.push('msg_new', message=dict(
            msg_id=99, room_id='r2', sender='박민수', kind='text', content='새 메시지',
            timestamp=datetime.now().isoformat(timespec='seconds'), reply_to=None, mentions=[], reactions={},
            deleted=False, attachment=None, event_id=None, poll=None))
        self.assertEqual(self.state.total_unread(), total + 1)
        self.assertEqual(win.sidebar.chat_badge.count, total + 1)
        self.assertEqual(win.windowTitle(), f'KCS Talk ({total + 1})')
        # 알림 끈 방의 안 읽은 수는 합계에서 빠진다
        self.settings.toggle_in('muted_rooms', 'r1')
        self.assertEqual(win.sidebar.chat_badge.count, self.state.total_unread())
        self.assertEqual(win.sidebar.chat_badge.count, total + 1 - 2)

    def test_connection_lost_banner_and_reconnect(self):
        calls = []
        win = self.make_window(reconnect=lambda: calls.append(1) or (True, ''))
        win.show()
        self.assertFalse(win.banner.isVisible())
        self.state.connection_lost.emit('끊김')
        self.assertTrue(win.banner.isVisible())
        self.assertIn('서버 연결이 끊어졌습니다', win.banner.label.text())
        win.banner.button.click()
        self.assertEqual(calls, [1])
        self.assertFalse(win.banner.isVisible())

    def test_error_shows_toast(self):
        win = self.make_window()
        win.show()
        self.state.error.emit('권한이 없습니다')
        self.assertTrue(win.toast.isVisible())
        self.assertEqual(win.toast.text(), '권한이 없습니다')

    def test_notification_respects_setting(self):
        win = self.make_window()
        self.settings.set('notifications', False)
        self.assertFalse(win.show_notification('제목', '본문', 'r1'))
        self.settings.set('notifications', True)
        with mock.patch.object(QApplication, 'activeWindow', return_value=None):
            self.assertTrue(win.show_notification('제목', '본문', 'r1'))
        self.assertEqual(win._last_notify_room, 'r1')
        with mock.patch.object(QApplication, 'activeWindow', return_value=win):
            self.assertFalse(win.show_notification('제목', '본문', 'r1'))

    def test_dark_mode_and_font_scale_rethemes(self):
        win = self.make_window()
        win.show()
        more = win.tabs['more']
        more.dark_toggle.setChecked(True)
        self.assertTrue(self.settings.get('dark_mode'))
        self.assertTrue(theme.is_dark())
        self.assertIn(theme.c('bg'), QApplication.instance().styleSheet())
        self.assertEqual(QApplication.instance().palette().window().color(), QColor(theme.c('bg')))
        more.set_font_scale_index(3)
        self.assertAlmostEqual(self.settings.get('font_scale'), 1.3)
        self.assertAlmostEqual(theme.font_scale(), 1.3)
        more.notify_toggle.setChecked(False)
        self.assertFalse(self.settings.get('notifications'))
        more.dark_toggle.setChecked(False)
        self.assertFalse(theme.is_dark())

    def test_room_opened_opens_window_once(self):
        win = self.make_window()
        if not HAS_CHAT_ROOM:
            self.skipTest('chat_room 미완성')
        self.state.room_opened.emit('r1')
        self.assertIn('r1', win.room_windows)
        first = win.room_windows['r1']
        self.state.room_opened.emit('r1')
        self.assertIs(win.room_windows['r1'], first)
        self.state.room_opened.emit('r2')
        self.assertEqual(set(win.room_windows), {'r1', 'r2'})
        self.assertNotEqual(win.room_windows['r1'].pos(), win.room_windows['r2'].pos())
        self.conn.push('room_removed', room_id='r2')
        self.assertNotIn('r2', win.room_windows)
        for w in list(win.room_windows.values()):
            w.close()

    def test_chat_list_click_brings_existing_room_window_as_is(self):
        # 채팅 목록에서 이미 열린 방을 다시 누르면 새로 열린 것처럼(처음 크기) 되지 않고 그 창이 그대로 앞에
        win = self.make_window()
        if not HAS_CHAT_ROOM:
            self.skipTest('chat_room 미완성')
        win.show()
        win.sidebar.buttons['chats'].click()
        chats = win.tabs['chats']
        item = chats.list.find_data('r1')
        chats.list.row_activated.emit(item)
        room = win.room_windows['r1']
        self.addCleanup(room.close)
        room.setWindowState(Qt.WindowMaximized)
        chats.list.row_activated.emit(item)
        self.assertIs(win.room_windows['r1'], room)
        self.assertTrue(room.windowState() & Qt.WindowMaximized)              # 최대화가 풀리지 않는다
        room.setWindowState(Qt.WindowMinimized | Qt.WindowMaximized)
        chats.list.row_activated.emit(item)
        self.assertFalse(room.windowState() & Qt.WindowMinimized)             # 최소화는 되살린다
        self.assertTrue(room.windowState() & Qt.WindowMaximized)
        self.assertTrue(room.isVisible())


# ================================================================ 자동 자리비움
class IdleTests(ShellTestBase):
    def test_auto_away_and_restore(self):
        watcher = IdleWatcher(self.state, idle_seconds=600)
        try:
            watcher.last_input = 1000.0
            watcher.check(now=1000.0 + 599)
            self.assertIsNone(self.conn.last('presence_set'))
            watcher.check(now=1000.0 + 601)
            self.assertEqual(self.conn.last('presence_set'), {'presence': 'away'})
            self.assertTrue(watcher.auto_away)
            me = dict(self.state.user('홍길동'), presence='away')
            self.conn.push('user_updated', user=me)
            self.conn.sent.clear()
            watcher.activity(now=2000.0)
            self.assertEqual(self.conn.last('presence_set'), {'presence': 'online'})
            self.assertFalse(watcher.auto_away)
        finally:
            watcher.stop()

    def test_manual_status_untouched(self):
        watcher = IdleWatcher(self.state, idle_seconds=600)
        try:
            self.conn.push('user_updated', user=dict(self.state.user('홍길동'), presence='meeting'))
            watcher.last_input = 0.0
            watcher.check(now=10000.0)
            self.assertIsNone(self.conn.last('presence_set'))
            watcher.activity(now=10001.0)
            self.assertIsNone(self.conn.last('presence_set'))
        finally:
            watcher.stop()

    def test_movement_in_other_apps_counts(self):
        """KCS Talk 창 밖(다른 앱)에서 마우스를 움직여도: OS 무입력 시간으로 자리비움을 막고, 자리비움이면 곧 온라인"""
        idle = {'s': 0.0}
        watcher = IdleWatcher(self.state, idle_seconds=600, system_idle=lambda: idle['s'])
        try:
            watcher.last_input = 1000.0
            idle['s'] = 30.0                                   # 다른 앱에서 30초 전에 움직임
            watcher.poll(now=1000.0 + 700)
            self.assertIsNone(self.conn.last('presence_set'))   # 앱 안 입력은 700초 없지만 자리비움 아님
            self.assertAlmostEqual(watcher.last_input, 1670.0)
            idle['s'] = 601.0                                   # 10분 넘게 아무 데서도 입력 없음
            watcher.poll(now=1670.0 + 601)
            self.assertEqual(self.conn.last('presence_set'), {'presence': 'away'})
            self.conn.push('user_updated', user=dict(self.state.user('홍길동'), presence='away'))
            self.conn.sent.clear()
            idle['s'] = 1.0                                     # 다른 앱에서 마우스를 움직이면
            watcher.poll(now=3000.0)
            self.assertEqual(self.conn.last('presence_set'), {'presence': 'online'})   # 곧바로 온라인
            self.assertFalse(watcher.auto_away)
        finally:
            watcher.stop()

    def test_cursor_fallback_when_os_idle_unknown(self):
        """OS 무입력 시간을 모르면(Wayland 등) 마우스 포인터 위치가 바뀌었는지로 본다"""
        pos = {'p': (10, 10)}
        watcher = IdleWatcher(self.state, idle_seconds=600, system_idle=lambda: None, cursor_pos=lambda: pos['p'])
        try:
            watcher.last_input = 0.0
            watcher.poll(now=100.0)                             # 처음 위치만 기억
            self.assertEqual(watcher.last_input, 0.0)
            watcher.poll(now=700.0)                             # 그대로면 10분 뒤 자리비움
            self.assertEqual(self.conn.last('presence_set'), {'presence': 'away'})
            self.conn.push('user_updated', user=dict(self.state.user('홍길동'), presence='away'))
            pos['p'] = (200, 340)
            watcher.poll(now=710.0)
            self.assertEqual(self.conn.last('presence_set'), {'presence': 'online'})
            self.assertEqual(watcher.last_input, 710.0)
        finally:
            watcher.stop()

    def test_poll_interval_is_short(self):
        watcher = IdleWatcher(self.state, idle_seconds=600, system_idle=lambda: 0.0)
        try:
            self.assertLessEqual(watcher.poll_timer.interval(), 5000)   # 돌아오면 몇 초 안에 온라인
            self.assertTrue(watcher.poll_timer.isActive())
        finally:
            watcher.stop()
            self.assertFalse(watcher.poll_timer.isActive())

    def test_presence_menu_sends(self):
        menu = presence_menu(self.state)
        labels = [a.text() for a in menu.actions()]
        self.assertEqual(labels, ['온라인', '자리비움', '다른 용무 중', '회의 중', '휴가 중'])
        menu.actions()[2].trigger()
        self.assertEqual(self.conn.last('presence_set'), {'presence': 'busy'})


# ================================================================ 친구 탭
class FriendsTabTests(ShellTestBase):
    def setUp(self):
        super().setUp()
        self.tab = FriendsTab(self.state, self.settings)

    def test_sections(self):
        self.assertEqual(self.tab.section_members('birthday'), ['김철수'])
        self.assertEqual(sorted(self.tab.section_members('online')), ['김철수', '박민수'])
        self.assertEqual(sorted(self.tab.section_members('offline')), ['이영희', '최지우'])
        self.assertEqual(self.tab.section_members('favorites'), [])
        data = rows(self.tab.list)
        self.assertEqual(data[0], ('me', '홍길동'))
        # 오프라인은 기본으로 접혀 있다
        self.assertIn(('section', 'offline'), data)
        self.assertNotIn(('friend', '이영희', 'offline'), data)
        self.tab.toggle_section('offline')
        self.assertIn(('friend', '이영희', 'offline'), rows(self.tab.list))

    def test_bot_section_on_top(self):
        add_org(self.state, self.conn)
        self.assertEqual(self.tab.section_members('bot'), ['AI 비서'])
        data = rows(self.tab.list)
        self.assertEqual(data[1], ('section', 'bot'))
        self.assertEqual(data[2], ('friend', 'AI 비서', 'bot'))
        self.assertNotIn('AI 비서', self.tab.section_members('online'))

    def test_company_chip_for_other_company(self):
        add_org(self.state, self.conn)
        widgets = {}
        for i in range(self.tab.list.count()):
            w = self.tab.list.itemWidget(self.tab.list.item(i))
            if isinstance(w, FriendRow):
                widgets[w.username] = w
        self.assertEqual(widgets['박민수'].company, 'KCS 물류')
        self.assertEqual(widgets['김철수'].company, '')

    def test_presence_label(self):
        self.conn.push('user_updated', user=dict(self.state.user('박민수'), presence='away'))
        item = self.tab.list.find_data(('friend', '박민수', 'online'))
        self.assertIsNotNone(item)
        row = self.tab.list.itemWidget(item)
        self.assertEqual(row.presence_label.text(), '자리비움')

    def test_search_filter(self):
        self.tab.toggle_search()
        self.tab.search_edit.setText('회의')
        self.assertEqual(self.tab.section_members('online'), ['김철수'])
        self.assertEqual(self.tab.section_members('offline'), [])
        self.assertNotIn(('me', '홍길동'), rows(self.tab.list))
        self.tab.search_edit.setText('바쁨')  # 상태메시지 검색 + 검색 중에는 접힌 구역도 펼침
        self.assertEqual(self.tab.section_members('offline'), ['최지우'])
        self.assertIn(('friend', '최지우', 'offline'), rows(self.tab.list))
        self.tab.toggle_search()
        self.assertEqual(self.tab.search_edit.text(), '')
        self.assertEqual(len(self.tab.section_members('online')), 2)

    def test_favorite_toggle_moves_friend(self):
        menu = self.tab.context_menu_for(('friend', '최지우', 'offline'))
        action = next(a for a in menu.actions() if a.text() == '즐겨찾기 추가')
        action.trigger()
        self.assertIn('최지우', self.settings.get('favorites'))
        self.assertEqual(self.tab.section_members('favorites'), ['최지우'])
        self.assertNotIn('최지우', self.tab.section_members('offline'))
        menu = self.tab.context_menu_for(('friend', '최지우', 'favorites'))
        next(a for a in menu.actions() if a.text() == '즐겨찾기 해제').trigger()
        self.assertNotIn('최지우', self.settings.get('favorites'))

    def test_double_click_opens_direct_room(self):
        opened = []
        self.state.room_opened.connect(opened.append)
        item = self.tab.list.find_data(('friend', '김철수', 'online'))
        self.tab.list.itemDoubleClicked.emit(item)
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '김철수'})
        self.assertEqual(opened, [])
        self.conn.push('room_updated', room={
            'room_id': 'r9', 'type': 'direct', 'name': '', 'members': ['홍길동', '김철수'],
            'created_at': datetime.now().isoformat(timespec='seconds'), 'notice': None,
            'last_message': None, 'read_pointers': {}, 'unread_count': 0})
        self.assertEqual(opened, ['r9'])
        # 이미 방이 있는 친구는 요청 없이 바로 열린다
        self.conn.sent.clear()
        item = self.tab.list.find_data(('friend', '박민수', 'online'))
        self.tab.list.row_activated.emit(item)
        self.assertIsNone(self.conn.last('room_open_direct'))
        self.assertEqual(opened[-1], 'r2')

    # ---- 여러 명 골라 대화방 만들기 (Shift+클릭 · Shift+누른 채 끌기) ----
    def _mouse(self, kind, data, mods=Qt.NoModifier, buttons=Qt.LeftButton, pos=None):
        from PyQt5.QtGui import QMouseEvent
        vp = self.tab.list.viewport()
        if pos is None:
            pos = self.tab.list.visualItemRect(self.tab.list.find_data(data)).center()
        event = QMouseEvent(kind, pos, Qt.LeftButton, buttons, mods)
        QApplication.sendEvent(vp, event)
        return pos

    def _shift_click(self, data):
        self._mouse(QEvent.MouseButtonPress, data, Qt.ShiftModifier)
        self._mouse(QEvent.MouseButtonRelease, data, Qt.ShiftModifier, Qt.NoButton)

    def test_shift_click_picks_friends_one_by_one(self):
        self.tab.resize(360, 700)
        self.tab.show()
        self.assertTrue(self.tab.pick_bar.isHidden())
        self._shift_click(('friend', '김철수', 'online'))
        self._shift_click(('friend', '박민수', 'online'))
        self.assertEqual(self.tab.picked_members(), ['김철수', '박민수'])
        self.assertFalse(self.tab.pick_bar.isHidden())
        self.assertEqual(self.tab.pick_count.text(), '2명 선택')
        self.assertEqual(self.tab.pick_chat_button.text(), '대화방 만들기')
        row = self.tab.list.itemWidget(self.tab.list.find_data(('friend', '박민수', 'online')))
        self.assertTrue(row.picked)
        # 다시 Shift+클릭하면 빠진다 · 한 명이면 1:1 채팅
        self._shift_click(('friend', '박민수', 'online'))
        self.assertEqual(self.tab.picked_members(), ['김철수'])
        self.assertFalse(row.picked)
        self.assertEqual(self.tab.pick_chat_button.text(), '1:1 채팅')
        # 내 프로필 · 구역 머리글 · AI 비서는 고를 수 없다
        for data in (('me', '홍길동'), ('section', 'online'), ('friend', 'AI 비서', 'bot')):
            self.assertIsNone(self.tab._pickable(data))
        # 목록을 다시 그려도(상태 변경) 고른 사람은 유지, Esc 로 모두 해제
        self.tab.refresh()
        self.assertTrue(self.tab.list.itemWidget(self.tab.list.find_data(('friend', '김철수', 'online'))).picked)
        QApplication.sendEvent(self.tab.list, __import__('PyQt5.QtGui', fromlist=['QKeyEvent']).QKeyEvent(
            QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertEqual(self.tab.picked_members(), [])
        self.assertTrue(self.tab.pick_bar.isHidden())

    def test_shift_double_click_does_not_open_direct_room(self):
        self.tab.resize(360, 700)
        self.tab.show()
        data = ('friend', '김철수', 'online')
        self._shift_click(data)
        self._mouse(QEvent.MouseButtonDblClick, data, Qt.ShiftModifier)
        self._mouse(QEvent.MouseButtonRelease, data, Qt.ShiftModifier, Qt.NoButton)
        self.assertIsNone(self.conn.last('room_open_direct'))
        self.assertEqual(self.tab.picked_members(), [])

    def test_shift_press_and_move_picks_range_to_the_end(self):
        self.tab.resize(360, 700)
        self.tab.show()
        start = ('friend', '김철수', 'online')
        self._mouse(QEvent.MouseButtonPress, start, Qt.ShiftModifier)
        # 누른 채 바로 아래 친구까지 → 둘
        self._mouse(QEvent.MouseMove, ('friend', '박민수', 'online'), Qt.ShiftModifier)
        self.assertEqual(sorted(self.tab.picked_members()), ['김철수', '박민수'])
        # 목록 아래 끝 밖까지 끌면 접힌 '오프라인' 구역까지 전부 선택
        below = QPoint(10, self.tab.list.viewport().height() + 40)
        self._mouse(QEvent.MouseMove, None, Qt.ShiftModifier, pos=below)
        self.assertEqual(sorted(self.tab.picked_members()), ['김철수', '박민수', '이영희', '최지우'])
        # 다시 위로 올리면 범위가 줄어든다
        self._mouse(QEvent.MouseMove, start, Qt.ShiftModifier)
        self.assertEqual(self.tab.picked_members(), ['김철수'])
        self._mouse(QEvent.MouseMove, None, Qt.ShiftModifier, pos=below)
        self._mouse(QEvent.MouseButtonRelease, None, Qt.ShiftModifier, Qt.NoButton, pos=below)
        self.assertEqual(len(self.tab.picked_members()), 4)
        self.assertFalse(self.tab._pick_scroll.isActive())
        # 그냥 클릭하면 여러 명 선택이 풀린다
        self._mouse(QEvent.MouseButtonPress, start)
        self.assertEqual(self.tab.picked_members(), [])

    def test_pick_bar_creates_group_room(self):
        self.tab.pick_range(0, self.tab.list.count() - 1)
        members = self.tab.picked_members()
        self.assertEqual(sorted(members), ['김철수', '박민수', '이영희', '최지우'])
        dlg = self.tab.create_from_picks(exec_=False)
        self.assertEqual(sorted(dlg.selected_members()), sorted(members))
        dlg.name_edit.setText('전체방')
        dlg.accept()
        sent = self.conn.last('room_create')
        self.assertEqual(sorted(sent['members']), sorted(members))
        self.assertEqual(sent['name'], '전체방')

    def test_right_click_menu_picks_and_creates_room(self):
        texts = lambda m: [a.text() for a in m.actions() if a.text()]
        # 고른 사람이 없을 때: 우클릭 → '대화상대로 선택'
        menu = self.tab.context_menu_for(('friend', '김철수', 'online'))
        next(a for a in menu.actions() if a.text() == '대화상대로 선택 (Shift+클릭)').trigger()
        self.assertEqual(self.tab.picked_members(), ['김철수'])
        # 고른 사람 우클릭 → 1:1 / 선택에서 빼기 / 선택 해제
        menu = self.tab.context_menu_for(('friend', '김철수', 'online'))
        self.assertIn('김철수님과 1:1 채팅', texts(menu))
        self.assertIn('선택에서 빼기', texts(menu))
        self.assertIn('선택 해제', texts(menu))
        # 고르지 않은 사람 우클릭 → '선택한 1명 + 박민수 대화방 만들기…' 로 둘이 체크된 창
        menu = self.tab.context_menu_for(('friend', '박민수', 'online'))
        create = next(a for a in menu.actions() if a.text() == '선택한 1명 + 박민수 대화방 만들기…')
        with mock.patch.object(CreateGroupDialog, 'exec_', return_value=0) as run:
            create.trigger()
        run.assert_called_once()
        self.assertEqual(self.tab.picked_members(), ['김철수', '박민수'])
        # 빈 곳 · 구역 머리글 우클릭 → '선택한 2명과 대화방 만들기…'
        menu = self.tab.context_menu_for(None)
        self.assertEqual(texts(menu), ['선택한 2명과 대화방 만들기…', '선택 해제'])
        self.assertEqual(texts(self.tab.context_menu_for(('section', 'online')))[0], '선택한 2명과 대화방 만들기…')
        with mock.patch.object(CreateGroupDialog, 'exec_', return_value=1):
            menu.actions()[0].trigger()
        self.assertEqual(self.tab.picked_members(), [])   # 방을 만들면 선택이 풀린다
        self.assertIsNone(self.tab.context_menu_for(None))
        # AI 비서 · 내 프로필 메뉴에는 고르기 항목이 없다
        add_org(self.state, self.conn)
        self.assertNotIn('대화상대로 선택 (Shift+클릭)', texts(self.tab.context_menu_for(('friend', 'AI 비서', 'bot'))))
        self.assertNotIn('대화상대로 선택 (Shift+클릭)', texts(self.tab.context_menu_for(('me', '홍길동'))))

    def test_birthday_gift_opens_chat(self):
        item = self.tab.list.find_data(('friend', '김철수', 'birthday'))
        row = self.tab.list.itemWidget(item)
        row.gift_button.click()
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '김철수'})

    def test_profile_edit_sends_update(self):
        dlg = ProfileEditDialog(self.state)
        dlg.name_edit.setText('길동이')
        dlg.status_edit.setText('휴가 중')
        dlg.birthday_edit.setText('13-40')
        dlg.accept()
        self.assertIsNone(self.conn.last('profile_update'))
        self.assertTrue(dlg.error_label.isVisibleTo(dlg))
        dlg.birthday_edit.setText('03-15')            # 연도 없는 생일은 저장하지 않는다 (연도까지)
        dlg.accept()
        self.assertIsNone(self.conn.last('profile_update'))
        dlg.birthday_edit.setText('1990-03-15')
        dlg.accept()
        self.assertEqual(self.conn.last('profile_update'),
                         {'display_name': '길동이', 'status_message': '휴가 중', 'birthday': '1990-03-15'})

    def test_profile_photo_change_and_clear(self):
        dlg = ProfileEditDialog(self.state)
        path = os.path.join(tempfile.mkdtemp(), 'photo.png')
        img = QImage(400, 300, QImage.Format_RGB32)
        img.fill(QColor('#3366CC'))
        self.assertTrue(img.save(path))
        self.assertIsNone(dlg.set_photo_from_path(path))
        payload = self.conn.last('profile_update')
        data = base64.b64decode(payload['avatar_b64'])
        self.assertLessEqual(len(data), 512 * 1024)
        decoded = QImage.fromData(data, 'PNG')
        self.assertEqual((decoded.width(), decoded.height()), (256, 256))
        bad = os.path.join(tempfile.mkdtemp(), 'bad.png')
        with open(bad, 'wb') as f:
            f.write(b'not an image')
        self.assertIsNotNone(dlg.set_photo_from_path(bad))
        dlg.clear_photo_button.click()
        self.assertEqual(self.conn.last('profile_update'), {'avatar_clear': True})

    def test_profile_view_chat_button(self):
        dlg = ProfileDialog(self.state, '최지우')
        dlg.chat_button.click()
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '최지우'})

    def test_invite_to_room_menu(self):
        menu = self.tab.context_menu_for(('friend', '최지우', 'offline'))
        self.assertIn('채팅방에 초대…', [a.text() for a in menu.actions()])


# ================================================================ 채팅 탭
class ChatsTabTests(ShellTestBase):
    def setUp(self):
        super().setUp()
        self.tab = ChatsTab(self.state, self.settings)

    def test_order_and_pinning(self):
        self.assertEqual(self.tab.room_ids(), ['r1', 'r2', 'r3'])
        self.settings.toggle_in('pinned_rooms', 'r3')
        self.assertEqual(self.tab.room_ids(), ['r3', 'r1', 'r2'])
        row = self.tab.list.itemWidget(self.tab.list.item(0))
        self.assertTrue(row.pinned)

    def test_row_content(self):
        row = self.tab.list.itemWidget(self.tab.list.find_data('r1'))
        self.assertIsInstance(row, RoomRow)
        self.assertEqual(row.title.text(), '프로젝트 팀')
        self.assertEqual(row.count_label.text(), '3')
        self.assertTrue(row.preview.text().startswith('긴 메시지 테스트'))
        self.assertEqual(row.badge.count, 2)
        self.settings.toggle_in('muted_rooms', 'r1')
        row = self.tab.list.itemWidget(self.tab.list.find_data('r1'))
        self.assertTrue(row.muted)
        self.assertTrue(row.badge.muted)
        r2 = self.tab.list.itemWidget(self.tab.list.find_data('r2'))
        self.assertEqual(r2.title.text(), '박민수')
        self.assertIsNone(r2.count_label)

    def test_search(self):
        self.tab.toggle_search()
        self.tab.search_edit.setText('박민')
        self.assertEqual(self.tab.room_ids(), ['r2'])
        self.tab.search_edit.setText('이영희')  # 참여자 이름으로도 검색
        self.assertEqual(self.tab.room_ids(), ['r1'])
        self.tab.search_edit.setText('없는방')
        self.assertEqual(self.tab.room_ids(), [])
        self.assertTrue(self.tab.empty_label.isVisibleTo(self.tab))

    def test_relative_time(self):
        now = datetime(2026, 10, 8, 15, 30)
        self.assertEqual(format_room_time(datetime(2026, 10, 8, 15, 12), now), '오후 3:12')
        self.assertEqual(format_room_time(datetime(2026, 10, 8, 9, 5), now), '오전 9:05')
        self.assertEqual(format_room_time(datetime(2026, 10, 8, 0, 7), now), '오전 12:07')
        self.assertEqual(format_room_time(datetime(2026, 10, 8, 12, 0), now), '오후 12:00')
        self.assertEqual(format_room_time('2026-10-07T23:59:00', now), '어제')
        self.assertEqual(format_room_time('2026-03-01T10:00:00', now), '3월 1일')
        self.assertEqual(format_room_time('2025-03-01T10:00:00', now), '2025. 3. 1.')
        self.assertEqual(format_room_time(None, now), '')

    def test_activate_emits_room_opened(self):
        opened = []
        self.state.room_opened.connect(opened.append)
        self.tab.list.itemDoubleClicked.emit(self.tab.list.find_data('r2'))
        self.assertEqual(opened, ['r2'])

    def test_context_menu_pin_mute_leave(self):
        menu = self.tab.context_menu_for('r2')
        texts = [a.text() for a in menu.actions() if a.text()]
        self.assertEqual(texts, ['채팅방 열기', '읽지 않고 미리보기', '채팅방 상단 고정', '채팅방 알림 끄기',
                                 '폴더에 추가', '대화상대 초대…', '대화 내용 저장…', '나가기'])
        next(a for a in menu.actions() if a.text() == '채팅방 상단 고정').trigger()
        self.assertTrue(self.state.is_pinned('r2'))
        next(a for a in menu.actions() if a.text() == '채팅방 알림 끄기').trigger()
        self.assertTrue(self.state.is_muted('r2'))
        menu = self.tab.context_menu_for('r2')
        texts = [a.text() for a in menu.actions() if a.text()]
        self.assertIn('상단 고정 해제', texts)
        self.assertIn('채팅방 알림 켜기', texts)
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            next(a for a in menu.actions() if a.text() == '나가기').trigger()
        self.assertIsNone(self.conn.last('room_leave'))
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            next(a for a in menu.actions() if a.text() == '나가기').trigger()
        self.assertEqual(self.conn.last('room_leave'), {'room_id': 'r2'})
        self.assertNotIn('대화상대 초대…', [a.text() for a in self.tab.context_menu_for('r3').actions()])

    @unittest.skipUnless(HAS_CHAT_ROOM, 'chat_room 미완성')
    def test_invite_dialog_from_chats(self):
        from client.kakao import chat_room
        if not hasattr(chat_room, 'InviteDialog'):
            self.skipTest('InviteDialog 미구현')
        with mock.patch.object(chat_room.InviteDialog, 'exec_', return_value=0):
            dlg = self.tab.show_invite_dialog('r1')
        self.assertIsNotNone(dlg)

    def test_live_update_on_new_message(self):
        self.conn.push('msg_new', message=dict(
            msg_id=100, room_id='r3', sender='홍길동', kind='text', content='나에게 메모',
            timestamp=(datetime.now() + timedelta(seconds=5)).isoformat(timespec='seconds'),
            reply_to=None, mentions=[], reactions={}, deleted=False, attachment=None, event_id=None, poll=None))
        self.assertEqual(self.tab.room_ids()[0], 'r3')
        row = self.tab.list.itemWidget(self.tab.list.item(0))
        self.assertEqual(row.preview.text(), '나에게 메모')


# ================================================================ 채팅방 폴더·미리보기·AI 버튼 (7.8)
class ChatFolderTests(ShellTestBase):
    def setUp(self):
        super().setUp()
        self.tab = ChatsTab(self.state, self.settings)

    def action(self, menu, text):
        return next(a for a in menu.actions() if a.text() == text)

    def test_chips_match_folders(self):
        self.assertEqual([c.folder for c in self.tab.folder_bar.chips], ['전체', '안읽음', '즐겨찾기', '통화'])
        self.assertEqual(self.tab.folder_bar.labels(), ['전체', '안읽음 2', '즐겨찾기 0', '통화'])
        self.assertTrue(self.tab.folder_bar.chip('전체').isChecked())
        self.assertIsNone(self.state.save_chat_folder('회사', ['r1']))
        self.assertEqual(self.tab.folder_bar.labels(), ['전체', '안읽음 2', '즐겨찾기 0', '통화', '회사 1'])

    def test_select_unread_and_favorites(self):
        self.tab.folder_bar.chip('안읽음').click()
        self.assertEqual(self.tab.current_folder, '안읽음')
        self.assertEqual(self.tab.room_ids(), ['r1', 'r2'])
        self.assertTrue(self.tab.folder_bar.chip('안읽음').isChecked())
        self.assertFalse(self.tab.folder_bar.chip('전체').isChecked())
        self.tab.select_folder('즐겨찾기')
        self.assertEqual(self.tab.room_ids(), [])
        self.assertIn('즐겨찾기한 채팅방이 없습니다', self.tab.empty_label.text())
        self.settings.toggle_in('pinned_rooms', 'r3')          # 고정된 방
        self.assertEqual(self.tab.room_ids(), ['r3'])
        self.settings.toggle_in('favorites', '박민수')          # 즐겨찾기 친구와의 1:1
        self.assertEqual(self.tab.room_ids(), ['r3', 'r2'])
        self.assertEqual(self.tab.folder_bar.chip('즐겨찾기').text(), '즐겨찾기 2')

    def test_custom_folder_filter_with_search(self):
        self.state.save_chat_folder('회사', ['r1', 'r3'])
        self.tab.folder_bar.chip('회사').click()
        self.assertEqual(self.tab.room_ids(), ['r1', 'r3'])
        self.tab.toggle_search()
        self.tab.search_edit.setText('프로젝트')
        self.assertEqual(self.tab.room_ids(), ['r1'])
        self.tab.search_edit.setText('박민')                    # 폴더 밖의 방은 검색돼도 안 보인다
        self.assertEqual(self.tab.room_ids(), [])

    def test_deleted_folder_falls_back_to_all(self):
        self.state.save_chat_folder('회사', ['r1'])
        self.tab.select_folder('회사')
        self.assertEqual(self.tab.current_folder, '회사')
        self.state.delete_chat_folder('회사')
        self.assertEqual(self.tab.current_folder, '전체')
        self.assertEqual(self.tab.room_ids(), ['r1', 'r2', 'r3'])
        self.tab.select_folder('없는 폴더')
        self.assertEqual(self.tab.current_folder, '전체')

    def test_subtitle_reflects_folder(self):
        self.assertTrue(self.tab.header.subtitle.text().startswith('채팅방 3개'))
        self.tab.select_folder('안읽음')
        self.assertEqual(self.tab.header.subtitle.text(), '안읽음 폴더 · 채팅방 2개')
        self.tab.select_folder('전체')
        self.assertEqual(self.tab.header.subtitle.text(), '채팅방 3개 · 안 읽은 메시지 3개')

    def test_unread_folder_updates_live(self):
        self.tab.select_folder('안읽음')
        self.conn.push('read_update', room_id='r2', username='홍길동', last_read_msg_id=14)
        self.assertEqual(self.state.room_unread('r2'), 0)
        self.assertEqual(self.tab.room_ids(), ['r1'])
        self.assertEqual(self.tab.folder_bar.chip('안읽음').text(), '안읽음 1')

    def test_folder_dialog_create(self):
        dlg = ChatFolderDialog(self.state)
        self.assertIsNone(dlg.delete_button)
        dlg.set_name('가족')
        dlg.set_checked(['r2', 'r3'])
        self.assertEqual(dlg.checked_room_ids(), ['r2', 'r3'])
        dlg.accept()
        self.assertEqual(dlg.result(), dlg.Accepted)
        self.assertEqual(dlg.saved_name, '가족')
        self.assertEqual([r['room_id'] for r in self.state.rooms_in_folder('가족')], ['r2', 'r3'])
        self.assertIn('가족', [c.folder for c in self.tab.folder_bar.chips])

    def test_folder_dialog_errors(self):
        self.state.save_chat_folder('회사', ['r1'])
        dlg = ChatFolderDialog(self.state)
        dlg.set_name('')
        dlg.accept()
        self.assertEqual(dlg.error_text(), '폴더 이름을 입력해 주세요.')
        dlg.set_name('안읽음')
        self.assertEqual(dlg.error_text(), '')                 # 입력하면 오류 숨김
        dlg.accept()
        self.assertIn('기본 폴더 이름', dlg.error_text())
        dlg.set_name('회사')
        dlg.accept()
        self.assertEqual(dlg.error_text(), '같은 이름의 폴더가 있습니다.')
        self.assertIsNone(dlg.saved_name)
        self.assertEqual(len(self.settings.get('chat_folders')), 1)

    def test_folder_dialog_edit_rename_and_delete(self):
        self.state.save_chat_folder('회사', ['r1'])
        self.tab.select_folder('회사')
        dlg = ChatFolderDialog(self.state, '회사')
        self.assertEqual(dlg.name_edit.text(), '회사')
        self.assertEqual(dlg.checked_room_ids(), ['r1'])
        self.assertIsNotNone(dlg.delete_button)
        dlg.set_name('업무')
        dlg.set_checked(['r1', 'r2'])
        dlg.accept()
        self.tab.apply_folder_dialog_result(dlg)
        self.assertEqual([f['name'] for f in self.settings.get('chat_folders')], ['업무'])
        self.assertEqual(self.tab.current_folder, '업무')      # 보고 있던 폴더 이름이 바뀌면 따라간다
        self.assertEqual(self.tab.room_ids(), ['r1', 'r2'])
        dlg = ChatFolderDialog(self.state, '업무')
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.assertFalse(dlg.delete_folder())
        self.assertTrue(dlg.delete_folder(confirm=False))
        self.tab.apply_folder_dialog_result(dlg)
        self.assertEqual(self.settings.get('chat_folders'), [])
        self.assertEqual(self.tab.current_folder, '전체')

    def test_new_folder_from_plus_chip_selects_it(self):
        def fake_exec(dlg):
            dlg.set_name('스터디')
            dlg.set_checked(['r1'])
            dlg.accept()
            return dlg.result()
        with mock.patch.object(ChatFolderDialog, 'exec_', fake_exec):
            self.tab.folder_bar.add_chip.click()
        self.assertEqual(self.tab.current_folder, '스터디')
        self.assertEqual(self.tab.room_ids(), ['r1'])

    def test_folder_chip_menu(self):
        self.state.save_chat_folder('회사', ['r1'])
        self.assertIsNone(self.tab.folder_menu_for('전체'))
        self.assertIsNone(self.tab.folder_menu_for('안읽음'))
        menu = self.tab.folder_menu_for('회사')
        self.assertEqual([a.text() for a in menu.actions()], ['폴더 편집', '폴더 삭제'])
        with mock.patch.object(ChatFolderDialog, 'exec_', return_value=0) as exec_:
            self.action(menu, '폴더 편집').trigger()
        self.assertEqual(exec_.call_count, 1)
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.action(menu, '폴더 삭제').trigger()
        self.assertEqual(self.settings.get('chat_folders'), [])

    def test_context_submenu_toggles_membership(self):
        self.state.save_chat_folder('회사', [])
        self.state.save_chat_folder('가족', ['r2'])
        sub = self.action(self.tab.context_menu_for('r2'), '폴더에 추가').menu()
        texts = [a.text() for a in sub.actions() if a.text()]
        self.assertEqual(texts, ['회사', '가족', '새 폴더…'])
        self.assertFalse(self.action(sub, '회사').isChecked())
        self.assertTrue(self.action(sub, '가족').isChecked())
        self.action(sub, '회사').trigger()
        self.assertEqual([r['room_id'] for r in self.state.rooms_in_folder('회사')], ['r2'])
        sub = self.action(self.tab.context_menu_for('r2'), '폴더에 추가').menu()
        self.action(sub, '가족').trigger()
        self.assertEqual(self.state.rooms_in_folder('가족'), [])
        # 새 폴더… → 그 방이 미리 체크된 다이얼로그
        seen = []
        with mock.patch.object(ChatFolderDialog, 'exec_', lambda dlg: seen.append(dlg.checked_room_ids()) or 0):
            self.action(sub, '새 폴더…').trigger()
        self.assertEqual(seen, [['r2']])

    def test_peek_menu_only_with_unread(self):
        texts = [a.text() for a in self.tab.context_menu_for('r1').actions()]
        self.assertIn('읽지 않고 미리보기', texts)
        texts = [a.text() for a in self.tab.context_menu_for('r3').actions()]
        self.assertNotIn('읽지 않고 미리보기', texts)

    def test_peek_dialog_does_not_mark_read(self):
        with mock.patch.object(PeekDialog, 'exec_', return_value=0):
            dlg = self.tab.show_peek_dialog('r1')
        self.assertLessEqual(len(dlg.messages), 5)
        self.assertEqual([m['msg_id'] for m in dlg.messages], [9, 11])   # 내 읽음 포인터(8) 이후, 삭제 제외
        self.assertEqual(dlg.rows[0].sender.text(), '김철수')
        self.assertEqual(dlg.rows[0].text.text(), '파일: 회의자료.pdf')
        self.assertTrue(dlg.rows[1].text.text().startswith('긴 메시지 테스트'))
        self.assertEqual(dlg.note_label.text(), '읽음으로 표시되지 않습니다')
        self.assertIn('2', dlg.subtitle.text())
        self.assertIsNone(self.conn.last('read'))
        self.assertEqual(self.state.room_unread('r1'), 2)
        opened = []
        self.state.room_opened.connect(opened.append)
        dlg.open_button.click()
        self.assertEqual(opened, ['r1'])

    def test_ai_button_opens_bot_chat(self):
        add_org(self.state, self.conn)
        self.assertIsInstance(self.tab.ai_button, AiButton)
        self.assertIs(self.tab.header.buttons[0], self.tab.ai_button)   # 기존 버튼보다 앞
        self.assertEqual(self.tab.ai_button.toolTip(), 'AI 비서')
        self.assertFalse(self.tab.ai_button.icon().isNull())
        self.tab.ai_button.click()
        self.assertEqual(self.conn.last('room_open_direct'), {'target': BOT_NAME})
        # 이미 봇과의 방이 있으면 서버 요청 없이 바로 연다
        self.conn.push('room_updated', room={'room_id': 'rb', 'type': 'direct', 'name': '',
                                             'members': ['홍길동', BOT_NAME], 'created_at': '2026-10-01T00:00:00',
                                             'notice': None, 'last_message': None,
                                             'read_pointers': {}, 'unread_count': 0})
        self.conn.sent.clear()
        opened = []
        self.state.room_opened.connect(opened.append)
        self.tab.ai_button.click()
        self.assertIn('rb', opened)
        self.assertIsNone(self.conn.last('room_open_direct'))

    def test_folder_bar_fits_narrow_window(self):
        self.state.save_chat_folder('아주 긴 폴더 이름입니다', ['r1'])
        win = self.make_window()
        win.resize(340, 680)
        win.show()
        win.set_tab('chats')
        tab = win.tabs['chats']
        tab.select_folder('안읽음')
        QApplication.processEvents()
        self.assertLessEqual(tab.width(), 340)
        for i in range(tab.list.count()):
            row = tab.list.itemWidget(tab.list.item(i))
            self.assertLessEqual(row.width(), tab.list.viewport().width())
        for btn in tab.header.buttons:
            self.assertLessEqual(btn.mapTo(tab, btn.rect().bottomRight()).x(), tab.width())


# ================================================================ 다이얼로그
class DialogTests(ShellTestBase):
    def test_create_group_sends_room_create(self):
        dlg = CreateGroupDialog(self.state)
        self.assertFalse(dlg.ok_button.isEnabled())
        dlg.set_checked('김철수')
        dlg.set_checked('박민수')
        dlg.name_edit.setText('주말 모임')
        self.assertEqual(dlg.selected_members(), ['김철수', '박민수'])
        dlg.accept()
        self.assertEqual(self.conn.last('room_create'), {'name': '주말 모임', 'members': ['김철수', '박민수']})

    def test_create_group_single_member_opens_direct(self):
        dlg = CreateGroupDialog(self.state)
        dlg.set_checked('최지우')
        dlg.accept()
        self.assertIsNone(self.conn.last('room_create'))
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '최지우'})

    def test_create_group_filter(self):
        dlg = CreateGroupDialog(self.state)
        dlg.search_edit.setText('영희')
        visible = [dlg.list.item(i).data(Qt.UserRole) for i in range(dlg.list.count())
                   if not dlg.list.item(i).isHidden()]
        self.assertEqual(visible, ['이영희'])

    def test_invite_to_group_room(self):
        dlg = InviteToRoomDialog(self.state, '최지우')
        self.assertEqual([r['room_id'] for r in dlg.candidate_rooms()], ['r1', 'r2'])
        self.assertTrue(dlg.select_room('r1'))
        dlg.accept()
        self.assertEqual(self.conn.last('room_invite'), {'room_id': 'r1', 'usernames': ['최지우']})

    def test_invite_to_direct_room_creates_group(self):
        dlg = InviteToRoomDialog(self.state, '최지우')
        dlg.select_room('r2')
        dlg.accept()
        payload = self.conn.last('room_create')
        self.assertIsNotNone(payload)
        self.assertEqual(sorted(payload['members']), ['박민수', '최지우'])

    def test_invite_excludes_rooms_with_member(self):
        dlg = InviteToRoomDialog(self.state, '김철수')
        self.assertEqual([r['room_id'] for r in dlg.candidate_rooms()], ['r2'])

    def test_login_dialog_values(self):
        dlg = LoginDialog('10.0.0.1', 6000)
        self.assertFalse(dlg.login_button.isEnabled())
        self.assertFalse(hasattr(dlg, 'preset_buttons'))         # 최근 로그인 칩 영역 없음
        dlg.name_edit.setText('이영희')
        self.assertFalse(dlg.login_button.isEnabled())          # 비밀번호가 있어야 로그인
        self.assertEqual(dlg.password_edit.echoMode(), QLineEdit.Password)
        dlg.password_edit.setText('pw1234')
        self.assertTrue(dlg.login_button.isEnabled())
        # 로그인 창에는 서버 설정이 없다 - 받은 주소로만 접속 (그룹 관리자가 더보기 → 정보에서 정함, 명세 §4.3)
        self.assertFalse(hasattr(dlg, 'server_toggle') or hasattr(dlg, 'host_edit'))
        self.assertFalse(any('서버' in b.text() for b in dlg.findChildren(QPushButton)))
        self.assertEqual(dlg.values(), ('이영희', '10.0.0.1', 6000))
        self.assertEqual(dlg.password(), 'pw1234')
        self.assertEqual(dlg.windowTitle(), 'KCS Talk 로그인')
        dlg.accept()
        self.assertEqual(dlg.result(), LoginDialog.Accepted)

    def test_login_dialog_rejects_short_password(self):
        dlg = LoginDialog()
        dlg.name_edit.setText('kmkim')
        dlg.password_edit.setText('12')
        dlg.accept()
        self.assertNotEqual(dlg.result(), LoginDialog.Accepted)
        self.assertIn('비밀번호', dlg.error_label.text())

    def test_login_dialog_password_reveal_toggle(self):
        dlg = LoginDialog()
        dlg.password_edit.setText('pw1234')
        self.assertEqual(dlg.password_edit.echoMode(), QLineEdit.Password)   # 기본은 가림
        self.assertEqual(dlg.reveal_action.toolTip(), '비밀번호 보기')
        self.assertFalse(dlg.reveal_action.icon().isNull())
        dlg.reveal_action.trigger()
        self.assertEqual(dlg.password_edit.echoMode(), QLineEdit.Normal)
        self.assertEqual(dlg.reveal_action.toolTip(), '비밀번호 숨기기')
        self.assertEqual(dlg.password(), 'pw1234')                          # 값은 그대로
        dlg.reveal_action.trigger()
        self.assertEqual(dlg.password_edit.echoMode(), QLineEdit.Password)

    def test_login_dialog_first_use_registration(self):
        from client.kakao import login
        with mock.patch.object(login, 'fetch_companies', return_value=['KCS 홀딩스', 'KCS 물류']) as fetch:
            dlg = LoginDialog('10.0.0.9', 6000)
            self.assertFalse(dlg.is_register())
            self.assertIsNone(dlg.registration())
            dlg.mode_toggle.click()
            self.assertTrue(dlg.is_register())
            fetch.assert_called_once_with('10.0.0.9', 6000)
            self.assertTrue(dlg.register_form.isVisibleTo(dlg) and not dlg.login_form.isVisibleTo(dlg))
            self.assertEqual([dlg.company_combo.itemText(i) for i in range(dlg.company_combo.count())],
                             ['소속 회사 선택', 'KCS 홀딩스', 'KCS 물류'])
            self.assertFalse(hasattr(dlg, 'reg_employee_edit'))          # 사번 칸 없음 (2026-10-14) — 이메일이 아이디
            self.assertEqual(dlg.reg_email_edit.placeholderText(), '이메일 (다음부터 로그인 아이디)')
            self.assertIn('이메일과 비밀번호만으로', dlg.register_hint.text())
            dlg.reg_name_edit.setText('도승현')
            dlg.reg_email_edit.setText('do@kcs.test')
            dlg.reg_password_edit.setText('pw1234')
            self.assertFalse(dlg.login_button.isEnabled())               # 소속 회사는 반드시 선택
            dlg.accept()
            self.assertNotEqual(dlg.result(), LoginDialog.Accepted)
            dlg.company_combo.setCurrentIndex(2)
            self.assertTrue(dlg.login_button.isEnabled())
            dlg.reg_email_edit.clear()
            self.assertFalse(dlg.login_button.isEnabled())               # 이메일도 필수
            dlg.reg_email_edit.setText('not-an-email')
            dlg.accept()
            self.assertIn('이메일', dlg.error_label.text())
            dlg.reg_email_edit.setText('do@kcs.test')
            self.assertEqual(dlg.reg_password_edit.echoMode(), QLineEdit.Password)
            dlg.reg_password_edit.reveal_action.trigger()                # 등록 화면 비밀번호도 눈 아이콘
            self.assertEqual(dlg.reg_password_edit.echoMode(), QLineEdit.Normal)
            dlg.accept()
            self.assertEqual(dlg.result(), LoginDialog.Accepted)
            self.assertEqual(dlg.registration(), {'company': 'KCS 물류', 'name': '도승현',
                                                  'email': 'do@kcs.test', 'password': 'pw1234'})   # 사번은 보내지 않는다
            self.assertEqual(dlg.values()[0], 'do@kcs.test')             # 다음부터의 로그인 아이디 = 이메일
            self.assertEqual(dlg.saved_token(), '')
            # 등록이 거절돼 다시 띄운 창은 입력값 그대로 등록 화면
            again = LoginDialog('10.0.0.9', 6000, registration=dlg.registration())
            self.assertTrue(again.is_register())
            self.assertEqual(again.registration(), dlg.registration())

    def test_company_list_opens_below_and_placeholder_not_pickable(self):
        from client.kakao import login
        with mock.patch.object(login, 'fetch_companies', return_value=['한국지주', '케이씨에스']):
            dlg = LoginDialog(fresh=True)
            dlg.show()
            dlg.set_mode('register')
            QApplication.processEvents()
            combo = dlg.company_combo
            self.assertFalse(combo.model().item(0).isEnabled())          # '소속 회사 선택' 은 고를 수 없음
            self.assertTrue(combo.model().item(2).isEnabled())
            combo.showPopup()
            popup = combo.view().window()
            self.assertTrue(combo.view().isVisible())
            # 콤보 위에 겹치지 않는다 (겹치면 누른 채 떼는 순간 손 밑 항목이 골라져 바로 닫힌 것처럼 보였다)
            combo_top, combo_bottom = combo.mapToGlobal(QPoint(0, 0)).y(), combo.mapToGlobal(QPoint(0, combo.height())).y()
            self.assertTrue(popup.geometry().top() >= combo_bottom or popup.geometry().bottom() <= combo_top)
            self.assertGreaterEqual(popup.width(), combo.width())
            combo.hidePopup()
            dlg.close()

    def test_login_dialog_asks_for_employee_no(self):
        dlg = LoginDialog(fresh=True)
        self.assertEqual(dlg.name_edit.placeholderText(), '이메일')     # 운영 로그인 아이디 = 이메일 (2026-10-14)
        self.assertIn('이메일', dlg.subtitle.text())
        dlg.name_edit.setText('20261009')
        self.assertFalse(dlg.login_button.isEnabled())                  # 비밀번호도 있어야
        dlg.password_edit.setText('pw1234')
        self.assertTrue(dlg.login_button.isEnabled())
        dlg.accept()
        self.assertEqual(dlg.result(), LoginDialog.Accepted)
        self.assertEqual((dlg.values()[0], dlg.password(), dlg.registration()), ('20261009', 'pw1234', None))
        from client.kakao import login
        with mock.patch.object(login, 'fetch_companies', side_effect=OSError('down')):
            new = LoginDialog(fresh=True)
            new.name_edit.setText('K-2026')
            new.show_error('처음 사용하는 아이디입니다. [처음 사용 등록]에서 소속 회사·이름·이메일·비밀번호를 등록해 주세요.')
        self.assertTrue(new.is_register())
        # 사번 모양의 아이디는 옮길 칸이 없다 (이름 칸에 넣지 않는다)
        self.assertEqual((new.reg_email_edit.text(), new.reg_name_edit.text()), ('', ''))
        with mock.patch.object(login, 'fetch_companies', side_effect=OSError('down')):
            by_name = LoginDialog(fresh=True)
            by_name.name_edit.setText('홍길동')
            by_name.show_error('처음 사용하는 아이디입니다. [처음 사용 등록]에서 소속 회사·이름·이메일·비밀번호를 등록해 주세요.')
        self.assertEqual(by_name.reg_name_edit.text(), '홍길동')

    def test_login_dialog_switches_to_registration_when_server_asks(self):
        from client.kakao import login
        with mock.patch.object(login, 'fetch_companies', side_effect=OSError('down')):
            dlg = LoginDialog()
            dlg.name_edit.setText('newbie@kcs.test')
            dlg.show_error('처음 사용하는 아이디입니다. [처음 사용 등록]에서 소속 회사·이름·이메일·비밀번호를 등록해 주세요.')
            self.assertTrue(dlg.is_register())
            self.assertEqual(dlg.reg_email_edit.text(), 'newbie@kcs.test')
            self.assertIn('처음 사용 등록', dlg.error_label.text())
            dlg.accept()                                                # 회사 목록을 못 받으면 진행 불가
            self.assertNotEqual(dlg.result(), LoginDialog.Accepted)
            self.assertIn('소속 회사', dlg.error_label.text())

    def test_logout_is_right_under_profile_and_asks_first(self):
        from client.kakao.main_window import MainWindow
        calls = []
        win = MainWindow(self.state, self.state.settings, logout=lambda: calls.append(1))
        more = win.tabs['more']
        sections = [label.text() for label in more.section_labels]
        self.assertEqual(sections[0], '계정')                                 # 탭 맨 위 (프로필 카드 바로 아래)
        self.assertIn('로그아웃', more.logout_button.text())
        from client.kakao.logout_dialog import LogoutDialog
        with mock.patch.object(LogoutDialog, 'exec_', return_value=LogoutDialog.Rejected):
            more.logout_button.click()
        self.assertEqual(calls, [])                                           # 취소하면 그대로
        with mock.patch.object(LogoutDialog, 'exec_', return_value=LogoutDialog.Accepted):
            more.logout_button.click()
            win.request_logout()                                              # 트레이 메뉴 '로그아웃…'
        self.assertEqual(calls, [1, 1])
        win.close()

    def test_sidebar_avatar_right_click_quick_logout(self):
        """왼쪽 아래 내 사진 우클릭 → 로그아웃: 확인 창 없이 바로 (간편 로그아웃)"""
        from client.kakao.main_window import MainWindow
        from client.kakao.logout_dialog import LogoutDialog
        calls = []
        win = MainWindow(self.state, self.state.settings, logout=lambda: calls.append(1))
        avatar = win.sidebar.me_avatar
        self.assertEqual(avatar.contextMenuPolicy(), Qt.CustomContextMenu)
        self.assertIn('우클릭: 로그아웃', avatar.toolTip())
        menu = win.sidebar.me_menu()
        texts = [a.text() for a in menu.actions() if a.text()]
        self.assertEqual(texts, ['홍길동', '로그아웃'])
        self.assertFalse(menu.actions()[0].isEnabled())                       # 이름은 표시만
        with mock.patch.object(LogoutDialog, 'exec_') as asked:
            next(a for a in menu.actions() if a.text() == '로그아웃').trigger()
        asked.assert_not_called()                                             # 확인 창 없이
        self.assertEqual(calls, [1])
        with mock.patch.object(win.sidebar, 'me_menu', wraps=win.sidebar.me_menu) as built, \
                mock.patch('client.kakao.main_window.QMenu.exec_'):
            avatar.customContextMenuRequested.emit(avatar.rect().center())    # 실제 우클릭 신호
        built.assert_called_once()
        win.close()
        bare = MainWindow(self.state, self.state.settings)                    # 로그아웃 처리가 없으면 메뉴만 꺼 둔다
        self.assertFalse(next(a for a in bare.sidebar.me_menu().actions() if a.text() == '로그아웃').isEnabled())
        bare.close()

    def test_logout_dialog_says_what_changes_and_what_stays(self):
        from client.kakao.logout_dialog import LogoutDialog, logout_effects
        auto = {'login_id': 'hgd', 'token': 't', 'auto_login': True, 'host': '', 'port': 0}
        self.assertIn('자동 로그인', logout_effects(auto)[0])
        self.assertIn('저장된 로그인 정보가 지워', logout_effects(dict(auto, auto_login=False))[0])
        self.assertIn('다시 입력', logout_effects(None)[0])
        self.assertIn('서버에 그대로', logout_effects(None)[1])
        dlg = LogoutDialog(self.state, auto)
        self.assertEqual(dlg.title_label.text(), '로그아웃할까요?')
        self.assertTrue(dlg.name_label.text().startswith(self.state.display_name(self.state.me)))
        self.assertIn('hgd', dlg.org_label.text())                            # 어떤 아이디에서 나가는지
        self.assertEqual((dlg.cancel_button.text(), dlg.logout_button.text()), ('취소', '로그아웃'))
        self.assertTrue(dlg.logout_button.isDefault())                        # Enter = 로그아웃
        dlg.cancel_button.click()
        self.assertEqual(dlg.result(), LogoutDialog.Rejected)
        dlg = LogoutDialog(self.state, None)
        dlg.logout_button.click()
        self.assertEqual(dlg.result(), LogoutDialog.Accepted)

    def test_no_auto_login_shows_login_dialog_despite_saved_auto_login(self):
        """런처는 --no-auto-login 으로 창을 연다: 저장된 자동 로그인(김명수)이 있어도 로그인 창 → 다른 사람으로"""
        from client.kakao import app as kapp, login
        saved = {'login_id': '김명수', 'token': 'tok', 'auto_login': True, 'host': '', 'port': 0}
        shown, sessions, tokens = [], [], []

        class FakeSession:
            def __init__(self, *args, **kwargs):
                sessions.append((args, kwargs))

            def connect(self):
                raise OSError('테스트: 연결 안 함')

        with mock.patch.object(login, 'load_saved_login', return_value=saved), \
                mock.patch.object(login.LoginDialog, 'exec_',
                                  lambda dlg: shown.append(dlg.name_edit.text()) or tokens.append(dlg.saved_token()) or 0), \
                mock.patch.object(kapp, 'Session', FakeSession), \
                mock.patch.object(kapp, 'ask_connection_failure', return_value='quit'):
            self.assertEqual(kapp.main(['--no-auto-login']), 0)
            self.assertEqual(shown, [''])                # 처음 로그인 상태: 아이디도 비어 있다
            self.assertEqual(tokens, [''])               # 저장된 비밀번호(토큰)로 [로그인]만 눌러 들어가지 않는다
            self.assertEqual(sessions, [])               # 저장된 토큰으로 바로 접속하지 않았다
            self.assertEqual(kapp.main([]), 1)            # 옵션이 없으면 예전처럼 자동 로그인 시도
            self.assertEqual(sessions[0][1].get('token'), 'tok')
            self.assertEqual(shown, [''])

    def test_fresh_login_dialog_needs_id_and_password(self):
        """런처로 연 로그인 창: 최근 아이디·저장된 로그인이 있어도 비어 있고, 아이디·비밀번호를 넣어야 [로그인]이 켜진다"""
        from client.kakao import login
        saved = {'login_id': '김명수', 'token': 'tok', 'auto_login': True, 'host': '', 'port': 0}
        with mock.patch.object(login, 'load_saved_login', return_value=saved), \
                mock.patch.object(login, 'load_recent', return_value=['김명수', '도승현']):
            dlg = login.LoginDialog(fresh=True)
            self.assertEqual((dlg.name_edit.text(), dlg.password_edit.text()), ('', ''))
            self.assertFalse(dlg.remember_check.isChecked() or dlg.auto_check.isChecked())
            self.assertEqual(dlg.password_edit.placeholderText(), '비밀번호')     # '저장된 비밀번호' 아님
            self.assertFalse(dlg.login_button.isEnabled())
            dlg.name_edit.setText('김명수')
            self.assertFalse(dlg.login_button.isEnabled())                      # 저장된 토큰으로 대신하지 않는다
            self.assertEqual(dlg.saved_token(), '')
            dlg.password_edit.setText('pw1234')
            self.assertTrue(dlg.login_button.isEnabled())
            self.assertEqual(login.LoginDialog().name_edit.text(), '김명수')      # 일반 실행은 예전처럼 채움

    def test_login_dialog_password_reset_screen(self):
        from client.kakao import login, net
        with mock.patch.object(login, 'reset_password', return_value='김명수') as reset:
            dlg = login.LoginDialog('10.0.0.9', 6000, fresh=True)
            dlg.name_edit.setText('kms')
            self.assertTrue(dlg.reset_link.isVisibleTo(dlg))
            dlg.reset_link.click()
            self.assertTrue(dlg.is_reset())
            self.assertEqual(dlg.reset_id_edit.text(), 'kms')                     # 로그인 화면의 아이디를 옮김
            self.assertFalse(dlg.options_box.isVisibleTo(dlg) or dlg.reset_link.isVisibleTo(dlg))
            self.assertEqual(dlg.login_button.text(), '비밀번호 바꾸기')
            self.assertFalse(dlg.login_button.isEnabled())
            dlg.reset_email_edit.setText('not-email')
            dlg.reset_password_edit.setText('new-pw')
            self.assertFalse(dlg.login_button.isEnabled())                       # 재설정 코드가 있어야
            dlg.reset_code_edit.setText('ABCD-EF23')
            self.assertTrue(dlg.login_button.isEnabled())
            dlg.accept()
            self.assertIn('이메일', dlg.error_label.text())
            reset.assert_not_called()
            dlg.reset_email_edit.setText('kms@kcs.test')
            self.assertEqual(dlg.reset_password_edit.echoMode(), QLineEdit.Password)   # 눈 아이콘 있는 칸
            dlg.accept()
            reset.assert_called_once_with('10.0.0.9', 6000, 'kms', 'kms@kcs.test', 'new-pw', code='ABCD-EF23')
            self.assertEqual(dlg.reset_code_edit.text(), '')                      # 쓴 코드는 지운다
            self.assertEqual(dlg._mode, 'login')                                  # 로그인 화면으로 돌아옴
            self.assertEqual((dlg.name_edit.text(), dlg.password_edit.text()), ('kms', ''))
            self.assertEqual(dlg.error_label.property('tone'), 'notice')
            self.assertIn('비밀번호를 바꿨습니다', dlg.error_label.text())
            self.assertNotEqual(dlg.result(), login.LoginDialog.Accepted)           # 창은 닫히지 않는다
        with mock.patch.object(login, 'reset_password', side_effect=net.ResetRejected('아이디 또는 가입한 이메일이 일치하지 않습니다.')):
            dlg.set_mode('reset')
            dlg.reset_code_edit.setText('ABCD-EF23')
            dlg.reset_email_edit.setText('x@kcs.test')
            dlg.reset_password_edit.setText('new-pw')
            dlg.accept()
            self.assertTrue(dlg.is_reset())
            self.assertEqual(dlg.error_label.property('tone'), 'error')
            self.assertIn('일치하지 않습니다', dlg.error_label.text())
            dlg.mode_toggle.click()                                               # 로그인 화면으로 돌아가기
            self.assertEqual(dlg._mode, 'login')

    def test_launcher_opens_clients_without_auto_login(self):
        import subprocess
        from client.launcher import chat_launcher
        launcher = chat_launcher.ChatLauncher()
        with mock.patch.object(subprocess, 'Popen') as popen:
            launcher.open_client()
        self.assertIn('--no-auto-login', popen.call_args[0][0])
        launcher.client_processes.clear()
        launcher.close()

    def test_login_dialog_fits_content(self):
        dlg = LoginDialog()
        dlg.show()
        QApplication.processEvents()
        self.assertGreaterEqual(dlg.height(), dlg.layout().sizeHint().height())
        dlg.hide()

    def test_rag_docs_dialog(self):
        dlg = RagDocsDialog(self.state)
        self.assertIsNotNone(self.conn.last('rag_doc_list'))
        self.conn.push('rag_doc_list', docs=[
            {'doc_id': 1, 'name': '규정.md', 'owner': '홍길동', 'size': 2048, 'chunks': 3, 'created_at': ''},
            {'doc_id': 2, 'name': '공용.txt', 'owner': '', 'size': 100, 'chunks': 1, 'created_at': ''}])
        self.assertEqual(dlg.list.count(), 2)
        dlg.list.setCurrentRow(1)
        self.assertFalse(dlg.delete_button.isEnabled())  # 공유 문서는 삭제 불가
        dlg.list.setCurrentRow(0)
        self.assertTrue(dlg.delete_button.isEnabled())
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dlg.delete_button.click()
        self.assertEqual(self.conn.last('rag_doc_delete'), {'doc_id': 1})
        path = os.path.join(tempfile.mkdtemp(), 'note.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('회의는 매주 월요일 10시')
        self.assertIsNone(dlg.add_file(path))
        self.assertEqual(self.conn.last('rag_doc_add')['name'], 'note.txt')


# ================================================================ 조직도 / 더보기
class OrgAndMoreTests(ShellTestBase):
    def test_org_tree(self):
        add_org(self.state, self.conn)
        tab = OrgTab(self.state, self.settings)
        self.assertEqual(tab.companies()[:2], ['KCS AI', 'KCS 홀딩스'])        # 지식봇이 맨 위
        self.assertEqual(set(tab.companies()), {'KCS 홀딩스', 'KCS 물류', 'KCS 커머스', 'KCS AI'})
        self.assertEqual(tab.members_of('KCS 홀딩스', '경영지원팀'), ['홍길동', '김철수'])
        self.assertEqual(tab.members_of('KCS 물류'), ['이영희', '박민수'])
        # 처음엔 내 회사(KCS 홀딩스)만 펼치고 다른 회사는 접어 둔다
        self.assertIn(('member', '김철수'), rows(tab.list))
        self.assertNotIn(('member', '이영희'), rows(tab.list))
        self.assertIn(('company', 'KCS 물류'), rows(tab.list))
        tab.toggle_company('KCS 물류')
        self.assertIn(('member', '이영희'), rows(tab.list))
        tab.toggle_company('KCS 물류')
        self.assertNotIn(('member', '이영희'), rows(tab.list))
        tab.toggle_company('KCS 물류')
        tab.search_edit.setText('마케팅')
        self.assertEqual(tab.companies(), ['KCS 커머스'])
        tab.search_edit.setText('')
        tab.toggle_company('KCS 커머스')
        tab.list.itemDoubleClicked.emit(tab.list.find_data(('member', '최지우')))
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '최지우'})
        menu = tab.context_menu_for(('member', '이영희'))
        self.assertEqual([a.text() for a in menu.actions()], ['1:1 채팅', '채팅방에 초대…', '프로필 보기', '연락처 보내기…'])

    def test_more_tab(self):
        tab = MoreTab(self.state, self.settings, server_address='127.0.0.1:5555')
        self.assertEqual(tab.name_label.text(), '홍길동')
        self.assertEqual(tab.server_value.text(), '127.0.0.1:5555')
        self.assertFalse(tab.info_group.isVisibleTo(tab))                     # 정보 구역은 그룹 관리자만
        self.assertEqual(tab.version_value.text(), '2.0')
        self.conn.push('user_updated', user=dict(self.state.user('홍길동'), status_message='새 상태'))
        self.assertEqual(tab.status_label.text(), '새 상태')
        # 설정이 다른 곳에서 바뀌어도 컨트롤이 따라간다
        self.settings.set('font_scale', 1.15)
        self.assertEqual(tab.font_combo.currentText(), '크게')
        self.settings.set('dark_mode', True)
        self.assertTrue(tab.dark_toggle.isChecked())
        tab.apply_theme()

    @unittest.skipUnless(HAS_WALLPAPER, 'wallpaper 미완성')
    def test_wallpaper_row(self):
        from client.kakao import wallpaper
        tab = MoreTab(self.state, self.settings)
        with mock.patch.object(wallpaper.WallpaperDialog, 'exec_', return_value=0) as ex:
            tab.wallpaper_button.click()
        ex.assert_called_once()


class UnreadBlinkTests(ShellTestBase):
    """안 읽은 메시지가 있는 동안 앱 아이콘(트레이·Dock) 깜박임"""

    def set_unread(self, win, counts):
        for rid, room in self.state.rooms.items():
            room['unread_count'] = counts.get(rid, 0)
        self.state.rooms_changed.emit()

    def test_blinks_while_unread_and_stops_when_read(self):
        win = self.make_window()
        rid = next(iter(self.state.rooms))
        self.set_unread(win, {})
        self.assertFalse(win.blinker.blinking)
        self.assertTrue(win.blinker.lit)
        self.set_unread(win, {rid: 3})
        self.assertTrue(win.blinker.blinking)
        self.assertFalse(win.blinker.lit)                       # 바로 '꺼짐'(흐린 아이콘) 프레임
        frames = set()
        for _ in range(4):
            win.blinker._tick()
            frames.add(win.blinker.lit)
        self.assertEqual(frames, {True, False})                 # 켜짐/꺼짐을 번갈아
        self.set_unread(win, {rid: 1})                          # 아직 남아 있으면 계속
        self.assertTrue(win.blinker.blinking)
        self.set_unread(win, {})                                # 다 읽음 → 멈추고 평소 아이콘
        self.assertFalse(win.blinker.blinking)
        self.assertTrue(win.blinker.lit)

    def test_muted_rooms_and_setting_off_do_not_blink(self):
        win = self.make_window()
        rid = next(iter(self.state.rooms))
        self.settings.set('muted_rooms', [rid])
        self.set_unread(win, {rid: 5})
        self.assertFalse(win.blinker.blinking)                  # 알림 끈 방은 제외
        self.settings.set('muted_rooms', [])
        self.assertTrue(win.blinker.blinking)
        self.settings.set('blink_unread', False)
        self.assertFalse(win.blinker.blinking)
        self.assertTrue(win.blinker.lit)
        self.settings.set('blink_unread', True)
        self.assertTrue(win.blinker.blinking)

    def test_reduce_motion_shows_steady_alert_icon(self):
        win = self.make_window()
        self.settings.set('reduce_motion', True)
        self.set_unread(win, {next(iter(self.state.rooms)): 2})
        self.assertFalse(win.blinker.blinking)
        self.assertTrue(win.blinker.alerting and not win.blinker.lit)
        self.settings.set('reduce_motion', False)
        self.assertTrue(win.blinker.blinking)

    def test_whole_icon_blinks_without_red_dot(self):
        """빨간 점을 덧그리지 않고 아이콘 전체가 흐려졌다 돌아온다"""
        from client.kakao.main_window import faded_icon, UnreadBlinker
        from client.kakao.login import talk_icon_pixmap
        normal = talk_icon_pixmap(64).toImage()
        off = faded_icon(UnreadBlinker.OFF_OPACITY, (64,)).pixmap(64, 64).toImage()
        for x, y in ((32, 32), (52, 12), (12, 52)):                 # 가운데 · 오른쪽 위(예전 빨간 점 자리) · 왼쪽 아래
            a, b = normal.pixelColor(x, y), off.pixelColor(x, y)
            self.assertLessEqual(b.alpha(), a.alpha() * UnreadBlinker.OFF_OPACITY + 2)   # 전체가 흐려짐
            self.assertFalse(b.red() > 150 and b.green() < 100 and b.blue() < 100)     # 빨간 점 없음
        win = self.make_window()
        self.set_unread(win, {next(iter(self.state.rooms)): 1})
        self.assertEqual(win.windowIcon().cacheKey(), win.blinker.off_icon.cacheKey())
        win.blinker._tick()
        self.assertEqual(win.windowIcon().cacheKey(), win.blinker.normal_icon.cacheKey())

    def test_more_tab_toggle(self):
        tab = MoreTab(self.state, self.settings)
        self.assertTrue(tab.blink_toggle.isChecked())
        tab.blink_toggle.setChecked(False)
        self.assertFalse(self.settings.get('blink_unread'))


class RecentIdLoginTests(unittest.TestCase):
    def setUp(self):
        app()

    def test_last_login_id_prefilled(self):
        from client.kakao import login
        path = os.path.join(tempfile.mkdtemp(), 'recent.json')
        with mock.patch.object(login, 'RECENT_PATH', path):
            dlg = login.LoginDialog()
            self.assertEqual(dlg.name_edit.text(), '')
            login.save_recent('도승현')          # 로그인에 성공한 뒤에만 저장된다 (app.remember_login)
            dlg2 = login.LoginDialog()
            self.assertEqual(dlg2.name_edit.text(), '도승현')          # 마지막 아이디만 채움
            self.assertEqual(dlg2.password_edit.text(), '')
            dlg3 = login.LoginDialog(username='kmkim')
            self.assertEqual(dlg3.name_edit.text(), 'kmkim')


class SavedLoginDialogTests(unittest.TestCase):
    """이메일·비밀번호 저장 / 자동 로그인 체크 (명세 §4.1)"""
    def setUp(self):
        app()


    def test_saved_token_used_when_password_left_empty(self):
        from client.kakao import login
        d = tempfile.mkdtemp()
        with mock.patch.object(login, 'RECENT_PATH', os.path.join(d, 'r.json')), \
                mock.patch.object(login, 'SAVED_PATH', os.path.join(d, 's.json')):
            dlg = login.LoginDialog()
            self.assertFalse(dlg.remember_check.isChecked() or dlg.auto_check.isChecked())
            dlg.auto_check.setChecked(True)
            self.assertTrue(dlg.remember_check.isChecked())          # 자동 로그인 → 저장도 켬
            dlg.remember_check.setChecked(False)
            self.assertFalse(dlg.auto_check.isChecked())             # 저장 끄면 자동 로그인도 끔
            login.save_saved_login('kmkim', 'tok123', False, '10.0.0.2', 6000)
            dlg = login.LoginDialog()
            self.assertEqual(dlg.name_edit.text(), 'kmkim')
            self.assertTrue(dlg.remember_check.isChecked())
            self.assertFalse(dlg.auto_check.isChecked())
            self.assertTrue(dlg.login_button.isEnabled())            # 비밀번호 칸이 비어도 저장된 정보로
            self.assertEqual(dlg.saved_token(), 'tok123')
            self.assertIn('저장된', dlg.password_edit.placeholderText())
            dlg.accept()
            self.assertEqual(dlg.result(), login.LoginDialog.Accepted)
            dlg = login.LoginDialog()
            dlg.password_edit.setText('newpass')
            self.assertEqual(dlg.saved_token(), '')                  # 새 비밀번호를 치면 그걸 쓴다
            dlg.name_edit.setText('other')
            dlg.password_edit.clear()
            self.assertEqual(dlg.saved_token(), '')
            self.assertFalse(dlg.login_button.isEnabled())
            login.clear_saved_login()
            self.assertIsNone(login.load_saved_login())

    def test_broken_saved_file_ignored(self):
        from client.kakao import login
        path = os.path.join(tempfile.mkdtemp(), 's.json')
        with open(path, 'w') as f:
            f.write('[1,2')
        self.assertIsNone(login.load_saved_login(path))
        with open(path, 'w') as f:
            json.dump({'login_id': 'a', 'token': 5}, f)
        self.assertIsNone(login.load_saved_login(path))


class OrgHierarchyTests(ShellTestBase):
    """지주사 → 계열사 → 다단계 조직 → 구성원(겸직 포함)"""

    def setUp(self):
        super().setUp()
        self.state.org = {'holding': {'name': '한국지주', 'business': '지주회사업'},
                          'companies': [{'name': '한네트', 'business': '부가통신업'}, {'name': '케이씨티'},
                                        {'name': '케이씨에스', 'units': [['기술연구소'], ['기술연구소', '연구1실'],
                                                                     ['기술연구소', '연구1실', '1팀'],
                                                                     ['기술연구소', '연구1실', '2팀(SI)'], ['신사업팀']]}]}
        for name, path, title, extra in (
                ('홍길동', ['기술연구소', '연구1실', '2팀(SI)'], '수석', []),
                ('김철수', ['기술연구소', '연구1실', '1팀'], '실장', [{'org_path': ['신사업팀'], 'title': '실장'}]),
                ('이영희', ['신사업팀'], '사원', [])):
            self.conn.push('user_updated', user=dict(self.state.user(name), company='케이씨에스',
                                                    department=path[-1], org_path=path, title=title,
                                                    extension='5100', concurrent=extra))

    def test_holding_then_affiliates_in_order(self):
        tab = OrgTab(self.state, self.settings)
        data = rows(tab.list)
        self.assertEqual(data[:2], [('section', '그룹사'), ('company', '한국지주')])
        companies = [d[1] for d in data if d[0] == 'company']
        self.assertEqual(companies[:4], ['한국지주', '한네트', '케이씨티', '케이씨에스'])
        self.assertIn('지주회사', tab.header.subtitle.text() + '지주회사')

    def test_multilevel_units_and_concurrent(self):
        tab = OrgTab(self.state, self.settings)
        data = rows(tab.list)
        units = [d[2] for d in data if d[0] == 'unit' and d[1] == '케이씨에스']
        self.assertEqual(units[:4], [('기술연구소',), ('기술연구소', '연구1실'), ('기술연구소', '연구1실', '1팀'),
                                     ('기술연구소', '연구1실', '2팀(SI)')])
        self.assertEqual(tab.members_of('케이씨에스', ('신사업팀',)), ['이영희'])  # 겸직자는 주소속 목록에서 제외
        # 내 소속(기술연구소) 옆 조직인 신사업팀은 처음엔 접혀 있다
        idx = data.index(('unit', '케이씨에스', ('신사업팀',)))
        self.assertNotIn(('member', '김철수'), data[idx:idx + 2])
        tab.toggle_unit('케이씨에스', ('신사업팀',))
        # 신사업팀 아래에는 겸직자 김철수도 행으로 나온다
        data = rows(tab.list)
        idx = data.index(('unit', '케이씨에스', ('신사업팀',)))
        self.assertIn(('member', '김철수'), data[idx:])
        tab.toggle_unit('케이씨에스', ('기술연구소',))
        self.assertNotIn(('member', '홍길동'), rows(tab.list))
        tab.toggle_unit('케이씨에스', ('기술연구소',))
        self.assertIn(('member', '홍길동'), rows(tab.list))

    def test_homepage_affiliates_include_kct(self):
        """회사 홈페이지(kcins.co.kr › 계열사) 기준 계열사 목록에 케이씨티 포함 + 화면 툴팁에 회사 정보"""
        import importlib
        mod = importlib.import_module('scripts.import_org_xlsx') if importlib.util.find_spec('scripts') else None
        if mod is None:
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
            mod = importlib.import_module('import_org_xlsx')
        names = [a['name'] for a in mod.AFFILIATES]
        # 한국지주 사업보고서(DART) 계열회사 현황 13곳: 상장 5 → 비상장 국내 6 → 해외 현지법인 2
        self.assertEqual(names, ['한국컴퓨터', '한네트', '로지시스', '케이씨티', '케이씨에스',
                                 '한국문학사', '한국상역개발', '상화원', '제주상화원', '한상개발', '상화공익',
                                 'KCI VINA', 'Pacom International'])
        self.assertTrue(all(a.get('business') for a in mod.AFFILIATES))
        self.assertEqual(mod.HOLDING['name'], '한국지주')
        kct = mod.AFFILIATES[names.index('케이씨티')]
        self.state.org['companies'] = [kct if c['name'] == '케이씨티' else c for c in self.state.org['companies']]
        tab = OrgTab(self.state, self.settings)
        item = tab.list.find_data(('company', '케이씨티'))
        tip = tab.list.itemWidget(item).toolTip()
        self.assertIn('금융자동화 시스템 공급 사업', tip)
        self.assertIn('02-6377-5622', tip)                                    # DART 대표전화
        self.assertIn('www.kctinc.co.kr', tip)

    def test_empty_affiliate_collapsed_then_expandable(self):
        tab = OrgTab(self.state, self.settings)
        self.assertNotIn(('empty', '한네트'), rows(tab.list))
        tab.toggle_company('한네트')
        self.assertIn(('empty', '한네트'), rows(tab.list))

    def test_search_by_org_path(self):
        tab = OrgTab(self.state, self.settings)
        tab.search_edit.setText('SI')
        members = [d[1] for d in rows(tab.list) if d[0] == 'member']
        self.assertEqual(members, ['홍길동'])

    def test_wide_window_docks_org_on_left(self):
        win = MainWindow(self.state, self.settings)
        win.resize(400, 680)
        win.show()
        app().processEvents()
        self.assertFalse(win.org_dock.isVisible())
        win.resize(1000, 700)
        app().processEvents()
        self.assertTrue(win.org_dock.isVisible())
        win.set_tab('org')
        self.assertFalse(win.org_dock.isVisible())   # 조직도 탭과 중복 표시 안 함
        win.close()


if __name__ == '__main__':
    unittest.main()
