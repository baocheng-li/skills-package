#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据压缩：把 ReportModel 压成页面消费的紧凑 payload。

为什么要压
----------
steps.csv 原始 1.5 MB，直接内嵌进 HTML 后文件 1.7 MB，双击打开要等两秒。
压缩手段是「字典化 + 数组化」：把 model/tool/phase 三个重复率最高的字符串
抽成字典，步骤对象改成 13 元素的定长数组，体积能砍掉 55% 左右。

编码约定（页面侧 p03_data.js 的 decodeStep 必须与此一致）
------------------------------------------------------
a[0] 步骤序号        a[7]  耗时 ms
a[1] 1=工具 0=模型   a[8]  重试次数
a[2] 模型字典下标    a[9]  时刻 HH:MM:SS
a[3] 工具字典下标    a[10] 阶段字典下标
a[4] input token     a[11] 1=出错
a[5] output token    a[12] 1=积分为估算值
a[6] 积分
"""

ENC_VERSION = "v1"


def _pack_steps(t, mi, ti, pi):
    out = []
    for s in t.get("steps") or []:
        out.append([
            s.get("i", 0),
            1 if s.get("kind") == "tool" else 0,
            mi.get(s.get("model"), -1),
            ti.get(s.get("tool"), -1),
            s.get("inTok", 0),
            s.get("outTok", 0),
            round(float(s.get("credits") or 0), 2),
            s.get("ms", 0) or 0,
            s.get("retry", 0) or 0,
            (s.get("ts") or "")[11:19],
            pi.get(s.get("phase"), -1),
            1 if s.get("err") else 0,
            1 if s.get("est") else 0,
        ])
    return out


def compact(data):
    """把完整数据模型压成页面负载。输入输出都不会被就地修改。"""
    tasks = data.get("tasks") or []

    models, tools, phases = [], [], []
    mi, ti, pi = {}, {}, {}
    for t in tasks:
        for s in t.get("steps") or []:
            m, tl, ph = s.get("model"), s.get("tool"), s.get("phase")
            if m and m not in mi:
                mi[m] = len(models)
                models.append(m)
            if tl and tl not in ti:
                ti[tl] = len(tools)
                tools.append(tl)
            if ph and ph not in pi:
                pi[ph] = len(phases)
                phases.append(ph)

    out_tasks = []
    for t in tasks:
        out_tasks.append({
            "id": t["id"], "name": t.get("name", ""), "type": t.get("type", ""),
            "txn": t.get("txn", ""),
            "level": t.get("level", "L1"), "trigger": t.get("trigger", "人工"),
            "rtype": t.get("rtype", "chat"),
            "project": t.get("project", ""), "modelChain": t.get("modelChain", ""),
            "mainChannel": t.get("mainChannel", ""), "models": t.get("models", []),
            "credits": t.get("credits", 0), "inTok": t.get("inTok", 0),
            "outTok": t.get("outTok", 0), "duration": round(t.get("duration", 0)),
            "retry": t.get("retry", 0), "nTools": t.get("nTools", 0),
            "nModelSteps": t.get("nModelSteps", 0), "status": t.get("status", "成功"),
            "flags": t.get("flags", []), "ruleIds": t.get("ruleIds", []),
            "waste": t.get("waste", 0),
            "day": t.get("day", ""), "ts": (t.get("ts") or "")[:19],
            "cwd": (t.get("cwd") or "")[:60], "steps": _pack_steps(t, mi, ti, pi),
        })

    return {
        "enc": ENC_VERSION,
        "dict": {"models": models, "tools": tools, "phases": phases},
        "meta": data.get("meta", {}),
        "models": data.get("models", []),
        "daily": data.get("daily", []),
        "rules": data.get("rules", []),
        "automations": data.get("automations", []),
        "bench": data.get("bench", []),
        "anomalies": data.get("anomalies", []),
        "tasks": out_tasks,
        "budget": data.get("budget", {}),
        "creditRecon": data.get("creditRecon", {}),
        "overview": data.get("overview", {}),
        "projects": data.get("projects", []),
        "matrix": data.get("matrix", []),
        "classification": data.get("classification", {}),
        "scheduled": data.get("scheduled", {}),
        "monitor": data.get("monitor", {}),
        # 系统计划任务（每日取数）：与 scheduled 分开——那个是 Agent 里配的自动化，
        # 这个是本机计划任务，不产生 Agent 任务、不消耗积分
        "dailyCredit": data.get("dailyCredit", {}),
        "recommendations": data.get("recommendations", {}),
    }
