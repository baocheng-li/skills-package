#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日读取 WorkBuddy 积分消耗（供系统计划任务直接调用）。

为什么不是「再解析一遍日志」
----------------------------
项目里已经有 ``refresh_report``：它自带并发锁、运行日志、退出码联动，
采集口径与 ``audit.py`` 完全一致。本命令只在它外面加一件事——
**把当天的结果固化成一行的日账**（``out/credit-daily.jsonl``）。

这样分开是有原因的：

  * 报告是离线单文件，页面里的数字在生成那一刻就固定了；要看「每天花多少」，
    必须有独立于报告的时间序列；
  * 日账一天一行、可幂等重算（同日再跑覆盖当日那一行），
    所以定时任务漏跑一次、事后补跑一次，序列都不会出现重复或空洞；
  * 采集失败（返回码 1）时**不写日账**——宁可缺口，也不要写进一行空数据，
    缺口一眼看得出来，脏数据会一直被当成真的。

用法
----
    python -m hosts.commands.daily_credit                     # 采集 + 写一行日账
    python -m hosts.commands.daily_credit --no-collect        # 只读取现有结果，不重跑采集
    python -m hosts.commands.daily_credit --print-schedule    # 打印脚本与注册命令
    python -m hosts.commands.daily_credit --install           # 注册为每日计划任务
    python -m hosts.commands.daily_credit --status            # 看任务是否已注册
    python -m hosts.commands.daily_credit --disable           # 停用（改配置 + 同步系统）
    python -m hosts.commands.daily_credit --enable            # 启用（改配置 + 同步系统）
    python -m hosts.commands.daily_credit --uninstall         # 取消注册

