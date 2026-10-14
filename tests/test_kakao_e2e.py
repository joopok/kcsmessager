# -*- coding: utf-8 -*-
"""
E2E 통합 테스트 - 실제 KakaoServer + 실제 클라이언트 스택(ChatConnection + AppState) 2~3명
서버/클라이언트가 같은 프로토콜 계약(claudedocs/KAKAO_FEATURE_SPEC.md)을 지키는지 검증한다.
"""

import base64
import os
import sys
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta

os.environ['KCS_RAG_LLM'] = 'off'
os.environ['KCS_LINK_PREVIEW'] = 'off'

from tests.kakao_fakes import app  # noqa: E402  (QApplication + sys.path 설정)
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt5.QtGui import QImage, QColor  # noqa: E402

from client.kakao.net import ChatConnection  # noqa: E402
from client.kakao.settings import LocalSettings  # noqa: E402
from client.kakao.state import AppState, BOT_NAME  # noqa: E402
from server.kakao.server import KakaoServer  # noqa: E402


def pump(cond, timeout=5.0):
    """조건이 참이 될 때까지 Qt 이벤트 처리"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app().processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app().processEvents()
    return cond()


def png_bytes(color='#3366CC', w=64, h=64):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, 'PNG')
    return bytes(ba)


class E2ETest(unittest.TestCase):
    def setUp(self):
        app()
        self.dir = tempfile.mkdtemp(prefix='kcs_e2e_')
        self.server = KakaoServer(host='127.0.0.1', port=0, db_path=os.path.join(self.dir, 'kakao.db'),
                                  tick_seconds=0.2)
        self.server.serve_in_thread()
        self.clients = []

    def tearDown(self):
        for state in self.clients:
            state.conn.close()
        pump(lambda: False, 0.2)
        self.server.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def login(self, username):
        conn = ChatConnection()
        settings = LocalSettings(username, base_dir=os.path.join(self.dir, 'settings'))
        state = AppState(conn, settings)
        conn.connect_to('127.0.0.1', self.server.port, username)
        self.assertTrue(pump(lambda: state.me == username), f'{username} welcome 수신 실패')
        self.clients.append(state)
        return state

    def open_direct(self, a, b_name):
        opened = []
        a.room_opened.connect(opened.append)
        a.open_direct(b_name)
        self.assertTrue(pump(lambda: opened))
        return opened[0]

    def test_direct_chat_unread_and_read(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        self.assertTrue(pump(lambda: hong.is_online('김철수')))
        rid = self.open_direct(hong, '김철수')
        self.assertTrue(pump(lambda: kim.room(rid) is not None))
        hong.send_text(rid, '안녕하세요 @김철수')
        self.assertTrue(pump(lambda: kim.room_unread(rid) == 1))
        kim.load_history(rid)
        self.assertTrue(pump(lambda: kim.room_messages(rid)))
        msg = kim.room_messages(rid)[-1]
        self.assertEqual(msg['content'], '안녕하세요 @김철수')
        self.assertEqual(msg['mentions'], ['김철수'])
        self.assertTrue(pump(lambda: hong.find_message(rid, msg['msg_id']) is not None))
        self.assertEqual(hong.message_unread(rid, hong.find_message(rid, msg['msg_id'])), 1)
        kim.mark_read(rid)
        self.assertTrue(pump(lambda: hong.message_unread(rid, hong.find_message(rid, msg['msg_id'])) == 0))
        self.assertTrue(pump(lambda: kim.room_unread(rid) == 0))

    def test_reply_react_delete_notice_search(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        hong.send_text(rid, '내일 배포 일정 공유드립니다')
        self.assertTrue(pump(lambda: hong.room_messages(rid)))
        first = hong.room_messages(rid)[-1]
        self.assertTrue(pump(lambda: kim.room(rid) is not None))
        kim.send_text(rid, '확인했습니다', reply_to_id=first['msg_id'])
        self.assertTrue(pump(lambda: len(hong.room_messages(rid)) == 2))
        reply = hong.room_messages(rid)[-1]
        self.assertEqual(reply['reply_to']['msg_id'], first['msg_id'])
        kim.react(first['msg_id'], '👍')
        self.assertTrue(pump(lambda: hong.find_message(rid, first['msg_id'])['reactions'].get('👍') == ['김철수']))
        hong.set_notice(rid, first['msg_id'])
        self.assertTrue(pump(lambda: (kim.room(rid).get('notice') or {}).get('msg_id') == first['msg_id']))
        results = []
        kim.search_results.connect(lambda q, msgs: results.append(msgs))
        kim.search('배포', rid)
        self.assertTrue(pump(lambda: results))
        self.assertIn(first['msg_id'], [m['msg_id'] for m in results[0]])
        hong.delete_for_all(first['msg_id'])
        self.assertTrue(pump(lambda: kim.find_message(rid, first['msg_id']) and
                             kim.find_message(rid, first['msg_id'])['deleted']))

    def test_attachment_send_and_receive(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        payload = os.urandom(1024 * 1024 + 7)  # 1MB 이진 파일
        path = os.path.join(self.dir, '보고서.bin')
        with open(path, 'wb') as f:
            f.write(payload)
        self.assertIsNone(hong.send_file(rid, path))
        kim.load_history(rid)
        self.assertTrue(pump(lambda: any(m['kind'] == 'file' for m in kim.room_messages(rid)), 8))
        m = [m for m in kim.room_messages(rid) if m['kind'] == 'file'][-1]
        self.assertEqual(m['attachment']['name'], '보고서.bin')
        got = []
        kim.attachment_loaded.connect(lambda aid, data, name, mime: got.append(data))
        kim.request_attachment(m['attachment']['attachment_id'])
        self.assertTrue(pump(lambda: got, 8))
        self.assertEqual(got[0], payload)

    def test_group_cross_company_and_presence_avatar(self):
        hong, lee, choi = self.login('홍길동'), self.login('이영희'), self.login('최지우')
        self.assertEqual(hong.company_of('이영희'), 'KCS 물류')
        self.assertTrue(hong.is_external('최지우'))
        opened = []
        hong.room_opened.connect(opened.append)
        hong.create_group(['이영희', '최지우'], name='그룹사 TF')
        self.assertTrue(pump(lambda: opened))
        rid = opened[0]
        self.assertTrue(pump(lambda: lee.room(rid) is not None and choi.room(rid) is not None))
        self.assertEqual(lee.room_title(rid), '그룹사 TF')
        # 상태 변경
        lee.set_presence('meeting')
        self.assertTrue(pump(lambda: hong.presence_of('이영희') == 'meeting'))
        # 프로필 사진
        changed = []
        hong.avatar_changed.connect(changed.append)
        lee.update_profile(avatar_b64=base64.b64encode(png_bytes()).decode())
        self.assertTrue(pump(lambda: '이영희' in changed, 5))
        self.assertTrue(hong.avatar_bytes('이영희').startswith(b'\x89PNG'))
        # 연결 종료 → offline
        lee.conn.close()
        self.assertTrue(pump(lambda: hong.presence_of('이영희') == 'offline'))

    def test_invite_to_group_and_from_direct(self):
        hong, kim, park = self.login('홍길동'), self.login('김철수'), self.login('박민수')
        # 1:1 방에서 초대 → 기존 두 사람 + 초대자로 새 그룹방
        rid = self.open_direct(hong, '김철수')
        opened = []
        hong.room_opened.connect(opened.append)
        self.assertTrue(hong.invite(rid, ['박민수']))
        self.assertTrue(pump(lambda: opened))
        gid = opened[0]
        self.assertNotEqual(gid, rid)
        self.assertTrue(pump(lambda: park.room(gid) is not None and kim.room(gid) is not None))
        self.assertEqual(sorted(park.room(gid)['members']), ['김철수', '박민수', '홍길동'])
        # 그룹방에서 초대 → 같은 방에 추가 + 시스템 메시지, 초대받은 사람에게 방이 나타남
        choi = self.login('최지우')
        self.assertFalse(hong.invite(gid, ['최지우', BOT_NAME]))
        self.assertTrue(pump(lambda: choi.room(gid) is not None))
        self.assertIn(BOT_NAME, hong.room(gid)['members'])
        kim.load_history(gid)
        self.assertTrue(pump(lambda: any(m['kind'] == 'system' and '초대' in m['content'] and '최지우' in m['content']
                                         for m in kim.room_messages(gid))))
        # 초대된 사람은 이전 메시지를 안 읽은 것으로 세지 않음
        self.assertEqual(choi.room_unread(gid), 0)
        # 초대된 AI 비서는 멘션하면 그룹방에서도 답한다
        hong.send_text(gid, '@AI 비서 안녕?')
        self.assertTrue(pump(lambda: any(m['sender'] == BOT_NAME for m in kim.room_messages(gid)), 8))

    def test_event_rsvp_and_reminder(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        reminders = []
        kim.event_reminder.connect(lambda ev, minutes: reminders.append(ev['title']))
        start = datetime.now() + timedelta(minutes=10, seconds=1)
        hong.create_event(title='코드 리뷰', start=start, end=start + timedelta(hours=1),
                          all_day=False, remind_minutes=10, room_id=rid)
        # 만든 사람 쪽에도 도착할 때까지 (느린 PC 에서는 받는 사람 쪽이 먼저 받는다)
        self.assertTrue(pump(lambda: all(any(e['title'] == '코드 리뷰' for e in s.events.values()) for s in (hong, kim))))
        ev = [e for e in kim.events.values() if e['title'] == '코드 리뷰'][0]
        self.assertEqual(ev['attendees']['김철수'], 'pending')
        kim.rsvp(ev['event_id'], 'yes')
        self.assertTrue(pump(lambda: hong.events.get(ev['event_id'], {}).get('attendees', {}).get('김철수') == 'yes'))
        self.assertTrue(pump(lambda: reminders == ['코드 리뷰'], 5))
        kim.load_history(rid)
        self.assertTrue(pump(lambda: any(m['kind'] == 'event' for m in kim.room_messages(rid))))

    def test_poll_and_todo(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        hong.create_poll(rid, '점심?', ['국밥', '파스타'], multiple=False)
        self.assertTrue(pump(lambda: any(m['kind'] == 'poll' for m in hong.room_messages(rid))))
        poll = [m for m in hong.room_messages(rid) if m['kind'] == 'poll'][0]['poll']
        kim.vote_poll(poll['poll_id'], [1])
        self.assertTrue(pump(lambda: [m for m in hong.room_messages(rid) if m['kind'] == 'poll'][0]
                             ['poll']['options'][1]['voters'] == ['김철수']))
        hong.create_todo('테스트 작성')
        self.assertTrue(pump(lambda: any(t['title'] == '테스트 작성' for t in hong.todos.values())))

    def test_scheduled_message(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        ok = []
        hong.scheduled_ok.connect(ok.append)
        hong.send_text(rid, '예약 메시지', send_at=datetime.now() + timedelta(seconds=1))
        self.assertTrue(pump(lambda: ok))
        self.assertTrue(pump(lambda: (kim.room(rid) or {}).get('last_message', {}) and
                             kim.room(rid)['last_message']['content'] == '예약 메시지', 6))

    def test_ai_bot_learns_document_and_answers(self):
        hong = self.login('홍길동')
        self.assertTrue(hong.is_bot(BOT_NAME))
        rid = self.open_direct(hong, BOT_NAME)
        hong.load_history(rid)
        doc = os.path.join(self.dir, '복지제도.md')
        with open(doc, 'w', encoding='utf-8') as f:
            f.write('# 복지제도\n\n자기계발비는 연 120만원까지 지원하며 도서 구입과 온라인 강의에 사용할 수 있다.')
        self.assertIsNone(hong.send_file(rid, doc))
        self.assertTrue(pump(lambda: any(m['sender'] == BOT_NAME and '학습' in m['content']
                                         for m in hong.room_messages(rid)), 8))
        hong.send_text(rid, '자기계발비는 얼마까지 지원돼?')
        self.assertTrue(pump(lambda: any(m['sender'] == BOT_NAME and '120만원' in m['content']
                                         for m in hong.room_messages(rid)), 8))
        docs = []
        hong.rag_docs_changed.connect(docs.append)
        hong.request_rag_docs()
        self.assertTrue(pump(lambda: docs))
        self.assertEqual(docs[-1][0]['name'], '복지제도.md')


class E2E2025Test(E2ETest):
    """카카오톡 2025 개편 기능을 실제 서버 + 클라이언트 스택으로 검증"""

    def test_edit_and_neutral_delete(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        hong.send_text(rid, '회의 3시')
        self.assertTrue(pump(lambda: hong.room_messages(rid)))
        m = hong.room_messages(rid)[-1]
        self.assertTrue(hong.can_edit(m))
        kim.load_history(rid)
        self.assertTrue(pump(lambda: kim.room_messages(rid)))
        hong.edit_message(m['msg_id'], '회의 4시로 변경')
        self.assertTrue(pump(lambda: kim.find_message(rid, m['msg_id'])['edited']))
        self.assertEqual(kim.find_message(rid, m['msg_id'])['content'], '회의 4시로 변경')
        hong.delete_for_all(m['msg_id'])
        self.assertTrue(pump(lambda: kim.find_message(rid, m['msg_id'])['deleted']))
        self.assertEqual(kim.message_preview(kim.find_message(rid, m['msg_id'])), '메시지가 삭제되었습니다')

    def test_silent_message_no_notification_but_unread(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        self.assertTrue(pump(lambda: kim.room(rid) is not None))
        notes = []
        kim.notify.connect(lambda *a: notes.append(a))
        hong.send_text(rid, '밤늦게 미안, 내일 봐', silent=True)
        self.assertTrue(pump(lambda: kim.room_unread(rid) == 1))
        self.assertEqual(notes, [])
        hong.send_text(rid, '일반 메시지')
        self.assertTrue(pump(lambda: len(notes) == 1))

    def test_event_change_notifies_attendee(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        start = datetime.now() + timedelta(days=2)
        hong.create_event(title='분기 리뷰', start=start, end=start + timedelta(hours=1), room_id=rid)
        self.assertTrue(pump(lambda: all(any(e['title'] == '분기 리뷰' for e in s.events.values()) for s in (hong, kim))))
        ev = [e for e in hong.events.values() if e['title'] == '분기 리뷰'][0]
        notes = []
        kim.notify.connect(lambda *a: notes.append(a))
        hong.update_event(ev['event_id'], location='본사 8층')
        self.assertTrue(pump(lambda: notes))
        self.assertIn('일정이 변경되었습니다', notes[0][0])
        self.assertEqual([e['title'] for e in kim.events_in_room(rid)], ['분기 리뷰'])

    def test_daily_briefing_from_ai(self):
        self.server.daily_briefing = True
        hong = self.login('홍길동')
        self.assertTrue(pump(lambda: hong.direct_room_with(BOT_NAME) is not None, 5))
        rid = hong.direct_room_with(BOT_NAME)
        self.assertTrue(pump(lambda: (hong.room(rid).get('last_message') or {}).get('sender') == BOT_NAME, 5))
        self.assertIn('오늘의 브리핑', hong.room(rid)['last_message']['content'])


# 부모(E2ETest)의 테스트는 다시 돌리지 않는다 (setUp/헬퍼만 재사용)
for _name in [n for n in vars(E2ETest) if n.startswith('test_')]:
    setattr(E2E2025Test, _name, None)


if __name__ == '__main__':
    unittest.main()


class E2EV3Test(E2ETest):
    """v3 기능(명세 §8)을 실제 서버 + 실제 클라이언트 스택으로 검증"""

    def test_secret_chat_end_to_end_encryption(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        self.assertTrue(pump(lambda: hong.user('김철수').get('public_key') and kim.user('홍길동').get('public_key')))
        opened = []
        hong.room_opened.connect(opened.append)
        hong.open_secret('김철수')
        self.assertTrue(pump(lambda: opened))
        rid = opened[0]
        self.assertEqual(hong.room(rid)['type'], 'secret')
        self.assertTrue(pump(lambda: kim.room(rid) is not None))
        self.assertIsNone(hong.send_secret(rid, '이건 서버도 못 읽어요'))
        kim.load_history(rid)
        self.assertTrue(pump(lambda: any(m['kind'] == 'secret' for m in kim.room_messages(rid))))
        m = [m for m in kim.room_messages(rid) if m['kind'] == 'secret'][-1]
        self.assertEqual(m['secret_text'], '이건 서버도 못 읽어요')
        # 서버 DB에는 평문이 없다
        stored = self.server.store.get_message(m['msg_id'])['content']
        self.assertNotIn('서버도', stored)

    def test_call_lifecycle_and_media_relay(self):
        hong, kim = self.login('홍길동'), self.login('김철수')
        rid = self.open_direct(hong, '김철수')
        hong.start_call(rid, 'voice')
        self.assertTrue(pump(lambda: kim.call_for_room(rid) is not None))
        call_id = kim.call_for_room(rid)['call_id']
        kim.join_call(call_id)
        self.assertTrue(pump(lambda: '김철수' in (hong.calls.get(call_id) or {}).get('participants', {})))
        got = []
        kim.call_media.connect(lambda *a: got.append(a))
        hong.send_media(call_id, 'audio', 1, 'AAAA', codec='pcm')
        self.assertTrue(pump(lambda: got))
        self.assertEqual(got[0][1:4], ('홍길동', 'audio', 1))
        ended = []
        kim.call_ended.connect(ended.append)
        hong.leave_call(call_id)
        kim.leave_call(call_id)
        self.assertTrue(pump(lambda: ended))
        self.assertIsNone(kim.call_for_room(rid))

    def test_story_block_ladder_board_open_drawer(self):
        hong, kim, lee = self.login('홍길동'), self.login('김철수'), self.login('이영희')
        # 펑
        kim.post_story('text', '오늘 점심 맛집 🍜')
        self.assertTrue(pump(lambda: '김철수' in hong.story_owners()))
        story_id = hong.stories_of('김철수')[0]['story_id']
        hong.view_story(story_id)
        self.assertTrue(pump(lambda: '홍길동' in (kim.stories.get(story_id) or {}).get('viewers', [])))
        # 사다리 + 게시판
        opened = []
        hong.room_opened.connect(opened.append)
        hong.create_group(['김철수', '이영희'], name='점심팟')
        self.assertTrue(pump(lambda: opened))
        gid = opened[0]
        hong.create_ladder(gid, ['홍길동', '김철수', '이영희'], ['커피', '꽝', '간식'])
        hong.load_history(gid)
        self.assertTrue(pump(lambda: any(m['kind'] == 'ladder' for m in hong.room_messages(gid))))
        ladder = [m for m in hong.room_messages(gid) if m['kind'] == 'ladder'][0]['ladder']
        self.assertEqual(sorted(ladder['mapping'].values()), ['간식', '꽝', '커피'])
        hong.post_board(gid, '이번 주 회의록', '1. 일정 공유')
        self.assertTrue(pump(lambda: kim.board_posts(gid)))
        kim.comment_board(kim.board_posts(gid)[0]['post_id'], '확인했습니다')
        self.assertTrue(pump(lambda: hong.board_posts(gid) and hong.board_posts(gid)[0]['comments']))
        # 차단: 차단한 사람의 메시지는 오지 않는다
        lee.block_user('김철수')
        self.assertTrue(pump(lambda: lee.is_blocked('김철수')))
        lee.load_history(gid)
        pump(lambda: False, 0.3)
        kim.send_text(gid, '차단된 사람의 메시지')
        self.assertTrue(pump(lambda: any(m['content'] == '차단된 사람의 메시지' for m in hong.room_messages(gid))))
        pump(lambda: False, 0.4)
        self.assertFalse(any(m.get('content') == '차단된 사람의 메시지' for m in lee.room_messages(gid)))
        # 오픈채팅
        found = []
        lee.open_rooms_found.connect(found.append)
        hong.create_open_room('파이썬 스터디', '매주 화요일', ['python'])
        self.assertTrue(pump(lambda: any(r['type'] == 'open' for r in hong.rooms.values())))
        lee.search_open_rooms('파이썬')
        self.assertTrue(pump(lambda: found and found[-1]))
        oid = found[-1][0]['room_id']
        lee.join_open_room(oid, '파이썬초보')
        self.assertTrue(pump(lambda: lee.room(oid) is not None))
        self.assertTrue(pump(lambda: hong.member_name(oid, '이영희') == '파이썬초보'))
        # 톡서랍 (전체)
        path = os.path.join(self.dir, '자료.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('자료')
        hong.send_file(gid, path)
        drawer = []
        hong.drawer_loaded.connect(lambda kind, msgs: drawer.append((kind, msgs)))
        pump(lambda: False, 0.3)
        hong.query_drawer('file')
        self.assertTrue(pump(lambda: drawer and drawer[-1][1]))
        self.assertEqual(drawer[-1][1][0]['attachment']['name'], '자료.txt')

    def test_multi_profile_seen_by_chosen_friend(self):
        hong, kim, lee = self.login('홍길동'), self.login('김철수'), self.login('이영희')
        hong.set_multi_profiles([{'name': '회사용', 'display_name': '홍팀장', 'status_message': '근무 중',
                                  'viewers': ['김철수']}])
        self.assertTrue(pump(lambda: kim.display_name('홍길동') == '홍팀장'))
        self.assertEqual(kim.user('홍길동')['status_message'], '근무 중')
        pump(lambda: False, 0.3)
        self.assertEqual(lee.display_name('홍길동'), '홍길동')     # 지정하지 않은 친구는 기본 프로필


for _name in [n for n in vars(E2ETest) if n.startswith('test_')]:
    setattr(E2EV3Test, _name, None)



class ChatExportE2ETest(unittest.TestCase):
    """대화 내용 저장(명세 §9.5)을 실제 서버로: 기간 + 사진 포함 zip (E2ETest 의 테스트는 다시 돌리지 않는다)"""
    setUp, tearDown, login, open_direct = E2ETest.setUp, E2ETest.tearDown, E2ETest.login, E2ETest.open_direct

    def test_zip_export_with_real_attachment(self):
        import zipfile
        from datetime import date
        from client.kakao.chat_room import ChatRoomWindow
        a, b = self.login('저장A'), self.login('저장B')
        rid = self.open_direct(a, '저장B')
        a.send_text(rid, '사진 보냅니다')
        path = os.path.join(self.dir, 'photo.png')
        with open(path, 'wb') as f:
            f.write(png_bytes())
        self.assertIsNone(a.send_file(rid, path))
        self.assertTrue(pump(lambda: any(m.get('kind') == 'image' for m in b.room_messages(rid))
                             or any(m.get('attachment') for m in b.room_messages(rid))))
        win = ChatRoomWindow(b, rid)
        win.show()
        self.addCleanup(win.close)
        out = os.path.join(self.dir, 'export.zip')
        today = date.today()
        win.export_chat(out, fmt='zip', since=today, until=today, period=today.isoformat())
        self.assertTrue(pump(lambda: os.path.exists(out) and not win._export_requested, 10))
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            text = zf.read('대화.txt').decode('utf-8')
            photo = [n for n in names if n.startswith('files/')]
            self.assertEqual(len(photo), 1)
            self.assertEqual(zf.read(photo[0]), png_bytes())
        self.assertIn('사진 보냅니다', text)
        self.assertIn(f'저장한 기간 : {today.isoformat()}', text)
        self.assertIn(f'[{photo[0]}]', text)
        # 어제 하루만 고르면 오늘 대화는 없다
        yesterday = date.fromordinal(today.toordinal() - 1)
        out2 = os.path.join(self.dir, 'yesterday.txt')
        win.export_chat(out2, since=yesterday, until=yesterday)
        self.assertTrue(pump(lambda: os.path.exists(out2), 10))
        with open(out2, encoding='utf-8') as f:
            self.assertNotIn('사진 보냅니다', f.read())


class LoginFlowTests(unittest.TestCase):
    """로그인 창 뒤의 실제 경로: Session(아이디, 비밀번호) → join → wait_for_login (명세 §4.1)"""

    def setUp(self):
        app()
        self.dir = tempfile.mkdtemp(prefix='kcs_login_')
        self.server = KakaoServer(host='127.0.0.1', port=0, db_path=os.path.join(self.dir, 'kakao.db'),
                                  tick_seconds=0.2)
        self.server.serve_in_thread()
        self._old_home = os.environ.get('KCS_MESSENGER_HOME')
        os.environ['KCS_MESSENGER_HOME'] = os.path.join(self.dir, 'home')
        self.sessions = []

    def tearDown(self):
        for s in self.sessions:
            s.close()
        # 지우지 않고 되돌린다: 지우면 뒤 테스트가 실제 ~/.kcs_messenger(저장된 로그인)를 읽는다
        if self._old_home is None:
            os.environ.pop('KCS_MESSENGER_HOME', None)
        else:
            os.environ['KCS_MESSENGER_HOME'] = self._old_home
        pump(lambda: False, 0.2)
        self.server.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def ensure_registered(self, login_id, password):
        if self.server.store.password_hash(login_id) is None:      # 처음 사용 등록은 한 번만 (명세 §4.1)
            self.server.store.register_user(login_id, 'KCS 홀딩스', f'{abs(hash(login_id))}@kcs.test', password,
                                            employee_no=f'T{abs(hash(login_id))}'[:20])

    def attempt(self, login_id, password):
        from client.kakao.app import Session, wait_for_login
        self.ensure_registered(login_id, password)
        s = Session('127.0.0.1', self.server.port, login_id, password)
        self.sessions.append(s)
        s.connect()
        return s, wait_for_login(s.state, 5000)

    def test_wait_for_login_ok_then_wrong_password(self):
        s, result = self.attempt('로그인테스트', 'right-pw')
        self.assertEqual(result, ('ok', ''))
        self.assertEqual(s.state.me, '로그인테스트')
        _, result = self.attempt('로그인테스트', 'nope-pw')
        self.assertEqual(result, ('failed', '아이디 또는 비밀번호가 올바르지 않습니다.'))

    def test_first_use_registration_then_password_login(self):
        from client.kakao.app import Session, wait_for_login
        reg = {'company': 'KCS 물류', 'name': '처음사용', 'email': 'first@kcs.test', 'password': 'first-pw'}   # 사번 없음
        s = Session('127.0.0.1', self.server.port, '', registration=reg)
        self.sessions.append(s)
        s.connect()
        self.assertEqual(wait_for_login(s.state, 5000), ('ok', ''))
        self.assertEqual(s.state.me, '처음사용')
        self.assertEqual(s.username, 'first@kcs.test')                  # 다음부터의 로그인 아이디 = 이메일
        self.assertEqual(s.state.users['처음사용']['company'], 'KCS 물류')
        welcomes = []
        s.state.welcomed.connect(lambda: welcomes.append(1))
        self.assertTrue(s.reconnect()[0])                               # 재연결은 등록한 비밀번호로 join
        self.assertTrue(pump(lambda: welcomes))
        # 다음 실행: 이메일/비밀번호만으로 로그인
        s2 = Session('127.0.0.1', self.server.port, 'first@kcs.test', 'first-pw')
        self.sessions.append(s2)
        s2.connect()
        self.assertEqual(wait_for_login(s2.state, 5000), ('ok', ''))
        self.assertEqual(s2.state.me, '처음사용')
        # 같은 이름으로 다시 등록하면 거절 → 로그인 창에 사유
        s3 = Session('127.0.0.1', self.server.port, '', registration=dict(reg, email='x@kcs.test'))
        self.sessions.append(s3)
        s3.connect()
        outcome, reason = wait_for_login(s3.state, 5000)
        self.assertEqual(outcome, 'failed')
        self.assertIn('이미 등록된 이름', reason)

    def test_disabled_by_admin_goes_back_to_login_without_retry(self):
        s, result = self.attempt('중지대상', 'keep-pw')
        self.assertEqual(result[0], 'ok')
        s.window = object()                                  # 메인 창이 떠 있는 상태로 (재로그인 경로)
        reasons = []
        s.on_relogin_needed = reasons.append
        admin, admin_result = self.attempt('관리자테스트', 'admin-pw')
        self.assertEqual(admin_result[0], 'ok')
        self.server.store.set_admin('관리자테스트', True)
        admin.state.admin_set_disabled('중지대상', True)
        self.assertTrue(pump(lambda: reasons, 5))
        self.assertIn('사용이 중지된 계정', reasons[0])
        pump(lambda: False, 1.5)                               # 재연결하지 않는다
        self.assertFalse(s._retry_timer.isActive())
        self.assertNotIn('중지대상', self.server._online_users())

    def test_reconnect_reuses_password(self):
        s, result = self.attempt('재연결테스트', 'keep-pw')
        self.assertEqual(result[0], 'ok')
        failures, welcomes = [], []
        s.state.login_failed.connect(failures.append)
        s.state.welcomed.connect(lambda: welcomes.append(1))
        ok, _ = s.reconnect()
        self.assertTrue(ok)
        self.assertTrue(pump(lambda: welcomes))                       # 두 번째 welcome
        self.assertTrue(pump(lambda: len(self.server._by_user.get('재연결테스트', ())) == 1))  # 예전 연결은 닫힘
        self.assertEqual(failures, [])

    def test_remember_issues_token_and_auto_login(self):
        from client.kakao import login
        from client.kakao.app import remember_login
        s, result = self.attempt_remember('자동로그인', 'auto-pw')
        self.assertEqual(result[0], 'ok')
        token = s.state.login_token
        self.assertTrue(token)
        remember_login(s, '자동로그인', True)
        saved = login.load_saved_login()
        self.assertEqual((saved['login_id'], saved['token'], saved['auto_login']), ('자동로그인', token, True))
        if not sys.platform.startswith('win'):              # POSIX 권한 (Windows 는 프로필 폴더 권한)
            self.assertEqual(os.stat(login.saved_path()).st_mode & 0o777, 0o600)
        with open(login.saved_path(), encoding='utf-8') as f:
            self.assertNotIn('auto-pw', f.read())                        # 비밀번호 원문은 저장하지 않는다
        # 다음 실행: 비밀번호 없이 토큰으로
        from client.kakao.app import Session, wait_for_login
        s2 = Session('127.0.0.1', self.server.port, '자동로그인', token=token)
        self.sessions.append(s2)
        s2.connect()
        self.assertEqual(wait_for_login(s2.state, 5000), ('ok', ''))
        # 로그아웃: 토큰 폐기 → 같은 토큰은 거절
        s2.state.revoke_login_token(token)
        self.assertTrue(pump(lambda: not self.server.store.check_login_token('자동로그인', token)))
        s3 = Session('127.0.0.1', self.server.port, '자동로그인', token=token)
        self.sessions.append(s3)
        s3.connect()
        self.assertEqual(wait_for_login(s3.state, 5000)[0], 'failed')

    def test_logout_revokes_token_clears_saved_login_and_relaunches(self):
        from client.kakao import login
        from client.kakao.app import perform_logout, remember_login
        s, result = self.attempt_remember('로그아웃테스트', 'out-pw')
        self.assertEqual(result[0], 'ok')
        token = s.state.login_token
        remember_login(s, '로그아웃테스트', True)
        self.assertIsNotNone(login.load_saved_login())
        relaunched = []
        perform_logout(s, relaunch=lambda: relaunched.append(1))
        self.assertEqual(relaunched, [1])                                     # 로그인 창부터 다시
        self.assertIsNone(login.load_saved_login())                           # 저장된 로그인·자동 로그인 해제
        self.assertFalse(s.conn.is_connected)
        self.assertTrue(pump(lambda: not self.server.store.check_login_token('로그아웃테스트', token)))
        self.assertTrue(pump(lambda: not self.server._by_user.get('로그아웃테스트')))   # 서버에서도 접속 종료

    def test_reconnect_rejected_asks_relogin(self):
        s, _ = self.attempt('재로그인', 'pw-one')
        s.window = object()                                            # 메인 창이 떠 있는 상태
        reasons = []
        s.on_relogin_needed = reasons.append
        self.server.store.set_password('재로그인', 'pw-two')            # 다른 곳에서 비밀번호 변경
        s.state.login_token = ''
        ok, _ = s.reconnect()
        self.assertTrue(ok)
        self.assertTrue(pump(lambda: reasons))
        self.assertTrue(s._closing)                                    # 재시도 멈춤

    def attempt_remember(self, login_id, password):
        from client.kakao.app import Session, wait_for_login
        self.ensure_registered(login_id, password)
        s = Session('127.0.0.1', self.server.port, login_id, password, remember=True)
        self.sessions.append(s)
        s.connect()
        return s, wait_for_login(s.state, 5000)
