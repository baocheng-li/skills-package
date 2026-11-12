#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设计令牌层 —— 把「全息前沿科技 B端 UI 视觉规范」落成可执行的 CSS。

为什么单独一层
--------------
同一套令牌要喂给三种渲染器（单文件 HTML、桌面版、Web 静态站），
如果色板写在各处的 CSS 里，改一次主题就要改三遍，且必然漂移。
这里把色板收成 Python 数据结构，由 :func:`theme_css` 一次输出，
布局层（assets/parts/01_head.html）只负责结构与间距，不持有颜色。

两套主题
--------
``dark``  —— 深空蓝黑 #060B19 + 量子蓝 #0055FF + 霓虹青 #00FFFF
``light`` —— 极浅冷灰蓝 #F0F4F8 + 冰蓝 #00D2FF + 全息紫 #9D4EDD

两个必须知道的约束
------------------
1) **自定义属性里不写 var()**。``--line2:rgba(var(--ink),.16)`` 这种写法在普通
   属性里能用，但 ``getComputedStyle`` 对自定义属性返回的是原始 token 文本，
   不做 var 替换——ECharts 拿到的就是字面量 ``rgba(var(--ink),.16)``，
   canvas 解析失败后静默画成黑色。所以这里的令牌一律展开成最终字面值。
2) **颜色以「分量 + 透明度」为主**。布局层有 110 处 ``rgba(148,180,220,.xx)``
   的半透明描边/底纹，整体切主题时靠 ``--ink:140,176,235`` 一个分量变量
   就能全量翻转，不必逐条改写。
