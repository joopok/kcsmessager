# -*- coding: utf-8 -*-
"""ChatRoomWindow 테스트 (서버 없이 FakeConnection으로 구동)

    QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_kakao_chat_room -v
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta

from tests.kakao_fakes import make_demo_state, app

from PyQt5.QtCore import Qt, QMimeData, QUrl
from PyQt5.QtGui import QImage, QColor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from client.kakao import theme
from client.kakao import chat_room
from client.kakao.chat_room import ChatRoomWindow, MessageRow, ChipRow, InviteDialog


def iso(dt):
    return dt.isoformat(timespec='seconds')


def new_message(msg_id, sender, content, room_id='r1', minutes_ago=0, **extra):
    m = dict(msg_id=msg_id, room_id=room_id, sender=sender, kind='text', content=content,
             timestamp=iso(datetime.now() - timedelta(minutes=minutes_ago)), reply_to=None, mentions=[],
             reactions={}, deleted=False, attachment=None, event_id=None, poll=None)
    m.update(extra)
    return m


def flush():
    for _ in range(3):
        QApplication.processEvents()


class ChatRoomTestBase(unittest.TestCase):
    room = 'r1'

    def setUp(self):
        app()
        theme.set_dark(False)
        self.state, self.conn = make_demo_state()
        self.win = ChatRoomWindow(self.state, self.room)
        self.win.confirm = lambda *a: True       # 확인창 자동 승인
        self.win.resize(420, 720)
        self.win.show()
        self.win.activateWindow()
        flush()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        flush()

    def sent(self, msg_type):
        return [p for t, p in self.conn.sent if t == msg_type]


class BuildTests(ChatRoomTestBase):
    def test_builds_r1_with_all_kinds(self):
        rows = self.win.rows
        self.assertEqual(sorted(rows), list(range(1, 12)))
        self.assertIsInstance(rows[1], ChipRow)          # 시스템 메시지
        self.assertIsNotNone(rows[7].card)               # 투표 카드
        self.assertIsNotNone(rows[8].card)               # 일정 카드
        self.assertTrue(rows[10].deleted)
        self.assertIsNotNone(rows[5].quote)              # 답장 인용
        self.assertIn('👍', rows[5].reaction_buttons)
        self.assertIn('href="https://example.com/docs"', rows[4].text_label.text())
        self.assertIn('@홍길동', rows[5].text_label.text())
        self.assertTrue(self.win.notice_wrap.isVisible())
        self.assertEqual(self.win.count_label.text(), '대화상대 3명')

    def test_window_title_brand(self):
        self.assertTrue(self.win.windowTitle().endswith('KCS Talk'))
        self.assertTrue(self.win.windowTitle().startswith('프로젝트 팀'))

    def test_builds_r2_and_r3(self):
        for rid in ('r2', 'r3'):
            w = ChatRoomWindow(self.state, rid)
            w.show()
            flush()
            self.assertTrue(w.windowTitle().endswith('KCS Talk'))
            w.close()
        self.assertEqual(self.sent('history')[-1]['room_id'], 'r3')

    def test_history_and_mark_read(self):
        self.assertIn('r1', self.state.active_rooms)
        reads = self.sent('read')
        self.assertTrue(reads)
        self.assertEqual(reads[-1], {'room_id': 'r1', 'last_msg_id': 11})
        self.win.hide()
        self.assertNotIn('r1', self.state.active_rooms)

    def test_run_rules(self):
        # 2·3은 서로 다른 사람 → 둘 다 첫 메시지
        self.assertTrue(self.win.rows[2].first)
        # 같은 사람·같은 분의 연속 메시지
        now = datetime.now().replace(second=5)
        a = new_message(20, '김철수', '첫째', timestamp=iso(now))
        b = new_message(21, '김철수', '둘째', timestamp=iso(now.replace(second=30)))
        self.conn.push('msg_new', message=a)
        self.conn.push('msg_new', message=b)
        ra, rb = self.win.rows[20], self.win.rows[21]
        self.assertTrue(ra.first)
        self.assertFalse(rb.first)
        self.assertTrue(ra.time_label.isHidden())       # 시간은 마지막에만
        self.assertFalse(rb.time_label.isHidden())
        self.assertTrue(rb.name_label.isHidden())

    def test_bubble_width_limit_and_no_clipping(self):
        row = self.win.rows[11]
        maxw = self.win.max_bubble_width()
        self.assertLessEqual(row.bubble.width(), maxw + 1)
        lab = row.text_label
        self.assertGreaterEqual(lab.height(), lab.heightForWidth(lab.width()))
        self.assertGreater(lab.height(), 30)             # 여러 줄로 줄바꿈됨

    def test_room_removed_closes(self):
        self.conn.push('room_removed', room_id='r1')
        flush()
        self.assertFalse(self.win.isVisible())

    def test_performance_300_messages(self):
        state, conn = make_demo_state()
        now = datetime.now()
        msgs = [new_message(1000 + i, ['김철수', '이영희', '홍길동'][i % 3],
                            f'성능 테스트 메시지 {i} ' + '가나다 abc ' * (i % 7),
                            timestamp=iso(now - timedelta(minutes=600 - i * 2)))
                for i in range(300)]
        conn.push('history', room_id='r1', messages=msgs, has_more=False)
        t0 = time.perf_counter()
        w = ChatRoomWindow(state, 'r1')
        w.resize(420, 720)
        w.show()
        flush()
        elapsed = time.perf_counter() - t0
        from client.kakao.chat_room import RENDER_PAGE
        # 처음에는 최근 RENDER_PAGE 개만 만든다 (받아 둔 300개 + 데모 메시지)
        self.assertLessEqual(len([m for m in w.rows if m >= 1000]), RENDER_PAGE)
        self.assertLess(elapsed, 1.5, f'300 messages took {elapsed:.2f}s')
        while w._load_older():                    # 위로 올리면 받아 둔 메시지를 150개씩 더
            flush()
        self.assertEqual(len([m for m in w.rows if m >= 1000]), 300)
        self.assertTrue(w.focus_message(1000))    # 가장 오래된 메시지로 이동
        w.close()


class SendTests(ChatRoomTestBase):
    def test_enter_sends_text_with_mentions(self):
        self.win.edit.setPlainText('@김철수 안녕하세요')
        self.assertTrue(self.win.send_btn.isEnabled())
        QTest.keyClick(self.win.edit, Qt.Key_Return)
        p = self.conn.last('send')
        self.assertEqual(p['kind'], 'text')
        self.assertEqual(p['content'], '@김철수 안녕하세요')
        self.assertEqual(p['mentions'], ['김철수'])
        self.assertEqual(self.win.edit.toPlainText(), '')
        self.assertFalse(self.win.send_btn.isEnabled())

    def test_shift_enter_newline(self):
        self.win.edit.setPlainText('첫 줄')
        self.win.edit.moveCursor(self.win.edit.textCursor().End)
        QTest.keyClick(self.win.edit, Qt.Key_Return, Qt.ShiftModifier)
        self.assertIsNone(self.conn.last('send'))
        self.assertIn('\n', self.win.edit.toPlainText())

    def test_mention_popup_inserts_name(self):
        self.win.edit.insertPlainText('@김')            # 비ASCII는 QTest.keyClicks 불가
        flush()
        self.assertTrue(self.win.mention_popup.isVisible())
        QTest.keyClick(self.win.edit, Qt.Key_Return)    # 팝업에서 선택
        self.assertEqual(self.win.edit.toPlainText(), '@김철수 ')
        self.assertIsNone(self.conn.last('send'))

    def test_reply_flow(self):
        self.win.start_reply(self.win.rows[5].msg)
        self.assertFalse(self.win.reply_bar.isHidden())
        self.win.edit.setPlainText('답장입니다')
        self.win.send_current()
        p = self.conn.last('send')
        self.assertEqual(p['reply_to_id'], 5)
        self.assertTrue(self.win.reply_bar.isHidden())

    def test_react_handler_and_pill(self):
        self.win.react(5, '😆')
        self.assertEqual(self.conn.last('react'), {'msg_id': 5, 'emoji': '😆'})
        self.win.rows[5].reaction_buttons['👍'].click()
        self.assertEqual(self.conn.last('react'), {'msg_id': 5, 'emoji': '👍'})

    def test_emoticon_popup_sends(self):
        popup = self.win.open_emoticons()
        self.assertGreaterEqual(len(popup.buttons), 40)
        popup.buttons[0].click()
        p = self.conn.last('send')
        self.assertEqual((p['kind'], p['content']), ('emoticon', chat_room.EMOTICONS[0]))

    def test_send_files_multi_and_error(self):
        d = tempfile.mkdtemp()
        a, b = os.path.join(d, 'a.txt'), os.path.join(d, 'b.txt')
        for path in (a, b):
            with open(path, 'w') as f:
                f.write('hello')
        errors = self.win.send_files([a, b, os.path.join(d, 'missing.bin')])
        sends = self.sent('send')
        self.assertEqual([s['attachment']['name'] for s in sends], ['a.txt', 'b.txt'])
        self.assertEqual(len(errors), 1)

    def test_drop_files_sends(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, '보고서.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('내용')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        self.assertTrue(self.win.handle_mime(mime))
        p = self.conn.last('send')
        self.assertEqual((p['kind'], p['attachment']['name']), ('file', '보고서.txt'))

    def test_paste_image_sends_png(self):
        img = QImage(20, 10, QImage.Format_RGB32)
        img.fill(QColor('#3366FF'))
        mime = QMimeData()
        mime.setImageData(img)
        self.assertTrue(self.win.edit.canInsertFromMimeData(mime))
        self.win.edit.insertFromMimeData(mime)          # Ctrl/Cmd+V 경로
        p = self.conn.last('send')
        self.assertEqual(p['kind'], 'image')
        self.assertEqual(p['attachment']['mime'], 'image/png')
        self.assertEqual(self.win.edit.toPlainText(), '')

    def test_paste_text_still_inserts(self):
        mime = QMimeData()
        mime.setText('그냥 글자')
        self.win.edit.insertFromMimeData(mime)
        self.assertEqual(self.win.edit.toPlainText(), '그냥 글자')
        self.assertIsNone(self.conn.last('send'))

    def test_scheduled_toast(self):
        self.conn.push('scheduled_ok', scheduled_id=1, room_id='r1', send_at=iso(datetime.now()), content='x')
        self.assertTrue(self.win.toast_label.isVisible())
        self.assertEqual(self.win.toast_label.text(), '예약되었습니다')


class LiveUpdateTests(ChatRoomTestBase):
    def test_msg_new_updated_read_update(self):
        m = new_message(30, '이영희', '새 메시지')
        self.conn.push('msg_new', message=m)
        self.assertIn(30, self.win.rows)
        old_row = self.win.rows[30]
        upd = dict(m, reactions={'❤️': ['홍길동']})
        self.conn.push('msg_updated', message=upd)
        self.assertIsNot(self.win.rows[30], old_row)
        self.assertEqual(self.win.rows[30].reaction_buttons['❤️'].objectName(), 'reactionMine')
        # 안 읽은 수: msg 11 → 홍길동(8), 이영희(9)가 안 읽음 = 2
        row11 = self.win.rows[11]
        self.assertEqual(row11.unread_label.text(), '2')
        self.conn.push('read_update', room_id='r1', username='홍길동', last_read_msg_id=11)
        self.assertEqual(row11.unread_label.text(), '1')
        self.conn.push('read_update', room_id='r1', username='이영희', last_read_msg_id=30)
        self.win.flush_read_refresh()          # 몰려오는 읽음은 50ms 안에 묶어 한 번에 그린다
        self.assertTrue(row11.unread_label.isHidden())

    def test_new_message_marks_read_when_active(self):
        self.conn.sent.clear()
        self.conn.push('msg_new', message=new_message(31, '김철수', '읽어주세요'))
        if self.win.isActiveWindow():
            self.assertEqual(self.conn.last('read')['last_msg_id'], 31)

    def test_delete_updates_row(self):
        mine = new_message(32, '홍길동', '지울 메시지')
        self.conn.push('msg_new', message=mine)
        self.conn.push('msg_updated', message=dict(mine, deleted=True, content='메시지가 삭제되었습니다'))
        self.assertTrue(self.win.rows[32].deleted)
        lab = self.win.rows[32]._fit[0][0]
        self.assertEqual(lab.text(), '메시지가 삭제되었습니다')

    def test_hide_for_me_removes_row(self):
        self.state.hide_for_me('r1', 9)
        flush()
        self.assertNotIn(9, self.win.rows)

    def test_room_changed_notice_cleared(self):
        room = dict(self.state.room('r1'), notice=None)
        self.conn.push('room_updated', room=room)
        self.assertTrue(self.win.notice_wrap.isHidden())

    def test_events_changed_rerenders_event(self):
        ev = dict(self.state.events[1], attendees={'홍길동': 'yes', '김철수': 'no', '이영희': 'maybe'})
        self.conn.push('event_updated', event=ev)
        card = self.win.rows[8].card
        self.assertIn('불참 1', ' '.join(l.text() for l in card._wraps))

    def test_avatar_changed_refreshes(self):
        self.state.avatar_changed.emit('김철수')     # 크래시 없이 다시 그림
        self.assertTrue(self.win.rows[2].first)

    def test_company_tag_and_bot_prefix(self):
        self.state.users['홍길동']['company'] = 'KCS 홀딩스'
        self.state.users['이영희']['company'] = 'KCS 물류'
        self.state.users['AI 비서'] = {'username': 'AI 비서', 'display_name': 'AI 비서', 'online': True,
                                      'is_bot': True, 'company': 'KCS AI'}
        self.conn.push('msg_new', message=new_message(40, '이영희', '외부 회사'))
        self.conn.push('msg_new', message=new_message(41, 'AI 비서', '봇 답변'))
        self.assertEqual(self.win.rows[40].company_label.text(), 'KCS 물류')
        from PyQt5.QtWidgets import QLabel
        tags = [l.text() for l in self.win.rows[41].name_label.findChildren(QLabel) if l.objectName() == 'aiTag']
        self.assertEqual(tags, ['AI'])                                   # 이모지 대신 'AI' 태그


class CardTests(ChatRoomTestBase):
    def test_poll_vote_and_close(self):
        card = self.win.rows[7].card
        card.option_buttons[0].setChecked(True)
        card.vote_button.click()
        self.assertEqual(self.conn.last('poll_vote'), {'poll_id': 1, 'option_indexes': [0]})
        self.assertIsNotNone(card.close_button)          # 내가 만든 투표
        card.close_button.click()
        self.assertEqual(self.conn.last('poll_close'), {'poll_id': 1})

    def test_poll_closed_state(self):
        m = dict(self.state.find_message('r1', 7))
        m['poll'] = dict(m['poll'], closed=True)
        self.conn.push('msg_updated', message=m)
        card = self.win.rows[7].card
        self.assertIsNone(card.vote_button)
        self.assertFalse(card.option_buttons[0].isEnabled())

    def test_rsvp(self):
        card = self.win.rows[8].card
        self.assertEqual(card.rsvp_buttons['yes'].objectName(), 'cardBtnOn')
        card.rsvp_buttons['no'].click()
        self.assertEqual(self.conn.last('event_rsvp'), {'event_id': 1, 'response': 'no'})

    def test_file_card_save_open_request(self):
        row = self.win.rows[9]
        row.save_button.click()
        self.assertEqual(self.conn.last('attachment_get'), {'attachment_id': 1})
        self.assertIn(1, self.win._pending_save)
        self.win._pending_save.clear()
        row.open_button.click()
        self.assertIn(1, self.win._pending_open)
        self.win._pending_open.clear()


class MenuSearchTests(ChatRoomTestBase):
    def menu_texts(self, menu):
        out = []
        for act in menu.actions():
            out.append(act.text())
            if act.menu():
                out += [f'{act.text()}/{a.text()}' for a in act.menu().actions()]
        return out

    def test_context_menu_others_message(self):
        texts = self.menu_texts(self.win.build_context_menu(self.win.rows[2].msg))
        for t in ('답장', '공감', '복사', '전달', '나에게 보내기', '공지 등록', '삭제', '삭제/나에게서 삭제'):
            self.assertIn(t, texts)
        self.assertNotIn('삭제/모든 대화 상대에게서 삭제', texts)
        self.assertIn('공감/❤️', texts)

    def test_delete_for_all_available_for_my_recent(self):
        mine = new_message(50, '홍길동', '내 메시지')
        self.conn.push('msg_new', message=mine)
        menu = self.win.build_context_menu(mine)
        texts = self.menu_texts(menu)
        self.assertIn('삭제/모든 대화 상대에게서 삭제', texts)
        act = [a for a in menu.actions() if a.text() == '삭제'][0].menu().actions()[0]
        act.trigger()
        self.assertEqual(self.conn.last('delete'), {'msg_id': 50})
        old = new_message(51, '홍길동', '오래된', minutes_ago=60 * 30)
        self.assertNotIn('삭제/모든 대화 상대에게서 삭제', self.menu_texts(self.win.build_context_menu(old)))

    def test_context_actions(self):
        menu = self.win.build_context_menu(self.win.rows[2].msg)
        acts = {a.text(): a for a in menu.actions()}
        acts['답장'].trigger()
        self.assertEqual(self.win._reply_to['msg_id'], 2)
        acts['공지 등록'].trigger()
        self.assertEqual(self.conn.last('notice_set'), {'room_id': 'r1', 'msg_id': 2})
        acts['복사'].trigger()
        self.assertEqual(QApplication.clipboard().text(), '다들 내일 회의 시간 괜찮아요?')
        acts['나에게 보내기'].trigger()                  # self 방 r3 존재 → 바로 전달
        p = self.conn.last('send')
        self.assertEqual((p['room_id'], p['content']), ('r3', '다들 내일 회의 시간 괜찮아요?'))
        self.assertEqual(self.win.build_context_menu({'msg_id': 1, 'kind': 'system'}).actions(), [])

    def test_room_menu(self):
        texts = [a.text() for a in self.win.build_room_menu().actions()]
        for t in ('대화상대 초대', '채팅방 이름 변경', '알림 설정', '게시판', '채팅방 상단 고정', '배경화면 설정',
                  '채팅방 나가기'):
            self.assertIn(t, texts)
        self.win.toggle_mute()
        self.assertTrue(self.state.is_muted('r1'))
        self.assertFalse(self.win.mute_icon.isHidden())
        self.win.leave_room()
        self.assertEqual(self.conn.last('room_leave'), {'room_id': 'r1'})

    def test_search_navigation(self):
        focused = []
        self.win.focus_message = lambda mid: focused.append(mid)
        self.win.open_search()
        self.win.search_edit.setText('회의')
        self.win._do_search()
        self.assertEqual(self.conn.last('search'), {'query': '회의', 'room_id': 'r1'})
        msgs = [self.state.find_message('r1', 2), self.state.find_message('r1', 11)]
        self.conn.push('search_result', query='회의', room_id='r1', messages=msgs)
        self.assertEqual(focused, [11])                   # 최신 결과부터
        self.assertEqual(self.win.search_count.text(), '1/2')
        self.win.search_prev()
        self.assertEqual(focused[-1], 2)
        self.assertEqual(self.win.search_count.text(), '2/2')
        self.win.search_next()
        self.assertEqual(focused[-1], 11)
        QTest.keyClick(self.win.search_edit, Qt.Key_Escape)
        self.assertTrue(self.win.search_bar.isHidden())

    def test_focus_message_and_missing(self):
        self.assertTrue(self.win.focus_message(4))
        self.assertTrue(self.win.rows[4]._flash)
        self.assertFalse(self.win.focus_message(999))     # has_more False → 토스트
        self.assertTrue(self.win.toast_label.isVisible())

    def test_focus_loads_older_when_has_more(self):
        self.state.has_more['r1'] = True
        self.conn.sent.clear()
        self.assertFalse(self.win.focus_message(0))       # 아직 로드 안 된 이전 메시지
        self.assertTrue(self.sent('history'))
        self.assertEqual(self.sent('history')[-1]['before_msg_id'], 1)
        older = new_message(0, '김철수', '아주 예전 메시지', minutes_ago=60 * 48)
        self.conn.push('history', room_id='r1', messages=[older], has_more=False)
        self.assertIn(0, self.win.rows)
        self.assertTrue(self.win.rows[0]._flash)

    def test_drawer_builds(self):
        from client.kakao.chat_room import DrawerDialog
        dlg = DrawerDialog(self.win, self.win)
        self.assertEqual(dlg.tabs.count(), 4)
        self.assertEqual(dlg.links.count(), 1)
        self.assertEqual(dlg.files.count(), 1)
        dlg.close()


class InviteTests(ChatRoomTestBase):
    def test_group_invite_sends_room_invite(self):
        dlg = self.win.make_invite_dialog()
        self.assertIsInstance(dlg, InviteDialog)
        self.assertIsNone(dlg.hint)
        self.assertNotIn('김철수', dlg.rows)              # 이미 멤버
        self.assertFalse(dlg.ok.isEnabled())
        dlg.select(['박민수', '최지우'])
        self.assertEqual(dlg.ok.text(), '초대 (2)')
        self.assertEqual(set(dlg.chips), {'박민수', '최지우'})
        dlg.chips['최지우'].click()                       # 칩 클릭 → 선택 해제
        self.assertEqual(dlg.selected(), ['박민수'])
        dlg.accept()
        created = self.win.invite(dlg.selected())
        self.assertFalse(created)
        self.assertEqual(self.conn.last('room_invite'), {'room_id': 'r1', 'usernames': ['박민수']})

    def test_direct_invite_creates_group(self):
        w = ChatRoomWindow(self.state, 'r2')
        dlg = w.make_invite_dialog()
        self.assertIsNotNone(dlg.hint)
        dlg.select(['김철수'])
        self.assertTrue(w.invite(dlg.selected()))
        p = self.conn.last('room_create')
        self.assertEqual(sorted(p['members']), ['김철수', '박민수'])
        w.close()

    def test_search_filter(self):
        self.state.users['박민수'].update(company='KCS 물류', department='운영팀', title='사원')
        dlg = self.win.make_invite_dialog()
        self.assertEqual(dlg.set_filter('물류'), 1)
        self.assertEqual(dlg.visible_usernames(), ['박민수'])
        self.assertEqual(dlg.set_filter('최지'), 1)
        self.assertEqual(dlg.set_filter('없는사람'), 0)
        self.assertFalse(dlg.empty.isHidden())
        dlg.set_filter('')
        self.assertEqual(len(dlg.visible_usernames()), len(dlg.rows))

    def test_members_popup(self):
        popup = self.win.build_members_popup()
        self.assertEqual(popup.member_rows[0], '홍길동')  # 나 먼저
        self.assertEqual(len(popup.member_rows), 3)
        self.assertIsNotNone(popup.invite_button)


class MembersStripTests(ChatRoomTestBase):
    """대화방이 만들어지면 참여자 목록(헤더 아래 참여자 줄)이 바로 보인다"""

    def _push_new_group(self, rid, members, owner='홍길동'):
        self.conn.push('room_updated', room={
            'room_id': rid, 'type': 'group', 'name': '', 'members': members, 'owner': owner,
            'created_at': iso(datetime.now()), 'notice': None, 'last_message': None,
            'read_pointers': {}, 'unread_count': 0})

    def test_members_strip_shown_when_i_create_group(self):
        opened = []
        self.state.room_opened.connect(opened.append)
        self.state.create_group(['김철수', '박민수', '최지우'], '프로젝트방')
        self._push_new_group('r77', ['홍길동', '김철수', '박민수', '최지우'])
        self.assertEqual(opened, ['r77'])
        self.assertIn('r77', self.state.created_rooms)
        w = ChatRoomWindow(self.state, 'r77')
        try:
            self.assertTrue(w.members_strip_visible())
            self.assertNotIn('r77', self.state.created_rooms)     # 처음 열 때 한 번만
            strip = w.members_strip
            self.assertEqual(strip.title.text(), '대화상대 4명')
            self.assertEqual(list(strip.chips), ['홍길동', '김철수', '박민수', '최지우'])   # 나 → 이름순
            self.assertEqual(strip.chips['홍길동'].text(), '나')
            self.assertIn('방장', strip.chips['홍길동'].toolTip())
            # 초대되면 참여자 줄도 바로 바뀐다
            self._push_new_group('r77', ['홍길동', '김철수', '박민수', '최지우', '이영희'])
            self.assertEqual(strip.title.text(), '대화상대 5명')
            self.assertIn('이영희', strip.chips)
            # 칩을 누르면 프로필, [전체 보기]는 대화상대 팝업
            with mock.patch.object(w, 'open_profile') as prof:
                strip.chips['김철수'].click()
            prof.assert_called_once_with('김철수')
            with mock.patch.object(w, 'show_members_popup') as pop:
                strip.all_button.click()
            pop.assert_called_once()
            # › 버튼으로 접어도 사라지지 않고 한 줄(이름 미리보기)로 남는다 → 다시 누르면 펼침
            strip.toggle_button.click()
            self.assertFalse(w.members_strip_visible())
            self.assertFalse(strip.isHidden())
            self.assertTrue(strip.area.isHidden())
            self.assertTrue(strip.preview.text().startswith('나, '))
            self.assertEqual(strip.toggle_button.accessibleName(), '대화상대 목록 펼치기')
            self.assertNotIn('r77', self.state.settings.get('members_strip_open'))
            strip.toggle_button.click()
            self.assertTrue(w.members_strip_visible())
            self.assertEqual(strip.toggle_button.accessibleName(), '대화상대 목록 접기')
            self.assertEqual(strip.preview.text(), '')
            # 머리 줄 아무 곳이나 눌러도 열렸다 닫혔다
            QTest.mouseClick(strip.head, Qt.LeftButton, pos=strip.head.rect().center())
            self.assertFalse(w.members_strip_visible())
            QTest.mouseClick(strip.head, Qt.LeftButton, pos=strip.head.rect().center())
            self.assertTrue(w.members_strip_visible())
            # 방 메뉴로도 접기/펼치기
            menu = w.build_room_menu()
            next(a for a in menu.actions() if a.text() == '대화상대 목록 접기').trigger()
            self.assertFalse(w.members_strip_visible())
            self.assertIn('대화상대 목록 펼치기', [a.text() for a in w.build_room_menu().actions()])
        finally:
            w.close()

    def test_members_strip_state_remembered_per_room(self):
        self.win.set_members_strip(True)
        self.assertIn('r1', self.state.settings.get('members_strip_open'))
        w = ChatRoomWindow(self.state, 'r1')            # 같은 방을 다시 열면 펼친 채로
        try:
            self.assertTrue(w.members_strip_visible())
        finally:
            w.close()
        self.win.set_members_strip(False)
        w = ChatRoomWindow(self.state, 'r1')
        try:
            self.assertFalse(w.members_strip_visible())
            self.assertFalse(w.members_strip.isHidden())
        finally:
            w.close()

    def test_members_strip_in_every_room_collapsed_by_default(self):
        strip = self.win.members_strip
        self.assertIsNotNone(strip)                   # 기존 방에도 참여자 줄(접힘)이 있다
        self.assertFalse(strip.isHidden())
        self.assertFalse(self.win.members_strip_visible())
        self.assertEqual(strip.title.text(), f"대화상대 {len(self.state.room('r1')['members'])}명")
        self.assertIn('대화상대 목록 펼치기', [a.text() for a in self.win.build_room_menu().actions()])
        self.win.set_members_strip(True)
        self.assertEqual(len(self.win.members_strip.chips), len(self.state.room('r1')['members']))
        theme.set_dark(True)
        self.win.apply_theme()                       # 테마가 바뀌어도 다시 그린다
        self.assertTrue(self.win.members_strip_visible())
        theme.set_dark(False)
        # 1:1 방에도 있다 (나 + 상대)
        w = ChatRoomWindow(self.state, 'r2')
        try:
            self.assertEqual(w.members_strip.title.text(), '대화상대 2명')
            w.set_members_strip(True)
            self.assertTrue(w.members_strip_visible())
        finally:
            w.close()
        w = ChatRoomWindow(self.state, 'r3')            # 나와의 채팅에는 없다
        try:
            self.assertIsNone(w.members_strip)
            w.set_members_strip(True)
            self.assertFalse(w.members_strip_visible())
        finally:
            w.close()


class ThemeWallpaperTests(ChatRoomTestBase):
    def test_apply_theme_dark(self):
        theme.set_dark(True)
        try:
            self.win.apply_theme()
            flush()
            self.assertGreaterEqual(len(self.win.rows), 11)
            self.assertFalse(self.win.grab().isNull())
        finally:
            theme.set_dark(False)
            self.win.apply_theme()

    def test_wallpaper_repaint(self):
        calls = []
        orig = chat_room._wallpaper_api()
        if orig is None:
            self.skipTest('wallpaper 모듈 없음')
        real = orig.paint_wallpaper

        def spy(painter, rect, spec):
            calls.append(spec)
            real(painter, rect, spec)
        orig.paint_wallpaper = spy
        try:
            self.state.settings.set('room_wallpapers', {'r1': {'type': 'color', 'value': '#202830'}})
            self.assertEqual(self.win._wallpaper_spec, {'type': 'color', 'value': '#202830'})
            self.win.grab()
            self.assertIn({'type': 'color', 'value': '#202830'}, calls)
            self.assertEqual(self.win._outer['chip_fg'], '#FFFFFF')
            self.assertEqual(self.win._outer['title'], '#FFFFFF')   # 어두운 배경 → 흰 제목
            self.state.settings.set('room_wallpapers', {})
            self.assertIsNone(self.win._wallpaper_spec)
        finally:
            orig.paint_wallpaper = real


class KakaoUpdate2025Tests(ChatRoomTestBase):
    """2025~2026 카카오톡 개편: 메시지 수정, 조용히 보내기, URL 미리보기, 서랍 일정, AI 비서"""

    def push_mine(self, msg_id=60, content='내가 보낸 메시지', **extra):
        fields = dict(edited=False, edited_at=None, silent=False, link_preview=None)
        fields.update(extra)
        m = new_message(msg_id, '홍길동', content, **fields)
        self.conn.push('msg_new', message=m)
        return self.state.find_message('r1', msg_id) or m

    def menu_texts(self, menu):
        return [a.text() for a in menu.actions()]

    # ---- 메시지 수정
    def test_edit_menu_only_for_editable(self):
        mine = self.push_mine()
        texts = self.menu_texts(self.win.build_context_menu(mine))
        self.assertIn('수정', texts)
        self.assertEqual(texts.index('수정'), texts.index('답장') + 1)
        self.assertNotIn('수정', self.menu_texts(self.win.build_context_menu(self.win.rows[2].msg)))
        old = new_message(61, '홍길동', '오래된', minutes_ago=60 * 30)
        self.assertNotIn('수정', self.menu_texts(self.win.build_context_menu(old)))

    def test_edit_flow_enter_saves(self):
        mine = self.push_mine()
        self.win.edit.setPlainText('쓰던 초안')
        acts = {a.text(): a for a in self.win.build_context_menu(mine).actions()}
        acts['수정'].trigger()
        self.assertFalse(self.win.edit_bar.isHidden())
        self.assertEqual(self.win.edit_title.text(), '메시지 수정')
        self.assertEqual(self.win.edit.toPlainText(), '내가 보낸 메시지')
        self.assertEqual(self.win.send_btn.text(), '수정')
        self.win.edit.setPlainText('고친 메시지')
        self.conn.sent.clear()
        QTest.keyClick(self.win.edit, Qt.Key_Return)
        self.assertEqual(self.sent('edit'), [{'msg_id': 60, 'content': '고친 메시지'}])
        self.assertEqual(self.sent('send'), [])
        self.assertTrue(self.win.edit_bar.isHidden())
        self.assertEqual(self.win.send_btn.text(), '전송')
        self.assertEqual(self.win.edit.toPlainText(), '쓰던 초안')   # 초안 복원

    def test_edit_escape_and_x_cancel(self):
        mine = self.push_mine()
        self.win.edit.setPlainText('초안')
        self.win.start_edit(mine)
        self.win.edit.setPlainText('바꾸는 중')
        QTest.keyClick(self.win.edit, Qt.Key_Escape)
        self.assertIsNone(self.win._editing)
        self.assertTrue(self.win.edit_bar.isHidden())
        self.assertEqual(self.win.edit.toPlainText(), '초안')
        self.assertEqual(self.win.send_btn.text(), '전송')
        self.win.start_edit(mine)
        self.win.edit_cancel.click()
        self.assertTrue(self.win.edit_bar.isHidden())
        self.assertEqual(self.win.edit.toPlainText(), '초안')
        self.assertIsNone(self.conn.last('edit'))

    def test_edited_label_rerenders_only_that_row(self):
        mine = self.push_mine()
        others = {mid: row for mid, row in self.win.rows.items() if mid != 60}
        self.assertIsNone(self.win.rows[60].edited_label)
        self.conn.push('msg_updated', message=dict(mine, content='고침', edited=True, edited_at=iso(datetime.now())))
        row = self.win.rows[60]
        self.assertIsNotNone(row.edited_label)
        self.assertEqual(row.edited_label.text(), '수정됨')
        self.assertFalse(row.edited_label.isHidden())
        self.assertIn('고침', row.text_label.text())
        for mid, old in others.items():
            self.assertIs(self.win.rows[mid], old)

    def test_edited_label_visible_when_time_hidden(self):
        now = datetime.now().replace(second=5)
        a = new_message(70, '김철수', '첫째', timestamp=iso(now), edited=True)
        b = new_message(71, '김철수', '둘째', timestamp=iso(now.replace(second=30)))
        self.conn.push('msg_new', message=a)
        self.conn.push('msg_new', message=b)
        ra = self.win.rows[70]
        self.assertTrue(ra.time_label.isHidden())
        self.assertFalse(ra.meta_line.isHidden())
        self.assertFalse(ra.edited_label.isHidden())
        self.assertFalse(self.win.rows[71].meta_line.isHidden())

    def test_editing_message_deleted_cancels(self):
        mine = self.push_mine()
        self.win.start_edit(mine)
        self.conn.push('msg_updated', message=dict(mine, deleted=True, content='메시지가 삭제되었습니다'))
        self.assertIsNone(self.win._editing)

    # ---- 조용히 보내기
    def test_silent_send_via_menu(self):
        self.win.edit.setPlainText('조용히')
        menu = self.win.build_send_menu()
        acts = {a.text(): a for a in menu.actions()}
        self.assertTrue(acts['조용히 보내기'].isEnabled())
        acts['조용히 보내기'].trigger()
        p = self.conn.last('send')
        self.assertEqual((p['content'], p.get('silent')), ('조용히', True))
        self.assertEqual(self.win.edit.toPlainText(), '')
        self.assertFalse(self.win.build_send_menu().actions()[0].isEnabled())   # 빈 입력
        self.assertIn('조용히 보내기', self.win.send_btn.toolTip())
        self.assertEqual(self.win.send_btn.contextMenuPolicy(), Qt.CustomContextMenu)

    def test_silent_send_via_shortcut(self):
        self.win.edit.setPlainText('단축키')
        QTest.keyClick(self.win.edit, Qt.Key_Return, Qt.ControlModifier | Qt.ShiftModifier)
        p = self.conn.last('send')
        self.assertEqual((p['content'], p.get('silent')), ('단축키', True))
        self.win.edit.setPlainText('보통')
        QTest.keyClick(self.win.edit, Qt.Key_Return)
        self.assertNotIn('silent', self.conn.last('send'))

    def test_silent_indicator_on_my_messages_only(self):
        self.push_mine(80, '조용한 내 메시지', silent=True)
        self.conn.push('msg_new', message=new_message(81, '김철수', '조용한 남의 메시지', silent=True))
        self.assertIsNotNone(self.win.rows[80].silent_icon)
        self.assertFalse(self.win.rows[80].silent_icon.pixmap().isNull())
        self.assertIsNone(self.win.rows[81].silent_icon)

    # ---- URL 미리보기
    def test_link_preview_card(self):
        preview = {'url': 'https://example.com/docs', 'title': '예제 문서 제목 ' * 8,
                   'description': '설명 문장입니다. ' * 12, 'site': 'Example'}
        for mid, sender in ((90, '홍길동'), (91, '김철수')):
            self.conn.push('msg_new', message=new_message(mid, sender, '링크 https://example.com/docs',
                                                          link_preview=preview))
            card = self.win.rows[mid].link_card
            self.assertIsNotNone(card)
            self.assertEqual(card.site_label.text(), 'Example')
            self.assertLessEqual(card.title_label.text().count('\n'), 1)        # 최대 2줄
            self.assertTrue(card.title_label.text().endswith('…'))
            self.assertLessEqual(card.desc_label.text().count('\n'), 1)
            self.assertLessEqual(self.win.rows[mid].bubble.width(), self.win.max_bubble_width() + 1)
            self.assertEqual(card.objectName(), 'linkCardMine' if sender == '홍길동' else 'linkCard')
        with mock.patch.object(chat_room.osenv, 'open_url') as open_url:      # OS 공통 계층으로 연다
            QTest.mouseClick(self.win.rows[91].link_card, Qt.LeftButton)
            open_url.assert_called_once_with('https://example.com/docs')

    def test_link_preview_arrives_via_update(self):
        mine = self.push_mine(92, 'https://kcs.example.com')
        self.assertIsNone(self.win.rows[92].link_card)
        self.conn.push('msg_updated', message=dict(mine, link_preview={
            'url': 'https://kcs.example.com', 'title': 'KCS', 'description': '', 'site': ''}))
        card = self.win.rows[92].link_card
        self.assertEqual(card.site_label.text(), 'kcs.example.com')     # 사이트명 없으면 호스트
        self.assertIsNone(card.desc_label)

    # ---- 서랍 일정 탭
    def test_drawer_events_tab(self):
        past = dict(self.state.events[1], event_id=9, title='지난 회의',
                    start=iso(datetime.now() - timedelta(days=2)), end=iso(datetime.now() - timedelta(days=2, hours=-1)))
        self.state.events[9] = past
        dlg = chat_room.DrawerDialog(self.win, self.win)
        self.assertEqual(dlg.tabs.tabText(3), '일정 2')
        self.assertEqual(dlg.events.count(), 2)
        texts = [dlg.events.item(i).text() for i in range(2)]
        self.assertTrue(texts[0].startswith('지난 회의'))
        self.assertIn('주간 회의', texts[1])
        self.assertIn('3층 회의실', texts[1])
        self.assertTrue(dlg.events.item(0).data(Qt.UserRole + 1))
        self.assertEqual(dlg.events.item(0).foreground().color().name(), theme.qc('text_hint').name())
        self.assertFalse(dlg.events.item(1).data(Qt.UserRole + 1))
        with mock.patch('client.kakao.event_dialog.EventDetailDialog') as det:
            dlg.events.itemDoubleClicked.emit(dlg.events.item(1))
            det.assert_called_once()
            self.assertEqual(det.call_args[0][1]['event_id'], 1)
            det.return_value.exec_.assert_called_once()
        dlg.close()

    # ---- AI 비서
    def test_room_menu_invite_bot(self):
        acts = {a.text(): a for a in self.win.build_room_menu().actions()}
        self.assertIn('AI 비서 부르기', acts)
        acts['AI 비서 부르기'].trigger()
        self.assertEqual(self.conn.last('room_invite'), {'room_id': 'r1', 'usernames': ['AI 비서']})
        self.assertEqual(self.win.toast_label.text(), 'AI 비서를 초대했습니다. @AI 비서 로 불러보세요')

    def test_room_menu_ask_bot_when_member(self):
        self.state.users['AI 비서'] = {'username': 'AI 비서', 'display_name': 'AI 비서', 'online': True,
                                      'is_bot': True}
        room = dict(self.state.room('r1'), members=['홍길동', '김철수', '이영희', 'AI 비서'])
        self.conn.push('room_updated', room=room)
        acts = {a.text(): a for a in self.win.build_room_menu().actions()}
        self.assertNotIn('AI 비서 부르기', acts)
        acts['AI 비서에게 질문'].trigger()
        self.assertEqual(self.win.edit.toPlainText(), '@AI 비서 ')
        self.win.send_current()
        self.assertEqual(self.conn.last('send')['mentions'], ['AI 비서'])

    def test_direct_room_invite_bot_creates_group(self):
        w = ChatRoomWindow(self.state, 'r2')
        acts = {a.text(): a for a in w.build_room_menu().actions()}
        acts['AI 비서 부르기'].trigger()
        self.assertEqual(sorted(self.conn.last('room_create')['members']), ['AI 비서', '박민수'])
        w.close()
        w3 = ChatRoomWindow(self.state, 'r3')                # 나와의 채팅에는 없음
        self.assertNotIn('AI 비서 부르기', [a.text() for a in w3.build_room_menu().actions()])
        w3.close()


class HeaderTest(unittest.TestCase):
    """채팅방 헤더 부제목: 그룹(인원·그룹사 수) / 1:1(상태·소속) / 나와의 채팅 / AI 비서"""

    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_group_counts_companies(self):
        s = self.state
        for name, company in (('홍길동', 'KCS 홀딩스'), ('김철수', 'KCS 홀딩스'), ('이영희', 'KCS 물류')):
            self.conn.push('user_updated', user=dict(s.user(name), company=company))
        win = chat_room.ChatRoomWindow(s, 'r1')
        win.resize(480, 640)          # 헤더에 통화 버튼이 있어 400px에서는 부제목이 말줄임된다
        win._refresh_header()
        self.assertEqual(win.count_label.text(), '대화상대 3명 · 그룹사 2곳')
        self.assertIn('이영희', win.count_label.toolTip())

    def test_direct_shows_presence_and_org(self):
        s = self.state
        self.conn.push('user_updated', user=dict(s.user('박민수'), presence='away', company='KCS 물류',
                                                department='운영팀', title='사원'))
        win = chat_room.ChatRoomWindow(s, 'r2')
        win.resize(420, 640)
        win.show()
        app().processEvents()
        win._refresh_header()
        self.assertEqual(win.count_label.text(), '자리비움 · KCS 물류 운영팀 사원')
        self.assertFalse(win.count_label.icon().isNull())
        self.conn.push('user_status_changed', username='박민수', status='offline')
        win._refresh_header()
        self.assertTrue(win.count_label.text().startswith('오프라인'))

    def test_self_room(self):
        win = chat_room.ChatRoomWindow(self.state, 'r3')
        self.assertTrue(win.count_label.text().startswith('나와의 채팅'))


# ---------------------------------------------------------------- v3 (명세 §8)
def push_room(conn, room_id, rtype, members, **extra):
    room = {'room_id': room_id, 'type': rtype, 'name': extra.pop('name', ''), 'members': members,
            'created_at': iso(datetime.now() - timedelta(days=1)), 'notice': None, 'last_message': None,
            'read_pointers': {m: 0 for m in members}, 'unread_count': 0}
    room.update(extra)
    conn.push('room_updated', room=room)


def call_obj(call_id, room_id, mode, host, participants):
    return {'call_id': call_id, 'room_id': room_id, 'mode': mode, 'host': host, 'started_at': iso(datetime.now()),
            'participants': {u: {'role': 'host' if u == host else 'speaker', 'muted': False, 'video': False,
                                 'screen': False, 'hand': False} for u in participants}}


def all_menu_texts(menu):
    out = []
    for act in menu.actions():
        out.append(act.text())
        if act.menu():
            out += [f'{act.text()}/{a.text()}' for a in act.menu().actions()]
    return out


class SecretRoomTests(unittest.TestCase):
    def setUp(self):
        app()
        theme.set_dark(False)
        self.state, self.conn = make_demo_state()
        push_room(self.conn, 's1', 'secret', ['홍길동', '박민수'])
        msgs = [new_message(101, '박민수', 'QUJD', room_id='s1', kind='secret',
                            secret_text='비밀 이야기', secret_ok=True),
                new_message(102, '박민수', 'WFla', room_id='s1', kind='secret',
                            secret_text='🔒 이 기기에서는 읽을 수 없는 비밀 메시지입니다', secret_ok=False)]
        self.conn.push('history', room_id='s1', messages=msgs, has_more=False)
        self.state.history_requested.add('s1')
        self.win = ChatRoomWindow(self.state, 's1')
        self.win.resize(420, 720)
        self.win.show()
        flush()

    def tearDown(self):
        self.win.close()
        flush()

    def test_header_banner_and_tools(self):
        w = self.win
        self.assertTrue(w.is_secret())
        self.assertFalse(w.lock_icon.isHidden())
        self.assertEqual(w.count_label.text(), '종단간 암호화된 비밀채팅')
        self.assertFalse(w.title_label.text().startswith('🔒'))
        self.assertFalse(w.secret_banner.isHidden())
        self.assertIn('이 기기에서만 해독', w.secret_banner.text())
        for b in (w.file_btn, w.poll_btn, w.event_btn, w.schedule_btn, w.ladder_btn, w.board_btn):
            self.assertTrue(b.isHidden())
        self.assertFalse(w.emoji_btn.isHidden())
        self.assertEqual(w.call_modes(), ['voice', 'video'])
        self.assertNotIn('대화상대 초대', [a.text() for a in w.build_room_menu().actions()])
        self.assertFalse(w.grab().isNull())

    def test_secret_bubbles(self):
        ok, bad = self.win.rows[101], self.win.rows[102]
        self.assertIn('비밀 이야기', ok.text_label.text())
        self.assertEqual(bad.text_label.objectName(), 'secretFail')
        self.assertIn('읽을 수 없는', bad.text_label.text())

    def test_context_menu_restricted(self):
        texts = all_menu_texts(self.win.build_context_menu(self.win.rows[101].msg))
        for t in ('답장', '전달', '나에게 보내기', '공지 등록', '번역'):
            self.assertNotIn(t, texts)
        self.assertIn('복사', texts)
        self.assertNotIn('복사', all_menu_texts(self.win.build_context_menu(self.win.rows[102].msg)))
        self.win.copy_message(self.win.rows[101].msg)
        self.assertEqual(QApplication.clipboard().text(), '비밀 이야기')

    def test_send_uses_send_secret_and_error_toast(self):
        calls = []
        self.state.send_secret = lambda rid, text: calls.append((rid, text)) or '상대방이 아직 비밀채팅을 사용할 수 없습니다.'
        self.win.edit.setPlainText('안녕')
        self.win.send_current()
        self.assertEqual(calls, [('s1', '안녕')])
        self.assertIn('비밀채팅을 사용할 수 없습니다', self.win.toast_label.text())
        self.assertEqual(self.win.edit.toPlainText(), '안녕')     # 실패하면 입력 유지
        self.assertFalse([p for t, p in self.conn.sent if t == 'send'])
        self.state.send_secret = lambda rid, text: calls.append((rid, text)) or None
        self.win.send_current()
        self.assertEqual(self.win.edit.toPlainText(), '')

    def test_files_blocked(self):
        path = os.path.join(tempfile.mkdtemp(), 'a.txt')
        with open(path, 'w') as f:
            f.write('x')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        self.assertTrue(self.win.handle_mime(mime))
        self.assertFalse([p for t, p in self.conn.sent if t == 'send'])
        self.assertIn('파일을 보낼 수 없습니다', self.win.toast_label.text())


class CallTests(ChatRoomTestBase):
    def setUp(self):
        super().setUp()
        self.opened = []
        stub = type(sys)('client.kakao.call')
        stub.show_call = lambda state, call_id: self.opened.append(call_id)
        self._mods = mock.patch.dict(sys.modules, {'client.kakao.call': stub})
        self._mods.start()

    def tearDown(self):
        self._mods.stop()
        super().tearDown()

    def test_buttons_by_room_type(self):
        self.assertIn('voiceroom', self.win.call_modes())             # 그룹방
        w = ChatRoomWindow(self.state, 'r2')
        self.assertEqual(w.call_modes(), ['voice', 'video'])
        w3 = ChatRoomWindow(self.state, 'r3')
        self.assertEqual(w3.call_modes(), [])
        self.assertTrue(w3.call_btn.isHidden())
        self.assertFalse(w3.invite_btn.isHidden())

    def test_start_opens_window_when_hosted(self):
        self.win.call_menu().actions()[0].trigger()
        self.assertEqual(self.conn.last('call_start'), {'room_id': 'r1', 'mode': 'voice'})
        self.assertEqual(self.opened, [])
        self.conn.push('call_updated', call=call_obj(7, 'r1', 'voice', '홍길동', ['홍길동']))
        self.assertEqual(self.opened, [7])
        self.assertFalse(self.win.call_banner.isHidden())
        self.assertEqual(self.win.call_banner_label.text(), '보이스톡 · 통화 중 · 1명')
        self.assertEqual(self.win.call_banner_btn.text(), '돌아가기')
        self.conn.push('call_updated', call=call_obj(7, 'r1', 'voice', '홍길동', ['홍길동', '김철수']))
        self.assertEqual(self.opened, [7])                            # 한 번만 연다
        self.assertIn('2명', self.win.call_banner_label.text())
        self.win.call_banner_btn.click()
        self.assertEqual(self.opened, [7, 7])

    def test_join_from_banner_and_card(self):
        self.conn.push('call_updated', call=call_obj(9, 'r1', 'video', '김철수', ['김철수']))
        self.assertEqual(self.win.call_banner_btn.text(), '참여하기')
        self.conn.push('msg_new', message=new_message(70, '김철수', 'video', kind='call', call_id=9))
        card = self.win.rows[70].card
        self.assertEqual(card.title.text(), '페이스톡')
        self.assertEqual(card.join_button.text(), '참여하기')
        card.join_button.click()
        self.assertEqual(self.conn.last('call_join'), {'call_id': 9})
        self.assertEqual(self.opened, [9])
        self.conn.sent.clear()
        self.win.call_banner_btn.click()
        self.assertEqual(self.conn.last('call_join'), {'call_id': 9})
        self.conn.push('call_ended', call_id=9, room_id='r1', duration_seconds=65)
        self.assertTrue(self.win.call_banner.isHidden())
        card = self.win.rows[70].card
        self.assertIsNone(card.join_button)
        self.assertEqual(card.ended_label.text(), '종료된 통화')

    def test_start_when_call_exists_joins(self):
        self.conn.push('call_updated', call=call_obj(3, 'r1', 'voiceroom', '김철수', ['김철수']))
        self.win.call_menu().actions()[1].trigger()
        self.assertIsNone(self.conn.last('call_start'))
        self.assertEqual(self.conn.last('call_join'), {'call_id': 3})
        self.assertEqual(self.opened, [3])

    def test_missing_call_module_toasts(self):
        with mock.patch.dict(sys.modules, {'client.kakao.call': None}):
            self.assertIsNone(self.win.open_call_window(1))
        self.assertIn('통화 기능', self.win.toast_label.text())


def make_ladder(participants, results, seed=1):
    """서버와 같은 규칙의 무작위 사다리 (같은 row에서 인접 가로줄 없음)"""
    import random
    rnd = random.Random(seed)
    n = len(participants)
    rungs = []
    for row in range(chat_room.LADDER_ROWS):
        col = 0
        while col < n - 1:
            if rnd.random() < 0.5:
                rungs.append([row, col])
                col += 2
            else:
                col += 1
    mapping = {}
    for i, name in enumerate(participants):
        _, end = chat_room.ladder_path_columns(i, rungs)
        mapping[name] = results[end]
    return {'participants': participants, 'results': results, 'rungs': rungs, 'mapping': mapping}


class LadderTests(ChatRoomTestBase):
    def test_dialog_defaults_and_validation(self):
        dlg = chat_room.LadderDialog(self.state, 'r1', self.win)
        parts, results = dlg.values()
        self.assertEqual(parts, ['홍길동', '김철수', '이영희'])
        self.assertEqual(results, ['당첨', '꽝', '꽝'])
        self.assertTrue(dlg.ok.isEnabled())
        dlg.rows[2][2].setText('')                                   # 결과 하나 비움 → 개수 불일치
        self.assertFalse(dlg.ok.isEnabled())
        self.assertIn('개수가 같아야', dlg.error.text())
        dlg.remove_row(dlg.rows[2])
        self.assertTrue(dlg.ok.isEnabled())
        dlg.remove_row(dlg.rows[1])                                  # 최소 2개 유지
        self.assertEqual(len(dlg.rows), 2)
        for i in range(10):
            dlg.add_row(f'p{i}', f'r{i}')
        self.assertEqual(len(dlg.rows), 10)
        self.assertFalse(dlg.add_button.isEnabled())

    def test_open_dialog_creates_ladder(self):
        with mock.patch.object(chat_room.LadderDialog, 'exec_', return_value=chat_room.QDialog.Accepted):
            self.win.ladder_btn.click()
        self.assertEqual(self.conn.last('ladder_create'),
                         {'room_id': 'r1', 'participants': ['홍길동', '김철수', '이영희'], 'results': ['당첨', '꽝', '꽝']})

    def test_ladder_card_reveal(self):
        ladder = make_ladder(['가', '나', '다', '라'], ['1등', '2등', '3등', '4등'], seed=3)
        self.conn.push('msg_new', message=new_message(80, '김철수', '', kind='ladder', ladder=ladder))
        card = self.win.rows[80].card
        self.assertIsInstance(card, chat_room.LadderCard)
        self.assertTrue(card.result_label.isHidden())
        self.assertFalse(card.canvas.grab().isNull())
        card.reveal_button.click()
        self.assertTrue(card._timer.isActive())
        for _ in range(100):
            if not card._timer.isActive():
                break
            card._step()
        self.assertEqual(card.canvas.progress, 1.0)
        self.assertFalse(card.result_label.isHidden())
        for name, res in ladder['mapping'].items():
            self.assertIn(f'{name} → {res}', card.result_label.text())
        self.assertFalse(card.canvas.grab().isNull())
        # 행을 다시 만들어도 결과 유지
        self.conn.push('msg_updated', message=dict(self.state.find_message('r1', 80), reactions={'👍': ['김철수']}))
        self.assertFalse(self.win.rows[80].card.result_label.isHidden())

    def test_path_matches_mapping_without_server_mapping(self):
        ladder = make_ladder(list('ABCDEFGHIJ'), [str(i) for i in range(10)], seed=5)
        expected = dict(ladder['mapping'])
        self.assertEqual(sorted(expected.values()), sorted(ladder['results']))     # 순열
        ladder.pop('mapping')
        self.conn.push('msg_new', message=new_message(81, '김철수', '', kind='ladder', ladder=ladder))
        self.assertEqual(dict(self.win.rows[81].card.mapping()), expected)


def board_post(post_id, author, title, comments=()):
    return {'post_id': post_id, 'room_id': 'r1', 'author': author, 'title': title, 'content': f'{title} 내용',
            'created_at': iso(datetime.now() - timedelta(minutes=post_id)),
            'comments': [{'comment_id': i, 'author': a, 'content': c, 'created_at': iso(datetime.now())}
                         for i, (a, c) in enumerate(comments, 1)]}


class BoardTests(ChatRoomTestBase):
    def open_board(self, post_id=None):
        with mock.patch.object(chat_room.BoardDialog, 'exec_', return_value=0):
            return self.win.open_board(post_id)

    def test_list_view_comment_delete(self):
        dlg = self.open_board()
        self.assertEqual(self.conn.last('board_list'), {'room_id': 'r1'})
        self.conn.push('board_list', room_id='r1', posts=[board_post(1, '홍길동', '회식 장소'),
                                                         board_post(2, '김철수', '자료 모음', [('이영희', '감사!')])])
        self.assertEqual(dlg.post_list.count(), 2)
        dlg.show_post(2)
        self.assertEqual(dlg.stack.currentIndex(), dlg.POST)
        self.assertEqual(dlg.comment_list.count(), 1)
        self.assertTrue(dlg.delete_button.isHidden())                # 남의 글
        dlg.comment_edit.setText('좋아요')
        dlg.submit_comment()
        self.assertEqual(self.conn.last('board_comment'), {'post_id': 2, 'content': '좋아요'})
        self.conn.push('board_updated', post=board_post(2, '김철수', '자료 모음', [('이영희', '감사!'), ('홍길동', '좋아요')]))
        self.assertEqual(dlg.comment_list.count(), 2)
        dlg.show_post(1)
        self.assertFalse(dlg.delete_button.isHidden())
        dlg.confirm = lambda *a: True
        dlg.delete_post()
        self.assertEqual(self.conn.last('board_delete'), {'post_id': 1})
        self.conn.push('board_deleted', post_id=1, room_id='r1')
        self.assertEqual(dlg.stack.currentIndex(), dlg.LIST)
        self.assertEqual(dlg.post_list.count(), 1)
        dlg.done(0)

    def test_write_post(self):
        dlg = self.open_board()
        dlg.show_write()
        self.assertFalse(dlg.post_button.isEnabled())
        dlg.title_edit.setText('새 공지')
        dlg.content_edit.setPlainText('본문')
        dlg.post_button.click()
        self.assertEqual(self.conn.last('board_post'), {'room_id': 'r1', 'title': '새 공지', 'content': '본문'})
        self.assertEqual(dlg.stack.currentIndex(), dlg.LIST)
        dlg.done(0)

    def test_board_card_opens_post_and_menu(self):
        self.conn.push('msg_new', message=new_message(90, '김철수', '자료 모음', kind='board', post_id=2))
        card = self.win.rows[90].card
        self.assertEqual(card.title.text(), '자료 모음')   # 머리에 게시판 아이콘이 있어 제목에 이모지를 붙이지 않는다
        opened = []
        self.win.open_board = lambda post_id=None: opened.append(post_id)
        card.clicked.emit()
        self.assertEqual(opened, [2])
        acts = {a.text(): a for a in self.win.build_room_menu().actions()}
        acts['게시판'].trigger()
        self.assertEqual(opened, [2, None])

    def test_pending_post_shown_when_list_arrives(self):
        dlg = self.open_board(2)
        self.assertEqual(dlg.stack.currentIndex(), dlg.LIST)
        self.conn.push('board_list', room_id='r1', posts=[board_post(2, '김철수', '자료 모음')])
        self.assertEqual(dlg.stack.currentIndex(), dlg.POST)
        self.assertEqual(dlg.post_title.text(), '자료 모음')
        dlg.done(0)


class BubbleStyleKeywordTests(ChatRoomTestBase):
    def push_mine(self, msg_id=60, content='내 메시지 https://example.com'):
        self.conn.push('msg_new', message=new_message(msg_id, '홍길동', content))
        return self.win.rows[msg_id]

    def test_bubble_style_applies_and_repaints(self):
        row = self.push_mine()
        self.assertEqual(row.bubble.radius, theme.BUBBLE_STYLES['basic']['radius'])   # KCS Ink 기본 17
        self.state.settings.set('bubble_style', 'pastel')
        row = self.win.rows[60]
        self.assertEqual(row.bubble.fill, '#FFD6E0')
        self.assertEqual(row.bubble.radius, 16)
        self.assertEqual(self.win._mine['fg'], '#3B2730')
        self.assertIn('#1F4FB8', row.text_label.text())             # 밝은 말풍선 → 진한 링크색
        self.assertIn('rgba(59,39,48,0.62)', self.win.styleSheet())  # 보조색은 글자색에서 파생
        self.assertFalse(self.win.grab().isNull())
        self.state.settings.set('bubble_style', {'preset': 'mono'})
        row = self.win.rows[60]
        self.assertEqual(row.bubble.fill, '#3C3C3C')
        self.assertIn('#C9D6FF', row.text_label.text())             # 어두운 말풍선 → 밝은 링크색
        self.assertIsNone(self.win.rows[2].bubble.fill)             # 상대 말풍선은 그대로

    def test_keyword_highlight(self):
        self.assertIsNone(self.win.rows[2].keyword_chip)
        self.state.settings.set('alert_keywords', ['회의'])
        row = self.win.rows[2]                                      # 김철수: '...회의 시간...'
        self.assertIsNotNone(row.keyword_chip)
        self.assertIn('키워드', row.keyword_chip.accessibleName())
        self.assertEqual(row.bubble.outline, chat_room.KEYWORD_ACCENT)
        self.assertIsNone(self.win.rows[3].keyword_chip)
        mine = self.push_mine(61, '회의 자료 올렸어요')
        self.assertIsNone(mine.keyword_chip)                        # 내 메시지는 강조 안 함


class OpenChatAlertRetentionTests(ChatRoomTestBase):
    def test_open_chat_nicknames_and_header(self):
        push_room(self.conn, 'o1', 'open', ['홍길동', '김철수'], name='등산 모임', description='주말 등산 같이 가요',
                  tags=['등산', '주말'], nicknames={'홍길동': '산타는길동', '김철수': '철수산악회'})
        self.conn.push('history', room_id='o1', has_more=False,
                       messages=[new_message(200, '김철수', '@산타는길동 이번 주 어때요?', room_id='o1')])
        self.state.history_requested.add('o1')
        w = ChatRoomWindow(self.state, 'o1')
        w.resize(480, 700)
        w.show()
        flush()
        self.assertEqual(w.count_label.text(), '오픈채팅 · 2명')
        self.assertIn('주말 등산 같이 가요', w.count_label.toolTip())
        self.assertIn('#등산', w.count_label.toolTip())
        self.assertEqual(w.rows[200].sender_label.text(), '철수산악회')
        self.assertIn('산타는길동', w.mention_names())
        self.assertIn('voiceroom', w.call_modes())
        w.close()

    def test_alert_mode_submenu(self):
        menu = self.win.build_room_menu()
        sub = [a for a in menu.actions() if a.text() == '알림 설정'][0].menu()
        acts = {a.text(): a for a in sub.actions()}
        self.assertEqual(list(acts), ['모든 메시지', '멘션만', '끄기'])
        self.assertTrue(acts['모든 메시지'].isChecked())
        self.assertTrue(self.win.mute_icon.isHidden())
        acts['멘션만'].trigger()
        self.assertEqual(self.state.room_alert_mode('r1'), 'mention')
        self.assertFalse(self.win.mute_icon.isHidden())
        self.assertEqual(self.win.mute_icon.toolTip(), '멘션 알림만 받기')
        sub = [a for a in self.win.build_room_menu().actions() if a.text() == '알림 설정'][0].menu()
        self.assertTrue([a for a in sub.actions() if a.text() == '멘션만'][0].isChecked())
        self.win.set_alert_mode('off')
        self.assertTrue(self.state.is_muted('r1'))
        self.assertEqual(self.win.mute_icon.toolTip(), '알림 끔')
        self.win.set_alert_mode('all')
        self.assertFalse(self.state.is_muted('r1'))
        self.assertTrue(self.win.mute_icon.isHidden())

    def test_file_retention_and_expired(self):
        exp = iso(datetime(2027, 1, 6, 12, 0))
        self.conn.push('msg_new', message=new_message(
            300, '김철수', 'a.pdf', kind='file',
            attachment={'attachment_id': 30, 'name': 'a.pdf', 'mime': 'application/pdf', 'size': 10, 'expires_at': exp}))
        row = self.win.rows[300]
        self.assertEqual(row.retention_label.text(), '~2027.01.06까지 보관')
        self.assertTrue(row.save_button.isEnabled())
        self.conn.push('msg_new', message=new_message(
            301, '김철수', 'b.pdf', kind='file',
            attachment={'attachment_id': 31, 'name': 'b.pdf', 'mime': 'application/pdf', 'size': 10,
                        'expires_at': exp, 'expired': True}))
        row = self.win.rows[301]
        self.assertEqual(row.retention_label.text(), '보관 기간이 지난 파일입니다')
        self.assertFalse(row.save_button.isEnabled())
        self.assertFalse(row.open_button.isEnabled())
        self.conn.sent.clear()
        self.win.save_attachment(row.msg)
        self.win.open_attachment(row.msg)
        self.assertIsNone(self.conn.last('attachment_get'))

    def test_image_retention_and_expired(self):
        exp = iso(datetime(2027, 1, 6, 12, 0))
        self.conn.sent.clear()
        self.conn.push('msg_new', message=new_message(
            310, '김철수', 'p.png', kind='image',
            attachment={'attachment_id': 40, 'name': 'p.png', 'mime': 'image/png', 'size': 10, 'expires_at': exp}))
        self.assertEqual(self.win.rows[310].retention_label.text(), '~2027.01.06까지 보관')
        self.assertEqual(self.conn.last('attachment_get'), {'attachment_id': 40})
        self.conn.sent.clear()
        self.conn.push('msg_new', message=new_message(
            311, '김철수', 'q.png', kind='image',
            attachment={'attachment_id': 41, 'name': 'q.png', 'mime': 'image/png', 'size': 10, 'expired': True}))
        row = self.win.rows[311]
        self.assertEqual(row.thumb.text(), '보관 기간이 지난 파일입니다')
        self.assertIsNone(self.conn.last('attachment_get'))
        self.win.open_image(row.msg)
        self.assertIsNone(self.conn.last('attachment_get'))
        self.assertIn('보관 기간', self.win.toast_label.text())


class HeaderInviteTest(unittest.TestCase):
    """사용자 요청 기능: 헤더의 대화상대 초대 버튼은 비밀채팅을 빼고 항상 보인다"""

    def test_invite_visible_with_call_menu(self):
        app()
        state, _conn = make_demo_state()
        for rid in ('r1', 'r2', 'r3'):
            w = chat_room.ChatRoomWindow(state, rid)
            self.assertFalse(w.invite_btn.isHidden(), rid)
        w = chat_room.ChatRoomWindow(state, 'r1')
        self.assertEqual([a.text() for a in w.call_menu().actions()], ['보이스톡', '페이스톡', '보이스룸'])


class ReviewFixRegressionTests(ChatRoomTestBase):
    """검토 반영: 다크 전환 시 바 아이콘 갱신 · 오프라인 이전 대화 로딩 플래그 · 창 목록 정리"""

    def test_bar_icons_follow_dark_mode(self):
        btns = (self.win.search_up, self.win.search_down, self.win.search_close,
                self.win.notice_close, self.win.reply_cancel, self.win.edit_cancel)

        def lightness(b):   # 아이콘에서 가장 밝은 불투명 픽셀 (text_sub: 라이트 #6B6B6B → 다크 #A6A6A6)
            img = b.icon().pixmap(16, 16).toImage()
            return max((QColor.fromRgba(img.pixel(x, y)).lightness() for x in range(img.width())
                        for y in range(img.height()) if QColor.fromRgba(img.pixel(x, y)).alpha() > 200), default=0)
        before = [lightness(b) for b in btns]
        theme.set_dark(True)
        try:
            self.win.apply_theme()
            for b, old in zip(btns, before):
                self.assertGreater(lightness(b), old + 20, b.accessibleName())   # 다크 색으로 다시 그림
        finally:
            theme.set_dark(False)
            self.win.apply_theme()

    def test_load_older_offline_does_not_stick(self):
        self.state.has_more[self.room] = True
        self.win._hidden_older = 0
        self.conn.is_connected = False
        try:
            self.assertFalse(self.win._load_older())
            self.assertFalse(self.win._loading_older)
            self.assertIsNone(self.win._anchor)
        finally:
            self.conn.is_connected = True
        self.assertTrue(self.win._load_older())    # 다시 연결되면 정상 요청
        self.assertTrue(self.win._loading_older)
        self.state.connection_lost.emit('끊김')     # 응답 전에 끊기면 플래그를 푼다
        self.assertFalse(self.win._loading_older)
        self.assertIsNone(self.win._anchor)

    def test_destroyed_old_window_keeps_new_entry(self):
        from PyQt5.QtCore import QEvent
        from client.kakao.main_window import MainWindow
        main = MainWindow(self.state, self.state.settings, server_address='127.0.0.1:5555')
        try:
            self.state.room_opened.emit('r2')
            old = main.room_windows['r2']
            newer = object()
            main.room_windows['r2'] = newer        # 같은 방을 새 창으로 다시 연 상황
            old.close()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            flush()
            self.assertIs(main.room_windows.get('r2'), newer)
            main.room_windows.pop('r2', None)
            self.state.room_opened.emit('r2')
            again = main.room_windows['r2']
            again.close()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            flush()
            self.assertNotIn('r2', main.room_windows)   # 자기 항목이면 지운다
        finally:
            for w in list(main.room_windows.values()):
                if hasattr(w, 'close'):
                    w.close()
            main.close()
            main.deleteLater()


if __name__ == '__main__':
    unittest.main()
