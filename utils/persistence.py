# -*- coding: utf-8 -*-
"""持久化：settings.json（非密钥配置） + history.json（对话历史）

- 密钥仍走 .env（config.py 已处理），这里只管非敏感配置和历史
- 线程安全：所有读写都在调用方线程；JSON 文件操作用原子写
"""

import json
import os
import threading

from utils.paths import BASE_DIR

SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")

_lock = threading.Lock()


# ---------- 设置 ----------

DEFAULT_SETTINGS = {
    "GG_MODEL": "gemini-3-flash-preview",
    "GG_BASE_URL": "https://gcli.ggchan.dev/v1",
    "TTS_BACKEND": "edge",
    "TTS_EDGE_VOICE": "zh-CN-XiaoxiaoNeural",
    "TTS_MODEL": "qwen3-tts-flash",
    "TTS_VOICE": "Bella",
    "WHISPER_MODEL_PATH": "small",  # 模型大小名（自动下载）或本地路径
    "WHISPER_DEVICE": "cuda",
    "WHISPER_COMPUTE": "float16",
    "MAX_TOKENS": 8192,
    "MIN_ROAST_INTERVAL": 30,
    "MAX_ROAST_INTERVAL": 300,
    "LOG_LEVEL": "INFO",
    "DANMAKU_ENABLED": "1",       # 语音气泡
    "DANMAKU_FLY_ENABLED": "0",   # 飘屏弹幕（旧式，默认关）
    # 快捷键（形如 "ctrl+alt+v" / "f2"，空串=禁用）
    "HOTKEY_PTT": "v",
    "HOTKEY_CHAT": "f2",
    "HOTKEY_SETTINGS": "f3",
    "HOTKEY_INTERRUPT": "",
    "HOTKEY_REGION": "",
    # AI 人设（default / genius / custom）
    "PERSONA": "default",
    "CUSTOM_SYSTEM_PROMPT": "",
}


def load_settings() -> dict:
    """读取 settings.json，缺失字段用默认值补齐"""
    with _lock:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(settings: dict):
    """写入 settings.json（只写已知字段，密钥不进这里）"""
    cleaned = {k: settings.get(k, DEFAULT_SETTINGS.get(k)) for k in DEFAULT_SETTINGS}
    with _lock:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)


# ---------- 对话历史 ----------

def load_history() -> list:
    with _lock:
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []


def save_history(history: list):
    with _lock:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_FILE)


def append_history(role: str, content: str):
    """追加一条历史并保存"""
    history = load_history()
    history.append({"role": role, "content": content})
    # 保留最近 200 条
    if len(history) > 200:
        history = history[-200:]
    save_history(history)


def clear_history():
    save_history([])