"""

import io
import os

# ============================================================================
# 色板
# ============================================================================
# Radix Colors 的 9/10/11/12 梯度被沿用：9=实色填充、10=悬停、11=深底正文、
# 12=高对比文字。浅色主题取 Radix light 的对应档位，避免自己在白底上把
# 红/绿调到看不清。
DARK = {
    # ---- 底色与面板 ----
    "bg": "#060b19",
    "bg2": "#070e20",
    "panel": "rgba(14,24,48,.60)",
    "panel2": "rgba(18,30,60,.76)",
    "panel3": "#101a36",
    "n-1": "#060b19",
    "n-2": "#0a1327",
    "n-3": "#0e1830",
    "n-4": "#131f3a",
    "n-6": "#243354",
    "n-8": "#3d5178",
    "n-11": "#93a8cc",
    "n-12": "#e9f1ff",
    # ---- 文字 ----
    "text": "#e9f1ff",
    "text2": "#93a8cc",
    "text3": "#5e739a",
    # ---- 状态色 ----
    "bad-9": "#e5484d", "bad-10": "#ec5d5e", "bad-11": "#ff9592", "bad-12": "#ffd1d9",
    "warn-9": "#ffc53d", "warn-10": "#ffd60a", "warn-11": "#ffca16", "warn-12": "#ffe7b3",
    "ok-9": "#30a46c", "ok-10": "#33b074", "ok-11": "#3dd68c", "ok-12": "#b1f1cb",
    "info-9": "#0090ff", "info-10": "#3b9eff", "info-11": "#70b8ff", "info-12": "#c2e6ff",
    "iris-9": "#5b5bd6", "iris-10": "#6e6ade", "iris-11": "#b1a9ff", "iris-12": "#e0dffe",
    # ---- 语义别名 ----
    "warn-soft": "#ffc470",
    "pink": "#ff7ab8",
    "pri-deep": "#1b3fd0",
    "warn-deep": "#8a5a1c",
    "bad-deep": "#7a2328",
    # ---- 半透明面：写死字面值，供 canvas 直接解析 ----
    "line": "rgba(140,176,235,.10)",
    "line2": "rgba(140,176,235,.18)",
    "line3": "rgba(140,176,235,.28)",
    "hover": "rgba(140,176,235,.06)",
    "ink": "140,176,235",
    "pri": "0,110,255",
    "iris": "177,169,255",
    "cyan-ink": "#66efd8",
    # ---- 全息材质 ----
    "holo-cyan": "#00ffff",
    "holo-violet": "#9d4edd",
    "holo-blue": "#0055ff",
    "holo-edge": "rgba(0,255,255,.55)",
    "glass-1": "rgba(24,41,78,.76)",
    "glass-2": "rgba(11,20,44,.60)",
    "glass-line": "rgba(150,186,255,.20)",
    "glass-hi": "rgba(255,255,255,.16)",
    "glass-shadow": "0 12px 34px rgba(0,0,0,.55),0 0 0 1px rgba(120,170,255,.04),"
                    "0 1px 0 rgba(255,255,255,.06) inset",
    "grid-line": "rgba(140,176,235,.055)",
    "glow": "rgba(0,170,255,.55)",
    "glow-soft": "rgba(0,170,255,.13)",
    "scan": "rgba(0,255,255,.55)",
    "shadow": "0 10px 34px rgba(0,0,0,.55)",
    "tip-bg": "rgba(8,16,34,.95)",
    "tip-line": "rgba(140,176,235,.22)",
    "mask": "rgba(3,7,16,.68)",
}

LIGHT = {
    "bg": "#f0f4f8",
    "bg2": "#e8eff7",
    "panel": "rgba(255,255,255,.64)",
    "panel2": "rgba(255,255,255,.84)",
    "panel3": "#ffffff",
    "n-1": "#f0f4f8",
    "n-2": "#eaf1f8",
    "n-3": "#ffffff",
    "n-4": "#f5f9fc",
    "n-6": "#d5e0ec",
    "n-8": "#a9bacd",
    "n-11": "#51678a",
    "n-12": "#2b3a55",
    "text": "#2b3a55",
    "text2": "#51678a",
    "text3": "#7b8da9",
    "bad-9": "#d93d42", "bad-10": "#c62a2f", "bad-11": "#c22a2f", "bad-12": "#641723",
    "warn-9": "#e09200", "warn-10": "#c07c00", "warn-11": "#ab6400", "warn-12": "#4f3422",
    "ok-9": "#169a63", "ok-10": "#12855a", "ok-11": "#218358", "ok-12": "#193b2d",
    "info-9": "#00a8d6", "info-10": "#0092bd", "info-11": "#0d74ce", "info-12": "#113264",
    "iris-9": "#6d4bd8", "iris-10": "#5b3fc4", "iris-11": "#5753c6", "iris-12": "#272962",
    "warn-soft": "#a86800",
    "pink": "#c02f7c",
    "pri-deep": "#8fd8e8",
    "warn-deep": "#e8c07a",
    "bad-deep": "#e9a3a6",
    "line": "rgba(43,58,85,.10)",
    "line2": "rgba(43,58,85,.16)",
    "line3": "rgba(43,58,85,.24)",
    "hover": "rgba(43,58,85,.05)",
    "ink": "43,58,85",
    "pri": "0,168,214",
    "iris": "87,83,198",
    "cyan-ink": "#0e7490",
    "holo-cyan": "#00d2ff",
    "holo-violet": "#9d4edd",
    "holo-blue": "#00a8d6",
    "holo-edge": "rgba(0,168,214,.34)",
    "glass-1": "rgba(255,255,255,.90)",
    "glass-2": "rgba(246,250,255,.78)",
    "glass-line": "rgba(150,182,220,.42)",
    "glass-hi": "rgba(255,255,255,1)",
    "glass-shadow": "0 8px 24px rgba(56,86,120,.13),0 1px 0 rgba(255,255,255,1) inset",
    "grid-line": "rgba(43,58,85,.075)",
    "glow": "rgba(0,168,214,.42)",
    "glow-soft": "rgba(0,168,214,.10)",
    "scan": "rgba(0,210,255,.75)",
    "shadow": "0 12px 30px rgba(56,86,120,.14)",
    "tip-bg": "rgba(255,255,255,.97)",
    "tip-line": "rgba(43,58,85,.16)",
    "mask": "rgba(206,220,235,.62)",
}

SKINS = {"dark": DARK, "light": LIGHT}
DEFAULT_SKIN = "dark"


# ============================================================================
# 结构令牌（两套主题共用，不随皮肤变化）
# ============================================================================
STRUCT = {
    # Carbon spacing $spacing-01…09 = 2/4/8/12/16/24/32/40/48
    "sp-01": "2px", "sp-02": "4px", "sp-03": "8px", "sp-04": "12px",
    "sp-05": "16px", "sp-06": "24px", "sp-07": "32px", "sp-08": "40px", "sp-09": "48px",
    "pad-card": "14px", "gap-card": "12px", "gap-row": "8px",
    "r-1": "4px", "r-2": "8px", "r-3": "12px", "r-pill": "999px",
    "fs-label": "10px", "fs-aux": "11px", "fs-body": "12.5px", "fs-num": "13px",
    "fs-h3": "14px", "fs-h2": "16px", "fs-h1": "20px",
}


def vars_block(palette, indent="  "):
    """把色板渲染成一串自定义属性声明。"""
    out = []
    for k, v in palette.items():
        out.append("%s--%s:%s;" % (indent, k, v))
    return "\n".join(out)


def _struct_block():
    out = []
    for k, v in STRUCT.items():
        out.append("  --%s:%s;" % (k, v))
    # 语义别名：间接引用别的自定义属性是安全的（引用方是普通属性）
    out.append("  --accent:var(--info-9); --accent2:var(--ok-11);")
    out.append("  --warn:var(--warn-9); --danger:var(--bad-9); --ok:var(--ok-10);")
    out.append("  --purple:var(--iris-11);")
    out.append("  --radius:var(--r-3); --radius-sm:var(--r-2);")
    out.append('  --mono:"SF Mono","JetBrains Mono",Consolas,Monaco,monospace;')
    return "\n".join(out)


# ============================================================================
# 全息材质基础类
# ============================================================================
# 说明：这里只放「材质原语」，具体组件（.card / .kpi）在布局层引用这些类或变量。
# 之所以不把 .card 直接写死在这里，是因为卡片在不同页签里尺寸不同，
# 尺寸属于布局层，材质属于令牌层——分层守住了，改一处才不会牵动另一处。
MATERIAL_CSS = """
/* 全息网格底：极微弱的等距网格 + 两团光斑，40px 栅格与 Carbon 的 8 倍数节奏同源。
   mask 让网格在视口边缘淡出，避免四条硬边把「深空」切成一个方框。 */
