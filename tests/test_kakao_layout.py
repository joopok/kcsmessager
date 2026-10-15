# -*- coding: utf-8 -*-
"""레이아웃 개선 회귀 테스트 - 헤더 2행, 선택지 줄이기(⋯/＋ 메뉴), 친구 탭 리듬, 더보기 묶음, 분할 화면, 대비 토큰"""

import unittest

from tests.kakao_fakes import make_demo_state, app
from PyQt5.QtGui import QColor

from client.kakao import theme


def pump(n=8):
    for _ in range(n):
        app().processEvents()


def contrast(a, b):
    def lum(c):
        c = QColor(c)
        ch = []
        for v in (c.redF(), c.greenF(), c.blueF()):
            ch.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class TokenContrastTest(unittest.TestCase):
    def tearDown(self):
        theme.set_dark(False)

    def test_hint_badge_sidebar_meet_aa(self):
        theme.set_dark(False)
        self.assertGreaterEqual(contrast(theme.c('text_hint'), theme.c('bg')), 4.5)
        self.assertGreaterEqual(contrast('#FFFFFF', theme.c('badge')), 4.5)
        self.assertGreaterEqual(contrast(theme.c('sidebar_icon'), theme.c('sidebar')), 4.5)
        theme.set_dark(True)
        self.assertGreaterEqual(contrast(theme.c('text_hint'), theme.c('bg')), 4.5)

    def test_spacing_scale(self):
        self.assertEqual([theme.sp(k) for k in ('xs', 's', 'm', 'l', 'xl')], [4, 8, 12, 16, 24])

    def test_avatar_palette_readable(self):
        from client.kakao.widgets import AVATAR_COLORS
        for color in AVATAR_COLORS:
            self.assertGreaterEqual(contrast('#FFFFFF', color), 3.0, color)   # 굵은 큰 글자 기준


def chat_room_av():
    from client.kakao import chat_room
    return chat_room.AV


