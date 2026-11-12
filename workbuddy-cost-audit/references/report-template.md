# 报告结构与区块说明（v3.1 · 四个一级菜单 / 十个页面）

报告是**单文件离线 HTML**：数据、图表库、脚本全部内联，双击 `file://` 即可打开，不发起任何请求。

结构由 `assets/report.html.j2` 的占位符决定（`STYLE` / `BODY` / `DATA_JSON` / `ECHARTS` / `SCRIPTS`），
布局与脚本分片在 `assets/parts/` 下，按 `renderer/html.py` 的 `JS_PARTS` 顺序拼接：

```
01_head.html · 02_body.html
p03_data.js → p04_shell.js → p05_cockpit.js → p06_recon.js → p07_exec.js
→ p08_recycle.js → p09_analysis.js → p10_control.js → p11_boot.js
```

**所有分片最终拼进同一个 `<script>`**：顶层 `const` 重复声明会直接抛
`SyntaxError: Identifier 'X' has already been declared`，所以跨分片同名常量必须换名
（例如 `p06` 的 `TRIG_TAG_LBL` 与 `p03` 的 `TRIG_TAG`）。

---

## 1. 全局外壳

| 区域 | 内容 | 数据来源 |
| --- | --- | --- |
| 左侧栏 | **四个一级项**；`对账中心`、`审计控制` 各带 **4 个常驻二级项**（`display:block`，激活时只提亮不展开——审计场景里人要能一眼看到自己在哪一层）；每组右侧有独立 `caret` 命中区，可折叠该组（状态存 `localStorage['wbc-nav-collapsed']`，路由进入该组时自动展开）；徽标：对账中心 = 待处理异常数 `#nbAnom`，审计控制 = 不可见执行次数 `#nbMon` | `anomalies[]`、`monitor.totals.unseenTasks` |
| 左侧栏底部 | 三行身份信息：工具类型 `#sfTool`（`meta.sources[].tool` 去重合并）/ 版本 `#sfVer`（`meta.appVersion` + 客户端版本）/ 用户名 `#sfUser`（`meta.user`）。**这里不放金额与告警**——它们已在监控审计舱与对账中心出现，同一数在三处出现必然改一处忘一处 | `meta` |
| 顶栏 | 左侧：页面标题（`一级 · 二级`）+「本机只读数据」胶囊；右侧：`#tbStamp` 时间戳、**数据更新**、导出说明 | `meta`、`coverage.creditAccuracy` |
| 视图容器 | 7 个 `.view`：`#v-cockpit`、`#v-recon`、`#v-control-exec/-task/-income/-recycle`、`#v-analysis` | — |

**页面清单只有一处定义**（`p04_shell.js` 的 `PAGES`），标题、导航态、子菜单展开、容器 id 全由它推导：

```js
const PAGES = {
  cockpit:{t:'监控审计舱', fit:true},
  recon:{t:'对账中心', fit:true, subs:{flow:…, expense:…, daily:…, book:…}},
  control:{t:'审计控制', fit:false, subs:{exec:…, task:…, income:…, recycle:…}},
  analysis:{t:'运营分析', fit:false}
};
```

- **路由**：`#page/sub`（如 `#recon/expense`）。`parseHash()` 容错——只给一级时落到该组第一个子页；
  非法值返回 `null` 并保持当前页。`hashchange` 只在 `page/sub` 真正变化时才重绘，避免同址重复渲染。
- **容器映射**：`viewNode(page, sub)` —— `control` 四页形态完全不同（表格 / 表单 / 图表 / 处置流），
  各用独立容器；`recon` 四页同构（筛选 + 表格），共用一个容器 `#v-recon`，避免滚动位置串台。
- **一屏约束**：`fit` 标志只作用于 `cockpit` 与 `recon`（`#viewWrap.fit{overflow:hidden}`）；
  `control` 的表单页与 `analysis` 允许整页滚动——硬塞进一屏只能靠缩字号，反而更难读。
- **旧链接兼容**：`LEGACY_PAGE = {overview:'cockpit', ledger:'recon', monitor:'control', benchmark:'analysis'}`。

**数据更新按钮**（`#btnRefresh`）= 按本次采集的快照重算派生指标，并**强制重绘当前视图**
（`refreshAllData()` 先把 `inited[*]` 全部作废）。它不会联网重新采集——报告是离线单文件，
要拿最新日志必须重跑 `audit.py`。执行后 `#tbStamp` 变为「更新于 HH:MM:SS」。

**积分实测率**（`coverage.creditAccuracy`）= 有 credit 上报的步骤占比，低于 90% 时要提醒。
该指标不占独立版面，挂在 `#btnRefresh` 的 `title` 悬浮说明里。

