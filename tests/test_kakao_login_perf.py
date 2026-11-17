# -*- coding: utf-8 -*-
"""로그인 직후 프로필 사진이 몰려와도 화면이 멈추지 않는다 (2026-10-10: 사진마다 탭 전체를 다시 만들어
빈 창에 로딩 커서만 1.5초 돌았다)"""

import time
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication

from tests.kakao_fakes import make_demo_state, app

from client.kakao import chats_tab, friends_tab, org_tab, more_tab


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class AvatarBurstTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_burst_of_avatars_refreshes_each_tab_once(self):
        counts = {}
        originals = {}

        def counting(cls, name):
            original = getattr(cls, name)
            originals[(cls, name)] = original

            def wrapper(this, *a, **k):
                key = (cls.__name__, id(this))                    # 화면마다 (조직도는 탭 + 옆 패널 두 개)
                counts[key] = counts.get(key, 0) + 1
                return original(this, *a, **k)
            return wrapper

        with mock.patch.multiple(org_tab.OrgTab, refresh=counting(org_tab.OrgTab, 'refresh')), \
                mock.patch.multiple(friends_tab.FriendsTab, refresh=counting(friends_tab.FriendsTab, 'refresh')), \
                mock.patch.multiple(more_tab.MoreTab, refresh_profile=counting(more_tab.MoreTab, 'refresh_profile')):
            from client.kakao.main_window import MainWindow
            win = MainWindow(self.state, self.state.settings)
            win.show()
            pump(0.2)
            counts.clear()
            started = time.perf_counter()
            for i in range(50):                                   # 로그인 직후 사진 50장
                self.state.avatar_changed.emit(f'user{i}')
            emit_ms = (time.perf_counter() - started) * 1000
            pump(0.3)
            win.close()
        self.assertLess(emit_ms, 200)                             # 알림을 받는 동안 화면을 붙잡지 않는다
        for name in ('OrgTab', 'FriendsTab', 'MoreTab'):
            per_screen = [n for (cls, _id), n in counts.items() if cls == name]
            if name != 'OrgTab':                                  # 다른 탭 뒤에 가려진 조직도는 다시 보일 때 그린다
                self.assertTrue(per_screen, name)
            self.assertLessEqual(set(per_screen), {1}, (name, counts))   # 몰려온 알림은 화면마다 한 번에 반영

    def test_relay_flush_and_single_change_still_applies(self):
        calls = []
        from PyQt5.QtCore import QObject

        class Owner(QObject):
            def refresh(self):
                calls.append(1)
        owner = Owner()
        chats_tab.connect_avatar_changed(self.state, owner.refresh)
        self.state.avatar_changed.emit('홍길동')
        self.assertEqual(calls, [])                               # 바로가 아니라 잠깐 모았다가
        pump(0.15)
        self.assertEqual(calls, [1])                              # 한 장만 와도 반영된다
        relay = owner.findChild(chats_tab._AvatarRelay)
        self.state.avatar_changed.emit('김철수')
        relay.flush()
        self.assertEqual(calls, [1, 1])


if __name__ == '__main__':
    unittest.main()
