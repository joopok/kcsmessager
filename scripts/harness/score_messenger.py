# -*- coding: utf-8 -*-
"""
KCS Talk 품질 하네스 — 실제 서버 + 실제 앱 세션(Session: 연결·설정·상태·창)으로 기능과 동작을 채점한다.

12개 영역 × 영역 안 점검 항목. 영역 점수 = 통과 항목 비율, 총점 = 영역 점수 평균 × 10 (10점 만점).
  1 기본 대화        2 읽음·안읽음      3 메시지 조작      4 연결 복원력      5 동시성·부하
  6 성능             7 데이터 지속성    8 서버 견고성      9 화면 안정성     10 기능 회귀
  11 디자인 품질 (명암비 · 접근성 이름 · 클릭 영역 · 글자 잘림 100%/200% · 대화상자 그리기 경고)
  12 업무 기능 (스레드 · 방장 · 휴가 · 방해금지 · 안 읽은 사람 · 임시저장 · 멘션 모아보기 · 책갈피)

사용:
  python3 scripts/harness/score_messenger.py            # 전체 (기능 회귀 포함, 약 4분)
  python3 scripts/harness/score_messenger.py --quick    # 10번(전체 테스트·데모) 생략
결과: 표 출력 + claudedocs/HARNESS_SCORE.md
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ.setdefault('KCS_RAG_LLM', 'off')
os.environ.setdefault('KCS_LINK_PREVIEW', 'off')
HOME = tempfile.mkdtemp(prefix='kcs_harness_')
os.environ['KCS_MESSENGER_HOME'] = HOME

from PyQt5.QtCore import qInstallMessageHandler  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from server.kakao.server import KakaoServer  # noqa: E402
from client.kakao.app import Session  # noqa: E402
from client.kakao.widgets import install_safe_gc  # noqa: E402

install_safe_gc(APP)      # 실제 앱(app.main)과 같게: 메모리 정리(GC)는 이벤트 사이에서만

# ---------------------------------------------------------------- 공용 도구
QT_WARNINGS, PY_ERRORS = [], []
IGNORED_QT = ('propagateSizeHints', 'does not support raise', 'grabbing the keyboard', 'font family aliases',
              'QFont::setPixelSize', 'requestActivate', 'Populating font')


def _qt_handler(mode, ctx, msg):
    if not any(s in msg for s in IGNORED_QT):
        QT_WARNINGS.append(msg)


def _excepthook(etype, value, tb):
    PY_ERRORS.append(''.join(traceback.format_exception(etype, value, tb))[-800:])


qInstallMessageHandler(_qt_handler)
sys.excepthook = _excepthook


def pump(cond=lambda: False, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    APP.processEvents()
    return bool(cond())


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    """같은 DB·포트로 껐다 켤 수 있는 테스트 서버"""

    def __init__(self, db_dir):
        self.db = os.path.join(db_dir, 'harness.db')
        self.port = free_port()
        self.srv = None

    def start(self):
        self.srv = KakaoServer(port=self.port, db_path=self.db, tick_seconds=0.2)
        self.srv.serve_in_thread()
        return self

    def stop(self):
        if self.srv:
            self.srv.stop()
            self.srv = None


def login(server, name, gui=False):
    s = Session('127.0.0.1', server.port, name)
    s.connect()
    if not pump(lambda: s.state.me == name, 5):
        raise RuntimeError(f'{name} 로그인 실패')
    if gui:
        s.show_main_window()
        pump(timeout=0.2)
    return s


def direct(a, b):
    """a 가 b 와 1:1 방을 열고 두 쪽 모두 방을 알 때까지"""
    a.state.open_direct(b.state.me)
    ok = pump(lambda: a.state.direct_room_with(b.state.me) and b.state.direct_room_with(a.state.me), 5)
    return a.state.direct_room_with(b.state.me) if ok else None


def group(owner, others, name):
    owner.state.create_group([o.state.me for o in others], name)
    find = lambda s: next((r['room_id'] for r in s.state.rooms.values()  # noqa: E731
                           if r['type'] == 'group' and r.get('name') == name), None)
    pump(lambda: all(find(s) for s in [owner] + others), 5)
    return find(owner)


def texts(session, rid):
    return [m.get('content') for m in session.state.room_messages(rid) if m.get('kind') == 'text']


def wait_text(session, rid, text, timeout=5):
    return pump(lambda: text in texts(session, rid), timeout)


class Score:
    def __init__(self):
        self.areas = []

    def area(self, name):
        entry = {'name': name, 'checks': []}
        self.areas.append(entry)
        return entry

    def check(self, area, label, fn):
        t = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as e:  # 점검 자체가 터지면 실패로 기록
            ok, detail = False, f'{type(e).__name__}: {e}'
        area['checks'].append({'label': label, 'ok': bool(ok), 'detail': detail,
                               'ms': round((time.perf_counter() - t) * 1000)})
        mark = '✓' if ok else '✗'
        print(f'   {mark} {label} — {detail}', flush=True)

    def total(self):
        if not self.areas:
            return 0.0
        return 10 * sum(sum(c['ok'] for c in a['checks']) / max(1, len(a['checks'])) for a in self.areas) / len(self.areas)


# ---------------------------------------------------------------- 1 기본 대화
def area_basic(sc, srv):
    ar = sc.area('기본 대화')
    a, b, c = login(srv, '하네스A'), login(srv, '하네스B'), login(srv, '하네스C')
    rid = direct(a, b)

    def one_to_one():
        a.state.send_text(rid, '안녕하세요 B')
        ok1 = wait_text(b, rid, '안녕하세요 B')
        b.state.send_text(rid, '반가워요 A')
        ok2 = wait_text(a, rid, '반가워요 A')
        return ok1 and ok2, '양방향 전달' if ok1 and ok2 else f'A→B {ok1}, B→A {ok2}'
    sc.check(ar, '1:1 텍스트 양방향', one_to_one)

    gid = group(a, [b, c], '하네스 그룹')

    def group_fanout():
        a.state.send_text(gid, '그룹 공지입니다')
        ok = all(wait_text(s, gid, '그룹 공지입니다') for s in (b, c))
        return ok, '3명 모두 수신' if ok else '일부 미수신'
    sc.check(ar, '그룹 3명 전달', group_fanout)

    def emoticon():
        a.state.send_emoticon(gid, '🎉')
        ok = pump(lambda: any(m.get('kind') == 'emoticon' and m.get('content') == '🎉'
                              for m in c.state.room_messages(gid)), 5)
        return ok, '이모티콘 수신' if ok else '미수신'
    sc.check(ar, '이모티콘', emoticon)

    def file_roundtrip():
        path = os.path.join(HOME, '보고서.txt')
        payload = ('KCS 하네스 첨부 ' * 200).encode()
        with open(path, 'wb') as f:
            f.write(payload)
        err = a.state.send_file(rid, path)
        if err:
            return False, err
        got = {}
        ok = pump(lambda: any(m.get('kind') == 'file' for m in b.state.room_messages(rid)), 5)
        if not ok:
            return False, '파일 메시지 미수신'
        m = [m for m in b.state.room_messages(rid) if m.get('kind') == 'file'][-1]
        aid = m['attachment']['attachment_id']
        b.state.attachment_loaded.connect(lambda i, data, n, mime: got.setdefault(i, data))
        b.state.request_attachment(aid)
        ok = pump(lambda: aid in got, 5)
        return ok and got.get(aid) == payload, f'{len(payload)}바이트 동일' if ok and got.get(aid) == payload else '내용 불일치'
    sc.check(ar, '파일 보내고 받아 내용 비교', file_roundtrip)

    def ordering():
        sent = [f'순서 {i:02d}' for i in range(40)]
        for t in sent:
            a.state.send_text(gid, t)
        ok = pump(lambda: all(t in texts(c, gid) for t in sent), 8)
        got = [t for t in texts(c, gid) if t.startswith('순서 ')]
        return ok and got == sent, '40개 순서 그대로' if got == sent else f'{len(got)}개, 순서 {"같음" if got == sorted(got) else "다름"}'
    sc.check(ar, '연속 40개 순서 보장', ordering)

    def gui_input_send():
        from client.kakao.chat_room import ChatRoomWindow
        wa, wb = ChatRoomWindow(a.state, rid), ChatRoomWindow(b.state, rid)
        wa.show()
        wb.show()
        pump(timeout=0.3)
        wa.edit.setPlainText('입력창으로 보낸 메시지')
        wa.send_current()
        ok = pump(lambda: any((r.msg or {}).get('content') == '입력창으로 보낸 메시지'
                              for r in wb.rows.values() if hasattr(r, 'msg')), 5)
        cleared = wa.edit.toPlainText() == ''
        wa.close()
        wb.close()
        return ok and cleared, '입력창 → 상대 채팅방 화면 표시, 입력창 비움' if ok and cleared else f'표시 {ok}, 비움 {cleared}'
    sc.check(ar, '화면 입력창으로 보내 상대 화면에 표시', gui_input_send)

    def rich_text():
        body = '첫 줄\n둘째 줄 😀👍🏻 한글 English 123\n' + '긴 글 ' * 700
        a.state.send_text(rid, body)
        ok = pump(lambda: body.strip() in texts(b, rid), 5)
        return ok, f'{len(body)}자 · 줄바꿈 · 이모지 그대로' if ok else '내용이 달라짐'
    sc.check(ar, '긴 글·줄바꿈·이모지 그대로', rich_text)

    def big_file_refused():
        path = os.path.join(HOME, 'big.bin')
        with open(path, 'wb') as f:
            f.write(b'0' * (10 * 1024 * 1024 + 10))
        err = a.state.send_file(rid, path)
        return bool(err) and '10MB' in err, f'거절 안내: {err}' if err else '그냥 보내짐'
    sc.check(ar, '10MB 넘는 파일은 안내와 함께 거절', big_file_refused)
    return a, b, c, rid, gid


# ---------------------------------------------------------------- 2 읽음·안읽음
def area_read(sc, srv, a, b, c, rid, gid):
    ar = sc.area('읽음·안읽음')
    for s in (a, b, c):
        s.state.load_history(gid)
    pump(timeout=0.5)

    def unread_counts():
        before = b.state.room_unread(rid)
        for i in range(3):
            a.state.send_text(rid, f'안읽음 {i}')
        ok = pump(lambda: b.state.room_unread(rid) == before + 3, 5)
        return ok, f'{before} → {b.state.room_unread(rid)} (+3 기대)'
    sc.check(ar, '받은 쪽 안 읽은 수 증가', unread_counts)

    def mark_read_clears():
        b.state.load_history(rid)
        pump(timeout=0.3)
        b.state.mark_read(rid)
        ok = pump(lambda: b.state.room_unread(rid) == 0, 5)
        return ok, f'읽은 뒤 {b.state.room_unread(rid)}'
    sc.check(ar, '읽으면 0으로', mark_read_clears)

    def sender_sees_read():
        last = [m for m in a.state.room_messages(rid) if m.get('kind') == 'text'][-1]
        ok = pump(lambda: a.state.message_unread(rid, a.state.find_message(rid, last['msg_id'])) == 0, 5)
        return ok, '보낸 쪽 숫자 1 → 0' if ok else f'남은 숫자 {a.state.message_unread(rid, last)}'
    sc.check(ar, '보낸 쪽 읽음 숫자 사라짐', sender_sees_read)

    def group_counts_down():
        a.state.send_text(gid, '읽음 숫자 시험')
        pump(lambda: all(wait_text(s, gid, '읽음 숫자 시험', 0.1) for s in (b, c)), 5)
        m = [m for m in a.state.room_messages(gid) if m.get('content') == '읽음 숫자 시험'][-1]
        n0 = a.state.message_unread(gid, m)
        b.state.mark_read(gid)
        ok1 = pump(lambda: a.state.message_unread(gid, a.state.find_message(gid, m['msg_id'])) == n0 - 1, 5)
        c.state.mark_read(gid)
        ok2 = pump(lambda: a.state.message_unread(gid, a.state.find_message(gid, m['msg_id'])) == 0, 5)
        return ok1 and ok2, f'{n0} → {n0 - 1} → 0' if ok1 and ok2 else '숫자가 줄지 않음'
    sc.check(ar, '그룹 읽음 숫자 2→1→0', group_counts_down)

    def total_consistent():
        total = c.state.total_unread()
        ssum = sum(c.state.room_unread(r) for r in c.state.rooms)
        return total == ssum, f'합계 {total} = 방별 합 {ssum}'
    sc.check(ar, '전체 안 읽은 수 = 방별 합', total_consistent)

    def offline_inbox():
        d = login(srv, '하네스D')
        rid_d = direct(a, d)
        d.close()
        pump(timeout=0.3)
        for i in range(4):
            a.state.send_text(rid_d, f'오프라인 중 {i}')
        pump(timeout=0.5)
        d2 = login(srv, '하네스D')
        n = d2.state.room_unread(rid_d)
        last = (d2.state.room(rid_d) or {}).get('last_message') or {}
        d2.close()
        return n == 4 and last.get('content') == '오프라인 중 3', f'다시 로그인: 안 읽은 {n}개, 마지막 미리보기 {last.get("content")!r}'
    sc.check(ar, '꺼져 있던 동안 온 메시지', offline_inbox)

    def multi_device_read_sync():
        b2 = login(srv, '하네스B')          # 같은 계정 두 번째 기기
        a.state.send_text(rid, '두 기기 동기화')
        pump(lambda: b2.state.room_unread(rid) > 0 and b.state.room_unread(rid) > 0, 5)
        b.state.load_history(rid)
        pump(timeout=0.3)
        b.state.mark_read(rid)
        ok = pump(lambda: b2.state.room_unread(rid) == 0, 5)
        b2.close()
        return ok, '한 기기에서 읽으면 다른 기기도 0' if ok else f'다른 기기 {b2.state.room_unread(rid)}개 남음'
    sc.check(ar, '같은 계정 두 기기 읽음 동기화', multi_device_read_sync)

    def notifications():
        got = []
        c.state.notify.connect(lambda t, body, r: got.append(r))
        a.state.send_text(gid, '알림 시험')
        ok1 = pump(lambda: gid in got, 5)
        c.settings.toggle_in('muted_rooms', gid)
        got.clear()
        a.state.send_text(gid, '알림 끈 방')
        wait_text(c, gid, '알림 끈 방')
        pump(timeout=0.3)
        ok2 = gid not in got
        c.settings.toggle_in('muted_rooms', gid)
        return ok1 and ok2, '새 메시지 알림 · 알림 끈 방은 조용' if ok1 and ok2 else f'알림 {ok1}, 끈 방 조용 {ok2}'
    sc.check(ar, '알림 (알림 끈 방 제외)', notifications)


# ---------------------------------------------------------------- 3 메시지 조작
def area_ops(sc, srv, a, b, c, rid, gid):
    ar = sc.area('메시지 조작')

    def last_text(s, content):
        return next((m for m in reversed(s.state.room_messages(gid)) if m.get('content') == content), None)

    a.state.send_text(gid, '원본 메시지')
    wait_text(b, gid, '원본 메시지')
    orig = last_text(a, '원본 메시지')

    def reply():
        b.state.send_text(gid, '답장입니다', reply_to_id=orig['msg_id'])
        ok = pump(lambda: (last_text(c, '답장입니다') or {}).get('reply_to', {}).get('msg_id') == orig['msg_id'], 5)
        return ok, '인용 정보 전달' if ok else '인용 없음'
    sc.check(ar, '답장(인용)', reply)

    def react():
        c.state.react(orig['msg_id'], '👍')
        ok = pump(lambda: c.state.me in ((a.state.find_message(gid, orig['msg_id']) or {}).get('reactions') or {}).get('👍', []), 5)
        c.state.react(orig['msg_id'], '👍')
        ok2 = pump(lambda: not ((a.state.find_message(gid, orig['msg_id']) or {}).get('reactions') or {}).get('👍'), 5)
        return ok and ok2, '공감 · 취소 모두 반영' if ok and ok2 else f'공감 {ok}, 취소 {ok2}'
    sc.check(ar, '공감 달기·취소', react)

    def edit():
        a.state.edit_message(orig['msg_id'], '수정된 메시지')
        ok = pump(lambda: (b.state.find_message(gid, orig['msg_id']) or {}).get('content') == '수정된 메시지'
                  and (b.state.find_message(gid, orig['msg_id']) or {}).get('edited'), 5)
        return ok, '수정됨 표시까지' if ok else '반영 안 됨'
    sc.check(ar, '수정', edit)

    def delete():
        a.state.send_text(gid, '지울 메시지')
        wait_text(c, gid, '지울 메시지')
        m = last_text(a, '지울 메시지')
        a.state.delete_for_all(m['msg_id'])
        ok = pump(lambda: (c.state.find_message(gid, m['msg_id']) or {}).get('deleted'), 5)
        return ok, '모두에게서 삭제됨' if ok else '삭제 안 됨'
    sc.check(ar, '모두에게서 삭제', delete)

    def notice():
        a.state.set_notice(gid, orig['msg_id'])
        ok = pump(lambda: ((b.state.room(gid) or {}).get('notice') or {}).get('msg_id') == orig['msg_id'], 5)
        return ok, '공지 배너 반영' if ok else '반영 안 됨'
    sc.check(ar, '공지 등록', notice)

    def search():
        found = []
        b.state.search_results.connect(lambda q, msgs: found.append((q, msgs)))
        b.state.search('그룹 공지', gid)
        ok = pump(lambda: any(any('그룹 공지' in (m.get('content') or '') for m in msgs) for _q, msgs in found), 5)
        return ok, '검색 결과에 포함' if ok else '결과 없음'
    sc.check(ar, '대화 검색', search)

    def invite_and_leave():
        e = login(srv, '하네스E')
        a.state.invite(gid, ['하네스E'])
        ok1 = pump(lambda: gid in e.state.rooms and '하네스E' in (a.state.room(gid) or {}).get('members', []), 5)
        e.state.leave_room(gid)
        ok2 = pump(lambda: '하네스E' not in (a.state.room(gid) or {}).get('members', []), 5)
        sys_msgs = [m.get('content') for m in a.state.room_messages(gid) if m.get('kind') == 'system']
        e.close()
        ok3 = any('하네스E' in (t or '') for t in sys_msgs)
        return ok1 and ok2 and ok3, '초대 · 나가기 · 안내 메시지' if ok1 and ok2 and ok3 else f'초대 {ok1}, 나감 {ok2}, 안내 {ok3}'
    sc.check(ar, '대화상대 초대·나가기', invite_and_leave)

    def scheduled():
        from datetime import timedelta
        a.state.send_text(gid, '2초 뒤 예약', send_at=datetime.now() + timedelta(seconds=2))
        early = wait_text(b, gid, '2초 뒤 예약', 0.8)
        ok = wait_text(b, gid, '2초 뒤 예약', 6)
        return ok and not early, '정해진 시각에 전달' if ok and not early else f'일찍 {early}, 도착 {ok}'
    sc.check(ar, '예약 메시지 정시 전달', scheduled)


# ---------------------------------------------------------------- 4 연결 복원력
def area_resilience(sc, srv):
    from client.kakao.chat_room import ChatRoomWindow
    ar = sc.area('연결 복원력')
    x, y = login(srv, '복원X'), login(srv, '복원Y', gui=True)
    rid = direct(x, y)
    x.state.send_text(rid, '끊기기 전')
    wait_text(y, rid, '끊기기 전')
    win = ChatRoomWindow(y.state, rid)
    win.show()
    pump(timeout=0.5)

    srv.stop()
    lost = pump(lambda: not y.conn.is_connected and not x.conn.is_connected, 5)
    y.state.send_text(rid, '끊긴 동안 보냄')
    failed = y.state.failed_messages(rid)
    srv.start()

    def detects_loss():
        return lost, '두 클라이언트 모두 끊김 감지' if lost else '끊김을 모름'
    sc.check(ar, '서버 중단 감지', detects_loss)

    def keeps_unsent():
        return bool(failed), '보내지 못한 메시지로 보존' if failed else '사라짐'
    sc.check(ar, '끊긴 동안 보낸 글 보존', keeps_unsent)

    def auto_reconnect():
        ok = pump(lambda: x.conn.is_connected and y.conn.is_connected and x.state.me and y.state.me, 15)
        return ok, '15초 안에 자동 재연결' if ok else '자동으로 다시 붙지 않음 (수동 버튼 필요)'
    sc.check(ar, '자동 재연결', auto_reconnect)

    def missed_messages_in_open_room():
        if not (x.conn.is_connected and y.conn.is_connected):
            return False, '재연결 안 됨'
        # Y 가 잠시 끊긴 사이 X 가 보낸 메시지가 열린 채팅방에 나타나야 한다
        old = y.conn
        old._sock.shutdown(socket.SHUT_RDWR)          # 네트워크 끊김 흉내 (사용자가 닫은 게 아님)
        pump(lambda: y.conn is not old or not old.is_connected, 3)
        x.state.send_text(rid, '놓친 메시지')
        pump(timeout=0.3)
        ok = pump(lambda: y.conn.is_connected and any(
            (row.msg or {}).get('content') == '놓친 메시지' for row in win.rows.values() if hasattr(row, 'msg')), 15)
        return ok, '다시 붙은 뒤 열린 방에 표시' if ok else '열린 방에 안 나타남'
    sc.check(ar, '끊긴 사이 온 메시지가 열린 방에 보임', missed_messages_in_open_room)

    def resend_after_reconnect():
        msgs = y.state.failed_messages(rid)
        if not msgs:
            return False, '보낼 실패 메시지 없음'
        err = y.state.resend_failed(rid, msgs[0]['msg_id'])
        ok = not err and wait_text(x, rid, '끊긴 동안 보냄')
        return ok, '재전송 후 상대가 받음' if ok else (err or '상대 미수신')
    sc.check(ar, '재연결 후 재전송', resend_after_reconnect)

    def long_outage():
        srv.stop()
        pump(lambda: not x.conn.is_connected, 5)
        pump(timeout=20)                               # 서버가 20초 꺼져 있음 (재시도 간격이 늘어남)
        srv.start()
        ok = pump(lambda: x.conn.is_connected and y.conn.is_connected, 15)
        return ok, '20초 중단 뒤 15초 안에 다시 붙음' if ok else '다시 붙지 않음'
    sc.check(ar, '서버가 20초 꺼졌다 켜져도 다시 붙음', long_outage)
    win.close()
    for s in (x, y):
        s.close()


# ---------------------------------------------------------------- 5 동시성·부하
def area_load(sc, srv):
    ar = sc.area('동시성·부하')
    n_clients, per = 12, 25
    clients = [login(srv, f'부하{i:02d}') for i in range(n_clients)]
    gid = group(clients[0], clients[1:], '부하 시험방')
    observer = clients[-1]
    recv_at = {}
    observer.state.message_added.connect(lambda r, m: recv_at.setdefault(m.get('content'), time.perf_counter()))
    sent_at = {}
    for k in range(per):
        for i, cl in enumerate(clients[:-1]):
            t = f'부하 {i:02d}-{k:02d}'
            sent_at[t] = time.perf_counter()
            cl.state.send_text(gid, t)
        pump(timeout=0.01)
    expected = len(sent_at)

    def no_loss():
        ok = pump(lambda: all(len([t for t in texts(cl, gid) if t.startswith('부하 ')]) == expected for cl in clients), 20)
        counts = sorted({len([t for t in texts(cl, gid) if t.startswith('부하 ')]) for cl in clients})
        return ok, f'{n_clients}명 × {expected}개 전부 수신' if ok else f'받은 개수 {counts} / {expected}'
    sc.check(ar, f'{n_clients}명 동시 {n_clients - 1}×{per}개 손실 없음', no_loss)

    def per_sender_order():
        got = [t for t in texts(observer, gid) if t.startswith('부하 ')]
        bad = [i for i in range(n_clients - 1)
               if [t for t in got if t.startswith(f'부하 {i:02d}-')] != [f'부하 {i:02d}-{k:02d}' for k in range(per)]]
        return not bad, '보낸 사람별 순서 유지' if not bad else f'순서 어긋남 {bad}'
    sc.check(ar, '보낸 사람별 순서', per_sender_order)

    def same_global_order():
        orders = {tuple(t for t in texts(cl, gid) if t.startswith('부하 ')) for cl in clients}
        return len(orders) == 1, '모든 사람이 같은 순서로 봄' if len(orders) == 1 else f'순서 {len(orders)}가지'
    sc.check(ar, '모두 같은 순서', same_global_order)

    def latency():
        lat = sorted((recv_at[t] - sent_at[t]) * 1000 for t in sent_at if t in recv_at)
        if not lat:
            return False, '측정 불가'
        p95 = lat[int(len(lat) * 0.95) - 1]
        return p95 < 100, f'p95 {p95:.1f}ms (기준 100ms), 중앙 {lat[len(lat) // 2]:.1f}ms'
    sc.check(ar, '전달 지연 p95 < 100ms', latency)
    for cl in clients:
        cl.close()


MEMORY_PROBE = '''
import os, sys, time, resource
sys.path.insert(0, {root!r})
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['KCS_MESSENGER_HOME'] = {home!r}
from PyQt5.QtWidgets import QApplication
app = QApplication([])
from client.kakao.app import Session
from client.kakao.chat_room import ChatRoomWindow
from client.kakao.widgets import install_safe_gc
install_safe_gc(app)      # 실제 앱과 같은 메모리 정리 방식으로 잰다
def pump(cond, t=20):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()
s = Session('127.0.0.1', {port}, {user!r}); s.connect()
pump(lambda: s.state.me)
rid = {rid!r}
s.state.load_history(rid, limit=200)
pump(lambda: rid in s.state.has_more)
while s.state.has_more.get(rid):
    n = len(s.state.room_messages(rid))
    s.state.load_history(rid, older=True, limit=200)
    pump(lambda: len(s.state.room_messages(rid)) > n or not s.state.has_more.get(rid))
w = ChatRoomWindow(s.state, rid); w.show()
pump(lambda: False, 1)
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print('MB', rss / (1024 * 1024) if sys.platform == 'darwin' else rss / 1024, len(s.state.room_messages(rid)))
'''


# ---------------------------------------------------------------- 6 성능
def area_perf(sc, srv):
    from client.kakao.main_window import MainWindow
    from client.kakao.chat_room import ChatRoomWindow
    ar = sc.area('성능')
    p, q = login(srv, '성능P'), login(srv, '성능Q')
    rid = direct(p, q)
    for i in range(1000):
        p.state.send_text(rid, f'성능 메시지 {i:04d} ' + '가나다라마바사 ' * (i % 5))
    pump(lambda: len(texts(q, rid)) >= 1000, 30)

    def startup():
        t = time.perf_counter()
        w = MainWindow(q.state, q.settings)
        w.show()
        pump(timeout=0.05)
        ms = (time.perf_counter() - t) * 1000
        w.close()
        return ms < 2000, f'{ms:.0f}ms (기준 2초)'
    sc.check(ar, '메인 창 첫 화면 < 2초', startup)

    def open_room():
        q.state.history_requested.discard(rid)
        q.state.load_history(rid, limit=50)
        pump(timeout=0.5)
        t = time.perf_counter()
        w = ChatRoomWindow(q.state, rid)
        w.show()
        pump(timeout=0.05)
        ms = (time.perf_counter() - t) * 1000
        w.close()
        return ms < 500, f'메시지 {len(q.state.room_messages(rid))}개 방 {ms:.0f}ms (기준 0.5초)'
    sc.check(ar, '큰 방 열기 < 0.5초', open_room)

    def new_message_cost():
        w = ChatRoomWindow(q.state, rid)
        w.show()
        pump(timeout=0.3)
        t = time.perf_counter()
        p.state.send_text(rid, '새 메시지 비용 측정')
        ok = pump(lambda: any((r.msg or {}).get('content') == '새 메시지 비용 측정'
                              for r in w.rows.values() if hasattr(r, 'msg')), 5)
        ms = (time.perf_counter() - t) * 1000
        w.close()
        return ok and ms < 150, f'보내서 화면에 뜨기까지 {ms:.0f}ms (기준 150ms)'
    sc.check(ar, '새 메시지 표시 < 150ms', new_message_cost)

    def memory():
        # 메신저 클라이언트 하나를 별도 프로세스로: 1000개 메시지를 모두 받아 채팅방을 연 뒤 최대 메모리
        code = MEMORY_PROBE.format(root=ROOT, home=HOME, port=srv.port, user='성능Q', rid=rid)
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
        out = [line for line in r.stdout.splitlines() if line.startswith('MB ')]
        if not out:
            return False, f'측정 실패: {r.stderr[-200:]}'
        mb, n = out[-1].split()[1:3]
        return float(mb) < 200, f'클라이언트 1개, 메시지 {n}개 받은 채팅방: 최대 {float(mb):.0f}MB (기준 200MB)'
    sc.check(ar, '메모리 < 200MB (클라이언트 1개)', memory)
    p.close()
    q.close()


# ---------------------------------------------------------------- 7 데이터 지속성
def area_persistence(sc, srv):
    ar = sc.area('데이터 지속성')
    u, v = login(srv, '지속U'), login(srv, '지속V')
    rid = direct(u, v)
    u.state.send_text(rid, '보존될 메시지')
    wait_text(v, rid, '보존될 메시지')
    m = [m for m in u.state.room_messages(rid) if m.get('content') == '보존될 메시지'][-1]
    v.state.react(m['msg_id'], '❤️')
    u.state.edit_message(m['msg_id'], '보존될 메시지(수정)')
    pump(lambda: (u.state.find_message(rid, m['msg_id']) or {}).get('edited'), 5)
    v.state.load_history(rid)
    pump(timeout=0.3)
    v.state.mark_read(rid)
    pump(timeout=0.5)
    u.state.send_text(rid, '안 읽고 남길 메시지')
    wait_text(v, rid, '안 읽고 남길 메시지')
    u.close()
    v.close()
    srv.stop()
    srv.start()
    v2 = login(srv, '지속V')

    def history_kept():
        v2.state.load_history(rid)
        ok = pump(lambda: '보존될 메시지(수정)' in texts(v2, rid), 5)
        return ok, '재시작 후 수정된 내용까지' if ok else '기록 없음'
    sc.check(ar, '서버 재시작 후 대화 기록', history_kept)

    def reactions_kept():
        mm = v2.state.find_message(rid, m['msg_id']) or {}
        ok = '지속V' in (mm.get('reactions') or {}).get('❤️', [])
        return ok, '공감 유지' if ok else '공감 사라짐'
    sc.check(ar, '공감 유지', reactions_kept)

    def unread_kept():
        n = v2.state.room_unread(rid)
        return n == 1, f'안 읽은 수 {n} (1 기대: 읽은 위치 보존)'
    sc.check(ar, '읽은 위치 유지', unread_kept)
    v2.close()


# ---------------------------------------------------------------- 8 서버 견고성
def area_robust(sc, srv):
    ar = sc.area('서버 견고성')

    def raw(lines, read=True):
        s = socket.create_connection(('127.0.0.1', srv.port), timeout=3)
        for line in lines:
            s.sendall(line if isinstance(line, bytes) else (line + '\n').encode())
        data = b''
        if read:
            s.settimeout(1.0)
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
        s.close()
        return data.decode('utf-8', 'replace')

    def alive():
        try:
            s = login(srv, '생존확인')
            s.close()
            return True
        except Exception:
            return False

    def garbage():
        raw([b'\x00\xff\xfe garbage \n', b'{not json}\n', b'[1,2,3]\n', b'"string"\n'])
        return alive(), '깨진 입력 뒤에도 서버 정상' if alive() else '서버 응답 없음'
    sc.check(ar, '깨진 입력', garbage)

    def before_join():
        out = raw([json.dumps({'type': 'send', 'room_id': 'x', 'content': 'a'})])
        return '"error"' in out and alive(), '로그인 전 요청 → 오류 응답' if '"error"' in out else '응답 없음'
    sc.check(ar, '로그인 전 요청', before_join)

    def unknown_and_missing():
        lines = [json.dumps({'type': 'join', 'username': '퍼저'})]
        for t in ('nope', 'send', 'react', 'edit', 'delete', 'history', 'room_create', 'poll_vote', 'event_create',
                  'room_summary', 'room_export', 'call_start', 'friend_add', 'board_post', 'ladder_create'):
            lines.append(json.dumps({'type': t}))
            lines.append(json.dumps({'type': t, 'room_id': 123, 'msg_id': 'x', 'content': None, 'members': 'x'}))
        out = raw(lines)
        errors = out.count('"type": "error"') + out.count('"type":"error"')
        return errors >= 25 and alive(), f'잘못된 요청 30개 중 오류 응답 {errors}개, 서버 정상'
    sc.check(ar, '빠진 값·잘못된 형식 요청 30개', unknown_and_missing)

    def huge():
        big = json.dumps({'type': 'join', 'username': 'x' * 10}) + '\n' + json.dumps({'type': 'send', 'content': 'a' * 3_000_000})
        raw([big.encode() + b'\n'], read=True)
        return alive(), '3MB 요청 뒤에도 정상' if alive() else '서버 멈춤'
    sc.check(ar, '아주 큰 요청', huge)

    def many_connections():
        socks = []
        for _ in range(60):
            socks.append(socket.create_connection(('127.0.0.1', srv.port), timeout=3))
        ok = alive()
        for s in socks:
            s.close()
        return ok, '빈 연결 60개 동시 → 정상' if ok else '새 로그인 실패'
    sc.check(ar, '빈 연결 60개', many_connections)

    def no_foreign_room():
        a, b, m = login(srv, '권한A'), login(srv, '권한B'), login(srv, '권한M')
        rid = direct(a, b)
        a.state.send_text(rid, '둘만의 대화')
        wait_text(b, rid, '둘만의 대화')
        out = raw([json.dumps({'type': 'join', 'username': '권한M'}),
                   json.dumps({'type': 'history', 'room_id': rid}),
                   json.dumps({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '끼어들기'}),
                   json.dumps({'type': 'room_export', 'room_id': rid})])
        leaked = '둘만의 대화' in out
        injected = wait_text(b, rid, '끼어들기', 1)
        for s_ in (a, b, m):
            s_.close()
        return not leaked and not injected, '남의 방 기록·전송·내보내기 모두 거절' if not leaked and not injected else f'유출 {leaked}, 끼어듦 {injected}'
    sc.check(ar, '남의 방 접근 거절', no_foreign_room)

    def no_spoofing():
        a, b = login(srv, '위조A'), login(srv, '위조B')
        rid = direct(a, b)
        raw([json.dumps({'type': 'join', 'username': '위조A'}),
             json.dumps({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': '위조 시도', 'sender': '위조B'})])
        ok = wait_text(b, rid, '위조 시도', 3)
        m = next((m for m in b.state.room_messages(rid) if m.get('content') == '위조 시도'), {})
        for s_ in (a, b):
            s_.close()
        return ok and m.get('sender') == '위조A', f'보낸 사람은 접속 계정({m.get("sender")})으로 기록'
    sc.check(ar, '보낸 사람 위조 불가', no_spoofing)


# ---------------------------------------------------------------- 9 화면 안정성
def area_ui(sc, srv):
    from client.kakao.chat_room import ChatRoomWindow
    from client.kakao import theme
    from client.kakao.main_window import apply_app_theme
    ar = sc.area('화면 안정성')
    QT_WARNINGS.clear()
    PY_ERRORS.clear()
    g, h = login(srv, '화면G', gui=True), login(srv, '화면H', gui=True)
    rid = direct(g, h)
    g.state.send_text(rid, '화면 시험 https://example.com')
    g.state.send_emoticon(rid, '😀')
    g.state.send_contact(rid, '화면H')
    path = os.path.join(HOME, 'pic.png')
    from PyQt5.QtGui import QImage, QColor
    img = QImage(80, 60, QImage.Format_RGB32)
    img.fill(QColor('#3366CC'))
    img.save(path)
    g.state.send_file(rid, path)
    pump(lambda: len(h.state.room_messages(rid)) >= 4, 5)

    def all_tabs():
        w = h.window
        for tab in ('friends', 'chats', 'org', 'calendar', 'more'):
            w.set_tab(tab)
            pump(timeout=0.15)
        for size in ((320, 520), (1320, 800)):
            w.resize(*size)
            pump(timeout=0.15)
        return not PY_ERRORS, '탭 5개 · 좁은/넓은 창 예외 없음' if not PY_ERRORS else PY_ERRORS[-1][-200:]
    sc.check(ar, '메인 창 탭·크기 전환', all_tabs)

    def room_all_kinds():
        before = len(PY_ERRORS)
        win = ChatRoomWindow(h.state, rid)
        win.show()
        pump(timeout=0.5)
        win.show_summary()
        win.show_reaction_picker(h.state.room_messages(rid)[-1], exec_=False)
        win.kb_select(-1)
        win.kb_menu(exec_=False)
        for size in ((320, 560), (900, 800)):
            win.resize(*size)
            pump(timeout=0.15)
        win.close()
        return len(PY_ERRORS) == before, '모든 말풍선·대화상자 예외 없음' if len(PY_ERRORS) == before else PY_ERRORS[-1][-200:]
    sc.check(ar, '채팅방 (모든 말풍선·요약·공감·키보드)', room_all_kinds)

    def dark_and_zoom():
        before = len(PY_ERRORS)
        for dark, scale in ((True, 1.0), (False, 2.0), (False, 1.0)):
            h.settings.set('dark_mode', dark)
            h.settings.set('font_scale', scale)
            apply_app_theme(h.settings)
            pump(timeout=0.2)
        theme.set_font_scale(1.0)
        return len(PY_ERRORS) == before, '다크 · 200% 전환 예외 없음' if len(PY_ERRORS) == before else PY_ERRORS[-1][-200:]
    sc.check(ar, '다크 모드 · 200% 글자', dark_and_zoom)

    def no_qt_warnings():
        bad = [w for w in QT_WARNINGS if 'QPainter' in w or 'QWidget' in w or 'QLayout' in w or 'Cannot' in w]
        return not bad, 'Qt 경고 없음' if not bad else f'{len(bad)}개: {bad[0][:120]}'
    sc.check(ar, 'Qt 그리기·배치 경고 없음', no_qt_warnings)
    for s in (g, h):
        if s.window:
            s.window.close()
        s.close()


# ---------------------------------------------------------------- 11 디자인 품질
def area_design(sc, srv):
    from client.kakao.chat_room import ChatRoomWindow
    from client.kakao import theme
    from scripts.harness.design_probe import contrast_failures, widget_audit, iter_dialogs
    ar = sc.area('디자인 품질')

    def contrast_ok():
        bad = contrast_failures()
        return not bad, '모든 글자/배경 토큰 쌍 기준 이상 (라이트·다크)' if not bad else \
            ', '.join(f'{m} {fg}/{bg} {r}<{n}' for m, fg, bg, r, n in bad[:3])
    sc.check(ar, 'WCAG 명암비 (토큰 16쌍 × 라이트·다크)', contrast_ok)

    d, e = login(srv, '디자인D', gui=True), login(srv, '디자인E', gui=True)
    rid = direct(d, e)
    gid = group(d, [e], '디자인방')
    d.state.send_text(gid, '공지로 올릴 아주 긴 메시지입니다. 자료는 https://example.com/docs/very/long/path 참고해 주세요')
    pump(lambda: len(e.state.room_messages(gid)) >= 2, 5)
    notice = [m for m in d.state.room_messages(gid) if m.get('kind') == 'text'][-1]
    d.state.set_notice(gid, notice['msg_id'])
    d.state.send_text(gid, '지울 메시지')
    pump(lambda: 'notice' in (e.state.room(gid) or {}) and (e.state.room(gid) or {}).get('notice'), 5)
    pump(lambda: '지울 메시지' in texts(d, gid), 5)
    gone = [m for m in d.state.room_messages(gid) if m.get('content') == '지울 메시지'][-1]
    d.state.delete_for_all(gone['msg_id'])
    pump(lambda: (e.state.find_message(gid, gone['msg_id']) or {}).get('deleted'), 5)
    screens = {}

    def audit_screens(scale):
        theme.set_font_scale(scale)
        found = {'unnamed': [], 'small': [], 'clipped': []}
        w = e.window
        w.resize(380, 680)
        for tab in ('friends', 'chats', 'org', 'calendar', 'more'):
            w.set_tab(tab)
            pump(timeout=0.2)
            for k, v in widget_audit(w).items():
                found[k] += [f'{tab}:{x}' for x in v]
        for room in (rid, gid):
            win = ChatRoomWindow(e.state, room)
            win.resize(420, 720)
            win.show()
            pump(timeout=0.5)
            for k, v in widget_audit(win).items():
                found[k] += [f'방:{x}' for x in v]
            if room == gid:
                screens['room'] = win
            else:
                win.close()
        theme.set_font_scale(1.0)
        return found

    base = audit_screens(1.0)
    zoom = audit_screens(2.0)
    room_win = screens['room']
    QT_WARNINGS.clear()
    dialogs = {'unnamed': [], 'small': [], 'clipped': []}
    names = []
    for scale in (1.0, 2.0):
        theme.set_font_scale(scale)
        for name, dlg in iter_dialogs(e.state, room_win):
            try:
                dlg.show()
                pump(timeout=0.12)
                for k, v in widget_audit(dlg).items():
                    dialogs[k] += [f'{name}@{int(scale * 100)}%:{x}' for x in v]
                names.append(name)
            finally:
                dlg.close()
                dlg.deleteLater()
        theme.set_font_scale(1.0)
    pump(timeout=0.2)
    paint_warnings = [w for w in QT_WARNINGS if 'QPaint' in w or 'QPainter' in w or 'Cannot' in w]

    def fmt(items, ok_text):
        return (not items), ok_text if not items else f'{len(items)}개: ' + ' · '.join(items[:3])
    sc.check(ar, '아이콘 버튼 접근성 이름', lambda: fmt(base['unnamed'] + zoom['unnamed'] + dialogs['unnamed'],
                                                f'화면 7개 · 대화상자 {len(set(names))}개 모두 이름 있음'))
    sc.check(ar, '클릭 영역 24px 이상 (WCAG 2.5.8)', lambda: fmt(base['small'] + zoom['small'] + dialogs['small'],
                                                         '아이콘 버튼·체크박스 모두 24px 이상'))
    sc.check(ar, '글자 잘림 없음 (100%)', lambda: fmt(base['clipped'] + [x for x in dialogs['clipped'] if '@100%' in x],
                                                '메인 탭 5개 · 채팅방 · 대화상자'))
    sc.check(ar, '글자 잘림 없음 (200% 확대)', lambda: fmt(zoom['clipped'] + [x for x in dialogs['clipped'] if '@200%' in x],
                                                   '200%에서도 잘린 글자 없음'))
    sc.check(ar, '대화상자 그리기 경고 없음', lambda: fmt(paint_warnings, f'대화상자 {len(names)}번 열고 닫기 경고 없음'))
    room_win.close()
    for s in (d, e):
        if s.window:
            s.window.close()
        s.close()


# ---------------------------------------------------------------- 12 업무 기능
def area_work(sc, srv):
    from client.kakao import dnd
    from client.kakao.chat_room import ChatRoomWindow
    ar = sc.area('업무 기능')
    a, b, c = login(srv, '업무A', gui=True), login(srv, '업무B', gui=True), login(srv, '업무C')
    gid = group(a, [b, c], '업무방')

    def thread():
        a.state.send_text(gid, '스레드 원글')
        pump(lambda: '스레드 원글' in texts(b, gid), 5)
        root = [m for m in b.state.room_messages(gid) if m.get('content') == '스레드 원글'][-1]
        b.state.send_text(gid, '스레드 답글', thread_root_id=root['msg_id'])
        ok = pump(lambda: (a.state.find_message(gid, root['msg_id']) or {}).get('thread_count') == 1, 5)
        hidden = all(m.get('content') != '스레드 답글' for m in a.state.timeline_messages(gid))
        return ok and hidden, '답글 수 1 · 본문에서 숨김' if ok and hidden else f'count ok={ok} hidden={hidden}'
    sc.check(ar, '스레드 답장', thread)

    def owner():
        tmp = group(a, [b, c], '방장시험')
        a.state.kick_member(tmp, c.state.me)
        gone = pump(lambda: tmp not in c.state.rooms, 5)
        return gone and a.state.room_owner(tmp) == a.state.me, '방장이 내보내기 → 상대 방 사라짐' if gone else '내보내기 실패'
    sc.check(ar, '방장 내보내기', owner)

    def vacation():
        c.state.set_presence('vacation')
        pump(lambda: a.state.presence_of(c.state.me) == 'vacation', 5)
        c.close()
        ok = pump(lambda: not a.state.is_online('업무C'), 5) and a.state.presence_of('업무C') == 'vacation'
        return ok, '접속 종료 후에도 휴가 중' if ok else a.state.presence_of('업무C')
    sc.check(ar, '휴가 상태 (미접속에도 표시)', vacation)

    def dnd_check():
        dnd.pause(a.settings, 60)
        msg = {'kind': 'text', 'content': 'x', 'sender': b.state.me, 'mentions': []}
        blocked = not a.state.should_alert(gid, msg)
        a.settings.set('vip_users', [b.state.me])
        vip = a.state.should_alert(gid, msg)
        dnd.resume(a.settings)
        a.settings.set('vip_users', [])
        return blocked and vip, '일시중지 중 알림 막힘 · VIP 통과' if blocked and vip else f'blocked={blocked} vip={vip}'
    sc.check(ar, '방해금지 · VIP', dnd_check)

    def unread_members():
        a.state.send_text(gid, '누가 안 읽었나')
        pump(lambda: '누가 안 읽었나' in texts(b, gid), 5)
        m = [x for x in a.state.room_messages(gid) if x.get('content') == '누가 안 읽었나'][-1]
        b.state.mark_read(gid)
        pump(lambda: a.state.message_unread(gid, m) == 1, 5)
        names = a.state.unread_members(gid, m)
        win = ChatRoomWindow(a.state, gid)
        win.show()
        pump(timeout=0.4)
        popup = win.show_unread_members(m)
        shown = popup is not None and '업무C' in popup.member_names()
        popup.close()
        win.close()
        return names == ['업무C'] and shown, '읽음 숫자 → 안 읽은 사람 목록' if shown else f'names={names}'
    sc.check(ar, '안 읽은 사람 보기', unread_members)

    def drafts():
        win = ChatRoomWindow(a.state, gid)
        win.show()
        pump(timeout=0.3)
        win.edit.setPlainText('쓰다 만 글')
        win.close()
        pump(timeout=0.2)
        again = ChatRoomWindow(a.state, gid)
        again.show()
        pump(timeout=0.3)
        restored = again.edit.toPlainText() == '쓰다 만 글'
        preview = a.state.room_draft(gid) == '쓰다 만 글'
        again.edit.clear()
        again.close()
        pump(timeout=0.2)
        cleared = not a.state.room_draft(gid)
        return restored and preview and cleared, '창을 닫았다 열어도 입력 유지 · 비우면 삭제' if restored and preview and cleared \
            else f'restored={restored} saved={preview} cleared={cleared}'
    sc.check(ar, '입력 임시저장', drafts)

    def mentions():
        b.state.send_text(gid, '@업무A 확인 부탁드려요')
        pump(lambda: '@업무A 확인 부탁드려요' in texts(a, gid), 5)
        a.state.request_mentions()
        ok = pump(lambda: any(m.get('content') == '@업무A 확인 부탁드려요' for m in a.state.mentions), 5)
        return ok, '다른 방 멘션까지 한곳에' if ok else f'{len(a.state.mentions)}개'
    sc.check(ar, '멘션 모아보기', mentions)

    def bookmarks():
        m = [x for x in a.state.room_messages(gid) if x.get('content') == '스레드 원글'][-1]
        a.state.save_message(m)
        saved = a.state.is_saved(gid, m['msg_id']) and a.state.saved_messages()[0]['msg_id'] == m['msg_id']
        a.state.unsave_message(gid, m['msg_id'])
        return saved and not a.state.is_saved(gid, m['msg_id']), '책갈피 저장 · 해제' if saved else '저장 안 됨'
    sc.check(ar, '책갈피 (저장한 메시지)', bookmarks)
    for s in (a, b):
        if s.window:
            s.window.close()
        s.close()


# ---------------------------------------------------------------- 10 기능 회귀
def area_regression(sc, quick):
    ar = sc.area('기능 회귀')
    if quick:
        sc.check(ar, '(--quick: 생략)', lambda: (True, '전체 실행 때 측정'))
        return

    def full_tests():
        r = subprocess.run([sys.executable, 'scripts/verify_features.py'], cwd=ROOT, capture_output=True,
                           text=True, timeout=900)
        tail = [line for line in r.stdout.splitlines() if '기능' in line and '통과' in line]
        return r.returncode == 0, (tail[-1].strip() if tail else r.stdout[-200:])
    sc.check(ar, '전체 테스트 · 기능 표', full_tests)

    def demo():
        r = subprocess.run([sys.executable, 'scripts/demo.py', '--headless-check'], cwd=ROOT, capture_output=True,
                           text=True, timeout=300)
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''
        return r.returncode == 0 and '통과' in last and last.split('/')[0].split()[-1] == last.split('/')[1].split()[0], last
    sc.check(ar, '실제 서버 데모', demo)


# ---------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    args = ap.parse_args()
    sc = Score()
    srv = Server(HOME).start()
    steps = [
        ('1 기본 대화', lambda: area_basic(sc, srv)),
    ]
    print('KCS Talk 품질 하네스\n')
    print('1. 기본 대화')
    ctx = area_basic(sc, srv)
    print('2. 읽음·안읽음')
    area_read(sc, srv, *ctx)
    print('3. 메시지 조작')
    area_ops(sc, srv, *ctx)
    for s in ctx[:3]:
        s.close()
    for label, fn in (('4. 연결 복원력', area_resilience), ('5. 동시성·부하', area_load), ('6. 성능', area_perf),
                      ('7. 데이터 지속성', area_persistence), ('8. 서버 견고성', area_robust), ('9. 화면 안정성', area_ui),
                      ('11. 디자인 품질', area_design), ('12. 업무 기능', area_work)):
        print(label)
        try:
            fn(sc, srv)
        except Exception as e:
            area = sc.area(label.split('. ', 1)[1])
            area['checks'].append({'label': '영역 실행', 'ok': False, 'detail': f'{type(e).__name__}: {e}', 'ms': 0})
            print(f'   ✗ 영역 실행 실패 — {e}')
    srv.stop()
    print('10. 기능 회귀')
    area_regression(sc, args.quick)
    del steps

    total = sc.total()
    print('\n영역별 점수')
    for a in sc.areas:
        ok = sum(c['ok'] for c in a['checks'])
        print(f"  {a['name']:<10} {ok}/{len(a['checks'])}  {ok / max(1, len(a['checks'])):.2f}")
    print(f'\n총점 {total:.1f} / 10')
    lines = [f'# KCS Talk 품질 하네스 점수 — {total:.1f} / 10',
             f"측정 {datetime.now():%Y-%m-%d %H:%M} · `python3 scripts/harness/score_messenger.py{' --quick' if args.quick else ''}`", '',
             '| 영역 | 통과 | 점수 |', '|---|---|---|']
    for a in sc.areas:
        ok = sum(c['ok'] for c in a['checks'])
        lines.append(f"| {a['name']} | {ok}/{len(a['checks'])} | {ok / max(1, len(a['checks'])):.2f} |")
    lines += ['', '## 점검 항목', '| 영역 | 항목 | 결과 | 내용 |', '|---|---|---|---|']
    for a in sc.areas:
        for c in a['checks']:
            lines.append(f"| {a['name']} | {c['label']} | {'✓' if c['ok'] else '✗'} | {c['detail']} |")
    with open(os.path.join(ROOT, 'claudedocs', 'HARNESS_SCORE.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    shutil.rmtree(HOME, ignore_errors=True)
    sys.stdout.flush()
    os._exit(0 if total >= 9.999 else 1)


if __name__ == '__main__':
    main()
