#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余额状态存储 —— 唯一真相源的**持久化实现**。

为什么余额只能人工填
--------------------
余额只存在于客户端界面里，本地没有任何可信副本：``workbuddy.db`` 只有
``session_usage``，Chromium 缓存与 ``local_storage`` 里都没有余额字段。
自动取数要么依赖客户端开调试端口（要改启动方式、随版本变化易碎），
要么依赖截图 OCR（要装 tesseract，读数不确定）。所以本应用彻底取消自动
抓取，改为人工填两个数，其余全部实时算。

分层位置
--------
本文件是 Adapter：把「一个 JSON 文件」适配成 Core 认识的状态 dict。
判定逻辑（阈值 / 级别 / 凭证状态）在 ``core/policy.py``，这里只负责
读、写、以及把两者粘起来。
"""

import io
import json
import os
import tempfile
from pathlib import Path

from core import policy

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "balance-state.json"

# 供旧调用点继续以 `bs.DEFAULT_THRESHOLDS` 之类的名字引用
DEFAULT_THRESHOLDS = policy.DEFAULT_THRESHOLDS
LEVEL_TEXT = policy.LEVEL_TEXT
SNAPSHOT_STATE_TEXT = policy.SNAPSHOT_STATE_TEXT
default_state = policy.default_state
thresholds_of = policy.thresholds_of
level_of = policy.level_of
snapshot_state = policy.snapshot_state


def default_path():
    return os.environ.get("WBC_BALANCE_STATE") or str(DEFAULT_PATH)


def load(path=None):
    """读状态；文件缺失或损坏时返回默认值（不抛异常，报告仍可生成）。"""
    p = os.path.expanduser(path or default_path())
    st = default_state()
    if not os.path.exists(p):
        return st
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return st
    if isinstance(raw, dict):
        for k in st:
            if k in raw:
                st[k] = raw[k]
        th = dict(DEFAULT_THRESHOLDS)
        th.update({k: v for k, v in (raw.get("thresholds") or {}).items()
                   if k in DEFAULT_THRESHOLDS and v is not None})
        st["thresholds"] = th
    st["version"] = 3                       # 旧版状态文件读入后按新结构对待
    return st


def save(state, path=None):
    """原子写：临时文件 + os.replace，避免读到半截 JSON。"""
    p = os.path.expanduser(path or default_path())
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    state = dict(state)
    state["updated_at"] = policy.now()
    fd, tmp = tempfile.mkstemp(prefix=".balance-state-", suffix=".json", dir=d or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            # audit-static:ignore-begin
            os.remove(tmp)                  # 清理本次失败的临时文件，不碰用户数据
            # audit-static:ignore-end
        raise
    return state


def set_manual(initial=None, today_balance=None, thresholds=None,
               balance_date=None, initial_date=None, path=None,
               snapshot=None, initial_source=None):
    """人工设置：初始积分 / 今日余额 / 阈值。只写显式传入的字段。

    ``snapshot`` 给出时，把 today_balance 与凭证绑定（``--apply`` 走这条路径）。
    没给 snapshot 却改了 today_balance 时，旧快照会被标记为 stale——不能拿
    8 点那张图去背书 10 点改的数，否则绑定就成了摆设。
    """
    st = load(path)
    st = policy.apply_manual(st, initial=initial, today_balance=today_balance,
                             thresholds=thresholds, balance_date=balance_date,
                             initial_date=initial_date, snapshot=snapshot,
                             initial_source=initial_source)
    return save(st, path)
