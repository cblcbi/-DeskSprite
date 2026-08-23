# -*- coding: utf-8 -*-
"""封装 OpenAI/Gemini 请求（含自动重试）"""

import logging
import re
import time
from typing import List, Dict

from openai import OpenAI

logger = logging.getLogger(__name__)


def _is_retryable_error(error_msg: str):
    """判断错误是否可重试，返回 (是否可重试, 建议等待秒数)"""
    lower = error_msg.lower()
    retryable = any(k in lower for k in (
        "502", "500", "503", "504", "429", "retryable",
        "bad gateway", "bad_gateway", "origin web server", "origin is overloaded",
        "connection", "timed out", "timeout",
    ))
    m = re.search(r"retry_after[\"']?\s*[:=]\s*(\d+)", error_msg)
    retry_after = float(m.group(1)) if m else 0.0
    return retryable, retry_after


class LLMClient:
    def __init__(self, config, max_retries: int = 3):
        self.config = config
        self.max_retries = max_retries
        self.client = OpenAI(
            api_key=config.GG_API_KEY,
            base_url=config.GG_BASE_URL,
        )

    def get_response(self, messages: List[Dict]) -> str:
        """请求对话模型（流式），返回纯文本内容；可重试错误自动退避重试"""
        for attempt in range(self.max_retries + 1):
            try:
                stream = self.client.chat.completions.create(
                    model=self.config.GG_MODEL,
                    messages=messages,
                    max_tokens=self.config.MAX_TOKENS,
                    stream=True,
                )

                chunks = []
                first_token = True
                for event in stream:
                    if not event.choices:
                        continue
                    delta = event.choices[0].delta
                    piece = getattr(delta, "content", None)
                    if piece:
                        if first_token:
                            logger.info("✍ 开始接收…")
                            first_token = False
                        chunks.append(piece)

                text = "".join(chunks)
                logger.debug("raw response:\n%s", text)
                return text.strip()

            except Exception as e:
                error_msg = str(e)
                retryable, retry_after = _is_retryable_error(error_msg)

                if retryable and attempt < self.max_retries:
                    wait = retry_after or min(5 * 2 ** attempt, 30)
                    logger.warning("⚠️ 请求失败（%d/%d），%.0fs 后自动重试...",
                                   attempt + 1, self.max_retries, wait)
                    time.sleep(wait)
                    continue

                return self._fallback_message(error_msg)

        return "抱歉，我遇到了一点问题，请稍后再试～"

    @staticmethod
    def _fallback_message(error_msg: str) -> str:
        logger.error("❌ AI 请求失败: %s", error_msg)

        if "api_key_missing" in error_msg.lower() or "401" in error_msg:
            return "还没有配置 API 密钥，去 .env 里填上你的密钥吧～"
        elif "rate_limit" in error_msg.lower():
            return "说太快啦，让我缓缓～"
        elif "authentication" in error_msg.lower():
            return "API Key 好像有问题，让流苏检查一下～"
        else:
            return "抱歉，我遇到了一点问题，请稍后再试～"
