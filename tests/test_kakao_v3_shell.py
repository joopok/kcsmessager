# -*- coding: utf-8 -*-
"""
v3 앱 셸 테스트 - 펑(스토리), 숨김/차단, 멀티프로필, 키워드 알림, 말풍선 꾸미기, 잠금모드, 기능 둘러보기

    QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_kakao_v3_shell -v
"""

import hashlib
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

from tests.kakao_fakes import make_demo_state, app

from PyQt5.QtCore import QEvent, Qt, QPoint
from PyQt5.QtGui import QImage, QColor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QMessageBox, QApplication, QWidget, QDialog

from client.kakao import theme, lock
from client.kakao.main_window import MainWindow, apply_app_theme
from client.kakao.friends_tab import FriendsTab, ProfileDialog, StoryBar
from client.kakao.more_tab import (MoreTab, MultiProfileDialog, KeywordAlertDialog, BubbleStyleDialog,
                                   FriendManageDialog, LockSettingsDialog, normalize_keywords)
from client.kakao.story import StoryComposeDialog, StoryViewer, time_ago


def iso(dt):
    return dt.isoformat(timespec='seconds')


def story(story_id, owner, content='오늘 날씨 좋다', minutes_ago=10, viewers=None, kind='text', **extra):
    now = datetime.now().replace(microsecond=0)
    s = {'story_id': story_id, 'owner': owner, 'kind': kind, 'content': content, 'attachment_id': None,
         'bg_color': '#FF7A59', 'created_at': iso(now - timedelta(minutes=minutes_ago)),
         'expires_at': iso(now + timedelta(hours=23)), 'viewers': list(viewers or [])}
    s.update(extra)
    return s


def write_png(path, w=300, h=200, color='#4CB6F2'):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(path, 'PNG')
    return path


def rows(lst):
    return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]


class V3TestBase(unittest.TestCase):
    def setUp(self):
        app()
        theme.set_dark(False)
        theme.set_font_scale(1.0)
        self.state, self.conn = make_demo_state()
        self.settings = self.state.settings
        self.tmp = tempfile.mkdtemp(prefix='kcs_v3_')
        self._windows = []

    def tearDown(self):
        for w in self._windows:
            w.hide()
            w.deleteLater()
        QApplication.processEvents()                              # 지연 삭제를 먼저 끝내고
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        theme.set_dark(False)
        apply_app_theme(None)                                      # 그다음 앱 스타일 다시 입히기
        QApplication.processEvents()

    def keep(self, w):
        self._windows.append(w)
        return w

    def make_window(self):
        return self.keep(MainWindow(self.state, self.settings, server_address='127.0.0.1:5555'))

    def push_stories(self, *stories):
        self.conn.push('story_list', stories=list(stories))

    def sent(self, msg_type):
        return [p for t, p in self.conn.sent if t == msg_type]


# ================================================================ 잠금 PIN
class LockPinTests(V3TestBase):
    def test_hash_matches_spec(self):
        self.assertEqual(lock.hash_pin('1234', 'abc'), hashlib.sha256(b'abc1234').hexdigest())

    def test_set_verify_clear(self):
        self.assertFalse(lock.has_pin(self.settings))
        self.assertFalse(lock.verify_pin(self.settings, '1234'))
        lock.set_pin(self.settings, '1234')
        self.assertTrue(lock.has_pin(self.settings))
        salt = self.settings.get('lock_salt')
        self.assertTrue(salt)
        self.assertEqual(self.settings.get('lock_pin_hash'), lock.slow_hash_pin('1234', salt))   # 느린 해시로 저장
        self.assertTrue(lock.verify_pin(self.settings, '1234'))
        self.assertFalse(lock.verify_pin(self.settings, '1235'))
        self.assertFalse(lock.verify_pin(self.settings, ''))
        lock.clear_pin(self.settings)
        self.assertFalse(lock.has_pin(self.settings))
        self.assertFalse(lock.verify_pin(self.settings, '1234'))

    def test_invalid_pin_rejected(self):
        for bad in ('123', '12345', 'abcd', '١٢٣٤', ''):
            with self.assertRaises(ValueError):
                lock.set_pin(self.settings, bad)
        self.assertFalse(lock.has_pin(self.settings))

    def test_salt_changes_each_time(self):
        lock.set_pin(self.settings, '1111')
        first = self.settings.get('lock_salt')
        lock.set_pin(self.settings, '1111')
        self.assertNotEqual(first, self.settings.get('lock_salt'))


