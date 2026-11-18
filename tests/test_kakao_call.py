# -*- coding: utf-8 -*-
"""통화(보이스톡·페이스톡·보이스룸) 클라이언트 테스트 - 서버 없이 가짜 연결로 구동"""

import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.kakao_fakes import make_demo_state, app  # noqa: E402

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QImage, QColor, QPainter  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402

from client.kakao import call as callmod  # noqa: E402
from client.kakao.call import (encode_audio, decode_audio, mix_pcm, rms_level, jpeg_encode, jpeg_decode,  # noqa: E402
                               blur_image, WavRecorder, JitterMixer, show_call, CallWindow, FRAME_BYTES,
                               resample_pcm, format_duration)


def tone(freq=440, amp=8000, samples=640, rate=16000):
    return struct.pack('<%dh' % samples, *[int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(samples)])


def const_pcm(value, samples=640):
    return struct.pack('<%dh' % samples, *([value] * samples))


def fake_frame(color='#3366CC', w=320, h=240):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(color))
    p = QPainter(img)
    p.setBrush(QColor('#F2C9A0'))
    p.drawEllipse(w // 2 - 40, h // 2 - 60, 80, 100)
    p.end()
    return img


def call_payload(call_id, mode, participants, host='홍길동', room_id='r1'):
    parts = {}
    for user, info in participants.items():
        base = {'role': 'speaker', 'muted': False, 'video': mode == 'video', 'screen': False, 'hand': False}
        base.update(info)
        parts[user] = base
    return {'call_id': call_id, 'room_id': room_id, 'mode': mode, 'host': host,
            'started_at': datetime.now().isoformat(timespec='seconds'), 'participants': parts}


class CodecTests(unittest.TestCase):
    def test_audio_roundtrip_zpcm(self):
        pcm = tone()
        data, codec = encode_audio(pcm)
        self.assertEqual(codec, 'zpcm')
        self.assertEqual(decode_audio(data, codec), pcm)
        silent = b'\x00' * FRAME_BYTES
        data, codec = encode_audio(silent)
        self.assertLess(len(data), len(silent))
        self.assertEqual(decode_audio(data, codec), silent)

    def test_audio_roundtrip_pcm_and_garbage(self):
        noise = os.urandom(FRAME_BYTES)
        data, codec = encode_audio(noise)
        self.assertEqual(codec, 'pcm')          # 압축 안 되는 데이터는 원본 그대로
        self.assertEqual(decode_audio(data, codec), noise)
        self.assertEqual(decode_audio('!!not base64', 'zpcm'), b'')
        self.assertEqual(decode_audio('AAAA', 'zpcm'), b'')

    def test_mix_clips(self):
        a = const_pcm(30000, 4)
        b = const_pcm(10000, 4)
        mixed = struct.unpack('<4h', mix_pcm([a, b]))
        self.assertEqual(mixed, (32767,) * 4)
        neg = struct.unpack('<4h', mix_pcm([const_pcm(-30000, 4), const_pcm(-30000, 4)]))
        self.assertEqual(neg, (-32768,) * 4)
        # 길이가 다르면 짧은 쪽은 무음으로 채움
        mixed = struct.unpack('<4h', mix_pcm([const_pcm(100, 4), const_pcm(50, 2)]))
        self.assertEqual(mixed, (150, 150, 100, 100))
        self.assertEqual(mix_pcm([]), b'')

    def test_rms(self):
        self.assertEqual(rms_level(b''), 0.0)
        self.assertEqual(rms_level(const_pcm(0)), 0.0)
        self.assertAlmostEqual(rms_level(const_pcm(16384)), 0.5, places=3)
        self.assertAlmostEqual(rms_level(tone(amp=16384)), 0.5 / math.sqrt(2), places=2)

    def test_resample(self):
        pcm = tone(samples=480, rate=48000)
        out = resample_pcm(pcm, 48000, 16000)
        self.assertEqual(len(out), 160 * 2)

    def test_wav_recorder_header(self):
        path = os.path.join(tempfile.mkdtemp(), 'sub', 'rec.wav')
        rec = WavRecorder(path)
        rec.write(tone())
        rec.write(tone())
        rec.close()
        with open(path, 'rb') as f:
            head = f.read(44)
        self.assertEqual(head[:4], b'RIFF')
        self.assertEqual(head[8:16], b'WAVEfmt ')
        with wave.open(path, 'rb') as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnframes(), 1280)

    def test_jpeg_roundtrip_and_blur(self):
        app()
        img = fake_frame()
        data = jpeg_encode(img, 60)
        self.assertTrue(data)
        back = jpeg_decode(data)
        self.assertFalse(back.isNull())
        self.assertEqual((back.width(), back.height()), (320, 240))
        self.assertTrue(jpeg_decode('garbage').isNull())
        blurred = blur_image(img)
        self.assertEqual(blurred.size(), img.size())
        # 모서리(배경)는 원본과 같은 단색이므로 흐려도 같은 색, 하지만 경계가 부드러워진다
        sharp_edge = QColor(img.pixel(160, 240 // 2 - 61)).red()
        self.assertFalse(blurred.isNull())
        self.assertIsInstance(sharp_edge, int)

    def test_jitter_mixer(self):
        m = JitterMixer(prebuffer=3)
        m.push('a', 1, const_pcm(100))
        m.push('a', 2, const_pcm(100))
        self.assertIsNone(m.pop_frame())        # 120ms 쌓이기 전에는 재생 안 함
        m.push('a', 3, const_pcm(100))
        self.assertFalse(m.push('a', 3, const_pcm(100)))   # 중복 seq 버림
        m.push('b', 1, const_pcm(200))
        m.push('b', 2, const_pcm(200))
        m.push('b', 3, const_pcm(200))
        frame = m.pop_frame()
        self.assertEqual(struct.unpack('<h', frame[:2])[0], 300)
        self.assertEqual(format_duration(83), '1:23')


class CallWindowBase(unittest.TestCase):
    def setUp(self):
        self.state, self.conn = make_demo_state()
        callmod._WINDOWS.clear()
        self.windows = []

    def tearDown(self):
        for w in list(callmod._WINDOWS.values()) + self.windows:
            try:
                if not w.closed:
                    w.close()
            except RuntimeError:
                pass
        callmod._WINDOWS.clear()
        QTest.qWait(1)

    def push_call(self, call_id, mode, participants, **kw):
        payload = call_payload(call_id, mode, participants, **kw)
        self.conn.push('call_updated', call=payload)
        return payload

    def open(self, call_id):
        w = show_call(self.state, call_id)
        self.windows.append(w)
        self.assertFalse(w._devices_started)       # 장치는 창을 먼저 그린 뒤에 연다 (DEVICE_START_DELAY_MS)
        w._start_devices_once()                    # 여기서는 바로 열어 예전과 같은 상태에서 시험한다
        self.conn.sent.clear()
        return w

    def sent(self, kind):
        return [p for t, p in self.conn.sent if t == kind]


class CallWindowTests(CallWindowBase):
    def test_show_call_reuses_window_and_title(self):
        self.push_call(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}})
        w1 = self.open(1)
        w2 = show_call(self.state, 1)
        self.assertIs(w1, w2)
        self.assertEqual(w1.windowTitle(), '프로젝트 팀 · 보이스톡 - KCS Talk')
        self.push_call(2, 'video', {'홍길동': {'role': 'host'}}, room_id='r2')
        w3 = self.open(2)
        self.assertIsNot(w1, w3)
        self.assertIn('페이스톡', w3.windowTitle())

    def test_voice_layout_and_speaking(self):
        self.push_call(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}, '이영희': {'muted': True}})
        w = self.open(1)
        self.assertEqual(set(w._tiles), {'홍길동', '김철수', '이영희'})
        self.assertTrue(w._tiles['이영희'].muted)
        for b in (w.btn_mute, w.btn_speaker, w.btn_record, w.btn_end):
            self.assertFalse(b.isHidden())
        self.assertTrue(w.btn_camera.isHidden())
        data, codec = encode_audio(tone(amp=12000))
        self.conn.push('call_media', call_id=1, **{'from': '김철수'}, kind='audio', seq=1, data_b64=data, codec=codec)
        self.assertTrue(w._tiles['김철수'].speaking)
        self.assertFalse(w._tiles['홍길동'].speaking)
        self.assertEqual(w.mixer.pending_frames('김철수'), 1)
        w.grab()   # 그리기 오류가 없어야 함

    def test_mute_stops_audio(self):
        self.push_call(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}})
        w = self.open(1)
        w._on_audio_frame(tone())
        audio = self.sent('call_media')
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]['kind'], 'audio')
        self.assertEqual(audio[0]['codec'], 'zpcm')
        self.assertEqual(decode_audio(audio[0]['data_b64'], 'zpcm'), tone())
        w.btn_mute.click()
        self.assertEqual(self.sent('call_state')[-1], {'call_id': 1, 'muted': True})
        self.conn.sent.clear()
        w._on_audio_frame(tone())
        self.assertEqual(self.sent('call_media'), [])
        w.btn_mute.click()
        self.assertEqual(self.sent('call_state')[-1], {'call_id': 1, 'muted': False})
        w._on_audio_frame(tone())
        self.assertEqual(len(self.sent('call_media')), 1)

    def test_recording_writes_wav(self):
        self.push_call(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}})
        w = self.open(1)
        w.btn_record.click()
        self.assertIsNotNone(w.recorder)
        self.assertFalse(w.rec_badge.isHidden())
        path = w.recorder.path
        self.assertTrue(path.startswith(callmod.recordings_dir()))
        w._on_audio_frame(tone())
        w._rec_in.append(tone(220))
        w._flush_recorder()
        w.btn_record.click()
        self.assertIsNone(w.recorder)
        self.assertTrue(w.rec_badge.isHidden())
        with wave.open(path, 'rb') as f:
            self.assertEqual(f.getframerate(), 16000)
            self.assertEqual(f.getnframes(), 640)

    def test_video_layout_and_incoming_frame(self):
        people = {'홍길동': {'role': 'host'}, '김철수': {}, '이영희': {}, '박민수': {'video': False}}
        self.push_call(5, 'video', people)
        w = self.open(5)
        self.assertEqual(len(w._tiles), 4)
        for b in (w.btn_camera, w.btn_mute, w.btn_screen, w.btn_blur, w.btn_end):
            self.assertFalse(b.isHidden())
        frame = jpeg_encode(fake_frame('#CC3333'), 60)
        self.conn.push('call_media', call_id=5, **{'from': '이영희'}, kind='video', seq=1, data_b64=frame)
        self.assertTrue(w._tiles['이영희'].showing_video())
        self.assertFalse(w._tiles['김철수'].showing_video())
        self.assertFalse(w._tiles['박민수'].showing_video())
        # 다른 통화의 프레임은 무시
        self.conn.push('call_media', call_id=99, **{'from': '김철수'}, kind='video', seq=1, data_b64=frame)
        self.assertFalse(w._tiles['김철수'].showing_video())
        w.grab()

    def test_camera_toggle_and_blur(self):
        self.push_call(5, 'video', {'홍길동': {'role': 'host'}, '김철수': {}})
        w = self.open(5)
        # 테스트 환경에는 카메라가 없으므로 시작 시 영상 끔 상태
        self.assertFalse(w.camera_on)
        w.btn_camera.click()
        self.assertEqual(self.sent('call_state')[-1], {'call_id': 5, 'video': True})
        w._on_camera_frame(fake_frame())
        media = self.sent('call_media')
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]['kind'], 'video')
        self.assertEqual((jpeg_decode(media[0]['data_b64']).width(), jpeg_decode(media[0]['data_b64']).height()), (320, 240))
        self.assertTrue(w._tiles['홍길동'].showing_video())
        w.btn_blur.click()
        self.assertTrue(w.blur_on)
        w._on_camera_frame(fake_frame())
        self.assertEqual(len(self.sent('call_media')), 2)
        self.conn.sent.clear()
        w.btn_camera.click()
        self.assertEqual(self.sent('call_state')[-1], {'call_id': 5, 'video': False})
        w._on_camera_frame(fake_frame())
        self.assertEqual(self.sent('call_media'), [])
        self.assertFalse(w._tiles['홍길동'].showing_video())

    def test_screen_share_toggle(self):
        self.push_call(5, 'video', {'홍길동': {'role': 'host'}, '김철수': {}})
        w = self.open(5)
        w.btn_screen.click()
        self.assertEqual(self.sent('call_state')[-1], {'call_id': 5, 'screen': True})
        big = QImage(2560, 1440, QImage.Format_RGB32)
        big.fill(QColor('#203040'))
        w._capture_screen(big)
        media = [m for m in self.sent('call_media') if m['kind'] == 'screen']
        self.assertEqual(len(media), 1)
        self.assertEqual(jpeg_decode(media[0]['data_b64']).width(), 1280)
        w.btn_screen.click()
        self.assertEqual(self.sent('call_state')[-1], {'call_id': 5, 'screen': False})
        self.conn.sent.clear()
        w._capture_screen(big)
        self.assertEqual(self.sent('call_media'), [])

    def test_incoming_screen_share_shows_large_tile(self):
        self.push_call(5, 'video', {'홍길동': {'role': 'host'}, '김철수': {'screen': True}})
        w = self.open(5)
        shot = QImage(1280, 720, QImage.Format_RGB32)
        shot.fill(QColor('#FFFFFF'))
        self.conn.push('call_media', call_id=5, **{'from': '김철수'}, kind='screen', seq=1, data_b64=jpeg_encode(shot, 50))
        self.assertIn('김철수', w._screen_tiles)
        self.assertFalse(w.screen_area.isHidden())
        self.push_call(5, 'video', {'홍길동': {'role': 'host'}, '김철수': {'screen': False}})
        self.assertNotIn('김철수', w._screen_tiles)

    def test_voiceroom_listener_hand_raise(self):
        self.push_call(7, 'voiceroom', {'김철수': {'role': 'host'}, '홍길동': {'role': 'listener'},
                                        '이영희': {'role': 'listener'}}, host='김철수')
        w = self.open(7)
        self.assertFalse(w.btn_hand.isHidden())
        self.assertTrue(w.btn_mute.isHidden())
        self.assertEqual(w.listener_list.count(), 2)
        self.assertIn('48시간 후 자동 종료', w.remain_label.text())
        self.assertTrue(w.manage_area.isHidden())
        w._on_audio_frame(tone())
        self.assertEqual(self.sent('call_media'), [])      # 리스너는 오디오를 보내지 않음
        w.btn_hand.click()
        self.assertEqual(self.sent('call_hand')[-1], {'call_id': 7, 'raised': True})
        # 스피커로 지정되면 음소거 버튼이 나타나고 오디오 전송
        self.push_call(7, 'voiceroom', {'김철수': {'role': 'host'}, '홍길동': {'role': 'speaker'},
                                        '이영희': {'role': 'listener'}}, host='김철수')
        self.assertFalse(w.btn_mute.isHidden())
        self.assertTrue(w.btn_hand.isHidden())
        w._on_audio_frame(tone())
        self.assertEqual(len(self.sent('call_media')), 1)

    def test_voiceroom_host_role_buttons(self):
        self.push_call(7, 'voiceroom', {'홍길동': {'role': 'host'}, '김철수': {'role': 'listener', 'hand': True},
                                        '이영희': {'role': 'speaker'}, '박민수': {'role': 'listener'}})
        w = self.open(7)
        self.assertFalse(w.manage_area.isHidden())
        self.assertIn(('김철수', 'speaker'), w.role_buttons)
        self.assertIn(('이영희', 'listener'), w.role_buttons)
        self.assertNotIn(('박민수', 'speaker'), w.role_buttons)   # 손 안 든 리스너는 목록에 없음
        w.role_buttons[('김철수', 'speaker')].click()
        self.assertEqual(self.sent('call_role')[-1], {'call_id': 7, 'username': '김철수', 'role': 'speaker'})
        w.role_buttons[('이영희', 'listener')].click()
        self.assertEqual(self.sent('call_role')[-1], {'call_id': 7, 'username': '이영희', 'role': 'listener'})
        w.role_buttons[('이영희', 'host')].click()
        self.assertEqual(self.sent('call_role')[-1], {'call_id': 7, 'username': '이영희', 'role': 'host'})
        self.assertEqual(len(w._tiles), 2)                         # 무대: 홍길동 + 이영희
        w.grab()

    def test_closes_on_call_ended(self):
        CallWindow.END_DELAY_MS = 0
        try:
            self.push_call(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}})
            w = self.open(1)
            self.conn.push('call_ended', call_id=1, room_id='r1', duration_seconds=83)
            self.assertTrue(w.ended)
            self.assertEqual(w.end_overlay.text(), '통화 종료 · 1:23')
            QTest.qWait(20)
            self.assertTrue(w.closed)
            self.assertNotIn(1, callmod._WINDOWS)
            self.assertEqual(self.sent('call_leave'), [])         # 이미 끝난 통화는 나가기 요청 안 함
        finally:
            CallWindow.END_DELAY_MS = 1800

    def test_closes_when_removed_and_hangup_leaves(self):
        CallWindow.END_DELAY_MS = 0
        try:
            self.push_call(1, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}})
            w = self.open(1)
            self.push_call(1, 'voice', {'김철수': {'role': 'host'}})
            QTest.qWait(20)
            self.assertTrue(w.closed)
            self.push_call(2, 'voice', {'홍길동': {'role': 'host'}, '김철수': {}})
            w2 = self.open(2)
            w2.btn_end.click()
            self.assertEqual(self.sent('call_leave'), [{'call_id': 2}])
            self.assertTrue(w2.closed)
        finally:
            CallWindow.END_DELAY_MS = 1800


if __name__ == '__main__':
    unittest.main()
