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

## L1：任务工作台

初学者不需要先寻找内部目录。下面四个命令负责创建任务、查看状态、查找历史和解释证据：

```powershell
# 只创建并保存任务，不运行 OpenFOAM
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli new `
  "做 Re=100 的二维圆柱绕流，入口速度=1，D=1"

# path 可以是 task.json、episode.json 或整个 workflow 目录
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli inspect "<path>"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli explain "<path>"

# 最近记录；可增加 --status completed 或 --limit 5
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli history
```

`new` 会保存经过验证的 task JSON 和对应的 `.plan.json`，不会生成 case 或调用
OpenFOAM；已有目标文件不会被覆盖。`inspect` 对 task 和 episode 使用同一状态格式，
并统一给出 task、报告、case 和日志索引。`explain` 特别区分“命令执行完成”和
“物理结果已验证”，然后给出下一条建议操作。现有 `run` 与 `resume` 继续负责实际
执行和安全恢复。

真实执行必须先预览并确认。预览不会创建 workflow，也不会运行 OpenFOAM：

```powershell
$preview = .\.venv\Scripts\python.exe -m cfd_memo_agent.cli run `
  --task "cases\tasks\<task>.json" --runner real --preview | ConvertFrom-Json

$preview.parameters
$preview.assumptions
$preview.memory.experiences
$preview.risks

.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run `
  --task "cases\tasks\<task>.json" --runner real `
  --confirm-plan $preview.confirmation_token
```

确认码绑定 task 内容、runner、超时、修正预算、记忆模式、记忆库位置和检索到的
经验编号。确认后修改 task 或上述执行选项，旧确认码就会失效。自然语言不能直接
进入真实 runner；应先用 `new` 冻结为 task。确认记录会保存为 workflow 内的
`preflight.json`，并写入 episode 和中文报告。模拟运行仍可直接使用，不强制确认。

每次工作流结束会同时生成：

- `report.md`：最前面用五个固定问题给出一页结论，后面保留逐轮证据。
- `report-summary.json`：供 `inspect`、实验统计或后续 Agent 读取的结构化摘要。

五个问题分别是：做了什么、是否运行完成、物理结果是否可信、经验起了什么作用、
下一步做什么。经验只有在保存了有效 outcome 或实际防错记录时才会被列为有效贡献；
仅检索但没有采用的经验不会被宣称为成功因素。`inspect <workflow>` 的
`outputs.summary` 可直接定位结构化摘要。

## M1：跨任务评估清单

正式评估 v1 已冻结 30 个任务规格，覆盖三个任务族和两个求解器；五个真实锚点在
四种记忆策略下配对，形成计划中的 20 次真实评估。M1 只审计协议，不调用模型或
OpenFOAM：

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli benchmark audit
```

命令会验证 task schema、适配器支持、参数唯一性、四类数据分区和四组配对公平性。
完整协议见 [跨任务评估协议](docs/cross-task-benchmark.md)。

M2 增加可恢复批量运行器。它先用非评估任务建立 simple-cache 与知识记忆，再复制成
彼此隔离的只读快照；每完成一个配对就原子更新 `state.json`。先用一项模拟评估检查
调度，不会调用模型或 OpenFOAM：

```powershell
python -m cfd_memo_agent.cli benchmark run --runner simulated --limit 1
```

真实批次必须使用已配置的 DeepSeek 和 OpenFOAM 10，可用 `--limit` 分批，并从输出目录
恢复。`state.json` 保存实际模型、token、价格快照和 episode 路径，但不保存 API 密钥：

```powershell
python -m cfd_memo_agent.cli benchmark run --runner real --limit 1
python -m cfd_memo_agent.cli benchmark run --runner real --resume <study目录> --limit 1
```

首轮正式 M2 已在本地 `cross-task-m2-real-v1` 完成 20/20 个槽位：12 个工程完成，
8 个保留为安全停止的失败样本，基础设施失败为零。该计数尚未替代 M3 的指标重算和
不确定性分析，也不表示三个任务族都完成了物理准确性验证。

M3 可从本地 episode 重算统计并生成中文失败案例报告；重复生成必须显式加 `--force`：

```powershell
python -m cfd_memo_agent.cli benchmark analyze `
  --study cases/runs/cross-task-m2-real-v1
```

当前结果中四组最终成功率相同；CFD-Memo 减少了修正并提前避免两个已知圆柱故障，
但每组仅五个任务且配对检验不显著，因此文档不把这一趋势表述为已证明的普遍提升。

M4 使用专用导出器生成脱敏交付包，不复制本地原始 episode 或 OpenFOAM 场文件：

```powershell
python -m cfd_memo_agent.cli benchmark package `
  --study cases/runs/cross-task-m2-real-v1
