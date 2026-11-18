# -*- coding: utf-8 -*-
"""
비밀채팅 종단간 암호화 (명세 §8.1)
- 사용자별 X25519 키쌍을 이 PC에만 보관: <KCS_MESSENGER_HOME 또는 ~/.kcs_messenger>/keys/<username>.key (권한 600)
- 방 키 = HKDF-SHA256(X25519(내 비밀키, 상대 공개키), salt=room_id, info=b'kcs-secret-v1')
- 메시지 = base64(nonce 12바이트 + AES-256-GCM 암호문), 평문은 {"text": "..."} JSON
서버는 공개키만 알고, 비밀키·평문은 절대 서버로 가지 않는다.
"""

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO = b'kcs-secret-v1'


def key_dir():
    from client.kakao import osenv
    return osenv.app_path('keys')


def key_file_stem(username):
    """키 파일 이름 (확장자 빼고). 서버가 알려 준 사용자 이름을 그대로 경로에 쓰지 않는다 - '/' · '..' · 예약 문자는
    설정 파일과 같은 방식(_XX)으로 바꿔 keys 폴더를 벗어나지 않게. 보통 이름(한글 · 영문 · 숫자)은 예전 이름 그대로"""
    from client.kakao.settings import settings_file_name
    return settings_file_name(username)[:-len('.json')]


class SecretKeyring:
    """한 사용자의 비밀키 보관 + 방 키 유도 + 암복호화"""

    def __init__(self, username, directory=None):
        self.username = username
        self.path = os.path.join(directory or key_dir(), key_file_stem(username) + '.key')
        self._private = self._load_or_create()
        self._room_keys = {}

    def _load_or_create(self):
        """키가 없을 때만 새로 만든다. 읽지 못한 이유가 잠깐의 오류(권한 · 다른 프로그램이 잡고 있음)면 덮어쓰지 않고
        예외를 그대로 올린다 - 새 키로 바꾸면 지난 비밀 메시지를 영영 읽을 수 없게 된다 (이번 실행만 비밀채팅 없음).
        내용이 깨진 키 파일은 .bak 으로 남겨 두고 새로 만든다"""
        try:
            with open(self.path, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            return self._create()
        try:
            key = serialization.load_pem_private_key(data, password=None)
            if isinstance(key, X25519PrivateKey):
                return key
        except (ValueError, TypeError):
            pass
        os.replace(self.path, self.path + '.bak')
        return self._create()

    def _create(self):
        key = X25519PrivateKey.generate()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        tmp = self.path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(pem)
        os.replace(tmp, self.path)       # 쓰다 만 파일이 키 파일 자리에 남지 않게
        return key

    @property
    def public_key_b64(self):
        raw = self._private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode('ascii')

    def room_key(self, room_id, peer_public_b64):
        cache_key = (room_id, peer_public_b64)
        if cache_key not in self._room_keys:
            peer = X25519PublicKey.from_public_bytes(base64.b64decode(peer_public_b64))
            shared = self._private.exchange(peer)
            self._room_keys[cache_key] = HKDF(algorithm=hashes.SHA256(), length=32,
                                              salt=room_id.encode('utf-8'), info=INFO).derive(shared)
        return self._room_keys[cache_key]

    def encrypt(self, room_id, peer_public_b64, text):
        key = self.room_key(room_id, peer_public_b64)
        nonce = os.urandom(12)
        plain = json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
        return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plain, room_id.encode('utf-8'))).decode('ascii')

    def decrypt(self, room_id, peer_public_b64, blob):
        """복호화한 텍스트. 키가 다르거나 변조되었으면 None"""
        try:
            raw = base64.b64decode(blob)
            key = self.room_key(room_id, peer_public_b64)
            plain = AESGCM(key).decrypt(raw[:12], raw[12:], room_id.encode('utf-8'))
            obj = json.loads(plain.decode('utf-8'))
        except (InvalidTag, ValueError, TypeError, KeyError):
            return None
        # 상대(수정한 클라이언트)가 올바르게 암호화했어도 내용이 {'text': 글자} 가 아니면 읽지 못한 메시지로 둔다
        # (예전에는 [] · 1 이면 AttributeError 로 그 방의 대화 받기가 통째로 멈췄다)
        if not isinstance(obj, dict) or not isinstance(obj.get('text', ''), str):
            return None
        return obj.get('text', '')


def fingerprint(key_a_b64, key_b_b64):
    """두 사람의 공개키로 만든 확인 번호 (5자리 × 6묶음). 두 사람 화면에 같은 번호가 보이고, 어느 한쪽 키가 바뀌면 달라진다.
    전화처럼 메신저 밖의 방법으로 서로 읽어 맞춰 보는 용도"""
    first, second = sorted([base64.b64decode(key_a_b64), base64.b64decode(key_b_b64)])
    digest = hashlib.sha256(b'kcs-secret-check-v1' + first + second).digest()
    number = str(int.from_bytes(digest, 'big') % 10 ** 30).zfill(30)
    return ' '.join(number[i:i + 5] for i in range(0, 30, 5))


class PeerKeys:
    """상대 공개키 기억 (이 PC, keys/<username>.peers.json).
    처음 본 키는 그대로 믿고, 그 뒤 서버가 다른 키를 알려 주면 사용자가 확인할 때까지 그 키로 보내지 않는다.
    확인했던 예전 키도 남겨 두어 그 키로 받은 지난 메시지를 계속 읽는다"""

    def __init__(self, username, directory=None):
        self.path = os.path.join(directory or key_dir(), key_file_stem(username) + '.peers.json')
        self._keys = self._load()          # {상대: [예전에 믿은 키 …, 지금 믿는 키]}

    def _load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(peer): [k for k in keys if isinstance(k, str) and k]
                for peer, keys in data.items() if isinstance(keys, list)}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + '.tmp'
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self._keys, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass                           # 저장하지 못해도 이번 실행 동안은 기억한다

    def trusted(self, peer):
        """믿는 키들 (오래된 것부터, 마지막이 지금 믿는 키)"""
        return list(self._keys.get(peer) or [])

    def status(self, peer, key_b64):
        """'new' (처음 보는 상대) / 'same' (지금 믿는 키) / 'changed' (믿는 키와 다르다)"""
        keys = self._keys.get(peer)
        if not keys:
            return 'new'
        return 'same' if keys[-1] == key_b64 else 'changed'

    def remember(self, peer, key_b64):
        """이 키를 지금 믿는 키로 (예전 키는 읽기용으로 남긴다)"""
        keys = [k for k in self._keys.get(peer) or [] if k != key_b64]
        self._keys[peer] = keys[-9:] + [key_b64]
        self._save()
