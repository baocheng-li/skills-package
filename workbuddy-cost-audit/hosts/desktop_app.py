#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面版宿主（原生窗口）—— Windows 安装包 `CostAudit.exe` 的入口。

为什么是「小窗口 + 本地服务 + 系统浏览器」
------------------------------------------
要的是一个**双击就能用**的东西，不是再造一个 Chromium。三种做法对比过：

* 嵌 WebView（pywebview / CEF）：渲染质量最好，但要么带 150 MB 运行时，
  要么要求目标机装 WebView2 运行时——「一键安装」就变成「先装运行时」。
* 自绘界面（tkinter 直接画表格）：报告有十个页面、几十张图表，重画一遍
  等于把渲染层再写一份，两边必然走形。
* **本地只读服务 + 系统浏览器**：代码零增量，报告与 Web / Skill 形态完全同一份。

本文件就是第三种：一个控制窗口（采集、打开、退出）+ 一个只绑定 127.0.0.1
的静态服务。关掉窗口＝服务停止，不留后台进程。

数据存哪
--------
``adapters.local_store.LocalStore``：SQLite 单文件，默认落在
``%LOCALAPPDATA%\\CostAudit\\data\\cost-audit.db``；产物落同级 ``out\\``。
可用 ``COST_AUDIT_HOME`` 重定向（便携模式）。

用法
----
    python -m hosts.desktop_app                 # 开窗口
    python -m hosts.desktop_app --console       # 无窗口（远程/无图形环境）
    python -m hosts.desktop_app --no-open       # 不自动开浏览器
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.local_store import LocalStore       # noqa: E402
from core import policy                            # noqa: E402
from hosts import desktop as desktop_host          # noqa: E402

TITLE = "积分对账审计应用"
ACCENT = "#0a84ff"
BG = "#0b0f16"
CARD = "#141b26"
FG = "#e8eef8"
DIM = "#8b9ab1"


# ---------------------------------------------------------------------------
# 采集 + 服务（与窗口无关，控制台模式也走这里）
# ---------------------------------------------------------------------------
class Session(object):
    """一次运行期的状态：库、产物目录、HTTP 服务。"""

    def __init__(self, home=None):
        self.store = LocalStore(seed_root=ROOT, home=home)
        self.out = self.store.out
        self.httpd = None
        self.url = ""
        self.last = None                  # (report_path, summary)
        self.error = ""

    def collect(self, on_step=None):
        """跑一次采集并生成报告。返回 summary；异常写进 self.error。"""
        if on_step:
            on_step("正在读取本机日志…")
        try:
            # 余额状态优先取库里的那一份；库里没有才用出厂默认
            st = self.store.read_json("config/balance-state.json", None) \
                or policy.default_state()
            report, summary = desktop_host.build_report(
                self.out, store=self.store, state=st)
            self.last = (report, summary)
            self.error = ""
            b = summary.get("budget") or {}
            self.store.record_run(level=b.get("level") or "",
                                  window_days=summary.get("windowDays") or 0,
                                  spent=b.get("todaySpent") or 0,
                                  balance=b.get("balance"),
                                  tasks=(summary.get("totals") or {}).get("tasks") or 0)
            return summary
        except Exception as exc:                       # noqa: BLE001
            self.error = str(exc)
            self.last = None
            return None

    def serve(self, port=0):
        if self.httpd:
            return self.url
        self.httpd = desktop_host.make_server(self.out, port)
        self.url = "http://127.0.0.1:%d/" % self.httpd.server_address[1]
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        return self.url

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:                          # noqa: BLE001
                pass
            self.httpd = None
        self.store.close()


