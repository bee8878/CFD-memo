# CFD-Memo M3 跨任务实验分析

本报告从 M2 的 20 个真实 runner episode 重算，不采用模拟结果。

| 对比组 | 工程成功率（95% Wilson CI） | 首轮成功率（95% Wilson CI） | 平均修正次数 | 有效经验任务率 | 失败避免率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| no_memory | 60.0% [23.1%, 88.2%] | 0.0% [0.0%, 43.4%] | 0.60 | 0.0% [0.0%, 43.4%] | 0.0% [0.0%, 49.0%] |
| simple_cache | 60.0% [23.1%, 88.2%] | 0.0% [0.0%, 43.4%] | 0.60 | 0.0% [0.0%, 43.4%] | 0.0% [0.0%, 49.0%] |
| retrieval_only | 60.0% [23.1%, 88.2%] | 0.0% [0.0%, 43.4%] | 0.60 | 40.0% [11.8%, 76.9%] | 0.0% [0.0%, 49.0%] |
| cfd_memo | 60.0% [23.1%, 88.2%] | 40.0% [11.8%, 76.9%] | 0.20 | 40.0% [11.8%, 76.9%] | 50.0% [15.0%, 85.0%] |

## 与无记忆组的配对比较

| 对比组 | 最终成功 McNemar p | 首轮成功 McNemar p | 平均修正次数差 | 平均耗时差（秒） |
| --- | ---: | ---: | ---: | ---: |
| simple_cache | 1.000 | 1.000 | +0.00 | +0.32 |
| retrieval_only | 1.000 | 1.000 | +0.00 | +2.23 |
| cfd_memo | 1.000 | 0.500 | -0.40 | -3.94 |

## 提前避免案例

- `real-016`：`cylinder-05-re150-boundary` / `missing-boundary`
- `real-017`：`cylinder-06-re180-transport` / `bad-transport`

## 失败案例

| 评估 | 对比组 | 任务 | 故障 | 停止原因 |
| --- | --- | --- | --- | --- |
| real-004 | no_memory | cavity-07-re150-time | time-control-drift | UNSUPPORTED_ERROR |
| real-005 | no_memory | step-05-re22860-boundary | missing-boundary | UNSUPPORTED_ERROR |
| real-009 | simple_cache | cavity-07-re150-time | time-control-drift | UNSUPPORTED_ERROR |
| real-010 | simple_cache | step-05-re22860-boundary | missing-boundary | UNSUPPORTED_ERROR |
| real-014 | retrieval_only | cavity-07-re150-time | time-control-drift | UNSUPPORTED_ERROR |
| real-015 | retrieval_only | step-05-re22860-boundary | missing-boundary | UNSUPPORTED_ERROR |
| real-019 | cfd_memo | cavity-07-re150-time | time-control-drift | UNSUPPORTED_ERROR |
| real-020 | cfd_memo | step-05-re22860-boundary | missing-boundary | UNSUPPORTED_ERROR |

## 资源消耗

- 输入 token：45774
- 输出 token：16156
- 总 token：61930
- 估算费用：$0.012593-$0.025186

## 结论边界

四组最终工程成功率相同，当前数据不能证明记忆提高最终成功率。CFD-Memo 在固定
任务集上减少了修正次数并提高首轮成功/失败避免，但每组只有五个配对任务，区间很宽；
这些结果应视为原型证据，不是广泛 CFD 泛化结论。后台阶与时间控制失败需要在失败
案例中单独讨论。`physical_validated=false` 的任务不能用于声称物理结果准确。
