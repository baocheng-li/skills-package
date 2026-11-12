#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""积分收入单入口：单人上传凭证快照 + 录入记录留痕。

为什么改成单人
--------------
原 v2.1 §3.8 要求「复核人 ≠ 录入人」。落地时发现两个绕不过去的问题：

1. 本应用是**离线单机** Skill，没有多账号体系。所谓「第二个复核人」，
   实际往往是同一个人用另一个名字再点一次，形式上合规、实质上没有增量；
2. 双人复核只解决「有第二个人看过」，**不解决「事后能不能证明当时录的是什么」**。
   而审计真正需要的，是后者——凭证在不在、金额有没有被改过、谁在什么时候改的。

所以本版改为「单人录入 + 三件套」，用可验证的痕迹替代不可验证的点头：

* **快照**：凭证文件复制进 ``config/vouchers/``，SHA256 写进台账。
  任何事后改动都会在 ``verify`` 里以 ``EVIDENCE_TAMPERED`` 暴露；
* **留痕**：``CREATE / EDIT / SUBMIT / REOPEN / VOID`` 每一次动作追加一行 JSON，
  含 before / after、操作人、时间、快照哈希，并做**哈希链**
  （每条记 ``prev_hash``），中间被删一条或改一个字都能查出来；
* **事后抽核**（可选）：按比例抽样，结论引用 ``snapshot.sha256``，不阻断入账。

一句话：不是「两个人签字所以可信」，而是「每一步都留痕、每张凭证可复算所以可信」。

命令
----
    python3 -m hosts.commands.income_entry add --date 2026-10-29 --type recharge \\
        --amount 100 --biz-no RECHG-001 --by 张三 --snapshot ~/Desktop/凭证.png
    python3 -m hosts.commands.income_entry confirm INC-20261029-01 --by 张三
    python3 -m hosts.commands.income_entry edit INC-20261029-01 --by 张三 --amount 120
    python3 -m hosts.commands.income_entry void INC-20261029-01 --by 张三 --reason 录错单号
    python3 -m hosts.commands.income_entry verify            # 复算哈希、校验留痕链
    python3 -m hosts.commands.income_entry list --json

退出码：0 成功 / 2 参数或状态不允许 / 5 证据或留痕异常（verify）。
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import platform
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

DEFAULT_CFG = Path(os.environ.get("WBC_INCOME_CFG")
                   or (ROOT / "config" / "credit-income.json"))
DEFAULT_LOG = Path(os.environ.get("WBC_INCOME_LOG")
                   or (ROOT / "config" / "income-entry-log.jsonl"))
VOUCHER_DIRNAME = "vouchers"

TYPES = {"recharge": "充值/购买", "grant": "领取/赠送", "refund": "退还"}
STATUS_TEXT = {"draft": "草稿", "confirmed": "已入账", "void": "已退回"}
ACTIONS = ("CREATE", "EDIT", "SUBMIT", "REOPEN", "VOID")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EVIDENCE = 5


# ---------------------------------------------------------------- 基础工具

def now_iso():
    """带时区的本地时间（审计要能定位到人所在时区，所以不做 UTC 归一）。"""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def operator_default():
    return (os.environ.get("WBC_OPERATOR")
            or os.environ.get("USERNAME") or os.environ.get("USER") or "本机用户")


def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def die(msg, code=EXIT_USAGE):
    sys.stderr.write("[income] %s\n" % msg)
    return code


def host_tag():
    return "%s/%s" % (platform.node() or "unknown", platform.system() or "unknown")


# ---------------------------------------------------------------- 台账读写

