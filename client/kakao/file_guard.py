# -*- coding: utf-8 -*-
"""
파일 전송 보안 (명세 §10.10)

- precheck: 받은 정책으로 이름 · 확장자만 미리 본다 (올리기 전에 바로 알려 주려고).
  파일 내용(확장자를 바꾼 실행 파일 등)은 서버가 보고 file_blocked 로 알려 준다 - 최종 판단은 언제나 서버.
- FileBlockedDialog: 막힌 파일 → 사유를 적어 그룹 관리자에게 승인 요청 (승인되면 그때 그 방으로 전송)
- FileSecurityDialog: 그룹 관리자 → 정책(형태 · 확장자 · 파일 이름) / 승인 요청 처리 / 예외(특정인 · 특정 파일)
"""

import fnmatch
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QAbstractItemView, QPlainTextEdit, QCheckBox,
                             QFormLayout, QSpinBox, QTabWidget, QWidget, QGridLayout, QFileDialog, QComboBox,
                             QFrame)

from client.kakao import theme, osenv
from client.kakao.widgets import human_size, disconnect_signals, underline_tabs_qss, check_box_qss, plain

FILE_BLOCKED = '보안 정책으로 막힌 파일입니다.'
REASON_MAX = 300
STATUS_TEXT = {'pending': '대기', 'approved': '승인', 'rejected': '반려'}


def extension_of(name):
    base = os.path.basename((name or '').replace('\\', '/')).strip().rstrip('.')
    return os.path.splitext(base)[1][1:].lower()


def name_matches(name, pattern):
    """와일드카드(* ?)가 있으면 이름 전체와, 없으면 이름에 들어 있는지 (대소문자 무시) - 서버와 같은 규칙"""
    base = os.path.basename((name or '').replace('\\', '/')).lower()
    pattern = (pattern or '').strip().lower()
    if not pattern:
        return False
    if any(ch in pattern for ch in '*?['):
        return fnmatch.fnmatchcase(base, pattern)
    return pattern in base


def category_labels(policy):
    return {c['key']: c['label'] for c in (policy or {}).get('categories') or []}


def precheck(policy, name):
    """이름 · 확장자 · 확장자로 본 형태가 막혔으면 {rule, value, detail}, 아니면 None"""
    policy = policy or {}
    if not policy.get('enabled'):
        return None
    ext = extension_of(name)
    for pattern in policy.get('blocked_names') or []:
        if name_matches(name, pattern):
            return {'rule': 'name', 'value': pattern, 'detail': f"이름에 '{pattern.strip('*')}' 이(가) 들어 있어요"}
    if ext and ext in (policy.get('blocked_extensions') or []):
        return {'rule': 'extension', 'value': ext, 'detail': f'.{ext} 파일은 보낼 수 없어요'}
    blocked = set(policy.get('blocked_types') or [])
    labels = category_labels(policy)
    for cat in policy.get('categories') or []:
        if cat['key'] in blocked and ext in cat.get('extensions', []):
            return {'rule': 'type', 'value': cat['key'], 'detail': f"{labels[cat['key']]} 형태는 보낼 수 없어요"}
    return None


def _hint(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.c('text_sub')}; font-size: {theme.fs('meta')}px;")
    return label


def _buttons(*buttons):
    row = QHBoxLayout()
    row.addStretch(1)
    for btn in buttons:
        row.addWidget(btn)
    return row


