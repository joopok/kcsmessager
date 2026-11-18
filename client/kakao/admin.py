# -*- coding: utf-8 -*-
"""
관리자 권한 (명세 §4.2 · §4.3) - 더보기 → 관리자 → 계정 관리 / 전체 공지
- 그룹 관리자(모든 계열사) / 회사 관리자(자기 회사 구성원만): 구성원 등록 · 조회 · 수정 · 삭제 · 복원
서버가 모든 관리자 요청에서 권한·범위를 다시 검사한다. 이 창은 AppState 액션만 부르고 시그널로 결과를 받는다.
"""

import html

from PyQt5.QtCore import Qt, QObject, QTimer
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QPlainTextEdit,
                             QInputDialog, QCheckBox, QComboBox, QFormLayout)

from client.kakao import theme
from client.kakao.widgets import exec_and_release
from client.kakao.widgets import disconnect_signals

NOTICE_MAX = 1000
COLUMNS = ('이름', '소속 · 부서', '사번', '이메일', '상태')


GROUP_SCOPE = '그룹 전체'
MEMBER_FIELDS = (   # (키, 라벨, 최대 길이)
    ('department', '부서', 60), ('team', '팀', 60), ('title', '직급 · 직책', 60), ('employee_no', '사번', 20),
    ('account', '사내 계정', 60), ('extension', '내선', 60), ('mobile', '휴대폰', 60), ('email', '이메일', 120))


def company_names(state):
    """등록 · 수정에서 고를 회사: 지주사 → 계열사 → 사용자 소속 (서버 company_names 와 같은 순서)"""
    org = state.org or {}
    names = [(org.get('holding') or {}).get('name') or '']
    names += [c.get('name') or '' for c in org.get('companies') or []]
    names += sorted({u.get('company') or '' for u in state.users.values() if not u.get('is_bot')})
    out = []
    for name in names:
        name = name.strip()
        if name and name != '게스트' and name not in out:
            out.append(name)
    return out


def status_text(u):
    """'관리자 · 사용 중 · 접속 중' 처럼 한 칸에"""
    if u.get('removed'):
        return '삭제됨'
    parts = []
    if u.get('is_admin'):
        parts.append(f"회사 관리자({u['admin_company']})" if u.get('admin_company') else '그룹 관리자')
    if u.get('disabled'):
        parts.append('사용 중지')
    elif u.get('registered'):
        parts.append('사용 중')
    else:
        parts.append('미등록')
    if u.get('online'):
        parts.append('접속 중')
    return ' · '.join(parts)


def matches(u, query):
    query = query.strip().lower()
    if not query:
        return True
    fields = (u.get('username'), u.get('display_name'), u.get('company'), u.get('department'),
              u.get('employee_no'), u.get('email'), u.get('account'))
    return any(query in (f or '').lower() for f in fields)


