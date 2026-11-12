#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""积分交易流水号（16 位）生成模块。

作用
----
WorkBuddy / CodeBuddy 每次执行一个新任务，本模块给它分配一个**全局唯一**的
16 位流水号，并登记「流水号 ↔ 任务 ID」的映射。全程只读本地日志 + 写一个
本地注册表文件，**不联网、不调用模型，因此不消耗任何积分**。

号码结构（16 位，纯 ASCII，可直接做订单号/对账键）
------------------------------------------------
    YYYYMMDD + nnnnnn + ss
    └─ 8 位 ─┘ └6 位─┘ └2┘
    交易日期   任务名   序列号

例：``20261027kraudt01``

* **8 位日期**：任务所属自然日（本地时区），取 ``occurred_at`` 的日期。
* **6 位任务名**：支出线取任务名里的英文字母，取小写前 6 位。
  中文名没有英文字母，或英文不足 6 位时，用「已有字母 + 任务名 sha256 确定性
  补齐」凑满 6 位——同一个名字永远得到同一段字母，跨机器、跨运行都一致。
  收入线不使用任务名，改用**固定缩写池**（见 INCOME_ABBR），一眼看得出是哪种收入。
* **2 位序列号**：同一「日期 + 6 位名」桶内从 01 递增到 99；
  超过 99 转成两位 36 进制（首位字母 + 次位字母数字，A0…ZZ），
  再溢出才换桶（变体哈希给出另一段 6 位名）——不回绕、不猜式生成。

唯一性怎么保证
--------------
1. 桶内序列号互斥 —— 注册表持久化 ``buckets``，进程崩溃也不会重号；
2. 单桶满 99 后自动换桶（变体哈希给出另一段 6 位名，序列号归 01），不会回绕；
3. 注册表把 ``txn → taskId`` 钉死，同一任务重跑**永远拿回同一个号**（幂等），
   因此对账可以跨天、跨次运行稳定匹配，不会因为重新采集而变号。

命令行
------
    python3 -m hosts.commands.txn next --name "补一个知识审核后台" --task-id tk_01a17ca3
    python3 -m hosts.commands.txn decode 20261027kraudt01
    python3 -m hosts.commands.txn verify            # 校验长度/唯一性/结构一致性
    python3 -m hosts.commands.txn stats             # 已分配数量、日期分布、尾号占用
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "txn-registry.json"

TXN_LEN = 16
DATE_LEN = 8
NAME_LEN = 6
SEQ_LEN = 2
SEQ_MAX = 10 ** SEQ_LEN - 1          # 99，十进制序列号上限
# 溢出后转两位 36 进制：首位 A—Z，次位 0—9A—Z，共 26 × 36 = 936 个槽位（A0…ZZ）
SEQ36_HEAD = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SEQ36_TAIL = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SEQ36_MAX = len(SEQ36_HEAD) * len(SEQ36_TAIL)
ASCII_LETTERS = "abcdefghijklmnopqrstuvwxyz"

TXN_RE = re.compile(r"^(\d{8})([a-z]{6})([0-9]{2}|[A-Z][0-9A-Z])$")

# 收入线固定缩写池（v2.1 §3.3）：收入没有任务名，用固定 6 位缩写区分类型
INCOME_ABBR = {
    "monthly": "mnthly",    # 月度套餐发放
    "topup":   "topupx",    # 加量包 / 临时购买
    "promo":   "promox",    # 活动赠送
    "reward":  "tskrwd",    # 完成任务领取
    "recharge":"rechrg",    # 充值
    "refund":  "refund",    # 退还
    "grant":   "promox",    # 兼容旧配置的「领取/赠送」
}
INCOME_ABBR_CN = {
    "mnthly": "月度套餐发放", "topupx": "加量包 / 临时购买", "promox": "活动赠送",
    "tskrwd": "完成任务领取", "rechrg": "充值", "refund": "退还",
}


def income6(kind):
    """收入类型 → 固定 6 位缩写；认不出来的类型一律用 promox，绝不用随机串。"""
    k = str(kind or "").strip().lower()
    if k in INCOME_ABBR:
        return INCOME_ABBR[k]
    return INCOME_ABBR["grant"]


