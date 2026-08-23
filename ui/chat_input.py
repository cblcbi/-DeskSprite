# -*- coding: utf-8 -*-
"""文本输入框组件：F2 呼出/隐藏，Enter 发送，Shift+Enter 换行，Esc 收起

毛玻璃样式与设置浮窗一致（Win32 acrylic）。
注意：所有方法必须在 Tk 主线程中调用（由 GUIManager 统一调度）。
"""

import ctypes
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

import pyautogui

from ui.settings_window import _enable_acrylic, shadow_label


class ChatInput:
    """置顶输入框（毛玻璃 + 自动换行 + 随显示行数自动增高）"""

    WIN_W = 420
    MAX_LINES = 6  # 最多自动撑到几行（超出后 Text 内部可滚动）

    def __init__(self, root: tk.Tk, on_submit: Callable[[str], None]):
        self._on_submit = on_submit
        self._visible = False
        self._base_h = 88
        self._line_h = 26
        self._dragged = False  # 用户拖动过就不再自动贴右下角
        self._drag_dx = 0
        self._drag_dy = 0

        top = tk.Toplevel(root)
        top.title("陪聊")
        top.overrideredirect(True)  # 无标题栏，纯浮动面板
        top.attributes("-topmost", True)
        top.resizable(False, False)
        top.withdraw()
        top.configure(bg="#1e1e2e")

        frame = tk.Frame(top, bg="#1e1e2e")
        frame.pack(fill="both", expand=True)
        self._head = shadow_label(frame, "💬 说点什么（Enter 发送 · Shift+Enter 换行）",
                                  "#1e1e2e", "#cdd6f4", ("Microsoft YaHei", 10),
                                  anchor="w", padx=10, pady=(8, 2))
        # 标题栏没了，用顶部标签栏拖动窗口
        self._make_draggable(self._head)
        row = tk.Frame(frame, bg="#1e1e2e")
        row.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._text = tk.Text(row, wrap="word", height=1,
                             bg="#313244", fg="#cdd6f4",
                             insertbackground="#cdd6f4", relief="flat",
                             font=("Microsoft YaHei", 12),
                             padx=8, pady=6, highlightthickness=0, bd=0)
        self._text.pack(side="left", fill="both", expand=True)
        tk.Button(row, text="发送", command=self._request_send,
                  bg="#f38ba8", fg="#1e1e2e", relief="flat",
                  font=("Microsoft YaHei", 10, "bold"), padx=12
                  ).pack(side="left", padx=(8, 0))

        self._text.bind("<Return>", self._on_enter)
        self._text.bind("<Shift-Return>", self._on_shift_enter)
        self._text.bind("<Escape>", lambda e: self.hide())
        self._text.bind("<KeyRelease>", lambda e: self._autosize())
        self._text.bind("<<Paste>>", lambda e: self._root.after_idle(self._autosize))

        # 毛玻璃（与设置浮窗同款，但 tint 更浅：3C 更透，聊天时不影响看背景）
        top.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(top.winfo_id()) or top.winfo_id()
        _enable_acrylic(hwnd, 0x3C2E1E1E)

        # 用初始布局实测 1 行高度（自适应字体/DPI）
        top.update_idletasks()
        self._base_h = max(60, top.winfo_height())
        # 行高取自字体 metrics（不依赖窗口映射状态，布局前也准确）
        try:
            self._line_h = max(12, tkfont.Font(font=self._text["font"]).metrics("linespace"))
        except Exception:
            pass

        self._root = top

    # ---------- 对外 ----------

    def toggle(self):
        """呼出/隐藏（仅主线程调用）"""
        if self._visible:
            self._hide()
        else:
            self._dragged = False  # 每次呼出重置到右下角
            self._root.deiconify()
            self._visible = True
            self._autosize()
            self._text.focus_force()

    def _make_draggable(self, widget):
        """给 widget 绑定鼠标拖动窗口（无标题栏的自定义拖动）"""
        def start(e):
            self._drag_dx = e.x_root - self._root.winfo_x()
            self._drag_dy = e.y_root - self._root.winfo_y()
        def drag(e):
            self._dragged = True
            sw, sh = pyautogui.size()
            x = max(0, min(sw - self.WIN_W, e.x_root - self._drag_dx))
            y = max(0, min(sh - 40, e.y_root - self._drag_dy))
            self._root.geometry(f"+{x}+{y}")
        widget.bind("<Button-1>", start)
        widget.bind("<B1-Motion>", drag)

    def hide(self):
        """收起（仅主线程调用）"""
        self._hide()

    # ---------- 内部 ----------

    def _hide(self):
        if self._visible:
            self._root.withdraw()
            self._visible = False

    def _display_lines(self) -> int:
        """当前显示行数（含自动换行后的行数）

        tk count -displaylines 的语义是"总显示行数 - 1"：
        空文本=0、两逻辑行=1、换行成 4 行=3，实测全部吻合，因此 +1 即真实行数。
        """
        try:
            res = self._text.count("1.0", "end-1c", "displaylines") or [0]
            return max(1, min(int(res[0]) + 1, self.MAX_LINES))
        except Exception:
            try:
                n_logical = int(self._text.index("end-1c").split(".")[0])
                return max(1, min(n_logical, self.MAX_LINES))
            except Exception:
                return 1

    def _autosize(self):
        """按显示行数调整窗口高度

        未拖动时锚定屏幕右下角（增高向上长）；拖动过则保持当前位置就地增高。
        Text 的 height 属性设为实际显示行数，窗口高度取其请求高度，避免估算误差。
        """
        n = self._display_lines()
        self._text.configure(height=n)
        self._root.update_idletasks()
        h = max(self._base_h, self._root.winfo_reqheight())
        if self._dragged:
            x = self._root.winfo_x()
            y = self._root.winfo_y()
        else:
            w, sh = pyautogui.size()
            x = w - self.WIN_W - 20
            y = sh - h - 60
        self._root.geometry(f"{self.WIN_W}x{h}+{x}+{y}")

    def _on_enter(self, event):
        self._request_send()
        return "break"

    def _on_shift_enter(self, event):
        self._text.insert("insert", "\n")
        self._autosize()
        return "break"

    def _request_send(self):
        text = self._text.get("1.0", "end-1c").strip()
        if not text:
            return
        self._text.delete("1.0", tk.END)
        self._hide()
        self._on_submit(text)
