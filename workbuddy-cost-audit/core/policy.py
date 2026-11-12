#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警策略 —— 阈值、级别、凭证状态的**纯语义**。

为什么从 balance_state 里拆出来
------------------------------
原来的 ``balance_state.py`` 混了两件事：政策的判定（多高的预警比算红）和
政策的存放（读哪个 JSON）。前者是领域知识，后者是平台的存储细节。
混在一起的直接后果是 ``budget.py`` 为了算一个告警级别，不得不 import
一个会读文件的模块——于是「纯计算」再也无法在无 IO 的环境里跑（比如
Web 端拿一份上传的 JSON 渲染报告，或插件里做一次干跑）。

拆开之后：
    core/policy.py      只做判定，输入是 dict，输出是 dict/str
    adapters/state.py   只做存取，内部调用本模块

预警口径（不变）
----------------
    预警比 = 今日消耗 / 当前余额 × 100
    当前余额 = 今日余额 − 今日消耗

``initial_credits`` 只用于展示「本期已用掉多少」，不参与告警判定。
"""

import datetime as dt
import hashlib
import os
from pathlib import Path

# 阈值语义：今日消耗占当前余额的百分比，超过即升级
# 默认 10 / 20 / 30：一天吃掉剩余余额的 10% 起黄、20% 起橙、30% 起红
DEFAULT_THRESHOLDS = {"mode": "pct", "warn": 10.0, "crit": 20.0, "red": 30.0}
LEVEL_TEXT = {"red": "红色告警", "crit": "橙色告警", "warn": "黄色告警", "none": "正常"}
LEVEL_ORDER = {"none": 0, "warn": 1, "crit": 2, "red": 3}

SNAPSHOT_STATE_TEXT = {
    "ok": "凭证已校验",
    "stale": "凭证已失效",
    "missing": "凭证文件缺失",
    "tampered": "凭证哈希不符",
    "noshot": "无凭证",
}


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def default_state():
    """状态结构的唯一声明。

    ⚠ 新增字段必须同步加在这里：读取时按本函数的键做白名单过滤，
    漏加就等于「写进去、读不出来」，而且是静默的。
    """
    return {
        "version": 3,
        "initial_credits": None,
        "initial_date": None,       # 初始积分对应的日期；日终对账从这一天开始链式结转
        "initial_set_at": None,
        "initial_source": None,     # measured（有截图佐证）/ derived（反推）/ manual（裸填）
        "today_balance": None,
        "today_balance_date": None,
        "today_balance_set_at": None,
        # 余额凭证绑定：today_balance 这个数是从哪张截图来的。
        "today_balance_snapshot": None,
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "updated_at": None,
    }


def thresholds_of(state):
    """返回生效阈值（百分比），并做单调性修正：warn ≤ crit ≤ red。"""
    th = dict(DEFAULT_THRESHOLDS)
    th.update({k: v for k, v in ((state or {}).get("thresholds") or {}).items()
               if v is not None})
    try:
        warn, crit, red = float(th["warn"]), float(th["crit"]), float(th["red"])
    except (KeyError, TypeError, ValueError):
        warn, crit, red = 10.0, 20.0, 30.0
    warn, crit, red = max(0.0, warn), max(0.0, crit), max(0.0, red)
    crit = max(crit, warn)
    red = max(red, crit)
    return {"mode": "pct", "warn": warn, "crit": crit, "red": red}


def level_of(spend_ratio, thresholds=None):
    """按「今日消耗 / 当前余额」判定级别。判不出来时返回 (None, 原因)。

    ``spend_ratio`` 为 None 表示判不出来（未配置或余额为 0）；
    为 ``float('inf')`` 表示今日消耗已超过当前余额本身。
    """
    if spend_ratio is None:
        return None, "未配置初始积分或今日余额，无法计算预警比"
    th = thresholds or DEFAULT_THRESHOLDS
    if spend_ratio == float("inf"):
        return "red", "今日消耗已超过当前余额，预警比 >100%"
    if spend_ratio >= th["red"]:
        return "red", "今日消耗占当前余额 %.1f%%，超过红色阈值 %.0f%%" % (
            spend_ratio, th["red"])
    if spend_ratio >= th["crit"]:
        return "crit", "今日消耗占当前余额 %.1f%%，超过橙色阈值 %.0f%%" % (
            spend_ratio, th["crit"])
    if spend_ratio >= th["warn"]:
        return "warn", "今日消耗占当前余额 %.1f%%，超过黄色阈值 %.0f%%" % (
            spend_ratio, th["warn"])
    return "none", "今日消耗占当前余额 %.1f%%，未超过黄色阈值 %.0f%%" % (
        spend_ratio, th["warn"])


def _default_resolver(ref):
    """凭证路径解析：相对路径按 <root>/config/ 解释。"""
    p = Path(os.path.expanduser(str(ref)))
    if not p.is_absolute():
        root = Path(__file__).resolve().parent.parent
        p = root / "config" / p
    return p


def snapshot_state(snap, resolver=None, digest=None):
    """今日余额那张凭证现在是什么状态。

    ``ok``        文件在、哈希对得上 —— 这个余额数字有据可查
    ``stale``     余额被手改过，原快照不再对应当前数值
    ``missing``   凭证文件被删/被移走
    ``tampered``  文件内容变了，哈希不符
    ``noshot``    根本没绑凭证（历史数据或人为绕过）

    ``resolver`` / ``digest`` 可注入，便于在没有文件系统的宿主（Web）里
    用预置的哈希表做同一套判定。
    """
    if not isinstance(snap, dict) or not snap:
        return "noshot"
    if snap.get("stale"):
        return "stale"
    ref = str(snap.get("file") or "")
    expect = str(snap.get("sha256") or "")
    if not ref:
        return "noshot"
    p = (resolver or _default_resolver)(ref)
    if digest is not None:
        return "ok" if digest(p) == expect else "tampered"
    try:
        if not Path(p).exists():
            return "missing"
    except OSError:
        return "missing"
    if not expect:
        return "noshot"
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "missing"
    return "ok" if h.hexdigest() == expect else "tampered"


def load(path=None):
    """兼容入口：老代码 ``bs.load()`` 继续可用。

    存储实现在 adapters/state.py，这里只是惰性转发——放在函数体内 import，
    是为了让 core 在 import 期不依赖任何 adapter（否则「无 IO 环境」又跑不起来）。
    """
    from adapters import state as _store
    return _store.load(path)


def apply_manual(st, initial=None, today_balance=None, thresholds=None,
                 balance_date=None, initial_date=None, snapshot=None,
                 initial_source=None):
    """把一次人工设置合并进状态，返回新状态（不落盘）。

    拆分点：这里只管「合并后应该是什么」，写文件在 adapters/state.py。
    """
    st = dict(st or default_state())
    if initial is not None:
        st["initial_credits"] = float(initial)
        st["initial_set_at"] = now()
        if initial_date:
            st["initial_date"] = str(initial_date)[:10]
        st["initial_source"] = (initial_source
                                or st.get("initial_source") or "manual")
    if today_balance is not None:
        st["today_balance"] = float(today_balance)
        st["today_balance_date"] = balance_date or dt.date.today().isoformat()
        st["today_balance_set_at"] = now()
        if snapshot:
            st["today_balance_snapshot"] = dict(snapshot)
        else:
            old = st.get("today_balance_snapshot")
            if isinstance(old, dict) and not old.get("stale"):
                stale = dict(old)
                stale["stale"] = True
                stale["stale_reason"] = "余额已被手工改写，原快照不再对应当前数值"
                stale["stale_at"] = now()
                st["today_balance_snapshot"] = stale
    if thresholds:
        th = dict(st.get("thresholds") or DEFAULT_THRESHOLDS)
        for k, v in thresholds.items():
            if v is not None:
                th[k] = v
        if th.get("mode") not in ("pct",):
            th["mode"] = "pct"
        st["thresholds"] = th
    return st