**列表分页**：`PG_SIZE = 10`；`pgSlice(rows, page, size)` 切片，`pagerHTML(s, attr)` 出页码
（折叠成 `1 … 3 4 5 … 25`）。`attr` 是页码的 URL 参数名，各列表分别用 `page` / `pgb` / `spg` / `icp`，
互不覆盖。**导出的是筛选后的全量**，不是当前页。

**列表列宽自适应**（`fitTableColumns` / `applyTableFit`）：渲染后按「该列实际展示的内容」重新分配 ——

1. 用 canvas `measureText` 量出每列表头与单元格的文字自然宽度（中英文按实际字形算）；
2. 每列取「表头宽」与「该列最宽内容」的较大值作为需求宽度，数字列上限 140px、文本列上限 320px；
3. 总宽超过容器时，从还有余量（高于表头下限）的列里按比例削；总宽小于容器时，富余空间全给**弹性列**（`th[data-fit-flex="1"]`）；
4. 结果写进 `<colgroup>` 并把表格宽度固定为各列之和；超宽时容器内横向滚动；
5. 触发时机：`renderView` 之后、筛选/翻页重绘之后、窗口 resize 去抖 120ms。

---

## 2. 十个页面

### 2.1 监控审计舱 `#v-cockpit`（一屏 2×2）

| 板块 | 内容 | 关键函数 |
| --- | --- | --- |
| ① 今日动态监测 | 日初结转 / 当前可用 / 今日消耗 / 今日收入四块读数 + 任务实时消耗曲线（按小时归集，收/支双线 + 累计线）+ 三个不可见执行入口（定时任务 / 后台隐藏任务 / 技能·子代理 → `go('control','exec')`） | `ckTilesHTML` `ckToday` `ckEntriesHTML` `ckDrawToday` |
| ② 周期动态监测 | 月度 / 季度 / 年度切换；期初结转、消耗、收入、可用四读数 + 收支柱状 | `ckAgg` `ckRange` `ckPeriodKpisHTML` `ckDrawPeriod` |
| ③ 对账审计分析 | 左：对账偏差（逐日账户余额与偏差，带「查看详情」跳日终对账）；右：风险审计（浪费 Top4 任务，带跳转处置） | `ckReconStats` `ckRiskStats` `ckReconMini` `ckRiskMini` |
| ④ 告警待办 | 今日收入维护 / 可用余额告警 / 日终对账 / 风险审计四条待办，按数据状态给可点动作 | `ckTodoHTML` |

**结转口径**：`carryIn` 取区间**第一天的日初**。把每日日初相加会把同一笔钱重复算 N 次，
看着规模很大，其实错得很离谱——页面标签因此写「期初结转」并注明「上一期结转到本期」。

**canvas 不吃 CSS 变量**：`rgba(var(--pri),.2)` 只有 CSS 能解析，塞进 ECharts 会在
`addColorStop` 抛 `SyntaxError`。图表里一律走 `varrgb('--pri', .24)`：读分量令牌（`--pri = "0,110,255"`）
再拼 `rgba()`。**内联 `style` 里的 `rgba(var(--ink),.07)` 不用改**，CSS 自己能解析。

### 2.2 对账中心 `#v-recon`（一屏，四个子页共用容器）

| 子页 | 内容 |
| --- | --- |
| 今日收支流水 | 筛选（**区间** / 方向 / 触发方式 / 关键词）+ 收支双卡 + 小时分布 + 近 7 日收支 + 区间支出 Top5；列表 10 列，点行下钻步级时间线。时间口径只有一处：`credRange()` 读筛选条的 `LF.from`/`LF.to`，都不填即「今天那一段」——原先顶部那排 T 日胶囊已删除，它与筛选条重复表达同一字段 |
| 任务消耗明细 | 十列主表 + **点行就地展开执行明细十二列**（见下） |
| T+1 日终对账 | 日链表（日初 / 收入 / 消耗 / 日结 / 账户余额 / 偏差 / 判定）+ 偏差行标红 + 偏差与调账侧栏 + 对账参数（初始积分 / 今日余额 / 三档阈值）+「立即对账」 |
| 对账台簿 | 周期图表（收入 / 消耗柱 + 日结线）+ 台账表 + 状态分段 + 分页 + 导出 |

**执行明细（`stepsPanelHTML`）为什么就地展开**：审计时要「主表与步骤表上下一屏对着看」，
抽屉会盖住主表。列：阶段 / 步骤 / 模型·工具 / 技能 / 用时 / 输入→输出 / 积分 / 占比 / 命中规则 /
状态 / 可回收 / 描述，独立分页（`spg`）与导出（`exportSteps`）。

