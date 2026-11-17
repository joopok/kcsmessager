# -*- coding: utf-8 -*-
"""
기능별 검증 - 전체 unittest(tests/test_*.py)를 화면 없이(offscreen) 실행하고
테스트를 기능 이름에 대응시켜 기능별 PASS/FAIL 표를 출력·저장한다.

    python3 scripts/verify_features.py            # 표 출력 + claudedocs/FEATURE_VERIFICATION.md 작성
    python3 scripts/verify_features.py -q         # 진행 표시 없이

기능 이름은 client/kakao/feature_tour.py 의 FEATURES 와 같다(기능 둘러보기 '검증 결과' 탭이 이 파일을 읽는다).
테스트 → 기능 대응은 아래 MAPPING 표(정규식, 먼저 맞는 것)로 관리한다. 맞는 것이 없으면 '기타'.
실패한 기능이 있으면 종료 코드 1.
"""

import argparse
import io
import os
import re
import sys
import time
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('KCS_LINK_PREVIEW', 'off')

OUTPUT = os.path.join(ROOT, 'claudedocs', 'FEATURE_VERIFICATION.md')
OTHER = '기타'

# (테스트 id 정규식 'module.Class.method', 기능 key) - 위에서부터 먼저 맞는 것
MAPPING = [
    (r'test_kakao_portal\.', 'ai_chat'),          # 사내 포털 RAG 연동 (명세 §7.6.1)
    (r'test_kakao_registration', 'login'),        # 등록 방식 - 열림/닫힘 · 등록 코드 (명세 §4.1)
    (r'test_kakao_admin_tools', 'admin'),         # 관리자 도구는 가동 중인 DB 의 구조 · 시드를 건드리지 않는다 (§4.2)
    (r'test_kakao_deploy_tools', 'platforms'),    # 배포 전 점검 · 백업 (docs/DEPLOY_CHECKLIST.md)
    (r'test_kakao_login_throttle', 'login'),      # 로그인 실패 제한 · 오류 구분 코드 (명세 §4.1)
    (r'test_kakao_relay_rules\.(Preview)', 'link_preview'),           # 미리보기 작업 · 태그 읽기
    (r'test_kakao_relay_rules\.ApprovalDelivery', 'file_security'),   # 승인한 파일을 못 보냈을 때
    (r'test_kakao_relay_rules', 'platforms'),     # 요청 값 공통 규칙 (명세 §4.5)
    (r'test_kakao_connection_limits', 'platforms'),   # 연결 한도 · 하트비트 · 요청 빈도 (명세 §2)
    (r'test_kakao_send_queue', 'platforms'),      # 연결별 보내기 대기열 (명세 §2)
    (r'test_kakao_read_connection', 'search'),    # 무거운 조회는 읽기 전용 두 번째 연결에서 (명세 §5)
    (r'test_kakao_room_list\.RoomList', 'room_list'),      # 방 목록 묶음 조회
    (r'test_kakao_room_list\.ReminderScan', 'calendar'),   # 일정 알림 검사 범위 · 색인
    (r'test_kakao_rag_workers', 'ai_chat'),       # AI 비서 작업 처리 (명세 §4.5)
    (r'test_kakao_final_rules\.ArchiveCheck', 'file_security'),      # 압축 파일 검사 보강 (§10.10)
    (r'test_kakao_final_rules\.PreviewAddress', 'link_preview'),     # 미리보기 접속 주소
    (r'test_kakao_final_rules\.AiScope', 'ai_chat'),                 # AI 비서 근거 범위 (§7.6)
    (r'test_kakao_final_rules\.(PasswordRule|OpenServerPassword)', 'login'),   # 비밀번호 길이 · 변경 (§4.1)
    (r'test_kakao_final_rules', 'platforms'),     # 이름 · 파일 이름 · 첨부 정리 · 용량 (§4.5)
    (r'test_kakao_decided_rules\.(LateJoiner|RagScope)', 'group'),   # 나중에 들어온 사람이 보는 범위 (§4.5)
    (r'test_kakao_decided_rules\.(FixedName|OpenServerName)', 'profile_edit'),   # 표시 이름은 실명으로 고정 (§4.5)
    (r'test_kakao_decided_rules\.PreviewPublicOnly', 'link_preview'),      # 미리보기는 공인 주소만 (§4.5)
    (r'test_kakao_login_ids\.(RemovedAdmin|SeedAdminOnce)', 'admin'),   # 삭제한 관리자 · org.json admins (§4.2 · §4.3)
    (r'test_kakao_login_ids', 'login'),           # 로그인 아이디 중복 금지 · 이메일 관리 (명세 §4.1)
    # 서버 규칙 개정(2026-11-17)에 맞춘 클라이언트 동작 (test_kakao_server_sync)
    (r'test_kakao_server_sync\.(AdminCode)', 'admin'),
    (r'test_kakao_server_sync\.ServerPolicy', 'profile_edit'),
    (r'test_kakao_server_sync\.UnreadCount', 'read_count'),
    (r'test_kakao_server_sync\.AttachmentKind', 'attach'),
    (r'test_kakao_server_sync\.', 'login'),
    # 전체 점검(2026-11-17) 뒤 고친 클라이언트 안정성 문제 (test_kakao_stability)
    (r'test_kakao_stability\.(ReconnectBackoff|HistoryAfterReconnect|ReconnectHistoryOrder|ResponsiveConnection|SingleInstance)', 'login'),
    (r'test_kakao_stability\.ActiveRoom', 'notifications'),
    (r'test_kakao_stability\.(ExportParts|ExportTimeoutWatch|AttachmentCollectorTimeout)', 'chat_export'),
    (r'test_kakao_stability\.(TabRefresh|FriendsRowUpdate|HiddenTab)', 'friends'),
    (r'test_kakao_stability\.(RowListSync|ChatsRowUpdate)', 'room_list'),
    (r'test_kakao_stability\.OrgRowUpdate', 'org_chart'),
    (r'test_kakao_stability\.MessageBoxWithoutScreen', 'platforms'),
    (r'test_kakao_stability\.(ImageThumb|AttachmentCache|PastedImage)', 'attach'),
    (r'test_kakao_stability\.CallDisconnect', 'voice_call'),
    (r'test_kakao_stability\.ProfileEditSize', 'profile_edit'),
    (r'test_kakao_stability\.LockHides', 'lock'),
    (r'test_kakao_stability\.SafeGarbageCollection', 'platforms'),   # 메모리 정리(GC)는 이벤트 사이에서만
    # 전체 점검(2026-11-17) 뒤 강화한 클라이언트 규칙 (test_kakao_hardening)
    (r'test_kakao_hardening\.ImageDecode', 'attach'),
    (r'test_kakao_hardening\.AudioDecode', 'voice_call'),
    (r'test_kakao_hardening\.(ReadLoop|BeforeLogin|PlainConnectionNotice)', 'login'),
    (r'test_kakao_hardening\.LockPin', 'lock'),
    (r'test_kakao_hardening\.SecretPeerKey', 'secret'),
    (r'test_kakao_hardening\.CsvExport', 'chat_export'),
    (r'test_kakao_hardening\.(PlainTextGuard|ScreensShow)', 'platforms'),
    (r'test_kakao_hardening\.', 'file_security'),
    (r'test_kakao_file_security|test_kakao_bugfixes\.FilePolicyBypass', 'file_security'),
    (r'test_kakao_bugfixes\.(ServerInput|StoreRollback|CrashGuard)', 'platforms'),
    (r'test_kakao_bugfixes\.(Connection|SessionReconnect|AccountSecurity)', 'login'),
    (r'test_kakao_bugfixes\.ScheduledFailure', 'scheduled'),
    (r'test_kakao_bugfixes\.SecretKey', 'secret'),
    (r'test_kakao_bugfixes\.LinkPreviewGuard', 'link_preview'),
    (r'test_kakao_bugfixes\.StateTests\.test_older_history', 'thread'),
    (r'test_kakao_bugfixes\.StateTests\.test_pending', 'direct'),
    (r'test_kakao_bugfixes\.StateTests\.test_failed', 'failed_resend'),
    (r'test_kakao_bugfixes\.StateTests\.test_unreadable|test_kakao_bugfixes\.WidgetTests\.test_image', 'attach'),
    (r'test_kakao_bugfixes\.WidgetTests\.test_export', 'chat_export'),
    (r'test_kakao_bugfixes\.WidgetTests\.test_recording', 'voice_call'),
    (r'test_kakao_bugfixes\.WidgetTests\.test_reopened', 'multi_profile'),
    (r'test_kakao_bugfixes\.WidgetTests\.test_member_edit', 'admin'),
    (r'test_kakao_admin|test_kakao_member_admin|test_kakao_member_edit_mode|test_kakao_server_address|disabled_by_admin', 'admin'),
    (r'(?i)Login(Flow)?Tests|login_dialog|logout|auto_login|fresh_login|password_reset|SavedLoginDialog|LoginHardening|RequirePassword', 'login'),
    (r'test_kakao_review_fixes\.ServerReviewFixTests\.test_(deleted|edit)', 'edit'),
    (r'test_kakao_review_fixes\.ServerReviewFixTests\.test_one_failing_scheduled', 'scheduled'),
    (r'test_kakao_review_fixes\.ClientReviewFixTests', 'room_list'),
    (r'test_kakao_settings_store|test_kakao_review_fixes\.SettingsUiReviewFixTests', 'lock'),
    (r'(?i)UnreadBlink', 'unread_blink'),
    # 사내 그룹웨어와 같은 조직도 (엑셀 → 그룹사 › 한국지주 · 계열사 13곳 같은 단계, 지식봇 맨 위, 조직도 그룹)
    # v5 - 업무용 메신저 필수 기능 (test_kakao_v5, 명세 §10)
    (r'test_kakao_v5\.(Dnd)|test_kakao_input_controls', 'dnd'),
    (r'test_kakao_v5\.Vacation', 'presence'),
    (r'test_kakao_v5\.RoomOwner', 'room_owner'),
    (r'test_kakao_v5\.Thread', 'thread'),
    (r'test_kakao_v5\.MentionList|test_kakao_v5\.WorkUiTest\.test_mentions', 'mentions_inbox'),
    (r'test_kakao_v5\.WorkUiTest\.test_unread', 'unread_members'),
    (r'test_kakao_v5\.WorkUiTest\.test_draft', 'draft'),
    (r'test_kakao_v5\.WorkUiTest\.test_bookmark', 'bookmark'),
    (r'test_kakao_v5\.(MainWindowMinSize|Contrast)', 'font_size'),
    # v4 - 카카오톡 2026 업데이트 반영 (test_kakao_v4)
    (r'test_kakao_v4\..*(Summary|summary)', 'ai_summary'),
    (r'test_kakao_v4\..*reaction', 'reaction_more'),
    (r'test_kakao_v4\..*voice', 'voice_msg'),
    (r'test_kakao_v4\..*contact', 'contact_share'),
    (r'test_kakao_v4\..*(call_history|call_folder)', 'call_folder'),
    (r'test_kakao_v4\..*export|test_kakao_chat_export', 'chat_export'),
    (r'test_kakao_v4\..*story', 'story_hide'),
    (r'test_kakao_v4\.FailedMessageTest', 'failed_resend'),
    (r'test_kakao_v4\.(UiStylingPatternsTest|DesignTokenExportTest)', 'keyboard'),
    (r'test_kakao_v4\.AccessibilityTest\.test_keyboard|shortcuts_and_quick_switcher', 'keyboard'),
    (r'test_kakao_v4\..*org_contact', 'contact_share'),
    (r'test_kakao_org_groupware\.|test_kakao_org_groups\.', 'org_chart'),
    (r'test_kakao_friend_add\.', 'friend_add'),
    (r'test_kakao_profile_edit\.(ProfileEdit|DeputyServer)', 'profile_edit'),
    (r'test_kakao_osenv\.', 'platforms'),   # OS 공통 계층 · kcs.py · 빌드 · 바로가기
    (r'test_kakao_login_perf\.', 'font_size'),   # 로그인 직후 사진이 몰려와도 화면이 멈추지 않음
    # v3 화면 (이 스크립트와 함께 만든 테스트)
    (r'test_kakao_tour\.OpenChatTests', 'open_chat'),
    (r'test_kakao_tour\.TalkDrawerTests', 'talk_drawer'),
    (r'test_kakao_tour\.(FeatureTourTests|DemoSeedTests)', 'feature_tour'),
    # 클래스 단위 (채팅방/서버/v3 셸 테스트)
    (r'StoryViewerTests', 'story_view'),
    (r'StoryComposeTests', 'story_post'),
    (r'\.BoardTests', 'board'),
    (r'\.LadderTests', 'ladder'),
    (r'\.(CallTests|TestCalls)', 'voice_call'),
    (r'\.SecretRoomTests', 'secret'),
    (r'(?i)multi_?profile', 'multi_profile'),
    (r'BubbleStyleTests', 'bubble_style'),
    (r'KeywordTests', 'keyword_alert'),
    (r'feature_tour|_tour_', 'feature_tour'),
    (r'Lock(Pin|Settings|Screen)Tests|MainWindowLockTests', 'lock'),
    (r'FriendsTabV3Tests\.test_context_menu_hide', 'hide_block'),
    (r'profile_dialog_actions', 'profile_view'),
    (r'focus_(message|loads)', 'search'),
    (r'members_popup|group_counts_companies', 'invite'),
    # 캘린더 모듈
    (r'test_kakao_calendar\..*todo', 'todo'),
    (r'test_kakao_calendar\..*birthday', 'birthday_cal'),
    (r'test_kakao_calendar\..*(rsvp|room_event)', 'event_share'),
    (r'test_kakao_calendar\.(EventDialogTest|EventDetailDialogTest|ShowAndProcessEventsTest)', 'event_crud'),
    (r'test_kakao_calendar\.', 'calendar'),
    # v3 기능
    (r'secret|round_trip_and_tamper|key_persists|publishes_key', 'secret'),
    (r'voiceroom', 'voiceroom'),
    (r'screen', 'screen_share'),
    (r'video|face_?call|group_call', 'face_call'),
    (r'call', 'voice_call'),
    (r'stor(y|ies)', 'story_post'),
    (r'multi_?profile', 'multi_profile'),
    (r'block|hide_friend|hidden_friend', 'hide_block'),
    (r'ladder', 'ladder'),
    (r'board_open_chat_drawer', 'talk_drawer'),
    (r'board', 'board'),
    (r'open_(room|chat)', 'open_chat'),
    (r'drawer_query|talk_drawer|global_drawer', 'talk_drawer'),
    (r'retention|expire', 'retention'),
    (r'keyword|alert_rules', 'keyword_alert'),
    (r'\block|_lock|lock_', 'lock'),
    (r'alert_mode', 'alert_mode'),
    (r'bubble_style', 'bubble_style'),
    # 2025 개편
    (r'briefing', 'briefing'),
    (r'link_preview', 'link_preview'),
    (r'silent', 'silent'),
    (r'folder', 'folders'),
    (r'peek', 'peek'),
    (r'event_change', 'event_changed'),
    (r'events_in_room|drawer_events_tab', 'room_events'),
    (r'neutral_delete|delete_for_all|hide_for_me|delete_updates', 'delete'),
    (r'edit_own|edit_rules|edit_menu|edit_flow|edit_escape|edited_label|editing_message|edit_and_', 'edit'),
    # AI 비서
    (r'ai_button|direct_question', 'ai_chat'),
    (r'invite_bot|ask_bot|group_only_on_mention', 'ai_invite'),
    (r'test_rag\.|rag|learn|bot_reports|decode_document', 'ai_docs'),
    # 일정 · 할 일 · 투표 (서버/상태/채팅방)
    (r'reminder', 'event_remind'),
    (r'rsvp|event_in_room|events_changed', 'event_share'),
    (r'event', 'event_crud'),
    (r'todo', 'todo'),
    (r'poll', 'poll'),
    # 조직도 · 프로필 · 상태
    (r'wide_window_docks', 'org_dock'),
    (r'org|holding|affiliate|multilevel|search_by_org_path|cross_company', 'org_chart'),
    (r'company', 'company_chip'),
    (r'presence|auto_away|manual_status|IdleTests', 'presence'),
    (r'profile_view', 'profile_view'),
    (r'avatar|profile_photo|profile_edit|profile_update', 'profile_edit'),
    (r'favorite', 'favorites'),
    (r'birthday', 'birthday'),
    (r'FriendsTabTests|bot_section|sections', 'friends'),
    # 채팅방 · 메시지
    (r'invite', 'invite'),
    (r'reply', 'reply'),
    (r'react', 'reaction'),
    (r'mention', 'mention'),
    (r'notice', 'notice'),
    (r'scheduled', 'scheduled'),
    (r'attachment|send_file|drop_files|paste_image|file_card', 'attach'),
    (r'emoticon|enter_sends|shift_enter|paste_text', 'text_emoticon'),
    (r'forward|context_actions|context_menu_others|send_to_me', 'copy_forward'),
    (r'search', 'search'),
    (r'drawer', 'room_drawer'),
    (r'group_create|create_group|rename', 'group'),
    (r'self_room', 'self_chat'),
    (r'direct', 'direct'),
    (r'pin|mute|order_and_pinning', 'pin_mute'),
    (r'unread|read_update|mark_read|message_unread|send_read', 'read_count'),
    (r'ChatsTabTests|room_list|row_content|relative_time', 'room_list'),
    # 설정 · 브랜드
    (r'test_kakao_wallpaper|wallpaper', 'wallpaper'),
    (r'test_kakao_brand|test_kakao_macos|brand|logo|app_icon', 'brand_ci'),
    (r'dark|apply_theme', 'dark_mode'),
    (r'font', 'font_size'),
    (r'notification|notifies', 'notifications'),
]


