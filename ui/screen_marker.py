# -*- coding: utf-8 -*-
"""屏幕画笔组件：透明置顶 Toplevel，绘制指示标记（点/圈/框/箭头）

行为：
- 多个标记排队链式播放（不会互相打断动画）
- 标记展开后停留一会儿再挪开，像人指完东西自然收回
- 空闲时在屏幕范围内随机游走（不走 AI，纯随机）

注意：所有方法必须在 Tk 主线程中调用（由 GUIManager 统一调度）。
"""

import ctypes
import logging
import math
import random
import re
import time
import tkinter as tk
from typing import Callable, List, Optional, Tuple

import pyautogui

_KIND_ALIAS = {
    "点": "point", "point": "point", "dot": "point", "spot": "point",
    "圈": "circle", "circle": "circle", "ring": "circle",
    "框": "rect", "box": "box", "rect": "rect", "rectangle": "rect", "方形": "rect",
    "箭头": "arrow", "arrow": "arrow", "pointer": "arrow",
}

MAX_MARKERS = 10  # 一次回复最多展示的标记数（多余的只剥离原文，不圈）


def parse_markers(text: str):
    """从回复中提取 [[指示:类型:x,y,...]] 标记。

    返回 (纯文本, 标记列表, 每个标记在纯文本中的字符偏移, 估算停留毫秒列表)
    - 字符偏移：标记剔除后，该标记【之后正文】开始处的字符 index，
      用于对齐 edge-tts 的 WordBoundary 时间戳
    - 估算停留：该标记到下一个标记之间那段文本的估算时长（无时间戳时兜底）
    """
    if not text:
        return text or "", [], [], []
    pattern = re.compile(
        r"\[\[\s*(?:指示|marker)\s*:\s*([^:\]\[\s]+)\s*:\s*([0-9.,\s\-]+?)\s*\]\]",
        re.IGNORECASE,
    )
    markers = []
    offsets = []
    clean_parts = []
    last_end = 0
    for m in pattern.finditer(text):
        kind = _KIND_ALIAS.get(m.group(1).strip().lower())
        if kind is None:
            continue
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", m.group(2))]
        if (kind == "point" and len(nums) < 2) or (kind == "circle" and len(nums) < 3) \
                or (kind in ("rect", "arrow") and len(nums) < 4):
            continue
        clean_parts.append(text[last_end:m.start()])
        last_end = m.end()
        if len(markers) >= MAX_MARKERS:
            # 超过上限：只剥离标记原文，不再记录（保证时间轴与显示一致）
            continue
        markers.append((kind, nums))
        offsets.append(sum(len(p) for p in clean_parts))
    clean_parts.append(text[last_end:])
    clean = "".join(clean_parts)

    from config import Config
    holds = []
    for i, off in enumerate(offsets):
        seg_end = offsets[i + 1] if i + 1 < len(offsets) else len(clean)
        ms = int((seg_end - off) * Config.MS_PER_CHAR)
        holds.append(max(800, min(30000, ms)))

    return clean, markers, offsets, holds


