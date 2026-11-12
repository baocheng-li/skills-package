#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adapter 层 —— 把具体平台/存储适配成 Core 认识的东西。

含三块
------
    workbuddy/    WorkBuddy / CodeBuddy / Claude 的日志与数据库读取（实现 LogSource）
    discover.py   本机目录发现与白名单校验
    parse_logs.py 采集入口（日志 + sqlite + 会话文件 → 统一数据模型）
    sandbox.py    沙箱自检（只读白名单、输出体积上限）
    state.py      余额状态文件（实现 Store 的一小块：唯一真相源）

新增一个平台 = 新增一个 ``adapters/<platform>/`` 实现 LogSource，
Core 与 Renderer 一行都不用改。
"""

__all__ = ["discover", "parse_logs", "sandbox", "state", "workbuddy"]
