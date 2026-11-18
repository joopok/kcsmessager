# -*- coding: utf-8 -*-
"""macOS 보정 (client/kakao/macos.py): Dock 이름 'KCS Talk'(번들에서 다시 실행) · 로그인 창 제목 표시줄을 본문과 한 배경으로"""

import os
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from client.kakao import macos


class DockNameTests(unittest.TestCase):
    def test_default_dock_name_is_product(self):
        self.assertEqual(macos.DOCK_NAME, 'KCS Talk')

    def test_noop_off_macos_and_in_frozen_app(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            self.assertFalse(macos.set_dock_name())
        with mock.patch.object(sys, 'frozen', True, create=True):
            self.assertFalse(macos.set_dock_name())          # 빌드한 .app 은 Info.plist 이름

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_launch_services_name_changes(self):
        # offscreen 플랫폼은 LaunchServices 에 앱으로 등록되지 않으므로 cocoa 로 자식 프로세스를 띄워 확인한다
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os, subprocess, sys\n"
                "from PyQt5.QtWidgets import QApplication\n"
                "from client.kakao.macos import set_dock_name\n"
                "app = QApplication([])\n"
                "print('ok' if set_dock_name() else 'fail')\n"
                "print(subprocess.run(['lsappinfo', 'info', '-only', 'name', str(os.getpid())],"
                " capture_output=True, text=True).stdout)\n")
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa')
        out = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                             text=True, timeout=30).stdout
        if not out.startswith('ok'):
            self.skipTest(f'GUI 세션이 없어 LaunchServices 를 쓸 수 없음: {out.strip()[:80]}')
        self.assertIn('"KCS Talk"', out)


class AppBundleTests(unittest.TestCase):
    def test_noop_off_macos_frozen_and_offscreen(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            self.assertFalse(macos.run_in_app_bundle('KCS Talk', 'kr.co.kcs.talk', '/tmp/x'))
        with mock.patch.object(sys, 'frozen', True, create=True):
            self.assertFalse(macos.run_in_app_bundle('KCS Talk', 'kr.co.kcs.talk', '/tmp/x'))
        with mock.patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
            self.assertFalse(macos.run_in_app_bundle('KCS Talk', 'kr.co.kcs.talk', '/tmp/x'))   # 테스트는 그대로

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_dock_shows_bundle_name_not_python(self):
        # 요즘 Dock 은 실행 파일이 든 번들 이름을 보인다: KCS Talk.app 안에서 다시 실행되어 번들 경로 · 이름이 바뀐다
        import tempfile
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os, subprocess, sys\n"
                "from client.kakao.macos import run_in_app_bundle\n"
                "run_in_app_bundle('KCS Talk', 'kr.co.kcs.talk.test', sys.argv[1])\n"
                "from PyQt5.QtWidgets import QApplication\n"
                "app = QApplication([])\n"
                "print(sys.argv[1:], subprocess.run(['lsappinfo', 'info', str(os.getpid())],"
                " capture_output=True, text=True).stdout)\n")
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, QT_QPA_PLATFORM='cocoa')
            env.pop('KCS_NO_APP_BUNDLE', None)
            out = subprocess.run([sys.executable, '-c', code, home], cwd=root, env=env, capture_output=True,
                                 text=True, timeout=30).stdout
            if 'bundle path' not in out:
                self.skipTest(f'GUI 세션이 없어 LaunchServices 를 쓸 수 없음: {out.strip()[:80]}')
            self.assertIn(repr([home]), out)                                   # 인자는 그대로 넘어간다
            self.assertIn('"KCS Talk"', out)
            self.assertIn('kr.co.kcs.talk.test/KCS Talk.app"', out)
            self.assertNotIn('Python.app"', out.split('bundle path')[1].splitlines()[0])
            again = subprocess.run([sys.executable, '-c', code, home], cwd=root, env=env, capture_output=True,
                                   text=True, timeout=30).stdout
            self.assertIn('KCS Talk.app"', again)                              # 이미 있는 번들을 그대로 쓴다


