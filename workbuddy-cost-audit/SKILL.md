---
name: workbuddy-cost-audit
description: 积分对账审计应用——只读采集本机 WorkBuddy / CodeBuddy 的会话日志与积分上报，输出「监控审计舱、对账中心（收支流水 / 任务消耗明细 / T+1 日终对账 / 对账台簿）、审计控制（执行监测 / 任务设置 / 收入配置 / 回收调整）、运营分析」四个一级菜单共十个页面的离线审计报告。当用户询问积分/token 消耗多少、钱花在哪、哪些任务在浪费、当前余额还剩多少、账实是否相符、定时与后台任务消耗多少、该换哪个模型时使用。对账口径：日初 = 日收 + 上一日日结余额，日结 = 日初 − 日耗，偏差 = 账户余额 − 日结，按 T+1（次日 00:00）对账；每笔支出与收入带全局唯一的 16 位交易流水号（8 位日期 + 6 位缩写 + 2 位序列），并按任务 ID 映射。已消耗积分不可追回，「回收调整」只做确认/止损/归因/留证与账面归因，不改余额、不承诺补偿。不自动截屏、不 OCR、不接客户端调试端口；不做可用天数或月度预估。全程不联网、不写回客户端配置、不自动处置任何任务。
license: MIT
---

# 积分对账审计应用（WorkBuddy / CodeBuddy Skill）

## 能力边界（先读，避免误用）

本 Skill 是**审计工具**，不是**管控工具**。

- 只读：只读取日志、会话数据库、项目会话文件与本地积分流水，不修改客户端任何配置与历史。
- 不联网：`manifest.json` 的 `network.allow` 为空数组，`sandbox_check` 检测到外联模块会直接阻断。
- 不处置：八条异常规则只做**标记与归因**，不自动重试、不自动降级模型、不自动删除定时任务。所有优化动作需人工确认。
- 不预测：**不提供「预计可用天数」「月度预估」「月省多少」**。每天实际消耗差异极大，任何外推都会给出误导性的确定性；页面只陈述四件事——今日余额多少、今日消耗多少、当前余额多少、预警比在哪一档。
- **余额不做自动抓取**：不截屏、不 OCR、不连客户端调试端口。余额只存在于客户端界面里，本地没有任何可信副本，因此改为人工填两个数（第 3.5 步）。

## 四个一级菜单 · 十个页面（导航即能力清单）

| 一级 | 二级 | 内容 |
| --- | --- | --- |
| **监控审计舱** | —（四板块） | ① **今日动态监测**：日初结转 / 当前可用 / 今日消耗 / 今日收入 + 任务实时消耗曲线（按小时归集，收/支双线 + 累计线）；三个入口可跳「执行监测」看定时任务、后台隐藏任务、技能·子代理；② **周期动态监测**：月 / 季 / 年切换，看期初结转 / 消耗 / 收入 / 可用 + 收支柱状；③ **对账审计分析**：对账偏差（逐日余额与偏差，可跳详情）与风险审计（浪费 Top4，可跳处置）；④ **告警待办**：今日收入维护、可用余额告警、日终对账、风险审计 |
| **对账中心** | 今日收支流水 | 16 位流水号、方向、任务、积分、经办；顶部筛选（日期/方向/触发/关键词）统一生效；导出筛选后全量；每页 10 行 |
| | 任务消耗明细 | 十列主表（任务类型 / 名称 / ID / 流水号 / 触发方式 / 模型·模式 / 步长 / 积分 / 耗时 / 详情）；**点行就地展开执行明细十二列**（阶段 / 步骤 / 模型·工具 / 技能 / 用时 / 输入→输出 / 积分 / 占比 / 命中规则 / 状态 / 可回收 / 描述），可按 全部 / 模型步 / 工具步 / 异常步 过滤，独立分页与导出 |
| | T+1 日终对账 | 逐日链式结转 + T+1 判定 + 偏差与调账；页面内「立即对账」重算日链（写盘仍需 `recon_daily.py --verify`） |
| | 对账台簿 | 周期台账：收入 / 消耗柱 + 日结线 + 状态分段（一致 / 偏差 / 待对账），导出 |
| **审计控制** | 执行监测 | 定时任务、后台隐藏任务、技能·子代理、隐藏调用分别跑了什么；**异常置顶**（错过计划 / 中断 / 失败） |
| | 任务设置 | 定时任务与预警任务的周期、阈值；生成 `schtasks` / `crontab` 与「交给自动化」提示语（**不执行**）；运行脚本导入与在线编辑 |
| | 收入配置 | 人工录入（类型 / 金额 / 日期 / 备注）+ 快照上传（SHA-256 入库）+ 录入留痕 + 数据源 API 配置（导出默认剔除令牌） |
| | 回收调整 | 识别 → 确认 → 止损 → 归因 → 留证；六类止损措施；补偿申请**预留位**；可回收积分为**账面归因列** |
| **运营分析** | —（双维度） | ① **运行模式**：纯推理 / 工具链 / 重试补跑三类的任务数、积分占比、均耗时、成功率，并给出条件触发的建议；② **模型**：覆盖层级、任务数、积分占比、均积分、均端到端、成功率与判定；③ 再往下是单层级性价比矩阵、分级路由、三档建议 |

