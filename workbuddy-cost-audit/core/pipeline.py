#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline —— 全应用**唯一的编排**。

为什么必须唯一
--------------
以前这套流程写在 ``hosts/commands/audit.py`` 的 ``main()`` 里，和 argparse、print、
退出码、写文件混在一起。要加一个宿主（比如 MCP 插件），只有两条路：
复制一遍流程（立刻分叉），或者 import CLI 再假装自己是命令行（立刻耦合）。

抽出来之后，四种宿主做的是同一件事：

    model = Pipeline(source, store).run(PipelineOptions(...))
    renderer.html.render(path, model.data)

退出码、打印、交互确认这些**只属于 CLI** 的东西留在 hosts/cli.py，
Pipeline 只管算。

步骤顺序不是随便排的
--------------------
    采集 → 脱敏 → 分类 → 过滤 → 流水号 → 异常 → 定时 → 监测 → 预算 → 对账 → 对比 → 总览

* 脱敏必须在任何持久化之前：一旦原始字符串写进 CSV，后面再抹就晚了；
* 分类要在过滤之前，因为过滤条件里有 ``--level``，而 level 是分类算出来的；
* 过滤后要再分类一次：窗口变了，分位阈值也要跟着窗口重算，否则同一任务
  在「近 7 天」和「全部历史」两个视图里的层级会不一致；
