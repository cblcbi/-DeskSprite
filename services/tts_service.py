# -*- coding: utf-8 -*-
"""封装 DashScope 语音合成（TTS），支持 barge-in 打断"""

import base64
import logging
import threading

import dashscope

logger = logging.getLogger(__name__)


class TTSService:
    def __init__(self, config, audio_engine):
        self.config = config
        self.audio = audio_engine
        self.stop_event = threading.Event()

    def request_stop(self):
        """请求立即停止当前播放（barge-in）"""
        self.stop_event.set()

    def synthesize(self, text: str, marker_info=None, on_schedule=None) -> float:
        """合成语音并播放；返回实际播出秒数（失败/未播返回 0）。

        marker_info / on_schedule: 与 EdgeTTSService 接口一致——
        DashScope 没有时间戳，直接用估算停留回调（立即，无弹幕）。
        """
        self.stop_event.clear()

        if marker_info and on_schedule:
            on_schedule(marker_info["markers"], marker_info["fallback_holds"], 0)

        if not self.config.QWEN_API_KEY:
            logger.warning("⚠️ 未配置 DashScope 密钥（.env 的 QWEN_API_KEY），无法合成语音")
            logger.info("📢 (文字版): %s", text)
            return 0.0

        try:
            response = dashscope.MultiModalConversation.call(
                model=self.config.TTS_MODEL,
                api_key=self.config.QWEN_API_KEY,
                text=text,
                voice=self.config.TTS_VOICE,
                stream=True,
            )

            self.audio.reset_play_measure()
            audio_received = False
            interrupted = False
            for chunk in response:
                if self.stop_event.is_set():
                    interrupted = True
                    break
                if chunk and chunk.output and chunk.output.audio:
                    audio_obj = chunk.output.audio
                    if hasattr(audio_obj, "data"):
                        audio_b64 = audio_obj.data
                    elif hasattr(audio_obj, "get_data"):
                        audio_b64 = audio_obj.get_data()
                    else:
                        audio_b64 = str(audio_obj)

                    audio_data = base64.b64decode(audio_b64)
                    self.audio.play(audio_data)
                    audio_received = True
                    if self.stop_event.is_set():
                        interrupted = True
                        break

            duration = self.audio.played_seconds
            if interrupted:
                logger.info("⏹ 语音被打断（已播 %.1fs）", duration)
            elif audio_received:
                logger.info("✅ 播放完成 (%.1fs)", duration)
            else:
                logger.warning("⚠️ 未收到音频")
                logger.info("📢 (文字版): %s", text)
            return duration

        except Exception as e:
            logger.error("❌ 语音合成失败: %s", e, exc_info=True)
            logger.info("📢 (文字版): %s", text)
            return 0.0
