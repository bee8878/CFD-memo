# CFD-Memo Agent

CFD-Memo 已实现 C1–C5，并完成 C6 的真实工程执行闭环：规则规划、配置生成、
验证、运行、有限修正、记录，以及通过 WSL 调用 Foundation OpenFOAM 10。

This project is a local engineering prototype for a memory-enhanced CFD agent. It does not copy or depend on sensitive research application files under `../科研立项/`.

## Project Structure

- Python package structure under `src/cfd_memo_agent/`
- CFD task and episode memory JSON schemas under `schemas/`
- A readable OpenFOAM-style 2D cylinder baseline case under `cases/templates/cylinder-2d/`
- Simulated runner logs under `cases/runs/sample-logs/`
- Experiment and schema notes under `docs/`

The current machine has the fixed C6 OpenFOAM backend. Sample logs remain
available only for explicit simulated tests.

## D1：统一模型接口

D1 已建立统一的结构化模型调用接口，默认仍使用现有规则 planner，不会自动发起
网络请求。查看当前脱敏配置：

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli model-info
```

OpenAI 模式使用 Responses API 与 Structured Outputs。配置只从环境变量读取；
`OPENAI_API_KEY` 的值不会写入 task、episode、报告或日志。D1 提供接口、
错误处理、调用元数据和假模型测试；D2 已让 Planner Agent 实际使用该接口，D3 已接入
Case Writer Agent。详细说明见
[D1 模型接口](docs/model-interface.md)。

## D2：Planner Agent 与共享状态

默认仍为离线规则模式，原命令和输出保持兼容。使用 `--details` 可查看请求 provider、
实际规划模式、提示词版本、假设、待确认问题和脱敏 trace：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli plan "做 Re=100 的二维圆柱绕流" --details
~~~

使用 DeepSeek 时，只在当前 PowerShell 会话设置真实密钥，不要写入代码或提交到 Git：

~~~powershell
$env:CFD_MEMO_MODEL_PROVIDER="deepseek"
$env:CFD_MEMO_MODEL="deepseek-flash"
$env:DEEPSEEK_API_KEY="<仅保存在本机的API密钥>"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli model-info
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli plan "做 Re=100 的二维圆柱绕流" --details
~~~

OpenAI 仍受支持，对应 provider、模型和密钥变量分别为 `openai`、
`CFD_MEMO_MODEL` 和 `OPENAI_API_KEY`。两种 provider 都只允许各自官方
Responses API 地址。

项目启动时会固定读取本目录的 `.env`，与启动命令所在目录无关。首次配置可以执行：

~~~powershell
Copy-Item .env.example .env
notepad .env
~~~

将 `.env` 中的 provider、模型和本机密钥改为实际值。`.env` 已被 Git 忽略；
`.env.example` 只能保留空密钥。若系统环境变量与 `.env` 同名，系统环境变量优先，
防止本地文件覆盖临时或部署环境配置。

模型只产生候选任务。候选必须再次通过 JSON Schema 和 `validate_task` 的 CFD 规则，
才能进入 Generator。接口失败默认停止；只有显式添加 `--fallback rules`（`plan`）
或 `--planner-fallback rules`（`generate`/`run`）才使用规则 Planner，并在记录中注明。
完整工作流另存 `planning.json`，同时把摘要写入 `episode.json` 和中文报告。

## Python Development Environment

Run these commands from this directory. Create a separate virtual environment
for each project; do not install this project's dependencies globally.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/test_planner.py -v
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli plan "cylinder flow Re=100"
```

The environment uses Python 3.13, which satisfies the project's Python >=3.12
requirement. The editable install makes source changes available immediately.
The `.venv/` directory is local and must not be committed.

If the repository is moved or renamed, rerun `python -m pip install -e ".[dev]"`
with this virtual environment. Editable-install metadata contains the source path;
without reinstalling, commands started outside the project directory may import
the old location and fail.

