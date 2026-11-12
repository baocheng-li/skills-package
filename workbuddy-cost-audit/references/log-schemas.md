# 日志与数据库字段字典

本文件说明 `parse_logs.py` + `adapters/workbuddy.py` 实际读取的每一类数据、字段来源、判定口径。
**所有采集均为只读**：数据库先复制 `db + -wal + -shm` 到临时目录再以 `mode=ro` 打开，避免与运行中的客户端抢锁。

---

## 1. 数据源清单

| 相对路径 | 格式 | 是否必需 | 缺失后的降级 |
| --- | --- | --- | --- |
| `<home>/logs/**/*.log` | 文本行（带时间戳前缀） | 必需 | 无积分数据，任务数为 0，直接结束 |
| `<home>/workbuddy.db` | SQLite | 可选 | 无会话标题/自动化信息，`meta.capabilities.balance=false`，流水对账不可用 |
| `<home>/projects/**/*.jsonl` | JSON Lines | 可选 | 无工具步骤，F5 的 `RETRY_STORM` / `TOOL_LOOP` 退化为 `insufficient` |
| `<home>/startup-seed-version.json` | JSON | 可选 | 版本显示 `unknown` |

`<home>` 依次探测：`~/.workbuddy`、`~/.codebuddy`、`~/.claude`、`~/.openclaw`。

---

## 2. 日志行格式

### 2.1 时间戳前缀

```
[2026/10/26 14:32:07.812] ...
```

正则：`^\[(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]`

毫秒缺位时补 0（右对齐）。解析失败的行整行跳过，不中断文件扫描。

### 2.2 `credit consumed` —— 步骤级积分（权威值）

```
[...] credit consumed sessionId=<sid> requestId=<rid> messageId=<mid> model=<model> credit=<float>
```

| 字段 | 含义 | 用途 |
| --- | --- | --- |
| `sessionId` | 会话 id | 关联 `sessions` 表 |
| `requestId` | 请求 id | 归并同一轮对话的多个 step |
| `messageId` | 消息 id | **步骤主键**，同一 id 多次上报取 `credit` 最大值（流式分片累计口径） |
| `model` | 模型标识 | 模型占比、模型切换判定、积分折算兜底 |
| `credit` | 本步消耗积分 | 全部积分统计的唯一来源 |

> **关键口径**：同一 `messageId` 可能因流式输出被上报多次，`credit` 为累计值。
> 取最大值而非求和，是避免重复计费的核心处理；跨 home 重复出现时同样只计一次。

### 2.3 `qua.end:` —— 请求级用量

```
[...] qua.end: {"message_id":"...","model":"...","input_tokens":123,"output_tokens":45,...}
```

JSON 体字段（按需读取，缺字段不报错）：

| 字段 | 含义 | 缺失影响 |
| --- | --- | --- |
| `message_id` | 步骤主键，与 `credit consumed` 对齐 | 缺失则该条被丢弃 |
| `conversation_request_id` | 请求 id，任务聚合的主键 | 缺失则该步无法归入任何任务 |
| `model` | 模型标识 | 用 `credit consumed` 的 `model` 兜底 |
| `prompt_tokens` | 输入 token（含缓存） | `CONTEXT_BLOAT` 退化为 `insufficient` |
| `completion_tokens` | 输出 token | token 合计偏低 |
| `total_tokens` | 总 token（`prompt + completion`） | 不用于计算，仅交叉校验 |
| `ttft_ms` | 首 token 时延 | 报告不展示，保留在原始字段 |
| `total_cost_ms` | 单步总耗时 | 用相邻步骤时间戳差兜底 |
| `request_type` | `chat` / `conversation_topic` / `compress` | 后两者归入「系统开销」，不参与任务统计 |
| `finish_reason` / `finish_flag` | 结束原因 | 无法区分「正常结束」与「被截断」，`status` 只用 `err` 判定 |
| `model_error_code` | 非空表示模型侧报错 | 该步标记 `err` |

> **没有缓存字段**：真实 `qua.end` 里不存在 `cache_read_tokens` 一类的键，
> 这就是 `CACHE_MISS` 恒为 `unsupported` 的根因（见第 6 节）。

适配器额外写入两个内部字段：`_ts`（可读时间）、`_epoch`（浮点秒，用于排序与窗口比较）。

---

## 3. 数据库表

### 3.1 `sessions`

```sql
select id, title, cwd, status, created_at, updated_at, source_mode,
       model, mode, is_background_automation, conversation_origin, permission_mode
from sessions
```

| 字段 | 用途 |
| --- | --- |
| `id` | 与 `sessionId` 关联 |
| `title` | 任务名（报告里的任务标题） |
| `cwd` | 项目归属推断依据 |
| `status` / `updated_at` | 结束时间与状态 |
| `is_background_automation` / `conversation_origin` | 触发方式判定：`人工` / `定时` / `后台自动化` |
| `model` | 会话级模型（仅当步骤级缺失时兜底） |

### 3.2 `session_usage`

`credit_json` 为 `{conversation_request_id: credit}` 的映射。

> **对账口径差异（重要）**：`session_usage.credit_json` 的 key 是 **conversation_request_id**，
> 日志侧主键是 **messageId**。两者存在交集但并非一一对应，因此对账只取**交集**部分比较，
> 并把结果标注为「日志 vs 会话积分表」，**不等于账户余额误差**。

