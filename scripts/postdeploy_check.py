# -*- coding: utf-8 -*-
"""
배포 뒤 확인 - 계정 없이 할 수 있는 것만 (scripts/deploy_server.sh 가 부른다. 따로 실행해도 된다)

    python3 scripts/postdeploy_check.py --port 9551 --proxy-protocol        # 서버에 접속해 확인
    pm2 jlist | python3 scripts/postdeploy_check.py --pm2-status kcs-talk-server
    pm2 logs kcs-talk-server --lines 300 --nostream | python3 scripts/postdeploy_check.py --read-log

접속 확인 (로그인하지 않는다)
  · 접속이 된다
  · ping 에 pong 이 온다 (2026-11-17 이후 서버)
  · 로그인 전 org_companies 응답의 open_registration 이 기대한 값이다 (기본: 닫힘 = false)
종료 코드: 0 모두 통과 · 1 하나라도 실패
"""

import argparse
import json
import re
import socket
import sys
import time

START_MARK = 'KCS Messenger 서버 시작'
STOP_MARK = 'KCS Messenger 서버 종료'
RENAMED_RE = re.compile(r'표시 이름 (\d+)개를')
POLICY_RE = re.compile(r'파일 전송 보안: 새 기본 차단 형태 (\d+)개')
ERROR_RE = re.compile(r'Traceback|\bERROR\b|\bCRITICAL\b')


def _read_reply(sock, wanted, req_id, deadline):
    """req_id 가 맞는 줄이 올 때까지 읽는다 (다른 알림은 지나친다) → 그 줄, 종류가 다르면 {'_unexpected': 줄}, 없으면 None"""
    buf = b''
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            try:
                frame = json.loads(line.decode('utf-8'))
            except ValueError:
                continue
            if isinstance(frame, dict) and frame.get('req_id') == req_id:
                return frame if frame.get('type') == wanted else {'_unexpected': frame}
    return None


def check_server(host, port, proxy_protocol=False, expect_closed=True, timeout=5.0, wait=0.0):
    """→ [(이름, 통과?, 설명)]. wait 초 동안은 접속될 때까지 다시 시도한다 (서버가 막 켜지는 중일 수 있다)"""
    results = []
    sock = None
    started = time.monotonic()
    error = ''
    while sock is None:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            error = str(exc)
            if time.monotonic() - started >= wait:
                break
            time.sleep(0.5)
    if sock is None:
        return [('서버 접속', False, f'{host}:{port} 에 접속할 수 없습니다 ({error})')]
    results.append(('서버 접속', True, f'{host}:{port}'))
    try:
        if proxy_protocol:          # nginx 가 붙이는 첫 줄을 흉내 낸다 (이 PC 에서 온 접속으로 보이게)
            sock.sendall(f'PROXY TCP4 127.0.0.1 127.0.0.1 {sock.getsockname()[1]} {port}\r\n'.encode('ascii'))
        sock.sendall(b'{"type": "ping", "req_id": "deploy-ping"}\n')
        pong = _read_reply(sock, 'pong', 'deploy-ping', time.monotonic() + timeout)
        answered = bool(pong) and '_unexpected' not in pong
        results.append(('ping → pong', answered,
                        '응답함' if answered else '응답이 없습니다 - 예전 버전 서버가 돌고 있을 수 있습니다'))
        sock.sendall(b'{"type": "org_companies", "req_id": "deploy-org"}\n')
        org = _read_reply(sock, 'org_companies', 'deploy-org', time.monotonic() + timeout)
        if not org or '_unexpected' in org:
            results.append(('로그인 전 회사 목록', False, '응답이 없습니다'))
        else:
            companies = org.get('companies') or []
            results.append(('로그인 전 회사 목록', bool(companies), f'회사 {len(companies)}곳'))
            opened = org.get('open_registration')
            label = '등록 닫힘' if expect_closed else '등록 열림'
            if opened is None:
                results.append((label, False, 'open_registration 값이 없습니다 - 예전 버전 서버가 돌고 있을 수 있습니다'))
            else:
                results.append((label, opened is (not expect_closed), f'open_registration={str(opened).lower()}'))
        try:
            sock.sendall(b'{"type": "leave"}\n')
        except OSError:
            pass
    except OSError as exc:
        results.append(('서버 응답', False, f'연결 오류: {exc}'))
    finally:
        sock.close()
    return results


