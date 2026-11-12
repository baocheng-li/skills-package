#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 宿主 —— 命令行形态的完整实现。

这一层只做「命令行才有的事」
---------------------------
    argparse 解析、交互授权、控制台摘要、退出码、审计日志、输出体积守卫

业务计算全部在 ``core.pipeline``；产物格式全部在 ``renderer``。
判断标准很简单：如果某段代码在桌面版/插件/Web 里也照样需要，它就不该在这里。

退出码（与 audit.py 历史行为一致，定时任务与 CI 依赖它）
    0 正常 / 2 黄色 / 3 橙色 / 4 红色 / 1 错误
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import sandbox as sandbox_mod       # noqa: E402
from adapters import state as state_mod           # noqa: E402
from adapters.store import FsStore                # noqa: E402
from adapters.workbuddy.source import WorkBuddySource  # noqa: E402
from core import pipeline as pipeline_mod         # noqa: E402
from core.calc import ledger as ledger_mod        # noqa: E402
from core.calc import query as query_mod          # noqa: E402
from core.models import PipelineOptions           # noqa: E402
from renderer import data as data_r               # noqa: E402
from renderer import docs as docs_r               # noqa: E402
from renderer import html as html_r               # noqa: E402

EXIT = {"ok": 0, "warn": 2, "crit": 3, "red": 4, "error": 1}


def load_json(path, default=None):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}


def parse_thresholds(s):
    """解析 --threshold：今日消耗占当前余额的百分比阈值。

    warn=10,crit=20,red=30   → 消耗占当前余额达 10% 黄、20% 橙、30% 红（可带 % 号）
    """
    if not s:
        return {}
    out = {"mode": "pct"}
    for part in s.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().lower(), v.strip().rstrip("%")
        if k not in ("warn", "crit", "red"):
            continue
        try:
            out[k] = float(v)
        except ValueError:
            continue
    return out


def authorize(tools, cfg, out_dir, yes=False, ledger=None, balance=None):
    lines = ["即将读取以下位置（只读，无网络）："]
    for t in tools:
        lines.append("  工具: %-10s 版本: %-12s 路径: %s"
                     % (t["tool"], t["version"], t["root"]))
    lines.append("  读取范围: 日志 *.log / 会话 *.jsonl / workbuddy.db（只读副本）")
    lines.append("  积分数据来源: %s" % (cfg.get("_desc") or "未配置（报告为 L4，预警禁用）"))
    if balance:
        lines.append("  余额设置: %s（初始 %s · 今日余额 %s）" % (
            balance.get("path"), balance.get("initial_credits"),
            balance.get("today_balance")))
    if ledger:
        lines.append("  积分流水: %s" % ", ".join(ledger))
    lines.append("  写权限: 仅 %s" % out_dir)
    print("\n".join(lines))
    if yes:
        return True
    if not sys.stdin.isatty():
        print("非交互环境且未指定 --yes，按未授权处理。")
        return False
    return input("是否授权？[y/N] ").strip().lower() in ("y", "yes")


def _n(v, dash="未设置"):
    if v is None:
        return dash
    return "{:,.0f}".format(round(float(v)))


def _ratio_txt(budget):
    if budget.get("spendRatioOver"):
        return ">100%"
    r = budget.get("spendRatio")
    return ("%.1f%%" % r) if r is not None else "—"