class FileBlockedDialog(QDialog):
    """막힌 파일 안내 + 승인 요청. 사유를 적어야 [승인 요청]이 눌린다"""

    def __init__(self, state, info, parent=None):
        super().__init__(parent)
        from client.kakao.widgets import make_icon
        self.state = state
        self.info = dict(info)
        self.setWindowTitle('파일 전송 보안')
        self.setMinimumWidth(theme.px(380))
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))

        head = QHBoxLayout()
        head.setSpacing(theme.sp('s'))
        icon = QLabel()
        icon.setPixmap(make_icon('file', theme.c('danger_text'), 20).pixmap(20, 20))
        head.addWidget(icon, 0, Qt.AlignVCenter)
        title = QLabel('보낼 수 없는 파일이에요')
        title.setStyleSheet(f"font-size: {theme.fs('title')}px; font-weight: bold; color: {theme.c('text')};")
        head.addWidget(title, 1)
        col.addLayout(head)

        card = QFrame()
        card.setObjectName('blockedFileCard')
        card.setStyleSheet(f"#blockedFileCard {{ background: {theme.c('bg_selected')}; border-radius: {theme.px(8)}px; }}")
        card_col = QVBoxLayout(card)
        card_col.setContentsMargins(theme.sp('m'), theme.sp('s'), theme.sp('m'), theme.sp('s'))
        card_col.setSpacing(2)
        self.name_label = plain(QLabel(self.info.get('name', '')))
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(f"font-weight: bold; color: {theme.c('text')};")
        card_col.addWidget(self.name_label)
        self.detail_label = plain(QLabel(f"{self.info.get('detail', '')} · {human_size(self.info.get('size'))}"))
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {theme.c('danger_text')}; font-size: {theme.fs('meta')}px;")
        card_col.addWidget(self.detail_label)
        col.addWidget(card)

        self.can_request = state.can_request_file(self.info.get('client_tmp_id'))
        col.addWidget(_hint('외부 유출을 막기 위해 관리자가 정한 파일은 보낼 수 없어요. 꼭 필요하면 사유를 적어 '
                            '승인을 요청하세요. 관리자가 확인하고 승인하면 그때 이 대화방으로 전송돼요.'
                            if self.can_request else
                            '외부 유출을 막기 위해 관리자가 정한 파일은 보낼 수 없어요. 승인을 요청하려면 '
                            '파일을 다시 골라 보내 주세요.'))
        self.reason_edit = QPlainTextEdit()
        self.reason_edit.setPlaceholderText('요청 사유 (예: 협력사 ○○에 계약 검토용으로 전달)')
        self.reason_edit.setFixedHeight(theme.px(72))
        self.reason_edit.setVisible(self.can_request)
        col.addWidget(self.reason_edit)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {theme.c('danger_text')}; font-size: {theme.fs('meta')}px;")
        self.message.hide()
        col.addWidget(self.message)

        cancel = QPushButton('닫기' if not self.can_request else '취소')
        cancel.clicked.connect(self.reject)
        self.request_button = QPushButton('승인 요청')
        self.request_button.setProperty('primary', True)
        self.request_button.setDefault(True)
        self.request_button.setVisible(self.can_request)
        self.request_button.clicked.connect(self.submit)
        col.addLayout(_buttons(cancel, self.request_button))
        self.reason_edit.textChanged.connect(self._update)
        self._update()

    def reason(self):
        return self.reason_edit.toPlainText().strip()

    def _update(self):
        self.request_button.setEnabled(0 < len(self.reason()) <= REASON_MAX)

    def submit(self):
        err = self.state.request_file_send(self.info.get('client_tmp_id'), self.reason())
        if err:
            self.message.setText(err)
            self.message.show()
            return
        self.accept()


def show_file_blocked(state, info, parent=None):
    """비모달로 띄운다 (여러 파일이 한꺼번에 막혀도 각각)"""
    dlg = FileBlockedDialog(state, info, parent)
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    dlg.show()
    return dlg


