# -*- coding: utf-8 -*-
"""전체 점검(2026-11-17) 뒤 고친 안정성 문제의 회귀 테스트 - 재연결 · 알림 · 목록 갱신 · 메모리 · 창 크기"""

import base64
import os
import socket
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QWidget

from tests.kakao_fakes import app, make_demo_state
from tests.test_kakao_hardening import png_bytes

from client.kakao import osenv


def b64(data):
    return base64.b64encode(data).decode('ascii')


def text_message(msg_id, room_id='r1', sender='김철수', content='안녕'):
    return {'msg_id': msg_id, 'room_id': room_id, 'sender': sender, 'kind': 'text', 'content': content,
            'timestamp': datetime.now().replace(microsecond=0).isoformat()}


def flush(ms=0):
    if ms:
        QTest.qWait(ms)
    QApplication.processEvents()


# ================================================================== 재연결 간격
class ReconnectBackoffTests(unittest.TestCase):
    def session(self, port=1):
        from client.kakao.app import Session
        app()
        s = Session('127.0.0.1', port, 'tester', 'x')
        self.addCleanup(s.close)
        return s

    def test_delay_keeps_growing_while_the_server_does_not_answer(self):
        s = self.session()
        delays = []
        with mock.patch.object(s, 'reconnect', return_value=(True, '')):   # 연결만 되고 welcome 은 오지 않음
            for _ in range(4):
                delays.append(s.retry_delay())
                s._auto_reconnect()
        self.assertEqual(delays, [1, 2, 4, 8])

    def test_welcome_resets_the_delay(self):
        s = self.session()
        s._retry = 3
        s.state.welcomed.emit()
        self.assertEqual(s.retry_delay(), 1)

    def test_connection_that_never_says_welcome_is_dropped_and_retried(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(2)
        self.addCleanup(server.close)
        s = self.session(server.getsockname()[1])
        s.WELCOME_TIMEOUT_MS = 60
        ok, _ = s.reconnect()
        self.assertTrue(ok)
        flush(400)
        self.assertFalse(s.conn.is_connected)
        self.assertTrue(s._retry_timer.isActive())


# ================================================================== 열려 있지만 보고 있지 않은 채팅방
class ActiveRoomTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.chat_room import ChatRoomWindow
        self.state, self.conn = make_demo_state()
        self.win = ChatRoomWindow(self.state, 'r1')
        self.addCleanup(self.win.close)
        self.win.show()
        self.win.activateWindow()
        flush()

    def test_room_in_front_is_active(self):
        self.assertIn('r1', self.state.active_rooms)

    def test_room_behind_another_window_gets_notifications_again(self):
        other = QWidget()
        self.addCleanup(other.close)
        other.show()
        other.activateWindow()
        flush()
        self.assertNotIn('r1', self.state.active_rooms)
        notes = []
        self.state.notify.connect(lambda *a: notes.append(a))
        self.conn.push('msg_new', message=text_message(9001))
        self.assertEqual(len(notes), 1)
        self.win.activateWindow()
        flush()
        self.assertIn('r1', self.state.active_rooms)

    def test_dialog_of_the_room_keeps_it_active(self):
        dlg = QDialog(self.win)
        self.addCleanup(dlg.close)
        dlg.show()
        dlg.activateWindow()
        flush()
        self.assertIn('r1', self.state.active_rooms)


# ================================================================== 끊긴 동안 연 방
class HistoryAfterReconnectTests(unittest.TestCase):
    def test_room_opened_while_offline_loads_history_once_connected(self):
        from client.kakao.chat_room import ChatRoomWindow
        state, conn = make_demo_state()
        conn.send = lambda *a, **k: None                    # 끊긴 동안: 요청이 나가지 않는다
        win = ChatRoomWindow(state, 'r3')
        self.addCleanup(win.close)
        win.show()
        flush()
        del conn.send                                       # 다시 연결됨
        state.welcomed.emit()
        self.assertEqual(conn.last('history'), {'room_id': 'r3', 'limit': 50})

    def test_room_that_already_loaded_does_not_ask_again(self):
        from client.kakao.chat_room import ChatRoomWindow
        state, conn = make_demo_state()
        win = ChatRoomWindow(state, 'r3')
        self.addCleanup(win.close)
        win.show()
        flush()
        conn.sent.clear()
        state.welcomed.emit()
        self.assertIsNone(conn.last('history'))


# ================================================================== 대화 내보내기 조각
class ExportPartsTests(unittest.TestCase):
    def setUp(self):
        self.state, self.conn = make_demo_state()
        self.got = []
        self.state.room_export_ready.connect(lambda rid, msgs, _cut: self.got.append([m['msg_id'] for m in msgs]))

    def test_new_export_starts_clean_after_an_interrupted_one(self):
        self.conn.push('room_export', room_id='r1', messages=[text_message(1)], done=False)
        self.state.request_export('r1')
        self.conn.push('room_export', room_id='r1', messages=[text_message(2)], done=True)
        self.assertEqual(self.got, [[2]])

    def test_lost_connection_drops_unfinished_parts(self):
        self.conn.push('room_export', room_id='r1', messages=[text_message(1)], done=False)
        self.conn.disconnected.emit('끊김')
        self.assertEqual(self.state._export_parts, {})


class ExportTimeoutWatchTests(unittest.TestCase):
    """대화 저장의 시간 초과 감시는 창 하나에 타이머 하나 - 저장이 끝나거나 창을 닫으면 멈춘다"""

    def setUp(self):
        from client.kakao.chat_room import ChatRoomWindow
        self.state, self.conn = make_demo_state()
        self.win = ChatRoomWindow(self.state, 'r1')
        self.addCleanup(self.win.close)
        self.path = os.path.join(tempfile.mkdtemp(prefix='kcs_export_'), 'a.txt')

    def test_watch_runs_while_waiting_and_stops_when_the_export_arrives(self):
        self.win.export_chat(self.path)
        self.assertTrue(self.win._export_timer.isActive())
        self.conn.push('room_export', room_id='r1', messages=[text_message(1)], done=True)
        self.assertFalse(self.win._export_timer.isActive())
        self.assertTrue(os.path.exists(self.path))

    def test_watch_stops_when_the_window_closes(self):
        self.win.export_chat(self.path)
        self.win.close()
        self.assertFalse(self.win._export_timer.isActive())

    def test_no_answer_releases_the_saving_state(self):
        self.win.EXPORT_TIMEOUT_MS = 20
        self.win.export_chat(self.path)
        flush(150)
        self.assertFalse(self.win._export_requested)


class AttachmentCollectorTimeoutTests(unittest.TestCase):
    def test_file_that_never_arrives_is_skipped(self):
        from client.kakao.chat_export import AttachmentCollector
        state, _conn = make_demo_state()
        message = dict(text_message(5), kind='file', attachment={'attachment_id': 77, 'name': 'a.pdf', 'size': 3})
        collector = AttachmentCollector(state, [message], timeout_ms=20)
        done = []
        collector.finished.connect(lambda files, notes: done.append((files, notes)))
        collector.start()
        flush(200)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][0], {})
        self.assertIn(5, done[0][1])


