#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F1 检索层：项目 / 类型 / 复杂度 / 触发方式 / 日期区间过滤与聚合。"""

import datetime as dt
from collections import defaultdict


def parse_range(date_from=None, date_to=None, last=None, anchor=None):
    """支持绝对区间与 --last 7d/14d/30d 相对区间。返回 (from, to) 或 (None, None)。"""
    today = dt.date.fromisoformat(anchor) if anchor else dt.date.today()
    if last:
        m = last.strip().lower()
        n = int("".join(ch for ch in m if ch.isdigit()) or 7)
        return (today - dt.timedelta(days=n - 1)).isoformat(), today.isoformat()
    f = date_from or None
    t = date_to or None
    return f, t


def filter_tasks(tasks, project=None, task_type=None, level=None, trigger=None,
                 date_from=None, date_to=None, status=None, keyword=None):
    out = []
    for t in tasks:
        day = (t.get("day") or "")[:10]
        if project and t.get("project") != project:
            continue
        if task_type and t.get("type") != task_type:
            continue
        if level and t.get("level") not in (level if isinstance(level, (list, tuple)) else [level]):
            continue
        if trigger and t.get("triggerType") != trigger and t.get("trigger") != trigger:
            continue
        if status and t.get("status") != status:
            continue
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if keyword:
            k = keyword.lower()
            if k not in (t.get("name") or "").lower():
                continue
        out.append(t)
    return out


def daily_stats(tasks, dims=("date", "project")):
    """按日期 + 项目聚合，输出 F1 的 daily_stats 行。"""
    bucket = defaultdict(lambda: {"tasks": 0, "credits": 0.0, "in": 0, "out": 0,
                                  "failed": 0, "duration": 0.0, "types": set()})
    for t in tasks:
        key = ((t.get("day") or "")[:10], t.get("project") or "未绑定项目")
        b = bucket[key]
        b["tasks"] += 1
        b["credits"] += t.get("credits") or 0
        b["in"] += t.get("inTok") or 0
        b["out"] += t.get("outTok") or 0
        b["failed"] += 1 if t.get("status") == "失败" else 0
        b["duration"] += t.get("duration") or 0
        b["types"].add(t.get("type"))
    rows = []
    for (day, proj), v in sorted(bucket.items(), reverse=True):
        rows.append({
            "date": day, "project": proj, "tasks": v["tasks"],
            "credits": round(v["credits"], 2),
            "input_tokens": v["in"], "output_tokens": v["out"],
            "total_tokens": v["in"] + v["out"],
            "failed": v["failed"],
            "duration_s": round(v["duration"], 1),
            "task_types": "/".join(sorted(x for x in v["types"] if x)),
        })
    return rows


def facet(tasks, field):
    c = defaultdict(lambda: {"tasks": 0, "credits": 0.0})
    for t in tasks:
        k = t.get(field) or "未标注"
        c[k]["tasks"] += 1
        c[k]["credits"] += t.get("credits") or 0
    return [{"key": k, "tasks": v["tasks"], "credits": round(v["credits"], 1)}
            for k, v in sorted(c.items(), key=lambda kv: -kv[1]["credits"])]
