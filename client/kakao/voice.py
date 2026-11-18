# -*- coding: utf-8 -*-
"""
음성메시지 (카카오톡 '+ → 음성메시지', 명세 §9.3)

- pcm_to_wav / wav_info: 16kHz mono s16le PCM ↔ WAV
- VoiceRecorder: 마이크 → 16kHz PCM 누적 (feed()로 테스트 주입 가능), 최대 MAX_VOICE_SECONDS
- VoicePlayer: WAV 재생 (한 번에 하나), 진행률 시그널
- VoiceRecordDialog: 녹음 시작/정지 · 경과 시간 · 입력 레벨 · 보내기/취소
- waveform_bars: 음성 말풍선의 막대 파형 (데이터가 없으면 msg_id 로 고정된 모양)
"""

import io
import math
import random
import struct
import wave

from PyQt5.QtCore import Qt, QObject, QTimer, QRectF, pyqtSignal, QEasingCurve
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget

from client.kakao import theme
from client.kakao.state import MAX_VOICE_SECONDS, fmt_duration
from client.kakao.call import SAMPLE_RATE, rms_level   # 장치 입출력은 call.AudioEngine 을 그대로 쓴다

BYTES_PER_SECOND = SAMPLE_RATE * 2


def pcm_to_wav(pcm, rate=SAMPLE_RATE):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_info(data):
    """WAV → (16kHz mono PCM, 길이 초). 해석 불가면 (b'', 0)"""
    try:
        with wave.open(io.BytesIO(data), 'rb') as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError, struct.error):
        return b'', 0.0
    # 받은 파일의 머리 값을 그대로 믿지 않는다: 표본율이 아주 낮게 적혀 있으면 16kHz 로 늘리는 동안 수 GB 가 되고
    # (10MB 파일에 200Hz → 80배), 0 이면 나눗셈 오류. 녹음 · 통화가 쓰는 범위 밖이면 재생하지 않는다
    if width != 2 or not 8000 <= rate <= 48000 or not 1 <= ch <= 2:
        return b'', 0.0
    frames = frames[:int(MAX_VOICE_SECONDS * 1.5 * rate) * 2 * ch]     # 음성 메시지 최대 길이(+여유)까지만
    if ch > 1:   # 첫 채널만
        frames = b''.join(frames[i:i + 2] for i in range(0, len(frames), 2 * ch))
    if rate != SAMPLE_RATE:
        from client.kakao.call import resample_pcm
        frames = resample_pcm(frames, rate, SAMPLE_RATE)
    return frames, len(frames) / BYTES_PER_SECOND


def pcm_level(pcm):
    """0~1 입력 레벨: 통화의 rms_level(numpy) 을 말소리 크기에 맞게 키운 값 (RMS 8000 ≈ 1.0)"""
    return min(1.0, rms_level(pcm) * 4.096)


