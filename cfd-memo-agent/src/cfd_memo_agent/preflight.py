"""Deterministic execution previews and task-bound confirmations."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from cfd_memo_agent.memory import MemoryManager
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.foam import read_json


PROJECT = Path(__file__).resolve().parents[2]
MEMORY_MODES = {"no_memory", "simple_cache", "retrieval_only", "cfd_memo"}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def task_sha256(task: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(task)).hexdigest()


def _plan_context(task_path: Path) -> dict[str, Any]:
    plan_path = task_path.with_name(task_path.stem + ".plan.json")
    if not plan_path.is_file():
        return {"plan_path": None, "assumptions": [], "questions": []}
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        raise ValueError(f"规划记录顶层必须是对象：{plan_path}")
    assumptions = plan.get("assumptions", [])
    questions = plan.get("questions", [])
    if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
        raise ValueError(f"规划记录 assumptions 无效：{plan_path}")
    if not isinstance(questions, list) or not all(isinstance(item, str) for item in questions):
        raise ValueError(f"规划记录 questions 无效：{plan_path}")
    return {"plan_path": str(plan_path), "assumptions": assumptions, "questions": questions}


def _memory_preview(task: dict[str, Any], memory_mode: str,
                    memory_dir: Path | str | None) -> tuple[str | None, list[dict[str, Any]]]:
    if memory_mode not in MEMORY_MODES:
        raise ValueError("memory_mode 无效")
    if memory_mode not in {"retrieval_only", "cfd_memo"}:
        return None, []
    root = Path(memory_dir).resolve() if memory_dir is not None else PROJECT / "cases/memory"
    manager = MemoryManager(root)
    query = (
        f"{task['case_type']} {task['solver']} {task['physics']['flow_model']} "
        f"Re={task['physics'].get('reynolds_number')} execution preflight"
    )
    records = manager.retrieve(task, query_text=query, stage="planner")
    return str(root), [{
        "experience_id": item["experience_id"],
        "trust_level": item["status"],
        "verification_scope": item["verification_scope"],
        "problem_codes": item["problem_codes"],
        "action_files": item["action"]["files"],
        "score": item["retrieval_score"]["final"],
        "reasons": item["retrieval_reason"]["summary"],
        "sources": [{
            "episode_id": evidence["episode_id"],
            "runner_mode": evidence["runner_mode"],
            "integrity_verified": evidence["integrity_verified"],
            "artifact_paths": [artifact["path"] for artifact in evidence["artifacts"]],
        } for evidence in item["evidence"]],
    } for item in records]


def _contract(task: dict[str, Any], *, runner_mode: str, memory_mode: str,
              memory_root: str | None, experience_ids: list[str],
              max_corrections: int | None, timeout: float) -> dict[str, Any]:
    return {
        "task_sha256": task_sha256(task),
        "runner_mode": runner_mode,
        "memory_mode": memory_mode,
        "memory_root": memory_root,
        "experience_ids": experience_ids,
        "max_corrections": max_corrections,
        "timeout": timeout,
    }


def build_preflight(
    task_path: Path | str,
    *,
    runner_mode: str,
    memory_mode: str = "no_memory",
    memory_dir: Path | str | None = None,
    max_corrections: int | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Build a preview whose confirmation token is bound to task and run options."""
    if runner_mode not in {"simulated", "real"}:
        raise ValueError("runner_mode 必须为 simulated 或 real")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout 必须为正数")
    path = Path(task_path).resolve()
    task = read_json(path)
    if not isinstance(task, dict):
        raise ValueError(f"task JSON 顶层必须是对象：{path}")
    validation = validate_task(task)
    plan = _plan_context(path)
    memory_root, experiences = _memory_preview(task, memory_mode, memory_dir)
    questions = list(plan["questions"])
    blockers = list(validation["errors"])
    status = "ready" if validation["task_valid"] and not questions else "needs_input"
    contract = _contract(
        task, runner_mode=runner_mode, memory_mode=memory_mode,
        memory_root=memory_root,
        experience_ids=[item["experience_id"] for item in experiences],
        max_corrections=max_corrections, timeout=float(timeout),
    )
    token = hashlib.sha256(_canonical(contract)).hexdigest()
    return {
        "schema_version": 1,
        "status": status,
        "task_path": str(path),
        "task_id": task.get("task_id"),
        "task_sha256": contract["task_sha256"],
        "runner_mode": runner_mode,
        "parameters": {
            "case_type": task.get("case_type"),
            "solver": task.get("solver"),
            "dimension": task.get("geometry", {}).get("dimension"),
            "reynolds_number": task.get("physics", {}).get("reynolds_number"),
            "inlet_velocity": task.get("physics", {}).get("inlet_velocity"),
            "kinematic_viscosity": task.get("physics", {}).get("kinematic_viscosity"),
            "cylinder_diameter": task.get("geometry", {}).get("cylinder_diameter"),
            "end_time": task.get("time_control", {}).get("end_time"),
            "delta_t": task.get("time_control", {}).get("delta_t"),
            "target_cells": task.get("mesh", {}).get("target_cells"),
            "max_corrections": (
                max_corrections if max_corrections is not None
                else task.get("convergence", {}).get("max_corrections")
            ),
            "timeout_per_command": float(timeout),
        },
        "plan_path": plan["plan_path"],
        "assumptions": plan["assumptions"],
        "questions": questions,
        "validation_errors": blockers,
        "validation_warnings": validation["warnings"],
        "memory": {
            "mode": memory_mode,
            "store_path": memory_root,
            "experiences": experiences,
        },
        "risks": [
            {"code": "PHYSICS_NOT_YET_VALIDATED",
             "message": "命令完成不等于物理结果可信，仍需基准对照和独立性检查。"},
            {"code": "MESH_TARGET_IS_ESTIMATE",
             "message": "target_cells 是规划值，实际网格数量由 blockMesh/checkMesh 确认。"},
            {"code": "RUNTIME_ENVIRONMENT_PENDING",
             "message": "OpenFOAM、WSL、磁盘和网格只会在真实执行阶段最终检查。"},
        ],
        "confirmation_required": runner_mode == "real",
        "confirmation_token": token,
        "confirmation": None,
        "next_action": (
            "先回答 questions 并重新生成 task。" if status != "ready" else
            f"核对后重新运行原命令并增加 --confirm-plan {token}。"
            if runner_mode == "real" else "参数检查完成，可以启动模拟工作流。"
        ),
    }