# ================================================================== 목록 갱신 횟수
class TabRefreshTests(unittest.TestCase):
    def counted(self, cls):
        original = cls.refresh
        counts = self.counts

        def refresh(tab, *args, **kwargs):
            counts[cls.__name__] = counts.get(cls.__name__, 0) + 1
            return original(tab, *args, **kwargs)
        cls.refresh = refresh
        self.addCleanup(setattr, cls, 'refresh', original)

    def setUp(self):
        from client.kakao.friends_tab import FriendsTab
        from client.kakao.org_tab import OrgTab
        from client.kakao.main_window import MainWindow
        self.counts = {}
        self.counted(FriendsTab)
        self.counted(OrgTab)
        self.state, self.conn = make_demo_state()
        self.win = MainWindow(self.state, self.state.settings)
        self.addCleanup(self.win.close)
        self.win.show()
        flush(400)
        self.org_tabs = len(self.win.findChildren(OrgTab))
        self.friends = self.win.findChildren(FriendsTab)[0]
        self.counts.clear()

    def test_one_presence_change_rebuilds_each_list_once(self):
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.assertEqual(self.counts, {'FriendsTab': 1})       # 보이는 친구 탭만 (다른 탭 뒤의 조직도는 보일 때 그린다)
        self.assertIn('이영희', self.friends.sections['online'])

    def test_burst_of_presence_changes_is_merged(self):
        for i in range(6):
            self.conn.push('user_status_changed', username='이영희', status='offline' if i % 2 else 'online')
        self.assertEqual(self.counts['FriendsTab'], 1)                    # 첫 변경은 바로
        flush(500)
        self.assertEqual(self.counts['FriendsTab'], 2)                    # 나머지 다섯은 한 번으로
        self.assertIn('이영희', self.friends.sections['offline'])         # 마지막 상태가 보인다


# ================================================================== 사진 · 첨부 메모리
class ImageThumbTests(unittest.TestCase):
    def test_resizing_a_wide_window_does_not_decode_the_photo_again(self):
        from client.kakao import chat_room
        app()
        thumb = chat_room.ImageThumb()
        with mock.patch.object(chat_room, 'decode_pixmap', wraps=chat_room.decode_pixmap) as decode:
            thumb.set_image(png_bytes(600, 400), 500)
            thumb.relayout(600)
            thumb.relayout(900)
            self.assertEqual(decode.call_count, 1)
            thumb.relayout(120)                                            # 좁아져 썸네일 크기가 달라질 때만 다시
            self.assertEqual(decode.call_count, 2)


