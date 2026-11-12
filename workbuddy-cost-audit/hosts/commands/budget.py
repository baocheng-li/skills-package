#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""budget —— 兼容壳（v3.0.0 分层重构后保留）。

实现在 ``core.calc.budget``：计算逻辑属于 Core，不属于这一层。
本文件只做转发，保证 ``import budget`` 与 ``python3 -m hosts.commands.budget`` 继续可用。
新代码请直接::

    from core.calc import budget

"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.calc.budget import *          # noqa: F401,F403
from core.calc.budget import (          # noqa: F401
    TYPE_INCOME,
    TYPE_CONSUME,
    VALID_TYPES,
    SOURCE_LABEL,
    INITIAL_SOURCE_TEXT,
    load_ledger,
    compute,
    fmt_num,
    usage_get,
)

if __name__ == "__main__":
    if hasattr(sys.modules[__name__], "main"):
        sys.exit(main())
