# KCS Messenger – 카카오톡 기능 분석 & 구현 명세 (v2)

작성일: 2026-10-08 · 브랜치: `feature/kakao-full-features`

이 문서는 (1) 카카오톡 PC 버전의 기능 분석, (2) 이번 버전에서 구현할 범위,
(3) 서버-클라이언트 **프로토콜 계약**, (4) 클라이언트 모듈 구조를 정의한다.
서버·클라이언트·테스트는 모두 이 문서를 기준으로 구현한다.

---

## 1. 카카오톡 기능 분석

| 영역 | 카카오톡 기능 | 이번 버전 | 비고 |
|------|--------------|-----------|------|
| 친구 | 친구 목록, 내 프로필, 상태메시지, 즐겨찾기, 생일인 친구, 온라인 상태 | ✅ | 즐겨찾기는 클라이언트 로컬 |
| 친구 | 프로필 편집(이름·상태메시지) | ✅ | `profile_update` |
| 친구 | 친구 목록 여러 명 선택(Shift/Ctrl+클릭 토글, Shift+누른 채 끌기 범위 선택·끝에서 자동 스크롤·접힌 구역 포함, 우클릭 '대화상대로 선택'·'선택한 N명(+그 친구) 대화방 만들기') → 대화방 만들기 | ✅ | 클라이언트 전용, 1명이면 `room_open_direct` · 2명 이상 `room_create` |
| 채팅방 | 1:1 채팅, 그룹 채팅, 나와의 채팅 | ✅ | `direct` / `group` / `self` |
| 채팅방 | 채팅방 목록(마지막 메시지, 시간, 안 읽은 수 배지) | ✅ | |
| 채팅방 | 채팅방 이름 변경, 친구 초대, 나가기 | ✅ | 시스템 메시지 생성 |
| 채팅방 | 참여자 줄(나와의 채팅 외 모든 방, 헤더 아래. 접힘 '대화상대 N명 이름… ›' ⇄ 펼침 사진·이름 칩 '⌄', 머리 줄·›·방 메뉴로 토글, 방마다 기억, 내가 만든 그룹방은 펼친 채로 열림) | ✅ | 클라이언트 전용, 설정 `members_strip_open:[room_id]`, `AppState.created_rooms` |
| 채팅방 | 채팅방 상단 고정, 알림 끄기 | ✅ | 클라이언트 로컬 |
| 메시지 | 텍스트, 이모티콘, 사진, 파일 | ✅ | 첨부 2MB 제한 |
| 메시지 | 읽음 표시(안 읽은 사람 수 노란 숫자) | ✅ | read pointer 기반 |
| 메시지 | 답장(인용) | ✅ | `reply_to` |
| 메시지 | 공감(반응 이모지) | ✅ | 토글 |
| 메시지 | 모든 대화 상대에게서 삭제(24시간 이내), 나에게서 삭제 | ✅ | 나에게서 삭제=로컬 |
| 메시지 | 복사, 전달, 나에게 보내기 | ✅ | 클라이언트에서 send 재사용 |
| 메시지 | 멘션(@이름) | ✅ | `mentions` |
| 메시지 | 예약 메시지 | ✅ | `send_at` |
| 메시지 | 날짜 구분선, 연속 메시지 프로필 생략 | ✅ | UI |
| 채팅방 기능 | 공지 등록/해제(상단 배너) | ✅ | |
| 채팅방 기능 | 투표(단일/복수 선택, 마감) | ✅ | |
| 채팅방 기능 | 대화 검색 | ✅ | |
| 채팅방 기능 | 채팅방 서랍(사진·파일·링크 모아보기) | ✅ | |
| 일정 | 톡캘린더 월간 보기, 일정 생성/수정/삭제 | ✅ | |
| 일정 | 채팅방 일정 공유(일정 카드 메시지) + 참석/불참/미정 응답 | ✅ | |
| 일정 | 일정 알림(N분 전) | ✅ | 서버 스케줄러 → 트레이 알림 |
| 일정 | 할 일(To-do) | ✅ | 개인 |
| 일정 | 생일·기념일 표시 | ✅ | 생일만 |
| 설정 | 다크 모드, 글자 크기 | ✅ | 클라이언트 로컬 |
| 알림 | 새 메시지 데스크톱 알림, 안 읽은 수 합계 | ✅ | `QSystemTrayIcon` |
| 제외 | 보이스톡/페이스톡, 송금(페이), 오픈채팅 검색, 선물하기 결제, E2E 비밀채팅 | ❌ | 외부 인프라·결제 필요 |

## 2. 전송 계층

- TCP, 기본 `127.0.0.1:5555`.
- **줄 단위 JSON(NDJSON)**, UTF-8. 메시지 1개 = JSON 객체 1개 + `\n`.
- 서버는 줄바꿈 없이 이어 붙은 JSON(구버전 클라이언트)도 `json.JSONDecoder.raw_decode`로 파싱한다.
- 모든 요청은 `"type"`을 가진다. 선택 필드 `"req_id"`를 보내면 응답에 그대로 돌려준다.
- 오류: `{"type":"error","req_type":"<요청 type>","message":"<한국어 설명>","req_id":...}`

## 3. 데이터 모델 (JSON)

```text
User    {username, display_name, status_message, birthday:"YYYY-MM-DD"|"MM-DD"|null, online:bool}
        # birthday: 생년월일(YYYY-MM-DD, 연도 1900~올해). 예전 값·연도 비공개는 MM-DD. 생일 비교는 끝 5자(MM-DD)로 한다.
        # 클라이언트 프로필 편집(2026-10-13)은 연도까지(YYYY-MM-DD)만 저장한다. 저장된 값이 MM-DD 면 칸 아래 '연도가 빠져 있어요' 안내 + 그 칸에 포커스. 서버는 예전 클라이언트 호환으로 MM-DD 도 받는다.
        # 프로필 카드는 "생년월일 1990년 3월 15일", 연도가 없으면 "생일 3월 15일"로 보여 준다.

Room    {room_id:str, type:"direct"|"group"|"self", name:str,        # direct/self의 name은 ""(클라이언트가 표시명 계산)
         members:[username], created_at:ISO,
         notice:Message|null, last_message:Message|null,
         read_pointers:{username: last_read_msg_id:int}}             # 0 = 아무것도 안 읽음

Message {msg_id:int(전역 증가), room_id, sender:username|"" (system),
         kind:"text"|"emoticon"|"image"|"file"|"system"|"event"|"poll",
         content:str, timestamp:ISO,
         reply_to:{msg_id, sender, preview}|null,
         mentions:[username], reactions:{emoji:[username]},
         deleted:bool,
         attachment:{attachment_id, name, mime, size}|null,         # image/file
         event_id:int|null,                                          # kind=event
         poll:Poll|null}                                             # kind=poll

Poll    {poll_id:int, question, multiple:bool, closed:bool,
         options:[{text, voters:[username]}]}

Event   {event_id:int, room_id:str|null, creator, title, description, location,
         start:ISO, end:ISO, all_day:bool, remind_minutes:int|null, color:"#RRGGBB",
         attendees:{username:"yes"|"no"|"maybe"|"pending"}, created_at:ISO}

Todo    {todo_id:int, owner, title, due:"YYYY-MM-DD"|null, done:bool, created_at:ISO}
```

**안 읽은 수 계산 규칙(클라이언트)**: 메시지 `m`의 unread =
`room.members` 중 `read_pointers.get(u, 0) < m.msg_id` 인 사람 수. (`system` 메시지는 표시 안 함)
발신자는 전송 즉시 자신의 read pointer가 해당 msg_id로 올라간다(서버 처리).
방의 내 안 읽은 메시지 수 = `sender != me` 이고 `msg_id > read_pointers[me]` 이며 `kind != system` 인 메시지 수.
서버는 정확한 값을 위해 Room에 `unread_count`(요청자 기준)도 포함한다.

## 4. 요청 / 응답 / 푸시

| 요청 (client→server) | 필드 | 서버 동작 / 응답 |
|---|---|---|
| `org_companies` | – | **로그인 전에도 가능**(§4.1). 응답 `org_companies {companies:[str]}` — 처음 사용 등록 때 고를 소속 회사 목록 |
| `register` | company, name, employee_no, email, password, remember? | 처음 사용 등록(§4.1). 성공하면 `join` 과 똑같이 `welcome …` |
| `password_reset` | username, email, new_password | **로그인 전에도 가능**(§4.1). 비밀번호 재설정 → `password_reset_ok {username}` |
| `join` | username, password? | 아이디/비밀번호 로그인(§4.1). 비밀번호 없는 구버전·테스트 join 만 사용자 자동 생성. 응답 `welcome {me:User, users:[User], rooms:[Room]}`. 이어서 구버전 호환 `all_users_list {users:[username], online_users:[username]}`. 다른 사람에게 `user_status_changed {username,status:"online"}` |
| `message` (구버전) | content | 모두에게 `message {sender, content, timestamp}` 브로드캐스트(구버전 호환, DB 미저장) |
| `room_open_direct` | target | 1:1 방 생성/조회 → 양쪽에 `room_updated {room}`. target==me면 `self` 방 |
| `room_create` | name, members:[username] | 그룹 방 생성(요청자 자동 포함) → 멤버에게 `room_updated`, 시스템 메시지 "A님이 B, C님을 초대했습니다." |
| `room_invite` | room_id, usernames | 멤버 추가(direct면 오류) → `room_updated` + 시스템 메시지 |
| `room_leave` | room_id | 멤버 제거 → 나간 사람에게 `room_removed {room_id}`, 남은 사람에게 `room_updated` + "A님이 나갔습니다." |
| `room_rename` | room_id, name | → `room_updated` |
| `history` | room_id, before_msg_id?:int, limit?:int=50 | `history {room_id, messages:[Message](오래된→최신), has_more}` |
| `send` | room_id, kind("text"/"emoticon"/"image"/"file"), content, reply_to_id?, mentions?, attachment?{name,mime,data_b64}, send_at?:ISO, client_tmp_id? | 즉시 전송이면 멤버 모두에게 `msg_new {message, client_tmp_id}` + `read_update`(발신자). `send_at`이 미래면 저장 후 요청자에게 `scheduled_ok {scheduled_id, room_id, send_at, content}`; 시각이 되면 `msg_new`. 그때 보낸 사람이 방을 나갔거나 서버 오류로 못 보내면 보낸 사람에게 `scheduled_failed {scheduled_id, room_id, kind, content, reason}`(첨부는 지움, 다시 보내지 않음) |
| `read` | room_id, last_msg_id | read pointer 갱신(감소 불가) → 멤버에게 `read_update {room_id, username, last_read_msg_id}` |
| `react` | msg_id, emoji | 토글 → 멤버에게 `msg_updated {message}` |
| `delete` | msg_id | 발신자 본인 + 24시간 이내만. deleted=true, content="삭제된 메시지입니다.", attachment/poll 유지하지 않음 → `msg_updated` |
| `notice_set` | room_id, msg_id:int\|null | → `room_updated` + 시스템 메시지 "A님이 공지를 등록했습니다." (해제는 메시지 없음) |
| `search` | query, room_id? | 내가 속한 방에서 삭제 안 된 text 메시지 content LIKE 검색, 최신순 최대 100 → `search_result {query, room_id, messages}` |
| `poll_create` | room_id, question, options:[str](2개 이상), multiple:bool | kind=poll 메시지 생성 → `msg_new` |
| `poll_vote` | poll_id, option_indexes:[int] | 내 표를 교체(빈 배열=취소). 마감이면 오류 → `msg_updated` |
| `poll_close` | poll_id | 생성자만 → `msg_updated` |
| `event_create` | title, start, end, all_day?, description?, location?, remind_minutes?, color?, room_id?, attendees?:[username] | room_id가 있으면 참석자=방 멤버(생성자 "yes", 나머지 "pending")이고 kind=event 메시지(content=title, event_id) 게시. 관련자 전원에게 `event_updated {event}` |
| `event_update` | event_id, + 변경 필드 | 생성자만 → `event_updated`. start/remind 변경 시 알림 재무장 |
| `event_delete` | event_id | 생성자만 → 관련자에게 `event_deleted {event_id}` |
| `event_rsvp` | event_id, response("yes"/"no"/"maybe") | 참석자만 → `event_updated` |
| `event_list` | start, end (ISO) | 내가 생성자/참석자인 일정 중 기간 겹치는 것 → `event_list {start, end, events}` |
| `todo_create` | title, due? | → 요청자에게 `todo_updated {todo}` |
| `todo_update` | todo_id, title?, due?, done? | 소유자만 → `todo_updated` |
| `todo_delete` | todo_id | → `todo_deleted {todo_id}` |
| `todo_list` | – | → `todo_list {todos}` |
| `profile_update` | display_name?, status_message?, birthday?, deputies? | → 모두에게 `user_updated {user}` (`deputies` §8.13) |
| `attachment_get` | attachment_id | → `attachment_data {attachment_id, name, mime, data_b64}` |
| `leave` | – | 연결 종료 처리 |