def build_parser():
    ap = argparse.ArgumentParser(
        prog="audit.py",
        description="积分对账审计应用 · 只读、离线")
    ap.add_argument("--format", default="all",
                    help="输出格式：html / json / csv / all（可逗号组合）")
    ap.add_argument("--out", default="./cost-audit-output", help="输出目录")
    ap.add_argument("--root", help="数据根目录（默认自动发现）")
    ap.add_argument("--days", type=int, default=0, help="统计窗口天数；0 = 全部历史")
    ap.add_argument("--last", help="相对区间，如 7d / 30d")
    ap.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--project", help="按项目过滤")
    ap.add_argument("--task-type", help="按任务类型过滤")
    ap.add_argument("--level", help="按复杂度过滤 L1—L4")
    ap.add_argument("--credit-ledger", action="append", default=[],
                    help="积分流水文件（jsonl/csv），可多次指定")
    ap.add_argument("--config", default=str(ROOT / "config" / "budget.json"))
    ap.add_argument("--rules", default=str(ROOT / "config" / "anomaly-rules.json"))
    ap.add_argument("--credit-rules", default=str(ROOT / "config" / "credit-rules.json"))
    ap.add_argument("--classification", default=str(ROOT / "config" / "classification.json"))
    ap.add_argument("--complexity-profile", default=None, choices=["spec", "quantile"])
    ap.add_argument("--balance", type=float, help="临时当前余额（只影响本次运行，不写状态文件）")
    ap.add_argument("--balance-date", help="临时余额对应日期")
    ap.add_argument("--set-initial", type=float, metavar="N", help="写入初始积分到余额状态文件")
    ap.add_argument("--set-initial-date", metavar="YYYY-MM-DD",
                    help="初始积分对应的日期；日终对账从这一天开始链式结转")
    ap.add_argument("--set-today-balance", type=float, metavar="N",
                    help="写入今日余额到余额状态文件（默认取数据窗口最后一天）")
    ap.add_argument("--set-thresholds", metavar="warn=10,crit=20,red=30",
                    help="写入告警阈值（今日消耗占当前余额的百分比）")
    ap.add_argument("--balance-state", help="余额状态文件（默认 config/balance-state.json）")
    ap.add_argument("--txn-registry", help="交易流水号注册表（默认 config/txn-registry.json）")
    ap.add_argument("--credit-income", help="人工登记的积分收入（默认 config/credit-income.json）")
    ap.add_argument("--threshold",
                    help="预警阈值：warn=10%%,crit=20%%,red=30%%（今日消耗占当前余额的百分比）")
    ap.add_argument("--scheduled-only", action="store_true", help="只分析定时任务")
    ap.add_argument("--budget-only", action="store_true", help="只输出预警与退出码")
    ap.add_argument("--compare-models", action="store_true", help="输出模型对比摘要")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认（仍需白名单校验）")
    ap.add_argument("--open", action="store_true", help="生成后打开报告")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--skin", default=None, choices=["dark", "light"],
                    help="报告初始主题（默认 dark；页面内可随时切换）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    def log(msg):
        if not args.quiet:
            print(msg, flush=True)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 沙箱自检 ---------------------------------------------------------
    manifest = load_json(ROOT / "manifest.json", {})
    try:
        sandbox_mod.run_checks(out_dir, manifest)
    except sandbox_mod.SandboxError as e:
        print("安全自检未通过：%s" % e)
        return EXIT["error"]

    # 2. 发现（授权前先告诉人要读哪里） -------------------------------------
    source = WorkBuddySource(log=log)
    tools = source.discover(args.root)
    if not tools:
        print("未发现可分析的 Agent 日志目录。可用 --root 指定数据目录。")
        return EXIT["ok"]

    # 3. 状态写入 + 授权 ---------------------------------------------------
    state_path = (Path(args.balance_state).expanduser() if args.balance_state
                  else state_mod.default_path())
    if args.set_initial is not None or args.set_today_balance is not None \
            or args.set_thresholds:
        state_mod.set_manual(
            initial=args.set_initial,
            today_balance=args.set_today_balance,
            thresholds=parse_thresholds(args.set_thresholds),
            balance_date=args.balance_date,
            initial_date=args.set_initial_date,
            path=state_path)
        print("已写入余额设置：%s" % state_path)
    state = state_mod.load(state_path)

    budget_cfg = load_json(args.config, {})
    budget_cfg["_desc"] = ("人工设置（初始 %s · 今日余额 %s）"
                           % (state.get("initial_credits"), state.get("today_balance")))
    balance_hint = {"path": str(state_path),
                    "initial_credits": state.get("initial_credits"),
                    "today_balance": state.get("today_balance")}
    if not authorize(tools, budget_cfg, out_dir, args.yes, args.credit_ledger, balance_hint):
        print("用户未授权，退出。")
        return EXIT["ok"]

    # 4. 分析（Core 编排，唯一入口） ----------------------------------------
    store = FsStore(ROOT, out_dir)
    opts = PipelineOptions(
        root=args.root, days=args.days, last=args.last,
        date_from=args.date_from, date_to=args.date_to,
        project=args.project, task_type=args.task_type, level=args.level,
        credit_ledger=list(args.credit_ledger or []),
        credit_income=args.credit_income,
        balance_override=args.balance, balance_date=args.balance_date,
        balance_state=str(state_path),
        thresholds=parse_thresholds(args.threshold),
        budget_config=args.config, rules=args.rules,
        credit_rules=args.credit_rules, classification=args.classification,
        complexity_profile=args.complexity_profile,
        txn_registry=args.txn_registry, on_log=log,
        out_dir=str(out_dir),
    )
    try:
        model = pipeline_mod.Pipeline(source, store, manifest).run(opts, state=state)
    except Exception as e:                              # noqa: BLE001
        print("采集失败：%s" % e)
        return EXIT["error"]

    if (model.meta or {}).get("empty"):
        return EXIT["ok"]

    data = model.data
    budget = model.budget
    anom = model.anomalies
    sched = model.scheduled
    mon = model.monitor
    comp_rows = model.bench
    overview = model.overview
    scrubbed = data["meta"].get("scrubbed")
    fmt = {x.strip().lower() for x in (args.format or "all").split(",") if x.strip()}
    if "all" in fmt:
        fmt = {"html", "json", "csv"}

    # 5. 只报预算 ---------------------------------------------------------
    if args.budget_only:
        p = out_dir / "budget_status.json"
        data_r.write_json(str(p), budget)
        print("初始积分 %s / 今日余额 %s / 今日消耗 %.0f / 当前余额 %s（预警比 %s）"
              % (_n(budget.get("initial")), _n(budget.get("todayBalance")),
                 budget.get("todaySpent") or 0, _n(budget.get("balance")),
                 _ratio_txt(budget)))
        print("告警 %s（退出码 %d）：%s"
              % (budget.get("levelText") or "未配置", budget["exitCode"],
                 budget.get("reason") or ""))
        return budget["exitCode"] or EXIT["ok"]

    written = []
    # 监测状态每次运行都写：refresh_report.py 的 last-refresh.json 与页面侧栏都读它。
    written.append(("budget_status.json",
                    data_r.write_json(str(out_dir / "budget_status.json"), budget)))

    # 6. JSON / Markdown --------------------------------------------------
    if "json" in fmt:
        written.append(("summary.json", data_r.write_json(
            str(out_dir / "summary.json"), {
                "version": data["meta"]["toolVersion"],
                "generatedAt": data["meta"]["generatedAt"],
                "totals": overview["totals"],
                "quality": overview["quality"],
                "source": overview["source"],
                "classification": data.get("classification"),
                "banner": overview["banner"],
                "creditRecon": data.get("creditRecon", {}).get("totals"),
            })))
        written.append(("anomalies.json", data_r.write_json(
            str(out_dir / "anomalies.json"), {
                "rules": anom["rules"], "totalWaste": anom["totalWaste"],
                "hitCount": anom["hitCount"], "tasks": anom["anomalyIds"],
            })))
        written.append(("recommendations.json", data_r.write_json(
            str(out_dir / "recommendations.json"), model.recommendations)))
        written.append(("recommendations.md", data_r.write_md(
            str(out_dir / "recommendations.md"),
            docs_r.recommendations_markdown(model.recommendations, budget))))

    # 7. CSV --------------------------------------------------------------
    if "csv" in fmt:
        led = ledger_mod.build(data["tasks"])
        written.append(("tasks.csv", data_r.write_csv(
            str(out_dir / "tasks.csv"), led["tasks"], data_r.TASK_COLS)))
        written.append(("steps.csv", data_r.write_csv(
            str(out_dir / "steps.csv"), led["steps"], data_r.STEP_COLS)))
        written.append(("daily_stats.csv", data_r.write_csv(
            str(out_dir / "daily_stats.csv"), query_mod.daily_stats(data["tasks"]),
            data_r.DAILY_COLS)))
        written.append(("scheduled_summary.csv", data_r.write_csv(
            str(out_dir / "scheduled_summary.csv"), [{
                "name": x["name"], "freq": x["freq"], "runs": x["runs"],
                "avg_credits": x["avg"], "window_credits": x["credits"],
                "last7_credits": x["last7"],
                "success_rate": x["succ"] if x["succ"] is not None else "",
                "mark": x["mark"], "reason": x["reason"],
            } for x in sched["items"]], data_r.SCHED_COLS)))
        written.append(("monitor_records.csv", data_r.write_csv(
            str(out_dir / "monitor_records.csv"), mon.get("records") or [],
            data_r.MON_COLS)))

    # 8. HTML -------------------------------------------------------------
    if "html" in fmt:
        skin = args.skin or "dark"
        written.append(("report.html", html_r.render(
            str(out_dir / "report.html"), data, skin=skin)))

    # 9. 审计日志 ---------------------------------------------------------
    digest = hashlib.sha256(json.dumps(
        overview.get("totals", {}), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    audit = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "version": data["meta"]["toolVersion"],
        "host": "cli",
        "argv": sys.argv[1:],
        "roots": [t["root"] for t in tools],
        "files": {"logs": sum(t["log_files"] for t in tools),
                  "projects": sum(t["project_files"] for t in tools)},
        "outputs": [{"name": n, "bytes": s} for n, s in written],
        "digest": digest,
        "redactions": scrubbed,
        "balanceSource": budget["source"],
    }
    with io.open(out_dir / "audit-log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(audit, ensure_ascii=False) + "\n")

    try:
        sandbox_mod.guard_output_size([out_dir / n for n, _ in written]
                                      + [out_dir / "audit-log.jsonl"])
    except sandbox_mod.SandboxError as e:
        print("输出超限：%s" % e)
        return EXIT["error"]

    # 10. 控制台摘要 ------------------------------------------------------
    t = overview["totals"]
    print("")
    print("任务 %d 个 / 步骤 %s 个 / 窗口 %d 天（%s → %s）"
          % (t["tasks"], data["meta"]["counts"]["steps"], data["meta"]["windowDays"],
             data["meta"]["span"][0], data["meta"]["span"][1]))
    print("消耗 %.1f 积分 · %.1f 亿 input token · 成功率 %.1f%% · 均 %.1f 积分/任务"
          % (t["credits"], t["inputTokens"] / 1e8, t["successRate"] * 100,
             t["avgCredits"]))
    print("异常 %d 个任务命中 / 止损空间 %.0f 积分（不提供回收）；不可见执行 %d 次 / %.0f 积分（占 %.1f%%）"
          % (anom["hitCount"], anom["totalWaste"], mon["totals"]["unseenTasks"],
             mon["totals"]["unseenCredits"], mon["totals"]["unseenShare"] * 100))
    if budget.get("configured"):
        print("积分监测：初始 %s · 今日余额 %s · 今日消耗 %.0f · 当前余额 %s（预警比 %s）· %s"
              % (_n(budget.get("initial")), _n(budget.get("todayBalance")),
                 budget.get("todaySpent") or 0, _n(budget.get("balance")),
                 _ratio_txt(budget), budget.get("levelText")))
    else:
        print("积分监测：未配置（初始积分 / 今日余额均为空），本页只报实测消耗")
    crecon = model.credit_recon or {}
    ct = crecon.get("totals") or {}
    if ct:
        print("积分对账：%d 天 · 账实一致 %d / 偏差 %d / 待对账 %d / 无实际余额 %d · "
              "流水号 %d 个（16 位，全局唯一）"
              % (ct.get("days", 0), ct.get("matched", 0), ct.get("diff", 0),
                 ct.get("pending", 0), ct.get("nobal", 0), ct.get("txns", 0)))
        if crecon.get("t1", {}).get("note"):
            print("  T+1：%s" % crecon["t1"]["note"])
    print("输出目录：%s" % out_dir)
    for n, s in written:
        print("  - %-22s %6.2f MB" % (n, s / 1048576.0) if s > 1024 * 1024
              else "  - %-22s %6.1f KB" % (n, s / 1024.0))

    if args.compare_models:
        print("\n模型对比（按复杂度层级，样本 ≥3 才给结论）：")
        for r in sorted(comp_rows, key=lambda x: (x["lvl"], -x["n"]))[:12]:
            print("  %s %-22s n=%-3d %6.1f 积分/任务  成功率 %5.1f%%  %s"
                  % (r["lvl"], r["model"], r["n"], r["credits"], r["succ"],
                     "" if r["sampleOK"] else "（样本不足）"))

    if args.scheduled_only:
        print("\n定时任务：")
        for x in sched["items"]:
            print("  %-34s %-14s 单次 %5.0f · 周 %6.0f · %s"
                  % (x["name"][:34], x["freq"], x["avg"], x["weekly"], x["mark"]))

    if args.open and "html" in fmt:
        webbrowser.open((out_dir / "report.html").as_uri())

    return budget["exitCode"] or EXIT["ok"]


if __name__ == "__main__":
    sys.exit(main())
