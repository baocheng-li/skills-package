# 积分监测口径、状态文件与流水格式（v2.0 · 人工口径）

F6「积分监测」能否给出红/橙/黄，取决于**两个人工数字**是否填了。
本文件定义：为什么改成人工、两级来源、`balance-state.json` 的结构、阈值与级别、流水格式与对账，以及每一档能说什么、不能说什么。

---

## 1. 为什么改成人工

日志只能算出「消耗了多少」，算不出「账户还剩多少」——余额必须来自日志之外。

曾经尝试过三种自动手段，都在实践中不成立：

| 手段 | 为什么放弃 |
| --- | --- |
| 客户端调试端口取数 | 需要客户端带 `--remote-debugging-port` 启动一次，Electron 运行中无法追加；端口、接口字段名随版本变化脆弱 |
| 界面截屏 + OCR | 需额外装 `tesseract`，数字读数不确定，还要处理排队/遮挡 |
| 视觉识图回填 | 依赖外部模型，读数不可复现，且与「离线、零依赖」目标冲突 |

同时，**旧版基于余额的换算也不成立**：`可用天数 = 余额 / dailyAvg7` 的问题在于每天消耗差异极大（本机实测近 3 日 117.8 积分/天 vs 前 7 日 25.0 积分/天，差 3.7 倍），任何除法都给出看似精确、实则误导的数字。

因此 v2.0 收敛为：

- **两个输入**：`初始积分`（阈值换算基数）、`今日余额`（当日开始时的余额）；
- **一个实时量**：`今日消耗` = 结算日全部任务积分之和；
- **结算日**：取「数据窗口最后一天」与「今日余额日期」中较新的一天（上界夹到日历今天）。这样当天刚填余额、当天还没跑任务时，不会被重复扣掉前一天的消耗；
- **两条派生式**：`当前余额 = 今日余额 − 今日消耗`、`预警比 = 今日消耗 / 当前余额 × 100%`；
- **不做**：可用天数、月度预估、月省、自动抓数、定额推荐。

---

## 2. 两级来源（人工 / 未设置）

| 级别 | 判定条件 | 当前余额 | 报告标注 |
| --- | --- | --- | --- |
| **人工设置** (`source=manual`) | `initial_credits` **且** `today_balance` 都非空 | `today_balance − todaySpent` | `人工设置`，给出预警比与级别 |
| **未设置** (`source=none`) | 任一项为空 | `null` | `未设置`，**禁用三级预警**，只报实测消耗 |

`--balance <float>`（可配 `--balance-date`）是**临时当前余额**：按人工口径参与本次计算（内部反推 `today_balance = 临时值 + 今日消耗`），**不写回任何文件**，用于「如果余额是 X，现在会是什么颜色」的试算。

### 2.1 未设置的正确表述

```
积分监测未配置：初始积分与今日余额至少有一项为空，预警不可用
```

**不能**说成「余额充足」。同时给出补齐方式：

- 命令行：`audit.py --set-initial 10000 --set-today-balance 8600`；
- 页面：「审计控制 → 收入配置」或「对账中心 → T+1 日终对账 → 对账参数」输入框（初始积分 / 今日余额 / 黄橙红三档阈值），改完点「保存设置」——立即重算并同步刷新监控总览；
- 临时试算：`audit.py --budget-only --balance 1200`。

---

## 3. `config/balance-state.json`

积分监测的**单一真相源**。路径可用环境变量 `WBC_BALANCE_STATE` 覆盖，默认 `config/balance-state.json`。
写入用 `mkstemp` + `os.replace` 原子替换，避免半截文件。`set_manual()` 只写显式传入的字段，不传的保持原值。

