# -*- coding: utf-8 -*-
"""
관리자용: 사용자 비밀번호 설정/초기화 (명세 §4.1)

    python3 scripts/set_password.py 김명수            # 새 비밀번호를 물어본다
    python3 scripts/set_password.py kmkim --clear     # 비밀번호 삭제 → 다음에 [처음 사용 등록]으로 다시 등록
    python3 scripts/set_password.py 김명수 --db server_data/kakao.db

아이디는 사용자 이름, 이메일 또는 사내 계정. 저장된 자동 로그인 토큰도 모두 폐기한다.
새 비밀번호는 서버 규칙(운영 8~64자)을 따른다 - 시험용 서버는 --allow-short 로 4자부터.
서버가 켜져 있어도 된다(SQLite) - 이미 있는 DB 의 구조 · 조직도 시드는 건드리지 않는다.
"""

import argparse
import getpass
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.chat.chat_server import DEFAULT_DB_PATH  # noqa: E402
from server.kakao.store import Store  # noqa: E402


PASSWORD_MIN, PASSWORD_MIN_SHORT, PASSWORD_MAX = 8, 4, 64     # 서버의 새 비밀번호 규칙 (명세 §4.1)


def main(argv=None):
    parser = argparse.ArgumentParser(description='KCS Talk 사용자 비밀번호 설정/초기화')
    parser.add_argument('login_id', help='사용자 이름, 이메일 또는 사내 계정')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help=f'SQLite DB 경로 (기본: {DEFAULT_DB_PATH})')
    parser.add_argument('--clear', action='store_true', help='비밀번호를 지워 [처음 사용 등록]을 다시 하게 한다')
    parser.add_argument('--password', help='새 비밀번호 (생략하면 입력 받음)')
    parser.add_argument('--allow-short', action='store_true',
                        help=f'시험용 서버: {PASSWORD_MIN_SHORT}자부터 허용 (기본 {PASSWORD_MIN}자 - 운영 서버 규칙)')
    args = parser.parse_args(argv)
    minimum = PASSWORD_MIN_SHORT if args.allow_short else PASSWORD_MIN

    store = Store.open_for_tools(args.db)
    try:
        username = store.resolve_login(args.login_id)
        if username is None:
            print(f'사용자를 찾을 수 없습니다: {args.login_id}', file=sys.stderr)
            return 1
        if args.clear:
            store.clear_password(username)
            print(f'{username}: 비밀번호를 지웠습니다. 로그인 창의 [처음 사용 등록]으로 다시 등록하면 됩니다.')
            return 0
        password = args.password or getpass.getpass('새 비밀번호: ')
        if not minimum <= len(password) <= PASSWORD_MAX:
            print(f'비밀번호는 {minimum}~{PASSWORD_MAX}자로 입력해 주세요.', file=sys.stderr)
            return 1
        if args.password is None and getpass.getpass('한 번 더: ') != password:
            print('두 비밀번호가 다릅니다.', file=sys.stderr)
            return 1
        store.set_password(username, password)
        store.revoke_all_login_tokens(username)
        print(f'{username}: 비밀번호를 바꿨습니다. 저장된 자동 로그인은 모두 해제되었습니다.')
        return 0
    except sqlite3.OperationalError as exc:         # 예전 버전 DB - 구조는 서버가 시작할 때 바꾼다
        print(f'{Store.TOOL_NEEDS_RESTART} ({exc})', file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == '__main__':
    sys.exit(main())
