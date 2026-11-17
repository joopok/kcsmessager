# -*- coding: utf-8 -*-
"""
파일 전송 보안 (명세 §10.10) - 외부 유출 · 악성 파일을 막는 첨부 검사

- 형태(종류): 확장자뿐 아니라 **파일 내용의 첫 바이트(매직 넘버)** 로도 판별한다.
  `보고서.txt` 로 이름만 바꾼 실행 파일도 '실행 파일' 로 잡힌다.
- 확장자: 관리자가 적은 확장자 목록 (`hwp`, `.zip` 처럼 점은 있어도 없어도 된다)
- 파일 이름: `*대외비*` 같은 와일드카드, 와일드카드가 없으면 '이름에 들어 있으면' 으로 본다 (대소문자 무시)
- 압축 파일(zip)은 안에 든 파일 이름까지 같은 규칙으로 본다 (암호가 걸려 있어도 이름은 보인다)
- 매크로 문서(docm · xlsm · pptm 등)는 확장자와 내용(오피스 zip 안의 vbaProject.bin · 엑셀 매크로 시트,
  옛 오피스 OLE 문서의 VBA 저장소)으로 판별한다. 7z · rar · alz · egg 는 안을 볼 수 없어 따로 묶는다 (2026-11-17 결정)

이 모듈은 저장소 · 네트워크를 모른다. 정책 dict 와 파일(이름 · 바이트)만 받아 위반 내용을 돌려준다.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import os
import zipfile
import zlib
from typing import Any, Optional

# (키, 이름, 대표 확장자) - 관리자 화면에 이 순서로 나온다
CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ('executable', '실행 파일 · 스크립트',
     ('exe', 'msi', 'com', 'scr', 'bat', 'cmd', 'ps1', 'psm1', 'vbs', 'vbe', 'js', 'jse', 'wsf', 'wsh', 'hta',
      'cpl', 'dll', 'sys', 'jar', 'app', 'pkg', 'dmg', 'deb', 'rpm', 'apk', 'sh', 'run', 'lnk', 'reg',
      'command', 'msix', 'appx',
      # 바로가기 · 도움말 · 설정 스크립트 · 디스크 이미지 (2026-11-17): 열면 다른 프로그램을 실행하거나 통째로 담아 온다
      'url', 'website', 'scf', 'pif', 'chm', 'wsc', 'sct', 'inf', 'msc', 'gadget', 'jnlp', 'xll', 'application',
      'msp', 'mst', 'iso', 'img', 'vhd', 'vhdx')),
    # 매크로 문서 (2026-11-17): 열면 매크로가 실행될 수 있다. 확장자가 docx 여도 내용에 매크로가 있으면 이 형태다
    ('macro_document', '매크로 문서',
     ('docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'pptm', 'potm', 'ppsm', 'ppam', 'sldm')),
    # 서버가 안을 열어 볼 수 없는 압축 파일 (2026-11-17): zip 과 달리 든 파일 이름 · 내용을 검사할 수 없다
    ('sealed_archive', '속을 볼 수 없는 압축 파일 (7z · rar · alz · egg · egg)', ('7z', 'rar', 'alz', 'egg')),
    # 7z · rar · alz · egg 와 매크로 문서는 아래 '압축 파일' · '문서' 목록에도 둔다 - 그 형태를 막으면 함께 막힌다 (IMPLIED_TYPES)
    ('archive', '압축 파일',
     ('zip', '7z', 'rar', 'alz', 'egg', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'iso', 'cab', 'lzh', 'z')),
    ('database', '데이터베이스 · 백업 · 메일함',
     ('sql', 'db', 'sqlite', 'sqlite3', 'mdb', 'accdb', 'dbf', 'bak', 'dump', 'dmp', 'pst', 'ost', 'mdf', 'ldf',
      'frm', 'ibd')),
    ('credential', '인증서 · 암호 키',
     ('pem', 'key', 'pfx', 'p12', 'cer', 'crt', 'der', 'jks', 'keystore', 'kdbx', 'ppk', 'ovpn', 'gpg', 'asc',
      'p7b', 'csr')),
    ('source', '소스 코드',
     ('py', 'java', 'c', 'cc', 'cpp', 'h', 'hpp', 'cs', 'go', 'rs', 'kt', 'swift', 'ts', 'tsx', 'jsx', 'php',
      'rb', 'scala', 'vue', 'ipynb', 'm', 'pl', 'lua', 'dart')),
    ('document', '문서',
     ('hwp', 'hwpx', 'doc', 'docx', 'xls', 'xlsx', 'xlsb', 'ppt', 'pptx', 'pdf', 'txt', 'csv', 'rtf', 'odt',
      'ods', 'odp', 'md', 'pages', 'numbers', 'dotx', 'xltx', 'potx', 'ppsx',
      'docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'pptm', 'potm', 'ppsm', 'ppam', 'sldm')),
    ('image', '이미지', ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'heic', 'svg', 'tif', 'tiff', 'ico')),
    ('video', '동영상', ('mp4', 'mov', 'avi', 'mkv', 'wmv', 'webm', 'm4v', 'flv')),
    ('audio', '음성 · 음악', ('mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac', 'wma', 'opus')),
)
CATEGORY_LABELS = {key: label for key, label, _exts in CATEGORIES}
# 확장자 → 종류
_EXT_CATEGORY: dict[str, str] = {}
for _key, _label, _exts in CATEGORIES:
    for _ext in _exts:
        _EXT_CATEGORY.setdefault(_ext, _key)

# zip 컨테이너지만 문서/앱인 확장자 (내용이 PK.. 여도 압축 파일로 보지 않는다)
_ZIP_DOCUMENTS = {'docx', 'xlsx', 'xlsb', 'pptx', 'hwpx', 'odt', 'ods', 'odp', 'pages', 'numbers', 'dotx', 'xltx',
                  'potx', 'ppsx', 'docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'pptm', 'potm', 'ppsm', 'ppam', 'sldm'}
_ZIP_EXECUTABLES = {'jar', 'apk', 'appx', 'msix', 'ipa'}
# OLE 복합 문서(D0 CF 11 E0): 옛 오피스 · 한글 문서. msi 도 같은 틀이라 확장자로 가른다
_OLE_EXECUTABLES = {'msi', 'msp'}
# 옛 오피스(doc · xls) 문서에 VBA 매크로가 있으면 OLE 디렉터리에 이 이름(UTF-16)의 항목이 있다 (Excel 은 _VBA_PROJECT_CUR)
_OLE_VBA = '_VBA_PROJECT'.encode('utf-16-le')
_OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_SEALED_MAGIC = (b'7z\xbc\xaf\x27\x1c', b'Rar!\x1a\x07', b'ALZ\x01', b'EGGA')     # 7z · RAR · ALZ · EGG · EGG(알집)
# 이 형태를 막으면 함께 막히는 형태 (압축 파일 → 7z · rar · alz · egg, 문서 → 매크로 문서)
IMPLIED_TYPES = {'archive': 'sealed_archive', 'document': 'macro_document'}

# 2026-11-17 전의 기본 차단 형태 - 그 전에 저장된 정책은 이것들만 '본' 것으로 친다 (merge_new_defaults)
LEGACY_DEFAULT_TYPES = ('executable', 'database', 'credential')
DEFAULT_POLICY: dict[str, Any] = {
    'enabled': True,
    'blocked_types': ['executable', 'macro_document', 'sealed_archive', 'database', 'credential'],
    'blocked_extensions': [],
    'blocked_names': ['*대외비*', '*기밀*', '*confidential*'],
}
MAX_EXTENSIONS = 100
MAX_NAMES = 100
MAX_RULE_LEN = 60
MAX_ZIP_ENTRIES = 250000        # 이름을 보는 항목 수 - 10MB 압축 파일이 담을 수 있는 것보다 많다 (전부 본다)
MAX_ZIP_SNIFF = 5000            # 내용 앞부분까지 보는 항목 수
MAX_ZIP_DEPTH = 2               # 압축 파일 안의 압축 파일을 여는 단계
MAX_NESTED_ARCHIVES = 10        # 안에 든 압축 파일을 여는 개수
MAX_NESTED_BYTES = 20 * 1024 * 1024    # 안에 든 압축 파일을 푸는 크기 합

RULE_TEXT = {'type': '막힌 파일 형태', 'extension': '막힌 확장자', 'name': '막힌 파일 이름'}


class PolicyError(ValueError):
    """관리자가 보낸 정책 값이 올바르지 않음"""


def extension_of(name: str) -> str:
    """'보고서.Tar.GZ' → 'gz' (점 없이 소문자). 확장자가 없으면 ''"""
    base = os.path.basename((name or '').replace('\\', '/')).strip().rstrip('.')
    _stem, ext = os.path.splitext(base)
    return ext[1:].lower()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_category(data: bytes, ext: str = '') -> Optional[str]:
    """파일 내용의 첫 바이트로 본 실제 형태. 모르면 None (평범한 글자 파일 등).
    data 가 파일 전체면 오피스 zip · OLE 문서 안의 매크로도 본다 (앞부분만 받으면 문서 · 압축 파일까지만)"""
    head = data[:16]
    if head.startswith(b'MZ') or head.startswith(b'\x7fELF') or head[:4] in (
            b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
        return 'executable'                       # Windows PE · Linux ELF · macOS Mach-O(유니버설 포함)
    if head.startswith(b'#!'):
        return 'executable'                       # 셸 · 파이썬 등 실행 스크립트
    if head.startswith(b'PK\x03\x04') or head.startswith(b'PK\x05\x06'):
        names = _zip_names(data) if len(data) > len(head) else []
        document = _names_are_document(names)
        if document and _names_have_macros(names):
            return 'macro_document'               # 확장자와 상관없이 (매크로 문서를 .docx · .zip 으로 바꿔도)
        if ext in _ZIP_DOCUMENTS and document:
            return 'document'                     # 진짜 오피스 · 한글 · ODF 문서 (이름만 .docx 인 압축 파일은 아님)
        if ext in _ZIP_EXECUTABLES:
            return 'executable'
        return 'archive'
    if head.startswith(_SEALED_MAGIC):
        return 'sealed_archive'
    if head.startswith((b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00', b'MSCF')):
        return 'archive'
    if head.startswith(b'SQLite format 3\x00') or head.startswith(b'!BDN'):
        return 'database'                         # SQLite · Outlook 메일함(pst)
    if head.startswith(b'-----BEGIN ') or head.startswith(b'PuTTY-User-Key-File'):
        return 'credential'
    if head.startswith(_OLE_MAGIC):
        if ext in _OLE_EXECUTABLES:
            return 'executable'
        return 'macro_document' if _OLE_VBA in data else 'document'
    if head.startswith(b'%PDF'):
        return 'document'
    if head.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF8')) or (head[:2] == b'BM' and head[6:10] == b'\0' * 4) \
            or (head[:4] == b'RIFF' and head[8:12] == b'WEBP'):
        return 'image'
    return None


def category_of(name: str) -> Optional[str]:
    return _EXT_CATEGORY.get(extension_of(name))


def blocked_types(policy: dict) -> set[str]:
    """정책이 막는 형태 (함께 막히는 형태 IMPLIED_TYPES 포함)"""
    types = {t for t in policy.get('blocked_types') or [] if isinstance(t, str)}
    return types | {child for parent, child in IMPLIED_TYPES.items() if parent in types}


def with_defaults_seen(policy: dict) -> dict:
    """저장할 정책에 '지금의 기본 차단 형태를 이미 보았다' 표시를 붙인다 (관리자가 저장할 때 · 시작 때 합친 뒤)"""
    seen = policy.get('defaults_seen')
    seen = [t for t in seen if isinstance(t, str)] if isinstance(seen, list) else list(LEGACY_DEFAULT_TYPES)
    return {**policy, 'defaults_seen': list(dict.fromkeys(seen + DEFAULT_POLICY['blocked_types']))}


def merge_new_defaults(stored: dict) -> tuple[dict, list[str]]:
    """서버가 시작할 때: 저장된 정책에 그 뒤 새로 생긴 기본 차단 형태를 한 번 더한다 → (정책, 새로 막은 형태).
    defaults_seen 에 있는 형태(관리자가 이미 본 기본값 - 그 뒤 뺐으면 뺀 그대로)는 다시 넣지 않는다.
    더할 것이 없으면 stored 를 그대로 돌려준다 (같은 객체)"""
    seen = stored.get('defaults_seen')
    seen = seen if isinstance(seen, list) else list(LEGACY_DEFAULT_TYPES)
    fresh = [t for t in DEFAULT_POLICY['blocked_types'] if t not in seen]
    if not fresh:
        return stored, []
    current = [t for t in stored.get('blocked_types') or [] if t in CATEGORY_LABELS]
    added = [t for t in fresh if t not in current]
    wanted = set(current) | set(fresh)
    merged = with_defaults_seen({**stored, 'blocked_types': [k for k, _l, _e in CATEGORIES if k in wanted]})
    return merged, added


def _clean_rules(values: Any, limit: int, what: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > limit:
        raise PolicyError(f'{what}은(는) {limit}개까지 목록으로 보내 주세요.')
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise PolicyError(f'{what} 값이 올바르지 않습니다.')
        value = value.strip()
        if not value:
            continue
        if len(value) > MAX_RULE_LEN or any(ord(ch) < 32 for ch in value):
            raise PolicyError(f'{what} 값이 올바르지 않습니다: {value[:20]}')
        if value.lower() not in (v.lower() for v in out):
            out.append(value)
    return out


def normalize_policy(raw: Any) -> dict:
    """관리자가 보낸 정책 → 저장할 정책. 잘못되면 PolicyError"""
    if not isinstance(raw, dict):
        raise PolicyError('정책 값이 올바르지 않습니다.')
    enabled = raw.get('enabled', True)
    if not isinstance(enabled, bool):
        raise PolicyError('enabled 는 true/false 여야 합니다.')
    types = _clean_rules(raw.get('blocked_types'), len(CATEGORIES), '막을 형태')
    unknown = [t for t in types if t not in CATEGORY_LABELS]
    if unknown:
        raise PolicyError(f'알 수 없는 파일 형태입니다: {unknown[0]}')
    extensions = [e.lstrip('.').lower() for e in _clean_rules(raw.get('blocked_extensions'), MAX_EXTENSIONS, '막을 확장자')]
    for ext in extensions:
        if not ext or any(ch in ext for ch in '/\\*?. '):
            raise PolicyError(f'확장자는 hwp, zip 처럼 글자만 적어 주세요: {ext or "."}')
    names = _clean_rules(raw.get('blocked_names'), MAX_NAMES, '막을 파일 이름')
    return {'enabled': enabled, 'blocked_types': [k for k, _l, _e in CATEGORIES if k in types],
            'blocked_extensions': list(dict.fromkeys(extensions)), 'blocked_names': names}


def name_matches(name: str, pattern: str) -> bool:
    """와일드카드(* ?)가 있으면 이름 전체와 비교, 없으면 이름에 들어 있는지 (대소문자 무시)"""
    base = os.path.basename((name or '').replace('\\', '/')).lower()
    pattern = (pattern or '').strip().lower()
    if not pattern:
        return False
    if any(ch in pattern for ch in '*?['):
        return fnmatch.fnmatchcase(base, pattern)
    return pattern in base


def _check_name(policy: dict, name: str, inner: bool) -> Optional[dict]:
    """이름 · 확장자 · 확장자로 본 형태만 (압축 안 파일에도 쓴다)"""
    where = f'압축 파일 안 \'{os.path.basename(name)}\'' if inner else ''
    ext = extension_of(name)
    for pattern in policy.get('blocked_names') or []:
        if name_matches(name, pattern):
            return {'rule': 'name', 'value': pattern,
                    'detail': f"{where + ': ' if where else ''}이름에 '{pattern.strip('*')}' 이(가) 들어 있어요"}
    if ext and ext in (policy.get('blocked_extensions') or []):
        return {'rule': 'extension', 'value': ext, 'detail': f"{where + ': ' if where else ''}.{ext} 파일은 보낼 수 없어요"}
    category = _EXT_CATEGORY.get(ext)
    if category and category in blocked_types(policy):
        return {'rule': 'type', 'value': category,
                'detail': f"{where + ': ' if where else ''}{CATEGORY_LABELS[category]} 형태는 보낼 수 없어요"}
    return None


# 오피스(OOXML) · ODF · 한글(hwpx) 문서 zip 안에 반드시 있는 항목
_ZIP_DOCUMENT_MARKERS = ('[Content_Types].xml', 'mimetype', 'META-INF/manifest.xml')


def _names_are_document(names: list[str]) -> bool:
    found = set(names)
    return any(marker in found for marker in _ZIP_DOCUMENT_MARKERS)


def _names_have_macros(names: list[str]) -> bool:
    """오피스(OOXML) 문서 zip 의 항목 이름으로 본 매크로: VBA 프로젝트(word/ · xl/ · ppt/vbaProject.bin) · 엑셀 4.0 매크로 시트"""
    for name in names:
        low = name.lower()
        if low.rsplit('/', 1)[-1] == 'vbaproject.bin' or low.startswith('xl/macrosheets/'):
            return True
    return False


def _zip_names(data: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return [info.filename for info in zf.infolist()[:MAX_ZIP_ENTRIES] if not info.is_dir()]
    except (zipfile.BadZipFile, ValueError, OSError, RuntimeError, NotImplementedError, EOFError):
        return []


_PROGRAM_MAGIC = (b'MZ', b'\x7fELF', b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
                  b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')
_ZIP_READ_ERRORS = (zipfile.BadZipFile, ValueError, OSError, RuntimeError, NotImplementedError, EOFError, zlib.error)


def _check_zip(policy: dict, data: bytes, document: bool = False, depth: int = 0,
               budget: Optional[dict] = None) -> Optional[dict]:
    """zip 안을 본다: 든 파일의 이름 · 내용 앞부분 · 그 안의 zip (MAX_ZIP_DEPTH 단계까지).
    document=True(오피스 · 한글 문서)면 이름 규칙은 적용하지 않고, 실행 파일이 들어 있는지만 본다.
    암호가 걸렸거나 깨진 항목은 내용을 볼 수 없어 이름만 본다"""
    budget = budget if budget is not None else {'archives': MAX_NESTED_ARCHIVES, 'bytes': MAX_NESTED_BYTES}
    blocked = blocked_types(policy)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except _ZIP_READ_ERRORS:
        return None
    with zf:
        infos = [info for info in zf.infolist()[:MAX_ZIP_ENTRIES] if not info.is_dir()]
        if not document:
            for info in infos:
                found = _check_name(policy, info.filename, inner=True)
                if found:
                    return found
        for info in infos[:MAX_ZIP_SNIFF]:
            try:
                with zf.open(info) as member:
                    head = member.read(16)
            except _ZIP_READ_ERRORS:
                continue
            base = os.path.basename(info.filename)
            if document:
                sniffed = 'executable' if head.startswith(_PROGRAM_MAGIC) else None
            else:
                sniffed = sniff_category(head, extension_of(info.filename))
            if sniffed and sniffed != 'archive' and sniffed in blocked:
                return {'rule': 'type', 'value': sniffed,
                        'detail': f"압축 파일 안 '{base}': 실제 내용은 {CATEGORY_LABELS[sniffed]} 형태예요"}
            if head.startswith(_OLE_MAGIC) and 'macro_document' in blocked \
                    and extension_of(info.filename) not in _OLE_EXECUTABLES and info.file_size <= budget['bytes']:
                budget['bytes'] -= info.file_size           # 옛 오피스 문서는 통째로 읽어야 매크로가 보인다
                try:
                    whole = zf.read(info)
                except _ZIP_READ_ERRORS:
                    continue
                if _OLE_VBA in whole:
                    return {'rule': 'type', 'value': 'macro_document',
                            'detail': f"압축 파일 안 '{base}': 실제 내용은 {CATEGORY_LABELS['macro_document']} 형태예요"}
            if head.startswith(b'PK\x03\x04') and depth + 1 < MAX_ZIP_DEPTH and budget['archives'] > 0 \
                    and info.file_size <= budget['bytes']:
                budget['archives'] -= 1
                budget['bytes'] -= info.file_size
                try:
                    inner = zf.read(info)
                except _ZIP_READ_ERRORS:
                    continue
                inner_names = _zip_names(inner)
                if 'macro_document' in blocked and _names_are_document(inner_names) and _names_have_macros(inner_names):
                    return {'rule': 'type', 'value': 'macro_document',
                            'detail': f"압축 파일 안 '{base}': 실제 내용은 {CATEGORY_LABELS['macro_document']} 형태예요"}
                inner_document = extension_of(info.filename) in _ZIP_DOCUMENTS and _names_are_document(inner_names)
                found = _check_zip(policy, inner, inner_document, depth + 1, budget)
                if found:
                    return found
    return None


def check(policy: Optional[dict], name: str, data: bytes) -> Optional[dict]:
    """정책 위반이면 {rule, value, detail, category}, 괜찮으면 None.

    rule: 'name'(파일 이름) · 'extension'(확장자) · 'type'(형태 - 확장자 또는 실제 내용)
    """
    policy = policy or DEFAULT_POLICY
    if not policy.get('enabled'):
        return None
    ext = extension_of(name)
    sniffed = sniff_category(data, ext)
    blocked = blocked_types(policy)
    found = _check_name(policy, name, inner=False)
    if found is None and sniffed and sniffed in blocked:
        claimed = _EXT_CATEGORY.get(ext)
        disguised = claimed != sniffed
        found = {'rule': 'type', 'value': sniffed,
                 'detail': (f"확장자는 .{ext or '(없음)'} 이지만 실제 내용은 {CATEGORY_LABELS[sniffed]} 형태예요"
                            if disguised else f"{CATEGORY_LABELS[sniffed]} 형태는 보낼 수 없어요")}
    if found is None and data[:2] == b'PK' and (sniffed in ('archive', 'document', 'macro_document') or ext == 'zip'):
        found = _check_zip(policy, data, document=sniffed in ('document', 'macro_document'))
    if found is not None:
        found['category'] = sniffed or _EXT_CATEGORY.get(ext) or ''
        # 이름이 말하는 형태와 실제 내용이 다르고, 그 내용이 막힌 형태인가 (이름 예외로 풀어 주면 안 되는 파일)
        found['disguised'] = bool(sniffed and sniffed != _EXT_CATEGORY.get(ext) and sniffed in blocked)
    return found


def public_policy(policy: Optional[dict]) -> dict:
    """클라이언트에 보낼 정책 + 형태 목록 (관리자 화면 · 보내기 전 안내용)"""
    policy = policy or DEFAULT_POLICY
    return {**{k: policy.get(k, DEFAULT_POLICY[k]) for k in DEFAULT_POLICY},
            'categories': [{'key': k, 'label': label, 'extensions': list(exts)} for k, label, exts in CATEGORIES]}
