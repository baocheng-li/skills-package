#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录发现 —— 兼容壳。实现在 ``adapters.discover``（属 Adapter：只有它知道
WorkBuddy 的目录长什么样）。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.discover import *          # noqa: F401,F403
from adapters.discover import (          # noqa: F401
    TOOL_DIRS, discover_tools, main, summarize,
)

# SKILL.md 工作流第 1 步就是 `python3 -m hosts.commands.discover`，必须能独立执行。
# 只导出函数而不转 `__main__`，命令会静默退出——比报错更难发现。
if __name__ == "__main__":
    sys.exit(main())
