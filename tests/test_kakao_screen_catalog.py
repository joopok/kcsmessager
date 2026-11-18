# -*- coding: utf-8 -*-
"""화면 반응 측정 목록(scripts/harness/screen_probe.py)이 앱의 모든 창을 맡는지.

client/kakao 의 QDialog · QMainWindow 하위 클래스와 따로 뜨는 창(채팅방 · 통화)을 모두 찾아,
측정 목록에 없는 창이 있으면 실패한다 — 새 창을 만들면 screen_probe.screens() 에도 넣어 열리는 시간을 잰다.
"""

import importlib
import inspect
import os
import pkgutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.kakao_fakes import app  # noqa: E402

# QDialog · QMainWindow 가 아니지만 따로 뜨는 창 (QWidget + Qt.Window)
STANDALONE_WINDOWS = {'chat_room.ChatRoomWindow', 'call.CallWindow'}


def app_window_classes():
    """'모듈.클래스' → 클래스 (client/kakao 에 정의된 QDialog · QMainWindow 하위 클래스 + 따로 뜨는 창)"""
    from PyQt5.QtWidgets import QDialog, QMainWindow
    import client.kakao as pkg
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module('client.kakao.' + info.name)
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ != mod.__name__:
                continue
            key = f'{info.name}.{name}'
            if issubclass(cls, (QDialog, QMainWindow)) or key in STANDALONE_WINDOWS:
                found[key] = cls
    return found


class ScreenCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()
        from scripts.harness import screen_probe
        cls.probe = screen_probe
        cls.windows = app_window_classes()
        cls.covered = screen_probe.covered_classes()

    def test_every_window_is_measured(self):
        missing = sorted(set(self.windows) - self.covered - set(self.probe.NOT_SCREENS))
        self.assertEqual(missing, [], '화면 반응 측정 목록(scripts/harness/screen_probe.py screens())에 없는 창 - '
                                      '여는 방법을 넣어 주세요')

    def test_catalog_names_real_windows(self):
        stale = sorted(self.covered - set(self.windows))
        self.assertEqual(stale, [], '측정 목록에 있는데 앱에 없는 창 (이름이 바뀌었거나 지워짐)')
        self.assertFalse(set(self.probe.NOT_SCREENS) - set(self.windows), '공통 바탕으로 뺀 클래스가 앱에 없음')

    def test_standalone_windows_exist(self):
        for key in STANDALONE_WINDOWS:
            self.assertIn(key, self.windows)

    def test_limits(self):
        self.assertEqual(self.probe.LIMIT_MS, 500)          # 사용자 기준: 누르고 0.5초 안에
        self.assertLess(self.probe.TARGET_MS, self.probe.LIMIT_MS)
        self.assertGreaterEqual(self.probe.SCALE['users'], 500)
        self.assertGreaterEqual(self.probe.SCALE['rooms'], 300)
        self.assertGreaterEqual(self.probe.SCALE['messages'], 1000)

    def test_keys_are_unique(self):
        class Stub:
            info = {'big': '', 'rooms': ['', '', ''], 'root_id': 0, 'names': [], 'png': b''}
            state = s = w = None
        keys = [entry[0] for entry in self.probe.screens(Stub())]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == '__main__':
    unittest.main()