### 4.1 아이디/비밀번호 로그인
- **처음 사용 등록(한 번만)**: `register {company, name, email, password, remember?, employee_no?}`. **2026-10-14 부터 등록 창에 사번 칸이 없다** — `email` 이 그다음부터의 로그인 아이디다(`resolve_login` 이 이메일로 찾는다). `employee_no`(사번)는 선택 필드로, 예전 클라이언트·서버 도구가 보내면 그대로 저장하고 로그인 아이디로도 쓴다. 로그인 전 연결에서 `org_companies` 로 회사 목록을 받아 그중 하나를 반드시 고른다.
  - 회사 목록: org.json v2 의 지주사 이름 + `companies[].name`, 그리고 사용자들의 company 값(빈 값·"게스트"·봇 제외) — 중복 없이 이 순서.
  - 검증(오류 `error {req_type:"register"}`): 목록에 없는 회사 "소속 회사를 선택해 주세요." / 이름 1~20자(제어 문자·`@` 불가) / 사번은 없어도 되지만 보냈으면 영문·숫자로 시작하는 영문·숫자·`-`·`_` 1~20자가 아니면 "사번은 영문·숫자 20자 이내로 입력해 주세요." / 다른 사용자의 사번·사내 계정(account)·이름과 같은 사번(대소문자 무시) "이미 등록된 사번입니다. 사번과 비밀번호로 로그인해 주세요." / 이메일 형식 "이메일 주소 형식이 올바르지 않습니다." / 비밀번호 4~64자 / 다른 사용자가 쓰는 이메일(대소문자 무시) "이미 가입된 이메일입니다." / 이미 비밀번호가 있는 이름 "이미 등록된 이름입니다. 아이디와 비밀번호로 로그인해 주세요." / 봇 이름 불가.
  - 없는 이름이면 그 회사 소속으로 새로 만든다. 비밀번호가 없는 기존 계정(조직도 시드·예전 게스트)이면 그 계정에 이메일·비밀번호(사번을 보냈으면 사번도)를 등록한다. 이때 계정의 회사가 비어 있거나 "게스트"면 고른 회사로 바꾸고, 다른 회사면 "선택한 소속에 같은 이름의 구성원이 없습니다. 소속을 다시 확인해 주세요." 비밀번호 등록은 `password_hash IS NULL` 일 때만(동시 등록 중 한쪽만).
  - **계정 가로채기 방지 (2026-10-11 · 2026-10-14 이메일 확인):** 비밀번호가 없는 기존 계정은 조직도에 적힌 값과 맞아야 가져갈 수 있다. 조직도에 이메일이 있으면 입력한 이메일이 같아야 한다(대소문자 무시) — "조직도에 등록된 이메일과 다릅니다. 회사 이메일을 확인하거나 관리자에게 문의해 주세요." 사번을 보냈고 조직도에도 사번이 있으면 같아야 한다 — "조직도에 등록된 사번과 다릅니다. 사번을 확인하거나 관리자에게 문의해 주세요." 조직도에 사번만 있고 이메일이 없는 계정에 사번 없이(새 클라이언트) 등록하면 — "조직도에 이메일이 등록되지 않아 직접 등록할 수 없습니다. 관리자에게 이메일 등록을 요청해 주세요." 사번·이메일이 모두 없는 관리자 계정은 직접 등록할 수 없다 — "관리자 계정은 직접 등록할 수 없습니다. 서버 PC 에서 scripts/set_password.py 로 비밀번호를 정해 주세요." 등록 실패는 로그인 실패처럼 세어 5번이면 5분 잠근다.
  - 성공하면 `join` 과 같은 `welcome`(+ `remember:true` 면 `login_token`) 과 이후 알림.
- **비밀번호 재설정**: `password_reset {username, email, new_password}` (로그인 전 연결). 관리자 계정은 이메일만으로 바꿀 수 없다 — "관리자 계정은 직접 재설정할 수 없습니다. 다른 그룹 관리자나 서버 PC 의 scripts/set_password.py 로 바꿔 주세요." **이메일은 본인 · 관리자에게만 보낸다**: welcome `users` · `user_updated` · `friend_added` 의 다른 사람 User 는 `email:""` (재설정 확인 값이자 '정확히 아는 사람만' 찾는 친구 추가 값이라 모두에게 공개하지 않는다). `username` 은 join 과 같은 로그인 아이디(이름·사내 계정·이메일). 그 계정에 비밀번호가 등록돼 있고 가입 이메일이 `email` 과 같으면(대소문자 무시) 새 비밀번호(4~64자)로 바꾸고 그 계정의 자동 로그인 토큰을 모두 폐기한 뒤 `password_reset_ok {username}` (연결은 로그인되지 않은 상태 그대로). 아이디가 없거나 이메일이 다르면 `error {req_type:"password_reset"}` "아이디 또는 가입한 이메일이 일치하지 않습니다." (계정이 있는지 드러내지 않는다) 이고 로그인 실패와 같은 횟수 제한(아이디 5번·IP 20번 → 5분)에 센다. 비밀번호를 등록한 적 없는 계정은 처음 사용 등록 안내 오류. 메일 발송 없이 가입 이메일로만 확인하므로, 메일 인증이 필요해지면 확인 코드 단계를 추가한다.
- **그다음부터는 `join {username, password}`**: `username` 은 로그인 아이디로, 클라이언트는 로그인 창에 넣은 값(운영은 이메일)을 보낸다. 사번(`employee_no`, 대소문자 무시)이 같은 사용자가 먼저이고, 없으면 사용자 이름(username), 그다음 대소문자 무시로 조직 `account`, `email` 과 같은 사용자로 로그인한다(1~120자. 사번 없이 등록한 예전 계정 호환). User 에 `employee_no`(없으면 "") 포함.
- `password` 가 있으면: 비밀번호가 등록된 계정은 PBKDF2-SHA256(솔트) 해시와 비교해 틀리면 오류 "아이디 또는 비밀번호가 올바르지 않습니다." 비밀번호가 아직 없는 계정이나 없는 아이디는 오류 "처음 사용하는 아이디입니다. [처음 사용 등록]에서 소속 회사·이름·이메일·비밀번호를 등록해 주세요." (로그인 실패 횟수에는 세지 않는다). 비밀번호는 4~64자, 아니면 오류 "비밀번호는 4~64자로 입력해 주세요."
- `password` 가 없으면(구버전·테스트 클라이언트): 비밀번호가 없는 계정만 로그인된다(없는 아이디는 20자 이내로 게스트 생성). 비밀번호가 등록된 계정은 오류 "비밀번호를 입력해 주세요."
- 비밀번호(해시)는 User 객체에 절대 포함하지 않는다. 오류 응답은 `error {req_type:"join"}` 이고 연결은 유지되어 같은 연결에서 다시 join 할 수 있다.
- 로그인 정보 저장/자동 로그인: `join` 에 `remember:true` 를 주고 비밀번호로 성공하면 welcome 에 `login_token`(무작위 32바이트, urlsafe) 을 넣는다. 서버는 토큰의 SHA-256 만 `login_tokens(token_hash, username, created_at)` 에 저장하고 30일 뒤 만료. 사용자당 최근 5개만 유지. `join {username, token}` 은 비밀번호 대신 토큰으로 로그인(토큰 주인과 아이디가 같아야 함). 틀리거나 만료되면 오류 "저장된 로그인 정보가 만료되었습니다. 비밀번호를 입력해 주세요." `login_token_revoke {token}` → 그 토큰 삭제 후 `login_token_revoked {}` (로그아웃).
- 보안: 같은 아이디로 5번(IP 기준 20번) 틀리면 5분간 그 아이디/IP 의 join 을 "로그인 실패가 너무 많습니다. N분 뒤에 다시 시도해 주세요." 로 거절. 서버 옵션 `require_password`(배포용 `chat_server.py` 기본 켬, `--allow-passwordless` 로 끔)면 비밀번호·토큰 없는 join 은 "비밀번호를 입력해 주세요." 관리자 도구 `scripts/set_password.py <아이디> [--password P | --clear]` (변경 시 토큰 모두 폐기. `--clear` 하면 그 사람은 다시 [처음 사용 등록]을 한다).
- 설정 파일: 클라이언트 설정은 welcome 의 실제 username 기준 파일(계정 아이디로 로그인해도 같은 파일)이며 임시 파일 → 교체로 저장, 바뀐 키만 합쳐 쓴다.
- **간편 로그아웃 (2026-10-10):** 메인 창 왼쪽 아래 내 사진(사이드바)을 우클릭하면 메뉴 [이름(표시만) · 로그아웃]. 로그아웃은 확인 창 없이 바로 더보기 → 로그아웃과 같은 처리(`login_token_revoke` 로 토큰 폐기 + 저장된 로그인 삭제 + 로그인 창부터 다시)를 한다(메뉴를 고른 것이 확인). 더보기 · 트레이의 로그아웃은 그대로 확인 창을 띄운다.
- 클라이언트 저장: `~/.kcs_messenger/saved_login.json` (권한 0600) `{login_id, token, auto_login, host, port}`. 비밀번호 원문은 어디에도 저장하지 않는다. 로그인 창의 [사번·비밀번호 저장] 이 켜져 있으면 다음 실행 때 아이디가 채워지고 비밀번호 칸은 '저장됨' 표시 → 그대로 로그인하면 토큰을 쓴다. [자동 로그인] 이면 로그인 창 없이 바로 토큰으로 로그인(실패하면 로그인 창 + 사유). 단, `app.py --no-auto-login`(런처가 창을 열 때 사용)이면 자동 로그인을 건너뛰고 로그인 창을 띄운다(아이디만 미리 채움). 더보기 → 로그아웃: 토큰 폐기 + 저장 정보 삭제 후 로그인 창으로 다시 시작.
- 로그인 창 모양(macOS): 카카오톡 로그인 창처럼 회색 제목 표시줄 없이 네이비 배경이 창 맨 위(신호등 버튼 뒤)까지 이어진다 — `macos.unify_title_bar` (투명 제목 표시줄 · 제목 글자 숨김 · 전체 크기 내용), 내용은 신호등 높이(28px)만큼 내린다. 메인 창도 같다: 사이드바 네이비가 맨 위까지, 사이드바 오른쪽 열(조직도 · 목록 · 분할 화면 대화창)은 위 28px 띠를 각 헤더 색(대화창은 대화 배경 위 반투명 헤더색)으로 칠한다(`TitleBand`). 채팅방 별도 창도 같다: 헤더(대화 배경 위 반투명 헤더)가 맨 위까지, 헤더 위 여백 10px + 28px. 분할 화면(메인 창 안)에 들어가면 10px 로 되돌리고, 다시 별도 창으로 꺼내면(새 네이티브 창) 다시 적용한다. 배경 끌기(`setMovableByWindowBackground`)는 켜지 않는다 — 누른 채로 있으면 창 끌기로 잡혀 콤보 목록 등 팝업이 바로 닫힌다. 다른 OS 는 기본 창 그대로.
- **운영 로그인 아이디 = 이메일 (2026-10-14):** KCS 전 직원 계정은 서버의 `server_data/org.json`(포털 사용자·부서 기준, 회사 `KCS`)으로 시드하고 이메일·초기 비밀번호를 `Store` 로 주입했다. 사번(`employee_no`)은 비워 두어 `resolve_login` 이 이메일로 찾는다. 로그인 창 문구는 '이메일'이지만 서버 규칙은 그대로라 사번·사내 계정·이름도 통한다. [처음 사용 등록]에도 사번 칸이 없다(같은 날 제거) — 등록한 이메일이 다음부터의 아이디.
- 클라이언트: 로그인 창은 [로그인](이메일·비밀번호, 둘 다 있어야 버튼 활성)과 [처음 사용 등록](소속 회사 선택 — 열 때 서버에서 `org_companies` 로 받아 옴, 이름, 이메일(다음부터 로그인 아이디), 비밀번호 — 사번 칸 없음) 두 화면을 아래 링크로 오간다. 비밀번호 칸에는 입력값 보기(눈) 아이콘. [로그인] 화면 아래 [비밀번호 재설정] 링크 → 아이디 · 가입한 이메일 · 새 비밀번호 화면 (`net.reset_password` 로 짧게 접속해 password_reset) → 성공하면 로그인 화면으로 돌아와 아이디를 채우고 안내. join 오류가 '처음 사용 등록' 안내면 등록 화면으로 바꿔 입력한 아이디를 이메일 형식이면 이메일 칸, 이름 모양(사번 모양이 아닌 20자 이내)이면 이름 칸에 옮겨 준다(사번 모양은 옮기지 않음). 등록에 성공하면 최근·저장 로그인 아이디는 이메일이고, 자동 재연결은 같은 비밀번호로 join 한다. 최근 로그인 칩은 아이디만 채우고 비밀번호로 포커스를 옮긴다(비밀번호는 저장하지 않음). join 오류면 로그인 창을 다시 띄워 오류를 보여 준다. 자동 재연결은 메모리에 둔 같은 비밀번호로 다시 join 한다.


