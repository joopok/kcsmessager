# -*- coding: utf-8 -*-
"""
KCS Talk 앱 아이콘 생성 - client/kakao/brand.paint_app_icon 으로 그려서 파일로 내보낸다.

출력:
  assets/icon/app_icon_{16..1024}.png   크기별 PNG
  assets/icon.ico                       Windows exe 아이콘 (kakaotalk_launcher_windows.spec 가 사용)
  assets/icon.icns                      macOS 앱 아이콘 (iconutil 이 없으면 Pillow 로)

사용: python scripts/make_app_icon.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT_DIR = os.path.join(ROOT, 'assets', 'icon')


def render(size):
    from client.kakao import brand
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    brand.paint_app_icon(p, size)
    p.end()
    return img


def main():
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 (살아 있어야 QPixmap 사용 가능)
    from client.kakao import theme
    theme.set_dark(False)
    theme.set_brand('kcs')
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = {}
    for s in SIZES:
        paths[s] = os.path.join(OUT_DIR, f'app_icon_{s}.png')
        if not render(s).save(paths[s]):
            sys.exit(f'저장 실패: {paths[s]}')
    print(f'PNG {len(SIZES)}개 → {os.path.relpath(OUT_DIR, ROOT)}')

    from PIL import Image
    ico = os.path.join(ROOT, 'assets', 'icon.ico')
    frames = [Image.open(paths[s]).convert('RGBA') for s in ICO_SIZES]
    frames[-1].save(ico, format='ICO', sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1])
    print(f'ICO → {os.path.relpath(ico, ROOT)}')

    icns = os.path.join(ROOT, 'assets', 'icon.icns')
    if not shutil.which('iconutil'):
        # macOS 가 아니면 Pillow 로 ICNS 를 쓴다 (16~1024 전 크기, PNG 압축)
        Image.open(paths[1024]).convert('RGBA').save(icns, format='ICNS', append_images=[
            Image.open(paths[s]).convert('RGBA') for s in (16, 32, 64, 128, 256, 512)])
        print(f'ICNS (Pillow) → {os.path.relpath(icns, ROOT)}')
    else:
        with tempfile.TemporaryDirectory() as tmp:
            iconset = os.path.join(tmp, 'app.iconset')
            os.makedirs(iconset)
            for s in (16, 32, 128, 256, 512):
                shutil.copy(paths[s], os.path.join(iconset, f'icon_{s}x{s}.png'))
                shutil.copy(paths[s * 2] if s * 2 in paths else paths[1024],
                            os.path.join(iconset, f'icon_{s}x{s}@2x.png'))
            subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', icns], check=True)
        print(f'ICNS → {os.path.relpath(icns, ROOT)}')


if __name__ == '__main__':
    main()
