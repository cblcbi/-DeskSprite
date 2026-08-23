# -*- coding: utf-8 -*-
"""程序入口：初始化各模块并启动主循环

架构约定：
- GUI（Tk）运行在主线程：gui.run() 里跑 mainloop
- 录音 / LLM 请求 / TTS / 随机吐槽 / 键盘监听 都在后台线程
- 后台线程通过 queue 给 GUI 发指令，不直接碰 Tk
"""

import logging

from config import Config
from core.orchestrator import Orchestrator
from services.edge_tts_service import EdgeTTSService
from services.llm_client import LLMClient
from services.stt_service import STTService
from services.tts_service import TTSService
from ui.gui_manager import GUIManager
from utils.audio_utils import AudioEngine
from utils.hotkeys import display as hk_display
from utils.logger import setup_logging
from utils.screen_utils import ensure_dpi_aware

logger = logging.getLogger(__name__)


def main():
    setup_logging(level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO))
    ensure_dpi_aware()

    logger.info("=" * 50)
    logger.info("🫧 桌灵 DeskSprite — 住在你屏幕里的 AI 精灵")
    logger.info("=" * 50)
    logger.info("🧠 理解: %s (GGCLI 反代)", Config.GG_MODEL)
    logger.info("🔊 语音: %s (%s 后端)", Config.TTS_MODEL if Config.TTS_BACKEND == "dashscope" else Config.TTS_EDGE_VOICE, Config.TTS_BACKEND)
    logger.info("🎤 识别: Whisper (%s)", Config.WHISPER_DEVICE)
    logger.info("=" * 50)
    if not Config.GG_API_KEY:
        logger.warning("⚠️ 未检测到 API 密钥！请打开 .env 填入密钥")
    if Config.TTS_BACKEND == "dashscope" and not Config.QWEN_API_KEY:
        logger.warning("⚠️ 未检测到 DashScope 密钥！语音合成将不可用（可打字/文字版回复）")
    logger.info("💡 按住 %s 说话，松开发送", hk_display(Config.HOTKEY_PTT))
    logger.info("💡 按 %s 打字聊天（Enter 发送，Esc 收起）", hk_display(Config.HOTKEY_CHAT))
    logger.info("💡 按 %s 打开设置", hk_display(Config.HOTKEY_SETTINGS))
    logger.info("💡 AI 会随机观察屏幕并评论")
    logger.info("%s", "=" * 50)

    # 底层服务（主线程创建，后台线程使用）
    audio = AudioEngine(Config)
    stt = STTService(Config)
    if Config.TTS_BACKEND == "edge":
        tts = EdgeTTSService(Config, audio)
    else:
        tts = TTSService(Config, audio)
    llm = LLMClient(Config)

    # GUI 主线程
    gui = GUIManager()

    # 核心控制
    orchestrator = Orchestrator(Config, llm, stt, tts, audio, gui)
    gui.set_text_handler(orchestrator.submit_text)
    gui.set_settings_saved_handler(
        lambda: logger.warning("⚠️ 设置已保存，部分项需重启程序生效")
    )

    # 后台线程
    orchestrator.start_keyboard_listener()
    orchestrator.start_roast_loop()
    orchestrator.start_worker()

    # 主线程进入 GUI 事件循环
    try:
        gui.run()
    except KeyboardInterrupt:
        logger.info("👋 再见！")
    finally:
        audio.cleanup()


if __name__ == "__main__":
    main()
