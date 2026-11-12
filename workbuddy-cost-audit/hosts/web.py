#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web 宿主 —— 生成可托管的静态站。

和「单文件报告」的区别
--------------------
单文件报告的读者是「拿到文件的人」；静态站的读者是「点开链接的人」。
后者需要三样东西：

    index.html    报告本体（仍是零外链单文件，服务端只做分发）
    summary.json  给外部看板/监控抓的小体积快照（不含任务明细）
    theme.css     主题令牌单独一份，便于二次开发时复用色板

产物目录可以直接丢到任意静态托管（Nginx / OSS / Pages），不需要运行时。

用法
----
    python -m hosts.web --out dist/web
"""

import json
import shutil
import sys
from pathlib import Path

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
from renderer import theme as theme_r            # noqa: E402

INDEX_README = """\
# 积分对账审计应用 · Web 静态站

本目录是**纯静态**的，直接丢到任意静态托管即可（Nginx / OSS / GitHub Pages）。
不需要 Node、不需要 Python 运行时。

- `index.html`  报告本体（单文件，零外链，断网可读）
- `summary.json` 小体积快照，供外部看板轮询
- `theme.css`   设计令牌（深浅两套），二次开发时可直接引入

重新生成：`python -m hosts.web --out <本目录>`
"""


def build(out_dir, payload=None, copies=2):
    """生成静态站，返回产物清单。"""
    payload = payload or {}
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    state = state_mod.load(payload.get("balance_state"))
    store = FsStore(ROOT, out)
    model = pipeline_mod.Pipeline(WorkBuddySource(), store).run(
        PipelineOptions(last=payload.get("last"), days=int(payload.get("days") or 0)),
        state=state)
    if (model.meta or {}).get("empty"):
        raise RuntimeError("未发现可分析的日志目录")

    written = []
    skin = payload.get("skin") or "dark"
    written.append(("index.html", html_r.render(str(out / "index.html"),
                                                model.data, skin=skin)))

    # 快照：只留能被外部引用的指标，不含任务明细（体积与隐私双重考虑）
    snap = {
        "generatedAt": (model.meta or {}).get("generatedAt"),
        "windowDays": (model.meta or {}).get("windowDays"),
        "totals": model.totals(),
        "budget": {k: model.budget.get(k) for k in
                   ("initial", "todayBalance", "todaySpent", "balance",
                    "spendRatio", "level", "levelText", "exitCode")},
        "recon": (model.credit_recon or {}).get("totals"),
        "anomalies": {"hitCount": (model.anomalies or {}).get("hitCount"),
                      "totalWaste": (model.anomalies or {}).get("totalWaste")},
    }
    with open(out / "summary.json", "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    written.append(("summary.json", (out / "summary.json").stat().st_size))

    written.append(("theme.css", len(theme_r.theme_css(skin))))
    theme_r.write_css(str(out / "theme.css"), skin)

    (out / "README.md").write_text(INDEX_README, encoding="utf-8")
    written.append(("README.md", (out / "README.md").stat().st_size))

    # 深浅两套的额外皮肤文件，方便站点在 <html data-skin> 之外再挂链接复用
    for name, sk in (("theme.dark.css", "dark"), ("theme.light.css", "light")):
        theme_r.write_css(str(out / name), sk)
        written.append((name, (out / name).stat().st_size))

    return {"out": str(out), "written": written}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    out = arg("--out", str(ROOT / "dist" / "web"))
    res = build(out)
    print("Web 静态站已生成：%s" % res["out"])
    for n, s in res["written"]:
        print("  - %-20s %6.1f KB" % (n, s / 1024.0))
    print("提示：直接用浏览器打开 index.html 即可；也可以把它整个目录部署到静态托管。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
