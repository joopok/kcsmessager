/**
 * pm2 ecosystem — KCS Talk 채팅 서버 (.230 메인 서버).
 *
 * [2026-10-14] 이 서버를 KCS Talk 메인 서버로 운영. 설치형 클라이언트(Windows·macOS·Linux)가
 *   이 주소:9550 으로 접속한다. 프로토콜은 raw TCP NDJSON 이라 nginx(HTTP) 를 거치지 않고
 *   포트를 직접 노출한다 — 외부 개방 범위는 UFW 가 결정한다.
 *
 *   네트워크 배치: 클라이언트 ─TLS─▶ talk.kcins.co.kr:9550 (nginx stream) ─PROXY v1─▶ 127.0.0.1:9551 (이 프로세스)
 *
 *   DB 는 SQLite(server_data/kakao.db). PostgreSQL 전환은 store.py 가 SQLite 문법에 강하게
 *   결합돼 있어(플레이스홀더 247곳 · AUTOINCREMENT · INSERT OR REPLACE · PRAGMA 등) 보류했다.
 *   운영 데이터 패턴을 보고 결정한다.
 *
 *   의존성은 .venv (anthropic · pypdf — AI 비서용 선택 패키지). 서버 자체는 표준 라이브러리만 쓴다.
 *
 * 운영 명령:
 *   pm2 startOrReload /kcs-project/kcsmessager/ecosystem.config.cjs --update-env
 *   pm2 save
 */

const ROOT = '/kcs-project/kcsmessager';

module.exports = {
  apps: [
    {
      name: 'kcs-talk-server',
      cwd: ROOT,
      // Python 앱: script 를 venv 의 python 으로 두고 interpreter 를 끈다 (pm2 가 node 로 실행하지 않게).
      script: `${ROOT}/.venv/bin/python`,
      // loopback 전용. 외부 9550 은 nginx stream 이 TLS 를 종단해 여기로 넘긴다 (stream.d/kcs-talk.conf).
      // --proxy-protocol: nginx 가 보내는 PROXY v1 헤더로 실제 클라이언트 IP 복원 (IP 기준 로그인 스로틀 보존).
      args: `kcs.py server --host 127.0.0.1 --port 9551 --proxy-protocol --db ${ROOT}/server_data/kakao.db`,
      interpreter: 'none',

      exec_mode: 'fork',
      autorestart: true,
      // 소켓 서버는 바인딩 실패(EADDRINUSE) 시 즉시 죽는다 — 무한 재시작 대신 멈춰서 알리게 한다.
      min_uptime: 10000,
      max_restarts: 10,
      restart_delay: 3000,
      max_memory_restart: '500M',
      // 종료 시 연결 정리 여유.
      kill_timeout: 8000,

      env: {
        PYTHONUNBUFFERED: '1',   // pm2 로그에 실시간으로 찍히게
        TZ: 'Asia/Seoul',
      },
    },
  ],
};