```

交付文件位于 `docs/results/cross-task-v1/`。公开前仍需人工审阅并选择开源许可证；仓库
当前没有许可证文件，生成交付包不等于授予第三方复用权利。

## H1：CaseSpec、能力注册表与已有 case 导入

阶段 H 的第一版把“任务是什么”和“程序会什么”从圆柱模板中分离出来：

- `CaseSpec` 统一记录求解器、物理模型、字段、边界、网格来源和时间控制。
- 能力注册表当前声明 `icoFoam` 的已验证导入能力，并把 `simpleFoam` 明确标为尚未
  支持；不会因为登记了求解器名称就假装能够生成或运行对应 case。
- `import-case` 检查已有 OpenFOAM 目录，保存 `original/` 原件、`case/` 工作副本、
  `case-spec.json` 和 `import.json`。符号链接和不支持的求解器会在复制前被拒绝。

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli capabilities
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli import-case "D:\OpenFOAM-Cases\cavity"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli validate --run "cases\runs\import-..."
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --run "cases\runs\import-..." --runner real
```

导入 case 只允许真实运行，不提供模拟成功。runner 不执行来源目录中的 `Allrun`
或其他脚本，只按能力注册表调用 `blockMesh`（需要时）、`checkMesh` 和求解器。
每次真实执行仍发生在独立 attempt 副本中，原件和导入工作副本保持不变。

H2 已把生成 case 的核心入口改成适配器调度。`generator.generate_case()` 和
`validator.validate_case()` 不再直接实现圆柱规则，而是选择 `cylinder-2d-laminar-v1`
适配器；圆柱网格写入和专用检查集中在 `case_adapters/`。每个新生成目录同时保存
`task.json` 和通用的 `case-spec.json`。新增场景仍须提供经过测试的适配器，但不再
修改核心 generator、validator 或 workflow。

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
K1 将经验可信度细分为 `candidate -> config_verified -> run_verified ->
physics_verified`。等级由 validation、真实 attempt、result index 和物理验收证据计算，
模型反思不能自行升级。旧版 `verified` 记录因没有文件哈希会按 `candidate` 读取。本地
`cases/memory/` 已被 Git 忽略。详见 [E1-E2 记忆说明](docs/memory.md)。

可以从已保存 episode 重新提炼、查看和管理经验：

~~~powershell
python -m cfd_memo_agent.cli memory extract --episode <episode.json> --memory-dir cases/memory
python -m cfd_memo_agent.cli memory list --memory-dir cases/memory
python -m cfd_memo_agent.cli memory approve <experience-id> --memory-dir cases/memory
python -m cfd_memo_agent.cli memory disable <experience-id> --memory-dir cases/memory
python -m cfd_memo_agent.cli memory delete <experience-id> --memory-dir cases/memory --confirm
~~~

批准只记录用户意见，不改变机器验证等级；停用后的经验不会进入 Agent 提示。

K2 将相似度计算抽象为 `EmbeddingProvider`。默认 `local-hashing` 完全离线且可复现；
Python 调用方可通过 `CallableEmbeddingProvider` 接入明确选择的本地或托管 embedding。
episode 会保存提供者/版本、硬过滤条件、各项分数、中文命中原因，以及经验具体影响了
哪个 Agent 决策。工作流结束后，引用结果标记为 `effective` 或 `ineffective` 并写回
经验的 `usage_history`。默认不会把任务文字发送给外部服务。

K3 增加经验冲突与修订追踪。相同适用条件和问题代码若对应不同修正动作，`memory audit`
会将双方标为 `conflicted`，在人工选择前都不会进入 Agent。`memory resolve` 显式保留一条
并停用另一条。`memory acceptance` 使用 manifest 检查真实跨任务迁移，强制要求真实 runner、
`run_verified` 证据、不同任务和更少修正次数；模拟 episode 不能通过。

~~~powershell
python -m cfd_memo_agent.cli memory audit --memory-dir cases/memory
python -m cfd_memo_agent.cli memory resolve <保留ID> <停用ID> `
  --memory-dir cases/memory --note "选择依据"
python -m cfd_memo_agent.cli memory acceptance `
  --manifest acceptance.json --memory-dir cases/memory --output acceptance-result.json
python -m cfd_memo_agent.cli memory collect-transfer `
  --task examples/task.cylinder-2d.json `
  --memory-dir cases/runs/k3c-memory --runs-dir cases/runs/k3c-acceptance
~~~

`collect-transfer` 只修改新建运行副本，强制使用 rules provider，不产生模型 token 费用。
本地 Re=100 → Re=120 真实验收已通过：来源修正 1 次，目标通过经验 prevention 将修正降为
0 次，经验为 `run_verified`。该结果不等于物理准确性验证。

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

## H3：第二个生成式场景