# ================================================================== 그룹 관리자
class PolicyTab(QWidget):
    """막을 형태(체크) · 확장자(쉼표) · 파일 이름(한 줄에 하나)"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('m'), theme.sp('m'), theme.sp('m'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))
        self.enabled_check = QCheckBox('파일 전송 보안 켜기')
        col.addWidget(self.enabled_check)
        col.addWidget(_hint('형태는 확장자뿐 아니라 파일 내용으로도 판별해요. 이름만 바꾼 실행 파일, '
                            '압축 파일 안에 든 파일도 막아요.'))
        self.type_checks = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.sp('l'))
        for i, cat in enumerate(state.file_policy.get('categories') or []):
            box = QCheckBox(cat['label'])
            box.setToolTip(', '.join('.' + e for e in cat.get('extensions', [])))
            self.type_checks[cat['key']] = box
            grid.addWidget(box, i // 2, i % 2)
        col.addLayout(grid)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText('예: hwp, dwg, zip')
        form.addRow('막을 확장자', self.ext_edit)
        self.names_edit = QPlainTextEdit()
        self.names_edit.setPlaceholderText('한 줄에 하나 (예: *대외비*, 고객명단*)')
        self.names_edit.setFixedHeight(theme.px(84))
        form.addRow('막을 파일 이름', self.names_edit)
        col.addLayout(form)
        col.addWidget(_hint('파일 이름에 * 를 쓰면 이름 전체와 맞춰 보고, 없으면 이름에 들어 있으면 막아요. '
                            '대소문자는 가리지 않아요.'))
        col.addStretch(1)
        self.save_button = QPushButton('정책 저장')
        self.save_button.setProperty('primary', True)
        self.save_button.clicked.connect(self.save)
        col.addLayout(_buttons(self.save_button))
        self.enabled_check.toggled.connect(self._update)
        self.load()

    def load(self):
        policy = self.state.file_policy or {}
        self.enabled_check.setChecked(bool(policy.get('enabled')))
        blocked = set(policy.get('blocked_types') or [])
        for key, box in self.type_checks.items():
            box.setChecked(key in blocked)
        self.ext_edit.setText(', '.join(policy.get('blocked_extensions') or []))
        self.names_edit.setPlainText('\n'.join(policy.get('blocked_names') or []))
        self._update()

    def _update(self):
        on = self.enabled_check.isChecked()
        for widget in (*self.type_checks.values(), self.ext_edit, self.names_edit):
            widget.setEnabled(on)

    def policy(self):
        exts = [e.strip().lstrip('.').lower() for e in self.ext_edit.text().replace(';', ',').split(',')]
        names = [n.strip() for n in self.names_edit.toPlainText().splitlines()]
        return {'enabled': self.enabled_check.isChecked(),
                'blocked_types': [k for k, box in self.type_checks.items() if box.isChecked()],
                'blocked_extensions': [e for e in exts if e], 'blocked_names': [n for n in names if n]}

    def save(self):
        self.state.admin_file_policy_set(self.policy())


class RequestsTab(QWidget):
    """승인 요청: 표 + 선택한 요청의 사유 · [파일 받아 확인] [반려] [승인]"""
    COLUMNS = ('상태', '요청한 사람', '파일', '막힌 이유', '보낼 곳', '요청 시각')

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.requests = []
        self._saving = {}          # attachment_id → 저장할 경로
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('m'), theme.sp('m'), theme.sp('m'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))
        top = QHBoxLayout()
        self.pending_only = QCheckBox('대기 중인 요청만')
        self.pending_only.setChecked(True)
        top.addWidget(self.pending_only)
        top.addStretch(1)
        self.refresh_button = QPushButton('새로고침')
        top.addWidget(self.refresh_button)
        col.addLayout(top)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)      # 파일 이름 · 막힌 이유가 남는 폭을 나눠 쓴다
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setTextElideMode(Qt.ElideMiddle)
        col.addWidget(self.table, 1)
        self.reason_label = plain(QLabel(''))
        self.reason_label.setWordWrap(True)
        self.reason_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(self.reason_label)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText('요청한 사람에게 보일 메모 (선택)')
        form.addRow('메모', self.note_edit)
        self.allow_days = QSpinBox()
        self.allow_days.setRange(0, 365)
        self.allow_days.setSuffix('일')
        self.allow_days.setSpecialValueText('이번 한 번만')
        form.addRow('같은 파일 다시 허용', self.allow_days)
        col.addLayout(form)
        self.download_button = QPushButton('파일 받아 확인…')
        self.reject_button = QPushButton('반려')
        self.approve_button = QPushButton('승인하고 보내기')
        self.approve_button.setProperty('primary', True)
        row = QHBoxLayout()
        row.addWidget(self.download_button)
        row.addStretch(1)
        row.addWidget(self.reject_button)
        row.addWidget(self.approve_button)
        col.addLayout(row)

        self.pending_only.toggled.connect(lambda _on: self._fill())
        self.refresh_button.clicked.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._update)
        self.download_button.clicked.connect(lambda: self.download())
        self.reject_button.clicked.connect(lambda: self.decide(False))
        self.approve_button.clicked.connect(lambda: self.decide(True))
        state.admin_file_requests_changed.connect(self._on_requests)
        state.admin_file_request_new.connect(self._on_request_new)
        state.attachment_loaded.connect(self._on_attachment)
        self._update()

    def disconnect_state(self):
        disconnect_signals((self.state.admin_file_requests_changed, self._on_requests),
                           (self.state.admin_file_request_new, self._on_request_new),
                           (self.state.attachment_loaded, self._on_attachment))

    def _on_request_new(self, _request):
        self.refresh()

    def refresh(self):
        self.state.admin_request_file_requests()

    def _on_requests(self, requests):
        self.requests = list(requests)
        self._fill()

    def visible(self):
        return [r for r in self.requests if not self.pending_only.isChecked() or r.get('status') == 'pending']

    def _fill(self):
        keep = self.selected()
        rows = self.visible()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            values = (STATUS_TEXT.get(r.get('status'), r.get('status', '')), r.get('username', ''),
                      f"{r.get('name', '')} ({human_size(r.get('size'))})",
                      r.get('detail', ''), r.get('room_label', ''),
                      (r.get('created_at') or '').replace('T', ' ')[:16])
            for j, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, r['request_id'])
                item.setToolTip(text)
                if r.get('status') != 'pending':
                    item.setForeground(theme.qc('text_hint'))
                self.table.setItem(i, j, item)
            if keep and r['request_id'] == keep['request_id']:
                self.table.selectRow(i)
        self._update()

    def selected(self):
        items = self.table.selectedItems()
        if not items:
            return None
        rid = items[0].data(Qt.UserRole)
        return next((r for r in self.requests if r['request_id'] == rid), None)

    def select(self, request_id):
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.UserRole) == request_id:
                self.table.selectRow(i)
                return True
        return False

    def _update(self):
        r = self.selected()
        pending = r is not None and r.get('status') == 'pending'
        for widget in (self.reject_button, self.approve_button, self.note_edit, self.allow_days):
            widget.setEnabled(pending)
        self.download_button.setEnabled(pending)
        if r is None:
            self.reason_label.setText('요청을 고르면 사유가 보여요.')
            return
        text = f"사유: {r.get('reason', '')}"
        if r.get('status') != 'pending':
            text += f"\n{STATUS_TEXT.get(r['status'])} · {r.get('decided_by') or ''}" \
                    f"{' · ' + r['note'] if r.get('note') else ''}"
        self.reason_label.setText(text)

    def decide(self, approve):
        r = self.selected()
        if r is None or r.get('status') != 'pending':
            return
        self.state.admin_decide_file_request(r['request_id'], approve, self.note_edit.text().strip(),
                                             self.allow_days.value() if approve else 0)
        self.note_edit.clear()
        self.allow_days.setValue(0)

    def download(self, path=None):
        """보관 중인 파일을 받아 저장 (내용 확인용)"""
        r = self.selected()
        if r is None:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, '요청 파일 저장',
                                                  os.path.join(osenv.user_dir('downloads'), osenv.safe_filename(r.get('name'))))
            if not path:
                return None
        self._saving[r['attachment_id']] = path
        self.state.request_attachment(r['attachment_id'])
        return path

    def _on_attachment(self, attachment_id, data, _name, _mime):
        path = self._saving.pop(attachment_id, None)
        if not path:
            return
        try:
            with open(path, 'wb') as f:
                f.write(data)
        except OSError as e:
            self.reason_label.setText(f'파일을 저장하지 못했어요: {e}')
            return
        osenv.mark_from_network(path)    # 막혔던 파일 - Windows 에서 SmartScreen · Office 제한된 보기가 보게
        self.reason_label.setText(f'저장했어요: {path}')


class ExceptionDialog(QDialog):
    """예외 추가: 사람 + 파일 이름(패턴) + 기간"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle('예외 추가')
        self.setMinimumWidth(theme.px(360))
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.user_combo = QComboBox()
        self.user_combo.setEditable(True)
        people = sorted(u['username'] for u in state.users.values()
                        if not u.get('is_bot') and not u.get('removed'))
        self.user_combo.addItems(people)
        self.user_combo.setCurrentText('')
        form.addRow('사람', self.user_combo)
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText('예: *.zip, 설계도_v3.dwg')
        form.addRow('파일 이름', self.pattern_edit)
        self.days = QSpinBox()
        self.days.setRange(1, 365)
        self.days.setValue(7)
        self.days.setSuffix('일')
        form.addRow('허용 기간', self.days)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText('메모 (선택)')
        form.addRow('메모', self.note_edit)
        col.addLayout(form)
        col.addWidget(_hint('이 사람만, 이름이 맞는 파일만, 기간 동안 막지 않아요.'))
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton('추가')
        self.add_button.setProperty('primary', True)
        self.add_button.clicked.connect(self.accept)
        col.addLayout(_buttons(cancel, self.add_button))
        self.user_combo.currentTextChanged.connect(self._update)
        self.pattern_edit.textChanged.connect(self._update)
        self._update()

    def values(self):
        return {'username': self.user_combo.currentText().strip(), 'pattern': self.pattern_edit.text().strip(),
                'days': self.days.value(), 'note': self.note_edit.text().strip()}

    def _update(self):
        v = self.values()
        self.add_button.setEnabled(v['username'] in self.state.users and bool(v['pattern']))