class AttachmentCacheTests(unittest.TestCase):
    def setUp(self):
        self.state, self.conn = make_demo_state()
        self.state.attachments.limit = 100

    def receive(self, aid, size=40):
        self.conn.push('attachment_data', attachment_id=aid, name='a.bin', mime='x', data_b64=b64(b'x' * size))

    def test_oldest_is_dropped_when_over_the_limit(self):
        for aid in (1, 2, 3):
            self.receive(aid)
        self.assertEqual(list(self.state.attachments), [2, 3])

    def test_recently_used_item_is_kept(self):
        self.receive(1)
        self.receive(2)
        self.assertIsNotNone(self.state.attachments.get(1))
        self.receive(3)
        self.assertEqual(sorted(self.state.attachments), [1, 3])

    def test_item_bigger_than_the_limit_is_still_usable_once(self):
        self.receive(1, 300)
        self.assertEqual(len(self.state.attachments.get(1)[0]), 300)


# ================================================================== 통화 중 연결 끊김
class CallDisconnectTests(unittest.TestCase):
    def test_call_window_ends_when_the_connection_is_lost(self):
        from client.kakao import call as callmod
        from tests.test_kakao_call import call_payload
        state, conn = make_demo_state()
        callmod._WINDOWS.clear()
        conn.push('call_updated', call=call_payload(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}}))
        window = callmod.show_call(state, 1)
        self.addCleanup(callmod._WINDOWS.clear)
        self.addCleanup(window.close)
        self.assertFalse(window.ended)
        conn.disconnected.emit('끊김')
        self.assertTrue(window.ended)


# ================================================================== 작은 화면의 프로필 편집
class ProfileEditSizeTests(unittest.TestCase):
    def test_dialog_fits_the_screen_and_keeps_buttons_visible(self):
        from client.kakao import main_window
        from client.kakao.friends_tab import ProfileEditDialog
        state, _ = make_demo_state()
        main_window.apply_app_theme(state.settings)
        dlg = ProfileEditDialog(state)
        self.addCleanup(dlg.close)
        dlg.show()
        flush()
        screen = app().primaryScreen().availableGeometry()
        self.assertLessEqual(dlg.frameGeometry().height(), screen.height())
        self.assertTrue(dlg.ok_button.isVisible())
        self.assertTrue(dlg.rect().contains(dlg.ok_button.geometry().translated(dlg.ok_button.parentWidget().mapTo(
            dlg, dlg.ok_button.parentWidget().rect().topLeft()))))


# ================================================================== 잠금 중에는 다른 창도 보이지 않는다
class LockHidesOtherWindowsTests(unittest.TestCase):
    def test_lock_hides_other_windows_and_unlock_brings_back_plain_ones(self):
        from client.kakao import lock
        from client.kakao.main_window import MainWindow
        state, _ = make_demo_state()
        lock.set_pin(state.settings, '1234')
        win = MainWindow(state, state.settings)
        self.addCleanup(win.close)
        win.unlock()
        win.show()
        extra = QWidget()
        self.addCleanup(extra.close)
        extra.show()
        modal = QDialog(win)
        modal.setModal(True)
        self.addCleanup(modal.close)
        modal.show()
        flush()
        self.assertTrue(win.lock())
        self.assertTrue(extra.isHidden() and modal.isHidden())
        win.unlock()
        self.assertFalse(extra.isHidden())
        self.assertTrue(modal.isHidden())


# ================================================================== 붙여 넣은 사진
class PastedImageTests(unittest.TestCase):
    def test_pasted_image_is_written_inside_the_private_folder(self):
        from client.kakao.chat_room import ChatRoomWindow
        home = tempfile.mkdtemp(prefix='kcs_paste_')
        patcher = mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': home})
        patcher.start()
        self.addCleanup(patcher.stop)
        osenv.forget_opened_files_dir()
        self.addCleanup(osenv.forget_opened_files_dir)
        state, _ = make_demo_state()
        win = ChatRoomWindow(state, 'r1')
        self.addCleanup(win.close)
        paths = []
        image = QImage(4, 4, QImage.Format_RGB32)
        image.fill(Qt.blue)
        with mock.patch.object(win, 'send_file', side_effect=lambda path: paths.append(path)):
            win.send_image(image)
        self.assertTrue(paths[0].startswith(home + os.sep), paths[0])
        self.assertFalse(os.path.exists(paths[0]))                          # 보낸 뒤에는 지운다



