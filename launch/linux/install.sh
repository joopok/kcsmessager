#!/bin/sh
# KCS Talk 처음 설치: 패키지 설치 + 점검 + 앱 목록 등록(.desktop)
# Qt 화면 라이브러리 (Ubuntu/Debian): sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0 fonts-noto-color-emoji
cd "$(dirname "$0")/../.." || exit 1
python3 -m pip install --user -r requirements-kcs.txt && python3 kcs.py doctor && python3 kcs.py shortcut \
  && echo "설치 완료. 앱 목록에서 'KCS Talk' 를 찾아 실행하세요."
