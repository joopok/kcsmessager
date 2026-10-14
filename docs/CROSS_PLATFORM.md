# KCS Talk - Windows · macOS · Linux 공통 사용 안내

KCS Talk(메신저 `client/kakao/` + 서버 `server/kakao/`)는 세 OS에서 같은 코드, 같은 화면, 같은 명령으로 돌아갑니다.
OS마다 다른 부분은 `client/kakao/osenv.py` 한 파일에만 있습니다.

## 1. 처음 설치 (한 번만)

필요한 것: Python 3.8 이상 (Windows는 python.org 설치 프로그램에서 "Add python.exe to PATH" 체크)

| OS | 더블클릭 | 직접 입력 |
|---|---|---|
| Windows | `launch\windows\install.bat` | `py -m pip install -r requirements-kcs.txt` |
| macOS | `launch/macos/install.command` | `python3 -m pip install -r requirements-kcs.txt` |
| Linux | `launch/linux/install.sh` | `python3 -m pip install --user -r requirements-kcs.txt` |

설치 파일은 패키지를 설치하고 `kcs.py doctor`로 점검한 뒤 바로가기를 만듭니다
(Windows: 바탕화면 + 시작 메뉴, macOS: 바탕화면, Linux: 앱 목록).

Linux(Ubuntu/Debian)에서 Qt 창이 뜨지 않으면:
`sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0 fonts-noto-color-emoji`

## 2. 실행

모든 OS 공통 명령 (Windows는 `python3` 대신 `py`):

```
python3 kcs.py                       메신저 (= kcs.py client)
python3 kcs.py client --host 서버주소 --port 5555
python3 kcs.py client --host talk.kcins.co.kr --port 9550 --tls   운영 서버 (TLS). server.json 에 "tls": true 로 저장해 두면 생략
python3 kcs.py server                채팅 서버 (이 PC에서만)
python3 kcs.py server --host 0.0.0.0 다른 PC에서 접속 허용
python3 kcs.py doctor                이 PC 점검 (필수 패키지 · 글꼴 · 데이터 폴더)
python3 kcs.py shortcut              바로가기 다시 만들기
python3 kcs.py client --self-check   창 없이 OS · 글꼴 · 로고 · 데이터 폴더 점검
```

더블클릭 파일: `launch/windows/KCS_Talk.bat` · `launch/macos/KCS Talk.command` · `launch/linux/kcs-talk.sh`
(서버는 각 폴더의 `…Server…` 파일)

예전 명령 `python3 client/kakao/app.py`, `python3 server/chat/chat_server.py`도 그대로 됩니다.

## 3. 실행 파일 만들기 (설치 없이 나눠 줄 때)

PyInstaller는 교차 빌드를 하지 않습니다. **배포할 OS에서 그 OS용을 만듭니다.**

```
python3 -m pip install pyinstaller
python3 kcs.py build                 메신저
python3 kcs.py build --server        서버 (콘솔)
python3 kcs.py build --dry-run       명령만 확인
```

| OS | 결과 | 아이콘 |
|---|---|---|
| macOS | `dist/KCS Talk.app` (번들 ID `kr.co.kcs.talk`) | `assets/icon.icns` |
| Windows | `dist\KCS Talk\KCS Talk.exe` | `assets/icon.ico` |
| Linux | `dist/KCS Talk/KCS Talk` + `kr.co.kcs.talk.desktop` | `assets/icon/app_icon_256.png` |
| 서버 (모든 OS) | `dist/KCS Talk Server/` - DB는 실행 파일 옆 `server_data/` | - |

만든 앱 확인: `"dist/KCS Talk.app/Contents/MacOS/KCS Talk" --self-check` (Windows: `"dist\KCS Talk\KCS Talk.exe" --self-check`)

### macOS 배포용 DMG (포털 등으로 나눠 줄 때)

```
python3 kcs.py build --dmg           앱 → 서명 → dist/YYYYMMDDHHMM_kcsmessager.dmg (→ 공증 · 스테이플)
```

인터넷(포털 · 메일 · 메신저)으로 받은 앱은 macOS Gatekeeper가 **"Apple은 'KCS Talk.app'에 … 악성 코드가 없음을
확인할 수 없습니다"** 로 막습니다. 원인은 앱이 ad-hoc 서명(Team ID 없음)이고 Apple 공증을 받지 않았기 때문입니다
(`spctl -a -vv -t exec "KCS Talk.app"` → `rejected`). 받은 Mac에서 막히지 않게 하려면:

1. Apple Developer Program(회사 계정)에서 **Developer ID Application** 인증서를 받아 빌드 Mac 키체인에 설치
   (`Apple Development` 인증서는 배포용이 아니라 소용없습니다).
2. 공증 자격을 한 번 저장: `xcrun notarytool store-credentials kcs-notary --apple-id <id> --team-id <TEAMID> --password <앱 암호>`
3. 빌드:
   ```
   KCS_SIGN_IDENTITY="Developer ID Application: 회사명 (TEAMID)" KCS_NOTARY_PROFILE=kcs-notary python3 kcs.py build --dmg
   ```
   hardened runtime + `assets/macos/entitlements.plist`(Python · 카메라 · 마이크)로 서명 → DMG 서명 → 공증 → 스테이플 →
   `spctl` 판정까지 하고 결과를 출력합니다.