# ---------------------------------------------------------------- 名字 → 6 位
def name6(name, variant=0):
    """把任务名压成 6 位小写英文。

    variant=0 是首选桶；桶满后由 allocate() 递增 variant 换桶，得到另一段
    确定性字母，从而在不回绕序列号的前提下继续保证唯一。
    """
    key = "" if name is None else str(name)
    # 只保留 ASCII 字母数字，再只取字母，避免中文/符号参与编码
    ascii_only = re.sub(r"[^A-Za-z0-9]+", "", key).lower()
    letters = re.sub(r"[^a-z]", "", ascii_only)

    if variant == 0 and len(letters) >= NAME_LEN:
        return letters[:NAME_LEN]

    seed_src = key if not variant else "%s|v%d" % (key, variant)
    digest = hashlib.sha256(seed_src.encode("utf-8")).hexdigest()
    # 每个 hex 位 → a..z，得到 64 个确定性小写字母，足够补齐任意长度
    fill = "".join(ASCII_LETTERS[int(c, 16) % 26] for c in digest)
    base = letters if variant == 0 else letters[:3]
    return (base + fill)[:NAME_LEN]


def seq_code(seq):
    """桶内计数 → 2 位后缀：1—99 用十进制，100 起用两位 36 进制（A0…ZZ）。"""
    seq = int(seq)
    if seq <= SEQ_MAX:
        return "%02d" % seq
    k = seq - SEQ_MAX - 1                      # 100 → 0
    if k >= SEQ36_MAX:
        raise RuntimeError("序列号超出两位 36 进制容量（%d），需换桶" % seq)
    return SEQ36_HEAD[k // len(SEQ36_TAIL)] + SEQ36_TAIL[k % len(SEQ36_TAIL)]


def seq_index(code):
    """反解：后缀 → 桶内计数（十进制段原样返回，36 进制段换算成 100 起的序号）。"""
    code = str(code or "").upper()
    if len(code) == 2 and code.isdigit():
        return int(code)
    if len(code) == 2 and code[0] in SEQ36_HEAD and code[1] in SEQ36_TAIL:
        return SEQ_MAX + 1 + SEQ36_HEAD.index(code[0]) * len(SEQ36_TAIL) \
               + SEQ36_TAIL.index(code[1])
    return None


def build_txn(date8, n6, seq):
    return "%s%s%s" % (date8, n6, seq_code(seq))


def date8_of(value):
    """从 '2026-10-27' / '2026-10-27 17:16:46.049' / datetime 里取 8 位日期。"""
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y%m%d")
    digits = re.sub(r"\D", "", str(value))
    return digits[:DATE_LEN]


def decode(txn):
    """拆解流水号；格式不符返回 None。"""
    m = TXN_RE.match(str(txn or "").strip())
    if not m:
        return None
    d8, n6, ss = m.groups()
    try:
        day = dt.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8])).isoformat()
    except ValueError:
        day = None
    return {"txn": m.group(0), "date": day, "date8": d8, "name6": n6,
            "seq": ss, "seqIndex": seq_index(ss),
            "incomeKind": INCOME_ABBR_CN.get(n6)}


# ---------------------------------------------------------------- 注册表
def default_path():
    return os.environ.get("WBC_TXN_REGISTRY") or str(DEFAULT_PATH)


