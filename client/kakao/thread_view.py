# -*- coding: utf-8 -*-
"""
스레드 (명세 §10.4) - 메시지 하나에 달린 답글 묶음을 따로 보는 창.
대화창 본문에는 원글 아래 '답글 N개'만 보이고, 누르면 이 창이 열린다.
"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QWidget, QFrame,
                             QLineEdit)

from client.kakao import theme
from client.kakao.widgets import disconnect_signals
from client.kakao.chat_room import _Dialog, fmt_time

THREAD_MAX_LEN = 4000


class ThreadDialog(_Dialog):
    """원글 + 답글 목록 + 답글 입력. 비모달로 열고, 받은 답글은 바로 붙인다"""

    def __init__(self, win, root_id, parent=None):
        super().__init__('스레드', parent)
        self.win, self.state = win, win.state
        self.room_id, self.root_id = win.room_id, root_id
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(theme.px(380), theme.px(520))
        c = theme.c
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)
        title = QLabel('스레드')
        title.setStyleSheet(f"font-size: {theme.fs('lead')}px; font-weight: bold;")
        v.addWidget(title)
        self.root_box = QFrame(self)
        self.root_box.setObjectName('threadRoot')
        self.root_box.setStyleSheet(f"QFrame#threadRoot {{ background: {c('bg_alt')}; border-radius: 10px; }}"
                                    f" QLabel {{ background: transparent; }}")
        rv = QVBoxLayout(self.root_box)
        rv.setContentsMargins(12, 10, 12, 10)
        rv.setSpacing(4)
        self.root_name = QLabel(self.root_box)
        self.root_name.setTextFormat(Qt.PlainText)
        self.root_name.setStyleSheet(f"color: {c('text_sub')}; font-size: {theme.fs('meta')}px; font-weight: bold;")
        self.root_text = QLabel(self.root_box)
        self.root_text.setTextFormat(Qt.PlainText)
        self.root_text.setWordWrap(True)
        self.root_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root_text.setStyleSheet(f"font-size: {theme.fs('body')}px;")
        rv.addWidget(self.root_name)
        rv.addWidget(self.root_text)
        v.addWidget(self.root_box)
        self.count_label = QLabel(self)
        self.count_label.setStyleSheet(f"color: {c('text_sub')}; font-size: {theme.fs('meta')}px;")
        v.addWidget(self.count_label)
        self.area = QScrollArea(self)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.host = QWidget()
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.area.setWidget(self.host)
        v.addWidget(self.area, 1)
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {c('badge')}; font-size: {theme.fs('meta')}px;")
        self.error_label.hide()
        v.addWidget(self.error_label)
        row = QHBoxLayout()
        self.edit = QLineEdit(self)
        self.edit.setMaxLength(THREAD_MAX_LEN)
        self.edit.setPlaceholderText('스레드에 답글 보내기')
        self.edit.setAccessibleName('스레드 답글 입력')
        self.send_button = QPushButton('전송', self)
        self.send_button.setObjectName('primary')
        self.send_button.setEnabled(False)
        row.addWidget(self.edit, 1)
        row.addWidget(self.send_button)
        v.addLayout(row)
        self.edit.textChanged.connect(lambda t: self.send_button.setEnabled(bool(t.strip())))
        self.edit.returnPressed.connect(self.send)
        self.send_button.clicked.connect(self.send)
        self.reply_labels = {}
        self.state.message_added.connect(self._on_message)
        self.state.message_updated.connect(self._on_message)
        self.state.thread_loaded.connect(self._on_loaded)
        self.refresh()
        self.state.request_thread(root_id)

    # ---- 데이터
    def root(self):
        return self.state.find_message(self.room_id, self.root_id) or {}

    def replies(self):
        return self.state.thread_messages(self.room_id, self.root_id)

    def _on_loaded(self, rid, root_id):
        if rid == self.room_id and root_id == self.root_id:
            self.refresh()
            self._scroll_to_bottom()

    def _on_message(self, rid, m):
        if rid != self.room_id:
            return
        if m.get('msg_id') == self.root_id or m.get('thread_root') == self.root_id:
            new = m.get('thread_root') == self.root_id and m['msg_id'] not in self.reply_labels
            self.refresh()
            if new:
                self._scroll_to_bottom()
                if self.isActiveWindow():
                    self.state.mark_read(self.room_id)

    # ---- 화면
    def _text_of(self, m):
        if m.get('deleted'):
            return '메시지가 삭제되었습니다'
        if m.get('kind') == 'text':
            return m.get('content', '')
        return self.state.message_preview(m)

    def refresh(self):
        root = self.root()
        if root:
            self.root_name.setText(f"{self.state.member_name(self.room_id, root.get('sender'))}"
                                   f" · {fmt_time(root.get('timestamp'))}")
            self.root_text.setText(self._text_of(root))
        replies = self.replies()
        count = max(len(replies), int(root.get('thread_count') or 0))
        self.count_label.setText(f'답글 {count}개' if count else '아직 답글이 없습니다. 첫 답글을 남겨 보세요.')
        can_reply = bool(root) and not root.get('deleted')
        self.edit.setEnabled(can_reply)
        if root.get('deleted'):
            self.edit.setPlaceholderText('원글이 삭제되어 답글을 보낼 수 없습니다')
        seen = set()
        for m in replies:
            seen.add(m['msg_id'])
            label = self.reply_labels.get(m['msg_id'])
            text = self._reply_html(m)
            if label is None:
                label = QLabel(self.host)
                label.setWordWrap(True)
                label.setTextFormat(Qt.RichText)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setStyleSheet(f"font-size: {theme.fs('body')}px; background: transparent;")
                self.list_layout.insertWidget(self.list_layout.count() - 1, label)
                self.reply_labels[m['msg_id']] = label
            label.setText(text)
            label.setAccessibleName(f"{self.state.member_name(self.room_id, m.get('sender'))}: {self._text_of(m)}")
        for mid in [k for k in self.reply_labels if k not in seen]:   # 나에게서 삭제 등으로 빠진 답글
            self.reply_labels.pop(mid).deleteLater()

    def _reply_html(self, m):
        from html import escape
        name = escape(self.state.member_name(self.room_id, m.get('sender')))
        when = escape(fmt_time(m.get('timestamp')))
        body = escape(self._text_of(m)).replace('\n', '<br>')
        sub = theme.c('text_sub')
        style = f"color: {theme.c('text_hint')};" if m.get('deleted') else ''
        edited = f" <span style='color:{sub};'>(수정됨)</span>" if m.get('edited') and not m.get('deleted') else ''
        return (f"<b>{name}</b> <span style='color:{sub}; font-size:{theme.fs('caption')}px;'>{when}</span>"
                f"<br><span style='{style}'>{body}</span>{edited}")

    def _scroll_to_bottom(self):
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))

    # ---- 보내기
    def send(self):
        text = self.edit.text().strip()
        if not text:
            return False
        if not self.state.send_text(self.room_id, text, thread_root_id=self.root_id):
            self.error_label.setText('서버 연결이 끊겨 답글을 보내지 못했습니다. 연결된 뒤 다시 보내 주세요.')
            self.error_label.show()
            return False
        self.error_label.hide()
        self.edit.clear()
        return True

    def done(self, result):
        disconnect_signals((self.state.message_added, self._on_message),
                           (self.state.message_updated, self._on_message),
                           (self.state.thread_loaded, self._on_loaded))
        super().done(result)