### 4.2 관리자 권한
- **관리자 지정**: `users.is_admin`. ① `org.json` v2 의 `admins:[username]`(서버 시작 때마다 is_admin=1 로 올린다 — 내리지는 않는다), ② 관리자 도구 `scripts/set_admin.py <아이디> [--off]`, ③ 다른 관리자가 앱에서 `admin_set_admin`. 회사 `org.json` 은 `admins:["KCS관리자"]`(사내 계정 `kcs_administrator`).
- User 객체에 `is_admin`(bool) 포함. 클라이언트는 `me.is_admin` 이면 더보기 탭에 '관리자' 메뉴를 보인다(서버가 모든 요청에서 다시 검사하므로 화면 숨김은 편의일 뿐).
- **사용 중지**: `users.disabled`. 중지된 계정은 `join`(비밀번호·토큰 모두)·`register`·`password_reset` 이 오류 "사용이 중지된 계정입니다. 관리자에게 문의해 주세요." (로그인 실패 횟수에 세지 않는다). 중지하면 그 계정의 자동 로그인 토큰을 모두 폐기하고, 접속 중인 연결에는 `account_disabled {message}` 를 보낸 뒤 끊는다. 클라이언트는 재연결하지 않고 사유를 보여 준 뒤 로그인 창으로 돌아간다.
- **관리자 요청** (관리자가 아니면 오류 "관리자만 사용할 수 있습니다." / 대상이 없으면 "사용자를 찾을 수 없습니다." / 봇이면 "봇 계정은 관리할 수 없습니다." — `username` 은 정확한 사용자 이름):

| 요청 | 필드 | 응답 |
|---|---|---|
| `admin_users` | – | `admin_users {users:[{username, display_name, company, department, title, account, employee_no, email, registered, disabled, is_admin, online}]}` 봇 제외, 이름순. `registered` = 비밀번호 등록됨 |
| `admin_reset_password` | username, new_password(4~64) | 비밀번호 교체 + 그 계정 토큰 모두 폐기 → `admin_done {action:"reset_password", username}` |
| `admin_clear_password` | username | 비밀번호 삭제(사번·이메일은 남김) + 토큰 폐기 → 그 사람은 [처음 사용 등록]을 다시 → `admin_done {action:"clear_password", username}` |
| `admin_set_disabled` | username, disabled(bool) | 자기 자신은 중지 불가 "자기 계정은 사용 중지할 수 없습니다." → `admin_done {action:"set_disabled", username, disabled}` |
| `admin_set_admin` | username, is_admin(bool), company?(§4.3) | 그룹 관리자만. 마지막 그룹 관리자를 해제하거나 회사 관리자로 바꾸면 "관리자가 한 명은 있어야 합니다." → 모두에게 `user_updated` + `admin_done {action:"set_admin", username, is_admin}` |
| `admin_notice` | content(1~1000자) | 접속 중인 모든 사람(나 포함)에게 `admin_notice {sender, content, timestamp}` → `admin_done {action:"notice", delivered:N}` (접속하지 않은 사람에게는 가지 않는다) |

- 관리자 요청이 성공하면 서버 로그에 `관리자 <누가>: <무엇> <대상>` 을 남긴다.
- 클라이언트(`client/kakao/admin.py` AdminDialog): 더보기 → 관리자 → 계정 관리 창(이름·소속·사번·이메일 검색, 표: 이름 / 소속·부서 / 사번 / 이메일 / 상태(미등록·사용 중·사용 중지·관리자·접속 중)), 선택한 사람에게 [비밀번호 재설정…] [등록 초기화] [사용 중지/해제] [관리자 지정/해제] (재설정 외에는 확인 창), [전체 공지…]. `admin_done` 을 받으면 목록을 다시 받는다. `admin_notice` 를 받으면 모든 클라이언트가 '관리자 공지' 창(닫을 수 있는 비모달)과 알림을 띄운다.

### 4.3 그룹사 관리자 · 구성원 관리 (등록 · 조회 · 수정 · 삭제)
- **관리 범위**: `users.admin_company`. 관리자(`is_admin`)이면서 `admin_company=""` → **그룹 관리자**(모든 계열사), 회사 이름 → **회사 관리자**(그 회사 구성원만). User 객체에 `admin_company` 포함. 회사 관리자는 자기 회사 구성원만 목록에 보이고 관리할 수 있으며, 다른 회사 사람을 대상으로 하면 "관리 범위(<회사>) 밖의 구성원입니다.". 관리자 지정·해제(`admin_set_admin`)와 전체 공지(`admin_notice`)는 그룹 관리자만 ("그룹 관리자만 할 수 있습니다."). `admin_set_admin {username, is_admin, company?}` 의 `company` 가 있으면 그 회사의 회사 관리자로 지정(회사 목록에 있어야 함), 없거나 ""면 그룹 관리자. `scripts/set_admin.py <아이디> [--company 회사]`.
- **구성원 필드**: `username`(이름, 1~20자, `@`·제어 문자 불가, 등록 후 바꿀 수 없음), `company`(회사 목록 중), `department`, `title`, `account`(사내 계정), `employee_no`(사번, §4.1 규칙), `extension`, `mobile`, `email`. 문자열 각 60자(이메일 120자) 이내. 사번·사내 계정·이메일은 다른 구성원과 겹치면 안 된다(대소문자 무시) — "이미 다른 구성원이 쓰는 사번입니다." / "… 사내 계정입니다." / "… 이메일입니다.". 조직 경로(`org_path`)는 `[department]`(부서가 없으면 []). 선택 필드 `team`(60자, 2026-10-10)을 함께 보내면 `[department, team]`(팀이 빈 문자열이면 `[department]`) — 수정에서 `team` 없이 `department` 만 바꾸면 예전처럼 `[department]`. `admin_users` 항목에는 `team`(`org_path` 두 번째 단계, 없으면 "")도 준다.
- **요청** (관리자만, 대상은 관리 범위 안):

| 요청 | 필드 | 응답 |
|---|---|---|
| `admin_users` | – | §4.2 + 각 항목에 `title, extension, mobile, removed, admin_company`. 삭제된 구성원도 포함(`removed:true`) |
| `admin_member_create` | 구성원 필드 | 이미 있는 이름이면 "이미 있는 이름입니다." → 모두에게 `user_updated` + `admin_done {action:"member_create", username}`. 비밀번호 없이 만들어지므로 그 사람은 [처음 사용 등록]으로 시작 |
| `admin_member_update` | username + 바꿀 필드 | 회사 관리자는 `company` 를 자기 회사 밖으로 바꿀 수 없다 → `user_updated` + `admin_done {action:"member_update", username}` |
| `admin_member_delete` | username | 자기 자신 불가 "자기 계정은 삭제할 수 없습니다." 소프트 삭제(`removed=1`, `disabled=1`, 토큰 폐기, 접속 중이면 `account_disabled` 후 끊음). 대화 기록은 남는다. 모두에게 `user_removed {username}` + `admin_done {action:"member_delete", username}` |
| `admin_member_restore` | username | `removed=0`, `disabled=0` → `user_updated` + `admin_done {action:"member_restore", username}` |

- **관리자 계정은 그룹 관리자만 (2026-10-11):** 회사 관리자는 같은 회사 소속이라도 다른 관리자(그룹 · 회사 관리자)를 대상으로 한 비밀번호 재설정 · 등록 초기화 · 사용 중지 · 수정 · 삭제를 할 수 없다 — "관리자 계정은 그룹 관리자만 관리할 수 있습니다." (자기 자신은 예외). 삭제된 구성원에게 `admin_set_disabled {disabled:false}` 는 "삭제한 구성원입니다. [복원]으로 되살려 주세요." — 삭제된 구성원은 `disabled` 값과 상관없이 로그인할 수 없다.

- 삭제된 구성원은 일반 사용자 목록(welcome `users`, `all_users_list`, 조직도, 친구 찾기)에 나오지 않고 로그인할 수 없다(사용 중지와 같은 오류). 클라이언트는 `user_removed` 를 받으면 그 사람을 사용자 목록·조직도·친구 목록에서 뺀다.
- 관리자가 등록·수정한 구성원은 `users.org_locked=1` 이 되어, 서버를 다시 켤 때 `org.json` 시드가 그 사람의 조직 정보(회사·부서·직함·계정·내선·휴대폰·조직 경로)를 덮어쓰지 않는다. 삭제한 구성원은 시드가 되살리지 않는다.
- **서버 주소는 그룹 관리자만 (2026-10-10):** 로그인 창에는 서버 설정(주소 · 포트)이 없다. 접속 주소는 ① 명령줄 `--host`/`--port`(관리자 비상용) → ② 이 PC 의 `server.json`(`app_home()` 아래, `{host, port, tls}`) → ③ 저장된 로그인의 주소 → ④ 기본 127.0.0.1:5555 순서로 정한다. **TLS(2026-10-14):** `tls:true`(또는 `--tls`/`--no-tls`)면 클라이언트가 소켓을 TLS 로 감싼다(SNI=host, OS·certifi 신뢰 저장소로 인증서 검증). 운영 서버 `talk.kcins.co.kr:9550` 은 nginx stream 이 TLS 를 종단해 `127.0.0.1:9551` 의 서버로 PROXY protocol v1 과 함께 넘기고, 서버는 `--proxy-protocol` 로 첫 줄에서 실제 클라이언트 IP 를 복원한다(IP 기준 로그인 스로틀 §4.1 보존). NDJSON 프로토콜 자체는 바뀌지 않는다 — TLS 는 전송 계층이고, `tls` 는 클라이언트 PC 설정이다. `save_server_address` 에 `tls` 를 넘기지 않으면 기존 값을 보존한다(더보기 → 정보에서 주소만 바꿔도 TLS 가 풀리지 않게). `server.json` 은 **그룹 관리자**(`is_admin` 이고 `admin_company=""`, 예: `KCS관리자` / `kcs_administrator`)만 더보기 → **정보** 에서 고친다(주소 · 포트 칸 + [저장], 다음 로그인부터 적용). 정보 구역(회사 로고 · 버전 · 서버 주소)은 그룹 관리자에게만 보이고, 회사 관리자 · 일반 사용자에게는 구역째 숨긴다. 연결 실패 창에는 [다시 시도] [종료]만 있고 '관리자에게 문의' 안내를 보인다. 서버 프로토콜 바뀜 없음(클라이언트 PC 설정).
- **조직도 편집 모드 (2026-10-10):** 관리자(예: `KCS관리자` / 사내 계정 `kcs_administrator`)로 로그인하면 조직도 탭 헤더에 연필 [편집 모드] 버튼이 보인다(관리자가 아니면 없음). 켜면 헤더 아래 '편집 모드' 안내 줄([끝내기] 포함)이 나오고 `admin_users` 를 받아 사번 · 이메일까지 채운다. 편집 모드에서는 구성원 행을 누르거나 두 번 누르면 채팅 대신 구성원 창(MemberDialog, 부서 아래 '팀' 칸 포함)이 열리고, [저장]하면 바뀐 필드만 `admin_member_update` 로 보낸다(부서나 팀이 바뀌면 `department` · `team` 을 함께). 관리 범위 밖(회사 관리자의 다른 회사 사람) · 봇은 열지 않는다. 관리자는 편집 모드가 아니어도 구성원 우클릭 → '정보 수정…' 으로 같은 창을 연다. 서버 오류는 알림 창으로 보여 준다.
- 클라이언트: 계정 관리 창에 [구성원 등록…] [조회 · 수정…](행 두 번 클릭도) [삭제] [복원] 버튼. 구성원 창(MemberDialog)은 회사 콤보(회사 관리자는 자기 회사로 고정) + 필드 입력. 창 제목에 범위 표시 '관리자 · 계정 관리 (그룹 전체)' / '(케이씨에스)'.
### 4.4 조직도 그룹 (그룹 관리자, 2026-10-10)
- 그룹 관리자가 회사와 상관없이 사람을 묶어 조직도에 보이는 그룹(예: 경영위원회, TF). 서버 DB `org_groups(group_id, name UNIQUE, description, members JSON, sort_order, created_by, created_at)` — `org.json` 과 별개라 서버를 다시 켜도 남는다.
- Group 객체: `{group_id, name, description, members:[username], created_by, created_at}`. 삭제된 구성원은 `members` 에서 빠진다. `org_groups`/welcome `groups` 는 `sort_order` 순서(새 그룹은 맨 뒤, 순서 칸이 없던 DB 는 만든 순서로 채운다).
- 요청 (그룹 관리자만 — 관리자가 아니면 "관리자만 사용할 수 있습니다.", 회사 관리자면 "그룹 관리자만 할 수 있습니다."):