**逐步积分可能缺失**：实测 35 个任务里 26 个有逐步积分，9 个只在任务级上报总数。
这时「积分 / 占比」显示 `—` 并附说明，**不能显示 0**——否则会被读成「这些步骤不耗积分」。
导出走同一判断（`noStepCredits`），CSV 里也留空。

### 2.3 审计控制 `#v-control-*`（四页四容器，定高 grid 一屏）

| 子页 | 容器 | 内容 |
| --- | --- | --- |
| 执行监测 | `#v-control-exec` | 四行定高：横幅 → **左 2×2 指标卡 + 右「定时任务配置」同一行等高**（`align-items:stretch`，水平居中）→ **异常置顶**（错过计划 / 中断 / 失败）→ 执行记录（伸缩）。执行记录五个页签「全部 / 定时任务执行 / 后台自动化 / 技能·子代理链 / 隐藏调用明细」，每页 10 行带分页；点行开「执行明细」弹窗（概览 8 格 + 技能链按载体聚合 + 隐藏调用按会话归属），隐藏调用页签的行另有独立明细弹窗。v3.3 撤掉消耗构成、四类不可见执行、技能·子代理调用、隐藏调用明细四块卡片 |
| 任务设置 | `#v-control-task` | 列表标题栏右侧「新建任务」→ 弹窗；行内「编辑」复用同一弹窗。**操作列每一行都是同一套三件：编辑（铅笔）/ 删除（垃圾桶）/ 启停（switch）**，不做「系统任务少给一个按钮」的差异化——少画一个只会被当成漏做。弹窗对所有任务同构：基本信息 → 运行状态（运行位置 / 注册状态 / 解释器·脚本 / 上次执行 / 下次执行 / 执行·消耗）→ 运行脚本 → 注册命令 →（系统任务多一段）启停同步。**首行固定是系统计划任务「每日积分取数与日账」**（`RAW.dailyCredit`）：带「系统任务」标记，状态列显示「已注册 / 未注册 / 未探测」三态；它的删除给的是 `--uninstall` 卸载命令（不从列表里藏掉一个还在跑的任务），启停给的是 `--disable` / `--enable` 同步命令，两边口径不一致时状态列写「待同步」。三层数据：基座（`MON.configs` + 内置预警，只读）+ 本机新建（`custom[]` 整条归页面）+ 覆盖层（`edit{}` / `del[]` 按 id 覆盖）→ `localStorage['wbc.taskset.v2']`。另含生成 `schtasks` / `crontab`（**只出文本，不执行**）、运行脚本导入（`FileReader`，>512 KB 提示）与在线编辑 |
| 收入配置 | `#v-control-income` | 人工录入（**日期默认当天**；来源固定四类：购买 / 签到领取 / 任务领取 / 其他）+ 快照上传（`crypto.subtle` 算 SHA-256，不可用时**明说**）。「记录留痕」= **先与账本流水比对、再把比对结论随记录一起入哈希链**（`prev_hash` / `hash`，`icVerifyChain` 复算并指出第一处断点）；「导出留痕包」打 `wbc.income.manual-trail.v1`。另有数据源 API 配置（导出默认剔除令牌）与 `income_entry.py add … --snapshot-sha256` 命令文本 |
| 回收调整 | `#v-control-recycle` | 左「**审计引擎**」八条规则卡（可点击筛选，再点取消，与状态胶囊取交集）+ 右异常事件列表（每页 10 行、行高与左列等高、垂直居中，联动收窄）：识别 → 确认 → 止损 → 归因 → 留证；六类止损措施；补偿申请缩为卡片脚一行预留位。v3.3 删除「规则影响积分构成」卡与 `mountAnomalyCharts()` |

### 2.4 运营分析 `#v-analysis`（整页滚动）

| 区块 | 内容 | 关键函数 |
| --- | --- | --- |
| 运行模式对比 | 纯推理 / 工具链 / 重试补跑三类：任务数、积分占比条、合计与均值、均耗时、成功率；下方三条建议**由数据条件触发** | `amBucket` `amAgg` `amModeAdvice` |
| 模型运行情况 | 覆盖层级、任务数、调用步数、积分与占比、均积分、均端到端、成功率、四档判定 | `amModelRows` `amModelVerdict` `amModelConclusion` |
| 单层级模型对比 | 性价比矩阵（对数横轴）、端到端时长结构、对比明细、结论 | `drawMatrix` `drawTime` `renderBenchTable` |
| 三档建议 / 分级路由 / 落地跟踪 | 立即可做 / 需验证 / 结构性优化；L1—L4 路由；当前主用 → 建议模型 | `RECS`、`ROUTING`、`bestAt` |

**模式按真实执行结构切分**（不是按业务名称编排）：三种模式对应的止损动作完全不同，
按业务分类分出来的组是没法指导动作的。

