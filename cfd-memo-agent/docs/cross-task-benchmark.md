# 跨任务正式评估协议 v1

## 目标

M1 只冻结任务和比较规则，不运行正式实验。机器可读清单位于
`experiments/cross-task-v1.json`，修改任务、模型、预算或成功定义必须创建新版本，
不能覆盖 v1。

## 数据集

共 30 个不同参数规格，每个任务族 10 个：

- `cylinder-2d`：`icoFoam`，不可压缩层流圆柱绕流。
- `cavity-2d`：`icoFoam`，不可压缩层流顶盖驱动方腔。
- `backward-step-2d`：`simpleFoam`，OpenFOAM 10 `pitzDaily` RANS 后台阶。

每个任务族包含 4 个同场景新参数、2 个已知故障、2 个未知故障和 2 个跨场景迁移
规格。审计器会把紧凑参数还原为完整 task，并调用当前 `validate_task` 和注册适配器；
参数完全相同的重复任务会被拒绝。

## 真实评估

正式核心选择五个锚点任务，分别交给 `no_memory`、`simple_cache`、
`retrieval_only`、`cfd_memo` 四组，形成 20 个唯一的 task/group 配对。四组必须共享
完全相同的五个锚点，避免把任务难度差异误认为记忆效果。

固定模型为 DeepSeek `deepseek-chat`，单次模型调用最多输出 2000 token，模型超时
60 秒；OpenFOAM 单命令超时 1800 秒，自动修正预算为 2。密钥只从
`DEEPSEEK_API_KEY` 读取，清单不保存密钥。托管模型名称和价格可能变化，因此 M2
执行时还必须把实际配置与计费快照写入运行 manifest。

## 指标与结论边界

记录真实成功率、首次成功率、修正次数、耗时、输入/输出 token、估算成本、有效经验
复用率和失败避免率。工程成功只表示可信命令链完成并产生有效场文件；物理准确性单独
报告，不从 `completed` 推断。

M2 已实现六种隔离夹具：缺边界、非法黏度、时间控制漂移、网格分辨率风险、边界语义
迁移和黏度关系迁移。前两类及迁移中已有确定性证据支持的字段可受控恢复；时间和网格
风险只诊断，不自动改变任务物理意图。夹具只修改工作流的首轮副本，模板和输入不变。

批量 runner 会用非评估任务建立训练存储，再为 `simple_cache`、`retrieval_only` 和
`cfd_memo` 复制独立快照。评估统一使用 `memory_learning=false`，每轮前后比较目录哈希，
若快照变化立即停止。状态逐项写入，可限制本次数量并恢复；不存在一次长批次失败后从头
重跑的问题。

`deepseek-chat` 是 M1 当时冻结的协议名称，执行时以 DeepSeek 当前可用模型为准，并把
二者同时写入状态。2026-10-06 的价格快照同时保存峰值和谷值费率，最终成本按 API usage
给出区间，不把密钥写入任何实验文件。

先运行静态审计：

```powershell
python -m cfd_memo_agent.cli benchmark audit
```

然后先跑一个无网络的模拟调度验收，真实评估再按小批次启动：

```powershell
python -m cfd_memo_agent.cli benchmark run --runner simulated --limit 1
python -m cfd_memo_agent.cli benchmark run --runner real --limit 1
python -m cfd_memo_agent.cli benchmark run --runner real --resume <study目录> --limit 1
```

只有 20 个槽位都产生真实 episode 才能把 M2 标为完成；模拟调度结果不进入 M3 统计。

## M2 实际执行

2026-10-06 在 `cross-task-m2-real-v1` 完成全部 20 个槽位。四组各执行五个相同
锚点，每组均有三个工程完成、两个按 `UNSUPPORTED_ERROR` 停止；基础设施失败为零。
CFD-Memo 组合计一次修正，其余三组各三次修正。总模型用量为 45,774 输入 token、
16,156 输出 token，其中 26,984 输入 token 命中缓存；按执行时同时保存的峰谷价格，
费用区间为 `$0.012593-$0.025186`。

这些是 M2 原始计数，不是显著性结论。验证阶段停止的任务没有启动 OpenFOAM，但仍是
真实 runner 下的失败评估，不记作成功，也不以模拟结果替代。M3 必须从 episode 重算
首次成功、最终成功、修正、有效引用和失败避免指标，并分析时间漂移与后台阶边界失败。

## M3 统计结论

使用以下命令从 episode 重新生成统计；已有输出默认拒绝覆盖：

```powershell
python -m cfd_memo_agent.cli benchmark analyze `
  --study cases/runs/cross-task-m2-real-v1
```

四组最终工程成功率均为 3/5（60%，Wilson 95% CI 23.1%-88.2%）。CFD-Memo 的
首轮成功为 2/5、四个已知故障机会中提前避免 2 个，平均修正次数为 0.2；无记忆组
对应为 0/5、0/4 和 0.6。CFD-Memo 相对无记忆组的首轮成功精确 McNemar 检验为
`p=0.5`，最终成功检验为 `p=1.0`。因此只能报告固定任务集上的效率趋势，不能宣称
统计显著、任意场景泛化或物理准确性提升。

提前避免发生在圆柱缺边界和错误黏度两个任务。八个失败由方腔时间控制漂移和后台阶
缺边界在四组中重复构成，均以 `UNSUPPORTED_ERROR` 安全停止。完整行级数据、配对检验
和失败表位于本地 study 的 `analysis/analysis.json` 与 `analysis/report.md`。

## M4 脱敏交付

以下命令把本地 study 转为可审阅的公开包；默认拒绝覆盖已有目录：

```powershell
python -m cfd_memo_agent.cli benchmark package `
  --study cases/runs/cross-task-m2-real-v1
```

输出位于 `docs/results/cross-task-v1/`，包含源码/环境清单、聚合统计、失败案例、有效
记忆证据、统计报告和复现说明。导出器会扫描 Windows/WSL/Linux 绝对路径与密钥格式，
发现后拒绝完成；它不复制原始 episode、模型响应 ID、CFD 场文件或科研原始资料。
源码范围使用内容 SHA-256 冻结，避免未提交工作树只记录旧 Git commit 的问题。仓库当前
没有明确开源许可证，因此交付包仅作为实验审阅材料，不自动授予复用权利。
