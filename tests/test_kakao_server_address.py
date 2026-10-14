# -*- coding: utf-8 -*-
"""서버 주소는 그룹 관리자만 (명세 §4.3, 2026-10-10): 로그인 창에 서버 설정 없음,
접속 주소 = 명령줄 → 이 PC 의 server.json → 저장된 로그인 → 기본값, 더보기 → 정보(그룹 관리자만 보임)에서 저장"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PyQt5.QtWidgets import QMessageBox

from tests.kakao_fakes import app, make_demo_state

from client.kakao import settings as ksettings
from client.kakao.more_tab import MoreTab


class ServerAddressFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_save_load_and_validate(self):
        self.assertIsNone(ksettings.load_server_address(self.dir))
        self.assertEqual(ksettings.save_server_address(' 192.168.0.10 ', '6000', self.dir), ('192.168.0.10', 6000))
        self.assertEqual(ksettings.load_server_address(self.dir), ('192.168.0.10', 6000))
        for host, port, word in (('', 5555, '서버 주소'), ('a b', 5555, '서버 주소'), ('http://x/y', 5555, '서버 주소'),
                                 ('x', 0, '포트'), ('x', 70000, '포트'), ('x', 'abc', '포트')):
            with self.assertRaises(ValueError) as cm:
                ksettings.save_server_address(host, port, self.dir)
            self.assertIn(word, str(cm.exception))
        self.assertEqual(ksettings.load_server_address(self.dir), ('192.168.0.10', 6000))   # 틀린 값은 안 덮음
        with open(os.path.join(self.dir, ksettings.SERVER_FILE), 'w', encoding='utf-8') as f:
            f.write('{broken')
        self.assertIsNone(ksettings.load_server_address(self.dir))

    def test_tls_flag_persists_and_survives_address_only_save(self):
        """tls 는 server.json 에 함께 저장되고, 주소만 다시 저장해도 풀리지 않는다 (2026-10-14)"""
        self.assertIs(ksettings.load_server_tls(self.dir), ksettings.DEFAULT_TLS)        # 파일 없음 → 기본값
        ksettings.save_server_address('192.168.0.10', 6000, self.dir)                    # tls 미지정 → 키 생략
        with open(os.path.join(self.dir, ksettings.SERVER_FILE), encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'host': '192.168.0.10', 'port': 6000})
        ksettings.save_server_address('talk.kcins.co.kr', 9550, self.dir, tls=True)
        self.assertTrue(ksettings.load_server_tls(self.dir))
        with open(os.path.join(self.dir, ksettings.SERVER_FILE), encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'host': 'talk.kcins.co.kr', 'port': 9550, 'tls': True})
        ksettings.save_server_address('talk.kcins.co.kr', 9551, self.dir)                # 더보기 → 정보: 주소만 저장
        self.assertTrue(ksettings.load_server_tls(self.dir))                             # tls 보존
        ksettings.save_server_address('127.0.0.1', 5555, self.dir, tls=False)
        self.assertFalse(ksettings.load_server_tls(self.dir))
        with open(os.path.join(self.dir, ksettings.SERVER_FILE), 'w', encoding='utf-8') as f:
            json.dump({'host': 'x', 'port': 1, 'tls': 'yes'}, f)                        # bool 아닌 값은 무시
        self.assertIs(ksettings.load_server_tls(self.dir), ksettings.DEFAULT_TLS)

    def test_bundled_deploy_defaults_sit_between_server_json_and_code_defaults(self):
        """kcs.py build --deploy 가 번들한 deploy.json: server.json 이 없을 때만 쓰이고, 있으면 server.json 이 이긴다"""
        deploy = {'host': 'talk.kcins.co.kr', 'port': 9550, 'tls': True}
        with mock.patch.object(ksettings, 'load_deploy_defaults', return_value=deploy):
            self.assertEqual(ksettings.load_server_address(self.dir), ('talk.kcins.co.kr', 9550))
            self.assertTrue(ksettings.load_server_tls(self.dir))
            ksettings.save_server_address('192.168.0.10', 6000, self.dir, tls=False)   # 그룹 관리자가 이 PC 에 지정
            self.assertEqual(ksettings.load_server_address(self.dir), ('192.168.0.10', 6000))
            self.assertFalse(ksettings.load_server_tls(self.dir))
        with mock.patch.object(ksettings.osenv, 'resource_path', return_value=os.path.join(self.dir, 'none.json')):
            self.assertIsNone(ksettings.load_deploy_defaults())                       # 번들 없음(소스 실행)

    def test_deploy_file_round_trip(self):
        """build_app.write_deploy_file → load_deploy_defaults 가 같은 값을 읽는다"""
        from scripts import build_app
        out = os.path.join(self.dir, 'dist')
        path = build_app.write_deploy_file(out, build_app.parse_deploy('talk.kcins.co.kr:9550'), tls=True)
        self.assertEqual(os.path.relpath(path, self.dir), os.path.join('build', 'deploy', 'deploy.json'))
        with mock.patch.object(ksettings.osenv, 'resource_path', return_value=path):
            self.assertEqual(ksettings.load_deploy_defaults(), {'host': 'talk.kcins.co.kr', 'port': 9550, 'tls': True})
        for bad in ('talk.kcins.co.kr', ':9550', 'h:0', 'h:70000', 'h:abc'):
            with self.assertRaises(ValueError):
                build_app.parse_deploy(bad)


class AppAddressOrderTests(unittest.TestCase):
    """app.main 이 어떤 주소로 접속하는지 - Session 을 가짜로 바꿔 첫 접속 주소만 본다"""

    def run_main(self, argv, configured=None, saved=None):
        from client.kakao import app as kapp, login
        seen = []

        class FakeSession:
            def __init__(self, host, port, *args, **kwargs):
                seen.append((host, port))

            def connect(self):
                raise OSError('테스트: 연결 안 함')

        with mock.patch('client.kakao.settings.load_server_address', return_value=configured), \
                mock.patch.object(login, 'load_saved_login', return_value=saved), \
                mock.patch.object(kapp, 'Session', FakeSession), \
                mock.patch.object(kapp, 'ask_connection_failure', return_value='quit'):
            kapp.main(argv)
        return seen[0]

    def test_command_line_then_server_file_then_saved_login(self):
        saved = {'login_id': '김명수', 'token': 'tok', 'auto_login': True, 'host': '10.0.0.9', 'port': 7000}
        self.assertEqual(self.run_main(['--host', '1.2.3.4', '--port', '6001', '--user', 'a'],
                                       configured=('5.6.7.8', 6002)), ('1.2.3.4', 6001))
        self.assertEqual(self.run_main(['--user', 'a'], configured=('5.6.7.8', 6002)), ('5.6.7.8', 6002))
        self.assertEqual(self.run_main([], configured=('5.6.7.8', 6002), saved=saved), ('5.6.7.8', 6002))  # 관리자 설정 우선
        self.assertEqual(self.run_main([], saved=saved), ('10.0.0.9', 7000))
        self.assertEqual(self.run_main(['--user', 'a']), ('127.0.0.1', 5555))

    def test_connection_failure_has_no_server_settings_button(self):
        from client.kakao import app as kapp
        texts = []

        def fake_exec(box):
            texts.extend(b.text() for b in box.buttons())
            return 0
        with mock.patch.object(QMessageBox, 'exec_', fake_exec):
            self.assertEqual(kapp.ask_connection_failure('1.2.3.4', 5555, OSError('x')), 'quit')
        self.assertEqual(texts[:2], ['다시 시도', '종료'])                        # (+ Qt 의 '자세한 정보' 단추)
        self.assertFalse(any('서버' in t for t in texts))


class InfoSectionTests(unittest.TestCase):
    def setUp(self):
        app()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        patcher = mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': self.dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state, self.conn = make_demo_state()                 # 나 = 홍길동

    def tab(self):
        tab = MoreTab(self.state, self.state.settings, server_address='127.0.0.1:5555')
        self.addCleanup(tab.deleteLater)
        return tab

    def admin(self, company=''):
        self.conn.push('user_updated', user=dict(self.state.user('홍길동'), is_admin=True, admin_company=company,
                                                 account='kcs_administrator'))

    def test_info_only_for_group_admin(self):
        tab = self.tab()
        self.assertFalse(tab.info_section.isVisibleTo(tab))                  # 일반 사용자
        self.assertFalse(tab.info_group.isVisibleTo(tab))
        self.admin('케이씨에스')                                               # 회사 관리자도 안 보임
        self.assertFalse(tab.info_group.isVisibleTo(tab))
        self.assertFalse(tab.save_server_address())
        self.assertIsNone(ksettings.load_server_address())
        self.admin('')                                                         # 그룹 관리자(kcs_administrator)
        self.assertTrue(tab.info_section.isVisibleTo(tab))
        self.assertTrue(tab.info_group.isVisibleTo(tab))
        self.assertEqual((tab.server_host_edit.text(), tab.server_port_edit.text()), ('127.0.0.1', '5555'))

    def test_group_admin_saves_server_address(self):
        self.admin('')
        tab = self.tab()
        tab.server_host_edit.setText('chat.kcs.co.kr')
        tab.server_port_edit.setText('99999')
        self.assertFalse(tab.save_server_address())
        self.assertIn('포트', tab.server_status.text())
        self.assertIsNone(ksettings.load_server_address())
        tab.server_port_edit.setText('6000')
        tab.server_save_button.click()
        self.assertEqual(ksettings.load_server_address(), ('chat.kcs.co.kr', 6000))
        self.assertIn('다음 로그인부터 chat.kcs.co.kr:6000', tab.server_status.text())
        with open(os.path.join(self.dir, ksettings.SERVER_FILE), encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'host': 'chat.kcs.co.kr', 'port': 6000})
        again = self.tab()                                                      # 다시 열면 저장한 값
        self.assertEqual((again.server_host_edit.text(), again.server_port_edit.text()), ('chat.kcs.co.kr', '6000'))


if __name__ == '__main__':
    unittest.main()
