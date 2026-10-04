"""Resume one proposal-ready workflow after explicit tutorial approval."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from jsonschema import ValidationError

from cfd_memo_agent.memory import save_episode
from cfd_memo_agent.memory.episodes import episode_validator, write_record
from cfd_memo_agent.reporter import write_report
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.tutorial_builder import build_tutorial_case
from cfd_memo_agent.tutorials import DEFAULT_INDEX_PATH
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.validator.task import issue


def _case_writing(status="pending", *, intent=None, rationale=(), warnings=(), blockers=(), error=None):
    return {
        "status": status, "requested_provider": "rules", "actual_mode": "rules",
        "fallback_used": False, "fallback_reason": None, "model": None,
        "prompt_version": "tutorial-builder-v1", "trace": None, "intent": intent,
        "rationale": list(rationale), "warnings": list(warnings),
        "blockers": list(blockers), "error": error,
        "experience_ids": [], "preventive_files": [],
    }


def _proposal_digest(proposal: dict) -> str:
    encoded = json.dumps(
        proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resume_tutorial_workflow(
    workflow_dir: Path | str, *, approved: bool, mode: str,
    timeout: float = 300, tutorial_index_path: Path | str = DEFAULT_INDEX_PATH,
) -> dict:
    """Build and execute an approved I2 proposal while preserving its original episode."""
    if approved is not True:
        raise ValueError("必须显式使用 --approve-tutorial 才能继续教程提案")
    if mode != "real":
        raise ValueError("教程提案续跑当前只支持 real，不用模拟日志冒充新场景结果")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout 必须是有限正数")
    requested_root = Path(workflow_dir)
    if requested_root.is_symlink():
        raise ValueError(f"workflow 目录不存在或不允许使用链接：{requested_root}")
    parent_root = requested_root.resolve()
    if not parent_root.is_dir():
        raise ValueError(f"workflow 目录不存在或不允许使用链接：{parent_root}")
    for name in ("episode.json", "case-spec-proposal.json", "planning.json"):
        path = parent_root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"proposal-ready workflow 缺少安全的 {name}")
    parent = read_json(parent_root / "episode.json")
    try:
        episode_validator().validate(parent)
    except ValidationError as exc:
        raise ValueError("原工作流的 episode.json 不符合当前 schema") from exc
    if Path(parent["workflow_path"]).resolve() != parent_root:
        raise ValueError("episode 记录的 workflow_path 与目标目录不一致")
    if (parent["status"] != "proposal_ready"
            or parent["stop_reason"]["code"] != "REFERENCE_PROPOSAL_READY"):
        raise ValueError("只能续跑 proposal_ready 的教程工作流")
    proposal = read_json(parent_root / "case-spec-proposal.json")
    if proposal != parent["planning"].get("case_spec_proposal"):
        raise ValueError("保存的教程提案与原 episode 不一致")

    started = time.monotonic()
    resume_id = "resume-" + uuid4().hex
    resume_root = parent_root / "resumes" / resume_id
    resume_root.mkdir(parents=True, exist_ok=False)
    approval = {
        "schema_version": 1, "approved": True,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "parent_episode_id": parent["episode_id"],
        "proposal_sha256": _proposal_digest(proposal),
        "tutorial_ids": list(proposal["tutorial_ids"]),
        "runner_mode": mode, "physical_validated": False,
    }
    write_record(resume_root / "approval.json", approval)
    write_record(resume_root / "parent-episode.json", parent)

    episode = deepcopy(parent)
    episode.update({
        "schema_version": 2, "episode_id": resume_id, "task_id": None, "task": None,
        "mode": "no_memory", "runner_mode": mode, "status": "failed",
        "physical_validated": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "workflow_path": str(resume_root),
        "episode_path": str(resume_root / "episode.json"),
        "report_path": str(resume_root / "report.md"), "max_corrections": 0,
        "rounds": [], "reviews": [], "corrections": [], "preventions": [],
        "injected_fault": None, "findings": [],
        "stop_reason": {"code": "NOT_STARTED", "message": "批准后的教程任务尚未完成"},
        "case_summary": None, "log_summary": None,
        "diagnosis": {"root_cause": "", "suggested_fix": "", "correction_count": 0},
        "reflection": {"success_factors": [], "failure_causes": [], "reusable_rules": []},
        "reuse_tags": [],
        "metrics": {"elapsed_seconds": 0, "config_valid": False,
                    "experience_reused": False},
        "case_writing": _case_writing(),
        "tutorial_resume": {
            "parent_episode_id": parent["episode_id"],
            "parent_workflow_path": str(parent_root),
            "approval_path": str(resume_root / "approval.json"),
            "build_path": None, "approved": True,
        },
    })
    episode.pop("memory", None)

    def finish(status: str, code: str, message: str) -> dict:
        episode["status"] = status
        episode["stop_reason"] = {"code": code, "message": message}
        episode["metrics"]["elapsed_seconds"] = time.monotonic() - started
        all_findings = episode["findings"] + [
            item for record in episode["rounds"] for item in record["findings"]
        ]
        episode["reflection"]["failure_causes"] = list(dict.fromkeys(
            item["message"] for item in all_findings))
        episode["diagnosis"]["root_cause"] = "；".join(
            episode["reflection"]["failure_causes"])
        episode["diagnosis"]["suggested_fix"] = message
        if status == "completed":
            episode["reflection"]["success_factors"] = [
                "教程提案经显式确认，白名单构建、静态检查、网格检查和真实求解完成。"
            ]
        save_episode(resume_root / "episode.json", episode)
        write_report(resume_root / "report.md", episode)
        return episode

    try:
        build = build_tutorial_case(
            proposal, index_path=tutorial_index_path, runs_dir=resume_root / "builds")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        problem = issue("TUTORIAL_BUILD_FAILED", str(exc), "tutorial_builder")
        episode["findings"] = [problem]
        episode["case_writing"] = _case_writing(
            "failed", blockers=[str(exc)],
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return finish("failed", "TUTORIAL_BUILD_FAILED", "教程副本构建失败，未启动 OpenFOAM。")

    build_root = Path(build["run_path"]).resolve()
    builds_root = (resume_root / "builds").resolve()
    if builds_root not in build_root.parents:
        episode["findings"] = [issue(
            "TUTORIAL_BUILD_PATH", "教程 Builder 输出超出本次续跑目录", str(build_root))]
        return finish("failed", "TUTORIAL_BUILD_PATH", "拒绝执行来源不明的 Builder 输出。")
    task = read_json(build_root / "task.json")
    generation = read_json(build_root / "generation.json")
    episode["task"], episode["task_id"] = task, task["task_id"]
    episode["reuse_tags"] = [
        task["case_type"], task["solver"], "tutorial",
        task["physics"]["flow_model"],
    ]
    episode["metrics"]["config_valid"] = build["validation"]["config_valid"]
    episode["tutorial_resume"]["build_path"] = str(build_root / "tutorial-build.json")
    episode["case_writing"] = _case_writing(
        "ready",
        intent={
            "builder": "reviewed_tutorial",
            "capability_id": build["capability_id"],
            "tutorial_id": build["tutorial_id"],
            "source_manifest_sha256": build["source_manifest_sha256"],
            "scripts_copied": False, "scripts_executed": False,
        },
        rationale=[
            "用户显式批准 I2 提案；使用已审查的教程能力和文件白名单 Builder。"
        ],
        warnings=["工程执行完成不代表该教程的物理结果已经验证。"],
    )
    if build.get("status") != "built" or not build["validation"]["config_valid"]:
        episode["case_writing"] = _case_writing(
            "blocked", intent=episode["case_writing"]["intent"],
            blockers=["教程 Builder 的静态配置检查未通过。"],
        )
        episode["findings"] = [issue(
            "TUTORIAL_BUILD_INVALID", "教程 Builder 的静态配置检查未通过",
            str(build_root / "tutorial-build.json"),
        )]
        return finish("failed", "TUTORIAL_BUILD_INVALID", "静态配置未通过，未启动 OpenFOAM。")
    try:
        outcome = run_case(build_root, mode=mode, timeout=timeout)
    except KeyboardInterrupt:
        return finish("interrupted", "INTERRUPTED", "用户中断真实执行，已保存批准和构建记录。")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        episode["findings"] = [issue("EXECUTION_FAILED", str(exc), str(build_root))]
        return finish("execution_error", "EXECUTION_FAILED", "真实执行入口失败，请检查保存的构建记录。")

    runtime_blockers = list(outcome["validation"]["runtime_blockers"])
    if outcome.get("mesh_evidence"):
        runtime_blockers = [item for item in runtime_blockers
                            if item["code"] != "MESH_NOT_VERIFIED"]
    record = {
        "index": 0, "run_path": str(build_root),
        "case_path": outcome.get("case_path") or str(build_root / "case"),
        "status": outcome["status"],
        "execution_path": str(Path(outcome["attempt_path"]) / "execution.json"),
        "validation_path": str(Path(outcome["attempt_path"]) / "validation.json"),
        "config_valid": outcome["validation"]["config_valid"],
        "environment": outcome.get("environment"),
        "mesh_evidence": outcome.get("mesh_evidence"),
        "result_evidence": outcome.get("result_evidence"),
        "log_paths": [step["log_path"] for step in outcome["steps"]],
        "residuals": [item for step in outcome["steps"]
                      for item in step["diagnosis"].get("residuals", [])],
        "findings": list(outcome["findings"]),
        "runtime_blockers": runtime_blockers,
    }
    episode["rounds"] = [record]
    episode["metrics"]["config_valid"] = record["config_valid"]
    episode["case_summary"] = {
        "case_type": task["case_type"], "solver": task["solver"],
        "case_path": record["case_path"],
        "generated_files": list(generation["generated_files"]),
    }
    if record["log_paths"]:
        episode["log_summary"] = {
            "log_path": record["log_paths"][-1],
            "error_type": record["findings"][0]["code"] if record["findings"] else None,
            "residual_summary": f"记录 {len(record['residuals'])} 条残差；未判定物理准确性。",
        }
    top_status = "blocked" if outcome["status"] == "environment_error" else outcome["status"]
    if top_status == "completed":
        return finish(
            "completed", "COMPLETED",
            "教程 case 已完成真实工程执行；物理准确性仍未验证。",
        )
    return finish(
        top_status, outcome["status"].upper(),
        "教程 case 未完成真实执行，请根据本轮日志和证据处理。",
    )


__all__ = ["resume_tutorial_workflow"]
