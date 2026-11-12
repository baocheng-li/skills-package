#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""领域模型。

为什么不给每个字段都建 dataclass
--------------------------------
采集结果本身是一棵 4–5 层、字段上百的树（任务 → 步骤 → 模型/工具/阶段），
给每一层都建 dataclass 只会带来巨量的 ``to_dict`` 样板代码，而收益是
「编辑器能补全」——对一个 31 项验收测试兜底的项目，这个交易不划算。

所以这里只给**边界**建模：
    PipelineOptions   一次分析的输入（谁调用都得说清这些）
    ReportModel       一次分析的输出（渲染器只认这个）
内部细节继续用 dict，字段约定写在 references/report-template.md。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PipelineOptions:
    """一次分析的输入参数。全部可选，缺省即「按本机默认配置跑全部历史」。"""

    root: Optional[str] = None
    days: int = 0
    last: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    project: Optional[str] = None
    task_type: Optional[str] = None
    level: Optional[str] = None

    # 积分口径
    credit_ledger: List[str] = field(default_factory=list)
    credit_income: Optional[str] = None
    balance_override: Optional[float] = None
    balance_date: Optional[str] = None
    balance_state: Optional[str] = None
    thresholds: Dict[str, float] = field(default_factory=dict)

    # 规则与分类
    budget_config: Optional[str] = None
    rules: Optional[str] = None
    credit_rules: Optional[str] = None
    classification: Optional[str] = None
    complexity_profile: Optional[str] = None
    txn_registry: Optional[str] = None

    # 产物目录：日账、计划任务状态都落在 out/ 下，Core 不猜路径
    out_dir: Optional[str] = None

    # 行为
    skip_sandbox: bool = False
    on_log: Any = None                  # callable(str)，用于把进度送出去

    def log(self, msg):
        if callable(self.on_log):
            self.on_log(msg)


@dataclass
class ReportModel:
    """一次分析的完整结果。Renderer 只吃这个，不碰 Core 内部。"""

    data: Dict[str, Any]                   # 采集 + 分析后的完整数据树
    budget: Dict[str, Any] = field(default_factory=dict)
    overview: Dict[str, Any] = field(default_factory=dict)
    credit_recon: Dict[str, Any] = field(default_factory=dict)
    anomalies: Dict[str, Any] = field(default_factory=dict)
    scheduled: Dict[str, Any] = field(default_factory=dict)
    monitor: Dict[str, Any] = field(default_factory=dict)
    bench: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    written: List[Any] = field(default_factory=list)   # [(name, bytes), ...]

    # ---- 便捷视图：控制台摘要与退出码都从这三个取，避免各处重复算 ----
    @property
    def banners(self):
        """横幅是**一组**（可能同时有「余额告警」和「采集层风险」两条）。"""
        b = self.overview.get("banner") or []
        return b if isinstance(b, list) else [b]

    @property
    def level(self):
        order = {"green": 0, "gray": 0, "none": 0, "yellow": 1, "orange": 2, "red": 3}
        best = "none"
        for b in self.banners:
            lv = str((b or {}).get("level") or "").lower()
            if order.get(lv, 0) > order.get(best, 0):
                best = lv
        return best if best != "none" else "none"

    @property
    def exit_code(self):
        return self.budget.get("exitCode") or 0

    def totals(self):
        return self.overview.get("totals") or {}

    def as_dict(self):
        """给 JSON 渲染器用的扁平快照（不含 tasks，避免整份日志进 summary）。"""
        return {
            "version": self.meta.get("toolVersion"),
            "generatedAt": self.data.get("meta", {}).get("generatedAt"),
            "totals": self.totals(),
            "quality": self.overview.get("quality"),
            "source": self.overview.get("source"),
            "classification": self.data.get("classification"),
            "banner": self.overview.get("banner"),
            "budget": self.budget,
            "creditRecon": (self.credit_recon or {}).get("totals"),
            "anomalies": {
                "rules": self.anomalies.get("rules"),
                "totalWaste": self.anomalies.get("totalWaste"),
                "hitCount": self.anomalies.get("hitCount"),
            },
            "recommendations": self.recommendations,
        }
