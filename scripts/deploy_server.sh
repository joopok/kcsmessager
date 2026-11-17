#!/usr/bin/env bash
# KCS Talk 서버 배포 - 서버의 프로젝트 폴더에서 실행한다 (docs/DEPLOY_CHECKLIST.md)
#
#   bash scripts/deploy_server.sh                   미리 보기: 무엇을 할지와 점검 결과만 보여 준다 (아무것도 바꾸지 않는다)
#   bash scripts/deploy_server.sh --apply           실제 배포: 백업 → 점검 → 코드 받기 → 다시 시작 → 확인
#   bash scripts/deploy_server.sh --apply --branch main      다른 브랜치로 바꿔서 배포
#
# 선택: --db <경로> (기본 server_data/kakao.db)  --port <번호> (기본 ecosystem.config.cjs 의 --port)
# 환경변수(시험용): KCS_PM2 · KCS_GIT · KCS_PYTHON 으로 pm2 · git · python 명령을 바꿀 수 있다
#
# 순서 (--apply)
#   1 백업                scripts/backup_db.py - 실패하면 멈춘다
#   2 코드 받기           지금 브랜치(또는 --branch)를 빨리 감기(fast-forward)로만. 갈라져 있으면 시작하지 않는다
#   3 의존성              requirements-kcs.txt 의 '서버:' 항목이 바뀌었으면 .venv 에 설치
#   4 다시 시작           pm2 startOrReload ecosystem.config.cjs --update-env → pm2 save
#   5 다시 시작한 뒤 확인 프로세스 · ping/pong · 등록 닫힘 · 로그 (로그인은 하지 않는다)
#   결과 요약             실패하면 되돌리는 방법을 보여 준다 (자동으로 되돌리지 않는다)
# 그 전에 늘 하는 것: 배포 전 점검 scripts/predeploy_check.py (DB 읽기 전용) - 겹치는 로그인 아이디가 있으면 시작하지 않는다
#
# 미리 보기도 원격 저장소의 새 커밋 목록을 보려고 git fetch 는 한다 (작업 폴더 · DB · 서버는 그대로).
# 비밀값(portal.json · 토큰)은 읽지도 출력하지도 않는다.

set -u

APPLY=0
BRANCH=''
DB='server_data/kakao.db'
PORT=''

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --branch) BRANCH="${2:-}"; shift ;;
        --db) DB="${2:-}"; shift ;;
        --port) PORT="${2:-}"; shift ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "모르는 옵션입니다: $1  (--help 로 사용법)" >&2; exit 2 ;;
    esac
    [ $# -gt 0 ] && shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf '\n✗ %s\n' "$*" >&2; }
short() { printf '%s' "$1" | cut -c1-7; }

ECO='ecosystem.config.cjs'
for needed in kcs.py "$ECO" scripts/backup_db.py scripts/predeploy_check.py scripts/postdeploy_check.py; do
    if [ ! -f "$needed" ]; then
        fail "프로젝트 폴더에서 실행해 주세요 ($needed 이(가) 없습니다)."
        exit 2
    fi
done

PM2="${KCS_PM2:-pm2}"
GIT="${KCS_GIT:-git}"
if [ -n "${KCS_PYTHON:-}" ]; then
    PY="$KCS_PYTHON"
elif [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
else
    PY=python3
fi

# 실행 설정은 ecosystem.config.cjs 에서 읽는다 (주소 · 경로를 이 스크립트에 적어 두지 않는다)
APP_NAME="$(sed -n "s/.*name: *'\([^']*\)'.*/\1/p" "$ECO" | head -n 1)"
ECO_ROOT="$(sed -n "s/^const ROOT *= *'\([^']*\)'.*/\1/p" "$ECO" | head -n 1)"
ARGS_LINE="$(grep -E '^[[:space:]]*args:' "$ECO" | head -n 1)"
if [ -z "$PORT" ]; then
    PORT="$(printf '%s' "$ARGS_LINE" | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
fi
CHECK_FLAGS=''
case "$ARGS_LINE" in *--proxy-protocol*) CHECK_FLAGS="$CHECK_FLAGS --proxy-protocol" ;; esac
case "$ARGS_LINE" in *--open-registration*) CHECK_FLAGS="$CHECK_FLAGS --expect-open" ;; esac
HERE="$(pwd -P)"

if [ -z "$APP_NAME" ] || [ -z "$PORT" ]; then
    fail "$ECO 에서 프로세스 이름이나 --port 를 찾지 못했습니다. --port 로 알려 주세요."
    exit 2
fi
if [ ! -f "$DB" ]; then
    fail "DB 파일이 없습니다: $DB  (--db 로 경로를 알려 주세요)"
    exit 2
fi

CURRENT_BRANCH="$("$GIT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
PREV_COMMIT="$("$GIT" rev-parse HEAD 2>/dev/null)"
if [ -z "$PREV_COMMIT" ]; then
    fail 'git 저장소가 아닙니다.'
    exit 2