退出码沿用预算告警口径：0 正常 / 2 黄色 / 3 橙色 / 4 红色 / 1 失败。
"""

import argparse
import base64
import datetime as dt
import io
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import refresh_report                                     # noqa: E402

DAILY_NAME = "credit-daily.jsonl"
LOG_NAME = "daily_credit.log"
STATE_NAME = "schedule-state.json"
TASK_NAME = "CostAudit\\DailyCredit"
DEFAULT_AT = "09:05"
CONFIG = ROOT / "config" / "schedule.json"


# --------------------------------------------------------------------------- #
# 日账读写
# --------------------------------------------------------------------------- #

def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def read_budget(out_dir):
    path = Path(out_dir) / "budget_status.json"
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def build_record(budget, at=None):
    """把一份 budget_status 压成一行日账。

    只留「以后要比对的东西」：当天花了多少、余额剩多少、占比、级别。
    采集时间、窗口消耗这类派生值也留着，但都是快照，不参与趋势计算。
    """
    day = budget.get("today") or budget.get("todayBalanceDate") or ""
    th = budget.get("thresholds") or {}
    wall = dt.date.today().isoformat()
    return {
        "date": day,
        "at": at or _now(),
        # 数据锚点日与自然日不是一回事：日志停更时 date 会冻在最后一天。
        # 记下滞后天数，读的人才知道这一行是「今天的账」还是「旧账又抄了一遍」。
        "wallDate": wall,
        "lagDays": _lag(day, wall),
        "spent": budget.get("todaySpent"),
        "balance": budget.get("balance"),
        "ratio": budget.get("spendRatio"),
        "level": budget.get("level"),
        "levelText": budget.get("levelText"),
        "exitCode": budget.get("exitCode"),
        "windowSpent": budget.get("windowSpent"),
        "balanceSource": budget.get("sourceLabel"),
        "thresholds": {"warn": th.get("warn"), "crit": th.get("crit"), "red": th.get("red")},
        "reason": budget.get("reason"),
    }


def _lag(day, wall):
    """数据锚点日落后自然日多少天。取不到日期返回 None，不猜 0。"""
    try:
        d = dt.date.fromisoformat(str(day))
        w = dt.date.fromisoformat(str(wall))
    except (TypeError, ValueError):
        return None
    return (w - d).days


def load_daily(path):
    rows = []
    path = Path(path)
    if not path.exists():
        return rows
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue            # 坏行跳过，但不能因此让整条时间序列不可用
    except OSError:
        return []
    return rows


def upsert_daily(path, rec):
    """同日覆盖、按日排序后整体重写。

    为什么不是 append：定时任务漏跑后补跑、或同一天手动跑两次，
    append 会在序列里留下两条同一天、数字不同的记录，
    之后任何「按日取数」的地方都要先想「取哪条」。
    """
    path = Path(path)
    rows = [r for r in load_daily(path) if r.get("date") != rec.get("date")]
    rows.append(rec)
    rows.sort(key=lambda r: r.get("date") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(str(tmp), str(path))       # 先写临时文件再换名，中途断电不会留下半行
    return len(rows)


# --------------------------------------------------------------------------- #
# 定时任务：脚本正文与注册命令
# --------------------------------------------------------------------------- #

def schedule_bat():
    """Windows 批处理正文——**全部 ASCII**。

    cmd.exe 按 OEM 代码页逐字节解析 .bat，正文里的中文会让行边界错位，
    实测会把注释后半截当命令执行。所以这里一个汉字都不写，
    所有说明性输出交给 Python 那一侧。
    """
    return "\n".join([
        "@echo off",
        "rem CostAudit daily credit reader. ASCII only on purpose:",
        "rem cmd.exe parses this file with the OEM code page, non-ASCII breaks line boundaries.",
        "setlocal",
        'cd /d "%~dp0..\\.."',
        'set "PY=python"',
        'if exist ".venv\\Scripts\\python.exe" set "PY=.venv\\Scripts\\python.exe"',
        'if not exist out mkdir out',
        'rem --log tees stdout/stderr into the file; do NOT also append with >> here,',
        'rem two writers on one log interleave and produce half lines.',
        '"%PY%" -m hosts.commands.daily_credit --out out --format html --quiet --log out\\daily_credit.log',
        'set "RC=%ERRORLEVEL%"',
        "endlocal & exit /b %RC%",
        "",
    ])


def schedule_sh():
    return "\n".join([
        "#!/bin/sh",
        "# CostAudit daily credit reader",
        'cd "$(dirname "$0")/../.." || exit 1',
        "PY=python3",
        '[ -x .venv/bin/python ] && PY=.venv/bin/python',
        'mkdir -p out',
        'exec "$PY" -m hosts.commands.daily_credit --out out --format html --quiet >> out/daily_credit.log 2>&1',
        "",
    ])


def script_path(os_name):
    name = "daily_credit.bat" if os_name == "win" else "daily_credit.sh"
    return ROOT / "deploy" / "schedule" / name


def load_plan():
    """读 config/schedule.json —— 「既定时间」只有这一处真源。

    读不到就退回内置默认，但要把 configured 标出来：静默兜底会让
    「配了个空的」和「根本没配」看起来一模一样，之后排查只能靠猜。
    """
    t = {}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        t = (cfg.get("tasks") or [{}])[0] or {}
    except (OSError, ValueError):
        t = {}
    return {
        "id": t.get("id") or "daily-credit",
        "name": t.get("name") or "每日积分取数与日账",
        "at": t.get("at") or DEFAULT_AT,
        "enabled": t.get("enabled") is not False,
        "systemTask": t.get("systemTask") or TASK_NAME,
        "module": t.get("module") or "hosts.commands.daily_credit",
        # --log 不是可选项：计划任务用 pythonw 跑，没有控制台，
        # 不自己落盘就等于什么都不留
        "args": t.get("args") or ("--out out --format html --quiet --log out/%s" % LOG_NAME),
        "script": t.get("script") or "deploy/schedule/daily_credit.bat",
        "desc": t.get("desc") or "",
        "configured": bool(t),
    }


def interp():
    """计划任务的解释器：Windows 上优先 pythonw.exe。

    用 python.exe 注册「仅在用户登录时运行」的任务，每天到点会在桌面
    弹一个黑框——哪怕只闪三秒，也是每天早上会被投诉的那种小事。
    """
    exe = sys.executable or "python"
    if os.name != "nt":
        return exe
    d = Path(exe).parent
    cand = d / "pythonw.exe"
    if not cand.exists():
        return exe
    # 名字里带 .old 的目录是版本升级留下的备份副本，清理磁盘时第一个被删。
    # 把计划任务钉在那种路径上，今天能跑、清理完就静默失败——优先回到正式版本目录。
    if ".old" in d.name:
        alt = d.parent / d.name.split(".old")[0] / "pythonw.exe"
        if alt.exists():
            return str(alt)
    return str(cand)


def split_task(name):
    """'CostAudit\\DailyCredit' → ('\\CostAudit\\', 'DailyCredit')。

    Get-ScheduledTask 的 -TaskName 只认叶子名，整串带反斜杠会被判成「不存在」——
    注册明明成功、查询却说没有，这种自相矛盾最难查，所以名字在这里先劈开。
    """
    name = str(name or "")
    if "\\" not in name:
        return "\\", name
    folder, leaf = name.rsplit("\\", 1)
    return "\\" + folder.strip("\\") + "\\", leaf


def action_of(plan):
    """(可执行文件, 参数, 工作目录)：三个都要，缺工作目录相对路径就废了。"""
    return interp(), "-m %s %s" % (plan["module"], plan["args"]), str(ROOT)


def register_cmd(at, os_name, plan=None):
    """注册命令文本（粘贴到终端用）。页面上的「注册命令」也由这段生成。

    /TR 的引号要包住「带空格的完整命令行」：外层一对给 schtasks 拆参数，
    内层一对给任务计划程序自己。少一层就变成「找不到 <盘符>:\<项目目录>\...」。
    """
    plan = plan or load_plan()
    exe, args, _cwd = action_of(plan)
    if os_name == "win":
        # 指批处理而不是直接指解释器：schtasks 没有「工作目录」这个参数，
        # 直接挂 pythonw 的话 `--out out` 会按 C:\Windows\System32 解析，
        # 跑起来不报错，只是什么都没写进 out/。批处理里有 cd /d %~dp0。
        target = str(script_path("win"))
        return ('schtasks /Create /TN "%s" /TR "\\"%s\\"" /SC DAILY /ST %s /F'
                % (plan["systemTask"], target, at))
    hh, mm = (at.split(":") + ["0"])[:2]
    # Unix 侧写死 python3：sys.executable 是**当前宿主**的解释器，在这台机器上
    # 它是个带中文用户名的 Windows 路径，抄进 crontab 既跑不通也不是 ASCII。
    return ("%d %d * * * cd %s && python3 -m %s %s"
            % (int(mm), int(hh), ROOT.as_posix(), plan["module"], plan["args"]))


def _q(v):
    """PowerShell 单引号字符串：内部单引号双写，别的都不用转。"""
    return "'" + str(v).replace("'", "''") + "'"


def _ps(script, timeout=120):
    """跑一段 PowerShell 脚本。

    用 -EncodedCommand（base64 / UTF-16LE）而不是把命令拼进命令行：
    注册任务要同时出现双引号、反斜杠、空格和中文描述，任何一层 shell 都可能
    把引号吃掉一截，排错时会误以为是任务计划程序的问题。
    编码之后只过一次进程边界，最稳。
    """
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return _run_win('powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass '
                    '-EncodedCommand ' + b64, timeout=timeout)


def ps_install_script(plan, at):
    exe, args, cwd = action_of(plan)
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$act = New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s"
        % (_q(exe), _q(args), _q(cwd)),
        "$trg = New-ScheduledTaskTrigger -Daily -At %s" % at,
        # StartWhenAvailable：到点时机器关着或休眠，开机后补跑一次。
        # 每天一行日账，断一天序列就有缺口，补跑比「准点」更重要。
        "$set = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 2)",
        "Register-ScheduledTask -TaskName %s -Action $act -Trigger $trg -Settings $set "
        "-Description %s -Force | Out-Null" % (_q(plan["systemTask"]), _q(plan["desc"])),
        "Write-Output 'OK'",
    ])


def ps_status_script(task_name):
    path, leaf = split_task(task_name)
    return "\n".join([
        "$t = Get-ScheduledTask -TaskPath %s -TaskName %s -ErrorAction SilentlyContinue"
        % (_q(path), _q(leaf)),
        "if (-not $t) { Write-Output 'MISSING'; exit 0 }",
        "$i = Get-ScheduledTaskInfo -TaskPath %s -TaskName %s" % (_q(path), _q(leaf)),
        "$last = ''; if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) "
        "{ $last = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') }",
        "$next = ''; if ($i.NextRunTime) "
        "{ $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') }",
        "$bd = ''; if ($t.Triggers.Count -gt 0) { $bd = $t.Triggers[0].StartBoundary }",
        "Write-Output ('STATE={0};AT={1};LAST={2};NEXT={3}' -f $t.State, $bd, $last, $next)",
    ])


def probe(plan):
    """问系统：这个任务注册没有、几点、跑没跑过。

    先问 PowerShell（拿得到 State 与下次运行时间，且不受系统语言影响），
    拿不到再退回 schtasks 的文本输出（只能判断有没有）。
    两条都问不出来就返回 installed=None —— 「不知道」必须能表达，
    否则页面只能显示「未注册」，而那是另一件事。
    """
    if os.name != "nt":
        return {"installed": None, "state": "", "at": "", "lastRun": "",
                "nextRun": "", "via": "", "detail": "非 Windows 系统，请用 crontab -l 查看"}

    rc, out, err = _ps(ps_status_script(plan["systemTask"]))
    txt = (out or "").strip()
    if rc == 0 and "MISSING" in txt:
        return {"installed": False, "state": "", "at": "", "lastRun": "",
                "nextRun": "", "via": "powershell", "detail": "任务不存在"}
    if rc == 0 and "STATE=" in txt:
        kv = {}
        for part in txt.split(";")[:4]:
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k.strip()] = v.strip()
        boundary = kv.get("AT") or ""
        at = boundary[11:16] if len(boundary) >= 16 else ""
        return {"installed": True, "state": kv.get("STATE") or "", "at": at,
                "lastRun": kv.get("LAST") or "", "nextRun": kv.get("NEXT") or "",
                "via": "powershell", "detail": txt}

    rc2, out2, err2 = _run_win('schtasks /Query /TN "%s" /FO LIST' % plan["systemTask"])
    if rc2 == 0:
        return {"installed": True, "state": "", "at": "",
                "lastRun": _grep(out2, ("上次运行时间", "Last Run Time")),
                "nextRun": _grep(out2, ("下次运行时间", "Next Run Time")),
                "via": "schtasks", "detail": (out2 or "").strip()}
    blob = (out2 or "") + (err2 or "")
    if "找不到" in blob or "cannot find" in blob.lower():
        return {"installed": False, "state": "", "at": "", "lastRun": "",
                "nextRun": "", "via": "schtasks", "detail": "任务不存在"}
    return {"installed": None, "state": "", "at": "", "lastRun": "",
            "nextRun": "", "via": "", "detail": blob.strip()[:200]}


def _grep(text, keys):
    for line in (text or "").splitlines():
        for k in keys:
            if k in line:
                return line.split(":", 1)[-1].strip() if ":" in line else line.strip()
    return ""


def write_state(out_dir, plan, info):
    """把「系统里实际是什么样」落盘，供页面读取。

    页面是离线单文件，没法自己去问系统；采集时把答案带进去，
    这样打开报告看到的是**上次探测到的事实**，不是页面猜的。
    """
    payload = {
        "checkedAt": _now(),
        "source": "daily_credit --status",
        "tasks": [{
            "id": plan["id"],
            "name": plan["name"],
            # 配置口径与系统口径分开存：启用是「打算跑」，state 是「系统认不认」。
            # 混成一个字段，页面上就永远分不清「我停了」和「系统还开着」。
            "enabled": plan.get("enabled") is not False,
            "installed": info.get("installed"),
            "at": info.get("at") or "",
            "configuredAt": plan["at"],
            "state": info.get("state") or "",
            "lastRun": info.get("lastRun") or "",
            "nextRun": info.get("nextRun") or "",
            "via": info.get("via") or "",
            "detail": (info.get("detail") or "")[:400],
        }],
    }
    path = Path(out_dir) / STATE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    os.replace(str(tmp), str(path))
    return path


def _run_win(cmd, timeout=60):
    """跑 schtasks 并按本机代码页解码输出。

    schtasks 把结果按**控制台代码页**输出（中文系统是 cp936），
    subprocess 默认按 UTF-8 解会在 reader 线程里抛 UnicodeDecodeError——
    主流程看着「执行完了」，实际上面那句话被吞掉了。
    """
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    raw = p.stdout or b""
    err = p.stderr or b""
    def dec(b):
        for enc in ("oem", "mbcs", "utf-8"):
            try:
                return b.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return b.decode("utf-8", "replace")
    return p.returncode, dec(raw), dec(err)


def write_script(os_name):
    """只把脚本落盘，不注册。注册是改系统状态的动作，必须单独一步做。"""
    sp = script_path(os_name)
    sp.parent.mkdir(parents=True, exist_ok=True)
    body = schedule_bat() if os_name == "win" else schedule_sh()
    with io.open(sp, "w", encoding="utf-8", newline="\r\n" if os_name == "win" else "\n") as f:
        f.write(body)
    if os_name != "win":
        try:
            os.chmod(str(sp), 0o755)
        except OSError:
            pass
    return sp


def write_plan_enabled(flag):
    """把 enabled 写回 config/schedule.json —— 「打算跑不跑」的唯一真源。

    只动这一个字段，别的键原样保留：这份配置是给人读的，
    整个重排一遍字段顺序，事后就看不出「到底改了哪儿」。
    """
    path = CONFIG
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, "读不到 %s（%s）" % (path, e)
    tasks = cfg.get("tasks") or []
    if not tasks:
        return False, "%s 里没有任务定义" % path
    tasks[0]["enabled"] = bool(flag)
    cfg["tasks"] = tasks
    try:
        tmp = path.with_suffix(".tmp")
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        os.replace(str(tmp), str(path))
    except OSError as e:
        return False, "写不进 %s（%s）" % (path, e)
    return True, str(path)


def set_enabled(flag, os_name, out_dir):
    """启用 / 停用：先改配置，再同步系统。

    顺序不能反。配置是意图的真源，系统只是它的投影；先动系统的话，
    中途失败就变成「系统停了、配置还说开着」，两边都说不清哪个是对的。
    """
    ok, msg = write_plan_enabled(flag)
    print(("配置已更新：%s · enabled=%s" if ok else "配置未更新：%s")
          % ((msg, "true" if flag else "false") if ok else msg))

    if os_name != "win":
        print("Unix 侧请手工在 crontab 里%s那一行（crontab -e）。"
              % ("取消注释" if flag else "注释掉"))
        return 0 if ok else 1

    plan = load_plan()          # 重新读：此时的 enabled 才是刚写进去的
    path, leaf = split_task(plan["systemTask"])
    # 优先 PowerShell（返回码可靠、不看系统语言），失败退 schtasks /Change
    verb = "Enable-ScheduledTask" if flag else "Disable-ScheduledTask"
    rc, out, err = _ps("%s -TaskPath %s -TaskName %s"
                       % (verb, _q(path), _q(leaf)))
    how = "powershell"
    if rc != 0:
        rc, out, err = _run_win('schtasks /Change /TN "%s" /%s'
                                % (plan["systemTask"],
                                   "ENABLE" if flag else "DISABLE"))
        how = "schtasks"

    info = probe(plan)
    info = dict(info, detail=(info.get("detail") or "")[:400])
    write_state(out_dir, plan, info)
    if rc == 0:
        print("系统任务已%s：%s（%s）" % ("启用" if flag else "停用",
                                          plan["systemTask"], how))
        return 0

    blob = (err or out or "").strip()
    if info.get("installed") is False:
        print("系统里没有 %s，没有什么可%s的。配置已写 enabled=%s，"
              "执行 --install 注册后按这个状态生效。"
              % (plan["systemTask"], "启用" if flag else "停用",
                 "true" if flag else "false"))
        return 0
    print("系统同步未成功（%s rc=%s）：%s" % (how, rc, blob or "无可读输出"))
    print("可手工执行：\n    schtasks /Change /TN \"%s\" /%s"
          % (plan["systemTask"], "ENABLE" if flag else "DISABLE"))
    return 1


def install(at, os_name, out_dir, via="auto"):
    """注册每日任务。先 PowerShell（能设工作目录、无黑框），失败退回 schtasks。"""
    plan = load_plan()
    at = at or plan["at"]
    sp = write_script(os_name)
    print("脚本已写入：%s" % sp)

    if os_name != "win":
        print("\nUnix 侧不自动改 crontab（避免覆盖别人的条目）。请手工执行 crontab -e 加一行：\n")
        print("    " + register_cmd(at, os_name, plan))
        return 0

    tried = []
    if via in ("auto", "powershell"):
        rc, out, err = _ps(ps_install_script(plan, at))
        tried.append(("powershell", rc, (err or out or "").strip()))
        if rc == 0:
            info = probe(plan)
            if info.get("installed") is not True:
                info = dict(info, installed=True, at=at, via="powershell")
            st = write_state(out_dir, plan, info)
            print("已注册：%s · 每天 %s · 解释器 %s"
                  % (plan["systemTask"], at, interp()))
            print("状态已写入：%s" % st)
            return 0

    if via in ("auto", "schtasks"):
        rc, out, err = _run_win(register_cmd(at, "win", plan))
        tried.append(("schtasks", rc, (err or out or "").strip()))
        if rc == 0:
            info = probe(plan)
            if info.get("installed") is not True:
                info = dict(info, installed=True, at=at, via="schtasks")
            write_state(out_dir, plan, info)
            print("已注册（schtasks）：%s · 每天 %s" % (plan["systemTask"], at))
            return 0

    print("注册未成功。两条路子都试过了：")
    for name, rc, msg in tried:
        print("  - %-10s rc=%s  %s" % (name, rc, msg or "无可读输出"))
    print("\n可手工执行（任选一条）：\n    %s" % register_cmd(at, "win", plan))
    return 1


def uninstall(os_name, out_dir):
    plan = load_plan()
    if os_name != "win":
        print("请手工删除 crontab 中对应那一行（crontab -e）。")
        return 0
    path, leaf = split_task(plan["systemTask"])
    rc, out, err = _ps("Unregister-ScheduledTask -TaskPath %s -TaskName %s -Confirm:$false"
                       % (_q(path), _q(leaf)))
    if rc != 0:
        rc, out, err = _run_win('schtasks /Delete /TN "%s" /F' % plan["systemTask"])
    ok = rc == 0
    if ok:
        write_state(out_dir, plan, {"installed": False, "detail": "已取消注册"})
    print(("已取消注册：%s" if ok else "取消失败（rc=%s）：%s")
          % (plan["systemTask"] if ok else rc, "" if ok else (err or out or "").strip()))
    return 0 if ok else 1


def status(os_name, out_dir):
    plan = load_plan()
    if os_name != "win":
        print("请用 crontab -l 查看。")
        return 0
    info = probe(plan)
    st = write_state(out_dir, plan, info)
    label = {True: "已注册", False: "未注册", None: "无法判定"}[info.get("installed")]
    print("任务：%s · %s · 配置 %s"
          % (plan["systemTask"], label,
             "启用" if plan.get("enabled") is not False else "停用"))
    if info.get("state"):
        print("  系统状态：%s" % info["state"])
    if info.get("at"):
        print("  实际触发：每天 %s" % info["at"])
        if info["at"] != plan["at"]:
            print("  注意：配置写的是每天 %s，系统里注册的是 %s —— 改完配置要重新 --install 才生效。"
                  % (plan["at"], info["at"]))
    if info.get("lastRun"):
        print("  上次运行：%s" % info["lastRun"])
    if info.get("nextRun"):
        print("  下次运行：%s" % info["nextRun"])
    if info.get("installed") is not True:
        print("  注册命令：\n    %s" % register_cmd(plan["at"], "win", plan))
    print("状态已写入：%s" % st)
    return 0


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="daily_credit.py",
        description="每日读取本机积分消耗并写入日账（只读采集、离线、不消耗额度）")
    ap.add_argument("--out", default="out", help="产物目录（默认 out）")
    ap.add_argument("--format", default="html", help="报告格式 html / all / none")
    ap.add_argument("--root", help="数据根目录（默认自动发现）")
    # 默认 30 天而不是「默认窗口」：日任务每天跑一次，窗口一旦贴着实时的 7 天走，
    # 旧数据会一天掉出去一天，报告越跑越薄。保住一个月才看得出趋势。
    ap.add_argument("--last", default="30d", help="统计窗口，默认 30d（如 7d / 30d / 90d）")
    ap.add_argument("--days", type=int, default=0, help="统计窗口天数；0 = 全部历史")
    ap.add_argument("--balance-state", help="余额状态文件路径")
    ap.add_argument("--no-collect", action="store_true",
                    help="不重新采集，直接用现有 budget_status.json 写日账")
    ap.add_argument("--print-schedule", action="store_true", help="打印脚本与注册命令")
    ap.add_argument("--write-script", action="store_true",
                    help="只把脚本落到 deploy/schedule/，不改系统计划任务")
    ap.add_argument("--install", action="store_true", help="注册为每日计划任务")
    ap.add_argument("--uninstall", action="store_true", help="取消注册")
    ap.add_argument("--enable", action="store_true", help="启用：改配置并同步系统任务")
    ap.add_argument("--disable", action="store_true", help="停用：改配置并同步系统任务")
    ap.add_argument("--status", action="store_true", help="查看注册状态")
    ap.add_argument("--via", default="auto", choices=["auto", "powershell", "schtasks"],
                    help="注册通道：auto 先试 PowerShell 再退回 schtasks")
    # 空 = 用 config/schedule.json 里的 at。命令行给了就以命令行为准，
    # 但页面和文档都以配置为准，避免三处各说一套。
    ap.add_argument("--at", help="每日触发时间，默认取 config/schedule.json")
    ap.add_argument("--log", help="把本次输出追加到日志文件（计划任务用 pythonw 跑时必需）")
    ap.add_argument("--os", dest="os_name", default=("win" if os.name == "nt" else "unix"),
                    choices=["win", "unix"])
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    plan = load_plan()
    at = args.at or plan["at"]

    if args.print_schedule:
        name = "daily_credit.bat" if args.os_name == "win" else "daily_credit.sh"
        body = schedule_bat() if args.os_name == "win" else schedule_sh()
        print(":: ---------- %s ----------" % name)
        print(body)
        print(":: ---------- 注册命令（schtasks，粘贴到终端执行一次） ----------")
        print(register_cmd(at, args.os_name, plan))
        if args.os_name == "win":
            print("\n:: ---------- 等价的 PowerShell（--install 会自动用它） ----------")
            print(ps_install_script(plan, at))
        return 0
    if args.write_script:
        print("脚本已写入：%s" % write_script(args.os_name))
        print("注册命令（执行一次）：\n    %s" % register_cmd(at, args.os_name, plan))
        return 0
    if args.install:
        return install(at, args.os_name, out_dir, args.via)
    if args.uninstall:
        return uninstall(args.os_name, out_dir)
    if args.enable or args.disable:
        return set_enabled(bool(args.enable), args.os_name, out_dir)
    if args.status:
        return status(args.os_name, out_dir)

    # ---- 1) 采集（除非明确不采）
    code = 0
    if not args.no_collect:
        argv2 = ["--out", str(out_dir), "--format", args.format]
        if args.root:
            argv2 += ["--root", args.root]
        if args.days:
            argv2 += ["--days", str(args.days)]
        if args.last:
            argv2 += ["--last", args.last]
        if args.balance_state:
            argv2 += ["--balance-state", args.balance_state]
        if args.quiet:
            argv2 += ["--quiet"]
        code = refresh_report.main(argv2)

    # ---- 2) 取当天结果
    budget = read_budget(out_dir)
    if not budget.get("today") and not budget.get("todaySpent"):
        print("[daily] 未取到预算结果（%s 不存在或为空），本次不写日账。"
              % (out_dir / "budget_status.json"))
        return 1 if code in (0, None) else code

    rec = build_record(budget)
    path = out_dir / DAILY_NAME
    total = upsert_daily(path, rec)

    # ---- 3) 一行摘要
    line = ("[daily] %s 今日消耗 %s · 当前余额 %s · 预警比 %s%% · %s"
            % (rec["date"] or "—",
               _num(rec["spent"]), _num(rec["balance"]),
               _num(rec["ratio"], 2), rec["levelText"] or "—"))
    # 有 --log 时即便 --quiet 也要打这一行：日志里唯一值得长期留的就是它，
    # 静默到底等于日志永远是空的
    if not args.quiet or args.log:
        print(line)
        # 滞后要说出来：日志停更时 date 会冻在最后一天，日账看着天天在记，
        # 其实天天在抄同一笔旧账。不知道就明说，别让它读成「今天没花钱」。
        lag = rec.get("lagDays")
        if lag is None:
            print("[daily] 注意：数据里没有可用日期，无法判断是否滞后。")
        elif lag >= 1:
            print("[daily] 注意：数据最后一天 %s，比今天早 %d 天——本机日志可能没更新，"
                  "这一行是 %s 的账，不是今天的。" % (rec["date"], lag, rec["date"]))
    print("[daily] 日账 %s 共 %d 天" % (path, total))

    # 采集失败但拿到了结果：以采集码为准上报，日账仍写（数字本身是有效的）
    return code if code else (budget.get("exitCode") or 0)


def _num(v, nd=1):
    if v is None:
        return "—"
    try:
        return ("%%,.%df" % nd) % float(v)
    except (TypeError, ValueError):
        return str(v)


class _Tee(object):
    """把 stdout 同时写日志文件。

    pythonw.exe 下 sys.stdout 是 None，任何一句 print 都会抛的
    AttributeError —— 而计划任务正是用 pythonw 跑的。所以 --log 不只是
    "顺便记一份"，它是这条链路能留下任何痕迹的前提。
    """

    def __init__(self, path):
        self.path = path
        self.f = io.open(path, "a", encoding="utf-8")

    def write(self, s):
        try:
            self.f.write(s)
            self.f.flush()
        except (OSError, ValueError):
            pass
        try:
            if sys.__stdout__ is not None:
                sys.__stdout__.write(s)
                sys.__stdout__.flush()
        except (OSError, ValueError, AttributeError):
            pass
        return len(s) if s else 0

    def flush(self):
        try:
            self.f.flush()
        except (OSError, ValueError):
            pass


def _log_path(rel):
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def entry(argv=None):
    """进程入口：先架好日志，再跑 main —— 异常也要落在文件里。

    Traceback 平时打到 stderr；计划任务里 stderr 没有去处，异常等于消失，
    第二天只会看到"昨天没数据"，而且没有任何线索。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    log = None
    if "--log" in argv:
        i = argv.index("--log")
        if i + 1 < len(argv):
            log = argv[i + 1]
    if log:
        path = _log_path(log)
        path.parent.mkdir(parents=True, exist_ok=True)
        tee = _Tee(str(path))
        sys.stdout = tee
        sys.stderr = tee
        sys.stdout.write("\n===== %s =====\n" % _now())
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception:                                    # noqa: BLE001
        msg = traceback.format_exc()
        try:
            sys.stdout.write("[daily] 运行失败：\n" + msg + "\n")
        except (AttributeError, OSError, ValueError):
            pass
        return 1


if __name__ == "__main__":
    sys.exit(entry())
