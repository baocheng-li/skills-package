#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 宿主 —— 给 Agent 用的形态。

和 CLI 的区别
-------------
CLI 的读者是人，所以打印表格、开浏览器、等交互回车；
Skill 的读者是模型，所以**只回 JSON**，且必须自带口径解释——模型不会去翻
页面上的小字，它只会读你给它的这几个字段。所以这里刻意不做「精简版」，
而是给「够用且可解释」的一组字段：

    verdict       一句话结论（含告警级别与关键数字）
    metrics       所有会被引用的数字，键名直接写口径（todaySpent/balance/...）
    warnings      需要注意的事（未配置、凭证失效、待对账）
    advice        三档建议（可直接转述）
    artifacts     产物路径（需要给人看时再打开）

调用方式
--------
    python -m hosts.skill --input '{"last":"7d"}'
    echo '{"last":"7d"}' | python -m hosts.skill
"""

import json
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import state as state_mod          # noqa: E402
from adapters.store import FsStore               # noqa: E402
from adapters.workbuddy.source import WorkBuddySource  # noqa: E402
from core import pipeline as pipeline_mod        # noqa: E402
from core.models import PipelineOptions          # noqa: E402
from renderer import docs as docs_r              # noqa: E402
from renderer import html as html_r              # noqa: E402


def _opts(payload):
    p = payload or {}
    return PipelineOptions(
        last=p.get("last"), days=int(p.get("days") or 0),
        date_from=p.get("from"), date_to=p.get("to"),
        project=p.get("project"), task_type=p.get("task_type"),
        level=p.get("level"),
        balance_override=p.get("balance"),
        thresholds=p.get("thresholds") or {},
    )


def run(payload=None, render_html=False, out_dir=None):
    """跑一次分析，返回 Skill 形态的结论 dict（可 JSON 序列化）。"""
    payload = payload or {}
    out_dir = Path(out_dir or (ROOT / "out")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    state = state_mod.load(payload.get("balance_state"))
    store = FsStore(ROOT, out_dir)
    source = WorkBuddySource()
    model = pipeline_mod.Pipeline(source, store).run(_opts(payload), state=state)

    if (model.meta or {}).get("empty"):
        return {"ok": False, "verdict": "未发现可分析的 Agent 日志目录。",
                "metrics": {}, "warnings": ["可用 root 指定数据目录"], "advice": []}

    t = model.totals()
    b = model.budget
    ct = (model.credit_recon or {}).get("totals") or {}
    banners = model.banners
    top = banners[0] if banners else {}

    warnings = []
    if not b.get("configured"):
        warnings.append("未配置初始积分/今日余额，积分监测不可用，本报告只报实测消耗。")
    snap = (state.get("today_balance_snapshot") or {})
    if state.get("today_balance") is not None and not snap:
        warnings.append("今日余额没有凭证截图，数字来源无法复核。")
    if ct.get("pending"):
        warnings.append("有 %d 天的账待 T+1 核对（当日账要到次日 00:00 才能对）。"
                        % ct["pending"])
    if ct.get("diff"):
        warnings.append("有 %d 天账面与实际余额存在偏差，见对账页明细。" % ct["diff"])

    advice = []
    for tier, items in ((model.recommendations or {}).get("tiers") or {}).items():
        for it in items[:3]:
            advice.append({"tier": tier, "title": it["title"], "action": it["action"],
                           "evidence": it["evidence"]})

    artifacts = []
    if render_html:
        size = html_r.render(str(out_dir / "report.html"), model.data,
                             skin=payload.get("skin") or "dark")
        artifacts.append({"name": "report.html", "bytes": size,
                          "path": str(out_dir / "report.html")})

    verdict = "%s：窗口内消耗 %s 积分（%s 个任务），当前余额 %s，预警比 %s。" % (
        top.get("title") or b.get("levelText") or "正常",
        _n(t.get("credits")), t.get("tasks"), _n(b.get("balance")),
        ("%.1f%%" % b["spendRatio"]) if b.get("spendRatio") is not None else "—")

    return {
        "ok": True,
        "verdict": verdict,
        "level": model.level,
        "metrics": {
            "windowDays": (model.meta or {}).get("windowDays"),
            "span": (model.meta or {}).get("span"),
            "tasks": t.get("tasks"),
            "steps": ((model.meta or {}).get("counts") or {}).get("steps"),
            "credits": t.get("credits"),
            "avgCreditsPerTask": t.get("avgCredits"),
            "successRate": t.get("successRate"),
            "inputTokens": t.get("inputTokens"),
            "todaySpent": b.get("todaySpent"),
            "todayBalance": b.get("todayBalance"),
            "balance": b.get("balance"),
            "spendRatioPct": b.get("spendRatio"),
            "initialCredits": b.get("initial"),
            "initialSource": state.get("initial_source"),
            "anomalyTasks": (model.anomalies or {}).get("hitCount"),
            "wasteCredits": (model.anomalies or {}).get("totalWaste"),
            "unseenRuns": ((model.monitor or {}).get("totals") or {}).get("unseenTasks"),
            "recon": {"days": ct.get("days"), "matched": ct.get("matched"),
                      "diff": ct.get("diff"), "pending": ct.get("pending")},
        },
        "warnings": warnings,
        "advice": advice,
        "artifacts": artifacts,
        "dataSource": (model.overview.get("source") or {}),
    }


def _n(v):
    if v is None:
        return "未设置"
    return "{:,.0f}".format(round(float(v)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    payload = {}
    if "--input" in argv:
        payload = json.loads(argv[argv.index("--input") + 1])
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            payload = json.loads(raw)
    # html 有两个入口：命令行 --html，或 payload 里的 "html": true。
    # 后者是 skill.json / DEPLOY.md 对外承诺的写法（Agent 只会传 JSON，不会拼参数），
    # 之前只认 --html，于是照文档调用的人拿到 artifacts: [] 却看不出哪里错了。
    want_html = ("--html" in argv) or bool(payload.get("html"))
    res = run(payload, render_html=want_html)
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