In VS Code, select `.venv/Scripts/python.exe` using **Python: Select Interpreter**.
Open this directory as the workspace, or use the repository workspace file
`../CFD-Memo.code-workspace`. Use the Testing view or `python -m pytest` for
tests rather than running the test file as a normal script.

Activation is optional when using the explicit interpreter path above. To use
short commands in PowerShell, activate with `.\.venv\Scripts\Activate.ps1`,
then use `python -m pytest`; run `deactivate` before changing projects.

## C2：生成 case 配置

从本目录运行，使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli generate "做 Re=200 的二维圆柱绕流，入口速度=2，D=1"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli generate --task examples/task.cylinder-2d.json
.\.venv\Scripts\python.exe -m pytest tests -v
```

每次生成新的 `cases/runs/run-<时间>-<唯一编号>/`，包含 `task.json`、
`generation.json` 和 `case/`。输出 JSON 中的 `case_path` 指向生成目录。
支持 `--runs-dir` 自定义输出位置，`--template-dir` 显式选择同结构模板。
默认模板路径来自源码工程。当前使用源码可编辑安装，任务 schema 从仓库
的 schemas/task.schema.json 读取；尚不支持脱离仓库的独立 wheel 运行。

生成器复制模板，写入入口速度、运动黏度、求解器、时间控制和计算域顶点。
圆柱直径及计算域尺寸写入 `case/constant/geometry.json`，该文件是项目元数据，
不由 OpenFOAM 读取。边界设置沿用默认配置；其他求解器或边界组合会被拒绝。

当前生成八块贴体圆柱 O-grid，含圆弧、32 个顶点、厚度方向一层网格。
`target_cells` 决定整数划分，默认目标 10000 对应计划 10368 个单元；
实际数量由运行后读取网格确定。`mesh_verified=false` 保留到真实检查通过。
本机已在 OpenFOAM 10 中完成网格和求解工程验收；默认网格为 10368 个单元，
圆柱工程 baseline 已完成真实网格、求解和场文件检查；物理验收另由固定研究矩阵汇总，
不能用单次 `completed` 代替物理准确性结论。

C6 已接入圆柱 `forceCoeffs`，并完成八组 `Re=100` 真实研究。原单环全局加密序列暴露出
升力不收敛后，网格改为双环局部加密：圆柱到 `10D` 的近场、尾迹扇区和远场分别控制。
`29440 → 47200` 单元时，`Cd/Cl/St` 的变化分别为 `0.088%/1.83%/0.727%`，
全部通过门槛；最终 `Cd=1.32566`、`Cl` 振幅 `0.33536`、`St=0.16309` 也进入
预设参考区间。研究汇总现为 `physical_validated=true`。协议与命令见
[C6 物理验收协议](docs/physical-validation.md)。

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli physics-study `
  --task examples\task.cylinder-2d-physical.json `
  --baseline-episode "cases\runs\某次真实基线\episode.json" `
  --timeout 1800
```

## C3：检查任务与配置

从本目录运行。先用 generate 命令生成 case，再将其输出的 run_path 填入下面
的 --run 参数。升级后的依赖只安装到项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli validate --run "cases/runs/你的运行目录"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli validate --run "cases/runs/你的运行目录" --output validation.json
.\.venv\Scripts\python.exe -m pytest tests -q
```

validate 读取该目录中的 task.json 和 case/，不会重新调用 planner 或修改 case。
默认只在终端显示报告。--output 使用新建方式保存 UTF-8 JSON，父目录须已存在，
目标文件已存在则拒绝覆盖。报告中的中文用于解释，code 和 location 用于程序定位。

| 字段 | 含义 |
| --- | --- |
| task_valid | 任务结构、数值和物理参数关系是否通过 |
| config_valid | 任务及当前支持的文件一致性检查是否全部通过 |
| errors | 导致检查不通过的问题，含 code、message、location |
| warnings | 网格数量取整、收敛目标尚未验证等提示 |
| runtime_blockers | 空边界、网格未经真实验证等运行阻碍 |
| mesh_verified | C3 始终为 false，不宣称验证了真实网格 |

