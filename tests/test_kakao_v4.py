# -*- coding: utf-8 -*-
"""v4 (카카오톡 2026 업데이트 반영, 명세 §9)
- 대화 요약 · 공감 30종 · 음성메시지 · 연락처 보내기 · 통화 기록('통화' 폴더) · 대화 내보내기 · 소식 숨기기
"""

import base64
import os
import unittest

os.environ.setdefault('KCS_RAG_LLM', 'off')
os.environ.setdefault('KCS_LINK_PREVIEW', 'off')

from tests.test_kakao_server import ServerTestBase  # noqa: E402
from server.kakao import summary  # noqa: E402

WAV = b'RIFF' + b'\x00' * 40   # 형식 검사는 mime 기준


def voice_payload(room_id, seconds=3):
    return {'type': 'send', 'room_id': room_id, 'kind': 'voice', 'content': str(seconds),
            'attachment': {'name': 'voice.wav', 'mime': 'audio/wav',
                           'data_b64': base64.b64encode(WAV).decode('ascii')}}


class SummaryUnitTest(unittest.TestCase):
    def test_extractive_summary_picks_decisions_requests_and_questions(self):
        names = {'a': '김철수', 'b': '홍길동'}.get
        msgs = [
            {'msg_id': 1, 'sender': 'a', 'kind': 'text', 'content': '이번 프로젝트 일정 이야기해요'},
            {'msg_id': 2, 'sender': 'b', 'kind': 'text', 'content': '출시는 다음 주 금요일로 확정했습니다'},
            {'msg_id': 3, 'sender': 'a', 'kind': 'text', 'content': '테스트 결과 공유해 주세요'},
            {'msg_id': 4, 'sender': 'b', 'kind': 'text', 'content': '디자인 시안은 누가 보나요?'},
            {'msg_id': 5, 'sender': 'a', 'kind': 'file', 'content': '기획서.pdf'},
            {'msg_id': 6, 'sender': 'b', 'kind': 'text', 'content': '지운 메시지', 'deleted': True},
            {'msg_id': 7, 'sender': '', 'kind': 'system', 'content': '홍길동님이 들어왔습니다'},
            {'msg_id': 8, 'sender': 'a', 'kind': 'text', 'content': '프로젝트 회의는 내일 오전 10시'},
        ]
        text, source = summary.summarize(msgs, names, llm=None)
        self.assertEqual(source, 'extractive')
        self.assertIn('메시지 6개 · 참여 2명', text)
        self.assertIn('[결정] 홍길동: 출시는 다음 주 금요일로 확정했습니다', text)
        self.assertIn('[요청] 김철수: 테스트 결과 공유해 주세요', text)
        self.assertIn('[질문] 홍길동: 디자인 시안은 누가 보나요?', text)
        self.assertIn('파일 1', text)
        self.assertIn('프로젝트', text)
        self.assertNotIn('지운 메시지', text)

    def test_empty_and_llm_failure_fallback(self):
        self.assertEqual(summary.summarize([], str)[0], summary.NOTHING_TO_SUMMARIZE)

        class Broken:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError('network')
        text, source = summary.summarize([{'msg_id': 1, 'sender': 'a', 'kind': 'text', 'content': '안녕'}],
                                         str, llm=Broken())
        self.assertEqual(source, 'extractive')
        self.assertIn('메시지 1개', text)


class SummaryFatalTest(unittest.TestCase):
    def test_auth_error_calls_on_fatal(self):
        class AuthenticationError(Exception):
            pass

        class Client:
            class messages:
                @staticmethod
                def create(**kw):
                    raise AuthenticationError('bad key')
        hit = []
        text, source = summary.summarize([{'msg_id': 1, 'sender': 'a', 'kind': 'text', 'content': '안녕'}],
                                         str, llm=Client(), on_fatal=lambda: hit.append(1))
        self.assertEqual((source, hit), ('extractive', [1]))