class ExceptionsTab(QWidget):
    COLUMNS = ('사람', '허용한 파일', '만료', '메모', '등록')

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.exceptions = []
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('m'), theme.sp('m'), theme.sp('m'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))
        col.addWidget(_hint('특정인에게 특정 파일을 기간을 정해 풀어 줘요. 승인할 때 \'같은 파일 다시 허용\' 을 '
                            '고르면 여기에 그 파일(지문)이 추가돼요.'))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        col.addWidget(self.table, 1)
        self.add_button = QPushButton('예외 추가…')
        self.delete_button = QPushButton('삭제')
        row = QHBoxLayout()
        row.addWidget(self.add_button)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        col.addLayout(row)
        self.add_button.clicked.connect(lambda: self.add())
        self.delete_button.clicked.connect(self.delete)
        self.table.itemSelectionChanged.connect(self._update)
        state.admin_file_exceptions_changed.connect(self._on_exceptions)
        self._update()

    def disconnect_state(self):
        disconnect_signals((self.state.admin_file_exceptions_changed, self._on_exceptions))

    def refresh(self):
        self.state.admin_request_file_exceptions()

    def _on_exceptions(self, exceptions):
        self.exceptions = list(exceptions)
        self.table.setRowCount(len(self.exceptions))
        for i, e in enumerate(self.exceptions):
            target = e.get('pattern') or f"{e.get('file_name') or '파일'} (이 파일만)"
            values = (e.get('username', ''), target, (e.get('expires_at') or '').replace('T', ' ')[:16],
                      e.get('note', ''), e.get('created_by', ''))
            for j, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, e['exception_id'])
                self.table.setItem(i, j, item)
        self._update()

    def _update(self):
        self.delete_button.setEnabled(bool(self.table.selectedItems()))

    def add(self, exec_=True):
        dlg = ExceptionDialog(self.state, self)
        self.exception_dialog = dlg
        if exec_ and dlg.exec_() == QDialog.Accepted:
            v = dlg.values()
            self.state.admin_add_file_exception(v['username'], v['days'], pattern=v['pattern'], note=v['note'])
        return dlg

    def delete(self):
        items = self.table.selectedItems()
        if items:
            self.state.admin_delete_file_exception(items[0].data(Qt.UserRole))


