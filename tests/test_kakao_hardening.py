# -*- coding: utf-8 -*-
"""전체 점검(2026-11-17) 뒤 강화한 규칙의 회귀 테스트 - 받은 글자·이미지·음성·파일을 다루는 공통 규칙"""

import base64
import os
import socket
import tempfile
import threading
import time
import unittest
import zlib
from unittest import mock

from PyQt5.QtCore import Qt, QBuffer, QByteArray, QEvent, QIODevice, QPoint
from PyQt5.QtGui import QHelpEvent, QImage
from PyQt5.QtWidgets import QApplication, QLabel, QListWidget, QToolTip, QWidget

from tests.kakao_fakes import app, make_demo_state

from client.kakao import call, net, osenv, widgets

MARK = '<i>zz9</i>'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


def png_bytes(width=8, height=8):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(Qt.red)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, 'PNG')
    buf.close()
    return bytes(ba)


def b64(data):
    return base64.b64encode(data).decode('ascii')


# ================================================================== 글자: 라벨 · 툴팁은 서식으로 해석하지 않는다
class PlainTextGuardTests(unittest.TestCase):
    def setUp(self):
        widgets.install_text_guard(app())

    def shown(self, widget):
        widget.show()
        self.addCleanup(widget.close)
        app().processEvents()
        return widget

    def test_installing_twice_keeps_one_guard(self):
        first = app()._text_guard
        widgets.install_text_guard(app())
        self.assertIs(app()._text_guard, first)

    def test_label_text_is_plain_by_default(self):
        label = self.shown(QLabel(MARK + '이름'))
        self.assertEqual(label.textFormat(), Qt.PlainText)

    def test_label_that_asks_for_rich_text_keeps_it(self):
        label = QLabel('<b>굵게</b>')
        label.setTextFormat(Qt.RichText)
        self.shown(label)
        self.assertEqual(label.textFormat(), Qt.RichText)

    def test_plain_helper_marks_label_before_it_is_shown(self):
        label = widgets.plain(QLabel(MARK))
        self.assertEqual(label.textFormat(), Qt.PlainText)

    def tooltip_label_for(self, widget, pos=QPoint(2, 2)):
        """widget 의 pos 에 툴팁을 띄우고, Qt 가 그 툴팁을 그리는 라벨을 돌려준다"""
        QToolTip.hideText()
        app().processEvents()
        QApplication.sendEvent(widget, QHelpEvent(QEvent.ToolTip, pos, widget.mapToGlobal(pos)))
        tips = [w for w in QApplication.topLevelWidgets()
                if w.metaObject().className() == 'QTipLabel' and w.isVisible()]
        self.addCleanup(QToolTip.hideText)
        self.assertEqual(len(tips), 1)
        return tips[0]

    def test_widget_tooltip_is_shown_as_plain_text(self):
        w = QWidget()
        w.setToolTip(MARK + '홍길동')
        self.shown(w)
        tip = self.tooltip_label_for(w)
        self.assertEqual((tip.text(), tip.textFormat()), (MARK + '홍길동', Qt.PlainText))

    def test_list_item_tooltip_is_shown_as_plain_text(self):
        lst = QListWidget()
        lst.addItem('행')
        lst.item(0).setToolTip(MARK + '내용')
        self.shown(lst)
        tip = self.tooltip_label_for(lst.viewport(), lst.visualItemRect(lst.item(0)).center())
        self.assertEqual((tip.text(), tip.textFormat()), (MARK + '내용', Qt.PlainText))


