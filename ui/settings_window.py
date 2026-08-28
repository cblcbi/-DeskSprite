# -*- coding: utf-8 -*-
"""设置浮窗：Win32 acrylic 毛玻璃效果，分 API / TTS / 通用 / 历史 四区

仅在主线程调用。保存时：
- 密钥写 .env（GG_API_KEY, QWEN_API_KEY）
- 其余写 settings.json
- 历史写 history.json
"""

import ctypes
import os
import tkinter as tk
from tkinter import ttk
from typing import Callable

import pyautogui

from utils import persistence
from utils.hotkeys import canonicalize, display
from utils.paths import BASE_DIR

ENV_FILE = os.path.join(BASE_DIR, ".env")

# Tk keysym → 快捷键规范名（pynput 风格小写）
_KEYSYM_TO_HOTKEY = {
    "escape": "esc", "return": "enter", "kp_enter": "enter", "tab": "tab",
    "backspace": "backspace", "delete": "delete", "home": "home", "end": "end",
    "insert": "insert", "page_up": "page_up", "page_down": "page_down",
    "print": "print_screen", "caps_lock": "caps_lock", "num_lock": "num_lock",
    "scroll_lock": "scroll_lock", "pause": "pause", "menu": "menu",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "space": "space", "minus": "-", "equal": "=", "bracketleft": "[",
    "bracketright": "]", "semicolon": ";", "apostrophe": "'", "comma": ",",
    "period": ".", "slash": "/", "backslash": "\\", "grave": "`",
}

# Tk keysym → 修饰键规范名
_KEYSYM_TO_MOD = {
    "control_l": "ctrl", "control_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "win_l": "win", "win_r": "win",
}

# Whisper 模型预设：auto=自动发现 models/；模型名=本地没有时联网下载；也可手输路径
WHISPER_OPTIONS = [
    "auto",
    "tiny", "base", "small", "medium",
    "large-v2", "large-v3", "large-v3-turbo", "turbo",
]


def _keysym_to_hotkey(ks: str) -> str:
    """Tk keysym → 快捷键规范名；F21-F24 与 pynput 不对齐，忽略"""
    ks = ks.lower()
    if ks.startswith("f") and ks[1:].isdigit():
        n = int(ks[1:])
        return f"f{n}" if 1 <= n <= 20 else ""
    return _KEYSYM_TO_HOTKEY.get(ks, ks if len(ks) == 1 else "")


def _enable_acrylic(hwnd: int, gradient_color: int = 0x662E1E1E):
    """给窗口加 Win32 acrylic 毛玻璃（Win10 1809+）

    gradient_color: AABBGGRR，暗色 tint（#1e1e2e）加 alpha。alpha 太浅时
    窗口内容会被 DWM 与亮色背景提亮混合，文字洗白看不清；越深越"实心"，
    越浅越透。设置窗口用 66，聊天输入框用 3C（更透）。
    """
    try:
        import ctypes
        from ctypes import wintypes

        ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

        class AccentPolicy(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_uint),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_uint),
            ]

        class WindowCompositionAttributeData(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(AccentPolicy)),
                ("SizeOfData", ctypes.c_int),
            ]

        accent = AccentPolicy()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.GradientColor = gradient_color
        data = WindowCompositionAttributeData()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)

        user32 = ctypes.windll.user32
        user32.SetWindowCompositionAttribute(hwnd, ctypes.pointer(data))
    except Exception:
        pass  # 不支持就算了，退回普通窗口


# 粉紫文字（Catppuccin Mauve），配合深色 tint 毛玻璃在任何背景下都可读
TEXT_FG = "#cba6f7"


def shadow_label(parent, text, bg, fg, font, **pack_kw):
    """毛玻璃上的文字：深色描边 + 投影垫底，保证任何背景下都可读

    用 Canvas 画三层文字：8 方向深色描边、右下投影、主文字在最上。
    """
    c = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -1), (-1, 0), (1, 0), (0, 1)):
        c.create_text(dx, dy, text=text, font=font, fill="#0b0b12", anchor="nw")
    c.create_text(2, 2, text=text, font=font, fill="#0b0b12", anchor="nw")
    c._shadow_main = c.create_text(0, 0, text=text, font=font, fill=fg, anchor="nw")
    c.update_idletasks()
    x1, y1, x2, y2 = c.bbox("all")
    c.configure(width=max(1, x2 - x1), height=max(1, y2 - y1))
    c.pack(**pack_kw)
    return c