`cavity-2d` 是第二个注册适配器，基于 OpenFOAM 10 官方 lid-driven cavity
拓扑，使用 `icoFoam`、单块 `blockMesh` 和 `movingWall/fixedWalls/frontAndBack`
边界。它与圆柱共用 planner、Case Writer、generator、validator 和 runner 入口；
场景特有的生成、静态检查和网格证据位于 `case_adapters/`。

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli plan "做 Re=100 的二维顶盖驱动方腔"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli generate --task examples/task.cavity-2d.json
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --run "cases/runs/运行目录" --runner real
~~~

真实验收已完成 `blockMesh -> checkMesh -> icoFoam`：20 x 20 网格得到 400 个
单元，三个边界非空，结束时刻 `0.5` 的 `U/p` 可读取且数值有限。
这证明第二场景可经同一入口执行，不表示方腔结果已经过网格独立性或公开基准对照；
因此该场景仍保持 `physical_validated=false`。

## I1：OpenFOAM 官方教程检索

教程索引器只读取本机 OpenFOAM 字典文件，不读取或执行 `Allrun`、`Allclean`
等脚本。Windows 默认通过只读 WSL UNC 路径扫描 Ubuntu-22.04 中的
Foundation OpenFOAM 10，索引保存到 Git 忽略的本地目录：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli tutorials index
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli tutorials search `
  --query "顶盖驱动方腔" --solver icoFoam --field U --field p --mesh-tool blockMesh
~~~

可使用 `--root` 索引其他只读教程副本，使用 `--force` 原子替换已有索引。
检索支持 `--solver`、`--physics-model`、重复的 `--field`、重复的
`--boundary-type`、`--mesh-tool` 和 `--limit`。结果包含官方 case 来源路径、
匹配理由和解析警告。当前 I1 只提供可追溯检索，尚未让 Planner 自动采用教程配置。

## I2：Planner 自动引用教程

自然语言不属于已注册的圆柱或方腔场景时，Orchestrator 会读取本地教程索引，
把最多五个候选作为 `tutorial_context` 交给 Planner。已注册场景仍优先使用适配器，
不会被相似教程覆盖；参数非法的已注册场景仍按错误处理。

~~~powershell
python -m cfd_memo_agent.cli plan "做一个二维后台阶流动" --details
python -m cfd_memo_agent.cli run "做一个二维后台阶流动" --runner simulated
~~~

未注册场景只会得到 `reference_proposal`：工作流保存 `planning.json`、
`case-spec-proposal.json`、`episode.json` 和中文报告，然后以 `proposal_ready`
停止。提案固定为 `executable=false`，模型只能引用实际检索到的 `tutorial_id`，
不能创建 case、调用 Generator 或启动 OpenFOAM。可用 `--tutorial-index` 指向
另一个本地索引。当前提案用于后续适配器设计，不是可运行配置。

## I3：受控教程 Case Builder

I3 只批准 Foundation OpenFOAM 10 的 `incompressible/simpleFoam/pitzDaily`。
先用 I2 保存的 `case-spec-proposal.json` 构建独立工作副本，再显式执行：

~~~powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli tutorials build `
  --proposal "cases\runs\某次I2任务\case-spec-proposal.json"
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run `
  --run "cases\runs\tutorial-..." --runner real --timeout 300
~~~

Builder 验证提案、教程 ID、OpenFOAM 版本、索引能力和白名单指纹，只复制
`U/p/k/epsilon/nut`、物性、RANS 模型、网格及求解控制字典。`Allrun` 和其他脚本
不会复制或执行；来源教程保持只读。生成目录保存正式 `task.json`、教程来源型
`case-spec.json`、提案、来源清单和静态验证结果。

后台阶适配器固定使用 `simpleFoam`、RAS/kEpsilon 和 pitzDaily 边界，runner 仍只按
注册计划执行 `blockMesh -> checkMesh -> simpleFoam`。本机真实验收得到 12225 个单元，
`checkMesh` 通过；稳态求解在第 287 次迭代按教程条件提前停止，最后 `U/p` 可读取、
数值有限且长度与网格一致。该结果证明工程链路可用，`physical_validated` 仍为 false。
当前功能不是任意教程生成器，其他教程或参数必须先增加审查过的能力描述与测试。

## I4：显式批准并续跑教程提案

I2 产生的 `proposal_ready` 工作流不会自动执行。审核其
`case-spec-proposal.json` 后，可用独立命令继续：

```powershell
python -m cfd_memo_agent.cli resume `
  --workflow "cases\runs\workflow-..." `
  --approve-tutorial `
  --runner real `
  --timeout 300