class LockScreenTests(V3TestBase):
    def setUp(self):
        super().setUp()
        lock.set_pin(self.settings, '2468')
        self.host = self.keep(QWidget())
        self.host.resize(400, 680)
        self.screen = lock.LockScreen(self.settings, self.host)
        self.unlocked = []
        self.failed = []
        self.screen.unlocked.connect(lambda: self.unlocked.append(True))
        self.screen.failed.connect(lambda: self.failed.append(True))
        self.host.show()
        self.screen.show_over()

    def test_covers_parent_and_has_logo(self):
        self.assertFalse(self.screen.isHidden())
        self.assertEqual(self.screen.geometry(), self.host.rect())
        self.assertFalse(self.screen.logo.pixmap().isNull())
        self.host.resize(500, 700)
        QApplication.processEvents()
        self.assertEqual(self.screen.size(), self.host.size())

    def test_wrong_pin_shakes_then_clears(self):
        for d in '1111':
            self.screen.press_digit(d)
        self.assertEqual(self.failed, [True])
        self.assertTrue(self.screen.is_shaking())
        self.assertIn('일치하지', self.screen.hint.text())
        self.assertTrue(self.screen.dots.error)
        self.assertFalse(self.unlocked)
        self.assertFalse(self.screen.isHidden())
        # 다음 입력은 새로 시작
        self.screen.press_digit('2')
        self.assertEqual(self.screen.entered, '2')

    def test_right_pin_unlocks(self):
        for d in '2468':
            self.screen.keys[d].click()
        self.assertEqual(self.unlocked, [True])
        self.assertTrue(self.screen.isHidden())

    def test_keyboard_and_backspace(self):
        QTest.keyClicks(self.screen, '249')
        self.assertEqual(self.screen.entered, '249')
        QTest.keyClick(self.screen, Qt.Key_Backspace)
        self.assertEqual(self.screen.entered, '24')
        self.screen.keys['⌫'].click()
        self.assertEqual(self.screen.entered, '2')
        QTest.keyClicks(self.screen, 'x468')
        self.assertEqual(self.unlocked, [True])


