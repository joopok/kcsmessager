# -*- coding: utf-8 -*-
"""
같은 PC · 같은 사용자 데이터 폴더에서 메신저는 하나만 실행한다.

- 잠금 파일(QLockFile)로 먼저 실행된 것이 있는지 본다. 비정상 종료로 남은 잠금 파일은 Qt 가 알아서 무시한다.
- 두 번째로 실행된 쪽은 먼저 실행된 쪽에 "창을 보여 달라"고 알리고 끝낸다 (QLocalServer, 같은 사용자만 접속).

    instance = SingleInstance()
    if not instance.acquire():
        instance.notify()      # 이미 실행 중 → 그 창을 앞으로
        return 0
    instance.activated.connect(창을_앞으로)
"""

import hashlib
import os

from PyQt5.QtCore import QObject, QLockFile, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

from client.kakao import osenv


class SingleInstance(QObject):
    activated = pyqtSignal()        # 다른 실행이 창을 보여 달라고 알림

    def __init__(self, name='main', folder=None, parent=None):
        super().__init__(parent)
        folder = folder or osenv.app_home()
        self._lock_path = os.path.join(folder, f'{name}.lock')
        digest = hashlib.sha256(os.path.abspath(self._lock_path).encode('utf-8')).hexdigest()[:20]
        self._server_name = f'kcs-talk-{digest}'
        self._lock = None
        self._server = None

    def acquire(self, wait_ms=0):
        """첫 실행이면 True (창 보여 달라는 알림을 받기 시작한다). 이미 실행 중이면 False.
        wait_ms: 앞선 실행이 끝나기를 기다리는 시간 (로그아웃 뒤 다시 실행처럼 곧 끝나는 경우)"""
        os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        lock = QLockFile(self._lock_path)
        lock.setStaleLockTime(0)        # 시간으로는 판단하지 않는다 (주인 프로세스가 없으면 Qt 가 풀어 준다)
        if not lock.tryLock(max(0, int(wait_ms))):
            return False
        self._lock = lock
        QLocalServer.removeServer(self._server_name)      # 비정상 종료로 남은 소켓 파일
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.UserAccessOption)
        server.newConnection.connect(self._on_connection)
        server.listen(self._server_name)                  # 실패해도 실행은 계속한다 (알림만 못 받는다)
        self._server = server
        return True

    def notify(self, timeout_ms=1000):
        """먼저 실행된 쪽에 창을 보여 달라고 알린다. 알렸으면 True"""
        sock = QLocalSocket()
        sock.connectToServer(self._server_name)
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write(b'show\n')
        ok = sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return bool(ok)

    def _on_connection(self):
        while self._server is not None and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.disconnected.connect(sock.deleteLater)
            self.activated.emit()

    def release(self):
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None