class ScreensShowUserTextAsPlainTests(unittest.TestCase):
    """이름 · 상태메시지 · 방 이름 · 파일 이름에 꺾쇠가 들어 있어도 화면은 글자 그대로 보여 준다"""

    KEYS = {'display_name', 'name', 'status_message', 'department', 'title', 'company', 'description', 'location'}

    def mutate(self, value):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if isinstance(item, str) and key in self.KEYS:
                    value[key] = MARK + item
                else:
                    self.mutate(item)
        elif isinstance(value, list):
            for item in value:
                self.mutate(item)

    def setUp(self):
        from client.kakao import main_window
        self.state, self.conn = make_demo_state()
        main_window.apply_app_theme(self.state.settings)
        for user in self.state.users.values():
            user.setdefault('display_name', user['username'])
            user['status_message'] = user.get('status_message') or '상태'
        self.mutate(self.state.users)
        self.mutate(self.state.rooms)
        self.mutate(self.state.events)
        self.state.users_changed.emit()
        self.state.rooms_changed.emit()
        self.open = []

    def show(self, widget):
        widget.show()
        self.open.append(widget)
        self.addCleanup(widget.close)
        return widget

    def rendered_markup(self):
        app().processEvents()
        found = []
        for w in QApplication.allWidgets():
            if isinstance(w, QLabel) and w.testAttribute(Qt.WA_WState_Polished) and MARK in w.text():
                fmt = w.textFormat()
                if fmt == Qt.RichText or (fmt == Qt.AutoText and Qt.mightBeRichText(w.text())):
                    found.append(f'{type(w.parentWidget()).__name__}: {w.text()[:40]}')
        return found

    def test_main_window_chat_room_and_dialogs(self):
        from client.kakao.main_window import MainWindow
        from client.kakao.chat_room import ChatRoomWindow
        from client.kakao.friends_tab import ProfileDialog
        from client.kakao.chats_tab import InviteToRoomDialog
        from client.kakao.event_dialog import EventDetailDialog
        from client.kakao.file_guard import FileBlockedDialog
        main = self.show(MainWindow(self.state, self.state.settings))
        self.show(ChatRoomWindow(self.state, 'r1'))
        self.show(ProfileDialog(self.state, '김철수'))
        self.show(InviteToRoomDialog(self.state, '김철수'))
        self.show(EventDetailDialog(self.state, self.state.events[1]))
        self.show(FileBlockedDialog(self.state, {'name': MARK + 'a.exe', 'detail': MARK + '막힘', 'size': 10}))
        main.show_toast(MARK + '안내')
        self.assertEqual(self.rendered_markup(), [])


# ================================================================== 이미지: 정해진 형식 · 크기만 푼다
class ImageDecodeTests(unittest.TestCase):
    def setUp(self):
        app()

    def test_png_is_decoded(self):
        image = widgets.decode_image(png_bytes(8, 6))
        self.assertEqual((image.width(), image.height()), (8, 6))

    def test_vector_image_is_refused(self):
        self.assertIsNone(widgets.decode_image(SVG))

    def test_image_with_too_many_pixels_is_refused(self):
        self.assertIsNone(widgets.decode_image(png_bytes(400, 300), max_pixels=100 * 100))

    def test_broken_data_is_refused(self):
        self.assertIsNone(widgets.decode_image(b'not an image'))
        self.assertIsNone(widgets.decode_image(b''))

    def test_decode_pixmap_follows_the_same_rules(self):
        self.assertFalse(widgets.decode_pixmap(png_bytes()).isNull())
        self.assertIsNone(widgets.decode_pixmap(SVG))

    def test_avatar_refuses_vector_image(self):
        self.assertIsNone(widgets.photo_avatar_pixmap(SVG, 40))
        self.assertIsNotNone(widgets.photo_avatar_pixmap(png_bytes(), 40))

    def test_chat_thumbnail_refuses_vector_image(self):
        from client.kakao.chat_room import ImageThumb
        thumb = ImageThumb()
        thumb.set_image(SVG, 200)
        self.assertEqual(thumb.text(), '사진을 표시할 수 없습니다')
        thumb.set_image(png_bytes(), 200)
        self.assertEqual(thumb.text(), '')

    def test_video_frame_only_accepts_jpeg(self):
        self.assertTrue(call.jpeg_decode(b64(png_bytes())).isNull())
        self.assertTrue(call.jpeg_decode(b64(SVG)).isNull())