| 요청 | 필드 | 응답 |
|---|---|---|
| `admin_group_create` | name(1~40자), description?(0~100자), members?([username], 최대 500, 중복은 하나로) | 같은 이름이 있으면 "이미 있는 그룹 이름입니다." / 없는 사람이면 "구성원을 찾을 수 없습니다: <이름>" → 모두에게 `org_groups {groups}` + `admin_done {action:"group_create", group_id, name}` |
| `admin_group_update` | group_id + name, description?, members? (전체를 새 값으로) | 없으면 "그룹을 찾을 수 없습니다." → `org_groups` + `admin_done {action:"group_update", group_id, name}` |
| `admin_group_reorder` | group_ids([int], 지금 있는 그룹 전부를 원하는 순서로 한 번씩) | 빠지거나 없는 · 겹친 id 면 "그룹 목록이 바뀌었습니다. 조직도를 새로 보고 다시 시도해 주세요."(요청한 사람에게도 `org_groups` 를 다시 보내 화면을 되돌린다) → 모두에게 `org_groups` + `admin_done {action:"group_reorder", group_ids}` |
| `admin_org_order` | order(["group:<id>" \| "company:<회사>"], 최대 600, 중복 불가) | 조직도 아래쪽 덩어리 순서 — KCS AI(봇 회사) · 그룹사는 고정이라 넣지 않는다. 형식이 틀리면 "order 값이 올바르지 않습니다.", 없는 그룹이면 "그룹 목록이 바뀌었습니다. …"(`org_groups` 로 되돌림). 저장(`org_settings.lower_order`) + 그 안의 그룹 순서를 `sort_order` 에도 반영 → 모두에게 `org_groups {groups, order}` + `admin_done {action:"org_order", order}`. welcome `org.order` 에도 |
| `admin_group_delete` | group_id | 구성원 계정은 그대로 → `org_groups` + `admin_done {action:"group_delete", group_id}` |

- 클라이언트: 그룹 관리자에게만 조직도 탭 헤더에 [+ 그룹 만들기] 버튼(GroupDialog: 이름 · 설명 · 구성원 검색 · 체크). 그룹은 '그룹' 구역에 사람 아이콘 머리글 + 구성원(접기 가능, 검색: 그룹 이름이 맞으면 구성원 모두, 아니면 맞는 구성원만). 그룹 머리글 우클릭 → 그룹 수정… / 위로 이동 / 아래로 이동 / 그룹 삭제(확인 창). **순서 바꾸기:** 맨 위 KCS AI · 그룹사(지주사 · 계열사)는 고정이다. 그 아래 덩어리(만든 그룹들 · 게스트 · 그 밖의 회사)는 `org.order` 순서(없는 것은 기본 순서로 뒤에)로 그리고, 덩어리가 둘 이상이고 검색 중이 아니면 **그룹 관리자만** 머리글 전체를 잡고 끌 수 있으며(점 모양 손잡이는 표시하지 않는다 - 마우스를 올리면 손 모양 커서와 옅은 강조로 알린다. 일반 사용자 · 회사 관리자는 끌기 · 이동 메뉴 · 요청 모두 없음, 서버도 거부), 머리글을 끌면(시작 거리 이상) 놓을 자리에 강조색 표시선이 나온다. 놓을 자리는 아래쪽 덩어리 사이로만 정해진다(고정 영역 위로는 못 감). **끄는 동안(2026-10-10 개선):** 머리글이 좌우로 8px 들인 카드(`DragGhost`: 그림자 · 테두리 · 왼쪽 강조색 띠, 누른 지점을 잡은 채)로 떠서 커서를 따라오고, 원래 자리는 강조색 점선 빈칸(`DropSlotRow`)이 되며, 다른 덩어리가 실시간으로 비켜 선다(미리 보기 순서: 위 이웃은 카드 윗변이 이웃 가운데를 넘으면, 아래 이웃은 카드 아랫변이 넘으면 — 머리글 '위에' 놓아도 그 자리로 간다). 목록 위 · 아래 끝 36px 안에서는 저절로 스크롤, Esc 는 취소. 끌 수 있는 머리글은 마우스를 올리면 배경 `bg_hover` · 손 모양 커서. 행 위젯은 마우스를 받지 않고(목록 뷰포트가 받음) 툴팁은 목록 항목이 보인다. 놓으면 화면 순서를 바로 바꾸고 `admin_org_order` 를 보낸다(서버의 `org_groups` 가 최종). 우클릭 → 위로 이동 / 아래로 이동도 같다(게스트 머리글 포함). 조금만 움직이고 놓으면 보통 클릭(접기/펼치기)이다. `org_groups` 를 받으면 `AppState.org['groups']` 를 바꾸고 `org_changed` 시그널.

**서버 푸시(요청 없이)**
- `user_status_changed {username, status:"online"|"offline", timestamp}`
- `event_reminder {event, minutes_before}` — `start - remind_minutes` 시각에 응답이 "no"가 아닌 참석자(+생성자) 중 접속자에게 1회. 15초 주기 검사.
- 신규 사용자가 처음 join하면 다른 접속자에게 `user_updated {user}`.

## 5. 서버 구조

```
server/kakao/__init__.py
server/kakao/store.py     # SQLite(sqlite3) 영속 계층. Store(db_path). 스레드 안전(Lock).
server/kakao/server.py    # KakaoServer(host, port, db_path) – start()/serve_in_thread()/stop()
server/kakao/protocol.py  # ProtocolError · 필드 검사(_req_str 등) · parse_iso · 이메일/사번 정규식 (server 에서도 import 가능)
server/kakao/admin_handlers.py  # AdminHandlers 믹스인: 관리자 · 구성원 · 조직도 그룹 · 파일 전송 보안 요청 (§4.2~4.4, §10.10)
server/kakao/file_policy.py     # 파일 전송 보안 검사 (형태 · 확장자 · 이름, 내용 판별) (§10.10)
server/chat/chat_server.py  # 기존 진입점 → KakaoServer 실행 (CLI: --host --port --db)
```
- 기본 DB 경로: `<프로젝트>/server_data/kakao.db`
- 기본 사용자: 홍길동, 김철수, 이영희, 박민수, 최지우 (생일 샘플 포함)
- 첨부 최대 2MB(base64 디코딩 기준), 초과 시 error

## 6. 클라이언트 구조 (`client/kakao/`)

```
client/kakao/app.py           # 진입점: python client/kakao/app.py [--host --port --user]
client/kakao/net.py           # ChatConnection(QObject): 소켓 + 수신 스레드
client/kakao/state.py         # AppState(QObject): 모든 데이터 + 액션 + 시그널
client/kakao/settings.py      # 로컬 설정(즐겨찾기, 고정, 알림끔, 다크모드, 글자크기) JSON 저장
client/kakao/theme.py         # 색상 팔레트(라이트/다크), 폰트 체인, QSS 헬퍼
client/kakao/widgets.py       # Avatar, Badge, IconButton 등 공용 위젯(QPainter 그래픽)
client/kakao/login.py         # LoginDialog
client/kakao/main_window.py   # MainWindow: 사이드바 + 친구/채팅/캘린더/더보기 탭, 트레이 알림
client/kakao/friends_tab.py   # FriendsTab
client/kakao/chats_tab.py     # ChatsTab (+ 그룹 채팅 만들기 다이얼로그)
client/kakao/chat_room.py     # ChatRoomWindow: 말풍선, 읽음수, 답장, 공감, 삭제, 공지, 투표, 검색, 서랍, 이모티콘, 파일, 예약
client/kakao/room_base.py     # 채팅방 공용 바탕: _Dialog · _label · dialog_qss · 시간 표기 · icon_pixmap
client/kakao/room_dialogs.py  # 채팅방 대화상자: 초대 · 투표 만들기 · 사진 보기 · 서랍 · 사다리 · 톡게시판 · 연락처 · 요약 (chat_room 에서도 import 가능)
client/kakao/date_picker.py   # DatePickerCalendar: 날짜 칸 달력 팝업 ('2026년 10월' 머리글 · 오늘 고리 · 선택 동그라미 · 주말 색 · 다른 달 흐리게)
client/kakao/file_guard.py    # 파일 전송 보안: 막힌 파일 승인 요청 창 · 그룹 관리자 정책/승인/예외 창 (§10.10)
client/kakao/calendar_tab.py  # CalendarTab: 월간 달력 + 일정 목록 + 할 일
client/kakao/event_dialog.py  # EventDialog: 일정 생성/수정. 제목은 필수 - 비운 채 [저장]하면 제목 칸 아래 "일정 제목을 입력해 주세요." + 빨간 테두리 + 포커스 (저장 버튼은 종료가 시작보다 빠를 때만 꺼진다). 날짜 · 시간 칸은 마우스 휠로 값이 바뀌지 않고(창이 스크롤된다, 콤보 · 숫자 칸은 포커스가 있을 때만 - `widgets.blocks_wheel`), 등록하면 캘린더가 그 날짜로 옮겨 가고 '일정을 등록했습니다 · 2026. 10. 12. (월)' 토스트
client/kakao/more_tab.py      # MoreTab: 프로필 편집, 다크모드, 글자 크기
```

---

## 7. 확장 명세 v2.1 (추가 요구사항)

### 7.1 첨부 파일 송수신
- 최대 크기 **10MB**(디코딩 기준)로 상향. 이미지/파일 모두 상대방이 `attachment_get`으로 받아 저장 가능.
- 보내는 파일은 그룹 관리자의 파일 전송 보안 정책(§10.10)으로 검사한다.
- 클라이언트: 드래그&드롭, 클립보드 이미지 붙여넣기, 여러 파일 동시 선택 지원.

### 7.2 User 필드 추가
```text
User += {avatar_id:int|null,            # 프로필 사진(attachment_id), 없으면 null
         presence:"online"|"away"|"busy"|"meeting"|"vacation"|"offline",  # 미접속이면 "offline" (vacation 은 그대로, §10.2)
         company:str, department:str, title:str,               # 그룹사/부서/직함
         is_bot:bool}
```
- `online`(접속 여부)은 그대로 유지. 접속 시 presence는 마지막으로 설정한 값(기본 "online")으로 복원.
- 요청 `presence_set {presence}` ("online"/"away"/"busy"/"meeting"/"vacation") → 모두에게 `user_updated {user}`.
  접속/해제 시에도 `user_status_changed`와 함께 `user_updated`를 보낸다.
- `profile_update`에 `avatar_b64`(이미지, 디코딩 기준 ≤ 512KB) 또는 `avatar_clear:true` 추가 → 첨부로 저장, `avatar_id` 갱신 → `user_updated`.
  `avatar_clear`(클라이언트 [기본 이미지])는 사진을 그 사람의 **조직도 사진**(`users.org_avatar_id`, `scripts/import_org_photos.py`가
  회사 엑셀 좌석배치도에서 가져옴)으로 되돌린다. 조직도 사진이 없으면 `avatar_id=null`. 멀티프로필의 `avatar_clear`도 같다.
  프로필 사진은 누구나 `attachment_get`으로 받을 수 있다(방 멤버 여부와 무관).

### 7.3 그룹사(계열사) 조직도 & 교차 대화
- 시드 사용자(회사/부서/직함):

| 사용자 | company | department | title |
|---|---|---|---|
| 홍길동 | KCS 홀딩스 | 경영지원팀 | 팀장 |
| 김철수 | KCS 홀딩스 | 경영지원팀 | 대리 |
| 윤서준 | KCS 홀딩스 | 재무팀 | 차장 |
| 이영희 | KCS 물류 | 운영팀 | 과장 |
| 박민수 | KCS 물류 | 운영팀 | 사원 |
| 강민지 | KCS 물류 | 고객지원팀 | 대리 |
| 최지우 | KCS 커머스 | 마케팅팀 | 팀장 |
| 정하늘 | KCS 커머스 | 개발팀 | 선임 |
| AI 비서 | KCS AI | 지식봇 | 봇 (is_bot=true, 항상 online) |

