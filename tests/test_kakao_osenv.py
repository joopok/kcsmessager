# -*- coding: utf-8 -*-
"""운영체제 공통 계층 (client/kakao/osenv.py) + 공통 실행기 (kcs.py) · 빌드 · 바로가기 - Windows · macOS · Linux"""

import importlib.util
import io
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.kakao_fakes import app

from client.kakao import osenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORMS = {'mac': 'darwin', 'windows': 'win32', 'linux': 'linux'}


def load_script(name):
    spec = importlib.util.spec_from_file_location(f'test_{name}', os.path.join(ROOT, 'scripts', f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def on(os_name):
    return mock.patch.object(sys, 'platform', PLATFORMS[os_name])


class DetectTest(unittest.TestCase):
    def test_current(self):
        for name, value in PLATFORMS.items():
            with on(name):
                self.assertEqual(osenv.current(), name)
        for value in ('cygwin', 'msys'):
            with mock.patch.object(sys, 'platform', value):
                self.assertTrue(osenv.is_windows())
        with mock.patch.object(sys, 'platform', 'freebsd13'):
            self.assertTrue(osenv.is_linux())          # 그 밖의 유닉스는 Linux 처럼

    def test_no_platform_checks_outside_osenv(self):
        """클라이언트 코드는 sys.platform 을 직접 보지 않고 osenv 를 쓴다 (macos.py 는 macOS 전용 보정 모듈)"""
        allowed = {'osenv.py', 'macos.py'}
        offenders = []
        folder = os.path.join(ROOT, 'client', 'kakao')
        for name in sorted(os.listdir(folder)):
            if name.endswith('.py') and name not in allowed:
                with open(os.path.join(folder, name), encoding='utf-8') as f:
                    if re.search(r'sys\.platform|platform\.system\(|os\.name\b', f.read()):
                        offenders.append(name)
        self.assertEqual(offenders, [])

    def test_no_hardcoded_data_home(self):
        folder = os.path.join(ROOT, 'client', 'kakao')
        for name in os.listdir(folder):
            if name.endswith('.py') and name != 'osenv.py':
                with open(os.path.join(folder, name), encoding='utf-8') as f:
                    self.assertNotIn("'.kcs_messenger'", f.read(), name)


class PathTest(unittest.TestCase):
    def test_app_home_env_and_default(self):
        with mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': '/tmp/kcs_x'}):
            self.assertEqual(osenv.app_home(), '/tmp/kcs_x')
            self.assertEqual(osenv.app_path('keys'), os.path.join('/tmp/kcs_x', 'keys'))
        env = {k: v for k, v in os.environ.items() if k != 'KCS_MESSENGER_HOME'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(osenv.app_home(), os.path.join(os.path.expanduser('~'), '.kcs_messenger'))

    def test_settings_login_keys_use_app_home(self):
        from client.kakao import secret, login, call
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'KCS_MESSENGER_HOME': tmp}):
            self.assertEqual(secret.key_dir(), os.path.join(tmp, 'keys'))
            self.assertEqual(call.recordings_dir(), os.path.join(tmp, 'recordings'))
            self.assertEqual(login._home_file('saved.json'), os.path.join(tmp, 'saved.json'))

    def test_user_dir_existing_folder(self):
        app()
        for kind in ('downloads', 'documents', 'pictures', 'home'):
            self.assertTrue(os.path.isdir(osenv.user_dir(kind)), kind)

    def test_user_dir_falls_back_to_home(self):
        from PyQt5.QtCore import QStandardPaths
        with mock.patch.object(QStandardPaths, 'writableLocation', return_value='/no/such/dir'):
            self.assertEqual(osenv.user_dir('downloads'), os.path.expanduser('~'))

    def test_resource_path_source_and_frozen(self):
        self.assertEqual(osenv.resource_path('assets', 'fonts'), os.path.join(ROOT, 'assets', 'fonts'))
        with mock.patch.object(sys, '_MEIPASS', '/frozen/app', create=True):
            self.assertEqual(osenv.resource_path('assets', 'fonts'), os.path.join('/frozen/app', 'assets', 'fonts'))

    def test_theme_and_brand_find_assets(self):
        from client.kakao import theme, brand
        self.assertTrue(os.path.exists(os.path.join(theme.FONT_DIR, 'Pretendard-Regular.otf')))
        self.assertTrue(os.path.isdir(brand.BRAND_DIR))

    def test_pretendard_all_weights_300_to_900(self):
        """기본 글꼴 Pretendard: assets/fonts 에 300~900 정적 글꼴 7종 + OFL 라이선스, 굵기마다 그 파일로 그린다"""
        from PyQt5.QtGui import QFontInfo, QFontDatabase
        from client.kakao import theme
        app()
        self.assertEqual(sorted(theme.FONT_WEIGHTS), [300, 400, 500, 600, 700, 800, 900])
        for name in theme.BUNDLED_FONTS:
            self.assertTrue(os.path.getsize(os.path.join(theme.FONT_DIR, name)) > 1_000_000, name)
        with open(os.path.join(theme.FONT_DIR, 'Pretendard-OFL.txt'), encoding='utf-8') as f:
            self.assertIn('SIL OPEN FONT LICENSE', f.read().upper())
        theme.load_bundled_fonts()
        self.assertEqual(set(QFontDatabase().styles('Pretendard')),
                         {'Light', 'Regular', 'Medium', 'SemiBold', 'Bold', 'ExtraBold', 'Black'})
        self.assertEqual(theme.font_families()[0], 'Pretendard')                  # 기본 글꼴
        self.assertEqual(QFontInfo(theme.font()).family(), 'Pretendard')
        for weight, style in theme.PRETENDARD_STYLES.items():
            self.assertEqual(QFontInfo(theme.font('body', weight=weight)).styleName(), style, weight)
        self.assertEqual(QFontInfo(theme.font(bold=True)).styleName(), 'Bold')
        with self.assertRaises(ValueError):
            theme.font(weight=450)

    def test_display_path_other_drive(self):
        """Windows: 저장소(D:)와 다른 드라이브(C:) 경로도 오류 없이 보여 준다"""
        self.assertEqual(osenv.display_path(os.path.join(ROOT, 'claudedocs', 'x.md')), os.path.join('claudedocs', 'x.md'))
        with mock.patch('os.path.relpath', side_effect=ValueError('path is on mount C:, start on mount D:')):
            self.assertEqual(osenv.display_path('/tmp/x.md'), os.path.abspath('/tmp/x.md'))

    def test_secure_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            with on('windows'), mock.patch('os.chmod') as chmod:
                osenv.secure_file(path)
                chmod.assert_not_called()
            if not sys.platform.startswith('win'):
                osenv.secure_file(path)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        finally:
            os.unlink(path)


class FileNameTest(unittest.TestCase):
    def test_safe_filename_any_os(self):
        f = osenv.safe_filename
        self.assertEqual(f('회의:안건.txt'), '회의_안건.txt')               # Windows 금지 글자
        self.assertEqual(f('../../etc/passwd'), 'passwd')                    # 경로는 떼어 낸다
        self.assertEqual(f(r'C:\Users\a\보고서?.pdf'), '보고서_.pdf')
        self.assertEqual(f('CON.txt'), '_CON.txt')                           # Windows 예약 이름
        self.assertEqual(f(' .. '), 'file')
        self.assertEqual(f('', 'image.png'), 'image.png')
        self.assertLessEqual(len(f('가' * 300 + '.png')), 180)
        self.assertTrue(f('가' * 300 + '.png').endswith('.png'))
        self.assertEqual(f('정상 파일.docx'), '정상 파일.docx')

    def test_export_names_safe(self):
        from client.kakao.chat_export import safe_name
        self.assertEqual(safe_name('A/B: 회의'), 'A_B_ 회의')
        self.assertEqual(safe_name('nul'), '_nul')


class OpenTest(unittest.TestCase):
    def test_open_command(self):
        with on('mac'):
            self.assertEqual(osenv.open_command('/x'), ['open', '/x'])
        with on('linux'):
            self.assertEqual(osenv.open_command('/x'), ['xdg-open', '/x'])
        with on('windows'):
            self.assertIsNone(osenv.open_command('/x'))       # os.startfile

    def test_open_uses_qt_first_then_fallback(self):
        from PyQt5.QtGui import QDesktopServices
        with mock.patch.object(QDesktopServices, 'openUrl', return_value=True) as qt:
            self.assertTrue(osenv.open_url('https://kcs.co.kr'))
            self.assertTrue(osenv.open_path(ROOT))
            self.assertEqual(qt.call_count, 2)
        with mock.patch.object(QDesktopServices, 'openUrl', return_value=False), on('linux'), \
                mock.patch('subprocess.Popen') as popen:
            self.assertTrue(osenv.open_url('https://kcs.co.kr'))
            self.assertEqual(popen.call_args[0][0], ['xdg-open', 'https://kcs.co.kr'])


class TextTest(unittest.TestCase):
    def test_shortcut_text(self):
        with on('mac'):
            self.assertEqual(osenv.shortcut_text('Ctrl+Shift+Enter'), '⇧⌘Enter')    # Apple 표기 순서 ⌃⌥⇧⌘
            self.assertEqual(osenv.shortcut_text('Ctrl+K'), '⌘K')
            self.assertEqual(osenv.shortcut_text('Alt+↑/↓'), '⌥↑/↓')
            self.assertEqual(osenv.mod_label(), '⌘')
        for name in ('windows', 'linux'):
            with on(name):
                self.assertEqual(osenv.shortcut_text('Ctrl+Shift+Enter'), 'Ctrl+Shift+Enter')
                self.assertEqual(osenv.mod_label(), 'Ctrl')

    def test_font_order_per_os(self):
        expect_first = {'mac': 'Apple SD Gothic Neo', 'windows': '맑은 고딕', 'linux': 'Noto Sans CJK KR'}
        for name, first in expect_first.items():
            with on(name):
                fams = osenv.ui_font_families()
                self.assertEqual(fams[0], 'Pretendard')                    # 앱에 든 글꼴이 먼저
                self.assertEqual(fams[1], first)                           # 그 다음 이 OS 한글 글꼴
                self.assertEqual(len(fams), len(set(fams)))
                self.assertTrue({'Apple SD Gothic Neo', '맑은 고딕', 'Noto Sans CJK KR'} <= set(fams))

    def test_emoji_order_per_os(self):
        expect = {'mac': 'Apple Color Emoji', 'windows': 'Segoe UI Emoji', 'linux': 'Noto Color Emoji'}
        for name, first in expect.items():
            with on(name):
                self.assertEqual(osenv.emoji_families()[0], first)
                self.assertEqual(len(osenv.emoji_families()), 3)

    def test_tooltips_follow_os(self):
        from client.kakao import chat_room
        self.assertEqual(chat_room.SILENT_KEYS, osenv.shortcut_text('Ctrl+Shift+Enter'))


class StartupTest(unittest.TestCase):
    def test_windows_app_id_only_on_windows(self):
        with on('mac'):
            self.assertFalse(osenv.set_windows_app_id())
        fake = mock.MagicMock()
        fake.windll.shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        with on('windows'), mock.patch.dict(sys.modules, {'ctypes': fake}):
            self.assertTrue(osenv.set_windows_app_id())
            fake.windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_with(osenv.APP_ID)

    def test_configure_app(self):
        qapp = app()
        with mock.patch.object(qapp, 'setStyle') as set_style:           # (앱 스타일시트가 있으면 style() 은 대리 객체)
            osenv.configure_app(qapp)
        set_style.assert_called_once_with('Fusion')                       # 세 OS 같은 모양
        self.assertEqual(qapp.applicationName(), 'KCS Talk')
        with on('linux'):
            osenv.configure_app(qapp)
            self.assertEqual(qapp.desktopFileName(), osenv.DESKTOP_FILE)

    def test_console_output_is_utf8(self):
        """빌드 exe(Windows cp1252 출력)에서도 '✓' · 한글 출력이 앱을 멈추지 않는다"""
        import io
        import client.kakao.app as app_mod
        raw = io.BytesIO()
        cp = io.TextIOWrapper(raw, encoding='cp1252')
        with mock.patch.object(sys, 'stdout', cp), mock.patch.object(sys, 'stderr', cp):
            app_mod._attach_log_when_no_console()
            print('✓ 글꼴 폴더')
            sys.stdout.flush()
        self.assertIn('✓ 글꼴 폴더'.encode('utf-8'), raw.getvalue())

    def test_relaunch_command(self):
        with mock.patch.object(sys, 'frozen', False, create=True):
            prog, args = osenv.relaunch_command(['client/kakao/app.py', '--port', '5555'])
            self.assertEqual(prog, sys.executable)
            self.assertEqual(args, [os.path.abspath('client/kakao/app.py'), '--port', '5555'])
        with mock.patch.object(sys, 'frozen', True, create=True):
            prog, args = osenv.relaunch_command(['/Applications/KCS Talk.app/Contents/MacOS/KCS Talk', '--port', '1'])
            self.assertEqual((prog, args), (sys.executable, ['--port', '1']))   # 빌드한 앱은 실행 파일 그대로

    def test_server_data_next_to_frozen_executable(self):
        import importlib
        import server.kakao.server as srv
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', os.path.join(os.sep, 'opt', 'kcs', 'KCS Talk Server')):
            reloaded = importlib.reload(srv)
            self.assertEqual(reloaded.DEFAULT_DB_PATH,
                             os.path.join(os.path.abspath(os.path.join(os.sep, 'opt', 'kcs')), 'server_data', 'kakao.db'))
        importlib.reload(srv)
        self.assertEqual(srv.DEFAULT_DB_PATH, os.path.join(ROOT, 'server_data', 'kakao.db'))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.build = load_script('build_app')

    def test_args_per_os(self):
        for name, sep, icon in (('mac', ':', 'icon.icns'), ('windows', ';', 'icon.ico'), ('linux', ':', 'app_icon_256.png')):
            args = self.build.pyinstaller_args(os_name=name, out='/tmp/out')
            self.assertIn('--windowed', args)
            data = [args[i + 1] for i, a in enumerate(args) if a == '--add-data']
            self.assertTrue(all(sep + 'assets/' in d for d in data), (name, data))
            self.assertEqual(len(data), 3)                              # 글꼴 · CI · 아이콘
            self.assertTrue(args[args.index('--icon') + 1].endswith(icon), name)
            self.assertEqual(args[-1], os.path.join(ROOT, 'client', 'kakao', 'app.py'))
            self.assertEqual(args[args.index('--distpath') + 1], '/tmp/out')
            self.assertEqual('--osx-bundle-identifier' in args, name == 'mac')

    def test_server_args(self):
        args = self.build.pyinstaller_args(server=True, os_name='windows')
        self.assertIn('--console', args)
        self.assertNotIn('--add-data', args)                            # DB · org.json 은 실행 파일 옆에
        self.assertEqual(args[args.index('--name') + 1], 'KCS Talk Server')
        self.assertTrue(args[-1].endswith(os.path.join('server', 'chat', 'chat_server.py')))

    def test_dry_run_prints_command(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            self.assertEqual(self.build.main(['--dry-run']), 0)
        self.assertIn('PyInstaller', out.getvalue())

    def test_mac_sign_commands(self):
        dev = 'Developer ID Application: KCS (TEAM123)'
        self.assertFalse(self.build.is_developer_id('-'))
        self.assertFalse(self.build.is_developer_id('Apple Development: a@b.c (X)'))   # 배포용 아님
        adhoc = self.build.sign_command('/x/KCS Talk.app', '-', self.build.ENTITLEMENTS, deep=True)
        self.assertEqual(adhoc[:4], ['codesign', '--force', '--sign', '-'])
        self.assertNotIn('runtime', adhoc)
        signed = self.build.sign_command('/x/KCS Talk.app', dev, self.build.ENTITLEMENTS, deep=True)
        for flag in ('--timestamp', 'runtime', '--entitlements', '--deep'):    # 공증 조건
            self.assertIn(flag, signed)
        self.assertTrue(os.path.exists(self.build.ENTITLEMENTS))

    def test_mac_dmg_commands(self):
        dev = 'Developer ID Application: KCS (TEAM123)'
        tools = lambda cmds: [c[1] if c[0] == 'xcrun' else c[0] for c in cmds]
        self.assertEqual(tools(self.build.dmg_commands('/s', '/o/a.dmg', '-', 'p')), ['hdiutil'])
        self.assertEqual(tools(self.build.dmg_commands('/s', '/o/a.dmg', dev, None)), ['hdiutil', 'codesign'])
        self.assertEqual(tools(self.build.dmg_commands('/s', '/o/a.dmg', dev, 'kcs-notary')),
                         ['hdiutil', 'codesign', 'notarytool', 'stapler'])
        self.assertRegex(self.build.dmg_name(), r'^\d{12}_kcsmessager\.dmg$')

    def test_mac_dmg_staging_and_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = os.path.join(tmp, 'KCS Talk.app')
            os.makedirs(os.path.join(app, 'Contents'))
            with open(os.path.join(app, 'Contents', 'Info.plist'), 'wb') as f:
                plistlib.dump({'CFBundleIdentifier': 'kr.co.kcs.talk'}, f)
            self.build.patch_info_plist(app)
            with open(os.path.join(app, 'Contents', 'Info.plist'), 'rb') as f:
                info = plistlib.load(f)
            self.assertIn('NSCameraUsageDescription', info)                # 페이스톡이 강제 종료되지 않게
            self.assertEqual(info['CFBundleIdentifier'], 'kr.co.kcs.talk')
            if shutil.which('ditto'):
                staging = os.path.join(tmp, 'stage')
                os.makedirs(staging)
                self.build.stage_dmg(app, staging, notarized=False)
                self.assertEqual(os.readlink(os.path.join(staging, 'Applications')), '/Applications')
                with open(os.path.join(staging, '설치 안내.txt'), encoding='utf-8') as f:
                    self.assertIn('그래도 열기', f.read())

    def test_mac_installer_script(self):
        script = self.build.installer_script('/o/202610150037_kcsmessager.dmg', 'ab' * 32, 'https://portal.example/dl')
        self.assertTrue(script.startswith('#!/bin/bash'))
        self.assertIn('https://portal.example/dl/202610150037_kcsmessager.dmg', script)
        self.assertIn('SHA256="' + 'ab' * 32 + '"', script)              # 받은 파일 무결성 확인
        self.assertIn('curl -fL', script)                                   # curl 은 다운로드 표시를 붙이지 않는다
        self.assertIn('xattr -dr com.apple.quarantine', script)
        self.assertIn('APP="KCS Talk.app"', script)
        if shutil.which('bash'):
            with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
                f.write(script)
            try:
                self.assertEqual(subprocess.call(['bash', '-n', f.name]), 0)  # 문법 오류 없음
            finally:
                os.unlink(f.name)

    def test_dmg_only_mac_client(self):
        err = io.StringIO()
        with mock.patch('sys.stderr', err), mock.patch.object(self.build.osenv, 'is_mac', return_value=False):
            self.assertEqual(self.build.main(['--dmg', '--dry-run']), 2)
        with mock.patch('sys.stderr', err), mock.patch.object(self.build.osenv, 'is_mac', return_value=True):
            self.assertEqual(self.build.main(['--dmg', '--server', '--dry-run']), 2)
        out = io.StringIO()
        with mock.patch('sys.stdout', out), mock.patch.object(self.build.osenv, 'is_mac', return_value=True):
            self.assertEqual(self.build.main(['--dmg', '--dry-run']), 0)
        self.assertIn('codesign', out.getvalue())
        self.assertIn('hdiutil', out.getvalue())

    def test_desktop_entry(self):
        text = self.build.desktop_entry('/opt/KCS Talk/KCS Talk', '/opt/icon.png')
        self.assertIn('Exec="/opt/KCS Talk/KCS Talk"', text)
        self.assertIn('Categories=Network;InstantMessaging;Chat;', text)


class ShortcutTest(unittest.TestCase):
    def setUp(self):
        self.sc = load_script('make_shortcut')

    def test_plan_per_os(self):
        with mock.patch.object(self.sc, 'built_executable', return_value=None):
            linux = self.sc.plan('linux', home='/home/u')
            self.assertEqual(linux[0][0], os.path.join('/home/u', '.local', 'share', 'applications',
                                                       'kr.co.kcs.talk.desktop'))
            self.assertIn('kcs.py" client', linux[0][1])
            mac = self.sc.plan('mac', home='/Users/u')
            self.assertEqual(mac[0][0], os.path.join('/Users/u', 'Desktop', 'KCS Talk.command'))
            self.assertTrue(mac[0][1].startswith('#!/bin/bash'))
            with mock.patch.dict(os.environ, {'APPDATA': r'C:\Users\u\AppData\Roaming'}):
                win = self.sc.plan('windows', home=r'C:\Users\u')
            self.assertEqual([p[2] for p in win], ['바탕화면', '시작 메뉴'])
            self.assertTrue(all(p[0].endswith('KCS Talk.lnk') and p[1] is None for p in win))

    def test_plan_prefers_built_app(self):
        with mock.patch.object(self.sc, 'built_executable', return_value='/opt/KCS Talk/KCS Talk'):
            self.assertIn('Exec="/opt/KCS Talk/KCS Talk"', self.sc.plan('linux', home='/home/u')[0][1])

    def test_windows_lnk_script_quotes(self):
        script = self.sc.windows_lnk_script(r"C:\Users\o'neil\Desktop\KCS Talk.lnk")
        self.assertIn("o''neil", script)                                # PowerShell 작은따옴표 이스케이프
        self.assertIn('CreateShortcut', script)


class LauncherTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('kcs_launcher', os.path.join(ROOT, 'kcs.py'))
        self.kcs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.kcs)

    def test_doctor_ok_here(self):
        lines = []
        self.assertEqual(self.kcs.doctor(out=lines.append), 0)
        self.assertTrue(any('Pretendard 7/7' in line for line in lines))        # 300~900 일곱 굵기

    def test_command_routing(self):
        calls = []
        with mock.patch.dict(self.kcs.COMMANDS, {'client': lambda a: calls.append(('client', a)) or 0,
                                                 'server': lambda a: calls.append(('server', a)) or 0}):
            self.kcs.main([])
            self.kcs.main(['--port', '6000'])
            self.kcs.main(['server', '--host', '0.0.0.0'])
        self.assertEqual(calls, [('client', []), ('client', ['--port', '6000']), ('server', ['--host', '0.0.0.0'])])

    def test_launch_files_exist_with_right_line_endings(self):
        for path in ('launch/windows/KCS_Talk.bat', 'launch/windows/KCS_Talk_Server.bat', 'launch/windows/install.bat'):
            with open(os.path.join(ROOT, path), 'rb') as f:
                data = f.read()
            self.assertIn(b'kcs.py', data)
            self.assertEqual(data.count(b'\n'), data.count(b'\r\n'), path)      # CRLF
        for path in ('launch/macos/KCS Talk.command', 'launch/macos/install.command',
                     'launch/linux/kcs-talk.sh', 'launch/linux/install.sh'):
            full = os.path.join(ROOT, path)
            with open(full, 'rb') as f:
                data = f.read()
            self.assertTrue(data.startswith(b'#!'), path)
            self.assertNotIn(b'\r\n', data, path)
            if not sys.platform.startswith('win'):
                self.assertTrue(os.access(full, os.X_OK), path)



class LatinInputTests(unittest.TestCase):
    """사번 칸: 포커스가 오면 한글 대신 영문 입력 (macOS 입력 소스 · Windows IME)"""

    def setUp(self):
        app()

    def test_login_id_fields_prefer_latin(self):
        from PyQt5.QtCore import Qt
        from client.kakao.login import LoginDialog
        dlg = LoginDialog(fresh=True)
        self.addCleanup(dlg.deleteLater)
        for edit in (dlg.name_edit, dlg.reg_employee_edit, dlg.reset_id_edit):
            self.assertTrue(edit.inputMethodHints() & Qt.ImhLatinOnly, edit.objectName())
            self.assertTrue(hasattr(edit, '_latin_filter'))

    def test_focus_switches_input_per_os(self):
        from PyQt5.QtCore import QEvent
        from PyQt5.QtGui import QFocusEvent
        from PyQt5.QtWidgets import QLineEdit
        edit = QLineEdit()
        self.addCleanup(edit.deleteLater)
        flt = osenv.prefer_latin_input(edit)
        with mock.patch.object(osenv, 'select_latin_input') as select, \
                mock.patch.object(edit, 'hasFocus', return_value=True):
            flt.eventFilter(edit, QFocusEvent(QEvent.FocusIn))
            select.assert_not_called()                      # Qt 의 IME 처리 다음에
            app().processEvents()
        select.assert_called_once_with(edit)

    def test_dispatch_by_os(self):
        with mock.patch('PyQt5.QtGui.QGuiApplication.platformName', return_value='cocoa'):
            with mock.patch.object(sys, 'platform', 'darwin'), \
                    mock.patch('client.kakao.macos.select_ascii_input', return_value=True) as mac:
                self.assertTrue(osenv.select_latin_input())
            mac.assert_called_once()
            widget = mock.Mock()
            widget.window.return_value.winId.return_value = 42
            with mock.patch.object(sys, 'platform', 'win32'), \
                    mock.patch.object(osenv, '_windows_ime_alphanumeric', return_value=True) as win:
                self.assertTrue(osenv.select_latin_input(widget))
            win.assert_called_once_with(42)
            with mock.patch.object(sys, 'platform', 'linux'):
                self.assertFalse(osenv.select_latin_input(widget))
        self.assertFalse(osenv.select_latin_input(mock.Mock()))   # offscreen: 이 PC 입력 소스는 건드리지 않는다


class SystemIdleTests(unittest.TestCase):
    """컴퓨터 전체 무입력 시간 (자동 자리비움이 다른 앱에서의 마우스 움직임도 세도록)"""

    def test_returns_seconds_or_none(self):
        value = osenv.system_idle_seconds()
        self.assertTrue(value is None or (isinstance(value, float) and value >= 0))

    def test_failures_become_none(self):
        from unittest import mock
        with mock.patch.object(osenv, '_mac_idle_seconds', side_effect=OSError), \
                mock.patch.object(osenv, '_windows_idle_seconds', side_effect=OSError), \
                mock.patch.object(osenv, '_x11_idle_seconds', side_effect=OSError):
            self.assertIsNone(osenv.system_idle_seconds())

    def test_dispatch_by_os(self):
        from unittest import mock
        for platform, fn in (('darwin', '_mac_idle_seconds'), ('win32', '_windows_idle_seconds'),
                             ('linux', '_x11_idle_seconds')):
            with mock.patch.object(osenv.sys, 'platform', platform), \
                    mock.patch.object(osenv, fn, return_value=12.5):
                self.assertEqual(osenv.system_idle_seconds(), 12.5)


if __name__ == '__main__':
    unittest.main()