def feature_catalog():
    """[(key, 영역, 이름)] - 기능 둘러보기 목록을 그대로 쓴다"""
    from client.kakao.feature_tour import FEATURES
    return [(f[0], f[1], f[2]) for f in FEATURES]


def map_test(test_id):
    """'tests.test_x.Class.method' → 기능 key (없으면 '기타')"""
    short = test_id.split('tests.', 1)[-1]
    for pattern, key in MAPPING:
        if re.search(pattern, short):
            return key
    return OTHER


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []      # (test_id, 'pass'|'fail'|'error'|'skip', traceback text)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append((test.id(), 'pass', ''))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append((test.id(), 'fail', self._exc_info_to_string(err, test)))

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append((getattr(test, 'id', lambda: str(test))(), 'error', self._exc_info_to_string(err, test)))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append((test.id(), 'skip', reason))

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.records.append((test.id(), 'pass', ''))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.records.append((test.id(), 'fail', '예상과 달리 성공했습니다'))


def run_suite(quiet=False, pattern='test_*.py'):
    from tests.kakao_fakes import app   # QApplication 을 먼저 만든다 (offscreen)
    app()
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, 'tests'), pattern=pattern, top_level_dir=ROOT)
    stream = sys.stderr if not quiet else io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=1, resultclass=RecordingResult, buffer=True)
    started = time.monotonic()
    result = runner.run(suite)
    return result.records, time.monotonic() - started


