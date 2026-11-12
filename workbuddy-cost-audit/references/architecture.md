# 三层解耦架构（Core + Adapter + Renderer）

> 目标：同一套业务逻辑，四种发布形态（Skill / 桌面版 / 插件 / Web）一键产出，
> 换平台只写 Adapter、换产物只写 Renderer、换壳只写 Host。

---

## 1. 一张图

```
                    ┌──────────────────────────────┐
                    │  hosts/   四种壳（唯一知道     │
                    │  cli skill desktop plugin web │
                    │  「外面是谁」）               │
                    └───────────┬──────────────────┘
                                │ 调用
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
┌───────────────┐      ┌────────────────┐      ┌──────────────────┐
│  core/        │      │  adapters/     │      │  renderer/       │
│  领域内核      │◀─────│  数据与存储     │      │  产物            │
│               │ 实现  │                │      │                  │
│  ports        │      │  workbuddy/    │      │  theme  icons    │
│  models       │      │  discover      │      │  payload html    │
│  policy       │      │  parse_logs    │      │  data  docs      │
│  pipeline     │      │  sandbox       │      │                  │
│  calc/*       │      │  store  state  │      │                  │
└───────────────┘      └────────────────┘      └──────────────────┘
        ▲                                              ▲
        └────────────── 只吃 dict，不反向 import ───────┘
```

依赖方向**只有一条**：`hosts → {core, adapters, renderer}`，`adapters → core`。
`core` 不 import 任何外层；`renderer` 不 import `core`（它只吃 `model.data` 这个 dict）。

由 TC-32 静态扫描全仓 import 守住这条线，破了就红。

---

## 2. 四层的契约

### 2.1 Core（`core/`）

| 文件 | 职责 | 不做什么 |
|------|------|----------|
| `ports.py` | 四个 Protocol：LogSource / Store / Clock / Notifier | 不提供实现 |
| `models.py` | `PipelineOptions`（输入）、`ReportModel`（输出） | 内部细节继续用 dict |
| `policy.py` | 阈值、告警级别、凭证状态的**判定** | 不读文件 |
| `pipeline.py` | **唯一编排**：采集→脱敏→分类→过滤→分析 | 不打印、不写盘、不定退出码 |
| `calc/*` | 12 个纯计算模块 | 不做 IO |

**唯一豁免**：`policy.load()` 惰性转发到 `adapters.state.load()`，用于兼容
`bs.load()` 这类旧调用点。它写在函数体内、只此一处，TC-32 用白名单显式放行。

### 2.2 Adapter（`adapters/`）

| 文件 | 适配什么 |
|------|----------|
| `workbuddy/` | WorkBuddy / CodeBuddy 日志、sqlite、会话 jsonl → 统一数据模型 |
| `workbuddy/source.py` | 把上面那套装成 `LogSource` |
| `discover.py` | 目录发现与白名单校验（只有它知道 `~/.workbuddy` 长什么样） |
| `parse_logs.py` | 采集调度（按工具名选适配器） |
| `sandbox.py` | 只读白名单、敏感路径拦截、输出体积上限 |
| `store.py` | `FsStore` / `ReadOnlyStore`（实现 `Store`） |
| `state.py` | 余额状态文件的读/原子写/合并 |

### 2.3 Renderer（`renderer/`）

| 文件 | 产物 |
|------|------|
| `theme.py` | 设计令牌：Holo Dark / Holo Light 两套 + 全息材质原语 |
| `icons.py` | 3D 图标：45° 等轴测 + 双层底座 + 全息投影（大小两版） |
| `payload.py` | 数据压缩（字典化 + 数组化，体积砍一半） |
| `html.py` | 单文件离线报告（零外链、断网可用） |
| `data.py` | JSON / CSV |
| `docs.py` | Markdown（建议书、口径说明） |

### 2.4 Host（`hosts/`）

| 文件 | 形态 | 入口 |
|------|------|------|
| `cli.py` | 命令行 | `python3 -m hosts.commands.audit --format html --out out --yes` |
| `skill.py` | Agent | `python -m hosts.skill --input '{"last":"7d"}'` |
| `desktop.py` | 桌面版 | `python -m hosts.desktop`（127.0.0.1 只读服务） |
| `plugin.py` | MCP 插件 | `python -m hosts.plugin`（stdio NDJSON） |
| `web.py` | Web 静态站 | `python -m hosts.web --out dist/web` |

**只有这一层**可以读 argv、环境变量、stdin、监听端口、弹浏览器。

---

## 3. 四种形态怎么发布

```bash
python publish.py --target all              # 四个都出
python publish.py --target plugin,web       # 只出两个
python publish.py --target skill --json     # 机器可读
```

