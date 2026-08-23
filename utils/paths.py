# -*- coding: utf-8 -*-
"""应用数据目录解析

- 源码运行：项目根目录（.env / settings.json / history.json 都在这里）
- PyInstaller 打包后（frozen）：exe 所在目录
  （避免配置被写进 _MEIPASS 临时解压目录，每次启动重置）
"""

import os
import sys


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = base_dir()