- `<db 디렉터리>/org.json`(선택)이 있으면 `[{username, company, department, title, birthday}]`로 시드를 덮어쓴다.
- 처음 join하는 미등록 사용자는 company "게스트", department "", title "".
- 회사가 달라도 자유롭게 1:1/그룹 대화 가능. 클라이언트는 다른 회사 사용자에게 회사명 칩을 표시하고, 조직도 탭(회사 → 부서 → 구성원)을 제공한다.

### 7.4 상태(Presence) 표시 색
online `#3DD16B` · away `#F5B400`(자리비움) · busy `#FF4B3E`(다른 용무 중) · meeting `#8E6CEF`(회의 중) · vacation `#1FA2B8`(휴가 중) · offline `#BDBDBD`
클라이언트는 10분간 입력이 없으면 자동으로 away, 입력이 생기면 원래 상태로 복원한다(사용자가 직접 설정한 away/busy/meeting은 건드리지 않음). 입력은 컴퓨터 전체 기준이다: KCS Talk 창 밖(다른 앱)에서 마우스를 움직여도 입력으로 세고, 자동 away 중이면 3초 안에 online 으로 돌아온다(`osenv.system_idle_seconds()` - macOS 는 마우스 입력만, Windows · Linux X11 은 마우스 · 키보드, 알 수 없으면 마우스 포인터 위치 변화). 앱 안의 키 입력도 입력이다.

### 7.5 배경화면(클라이언트 로컬)
- 설정 `chat_wallpaper`: `null`(테마 기본) | `{"type":"color","value":"#RRGGBB"}` | `{"type":"gradient","value":["#..","#.."]}` | `{"type":"image","value":"<절대경로>"}`
- 설정 `room_wallpapers`: `{room_id: 위 형식}` — 방별 덮어쓰기.
- `client/kakao/wallpaper.py`: `resolve_wallpaper(settings, room_id)`, `paint_wallpaper(painter, rect, spec)`, `WallpaperDialog(settings, room_id=None, parent=None)`.

### 7.6 RAG(AI 비서)
- 봇 사용자 `AI 비서`. 다음 경우 서버가 답변 메시지(sender="AI 비서", kind="text")를 같은 방에 게시한다:
  (a) 봇이 포함된 1:1 방의 모든 text 메시지, (b) 봇이 멤버인 그룹 방에서 봇을 멘션(`@AI 비서`)한 메시지.
  답변 생성은 별도 스레드에서 하며, 생성 중에는 아무것도 보내지 않는다.
- 봇 1:1 방으로 보낸 텍스트형 파일(txt, md, csv, json, log, py, pdf)은 자동으로 지식베이스에 추가되고 봇이 "문서를 학습했습니다" 답변.
- 검색 범위: 질문자가 멤버인 방의 메시지 + 질문자가 올린 문서 + 공유 문서(owner="")
- 요청: `rag_doc_list` → `rag_doc_list {docs:[{doc_id,name,owner,size,chunks,created_at}]}` / `rag_doc_add {name, data_b64}` → `rag_doc_added {doc}` / `rag_doc_delete {doc_id}`(소유자만) → `rag_doc_deleted {doc_id}`
- 구현: `server/kakao/rag.py` `RagService(db_path)` — SQLite FTS5(trigram) 검색 + 생성기:
  `ANTHROPIC_API_KEY`와 `anthropic` 패키지가 있으면 Claude로 답변 생성, 없으면 검색 결과 기반 요약(추출형) 답변.

#### 7.6.1 사내 포털 RAG 연동 (portal.kcins.co.kr, 2026-10-13)
- `server/kakao/portal.py` `PortalRag(local, config, outbox_path)` 가 `RagService` 를 감싼다(같은 메서드 제공, 색인·문서 학습은 자체 RAG 에 위임). 배포 진입점 `server/chat/chat_server.py`(= `kcs.py server`)는 기본으로 켜고 `--no-portal` · `KCS_PORTAL=off` 로 끈다. `KakaoServer(portal=None)`(테스트 기본)이면 자체 RAG 만.
- **질문:** AI 비서 트리거(§7.6 (a)(b) 그대로 - 봇 1:1 방 · 그룹방 `@AI 비서`)가 오면 서버가 포털 RAG 에 묻고 그 답을 봇 메시지로 게시. 출처가 오면 답 끝에 `📎 출처 (사내 포털)` 목록. 포털이 준 대화 id 는 (방, 사용자)별로 기억해 다음 질문에 넘긴다(앞 대화 맥락).
- **대체:** 포털 연결 실패 · HTTP 오류 · 답 없음 → 자체 RAG 답 + "(사내 포털에 연결하지 못해 메신저 자료로 답했어요)". 실패 후 30초 동안은 포털을 건너뛴다(답 지연 방지).
- **저장:** 질문·답변 한 쌍을 매번 포털 저장 API 로 보낸다(포털 답 · 자체 답 모두, `answered_by: portal|local`). DB 폴더의 `portal_outbox.db` 보낼 함에 먼저 넣고 백그라운드 스레드가 보낸다. 실패하면 남겨 두고 `retry_seconds`(기본 60초)마다 순서대로 다시 보낸다(서버 재시작 후에도).
- **요청 (api_style=kcs, 기본):** `POST {base_url}{ask_path}`(기본 `/api/rag/ask`) `{question, query, user, user_name, room_id, conversation_id, history:[{role,content}] (최근 10, 지금 질문 제외), source:"kcs-talk", model?}` → 응답에서 답(`answer` · `data.answer` · `response` · `text` · `content` · `choices[0].message.content` · `result` 중 처음), 출처(`sources` · `references` · `citations` · `documents` - 문자열 또는 `{title|name, url|link}`), 대화 id(`conversation_id` · `session_id` · `chat_id` · `id`). 일반 텍스트 응답도 답으로 받는다.
  `api_style=openai`: `POST /api/chat/completions`(ask_path 를 바꾸면 그 경로) `{model?, messages, stream:false, user}`.
- **저장 요청:** `POST {base_url}{save_path}`(기본 `/api/rag/conversations`) `{source:"kcs-talk", conversation_id, user, user_name, room_id, room_name, answered_by, messages:[{role:"user",content,timestamp},{role:"assistant",content,timestamp}], created_at}`. 2xx 면 보냄.
- **인증 · 설정:** `{auth_header}: {auth_scheme} {token}`(기본 `Authorization: Bearer …`) + `headers`. 설정은 DB 폴더의 `portal.json`(예: `docs/portal.example.json`) 위에 환경변수 `KCS_PORTAL_URL` · `KCS_PORTAL_TOKEN` · `KCS_PORTAL_ASK_PATH` · `KCS_PORTAL_SAVE_PATH` · `KCS_PORTAL_STYLE` · `KCS_PORTAL_MODEL` · `KCS_PORTAL_TIMEOUT` 가 덮어쓴다. `verify_tls:false` 는 사내 사설 인증서용.
- **운영 연동 (2026-10-14, 포털 `ask-as`):** 기본값이 포털 kcs-rag 의 서비스 질문 API 로 바뀌었다 — `ask_path` `/api/chat/ask-as`, 인증 `X-Service-Token: {token}`(`auth_header` `X-Service-Token`, `auth_scheme` 빈 값; 토큰은 포털 `.env` `SERVICE_TOKEN`), `timeout` 90초, `save_conversations` **false**(포털이 질문자의 포털 대화 목록에 `[kcs-talk] …` 제목으로 직접 남긴다). 요청에 질문자 **`email`**(KCS Talk 계정 이메일 = 포털 계정)을 더 보내고(`KakaoServer._rag_answer` 가 `asker_email=` 로 넘김), 포털은 그 사람 권한(내 부서 문서 + 전사 공개 문서, master 는 전체)으로 gemma4 가 답하며 `{answer, sources:[{title, filename, page, section, kind, url?}], conversation_id, message_id, user_name, department}` 를 돌려준다(위 `parse_answer` 규칙으로 읽음). 포털에 없는 이메일이면 404 → 위 **대체** 흐름. 운영 서버는 `server_data/portal.json` 으로 `http://127.0.0.1:6031`(포털 API 컨테이너, 같은 호스트)을 직접 부른다. 예전 `/api/rag/ask` · 저장 API 모양은 설정으로 그대로 쓸 수 있다.
- 연결 점검: `python3 scripts/portal_check.py ["질문"]` - 설정을 읽어 질문 1건 · 저장 1건을 보내고 결과를 보여 준다.

### 7.7 조직도 v2 (2026-10-08)
- `server_data/org.json` v2: `{"version":2, "holding":{name,business,homepage,phone}, "companies":[{name,business,homepage,phone,units:[[경로]...]}], "include_demo_users":false, "users":[...]}` — `scripts/import_org_xlsx.py`가 회사 엑셀에서 생성.
- User 추가 필드: `org_path:[str]`(예: ["기술연구소","연구1실","2팀(SI)"]), `account`, `extension`, `mobile`, `concurrent:[{org_path,title}]`.
- `welcome`에 `org: {holding, companies, groups}` 포함(없으면 `{holding:null, companies:[], groups:[]}`). `groups` 는 §4.4.
- **카드 디자인 (2026-10-10, `OrgList`):** 연회색(`bg_alt`) 바닥 위에 덩어리마다 흰(`bg`) 카드(좌우 10px 여백, 모서리 10, `border` 테두리), 카드 사이 10px 간격. 카드 · 마우스 올림(`bg_hover`) · 선택(`bg_selected`) 둥근 바탕과 그룹사 계층선(그룹사 아이콘 타일 가운데에서 각 계열사로 ├ · 마지막 └)은 목록이 행 뒤에 그리고 행 위젯은 배경을 칠하지 않는다. 머리글 아이콘은 28px 둥근 타일(그룹사 · 내 회사는 네이비 옅은 바탕 + 네이비 아이콘, 나머지는 `bg_alt` + `text_sub`), 인원 · 곳 수는 알약. 모든 덩어리의 아이콘 타일은 같은 세로선(`TILE_X`)이다(점 모양 끌기 손잡이는 두지 않는다). 그 아래 구성원 사진 · 안내 문구는 머리글 글자 시작(`TITLE_X`)에서 시작한다(게스트 · KCS AI · 그룹). 끄는 카드(`DragGhost`)는 머리글을 흰 카드 위에 직접 그린다(`render`, 배경 없음 - macOS `grab()` 은 칠하지 않은 부분을 검게 채운다), 그림자도 그림 안에 직접.
- 조직도 탭 덩어리 사이에는 구분 간격(`DividerRow`, 키 `('divider', n)`, 높이 10)을 넣는다: 지식봇 | 그룹사 | 그룹 하나하나 | 게스트(맨 위 제외). 봇만 있는 회사는 부서 단계 없이 'KCS AI 1명' 바로 아래 봇을 보이고, 조직도 그룹 앞에는 '그룹' 같은 구역 문구를 두지 않는다.
- 조직도 탭 순서 (2026-10-10): **봇만 있는 회사(KCS AI 지식봇) 맨 위** → 접을 수 있는 **'그룹사 N곳' 머리글** 아래 한 단계 들여쓰기로 **지주사(`holding`)와 `companies[]` 를 모두 같은 단계**로(한국지주 → 계열사 13곳 순서, 지주사 단계 따로 없음, 부제목 '그룹사 N곳 · 구성원 M명'. 지식봇 · 그룹 · 게스트는 그룹사 머리글과 같은 바깥 단계) → '그룹'(§4.4 조직도 그룹) → 그 밖의 회사 → 게스트(맨 아래, 그대로).
- **처음 펼침 상태 (2026-10-10):** 조직도는 **내가 속한 곳만 펼친다** — 내 회사(주소속, 없으면 게스트) · 내 조직 경로(주소속 + 겸직의 `org_path`) · 내가 구성원인 조직도 그룹. 다른 회사 · KCS AI · 남의 그룹은 접혀 있고, 내 회사 안에서는 내 경로 옆 조직(같은 상위 아래 다른 부서 · 팀)만 접는다(접힌 곳 안쪽은 원래 기본대로라 열면 바로 보인다). 내 회사가 그룹사에 없으면 '그룹사' 머리글도 접는다. 항목마다 처음 볼 때 한 번만 정하고(`OrgTab._apply_default_collapse`), 그 뒤로는 사용자가 접고 편 상태를 유지한다. 검색 중에는 모두 펼친다.
- `companies[]` 의 그룹웨어 체계 회사(`depth:1`, `group_name`, `root_members`, `count_excludes_root`)는 조직도 탭에서 그룹사 아래 자기 순서 자리에 `회사 [👤N] › 직속 구성원 + 부서(접힘)` 로 그린다(법인명 단계 없음). `hidden_members:[username]`(관리자 계정)은 조직도·조직도 검색에서 숨기되, 그 계정 본인으로 로그인하면 숨기지 않고 전체를 보여 준다(클라이언트 표시만, 서버 User 목록은 그대로).
- `include_demo_users:false`면 기본 데모 사용자는 시드하지 않고 기존 DB의 데모 사용자는 company='게스트'로 내린다(AI 비서는 유지).