class LayoutTest(unittest.TestCase):
    def setUp(self):
        app()
        theme.set_dark(False)
        self.state, self.conn = make_demo_state()

    def test_my_profile_avatar_same_size_as_friend_rows(self):
        """친구 탭 맨 위 내 프로필 사진이 AI 비서 · 친구 행 사진보다 크지 않다 (2026-10-10 요청)"""
        from client.kakao.friends_tab import MyProfileRow, FriendRow
        mine = MyProfileRow(self.state)
        friend = FriendRow(self.state, '김철수')
        self.assertEqual(mine.avatar.width(), friend.avatar.width())
        self.assertEqual(mine.avatar.height(), friend.avatar.height())

    def test_white_background_photo_reads_same_size_as_initials(self):
        """흰 배경 증명사진도 가장자리 선으로 이니셜 아바타와 같은 칸 크기가 보인다 (2026-10-10 요청)"""
        from PyQt5.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt5.QtGui import QImage, QColor
        from client.kakao.widgets import photo_avatar_pixmap, avatar_pixmap
        img = QImage(64, 64, QImage.Format_RGB32)
        img.fill(QColor('#FFFFFF'))
        raw = QByteArray()
        buf = QBuffer(raw)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, 'PNG')
        size = 44
        photo = photo_avatar_pixmap(bytes(raw), size).toImage()
        initials = avatar_pixmap('김철수', '김철수', size).toImage()
        self.assertEqual(photo.size(), initials.size())
        mid = photo.height() // 2
        edge = QColor(photo.pixel(1, mid))                     # 왼쪽 가장자리 (2배 해상도 기준)
        self.assertLess(edge.lightness(), 250)                  # 흰 사진이어도 가장자리가 보인다

    def test_more_tab_profile_avatar_same_size_as_friends_tab(self):
        """더보기 프로필 카드 사진 = 친구 탭 내 프로필 사진 크기 (2026-10-10 요청)"""
        from client.kakao.friends_tab import MyProfileRow
        from client.kakao.more_tab import MoreTab
        more = MoreTab(self.state, self.state.settings)
        more.refresh_profile()
        mine = MyProfileRow(self.state)
        self.assertEqual(more.avatar.size(), mine.avatar.size())
        pm = more.avatar.pixmap()
        self.assertEqual(pm.width() / pm.devicePixelRatio(), mine.avatar.width())

    def test_every_person_avatar_same_size(self):
        """친구 · 채팅 목록 · 조직도 · 더보기 · 사이드바 · 채팅방의 사람 사진이 모두 같은 크기 (2026-10-10 요청)"""
        from PyQt5.QtWidgets import QApplication
        from client.kakao.main_window import MainWindow
        from client.kakao.chat_room import ChatRoomWindow
        from client.kakao.friends_tab import MyProfileRow, FriendRow
        from client.kakao.chats_tab import RoomRow
        from client.kakao.org_tab import MemberRow, CompactMemberRow
        want = theme.px(theme.AVATAR_ROW)
        win = MainWindow(self.state, self.state.settings)
        win.show()
        sizes = {}
        for i in range(win.stack.count()):
            win.stack.setCurrentIndex(i)
            QApplication.processEvents()
            for cls in (MyProfileRow, FriendRow, RoomRow, MemberRow, CompactMemberRow):
                for row in win.stack.currentWidget().findChildren(cls):
                    if getattr(row, 'avatar', None) is not None:
                        sizes.setdefault(cls.__name__, set()).add(row.avatar.width())
        sizes['Sidebar'] = {win.sidebar.me_avatar.width()}
        sizes['MoreTab'] = {win.tabs['more'].avatar.width()}
        room = ChatRoomWindow(self.state, sorted(self.state.rooms)[0])
        sizes['ChatHeader'] = {room.avatar.width()}
        sizes['ChatMessage'] = {chat_room_av()}
        for name, got in sizes.items():
            self.assertEqual(got, {want}, name)
        win.close()
        room.close()

    def test_header_subtitle_on_own_row(self):
        from client.kakao.chats_tab import ChatsTab
        tab = ChatsTab(self.state, self.state.settings)
        tab.resize(336, 600)
        tab.show()
        pump()
        sub = tab.header.subtitle
        self.assertGreater(sub.y(), tab.header.title.y())               # 제목 아래 줄
        self.assertGreaterEqual(sub.width(), 250)                         # 버튼에 밀리지 않고 폭 전체
        tab.close()

    def test_chats_header_has_four_actions_and_overflow(self):
        from client.kakao.chats_tab import ChatsTab
        tab = ChatsTab(self.state, self.state.settings)
        visible = [b for b in tab.header.buttons if not b.isHidden()]
        self.assertLessEqual(len(visible), 4)
        self.assertEqual([a.text() for a in tab.more_menu().actions()],
                         ['오픈채팅 찾기 · 만들기', '톡서랍 (모든 채팅방의 사진·파일·링크)', '',
                          '@ 나를 멘션한 메시지', '책갈피 (저장한 메시지)'])

    def test_chat_input_shows_three_tools_and_labelled_menu(self):
        from client.kakao.chat_room import ChatRoomWindow
        win = ChatRoomWindow(self.state, 'r1')
        win.show()
        pump()
        shown = [b for b in win.tool_buttons if b.isVisible()]
        self.assertEqual(shown, [win.emoji_btn, win.file_btn, win.plus_btn])
        self.assertEqual([a.text() for a in win.tools_menu().actions() if not a.isSeparator()],
                         ['투표', '일정 공유', '예약 메시지', '사다리 게임', '톡게시판', '음성메시지', '연락처 보내기'])
        from unittest import mock
        from PyQt5.QtWidgets import QDialog
        with mock.patch.object(QDialog, 'exec_', return_value=0) as ex:
            win.tools_menu().actions()[0].trigger()   # 숨긴 버튼의 기능(투표 창)이 그대로 동작
        ex.assert_called()
        win.close()

    def test_friends_compact_story_and_no_duplicates(self):
        from client.kakao.friends_tab import FriendsTab
        self.conn.push('friend_list', friends=['박민수'])
        tab = FriendsTab(self.state, self.state.settings)
        tab.show()
        pump()
        self.assertTrue(tab.story_bar.scroll.isHidden())                 # 펑 없음 → 한 줄 링크
        self.assertFalse(tab.story_bar.compose_link.isHidden())
        sections = tab.sections
        self.assertIn('박민수', sections['registered'])
        self.assertNotIn('박민수', sections['offline'])                   # 다른 구역에 든 사람은 '오프라인' 에 안 겹친다
        # '온라인' 은 접속 중인 모든 친구 - 내가 추가한 친구 · 내 부서 동료도 접속해 있으면 함께 보인다 (2026-10-15)
        online = {u['username'] for u in self.state.friends() if self.state.is_online(u['username'])}
        self.assertEqual(set(sections['online']), online)
        tab.close()

    def test_more_tab_grouped(self):
        from client.kakao.more_tab import MoreTab
        tab = MoreTab(self.state, self.state.settings)
        self.assertGreaterEqual(len(tab.groups), 5)
        titles = [l.text() for l in tab.section_labels]
        self.assertEqual(titles[:3], ['계정', '상태 · 화면', '알림 · 채팅'])   # 로그아웃이 맨 위
        for frame in tab.groups:
            rows = [frame.layout().itemAt(i).widget() for i in range(frame.layout().count())]
            rows = [r for r in rows if r is not None and r.objectName() != 'settingsDivider']
            self.assertLessEqual(len(rows), 4)                               # 한 묶음 4개 이하

    def test_wide_window_split_embeds_and_pops_out(self):
        from client.kakao.main_window import MainWindow
        win = MainWindow(self.state, self.state.settings)
        win.resize(1320, 760)
        win.show()
        pump()
        self.assertTrue(win.is_split())
        self.assertEqual(win.room_pane.currentWidget(), win.room_placeholder)
        room = win.open_room('r1')
        pump()
        self.assertIs(room.parent(), win.room_pane)
        self.assertIs(win.open_room('r1'), room)                          # 다시 열어도 같은 칸
        win.resize(500, 700)
        pump()
        self.assertFalse(win.is_split())
        self.assertIsNone(room.parent())                                  # 좁아지면 별도 창으로
        room.close()
        win.close()


