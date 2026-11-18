# -*- coding: utf-8 -*-
"""2026-11-18 클라이언트 보안 · 성능 점검에서 고친 것 (화면 0.5초 작업 뒤 client/kakao 다시 훑기)

- 대화 저장(zip): 받아 둔 첨부가 수백 개 이어져도 호출이 겹치지 않고(RecursionError 로 멈춤), 원본은 디스크에 둔다
- 음성 메시지: WAV 머리의 표본율 · 채널을 믿지 않는다 (200Hz → 16kHz 로 늘리며 수 GB)
- 비밀 메시지: 복호화한 내용이 {'text': 글자} 가 아니면 읽지 못한 메시지로 / 키 파일 이름이 keys 폴더를 벗어나지 않는다
- 잠금 중: 단축키가 듣지 않고, 알림 내용을 숨긴다
- 설정 파일은 본인만 읽는다 (0600)
- 접속 상태만 바뀐 user_updated 는 그 사람 행만 다시 그린다 (조직도 통째로 다시 만들기 · 채팅 목록 갱신 없음)
- 트레이로 숨긴 메인 창의 목록은 다시 보일 때 그린다
- 닫은 모달 대화상자(대화상대 선택 · 프로필 · 사진 보기)는 지운다
"""

import os
import struct
import sys
import tempfile
import unittest
import wave
import io
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.kakao_fakes import app, make_demo_state  # noqa: E402


def pump(ms=0):
    from PyQt5.QtTest import QTest
    from PyQt5.QtCore import QCoreApplication, QEvent
    QTest.qWait(ms)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class ExportCollectorTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def messages(self, n):
        out = []
        for i in range(1, n + 1):
            self.state.attachments[1000 + i] = (b'data-%d' % i, f'f{i}.txt', 'text/plain')
            out.append({'msg_id': i, 'kind': 'file', 'deleted': False,
                        'attachment': {'attachment_id': 1000 + i, 'name': f'f{i}.txt'}})
        return out

    def test_many_cached_attachments_finish_without_recursion(self):
        from client.kakao.chat_export import AttachmentCollector, write_zip
        n = sys.getrecursionlimit() + 200
        collector = AttachmentCollector(self.state, self.messages(n))
        done = []
        collector.finished.connect(lambda files, notes: done.append((files, notes)))
        collector.start()
        self.assertEqual(len(done), 1)
        files, notes = done[0]
        self.assertEqual((len(files), notes), (n, {}))
        self.assertFalse(self.state.quiet_attachment_errors)
        rel, kept = files[7]
        self.assertTrue(os.path.isfile(kept))                   # 원본은 메모리가 아니라 임시 폴더에
        out = os.path.join(tempfile.mkdtemp(prefix='kcs_test_'), 'a.zip')
        write_zip(out, '대화', 'csv', {7: files[7], 8: ('files/inline.txt', b'inline')})
        with zipfile.ZipFile(out) as zf:
            self.assertEqual(zf.read(rel), b'data-7')
            self.assertEqual(zf.read('files/inline.txt'), b'inline')
        collector.cleanup()
        self.assertFalse(os.path.exists(kept))

    def test_cancel_removes_spooled_files(self):
        from client.kakao.chat_export import AttachmentCollector
        msgs = self.messages(2) + [{'msg_id': 99, 'kind': 'file', 'deleted': False,
                                     'attachment': {'attachment_id': 5555, 'name': 'wait.txt'}}]
        collector = AttachmentCollector(self.state, msgs)
        collector.start()                                        # 앞의 둘은 바로, 셋째는 서버를 기다린다
        kept = collector.files[1][1]
        self.assertTrue(os.path.isfile(kept))
        collector.cancel()
        self.assertFalse(os.path.exists(kept))


def wav(rate, channels=1, seconds=0.1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b'\x01\x00' * channels * int(rate * seconds))
    return buf.getvalue()


