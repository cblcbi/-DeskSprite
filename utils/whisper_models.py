# -*- coding: utf-8 -*-
"""Whisper 模型来源解析（纯文件逻辑，不依赖 faster_whisper 库）

- 模型名（small/base/...）→ 交给 faster-whisper 联网下载
- 本地目录（含 model.bin）→ 直接用，可检测档位标签
- auto → 自动发现应用目录（models/ 或 exe 同目录）下的模型，没有则 small
"""

import glob
import json
import logging
import os

from utils.paths import BASE_DIR

logger = logging.getLogger(__name__)

# 常见官方模型名（这些值交给 faster-whisper 处理，本地不存在时联网下载）
MODEL_NAMES = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "turbo", "distil-large-v2", "distil-large-v3",
}


def is_model_name(path: str) -> bool:
    """纯模型名（无路径分隔符且不在磁盘上）→ 交给 faster-whisper 联网下载"""
    if not path or ("/" in path) or ("\\" in path):
        return False
    if os.path.isdir(path):
        return False
    return path.lower() in MODEL_NAMES


def is_local_model_dir(path: str) -> bool:
    """本地模型目录：存在且含 model.bin"""
    return bool(path) and os.path.isdir(path) and os.path.isfile(os.path.join(path, "model.bin"))


def discover_local_models():
    """自动发现应用目录下的本地模型（model.bin 大的优先=更精准）：
    1. BASE_DIR/models/*    （约定目录）
    2. BASE_DIR/*           （exe 同目录下的模型文件夹，默认自动检测）"""
    found = []
    for base in (os.path.join(BASE_DIR, "models"), BASE_DIR):
        found.extend(glob.glob(os.path.join(base, "*", "model.bin")))
    found.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return [os.path.dirname(p) for p in found]


def detect_model_label(path: str):
    """检测本地模型目录的档位标签（large-v3 / medium / small ...），无效路径返回 None"""
    if not is_local_model_dir(path):
        return None
    size_mb = os.path.getsize(os.path.join(path, "model.bin")) / (1024 * 1024)
    if size_mb > 2500:
        # large 档：用 alignment_heads 区分 v2(20) / v3(10)
        try:
            cfg = json.load(open(os.path.join(path, "config.json"), encoding="utf-8"))
            if len(cfg.get("alignment_heads", [])) <= 10:
                return "large-v3"
        except Exception:
            pass
        return "large-v2"
    if size_mb > 1000:
        return "medium"
    if size_mb > 300:
        return "small"
    if size_mb > 100:
        return "base"
    return "tiny"


def resolve_model_source(config):
    """模型来源解析：配置路径有效优先；否则按模型选择（auto=自动发现→small）

    返回实际传给 faster-whisper 的路径/模型名。"""
    path = getattr(config, "WHISPER_MODEL_PATH", "") or ""
    model = getattr(config, "WHISPER_MODEL", "auto") or "auto"

    if is_local_model_dir(path):
        logger.info("📁 使用配置的本地模型: %s（检测为 %s）", path, detect_model_label(path))
        return path
    if is_model_name(path):
        # 旧式配置：路径字段直接填了模型名
        logger.info("📁 使用模型名: %s（本地不存在时自动下载）", path)
        return path
    if path:
        logger.warning("⚠️ 配置的模型路径无效: %s，改按模型选择处理", path)

    if model == "auto":
        discovered = discover_local_models()
        if discovered:
            logger.info("🔍 自动检测到本地模型: %s（%s）",
                        discovered[0], detect_model_label(discovered[0]))
            return discovered[0]
        logger.info("📁 未发现本地模型，按 small 联网下载")
        return "small"
    if model in MODEL_NAMES:
        logger.info("📁 使用模型名: %s（本地不存在时自动下载）", model)
        return model
    logger.warning("⚠️ 未知模型选择 %r，回退 small", model)
    return "small"
