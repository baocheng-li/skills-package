#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志解析调度 —— 兼容壳。实现在 ``adapters.parse_logs``。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.parse_logs import *        # noqa: F401,F403
from adapters.parse_logs import ADAPTERS, parse_all  # noqa: F401
