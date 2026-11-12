#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脱敏：在解析层对任务名、工具摘要做模式替换，避免密钥进入报告。"""

import re

PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{10,}"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{12,}"), "[REDACTED_AWSKEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(cookie|set-cookie)\s*[:=]\s*[^;\s]{6,}"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*[\"']?[^\"'\s]{6,}"),
     r"\1=[REDACTED]"),
]


def scrub_text(s, limit=None):
    if not isinstance(s, str) or not s:
        return s
    out = s
    for pat, rep in PATTERNS:
        out = pat.sub(rep, out)
    if limit:
        out = out[:limit]
    return out


def scrub_data(data):
    hits = 0
    for t in data.get("tasks", []):
        before = t.get("name") or ""
        t["name"] = scrub_text(before)
        if t["name"] != before:
            hits += 1
        for s in t.get("steps", []):
            if s.get("snippet"):
                b = s["snippet"]
                s["snippet"] = scrub_text(b)
                if s["snippet"] != b:
                    hits += 1
    return hits