fi
TARGET_BRANCH="${BRANCH:-$CURRENT_BRANCH}"
UPSTREAM="origin/$TARGET_BRANCH"
BACKUP_DIR=''

rollback_help() {
    say ''
    say '되돌리는 방법 (자동으로 되돌리지 않았습니다)'
    say '  코드만 되돌리기:'
    say "    $GIT checkout $PREV_COMMIT"
    say "    $PM2 startOrReload $ECO --update-env"
    say "    (나중에 다시 받을 때는 $GIT checkout $TARGET_BRANCH)"
    if [ -n "$BACKUP_DIR" ]; then
        say '  데이터까지 되돌리기 (백업 뒤에 오간 대화는 사라집니다):'
        say "    $PM2 stop $APP_NAME"
        say "    cp \"$BACKUP_DIR\"/* \"$(dirname "$DB")\"/"
        say "    rm -f \"$DB-wal\" \"$DB-shm\""
        say "    $PM2 start $APP_NAME"
    fi
}

server_deps() {      # requirements-kcs.txt 에서 '서버:' 표시가 있는 줄만 (메신저 화면용 패키지는 서버에 설치하지 않는다)
    grep -E '#[[:space:]]*서버' | sed 's/#.*//' | tr -d ' \t' | sort
}

step '배포 대상'
say "  폴더      $HERE"
say "  프로세스  $APP_NAME (포트 $PORT)"
say "  DB        $DB"
say "  브랜치    $TARGET_BRANCH  (지금 $CURRENT_BRANCH, 커밋 $(short "$PREV_COMMIT"))"
if [ "$APPLY" -eq 1 ]; then
    say '  방식      실제 배포 (--apply)'
else
    say '  방식      미리 보기 (아무것도 바꾸지 않습니다. 실제 배포는 --apply)'
fi

PROBLEM=0
if [ -n "$ECO_ROOT" ] && [ "$ECO_ROOT" != "$HERE" ] && [ "$ECO_ROOT" != "$(pwd)" ]; then
    say ''
    say "  ! $ECO 의 ROOT($ECO_ROOT)가 지금 폴더와 다릅니다. pm2 는 ROOT 폴더의 코드를 실행합니다."
    PROBLEM=1
