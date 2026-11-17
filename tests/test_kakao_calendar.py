# -*- coding: utf-8 -*-
"""톡캘린더 UI 테스트 (CalendarTab / EventDialog / EventDetailDialog)

    QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_kakao_calendar -v
"""

import unittest
from datetime import date, datetime, timedelta

from tests.kakao_fakes import make_demo_state, app

from PyQt5.QtCore import Qt, QDateTime, QDate, QTime  # noqa: E402

from client.kakao import theme  # noqa: E402
from client.kakao.calendar_tab import CalendarTab, MonthGrid, event_dates, time_text  # noqa: E402
from client.kakao.event_dialog import EventDialog, EventDetailDialog  # noqa: E402


def _qdt(dt):
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute))


class CalendarTabTest(unittest.TestCase):
    def setUp(self):
        app()
        theme.set_dark(False)
        self.state, self.conn = make_demo_state()
        self.tab = CalendarTab(self.state)
        self.tab.resize(420, 680)
        self.today = date.today()
        self.conn.sent.clear()

    def tearDown(self):
        theme.set_dark(False)
        self.tab.deleteLater()

    def test_builds(self):
        self.assertEqual(self.tab.month_label.text(), f'{self.today.year}년 {self.today.month}월')
        self.assertEqual(self.tab.selected, self.today)
        self.tab.grab()  # 그리기 오류 없음

    def test_month_navigation_requests_range(self):
        self.tab.next_month()
        y, m = (self.today.year, self.today.month + 1) if self.today.month < 12 else (self.today.year + 1, 1)
        req = self.conn.last('event_list')
        self.assertIsNotNone(req)
        start = datetime(y, m, 1) - timedelta(days=7)
        ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
        end = datetime(ny, nm, 1) + timedelta(days=7)
        self.assertEqual(req['start'], start.isoformat(timespec='seconds'))
        self.assertEqual(req['end'], end.isoformat(timespec='seconds'))
        self.assertEqual(self.tab.month_label.text(), f'{y}년 {m}월')
        self.tab.prev_month()
        self.tab.prev_month()
        self.assertEqual(len([t for t, _ in self.conn.sent if t == 'event_list']), 3)
        # 같은 달 안에서 날짜 선택은 요청하지 않음
        self.conn.sent.clear()
        self.tab.go_today()
        self.conn.sent.clear()
        self.tab.select_date(self.today)
        self.assertIsNone(self.conn.last('event_list'))

    def test_day_selection_lists_events(self):
        self.tab.select_date(self.today)
        titles = self.tab.agenda_titles()
        self.assertIn('치과 예약', titles)
        self.assertIn('김철수님 생일', titles)
        self.assertNotIn('주간 회의', titles)

        self.tab.select_date(self.today + timedelta(days=1))
        self.assertEqual(self.tab.agenda_titles(), ['주간 회의'])

        for offset in (3, 4):
            self.tab.select_date(self.today + timedelta(days=offset))
            self.assertEqual(self.tab.agenda_titles(), ['워크숍'])
            ev = self.tab.agenda_entries[0][1]
            self.assertEqual(time_text(ev, self.today + timedelta(days=offset)), '종일')

        self.tab.select_date(self.today + timedelta(days=5))
        self.assertEqual(self.tab.agenda_titles(), [])
        labels = [w.text() for w in self.tab.agenda_box.findChildren(type(self.tab.day_label))]
        self.assertIn('일정이 없습니다', labels)

    def test_workshop_spans_two_cells(self):
        ws = self.state.events[3]
        first, last = event_dates(ws)
        self.assertEqual((last - first).days, 1)
        grid = self.tab.grid
        week_start = first - timedelta(days=(first.weekday() + 1) % 7)
        placed, hidden = grid.week_layout(week_start)
        spans = {ev['event_id']: (c0, c1) for ev, c0, c1, lane in placed}
        if last <= week_start + timedelta(days=6):
            self.assertEqual(spans[3][1] - spans[3][0], 1)
        else:
            self.assertEqual(spans[3], (6, 6))

    def test_overflow_counts(self):
        for i in range(4):
            self.conn.push('event_updated', event={
                'event_id': 100 + i, 'room_id': None, 'creator': '홍길동', 'title': f'추가{i}',
                'description': '', 'location': '', 'start': f'{self.today.isoformat()}T0{i + 1}:00:00',
                'end': f'{self.today.isoformat()}T0{i + 1}:30:00', 'all_day': False, 'remind_minutes': None,
                'color': '#B184F2', 'attendees': {'홍길동': 'yes'}, 'created_at': '2026-01-01T00:00:00'})
        week_start = self.today - timedelta(days=(self.today.weekday() + 1) % 7)
        placed, hidden = self.tab.grid.week_layout(week_start)
        self.assertEqual(hidden.get(self.today), 5 - MonthGrid.MAX_LANES)
        self.tab.grab()

    def test_birthday_today(self):
        self.tab.select_date(self.today)
        self.assertEqual(self.tab.agenda_entries[0][0], 'birthday')
        self.assertEqual(self.tab.agenda_entries[0][1]['username'], '김철수')

    def test_live_event_updates(self):
        tomorrow = self.today + timedelta(days=1)
        self.tab.select_date(tomorrow)
        ev = dict(self.state.events[1], title='주간 회의 (변경)')
        self.conn.push('event_updated', event=ev)
        self.assertEqual(self.tab.agenda_titles(), ['주간 회의 (변경)'])
        self.conn.push('event_deleted', event_id=1)
        self.assertEqual(self.tab.agenda_titles(), [])

    def test_todos(self):
        self.assertEqual(set(self.tab.todo_rows), {1, 2, 3})
        self.tab.set_section(1)
        self.assertEqual(self.tab.stack.currentIndex(), 1)
        self.tab.todo_input.setText('우유 사기')
        self.tab.todo_due_btn.setChecked(True)
        self.tab.todo_due.setDate(QDate(2026, 12, 25))
        self.tab.add_todo()
        self.assertEqual(self.conn.last('todo_create'), {'title': '우유 사기', 'due': '2026-12-25'})
        self.assertEqual(self.tab.todo_input.text(), '')
        self.tab.todo_input.setText('기한 없음')
        self.tab.add_todo()
        self.assertEqual(self.conn.last('todo_create'), {'title': '기한 없음'})
        self.tab.todo_rows[1].toggled.emit(self.tab.todo_rows[1].todo)
        self.assertEqual(self.conn.last('todo_update'), {'todo_id': 1, 'done': True})
        self.tab.todo_rows[2].toggled.emit(self.tab.todo_rows[2].todo)
        self.assertEqual(self.conn.last('todo_update'), {'todo_id': 2, 'done': False})
        self.tab.todo_rows[3].delete_requested.emit(self.tab.todo_rows[3].todo)
        self.assertEqual(self.conn.last('todo_delete'), {'todo_id': 3})
        # 서버 푸시 반영
        self.conn.push('todo_deleted', todo_id=3)
        self.assertNotIn(3, self.tab.todo_rows)
        self.conn.push('todo_updated', todo={'todo_id': 9, 'owner': '홍길동', 'title': '새 일',
                                             'due': None, 'done': False, 'created_at': '2026-01-01T00:00:00'})
        self.assertIn(9, self.tab.todo_rows)

    def test_overdue_todo_chip(self):
        past = (self.today - timedelta(days=2)).isoformat()
        self.conn.push('todo_updated', todo={'todo_id': 7, 'owner': '홍길동', 'title': '밀린 일',
                                             'due': past, 'done': False, 'created_at': '2026-01-01T00:00:00'})
        label, overdue = self.tab.todo_rows[7].due_info()
        self.assertTrue(overdue)
        self.tab.todo_rows[2].todo  # 오늘 기한 + 완료 → 지남 아님
        self.assertFalse(self.tab.todo_rows[2].due_info()[1])

    def test_dark_mode(self):
        theme.set_dark(True)
        self.tab.apply_theme()
        self.assertIn(theme.c('bg'), self.tab.styleSheet())
        self.tab.set_section(1)
        self.tab.grab()


class EventDialogTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()
        self.today = date.today()

    def test_create_personal_event(self):
        d = self.today + timedelta(days=10)
        dlg = EventDialog(self.state, default_date=d)
        self.assertTrue(dlg.save_btn.isEnabled())    # 제목이 없어도 누를 수 있고, 누르면 이유를 알려 준다
        dlg.location_edit.setText('부산 한국자산관리공사')
        self.assertFalse(dlg.accept())
        self.assertIsNone(self.conn.last('event_create'))
        self.assertTrue(dlg.title_error.isVisibleTo(dlg))
        self.assertTrue(dlg.title_edit.property('invalid'))
        dlg.title_edit.setText('  저녁 약속 ')
        self.assertFalse(dlg.title_error.isVisibleTo(dlg))                     # 쓰면 바로 사라진다
        self.assertTrue(dlg.save_btn.isEnabled())
        start = datetime(d.year, d.month, d.day, 19, 0)
        dlg.start_edit.setDateTime(_qdt(start))
        dlg.end_edit.setDateTime(_qdt(start + timedelta(hours=2)))
        dlg.location_edit.setText('강남역')
        dlg.desc_edit.setPlainText('예약 완료')
        dlg.remind_combo.setCurrentIndex(5)          # 1시간 전
        dlg.swatches[2].setChecked(True)
        for i in range(dlg.attendee_list.count()):
            item = dlg.attendee_list.item(i)
            if item.data(Qt.UserRole) in ('김철수', '박민수'):
                item.setCheckState(Qt.Checked)
        self.assertTrue(dlg.accept())
        p = self.conn.last('event_create')
        self.assertEqual(p['title'], '저녁 약속')
        self.assertEqual(p['start'], start.isoformat(timespec='seconds'))
        self.assertEqual(p['end'], (start + timedelta(hours=2)).isoformat(timespec='seconds'))
        self.assertFalse(p['all_day'])
        self.assertEqual(p['remind_minutes'], 60)
        self.assertEqual(p['color'], theme.EVENT_COLORS[2])
        self.assertEqual(p['location'], '강남역')
        self.assertEqual(p['description'], '예약 완료')
        self.assertEqual(sorted(p['attendees']), ['김철수', '박민수'])
        self.assertNotIn('room_id', p)

    def test_start_moves_end_and_validation(self):
        d = self.today + timedelta(days=2)
        dlg = EventDialog(self.state, default_date=d)
        dlg.title_edit.setText('회의')
        s = datetime(d.year, d.month, d.day, 10, 0)
        dlg.start_edit.setDateTime(_qdt(s))
        dlg.end_edit.setDateTime(_qdt(s + timedelta(minutes=90)))
        dlg.start_edit.setDateTime(_qdt(s + timedelta(hours=3)))
        self.assertEqual(dlg.start_end()[1], s + timedelta(hours=4, minutes=30))
        dlg.end_edit.setDateTime(_qdt(s))
        self.assertFalse(dlg.save_btn.isEnabled())
        self.assertFalse(dlg.error_label.isHidden())
        self.assertFalse(dlg.accept())
        self.assertIsNone(self.conn.last('event_create'))

    def test_create_all_day_room_event(self):
        d = self.today + timedelta(days=6)
        dlg = EventDialog(self.state, room_id='r1', default_date=d)
        self.assertIsNone(dlg.attendee_list)
        self.assertIn('프로젝트 팀', dlg.room_label.text())
        dlg.title_edit.setText('팀 야유회')
        dlg.all_day_check.setChecked(True)
        dlg.end_edit.setDateTime(_qdt(datetime(d.year, d.month, d.day, 9) + timedelta(days=1)))
        dlg.remind_combo.setCurrentIndex(0)
        dlg.accept()
        p = self.conn.last('event_create')
        self.assertTrue(p['all_day'])
        self.assertEqual(p['room_id'], 'r1')
        self.assertNotIn('attendees', p)
        self.assertIsNone(p['remind_minutes'])
        self.assertEqual(p['start'], datetime(d.year, d.month, d.day).isoformat(timespec='seconds'))
        e = d + timedelta(days=1)
        self.assertEqual(p['end'], datetime(e.year, e.month, e.day, 23, 59, 59).isoformat(timespec='seconds'))

    def test_edit_sends_only_changed(self):
        ev = self.state.events[1]
        dlg = EventDialog(self.state, event=ev)
        self.assertEqual(dlg.changed_fields(), {})
        dlg.title_edit.setText('주간 회의 2')
        dlg.location_edit.setText('2층 라운지')
        dlg.accept()
        p = self.conn.last('event_update')
        self.assertEqual(p, {'event_id': 1, 'title': '주간 회의 2', 'location': '2층 라운지'})

    def test_edit_all_day_unchanged(self):
        dlg = EventDialog(self.state, event=self.state.events[3])
        self.assertTrue(dlg.all_day_check.isChecked())
        self.assertEqual(dlg.changed_fields(), {})
        dlg.remind_combo.setCurrentIndex(1)
        self.assertEqual(dlg.changed_fields(), {'remind_minutes': 0})

    def test_delete_for_creator(self):
        dlg = EventDialog(self.state, event=self.state.events[2])
        self.assertIsNotNone(dlg.delete_btn)
        self.assertFalse(dlg.request_delete(confirm=lambda text: False))
        self.assertIsNone(self.conn.last('event_delete'))
        self.assertTrue(dlg.request_delete(confirm=lambda text: True))
        self.assertEqual(self.conn.last('event_delete'), {'event_id': 2})

    def test_no_delete_for_non_creator(self):
        ev = dict(self.state.events[1], creator='김철수')
        dlg = EventDialog(self.state, event=ev)
        self.assertIsNone(dlg.delete_btn)
        self.assertFalse(dlg.request_delete(confirm=lambda text: True))


