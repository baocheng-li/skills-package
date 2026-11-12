#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 HTML 渲染（兼容壳）。

真正的实现在 ``renderer/html.py``：主题令牌、3D 图标、数据压缩、模板拼装
都在 Renderer 层完成，本文件只负责把旧调用点（audit.py / refresh_report.py /
tests）转发过去，保证 ``from render_report import build`` 这类**导入级**调用
继续可用。

注意：本壳**不提供命令行入口**——``build(data, skin, check_syntax)`` 的第一个
参数是内存里的数据模型，不是文件路径，硬造一个 CLI 就得先定义一套磁盘上并不
存在的中间格式。要出报告请用 ``hosts/commands/audit.py`` 或 ``python -m hosts.cli``。

保留这个壳而不是直接改调用点，是因为「Render 入口只有一处」比「少一个文件」
重要——将来换渲染器（比如出 PDF）只改这里的一行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from renderer.html import build, render  # noqa: E402,F401
from renderer.payload import compact     # noqa: E402,F401

__all__ = ["render", "build", "compact"]