fi
if [ -n "$("$GIT" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    say ''
    say '  ! 커밋하지 않은 수정이 있습니다. 코드를 받기 전에 정리해 주세요 (git status).'
    PROBLEM=1
fi

step '받을 코드'
if "$GIT" fetch --quiet origin 2>/dev/null; then
    if "$GIT" rev-parse --verify --quiet "$UPSTREAM" >/dev/null; then
        INCOMING="$("$GIT" log --oneline "$PREV_COMMIT..$UPSTREAM" 2>/dev/null)"
        if [ "$TARGET_BRANCH" != "$CURRENT_BRANCH" ]; then
            say "  브랜치를 $CURRENT_BRANCH → $TARGET_BRANCH 로 바꿉니다."
        fi
        if [ -z "$INCOMING" ]; then
            say '  새 커밋이 없습니다 (다시 시작만 합니다).'
        else
            printf '%s\n' "$INCOMING" | sed 's/^/  /'
        fi
        if ! "$GIT" merge-base --is-ancestor "$PREV_COMMIT" "$UPSTREAM" 2>/dev/null; then
            say "  ! 지금 커밋이 $UPSTREAM 의 과거가 아닙니다 (갈라져 있음). 빨리 감기로 받을 수 없습니다."
            PROBLEM=1
        fi
    else
        say "  ! 원격에 $TARGET_BRANCH 브랜치가 없습니다."
        PROBLEM=1
    fi
else
    say '  ! 원격 저장소에서 받아올 수 없습니다 (git fetch 실패).'
    PROBLEM=1
fi

step '배포 전 점검 (DB 읽기 전용)'
"$PY" scripts/predeploy_check.py "$DB"
CHECK_CODE=$?
if [ "$CHECK_CODE" -eq 1 ]; then
    say ''
    say '  ! 겹치는 로그인 아이디가 있습니다. 관리자 화면(구성원 조회 · 수정)에서 한쪽 값을 고친 뒤 다시 실행해 주세요.'
    PROBLEM=1
elif [ "$CHECK_CODE" -ne 0 ]; then
    say '  ! 점검을 실행하지 못했습니다.'
    PROBLEM=1
fi

if [ "$APPLY" -ne 1 ]; then
    step '실제 배포(--apply)를 하면'
    say '  1 백업 (DB 폴더의 backups/<날짜-시각>/)'
    say "  2 $TARGET_BRANCH 를 빨리 감기로 받기"
    say '  3 서버용 패키지가 바뀌었으면 .venv 에 설치'
    say "  4 $PM2 startOrReload $ECO --update-env → $PM2 save"
    say "  5 다시 시작한 뒤 확인 (프로세스 · 127.0.0.1:$PORT 의 ping/pong · 등록 상태 · 로그)"
    say ''
    if [ "$PROBLEM" -ne 0 ]; then
        say '미리 보기 결과: 위의 ! 표시를 먼저 해결해야 배포할 수 있습니다.'
        exit 1
    fi
    say '미리 보기 결과: 배포할 수 있습니다.  →  bash scripts/deploy_server.sh --apply'
    exit 0
fi

if [ "$PROBLEM" -ne 0 ]; then
    fail '위의 ! 표시 때문에 배포를 시작하지 않았습니다. 아무것도 바꾸지 않았습니다.'
    exit 1
fi
if ! command -v "$PM2" >/dev/null 2>&1; then
    fail "pm2 명령을 찾을 수 없습니다 ($PM2). 아무것도 바꾸지 않았습니다."
    exit 1
fi

step '1 백업'
BACKUP_OUT="$("$PY" scripts/backup_db.py --db "$DB" 2>&1)"
BACKUP_CODE=$?
say "  $BACKUP_OUT"
if [ "$BACKUP_CODE" -ne 0 ]; then
    fail '백업에 실패해 배포를 멈췄습니다. 코드와 서버는 그대로입니다.'
    exit 1
fi
BACKUP_DIR="$(printf '%s' "$BACKUP_OUT" | sed -n 's/^백업 완료 → \(.*\) ([0-9.]*MB.*$/\1/p' | head -n 1)"

step '2 코드 받기'
if [ "$TARGET_BRANCH" != "$CURRENT_BRANCH" ]; then
    if ! "$GIT" checkout --quiet "$TARGET_BRANCH"; then
        fail "$TARGET_BRANCH 브랜치로 바꾸지 못했습니다. 서버는 그대로 돌고 있습니다."
        exit 1
    fi
fi
if ! "$GIT" merge --ff-only --quiet "$UPSTREAM"; then
    fail '코드를 받지 못했습니다 (빨리 감기 불가). 서버는 예전 코드로 그대로 돌고 있습니다.'
    rollback_help
    exit 1
fi
NEW_COMMIT="$("$GIT" rev-parse HEAD)"
say "  $(short "$PREV_COMMIT") → $(short "$NEW_COMMIT")"

step '3 의존성'
OLD_DEPS="$("$GIT" show "$PREV_COMMIT:requirements-kcs.txt" 2>/dev/null | server_deps)"
NEW_DEPS="$(server_deps < requirements-kcs.txt 2>/dev/null)"
if [ "$OLD_DEPS" = "$NEW_DEPS" ]; then
    say '  서버용 패키지 변화 없음'
elif [ ! -x .venv/bin/python ]; then
    say '  ! 서버용 패키지가 바뀌었지만 .venv 가 없어 설치하지 않았습니다. 필요한 패키지:'
    printf '%s\n' "$NEW_DEPS" | sed 's/^/      /'
else
    say '  서버용 패키지가 바뀌어 .venv 에 설치합니다:'
    printf '%s\n' "$NEW_DEPS" | sed 's/^/      /'
    # shellcheck disable=SC2086
    if ! .venv/bin/python -m pip install --quiet $NEW_DEPS; then
        fail '패키지 설치에 실패했습니다. 서버는 아직 다시 시작하지 않았습니다 (예전 프로세스가 돌고 있습니다).'
        rollback_help
        exit 1
    fi
fi

step '4 다시 시작'
if ! "$PM2" startOrReload "$ECO" --update-env; then
    fail 'pm2 다시 시작에 실패했습니다.'
    rollback_help
    exit 1
fi
"$PM2" save >/dev/null 2>&1 || say '  ! pm2 save 는 실패했습니다 (서버 PC 를 다시 켠 뒤의 자동 시작에 영향). 직접 실행해 주세요.'

step '5 다시 시작한 뒤 확인'
FAILED=0
# shellcheck disable=SC2086
"$PY" scripts/postdeploy_check.py --port "$PORT" --wait 30 $CHECK_FLAGS || FAILED=1
"$PM2" jlist 2>/dev/null | "$PY" scripts/postdeploy_check.py --pm2-status "$APP_NAME" || FAILED=1
"$PM2" logs "$APP_NAME" --lines 300 --nostream 2>&1 | "$PY" scripts/postdeploy_check.py --read-log || FAILED=1

step '결과'
say "  코드    $(short "$PREV_COMMIT") → $(short "$NEW_COMMIT") ($TARGET_BRANCH)"
say "  백업    ${BACKUP_DIR:-(경로를 읽지 못했습니다 - 위 1번 출력 참고)}"
if [ "$FAILED" -ne 0 ]; then
    fail '다시 시작한 뒤 확인에서 실패한 항목이 있습니다 (위 ✗ 표시).'
    rollback_help
    exit 1
fi
say '  확인    모두 통과'
say ''
say '이어서 사람이 확인할 것: docs/DEPLOY_CHECKLIST.md 의 "사람이 확인할 것" (기존 계정 로그인 · nginx · 직원 공지)'
exit 0
