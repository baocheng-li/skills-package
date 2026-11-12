#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面版宿主 —— 本地只读 HTTP 服务 + 浏览器。

为什么不是 Electron
-------------------
Electron 要多 150 MB 运行时，而这里要的只是「双击打开一个能用的报告」。
标准库的 ``http.server`` 加上 ``--single-file``（默认）就够了：
报告本身是零外链的单文件 HTML，服务端只负责把文件吐出去。

安全边界（和 CLI 一致，不放宽）
------------------------------
* 只绑定 127.0.0.1，不监听 0.0.0.0；
* 只 serve 产物目录，路径穿越一律 403；
* 只读：``/api/*`` 只有 GET，没有写接口。

用法
----
    python -m hosts.desktop                 # 生成报告并打开浏览器
    python -m hosts.desktop --port 8777 --no-open
"""

import json
import mimetypes
import os
import shutil
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import state as state_mod          # noqa: E402
from adapters.store import FsStore               # noqa: E402
from adapters.workbuddy.source import WorkBuddySource  # noqa: E402
from core import pipeline as pipeline_mod        # noqa: E402
from core.models import PipelineOptions          # noqa: E402
from renderer import html as html_r              # noqa: E402

BIND = "127.0.0.1"


def build_report(out_dir, payload=None, store=None, state=None):
    """生成报告，返回 (report 路径, 摘要 dict)。

    ``store`` 可注入：桌面版安装包传 ``LocalStore``（SQLite 单文件），
    源码形态与测试不传就走 ``FsStore``（JSON 文件）。Core 与 Renderer
    完全不需要知道这件事——这正是 Store 端口存在的理由。
    """
    payload = payload or {}
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # state 可由桌面版直接注入（它从 SQLite 读），否则按老规矩读文件
    if state is None:
        state = state_mod.load(payload.get("balance_state"))
    if store is None:
        store = FsStore(ROOT, out_dir)
    model = pipeline_mod.Pipeline(source=WorkBuddySource(), store=store).run(
        PipelineOptions(last=payload.get("last"), days=int(payload.get("days") or 0),
                        on_log=lambda m: print(m, flush=True)), state=state)
    if (model.meta or {}).get("empty"):
        raise RuntimeError("未发现可分析的日志目录")
    size = html_r.render(str(out_dir / "report.html"), model.data,
                         skin=payload.get("skin") or "dark")
    summary = {"bytes": size, "level": model.level, "budget": model.budget,
               "totals": model.totals()}
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return out_dir / "report.html", summary


class Handler(BaseHTTPRequestHandler):
    root = None                     # 由 make_server 注入

    def log_message(self, fmt, *args):        # 安静点，别把控制台刷满
        pass

    def _safe(self, path):
        """把 URL 映射到 root 内的文件；越界返回 None。"""
        rel = unquote(path.split("?", 1)[0]).lstrip("/")
        if rel in ("", "/"):
            rel = "report.html"
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None
        return target if target.is_file() else None

    def do_GET(self):
        if self.path.startswith("/api/summary"):
            p = self.root / "summary.json"
            body = p.read_bytes() if p.exists() else b"{}"
            ctype = "application/json; charset=utf-8"
        else:
            f = self._safe(self.path)
            if not f:
                self.send_error(403 if ".." in self.path else 404)
                return
            body = f.read_bytes()
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(out_dir, port=0):
    root = Path(out_dir).resolve()
    handler = type("BoundHandler", (Handler,), {"root": root})
    httpd = ThreadingHTTPServer((BIND, port), handler)
    return httpd


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    out_dir = Path(arg("--out", str(ROOT / "out")))
    port = int(arg("--port", "0"))
    print("正在采集并生成报告…", flush=True)
    report, summary = build_report(out_dir)
    print("报告：%s（%.2f MB）" % (report, summary["bytes"] / 1048576.0))
    print("告警：%s / 当前余额：%s"
          % (summary["budget"].get("levelText"),
             summary["budget"].get("balance")))

    httpd = make_server(out_dir, port)
    url = "http://%s:%d/" % (BIND, httpd.server_address[1])
    print("桌面版已就绪：%s（Ctrl+C 退出）" % url)
    print("提示：报告本身是离线单文件，直接双击 %s 也能看。" % report.name)
    if "--no-open" not in argv:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