def waveform_bars(seed, count=22, pcm=None):
    """막대 파형 높이(0.2~1). pcm 이 있으면 실제 크기, 없으면 seed 로 고정된 모양"""
    if pcm:
        chunk = max(2, len(pcm) // count // 2 * 2)
        return [max(0.2, min(1.0, pcm_level(pcm[i * chunk:(i + 1) * chunk]) * 1.6)) for i in range(count)]
    rnd = random.Random(seed)
    return [0.25 + 0.75 * abs(math.sin(i * 0.7 + rnd.random() * 2.2)) for i in range(count)]


class VoiceRecorder(QObject):
    """마이크 녹음 → 16kHz mono PCM. start() 실패하면 error 시그널"""
    level_changed = pyqtSignal(float)
    elapsed_changed = pyqtSignal(float)
    limit_reached = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pcm = bytearray()
        self.recording = False
        self._engine = None

    @property
    def seconds(self):
        return len(self.pcm) / BYTES_PER_SECOND

    def start(self):
        """통화와 같은 AudioEngine 입력(장치 확인·형식 변환·오류 처리)을 그대로 쓴다"""
        from client.kakao.call import AudioEngine
        self.pcm = bytearray()
        self.recording = False
        if self._engine is None:
            self._engine = AudioEngine(self)
            self._engine.frame_captured.connect(self.feed)
            self._engine.error.connect(self.error)
        if not self._engine.start_input():
            return False
        self.recording = True
        return True

    def feed(self, pcm):
        """16kHz PCM 추가 (장치 입력 또는 테스트)"""
        if not self.recording:
            return
        room = MAX_VOICE_SECONDS * BYTES_PER_SECOND - len(self.pcm)
        self.pcm += pcm[:max(0, room)]
        self.level_changed.emit(pcm_level(pcm))
        self.elapsed_changed.emit(self.seconds)
        if room <= len(pcm):
            self.stop()
            self.limit_reached.emit()

    def stop(self):
        self.recording = False
        if self._engine is not None:
            self._engine.stop_input()
        return bytes(self.pcm)


class _ClipMixer:
    """음성 파일 PCM 을 통화 AudioEngine 이 쓰는 믹서처럼 40ms 프레임으로 내준다"""

    def __init__(self, pcm):
        from client.kakao.call import FRAME_BYTES
        self.pcm, self.pos, self.frame = pcm, 0, FRAME_BYTES

    def pop_frame(self):
        if self.pos >= len(self.pcm):
            return None
        frame = self.pcm[self.pos:self.pos + self.frame]
        self.pos += len(frame)
        return frame.ljust(self.frame, b'\x00')

    def done(self):
        return self.pos >= len(self.pcm)


class VoicePlayer(QObject):
    """WAV 재생기 (앱 전체에서 하나: VoicePlayer.instance()). 출력은 통화와 같은 AudioEngine
    (장치 확인·형식 변환·장치가 없으면 실시간 시계로 소비) 을 그대로 쓴다. key 로 재생 중인 말풍선을 구분"""
    progress = pyqtSignal(object, float)   # key, 0~1
    stopped = pyqtSignal(object)
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = VoicePlayer()
        return cls._inst

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = None
        self._engine = None
        self._mixer = None
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._pump)

    def is_playing(self, key):
        return self.key == key

    def play(self, key, wav_bytes):
        from client.kakao.call import AudioEngine
        self.stop()
        pcm, _secs = wav_info(wav_bytes)
        if not pcm:
            return False
        if self._engine is None:
            self._engine = AudioEngine(self)
        self.key, self._mixer = key, _ClipMixer(pcm)
        self._engine.start_output()          # 스피커가 없으면 시계에 맞춰 진행만 (오류는 무시)
        self._timer.start()
        return True

    def _pump(self):
        self._engine.play_tick(self._mixer)
        self.progress.emit(self.key, min(1.0, self._mixer.pos / max(1, len(self._mixer.pcm))))
        if self._mixer.done():
            self.stop()

    def stop(self):
        key = self.key
        self._timer.stop()
        if self._engine is not None:
            self._engine.stop_output()
        self.key = None
        if key is not None:
            self.stopped.emit(key)


class _PhaseWidget(QWidget):
    """PERIOD_MS 주기로 phase(0~1)가 도는 그림 위젯 공통 (동작 줄이기면 멈춤)"""
    PERIOD_MS = 1000
    TICK_MS = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    def run(self, on):
        self.phase = 0.0
        if on and not theme.reduce_motion():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def running(self):
        return self._timer.isActive()

    def _tick(self):
        self.phase = (self.phase + self.TICK_MS / self.PERIOD_MS) % 1.0
        self.update()


