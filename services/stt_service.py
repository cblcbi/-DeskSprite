# -*- coding: utf-8 -*-
"""封装 Whisper 语音识别（STT）"""

import glob
import logging
import os
import tempfile
import time
import wave

from faster_whisper import WhisperModel

from utils.paths import BASE_DIR

logger = logging.getLogger(__name__)

# 常见官方模型名（这些值交给 faster-whisper 处理，本地不存在时联网下载）
_MODEL_NAMES = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "turbo", "distil-large-v2", "distil-large-v3",
}


def _is_model_name(path: str) -> bool:
    """纯模型名（无路径分隔符且不在磁盘上）→ 交给 faster-whisper 联网下载"""
    if not path or ("/" in path) or ("\\" in path):
        return False
    if os.path.isdir(path):
        return False
    return path.lower() in _MODEL_NAMES


def _is_local_model_dir(path: str) -> bool:
    """本地模型目录：存在且含 model.bin"""
    return bool(path) and os.path.isdir(path) and os.path.isfile(os.path.join(path, "model.bin"))


def _discover_local_models():
    """自动发现应用目录 models/ 下的本地模型（model.bin 大的优先=更精准）"""
    found = glob.glob(os.path.join(BASE_DIR, "models", "*", "model.bin"))
    found.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return [os.path.dirname(p) for p in found]


class STTService:
    def __init__(self, config):
        self.config = config
        self.model = None

        # 模型来源：配置值有效直接用；无效则自动发现应用目录 models/ 下的模型
        path = config.WHISPER_MODEL_PATH
        if _is_local_model_dir(path):
            logger.info("📁 使用配置的本地模型: %s", path)
        elif _is_model_name(path):
            logger.info("📁 使用模型名: %s（本地不存在时自动下载）", path)
        else:
            discovered = _discover_local_models()
            if discovered:
                path = discovered[0]
                logger.info("🔍 配置的模型无效，自动发现本地模型: %s", path)
            else:
                logger.warning("⚠️ 配置的模型路径无效且 models/ 下未发现模型，尝试按模型名下载: %s",
                               path or "small")
                path = path if _is_model_name(path) else "small"

        # 加载尝试链：优先用户配置的设备；CUDA 不可用（无 N 卡/驱动问题）自动降级 CPU
        attempts = [(config.WHISPER_DEVICE, config.WHISPER_COMPUTE)]
        if str(config.WHISPER_DEVICE).lower() != "cpu":
            attempts.append(("cpu", "int8"))
        for device, compute in attempts:
            try:
                logger.info("⏳ 加载语音识别模型 (%s @ %s/%s)...", path, device, compute)
                self.model = WhisperModel(
                    path,
                    device=device,
                    compute_type=compute,
                )
                logger.info("✅ 语音识别模型已加载 (%s/%s)", device, compute)
                break
            except Exception as e:
                logger.warning("⚠️ %s/%s 加载失败: %s", device, compute, e)
                self.model = None
        if self.model is None:
            # 模型缺失/网络下载失败时不崩溃：语音转写降级不可用，程序照常运行
            logger.error(
                "❌ 语音识别模型加载失败（语音对话将不可用，打字聊天不受影响）\n"
                "   请检查 WHISPER_MODEL_PATH（本地模型目录）或网络后重启。"
            )

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
        """把 PCM16 音频转写为文本；模型不可用时返回空串"""
        if self.model is None:
            logger.warning("⚠️ 语音识别模型不可用，跳过转写")
            return ""
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
