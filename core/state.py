# -*- coding: utf-8 -*-
"""全局状态管理

注意：State 实例由 Orchestrator 创建并持有，不要在这里导出单例。
"""

import queue
import threading
from typing import Dict, List, Any


class State:
    def __init__(self):
        self.is_recording = False
        self.is_ai_speaking = False
        self.recorded_audio: bytes = None  # 最近一次录音结果（供录音线程写入）
        self.record_thread: threading.Thread = None
        self.conversation_history: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.pending: "queue.Queue" = queue.Queue()  # 待处理的对话（串行执行）
