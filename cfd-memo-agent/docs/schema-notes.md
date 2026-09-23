# Schema Notes

## Task Schema

`schemas/task.schema.json` describes the user-requested CFD task after planning. It captures the case type, solver, geometry, physics, boundary conditions, mesh intent, time control, and convergence target.

For Stage B, the schema is intentionally narrow: `case_type` is `cylinder-2d`, `dimension` is `2D`, and `flow_model` is `incompressible_laminar`.

## C3 任务约束

schema 采用 JSON Schema Draft 2020-12，通过 jsonschema 库执行。
计算域 domain_length/domain_height 现为必填项；六个边界均须提供字符串 U/p，
start_time 必须非负。原有示例无需迁移，缺少这些字段的旧任务须补齐。
solver 枚举仍保留 pimpleFoam，但当前验证器只接受 icoFoam 和默认边界组合，
网格生成器只接受 manual-template（字段省略时按当前模板模式处理）。

Python 额外检查有限数值、nu=U*D/Re、时间先后关系、圆柱直径小于计算域。
数值一致性使用相对误差 1e-8、绝对误差 1e-10。task schema 是唯一结构定义，
保留在仓库 schemas/ 中，由可编辑安装的程序按文件位置加载。

验证结果不是 episode：它包含 task_valid/config_valid/errors/warnings/
runtime_blockers/mesh_verified。每项问题包含稳定的 code、中文 message
和字段或文件 location；不会把静态检查通过当作网格已验证或仿真成功。
episode 仍由后续运行闭环生成。

## C4 执行与诊断记录

C4 记录是普通 JSON 报告，尚不是 episode，不套用 episode.schema.json。
每次执行保存于 run/attempts/attempt-<唯一编号>/，不覆盖既有尝试。

- execution.json：mode/simulated/scenario 标记来源；status 区分
  simulated_success、completed、blocked、environment_error、failed、
  timeout、execution_error。physical_validated 始终为 false。
- validation 和 validation_findings：保留 C3 报告及对应处理建议。
  模拟模式允许运行阻碍存在，但仍要求 config_valid=true。
- steps：按执行顺序保存 stage、command、log_path、returncode、
  timed_out、duration_seconds、diagnosis。未启动的后续步骤不会伪造退出码；
  命令启动失败 returncode 为 null；模拟退出码来自所选样例。
- diagnosis.json：保存本次 findings、validation_findings 及逐步诊断。
  日志诊断含 last_time、residuals（time/field/initial/final/iterations）；
  不由残差数据推断收敛或物理有效性。
- 每项 finding 含 code、message、location、evidence、action、suggestion。
  action 为 rule_candidate、user_clarification 或 manual_required。
  候选建议不代表已经修复，C5 实施前还须核对有效 task。

完整 episode、各轮关联和修正记录已在 C5 接入，C4 单次执行记录保持原格式。

## C5 Episode v2

新增 schema_version=2；未标版本的原阶段 B 样例仍能通过 schema。
v2 记录包含有效 task（或 null）、input、runner_mode、rounds、corrections、
injected_fault、stop_reason、物理验证标记及工作流、episode、报告路径。
input 保留一次读取的原始文本，解析失败时也可追溯；无效任务不冒充有效 task。

mode 固定为 no_memory，runner_mode 单独区分 simulated/real；
physical_validated 与 experience_reused 固定为 false。
状态区分 simulated_success、completed、failed、blocked、timeout、
execution_error、interrupted；C5 不输出真实物理成功状态 success。
缺少任务、case 或日志时相关摘要为 null，不使用虚构的文件路径。

rounds 记录从 0 开始的轮次、task/case 所在目录、执行和检查报告路径、
配置结果、问题、真实运行阻碍、实际日志列表与残差。validation_failed
表示执行前被拦截；空日志列表表示没有产生执行日志，不表示日志丢失。

corrections 记录 from_round/to_round、reason_codes 及 changes；
每个 change 包含 file、before、after。原始故障注入单独记录，
不算自动修正次数。max_corrections 是本次生效预算；
diagnosis.correction_count 是实际实施的修正次数。
reflection 来自证据与规则，reusable_rules 只是候选建议；
模拟完成不能提升其真实 CFD 可信度。

保存 episode 前使用仓库同一份 Draft 2020-12 schema 验证。
逐轮记录使用独立文件及时落盘，最终 episode 和中文报告汇总全过程。
本阶段不实现崩溃续跑、向量检索或跨任务知识提炼。

## C6 真实执行证据

保留 episode v2，给 rounds 增加可选 environment、mesh_evidence、result_evidence，
没有实际证据时为 null；既有 episode 无需迁移。真实执行时 case_path 指向 attempt
内实际计算的副本，不再把输入 case 当作流场结果目录。

- environment：native/wsl、固定版本 10、实际命令路径；WSL 另记发行版。
- mesh_evidence：实际单元数、边界面数、输入配置和生成网格的 SHA-256 指纹。
  只有本轮 checkMesh 及实际文件检查通过才生成，不能由旧报告直接授予准入。
- result_evidence：结束时刻、U/p 路径、单元数及 physical_validated=false。
  文件缺失、非有限值、内部场长度不符或配置/网格中途变化均不能 completed。

静态 validation.mesh_verified 仍为 false；检查后的证据单独保存，不改写执行前报告。
geometry.json 的 planned_cells 是整数划分的计划数量，actual_cells 初始为 null；
实际数量只在 mesh_evidence 中记录。target_cells 现在参与划分，不是实际测量值。
真实运行的工程验收仍待环境安装后完成，物理对照另行进行。

## Episode Schema 概述

`schemas/episode.schema.json` describes one completed or simulated CFD run. It records the task, comparison mode, result status, generated case files, log summary, diagnosis, reflection, reuse tags, and metrics.

Episodes are the first version of CFD-Memo's memory. Later stages can store them under `memory/episodes/` and retrieve them by tags such as `cylinder-2d`, `re100`, `icoFoam`, and `laminar`.