# ================================================================== 같은 PC 에서 두 번 실행
class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        app()
        self.folder = tempfile.mkdtemp(prefix='kcs_single_')

    def make(self):
        from client.kakao.single import SingleInstance
        instance = SingleInstance(folder=self.folder)
        self.addCleanup(instance.release)
        return instance

    def test_second_start_is_refused_and_wakes_the_first(self):
        first, second = self.make(), self.make()
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        woke = []
        first.activated.connect(lambda: woke.append(1))
        self.assertTrue(second.notify())
        flush(200)
        self.assertEqual(woke, [1])

    def test_next_start_works_after_the_first_exits(self):
        first, second = self.make(), self.make()
        self.assertTrue(first.acquire())
        first.release()
        self.assertTrue(second.acquire())

    def test_only_a_plain_start_is_limited_to_one(self):
        from client.kakao.app import parse_args, single_instance_wanted
        self.assertTrue(single_instance_wanted(parse_args([])))
        self.assertTrue(single_instance_wanted(parse_args(['--host', '10.0.0.1', '--port', '5555'])))
        for argv in (['--user', '홍길동'], ['--no-auto-login'], ['--new-instance'], ['--self-check']):
            self.assertFalse(single_instance_wanted(parse_args(argv)), argv)


# ================================================================== 다시 연결된 뒤 대화 받기
class ReconnectHistoryOrderTests(unittest.TestCase):
    def setUp(self):
        self.state, self.conn = make_demo_state()
        self.state.REFILL_INTERVAL_MS = 20

    def reconnect(self):
        self.conn.sent.clear()
        self.conn.push('welcome', me=self.state.users[self.state.me], users=list(self.state.users.values()),
                       rooms=list(self.state.rooms.values()))

    def asked(self):
        return [p['room_id'] for kind, p in self.conn.sent if kind == 'history']

    def test_open_room_is_asked_first_and_the_rest_one_by_one(self):
        self.state.open_rooms.add('r1')
        self.reconnect()
        self.assertEqual(self.asked(), ['r1'])
        flush(300)
        self.assertEqual(sorted(self.asked()), ['r1', 'r2'])

    def test_waiting_rooms_are_forgotten_when_the_connection_drops_again(self):
        self.reconnect()
        self.conn.disconnected.emit('끊김')
        before = self.asked()
        flush(300)
        self.assertEqual(self.asked(), before)

    def test_chat_window_marks_its_room_as_open(self):
        from client.kakao.chat_room import ChatRoomWindow
        win = ChatRoomWindow(self.state, 'r1')
        win.show()
        flush()
        self.assertIn('r1', self.state.open_rooms)
        win.close()
        self.assertNotIn('r1', self.state.open_rooms)



# ================================================================== 접속 · 전송은 화면을 붙잡지 않는다
class ResponsiveConnectionTests(unittest.TestCase):
    def setUp(self):
        app()

    def test_screen_keeps_running_while_the_server_does_not_answer(self):
        import time
        from PyQt5.QtCore import QTimer
        from client.kakao import net
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.addCleanup(timer.stop)

        def slow(*_args, **_kwargs):
            time.sleep(0.4)
            raise OSError('응답 없음')
        with mock.patch.object(net, 'open_socket', side_effect=slow):
            sock, error = net.open_socket_responsive('10.255.255.1', 5555)
        self.assertIsNone(sock)
        self.assertIn('응답 없음', error)
        self.assertGreater(len(ticks), 5)            # 기다리는 동안에도 화면 쪽 타이머가 돌았다

    def test_socket_is_returned_when_the_server_accepts(self):
        from client.kakao import net
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        self.addCleanup(server.close)
        sock, error = net.open_socket_responsive('127.0.0.1', server.getsockname()[1])
        self.assertEqual(error, '')
        self.assertIsNotNone(sock)
        sock.close()

    def pair(self):
        from client.kakao import net
        ours, theirs = socket.socketpair()
        self.addCleanup(theirs.close)
        conn = net.ChatConnection()
        self.lost = []
        conn.disconnected.connect(self.lost.append, Qt.DirectConnection)
        conn.attach(ours, '홍길동', password='pw')
        return conn, theirs

    def read_lines(self, sock, count, seconds=3.0):
        import json
        import time
        sock.settimeout(0.2)
        buf, end = b'', time.time() + seconds
        while buf.count(b'\n') < count and time.time() < end:
            try:
                buf += sock.recv(65536)
            except socket.timeout:
                pass
        return [json.loads(line) for line in buf.split(b'\n') if line]

    def test_requests_go_out_in_the_order_they_were_sent(self):
        conn, theirs = self.pair()
        for i in range(30):
            conn.send('send', content=str(i))
        lines = self.read_lines(theirs, 31)
        conn.close()
        self.assertEqual(lines[0]['type'], 'join')
        self.assertEqual([m['content'] for m in lines[1:31]], [str(i) for i in range(30)])

    def test_sending_does_not_wait_for_a_peer_that_stopped_reading(self):
        import time
        conn, _theirs = self.pair()
        start = time.time()
        for _ in range(100):
            self.assertIsNotNone(conn.send('send', content='x' * 200000))     # 20MB, 상대는 읽지 않는다
        self.assertLess(time.time() - start, 2.0)
        start = time.time()
        conn.close()                                                           # 막힌 전송을 끝없이 기다리지 않는다
        self.assertLess(time.time() - start, 3.0)

    def test_broken_connection_is_reported_after_a_failed_send(self):
        import time
        conn, theirs = self.pair()
        theirs.close()
        for _ in range(20):
            conn.send('send', content='x' * 100000)
        end = time.time() + 3.0
        while not self.lost and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(self.lost)
        self.assertFalse(conn.is_connected)
        self.assertIsNone(conn.send('send', content='끊긴 뒤'))



