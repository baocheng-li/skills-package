#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F1 分类层：项目、任务类型、复杂度、触发方式。

复杂度默认按 BRD 的步数阈值（L1 ≤3 / L2 4—8 / L3 9—15 / L4 >15）；
`--complexity-profile quantile` 时改用本机步数分位切分，用于 Agent 长任务场景
（真实数据里 >70% 任务落在 L4，固定阈值会失去分辨率，报告会显式提示）。
"""

from collections import Counter, defaultdict


def _bounds(profile, cfg, tasks):
    """返回四个 [lo, hi] 区间，供 _level_of 统一按上界判定。"""
    profiles = (cfg or {}).get("profiles") or {}
    if profile == "quantile":
        steps = sorted(t.get("nModelSteps") or 0 for t in tasks)
        if not steps:
            return [[0, 3], [4, 8], [9, 15], [16, 10 ** 6]]
        q = (profiles.get("quantile") or {}).get("quantiles") or [0.25, 0.6, 0.85]
        cuts = [steps[min(len(steps) - 1, int(len(steps) * x))] for x in q]
        cuts = sorted(cuts)
        return [[0, cuts[0]], [cuts[0] + 1, cuts[1]],
                [cuts[1] + 1, cuts[2]], [cuts[2] + 1, 10 ** 6]]
    spec = profiles.get("spec") or {}
    return [spec.get(k, [0, 0]) for k in ("L1", "L2", "L3", "L4")]


def _level_of(n, bounds):
    if n <= bounds[0][1]:
        return "L1"
    if n <= bounds[1][1]:
        return "L2"
    if n <= bounds[2][1]:
        return "L3"
    return "L4"


def apply(data, cfg=None, profile="spec"):
    cfg = cfg or {}
    tasks = data.get("tasks") or []
    bounds = _bounds(profile, cfg, tasks)
    aliases = cfg.get("project_aliases") or {}

    for t in tasks:
        n = t.get("nModelSteps") or sum(1 for s in t.get("steps", []) if s.get("kind") == "model")
        t["level"] = _level_of(n, bounds)
        t["levelBasis"] = "steps=%d" % n
        proj = t.get("project") or "未绑定项目"
        t["project"] = aliases.get(proj, proj)
        if not t.get("type") or t["type"] == "未分类":
            t["type"] = "通用任务"
        t["triggerType"] = {"人工": "manual", "定时": "scheduled",
                             "后台自动化": "auto"}.get(t.get("trigger"), "manual")

    # 项目维度汇总
    by_proj = defaultdict(lambda: {"tasks": 0, "credits": 0.0, "tokens": 0, "types": Counter(),
                                   "last": ""})
    for t in tasks:
        p = by_proj[t["project"]]
        p["tasks"] += 1
        p["credits"] += t.get("credits") or 0
        p["tokens"] += (t.get("inTok") or 0) + (t.get("outTok") or 0)
        p["types"][t["type"]] += 1
        p["last"] = max(p["last"], t.get("ts") or "")
    projects = [{"name": k, "tasks": v["tasks"], "credits": round(v["credits"], 1),
                 "tokens": v["tokens"], "last": v["last"],
                 "types": v["types"].most_common(3)}
                for k, v in by_proj.items()]
    projects.sort(key=lambda x: -x["credits"])

    # 类型 × 复杂度
    matrix = defaultdict(lambda: {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "credits": 0.0})
    for t in tasks:
        m = matrix[t["type"]]
        m[t["level"]] += 1
        m["credits"] += t.get("credits") or 0

    l4 = sum(1 for t in tasks if t["level"] == "L4")
    levels = Counter(t["level"] for t in tasks)
    data["projects"] = projects
    data["matrix"] = [{"type": k, "L1": v["L1"], "L2": v["L2"], "L3": v["L3"], "L4": v["L4"],
                       "credits": round(v["credits"], 1)} for k, v in
                      sorted(matrix.items(), key=lambda kv: -kv[1]["credits"])]
    data["classification"] = {
        "profile": profile,
        "bounds": [b[1] for b in bounds[:3]],
        "l4Share": round(l4 / len(tasks), 4) if tasks else 0,
        "levels": {k: levels.get(k, 0) for k in ("L1", "L2", "L3", "L4")},
        "unboundProjectRate": round(
            sum(1 for t in tasks if t["project"] == "未绑定项目") / len(tasks), 4
        ) if tasks else 0,
        "note": ("固定步数阈值下有 %.0f%% 任务落在 L4，分级分辨率不足；"
                 "可用 --complexity-profile quantile 按本机分位重定标。" % (l4 * 100.0 / len(tasks)))
                if tasks and l4 / len(tasks) > 0.6 else "",
    }
    return data
