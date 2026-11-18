# -*- coding: utf-8 -*-
"""배포 스크립트 (scripts/deploy_server.sh · scripts/postdeploy_check.py, docs/DEPLOY_CHECKLIST.md)
임시 폴더에 프로젝트 복사본 · 임시 git 저장소 · 가짜 pm2 를 두고 순서와 멈추는 조건을 본다.
다시 시작한 뒤 확인은 이 테스트가 띄운 실제 서버에 접속해서 한다"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from scripts import postdeploy_check
from server.kakao.server import KakaoServer
from server.kakao.store import Store
from tests.test_kakao_server import FakeRag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_TOOLS = os.name != 'nt' and shutil.which('bash') and shutil.which('git')

FAKE_PM2 = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_PM2_CALLS"
case "$1" in
  jlist) echo "[{\"name\": \"kcs-test-server\", \"pm2_env\": {\"status\": \"${FAKE_PM2_STATUS:-online}\", \"restart_time\": 3}}]" ;;
  logs) cat "$FAKE_PM2_LOG" ;;
  startOrReload) [ -z "${FAKE_PM2_FAIL:-}" ] || exit 1 ;;
esac
exit 0
"""

ECOSYSTEM = """const ROOT = '%(root)s';
module.exports = {
  apps: [
    {
      name: 'kcs-test-server',
      cwd: ROOT,
      args: 'kcs.py server --host 127.0.0.1 --port %(port)s --proxy-protocol%(extra)s --db %(root)s/server_data/kakao.db',
    },
  ],
};
"""

GOOD_LOG = """[SERVER] 2026-11-17 20:00:00 INFO KCS Messenger 서버 종료
[SERVER] 2026-11-17 20:00:05 INFO 표시 이름 2개를 조직도 이름으로 되돌렸습니다
[SERVER] 2026-11-17 20:00:05 INFO 파일 전송 보안: 새 기본 차단 형태 2개를 저장된 정책에 더했습니다 (매크로 문서, 속을 볼 수 없는 압축 파일 (7z · rar · alz · egg))
[SERVER] 2026-11-17 20:00:05 INFO KCS Messenger 서버 시작: 127.0.0.1:9551 (db=x)
[SERVER] 2026-11-17 20:00:09 INFO 누구 접속 (('10.0.0.1', 1))
"""


class PostDeployCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='kcs_post_')
        cls.closed = KakaoServer(port=0, db_path=os.path.join(cls.tmp, 'closed.db'), rag_factory=FakeRag,
                                 require_password=True, proxy_protocol=True)
        cls.closed.serve_in_thread()
        cls.opened = KakaoServer(port=0, db_path=os.path.join(cls.tmp, 'open.db'), rag_factory=FakeRag)
        cls.opened.serve_in_thread()

    @classmethod
    def tearDownClass(cls):
        cls.closed.stop()
        cls.opened.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def flags(results):
        return {name: ok for name, ok, _detail in results}

    def test_running_server_behind_proxy_protocol(self):
        results = postdeploy_check.check_server('127.0.0.1', self.closed.port, proxy_protocol=True)
        self.assertEqual(self.flags(results), {'서버 접속': True, 'ping → pong': True, '로그인 전 회사 목록': True,
                                               '등록 닫힘': True})

    def test_open_registration_is_reported(self):
        results = postdeploy_check.check_server('127.0.0.1', self.opened.port)
        self.assertFalse(self.flags(results)['등록 닫힘'])                      # 닫혀 있어야 하는데 열려 있다
        results = postdeploy_check.check_server('127.0.0.1', self.opened.port, expect_closed=False)
        self.assertTrue(self.flags(results)['등록 열림'])

    def test_nothing_listening(self):
        probe = __import__('socket').socket()
        probe.bind(('127.0.0.1', 0))
        free = probe.getsockname()[1]
        probe.close()
        results = postdeploy_check.check_server('127.0.0.1', free, wait=0.6)
        self.assertEqual([(name, ok) for name, ok, _d in results], [('서버 접속', False)])

    def test_pm2_listing(self):
        listing = json.dumps([{'name': 'other', 'pm2_env': {'status': 'online'}},
                              {'name': 'kcs', 'pm2_env': {'status': 'online', 'restart_time': 2}}])
        self.assertTrue(postdeploy_check.pm2_status(listing, 'kcs')[0][1])
        stopped = json.dumps([{'name': 'kcs', 'pm2_env': {'status': 'errored'}}])
        self.assertFalse(postdeploy_check.pm2_status(stopped, 'kcs')[0][1])
        self.assertFalse(postdeploy_check.pm2_status(listing, 'missing')[0][1])
        self.assertFalse(postdeploy_check.pm2_status('not json', 'kcs')[0][1])

    def test_log_summary(self):
        good = {name: (ok, detail) for name, ok, detail in postdeploy_check.summarize_log(GOOD_LOG)}
        self.assertEqual(good['실명으로 되돌린 표시 이름'], (True, '2개'))
        self.assertEqual(good['시작 뒤 오류 기록'], (True, '없음'))
        self.assertEqual(good['저장된 파일 정책에 더한 기본 차단 형태'], (True, '2개'))
        bad = postdeploy_check.summarize_log(GOOD_LOG + 'Traceback (most recent call last):\n  boom\n')
        self.assertFalse({name: ok for name, ok, _d in bad}['시작 뒤 오류 기록'])
        earlier = '[SERVER] x ERROR 예전 오류\n' + GOOD_LOG                     # 마지막 시작 전의 오류는 세지 않는다
        self.assertTrue(all(ok for _n, ok, _d in postdeploy_check.summarize_log(earlier)))
        no_rename = GOOD_LOG.replace('[SERVER] 2026-11-17 20:00:05 INFO 표시 이름 2개를 조직도 이름으로 되돌렸습니다\n', '')
        self.assertEqual(postdeploy_check.summarize_log(no_rename)[1][2], '0개')
        self.assertFalse(postdeploy_check.summarize_log('아무 기록 없음')[0][1])


