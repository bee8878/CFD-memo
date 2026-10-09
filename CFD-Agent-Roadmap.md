# CFD-Memo 项目路线图

## 1. 目标与依据

构建一个 Python 命令行 CFD 智能体：理解需求、调用 OpenFOAM、诊断并修正错误，再把经验用于下一次任务。研究问题是：**长期记忆与自我反思能否减少重复试错，提高仿真成功率和效率？**

- 本地立项书：要求四层记忆、经验编码与检索、自我反思、OpenFOAM 集成，以及经验库、失败案例库和实验报告。Idea9 作为扩展构想，效果数值和发表目标不视为已验证结论。
- [Foam-Agent 仓库](https://github.com/csml-rpi/Foam-Agent)、[架构说明](https://github.com/csml-rpi/Foam-Agent/blob/main/AGENTS.md) 与[论文](https://arxiv.org/abs/2505.04997)：借鉴其 LangGraph 多 Agent 流程（Architect、Input Writer、Runner、Reviewer）、教程 FAISS 检索、审查修正循环及工具接口分离。本项目不直接复制代码；Foam-Agent 的教程 RAG 不等同于 CFD-Memo 要验证的跨任务经验记忆。
- [MetaOpenFOAM 论文](https://arxiv.org/abs/2407.21320)：作为另一项 LLM 多 Agent CFD 自动化参考，用于比较任务分工与工作流设计，不作为本项目已经实现的功能。
- 本项目先沿用现有 Python 模块组织流程；MCP、Skill 是后续可选接口。教程检索与跨任务经验记忆分别评估，不预设其他项目完全没有记忆能力。

## 2. 当前起点与最小范围

代码位于 `cfd-memo-agent/`。已实现 C1–C6、阶段 D 三 Agent 闭环及 E1–E2 跨任务记忆：规则/模型规划、受限配置意图、配置生成、验证、运行、Reviewer 修正、本地 episode、结构化与本地向量检索、三 Agent 经验注入、置信度降权、中文报告及 WSL/OpenFOAM 10 后端；CLI 提供 `plan/generate/validate/run/diagnose`。

首个算例固定为二维、不可压缩、层流圆柱绕流，使用 `icoFoam`；默认 `Re=100`、`U=1 m/s`、`D=1 m`，由 `nu=U*D/Re` 得到运动黏度。先只支持此算例，GUI、多相流和湍流不纳入首版。

**首个物理 baseline 已验收：** 项目自编网格已在 Foundation OpenFOAM 10 中手工和自动跑通；八组真实计算完成 `Cd`、`Cl`、`St` 参考对照及网格/时间步独立性检查。原单环网格未收敛后改为双环 wake-focused 局部加密，29440 到 47200 单元的三项变化均通过门槛，汇总 `physical_validated=true`。该结论只适用于固定的 Re=100 二维圆柱 benchmark。运行环境固定为 WSL2 Ubuntu 22.04，Windows 保留现有 Python 环境。

区分两级完成：**工程 MVP 与首个物理 benchmark** 已完成；**研究原型** 还须完成 LLM、多 Agent、记忆复用和对比实验。模拟成功不能计入真实 CFD 成功率。

## 3. 一次任务如何执行

CLI 接收需求或任务文件，由 `workflow.py` 统一调度；模块只负责各自的工作。当前工作流已串联规划、生成、验证、运行、修正与跨任务记忆；原 C4 `run --run` 保留单次执行行为。

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

- [x] E1 四层记忆基础：工作记忆保存当前状态；情景记忆归档完整 episode；知识记忆保存带证据的配置修正规则；程序记忆保存通过配置复验的操作步骤。
- [x] E1 JSON 与结构化标签检索：按算例、求解器、流动模型、维度和错误 code 筛选已验证经验；重启程序后仍可检索并由 Reviewer 引用。
- [x] E2 本地向量编码与检索：在结构化适用条件硬过滤后，按自然语言/边界语义、Re 相似度、错误 code 和置信度排序；当前为可复现的哈希词法向量基线，不宣称通用语义 embedding 能力。
- [x] 统一 `MemoryManager` 服务 Planner、Case Writer 和 Reviewer；分别记录规划、生成和修正阶段的检索 query、分数、引用与效果，共享知识和当前工作状态分开保存。
- [x] 三阶段经验注入：Planner 增加防错假设，Case Writer 生成受限防错文件清单，Reviewer 引用与当前 finding 匹配的经验。已知首轮配置漂移可在 runner 前从本次可信 reference 恢复并留下 prevention 证据。
- [x] 经验可信度：候选经验经配置复验后可用，重复成功合并证据；连续失败会降低置信度，达到阈值后降回 candidate 并停止检索。

验收：重启程序后，第二个相似但不同的任务能引用第一次的经验并影响具体决策；相同失败能被预防或更快修正，且完整保留证据。同步扩展 schema 与样例，使任务输入、每轮尝试、经验来源和验证状态可追溯。

### F：可选 MCP / Skill 接入

核心接口稳定后，再暴露规划、生成、运行、诊断等工具；Skill 说明调用顺序。不把此阶段作为实验或结题的前置条件。

### G：实验与交付

按下一节固定协议运行，提交原型系统、可复现实验记录、经验库、失败案例库、系统文档和结题报告；论文作为研究成果目标，不预先承诺效果或录用。

- [x] 冻结单算例配置记忆协议：四组、四个参数任务、两类受控故障、修正预算、重复次数和统计口径已写入机器可读协议。
- [x] 实现实验运行器：训练/评估分离，评估期冻结记忆，保存逐任务 episode、汇总 JSON 和中文报告。
- [x] 完成一次 pilot：4 组 × 4 参数任务 × 2 故障，共 32 个评估任务；结果用于验证工具链，不作为正式统计结论。
- [x] 完成正式 3 次重复实验：96 个评估 episode 已生成，并计算 95% Wilson 区间、修正次数和耗时标准差及按故障拆分结果。
- [x] 生成结题用四组对比图，整理无记忆、缓存命中/未命中、仅检索和提前预防五条代表性决策路径。
- [ ] 增加未知故障与模型模式的外部有效性实验；物理泛化实验另行设计。

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

**G 的正式配置实验已完成，下一项是结果可视化和外部有效性验证。** 96 个评估 episode 与统计分析已经生成；下一步制作结题图表和失败案例说明，并另行设计带模型采样或未知故障的实验。不要把模拟配置实验解释为 OpenFOAM 物理准确性或跨场景泛化。

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

C5/E 输出到 `cases/runs/workflow-<唯一编号>/`：input、task 检查、可信配置 reference、逐轮 rounds、episode.json 和 report.md。`--fault` 仅用于模拟配置故障；`--scenario` 保留给 C4 日志播放。显式 `--memory-mode cfd_memo` 时还会在忽略上传的本地 memory 目录保存跨任务经验、检索分数和使用证据。

隐私约束：`科研立项/` 继续由 `.gitignore` 忽略；原始申请书、个人资料和密钥不复制到代码或公开报告。调用外部模型只发送任务所需且经脱敏的数据。本路线图不包含原始申请书内容或私人文件链接。

## 7. 后续路线：从单场景原型到可扩展工具

以下阶段接续原 A-G 阶段。固定算例只作为验收基准，**不能成为程序支持范围的硬编码清单**。目标不是复制 Foam-Agent，而是在现有可靠执行闭环上建立通用 case 能力，并突出长期经验记忆。

### H：通用 Case 能力层

- 定义统一 `CaseSpec`：描述物理模型、求解器、网格来源、场变量、边界、时间控制和结果要求。
- 建立能力注册表：求解器能力、必需字段、文件依赖、执行命令和结果检查分别注册，不再散落在圆柱判断中。
- 将现有圆柱实现迁移为第一个能力实现，迁移期间保持原命令和结果不变。
- 支持导入已有 OpenFOAM case：保留只读原件，在工作副本中识别求解器、边界、网格和已有结果，然后进入同一验证、运行和修正流程。

**验收：** 导入一个仓库外的合法 case 时，不增加针对其几何名称的 Python 分支也能检查和运行；不支持的求解器返回明确的能力缺口。

### I：教程检索与受控配置生成

- 从本机对应版本的 OpenFOAM 官方 tutorials 建立按求解器、物理模型、字段和边界类型组织的参考库。
- Planner 先检索相近教程，再生成 `CaseSpec`；Case Writer 依据文件依赖顺序生成或修改配置。
- LLM 只能修改任务工作目录中声明过的 OpenFOAM 文件；所有输出必须经过语法、跨文件、网格和运行检查。
- 方腔流、圆柱绕流和后台阶作为回归基准，用来检验通用流程，不为每个基准各写一套 workflow。

**验收：** 至少三个不同基准、两个求解器通过同一入口完成；新增同类教程 case 时只增加数据或能力描述，不修改核心调度代码。

### J：网格与执行扩展

- [x] J1 统一模板生成、教程 `blockMesh` 和已有 `polyMesh` 的 `MeshSpec`；网格工具与求解器解耦。runner 从可信注册表规划命令，不接受模型或 JSON 直接拼接 shell 命令。Gmsh 已登记为明确的未实现能力缺口。
- [x] J2 实现外部 Gmsh 2.2 ASCII 网格的安全导入、`gmshToFoam` 转换、一对一边界映射和转换后校验；首个 `cavity-2d` 外部网格已真实完成。
- [x] J3 增加阶段状态、不可变 checkpoint、新 attempt 恢复、超时/中断清理、磁盘与日志限制和结果完整性索引；恢复时强制重新执行 `checkMesh`。

**验收：** 至少两种网格来源真实运行成功；中断后可以从已保存状态继续；失败不会污染原始 case 或旧结果。

### K：真实长期记忆与反馈

- [x] K1 从 episode/attempt 提炼经验，保存适用条件、修改动作和带 SHA-256 的来源证据；建立 `candidate`、`config_verified`、`run_verified`、`physics_verified` 四级可信度，模型反思和用户批准均不能直接升级机器验证等级。
- [x] K1 增加 `memory extract/list/approve/reject/enable/disable/delete` 入口；停用经验不参与检索，连续失败仍会自动降回 candidate。旧版无哈希 `verified` 记录按 candidate 读取，避免继承过度声明。
- [x] K2 保留结构化硬过滤并增加可替换 `EmbeddingProvider`；默认离线 hashing，可注入本地或托管 dense embedding。episode 记录提供者/版本、分数组成、命中原因、Agent 决策与最终有效性，经验库持久化 `usage_history`。
- [x] K3a 增加经验冲突检测与显式解决：相同适用条件/问题代码却给出不同动作时，双方停止检索；用户选择保留项后，另一项停用。
- [x] K3b 增加递增修订历史及真实跨任务验收 manifest/CLI；验收强制检查真实 runner、来源修正后完成、不同任务、`run_verified` 经验有效引用和目标修正次数减少，模拟记录不能通过。
- [x] K3c 新增隔离的 `memory collect-transfer` 采集命令，并完成一对真实 OpenFOAM 验收：来源 Re=100 在边界配置失败后修正 1 次并完成，经验达到 `run_verified`；目标 Re=120 引用该经验，在首次真实执行前留下 prevention，修正次数降为 0。七项迁移检查全部通过；两次运行均使用 rules provider，无 token 费用。

**验收：** 真实失败形成的经验能在相似新任务中被引用并减少试错；错误经验可追踪、降权和撤销；重启后仍可复用。

K2 已通过默认与自定义 embedding、无效向量拒绝、三 Agent 引用结果回写及重启后记录读取测试。K3 的冲突、修订、真实采集和跨任务验收已完成；该实验只证明配置经验减少一次受控重复试错，`physical_validated=false`，不代表物理精度验证。下一步进入 L 阶段。

### L：面向用户的任务工作台

- [x] L1 增加 `new`、`inspect`、`history`、`explain`，并与现有 `run`、`resume` 组成用户工作台；统一任务状态、物理验证状态、经验引用和 task/report/case/log 输出索引。
- [x] L2 为 task 真实执行增加 `--preview` 与任务绑定确认码：展示参数、Planner 假设/问题、验证提示、经验可信等级与来源、物理/网格/环境风险；问题未解决时禁止确认。确认码同时绑定 task SHA-256、runner、timeout、修正预算、记忆配置和经验集合，内容变化后失效；workflow 在生成 case 前复核并保存 `preflight.json`。
- [x] L3 统一生成 `report.md` 与经过 schema 验证的 `report-summary.json`：固定回答做了什么、是否运行完成、物理结果是否可信、经验是否产生有效贡献和下一步操作；详细轮次与日志仍保留。只有有效 outcome 或 prevention 才归因于经验，单纯检索不冒充贡献；工作台输出索引可直接定位摘要。

**验收：** 新用户只依据 README 即可导入或创建任务、确认计划、运行、恢复失败任务并找到结果，不需要理解内部目录结构。

### M：跨任务实验与结题交付

- [x] M1 冻结 `cross-task-v1`：30 个唯一任务规格覆盖圆柱、方腔、后台阶三个任务族及 `icoFoam`/`simpleFoam`；四类分区为同场景新参数、已知故障、未知故障和跨场景迁移。五个锚点在四种记忆策略下配对形成 20 个计划中的真实评估，审计器逐条还原完整 task 并检查 schema、适配器、唯一性和组间公平性。固定 DeepSeek 模型名、token 上限、超时、修正预算、环境、指标和成功定义；M1 不执行模型或 OpenFOAM。
- [x] M2 实现并执行跨任务配对评估：
  - [x] 六类隔离测试夹具覆盖已知故障、未知故障和跨场景迁移；已知的边界/黏度错误可证据化恢复，时间与网格风险保持只诊断。
  - [x] 用非评估任务建立训练存储，为三种记忆组复制独立只读快照；评估前后校验目录 SHA-256，禁止测试数据写回记忆。
  - [x] 批量 runner 逐项原子保存状态，支持 `--limit` 和 `--resume`；记录实际模型、token、价格区间、episode 与错误，不保存密钥。
  - [x] 按 M1 清单完成 20 次真实 runner 配对评估：20 个槽位均有 episode，12 个工程完成、8 个因不支持的安全修正停止，基础设施失败为 0；失败样本同样保留为正式结果。总计 61,930 token，按执行时价格快照估算 `$0.0126-$0.0252`。只有通过配置准入的轮次才实际启动 OpenFOAM，不能把验证前停止伪装成求解。
- [x] M3 从 20 个真实 episode 重算最终/首轮成功率、修正次数、耗时、token/成本、有效经验任务率和失败避免率；报告 Wilson 95% 区间、与无记忆组的配对 McNemar 检验及八个失败案例。四组最终成功率均为 60%；CFD-Memo 首轮成功率 40%、失败避免率 50%、平均修正 0.2（无记忆为 0%、0%、0.6），但首轮配对 `p=0.5` 且每组仅五个任务，不能宣称统计显著或广泛物理泛化。
- [x] M4 生成脱敏可复现交付包：以源码内容 SHA-256、Python/依赖/OpenFOAM/模型与协议版本冻结环境；提供安装与复现命令、聚合统计、四条有效记忆证据、八条失败案例和限制说明。公开包不含本机路径、密钥、响应 ID、原始 episode、OpenFOAM 场文件或科研原始资料；仓库尚无明确开源许可证，发布前需由维护者选择许可证。

**验收：** 至少三个任务族、两个求解器、30 个不同任务规格和 20 次真实 OpenFOAM 评估；每个研究结论可追溯到 task、case、日志、episode 和统计结果。

### 当前下一步

H1 已完成 `CaseSpec`、求解器能力注册表和已有 case 的安全导入：保存原件与工作副本，支持统一验证，并按注册命令真实执行而不运行导入脚本。

H2 已完成生成与验证适配器层：核心 generator/validator 只选择适配器，圆柱网格生成和专用检查已集中到 `case_adapters/`；新生成任务同时保存 `CaseSpec`。现有圆柱命令、schema 和物理 baseline 保持兼容。

H3 已完成第二个 `cavity-2d-laminar` 适配器：任务 schema、规则/LLM Planner 输出约束、Case Writer 文件映射、模板生成、静态验证、命令计划和网格证据都通过适配器注册。真实 OpenFOAM 10 验收完成 `blockMesh -> checkMesh -> icoFoam`，20 x 20 网格得到 400 个单元，三个边界非空，结束时刻 `0.5` 的 `U/p` 通过有限数值和规模检查。该结果只证明工程执行可用，尚未进行方腔基准解和网格独立性对照，因此 `physical_validated=false`。

I1 已完成 OpenFOAM 官方教程只读索引和检索接口：从 `controlDict`、初始场及配置文件提取求解器、物理模型、字段、边界类型、网格工具和来源路径；支持 CLI 的结构化过滤、关键词排序、解析警告和可追溯匹配理由。索引保存在本地 Git 忽略目录，明确记录 `scripts_executed=false`，不会读取或执行教程脚本。

I2 已完成 Planner 教程检索接入：已注册圆柱/方腔任务优先走确定性适配器；未注册场景自动检索本地官方教程，将候选、匹配理由和来源路径写入规划状态。Planner 只能引用候选中的 `tutorial_id`，提案由确定性代码固定为 `executable=false`。工作流保存 `case-spec-proposal.json` 和 episode 后以 `proposal_ready` 停止，不调用 Case Writer、Generator 或 OpenFOAM。

I3 已完成首个受控教程 Case Builder：只批准 Foundation OpenFOAM 10 的 `incompressible/simpleFoam/pitzDaily`，把 I2 提案转换为正式 `backward-step-2d` task 和教程来源型 `CaseSpec`。Builder 校验索引与白名单指纹，只复制必要字典，不复制或执行脚本；后台阶适配器负责静态一致性、固定命令计划和稳态结果证据。本机真实执行 `blockMesh -> checkMesh -> simpleFoam` 通过，网格 12225 单元，求解器在第 287 次迭代按教程收敛条件停止，最终 `U/p` 有限且规模正确。该结果仅证明工程执行，`physical_validated=false`。

I4 已完成教程提案的显式批准续跑：`resume --approve-tutorial --runner real` 将 I2 提案、I3 白名单 Builder、静态验证、网格检查和真实求解串成一条可审计链。系统复核原 episode、工作流路径和提案内容，拒绝被篡改的输入及模拟执行；批准、父记录、构建和执行证据写入独立 `resumes/resume-*`，原提案 episode 保持不变。报告区分原提案、用户批准、工程完成和物理未验证。

I5 已完成教程能力数据化和第二官方基准：可安装的 `tutorial_capabilities.json` 声明版本、求解器、物理模型、adapter、字段、网格工具、文件白名单和冻结 task，通用 Builder 不再包含按教程 ID 分支。新增 OpenFOAM 10 `incompressible/icoFoam/cavity/cavity`，只复制批准字典并受控展开 `$p` 引用，将官方 `timeStep` 写出控制映射为 task 的 `runTime` 语义。自然语言提案经同一 `resume` 入口真实完成 `blockMesh -> checkMesh -> icoFoam`，400 个单元运行至 `t=0.5`，`U/p` 结果证据通过；原提案、两次失败续跑和最终成功续跑均保留。后台阶回归保持通过，两教程覆盖 `simpleFoam`/RANS/稳态与 `icoFoam`/层流/瞬态。两者仍是已审查能力，不代表支持任意教程，且 `physical_validated=false`。

J1 已完成统一网格来源协议：生成 case、教程 case 和导入 case 都保存或推导 `MeshSpec`；`blockMesh` 与已有 `polyMesh` 通过同一能力注册表形成不同命令序列，runner 不再读取 adapter 的命令计划。保存计划若被篡改会停止执行；Gmsh 只公开能力缺口，不会被误执行。现有三个真实基准仍使用原求解器与网格配置，`physical_validated` 状态不变。

J2 已完成外部 Gmsh 的受控导入与真实执行：导入器限制为 2.2 ASCII，检查节点、六面体、几何范围、单元数、物理组和边界映射，并保存来源与工作副本哈希。runner 仅按可信计划执行 `gmshToFoam -> checkMesh -> icoFoam`，转换后受控设置 `wall/empty` patch 类型。2 x 2 方腔外部网格真实得到 4 个单元并运行至 `t=0.05`，最终 `U/p` 证据通过；该小网格只用于工程验收，`physical_validated=false`。

J3 已完成阶段级安全恢复和资源保护：真实 attempt 原子保存 `stage-state.json`，网格阶段保存带 SHA-256 的 checkpoint；恢复总是创建新 attempt，核对原始输入、执行计划和 checkpoint，复用 `blockMesh/gmshToFoam` 后强制重跑 `checkMesh`。命令保留超时与进程组清理，并增加运行前磁盘空间、单日志大小限制。`result-index.json` 给输入、状态、日志、证据和最终场文件建立完整性索引。真实 Gmsh 验收先在 `checkMesh` 超时，随后从 `gmshToFoam` checkpoint 恢复并运行至 `t=10`，原失败 attempt 保持不变。

K1-K3 已完成证据化长期记忆，L1-L3 已完成用户工作台。M1-M4 已完成冻结协议、20 个真实配对槽位、可复现统计和脱敏交付包。结果支持“已知圆柱配置故障可减少修正”的有限趋势，但不支持最终成功率提升或统计显著性结论。下一步不是继续堆功能，而是人工审阅公开包、选择许可证、提交并推送代码，然后以 M3 的真实边界撰写结题材料；新的场景泛化和物理验证应另建下一版协议。

### N1：显式结果可视化

- [x] 新增 `view --run <目录>`，从运行目录、工作流或 attempt 中选择最新完成的真实 OpenFOAM 结果。
- [x] 打开前检查 `constant/polyMesh`，并按 `result-index.json` 复核最终 `U/p` 的大小与 SHA-256；模拟结果、缺失或被篡改的场文件不会启动 GUI。
- [x] Windows 后端通过固定的 Ubuntu-22.04、OpenFOAM 10 环境和 WSLg 非阻塞启动 `paraFoam -builtin`；用户路径不作为 shell 命令执行。
- [x] `run` 和批量实验保持无界面，只有用户主动执行 `view` 才弹出 ParaView；显示结果不会改变 `physical_validated`。
- [x] 单元与 CLI 回归覆盖结果选择、模拟拒绝、网格缺失、场文件哈希、空格路径和假启动器；完整测试套件通过。
