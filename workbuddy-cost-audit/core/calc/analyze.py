#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F2 总览聚合：把七项功能的结果收敛成页面所需的指标、横幅与数据源声明。"""


def build(data, budget, anomalies, sched, recs, ctx):
    tasks = data.get("tasks") or []
    n = len(tasks)
    credits = sum(t.get("credits") or 0 for t in tasks)
    tin = sum(t.get("inTok") or 0 for t in tasks)
    tout = sum(t.get("outTok") or 0 for t in tasks)
    ok = sum(1 for t in tasks if t.get("status") == "成功")
    dur = sum(t.get("duration") or 0 for t in tasks)
    meta = data.get("meta") or {}

    # ---- 预警横幅（只陈述余额与阈值，不做可用天数预测）
    banner = []
    lv = budget.get("level")
    th = budget.get("thresholds") or {}
    if lv in ("red", "crit", "warn"):
        color = {"red": "red", "crit": "orange", "warn": "yellow"}[lv]
        title = {"red": "积分消耗红色预警", "crit": "积分消耗橙色预警",
                 "warn": "积分消耗黄色预警"}[lv]
        ratio = budget.get("spendRatio")
        ratio_txt = (">100%（今日消耗已超过当前余额）" if budget.get("spendRatioOver")
                     else ("%.1f%%" % ratio if ratio is not None else "—"))
        banner.append({
            "level": color, "title": title,
            "desc": "今日消耗占当前余额 %s（今日消耗 %s / 当前余额 %s）；"
                    "阈值 黄 %s%% / 橙 %s%% / 红 %s%%。"
                    % (ratio_txt, _fmt(budget.get("todaySpent")),
                       _fmt(budget.get("balance")),
                       _fmt(th.get("warn")), _fmt(th.get("crit")), _fmt(th.get("red"))),
            "tag": "积分监测",
        })
    elif not budget.get("configured"):
        banner.append({
            "level": "gray", "title": "积分监测未配置",
            "desc": "初始积分与今日余额都为空，只输出实测消耗（近 7 日日均 %s 积分）。"
                    "在「消耗台账 → 耗支流水 → 积分监测」里填写两个数即可启用预警。"
                    % _fmt(budget.get("dailyAvg7"), 1),
            "tag": "人工设置",
        })

    if anomalies.get("hitCount"):
        banner.append({
            "level": "yellow", "title": "异常消耗待处理",
            "desc": "%d 个任务命中规则，止损空间估算 %.0f 积分（已消耗，不可追回；占窗口消耗 %.1f%%）" % (
                anomalies["hitCount"], anomalies.get("totalWaste", 0),
                (anomalies.get("totalWaste", 0) / credits * 100) if credits else 0),
            "tag": "F5",
        })
    if sched.get("items") and any(x["mark"] != "正常" for x in sched["items"]):
        bad = [x for x in sched["items"] if x["mark"] != "正常"]
        banner.append({
            "level": "yellow", "title": "定时任务待治理",
            "desc": "%d 个后台任务被标记（%s），窗口内合计 %.0f 积分" % (
                len(bad), "、".join(sorted({x["mark"] for x in bad})),
                sched.get("credits", 0)),
            "tag": "任务监测",
        })
    mon = data.get("monitor") or {}
    mt = mon.get("totals") or {}
    if mt.get("unseenCredits") and (mt.get("unseenShare") or 0) >= 0.05:
        banner.append({
            "level": "yellow", "title": "不可见执行占比偏高",
            "desc": "定时 / 后台 / 技能 / 隐藏调用合计 %d 次执行、%.0f 积分，占窗口 %.1f%%；"
                    "其中隐藏调用 %d 次。详见「任务监测」。"
                    % (mt.get("unseenTasks", 0), mt.get("unseenCredits", 0),
                       (mt.get("unseenShare") or 0) * 100, mt.get("hiddenCalls", 0)),
            "tag": "任务监测",
        })
    if not banner:
        banner.append({"level": "green", "title": "窗口内无预警",
                       "desc": "余额、异常与后台执行均未触发阈值", "tag": "OK"})

    # ---- 数据源与质量
    quality = {
        "creditAccuracy": meta.get("coverage", {}).get("creditAccuracy", 0),
        "tokenCoverage": meta.get("coverage", {}).get("tokenCoverage", 0),
        "reconcileDiff": budget.get("reconcile", {}).get("diffPct"),
        "reconcileMode": budget.get("reconcile", {}).get("label"),
        "reconcileAlert": budget.get("reconcile", {}).get("alert"),
        "l4Share": (data.get("classification") or {}).get("l4Share", 0),
        "unboundProjectRate": (data.get("classification") or {}).get("unboundProjectRate", 0),
    }
    source = {
        "homes": meta.get("homes") or [],
        "logFiles": meta.get("counts", {}).get("logFiles", 0),
        "projectFiles": meta.get("counts", {}).get("projectFiles", 0),
        "sessions": meta.get("counts", {}).get("sessions", 0),
        "generatedAt": meta.get("generatedAt"),
        "windowDays": meta.get("windowDays"),
        "span": meta.get("span"),
        "source": meta.get("source"),
        "rateVersion": (meta.get("rates") or {}).get("fallback"),
        "capabilities": meta.get("capabilities") or {},
        "parsedTools": (ctx or {}).get("parsed_tools") or [],
        "unsupported": (ctx or {}).get("unsupported") or [],
    }

    top = sorted(tasks, key=lambda t: -(t.get("credits") or 0))[:10]

    return {
        "totals": {
            "credits": round(credits, 1),
            "tokens": tin + tout,
            "inputTokens": tin,
            "outputTokens": tout,
            "tasks": n,
            "successRate": round(ok / n, 4) if n else 0,
            "avgCredits": round(credits / n, 1) if n else 0,
            "avgDuration": round(dur / n, 1) if n else 0,
            "activeDays": len(data.get("daily") or []),
        },
        "banner": banner,
        "topTasks": [{"id": t["id"], "name": t.get("name"), "credits": t.get("credits"),
                      "level": t.get("level"), "status": t.get("status"),
                      "type": t.get("type"), "flags": t.get("flags") or []} for t in top],
        "quality": quality,
        "source": source,
        "scheduled": {"count": sched.get("count", 0), "credits": sched.get("credits", 0),
                      "last7": sched.get("last7", 0)},
        "unseen": {"tasks": mt.get("unseenTasks", 0), "credits": mt.get("unseenCredits", 0),
                   "share": mt.get("unseenShare", 0), "hidden": mt.get("hiddenCalls", 0)},
        "recommendation": {"immediateWindowSaving": recs.get("immediateWindowSaving", 0)},
    }


def _fmt(v, nd=0):
    if v is None:
        return "—"
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)