**一屏策略**：审计舱与对账中心四页强制一屏（`#viewWrap.fit`），超长内容在卡片内部滚动；
审计控制的表单页与运营分析允许整页滚动——那几页塞进一屏只能靠缩字号，反而更难读。

所有表格的**列宽按该列实际展示的内容自适应**：内容多的列（如任务名）自动放宽，数字与状态列保持紧凑，不会出现某列空着一大片、某列挤成一团。

**日终对账的偏差会标红到具体的那一天那一行**：日初 − 日耗 = 日结，日结 vs 账户余额超容差（`max(0.5 积分, |日初| × 5%)`）的整行标红，右侧逐日列明日结 / 实盘 / 调账金额 / 归因提示——不只给一个笼统的总差异率。未到 T+1 00:00 的日子标「待 T+1 对账」，账面值只作 provisional。

**执行明细不跳抽屉**：任务消耗明细点行**就地展开**，主表仍在上面——审计时要上下对着看。
步骤表的「积分 / 占比」对**只在任务级上报积分**的任务显示为「—」，并附一句说明；
按 0 显示会让人误读成「这些步骤不耗积分」。

**口径诚实**：「结转总额」取区间第一天的日初（逐日相加会把同一笔钱重复算 N 次）；
采集窗口 < 7 天时不做按天外推；步骤级「输入 / 输出用时」不编造（日志只有步级总耗时）；
模式与模型的建议都由数据条件触发，条件不成立就不出现。

**顶栏「数据更新」按钮**：按本次采集快照重算并重绘当前视图。报告是离线单文件，**这个按钮不会联网重新采集**——用户若说「数据是旧的 / 想看最新的」，正确做法是重跑 `audit.py`，不要让人以为点一下就能拿到新日志。

**顶栏主题切换**（太阳/月亮）：Holo Dark / Holo Light 双主题，选择存 `localStorage` 的 `wbc-skin`。切主题会连带**重绘图表**——ECharts 不认 CSS 变量，颜色是生成时读出来喂给 canvas 的。用户若要默认浅色，用 `hosts/commands/audit.py --skin light` 生成，不必每次手点。

## 启动模式

| 模式 | 触发场景 | 命令 |
| --- | --- | --- |
| A 完整审计（默认） | 「看看我这个月积分花在哪了」 | `python3 -m hosts.commands.audit --format all --out ./cost-audit-output` |
| B 只看积分监测（轻量） | 「余额还剩多少 / 够不够」 | `python3 -m hosts.commands.audit --budget-only` |
| C 只看定时与后台执行 | 「这些自动化和后台任务值不值得留」 | `python3 -m hosts.commands.audit --scheduled-only --last 30d` |
| D 定时重跑采集 | 「让报告的监测数据自动保持最新」 | `python3 -m hosts.commands.refresh_report --out ./out --format all` |
| E 每日积分日账 | 「每天花了多少 / 余额怎么掉的」 | `python3 -m hosts.commands.daily_credit` |