class WorkFirstAndKeyboardTest(unittest.TestCase):
    """사용자 결정 반영: 업무 우선 친구 탭 · 키보드/접근성 · KCS 블루 고정"""

    def setUp(self):
        app()
        theme.set_dark(False)
        self.state, self.conn = make_demo_state()
        for name, path, title in (('홍길동', ['기술연구소1실', '2팀(SI)'], '수석'),
                                  ('김철수', ['기술연구소1실', '1팀(Tandem)'], '팀장'),
                                  ('박민수', ['영업1부'], '부장')):
            self.conn.push('user_updated', user=dict(self.state.user(name), company='케이씨에스', org_path=path,
                                                    department=path[0], title=title))

    def test_social_features_default_off(self):
        from client.kakao.settings import LocalSettings
        import tempfile
        fresh = LocalSettings('누군가', base_dir=tempfile.mkdtemp())
        self.assertFalse(fresh.get('social_features'))

    def test_team_section_first_with_work_info(self):
        from client.kakao.friends_tab import FriendsTab
        from datetime import datetime, timedelta
        now = datetime.now()
        ev = {'event_id': 50, 'room_id': None, 'creator': '김철수', 'title': '고객 미팅', 'description': '',
              'location': '', 'start': (now - timedelta(minutes=30)).isoformat(timespec='seconds'),
              'end': (now + timedelta(minutes=30)).isoformat(timespec='seconds'), 'all_day': False,
              'remind_minutes': None, 'color': '#2A386C', 'attendees': {'김철수': 'yes'}, 'created_at': ''}
        self.conn.push('event_updated', event=ev)
        self.state.settings.set('social_features', False)
        tab = FriendsTab(self.state, self.state.settings)
        self.assertTrue(tab.story_bar.isHidden())
        self.assertEqual(tab.sections['team'], ['김철수'])               # 같은 부서(기술연구소1실)
        self.assertEqual(tab.sections['birthday'], [])                 # 소식 기능 꺼짐
        self.assertNotIn('김철수', tab.sections['offline'])
        self.assertIn('김철수', tab.sections['online'])                  # 접속 중인 동료는 '온라인' 에도 보인다
        data = [tab.list.item(i).data(0x0100) for i in range(tab.list.count())]
        sections = [d[1] for d in data if d[0] == 'section']
        self.assertEqual(sections[0], 'team')
        row = tab.list.itemWidget(tab.list.find_data(('friend', '김철수', 'team')))
        self.assertEqual(row.title_label.text(), '팀장')                # '김철수 팀장' - 직함은 이름 옆
        self.assertNotIn('팀장', row.sub.text())
        self.assertTrue(row.sub.text().startswith('1팀(Tandem)'))      # 둘째 줄은 팀
        self.assertIn('일정 중 ~', row.sub.text())
        self.state.settings.set('social_features', True)
        self.assertFalse(tab.story_bar.isHidden())

    def test_shortcuts_and_quick_switcher(self):
        from client.kakao.main_window import MainWindow, QuickSwitcher
        win = MainWindow(self.state, self.state.settings)
        win.show()
        keys = {sc.key().toString() for sc in win.shortcuts}
        for k in ('Ctrl+1', 'Ctrl+2', 'Ctrl+3', 'Ctrl+4', 'Ctrl+5', 'Ctrl+K', 'Ctrl+N', 'Ctrl+Shift+A'):
            self.assertIn(k, keys)
        [sc for sc in win.shortcuts if sc.key().toString() == 'Ctrl+3'][0].activated.emit()
        self.assertEqual(win.current_tab(), 'org')
        dlg = win.open_quick_switcher(exec_=False)
        self.assertIsInstance(dlg, QuickSwitcher)
        dlg.search.setText('프로젝트')
        self.assertEqual(dlg.list.currentItem().data(0x0100), ('room', 'r1'))
        dlg.search.setText('Tandem')
        self.assertEqual(dlg.list.currentItem().data(0x0100), ('person', '김철수'))
        dlg.accept_current()
        self.assertEqual(self.conn.last('room_open_direct'), {'target': '김철수'})   # 1:1 방이 없으면 만들기 요청
        win.close()

    def test_icon_buttons_have_accessible_names(self):
        from client.kakao.main_window import MainWindow
        from PyQt5.QtWidgets import QAbstractButton
        win = MainWindow(self.state, self.state.settings)
        unnamed = [b for b in win.findChildren(QAbstractButton)
                   if b.isVisibleTo(win) and not b.accessibleName() and not b.text()]
        self.assertEqual(unnamed, [])
        self.assertEqual(win.sidebar.buttons['org'].accessibleName(), '조직도')
        win.close()


