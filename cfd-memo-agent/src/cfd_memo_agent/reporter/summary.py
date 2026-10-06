"""Build a stable, machine-readable answer sheet for one workflow episode."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from cfd_memo_agent.memory.episodes import write_record
from cfd_memo_agent.validator.foam import read_json


SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas/report-summary.schema.json"
SUCCESS_STATUSES = {"completed", "simulated_success"}
STATUS_LABELS = {
    "completed": "真实命令完成",
    "simulated_success": "模拟流程完成",
    "proposal_ready": "教程参考提案已生成",
    "failed": "失败",
    "blocked": "被阻止",
    "timeout": "超时",
    "interrupted": "用户中断",
    "execution_error": "执行错误",
}


@lru_cache(maxsize=1)
def summary_validator() -> Draft202012Validator:
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _task_answer(episode: dict[str, Any]) -> dict[str, Any]:
    task = episode.get("task") or {}
    source = episode.get("input") or {}
    description = source.get("description")
    case_type = task.get("case_type")
    solver = task.get("solver")
    objective = description or (
        f"使用 {solver} 处理 {case_type}。" if case_type and solver
        else "任务规划未形成有效 CFD task。"
    )
    physics = task.get("physics") or {}
    geometry = task.get("geometry") or {}
    time_control = task.get("time_control") or {}
    return {
        "objective": objective,
        "task_id": episode.get("task_id") or task.get("task_id"),
        "case_type": case_type,
        "solver": solver,
        "parameters": {
            "reynolds_number": physics.get("reynolds_number"),
            "inlet_velocity": physics.get("inlet_velocity"),
            "kinematic_viscosity": physics.get("kinematic_viscosity"),
            "dimension": geometry.get("dimension"),
            "end_time": time_control.get("end_time"),
            "delta_t": time_control.get("delta_t"),
        },
    }


def _execution_answer(episode: dict[str, Any]) -> dict[str, Any]:
    status = episode.get("status", "failed")
    rounds = episode.get("rounds") or []
    return {
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "completed": status in SUCCESS_STATUSES,
        "runner_mode": episode.get("runner_mode"),
        "comparison_mode": episode.get("mode"),
        "round_count": len(rounds),
        "correction_count": (episode.get("diagnosis") or {}).get(
            "correction_count", len(episode.get("corrections") or [])),
        "elapsed_seconds": float((episode.get("metrics") or {}).get("elapsed_seconds", 0)),
        "stop_reason": episode.get("stop_reason") or {
            "code": "UNKNOWN", "message": "没有保存停止原因。",
        },
    }


def _physical_answer(episode: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    validated = bool(episode.get("physical_validated", False))
    rounds = episode.get("rounds") or []
    result_paths = []
    for record in rounds:
        evidence = record.get("result_evidence") or {}
        result_paths.extend((evidence.get("fields") or {}).values())
    if validated:
        verdict = "validated"
        explanation = "该 episode 保存了通过项目物理验收规则的结果证据。"
    elif not execution["completed"]:
        verdict = "no_completed_run"
        explanation = "执行尚未完成，因此不能评价物理结果是否可信。"
    elif episode.get("runner_mode") == "simulated":
        verdict = "not_validated"
        explanation = "模拟日志只验证工作流，不代表真实 CFD 流场。"
    else:
        verdict = "not_validated"
        explanation = "真实命令完成只证明工程链路可用；仍需基准对照和网格/时间步独立性检查。"
    return {
        "validated": validated,
        "verdict": verdict,
        "explanation": explanation,
        "result_evidence": _unique(result_paths),
    }


def _memory_answer(episode: dict[str, Any]) -> dict[str, Any]:
    mode = episode.get("mode", "no_memory")
    memory = episode.get("memory") or {}
    uses = memory.get("uses") or []
    preventions = episode.get("preventions") or []
    retrieved = _unique(memory.get("retrieved_experience_ids") or [])
    used = _unique(item.get("experience_id") for item in uses)
    effective = _unique([
        *(item.get("experience_id") for item in uses if item.get("outcome") == "effective"),
        *(experience_id for item in preventions
          for experience_id in item.get("experience_ids", [])),
    ])
    prevented_files = _unique(
        file for item in preventions for file in item.get("files", [])
    )
    learned = _unique([
        *(memory.get("learned_experience_ids") or []),
        *(memory.get("learned_procedure_ids") or []),
    ])
    if mode == "no_memory":
        conclusion = "本次未启用长期记忆，结果不能归因于经验复用。"
    elif effective:
        conclusion = "已有经验产生了可追踪的有效引用或执行前防错。"
    elif used:
        conclusion = "工作流引用了经验，但当前记录尚不能证明它带来有效改进。"
    elif retrieved:
        conclusion = "检索到了候选经验，但 Agent 没有采用。"
    elif mode == "simple_cache" and memory.get("cache_hit"):
        conclusion = "命中了完全相同 task 的完整 case 缓存。"
    else:
        conclusion = "本次没有检索到或采用可用经验。"
    return {
        "mode": mode,
        "retrieved_experience_ids": retrieved,
        "used_experience_ids": used,
        "effective_experience_ids": effective,
        "prevented_files": prevented_files,
        "learned_record_ids": learned,
        "conclusion": conclusion,
    }


def _outputs(episode: dict[str, Any]) -> dict[str, Any]:
    rounds = episode.get("rounds") or []
    last = rounds[-1] if rounds else {}
    report = episode.get("report_path")
    summary = str(Path(report).with_name("report-summary.json")) if report else None
    return {
        "episode": episode.get("episode_path"),
        "report": report,
        "summary": summary,
        "task": (episode.get("input") or {}).get("task_path"),
        "case": last.get("case_path") or (episode.get("case_summary") or {}).get("case_path"),
        "logs": _unique(
            log for record in rounds for log in (record.get("log_paths") or [])
        ),
    }


def _next_steps(episode: dict[str, Any], execution: dict[str, Any],
                physical: dict[str, Any]) -> list[str]:
    status = execution["status"]
    reason = execution["stop_reason"].get("code")
    if status == "proposal_ready":
        return ["审核教程提案来源和参数。", "确认后使用 resume 的显式批准入口继续。"]
    if reason == "CLARIFICATION_REQUIRED":
        questions = (episode.get("planning") or {}).get("questions") or []
        return [*("补充条件：" + item for item in questions), "重新创建任务并再次预览。"]
    if status in {"timeout", "interrupted"}:
        return ["查看最后一次 attempt 的阶段状态和日志。", "核对 checkpoint 后使用 --resume-attempt。"]
    if status in {"failed", "blocked", "execution_error"}:
        return ["处理停止原因：" + execution["stop_reason"].get("message", "检查报告和日志。")]
    if episode.get("runner_mode") == "simulated":
        return ["用 --runner real 生成执行前预览并确认。", "真实完成后再做物理验收。"]
    if physical["validated"]:
        return ["将该结果纳入跨任务对比实验，并保留完整证据链。"]
    return ["与公开基准物理量比较。", "完成网格与时间步独立性检查。"]


def build_report_summary(episode: dict[str, Any]) -> dict[str, Any]:
    """Answer the five user questions with evidence-derived fields only."""
    task = _task_answer(episode)
    execution = _execution_answer(episode)
    physical = _physical_answer(episode, execution)
    memory = _memory_answer(episode)
    summary = {
        "schema_version": 1,
        "episode_id": episode.get("episode_id"),
        "task": task,
        "execution": execution,
        "physical_result": physical,
        "memory_contribution": memory,
        "outputs": _outputs(episode),
        "next_steps": _next_steps(episode, execution, physical),
    }
    summary_validator().validate(summary)
    return summary


def write_report_summary(path: Path, episode: dict[str, Any]) -> dict[str, Any]:
    summary = build_report_summary(episode)
    write_record(path, summary)
    return summary


__all__ = ["build_report_summary", "summary_validator", "write_report_summary"]