### 7.8 2025~2026 카카오톡 개편 반영 (deep-research 검증 결과 기반)
| 기능 | 프로토콜 / 동작 |
|---|---|
| 메시지 수정 | 요청 `edit {msg_id, content}` — 본인·kind=text·삭제 안 됨·**24시간 이내**만. Message에 `edited:bool`, `edited_at:ISO|null`. 성공 시 멤버에게 `msg_updated`. 수정된 내용으로 검색/RAG 색인 갱신 |
| 삭제 문구 | 삭제된 메시지 content = **"메시지가 삭제되었습니다"** (누가 지웠는지 표시하지 않음) |
| 조용히 보내기 | `send`에 `silent:true` → Message `silent:true`. 받는 클라이언트는 소리/트레이 알림을 띄우지 않지만 안 읽은 수에는 반영 |
| URL 미리보기 | text 메시지에 http(s) URL이 있으면 서버가 비동기로 첫 URL의 og:title/og:description/og:site_name(없으면 <title>)을 가져와 Message `link_preview:{url,title,description,site}`를 채우고 `msg_updated`. 3초 타임아웃, 256KB까지만 읽음. 서버 자신(127.x · ::1) · 링크 로컬(169.254.x) · 0.0.0.0 · 멀티캐스트 주소는 가져오지 않는다(리다이렉트 주소도 다시 검사, 사내망 10.x · 192.168.x 는 허용). 서버 옵션 `link_preview=True`(환경변수 `KCS_LINK_PREVIEW=off`로 끔) |
| 오늘의 브리핑(선톡) | 서버 옵션 `daily_briefing`(기본 False, 실행 파일은 True). 사용자가 그날 처음 접속하면 AI 비서 1:1 방에 오늘 일정·응답 필요한 일정·마감 할 일·안 읽은 메시지 수 요약을 봇이 먼저 보냄(하루 1회, users.last_briefing) |
| 채팅방 폴더 | 클라이언트 로컬 설정 `chat_folders:[{name, room_ids}]`. 기본 폴더: 전체 / 안읽음 / 즐겨찾기(고정된 방 + 즐겨찾기 친구와의 1:1). 안읽음 폴더에서 **읽지 않고 미리보기** |
| 일정 변경 알림 | 클라이언트: 이미 알고 있는 일정이 다른 사람에 의해 바뀌면(제목/시간/장소) 트레이 알림 "일정이 변경되었습니다" |
| 수정·삭제와 답장 | 원문을 수정·삭제하면 그 원문을 인용한 답장의 `reply_to.preview` 도 바꾸고 답장마다 `msg_updated` 를 보낸다(삭제된 원문 내용이 답장에 남지 않게). 이미 삭제된 메시지 수정은 오류 "삭제된 메시지는 수정할 수 없습니다." |
| 안 읽은 메시지 아이콘 깜박임 | 클라이언트 전용: `total_unread()`(알림 끈 방 제외) > 0 이고 설정 `blink_unread`(기본 켬)면 600ms 간격으로 트레이·앱·창 아이콘(macOS Dock·Windows 작업 표시줄) 전체를 평소 아이콘 ↔ 거의 투명(12%)한 같은 아이콘으로 번갈아 보여 준다(빨간 점 등 덧그림 없음). 0이 되면 멈추고 평소 아이콘. `reduce_motion`이면 깜박이지 않고 흐린(45%) 아이콘 고정. macOS 에서 `python3` 로 실행해도 Dock 이름은 'KCS Talk'(런처도): 요즘 Dock 은 실행 파일이 든 번들 이름(Python.app)만 보이므로 `client/kakao/macos.py` `run_in_app_bundle` 이 데이터 폴더 `macos/<번들 ID>/KCS Talk.app`(파이썬 실행 파일로의 심볼릭 링크 + Info.plist)에서 같은 프로세스를 다시 실행한다. 테스트(offscreen) · 빌드한 앱 · `KCS_NO_APP_BUNDLE=1` 은 그대로 |
| 채팅방 사이드 일정 | 채팅방 서랍에 '일정' 탭 (그 방에서 만든 일정) |
| AI 챗봇 | 채팅방 메뉴 "AI 비서 부르기"(초대), 채팅탭 상단 AI 버튼(봇 1:1 열기) |

## 8. v3 – 조사된 카카오톡 기능 전부 반영 (2026-10-08)

공통: 아래 요청/응답은 §2의 NDJSON 규칙을 따른다. 오류는 `error`.

### 8.1 비밀채팅 (종단간 암호화)
- 각 클라이언트는 X25519 키쌍을 `~/.kcs_messenger/keys/<username>.key`(KCS_MESSENGER_HOME 우선)에 보관. 접속 후 `key_publish {public_key_b64}` → User에 `public_key:str|null` 추가, 모두에게 `user_updated`.
- 방 종류 `secret` (1:1 전용): `room_open_secret {target}` → 기존 secret 방이 있으면 그 방, 없으면 생성 → `room_updated`. 상대 public_key가 없으면 오류 "상대방이 아직 비밀채팅을 사용할 수 없습니다".
- 메시지: `send {room_id, kind:'secret', content:<base64(nonce12 + AESGCM ciphertext)>}`. 키 = HKDF-SHA256(X25519(my_priv, peer_pub), salt=room_id, info=b'kcs-secret-v1', 32B). 평문은 UTF-8 JSON `{"text": "..."}`.
- 서버는 secret 메시지 내용을 해석하지 않음: 검색·RAG·URL 미리보기·답장 미리보기 제외, 알림 미리보기 "비밀채팅 메시지". secret 방에서는 file/image/poll/event 불가(오류).

### 8.2 통화 – 보이스톡 · 페이스톡(그룹 최대 10명) · 보이스룸 · 화면 공유
- Call = `{call_id:int, room_id, mode:'voice'|'video'|'voiceroom', host, started_at, participants:{username:{role:'host'|'speaker'|'listener', muted:bool, video:bool, screen:bool, hand:bool}}}`
- `call_start {room_id, mode}` → 방에 진행 중 통화가 있으면 오류. kind=`call` 메시지 게시(content=모드 이름, `call_id`) 후 멤버에게 `call_updated {call}`. voice/video: 시작자 role=host, voiceroom: host.
- `call_join {call_id}` → voice/video는 role=speaker(video 최대 10명, 초과 시 오류 "최대 10명까지 참여할 수 있습니다"), voiceroom은 listener(최대 1500명). → 멤버에게 `call_updated`.
- `call_leave {call_id}` → 참여자가 0명이거나, voiceroom에서 host가 모두 나가면 종료.
- `call_hand {call_id, raised:bool}` (voiceroom listener) · `call_role {call_id, username, role:'speaker'|'listener'|'host'}` (host만, speaker+host ≤ 10) · `call_state {call_id, muted?, video?, screen?}` → 각각 `call_updated`.
- 종료: `call_ended {call_id, room_id, duration_seconds}` + 시스템 메시지 "통화 종료 · M:SS" (보이스룸은 "보이스룸이 종료되었습니다"). 보이스룸은 생성 48시간 후 자동 종료(스케줄러).
- 미디어 중계: `call_media {call_id, kind:'audio'|'video'|'screen', seq:int, data_b64}` → 서버가 보낸 사람을 뺀 참여자에게 `call_media {call_id, from, kind, seq, data_b64}` 그대로 전달(저장 안 함). 보이스룸에서 audio는 host/speaker만, listener 것은 버림. 형식: audio = 16kHz mono s16le PCM 40ms 프레임(zlib 압축 가능, `kind:'audio'`에 `codec:'pcm'|'zpcm'` 필드), video/screen = JPEG.
- `call_list {room_id?}` → `call_list {calls}` (내가 속한 방의 진행 중 통화). welcome에도 `calls` 포함.

### 8.3 펑 (24시간 스토리)
- `story_post {kind:'text'|'image', content, attachment?{name,mime,data_b64}, bg_color?}` → Story `{story_id, owner, kind, content, attachment_id?, bg_color, created_at, expires_at, viewers:[username] (owner에게만 채움, 그 외 [])}`. 모든 접속자에게 `story_new {story}`.
- `story_list` → `story_list {stories}` (만료 안 된 것, 최신순). `story_view {story_id}` → 조회자 기록, 소유자에게 `story_updated {story}`. `story_delete {story_id}`(소유자) → 모두에게 `story_deleted {story_id}`. 24시간 지나면 스케줄러가 삭제하고 `story_deleted`.

### 8.4 번역 — 제외
사용자 요청으로 메시지 번역은 구현하지 않는다.

### 8.5 멀티프로필
- `multi_profile_set {profiles:[{profile_id?, name, display_name, status_message, avatar_b64?|avatar_clear?, viewers:[username]}]}` → 내 멀티프로필 전체 교체. `multi_profile_list` → `multi_profile_list {profiles:[{profile_id, name, display_name, status_message, avatar_id, viewers}]}`.
- 서버는 viewer에게 보내는 내 User의 display_name/status_message/avatar_id를 해당 프로필 값으로 바꿔서 보낸다(welcome, user_updated 모두 수신자별). 나 자신은 기본 프로필을 본다.

### 8.6 차단 / 숨김
- 숨김: 클라이언트 로컬 설정 `hidden_friends`.
- 차단: `block_set {username, blocked:bool}` → `block_list {blocked:[username]}` 응답. 차단하면 차단된 사람이 보낸 메시지(1:1·그룹 모두)를 나에게 push 하지 않고, history/last_message/unread_count/search 에서도 제외. 상대는 차단 사실을 알 수 없다. welcome에 `blocked:[...]`.

### 8.7 사다리 게임
- `ladder_create {room_id, participants:[str], results:[str]}` (2~10개, 개수 동일) → 서버가 무작위 사다리 생성: Message kind=`ladder`, `ladder:{participants, results, rungs:[[row, col], ...], mapping:{participant: result}}` (rows=10, col은 왼쪽 세로줄 인덱스, 같은 row에서 인접 가로줄 없음).

### 8.8 톡게시판
- `board_post {room_id, title, content}` → Post `{post_id, room_id, author, title, content, created_at, comments:[{comment_id, author, content, created_at}]}`; 방에 kind=`board` 메시지(content=title, `post_id`) 게시 + 멤버에게 `board_updated {post}`.
- `board_list {room_id}` → `board_list {room_id, posts}` · `board_comment {post_id, content}` → `board_updated` · `board_delete {post_id}`(작성자) → `board_deleted {post_id, room_id}`.

### 8.9 오픈채팅
- 방 종류 `open`: `open_room_create {name, description, tags:[str]}` → 만든 사람 포함 room_updated. Room에 `description`, `tags`, `nicknames:{username: nickname}` 추가.
- `open_room_search {query}` → `open_room_search {rooms:[{room_id, name, description, tags, member_count}]}` (참여 안 한 방 포함). `open_room_join {room_id, nickname}` → 멤버 추가 + 시스템 메시지 "<닉네임>님이 들어왔습니다" (오픈채팅에서는 실명 대신 닉네임 표시).

### 8.10 받은 파일 보관 기간 / 톡서랍
- 첨부 보관 기간: 서버 옵션 `retention_days`(기본 90). attachment 메타에 `expires_at`. 만료된 첨부는 스케줄러가 데이터 삭제, attachment 메타 `expired:true`, `attachment_get`은 오류 "보관 기간이 지난 파일입니다".
- 톡서랍(전체): `drawer_query {kind:'image'|'file'|'link', limit?}` → `drawer_result {kind, messages}` 내가 속한 모든 방에서 최신순.

