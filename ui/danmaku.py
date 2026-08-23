# -*- coding: utf-8 -*-
"""弹幕组件：独立的全屏透明浮层，把语音飘成彩色半透明滚动弹幕

- 输入：[(time_ms, dur_ms, text)] 句子级时间戳（由 edge-tts WordBoundary 分句得到）
- 独立 Toplevel + 窗口级 alpha（真半透明），与屏幕画笔互不干扰
- 句子在其开始被念出时（延后一点）生成一条弹幕，向左飘，飘完自然消失
- 飘速与该句实际朗读时长挂钩，多轨道随机分配
"""

import random
import time
import tkinter as tk
from typing import List, Optional, Tuple


class DanmakuLayer:
    FONT = ("Microsoft YaHei", 20, "bold")
    PALETTE = ["#FFE066", "#7BD8FF", "#FF9BD2", "#9BFF8A", "#FFB86B"]
    WINDOW_ALPHA = 0.7       # 窗口级透明度（真半透明）
    TRACK_H = 38             # 每条轨道高度
    TOP_MARGIN = 8           # 顶部留白
    SIDE_MARGIN = 20
    SPAWN_OFFSET_MS = 120    # 念到该句后延后一点再飘
    MIN_CROSS_MS = 4000      # 最短飘完时间
    MAX_CROSS_MS = 20000     # 最长飘完时间
    CROSS_FACTOR = 2.75      # 飘完时长 = 该句朗读时长 × 2.75 + 1s

    def __init__(self, root: tk.Tk, width: int, height: int):
        self.width = width
        self.height = height

        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-transparentcolor", "#010203")
        top.attributes("-alpha", self.WINDOW_ALPHA)
        top.geometry(f"{width}x{height}+0+0")
        canvas = tk.Canvas(
            top, width=width, height=height,
            bg="#010203", highlightthickness=0, bd=0,
        )
        canvas.pack()
        top.update_idletasks()
        top.update()

        # 点击穿透 + 不抢焦点 + 不进任务栏
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(top.winfo_id()) or top.winfo_id()
        GWL_EXSTYLE = -20
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            ex | 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080,
        )

        self._root = top
        self._canvas = canvas
        self._n_tracks = max(1, (height - self.TOP_MARGIN) // self.TRACK_H)
        self._track_until = [0] * self._n_tracks
        self._items: List[Tuple[float, float, str]] = []  # (time_ms, dur_ms, text)
        self._spawned = 0
        self._start_at: Optional[float] = None
        self._spawn_after: Optional[str] = None

    def set_items(self, items: List[Tuple[float, float, str]]):
        """设置本批弹幕：(time_ms 句首时刻, dur_ms 该句朗读时长, 文本)。
        不清理旧弹幕——它们按自己的节奏飘完自然消失。"""
        self._items = items
        self._spawned = 0
        self._start_at = None
        self._cancel_spawn()

    def start(self, start_delay_ms: int = 0):
        """开始调度（相对 now 延迟 start_delay_ms）"""
        self._start_at = time.time() * 1000 + start_delay_ms
        self._cancel_spawn()
        self._spawn_after = self._canvas.after(int(start_delay_ms), self._spawn_loop)

    def clear(self):
        """清掉全部弹幕（一般用不到：它们会自己飘完）"""
        self._cancel_spawn()
        self._canvas.delete("danmaku")
        self._items = []
        self._spawned = 0
        self._track_until = [0] * self._n_tracks

    def resize(self, width: int, height: int):
        """屏幕尺寸变化时同步浮层（轨道数按新高度重新分配）"""
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self._root.geometry(f"{width}x{height}+0+0")
        self._canvas.configure(width=width, height=height)
        self._n_tracks = max(1, (height - self.TOP_MARGIN) // self.TRACK_H)
        self._track_until = [0] * self._n_tracks

    # ---- 内部 ----

    def _cancel_spawn(self):
        if self._spawn_after is not None:
            try:
                self._canvas.after_cancel(self._spawn_after)
            except Exception:
                pass
            self._spawn_after = None

    def _spawn_loop(self):
        """到点检查：把所有到时的句子飘出来"""
        if self._start_at is None:
            return
        now = time.time() * 1000
        elapsed = now - self._start_at
        while self._spawned < len(self._items):
            t, dur, text = self._items[self._spawned]
            if t + self.SPAWN_OFFSET_MS > elapsed:
                break
            self._spawn_one(text, dur, elapsed)
            self._spawned += 1
        if self._spawned < len(self._items):
            self._spawn_after = self._canvas.after(50, self._spawn_loop)
        else:
            self._spawn_after = None

    def _spawn_one(self, text: str, dur_ms: float, now_ms: float):
        """生成一条弹幕：随机挑空闲轨道，按该句朗读时长（放慢）飘完"""
        free = [i for i, t in enumerate(self._track_until) if t <= now_ms]
        if not free:
            return
        track = random.choice(free)
        y = self.TOP_MARGIN + track * self.TRACK_H
        x0 = self.width + self.SIDE_MARGIN
        approx_w = max(60, len(text) * 20)
        x_end = -approx_w - self.SIDE_MARGIN
        # 飘完时长 = 朗读时长放慢 2.75 倍 + 1s
        cross = max(self.MIN_CROSS_MS, min(self.MAX_CROSS_MS, dur_ms * self.CROSS_FACTOR + 1000))
        self._track_until[track] = now_ms + cross

        color = random.choice(self.PALETTE)
        item = self._canvas.create_text(x0, y, text=text, font=self.FONT,
                                        fill=color, tags="danmaku")

        steps = max(1, int(cross / 33))
        dx = (x_end - x0) / steps

        def move(i: int, x: float):
            if i > steps:
                return
            x += dx
            self._canvas.coords(item, x, y)
            self._canvas.after(33, lambda: move(i + 1, x))

        move(0, x0)
