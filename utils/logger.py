# -*- coding: utf-8 -*-
"""日志初始化工具

用法：
    from utils.logger import setup_logging
    setup_logging()   # 在 main() 最开头调用一次

各模块内：
    import logging
    logger = logging.getLogger(__name__)
"""

import io
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_TIME_FORMAT = "%H:%M:%S"


def _ensure_utf8_streams():
    """确保控制台输出用 UTF-8 且出错不崩溃（emoji/中文兼容）"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        if getattr(stream, "encoding", "").lower() in ("utf-8", "utf8"):
            continue  # 已是 UTF-8，避免重复包装
        try:
            wrapper = io.TextIOWrapper(
                stream.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )
            setattr(sys, stream_name, wrapper)
        except Exception:
            pass


def setup_logging(level: int = logging.INFO):
    """配置全局日志：控制台 + 滚动文件（可重复调用，幂等）"""
    _ensure_utf8_streams()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = None

    root = logging.getLogger()
    root.setLevel(level)

    # 清掉可能重复的 handler，保证幂等
    for handler in list(root.handlers):
        if getattr(handler, "_ai_pet_handler", False):
            root.removeHandler(handler)
            handler.close()

    fmt = logging.Formatter(_FORMAT, datefmt=_TIME_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console._ai_pet_handler = True
    root.addHandler(console)

    if log_dir:
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler._ai_pet_handler = True
        root.addHandler(file_handler)

    # 第三方库日志太吵，压掉
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "ctranslate2",
                  "asyncio", "edge_tts", "miniaudio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