class ShowAndProcessEventsTest(unittest.TestCase):
    """실제로 show() 하고 이벤트 루프를 돌려도 오류가 없어야 한다(QWidget.event 가림 방지)"""

    def test_show_widgets(self):
        a = app()
        state, conn = make_demo_state()
        tab = CalendarTab(state)
        tab.resize(420, 680)
        tab.show()
        tab.select_date(date.today() + timedelta(days=1))
        tab.set_section(1)
        tab.set_section(0)
        dialogs = [EventDialog(state, default_date=date.today()),
                   EventDialog(state, event=state.events[1]),
                   EventDetailDialog(state, state.events[1])]
        for dlg in dialogs:
            self.assertFalse(isinstance(dlg.__dict__.get('event'), dict))
            dlg.show()
        for _ in range(3):
            a.processEvents()
        for dlg in dialogs:
            dlg.close()
        tab.close()
        a.processEvents()


class EventDetailDialogTest(unittest.TestCase):
    def setUp(self):
        app()
        self.state, self.conn = make_demo_state()

    def test_detail_and_rsvp(self):
        dlg = EventDetailDialog(self.state, self.state.events[1])
        self.assertEqual(dlg.title_label.text(), '주간 회의')
        self.assertEqual(dlg.attendee_rows, {'홍길동': '참석', '김철수': '참석', '이영희': '응답 전'})
        self.assertIsNotNone(dlg.edit_btn)
        self.assertTrue(dlg.rsvp_buttons['yes'].isChecked())
        dlg.rsvp_buttons['maybe'].click()
        self.assertEqual(self.conn.last('event_rsvp'), {'event_id': 1, 'response': 'maybe'})
        # 서버 반영 → 다시 그림
        ev = dict(self.state.events[1])
        ev['attendees'] = dict(ev['attendees'], 홍길동='maybe', 이영희='no')
        self.conn.push('event_updated', event=ev)
        self.assertEqual(dlg.attendee_rows['이영희'], '불참')
        self.assertTrue(dlg.rsvp_buttons['maybe'].isChecked())
        edit = dlg.make_edit_dialog()
        self.assertEqual(edit.title_edit.text(), '주간 회의')

    def test_non_creator_has_no_edit(self):
        ev = dict(self.state.events[1], creator='김철수')
        self.state.events[1] = ev
        dlg = EventDetailDialog(self.state, ev)
        self.assertIsNone(dlg.edit_btn)
        self.assertIn('yes', dlg.rsvp_buttons)

    def test_deleted_event_closes(self):
        dlg = EventDetailDialog(self.state, self.state.events[2])
        dlg.show()
        self.conn.push('event_deleted', event_id=2)
        self.assertFalse(dlg.isVisible())


