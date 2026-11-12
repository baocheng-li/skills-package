#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单文件 HTML 渲染器。

产物约束（违反即视为缺陷，见 renderer/assets/report.html.j2 头部注释）
------------------------------------------------------------
1) 单文件、零外链：不出现 http(s)://、不出现 <img src>、不出现 <link href>；
2) 断网可用：图表库、主题样式、图标全部内联；
3) 无服务端：双击 file:// 打开即可运行；
4) 数据只读：页面不发起任何请求。

占位符分两类
------------
模板级（renderer/assets/report.html.j2）：STYLE / BODY / DATA_JSON / ECHARTS / SCRIPTS
分片级：
    {{THEME_CSS}}  ← renderer/theme.py 生成的设计令牌，注入 01_head.html
    {{ICON:name}}  ← renderer/icons.py 生成的 3D 图标，注入 02_body.html

分片级占位符单独处理，是为了让 renderer/assets/ 下的分片保持「结构与材质分离」：
布局文件里一行颜色都不写，改主题只动 theme.py。
"""

import io
import json
import os
import re
import shutil
import subprocess

from renderer import icons, theme
from renderer.payload import compact

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# v3.2：模板与分片归 Renderer 所有，物理上就放在 renderer/assets/ 下——
# 分层不该只在文档里成立，目录结构本身要能说明「这份文件属于哪一层」。
ASSETS = os.path.join(HERE, "assets")
PARTS = os.path.join(ASSETS, "parts")
ECHARTS = os.path.join(ASSETS, "libs", "echarts.min.js")
TPL = os.path.join(ASSETS, "report.html.j2")

JS_PARTS = [
    "p03_data.js", "p04_shell.js", "p05_cockpit.js", "p06_recon.js",
    "p07_exec.js", "p08_recycle.js", "p09_analysis.js", "p10_control.js",
    "p11_boot.js",
]

ICON_RE = re.compile(r"\{\{ICON:([a-z]+)\}\}")
TPL_KEYS = ("{{STYLE}}", "{{BODY}}", "{{DATA_JSON}}", "{{ECHARTS}}", "{{SCRIPTS}}")


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def render_icons(html, icon_size=20, brand_size=38):
    """把 {{ICON:xxx}} 换成实际 SVG。名字不在白名单里直接报错，不静默留坑。"""
    def sub(m):
        name = m.group(1)
        if name == "brand":
            return icons.brand_icon(size=brand_size)
        if name in icons.KIND_OF:
            return icons.nav_icon(name, size=icon_size)
        raise RuntimeError("未知图标占位符：{{ICON:%s}}" % name)

    out = ICON_RE.sub(sub, html)
    left = ICON_RE.findall(out)
    if left:
        raise RuntimeError("图标占位符未替换：%s" % ", ".join(left))
    return out


def check_js(js):
    """用 node --check 做语法体检。没有 node 就跳过（不阻断出报告）。"""
    if not shutil.which("node"):
        return
    tmp = os.path.join(ROOT, ".app.js")
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    finally:
        try:
            # audit-static:ignore-begin
            os.remove(tmp)                      # 清理本函数刚建的临时文件
            # audit-static:ignore-end
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError("JS 语法错误：\n%s" % (r.stderr or r.stdout))


def build(data, skin=theme.DEFAULT_SKIN, check_syntax=True):
    """把数据模型编成完整 HTML 字符串。"""
    head = read(os.path.join(PARTS, "01_head.html"))
    if "{{THEME_CSS}}" not in head:
        raise RuntimeError("01_head.html 缺少 {{THEME_CSS}} 注入点")
    head = head.replace("{{THEME_CSS}}", theme.theme_css(skin), 1)
    # 初始皮肤写在 <html data-skin>：渲染时就定好，避免打开瞬间闪一下深色再变浅色
    head = head.replace('data-skin="dark"', 'data-skin="%s"' % skin, 1)
    if 'data-skin="%s"' % skin not in head:
        raise RuntimeError("01_head.html 的 <html> 缺少 data-skin 属性")

    body = render_icons(read(os.path.join(PARTS, "02_body.html")))

    js = "\n".join(read(os.path.join(PARTS, p)) for p in JS_PARTS)
    if check_syntax:
        check_js(js)

    echarts = read(ECHARTS)
    if "</script" in echarts.lower():
        raise RuntimeError("图表库包含 </script，无法内联")

    payload = json.dumps(compact(data), ensure_ascii=False,
                         separators=(",", ":")).replace("</", "<\\/")

    if os.path.exists(TPL):
        # 模板自带 <!DOCTYPE html>，分片里的重复声明要去掉
        h = head.lstrip()
        if h[:9].upper() == "<!DOCTYPE":
            head = h.split(">", 1)[1].lstrip("\r\n")
        html = read(TPL)
        for key, val in (("{{STYLE}}", head.rstrip()),
                         ("{{BODY}}", body),
                         ("{{DATA_JSON}}", payload),
                         ("{{ECHARTS}}", echarts),
                         ("{{SCRIPTS}}", js)):
            html = html.replace(key, val, 1)
        for key in TPL_KEYS:
            if key in html:
                raise RuntimeError("模板占位符未替换：%s" % key)
    else:
        html = (head + body
                + "\n<script>window.WBC_DATA=" + payload + ";</script>\n"
                + "<script>\n" + echarts + "\n</script>\n"
                + "<script>\n" + js + "\n</script>\n"
                + "</body>\n</html>\n")
    return html


def render(out_path, data, skin=theme.DEFAULT_SKIN, check_js=True):
    """渲染并落盘，返回字节数（供审计日志记录产物体积）。"""
    html = build(data, skin=skin, check_syntax=check_js)
    d = os.path.dirname(os.path.abspath(out_path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return len(html.encode("utf-8"))
