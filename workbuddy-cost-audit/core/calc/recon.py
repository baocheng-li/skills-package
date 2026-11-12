#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日终积分对账（T+1）引擎 + 定时任务脚本。

一天一条账，逐日结转
--------------------
    日收积分   ← 当日领取 / 购买的积分（积分流水里的 recharge / grant / refund）
    日初积分   = 日收积分 + 上一日日结余额        ← 规则 1：上日日结自动转入日初
    日耗积分   ← 当日全部任务的实测消耗（本机日志汇总）
    日结余额   = 日初积分 − 日耗积分
    账户余额   ← 客户端实际余额（流水 balance_after 优先，其次人工填的今日余额）

对账口径
--------
「账户余额 = 当日消耗积分 + 当日结余」与「账户余额 = 日初积分 − 日耗积分」是同一个
等式（因为 日结余额 = 日初积分 − 日耗积分）。落地时取后者做校验：

    偏差 = 账户余额 − 日结余额
    |偏差| ≤ max(0.5 积分, |日初积分| × 容差%)  → 账实一致
    否则                                         → 存在偏差，按行标红

T+1 语义
--------
T 日的账在 **T+1 日 00:00** 才对（因为 T 日当天还没过完，日志仍在写入）。
脚本由系统计划任务在 00:00 触发，所以 ``status`` 有两种「还没对」：

    pending   T+1 00:00 未到 —— 当天还没到对账时点
    nobal     时点已到，但拿不到真实账户余额 —— 只能给出账面值

结转规则（规则 1 的落地细节）
-----------------------------
对账一致的日：``日结余额`` 原样结转到次日 ``上一日日结余额``；
对账有偏差的日：**以账实为准**，把 ``账户余额`` 结转到次日，并把差额记为调账金额，
               同时在该行标红，避免把错误余额一路带下去。

收入侧的可信度（v3）
--------------------
收入由**单人**登记，不再设「复核人」卡点。可审计性由三件事承担：

    snapshot    凭证复制进 config/vouchers/，SHA256 写进台账，可随时复算；
    revisions   每一次 CREATE/EDIT/SUBMIT/REOPEN/VOID 的 before/after；
    entry log   income-entry-log.jsonl 追加式日志，带哈希链（prev_hash）

本模块只读这些痕迹并据此算 ``incomeQuality``（快照覆盖率 / 留痕覆盖率 /
无快照入账数 / 篡改数），写入动作一律走 ``hosts/commands/income_entry.py``。

命令行
------
    python3 -m hosts.commands.recon_daily                       # 打印当日对账摘要
    python3 -m hosts.commands.recon_daily --save out/recon.json # 落盘完整结果
    python3 -m hosts.commands.recon_daily --print-schedule      # 生成 T+1 0:00 定时任务
    python3 -m hosts.commands.recon_daily --check               # 仅有偏差时退出码 2
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sys
from pathlib import Path

# 包内相对导入：core.calc 是包，用 `..` 回到 core 取策略、
# 用 `.` 取同层的计算模块。不依赖 sys.path 顺序，被哪个宿主 import 都一样。
from .. import policy as bs          # noqa: E402
from . import budget as budget_mod   # noqa: E402
from . import txn as txn_mod         # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE = ROOT / "config" / "balance-state.json"
DEFAULT_INCOME = ROOT / "config" / "credit-income.json"

INCOME_TYPES = {"recharge": "充值", "grant": "领取/赠送", "refund": "退还"}
STATUS_TEXT = {
    "matched": "账实一致",
    "diff": "存在偏差",
    "pending": "待 T+1 对账",
    "nobal": "无实际余额",
}

# v2.1 §3.2：收入的确认状态 —— 只有「已入账」才进入账本、参与日结计算值；
# 草稿（未提交）只做实时余额展示用；退回（已冲正）既不计入也不影响余额。
# 注意：pending 这一档在 v3 里表示「草稿」，老数据里的 待复核 写法仍归到同一档（兼容）。
CONFIRMED_SET = ("confirmed", "posted", "已入账", "已复核", "已确认")
PENDING_SET = ("pending", "draft", "待确认", "待复核", "草稿")
VOID_SET = ("void", "rejected", "退回", "已冲正", "已退回")


