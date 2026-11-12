#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据产物渲染 —— JSON / CSV。

只做「已经算好的行 → 文件」，不做任何聚合：聚合在 Core，格式在 Renderer。
这条边界让「换一种表格格式」不需要碰计算，也让「换一种压缩策略」不需要
碰 CSV 的列顺序。
"""

import csv
import io
import json
import os

# 列顺序即契约：下游（Excel / 透视表 / 数据看板）按列名取值，
# 调整顺序不会破坏下游，但改名会——改名前先在 references 里登记。
# 任务级 / 步骤级列名与 BRD 附录 E 对齐（与 core.calc.ledger 保持一致，
# 差别只在「谁负责组装行」：行由 Core 算，列顺序由 Renderer 定）。
TASK_COLS = [
    "task_id", "project", "task_type", "complexity", "trigger_type",
    "start_time", "end_time", "total_input_tokens", "total_output_tokens",
    "total_cache_read_tokens", "total_credits", "duration_ms", "model_chain",
    "step_count", "retry_count", "status", "anomaly_flags",
]
STEP_COLS = [
    "task_id", "step_index", "model_id", "input_tokens", "output_tokens",
    "cache_read_tokens", "credits", "duration_ms", "tool_name", "retry_count",
    "timestamp", "phase",
]
DAILY_COLS = ["date", "project", "tasks", "credits", "input_tokens",
              "output_tokens", "total_tokens", "failed", "duration_s", "task_types"]
SCHED_COLS = ["name", "freq", "runs", "avg_credits", "window_credits",
              "last7_credits", "success_rate", "mark", "reason"]
MON_COLS = ["ts", "cat", "name", "taskId", "credits", "steps", "status",
            "model", "level", "trigger", "rtype", "duration"]


def rows_of(items, cols):
    """按列名投影，缺列补空串——下游永远拿到定长行。"""
    out = []
    for it in items or []:
        out.append({c: ("" if it.get(c) is None else it.get(c)) for c in cols})
    return out


def csv_text(items, cols):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore",
                       lineterminator="\n")
    w.writeheader()
    for r in rows_of(items, cols):
        w.writerow(r)
    return buf.getvalue()


def write_csv(path, items, cols):
    """写 CSV（UTF-8 无 BOM，Excel 打开中文可能乱码；下游脚本读取优先）。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    text = csv_text(items, cols)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return os.path.getsize(path)


def write_json(path, obj, indent=2):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    return os.path.getsize(path)


def write_md(path, text):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.getsize(path)
