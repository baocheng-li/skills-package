#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余额凭证快照：把「人工填的余额」和「当时那张截图」绑死在一起。

为什么需要这个
--------------
``balance-state.json`` 里那两个数（初始积分、今日余额）是**人工敲进去的**，
以前没有任何东西证明「敲的时候屏幕上真写着这个数」。对账体系里最尴尬的场景
不是算错，而是**算得对但基数来源不明**——偏差算出来 0，可谁也不知道那个
0 是建在什么之上的。

v2.1 §3.8 B4 已经写了「人工余额也需截图留痕」，本脚本把这条规则做成可执行的：

    一张截图 → 复制进 config/vouchers/ → SHA256 进台账 → 数字与凭证绑定

三道约束
--------
1. **必须有凭证**：``add`` 不带 ``--shot`` 直接拒绝（退出码 2）。
   没有图的余额数字不进台账，也就不该进状态文件。
2. **哈希可复算**：``verify`` 逐个重算凭证 SHA256，对不上报 ``SHOT_TAMPERED``，
   文件丢了报 ``SHOT_MISSING``。
3. **留痕不可改**：``config/balance-snapshots.jsonl`` 追加写，每条带
   ``prev_hash`` 串成哈希链；``verify`` 发现断链报 ``CHAIN_BROKEN``。

快照时刻 ≠ 日初余额
-------------------
截图上只有「此刻余额」，而日终对账要的是「当日日初余额」。两者差一个
「00:00 到截图时刻之间已经消耗掉的部分」。所以台账里分开记：

    account       截图显示的余额（实测，不可改）
    taken_at      截图时刻
    day_open      当日日初余额 = account + 00:00~taken_at 的消耗
    spent_before  上面那个差值，显式记下来，不让它无声地混进偏差

``day_open`` 由调用方给（``--day-open``）；不给就退化为 ``account``，
并在记录里标 ``imputed=false``，报告会以 ``B_SNAPSHOT（快照时点漂移）``
把它作为可解释偏差列出来——宁可显式承认这点漂移，也不要假装截图就发生在 00:00。

命令行
------
    # 录入（必须带图）
    python3 -m hosts.commands.balance_snapshot add --account 1419.51 \\
        --shot ~/Desktop/个人中心.png --taken-at "2026-10-29 10:01:15" \\
        --plan 体验版 --by 张三

    # 连同状态文件一起更新（日初余额已知时）
    python3 -m hosts.commands.balance_snapshot add --account 1419.51 --day-open 1480.0 \\
        --shot ~/Desktop/个人中心.png --taken-at "2026-10-29 10:01:15" --apply

    python3 -m hosts.commands.balance_snapshot verify    # 复算哈希 + 校验链
    python3 -m hosts.commands.balance_snapshot list      # 看历史快照
    python3 -m hosts.commands.balance_snapshot latest --json
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CFG_DIR = ROOT / "config"
DEFAULT_LOG = CFG_DIR / "balance-snapshots.jsonl"
DEFAULT_VAULT = CFG_DIR / "vouchers"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ISSUE = 5          # verify 发现问题（与 income_entry.py 保持一致）