class PulseDot(_PhaseWidget):
    """녹음 중 표시: 빨간 점이 900ms 주기로 숨 쉬듯 밝아졌다 흐려진다 (동작 줄이기면 그대로 켜짐)"""
    PERIOD_MS = 900

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.active = False

    def set_active(self, on):
        self.active = bool(on)
        self.run(self.active)

    def opacity(self):
        if not self.active:
            return 0.25
        if theme.reduce_motion():
            return 1.0
        return 0.35 + 0.65 * (0.5 + 0.5 * math.cos(self.phase * 2 * math.pi))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        color = QColor(theme.c('badge') if self.active else theme.c('text_hint'))
        color.setAlphaF(self.opacity())
        p.setBrush(color)
        p.drawEllipse(QRectF(1, 1, 10, 10))


class LiveWave(QWidget):
    """녹음 중 실시간 파형 (overdrive): 입력 레벨이 오른쪽에서 들어와 왼쪽으로 흐르는 막대.
    최근 48개(약 2초)를 보여 주고, 녹음 길이에 따라 진행 바탕이 채워진다"""
    HISTORY = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.history = []
        self.progress = 0.0
        self.setFixedHeight(44)

    def set_level(self, level):
        self.level = max(0.0, min(1.0, level))
        self.history = (self.history + [self.level])[-self.HISTORY:]
        self.update()

    def set_progress(self, value):
        self.progress = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        p.setBrush(QColor(theme.c('bg_alt')))
        p.drawRoundedRect(r, theme.radius('m'), theme.radius('m'))
        if self.progress > 0:      # 최대 5분 중 얼마나 썼는지 (옅은 바탕)
            tint = QColor(theme.c('yellow'))
            tint.setAlphaF(0.12)
            p.setBrush(tint)
            p.drawRoundedRect(QRectF(0, 0, r.width() * self.progress, r.height()), theme.radius('m'), theme.radius('m'))
        gap, bw = 3.0, 3.0
        n = int((r.width() - 16) // (bw + gap))
        bars = ([0.0] * max(0, n - len(self.history)) + self.history)[-n:] if n > 0 else []
        color = QColor(theme.c('yellow'))
        mid = r.height() / 2
        for i, lv in enumerate(bars):
            h = max(3.0, (r.height() - 14) * min(1.0, lv * 1.4))
            c = QColor(color)
            c.setAlphaF(0.35 + 0.65 * (i + 1) / max(1, len(bars)))   # 최근 막대일수록 진하게
            p.setBrush(c)
            p.drawRoundedRect(QRectF(8 + i * (bw + gap), mid - h / 2, bw, h), 1.5, 1.5)


class VoiceRecordDialog(QDialog):
    """음성메시지 녹음 창: ● 녹음 → ■ 정지 → 보내기"""

    def __init__(self, parent=None, recorder=None, autostart=True):
        super().__init__(parent)
        self.setWindowTitle('음성메시지')
        self.recorder = recorder or VoiceRecorder(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        self.title = QLabel('음성메시지')
        self.title.setObjectName('dialogTitle')
        self.title.setStyleSheet(f"font-size: {theme.fs('title')}px; font-weight: bold; color: {theme.c('text')};")
        v.addWidget(self.title)
        self.time_label = QLabel(f'0:00 / {fmt_duration(MAX_VOICE_SECONDS)}')
        self.time_label.setAlignment(Qt.AlignCenter)
        self.pulse = PulseDot(self)
        self.time_label.setStyleSheet(f"font-size: {theme.fs('numeral')}px; font-weight: 700; color: {theme.c('text')};")
        time_row = QHBoxLayout()
        time_row.addStretch(1)
        time_row.addWidget(self.pulse, 0, Qt.AlignVCenter)
        time_row.addSpacing(8)
        time_row.addWidget(self.time_label)
        time_row.addStretch(1)
        v.addLayout(time_row)
        self.meter = LiveWave(self)
        v.addWidget(self.meter)
        self.status = QLabel('')
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.c('text_sub')};")
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.record_button = QPushButton('녹음')
        self._set_record_icon(False)
        self.cancel_button = QPushButton('취소')
        self.send_button = QPushButton('보내기')
        self.send_button.setObjectName('primary')
        self.send_button.setEnabled(False)
        for b in (self.record_button, self.cancel_button, self.send_button):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        v.addLayout(row)
        self.record_button.clicked.connect(self.toggle)
        self.cancel_button.clicked.connect(self.reject)
        self.send_button.clicked.connect(self.finish)
        self.recorder.elapsed_changed.connect(self._on_elapsed)
        self.recorder.level_changed.connect(self.meter.set_level)
        self.recorder.limit_reached.connect(lambda: self._stopped('5분이 되어 녹음을 멈췄습니다. 보내기를 누르면 전송됩니다.'))
        self.recorder.error.connect(self._on_error)
        self.setMinimumWidth(theme.px(300))
        if autostart:
            QTimer.singleShot(0, self.toggle)

    def toggle(self):
        if self.recorder.recording:
            self.recorder.stop()
            self._stopped('녹음을 멈췄습니다. 보내기를 누르면 전송됩니다.')
        else:
            self.status.setText('녹음 중입니다. 말씀하세요.')
            self.record_button.setText('정지')
            self._set_record_icon(True)
            self.send_button.setEnabled(False)
            self.pulse.set_active(True)
            self.recorder.start()

    def _set_record_icon(self, recording):
        """녹음 = 빨간 원, 정지 = 사각 (글자 기호 ●■ 대신 직접 그린 아이콘)"""
        from PyQt5.QtGui import QPixmap, QIcon
        pm = QPixmap(28, 28)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if recording:
            p.setBrush(QColor(theme.c('text')))
            p.drawRoundedRect(QRectF(3, 3, 8, 8), 1.5, 1.5)
        else:
            p.setBrush(QColor(theme.c('badge')))
            p.drawEllipse(QRectF(2.5, 2.5, 9, 9))
        p.end()
        self.record_button.setIcon(QIcon(pm))
        self.record_button.setAccessibleName('녹음 정지' if recording else '녹음 시작')

    def _stopped(self, text):
        self.pulse.set_active(False)
        self.record_button.setText('다시 녹음')
        self._set_record_icon(False)
        self.status.setText(text)
        self.send_button.setEnabled(self.recorder.seconds >= 1)

    def _on_elapsed(self, secs):
        self.meter.set_progress(secs / MAX_VOICE_SECONDS)
        self.time_label.setText(f'{fmt_duration(secs)} / {fmt_duration(MAX_VOICE_SECONDS)}')

    def _on_error(self, text):
        """장치 오류: 무엇이 잘못됐는지 + 할 일 (녹음기는 장치가 열린 뒤에만 녹음 중이 된다)"""
        self._stopped(f'{text}. 마이크 연결과 권한(시스템 설정 → 개인정보 보호 → 마이크)을 확인한 뒤 다시 녹음하세요.')

    def seconds(self):
        return self.recorder.seconds

    def wav_bytes(self):
        return pcm_to_wav(bytes(self.recorder.pcm))

    def finish(self):
        if self.recorder.recording:
            self.recorder.stop()
        if self.recorder.seconds < 1:
            self.status.setText('1초 이상 녹음해 주세요')
            return
        self.accept()

    def reject(self):
        if self.recorder.recording:
            self.recorder.stop()
        super().reject()


class BusyBar(_PhaseWidget):
    """진행 중 표시: 3px 막대 조각이 1.2초마다 왼→오로 지나간다 (감속 곡선). 동작 줄이기면 막대 전체가 옅게 고정"""
    PERIOD_MS = 1200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._curve = QEasingCurve(QEasingCurve.OutCubic)

    def start(self):
        self.show()
        self.run(True)

    def stop(self):
        self.run(False)
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        track = QColor(theme.c('yellow'))
        track.setAlphaF(0.18)
        p.fillRect(self.rect(), track)
        if theme.reduce_motion():
            return
        w = self.width()
        seg = w * 0.32
        x = -seg + (w + seg) * self._curve.valueForProgress(self.phase)
        p.fillRect(QRectF(x, 0, seg, self.height()), QColor(theme.c('yellow')))
