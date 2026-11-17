# -*- coding: utf-8 -*-
"""
실시간 통화 - 보이스톡 · 페이스톡(최대 10명) · 보이스룸 · 화면 공유 (명세 §8.2)

    from client.kakao.call import show_call
    window = show_call(state, call_id)     # call_id 당 창 하나 (있으면 앞으로 가져옴)

미디어 형식
    audio  : 16kHz mono s16le PCM 40ms 프레임 → zlib 압축(codec 'zpcm') → base64
    video  : 320x240 JPEG(q≈60) base64, 약 5fps
    screen : 가로 ≤1280 JPEG(q≈50) base64, 약 2fps

장치(마이크/스피커/카메라/화면)는 모두 선택 사항이다. 장치가 없거나 열 수 없으면 안내 문구만
띄우고 UI는 계속 동작한다. QT_QPA_PLATFORM=offscreen(테스트) 또는 KCS_CALL_DEVICES=0 이면
실제 장치를 열지 않는다.
"""

import base64
import math
import os
import re
import time
import wave
import zlib
from collections import deque
from datetime import datetime, timedelta

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy 가 없으면 순수 파이썬 경로 사용
    np = None

from PyQt5.QtCore import (Qt, QObject, QTimer, QRectF, QPointF, QSize, QBuffer, QByteArray,
                          QIODevice, pyqtSignal)
from PyQt5.QtGui import (QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QLinearGradient, QRadialGradient,
                         QGuiApplication, QFontMetrics, QIcon)
from PyQt5.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QAbstractButton,
                             QPushButton, QListWidget, QListWidgetItem, QScrollArea, QFrame, QMenu,
                             QSizePolicy, QListView)

from client.kakao import theme
from client.kakao.widgets import user_avatar_pixmap, decode_image

try:  # QtMultimedia 는 선택 사항
    from PyQt5.QtMultimedia import (QAudioFormat, QAudioDeviceInfo, QAudioInput, QAudioOutput, QAudio,
                                    QCamera, QCameraInfo, QAbstractVideoSurface, QAbstractVideoBuffer,
                                    QVideoFrame, QVideoSurfaceFormat)
    HAS_MULTIMEDIA = True
except Exception:  # pragma: no cover
    HAS_MULTIMEDIA = False
    QAbstractVideoSurface = QObject


# ---------------------------------------------------------------- 상수
SAMPLE_RATE = 16000
FRAME_MS = 40
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000          # 640
FRAME_BYTES = FRAME_SAMPLES * 2                         # 1280
MAX_AUDIO_FRAME_BYTES = FRAME_BYTES * 25                # 받은 음성 한 조각의 상한 (1초 분량, 보내는 쪽은 40ms 씩)
MAX_FRAME_PIXELS = 3840 * 2160                          # 받은 영상 · 화면 공유 한 장의 상한
JITTER_FRAMES = 3                                       # 약 120ms 미리 채운 뒤 재생
MAX_QUEUE_FRAMES = 25                                   # 보낸 사람별 최대 1초 지연
VIDEO_SIZE = (320, 240)
VIDEO_FPS = 5
VIDEO_QUALITY = 60
SCREEN_MAX_WIDTH = 1280
SCREEN_INTERVAL_MS = 500
SCREEN_QUALITY = 50
VOICEROOM_HOURS = 48
MAX_STAGE = 10
SPEAKING_THRESHOLD = 0.015

from client.kakao.state import CALL_MODE_LABELS as MODE_LABELS  # noqa: E402  통화 종류 이름은 state 하나
ROLE_LABELS = {'host': '진행자', 'speaker': '스피커', 'listener': '리스너'}
SPEAK_COLOR = '#34C759'
DANGER = '#FF3B30'


# ---------------------------------------------------------------- 순수 함수 (테스트 대상)
def encode_audio(pcm_bytes):
    """PCM → (base64, codec). zlib 으로 줄어들면 'zpcm', 아니면 'pcm'"""
    raw = bytes(pcm_bytes or b'')
    packed = zlib.compress(raw, 6)
    if len(packed) < len(raw):
        return base64.b64encode(packed).decode('ascii'), 'zpcm'
    return base64.b64encode(raw).decode('ascii'), 'pcm'


def decode_audio(data_b64, codec='pcm'):
    """(base64, codec) → PCM bytes. 깨진 데이터는 b''"""
    data_b64 = data_b64 or ''
    if len(data_b64) > MAX_AUDIO_FRAME_BYTES * 2:           # base64 는 4/3 배 - 그보다 훨씬 길면 풀지 않는다
        return b''
    try:
        raw = base64.b64decode(data_b64)
        if codec == 'zpcm':                                 # 풀린 크기가 상한을 넘는지 볼 만큼만 푼다
            raw = zlib.decompressobj().decompress(raw, MAX_AUDIO_FRAME_BYTES + 1)
    except Exception:
        return b''
    if len(raw) > MAX_AUDIO_FRAME_BYTES:
        return b''
    if len(raw) % 2:
        raw = raw[:-1]
    return raw


