#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F5 异常规则引擎：八条规则，只标记与归因，不执行任何处置。"""

from collections import defaultdict


def _p95(values):
    if not values:
        return 0.0
    vs = sorted(values)
    idx = min(len(vs) - 1, int(round(len(vs) * 0.95)) - 1)
    return vs[max(0, idx)]


def _tool_runs(steps):
    """把工具步骤按出现顺序串起来，供循环/连续失败判定。"""
    return [s for s in steps if s.get("kind") == "tool"]


def run_rules(tasks, cfg, has_cache=False):
    cfg = cfg or {}
    rules = []

    def conf(key, default=None):
        return cfg.get(key, default) or {}

    # ---- 规则 1：重试风暴
    c = conf("RETRY_STORM")
    hits, waste, ids = 0, 0.0, []
    if c.get("enabled"):
        th = int(c.get("task_retry_sum", 5))
        fail_th = int(c.get("same_tool_consecutive_fail", 3))
        for t in tasks:
            tools = _tool_runs(t.get("steps") or [])
            fails = sum(1 for s in tools if s.get("err"))
            run, prev, max_run = 0, None, 0
            for s in tools:
                if s.get("err") and s.get("tool") == prev:
                    run += 1
                elif s.get("err"):
                    run = 1
                else:
                    run = 0
                prev = s.get("tool")
                max_run = max(max_run, run)
            if fails >= th or max_run >= fail_th:
                hits += 1
                ids.append(t["id"])
                model_steps = [s for s in t.get("steps", []) if s.get("kind") == "model"]
                avg = (sum(s.get("credits") or 0 for s in model_steps) / len(model_steps)) if model_steps else 0
                waste += avg * min(fails, 3) * 0.5
        rules.append({"id": "RETRY_STORM", "name": "重试风暴", "hits": hits,
                      "waste": round(waste, 1), "status": "hit" if hits else "clear",
                      "th": "单任务失败工具调用 ≥%d 次，或同工具连续失败 ≥%d 次" % (th, fail_th),
                      "advice": c.get("advice", ""), "taskIds": ids[:20]})

    # ---- 规则 2：上下文膨胀
    c = conf("CONTEXT_BLOAT")
    hits, waste, ids = 0, 0.0, []
    if c.get("enabled"):
        th = int(c.get("single_step_input_tokens", 100000))
        for t in tasks:
            w = 0.0
            for s in t.get("steps", []):
                if s.get("kind") != "model":
                    continue
                it = s.get("inTok") or 0
                if it > th and (s.get("credits") or 0) > 0:
                    w += s["credits"] * (it - th) / float(it)
            if w >= 0.5:
                hits += 1
                ids.append(t["id"])
                waste += w
        rules.append({"id": "CONTEXT_BLOAT", "name": "上下文膨胀", "hits": hits,
                      "waste": round(waste, 1), "status": "hit" if hits else "clear",
                      "th": "单步输入 token > %s" % format(th, ","),
                      "advice": c.get("advice", ""), "taskIds": ids[:20]})

    # ---- 规则 3：工具循环
    c = conf("TOOL_LOOP")
    hits, waste, ids = 0, 0.0, []
    if c.get("enabled"):
        th = int(c.get("repeat_threshold", 3))
        same_args = bool(c.get("require_same_args", True))
        for t in tasks:
            tools = _tool_runs(t.get("steps") or [])
            run, prev, extra = 0, None, 0
            for s in tools:
                key = (s.get("tool"), s.get("arg") if same_args else "")
                if key == prev:
                    run += 1
                    if run >= th:
                        extra += 1
                else:
                    run, prev = 1, key
            if extra:
                hits += 1
                ids.append(t["id"])
                model_steps = [s for s in t.get("steps", []) if s.get("kind") == "model"]
                avg = (sum(s.get("credits") or 0 for s in model_steps) / len(model_steps)) if model_steps else 0
                waste += avg * extra
        rules.append({"id": "TOOL_LOOP", "name": "工具循环", "hits": hits,
                      "waste": round(waste, 1), "status": "hit" if hits else "clear",
                      "th": "同工具%s连续重复 ≥%d 次" % ("同参数" if same_args else "", th),
                      "advice": c.get("advice", ""), "taskIds": ids[:20]})

    # ---- 规则 4：单任务积分尖峰（同类型 P95 的 K 倍）
    c = conf("TASK_CREDIT_SPIKE")
    hits, waste, ids = 0, 0.0, []
    if c.get("enabled"):
        k = float(c.get("p95_multiplier", 2))
        by_type = defaultdict(list)
        for t in tasks:
            by_type[t.get("type") or "未分类"].append(t.get("credits") or 0)
        p95 = {k2: _p95(v) for k2, v in by_type.items()}
        for t in tasks:
            base = p95.get(t.get("type") or "未分类") or 0
            cr = t.get("credits") or 0
            if base > 0 and cr > k * base:
                hits += 1
                ids.append(t["id"])
                waste += cr - base
        rules.append({"id": "TASK_CREDIT_SPIKE", "name": "单任务尖峰", "hits": hits,
                      "waste": round(waste, 1), "status": "hit" if hits else "clear",
                      "th": "单任务积分 > 同类型 P95 × %.1f" % k,
                      "advice": c.get("advice", ""), "taskIds": ids[:20]})

    # ---- 规则 5：定时任务尖峰
    c = conf("SCHEDULED_SPIKE")
    hits, waste, ids = 0, 0.0, []
    if c.get("enabled"):
        k = float(c.get("mean_multiplier", 3))
        groups = defaultdict(list)
        for t in tasks:
            if t.get("trigger") != "人工":
                groups[(t.get("name") or "")[:24]].append(t)
        eligible = 0
        for name, ts in groups.items():
            if len(ts) < 3:
                continue
            eligible += 1
            mean = sum(x.get("credits") or 0 for x in ts) / len(ts)
            for t in ts:
                if mean > 0 and (t.get("credits") or 0) > k * mean:
                    hits += 1
                    ids.append(t["id"])
                    waste += (t.get("credits") or 0) - mean
        status = "hit" if hits else ("insufficient" if not eligible else "clear")
        rules.append({"id": "SCHEDULED_SPIKE", "name": "定时任务尖峰", "hits": hits,
                      "waste": round(waste, 1), "status": status,
                      "th": "单次积分 > 同任务均值 × %.1f（需 ≥3 次执行）" % k,
                      "advice": c.get("advice", ""), "taskIds": ids[:20],
                      "note": "窗口内无任务执行 ≥3 次，规则样本不足" if not eligible else ""})

    # ---- 规则 6：任务内模型切换（一致性风险，不计入浪费）
    c = conf("MODEL_SWITCH")
    hits, ids = 0, []
    if c.get("enabled"):
        for t in tasks:
            models = [s.get("model") for s in t.get("steps", [])
                      if s.get("kind") == "model" and s.get("model")]
            if len(set(models)) > 1:
                hits += 1
                ids.append(t["id"])
        rules.append({"id": "MODEL_SWITCH", "name": "模型切换", "hits": hits,
                      "waste": 0.0, "status": "hit" if hits else "clear",
                      "th": "任务内出现 ≥2 个模型", "advice": c.get("advice", ""),
                      "taskIds": ids[:20], "risk": True})

    # ---- 规则 7：失败仍烧钱
    c = conf("FAILED_BURN")
    hits, waste, ids = 0, 0.0, []
    if c.get("enabled"):
        th = float(c.get("credits_threshold", 10000))
        for t in tasks:
            cr = t.get("credits") or 0
            if t.get("status") == "失败" and cr > th:
                hits += 1
                ids.append(t["id"])
                waste += cr
        rules.append({"id": "FAILED_BURN", "name": "失败消耗", "hits": hits,
                      "waste": round(waste, 1), "status": "hit" if hits else "clear",
                      "th": "失败任务积分 > %s" % format(int(th), ","),
                      "advice": c.get("advice", ""), "taskIds": ids[:20]})

    # ---- 规则 8：缓存未生效
    c = conf("CACHE_MISS")
    if c.get("enabled") and not has_cache:
        rules.append({"id": "CACHE_MISS", "name": "缓存未命中", "hits": 0, "waste": 0.0,
                      "status": "unsupported", "th": "缓存命中率 < %.0f%% 且输入 > %s"
                      % (float(c.get("hit_rate_threshold", 0.3)) * 100,
                         format(int(c.get("min_input_tokens", 50000)), ",")),
                      "advice": c.get("advice", ""), "taskIds": [],
                      "note": "数据源未提供 cache_read_tokens，规则自动禁用"})
    elif c.get("enabled"):
        rules.append({"id": "CACHE_MISS", "name": "缓存未命中", "hits": 0, "waste": 0.0,
                      "status": "clear", "th": "缓存命中率阈值",
                      "advice": c.get("advice", ""), "taskIds": []})

    # ---- 回写任务标记
    by_id = {t["id"]: t for t in tasks}
    for r in rules:
        if r.get("risk"):
            continue
        for tid in r["taskIds"]:
            t = by_id.get(tid)
            if not t:
                continue
            t.setdefault("flagIds", [])
            if r["id"] not in t["flagIds"]:
                t["flagIds"].append(r["id"])
                t.setdefault("flags", []).append(r["name"])

    total_waste = 0.0
    anomaly_ids = []
    for t in tasks:
        ids_ = t.get("flagIds") or []
        if not ids_:
            t.setdefault("flagIds", [])
            t.setdefault("flags", [])
            t["waste"] = 0.0
            t["wasteByRule"] = {}
            continue
        # 单任务浪费 = 各命中规则口径之和，且不超过任务总积分
        raw = {}
        for r in rules:
            if r["id"] in ids_:
                raw[r["id"]] = max(0.0, _task_waste(t, r["id"], cfg, tasks))
        tot = sum(raw.values())
        cap = t.get("credits") or 0
        scale = min(1.0, cap / tot) if tot > 0 else 0.0
        # 按比例归因到各规则，保证「规则合计 == 任务合计」严格成立
        t["wasteByRule"] = {k: round(v * scale, 2) for k, v in raw.items()}
        t["waste"] = round(tot * scale, 1)
        t["ruleIds"] = ids_
        total_waste += t["waste"]
        if t["waste"] > 0:
            anomaly_ids.append(t["id"])

    # 规则级浪费由任务级归因汇总而来（不走 taskIds 截断，避免与任务合计不一致）
    for r in rules:
        if r.get("risk"):
            r["waste"] = 0.0
            continue
        r["waste"] = round(sum((t.get("wasteByRule") or {}).get(r["id"], 0.0) for t in tasks), 1)

    return {
        "rules": rules,
        "totalWaste": round(total_waste, 1),
        "anomalyIds": sorted(anomaly_ids, key=lambda i: -(by_id[i].get("waste") or 0)),
        "hitCount": sum(1 for t in tasks if t.get("flagIds")),
    }


