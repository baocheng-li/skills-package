#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量本地存储 —— 桌面版默认的 Store 实现（SQLite 单文件）。

为什么不是「再开一个数据库服务」
--------------------------------
桌面版要装在一台普通办公电脑上，不能要求装 PostgreSQL / MySQL，也不该
像 Electron 那样拖 150 MB 运行时。Python 标准库自带 ``sqlite3``，
一个文件就是一个库，备份 = 复制一个文件，删数据 = 删一个文件。

三种数据，两种去处
------------------
* **配置**（阈值、规则、余额状态、收入台账）→ SQLite 的 ``kv`` / ``income`` 表。
  它们小、要事务、要「改了立刻生效」，塞进一个文件最省心。
* **产物**（report.html 1.7 MB、tasks.csv、steps.csv）→ 落磁盘 ``out/``。
  这些是给人看/给 Excel 打开的，塞进数据库只会让用户找不到。
* **种子配置**（随包发布的默认阈值与规则）→ 留在包内的 ``config/``。读的时候
  「先查库、库里没有再用包里的」，这样升级包换了默认值能生效，
  而用户改过的值不会被覆盖。

数据目录
--------
    Windows   %LOCALAPPDATA%\\CostAudit\\data\\cost-audit.db
    macOS     ~/Library/Application Support/CostAudit/data/
    Linux     ~/.local/share/CostAudit/data/

可用环境变量 ``COST_AUDIT_HOME`` 整体重定向（便携模式、测试都靠它）。
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

APP = "CostAudit"
DB_NAME = "cost-audit.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS income (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    amount      REAL NOT NULL,
    note        TEXT,
    snapshot    TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at      TEXT NOT NULL,
    level       TEXT,
    window_days INTEGER,
    spent       REAL,
    balance     REAL,
    tasks       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_at ON runs(ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_income_date ON income(date DESC);
"""


def default_home():
    """按平台给数据目录。不 import platformdirs，少一个依赖少一处失败。"""
    env = os.environ.get("COST_AUDIT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class LocalStore(object):
    """实现 ``core.ports.Store``。配置进库，产物落盘，种子配置读包内。"""

    def __init__(self, seed_root=None, home=None):
        self.seed = Path(seed_root).resolve() if seed_root else None
        self.home = Path(home).resolve() if home else default_home()
        self.data = self.home / "data"
        self.out = self.home / "out"
        self.db_path = self.data / DB_NAME
        self.data.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---- 生命周期 ------------------------------------------------------
    def close(self):
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    @staticmethod
    def _key(rel):
        """相对路径 → 库内键。统一用正斜杠，避免 Windows 反斜杠造成同一个键两份数据。"""
        return str(rel).replace("\\", "/").lstrip("/")

    # ---- Store 协议 ----------------------------------------------------
    def exists(self, rel):
        k = self._key(rel)
        row = self._conn.execute("SELECT 1 FROM kv WHERE key=?", (k,)).fetchone()
        if row:
            return True
        return bool(self.seed and (self.seed / k).exists())

    def read_json(self, rel, default=None):
        k = self._key(rel)
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (k,)).fetchone()
        if row:
            try:
                return json.loads(row["value"])
            except ValueError:
                pass                      # 库里的值坏了就退回种子，不让一个坏行毁掉整次运行
        if self.seed:
            p = self.seed / k
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        return default if default is not None else {}

    def write_json(self, rel, obj):
        k = self._key(rel)
        text = json.dumps(obj, ensure_ascii=False, indent=1)
        self._conn.execute(
            "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (k, text, _now()))
        self._conn.commit()
        return len(text.encode("utf-8"))

    def write_text(self, rel, text):
        k = self._key(rel)
        if k.endswith(".json"):
            self._conn.execute(
                "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, text, _now()))
            self._conn.commit()
            return len(text.encode("utf-8"))
        p = self.out / k
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return len(text.encode("utf-8"))

    def read_text(self, rel, default=""):
        p = self.out / self._key(rel)
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return default
        if self.seed:
            q = self.seed / self._key(rel)
            if q.exists():
                try:
                    return q.read_text(encoding="utf-8")
                except OSError:
                    return default
        return default

    def resolve(self, rel):
        """Core 需要真实文件路径时（如外部凭证台账）用。

        库里的 JSON 先投影成真文件再返回路径——把「数据库里有一份」和
        「磁盘上有一份」对齐，避免 Core 拿到一个不存在的路径。
        """
        k = self._key(rel)
        # 库里优先：库里那份是用户当前值，种子那份只是「出厂默认」。
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (k,)).fetchone()
        if not row and self.seed and (self.seed / k).exists():
            return str(self.seed / k)
        if row:
            p = self.data / "materialized" / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(row["value"], encoding="utf-8")
            return str(p)
        return str(self.data / k)

    # ---- 业务便捷方法 --------------------------------------------------
    def setting(self, key, default=None):
        return self.read_json("settings/%s.json" % key, default)

    def set_setting(self, key, value):
        return self.write_json("settings/%s.json" % key, value)

    def add_income(self, date, kind, amount, note="", snapshot=""):
        cur = self._conn.execute(
            "INSERT INTO income(date,kind,amount,note,snapshot,created_at) VALUES(?,?,?,?,?,?)",
            (date, kind, float(amount), note, snapshot, _now()))
        self._conn.commit()
        return cur.lastrowid

    def list_income(self, limit=200):
        rows = self._conn.execute(
            "SELECT * FROM income ORDER BY date DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def record_run(self, level="", window_days=0, spent=0.0, balance=None, tasks=0):
        self._conn.execute(
            "INSERT INTO runs(ran_at,level,window_days,spent,balance,tasks) VALUES(?,?,?,?,?,?)",
            (_now(), level, int(window_days or 0), float(spent or 0),
             None if balance is None else float(balance), int(tasks or 0)))
        self._conn.commit()

    def recent_runs(self, limit=10):
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def info(self):
        """给页面侧栏与「关于」用：数据在哪、多大、什么时候建库的。"""
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        n_kv = self._conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
        n_inc = self._conn.execute("SELECT COUNT(*) FROM income").fetchone()[0]
        last = self._conn.execute(
            "SELECT ran_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {"home": str(self.home), "db": str(self.db_path), "bytes": size,
                "kv": n_kv, "income": n_inc,
                "lastRun": (last[0] if last else None)}

    # ---- 迁移 ----------------------------------------------------------
    def import_from_fs(self, root, dry=False):
        """把旧版源码形态的 config/*.json 一次性搬进库。

        只在「库里还没有这个键」时写入——迁移不能覆盖用户当前值。
        返回导入的键列表，便于在界面上如实告知「搬了哪几个」。
        """
        root = Path(root)
        cfg = root / "config"
        done = []
        if not cfg.is_dir():
            return done
        for p in sorted(cfg.glob("*.json")):
            k = "config/" + p.name
            if not dry and self._conn.execute(
                    "SELECT 1 FROM kv WHERE key=?", (k,)).fetchone():
                continue
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not dry:
                self.write_json(k, obj)
            done.append(p.name)
        return done