模式 B 不写 HTML、不写 CSV，只输出一行摘要 + `budget_status.json` + 退出码，适合挂进 hooks 或 CI 的第一步。

**模式 D 是给系统计划任务直接调用的入口**：它内部调用 `audit.py`，口径完全一致，额外带**并发锁**（上一次没跑完就跳过）、`out/refresh.log` 运行日志与 `out/last-refresh.json` 状态快照。触发时**只跑本机 Python、不唤醒 WorkBuddy，因此不消耗积分**。脚本内容可在报告的「审计控制 → 任务设置 → 任一行『编辑』→ 运行脚本」里直接编辑、导入或下载到本机。

**模式 E 解决的是趋势而非快照**：报告是单文件，数字在生成那一刻就固定了；`daily_credit` 把每天的结果
压成一行写进 `out/credit-daily.jsonl`（一天一行、同日覆盖、采集失败不写、带 `lagDays` 标明数据锚点日
落后自然日多少天）。`--install` 落盘 `deploy/schedule/daily_credit.bat|sh`、注册每日计划任务并回写
`out/schedule-state.json`；`--print-schedule` 只打印脚本与命令。同样不唤醒 WorkBuddy，不消耗积分。

**这条任务在页面上也看得见**：报告的「审计控制 → 任务设置」首行就是它，带「系统任务」标记与
「已注册 / 未注册 / 未探测」三态。三份数据分开存、并排显示——`config/schedule.json` 是打算几点跑、
`out/schedule-state.json` 是系统里实际注册成什么样、`out/credit-daily.jsonl` 是真跑过几天（含缺口）。
改完时间要重新 `--install`（或 `--status` 重新探测），否则页面显示的还是旧状态。

**启停**用 `--disable` / `--enable` 一条命令做完三件事：改 `config/schedule.json` 的 `enabled`、
同步任务计划程序（`Disable-/Enable-ScheduledTask`，失败退 `schtasks /Change`）、回写
`out/schedule-state.json`。页面上每一行的操作列都是同一套三件（编辑 / 删除 / 启停）；系统任务那行
的开关改的是页面口径，点完会给出这条命令，两边不一致时状态列写「待同步」。先写配置再动系统——
反过来中途失败就变成「系统停了、配置还说开着」，两边都说不清哪个是对的。

## 分层与发布形态（v3.0.0）

逻辑只有一份，四种形态是它的四个入口。**不要为某个形态另写一套流程**——那会让口径分叉：

```
core/       纯逻辑：ports（Protocol 接口）/ policy（纯判定）/ pipeline（唯一编排）/ calc（12 个计算）
  ↑
adapters/   只有这层碰外部世界：state / store / discover / parse_logs / workbuddy.source
  ↑
renderer/   数据 → HTML/CSS/SVG：theme（令牌）/ icons（等轴测图标）/ payload / html / data / docs
  ↑
hosts/      cli（人）/ skill（模型）/ desktop（本地 HTTP）/ plugin（MCP）/ web（静态站）
```

依赖方向单向，由验收 TC-32 用 AST 扫 import 守住：`hosts → 全部`、`adapters → core`、`renderer → 无`、`core → 无`。
所以任何改动都先问一句「这属于哪层」：判定规则进 `core`，读文件进 `adapters`，拼 HTML 进 `renderer`，参数解析与输出格式进 `hosts`。

`hosts/commands/*.py` 现在只是**转发壳**（`from core.calc.x import *`），保留是为了让已登记的定时任务、测试与老文档继续可用；每个壳都设了 40 行上限，超了判失败——防的是实现被抄回去又变成两套。

```bash
python publish.py --target all            # 四形态一键发布 → dist/{skill,desktop,plugin,web}
python publish.py --target plugin,web     # 只出指定形态
```

| 形态 | 入口 | 读者 | 特征 |
| --- | --- | --- | --- |
| Skill | `python -m hosts.skill` | 模型 | 只回 JSON：`verdict` / `metrics` / `warnings` / `advice` / `artifacts`，每项自带口径，模型不会去翻页面小字 |
| 桌面版 | `python -m hosts.desktop` | 打开电脑的人 | `127.0.0.1` 只读 HTTP，无 Electron，路径穿越 403 |
| 插件 | `python -m hosts.plugin` | MCP 客户端 | stdio NDJSON JSON-RPC；`audit_run` / `audit_budget` / `audit_explain` |
| Web | `python -m hosts.web` | 点开链接的人 | 静态站 6 文件，无需运行时 |

发布包**不含个人数据**：`config/` 只带结构（阈值、规则、分类表），`balance-state.json` 是空结构（金额全为 `null`），`credit-income.json`、`balance-snapshots.jsonl`、`vouchers/` 不出包。验收 TC-33 会在发布包里搜个人数据痕迹。

**插件形态的 stdout 是协议通道**：任何 `print` 混进去都会让客户端解析失败，日志一律走 stderr。这条在改 `hosts/plugin.py` 时最容易踩。

## 工作流

### 第 1 步：确认数据范围与授权

1. `python3 -m hosts.commands.discover` 发现可用数据源（依次探测 `.workbuddy` / `.codebuddy` / `.claude` / `.openclaw`），打印每个根的版本号、日志文件数、会话文件数。
2. 向用户复述**将要读取什么、写到哪里、不会做什么**：
   - 读到：`~/<agent>/logs/**/*.log`、`~/<agent>/<agent>.db`、`~/<agent>/projects/**/*.jsonl`；
   - 写出：`<out-dir>/` 下的报告与数据文件；带 `--set-*` 时额外写 `config/balance-state.json`；
   - 不做：不联网、不改客户端配置、不动任务。
3. 用户确认后继续；用户拒绝则以退出码 0 正常结束（用户取消不是错误）。
   - 自动化场景传 `--yes` 跳过交互，但路径白名单校验**不会**被跳过。

### 第 2 步：采集与解析

```bash
python3 -m hosts.commands.audit --out ./cost-audit-output [--days 30 | --last 7d | --from 2026-09-01 --to 2026-09-30]
```

- 采集顺序：`sandbox_check` → `discover` → `parse_logs`（按 `ADAPTERS` 注册表分派）→ `redact` → `classify`。
- 步骤级积分以日志 `credit consumed` 为准（`messageId` 维度，同 id 多次上报取最大值），token 与耗时以 `qua.end:` 为准。
- 同一 `messageId` 在多个根出现时只计一次，避免多 Agent 共同上报导致重复计数。
- 采集阶段即执行跳过规则：证书/私钥/凭据类文件在枚举阶段就跳过，不进入解析。

### 第 3 步：分析

全部在本地内存完成：

1. **总览** `analyze.py`：KPI、横幅（黄/橙/红三档，未配置时为灰色）、执行构成、Top 任务、数据质量。
2. **台账** `ledger.py` + `query.py`：任务级/步骤级明细与多维筛选；两行统计（任务情况 / 积分监测）。顶部筛选栏对「今日收支流水 / 任务消耗明细 / T+1 日终对账 / 对账台簿」四个子页统一生效。
3. **执行监测** `monitor.py`：四类互斥归类（定时 > 后台 > 技能 > 人工）+ 隐藏调用单列，输出执行记录、定时任务配置、技能调用、隐藏调用明细。页面侧只保留「异常 → 执行记录」两件事：四个统计指标卡与定时任务配置同处一行等高，执行记录按五个页签（全部 / 定时任务执行 / 后台自动化 / 技能·子代理链 / 隐藏调用明细）每页 10 行，点行开执行明细弹窗（技能链按调用载体聚合，子技能名未落库时如实说明）。
4. **异常** `anomaly.py`：八条规则（`RETRY_STORM` `CONTEXT_BLOAT` `TOOL_LOOP` `TASK_CREDIT_SPIKE` `SCHEDULED_SPIKE` `MODEL_SWITCH` `FAILED_BURN` `CACHE_MISS`），规则级浪费回算到任务级，保证「规则合计 = 任务合计」。
5. **积分监测** `budget.py`：`今日消耗 → 当前可用余额 → 预警比 → 黄/橙/红级别 → 退出码`，外加模型切换一致性。

