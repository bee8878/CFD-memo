"""Run one attempt; workflow retries and automatic edits belong to C5."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import inspect
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from uuid import uuid4

from cfd_memo_agent.case_adapters import get_case_adapter
from cfd_memo_agent.diagnoser import diagnose_log, diagnose_validation
from cfd_memo_agent.diagnoser.logs import finding
from cfd_memo_agent.gmsh import normalize_openfoam_boundary
from cfd_memo_agent.mesh_capabilities import (
    mesh_command_plan, mesh_spec_from_task, mesh_stage_arguments, resolve_mesh_spec,
)
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.validator.task import issue, new_report
from .backend import discover, execute_wsl, wsl_path
from .evidence import mesh_evidence, field_evidence, fingerprint
from .physics import force_coefficient_evidence
from .state import (
    build_result_index, checkpoint_case, ensure_disk_space, new_state, resume_checkpoint,
    run_input_fingerprint, save_state, update_stage,
)

SAMPLE_LOGS = Path(__file__).resolve().parents[3] / "cases/runs/sample-logs"
SCENARIOS = {"success": 0, "missing-boundary": 1, "bad-transport": 1, "unknown-failure": 1}
COMMANDS = ("blockMesh", "checkMesh", "icoFoam")


def _save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _execute(
    command: list[str], case_dir: Path, log_path: Path, timeout: float,
    max_log_bytes: int | None = None,
) -> dict:
    started = time.monotonic()
    timed_out = False
    resource_limited = False
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command, cwd=case_dir, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, shell=False, start_new_session=os.name != "nt",
        )
        try:
            deadline = started + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                if (max_log_bytes is not None and log_path.exists()
                        and log_path.stat().st_size > max_log_bytes):
                    resource_limited = True
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    returncode = process.wait(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if (max_log_bytes is not None and log_path.exists()
                    and log_path.stat().st_size > max_log_bytes):
                resource_limited = True
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            timed_out = isinstance(exc, subprocess.TimeoutExpired) and not resource_limited
            if os.name == "nt":
                # Terminate only this invocation's process tree on timeout.
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   check=False, timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            returncode = process.wait()
            if not timed_out and not resource_limited:
                raise
    return {"returncode": returncode, "timed_out": timed_out,
            "resource_limited": resource_limited,
            "duration_seconds": time.monotonic() - started}


def run_case(run_dir: Path | str, *, mode: str, scenario: str | None = None,
             timeout: float = 300.0, resume_attempt: Path | str | None = None,
             minimum_free_mb: int = 100, max_log_mb: int = 100) -> dict:
    """Execute a saved C2 run, saving validation, logs and diagnosis per attempt."""
    if mode not in {"simulated", "real"}:
        raise ValueError("mode 必须为 simulated 或 real")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout 必须为有限正数（秒）")
    if mode == "real" and scenario is not None:
        raise ValueError("真实模式不能选择模拟场景")
    if scenario is not None and scenario not in SCENARIOS:
        raise ValueError("未知模拟场景")
    for value, name in ((minimum_free_mb, "minimum_free_mb"), (max_log_mb, "max_log_mb")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} 必须是正整数")
    if resume_attempt is not None and mode != "real":
        raise ValueError("只有真实 runner 支持阶段恢复")
    # Preserve the caller's lexical path for the WSL OpenFOAM backend.
    root = Path(run_dir).absolute()
    if not root.is_dir():
        raise ValueError(f"运行目录不存在：{root}")
    if (root / "import.json").is_file() and (root / "case-spec.json").is_file():
        if mode != "real":
            raise ValueError("导入 case 只支持真实运行；不会用模拟日志冒充结果")
        from .imported import run_imported_case
        return run_imported_case(
            root, timeout=timeout, execute=_execute, resume_attempt=resume_attempt,
            minimum_free_mb=minimum_free_mb, max_log_mb=max_log_mb)
    started = time.monotonic()
    attempt = root / "attempts" / f"attempt-{uuid4().hex}"
    attempt.mkdir(parents=True)
    report = {
        "mode": mode, "simulated": mode == "simulated",
        "scenario": (scenario or "success") if mode == "simulated" else None,
        "status": "blocked", "started_at": datetime.now(timezone.utc).isoformat(),
        "run_path": str(root), "attempt_path": str(attempt), "case_path": None,
        "steps": [], "findings": [], "physical_validated": False,
        "environment": None, "mesh_evidence": None, "result_evidence": None,
        "mesh_spec": None, "mesh_conversion": None,
        "resume": None,
        "resource_limits": {"minimum_free_mb": minimum_free_mb, "max_log_mb": max_log_mb,
                            "timeout_seconds": timeout},
    }

    state = None

    def finish():
        report["duration_seconds"] = time.monotonic() - started
        report["result_index_path"] = str(attempt / "result-index.json")
        if state is not None:
            state["status"] = report["status"]
            save_state(attempt, state)
        _save(attempt / "diagnosis.json", {"status": report["status"], "findings": report["findings"],
                                          "validation_findings": report["validation_findings"],
                                          "steps": [step["diagnosis"] for step in report["steps"]]})
        _save(attempt / "execution.json", report)
        _save(attempt / "result-index.json", build_result_index(root, attempt, report))
        return report

    adapter = None
    mesh_spec = None
    try:
        task = read_json(root / "task.json")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        validation = new_report()
        validation["errors"].append(issue("TASK_READ", f"无法读取任务：{exc}", "task.json"))
    else:
        validation = validate_case(task, root / "case")
        if validation["task_valid"]:
            try:
                adapter = get_case_adapter(task)
                mesh_spec = resolve_mesh_spec(root, mesh_spec_from_task(task))
                report["mesh_spec"] = mesh_spec.to_dict()
            except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
                validation["errors"].append(issue(
                    "MESH_SPEC_INVALID", str(exc), "mesh-spec.json"))
                validation["config_valid"] = False
    _save(attempt / "validation.json", validation)
    report["validation"] = validation
    validation_diagnosis = diagnose_validation(validation)
    report["validation_findings"] = validation_diagnosis["findings"]
    if not validation["config_valid"]:
        report["findings"] = validation_diagnosis["findings"]
        return finish()

    executables = {}
    commands = mesh_command_plan(mesh_spec, task["solver"])
    input_sha256 = run_input_fingerprint(root)
    try:
        free_bytes = ensure_disk_space(attempt, minimum_free_mb * 1024 * 1024)
        report["resource_limits"]["free_bytes_before_run"] = free_bytes
    except OSError as exc:
        report["status"] = "resource_limit"
        report["findings"] = [finding(
            "DISK_SPACE_LIMIT", "运行前磁盘空间检查失败", str(attempt), str(exc),
            "manual_required", "释放磁盘空间或降低明确的最小可用空间要求。")]
        return finish()
    if mode == "real":
        try:
            environment = discover() if commands == COMMANDS else discover(commands)
            report["environment"] = environment
            executables = environment["executables"]
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["findings"].append(finding(
                "OPENFOAM_UNAVAILABLE", "未找到 OpenFOAM 命令", "environment",
                str(exc), "manual_required", "配置 Foundation OpenFOAM 10；不会自动改用模拟模式。"))
        blocking = [p for p in validation["runtime_blockers"] if p["code"] != "MESH_NOT_VERIFIED"]
        if blocking:
            report["findings"].extend(item for item in validation_diagnosis["findings"]
                                      if item["code"] != "MESH_NOT_VERIFIED")
        if report["findings"]:
            report["status"] = "blocked" if blocking else "environment_error"
            return finish()
        try:
            recovery = (
                resume_checkpoint(root, resume_attempt, commands, input_sha256)
                if resume_attempt is not None else None
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            report["status"] = "blocked"
            report["findings"] = [finding(
                "RESUME_INVALID", "无法安全恢复旧 attempt", str(resume_attempt), str(exc),
                "manual_required", "确认来源 attempt、输入文件和 checkpoint 均未改变。")]
            return finish()
        parent = recovery["source_attempt"] if recovery else None
        state = new_state(root, attempt, commands, input_sha256, parent_attempt=parent)
        save_state(attempt, state)
        report["resume"] = ({
            "parent_attempt": str(parent),
            "checkpoint_stage": recovery.get("checkpoint_stage"),
            "reused_stages": list(recovery["skipped"]),
        } if recovery else None)

    try:
        if mode == "simulated":
            selected = report["scenario"]
            log_path = attempt / "icoFoam.log"
            shutil.copyfile(SAMPLE_LOGS / f"{selected}.log", log_path)
            diagnosis = diagnose_log(log_path.read_text(encoding="utf-8"),
                                     returncode=SCENARIOS[selected], simulated=True)
            report["steps"].append({"stage": "icoFoam", "command": None,
                                    "log_path": str(log_path), "returncode": SCENARIOS[selected],
                                    "timed_out": False, "duration_seconds": 0.0,
                                    "diagnosis": diagnosis})
            report["findings"] = diagnosis["findings"]
            report["status"] = "simulated_success" if diagnosis["status"] == "completed" else "failed"
        else:
            case = attempt / "case"
            skipped = recovery["skipped"] if recovery else ()
            if recovery and recovery["checkpoint"] is not None:
                shutil.copytree(recovery["checkpoint"], case)
            else:
                case.mkdir()
                for folder in ("0", "constant", "system"):
                    ignored = (
                        shutil.ignore_patterns("polyMesh")
                        if folder == "constant" and mesh_spec.preparation_commands
                        else None
                    )
                    shutil.copytree(root / "case" / folder, case / folder,
                                    ignore=ignored)
                if (root / "case/mesh").is_dir():
                    shutil.copytree(root / "case/mesh", case / "mesh")
                if (root / "case/mesh-import.json").is_file():
                    shutil.copy2(root / "case/mesh-import.json", case / "mesh-import.json")
            report["case_path"] = str(case)
            if skipped:
                for stage in skipped:
                    report["steps"].append({
                        "stage": stage, "command": None, "log_path": None,
                        "returncode": 0, "timed_out": False, "resource_limited": False,
                        "duration_seconds": 0.0, "reused": True,
                        "diagnosis": {"status": "completed", "findings": [],
                                      "physical_validated": False},
                    })
                    update_stage(attempt, state, stage, "completed", reused=True)
                checkpoint = checkpoint_case(attempt, skipped[-1], case)
                update_stage(attempt, state, skipped[-1], "completed",
                             reused=True, checkpoint=checkpoint)
            initial_hash = fingerprint(case)
            max_log_bytes = max_log_mb * 1024 * 1024

            def bounded_execute(command, cwd, log, command_timeout):
                if "max_log_bytes" in inspect.signature(_execute).parameters:
                    return _execute(command, cwd, log, command_timeout,
                                    max_log_bytes=max_log_bytes)
                return _execute(command, cwd, log, command_timeout)

            for stage in commands[len(skipped):]:
                log_path = attempt / f"{stage}.log"
                transform = (
                    lambda path: wsl_path(environment, path)
                    if environment["backend"] == "wsl" else str(path)
                )
                arguments = mesh_stage_arguments(
                    mesh_spec, stage, case, path_transform=transform)
                command = [executables[stage], *arguments]
                update_stage(attempt, state, stage, "running", command=command,
                             log_path=str(log_path))
                try:
                    if environment["backend"] == "wsl":
                        command, outcome = execute_wsl(
                            environment, stage, case, log_path, timeout, bounded_execute,
                            arguments=arguments)
                    else:
                        outcome = bounded_execute(command, case, log_path, timeout)
                except KeyboardInterrupt:
                    problem = finding(
                        "EXECUTION_INTERRUPTED", "命令被用户中断", str(log_path),
                        "进程已终止，阶段未完成", "manual_required", "使用 --resume-attempt 安全恢复。")
                    report["status"] = "interrupted"
                    report["findings"] = [problem]
                    update_stage(attempt, state, stage, "interrupted", log_path=str(log_path))
                    break
                except (OSError, subprocess.TimeoutExpired) as exc:
                    problem = finding("EXECUTION_IO", "命令启动或日志写入失败", str(log_path),
                                      str(exc), "manual_required", "检查命令路径、文件权限与执行环境。")
                    report["steps"].append({
                        "stage": stage, "command": command, "log_path": str(log_path),
                        "returncode": None, "timed_out": False, "duration_seconds": None,
                        "diagnosis": {"status": "execution_error", "findings": [problem],
                                      "residuals": [], "physical_validated": False},
                    })
                    report["findings"] = [problem]
                    report["status"] = "execution_error"
                    update_stage(attempt, state, stage, "execution_error",
                                 log_path=str(log_path), evidence=str(exc))
                    break
                if outcome.get("resource_limited"):
                    problem = finding(
                        "LOG_SIZE_LIMIT", "命令日志超过资源上限", str(log_path),
                        f"限制 {max_log_mb} MiB", "manual_required",
                        "检查异常高频输出，或在确认磁盘容量后提高 --max-log-mb。")
                    report["steps"].append({
                        "stage": stage, "command": command, "log_path": str(log_path),
                        **outcome, "diagnosis": {"status": "resource_limit",
                                                 "findings": [problem],
                                                 "physical_validated": False},
                    })
                    report["status"] = "resource_limit"
                    report["findings"] = [problem]
                    update_stage(attempt, state, stage, "resource_limit",
                                 log_path=str(log_path), returncode=outcome["returncode"])
                    break
                diagnosis = diagnose_log(log_path.read_text(encoding="utf-8", errors="replace"),
                                         returncode=outcome["returncode"],
                                         timed_out=outcome["timed_out"], stage=stage)
                report["steps"].append({"stage": stage, "command": command,
                                        "log_path": str(log_path), **outcome, "diagnosis": diagnosis})
                if diagnosis["status"] != "completed":
                    report["status"] = diagnosis["status"]
                    report["findings"] = diagnosis["findings"]
                    update_stage(attempt, state, stage, report["status"],
                                 log_path=str(log_path), returncode=outcome["returncode"])
                    break
                try:
                    if fingerprint(case) != initial_hash:
                        raise ValueError("运行期间配置发生变化")
                    if stage == "gmshToFoam":
                        conversion = normalize_openfoam_boundary(case, {
                            "movingWall": "wall", "fixedWalls": "wall",
                            "frontAndBack": "empty",
                        })
                        conversion.update({
                            "source_sha256": task["mesh"]["source_sha256"],
                            "copied_sha256": task["mesh"]["copied_sha256"],
                            "physical_validated": False,
                        })
                        report["mesh_conversion"] = conversion
                        _save(attempt / "mesh-conversion.json", conversion)
                    if stage == "checkMesh":
                        report["mesh_evidence"] = adapter.collect_mesh_evidence(
                            case, task, mesh_evidence)
                        report["mesh_evidence"]["mesh_spec"] = mesh_spec.to_dict()
                        _save(attempt / "mesh-evidence.json", report["mesh_evidence"])
                    if stage == task["solver"]:
                        report["result_evidence"] = adapter.collect_field_evidence(
                            case, task, report["mesh_evidence"], field_evidence)
                        force = adapter.collect_physical_evidence(
                            case, task, force_coefficient_evidence)
                        if force is not None:
                            report["result_evidence"]["force_coefficients"] = force
                            _save(attempt / "force-evidence.json", force)
                        _save(attempt / "result-evidence.json", report["result_evidence"])
                    checkpoint = (
                        checkpoint_case(attempt, stage, case)
                        if stage != task["solver"] else None
                    )
                    update_stage(attempt, state, stage, "completed",
                                 log_path=str(log_path), returncode=outcome["returncode"],
                                 checkpoint=checkpoint)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    report["status"] = "failed"
                    report["findings"] = [finding(
                        "OUTPUT_INVALID", "实际网格或流场输出不符合要求", str(case), str(exc),
                        "manual_required", "检查本次网格和结果文件；命令结束不代表计算验收通过。")]
                    update_stage(attempt, state, stage, "failed",
                                 log_path=str(log_path), evidence=str(exc))
                    break
            else:
                report["status"] = "completed"
    except (OSError, UnicodeError) as exc:
        report["status"] = "execution_error"
        report["findings"].append(finding("EXECUTION_IO", "执行或日志读取失败", str(attempt),
                                          str(exc), "manual_required", "检查文件权限、日志样例与命令执行环境。"))
    return finish()
