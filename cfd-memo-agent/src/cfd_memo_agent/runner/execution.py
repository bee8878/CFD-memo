"""Run one attempt; workflow retries and automatic edits belong to C5."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from uuid import uuid4

from cfd_memo_agent.diagnoser import diagnose_log, diagnose_validation
from cfd_memo_agent.diagnoser.logs import finding
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.validator.task import issue, new_report
from .backend import discover, execute_wsl
from .evidence import mesh_evidence, field_evidence, fingerprint

SAMPLE_LOGS = Path(__file__).resolve().parents[3] / "cases/runs/sample-logs"
SCENARIOS = {"success": 0, "missing-boundary": 1, "bad-transport": 1, "unknown-failure": 1}
COMMANDS = ("blockMesh", "checkMesh", "icoFoam")


def _save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _execute(command: list[str], case_dir: Path, log_path: Path, timeout: float) -> dict:
    started = time.monotonic()
    timed_out = False
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command, cwd=case_dir, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, shell=False, start_new_session=os.name != "nt",
        )
        try:
            returncode = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            timed_out = isinstance(exc, subprocess.TimeoutExpired)
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
            if not timed_out:
                raise
    return {"returncode": returncode, "timed_out": timed_out,
            "duration_seconds": time.monotonic() - started}


def run_case(run_dir: Path | str, *, mode: str, scenario: str | None = None,
             timeout: float = 300.0) -> dict:
    """Execute a saved C2 run, saving validation, logs and diagnosis per attempt."""
    if mode not in {"simulated", "real"}:
        raise ValueError("mode 必须为 simulated 或 real")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout 必须为有限正数（秒）")
    if mode == "real" and scenario is not None:
        raise ValueError("真实模式不能选择模拟场景")
    if scenario is not None and scenario not in SCENARIOS:
        raise ValueError("未知模拟场景")
    # Preserve a caller-provided no-space junction used by the WSL OpenFOAM backend.
    root = Path(run_dir).absolute()
    if not root.is_dir():
        raise ValueError(f"运行目录不存在：{root}")
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
    }

    def finish():
        report["duration_seconds"] = time.monotonic() - started
        _save(attempt / "diagnosis.json", {"status": report["status"], "findings": report["findings"],
                                          "validation_findings": report["validation_findings"],
                                          "steps": [step["diagnosis"] for step in report["steps"]]})
        _save(attempt / "execution.json", report)
        return report

    try:
        task = read_json(root / "task.json")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        validation = new_report()
        validation["errors"].append(issue("TASK_READ", f"无法读取任务：{exc}", "task.json"))
    else:
        validation = validate_case(task, root / "case")
    _save(attempt / "validation.json", validation)
    report["validation"] = validation
    validation_diagnosis = diagnose_validation(validation)
    report["validation_findings"] = validation_diagnosis["findings"]
    if not validation["config_valid"]:
        report["findings"] = validation_diagnosis["findings"]
        return finish()

    executables = {}
    if mode == "real":
        try:
            environment = discover()
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
            case.mkdir()
            for folder in ("0", "constant", "system"):
                shutil.copytree(root / "case" / folder, case / folder,
                                ignore=shutil.ignore_patterns("polyMesh"))
            report["case_path"] = str(case)
            initial_hash = fingerprint(case)
            for stage in COMMANDS:
                log_path = attempt / f"{stage}.log"
                command = [executables[stage], "-case", str(case)]
                try:
                    if environment["backend"] == "wsl":
                        command, outcome = execute_wsl(environment, stage, case, log_path, timeout, _execute)
                    else:
                        outcome = _execute(command, case, log_path, timeout)
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
                    break
                diagnosis = diagnose_log(log_path.read_text(encoding="utf-8", errors="replace"),
                                         returncode=outcome["returncode"],
                                         timed_out=outcome["timed_out"], stage=stage)
                report["steps"].append({"stage": stage, "command": command,
                                        "log_path": str(log_path), **outcome, "diagnosis": diagnosis})
                if diagnosis["status"] != "completed":
                    report["status"] = diagnosis["status"]
                    report["findings"] = diagnosis["findings"]
                    break
                try:
                    if fingerprint(case) != initial_hash:
                        raise ValueError("运行期间配置发生变化")
                    if stage == "checkMesh":
                        report["mesh_evidence"] = mesh_evidence(case, task)
                        _save(attempt / "mesh-evidence.json", report["mesh_evidence"])
                    if stage == "icoFoam":
                        report["result_evidence"] = field_evidence(case, task, report["mesh_evidence"])
                        _save(attempt / "result-evidence.json", report["result_evidence"])
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    report["status"] = "failed"
                    report["findings"] = [finding(
                        "OUTPUT_INVALID", "实际网格或流场输出不符合要求", str(case), str(exc),
                        "manual_required", "检查本次网格和结果文件；命令结束不代表计算验收通过。")]
                    break
            else:
                report["status"] = "completed"
    except (OSError, UnicodeError) as exc:
        report["status"] = "execution_error"
        report["findings"].append(finding("EXECUTION_IO", "执行或日志读取失败", str(attempt),
                                          str(exc), "manual_required", "检查文件权限、日志样例与命令执行环境。"))
    return finish()
