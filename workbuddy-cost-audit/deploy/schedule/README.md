# 每日积分消耗读取 · 定时任务

每天跑一次采集，把当天的消耗 / 余额 / 预警级别固化成一行的日账
`out/credit-daily.jsonl`，同时刷新 `out/report.html`。

**不联网、只读本机日志、不经 WorkBuddy 对话，因此不消耗对话额度。**

这条任务已接入报告的「审计控制 → 任务设置」，在任务列表里是带「系统任务」标记的那一行。

## 一、为什么单独写这一层

项目里的 `refresh_report` 已经能定时重跑采集（并发锁 + 运行日志 + 退出码联动），
但它只产「当前时刻的报告」。报告是离线单文件，数字在生成那一刻就固定了，
想看「每天花多少」必须有独立于报告的时间序列——这就是日账存在的理由。

日账**一天一行**，同日再跑覆盖当日那一行，所以：

* 定时任务漏跑一天，事后补跑一次，序列补上，不会出现空洞；
* 同一天误跑两次，不会留下两条数字不同的记录；
* 采集失败（退出码 1）**不写日账**——宁可缺口，也不写一行空数据。
  缺口一眼看得出来，脏数据会一直被当成真的。

## 二、「既定时间」只有一处真源

`config/schedule.json`：

```json
{
  "tasks": [{
    "id": "daily-credit",
    "name": "每日积分取数与日账",
    "at": "09:05",
    "systemTask": "CostAudit\\DailyCredit",
    "module": "hosts.commands.daily_credit",
    "args": "--out out --format html --quiet --log out/daily_credit.log",
    "script": "deploy/schedule/daily_credit.bat"
  }]
}
```

页面、`--install`、`--print-schedule`、打包脚本都读这一份。改时间只改这里，再跑一次
`--install`——不要出现「页面上写 09:05、系统里注册 08:00」这种两处各说一套。

页面之所以能显示「已注册 / 未注册 / 未探测」，是因为采集时读了三个地方：

| 来源 | 回答的问题 |
| --- | --- |
| `config/schedule.json` | 我们**打算**几点跑 |
| `out/schedule-state.json` | 系统里**实际**注册成什么样（`--status` 采回） |
| `out/credit-daily.jsonl` | **真的**跑过几天，中间断没断 |

三者不一致是常态，页面把差异明写出来，而不是挑一份好看的显示。

## 三、文件

| 文件 | 作用 |
| --- | --- |
| `hosts/commands/daily_credit.py` | 主程序：采集 → 取当天结果 → 写日账 |
| `deploy/schedule/daily_credit.bat` | Windows 批处理（**纯 ASCII**，见下方说明） |
| `deploy/schedule/daily_credit.sh` | Unix / macOS shell |
| `config/schedule.json` | 任务定义（时间 / 系统任务名 / 参数），唯一真源 |
| `out/credit-daily.jsonl` | 日账，一天一行 |
| `out/schedule-state.json` | 上次探测到的系统注册状态 |
| `out/daily_credit.log` | 每次运行的输出（追加，不覆盖） |

批处理为什么一个汉字都不写：`cmd.exe` 按 OEM 代码页**逐字节**解析 `.bat`，
正文里的中文会让行边界错位，实测会把注释后半截当命令执行。
所有说明性输出交给 Python 那一侧，脚本本身只做 `cd` + 调 Python 两件事。

## 四、注册

一条命令，普通权限即可（写的是「当前用户」的计划任务，不需要管理员）：

```
python -m hosts.commands.daily_credit --install
```

它做三件事：落盘脚本 → 注册计划任务 → 重新探测并写 `out/schedule-state.json`。

注册优先走 PowerShell 的 `Register-ScheduledTask`，因为：

* 能设**工作目录**。`schtasks` 没有这个参数，任务启动时在 `C:\Windows\System32`，
  脚本里的 `--out out` 会解析到那儿——跑起来不报错，只是什么都没写进 `out/`；
* 能直接用 `pythonw.exe` 起进程，**不弹黑框**。每天早上 9:05 在桌面闪一个命令行窗口，
  是那种「用一天就想卸载」的体验。

PowerShell 调不通时自动退回 `schtasks`（挂批处理，窗口会闪一下），两条都失败才报错，
并把可手工执行的命令打印出来。

Unix 侧**不自动改 crontab**（避免覆盖别人的条目），`--install` 会打印该加的那一行：

```
5 9 * * * cd /path/to/workbuddy-cost-audit && python3 -m hosts.commands.daily_credit --out out --format html --quiet --log out/daily_credit.log
```

时间选 09:05 的两个原因：凌晨 00:00 已被 T+1 日终对账占用；早晨 9 点后当天日志
通常已落定，读到的数是稳定的。

## 五、日常查看

```
python -m hosts.commands.daily_credit --status      # 是否已注册 + 下次运行时间
tail -n 5 out/credit-daily.jsonl                    # 最近几天
tail -n 20 out/daily_credit.log                     # 最近一次运行输出
```

`--status` 会顺带写 `out/schedule-state.json`。**改完配置不重新探测，页面会一直显示旧状态**，
所以改完时间请跑一次 `--status`（或直接 `--install`）。