### 8.11 클라이언트 전용
- 잠금모드: 4자리 PIN(설정 `lock_pin_hash` = sha256(salt+pin), `lock_salt`), N분(설정 `lock_minutes`, 기본 5) 무입력 시 또는 수동 잠금, 잠금 중 알림은 "새 메시지" 로만 표시.
- 키워드 알림: 설정 `alert_keywords:[str]` — 알림 끈 방이어도 키워드 포함 메시지는 알림 + 말풍선 강조.
- 채팅방별 알림 모드: 설정 `room_alert_modes:{room_id: 'all'|'mention'|'off'}` (off = 기존 muted_rooms와 동기).
- 말풍선 꾸미기: 설정 `bubble_style:{preset}` – 'basic'|'round'|'square'|'pastel'|'mono' 등, 내 말풍선 색/모서리.
- 기능 둘러보기: 더보기 → "기능 둘러보기" 체크리스트(기능·설명·해보기 버튼) + `scripts/demo.py`(서버 + 데모 데이터 + 클라이언트 여러 개 실행) + `scripts/verify_features.py`(기능별 테스트 결과 표).

### 8.12 친구 등록 (전화번호 · 이메일)
- User 에 `email` 추가. `profile_update` 에 `mobile`, `email` (형식 검증) 추가.
- `friend_add {query}` → 전화번호(숫자만 비교, +82→0) 또는 이메일(대소문자 무시)이 **정확히** 일치하는 사용자를 내 친구로 등록 → `friend_added {user, already}` + `friend_list {friends}`. 없으면 오류 "해당 전화번호·이메일로 가입한 사용자가 없습니다." (부분 일치·목록 조회 없음)
- `friend_add {username}` (2026-10-11): 프로필 창의 [친구 추가] — 보고 있는 사람을 연락처 없이 바로 등록(다른 사람의 이메일은 오지 않으므로). 없는 · 삭제된 사용자 · 봇이면 "사용자를 찾을 수 없습니다."
- `friend_remove {username}` / `friend_list` → `friend_list {friends}`. welcome 에 `friends`.

### 8.13 대직자 (2026-10-11)
- User 에 `deputies`(대직자 username 목록, 순서 유지, 없으면 []) 추가. 내가 자리에 없을 때 대신 업무를 맡는 사람.
- `profile_update` 에 `deputies:[username]` → 중복 제거 후 저장 → `user_updated`. 최대 10명. 나 자신 · 봇 · 없는(삭제된) 사용자는 오류 "대직자로 지정할 수 없는 사용자입니다: {이름}". 빈 목록이면 지정 해제.
- 클라이언트: 프로필 편집 '대직자' 칸에서 이름 · 부서 · 팀 · 직함으로 검색 → 한 명이면 바로 추가, 여럿이면 체크 목록 팝업에서 골라 추가, 칩의 ✕ 로 빼기. 프로필 보기에 "대직자 김철수 책임 외 n명".

---

## 9. v4 — 카카오톡 2026 업데이트 반영 (2026-10-09)

출처: 카카오 정기 업데이트 발표 (2차 3월, 4차 5월 v26.4.0, 6차 7월 v26.6.0) + PC 카카오톡 '＋' 메뉴·대화 내보내기.

| 카카오톡 | KCS Talk |
|---|---|
| v26.6.0 채팅방별 AI 대화 요약 | §9.1 `room_summary` (Claude, 실패 시 추출형) + 안 읽은 메시지 20개 이상이면 '대화 요약 보기' 칩 |
| v26.4.0 말풍선 리액션 6종 → 114종, 말풍선당 최대 30개 | §9.2 공감 72종 선택 창, 말풍선당 30종 상한, 6종 넘으면 +N |
| PC ＋ 메뉴: 음성메시지 · 연락처 | §9.3 kind `voice` · `contact` |
| v26.4.0 채팅탭 '통화' 폴더 | §9.4 `call_history` + 기본 폴더 '통화' |
| PC 대화 내보내기 · 대화 내용 저장 | §9.5 `room_export`(기간) + 텍스트 · 엑셀용 표 · 사진·파일 포함 zip |
| 2차: 친구 소식(피드) 숨기기 | §9.6 클라이언트 설정 `hidden_story_users` |

### 9.1 대화 요약
- `room_summary {room_id, unread_only?:bool, since_msg_id?:int}` → (비동기) `room_summary {room_id, summary, source:'claude'|'extractive', count, unread_only}`.
- 대상: 최근 300개 중 삭제·시스템·비밀 메시지를 뺀 것. `since_msg_id`(또는 unread_only 면 내 읽음 위치) 이후만.
- 비밀채팅방은 오류(서버가 내용을 모름). Claude 는 RAG 서비스의 클라이언트를 재사용하고, 꺼져 있거나 실패하면 추출형(참여자·키워드·[결정]/[요청]/[일정]/[질문] 문장·공유 수).

### 9.2 공감 확대
- `react` 에서 새 종류를 추가할 때 이미 30종이면 오류 "공감은 메시지 하나에 30종류까지 달 수 있습니다." (이미 있는 종류에 더하기·취소는 가능)

### 9.3 음성메시지 · 연락처
- `send {kind:'voice', content:'<초 1~300>', attachment:{name, mime:'audio/*', data_b64}}` — 첨부는 file 과 같이 저장·보관 기간 적용, 톡서랍 '파일'에 포함. 클라이언트는 16kHz mono WAV.
- `send {kind:'contact', content:'<username>'}` — 존재하는 사용자만. 받은 쪽은 카드(1:1 채팅·프로필). 전달 가능.
- 비밀채팅방에서는 둘 다 불가(기존 규칙).

### 9.4 통화 기록
- 서버는 통화가 끝나면 `call_log(call_id, room_id, mode, host, started_at, duration, participants)` 저장 (participants = 한 번이라도 참여한 사람).
- `call_history {limit?}` → `call_history {calls:[{call_id, room_id, mode, host, started_at, duration_seconds, participants, joined, missed}]}` 내가 속한 방, 최근 순. `missed = 내가 참여하지 않음`.
- 클라이언트: welcome 직후·`call_ended` 때 다시 요청. 기본 폴더 '통화'(칩 숫자 = 부재중 수, 0이면 숫자 없음)에서 통화 기록 행을 보여 주고 누르면 그 방을 연다.

### 9.5 대화 내용 저장 (내보내기)
- `room_export {room_id, since?, until?}` → `room_export {room_id, messages(이 조각, 오래된 순, 최대 20000, 차단 사용자 제외), part, done, truncated, since, until}` (2000개씩 조각, 클라이언트는 done 까지 모은다). `since`/`until` 은 `YYYY-MM-DD`(그날 포함, 메시지 timestamp 의 날짜로 비교) — 주면 그 기간만 보내고, 서버는 기간 시작보다 오래된 쪽까지 읽으면 멈춘다. 형식이 틀리면 `error` "기간은 YYYY-MM-DD 형식으로 보내 주세요.", since > until 이면 "시작 날짜가 끝 날짜보다 늦습니다.". 방 구성원만.
- 진입: 채팅방 메뉴(☰) → '대화 내용 저장…', 채팅 목록 우클릭 → '대화 내용 저장…'(그 방을 열고 같은 창). 옵션 창(`client/kakao/chat_export.py` ExportDialog): 형식 **텍스트(.txt)** / **엑셀용 표(.csv)** / **사진·파일 포함(.zip)**, 기간 **전체 · 오늘 · 최근 7일 · 최근 30일 · 기간 지정(시작~끝 날짜)**. 고른 뒤 저장 창(확장자 자동). 연결이 없으면 바로 안내, 60초 안에 응답이 없으면 저장 중 상태를 풀고 안내.
- .csv: UTF-8 BOM, 열 `날짜, 시각, 보낸 사람, 종류, 내용, 첨부 파일` (엑셀에서 한글이 바로 열린다). `= + - @` 로 시작하는 내용은 앞에 `'` 를 붙인다(수식 주입 방지).
- .zip: `대화.txt` + `대화.csv` + `files/<msg_id>_<파일 이름>`(사진·파일·음성 원본). 첨부는 `attachment_get` 으로 하나씩 받는다(첨부당 20초 넘으면 건너뜀). 보관 기간이 지났거나 받을 수 없는 첨부는 빠지고, 텍스트·CSV 의 그 줄 끝에 `[포함되지 않음: 사유]`, 포함된 첨부는 `[files/…]`. 모으는 동안 첨부 오류는 알림을 띄우지 않는다(AppState `attachment_failed` 신호로만). 비밀채팅·삭제된 메시지의 첨부는 담지 않는다.
- 텍스트 형식: `<방 이름> 님과 카카오톡 대화` / `저장한 날짜 : YYYY-MM-DD HH:MM:SS` / `--------------- YYYY년 M월 D일 X요일 ---------------` / `[이름] [오전|오후 H:MM] 내용` (시스템 메시지는 내용만, 첨부 등은 미리보기 문구). 기간을 고르면 머리에 `저장한 기간 : YYYY-MM-DD ~ YYYY-MM-DD`. 나에게서 삭제한 메시지는 뺀다. 비밀채팅은 이 PC에서 해독한 내용.

### 9.6 친구 소식 숨기기
- 설정 `hidden_story_users:[username]`. 숨긴 사람의 펑은 펑 줄에서 빠지고, 친구 관계·채팅은 그대로. 친구 우클릭 '소식 숨기기/소식 다시 보기', 펑 동그라미 우클릭 '소식 숨기기'.

### 9.7 보내지 못한 메시지 (클라이언트 전용, 프로토콜 변경 없음)
- `send`가 연결 없음으로 나가지 못하면(text·emoticon) 로컬 메시지 `{msg_id: 10^12+n, failed:true, ...}`를 만들어 대화 맨 아래에 표시하고 설정 `failed_messages`에 저장(앱 재시작 후 복원).
- 실패 메시지는 읽음 처리·안 읽은 수·이전 기록 요청에서 제외. 재전송 = 로컬 메시지 제거 후 일반 `send`(다시 실패하면 새 실패 메시지). 삭제 = 로컬에서만 제거.
- 파일·사진·비밀 메시지는 보존하지 않고 "서버 연결이 끊겨 … 보내지 못했습니다. 연결된 뒤 다시 보내 주세요." 안내.

---

## 10. v5 — 업무용 메신저 필수 기능 (2026-10-10)

출처: deep-research 조사(카카오워크·네이버웍스·잔디·Slack·Teams 공식 도움말·앱 소개, 검증 통과 22개 주장).
필수로 분류된 기능 중 KCS Talk 에 없던 것만 추가한다. 2단계 인증·비밀번호 만료 정책은 메일/SMS 발송 인프라가 필요해 이번 범위에서 제외.

| 조사 결과 (필수) | KCS Talk |
|---|---|
| 방해금지 · 근무 시간 알림 · VIP 예외 (Slack, 네이버웍스) | §10.1 클라이언트 설정 `dnd_until` · `work_hours` · `vip_users` |
| 조직도에서 휴가 상태 확인 (카카오워크) | §10.2 presence `vacation` |
| 방별 관리자 (잔디) | §10.3 Room `owner` + `room_kick` · `room_owner_set` |
| 스레드 답장 (Slack, Teams) | §10.4 `send.thread_root_id` · `thread_get` |

### 10.1 방해금지 (클라이언트 전용, 프로토콜 변경 없음)
- 설정 `dnd_until`: ISO 시각 — 그때까지 알림 일시중지. `''` = 사용 안 함, `9999-12-31T23:59:59` = 직접 끌 때까지. 프리셋: 30분 · 1시간 · 2시간 · 내일 오전 9시까지 · 직접 끌 때까지.
- 설정 `work_hours`: `{enabled, days:[0=월..6=일], start:'HH:MM', end:'HH:MM'}` (기본 월~금 09:00~18:00, 꺼짐). 켜면 근무 시간 밖에는 알림 없음. `start > end` 는 밤샘 근무(자정 이후는 전날 요일로 판단), `start == end` 는 그 요일 종일.
- 설정 `vip_users`: 방해금지 중에도 알림을 받는 사람 (최대 30명, 나 자신 불가).
- `AppState.should_alert` 순서: 조용히 보내기 → **방해금지(보낸 사람이 VIP 가 아니면 알림 없음 — 키워드·멘션 알림도 막는다)** → 키워드 → 방별 알림 모드. 안 읽은 수·아이콘 깜박임은 그대로. 관리자 공지·내보내짐 안내는 방해금지와 관계없이 표시.
- UI: 더보기 → 방해금지 · 키워드 · 꾸미기 → 방해금지(`client/kakao/dnd.py` DndDialog, 설명에 현재 상태 요약), 트레이 메뉴 → 알림 일시중지(프리셋 · 일시중지 끄기).

### 10.2 휴가 상태
- presence 에 `vacation`(라벨 '휴가 중', 색 `#1FA2B8`) 추가. `presence_set {presence:'vacation'}`.
- 다른 상태는 미접속이면 `offline` 이지만, **`vacation` 은 미접속이어도 `vacation`** 으로 보낸다(`online` 필드는 실제 접속 여부). 클라이언트 `presence_of` 도 같은 규칙. 자동 자리비움은 `online` 일 때만 바꾸므로 휴가를 건드리지 않는다.

