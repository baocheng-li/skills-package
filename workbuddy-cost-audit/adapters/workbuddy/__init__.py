#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy / CodeBuddy 积分·Token 消耗采集器
================================================
从本机真实运行数据生成步骤级消耗台账，输出 data.json 供看板渲染。

数据来源（全部为本机文件，不联网、不上传）：
  1) <home>/logs/**/*.log        —— AcpUsagePublisher 的 credit consumed（步骤级积分，权威值）
                                   + ModelProvider 的 qua.end（模型、token、耗时、finish_reason）
  2) <home>/workbuddy.db         —— sessions / session_usage / automations / automation_runs
  3) <home>/projects/**/*.jsonl  —— 工具调用、用户指令、session 归属

用法:
  python3 collect.py                        # 自动探测 ~/.workbuddy、~/.codebuddy
  python3 collect.py --root /path/to/home   # 指定数据目录
  python3 collect.py -o data.json --days 30

设计原则:
  * 只读：不写、不改任何原始文件；数据库以只读副本方式打开，避免锁竞争。
  * 可解释：每个积分、每条规则命中都能追到原始日志行。
  * 缺数据不编造：缺失字段留空并计入 coverage，由前端标注“折算/样本不足”。
"""

import os
import re
import io
import sys
import json
import glob
import time
import shutil
import sqlite3
import argparse
import datetime as dt
from collections import defaultdict, Counter

VERSION = "1.0.0"

# ---------------------------------------------------------------- 常量与配置

DEFAULT_RULES = {
    # 单步输入 token 超过该值即视为上下文膨胀，超出部分计入可优化空间
    "bloat_prompt_tokens": 60000,
    # 单任务积分阈值
    "task_credit_threshold": 200,
    # 工具循环：同一工具 + 同一参数连续重复次数阈值
    "tool_loop_repeat": 3,
    # 重试风暴：单任务工具报错次数阈值
    "retry_storm": 3,
    # 定时任务尖峰：单日消耗 / 7 日均值 倍数
    "cron_spike_multiple": 2.0,
}

MODEL_LABELS = {
    "deepseek-v4.1-flash": ("DeepSeek V4.1 Flash", "DS Flash", "DeepSeek", "高效", "#2ee6c5"),
    "deepseek-v4-flash":   ("DeepSeek V4 Flash",   "DS V4",    "DeepSeek", "高效", "#2ee6c5"),
    "deepseek-v4-pro":     ("DeepSeek V4 Pro",     "DS Pro",   "DeepSeek", "旗舰", "#ff5a68"),
    "fast-model":          ("快速档位",             "Fast",     "WorkBuddy", "高效", "#3d8bff"),
    "balanced-model":      ("均衡档位",             "Balanced", "WorkBuddy", "均衡", "#9b7bff"),
    "deep-model":          ("极致档位",             "Deep",     "WorkBuddy", "旗舰", "#ff5a68"),
    "hy3":                 ("混元 Hy3",            "Hy3",      "腾讯混元", "均衡", "#ffab2e"),
    "hy4-preview":         ("混元 Hy4 Preview",    "Hy4",      "腾讯混元", "旗舰", "#ff8a5b"),
    "glm-5.3":             ("GLM-5.3",             "GLM-5.3",  "智谱",     "均衡", "#9b7bff"),
    "glm-5.3-flash":       ("GLM-5.3-Flash",       "GLM Flash", "智谱",    "高效", "#6bc7e8"),
    "glm-4.6":             ("GLM-4.6",             "GLM-4.6",  "智谱",     "均衡", "#9b7bff"),
    "kimi-k2.5":           ("Kimi K2.5",           "Kimi K2.5", "月之暗面", "均衡", "#ffab2e"),
    "kimi-k3-1":           ("Kimi K3",             "Kimi K3",  "月之暗面", "旗舰", "#ffab2e"),
    "minimax-m3":          ("MiniMax-M3",          "MM-M3",    "MiniMax",  "均衡", "#35d07f"),
    "minimax-m2.5":        ("MiniMax-M2.5",        "MM-M2.5",  "MiniMax",  "高效", "#35d07f"),
    "step-5-preview":      ("Step-5 Preview",      "Step-5",   "阶跃",     "均衡", "#6bc7e8"),
    "wb-lite":             ("wb-lite（标题生成）",  "lite",     "WorkBuddy", "轻量", "#8aa4c8"),
    "glm-5.3-flash":       ("GLM-5.3-Flash",       "GLM Flash", "智谱",     "高效", "#6bc7e8"),
    "glm-5.2":             ("GLM-5.2",             "GLM-5.2",  "智谱",     "均衡", "#9b7bff"),
    "glm-5.1":             ("GLM-5.1",             "GLM-5.1",  "智谱",     "均衡", "#9b7bff"),
    "hy4-preview-f":       ("混元 Hy4 Preview",    "Hy4",      "腾讯混元", "均衡", "#ff8a5b"),
    "kimi-k2.7":           ("Kimi K2.7",           "Kimi K2.7", "月之暗面", "均衡", "#ffab2e"),
    "kimi-k2.8-preview":   ("Kimi K2.8 Preview",   "Kimi K2.8", "月之暗面", "均衡", "#ffab2e"),
    "minimax-m3.1-flash":  ("MiniMax-M3.1-Flash",  "MM-M3.1",  "MiniMax",  "高效", "#35d07f"),
    "mimo-v2.6-pro":       ("MiMo-V2.6-Pro",       "MiMo",     "小米",     "均衡", "#6bc7e8"),
    "unlabeled":           ("未标注模型",           "未标注",   "—",        "轻量", "#8aa4c8"),
}

TYPE_RULES = [
    ("抓取监测", ("抓取", "爬取", "政策", "法规", "监控", "监测", "日报", "早报", "简报", "巡检", "定时")),
    ("应用开发", ("小程序", "APP", "网页", "网站", "前端", "页面", "后台", "安装工具", "安装包",
                  "部署", "打包", "构建", "工具", "系统", "插件")),
    ("代码开发", ("重构", "修复", "bug", "实现", "函数", "接口", "脚本", "组件", "迁移", "升级", "改造", "代码")),
    ("表格处理", ("表格", "Excel", "CSV", "数据清洗", "对账", "字段", "汇总表")),
    ("文档生成", ("文档", "报告", "方案", "说明书", "周报", "纪要", "PPT", "演示", "汇报", "建议书", "白皮书")),
    ("调研分析", ("调研", "分析", "对比", "评估", "选型", "测算", "预算", "估值", "核查", "验证")),
    ("多模态生成", ("图片", "海报", "视频", "视觉", "logo", "插画", "封面", "配图")),
]

TS_RE = re.compile(r"^\[(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]")
CREDIT_RE = re.compile(
    r"credit consumed .*?sessionId=(\S+).*?requestId=(\S+).*?messageId=(\S+).*?credit=([\d.]+)"
)
MODEL_RE = re.compile(r"credit consumed .*?model=(\S+)")


def parse_ts(line):
    m = TS_RE.match(line)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    ms = int((m.group(7) or "0").ljust(3, "0"))
    try:
        return dt.datetime(y, mo, d, h, mi, s, ms * 1000)
    except ValueError:
        return None


def home_candidates(explicit):
    """按优先级列出可能的数据根目录（WorkBuddy 桌面端 / CodeBuddy CLI）。

    语义约定：一旦显式指定 root，就**只**用该目录——这是 --root 的预期行为，
    也是隔离测试与容器只读挂载能成立的前提。需要合并多个根时，不传 --root 走自动探测。
    """
    out = []
    if explicit:
        out.append(os.path.expanduser(explicit))
    else:
        for env in ("WORKBUDDY_HOME", "CODEBUDDY_HOME"):
            v = os.environ.get(env)
            if v:
                out.append(os.path.expanduser(v))
        out += [
            os.path.expanduser("~/.workbuddy"),
            os.path.expanduser("~/.codebuddy"),
            os.path.expanduser("~/Library/Application Support/workbuddy"),
            os.path.expanduser("~/Library/Application Support/codebuddy"),
        ]
    seen, res = set(), []
    for p in out:
        rp = os.path.realpath(p)
        if rp in seen or not os.path.isdir(rp):
            continue
        # 只接受确实含有数据的目录
        if not any(os.path.isdir(os.path.join(rp, k)) for k in ("logs", "projects")):
            continue
        seen.add(rp)
        res.append(rp)
    return res


# ---------------------------------------------------------------- 采集：日志

def scan_logs(home):
    """扫描日志目录，取出步骤级积分与请求级用量。"""
    credits = {}      # messageId -> dict
    requests = {}     # messageId -> qua 记录
    log_files = glob.glob(os.path.join(home, "logs", "**", "*.log"), recursive=True)

    for path in log_files:
        try:
            fh = io.open(path, "r", encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if "credit consumed" in line:
                    m = CREDIT_RE.search(line)
                    if not m:
                        continue
                    sid, rid, mid, c = m.groups()
                    ts = parse_ts(line)
                    prev = credits.get(mid)
                    val = float(c)
                    # 同一 message 可能因流式分片多次上报，取最大值（累计口径）
                    if prev is None or val > prev["credit"]:
                        credits[mid] = {
                            "session": sid, "request": rid, "credit": val,
                            "ts": ts.isoformat(sep=" ") if ts else None,
                        }
                elif "qua.end:" in line:
                    i = line.find("qua.end:")
                    raw = line[i + 8:].strip()
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    mid = d.get("message_id")
                    if not mid:
                        continue
                    ts = parse_ts(line)
                    d["_ts"] = ts.isoformat(sep=" ") if ts else None
                    d["_epoch"] = ts.timestamp() if ts else 0.0
                    requests[mid] = d
    return credits, requests, len(log_files)


# ---------------------------------------------------------------- 采集：数据库

def open_db_readonly(home):
    """把 db + wal 复制到临时目录后只读打开，避免与正在运行的客户端抢锁。"""
    src = os.path.join(home, "workbuddy.db")
    if not os.path.exists(src):
        return None
    tmpdir = os.path.join(
        os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp",
        "wb-cost-%d" % os.getpid(),
    )
    os.makedirs(tmpdir, exist_ok=True)
    dst = os.path.join(tmpdir, "workbuddy.db")
    try:
        shutil.copy2(src, dst)
        for suf in ("-wal", "-shm"):
            if os.path.exists(src + suf):
                shutil.copy2(src + suf, dst + suf)
        con = sqlite3.connect("file:%s?mode=ro" % dst.replace("\\", "/"), uri=True)
        con.execute("select 1 from sqlite_master limit 1").fetchone()
        return con
    except Exception:
        return None


def scan_db(con):
    out = {"sessions": {}, "usage": {}, "automations": [], "runs": defaultdict(list)}
    if con is None:
        return out
    try:
        cur = con.execute(
            "select id,title,cwd,status,created_at,updated_at,source_mode,model,mode,"
            "is_background_automation,conversation_origin,permission_mode from sessions"
        )
        for r in cur:
            out["sessions"][r[0]] = {
                "id": r[0], "title": r[1], "cwd": r[2], "status": r[3],
                "created_at": r[4], "updated_at": r[5], "source_mode": r[6],
                "model": r[7], "mode": r[8], "bg": bool(r[9]),
                "origin": r[10], "permission": r[11],
            }
    except sqlite3.Error:
        pass
    try:
        for sid, cj in con.execute("select session_id, credit_json from session_usage"):
            if not cj:
                continue
            try:
                out["usage"][sid] = {k: float(v) for k, v in json.loads(cj).items()}
            except ValueError:
                pass
    except sqlite3.Error:
        pass
    try:
        cur = con.execute(
            "select id,name,status,schedule_type,rrule,last_run_at,created_at,model_id,cwds "
            "from automations where deleted_at is null"
        )
        for r in cur:
            out["automations"].append({
                "id": r[0], "name": r[1], "status": r[2], "schedule_type": r[3],
                "rrule": r[4], "last_run_at": r[5], "created_at": r[6],
                "model": r[7], "cwds": r[8],
            })
    except sqlite3.Error:
        pass
    try:
        for aid, tid, st, created, meta in con.execute(
            "select automation_id, thread_id, status, created_at, metadata_json from automation_runs"
        ):
            out["runs"][aid].append({"thread": tid, "status": st, "created_at": created,
                                     "meta": meta})
    except sqlite3.Error:
        pass
    return out


# ---------------------------------------------------------------- 采集：会话记录

def scan_projects(home):
    """提取工具调用、用户指令、以及 requestId 与 sessionId 的归属关系。"""
    tools = defaultdict(list)     # requestId -> [tool step]
    prompts = {}                  # requestId -> 首条用户指令
    req_session = {}              # requestId -> sessionId
    msg_alias = {}                # messageId -> requestModelId（档位别名）
    msg_model = {}                # messageId -> 实际模型（来自 assistant 消息）
    files = glob.glob(os.path.join(home, "projects", "**", "*.jsonl"), recursive=True)

    pending = {}                  # callId -> 发起时间
    for path in files:
        try:
            fh = io.open(path, "r", encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                # 只按行首前 300 字符判断类型，避免整行扫描 + 让纯文本消息也能进入解析
                head = line[:300]
                if ('"role": "user"' not in head and '"role":"user"' not in head
                        and '"role": "assistant"' not in head and '"role":"assistant"' not in head
                        and '"function_call' not in head):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                pd = d.get("providerData") or {}
                rid = pd.get("conversationRequestId")
                mid = pd.get("messageId")
                if mid and pd.get("requestModelId"):
                    msg_alias[mid] = pd["requestModelId"]
                if mid and pd.get("model") and d.get("role") == "assistant":
                    msg_model[mid] = pd["model"]
                if rid and d.get("sessionId"):
                    req_session.setdefault(rid, d["sessionId"])

                if t == "function_call":
                    pending[d.get("callId")] = {
                        "tool": d.get("name"),
                        "arg": json.dumps(d.get("arguments"), ensure_ascii=False, sort_keys=True)[:400],
                        "ts": d.get("timestamp"),
                        "rid": rid, "mid": mid,
                    }
                elif t == "function_call_result":
                    p = pending.pop(d.get("callId"), None)
                    if not p:
                        continue
                    out = d.get("output")
                    text = json.dumps(out, ensure_ascii=False) if not isinstance(out, str) else out
                    err = ("<tool_use_error>" in text) or (d.get("status") not in (None, "completed"))
                    dur = None
                    if p["ts"] and d.get("timestamp"):
                        dur = max(0, int(d["timestamp"] - p["ts"]))
                    if p["rid"]:
                        tools[p["rid"]].append({
                            "tool": p["tool"], "arg": p["arg"], "err": err,
                            "ms": dur, "ts": d.get("timestamp"), "mid": p["mid"],
                            "snippet": text[:160].replace("\n", " "),
                        })
                elif t == "message" and d.get("role") == "user":
                    if not rid:
                        continue
                    content = d.get("content")
                    txt = ""
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "input_text":
                                txt += c.get("text", "")
                    elif isinstance(content, str):
                        txt = content
                    txt = re.sub(r"<system-reminder[\s\S]*?</system-reminder>", " ", txt)
                    txt = re.sub(r"\s+", " ", txt).strip()
                    if txt and not txt.startswith("<") and rid not in prompts:
                        prompts[rid] = txt[:120]
    return tools, prompts, req_session, msg_alias, msg_model, len(files)


# ---------------------------------------------------------------- 归并：任务与步骤

PROJECT_TEMP = "未绑定项目"


def project_of(cwd):
    """从 cwd 归一化项目名。

    WorkBuddy 当前不持久化 project_id，会话工作区形如
    ~/WorkBuddy/2026-09-29-20-35-09，属于临时目录，统一归入“未绑定项目”，
    避免把时间戳当成项目名制造伪维度。真实工程目录取末级目录名。
    """
    if not cwd:
        return PROJECT_TEMP
    p = str(cwd).replace("\\", "/").rstrip("/")
    parts = [x for x in p.split("/") if x]
    if not parts:
        return PROJECT_TEMP
    last = parts[-1]
    if len(parts) >= 2 and parts[-2].lower() in ("workbuddy", "codebuddy") \
            and re.match(r"^\d{4}-\d{2}-\d{2}", last):
        return PROJECT_TEMP
    return last



def set_type_rules(items):
    """由 config/classification.json 的 task_types 覆盖内置关键词表。

    items: [{"name": "抓取监测", "keys": ["抓取", ...]}, ...]
    顺序即优先级，先命中先返回。非法输入直接忽略，保留内置表。
    """
    global TYPE_RULES
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        keys = [str(k) for k in (it.get("keys") or []) if str(k).strip()]
        if name and keys:
            out.append((name, tuple(keys)))
    if out:
        TYPE_RULES = out
    return TYPE_RULES


def match_type(text):
    low = (text or "").lower()
    for name, keys in TYPE_RULES:
        for k in keys:
            if k.lower() in low:
                return name
    return None


def infer_type(title, prompt, tools):
    """标题优先，其次用户指令，最后按工具画像判定——避免长 prompt 里的偶然词决定分类。"""
    for src in (title, (prompt or "")[:60]):
        t = match_type(src)
        if t:
            return t
    ts = set(tools or [])
    if ts & {"Write", "Edit", "NotebookEdit", "MultiEdit"}:
        return "应用开发" if ("Write" in ts and "Bash" not in ts) else "代码开发"
    if ts & {"Bash", "PS"}:
        return "代码开发"
    if ts & {"WebSearch", "WebFetch", "domain_search"}:
        return "调研分析"
    if ts & {"Read", "Grep", "Glob"}:
        return "调研分析"
    return "通用任务"


def level_of(n_model_steps):
    if n_model_steps <= 3:
        return "L1"
    if n_model_steps <= 8:
        return "L2"
    if n_model_steps <= 15:
        return "L3"
    return "L4"


def build_tasks(credits, requests, tools, prompts, req_session, msg_alias, msg_model, db, rules):
    """把步骤级日志聚合成任务（task = 一次用户触发或自动触发的执行轮次）。"""
    # 1) 以 requestId 分组所有步骤
    steps_by_req = defaultdict(list)
    for mid, q in requests.items():
        rid = q.get("conversation_request_id")
        if not rid:
            continue
        c = credits.get(mid)
        steps_by_req[rid].append({
            "mid": mid,
            "model": q.get("model") or "unknown",
            "alias": msg_alias.get(mid),
            "inTok": int(q.get("prompt_tokens") or 0),
            "outTok": int(q.get("completion_tokens") or 0),
            "ms": int(q.get("total_cost_ms") or 0),
            "ttft": int(q.get("ttft_ms") or 0),
            "rtype": q.get("request_type") or "chat",
            "finish": q.get("finish_reason"),
            "err": bool(q.get("model_error_code")),
            "ts": q.get("_ts"),
            "epoch": q.get("_epoch") or 0.0,
            "credit": (c or {}).get("credit"),
        })
    # 仅有积分、无 token 明细的历史步骤（早期日志）也要入账
    for mid, c in credits.items():
        rid = c.get("request")
        if not rid:
            continue
        if any(s["mid"] == mid for s in steps_by_req.get(rid, [])):
            continue
        steps_by_req[rid].append({
            "mid": mid, "model": msg_model.get(mid) or "unlabeled",
            "alias": msg_alias.get(mid), "inTok": 0, "outTok": 0, "ms": 0,
            "ttft": 0, "rtype": "chat", "finish": None, "err": False,
            "ts": c.get("ts"), "epoch": 0.0, "credit": c["credit"],
        })

    # 2) 混合折算率：按模型用真实 (积分, token) 拟合每千 token 积分
    rate_num, rate_den = defaultdict(float), defaultdict(float)
    for st in (s for lst in steps_by_req.values() for s in lst):
        if st["credit"] and st["inTok"] + st["outTok"] > 0:
            rate_num[st["model"]] += st["credit"]
            rate_den[st["model"]] += (st["inTok"] + st["outTok"]) / 1000.0
    blended = {m: rate_num[m] / rate_den[m] for m in rate_num if rate_den[m] > 0}
    all_num = sum(rate_num.values())
    all_den = sum(rate_den.values())
    fallback_rate = (all_num / all_den) if all_den else 0.1

    tasks = []
    per_request = {}
    sys_stat = {"credits": 0.0, "calls": 0, "tokens": 0, "kinds": {}, "calls_detail": []}
    for rid, model_steps in steps_by_req.items():
        model_steps.sort(key=lambda s: (s["epoch"] or 0, s["ts"] or ""))
        known = [x["model"] for x in model_steps if x["model"] and x["model"] != "unlabeled"]
        if known:
            top_model = Counter(known).most_common(1)[0][0]
            for x in model_steps:
                if not x["model"] or x["model"] == "unlabeled":
                    x["model"] = top_model
        t_tools = sorted(tools.get(rid, []), key=lambda x: x.get("ts") or 0)
        sess_id = req_session.get(rid) or next(
            (c["session"] for c in credits.values() if c["request"] == rid), None
        )
        sess = db["sessions"].get(sess_id, {})

        # 3) 组装步骤序列：模型步按时间插入工具步
        seq, ti = [], 0
        for idx, s in enumerate(model_steps):
            while ti < len(t_tools) and (t_tools[ti].get("ts") or 0) <= (s["epoch"] * 1000 or 0):
                t = t_tools[ti]
                seq.append({"kind": "tool", "tool": t["tool"], "ms": t["ms"] or 0,
                            "err": t["err"], "ts": t["ts"], "snippet": t["snippet"],
                            "arg": t.get("arg") or ""})
                ti += 1
            seq.append({"kind": "model", "src": s})
        while ti < len(t_tools):
            t = t_tools[ti]
            seq.append({"kind": "tool", "tool": t["tool"], "ms": t["ms"] or 0,
                        "err": t["err"], "ts": t["ts"], "snippet": t["snippet"],
                        "arg": t.get("arg") or ""})
            ti += 1

        n_model = len(model_steps)
        rtypes = set(s["rtype"] for s in model_steps)
        if rtypes and rtypes <= {"conversation_topic", "compress"}:
            # 话题生成 / 上下文压缩：不属于用户任务，单独记录为「隐藏调用」。
            # 客户端一般不为这类请求上报积分，按本机实测折算率估算并标记 est。
            for x in model_steps:
                est = x["credit"]
                estimated = est is None
                if estimated:
                    est = (x["inTok"] + x["outTok"]) / 1000.0 * blended.get(
                        x["model"], fallback_rate)
                sys_stat["credits"] += est or 0
                sys_stat["calls"] += 1
                sys_stat["tokens"] += x["inTok"] + x["outTok"]
                kind = x["rtype"] or "hidden"
                sys_stat["kinds"][kind] = sys_stat["kinds"].get(kind, 0) + 1
                sys_stat["calls_detail"].append({
                    "ts": x["ts"], "mid": x["mid"], "rid": rid,
                    "model": x["model"], "inTok": x["inTok"], "outTok": x["outTok"],
                    "ms": x["ms"], "rtype": kind,
                    "credits": round(est or 0, 2), "est": estimated,
                    "session": req_session.get(rid) or sess_id,
                })
            continue
        lvl = level_of(n_model)

        credit_sum, est_credit = 0.0, 0.0
        in_sum = sum(s["inTok"] for s in model_steps)
        out_sum = sum(s["outTok"] for s in model_steps)
        for s in model_steps:
            if s["credit"] is not None:
                credit_sum += s["credit"]
            else:
                rate = blended.get(s["model"], fallback_rate)
                e = (s["inTok"] + s["outTok"]) / 1000.0 * rate
                credit_sum += e
                est_credit += e

        # 4) 异常判定
        flags, waste = [], 0.0
        bloat_credits = 0.0
        for s in model_steps:
            if s["inTok"] > rules["bloat_prompt_tokens"] and s["credit"]:
                bloat_credits += s["credit"] * (s["inTok"] - rules["bloat_prompt_tokens"]) / s["inTok"]
        if bloat_credits >= 1:
            flags.append("上下文膨胀")
            waste += bloat_credits

        err_tools = [t for t in t_tools if t["err"]]
        if err_tools:
            # 工具报错后模型必须重新规划：把紧随其后的模型步积分记为返工成本
            rework = 0.0
            for t in err_tools:
                nxt = next((s for s in model_steps if (s["epoch"] * 1000 or 0) >= (t.get("ts") or 0)), None)
                if nxt and nxt["credit"]:
                    rework += nxt["credit"] * 0.5
            if rework >= 1:
                waste += rework
            if len(err_tools) >= rules["retry_storm"]:
                flags.append("重试风暴")

        # 工具循环：同工具同参数重复
        loop_extra = 0
        run_key, run_len = None, 0
        for t in t_tools:
            key = (t["tool"], t["arg"])
            if key == run_key:
                run_len += 1
                if run_len >= rules["tool_loop_repeat"]:
                    loop_extra += 1
            else:
                run_key, run_len = key, 1
        if loop_extra:
            flags.append("工具循环")
            avg_step_credit = credit_sum / max(1, n_model)
            waste += avg_step_credit * loop_extra

        if credit_sum > rules["task_credit_threshold"]:
            flags.append("单任务超阈值")

        # 5) 基础属性
        first_ts = min((s["ts"] for s in model_steps if s["ts"]), default=None) or \
                   min((t["ts"] for t in t_tools if t.get("ts")), default=None)
        last_ts = max((s["ts"] for s in model_steps if s["ts"]), default=None)
        duration_ms = sum(s["ms"] for s in model_steps)
        if first_ts and last_ts:
            try:
                span = (dt.datetime.fromisoformat(last_ts) - dt.datetime.fromisoformat(first_ts)).total_seconds()
                duration_ms = max(duration_ms, int(span * 1000))
            except ValueError:
                pass

        models = [s["model"] for s in model_steps if s["model"]]
        main_model = Counter(models).most_common(1)[0][0] if models else "unknown"
        name = sess.get("title") or prompts.get(rid) or "未命名任务"
        task_type = infer_type(name, prompts.get(rid) or "",
                               [t["tool"] for t in t_tools])

        rtype = max((s["rtype"] for s in model_steps), key=lambda r: sum(1 for x in model_steps if x["rtype"] == r), default="chat")
        if rtype == "automation" or sess.get("bg"):
            trigger = "定时" if rtype == "automation" else "后台自动化"
        else:
            trigger = "人工"

        failed_models = sum(1 for s in model_steps if s["err"])
        if failed_models and failed_models >= n_model:
            status = "失败"
        elif failed_models or err_tools:
            status = "部分交付"
        else:
            status = "成功"

        steps = []
        for i, item in enumerate(seq, 1):
            if item["kind"] == "model":
                s = item["src"]
                n_seq = len(seq)
                if i <= max(2, int(n_seq * 0.1)):
                    phase = "规划"
                elif i > n_seq * 0.85:
                    phase = "生成"
                else:
                    phase = "推理"
                steps.append({
                    "i": i, "kind": "model", "phase": phase,
                    "model": s["model"], "alias": s["alias"], "tool": None,
                    "inTok": s["inTok"], "outTok": s["outTok"],
                    "credits": round(s["credit"], 2) if s["credit"] is not None else None,
                    "ms": s["ms"], "ttft": s["ttft"], "retry": 0, "ts": s["ts"],
                    "mid": s["mid"], "est": s["credit"] is None, "cache": None,
                })
            else:
                steps.append({
                    "i": i, "kind": "tool", "phase": "工具调用",
                    "model": None, "alias": None, "tool": item["tool"],
                    "inTok": 0, "outTok": 0, "credits": 0,
                    "ms": item["ms"], "ttft": 0, "retry": 1 if item["err"] else 0,
                    "ts": dt.datetime.fromtimestamp(item["ts"] / 1000).isoformat(sep=" ")
                    if item.get("ts") else None,
                    "snippet": item["snippet"], "err": item["err"],
                    "arg": item.get("arg") or "", "toolErr": bool(item["err"]),
                })

        per_request[rid] = round(credit_sum, 2)

        name = name[:46]
        turn = sum(1 for x in tasks if x["session"] == sess_id)
        if turn:
            name = "%s · 第 %d 轮" % (name[:38], turn + 1)

        ts_text = first_ts or "1970-01-01 00:00:00"
        day = ts_text[:10]
        tasks.append({
            "id": "tk_" + rid[:8],
            "rid": rid,
            "session": sess_id,
            "name": name,
            "type": task_type,
            "level": lvl,
            "trigger": trigger,
            "rtype": rtype,
            "mainChannel": main_model,
            "models": sorted(set(models)),
            "steps": steps,
            "credits": round(credit_sum, 2),
            "creditsEstimated": round(est_credit, 2),
            "inTok": in_sum,
            "outTok": out_sum,
            "duration": round(duration_ms / 1000, 1),
            "retry": len(err_tools),
            "nTools": len(set(t["tool"] for t in t_tools)),
            "nModelSteps": n_model,
            "status": status,
            "flags": flags,
            "waste": round(waste, 1),
            "day": day,
            "ts": ts_text,
            "cwd": sess.get("cwd") or "",
            "project": project_of(sess.get("cwd") or ""),
            "modelChain": " → ".join(dict.fromkeys(m for m in models if m)) or "unknown",
        })

    tasks.sort(key=lambda t: t["ts"], reverse=True)
    return tasks, blended, fallback_rate, sys_stat, per_request


# ---------------------------------------------------------------- 聚合与口径

def aggregate(tasks, days, rules, today):
    first = min(t["day"] for t in tasks)
    if days <= 0:
        start = dt.date.fromisoformat(first)
    else:
        start = max(dt.date.fromisoformat(first), today - dt.timedelta(days=days - 1))
    span = (today - start).days + 1
    window = [t for t in tasks if start.isoformat() <= t["day"] <= today.isoformat()]

    # 只输出有消耗的日期：Agent 使用本身是事件驱动的，补齐 0 只会拉扁曲线
    by_date = defaultdict(list)
    for t in window:
        by_date[t["day"]].append(t)
    daily = []
    for key in sorted(by_date):
        d = dt.date.fromisoformat(key)
        rows = by_date[key]
        rec = {"d": (today - d).days, "date": key,
               "label": "%d/%d" % (d.month, d.day),
               "人工": 0.0, "定时": 0.0, "后台自动化": 0.0,
               "credits": 0.0, "tasks": len(rows), "tokens": 0}
        for t in rows:
            rec[t["trigger"]] += t["credits"]
            rec["credits"] += t["credits"]
            rec["tokens"] += t["inTok"] + t["outTok"]
        for k in ("人工", "定时", "后台自动化", "credits"):
            rec[k] = round(rec[k], 1)
        daily.append(rec)

    # 异常：可优化空间 > 0 的任务
    anomalies = sorted([t for t in window if t["flags"] and t["waste"] > 0],
                       key=lambda t: -t["waste"])
    rule_defs = [
        ("TOOL_LOOP", "工具循环", "同一 tool + 同一参数连续 ≥ %d 次" % rules["tool_loop_repeat"], "连续 %d 次" % rules["tool_loop_repeat"], "中"),
        ("CTX_BLOAT", "上下文膨胀", "单步输入 token > %s，超出部分计入可优化空间" % f"{rules['bloat_prompt_tokens']:,}", f"{rules['bloat_prompt_tokens']:,} tok", "高"),
        ("RETRY_STORM", "重试风暴", "单任务工具报错 ≥ %d 次" % rules["retry_storm"], "≥ %d 次" % rules["retry_storm"], "高"),
        ("TASK_BUDGET", "单任务积分阈值", "单任务积分 > %d，需复核是否可拆分" % rules["task_credit_threshold"], f"{rules['task_credit_threshold']} 积分", "中"),
        ("CRON_SPIKE", "定时任务尖峰", "自动化任务单日消耗超过其自身均值的 %s 倍" % rules["cron_spike_multiple"], "× %s" % rules["cron_spike_multiple"], "高"),
    ]
    rules_out = []
    for rid, name, desc, th, lvl in rule_defs:
        hits = [t for t in window if name in t["flags"]]
        rules_out.append({"id": rid, "name": name, "desc": desc, "th": th,
                          "hits": len(hits), "waste": round(sum(t["waste"] for t in hits), 1),
                          "on": True, "level": lvl})

    # 定时任务：按任务名聚合
    autos = {}
    for t in window:
        if t["trigger"] == "人工":
            continue
        key = t["name"]
        a = autos.setdefault(key, {
            "name": key, "freq": "—", "level": t["level"], "runs": 0,
            "credits": 0.0, "model": t["mainChannel"], "status": "正常",
            "owner": "本机", "days": set(), "tasks": [],
        })
        a["runs"] += 1
        a["credits"] += t["credits"]
        a["days"].add(t["day"])
        a["tasks"].append(t)
        if t["flags"]:
            a["status"] = "尖峰"
        if t["level"] > a["level"]:
            a["level"] = t["level"]
    auto_list = []
    for a in autos.values():
        ndays = max(1, len(a["days"]))
        per = span / ndays
        auto_list.append({
            "name": a["name"],
            "freq": "每日" if per < 1.5 else ("每 %d 天 1 次" % round(per)),
            "level": a["level"], "runs": a["runs"],
            "avg": round(a["credits"] / max(1, a["runs"]), 1),
            "daily": round(a["credits"] / max(1, span), 2),
            "prev": 0, "model": a["model"], "status": a["status"],
            "owner": a["owner"], "days": ndays,
        })
    auto_list.sort(key=lambda x: -(x["avg"] * x["runs"]))

    # 模型 × 复杂度：真实观测（非受控实验）
    cells = defaultdict(list)
    for t in window:
        if t["mainChannel"] in ("unlabeled", "", None):
            continue
        cells[(t["mainChannel"], t["level"])].append(t)
    bench = []
    for (model, lvl), rows in cells.items():
        n = len(rows)
        succ = sum(1 for r in rows if r["status"] == "成功") / n
        bench.append({
            "lvl": lvl, "model": model, "n": n,
            "succ": round(succ * 100, 1),
            "credits": round(sum(r["credits"] for r in rows) / n, 1),
            "e2e": round(sum(r["duration"] for r in rows) / n, 1),
            "infer": round(sum(s["ms"] for r in rows for s in r["steps"] if s["kind"] == "model") / n / 1000, 1),
            "wait": round(sum(s["ms"] for r in rows for s in r["steps"] if s["kind"] == "tool") / n / 1000, 1),
            "avgIn": round(sum(r["inTok"] for r in rows) / n),
            "avgOut": round(sum(r["outTok"] for r in rows) / n),
            "steps": round(sum(r["nModelSteps"] for r in rows) / n, 1),
        })
    bench.sort(key=lambda r: (r["lvl"], r["credits"]))

    # 模型总体
    per_model = defaultdict(lambda: {"calls": 0, "credits": 0.0, "in": 0, "out": 0, "tasks": 0})
    for t in window:
        for s in t["steps"]:
            if s["kind"] != "model" or not s["model"]:
                continue
            m = per_model[s["model"]]
            m["calls"] += 1
            m["credits"] += s["credits"] or 0
            m["in"] += s["inTok"]
            m["out"] += s["outTok"]
    for t in window:
        per_model[t["mainChannel"]]["tasks"] += 1

    return {
        "window": window, "daily": daily, "span": span, "anomalies": anomalies,
        "rules": rules_out, "automations": auto_list, "bench": bench,
        "perModel": per_model,
    }


# ---------------------------------------------------------------- 对外入口

def collect_data(days=0, root=None, rules=None, log=None):
    """采集本机用量并返回 (dashboard_data, context)。

    days<=0 表示覆盖全部历史；context 提供对账所需的请求级积分明细。
    """
    homes = home_candidates(root)
    if not homes:
        raise RuntimeError("未找到数据目录，请用 --root 指定 WorkBuddy 数据目录（含 logs/ 与 projects/）")

    all_credits, all_requests = {}, {}
    all_tools, all_prompts, all_req_session, all_alias, all_msg_model = {}, {}, {}, {}, {}
    db = {"sessions": {}, "usage": {}, "automations": [], "runs": {}}
    n_logs = n_projects = 0

    for home in homes:
        if log:
            log("扫描 %s" % home)
        c, r, nf = scan_logs(home)
        all_credits.update(c)
        all_requests.update(r)
        n_logs += nf
        t, p, rs, al, mm, npj = scan_projects(home)
        all_tools.update(t)
        all_prompts.update(p)
        all_req_session.update(rs)
        all_alias.update(al)
        all_msg_model.update(mm)
        n_projects += npj
        con = open_db_readonly(home)
        if con:
            d = scan_db(con)
            db["sessions"].update(d["sessions"])
            db["usage"].update(d["usage"])
            db["automations"] += d["automations"]
            for k, v in d["runs"].items():
                db["runs"].setdefault(k, []).extend(v)
            con.close()

    merged_rules = dict(DEFAULT_RULES)
    if rules:
        merged_rules.update(rules)
    tasks, blended, fallback_rate, sys_stat, per_request = build_tasks(
        all_credits, all_requests, all_tools, all_prompts, all_req_session, all_alias,
        all_msg_model, db, merged_rules
    )
    if not tasks:
        raise RuntimeError("未采集到任何任务，请确认目录正确且客户端至少运行过一次任务")

    max_day = max(t["day"] for t in tasks)
    today = dt.date.fromisoformat(max_day)
    agg = aggregate(tasks, days, merged_rules, today)
    window_days = agg["span"]

    models = []
    for mid, st in sorted(agg["perModel"].items(), key=lambda kv: -kv[1]["credits"]):
        label, short, vendor, tier, color = MODEL_LABELS.get(
            mid, (mid, mid[:10], "—", "—", "#8aa4c8")
        )
        tok = st["in"] + st["out"]
        rate = (st["credits"] / (tok / 1000.0)) if tok else 0.0
        models.append({
            "id": mid, "name": label, "short": short, "vendor": vendor,
            "tier": tier, "color": color,
            "ratePerKTok": round(rate, 5),
            "calls": st["calls"], "tasks": st["tasks"],
            "inTok": st["in"], "outTok": st["out"], "credits": round(st["credits"], 1),
        })

    window_tasks = agg["window"]
    real_credit_steps = sum(1 for t in window_tasks for s in t["steps"]
                            if s["kind"] == "model" and s["credits"] is not None)
    est_credit_steps = sum(1 for t in window_tasks for s in t["steps"]
                           if s["kind"] == "model" and s["credits"] is None)
    n_steps = real_credit_steps + est_credit_steps
    data = {
        "meta": {
            "version": VERSION,
            "generatedAt": dt.datetime.now().isoformat(sep=" ", timespec="seconds"),
            "homes": homes,
            "windowDays": window_days,
            "dailyMode": "active-days",
            "span": [min(t["day"] for t in tasks), max_day],
            "counts": {
                "tasks": len(window_tasks), "steps": n_steps,
                "logFiles": n_logs, "projectFiles": n_projects,
                "sessions": len(db["sessions"]), "models": len(models),
                "creditStepsReal": real_credit_steps,
                "creditStepsEstimated": est_credit_steps,
            },
            "coverage": {
                "creditAccuracy": round(real_credit_steps / n_steps, 4) if n_steps else 0,
                "tokenCoverage": round(
                    sum(1 for t in tasks for s in t["steps"]
                        if s["kind"] == "model" and s["inTok"] > 0) / n_steps, 4
                ) if n_steps else 0,
            },
            "rates": {"blended": {k: round(v, 5) for k, v in blended.items()},
                      "fallback": round(fallback_rate, 5)},
            "rules": merged_rules,
            "source": "WorkBuddy/CodeBuddy 本机日志、会话记录与本地数据库（离线只读采集）",
            "capabilities": {
                "cacheTokens": False,
                "balance": False,
                "ledger": False,
                "toolArgs": True,
            },
            "systemCredits": round(sys_stat["credits"], 1),
            "systemCalls": sys_stat["calls"],
            "systemTokens": sys_stat["tokens"],
            "systemKinds": sys_stat["kinds"],
        },
        "models": models,
        "tasks": window_tasks,
        "daily": agg["daily"],
        "anomalies": [t["id"] for t in agg["anomalies"]],
        "rules": agg["rules"],
        "automations": agg["automations"],
        "bench": agg["bench"],
    }
    ctx = {
        "homes": homes,
        "log_files": n_logs,
        "project_files": n_projects,
        "per_request": per_request,
        "usage": db["usage"],
        "db_automations": db["automations"],
        "db_runs": {k: v for k, v in db["runs"].items()},
        "sessions": db["sessions"],
        "system": sys_stat,
    }
    return data, ctx


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="采集 WorkBuddy/CodeBuddy 本机真实用量")
    ap.add_argument("--root")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    payload, _ctx = collect_data(args.days, args.root, log=lambda m: print(m, flush=True))
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2 if args.pretty else None)
    print("输出：%s" % os.path.abspath(args.out))