* 预算要在对账之前：对账要用预算里的今日消耗口径；
* 总览必须最后：它是前面所有结果的收敛。
"""

import datetime as dt
import getpass
import os
import sys

from . import policy
from .calc import (analyze, anomaly, budget, classify, compare, monitor, query,
                   recon, redact, schedule, scheduled, txn)
from .models import PipelineOptions, ReportModel


def _current_user():
    """当前系统用户名。只读环境变量，不读文件、不查通讯录。"""
    for fn in (getpass.getuser, lambda: ""):
        try:
            v = fn()
            if v:
                return v
        except Exception:            # noqa: BLE001  某些沙箱里 getpass 会抛
            continue
    return "—"


class Pipeline(object):
    """把 LogSource + Store 组装成一次完整的分析。"""

    def __init__(self, source, store, manifest=None):
        self.source = source
        self.store = store
        self.manifest = manifest or {}

    # ------------------------------------------------------------------
    def _cfg(self, rel, override=None):
        if override:
            return self.store.read_json(override, {}) or {}
        return self.store.read_json(rel, {}) or {}

    def _abs(self, rel, override=None):
        """配置/注册表的绝对路径。宿主决定落脚点，Core 不猜。"""
        if override:
            return self.store.resolve(override)
        return self.store.resolve(rel)

    # ------------------------------------------------------------------
    def _daily_credit(self, opts):
        """把「打算几点跑 / 系统里注册成什么 / 真跑过几天」三份并排交给前端。"""
        out_dir = opts.out_dir or self.store.resolve("out")
        try:
            cfg = self.store.read_json("config/schedule.json", {}) or {}
        except (OSError, ValueError):
            cfg = {}
        state = schedule.load_json(os.path.join(out_dir, "schedule-state.json"), {})
        rows = schedule.load_daily(os.path.join(out_dir, "credit-daily.jsonl"))

        # 每个任务自己的脚本落点：既要判断存在性，也要拼进注册命令——同一份来源
        scripts = {}
        for t in (cfg.get("tasks") or []):
            rel = t.get("script") if os.name == "nt" else t.get("scriptUnix")
            if rel and self.store.exists(rel):
                scripts[t.get("id") or t.get("name")] = self.store.resolve(rel)
        log_path = os.path.join(out_dir, "daily_credit.log")
        log_time = ""
        try:
            log_time = dt.datetime.fromtimestamp(
                os.path.getmtime(log_path)).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
        return schedule.build(
            cfg, rows, state,
            root=self.store.resolve("."),
            scripts=scripts,
            os_name="win" if os.name == "nt" else "unix",
            python_exe=_windowless_python(),
            log_time=log_time,
        )

    # ------------------------------------------------------------------
    def run(self, opts=None, state=None):
        opts = opts or PipelineOptions()
        log = opts.log

        # 1. 发现 ---------------------------------------------------------
        tools = self.source.discover(opts.root)
        if not tools:
            log("未发现可分析的 Agent 日志目录。可用 --root 指定数据目录。")
            return ReportModel(data={"meta": {}, "tasks": []}, tools=[],
                               meta={"empty": True})

        # 2. 配置 ---------------------------------------------------------
        budget_cfg = self._cfg("config/budget.json", opts.budget_config)
        rules_cfg = self._cfg("config/anomaly-rules.json", opts.rules)
        class_cfg = self._cfg("config/classification.json", opts.classification)
        if state is None:
            state = policy.default_state()
        budget_cfg["_desc"] = ("人工设置（初始 %s · 今日余额 %s）"
                               % (state.get("initial_credits"),
                                  state.get("today_balance")))

        # 3. 采集 ---------------------------------------------------------
        days = opts.days
        if opts.last:
            digits = "".join(ch for ch in opts.last if ch.isdigit())
            days = int(digits or 7)
        data, ctx = self.source.collect(
            tools, days=days, log=log, root=opts.root,
            type_rules=class_cfg.get("task_types"))

        # 4. 脱敏（必须在落盘前） ------------------------------------------
        scrubbed = redact.scrub_data(data)
        tasks = data.get("tasks") or []

        # 5. 分类 ---------------------------------------------------------
        profile = opts.complexity_profile or class_cfg.get("profile") or "spec"
        data = classify.apply(data, class_cfg, profile)

        # 6. 过滤 ---------------------------------------------------------
        dfrom, dto = query.parse_range(
            opts.date_from, opts.date_to, opts.last,
            anchor=(data["meta"]["span"][1] if data.get("meta") else None))
        if any([opts.project, opts.task_type, opts.level, dfrom, dto]):
            tasks = query.filter_tasks(tasks, project=opts.project,
                                       task_type=opts.task_type, level=opts.level,
                                       date_from=dfrom, date_to=dto)
            data["tasks"] = tasks
            data = classify.apply(data, class_cfg, profile)
            tasks = data["tasks"]

        # 7. 流水号 -------------------------------------------------------
        reg_path = self._abs("config/txn-registry.json", opts.txn_registry)
        txn_reg = txn.Registry(reg_path).load()
        txn_map = txn_reg.assign_tasks(tasks)
        try:
            txn_reg.save()
        except OSError as e:
            log("流水号注册表写入失败（%s），本次号码仅当次有效" % e)

        # 8. 异常 ---------------------------------------------------------
        has_cache = bool(((data.get("meta") or {}).get("capabilities") or {})
                         .get("cacheTokens"))
        anom = anomaly.run_rules(tasks, rules_cfg, has_cache=has_cache)
        data["rules"] = anom["rules"]
        data["anomalies"] = anom["anomalyIds"]

        # 9. 定时任务 -----------------------------------------------------
        sched = scheduled.analyze(tasks, data.get("automations") or [],
                                  (data.get("meta") or {}).get("windowDays") or 1)
        data["automations"] = [{
            "name": x["name"], "freq": x["freq"], "level": x["level"], "runs": x["runs"],
            "avg": x["avg"], "credits": x["credits"], "daily": x["daily"],
            "last7": x["last7"], "last14": x["last14"], "prev": x["prev"],
            "model": x["model"], "status": x["status"], "owner": x["owner"],
            "days": x["days"], "succ": x["succ"], "mark": x["mark"], "reason": x["reason"],
        } for x in sched["items"]]
        data["scheduled"] = sched

        # 9.5 系统计划任务（每日取数） ------------------------------------
        # 与上面 step 9 不是一回事：step 9 统计的是「Agent 里配了哪些自动化」，
        # 这里描述的是「本机系统计划任务里那个每天 9 点叫醒采集的活儿」。
        # 后者不产生 Agent 任务、不消耗积分，混进 sched 会把对账口径带歪。
        data["dailyCredit"] = self._daily_credit(opts)

        # 10. 任务监测 ----------------------------------------------------
        mon = monitor.build(tasks, ctx, data.get("automations") or [],
                            (data.get("meta") or {}).get("windowDays") or 1)
        data["monitor"] = mon

        # 11. 余额与预警 --------------------------------------------------
        ledger_paths = ([self.store.resolve(p) for p in opts.credit_ledger]
                        if opts.credit_ledger else None)
        bud = budget.compute(
            budget_cfg, tasks, ctx,
            ledger_paths=ledger_paths,
            balance_override=opts.balance_override,
            balance_date=opts.balance_date,
            thresholds=opts.thresholds,
            log=log, state=state)
        data["budget"] = bud

        # 12. 日终对账（T+1） ---------------------------------------------
        credit_ledger = budget.load_ledger(
            ledger_paths or (budget_cfg.get("credit_ledger_paths") or []), log=None)
        income_rows = recon.load_income(
            self._abs("config/credit-income.json", opts.credit_income))
        # 收入也要有号：走「固定缩写池 + 幂等键」，有业务单号时同单同号
        for i, e in enumerate(income_rows or []):
            if e.get("txn"):
                continue
            try:
                e["txn"] = txn_reg.allocate_income(
                    e.get("date"), e.get("type"), biz_no=e.get("bizNo"), idx=i)
            except (ValueError, RuntimeError) as exc:
                log("收入流水号分配失败（%s），该笔以「未登记」呈现" % exc)
        recon_out = recon.build(
            tasks, ledger=credit_ledger, state=state, income=income_rows,
            tolerance_pct=float(budget_cfg.get("reconcile_tolerance_pct", 5)),
            anchor=opts.date_to)
        recon_out["txnAssigned"] = len(txn_map) + sum(
            1 for e in (income_rows or []) if e.get("txn"))
        recon_out["incomeN"] = len(income_rows or [])
        recon_out["incomePendingN"] = sum(
            1 for e in (income_rows or []) if e.get("status") == "pending")
        data["creditRecon"] = recon_out

        # 13. 对比与建议 --------------------------------------------------
        comp = compare.build(tasks, anom, sched, bud,
                             (data.get("meta") or {}).get("windowDays") or 1)
        data["bench"] = comp["rows"]
        data["recommendations"] = comp["recommendations"]

        # 14. 总览（收敛） ------------------------------------------------
        overview = analyze.build(data, bud, anom, sched, comp["recommendations"], ctx)
        data["overview"] = overview
        data["meta"]["scrubbed"] = scrubbed
        data["meta"]["toolVersion"] = self.manifest.get("version") or "3.0.0"
        # 页面侧栏要回答「谁、用什么工具、什么版本」——这三个字段由这里统一给，
        # 前端不再各自去猜（猜出来的一个是错的，另一个迟早会不一致）。
        data["meta"]["appName"] = (self.manifest.get("displayName")
                                   or "积分对账审计应用")
        data["meta"]["appVersion"] = self.manifest.get("version") or ""
        data["meta"]["user"] = _current_user()
        data["meta"]["sources"] = [
            {"tool": str(t.get("tool") or "—"),
             "version": str(t.get("version") or ""),
             "root": str(t.get("root") or "")}
            for t in (tools or [])
        ]

        return ReportModel(
            data=data, budget=bud, overview=overview, credit_recon=recon_out,
            anomalies=anom, scheduled=sched, monitor=mon, bench=comp["rows"],
            recommendations=comp["recommendations"], state=state,
            tools=tools, meta=dict(data.get("meta") or {}))


def _windowless_python():
    """计划任务用的解释器：优先 pythonw.exe。

    cmd.exe 起的控制台窗口在「用户登录时运行」的任务里会真的闪一下，
    每天早上 9:05 在桌面弹一个黑框是会被投诉的。pythonw 没有控制台，
    代价是 stdout 丢失——所以 daily_credit 有自己的 --log 落盘。
    """
    exe = sys.executable or "python"
    if os.name == "nt":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe
