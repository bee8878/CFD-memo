# C6：真实运行指南

## 当前状态

2026-09-23：WSL2、Ubuntu 22.04.5 和 Foundation OpenFOAM 10 已安装并可用。
Ubuntu 虚拟磁盘位于 `D:\CFD-Environment\Ubuntu-22.04`，安装镜像位于
`D:\CFD-Environment\installers`；Windows 的 Python 虚拟环境仍留在项目内。
`blockMesh`、`checkMesh`、`icoFoam` 已手工通过，Python runner 也在独立 attempt
中完成三阶段运行。默认网格 10368 个单元，圆柱边界 288 个面，最终生成 `10/U` 和 `10/p`。

工程执行、阻力/升力/Strouhal 数参考对照以及网格/时间步独立性已经通过。
汇总研究为 `physical_validated=true`，该结论仅适用于固定的 Re=100 二维圆柱 benchmark。项目已经正式重命名为无空格目录 `D:\CFD_MEMO`，
不再依赖 Junction 入口。

## 安装环境

保留 Windows 的 Python 虚拟环境。用户要求安装包和 Linux 系统放 D 盘，
当前使用仓库外的 `D:\CFD-Environment`：
安装包放 `installers/`，Ubuntu 虚拟磁盘放 `Ubuntu-22.04/`，OpenFOAM 安装在该 Linux 系统内。
Windows 自身的 WSL 组件及系统修复缓存仍可能必须使用 C 盘，相关操作应提前说明。
重装时重新获取官方镜像并校验，再执行指定安装位置的命令：

```powershell
wsl --install --from-file "D:\CFD-Environment\installers\ubuntu-22.04.5-wsl-amd64.wsl" --location "D:\CFD-Environment\Ubuntu-22.04" --name Ubuntu-22.04
```

该命令来自 [微软的发行版安装说明](https://learn.microsoft.com/en-us/windows/wsl/build-custom-distro)。
安装前确认文件存在并按微软官方发行版清单校验。
在线安装的备用命令（此前遇到 403）：以管理员身份打开 PowerShell：

```powershell
wsl --install -d Ubuntu-22.04 --location "D:\CFD-Environment\Ubuntu-22.04"
```

按系统提示完成安装，必要时重启。首次打开 Ubuntu 时自行设置 Linux 用户名和密码，
不要把密码发到聊天或写进仓库。执行 `wsl --list --verbose` 确认发行版为 Ubuntu-22.04、版本为 2。
如果安装报错，保留完整错误信息，先解决系统环境，不反复启动求解。
依据：[Microsoft WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)。

在 Ubuntu 终端按 [Foundation OpenFOAM 10 官方安装说明](https://openfoam.org/download/10-ubuntu/)
配置软件源和签名密钥，安装 `openfoam10`，不是 Ubuntu 同名通用包或其他发行版。
然后检查：

```bash
source /opt/openfoam10/etc/bashrc
printf '%s\n' "$WM_PROJECT_VERSION"
command -v blockMesh checkMesh icoFoam
```

预期版本为 10，三个命令均有路径。Python 后端每次调用自行加载该环境。

## 先手工运行

在 Windows 项目目录用 `generate` 创建新的独立 run，不使用模板目录计算：

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli generate --task examples/task.cylinder-2d.json
```

复制输出的 `case_path`。在 Ubuntu 中使用无空格路径 `/mnt/d/CFD_MEMO/.../case`，
进入该 case 后运行以下命令。每个命令都必须成功后才执行下一项：

```bash
source /opt/openfoam10/etc/bashrc
blockMesh > blockMesh.log 2>&1
checkMesh > checkMesh.log 2>&1
icoFoam > icoFoam.log 2>&1
```

逐项检查退出码 `echo $?` 和日志，`checkMesh` 必须报告 `Mesh OK.`。
默认结束时刻为 10，检查 `10/U`、`10/p` 是否产生。
若网格质量失败，必须改进网格并复验，不能绕过检查。

## 再验证自动运行

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli run --task examples/task.cylinder-2d.json --runner real --timeout 300
```

这是新工作流，不复用手工运行结果。Windows 路径作为独立参数传入 WSL。
Foundation OpenFOAM 10 会拒绝带空格的 case 路径，因此项目使用实际目录 `D:\CFD_MEMO`。
每个命令独立计时；WSL 使用 Linux watchdog 和本次进程组清理，可能有少量清理等待时间。
不关闭整个 WSL，也不自动提高超时或修改物理参数。

物理验收使用长时任务并运行固定的八组对照：

```powershell
.\.venv\Scripts\python.exe -m cfd_memo_agent.cli physics-study --task examples/task.cylinder-2d-physical.json --timeout 1800
```

该命令提取 `forceCoeffs`，计算平均阻力系数、升力振幅和 Strouhal 数，并比较网格与时间步变化。已有研究可以用 `--resume <study目录>` 只补跑缺失变体。原单环网格的 `Cl` 未收敛后，双环 wake-focused 网格把近场、尾迹和远场分开控制；29440 到 47200 单元的 `Cd/Cl/St` 变化均通过门槛。

## 输出与限制

- `rounds/round-000/attempts/attempt-*/case/constant/polyMesh/`：本次真实网格。
- 同一个 `case/10/`：默认结束时刻的速度 U 和运动学压力 p。
- attempt 内的三个 `.log`、`execution.json`：命令、环境、退出码及诊断。
- `mesh-evidence.json`：实际单元数、非空边界、配置与网格指纹。
- `result-evidence.json`：结束时刻、有限数值、内部场长度检查、场文件路径及 `Cd/Cl/St` 摘要。
- `force-evidence.json`：本轮 `forceCoeffs` 原始文件位置、周期统计和参考区间对照。
- 工作流根目录的 `episode.json`、`report.md`：汇总证据。

网格由本项目 `mesh.py` 编写，不是复制某个已发表 benchmark。
语法依据：[OpenFOAM v10 blockMesh 文档](https://doc.cfd.direct/openfoam/user-guide-v10/blockmesh)。
采用八个扇区连接圆柱与矩形远场，径向 simpleGrading=10；默认目标 10000，计划 10368 单元。
压力最终修正显式配置 pFinal，参考 [v10 icoFoam 官方教程配置](https://github.com/OpenFOAM/OpenFOAM-10/blob/master/tutorials/incompressible/icoFoam/cavity/cavity/system/fvSolution)，
不使用解析器尚不支持的变量展开。
默认网格已通过真实 `checkMesh`；几何比例极端的任务仍可能失败，不保证任意尺寸都可运行。

单次 `completed` 只表示真实执行及基本文件检查通过；只有完整研究矩阵才能设置汇总的 `physical_validated=true`。当前结论不适用于其他 Re、几何、求解器或湍流模型。项目尚无长期记忆检索，也没有接入大模型。
