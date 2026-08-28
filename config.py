# -*- coding: utf-8 -*-
"""应用配置：集中管理密钥、模型、参数（支持 .env 覆盖）"""

import os

from utils.paths import BASE_DIR

ENV_FILE = os.path.join(BASE_DIR, ".env")


def _load_env(path: str) -> dict:
    """极简 .env 解析器（KEY=VALUE，支持 # 注释），避免额外依赖"""
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    env[key] = value
    except FileNotFoundError:
        pass
    return env


_env = _load_env(ENV_FILE)

# 非密钥配置可被 settings.json 覆盖（设置浮窗写入）
try:
    from utils.persistence import load_settings
    _settings = load_settings()
except Exception:
    _settings = {}


def _get(key: str, default: str = "") -> str:
    """优先级：环境变量 > .env > settings.json > 默认值"""
    return os.environ.get(key, _env.get(key, _settings.get(key, default)))


class Config:
    # ---------- API 密钥（仅 .env / 环境变量，绝不进 settings.json） ----------
    GG_API_KEY = _get("GG_API_KEY", "")
    QWEN_API_KEY = _get("QWEN_API_KEY", "")

    # ---------- 模型配置 ----------
    GG_MODEL = _get("GG_MODEL", "gemini-3-flash-preview")
    GG_BASE_URL = _get("GG_BASE_URL", "https://api.openai.com/v1")
    TTS_MODEL = _get("TTS_MODEL", "qwen3-tts-flash")
    TTS_VOICE = _get("TTS_VOICE", "Bella")
    TTS_BACKEND = _get("TTS_BACKEND", "edge")  # edge | dashscope
    TTS_EDGE_VOICE = _get("TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")

    # 标记时序估算：每字毫秒（edge-tts 无词边界时的兜底）
    MS_PER_CHAR = int(_get("MS_PER_CHAR", "250"))

    # Token 限制（Gemini 思考 token 也占此额度，给足避免截断）
    MAX_TOKENS = int(_get("MAX_TOKENS", "8192"))

    # ---------- 音频配置 ----------
    RATE = int(_get("RATE", "24000"))
    CHUNK = int(_get("CHUNK", "960"))
    CHANNELS = int(_get("CHANNELS", "1"))

    # 音频质量检测
    MIN_AUDIO_ENERGY = int(_get("MIN_AUDIO_ENERGY", "100"))
    MIN_TEXT_LENGTH = int(_get("MIN_TEXT_LENGTH", "2"))
    MIN_RECORD_SECONDS = float(_get("MIN_RECORD_SECONDS", "0.3"))

    # ---------- Whisper ----------
    # 填模型大小名（small/base/medium...，首次自动从 HuggingFace 下载）或本地模型目录；
    # 相对路径（如 models/faster-whisper）相对应用目录（exe/项目所在目录）解析，
    # 方便把模型文件夹和 exe 打包在一起分发
    _whisper_raw = _get("WHISPER_MODEL_PATH", "small")
    if _whisper_raw and (("\\" in _whisper_raw) or ("/" in _whisper_raw)) \
            and not os.path.isabs(_whisper_raw):
        _whisper_raw = os.path.join(BASE_DIR, _whisper_raw)
    WHISPER_MODEL_PATH = _whisper_raw
    WHISPER_DEVICE = _get("WHISPER_DEVICE", "cuda")
    WHISPER_COMPUTE = _get("WHISPER_COMPUTE", "float16")

    # ---------- 对话 ----------
    MAX_HISTORY_TURNS = int(_get("MAX_HISTORY_TURNS", "6"))

    # ---------- 弹幕 ----------
    DANMAKU_ENABLED = _get("DANMAKU_ENABLED", "1") == "1"        # 语音气泡
    DANMAKU_FLY_ENABLED = _get("DANMAKU_FLY_ENABLED", "0") == "1"  # 飘屏弹幕（默认关）

    # 截图叠加百分比网格线，帮助 AI 精确定位（0 关闭）
    SCREEN_GRID_ENABLED = _get("SCREEN_GRID_ENABLED", "1") == "1"

    # ---------- 日志 ----------
    LOG_LEVEL = _get("LOG_LEVEL", "INFO")

    # 随机吐槽时间间隔（秒）
    MIN_ROAST_INTERVAL = int(_get("MIN_ROAST_INTERVAL", "30"))
    MAX_ROAST_INTERVAL = int(_get("MAX_ROAST_INTERVAL", "300"))

    # ---------- 快捷键（形如 "ctrl+alt+v"，空串=禁用） ----------
    HOTKEY_PTT = _get("HOTKEY_PTT", "v")            # 按住说话
    HOTKEY_CHAT = _get("HOTKEY_CHAT", "f2")         # 聊天输入框
    HOTKEY_SETTINGS = _get("HOTKEY_SETTINGS", "f3")  # 设置窗口
    HOTKEY_INTERRUPT = _get("HOTKEY_INTERRUPT", "")  # 打断语音
    HOTKEY_REGION = _get("HOTKEY_REGION", "")        # 框选截图（隐私模式）

    # ---------- AI 人设（default / genius / custom） ----------
    PERSONA = _get("PERSONA", "default")
    CUSTOM_SYSTEM_PROMPT = _get("CUSTOM_SYSTEM_PROMPT", "")


def _build_system_prompt() -> str:
    """SYSTEM_PROMPT = 功能基础指令 + 人设（按 Config.PERSONA 选择）"""
    from core.personas import BASE_INSTRUCTIONS, PERSONA_PRESETS
    persona = PERSONA_PRESETS.get(Config.PERSONA)
    if Config.PERSONA == "custom" and Config.CUSTOM_SYSTEM_PROMPT.strip():
        persona = Config.CUSTOM_SYSTEM_PROMPT.strip()
    if not persona:
        persona = PERSONA_PRESETS["default"]
    return BASE_INSTRUCTIONS + "\n\n" + persona


def get_system_prompt() -> str:
    """实时组装当前生效的人设：保存设置后下一次对话立即生效（热切换）"""
    return _build_system_prompt()


SYSTEM_PROMPT = _build_system_prompt()
