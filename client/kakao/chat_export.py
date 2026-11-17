# -*- coding: utf-8 -*-
"""
대화 내용 저장 (명세 §9.5) - 채팅방 메뉴 / 채팅 목록 우클릭 → 대화 내용 저장…
- 형식: 텍스트(.txt, 카카오톡 PC 형식) · 엑셀용 표(.csv) · 사진·파일 포함(.zip = 대화.txt + files/ 원본)
- 기간: 전체 · 오늘 · 최근 7일 · 최근 30일 · 기간 지정 (서버 room_export 의 since/until)
UI 는 소켓을 직접 만지지 않는다: 기록은 AppState.request_export, 첨부는 AppState.request_attachment 로 받는다.
"""

import re
import zipfile
from datetime import date, timedelta

from PyQt5.QtCore import QObject, QTimer, QDate, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
                             QComboBox, QDateEdit, QPushButton, QWidget)

from client.kakao import theme
from client.kakao import osenv
from client.kakao.widgets import disconnect_signals

FORMATS = (   # (키, 화면 이름, 확장자, 저장 창 필터)
    ('txt', '텍스트 (.txt) — 카카오톡 PC 와 같은 형식', 'txt', '텍스트 파일 (*.txt)'),
    ('csv', '엑셀용 표 (.csv) — 날짜·시각·보낸 사람·내용 열', 'csv', 'CSV 파일 (*.csv)'),
    ('zip', '사진·파일 포함 (.zip) — 대화 텍스트 + 첨부 원본', 'zip', '압축 파일 (*.zip)'),
)
PERIODS = (('all', '전체'), ('today', '오늘'), ('week', '최근 7일'), ('month', '최근 30일'), ('custom', '기간 지정'))
ATTACHMENT_TIMEOUT_MS = 20000   # 첨부 하나를 기다리는 최대 시간 (넘으면 '받지 못함'으로 적고 다음 것)


def format_info(fmt):
    return next(f for f in FORMATS if f[0] == fmt)


def period_range(choice, today=None, start=None, end=None):
    """기간 선택 → (since, until, 설명). 전체면 (None, None, '')"""
    today = today or date.today()
    if choice == 'today':
        since, until = today, today
    elif choice == 'week':
        since, until = today - timedelta(days=6), today
    elif choice == 'month':
        since, until = today - timedelta(days=29), today
    elif choice == 'custom' and start and end:
        since, until = min(start, end), max(start, end)
    else:
        return None, None, ''
    label = since.isoformat() if since == until else f'{since.isoformat()} ~ {until.isoformat()}'
    return since, until, label


