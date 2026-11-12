#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F3 台账：任务级与步骤级导出（字段名与 BRD 附录 E 对齐）。"""

import csv
import io

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


def _end_time(t):
    stamps = [s.get("ts") for s in t.get("steps", []) if s.get("ts")]
    return max(stamps) if stamps else (t.get("ts") or "")


def task_rows(tasks):
    rows = []
    for t in tasks:
        rows.append({
            "task_id": t.get("id"),
            "project": t.get("project") or "",
            "task_type": t.get("type") or "",
            "complexity": t.get("level") or "",
            "trigger_type": t.get("trigger") or "",
            "start_time": t.get("ts") or "",
            "end_time": _end_time(t),
            "total_input_tokens": t.get("inTok") or 0,
            "total_output_tokens": t.get("outTok") or 0,
            "total_cache_read_tokens": "",
            "total_credits": t.get("credits") or 0,
            "duration_ms": int(round((t.get("duration") or 0) * 1000)),
            "model_chain": t.get("modelChain") or "",
            "step_count": len(t.get("steps") or []),
            "retry_count": t.get("retry") or 0,
            "status": t.get("status") or "",
            "anomaly_flags": ",".join(t.get("flagIds") or []),
        })
    return rows


def step_rows(tasks):
    rows = []
    for t in tasks:
        for s in t.get("steps") or []:
            rows.append({
                "task_id": t.get("id"),
                "step_index": s.get("i"),
                "model_id": s.get("model") or "",
                "input_tokens": s.get("inTok") or 0,
                "output_tokens": s.get("outTok") or 0,
                "cache_read_tokens": "",
                "credits": s.get("credits") if s.get("credits") is not None else "",
                "duration_ms": s.get("ms") or 0,
                "tool_name": s.get("tool") or "",
                "retry_count": s.get("retry") or 0,
                "timestamp": s.get("ts") or "",
                "phase": s.get("phase") or "",
            })
    return rows


def build(tasks):
    return {"tasks": task_rows(tasks), "steps": step_rows(tasks)}


def write_csv(path, rows, cols):
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)