# ================================================================== 메모리 정리(GC)는 화면 스레드의 이벤트 사이에서만
class SafeGarbageCollectionTests(unittest.TestCase):
    def test_automatic_collection_is_off_and_the_guard_collects_instead(self):
        import gc
        import weakref
        from client.kakao import widgets
        widgets.install_safe_gc(app())
        guard = app()._safe_gc
        widgets.install_safe_gc(app())
        self.assertIs(app()._safe_gc, guard)                    # 다시 불러도 하나
        self.assertFalse(gc.isenabled())

        class Node:
            pass
        a, b = Node(), Node()
        a.other, b.other = b, a                                 # 서로 물고 있는 쓰레기
        probe = weakref.ref(a)
        del a, b
        self.assertIsNotNone(probe())                           # 자동으로는 지워지지 않는다
        guard.collect(force=True)
        self.assertIsNone(probe())

    def test_guard_runs_on_a_timer(self):
        from client.kakao import widgets
        widgets.install_safe_gc(app())
        self.assertTrue(app()._safe_gc.timer.isActive())

    def cyclic_garbage(self):
        """서로 물고 있는 쓰레기 한 쌍 → 살아 있는지 보는 약한 참조"""
        import weakref

        class Node:
            pass
        a, b = Node(), Node()
        a.other, b.other = b, a
        return weakref.ref(a)

    def test_dialog_exec_inside_the_app_loop_counts_as_nested(self):
        # 실제 앱처럼 app.exec_() 안에서 대화상자 exec_() 를 띄워 본다. 별도 프로세스: 앞선 테스트가 quit() 를 불렀으면
        # 이 프로세스의 이벤트 루프는 바로 끝나 버린다
        import json
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import json\n"
                "from PyQt5.QtCore import QTimer\n"
                "from PyQt5.QtWidgets import QApplication, QDialog\n"
                "from client.kakao.widgets import install_safe_gc\n"
                "app = QApplication([])\n"
                "install_safe_gc(app)\n"
                "guard, dlg, seen = app._safe_gc, QDialog(), {}\n"
                "seen['before'] = guard.nested()\n"
                "def in_dialog():\n"
                "    seen['dialog'] = guard.nested()\n"
                "    dlg.reject()\n"
                "def in_app():\n"
                "    seen['app'] = guard.nested()\n"
                "    QTimer.singleShot(0, in_dialog)\n"
                "    dlg.exec_()\n"
                "    seen['after'] = guard.nested()\n"
                "    app.quit()\n"
                "QTimer.singleShot(0, in_app)\n"
                "QTimer.singleShot(5000, lambda: app.exit(7))\n"
                "code = app.exec_()\n"
                "print(json.dumps(seen))\n"
                "raise SystemExit(code)\n")
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        done = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]),
                         {'before': False, 'app': False, 'dialog': True, 'after': False})

    def test_collection_waits_while_a_nested_event_loop_runs(self):
        from client.kakao import widgets
        widgets.install_safe_gc(app())
        guard = app()._safe_gc
        probe = self.cyclic_garbage()
        with mock.patch.object(guard, 'thresholds', (1, 10 ** 9, 10 ** 9)), \
                mock.patch.object(guard, 'NESTED_BACKLOG', 10 ** 9):
            with mock.patch.object(guard, 'nested', return_value=True):
                guard.collect()
                self.assertIsNotNone(probe())                   # 바깥 이벤트 처리가 끝나지 않았다 - 미룬다
            with mock.patch.object(guard, 'nested', return_value=False):
                guard.collect()
                self.assertIsNone(probe())                      # 루프가 끝난 뒤 정리

    def test_long_nested_loop_still_collects_once_garbage_piles_up(self):
        from client.kakao import widgets
        widgets.install_safe_gc(app())
        guard = app()._safe_gc
        probe = self.cyclic_garbage()
        with mock.patch.object(guard, 'thresholds', (1, 10 ** 9, 10 ** 9)), \
                mock.patch.object(guard, 'NESTED_BACKLOG', 0), \
                mock.patch.object(guard, 'nested', return_value=True):
            guard.collect()                                     # 알림 상자가 며칠 떠 있어도 메모리가 끝없이 늘지 않게
        self.assertIsNone(probe())

    def test_pumping_events_by_hand_counts_as_nested(self):
        from PyQt5.QtCore import QTimer
        from client.kakao import widgets
        widgets.install_safe_gc(app())
        guard = app()._safe_gc
        seen = []
        QTimer.singleShot(0, lambda: seen.append(guard.nested()))
        for _ in range(20):                                     # 단추 처리 안에서 화면을 한 번 그리게 하는 경우처럼
            widgets.pump_events()
            if seen:
                break
        self.assertEqual(seen, [True])
        self.assertFalse(guard.nested())                        # 끝나면 다시 정리할 수 있다

    def test_client_code_pumps_events_only_through_the_guard(self):
        """client/kakao 는 processEvents 를 직접 부르지 않고 widgets.pump_events 를 쓴다 (그동안 메모리 정리가 미뤄진다)"""
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'client', 'kakao')
        direct = []
        for folder, _dirs, names in os.walk(root):
            for name in sorted(names):
                path = os.path.join(folder, name)
                if not name.endswith('.py') or os.path.relpath(path, root) == 'widgets.py':
                    continue
                with open(path, encoding='utf-8') as f:
                    direct += [f'{os.path.relpath(path, root)}:{no}' for no, line in enumerate(f, 1)
                               if 'processEvents(' in line and not line.lstrip().startswith('#')]
        self.assertEqual(direct, [])



