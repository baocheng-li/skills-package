#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recon_daily —— 兼容壳（v3.0.0 分层重构后保留）。

实现在 ``core.calc.recon``：计算逻辑属于 Core，不属于这一层。
本文件只做转发，保证 ``import recon_daily`` 与 ``python3 -m hosts.commands.recon_daily`` 继续可用。
新代码请直接::

    from core.calc import recon

"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.calc.recon import *          # noqa: F401,F403
from core.calc.recon import (          # noqa: F401
    ROOT,
    DEFAULT_STATE,
    DEFAULT_INCOME,
    INCOME_TYPES,
    STATUS_TEXT,
    CONFIRMED_SET,
    PENDING_SET,
    VOID_SET,
    income_status,
    normalize_income,
    income_quality,
    load_income,
    income_from_ledger,
    build,
    script_text,
    schedule_command,
    main,
)

if __name__ == "__main__":
    if hasattr(sys.modules[__name__], "main"):
        sys.exit(main())