def income_status(entry):
    """把各种写法归一化成 confirmed / pending / void 三档。"""
    s = str(entry.get("status") or entry.get("entry_status") or "").strip().lower()
    if not s:
        return "confirmed"          # 老配置没有状态字段，按已入账处理
    if s in CONFIRMED_SET:
        return "confirmed"
    if s in VOID_SET:
        return "void"
    if s in PENDING_SET:
        return "pending"
    return "confirmed"


# ---------------------------------------------------------------- 收入
def _snap_state(snap, base_dir=None):
    """凭证快照状态：ok / nosnap（没附凭证）/ missing（文件不在）/ tampered（哈希不符）。

    只有 base_dir 可知时才做文件级校验——Skill 形态由调用方直接注入归一化后的行，
    此时没有本地凭证目录，只能依据「有没有哈希」判断，不做假的通过。
    """
    snap = snap if isinstance(snap, dict) else {}
    sha = str(snap.get("sha256") or "").strip().lower()
    if not sha:
        return "nosnap"
    rel = str(snap.get("file") or "")
    if not base_dir or not rel:
        return "ok"
    p = os.path.join(base_dir, rel.replace("/", os.sep))
    if not os.path.exists(p):
        return "missing"
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "missing"
    return "ok" if h.hexdigest() == sha else "tampered"


def normalize_income(rows, base_dir=None):
    """把外部传入的收入条目归一化成内部结构。

    调用方可能给的是「原始录入行」（只有 date / type / amount / status），
    也可能是 v3 台账（带 snapshot / revisions）；这里统一补齐 kind（中文类型名）、
    status（三档确认状态）、凭证状态与留痕条数，让 build() 对两种输入都成立。

    v3 起不再有 reviewer：单人录入，可信度由「快照可复算 + 留痕可追溯」承担。
    老数据里的 reviewer 收进 legacyReviewer，只作痕迹保留，不参与任何判定。
    """
    out = []
    for e in rows or []:
        if not isinstance(e, dict):
            continue
        d = str(e.get("date") or e.get("biz_date") or "")[:10]
        if len(d) != 10:
            continue
        try:
            amount = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        typ = str(e.get("type") or "grant").strip().lower()
        snap_obj = e.get("snapshot")
        if isinstance(snap_obj, dict):
            snap = {"sha256": snap_obj.get("sha256") or "",
                    "file": snap_obj.get("file") or "", "bytes": 0}
        elif "snap" in e:
            # 已归一化过的行：用扁平字段还原，保证本函数幂等
            # （上游先归一化一遍再交给 build()，凭证信息不能被洗掉）
            snap = {"sha256": e.get("snapSha") or "", "file": e.get("snapFile") or "",
                    "bytes": 0}
        else:
            snap = {"sha256": e.get("sha256") or "", "file": "", "bytes": 0}
        state = _snap_state(snap, base_dir)
        if state == "ok" and e.get("snap") in ("missing", "tampered"):
            # 上游已判过且更差（当时能读到文件），保留更差的判定，不往上"修好"
            state = e["snap"]
        revs = e.get("revisions") if isinstance(e.get("revisions"), list) else []
        trail_n = len(revs) if revs else int(e.get("trailN") or 0)
        out.append({"date": d, "type": typ,
                    "kind": e.get("kind") or INCOME_TYPES.get(typ, typ),
                    "amount": round(amount, 2), "note": str(e.get("note") or ""),
                    "txn": str(e.get("txn") or ""),
                    "entryId": str(e.get("id") or e.get("entryId") or ""),
                    "bizNo": str(e.get("biz_no") or e.get("bizNo") or ""),
                    "enteredBy": str(e.get("entered_by") or e.get("enteredBy") or ""),
                    "enteredAt": str(e.get("entered_at") or e.get("enteredAt") or ""),
                    "legacyReviewer": str(e.get("reviewer") or e.get("legacyReviewer") or ""),
                    "snap": state,
                    "snapSha": str(snap.get("sha256") or ""),
                    "snapFile": str(snap.get("file") or ""),
                    "trailN": trail_n,
                    "status": income_status(e)})
    out.sort(key=lambda x: (x["date"], x.get("txn") or ""))
    return out