# ================================================================ 메인 창 잠금 / 둘러보기
class MainWindowLockTests(V3TestBase):
    def test_lock_without_pin_shows_toast(self):
        win = self.make_window()
        self.assertFalse(win.lock())
        self.assertFalse(self.state.locked)
        self.assertIn('비밀번호', win.toast.text())

    def test_lock_hides_rooms_and_unlock_restores(self):
        lock.set_pin(self.settings, '1234')
        win = self.make_window()
        self.assertTrue(self.state.locked)   # PIN 이 있으면 잠긴 채 시작
        win.unlock()
        room = self.keep(QWidget())
        win.room_windows['r1'] = room
        room.show()
        win.show()
        self.assertTrue(win.lock())
        self.assertTrue(self.state.locked)
        self.assertFalse(win.lock_screen.isHidden())
        self.assertTrue(room.isHidden())
        for d in '1234':
            win.lock_screen.press_digit(d)
        self.assertFalse(self.state.locked)
        self.assertTrue(win.lock_screen.isHidden())
        self.assertFalse(room.isHidden())

    def test_starts_locked_with_pin(self):
        lock.set_pin(self.settings, '0000')
        win = self.make_window()
        self.assertTrue(win.is_locked())
        self.assertFalse(win.lock_screen.isHidden())

    def test_idle_lock_after_lock_minutes(self):
        lock.set_pin(self.settings, '1234')
        self.settings.set('lock_minutes', 1)
        win = self.make_window()
        win.unlock()
        watcher = win.idle_watcher
        base = watcher.last_input
        watcher.check(now=base + 30)
        self.assertFalse(self.state.locked)
        watcher.check(now=base + 61)
        self.assertTrue(self.state.locked)

    def test_no_idle_lock_without_pin(self):
        win = self.make_window()
        win.idle_watcher.check(now=win.idle_watcher.last_input + 3600)
        self.assertFalse(self.state.locked)

    def test_locked_notification_hides_content(self):
        lock.set_pin(self.settings, '1234')
        self.make_window()
        got = []
        self.state.notify.connect(lambda t, b, r: got.append(b))
        self.conn.push('msg_new', message={'msg_id': 99, 'room_id': 'r2', 'sender': '박민수', 'kind': 'text',
                                           'content': '비밀 내용', 'timestamp': iso(datetime.now()),
                                           'mentions': [], 'reactions': {}})
        self.assertEqual(got, ['새 메시지가 있습니다'])

    def test_open_room_deferred_while_locked(self):
        lock.set_pin(self.settings, '1234')
        win = self.make_window()
        self.assertIsNone(win.open_room('r1'))
        self.assertEqual(win._pending_rooms, ['r1'])
        with mock.patch.object(MainWindow, 'open_room', return_value=None) as opener:
            win.unlock()
        opener.assert_called_with('r1')

    def test_feature_tour_missing_shows_toast(self):
        win = self.make_window()
        with mock.patch.dict(sys.modules, {'client.kakao.feature_tour': None}):
            self.assertIsNone(win.open_feature_tour())
        self.assertIn('기능 둘러보기', win.toast.text())

    def test_feature_tour_constructed_with_contract_args(self):
        win = self.make_window()
        calls = []

        class FakeTour(QDialog):
            def __init__(self, state, settings, main_window, parent=None):
                super().__init__(parent)
                calls.append((state, settings, main_window, parent))

        module = types.ModuleType('client.kakao.feature_tour')
        module.FeatureTourDialog = FakeTour
        with mock.patch.dict(sys.modules, {'client.kakao.feature_tour': module}):
            dlg = win.open_feature_tour()
            self.assertIs(win.open_feature_tour(), dlg)   # 이미 열려 있으면 재사용
        self.assertEqual(calls, [(self.state, self.settings, win, win)])
        dlg.close()

    def test_more_tab_tour_row_calls_main_window(self):
        win = self.make_window()
        with mock.patch.object(MainWindow, 'open_feature_tour') as opener:
            win.tabs['more'].tour_button.click()
        opener.assert_called_once()


# ================================================================ 펑
class StoryComposeTests(V3TestBase):
    def test_text_story(self):
        dlg = self.keep(StoryComposeDialog(self.state))
        dlg.set_text('  점심 맛있었다  ')
        dlg.set_color('#6DCC7B')
        self.assertEqual(dlg.preview.bg_color, '#6DCC7B')
        self.assertTrue([s for s in dlg.swatches if s.color == '#6DCC7B'][0].isChecked())
        dlg.accept()
        self.assertEqual(dlg.result(), QDialog.Accepted)
        self.assertEqual(self.conn.last('story_post'),
                         {'kind': 'text', 'content': '점심 맛있었다', 'bg_color': '#6DCC7B'})

    def test_empty_rejected(self):
        dlg = self.keep(StoryComposeDialog(self.state))
        dlg.accept()
        self.assertIsNone(self.conn.last('story_post'))
        self.assertFalse(dlg.error_label.isHidden())

    def test_photo_story(self):
        path = write_png(os.path.join(self.tmp, 'pic.png'))
        dlg = self.keep(StoryComposeDialog(self.state))
        self.assertIsNone(dlg.set_image(path))
        self.assertTrue(dlg.clear_photo_button.isEnabled())
        dlg.set_text('바다')
        dlg.accept()
        sent = self.conn.last('story_post')
        self.assertEqual(sent['kind'], 'image')
        self.assertEqual(sent['content'], '바다')
        self.assertEqual(sent['attachment']['name'], 'pic.png')

    def test_bad_photo(self):
        bad = os.path.join(self.tmp, 'x.png')
        with open(bad, 'wb') as f:
            f.write(b'not an image')
        dlg = self.keep(StoryComposeDialog(self.state))
        self.assertTrue(dlg.set_image(bad))
        self.assertIsNone(dlg.image_path)

    def test_time_ago(self):
        now = datetime(2026, 10, 8, 12, 0, 0)
        self.assertEqual(time_ago('2026-10-08T11:59:40', now), '방금')
        self.assertEqual(time_ago('2026-10-08T11:35:00', now), '25분 전')
        self.assertEqual(time_ago('2026-10-08T07:00:00', now), '5시간 전')