5.1 **流水号** `txn.py`：16 位（`YYYYMMDD + 6 位缩写 + 2 位序列`），按 `taskId` 映射且幂等；收入线走固定缩写池，序列 01—99 后转两位 36 进制；本地生成，零积分消耗。

5.2 **日终对账** `recon_daily.py`：逐日链式结转（`日初 = 日收 + 上一日日结`、`日结 = 日初 − 日耗`、`偏差 = 账户余额 − 日结`），T+1 00:00 判定，附 `--print-schedule` 生成 `schtasks` / `crontab` 定时对账脚本。同时输出收入侧质量指标：`snapshotRate`（已附凭证）/ `evidenceRate`（哈希可复算）/ `trailRate`（留痕覆盖）/ `noSnapConfirmed` / `tampered` / `missing`。

5.3 **收入录入** `income_entry.py`：**单人**上传凭证快照 + 录入记录留痕（v3 起取消「复核人 ≠ 录入人」的双人复核）。`add` 必须带 `--snapshot` 或 `--snapshot-sha256`，凭证复制进 `config/vouchers/` 并记 SHA256；`confirm` 单人提交即入账；`edit`（已入账需 `--reopen`）/ `void`（必须写原因）每一步都追加 `revisions`；每次动作追加一行 `config/income-entry-log.jsonl`，带 `prev_hash` 哈希链。`verify` 复算全部凭证哈希 + 校验留痕链，检出 `EVIDENCE_TAMPERED` / `EVIDENCE_MISSING` / `NO_SNAPSHOT_INCOME` / `LOG_CHAIN_BROKEN` / `LOG_TAMPERED`，有异常退出码 5。

5.4 **余额凭证** `balance_snapshot.py`：人工填的余额必须有截图佐证（v2.1 §3.8 B4）。`add` 必须带 `--shot`，凭证复制进 `config/vouchers/` 并记 SHA256；`--taken-at` 记截图时刻，`--day-open` 记当日日初（= 截图余额 + 时刻前已消耗，不填则按截图值近似并标 `B_SNAPSHOT`）；`--apply` 把 `today_balance` 与凭证一起写进 `config/balance-state.json`（`today_balance_snapshot`）。每次录入追加一行 `config/balance-snapshots.jsonl`（`prev_hash` 哈希链）。`verify` 复算哈希 + 校验链，检出 `SHOT_TAMPERED` / `SHOT_MISSING` / `CHAIN_BROKEN` / `LOG_TAMPERED`，退出码 5。手工改余额而不换凭证时，旧快照自动标 `stale`。

6. **对比建议** `compare.py`：层级 × 模型成本/成功率（样本 <3 只展示不推荐）→ 三档建议，用量口径是 `windowSaving`（窗口内实测止损空间，已发生消耗，向前止损），不是月度预估。

### 第 3.5 步：填两个数字，启用积分监测（关键）

**为什么必须人工填**：余额只存在于客户端界面里，`workbuddy.db` 只有 `session_usage`，Chromium 缓存与 `local_storage` 里都没有余额字段。任何自动手段要么依赖客户端开调试端口（随版本变化易碎），要么依赖截图 OCR（读数不确定）。所以本版只保留人工入口，其余全部实时算：

```
初始积分    ← 人工填（换算基数，一期填一次）
今日余额    ← 人工填（当日开始时的余额，跨日后要重填）
今日消耗    ← 日志实时汇总（结算日全部任务积分；结算日 = 数据最后一天与余额日期中较新的一天）
当前余额    ← 今日余额 − 今日消耗
预警比      ← 今日消耗 / 当前余额 × 100%     ← 告警只看这一条线
告警级别    ← 预警比 ≥ 红 30% / 橙 20% / 黄 10%（阈值可配），未越线显示绿色「正常」
```

**怎么引导用户**：让用户打开客户端「个人中心 / 积分」页，把看到的**可用积分**与**本期总额**报给你，然后执行：

```bash
python3 -m hosts.commands.audit --set-initial 10000 --set-today-balance 8600
python3 -m hosts.commands.audit --set-thresholds warn=10,crit=20,red=30
```

其它用法：

