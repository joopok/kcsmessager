#!/usr/bin/env python
"""
Launcher for Real KakaoTalk Chat System
Starts server and multiple client windows for real communication
"""

import sys
import subprocess
import time
import threading
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from client.kakao import osenv  # noqa: E402  - OS 차이는 공통 계층에서


def _new_console_flags():
    """Windows 는 서버 · 클라이언트를 새 콘솔 창으로, 그 밖 OS 는 0"""
    return getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) if osenv.is_windows() else 0


class ChatLauncher(QMainWindow):
    """Launcher window for managing chat server and clients"""
    
    def __init__(self):
        super().__init__()
        self.server_process = None
        self.client_processes = []
        self.init_ui()
        
    def init_ui(self):
        """Initialize launcher UI"""
        self.setWindowTitle("KCS Talk")
        
        # 윈도우 아이콘 설정 (옵션)
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        
        # Main layout - 더 넉넉한 여백과 간격 설정
        layout = QVBoxLayout(central)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 24, 30, 24)
        # 창 최소 크기를 내용물 크기에 맞춰 위젯이 찌그러지지 않게 함
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        
        # 회사 CI 로고
        logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 "assets", "brand", "ci_horizontal.png")
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            logo = QLabel()
            ratio = self.devicePixelRatioF()
            # PNG 위아래 투명 여백이 커서(로고는 높이의 약 1/3, 전체 높이의 25%~74% 띠) 그 띠만 잘라 쓴다.
            # 그림 해상도가 바뀌어도(2026-10-15 4배 렌더) 같은 자리를 자르도록 비율로 계산한다
            h = logo_pixmap.height()
            logo_pixmap = logo_pixmap.copy(0, round(h * 40 / 163), logo_pixmap.width(), round(h * 80 / 163))
            scaled = logo_pixmap.scaledToHeight(int(48 * ratio), Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            logo.setPixmap(scaled)
            logo.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo)

        # Title - 더 나은 타이포그래피
        title = QLabel("KCS Talk")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            QLabel {
                font-size: 22px;
                font-weight: bold;
                color: #FFFFFF;
                padding: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #34478F, stop:1 #2A386C);
                border-radius: 8px;
                border: 1px solid #1E2955;
            }
        """)
        layout.addWidget(title)
        
        # Server section - 개선된 그룹박스 스타일
        server_group = QGroupBox("서버 관리")
        server_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #333333;
                border: 2px solid #D0D0D0;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 15px;
                background-color: #FAFAFA;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 15px;
                padding: 2px 8px 2px 8px;
                background-color: #FFFFFF;
                border: 1px solid #D0D0D0;
                border-radius: 4px;
            }
        """)
        server_layout = QVBoxLayout(server_group)
        server_layout.setSpacing(12)
        server_layout.setContentsMargins(15, 20, 15, 15)
        
        # Server status - 더 나은 시각적 피드백
        self.server_status = QLabel("● 서버 상태: 중지됨")
        self.server_status.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 13px;
                font-weight: 500;
                padding: 8px 12px;
                background-color: #F0F0F0;
                border-radius: 6px;
                border: 1px solid #E0E0E0;
            }
        """)
        server_layout.addWidget(self.server_status)
        
        # Server buttons
        server_btn_layout = QHBoxLayout()
        server_btn_layout.setSpacing(10)
        
        self.start_server_btn = QPushButton("서버 시작")
        self.start_server_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4CAF50, stop:1 #45A049);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 14px 28px;
                font-size: 14px;
                font-weight: bold;
                min-height: 25px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #45A049, stop:1 #3E8E41);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3E8E41, stop:1 #357A32);
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
                color: #888888;
            }
        """)
        self.start_server_btn.clicked.connect(self.start_server)
        server_btn_layout.addWidget(self.start_server_btn)
        
        self.stop_server_btn = QPushButton("서버 중지")
        self.stop_server_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #F44336, stop:1 #E53935);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 14px 28px;
                font-size: 14px;
                font-weight: bold;
                min-height: 25px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #E53935, stop:1 #D32F2F);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #D32F2F, stop:1 #C62828);
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
                color: #888888;
            }
        """)
        self.stop_server_btn.clicked.connect(self.stop_server)
        self.stop_server_btn.setEnabled(False)
        server_btn_layout.addWidget(self.stop_server_btn)
        
        server_layout.addLayout(server_btn_layout)
        layout.addWidget(server_group)
        
        # Client section - 서버 섹션과 동일한 스타일 적용
        client_group = QGroupBox("클라이언트 관리")
        client_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #333333;
                border: 2px solid #D0D0D0;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 15px;
                background-color: #FAFAFA;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 15px;
                padding: 2px 8px 2px 8px;
                background-color: #FFFFFF;
                border: 1px solid #D0D0D0;
                border-radius: 4px;
            }
        """)
        client_layout = QVBoxLayout(client_group)
        client_layout.setSpacing(12)
        client_layout.setContentsMargins(15, 20, 15, 15)
        
        # Client status - 서버 상태와 동일한 스타일
        self.client_status = QLabel("활성 클라이언트: 0개")
        self.client_status.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 13px;
                font-weight: 500;
                padding: 8px 12px;
                background-color: #F0F0F0;
                border-radius: 6px;
                border: 1px solid #E0E0E0;
            }
        """)
        client_layout.addWidget(self.client_status)
        
        # Client buttons
        client_btn_layout = QHBoxLayout()
        client_btn_layout.setSpacing(10)
        
        self.open_client_btn = QPushButton("채팅창 열기")
        self.open_client_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4A62C2, stop:1 #3B51A8);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 14px 28px;
                font-size: 14px;
                font-weight: bold;
                min-height: 25px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3B51A8, stop:1 #33478F);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #33478F, stop:1 #2A386C);
            }
        """)
        self.open_client_btn.clicked.connect(self.open_client)
        client_btn_layout.addWidget(self.open_client_btn)
        
        self.open_two_btn = QPushButton("2개 창 열기")
        self.open_two_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2196F3, stop:1 #1976D2);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 14px 28px;
                font-size: 14px;
                font-weight: bold;
                min-height: 25px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1976D2, stop:1 #1565C0);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1565C0, stop:1 #0D47A1);
            }
        """)
        self.open_two_btn.clicked.connect(self.open_two_clients)
        client_btn_layout.addWidget(self.open_two_btn)
        
        client_layout.addLayout(client_btn_layout)
        layout.addWidget(client_group)
        
        # Quick start button - 더 눈에 띄는 디자인
        self.quick_start_btn = QPushButton("🚀 빠른 시작 (서버 + 2개 창)")
        self.quick_start_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #34478F, stop:1 #2A386C);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 18px 36px;
                font-size: 16px;
                font-weight: bold;
                min-height: 30px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2A386C, stop:1 #1E2955);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1E2955, stop:1 #141C3D);
            }
        """)
        self.quick_start_btn.clicked.connect(self.quick_start)
        layout.addWidget(self.quick_start_btn)
        
        # Instructions - 더 읽기 쉬운 스타일
        instructions = QLabel(
            "📋 사용법:\n"
            "• '빠른 시작' 버튼으로 서버와 2개 창을 한번에 시작\n"
            "• 각 창에서 다른 이름으로 로그인\n"
            "• 실시간으로 메시지 주고받기 가능"
        )
        instructions.setStyleSheet("""
            QLabel {
                color: #555555;
                font-size: 12px;
                padding: 16px 20px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #F8F9FA, stop:1 #E9ECEF);
                border: 1px solid #DEE2E6;
                border-radius: 8px;
            }
        """)
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        layout.addStretch()
        
        # Apply global style - 더 나은 전체 테마
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFFFFF, stop:1 #F8F9FA);
            }
        """)
        self.resize(650, self.minimumSizeHint().height())
    
    def start_server(self):
        """Start the chat server"""
        if not self.server_process:
            try:
                # 프로젝트 루트 디렉토리 기준으로 경로 설정
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                server_path = os.path.join(project_root, "server", "chat", "chat_server.py")
                
                self.server_process = subprocess.Popen(
                    [sys.executable, server_path],
                    cwd=project_root,  # 작업 디렉토리를 프로젝트 루트로 설정
                    creationflags=_new_console_flags()
                )
                
                # Wait a moment for server to start
                time.sleep(1)
                
                self.server_status.setText("● 서버 상태: 실행 중")
                self.server_status.setStyleSheet("""
                    QLabel {
                        color: #2E7D32;
                        font-size: 13px;
                        font-weight: 500;
                        padding: 8px 12px;
                        background-color: #E8F5E8;
                        border-radius: 6px;
                        border: 1px solid #4CAF50;
                    }
                """)
                
                self.start_server_btn.setEnabled(False)
                self.stop_server_btn.setEnabled(True)
                
                QMessageBox.information(self, "서버 시작", 
                                      "채팅 서버가 시작되었습니다.\n이제 채팅창을 열 수 있습니다.")
                
            except Exception as e:
                QMessageBox.critical(self, "오류", f"서버 시작 실패: {str(e)}")
    
    def stop_server(self):
        """Stop the chat server"""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process = None
                
                self.server_status.setText("● 서버 상태: 중지됨")
                self.server_status.setStyleSheet("""
                    QLabel {
                        color: #666666;
                        font-size: 13px;
                        font-weight: 500;
                        padding: 8px 12px;
                        background-color: #F0F0F0;
                        border-radius: 6px;
                        border: 1px solid #E0E0E0;
                    }
                """)
                
                self.start_server_btn.setEnabled(True)
                self.stop_server_btn.setEnabled(False)
                
                # Close all client windows
                for process in self.client_processes:
                    try:
                        process.terminate()
                    except:
                        pass
                self.client_processes.clear()
                self.update_client_count()
                
            except Exception as e:
                QMessageBox.critical(self, "오류", f"서버 중지 실패: {str(e)}")
    
    def open_client(self):
        """Open a single client window"""
        try:
            # 프로젝트 루트 디렉토리 기준으로 경로 설정
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            client_path = os.path.join(project_root, "client", "kakao", "app.py")
            
            # 저장된 자동 로그인(예: 김명수)으로 바로 들어가지 않고 창마다 로그인 창 → 다른 사람으로 로그인 가능
            process = subprocess.Popen(
                [sys.executable, client_path, '--no-auto-login'],
                cwd=project_root,  # 작업 디렉토리를 프로젝트 루트로 설정
                creationflags=_new_console_flags()
            )
            self.client_processes.append(process)
            self.update_client_count()
            
        except Exception as e:
            QMessageBox.critical(self, "오류", f"클라이언트 실행 실패: {str(e)}")
    
    def open_two_clients(self):
        """Open two client windows"""
        self.open_client()
        time.sleep(0.5)  # Small delay to prevent overlap
        self.open_client()
    
    def quick_start(self):
        """Quick start server and two clients"""
        if not self.server_process:
            self.start_server()
            time.sleep(1)  # Wait for server to fully start
        
        self.open_two_clients()
    
    def update_client_count(self):
        """Update client count display"""
        # Clean up terminated processes
        self.client_processes = [p for p in self.client_processes if p.poll() is None]
        count = len(self.client_processes)
        self.client_status.setText(f"활성 클라이언트: {count}개")
    
    def closeEvent(self, event):
        """Handle window close"""
        # Stop server and clients
        if self.server_process:
            self.stop_server()
        
        for process in self.client_processes:
            try:
                process.terminate()
            except:
                pass
        
        event.accept()


LAUNCHER_NAME = 'KCS Talk'   # Dock 툴팁 · 메뉴 막대 이름 (번들 ID 로 메신저와 구분)


def main():
    """Run the launcher"""
    # High DPI 지원 설정
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    # Dock 에 'Python' 대신 런처 이름 · KCS 아이콘 (QApplication 보다 먼저 이름을 바꾼다)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from client.kakao import osenv
    from client.kakao.macos import run_in_app_bundle, set_app_name, set_dock_name
    run_in_app_bundle(LAUNCHER_NAME, osenv.APP_ID + '.launcher', osenv.app_home())   # Dock 툴팁 (되면 번들에서 다시 시작)
    set_app_name(LAUNCHER_NAME)
    app = QApplication(sys.argv)
    set_dock_name(LAUNCHER_NAME)   # 번들로 못 옮겼을 때 (QApplication 다음에)
    from client.kakao import brand
    app.setWindowIcon(brand.app_icon())
    
    # 더 나은 스타일과 폰트 설정
    app.setStyle('Fusion')
    
    # 한글 폰트 체인 설정 (OS별 첫 번째로 존재하는 폰트 사용)
    font = QFont()
    font.setFamilies(['맑은 고딕', 'Malgun Gothic', 'Apple SD Gothic Neo',
                      'Nanum Gothic', 'Noto Sans CJK KR', 'Arial'])
    font.setPointSize(10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    
    # 애플리케이션 정보 설정
    app.setApplicationName("KCS Talk")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("KCS Messenger")
    
    launcher = ChatLauncher()
    launcher.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()