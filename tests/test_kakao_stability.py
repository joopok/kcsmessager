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
        self.assertEqual(self.counts, {'FriendsTab': 1, 'OrgTab': self.org_tabs})
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


if __name__ == '__main__':
    unittest.main()
