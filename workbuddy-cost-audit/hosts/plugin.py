#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件宿主 —— MCP 兼容的 JSON-RPC stdio server。

为什么自己实现而不用 SDK
------------------------
整个项目的硬约束是「零依赖、离线可用」。MCP 的 stdio 传输就是
「一行一个 JSON 的 JSON-RPC 2.0」，实现它只需要 100 行标准库代码，
比引入 SDK 再处理版本兼容更省事，也让发布包保持「解压即用」。

协议要点
--------
* 传输：stdin/stdout，NDJSON（每行一条消息）；
* 方法：``initialize`` / ``tools/list`` / ``tools/call`` / ``shutdown``；
* 日志一律走 stderr——stdout 是协议通道，混进一行 print 就会让客户端解析失败。

注册的工具
----------
    audit_run     跑一次完整分析（可选同时出 HTML），返回结论与指标
    audit_budget  只回预算与告警（最轻，适合高频轮询）
    audit_explain 返回口径说明（数据从哪来、哪些数不是测出来的）

用法
----
    python -m hosts.plugin          # 由 MCP 客户端拉起，或手工喂 JSON 行
"""

import json
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hosts import skill as skill_host            # noqa: E402
from renderer import docs as docs_r              # noqa: E402

PROTOCOL = "2024-11-05"
SERVER = {"name": "workbuddy-cost-audit", "version": "3.1.0"}

TOOLS = [
    {
        "name": "audit_run",
        "description": "分析本机 Agent 日志的积分/Token 消耗，返回结论、关键指标与三档建议。"
                       "只读采集，不联网、不上传。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "last": {"type": "string", "description": "相对区间，如 7d / 30d"},
                "days": {"type": "integer", "description": "统计窗口天数，0=全部历史"},
                "project": {"type": "string"},
                "html": {"type": "boolean", "description": "是否同时生成单文件报告"},
                "skin": {"type": "string", "enum": ["dark", "light"]},
            },
        },
    },
    {
        "name": "audit_budget",
        "description": "只返回余额三口径与告警级别（今日消耗 / 当前余额 / 预警比 / 退出码）。",
        "inputSchema": {"type": "object", "properties": {
            "last": {"type": "string"}, "days": {"type": "integer"}}},
    },
    {
        "name": "audit_explain",
        "description": "返回报告口径说明：数据来源、三个口径的定义、哪些数字不是测出来的、"
                       "报告不做什么。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text(s):
    return {"content": [{"type": "text", "text": s}], "isError": False}


def call_tool(name, args):
    args = args or {}
    if name == "audit_run":
        res = skill_host.run(args, render_html=bool(args.get("html")))
        return _text(json.dumps(res, ensure_ascii=False, indent=2))
    if name == "audit_budget":
        res = skill_host.run({"last": args.get("last"), "days": args.get("days")})
        m = res.get("metrics") or {}
        return _text(json.dumps({
            "level": res.get("level"),
            "todaySpent": m.get("todaySpent"),
            "todayBalance": m.get("todayBalance"),
            "balance": m.get("balance"),
            "spendRatioPct": m.get("spendRatioPct"),
            "warning": res.get("warnings"),
        }, ensure_ascii=False, indent=2))
    if name == "audit_explain":
        return _text(docs_r.explain_card(type("M", (), {
            "totals": lambda self: {}, "budget": {}})()))
    raise KeyError("未知工具：%s" % name)


def handle(msg):
    """处理一条 JSON-RPC 消息，返回响应 dict 或 None（通知不回）。"""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            out = call_tool(params.get("name"), params.get("arguments"))
            return {"jsonrpc": "2.0", "id": mid, "result": out}
        except Exception as e:                        # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": "执行失败：%s" % e}],
                               "isError": True}}
    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if mid is None:
        return None                                   # 通知类，不响应
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "未实现的方法：%s" % method}}


def main(argv=None):
    # stdout 是协议通道：把可能的调试输出重定向到 stderr
    print("[plugin] workbuddy-cost-audit MCP server ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print("[plugin] 非 JSON 输入，已忽略", file=sys.stderr, flush=True)
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