```bash
# 临时试算，不写盘：回答「如果余额是 X，现在会是什么颜色」
python3 -m hosts.commands.audit --budget-only --balance 1200
# 换状态文件路径（多账号 / 多期共用一台机器）
python3 -m hosts.commands.audit --balance-state ~/.config/wb-balance.json --budget-only
```

报告网页里也能改：「对账中心 → T+1 日终对账 → 对账参数」（折叠在偏差卡底部）的输入框（初始积分 / 今日余额 / 黄橙红三档阈值）改完点**保存并重算日链**：立即按同一套规则重算整条日链，并**同步刷新审计舱、侧栏**。改动默认也存一份到浏览器，下次打开同一份报告还在；**要写进状态文件、跨报告长期生效，仍需跑上面的 `--set-*` 命令**。

**跨日要提醒**：状态文件里记着 `today_balance_date`。当它不是「数据窗口最后一天」时，说明这份余额已经过期，必须让用户重新看一眼客户端再填，不要用旧数字算。

**未配置时必须如实说**：`未设置初始积分与今日余额，本次不产生红/橙/黄预警，只报实测消耗`，并给出上面两条命令。**不要**把「无预警」说成「余额充足」。

### 第 4 步：输出形态

| 用户诉求 | `--format` | 附加 |
| --- | --- | --- |
| 要一个能点开看的报告 | `html` | 加 `--open` 自动打开 |
| 要贴进周报 / 发给财务 | `md,json` | 读 `recommendations.md` |
| 要自己做透视 | `csv` | `tasks.csv` + `steps.csv` + `daily_stats.csv` + `scheduled_summary.csv` + `monitor_records.csv` |
| 要接进流水线 | `json` | 只用 `summary.json` + `budget_status.json` + 退出码 |
| 不确定 | `all` | 默认，四类全出 |

输出体积上限 10 MB：历史窗口超过 180 天或步骤数超过 5 万时，先建议 `--last 30d` 或 `--format json,csv` 再出 HTML。

### 第 5 步：呈现与建议

1. 先给**一句话结论**：消耗总量、最大浪费源、当前余额与所在阈值区间。
2. 再给**证据**：命中规则、Top 任务、不可见执行占比、对账差异。
3. 最后给**分档建议**，严格按三档措辞，不要越档：
   - t1 立即可做（低风险、可量化节省）
   - t2 需要验证（先跑 3—5 轮再切）
   - t3 结构性优化（改机制，不产生即时节省）
4. 主动披露**数据质量限制**：L4 分级占比、未绑定项目占比、`CACHE_MISS` 是否 `unsupported`、对账差异是否超容差、今日余额是否已跨日。

退出码要转述给用户，不要吞掉：`0` 正常 / `2` 黄 / `3` 橙 / `4` 红 / `1` 运行错误。

## 配置

| 文件 | 作用 | 常见改法 |
| --- | --- | --- |
| `config/balance-state.json` | 积分监测唯一真相源（v3）：初始积分 + `initial_source`（measured / derived / manual）、今日余额及其日期、`today_balance_snapshot`（凭证绑定）、阈值 | 用 `balance_snapshot.py add --apply` 或 `audit.py --set-initial/--set-today-balance/--set-thresholds` 写，不要手改；`WBC_BALANCE_STATE` 可换路径。注意 `today_balance` 是当日**日初**余额，日终对账会自动折成日末再比 |
| `config/balance-snapshots.jsonl` | 余额凭证台账（append-only，带 `prev_hash` 哈希链） | 用 `balance_snapshot.py` 写；`verify` 校验 |
| `config/vouchers/**` | 凭证仓库（余额截图 + 收入凭证），文件名含日期与哈希前 8 位 | 文件被改动会在 `verify` 里以 `SHOT_TAMPERED` / `EVIDENCE_TAMPERED` 暴露 |
| `config/credit-income.json` | 收入台账 v3：`id` / `snapshot{file,sha256}` / `revisions[]` / `status(draft\|confirmed\|void)` | 用 `income_entry.py add/confirm/edit/void` 写，不要手改——手改没有快照哈希与留痕；`WBC_INCOME_CFG` 可换路径 |
| `config/income-entry-log.jsonl` | 收入留痕日志：每次动作一行 + `prev_hash` 哈希链 | 只追加，不编辑；`verify` 会校验链完整性；`WBC_INCOME_LOG` 可换路径 |
| `config/budget.json` | 对账容差、模型切换判定、速率告警阈值 | 按团队口径调容差 |
| `config/anomaly-rules.json` | 八条规则的开关与阈值 | 关掉不适用的规则；按习惯调 `RETRY_STORM.task_retry_sum` |
| `config/credit-rules.json` | 按 1k token 的积分别率（仅客户端未上报 credit 时折算） | 新增模型时补一行，注意 `rate_unit` |
| `config/classification.json` | 任务类型关键词、复杂度分级档位 | Agent 长任务场景把 `profile` 改为 `quantile` |
| `config/report-layout.json` | 报告页签与区块顺序 | 裁剪不看的视图 |

