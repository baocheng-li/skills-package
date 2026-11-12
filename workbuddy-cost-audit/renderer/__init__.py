#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Renderer 层 —— 把 Core 产出的 ReportModel 编成具体产物。

三种产物，同一份数据模型：
    html  —— 单文件离线报告（file:// 双击可用）
    data  —— JSON / CSV，供 ETL 与二次分析
    docs  —— Markdown，供人读与入库

Renderer 只依赖标准库与 assets/（模板分片、图表库），
不 import core，也不 import adapters —— 依赖方向是 host → renderer → (无)，
所以同一份渲染器可以被 Skill、桌面版、插件、Web 四种宿主共用。
"""

__all__ = ["theme", "icons", "payload", "html", "data", "docs"]
