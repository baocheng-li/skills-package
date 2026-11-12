#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时任务汇总：频率、执行次数、单次成本、成功率与治理标记。

只输出**实测**数据（窗口内执行次数、单次均值、近 7 / 14 日实测消耗），
不做月度预估——每日消耗波动大，外推没有决策价值（见 F6 说明）。
"""

import datetime as dt


def _match_tasks(auto, tasks):
    name = (auto.get("name") or "").strip()
    if not name:
        return []
    return [t for t in tasks
            if t.get("trigger") != "人工" and name[:12] in (t.get("name") or "")]


def _sum_between(tasks, start, end):
    return round(sum(t.get("credits") or 0 for t in tasks
                     if start <= (t.get("day") or "")[:10] <= end), 1)


def analyze(tasks, automations, days):
    days = max(1, days)
    task_days = sorted({(t.get("day") or "")[:10] for t in tasks if t.get("day")})
    anchor = task_days[-1] if task_days else dt.date.today().isoformat()
    anchor_d = dt.date.fromisoformat(anchor)
    d7 = (anchor_d - dt.timedelta(days=6)).isoformat()
    d14 = (anchor_d - dt.timedelta(days=13)).isoformat()

    out = []
    for a in automations:
        runs = a.get("runs") or 0
        avg = a.get("avg") or 0
        credits = a.get("credits") if a.get("credits") is not None else avg * runs
        daily = a.get("daily")
        if daily is None:
            daily = round(credits / days, 2)
        related = _match_tasks(a, tasks)
        ok = sum(1 for t in related if t.get("status") == "成功")
        succ = round(ok / len(related), 3) if related else None
        last7 = _sum_between(related, d7, anchor)
        last14 = _sum_between(related, d14, anchor)

        mark, reason = "正常", ""
        if succ is not None and succ < 0.8 and credits > 0:
            mark, reason = "建议降频/暂停", "成功率 %.0f%% 低于 80%%，窗口内消耗 %.0f 积分" % (
                succ * 100, credits)
        elif a.get("level") == "L4" and credits >= 300:
            mark, reason = "建议切轻量模型", "L4 任务窗口内消耗 %.0f 积分，可对提取/汇总段降档" % credits
        elif credits == 0:
            mark, reason = "无消耗", "窗口内未产生积分"

        out.append({
            "name": a.get("name"),
            "freq": a.get("freq") or "—",
            "level": a.get("level") or "—",
            "runs": runs,
            "avg": round(avg, 1),
            "credits": round(credits, 1),
            "daily": round(daily, 2),
            "last7": last7,
            "last14": last14,
            "prev": a.get("prev") or 0,
            "model": a.get("model") or "unknown",
            "status": a.get("status") or "正常",
            "owner": a.get("owner") or "本机",
            "days": a.get("days") or 1,
            "succ": succ,
            "mark": mark,
            "reason": reason,
            "taskIds": [t.get("id") for t in related][:20],
        })

    out.sort(key=lambda x: -x["credits"])
    total = sum(x["credits"] for x in out)
    top3 = sum(x["credits"] for x in out[:3])
    return {
        "items": out,
        "count": len(out),
        "credits": round(total, 1),
        "last7": round(sum(x["last7"] for x in out), 1),
        "last14": round(sum(x["last14"] for x in out), 1),
        "top3Share": round(top3 / total, 4) if total else 0,
        "sufficient": any(x["runs"] >= 3 for x in out),
        "note": ("执行记录不足 3 次，成功率与尖峰判定标记为样本不足"
                 if not any(x["runs"] >= 3 for x in out) else ""),
    }
