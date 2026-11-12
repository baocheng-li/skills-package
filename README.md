# 工作常用技能包

WorkBuddy / CodeBuddy 技能包的集合。每个子目录是一个**可直接安装**的独立技能包，
自带 `SKILL.md`、依赖与文档，拷到技能目录或打包上传即可使用，互不依赖。

## 包含的技能

| 技能 | 标识 | 版本 | 用途 |
|---|---|---|---|
| 积分对账审计应用 | `workbuddy-cost-audit` | 3.4.1 | 只读采集本机 Agent 日志与积分上报，输出四个一级菜单共十个页面的离线审计报告 |

### 积分对账审计应用

回答「积分/token 花了多少、钱花在哪、哪些任务在浪费、余额还剩多少、账实是否相符」。
对账口径为 T+1：日初 = 日收 + 上一日日结余额，日结 = 日初 − 日耗，偏差 = 账户余额 − 日结。

- 全程**不联网**、**不写回**客户端配置、**不自动处置**任何任务
- 不截屏、不 OCR、不接客户端调试端口
- 已消耗积分不可追回，「回收调整」只做确认 / 止损 / 归因 / 留证，不改余额

详见 [`workbuddy-cost-audit/README.md`](workbuddy-cost-audit/README.md)。

## 安装

**方式一：客户端导入（推荐）**

WorkBuddy → 技能 → 从本地导入，选择 Release 里的
`workbuddy-cost-audit-skill-3.4.1.zip`。

**方式二：直接放目录**

把子目录整个拷到技能目录，重启客户端即可识别：

```
Windows   %USERPROFILE%\.workbuddy\skills\
macOS     ~/.workbuddy/skills/
```

**方式三：命令行调用**

```bash
cd workbuddy-cost-audit
python -m hosts.skill --input '{"last":"7d","html":true}'
```

## 目录约定

每个技能包的目录名必须与 `SKILL.md` frontmatter 里的 `name` 一致——
平台拿其中一个当技能 id，两边不同会出现「装上了、列表里有、但叫不出来」。

```
<skill-name>/
├── SKILL.md          技能定义（frontmatter 含 name / description / license）
├── manifest.json     元信息与变更记录
└── ...               代码、配置与文档
```

## 下载

预打包的安装包、桌面版与源码见 [Releases](../../releases)。

## 许可

MIT，见 [LICENSE](LICENSE)。