if __name__ == '__main__':
    unittest.main()


class StrayWindowAndTrayTest(unittest.TestCase):
    """2026-10-15: (1) 탭을 만들거나 친구 구역을 접고 펼 때 부모 없는 위젯이 최상위 창으로 번쩍이지 않는다
    (Windows 에서 작은 창들이 여러 개 나타났다 사라지던 문제). (2) 창 닫기(X) 는 트레이로 내려가고 [종료] 만 끝낸다"""

    def setUp(self):
        from PyQt5.QtCore import QObject, QEvent
        from PyQt5.QtWidgets import QWidget
        app()
        self.state, self.conn = make_demo_state()

        class Probe(QObject):
            def __init__(self):
                super().__init__()
                self.shown = []

            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
                    self.shown.append(type(obj).__name__)
                return False
        self.probe = Probe()
        app().installEventFilter(self.probe)

    def tearDown(self):
        app().removeEventFilter(self.probe)

    def test_no_stray_toplevel_windows(self):
        from client.kakao.main_window import MainWindow, apply_app_theme
        from client.kakao.widgets import set_shown
        from PyQt5.QtWidgets import QLabel, QWidget, QVBoxLayout
        apply_app_theme(self.state.settings)
        win = MainWindow(self.state, self.state.settings)
        win.resize(420, 720)
        win.show()
        pump()
        for tab in ('friends', 'chats', 'org', 'calendar', 'more'):
            win.set_tab(tab)
            pump()
        friends = win.tabs['friends']
        for key in ('online', 'offline', 'birthday'):
            friends.toggle_section(key)
            pump()
            friends.toggle_section(key)
            pump()
        self.assertEqual([n for n in self.probe.shown if n != 'MainWindow'], [])   # 메인 창 말고는 최상위 창이 뜨지 않는다
        win.close()
        app().removeEventFilter(self.probe)        # 아래 holder 는 일부러 띄우는 최상위 창
        # set_shown: 부모 없는 위젯은 보이기를 미루고, 레이아웃에 들어가 부모가 보이면 함께 보인다. 숨기기는 바로 적용
        label = QLabel('x')
        set_shown(label, False)
        set_shown(label, True)
        self.assertFalse(label.isVisible())
        holder = QWidget()
        QVBoxLayout(holder).addWidget(label)
        holder.show()
        pump()
        self.assertTrue(label.isVisible())
        set_shown(label, False)
        self.assertFalse(label.isVisible())
        set_shown(label, True)
        self.assertTrue(label.isVisible())
        hidden = QLabel('y')
        set_shown(hidden, False)
        holder.layout().addWidget(hidden)
        pump()
        self.assertFalse(hidden.isVisible())                                    # 명시적으로 숨긴 것은 부모가 보여도 숨김
        holder.close()

    def test_close_hides_to_tray_and_quit_really_closes(self):
        from PyQt5.QtWidgets import QSystemTrayIcon
        from client.kakao.main_window import MainWindow, apply_app_theme
        apply_app_theme(self.state.settings)
        win = MainWindow(self.state, self.state.settings)
        win.show()
        pump()
        if win.tray is None:                       # 오프스크린 · CI 에는 트레이가 없다 → 트레이가 있는 것처럼
            win.tray = QSystemTrayIcon(win)
        win.close()
        pump()
        self.assertFalse(win.isVisible())          # 숨겨졌을 뿐
        self.assertFalse(win._quitting)
        win.bring_to_front()
        pump()
        self.assertTrue(win.isVisible())           # 트레이 아이콘 → 다시 열기
        win.quit_app()
        pump()
        self.assertTrue(win._quitting)
        self.assertFalse(win.isVisible())
        win.tray = None