**模型积分从 `bench.totalCredits` 汇总**，不用 `RAW.models[].credits`——后者按单价折算，
单价未上报的模型会被算成 0，等于把真实使用量抹掉（实测差 78 积分）。

**判定与结论都不跨层级**：`均积分/任务` 受覆盖层级影响（L4 与 L1 差一个量级），
表里明确提示「不能跨层级直接比性价比」，选型看下方按层级切分的对比。

---

## 3. 数据文件与页面

| 文件 | 页面 |
| --- | --- |
| `summary.json` | 监控审计舱 |
| `budget_status.json` | 监控审计舱（今日读数）+ 对账中心 · T+1 日终对账 |
| `tasks.csv` / `steps.csv` | 对账中心 · 今日收支流水 / 任务消耗明细 |
| `daily_stats.csv` | 对账中心 · 对账台簿 |
| `recon-daily.json` | 对账中心 · T+1 日终对账（日链 + 流水 + T+1 状态） |
| `anomalies.json` | 审计控制 · 回收调整 |
| `scheduled_summary.csv` / `monitor_records.csv` | 审计控制 · 执行监测 |
| `recommendations.json` / `recommendations.md` | 运营分析（三档建议） |
| `audit-log.jsonl` | 不渲染，仅审计追溯 |


## 4. 前端重算逻辑（`p03_data.js`）

报告是静态 HTML，但积分监测需要「页面里改完马上看效果」：

- `BAL_CFG`：页内配置的**内存第一真源**，`recalcBudget()` 每次都先 `Object.assign(t, BUDGET_BASE)` 回滚到报告原始值，再叠加 `BAL_CFG`（或 localStorage 读回的值），因此改一次算一次，不会残留脏值；
- `BAL_KEY = 'wbc.balance.v2'`：localStorage 键，**只做尽力持久化**。`saveLocalBalance()` 先写内存再试写 localStorage，写入失败（部分浏览器在 `file://` 下禁用）时 `BAL_PERSISTED = false`，页面照常生效并在 toast 里提示「刷新后回到报告值」；
- `normThresholds()`：阈值单调修正 `黄 ≤ 橙 ≤ 红` 并夹到 0—100，与后端 `budget.py` 同口径；`thFixedNotice()` 在被修正时给出说明；
- `refreshAfterBalance()`：重算 → `syncBalanceChrome()` 刷侧栏 → 对当前视图 `refreshView()`（已渲染的强制重建、未渲染的作废缓存）。**这一步是审计舱能跟着变的关键**，`renderView(page, sub)` 默认有 `inited[key]` 缓存，不强制就不会重画；
- `BUDGET_BASE`：`RAW.budget` 的深拷贝快照。`recalcBudget()` 每次先整体回滚到它再叠加页内配置，因此不需要单独的「恢复」动作也不会残留脏值；
- 未配置时 `levelText = '未配置'`、`source = 'none'`；
- **页面改动默认只存在浏览器里**，重新跑 `audit.py` 时以状态文件为准。要长期生效请用 `--set-initial / --set-today-balance / --set-thresholds`。

---

## 5. CLI 与退出码

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--format` | `all` | `html` / `json` / `csv` 的组合 |
| `--out` | `./cost-audit-output` | 输出目录 |
| `--days` / `--last` / `--from` `--to` | 全部历史 | 时间窗口 |
| `--set-initial N` | — | 写初始积分到状态文件 |
| `--set-today-balance N` | — | 写今日余额到状态文件（日期可用 `--balance-date` 指定） |
| `--set-thresholds warn=10,crit=20,red=30` | — | 写预警比阈值（今日消耗占当前余额的百分比，可带 `%` 号） |
| `--balance N` / `--balance-date` | 无 | 临时当前余额（仅本次运行，不写盘） |
| `--balance-state` | `config/balance-state.json` | 状态文件路径，等价于环境变量 `WBC_BALANCE_STATE` |
| `--threshold` | 读状态文件 | 临时阈值 `warn=10,crit=20,red=30`（仅本次运行） |
| `--credit-ledger` | 无 | 第二来源流水（可重复） |
| `--scheduled-only` | `false` | 只打印定时与后台执行 |
| `--budget-only` | `false` | 只输出积分监测摘要与退出码 |
| `--yes` | `false` | 跳过交互确认（白名单校验不跳过） |

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常完成；用户取消；或未配置（不产生级别） |
| `2` | 黄：预警比 ≥ 黄色阈值（默认 10%） |
| `3` | 橙：预警比 ≥ 橙色阈值（默认 20%） |
| `4` | 红：预警比 ≥ 红色阈值（默认 30%） |
| `1` | 运行错误（安全自检失败、采集异常、输出超限） |
