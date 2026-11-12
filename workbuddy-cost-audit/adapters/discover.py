#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台发现：定位 Agent 数据目录、版本、日志与会话规模。"""

import json
import os
import platform
from pathlib import Path

from .sandbox import assert_safe_path, safe_roots, should_skip

TOOL_DIRS = [
    ("workbuddy", ".workbuddy"),
    ("codebuddy", ".codebuddy"),
    ("claude", ".claude"),
    ("openclaw", ".openclaw"),
]


def _version_of(root: Path):
    for name in ("startup-seed-version.json", "package.json", ".version"):
        f = root / name
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                v = d.get("version") or d.get("seedVersion") or d.get("appVersion")
                if v:
                    return str(v)
            except (ValueError, OSError):
                pass
    return "unknown"


def _count(folder: Path, pattern):
    if not folder.exists():
        return 0
    try:
        return sum(1 for _ in folder.rglob(pattern))
    except OSError:
        return 0


def discover_tools(root=None, log=None):
    """返回可用数据源列表。root 指定时只探测该目录。"""
    home = Path.home()
    roots = []
    if root:
        roots = [Path(root).expanduser()]
    else:
        for name, rel in TOOL_DIRS:
            p = home / rel
            if p.exists():
                roots.append(p)

    found = []
    skipped = []
    allow = safe_roots([root] if root else None)
    for p in roots:
        try:
            rp = assert_safe_path(p, allow)
        except Exception as e:
            skipped.append({"path": str(p), "reason": str(e)})
            continue
        has_logs = (rp / "logs").exists()
        has_projects = (rp / "projects").exists()
        has_db = (rp / "workbuddy.db").exists()
        if not (has_logs or has_projects or has_db):
            skipped.append({"path": str(rp), "reason": "no_agent_data"})
            continue
        tool = rp.name.lstrip(".")
        found.append({
            "tool": tool,
            "root": rp.as_posix(),
            "version": _version_of(rp),
            "logs": (rp / "logs").as_posix() if has_logs else None,
            "projects": (rp / "projects").as_posix() if has_projects else None,
            "db": (rp / "workbuddy.db").as_posix() if has_db else None,
            "log_files": _count(rp / "logs", "*.log"),
            "project_files": _count(rp / "projects", "*.jsonl"),
            "platform": platform.system(),
            "skipped": skipped,
        })
        if log:
            log("发现数据源：%s（%s）" % (rp, tool))
    return found


def summarize(found):
    if not found:
        return "未发现可分析的 Agent 数据目录"
    parts = []
    for t in found:
        parts.append("%s@%s（日志 %d / 会话 %d）"
                     % (t["tool"], t["version"], t["log_files"], t["project_files"]))
    return "；".join(parts)


def main(argv=None):
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="发现本机 Agent 数据目录（只读探测）")
    ap.add_argument("--root", help="指定数据根目录（默认自动探测）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args(argv)

    found = discover_tools(args.root)
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0 if found else 0
    if not found:
        print("未发现可分析的 Agent 数据目录。可用 --root 指定数据目录。")
        return 0
    print("发现 %d 个数据源：" % len(found))
    for t in found:
        print("  %-10s v%-12s %s" % (t["tool"], t["version"], t["root"]))
        print("             日志 %-5d 会话 %-5d 平台 %s"
              % (t["log_files"], t["project_files"], t["platform"]))
        if t["skipped"]:
            names = sorted({(s.get("reason") if isinstance(s, dict) else str(s))
                            for s in t["skipped"]})
            print("             已跳过：%s" % "、".join(names))
    print("")
    print(summarize(found))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