def income_quality(income):
    """收入侧的可信度指标：不看「谁复核了」，看「凭证齐不齐、留痕全不全」。

    这组数字替代原「双人复核率」——复核率只能证明流程走过了，
    快照覆盖率与留痕覆盖率才能证明「这笔账事后核得动」。
    """
    rows = [e for e in (income or []) if isinstance(e, dict)]
    n = len(rows)
    conf = [e for e in rows if e.get("status") == "confirmed"]
    hashed = [e for e in rows if str(e.get("snapSha") or "")]
    ok = [e for e in rows if e.get("snap") == "ok"]
    trailed = [e for e in rows if int(e.get("trailN") or 0) > 0]
    no_snap_conf = [e for e in conf if e.get("snap") == "nosnap"]
    tampered = [e for e in rows if e.get("snap") == "tampered"]
    missing = [e for e in rows if e.get("snap") == "missing"]

    def ratio(part):
        return round(len(part) * 100.0 / n, 1) if n else 100.0

    return {
        "n": n,
        "confirmed": len(conf),
        "draft": len([e for e in rows if e.get("status") == "pending"]),
        "void": len([e for e in rows if e.get("status") == "void"]),
        "hashed": len(hashed),
        "snapshotRate": ratio(hashed),          # 已附凭证（有哈希）的比例
        "evidenceOk": len(ok),
        "evidenceRate": ratio(ok),              # 哈希可复算的比例（含文件在不在、改没改）
        "trailCovered": len(trailed),
        "trailRate": ratio(trailed),
        "noSnapConfirmed": len(no_snap_conf),
        "tampered": len(tampered),
        "missing": len(missing),
        "multiOp": len([e for e in rows if int(e.get("trailN") or 0) > 1]),
        "policy": ("单人上传凭证快照 + 录入记录留痕（v3 起取消双人复核）："
                   "snapshot.sha256 可复算，CREATE/EDIT/SUBMIT/VOID 逐条留痕"),
        "gate": ("入账门槛 = 有快照 + 哈希可复算 + 留痕≥1 条；"
                 "无快照不入账，篡改由 income_entry.py verify 检出"),
    }


def load_income(path=None):
    """读取人工登记的积分收入（json）。

    v3 结构：{"version":3,"entries":[{id,date,type,amount,snapshot,revisions,status}]}；
    v2 老结构（reviewer + 裸 sha256）照样能读，归一化时自动降级处理。
    """
    p = os.path.expanduser(path or str(DEFAULT_INCOME))
    if not os.path.exists(p):
        return []
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    rows = raw.get("entries") if isinstance(raw, dict) else raw
    return normalize_income(rows, base_dir=os.path.dirname(os.path.abspath(p)))


def income_from_ledger(entries):
    """把积分流水里的收入类条目转成收入行。"""
    out = []
    for e in entries or []:
        if e.get("type") not in INCOME_TYPES:
            continue
        ts = e.get("ts")
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        out.append({"date": d, "type": e["type"], "kind": INCOME_TYPES[e["type"]],
                    "amount": round(abs(float(e.get("amount") or 0)), 2),
                    "note": "积分流水", "txn": "", "entryId": "",
                    "bizNo": str(e.get("biz_no") or ""), "enteredBy": "",
                    "enteredAt": "", "legacyReviewer": "",
                    "snap": "ok", "snapSha": "", "snapFile": "", "trailN": 0,
                    "status": "confirmed"})
    return out


