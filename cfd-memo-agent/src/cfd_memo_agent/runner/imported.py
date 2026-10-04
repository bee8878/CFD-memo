"""Real execution for imported cases using capability-owned command plans."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from cfd_memo_agent.capabilities import get_capability
from cfd_memo_agent.case_import import load_case_spec, validate_imported_case
from cfd_memo_agent.diagnoser import diagnose_log, diagnose_validation
from cfd_memo_agent.diagnoser.logs import finding
from cfd_memo_agent.mesh_capabilities import (
    mesh_command_plan, mesh_spec_from_case_spec, resolve_mesh_spec,
)
from cfd_memo_agent.validator.task import issue
from cfd_memo_agent.validator.foam import Group, parse_foam
from .backend import discover, execute_wsl
from .evidence import fingerprint
from .state import (
    build_result_index, checkpoint_case, ensure_disk_space, new_state, resume_checkpoint,
    run_input_fingerprint, save_state, update_stage,
)


def _save(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _mesh_hash(case: Path) -> str:
    mesh = case / "constant/polyMesh"
    digest = hashlib.sha256()
    for name in ("boundary", "faces", "neighbour", "owner", "points"):
        path = mesh / name
        if not path.is_file():
            raise ValueError(f"网格检查后缺少 constant/polyMesh/{name}")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _finite_tree(value) -> bool:
    if isinstance(value, Group):
        return _finite_tree(value.items)
    if isinstance(value, dict):
        return all(_finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _result_evidence(case: Path, spec, config_hash: str, mesh_hash: str) -> dict:
    end_time = spec.time_control["end_time"]
    candidates = []
    for path in case.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if math.isfinite(value) and math.isclose(value, end_time, rel_tol=1e-8, abs_tol=1e-10):
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("缺少或存在多个结束时刻结果目录")
    fields = {}
    expected_boundaries = set(spec.boundaries)
    for name in spec.fields:
        path = candidates[0] / name
        data = parse_foam(path.read_text(encoding="utf-8-sig"))
        if not _finite_tree(data) or set(data.get("boundaryField", {})) != expected_boundaries:
            raise ValueError(f"结束时刻字段无效：{name}")
        fields[name] = str(path)
    if fingerprint(case) != config_hash:
        raise ValueError("求解期间配置文件发生变化")
    if _mesh_hash(case) != mesh_hash:
        raise ValueError("checkMesh 后网格发生变化")
    return {
        "end_time": end_time,
        "fields": fields,
        "mesh_sha256": mesh_hash,
        "physical_validated": False,
    }


def run_imported_case(
    run_dir: Path, *, timeout: float, execute, resume_attempt=None,
    minimum_free_mb: int = 100, max_log_mb: int = 100,
) -> dict:
    """Validate and run an imported case; never execute Allrun or imported scripts."""
    root = Path(run_dir).absolute()
    spec = load_case_spec(root / "case-spec.json")
    capability = get_capability(spec.solver)
    validation = validate_imported_case(spec, root / "case")
    mesh_spec = None
    try:
        mesh_spec = resolve_mesh_spec(root, mesh_spec_from_case_spec(spec))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        validation["errors"].append(issue(
            "MESH_SPEC_INVALID", str(exc), "mesh-spec.json"))
        validation["config_valid"] = False
    started = time.monotonic()
    attempt = root / "attempts" / f"attempt-{uuid4().hex}"
    attempt.mkdir(parents=True)
    report = {
        "mode": "real", "simulated": False, "scenario": None,
        "status": "blocked", "started_at": datetime.now(timezone.utc).isoformat(),
        "run_path": str(root), "attempt_path": str(attempt), "case_path": None,
        "steps": [], "findings": [], "physical_validated": False,
        "environment": None, "mesh_evidence": None, "result_evidence": None,
        "case_spec": spec.to_dict(), "imported_case": True,
        "mesh_spec": mesh_spec.to_dict() if mesh_spec is not None else None,
        "resume": None,
        "resource_limits": {"minimum_free_mb": minimum_free_mb, "max_log_mb": max_log_mb,
                            "timeout_seconds": timeout},
    }
    _save(attempt / "validation.json", validation)
    validation_diagnosis = diagnose_validation(validation)
    report["validation"] = validation
    report["validation_findings"] = validation_diagnosis["findings"]

    state = None

    def finish():
        report["duration_seconds"] = time.monotonic() - started
        report["result_index_path"] = str(attempt / "result-index.json")
        if state is not None:
            state["status"] = report["status"]
            save_state(attempt, state)
        _save(attempt / "diagnosis.json", {
            "status": report["status"], "findings": report["findings"],
            "validation_findings": report["validation_findings"],
            "steps": [step["diagnosis"] for step in report["steps"]],
        })
        _save(attempt / "execution.json", report)
        _save(attempt / "result-index.json", build_result_index(root, attempt, report))
        return report

    if not validation["config_valid"] or validation["runtime_blockers"]:
        report["findings"] = validation_diagnosis["findings"]
        return finish()

    stages = mesh_command_plan(mesh_spec, capability.executable)
    input_sha256 = run_input_fingerprint(root)
    try:
        report["resource_limits"]["free_bytes_before_run"] = ensure_disk_space(
            attempt, minimum_free_mb * 1024 * 1024)
    except OSError as exc:
        report["status"] = "resource_limit"
        report["findings"] = [finding(
            "DISK_SPACE_LIMIT", "运行前磁盘空间检查失败", str(attempt), str(exc),
            "manual_required", "释放磁盘空间或降低明确的最小可用空间要求。")]
        return finish()
    try:
        environment = discover(stages)
        report["environment"] = environment
    except (OSError, ValueError) as exc:
        report["status"] = "environment_error"
        report["findings"] = [finding(
            "OPENFOAM_UNAVAILABLE", "未找到导入 case 所需的 OpenFOAM 命令", "environment",
            str(exc), "manual_required", "检查 OpenFOAM 10 环境和能力注册表。")]
        return finish()

    try:
        recovery = (
            resume_checkpoint(root, resume_attempt, stages, input_sha256)
            if resume_attempt is not None else None
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        report["status"] = "blocked"
        report["findings"] = [finding(
            "RESUME_INVALID", "无法安全恢复旧 attempt", str(resume_attempt), str(exc),
            "manual_required", "确认来源 attempt、输入文件和 checkpoint 均未改变。")]
        return finish()
    parent = recovery["source_attempt"] if recovery else None
    state = new_state(root, attempt, stages, input_sha256, parent_attempt=parent)
    save_state(attempt, state)
    report["resume"] = ({
        "parent_attempt": str(parent),
        "checkpoint_stage": recovery.get("checkpoint_stage"),
        "reused_stages": list(recovery["skipped"]),
    } if recovery else None)

    case = attempt / "case"
    skipped = recovery["skipped"] if recovery else ()
    shutil.copytree(
        recovery["checkpoint"] if recovery and recovery["checkpoint"] is not None
        else root / "case",
        case,
    )
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
    config_hash = fingerprint(case)
    mesh_hash = None
    max_log_bytes = max_log_mb * 1024 * 1024

    def bounded_execute(command, cwd, log, command_timeout):
        if "max_log_bytes" in inspect.signature(execute).parameters:
            return execute(command, cwd, log, command_timeout,
                           max_log_bytes=max_log_bytes)
        return execute(command, cwd, log, command_timeout)

    for stage in stages[len(skipped):]:
        log_path = attempt / f"{stage}.log"
        command = [environment["executables"][stage], "-case", str(case)]
        update_stage(attempt, state, stage, "running", command=command,
                     log_path=str(log_path))
        try:
            if environment["backend"] == "wsl":
                command, outcome = execute_wsl(
                    environment, stage, case, log_path, timeout, bounded_execute)
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
            problem = finding(
                "EXECUTION_IO", "命令启动或日志写入失败", str(log_path), str(exc),
                "manual_required", "检查命令路径、文件权限与执行环境。")
            report["steps"].append({
                "stage": stage, "command": command, "log_path": str(log_path),
                "returncode": None, "timed_out": False, "duration_seconds": None,
                "diagnosis": {"status": "execution_error", "findings": [problem]},
            })
            report["status"] = "execution_error"
            report["findings"] = [problem]
            update_stage(attempt, state, stage, "execution_error",
                         log_path=str(log_path), evidence=str(exc))
            break
        if outcome.get("resource_limited"):
            problem = finding(
                "LOG_SIZE_LIMIT", "命令日志超过资源上限", str(log_path),
                f"限制 {max_log_mb} MiB", "manual_required", "检查异常输出或提高限制。")
            report["steps"].append({
                "stage": stage, "command": command, "log_path": str(log_path),
                **outcome, "diagnosis": {"status": "resource_limit",
                                         "findings": [problem]},
            })
            report["status"] = "resource_limit"
            report["findings"] = [problem]
            update_stage(attempt, state, stage, "resource_limit",
                         log_path=str(log_path), returncode=outcome["returncode"])
            break
        diagnosis = diagnose_log(
            log_path.read_text(encoding="utf-8", errors="replace"),
            returncode=outcome["returncode"], timed_out=outcome["timed_out"], stage=stage)
        report["steps"].append({
            "stage": stage, "command": command, "log_path": str(log_path),
            **outcome, "diagnosis": diagnosis,
        })
        if diagnosis["status"] != "completed":
            report["status"] = diagnosis["status"]
            report["findings"] = diagnosis["findings"]
            update_stage(attempt, state, stage, report["status"],
                         log_path=str(log_path), returncode=outcome["returncode"])
            break
        try:
            if fingerprint(case) != config_hash:
                raise ValueError("运行期间配置发生变化")
            if stage == "checkMesh":
                mesh_hash = _mesh_hash(case)
                report["mesh_evidence"] = {
                    "mesh_verified": True,
                    "mesh_sha256": mesh_hash,
                    "case_path": str(case),
                    "mesh_spec": mesh_spec.to_dict(),
                    "physical_validated": False,
                }
                _save(attempt / "mesh-evidence.json", report["mesh_evidence"])
            if stage == capability.executable:
                report["result_evidence"] = _result_evidence(
                    case, spec, config_hash, mesh_hash)
                _save(attempt / "result-evidence.json", report["result_evidence"])
            checkpoint = (
                checkpoint_case(attempt, stage, case)
                if stage != capability.executable else None
            )
            update_stage(attempt, state, stage, "completed",
                         log_path=str(log_path), returncode=outcome["returncode"],
                         checkpoint=checkpoint)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            report["status"] = "failed"
            report["findings"] = [finding(
                "OUTPUT_INVALID", "导入 case 的网格或结果证据不完整", str(case), str(exc),
                "manual_required", "检查网格、结束时刻和字段文件。")]
            update_stage(attempt, state, stage, "failed",
                         log_path=str(log_path), evidence=str(exc))
            break
    else:
        report["status"] = "completed"
    return finish()


__all__ = ["run_imported_case"]