```json
{
  "version": 3,
  "initial_credits": 2000.0,
  "initial_date": "2026-10-26",
  "initial_source": "derived",
  "initial_set_at": "2026-10-29 10:05:17",
  "today_balance": 1500.0,
  "today_balance_date": "2026-10-29",
  "today_balance_set_at": "2026-10-29 10:05:11",
  "today_balance_snapshot": {
    "ref": "5c8d0e91…",
    "file": "vouchers/balance-20261029-100115-e29b676c.png",
    "sha256": "e29b676c93b561af98b8ff2313b8f807a2344dbdb11ade92c090be9dff2b5a0f",
    "taken_at": "2026-10-29 10:01:15",
    "account": 1419.51,
    "day_open": 1500.0
  },
  "thresholds": {
    "mode": "pct",
    "warn": 10.0,
    "crit": 20.0,
    "red": 30.0
  },
  "updated_at": "2026-10-29 10:05:17"
}
```

| 字段 | 说明 |
| --- | --- |
| `version` | 固定 `3`。读入旧版（v1 / v2）文件时按 `3` 对待，缺的字段取默认、多余字段忽略 |
| `initial_credits` / `initial_date` / `initial_set_at` | 初始积分（日链起点）及其生效日期、设定时刻 |
| `initial_source` | `measured`（有截图佐证的实测）/ `derived`（由实测余额逐日反推）/ `manual`（裸填，无凭证） |
| `today_balance` / `today_balance_date` / `today_balance_set_at` | 今日余额（**当日开始时**的账户余额）、对应日期、设定时刻 |
| `today_balance_snapshot` | 余额凭证绑定：`file` / `sha256` / `taken_at` / `account`（截图值）/ `day_open`（折算日初）/ `ref`（台账记录 hash）。手改余额而不换凭证时会被标 `stale` |
| `thresholds.mode` | 固定 `pct`（按「今日消耗占当前余额的百分比」判定），旧字段一律按 pct 归一 |
| `thresholds.warn/crit/red` | 预警比阈值（%），默认 10 / 20 / 30 |
| `updated_at` | 最后一次写入时间 |

> **`today_balance` 是日初值，不是日末值。** 这一点直接影响偏差算得对不对：
> 日终对账里 `account` 那一列要的是「当日**日末**实测余额」，所以代码会把它折成
> `today_balance − 当日消耗` 再参与比对。早期版本漏了这一步，导致偏差凭空多出
> 一整天消耗（10-26 的 −1,221.67、10-29 的 +71.41 都是这个根因）。

**关键行为**：

- 阈值自动做单调性修正 `warn ≤ crit ≤ red`，负值夹到 0；三值缺失时用默认值；
- **结算日** = `max(数据窗口最后一天, today_balance_date)`，且不超过日历今天；
- `today_balance_date` **早于**结算日时，`todayBalanceStale = true`（余额比数据旧，需重填）；晚于时以余额日期结算，当日消耗自然为 0，不算过期；
- 读不到 / 文件损坏时返回默认结构，**不抛异常**，报告照常生成（降级为「未设置」）。

---

## 4. 阈值与级别

### 4.1 语义变更（v1 → v2）

| | v1（已废弃） | v2（现行） |
| --- | --- | --- |
| 比较对象 | 当前余额 vs 阈值余额 | **预警比**（今日消耗 / 当前余额）vs 阈值百分比 |
| 阈值方向 | 余额越**低**越严重 | 消耗越**高**越严重 |
| 默认阈值 | warn 30% / crit 15% / red 5%（剩余） | warn 10% / crit 20% / red 30%（消耗占当前余额） |
| 模式 | `pct` / `abs` 两种 | 只有 `pct` 一种，`abs` 入参按 pct 归一 |

理由：「还剩 30% 就告警」容易被读反，且需要初始积分才能换算；直接比「今日消耗占当前余额 X%」更直观，也不需要再把阈值换算成绝对积分。

### 4.2 级别判定

```
预警比 = 今日消耗 / 当前余额 × 100%
```

| 级别 | 条件 | 退出码 | 颜色 |
| --- | --- | --- | --- |
| `red` | 预警比 ≥ `red`（默认 30%） | 4 | 红 |
| `crit` | ≥ `crit`（默认 20%） | 3 | 橙 |
| `warn` | ≥ `warn`（默认 10%） | 2 | 黄 |
| `none` | 低于黄线（已填数） | 0 | 绿（正常） |
| `null` | 未设置（缺初始积分或今日余额） | 0 | — |