def safe_name(text, fallback='file'):
    """파일 이름에 쓸 수 없는 문자를 _ 로 (Windows 포함)"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', text or '').strip(' .')
    return osenv.safe_filename(name[:120], fallback)      # + Windows 예약 이름(CON · NUL …) 피하기


def default_file_name(room_title, fmt, now):
    return f"KCS Talk_{safe_name(room_title, '채팅방')}_{now:%Y%m%d_%H%M%S}.{format_info(fmt)[2]}"


def attachment_messages(messages):
    """원본을 받아 담을 첨부 메시지 (삭제·비밀채팅 제외)"""
    return [m for m in messages
            if m.get('attachment') and not m.get('deleted') and m.get('kind') != 'secret']


class ExportDialog(QDialog):
    """대화 내용 저장 옵션: 형식 + 기간"""

    def __init__(self, room_title, parent=None, today=None):
        super().__init__(parent)
        self.today = today or date.today()
        self.setWindowTitle('대화 내용 저장')
        self.setStyleSheet(f"""
            QRadioButton {{ color: {theme.c('text')}; font-size: {theme.fs('body')}px; padding: 3px 0; }}
            QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px;
                border: 1px solid {theme.c('text_hint')}; background: transparent; }}
            QRadioButton::indicator:checked {{ border: 5px solid {theme.c('yellow')}; background: {theme.c('on_accent')};
                width: 6px; height: 6px; }}
        """)
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))
        title = QLabel(f"'{room_title}' 대화를 파일로 저장합니다.")
        title.setWordWrap(True)
        col.addWidget(title)

        col.addWidget(self._section('저장 형식'))
        self.format_group = QButtonGroup(self)
        self.format_buttons = {}
        for key, label, _ext, _filter in FORMATS:
            btn = QRadioButton(label)
            self.format_group.addButton(btn)
            self.format_buttons[key] = btn
            col.addWidget(btn)
        self.format_buttons['txt'].setChecked(True)

        col.addWidget(self._section('기간'))
        self.period_combo = QComboBox()
        for key, label in PERIODS:
            self.period_combo.addItem(label, key)
        col.addWidget(self.period_combo)
        self.custom_box = QWidget()
        row = QHBoxLayout(self.custom_box)
        row.setContentsMargins(0, 0, 0, 0)
        self.start_edit, self.end_edit = QDateEdit(), QDateEdit()
        for edit in (self.start_edit, self.end_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('yyyy-MM-dd')
            edit.setDate(QDate(self.today.year, self.today.month, self.today.day))
        self.start_edit.setDate(self.start_edit.date().addDays(-6))
        row.addWidget(self.start_edit, 1)
        row.addWidget(QLabel('~'))
        row.addWidget(self.end_edit, 1)
        col.addWidget(self.custom_box)
        self.custom_box.hide()
        self.period_combo.currentIndexChanged.connect(
            lambda _i: self.custom_box.setVisible(self.period_combo.currentData() == 'custom'))

        self.hint = QLabel('사진·파일 포함은 서버에서 원본을 받아 담습니다. 보관 기간이 지난 첨부는 빠지고 대화.txt 에 표시됩니다.')
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")
        col.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton('저장')
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        col.addLayout(buttons)

    @staticmethod
    def _section(text):
        label = QLabel(text)
        label.setStyleSheet(f"font-weight: bold; color: {theme.c('text_sub')}; padding-top: 6px;")
        return label

    def options(self):
        """{fmt, since, until, period}"""
        fmt = next(k for k, b in self.format_buttons.items() if b.isChecked())
        to_date = lambda edit: edit.date().toPyDate()   # noqa: E731
        since, until, label = period_range(self.period_combo.currentData(), self.today,
                                           to_date(self.start_edit), to_date(self.end_edit))
        return {'fmt': fmt, 'since': since, 'until': until, 'period': label}


class AttachmentCollector(QObject):
    """첨부 원본을 하나씩 받는다 (서버 부하·순서 보장). 끝나면 finished({msg_id: (파일 경로, 데이터)}, {msg_id: 사유})"""

    progress = pyqtSignal(int, int)        # 받은 수, 전체
    finished = pyqtSignal(dict, dict)

    def __init__(self, state, messages, parent=None, timeout_ms=None):
        super().__init__(parent)
        self.state = state
        self.queue = attachment_messages(messages)
        self.total = len(self.queue)
        self.files, self.notes = {}, {}
        self._current = None
        self._used_names = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms or ATTACHMENT_TIMEOUT_MS)
        self._timer.timeout.connect(self._on_timeout)     # 람다가 아니라 메서드: 수집기가 사라지면 연결도 사라진다

    def _on_timeout(self):
        self._skip('받지 못함(응답 없음)')

    def start(self):
        self.state.quiet_attachment_errors = True
        self.state.attachment_loaded.connect(self._on_loaded)
        self.state.attachment_failed.connect(self._on_failed)
        self._next()

    def _stop(self):
        self._timer.stop()
        disconnect_signals((self.state.attachment_loaded, self._on_loaded),
                           (self.state.attachment_failed, self._on_failed))
        self.state.quiet_attachment_errors = False

    def cancel(self):
        """채팅방 창을 닫아 저장을 그만둠: finished 없이 멈춘다. 오류 알림 숨김도 푼다 - 남아 있으면
        그 뒤로 어느 방에서도 첨부 받기 오류가 보이지 않았다"""
        self.queue = []
        self._stop()

    def _done(self):
        self._stop()
        self.finished.emit(self.files, self.notes)

    def _next(self):
        self.progress.emit(len(self.files) + len(self.notes), self.total)
        while self.queue:
            m = self.queue.pop(0)
            meta = m['attachment']
            if meta.get('expired'):
                self.notes[m['msg_id']] = '포함되지 않음: 보관 기간 만료'
                continue
            self._current = m
            self._timer.start()
            self.state.request_attachment(meta['attachment_id'])
            return
        self._current = None
        self._done()

    def _file_path(self, m, name):
        base = safe_name(name or (m['attachment'].get('name')), f"attachment_{m['msg_id']}")
        path = f"files/{m['msg_id']}_{base}"
        while path in self._used_names:
            path = f"files/{m['msg_id']}_{len(self._used_names)}_{base}"
        self._used_names.add(path)
        return path

    def _on_loaded(self, attachment_id, data, name, _mime):
        m = self._current
        if m is None or m['attachment'].get('attachment_id') != attachment_id:
            return
        self._timer.stop()
        self.files[m['msg_id']] = (self._file_path(m, name), bytes(data))
        self._current = None
        self._next()

    def _on_failed(self, reason):
        if self._current is not None:
            self._skip(f'포함되지 않음: {reason}')

    def _skip(self, reason):
        m = self._current
        if m is None:
            return
        self._timer.stop()
        self.notes[m['msg_id']] = reason
        self._current = None
        self._next()


def write_zip(path, text, csv_text, files):
    """사진·파일 포함 저장: 대화.txt + 대화.csv + files/ 원본"""
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('대화.txt', text)
        zf.writestr('대화.csv', csv_text.encode('utf-8'))
        for rel, data in files.values():
            zf.writestr(rel, data)


def file_marks(files, notes):
    """export_text/export_csv 에 넘길 {msg_id: 'files/…' 또는 사유}"""
    marks = {mid: rel for mid, (rel, _data) in files.items()}
    marks.update(notes)
    return marks


def write_export(path, fmt, state, room_id, messages, period='', files=None, notes=None):
    """형식에 맞춰 파일을 쓴다 (zip 이면 files/notes = 받아 둔 첨부)"""
    if fmt == 'csv':
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(state.export_csv(room_id, messages))
        return
    if fmt == 'zip':
        marks = file_marks(files or {}, notes or {})
        write_zip(path, state.export_text(room_id, messages, files=marks, period=period),
                  state.export_csv(room_id, messages, files=marks), files or {})
        return
    with open(path, 'w', encoding='utf-8') as f:
        f.write(state.export_text(room_id, messages, period=period))


def ensure_extension(path, fmt):
    ext = '.' + format_info(fmt)[2]
    return path if path.lower().endswith(ext) else path + ext


__all__ = ['ExportDialog', 'AttachmentCollector', 'period_range', 'write_export', 'default_file_name',
           'ensure_extension', 'format_info', 'attachment_messages', 'FORMATS', 'PERIODS']
