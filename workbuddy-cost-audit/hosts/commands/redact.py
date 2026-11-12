#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""redact —— 兼容壳（v3.0.0 分层重构后保留）。

实现在 ``core.calc.redact``：计算逻辑属于 Core，不属于这一层。
本文件只做转发，保证 ``import redact`` 与 ``python3 -m hosts.commands.redact`` 继续可用。
新代码请直接::

    from core.calc import redact

"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.calc.redact import *          # noqa: F401,F403
from core.calc.redact import (          # noqa: F401
    PATTERNS,
    scrub_text,
    scrub_data,
)

if __name__ == "__main__":
    if hasattr(sys.modules[__name__], "main"):
        sys.exit(main())