退出码：一致性通过为 0，任务/文件检查失败为 1，命令用法或报告保存失败为 2。
**退出码 0 不表示可以启动真实求解**；后续 runner 还必须处理 runtime_blockers
并完成真实网格检查。当前圆柱模板可以 config_valid=true，同时保留运行阻碍。

Python 接口：

```python
from cfd_memo_agent.validator import validate_task, validate_case

task_report = validate_task(task)
case_report = validate_case(task, case_dir)
```

只检查任务时 config_valid=false，表示尚未检查 case。generate_case 在创建
run 目录前调用 validate_task；非法任务会报错且不产生新目录。

当前检查支持仓库的八块圆柱 ASCII 网格及默认六个边界；旧矩形模板可诊断但禁止真实求解。
解析支持注释、换行和科学计数法；不支持 #include 等预处理指令、变量展开
或任意 OpenFOAM 语法。重复项、缺括号、缺分号等会明确报错。
数值格式仅检查必要结构，不能代替 OpenFOAM 的语义检查和收敛验证。

## C4：单次运行与日志诊断

C4 接收 C2 已保存的运行目录，重新执行 C3 检查，再执行一次并输出 JSON。
将下面的“你的运行目录”替换成 generate 输出的 run_path；--runner 必须显式指定。

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --run "cases/runs/你的运行目录" --runner simulated
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --run "cases/runs/你的运行目录" --runner simulated --scenario missing-boundary
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli diagnose --log cases/runs/sample-logs/bad-transport.log --returncode 1 --simulated
.\.venv\Scripts\python.exe -m pytest tests/test_runner.py -q
~~~

模拟场景有 success、missing-boundary、bad-transport、unknown-failure。
模拟模式复制对应样例日志并使用预设退出码，不启动 OpenFOAM、不生成流场。
成功状态是 simulated_success；物理验证始终为 physical_validated=false。
配置错误会阻止模拟执行；空圆柱网格等真实运行阻碍保留在 validation 和
validation_findings 中，不妨碍显式选择的日志模拟。

每次调用在该 run 下新建 attempts/attempt-<唯一编号>/，保存：

- validation.json：本次执行前的 C3 检查。
- execution.json：模式、场景、状态、各命令退出码、超时标记、耗时及路径。
- diagnosis.json：错误位置、证据、建议，以及时间步和残差记录。
- icoFoam.log：模拟日志；真实模式还会有 blockMesh.log、checkMesh.log。
- case/：仅真实执行时复制输入 case，命令在此副本内运行，结果也位于此处。

真实命令入口为：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --run "cases/runs/你的运行目录" --runner real --timeout 300
~~~

真实模式固定 Foundation OpenFOAM 10。原生后端检查 PATH 和版本；Windows
可调用 Ubuntu-22.04 内的 OpenFOAM，自动加载 /opt/openfoam10/etc/bashrc。
静态配置通过后允许 blockMesh/checkMesh；只有实际网格检查和输出检查通过才启动 icoFoam。
MESH_NOT_VERIFIED 作为待执行检查保留在静态报告中，不是直接删除或跳过验证。
环境缺失仍停止，不回退模拟。安装与手工验收步骤见 [C6 运行指南](docs/c6-real-run.md)。

准入通过后的后端依次执行 blockMesh、checkMesh、icoFoam，每个命令有独立
超时预算，失败或超时即停止后续命令。日志合并 stdout/stderr；checkMesh
须有 Mesh OK.，其他命令须有独立的 End 行，同时检查退出码和已知错误。
真实模式 completed 还要求本轮网格和结束时刻的 U/p 文件通过检查；
残差只提取，不判定收敛或物理正确。mesh-evidence.json 和 result-evidence.json
保存实际单元数、配置与网格指纹、结果路径，旧结果不会复制进新 attempt。