def mix_pcm(chunks):
    """여러 s16le PCM 을 더해서 섞는다(짧은 것은 무음으로 채움, ±32767 범위로 자름)"""
    chunks = [bytes(c) for c in chunks if c]
    if not chunks:
        return b''
    if len(chunks) == 1:
        return chunks[0][:len(chunks[0]) // 2 * 2]
    length = max(len(c) // 2 for c in chunks)
    if np is not None:
        acc = np.zeros(length, dtype=np.int32)
        for c in chunks:
            arr = np.frombuffer(c[:len(c) // 2 * 2], dtype='<i2')
            acc[:len(arr)] += arr
        return np.clip(acc, -32768, 32767).astype('<i2').tobytes()
    import array  # pragma: no cover
    acc = [0] * length
    for c in chunks:
        a = array.array('h', c[:len(c) // 2 * 2])
        for i, v in enumerate(a):
            acc[i] += v
    return array.array('h', [max(-32768, min(32767, v)) for v in acc]).tobytes()


def rms_level(pcm):
    """s16le PCM 의 RMS (0.0 ~ 1.0)"""
    pcm = bytes(pcm or b'')
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    if np is not None:
        arr = np.frombuffer(pcm[:n * 2], dtype='<i2').astype(np.float64)
        return float(min(1.0, math.sqrt(float(np.mean(arr * arr))) / 32768.0))
    import array  # pragma: no cover
    a = array.array('h', pcm[:n * 2])
    return min(1.0, math.sqrt(sum(v * v for v in a) / n) / 32768.0)


def resample_pcm(pcm, src_rate, dst_rate):
    """mono s16le 선형 보간 리샘플링"""
    if src_rate == dst_rate or not pcm or np is None:
        return bytes(pcm or b'')
    arr = np.frombuffer(bytes(pcm)[:len(pcm) // 2 * 2], dtype='<i2').astype(np.float32)
    if len(arr) < 2:
        return arr.astype('<i2').tobytes()
    n_out = max(1, int(round(len(arr) * dst_rate / float(src_rate))))
    x_old = np.linspace(0.0, 1.0, num=len(arr), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    out = np.interp(x_new, x_old, arr)
    return np.clip(out, -32768, 32767).astype('<i2').tobytes()


def _np_dtype(fmt):
    """QAudioFormat → numpy dtype (지원하지 않으면 None)"""
    if np is None:
        return None
    size = fmt.sampleSize()
    stype = fmt.sampleType()
    order = '<' if fmt.byteOrder() == QAudioFormat.LittleEndian else '>'
    if stype == QAudioFormat.Float and size == 32:
        return np.dtype(order + 'f4')
    if stype == QAudioFormat.SignedInt and size in (8, 16, 32):
        return np.dtype({8: 'i1', 16: order + 'i2', 32: order + 'i4'}[size])
    if stype == QAudioFormat.UnSignedInt and size in (8, 16):
        return np.dtype({8: 'u1', 16: order + 'u2'}[size])
    return None


def device_to_pcm16k(raw, fmt):
    """장치 형식의 오디오 → 16kHz mono s16le (형식이 이미 같으면 그대로)"""
    if _is_target_format(fmt):
        return bytes(raw)
    dt = _np_dtype(fmt)
    if dt is None:
        return b''
    ch = max(1, fmt.channelCount())
    usable = len(raw) // (dt.itemsize * ch) * dt.itemsize * ch
    arr = np.frombuffer(bytes(raw)[:usable], dtype=dt).astype(np.float64)
    if dt.kind == 'f':
        arr = arr * 32767.0
    elif dt.kind == 'u':
        mid = 2 ** (dt.itemsize * 8 - 1)
        arr = (arr - mid) * (32768.0 / mid)
    else:
        arr = arr * (32768.0 / 2 ** (dt.itemsize * 8 - 1))
    if ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1)
    pcm = np.clip(arr, -32768, 32767).astype('<i2').tobytes()
    return resample_pcm(pcm, fmt.sampleRate(), SAMPLE_RATE)


def pcm16k_to_device(pcm, fmt):
    """16kHz mono s16le → 장치 형식"""
    if _is_target_format(fmt):
        return bytes(pcm)
    dt = _np_dtype(fmt)
    if dt is None:
        return b''
    pcm = resample_pcm(pcm, SAMPLE_RATE, fmt.sampleRate())
    arr = np.frombuffer(pcm, dtype='<i2').astype(np.float64)
    if dt.kind == 'f':
        arr = arr / 32768.0
    elif dt.kind == 'u':
        mid = 2 ** (dt.itemsize * 8 - 1)
        arr = arr * (mid / 32768.0) + mid
    else:
        arr = arr * (2 ** (dt.itemsize * 8 - 1) / 32768.0)
    ch = max(1, fmt.channelCount())
    if ch > 1:
        arr = np.repeat(arr, ch)
    info = np.iinfo(dt) if dt.kind in 'iu' else None
    if info is not None:
        arr = np.clip(arr, info.min, info.max)
    return arr.astype(dt).tobytes()


def _is_target_format(fmt):
    return (fmt.sampleRate() == SAMPLE_RATE and fmt.channelCount() == 1 and fmt.sampleSize() == 16
            and fmt.sampleType() == QAudioFormat.SignedInt and fmt.byteOrder() == QAudioFormat.LittleEndian)


def target_audio_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(1)
    fmt.setSampleSize(16)
    fmt.setCodec('audio/pcm')
    fmt.setByteOrder(QAudioFormat.LittleEndian)
    fmt.setSampleType(QAudioFormat.SignedInt)
    return fmt


def jpeg_encode(image, quality=VIDEO_QUALITY):
    """QImage → JPEG base64 문자열 (실패 시 '')"""
    if image is None or image.isNull():
        return ''
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img = image if image.format() in (QImage.Format_RGB32, QImage.Format_RGB888) \
        else image.convertToFormat(QImage.Format_RGB32)
    ok = img.save(buf, 'JPEG', int(quality))
    buf.close()
    if not ok:
        return ''
    return base64.b64encode(bytes(ba)).decode('ascii')


def jpeg_decode(data_b64):
    """JPEG base64 → QImage (실패 시 null QImage)"""
    try:
        img = decode_image(base64.b64decode(data_b64 or ''), MAX_FRAME_PIXELS, ('jpeg',))
    except Exception:
        return QImage()
    return img if img is not None else QImage()


def fit_frame(image, width=VIDEO_SIZE[0], height=VIDEO_SIZE[1]):
    """가운데를 잘라서(cover) width x height 로 맞춘다"""
    if image is None or image.isNull():
        return QImage()
    scaled = image.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height).convertToFormat(QImage.Format_RGB32)


def blur_image(image, factor=10):
    """배경 흐림 - 축소/확대로 흐리게 만든 뒤 가운데 인물 영역(타원)만 선명하게 남긴다"""
    if image is None or image.isNull():
        return QImage()
    src = image.convertToFormat(QImage.Format_RGB32)
    w, h = src.width(), src.height()
    small = src.scaled(max(1, w // factor), max(1, h // factor), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    small = small.scaled(max(1, w // (factor // 2 or 1)), max(1, h // (factor // 2 or 1)),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    # 가운데 인물 영역(얼굴+어깨)만 선명하게 - 가장자리는 부드럽게 섞는다
    sharp = src.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    mask = QPainter(sharp)
    mask.setRenderHint(QPainter.Antialiasing)
    mask.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    grad = QRadialGradient(QPointF(0, 0), 1.0)
    grad.setColorAt(0.0, QColor(0, 0, 0, 255))
    grad.setColorAt(0.72, QColor(0, 0, 0, 255))
    grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    mask.translate(w * 0.5, h * 0.6)
    mask.scale(w * 0.36, h * 0.62)
    mask.fillRect(QRectF(-w, -h, 2 * w, 2 * h), grad)
    mask.end()
    p = QPainter(out)
    p.drawImage(0, 0, sharp)
    p.end()
    return out


def recordings_dir():
    from client.kakao import osenv
    return osenv.app_path('recordings')


class WavRecorder:
    """16kHz mono s16le PCM 을 WAV 파일로 기록"""

    def __init__(self, path, rate=SAMPLE_RATE):
        self.path = path
        self.rate = rate
        self.frames = 0
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._wav = wave.open(path, 'wb')
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)

    @property
    def is_open(self):
        return self._wav is not None

    @property
    def duration(self):
        return self.frames / float(self.rate)

    def write(self, pcm):
        if self._wav is None or not pcm:
            return
        pcm = bytes(pcm)[:len(pcm) // 2 * 2]
        self._wav.writeframes(pcm)
        self.frames += len(pcm) // 2

    def close(self):
        if self._wav is not None:
            try:
                self._wav.close()
            finally:
                self._wav = None
        return self.path


class JitterMixer:
    """보낸 사람별 지터 버퍼 + 믹서. 각자 JITTER_FRAMES 만큼 쌓이면 재생을 시작한다"""

    def __init__(self, prebuffer=JITTER_FRAMES, max_frames=MAX_QUEUE_FRAMES):
        self.prebuffer = prebuffer
        self.max_frames = max_frames
        self.buffers = {}
        self.ready = set()
        self.last_seq = {}

    def push(self, sender, seq, pcm):
        last = self.last_seq.get(sender)
        if last is not None and seq and seq <= last:
            return False                                   # 중복/역순 프레임
        self.last_seq[sender] = seq
        buf = self.buffers.setdefault(sender, bytearray())
        buf.extend(pcm[:len(pcm) // 2 * 2])
        limit = self.max_frames * FRAME_BYTES
        if len(buf) > limit:                               # 너무 밀리면 오래된 것부터 버림
            del buf[:len(buf) - self.prebuffer * FRAME_BYTES]
        if len(buf) >= self.prebuffer * FRAME_BYTES:
            self.ready.add(sender)
        return True

    def pending_frames(self, sender):
        return len(self.buffers.get(sender, b'')) // FRAME_BYTES

    def pop_frame(self):
        """재생할 40ms 프레임(섞인 것) 또는 None"""
        parts = []
        for sender in list(self.ready):
            buf = self.buffers.get(sender)
            if not buf:
                self.ready.discard(sender)
                continue
            chunk = bytes(buf[:FRAME_BYTES])
            del buf[:FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                chunk += b'\x00' * (FRAME_BYTES - len(chunk))
            parts.append(chunk)
            if not buf:
                self.ready.discard(sender)                 # 다 비면 다시 미리 채우기
        return mix_pcm(parts) if parts else None

    def remove(self, sender):
        self.buffers.pop(sender, None)
        self.ready.discard(sender)
        self.last_seq.pop(sender, None)

    def clear(self):
        self.buffers.clear()
        self.ready.clear()
        self.last_seq.clear()


def devices_enabled():
    """실제 마이크/카메라를 열어도 되는 환경인지"""
    if not HAS_MULTIMEDIA:
        return False
    if os.environ.get('KCS_CALL_DEVICES', '') == '0':
        return False
    platform = os.environ.get('QT_QPA_PLATFORM', '')
    app = QGuiApplication.instance()
    if app is not None:
        platform = app.platformName() or platform
    return platform not in ('offscreen', 'minimal')


class RecBadge(QLabel):
    """'녹음 중' 배지: 왼쪽 빨간 점은 직접 그린다 (글자 기호 ● 대신)"""

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setIndent(12)   # 점 자리

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt5.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#FF4B3E'))
        r = 3.5
        m = self.contentsMargins()
        p.drawEllipse(QPointF(m.left() + 6, self.height() / 2), r, r)


def format_duration(seconds):
    """state.fmt_duration 과 같은 함수 (통화 창에서 쓰는 이름 유지)"""
    from client.kakao.state import fmt_duration
    return fmt_duration(seconds)


def parse_time(value):
    if not value:
        return None
    try:
        text = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)          # 로컬 시간으로 통일
    return dt


# ---------------------------------------------------------------- 오디오 장치
class AudioEngine(QObject):
    """QAudioInput/QAudioOutput 래퍼. 모든 장치 오류는 error 시그널로만 알린다"""
    frame_captured = pyqtSignal(bytes)       # 16kHz mono s16le 40ms
    error = pyqtSignal(str)

    def __init__(self, parent=None, enabled=None):
        super().__init__(parent)
        self.enabled = devices_enabled() if enabled is None else enabled
        self._input = None
        self._in_dev = None
        self._in_fmt = None
        self._in_pending = bytearray()
        self._in_raw = bytearray()
        self._output = None
        self._out_dev = None
        self._out_fmt = None
        self.volume = 1.0
        self._clock0 = None
        self._popped = 0
        self._poll = QTimer(self)
        self._poll.setInterval(20)
        self._poll.timeout.connect(self._read_input)

    # -- 입력
    @property
    def input_active(self):
        return self._in_dev is not None

    @property
    def output_active(self):
        return self._out_dev is not None

    def start_input(self):
        if self._in_dev is not None:
            return True
        if not self.enabled:
            self.error.emit('마이크를 찾을 수 없습니다')
            return False
        try:
            info = QAudioDeviceInfo.defaultInputDevice()
            if info.isNull():
                self.error.emit('마이크를 찾을 수 없습니다')
                return False
            fmt = target_audio_format()
            if not info.isFormatSupported(fmt):
                fmt = info.nearestFormat(fmt)
                if _np_dtype(fmt) is None:
                    self.error.emit('마이크 형식을 지원하지 않습니다')
                    return False
            self._in_fmt = fmt
            self._input = QAudioInput(info, fmt, self)
            per_sec = fmt.sampleRate() * fmt.channelCount() * max(1, fmt.sampleSize() // 8)
            self._input.setBufferSize(int(per_sec * 0.2))
            self._in_dev = self._input.start()
            if self._in_dev is None or self._input.error() != QAudio.NoError:
                self._in_dev = None
                self.error.emit('마이크를 열 수 없습니다')
                return False
            self._in_dev.readyRead.connect(self._read_input)
            self._poll.start()
            return True
        except Exception:
            self._in_dev = None
            self.error.emit('마이크를 열 수 없습니다')
            return False

    def stop_input(self):
        self._poll.stop()
        try:
            if self._input is not None:
                self._input.stop()
                self._input.deleteLater()
        except Exception:
            pass
        self._input = None
        self._in_dev = None
        self._in_pending.clear()
        self._in_raw.clear()

    def _read_input(self):
        dev = self._in_dev
        if dev is None:
            return
        try:
            data = bytes(dev.readAll())
        except Exception:
            return
        if not data:
            return
        fmt = self._in_fmt
        if _is_target_format(fmt):
            self._in_pending.extend(data)
        else:
            # 장치 형식이 다르면 샘플 경계에 맞춰 변환 (약 40ms 단위)
            self._in_raw.extend(data)
            frame = max(1, fmt.channelCount() * max(1, fmt.sampleSize() // 8))
            chunk = int(fmt.sampleRate() * FRAME_MS / 1000) * frame
            while len(self._in_raw) >= chunk:
                raw = bytes(self._in_raw[:chunk])
                del self._in_raw[:chunk]
                self._in_pending.extend(device_to_pcm16k(raw, fmt))
        while len(self._in_pending) >= FRAME_BYTES:
            frame_bytes = bytes(self._in_pending[:FRAME_BYTES])
            del self._in_pending[:FRAME_BYTES]
            self.frame_captured.emit(frame_bytes)

    # -- 출력
    def start_output(self):
        if self._out_dev is not None:
            return True
        self._clock0 = time.monotonic()
        self._popped = 0
        if not self.enabled:
            self.error.emit('스피커를 찾을 수 없습니다')
            return False
        try:
            info = QAudioDeviceInfo.defaultOutputDevice()
            if info.isNull():
                self.error.emit('스피커를 찾을 수 없습니다')
                return False
            fmt = target_audio_format()
            if not info.isFormatSupported(fmt):
                fmt = info.nearestFormat(fmt)
                if _np_dtype(fmt) is None:
                    self.error.emit('스피커 형식을 지원하지 않습니다')
                    return False
            self._out_fmt = fmt
            self._output = QAudioOutput(info, fmt, self)
            per_sec = fmt.sampleRate() * fmt.channelCount() * max(1, fmt.sampleSize() // 8)
            self._output.setBufferSize(int(per_sec * 0.3))
            self._output.setVolume(self.volume)
            self._out_dev = self._output.start()
            if self._out_dev is None:
                self.error.emit('스피커를 열 수 없습니다')
                return False
            return True
        except Exception:
            self._out_dev = None
            self.error.emit('스피커를 열 수 없습니다')
            return False

    def stop_output(self):
        try:
            if self._output is not None:
                self._output.stop()
                self._output.deleteLater()
        except Exception:
            pass
        self._output = None
        self._out_dev = None

    def set_volume(self, volume):
        self.volume = float(volume)
        try:
            if self._output is not None:
                self._output.setVolume(self.volume)
        except Exception:
            pass

    def play_tick(self, mixer):
        """믹서에서 재생할 만큼 꺼내 출력. 실제로 재생(소비)한 16k 프레임 목록 반환"""
        played = []
        if self._out_dev is not None and self._output is not None:
            try:
                fmt = self._out_fmt
                out_frame = len(pcm16k_to_device(b'\x00' * FRAME_BYTES, fmt)) or FRAME_BYTES
                free = self._output.bytesFree()
                while free >= out_frame:
                    frame = mixer.pop_frame()
                    if frame is None:
                        break
                    data = pcm16k_to_device(frame, fmt)
                    self._out_dev.write(data)
                    free -= len(data)
                    played.append(frame)
                return played
            except Exception:
                self.stop_output()
                self.error.emit('스피커 출력 중 오류가 발생했습니다')
        # 출력 장치가 없으면 실시간 시계에 맞춰 소비 (말하는 표시·녹음 유지)
        if self._clock0 is None:
            self._clock0 = time.monotonic()
        due = int((time.monotonic() - self._clock0) * 1000 / FRAME_MS) - self._popped
        if due > 10:                                         # 오래 멈췄다면 따라잡지 않음
            self._popped += due - 10
            due = 10
        for _ in range(max(0, due)):
            self._popped += 1
            frame = mixer.pop_frame()
            if frame is not None:
                played.append(frame)
        return played

    def stop(self):
        self.stop_input()
        self.stop_output()


# ---------------------------------------------------------------- 카메라
def video_frame_to_image(frame):
    """QVideoFrame → QImage (RGB 계열·NV12·UYVY·YUYV 지원, 실패 시 None)"""
    f = QVideoFrame(frame)
    if not f.isValid() or not f.map(QAbstractVideoBuffer.ReadOnly):
        return None
    try:
        w, h = f.width(), f.height()
        pf = f.pixelFormat()
        fmt = QVideoFrame.imageFormatFromPixelFormat(pf)
        if fmt != QImage.Format_Invalid:
            bits = f.bits()
            bits.setsize(f.mappedBytes())
            return QImage(bytes(bits), w, h, f.bytesPerLine(), fmt).copy()
        if np is None:
            return None
        if pf == QVideoFrame.Format_NV12:
            planes = f.planeCount()
            y_bits = f.bits(0)
            bpl0 = f.bytesPerLine(0)
            y_bits.setsize(bpl0 * h)
            y = np.frombuffer(bytes(y_bits), dtype=np.uint8).reshape(h, bpl0)[:, :w]
            if planes >= 2:
                uv_bits = f.bits(1)
                bpl1 = f.bytesPerLine(1)
                uv_bits.setsize(bpl1 * (h // 2))
                uv = np.frombuffer(bytes(uv_bits), dtype=np.uint8).reshape(h // 2, bpl1)[:, :w]
            else:
                allb = f.bits()
                allb.setsize(f.mappedBytes())
                arr = np.frombuffer(bytes(allb), dtype=np.uint8)
                uv = arr[bpl0 * h: bpl0 * h + bpl0 * (h // 2)].reshape(h // 2, bpl0)[:, :w]
            u = np.repeat(np.repeat(uv[:, 0::2], 2, axis=1), 2, axis=0)[:h, :w]
            v = np.repeat(np.repeat(uv[:, 1::2], 2, axis=1), 2, axis=0)[:h, :w]
            return _yuv_to_qimage(y, u, v)
        if pf in (QVideoFrame.Format_UYVY, QVideoFrame.Format_YUYV):
            bits = f.bits()
            bits.setsize(f.mappedBytes())
            bpl = f.bytesPerLine()
            arr = np.frombuffer(bytes(bits), dtype=np.uint8)[:bpl * h].reshape(h, bpl)[:, :w * 2]
            if pf == QVideoFrame.Format_UYVY:
                y = arr[:, 1::2]
                u = np.repeat(arr[:, 0::4], 2, axis=1)[:, :w]
                v = np.repeat(arr[:, 2::4], 2, axis=1)[:, :w]
            else:
                y = arr[:, 0::2]
                u = np.repeat(arr[:, 1::4], 2, axis=1)[:, :w]
                v = np.repeat(arr[:, 3::4], 2, axis=1)[:, :w]
            return _yuv_to_qimage(y, u, v)
        return None
    except Exception:
        return None
    finally:
        f.unmap()


def _yuv_to_qimage(y, u, v):
    y = y.astype(np.float32)
    u = u.astype(np.float32) - 128.0
    v = v.astype(np.float32) - 128.0
    r = y + 1.402 * v
    g = y - 0.344136 * u - 0.714136 * v
    b = y + 1.772 * u
    rgb = np.clip(np.dstack([r, g, b]), 0, 255).astype(np.uint8)
    h, w = rgb.shape[:2]
    data = np.ascontiguousarray(rgb).tobytes()
    return QImage(data, w, h, w * 3, QImage.Format_RGB888).copy()


if HAS_MULTIMEDIA:
    class _FrameGrabber(QAbstractVideoSurface):
        """뷰파인더 프레임을 약 VIDEO_FPS 로 QImage 로 꺼낸다"""
        frame_ready = pyqtSignal(QImage)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._last = 0.0

        def supportedPixelFormats(self, handle_type=QAbstractVideoBuffer.NoHandle):
            if handle_type != QAbstractVideoBuffer.NoHandle:
                return []
            return [QVideoFrame.Format_RGB32, QVideoFrame.Format_ARGB32, QVideoFrame.Format_ARGB32_Premultiplied,
                    QVideoFrame.Format_RGB24, QVideoFrame.Format_NV12, QVideoFrame.Format_UYVY,
                    QVideoFrame.Format_YUYV]

        def present(self, frame):
            now = time.monotonic()
            if now - self._last < 1.0 / VIDEO_FPS:
                return True
            self._last = now
            img = video_frame_to_image(frame)
            if img is not None and not img.isNull():
                try:
                    if self.surfaceFormat().scanLineDirection() == QVideoSurfaceFormat.BottomToTop:
                        img = img.mirrored(False, True)
                except Exception:
                    pass
                self.frame_ready.emit(img)
            return True
else:  # pragma: no cover
    _FrameGrabber = None


class CameraCapture(QObject):
    frame_captured = pyqtSignal(QImage)
    error = pyqtSignal(str)

    def __init__(self, parent=None, enabled=None):
        super().__init__(parent)
        self.enabled = devices_enabled() if enabled is None else enabled
        self._camera = None
        self._grabber = None

    @property
    def active(self):
        return self._camera is not None

    def available(self):
        if not self.enabled:
            return False
        try:
            return bool(QCameraInfo.availableCameras())
        except Exception:
            return False

    def start(self):
        if self._camera is not None:
            return True
        if not self.available():
            self.error.emit('카메라를 찾을 수 없습니다')
            return False
        try:
            self._grabber = _FrameGrabber(self)
            self._grabber.frame_ready.connect(self.frame_captured)
            self._grabber.frame_ready.connect(self._mark_frame)
            self._camera = QCamera(QCameraInfo.defaultCamera(), self)
            self._camera.error.connect(self._on_error)
            self._camera.setCaptureMode(QCamera.CaptureViewfinder)
            self._camera.setViewfinder(self._grabber)
            self._got_frame = False
            self._camera.start()
            QTimer.singleShot(4000, self._watchdog)
            return True
        except Exception:
            self.stop()
            self.error.emit('카메라를 열 수 없습니다')
            return False

    def _mark_frame(self, image):
        self._got_frame = True

    def _watchdog(self):
        # 카메라가 켜졌는데 영상이 오지 않음 (macOS 카메라 권한 거부 등)
        if self._camera is not None and not getattr(self, '_got_frame', True):
            self.error.emit('카메라 영상을 받을 수 없습니다. 시스템 설정에서 카메라 권한을 확인하세요')

    def _on_error(self, *args):
        self.error.emit('카메라를 사용할 수 없습니다')
        QTimer.singleShot(0, self.stop)

    def stop(self):
        try:
            if self._camera is not None:
                self._camera.stop()
                self._camera.unload()
                self._camera.deleteLater()
        except Exception:
            pass
        self._camera = None
        self._grabber = None


def grab_screen():
    """주 화면 캡처 → QImage (실패 시 None)"""
    try:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        pm = screen.grabWindow(0)
        if pm.isNull():
            return None
        return pm.toImage()
    except Exception:
        return None


# ---------------------------------------------------------------- 팔레트 / 아이콘
def pal(key):
    dark = theme.is_dark()
    table = {
        'bg_top': ('#E6ECF5', '#1B1F27'),
        'bg_bottom': ('#F8F9FB', '#111317'),
        'tile': ('#FFFFFF', '#242830'),
        'tile_line': ('#E3E7EE', '#30353E'),
        'video_tile': ('#1E2229', '#1A1D23'),
        'text': ('#191919', '#EDEDED'),
        'sub': ('#6B7280', '#A0A6B0'),
        'btn': ('#FFFFFF', '#2C313A'),
        'btn_line': ('#DDE2EA', '#3A404B'),
        'btn_fg': ('#2B2F36', '#E6E8EC'),
        'btn_on': ('#2B2F36', '#F2F3F5'),
        'btn_on_fg': ('#FFFFFF', '#16181D'),
        'chip': ('rgba(0,0,0,0.05)', 'rgba(255,255,255,0.07)'),
    }
    value = table[key][1 if dark else 0]
    return value


def qpal(key):
    value = pal(key)
    if value.startswith('rgba'):
        r, g, b, a = [x.strip() for x in value[5:-1].split(',')]
        color = QColor(int(r), int(g), int(b))
        color.setAlphaF(float(a))
        return color
    return QColor(value)


def _glyph_path(name):
    """24x24 좌표 아이콘 → (path, filled)"""
    p = QPainterPath()
    filled = False
    if name in ('mic', 'mic_off'):
        p.addRoundedRect(QRectF(9, 2.5, 6, 11.5), 3, 3)
        p.moveTo(5.5, 11)
        p.cubicTo(5.5, 19, 18.5, 19, 18.5, 11)
        p.moveTo(12, 17.2)
        p.lineTo(12, 21)
        p.moveTo(8.5, 21)
        p.lineTo(15.5, 21)
        if name == 'mic_off':
            p.moveTo(4, 3.5)
            p.lineTo(20, 20.5)
    elif name in ('speaker', 'speaker_off'):
        p.moveTo(3.5, 9)
        p.lineTo(7.5, 9)
        p.lineTo(12.5, 4.5)
        p.lineTo(12.5, 19.5)
        p.lineTo(7.5, 15)
        p.lineTo(3.5, 15)
        p.closeSubpath()
        if name == 'speaker':
            p.moveTo(15.5, 9)
            p.cubicTo(17.2, 10.5, 17.2, 13.5, 15.5, 15)
            p.moveTo(18, 6.5)
            p.cubicTo(21.5, 9.5, 21.5, 14.5, 18, 17.5)
        else:
            p.moveTo(16, 9.5)
            p.lineTo(21, 14.5)
            p.moveTo(21, 9.5)
            p.lineTo(16, 14.5)
    elif name == 'record':
        p.addEllipse(QRectF(3, 3, 18, 18))
        p.addEllipse(QRectF(8, 8, 8, 8))
    elif name in ('camera', 'camera_off'):
        p.addRoundedRect(QRectF(2.5, 6.5, 13.5, 11), 2.5, 2.5)
        p.moveTo(16, 10.5)
        p.lineTo(21.5, 7.5)
        p.lineTo(21.5, 16.5)
        p.lineTo(16, 13.5)
        if name == 'camera_off':
            p.moveTo(3, 3.5)
            p.lineTo(20, 20.5)
    elif name == 'screen':
        p.addRoundedRect(QRectF(2.5, 4, 19, 12.5), 2, 2)
        p.moveTo(12, 16.5)
        p.lineTo(12, 20)
        p.moveTo(8, 20.5)
        p.lineTo(16, 20.5)
        p.moveTo(12, 13)
        p.lineTo(12, 7.5)
        p.moveTo(9.5, 10)
        p.lineTo(12, 7.5)
        p.lineTo(14.5, 10)
    elif name == 'blur':
        p.addEllipse(QRectF(9, 5.5, 6, 6))
        p.moveTo(6.5, 19.5)
        p.cubicTo(6.5, 14.5, 17.5, 14.5, 17.5, 19.5)
        for x, y in ((3, 6), (3, 12), (3, 18), (21, 6), (21, 12), (21, 18), (12, 2)):
            p.addEllipse(QPointF(x, y), 0.6, 0.6)
    elif name == 'hangup':
        p.moveTo(3, 13.5)
        p.cubicTo(7.5, 8.5, 16.5, 8.5, 21, 13.5)
        p.lineTo(19.5, 16)
        p.lineTo(16, 14.5)
        p.lineTo(15.8, 12.2)
        p.cubicTo(13.5, 11.4, 10.5, 11.4, 8.2, 12.2)
        p.lineTo(8, 14.5)
        p.lineTo(4.5, 16)
        p.closeSubpath()
        filled = True
    elif name == 'hand':
        for i, (x, top) in enumerate(((6, 8), (9, 4.5), (12, 3.5), (15, 5))):
            p.addRoundedRect(QRectF(x - 1.2, top, 2.4, 9), 1.2, 1.2)
        p.addRoundedRect(QRectF(4.8, 10.5, 12.6, 10.5), 4.5, 4.5)
        p.moveTo(17, 14)
        p.cubicTo(18, 11.5, 19.5, 10.5, 20.5, 11.5)
        p.cubicTo(19.5, 13.5, 18.5, 16, 16.5, 18)
        p = p.simplified()
        filled = True
    elif name == 'leave':
        p.moveTo(10, 4)
        p.lineTo(5, 4)
        p.lineTo(5, 20)
        p.lineTo(10, 20)
        p.moveTo(10, 12)
        p.lineTo(20, 12)
        p.moveTo(16.5, 8.5)
        p.lineTo(20, 12)
        p.lineTo(16.5, 15.5)
    else:
        p.addEllipse(QRectF(5, 5, 14, 14))
    return p, filled


def paint_glyph(painter, name, rect, color, stroke=1.8):
    path, filled = _glyph_path(name)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    color = QColor(color)
    if filled:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
    else:
        pen = QPen(color, stroke)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if name == 'record':
            painter.drawEllipse(QRectF(3, 3, 18, 18))
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(7.5, 7.5, 9, 9))
            painter.restore()
            return
    painter.drawPath(path)
    painter.restore()


def glyph_icon(name, color, size=18):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_glyph(p, name, QRectF(0, 0, size, size), color)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- 위젯
class CallButton(QAbstractButton):
    """원형 통화 버튼 + 아래 라벨"""
    DIAMETER = 54

    def __init__(self, glyph, label, checkable=True, danger=False, parent=None, on_glyph=None, on_label=None):
        super().__init__(parent)
        self.glyph = glyph
        self.on_glyph = on_glyph or glyph
        self.label = label
        self.on_label = on_label or label
        self.danger = danger
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(78, self.DIAMETER + 30)
        self.setAccessibleName(label)
        self._hover = False

    def text_now(self):
        return self.on_label if self.isChecked() else self.label

    def sizeHint(self):
        return QSize(78, self.DIAMETER + 30)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = self.DIAMETER
        circle = QRectF((self.width() - d) / 2.0, 1, d, d)
        on = self.isChecked()
        if self.danger:
            bg, fg, line = QColor(DANGER), QColor('#FFFFFF'), None
        elif on:
            bg, fg, line = qpal('btn_on'), qpal('btn_on_fg'), None
        else:
            bg, fg, line = qpal('btn'), qpal('btn_fg'), qpal('btn_line')
        if not self.isEnabled():
            bg.setAlphaF(bg.alphaF() * 0.5)
            fg.setAlphaF(0.4)
        if self._hover and self.isEnabled():
            bg = bg.darker(108) if not theme.is_dark() or self.danger or on else bg.lighter(120)
        p.setPen(QPen(line, 1) if line is not None else Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))
        g = 24
        paint_glyph(p, self.on_glyph if on else self.glyph,
                    QRectF(circle.center().x() - g / 2, circle.center().y() - g / 2, g, g), fg)
        p.setPen(qpal('sub') if not on else qpal('text'))
        p.setFont(theme.font('caption', bold=on))
        p.drawText(QRectF(0, d + 6, self.width(), 20), Qt.AlignHCenter | Qt.AlignTop, self.text_now())
        p.end()


class ParticipantTile(QWidget):
    """참여자 한 명 - 아바타(또는 영상) + 말하는 중 링 + 이름/상태"""

    def __init__(self, state, username, video_style=False, compact=False, parent=None):
        super().__init__(parent)
        self.state = state
        self.username = username
        self.video_style = video_style
        self.compact = compact
        self.role = 'speaker'
        self.muted = False
        self.video_on = False
        self.hand = False
        self.is_me = username == state.me
        self.level = 0.0
        self.image = None
        self.room_id = ''
        self.setMinimumSize(96, 96 if compact else 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def name(self):
        name = self.state.member_name(self.room_id, self.username) if self.room_id else \
            self.state.display_name(self.username)
        return name + (' (나)' if self.is_me else '')

    def set_info(self, info, room_id=''):
        self.room_id = room_id
        self.role = info.get('role', 'speaker')
        self.muted = bool(info.get('muted'))
        self.video_on = bool(info.get('video'))
        self.hand = bool(info.get('hand'))
        self.update()

    def set_level(self, level):
        level = max(0.0, min(1.0, level))
        if abs(level - self.level) > 0.01 or (level == 0 and self.level):
            self.level = level
            self.update()

    def set_image(self, image):
        self.image = image
        self.update()

    @property
    def speaking(self):
        return self.level > SPEAKING_THRESHOLD and not self.muted

    def showing_video(self):
        return self.video_on and self.image is not None and not self.image.isNull()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        radius = 12 if self.compact else 16
        clip = QPainterPath()
        clip.addRoundedRect(r, radius, radius)
        if self.video_style:
            p.fillPath(clip, qpal('video_tile'))
        elif not self.compact:
            p.fillPath(clip, qpal('tile'))
            p.setPen(QPen(qpal('tile_line'), 1))
            p.drawPath(clip)
        if self.showing_video():
            p.save()
            p.setClipPath(clip)
            img = self.image
            scale = max(r.width() / img.width(), r.height() / img.height())
            w, h = img.width() * scale, img.height() * scale
            p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), img)
            shade = QLinearGradient(0, r.bottom() - 46, 0, r.bottom())
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor(0, 0, 0, 130))
            p.fillRect(QRectF(r.left(), r.bottom() - 46, r.width(), 46), shade)
            p.restore()
            if self.speaking:
                p.setPen(QPen(QColor(SPEAK_COLOR), 3))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1, radius - 1)
            self._paint_name_pill(p, r, light_text=True)
        else:
            self._paint_avatar_layout(p, r)
        if self.hand:
            hr = QRectF(r.right() - 34, r.top() + 8, 26, 26)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#FFB800'))
            p.drawEllipse(hr)
            paint_glyph(p, 'hand', hr.adjusted(5, 5, -5, -5), '#FFFFFF')
        p.end()

    def _paint_avatar_layout(self, p, r):
        text_h = 34 if not self.compact else 30
        ratio = 0.6 if self.compact else 0.5
        size = int(max(36, min(r.width() * ratio, (r.height() - text_h) * (0.7 if self.compact else 0.62), 128)))
        cx = r.center().x()
        cy = r.top() + (r.height() - text_h) / 2.0 + 4
        av = QRectF(cx - size / 2.0, cy - size / 2.0, size, size)
        if self.speaking:
            glow = QColor(SPEAK_COLOR)
            amount = min(1.0, self.level * 8)
            for i, alpha in ((3, 0.18), (2, 0.32)):
                glow.setAlphaF(alpha * (0.5 + amount / 2))
                pad = 4 + i * (2 + amount * 3)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawRoundedRect(av.adjusted(-pad, -pad, pad, pad), (size + 2 * pad) * 0.4, (size + 2 * pad) * 0.4)
            p.setPen(QPen(QColor(SPEAK_COLOR), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(av.adjusted(-3, -3, 3, 3), (size + 6) * 0.39, (size + 6) * 0.39)
        p.drawPixmap(av.toRect(), user_avatar_pixmap(self.state, self.username, size))
        if self.muted:
            mr = QRectF(av.right() - size * 0.26, av.bottom() - size * 0.26, max(20, size * 0.3), max(20, size * 0.3))
            p.setPen(QPen(qpal('tile') if not self.video_style else qpal('video_tile'), 2))
            p.setBrush(QColor(DANGER))
            p.drawEllipse(mr)
            paint_glyph(p, 'mic_off', mr.adjusted(mr.width() * 0.2, mr.height() * 0.2,
                                                  -mr.width() * 0.2, -mr.height() * 0.2), '#FFFFFF', 2.2)
        # 이름 + 역할/상태
        light = self.video_style
        p.setPen(QColor('#FFFFFF') if light else qpal('text'))
        p.setFont(theme.font('meta' if self.compact else 'emphasis', bold=True))
        name_rect = QRectF(r.left() + 6, av.bottom() + (12 if self.speaking else 8), r.width() - 12, 20)
        fm = QFontMetrics(p.font())
        p.drawText(name_rect, Qt.AlignHCenter | Qt.AlignTop, fm.elidedText(self.name(), Qt.ElideRight, int(name_rect.width())))
        sub = ''
        if self.role == 'host' and self.state.calls and self.compact:
            sub = '진행자'
        elif self.speaking:
            sub = '말하는 중'
        if sub and r.bottom() - name_rect.bottom() > 12:
            p.setFont(theme.font('caption'))
            p.setPen(QColor(SPEAK_COLOR) if sub == '말하는 중' else (QColor('#C8CCD4') if light else qpal('sub')))
            p.drawText(QRectF(r.left(), name_rect.bottom() - 2, r.width(), 16), Qt.AlignHCenter | Qt.AlignTop, sub)

    def _paint_name_pill(self, p, r, light_text=True):
        p.setFont(theme.font('meta', bold=True))
        fm = QFontMetrics(p.font())
        name = fm.elidedText(self.name(), Qt.ElideRight, int(r.width() - 60))
        w = fm.horizontalAdvance(name) + (24 if self.muted else 0) + 18
        pill = QRectF(r.left() + 10, r.bottom() - 34, w, 24)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRoundedRect(pill, 12, 12)
        x = pill.left() + 9
        if self.muted:
            paint_glyph(p, 'mic_off', QRectF(x - 1, pill.top() + 4, 16, 16), '#FF6B62', 2.0)
            x += 20
        p.setPen(QColor('#FFFFFF'))
        p.drawText(QRectF(x, pill.top(), pill.right() - x, pill.height()), Qt.AlignVCenter | Qt.AlignLeft, name)


class ScreenTile(QWidget):
    """화면 공유 - 크게 표시 (비율 유지)"""

    def __init__(self, state, username, parent=None):
        super().__init__(parent)
        self.state = state
        self.username = username
        self.image = None
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, image):
        self.image = image
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QColor('#0E1014'))
        mine = self.username == self.state.me
        if self.image is not None and not self.image.isNull() and not mine:
            img = self.image
            scale = min(r.width() / img.width(), (r.height() - 8) / img.height())
            w, h = img.width() * scale, img.height() * scale
            p.save()
            p.setClipPath(path)
            p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), img)
            p.restore()
        else:
            p.setPen(QColor('#C8CCD4'))
            p.setFont(theme.font('emphasis', bold=True))
            text = '내 화면을 공유하고 있습니다' if mine else '화면을 불러오는 중...'
            paint_glyph(p, 'screen', QRectF(r.center().x() - 22, r.center().y() - 50, 44, 44), '#C8CCD4')
            p.drawText(r.adjusted(0, 20, 0, 0), Qt.AlignCenter, text)
        p.setFont(theme.font('meta', bold=True))
        label = f'{self.state.display_name(self.username)}님의 화면'
        fm = QFontMetrics(p.font())
        pill = QRectF(r.left() + 12, r.top() + 12, fm.horizontalAdvance(label) + 36, 26)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(pill, 13, 13)
        paint_glyph(p, 'screen', QRectF(pill.left() + 8, pill.top() + 5, 16, 16), '#FFFFFF')
        p.setPen(QColor('#FFFFFF'))
        p.drawText(pill.adjusted(28, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
        p.end()


class _Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text, msec=2600):
        self.setStyleSheet(f'background: rgba(20,22,26,0.88); color: #FFFFFF; border-radius: 10px;'
                           f' padding: 9px 16px; font-size: {theme.fs('meta')}px;')
        self.setText(text)
        parent = self.parentWidget()
        fm = QFontMetrics(theme.font('meta'))
        width = min(max(160, parent.width() - 40), fm.horizontalAdvance(text) + 40)
        self.setFixedWidth(width)
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, max(10, parent.height() - self.height() - 128))
        self.show()
        self.raise_()
        self._timer.start(msec)


# ---------------------------------------------------------------- 통화 창
_WINDOWS = {}


def show_call(state, call_id):
    """call_id 당 창 하나. 이미 열려 있으면 앞으로 가져온다"""
    call_id = int(call_id)
    window = _WINDOWS.get(call_id)
    if window is not None and not window.closed:
        try:
            _bring_front(window)
            return window
        except RuntimeError:  # 이미 삭제된 창
            _WINDOWS.pop(call_id, None)
    window = CallWindow(state, call_id)
    _WINDOWS[call_id] = window
    _bring_front(window)
    return window


def _bring_front(window):
    window.show()
    app = QGuiApplication.instance()
    if app is not None and app.platformName() in ('offscreen', 'minimal'):
        return
    window.raise_()
    window.activateWindow()


class CallWindow(QWidget):
    END_DELAY_MS = 1800

    def __init__(self, state, call_id, parent=None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.state = state
        self.call_id = int(call_id)
        self.call = dict(state.calls.get(self.call_id) or {})
        self.mode = self.call.get('mode') or 'voice'
        self.room_id = self.call.get('room_id', '')
        self.closed = False
        self.ended = False
        self._left = False
        self._was_participant = False
        self._initialized_me = False
        self.muted = False
        self.speaker_on = True
        self.camera_on = False
        self.screen_on = False
        self.blur_on = False
        self.my_role = 'speaker'
        self.my_hand = False
        self.recorder = None
        self.toasts = []
        self._seq = {'audio': 0, 'video': 0, 'screen': 0}
        self._levels = {}
        self._frames = {}
        self._screens = {}
        self._tiles = {}
        self._screen_tiles = {}
        self._rec_in = deque()
        self._rec_out = deque()
        self._opened_at = datetime.now()
        self._screen_error_shown = False
        self.role_buttons = {}

        self.mixer = JitterMixer()
        self.audio = AudioEngine(self)
        self.audio.frame_captured.connect(self._on_audio_frame)
        self.audio.error.connect(self.show_toast)
        self.camera = CameraCapture(self)
        self.camera.frame_captured.connect(self._on_camera_frame)
        self.camera.error.connect(self.show_toast)

        self._build()
        self._apply_title()

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_second)
        self._clock.start(1000)
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_tick)
        self._play_timer.start(20)
        self._decay = QTimer(self)
        self._decay.timeout.connect(self._decay_levels)
        self._decay.start(120)
        self._screen_timer = QTimer(self)
        self._screen_timer.timeout.connect(self._capture_screen)

        state.calls_changed.connect(self._on_calls_changed)
        state.call_ended.connect(self._on_call_ended)
        state.call_media.connect(self._on_media)
        state.connection_lost.connect(self._on_connection_lost)

        self._refresh()
        self._tick_second()
        self._start_devices()

    # ------------------------------------------------------------ UI 구성
    def _build(self):
        sizes = {'voice': (440, 660), 'video': (960, 680), 'voiceroom': (480, 740)}
        w, h = sizes.get(self.mode, (440, 660))
        self.resize(w, h)
        self.setMinimumSize(380, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        # 헤더: 방 이름 / 모드 · 시간 / 녹음 중 배지
        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setObjectName('callTitle')
        self.sub_label = QLabel()
        self.sub_label.setObjectName('callSub')
        titles.addWidget(self.title_label)
        titles.addWidget(self.sub_label)
        header.addLayout(titles, 1)
        self.rec_badge = RecBadge('녹음 중')
        self.rec_badge.setObjectName('recBadge')
        self.rec_badge.hide()
        header.addWidget(self.rec_badge, 0, Qt.AlignTop)
        self.blur_badge = QLabel('배경 흐림')
        self.blur_badge.setObjectName('chipBadge')
        self.blur_badge.hide()
        header.addWidget(self.blur_badge, 0, Qt.AlignTop)
        root.addLayout(header)

        # 본문
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)
        root.addWidget(self.body, 1)

        if self.mode == 'voiceroom':
            self._build_voiceroom()
        else:
            self.screen_area = QWidget()
            self.screen_layout = QHBoxLayout(self.screen_area)
            self.screen_layout.setContentsMargins(0, 0, 0, 0)
            self.screen_area.hide()
            self.grid_host = QWidget()
            self.grid = QGridLayout(self.grid_host)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.grid.setSpacing(10)
            self.body_layout.addWidget(self.screen_area, 3)
            self.body_layout.addWidget(self.grid_host, 2)

        self.status_label = QLabel('')
        self.status_label.setObjectName('callStatus')
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.hide()
        root.addWidget(self.status_label)

        # 하단 버튼
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addStretch(1)
        self.btn_camera = CallButton('camera', '카메라', on_glyph='camera', parent=self)
        self.btn_mute = CallButton('mic', '음소거', on_glyph='mic_off', on_label='음소거 해제', parent=self)
        self.btn_speaker = CallButton('speaker', '스피커', on_glyph='speaker', parent=self)
        self.btn_record = CallButton('record', '녹음', on_label='녹음 중지', parent=self)
        self.btn_screen = CallButton('screen', '화면 공유', on_label='공유 중지', parent=self)
        self.btn_blur = CallButton('blur', '배경 흐림', parent=self)
        self.btn_hand = CallButton('hand', '손들기', on_label='손 내리기', parent=self)
        self.btn_end = CallButton('hangup', '종료', checkable=False, danger=True, parent=self)
        if self.mode == 'voiceroom':
            self.btn_end.glyph = 'leave'
            self.btn_end.label = '나가기'
        self.btn_camera.setObjectName('btnCamera')
        self.btn_mute.setObjectName('btnMute')
        self.btn_speaker.setObjectName('btnSpeaker')
        self.btn_record.setObjectName('btnRecord')
        self.btn_screen.setObjectName('btnScreen')
        self.btn_blur.setObjectName('btnBlur')
        self.btn_hand.setObjectName('btnHand')
        self.btn_end.setObjectName('btnEnd')
        order = {
            'voice': [self.btn_mute, self.btn_speaker, self.btn_record, self.btn_end],
            'video': [self.btn_camera, self.btn_mute, self.btn_screen, self.btn_blur, self.btn_end],
            'voiceroom': [self.btn_mute, self.btn_hand, self.btn_speaker, self.btn_end],
        }[self.mode if self.mode in ('voice', 'video', 'voiceroom') else 'voice']
        for b in (self.btn_camera, self.btn_mute, self.btn_speaker, self.btn_record, self.btn_screen,
                  self.btn_blur, self.btn_hand, self.btn_end):
            if b in order:
                bar.addWidget(b)
            else:
                b.hide()
        bar.addStretch(1)
        self.btn_speaker.setChecked(False)
        self.btn_speaker.on_glyph = 'speaker_off'
        self.btn_speaker.on_label = '스피커 꺼짐'
        self.btn_camera.glyph, self.btn_camera.on_glyph = 'camera', 'camera_off'
        self.btn_camera.on_label = '카메라 꺼짐'
        root.addLayout(bar)

        self.btn_mute.clicked.connect(self.toggle_mute)
        self.btn_speaker.clicked.connect(self.toggle_speaker)
        self.btn_record.clicked.connect(self.toggle_record)
        self.btn_camera.clicked.connect(self.toggle_camera)
        self.btn_screen.clicked.connect(self.toggle_screen)
        self.btn_blur.clicked.connect(self.toggle_blur)
        self.btn_hand.clicked.connect(self.toggle_hand)
        self.btn_end.clicked.connect(self.hang_up)

        self.toast = _Toast(self)
        self.end_overlay = QLabel(self)
        self.end_overlay.setAlignment(Qt.AlignCenter)
        self.end_overlay.hide()
        self._apply_style()

    def _build_voiceroom(self):
        self.remain_label = QLabel()
        self.remain_label.setObjectName('remainChip')
        row = QHBoxLayout()
        row.addWidget(self.remain_label)
        row.addStretch(1)
        self.body_layout.addLayout(row)

        self.stage_title = QLabel('무대')
        self.stage_title.setObjectName('sectionTitle')
        self.body_layout.addWidget(self.stage_title)
        self.stage_host = QFrame()
        self.stage_host.setObjectName('stageCard')
        self.grid = QGridLayout(self.stage_host)
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setSpacing(6)
        self.grid_host = self.stage_host
        self.body_layout.addWidget(self.stage_host, 2)

        self.manage_title = QLabel('손 든 참여자')
        self.manage_title.setObjectName('sectionTitle')
        self.body_layout.addWidget(self.manage_title)
        self.manage_area = QScrollArea()
        self.manage_area.setWidgetResizable(True)
        self.manage_area.setFrameShape(QFrame.NoFrame)
        self.manage_area.setObjectName('manageArea')
        self.manage_host = QWidget()
        self.manage_host.setObjectName('manageHost')
        self.manage_layout = QVBoxLayout(self.manage_host)
        self.manage_layout.setContentsMargins(0, 0, 0, 0)
        self.manage_layout.setSpacing(6)
        self.manage_area.setWidget(self.manage_host)
        self.body_layout.addWidget(self.manage_area, 2)

        self.listener_title = QLabel('리스너')
        self.listener_title.setObjectName('sectionTitle')
        self.body_layout.addWidget(self.listener_title)
        self.listener_list = QListWidget()
        self.listener_list.setObjectName('listenerList')
        self.listener_list.setViewMode(QListView.IconMode)
        self.listener_list.setIconSize(QSize(40, 40))
        self.listener_list.setGridSize(QSize(74, 70))
        self.listener_list.setResizeMode(QListView.Adjust)
        self.listener_list.setMovement(QListView.Static)
        self.listener_list.setWordWrap(False)
        self.listener_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listener_list.customContextMenuRequested.connect(self._listener_menu)
        self.body_layout.addWidget(self.listener_list, 2)

    def _apply_style(self):
        self.setProperty('titleBarColor', pal('bg_top'))   # macOS 제목 표시줄을 위쪽 배경색과 같게 (widgets.DialogTitleBarFilter)
        self.setStyleSheet(f"""
            QLabel {{ color: {pal('text')}; background: transparent; }}
            QLabel#callTitle {{ font-size: {theme.fs('heading')}px; font-weight: bold; }}
            QLabel#callSub {{ color: {pal('sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#callStatus {{ color: {pal('sub')}; font-size: {theme.fs('meta')}px; }}
            QLabel#sectionTitle {{ color: {pal('sub')}; font-size: {theme.fs('meta')}px; font-weight: bold; padding-left: 2px; }}
            QLabel#recBadge {{ background: {DANGER}; color: #FFFFFF; border-radius: 9px; padding: 2px 10px;
                               font-size: {theme.fs('caption')}px; font-weight: bold; }}
            QLabel#chipBadge, QLabel#remainChip {{ background: {pal('chip')}; color: {pal('sub')}; border-radius: 11px;
                               padding: 4px 10px; font-size: {theme.fs('caption')}px; }}
            QFrame#stageCard {{ background: {pal('tile')}; border: 1px solid {pal('tile_line')}; border-radius: 16px; }}
            QScrollArea#manageArea, QWidget#manageHost {{ background: transparent; border: none; }}
            QFrame#manageRow {{ background: {pal('tile')}; border: 1px solid {pal('tile_line')}; border-radius: 12px; }}
            QListWidget#listenerList {{ background: {pal('tile')}; border: 1px solid {pal('tile_line')};
                               border-radius: 16px; padding: 6px; color: {pal('text')}; font-size: {theme.fs('caption')}px; }}
            QListWidget#listenerList::item {{ border-radius: 8px; padding: 2px; }}
            QListWidget#listenerList::item:selected {{ background: {pal('chip')}; color: {pal('text')}; }}
            QPushButton#roleBtn {{ background: {pal('btn')}; color: {pal('btn_fg')}; border: 1px solid {pal('btn_line')};
                               border-radius: 11px; padding: 3px 10px; min-height: 18px; font-size: {theme.fs('caption')}px; }}
            QPushButton#roleBtn:hover {{ background: {pal('chip')}; }}
            QPushButton#roleBtn[primary="true"] {{ background: {theme.c('yellow')}; color: {theme.c('on_accent')};
                               border: none; font-weight: bold; }}
            QPushButton#roleBtn:disabled {{ color: {pal('sub')}; }}
            {theme.scrollbar_qss()}
        """)
        self.end_overlay.setStyleSheet(
            f'background: rgba(10,12,16,0.82); color: #FFFFFF; font-size: {theme.fs('display')}px; font-weight: bold;')

    def paintEvent(self, event):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, qpal('bg_top'))
        g.setColorAt(1, qpal('bg_bottom'))
        p.fillRect(self.rect(), g)
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.end_overlay.setGeometry(self.rect())

    def _apply_title(self):
        room = self.state.room_title(self.room_id) if self.room_id else ''
        label = MODE_LABELS.get(self.mode, '통화')
        self.setWindowTitle(f'{room or "통화"} · {label} - KCS Talk')
        self.title_label.setText(room or label)

    # ------------------------------------------------------------ 상태
    def participants(self):
        return dict(self.call.get('participants') or {})

    def my_info(self):
        return self.participants().get(self.state.me) or {}

    def can_speak(self):
        if self.mode != 'voiceroom':
            return True
        return self.my_role in ('host', 'speaker')

    def is_host(self):
        return self.my_role == 'host'

    def show_toast(self, text):
        self.toasts.append(text)
        if self.closed:
            return
        try:
            self.toast.show_message(text)
        except RuntimeError:
            pass

    def _ordered(self, names):
        parts = self.participants()
        host = self.call.get('host')
        return sorted(names, key=lambda u: (u == self.state.me, u != host,
                                            parts.get(u, {}).get('role') != 'host',
                                            self.state.display_name(u)))

    def _on_connection_lost(self, *_):
        """서버 연결이 끊기면 통화도 이어지지 않는다 - 통화 중 화면으로 남지 않게 끝낸다"""
        if self.closed or self.ended:
            return
        self._left = True       # 서버는 끊긴 사람을 통화에서 이미 뺐다 - 다시 연결된 뒤 나가기를 또 보내지 않는다
        self._finish('서버 연결이 끊어져 통화가 종료되었습니다')

    def _on_calls_changed(self):
        if self.closed or self.ended:
            return
        call = self.state.calls.get(self.call_id)
        if call is None:
            if self._was_participant:
                # call_ended 가 바로 뒤따라오면 그쪽에서 종료 문구를 띄운다
                QTimer.singleShot(0, self._check_gone)
            return
        self.call = dict(call)
        if self.state.me in self.participants():
            self._was_participant = True
        elif self._was_participant:
            self._finish('통화에서 나왔습니다')
            return
        self._refresh()

    def _check_gone(self):
        if not self.closed and not self.ended and self.call_id not in self.state.calls:
            self._finish('통화가 종료되었습니다')

    def _refresh(self):
        parts = self.participants()
        me = parts.get(self.state.me)
        if me is not None:
            self._was_participant = True
            prev_role = self.my_role
            self.my_role = me.get('role', 'speaker')
            self.my_hand = bool(me.get('hand'))
            if not self._initialized_me:
                self._initialized_me = True
                self.muted = bool(me.get('muted'))
                self.camera_on = bool(me.get('video')) and self.mode == 'video'
                self.screen_on = bool(me.get('screen'))
            if self.mode == 'voiceroom' and prev_role != self.my_role:
                if self.my_role == 'listener':
                    self.audio.stop_input()
                    self.show_toast('리스너로 변경되었습니다')
                elif prev_role == 'listener':
                    self.show_toast('스피커로 지정되었습니다' if self.my_role == 'speaker' else '공동 진행자가 되었습니다')
                    if self._devices_started:
                        self.audio.start_input()
        # 화면 공유가 꺼진 사람의 화면 제거
        for user in list(self._screens):
            if not parts.get(user, {}).get('screen'):
                self._screens.pop(user, None)
        self._sync_buttons()
        if self.mode == 'voiceroom':
            self._layout_voiceroom()
        else:
            self._layout_grid()
        self._update_sub()

    _devices_started = False

    def _sync_buttons(self):
        self.btn_mute.setChecked(self.muted)
        self.btn_camera.setChecked(not self.camera_on)
        self.btn_screen.setChecked(self.screen_on)
        self.btn_blur.setChecked(self.blur_on)
        self.btn_speaker.setChecked(not self.speaker_on)
        self.btn_record.setChecked(self.recorder is not None)
        self.btn_hand.setChecked(self.my_hand)
        self.rec_badge.setVisible(self.recorder is not None)
        self.blur_badge.setVisible(self.blur_on and self.camera_on)
        if self.mode == 'voiceroom':
            listener = self.my_role == 'listener'
            self.btn_mute.setVisible(not listener)
            self.btn_hand.setVisible(listener)
        for b in (self.btn_camera, self.btn_mute, self.btn_speaker, self.btn_record, self.btn_screen,
                  self.btn_blur, self.btn_hand):
            b.update()

    def _tile(self, username, compact=False):
        tile = self._tiles.get(username)
        video_style = self.mode == 'video'
        if tile is None or tile.compact != compact:
            if tile is not None:
                tile.deleteLater()
            tile = ParticipantTile(self.state, username, video_style=video_style, compact=compact, parent=self)
            tile.setContextMenuPolicy(Qt.CustomContextMenu)
            tile.customContextMenuRequested.connect(lambda pos, u=username: self._tile_menu(u))
            self._tiles[username] = tile
        info = dict(self.participants().get(username) or {})
        if username == self.state.me:
            info['muted'] = self.muted
            info['video'] = self.camera_on
        tile.set_info(info, self.room_id)
        if username in self._frames:
            tile.image = self._frames[username]
        return tile

    def _clear_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(self.grid_host)
                w.hide()

    def _layout_grid(self):
        parts = self.participants()
        names = self._ordered(list(parts))
        for user in list(self._tiles):
            if user not in parts:
                self._tiles.pop(user).deleteLater()
                self._frames.pop(user, None)
                self.mixer.remove(user)
        self._clear_grid()
        n = len(names)
        if self.mode == 'video':
            cols = 1 if n <= 1 else 2 if n <= 4 else 3 if n <= 9 else 4
        else:
            cols = 1 if n <= 3 else 2 if n <= 6 else 3
        if self.mode == 'video' and self._screens_or_mine():
            cols = min(max(n, 1), 5)
        for i, user in enumerate(names):
            tile = self._tile(user)
            self.grid.addWidget(tile, i // cols, i % cols)
            tile.show()
        rows = max(1, math.ceil(n / cols)) if n else 1
        for c in range(6):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        for r in range(6):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        # 화면 공유 영역
        sharers = self._screens_or_mine()
        for user in list(self._screen_tiles):
            if user not in sharers:
                self._screen_tiles.pop(user).deleteLater()
        for user in sharers:
            st = self._screen_tiles.get(user)
            if st is None:
                st = ScreenTile(self.state, user, self.screen_area)
                self._screen_tiles[user] = st
                self.screen_layout.addWidget(st)
            st.set_image(self._screens.get(user))
            st.show()
        self.screen_area.setVisible(bool(sharers))
        self.body_layout.setStretchFactor(self.grid_host, 1 if sharers else 3)
        if n == 0:
            self.status_label.setText('연결 중...')
            self.status_label.show()
        elif n == 1 and self._was_participant:
            self.status_label.setText('상대방을 기다리는 중...')
            self.status_label.show()
        else:
            self.status_label.hide()

    def _screens_or_mine(self):
        parts = self.participants()
        sharers = [u for u in self._ordered(list(parts)) if parts[u].get('screen') and u != self.state.me]
        if self.screen_on:
            sharers.append(self.state.me)
        return sharers

    def _layout_voiceroom(self):
        parts = self.participants()
        stage = self._ordered([u for u, p in parts.items() if p.get('role') in ('host', 'speaker')])
        listeners = self._ordered([u for u, p in parts.items() if p.get('role') == 'listener'])
        for user in list(self._tiles):
            if user not in stage:
                self._tiles.pop(user).deleteLater()
        self._clear_grid()
        cols = 4 if len(stage) <= 8 else 5
        for i, user in enumerate(stage):
            tile = self._tile(user, compact=True)
            tile.setMinimumSize(80, 112)
            self.grid.addWidget(tile, i // cols, i % cols)
            tile.show()
        for c in range(6):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        self.stage_title.setText(f'무대 · {len(stage)}/{MAX_STAGE}')

        self.listener_list.clear()
        for user in listeners:
            name = self.state.member_name(self.room_id, user) + (' (나)' if user == self.state.me else '')
            if parts[user].get('hand'):
                name = '✋ ' + name
            item = QListWidgetItem(QIcon(user_avatar_pixmap(self.state, user, 40)), name)
            item.setData(Qt.UserRole, user)
            item.setTextAlignment(Qt.AlignHCenter)
            item.setToolTip(self.state.display_name(user))
            self.listener_list.addItem(item)
        self.listener_title.setText(f'리스너 {len(listeners)}명')

        # 진행자: 손 든 사람 + 무대 위 사람 관리
        while self.manage_layout.count():
            item = self.manage_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.role_buttons = {}
        show_manage = self.is_host()
        self.manage_title.setVisible(show_manage)
        self.manage_area.setVisible(show_manage)
        if show_manage:
            hands = [u for u in listeners if parts[u].get('hand')]
            others = [u for u in stage if u != self.state.me]
            stage_full = len(stage) >= MAX_STAGE
            for user in hands + others:
                self.manage_layout.addWidget(self._manage_row(user, parts[user], stage_full))
            if not hands and not others:
                empty = QLabel('손 든 참여자가 없습니다')
                empty.setObjectName('callStatus')
                empty.setAlignment(Qt.AlignCenter)
                self.manage_layout.addWidget(empty)
            self.manage_layout.addStretch(1)
            self.manage_title.setText(f'참여자 관리 · 손 든 사람 {len(hands)}명')
        self.status_label.setText('진행자가 스피커로 지정하면 말할 수 있어요' if self.my_role == 'listener' else '')
        self.status_label.setVisible(self.my_role == 'listener')

    def _manage_row(self, user, info, stage_full):
        row = QFrame()
        row.setObjectName('manageRow')
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(8)
        av = QLabel()
        av.setPixmap(user_avatar_pixmap(self.state, user, 30))
        av.setFixedSize(30, 30)
        lay.addWidget(av)
        role = info.get('role', 'listener')
        text = self.state.member_name(self.room_id, user)
        sub = ('✋ 손 들었음' if info.get('hand') else '') if role == 'listener' else ROLE_LABELS.get(role, '')
        name = QLabel(f'<b>{_html(text)}</b>  <span style="color:{pal("sub")}">{_html(sub)}</span>')
        name.setTextFormat(Qt.RichText)      # 넣는 글자는 _html 로 감쌌다
        lay.addWidget(name, 1)

        def add(role_name, label, primary=False, enabled=True):
            b = QPushButton(label)
            b.setObjectName('roleBtn')
            b.setCursor(Qt.PointingHandCursor)
            if primary:
                b.setProperty('primary', True)
            b.setEnabled(enabled)
            b.clicked.connect(lambda _=False, u=user, r=role_name: self.set_role(u, r))
            self.role_buttons[(user, role_name)] = b
            lay.addWidget(b)

        if role == 'listener':
            add('speaker', '스피커로 지정', primary=True, enabled=not stage_full)
        if role == 'speaker':
            add('listener', '리스너로')
        if role != 'host':
            add('host', '공동 진행자', enabled=role != 'listener' or not stage_full)
        else:
            add('speaker', '진행자 해제')
        return row

    def _tile_menu(self, username):
        if self.mode != 'voiceroom' or not self.is_host() or username == self.state.me:
            return
        role = self.participants().get(username, {}).get('role')
        menu = QMenu(self)
        if role == 'speaker':
            menu.addAction('리스너로', lambda: self.set_role(username, 'listener'))
            menu.addAction('공동 진행자', lambda: self.set_role(username, 'host'))
        elif role == 'host':
            menu.addAction('스피커로', lambda: self.set_role(username, 'speaker'))
        menu.exec_(self.cursor().pos())

    def _listener_menu(self, pos):
        item = self.listener_list.itemAt(pos)
        if item is None or not self.is_host():
            return
        user = item.data(Qt.UserRole)
        if user == self.state.me:
            return
        menu = QMenu(self)
        menu.addAction('스피커로 지정', lambda: self.set_role(user, 'speaker'))
        menu.addAction('공동 진행자', lambda: self.set_role(user, 'host'))
        menu.exec_(self.listener_list.viewport().mapToGlobal(pos))

    def _update_sub(self):
        label = MODE_LABELS.get(self.mode, '통화')
        started = parse_time(self.call.get('started_at')) or self._opened_at
        elapsed = (datetime.now() - started).total_seconds()
        n = len(self.participants())
        if self.mode == 'voiceroom':
            self.sub_label.setText(f'{label} · {n}명 참여 · {format_duration(elapsed)}')
            remain = timedelta(hours=VOICEROOM_HOURS) - (datetime.now() - started)
            secs = max(0, int(remain.total_seconds()))
            self.remain_label.setText(
                f'{VOICEROOM_HOURS}시간 후 자동 종료 · {secs // 3600}시간 {secs % 3600 // 60:02d}분 남음')
        else:
            extra = f' · {n}명' if n > 2 else ''
            self.sub_label.setText(f'{label}{extra} · {format_duration(elapsed)}')

    def _tick_second(self):
        if self.closed or self.ended:
            return
        self._update_sub()
        if self.recorder is not None:
            self.rec_badge.setText(f'녹음 중 {format_duration(self.recorder.duration)}')

    # ------------------------------------------------------------ 장치
    def _start_devices(self):
        self._devices_started = True
        self.audio.start_output()
        if self.can_speak():
            self.audio.start_input()
        if self.mode == 'video' and self.camera_on:
            if not self.camera.start():
                # 카메라가 없으면 상대방에게 영상 끔을 알림
                self.camera_on = False
                self.state.set_call_state(self.call_id, video=False)
                self._refresh()
        settings = getattr(self.state, 'settings', None)
        try:
            auto = bool(settings.get('call_auto_record')) if settings is not None else False
        except Exception:
            auto = False
        if auto and self.mode == 'voice':
            self.start_recording()

    def _stop_devices(self):
        for fn in (self.audio.stop, self.camera.stop, self._screen_timer.stop):
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------ 버튼 동작
    def toggle_mute(self):
        self.muted = not self.muted
        self.state.set_call_state(self.call_id, muted=self.muted)
        if self.muted:
            self._levels[self.state.me] = 0.0
        self._refresh()

    def toggle_speaker(self):
        self.speaker_on = not self.speaker_on
        self.audio.set_volume(1.0 if self.speaker_on else 0.0)
        self.show_toast('스피커를 켰습니다' if self.speaker_on else '스피커를 껐습니다')
        self._sync_buttons()

    def toggle_camera(self):
        self.camera_on = not self.camera_on
        self.state.set_call_state(self.call_id, video=self.camera_on)
        if self.camera_on:
            self.camera.start()
        else:
            self.camera.stop()
            self._frames.pop(self.state.me, None)
            tile = self._tiles.get(self.state.me)
            if tile is not None:
                tile.set_image(None)
        self._refresh()

    def toggle_screen(self):
        self.screen_on = not self.screen_on
        self.state.set_call_state(self.call_id, screen=self.screen_on)
        if self.screen_on:
            self._screen_error_shown = False
            self._screen_timer.start(SCREEN_INTERVAL_MS)
            QTimer.singleShot(0, self._capture_screen)
        else:
            self._screen_timer.stop()
        self._refresh()

    def toggle_blur(self):
        self.blur_on = not self.blur_on
        self.show_toast('배경 흐림을 켰습니다' if self.blur_on else '배경 흐림을 껐습니다')
        self._sync_buttons()

    def toggle_hand(self):
        raised = not self.my_hand
        self.my_hand = raised
        self.state.raise_hand(self.call_id, raised)
        self.show_toast('손을 들었습니다. 진행자가 스피커로 지정하면 말할 수 있어요' if raised else '손을 내렸습니다')
        self._sync_buttons()

    def set_role(self, username, role):
        self.state.set_call_role(self.call_id, username, role)

    def toggle_record(self):
        if self.recorder is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        if self.recorder is not None:
            return self.recorder.path
        room = self.state.room_title(self.room_id) or 'call'
        safe = re.sub(r'[\\/:*?"<>|\s]+', '_', room).strip('_') or 'call'
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(recordings_dir(), f'{safe}_{MODE_LABELS.get(self.mode, "통화")}_{stamp}.wav')
        try:
            self.recorder = WavRecorder(path)
        except Exception:
            self.recorder = None
            self.show_toast('녹음 파일을 만들 수 없습니다')
            return None
        self._rec_in.clear()
        self._rec_out.clear()
        self.show_toast('통화 녹음을 시작합니다')
        self._sync_buttons()
        return path

    def stop_recording(self, notify=True):
        rec = self.recorder
        if rec is None:
            return None
        self._flush_recorder()
        if self.recorder is None:            # 쓰다가 실패해 이미 멈춤 (알림도 이미 함)
            return None
        self.recorder = None
        try:
            path = rec.close()
        except OSError as e:
            self.show_toast(f'녹음을 저장하지 못했습니다: {e}')
            return None
        if notify:
            self.show_toast(f'녹음이 저장되었습니다 · {os.path.basename(path)}')
        if not self.closed:
            self._sync_buttons()
        return path

    def hang_up(self):
        if not self.ended and self.state.me in self.participants():
            self.state.leave_call(self.call_id)
        self._left = True
        self.close()

    # ------------------------------------------------------------ 미디어
    def _on_audio_frame(self, pcm):
        """마이크 40ms 프레임 → 전송 (음소거/리스너면 보내지 않음)"""
        if self.closed or self.ended:
            return
        if self.muted or not self.can_speak():
            return
        level = rms_level(pcm)
        self._levels[self.state.me] = max(self._levels.get(self.state.me, 0.0), level)
        tile = self._tiles.get(self.state.me)
        if tile is not None:
            tile.set_level(self._levels[self.state.me])
        if self.recorder is not None:
            self._rec_out.append(bytes(pcm))
        data, codec = encode_audio(pcm)
        self._seq['audio'] += 1
        self.state.send_media(self.call_id, 'audio', self._seq['audio'], data, codec=codec)

    def _on_camera_frame(self, image):
        if self.closed or self.ended or self.mode != 'video' or not self.camera_on:
            return
        frame = fit_frame(image)
        if frame.isNull():
            return
        if self.blur_on:
            frame = blur_image(frame)
        self._frames[self.state.me] = frame.mirrored(True, False)
        tile = self._tiles.get(self.state.me)
        if tile is not None:
            tile.set_image(self._frames[self.state.me])
        data = jpeg_encode(frame, VIDEO_QUALITY)
        if data:
            self._seq['video'] += 1
            self.state.send_media(self.call_id, 'video', self._seq['video'], data)

    def _capture_screen(self, image=None):
        if self.closed or self.ended or not self.screen_on:
            return
        img = image if image is not None else grab_screen()
        if img is None or img.isNull():
            if not self._screen_error_shown:
                self._screen_error_shown = True
                self.show_toast('화면을 캡처할 수 없습니다')
            return
        if img.width() > SCREEN_MAX_WIDTH:
            img = img.scaledToWidth(SCREEN_MAX_WIDTH, Qt.SmoothTransformation)
        data = jpeg_encode(img, SCREEN_QUALITY)
        if data:
            self._seq['screen'] += 1
            self.state.send_media(self.call_id, 'screen', self._seq['screen'], data)

    def _on_media(self, call_id, sender, kind, seq, data_b64, extra):
        if call_id != self.call_id or self.closed or self.ended or sender == self.state.me:
            return
        if kind == 'audio':
            pcm = decode_audio(data_b64, (extra or {}).get('codec', 'pcm'))
            if not pcm:
                return
            self.mixer.push(sender, seq, pcm)
            level = rms_level(pcm)
            self._levels[sender] = max(self._levels.get(sender, 0.0), level)
            tile = self._tiles.get(sender)
            if tile is not None:
                tile.set_level(self._levels[sender])
        elif kind == 'video':
            img = jpeg_decode(data_b64)
            if img.isNull():
                return
            self._frames[sender] = img
            tile = self._tiles.get(sender)
            if tile is not None:
                tile.set_image(img)
        elif kind == 'screen':
            img = jpeg_decode(data_b64)
            if img.isNull():
                return
            new = sender not in self._screens
            self._screens[sender] = img
            st = self._screen_tiles.get(sender)
            if st is not None:
                st.set_image(img)
            elif new and self.mode != 'voiceroom':
                self._layout_grid()

    def _play_tick(self):
        if self.closed:
            return
        played = self.audio.play_tick(self.mixer)
        if self.recorder is not None:
            self._rec_in.extend(played)
            self._flush_recorder()

    def _flush_recorder(self):
        rec = self.recorder
        if rec is None:
            return
        silence = b'\x00' * FRAME_BYTES
        try:
            while self._rec_in or self._rec_out:
                a = self._rec_in.popleft() if self._rec_in else silence
                b = self._rec_out.popleft() if self._rec_out else silence
                rec.write(mix_pcm([a, b]))
        except OSError as e:                 # 디스크 가득 참 · 빠진 USB 등 → 통화는 이어 가고 녹음만 멈춘다
            self._abort_recording(e)

    def _abort_recording(self, error):
        rec, self.recorder = self.recorder, None
        self._rec_in.clear()
        self._rec_out.clear()
        try:
            if rec is not None:
                rec.close()
        except OSError:
            pass
        self.show_toast(f'녹음을 저장하지 못해 멈췄습니다: {error}')
        if not self.closed:
            self._sync_buttons()

    def _decay_levels(self):
        for user in list(self._levels):
            self._levels[user] *= 0.55
            if self._levels[user] < 0.004:
                self._levels[user] = 0.0
            tile = self._tiles.get(user)
            if tile is not None:
                tile.set_level(self._levels[user])

    # ------------------------------------------------------------ 종료
    def _on_call_ended(self, payload):
        if self.closed or int(payload.get('call_id', -1)) != self.call_id:
            return
        if self.mode == 'voiceroom':
            text = '보이스룸이 종료되었습니다'
        else:
            text = f'통화 종료 · {format_duration(payload.get("duration_seconds", 0))}'
        self._finish(text)

    def _finish(self, text):
        if self.ended:
            return
        self.ended = True
        self._stop_devices()
        self.stop_recording(notify=False)
        if text:
            self.end_overlay.setText(text)
            self.end_overlay.setGeometry(self.rect())
            self.end_overlay.show()
            self.end_overlay.raise_()
            QTimer.singleShot(self.END_DELAY_MS, self.close)
        else:
            self.close()

    def closeEvent(self, event):
        if not self.closed:
            if not self.ended and not self._left and self.state.me in self.participants():
                self.state.leave_call(self.call_id)
            self.closed = True
            self._stop_devices()
            self.stop_recording(notify=False)
            for t in (self._clock, self._play_timer, self._decay, self._screen_timer):
                t.stop()
            for sig, slot in ((self.state.calls_changed, self._on_calls_changed),
                              (self.state.call_ended, self._on_call_ended),
                              (self.state.call_media, self._on_media),
                              (self.state.connection_lost, self._on_connection_lost)):
                try:
                    sig.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            if _WINDOWS.get(self.call_id) is self:
                _WINDOWS.pop(self.call_id, None)
        super().closeEvent(event)


def _html(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