def file_security_qss():
    """밑줄 탭 · 테마 색 체크박스 (macOS 기본 회색 탭 판을 쓰지 않는다) + 정책을 끈 동안 흐린 체크박스"""
    c = theme.c
    return underline_tabs_qss() + check_box_qss() + f"""
        QCheckBox {{ spacing: 8px; }}
        QCheckBox:disabled {{ color: {c('text_hint')}; }}
        QCheckBox::indicator:checked:disabled {{ background: {c('bg_selected')}; border-color: {c('bg_selected')}; }}
    """


class FileSecurityDialog(QDialog):
    """그룹 관리자 · 파일 전송 보안: [정책] [승인 요청] [예외]"""

    DONE_TEXT = {'file_policy': '정책을 저장했어요. 모든 사용자에게 바로 적용돼요.',
                 'file_exception_add': '예외를 추가했어요.',
                 'file_exception_delete': '예외를 삭제했어요.'}

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle('관리자 · 파일 전송 보안')
        self.setMinimumWidth(theme.px(620))
        self.setStyleSheet(file_security_qss())
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))
        self.tabs = QTabWidget()
        self.policy_tab = PolicyTab(state)
        self.requests_tab = RequestsTab(state)
        self.exceptions_tab = ExceptionsTab(state)
        self.tabs.addTab(self.policy_tab, '정책')
        self.tabs.addTab(self.requests_tab, '승인 요청')
        self.tabs.addTab(self.exceptions_tab, '예외')
        col.addWidget(self.tabs, 1)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        col.addWidget(self.status)
        state.admin_done.connect(self._on_done)
        state.error.connect(self._on_error)
        state.file_policy_changed.connect(self.policy_tab.load)
        state.admin_file_requests_changed.connect(self._update_tab_titles)
        self.resize(theme.px(680), theme.px(520))
        self.requests_tab.refresh()
        self.exceptions_tab.refresh()

    def done(self, result):
        disconnect_signals((self.state.admin_done, self._on_done), (self.state.error, self._on_error),
                           (self.state.file_policy_changed, self.policy_tab.load),
                           (self.state.admin_file_requests_changed, self._update_tab_titles))
        self.requests_tab.disconnect_state()
        self.exceptions_tab.disconnect_state()
        super().done(result)

    def _update_tab_titles(self, requests):
        pending = sum(1 for r in requests if r.get('status') == 'pending')
        self.tabs.setTabText(1, f'승인 요청 {pending}' if pending else '승인 요청')

    def _on_done(self, p):
        action = p.get('action')
        if action == 'file_request_decide':
            self.show_status(f"{p.get('username', '')} 님의 요청을 "
                             + ('승인했어요. 파일을 그 대화방으로 보냈어요.' if p.get('approved') else '반려했어요.'))
            self.requests_tab.refresh()
            self.exceptions_tab.refresh()
        elif action in self.DONE_TEXT:
            self.show_status(self.DONE_TEXT[action])
            if action.startswith('file_exception'):
                self.exceptions_tab.refresh()

    def _on_error(self, message):
        if self.isVisible():
            self.show_status(message, error=True)

    def show_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.c('danger_text') if error else theme.c('text_sub')};")