# ================================================================== 통화 음성: 풀린 크기에 상한
class AudioDecodeTests(unittest.TestCase):
    def test_round_trip_of_one_frame(self):
        pcm = bytes(range(256)) * 5
        data, codec = call.encode_audio(pcm)
        self.assertEqual(call.decode_audio(data, codec), pcm)

    def test_frame_that_expands_past_the_limit_is_dropped(self):
        packed = zlib.compress(b'\0' * (call.MAX_AUDIO_FRAME_BYTES + 2), 9)
        self.assertEqual(call.decode_audio(b64(packed), 'zpcm'), b'')

    def test_frame_at_the_limit_is_kept(self):
        packed = zlib.compress(b'\0' * call.MAX_AUDIO_FRAME_BYTES, 9)
        self.assertEqual(len(call.decode_audio(b64(packed), 'zpcm')), call.MAX_AUDIO_FRAME_BYTES)

    def test_uncompressed_frame_past_the_limit_is_dropped(self):
        self.assertEqual(call.decode_audio(b64(b'\0' * (call.MAX_AUDIO_FRAME_BYTES + 2)), 'pcm'), b'')


# ================================================================== 연결: 이상한 줄이 와도 읽기가 멈추지 않는다
class ReadLoopTests(unittest.TestCase):
    def setUp(self):
        app()
        self.ours, self.theirs = socket.socketpair()
        self.addCleanup(self.theirs.close)
        self.conn = net.ChatConnection()
        self.conn._sock = self.ours
        self.events, self.lost = [], []
        self.conn.event_received.connect(lambda kind, payload: self.events.append(kind), Qt.DirectConnection)
        self.conn.disconnected.connect(self.lost.append, Qt.DirectConnection)
        self.reader = threading.Thread(target=self.conn._read_loop, daemon=True)
        self.reader.start()

    def wait(self, done, seconds=3.0):
        end = time.time() + seconds
        while time.time() < end and not done():
            time.sleep(0.01)
        return done()

    def test_line_whose_type_is_not_text_is_skipped(self):
        self.theirs.sendall(b'{"type": 1}\n{"type": ["a"]}\n{"type": "hello"}\n')
        self.assertTrue(self.wait(lambda: self.events == ['hello']))
        self.assertTrue(self.reader.is_alive())

    def test_line_longer_than_the_limit_ends_the_connection(self):
        with mock.patch.object(net, 'MAX_LINE_BYTES', 1024):
            self.theirs.sendall(b'x' * 4096)
            self.assertTrue(self.wait(lambda: bool(self.lost)))
        self.assertFalse(self.conn.is_connected)

    def test_error_inside_the_loop_ends_the_connection_cleanly(self):
        def boom(*_):
            raise RuntimeError('깨진 처리')
        with mock.patch.object(net.json, 'loads', boom):
            self.theirs.sendall(b'{"type": "hello"}\n')
            self.assertTrue(self.wait(lambda: bool(self.lost)))
        self.assertFalse(self.conn.is_connected)