# ================================================================== 목록은 달라진 행만 다시 그린다
def counted(testcase, module, name):
    """module.name 행 위젯이 몇 번 만들어지는지 센다 → 만들어진 인자 목록"""
    original = getattr(module, name)
    made = []

    class Counted(original):
        def __init__(self, *args, **kwargs):
            made.append(args)
            super().__init__(*args, **kwargs)
    setattr(module, name, Counted)
    testcase.addCleanup(setattr, module, name, original)
    return made


def row_data(row_list):
    return [row_list.item(i).data(Qt.UserRole) for i in range(row_list.count())]


def row_widgets(row_list):
    return {repr(row_list.item(i).data(Qt.UserRole)): row_list.itemWidget(row_list.item(i)) for i in range(row_list.count())}


class RowListSyncTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.chats_tab import RowList
        app()
        self.list = RowList()
        self.addCleanup(self.list.close)
        self.made = []

    def rows(self, *specs):
        from PyQt5.QtWidgets import QLabel

        def factory(key):
            def make():
                self.made.append(key)
                return QLabel(str(key))
            return make
        return [(key, sig, factory(key), True) for key, sig in specs]

    def test_only_changed_rows_are_built(self):
        self.assertEqual(self.list.sync_rows(self.rows(('a', 1), ('b', 1), ('c', 1))), 3)
        before = row_widgets(self.list)
        self.made.clear()
        self.assertEqual(self.list.sync_rows(self.rows(('a', 1), ('b', 2), ('c', 1))), 1)
        self.assertEqual(self.made, ['b'])
        after = row_widgets(self.list)
        self.assertIs(after["'a'"], before["'a'"])
        self.assertIs(after["'c'"], before["'c'"])
        self.assertIsNot(after["'b'"], before["'b'"])

    def test_rows_are_added_removed_and_moved_without_rebuilding_the_rest(self):
        self.list.sync_rows(self.rows(('a', 1), ('b', 1), ('c', 1), ('d', 1)))
        self.list.setCurrentRow(1)                               # b 선택
        before = row_widgets(self.list)
        self.made.clear()
        self.list.sync_rows(self.rows(('c', 1), ('a', 1), ('b', 1), ('e', 1)))     # c 가 맨 위로, d 삭제, e 추가
        self.assertEqual(row_data(self.list), ['c', 'a', 'b', 'e'])
        self.assertEqual(sorted(self.made), ['c', 'e'])
        after = row_widgets(self.list)
        self.assertIs(after["'a'"], before["'a'"])
        self.assertIs(after["'b'"], before["'b'"])
        self.assertEqual(self.list.currentItem().data(Qt.UserRole), 'b')            # 선택은 그대로

    def test_moved_row_stays_selected(self):
        self.list.sync_rows(self.rows(('a', 1), ('b', 1), ('c', 1)))
        self.list.setCurrentRow(2)
        self.list.sync_rows(self.rows(('c', 1), ('a', 1), ('b', 1)))
        self.assertEqual(self.list.currentItem().data(Qt.UserRole), 'c')

    def test_empty_and_repeated_sync(self):
        self.list.sync_rows(self.rows(('a', 1)))
        self.assertEqual(self.list.sync_rows([]), 0)
        self.assertEqual(self.list.count(), 0)


