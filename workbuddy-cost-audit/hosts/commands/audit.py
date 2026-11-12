#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口（兼容壳）。

实现在 ``hosts/cli.py``：argparse、交互授权、控制台摘要、退出码、审计日志
都在那一层；业务计算在 ``core.pipeline``；产物格式在 ``renderer``。

保留本文件是为了不破坏既有调用点：
    python3 -m hosts.commands.audit --format html --out out --yes
    python3 -m hosts.commands.refresh_report ...        （内部 import audit）
以及定时任务里已经登记的命令行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hosts.cli import build_parser, main  # noqa: E402,F401

__all__ = ["main", "build_parser"]

if __name__ == "__main__":
    sys.exit(main())