def load_cfg(path):
    """读台账。缺文件返回空台账；结构不对时明确报错，不静默当成空表——
    收入台账是账本侧数据，静默清空比报错危险得多。"""
    if not Path(path).exists():
        return {"version": 3, "entries": []}
    try:
        with io.open(str(path), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise RuntimeError("收入台账无法解析：%s" % e)
    if isinstance(raw, list):
        raw = {"version": 3, "entries": raw}
    if not isinstance(raw, dict):
        raise RuntimeError("收入台账结构不是对象")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("收入台账缺少 entries 数组")
    raw["entries"] = [normalize_entry(e) for e in entries if isinstance(e, dict)]
    # v2 老数据没有录入ID：按业务日顺序补一个，补完立刻可被 confirm / edit 引用。
    # 只补空值，已有 ID 一律不动（ID 是留痕主键，改它等于断链）。
    for e in raw["entries"]:
        if not e.get("id") and str(e.get("date") or "")[:10]:
            e["id"] = next_id(raw["entries"], str(e["date"])[:10])
    raw.setdefault("version", 3)
    return raw


def normalize_entry(e):
    """把老结构（v2：reviewer + 裸 sha256）补成 v3 结构。

    老字段不删：``reviewer`` 收进 ``legacy``，作为历史痕迹保留但不再参与任何判定；
    裸 ``sha256`` 提升成 ``snapshot`` 对象，让新旧数据走同一套展示与校验。
    """
    e = dict(e)
    if not e.get("id"):
        e["id"] = ""
    snap = e.get("snapshot")
    if not isinstance(snap, dict):
        snap = {}
    if not snap.get("sha256") and e.get("sha256"):
        snap["sha256"] = str(e.get("sha256") or "")
    if snap:
        e["snapshot"] = {
            "file": str(snap.get("file") or "").replace("\\", "/"),
            "sha256": str(snap.get("sha256") or "").lower(),
            "bytes": int(snap.get("bytes") or 0),
            "taken_at": str(snap.get("taken_at") or ""),
        }
    else:
        e["snapshot"] = {"file": "", "sha256": "", "bytes": 0, "taken_at": ""}
    e.pop("sha256", None)
    if not isinstance(e.get("revisions"), list):
        e["revisions"] = []
    if e.get("reviewer"):
        legacy = dict(e.get("legacy") or {})
        legacy.setdefault("reviewer", e.get("reviewer"))
        legacy.setdefault("legacy_note", "v2 双人复核字段，v3 起仅留痕、不参与判定")
        e["legacy"] = legacy
    e.pop("reviewer", None)
    e["status"] = normalize_status(e.get("status") or e.get("entry_status"))
    return e


def normalize_status(s):
    s = str(s or "").strip().lower()
    if s in ("confirmed", "posted", "已入账", "已确认", "已复核"):
        return "confirmed"
    if s in ("void", "rejected", "退回", "已冲正", "已退回"):
        return "void"
    return "draft"          # pending / 待复核 / 草稿 / 空 → 一律草稿，未入账


def save_cfg(cfg, path):
    """原子写：先写 .tmp 再 os.replace，避免采集中途读到半个文件。"""
    path = Path(path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = {"version": 3,
               "_readme": cfg.get("_readme") or [],
               "entries": cfg["entries"]}
    with io.open(str(tmp), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------- 留痕（哈希链）

def _iter_log(log_path):
    p = Path(log_path)
    if not p.exists():
        return []
    out = []
    with io.open(str(p), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"__broken__": line[:200]})
    return out


def _canon(rec):
    body = {k: v for k, v in rec.items() if k != "hash"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _last_hash(records):
    for r in reversed(records):
        if isinstance(r, dict) and r.get("hash"):
            return str(r["hash"])
    return "0" * 64


def append_log(log_path, rec):
    """追加一行留痕，并把上一条的 hash 串进来（哈希链）。

    只追加、不改写：任何「回填一条更早的记录」都会让后续 prev_hash 对不上。
    """
    records = _iter_log(log_path)
    rec = dict(rec)
    rec.setdefault("ts", now_iso())
    rec.setdefault("host", host_tag())
    rec.setdefault("tool", "income_entry.py/v1")
    rec["prev_hash"] = _last_hash(records)
    rec["hash"] = sha256_text(_canon(rec))
    p = Path(log_path)
    if p.parent:
        p.parent.mkdir(parents=True, exist_ok=True)
    with io.open(str(p), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


# ---------------------------------------------------------------- 快照

def attach_snapshot(cfg_path, entry_id, src, taken_at=None, keep_file=True):
    """把凭证复制进 vouchers/ 并返回 snapshot 对象。

    复制而不是移动：原始凭证留在用户手上，删了台账也不影响取证。
    文件名带哈希前缀，同一张凭证重复上传会得到同名文件，天然去重。
    """
    if not src:
        return None
    s = Path(src).expanduser()
    if not s.exists():
        raise RuntimeError("快照文件不存在：%s" % s)
    digest = sha256_file(s)
    vdir = Path(cfg_path).parent / VOUCHER_DIRNAME
    vdir.mkdir(parents=True, exist_ok=True)
    name = "%s-%s%s" % (entry_id, digest[:8], (s.suffix or ".bin").lower())
    dest = vdir / name
    if not keep_file or not dest.exists():
        shutil.copy2(str(s), str(dest))
    return {"file": "%s/%s" % (VOUCHER_DIRNAME, name),
            "sha256": digest,
            "bytes": dest.stat().st_size if dest.exists() else s.stat().st_size,
            "taken_at": taken_at or now_iso()}


def check_snapshot_hash(cfg_path, snap):
    """返回 ok / missing / tampered / nosnap。"""
    sha = str((snap or {}).get("sha256") or "")
    if not sha:
        return "nosnap"
    rel = str((snap or {}).get("file") or "")
    if not rel:
        return "missing"
    p = Path(cfg_path).parent / rel
    if not p.exists():
        return "missing"
    return "ok" if sha256_file(p) == sha else "tampered"


# ---------------------------------------------------------------- 子命令

def next_id(entries, date):
    stamp = date.replace("-", "")
    used = set()
    for e in entries:
        eid = str(e.get("id") or "")
        if eid.startswith("INC-" + stamp + "-"):
            try:
                used.add(int(eid.rsplit("-", 1)[1]))
            except ValueError:
                pass
    return "INC-%s-%02d" % (stamp, max(used) + 1 if used else 1)


def find_entry(cfg, entry_id):
    for e in cfg["entries"]:
        if str(e.get("id") or "") == entry_id:
            return e
    return None


def require_entry(cfg, entry_id):
    e = find_entry(cfg, entry_id)
    if e is None:
        raise RuntimeError("找不到收入记录：%s" % entry_id)
    return e


def snapshot_summary(e):
    snap = e.get("snapshot") or {}
    return {"sha256": snap.get("sha256") or "",
            "file": snap.get("file") or "",
            "bytes": snap.get("bytes") or 0,
            "taken_at": snap.get("taken_at") or ""}


def entry_view(e):
    """写留痕时用的快照视图：只保留判定相关字段，避免把整条历史塞进每条日志。"""
    return {"id": e.get("id"), "date": e.get("date"), "type": e.get("type"),
            "amount": e.get("amount"), "biz_no": e.get("biz_no") or "",
            "note": e.get("note") or "", "status": e.get("status"),
            "entered_by": e.get("entered_by") or "",
            "snapshot": snapshot_summary(e)}


def cmd_add(args, cfg, cfg_path, log_path):
    date = args.date or dt.date.today().isoformat()
    typ = str(args.type or "").strip().lower()
    if typ not in TYPES:
        return die("收入类型只认 %s" % " / ".join(TYPES))
    try:
        amount = float(args.amount)
    except (TypeError, ValueError):
        return die("金额必须是数字")
    if amount <= 0:
        return die("金额必须为正数（支出走流水线，不在这里登记）")

    if not args.snapshot and not args.snapshot_sha256:
        return die("收入登记必须上传凭证快照：--snapshot <文件> 或 --snapshot-sha256 <哈希>。\n"
                   "        没有凭证的收入不进台账——这是单人机制的替代约束。")

    eid = next_id(cfg["entries"], date)
    by = args.by or operator_default()

    if args.snapshot:
        snap = attach_snapshot(cfg_path, eid, args.snapshot, args.taken_at)
    else:
        sha = str(args.snapshot_sha256 or "").strip().lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            return die("--snapshot-sha256 必须是 64 位十六进制")
        snap = {"file": "", "sha256": sha, "bytes": 0,
                "taken_at": args.taken_at or now_iso()}

    entry = {
        "id": eid,
        "date": date,
        "type": typ,
        "amount": round(amount, 2),
        "biz_no": str(args.biz_no or ""),
        "note": str(args.note or ""),
        "entered_by": by,
        "entered_at": now_iso(),
        "status": "confirmed" if args.confirm else "draft",
        "snapshot": snap,
        "revisions": [{"at": now_iso(), "by": by, "action": "CREATE",
                       "before": None, "after": {"amount": round(amount, 2),
                                                 "status": "draft"}}],
    }
    if args.confirm:
        entry["revisions"].append({"at": now_iso(), "by": by, "action": "SUBMIT",
                                   "before": {"status": "draft"},
                                   "after": {"status": "confirmed",
                                             "snapshot_sha256": snap["sha256"]}})
    cfg["entries"].append(entry)
    save_cfg(cfg, cfg_path)
    append_log(log_path, {"id": eid, "action": "CREATE", "by": by,
                          "before": None, "after": entry_view(entry),
                          "snapshot_sha256": snap["sha256"],
                          "note": args.note or ""})
    if args.confirm:
        append_log(log_path, {"id": eid, "action": "SUBMIT", "by": by,
                              "before": {"status": "draft"},
                              "after": {"status": "confirmed"},
                              "snapshot_sha256": snap["sha256"],
                              "note": "单人提交即入账，凭证快照与留痕承担可审计性"})
    print("[income] %s %s %.2f 积分 · %s · 状态 %s · 快照 %s"
          % (eid, TYPES[typ], amount, date, STATUS_TEXT[entry["status"]],
             (snap["sha256"][:12] + "…") if snap["sha256"] else "缺失"))
    return EXIT_OK


def cmd_confirm(args, cfg, cfg_path, log_path):
    e = require_entry(cfg, args.id)
    if e["status"] == "confirmed":
        print("[income] %s 已是「已入账」，无需重复提交" % args.id)
        return EXIT_OK
    if e["status"] == "void":
        return die("%s 已退回，不能直接入账；请重新登记一笔" % args.id)
    state = check_snapshot_hash(cfg_path, e.get("snapshot"))
    if state == "nosnap":
        return die("%s 没有凭证快照，不能入账" % args.id)
    if state == "missing":
        return die("%s 的快照文件不在（%s），不能入账"
                   % (args.id, (e.get("snapshot") or {}).get("file") or "未记录路径"))
    if state == "tampered":
        return die("%s 的快照与台账哈希不符（EVIDENCE_TAMPERED），先处理凭证再入账" % args.id)
    by = args.by or operator_default()
    before = entry_view(e)
    e["status"] = "confirmed"
    e["revisions"].append({"at": now_iso(), "by": by, "action": "SUBMIT",
                           "before": {"status": before["status"]},
                           "after": {"status": "confirmed",
                                     "snapshot_sha256": snapshot_summary(e)["sha256"]}})
    save_cfg(cfg, cfg_path)
    append_log(log_path, {"id": e["id"], "action": "SUBMIT", "by": by,
                          "before": before, "after": entry_view(e),
                          "snapshot_sha256": snapshot_summary(e)["sha256"], "note": ""})
    print("[income] %s 已入账（单人提交）" % args.id)
    return EXIT_OK


def cmd_edit(args, cfg, cfg_path, log_path):
    e = require_entry(cfg, args.id)
    if e["status"] == "confirmed" and not args.reopen:
        return die("%s 已入账，字段锁定。要改先加 --reopen 退回草稿再改，"
                   "改完重新 confirm（两次动作都会留痕）" % args.id)
    before = entry_view(e)
    by = args.by or operator_default()
    changed = False

    if args.reopen and e["status"] == "confirmed":
        e["status"] = "draft"
        e["revisions"].append({"at": now_iso(), "by": by, "action": "REOPEN",
                               "before": {"status": "confirmed"},
                               "after": {"status": "draft"}})
        append_log(log_path, {"id": e["id"], "action": "REOPEN", "by": by,
                              "before": before, "after": entry_view(e),
                              "snapshot_sha256": snapshot_summary(e)["sha256"],
                              "note": args.note or "改前先退回草稿"})
        changed = True

    if args.date and args.date != e.get("date"):
        e["date"] = args.date
        changed = True
    if args.type:
        typ = str(args.type).strip().lower()
        if typ not in TYPES:
            return die("收入类型只认 %s" % " / ".join(TYPES))
        e["type"] = typ
        changed = True
    if args.amount is not None:
        try:
            amount = float(args.amount)
        except (TypeError, ValueError):
            return die("金额必须是数字")
        if amount <= 0:
            return die("金额必须为正数")
        e["amount"] = round(amount, 2)
        changed = True
    if args.biz_no is not None:
        e["biz_no"] = str(args.biz_no)
        changed = True
    if args.note is not None:
        e["note"] = str(args.note)
        changed = True

    if args.snapshot:
        old = snapshot_summary(e)
        try:
            snap = attach_snapshot(cfg_path, e["id"], args.snapshot, args.taken_at)
        except RuntimeError as exc:
            return die(str(exc))
        e["snapshot"] = snap
        # 旧快照不删：历史哈希留在 revisions 里，换凭证本身也是一次留痕
        e["revisions"].append({"at": now_iso(), "by": by, "action": "EDIT",
                               "before": {"snapshot": old},
                               "after": {"snapshot": snapshot_summary(e)}})
        changed = True

    if not changed:
        return die("没有给出要改的字段（--date / --type / --amount / --biz-no / "
                   "--note / --snapshot / --reopen）")

    after = entry_view(e)
    e["revisions"].append({"at": now_iso(), "by": by, "action": "EDIT",
                           "before": before, "after": after})
    save_cfg(cfg, cfg_path)
    append_log(log_path, {"id": e["id"], "action": "EDIT", "by": by,
                          "before": before, "after": after,
                          "snapshot_sha256": snapshot_summary(e)["sha256"],
                          "note": args.note or ""})
    print("[income] %s 已修改（before/after 已留痕）· 状态 %s"
          % (args.id, STATUS_TEXT[e["status"]]))
    return EXIT_OK


def cmd_void(args, cfg, cfg_path, log_path):
    e = require_entry(cfg, args.id)
    if not str(args.reason or "").strip():
        return die("退回必须写原因（--reason），否则事后无法解释这笔账为什么没了")
    before = entry_view(e)
    by = args.by or operator_default()
    e["status"] = "void"
    e["revisions"].append({"at": now_iso(), "by": by, "action": "VOID",
                           "before": {"status": before["status"]},
                           "after": {"status": "void"}, "reason": args.reason})
    save_cfg(cfg, cfg_path)
    append_log(log_path, {"id": e["id"], "action": "VOID", "by": by,
                          "before": before, "after": entry_view(e),
                          "snapshot_sha256": snapshot_summary(e)["sha256"],
                          "note": args.reason})
    print("[income] %s 已退回 · 原因：%s" % (args.id, args.reason))
    return EXIT_OK


def cmd_verify(args, cfg, cfg_path, log_path):
    """复算每张快照的哈希 + 校验留痕哈希链。"""
    issues = []
    warnings = []
    rows = []
    conf_n = 0
    for e in cfg["entries"]:
        state = check_snapshot_hash(cfg_path, e.get("snapshot"))
        if e.get("status") == "confirmed":
            conf_n += 1
            if state == "nosnap":
                issues.append({"id": e.get("id"), "kind": "NO_SNAPSHOT_INCOME",
                               "detail": "已入账但无凭证快照"})
            elif state == "missing":
                issues.append({"id": e.get("id"), "kind": "EVIDENCE_MISSING",
                               "detail": "快照文件不在：%s"
                                         % (e.get("snapshot") or {}).get("file")})
            elif state == "tampered":
                issues.append({"id": e.get("id"), "kind": "EVIDENCE_TAMPERED",
                               "detail": "快照哈希与台账不符"})
        elif state == "tampered":
            issues.append({"id": e.get("id"), "kind": "EVIDENCE_TAMPERED",
                           "detail": "快照哈希与台账不符（未入账记录）"})
        elif state in ("nosnap", "missing"):
            # 草稿还没进账本，凭证不齐只是提示，不阻断（补证后才能 confirm）
            warnings.append({"id": e.get("id"), "kind": "DRAFT_EVIDENCE_PENDING",
                             "detail": "草稿尚未附齐凭证，入账前需补齐"})
        rows.append({"id": e.get("id"), "status": e.get("status"),
                     "amount": e.get("amount"), "snapshot": state,
                     "revisions": len(e.get("revisions") or [])})

    # 留痕链：prev_hash 必须串得上，hash 必须自洽
    prev = "0" * 64
    log_n = 0
    for i, rec in enumerate(_iter_log(log_path), 1):
        log_n += 1
        if "__broken__" in rec:
            issues.append({"id": "-", "kind": "LOG_UNREADABLE",
                           "detail": "第 %d 行不是合法 JSON" % i})
            break
        if rec.get("prev_hash") != prev:
            issues.append({"id": rec.get("id") or "-", "kind": "LOG_CHAIN_BROKEN",
                           "detail": "第 %d 行 prev_hash 断链" % i})
            break
        if rec.get("hash") != sha256_text(_canon(rec)):
            issues.append({"id": rec.get("id") or "-", "kind": "LOG_TAMPERED",
                           "detail": "第 %d 行内容与自身 hash 不符" % i})
            break
        prev = rec["hash"]

    result = {"entries": len(cfg["entries"]), "confirmed": conf_n,
              "logRecords": log_n, "ok": not issues,
              "issues": issues, "warnings": warnings, "rows": rows}
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("收入台账 %d 笔（已入账 %d）· 留痕 %d 条"
              % (result["entries"], conf_n, log_n))
        for r in rows:
            label = {"ok": "快照 ✓", "nosnap": "无快照",
                     "missing": "快照文件缺失", "tampered": "哈希不符"}[r["snapshot"]]
            print("  %-20s %-8s %8.2f  %-12s 留痕 %d 条"
                  % (r["id"] or "(无ID)", STATUS_TEXT.get(r["status"], r["status"]),
                     float(r["amount"] or 0), label, r["revisions"]))
        if warnings:
            print("提示 %d 项（不阻断，但入账前要补齐）：" % len(warnings))
            for it in warnings:
                print("  [%s] %s — %s" % (it["kind"], it["id"], it["detail"]))
        if issues:
            print("异常 %d 项：" % len(issues))
            for it in issues:
                print("  [%s] %s — %s" % (it["kind"], it["id"], it["detail"]))
        elif warnings:
            print("无阻断异常：已入账记录的快照均可复算、留痕链完整。")
        else:
            print("全部通过：快照可复算、留痕链完整。")

    msg_parts = []
    if any(i["kind"] in ("EVIDENCE_TAMPERED", "EVIDENCE_MISSING") for i in issues):
        msg_parts.append("取证异常")
    if any(i["kind"] == "NO_SNAPSHOT_INCOME" for i in issues):
        msg_parts.append("无凭证入账")
    if any(i["kind"].startswith("LOG_") for i in issues):
        msg_parts.append("留痕链断裂")
    if msg_parts:
        print("[verify] 需要处理：%s" % " / ".join(msg_parts))
        return EXIT_EVIDENCE
    return EXIT_OK


def cmd_list(args, cfg, cfg_path):
    rows = []
    for e in sorted(cfg["entries"], key=lambda x: (str(x.get("date") or ""),
                                                   str(x.get("id") or ""))):
        state = check_snapshot_hash(cfg_path, e.get("snapshot"))
        rows.append({"id": e.get("id"), "date": e.get("date"), "type": e.get("type"),
                     "amount": e.get("amount"), "status": e.get("status"),
                     "entered_by": e.get("entered_by") or "",
                     "snapshot": state,
                     "snapshot_sha256": (e.get("snapshot") or {}).get("sha256") or "",
                     "revisions": len(e.get("revisions") or [])})
    if getattr(args, "json", False):
        print(json.dumps({"entries": rows}, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        print("台账为空。用 add 子命令登记第一笔收入（必须带凭证快照）。")
        return EXIT_OK
    print("%-20s %-11s %-9s %9s  %-8s %-12s %s"
          % ("录入ID", "业务日", "类型", "积分", "状态", "录入人", "凭证"))
    for r in rows:
        print("%-20s %-11s %-9s %9.2f  %-8s %-12s %s"
              % (r["id"] or "-", r["date"] or "-", TYPES.get(r["type"], r["type"]),
                 float(r["amount"] or 0), STATUS_TEXT.get(r["status"], r["status"]),
                 r["entered_by"] or "-",
                 ("快照 " + r["snapshot_sha256"][:10] + "…") if r["snapshot_sha256"]
                 else "无快照"))
    return EXIT_OK


# ---------------------------------------------------------------- 入口

def build_parser():
    ap = argparse.ArgumentParser(
        prog="income_entry.py",
        description="积分收入单入口：单人上传凭证快照 + 录入记录留痕（替代双人复核）")
    ap.add_argument("--config", default=str(DEFAULT_CFG), help="收入台账路径")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="留痕日志路径（append-only）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    # --json 在子命令后面写也应该生效（verify --json 是人的直觉写法），
    # 所以给每个子命令挂一份同名参数；default=SUPPRESS 保证「没写就不覆盖」
    # 父级已经解析到的 True。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="以 JSON 输出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="登记一笔收入（必须带凭证快照）")
    a.add_argument("--date", help="业务日 YYYY-MM-DD（默认今天）")
    a.add_argument("--type", required=True, help="recharge / grant / refund")
    a.add_argument("--amount", required=True, help="正数积分")
    a.add_argument("--biz-no", dest="biz_no", help="厂商侧业务单号（幂等键）")
    a.add_argument("--note", help="备注")
    a.add_argument("--by", help="录入人（默认取系统账号）")
    a.add_argument("--snapshot", help="凭证快照文件路径（会被复制进 vouchers/）")
    a.add_argument("--snapshot-sha256", dest="snapshot_sha256",
                   help="已有的快照哈希（文件不在本机时用）")
    a.add_argument("--taken-at", dest="taken_at", help="凭证上的时间（截图时间）")
    a.add_argument("--confirm", action="store_true", help="登记后立即提交入账")

    c = sub.add_parser("confirm", help="提交入账（单人，需已有快照）")
    c.add_argument("id")
    c.add_argument("--by")

    d = sub.add_parser("edit", help="修改（已入账需先 --reopen）")
    d.add_argument("id")
    d.add_argument("--by")
    d.add_argument("--date")
    d.add_argument("--type")
    d.add_argument("--amount", type=float)
    d.add_argument("--biz-no", dest="biz_no")
    d.add_argument("--note")
    d.add_argument("--snapshot", help="换一张凭证（旧快照保留在 revisions 里）")
    d.add_argument("--taken-at", dest="taken_at")
    d.add_argument("--reopen", action="store_true", help="已入账记录先退回草稿再改")

    v = sub.add_parser("void", help="退回 / 冲正（必须写原因）")
    v.add_argument("id")
    v.add_argument("--by")
    v.add_argument("--reason", required=True)

    sub.add_parser("verify", parents=[common], help="复算快照哈希 + 校验留痕链")
    sub.add_parser("list", parents=[common], help="列出台账")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg_path = Path(args.config).expanduser()
    log_path = Path(args.log).expanduser()
    try:
        cfg = load_cfg(cfg_path)
    except RuntimeError as e:
        return die(str(e), 1)
    handlers = {"add": cmd_add, "confirm": cmd_confirm, "edit": cmd_edit,
                "void": cmd_void, "verify": cmd_verify, "list": cmd_list}
    try:
        if args.cmd == "list":
            return cmd_list(args, cfg, cfg_path)
        return handlers[args.cmd](args, cfg, cfg_path, log_path)
    except RuntimeError as e:
        return die(str(e))


if __name__ == "__main__":
    sys.exit(main())
