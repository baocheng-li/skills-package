#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件系统 Store —— 把项目根下的 JSON / 文本适配成 core.ports.Store。

只有 Adapter 层知道「配置放在 config/、产物放在 out/」。
Core 只说要 ``config/budget.json``，具体落在哪个盘由宿主决定——这样同一个
Pipeline 可以在桌面版（本地盘）、Web（临时目录）、插件（只读挂载）里跑。
"""

import io
import json
import os
from pathlib import Path


class FsStore(object):
    """以某个目录为根的只写产物 + 只读配置。"""

    def __init__(self, root, out_dir=None):
        self.root = Path(root).resolve()
        self.out = Path(out_dir).resolve() if out_dir else self.root / "out"

    # ---- 路径 ----------------------------------------------------------
    def _p(self, rel):
        p = Path(str(rel)).expanduser()
        return p if p.is_absolute() else (self.root / p)

    def resolve(self, rel):
        return str(self._p(rel))

    def exists(self, rel):
        try:
            return self._p(rel).exists()
        except OSError:
            return False

    # ---- 读 ------------------------------------------------------------
    def read_json(self, rel, default=None):
        p = self._p(rel)
        try:
            with io.open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default if default is not None else {}

    def read_text(self, rel, default=""):
        try:
            with io.open(self._p(rel), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return default

    # ---- 写（一律落在 out/，除非显式绝对路径） --------------------------
    def _target(self, rel):
        p = Path(str(rel)).expanduser()
        if p.is_absolute():
            return p
        return self.out / p

    def write_json(self, rel, obj):
        p = self._target(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return os.path.getsize(p)

    def write_text(self, rel, text):
        p = self._target(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return os.path.getsize(p)

    def append_line(self, rel, line):
        p = self._target(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with io.open(p, "a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")
        return os.path.getsize(p)


class ReadOnlyStore(object):
    """只读挂载（插件 / Web 上传包）：写操作直接抛错，让问题在调用点暴露。"""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def _p(self, rel):
        p = Path(str(rel)).expanduser()
        return p if p.is_absolute() else (self.root / p)

    def resolve(self, rel):
        return str(self._p(rel))

    def exists(self, rel):
        return self._p(rel).exists()

    def read_json(self, rel, default=None):
        try:
            with io.open(self._p(rel), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default if default is not None else {}

    def read_text(self, rel, default=""):
        try:
            with io.open(self._p(rel), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return default

    def write_json(self, rel, obj):
        raise PermissionError("只读挂载，拒绝写入：%s" % rel)

    def write_text(self, rel, text):
        raise PermissionError("只读挂载，拒绝写入：%s" % rel)

    def append_line(self, rel, line):
        raise PermissionError("只读挂载，拒绝写入：%s" % rel)