class UnifiedTitleBarTests(unittest.TestCase):
    def test_offscreen_login_dialog_keeps_layout(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])   # 참조를 잡아 둬야 지워지지 않는다
        from client.kakao.login import LoginDialog
        dlg = LoginDialog(fresh=True)
        dlg.show()
        self.assertFalse(macos.unify_title_bar(dlg))                  # macOS 창(cocoa)이 아니면 그대로
        self.assertEqual(dlg.layout().contentsMargins().top(), 48)
        dlg.close()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_cocoa_login_dialog_title_bar_is_transparent(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import ctypes, sys\n"
                "from PyQt5.QtWidgets import QApplication\n"
                "app = QApplication([])\n"
                "from client.kakao.login import LoginDialog\n"
                "from client.kakao import macos\n"
                "d = LoginDialog(fresh=True); d.show(); app.processEvents()\n"
                "objc = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.dylib')\n"
                "objc.sel_registerName.restype = ctypes.c_void_p\n"
                "send = objc.objc_msgSend\n"
                "send.restype = ctypes.c_void_p; send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]\n"
                "w = send(int(d.winId()), objc.sel_registerName(b'window'))\n"
                "send.restype = ctypes.c_ulong\n"
                "mask = send(w, objc.sel_registerName(b'styleMask'))\n"
                "send.restype = ctypes.c_bool\n"
                "clear = send(w, objc.sel_registerName(b'titlebarAppearsTransparent'))\n"
                "send.restype = ctypes.c_long\n"
                "hidden = send(w, objc.sel_registerName(b'titleVisibility'))\n"
                "send.restype = ctypes.c_bool\n"
                "movable = send(w, objc.sel_registerName(b'isMovableByWindowBackground'))\n"
                "print(bool(mask & macos.NS_FULL_SIZE_CONTENT_VIEW), clear, hidden, movable,"
                " d.layout().contentsMargins().top())\n")
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa', KCS_MESSENGER_HOME=os.path.join(root, '.no-such-home'))
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                                text=True, timeout=30)
        if result.returncode != 0 or not result.stdout.strip():
            self.skipTest(f'GUI 세션이 없어 cocoa 창을 띄울 수 없음: {result.stderr.strip()[-80:]}')
        # 본문이 창 맨 위까지 · 제목 표시줄 투명 · 제목 글자 숨김 · 신호등 높이만큼 내용을 내림
        # 배경 끌기는 꺼 둔다: 켜면 누른 채로 있을 때 창 끌기로 잡혀 소속 회사 목록이 바로 닫혔다
        self.assertEqual(result.stdout.split(),
                         ['True', 'True', '1', 'False', str(48 + macos.TITLE_BAR_HEIGHT)])



class MainWindowTitleBarTests(unittest.TestCase):
    def test_offscreen_main_window_has_no_band(self):
        from tests.kakao_fakes import make_demo_state
        from client.kakao.main_window import MainWindow
        state, _conn = make_demo_state()
        win = MainWindow(state, state.settings)
        win.show()
        self.assertEqual(win.body.band, 0)                                   # macOS 창이 아니면 그대로
        self.assertEqual(win.sidebar.layout().contentsMargins().top(), 16)
        win.close()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_cocoa_main_window_header_reaches_top(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os\n"
                "os.environ['QT_QPA_PLATFORM'] = 'cocoa'\n"
                "from tests.kakao_fakes import make_demo_state, app\n"
                "state, _c = make_demo_state()\n"
                "from client.kakao.main_window import MainWindow, apply_app_theme\n"
                "from client.kakao import macos\n"
                "apply_app_theme(state.settings)\n"
                "w = MainWindow(state, state.settings); w.resize(1300, 680); w.show(); app().processEvents()\n"
                "state.room_opened.emit(sorted(state.rooms)[0]); app().processEvents()\n"
                "img = w.body.grab().toImage()\n"
                "x = w.room_pane.x() + w.room_pane.width() // 2\n"
                "band = img.pixelColor(x * img.width() // w.body.width(), 4)\n"
                "print(w.body.band, w.sidebar.layout().contentsMargins().top(), w.is_split(), band.name(),\n"
                "      w.width(), flush=True)\n"
                "os._exit(0)\n")
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa')
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                                text=True, timeout=60)
        if result.returncode != 0 or not result.stdout.strip():
            self.skipTest(f'GUI 세션이 없어 cocoa 창을 띄울 수 없음: {result.stderr.strip()[-80:]}')
        band, inset, split, color, width = result.stdout.split()
        if int(width) < 1300:                     # 가상 화면이 좁은 환경(CI 러너)은 분할 화면 폭을 못 낸다
            self.skipTest(f'화면이 좁아 창을 1300px 로 키울 수 없음 (실제 {width}px)')
        self.assertEqual((int(band), int(inset), split), (macos.TITLE_BAR_HEIGHT, 16 + macos.TITLE_BAR_HEIGHT, 'True'))
        self.assertNotEqual(color, '#000000')     # 반투명 헤더색(rgba)을 QColor 로 바로 쓰면 검게 칠해졌다



