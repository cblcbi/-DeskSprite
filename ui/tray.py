# -*- coding: utf-8 -*-
"""系统托盘图标：右键菜单（打开设置 / 退出）

程序没有主窗口，关闭全靠托盘：图标常驻任务栏，右键退出。
回调运行在 pystray 线程，对 Tk 的操作一律转回主线程（root.after）。
"""

import logging
import threading

import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


def _make_icon_image() -> Image.Image:
    """程序图标：深色玻璃圆角底 + 红圈（与 exe 图标同款）"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(30, 30, 46, 255))
    for w, col in ((6, (255, 255, 255, 255)), (3, (255, 45, 85, 255))):
        d.ellipse([16, 16, 48, 48], outline=col, width=w)
    d.ellipse([28, 28, 36, 36], fill=(255, 45, 85, 255))
    return img


class TrayIcon:
    def __init__(self, on_quit, on_settings=None, title="桌灵 DeskSprite"):
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._icon = None
        self._thread = None
        menu = pystray.Menu(
            pystray.MenuItem("打开设置", self._open_settings, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        try:
            self._icon = pystray.Icon(
                "DeskSprite", _make_icon_image(), title, menu=menu,
            )
        except Exception as e:
            logger.warning("⚠️ 托盘图标初始化失败（不影响使用）: %s", e)

    def start(self):
        """后台线程运行托盘（不阻塞主线程）"""
        if self._icon is None:
            return
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("🖥 托盘图标已启动（右键退出程序）")

    def _open_settings(self, icon, item):
        if self._on_settings is not None:
            try:
                self._on_settings()
            except Exception as e:
                logger.error("托盘打开设置失败: %s", e)

    def _quit(self, icon, item):
        icon.stop()
        if self._on_quit is not None:
            try:
                self._on_quit()
            except Exception as e:
                logger.error("托盘退出回调失败: %s", e)
