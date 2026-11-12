# 积分折算率与更新流程

积分统计的**第一优先**永远是客户端上报的 `credit`（日志里的 `credit consumed`）。
本文件定义的折算率只在**没有上报 credit** 时兜底，用于让 token 仍能进入统计。

---

## 1. 什么时候用折算率

| 场景 | 是否折算 | 说明 |
| --- | --- | --- |
| 日志有 `credit consumed` | ❌ 不折算 | 直接采信，步骤标 `est=false` |
| 同一 `messageId` 有多次上报 | ❌ 不折算 | 取最大值，属累计口径而非求和 |
| 有 `qua.end` 的 token，但无对应 credit 行 | ✅ 折算 | 结果标 `est=true`，报告可据此提示估算占比 |
| 两者都没有 | ❌ 记为 0 | 不臆造 |

折算公式：

```
credits = input_tokens / 1000 × rate.input + output_tokens / 1000 × rate.output
```

`rate_unit` 固定为 `credits_per_1k_tokens`。

---

## 2. 折算率表

文件：`config/credit-rules.json`

| 模型 | input（积分/1k tok） | output（积分/1k tok） | 输出/输入倍数 |
| --- | --- | --- | --- |
| `deepseek-v4.1-flash` | 0.00052 | 0.00208 | 4× |
| `deepseek-v4-flash` | 0.00052 | 0.00208 | 4× |
| `glm-5.3-flash` | 0.00120 | 0.00480 | 4× |
| `hy3` | 0.00150 | 0.00600 | 4× |
| `kimi-k2.7` | 0.00220 | 0.00880 | 4× |
| `fast-model` | 0.00294 | 0.01176 | 4× |
| `minimax-m3` | 0.00300 | 0.01200 | 4× |
| `glm-5.3` | 0.00720 | 0.02880 | 4× |
| **default**（未列出的模型） | 0.00200 | 0.00800 | 4× |

### 2.1 关于「输出恒为输入的 4 倍」

这不是巧合，而是取数时观察到的**实际计费规律**：所有已接入模型都保持 output = 4 × input 的比例。
因此新增模型时，最省事的做法是只确认 input 单价，output 直接乘 4；若某天出现非线性定价，
按实际值写，公式本身支持两者独立。

### 2.2 default 的作用

未在表内出现的模型（新上线、内部代号、用户自定义）走 `default`，保证统计不中断。
`default` 取的是全部已知模型的 input 中位数（0.00200），属于**保守取值**：
既不会因为太低而低估消耗，也不会因为太高而制造虚假警报。

代价是：如果实际单价远低于 default，估算会偏高。所以报告应把 `est=true` 的步骤占比显性化，
让用户知道「这个数字有多少是估的」。

---

## 3. 更新流程

### 3.1 何时需要更新

任一条件成立就该更新：

1. 客户端上线新模型，日志里出现未登记的 `model` 值；
2. 观察到「有 token 无 credit」的比例上升（说明有新链路不再上报 credit）；
3. 官方调价。

发现未登记模型的方法：

```bash
python3 -m hosts.commands.audit --format json --out ./out --yes
# 打开 summary.json，看 models 里是否有折算率表外的名字
```

或直接查报告「运营分析」的模型运行情况表，凡出现在图里但不在上表的，都是未登记。

### 3.2 怎么加

在 `models` 里加一行，**不要动 `default`**：

```json
"my-new-model": {"input": 0.00180, "output": 0.00720}
```

同时更新 `effective_date`：

```json
"effective_date": "2026-11-01"
```

### 3.3 历史是否追溯

**不追溯**。`note` 字段已声明：

> rate 取本机实测均值，更新后历史积分不追溯。

理由：折算率只是兜底，改动它会静默改写历史报表，导致「同一个月的报告两次跑出不同数字」，
审计工具最忌讳这一点。需要重算历史时，显式用 `--credit-rules <旧文件>` 重跑，而不是覆盖默认配置让历史漂移。

### 3.4 校验

```bash
python3 -m hosts.commands.audit --budget-only --yes        # 能看到折算后的 P95 与日均
python3 -m hosts.commands.audit --format json --out ./out --yes
# 对比 summary.json 的 totals.credits 与上次结果，确认变化可解释
```

---

## 4. 与 F7 模型对比的关系

F7「对比建议」用的是**实测积分/任务**（真实 credit），不是折算值，因此：

- 折算率误差不会影响「该换哪个模型」的结论方向；
- 但若某模型的步骤大量 `est=true`，它的成本数字就是估的，样本再大也不该给强建议。

因此模型对比的最小样本门槛是 **n ≥ 3**，且报告中样本不足的组合只展示、不推荐：

> 对比为真实观测而非受控实验；样本 <3 的组合只展示不推荐。

---

## 5. 常见问题

**Q：能不能按 token 直接统计，不看积分？**
可以，`tasks.csv` 与 `steps.csv` 都有 token 列，`summary.json` 也有 `inputTokens` / `outputTokens`。
但积分才是**实际扣费口径**，tokens 只是过程量，两者会因缓存、批量折扣而不等比。

**Q：为什么折算后总积分和账户扣费对不上？**
因为折算只覆盖「有 token 无 credit」的部分，且 default 是估值。先看 `est` 占比，
再用 `--credit-ledger` 做真实对账（见 `credit-ledger-schemas.md`）。

**Q：能不能让工具自动去拉官方价目表？**
不能。本 Skill 的网络白名单为空，禁止任何外联。价格表必须由人维护，这是刻意的设计——
价格变化会影响所有历史解读，必须留下人的决策痕迹。
