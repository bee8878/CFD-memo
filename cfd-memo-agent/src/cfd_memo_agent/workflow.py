"""C5 orchestration of one task with bounded, evidence-based repairs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import shutil
import time
from uuid import uuid4

from cfd_memo_agent.correction import FAULTS, apply_repairs, inject_fault, propose_repairs
from cfd_memo_agent.diagnoser import diagnose_validation
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.memory import save_episode
from cfd_memo_agent.memory.episodes import write_record
from cfd_memo_agent.planner import plan_task
from cfd_memo_agent.reporter import write_report
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.foam import parse_json
from cfd_memo_agent.validator.task import finite, issue, new_report

PROJECT = Path(__file__).resolve().parents[2]


def _options(description, task_path, mode, max_corrections, timeout, fault):
    if (description is None) == (task_path is None):
        raise ValueError("需求与 task_path 必须二选一")
    if description is not None and not isinstance(description, str):
        raise ValueError("需求必须为字符串")
    if mode not in {"simulated", "real"}:
        raise ValueError("mode 必须为 simulated 或 real")
    if max_corrections is not None and (
        isinstance(max_corrections, bool) or not isinstance(max_corrections, int) or max_corrections < 0
    ):
        raise ValueError("max_corrections 必须为非负整数")
    if not finite(timeout) or timeout <= 0:
        raise ValueError("timeout 必须为有限正数")
    if fault is not None and (fault not in FAULTS or mode != "simulated"):
        raise ValueError("受控故障仅支持模拟模式下的 missing-boundary 或 bad-transport")


def run_workflow(description=None, *, task_path=None, mode, runs_dir=None,
                 max_corrections=None, timeout=300, fault=None) -> dict:
    """Run C1-C4, optionally repair supported fields, and persist an episode."""
    _options(description, task_path, mode, max_corrections, timeout, fault)
    parent = Path(runs_dir).resolve() if runs_dir is not None else PROJECT / "cases/runs"
    template = (PROJECT / "cases/templates").resolve()
    if parent == template or template in parent.parents:
        raise ValueError("工作流输出不能位于模板目录内")
    root = parent / ("workflow-" + uuid4().hex)
    root.mkdir(parents=True)
    start = time.monotonic()
    source = {"kind": "description" if task_path is None else "task_file",
              "description": description, "task_path": str(Path(task_path).resolve()) if task_path is not None else None,
              "raw_text": None}
    episode = {
        "schema_version": 2, "episode_id": root.name, "task_id": None, "task": None,
        "input": source, "mode": "no_memory", "runner_mode": mode,
        "status": "failed", "physical_validated": False,
        "started_at": datetime.now(timezone.utc).isoformat(), "workflow_path": str(root),
        "episode_path": str(root / "episode.json"), "report_path": str(root / "report.md"),
        "max_corrections": max_corrections if max_corrections is not None else 2,
        "rounds": [], "corrections": [], "injected_fault": None, "findings": [],
        "stop_reason": {"code": "NOT_STARTED", "message": "任务尚未完成"},
        "case_summary": None, "log_summary": None,
        "diagnosis": {"root_cause": "", "suggested_fix": "", "correction_count": 0},
        "reflection": {"success_factors": [], "failure_causes": [], "reusable_rules": []},
        "reuse_tags": [], "metrics": {"elapsed_seconds": 0, "config_valid": False, "experience_reused": False},
    }

    def finish(status, code, message):
        episode["status"] = status
        episode["stop_reason"] = {"code": code, "message": message}
        episode["metrics"]["elapsed_seconds"] = time.monotonic() - start
        episode["diagnosis"]["correction_count"] = len(episode["corrections"])
        all_findings = episode["findings"] + [
            item for record in episode["rounds"] for item in record["findings"]]
        episode["reflection"]["failure_causes"] = list(dict.fromkeys(
            item["message"] for item in all_findings))
        episode["diagnosis"]["root_cause"] = "；".join(episode["reflection"]["failure_causes"])
        episode["diagnosis"]["suggested_fix"] = message
        if episode["rounds"]:
            last = episode["rounds"][-1]
            episode["metrics"]["config_valid"] = last["config_valid"]
            if last["case_path"] is not None:
                episode["case_summary"] = {
                    "case_type": episode["task"]["case_type"], "solver": episode["task"]["solver"],
                    "case_path": last["case_path"],
                }
            if last["log_paths"]:
                episode["log_summary"] = {
                    "log_path": last["log_paths"][-1],
                    "error_type": last["findings"][0]["code"] if last["findings"] else None,
                    "residual_summary": f"记录 {len(last['residuals'])} 条残差；未判定收敛。",
                }
        if status in {"simulated_success", "completed"}:
            episode["reflection"]["success_factors"] = ["配置检查通过，所选执行模式的日志检查完成。"]
            if episode["corrections"]:
                episode["reflection"]["reusable_rules"] = [
                    "候选：有效任务支持的边界或黏度字段，可从可信配置恢复并重新验证。"]
        save_episode(root / "episode.json", episode)
        write_report(root / "report.md", episode)
        return episode

    try:
        if task_path is None:
            task = plan_task(description)
        else:
            source["raw_text"] = Path(task_path).read_text(encoding="utf-8-sig")
            task = parse_json(source["raw_text"])
    except (OSError, UnicodeError, ValueError, ArithmeticError, RecursionError) as exc:
        write_record(root / "input.json", source)
        problem = issue("INPUT_FAILED", f"任务读取或规划失败：{exc}", "task")
        validation = new_report()
        validation["errors"] = [problem]
        write_record(root / "task-validation.json", validation)
        episode["findings"] = diagnose_validation(validation)["findings"]
        return finish("failed", "INPUT_FAILED", "请核对任务输入；本次未生成 case。")
    write_record(root / "input.json", source)
    validation = validate_task(task)
    write_record(root / "task-validation.json", validation)
    if not validation["task_valid"]:
        episode["findings"] = diagnose_validation(validation)["findings"]
        return finish("failed", "TASK_INVALID", "任务未通过检查，请确认参数和物理意图。")
    episode["task"], episode["task_id"] = deepcopy(task), task["task_id"]
    episode["reuse_tags"] = [task["case_type"], task["solver"], "laminar"]
    if max_corrections is None:
        episode["max_corrections"] = int(task.get("convergence", {}).get("max_corrections", 2))
    write_record(root / "task.json", task)
    try:
        reference = Path(generate_case(task, runs_dir=root / "reference")["run_path"])
    except (OSError, ValueError) as exc:
        episode["findings"] = [issue("GENERATION_FAILED", str(exc), "generator")]
        return finish("failed", "GENERATION_FAILED", "case 生成失败，请检查模板和文件权限。")

    previous = reference
    pending = None
    while True:
        index = len(episode["rounds"])
        round_dir = root / "rounds" / f"round-{index:03d}"
        round_dir.mkdir(parents=True)
        record = {
            "index": index, "run_path": str(round_dir), "case_path": None,
            "status": "preparing", "execution_path": None, "validation_path": None,
            "config_valid": False, "log_paths": [], "residuals": [],
            "findings": [], "runtime_blockers": [],
        }
        episode["rounds"].append(record)
        write_record(round_dir / "started.json", record)
        try:
            shutil.copytree(previous / "case", round_dir / "case")
            shutil.copyfile(previous / "task.json", round_dir / "task.json")
            record["case_path"] = str(round_dir / "case")
            if pending is not None:
                apply_repairs(round_dir / "case", pending["changes"])
                write_record(round_dir / "correction.json", pending)
                episode["corrections"].append(pending)
            if index == 0 and fault is not None:
                episode["injected_fault"] = inject_fault(round_dir / "case", fault)
                write_record(round_dir / "injected-fault.json", episode["injected_fault"])
        except (OSError, ValueError) as exc:
            record["status"] = "execution_error"
            record["findings"] = [issue("ROUND_PREPARATION_FAILED", str(exc), str(round_dir))]
            write_record(round_dir / "round.json", record)
            return finish("failed", "ROUND_PREPARATION_FAILED", "本轮文件准备失败，已保留可用记录。")
        try:
            outcome = run_case(round_dir, mode=mode, timeout=timeout)
        except KeyboardInterrupt:
            record["status"] = "interrupted"
            write_record(round_dir / "round.json", record)
            return finish("interrupted", "INTERRUPTED", "用户中断执行，已保存完成的轮次。")
        record["execution_path"] = str(Path(outcome["attempt_path"]) / "execution.json")
        record["validation_path"] = str(Path(outcome["attempt_path"]) / "validation.json")
        record["config_valid"] = outcome["validation"]["config_valid"]
        record["status"] = outcome["status"] if record["config_valid"] else "validation_failed"
        record["runtime_blockers"] = outcome["validation"]["runtime_blockers"]
        record["environment"] = outcome.get("environment")
        record["mesh_evidence"] = outcome.get("mesh_evidence")
        record["result_evidence"] = outcome.get("result_evidence")
        if outcome.get("case_path"):
            record["case_path"] = outcome["case_path"]
        if record["mesh_evidence"]:
            record["runtime_blockers"] = [item for item in record["runtime_blockers"]
                                           if item["code"] != "MESH_NOT_VERIFIED"]
        record["log_paths"] = [step["log_path"] for step in outcome["steps"]]
        record["residuals"] = [r for step in outcome["steps"] for r in step["diagnosis"].get("residuals", [])]
        # Static blockers remain visible separately, but are not errors in explicit simulation.
        blocker_codes = {item["code"] for item in record["runtime_blockers"]}
        record["findings"] = [item for item in outcome["findings"] if item["code"] not in blocker_codes]
        write_record(round_dir / "round.json", record)
        if outcome["status"] in {"simulated_success", "completed"}:
            return finish(outcome["status"], "COMPLETED", "本次执行模式的流程已完成；物理结果仍未验证。")
        if mode == "real" and any(p["code"] != "MESH_NOT_VERIFIED" for p in record["runtime_blockers"]):
            return finish("blocked", "RUNTIME_BLOCKERS", "真实网格或几何验证尚未完成，需在 C6 处理。")
        if outcome["status"] in {"timeout", "environment_error", "execution_error"}:
            status = "blocked" if outcome["status"] == "environment_error" else outcome["status"]
            return finish(status, outcome["status"].upper(), "执行超时或环境/文件错误，需人工处理后再运行。")
        if not record["findings"]:
            return finish("blocked", "NO_DIAGNOSTIC", "没有足够诊断证据，停止自动修正。")
        proposal = propose_repairs(task, round_dir / "case", reference / "case", record["findings"])
        if not proposal["changes"]:
            return finish("failed", proposal["stop_code"], "没有可实施的安全修正或没有实际变更，需人工核对。")
        if len(episode["corrections"]) >= episode["max_corrections"]:
            return finish("failed", "CORRECTION_BUDGET_EXHAUSTED", "修正次数已达到上限。")
        pending = {"from_round": index, "to_round": index + 1,
                   "reason_codes": sorted({p["code"] for p in record["findings"]}),
                   "changes": proposal["changes"]}
        previous = round_dir