@unittest.skipUnless(HAVE_TOOLS, 'bash 와 git 이 있는 곳에서만 (서버용 스크립트)')
class DeployScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_tmp = tempfile.mkdtemp(prefix='kcs_deploy_srv_')
        cls.server = KakaoServer(port=0, db_path=os.path.join(cls.server_tmp, 'live.db'), rag_factory=FakeRag,
                                 require_password=True, proxy_protocol=True)
        cls.server.serve_in_thread()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.server_tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', '-c', 'user.email=t@example.com', '-c', 'user.name=tester', *args],
                              cwd=cwd or self.proj, capture_output=True, text=True, check=True).stdout.strip()

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='kcs_deploy_'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, 'proj')
        os.makedirs(os.path.join(self.proj, 'scripts'))
        for name in ('deploy_server.sh', 'backup_db.py', 'predeploy_check.py', 'postdeploy_check.py'):
            shutil.copy2(os.path.join(ROOT, 'scripts', name), os.path.join(self.proj, 'scripts', name))
        shutil.copy2(os.path.join(ROOT, 'requirements-kcs.txt'), self.proj)
        with open(os.path.join(self.proj, 'kcs.py'), 'w') as f:
            f.write('# placeholder\n')
        with open(os.path.join(self.proj, '.gitignore'), 'w') as f:
            f.write('server_data/\n')
        self.write_ecosystem(self.server.port)
        self.db = os.path.join(self.proj, 'server_data', 'kakao.db')
        store = Store(self.db)
        store.member_create('가나다', {'company': 'KCS 홀딩스', 'email': 'gana@kcs.test'})
        store.set_admin('가나다', True)
        self.store = store
        self.addCleanup(store.close)
        with open(os.path.join(self.proj, 'server_data', 'portal.json'), 'w') as f:
            f.write('{"token": "TOP-SECRET-TOKEN"}')
        origin = os.path.join(self.tmp, 'origin.git')
        subprocess.run(['git', 'init', '--quiet', '--bare', origin], check=True)
        subprocess.run(['git', 'init', '--quiet', self.proj], check=True)
        self.git('checkout', '--quiet', '-b', 'main')
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'first')
        self.git('remote', 'add', 'origin', origin)
        self.git('push', '--quiet', '-u', 'origin', 'main')
        self.first = self.git('rev-parse', 'HEAD')
        self.calls = os.path.join(self.tmp, 'pm2_calls.txt')
        self.log = os.path.join(self.tmp, 'pm2_log.txt')
        with open(self.log, 'w', encoding='utf-8') as f:
            f.write(GOOD_LOG)
        self.pm2 = os.path.join(self.tmp, 'pm2')
        with open(self.pm2, 'w', encoding='utf-8') as f:
            f.write(FAKE_PM2)
        os.chmod(self.pm2, os.stat(self.pm2).st_mode | stat.S_IEXEC)

    def write_ecosystem(self, port, extra=''):
        with open(os.path.join(self.proj, 'ecosystem.config.cjs'), 'w', encoding='utf-8') as f:
            f.write(ECOSYSTEM % {'root': self.proj, 'port': port, 'extra': extra})

    def upstream_commit(self, name='NEWS.txt', text='new\n', message='second'):
        """원격에 새 커밋을 하나 올린다 → 그 커밋 해시"""
        work = os.path.join(self.tmp, f'work_{message}')
        subprocess.run(['git', 'clone', '--quiet', '--branch', 'main', os.path.join(self.tmp, 'origin.git'), work],
                       check=True)
        with open(os.path.join(work, name), 'a', encoding='utf-8') as f:
            f.write(text)
        self.git('add', '-A', cwd=work)
        self.git('commit', '--quiet', '-m', message, cwd=work)
        self.git('push', '--quiet', 'origin', 'main', cwd=work)
        return self.git('rev-parse', 'HEAD', cwd=work)

    def deploy(self, *args, **env):
        result = subprocess.run(
            ['bash', 'scripts/deploy_server.sh', *args], cwd=self.proj, capture_output=True, text=True, timeout=120,
            env={**os.environ, 'KCS_PM2': self.pm2, 'KCS_PYTHON': sys.executable, 'FAKE_PM2_CALLS': self.calls,
                 'FAKE_PM2_LOG': self.log, **env})
        return result.returncode, result.stdout + result.stderr

    def pm2_calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding='utf-8') as f:
            return [line.split()[0] for line in f.read().splitlines() if line.strip()]

    def backups(self):
        folder = os.path.join(self.proj, 'server_data', 'backups')
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_preview_changes_nothing(self):
        second = self.upstream_commit()
        code, out = self.deploy()
        self.assertEqual(code, 0, out)
        for expected in ('미리 보기', 'second', '겹치는 로그인 아이디', '✓ 없음', '배포할 수 있습니다', '--apply'):
            self.assertIn(expected, out)
        self.assertIn(second[:7], out)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.first)              # 코드 그대로
        self.assertEqual(self.pm2_calls(), [])                                 # pm2 를 부르지 않았다
        self.assertEqual(self.backups(), [])
        self.assertNotIn('TOP-SECRET-TOKEN', out)

    def test_apply_runs_every_step_in_order(self):
        second = self.upstream_commit()
        code, out = self.deploy('--apply')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('rev-parse', 'HEAD'), second)
        self.assertEqual(self.pm2_calls(), ['startOrReload', 'save', 'jlist', 'logs'])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(sorted(os.listdir(os.path.join(self.proj, 'server_data', 'backups', self.backups()[0]))),
                         ['kakao.db'])                                         # 토큰 파일은 백업하지 않는다
        order = [out.index(mark) for mark in ('== 배포 전 점검', '== 1 백업', '== 2 코드 받기', '== 3 의존성',
                                              '== 4 다시 시작', '== 5 다시 시작한 뒤 확인', '== 결과')]
        self.assertEqual(order, sorted(order))
        for expected in (f'{self.first[:7]} → {second[:7]}', '서버용 패키지 변화 없음', '✓ ping → pong', '✓ 등록 닫힘',
                         '✓ pm2 프로세스', '실명으로 되돌린 표시 이름 — 2개', '모두 통과', '사람이 확인할 것'):
            self.assertIn(expected, out)
        self.assertIn(self.backups()[0], out)
        self.assertNotIn('TOP-SECRET-TOKEN', out)
        self.assertNotIn('되돌리는 방법', out)

    def test_apply_with_nothing_new_still_restarts(self):
        code, out = self.deploy('--apply')
        self.assertEqual(code, 0, out)
        self.assertIn('새 커밋이 없습니다', out)
        self.assertIn('startOrReload', self.pm2_calls())

    def test_duplicate_login_ids_stop_before_anything_changes(self):
        self.upstream_commit()
        with self.store._lock:
            self.store._db.execute("INSERT INTO users(username, display_name, created_at, email) "
                                   "VALUES ('라마바', '라마바', '2026-11-17T10:00:00', 'GANA@kcs.test')")
            self.store._db.commit()
        for args in ((), ('--apply',)):
            code, out = self.deploy(*args)
            self.assertEqual(code, 1, out)
            self.assertIn('겹치는 로그인 아이디가 있습니다', out)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.first)
        self.assertEqual(self.pm2_calls(), [])
        self.assertEqual(self.backups(), [])

    def test_uncommitted_changes_stop_the_deploy(self):
        self.upstream_commit()
        with open(os.path.join(self.proj, 'kcs.py'), 'a') as f:
            f.write('# local edit\n')
        code, out = self.deploy('--apply')
        self.assertEqual(code, 1, out)
        self.assertIn('커밋하지 않은 수정', out)
        self.assertEqual((self.git('rev-parse', 'HEAD'), self.pm2_calls(), self.backups()), (self.first, [], []))

    def test_diverged_branch_is_not_pulled(self):
        self.upstream_commit()
        with open(os.path.join(self.proj, 'LOCAL.txt'), 'w') as f:
            f.write('local\n')
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'local only')
        local = self.git('rev-parse', 'HEAD')
        code, out = self.deploy('--apply')
        self.assertEqual(code, 1, out)
        self.assertIn('갈라져', out)
        self.assertEqual((self.git('rev-parse', 'HEAD'), self.pm2_calls()), (local, []))

    def test_failed_check_after_restart_shows_how_to_go_back(self):
        second = self.upstream_commit()
        with open(self.log, 'a', encoding='utf-8') as f:
            f.write('Traceback (most recent call last):\n  File "x", line 1\nRuntimeError: boom\n')
        code, out = self.deploy('--apply', FAKE_PM2_STATUS='errored')
        self.assertEqual(code, 1, out)
        self.assertEqual(self.git('rev-parse', 'HEAD'), second)                 # 자동으로 되돌리지 않는다
        for expected in ('✗ pm2 프로세스', '✗ 시작 뒤 오류 기록', '되돌리는 방법', f'checkout {self.first}',
                         '데이터까지 되돌리기', os.path.join('server_data', 'backups', self.backups()[0])):
            self.assertIn(expected, out)

    def test_server_not_answering_is_a_failure(self):
        code, out = self.deploy('--apply', '--port', '1')                      # 아무도 듣지 않는 포트
        self.assertEqual(code, 1, out)
        self.assertIn('✗ 서버 접속', out)
        self.assertIn('되돌리는 방법', out)

    def test_restart_failure_stops_and_explains(self):
        self.upstream_commit()
        code, out = self.deploy('--apply', FAKE_PM2_FAIL='1')
        self.assertEqual(code, 1, out)
        self.assertIn('pm2 다시 시작에 실패', out)
        self.assertIn('되돌리는 방법', out)
        self.assertEqual(self.pm2_calls(), ['startOrReload'])

    def test_server_packages_are_noticed_when_they_change(self):
        self.upstream_commit('requirements-kcs.txt', 'newpkg>=1.0          # 서버: 시험용 패키지\n', 'deps')
        code, out = self.deploy('--apply')
        self.assertEqual(code, 0, out)
        self.assertIn('서버용 패키지가 바뀌었지만 .venv 가 없어', out)
        self.assertIn('newpkg>=1.0', out)
        self.assertNotIn('PyQt5', out)                                         # 화면용 패키지는 서버에 설치하지 않는다

    def test_open_registration_setting_is_respected(self):
        self.write_ecosystem(self.server.port, ' --open-registration')
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'open')
        self.git('push', '--quiet', 'origin', 'main')
        code, out = self.deploy('--apply')
        self.assertEqual(code, 1, out)                                         # 설정은 열림인데 떠 있는 서버는 닫힘
        self.assertIn('✗ 등록 열림', out)

    def test_wrong_folder_and_options(self):
        elsewhere = tempfile.mkdtemp(prefix='kcs_else_')
        self.addCleanup(shutil.rmtree, elsewhere, True)
        result = subprocess.run(['bash', os.path.join(self.proj, 'scripts', 'deploy_server.sh')], cwd=elsewhere,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('프로젝트 폴더에서 실행', result.stderr)
        self.assertEqual(self.deploy('--nope')[0], 2)
        self.assertEqual(self.deploy('--db', 'missing.db')[0], 2)
        code, out = self.deploy('--help')
        self.assertEqual(code, 0)
        self.assertIn('--apply', out)
