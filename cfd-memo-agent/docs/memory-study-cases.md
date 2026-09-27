# 五个代表性配置记忆案例

## no-memory-repair

无历史信息；首轮验证失败，规则修复后第二轮成功。

- 组别：`no_memory`；任务：`re200-u2-d1`；故障：`missing-boundary`
- 轮次状态：`validation_failed -> simulated_success`；修正次数：1
- Reviewer 决策：`repair -> accept`
- 缓存命中：False；运行前恢复：无
- 检索经验数：0

## cache-exact-hit

task 与缓存完全相同；完整 case 命中并覆盖受控漂移。

- 组别：`simple_cache`；任务：`re100-u1-d1`；故障：`missing-boundary`
- 轮次状态：`simulated_success`；修正次数：0
- Reviewer 决策：`accept`
- 缓存命中：True；运行前恢复：无
- 检索经验数：0

## cache-parameter-miss

Re 与入口速度变化导致 task 指纹不同；缓存未命中。

- 组别：`simple_cache`；任务：`re200-u2-d1`；故障：`missing-boundary`
- 轮次状态：`validation_failed -> simulated_success`；修正次数：1
- Reviewer 决策：`repair -> accept`
- 缓存命中：False；运行前恢复：无
- 检索经验数：0

## retrieval-after-failure

Reviewer 在失败后引用经验，但生成前没有防错动作。

- 组别：`retrieval_only`；任务：`re200-u2-d1`；故障：`missing-boundary`
- 轮次状态：`validation_failed -> simulated_success`；修正次数：1
- Reviewer 决策：`repair -> accept`
- 缓存命中：False；运行前恢复：无
- 检索经验数：1

## memo-prevention

Planner/Case Writer 提前使用经验，0/U 在 runner 前恢复。

- 组别：`cfd_memo`；任务：`re200-u2-d1`；故障：`missing-boundary`
- 轮次状态：`simulated_success`；修正次数：0
- Reviewer 决策：`accept`
- 缓存命中：False；运行前恢复：0/U
- 检索经验数：2

这些案例来自确定性模拟配置实验，只用于说明软件决策路径，不代表真实 CFD 物理结论。