class FriendsRowUpdateTests(unittest.TestCase):
    def setUp(self):
        from client.kakao import friends_tab
        self.module = friends_tab
        self.state, self.conn = make_demo_state()
        self.made = counted(self, friends_tab, 'FriendRow')
        self.tab = friends_tab.FriendsTab(self.state, self.state.settings)
        self.addCleanup(self.tab.close)
        self.made.clear()

    def test_presence_change_builds_only_that_persons_rows(self):
        before = row_widgets(self.tab.list)
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.assertEqual([args[1] for args in self.made], ['이영희'])
        after = row_widgets(self.tab.list)
        for key in ("('friend', '김철수', 'online')", "('friend', '박민수', 'online')", "('me', '홍길동')"):
            self.assertIs(after[key], before[key], key)                    # 다른 사람의 행은 그대로
        self.assertIn(('friend', '이영희', 'online'), row_data(self.tab.list))
        self.assertNotIn(('friend', '이영희', 'offline'), row_data(self.tab.list))

    def test_result_is_the_same_as_a_full_rebuild(self):
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.conn.push('user_updated', user=dict(self.state.user('박민수'), status_message='외근'))
        self.tab._users_relay.flush()
        fresh = self.module.FriendsTab(self.state, self.state.settings)
        self.addCleanup(fresh.close)
        self.assertEqual(row_data(self.tab.list), row_data(fresh.list))

    def test_selection_survives_someone_elses_change(self):
        item = self.tab.list.find_data(('friend', '김철수', 'online'))
        self.tab.list.setCurrentItem(item)
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.assertEqual(self.tab.list.currentItem().data(Qt.UserRole), ('friend', '김철수', 'online'))

    def test_theme_change_rebuilds_every_row(self):
        friend_rows = sum(1 for data in row_data(self.tab.list) if data[0] == 'friend')
        self.tab.apply_theme()
        self.assertEqual(len(self.made), friend_rows)


class ChatsRowUpdateTests(unittest.TestCase):
    def setUp(self):
        from client.kakao import chats_tab
        self.state, self.conn = make_demo_state()
        self.made = counted(self, chats_tab, 'RoomRow')
        self.tab = chats_tab.ChatsTab(self.state, self.state.settings)
        self.addCleanup(self.tab.close)
        self.made.clear()
        self.order = row_data(self.tab.list)

    def new_message(self, room_id, msg_id):
        self.made.clear()
        self.conn.push('msg_new', message=text_message(msg_id, room_id=room_id, sender='김철수' if room_id == 'r1' else '박민수'))

    def test_new_message_moves_only_that_room(self):
        last = self.order[-1] if self.order[-1] != 'r3' else self.order[-2]       # 맨 위가 아닌 대화방
        other = next(r for r in self.order if r != last)
        self.tab.list.setCurrentItem(self.tab.list.find_data(other))
        before = row_widgets(self.tab.list)
        self.new_message(last, 9101)
        self.assertEqual(row_data(self.tab.list)[0], last)
        self.assertEqual([args[1] for args in self.made], [last])
        self.assertIs(row_widgets(self.tab.list)[repr(other)], before[repr(other)])
        self.assertEqual(self.tab.list.currentItem().data(Qt.UserRole), other)

    def test_new_message_in_the_top_room_rebuilds_one_row(self):
        top = self.order[0]
        self.new_message(top, 9102)
        self.assertEqual(row_data(self.tab.list), self.order)
        self.assertEqual([args[1] for args in self.made], [top])

    def test_unrelated_presence_change_builds_nothing(self):
        self.made.clear()
        self.conn.push('user_status_changed', username='최지우', status='online')
        self.assertEqual(self.made, [])

    def test_pin_and_search_still_work(self):
        self.state.toggle_pin('r3') if hasattr(self.state, 'toggle_pin') else self.state.settings.toggle_in('pinned_rooms', 'r3')
        self.tab.refresh()
        self.assertEqual(row_data(self.tab.list)[0], 'r3')
        self.tab.search_edit.setText('프로젝트')
        self.assertEqual(row_data(self.tab.list), ['r1'])
        self.tab.search_edit.setText('')
        self.assertEqual(sorted(row_data(self.tab.list)), ['r1', 'r2', 'r3'])