def summarize(records):
    """→ (기능 행 목록, 실패 목록). 행 = dict(key, group, name, status, passed, total, tests)"""
    catalog = feature_catalog()
    by_key = {}
    for test_id, outcome, detail in records:
        by_key.setdefault(map_test(test_id), []).append((test_id, outcome, detail))
    rows = []
    for key, group, name in catalog + [(OTHER, OTHER, OTHER)]:
        tests = by_key.get(key, [])
        counted = [t for t in tests if t[1] != 'skip']
        passed = sum(1 for t in counted if t[1] == 'pass')
        if not counted:
            status = '-'
        else:
            status = 'PASS' if passed == len(counted) else 'FAIL'
        rows.append(dict(key=key, group=group, name=name, status=status, passed=passed, total=len(counted),
                         tests=tests))
    failures = [(t, o, d) for t, o, d in records if o in ('fail', 'error')]
    return rows, failures


def _first_lines(tb, n=6):
    lines = [ln for ln in tb.strip().splitlines() if ln.strip()]
    # 마지막 오류 줄 + 그 앞의 위치 몇 줄
    return lines[-n:]


def render_markdown(rows, failures, records, elapsed):
    total = len(records)
    count = {k: sum(1 for r in records if r[1] == k) for k in ('pass', 'fail', 'error', 'skip')}
    tested = [r for r in rows if r['status'] != '-']
    ok = sum(1 for r in tested if r['status'] == 'PASS')
    out = ['# KCS Talk 기능 검증 결과', '',
           f"- 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
           '- 명령: `python3 scripts/verify_features.py` (tests/test_*.py 전체, offscreen)',
           f"- 테스트 {total}개: 통과 {count['pass']} · 실패 {count['fail']} · 오류 {count['error']} · "
           f"건너뜀 {count['skip']} ({elapsed:.1f}초)",
           f'- 기능 {len(tested)}개 검증: PASS {ok} · FAIL {len(tested) - ok}', '',
           '| 영역 | 기능 | 결과 | 테스트 |', '|---|---|---|---|']
    for r in tested:
        mark = '✅ PASS' if r['status'] == 'PASS' else '❌ FAIL'
        out.append(f"| {r['group']} | {r['name']} | {mark} | {r['passed']}/{r['total']} |")
    untested = [r for r in rows if r['status'] == '-' and r['key'] != OTHER]
    if untested:
        out += ['', '## 자동 테스트가 없는 기능', '',
                ', '.join(r['name'] for r in untested) + ' — 기능 둘러보기의 [해보기] 또는 `python3 scripts/demo.py` 로 확인하세요.']
    if failures:
        out += ['', '## 실패한 테스트', '']
        for test_id, outcome, detail in failures:
            out.append(f"### {test_id} ({'실패' if outcome == 'fail' else '오류'}) → {map_test(test_id)}")
            out.append('```')
            out += _first_lines(detail)
            out.append('```')
    return '\n'.join(out) + '\n'


