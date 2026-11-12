#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沙箱自检 —— 兼容壳。实现在 ``adapters.sandbox``。

命令行契约不能丢：README 第 2 节与 SKILL.md 安全章都写着
``python3 -m hosts.commands.sandbox_check --audit-static`` 应当输出「通过」。
所以这个壳除了转发函数，还必须转发 ``__main__``——只导出 `audit_static`
的话命令能跑通但不打印任何东西，静默通过比报错更难发现。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.sandbox import *           # noqa: F401,F403
from adapters.sandbox import (           # noqa: F401
    SandboxError, assert_safe_path, audit_static, guard_output_size,
    main, run_checks, safe_roots, should_skip,
)

if __name__ == "__main__":
    sys.exit(main())