```

`--approve-tutorial` 是必需的人为确认。I4 会复核原 episode 与保存的提案是否一致，
调用白名单 Builder，再执行 `blockMesh -> checkMesh -> simpleFoam`。原 I2 目录不被改写；
批准、构建、环境、网格、日志和场文件证据保存在原工作流的
`resumes/resume-*/` 子目录。当前不接受模拟 runner，工程完成仍保持
`physical_validated=false`。

## I5：数据化教程能力与第二基准

教程白名单不再写死在 Builder 中。可安装的数据文件
`src/cfd_memo_agent/tutorial_capabilities.json` 统一声明教程 ID、OpenFOAM
版本、求解器、物理模型、adapter、必需字段、网格工具、允许复制的文件及冻结任务。
查看当前能力：

```powershell
python -m cfd_memo_agent.cli capabilities
```

当前批准两个官方教程：`simpleFoam/pitzDaily` 和 `icoFoam/cavity/cavity`。
下面的未注册表述会先形成不可执行提案，再经人工批准进入同一个 Builder/runner：

```powershell
python -m cfd_memo_agent.cli run "复现 icoFoam movingWall 基准" `
  --runner simulated
python -m cfd_memo_agent.cli resume `
  --workflow "cases\runs\workflow-..." `
  --approve-tutorial --runner real --timeout 300
```

方腔 Builder 只复制七个批准字典，并将官方 `$p` 字典引用展开为静态可审查内容；
同时把 `writeControl timeStep` 显式映射为本项目 task 所定义的 `runTime` 语义。
真实验收得到 400 个单元，运行到 `t=0.5`，最终 `U/p` 证据通过。
两种教程共享 CLI、Builder、validator、runner、episode 和报告入口，但各自仍需要经过
审查的 adapter；这不是任意教程自动执行器，且 `physical_validated=false`。

## J1：统一网格能力

每个新生成或导入的运行目录现在保存 `mesh-spec.json`。它把网格来源与求解器分开，
明确记录所需输入、准备命令和检查命令。runner 从该文件和可信能力注册表生成命令序列：

- 模板或教程 `blockMesh`：`blockMesh -> checkMesh -> solver`。
- 已有 `constant/polyMesh`：`checkMesh -> solver`，不会重新生成网格。
- 外部 Gmsh：`gmshToFoam -> checkMesh -> solver`，只接受经检查的 2.2 ASCII 网格。

`mesh-spec.json` 不能注入任意 shell 命令；保存内容与代码注册表不一致时执行会停止。
使用 `python -m cfd_memo_agent.cli capabilities` 可查看求解器、case、教程和网格能力。
旧运行目录没有该文件时会从受验证的 task 或 `CaseSpec` 推导，以保持兼容。

## J2：外部 Gmsh 网格

外部网格先通过受控导入，不直接交给 shell：

```powershell
python -m cfd_memo_agent.cli import-mesh examples\cavity-2x2.msh `
  --task examples\task.cavity-2d-gmsh.json `
  --boundary-map examples\gmsh-boundary-map.json `
  --runs-dir cases\runs\gmsh
python -m cfd_memo_agent.cli run --run "cases\runs\gmsh\运行目录" `
  --runner real --timeout 120
```

导入器只支持 Gmsh 2.2 ASCII、三维六面体和四边形边界面；首版限已注册的
`cavity-2d`。它验证几何范围、单元数、物理组和一对一边界映射，保存原文件/副本
SHA-256。runner 使用固定参数调用 `gmshToFoam`，把转换后的 patch 类型受控设为
`wall/empty`，再执行 `checkMesh` 和求解器。真实示例为 4 单元工程验收，
`physical_validated=false`，不能作为方腔物理精度结果。

## J3：安全恢复与资源限制

每次真实执行都会保存 `stage-state.json`，并在网格准备和 `checkMesh` 完成后创建
只读约定的阶段 checkpoint。失败或超时后，用旧 attempt 创建一个新的恢复 attempt：

```powershell
python -m cfd_memo_agent.cli run --run "cases\runs\某次运行" --runner real `
  --resume-attempt "cases\runs\某次运行\attempts\失败的attempt" `
  --timeout 300 --minimum-free-mb 100 --max-log-mb 100
```

恢复前会核对 task、case、CaseSpec、MeshSpec、执行计划和 checkpoint SHA-256。
输入或 checkpoint 改动后拒绝恢复；旧 attempt 永不改写。已完成的 `blockMesh` 或
`gmshToFoam` 可以复用，但 `checkMesh` 必须在新 attempt 中重新执行。每个命令仍有
超时限制，并增加运行前磁盘空间和单日志大小限制。`result-index.json` 汇总输入、
阶段状态、日志、网格/结果证据和最终场文件的大小与 SHA-256。

## JSON 格式检查

```powershell
python -m json.tool schemas/task.schema.json
python -m json.tool schemas/episode.schema.json
```
