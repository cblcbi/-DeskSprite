# -*- coding: utf-8 -*-
"""核心控制逻辑：把语音、截图、LLM、TTS、UI 串联起来"""

import logging
import random
import threading
import time
from typing import Dict, List, Any, Optional

from pynput import keyboard

from config import get_system_prompt
from core.state import State
from ui.screen_marker import parse_markers
from utils import persistence
from utils.hotkeys import Hotkey, foreground_is_ours, key_name, MODIFIER_NAMES
from utils.screen_utils import capture_screen

logger = logging.getLogger(__name__)

# 对话队列项：(user_content, is_roast, history_text)
# history_text 是存进对话历史的干净用户原话（不带"请结合屏幕截图回答"模板）


class Orchestrator:
    """控制流中枢：持有全部依赖，串联语音/截图/LLM/TTS/UI"""

    def __init__(self, config, llm, stt, tts, audio, gui, state=None):
        self.config = config
        self.llm = llm
        self.stt = stt
        self.tts = tts
        self.audio = audio
        self.gui = gui
        self.state = state if state is not None else State()
        self._record_start_time = 0.0

        # 可配置快捷键（设置窗口「通用」页录制，空串=禁用）
        self._hk = {
            "ptt": Hotkey(self.config.HOTKEY_PTT),          # 按住说话
            "chat": Hotkey(self.config.HOTKEY_CHAT),        # 聊天输入框
            "settings": Hotkey(self.config.HOTKEY_SETTINGS),  # 设置窗口
            "interrupt": Hotkey(self.config.HOTKEY_INTERRUPT),  # 打断语音
            "region": Hotkey(self.config.HOTKEY_REGION),    # 框选截图（待实现）
        }
        self._held_mods = set()  # 当前按住的修饰键（规范名）
        self._ptt_armed = False  # PTT 触发中，等待松开

        # 从磁盘恢复对话历史
        self.state.conversation_history = persistence.load_history()

    # ========================
    # 对话主流程
    # ========================

    def handle_conversation(self, user_content, is_roast=False, history_text=None):
        """处理完整的对话流程（在 worker 线程中串行执行）"""
        self.state.is_ai_speaking = True

        try:
            with self.state.lock:
                messages = [{"role": "system", "content": get_system_prompt()}]
                messages.extend(self.state.conversation_history[-(self.config.MAX_HISTORY_TURNS * 2):])
                messages.append({"role": "user", "content": user_content})

            # 1️⃣ LLM 生成文本
            logger.info("🤔 思考中...")
            response_text = self.llm.get_response(messages)
            logger.info("💬 AI: %s", response_text or "（空响应）")
            # 等待结束：指示恢复默认（接下来由说话/圈圈接管）
            self.gui.set_marker_state("idle")

            # 🎯 解析屏幕标记：拿到纯文本、字符偏移（对齐语音时间戳）
            marker_info = None
            if response_text:
                response_text, markers, offsets, fallback_holds = parse_markers(response_text)
                if markers:
                    logger.info("🎯 在屏幕上标记了 %d 处: %s", len(markers), markers)
                    marker_info = {
                        "markers": markers,
                        "offsets": offsets,
                        "fallback_holds": fallback_holds,
                    }
            else:
                response_text = ""

            # 2️⃣ TTS 合成语音（可被 barge-in 打断；edge 后端会回调精确标记时序）
            if response_text and not response_text.startswith("抱歉"):
                logger.info("🔊 合成语音...")
                self.tts.synthesize(
                    response_text,
                    marker_info=marker_info,
                    on_schedule=self._show_markers,
                )
            elif marker_info:
                # 没有语音（空/抱歉），直接按估算显示标记
                self.gui.show_markers(
                    marker_info["markers"], marker_info["fallback_holds"]
                )

            # 3️⃣ 更新历史（只存干净的用户原话，不存模板串）+ 持久化
            with self.state.lock:
                if history_text is None:
                    history_text = "（随机观察）" if is_roast else "（语音+截图）"
                self.state.conversation_history.append({
                    "role": "user",
                    "content": history_text,
                })
                self.state.conversation_history.append({
                    "role": "assistant",
                    "content": response_text,
                })
            persistence.save_history(self.state.conversation_history)

        except Exception as e:
            logger.error("❌ 处理失败: %s", e, exc_info=True)

        finally:
            # 无论 TTS 是否成功，都让标记收手（避免挂死）
            self.gui.release_markers()
            self.gui.set_marker_state("idle")
            self.state.is_ai_speaking = False

    # ========================
    # 消息入口
    # ========================

    def _show_markers(self, markers, holds, start_delay_ms=0, words=None):
        """TTS 拿到精确时间戳后回调：把标记调度 + 弹幕发给 GUI"""
        self.gui.show_markers(markers, holds, start_delay_ms, words=words)

    def submit_text(self, text: str):
        """文本消息（带截图）"""
        img_b64 = capture_screen()
        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": f"用户说：「{text}」\n请结合屏幕截图回答。"},
        ]
        logger.info("💬 你说: %s", text)
        self.state.pending.put((user_content, False, text))
        # 等待回复：指示变形成加载圈
        self.gui.set_marker_state("thinking")

    def submit_voice(self, audio_data: bytes):
        """语音消息（音频 + 截图）"""
        if not audio_data:
            logger.warning("⚠️ 未录到音频数据")
            return

        img_b64 = capture_screen()

        # 只有音频能量足够时才转写
        transcript = ""
        if self.audio.compute_energy(audio_data) >= self.config.MIN_AUDIO_ENERGY:
            logger.info("📝 转写中...")
            transcript = self.stt.transcribe(audio_data)
            if transcript:
                logger.info("📝 你说: %s", transcript)

        if transcript:
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": f"用户说：「{transcript}」\n请结合屏幕截图回答。"},
            ]
            history_text = transcript
            logger.info("📤 发送: 语音 + 截图")
        else:
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "这是用户此时的屏幕画面，请简单评论一下你看到的内容。"},
            ]
            history_text = "（仅截图）"
            logger.info("📤 发送: 仅截图")

        self.state.pending.put((user_content, False, history_text))
        # 等待回复：指示变形成加载圈
        self.gui.set_marker_state("thinking")

    # ========================
    # 录音（按住 V 说话）
    # ========================

    def start_recording(self):
        self.state.is_recording = True
        self.state.recorded_audio = None
        self._record_start_time = time.time()
        # 录音中：指示变形成录音方块
        self.gui.set_marker_state("recording")

        def target():
            self.state.recorded_audio = self.audio.record(lambda: not self.state.is_recording)

        self.state.record_thread = threading.Thread(target=target, daemon=True)
        self.state.record_thread.start()

    def stop_recording(self):
        self.state.is_recording = False
        if self.state.record_thread is not None:
            self.state.record_thread.join(timeout=1.0)

        # 防抖：录音太短直接丢弃，避免误触
        duration = time.time() - self._record_start_time
        if duration < self.config.MIN_RECORD_SECONDS:
            logger.info("⏭ 录音过短（%.2fs），已忽略", duration)
            self.gui.set_marker_state("idle")
            return

        audio_data = self.state.recorded_audio or b""
        # 松开即进入等待回复：指示变形成加载圈（submit_voice 里也会设，这里先切）
        self.gui.set_marker_state("thinking")
        threading.Thread(target=self.submit_voice, args=(audio_data,), daemon=True).start()

    # ========================
    # 随机吐槽
    # ========================

    def start_roast_loop(self):
        threading.Thread(target=self._roast_loop, daemon=True).start()

    def _roast_loop(self):
        next_roast_time = time.time() + random.randint(
            self.config.MIN_ROAST_INTERVAL, self.config.MAX_ROAST_INTERVAL
        )
        while True:
            time.sleep(1)
            if self.state.is_ai_speaking or self.state.is_recording:
                continue
            if time.time() < next_roast_time:
                continue

            logger.info("=" * 50)
            logger.info("🎭 AI 随机观察")
            logger.info("=" * 50)

            img_b64 = capture_screen()
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "这是我当前的屏幕。请像朋友一样随意评论一下，简短自然即可。"},
            ]
            self.state.pending.put((user_content, True, "（随机观察）"))

            next_roast_time = time.time() + random.randint(
                self.config.MIN_ROAST_INTERVAL, self.config.MAX_ROAST_INTERVAL
            )

    # ========================
    # 对话队列 worker（串行处理）
    # ========================

    def start_worker(self):
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            user_content, is_roast, history_text = self.state.pending.get()
            self.handle_conversation(user_content, is_roast, history_text)

    # ========================
    # 全局键盘监听
    # ========================

    def start_keyboard_listener(self):
        listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        listener.start()

    def _on_press(self, key):
        name = key_name(key)
        if name in MODIFIER_NAMES:
            self._held_mods.add(name)
            return

        # 自己家窗口（设置/聊天输入框）在前台时不触发，避免打字误触
        if foreground_is_ours():
            return

        if self._hk["ptt"].matches(key, self._held_mods):
            # 按住说话：AI 正在说话则先打断（barge-in）
            if self.state.is_ai_speaking:
                self.tts.request_stop()
            if not self.state.is_recording:
                self.start_recording()
                self._ptt_armed = True
            return
        if self._hk["interrupt"].matches(key, self._held_mods):
            self.tts.request_stop()
            return
        if self._hk["chat"].matches(key, self._held_mods):
            self.gui.toggle_chat()
            return
        if self._hk["settings"].matches(key, self._held_mods):
            self.gui.open_settings()
            return
        if self._hk["region"].matches(key, self._held_mods):
            # 隐私模式（框选截图）待实现，先占位
            logger.info("🖼 框选截图热键已触发（隐私模式待实现）")
            return

    def _on_release(self, key):
        name = key_name(key)
        if self._ptt_armed:
            hk = self._hk["ptt"]
            # 松开主键或任一修饰键即停止录音
            if name is not None and (name == hk.main or name in hk.mods):
                self._ptt_armed = False
                self.stop_recording()
        if name in MODIFIER_NAMES:
            self._held_mods.discard(name)