# ---------------------------------------------------------------- 对账主逻辑
def build(tasks, ledger=None, state=None, income=None, tolerance_pct=5.0,
          now=None, max_days=60, anchor=None):
    """构建按日对账结果。tasks 需已带 txn 字段。"""
    tasks = tasks or []
    state = state if state is not None else bs.load()
    now = now or dt.datetime.now()
    tol = float(tolerance_pct or 5.0)
    tol_abs = 0.5

    initial = state.get("initial_credits")
    initial = round(float(initial), 2) if initial is not None else None
    initial_date = str(state.get("initial_date") or "")[:10] or None

    # ---- 收入合并：人工登记 + 积分流水
    # 传入的可能是原始录入行（只有 date/type/amount/status），先归一化再合并，
    # 避免上游忘了补 kind 就 KeyError。
    inc_manual = normalize_income(income if income is not None else load_income())
    inc = list(inc_manual) + income_from_ledger(ledger)
    inc_by_day = {}
    for e in inc:
        inc_by_day.setdefault(e["date"], []).append(e)

    # ---- 任务按日聚合
    spend_by_day, tasks_by_day = {}, {}
    for t in tasks:
        d = str(t.get("day") or "")[:10]
        if len(d) != 10:
            continue
        spend_by_day[d] = spend_by_day.get(d, 0.0) + float(t.get("credits") or 0)
        tasks_by_day.setdefault(d, []).append(t)

    # ---- 实际余额：流水 balance_after（当日最后一笔）> 人工今日余额
    bal_by_day = {}                     # date → (ts 字符串, 余额, 来源)
    for e in ledger or []:
        b = e.get("balance_after")
        if b is None:
            continue
        ts = e.get("ts")
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        stamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        prev = bal_by_day.get(d)
        if prev is None or stamp >= prev[0]:
            bal_by_day[d] = (stamp, float(b), "流水 balance_after")
    tb = state.get("today_balance")
    tb_date = str(state.get("today_balance_date") or "")[:10]
    if tb is not None and len(tb_date) == 10:
        # today_balance 是「当日**日初**余额」，而 account 这一列要的是「当日**日末**
        # 实测余额」。直接拿日初去比日结，会凭空差出一整天的消耗——10-26 那个
        # -1221.67、10-29 那个 +71.41 都是这么来的，本质是同一个根因。
        # 折成日末再比：日末 = 日初 − 当日消耗。
        tb_end = round(float(tb) - spend_by_day.get(tb_date, 0.0), 2)
        bal_by_day.setdefault(
            tb_date, (tb_date, tb_end, "人工设置今日余额（日初）− 当日消耗"))

    # ---- 日期轴：有任务或有收入的日子
    days = sorted(set(spend_by_day) | set(inc_by_day))
    if initial_date:
        # 初始积分只对「初始积分日期」及之后的日子有效；更早的日子没有可信起点
        days = [d for d in days if d >= initial_date]
    if anchor and anchor in days:
        days = days[:days.index(anchor) + 1]
    if max_days and len(days) > max_days:
        days = days[-max_days:]

    rows = []
    prev_close = initial if initial is not None else 0.0
    for d in days:
        day_entries = inc_by_day.get(d, [])
        income_v = round(sum(e["amount"] for e in day_entries
                             if e.get("status") == "confirmed"), 2)
        income_pending = round(sum(e["amount"] for e in day_entries
                                   if e.get("status") == "pending"), 2)
        spend_v = round(spend_by_day.get(d, 0.0), 2)

        # ---- v2.1 §3.2.2 三个口径，全部由「已入账」这一唯一数据源推出，不重复计入
        prev_close = round(prev_close, 2)
        avail = round(prev_close - spend_v, 2)          # 当前可用余额：不含任何今日收入
        realtime = round(avail + income_v + income_pending, 2)   # 当前实时余额
        open_v = round(income_v + prev_close, 2)        # 日初积分（= 上一日日结 + 今日已确认收入）
        close_v = round(open_v - spend_v, 2)            # 日结计算值

        bal = bal_by_day.get(d)
        account = round(bal[1], 2) if bal else None
        account_src = bal[2] if bal else ""

        recon_at = (dt.date.fromisoformat(d) + dt.timedelta(days=1))
        recon_dt = dt.datetime.combine(recon_at, dt.time(0, 0, 0))
        recon_done = now >= recon_dt

        if account is None:
            diff = None
            diff_pct = None
            ok = None
        else:
            diff = round(account - close_v, 2)
            diff_pct = round(diff / close_v * 100, 2) if close_v else None
            ok = abs(diff) <= max(tol_abs, abs(open_v) * tol / 100.0)

        if not recon_done:
            status = "pending"
        elif account is None:
            status = "nobal"
        elif ok:
            status = "matched"
        else:
            status = "diff"

        carry = close_v if (account is None or ok or not recon_done) else account
        adjust = round(carry - close_v, 2)
        first = (d == days[0])

        if status == "diff":
            hint = ("账面按%s推算为 %.2f，实际余额 %.2f，相差 %+.2f。"
                    "多为初始积分与窗口起点不一致、或期间存在未记账的收入/支出；"
                    "已按账实为准调账结转。" %
                    (("初始积分 %s" % ("%.2f" % initial)) if first and initial is not None
                     else "上一日日结余额", close_v, account, diff))
        elif status == "pending":
            hint = "T+1 未到，先按账面值结转，届时以实际余额核对。"
        elif status == "nobal":
            hint = "缺可比对的账户余额：未提供积分流水，也未在这一天填过今日余额。"
        else:
            hint = "账户余额与日结余额一致（容差 ±%.0f%% / 0.5 积分内）。" % tol

        rows.append({
            "date": d,
            "first": first,
            "income": income_v,
            "incomeN": len([e for e in day_entries if e.get("status") == "confirmed"]),
            "incomePending": income_pending,
            "incomePendingN": len([e for e in day_entries if e.get("status") == "pending"]),
            "incomeVoidN": len([e for e in day_entries if e.get("status") == "void"]),
            "avail": avail,
            "realtime": realtime,
            "prevClose": prev_close,
            "open": open_v,
            "spend": spend_v,
            "spendN": len(tasks_by_day.get(d, [])),
            "close": close_v,
            "account": account,
            "accountSrc": account_src,
            "diff": diff,
            "diffPct": diff_pct,
            "ok": ok,
            "status": status,
            "statusText": STATUS_TEXT[status],
            "reconAt": recon_dt.isoformat(sep=" ", timespec="minutes"),
            "reconDone": recon_done,
            "carry": round(carry, 2),
            "adjust": adjust,
            "provisional": (status == "pending"),
            "hint": hint,
        })
        prev_close = carry

    # ---- 收支流水（收入 + 支出，统一成一张 T 日流水表）
    flow = []
    for d in days:
        for e in inc_by_day.get(d, []):
            st = e.get("status") or "confirmed"
            label = e["kind"] + ("（" + e["note"] + "）" if e.get("note") else "")
            if st == "pending":
                label = "草稿 · " + label
            elif st == "void":
                label = "已退回 · " + label
            flow.append({
                "txn": e.get("txn") or "", "date": d, "dir": "in", "kind": "收入",
                "ts": d, "name": label,
                "taskId": "", "credits": e["amount"], "duration": 0,
                "status": {"pending": "草稿", "void": "退回",
                           "confirmed": "已入账"}[st],
                "trigger": "", "level": "", "project": "",
                "model": "", "src": "income", "note": e.get("note") or e["kind"],
                "entryStatus": st, "entryId": e.get("entryId") or "",
                "bizNo": e.get("bizNo") or "",
                "enteredBy": e.get("enteredBy") or "",
                "snap": e.get("snap") or "nosnap",
                "snapSha": e.get("snapSha") or "",
                "trailN": int(e.get("trailN") or 0),
            })
        for t in tasks_by_day.get(d, []):
            flow.append({
                "txn": t.get("txn") or "", "date": d, "dir": "out", "kind": "支出",
                "ts": (t.get("ts") or "")[:19], "name": t.get("name") or "",
                "taskId": t.get("id") or "",
                "credits": round(float(t.get("credits") or 0), 2),
                "duration": int(t.get("duration") or 0),
                "status": t.get("status") or "成功",
                "trigger": t.get("trigger") or "", "level": t.get("level") or "",
                "project": t.get("project") or "",
                "model": t.get("mainChannel") or "",
                "steps": len(t.get("steps") or []),
                "inTok": int(t.get("inTok") or 0), "outTok": int(t.get("outTok") or 0),
                "src": "task", "note": "",
            })
    flow.sort(key=lambda r: (r["date"], r["ts"]), reverse=True)

    # ---- 汇总
    latest = rows[-1] if rows else None
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total_income = round(sum(r["income"] for r in rows), 2)
    total_income_pending = round(sum(r["incomePending"] for r in rows), 2)
    total_spend = round(sum(r["spend"] for r in rows), 2)
    diff_rows = [r for r in rows if r["status"] == "diff"]

    # v2.1 §3.2.2 落地自检：实时余额 − 日结计算值 必须等于「当日待确认收入」
    explain = []
    for r in rows:
        gap = round(r["realtime"] - r["close"], 2)
        if abs(gap - r["incomePending"]) > 0.005:
            explain.append({"date": r["date"], "gap": gap, "pending": r["incomePending"]})

    anchor_day = latest["date"] if latest else (anchor or "")
    t1 = {
        "date": anchor_day,
        "runAt": latest["reconAt"] if latest else "",
        "status": latest["status"] if latest else "pending",
        "statusText": latest["statusText"] if latest else STATUS_TEXT["pending"],
        "note": "",
    }
    if latest:
        if latest["status"] == "pending":
            t1["note"] = ("T 日 %s 的账在 T+1（%s 00:00）由定时任务对账；"
                          "现在给出的是账面值，届时以实际余额核对。"
                          % (latest["date"], (dt.date.fromisoformat(latest["date"])
                                              + dt.timedelta(days=1)).isoformat()))
        elif latest["status"] == "nobal":
            t1["note"] = "已过对账时点，但没有可直接比对的账户余额（未提供积分流水，也未填今日余额）。"
        elif latest["status"] == "matched":
            t1["note"] = "T+1 对账通过：账户余额与日结余额一致。"
        else:
            t1["note"] = ("T+1 对账发现偏差 %s 积分，已按账实为准调账结转。"
                          % ("%+.2f" % (latest["diff"] or 0)))

    return {
        "available": bool(rows),
        "anchor": anchor_day,
        "initial": initial,
        "initialDate": initial_date,
        "seeded": initial is not None,
        "tolerance": tol,
        "toleranceAbs": tol_abs,
        "now": now.isoformat(sep=" ", timespec="seconds"),
        "t1": t1,
        "days": rows,
        "flow": flow,
        "incomeQuality": income_quality(inc_manual),
        "totals": {
            "days": len(rows),
            "income": total_income,
            "incomePending": total_income_pending,
            "spend": total_spend,
            "net": round(total_income - total_spend, 2),
            "txns": len(flow),
            "matched": counts.get("matched", 0),
            "diff": counts.get("diff", 0),
            "pending": counts.get("pending", 0),
            "nobal": counts.get("nobal", 0),
            "diffCredits": round(sum(abs(r["diff"] or 0) for r in diff_rows), 2),
            # 三个口径（取有账的最后一天）
            "avail": latest["avail"] if latest else None,
            "realtime": latest["realtime"] if latest else None,
            "close": latest["close"] if latest else None,
            "balanceExplain": [] if not explain else explain,
        },
        "balancePolicy": {
            "avail": "当前可用余额 = 日初结转 − 今日消耗（不含任何今日收入，最保守，用于告警与冻结判断）",
            "realtime": "当前实时余额 = 当前可用余额 + 今日已确认收入 + 今日待确认收入（展示口径）",
            "close": "日结计算值 = 日初结转 + 今日已确认收入 − 今日消耗（账本口径，唯一的收入数据源）",
            "identity": "当前实时余额 − 日结计算值 = 今日待确认收入（差异可解释）",
            "carry": ("对账一致：日结余额原样结转；存在偏差：以账户余额为准结转并记调账金额；"
                      "待对账：先按账面值结转，T+1 核对后再定。"),
        },
    }


