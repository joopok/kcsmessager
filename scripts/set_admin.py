# -*- coding: utf-8 -*-
"""
관리자용: 관리자 권한 지정/해제 (명세 §4.2)

    python3 scripts/set_admin.py KCS관리자           # 그룹 관리자로 지정 (모든 계열사)
    python3 scripts/set_admin.py kmkim --company 케이씨에스   # 회사 관리자 (그 회사 구성원만 관리, 명세 §4.3)
    python3 scripts/set_admin.py kmkim --off         # 관리자 해제 (마지막 관리자는 해제할 수 없다)
    python3 scripts/set_admin.py --list              # 관리자 목록
    python3 scripts/set_admin.py 김명수 --db server_data/kakao.db

아이디는 사번, 사용자 이름, 이메일 또는 사내 계정. 서버가 켜져 있어도 된다(SQLite) - 이미 있는 DB 의 구조 · 조직도 시드는 건드리지 않는다.
접속 중인 사람의 화면(더보기 → 관리자 메뉴)은 다시 로그인하면 바뀐다.
"""

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.chat.chat_server import DEFAULT_DB_PATH  # noqa: E402
from server.kakao.store import Store  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='KCS Talk 관리자 권한 지정/해제')
    parser.add_argument('login_id', nargs='?', help='사번, 사용자 이름, 이메일 또는 사내 계정')
    parser.add_argument('--off', action='store_true', help='관리자 권한을 해제한다')
    parser.add_argument('--company', default='', help='회사 관리자로 지정할 때 그 회사 이름 (없으면 그룹 관리자)')
    parser.add_argument('--list', action='store_true', help='관리자 목록을 보여 준다')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help=f'SQLite DB 경로 (기본: {DEFAULT_DB_PATH})')
    args = parser.parse_args(argv)
    if not args.list and not args.login_id:
        parser.error('아이디를 입력하거나 --list 를 쓰세요.')

    store = Store.open_for_tools(args.db)
    try:
        if args.list:
            admins = [u['username'] + (f" ({u['admin_company']})" if u['admin_company'] else ' (그룹)')
                      for u in store.admin_user_list() if u['is_admin']]
            print('관리자: ' + (', '.join(admins) if admins else '(없음)'))
            return 0
        username = store.resolve_login(args.login_id)
        if username is None or store.is_bot(username):
            print(f'사용자를 찾을 수 없습니다: {args.login_id}', file=sys.stderr)
            return 1
        if args.company and args.company not in store.company_names():
            print(f'회사를 찾을 수 없습니다: {args.company}', file=sys.stderr)
            return 1
        try:
            store.set_admin(username, not args.off, args.company)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        scope = '' if args.off else f" ({args.company or '그룹 전체'})"
        print(f"{username}: 관리자 {'해제' if args.off else '지정'}했습니다{scope}.")
        return 0
    except sqlite3.OperationalError as exc:         # 예전 버전 DB - 구조는 서버가 시작할 때 바꾼다
        print(f'{Store.TOOL_NEEDS_RESTART} ({exc})', file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == '__main__':
    sys.exit(main())
