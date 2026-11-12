#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时重跑采集脚本（供系统计划任务 / cron 直接调用）。

为什么要有这个脚本
------------------
报告是**离线单文件 HTML**，页面里的监测数据在生成那一刻就固定了；
要让「当前余额 / 今日消耗 / 预警比 / 定时任务消耗」持续保持最新，
必须定时重跑一次采集。

本脚本把 audit.py 包一层，好处有三个：

  1) 触发时只跑本机 Python，**不经过 WorkBuddy 对话**，因此不消耗对话额度；
  2) 口径与 audit.py **完全一致**（只读采集、不联网、不改客户端配置）；
  3) 自带**并发锁**与**运行日志**，上一次没跑完不会叠一个。

用法
----
    python3 -m hosts.commands.refresh_report --out out --format all --last 30d
    python3 -m hosts.commands.refresh_report --out out --format html --log out/refresh.log

退出码与 audit.py 一致：0 正常 / 2 黄色告警 / 3 橙色告警 / 4 红色告警 / 1 错误。
"""

import argparse
import datetime as dt
import io
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import audit  # noqa: E402

LOCK_NAME = ".refresh.lock.json"
STATE_NAME = "last-refresh.json"


class Tee(object):
    """把标准输出同时写到终端与日志文件。"""

    def __init__(self, stream, handle):
        self.stream = stream
        self.handle = handle

    def write(self, text):
        try:
            self.stream.write(text)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            self.handle.write(text)
        except Exception:                                     # noqa: BLE001
            pass
        return len(text)

    def flush(self):
        for s in (self.stream, self.handle):
            try:
                s.flush()
            except Exception:                                 # noqa: BLE001
                pass


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _lock_path(out_dir):
    return out_dir / LOCK_NAME


def _acquire_lock(out_dir, timeout_s):
    """拿到锁返回路径，拿不到返回 None（说明上一次还在跑）。"""
    path = _lock_path(out_dir)
    now = time.time()
    if path.exists():
        info = {}
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        try:
            age = now - float(info.get("ts") or 0)
        except (TypeError, ValueError):
            age = timeout_s + 1
        if info.get("state") == "running" and age < timeout_s:
            return None
    try:
        path.write_text(json.dumps({"state": "running", "ts": now, "pid": os.getpid()}),
                        encoding="utf-8")
    except OSError:
        return None
    return path


def _release_lock(path, result):
    if not path:
        return
    try:
        path.write_text(json.dumps({"state": "done", "ts": time.time(),
                                    "pid": os.getpid(), "result": result},
                                   ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _write_state(out_dir, payload):
    path = out_dir / STATE_NAME
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _read_budget(out_dir):
    path = out_dir / "budget_status.json"
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def build_audit_argv(args):
    """把 refresh 的参数翻译成 audit.py 的命令行参数。"""
    argv = ["--format", args.format, "--out", str(args.out_dir), "--yes"]
    if args.root:
        argv += ["--root", args.root]
    if args.days:
        argv += ["--days", str(args.days)]
    if args.last:
        argv += ["--last", args.last]
    if args.project:
        argv += ["--project", args.project]
    if args.task_type:
        argv += ["--task-type", args.task_type]
    if args.level:
        argv += ["--level", args.level]
    if args.balance is not None:
        argv += ["--balance", str(args.balance)]
    if args.threshold:
        argv += ["--threshold", args.threshold]
    if args.balance_state:
        argv += ["--balance-state", args.balance_state]
    if args.open:
        argv += ["--open"]
    if args.quiet:
        argv += ["--quiet"]
    return argv


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="refresh_report.py",
        description="定时重跑采集：刷新本机消耗审计报告（只读、离线）")
    ap.add_argument("--out", default="out", help="产物目录（默认 out）")
    ap.add_argument("--format", default="all", help="html / json / csv / all")
    ap.add_argument("--root", help="数据根目录（默认自动发现）")
    ap.add_argument("--days", type=int, default=0, help="统计窗口天数；0 = 全部历史")
    ap.add_argument("--last", help="相对区间，如 7d / 30d")
    ap.add_argument("--project", help="按项目过滤")
    ap.add_argument("--task-type", help="按任务类型过滤")
    ap.add_argument("--level", help="按复杂度过滤 L1—L4")
    ap.add_argument("--balance", type=float, help="临时当前余额（不写状态文件）")
    ap.add_argument("--threshold", help="告警阈值：warn=10,crit=20,red=30")
    ap.add_argument("--balance-state", help="余额状态文件路径")
    ap.add_argument("--log", help="运行日志路径（默认 <out>/refresh.log）")
    ap.add_argument("--lock-timeout", type=int, default=3600,
                    help="上一次运行超过多少秒视为失效（默认 3600）")
    ap.add_argument("--open", action="store_true", help="生成后打开报告")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir = out_dir

    log_path = Path(args.log).expanduser() if args.log else (out_dir / "refresh.log")
    if not log_path.is_absolute():
        log_path = (ROOT / log_path).resolve()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = io.open(log_path, "a", encoding="utf-8")
    except OSError:
        handle = io.open(os.devnull, "w", encoding="utf-8")

    lock = _acquire_lock(out_dir, args.lock_timeout)
    if lock is None:
        handle.write("[%s] 上一轮采集仍在运行，本次跳过\n" % _now())
        handle.flush()
        handle.close()
        print("上一轮采集仍在运行，本次跳过。")
        return 0

    started = time.time()
    handle.write("\n===== [%s] refresh start =====\n" % _now())
    handle.flush()

    old_stdout = sys.stdout
    sys.stdout = Tee(old_stdout, handle)
    code = 1
    try:
        code = audit.main(build_audit_argv(args))
    except Exception as e:                                    # noqa: BLE001
        print("采集失败：%s" % e)
        code = 1
    finally:
        sys.stdout = old_stdout
        try:
            handle.flush()
            handle.close()
        except Exception:                                     # noqa: BLE001
            pass

    budget = _read_budget(out_dir)
    state = {
        "ts": _now(),
        "exitCode": code,
        "durationSec": round(time.time() - started, 1),
        "level": budget.get("level"),
        "levelText": budget.get("levelText"),
        "balance": budget.get("balance"),
        "todaySpent": budget.get("todaySpent"),
        "out": str(out_dir),
        "log": str(log_path),
    }
    _write_state(out_dir, state)
    _release_lock(lock, code)

    print("[refresh] %s rc=%s 告警=%s 余额=%s 今日消耗=%s 耗时=%ss"
          % (state["ts"], code, state["levelText"], state["balance"],
             state["todaySpent"], state["durationSec"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
