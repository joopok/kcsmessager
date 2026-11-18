# 서버 요청 처리 시간 (화면을 여는 요청)

`python3 scripts/bench_server_requests.py --write` 로 만든다 (2026-11-18 08:28).

규모: full · 요청마다 5번 · 목표 서버 처리 100ms 이하 (비밀번호 로그인은 의도한 PBKDF2 시간이 들어 있어 목표에서 뺀다)

| 요청 | 화면 | 서버 처리 ms (중앙값 / 최대) | 왕복 ms (중앙값) | 응답 크기 | 판정 |
|---|---|---|---|---|---|
| join (자동 로그인) | 로그인 → welcome · 메인 창 | 29.5 / 38.8 | 32.2 | 453.3 KB | ✓ |
| join (비밀번호) | 비밀번호 로그인 (PBKDF2 포함) | 48.8 / 52.1 | 51.0 | 453.3 KB | ✓ |
| history | 큰 방 처음 50개 | 0.5 / 0.8 | 0.8 | 24.5 KB | ✓ |
| history:이전 | 큰 방 이전 대화 50개 | 0.7 / 0.8 | 1.0 | 24.9 KB | ✓ |
| history:1:1 | 1:1 방 처음 50개 | 0.3 / 0.4 | 0.6 | 24.5 KB | ✓ |
| search | 전체 검색 | 22.7 / 24.2 | 23.1 | 50.2 KB | ✓ |
| search:방 | 큰 방 안 검색 | 1.0 / 1.2 | 1.4 | 50.5 KB | ✓ |
| attachment_get | 사진 받기 | 0.5 / 2.0 | 0.9 | 160.1 KB | ✓ |
| drawer_query:image | 서랍 사진 | 7.7 / 10.3 | 8.3 | 116.1 KB | ✓ |
| drawer_query:file | 서랍 파일 | 0.1 / 0.2 | 0.1 | 0.1 KB | ✓ |
| drawer_query:link | 서랍 링크 | 2.2 / 2.2 | 2.7 | 94.1 KB | ✓ |
| mention_list | 멘션 모아보기 | 1.0 / 1.2 | 1.4 | 50.7 KB | ✓ |
| thread_get | 스레드 열기 | 0.3 / 0.4 | 0.4 | 14.0 KB | ✓ |
| board_list | 톡게시판 | 0.0 / 0.1 | 0.1 | 0.2 KB | ✓ |
| event_list | 캘린더 (6주) | 4.8 / 5.8 | 6.0 | 165.7 KB | ✓ |
| todo_list | 할 일 | 0.7 / 0.8 | 0.9 | 2.6 KB | ✓ |
| story_list | 펑 | 0.6 / 1.0 | 1.0 | 43.4 KB | ✓ |
| call_history | 통화 기록 | 1.7 / 2.8 | 2.1 | 21.1 KB | ✓ |
| call_list | 진행 중 통화 | 0.1 / 0.3 | 0.2 | 0.1 KB | ✓ |
| multi_profile_list | 멀티프로필 | 0.0 / 0.0 | 0.1 | 0.1 KB | ✓ |
| rag_doc_list | AI 문서 목록 | 0.0 / 0.0 | 0.1 | 0.1 KB | ✓ |
| file_request_list | 내 파일 승인 요청 | 0.0 / 0.1 | 0.1 | 0.1 KB | ✓ |
| open_room_search | 오픈채팅 찾기 | 0.1 / 0.1 | 0.2 | 0.1 KB | ✓ |
| admin_users | 관리자 구성원 목록 | 3.0 / 3.2 | 3.9 | 173.5 KB | ✓ |
| admin_file_requests | 관리자 승인 요청 | 0.0 / 0.1 | 0.1 | 0.1 KB | ✓ |
| admin_file_exceptions | 관리자 예외 | 0.0 / 0.1 | 0.1 | 0.1 KB | ✓ |
| room_open_direct | 있는 1:1 열기 | 0.1 / 0.4 | 0.3 | 0.8 KB | ✓ |
| room_open_direct:새 | 새 1:1 열기 | 0.1 / 0.4 | 0.2 | 0.3 KB | ✓ |
| room_create | 그룹방 만들기 | 0.5 / 0.6 | 0.4 | 0.4 KB | ✓ |
| read | 큰 방 읽음 (처음엔 안 읽은 것이 많음) | 0.7 / 1.7 | 0.9 | 0.1 KB | ✓ |

welcome 항목별 크기: users 228.4KB, rooms 217.4KB, org 4.0KB, file_policy 2.4KB, me 0.5KB, friends 0.4KB, policy 0.1KB, calls 0.0KB, blocked 0.0KB

주기 작업 (한 번, 저장소 잠금을 잡는 동안 다른 요청이 기다린다)

| 작업 | ms |
|---|---|
| 보관 기간 정리 (_run_retention) | 0.2 |
| 안 쓰는 첨부 정리 (_run_orphan_sweep) | 0.1 |
| 일정 알림 (_run_reminders) | 0.8 |
| 펑 만료 (_run_story_expiry) | 0.0 |
| 예약 메시지 (_run_scheduled) | 0.0 |

로그인 몰림: 100명이 한꺼번에 자동 로그인 (welcome 100명 받음, 모두 끝날 때까지 1,845ms)

| 항목 | 중앙값 ms | p95 ms | 최대 ms |
|---|---|---|---|
| 로그인 한 건 (연결 → welcome) | 923.6 | – | 1459.9 |
| 그동안 다른 사람의 메시지 보내기 (134건) | 1.4 | 5.9 | 68.5 |