判定用 `>=`（含等号）：恰好 10.0% 判为黄。未设置时 `level = null`、`levelText = "未配置"`、**不产生任何级别**，不允许默认按消耗大小报警。

当前余额 ≤ 0（余额见底或已为负）且今日有消耗时，预警比按 `>100%` 处理，直接判红。

```bash
python3 -m hosts.commands.audit --set-thresholds warn=10,crit=20,red=30       # 写盘（可带 % 号）
python3 -m hosts.commands.audit --budget-only --threshold warn=8,crit=15,red=25  # 仅本次运行
```

---

## 5. 积分流水格式（第二来源对账的输入）

通过 `--credit-ledger <path>` 传入（可重复），或写进 `config/budget.json` 的 `credit_ledger_paths`。
扩展名决定解析方式：`.csv` → CSV，其余 → JSONL。

```jsonl
{"ts":"2026-10-20T00:00:00Z","type":"grant","amount":60000,"balance_after":60000,"task_id":null}
{"ts":"2026-10-24T10:12:00Z","type":"consume","amount":-248.4,"balance_after":59751.6,"task_id":"tk_01a1444f"}
{"ts":"2026-10-26T09:05:00Z","type":"refund","amount":50,"balance_after":59788.2,"task_id":null}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `ts` | ✅ | ISO 8601，也接受 `time` / `timestamp`；带时区先转 UTC |
| `type` | ✅ | `recharge` / `grant` / `refund`（入账）、`consume`（出账）、`expire`（过期）。**其他值整行丢弃** |
| `amount` | ✅ | 带符号；`consume` 写成正数时按绝对值处理 |
| `balance_after` | 可选 | v2 不再用它反推余额，仅保留与未来扩展 |
| `task_id` | 可选 | 关联任务 id |

> v1 曾用流水 `balance_after` 推算余额（L3 来源），v2 已废弃：**余额不再来自任何推算**，只来自人工。

---

## 6. 对账

### 6.1 余额对账（是定义，不是校验）

```
今日余额 − 今日消耗 = 当前余额
```

这是**恒等式**：右边由左边定义得出，不构成任何校验。页面上把它作为「今日账面」等式条展示，让使用者看清数字怎么来的。

真正的校验是下面两种「与第二来源比」的口径。

### 6.2 第二来源对账

| `mode` | 触发条件 | `label` | 差异含义 |
| --- | --- | --- | --- |
| `ledger_vs_logs` | 传了 `--credit-ledger` | 积分流水 vs 日志 | 真正的账实差异，超容差要查 |
| `session_table` | 没传流水，但会话积分表与日志有交集 | 日志 vs 会话积分表 | 口径校验，不是账户误差 |
| `none` | 两者都没有 | 无第二来源 | 无法对账，报告显式说明 |

`session_table` 的差异来自**主键不同**：日志按 `messageId` 累计、会话积分表按 `conversation_request_id` 聚合。实测差异约 −1.0%，属正常口径差。

差异明细行结构（`reconcile.rows`，最多 60 行）：

```json
{"rid": "r1", "taskId": "tk_01a1444f", "name": "政策监测日报", "day": "2026-10-24",
 "logs": 15.5, "second": 15.6, "diff": -0.1, "diffPct": -0.65}