class VoiceHeaderTests(unittest.TestCase):
    def test_normal_voice_message_plays(self):
        from client.kakao.voice import wav_info
        pcm, secs = wav_info(wav(16000, seconds=0.5))
        self.assertAlmostEqual(secs, 0.5, places=2)
        pcm, secs = wav_info(wav(44100, channels=2, seconds=0.5))
        self.assertAlmostEqual(secs, 0.5, places=1)

    def test_header_outside_voice_range_is_refused(self):
        from client.kakao.voice import wav_info
        for rate in (200, 1000, 96000):
            self.assertEqual(wav_info(wav(rate)), (b'', 0.0), rate)
        self.assertEqual(wav_info(wav(16000, channels=6)), (b'', 0.0))
        bad = bytearray(wav(16000))
        bad[24:28] = struct.pack('<I', 0)                        # 표본율 0
        self.assertEqual(wav_info(bytes(bad)), (b'', 0.0))

    def test_length_is_capped(self):
        from client.kakao.voice import wav_info
        from client.kakao.state import MAX_VOICE_SECONDS
        _pcm, secs = wav_info(wav(8000, seconds=MAX_VOICE_SECONDS * 3))
        self.assertLessEqual(secs, MAX_VOICE_SECONDS * 1.5 + 0.01)


class SecretPayloadTests(unittest.TestCase):
    def setUp(self):
        from client.kakao import secret
        self.secret = secret
        self.dir = tempfile.mkdtemp(prefix='kcs_keys_')
        self.a = secret.SecretKeyring('가', self.dir)
        self.b = secret.SecretKeyring('나', self.dir)

    def blob(self, obj):
        import base64
        import json
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = self.a.room_key('r1', self.b.public_key_b64)
        nonce = os.urandom(12)
        raw = json.dumps(obj).encode('utf-8')
        return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, raw, b'r1')).decode('ascii')

    def test_normal_message(self):
        blob = self.a.encrypt('r1', self.b.public_key_b64, '비밀 안녕')
        self.assertEqual(self.b.decrypt('r1', self.a.public_key_b64, blob), '비밀 안녕')

    def test_wrong_shape_is_unreadable_not_an_exception(self):
        for obj in ([], 1, 'text', {'text': {'x': 1}}, {'text': ['a']}):
            self.assertIsNone(self.b.decrypt('r1', self.a.public_key_b64, self.blob(obj)), obj)

    def test_key_files_stay_in_keys_folder(self):
        self.assertEqual(os.path.basename(self.a.path), '가.key')            # 보통 이름은 예전 그대로
        ring = self.secret.SecretKeyring('../../밖', self.dir)
        self.assertEqual(os.path.dirname(os.path.abspath(ring.path)), os.path.abspath(self.dir))
        peers = self.secret.PeerKeys('a/b', self.dir)
        self.assertEqual(os.path.dirname(os.path.abspath(peers.path)), os.path.abspath(self.dir))


class LockedWindowTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.main_window import MainWindow, apply_app_theme
        from client.kakao.lock import set_pin
        app()
        self.state, self.conn = make_demo_state()
        apply_app_theme(self.state.settings)
        set_pin(self.state.settings, '1234')
        self.win = MainWindow(self.state, self.state.settings)
        self.addCleanup(self.win.close)
        self.win.show()
        pump()

    def shortcut(self, text):
        from PyQt5.QtGui import QKeySequence
        return next(sc for sc in self.win.shortcuts if sc.key() == QKeySequence(text))

    def test_shortcuts_do_nothing_while_locked(self):
        from PyQt5.QtWidgets import QDialog
        self.assertTrue(self.win.is_locked())
        with mock.patch.object(QDialog, 'exec_', return_value=0) as opened:    # Ctrl+K = 빠른 이동 창
            self.shortcut('Ctrl+K').activated.emit()
            opened.assert_not_called()
            self.win.unlock()
            self.shortcut('Ctrl+K').activated.emit()
            opened.assert_called_once()

    def test_notifications_hide_content_while_locked(self):
        shown = []
        self.win.tray = mock.Mock()
        self.win.tray.supportsMessages.return_value = True
        self.win.tray.showMessage.side_effect = lambda title, body, *_: shown.append((title, body))
        with mock.patch.object(type(self.win), 'should_notify', return_value=True):
            self.win.show_notification('일정 변경', '연봉 협상 회의', 'r1')
            self.win.unlock()
            self.win.show_notification('일정 변경', '연봉 협상 회의', 'r1')
        self.win.tray = None
        self.assertEqual(shown[0], ('KCS Talk', '새 알림이 있습니다'))
        self.assertEqual(shown[1], ('일정 변경', '연봉 협상 회의'))


class SettingsFileModeTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Windows 는 파일 권한 비트를 쓰지 않는다')
    def test_settings_file_is_private(self):
        from client.kakao.settings import LocalSettings
        folder = tempfile.mkdtemp(prefix='kcs_settings_')
        settings = LocalSettings('홍길동', base_dir=folder)
        settings.set('room_drafts', {'r1': '쓰다 만 글'})
        self.assertEqual(os.stat(settings.path).st_mode & 0o777, 0o600)


class PresenceOnlyUpdateTests(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_presence_only_update_skips_room_list_and_marks_presence(self):
        seen = {'presence': [], 'rooms': 0}
        self.state.presence_changed.connect(lambda n: seen['presence'].append(n))
        self.state.rooms_changed.connect(lambda: seen.__setitem__('rooms', seen['rooms'] + 1))
        user = dict(self.state.user('이영희'))
        self.conn.push('user_updated', user=dict(user, online=not user.get('online'), presence='away'))
        self.assertEqual((seen['presence'], seen['rooms']), (['이영희'], 0))
        self.conn.push('user_updated', user=dict(self.state.user('이영희'), status_message='외근'))
        self.assertEqual((seen['presence'], seen['rooms']), (['이영희'], 1))   # 다른 정보가 바뀌면 예전처럼

    def test_org_redraws_only_that_member(self):
        from client.kakao import org_tab
        from tests.kakao_fakes import expand_org
        tab = org_tab.OrgTab(self.state, self.state.settings)
        self.addCleanup(tab.close)
        expand_org(tab)
        with mock.patch.object(tab, 'refresh', wraps=tab.refresh) as full, \
                mock.patch.object(tab, 'refresh_members', wraps=tab.refresh_members) as rows:
            user = dict(self.state.user('이영희'))
            self.conn.push('user_status_changed', username='이영희', status='offline' if user.get('online') else 'online')
            self.conn.push('user_updated', user=dict(self.state.user('이영희')))
            self.conn.push('user_updated', user=dict(self.state.user('이영희'), presence='away'))
            tab._users_relay.flush()
            pump(200)
            full.assert_not_called()
            self.assertTrue(rows.called)


class HiddenWindowRefreshTests(unittest.TestCase):
    def test_tray_hidden_window_redraws_when_shown_again(self):
        from client.kakao.main_window import MainWindow, apply_app_theme
        app()
        state, conn = make_demo_state()
        apply_app_theme(state.settings)
        win = MainWindow(state, state.settings)
        self.addCleanup(win.close)
        win.show()
        win.set_tab('chats')
        pump()
        chats = win.tabs['chats']
        win.hide()                                   # X → 트레이
        pump()
        with mock.patch.object(chats, 'refresh', wraps=chats.refresh) as refresh:
            self.assertFalse(chats.refresh_when_visible())
            refresh.assert_not_called()
            win.show()
            pump()
            refresh.assert_called_once()

    def test_never_shown_list_still_draws_at_once(self):
        from client.kakao.chats_tab import ChatsTab
        app()
        state, _conn = make_demo_state()
        tab = ChatsTab(state, state.settings)
        self.addCleanup(tab.close)
        self.assertTrue(tab.refresh_when_visible())


class SplitRoomsTests(unittest.TestCase):
    def test_split_pane_keeps_only_recent_rooms(self):
        from client.kakao import main_window
        from client.kakao.main_window import MainWindow, apply_app_theme
        app()
        state, conn = make_demo_state()
        for i in range(8):
            conn.push('room_updated', room={'room_id': f'g{i}', 'type': 'group', 'name': f'방{i}',
                                            'members': [state.me, '김철수', '이영희'], 'created_at': '2026-11-18T09:00:00',
                                            'read_pointers': {}, 'unread_count': 0})
        apply_app_theme(state.settings)
        win = MainWindow(state, state.settings)
        self.addCleanup(win.close)
        win.resize(main_window.SPLIT_MIN_WINDOW + 40, 760)
        win.show()
        pump()
        self.assertTrue(win.is_split())
        rooms = [f'g{i}' for i in range(8)]
        for rid in rooms:
            win.open_room(rid)
        win.open_room('g2')                                   # 다시 본 방은 최근으로
        win.open_room('g7')
        pump()
        docked = [rid for rid, w in win.room_windows.items() if w.parent() is win.room_pane]
        self.assertLessEqual(len(docked), main_window.SPLIT_KEEP_ROOMS)
        self.assertIn('g7', docked)
        self.assertIn('g2', docked)
        self.assertIs(win.room_pane.currentWidget(), win.room_windows['g7'])


def _text_message(msg_id, sender, content, room_id='r1', minutes_ago=0):
    from datetime import datetime, timedelta
    return dict(msg_id=msg_id, room_id=room_id, sender=sender, kind='text', content=content,
                timestamp=(datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds'),
                reply_to=None, mentions=[], reactions={}, deleted=False, attachment=None, event_id=None, poll=None)


class ProgressiveRenderTests(unittest.TestCase):
    """채팅방은 말풍선을 RENDER_STEP 개씩 나눠 그린다 (느린 PC 에서 채팅방 열기 · 이전 대화가 1초 가까이 걸렸다)"""

    def setUp(self):
        from client.kakao.chat_room import ChatRoomWindow
        app()
        self.state, self.conn = make_demo_state()
        for i in range(60):                       # 이틀에 걸친 메시지 60개 (날짜 구분선이 생기게)
            self.conn.push('msg_new', message=_text_message(100 + i, '김철수' if i % 3 else '이영희', f'메시지 {i}',
                                                             minutes_ago=(60 - i) * 60))
        self.Win = ChatRoomWindow
        self.addCleanup(setattr, ChatRoomWindow, 'RENDER_STEP', ChatRoomWindow.RENDER_STEP)
        ChatRoomWindow.RENDER_STEP = 10

    def open(self):
        win = self.Win(self.state, 'r1')
        self.addCleanup(win.close)
        win.resize(420, 720)
        win.show()
        return win

    def ids(self, win):
        return [m['msg_id'] for m in win._order]

    def test_first_screen_then_the_rest_in_steps(self):
        win = self.open()
        timeline = [m['msg_id'] for m in self.state.timeline_messages('r1')]
        first = self.ids(win)                                       # 처음에는 맨 아래부터 몇 덩어리만 (덩어리 = 10개)
        self.assertLess(len(first), len(timeline))
        self.assertLessEqual(len(first), 20)
        self.assertEqual(first, timeline[-len(first):])
        pump(200)
        self.assertEqual(self.ids(win), timeline[-win._render_count:])   # 이벤트 사이에 나머지를 위로 붙였다
        self.assertEqual(sorted(win.rows), sorted(self.ids(win)))
        # 같은 날짜 구분선은 하나씩, 날짜가 바뀌는 메시지 바로 위에
        lay = win.list_layout
        seps = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget() in win._seps.values()]
        self.assertEqual(len(seps), len(set(win._msg_date(m) for m in win._order)))
        for d, sep in win._seps.items():
            first = next(m for m in win._order if win._msg_date(m) == d)
            self.assertEqual(lay.indexOf(sep) + 1, lay.indexOf(win.rows[first['msg_id']]))

    def test_same_result_as_drawing_at_once(self):
        win = self.open()
        pump(200)
        stepped = (self.ids(win), [(r.first, r.last) for r in (win.rows[i] for i in self.ids(win)) if hasattr(r, 'first')])
        self.Win.RENDER_STEP = 0
        whole = self.open()
        self.assertEqual(stepped, (self.ids(whole), [(r.first, r.last) for r in (whole.rows[i] for i in self.ids(whole))
                                                      if hasattr(r, 'first')]))

    def test_finding_an_old_message_draws_it_at_once(self):
        win = self.open()
        target = self.ids(win)[0] - 30
        self.assertNotIn(target, win.rows)
        self.assertTrue(win.focus_message(target))
        self.assertIn(target, win.rows)


class BigRoomReadTests(unittest.TestCase):
    """큰 방 읽음 처리: 안 읽은 수는 정렬해 둔 읽은 위치에서 이진 탐색, 읽은 위치가 옮겨진 구간의 행만, 몰려오면 묶어서"""

    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def brute(self, rid, m):
        room = self.state.room(rid)
        return sum(1 for u in room['members'] if room.get('read_pointers', {}).get(u, 0) < m['msg_id'])

    def test_unread_count_matches_counting_everyone(self):
        rid = 'r1'
        msgs = [m for m in self.state.room_messages(rid) if m.get('kind') != 'system']
        for reader, last in (('김철수', 3), ('이영희', 9), (self.state.me, 11), ('김철수', 11)):
            self.conn.push('read_update', room_id=rid, username=reader, last_read_msg_id=last)
            for m in msgs:
                self.assertEqual(self.state.message_unread(rid, m), self.brute(rid, m), (reader, last, m['msg_id']))
        room = self.state.room(rid)                       # 읽은 위치를 직접 바꿔도 (테스트 · 예전 코드) 맞게 다시 센다
        room['read_pointers']['이영희'] = 1
        self.assertEqual(self.state.message_unread(rid, msgs[-1]), self.brute(rid, msgs[-1]))

    def test_room_refreshes_only_rows_in_the_moved_span_and_batches_bursts(self):
        from client.kakao.chat_room import ChatRoomWindow
        self.state.room('r1').setdefault('read_pointers', {})['이영희'] = 0      # 아직 하나도 안 읽은 사람
        win = ChatRoomWindow(self.state, 'r1')
        self.addCleanup(win.close)
        win.show()
        pump()
        win.flush_read_refresh()
        touched = []
        for mid, row in win.rows.items():
            original = row.refresh_unread
            row.refresh_unread = lambda mid=mid, original=original: (touched.append(mid), original())
        room = self.state.room('r1')
        pointers = room.get('read_pointers', {})
        reader, start = '이영희', pointers.get('이영희', 0)
        top = max(m for m in win.rows if isinstance(m, int))
        self.assertGreaterEqual(top - start, 4)
        self.conn.push('read_update', room_id='r1', username=reader, last_read_msg_id=start + 1)
        self.assertTrue(touched)
        self.assertTrue(all(start < mid <= start + 1 for mid in touched))   # 새로 읽은 메시지만
        touched.clear()
        for last in (start + 2, start + 3, start + 4):               # 이어서 몰려오는 것은 묶어서 끝에 한 번
            self.conn.push('read_update', room_id='r1', username=reader, last_read_msg_id=last)
        self.assertEqual(touched, [])
        win.flush_read_refresh()
        self.assertEqual(sorted(set(touched)),
                         [m for m in sorted(win.rows) if isinstance(m, int) and start + 1 < m <= start + 4])

    def test_rows_are_trimmed_while_following_the_bottom(self):
        from client.kakao.chat_room import ChatRoomWindow, RENDER_PAGE
        win = ChatRoomWindow(self.state, 'r1')
        self.addCleanup(win.close)
        win.show()
        pump()
        for i in range(RENDER_PAGE * 2 + 5):
            self.conn.push('msg_new', message=_text_message(1000 + i, '김철수', f'공지 뒤 {i}'))
        self.assertLessEqual(len(win._order), win.RENDER_MAX)
        self.assertEqual(win._order[-1]['msg_id'], 1000 + RENDER_PAGE * 2 + 4)
        self.assertGreater(win._hidden_older, 0)                   # 정리한 메시지는 받아 둔 채로 - 위로 올리면 다시 그린다


class ScreenDetailTests(unittest.TestCase):
    def test_reaction_picker_styles_once(self):
        from client.kakao.chat_room import ReactionPicker
        app()
        dlg = ReactionPicker(mine={'👍'}, counts={'👍': 2})
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.buttons['👍'].objectName(), 'reactionPickMine')
        self.assertTrue(all(b.styleSheet() == '' for b in dlg.buttons.values()))
        self.assertIn('reactionPickMine', dlg.styleSheet())

    def test_members_strip_builds_photos_only_when_opened(self):
        from client.kakao.chat_room import ChatRoomWindow
        app()
        state, _conn = make_demo_state()
        win = ChatRoomWindow(state, 'r1')
        self.addCleanup(win.close)
        strip = win.members_strip
        self.assertFalse(strip.expanded)
        self.assertTrue(strip._chips_stale)                        # 접힌 채로는 사진 칩을 만들지 않는다
        win.set_members_strip(True)
        self.assertFalse(strip._chips_stale)
        self.assertIn(state.me, strip.chips)

    def test_notification_text_and_private_home(self):
        from client.kakao import osenv
        body = '<a href="file:///etc">공지</a> & 1 < 2'
        with mock.patch.object(osenv, 'is_linux', return_value=True):
            self.assertEqual(osenv.notification_text(body), '&lt;a href="file:///etc"&gt;공지&lt;/a&gt; &amp; 1 &lt; 2')
        with mock.patch.object(osenv, 'is_linux', return_value=False):
            self.assertEqual(osenv.notification_text(body), body)
        if os.name == 'nt':
            return
        home = os.path.join(tempfile.mkdtemp(prefix='kcs_home_'), '.kcs_messenger')
        for sub in ('', 'logs', 'recordings'):
            os.makedirs(os.path.join(home, sub), exist_ok=True)
            os.chmod(os.path.join(home, sub), 0o755)
        with mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': home}):
            self.assertTrue(osenv.secure_app_home())
        for sub in ('', 'logs', 'recordings'):
            self.assertEqual(os.stat(os.path.join(home, sub)).st_mode & 0o777, 0o700, sub)