class StoryViewerTests(V3TestBase):
    def test_friend_story_navigation_and_view(self):
        self.push_stories(story(1, '김철수', '첫 번째', 30), story(2, '김철수', '두 번째', 5))
        viewer = self.keep(StoryViewer(self.state, '김철수'))
        self.assertTrue(viewer.viewers_panel.isHidden())
        self.assertEqual(viewer.current_story()['story_id'], 1)
        self.assertEqual(self.sent('story_view'), [{'story_id': 1}])
        viewer.card.tapped.emit('next')
        self.assertEqual(viewer.index, 1)
        self.assertEqual(self.sent('story_view')[-1], {'story_id': 2})
        viewer.card.tapped.emit('prev')
        self.assertEqual(viewer.index, 0)
        self.assertEqual(len(self.sent('story_view')), 2)   # 이미 본 것은 다시 보내지 않음
        self.assertFalse(self.state.has_unseen_story('김철수'))

    def test_tap_regions(self):
        self.push_stories(story(1, '김철수'), story(2, '김철수', minutes_ago=1))
        viewer = self.keep(StoryViewer(self.state, '김철수'))
        viewer.show()
        w, h = viewer.card.width(), viewer.card.height()
        QTest.mouseClick(viewer.card, Qt.LeftButton, Qt.NoModifier, QPoint(w - 20, h // 2))
        self.assertEqual(viewer.index, 1)
        QTest.mouseClick(viewer.card, Qt.LeftButton, Qt.NoModifier, QPoint(20, h // 2))
        self.assertEqual(viewer.index, 0)

    def test_auto_advance_and_close_at_end(self):
        self.push_stories(story(1, '김철수'), story(2, '김철수', minutes_ago=1))
        viewer = self.keep(StoryViewer(self.state, '김철수'))
        self.assertEqual(viewer.timer.interval(), 50)
        self.assertTrue(viewer.timer.isActive())
        viewer.progress = lambda: 1.0
        viewer._tick()
        self.assertEqual(viewer.index, 1)
        viewer._tick()
        self.assertEqual(viewer.result(), QDialog.Accepted)
        self.assertFalse(viewer.timer.isActive())

    def test_my_story_viewers_and_delete(self):
        self.push_stories(story(7, '홍길동', '내 펑', viewers=['김철수', '이영희']))
        viewer = self.keep(StoryViewer(self.state, '홍길동'))
        self.assertFalse(viewer.viewers_panel.isHidden())
        self.assertEqual(viewer.viewers_label.text(), '2명이 봤어요')
        self.assertEqual(rows(viewer.viewers_list), ['김철수', '이영희'])
        self.assertEqual(self.sent('story_view'), [])   # 내 펑은 조회 기록 안 보냄
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.No) as ask:
            viewer.delete_button.click()
        ask.assert_called_once()                         # 버튼으로 지우면 반드시 먼저 묻는다
        self.assertEqual(self.sent('story_delete'), [])  # 아니요 → 안 지움
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            viewer.delete_button.click()
        self.assertEqual(self.conn.last('story_delete'), {'story_id': 7})
        self.conn.push('story_deleted', story_id=7)
        self.assertEqual(viewer.result(), QDialog.Accepted)

    def test_viewer_list_updates_live(self):
        self.push_stories(story(7, '홍길동', viewers=[]))
        viewer = self.keep(StoryViewer(self.state, '홍길동'))
        self.assertIn('아직', viewer.viewers_label.text())
        self.conn.push('story_updated', story=story(7, '홍길동', viewers=['박민수']))
        self.assertEqual(viewer.viewers_label.text(), '1명이 봤어요')

    def test_image_story_requests_attachment(self):
        self.push_stories(story(3, '이영희', '', kind='image', attachment_id=55))
        viewer = self.keep(StoryViewer(self.state, '이영희'))
        viewer.show()
        viewer.repaint()
        self.assertEqual(self.conn.last('attachment_get'), {'attachment_id': 55})


# ================================================================ 친구 탭: 펑 줄, 숨김/차단
class FriendsTabV3Tests(V3TestBase):
    def setUp(self):
        super().setUp()
        self.tab = self.keep(FriendsTab(self.state, self.settings))
        self.tab.resize(400, 640)

    def friend_names(self):
        return [d[1] for d in rows(self.tab.list) if d and d[0] == 'friend']

    def test_story_bar_rings(self):
        bar = self.tab.story_bar
        self.assertEqual([b.kind for b in bar.bubbles], ['add'])
        self.push_stories(story(1, '김철수'), story(2, '홍길동'), story(3, '박민수'))
        self.assertEqual(bar.owners()[0], '홍길동')   # 내 펑이 맨 앞
        self.assertEqual([b.kind for b in bar.bubbles][:2], ['add', 'story'])
        kim = bar.bubble_for('김철수')
        self.assertTrue(kim.unseen())
        self.assertEqual(kim.ring_color(), theme.c('yellow'))
        self.state.view_story(1)
        kim = bar.bubble_for('김철수')
        self.assertFalse(kim.unseen())
        self.assertNotEqual(kim.ring_color(), theme.c('yellow'))
        self.assertEqual(bar.bubble_for('홍길동').label(), '내 펑')

    def test_story_bar_click_opens_viewer_and_compose(self):
        self.push_stories(story(1, '김철수'))
        with mock.patch.object(StoryViewer, 'exec_', return_value=0) as ex:
            self.tab.story_bar.bubble_for('김철수').click()
        ex.assert_called_once()
        self.assertEqual(self.sent('story_view'), [{'story_id': 1}])
        from client.kakao.story import StoryComposeDialog as Compose
        with mock.patch.object(Compose, 'exec_', return_value=0) as ex2:
            self.tab.story_bar.bubbles[0].click()
        ex2.assert_called_once()

    def test_blocked_story_owner_hidden(self):
        self.push_stories(story(1, '김철수'))
        self.conn.push('block_list', blocked=['김철수'])
        self.assertIsNone(self.tab.story_bar.bubble_for('김철수'))

    def test_context_menu_hide(self):
        menu = self.tab.context_menu_for(('friend', '김철수', 'online'))
        actions = {a.text(): a for a in menu.actions()}
        self.assertIn('숨기기', actions)
        self.assertIn('차단', actions)
        actions['숨기기'].trigger()
        self.assertTrue(self.state.is_hidden('김철수'))
        self.assertNotIn('김철수', self.friend_names())

    def test_context_menu_block_confirm(self):
        menu = self.tab.context_menu_for(('friend', '이영희', 'offline'))
        block = [a for a in menu.actions() if a.text() == '차단'][0]
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            block.trigger()
        self.assertIsNone(self.conn.last('block_set'))
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            block.trigger()
        self.assertEqual(self.conn.last('block_set'), {'username': '이영희', 'blocked': True})
        self.conn.push('block_list', blocked=['이영희'])
        self.tab.toggle_section('offline')
        self.assertNotIn('이영희', self.friend_names())

    def test_profile_dialog_actions(self):
        dlg = self.keep(ProfileDialog(self.state, '박민수'))
        self.assertEqual(dlg.hide_button.text(), '숨기기')
        dlg.hide_button.click()
        self.assertTrue(self.state.is_hidden('박민수'))
        dlg2 = self.keep(ProfileDialog(self.state, '박민수'))
        self.assertEqual(dlg2.hide_button.text(), '숨김 해제')
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dlg2.block_button.click()
        self.assertEqual(self.conn.last('block_set'), {'username': '박민수', 'blocked': True})
        self.conn.push('block_list', blocked=['박민수'])
        dlg3 = self.keep(ProfileDialog(self.state, '박민수'))
        self.assertEqual(dlg3.block_button.text(), '차단 해제')
        dlg3.block_button.click()
        self.assertEqual(self.conn.last('block_set'), {'username': '박민수', 'blocked': False})
        mine = self.keep(ProfileDialog(self.state, '홍길동'))
        self.assertIsNone(mine.block_button)


# ================================================================ 더보기: 새 행과 다이얼로그
class MoreTabV3Tests(V3TestBase):
    def test_rows_open_dialogs(self):
        tab = self.keep(MoreTab(self.state, self.settings))
        cases = [(tab.multi_profile_button, MultiProfileDialog), (tab.friend_manage_button, FriendManageDialog),
                 (tab.keyword_button, KeywordAlertDialog), (tab.bubble_button, BubbleStyleDialog),
                 (tab.lock_button, LockSettingsDialog)]
        for button, cls in cases:
            with mock.patch.object(cls, 'exec_', return_value=0) as ex:
                button.click()
            ex.assert_called_once()

    def test_row_descriptions_follow_settings(self):
        tab = self.keep(MoreTab(self.state, self.settings))
        self.assertEqual(tab.lock_row.description.text(), '사용 안 함')
        self.settings.set('alert_keywords', ['긴급', '회의'])
        self.assertEqual(tab.keyword_row.description.text(), '긴급, 회의')
        self.settings.set('bubble_style', 'mint')
        self.assertEqual(tab.bubble_row.description.text(), '민트')
        lock.set_pin(self.settings, '1234')
        self.assertIn('사용 중', tab.lock_row.description.text())

    def test_call_auto_record_toggle(self):
        self.assertFalse(self.settings.get('call_auto_record'))
        tab = self.keep(MoreTab(self.state, self.settings))
        self.assertEqual(tab.record_row.title.text(), '보이스톡 자동 녹음')
        self.assertIn('recordings 폴더', tab.record_row.description.text())
        self.assertFalse(tab.record_toggle.isChecked())
        tab.record_toggle.click()
        self.assertIs(self.settings.get('call_auto_record'), True)
        self.settings.set('call_auto_record', False)      # 외부 변경도 반영
        self.assertFalse(tab.record_toggle.isChecked())

    def test_tour_without_main_window(self):
        tab = self.keep(MoreTab(self.state, self.settings))
        with mock.patch.object(QMessageBox, 'information') as info:
            tab.open_feature_tour()
        info.assert_called_once()


class MultiProfileTests(V3TestBase):
    def test_requests_and_loads(self):
        dlg = self.keep(MultiProfileDialog(self.state))
        self.assertTrue(self.sent('multi_profile_list'))
        self.assertTrue(dlg.editor.isHidden())
        self.conn.push('multi_profile_list', profiles=[
            {'profile_id': 3, 'name': '회사용', 'display_name': '홍 팀장', 'status_message': '업무 중',
             'avatar_id': None, 'viewers': ['김철수']}])
        self.assertEqual(dlg.list.count(), 1)
        self.assertEqual(dlg.display_edit.text(), '홍 팀장')
        checked = [dlg.viewers_list.item(i).data(Qt.UserRole) for i in range(dlg.viewers_list.count())
                   if dlg.viewers_list.item(i).checkState() == Qt.Checked]
        self.assertEqual(checked, ['김철수'])

    def test_add_edit_photo_save(self):
        dlg = self.keep(MultiProfileDialog(self.state))
        self.conn.push('multi_profile_list', profiles=[
            {'profile_id': 3, 'name': '회사용', 'display_name': '홍 팀장', 'status_message': '',
             'avatar_id': None, 'viewers': ['김철수']}])
        dlg.add_profile('동호회')
        self.assertEqual(dlg.current, 1)
        dlg.set_fields(display_name='길동이', status_message='주말엔 등산')
        dlg.set_viewers(['박민수', '최지우'])
        path = write_png(os.path.join(self.tmp, 'face.png'), 600, 400)
        self.assertIsNone(dlg.set_photo_from_path(path))
        img = QImage.fromData(dlg.profiles[1]['_avatar_bytes'])
        self.assertEqual((img.width(), img.height()), (256, 256))
        dlg.accept()
        payload = self.conn.last('multi_profile_set')['profiles']
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0], {'profile_id': 3, 'name': '회사용', 'display_name': '홍 팀장',
                                      'status_message': '', 'viewers': ['김철수']})
        second = payload[1]
        self.assertEqual((second['name'], second['display_name'], second['status_message']),
                         ('동호회', '길동이', '주말엔 등산'))
        self.assertEqual(sorted(second['viewers']), ['박민수', '최지우'])
        self.assertTrue(second['avatar_b64'])
        self.assertNotIn('profile_id', second)

    def test_typing_writes_back_and_clear_photo(self):
        dlg = self.keep(MultiProfileDialog(self.state))
        dlg.add_profile()
        QTest.keyClicks(dlg.status_edit, 'on vacation')   # QTest 는 ASCII 키만 지원
        self.assertEqual(dlg.profiles[0]['status_message'], 'on vacation')
        dlg.clear_photo()
        dlg.accept()
        self.assertTrue(self.conn.last('multi_profile_set')['profiles'][0]['avatar_clear'])

    def test_delete_and_validation(self):
        dlg = self.keep(MultiProfileDialog(self.state))
        dlg.add_profile('A')
        dlg.add_profile('B')
        dlg.delete_profile(0)
        self.assertEqual([p['name'] for p in dlg.profiles], ['B'])
        dlg.set_fields(name='')
        dlg.accept()
        self.assertIsNone(self.conn.last('multi_profile_set'))
        self.assertIn('프로필 이름', dlg.error_label.text())
        dlg.set_fields(name='B')
        dlg.add_profile('B')
        dlg.accept()
        self.assertIn('이미 있어요', dlg.error_label.text())
        dlg.delete_profile(1)
        dlg.delete_profile(0)
        dlg.accept()
        self.assertEqual(self.conn.last('multi_profile_set'), {'profiles': []})

    def test_late_response_does_not_overwrite_edits(self):
        dlg = self.keep(MultiProfileDialog(self.state))
        dlg.add_profile('작업중')
        self.conn.push('multi_profile_list', profiles=[])
        self.assertEqual([p['name'] for p in dlg.profiles], ['작업중'])


class KeywordTests(V3TestBase):
    def test_add_trim_dedup_remove(self):
        dlg = self.keep(KeywordAlertDialog(self.settings))
        self.assertFalse(dlg.empty_label.isHidden())
        self.assertIsNone(dlg.add_keyword('  긴급  '))
        self.assertIsNone(dlg.add_keyword('회의'))
        self.assertTrue(dlg.add_keyword('긴급'))
        self.assertTrue(dlg.add_keyword('   '))
        self.assertEqual(self.settings.get('alert_keywords'), ['긴급', '회의'])
        self.assertEqual(dlg.chip_words(), ['긴급', '회의'])
        chip = dlg.flow.items[0]
        chip.remove_button.click()
        self.assertEqual(self.settings.get('alert_keywords'), ['회의'])

    def test_edit_and_return(self):
        dlg = self.keep(KeywordAlertDialog(self.settings))
        dlg.edit.setText('배포')
        QTest.keyClick(dlg.edit, Qt.Key_Return)
        self.assertEqual(self.settings.get('alert_keywords'), ['배포'])
        self.assertEqual(dlg.edit.text(), '')

    def test_max_twenty(self):
        dlg = self.keep(KeywordAlertDialog(self.settings))
        for i in range(20):
            self.assertIsNone(dlg.add_keyword(f'단어{i}'))
        self.assertIn('최대', dlg.add_keyword('스물하나'))
        self.assertEqual(len(self.settings.get('alert_keywords')), 20)
        self.assertFalse(dlg.add_button.isEnabled())

    def test_normalize(self):
        self.assertEqual(normalize_keywords([' a ', 'A', '', 'b'] + [str(i) for i in range(30)])[:3], ['a', 'b', '0'])
        self.assertEqual(len(normalize_keywords([str(i) for i in range(30)])), 20)

    def test_state_keyword_hit_uses_setting(self):
        dlg = self.keep(KeywordAlertDialog(self.settings))
        dlg.add_keyword('긴급')
        self.assertTrue(self.state.keyword_hit({'kind': 'text', 'content': '긴급 공지입니다', 'sender': '김철수'}))


class BubbleStyleTests(V3TestBase):
    def test_grid_and_save(self):
        dlg = self.keep(BubbleStyleDialog(self.settings))
        self.assertEqual(set(dlg.cards), set(theme.BUBBLE_STYLES))
        self.assertEqual(dlg.selected(), 'basic')
        self.assertTrue(dlg.cards['basic'].isChecked())
        dlg.cards['pastel'].click()
        self.assertEqual(dlg.selected(), 'pastel')
        self.assertFalse(dlg.cards['basic'].isChecked())
        self.assertEqual(self.settings.get('bubble_style'), 'basic')   # 저장 전엔 그대로
        dlg.accept()
        self.assertEqual(self.settings.get('bubble_style'), 'pastel')

    def test_cancel_keeps_setting(self):
        self.settings.set('bubble_style', 'mono')
        dlg = self.keep(BubbleStyleDialog(self.settings))
        self.assertEqual(dlg.selected(), 'mono')
        dlg.select('mint')
        dlg.reject()
        self.assertEqual(self.settings.get('bubble_style'), 'mono')

    def test_preview_paints_style_colour(self):
        dlg = self.keep(BubbleStyleDialog(self.settings))
        card = dlg.cards['pastel']
        img = card.grab().toImage()
        sx = img.width() / card.width()
        # 내 말풍선 왼쪽 안쪽(글자 없는 곳)
        x, y = int((card.width() - 2 - 10 - 104 + 6) * sx), int((2 + 46 + 22) * sx)
        self.assertEqual(img.pixelColor(x, y).name().upper(), '#FFD6E0')


class FriendManageTests(V3TestBase):
    def test_hidden_and_blocked_tabs(self):
        self.state.set_hidden('김철수', True)
        self.conn.push('block_list', blocked=['이영희'])
        dlg = self.keep(FriendManageDialog(self.state))
        self.assertEqual(dlg.hidden_names(), ['김철수'])
        self.assertEqual(rows(dlg.hidden_list), ['김철수'])
        self.assertEqual(rows(dlg.blocked_list), ['이영희'])
        self.assertEqual(dlg.tabs.tabText(0), '숨긴 친구 1')
        row = dlg.hidden_list.itemWidget(dlg.hidden_list.item(0))
        row.button.click()
        self.assertFalse(self.state.is_hidden('김철수'))
        self.assertEqual(rows(dlg.hidden_list), [None])   # '숨긴 친구가 없습니다' 안내 행
        brow = dlg.blocked_list.itemWidget(dlg.blocked_list.item(0))
        brow.button.click()
        self.assertEqual(self.conn.last('block_set'), {'username': '이영희', 'blocked': False})
        self.conn.push('block_list', blocked=[])
        self.assertEqual(dlg.tabs.tabText(1), '차단한 친구 0')


class LockSettingsTests(V3TestBase):
    def test_set_change_clear(self):
        dlg = self.keep(LockSettingsDialog(self.state, self.settings))
        self.assertFalse(dlg.lock_now_button.isEnabled())
        self.assertTrue(dlg.current_edit.isHidden())
        self.assertIn('4자리', dlg.set_new_pin('12', '12'))
        self.assertIn('다릅니다', dlg.set_new_pin('1234', '4321'))
        self.assertIsNone(dlg.set_new_pin('1234', '1234'))
        self.assertTrue(lock.verify_pin(self.settings, '1234'))
        self.assertEqual(dlg.set_button.text(), '비밀번호 변경')
        self.assertIn('현재 비밀번호', dlg.set_new_pin('5555', '5555', '0000'))
        self.assertIsNone(dlg.set_new_pin('5555', '5555', '1234'))
        self.assertTrue(lock.verify_pin(self.settings, '5555'))
        self.assertIn('일치하지', dlg.clear('1234'))
        self.assertIsNone(dlg.clear('5555'))
        self.assertFalse(lock.has_pin(self.settings))

    def test_minutes(self):
        dlg = self.keep(LockSettingsDialog(self.state, self.settings))
        self.assertEqual(dlg.minutes_combo.currentData(), 5)
        self.assertEqual([dlg.minutes_combo.itemData(i) for i in range(dlg.minutes_combo.count())], [1, 5, 10, 30])
        dlg.minutes_combo.setCurrentIndex(dlg.minutes_combo.findData(10))
        self.assertEqual(self.settings.get('lock_minutes'), 10)

    def test_lock_now_locks_main_window(self):
        win = self.make_window()
        dlg = self.keep(LockSettingsDialog(self.state, self.settings, win))
        self.assertTrue(dlg.lock_now())        # 비밀번호 없으면 오류
        dlg.set_new_pin('1234', '1234')
        self.assertIsNone(dlg.lock_now())
        self.assertTrue(self.state.locked)
        self.assertFalse(win.lock_screen.isHidden())


if __name__ == '__main__':
    unittest.main()