class AdminDialog(QDialog):
    """계정 관리: 검색 · 표 · 선택한 사람에게 비밀번호 재설정 / 등록 초기화 / 사용 중지 / 관리자 지정, 전체 공지"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.users = []
        self.setWindowTitle(self.title_text())
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        col.setSpacing(theme.sp('s'))

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('이름 · 소속 · 사번 · 이메일 검색')
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search, 1)
        self.show_removed = QCheckBox('삭제한 구성원 보기')
        top.addWidget(self.show_removed)
        self.refresh_button = QPushButton('새로고침')
        top.addWidget(self.refresh_button)
        col.addLayout(top)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(theme.sp('xxl') * 10)
        col.addWidget(self.table, 1)

        self.count_label = QLabel('')
        self.count_label.setStyleSheet(f"color: {theme.c('text_hint')}; font-size: {theme.fs('meta')}px;")
        col.addWidget(self.count_label)

        members = QHBoxLayout()
        self.create_button = QPushButton('구성원 등록…')
        self.edit_button = QPushButton('조회 · 수정…')
        self.delete_button = QPushButton('삭제')
        for btn in (self.create_button, self.edit_button, self.delete_button):
            members.addWidget(btn)
        members.addStretch(1)
        col.addLayout(members)

        actions = QHBoxLayout()
        self.reset_button = QPushButton('비밀번호 재설정…')
        self.code_button = QPushButton('재설정 코드 발급')
        self.code_button.setToolTip('본인 확인 후 코드를 알려 주면, 본인이 로그인 창에서 직접 비밀번호를 정합니다 '
                                    '(등록한 사람: [비밀번호 재설정] · 아직 등록하지 않은 사람: [처음 사용 등록])')
        self.clear_button = QPushButton('등록 초기화')
        self.disable_button = QPushButton('사용 중지')
        self.admin_button = QPushButton('관리자 지정')
        for btn in (self.reset_button, self.code_button, self.clear_button, self.disable_button, self.admin_button):
            actions.addWidget(btn)
        actions.addStretch(1)
        self.file_security_button = QPushButton('파일 전송 보안…')   # 그룹 관리자만 (§10.10)
        actions.addWidget(self.file_security_button)
        self.notice_button = QPushButton('전체 공지…')
        actions.addWidget(self.notice_button)
        col.addLayout(actions)

        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)   # 재설정 코드를 복사할 수 있게
        col.addWidget(self.status)

        self.search.textChanged.connect(lambda _t: self._fill())
        self.show_removed.toggled.connect(lambda _on: self._fill())
        self.create_button.clicked.connect(lambda: self.create_member())
        self.edit_button.clicked.connect(lambda: self.edit_member())
        self.delete_button.clicked.connect(lambda: self.toggle_removed())
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_member())
        self.refresh_button.clicked.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.reset_button.clicked.connect(lambda: self.reset_password())
        self.code_button.clicked.connect(lambda: self.issue_reset_code())
        self.clear_button.clicked.connect(lambda: self.clear_registration())
        self.disable_button.clicked.connect(lambda: self.toggle_disabled())
        self.admin_button.clicked.connect(lambda: self.toggle_admin())
        self.notice_button.clicked.connect(lambda: self.send_notice() and None)
        self.file_security_button.clicked.connect(lambda: self.open_file_security() and None)
        state.admin_users_changed.connect(self._on_users)
        state.admin_done.connect(self._on_done)
        state.error.connect(self._on_error)
        self._update_buttons()
        self.resize(760, 520)
        self.refresh()

    def done(self, result):
        disconnect_signals((self.state.admin_users_changed, self._on_users),
                           (self.state.admin_done, self._on_done), (self.state.error, self._on_error))
        super().done(result)

    def title_text(self):
        return f"관리자 · 계정 관리 ({self.state.admin_company() or GROUP_SCOPE})"

    def is_group_admin(self):
        return not self.state.admin_company()

    # ---- 목록 ----
    def refresh(self):
        self.state.admin_request_users()

    def _on_users(self, users):
        self.users = list(users)
        self._fill()

    def visible_users(self):
        return [u for u in self.users if matches(u, self.search.text())
                and (self.show_removed.isChecked() or not u.get('removed'))]

    def _fill(self):
        keep = self.selected_username()
        rows = self.visible_users()
        self.table.setRowCount(len(rows))
        for i, u in enumerate(rows):
            org = ' · '.join(x for x in (u.get('company'), u.get('department'), u.get('title')) if x)
            for j, text in enumerate((u['username'], org, u.get('employee_no') or '-', u.get('email') or '-',
                                      status_text(u))):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, u['username'])
                if u.get('disabled') or u.get('removed'):
                    item.setForeground(theme.qc('text_hint'))
                self.table.setItem(i, j, item)
            if u['username'] == keep:
                self.table.selectRow(i)
        active = [u for u in self.users if not u.get('removed')]
        registered = sum(1 for u in active if u.get('registered'))
        removed = len(self.users) - len(active)
        self.count_label.setText(f'전체 {len(active)}명 · 등록 {registered}명 · 표시 {len(rows)}명'
                                 + (f' · 삭제 {removed}명' if removed else ''))
        self.setWindowTitle(self.title_text())
        self._update_buttons()

    def selected_username(self):
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def selected_user(self):
        name = self.selected_username()
        return next((u for u in self.users if u['username'] == name), None)

    def select(self, username):
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.UserRole) == username:
                self.table.selectRow(i)
                return True
        return False

    def _update_buttons(self):
        u = self.selected_user()
        group = self.is_group_admin()
        self.notice_button.setVisible(group)                 # 전체 공지 · 관리자 지정은 그룹 관리자만 (§4.3)
        self.file_security_button.setVisible(group)          # 파일 전송 보안도 그룹 관리자만 (§10.10)
        self.admin_button.setVisible(group)
        for btn in (self.reset_button, self.code_button, self.clear_button, self.disable_button, self.admin_button,
                    self.edit_button, self.delete_button):
            btn.setEnabled(u is not None)
        if u is None:
            self.delete_button.setText('삭제')
            self.code_button.setText('재설정 코드 발급')
            return
        self.delete_button.setText('복원' if u.get('removed') else '삭제')
        self.delete_button.setEnabled(bool(u.get('removed')) or u['username'] != self.state.me)
        if u.get('removed'):          # 삭제된 사람은 복원 · 조회만
            for btn in (self.reset_button, self.code_button, self.clear_button, self.disable_button, self.admin_button):
                btn.setEnabled(False)
            return
        # 코드 발급: 등록한 사람에게는 비밀번호 재설정 코드, 아직 등록하지 않은 사람에게는 등록 코드 (관리자 계정은 제외)
        self.code_button.setEnabled(not u.get('is_admin'))
        self.code_button.setText('재설정 코드 발급' if u.get('registered') else '등록 코드 발급')
        self.disable_button.setText('사용 중지 해제' if u.get('disabled') else '사용 중지')
        self.disable_button.setEnabled(bool(u.get('disabled')) or u['username'] != self.state.me)
        self.admin_button.setText('관리자 해제' if u.get('is_admin') else '관리자 지정')
        self.clear_button.setEnabled(bool(u.get('registered')))

    # ---- 동작 (확인 창은 테스트에서 바꿔 끼울 수 있게 메서드로) ----
    def confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def ask_new_password(self, username):
        """새 비밀번호를 두 번 받는다 → 문자열 또는 None"""
        first, ok = QInputDialog.getText(self, '비밀번호 재설정', f'{username} 님의 새 비밀번호 (8~64자)',
                                         QLineEdit.Password)
        if not ok:
            return None
        second, ok = QInputDialog.getText(self, '비밀번호 재설정', '한 번 더 입력', QLineEdit.Password)
        if not ok:
            return None
        if first != second:
            self.show_status('두 비밀번호가 다릅니다.', error=True)
            return None
        if not 8 <= len(first) <= 64:
            self.show_status('비밀번호는 8~64자로 입력해 주세요.', error=True)
            return None
        return first

    def reset_password(self, password=None):
        u = self.selected_user()
        if u is None:
            return
        password = password if password is not None else self.ask_new_password(u['username'])
        if password:
            self.state.admin_reset_password(u['username'], password)

    def issue_reset_code(self):
        u = self.selected_user()
        if u is not None:
            self.state.admin_reset_code(u['username'])

    def clear_registration(self):
        u = self.selected_user()
        if u is None or not self.confirm('등록 초기화', f"{u['username']} 님의 비밀번호를 지웁니다.\n"
                                                    '그 사람은 로그인 창의 [처음 사용 등록]을 다시 해야 합니다.'):
            return
        self.state.admin_clear_password(u['username'])

    def toggle_disabled(self):
        u = self.selected_user()
        if u is None:
            return
        disable = not u.get('disabled')
        text = (f"{u['username']} 님의 계정을 사용 중지합니다.\n로그인할 수 없고, 접속 중이면 바로 끊깁니다."
                if disable else f"{u['username']} 님의 사용 중지를 해제합니다.")
        if self.confirm('사용 중지' if disable else '사용 중지 해제', text):
            self.state.admin_set_disabled(u['username'], disable)

    def ask_admin_scope(self, username):
        """관리자 범위 고르기 → '' (그룹 전체) / 회사 이름 / None(취소)"""
        choices = [GROUP_SCOPE] + company_names(self.state)
        current = next((i for i, c in enumerate(choices) if c == self.selected_user().get('company')), 0)
        choice, ok = QInputDialog.getItem(self, '관리자 지정', f'{username} 님의 관리 범위',
                                          choices, current, False)
        if not ok:
            return None
        return '' if choice == GROUP_SCOPE else choice

    def toggle_admin(self):
        u = self.selected_user()
        if u is None:
            return
        if u.get('is_admin'):
            if self.confirm('관리자 해제', f"{u['username']} 님을 관리자에서 해제합니다."):
                self.state.admin_set_admin(u['username'], False)
            return
        scope = self.ask_admin_scope(u['username'])
        if scope is not None:
            self.state.admin_set_admin(u['username'], True, scope)

    # ---- 구성원 등록 · 조회 · 수정 · 삭제 (§4.3) ----
    def create_member(self, exec_=True):
        dlg = MemberDialog(self.state, None, self)
        self.member_dialog = dlg
        if exec_ and dlg.exec_() == MemberDialog.Accepted:
            self.state.admin_member_create(dlg.username(), dlg.fields())
        return dlg

    def edit_member(self, exec_=True):
        u = self.selected_user()
        if u is None:
            return None
        dlg = MemberDialog(self.state, u, self)
        self.member_dialog = dlg
        if exec_ and dlg.exec_() == MemberDialog.Accepted:
            changes = dlg.changes()
            if changes:
                self.state.admin_member_update(u['username'], changes)
        return dlg

    def toggle_removed(self):
        u = self.selected_user()
        if u is None:
            return
        if u.get('removed'):
            if self.confirm('구성원 복원', f"{u['username']} 님을 복원합니다. 다시 로그인하고 조직도에 나옵니다.\n"
                                           '관리자였던 사람도 일반 구성원으로 돌아옵니다 - 관리자 권한은 다시 지정해야 합니다.'):
                self.state.admin_member_restore(u['username'])
            return
        if self.confirm('구성원 삭제', f"{u['username']} 님을 삭제합니다.\n조직도·친구 목록에서 빠지고 로그인할 수 없습니다. "
                                     '주고받은 대화 기록은 남고, [삭제한 구성원 보기]에서 복원할 수 있습니다.'):
            self.state.admin_member_delete(u['username'])

    def open_file_security(self, exec_=True):
        from client.kakao.file_guard import FileSecurityDialog
        dlg = FileSecurityDialog(self.state, self)
        self.file_security_dialog = dlg
        if exec_:
            exec_and_release(dlg)
        return dlg

    def send_notice(self, exec_=True):
        dlg = NoticeDialog(self)
        self.notice_dialog = dlg
        if exec_ and dlg.exec_() == NoticeDialog.Accepted:
            self.state.admin_send_notice(dlg.content())
        return dlg

    # ---- 결과 ----
    DONE_TEXT = {'reset_password': '{username} 님의 비밀번호를 바꿨습니다. 저장된 자동 로그인은 해제되었습니다.',
                 'clear_password': '{username} 님의 등록을 초기화했습니다. 처음 사용 등록부터 다시 하면 됩니다 '
                                   '(등록이 닫힌 서버에서는 [등록 코드 발급]으로 코드를 만들어 알려 주세요).',
                 'notice': '전체 공지를 {delivered}명에게 보냈습니다.'}

    MEMBER_DONE = {'member_create': '{username} 님을 등록했습니다. 본인이 [처음 사용 등록]으로 비밀번호(8자 이상)를 정하면 로그인할 수 '
                                    '있습니다. 등록이 닫힌 서버에서는 [등록 코드 발급]으로 코드를 만들어 알려 주세요.',
                   'member_update': '{username} 님의 정보를 고쳤습니다.',
                   'member_delete': '{username} 님을 삭제했습니다. [삭제한 구성원 보기]에서 복원할 수 있습니다.',
                   'member_restore': '{username} 님을 복원했습니다.'}

    def _on_done(self, p):
        action = p.get('action')
        if action in self.MEMBER_DONE:
            text = self.MEMBER_DONE[action].format(username=p.get('username', ''))
        elif action == 'set_admin' and p.get('is_admin'):
            text = f"{p.get('username')} 님을 {'회사 관리자(' + p['company'] + ')' if p.get('company') else '그룹 관리자'}로 지정했습니다."
        elif action == 'reset_code':
            until = (p.get('expires_at') or '').replace('T', ' ')[11:16]
            if p.get('registered') is False:       # 아직 등록하지 않은 사람: 등록 코드 (예전 서버는 이 값을 주지 않는다)
                text = (f"{p.get('username')} 님의 등록 코드: {p.get('code')} ({until}까지 · 한 번만). "
                        '본인 확인 후 알려 주세요. 로그인 창의 [처음 사용 등록](또는 [비밀번호 재설정])에서 넣으면 됩니다.')
            else:
                text = (f"{p.get('username')} 님의 재설정 코드: {p.get('code')} ({until}까지 · 한 번만). "
                        '본인 확인 후 알려 주세요. 로그인 창의 [비밀번호 재설정]에서 이메일과 함께 넣으면 됩니다.')
        elif action == 'set_disabled':
            text = f"{p.get('username')} 님을 {'사용 중지했습니다' if p.get('disabled') else '다시 사용하게 했습니다'}."
        elif action == 'set_admin':
            text = f"{p.get('username')} 님을 관리자{'로 지정했습니다' if p.get('is_admin') else '에서 해제했습니다'}."
        else:
            text = self.DONE_TEXT.get(action, '처리했습니다.').format(**{'username': '', 'delivered': 0, **p})
        self.show_status(text)
        self.refresh()

    def _on_error(self, message):
        if self.isVisible():
            self.show_status(message, error=True)

    def show_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.c('badge') if error else theme.c('text_sub')};")


class MemberDialog(QDialog):
    """구성원 등록(user=None) / 조회 · 수정. 이름은 등록 때만 정한다. 회사 관리자는 회사가 자기 회사로 고정"""

    def __init__(self, state, user=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.user = user or {}
        creating = user is None
        self.setWindowTitle('구성원 등록' if creating else f"구성원 정보 · {user['username']}")
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        form = QFormLayout()
        self.name_edit = QLineEdit(self.user.get('username', ''))
        self.name_edit.setMaxLength(20)
        self.name_edit.setReadOnly(not creating)
        self.name_edit.setPlaceholderText('이름 (등록 후 바꿀 수 없음)')
        form.addRow('이름', self.name_edit)
        self.company_combo = QComboBox()
        scope = state.admin_company()
        names = [scope] if scope else company_names(state)
        if self.user.get('company') and self.user['company'] not in names:
            names.append(self.user['company'])
        for name in names:
            self.company_combo.addItem(name)
        self.company_combo.setCurrentIndex(max(0, self.company_combo.findText(self.user.get('company') or scope)))
        self.company_combo.setEnabled(not scope)
        form.addRow('소속 회사', self.company_combo)
        self.edits = {}
        if 'team' not in self.user and len(self.user.get('org_path') or []) > 1:
            self.user = dict(self.user, team=self.user['org_path'][1])    # 조직도 사용자 정보에서 연 경우
        for key, label, limit in MEMBER_FIELDS:
            edit = QLineEdit(self.user.get(key) or '')
            edit.setMaxLength(limit)
            self.edits[key] = edit
            form.addRow(label, edit)
        col.addLayout(form)
        if not creating:
            info = QLabel(f"상태: {status_text(self.user)}")
            info.setStyleSheet(f"color: {theme.c('text_sub')};")
            col.addWidget(info)
        self.error = QLabel('')
        self.error.setStyleSheet(f"color: {theme.c('badge')};")
        self.error.setWordWrap(True)
        col.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton('등록' if creating else '저장')
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.save_button)
        col.addLayout(row)
        self.resize(400, 0)

    def username(self):
        return self.name_edit.text().strip()

    def fields(self):
        out = {'company': self.company_combo.currentText().strip()}
        out.update({k: e.text().strip() for k, e in self.edits.items()})
        return out

    def changes(self):
        """수정: 바뀐 필드만. 부서나 팀이 바뀌면 조직 경로를 [부서, 팀]으로 다시 정하도록 둘 다 보낸다"""
        fields = self.fields()
        out = {k: v for k, v in fields.items() if v != (self.user.get(k) or '')}
        if 'department' in out or 'team' in out:
            out.update(department=fields['department'], team=fields['team'])
        return out

    def validate(self):
        import re
        name = self.username()
        if not name or '@' in name:
            return '이름을 입력해 주세요.' if not name else '이름에는 @ 를 쓸 수 없습니다.'
        if not self.company_combo.currentText().strip():
            return '소속 회사를 선택해 주세요.'
        email = self.edits['email'].text().strip()
        if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            return '이메일 주소 형식이 올바르지 않습니다.'
        number = self.edits['employee_no'].text().strip()
        if number and not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z_-]{0,19}', number):
            return '사번은 영문·숫자 20자 이내로 입력해 주세요.'
        return ''

    def accept(self):
        error = self.validate()
        self.error.setText(error)
        if not error:
            super().accept()


    def fill_from(self, users):
        """관리자 목록(admin_users)이 늦게 오면 사용자가 아직 손대지 않은 칸만 채운다 (사번 · 이메일 등)"""
        record = next((u for u in users if u.get('username') == self.user.get('username')), None)
        if not record:
            return
        for key, edit in self.edits.items():
            old = self.user.get(key) or ''
            if edit.text() == old and (record.get(key) or '') != old:
                edit.setText(record.get(key) or '')
        if self.company_combo.currentText() == (self.user.get('company') or '') and record.get('company'):
            index = self.company_combo.findText(record['company'])
            if index >= 0:
                self.company_combo.setCurrentIndex(index)
        self.user = dict(self.user, **{k: record.get(k) or '' for k in ('company', *self.edits)})


# ---------------------------------------------------------------- 편집 모드 (조직도 · 친구 · 프로필에서 바로 수정)
EDIT_ACTION_TEXT = '정보 수정 (편집 모드)…'


def can_edit_member(state, username):
    """관리자이고 대상이 관리 범위 안의 사람(봇 제외)이면 True. 서버도 다시 검사한다 (명세 §4.3)"""
    if not state.is_admin() or not username:
        return False
    user = state.users.get(username) or (state.user(username) if username == state.me else None)
    if not user or user.get('is_bot'):
        return False
    scope = state.admin_company()
    return not scope or (user.get('company') or '') == scope


def member_record(state, username):
    """구성원 창에 채울 값: 사용자 정보 + (받아 둔) 관리자 목록의 사번 · 이메일 · 팀"""
    record = dict(state.user(username))
    record.setdefault('username', username)
    for u in getattr(state, 'admin_users', None) or []:
        if u.get('username') == username:
            record.update(u)
            break
    return record


class _EditResult(QObject):
    """보낸 admin_member_update 의 결과를 한 번 기다린다: 오류면 알림 창, 성공하면 조용히 끝"""

    def __init__(self, state, username, parent):
        super().__init__(parent)
        self.state, self.username, self.window = state, username, parent
        self.error = ''
        state.error.connect(self._on_error)
        state.admin_done.connect(self._on_done)
        # 답이 오지 않으면(연결 끊김) 기다리기를 그만둔다 - 남아 있으면 한참 뒤 관계없는 오류에 '정보 수정' 창이 떴다
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._finish)
        self._timeout.start(self.WAIT_MS)

    WAIT_MS = 30000

    def _finish(self):
        disconnect_signals((self.state.error, self._on_error), (self.state.admin_done, self._on_done))
        self.deleteLater()

    def _on_done(self, p):
        if p.get('action') == 'member_update' and p.get('username') == self.username:
            self._finish()

    def _on_error(self, message):
        self.error = message
        box = QMessageBox(QMessageBox.Warning, '정보 수정', message, QMessageBox.Ok, self.window)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.open()
        self._finish()


def submit_member_edit(state, username, dlg, parent=None):
    """구성원 창에서 바뀐 필드만 보낸다. 바뀐 것이 없으면 False"""
    changes = dlg.changes()
    if not changes:
        return False
    _EditResult(state, username, parent)
    state.admin_member_update(username, changes)
    return True


def edit_member_info(state, username, parent=None, exec_=True):
    """편집 모드로 구성원 정보를 연다 (관리자 · 관리 범위 안). 권한이 없으면 None"""
    if not can_edit_member(state, username):
        return None
    dlg = MemberDialog(state, member_record(state, username), parent)
    dlg.setWindowTitle(f'편집 모드 · {username}')
    state.admin_users_changed.connect(dlg.fill_from)
    if not any(u.get('username') == username for u in getattr(state, 'admin_users', None) or []):
        state.admin_request_users()            # 사번 · 이메일 · 팀은 관리자 목록에만 있다
    if exec_:
        accepted = dlg.exec_() == QDialog.Accepted
        disconnect_signals((state.admin_users_changed, dlg.fill_from))
        if accepted:
            submit_member_edit(state, username, dlg, parent)
    return dlg


class NoticeDialog(QDialog):
    """전체 공지 쓰기 (접속 중인 모든 사람에게 바로 뜬다)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('전체 공지')
        col = QVBoxLayout(self)
        col.setContentsMargins(theme.sp('l'), theme.sp('l'), theme.sp('l'), theme.sp('m'))
        hint = QLabel('지금 접속 중인 모든 사람에게 \'관리자 공지\' 창으로 바로 보냅니다.')
        hint.setWordWrap(True)
        col.addWidget(hint)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(f'공지 내용 ({NOTICE_MAX}자 이내)')
        col.addWidget(self.edit, 1)
        self.counter = QLabel(f'0 / {NOTICE_MAX}')
        self.counter.setAlignment(Qt.AlignRight)
        col.addWidget(self.counter)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        self.send_button = QPushButton('보내기')
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.send_button)
        col.addLayout(row)
        self.edit.textChanged.connect(self._update)
        self._update()
        self.resize(440, 280)

    def content(self):
        return self.edit.toPlainText().strip()

    def _update(self):
        n = len(self.content())
        self.counter.setText(f'{n} / {NOTICE_MAX}')
        self.send_button.setEnabled(0 < n <= NOTICE_MAX)


