# -*- coding: utf-8 -*-
"""截图工具"""

import base64
import ctypes
from io import BytesIO

import pyautogui


def ensure_dpi_aware():
    """让截图和指示层使用物理像素，避免高 DPI 缩放下坐标偏移"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_size():
    """返回主屏 (宽, 高) 物理像素"""
    size = pyautogui.size()
    return size.width, size.height


def _overlay_grid(img):
    """叠加浅灰百分比网格线（每 10% 一条 + 边缘刻度数字），辅助 AI 精确定位。

    刻度与发送给 AI 的坐标约定一致：0,0 在左上角，100,100 在右下角。
    """
    from PIL import Image, ImageDraw, ImageFont

    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    line = (128, 128, 128, 80)
    for i in range(1, 10):
        x = int(i * w / 10)
        d.line([(x, 0), (x, h)], fill=line, width=1)
    for j in range(1, 10):
        y = int(j * h / 10)
        d.line([(0, y), (w, y)], fill=line, width=1)
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
    label = (128, 128, 128, 190)
    for v in range(0, 101, 20):
        d.text((int(v / 100 * w) + 3, 3), str(v), fill=label, font=font)
        d.text((3, int(v / 100 * h) + 3), str(v), fill=label, font=font)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def capture_screen(max_side: int = 1920, quality: int = 75, grid: bool = None) -> str:
    """截取【主屏】并返回 base64 编码的 JPEG（按最长边等比缩放）

    只截主屏，避免多显示器下截图与指示层坐标系不一致。
    grid=True 时叠加百分比网格线（默认跟随 Config.SCREEN_GRID_ENABLED）。
    """
    w, h = screen_size()
    screenshot = pyautogui.screenshot(region=(0, 0, w, h))
    # thumbnail 保持宽高比，按最长边缩放
    screenshot.thumbnail((max_side, max_side))
    if grid is None:
        try:
            from config import Config
            grid = Config.SCREEN_GRID_ENABLED
        except Exception:
            grid = False
    if grid:
        screenshot = _overlay_grid(screenshot)
    buffer = BytesIO()
    screenshot.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode()