`--config` / `--rules` / `--credit-rules` / `--classification` / `--balance-state` 可指向自定义文件，不改默认配置。

## 安全与合规（四道闸门）

1. **安装期静态审查**：`python3 -m hosts.commands.sandbox_check --audit-static`，检查无网络模块、无写回配置、无 `eval/exec` 拼接。
2. **运行前自检**：`run_checks()` 校验输出目录可写、外联模块未加载、manifest 网络白名单为空。
3. **采集中三原则**：只读打开、脱敏在解析层、敏感文件在枚举层跳过。
4. **运行后可追溯**：`audit-log.jsonl` 每次运行追加一行（时间、用户、版本、argv、读取根、文件数、产出清单、数据摘要 SHA256、脱敏计数、余额来源）。

脱敏覆盖 6 类：`sk-` 密钥、`ghp_` token、`AKIA` 云密钥、`Bearer` 头、`Cookie`、`api_key/secret/password/token` 键值。

**没有的东西**：不注册系统计划任务，不 spawn 任何进程去截图，不连任何端口。

## 常见追问

- **「为什么没有告警」**：先检查是否填了初始积分与今日余额。任一项为空 → 不产生级别，只报实测消耗。**不要**说成「余额充足」。
- **「为什么提示今日余额过期」**：状态文件里的余额日期比数据最后一天更**旧**。让用户重新看客户端再 `--set-today-balance` 一次。
- **「为什么当前余额和客户端显示的差一点」**：客户端显示的是实时值，`当前余额 = 今日余额 − 今日消耗` 是「填数那一刻 + 当天日志」的结果。请把用户在客户端看到的数作为**今日余额**重新填一次。
- **「为什么异常只命中这些任务」**：规则是保守阈值，未命中的不是「没问题」而是「未达阈值」。先看 `anomalies.json` 里每条规则的 `status` 与 `th`。
- **「为什么 CACHE_MISS 显示 unsupported」**：日志里没有 `cache_read_tokens` 字段，规则无法计算命中率，属于**能力缺失**而非「缓存没问题」。
- **「为什么隐藏调用只有 1 积分」**：话题生成 / 上下文压缩这类请求客户端通常不上报 credit，页面按本机实测折算率估算并标注「折算」。
- **「为什么对账差 1%」**：日志按 `messageId` 累计、会话积分表按 `conversation_request_id` 聚合，口径不同；容差按 `budget.json` 的 `reconcile_tolerance_pct`。
- **「能不能自动降级模型省钱」**：不能。本 Skill 只出建议，降级会导致交付不一致，必须人工决策。

## 参考文档

- `references/log-schemas.md` — 日志与数据库字段字典
- `references/credit-ledger-schemas.md` — 积分监测口径、余额状态文件、流水格式与对账
- `references/classification.md` — 任务分类与复杂度分级规则
- `references/credit-rules.md` — 积分折算率与更新流程
- `references/report-template.md` — 四个一级菜单与十个页面的结构与区块说明
- `references/architecture.md` — 四层职责、依赖方向、扩展路径（加数据源 / 加视图 / 加宿主）
- `references/visual-system.md` — 视觉规范工程落地版：双主题令牌、磨砂玻璃、等轴测图标、QA 清单与负面词库