# ---------------------------------------------------------------- 定时任务
def script_text(project=None, out="out", at="00:00", os_name="win"):
    """T+1 日终对账的本地执行脚本：只跑本机 Python，不唤醒任何工具，零积分消耗。"""
    project = project or "<项目目录>"
    hh, mm = (at or "00:00").split(":")[:2]
    if os_name == "win":
        return "\r\n".join([
            "@echo off",
            "REM WorkBuddy 积分审计 · T+1 日终积分对账（只读、离线、不消耗积分）",
            "REM 由「任务监测 → 定时任务配置 → T+1 日终对账」生成，可直接编辑",
            "setlocal",
            'set "PROJECT=' + project + '"',
            'set "OUT=' + out + '"',
            'cd /d "%PROJECT%"',
            "REM 核对前一日（T 日）的账：日初 − 日耗 = 日结，账户余额是否相符",
            'python -m hosts.commands.recon_daily --save "%OUT%\\recon-daily.json"'
            ' --log "%OUT%\\recon.log" --yes >> "%OUT%\\recon.log" 2>&1',
            "endlocal",
        ])
    return "\n".join([
        "#!/bin/sh",
        "# WorkBuddy 积分审计 · T+1 日终积分对账（只读、离线、不消耗积分）",
        "# 由「任务监测 → 定时任务配置 → T+1 日终对账」生成，可直接编辑",
        'PROJECT="' + project + '"',
        'OUT="' + out + '"',
        'cd "$PROJECT" || exit 1',
        # 与 bat 一致：cd 到项目根后按模块跑，路径即模块，不再拼文件路径
        "python3 -m hosts.commands.recon_daily --save \"$OUT/recon-daily.json\""
        " --log \"$OUT/recon.log\" --yes >> \"$OUT/recon.log\" 2>&1",
    ])


