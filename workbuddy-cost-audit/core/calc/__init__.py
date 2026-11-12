#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计算模块集合 —— 全部是「输入 dict，输出 dict」的纯函数。

| 模块        | 职责                                              |
|-------------|---------------------------------------------------|
| analyze     | F2 总览聚合（指标、横幅、数据源声明）              |
| anomaly     | F5 异常规则命中与止损空间                          |
| budget      | F6 余额与预警（三口径 + 阈值分级）                 |
| classify    | 任务分类与复杂度分层 L1–L4                         |
| compare     | F7 模型对比与三档建议                              |
| ledger      | 台账 CSV（任务 / 步骤）                            |
| monitor     | 任务监测（定时 / 后台 / 技能 / 隐藏调用）          |
| query       | 时间与维度过滤                                     |
| recon       | 日终积分对账（T+1，逐日结转）                      |
| redact      | 脱敏                                               |
| scheduled   | F4 定时任务统计                                    |
| txn         | 16 位积分交易流水号                                |

除 ``recon`` 会惰性调用 ``bs.load()``（无 state 传入时的兜底）外，
本包不做任何 IO，可以在没有文件系统的宿主里直接跑。
"""

__all__ = ["analyze", "anomaly", "budget", "classify", "compare", "ledger",
           "monitor", "query", "recon", "redact", "scheduled", "txn"]
