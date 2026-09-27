# CFD-Memo 配置记忆对比实验协议

## 研究问题

在固定二维层流圆柱绕流 MVP 中，跨任务记忆能否提高首次配置成功率、减少修正次数，
并在再次遇到已知配置故障时于 runner 前避免失败？本实验不评价 CFD 物理准确性。

## 固定条件

- 执行后端：`simulated`，只验证软件闭环和配置行为。
- 模型：`rules`，排除模型随机性、网络与 API 费用。
- 求解器与模板：`icoFoam`、`cases/templates/cylinder-2d/`。
- 修正预算：每个任务最多 2 次；正式重复次数为 3。
- 受控故障：`missing-boundary`、`bad-transport`。
- 参数任务：`Re/U/D` 分别为 `100/1/1`、`200/2/1`、`150/1.5/1`、
  `80/0.8/1`，均满足 `nu=U*D/Re=0.01`。

机器可读协议固定在 `experiments/memory-study.protocol.json`。修改任务、重复次数或统计
规则必须提升 `protocol_version`，不得覆盖已有实验结果。

## 对比组

| 组别 | 可用历史 | 评估期间是否学习 |
| --- | --- | --- |
| `no_memory` | 无 | 否 |
| `simple_cache` | 完全相同 task 的完整 case | 否 |
| `retrieval_only` | 冻结的结构化经验，仅 Reviewer 使用 | 否 |
| `cfd_memo` | 同一冻结经验，Planner/Case Writer/Reviewer 使用 | 否 |

训练与评估严格分开。简单缓存只预存 Re=100 完整 case；结构化记忆用 Re=100 的两类
故障建立经验。之后复制相同知识快照给 `retrieval_only` 和 `cfd_memo`，评估时使用
`memory_learning=false`，避免测试数据泄漏回经验库。

## 指标

- 首轮成功率：第 0 轮直接通过配置验证和模拟执行的比例。
- 最终成功率：修正预算内达到 `simulated_success` 的比例。
- 配置正确率：第 0 轮配置通过比例。
- 错误识别率：未被提前避免的标注故障中，正确诊断错误 code 的比例。
- 平均修正次数与平均耗时：失败任务同样计入。
- 经验复用率：episode 明确记录历史信息被使用的任务比例。
- 失败避免率：注入已知漂移后，在首次 runner 验证前恢复的比例。

## 执行与解释

```powershell
python -m cfd_memo_agent.cli memory-study --repeats 3
```

输出包含冻结协议副本、训练记录、四组逐任务 episode、`results.json` 和 `report.md`。
所有实验目录位于本地 `cases/runs/` 并被 Git 忽略。

该实验只能证明当前单算例中的配置记忆效果。`simulated_success` 不能解释为 OpenFOAM
真实求解成功、物理量正确或跨几何泛化；真实物理 baseline 仍以 C6 独立验收为准。