def print_table(rows, out=sys.stdout):
    tested = [r for r in rows if r['status'] != '-']
    width = max(len(r['name']) for r in tested) + 2 if tested else 10
    out.write('\n  결과   ' + '기능'.ljust(width) + '테스트\n  ' + '-' * (width + 20) + '\n')
    group = None
    for r in tested:
        if r['group'] != group:
            group = r['group']
            out.write(f'  [{group}]\n')
        out.write(f"  {'✓ PASS' if r['status'] == 'PASS' else '✗ FAIL'}  {r['name'].ljust(width)}{r['passed']}/{r['total']}\n")
    ok = sum(1 for r in tested if r['status'] == 'PASS')
    out.write(f'\n  기능 {len(tested)}개 중 {ok}개 통과\n')
    out.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description='기능별 테스트 결과 표')
    ap.add_argument('-q', '--quiet', action='store_true', help='진행 표시 없이')
    ap.add_argument('--pattern', default='test_*.py', help='테스트 파일 패턴 (기본 test_*.py)')
    ap.add_argument('--output', default=OUTPUT, help='결과 마크다운 경로')
    args = ap.parse_args(argv)
    records, elapsed = run_suite(args.quiet, args.pattern)
    rows, failures = summarize(records)
    print_table(rows)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(render_markdown(rows, failures, records, elapsed))
    print(f'  → {os.path.relpath(args.output, ROOT)}')
    if failures:
        print(f'  실패/오류 테스트 {len(failures)}개 (상세는 결과 파일 참고)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