def schedule_command(at="00:00", os_name="win", project=None, out="out",
                     task_name="WorkBuddyCreditRecon", script_path=None):
    """返回 (脚本内容, 注册命令)。T+1 的 00:00 是默认值，可按需改。"""
    at = at or "00:00"
    body = script_text(project, out, at, os_name)
    if os_name == "win":
        path = script_path or "%USERPROFILE%\\.workbuddy\\credit-recon-daily.bat"
        tr = ('cmd /c "' + path + '"').replace('"', '\\"')
        cmd = ('schtasks /Create /TN "%s" /TR "%s" /SC DAILY /ST %s /F'
               % (task_name, tr, at))
    else:
        path = script_path or "~/.workbuddy/credit-recon-daily.sh"
        hh, mm = at.split(":")[:2]
        cmd = ('# crontab -e 追加一行（T+1 每日 %s 触发）\n'
               '%d %d * * * /bin/sh "%s"' % (at, int(mm), int(hh), path))
    return body, cmd


# ---------------------------------------------------------------- CLI
def _parse_at(s):
    try:
        hh, mm = str(s or "00:00").split(":")[:2]
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
        return "%02d:%02d" % (hh, mm)
    except (ValueError, AttributeError):
        raise SystemExit("时间格式应为 HH:MM（00:00—23:59）")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="recon_daily.py",
                                 description="日终积分对账（T+1）与定时任务脚本")
    ap.add_argument("--tasks", help="任务 JSON（默认从本机日志重新采集）")
    ap.add_argument("--ledger", action="append", default=[], help="积分流水文件")
    ap.add_argument("--income", help="人工登记积分收入（默认 config/credit-income.json）")
    ap.add_argument("--state", default=str(DEFAULT_STATE), help="余额状态文件")
    ap.add_argument("--tolerance", type=float, default=5.0, help="容差百分比，默认 5")
    ap.add_argument("--for-date", help="只算到该日（YYYY-MM-DD），默认取数据最后一天")
    ap.add_argument("--save", help="把完整结果写成 JSON")
    ap.add_argument("--log", help="追加一行运行日志到该文件")
    ap.add_argument("--check", action="store_true", help="仅有偏差时退出码 2")
    ap.add_argument("--print-schedule", action="store_true", help="输出 T+1 定时任务脚本与命令")
    ap.add_argument("--at", default="00:00", help="定时任务触发时间，默认 00:00")
    ap.add_argument("--os", dest="os_name", default=("win" if os.name == "nt" else "unix"),
                    choices=["win", "unix"])
    ap.add_argument("--project", help="项目目录（生成脚本时用）")
    ap.add_argument("--out", default="out", help="产物目录")
    ap.add_argument("--script-path", help="脚本落盘路径")
    ap.add_argument("--yes", action="store_true", help="非交互环境直接继续")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    at = _parse_at(args.at)

    if args.print_schedule:
        body, cmd = schedule_command(at, args.os_name, args.project, args.out,
                                     script_path=args.script_path)
        name = ("credit-recon-daily.bat" if args.os_name == "win"
                else "credit-recon-daily.sh")
        print(":: ---------- %s ----------" % name)
        print(body)
        print()
        print(":: ---------- 注册命令（管理员执行一次） ----------")
        print(cmd)
        return 0

    # ---- 取任务
    tasks = None
    if args.tasks:
        try:
            with io.open(os.path.expanduser(args.tasks), "r", encoding="utf-8") as f:
                raw = json.load(f)
            tasks = raw.get("tasks") if isinstance(raw, dict) else raw
        except (OSError, ValueError) as e:
            print("任务文件读取失败：%s" % e)
            return 1
    if tasks is None:
        import discover as discover_mod
        import parse_logs
        tools = discover_mod.discover_tools(None, log=None)
        if not tools:
            print("未发现可分析的 Agent 日志目录")
            return 1
        data, _ctx = parse_logs.parse_all(tools, days=0, rules=None, log=None)
        tasks = data.get("tasks") or []

    # ---- 分配流水号（注册表幂等，重跑不变号）
    reg = txn_mod.Registry().load()
    reg.assign_tasks(tasks)
    reg.save()

    ledger = budget_mod.load_ledger(args.ledger or None, log=None)
    state = bs.load(args.state)
    result = build(tasks, ledger=ledger, state=state, income=load_income(args.income),
                   tolerance_pct=args.tolerance, anchor=args.for_date)

    if args.save:
        p = os.path.expanduser(args.save)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
            f.write("\n")

    t = result["totals"]
    if not args.quiet:
        print("日终积分对账（T+1）· 数据日 %s · 共 %d 天" % (result["anchor"] or "—", t["days"]))
        print("  初始积分 %s · 收入 %.2f · 消耗 %.2f · 净额 %.2f"
              % ("—" if result["initial"] is None else "%.2f" % result["initial"],
                 t["income"], t["spend"], t["net"]))
        print("  账实一致 %d 天 · 存在偏差 %d 天 · 待对账 %d 天 · 无实际余额 %d 天"
              % (t["matched"], t["diff"], t["pending"], t["nobal"]))
        for r in result["days"][-6:]:
            print("    %s  日初 %9.2f  − 日耗 %8.2f  = 日结 %9.2f   %s%s"
                  % (r["date"], r["open"], r["spend"], r["close"], r["statusText"],
                     ("（偏差 %+.2f）" % r["diff"]) if r["diff"] is not None else ""))
        if result["t1"]["note"]:
            print("  T+1：%s" % result["t1"]["note"])

    if args.log:
        line = ("%s\trecon\tanchor=%s\tdays=%d\tdiff=%d\tpending=%d\tnobal=%d\texit=%d"
                % (dt.datetime.now().isoformat(timespec="seconds"), result["anchor"],
                   t["days"], t["diff"], t["pending"], t["nobal"], 2 if t["diff"] else 0))
        p = os.path.expanduser(args.log)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    if args.check and t["diff"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