class AdminNoticeDialog(QDialog):
    """받은 관리자 공지 창 - 시스템 메시지 상자(QMessageBox) 대신 앱이 직접 그린다.
    QMessageBox 의 시스템 아이콘은 Windows 에서 창을 띄울 때 접근 위반으로 앱을 멈춘 적이 있다 (CI windows-latest).
    세 OS 에서 같은 모양 · 같은 동작이 되도록 아이콘은 widgets.make_icon 으로 그린다."""

    def __init__(self, parent, notice):
        super().__init__(parent)
        from client.kakao.widgets import make_icon
        self.setWindowTitle('관리자 공지')
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        when = (notice.get('timestamp') or '').replace('T', ' ')[:16]
        self._title = '관리자 공지'                    # macOS 는 창 제목을 보이지 않으므로 본문 첫 줄에도 쓴다
        body = html.escape(notice.get('content', '')).replace('\n', '<br>')   # 공지 글자는 서식으로 해석하지 않는다
        sender = html.escape(f"보낸 사람: {notice.get('sender', '')}  ·  {when}")
        self._info = f'{body}<br><br><span style="color:{theme.c("text_hint")}">{sender}</span>'
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 16)
        col.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(make_icon('notice', theme.c('yellow'), 20).pixmap(20, 20))
        head.addWidget(icon, 0, Qt.AlignVCenter)
        title = QLabel(self._title)
        title.setStyleSheet(f"font-size: {theme.fs('title')}px; font-weight: bold; color: {theme.c('text')};")
        head.addWidget(title, 1)
        col.addLayout(head)
        self.body_label = QLabel(self._info)
        self.body_label.setTextFormat(Qt.RichText)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_label.setStyleSheet(f"font-size: {theme.fs('body')}px; color: {theme.c('text')};")
        col.addWidget(self.body_label)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton('확인')
        ok.setProperty('primary', True)
        ok.setDefault(True)
        ok.clicked.connect(self.close)
        row.addWidget(ok)
        col.addLayout(row)
        self.resize(theme.px(360), self.sizeHint().height())   # 글자 크기 배율을 따르는 기본 폭 (고정 크기 아님)

    # QMessageBox 와 같은 이름 (호출하던 코드 · 테스트 호환)
    def text(self):
        return self._title

    def informativeText(self):
        return self._info


def show_admin_notice(parent, notice):
    """받은 관리자 공지: 닫을 수 있는 비모달 창 (여러 개가 와도 각각)"""
    box = AdminNoticeDialog(parent, notice)
    box.show()
    return box