class OrgRowUpdateTests(unittest.TestCase):
    def setUp(self):
        from client.kakao import org_tab
        from tests.kakao_fakes import expand_org
        self.state, self.conn = make_demo_state()
        self.made = counted(self, org_tab, 'MemberRow') 
        self.made_compact = counted(self, org_tab, 'CompactMemberRow')
        self.tab = org_tab.OrgTab(self.state, self.state.settings)
        self.addCleanup(self.tab.close)
        expand_org(self.tab)
        self.made.clear()
        self.made_compact.clear()

    def member_rows(self, name):
        return [i for i in range(self.tab.list.count()) if self.tab.list.item(i).data(Qt.UserRole) == ('member', name)]

    def test_presence_change_rebuilds_only_that_members_rows(self):
        rows = self.member_rows('이영희')
        self.assertTrue(rows)
        others = {i: self.tab.list.itemWidget(self.tab.list.item(i)) for i in range(self.tab.list.count()) if i not in rows}
        count = self.tab.list.count()
        self.conn.push('user_status_changed', username='이영희', status='online')
        built = [args[1] for args in self.made + self.made_compact]
        self.assertEqual(built, ['이영희'] * len(rows))
        self.assertEqual(self.tab.list.count(), count)
        for i, widget in others.items():
            self.assertIs(self.tab.list.itemWidget(self.tab.list.item(i)), widget)

    def test_other_changes_still_rebuild_the_list(self):
        self.conn.push('user_updated', user=dict(self.state.user('이영희'), department='새 부서'))
        self.assertGreater(len(self.made) + len(self.made_compact), 1)


class HiddenTabTests(unittest.TestCase):
    def setUp(self):
        from client.kakao.main_window import MainWindow
        self.state, self.conn = make_demo_state()
        self.win = MainWindow(self.state, self.state.settings)
        self.addCleanup(self.win.close)
        self.win.show()
        flush(300)

    def test_tab_behind_another_waits_until_it_is_shown(self):
        self.win.set_tab('friends')
        flush()
        chats = self.win.tabs['chats']
        before = row_data(chats.list)
        top = before[0]
        last = next(r for r in reversed(before) if r != 'r3' and r != top)
        self.conn.push('msg_new', message=text_message(9201, room_id=last, sender='김철수' if last == 'r1' else '박민수'))
        self.assertEqual(row_data(chats.list), before)                     # 숨은 탭은 그대로
        self.win.set_tab('chats')
        flush()
        self.assertEqual(row_data(chats.list)[0], last)                    # 보이는 순간 반영

    def test_hidden_org_chart_redraws_only_changed_members_when_shown(self):
        from client.kakao import org_tab
        from tests.kakao_fakes import expand_org
        org = self.win.tabs['org']
        self.win.set_tab('org')
        flush()
        expand_org(org)
        self.win.set_tab('friends')
        flush()
        made_a, made_b = counted(self, org_tab, 'MemberRow'), counted(self, org_tab, 'CompactMemberRow')
        rows = sum(1 for data in row_data(org.list) if data == ('member', '이영희'))
        self.assertTrue(rows)
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.assertEqual(made_a + made_b, [])                              # 가려진 동안에는 그리지 않는다
        self.win.set_tab('org')
        flush()
        self.assertEqual([args[1] for args in made_a + made_b], ['이영희'] * rows)   # 보일 때 그 사람 행만

    def test_tab_that_is_not_inside_a_window_updates_right_away(self):
        from client.kakao.friends_tab import FriendsTab
        tab = FriendsTab(self.state, self.state.settings)                  # 어디에도 넣지 않은 목록 (화면에 없음)
        self.addCleanup(tab.close)
        self.conn.push('user_status_changed', username='이영희', status='online')
        self.assertIn('이영희', tab.sections['online'])



# ================================================================== 테스트 환경: Windows 화면 없는 실행
class MessageBoxWithoutScreenTests(unittest.TestCase):
    def test_message_box_skips_the_native_system_menu_step(self):
        from PyQt5.QtWidgets import QMessageBox
        from tests import kakao_fakes
        app()
        restore = kakao_fakes.skip_message_box_system_menu()
        self.addCleanup(restore)
        calls = []
        patched = QMessageBox.showEvent
        QMessageBox.showEvent = lambda box, event: (calls.append(1), patched(box, event))[1]
        box = QMessageBox(QMessageBox.Warning, '정보 수정', '이미 다른 구성원이 쓰는 사번입니다.', QMessageBox.Ok)
        self.addCleanup(box.close)
        box.open()
        flush()
        self.assertEqual(calls, [1])                      # Qt 가 QMessageBox 의 것이 아니라 바꿔 둔 단계를 불렀다
        self.assertTrue(box.isVisible())
        self.assertEqual(box.text(), '이미 다른 구성원이 쓰는 사번입니다.')

    def test_restore_puts_the_original_step_back(self):
        from PyQt5.QtWidgets import QMessageBox
        from tests import kakao_fakes
        original = QMessageBox.__dict__.get('showEvent')
        kakao_fakes.skip_message_box_system_menu()()
        self.assertIs(QMessageBox.__dict__.get('showEvent'), original)


if __name__ == '__main__':
    unittest.main()
