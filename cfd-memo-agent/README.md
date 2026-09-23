# CFD-Memo Agent

CFD-Memo 已实现 C1–C5，并完成 C6 的真实工程执行闭环：规则规划、配置生成、
验证、运行、有限修正、记录，以及通过 WSL 调用 Foundation OpenFOAM 10。

This project currently defines the local engineering skeleton for a memory-enhanced CFD agent. It does not copy or depend on sensitive research application files under `../科研立项/`.

## Stage B Scope

- Python package structure under `src/cfd_memo_agent/`
- CFD task and episode memory JSON schemas under `schemas/`
- A readable OpenFOAM-style 2D cylinder baseline case under `cases/templates/cylinder-2d/`
- Simulated runner logs under `cases/runs/sample-logs/`
- Experiment and schema notes under `docs/`

Stage B did not require OpenFOAM. The current machine now has the fixed C6 backend;
sample logs remain available only for explicit simulated tests.

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
圆柱边界为 288 个面。阻力、升力、涡脱落频率及网格/时间步独立性尚未验证，
因此 `physical_validated=false`，不能宣称物理准确性已经成立。

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
当前使用规则 planner，保持其有限输入能力；未接入 LLM。
episode 标记 no_memory、experience_reused=false，候选经验未进入跨任务经验库。

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