class V4ServerTest(ServerTestBase):
    def test_contact_and_voice_messages(self):
        a, b = self.connect(), self.connect()
        room = self.group(a, [b])
        rid = room['room_id']
        a.send({'type': 'send', 'room_id': rid, 'kind': 'contact', 'content': b.username})
        m = b.wait_for('msg_new', lambda x: x['message']['kind'] == 'contact')['message']
        self.assertEqual(m['content'], b.username)
        a.expect_error({'type': 'send', 'room_id': rid, 'kind': 'contact', 'content': 'nobody_xyz'})
        a.send(voice_payload(rid, 7))
        m = b.wait_for('msg_new', lambda x: x['message']['kind'] == 'voice')['message']
        self.assertEqual(m['content'], '7')
        self.assertEqual(m['attachment']['mime'], 'audio/wav')
        a.expect_error(voice_payload(rid, 0))
        a.expect_error(voice_payload(rid, 301))
        bad = voice_payload(rid)
        bad['attachment']['mime'] = 'image/png'
        a.expect_error(bad)
        drawer = a.request({'type': 'drawer_query', 'kind': 'file'}, 'drawer_result')
        self.assertIn('voice', [x['kind'] for x in drawer['messages']])

    def test_reaction_kinds_capped_at_30(self):
        a, b = self.connect(), self.connect()
        room = self.group(a, [b])
        a.send({'type': 'send', 'room_id': room['room_id'], 'kind': 'text', 'content': '공감 테스트'})
        msg_id = a.wait_for('msg_new', lambda x: x['message']['content'] == '공감 테스트')['message']['msg_id']
        emojis = [chr(0x1F600 + i) for i in range(30)]
        for e in emojis:
            a.send({'type': 'react', 'msg_id': msg_id, 'emoji': e})
        a.wait_for('msg_updated', lambda x: len(x['message']['reactions']) == 30)
        b.expect_error({'type': 'react', 'msg_id': msg_id, 'emoji': '🦄'})
        b.send({'type': 'react', 'msg_id': msg_id, 'emoji': emojis[0]})   # 이미 있는 종류에는 더할 수 있음
        b.wait_for('msg_updated', lambda x: len(x['message']['reactions'].get(emojis[0], [])) == 2)

    def test_room_summary(self):
        a, b = self.connect(), self.connect()
        room = self.group(a, [b])
        rid = room['room_id']
        for text in ('보고서 마감은 금요일까지로 확정했습니다', '자료 검토 부탁드려요', '회의실은 어디인가요?'):
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': text})
            b.wait_for('msg_new', lambda x, t=text: x['message']['content'] == t)
        res = b.request({'type': 'room_summary', 'room_id': rid}, 'room_summary', timeout=6)
        self.assertEqual(res['source'], 'extractive')
        self.assertEqual(res['count'], 3)
        self.assertIn('[결정]', res['summary'])
        self.assertIn('[요청]', res['summary'])
        hist = b.request({'type': 'history', 'room_id': rid, 'limit': 50}, 'history')
        b.send({'type': 'read', 'room_id': rid, 'last_msg_id': hist['messages'][-1]['msg_id']})
        a.wait_for('read_update', lambda x: x['username'] == b.username)
        res = b.request({'type': 'room_summary', 'room_id': rid, 'unread_only': True}, 'room_summary', timeout=6)
        self.assertEqual(res['count'], 0)
        self.assertEqual(res['summary'], summary.NOTHING_TO_SUMMARIZE)

    def test_call_history_and_missed(self):
        a, b = self.connect(), self.connect()
        room = self.group(a, [b])
        rid = room['room_id']
        a.send({'type': 'call_start', 'room_id': rid, 'mode': 'voice'})
        call = a.wait_for('call_updated', lambda x: x['call']['room_id'] == rid)['call']
        a.send({'type': 'call_leave', 'call_id': call['call_id']})
        a.wait_for('call_ended', lambda x: x['call_id'] == call['call_id'])
        hist_a = a.request({'type': 'call_history'}, 'call_history')
        hist_b = b.request({'type': 'call_history'}, 'call_history')
        entry_a = next(c for c in hist_a['calls'] if c['call_id'] == call['call_id'])
        entry_b = next(c for c in hist_b['calls'] if c['call_id'] == call['call_id'])
        self.assertEqual((entry_a['mode'], entry_a['host'], entry_a['room_id']), ('voice', a.username, rid))
        self.assertTrue(entry_a['joined'])
        self.assertTrue(entry_b['missed'])       # b 는 받지 않음 → 부재중
        outsider = self.connect()
        self.assertEqual(outsider.request({'type': 'call_history'}, 'call_history')['calls'], [])

    def test_room_export(self):
        a, b = self.connect(), self.connect()
        room = self.group(a, [b])
        rid = room['room_id']
        for i in range(3):
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': f'내보내기 {i}'})
            b.wait_for('msg_new', lambda x, i=i: x['message']['content'] == f'내보내기 {i}')
        res = b.request({'type': 'room_export', 'room_id': rid}, 'room_export')
        texts = [m['content'] for m in res['messages'] if m['kind'] == 'text']
        self.assertEqual(texts, ['내보내기 0', '내보내기 1', '내보내기 2'])
        self.assertFalse(res['truncated'])
        self.connect().expect_error({'type': 'room_export', 'room_id': rid})
        import server.kakao.server as srv_mod
        old = srv_mod.EXPORT_CHUNK
        srv_mod.EXPORT_CHUNK = 2                     # 조각으로 나눠 보내기
        try:
            b.send({'type': 'room_export', 'room_id': rid})
            n = (len(res['messages']) + 1) // 2
            self.assertGreaterEqual(n, 2)
            parts = [b.wait_for('room_export', lambda x, k=k: x.get('part') == k) for k in range(n)]
            self.assertEqual([p['done'] for p in parts], [False] * (n - 1) + [True])
            self.assertEqual(sum(len(p['messages']) for p in parts), len(res['messages']))
        finally:
            srv_mod.EXPORT_CHUNK = old

    def test_room_export_period(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        days = ['2026-01-01', '2026-01-05', '2026-01-10']
        for i, _day in enumerate(days):
            a.send({'type': 'send', 'room_id': rid, 'kind': 'text', 'content': f'기간 {i}'})
            b.wait_for('msg_new', lambda x, i=i: x['message']['content'] == f'기간 {i}')
        store = self.server.store
        with store._lock:                                    # 메시지 날짜를 과거로 옮겨 기간 필터를 확인
            for i, day in enumerate(days):
                store._db.execute("UPDATE messages SET timestamp=? WHERE room_id=? AND content=?",
                                  (f'{day}T10:00:00', rid, f'기간 {i}'))
            store._db.execute("UPDATE messages SET timestamp='2025-12-31T09:00:00' WHERE room_id=? AND kind='system'",
                              (rid,))
            store._db.commit()
        texts = lambda res: [m['content'] for m in res['messages'] if m['kind'] == 'text']   # noqa: E731
        res = b.request({'type': 'room_export', 'room_id': rid, 'since': '2026-01-04', 'until': '2026-01-06'},
                        'room_export')
        self.assertEqual((texts(res), res['since'], res['until']), (['기간 1'], '2026-01-04', '2026-01-06'))
        res = b.request({'type': 'room_export', 'room_id': rid, 'since': '2026-01-05'}, 'room_export')
        self.assertEqual(texts(res), ['기간 1', '기간 2'])                      # 그날 포함, 끝 없음
        res = b.request({'type': 'room_export', 'room_id': rid, 'until': '2026-01-01'}, 'room_export')
        self.assertEqual(texts(res), ['기간 0'])
        for bad, message in (({'since': '2026/01/01'}, 'YYYY-MM-DD'), ({'since': '2026-02-30'}, 'YYYY-MM-DD'),
                             ({'since': '2026-01-10', 'until': '2026-01-01'}, '시작 날짜')):
            err = b.expect_error({'type': 'room_export', 'room_id': rid, **bad})
            self.assertIn(message, err['message'])


# ======================================================================== 클라이언트 (상태 · 화면)
from datetime import datetime, timedelta  # noqa: E402
from unittest import mock  # noqa: E402

from tests.kakao_fakes import make_demo_state, app  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


def pump(n=5):
    for _ in range(n):
        app().processEvents()


def push_msg(conn, **kw):
    base = dict(room_id='r1', sender='김철수', timestamp=datetime.now().isoformat(timespec='seconds'),
                reactions={}, mentions=[], deleted=False)
    base.update(kw)
    conn.push('msg_new', message=base)
    return base


class V4StateTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_actions_send_protocol(self):
        s, c = self.state, self.conn
        s.send_contact('r1', '박민수')
        self.assertEqual(c.last('send'), {'room_id': 'r1', 'kind': 'contact', 'content': '박민수'})
        self.assertIsNone(s.send_voice('r1', b'RIFFxxxx', 4.4))
        sent = c.last('send')
        self.assertEqual((sent['kind'], sent['content'], sent['attachment']['mime']), ('voice', '4', 'audio/wav'))
        self.assertIn('1초', s.send_voice('r1', b'x', 0.3))
        self.assertIn('5분', s.send_voice('r1', b'x', 301))
        s.request_summary('r1', since_msg_id=5)
        self.assertEqual(c.last('room_summary'), {'room_id': 'r1', 'unread_only': False, 'since_msg_id': 5})
        s.request_export('r2')
        self.assertEqual(c.last('room_export'), {'room_id': 'r2'})
        s.request_call_history()
        self.assertEqual(c.last('call_history'), {'limit': 100})

    def test_previews_and_forward_contact(self):
        s = self.state
        self.assertEqual(s.message_preview({'kind': 'voice', 'content': '75'}), '음성메시지 1:15')
        self.assertEqual(s.message_preview({'kind': 'contact', 'content': '박민수'}), '연락처: 박민수')
        s.forward({'kind': 'contact', 'content': '박민수'}, 'r2')
        self.assertEqual(self.conn.last('send'), {'room_id': 'r2', 'kind': 'contact', 'content': '박민수'})

    def test_call_history_folder(self):
        s, c = self.state, self.conn
        got = []
        s.call_history_changed.connect(lambda: got.append(1))
        now = datetime.now()
        c.push('call_history', calls=[
            {'call_id': 9, 'room_id': 'r2', 'mode': 'voice', 'host': '박민수', 'started_at': now.isoformat(),
             'duration_seconds': 0, 'participants': ['박민수'], 'joined': False, 'missed': True},
            {'call_id': 8, 'room_id': 'r1', 'mode': 'video', 'host': '홍길동',
             'started_at': (now - timedelta(hours=1)).isoformat(), 'duration_seconds': 185,
             'participants': ['홍길동', '김철수'], 'joined': True, 'missed': False},
            {'call_id': 7, 'room_id': 'r2', 'mode': 'voice', 'host': '홍길동',
             'started_at': (now - timedelta(hours=2)).isoformat(), 'duration_seconds': 30,
             'participants': ['홍길동', '박민수'], 'joined': True, 'missed': False}])
        self.assertTrue(got)
        self.assertEqual(s.missed_call_count(), 1)
        self.assertEqual([r['room_id'] for r in s.rooms_in_folder('통화')], ['r2', 'r1'])
        self.assertIn(('통화', 1), s.chat_folders())
        c.sent.clear()
        c.push('call_ended', call_id=8, room_id='r1', duration_seconds=3)
        self.assertEqual(c.last('call_history'), {'limit': 100})     # 통화가 끝나면 기록 새로 받기

    def test_export_text_kakao_format(self):
        s = self.state
        msgs = [
            {'msg_id': 1, 'room_id': 'r1', 'sender': '', 'kind': 'system', 'content': '김철수님이 들어왔습니다.',
             'timestamp': '2026-10-08T09:05:00'},
            {'msg_id': 2, 'room_id': 'r1', 'sender': '김철수', 'kind': 'text', 'content': '안녕하세요',
             'timestamp': '2026-10-08T09:06:00'},
            {'msg_id': 3, 'room_id': 'r1', 'sender': '홍길동', 'kind': 'file', 'content': '보고서.pdf',
             'attachment': {'name': '보고서.pdf'}, 'timestamp': '2026-10-09T14:30:00'},
            {'msg_id': 4, 'room_id': 'r1', 'sender': '홍길동', 'kind': 'text', 'content': 'x', 'deleted': True,
             'timestamp': '2026-10-09T14:31:00'}]
        text = s.export_text('r1', msgs, saved_at=datetime(2026, 10, 9, 15, 0, 0))
        lines = text.splitlines()
        self.assertEqual(lines[0], '프로젝트 팀 님과 카카오톡 대화')
        self.assertEqual(lines[1], '저장한 날짜 : 2026-10-09 15:00:00')
        self.assertIn('--------------- 2026년 10월 8일 목요일 ---------------', lines)
        self.assertIn('김철수님이 들어왔습니다.', lines)
        self.assertIn('[김철수] [오전 9:06] 안녕하세요', lines)
        self.assertIn('--------------- 2026년 10월 9일 금요일 ---------------', lines)
        self.assertIn('[홍길동] [오후 2:30] 파일: 보고서.pdf', lines)
        self.assertIn('[홍길동] [오후 2:31] 삭제된 메시지입니다.', lines)

    def test_story_hide(self):
        s, c = self.state, self.conn
        now = datetime.now()
        c.push('story_list', stories=[
            {'story_id': 1, 'owner': '김철수', 'kind': 'text', 'content': '출장 중', 'bg_color': '#2A386C',
             'created_at': now.isoformat(), 'expires_at': (now + timedelta(hours=5)).isoformat(), 'viewers': []}])
        self.assertIn('김철수', s.story_owners())
        s.set_story_hidden('김철수', True)
        self.assertTrue(s.is_story_hidden('김철수'))
        self.assertNotIn('김철수', s.story_owners())
        self.assertIn('김철수', [u['username'] for u in s.friends()])      # 친구 관계는 유지
        s.set_story_hidden('김철수', False)
        self.assertIn('김철수', s.story_owners())


class V4ChatRoomUiTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()
        from client.kakao.chat_room import ChatRoomWindow
        self.Win = ChatRoomWindow

    def open(self, rid='r1'):
        win = self.Win(self.state, rid)
        win.resize(420, 700)
        win.show()
        pump()
        self.addCleanup(win.close)
        return win

    def test_contact_card_and_picker(self):
        from client.kakao.chat_room import ContactCard, ContactPickDialog
        win = self.open()
        m = push_msg(self.conn, msg_id=101, kind='contact', content='박민수')
        pump()
        card = win.rows[101].card
        self.assertIsInstance(card, ContactCard)
        self.assertEqual(card.title.text(), '박민수')
        card.chat_button.click()
        self.assertEqual(self.conn.last('room_open_direct'), None)   # r2 가 이미 있음 → 바로 열기
        dlg = win.open_contact_picker(exec_=False)
        self.assertIsInstance(dlg, ContactPickDialog)
        dlg.search.setText('이영희')
        self.assertEqual(dlg.username(), '이영희')
        with mock.patch.object(ContactPickDialog, 'exec_', return_value=1), \
                mock.patch.object(ContactPickDialog, 'username', return_value='이영희'):
            win.open_contact_picker()
        self.assertEqual(self.conn.last('send'), {'room_id': 'r1', 'kind': 'contact', 'content': '이영희'})
        self.assertIn('전달', [a.text() for a in win.build_context_menu(m).actions()])

    def test_voice_record_send_and_play(self):
        import math
        import struct
        from client.kakao.chat_room import VoiceCard
        from client.kakao.voice import VoiceRecorder, VoicePlayer, pcm_to_wav
        win = self.open()
        rec = VoiceRecorder()
        dlg = win.open_voice_recorder(recorder=rec, exec_=False)
        rec.recording = True                       # 장치 없이 PCM 주입
        tone = b''.join(struct.pack('<h', int(6000 * math.sin(i / 8))) for i in range(16000 * 3))
        rec.feed(tone)
        self.assertIn('0:03', dlg.time_label.text())
        self.assertGreater(dlg.meter.level, 0)
        dlg.finish()
        self.assertEqual(dlg.result(), 1)
        self.assertIsNone(win.send_voice_from(dlg))
        sent = self.conn.last('send')
        self.assertEqual((sent['kind'], sent['content']), ('voice', '3'))
        # 수신한 음성메시지 말풍선 → 재생
        push_msg(self.conn, msg_id=102, kind='voice', content='3',
                 attachment={'attachment_id': 77, 'name': 'v.wav', 'mime': 'audio/wav', 'size': 96044})
        pump()
        card = win.rows[102].card
        self.assertIsInstance(card, VoiceCard)
        self.assertEqual(card.duration.text(), '0:03')
        card.play_button.click()
        self.assertEqual(self.conn.last('attachment_get'), {'attachment_id': 77})
        self.conn.push('attachment_data', attachment_id=77, name='v.wav', mime='audio/wav',
                       data_b64=base64.b64encode(pcm_to_wav(tone)).decode('ascii'))
        pump()
        player = VoicePlayer.instance()
        self.assertTrue(player.is_playing(card.key()))
        self.assertTrue(card.play_button.playing)
        self.assertEqual(card.play_button.accessibleName(), '음성메시지 정지')
        self.assertTrue(card.real_wave)                     # delight: 실제 소리 모양 파형
        self.assertGreater(len(dlg.meter.history), 0)       # overdrive: 녹음 중 실시간 파형 기록
        self.assertGreater(dlg.meter.progress, 0)
        card.play_button.click()                   # 다시 누르면 정지
        self.assertFalse(player.is_playing(card.key()))
        self.assertFalse(card.play_button.playing)

    def test_voice_recorder_error_resets_button(self):
        from client.kakao.voice import VoiceRecordDialog, VoiceRecorder
        rec = VoiceRecorder()
        dlg = VoiceRecordDialog(recorder=rec, autostart=False)
        with mock.patch.object(VoiceRecorder, 'start', lambda self_: (self_.error.emit('마이크를 찾을 수 없습니다'), False)[1]):
            dlg.toggle()
        self.assertEqual(dlg.record_button.text(), '다시 녹음')
        self.assertFalse(dlg.record_button.icon().isNull())   # 그린 아이콘
        self.assertIn('마이크를 찾을 수 없습니다', dlg.status.text())
        self.assertFalse(rec.recording)
        self.assertFalse(dlg.send_button.isEnabled())

    def test_voice_recorder_limit(self):
        from client.kakao.voice import VoiceRecorder, BYTES_PER_SECOND
        rec = VoiceRecorder()
        rec.recording = True
        hit = []
        rec.limit_reached.connect(lambda: hit.append(1))
        rec.feed(b'\x00\x00' * (BYTES_PER_SECOND // 2) * 301)
        self.assertEqual(hit, [1])
        self.assertFalse(rec.recording)
        self.assertEqual(rec.seconds, 300)

    def test_summary_menu_chip_and_dialog(self):
        from client.kakao.chat_room import SummaryDialog
        self.state.rooms['r1']['unread_count'] = 25
        self.state.rooms['r1'].setdefault('read_pointers', {})['홍길동'] = 4
        win = self.open()
        self.assertFalse(win.summary_chip.isHidden())
        self.assertIn('25개', win.summary_chip.text())
        self.assertIn('대화 요약', [a.text() for a in win.build_room_menu().actions()])
        dlg = win._summarize_unread()
        self.assertIsInstance(dlg, SummaryDialog)
        self.assertTrue(win.summary_chip.isHidden())
        self.assertEqual(self.conn.last('room_summary'), {'room_id': 'r1', 'unread_only': True, 'since_msg_id': 4})
        self.conn.push('room_summary', room_id='r1', summary='메시지 7개 · 참여 3명\n• [결정] 회의는 2시',
                       source='extractive', count=7, unread_only=True)
        self.assertIn('[결정] 회의는 2시', dlg.text())
        self.assertIn('안 읽은 대화 7개', dlg.meta.text())
        self.assertIn('자동 요약', dlg.meta.text())
        self.conn.push('room_summary', room_id='r2', summary='다른 방', source='claude', count=1)
        self.assertNotIn('다른 방', dlg.text())
        dlg.close()

    def test_no_summary_chip_when_few_unread(self):
        self.state.rooms['r1']['unread_count'] = 3
        win = self.open()
        self.assertTrue(win.summary_chip.isHidden())

    def test_export_writes_txt(self):
        import tempfile
        win = self.open()
        self.assertIn('대화 내용 저장… (텍스트 · 엑셀 · 사진 포함)', [a.text() for a in win.build_room_menu().actions()])
        path = os.path.join(tempfile.mkdtemp(), 'chat.txt')
        win.export_chat(path)
        self.assertEqual(self.conn.last('room_export'), {'room_id': 'r1'})
        msgs = [m for m in self.state.room_messages('r1')]
        self.conn.push('room_export', room_id='r1', messages=msgs, truncated=False)
        with open(path, encoding='utf-8') as f:
            text = f.read()
        self.assertTrue(text.startswith('프로젝트 팀 님과 카카오톡 대화'))
        self.assertIn('[김철수]', text)
        self.assertIn('다들 내일 회의 시간 괜찮아요?', text)
        self.assertIn('저장했습니다', win.toast_label.text())
        self.assertTrue(win.default_export_name().startswith('KCS Talk_프로젝트 팀_'))

    def test_reaction_picker_and_overflow(self):
        from client.kakao.chat_room import ReactionPicker, MORE_REACTIONS
        win = self.open()
        self.assertGreaterEqual(len(MORE_REACTIONS), 72)
        m = push_msg(self.conn, msg_id=103, kind='text', content='공감 많이',
                     reactions={e: ['김철수'] for e in MORE_REACTIONS[:9]})
        pump()
        row = win.rows[103]
        shown = [e for e, b in row.reaction_buttons.items() if not b.isHidden()]
        self.assertTrue(1 <= len(shown) <= 6)
        self.assertEqual(row.more_reactions.text(), f'+{9 - len(shown)}')
        # 공감 줄이 말풍선 최대 폭을 넘지 않아 대화 영역이 옆으로 늘어나지 않음
        self.assertLessEqual(win.container.minimumSizeHint().width(), win.scroll.viewport().width())
        dlg = win.show_reaction_picker(m, exec_=False)
        self.assertIsInstance(dlg, ReactionPicker)
        dlg.buttons['🚀'].click()
        self.assertEqual(self.conn.last('react'), {'msg_id': 103, 'emoji': '🚀'})
        sub = [a for a in win.build_context_menu(m).actions() if a.text() == '공감'][0].menu()
        self.assertIn('더 많은 공감…', [a.text() for a in sub.actions()])


class V4ChatsAndFriendsUiTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_call_folder_rows(self):
        from client.kakao.chats_tab import ChatsTab, CallLogRow
        now = datetime.now()
        self.conn.push('call_history', calls=[
            {'call_id': 9, 'room_id': 'r2', 'mode': 'voice', 'host': '박민수', 'started_at': now.isoformat(),
             'duration_seconds': 0, 'participants': ['박민수'], 'joined': False, 'missed': True},
            {'call_id': 8, 'room_id': 'r1', 'mode': 'video', 'host': '홍길동',
             'started_at': (now - timedelta(hours=1)).isoformat(), 'duration_seconds': 185,
             'participants': ['홍길동', '김철수'], 'joined': True, 'missed': False}])
        tab = ChatsTab(self.state, self.state.settings)
        tab.resize(380, 600)
        tab.show()
        self.assertEqual(tab.folder_bar.chip('통화').text(), '통화 1')
        tab.select_folder('통화')
        pump()
        rows = [tab.list.itemWidget(tab.list.item(i)) for i in range(tab.list.count())]
        self.assertTrue(all(isinstance(r, CallLogRow) for r in rows))
        self.assertEqual([r.room_id for r in rows], ['r2', 'r1'])
        self.assertIn('부재중', rows[0].sub.text())
        self.assertIn('페이스톡 · 발신 · 3:05', rows[1].sub.text())
        self.assertIn('부재중 1건', tab.header.subtitle.text())
        opened = []
        self.state.room_opened.connect(opened.append)
        tab._activate(tab.list.item(0))
        self.assertEqual(opened, ['r2'])
        self.conn.push('call_history', calls=[])
        self.assertIn('통화 기록이 없습니다', tab.empty_label.text())
        tab.close()

    def test_friend_story_hide_menu(self):
        from client.kakao.friends_tab import FriendsTab
        now = datetime.now()
        self.state.settings.set('social_features', True)
        self.conn.push('story_list', stories=[
            {'story_id': 1, 'owner': '김철수', 'kind': 'text', 'content': '출장', 'bg_color': '#2A386C',
             'created_at': now.isoformat(), 'expires_at': (now + timedelta(hours=5)).isoformat(), 'viewers': []}])
        tab = FriendsTab(self.state, self.state.settings)
        tab.show()
        pump()
        self.assertIsNotNone(tab.story_bar.bubble_for('김철수'))
        menu = tab.story_bar.bubble_menu('김철수')
        [a for a in menu.actions() if '소식 숨기기' in a.text()][0].trigger()
        pump()
        self.assertIsNone(tab.story_bar.bubble_for('김철수'))
        fmenu = tab.context_menu_for(('friend', '김철수', 'online'))
        self.assertIn('소식 다시 보기', [a.text() for a in fmenu.actions()])
        tab.close()


class OrgContactTest(unittest.TestCase):
    def test_org_contact_menu_sends_contact(self):
        from client.kakao.org_tab import OrgTab
        state, conn = make_demo_state()
        tab = OrgTab(state, state.settings)
        menu = tab.context_menu_for(('member', '박민수'))
        self.assertIn('연락처 보내기…', [a.text() for a in menu.actions()])
        self.assertEqual(tab.send_contact_to_room('박민수', room_id='r1'), 'r1')
        self.assertEqual(conn.last('send'), {'room_id': 'r1', 'kind': 'contact', 'content': '박민수'})


class AccessibilityTest(unittest.TestCase):
    """design:accessibility-review 반영 (WCAG 2.1 AA)"""

    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_keyboard_message_navigation_and_menu(self):
        from client.kakao.chat_room import ChatRoomWindow
        win = ChatRoomWindow(self.state, 'r1')
        win.resize(420, 700)
        win.show()
        pump()
        self.addCleanup(win.close)
        keys = {sc.key().toString() for sc in win.kb_shortcuts}
        self.assertTrue({'Alt+Up', 'Alt+Down', 'Shift+F10'} <= keys)
        last = win.kb_select(-1)                       # 처음엔 가장 최근 메시지
        self.assertEqual(last, win._kb_rows()[-1])
        prev = win.kb_select(-1)
        self.assertEqual(prev, win._kb_rows()[-2])
        self.assertTrue(win.rows[prev]._kb)
        self.assertFalse(win.rows[last]._kb)
        self.assertIn(':', win.kb_status.accessibleName())
        win.kb_select(1)                               # 다시 가장 최근(삭제되지 않은 텍스트)
        menu = win.kb_menu(exec_=False)
        self.assertIn('공감', [a.text() for a in menu.actions()])
        win._on_escape()
        self.assertIsNone(win._kb_msg_id)
        self.assertEqual(win.edit.accessibleName(), '메시지 입력')

    def test_profile_from_message_menu(self):
        from client.kakao.chat_room import ChatRoomWindow
        win = ChatRoomWindow(self.state, 'r1')
        self.addCleanup(win.close)
        m = self.state.find_message('r1', 2)       # 김철수의 메시지
        self.assertIn('프로필 보기', [a.text() for a in win.build_context_menu(m).actions()])
        mine = self.state.find_message('r1', 4)
        self.assertNotIn('프로필 보기', [a.text() for a in win.build_context_menu(mine).actions()])

    def test_badges_read_with_context_and_no_glyph_icons(self):
        import re
        from client.kakao.main_window import MainWindow
        from client.kakao.chats_tab import ChatsTab, RoomRow
        win = MainWindow(self.state, self.state.settings)
        win.sidebar.set_unread(4)
        self.assertEqual(win.sidebar.buttons['chats'].accessibleName(), '채팅, 안 읽은 메시지 4개')
        tab = ChatsTab(self.state, self.state.settings)
        row = [tab.list.itemWidget(tab.list.item(i)) for i in range(tab.list.count())
               if isinstance(tab.list.itemWidget(tab.list.item(i)), RoomRow)][0]
        self.assertIn('안 읽은 메시지', row.accessibleName())
        from client.kakao.chat_room import ChatRoomWindow
        room = ChatRoomWindow(self.state, 'r1')
        self.assertEqual(room.notice_close.text(), '')
        self.assertFalse(room.notice_close.icon().isNull())
        room._refresh_notice()
        self.assertEqual(room.notice_toggle.text(), '')                  # ▾▴ 대신 그린 꺾쇠
        self.assertFalse(room.notice_toggle.icon().isNull())
        import glob
        root = os.path.join(os.path.dirname(__file__), '..', 'client', 'kakao')
        glyphs = []
        for path in glob.glob(os.path.join(root, '*.py')):
            with open(path, encoding='utf-8') as fh:
                for i, line in enumerate(fh, 1):
                    if re.search(r"(setText|QLabel|QPushButton|_label)\(f?'[●■▴▾▲▼▶❚✕]", line):
                        glyphs.append(f'{os.path.basename(path)}:{i}')
        self.assertEqual(glyphs, [])                                      # 컨트롤 글자 기호 아이콘 없음
        win.close()
        room.close()

    def test_settings_toggles_have_names(self):
        from client.kakao.more_tab import MoreTab, ToggleSwitch
        tab = MoreTab(self.state, self.state.settings)
        toggles = tab.findChildren(ToggleSwitch)
        self.assertTrue(toggles)
        self.assertTrue(all(t.accessibleName() for t in toggles))

    def test_dark_tokens_meet_aa(self):
        from tests.test_kakao_layout import contrast
        from client.kakao import theme
        try:
            for dark in (False, True):
                theme.set_dark(dark)
                self.assertGreaterEqual(contrast(theme.c('text_hint'), theme.c('bubble_other')), 4.5, dark)
                self.assertGreaterEqual(contrast(theme.c('danger_text'), theme.c('bg')), 4.5, dark)
                self.assertGreaterEqual(contrast(theme.c('danger_text'), theme.c('chat_bg')), 4.5, dark)   # '전송 실패'
                self.assertGreaterEqual(contrast(theme.c('yellow'), theme.c('on_accent')), 4.5, dark)   # 주요 버튼 글자
        finally:
            theme.set_dark(False)


class UxCopyTest(unittest.TestCase):
    """design:ux-copy 반영"""

    def test_standard_dialog_buttons_in_korean(self):
        from PyQt5.QtWidgets import QMessageBox
        from client.kakao.app import install_korean_qt_translation
        a = app()
        self.assertTrue(install_korean_qt_translation(a))
        try:
            box = QMessageBox(QMessageBox.Question, '채팅방 나가기', '나갈까요?', QMessageBox.Yes | QMessageBox.No)
            texts = [b.text() for b in box.buttons()]
            self.assertTrue(any(t.startswith('예') for t in texts), texts)
            self.assertTrue(any(t.startswith('아니요') for t in texts), texts)
        finally:
            a.removeTranslator(a._kcs_translator)

    def test_errors_tell_how_to_fix(self):
        import re
        with open(os.path.join(os.path.dirname(__file__), '..', 'server', 'kakao', 'server.py'), encoding='utf-8') as fh:
            src = fh.read()
        for phrase in ('통화 카드에서 참여하기를 누르세요', '나에게서 삭제는 할 수 있습니다',
                       '프로필에 등록한 번호·이메일인지 확인하세요', '이미 달린 공감을 누르면'):
            self.assertIn(phrase, src)
        self.assertFalse(re.search(r"ProtocolError\('최대 10명까지 참여할 수 있습니다'\)", src))


class DesignSystemTest(unittest.TestCase):
    """design:design-system 감사 반영: 글자·모서리 토큰, 배율 무시하는 글자 크기 금지"""

    def test_type_scale_and_radii(self):
        from client.kakao import theme
        sizes = list(theme.TYPE_SCALE.values())
        self.assertEqual(sizes, sorted(sizes))
        try:
            theme.set_font_scale(1.25)
            self.assertEqual(theme.fs('meta'), 15)        # 배율 적용
        finally:
            theme.set_font_scale(1.0)
        self.assertEqual(theme.radius('card'), 12)

    def test_no_unscaled_font_sizes_outside_login(self):
        import glob
        import re
        root = os.path.join(os.path.dirname(__file__), '..', 'client', 'kakao')
        offenders = []
        for path in glob.glob(os.path.join(root, '*.py')):
            if os.path.basename(path) == 'login.py':   # 설정을 읽기 전 화면 (배율 1 고정)
                continue
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            offenders += [f'{os.path.basename(path)}: {m}' for m in re.findall(r'font-size: \d+px', text)]
        self.assertEqual(offenders, [])

    def test_button_variants_cover_all_states(self):
        """ui-styling(shadcn 원칙): 변형마다 hover·pressed·focus·disabled 규칙이 있다"""
        from client.kakao import theme
        for variant in theme.BUTTON_VARIANTS:
            qss = theme.button_qss('x', variant)
            for state in (':hover', ':pressed', '[focusVisible="true"]:focus', ':disabled'):
                self.assertIn(f'QPushButton#x{state}', qss, (variant, state))   # 포커스 테두리는 키보드로 옮겼을 때만
        with self.assertRaises(ValueError):
            theme.button_qss('x', 'fancy')

    def test_type_scale_steps_used_by_name(self):
        """impeccable typeset: 글자 단계에 있는 크기는 숫자 대신 이름(theme.fs)으로 쓴다"""
        import glob
        import re
        from client.kakao import theme
        root = os.path.join(os.path.dirname(__file__), '..', 'client', 'kakao')
        steps = set(theme.TYPE_SCALE.values())
        offenders = []
        for path in glob.glob(os.path.join(root, '*.py')):
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            for n in re.findall(r'font-size: \{(?:theme\.)?px\(([\d.]+)\)\}px', text):
                offenders.append(f'{os.path.basename(path)}: font-size px({n})')
            for n in re.findall(r'(?:theme\.)?\bfont\(([\d.]+)', text):
                offenders.append(f'{os.path.basename(path)}: font({n})')
            for n in re.findall(r"ElidedLabel\((?:[^()]|\([^()]*\))*?, ([\d.]+), '", text):
                offenders.append(f'{os.path.basename(path)}: ElidedLabel({n})')
            for n in re.findall(r'setPixelSize\((\d+)', text):
                if os.path.basename(path) != 'login.py':
                    offenders.append(f'{os.path.basename(path)}: setPixelSize({n})')
        # 예외: 이모티콘 그림 크기(46px)는 글자 단계가 아니다
        offenders = [o for o in offenders if 'px(46)' not in o]
        self.assertEqual(offenders, [])


class AdaptTest(unittest.TestCase):
    """impeccable adapt: 최소 창(320px) + 글자 150%에서 대화 영역이 넘치지 않는다"""

    def test_narrow_window_large_text(self):
        for scale in (1.5, 2.0):                     # 큰 글자 · 200% 확대 둘 다
            with self.subTest(scale=scale):
                self._narrow(scale)

    def _narrow(self, scale):
        import time
        from client.kakao import theme
        from client.kakao.chat_room import ChatRoomWindow, ContactCard
        from PyQt5.QtWidgets import QBoxLayout
        state, conn = make_demo_state()
        theme.set_font_scale(scale)
        try:
            push_msg(conn, msg_id=401, kind='contact', content='박민수')
            push_msg(conn, msg_id=402, sender='홍길동', kind='voice', content='9',
                     attachment={'attachment_id': 1, 'name': 'v', 'mime': 'audio/wav', 'size': 1})
            win = ChatRoomWindow(state, 'r1')
            win.resize(320, 560)
            win.show()
            for _ in range(4):
                time.sleep(0.05)
                pump()
            self.assertLessEqual(win.container.minimumSizeHint().width(), win.scroll.viewport().width())
            self.assertTrue(win.compact_header)
            conn.send = lambda *a, **k: None
            conn.is_connected = False
            state.send_text('r1', '좁은 창에서 보내지 못한 메시지도 잘리지 않아야 합니다')
            for _ in range(4):
                time.sleep(0.05)
                pump()
            self.assertLessEqual(win.container.minimumSizeHint().width(), win.scroll.viewport().width())
            self.assertTrue(win.drawer_btn.isHidden())
            self.assertIn('채팅방 서랍', [a.text() for a in win.build_room_menu().actions()])
            card = win.rows[401].card
            self.assertIsInstance(card, ContactCard)
            self.assertEqual(card.button_box.direction(), QBoxLayout.TopToBottom)
            win.resize(700, 700)
            for _ in range(4):
                time.sleep(0.05)
                pump()
            self.assertFalse(win.compact_header)
            win.close()
        finally:
            theme.set_font_scale(1.0)


class MotionTest(unittest.TestCase):
    """impeccable animate: 녹음 표시 · 요약 진행 막대, 동작 줄이기"""

    def tearDown(self):
        from client.kakao import theme
        theme.set_reduce_motion(False)

    def test_pulse_and_busy_bar_respect_reduce_motion(self):
        from client.kakao import theme
        from client.kakao.voice import PulseDot, BusyBar
        app()
        dot, bar = PulseDot(), BusyBar()
        dot.set_active(True)
        bar.start()
        self.assertTrue(dot._timer.isActive())
        self.assertTrue(bar.running())
        dot.phase = 0.5
        self.assertLess(dot.opacity(), 0.5)            # 흐려지는 구간
        theme.set_reduce_motion(True)
        dot.set_active(True)
        bar.stop()
        bar.start()
        self.assertFalse(dot._timer.isActive())
        self.assertFalse(bar.running())
        self.assertEqual(dot.opacity(), 1.0)           # 움직임 없이 켜진 상태로
        dot.set_active(False)
        self.assertEqual(dot.opacity(), 0.25)

    def test_summary_dialog_busy_until_result(self):
        from client.kakao.chat_room import ChatRoomWindow
        state, conn = make_demo_state()
        win = ChatRoomWindow(state, 'r1')
        self.addCleanup(win.close)
        dlg = win.show_summary()
        self.assertTrue(dlg.busy.running())
        conn.push('room_summary', room_id='r1', summary='• [결정] 2시', source='extractive', count=3)
        self.assertFalse(dlg.busy.running())
        self.assertTrue(dlg.busy.isHidden())
        dlg.close()

    def test_reduce_motion_setting_row(self):
        from client.kakao import theme
        from client.kakao.more_tab import MoreTab
        state, _ = make_demo_state()
        tab = MoreTab(state, state.settings)
        self.assertEqual(tab.motion_toggle.accessibleName(), '동작 줄이기')
        tab.motion_toggle.setChecked(True)
        self.assertTrue(state.settings.get('reduce_motion'))
        self.assertTrue(theme.reduce_motion())


class FailedMessageTest(unittest.TestCase):
    """shape 09 → craft: 연결이 끊긴 동안 보낸 메시지 = 빨간 ! + 재전송/삭제, 앱을 꺼도 보존"""

    def go_offline(self, conn):
        conn.is_connected = False
        conn._real_send = conn.send
        conn.send = lambda *a, **k: None

    def go_online(self, conn):
        conn.is_connected = True
        conn.send = conn._real_send

    def test_state_record_persist_resend_delete(self):
        import tempfile
        from client.kakao.state import FAILED_ID_BASE
        d = tempfile.mkdtemp()
        state, conn = make_demo_state(settings_dir=d)
        self.go_offline(conn)
        state.send_text('r1', '회의 자료 지금 올립니다')
        state.send_emoticon('r1', '👍')
        failed = state.failed_messages('r1')
        self.assertEqual([m['content'] for m in failed], ['회의 자료 지금 올립니다', '👍'])
        msgs = state.room_messages('r1')
        self.assertEqual([m['content'] for m in msgs[-2:]], ['회의 자료 지금 올립니다', '👍'])   # 맨 아래
        self.assertTrue(all(m['msg_id'] > FAILED_ID_BASE for m in failed))
        self.assertNotIn(failed[0]['msg_id'], state._messages.get('r1', {}))   # 서버 메시지 저장소와 분리
        self.assertEqual(state.message_unread('r1', failed[0]), 0)
        self.assertTrue(state.room_has_failed('r1'))
        self.assertIn('파일을 보내지 못했습니다', state.send_file('r1', __file__))
        state._flush_failed()                                       # (연속 저장은 0.5초 모아 쓰므로 즉시 기록)
        # 앱을 다시 켜도 남아 있다
        state2, conn2 = make_demo_state(settings_dir=d)
        self.assertEqual([m['content'] for m in state2.failed_messages('r1')], ['회의 자료 지금 올립니다', '👍'])
        self.assertIn('회의 자료 지금 올립니다', [m['content'] for m in state2.room_messages('r1')])
        # 아직 오프라인이면 재전송 거절
        self.assertIn('아직 서버에 연결되지 않았습니다', state.resend_failed('r1', failed[0]['msg_id']))
        self.go_online(conn)
        conn.sent.clear()
        self.assertIsNone(state.resend_failed('r1', failed[0]['msg_id']))
        self.assertEqual(conn.last('send')['content'], '회의 자료 지금 올립니다')
        state.delete_failed('r1', failed[1]['msg_id'])
        self.assertEqual(state.failed_messages('r1'), [])
        self.assertFalse(state.room_has_failed('r1'))
        state.mark_read('r1')                                       # 로컬 id 로 읽음 처리하지 않음
        last = conn.last('read')
        self.assertTrue(last is None or last['last_msg_id'] < FAILED_ID_BASE)

    def test_failed_bubble_menu_and_list(self):
        from client.kakao.chat_room import ChatRoomWindow, FailButton
        from client.kakao.chats_tab import ChatsTab, RoomRow
        state, conn = make_demo_state()
        win = ChatRoomWindow(state, 'r1')
        win.resize(420, 700)
        win.show()
        pump()
        self.addCleanup(win.close)
        self.go_offline(conn)
        state.send_text('r1', '연결 끊김 테스트')
        pump()
        m = state.failed_messages('r1')[0]
        row = win.rows[m['msg_id']]
        self.assertIsInstance(row.fail_button, FailButton)
        self.assertEqual(row.time_label.text(), '전송 실패')
        state.send_text('r1', '연속 두 번째')                          # 연속 메시지여도 첫 줄의 '전송 실패'는 보인다
        pump()
        row = win.rows[m['msg_id']]
        self.assertTrue(row.time_label.isVisibleTo(win))
        import time as _t
        for _ in range(3):
            _t.sleep(0.05)
            pump()
        self.assertIsNotNone(row.fail_button._anim)                 # 막 실패 → 알림 고리 1회
        self.assertNotIn('fresh', state.failed_messages('r1')[0])   # 저장본에는 표시 플래그 없음
        from client.kakao import theme
        theme.set_reduce_motion(True)
        try:
            self.assertFalse(row.fail_button.announce())
        finally:
            theme.set_reduce_motion(False)
        self.assertEqual(row.unread_label.isVisible(), False)
        self.assertIn('보내지 못한 메시지', row.accessibleName())
        self.assertEqual([a.text() for a in win.failed_menu(m).actions()], ['재전송', '복사', '삭제'])
        self.assertEqual([a.text() for a in win.build_context_menu(m).actions()], ['재전송', '복사', '삭제'])
        self.assertIn('아직 서버에 연결되지 않았습니다', win.resend_failed(m))
        tab = ChatsTab(state, state.settings)
        rows = {tab.list.itemWidget(tab.list.item(i)).room_id: tab.list.itemWidget(tab.list.item(i))
                for i in range(tab.list.count()) if isinstance(tab.list.itemWidget(tab.list.item(i)), RoomRow)}
        self.assertEqual(rows['r1'].preview.text(), '보내지 못한 메시지가 있습니다')
        # 새 서버 메시지는 실패 말풍선 위에 한 행만 끼워 넣는다 (방 전체 재구성 없음)
        rebuilt = []
        orig = win._rebuild
        win._rebuild = lambda: (rebuilt.append(1), orig())
        push_msg(conn, msg_id=500, kind='text', content='새 메시지')
        pump()
        self.assertEqual(rebuilt, [])
        ids = [o['msg_id'] for o in win._order]
        self.assertLess(ids.index(500), ids.index(m['msg_id']))
        win._rebuild = orig
        self.go_online(conn)
        self.assertIsNone(win.resend_failed(m))
        pump()
        self.assertNotIn(m['msg_id'], win.rows)


class OptimizeTest(unittest.TestCase):
    """impeccable optimize: 설치되지 않은 글꼴을 체인에서 빼서 첫 그리기 지연(별칭 표 생성 ~130ms) 제거"""

    def test_font_chain_only_installed(self):
        from PyQt5.QtGui import QFontDatabase
        from client.kakao import theme
        app()
        available = set(QFontDatabase().families())
        fams = theme.font_families()
        self.assertTrue(fams)
        self.assertTrue(all(f in available for f in fams) or fams == theme.FONT_FAMILIES)
        self.assertEqual([f for f in theme.FONT_FAMILIES if f in fams], fams)     # 순서 유지


class SummaryStyleTest(unittest.TestCase):
    """impeccable bolder · colorize · layout: 요약 결과 위계와 유형 색"""

    def test_summary_html_hierarchy_and_tags(self):
        from client.kakao.chat_room import summary_html, SUMMARY_TAGS
        from client.kakao import theme
        app()
        text = ('메시지 11개 · 참여 3명 — 김철수(5)\n주요 키워드: 회의, 자료\n'
                '• [결정] 홍길동: 2시로 확정\n• [질문] 김철수: 회의실은?\n공유: 파일 1')
        h = summary_html(text)
        self.assertIn('font-weight:700', h.split('</p>')[0])                 # 첫 줄 = 제목
        self.assertIn(SUMMARY_TAGS['light']['결정'][1], h)                   # 결정 태그 배경
        self.assertIn(SUMMARY_TAGS['light']['질문'][0], h)
        self.assertIn('회의', h)
        self.assertIn('&lt;', summary_html('메시지\n<script>'))              # 이스케이프
        theme.set_dark(True)
        try:
            self.assertIn(SUMMARY_TAGS['dark']['결정'][1], summary_html(text))
        finally:
            theme.set_dark(False)

    def test_keyword_chip_text_contrast(self):
        from client.kakao.chat_room import keyword_text_color
        from client.kakao import theme
        from tests.test_kakao_layout import contrast
        app()
        try:
            for dark in (False, True):
                theme.set_dark(dark)
                self.assertGreaterEqual(contrast(keyword_text_color(), theme.c('bg')), 4.5, dark)
        finally:
            theme.set_dark(False)

    def test_tag_colors_meet_aa(self):
        from client.kakao.chat_room import SUMMARY_TAGS
        from tests.test_kakao_layout import contrast
        for mode in SUMMARY_TAGS.values():
            for fg, bg in mode.values():
                self.assertGreaterEqual(contrast(fg, bg), 4.5)


class UiStylingPatternsTest(unittest.TestCase):
    """ui-styling(shadcn/Radix) 접근성 체크리스트를 PyQt 로 옮긴 것"""

    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_dialog_focus_first_input_and_return_to_opener(self):
        from client.kakao.chat_room import ChatRoomWindow, ContactPickDialog
        win = ChatRoomWindow(self.state, 'r1')
        win.show()
        pump()
        self.addCleanup(win.close)
        win.activateWindow()
        win.edit.setFocus()
        pump()
        dlg = ContactPickDialog(self.state, win)
        self.assertIs(dlg._opener, QApplication.focusWidget())
        dlg.show()
        pump()
        self.assertIs(QApplication.focusWidget(), dlg.search)          # 첫 입력칸
        dlg.reject()
        pump()
        if dlg._opener is not None:                                      # 오프스크린에서 포커스가 있을 때만
            self.assertIs(QApplication.focusWidget(), dlg._opener)       # 연 곳으로 돌아감

    def test_toast_live_region_and_f6_skip(self):
        from client.kakao.chat_room import ChatRoomWindow
        win = ChatRoomWindow(self.state, 'r1')
        win.show()
        pump()
        self.addCleanup(win.close)
        win.toast('전달했습니다.')
        self.assertEqual(win.toast_label.accessibleName(), '전달했습니다.')
        self.assertIn('F6', {sc.key().toString() for sc in win.kb_shortcuts})
        win.kb_select(-1)
        win.focus_compose()
        self.assertIsNone(win._kb_msg_id)

    def test_form_error_linked_to_field(self):
        from client.kakao.friends_tab import ProfileEditDialog, AddFriendDialog
        dlg = ProfileEditDialog(self.state)
        dlg.email_edit.setText('not-email')
        dlg.accept()
        self.assertIn('이메일', dlg.email_edit.accessibleDescription())
        self.assertEqual(dlg.mobile_edit.accessibleDescription(), '')
        add = AddFriendDialog(self.state)
        self.conn.push('error', req_type='friend_add', message='해당 전화번호·이메일로 가입한 사용자가 없습니다.')
        add._show_message('해당 전화번호·이메일로 가입한 사용자가 없습니다.', error=True)
        self.assertIn('가입한 사용자가 없습니다', add.input.accessibleDescription())

    def test_text_zoom_up_to_200(self):
        from client.kakao import theme
        from client.kakao.more_tab import FONT_SCALES
        self.assertIn(2.0, [v for _l, v in FONT_SCALES])
        try:
            theme.set_font_scale(2.0)
            self.assertEqual(theme.fs('body'), 26)
        finally:
            theme.set_font_scale(1.0)


class DesignTokenExportTest(unittest.TestCase):
    """ui-styling 테마 계층: theme.py → shadcn 형식 CSS 변수 / JSON (scripts/make_design_board.py)"""

    def test_exported_tokens_match_theme(self):
        import importlib.util
        import json
        from client.kakao import theme
        app()
        root = os.path.join(os.path.dirname(__file__), '..')
        spec = importlib.util.spec_from_file_location('board', os.path.join(root, 'scripts', 'make_design_board.py'))
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        data = board.export_tokens(theme)
        theme.set_dark(False)
        self.assertEqual(data['shadcn']['light']['primary'], theme.c('yellow').lower())
        self.assertEqual(data['type_scale']['meta'], 12)
        with open(os.path.join(root, 'assets', 'design-tokens.css'), encoding='utf-8') as fh:
            css = fh.read()
        for var in ('--background', '--primary', '--primary-foreground', '--destructive', '--ring', '--radius'):
            self.assertIn(var, css)
        self.assertIn('.dark {', css)
        with open(os.path.join(root, 'assets', 'design-tokens.json'), encoding='utf-8') as fh:
            self.assertIn('dark', json.load(fh))


class EfficiencyFollowupTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_export_parts_collected_once(self):
        got = []
        self.state.room_export_ready.connect(lambda rid, msgs, tr: got.append((rid, len(msgs))))
        m = {'msg_id': 1, 'room_id': 'r1', 'sender': '김철수', 'kind': 'text', 'content': 'a', 'timestamp': '2026-10-09T09:00:00'}
        self.conn.push('room_export', room_id='r1', messages=[m], part=0, done=False, truncated=False)
        self.assertEqual(got, [])
        self.conn.push('room_export', room_id='r1', messages=[dict(m, msg_id=2)], part=1, done=True, truncated=False)
        self.assertEqual(got, [('r1', 2)])

    def test_room_changed_updates_one_row(self):
        from client.kakao.chats_tab import ChatsTab, RoomRow
        tab = ChatsTab(self.state, self.state.settings)
        before = {tab.list.item(i).data(0x0100): tab.list.itemWidget(tab.list.item(i)) for i in range(tab.list.count())}
        self.state.set_room_draft('r2', '쓰다 만 글')     # r2 의 미리보기가 바뀐다
        self.state.room_changed.emit('r2')
        after = {tab.list.item(i).data(0x0100): tab.list.itemWidget(tab.list.item(i)) for i in range(tab.list.count())}
        self.assertIsNot(after['r2'], before['r2'])      # 그 행만 새로
        self.assertIs(after['r1'], before['r1'])
        self.assertIsInstance(after['r2'], RoomRow)
        self.assertIn('임시저장', after['r2'].preview.text())
        self.state.room_changed.emit('r1')               # 보이는 내용이 그대로면 아무 행도 다시 만들지 않는다
        again = {tab.list.item(i).data(0x0100): tab.list.itemWidget(tab.list.item(i)) for i in range(tab.list.count())}
        self.assertEqual(again, after)


class AutoReconnectTest(unittest.TestCase):
    """연결이 예기치 않게 끊기면 1·2·4·8·10초 간격으로 자동 재연결, 직접 닫은 연결은 그대로"""

    def test_backoff_and_reset(self):
        from client.kakao.app import Session
        app()
        s = Session('127.0.0.1', 1, '재연결시험')
        calls = []
        s.reconnect = lambda: (calls.append(1), (len(calls) >= 3, ''))[1]
        delays = []
        s._retry_timer.start = lambda ms: (delays.append(ms // 1000), None)[1]
        s._retry_timer.isActive = lambda: False
        s._schedule_reconnect('끊김')
        for _ in range(3):
            s._auto_reconnect()
        self.assertEqual(delays, [1, 2, 4])            # 실패할수록 간격이 늘어남
        self.assertEqual(s._retry, 3)                   # 연결만 된 것으로는 줄지 않는다 (앞단만 살아 있을 수 있다)
        s.state.welcomed.emit()
        self.assertEqual(s._retry, 0)                   # 서버가 welcome 으로 답하면 처음부터
        s.close()
        delays.clear()
        s._schedule_reconnect('끊김')
        self.assertEqual(delays, [])                    # 직접 닫으면 다시 붙지 않음
        self.assertEqual(Session.RETRY_DELAYS[-1], 10)


if __name__ == '__main__':
    unittest.main()