# ------------------------------------------------------------------ 基础
def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def operator_default():
    for k in ("WBC_OPERATOR", "USERNAME", "USER", "LOGNAME"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return "unknown"


def host_tag():
    try:
        return socket.gethostname() or "localhost"
    except OSError:
        return "localhost"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def die(msg, code=EXIT_USAGE):
    sys.stderr.write(msg.rstrip() + "\n")
    return code


# ------------------------------------------------------------------ 日志链
def _iter_log(log_path):
    p = Path(log_path)
    if not p.exists():
        return []
    out = []
    with io.open(p, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                rec = {"_broken": True, "_line": i, "_raw": line[:200]}
            out.append(rec)
    return out


def _canon(rec):
    """把记录里除 hash 本身之外的字段按固定顺序序列化，用于算 hash。"""
    body = {k: v for k, v in rec.items() if k not in ("hash",)}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _last_hash(records):
    for rec in reversed(records):
        if rec.get("hash"):
            return rec["hash"]
    return ""


def append_log(log_path, rec):
    """追加一条；prev_hash 串上一条的 hash。"""
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    records = _iter_log(p)
    rec = dict(rec)
    rec["prev_hash"] = _last_hash(records)
    rec["hash"] = sha256_text(_canon(rec))
    with io.open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


# ------------------------------------------------------------------ 凭证
def store_shot(src, vault=None, taken_at=None):
    """把凭证复制进 vouchers/，文件名带日期与哈希前缀（不可覆盖）。"""
    src_p = Path(os.path.expanduser(str(src)))
    if not src_p.exists():
        raise RuntimeError("凭证文件不存在：%s" % src_p)
    if not src_p.is_file():
        raise RuntimeError("凭证必须是文件：%s" % src_p)
    raw = src_p.read_bytes()
    if not raw:
        raise RuntimeError("凭证文件是空的：%s" % src_p)
    h = hashlib.sha256(raw).hexdigest()
    vault_p = Path(vault or DEFAULT_VAULT)
    vault_p.mkdir(parents=True, exist_ok=True)
    stamp = (str(taken_at or now_iso())[:19]
             .replace("-", "").replace(":", "").replace(" ", "-"))
    name = "balance-%s-%s%s" % (stamp, h[:8], src_p.suffix or ".png")
    dst = vault_p / name
    if dst.exists():
        # 同名同哈希：同一张图重复录入，直接复用，不产生第二份副本
        if sha256_file(dst) != h:
            raise RuntimeError("同名凭证已存在但内容不同：%s" % dst)
    else:
        shutil.copyfile(src_p, dst)
    return {
        "file": _rel(dst),
        "sha256": h,
        "bytes": len(raw),
        "ext": (src_p.suffix or ".png").lower(),
        "copied_at": now_iso(),
    }


def _rel(p):
    """凭证路径尽量写成相对 config/ 的形式，便于整体迁移。

    注意：相对形式只在「凭证确实位于 config/ 之下」时才安全。若凭证被放到
    别处（自定义 --vault），必须回落成绝对路径——否则 resolve_shot() 会拿
    config/ 去拼一个不存在的路径，把所有记录误报成 SHOT_MISSING。
    """
    p = Path(p).resolve()
    try:
        return str(p.relative_to(CFG_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return str(p)


def resolve_shot(ref):
    """把台账里的相对路径还原成实际路径。"""
    if not ref:
        return None
    p = Path(str(ref))
    if p.is_absolute():
        return p
    return CFG_DIR / p


def check_shot(ref, expect_sha=None):
    """返回 ok / missing / tampered / noshot。"""
    if not ref:
        return "noshot"
    p = resolve_shot(ref)
    if p is None or not p.exists():
        return "missing"
    if expect_sha:
        try:
            return "ok" if sha256_file(p) == expect_sha else "tampered"
        except OSError:
            return "missing"
    return "ok"


# ------------------------------------------------------------------ 命令
def cmd_add(args, log_path, vault):
    if not args.shot:
        return die("缺少凭证：--shot 必填。\n"
                   "  余额数字必须有截图佐证（v2.1 §3.8 B4：人工余额也需截图留痕）。\n"
                   "  没有图的余额不进台账，也不该写进状态文件。")
    try:
        account = float(args.account)
    except (TypeError, ValueError):
        return die("--account 必须是数字")
    if account < 0:
        return die("--account 不能为负")

    taken_at = str(args.taken_at or now_iso())[:19]
    day_open = None
    if args.day_open is not None:
        try:
            day_open = float(args.day_open)
        except (TypeError, ValueError):
            return die("--day-open 必须是数字")
    imputed = day_open is not None
    if not imputed:
        day_open = account
    spent_before = round(day_open - account, 4)
    if imputed and spent_before < -1e-9:
        return die("--day-open 不能小于截图余额（当日日初不可能比此刻更低）")

    try:
        shot = store_shot(args.shot, vault=vault, taken_at=taken_at)
    except RuntimeError as e:
        return die(str(e))

    rec = {
        "ts": now_iso(),
        "taken_at": taken_at,
        "date": taken_at[:10],
        "account": round(account, 4),
        "day_open": round(day_open, 4),
        "spent_before": spent_before,
        "imputed": imputed,
        "plan": str(args.plan or ""),
        "note": str(args.note or ""),
        "shot": shot,
        "by": args.by or operator_default(),
        "host": host_tag(),
    }
    rec = append_log(log_path, rec)

    # 与状态文件绑定：把余额数字和这条快照的 hash 一起写进去，
    # 事后可验证「这个数是从哪张图来的」。
    applied = False
    if args.apply:
        sys.path.insert(0, str(ROOT / "hosts" / "commands"))
        import balance_state as bsmod  # noqa: E402
        bsmod.set_manual(
            today_balance=round(day_open, 4),
            balance_date=rec["date"],
            snapshot={
                "ref": rec["hash"],
                "file": shot["file"],
                "sha256": shot["sha256"],
                "taken_at": taken_at,
                "account": round(account, 4),
                "day_open": round(day_open, 4),
            },
            path=args.balance_state)
        applied = True

    if getattr(args, "json", False):
        print(json.dumps({"record": rec, "applied": applied},
                         ensure_ascii=False, indent=2))
        return EXIT_OK

    print("已录入余额快照")
    print("  截图余额    %s 积分（%s）" % (_num(account), rec["plan"] or "未标注套餐"))
    print("  截图时刻    %s" % taken_at)
    if imputed:
        print("  当日日初    %s 积分（= 截图余额 + 时刻前已消耗 %s）"
              % (_num(day_open), _num(spent_before)))
    else:
        print("  当日日初    未提供（按截图余额近似，将以 B_SNAPSHOT 快照时点漂移记录）")
    print("  凭证         %s" % shot["file"])
    print("  凭证 SHA256  %s" % shot["sha256"])
    print("  留痕 hash    %s" % rec["hash"][:16])
    if applied:
        print("  已写入余额状态文件：today_balance=%s @ %s"
              % (_num(day_open), rec["date"]))
    else:
        print("  （未写状态文件；加 --apply 可一并更新 today_balance）")
    return EXIT_OK


def cmd_verify(args, log_path, vault=None):
    records = _iter_log(log_path)
    issues, rows = [], []
    prev = ""
    for i, rec in enumerate(records, 1):
        if rec.get("_broken"):
            issues.append({"kind": "LOG_TAMPERED", "line": i,
                           "detail": "第 %d 行不是合法 JSON" % i})
            prev = ""
            continue
        if rec.get("prev_hash") != prev:
            issues.append({"kind": "CHAIN_BROKEN", "line": i,
                           "detail": "第 %d 行 prev_hash 断链" % i})
        if rec.get("hash") != sha256_text(_canon(rec)):
            issues.append({"kind": "LOG_TAMPERED", "line": i,
                           "detail": "第 %d 行内容与 hash 不符" % i})
        prev = rec.get("hash") or ""

        shot = rec.get("shot") or {}
        state = check_shot(shot.get("file"), shot.get("sha256"))
        if state == "missing":
            issues.append({"kind": "SHOT_MISSING", "line": i,
                           "detail": "凭证文件不在：%s" % shot.get("file")})
        elif state == "tampered":
            issues.append({"kind": "SHOT_TAMPERED", "line": i,
                           "detail": "凭证哈希与台账不符：%s" % shot.get("file")})
        elif state == "noshot":
            issues.append({"kind": "NO_SHOT", "line": i,
                           "detail": "第 %d 条快照没有凭证" % i})
        rows.append({"ts": rec.get("ts"), "taken_at": rec.get("taken_at"),
                     "account": rec.get("account"), "day_open": rec.get("day_open"),
                     "plan": rec.get("plan"), "shot": state,
                     "file": shot.get("file")})

    result = {"records": len(records), "ok": not issues,
              "issues": issues, "rows": rows}
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("余额快照台账：%d 条" % len(records))
        for r in rows:
            flag = {"ok": "✓", "missing": "✗ 文件缺失",
                    "tampered": "✗ 哈希不符", "noshot": "✗ 无凭证"}.get(r["shot"], "?")
            print("  %s  %s  %-12s  %s  %s"
                  % (r["taken_at"], _num(r["account"]), r["plan"] or "-", flag,
                     r["file"] or ""))
        if issues:
            print("\n异常 %d 项：" % len(issues))
            for it in issues:
                print("  [%s] %s" % (it["kind"], it["detail"]))
        else:
            print("\n全部通过：凭证可复算、留痕链完整。")
    return EXIT_OK if not issues else EXIT_ISSUE


def cmd_list(args, log_path, vault=None):
    records = _iter_log(log_path)
    rows = []
    for rec in records:
        shot = rec.get("shot") or {}
        rows.append({
            "taken_at": rec.get("taken_at"), "account": rec.get("account"),
            "day_open": rec.get("day_open"), "imputed": rec.get("imputed"),
            "plan": rec.get("plan"), "by": rec.get("by"),
            "shot": check_shot(shot.get("file"), shot.get("sha256")),
            "file": shot.get("file"), "sha256": shot.get("sha256"),
            "hash": rec.get("hash"),
        })
    if getattr(args, "json", False):
        print(json.dumps({"snapshots": rows}, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        print("余额快照台账为空。录入：python3 -m hosts.commands.balance_snapshot add "
              "--account 1419.51 --shot 截图.png")
        return EXIT_OK
    print("%-20s %12s %12s  %-10s %-9s %s"
          % ("截图时刻", "截图余额", "当日日初", "套餐", "凭证", "操作人"))
    for r in rows:
        print("%-20s %12s %12s  %-10s %-9s %s"
              % (r["taken_at"], _num(r["account"]), _num(r["day_open"]),
                 (r["plan"] or "-")[:10], r["shot"], r["by"] or "-"))
    return EXIT_OK


def cmd_latest(args, log_path, vault=None):
    records = [r for r in _iter_log(log_path) if not r.get("_broken")]
    if not records:
        if getattr(args, "json", False):
            print(json.dumps({"snapshot": None}, ensure_ascii=False))
        else:
            print("没有余额快照记录。")
        return EXIT_OK
    rec = records[-1]
    shot = rec.get("shot") or {}
    out = dict(rec)
    out["shotState"] = check_shot(shot.get("file"), shot.get("sha256"))
    if getattr(args, "json", False):
        print(json.dumps({"snapshot": out}, ensure_ascii=False, indent=2))
    else:
        print("最新余额快照：%s  %s 积分（%s）"
              % (rec.get("taken_at"), _num(rec.get("account")),
                 rec.get("plan") or "未标注"))
    return EXIT_OK


def _num(v):
    try:
        return "{:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return "—"


# ------------------------------------------------------------------ 入口
def build_parser():
    ap = argparse.ArgumentParser(
        prog="balance_snapshot.py",
        description="余额凭证快照：人工填的余额必须有截图 + SHA256 佐证")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="快照台账路径（append-only）")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT), help="凭证存放目录")
    ap.add_argument("--balance-state", default=None, help="余额状态文件（--apply 时写）")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="以 JSON 输出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="录入一条余额快照（必须带凭证）", parents=[common])
    p.add_argument("--account", required=True, help="截图显示的余额")
    p.add_argument("--shot", help="凭证截图路径（png/jpg/pdf）——必填，由脚本自己校验以便给友好提示")
    p.add_argument("--taken-at", help="截图时刻 YYYY-MM-DD HH:MM:SS（默认现在）")
    p.add_argument("--day-open", type=float,
                   help="当日日初余额 = 截图余额 + 时刻前已消耗；不给则按截图余额近似")
    p.add_argument("--plan", help="套餐名（如 体验版 / 专业版）")
    p.add_argument("--note", help="备注")
    p.add_argument("--by", help="操作人（默认取系统用户名）")
    p.add_argument("--apply", action="store_true",
                   help="同时写入余额状态文件的 today_balance")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("verify", help="复算凭证哈希 + 校验留痕链", parents=[common])
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("list", help="列出全部快照", parents=[common])
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("latest", help="查看最新一条", parents=[common])
    p.set_defaults(func=cmd_latest)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args, args.log, args.vault)


if __name__ == "__main__":
    sys.exit(main())