def _now():
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Registry(object):
    """流水号注册表：唯一真相源，原子写盘，读坏不抛异常。"""

    def __init__(self, path=None, keep=20000):
        self.path = os.path.expanduser(path or default_path())
        self.keep = keep
        self.data = {"version": 1, "updatedAt": None, "buckets": {}, "txns": {}}
        self._by_task = {}
        self.dirty = False

    # -- 读写 ------------------------------------------------------------
    def load(self):
        if not os.path.exists(self.path):
            return self
        try:
            with io.open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return self
        if isinstance(raw, dict):
            self.data["buckets"] = dict(raw.get("buckets") or {})
            self.data["txns"] = dict(raw.get("txns") or {})
            self.data["version"] = raw.get("version") or 1
            self.data["updatedAt"] = raw.get("updatedAt")
        self._reindex()
        return self

    def _reindex(self):
        self._by_task = {}
        for txn, meta in self.data["txns"].items():
            tid = (meta or {}).get("taskId")
            if tid:
                self._by_task[str(tid)] = txn

    def save(self):
        if not self.dirty:
            return self.path
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.data["updatedAt"] = _now()
        self._prune()
        fd, tmp = tempfile.mkstemp(prefix=".txn-registry-", suffix=".json", dir=d or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)          # 原子替换，避免读到半截 JSON
        except BaseException:
            raise
        self.dirty = False
        return self.path

    def _prune(self):
        """注册表过大时按日期裁掉最老的一批，防止无限增长。"""
        txns = self.data["txns"]
        if len(txns) <= self.keep:
            return
        ordered = sorted(txns.items(), key=lambda kv: (kv[1].get("date") or "", kv[0]))
        drop = len(txns) - self.keep
        for txn, _meta in ordered[:drop]:
            txns.pop(txn, None)
        self._reindex()

    # -- 查询 ------------------------------------------------------------
    def txn_of(self, task_id):
        return self._by_task.get(str(task_id or ""))

    def meta(self, txn):
        return self.data["txns"].get(txn)

    def __len__(self):
        return len(self.data["txns"])

    # -- 分配 ------------------------------------------------------------
    def allocate(self, date, name, task_id, ts=None, note="", income_pool=None):
        """给一个任务分配流水号。同一 task_id 已分配过则原样返回（幂等）。

        income_pool 传了 6 位缩写时，走收入线固定缩写池（不按任务名取字母）。
        """
        tid = str(task_id or "")
        if tid:
            got = self._by_task.get(tid)
            if got:
                return got

        d8 = date8_of(date)
        if len(d8) != DATE_LEN:
            raise ValueError("无法从 %r 解析出 8 位日期" % (date,))
        if not tid:
            # 没有任务 ID 时用「日期+名字+时间戳」做去重键，保证同一次运行内不重号
            tid = "anon:%s:%s" % (d8, ts or _now())

        variant = 0
        while variant <= 64:
            n6 = income_pool or name6(name, variant)
            bucket = "%s:%s" % (d8, n6)
            seq = int(self.data["buckets"].get(bucket) or 0)
            if seq < SEQ_MAX + SEQ36_MAX:
                seq += 1
                try:
                    txn = build_txn(d8, n6, seq)
                except RuntimeError:
                    variant += 1
                    continue
                self.data["buckets"][bucket] = seq
                if txn in self.data["txns"]:        # 极端情况下的兜底，绝不覆盖
                    continue
                self.data["txns"][txn] = {
                    "taskId": tid,
                    "name": "" if name is None else str(name),
                    "date": dt.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8])).isoformat(),
                    "seq": seq,
                    "variant": variant,
                    "at": _now(),
                }
                if not tid.startswith("anon:"):
                    self._by_task[tid] = txn
                self.dirty = True
                return txn
            variant += 1                            # 桶彻底满：换桶，序列号重新从 01
        raise RuntimeError("任务 %s 在 %s 单日已无可用流水号" % (tid, d8))

    def allocate_income(self, date, kind, biz_no="", idx=0, note=""):
        """收入线流水号：固定缩写池 + 幂等键（业务单号优先，其次 日期:类型:序号）。"""
        key = str(biz_no or "").strip()
        if not key:
            key = "income:%s:%s:%d" % (date8_of(date), str(kind or "").lower(), int(idx or 0))
        return self.allocate(date, None, "biz:" + key, note=note,
                             income_pool=income6(kind))

    def assign_tasks(self, tasks, date_key="day", ts_key="ts", name_key="name",
                     id_key="id", out_key="txn"):
        """给任务列表批量挂上 txn 字段，返回 {taskId: txn}。"""
        ordered = sorted(tasks or [],
                         key=lambda t: str(t.get(ts_key) or t.get(date_key) or ""))
        assigned = {}
        for t in ordered:
            txn = self.allocate(t.get(date_key) or t.get(ts_key),
                                t.get(name_key), t.get(id_key), ts=t.get(ts_key))
            t[out_key] = txn
            if t.get(id_key):
                assigned[str(t.get(id_key))] = txn
        return assigned