**인증서 없이 지금 바로 경고 없이 설치하는 방법 (권장 배포):** `--dmg` 는 DMG 옆에 `install_kcstalk.sh` 도 만듭니다.
둘 다 포털의 같은 폴더(기본 `https://portal.kcins.co.kr/downloads/kcstalk/`, 다르면 빌드 때 `KCS_DOWNLOAD_URL`)에 올리고
Mac 사용자에게는 터미널에 아래 한 줄을 붙여 넣으라고 안내합니다.

```
curl -fsSL https://portal.kcins.co.kr/downloads/kcstalk/install_kcstalk.sh | bash
```

Gatekeeper는 브라우저 · 메일 · 메신저로 받은 파일(다운로드 표시 `com.apple.quarantine` 가 붙은 파일)만 검사합니다.
스크립트는 DMG를 `curl` 로 받아 SHA-256을 확인하고 `/Applications` 에 설치한 뒤 실행하므로 위 경고 창이 뜨지 않습니다
(기존 앱은 교체, 필요하면 Mac 암호를 묻습니다).

인증서가 없으면 ad-hoc 서명 DMG를 만들고 경고를 출력합니다. 브라우저로 DMG를 받은 사람은 DMG 안의 `설치 안내.txt` 대로 처음 한 번
**시스템 설정 → 개인정보 보호 및 보안 → 'KCS Talk.app이(가) 차단되었습니다' 옆 [그래도 열기]** (또는 터미널
`xattr -dr com.apple.quarantine "/Applications/KCS Talk.app"`)로 열어야 합니다.

## 4. OS마다 같게 맞춘 것 · 다르게 둔 것

| 항목 | 처리 |
|---|---|
| 화면 모양 | Qt `Fusion` 스타일 + 앱에 든 Pretendard 글꼴 → 세 OS 같은 모양 |
| 한글 글꼴 예비 순서 | Pretendard → macOS Apple SD Gothic Neo / Windows 맑은 고딕 / Linux Noto Sans CJK KR |
| 이모지 | macOS Apple Color Emoji / Windows Segoe UI Emoji / Linux Noto Color Emoji |
| 사용자 데이터 | 모든 OS `~/.kcs_messenger` (예전 설치와 같은 폴더). `KCS_MESSENGER_HOME`으로 바꿀 수 있음 |
| 다운로드 · 문서 · 사진 폴더 | OS가 알려 주는 위치 (한글 이름 폴더 포함), 없으면 홈 |
| 파일 · 링크 열기 | Qt 기본 앱 열기 → 안 되면 `open` / `os.startfile` / `xdg-open` |
| 단축키 | 키 연결은 모두 `Ctrl+…` (macOS에서는 Qt가 ⌘로 연결). 화면 글자는 macOS `⇧⌘Enter`, 그 밖 `Ctrl+Shift+Enter` |
| 앱 이름 표시 | macOS: Dock · 메뉴 막대 이름 (소스 실행은 데이터 폴더의 `KCS Talk.app` 번들에서 다시 실행) / Windows: 작업 표시줄 묶음(AppUserModelID) / Linux: `.desktop` 연결 |
| 받은 파일 이름 | 다른 OS에서 온 이름도 저장되게 `osenv.safe_filename` (Windows 금지 글자 `: * ? " < > \|` → `_`, CON · NUL 같은 예약 이름 피하기, 경로 떼기) |
| 비밀 파일 권한 | macOS · Linux 600, Windows는 사용자 프로필 폴더 권한 |
| 다시 실행(로그아웃) | 소스 실행이면 파이썬 + 스크립트, 빌드한 앱이면 실행 파일 그대로 |

## 5. 개발 규칙

- `client/kakao/`에서 `sys.platform`, `os.name`, `platform.system()`을 직접 쓰지 않습니다. `osenv`를 씁니다
  (`tests/test_kakao_osenv.py`가 검사). macOS 전용 보정은 `macos.py`에 있습니다.
- 데이터 경로는 `osenv.app_home()` / `osenv.app_path(...)`, 앱에 든 파일은 `osenv.resource_path('assets', ...)`.
- 보이는 단축키 글자는 `osenv.shortcut_text('Ctrl+…')`, 저장할 파일 이름은 `osenv.safe_filename(...)`.
- 새 OS 차이가 생기면 `osenv.py`에 함수를 더하고, 세 OS를 흉내 내는 테스트를 `tests/test_kakao_osenv.py`에 더합니다.
- `.gitattributes`가 줄끝을 고정합니다: `.bat` CRLF, `.sh` · `.command` LF.

## 6. 확인한 범위

- macOS(arm64): 전체 테스트, `kcs.py doctor`, PyInstaller로 `.app`과 서버를 실제로 빌드했고, 빌드한 앱 `--self-check`와
  빌드한 서버 접속까지 확인했습니다.
- Windows · Linux: OS 차이를 흉내 낸 단위 테스트(경로, 단축키, 글꼴, 빌드 인자, 바로가기, 작업 표시줄 ID)로 확인했습니다.
  실제 Windows · Linux PC에서는 `kcs.py doctor` → `kcs.py client --self-check` → `kcs.py build`로 한 번 확인해 주세요.
