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
