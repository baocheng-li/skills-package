# Skill 形态 · 实施部署步骤

> 读者：要把「积分对账审计应用」交给 Agent（WorkBuddy / CodeBuddy / 支持 Skill 的平台）自动跑的人。
> 目标：Agent 说一句话就能拿到结论 JSON，不需要人点页面。

---

## 1. 形态说明

Skill 不提供界面。它是一次**函数调用**：给参数、回 JSON。

| 项 | 值 |
| --- | --- |
| 入口 | `python -m hosts.skill` |
| 传输 | stdin / stdout（`--input '<json>'` 或管道喂入） |
| 输出 | 单个 JSON 对象，无表格、无进度条 |
| 依赖 | Python ≥ 3.9，**零第三方包** |
| 权限 | 只读本机日志，不写回客户端配置 |

返回结构：

```json
{
  "verdict": "一句话结论（含告警级别与关键数字）",
  "metrics": { "todaySpent": 0, "balance": 0, "warnRatio": 0, "...": "键名即口径" },
  "warnings": ["未配置初始积分", "凭证失效", "T 日待对账"],
  "advice": [{"level": "立即", "title": "...", "detail": "..."}],
  "artifacts": {"report": "out/report.html"}
}
```

**为什么不做「精简版」**：模型不会去翻页面上的小字，它只读这几个字段。
所以 `metrics` 给全、`warnings` 给明，宁可长一点也不要让模型猜。

---

## 2. 前置检查

```bash
python -V                      # 需要 3.9+
python -c "import sys; print(sys.platform)"
ls ~/.workbuddy                # macOS / Linux
dir %USERPROFILE%\.workbuddy   # Windows
```

日志目录不存在时，Skill 不会崩，而是返回 `warnings: ["未发现可分析的日志目录"]`
——装完先跑一次确认没有这条。

---

## 3. 部署步骤

### 3.1 取包

```bash
# 方式 A：从源码发布
python publish.py --target skill --clean
# 产物：dist/skill/

# 方式 B：直接拷贝源码目录（Skill 需要完整代码树）
#   core/ adapters/ renderer/ hosts/ references/ config/ manifest.json SKILL.md
```

`dist/skill/skill.json` 是工具声明：

```json
{
  "name": "workbuddy-cost-audit",
  "entry": "python -m hosts.skill",
  "input":  {"last": "7d", "days": 0, "project": null, "html": false},
  "output": "json",
  "readonly": true,
  "offline": true
}
```

### 3.2 首次配置（必做）

Skill 的账要能算，必须知道「起点」和「今天余额」：

```bash
cd dist/skill
python -m hosts.commands.audit --set-initial 1000 --set-initial-date 2026-10-01
python -m hosts.commands.audit --set-today-balance 1419.51
```

配置写在 `config/balance-state.json`。之后每天只更新余额这一个数。

### 3.3 注册到 Agent 平台

把整个 `dist/skill/` 作为 Skill 包提交，`SKILL.md` 是给人看的方法论、
`skill.json` 是给平台看的工具声明。平台侧只需要知道一件事：

```bash
python -m hosts.skill --input '{"last":"7d"}'
```

如果平台要求「工作目录 + 命令」两段式，工作目录填包根，命令填上面这一行。

### 3.4 冒烟验证

```bash
cd dist/skill
echo '{"last":"7d"}' | python -m hosts.skill | python -m json.tool | head -20
```

期望：输出合法 JSON，`verdict` 非空，`metrics` 里 `balance` / `todaySpent` 有数值。
同时需要出报表时：

```bash
python -m hosts.skill --input '{"last":"7d","html":true}'
# artifacts.report 指向生成的 out/report.html
```

---

## 4. 参数

| 参数 | 含义 | 默认 |
| --- | --- | --- |
| `last` | 时间窗口，`7d` / `30d` / `today` | `7d` |
| `days` | 近 N 天（与 `last` 二选一，优先 `days`） | `0` |
| `project` | 只看某个项目 | 全部 |
| `html` | 是否同时生成报告文件 | `false` |

---

## 5. 升级

```bash
cp -r <新版本>/core <新版本>/adapters <新版本>/renderer <新版本>/hosts dist/skill/
cp <新版本>/manifest.json <新版本>/SKILL.md dist/skill/
python deploy/package.py --merge-config dist/skill/config   # 补新增配置字段
```

`--merge-config` 只补新键，不动已有值——`balance-state.json` 里的钱不会被覆盖。

---

## 6. 卸载

删除包目录即可。若在 Agent 平台注册过，先在平台侧移除该 Skill 条目。
配置与产物随目录一起删；只想清数据就删 `config/balance-state.json` 与 `out/`。

---

## 7. 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `warnings` 含「未发现可分析的日志目录」 | 平台容器里没有挂载 `~/.workbuddy` | 把宿主日志目录挂进容器，或用 `--root` 指向 |
| `balance` 为 `null` | 没设初始积分 / 今日余额 | 跑 §3.2 两条命令 |
| 输出里混进非 JSON 文本 | 有人往 stdout 打了日志 | stdout 是协议通道，调试信息只能进 stderr |
| `rc=2` | 有黄色告警 | 这是正常结果，不是失败 |
| JSON 中文变成 `\uXXXX` | 终端编码 | 用 `| python -m json.tool --no-ensure-ascii` 看 |
