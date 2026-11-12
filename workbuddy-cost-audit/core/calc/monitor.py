#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务监测：把「平时看不见的执行」单独拎出来看。

监测四类
--------
1. 定时任务执行   自动化配置触发的执行，含 ``runKind=missed`` 的补跑；
2. 后台自动化     会话被标记为后台自动化（is_background_automation）
3. 技能/子代理链  调用了 Skill / MCP / DeferExecuteTool / Agent 等外部能力的执行；
4. 隐藏调用       非 chat 类型的模型请求——话题生成（conversation_topic）、
                  上下文压缩（compress）这类用户完全无感的调用。

每个任务只归入一个类别（优先级：隐藏 > 定时 > 后台 > 技能 > 人工常规），
因此各类别积分与总消耗严格可加，不会重复计数。
"""

import datetime as dt
import json
from collections import Counter, defaultdict

HIDDEN_TYPES = ("conversation_topic", "compress", "summary", "title", "compact")
SKILL_TOOLS = ("Skill", "DeferExecuteTool", "Agent", "Task", "TaskOutput")
CAT_ORDER = ("hidden", "scheduled", "background", "skill")

CAT_META = {
    "scheduled": {
        "name": "定时任务执行",
        "desc": "由定时任务配置触发，执行记录散落在 automation_runs 与会话里",
        "why": "触发时不弹窗，只在结果里出现，容易漏看失败与补跑",
        "tone": "amber",
    },
    "background": {
        "name": "后台自动化",
        "desc": "会话被标记为后台自动化（is_background_automation）",
        "why": "不进入常规会话列表，消耗只在日志里可见",
        "tone": "purple",
    },
    "skill": {
        "name": "技能 / 子代理链",
        "desc": "调用 Skill、MCP 工具或派生子代理完成的工作",
        "why": "真实消耗发生在被调起的技能内部，主会话只看到一行结果",
        "tone": "cyan",
    },
    "hidden": {
        "name": "隐藏调用",
        "desc": "话题生成、上下文压缩等非 chat 请求",
        "why": "用户完全不感知，却按同样费率扣积分",
        "tone": "red",
    },
    "visible": {
        "name": "人工常规",
        "desc": "用户手动发起的主链路执行",
        "why": "在会话里可见，本页只做分母对比",
        "tone": "blue",
    },
}


def _is_skill(tool):
    t = tool or ""
    return t in SKILL_TOOLS or t.startswith("mcp__")


def _cat_of(task):
    rtype = (task.get("rtype") or "chat").lower()
    if rtype in HIDDEN_TYPES:
        return "hidden"
    trig = task.get("trigger") or "人工"
    if trig == "定时":
        return "scheduled"
    if trig == "后台自动化":
        return "background"
    if any(_is_skill(s.get("tool")) for s in task.get("steps") or []):
        return "skill"
    return "visible"


def _to_dt(v):
    """兼容 epoch 毫秒（数据库）与 ISO 文本（测试夹具）。"""
    if v in (None, ""):
        return None
    try:
        n = float(v)
        if n > 1e11:
            n = n / 1000.0
        return dt.datetime.fromtimestamp(n)
    except (TypeError, ValueError):
        pass
    s = str(v).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _ts_text(v):
    d = _to_dt(v)
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else (str(v) if v else None)


DAY_CN = {"MO": "一", "TU": "二", "WE": "三", "TH": "四", "FR": "五", "SA": "六", "SU": "日"}


def rrule_text(rrule, schedule_type=None):
    """把 rrule 翻译成一句人话；解析失败时原样返回。"""
    s = (rrule or "").strip()
    if not s:
        return "—"
    parts = {}
    for kv in s.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.upper()] = v
    freq = (parts.get("FREQ") or "").upper()
    hh = parts.get("BYHOUR")
    mm = parts.get("BYMINUTE", "0")
    time_txt = ("%s:%s" % (str(hh).zfill(2), str(mm).zfill(2))) if hh else ""
    if freq == "WEEKLY":
        days = "".join("周" + DAY_CN.get(d, d) for d in (parts.get("BYDAY") or "").split(",") if d)
        return ("每%s %s" % (days, time_txt)).strip()
    if freq == "DAILY":
        return ("每天 %s" % time_txt).strip()
    if freq == "HOURLY":
        return "每 %s 小时" % parts.get("INTERVAL", "1")
    if freq == "MONTHLY":
        return ("每月 %s 日 %s" % (parts.get("BYMONTHDAY", "?"), time_txt)).strip()
    return s


def _records(tasks):
    out = []
    for t in tasks:
        cat = _cat_of(t)
        if cat == "visible":
            continue
        out.append({
            "ts": t.get("ts"), "cat": cat, "name": t.get("name"),
            "taskId": t.get("id"), "credits": round(t.get("credits") or 0, 2),
            "steps": len(t.get("steps") or []), "status": t.get("status"),
            "model": t.get("mainChannel"), "level": t.get("level"),
            "trigger": t.get("trigger"), "rtype": t.get("rtype") or "chat",
            "duration": t.get("duration"),
        })
    out.sort(key=lambda r: (r["ts"] or ""), reverse=True)
    return out


def _auto_runs(ctx, tasks):
    """把 automation_runs 展开成可读记录，并关联到具体任务。"""
    by_session = defaultdict(list)
    for t in tasks:
        by_session[t.get("session")].append(t)
    names = {(a.get("id")): a.get("name") for a in (ctx.get("db_automations") or [])}
    rows = []
    for aid, runs in (ctx.get("db_runs") or {}).items():
        for r in runs or []:
            meta = {}
            try:
                import json
                meta = json.loads(r.get("meta") or "{}")
            except ValueError:
                meta = {}
            sid = meta.get("sessionId") or meta.get("conversationId")
            linked = by_session.get(sid) or []
            rows.append({
                "automationId": aid,
                "name": names.get(aid) or meta.get("resultTitle") or aid[:8],
                "ts": _ts_text(r.get("created_at")),
                "kind": meta.get("runKind") or "scheduled",
                "missedAt": _ts_text(meta.get("missedScheduledAt")),
                "status": r.get("status"),
                "resultState": meta.get("resultState"),
                "evidence": meta.get("resultEvidence"),
                "interrupted": bool(meta.get("interrupted")),
                "sessionOutcome": meta.get("sessionOutcome"),
                "sessionId": sid,
                "taskId": linked[0].get("id") if linked else None,
                "credits": round(sum(t.get("credits") or 0 for t in linked), 2) if linked else None,
                "linkedTasks": len(linked),
            })
    rows.sort(key=lambda r: (r["ts"] or ""), reverse=True)
    return rows


def _configs(ctx, tasks, automations, runs=None):
    """定时任务配置 + 窗口内实测执行情况。"""
    out = []
    last_by_auto = {}
    for r in (runs or []):
        ts = r.get("ts")
        if ts and ts > last_by_auto.get(r.get("automationId"), ""):
            last_by_auto[r.get("automationId")] = ts
    for a in (ctx.get("db_automations") or []):
        name = a.get("name") or ""
        matched = [t for t in tasks
                   if (t.get("trigger") or "") != "人工" and name[:12] and name[:12] in (t.get("name") or "")]
        runs = len(matched)
        credits = sum(t.get("credits") or 0 for t in matched)
        out.append({
            "name": name,
            "freq": rrule_text(a.get("rrule"), a.get("schedule_type")),
            "rrule": a.get("rrule") or "",
            "status": a.get("status") or "—",
            # last_run_at 常为空，回落到 automation_runs 里最新一次执行
            "lastRun": _ts_text(a.get("last_run_at")) or last_by_auto.get(a.get("id")),
            "createdAt": _ts_text(a.get("created_at")),
            "model": a.get("model") or "—",
            "cwds": a.get("cwds") or "",
            "runs": runs,
            "credits": round(credits, 1),
            "avg": round(credits / runs, 1) if runs else 0,
            "fromDb": True,
        })
    seen = {c["name"] for c in out}
    for s in automations or []:
        if s.get("name") in seen:
            continue
        out.append({
            "name": s.get("name"), "freq": s.get("freq") or "—", "rrule": "",
            "status": "—", "lastRun": None, "createdAt": None,
            "model": s.get("model") or "—", "cwds": "",
            "runs": s.get("runs") or 0, "credits": round((s.get("avg") or 0) * (s.get("runs") or 0), 1),
            "avg": s.get("avg") or 0, "fromDb": False,
        })
    out.sort(key=lambda c: -(c["credits"] or 0))
    return out


def _skills(tasks):
    call = Counter()
    task_ids = defaultdict(set)
    for t in tasks:
        for s in t.get("steps") or []:
            tool = s.get("tool")
            if s.get("kind") == "tool" and _is_skill(tool):
                call[tool] += 1
                task_ids[tool].add(t.get("id"))
    rows = []
    for tool, n in call.most_common(20):
        rows.append({"name": tool, "calls": n, "tasks": len(task_ids[tool])})
    return rows


def build(tasks, ctx, automations=None, days=1):
    tasks = tasks or []
    ctx = ctx or {}
    task_credits = sum(t.get("credits") or 0 for t in tasks) or 0.0
    cat_stats = defaultdict(lambda: {"count": 0, "credits": 0.0})
    for t in tasks:
        c = _cat_of(t)
        cat_stats[c]["count"] += 1
        cat_stats[c]["credits"] += t.get("credits") or 0

    # 隐藏调用（话题生成 / 上下文压缩）不在任务台账里，来自适配器的系统调用明细
    syscalls = ((ctx.get("system") or {}).get("calls_detail") or [])
    hidden_credits = sum(c.get("credits") or 0 for c in syscalls)
    if syscalls:
        cat_stats["hidden"] = {"count": len(syscalls), "credits": hidden_credits}

    universe = sum(v["credits"] for v in cat_stats.values()) or 0.0

    cats = []
    for cid in ("scheduled", "background", "skill", "hidden", "visible"):
        st = cat_stats.get(cid) or {"count": 0, "credits": 0.0}
        meta = CAT_META[cid]
        cats.append({
            "id": cid, "name": meta["name"], "desc": meta["desc"], "why": meta["why"],
            "tone": meta["tone"], "count": st["count"],
            "credits": round(st["credits"], 1),
            "share": round(st["credits"] / universe, 4) if universe else 0,
            "avg": round(st["credits"] / st["count"], 1) if st["count"] else 0,
        })

    unseen = [c for c in cats if c["id"] in CAT_ORDER]
    unseen_count = sum(c["count"] for c in unseen)
    unseen_credits = sum(c["credits"] for c in unseen)

    runs = _auto_runs(ctx, tasks)
    skill_tasks = [t for t in tasks if _cat_of(t) == "skill"]
    subagent = [t for t in tasks if any(
        (s.get("tool") in ("Agent", "Task", "TaskOutput")) for s in t.get("steps") or [])]

    HIDDEN_NAME = {"conversation_topic": "对话话题生成", "compress": "上下文压缩",
                   "summary": "会话摘要", "title": "标题生成"}
    hidden_rows = [{
        "ts": c.get("ts"), "name": HIDDEN_NAME.get(c.get("rtype"), c.get("rtype") or "隐藏调用"),
        "taskId": None, "rtype": c.get("rtype"), "credits": round(c.get("credits") or 0, 2),
        "inTok": c.get("inTok") or 0, "outTok": c.get("outTok") or 0,
        "duration": round((c.get("ms") or 0) / 1000.0, 1),
        "model": c.get("model"), "est": bool(c.get("est")),
        "session": (c.get("session") or "")[:8],
    } for c in sorted(syscalls, key=lambda x: -(x.get("credits") or 0))]

    missed = sum(1 for r in runs if (r.get("kind") or "") == "missed")
    interrupted = sum(1 for r in runs if r.get("interrupted"))
    failed_runs = sum(1 for r in runs if (r.get("resultState") or "") not in
                      ("delivered", "empty", "", None))

    notes = []
    if not runs:
        notes.append("窗口内没有 automation_runs 记录，定时任务只能按会话推断。")
    if missed:
        notes.append("检测到 %d 次「错过计划时间后补跑」的执行（runKind=missed），"
                     "这类执行不会出现在常规会话列表里。" % missed)
    if hidden_rows:
        est = sum(1 for h in hidden_rows if h.get("est"))
        notes.append("隐藏调用 %d 次、折算 %.1f 积分：客户端通常不为这类请求上报积分，"
                     "金额按本机实测每千 token 折算率估算%s。"
                     % (len(hidden_rows), hidden_credits,
                        "（%d 次为估算）" % est if est else ""))
    else:
        notes.append("窗口内没有话题生成 / 上下文压缩类隐藏调用。")
    if not skill_tasks:
        notes.append("窗口内没有技能或子代理调用记录。")
    notes.append("任务侧四类互斥归类（定时 > 后台 > 技能 > 人工），积分合计可加；"
                 "隐藏调用单独计入，不与任务重复。")

    return {
        "categories": cats,
        "totals": {
            "tasks": len(tasks),
            "credits": round(universe, 1),
            "taskCredits": round(task_credits, 1),
            "unseenTasks": unseen_count,
            "unseenCredits": round(unseen_credits, 1),
            "unseenShare": round(unseen_credits / universe, 4) if universe else 0,
            "runs": len(runs),
            "missed": missed,
            "interrupted": interrupted,
            "failedRuns": failed_runs,
            "hiddenCalls": len(hidden_rows),
            "hiddenCredits": round(hidden_credits, 1),
            "skillTasks": len(skill_tasks),
            "skillCredits": round(sum(t.get("credits") or 0 for t in skill_tasks), 1),
            "subagentTasks": len(subagent),
        },
        "records": _records(tasks)[:200],
        "autoRuns": runs,
        "configs": _configs(ctx, tasks, automations, runs),
        "skills": _skills(tasks),
        "hidden": hidden_rows[:60],
        "notes": notes,
        "sufficient": bool(runs or hidden_rows or skill_tasks),
        "windowDays": days,
    }
