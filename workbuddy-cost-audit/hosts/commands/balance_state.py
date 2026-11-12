#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余额状态 —— 兼容壳（v3.0.0 分层重构后保留）。

拆成了两半：

    core/policy.py      判定：阈值、告警级别、凭证状态的纯语义
    adapters/state.py   存取：读哪个 JSON、原子写、白名单过滤

本文件把两边的公开名字重新导出，保持 ``bs.load()`` / ``bs.set_manual()`` /
``bs.level_of()`` 这些旧调用点继续可用。新代码请按用途分别 import。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.policy import (                # noqa: F401
    DEFAULT_THRESHOLDS, LEVEL_TEXT, LEVEL_ORDER, SNAPSHOT_STATE_TEXT,
    apply_manual, default_state, level_of, now, snapshot_state, thresholds_of,
)
from adapters.state import (             # noqa: F401
    DEFAULT_PATH, default_path, load, save, set_manual,
)
