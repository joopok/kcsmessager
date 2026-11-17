# -*- coding: utf-8 -*-
"""
배포 전 점검: 운영 DB 를 **읽기 전용**으로 열어, 새 서버로 다시 시작하기 전에 알아야 할 것을 보여 준다.
DB 는 고치지 않는다. 서버가 켜져 있어도 된다.

    python3 scripts/predeploy_check.py server_data/kakao.db
    python3 scripts/predeploy_check.py server_data/kakao.db --names     # 해당 계정 이름까지

보는 것 (명세 §4.1 · §4.2 · §4.5)
  1. 로그인 아이디(이름 · 사번 · 사내 계정 · 이메일)가 서로 겹치는 계정 - 겹치면 한쪽이 로그인하지 못한다
  2. 표시 이름이 실명과 달라 재시작 때 실명으로 돌아갈 계정 · 멀티프로필
  3. 비밀번호를 등록하지 않은 계정 (등록이 닫히면 관리자 등록 코드나 초기 비밀번호가 필요하다)
  4. 관리자 계정, 삭제됐는데 관리자 표시가 남은 계정
  5. 그 밖에 재시작 때 바뀌는 것 안내

종료 코드: 0 문제 없음 · 1 먼저 고쳐야 할 것이 있음(겹치는 로그인 아이디) · 2 DB 를 열 수 없음
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

LOGIN_ID_COLUMNS = (('username', '이름'), ('employee_no', '사번'), ('account', '사내 계정'), ('email', '이메일'))
NAME_LIMIT = 30          # --names 로 보여 주는 이름 수


def open_read_only(path):
    """읽기 전용 연결 (쓰기 시도는 sqlite 가 거절한다)"""
    db = sqlite3.connect(Path(os.path.abspath(path)).as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def columns(db, table):
    return {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}


def duplicate_login_ids(users):
    """[(겹치는 값, [(이름, 어느 칸)])] - 서로 다른 계정이 같은 값을 쓰는 것만"""
    owners = {}
    for user in users:
        for column, label in LOGIN_ID_COLUMNS:
            value = (user.get(column) or '').strip()
            if value:
                owners.setdefault(value.lower(), []).append((user['username'], label))
    return sorted((value, sorted(set(holders))) for value, holders in owners.items()
                  if len({name for name, _label in holders}) > 1)


def collect(db):
    """점검 결과 dict (출력과 테스트가 함께 쓴다)"""
    have = columns(db, 'users')
    if not have:
        raise sqlite3.DatabaseError('users 표가 없습니다 - KCS Talk DB 가 아닙니다.')
    wanted = ['username', 'display_name', 'employee_no', 'account', 'email', 'password_hash', 'is_bot', 'is_admin',
              'admin_company', 'removed', 'disabled']
    select = ', '.join(c if c in have else f"NULL AS {c}" for c in wanted)
    users = [dict(row) for row in db.execute(f'SELECT {select} FROM users')]
    people = [u for u in users if not u['is_bot']]
    active = [u for u in people if not u['removed']]
    unregistered = [u for u in active if not u['password_hash']]
    multi = []
    if columns(db, 'multi_profiles'):
        multi = [dict(row) for row in db.execute(
            'SELECT owner, display_name FROM multi_profiles WHERE display_name<>owner')]
    members = columns(db, 'room_members')
    return {
        'missing_columns': sorted(c for c in wanted if c not in have),
        'users': len(active),
        'duplicates': duplicate_login_ids(people),
        'renamed': sorted(u['username'] for u in people if (u['display_name'] or '') != u['username']),
        'renamed_profiles': sorted({m['owner'] for m in multi}),
        'renamed_profile_count': len(multi),
        'unregistered': sorted(u['username'] for u in unregistered),
        'unregistered_without_email': sorted(u['username'] for u in unregistered if not (u['email'] or '').strip()),
        'group_admins': sorted(u['username'] for u in active if u['is_admin'] and not (u['admin_company'] or '')),
        'company_admins': sorted(f"{u['username']} ({u['admin_company']})" for u in active
                                 if u['is_admin'] and (u['admin_company'] or '')),
        'removed_admins': sorted(u['username'] for u in people if u['removed'] and u['is_admin']),
        'late_joiner_rule_ready': 'since_msg_id' in members,
    }


def _names(items, show):
    if not show or not items:
        return ''
    more = f' 외 {len(items) - NAME_LIMIT}' if len(items) > NAME_LIMIT else ''
    return '\n      ' + ', '.join(items[:NAME_LIMIT]) + more


def report(result, show_names=False, out=sys.stdout):
    def say(text=''):
        print(text, file=out)
    say(f"계정 {result['users']}개 (봇 · 삭제된 구성원 제외)")
    if result['missing_columns']:
        say(f"  · 예전 버전 DB 입니다 (없는 칸: {', '.join(result['missing_columns'])}) - 새 서버가 시작할 때 추가합니다.")
    say()
    say('1. 겹치는 로그인 아이디 (이름 · 사번 · 사내 계정 · 이메일)')
    if result['duplicates']:
        say(f"  ✗ {len(result['duplicates'])}건 - 다시 시작하기 전에 관리자 화면에서 한쪽 값을 고쳐 주세요.")
        for value, holders in result['duplicates'][:NAME_LIMIT]:
            say(f"      {value}: " + ', '.join(f'{name}의 {label}' for name, label in holders))
    else:
        say('  ✓ 없음')
    say()
    say('2. 다시 시작하면 실명으로 돌아가는 표시 이름')
    say(f"  · 프로필 {len(result['renamed'])}개{_names(result['renamed'], show_names)}")
    say(f"  · 멀티프로필 {result['renamed_profile_count']}개 (계정 {len(result['renamed_profiles'])}개)"
        f"{_names(result['renamed_profiles'], show_names)}")
    say()
    say('3. 비밀번호를 등록하지 않은 계정')
    say(f"  · {len(result['unregistered'])}개{_names(result['unregistered'], show_names)}")
    say(f"  · 그중 이메일이 비어 있는 계정 {len(result['unregistered_without_email'])}개"
        f"{_names(result['unregistered_without_email'], show_names)}")
    if result['unregistered']:
        say('    등록이 닫히면 이 계정들은 관리자가 등록 코드를 발급하거나 초기 비밀번호를 정해 줘야 시작할 수 있습니다.')
    say()
    say('4. 관리자')
    say(f"  · 그룹 관리자 {len(result['group_admins'])}명: {', '.join(result['group_admins']) or '(없음)'}")
    say(f"  · 회사 관리자 {len(result['company_admins'])}명: {', '.join(result['company_admins']) or '(없음)'}")
    if not result['group_admins']:
        say('  ✗ 그룹 관리자가 없습니다 - org.json 의 admins 또는 scripts/set_admin.py 로 지정해 주세요.')
    if result['removed_admins']:
        say(f"  · 삭제됐는데 관리자 표시가 남은 계정 {len(result['removed_admins'])}개: "
            f"{', '.join(result['removed_admins'])}")
        say('    새 서버에서는 회사 관리자가 이 계정을 건드릴 수 없고, 그룹 관리자가 복원하면 관리자 권한이 그대로입니다.')
        say('    필요 없으면 복원하기 전에 scripts/set_admin.py <아이디> --off 로 내려 주세요.')
    say()
    say('5. 다시 시작하면 바뀌는 것')
    say('  · 기존 비밀번호는 4~7자여도 그대로 로그인됩니다. 새로 정하는 비밀번호만 8자 이상입니다.')
    say('    (비밀번호는 해시로만 저장되어 길이를 알 수 없으므로 짧은 비밀번호 사용자 수는 셀 수 없습니다.)')
    say('  · 등록이 닫힙니다: 조직도에 없는 이름으로 계정이 만들어지지 않습니다.')
    if not result['late_joiner_rule_ready']:
        say('  · 지금 방에 있는 사람은 예전 대화를 계속 봅니다. 다시 시작한 뒤 초대받는 사람부터 그 뒤 대화만 봅니다.')
    return 1 if result['duplicates'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='배포 전 점검 (DB 읽기 전용)')
    parser.add_argument('db', help='SQLite DB 경로 (예: server_data/kakao.db)')
    parser.add_argument('--names', action='store_true', help='해당 계정 이름까지 보여 준다')
    args = parser.parse_args(argv)
    if not os.path.isfile(args.db):
        print(f'DB 파일이 없습니다: {args.db}', file=sys.stderr)
        return 2
    try:
        db = open_read_only(args.db)
        try:
            result = collect(db)
        finally:
            db.close()
    except sqlite3.Error as exc:
        print(f'DB 를 읽을 수 없습니다: {exc}', file=sys.stderr)
        return 2
    print(f'배포 전 점검: {os.path.abspath(args.db)} (읽기 전용)')
    return report(result, show_names=args.names)


if __name__ == '__main__':
    sys.exit(main())