def pm2_status(listing, name):
    """pm2 jlist 출력(JSON)에서 name 프로세스 → [(이름, 통과?, 설명)]"""
    try:
        processes = json.loads(listing)
    except ValueError:
        return [('pm2 프로세스', False, 'pm2 jlist 출력을 읽을 수 없습니다')]
    for proc in processes if isinstance(processes, list) else []:
        if isinstance(proc, dict) and proc.get('name') == name:
            env = proc.get('pm2_env') or {}
            status = env.get('status', '?')
            return [('pm2 프로세스', status == 'online', f"{name}: {status} (다시 시작 {env.get('restart_time', '?')}번)")]
    return [('pm2 프로세스', False, f'{name} 이(가) pm2 목록에 없습니다')]


def summarize_log(text):
    """서버 로그(끝부분)에서 마지막 시작 뒤의 상태 → [(이름, 통과?, 설명)]"""
    lines = text.splitlines()
    last_start = max((i for i, line in enumerate(lines) if START_MARK in line), default=-1)
    if last_start < 0:
        return [('서버 시작 기록', False, '로그 끝부분에 시작 기록이 없습니다 - pm2 logs 로 직접 확인해 주세요')]
    results = [('서버 시작 기록', True, '있음')]
    renamed = policy = 0
    for line in reversed(lines[max(0, last_start - 50):last_start]):      # 시작할 때의 정리 기록은 시작 기록 바로 앞에 찍힌다
        if START_MARK in line or STOP_MARK in line:
            break
        found = RENAMED_RE.search(line)
        if found and not renamed:
            renamed = int(found.group(1))
        found = POLICY_RE.search(line)
        if found and not policy:
            policy = int(found.group(1))
    results.append(('실명으로 되돌린 표시 이름', True, f'{renamed}개'))
    results.append(('저장된 파일 정책에 더한 기본 차단 형태', True, f'{policy}개'))
    errors = [line.strip()[:200] for line in lines[last_start + 1:] if ERROR_RE.search(line)]
    detail = '없음' if not errors else f'{len(errors)}줄: ' + ' / '.join(errors[:3])
    results.append(('시작 뒤 오류 기록', not errors, detail))
    return results


def show(results, out=sys.stdout):
    for name, ok, detail in results:
        print(f"  {'✓' if ok else '✗'} {name} — {detail}", file=out)
    return 0 if all(ok for _name, ok, _detail in results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='배포 뒤 확인 (계정 없이)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, help='서버가 듣는 포트')
    parser.add_argument('--proxy-protocol', action='store_true', help='서버가 --proxy-protocol 로 실행 중이면')
    parser.add_argument('--expect-open', action='store_true', help='등록이 열려 있어야 정상인 서버 (--open-registration)')
    parser.add_argument('--wait', type=float, default=0.0, help='접속될 때까지 기다리는 시간(초)')
    parser.add_argument('--pm2-status', metavar='NAME', help='표준 입력의 pm2 jlist 출력에서 NAME 의 상태를 본다')
    parser.add_argument('--read-log', action='store_true', help='표준 입력의 서버 로그 끝부분을 본다')
    args = parser.parse_args(argv)
    if args.pm2_status:
        return show(pm2_status(sys.stdin.read(), args.pm2_status))
    if args.read_log:
        return show(summarize_log(sys.stdin.read()))
    if not args.port:
        parser.error('--port 가 필요합니다')
    return show(check_server(args.host, args.port, args.proxy_protocol, not args.expect_open, wait=args.wait))


if __name__ == '__main__':
    sys.exit(main())