```

`taskId` 与 `day` 用于**抽屉里回链任务**：点开任意一行就能看到这次差异对应的执行链路。

---

## 6.3 收入台账 v3：单人快照 + 录入留痕

v2 的「复核人 ≠ 录入人」在单机场景下退化成了同一个人点两次，且不解决「事后能否还原当时录的是什么」。
v3 取消双人卡点，改用两样**可验证**的东西承担可审计性。

### 6.3.1 结构（`config/credit-income.json`，`version: 3`）

```json
{
  "id": "INC-20261029-01",
  "date": "2026-10-29",
  "type": "recharge",
  "amount": 100.0,
  "biz_no": "RECHG-20261029-001",
  "entered_by": "张三",
  "entered_at": "2026-10-29T09:12:03+08:00",
  "status": "confirmed",
  "snapshot": {"file": "vouchers/INC-20261029-01-b4dfe20b.png",
                "sha256": "b4dfe20b…", "bytes": 48213,
                "taken_at": "2026-10-29T09:10:11+08:00"},
  "revisions": [{"at": "…", "by": "…", "action": "CREATE",
                  "before": null, "after": {"amount": 100.0, "status": "draft"}},
                 {"at": "…", "by": "…", "action": "SUBMIT",
                  "before": {"status": "draft"},
                  "after": {"status": "confirmed", "snapshot_sha256": "b4dfe20b…"}}]
}
```

| 字段 | 规则 |
| --- | --- |
| `id` | `INC-YYYYMMDD-NN`，业务日 + 当日序号；**留痕主键，一经写入不改** |
| `snapshot.sha256` | 必填（`add` 强制）。凭证文件复制进 `config/vouchers/`，原文件不移动 |
| `revisions[].action` | `CREATE` / `EDIT` / `SUBMIT` / `REOPEN` / `VOID`，**只追加** |
| `status` | `draft`（草稿，只进实时余额）/ `confirmed`（已入账，唯一进账本）/ `void`（退回，两个口径都不计） |
| `legacy.reviewer` | v2 老字段，读入时收进 `legacy`，只作痕迹保留，不参与任何判定 |

### 6.3.2 留痕日志（`config/income-entry-log.jsonl`）

每行一条动作，字段含 `ts / id / action / by / host / before / after / snapshot_sha256`，外加两个哈希：

```text
prev_hash = 上一条的 hash（首条为 64 个 0）
hash      = sha256(本条除 hash 外的规范化 JSON，key 排序、无空格)
```

`verify` 逐行重算，出现 `LOG_CHAIN_BROKEN`（prev_hash 对不上）或 `LOG_TAMPERED`（内容与自身 hash 不符）即退出码 5。
**为什么做哈希链**：台账是账本侧数据，「事后补一条更早的记录」是最常见的伪装，链式哈希让补录必然断链。

### 6.3.3 凭证状态与入账门槛

| `snap` | 含义 | 能否入账 |
| --- | --- | --- |
| `ok` | 有哈希且复算一致 | ✅ |
| `nosnap` | 没有凭证 | ❌ 拒绝入账 |
| `missing` | 有哈希但文件不在 | ❌ 拒绝入账 |
| `tampered` | 复算哈希与台账不符 | ❌ 拒绝入账，需先处理凭证 |

报告里对应四个指标：`snapshotRate`（已附凭证）/ `evidenceRate`（可复算）/ `trailRate`（留痕覆盖）/
`noSnapConfirmed`（无凭证入账，应恒为 0）；另有 `tampered`、`missing` 计数，非 0 时在卡片副标题最前面以 `⚠` 提示。

---

## 6.4 余额凭证台账（`config/balance-snapshots.jsonl`）

### 6.4.1 为什么余额也要凭证

收入侧有 `snapshot + revisions + 哈希链` 三件套，余额侧原来什么都没有：两个数字敲进
`balance-state.json` 就算数。这留下一个很难堪的口子——**对账算出偏差 0，也没人知道那个
0 建在什么之上**。v2.1 §3.8 B4 要求「人工余额也需截图留痕」，本节就是它的落地结构。

原则只有一条：**没有截图，余额数字不进台账，也不该进状态文件。**

### 6.4.2 一条记录的字段

```json
{
  "ts": "2026-10-29 10:05:11",
  "taken_at": "2026-10-29 10:01:15",
  "date": "2026-10-29",
  "account": 1419.51,
  "day_open": 1500.0,
  "spent_before": 80.49,
  "imputed": true,
  "plan": "体验版",
  "note": "…",
  "shot": {
    "file": "vouchers/balance-20261029-100115-e29b676c.png",
    "sha256": "e29b676c…",
    "bytes": 8411,
    "ext": ".png",
    "copied_at": "2026-10-29 10:05:11"
  },
  "by": "张三",
  "host": "DESKTOP-7K2M9Q4",
  "prev_hash": "",
  "hash": "91622a6b…"
}
```

| 字段 | 含义 |
| --- | --- |
| `account` | **截图显示的余额**，实测值，不可改 |
| `taken_at` | 截图时刻（不是录入时刻） |
| `day_open` | 当日日初余额 = `account + 00:00~taken_at 的消耗` |
| `spent_before` | 上面那个差值，显式记下来，不让它无声地混进偏差 |
| `imputed` | `day_open` 是否由调用方显式给出。`false` 表示按截图值近似，报告会以 `B_SNAPSHOT`（快照时点漂移）呈现 |
| `shot.sha256` | 凭证内容哈希，`verify` 每次重算比对 |
| `prev_hash` / `hash` | 哈希链，删一行或改一个字都会断 |

### 6.4.3 快照时刻 ≠ 日初余额

截图上只有「此刻余额」，日终对账要的是「当日日初」。两者差一个「00:00 到截图那一刻已经
消耗掉的部分」。**这个差值必须显式记下来**，否则它会悄悄并进当日偏差里，让一个本来干净的
账看起来像是出错了。

`--day-open` 就是用来记这个的：

```bash
# 截图 10:01:15 显示 1,419.51；当日 00:00~10:01:15 已有 4 笔共 80.49
python3 -m hosts.commands.balance_snapshot add --account 1419.51 --day-open 1500.00 \
    --shot 截图.png --taken-at "2026-10-29 10:01:15" --apply
