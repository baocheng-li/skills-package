#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 数据源 —— ``core.ports.LogSource`` 的本地实现。

职责边界
--------
    发现：扫 ``~/.workbuddy`` / ``~/.codebuddy`` / ``~/.claude``，校验白名单
    采集：日志 + sqlite（只读副本）+ 会话 jsonl → 统一数据模型

它**不做**任何计算：分类、异常、预算、对账都不在这里。
这样换一个平台（比如换成分析 Cursor 的日志），只需要再写一个
实现了同样两个方法的类，Core 与 Renderer 一行都不用改。
"""

from adapters import discover as discover_mod
from adapters import parse_logs


class WorkBuddySource(object):
    """本机 WorkBuddy / CodeBuddy / Claude 日志源。"""

    name = "workbuddy"

    def __init__(self, log=None):
        self._log = log

    def discover(self, root=None):
        return discover_mod.discover_tools(root, log=self._log)

    def collect(self, tools, days=None, log=None, root=None, type_rules=None):
        return parse_logs.parse_all(
            tools, days=days, rules=None, log=log or self._log,
            root=root, type_rules=type_rules)


def make(**kw):
    """工厂：宿主按名字取数据源时用。"""
    return WorkBuddySource(**kw)
