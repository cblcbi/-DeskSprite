# -*- coding: utf-8 -*-
"""统一管理所有 Tkinter 窗口（屏幕画笔 + 文本输入框）

架构约定：
- GUI 全部运行在 Tk 主线程（GUIManager.run 里跑 mainloop）
- 后台线程（录音/LLM/TTS/键盘监听）只通过线程安全的 queue 提交 UI 指令
"""

import queue
import tkinter as tk
from itertools import zip_longest
from typing import Callable, List, Tuple

from config import Config
from ui.bubble import BubbleLayer
from ui.chat_input import ChatInput
from ui.danmaku import DanmakuLayer
from ui.screen_marker import ScreenMarker
from ui.settings_window import SettingsWindow


class GUIManager:
    def __init__(self):
        self.root = tk.Tk()  # 隐藏的主窗口（必须在主线程创建）
        self.root.withdraw()
        self._cmd_queue: "queue.Queue" = queue.Queue()
        self._text_handler: Callable[[str], None] = None
        self._settings_saved_handler: Callable[[], None] = None

        self.marker = ScreenMarker(self.root)
        self.chat = ChatInput(self.root, on_submit=self._on_chat_submit)
        self.bubble = BubbleLayer(self.root)  # AI 说话气泡（默认展示方式）
        self.danmaku = DanmakuLayer(self.root, self.marker.width, self.marker.height)  # 飘屏弹幕（可选）
        # 指示落点 → 气泡锚点：气泡显示在 AI 指示的右上角
        self.marker.set_position_callback(self.bubble.set_anchor)
        # 启动即散步：屏幕画笔程序一启动就出来溜溜
        self.marker.start_idle()
        self._settings_win = None

    # ========================
    # 线程安全的对外接口（可被任意后台线程调用）
    # ========================

    def set_text_handler(self, handler: Callable[[str], None]):
        """注册文本消息处理回调（由 orchestrator 注入）"""
        self._text_handler = handler

    def set_settings_saved_handler(self, handler: Callable[[], None]):
        """注册设置保存后回调（提示用户重启或热重载）"""
        self._settings_saved_handler = handler

    def show_markers(self, markers, holds=None, start_delay_ms=0, words=None):
        """在屏幕上显示标记（最多3 处）；holds 为每处停留毫秒，最后一个应为 None 挂起。
        words: [(time_ms, text)] 词级时间戳，用于说话气泡（可选）"""
        self._cmd_queue.put(("show_markers", (markers, holds or [], start_delay_ms, words)))

    def release_markers(self):
        """语音播完，让挂起的标记收手"""
        self._cmd_queue.put(("release_markers", None))

    def toggle_chat(self):
        self._cmd_queue.put(("toggle_chat", None))

    def hide_chat(self):
        self._cmd_queue.put(("hide_chat", None))

    def open_settings(self):
        """呼出设置浮窗（F3）"""
        self._cmd_queue.put(("open_settings", None))

    def set_marker_state(self, state: str):
        """指示状态：idle / recording（录音方块）/ thinking（等待回复加载圈）"""
        self._cmd_queue.put(("marker_state", state))

    # ========================
    # 主线程：事件循环
    # ========================

    def run(self):
        """主线程入口：轮询指令队列 + 进入 Tk mainloop"""
        def poll():
            try:
                while True:
                    cmd, payload = self._cmd_queue.get_nowait()
                    self._dispatch(cmd, payload)
            except queue.Empty:
                pass
            self.root.after(40, poll)

        self.root.after(40, poll)
        self.root.mainloop()

    def _dispatch(self, cmd: str, payload):
        """在 Tk 主线程中执行 UI 指令"""
        if cmd == "show_markers":
            markers, holds, delay, words = payload
            if markers:
                # 先与当前屏幕尺寸对齐（运行中改分辨率/缩放后自动修复偏移）
                self.marker.sync_size()
                self.danmaku.resize(self.marker.width, self.marker.height)
                items = [
                    (kind, nums, hold)
                    for (kind, nums), hold in zip_longest(markers, holds, fillvalue=None)
                ]
                self.marker.show_batch(items, start_delay_ms=delay)
                # 画笔窗口提到最前，圈圈不被气泡浮层盖住
                self.marker._root.lift()
                # 气泡从指示光标当前位置起步，之后每帧贴住跟随（不提前去目标点）
                self.bubble.set_anchor(*self.marker.current_pos())
            if words and Config.DANMAKU_ENABLED:
                # AI 说话气泡：指示右上角逐句弹出
                self.bubble.set_items(words)
                self.bubble.start(start_delay_ms=0)
            if words and Config.DANMAKU_FLY_ENABLED:
                # 飘屏弹幕（可选开关，默认关）
                self.danmaku.set_items(words)
                self.danmaku.start(start_delay_ms=0)
        elif cmd == "release_markers":
            # 标记收手 + 气泡温和收尾（TTS 结束/被打断时）；弹幕飘完自然消失
            self.marker.release()
            self.bubble.release()
        elif cmd == "toggle_chat":
            self.chat.toggle()
        elif cmd == "hide_chat":
            self.chat.hide()
        elif cmd == "open_settings":
            if self._settings_win is None or not self._settings_win._win.winfo_exists():
                self._settings_win = SettingsWindow(self.root, on_saved=self._on_settings_saved)
            else:
                self._settings_win.show()
        elif cmd == "marker_state":
            self.marker.set_state(payload)

    def _on_settings_saved(self):
        if self._settings_saved_handler is not None:
            self._settings_saved_handler()

    # ========================
    # 内部
    # ========================

    def _on_chat_submit(self, text: str):
        if self._text_handler is not None:
            self._text_handler(text)