class BeforeLoginRequestTests(unittest.TestCase):
    def test_reply_without_line_end_is_cut_off(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        self.addCleanup(server.close)

        def serve():
            peer, _ = server.accept()
            try:
                peer.recv(4096)
                peer.sendall(b'y' * (net.MAX_PRELOGIN_BYTES * 2))
                time.sleep(0.3)
            except OSError:
                pass
            finally:
                peer.close()
        threading.Thread(target=serve, daemon=True).start()
        with self.assertRaises(OSError):
            net.fetch_companies('127.0.0.1', server.getsockname()[1], timeout=3.0)


# ================================================================== 받은 파일 열기
class OpenRulesTests(unittest.TestCase):
    def test_documents_and_media_open_directly(self):
        for name in ('보고서.pdf', 'A.PDF', '사진.jpg', '표.xlsx', '글.hwpx', '메모.txt'):
            self.assertTrue(osenv.can_open_directly(name), name)

    def test_direction_marks_cannot_hide_the_real_extension(self):
        self.assertEqual(osenv.safe_filename('report\u202efdp.exe'), 'reportfdp.exe')
        self.assertFalse(osenv.can_open_directly('report\u202efdp.exe'))

    def test_programs_scripts_and_unknown_types_do_not(self):
        for name in ('설치.exe', 'run.bat', 'a.pdf.exe', 'x.lnk', 'tool.command', 'm.docm', 'a.html', '이름만', '', None):
            self.assertFalse(osenv.can_open_directly(name), name)

    def test_only_web_and_mail_addresses_are_opened(self):
        with mock.patch('PyQt5.QtGui.QDesktopServices.openUrl', return_value=True) as opened:
            self.assertTrue(osenv.open_url('https://kcs.co.kr/a?b=1'))
            self.assertTrue(osenv.open_url('mailto:a@kcs.co.kr'))
            self.assertEqual(opened.call_count, 2)
            for url in ('file:///etc/hosts', 'javascript:alert(1)', 'smb://host/share', 'ms-msdt:x', '/etc/hosts', ''):
                self.assertFalse(osenv.open_url(url), url)
            self.assertEqual(opened.call_count, 2)

    def test_reveal_command_per_os(self):
        self.assertEqual(osenv.reveal_command('/tmp/a.exe', 'mac'), ['open', '-R', '/tmp/a.exe'])
        self.assertEqual(osenv.reveal_command(r'C:\t\a.exe', 'windows'), ['explorer', '/select,', r'C:\t\a.exe'])
        self.assertIsNone(osenv.reveal_command('/tmp/a.exe', 'linux'))


class OpenedFilesFolderTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='kcs_open_')
        patcher = mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        osenv.forget_opened_files_dir()
        self.addCleanup(osenv.forget_opened_files_dir)

    def test_folder_is_private_and_inside_app_home(self):
        folder = osenv.opened_files_dir()
        self.assertTrue(folder.startswith(os.path.join(self.home, 'opened') + os.sep))
        self.assertEqual(osenv.opened_files_dir(), folder)
        if not osenv.is_windows():
            self.assertEqual(os.stat(folder).st_mode & 0o077, 0)

    def test_same_name_does_not_overwrite_an_earlier_file(self):
        first = osenv.save_opened_file('a.pdf', b'one')
        second = osenv.save_opened_file('a.pdf', b'two')
        self.assertNotEqual(first, second)
        with open(first, 'rb') as f:
            self.assertEqual(f.read(), b'one')
        self.assertTrue(second.endswith('.pdf'))

    def test_name_with_folders_stays_inside(self):
        path = osenv.save_opened_file('../../x/evil.pdf', b'd')
        self.assertEqual(os.path.dirname(path), osenv.opened_files_dir())

    def test_old_session_folders_are_removed(self):
        stale = os.path.join(self.home, 'opened', 'old')
        os.makedirs(stale)
        with open(os.path.join(stale, 'a.txt'), 'w') as f:
            f.write('x')
        old = time.time() - 3 * 24 * 3600
        os.utime(stale, (old, old))
        recent = os.path.join(self.home, 'opened', 'recent')
        os.makedirs(recent)
        osenv.opened_files_dir()
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(recent))

    def test_downloaded_mark_is_written_on_windows_only(self):
        path = osenv.save_opened_file('a.pdf', b'd')
        with mock.patch.object(osenv, 'is_windows', return_value=False):
            self.assertFalse(osenv.mark_from_network(path))
        with mock.patch.object(osenv, 'is_windows', return_value=True):
            self.assertTrue(osenv.mark_from_network(path))
        with open(path + ':Zone.Identifier', encoding='ascii') as f:
            self.assertIn('ZoneId=3', f.read())


class ChatRoomOpenAttachmentTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.chat_room import ChatRoomWindow
        self.home = tempfile.mkdtemp(prefix='kcs_open_')
        patcher = mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        osenv.forget_opened_files_dir()
        self.addCleanup(osenv.forget_opened_files_dir)
        self.state, self.conn = make_demo_state()
        self.win = ChatRoomWindow(self.state, 'r1')
        self.addCleanup(self.win.close)

    def receive(self, name, data=b'data'):
        self.win._pending_open.add(77)
        self.conn.push('attachment_data', attachment_id=77, name=name, mime='application/octet-stream', data_b64=b64(data))

    def test_document_is_saved_in_private_folder_and_opened(self):
        with mock.patch.object(osenv, 'open_path', return_value=True) as opened:
            self.receive('보고서.pdf')
        path = opened.call_args[0][0]
        self.assertEqual(os.path.dirname(path), osenv.opened_files_dir())
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'data')

    def test_program_is_not_opened_and_asks_first(self):
        with mock.patch.object(osenv, 'open_path') as opened, mock.patch.object(osenv, 'reveal_path') as revealed, \
                mock.patch.object(self.win, 'confirm', return_value=False) as asked:
            self.receive('설치.exe')
        asked.assert_called_once()
        opened.assert_not_called()
        revealed.assert_not_called()
        self.assertEqual(os.listdir(os.path.join(self.home, 'opened')) if os.path.isdir(os.path.join(self.home, 'opened')) else [], [])

    def test_program_is_shown_in_folder_after_confirmation(self):
        with mock.patch.object(osenv, 'open_path') as opened, mock.patch.object(osenv, 'reveal_path') as revealed, \
                mock.patch.object(self.win, 'confirm', return_value=True):
            self.receive('설치.exe')
        opened.assert_not_called()
        path = revealed.call_args[0][0]
        self.assertTrue(path.endswith('설치.exe') and os.path.exists(path))


class AdminFileDownloadNameTests(unittest.TestCase):
    def test_default_save_name_has_no_folders(self):
        from client.kakao.file_guard import RequestsTab
        state, conn = make_demo_state()
        tab = RequestsTab(state)
        self.addCleanup(tab.close)
        request = {'request_id': 7, 'username': '김철수', 'name': '/etc/../x/tool.pdf', 'size': 10, 'rule': 'type',
                   'detail': '', 'room_label': '', 'reason': '', 'status': 'pending', 'attachment_id': 99,
                   'created_at': '2026-10-11T09:00:00'}
        conn.push('admin_file_requests', requests=[request])
        self.assertTrue(tab.select(7))
        with mock.patch('client.kakao.file_guard.QFileDialog.getSaveFileName', return_value=('', '')) as asked:
            tab.download()
        self.assertEqual(asked.call_args[0][2], os.path.join(osenv.user_dir('downloads'), 'tool.pdf'))


# ================================================================== 잠금 PIN
class LockPinTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.settings import LocalSettings
        app()
        self.settings = LocalSettings('pin', base_dir=tempfile.mkdtemp(prefix='kcs_pin_'))

    def test_pin_is_stored_with_a_slow_hash_and_still_verifies(self):
        from client.kakao import lock
        self.assertTrue(lock.set_pin(self.settings, '1234'))
        self.assertTrue(self.settings.get('lock_pin_hash').startswith('pbkdf2:'))
        self.assertTrue(lock.verify_pin(self.settings, '1234'))
        self.assertFalse(lock.verify_pin(self.settings, '4321'))

    def test_pin_saved_by_an_older_version_unlocks_and_is_upgraded(self):
        from client.kakao import lock
        self.settings.set('lock_salt', 'ab12')
        self.settings.set('lock_pin_hash', lock.hash_pin('1234', 'ab12'))
        self.assertFalse(lock.verify_pin(self.settings, '0000'))
        self.assertTrue(lock.verify_pin(self.settings, '1234'))
        self.assertTrue(self.settings.get('lock_pin_hash').startswith('pbkdf2:'))
        self.assertTrue(lock.verify_pin(self.settings, '1234'))

    def test_repeated_wrong_pins_start_a_growing_wait(self):
        from client.kakao import lock
        waits = [lock.record_failure(self.settings, now=1000.0) for _ in range(7)]
        self.assertEqual(waits, [0, 0, 0, 0, 30, 60, 120])
        self.assertEqual(lock.blocked_seconds(self.settings, now=1000.0), 120)
        self.assertEqual(lock.blocked_seconds(self.settings, now=1119.5), 1)
        self.assertEqual(lock.blocked_seconds(self.settings, now=1121.0), 0)

    def test_wait_has_an_upper_limit_and_success_clears_it(self):
        from client.kakao import lock
        for _ in range(30):
            wait = lock.record_failure(self.settings, now=1000.0)
        self.assertEqual(wait, lock.MAX_WAIT_SECONDS)
        lock.clear_failures(self.settings)
        self.assertEqual(lock.blocked_seconds(self.settings, now=1000.0), 0)
        self.assertEqual(lock.record_failure(self.settings, now=1000.0), 0)

    def test_lock_screen_ignores_input_while_waiting(self):
        from client.kakao import lock
        lock.set_pin(self.settings, '1234')
        parent = QWidget()
        self.addCleanup(parent.close)
        screen = lock.LockScreen(self.settings, parent)
        opened = []
        screen.unlocked.connect(lambda: opened.append(1))
        for _ in range(5):
            for digit in '0000':
                screen.press_digit(digit)
            screen._reset_after_error()
        self.assertGreater(lock.blocked_seconds(self.settings), 0)
        for digit in '1234':                                   # 맞는 번호여도 기다리는 동안은 받지 않는다
            screen.press_digit(digit)
        self.assertEqual(opened, [])
        self.assertIn('뒤에', screen.hint.text())
        self.settings.set('lock_blocked_until', 0)             # 기다린 뒤
        for digit in '1234':
            screen.press_digit(digit)
        self.assertEqual(opened, [1])
        self.assertEqual(lock.failed_attempts(self.settings), 0)


