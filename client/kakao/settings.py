# -*- coding: utf-8 -*-
"""
로컬 사용자 설정 - 사용자별 JSON 파일(~/.kcs_messenger/<username>.json)
즐겨찾기, 채팅방 고정/알림끔, 나에게서 삭제한 메시지, 다크모드, 글자 크기
"""

import json
import os

from PyQt5.QtCore import QObject, pyqtSignal

from client.kakao import osenv

DEFAULTS = {
    'favorites': [],
    'pinned_rooms': [],
    'muted_rooms': [],
    'hidden_messages': [],
    'dark_mode': False,
    'font_scale': 1.0,
    'reduce_motion': False,      # 동작 줄이기 (녹음 표시 깜빡임·요약 진행 막대 정지)
    'notifications': True,
    'blink_unread': True,        # 안 읽은 메시지가 있으면 앱 아이콘(트레이·Dock) 깜박임
    'brand_theme': 'kcs',
    'chat_wallpaper': None,
    'room_wallpapers': {},
    'chat_folders': [],
    'hidden_friends': [],
    'hidden_story_users': [],
    'members_strip_open': [],   # 참여자 줄을 펼쳐 둔 채팅방 room_id (방마다 기억)
    'failed_messages': [],      # 연결이 끊겨 보내지 못한 메시지 (재전송/삭제 전까지 보존)   # 소식(펑)을 숨긴 친구 (카카오톡 2026 2차 업데이트)
    'alert_keywords': [],
    'dnd_until': '',             # 방해금지: 이 시각까지 알림 일시중지 (§10.1)
    'work_hours': {},            # 방해금지: 근무 시간에만 알림 {enabled, days, start, end}
    'vip_users': [],             # 방해금지 중에도 알림을 받는 사람
    'room_drafts': {},           # 방별 쓰다 만 글 (§10.8)
    'saved_messages': [],        # 책갈피 - 저장한 메시지 (§10.7)
    'room_alert_modes': {},
    'bubble_style': 'basic',
    'lock_pin_hash': '',
    'lock_salt': '',
    'lock_minutes': 5,
    'call_auto_record': False,   # 보이스톡 자동 녹음 (카카오톡 2025-11)
    'social_features': False,    # 펑·생일·선물하기 (회사 메신저는 기본 끔 - 업무 우선)
}


_UNSAFE_FILE_CHARS = set('\\/:*?"<>|')

# ---------------------------------------------------------------- 이 PC 의 서버 주소 (그룹 관리자만 더보기 → 정보에서 바꾼다)
SERVER_FILE = 'server.json'
DEFAULT_HOST, DEFAULT_PORT = '127.0.0.1', 5555
# 기본은 평문(개발용 로컬 서버). 운영 서버(talk.kcins.co.kr:9550)는 nginx 가 TLS 를 종단하므로
# server.json 에 "tls": true 를 넣거나 --tls 로 켠다. 배포용 빌드는 이 값을 True 로 바꿔 만든다.
DEFAULT_TLS = False


def parse_server_address(host, port):
    """칸 값 → (host, port). 틀리면 ValueError(화면에 보일 문장)"""
    host = (host or '').strip()
    if not host or len(host) > 253 or any(ch.isspace() or ord(ch) < 32 for ch in host) or '/' in host:
        raise ValueError('서버 주소를 올바르게 입력해 주세요. (예: 192.168.0.10 또는 chat.kcs.co.kr)')
    try:
        port = int(str(port).strip())
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError('포트는 1~65535 사이 숫자로 입력해 주세요.')
    return host, port


DEPLOY_FILE = ('deploy', 'deploy.json')   # 빌드에 번들되는 배포 기본값 (kcs.py build --deploy …)


def load_deploy_defaults():
    """빌드에 들어간 배포 기본값 {host, port, tls} - 없거나 깨졌으면 None.
    설치형 배포본이 운영 서버(talk.kcins.co.kr:9550, TLS)로 바로 붙게 하기 위한 것으로, 이 PC 의
    server.json(그룹 관리자 설정)보다는 뒤, 코드 기본값(127.0.0.1:5555)보다는 앞 순위다.
    소스 실행에는 보통 이 파일이 없어 개발 환경은 영향받지 않는다."""
    path = osenv.resource_path(*DEPLOY_FILE)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        host, port = parse_server_address(data.get('host'), data.get('port'))
        tls = data.get('tls')
        return {'host': host, 'port': port, 'tls': bool(tls) if isinstance(tls, bool) else DEFAULT_TLS}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def load_server_address(base_dir=None):
    """저장된 (host, port) - 이 PC 의 server.json → 번들된 deploy.json → 없으면 None"""
    path = os.path.join(base_dir or osenv.app_home(), SERVER_FILE)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return parse_server_address(data.get('host'), data.get('port'))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    deploy = load_deploy_defaults()
    return (deploy['host'], deploy['port']) if deploy else None


