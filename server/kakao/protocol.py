# -*- coding: utf-8 -*-
"""요청 검사 공용 도구: ProtocolError(사용자에게 보일 오류) · 필드 꺼내기(_req_str 등) · ISO 시각 · 검증 정규식
server.py 와 관리자 처리(admin_handlers.py)가 함께 쓴다"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .store import ISO_FMT

ACCOUNT_DISABLED = '사용이 중지된 계정입니다. 관리자에게 문의해 주세요.'
ADMIN_ONLY = '관리자만 사용할 수 있습니다.'
EMAIL_RE = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')
EMPLOYEE_NO_RE = re.compile(r'[0-9A-Za-z][0-9A-Za-z_-]{0,19}')   # 사번: 영문·숫자(-, _) 1~20자


class ProtocolError(Exception):
    """클라이언트에게 error 로 돌려줄 검증 오류 (메시지는 한국어).
    code: 클라이언트가 문구 대신 보고 가르는 구분 값 (명세 §4.1 '오류 구분 코드', 없으면 ''), extra: error 에 함께 실을 값"""

    def __init__(self, message: str, code: str = '', **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------- 입력 검증 헬퍼
MAX_LIST_ITEMS = 1000      # 이름 목록(_str_list) 한 번에 받는 최대 개수
MAX_TAG_LEN = 64           # req_id · client_tmp_id 문자열 길이


def _utf8_ok(value: str) -> bool:
    """UTF-8 로 보낼 수 있는 글자인가 (짝 없는 서러게이트가 섞이면 저장 · 전송이 실패한다)"""
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


# 글자 방향을 바꾸는 문자 (이름 · 파일 이름의 겉모습을 바꾼다)
_BIDI_CONTROLS = frozenset({0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)})


def has_hidden_chars(value: str) -> bool:
    """제어 문자(줄바꿈 · 탭 포함)나 글자 방향을 바꾸는 문자가 들어 있는가"""
    return any(ord(ch) < 32 or ord(ch) == 127 or ord(ch) in _BIDI_CONTROLS for ch in value)


def clean_file_name(name: str) -> str:
    """첨부 파일 이름 정리 (명세 §4.5): 폴더 부분을 떼고, 제어 문자 · 방향 전환 문자를 지운다. 남는 게 없으면 '파일'"""
    name = (name or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(ch for ch in name if not has_hidden_chars(ch)).strip().rstrip(' .')
    return name if name not in ('', '.', '..') else '파일'


def _tag_ok(value: Any) -> bool:
    """req_id · client_tmp_id: 클라이언트가 붙이는 짧은 표식 - 없음 · 정수 · 64자 이내 문자열만 그대로 돌려준다"""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return -INT_LIMIT <= value <= INT_LIMIT
    return isinstance(value, str) and len(value) <= MAX_TAG_LEN and _utf8_ok(value)


def _opt_tag(msg: dict, key: str) -> Any:
    value = msg.get(key)
    if not _tag_ok(value):
        raise ProtocolError(f'{key} 값이 올바르지 않습니다.')
    return value


def _req_str(msg: dict, key: str, max_len: int, allow_empty: bool = False, label: str = '') -> str:
    value = msg.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f'{label or key} 값이 올바르지 않습니다.')
    if not _utf8_ok(value):
        raise ProtocolError(f'{label or key} 값에 쓸 수 없는 문자가 있습니다.')
    value = value.strip()
    if not value and not allow_empty:
        raise ProtocolError(f'{label or key} 값을 입력하세요.')
    if len(value) > max_len:
        raise ProtocolError(f'{label or key} 값은 {max_len}자를 넘을 수 없습니다.')
    return value


def _req_name(msg: dict, key: str, max_len: int, allow_empty: bool = False, label: str = '') -> str:
    """사람 · 방 · 프로필 이름처럼 한 줄로 보이는 값: _req_str + 제어 문자 · 방향 전환 문자 불가"""
    value = _req_str(msg, key, max_len, allow_empty=allow_empty, label=label)
    if has_hidden_chars(value):
        raise ProtocolError(f'{label or key}에 사용할 수 없는 문자가 있습니다.')
    return value


def _opt_name(msg: dict, key: str, max_len: int, default: str = '') -> str:
    if msg.get(key) is None:
        return default
    return _req_name(msg, key, max_len, allow_empty=True)


def _opt_str(msg: dict, key: str, max_len: int, default: str = '') -> str:
    if msg.get(key) is None:
        return default
    return _req_str(msg, key, max_len, allow_empty=True)


INT_LIMIT = 2 ** 62   # SQLite INTEGER(부호 있는 64비트)에 넣을 수 있는 범위 안 (더 크면 OverflowError)


def _req_int(msg: dict, key: str) -> int:
    value = msg.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not -INT_LIMIT <= value <= INT_LIMIT:
        raise ProtocolError(f'{key} 값이 올바르지 않습니다.')
    return value


def _opt_int(msg: dict, key: str) -> Optional[int]:
    return None if msg.get(key) is None else _req_int(msg, key)


def _bool(msg: dict, key: str, default: bool = False) -> bool:
    value = msg.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ProtocolError(f'{key} 값이 올바르지 않습니다.')


def _str_list(msg: dict, key: str, required: bool = True, max_items: int = MAX_LIST_ITEMS) -> list[str]:
    """문자열 목록 (앞뒤 공백 제거 · 빈 값 제외 · 중복은 처음 것만, 순서 유지). max_items 보다 길면 오류"""
    value = msg.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise ProtocolError(f'{key} 값이 올바르지 않습니다.')
    if len(value) > max_items:
        raise ProtocolError(f'{key} 항목이 너무 많습니다 (최대 {max_items}개).')
    if not all(isinstance(v, str) and _utf8_ok(v) for v in value):
        raise ProtocolError(f'{key} 값이 올바르지 않습니다.')
    seen: dict[str, None] = {}
    for v in value:
        v = v.strip()
        if v:
            seen.setdefault(v, None)
    return list(seen)


def parse_iso(value: Any, key: str) -> datetime:
    """ISO 문자열 → 서버 로컬 naive datetime. 시간대가 있으면 로컬로 변환."""
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f'{key} 시각 형식이 올바르지 않습니다.')
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ProtocolError(f'{key} 시각 형식이 올바르지 않습니다.') from None
    if dt.tzinfo is not None:
        try:
            dt = dt.astimezone().replace(tzinfo=None)
        except (OverflowError, OSError):        # 0001-01-01T00:00:00+09:00 처럼 로컬로 바꿀 수 없는 시각
            raise ProtocolError(f'{key} 시각 형식이 올바르지 않습니다.') from None
    return dt


def _norm_iso(value: Any, key: str) -> str:
    return parse_iso(value, key).strftime(ISO_FMT)
