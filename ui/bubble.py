# -*- coding: utf-8 -*-
"""AI 说话气泡：跟随屏幕指示，固定两行的滚动字幕气泡

- 输入：[(time_ms, dur_ms, text)] 句子级时间戳（edge-tts WordBoundary 分句）
- 毛玻璃样式与聊天输入框（F2）一致：tint 0x3C（更透），浅粉紫文字
- 锚定在 AI 指示右上角，指示移动时平滑跟随（ScreenMarker 每帧回调位置）
- 固定显示两行：新句子从底部滚入，旧句子向上滚出（终端字幕感）
- 全部句子念完（含最后一句朗读时长）再停留 1.2s 自动隐藏；TTS 结束/打断
  时补全剩余句子，稍作停留后收起
"""

import ctypes
import time
import tkinter as tk
from tkinter import font as tkfont

from ui.settings_window import _enable_acrylic

GRADIENT = 0x3C2E1E1E  # 与 F2 聊天输入框同款毛玻璃


class BubbleLayer:
    WIDTH = 360
    DISPLAY_LINES = 2        # 固定显示两行
    MAX_KEEP_LINES = 60      # 内部最多保留行数，超出丢顶部防膨胀
    FONT = ("Microsoft YaHei", 11)
    FG = "#cdd6f4"
    BG = "#1e1e2e"
    TICK_MS = 40
    ROLL_MS = 40             # 滚动字幕每帧间隔（原 16ms，放缓 2.5 倍）
    HIDE_DELAY_MS = 1200     # 念完后停留时间

    def __init__(self, root: tk.Tk):
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.withdraw()
        top.configure(bg=self.BG)

        self._text = tk.Text(top, bg=self.BG, fg=self.FG, insertbackground=self.FG,
                             relief="flat", font=self.FONT, wrap="word", height=self.DISPLAY_LINES,
                             padx=14, pady=8, bd=0, highlightthickness=0,
                             state="disabled", spacing1=2, spacing3=2)
        self._text.pack(fill="both", expand=True)
        self._line_h = max(12, tkfont.Font(font=self.FONT).metrics("linespace"))
        self._display_h = self.DISPLAY_LINES  # 当前显示行数（超长句子临时扩高）
        self._target_h = self.DISPLAY_LINES * self._line_h + 24

        top.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(top.winfo_id()) or top.winfo_id()
        _enable_acrylic(hwnd, GRADIENT)
        # 点击穿透 + 不抢焦点（气泡不挡鼠标）
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x00000020 | 0x08000000)

        self._top = top
        self._items = []
        self._shown = 0
        self._start_at = None
        self._done = False
        self._tick_after = None
        self._hide_after = None
        self._roll_after = None
        self._anchor = None   # 指示光标当前位置 (x, y)；None=跟随鼠标

    # ---------- 对外 ----------

    def set_items(self, items):
        """设置本批句子：(time_ms 句首时刻, dur_ms 朗读时长, 文本)"""
        self._cancel_all()
        self._items = list(items)
        self._shown = 0
        self._start_at = None
        self._done = False

    def start(self, start_delay_ms: int = 0):
        """开始逐句显示（相对 now 延迟 start_delay_ms）"""
        self._start_at = time.time() * 1000 + start_delay_ms
        self._done = False
        self._resize()
        self._top.deiconify()
        self._top.update_idletasks()
        self._place()
        if self._tick_after is None:
            self._tick_after = self._top.after(self.TICK_MS, self._tick)

    def release(self):
        """TTS 结束/被打断：补全剩余句子，稍作停留后收起（不立刻消失）"""
        if self._start_at is not None and self._shown < len(self._items):
            while self._shown < len(self._items):
                self._append(self._items[self._shown][2])
                self._shown += 1
            self._roll_to_bottom()
        if not self._done:
            self._done = True
            self._hide_after = self._top.after(self.HIDE_DELAY_MS, self.hide)

    def set_anchor(self, x: float, y: float):
        """指示位置：气泡精确贴住指示光标（每帧同步，无插值、无提前）"""
        self._anchor = (float(x), float(y))
        if self._start_at is not None:
            self._place()

    def hide(self):
        """收起并清空，等待下一批"""
        self._cancel_all()
        self._top.withdraw()
        self._items = []
        self._shown = 0
        self._start_at = None
        self._done = False
        self._anchor = None
        self._text.configure(state="normal")
        self._text.delete("1.0", tk.END)
        self._text.configure(state="disabled")

    # ---------- 内部 ----------

    def _tick(self):
        self._tick_after = None
        if self._start_at is None:
            return
        elapsed = time.time() * 1000 - self._start_at
        appended = False
        while self._shown < len(self._items):
            t, _dur, text = self._items[self._shown]
            if t > elapsed:
                break
            self._append(text)
            self._shown += 1
            appended = True
        if appended:
            self._roll_to_bottom()
        self._place()
        # 全部上屏 且 最后一句也念完 → 停留后自动隐藏（说完话再消失）
        if self._shown >= len(self._items) and not self._done and self._items:
            last_t, last_dur, _ = self._items[-1]
            if elapsed >= last_t + last_dur:
                self._done = True
                self._hide_after = self._top.after(self.HIDE_DELAY_MS, self.hide)
        self._tick_after = self._top.after(self.TICK_MS, self._tick)

    def _append(self, text: str):
        """追加一句：显示行数跟随该句折行数（超长句子临时扩高，看全再滚走）"""
        self._text.configure(state="normal")
        # 内部保留行数封顶：超出从顶部丢，保证滚动流畅
        n = self._line_count()
        if n > self.MAX_KEEP_LINES:
            drop = n - self.MAX_KEEP_LINES
            self._text.delete(f"1.0", f"{drop + 1}.0")
        before = self._line_count()
        if self._text.get("1.0", "end-1c").strip():
            self._text.insert("end", "\n")
        self._text.insert("end", text)
        self._text.configure(state="disabled")
        # 该句折行后占几行（插入前后总行数之差）→ 显示区至少能放下整句
        sent_lines = max(1, self._line_count() - before)
        self._display_h = max(self.DISPLAY_LINES, sent_lines)
        self._resize()

    def _line_count(self) -> int:
        """文本总显示行数（wrap 折行后）"""
        try:
            return int((self._text.count("1.0", "end", "displaylines") or [0])[0]) + 1
        except Exception:
            return 1

    def _resize(self):
        """按当前显示行数设置窗口高度"""
        self._target_h = self._display_h * self._line_h + 24
        self._top.geometry(f"{self.WIDTH}x{self._target_h}")
        self._text.configure(height=self._display_h)

    def _roll_to_bottom(self):
        """平滑滚到底：新句子从底部进入，旧句子向上滚出"""
        def step():
            self._roll_after = None
            if self._text.yview()[1] >= 0.999:
                self._text.yview_moveto(1.0)
                return
            self._text.yview_scroll(1, "units")
            self._roll_after = self._top.after(self.ROLL_MS, step)

        self._cancel_roll()
        step()

    def _place(self):
        """锚定当前指示的右上角（无指示时跟随鼠标），越出屏幕边缘自动翻转/clamp"""
        cx, cy = self._anchor if self._anchor is not None else self._cursor()
        vx, vy, vw, vh = self._virtual_screen()
        w = self.WIDTH
        h = self._target_h
        x = cx + 18
        y = cy - h - 18
        if x + w > vx + vw:
            x = cx - w - 18          # 右边放不下 → 翻到左边
        if y < vy:
            y = cy + 18              # 上边放不下 → 放到下方
        x = max(vx + 6, x)
        y = max(vy + 6, y)
        if y + h > vy + vh:
            y = vy + vh - h - 6
        self._top.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def _cursor(self):
        try:
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
        except Exception:
            return 0, 0

    def _virtual_screen(self):
        """虚拟屏矩形（多显示器）(x, y, w, h)"""
        try:
            user32 = ctypes.windll.user32
            return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                    user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))
        except Exception:
            return 0, 0, 1920, 1080

    def _cancel_roll(self):
        if self._roll_after is not None:
            try:
                self._top.after_cancel(self._roll_after)
            except Exception:
                pass
            self._roll_after = None

    def _cancel_all(self):
        self._cancel_roll()
        for attr in ("_tick_after", "_hide_after"):
            h = getattr(self, attr)
            if h is not None:
                try:
                    self._top.after_cancel(h)
                except Exception:
                    pass
                setattr(self, attr, None)
