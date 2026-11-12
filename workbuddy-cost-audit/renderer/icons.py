#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3D 图标引擎 —— 45° 等轴测 + 双层底座 + 全息投影。

规范里最硬的三条（不满足即视为不合格）
--------------------------------------
1) **不孤立**：图标必须有双层底座（底层实体盘 + 上层全息网格盘），
   主体悬浮其上，底座向外扩散能量波纹；
2) **有反差**：底层是实心磨砂盘，上层是高透网格盘，主体是实心晶体；
3) **全息感**：主体与底座之间要有垂直光柱连接，四周有悬浮几何碎片。

为什么用 SVG 而不是位图
----------------------
单文件离线报告不能有外链图片，位图要么内联成 base64（几十 KB × 5 个图标），
要么丢失清晰度。SVG 用 CSS 变量取色，深浅两套主题共用一套代码，
换主题时图标自动跟着变色，不需要出两套图。

坐标约定
--------
等轴测投影下，一个半径为 ``a`` 的圆在屏幕上压成 ``(a, a/1.732)`` 的椭圆，
这是 30° 轴测的标准比例；``a/1.732`` 出现在这里就是「45 度等轴测视角」的
数学落点，不是随手压扁的。
"""

import math

ISO = 1.0 / 1.7320508  # tan(30°)：等轴测椭圆的短半轴比例

_seq = [0]


def _uid(kind):
    _seq[0] += 1
    return "%s%d" % (kind, _seq[0])


def _f(v):
    """数值格式化：去掉多余小数，SVG 体积能省一点是一点。"""
    return ("%.2f" % v).rstrip("0").rstrip(".")


def iso_disc(cx, cy, a, fill=None, stroke=None, sw=1, dash=None, opacity=1):
    """等轴测圆盘：屏幕上就是一个椭圆。"""
    b = a * ISO
    attrs = ['cx="%s"' % _f(cx), 'cy="%s"' % _f(cy),
             'rx="%s"' % _f(a), 'ry="%s"' % _f(b)]
    if fill:
        attrs.append('fill="%s"' % fill)
    else:
        attrs.append('fill="none"')
    if stroke:
        attrs.append('stroke="%s"' % stroke)
        attrs.append('stroke-width="%s"' % _f(sw))
        if dash:
            attrs.append('stroke-dasharray="%s"' % dash)
    if opacity != 1:
        attrs.append('opacity="%s"' % _f(opacity))
    return "<ellipse %s/>" % " ".join(attrs)


def iso_box(cx, cy, a, h, top, left, right, edge=None):
    """等轴测立方体：顶面菱形 + 左右侧面。

    ``cy`` 是立方体**底面中心**在屏幕上的 y，``a`` 是半宽，``h`` 是高。
    三个面色分开传，是为了做出「左暗右亮」的实心立体反差——
    这是规范里「实心立体 vs 磨砂玻璃」对比的一半。
    """
    b = a * ISO
    ty = cy - h
    top_path = "M%s,%s L%s,%s L%s,%s L%s,%s Z" % (
        _f(cx), _f(ty - b), _f(cx + a), _f(ty), _f(cx), _f(ty + b), _f(cx - a), _f(ty))
    left_path = "M%s,%s L%s,%s L%s,%s L%s,%s Z" % (
        _f(cx - a), _f(ty), _f(cx - a), _f(cy), _f(cx), _f(cy + b), _f(cx), _f(ty + b))
    right_path = "M%s,%s L%s,%s L%s,%s L%s,%s Z" % (
        _f(cx + a), _f(ty), _f(cx + a), _f(cy), _f(cx), _f(cy + b), _f(cx), _f(ty + b))
    e = (' stroke="%s" stroke-width=".7" stroke-linejoin="round"' % edge) if edge else ""
    return ('<path d="%s" fill="%s"%s/><path d="%s" fill="%s"%s/>'
            '<path d="%s" fill="%s"%s/>' % (
                top_path, top, e, left_path, left, e, right_path, right, e))


def _arc(cx, cy, a, start_deg, end_deg, stroke, sw=1.4, opacity=0.8):
    """环绕半圈：等轴测平面上的椭圆弧（不是正圆），才贴合透视。"""
    b = a * ISO
    x1 = cx + a * math.cos(math.radians(start_deg))
    y1 = cy + b * math.sin(math.radians(start_deg))
    x2 = cx + a * math.cos(math.radians(end_deg))
    y2 = cy + b * math.sin(math.radians(end_deg))
    large = 1 if abs(end_deg - start_deg) > 180 else 0
    sweep = 1 if end_deg > start_deg else 0
    return ('<path d="M%s,%s A%s,%s 0 %d %d %s,%s" fill="none" stroke="%s" '
            'stroke-width="%s" stroke-linecap="round" opacity="%s"/>' % (
                _f(x1), _f(y1), _f(a), _f(b), large, sweep, _f(x2), _f(y2),
                stroke, _f(sw), _f(opacity)))


# ---------------------------------------------------------------------------
# 主体：五种业务语义各一个，都按等轴测画，不用扁平图标凑数
# ---------------------------------------------------------------------------
def _subject(kind, gid="g0"):
    if kind == "bars":
        # 三根高低不同的立方柱：消耗量随时间起伏
        out = []
        for i, (dx, h) in enumerate(((-8, 9), (0, 15), (8, 6))):
            out.append(iso_box(24 + dx, 26, 3.4, h,
                               "var(--info-11)", "var(--info-9)", "var(--info-10)",
                               edge="rgba(255,255,255,.18)"))
        return "".join(out)
    if kind == "stack":
        # 两块叠放的板：账页 / 分录
        out = [iso_box(24, 26, 9, 2.6, "var(--info-11)", "var(--info-9)", "var(--info-10)",
                       edge="rgba(255,255,255,.16)")]
        out.append(iso_box(24, 20, 9, 2.6, "var(--iris-11)", "var(--iris-9)",
                           "var(--iris-10)", edge="rgba(255,255,255,.16)"))
        out.append(iso_box(24, 14, 9, 2.6, "var(--ok-11)", "var(--ok-9)",
                           "var(--ok-10)", edge="rgba(255,255,255,.16)"))
        return "".join(out)
    if kind == "dial":
        # 立起来的圆盘 + 指针：监测 / 时钟
        out = ['<ellipse cx="24" cy="18" rx="9" ry="9" fill="url(#%s)" '
               'stroke="var(--info-11)" stroke-width="1.1"/>' % gid]
        out.append('<path d="M24,18 L24,11.4" stroke="var(--holo-cyan)" stroke-width="1.6" '
                   'stroke-linecap="round"/>')
        out.append('<path d="M24,18 L28.4,20.6" stroke="var(--info-11)" stroke-width="1.2" '
                   'stroke-linecap="round"/>')
        out.append('<circle cx="24" cy="18" r="1.5" fill="var(--holo-cyan)"/>')
        return "".join(out)
    if kind == "arrow":
        # 阶梯 + 上升箭头：对比与优化空间
        out = []
        for i, (dx, h) in enumerate(((-9, 5), (0, 10), (9, 16))):
            out.append(iso_box(24 + dx, 26, 3.2, h,
                               "var(--ok-11)", "var(--ok-9)", "var(--ok-10)",
                               edge="rgba(255,255,255,.16)"))
        out.append('<path d="M15,18 L27,9 L24.6,8.2 L33,6.2 L30.6,14 L28.6,11.6 L17.2,20.4 Z" '
                   'fill="var(--holo-cyan)" opacity=".92"/>')
        return "".join(out)
    # 默认：单晶体
    return iso_box(24, 26, 7, 12, "var(--info-11)", "var(--info-9)", "var(--info-10)",
                   edge="rgba(255,255,255,.18)")


LABELS = {
    "overview": "监控总览",
    "ledger": "积分对账",
    "monitor": "任务监测",
    "benchmark": "对比分析与建议",
}
KIND_OF = {
    # v3.1：导航改成 4 个一级菜单，语义按新名字注册
    "cockpit": "bars",      # 监控审计舱：柱状（读数）
    "recon": "stack",       # 对账中心：堆叠盘（账本）
    "control": "dial",      # 审计控制：仪表盘（设阈值）
    "analysis": "arrow",    # 运营分析：上行箭头（趋势）
    # 旧名保留：老版本产物、文档与测试仍可能引用，删掉会让占位符直接报错
    "overview": "bars", "ledger": "stack",
    "monitor": "dial", "benchmark": "arrow",
}


def _compact(kind, w, g):
    """小尺寸版（16–22px）：主体放大、底座下移，只留三个可读层次。

    18–20px 下如果按 48 网格等比缩小，主体只剩 8px 高，缩略图上就是一团色块。
    这里刻意把「主体」占满 y=3…31（26 单位），底座压到 y=33…41，
    相当于牺牲一点透视比例换取可辨识度——图标设计里这叫尺寸降级，
    不是把大图等比缩小。
    """
    parts = ['<defs>'
             '<radialGradient id="%s" cx="50%%" cy="50%%" r="50%%">'
             '<stop offset="0" stop-color="var(--holo-cyan)" stop-opacity=".40"/>'
             '<stop offset="1" stop-color="var(--holo-cyan)" stop-opacity="0"/>'
             '</radialGradient></defs>' % w]

    # 底盘：实心（对比上层的高透环）
    parts.append(iso_disc(24, 38.6, 13.4, fill="url(#%s)" % w))
    parts.append(iso_disc(24, 38.6, 13.4, stroke="var(--info-11)", sw=1.8, opacity=0.8))
    # 上层发光环：唯一的高饱和青色，作为「全息」的记忆点
    parts.append(iso_disc(24, 32.6, 9.6, fill="rgba(0,255,255,.12)"))
    parts.append(iso_disc(24, 32.6, 9.6, stroke="var(--holo-cyan)", sw=2, opacity=0.95))

    E = "rgba(255,255,255,.35)"          # 棱边：小尺寸下靠它托出体积
    if kind == "bars":
        # 三根柱：高低差拉到 2 倍，宽度给到 4.2（缩放后 ≈1.8px，仍能分辨）
        for dx, h, top, lf, rt in ((-7.4, 8, "var(--info-12)", "var(--info-9)", "var(--info-10)"),
                                   (0, 17, "var(--info-12)", "var(--info-9)", "var(--info-10)"),
                                   (7.4, 12, "var(--info-12)", "var(--info-9)", "var(--info-10)")):
            parts.append(iso_box(24 + dx, 30, 4.2, h, top, lf, rt, edge=E))
    elif kind == "stack":
        # 两块板：一大一小、一冷一暖，叠出「账页」的层次
        parts.append(iso_box(24, 30, 10, 4.4, "var(--info-12)", "var(--info-9)",
                             "var(--info-10)", edge=E))
        parts.append(iso_box(24, 21, 10, 4.4, "var(--ok-12)", "var(--ok-9)",
                             "var(--ok-10)", edge=E))
        parts.append('<ellipse cx="24" cy="15.4" rx="10" ry="5.8" fill="none" '
                     'stroke="var(--holo-cyan)" stroke-width="1.7" opacity=".9"/>')
    elif kind == "dial":
        parts.append('<circle cx="24" cy="18" r="11" fill="rgba(0,190,255,.16)" '
                     'stroke="var(--info-11)" stroke-width="2.2"/>')
        parts.append('<path d="M24,18 L24,9.6" stroke="var(--holo-cyan)" stroke-width="2.6" '
                     'stroke-linecap="round"/>')
        parts.append('<path d="M24,18 L30.4,21.4" stroke="var(--info-11)" stroke-width="2" '
                     'stroke-linecap="round"/>')
        parts.append('<circle cx="24" cy="18" r="2.2" fill="var(--holo-cyan)"/>')
    elif kind == "arrow":
        # 阶梯 + 箭头：三档台阶，箭头压在最上一层
        for dx, h in ((-8.4, 7), (-0.4, 12), (7.6, 17)):
            parts.append(iso_box(24 + dx, 30, 4.4, h, "var(--ok-12)", "var(--ok-9)",
                                 "var(--ok-10)", edge=E))
        parts.append('<path d="M15,7.6 L26,7.6 L26,3.4 L36,11.4 L26,19.4 L26,15.2 L15,15.2 Z" '
                     'fill="var(--holo-cyan)" opacity=".95"/>')
    else:
        parts.append(iso_box(24, 30, 9, 15, "var(--info-12)", "var(--info-9)",
                             "var(--info-10)", edge=E))
    return "".join(parts)


def icon(kind="cube", size=34, detail=True, cls="ico3d"):
    """生成一个 3D 图标。

    :param kind:   bars / stack / dial / arrow / cube
    :param size:   显示边长（px）；内部统一按 48×48 坐标绘制后缩放
    :param detail: 是否画碎片、波纹、粒子（小尺寸下关掉，否则糊成一团）
    """
    u = _uid(kind)
    g, w = "g" + u, "w" + u

    if not detail:
        return ('<svg class="%s" width="%d" height="%d" viewBox="0 0 48 48" fill="none" '
                'aria-hidden="true">%s</svg>' % (cls, size, size, _compact(kind, w, g)))

    defs = (
        '<defs>'
        '<linearGradient id="%s" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="rgba(255,255,255,.30)"/>'
        '<stop offset="1" stop-color="rgba(0,190,255,.10)"/>'
        '</linearGradient>'
        '<radialGradient id="%s" cx="50%%" cy="50%%" r="50%%">'
        '<stop offset="0" stop-color="var(--holo-cyan)" stop-opacity=".30"/>'
        '<stop offset="1" stop-color="var(--holo-cyan)" stop-opacity="0"/>'
        '</radialGradient>'
        '</defs>' % (g, w)
    )

    parts = [defs]

    # ---- 底座：外扩散的能量波纹（最底、最淡）----
    parts.append(iso_disc(24, 37, 19, stroke="var(--holo-cyan)", sw=0.7, opacity=0.16))
    parts.append(iso_disc(24, 36.2, 15.6, stroke="var(--holo-cyan)", sw=0.8, opacity=0.24))

    # ---- 底座第一层：实体磨砂盘（实心 vs 玻璃的「实心」一半）----
    parts.append(iso_disc(24, 34, 13.2, fill="url(#%s)" % w))
    parts.append(iso_disc(24, 34, 13.2, stroke="var(--glass-hi)", sw=0.9, opacity=0.55))
    parts.append(iso_disc(24, 34, 13.2, stroke="var(--info-11)", sw=0.9, opacity=0.5,
                          dash="3 3.2"))

    # ---- 底座第二层：全息网格盘（高透 + 青色发光环）----
    parts.append(iso_disc(24, 30.4, 9.4, fill="rgba(0,255,255,.055)"))
    parts.append(iso_disc(24, 30.4, 9.4, stroke="var(--holo-cyan)", sw=0.8, opacity=0.85))
    parts.append(iso_disc(24, 30.4, 6.2, stroke="var(--holo-cyan)", sw=0.6, opacity=0.45,
                          dash="1.6 2.4"))

    # ---- 垂直光柱：主体与底座之间的连接（「悬浮」不能悬空）----
    parts.append('<path d="M22.3,15 L25.7,15 L28.6,30 L19.4,30 Z" fill="url(#%s)" '
                 'opacity=".55"/>' % w)

    # ---- 主体 ----
    parts.append(_subject(kind, g))

    if detail:
        # ---- 环绕半圈：一圈半透明的环，穿过主体背后 ----
        parts.append(_arc(24, 31, 16.4, 168, 342, "var(--holo-cyan)", 1.1, 0.5))
        parts.append(_arc(24, 31, 16.4, 192, 348, "var(--iris-11)", 1.0, 0.42))
        # ---- 悬浮几何碎片：三片，大小不一，制造「凝固的动态」----
        parts.append('<path d="M8.5,20.5 l3.1,1.1 -1.4,2.6 -2.6,-1.2 Z" '
                     'fill="var(--holo-cyan)" opacity=".42"/>')
        parts.append('<path d="M38.6,17.4 l2.6,1.5 -0.9,2.4 -2.4,-1.3 Z" '
                     'fill="var(--iris-11)" opacity=".5"/>')
        parts.append('<path d="M34.2,27.5 l2.1,1 -0.7,1.9 -1.9,-1 Z" '
                     'fill="var(--info-11)" opacity=".38"/>')
        # ---- 粒子 ----
        parts.append('<circle cx="13.4" cy="27.6" r="1" fill="var(--holo-cyan)" opacity=".7"/>')
        parts.append('<circle cx="35.8" cy="22.4" r=".85" fill="var(--info-12)" opacity=".6"/>')
        parts.append('<circle cx="17.2" cy="14.8" r=".7" fill="var(--iris-11)" opacity=".6"/>')

    return ('<svg class="%s" width="%d" height="%d" viewBox="0 0 48 48" fill="none" '
            'aria-hidden="true">%s</svg>' % (cls, size, size, "".join(parts)))


def brand_icon(size=34):
    """侧栏品牌位：完整版图标（双层底座 + 光柱 + 环绕 + 碎片）。"""
    return icon("cube", size=size, detail=True, cls="brand-ico")


def nav_icon(view, size=20):
    """导航项图标：降级版（20px 下仍有三个可读层次）。"""
    return icon(KIND_OF.get(view, "cube"), size=size, detail=False, cls="ico3d")


def gallery(size=44):
    """四个导航语义的图标陈列（用于文档 / 设计规范自检页）。"""
    return {k: icon(KIND_OF[k], size=size, detail=True) for k in KIND_OF}


if __name__ == "__main__":
    import sys
    print(icon(sys.argv[1] if len(sys.argv) > 1 else "cube", size=48))