```
dist/
├── publish-manifest.json     版本、构建时间、每形态的文件数/体积/入口
├── skill/    SKILL.md + skill.json + 完整代码（96 个文件 ≈1.8 MB）
├── desktop/  start.bat + start.sh + 完整代码
├── plugin/   mcp.json + 完整代码
└── web/      index.html + summary.json + theme.css + theme.dark/light.css（纯静态）
```

发布包**不含个人数据**：`config/` 只带结构文件（阈值、规则、分类表），
不带 `balance-state.json` 的实际数值、`credit-income.json`、`vouchers/`。
`balance-state.json` 用空结构生成。TC-33 会扫描产物，发现泄漏即失败。

---

## 4. 两条扩展路径

### 4.1 新增一个数据平台（比如分析 Cursor 的日志）

只做一件事：写一个类。

```python
# adapters/cursor/source.py
class CursorSource:
    name = "cursor"
    def discover(self, root=None): ...          # 返回 [{tool, version, root, ...}]
    def collect(self, tools, days=None, log=None, root=None, type_rules=None):
        return data, ctx                        # 统一数据模型
```

然后在 host 里换成 `CursorSource()`。**Core 与 Renderer 一行都不用改**——
因为 Core 只认 `data` 这个 dict 的形状（字段约定见
`references/report-template.md`），不认它是从谁的日志里来的。

### 4.2 新增一种产物（比如 PDF 或飞书文档）

只做一件事：在 `renderer/` 加一个模块。

```python
# renderer/pdf.py
def render(path, data): ...   # 只吃 dict
```

因为 Renderer 不 import Core，它天然可以脱离「分析」单独测试：
拿一份固定的 `data.json` 就能出图。

---

## 5. 目录即分层：v3.2 的目录归位

v3.0 把代码拆成了四层，但目录还留着上一版的痕迹：`scripts/` 装着一堆
`from core.calc.x import *` 的转发壳，`assets/` 与 `core/`、`renderer/` 平级，
`tools/` 里既有打包脚本又有浏览器探针。分层写在文档里成立，看目录看不出来。

v3.2 把这些目录按层归位，**不再保留转发壳**：

| 旧位置 | 新位置 | 归属理由 |
|---|---|---|
| `hosts/commands/audit.py` … （21 个） | `hosts/commands/` | 每个文件是一条命令，知道 argparse 与退出码——这是 Host 的事，Core 不知道 |
| `assets/parts/` · `assets/report.html.j2` | `renderer/assets/` | 模板与分片是产物的构造材料，Renderer 只吃 dict，产物自然归它 |
| `tools/package.py` | `deploy/package.py` | 它做的事是「把产物装成可分发的形态」 |
| `tools/cdp_probe.js` 等三个探针 | `tests/tools/` | 探针服务于验收，不是发布工具 |

转发壳没了，**「同一个功能在两处实现」的风险也随之消失**——壳的存在本来是为了
不动已验证的调用点，但壳本身又是一份需要维护的路径映射，改一次层深就要改 21 个文件。
现在调用点统一是 `python -m hosts.commands.<name>`，路径即模块，没有中间层可对不上。

TC-32 因此换了判据：不再检查壳的行数，改为检查
① 依赖方向（`core` 不 import 外层，`adapters` 不 import `renderer`/`hosts`）；
② `hosts/commands/` 的命令数量不缩水（≥20）且 `audit.py` 在位。

迁移的代价是旧命令的写法变了：

```bash
python3 -m hosts.commands.audit --format html --out out    # 旧
python3 -m hosts.commands.audit --format html --out out   # 新
```

已经登记过的系统计划任务要跟着改一次 `--TR` 参数；`refresh_report.py` 生成的
schtasks / crontab 命令模板在 v3.2 里也已改为指向新路径。

---

## 6. 与视觉规范的关系

视觉规范落在 `renderer/theme.py`（令牌）与 `renderer/icons.py`（3D 图标），
**布局文件 `renderer/assets/parts/01_head.html` 里一行颜色都不写**。

好处有三：

1. 深浅两套主题在 Python 里定义一次，HTML / 桌面版 / Web 站三处共用；
2. ECharts 是 canvas 不认 `var()`，页面侧用 `resolveColor()` 把令牌解析成
   具体色值——色板仍然只有一份；
3. 3D 图标用 SVG + `currentColor`/`var()` 取色，换主题时图标自动跟着变，
   不需要出两套图。

细节见 `references/visual-system.md`。

---

## 7. 自检

```bash
python tests/run_acceptance.py TC-32   # 依赖方向
python tests/run_acceptance.py TC-33   # 四形态产物
python tests/run_acceptance.py TC-34   # 假数据源驱动 Core
python tests/run_acceptance.py         # 全量 34 项
```
