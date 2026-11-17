#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
회사 연락처/조직도 엑셀의 좌석배치도 사진 → KCS Talk 프로필 기본 사진(avatar_id)

    python3 scripts/import_org_photos.py "~/Downloads/연락처 및 조직도(20260901).xlsx" --dry-run
    python3 scripts/import_org_photos.py "~/Downloads/연락처 및 조직도(20260901).xlsx"
    python3 scripts/import_org_photos.py 조직도.xlsx --force      # 본인이 올린 사진도 엑셀 사진으로 바꾼다

엑셀 형식('3층(관리)', '3층(영업)', '4층', '6층' 같은 좌석배치도 시트): 사진 바로 아래(같은 열, 3~6행 아래)
셀에 '직급 이름\\n내선'이 적혀 있다(이름의 '(*)'는 겸직 표시). 같은 사람이 여러 층에 나오면 첫 사진을 쓴다.
사진은 가운데 위쪽 기준 정사각형으로 잘라 256px JPEG 로 저장한다(서버 한도 512KB, 명세 §3 profile_update).
EMF 사진은 안에 든 비트맵(EMR_STRETCHDIBITS)을 꺼내 쓴다.

사진은 구성원의 조직도 사진(users.org_avatar_id)으로 저장되고, 프로필 편집의 [기본 이미지]를 누르면 이 사진으로
돌아간다(명세 §7.2). 지금 프로필 사진은 사진이 없거나 예전 조직도 사진 그대로인 사람만 바꾼다(본인이 직접 올린
사진은 그대로, --force 면 모두). 같은 사진을 다시 가져오면 새로 저장하지 않는다. 서버가 켜져 있어도 된다(SQLite).
접속 중인 사람의 화면에는 다시 로그인하면 보인다.
"""

import argparse
import io
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.chat.chat_server import DEFAULT_DB_PATH  # noqa: E402
from server.kakao.store import Store  # noqa: E402

NS = {'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL_ID = '{%s}id' % NS['r']
REL_EMBED = '{%s}embed' % NS['r']
AVATAR_PX = 256
NAME_ROWS = range(3, 7)      # 사진 왼쪽 위 행에서 이름 셀까지 행 차이
NAME_COLS = range(-1, 3)     # 사진 왼쪽 열에서 이름 셀까지 열 차이
NAME_CELL = re.compile(r'^\s*\S+?(?:\(\*\))?\s+([가-힣A-Za-z]{2,10})(?:\(\*\))?\s*(?:\n|$)')


def _rels(zf, path):
    """xl/.../x.xml 의 관계 파일 → {Id: 대상 경로(zip 안 절대 경로)}"""
    folder, name = os.path.split(path)
    rels_path = f'{folder}/_rels/{name}.rels'
    if rels_path not in zf.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(zf.read(rels_path)):
        target = rel.get('Target')
        out[rel.get('Id')] = os.path.normpath(os.path.join(folder, target)) if not target.startswith('/') \
            else target.lstrip('/')
    return out


def sheet_paths(zf):
    """[(시트 이름, xl/worksheets/sheetN.xml)]"""
    book = ET.fromstring(zf.read('xl/workbook.xml'))
    rels = _rels(zf, 'xl/workbook.xml')
    main = book.tag.split('}')[0] + '}'
    return [(s.get('name'), rels[s.get(REL_ID)]) for s in book.iter(main + 'sheet')]


def sheet_pictures(zf, sheet_path):
    """시트에 붙은 그림 → [(왼쪽 위 열, 행, 미디어 경로, 자르기(l, t, r, b) 비율)]"""
    drawings = [p for p in _rels(zf, sheet_path).values() if '/drawings/' in p]
    pics = []
    for drawing in drawings:
        media = _rels(zf, drawing)
        for anchor in ET.fromstring(zf.read(drawing)):
            start = anchor.find('xdr:from', NS)
            if start is None:
                continue
            for pic in anchor.iter('{%s}pic' % NS['xdr']):
                blip = pic.find('.//a:blip', NS)
                if blip is not None and blip.get(REL_EMBED) in media:
                    src = pic.find('.//a:srcRect', NS)     # 엑셀에서 잘라 보여 주는 부분 (1/100000 단위)
                    crop = tuple(int((src.get(k) if src is not None else None) or 0) / 100000
                                 for k in ('l', 't', 'r', 'b'))
                    pics.append((int(start.find('xdr:col', NS).text), int(start.find('xdr:row', NS).text),
                                 media[blip.get(REL_EMBED)], crop))
    return pics


def match_photos(xlsx_path):
    """{이름: (미디어 경로, 자르기, '시트!셀')} - 좌석배치도 시트에서 사진과 그 아래 이름 셀을 짝짓는다"""
    import warnings
    warnings.filterwarnings('ignore', category=UserWarning)   # wmf 이미지 경고
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    found = {}
    with zipfile.ZipFile(xlsx_path) as zf:
        for sheet_name, path in sheet_paths(zf):
            pics = sheet_pictures(zf, path)
            if len(pics) < 2:          # 사진이 없거나 로고 하나뿐인 시트
                continue
            ws = wb[sheet_name]
            names = {}                 # (행, 열) 0부터 → 이름
            for row in ws.iter_rows():
                for cell in row:
                    m = NAME_CELL.match(str(cell.value or ''))
                    if m:
                        names[(cell.row - 1, cell.column - 1)] = (m.group(1), cell.coordinate)
            for col, row, media, crop in pics:
                hits = [names[(row + dr, col + dc)] for dr in NAME_ROWS for dc in NAME_COLS
                        if (row + dr, col + dc) in names]
                if hits:
                    name, coord = hits[0]
                    found.setdefault(name, (media, crop, f'{sheet_name}!{coord}'))
    return found


def emf_bitmap(data):
    """EMF 안의 첫 EMR_STRETCHDIBITS 비트맵 → BMP 바이트 (없으면 None)"""
    pos = 0
    while pos + 8 <= len(data):
        kind, size = struct.unpack_from('<II', data, pos)
        if size < 8:
            break
        if kind == 81:   # EMR_STRETCHDIBITS
            off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from('<4I', data, pos + 48)
            bmi = data[pos + off_bmi:pos + off_bmi + cb_bmi]
            bits = data[pos + off_bits:pos + off_bits + cb_bits]
            header = b'BM' + struct.pack('<IHHI', 14 + len(bmi) + len(bits), 0, 0, 14 + len(bmi))
            return header + bmi + bits
        pos += size
    return None


def to_avatar(raw, media_path, crop=(0, 0, 0, 0)):
    """원본 이미지 → (엑셀 자르기 반영 후) 가운데 위쪽 기준 정사각형 256px JPEG 바이트"""
    from PIL import Image
    if media_path.lower().endswith('.emf'):
        raw = emf_bitmap(raw)
        if raw is None:
            raise ValueError('EMF 안에 비트맵이 없습니다')
    img = Image.open(io.BytesIO(raw))
    img = img.convert('RGBA') if img.mode in ('P', 'LA') else img
    if img.mode == 'RGBA':     # 투명 배경은 흰색으로
        flat = Image.new('RGB', img.size, 'white')
        flat.paste(img, mask=img.split()[3])
        img = flat
    img = img.convert('RGB')
    w, h = img.size
    l, t, r, b = crop
    img = img.crop((round(w * l), round(h * t), round(w * (1 - r)), round(h * (1 - b))))
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = 0 if h > w else (h - side) // 2    # 세로 사진은 얼굴이 있는 위쪽을 남긴다
    img = img.crop((left, top, left + side, top + side)).resize((AVATAR_PX, AVATAR_PX), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=90)
    return out.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description='조직도 엑셀 좌석배치도 사진 → KCS Talk 프로필 기본 사진')
    ap.add_argument('xlsx')
    ap.add_argument('--db', default=DEFAULT_DB_PATH, help=f'SQLite DB 경로 (기본: {DEFAULT_DB_PATH})')
    ap.add_argument('--force', action='store_true', help='이미 사진이 있는 사용자도 엑셀 사진으로 바꾼다')
    ap.add_argument('--dry-run', action='store_true', help='짝지은 결과만 보여 주고 저장하지 않는다')
    args = ap.parse_args(argv)
    xlsx = os.path.expanduser(args.xlsx)

    photos = match_photos(xlsx)
    store = Store.open_for_tools(args.db)      # 가동 중인 서버의 DB 구조 · 조직도 시드는 건드리지 않는다
    applied, kept, missing = [], [], []
    try:
        with zipfile.ZipFile(xlsx) as zf:
            for name, (media, crop, where) in sorted(photos.items()):
                username = store.resolve_login(name)
                if username is None or store.is_bot(username):
                    missing.append(f'{name}({where})')
                    continue
                data = to_avatar(zf.read(media), media, crop)
                current = (store.get_user(username) or {}).get('avatar_id')
                old_org = store.org_avatar(username)
                # 같은 사진이 이미 있으면(예전에 가져온 것) 그 첨부를 조직도 사진으로 쓴다
                same = [a for a in (old_org, current) if a and (store.get_attachment(a) or {}).get('data') == data]
                replace = args.force or current is None or current == old_org or current in same
                if not args.dry_run:
                    org_id = same[0] if same else store.save_attachment('avatar.jpg', 'image/jpeg', data, username)
                    store.set_org_avatar(username, org_id)
                    if replace:
                        store.update_profile(username, {'avatar_id': org_id})
                (applied if replace else kept).append(f'{name}({where}, {len(data) // 1024}KB)' if replace else name)
    finally:
        store.close()

    print(f"{'[미리보기] ' if args.dry_run else ''}조직도 사진 {len(applied) + len(kept)}명 저장, "
          f"프로필 사진으로 적용 {len(applied)}명: " + ', '.join(applied))
    if kept:
        print(f'본인이 올린 사진이 있어 프로필은 그대로 둠 {len(kept)}명 ([기본 이미지]를 누르면 조직도 사진, '
              f'--force 로 바로 교체): ' + ', '.join(kept))
    if missing:
        print(f'DB에 없는 이름 {len(missing)}명: ' + ', '.join(missing))
    return 0


if __name__ == '__main__':
    sys.exit(main())
