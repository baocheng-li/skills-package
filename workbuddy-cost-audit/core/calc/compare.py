#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F7 对比分析与三档建议。

对比基于本机真实观测（不同模型跑的是不同任务，属于观测对比而非受控 A/B），
因此所有结论都带样本量与可比性限制，样本 <3 的组合不参与推荐。
"""

from collections import defaultdict

MIN_SAMPLE = 3


def build_bench(tasks):
    groups = defaultdict(list)
    for t in tasks:
        model = t.get("mainChannel") or "unknown"
        groups[(t.get("level") or "L4", model)].append(t)

    rows = []
    for (lvl, model), ts in groups.items():
        n = len(ts)
        infer = sum(sum(s.get("ms") or 0 for s in t.get("steps", []) if s.get("kind") == "model")
                    for t in ts) / 1000.0
        wait = sum(sum(s.get("ms") or 0 for s in t.get("steps", []) if s.get("kind") == "tool")
                   for t in ts) / 1000.0
        e2e = sum(t.get("duration") or 0 for t in ts)
        ok = sum(1 for t in ts if t.get("status") == "成功")
        rows.append({
            "lvl": lvl, "model": model, "n": n,
            "succ": round(ok / n * 100, 1) if n else 0,
            "credits": round(sum(t.get("credits") or 0 for t in ts) / n, 1) if n else 0,
            "totalCredits": round(sum(t.get("credits") or 0 for t in ts), 1),
            "e2e": round(e2e / n, 1) if n else 0,
            "infer": round(infer / n, 1) if n else 0,
            "wait": round(wait / n, 1) if n else 0,
            "avgIn": int(sum(t.get("inTok") or 0 for t in ts) / n) if n else 0,
            "avgOut": int(sum(t.get("outTok") or 0 for t in ts) / n) if n else 0,
            "steps": round(sum(len(t.get("steps") or []) for t in ts) / n, 1) if n else 0,
            "cacheHit": None,
            "sampleOK": n >= MIN_SAMPLE,
        })
    rows.sort(key=lambda r: (r["lvl"], r["credits"]))
    return rows


def _level_spend(tasks):
    out = defaultdict(float)
    for t in tasks:
        out[t.get("level") or "L4"] += t.get("credits") or 0
    return out


def _best(rows, lvl):
    ok = [r for r in rows if r["lvl"] == lvl and r["sampleOK"]]
    if len(ok) < 2:
        return None
    top = max(r["succ"] for r in ok)
    cand = sorted([r for r in ok if r["succ"] >= top - 3], key=lambda r: r["credits"])
    if not cand:
        return None
    pick = cand[0]
    costly = max(ok, key=lambda r: r["credits"])
    return {"pick": pick, "costly": costly,
            "save": 1 - pick["credits"] / costly["credits"] if costly["credits"] else 0,
            "sample": sum(r["n"] for r in ok)}


def build_recommendations(tasks, bench, anomalies, sched, budget, days):
    """三档建议。所有金额均为**窗口内实测值**，不做月度外推。"""
    tiers = {"t1": [], "t2": [], "t3": []}
    spend = _level_spend(tasks)

    # ---- 第一档：立即可做
    if anomalies:
        for r in anomalies.get("rules", []):
            if r["id"] in ("RETRY_STORM", "TOOL_LOOP", "CONTEXT_BLOAT") and r["waste"] > 0:
                tiers["t1"].append({
                    "title": "修复「%s」造成的返工消耗" % r["name"],
                    "action": r["advice"],
                    "evidence": "窗口内 %d 个任务命中，实测止损空间 %.0f 积分（已消耗，落到配置上止损）" % (r["hits"], r["waste"]),
                    "windowSaving": round(r["waste"], 1),
                    "taskIds": r["taskIds"][:5], "risk": "低",
                })
    for s in sched.get("items", [])[:10]:
        if s["mark"] in ("建议降频/暂停", "建议切轻量模型") and (s.get("credits") or 0) > 0:
            tiers["t1"].append({
                "title": "%s：%s" % (s["name"], s["mark"]),
                "action": s["reason"],
                "evidence": "窗口内 %d 次执行，单次 %.0f 积分，合计 %.0f 积分（实测）" % (
                    s["runs"], s["avg"], s.get("credits") or 0),
                "windowSaving": 0,
                "taskIds": s["taskIds"][:5], "risk": "低",
            })

    # ---- 第二档：需要验证
    for lvl in ("L1", "L2", "L3", "L4"):
        b = _best(bench, lvl)
        if not b:
            continue
        tiers["t2"].append({
            "title": "%s 层级试点 %s" % (lvl, b["pick"]["model"]),
            "action": "在同类任务上先跑 3—5 轮，对齐成功率与质量后再切换",
            "evidence": "%s 在 %s 为 %.0f 积分/任务、成功率 %.1f%%，对比 %s 的 %.0f 积分/%.1f%%，成本低 %.0f%%（样本 n=%d）" % (
                b["pick"]["model"], lvl, b["pick"]["credits"], b["pick"]["succ"],
                b["costly"]["model"], b["costly"]["credits"], b["costly"]["succ"],
                b["save"] * 100, b["pick"]["n"]),
            "windowSaving": 0,
            "taskIds": [], "risk": "中",
        })

    # ---- 第三档：结构性优化
    l4share = sum(1 for t in tasks if t.get("level") == "L4") / len(tasks) if tasks else 0
    if l4share > 0.6:
        tiers["t3"].append({
            "title": "重定标任务复杂度分级",
            "action": "改用 --complexity-profile quantile，或按本机步数分位重设 L1—L4 阈值，"
                      "否则 70%+ 任务挤在 L4，路由建议没有分辨率",
            "evidence": "当前 L4 占比 %.0f%%（%d/%d）" % (l4share * 100,
                                                    sum(1 for t in tasks if t.get("level") == "L4"),
                                                    len(tasks)),
            "windowSaving": 0, "taskIds": [], "risk": "低",
        })
    tiers["t3"].append({
        "title": "建立任务级预算上限与预警联动",
        "action": "对单任务积分设置上限并在命中时告警；把积分监测的预警比告警接入站内/邮件/Webhook",
        "evidence": "当前单任务 P95 为 %.0f 积分；今日消耗 %.0f 积分（今日消耗 / 当前余额 %s）" % (
            budget.get("p95Task", 0), budget.get("todaySpent") or 0,
            (">100%" if budget.get("spendRatioOver") else
             ("%.1f%%" % budget["spendRatio"]) if budget.get("spendRatio") is not None
             else "未配置余额")),
        "windowSaving": 0, "taskIds": [], "risk": "中",
    })
    tiers["t3"].append({
        "title": "关键任务锁定模型，禁止余额驱动降级",
        "action": "对长链路任务固定模型，避免中途降级导致的交付不一致",
        "evidence": "窗口内 %d 个任务出现模型切换（占 %.0f%%），其中高置信度余额驱动 %d 个" % (
            budget.get("modelSwitch", {}).get("count", 0),
            budget.get("modelSwitch", {}).get("ratio", 0) * 100,
            budget.get("modelSwitch", {}).get("highConfidence", 0)),
        "windowSaving": 0, "taskIds": [], "risk": "中",
    })

    for k in tiers:
        tiers[k].sort(key=lambda x: -(x.get("windowSaving") or 0))
    total = sum(x.get("windowSaving") or 0 for x in tiers["t1"])
    return {
        "tiers": tiers,
        "immediateWindowSaving": round(total, 1),
        "minSample": MIN_SAMPLE,
        "note": "对比为真实观测而非受控实验；样本 <3 的组合只展示不推荐。",
    }


def build(tasks, anomalies, sched, budget, days):
    bench = build_bench(tasks)
    recs = build_recommendations(tasks, bench, anomalies, sched, budget, days)
    return {"rows": bench, "recommendations": recs}