def confirm_preflight(preflight: dict[str, Any], token: str) -> dict[str, Any]:
    if preflight["status"] != "ready":
        raise ValueError("任务仍有待确认问题或参数错误，不能执行")
    valid_shape = (
        isinstance(token, str) and len(token) == 64
        and all(character in "0123456789abcdef" for character in token)
    )
    if not valid_shape or not hmac.compare_digest(token, preflight["confirmation_token"]):
        raise ValueError("确认码无效；任务、经验或执行参数可能已经变化，请重新 --preview")
    confirmed = json.loads(json.dumps(preflight, ensure_ascii=False))
    confirmed["confirmation"] = {
        "confirmed": True,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
    }
    return confirmed


def verify_confirmed_task(task: dict[str, Any], preflight: dict[str, Any] | None) -> None:
    """Reject a changed task before any case files are generated."""
    if preflight is None:
        return
    if preflight.get("confirmation", {}).get("confirmed") is not True:
        raise ValueError("preflight 没有有效确认记录")
    expected = preflight.get("task_sha256")
    if not isinstance(expected, str) or not hmac.compare_digest(task_sha256(task), expected):
        raise ValueError("task 在确认后发生变化，拒绝执行；请重新 --preview")


__all__ = [
    "build_preflight", "confirm_preflight", "task_sha256", "verify_confirmed_task",
]
