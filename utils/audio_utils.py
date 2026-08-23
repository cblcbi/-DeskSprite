# -*- coding: utf-8 -*-
"""麦克风录音、音频播放底层逻辑"""

import logging

import numpy as np
import pyaudio

logger = logging.getLogger(__name__)


class AudioEngine:
    """持有 PyAudio 实例：负责播放与录音（播放侧可测量实际播出时长）"""

    def __init__(self, config):
        self.config = config
        self.p = pyaudio.PyAudio()
        self.stream_out = self.p.open(
            format=pyaudio.paInt16,
            channels=config.CHANNELS,
            rate=config.RATE,
            output=True,
        )
        self._played_bytes = 0

    def play(self, audio_bytes: bytes):
        """播放 PCM16 音频数据（阻塞至该段播完），并累计播出字节数"""
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
        self.stream_out.write(audio_np.tobytes())
        self._played_bytes += len(audio_bytes)

    def reset_play_measure(self):
        """重置播出计量（每次 TTS 合成前调用）"""
        self._played_bytes = 0

    @property
    def played_seconds(self) -> float:
        """实际已播出的音频秒数（PCM16 = 字节数 / 采样率 / 声道数 / 2）"""
        return self._played_bytes / (self.config.RATE * self.config.CHANNELS * 2)

    def record(self, should_stop) -> bytes:
        """录音直到 should_stop() 返回 True，返回 PCM16 数据"""
        stream_in = self.p.open(
            format=pyaudio.paInt16,
            channels=self.config.CHANNELS,
            rate=self.config.RATE,
            input=True,
            frames_per_buffer=self.config.CHUNK,
        )
        logger.info("🎤 录音中...")
        frames = []
        while not should_stop():
            data = stream_in.read(self.config.CHUNK, exception_on_overflow=False)
            frames.append(data)
        stream_in.stop_stream()
        stream_in.close()
        logger.info("🛑 停止录音")
        return b"".join(frames)

    def compute_energy(self, audio_data: bytes) -> float:
        """计算音频平均能量，用于判断是否真的说了话"""
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        return float(np.abs(audio_np).mean())

    def cleanup(self):
        self.stream_out.stop_stream()
        self.stream_out.close()
        self.p.terminate()
