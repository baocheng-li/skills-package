#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统计划任务描述 —— 把 config/schedule.json、日账、注册状态拼成页面能直接渲染的一份。

为什么要有这一层
----------------
「每天 9 点自动跑」这件事，真相散在三个地方：

  1. ``config/schedule.json`` —— 我们**打算**几点跑（意图）；
  2. ``out/schedule-state.json`` —— 系统里**实际**注册成什么样（事实，由 ``daily_credit --status`` 采回）；
  3. ``out/credit-daily.jsonl`` —— **真的**跑过几次、留下了哪些天（结果）。

三者不一致是常态：改了配置没重新注册、注册了但采集失败、跑过但中间断了两天。
页面只能展示其中一个数字的话，用户永远不知道自己在看「意图」还是「事实」。
所以这里把三份并排交给前端，由页面把差异**明写出来**，而不是挑一份好看的显示。

不做的事
--------
不读注册表、不调用 schtasks / Get-ScheduledTask。系统状态由 Hosts 层采，
Core 只做合并与判定——这样同一份代码在 Web / 插件宿主里没有 shell 也能跑。
"""

import datetime as dt
import io
import json


def _at_parts(at):
    """'09:05' → (9, 5)。写错格式就返回 None，不猜一个默认值蒙混过去。"""
    try:
        hh, mm = str(at).split(":")[:2]
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def freq_text(at):
    parts = _at_parts(at)
    return "每天 %02d:%02d" % parts if parts else "每天 —"


def rrule_of(at):
    parts = _at_parts(at)
    if not parts:
        return ""
    return "FREQ=DAILY;BYHOUR=%d;BYMINUTE=%d" % parts


def _sys_enabled(state_text):
    """系统口径的「启用」：Ready / Running 算开，Disabled 算关，其余都是不知道。

    三态而不是布尔：任务没注册、探测失败时 state 是空串，
    这时候答「已停用」和答「已启用」一样是在编。
    """
    txt = str(state_text or "").strip().lower()
    if not txt:
        return None
    return txt not in ("disabled", "已禁用")


def management_cmds(root=""):
    """页面上的启停 / 卸载命令。统一走 CLI，不直接拼 schtasks。

    一条命令同时改 config、同步系统、回写 state —— 三个动作必须绑在一起，
    拆成三条让用户分别执行，中间漏一条就出现「配置说停了、系统还在跑」。
    """
    base = "python -m hosts.commands.daily_credit "
    return {
        "enable": base + "--enable",
        "disable": base + "--disable",
        "install": base + "--install",
        "uninstall": base + "--uninstall",
        "status": base + "--status",
    }


def load_json(path, default=None):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}


def load_daily(path):
    """读 jsonl 日账。坏行跳过但保留其余——一行坏了不该让整条时间序列作废。"""
    rows = []
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def daily_stats(rows, today=None):
    """日账体检：跑了几天、中间断没断、有没有同一天两条。

    缺口（gap）与重复（dup）都要报：这两样都会让「按日取数」的下游拿到错的答案，
    而且都不报错——缺口看起来像「那天没花钱」，重复看起来像「那天花了两遍」。
    """
    rows = sorted([r for r in (rows or []) if r.get("date")],
                  key=lambda r: str(r.get("date")))
    dates = [str(r["date"])[:10] for r in rows]
    uniq = sorted(set(dates))
    seg = [d for d in uniq]

    gaps = []
    for i in range(1, len(seg)):
        try:
            a = dt.date.fromisoformat(seg[i - 1])
            b = dt.date.fromisoformat(seg[i])
        except ValueError:
            continue
        cur = a + dt.timedelta(days=1)
        while cur < b:
            gaps.append(cur.isoformat())
            cur += dt.timedelta(days=1)

    today = today or dt.date.today().isoformat()
    last = rows[-1] if rows else {}
    lag = last.get("lagDays")
    if lag is None and last.get("date"):
        try:
            lag = (dt.date.fromisoformat(str(today))
                   - dt.date.fromisoformat(str(last["date"])[:10])).days
        except ValueError:
            lag = None

    spent = [r.get("spent") for r in rows if isinstance(r.get("spent"), (int, float))]
    return {
        "n": len(rows),
        "span": [uniq[0], uniq[-1]] if uniq else ["", ""],
        "days": len(uniq),
        "gaps": gaps[:30],
        "gapN": len(gaps),
        "dupN": len(dates) - len(uniq),
        "lagDays": lag,
        "spentSum": round(sum(spent), 1) if spent else 0,
        "spentAvg": round(sum(spent) / len(spent), 2) if spent else 0,
        "last": {k: last.get(k) for k in
                 ("date", "at", "spent", "balance", "ratio", "levelText",
                  "lagDays", "exitCode")} if last else {},
        "broken": bool(len(dates) - len(uniq) or gaps),
    }


def register_cmd(script_abs, system_task, at, os_name="win",
                 python_exe="python", args="", cwd=""):
    """注册命令文本。给页面「复制后自己执行」用，也给人肉核对用。

    Windows 挂的是**批处理**而不是解释器：schtasks 没有「工作目录」这个参数，
    直接挂 pythonw 的话脚本里的相对路径会按 C:\\Windows\\System32 解析——
    跑起来不报错，只是什么都没写进 out/。批处理第一行就是 cd /d %~dp0。
    """
    if os_name == "win":
        return ('schtasks /Create /TN "%s" /TR "\\"%s\\"" /SC DAILY /ST %s /F'
                % (system_task, script_abs, at))
    parts = _at_parts(at) or (9, 0)
    return ("%d %d * * * cd %s && \"%s\" %s"
            % (parts[1], parts[0], cwd, python_exe, args))


def build(cfg, daily_rows, state, root, scripts, os_name="win",
          python_exe="python", today=None, log_time=None):
    """合并三份真相。缺哪份就写 None，不拿默认值顶替。

    ``scripts`` 是 {任务 id: 脚本绝对路径}，同时用于判断「脚本在不在」与
    生成注册命令——两件事必须看同一个路径，否则会出现「显示脚本缺失、
    注册命令却指向一个存在的文件」这种自相矛盾的页面。
    """
    cfg = cfg or {}
    state_rows = {}
    for s in ((state or {}).get("tasks") or []):
        if s.get("id"):
            state_rows[s["id"]] = s

    tasks = []
    for t in (cfg.get("tasks") or []):
        tid = t.get("id") or t.get("name")
        st = state_rows.get(tid) or {}
        at = t.get("at") or ""
        want = _at_parts(at)
        have = _at_parts(st.get("at")) if st.get("at") else None
        # 注册时间与配置时间不一致 = 改了配置没重新安装。这不是错误，
        # 但必须让用户看见，否则他会以为「我明明改成 10:30 了」。
        drift = bool(want and have and want != have)
        script_rel = t.get("script") if os_name == "win" else t.get("scriptUnix")
        tasks.append({
            "id": tid,
            "name": t.get("name") or tid,
            "type": t.get("type") or "sched",
            "at": at,
            "freq": freq_text(at),
            "rrule": rrule_of(at),
            "enabled": t.get("enabled") is not False,
            "systemTask": t.get("systemTask") or "",
            "module": t.get("module") or "",
            "args": t.get("args") or "",
            "desc": t.get("desc") or "",
            "script": script_rel or "",
            "scriptAbs": scripts.get(tid) or "",
            "scriptExists": bool(scripts.get(tid)),
            # installed: True 已注册 / False 明确未注册 / None 没探测过
            "installed": st.get("installed"),
            "state": st.get("state") or "",
            # 系统口径的启停：只在系统真的答了 state 时才有值
            "sysEnabled": _sys_enabled(st.get("state")),
            "registeredAt": st.get("at") or "",
            # 上次执行：系统说得准就用系统的；说不准（任务刚建、从没跑过）
            # 就退回日志文件的最后写入时间——那正是每次运行开头写下的。
            "lastRun": st.get("lastRun") or log_time or "",
            "nextRun": st.get("nextRun") or "",
            "checkedAt": st.get("checkedAt") or "",
            "via": st.get("via") or "",
            "atDrift": drift,
            "registerCmd": register_cmd(scripts.get(tid) or "",
                                        t.get("systemTask") or "", at, os_name,
                                        python_exe, t.get("args") or "", root),
        })

    stats = daily_stats(daily_rows, today=today)
    note = ""
    if not (cfg.get("tasks") or []):
        note = "config/schedule.json 没读到任务定义，页面无法判断该不该有每日任务。"
    elif any(t["installed"] is None for t in tasks):
        note = ("系统里是否已注册还不知道——跑一次 "
                "python -m hosts.commands.daily_credit --status 采回状态。")
    return {
        "source": "config/schedule.json",
        "tasks": tasks,
        "daily": stats,
        "root": root,
        "cmds": management_cmds(root),
        "note": note,
    }
