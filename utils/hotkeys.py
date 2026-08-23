# -*- coding: utf-8 -*-
"""快捷键解析与匹配（任意键组合）

约定：配置串形如 "ctrl+alt+v" / "f2" / "win+shift+s"，全小写、'+' 连接。
修饰键左右不分，统一归并为 ctrl / alt / shift / win。
字母、数字、F1-F20、方向键、功能键、标点都能作主键（pynput 低层钩子
能听到全部按键，Windows 系统保留组合如 Win+L 除外）。
"""

import ctypes
import os
from ctypes import wintypes

from pynput import keyboard

MODIFIER_NAMES = {"ctrl", "alt", "shift", "win"}

_ALIASES = {
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "control": "ctrl",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "win",
    "cmd_l": "win",
    "cmd_r": "win",
    "windows": "win",
    "meta": "win",
    "escape": "esc",
    "return": "enter",
}

# 特殊字符 → 规范名（pynput 里这些字符可能以 Key 或 KeyCode 形式出现）
_CHAR_MAP = {
    " ": "space",
    "\r": "enter",
    "\n": "enter",
    "\t": "tab",
    "\x1b": "esc",
    "\b": "backspace",
}


def key_name(key):
    """pynput 按键对象 → 规范名；未知按键（无 char 的 KeyCode）返回 None"""
    if isinstance(key, keyboard.Key):
        n = key.name.lower() if key.name else ""
        return _ALIASES.get(n, n) or None
    if hasattr(key, "char") and key.char is not None:
        c = key.char.lower()
        if c in _CHAR_MAP:
            return _CHAR_MAP[c]
        if len(c) == 1:
            return c
    return None


def canonicalize(spec: str) -> str:
    """显示串/任意写法 → 规范串（小写、去空格、修饰键归并，保持输入顺序）"""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        return ""
    mods = []
    main = ""
    for p in parts:
        n = _ALIASES.get(p, p)
        if n in MODIFIER_NAMES:
            if n not in mods:
                mods.append(n)
        else:
            main = n
    return "+".join(mods + ([main] if main else []))


def display(spec: str) -> str:
    """规范串 → 显示串（Ctrl+Alt+V）；空返回「未设置」"""
    c = canonicalize(spec)
    if not c:
        return "未设置"
    return "+".join(p.capitalize() for p in c.split("+"))


class Hotkey:
    """一个可配置快捷键：修饰键集合 + 主键"""

    def __init__(self, spec: str):
        self.spec = canonicalize(spec)
        parts = self.spec.split("+")
        self.main = parts[-1] if parts else ""
        self.mods = frozenset(p for p in parts[:-1] if p in MODIFIER_NAMES)
        self.enabled = bool(self.main)

    def matches(self, key, held_mods) -> bool:
        """主键匹配且修饰键集合与当前按住的一致（避免 ctrl+f2 误触发 f2）"""
        if not self.enabled:
            return False
        name = key_name(key)
        if name is None or name != self.main:
            return False
        return self.mods == frozenset(held_mods)


def foreground_is_ours() -> bool:
    """前台窗口是否属于本进程（自己家窗口在前台时不触发全局热键，
    避免在设置/聊天输入框里打字触发录音等）"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False