class DialogReleaseTests(unittest.TestCase):
    def test_closed_modal_dialogs_are_deleted(self):
        from PyQt5.QtWidgets import QDialog, QWidget
        from client.kakao.friends_tab import FriendsTab
        app()
        state, _conn = make_demo_state()
        parent = QWidget()
        self.addCleanup(parent.close)
        tab = FriendsTab(state, state.settings, parent) if 'parent' in FriendsTab.__init__.__code__.co_varnames \
            else FriendsTab(state, state.settings)
        self.addCleanup(tab.close)
        with mock.patch.object(QDialog, 'exec_', return_value=0):
            tab.show_create_dialog()
            tab.show_profile('김철수')
            tab.edit_profile()
        pump()
        left = [d for d in tab.window().findChildren(QDialog)]
        self.assertEqual(left, [])

    def test_image_viewer_save_marks_file_from_network(self):
        from client.kakao.room_dialogs import ImageViewer
        from client.kakao import osenv
        app()
        viewer = ImageViewer(b'img', 'a.png')
        self.addCleanup(viewer.close)
        path = os.path.join(tempfile.mkdtemp(prefix='kcs_save_'), 'a.png')
        with mock.patch('client.kakao.room_dialogs.QFileDialog.getSaveFileName', return_value=(path, '')), \
                mock.patch.object(osenv, 'mark_from_network') as mark:
            self.assertTrue(viewer.save())
        mark.assert_called_once_with(path)


if __name__ == '__main__':
    unittest.main()
