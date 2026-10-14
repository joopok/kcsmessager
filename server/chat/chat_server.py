#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
KCS Messenger 서버 진입점.

실제 구현은 ``server/kakao`` 패키지(KakaoServer)에 있으며, 구버전 클라이언트
(join / message / all_users_list / user_status_changed)와도 호환된다.

사용법:
    python server/chat/chat_server.py [--host 127.0.0.1] [--port 5555] [--db server_data/kakao.db] [--retention-days 90] [--no-portal] [--proxy-protocol]
"""

import argparse
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from server.kakao.portal import load_config as load_portal_config  # noqa: E402
from server.kakao.server import DEFAULT_DB_PATH, KakaoServer  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='KCS Messenger 채팅 서버')
    parser.add_argument('--host', default='127.0.0.1', help='바인드 주소 (기본: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5555, help='포트 (기본: 5555)')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help=f'SQLite DB 경로 (기본: {DEFAULT_DB_PATH})')
    parser.add_argument('--no-briefing', action='store_true', help='AI 비서의 오늘의 브리핑(선톡) 끄기')
    parser.add_argument('--no-link-preview', action='store_true', help='URL 미리보기 끄기')
    parser.add_argument('--allow-passwordless', action='store_true',
                        help='비밀번호 없는 구버전 클라이언트 로그인 허용 (기본: 비밀번호 필수)')
    parser.add_argument('--no-portal', action='store_true',
                        help='사내 포털(portal.kcins.co.kr) RAG 연동 끄기 - AI 비서가 메신저 자료로만 답함')
    parser.add_argument('--portal-config', default=None,
                        help='포털 연동 설정 파일 (기본: DB 폴더의 portal.json, 없으면 기본값 + 환경변수)')
    parser.add_argument('--proxy-protocol', action='store_true',
                        help='연결 첫 줄을 PROXY protocol v1 로 읽어 실제 클라이언트 IP 복원 (nginx stream 뒤에서 실행할 때)')
    parser.add_argument('--retention-days', type=float, default=90,
                        help='받은 파일(첨부) 보관 기간, 일 단위 (기본: 90)')
    return parser.parse_args(argv)


def main(argv=None):
    """채팅 서버 실행 (Ctrl+C 로 종료)."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='[SERVER] %(asctime)s %(levelname)s %(message)s')
    portal = None
    if not args.no_portal:
        portal = load_portal_config(args.portal_config or
                                    os.path.join(os.path.dirname(os.path.abspath(args.db)), 'portal.json'))
    server = KakaoServer(host=args.host, port=args.port, db_path=args.db,
                         daily_briefing=not args.no_briefing,
                         link_preview=False if args.no_link_preview else None,
                         retention_days=args.retention_days,
                         require_password=not args.allow_passwordless,
                         portal=portal, proxy_protocol=args.proxy_protocol)
    try:
        server.start()
    except KeyboardInterrupt:
        print('\n[SERVER] Shutting down...')
        server.stop()


if __name__ == '__main__':
    main()