# ---------------------------------------------------------------------------
# 控制窗口
# ---------------------------------------------------------------------------
def run_window(session, open_browser=True, port=0):
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg=BG)
    root.geometry("480x430")
    root.minsize(460, 410)
    try:
        root.iconbitmap(default="")                    # 无图标也不报错
    except Exception:                                  # noqa: BLE001
        pass

    tk.Label(root, text=TITLE, bg=BG, fg=FG, font=("Microsoft YaHei UI", 15, "bold"),
             anchor="w").pack(fill="x", padx=20, pady=(18, 2))
    sub = tk.Label(root, text="本机只读 · 离线 · 数据不出本机", bg=BG, fg=DIM,
                   font=("Microsoft YaHei UI", 9), anchor="w")
    sub.pack(fill="x", padx=20)

    status = tk.Label(root, text="准备中…", bg=BG, fg=FG,
                      font=("Microsoft YaHei UI", 10), anchor="w", justify="left",
                      wraplength=410)
    status.pack(fill="x", padx=20, pady=(14, 4))

    meta = tk.Label(root, text="", bg=BG, fg=DIM, font=("Consolas", 8),
                    anchor="w", justify="left", wraplength=430)
    meta.pack(fill="x", padx=20)

    # ---- 两个人工输入：余额只在客户端界面里，本机没有可信副本，只能人填 ----
    cfg = tk.Frame(root, bg=CARD)
    cfg.pack(fill="x", padx=20, pady=(10, 0))
    tk.Label(cfg, text="余额设置（只会用在本机算账，不上传）", bg=CARD, fg=DIM,
             font=("Microsoft YaHei UI", 8), anchor="w").grid(
        row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 4))

    st0 = session.store.read_json("config/balance-state.json", None) or {}
    initial_var = tk.StringVar(
        value="" if st0.get("initial_credits") is None else ("%g" % st0["initial_credits"]))
    balance_var = tk.StringVar(
        value="" if st0.get("today_balance") is None else ("%g" % st0["today_balance"]))

    def field(row, col, label, var):
        tk.Label(cfg, text=label, bg=CARD, fg=FG, font=("Microsoft YaHei UI", 9)).grid(
            row=row, column=col * 2, sticky="w", padx=(12 if col == 0 else 6, 4), pady=4)
        e = tk.Entry(cfg, textvariable=var, width=12, bg="#0d131c", fg=FG,
                     insertbackground=FG, relief="flat", font=("Consolas", 9))
        e.grid(row=row, column=col * 2 + 1, sticky="w", ipady=3)
        return e

    field(1, 0, "初始积分", initial_var)
    field(1, 1, "今日余额", balance_var)

    def save_balance():
        def num(v):
            v = (v or "").strip()
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                raise ValueError("请填数字：%s" % v)
        try:
            ini, bal = num(initial_var.get()), num(balance_var.get())
        except ValueError as exc:
            messagebox.showwarning(TITLE, str(exc))
            return
        if ini is None and bal is None:
            messagebox.showinfo(TITLE, "两个数都没填，无需保存。")
            return
        st = session.store.read_json("config/balance-state.json", None) \
            or policy.default_state()
        st = policy.apply_manual(st, initial=ini, today_balance=bal,
                                 balance_date=None, initial_date=None)
        st["updated_at"] = policy.now()
        session.store.write_json("config/balance-state.json", st)
        status.config(text="余额已保存，正在重算…", fg=FG)
        start()

    tk.Button(cfg, text="保存并重算", command=save_balance, bg="#1b2433", fg=FG,
              relief="flat", cursor="hand2", font=("Microsoft YaHei UI", 9)).grid(
        row=2, column=0, columnspan=4, sticky="e", padx=12, pady=(2, 10))

    bar = tk.Frame(root, bg=BG)
    bar.pack(side="bottom", fill="x", padx=18, pady=16)

    def open_report():
        if session.url:
            webbrowser.open(session.url)
        elif session.last:
            webbrowser.open(session.last[0].as_uri())

    def open_data():
        d = str(session.store.home)
        if sys.platform.startswith("win"):
            os.startfile(d)                            # noqa: S606
        else:
            webbrowser.open(Path(d).as_uri())

    b1 = tk.Button(bar, text="打开报告", command=open_report, width=10,
                   bg=ACCENT, fg="white", relief="flat",
                   font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2")
    b1.pack(side="left")
    b2 = tk.Button(bar, text="重新采集", command=lambda: start(), width=10,
                   bg="#1b2433", fg=FG, relief="flat", cursor="hand2",
                   font=("Microsoft YaHei UI", 9))
    b2.pack(side="left", padx=8)
    b3 = tk.Button(bar, text="数据目录", command=open_data, width=10,
                   bg="#1b2433", fg=FG, relief="flat", cursor="hand2",
                   font=("Microsoft YaHei UI", 9))
    b3.pack(side="left")
    b4 = tk.Button(bar, text="退出", command=lambda: (session.stop(), root.destroy()),
                   width=8, bg="#1b2433", fg=DIM, relief="flat", cursor="hand2",
                   font=("Microsoft YaHei UI", 9))
    b4.pack(side="right")

    def step(msg):
        root.after(0, lambda: status.config(text=msg))

    def done(summary):
        if not summary:
            status.config(text="采集失败：%s" % (session.error or "未知原因"),
                          fg="#ff6b6b")
            messagebox.showwarning(TITLE, "采集失败：\n%s\n\n请确认本机有 WorkBuddy / "
                                          "CodeBuddy 的日志目录。" % (session.error or ""))
            return
        b = summary.get("budget") or {}
        status.config(text="已就绪 · 告警 %s · 当前余额 %s"
                      % (b.get("levelText") or "未配置",
                         b.get("balance") if b.get("balance") is not None else "—"),
                      fg=FG)
        info = session.store.info()
        meta.config(text="%s\n数据库 %.0f KB · %d 项配置 · %d 条收入"
                    % (info["home"], info["bytes"] / 1024.0, info["kv"], info["income"]))
        if open_browser and session.url:
            webbrowser.open(session.url)

    def start():
        b2.config(state="disabled")

        def work():
            step("正在采集…")
            summary = session.collect(on_step=step)
            if summary and not session.httpd:
                step("正在启动本地服务…")
                session.serve(port)
            root.after(0, lambda: (b2.config(state="normal"), done(summary)))
        threading.Thread(target=work, daemon=True).start()

    root.after(120, start)
    root.mainloop()


# ---------------------------------------------------------------------------
# 无窗口模式
# ---------------------------------------------------------------------------
def run_console(session, open_browser=True, port=0):
    print("%s · 桌面版（控制台模式）" % TITLE, flush=True)
    summary = session.collect(on_step=lambda m: print(m, flush=True))
    if not summary:
        print("采集失败：%s" % session.error, file=sys.stderr)
        return 1
    url = session.serve(port)
    print("报告：%s" % session.last[0], flush=True)
    print("服务：%s（Ctrl+C 退出）" % url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        session.stop()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Windows 控制台默认 GBK，中文会变乱码；控制台模式要先把输出编成 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    home = arg("--home")
    port = int(arg("--port", "0"))
    console = "--console" in argv
    open_browser = "--no-open" not in argv

    session = Session(home=home)
    if console or not sys.stdout.isatty() and "--console" in argv:
        return run_console(session, open_browser, port)
    try:
        run_window(session, open_browser, port)
        session.stop()
        return 0
    except ImportError:                     # 没有 tkinter 的极简环境
        return run_console(session, open_browser, port)


if __name__ == "__main__":
    sys.exit(main())
