#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""积分监测：人工设置余额 + 当日实时消耗 + 预警比阈值告警 + 对账。

模型（只有两个人工输入，其余全部实时算）
--------------------------------------
    初始积分          ← 人工设置（只做「本期已用掉多少」的参考口径）
    今日余额          ← 人工设置（当日开始时的余额）
    今日消耗          ← 日志实时汇总（当日全部任务积分）
    当前余额          ← 今日余额 − 今日消耗
    本期消耗比例      ← (初始积分 − 当前余额) / 初始积分     （参考值，不触发告警）
    预警比            ← 今日消耗 / 当前余额 × 100          （告警判定用）
    告警级别          ← 预警比 ≥ 红/橙/黄 阈值；未超过黄色即绿色

明确**不做**的事：不做自动截屏、不做余额自动抓取、不做可用天数或月度预估。
"""

import csv
import datetime as dt
import io
import json
import os
from collections import defaultdict

from .. import policy as bs

TYPE_INCOME = ("recharge", "grant", "refund")
TYPE_CONSUME = ("consume",)
VALID_TYPES = TYPE_INCOME + TYPE_CONSUME + ("expire",)

SOURCE_LABEL = {"manual": "人工设置", "none": "未设置"}

INITIAL_SOURCE_TEXT = {
    "measured": "实测（有截图佐证）",
    "derived": "反推（由实测余额逐日倒推）",
    "manual": "手工填写（无凭证）",
}


def _snapshot_out(state):
    """把「今日余额对应的那张凭证」整理成前端可直接渲染的结构。"""
    snap = state.get("today_balance_snapshot")
    if not isinstance(snap, dict) or not snap:
        return None
    st = bs.snapshot_state(snap)
    return {
        "state": st,
        "stateText": bs.SNAPSHOT_STATE_TEXT.get(st, st),
        "file": snap.get("file"),
        "sha256": snap.get("sha256"),
        "takenAt": snap.get("taken_at"),
        "account": snap.get("account"),
        "dayOpen": snap.get("day_open"),
        "ref": snap.get("ref"),
        "staleReason": snap.get("stale_reason"),
    }


def _parse_ts(v):
    if v is None:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo:
            d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return d
    except ValueError:
        return None


def load_ledger(paths, log=None):
    """读取本地积分流水（jsonl 或 csv）。只解析 ts/type/amount/balance_after/task_id。"""
    entries = []
    for p in paths or []:
        if not p:
            continue
        path = os.path.expanduser(p)
        if not os.path.exists(path):
            if log:
                log("流水文件不存在，跳过：%s" % path)
            continue
        try:
            if path.lower().endswith(".csv"):
                with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                    for row in csv.DictReader(f):
                        e = _norm_entry(row)
                        if e:
                            entries.append(e)
            else:
                with io.open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = _norm_entry(json.loads(line))
                        except ValueError:
                            continue
                        if e:
                            entries.append(e)
        except OSError as e:
            if log:
                log("流水读取失败：%s（%s）" % (path, e))
    entries.sort(key=lambda x: x["ts"])
    return entries


def _norm_entry(d):
    ts = _parse_ts(d.get("ts") or d.get("time") or d.get("timestamp"))
    typ = (d.get("type") or "").strip().lower()
    if not ts or typ not in VALID_TYPES:
        return None
    try:
        amount = float(d.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    bal = d.get("balance_after")
    try:
        bal = float(bal) if bal not in (None, "") else None
    except (TypeError, ValueError):
        bal = None
    return {"ts": ts, "type": typ, "amount": amount, "balance_after": bal,
            "task_id": d.get("task_id") or None}


def _sum_window(tasks, start_day, end_day):
    out = defaultdict(float)
    for t in tasks:
        d = (t.get("day") or "")[:10]
        if start_day <= d <= end_day:
            out[d] += t.get("credits") or 0
    return out


def compute(cfg, tasks, ctx=None, ledger_paths=None, balance_override=None,
            balance_date=None, thresholds=None, log=None, state=None):
    cfg = cfg or {}
    ctx = ctx or {}
    tasks = tasks or []
    state = state if state is not None else bs.load()
    if thresholds:
        st = json.loads(json.dumps(state))
        th_raw = dict(st.get("thresholds") or {})
        th_raw.update({k: v for k, v in thresholds.items() if v is not None})
        st["thresholds"] = th_raw
        state = st
    th = bs.thresholds_of(state)

    days = sorted({(t.get("day") or "")[:10] for t in tasks if t.get("day")})
    data_anchor = days[-1] if days else dt.date.today().isoformat()
    # 结算锚点：取「数据最后一天」与「今日余额日期」中较新的一天。
    # 为什么：当日刚填余额、当天还没有任务时，若仍按昨天的数据结算，会把昨天的消耗
    # 再从今天的余额里扣一次（重复扣减）。余额日期比数据新，就以余额日期结算，
    # 当日消耗自然为 0，等式两边同一天。上界夹到日历今天，防止手填未来日期。
    tb_raw = (state.get("today_balance_date") or "")[:10]
    today_cal = dt.date.today().isoformat()
    anchor = data_anchor
    if tb_raw and data_anchor < tb_raw <= today_cal:
        anchor = tb_raw
    anchor_d = dt.date.fromisoformat(anchor)

    # ---- 实测消耗（只陈述事实，不做任何外推）
    last7 = [(anchor_d - dt.timedelta(days=i)).isoformat() for i in range(7)]
    win7 = _sum_window(tasks, last7[-1], last7[0])
    daily7 = sum(win7.values()) / 7.0
    active_days = [d for d in last7 if win7.get(d)]
    active_avg = (sum(win7.values()) / len(active_days)) if active_days else 0.0
    today_spent = round(_sum_window(tasks, anchor, anchor).get(anchor, 0.0), 1)
    p95_task = _p95([t.get("credits") or 0 for t in tasks])
    window_spent = round(sum(t.get("credits") or 0 for t in tasks), 1)

    # ---- 人工设置的余额
    initial = state.get("initial_credits")
    today_balance = state.get("today_balance")
    tb_date = (state.get("today_balance_date") or "")[:10]
    if balance_override is not None:
        # 临时试算：直接指定当前余额，不写回状态文件
        today_balance = float(balance_override) + today_spent
        tb_date = balance_date or anchor
    current = (round(float(today_balance) - today_spent, 1)
               if today_balance is not None else None)
    consume_pct = None
    if initial and current is not None and float(initial) > 0:
        consume_pct = round((float(initial) - current) / float(initial) * 100, 2)

    configured = initial is not None and today_balance is not None
    # 预警比 = 今日消耗 / 当前余额；余额见底或已为负时视为「远超」
    spend_ratio = None
    if configured:
        if current is None or current <= 0:
            spend_ratio = float("inf") if today_spent > 0 else None
        else:
            spend_ratio = round(today_spent / current * 100, 2)
    level, level_reason = bs.level_of(spend_ratio, th)
    exit_code = {"red": 4, "crit": 3, "warn": 2}.get(level, 0)
    stale = bool(tb_date and tb_date != anchor)
    ratio_out = None
    ratio_over = False
    if spend_ratio is not None:
        if spend_ratio == float("inf"):
            ratio_over = True          # 今日消耗已超过当前余额，前端显示「>100%」
        else:
            ratio_out = spend_ratio

    # ---- 告警清单（总览页使用）
    alerts = []
    if not configured:
        alerts.append({
            "type": "credit", "level": "gray", "title": "积分未配置",
            "desc": "未设置初始积分或今日余额，无法计算当前余额与告警。"
                    "在「消耗台账 → 耗支流水 → 积分监测」里填写即可。",
        })
    elif level in ("red", "crit", "warn"):
        alerts.append({
            "type": "credit", "level": level,
            "title": bs.LEVEL_TEXT.get(level, "告警") + "：今日消耗偏高",
            "desc": "%s；当前余额 %s 积分（今日余额 %s − 今日消耗 %s）。"
                    % (level_reason, fmt_num(current), fmt_num(today_balance),
                       fmt_num(today_spent)),
        })
    else:
        alerts.append({
            "type": "credit", "level": "ok", "title": "积分消耗正常",
            "desc": "%s；当前余额 %s 积分。" % (level_reason, fmt_num(current)),
            "ok": True,
        })
    failed = [t for t in tasks if (t.get("status") or "成功") != "成功"]
    if failed:
        alerts.append({
            "type": "task_failed", "level": "amber",
            "title": "任务执行未成功 %d 个" % len(failed),
            "desc": "失败任务合计消耗 %s 积分，占窗口 %s。点击「消耗台账」可逐条下钻。"
                    % (fmt_num(sum(t.get("credits") or 0 for t in failed)),
                       ("%.1f%%" % (sum(t.get("credits") or 0 for t in failed)
                                    / window_spent * 100)) if window_spent else "—"),
            "tasks": [{"id": t.get("id"), "name": t.get("name"),
                       "credits": round(t.get("credits") or 0, 1)} for t in failed[:8]],
        })
    slowed = [t for t in tasks if (t.get("retry") or 0) >= 5]
    if slowed:
        alerts.append({
            "type": "retry", "level": "amber",
            "title": "重复重试任务 %d 个" % len(slowed),
            "desc": "单任务重试 ≥5 次，多为工具调用失败后反复尝试，可优先排查。",
            "tasks": [{"id": t.get("id"), "name": t.get("name"),
                       "credits": round(t.get("credits") or 0, 1)} for t in slowed[:8]],
        })

    return {
        "source": "manual" if configured else "none",
        "sourceLabel": SOURCE_LABEL["manual" if configured else "none"],
        "configured": configured,
        "initial": round(float(initial), 1) if initial is not None else None,
        "initialAt": state.get("initial_set_at"),
        "initialSource": state.get("initial_source") or "manual",
        "initialSourceText": INITIAL_SOURCE_TEXT.get(
            state.get("initial_source") or "manual", "手工填写（无凭证）"),
        "snapshot": _snapshot_out(state),
        "todayBalance": round(float(today_balance), 1) if today_balance is not None else None,
        "todayBalanceDate": tb_date or None,
        "todayBalanceAt": state.get("today_balance_set_at"),
        "todayBalanceStale": stale,
        "today": anchor,
        "todaySpent": today_spent,
        "balance": current,                      # 当前余额 = 今日余额 − 今日消耗
        "balanceAt": tb_date or anchor,
        "consumePct": consume_pct,               # 本期消耗比例（初始积分口径，仅参考）
        "spendRatio": ratio_out,                 # 预警比 = 今日消耗 / 当前余额 × 100
        "spendRatioOver": ratio_over,            # True 表示今日消耗已超过当前余额
        "level": level,
        # 未配置时不给「正常」这种暗示性措辞，只说未配置
        "levelText": bs.LEVEL_TEXT[level] if (configured and level) else "未配置",
        "exitCode": exit_code,
        "reason": level_reason,
        "thresholds": {"mode": "pct", "warn": th["warn"], "crit": th["crit"],
                       "red": th["red"]},
        "alerts": alerts,
        "windowSpent": window_spent,
        "rate": _rate(tasks, anchor_d, today_spent, daily7, active_avg),
        "dailyAvg7": round(daily7, 2),
        "activeDayAvg": round(active_avg, 2),
        "p95Task": round(p95_task, 1),
        "reconcile": _reconcile(tasks, ctx, load_ledger(
            ledger_paths if ledger_paths is not None
            else (cfg.get("credit_ledger_paths") or []), log=log), cfg, log),
        "modelSwitch": _model_switch(tasks, load_ledger(
            ledger_paths if ledger_paths is not None
            else (cfg.get("credit_ledger_paths") or []), log=None),
            today_balance, p95_task, daily7),
        "series": [{"date": d, "credits": round(_sum_window(tasks, d, d).get(d, 0), 1)}
                   for d in days],
        "anchor": anchor,
    }


def fmt_num(v):
    if v is None:
        return "—"
    return "{:,.0f}".format(round(float(v)))


def _p95(values):
    vs = sorted(values or [])
    if not vs:
        return 0.0
    idx = min(len(vs) - 1, int(round(len(vs) * 0.95)) - 1)
    return vs[max(0, idx)]


def _rate(tasks, anchor, today_spend, daily7, active_avg):
    """实测速率：今日、近 7 日、近 3 日 vs 前 7 日变化。不含任何预测。"""
    def total(a, b):
        return sum(t.get("credits") or 0 for t in tasks
                   if a <= (t.get("day") or "")[:10] <= b)

    d3a = (anchor - dt.timedelta(days=2)).isoformat()
    prev_a = (anchor - dt.timedelta(days=13)).isoformat()
    prev_b = (anchor - dt.timedelta(days=7)).isoformat()
    recent3 = total(d3a, anchor.isoformat()) / 3.0
    prev7 = total(prev_a, prev_b) / 7.0
    change = round((recent3 - prev7) / prev7, 4) if prev7 > 0 else None
    return {
        "today": round(today_spend, 1),
        "d3Avg": round(recent3, 2),
        "d7Avg": round(daily7, 2),
        "prev7Avg": round(prev7, 2),
        "activeAvg": round(active_avg, 2),
        "changePct": change,
        "alert": bool(change is not None and change > 0.5),
        "note": ("近 3 日日均较前 7 日 %s%.0f%%（%.1f vs %.1f 积分/天）"
                 % ("+" if change and change > 0 else "", (change or 0) * 100,
                    recent3, prev7)) if change is not None else "缺少对比窗口，无法判断速率变化",
    }


def _reconcile(tasks, ctx, ledger, cfg, log):
    """积分对账：日志口径 vs 第二来源（流水 / 会话积分表）。"""
    tol = float(cfg.get("reconcile_tolerance_pct", 5))
    per_request = (ctx or {}).get("per_request") or {}
    usage = (ctx or {}).get("usage") or {}
    by_rid = {}
    for t in tasks or []:
        if t.get("rid"):
            by_rid[t["rid"]] = {"taskId": t.get("id"), "name": t.get("name"),
                                "day": t.get("day") or "",
                                "credits": round(t.get("credits") or 0, 2)}
    total_log = round(sum(per_request.values()), 1)

    if ledger:
        consume = [e for e in ledger if e["type"] == "consume"]
        ledger_sum = round(sum(-e["amount"] if e["amount"] < 0 else e["amount"]
                               for e in consume), 1)
        diff = (total_log - ledger_sum) / ledger_sum if ledger_sum else None
        return {"available": True, "mode": "ledger_vs_logs", "label": "积分流水 vs 日志",
                "ledger": ledger_sum, "logs": total_log,
                "diffPct": round(diff * 100, 2) if diff is not None else None,
                "tolerance": tol,
                "alert": bool(diff is not None and abs(diff) * 100 > tol),
                "rows": [], "note": "流水覆盖窗口不足时差异仅供参考"}

    overlap = set(per_request) & {k for v in usage.values() for k in v}
    if not overlap:
        return {"available": False, "mode": "none", "label": "无第二来源",
                "logs": total_log, "diffPct": None, "tolerance": tol,
                "alert": False, "rows": [],
                "note": "未提供积分流水，且会话积分表与日志无交集，无法对账"}

    db_sum = sum(v[k] for v in usage.values() for k in v if k in overlap)
    log_sum = sum(per_request[k] for k in overlap)
    diff = (log_sum - db_sum) / db_sum if db_sum else None
    rows = []
    for k in overlap:
        second = usage_get(usage, k)
        d = round(per_request[k] - second, 2)
        meta = by_rid.get(k) or {}
        rows.append({"rid": k, "taskId": meta.get("taskId"), "name": meta.get("name") or k,
                     "day": meta.get("day") or "",
                     "logs": round(per_request[k], 2), "second": round(second, 2),
                     "diff": d, "diffPct": round(d / second * 100, 1) if second else None})
    rows.sort(key=lambda r: -abs(r["diff"]))
    return {"available": True, "mode": "session_table", "label": "日志 vs 会话积分表",
            "ledger": round(db_sum, 1), "logs": round(log_sum, 1),
            "diffPct": round(diff * 100, 2) if diff is not None else None,
            "tolerance": tol, "alert": bool(diff is not None and abs(diff) * 100 > tol),
            "overlap": len(overlap), "matched": len(rows),
            "rows": rows[:60],
            "note": "会话积分表仅覆盖部分会话，差异用于校验折算口径，不等于账户余额误差"}


def usage_get(usage, key):
    for v in usage.values():
        if key in v:
            return v[key]
    return 0.0


def _model_switch(tasks, ledger, balance, p95_task, daily7):
    switched = [t for t in tasks if len(set(
        s.get("model") for s in t.get("steps", [])
        if s.get("kind") == "model" and s.get("model"))) > 1]
    ratio = len(switched) / len(tasks) if tasks else 0

    high = []
    if ledger and balance is not None:
        timeline = [(e["ts"], e["balance_after"]) for e in ledger
                    if e["balance_after"] is not None]
        for t in switched:
            ts = _parse_ts(t.get("ts"))
            if not ts or not timeline:
                continue
            prev = [b for (x, b) in timeline if x <= ts]
            b = prev[-1] if prev else timeline[0][1]
            if b < max(p95_task, daily7 * 3):
                high.append({"task": t.get("id"), "name": t.get("name"),
                             "chain": t.get("modelChain"), "balance": round(b, 1)})

    return {
        "count": len(switched), "ratio": round(ratio, 4),
        "highConfidence": len(high), "items": high[:10],
        "tasks": [{"task": t.get("id"), "name": t.get("name"), "ts": t.get("ts"),
                   "chain": t.get("modelChain"), "credits": t.get("credits")}
                  for t in sorted(switched, key=lambda x: -(x.get("credits") or 0))[:10]],
        "note": ("有流水时间线，可判定切换是否落在低余额窗口" if ledger
                 else "无流水时间戳，只能给出切换次数，无法判定是否由余额驱动"),
    }