# ---------------------------------------------------------------- 校验
def verify(reg, tasks=None):
    """结构校验：长度、字符集、唯一性、序号不重复、映射完整。"""
    problems = []
    seen = {}
    buckets = {}
    for txn, meta in sorted(reg.data["txns"].items()):
        info = decode(txn)
        if not info:
            problems.append({"txn": txn, "kind": "format",
                             "detail": "不是 %d 位『8 位日期 + 6 位小写字母 + 2 位数字』" % TXN_LEN})
            continue
        if len(txn) != TXN_LEN:
            problems.append({"txn": txn, "kind": "length", "detail": "长度 %d" % len(txn)})
        if txn in seen:
            problems.append({"txn": txn, "kind": "duplicate", "detail": "流水号重复"})
        seen[txn] = 1
        key = info["date8"] + ":" + info["name6"]
        if info["seq"] in buckets.setdefault(key, {}):
            problems.append({"txn": txn, "kind": "seq_clash",
                             "detail": "同一桶内序号 %d 被复用" % info["seq"]})
        buckets[key][info["seq"]] = txn
        if not (meta or {}).get("taskId"):
            problems.append({"txn": txn, "kind": "no_task", "detail": "未绑定任务 ID"})
        if meta.get("date") and info["date"] and meta["date"] != info["date"]:
            problems.append({"txn": txn, "kind": "date_mismatch",
                             "detail": "%s ≠ %s" % (meta["date"], info["date"])})

    if tasks is not None:
        missing = [t.get("id") for t in tasks if not t.get("txn")]
        if missing:
            problems.append({"txn": "", "kind": "unassigned",
                             "detail": "%d 个任务没有流水号，例如 %s"
                                       % (len(missing), ", ".join(map(str, missing[:5])))})
    return problems


def stats(reg):
    txns = reg.data["txns"]
    by_date = {}
    for txn, meta in txns.items():
        d = (decode(txn) or {}).get("date") or (meta or {}).get("date") or "?"
        by_date[d] = by_date.get(d, 0) + 1
    dates = sorted(by_date)
    return {
        "registry": reg.path,
        "total": len(txns),
        "buckets": len(reg.data["buckets"]),
        "dates": len(by_date),
        "span": [dates[0], dates[-1]] if dates else [],
        "perDate": [{"date": d, "txns": by_date[d]} for d in dates[-15:]],
        "updatedAt": reg.data.get("updatedAt"),
    }


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="txn.py", description="积分交易流水号（16 位）生成与校验")
    ap.add_argument("--registry", help="注册表文件（默认 config/txn-registry.json）")
    sub = ap.add_subparsers(dest="cmd")

    p_next = sub.add_parser("next", help="为一个任务分配/取回流水号")
    p_next.add_argument("--name", required=True, help="任务名称")
    p_next.add_argument("--task-id", required=True, help="任务 ID（来自工具运行日志）")
    p_next.add_argument("--date", help="任务日期 YYYY-MM-DD（默认今天）")
    p_next.add_argument("--ts", help="任务开始时间，用于排序参考")

    p_dec = sub.add_parser("decode", help="拆解一个流水号")
    p_dec.add_argument("txn")

    sub.add_parser("verify", help="校验注册表（长度/唯一性/序号/映射）")
    sub.add_parser("stats", help="统计已分配情况")

    args = ap.parse_args(argv)
    reg = Registry(args.registry).load()

    if args.cmd == "next":
        date = args.date or dt.date.today().isoformat()
        txn = reg.allocate(date, args.name, args.task_id, ts=args.ts)
        reg.save()
        print(txn)
        return 0

    if args.cmd == "decode":
        info = decode(args.txn)
        if not info:
            print("格式不符：%r 应为 8 位日期 + 6 位小写字母 + 2 位数字" % (args.txn,))
            return 1
        meta = reg.meta(info["txn"]) or {}
        info.update({"taskId": meta.get("taskId"), "name": meta.get("name")})
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "verify":
        problems = verify(reg)
        if not problems:
            print("校验通过：%d 个流水号，格式与唯一性均正常（%s）" % (len(reg), reg.path))
            return 0
        print("发现 %d 个问题：" % len(problems))
        for p in problems[:40]:
            print("  [%s] %s %s" % (p["kind"], p["txn"] or "-", p["detail"]))
        return 1

    if args.cmd == "stats":
        print(json.dumps(stats(reg), ensure_ascii=False, indent=1))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
