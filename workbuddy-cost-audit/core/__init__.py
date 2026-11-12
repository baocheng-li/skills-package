#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core 层 —— 领域内核。

三条铁律
--------
1. **不 import adapters / renderer / hosts**。Core 不知道数据从哪个平台的日志来，
   也不知道最终是 HTML 还是 PDF。依赖方向永远指向内。
2. **不打开文件、不发网络请求**（唯一例外是凭证哈希校验，
   通过可注入的 ``resolver`` / ``digest`` 让宿主决定怎么读）。
3. **输入输出都是普通 dict / dataclass**，可被序列化、可被跨进程传递。

这层的东西可以被四种宿主原样复用：
    Skill      拿它做一次分析并回 JSON 给 Agent
    桌面版     拿它配本地 HTTP 服务
    插件       拿它做 MCP 工具的实现
    Web        拿它做无文件系统的纯计算

目录
----
    ports.py    宿主必须提供的四个能力（日志源 / 存储 / 时钟 / 通知）
    models.py   领域模型与 ReportModel
    policy.py   告警阈值、级别、凭证状态的纯语义
    pipeline.py 唯一编排：采集 → 脱敏 → 分类 → 过滤 → 分析 → 汇总
    calc/       各项计算（异常、预算、对账、对比、建议……）
"""

__all__ = ["ports", "models", "policy", "pipeline"]

VERSION = "3.1.0"
SCHEMA = "report-model/v1"