class ScrollableFrame(tk.Frame):
    """内容可滚动的容器：Canvas + 纵向滚动条 + 滚轮支持

    内容放进 inner 里；窗口高度不够时出现滚动条，滚轮悬停可滚。
    """

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._sb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._sb.pack(side="right", fill="y")

        self.inner = tk.Frame(self._canvas, bg=bg)
        self._win_id = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind("<Configure>", self._on_canvas_size)
        # 指针在内容区时滚轮滚动；离开即解绑，避免影响其他窗口
        self.inner.bind("<Enter>", lambda e: self._canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.inner.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

    def _on_canvas_size(self, e):
        self._canvas.itemconfigure(self._win_id, width=e.width)

    def _on_wheel(self, event):
        try:
            steps = -int(event.delta / 120)
            if steps == 0:
                steps = -1 if event.delta > 0 else 1
            self._canvas.yview_scroll(steps * 2, "units")
        except Exception:
            pass


class SettingsWindow:
    """毛玻璃设置浮窗（主线程）"""

    def __init__(self, root: tk.Tk, on_saved: Callable[[], None] = None):
        self._on_saved = on_saved
        self._win = tk.Toplevel(root)
        self._win.title("设置")
        self._win.attributes("-topmost", True)
        self._win.configure(bg="#1e1e2e")
        self._win.resizable(False, False)

        # 尝试毛玻璃
        self._win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id()) or self._win.winfo_id()
        _enable_acrylic(hwnd)

        self._vars = {}
        self._check_vars = {}
        self._hotkey_labels = {}
        self._rec_key = None  # 正在录制的快捷键键名
        self._build_ui()
        self._load_current()

        # 居中定位；高度按屏幕自适应（小屏不截断，多余内容靠标签页内滚动条）
        sw, sh = pyautogui.size()
        h = min(780, max(480, sh - 120))
        self._win_x = (sw - 560) // 2
        self._win_y = max(10, (sh - h) // 2)
        self._win.geometry(f"560x{h}+{self._win_x}+{self._win_y}")

    # ---------- UI ----------

    def _build_ui(self):
        bg = "#1e1e2e"
        fg = TEXT_FG
        accent = "#f38ba8"

        container = tk.Frame(self._win, bg=bg, padx=20, pady=16)
        container.pack(fill="both", expand=True)

        # 底部按钮栏先 pack(side=bottom)，避免被 Notebook 挤成一条线
        bottom = tk.Frame(container, bg=bg)
        bottom.pack(side="bottom", fill="x", pady=(12, 0))

        style = ttk.Style()
        # clam 主题才吃 configure 的配色（vista 原生主题会忽略 Tab 背景色，
        # 画成浅灰，毛玻璃透浅色背景时标签文字被洗白）
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background="#313244", foreground=fg, padding=(14, 6))
        style.map("TNotebook.Tab", background=[("selected", accent)], foreground=[("selected", "#1e1e2e")])
        style.configure("TFrame", background=bg)
        style.configure("Vertical.TScrollbar", background="#313244", troughcolor=bg,
                        bordercolor=bg, lightcolor=bg, darkcolor=bg, arrowcolor=fg)
        style.configure("TCombobox", fieldbackground="#313244", background="#313244",
                        foreground=fg, arrowcolor=fg, bordercolor=bg, lightcolor=bg, darkcolor=bg)
        style.map("TCombobox", fieldbackground=[("readonly", "#313244")],
                  foreground=[("readonly", fg)])
        # 下拉列表颜色（option database，clam 主题的 Listbox 由它控制）
        self._win.option_add("*TCombobox*Listbox*Background", "#313244")
        self._win.option_add("*TCombobox*Listbox*Foreground", fg)
        self._win.option_add("*TCombobox*Listbox*selectBackground", accent)
        self._win.option_add("*TCombobox*Listbox*selectForeground", "#1e1e2e")

        nb = ttk.Notebook(container)
        nb.pack(fill="both", expand=True)

        self._add_entry_tab(nb, "主 API", [
            ("GG_BASE_URL", "API 地址"),
            ("GG_API_KEY", "API 密钥", True),
            ("GG_MODEL", "模型名"),
        ])
        self._add_entry_tab(nb, "TTS", [
            ("TTS_BACKEND", "后端（edge / dashscope）"),
            ("TTS_EDGE_VOICE", "edge 语音"),
            ("TTS_MODEL", "dashscope 模型"),
            ("TTS_VOICE", "dashscope 语音"),
            ("QWEN_API_KEY", "DashScope 密钥（可选）", True),
        ])
        gen_tab = self._add_entry_tab(nb, "通用", [
            ("WHISPER_MODEL_PATH", "Whisper 模型（auto=自动发现 models/，模型名=联网下载，也可手输路径）",
             False, WHISPER_OPTIONS),
            ("WHISPER_DEVICE", "Whisper 设备"),
            ("WHISPER_COMPUTE", "Whisper 精度"),
            ("MAX_TOKENS", "最大 token"),
            ("MIN_ROAST_INTERVAL", "吐槽最短间隔(秒)"),
            ("MAX_ROAST_INTERVAL", "吐槽最长间隔(秒)"),
            ("LOG_LEVEL", "日志级别"),
        ], extra_checkbox=("DANMAKU_ENABLED", "开启语音气泡（AI 说话时跟随指示）"))
        # 飘屏弹幕：旧式滚动弹幕，默认关
        self._check_vars["DANMAKU_FLY_ENABLED"] = tk.BooleanVar()
        tk.Checkbutton(gen_tab, text="开启飘屏弹幕（旧式，默认关）",
                       variable=self._check_vars["DANMAKU_FLY_ENABLED"],
                       bg=bg, fg=fg, selectcolor="#313244",
                       activebackground=bg, activeforeground=fg,
                       font=("Microsoft YaHei", 10),
                       ).pack(anchor="w", pady=(6, 0))
        self._add_hotkey_section(gen_tab)

        # 历史编辑 tab
        hist_tab = tk.Frame(nb, bg=bg, padx=12, pady=12)
        nb.add(hist_tab, text="对话历史")
        shadow_label(hist_tab, "可编辑，保存即生效（重启后用新历史）：",
                     bg, fg, ("Microsoft YaHei", 10), anchor="w")
        self._history_text = tk.Text(hist_tab, bg="#313244", fg=fg, insertbackground=fg,
                                     relief="flat", font=("Microsoft YaHei", 10), height=18)
        self._history_text.pack(fill="both", expand=True, pady=(6, 0))
        btns = tk.Frame(hist_tab, bg=bg)
        btns.pack(fill="x", pady=(6, 0))
        tk.Button(btns, text="清空历史", command=self._clear_history,
                  bg=accent, fg="#1e1e2e", relief="flat", padx=10).pack(side="left")

        # AI 人设 tab（预设 / 自定义）
        self._add_persona_tab(nb, bg, fg, accent)

        # 底部保存（bottom 已在上方 pack(side=bottom)）
        tk.Button(bottom, text="保存并关闭", command=self._save,
                  bg=accent, fg="#1e1e2e", relief="flat",
                  font=("Microsoft YaHei", 11, "bold"), padx=20, pady=10
                  ).pack(side="right")
        tk.Button(bottom, text="取消", command=self._win.destroy,
                  bg="#313244", fg=fg, relief="flat", padx=14, pady=10
                  ).pack(side="right", padx=(0, 8))

    def _add_entry_tab(self, nb, title, fields, extra_checkbox=None):
        bg = "#1e1e2e"
        fg = TEXT_FG
        tab = tk.Frame(nb, bg=bg, padx=12, pady=12)
        nb.add(tab, text=title)
        # 内容放进可滚动容器：高 DPI/小屏下超出的设置项靠滚动条查看
        sc = ScrollableFrame(tab, bg)
        sc.pack(fill="both", expand=True)
        inner = sc.inner
        for key, label, *rest in fields:
            is_secret = rest[0] if rest else False
            options = rest[1] if len(rest) > 1 else None
            shadow_label(inner, label, bg, fg, ("Microsoft YaHei", 10),
                         anchor="w", pady=(5, 1))
            var = tk.StringVar()
            if options:
                # 可编辑下拉：预设一键可选，也允许手输任意路径
                combo = ttk.Combobox(inner, textvariable=var, values=options,
                                     state="normal", font=("Microsoft YaHei", 10))
                combo.pack(fill="x", ipady=2)
            else:
                entry = tk.Entry(inner, textvariable=var, bg="#313244", fg=fg,
                                  insertbackground=fg, relief="flat", font=("Microsoft YaHei", 11),
                                  show="*" if is_secret else "")
                entry.pack(fill="x", ipady=2)
            self._vars[key] = var
        if extra_checkbox:
            key, label = extra_checkbox
            self._check_vars[key] = tk.BooleanVar()
            tk.Checkbutton(inner, text=label, variable=self._check_vars[key],
                           bg=bg, fg=fg, selectcolor="#313244",
                           activebackground=bg, activeforeground=fg,
                           font=("Microsoft YaHei", 10),
                           ).pack(anchor="w", pady=(6, 0))
        return inner

    # ---------- AI 人设 ----------

    PERSONA_ROWS = [
        ("default", "默认暖友（温柔幽默，提供情绪支持）"),
        ("genius", "毒舌损友（犀利吐槽，嘴硬心软）"),
        ("custom", "自定义（在下方文本框里写）"),
    ]

    def _add_persona_tab(self, nb, bg, fg, accent):
        tab = tk.Frame(nb, bg=bg, padx=12, pady=12)
        nb.add(tab, text="AI 人设")
        sc = ScrollableFrame(tab, bg)
        sc.pack(fill="both", expand=True)
        inner = sc.inner

        shadow_label(inner, "AI 人设：决定 AI 的性格与说话方式（保存后下一次对话立即生效）",
                     bg, fg, ("Microsoft YaHei", 10), anchor="w", pady=(4, 4))
        self._persona_var = tk.StringVar()
        for key, label in self.PERSONA_ROWS:
            tk.Radiobutton(inner, text=label, value=key, variable=self._persona_var,
                           command=self._on_persona_change,
                           bg=bg, fg=fg, selectcolor="#313244",
                           activebackground=bg, activeforeground=fg,
                           font=("Microsoft YaHei", 10),
                           ).pack(anchor="w", pady=(2, 0))
        self._persona_text = tk.Text(inner, bg="#313244", fg=fg, insertbackground=fg,
                                     relief="flat", font=("Microsoft YaHei", 10),
                                     height=14, wrap="word", padx=8, pady=6)
        self._persona_text.pack(fill="both", expand=True, pady=(6, 0))

    def _on_persona_change(self):
        if self._persona_var.get() == "custom":
            self._persona_text.pack(fill="both", expand=True, pady=(6, 0))
        else:
            self._persona_text.pack_forget()

    # ---------- 快捷键 ----------

    HOTKEY_ROWS = [
        ("HOTKEY_PTT", "按住说话"),
        ("HOTKEY_CHAT", "聊天输入框"),
        ("HOTKEY_SETTINGS", "设置窗口"),
        ("HOTKEY_INTERRUPT", "打断语音"),
        ("HOTKEY_REGION", "框选截图（待实现）"),
    ]

    def _add_hotkey_section(self, tab):
        bg = "#1e1e2e"
        fg = TEXT_FG
        accent = "#f38ba8"
        shadow_label(tab, "快捷键：点「录制」后按下新组合（Esc 取消，支持任意键）",
                     bg, fg, ("Microsoft YaHei", 9), anchor="w", pady=(8, 3))
        for key, label in self.HOTKEY_ROWS:
            row = tk.Frame(tab, bg=bg)
            row.pack(fill="x", pady=1)
            # side="left" 让动作名与按钮同一行（默认 top 会把按钮挤到下一行）
            shadow_label(row, label, bg, fg, ("Microsoft YaHei", 9),
                         anchor="w", side="left")
            tk.Button(row, text="录制",
                      command=lambda k=key: self._start_record(k),
                      bg=accent, fg="#1e1e2e", relief="flat", padx=8,
                      font=("Microsoft YaHei", 9)).pack(side="right")
            tk.Button(row, text="清除",
                      command=lambda k=key: self._clear_hotkey(k),
                      bg="#313244", fg=fg, relief="flat", padx=6,
                      font=("Microsoft YaHei", 9)).pack(side="right", padx=(4, 0))
            disp = tk.Label(row, text="未设置", bg="#313244", fg=fg,
                            font=("Microsoft YaHei", 9), padx=6, pady=1,
                            width=14, anchor="w", relief="flat")
            disp.pack(side="right", padx=(8, 4))
            self._hotkey_labels[key] = disp

    def _start_record(self, key):
        """进入快捷键录制状态：监听本窗口的按键，按下的组合存进规范串"""
        if self._rec_key:
            self._cancel_record()
        self._rec_key = key
        self._rec_mods = []
        self._rec_old = self._hotkey_labels[key].cget("text")
        self._hotkey_labels[key].configure(text="请按键...")
        self._win.bind("<KeyPress>", self._on_rec_keypress)
        self._rec_timer = self._win.after(6000, self._cancel_record)

    def _on_rec_keypress(self, e):
        if e.keysym == "Escape":
            self._cancel_record()
            return
        mod = _KEYSYM_TO_MOD.get(e.keysym.lower())
        if mod:
            if mod not in self._rec_mods:
                self._rec_mods.append(mod)
            return
        main = _keysym_to_hotkey(e.keysym.lower())
        if not main:
            return
        combo = "+".join(self._rec_mods + [main])
        self._finish_record(combo)

    def _finish_record(self, combo):
        key = self._rec_key
        self._cleanup_record()
        if key:
            self._hotkey_labels[key].configure(text=display(combo))

    def _clear_hotkey(self, key):
        self._hotkey_labels[key].configure(text="未设置")

    def _cancel_record(self):
        key = self._rec_key
        self._cleanup_record()
        if key:
            self._hotkey_labels[key].configure(text=self._rec_old)

    def _cleanup_record(self):
        self._rec_key = None
        if getattr(self, "_rec_timer", None):
            try:
                self._win.after_cancel(self._rec_timer)
            except Exception:
                pass
            self._rec_timer = None
        try:
            self._win.unbind("<KeyPress>")
        except Exception:
            pass

    # ---------- 数据 ----------

    def _load_current(self):
        """把当前生效的配置填进表单"""
        from config import Config
        for key, var in self._vars.items():
            var.set(str(getattr(Config, key, "")))
        for key, var in self._check_vars.items():
            var.set(bool(getattr(Config, key, True)))
        for key, disp in self._hotkey_labels.items():
            disp.configure(text=display(getattr(Config, key, "")))
        self._persona_var.set(getattr(Config, "PERSONA", "default"))
        self._persona_text.delete("1.0", tk.END)
        self._persona_text.insert("1.0", getattr(Config, "CUSTOM_SYSTEM_PROMPT", ""))
        self._on_persona_change()
        # 历史显示
        history = persistence.load_history()
        text = "\n".join(f'{h["role"]}: {h["content"]}' for h in history)
        self._history_text.delete("1.0", tk.END)
        self._history_text.insert("1.0", text)

    def _save(self):
        """保存：密钥 → .env（增量更新），其余 → settings.json，历史 → history.json"""
        keys = ["GG_API_KEY", "QWEN_API_KEY"]
        self._save_env(keys)

        # 其余写 settings.json
        s = {}
        for k, var in self._vars.items():
            if k in keys:
                continue
            v = var.get().strip()
            if v != "":
                s[k] = v
        for k, var in self._check_vars.items():
            s[k] = "1" if var.get() else "0"
        for k, disp in self._hotkey_labels.items():
            txt = disp.cget("text")
            s[k] = "" if txt in ("未设置", "请按键...") else canonicalize(txt)
        s["PERSONA"] = self._persona_var.get()
        s["CUSTOM_SYSTEM_PROMPT"] = self._persona_text.get("1.0", tk.END).strip()
        persistence.save_settings(s)

        # 人设热切换：同步内存里的配置，下一次对话立即生效（不用重启）
        try:
            import config as config_mod
            config_mod.Config.PERSONA = s.get("PERSONA", "default")
            config_mod.Config.CUSTOM_SYSTEM_PROMPT = s.get("CUSTOM_SYSTEM_PROMPT", "")
        except Exception:
            pass

        # 历史
        raw = self._history_text.get("1.0", tk.END).strip()
        new_hist = []
        for line in raw.splitlines():
            if ": " in line:
                role, _, content = line.partition(": ")
                new_hist.append({"role": role.strip(), "content": content.strip()})
        persistence.save_history(new_hist)

        self._win.destroy()
        if self._on_saved:
            self._on_saved()

    def _save_env(self, keys):
        """增量更新 .env：只替换指定键的值，其他行（注释、非密钥配置）原样保留。

        空值表示删除该键；原文件没有的键追加到末尾。"""
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        values = {k: self._vars[k].get().strip() for k in keys}
        seen = set()
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                seen.add(key)
                if values[key]:
                    out.append(f"{key}={values[key]}\n")
                continue
            out.append(line)
        for k in keys:
            if k not in seen and values[k]:
                out.append(f"{k}={values[k]}\n")
        try:
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.writelines(out)
        except Exception:
            pass

    def _clear_history(self):
        self._history_text.delete("1.0", tk.END)

    def show(self):
        self._win.deiconify()
        self._win.focus_force()