class ChatRoomWindowTitleBarTests(unittest.TestCase):
    def test_offscreen_room_window_keeps_header_margin(self):
        from tests.kakao_fakes import make_demo_state
        from client.kakao.chat_room import ChatRoomWindow
        state, _conn = make_demo_state()
        win = ChatRoomWindow(state, sorted(state.rooms)[0])
        win.show()
        self.assertEqual(win.header.layout().contentsMargins().top(), ChatRoomWindow.HEADER_TOP)
        win.close()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_cocoa_room_window_header_reaches_top_and_split_pane_does_not(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os\n"
                "os.environ['QT_QPA_PLATFORM'] = 'cocoa'\n"
                "from tests.kakao_fakes import make_demo_state, app\n"
                "state, _c = make_demo_state()\n"
                "from client.kakao.main_window import MainWindow, apply_app_theme\n"
                "apply_app_theme(state.settings)\n"
                "w = MainWindow(state, state.settings); w.resize(1300, 680); w.show(); app().processEvents()\n"
                "r = w.open_room(sorted(state.rooms)[0]); app().processEvents()\n"
                "top = lambda: r.header.layout().contentsMargins().top()\n"
                "embedded = (r.isWindow(), top(), w.width())\n"
                "w.resize(400, 680); app().processEvents(); app().processEvents()\n"
                "print(embedded[0], embedded[1], r.isWindow(), top(), embedded[2], flush=True)\n"
                "os._exit(0)\n")
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa')
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                                text=True, timeout=60)
        if result.returncode != 0 or not result.stdout.strip():
            self.skipTest(f'GUI 세션이 없어 cocoa 창을 띄울 수 없음: {result.stderr.strip()[-80:]}')
        *values, width = result.stdout.split()
        if int(width) < 1300:                     # 가상 화면이 좁은 환경(CI 러너)은 분할 화면 폭을 못 낸다
            self.skipTest(f'화면이 좁아 창을 1300px 로 키울 수 없음 (실제 {width}px)')
        # 분할 화면 안에서는 원래 여백, 별도 창으로 꺼내면 신호등 높이만큼 헤더를 내린다(헤더가 창 맨 위까지)
        self.assertEqual(values, ['False', '10', 'True', str(10 + macos.TITLE_BAR_HEIGHT)])

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_cocoa_room_window_opens_where_it_was_placed(self):
        """showEvent 안에서 제목 표시줄을 바꾸면(아직 화면에 놓이기 전) macOS 가 창을 그 모니터 왼쪽 끝으로 옮겼다 -
        메인 창 오른쪽에 열려야 할 채팅방 창이 700px 넘게 떨어진 곳에 떴다 (2026-11-18, 보조 모니터에서 확인)"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os, time\n"
                "os.environ['QT_QPA_PLATFORM'] = 'cocoa'\n"
                "from tests.kakao_fakes import make_demo_state, app\n"
                "state, _c = make_demo_state()\n"
                "from client.kakao.chat_room import ChatRoomWindow\n"
                "a = app(); avail = a.primaryScreen().availableGeometry()\n"
                "x, y = avail.right() - 520, avail.top() + avail.height() // 4\n"
                "r = ChatRoomWindow(state, sorted(state.rooms)[0]); r.move(x, y); r.show()\n"
                "end = time.time() + 0.6\n"
                "while time.time() < end: a.processEvents(); time.sleep(0.01)\n"
                "print(x, y, r.pos().x(), r.pos().y(), flush=True)\n"
                "os._exit(0)\n")
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa')
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                                text=True, timeout=60)
        if result.returncode != 0 or not result.stdout.strip():
            self.skipTest(f'GUI 세션이 없어 cocoa 창을 띄울 수 없음: {result.stderr.strip()[-80:]}')
        want_x, want_y, got_x, got_y = map(int, result.stdout.split())
        # 고친 버그는 '모니터 왼쪽 끝으로 옮겨짐' → 가로 위치는 그대로여야 한다. 세로는 화면이 작은 가상 화면(CI 러너)에서
        # macOS 가 창을 메뉴 막대 · 보이는 영역 안으로 조금 밀어 내리기도 해서(러너에서 +60px) 그만큼은 허용한다
        self.assertEqual(got_x, want_x)
        self.assertLessEqual(abs(got_y - want_y), 80, (want_y, got_y))


class DialogTitleBarTests(unittest.TestCase):
    """모든 팝업: macOS 제목 표시줄을 본문과 같은 색으로 (시스템 다크 모드 + 앱 밝은 테마여도 검게 그리지 않는다)"""

    def test_targets_dialogs_and_marked_windows_only(self):
        from tests.kakao_fakes import app
        app()
        from PyQt5.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget
        from client.kakao.widgets import wants_matched_title_bar
        self.assertTrue(wants_matched_title_bar(QDialog()))
        self.assertTrue(wants_matched_title_bar(QMessageBox()))
        self.assertFalse(wants_matched_title_bar(QFileDialog()))             # macOS 기본 창
        self.assertFalse(wants_matched_title_bar(QWidget()))                 # 일반 창은 자기 방식 (unify_title_bar)
        marked = QWidget()
        marked.setProperty('titleBarColor', '#E6ECF5')
        self.assertTrue(wants_matched_title_bar(marked))                     # 통화 창처럼 표시한 창
        self.assertFalse(macos.match_title_bar(QDialog(), '#FFFFFF', False))  # cocoa 가 아니면 아무것도 안 함

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS 전용')
    def test_cocoa_dialog_title_bar_matches_body(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import ctypes, sys\n"
                "from PyQt5.QtWidgets import QApplication, QDialog\n"
                "app = QApplication([])\n"
                "from client.kakao import main_window, theme\n"
                "objc = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.dylib')\n"
                "objc.sel_registerName.restype = ctypes.c_void_p\n"
                "send = objc.objc_msgSend\n"
                "def msg(obj, sel, restype=ctypes.c_void_p):\n"
                "    send.restype = restype; send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]\n"
                "    return send(obj, objc.sel_registerName(sel))\n"
                "def state(d):\n"
                "    w = msg(int(d.winId()), b'window')\n"
                "    clear = msg(w, b'titlebarAppearsTransparent', ctypes.c_bool)\n"
                "    name = ctypes.string_at(msg(msg(msg(w, b'appearance'), b'name'), b'UTF8String')).decode()\n"
                "    return clear, name\n"
                "for dark in (False, True):\n"
                "    theme.set_dark(dark); main_window.apply_app_theme(None)\n"
                "    d = QDialog(); d.show(); app.processEvents()\n"
                "    print(*state(d)); d.close()\n")
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa', KCS_MESSENGER_HOME=os.path.join(root, '.no-such-home'))
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                                text=True, timeout=30)
        if result.returncode != 0 or not result.stdout.strip():
            self.skipTest(f'GUI 세션이 없어 cocoa 창을 띄울 수 없음: {result.stderr.strip()[-80:]}')
        # 투명 제목 표시줄(뒤는 본문 색) + 창 모양은 앱 테마를 따른다 (시스템 다크 모드와 상관없이)
        self.assertEqual(result.stdout.split(), ['True', 'NSAppearanceNameAqua', 'True', 'NSAppearanceNameDarkAqua'])


if __name__ == '__main__':
    unittest.main()