日账每行是一个 JSON 对象：

```json
{"date":"2026-11-06","at":"2026-11-06T09:05:11","wallDate":"2026-11-06","lagDays":0,
 "spent":27.0,"balance":1053.5,"ratio":2.56,"level":"ok","levelText":"正常",
 "exitCode":0,"windowSpent":27.0,"thresholds":{"warn":10.0,"crit":20.0,"red":30.0}}
```

`lagDays` 是**数据锚点日落后自然日的天数**。日志停更时 `date` 会冻在最后一天，
日账看着天天在记、其实天天在抄同一笔旧账；这个字段让读的人一眼看出是哪种情况。
`lagDays ≥ 1` 时程序会额外打印一行提示，不会把旧账静默记成今天。

## 六、启停

「把它关了」在页面和系统里是两件事。页面是离线单文件，改不了任务计划程序，所以启停
做成一条命令，三个动作一次做完：

```
python -m hosts.commands.daily_credit --disable    # 改配置 + 系统停用 + 回写状态
python -m hosts.commands.daily_credit --enable     # 反过来
```

| 动作 | 落点 |
| --- | --- |
| 改配置 | `config/schedule.json` 的 `tasks[0].enabled`，**只动这一个字段**，别的键（含 `_comment`）原样保留 |
| 同步系统 | `Disable-ScheduledTask` / `Enable-ScheduledTask`；失败退回 `schtasks /Change /TN "CostAudit\DailyCredit" /DISABLE` |
| 回写状态 | `out/schedule-state.json` 的 `state` 变成 `Disabled` / `Ready`，页面下次打开就是新状态 |

顺序是**先写配置、再动系统**。反过来一旦中途失败，就变成「系统停了、配置还说开着」，
两边都说不清哪个是对的。`--disable` 打印的每一行都在说这三步走到哪儿了，不用猜。

页面上的开关点下去会弹一条同步提示：页面口径立即生效（列表、KPI、筛选都按新口径算），
系统那边要执行这条命令才跟上。两边不一致时状态列写「待同步」，**不并排显示两个标签**——
并排会让人以为其中一个是历史值，看不出到底哪个是真的。

系统任务没注册时 `--disable` 不算失败：配置照写，提示「没有什么可停的，注册后按这个状态生效」。

## 七、参数

| 参数 | 说明 |
| --- | --- |
| `--at 09:05` | 每日触发时刻；不填则取 `config/schedule.json` |
| `--via auto` | 注册通道：`auto` 先 PowerShell 再 schtasks，也可固定为 `powershell` / `schtasks` |
| `--log out/daily_credit.log` | 输出追加到日志。**计划任务用 pythonw 跑，没有控制台，不加这个等于什么都不留** |
| `--last 30d` | 统计窗口，**默认 30d**。默认贴近实时的 7 天窗口会让旧数据一天掉出去一天，报告越跑越薄 |
| `--out out` | 产物目录 |
| `--format html` | 报告格式；`all` 会把 JSON / CSV 一并重写 |
| `--balance-state` | 余额状态文件（不填则用 `config/balance-state.json`） |
| `--no-collect` | 不重跑采集，直接用现有 `budget_status.json` 写日账 |
| `--print-schedule` | 只打印脚本正文、schtasks 命令与等价的 PowerShell |
| `--disable` / `--enable` | 停用 / 启用：改配置、同步系统任务、回写状态，一次做完 |

退出码沿用告警口径：`0` 正常 / `2` 黄色 / `3` 橙色 / `4` 红色 / `1` 失败。
计划任务里看到 `2` 不代表跑挂了，是「跑到一半发现有告警」——这正是它该上报的东西。

## 八、取消与排错

```
python -m hosts.commands.daily_credit --uninstall
```

| 现象 | 先查 |
| --- | --- |
| 任务显示「上次运行结果 0x1」 | `out/daily_credit.log`；多半是解释器路径失效（`.venv` 重建过 / Python 升级） |
| 日志里一片静默，什么都没有 | 注册时用了 `python.exe` 而不是 `pythonw.exe` 且没带 `--log`，输出无处可去 |
| 日账长期是同一天 | 看 `lagDays`：本机日志没更新，或客户端换了数据目录（`--root` 指定） |
| 报告数字比上次少 | 检查 `--last`：窗口收窄会把旧数据切掉 |
| 页面一直显示「未注册」 | 没重新 `--status`，`out/schedule-state.json` 还是旧的 |
| 页面显示「待同步」 | 页面口径与系统不一致：执行弹窗里那条 `--disable` / `--enable`，然后重新生成报告 |
| 点了停用还在跑 | 只点了开关没执行命令。查 `schtasks /Query /TN "CostAudit\DailyCredit" /FO LIST` 的 Status 是否 `Disabled` |
| 任务没触发 | `Get-ScheduledTaskInfo -TaskPath '\CostAudit\' -TaskName 'DailyCredit'` 看下次运行时间 |

解释器选择有个坑值得记一笔：程序优先用 `pythonw.exe`，并且**避开名字里带 `.old` 的目录**
（那是版本升级留下的备份副本，清理磁盘时第一个被删）。把计划任务钉在那种路径上，
今天能跑、清理完就静默失败。