.holo-grid{position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:
    linear-gradient(var(--grid-line) 1px,transparent 1px),
    linear-gradient(90deg,var(--grid-line) 1px,transparent 1px);
  background-size:40px 40px,40px 40px;
  -webkit-mask-image:radial-gradient(120% 90% at 50% 0%,#000 30%,transparent 82%);
  mask-image:radial-gradient(120% 90% at 50% 0%,#000 30%,transparent 82%);
}
.holo-aura{position:fixed;inset:0;pointer-events:none;z-index:0;
  background:
    radial-gradient(920px 520px at 10% -10%,var(--glow-soft),transparent 62%),
    radial-gradient(780px 440px at 92% 110%,rgba(157,78,221,.10),transparent 64%);
}
[data-skin="light"] .holo-aura{
  background:
    radial-gradient(920px 520px at 10% -10%,rgba(0,210,255,.14),transparent 62%),
    radial-gradient(780px 440px at 92% 110%,rgba(157,78,221,.10),transparent 64%);
}

/* 全息扫描带：一条极淡的横向光带自顶向下缓慢掠过，8s 一轮。
   opacity 压到 .5 以内——扫描线是「有全息感」而不是「有东西在闪」。 */
.holo-scan{position:fixed;left:0;right:0;top:0;height:180px;pointer-events:none;
  z-index:2;opacity:.5;
  background:linear-gradient(180deg,transparent,var(--glow-soft) 62%,transparent);
  animation:holoSweep 9s linear infinite}
@keyframes holoSweep{
  0%{transform:translateY(-190px)}
  55%,100%{transform:translateY(104vh)}
}

/* 磨砂玻璃：1px 全息描边用 mask-composite 抠出来，比叠 border 更薄且能渐变。
   注意 iOS/Safari 需要 -webkit- 前缀，两个都写。 */
.glass{
  position:relative;
  background:linear-gradient(158deg,var(--glass-1),var(--glass-2));
  -webkit-backdrop-filter:blur(16px) saturate(1.4);
  backdrop-filter:blur(16px) saturate(1.4);
  border:1px solid var(--glass-line);
  box-shadow:var(--glass-shadow);
}
.glass::before{
  content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  padding:1px;
  background:linear-gradient(140deg,var(--glass-hi),transparent 34%,transparent 66%,var(--holo-cyan));
  opacity:.5;
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;
  mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  mask-composite:exclude;
}
/* 呼吸光晕：只给「当前值」用，全页不超过 3 处，多了就变成迪厅 */
.glow-pulse{animation:glowPulse 3.2s ease-in-out infinite}
@keyframes glowPulse{
  0%,100%{filter:drop-shadow(0 0 2px var(--glow))}
  50%{filter:drop-shadow(0 0 7px var(--glow))}
}
"""


def theme_css(skin=DEFAULT_SKIN):
    """输出完整的主题样式表：两套皮肤 + 材质原语。

    两套都输出（而不是只输出当前皮肤）是为了让页面能**运行时切换**，
    而不必重新生成文件——单文件报告双击打开后仍可切主题。
    """
    pal = SKINS.get(skin) or DARK
    other = "light" if skin == "dark" else "dark"
    return (
        "/* ===== 设计令牌 · 由 renderer/theme.py 生成，勿手改 ===== */\n"
        ":root{\n"
        + vars_block(pal)
        + "\n"
        + _struct_block()
        + "\n}\n"
        + '[data-skin="%s"]{\n' % other
        + vars_block(SKINS[other])
        + "\n}\n"
        + MATERIAL_CSS
    )


def css_vars(skin=DEFAULT_SKIN):
    """只取色板（给需要具体色值的调用方，例如 Python 侧出图/出文档）。"""
    return dict(SKINS.get(skin) or DARK)


def echarts_palette(skin=DEFAULT_SKIN):
    """图表调色板：与页面状态色同源，顺序保证相邻系列可区分。"""
    p = SKINS.get(skin) or DARK
    return [p["info-9"], p["ok-9"], p["warn-9"], p["iris-11"],
            p["bad-9"], p["holo-cyan"].upper(), p["info-11"], p["pink"]]


def write_css(path, skin=DEFAULT_SKIN):
    """落盘一份主题样式（供桌面版/Web 静态站外链复用）。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(theme_css(skin))
    return path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "out/theme.css"
    write_css(out)
    print("已生成 %s（%d 字节）" % (out, os.path.getsize(out)))
