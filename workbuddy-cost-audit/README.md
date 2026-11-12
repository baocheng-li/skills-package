# 积分对账审计应用 · Skill 包

## 怎么用

Agent 侧调用一次、拿到结论 JSON：

```bash
python -m hosts.skill --input '{"last":"7d"}'
# 需要同时出给人看的报告时加 --html
python -m hosts.skill --input '{"last":"7d","html":true}'
```

返回字段见 `core/models.py` 与 `references/report-template.md`：

- `verdict` 一句话结论
- `metrics` 全部会被引用的数字（键名即口径）
- `warnings` 需要注意的事（未配置 / 凭证失效 / 待对账）
- `advice` 三档建议
- `artifacts` 产物路径

## 放进 Agent 平台

把本目录整体作为一个 Skill 包提交即可；`skill.json` 是工具声明，
`SKILL.md` 是给人看的方法论说明。

## 边界

- 只读：不写回客户端配置、不处置任务
- 离线：不联网、不上传
- 不预测：所有「止损空间」都是窗口内已发生的消耗
