# CFD-Memo 项目路线图

## 1. 目标与依据

构建一个 Python 命令行 CFD 智能体：理解需求、调用 OpenFOAM、诊断并修正错误，再把经验用于下一次任务。研究问题是：**长期记忆与自我反思能否减少重复试错，提高仿真成功率和效率？**

- 本地立项书：要求四层记忆、经验编码与检索、自我反思、OpenFOAM 集成，以及经验库、失败案例库和实验报告。Idea9 作为扩展构想，效果数值和发表目标不视为已验证结论。
- [Foam-Agent 仓库](https://github.com/csml-rpi/Foam-Agent)、[架构说明](https://github.com/csml-rpi/Foam-Agent/blob/main/AGENTS.md) 与[论文](https://arxiv.org/abs/2505.04997)：借鉴其 LangGraph 多 Agent 流程（Architect、Input Writer、Runner、Reviewer）、教程 FAISS 检索、审查修正循环及工具接口分离。本项目不直接复制代码；Foam-Agent 的教程 RAG 不等同于 CFD-Memo 要验证的跨任务经验记忆。
- [MetaOpenFOAM 论文](https://arxiv.org/abs/2407.21320)：作为另一项 LLM 多 Agent CFD 自动化参考，用于比较任务分工与工作流设计，不作为本项目已经实现的功能。
- 本项目先沿用现有 Python 模块组织流程；MCP、Skill 是后续可选接口。教程检索与跨任务经验记忆分别评估，不预设其他项目完全没有记忆能力。

## 2. 当前起点与最小范围

代码位于 `cfd-memo-agent/`。已实现 C1–C6、LLM 统一接口、Planner Agent 与 Case Writer Agent：规则/模型规划、受限配置意图、配置生成、验证、运行、有限修正工作流、本地 episode、中文报告及 WSL/OpenFOAM 10 后端；CLI 提供 `plan/generate/validate/run/diagnose`。Reviewer Agent 与跨任务经验检索尚未完成。

首个算例固定为二维、不可压缩、层流圆柱绕流，使用 `icoFoam`；默认 `Re=100`、`U=1 m/s`、`D=1 m`，由 `nu=U*D/Re` 得到运动黏度。先只支持此算例，GUI、多相流和湍流不纳入首版。

**首个物理 baseline 已验收：** 项目自编网格已在 Foundation OpenFOAM 10 中手工和自动跑通；八组真实计算完成 `Cd`、`Cl`、`St` 参考对照及网格/时间步独立性检查。原单环网格未收敛后改为双环 wake-focused 局部加密，29440 到 47200 单元的三项变化均通过门槛，汇总 `physical_validated=true`。该结论只适用于固定的 Re=100 二维圆柱 benchmark。运行环境固定为 WSL2 Ubuntu 22.04，Windows 保留现有 Python 环境。

区分两级完成：**工程 MVP 与首个物理 benchmark** 已完成；**研究原型** 还须完成 LLM、多 Agent、记忆复用和对比实验。模拟成功不能计入真实 CFD 成功率。

## 3. 一次任务如何执行

CLI 接收需求或任务文件，由 `workflow.py` 统一调度；模块只负责各自的工作。以下是目标流程，当前 C5 已串联 C1–C4，支持边界和黏度字段的有限修正；历史经验检索与跨任务提炼仍待 E。原 C4 `run --run` 保留单次执行行为。

| 顺序 | 模块与输入 | 输出与下一步 |
| --- | --- | --- |
| 1 | `orchestrator` 接收需求并建立共享任务状态 | 保存原始输入、模式、预算和证据路径；控制后续 Agent 与工具的调用顺序 |
| 2 | `memory` 按任务特征检索（阶段 E） | 返回成功经验、失败模式及适用条件；无匹配时使用默认模板 |
| 3 | Planner Agent 接收需求与检索结果（阶段 D） | 生成 task JSON；schema 和物理约束检查通过后继续，冲突或超范围需求返回说明 |
| 4 | Case Writer Agent / `generator` 接收 task、模板和经验（阶段 D） | Agent 提出配置意图，生成器在独立 run 目录受控写入 case；不允许绕过 schema 与模板边界 |
| 5 | `validator` 确定性检查生成文件 | 核对文件、边界、求解器、单位与参数关系；未通过则禁止运行，进入诊断 |
| 6 | `runner` 确定性执行合格 case | 真实模式依次执行 `blockMesh -> checkMesh -> icoFoam`；保存退出码、日志、残差与结果路径 |
| 7 | Reviewer Agent / `diagnoser` 读取检查或运行证据（阶段 D） | 输出结构化原因和修改建议；可修正错误返回第 4 步并重新验证，未知错误、超时或超过预算则停止 |
| 8 | `memory`、`reporter` 接收全过程（阶段 E） | 无论成功失败都保存 episode 和报告；反思先作为候选知识，经证据确认后供下一任务检索 |

默认最多修正 2 次，每次保存修改内容、原因和结果。先实现有限的规则修正，阶段 D 再接入 LLM 建议；不能仅凭模型判断宣告仿真成功。每轮结果先写入任务记录，任务结束后再进行跨任务知识提炼。

模拟模式必须显式选择，使用带标签的日志样例；真实模式缺少 OpenFOAM 时报告环境错误，不悄悄切换成模拟成功。

## 4. 实施顺序与验收

A（范围确定）、B（基础骨架）已建立；B 不包含真实 baseline 验证。接下来按 **C -> D -> E -> G** 推进，F 为可选扩展。每完成一项，以测试或运行产物为依据更新状态。

### C：工程闭环，当前阶段

按以下顺序实现，每项都要能独立测试：

- [x] C1 规则规划：简单需求转为 task JSON。
- [x] C2 配置生成：复制模板到唯一 run 目录；写入速度、黏度、时间与计算域顶点，圆柱直径保存到 `constant/geometry.json`；不覆盖模板与旧记录。C6 已补齐圆柱实体网格。
- [x] C3 配置验证：已验证 schema、有限数值、参数关系、量纲与跨文件一致性；非法任务在生成前被拒绝。报告分开记录配置结果与真实运行阻碍，支持 CLI 检查和保存。
- [x] C4 运行与诊断：支持显式模拟模式、真实执行入口及独立日志诊断；保存每次尝试的检查、日志、残差、退出码与超时状态。C6 已验证 WSL/OpenFOAM 10 的真实三阶段执行。
- [x] C5 调度与记录：串联 C1–C4，支持需求或 task JSON；默认最多修正 2 次，恢复边界/黏度文件后复验。逐轮保存独立副本，成功和失败均生成 episode v2 与中文 Markdown 报告。
- [x] C6 物理 baseline：真实网格、固定版本、手工/程序求解、受力提取、参考对照和八组独立性研究均已完成。静态 `MESH_NOT_VERIFIED` 仍表示每个新 case 必须重新执行网格检查，不能复用旧证据跳过准入。

#### C6 第一部分进度（2026-09-23）

- [x] 工程代码：八块圆柱网格生成、拓扑检查、Windows 到 WSL 执行适配、本轮网格与场文件证据、episode 与报告。
- [x] 自动测试：完整测试套件通过；另有真实 OpenFOAM 验收，不用合成输出冒充 CFD 结果。
- [x] 环境安装：Ubuntu 22.04.5 WSL2 与 Foundation OpenFOAM 10 已安装；镜像和发行版虚拟磁盘位于仓库外 `D:\CFD-Environment`。Python 仍使用 Windows 项目虚拟环境。
- [x] 手工验收：`blockMesh`、`checkMesh`、`icoFoam` 均实际通过；默认结束时刻 10 的 U/p 已生成。
- [x] 自动真实验收：独立 attempt 状态为 completed；网格 10368 单元、圆柱 288 面，结果证据指向本轮 `10/U` 与 `10/p`。
- [x] 物理验收：参考物理量对照、网格与时间步独立性研究通过；汇总 `physical_validated=true`。
- [x] 受力与频率证据：OpenFOAM `forceCoeffs` 输出已接入，保存平均 `Cd`、`Cl` 振幅/RMS、周期稳定性与 `St`；`endTime=100` 的真实周期信号已提取。
- [x] 八组研究：保留原全局加密序列作为失败证据，并新增双环 wake-focused 的 29440/47200 单元序列；最终 `Cd=1.32566`、`Cl` 振幅 `0.33536`、`St=0.16309` 均进入预先固定的 Re=100 参考区间。
- [x] 升力网格独立性：新序列的 `Cd/St/Cl` 振幅变化为 0.088%/0.727%/1.83%，分别通过 3%/3%/5% 门槛。

安装和执行步骤见 `cfd-memo-agent/docs/c6-real-run.md`。C6 工程执行部分完成；
整体仍保持未完成，直到物理量对照和网格/时间步独立性研究完成。

C1–C6 的模拟与单场景真实执行均已通过，工程 MVP 完成。已覆盖成功、验证失败、修正后成功、无有效修改和耗尽重试；工程执行成功不代表物理准确性已经验证。

#### 待解决：验证失败后的自动修正

当前 C3 和 C4 独立命令仍只检查或执行一次；C5 工作流已能有限修正。非法任务停止生成并保存失败记录；真实模式的环境与默认网格阻碍已在 C6 工程验收中解决，但未知运行错误仍需人工判断。

- [x] C4 定义诊断结果：用 rule_candidate、user_clarification、manual_required 区分候选规则修正、需用户澄清和人工处理，输出问题位置、证据与建议；候选建议不等于已修复。
- [x] C5 有限规则修正：仅恢复有效任务支持的边界字段和 nu；若对应文件存在其他未解释的改动则拒绝替换。保留 Re、几何、物理模型、原始输入和历史轮次；记录文件前后内容。
- [x] 每次修正重新验证；默认最多 2 次，可由 task 或命令行设置。未知错误、无实际变更、参数冲突、超时或预算耗尽会停止。网格和物理结果问题不由配置修正规则擅自处理。
- [x] 自动修正验收覆盖修正后通过、持续失败、不可修正和预算耗尽；受控故障改动首轮 case，验证失败时无执行日志，修复后才进入模拟运行。
- [x] D4 已扩展 LLM Reviewer 修正建议；建议受确定性证据与 schema 约束，实际修改仍由规则工具执行并复验。

### D：LLM、多 Agent 协作与反思

- [x] D1 统一模型接口：规则模式保持默认；已实现 OpenAI 与 DeepSeek 官方 Responses API 结构化输出适配、假模型、超时/输出限制、schema 二次验证、脱敏配置状态及模型调用 trace。密钥只从各 provider 的环境变量读取。
- [x] D2 建立 `orchestrator` 共享状态并接入 Planner Agent；支持 ready/需澄清结果、schema 与 CFD 规则双重验证，记录模型、提示词版本、假设和实际使用模式；接口故障默认停止，仅在显式允许时回退规则 Planner。
- [x] D3 接入 Case Writer Agent：把已验证 task 转为结构化配置意图；用 task 指纹绑定本次决策，只允许固定模板和文件映射。生成器仍确定性写入，完整工作流保存 `case-writing.json`、脱敏 trace、理由与提示。
- [x] D4 接入 Reviewer Agent：读取 validator/runner 的结构化证据，输出原因、修正建议和适用条件；建议必须重新验证后才能实施。
- [x] 实现三个有独立角色、输入输出和提示词的协作 Agent：Planner 负责结构化任务，Case Writer 负责配置意图，Reviewer 负责基于证据诊断与修正建议。普通 Python 模块不因改名而算 Agent。
- [x] `validator`、`runner`、文件写入和证据提取保持确定性工具；Agent 只能提出结构化决策，不能绕过验证直接宣称成功或任意执行命令。
- [x] 记录每个 Agent 的输入摘要、输出、状态转移和决策来源；由 orchestrator 统一限制修正次数、处理失败并保存完整轨迹。
- [x] 模型输出经过 schema、物理规则与文件检查；缺失参数注明默认来源，冲突需求要求澄清。
- [x] 用验证/运行证据生成“原因、修正建议、适用条件”，修正后重新运行；记录模型、提示词版本、调用量和耗时。

验收：同一任务能显示 Planner -> Case Writer -> 工具验证/执行 -> Reviewer 的可追溯协作；多种同义需求可生成合法任务；无效输出和接口故障可处理；至少一个受控失败可经审查、修正、重跑验证。只拆成多个类、只生成一段反思文字或由模型自报成功，都不算多 Agent 闭环完成。

### E：跨任务记忆，研究核心

- [ ] 四层记忆：工作记忆保存当前状态；情景记忆保存完整 episode；知识记忆保存带证据的规则和失败模式；程序记忆保存已验证操作步骤。
- [ ] 先用 JSON 与结构化标签检索，再实现申请书要求的向量编码与检索；按几何、物理模型、Re、边界和求解器筛选适用经验。
- [ ] 由统一 `MemoryManager` 服务 Planner、Case Writer 和 Reviewer；在规划、生成和修正前分别检索适用经验，区分共享知识与当前 Agent 的工作状态。
- [ ] 在规划、生成和修正阶段注入经验；记录引用的经验 ID、适用条件、影响的 Agent 与具体决策、使用后效果。
- [ ] 反思得到的规则先标为候选，经运行证据确认后提升可信度；错误经验可降权，重复经验可合并。

验收：重启程序后，第二个相似但不同的任务能引用第一次的经验并影响具体决策；相同失败能被预防或更快修正，且完整保留证据。同步扩展 schema 与样例，使任务输入、每轮尝试、经验来源和验证状态可追溯。

### F：可选 MCP / Skill 接入

核心接口稳定后，再暴露规划、生成、运行、诊断等工具；Skill 说明调用顺序。不把此阶段作为实验或结题的前置条件。

### G：实验与交付

按下一节固定协议运行，提交原型系统、可复现实验记录、经验库、失败案例库、系统文档和结题报告；论文作为研究成果目标，不预先承诺效果或录用。

## 5. 怎样证明记忆有用

**Baseline 分开定义：** 手工验证的圆柱 case 是仿真参照；以下各组是智能体方法对照。先在同一执行后端比较，确保差异来自记忆机制；与原版 Foam-Agent 的外部比较另行固定版本和运行条件。

| 对比组 | 可使用的信息 |
| --- | --- |
| 无记忆 | 公共模板与当前任务，不读取历史经验 |
| 简单缓存 | 完整历史 case 或模板，不提炼知识 |
| 仅检索、不反思 | 检索历史记录，不生成提炼规则，用于分离检索与反思的贡献 |
| CFD-Memo | 检索历史记录、失败策略与经验证的反思规则 |

四组使用相同模型、任务、模板、运行环境和修正预算。经验积累集与测试集分开；主实验冻结历史记忆，不让测试答案提前进入经验库。持续学习实验另行按固定任务顺序记录。

测试先覆盖同一物理适用范围内的不同 Re、速度和直径，以及边界缺失、非法黏度等受控故障；后续增加新的参数组合或第二类算例评估迁移。同一圆柱算例上的提升不等于跨几何泛化。

| 指标 | 统计口径 |
| --- | --- |
| 首次 / 最终成功率 | 不经修正 / 预算内通过真实验收的任务数，占全部测试任务比例 |
| 配置正确率 / 错误识别率 | 首次配置通过验证的比例 / 已标注错误被正确识别的比例 |
| 修正次数 / 单次耗时 | 每任务实际修改次数与端到端耗时，失败任务也计入；另记模型调用成本 |
| 经验复用率 | 有历史证据支持的复用决策数 / 预先定义的可复用决策总数 |
| 失败避免率 | 重遇已知故障机会中，在首次运行前成功避免的比例；与事后修复分开 |
| 泛化能力 | 在未用于积累经验的参数组合或新算例上，分别报告成功率和耗时 |

正式实验前，在 `cfd-memo-agent/docs/experiment-plan.md` 固定任务清单、样本量、重复次数和验收阈值。真实验收包括几何与网格、求解完成、场数据有效性及物理量对照；不能只看日志含有 `End`。该文件仍是实验协议草案，与本路线图不一致处以本文件为准。

## 6. 现在从哪里开始

**下一项是阶段 E：跨任务记忆。** C1–C6 与阶段 D 的 Planner、Case Writer、Reviewer 多 Agent 闭环已完成；episode 仍标记 no_memory、尚未复用经验。下一步先建立统一 MemoryManager 和 JSON 结构化检索，让第二个相似任务能引用第一项经过证据确认的经验，再增加向量检索。不要重复运行现有物理矩阵，也不要把单一圆柱算例的通过解释为跨场景泛化。

现有命令（在已安装项目的 Python 环境中，从 `cfd-memo-agent/` 执行）：

```powershell
python -m cfd_memo_agent.cli plan "做 Re=100 的二维圆柱绕流"
python -m cfd_memo_agent.cli generate "做 Re=200 的二维圆柱绕流，入口速度=2，D=1"
python -m cfd_memo_agent.cli validate --run "cases/runs/你的运行目录"
python -m cfd_memo_agent.cli run --run "cases/runs/你的运行目录" --runner simulated
python -m cfd_memo_agent.cli run --run "cases/runs/你的运行目录" --runner simulated --scenario missing-boundary
python -m cfd_memo_agent.cli diagnose --log cases/runs/sample-logs/bad-transport.log --returncode 1 --simulated
```

C4 每次运行保存到 `cases/runs/<run_id>/attempts/<attempt_id>/`，包含 validation.json、execution.json、diagnosis.json 和日志；真实执行使用独立 case 副本。C4 单次命令不自动修改、不重试、不生成 episode。真实入口会报告环境缺失；环境就绪后先生成并检查网格，再求解和检查实际 U/p。静态报告 MESH_NOT_VERIFIED 只允许进入网格检查，不能直接启动求解。不会回退模拟。

C5 已实现的完整工作流命令：

```powershell
python -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" --runner simulated
python -m cfd_memo_agent.cli run --task examples/task.cylinder-2d.json --runner simulated
python -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" --runner simulated --fault missing-boundary
python -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" --runner simulated --fault bad-transport --max-corrections 0
```

C5 输出到 `cases/runs/workflow-<唯一编号>/`：input、task 检查、可信配置 reference、逐轮 rounds、episode.json 和 report.md。`--fault` 仅用于模拟配置故障；`--scenario` 保留给 C4 日志播放。长期经验存储与检索仍待 E；当前 episode 只是本地全过程记录。

隐私约束：`科研立项/` 继续由 `.gitignore` 忽略；原始申请书、个人资料和密钥不复制到代码或公开报告。调用外部模型只发送任务所需且经脱敏的数据。本路线图不包含原始申请书内容或私人文件链接。
