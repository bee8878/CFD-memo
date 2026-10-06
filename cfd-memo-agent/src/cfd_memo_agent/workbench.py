"""User-facing task creation, history, inspection, and explanation helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from cfd_memo_agent.memory.episodes import write_record
from cfd_memo_agent.models import ModelClient, ModelSettings
from cfd_memo_agent.orchestrator import plan_description
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.foam import read_json


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS_DIR = PROJECT / "cases" / "tasks"
DEFAULT_RUNS_DIR = PROJECT / "cases" / "runs"
SUCCESS_STATUSES = {"completed", "simulated_success"}
STATUS_LABELS = {
    "completed": "真实命令完成",
    "simulated_success": "模拟流程完成",
    "proposal_ready": "等待批准教程提案",
    "failed": "失败",
    "blocked": "被阻止",
    "timeout": "超时",
    "interrupted": "已中断",
    "execution_error": "执行错误",
}


def _task_destination(task: dict[str, Any], output: Path | str | None) -> Path:
    if output is not None:
        target = Path(output).resolve()
        if target.suffix.lower() != ".json":
            raise ValueError("--output 必须是 .json 文件路径")
        return target
    name = f"{task['task_id']}-{uuid4().hex[:8]}.json"
    return (DEFAULT_TASKS_DIR / name).resolve()


def create_task(
    description: str,
    *,
    output: Path | str | None = None,
    fallback: str = "stop",
    tutorial_index_path: Path | str | None = None,
    settings: ModelSettings | None = None,
    client: ModelClient | None = None,
) -> dict[str, Any]:
    """Plan a description and save a validated task without executing CFD."""
    state = plan_description(
        description,
        settings=settings,
        client=client,
        fallback=fallback,
        tutorial_index_path=tutorial_index_path,
    )
    result: dict[str, Any] = {
        "status": state["status"],
        "description": description,
        "task_path": None,
        "plan_path": None,
        "task": state.get("task"),
        "assumptions": state.get("assumptions", []),
        "questions": state.get("questions", []),
        "planning": state.get("planning"),
        "error": state.get("error"),
        "next_action": None,
    }
    if state["status"] != "ready" or state.get("task") is None:
        result["next_action"] = (
            "回答 questions 后重新执行 new。"
            if state.get("questions") else "检查规划错误或教程提案后再继续。"
        )
        return result

    validation = validate_task(state["task"])
    if not validation["task_valid"]:
        result.update({
            "status": "failed",
            "error": {"type": "TaskValidationError", "issues": validation["errors"]},
            "next_action": "修正任务参数后重新创建。",
        })
        return result

    target = _task_destination(state["task"], output)
    plan_path = target.with_name(target.stem + ".plan.json")
    if target.exists():
        raise FileExistsError(f"拒绝覆盖已有任务文件：{target}")
    if plan_path.exists():
        raise FileExistsError(f"拒绝覆盖已有规划记录：{plan_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_record(target, state["task"])
    plan_record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": description,
        "status": state["status"],
        "task_path": str(target),
        "assumptions": state["assumptions"],
        "questions": state["questions"],
        "experience_ids": state["experience_ids"],
        "tutorial_ids": state["tutorial_ids"],
        "planning": state["planning"],
    }
    write_record(plan_path, plan_record)
    result.update({
        "task_path": str(target),
        "plan_path": str(plan_path),
        "validation": validation,
        "next_action": (
            f'先执行 inspect "{target}" 核对，再用 run --task "{target}" '
            "--runner real --preview 生成执行前确认。"
        ),
    })
    return result


def _resolve_record_path(path: Path | str) -> Path:
    target = Path(path).resolve()
    if target.is_dir():
        for name in ("episode.json", "task.json"):
            candidate = target / name
            if candidate.is_file():
                return candidate
        raise ValueError(f"目录中没有 episode.json 或 task.json：{target}")
    if not target.is_file():
        raise ValueError(f"文件不存在：{target}")
    return target


def _task_summary(task: dict[str, Any], path: Path) -> dict[str, Any]:
    report = validate_task(task)
    physics = task.get("physics", {})
    return {
        "record_type": "task",
        "status": "ready" if report["task_valid"] else "invalid",
        "status_label": "任务结构有效" if report["task_valid"] else "任务参数无效",
        "task_id": task.get("task_id"),
        "case_type": task.get("case_type"),
        "solver": task.get("solver"),
        "parameters": {
            "reynolds_number": physics.get("reynolds_number"),
            "inlet_velocity": physics.get("inlet_velocity"),
            "kinematic_viscosity": physics.get("kinematic_viscosity"),
            "end_time": task.get("time_control", {}).get("end_time"),
        },
        "validation": report,
        "physical_validated": False,
        "outputs": {"task": str(path)},
        "next_action": (
            f'python -m cfd_memo_agent.cli run --task "{path}" --runner real --preview'
            if report["task_valid"] else "根据 validation.errors 修正任务。"
        ),
    }


def _output_index(episode: dict[str, Any], path: Path) -> dict[str, Any]:
    rounds = episode.get("rounds") or []
    last = rounds[-1] if rounds else {}
    logs = [item for record in rounds for item in (record.get("log_paths") or [])]
    task_path = episode.get("input", {}).get("task_path")
    report_path = episode.get("report_path")
    summary_path = Path(report_path).with_name("report-summary.json") if report_path else None
    return {
        "episode": str(path),
        "report": report_path,
        "summary": str(summary_path) if summary_path is not None and summary_path.is_file() else None,
        "task": task_path,
        "case": last.get("case_path") or (episode.get("case_summary") or {}).get("case_path"),
        "logs": logs,
    }


def _next_action(episode: dict[str, Any]) -> str:
    status = episode.get("status")
    reason = (episode.get("stop_reason") or {}).get("code")
    if status == "proposal_ready":
        return "审核教程提案后，使用 resume --workflow <目录> --approve-tutorial --runner real。"
    if reason == "CLARIFICATION_REQUIRED":
        questions = episode.get("planning", {}).get("questions") or []
        return "补充信息后重新 new：" + "；".join(questions)
    if status in SUCCESS_STATUSES and episode.get("physical_validated"):
        return "结果已有物理验收证据，可进入对比实验或报告整理。"
    if status in SUCCESS_STATUSES:
        return "执行已完成，但仍需基准对照和独立性检查才能确认物理可信度。"
    if status in {"timeout", "interrupted"}:
        return "检查最后一轮 attempt，使用 run --resume-attempt 安全续跑。"
    if status in {"failed", "blocked", "execution_error"}:
        message = (episode.get("stop_reason") or {}).get("message", "检查报告和日志。")
        return "先处理停止原因：" + message
    return "使用 inspect 检查记录，确认状态后再继续。"


def _episode_summary(episode: dict[str, Any], path: Path) -> dict[str, Any]:
    task = episode.get("task") or {}
    memory = episode.get("memory") or {}
    uses = memory.get("uses") or []
    experience_ids = list(dict.fromkeys(
        item.get("experience_id") for item in uses if item.get("experience_id")
    ))
    return {
        "record_type": "episode",
        "status": episode.get("status", "unknown"),
        "status_label": STATUS_LABELS.get(episode.get("status"), episode.get("status", "未知")),
        "episode_id": episode.get("episode_id"),
        "task_id": episode.get("task_id") or task.get("task_id"),
        "case_type": task.get("case_type"),
        "solver": task.get("solver"),
        "runner_mode": episode.get("runner_mode"),
        "execution_completed": episode.get("status") in SUCCESS_STATUSES,
        "physical_validated": bool(episode.get("physical_validated", False)),
        "correction_count": (episode.get("diagnosis") or {}).get(
            "correction_count", len(episode.get("corrections") or [])),
        "experience_ids": experience_ids,
        "stop_reason": episode.get("stop_reason"),
        "outputs": _output_index(episode, path),
        "next_action": _next_action(episode),
    }


def inspect_record(path: Path | str) -> dict[str, Any]:
    """Return one normalized user-facing view of a task or episode."""
    record_path = _resolve_record_path(path)
    value = read_json(record_path)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{record_path}")
    if "episode_id" in value and "status" in value:
        return _episode_summary(value, record_path)
    if "task_id" in value and "case_type" in value:
        return _task_summary(value, record_path)
    raise ValueError(f"不是可识别的 task 或 episode：{record_path}")


def _episode_paths(root: Path, max_depth: int = 8):
    excluded = {".git", ".venv", "case", "reference", "rounds", "attempts", "checkpoints"}
    base_depth = len(root.parts)
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.parts) - base_depth
        directories[:] = [name for name in directories if name not in excluded]
        if depth >= max_depth:
            directories[:] = []
        if "episode.json" in files:
            yield current_path / "episode.json"


def list_history(
    runs_dir: Path | str | None = None,
    *,
    limit: int = 20,
    status: str | None = None,
) -> dict[str, Any]:
    """List recent workflow episodes without exposing internal directory traversal."""
    if limit < 1 or limit > 200:
        raise ValueError("--limit 必须在 1 到 200 之间")
    root = Path(runs_dir).resolve() if runs_dir is not None else DEFAULT_RUNS_DIR.resolve()
    if not root.is_dir():
        raise ValueError(f"运行记录目录不存在：{root}")
    candidates = sorted(
        _episode_paths(root), key=lambda item: item.stat().st_mtime, reverse=True,
    )
    records = []
    skipped = []
    for path in candidates:
        try:
            summary = inspect_record(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
            continue
        if status is not None and summary["status"] != status:
            continue
        records.append(summary)
        if len(records) >= limit:
            break
    return {"runs_dir": str(root), "count": len(records), "records": records, "skipped": skipped}


def explain_record(path: Path | str) -> dict[str, Any]:
    """Explain a normalized record in beginner-oriented Chinese."""
    summary = inspect_record(path)
    if summary["record_type"] == "task":
        meaning = "这是尚未运行的结构化 CFD 任务。参数检查通过不代表已经得到流场结果。"
        evidence = ["任务 JSON", "参数与 schema 检查"]
    else:
        meaning = (
            "这是一次完整工作流记录。execution_completed 表示命令链结束；"
            "physical_validated 只有经过物理基准对照后才会为 true。"
        )
        evidence = [value for value in (
            summary["outputs"].get("episode"),
            summary["outputs"].get("report"),
            summary["outputs"].get("summary"),
            summary["outputs"].get("case"),
            *summary["outputs"].get("logs", []),
        ) if value]
    return {
        "summary": summary,
        "meaning": meaning,
        "evidence": evidence,
        "next_action": summary["next_action"],
    }


__all__ = ["create_task", "explain_record", "inspect_record", "list_history"]
