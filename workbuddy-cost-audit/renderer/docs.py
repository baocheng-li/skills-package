#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档产物渲染 —— Markdown 建议书。

为什么单独一份 Markdown
----------------------
HTML 是给人「看」的，Markdown 是给人「引用、贴进周报、进知识库」的。
同一份 recommendations 出两种形态，比让人从 HTML 里复制粘贴靠谱。

这里的措辞原则：不承诺收益。所有数字都是「窗口内已发生的消耗」，
不是「未来能省下的钱」——审计报告一旦开始许诺节省，可信度就没了。
"""


def _n(v, dash="—"):
    if v is None:
        return dash
    return "{:,.0f}".format(round(float(v)))


def recommendations_markdown(recs, budget):
    lines = ["# 消耗治理建议", "",
             "> 数据来源：本机只读采集；建议仅作参考，执行需人工确认。", ""]

    meta = [
        ("余额设置", ("初始 %s / 今日余额 %s" % (_n(budget.get("initial")),
                                          _n(budget.get("todayBalance"))))
         if budget.get("configured") else "未配置（积分监测不可用）"),
        ("当前余额", ("%s 积分" % _n(budget.get("balance")))
         if budget.get("balance") is not None else "未计算"),
        ("今日消耗", "%s 积分" % _n(budget.get("todaySpent") or 0)),
        ("近 7 日实测日均", "%.1f 积分" % (budget.get("dailyAvg7") or 0)),
    ]
    for k, v in meta:
        lines.append("- **%s**：%s" % (k, v))
    lines.append("")

    names = {"t1": "第一档 · 立即可做（低风险）",
             "t2": "第二档 · 需要验证（中风险）",
             "t3": "第三档 · 结构性优化（高价值）"}
    for k in ("t1", "t2", "t3"):
        items = (recs or {}).get("tiers", {}).get(k, [])
        lines.append("## %s" % names[k])
        if not items:
            lines.append("_本档暂无建议（样本不足或未触发阈值）_")
            lines.append("")
            continue
        for i, it in enumerate(items, 1):
            lines.append("### %d. %s" % (i, it["title"]))
            lines.append("- 动作：%s" % it["action"])
            lines.append("- 依据：%s" % it["evidence"])
            if it.get("windowSaving"):
                lines.append("- 窗口内实测止损空间：%.0f 积分（已发生消耗，向前止损）"
                             % it["windowSaving"])
            if it.get("taskIds"):
                lines.append("- 可复核任务：`%s`" % "`, `".join(it["taskIds"]))
            lines.append("- 风险：%s" % it.get("risk", "—"))
            lines.append("")
    return "\n".join(lines)


def explain_card(model):
    """单页说明（导出说明按钮用）：把口径、数据源、限制写清楚。"""
    t = model.totals() if hasattr(model, "totals") else {}
    db = model.budget or {}
    return "\n".join([
        "# 本报告怎么读",
        "",
        "## 数据从哪来",
        "本机只读采集：Agent 日志、会话记录、本地 sqlite（只读副本）。",
        "**不联网，不上传，不改客户端配置。**",
        "",
        "## 三个口径",
        "- **窗口内消耗**：%s 积分（%s 个任务）" % (_n(t.get("credits")), t.get("tasks")),
        "- **今日消耗**：%s 积分（当日日志实时汇总）" % _n(db.get("todaySpent") or 0),
        "- **当前余额**：%s 积分（今日余额 − 今日消耗）" % _n(db.get("balance")),
        "",
        "## 哪些数不是测出来的",
        "- 初始积分与今日余额是**人工设置**的，来源标注在侧栏与对账页（measured / derived / manual）；",
        "- 积分为客户端上报值；缺失时按实测折算率补算，并在步骤上标 `est`；",
        "- 余额凭证（截图）与哈希的绑定关系见「积分对账 → 日终对账」的凭证条。",
        "",
        "## 报告不做什么",
        "- 不回收任何积分，不自动处置任务；",
        "- 不预测未来花费；所有「止损空间」都是窗口内**已发生**的消耗。",
        "",
    ])
