# -*- coding: utf-8 -*-
"""封装 Whisper 语音识别（STT）"""

import logging
import os
import tempfile
import time
import wave

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class STTService:
    def __init__(self, config):
        self.config = config
        logger.info("⏳ 加载语音识别模型 (%s)...", config.WHISPER_DEVICE)
        self.model = WhisperModel(
            config.WHISPER_MODEL_PATH,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
        logger.info("✅ 语音识别模型已加载")

        # 过滤 whisper 幻觉文本（常见字幕残留）
        self.hallucination_filters = [
            "字幕by索兰娅",
            "字幕By索兰娅",
            "索兰娅",
            "subtitle by",
            "字幕:",
            "翻译:",
            "校对:",
            "时间轴:",
            "制作:",
        ]

    def transcribe(self, audio_data: bytes) -> str:
        """把 PCM16 音频转写为文本；过短/无内容返回空串"""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(self.config.CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(self.config.RATE)
                wf.writeframes(audio_data)

            segments, _ = self.model.transcribe(
                tmp_path,
                language="zh",
                condition_on_previous_text=False,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )

            text = "".join(seg.text for seg in segments).strip()
            for hallucination in self.hallucination_filters:
                text = text.replace(hallucination, "")
            text = text.strip()

            if len(text) < self.config.MIN_TEXT_LENGTH:
                return ""
            return text

        finally:
            time.sleep(0.05)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
