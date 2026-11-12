#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志解析调度层：按工具类型选择适配器，输出统一中间结构。

当前内置 WorkBuddy / CodeBuddy 适配器（同源日志格式）；
新增工具只需在 ADAPTERS 中注册一个 `collect(days, root, rules, log) -> (data, ctx)`。
"""

from adapters.workbuddy import collect_data

ADAPTERS = {
    "workbuddy": collect_data,
    "codebuddy": collect_data,
    "claude": None,      # 预留：Anthropic JSONL 适配器
    "openclaw": None,    # 预留
}


def parse_all(tools, days=0, rules=None, log=None, root=None, type_rules=None):
    usable = [t for t in tools if ADAPTERS.get(t["tool"])]
    if not usable:
        names = ", ".join(t["tool"] for t in tools) or "无"
        raise RuntimeError("已发现 %s，但尚无对应解析适配器。" % names)

    # 允许 config/classification.json 覆盖任务类型关键词表
    if type_rules:
        import adapters.workbuddy as _wb
        if hasattr(_wb, "set_type_rules"):
            _wb.set_type_rules(type_rules)
            if log:
                log("任务类型关键词表已由配置覆盖：%d 类" % len(type_rules))

    # WorkBuddy 与 CodeBuddy 共用同一适配器，且适配器本身支持一次扫描多个 home，
    # 因此只调用一次，避免重复计数。
    primary = usable[0]
    data, ctx = ADAPTERS[primary["tool"]](days=days, root=root, rules=rules, log=log)
    ctx["parsed_tools"] = [t["tool"] for t in usable]
    ctx["unsupported"] = [t["tool"] for t in tools if not ADAPTERS.get(t["tool"])]
    return data, ctx