if __name__ == '__main__':
    unittest.main()


class WheelAndCreatedEventTest(unittest.TestCase):
    """휠로 날짜가 바뀌어 다른 해에 등록되고 달력에 안 보이던 문제 (2026-10-11)"""

    def setUp(self):
        app()
        from client.kakao import main_window
        self.state, self.conn = make_demo_state()
        main_window.apply_app_theme(self.state.settings)

    def wheel(self, widget):
        from PyQt5.QtCore import QPoint, QPointF
        from PyQt5.QtGui import QWheelEvent
        from PyQt5.QtWidgets import QApplication
        event = QWheelEvent(QPointF(5, 5), widget.mapToGlobal(QPoint(5, 5)), QPoint(0, 0), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QApplication.sendEvent(widget, event)

    def test_wheel_over_date_does_not_change_year(self):
        dlg = EventDialog(self.state)
        self.addCleanup(dlg.close)
        dlg.show()
        before = dlg.start_edit.dateTime()
        self.wheel(dlg.start_edit)
        self.assertEqual(dlg.start_edit.dateTime(), before)                    # 2027년이 되지 않는다
        before_index = dlg.remind_combo.currentIndex()
        self.wheel(dlg.remind_combo)                                           # 포커스 없는 콤보도 그대로
        self.assertEqual(dlg.remind_combo.currentIndex(), before_index)

    def test_created_event_moves_calendar_to_its_date(self):
        from unittest import mock
        from client.kakao import event_dialog
        tab = CalendarTab(self.state)
        self.addCleanup(tab.close)
        target = datetime(date.today().year + 1, 3, 5, 9, 0)

        def fake_exec(dlg):
            dlg.title_edit.setText('내년 일정')
            dlg.start_edit.setDateTime(_qdt(target))
            dlg.end_edit.setDateTime(_qdt(target + timedelta(hours=1)))
            dlg.accept()
            return event_dialog.QDialog.Accepted
        with mock.patch.object(event_dialog.EventDialog, 'exec_', fake_exec):
            tab.open_new_event()
        self.assertEqual((tab.year, tab.month, tab.selected), (target.year, 3, target.date()))
        self.assertEqual(self.conn.last('event_create')['start'], target.isoformat(timespec='seconds'))
