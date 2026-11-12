#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare —— 兼容壳（v3.0.0 分层重构后保留）。

实现在 ``core.calc.compare``：计算逻辑属于 Core，不属于这一层。
本文件只做转发，保证 ``import compare`` 与 ``python3 -m hosts.commands.compare`` 继续可用。
新代码请直接::

    from core.calc import compare

"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.calc.compare import *          # noqa: F401,F403
from core.calc.compare import (          # noqa: F401
    MIN_SAMPLE,
    build_bench,
    build_recommendations,
    build,
)

if __name__ == "__main__":
    if hasattr(sys.modules[__name__], "main"):
        sys.exit(main())
