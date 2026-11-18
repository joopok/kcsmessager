# -*- coding: utf-8 -*-
"""
서버 데이터 백업 - 서버가 켜져 있어도 안전하다 (SQLite 백업 기능으로 한 시점의 내용을 통째로 복사).

    python3 scripts/backup_db.py                               # server_data/ → server_data/backups/<날짜-시각>/
    python3 scripts/backup_db.py --db /경로/kakao.db --out /백업/폴더 --keep 30

한 번 실행하면 <out>/<YYYYmmdd-HHMMSS>/ 폴더에 다음을 둔다
  kakao.db            대화 · 계정 · 첨부 (가장 중요)
  rag.db              AI 비서 색인 (있으면)
  portal_outbox.db    포털로 보낼 대화 기록 (있으면)
  org.json            조직도 (있으면)
복사한 DB 는 열어서 검사(quick_check)하고, 하나라도 실패하면 그 폴더를 지우고 종료 코드 1.
폴더는 최근 --keep 개(기본 14)만 남긴다. 백업 파일은 본인만 읽을 수 있게(0600) 만든다.
portal.json(포털 토큰)은 복사하지 않는다 - 따로 안전한 곳에 보관한다.

되돌리기: 서버를 멈추고 백업 폴더의 파일을 server_data/ 에 덮어쓴 뒤(kakao.db-wal · kakao.db-shm 은 지운다) 서버를 켠다.
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, 'server_data', 'kakao.db')
EXTRA_DBS = ('rag.db', 'portal_outbox.db')
EXTRA_FILES = ('org.json',)
STAMP = '%Y%m%d-%H%M%S'


def copy_database(source, target):
    """SQLite 백업 기능으로 복사 → 복사본 검사. 쓰는 중인 DB 도 한 시점의 내용으로 온전히 복사된다"""
    src = sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True, timeout=30)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')       # 복사본은 파일 하나로 (wal/shm 없이)
            dst.commit()
            verdict = dst.execute('PRAGMA quick_check').fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if verdict != 'ok':
        raise sqlite3.DatabaseError(f'복사본 검사 실패: {verdict}')
    os.chmod(target, 0o600)


def is_backup_folder(name):
    try:
        time.strptime(name, STAMP)
    except ValueError:
        return False
    return True


def prune(out_dir, keep):
    """날짜-시각 이름의 폴더 중 최근 keep 개만 남긴다 → 지운 폴더 이름"""
    folders = sorted(name for name in os.listdir(out_dir)
                     if is_backup_folder(name) and os.path.isdir(os.path.join(out_dir, name)))
    removed = folders[:-keep] if keep > 0 else []
    for name in removed:
        shutil.rmtree(os.path.join(out_dir, name), ignore_errors=True)
    return removed


def backup(db_path, out_dir, keep=14, stamp=None):
    """백업 한 번 → 만든 폴더 경로. 실패하면 만든 폴더를 지우고 예외를 올린다"""
    data_dir = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(out_dir, exist_ok=True)
    folder = os.path.join(out_dir, stamp or time.strftime(STAMP))
    os.makedirs(folder, exist_ok=False)
    try:
        os.chmod(folder, 0o700)
        copy_database(os.path.abspath(db_path), os.path.join(folder, 'kakao.db'))
        for name in EXTRA_DBS:
            source = os.path.join(data_dir, name)
            if os.path.isfile(source):
                copy_database(source, os.path.join(folder, name))
        for name in EXTRA_FILES:
            source = os.path.join(data_dir, name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(folder, name))
                os.chmod(os.path.join(folder, name), 0o600)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    prune(out_dir, keep)
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description='KCS Talk 서버 데이터 백업 (가동 중에도 안전)')
    parser.add_argument('--db', default=DEFAULT_DB, help=f'SQLite DB 경로 (기본: {DEFAULT_DB})')
    parser.add_argument('--out', default=None, help='백업 폴더 (기본: DB 폴더의 backups/)')
    parser.add_argument('--keep', type=int, default=14, help='남길 백업 수 (기본 14)')
    args = parser.parse_args(argv)
    if not os.path.isfile(args.db):
        print(f'DB 파일이 없습니다: {args.db}', file=sys.stderr)
        return 1
    if args.keep < 1:
        print('--keep 은 1 이상이어야 합니다.', file=sys.stderr)
        return 1
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.db)), 'backups')
    try:
        folder = backup(args.db, out_dir, args.keep)
    except (OSError, sqlite3.Error) as exc:
        print(f'백업 실패: {exc}', file=sys.stderr)
        return 1
    size = sum(os.path.getsize(os.path.join(folder, name)) for name in os.listdir(folder))
    print(f'백업 완료 → {folder} ({size / 1024 / 1024:.1f}MB, 최근 {args.keep}개 보관)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