class ScreenMarker:
    """全屏透明置顶窗口，光标滑过去 -> 标记弹性展开 -> 挪到下角 -> 闲时晃动"""

    COLOR = "#FF2D55"
    GLOW = "#FFFFFF"
    FRAME_MS = 33
    GLIDE_MS = 500
    EXPAND_MS = 300
    HOLD_MS = 2500  # 保留：普通标记最短停留
    MIN_HOLD_MS = 2000  # release 也不能早于这个时间收手（保证看得见）
    IDLE_MIN_S = 5
    IDLE_MAX_S = 20
    NUDGE_RATIO = 0.15  # 指示后挪开的距离（相对屏幕宽）

    def __init__(self, root: tk.Tk):
        self.width, self.height = pyautogui.size()

        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-transparentcolor", "#010203")
        top.geometry(f"{self.width}x{self.height}+0+0")
        canvas = tk.Canvas(
            top, width=self.width, height=self.height,
            bg="#010203", highlightthickness=0, bd=0,
        )
        canvas.pack()
        top.update_idletasks()
        top.update()

        # 点击穿透 + 不抢焦点 + 不进任务栏
        hwnd = ctypes.windll.user32.GetParent(top.winfo_id()) or top.winfo_id()
        GWL_EXSTYLE = -20
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            ex | 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080,
        )

        self._root = top
        self._canvas = canvas

        # 状态机
        self._pos: Tuple[float, float] = (0.10 * self.width, 0.90 * self.height)  # 起始：左下角
        self._pending: List[tuple] = []
        self._animating = False
        self._idle_after: Optional[str] = None
        self._start_after: Optional[str] = None
        self._waiting_hold = False  # 正在挂起等 release
        self._release_requested = False
        self._current_hold = False  # 当前标记是否要挂起
        self._placed_at = 0.0  # 当前标记展开完成的时间戳
        self._pos_cb: Optional[Callable[[float, float], None]] = None  # 标记落点回调
        self._state = "idle"            # idle / recording / thinking
        self._state_phase = 0
        self._state_after: Optional[str] = None

    def set_position_callback(self, cb: Callable[[float, float], None]):
        """注册回调：每个标记展开落定时调用 cb(x, y)（屏幕像素），
        供说话气泡锚定到指示右上角"""
        self._pos_cb = cb

    def current_pos(self):
        """当前光标位置（屏幕像素），供气泡起步贴住指示"""
        return self._pos

    def start_idle(self):
        """启动闲时散步：程序启动就出来溜溜（指示完也会自动续上）"""
        self._root.after(1200, self._idle_wiggle)

    def set_state(self, state: str):
        """切换指示状态：idle（散步圆点）/ recording（录音方块脉冲）/
        thinking（等待回复加载圈）。非 idle 时暂停散步，恢复 idle 后自动续上。"""
        if state not in ("idle", "recording", "thinking"):
            state = "idle"
        if state == self._state:
            return
        self._state = state
        self._cancel_state_anim()
        self._canvas.delete("state")  # 清掉旧状态图标，防止残留
        if state == "idle":
            if not self._animating and not self._pending:
                self._start_idle()
        else:
            self._cancel_idle()
            self._state_phase = 0
            self._state_after = self._root.after(self.FRAME_MS, self._state_tick)

    # ========================================================
    # 对外接口
    # ========================================================

    def show_batch(self, items, start_delay_ms: int = 0):
        """显示一批标记（仅主线程调用），依次排队播放。

        items: [(kind, nums, hold_ms)]，hold_ms=None 表示挂起等 release()。
        start_delay_ms: 从此刻起延迟多少毫秒再开始播放第一个标记
        （用于对齐语音时间轴）。
        """
        self._sync_size()
        self._cancel_idle()
        self._cancel_start()
        was_waiting = self._waiting_hold
        self._waiting_hold = False
        self._release_requested = False
        for kind, nums, hold in items:
            self._pending.append((kind, list(nums), hold))
        if was_waiting:
            self._play_next()
        elif not self._animating:
            if start_delay_ms > 0:
                self._start_after = self._root.after(int(start_delay_ms), self._play_next)
            else:
                self._play_next()

    def release(self):
        """语音播完，让当前挂起的标记收手（仅主线程调用）"""
        self._release_requested = True
        if self._waiting_hold:
            self._waiting_hold = False
            remain = (self._placed_at + self.MIN_HOLD_MS / 1000.0) - time.time()
            if remain > 0:
                self._root.after(int(remain * 1000), self._after_hold)
            else:
                self._after_hold()

    def sync_size(self):
        """把标记层窗口/画布尺寸与当前屏幕对齐（运行中分辨率或缩放变化时调用）"""
        try:
            w, h = pyautogui.size()
            w, h = int(w), int(h)
        except Exception:
            return
        if (w, h) == (self.width, self.height):
            return
        logger = logging.getLogger(__name__)
        logger.warning("⚠️ 屏幕尺寸变化 %dx%d -> %dx%d，标记层已同步",
                       self.width, self.height, w, h)
        self.width, self.height = w, h
        self._root.geometry(f"{w}x{h}+0+0")
        self._canvas.configure(width=w, height=h)
        self._pos = (
            min(self._pos[0], 0.98 * w),
            min(self._pos[1], 0.98 * h),
        )

    def _sync_size(self):
        """show_batch 前轻量同步（只在尺寸变化时有开销）"""
        self.sync_size()

    # ========================================================
    # 状态机：标记 / 挂起 / 漂移 / 闲时晃动
    # ========================================================

    def _play_next(self):
        """播放队列里的下一个标记；队列空则进入漂移→闲时"""
        if self._pending:
            self._animating = True
            kind, nums, hold = self._pending.pop(0)
            self._current_hold = hold
            self._draw(kind, nums)
        else:
            self._animating = False
            self._drift_to_rest()

    def _on_marker_done(self, pos: Tuple[float, float]):
        """单个标记动画完成：按各自停留时长停留，或挂起等 release"""
        self._pos = pos
        self._placed_at = time.time()
        self._waiting_hold = False
        hold = self._current_hold
        if hold is None:
            # 最后一个：挂起等语音播完（release）再收手
            if self._release_requested:
                self._after_hold()
            else:
                self._waiting_hold = True
        else:
            # 中间标记：按估算停留时长停留，然后走下一个
            self._root.after(int(hold), self._after_hold)

    def _after_hold(self):
        """收手：清掉图形，进入下一步"""
        self._canvas.delete("shape")
        if self._pending:
            self._play_next()
        else:
            self._drift_to_rest()

    def _drift_to_rest(self):
        """指示完后，光标从目标点往左下/右下方向挪开一小段"""
        self._animating = True
        target = self._nudge_target()

        def on_done():
            self._pos = target
            self._animating = False
            if self._pending:
                self._play_next()
            else:
                self._start_idle()

        self._glide_pointer_only(target, on_done)

    def _nudge_target(self) -> Tuple[float, float]:
        """从当前位置往左下或右下方向挪开一小段（像手指点完自然收回）"""
        x, y = self._pos
        nudge = self.NUDGE_RATIO * self.width
        # 往左下或右下随机选一个方向
        if random.random() < 0.5:
            tx = x - nudge
        else:
            tx = x + nudge
        ty = y + nudge * 0.7
        # 加轻微随机
        tx += random.uniform(-0.005, 0.005) * self.width
        ty += random.uniform(-0.005, 0.005) * self.height
        # 钳到屏幕内
        tx = max(0.02 * self.width, min(0.98 * self.width, tx))
        ty = max(0.02 * self.height, min(0.98 * self.height, ty))
        return tx, ty

    def _start_idle(self):
        """安排一次闲时随机晃动"""
        self._cancel_idle()
        delay = random.randint(self.IDLE_MIN_S, self.IDLE_MAX_S) * 1000
        self._idle_after = self._root.after(delay, self._idle_wiggle)

    def _cancel_idle(self):
        if self._idle_after is not None:
            try:
                self._root.after_cancel(self._idle_after)
            except Exception:
                pass
            self._idle_after = None

    def _cancel_start(self):
        if self._start_after is not None:
            try:
                self._root.after_cancel(self._start_after)
            except Exception:
                pass
            self._start_after = None

    def _idle_wiggle(self):
        """闲时光标在屏幕范围内随机游走"""
        self._idle_after = None
        if self._animating or self._pending:
            return

        # 屏幕范围内随机选一个点（留点边距）
        margin = 0.05
        tx = random.uniform(margin, 1 - margin) * self.width
        ty = random.uniform(margin, 1 - margin) * self.height

        self._animating = True

        def on_done():
            self._pos = (tx, ty)
            self._animating = False
            if self._pending:
                self._play_next()
            else:
                self._start_idle()

        self._glide_pointer_only((tx, ty), on_done)

    # ========================================================
    # 状态图标（录音方块脉冲 / 等待回复加载圈）
    # ========================================================

    def _state_tick(self):
        self._state_after = None
        if self._state == "idle":
            return
        self._state_phase += 1
        self._draw_state_icon(self._pos[0], self._pos[1])
        self._state_after = self._root.after(self.FRAME_MS, self._state_tick)

    def _cancel_state_anim(self):
        if self._state_after is not None:
            try:
                self._root.after_cancel(self._state_after)
            except Exception:
                pass
            self._state_after = None

    def _draw_state_icon(self, x: float, y: float):
        """在光标位置画状态图标：recording=录音方块脉冲，thinking=旋转加载圈"""
        c = self._canvas
        c.delete("state")
        if self._state == "recording":
            # 红色方块 + 白色光晕，呼吸脉动
            r = 9 + 2 * math.sin(self._state_phase * 0.18)
            c.create_oval(x - r - 7, y - r - 7, x + r + 7, y + r + 7,
                          outline=self.GLOW, width=3, tags="state")
            c.create_rectangle(x - r, y - r, x + r, y + r,
                               fill=self.COLOR, outline="", tags="state")
        elif self._state == "thinking":
            # 旋转的弧（spinner）
            start = (self._state_phase * 12) % 360
            c.create_arc(x - 15, y - 15, x + 15, y + 15,
                         start=start, extent=220, style=tk.ARC,
                         outline=self.COLOR, width=4, tags="state")
            c.create_oval(x - 3, y - 3, x + 3, y + 3,
                          fill=self.COLOR, outline="", tags="state")

    # ========================================================
    # 绘制
    # ========================================================

    def _px(self, x: float, y: float, px_mode: bool = False):
        """坐标 -> 屏幕像素。

        px_mode=True：整组判定为像素坐标（AI 误写），换算回百分比再画。
        px_mode=False：百分比坐标，clamp 到 0~100 防飞出屏幕。"""
        if px_mode:
            x = max(0.0, x) / self.width * 100.0
            y = max(0.0, y) / self.height * 100.0
        else:
            x = max(0.0, min(100.0, x))
            y = max(0.0, min(100.0, y))
        return x / 100.0 * self.width, y / 100.0 * self.height

    @staticmethod
    def _clamp_pct(v: float) -> float:
        return max(0.0, min(100.0, v))

    def _draw(self, kind: str, nums: List[float]):
        """开始一个标记：清场 -> 光标滑过去 -> 标记展开"""
        self._canvas.delete("shape")
        self._canvas.delete("pointer")

        # 像素/百分比判别：任一坐标 > 100 视为 AI 误写的像素坐标，整组换算
        px_mode = any(v > 100 for v in nums)

        if kind == "point" and len(nums) >= 2:
            x, y = self._px(nums[0], nums[1], px_mode)
            self._glide_pointer((x, y), kind, (x, y))
        elif kind == "circle" and len(nums) >= 3:
            x, y = self._px(nums[0], nums[1], px_mode)
            # 半径：百分比（或像素）换算 + clamp 到屏幕短边一半
            if px_mode:
                r = max(0.0, nums[2])
            else:
                r = max(0.0, min(100.0, nums[2])) / 100.0 * self.width
            r = min(r, min(self.width, self.height) / 2)
            self._glide_pointer((x, y), kind, (x, y, r))
        elif kind == "rect" and len(nums) >= 4:
            # 两角坐标（像素/百分比）+ 排序（AI 可能给反或超界）
            x1, y1 = self._px(nums[0], nums[1], px_mode)
            x2, y2 = self._px(nums[2], nums[3], px_mode)
            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1
            self._glide_pointer(((x1 + x2) / 2, (y1 + y2) / 2), kind, (x1, y1, x2, y2))
        elif kind == "arrow" and len(nums) >= 4:
            x1, y1 = self._px(nums[0], nums[1], px_mode)
            x2, y2 = self._px(nums[2], nums[3], px_mode)
            self._glide_pointer((x1, y1), kind, (x1, y1, x2, y2))

    def _glide_pointer(self, target: tuple, kind: str, data: tuple):
        """光标从当前位置滑向目标，到达后展开标记"""
        c = self._canvas
        start = self._pos
        steps = max(1, int(self.GLIDE_MS / self.FRAME_MS))
        px0, py0 = start
        tx, ty = target

        def frame(i):
            if i > steps:
                self._draw_marker(kind, data)
                return
            t = i / steps
            e = 1 - (1 - t) ** 3  # ease-out
            x = px0 + (tx - px0) * e
            y = py0 + (ty - py0) * e
            dist = max(abs(tx - x), abs(ty - y), 1.0)
            dx = (tx - x) / dist
            dy = (ty - y) / dist
            self._draw_pointer(x, y, dx, dy)
            self._root.after(self.FRAME_MS, lambda: frame(i + 1))

        frame(0)

    def _glide_pointer_only(self, target: tuple, on_done: Callable[[], None]):
        """只移动光标（不画标记），到达后回调 on_done"""
        c = self._canvas
        start = self._pos
        steps = max(1, int(self.GLIDE_MS / self.FRAME_MS))
        px0, py0 = start
        tx, ty = target

        def frame(i):
            if i > steps:
                on_done()
                return
            t = i / steps
            e = 1 - (1 - t) ** 3
            x = px0 + (tx - px0) * e
            y = py0 + (ty - py0) * e
            dist = max(abs(tx - x), abs(ty - y), 1.0)
            dx = (tx - x) / dist
            dy = (ty - y) / dist
            self._draw_pointer(x, y, dx, dy)
            self._root.after(self.FRAME_MS, lambda: frame(i + 1))

        frame(0)

    def _draw_pointer(self, x: float, y: float, dx: float, dy: float):
        """绘制一帧光标（圆点 + 拖尾），并实时回调位置——气泡贴住指示走"""
        c = self._canvas
        c.delete("pointer")
        # 拖尾：白色光晕打底 + 红色主线，沿运动方向
        c.create_line(x - dx * 40, y - dy * 40, x, y,
                      fill=self.GLOW, width=9, tags="pointer")
        c.create_line(x - dx * 36, y - dy * 36, x, y,
                      fill=self.COLOR, width=4, tags="pointer")
        r = 9
        c.create_oval(x - r, y - r, x + r, y + r,
                      outline=self.GLOW, width=3, tags="pointer")
        c.create_oval(x - r, y - r, x + r, y + r,
                      outline=self.COLOR, width=2, tags="pointer")
        c.create_oval(x - 3, y - 3, x + 3, y + 3,
                      fill=self.COLOR, outline="", tags="pointer")
        if self._pos_cb is not None:
            try:
                self._pos_cb(x, y)
            except Exception:
                pass

    def _draw_marker(self, kind: str, data: tuple):
        """标记带弹性展开动画（back ease）；完成后回调状态机"""
        c = self._canvas
        c.delete("pointer")  # 展开期间隐藏光标圆点
        steps = max(1, int(self.EXPAND_MS / self.FRAME_MS))
        c1, c3 = 1.70158, 2.70158  # back ease 系数

        def back(t):
            return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2

        def frame(i):
            if i > steps:
                # 动画完成：算出落点，交给状态机
                if kind in ("point", "circle"):
                    pos = (data[0], data[1])
                elif kind == "rect":
                    pos = ((data[0] + data[2]) / 2, (data[1] + data[3]) / 2)
                elif kind == "arrow":
                    pos = (data[2], data[3])
                else:
                    pos = self._pos
                self._on_marker_done(pos)
                return
            t = i / steps
            s = back(t)
            c.delete("shape")
            glow, color = self.GLOW, self.COLOR

            if kind == "point":
                x, y = data
                r = 16 * max(s, 0.02)
                for outline, wd in ((glow, 7), (color, 3)):
                    c.create_oval(x - r, y - r, x + r, y + r, outline=outline, width=wd, tags="shape")
                c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline="", tags="shape")
            elif kind == "circle":
                x, y, r0 = data
                r = r0 * max(s, 0.02)
                for outline, wd in ((glow, 7), (color, 3)):
                    c.create_oval(x - r, y - r, x + r, y + r, outline=outline, width=wd, tags="shape")
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="", tags="shape")
            elif kind == "rect":
                x1, y1, x2, y2 = data
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                w, h = x2 - x1, y2 - y1
                for outline, wd in ((glow, 7), (color, 3)):
                    c.create_rectangle(cx - w * s / 2, cy - h * s / 2,
                                       cx + w * s / 2, cy + h * s / 2,
                                       outline=outline, width=wd, dash=(8, 5), tags="shape")
            elif kind == "arrow":
                x1, y1, x2, y2 = data
                for outline, wd in ((glow, 9), (color, 4)):
                    c.create_line(x1, y1, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                                  fill=outline, width=wd, arrow=tk.LAST, tags="shape")

            self._root.after(self.FRAME_MS, lambda: frame(i + 1))

        frame(0)