### 3.3 `automations` / `automation_runs`

| 字段 | 用途 |
| --- | --- |
| `automations.name` | 定时任务名 |
| `automations.rrule` / `schedule_type` | 译为可读调度（如「每周一 08:00」）与 `freq` |
| `automation_runs.created_at` / `metadata_json` | 执行时间、`runKind`（按时/补跑）、结果证据、关联会话 |
| `automation_runs.status` | 成功 / 失败，用于 `succ` |

`SCHEDULED_SPIKE` 需要单次执行消耗的分布；`runs < 3` 时规则置 `insufficient`。

---

## 4. 项目会话文件 `projects/**/*.jsonl`

只取三类记录，其他类型忽略：

| `type` | 取用字段 | 用途 |
| --- | --- | --- |
| `function_call` | `name`、`arguments`、`call_id` | 工具名、工具参数（`TOOL_LOOP` 的「同参数」判定）、步骤序号 |
| `function_call_result` | `call_id`、`is_error` / `error` | 工具失败标记（`RETRY_STORM`） |
| 消息记录 | 用户首条指令文本（截断 60 字） | 任务类型关键词匹配的次选输入 |

**清洗规则**：

1. `<system-reminder>...</system-reminder>` 整段替换为空格；
2. 连续空白压缩为单个空格；
3. 以 `<` 开头的文本（残留标签）丢弃，不作为分类依据；
4. 只保留每轮对话的**首条**用户指令，避免长对话把关键词权重放大。

**项目归属**：优先 `sessions.cwd` → `plugin_context_json.projectResources`（常为空）→ `session.project_id`（常为 NULL）。
三者都取不到时映射为 `未绑定项目`，**不臆造项目名**。报告中会给出 `unboundProjectRate` 明示占比。

---

## 5. 统一中间结构

适配器输出 `(data, ctx)`，`data` 供后续 F1—F7 使用：

```jsonc
{
  "meta": {
    "generatedAt": "2026-10-26T10:12:33",
    "windowDays": 33,
    "span": ["2026-09-24", "2026-10-26"],
    "counts": {"tasks": 110, "steps": 7313},
    "homes": ["C:/Users/x/.workbuddy"],
    "capabilities": {
      "cacheTokens": false,   // 决定 CACHE_MISS 是否 unsupported
      "balance": false,       // 决定 F6 是否只能到 L4
      "ledger": false,        // 是否有积分流水
      "toolArgs": true        // TOOL_LOOP 能否比较参数
    },
    "warnings": []
  },
  "tasks": [ /* 见下 */ ],
  "daily": [ {"day": "...", "credits": 0, "tasks": 0} ],
  "models": [ {"model": "...", "credits": 0, "tasks": 0} ]
}
```

### 任务对象

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | 任务 id（messageId 聚合后的稳定键） |
| `name` | str | 标题，缺省截断首条指令 |
| `ts` / `day` | str | 开始时间 / 日期 |
| `credits` | float | 任务积分合计（步骤级累加） |
| `inTok` / `outTok` | int | token 合计 |
| `nModelSteps` / `nTools` | int | 模型步数 / 工具调用数 |
| `duration` | float | 秒 |
| `retry` | int | 重试次数（同 requestId 内的重复 step） |
| `status` | str | `成功` / `失败` |
| `trigger` | str | `人工` / `定时` / `后台自动化` |
| `type` | str | 任务类型（7 类，见 `classification.md`） |
| `project` | str | 项目名或 `未绑定项目` |
| `modelChain` | str | 模型链，如 `glm-5.3→fast-model`（发生切换时） |
| `mainChannel` | str | 主用模型 |
| `cwd` | str | 工作目录（报告里截断 60 字） |
| `steps` | list | 步骤数组（见下） |

### 步骤对象

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `i` | int | 步骤序号 |
| `kind` | str | `model` 或 `tool` |
| `model` | str | 模型标识（`kind=model`） |
| `inTok` / `outTok` | int | token |
| `credits` | float | 积分 |
| `ms` | int | 毫秒 |
| `ts` | str | 时间戳（报告只保留 `HH:MM:SS`） |
| `phase` | str | 阶段标签 |
| `retry` | int | 重试序号 |
| `est` | bool | 是否由折算率估算（`credit` 缺失时） |
| `tool` / `arg` / `err` / `snippet` | str/bool | 工具名 / 参数指纹 / 是否报错 / 结果摘要（仅 `kind=tool`） |

---

## 6. 已知采集限制

1. **缓存字段普遍缺失**：`cache_read_tokens` 未上报 → `CACHE_MISS` 恒为 `unsupported`，报告不得写成「缓存正常」。
2. **`finish_reason` 覆盖不全**：无法稳定区分截断与正常结束，失败判定只依赖工具错误与显式 error 字段。
3. **`MODEL_SWITCH` 只能给次数**：无流水时间线时无法判定切换是否由低余额驱动，报告标注「只能给出切换次数」。
4. **跨设备不可用**：本 Skill 只读本机文件，多设备用量需各自跑一遍后合并 `summary.json`。
5. **时区**：日志时间戳为本地时间，不做时区转换；`qua.end` 若带 `Z` 或偏移，统一折算为 UTC 再去掉时区，可能与本地时间差一个时区，对账时按日期聚合会吸收这个误差。
