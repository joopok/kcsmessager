# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KCS Messenger is a KakaoTalk clone written in Python using PyQt5. It provides both standalone and real-time chat capabilities with a Korean-style UI.

## Project Structure

The codebase follows an organized client-server architecture:

```
client/
├── standalone/     # Offline chat UI (no server needed)
├── realtime/      # Network-enabled chat client
└── launcher/      # GUI launcher for managing server & clients

server/
├── chat/          # TCP socket chat server
└── core/          # Async server implementation

tests/             # Test suite
```

## KCS Talk 2.0 (current client/server)

The maintained implementation is `client/kakao/` (PyQt5 client) + `server/kakao/` (TCP NDJSON server, SQLite, RAG bot).
- Protocol & feature contract: `claudedocs/KAKAO_FEATURE_SPEC.md` — server, client `AppState`, and tests all follow it; update it first when changing the protocol.
- UI never touches sockets: widgets call `client/kakao/state.py` (`AppState`) actions and listen to its signals.
- Colors/fonts only via `client/kakao/theme.py`; icons/avatars via `client/kakao/widgets.py` (QPainter, no image assets).
- Product name shown to users is **KCS Talk**. Company CI (㈜케이씨에스) assets live in `assets/brand/`; render them only via `client/kakao/brand.py`. Default accent theme is `kcs` (navy #2A386C); `theme.c('yellow')` means "primary accent" and text on it must use `theme.c('on_accent')`, my-bubble text `theme.c('bubble_mine_text')` — never hardcode `#191919` on accent colors.
- Run (same on Windows · macOS · Linux; Windows uses `py`): `python3 kcs.py server` then `python3 kcs.py` (client). `kcs.py doctor` checks the PC, `kcs.py build [--server] [--dmg]` makes this OS's executable (PyInstaller; `--dmg` = signed macOS DMG, notarized when `KCS_SIGN_IDENTITY` (Developer ID) + `KCS_NOTARY_PROFILE` are set — otherwise Gatekeeper blocks downloaded copies), `kcs.py shortcut` adds desktop/start-menu/app-list shortcuts; double-click launchers in `launch/{windows,macos,linux}/`; deps in `requirements-kcs.txt`. Old entry points (`client/kakao/app.py`, `server/chat/chat_server.py`) still work. User guide: `docs/KCS_TALK_GUIDE.md`, OS guide: `docs/CROSS_PLATFORM.md`.
- OS differences live only in `client/kakao/osenv.py` (data dir `app_home()`, `resource_path()` for assets incl. frozen builds, `user_dir()`, `open_path/open_url`, font/emoji chains, `shortcut_text()`, `prepare_process()/configure_app()`). Never use `sys.platform`/`os.name` elsewhere in `client/kakao/` (`tests/test_kakao_osenv.py` enforces; macOS-only ctypes tweaks stay in `macos.py`).
- Tests: `python3 scripts/verify_features.py` (all tests → per-feature table in claudedocs/FEATURE_VERIFICATION.md; add new tests to its mapping) or `QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -p 'test_*.py'`. `python3 scripts/demo.py --headless-check` exercises the real server end to end. `tests/kakao_fakes.py` provides `make_demo_state()` for UI tests without a server. `tests/__init__.py` must stay (a site-packages `tests` package shadows it otherwise).
- Quality harness: `python3 scripts/harness/score_messenger.py` (real server + real `Session` clients; 12 areas: messaging, read counts, message ops, reconnect, 12-client load, performance, persistence, server fuzzing, UI stability, regression, design quality via `scripts/harness/design_probe.py` (WCAG contrast, accessible names, 24px targets, no clipped text at 100%/200%), work features (threads, room owner, vacation, DND, unread members, drafts, mentions, bookmarks)) → score = mean of areas × 10 and `claudedocs/HARNESS_SCORE.md`. Keep it at 10.0; `--quick` skips the slow regression area.
- Visual language is **KCS Ink** (`claudedocs/design/12_kcs_ink_redesign.md`): navy ink on cool paper chat background, bundled Pretendard font (`assets/fonts`, loaded by `theme.load_bundled_fonts()`), tail-less bubbles with a 4px speaker corner, date dividers with hairlines. No colored side-stripes on cards, no accent bar before titles.
- Avoid fixed window sizes (`setFixedSize`/`setMinimumSize` with literals) — they clipped widgets before; size from layouts.

## Core Components

### Server Architecture
- **TCP Socket Server** (`server/chat/chat_server.py`): Handles real-time message routing, user management, and broadcasts. Uses JSON protocol over TCP on port 5555.
- **Async Server** (`server/core/async_server.py`): WebSocket-based async implementation for future scalability.

### Client Architecture
- **Standalone Chat** (`client/standalone/standalone_chat.py`): Complete chat UI that works offline. Imports from the fixed version with bubble tails and High DPI support.
- **Real-time Client** (`client/realtime/chat_client.py`): Network-enabled client with login dialog, socket communication, and real-time message updates. Includes recent UI improvements for better text rendering.
- **Launcher GUI** (`client/launcher/chat_launcher.py`): Manages server lifecycle and multiple client windows with visual status indicators.

### Shared UI Components
All clients use PyQt5 with:
- Custom bubble painting with tail design (`BubbleWithTail` class)
- Korean font chain: `'맑은 고딕', 'Malgun Gothic', 'Apple SD Gothic Neo', 'Nanum Gothic', 'Arial'`
- High DPI scaling support with `QT_AUTO_SCREEN_SCALE_FACTOR`
- UTF-8 encoding for Korean text support

## Build and Run Commands

### Running the Application
```bash
# New organized structure
python client/standalone/standalone_chat.py    # Standalone mode
python server/chat/chat_server.py              # Start server
python client/realtime/chat_client.py          # Start client
python client/launcher/chat_launcher.py        # GUI launcher

# Or use batch files
run_organized.bat    # Menu for organized structure
run.bat             # Legacy compatibility
```

### Installation
```bash
# Core dependencies only
pip install PyQt5

# Full dependencies (includes many optional packages)
pip install -r requirements.txt
```

### Testing
```bash
python tests/test_installation.py      # Check dependencies
python tests/test_all_versions.py      # Test all versions
python tests/test_real_chat_fixes.py   # Verify real chat fixes
```

## Development Guidelines

### When modifying UI components:
1. Always test with Korean text to ensure proper encoding
2. Maintain High DPI support by setting attributes before QApplication creation
3. Use the established font chain for cross-platform compatibility
4. Test bubble rendering with antialiasing enabled

### When working with networking:
1. Server uses JSON messages with UTF-8 encoding
2. Message protocol includes types: `message`, `join`, `leave`, `users_list`
3. Client socket operations should be in separate threads
4. Handle connection loss gracefully with status updates

### Important patterns:
- All new server code goes in `/server/` directory
- All new client code goes in `/client/` directory
- Test files go in `/tests/` directory
- Use wrapper files in root for backward compatibility

## Key Technical Details

### Message Protocol
```json
{
  "type": "message|join|leave|users_list",
  "content": "message text",
  "sender": "username",
  "timestamp": "ISO format"
}
```

### UI Rendering Fixes Applied
- UTF-8 encoding declarations and locale settings
- High DPI scaling attributes set before QApplication
- Enhanced painter render hints for smooth text
- Responsive window sizing based on screen geometry
- Font family chains with fallbacks

### Known Issues Resolved
- Text display issues in `kakaotalk_real_chat.py` - fixed with font improvements
- UI breaking/scaling problems - resolved with High DPI support
- Korean text encoding - fixed with UTF-8 and locale settings

## Architecture Decisions

The project transitioned from a monolithic structure to organized client-server separation while maintaining backward compatibility through wrapper files. The real-time chat system uses TCP sockets for simplicity and reliability, with plans for WebSocket migration in the async server implementation.

## Development Roadmap

### Current Status
The project has completed basic real-time chat functionality with Korean UI support. The codebase follows a phased development approach targeting enterprise-grade features over 24 weeks.

### Major Milestones
- **Phase 1**: Architecture modernization (asyncio, WebSocket, PostgreSQL)
- **Phase 2**: Core messaging features (read receipts, reactions, search)
- **Phase 3**: GUI components with PyQt5 (dark mode, rich media)
- **Phase 4**: Real-time communication (notifications, voice/video calls via WebRTC)
- **Phase 5**: Group chat features (channels, permissions, polls)
- **Phase 6**: Security & performance (E2E encryption, rate limiting)
- **Phase 7**: Enterprise features (REST API, webhooks, SSO)

### Technical Stack Decisions
- **GUI**: PyQt5 (migrated from tkinter)
- **Database**: PostgreSQL + Redis (from SQLite)
- **Real-time**: WebSocket protocol
- **Voice/Video**: WebRTC integration
- **ORM**: SQLAlchemy for database operations

## UI/UX Guidelines

### Design System
- **Primary Color**: #FFE400 (KakaoTalk Yellow)
- **Background**: #ABC1D1 (KakaoTalk Blue)
- **Sent Messages**: Yellow bubbles (#FFE400)
- **Received Messages**: White bubbles (#FFFFFF)
- **Border Radius**: 18px for message bubbles
- **Max Message Width**: 280px (73% of window width)

### Font Hierarchy
```
Windows: 맑은 고딕 (Malgun Gothic)
macOS: Apple SD Gothic Neo
Linux: Noto Sans CJK KR
Fallback: Arial, Helvetica
```

### Responsive Design
- **Minimum Window**: 320x500px
- **Recommended**: 380x650px
- **Message Padding**: 10px 14px
- **Message Spacing**: 8px between messages

## Performance Requirements

### Current Benchmarks
- Message delivery: <100ms local network
- UI rendering: 60fps with antialiasing
- Memory usage: <200MB for 1000 messages
- Startup time: <2 seconds

### Target Metrics (from roadmap)
- Message throughput: >10,000 messages/second
- Concurrent users: >1,000 active connections
- Query response: <100ms for 95% of operations
- File transfer: >10MB/s sustained rate

## Testing Strategy

### Existing Test Files
```bash
python tests/test_installation.py      # Dependency verification
python tests/test_all_versions.py      # Version compatibility
python tests/test_real_chat_fixes.py   # Real-time chat validation
```

### Test Coverage Requirements
- Unit tests: >80% coverage target
- Integration tests: >70% coverage target
- UI tests: Manual testing with Korean text
- Performance tests: Load testing for concurrent users

## Common Issues and Solutions

### Korean Text Display
- Always set UTF-8 encoding: `# -*- coding: utf-8 -*-`
- Configure locale: `locale.setlocale(locale.LC_ALL, 'ko_KR.UTF-8')`
- Use proper font chain with Korean fonts first

### High DPI Scaling
- Set attributes BEFORE QApplication creation
- Use `QT_AUTO_SCREEN_SCALE_FACTOR` environment variable
- Test on various DPI settings (100%, 125%, 150%, 200%)

### Socket Connection
- Server default port: 5555 (TCP)
- Handle connection loss with retry mechanism
- Implement heartbeat for connection monitoring
- Queue messages during disconnection

## Security Considerations

### Current Implementation
- Basic authentication with username
- Plain text message transmission
- No rate limiting or abuse prevention

### Planned Security Features
- E2E encryption (Phase 6.1.1)
- Two-factor authentication (Phase 6.1.2)
- Session management (Phase 6.1.3)
- Rate limiting (Phase 6.1.4)
- Input sanitization (Phase 6.1.5)