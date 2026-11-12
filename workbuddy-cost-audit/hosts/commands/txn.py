#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""txn —— 兼容壳（v3.0.0 分层重构后保留）。

实现在 ``core.calc.txn``：计算逻辑属于 Core，不属于这一层。
本文件只做转发，保证 ``import txn`` 与 ``python3 -m hosts.commands.txn`` 继续可用。
新代码请直接::

    from core.calc import txn

"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.calc.txn import *          # noqa: F401,F403
from core.calc.txn import (          # noqa: F401
    ROOT,
    DEFAULT_PATH,
    TXN_LEN,
    DATE_LEN,
    NAME_LEN,
    SEQ_LEN,
    SEQ_MAX,
    SEQ36_HEAD,
    SEQ36_TAIL,
    SEQ36_MAX,
    ASCII_LETTERS,
    TXN_RE,
    INCOME_ABBR,
    INCOME_ABBR_CN,
    income6,
    name6,
    seq_code,
    seq_index,
    build_txn,
    date8_of,
    decode,
    default_path,
    Registry,
    verify,
    stats,
    main,
)

if __name__ == "__main__":
    if hasattr(sys.modules[__name__], "main"):
        sys.exit(main())