```

不填 `--day-open` 也能录，但记录里 `imputed=false`，报告会用 `B_SNAPSHOT` 标出这点漂移——
宁可显式承认，也不假装截图发生在 00:00。

### 6.4.4 `verify` 会检出什么

| 代码 | 含义 |
| --- | --- |
| `SHOT_MISSING` | 凭证文件不在（被删/被移走） |
| `SHOT_TAMPERED` | 文件内容变了，SHA256 与台账不符 |
| `NO_SHOT` | 该条记录没有凭证 |
| `CHAIN_BROKEN` | `prev_hash` 串不上，中间被删过记录 |
| `LOG_TAMPERED` | 行内容与自身 `hash` 不符，被改过 |

有任何一项 → 退出码 `5`。

### 6.4.5 改数必须换图

`balance_state.set_manual(today_balance=…)` 若没同时给新凭证，旧快照会被标
`stale: true` 并附 `stale_reason`。理由很直接：**不能让 8 点那张图去背书 10 点改的数**，
否则「数字与凭证绑定」就只是个摆设。报告里 `stale` 的快照显示为「凭证已失效」而不是「已校验」。

---

## 7. 模型切换一致性保护

`MODEL_SWITCH` 是**风险信号**，不是浪费：

- 不计入「止损空间」，`waste` 恒为 0；
- 只在台账对账区域呈现：`count` / `ratio` / `highConfidence` / `items` / `tasks` / `note`；
- 无流水时间线时 `note` 明确写「只能给出切换次数，无法判定是否由余额驱动」。

**为什么重要**：余额不足触发的静默降级会让长链路任务中途换模型，交付一致性受损。
建议对关键任务显式锁定模型（属第三档建议，不自动执行）。

---

## 8. 校验清单

1. `initial_credits` 与 `today_balance` 是否都填了（缺一个就不产生级别）；
2. `today_balance_date` 是否**不早于**数据窗口最后一天（早于即跨日过期，需重填）；
3. `todaySpent` 是否与「结算日当天任务积分之和」对得上（可从 `tasks.csv` 按日期汇总复核）；
4. 三条阈值是否满足 `warn ≤ crit ≤ red`，且语义是**消耗百分比**而不是剩余百分比；
5. 第二来源对账是否为 `none`（是则说清楚无法对账，不要含糊带过）；
6. `audit.py --budget-only` 跑一次，确认 `source` / `consumePct` / `level` / 退出码与预期一致。