### 10.3 방장
- Room 에 `owner: username|null` — group/open 방만(그 외 null). 만든 사람이 방장. 저장된 방장이 방에 없으면(예전 방 포함) 가장 먼저 들어온 봇이 아닌 멤버.
- 방장이 `room_leave` 하면 남은 사람 중 가장 먼저 들어온 사람을 방장으로 저장하고 시스템 메시지 "<이름>님이 방장이 되었습니다."
- `room_kick {room_id, username}` — 방장만("방장만 할 수 있습니다."), 자기 자신 불가, 1:1·나와의 채팅·비밀채팅은 "그룹 채팅방과 오픈채팅방에서만 할 수 있습니다.". 내보낸 사람에게 `room_removed {room_id, kicked:true}`(클라이언트 알림 "'<방>' 채팅방에서 내보내졌습니다."), 진행 중 통화에서도 빠짐. 남은 사람에게 `room_updated` + "A님이 B님을 내보냈습니다."
- `room_owner_set {room_id, username}` — 방장만, 대상은 멤버(AI 비서 불가) → `room_updated` + "B님이 방장이 되었습니다."
- 오픈채팅방에서 내보낸 사람은 `room_bans` 에 남아 `open_room_join` 이 "방장이 내보낸 오픈채팅방에는 다시 들어갈 수 없습니다." 로 거절된다. 그룹방은 다른 멤버가 다시 초대할 수 있다.
- 이름 변경·공지·초대는 지금처럼 모든 멤버가 할 수 있다.
- UI: 대화상대 목록 팝업에 '방장' 표시, 내가 방장이면 다른 사람 줄의 ⋯ → 방장 넘기기 · 내보내기(확인 창).

### 10.4 스레드
- Message 에 `thread_root:int|null`(스레드 답글이면 원글 id), `thread_count:int`, `thread_last_at:ISO|null`(원글에만 의미).
- `send` 에 `thread_root_id` — 원글은 같은 방, 삭제·시스템 메시지·스레드 답글이 아니어야 함(오류 "스레드 답글에는 다시 스레드를 달 수 없습니다." / "이 메시지에는 스레드를 달 수 없습니다." / "스레드 원글을 찾을 수 없습니다."). 비밀채팅방 불가. `send_at` 과 함께 쓰면 "스레드 답글은 예약해서 보낼 수 없습니다."
- 답글은 일반 메시지처럼 저장·`msg_new` 되고(안 읽은 수·마지막 메시지·검색·알림 포함), 서버는 원글의 `thread_count`/`thread_last_at` 을 올려 `msg_updated` 를 보낸다.
- `thread_get {root_id}` → `thread {root_id, room_id, root, messages}` (답글 오래된 순, 최근 500개, 차단한 사람 제외). 답글 id 를 주면 그 원글의 스레드. 방 구성원만.
- 클라이언트: 대화창 본문(`AppState.timeline_messages`)에는 답글을 그리지 않고 원글 아래 '답글 N개 · 마지막 시각' 링크. 말풍선 우클릭 → '스레드로 답장' → 스레드 창(`client/kakao/thread_view.py`, 원글마다 하나, 비모달). 채팅 목록 미리보기는 `↳ ` 로 시작. 검색 결과 등으로 답글에 이동하면 스레드 창을 연다. 답글 전송이 연결 끊김으로 실패하면 스레드 창에 안내하고 입력은 남긴다(보내지 못한 메시지 보관 §9.7 대상 아님).

### 10.5 멘션 모아보기 (Slack 'Mentions', Teams 'Activity')
- `mention_list {limit?:int=100 (1~200)}` → `mention_list {messages}` — 내가 속한 방에서 다른 사람이 나를 멘션한 메시지(`mentions` 에 내 이름), 삭제·차단한 사람 것 제외, 최신순.
- 클라이언트 `AppState.mentions`(최신순) · `mentions_changed`. 새로 받은 `msg_new` 가 나를 멘션하면 목록 맨 앞에 넣는다.
- UI: 채팅 탭 ⋯ → '@ 나를 멘션한 메시지' (`client/kakao/inbox.py` MentionsDialog). 줄 두 번 누르기·[이동] → 그 방의 그 메시지(스레드 답글이면 스레드 창).

### 10.6 안 읽은 사람 보기 (카카오워크 읽음 확인)
- 클라이언트 전용. `AppState.unread_members(room_id, message)` = `read_pointers` 가 메시지 id 보다 작은 멤버(이름순).
- 말풍선 옆 읽음 숫자를 누르면 '안 읽은 사람 N' 팝업(프로필·상태 점·소속). 숫자에 툴팁·접근성 이름 '안 읽은 사람 N명 보기'.

### 10.7 책갈피 (Slack 'Save for later', 카카오톡 책갈피)
- 클라이언트 설정 `saved_messages:[{room_id, msg_id, sender, thread_root, preview, timestamp, saved_at}]` (최근 저장 순, 최대 200).
- 말풍선 우클릭 → '책갈피' / '책갈피 해제'. 채팅 탭 ⋯ → '책갈피 (저장한 메시지)' (SavedDialog: 이동 · 책갈피 해제). 나간 방의 책갈피는 '이 채팅방에서 나가서 열 수 없습니다.'

### 10.8 입력 임시저장
- 클라이언트 설정 `room_drafts:{room_id: text}` (4000자까지). 입력이 0.8초 멈추면·창을 닫거나 숨기면 저장, 공백뿐이면 지움. 메시지 수정 중이면 수정 전 입력 내용을 저장.
- 채팅방을 다시 열면 입력창에 되살리고 커서를 끝으로. 채팅 목록 미리보기는 그 방이 열려 있지 않을 때 `[임시저장] <글>` (보내지 못한 메시지 안내가 우선).

### 10.9 디자인 품질 기준 (품질 하네스 11번 영역)
- 글자/배경 토큰 16쌍 WCAG 명암비: 본문 4.5:1, 읽음 숫자 3:1 (라이트·다크). 다크 `mention` = `#80ACF2`.
- 아이콘 버튼·체크박스 클릭 영역 24px 이상, 아이콘만 있는 버튼은 접근성 이름 또는 툴팁 필수.
- 메인 탭 5개·채팅방·모든 대화상자에서 100%·200% 글자 크기 모두 잘린 글자 없음 — 줄바꿈이 필요한 안내·조직명·삭제 문구는 `wordWrap`, 공지 말줄임 폭은 실제 위젯 크기로 계산.
- 메인 창 최소 크기는 레이아웃이 정한다(숫자 고정 금지) — 200%에서 창이 내용보다 좁아지지 않는다.

### 10.10 파일 전송 보안 (외부 유출 방지, 2026-10-11)
메시지 첨부(`send` 의 image · file · voice, 예약 · 전달 포함)를 서버가 **그룹 관리자 정책**으로 검사한다. 판단은 언제나 서버(`server/kakao/file_policy.py`)가 하고, 클라이언트는 이름 · 확장자만 미리 봐서 올리기 전에 알려 준다.

- **정책** (`org_settings.file_policy`, 저장한 적 없으면 기본값): `{enabled, blocked_types:[형태], blocked_extensions:[확장자(점 없이 소문자, 100개)], blocked_names:[파일 이름 규칙(60자, 100개)]}`. 기본값은 켜짐 + 형태 `executable` · `database` · `credential` + 이름 `*대외비*` `*기밀*` `*confidential*`.
- **형태** (`categories`): `executable` 실행 파일 · 스크립트 / `archive` 압축 / `database` 데이터베이스 · 백업 · 메일함 / `credential` 인증서 · 암호 키 / `source` 소스 코드 / `document` 문서 / `image` / `video` / `audio`. 확장자와 **파일 내용의 첫 바이트**(MZ · ELF · Mach-O · `#!` · PK · 7z · RAR · gzip · SQLite · `-----BEGIN` · OLE · PDF · 이미지) 둘 다로 판별한다 — `보고서.txt` 로 이름만 바꾼 실행 파일도 '실행 파일'(상세: "확장자는 .txt 이지만 실제 내용은 실행 파일 · 스크립트 형태예요"). docx · xlsx · pptx · hwpx 등은 zip 틀이어도 문서(안에 `[Content_Types].xml` · `mimetype` · `META-INF/manifest.xml` 이 있어야 문서, 없으면 이름만 바꾼 압축 파일), jar · apk 는 실행 파일. zip 은 안에 든 파일 이름(최대 2000개)도 같은 규칙으로 본다.
- **파일 이름 규칙**: `*` `?` 가 있으면 이름 전체와 맞춰 보고(fnmatch), 없으면 이름에 들어 있으면 막는다. 대소문자 무시.
- 검사 순서: 파일 이름 → 확장자 → 형태(확장자) → 형태(내용) → zip 안 파일. 위반 = `{rule:"name"|"extension"|"type", value, detail, category}`.
- **막히면** `send` 는 오류 대신 요청자에게 `file_blocked {room_id, kind, name, size, client_tmp_id, rule, value, detail, category, sha256, message}` 를 보내고 저장 · 전달하지 않는다.
- **예외** (`file_exceptions`): 그룹 관리자가 **특정인**에게 **특정 파일**(sha256 지문) 또는 **파일 이름 규칙**을 기간(1~365일) 동안 풀어 준다. 그 사람이 보내는 파일이 예외와 맞으면 정책 위반이어도 보낸다(서버 로그에 남김). 파일 이름 예외는 확장자와 실제 내용이 다른(막힌 형태를 숨긴) 파일에는 쓰지 않는다.
- **승인 요청**: 막힌 사람은 사유를 적어 `file_send_request {room_id, kind(image|file), content?, attachment, reason(1~300자)}` → 파일은 서버에 보관(전송 안 됨) → 요청자 `file_request_ok {request}`, 접속 중인 그룹 관리자 모두 `file_request_new {request}`. 막히지 않는 파일이면 바로 전송. 한 사람의 대기 요청은 20건까지. 비밀채팅방 불가.
  - Request: `{request_id, username, room_id, room_label, kind, content, attachment_id, name, size, sha256, rule, detail, reason, status:"pending"|"approved"|"rejected", decided_by, decided_at, note, msg_id, created_at}`
  - `file_request_list` → `file_requests {requests}` (내 요청, 최신순 100건)
- **그룹 관리자 요청** (회사 관리자 · 일반 사용자는 "그룹 관리자만 할 수 있습니다." / "관리자만 사용할 수 있습니다."):

| 요청 | 필드 | 응답 |
|---|---|---|
| `admin_file_policy_set` | policy | 검사 후 저장 → 모두에게 `file_policy {policy}` + `admin_done {action:"file_policy"}` |
| `admin_file_requests` | status? | `admin_file_requests {requests}` (최신순 200건) |
| `admin_file_request_decide` | request_id, approve(bool), note?(300자), allow_days?(0~365) | 대기 중인 것만("이미 처리된 요청입니다."). **승인하면 그때 요청한 사람 이름으로 그 방에 전송**(요청자가 방을 나갔으면 오류), `allow_days>0` 이면 같은 파일(sha256)을 그 사람에게 그 기간 허용하는 예외도 만든다. 반려하면 보관 파일을 지운다. 요청자 + 그룹 관리자에게 `file_request_updated {request}` + `admin_done {action:"file_request_decide", username, request_id, approved}` |
| `admin_file_exceptions` | – | `admin_file_exceptions {exceptions:[{exception_id, username, sha256, file_name, pattern, expires_at, note, request_id, created_by, created_at}]}` (만료 안 된 것) |
| `admin_file_exception_add` | username, days(1~365), pattern? 또는 sha256?, file_name?, note? | `admin_done {action:"file_exception_add", username, exception}` |
| `admin_file_exception_delete` | exception_id | `admin_done {action:"file_exception_delete", exception_id}` |

- 그룹 관리자는 보관 중인 요청 파일을 `attachment_get` 으로 받아 내용을 확인할 수 있다.
- welcome 에 `file_policy` (정책 + `categories:[{key, label, extensions}]`).
- 클라이언트: 막히면 '보낼 수 없는 파일이에요' 창(파일 이름 · 막힌 이유 · 크기, 사유 입력 → [승인 요청]). 결과는 알림 · 토스트. 관리자 → 계정 관리 → [파일 전송 보안…](그룹 관리자만): [정책](켜기, 형태 체크, 확장자, 파일 이름) · [승인 요청](대기 중만 보기, 사유, 메모, '같은 파일 다시 허용' 일수, [파일 받아 확인…] [반려] [승인하고 보내기]) · [예외](사람 · 파일 이름 · 기간 추가, 삭제).