# ================================================================== 암호화되지 않은 접속 안내
class PlainConnectionNoticeTests(unittest.TestCase):
    def test_only_unencrypted_connections_to_other_computers_count(self):
        from client.kakao import settings
        self.assertTrue(settings.is_plain_remote('10.20.30.40', False))
        self.assertTrue(settings.is_plain_remote('talk.example.co.kr', False))
        self.assertFalse(settings.is_plain_remote('talk.example.co.kr', True))
        for host in ('127.0.0.1', 'localhost', '::1', ''):
            self.assertFalse(settings.is_plain_remote(host, False), host)

    def test_login_window_says_so(self):
        from client.kakao.login import LoginDialog
        app()
        plain = LoginDialog(host='10.20.30.40', port=5555, fresh=True)
        self.addCleanup(plain.close)
        self.assertFalse(plain.plain_notice.isHidden())
        self.assertIn('암호화', plain.plain_notice.text())
        for kwargs in ({'host': '127.0.0.1'}, {'host': '10.20.30.40', 'tls': True}):
            dlg = LoginDialog(port=5555, fresh=True, **kwargs)
            self.addCleanup(dlg.close)
            self.assertTrue(dlg.plain_notice.isHidden(), kwargs)


# ================================================================== 비밀채팅: 상대 키가 바뀌면 확인을 거친다
class SecretPeerKeyTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.secret import SecretKeyring
        self.home = tempfile.mkdtemp(prefix='kcs_secret_')
        patcher = mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state, self.conn = make_demo_state()
        self.peer_dir = tempfile.mkdtemp(prefix='kcs_peer_')
        self.peer = SecretKeyring('김철수', directory=self.peer_dir)
        self.state.users['김철수']['public_key'] = self.peer.public_key_b64
        self.state.rooms['s1'] = {'room_id': 's1', 'type': 'secret', 'name': '', 'members': ['홍길동', '김철수'],
                                  'created_at': '2026-11-17T09:00:00', 'notice': None, 'last_message': None,
                                  'read_pointers': {}, 'unread_count': 0}
        self.next_id = 500

    def receive(self, keyring, text):
        self.next_id += 1
        content = keyring.encrypt('s1', self.state.keyring.public_key_b64, text)
        self.conn.push('msg_new', message={'msg_id': self.next_id, 'room_id': 's1', 'sender': '김철수', 'kind': 'secret',
                                           'content': content, 'timestamp': '2026-11-17T09:00:00'})
        return self.state._messages['s1'][self.next_id]

    def new_peer_key(self):
        from client.kakao.secret import SecretKeyring
        other = SecretKeyring('김철수', directory=tempfile.mkdtemp(prefix='kcs_peer2_'))
        self.state.users['김철수']['public_key'] = other.public_key_b64
        return other

    def test_first_key_is_remembered_and_messages_flow(self):
        self.assertIsNone(self.state.send_secret('s1', '보냄'))
        self.assertEqual(self.receive(self.peer, '받음')['secret_text'], '받음')
        self.assertFalse(self.state.secret_key_changed('s1'))

    def test_changed_key_blocks_sending_until_confirmed(self):
        self.state.send_secret('s1', '첫 메시지')
        self.new_peer_key()
        self.assertTrue(self.state.secret_key_changed('s1'))
        self.conn.sent.clear()
        self.assertIn('키', self.state.send_secret('s1', '새 키로는 아직'))
        self.assertIsNone(self.conn.last('send'))
        self.state.trust_secret_key('s1')
        self.assertFalse(self.state.secret_key_changed('s1'))
        self.assertIsNone(self.state.send_secret('s1', '확인 뒤'))
        self.assertIsNotNone(self.conn.last('send'))

    def test_message_under_an_unconfirmed_key_is_readable_after_confirming(self):
        old = self.receive(self.peer, '예전 키로 온 글')
        other = self.new_peer_key()
        new = self.receive(other, '새 키로 온 글')
        self.assertFalse(new['secret_ok'])
        self.state.trust_secret_key('s1')
        self.assertEqual(new['secret_text'], '새 키로 온 글')
        self.assertEqual(old['secret_text'], '예전 키로 온 글')
        self.assertEqual(self.receive(self.peer, '예전 키로 또')['secret_text'], '예전 키로 또')   # 확인했던 키는 계속 읽힌다

    def test_remembered_keys_survive_a_restart(self):
        from client.kakao.secret import PeerKeys
        self.state.send_secret('s1', '첫 메시지')
        again = PeerKeys('홍길동')
        self.assertEqual(again.status('김철수', self.peer.public_key_b64), 'same')
        self.assertEqual(again.status('김철수', self.new_peer_key().public_key_b64), 'changed')

    def test_check_number_is_shared_by_both_sides_and_follows_the_keys(self):
        from client.kakao.secret import fingerprint
        mine, theirs = self.state.keyring.public_key_b64, self.peer.public_key_b64
        number = fingerprint(mine, theirs)
        self.assertEqual(number, fingerprint(theirs, mine))
        self.assertRegex(number, r'^\d{5}( \d{5}){5}$')
        self.assertNotEqual(number, fingerprint(mine, self.new_peer_key().public_key_b64))
        self.assertEqual(self.state.secret_fingerprint('s1'), fingerprint(mine, self.state.users['김철수']['public_key']))

    def test_chat_window_shows_the_warning_and_confirms(self):
        from client.kakao.chat_room import ChatRoomWindow
        self.state.send_secret('s1', '첫 메시지')
        win = ChatRoomWindow(self.state, 's1')
        self.addCleanup(win.close)
        self.assertTrue(win.secret_key_button.isHidden())
        self.new_peer_key()
        self.state.users_changed.emit()
        self.assertFalse(win.secret_key_button.isHidden())
        with mock.patch.object(win, 'confirm', return_value=True) as asked:
            win.secret_key_button.click()
        self.assertIn(self.state.secret_fingerprint('s1'), asked.call_args[0][1])
        self.assertTrue(win.secret_key_button.isHidden())
        self.assertFalse(self.state.secret_key_changed('s1'))


# ================================================================== 대화 내보내기(CSV)
class CsvExportCellTests(unittest.TestCase):
    def test_every_text_cell_is_kept_from_running_as_a_formula(self):
        state, _conn = make_demo_state()
        state.users['김철수']['display_name'] = '=SUM(1)'
        message = {'msg_id': 1, 'room_id': 'r1', 'sender': '김철수', 'kind': 'file', 'content': '@x',
                   'timestamp': '2026-11-17T09:00:00', 'attachment': {'attachment_id': 5, 'name': '+f.pdf', 'size': 1}}
        row = state.export_csv('r1', [message]).splitlines()[1]
        cells = row.split(',')
        self.assertEqual(cells[2], "'=SUM(1)")
        self.assertEqual(cells[5], "'+f.pdf")


if __name__ == '__main__':
    unittest.main()