诊断 action 分为 rule_candidate（候选规则修正）、user_clarification（需确认任务）、
manual_required（人工处理）。C4 单次执行只提供建议；下面的 C5 工作流
负责有限修正、重试、episode 和报告。独立 diagnose 支持 --stage、--timed-out、--output；
--returncode 必须填入实际记录，输出文件已存在时拒绝覆盖。

run/diagnose 退出码：执行或样例检查完成为 0，失败、超时或被阻止为 1，
参数错误、日志输入无法读取或报告无法保存为 2。重复运行保留旧记录。

Python 接口：

~~~python
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.diagnoser import diagnose_log, diagnose_validation

result = run_case(run_dir, mode="simulated", scenario="bad-transport")
diagnosis = diagnose_log(log_text, returncode=1, stage="icoFoam")
~~~

## C5：完整工作流与有限修正

从本目录运行，一条命令完成规划、生成、验证、模拟执行和报告：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" --runner simulated
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --task examples/task.cylinder-2d.json --runner simulated
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" --runner simulated --fault missing-boundary
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" --runner simulated --fault bad-transport --max-corrections 0
~~~

--fault 会在首轮 case 副本中删除出口速度边界或写入负黏度，并保存修改前后内容。
首轮因此被 C3 拦截，不启动求解，也不播放失败日志。可修正时创建下一轮副本，
恢复对应文件并重新验证；通过后才读取成功模拟日志。成功日志仍是固定样例，
并非当前任务的真实计算结果；演示验证的是文件修复及调度逻辑。

自动修正仅支持默认模板的速度/压力边界字段，以及 nu 数值、量纲或字段缺失。
修正使用有效 task 和默认模板生成的可信文件；文件中若存在其他不匹配设置，
或错误没有对应规则，便停止处理。不会更改任务的 Re、几何或物理模型。

--max-corrections 优先于 task 的 convergence.max_corrections，否则默认 2。
首次尝试不计入次数；0 表示禁用修正。每次有实际修改才计数。
未知错误、没有实际变更、超时、环境与真实网格阻碍均会停止，不反复空跑。

每次创建 cases/runs/workflow-<唯一编号>/：

~~~text
input.json                 原始需求或任务文件文本
task-validation.json       任务检查结果
task.json                  仅在任务有效时保存
reference/                 从有效任务生成的可信配置
rounds/round-000/           首轮 task、case、状态及 C4 attempts
rounds/round-001/           修正后的副本，含 correction.json
episode.json               全过程结构化记录
report.md                  中文报告
~~~

各轮 started.json 和 round.json 保存开始、完成状态；失败轮次没有日志时，
记录空日志列表。生成前失败也保存 episode 和报告，不编造 case 或日志路径。
correction.json 与 episode 保存修改原因、前后文件内容和轮次关联。
原始输入、默认模板和旧轮次保留；--runs-dir 可指定工作流输出父目录。

C5 支持 --runner real，但环境缺失或网格检查失败仍会终止真实执行。即使命令执行完成，
completed 也不代表物理验证成功。模拟完成状态为 simulated_success。
失败、阻止、超时分别记录；退出码为 0（完成）、1（业务失败或被阻止）、
2（参数用法或记录保存错误）。用户中断会清理运行进程并尽可能保存已完成记录。

需求、--task、--run 三种输入互斥。原 C4 的 run --run <目录> 仍是单次执行；
--scenario 仅用于 C4。--fault、--max-corrections、--runs-dir 仅用于 C5。
默认使用规则 planner；设置 OpenAI 或 DeepSeek provider 后使用 D2 Planner Agent。模型故障不会
静默回退，只有 `--planner-fallback rules` 才允许并记录回退。
episode 标记 no_memory、experience_reused=false，候选经验未进入跨任务经验库。

## D3：Case Writer Agent

完整 `run` 工作流在 Planner 和 Generator 之间增加了 Case Writer。它不会直接写文件，
而是输出结构化 intent，明确固定模板、求解器以及 task 字段应映射到的 OpenFOAM 文件。
intent 包含 task 指纹；task 在规划后若被修改，旧 intent 会被拒绝。

