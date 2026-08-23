# -*- coding: utf-8 -*-
"""基于 edge-tts 的 TTS 服务：流式 MP3 + WordBoundary 精确时间戳

- 输出 audio-24khz-48kbitrate-mono-mp3，用 miniaudio 解码为 PCM16 播放
- WordBoundary 给出每个词的精确时间（100ns 刻度），
  用于把屏幕标记对齐到"念到该处"的瞬间
- 支持 barge-in 打断；失败时回退到估算停留
"""

import logging
import threading

logger = logging.getLogger(__name__)


class EdgeTTSService:
    SAMPLE_RATE = 24000

    def __init__(self, config, audio_engine):
        self.config = config
        self.audio = audio_engine
        self.stop_event = threading.Event()

    def request_stop(self):
        """请求立即停止当前播放（barge-in）"""
        self.stop_event.set()

    def synthesize(self, text: str, marker_info=None, on_schedule=None) -> float:
        """合成并播放；返回实际播出秒数。

        marker_info: {"markers": [(kind, nums)...], "offsets": [字符偏移...], "fallback_holds": [...]}
        on_schedule(markers, holds, start_delay_ms): 拿到精确时间戳后回调
            （在开始播放前调用，由 orchestrator 转给 GUI）
        """
        self.stop_event.clear()
        if not text:
            return 0.0

        try:
            import edge_tts
            import miniaudio

            communicate = edge_tts.Communicate(
                text, self.config.TTS_EDGE_VOICE, boundary="WordBoundary"
            )

            # 先缓冲完整 MP3 流再解码：
            # websocket 块可能切断 MP3 帧边界，逐块 decode 会报错；
            # 完整缓冲后一次性解码最稳。
            mp3_data = bytearray()
            words = []  # (offset_ms, duration_ms, 词在纯文本中的起始位置, 词文本)
            pos = 0
            for chunk in communicate.stream_sync():
                if self.stop_event.is_set():
                    logger.info("⏹ 合成被打断")
                    return 0.0
                if chunk["type"] == "audio":
                    mp3_data += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    off_ms = chunk["offset"] / 10000
                    dur_ms = chunk["duration"] / 10000
                    wt = chunk["text"]
                    # 在纯文本里顺序定位该词（词按语音顺序=文本顺序出现；
                    # WordBoundary 不数标点，不能直接用累计字符对齐）
                    idx = text.find(wt, pos)
                    if idx < 0:
                        idx = pos
                    words.append((off_ms, dur_ms, idx, wt))
                    pos = idx + len(wt)

            if not mp3_data:
                logger.warning("⚠️ edge-tts 未收到音频")
                return 0.0

            dec = miniaudio.decode(
                bytes(mp3_data),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=self.SAMPLE_RATE,
            )
            samples = dec.samples
            pcm = samples.tobytes() if hasattr(samples, "tobytes") else bytes(samples)

            total_ms = (words[-1][0] + words[-1][1]) if words else len(text) * self.config.MS_PER_CHAR

            # ---- 弹幕：按标点分句，组装 (句首时刻, 该句朗读时长, 文本) ----
            danmaku_items = self._build_danmaku(text, words)

            # ---- 标记调度：字级时间戳 → 精确触发 ----
            holds = []
            start_delay = 0
            if marker_info:
                if words:
                    triggers = []
                    for off in marker_info["offsets"]:
                        # 第一个【完整文本位置】>= 标记偏移的词，即标记后面那句的第一个词
                        t = next((w[0] for w in words if w[2] >= off), total_ms)
                        triggers.append(t)
                    for i, t in enumerate(triggers):
                        if i == len(triggers) - 1:
                            holds.append(None)  # 最后一个挂到语音结束
                        else:
                            # 扣除下一个标记 0.8s 的动画时间
                            holds.append(max(1200, int(triggers[i + 1] - t - 800)))
                    start_delay = max(0, int(triggers[0] - 800))
                    logger.info("🎯 标记时间戳: triggers=%s", [int(t) for t in triggers])
                else:
                    # 没有词边界（异常情况）→ 用估算停留
                    holds = marker_info["fallback_holds"]

            if on_schedule:
                on_schedule(
                    marker_info["markers"] if marker_info else [],
                    holds,
                    start_delay,
                    words=danmaku_items,
                )

            # ---- 播放（100ms 小块写，随时可打断）----
            interrupted = False
            self.audio.reset_play_measure()
            step = self.SAMPLE_RATE * 2 // 10  # 0.1s
            for i in range(0, len(pcm), step):
                if self.stop_event.is_set():
                    interrupted = True
                    break
                self.audio.play(pcm[i:i + step])

            duration = self.audio.played_seconds
            if interrupted:
                logger.info("⏹ 语音被打断（已播 %.1fs）", duration)
            else:
                logger.info("✅ 播放完成 (%.1fs)", duration)
            return duration

        except Exception as e:
            logger.error("❌ edge-tts 合成失败: %s", e, exc_info=True)
            if marker_info and on_schedule:
                on_schedule(marker_info["markers"], marker_info["fallback_holds"], 0)
            return 0.0

    @staticmethod
    def _build_danmaku(text: str, words):
        """把词级时间戳按标点分句，组装弹幕：[(句首时刻ms, 该句朗读时长ms, 句子文本)]"""
        if not words:
            return []
        ends = [i for i, ch in enumerate(text) if ch in "。！？；!?;\n"]
        items = []
        sent_start = 0
        for end in ends + [len(text)]:
            sent_end = end + 1 if end < len(text) else end
            sent_text = text[sent_start:sent_end].strip()
            ws = [w for w in words if sent_start <= w[2] < end]
            if ws and sent_text:
                t0 = ws[0][0]
                t1 = max(w[0] + w[1] for w in ws)
                items.append((t0, t1 - t0, sent_text))
            sent_start = sent_end
        return items