def _task_waste(t, rule_id, cfg, tasks):
    """按单任务重算某条规则的浪费，保证规则合计 = 任务合计。"""
    steps = t.get("steps") or []
    if rule_id == "CONTEXT_BLOAT":
        th = int((cfg.get("CONTEXT_BLOAT") or {}).get("single_step_input_tokens", 100000))
        w = 0.0
        for s in steps:
            if s.get("kind") == "model" and (s.get("inTok") or 0) > th and (s.get("credits") or 0) > 0:
                w += s["credits"] * (s["inTok"] - th) / float(s["inTok"])
        return w
    if rule_id == "TASK_CREDIT_SPIKE":
        k = float((cfg.get("TASK_CREDIT_SPIKE") or {}).get("p95_multiplier", 2))
        vals = [x.get("credits") or 0 for x in tasks if (x.get("type") or "") == (t.get("type") or "")]
        base = _p95(vals)
        return max(0.0, (t.get("credits") or 0) - base) if base else 0.0
    if rule_id in ("RETRY_STORM", "TOOL_LOOP"):
        model_steps = [s for s in steps if s.get("kind") == "model"]
        avg = (sum(s.get("credits") or 0 for s in model_steps) / len(model_steps)) if model_steps else 0
        tools = [s for s in steps if s.get("kind") == "tool"]
        fails = sum(1 for s in tools if s.get("err"))
        if rule_id == "RETRY_STORM":
            return avg * min(fails, 3) * 0.5 if fails else 0.0
        th = int((cfg.get("TOOL_LOOP") or {}).get("repeat_threshold", 3))
        same_args = bool((cfg.get("TOOL_LOOP") or {}).get("require_same_args", True))
        run, prev, extra = 0, None, 0
        for s in tools:
            key = (s.get("tool"), s.get("arg") if same_args else "")
            if key == prev:
                run += 1
                if run >= th:
                    extra += 1
            else:
                run, prev = 1, key
        return avg * extra
    if rule_id == "FAILED_BURN":
        return t.get("credits") or 0
    return 0.0
