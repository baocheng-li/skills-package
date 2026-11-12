#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Host 层 —— 四种发布形态的入口。

同一个 Core + 同一套 Renderer，四种壳：

    cli.py      命令行          python -m hosts.cli --format html
    skill.py    Skill（Agent）  返回 JSON 给对话层，由 Agent 读结论
    desktop.py  桌面版          本地起一个只读 HTTP 服务并打开浏览器
    plugin.py   插件            MCP 兼容的 JSON-RPC stdio server
    web.py      Web 静态站      生成 index.html + 数据 JSON，可托管到任意静态空间

约定：只有这一层可以读环境变量、argv、stdin、监听端口、弹浏览器。
Core / Adapter / Renderer 一律不知道「自己正跑在哪种形态里」。
"""

HOSTS = ("cli", "skill", "desktop", "plugin", "web")