生成器只允许 `0/U`、`0/p`、`constant/physicalProperties`、`system/controlDict`、
`system/blockMeshDict` 和 `constant/geometry.json` 的固定映射。通过后仍由原生成器写文件，
并继续经过 validator 和 runner，模型不能输出任意路径、命令或自行宣告运行成功。
每次工作流把完整决策保存为 `case-writing.json`，摘要和脱敏 trace 同时进入
`episode.json` 与中文报告。规则模式使用同一 intent 接口但不联网。

## D4：Reviewer Agent

每轮 validator/runner 完成后，Reviewer 只读取 task 摘要、执行状态、findings 和
runtime blockers，不直接读取密钥或修改 case。它输出 `accept`、`repair` 或 `stop`，
并记录错误 code、修正范围、原因、建议、适用条件和限制。

可执行决策由确定性证据锁定：无错误且本轮完成才能接受；所有错误都是
`rule_candidate` 才能建议修正；未知错误、超时和人工处理项必须停止。模型只能解释，
不能把失败说成成功。即使 Reviewer 建议修正，也必须由 `propose_repairs()` 再次核对，
仅恢复允许的边界或黏度文件，并在新一轮重新验证。

每轮保存 `rounds/round-<编号>/review.json`，episode 和报告汇总完整审查轨迹。
受控边界故障已真实通过 DeepSeek 验收：`repair -> 重新验证 -> accept`。

## E1-E2：结构化长期记忆与向量检索

默认 `no_memory` 行为不变。显式选择 `cfd_memo` 后，统一 `MemoryManager` 会分别维护
当前工作状态、完整 episode、带证据的修正规则和已验证操作步骤：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run `
  "做 Re=100 的二维圆柱绕流" --runner simulated --fault missing-boundary `
  --memory-mode cfd_memo --memory-dir cases/memory
~~~

第一次任务验证修正后生成经验；第二个相似任务使用同一目录时，Planner、Case Writer
和 Reviewer 分阶段检索并记录 experience ID。系统先按算例、求解器、流动模型和维度
硬过滤，再使用本地哈希向量、Re 相似度、错误 code 与置信度排序。Case Writer 只能把
经验允许的文件加入防错清单；首轮运行前若这些文件偏离本次可信 reference，工作流会
恢复对应文件并保存 prevention 证据。连续失败会降低经验置信度并停用该经验。

该向量器是无需网络和额外费用的可复现词法基线，不等于通用语义 embedding。
`verified` 仍只表示配置复验通过，不表示 CFD 物理准确性已经验证。本地
`cases/memory/` 已被 Git 忽略。详见 [E1-E2 记忆说明](docs/memory.md)。

## G：配置记忆对比实验

冻结协议提供 `no_memory`、`simple_cache`、`retrieval_only`、`cfd_memo` 四组，评估期
只读训练快照，防止测试任务污染记忆。运行：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli memory-study --repeats 3
~~~

结果保存为本地 `results.json` 与中文 `report.md`。这是模拟配置实验，不替代 C6 的真实
OpenFOAM 物理验收。详见 [冻结实验协议](docs/experiment-plan.md)。
一次 32 任务的离线 pilot 已完成，结果与限制见
[Pilot 结果](docs/memory-study-pilot.md)。
正式 3 次重复实验共生成 96 个评估 episode，统计结果见
[正式实验结果](docs/memory-study-results.md)。
聚合图表和五条决策路径分别见
[四组对比图](docs/assets/memory-study-comparison.svg) 与
[典型案例](docs/memory-study-cases.md)。

Python 接口与测试：

~~~python
from cfd_memo_agent.workflow import run_workflow

result = run_workflow("做 Re=100 的二维圆柱绕流",
                      mode="simulated", fault="missing-boundary")
~~~

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
~~~

## JSON 格式检查

```powershell
python -m json.tool schemas/task.schema.json
python -m json.tool schemas/episode.schema.json
```