def _saved_tls(base_dir):
    """server.json 에 명시된 tls (bool) - 없거나 bool 이 아니면 None"""
    try:
        with open(os.path.join(base_dir, SERVER_FILE), encoding='utf-8') as f:
            value = json.load(f).get('tls')
        return value if isinstance(value, bool) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def load_server_tls(base_dir=None):
    """TLS 사용 여부: 이 PC 의 server.json → 번들된 deploy.json → DEFAULT_TLS"""
    saved = _saved_tls(base_dir or osenv.app_home())
    if saved is not None:
        return saved
    deploy = load_deploy_defaults()
    return deploy['tls'] if deploy else DEFAULT_TLS


def save_server_address(host, port, base_dir=None, tls=None):
    """검사한 뒤 저장 (임시 파일 → 교체). 저장한 (host, port).
    tls 를 넘기지 않으면 기존 파일의 tls 값을 그대로 둔다(더보기 → 정보에서 주소만 바꿔도 TLS 설정이 풀리지 않게).
    명시한 값도 기존 값도 없으면 tls 키를 쓰지 않는다 - 파일은 {host, port} 그대로(기본값 = 생략)."""
    host, port = parse_server_address(host, port)
    base = base_dir or osenv.app_home()
    if tls is None:
        tls = _saved_tls(base)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, SERVER_FILE)
    data = {'host': host, 'port': port}
    if tls is not None:
        data['tls'] = bool(tls)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    return host, port


def settings_file_name(username):
    """사용자 이름 → 설정 파일 이름. 경로 구분자·예약 문자·제어 문자는 _XX 로 바꿔 디렉터리를 벗어나지 않게 한다"""
    safe = ''.join(f'_{ord(ch):02X}' if ch in _UNSAFE_FILE_CHARS or ord(ch) < 32 else ch for ch in username)
    if not safe or safe.startswith('.'):
        safe = '_' + safe
    return f'{safe}.json'


class LocalSettings(QObject):
    """키 단위 변경 시그널을 내보내는 간단한 설정 저장소.
    저장은 임시 파일 → 교체(원자적)이고, 같은 사용자의 다른 실행이 쓴 키를 덮어쓰지 않도록 바꾼 키만 합쳐 쓴다."""

    changed = pyqtSignal(str)  # key

    def __init__(self, username, base_dir=None, parent=None):
        super().__init__(parent)
        self.base_dir = base_dir or osenv.app_home()
        self.username = username
        self.path = os.path.join(self.base_dir, settings_file_name(username))
        self._data = dict(DEFAULTS)
        self._data.update(self._read_file(keep_bad=True))

    def use_user(self, username):
        """로그인 아이디(계정)와 실제 사용자 이름이 다르면 사용자 이름 파일로 바꾼다 (welcome 직후).
        예전 아이디 파일에만 있던 값은 사용자 파일에 없는 키만 옮긴다."""
        path = os.path.join(self.base_dir, settings_file_name(username))
        if path == self.path:
            return
        previous = self._read_file()
        self.username, self.path = username, path
        target = self._read_file(keep_bad=True)
        self._data = dict(DEFAULTS)
        self._data.update(previous)
        self._data.update(target)
        if set(previous) - set(target):
            self._write({**previous, **target})

    def _read_file(self, keep_bad=False):
        try:
            with open(self.path, encoding='utf-8') as f:
                loaded = json.load(f)
        except OSError:
            return {}
        except ValueError:
            if keep_bad:   # 깨진 파일은 덮어쓰지 않고 따로 보관
                try:
                    os.replace(self.path, self.path + '.bak')
                except OSError:
                    pass
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def get(self, key):
        value = self._data.get(key, DEFAULTS.get(key))
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            return dict(value)
        return value

    def set(self, key, value):
        """값을 바꾸고 저장한다. 디스크 저장에 실패하면 False (값은 이번 실행 동안 유지)"""
        self._data[key] = value
        ok = self._save(key)
        self.changed.emit(key)
        return ok

    def toggle_in(self, key, item):
        """리스트형 설정에서 item을 넣거나 뺀다. 결과 포함 여부를 돌려준다."""
        items = list(self.get(key) or [])
        if item in items:
            items.remove(item)
            included = False
        else:
            items.append(item)
            included = True
        self.set(key, items)
        return included

    def contains(self, key, item):
        return item in self._data.get(key, [])

    def _save(self, key=None):
        data = self._read_file() if key is not None else {}
        if key is None:
            data = dict(self._data)
        else:
            data[key] = self._data[key]
        return self._write(data)

    def _write(self, data):
        tmp = self.path + '.tmp'
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False
