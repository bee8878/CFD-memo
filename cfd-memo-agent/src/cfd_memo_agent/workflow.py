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
from cfd_memo_agent.memory import CaseCache, MemoryManager, save_episode
from cfd_memo_agent.memory.episodes import write_record
from cfd_memo_agent.models import ModelClient, ModelError, ModelSettings
from cfd_memo_agent.orchestrator import (
    plan_description, planning_record, prepare_case_writing, review_evidence,
    task_file_planning_state,
)
from cfd_memo_agent.reporter import write_report
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.foam import parse_json
from cfd_memo_agent.validator.task import finite, issue, new_report

PROJECT = Path(__file__).resolve().parents[2]


def _options(description, task_path, mode, max_corrections, timeout, fault, planner_fallback,
             memory_mode, memory_dir, memory_learning):
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
    if planner_fallback not in {"stop", "rules"}:
        raise ValueError("planner_fallback 必须为 stop 或 rules")
    if memory_mode not in {"no_memory", "simple_cache", "retrieval_only", "cfd_memo"}:
        raise ValueError("memory_mode 必须为 no_memory、simple_cache、retrieval_only 或 cfd_memo")
    if memory_mode == "no_memory" and memory_dir is not None:
        raise ValueError("memory_dir 仅用于启用历史信息的模式")
    if not isinstance(memory_learning, bool):
        raise ValueError("memory_learning 必须为布尔值")


def run_workflow(description=None, *, task_path=None, mode, runs_dir=None,
                 max_corrections=None, timeout=300, fault=None,
                 planner_fallback="stop", model_settings: ModelSettings | None = None,
                 model_client: ModelClient | None = None, memory_mode="no_memory",
                 memory_dir=None, memory_learning=True, tutorial_index_path=None) -> dict:
    """Run C1-C4, optionally repair supported fields, and persist an episode."""
    _options(description, task_path, mode, max_corrections, timeout, fault,
             planner_fallback, memory_mode, memory_dir, memory_learning)
    parent = Path(runs_dir).resolve() if runs_dir is not None else PROJECT / "cases/runs"
    template = (PROJECT / "cases/templates").resolve()
    if parent == template or template in parent.parents:
        raise ValueError("工作流输出不能位于模板目录内")
    root = parent / ("workflow-" + uuid4().hex)
    root.mkdir(parents=True)
    manager = None
    cache = None
    memory_root = Path(memory_dir).resolve() if memory_dir is not None else PROJECT / "cases/memory"
    if memory_mode in {"retrieval_only", "cfd_memo"}:
        manager = MemoryManager(memory_root)
    elif memory_mode == "simple_cache":
        cache = CaseCache(memory_root)
    start = time.monotonic()
    source = {"kind": "description" if task_path is None else "task_file",
              "description": description, "task_path": str(Path(task_path).resolve()) if task_path is not None else None,
              "raw_text": None}
    episode = {
        "schema_version": 3 if memory_mode != "no_memory" else 2,
        "episode_id": root.name, "task_id": None, "task": None,
        "input": source, "mode": memory_mode, "runner_mode": mode,
        "planning": task_file_planning_state() if task_path is not None else {
            "status": "pending", "requested_provider": None, "actual_mode": None,
            "fallback_used": False, "fallback_reason": None, "model": None,
            "prompt_version": None, "trace": None, "assumptions": [],
            "questions": [], "experience_ids": [], "error": None,
            "tutorial_ids": [], "case_spec_proposal": None,
            "tutorial_retrieval": {
                "status": "pending", "query": description, "index_path": None,
                "candidate_count": 0, "candidates": [], "error": None,
            },
        },
        "case_writing": {
            "status": "pending", "requested_provider": None, "actual_mode": None,
            "fallback_used": False, "fallback_reason": None, "model": None,
            "prompt_version": None, "trace": None, "intent": None,
            "rationale": [], "warnings": [], "blockers": [], "error": None,
            "experience_ids": [], "preventive_files": [],
        },
        "status": "failed", "physical_validated": False,
        "started_at": datetime.now(timezone.utc).isoformat(), "workflow_path": str(root),
        "episode_path": str(root / "episode.json"), "report_path": str(root / "report.md"),
        "max_corrections": max_corrections if max_corrections is not None else 2,
        "rounds": [], "reviews": [], "corrections": [], "preventions": [],
        "injected_fault": None,
        "findings": [],
        "stop_reason": {"code": "NOT_STARTED", "message": "任务尚未完成"},
        "case_summary": None, "log_summary": None,
        "diagnosis": {"root_cause": "", "suggested_fix": "", "correction_count": 0},
        "reflection": {"success_factors": [], "failure_causes": [], "reusable_rules": []},
        "reuse_tags": [], "metrics": {"elapsed_seconds": 0, "config_valid": False, "experience_reused": False},
    }
    if memory_mode != "no_memory":
        episode["memory"] = {
            "store_path": str(memory_root), "retrieved_experience_ids": [],
            "retrievals": [], "uses": [], "learned_experience_ids": [],
            "learned_procedure_ids": [],
            "archived_episode_path": None, "learning_enabled": memory_learning,
            "cache_key": None, "cache_hit": False, "cached_case_path": None,
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
        if manager is not None:
            if memory_learning and memory_mode == "cfd_memo":
                manager.record_use_outcomes(episode)
                learned = manager.learn_from_episode(episode)
            else:
                learned = {"experience_ids": [], "procedure_ids": []}
            episode["memory"].update({
                "retrieved_experience_ids": list(manager.working["retrieved_experience_ids"]),
                "retrievals": deepcopy(manager.working["retrievals"]),
                "uses": deepcopy(manager.working["uses"]),
                "learned_experience_ids": learned["experience_ids"],
                "learned_procedure_ids": learned["procedure_ids"],
                "archived_episode_path": (
                    str(manager.episodes_dir / f"{episode['episode_id']}.json")
                    if memory_learning else None
                ),
            })
            episode["metrics"]["experience_reused"] = bool(manager.working["uses"])
        elif cache is not None:
            if (memory_learning and episode["task"] is not None and episode["rounds"]
                    and episode["metrics"]["config_valid"]):
                key, cached_path = cache.store(
                    episode["task"], Path(episode["rounds"][-1]["case_path"]),
                )
                episode["memory"].update({
                    "cache_key": key, "cached_case_path": str(cached_path),
                })
            episode["metrics"]["experience_reused"] = episode["memory"]["cache_hit"]
        save_episode(root / "episode.json", episode)
        write_report(root / "report.md", episode)
        if manager is not None and memory_learning:
            manager.archive_episode(episode)
        return episode

    try:
        selected_settings = model_settings or ModelSettings.from_env()
        if task_path is None:
            planning_memories = (
                manager.retrieve_for_planning(description)
                if manager is not None and memory_mode == "cfd_memo" else []
            )
            state = plan_description(
                description, settings=selected_settings, client=model_client,
                fallback=planner_fallback,
                memory_context=(manager.context(planning_memories) if manager is not None else []),
                tutorial_index_path=tutorial_index_path,
            )
            episode["planning"] = planning_record(state)
            write_record(root / "planning.json", state)
            if state["status"] == "reference_proposal":
                write_record(root / "input.json", source)
                write_record(root / "case-spec-proposal.json", state["case_spec_proposal"])
                problem = issue(
                    "TUTORIAL_PROPOSAL_READY",
                    "已找到官方教程参考并形成非执行 CaseSpec 提案；当前没有生成适配器。",
                    "planner.case_spec_proposal",
                )
                validation = new_report()
                validation["errors"] = [problem]
                write_record(root / "task-validation.json", validation)
                episode["findings"] = diagnose_validation(validation)["findings"]
                return finish(
                    "proposal_ready", "REFERENCE_PROPOSAL_READY",
                    "请审核教程提案并实现或批准相应适配器；本次没有生成或运行 case。",
                )
            if state["status"] != "ready":
                write_record(root / "input.json", source)
                code = (
                    "CLARIFICATION_REQUIRED"
                    if state["status"] == "needs_clarification"
                    else "PLANNING_FAILED"
                )
                message = "；".join(state["questions"])
                if not message and state["error"]:
                    message = state["error"]["message"]
                problem = issue(code, f"任务规划未完成：{message}", "planner")
                validation = new_report()
                validation["errors"] = [problem]
                write_record(root / "task-validation.json", validation)
                episode["findings"] = diagnose_validation(validation)["findings"]
                advice = (
                    "请回答 Planner 提出的问题后重新运行。"
                    if code == "CLARIFICATION_REQUIRED"
                    else "模型规划失败；请检查配置，或显式选择规则回退。"
                )
                return finish("failed", code, advice)
            task = state["task"]
        else:
            source["raw_text"] = Path(task_path).read_text(encoding="utf-8-sig")
            task = parse_json(source["raw_text"])
            write_record(root / "planning.json", episode["planning"])
    except (OSError, UnicodeError, ValueError, ArithmeticError, RecursionError, ModelError) as exc:
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
    if manager is not None:
        manager.start_task(task)
        if memory_mode == "cfd_memo" and episode["planning"]["experience_ids"]:
            manager.note_use(
                episode["planning"]["experience_ids"], agent="planner", round_index=0,
                decision="plan_with_memory", effect="added_planning_guardrails",
            )
        case_memories = (
            manager.retrieve(
                task, query_text=(description or "二维圆柱 OpenFOAM case 配置生成"),
                stage="case_writer",
            ) if memory_mode == "cfd_memo" else []
        )
    else:
        case_memories = []
    cached_case = None
    if cache is not None:
        cache_key, cached_case = cache.lookup(task)
        episode["memory"]["cache_key"] = cache_key
        episode["memory"]["cached_case_path"] = (
            str(cached_case) if cached_case is not None else None
        )
    case_writing = prepare_case_writing(
        task, settings=selected_settings, client=model_client, fallback=planner_fallback,
        memory_context=(manager.context(case_memories) if manager is not None else []),
    )
    episode["case_writing"] = case_writing
    write_record(root / "case-writing.json", case_writing)
    if manager is not None and case_writing["experience_ids"]:
        manager.note_use(
            case_writing["experience_ids"], agent="case_writer", round_index=0,
            decision="prepare_with_memory", effect="added_preventive_file_checks",
        )
    if case_writing["status"] != "ready":
        detail = "；".join(case_writing["blockers"])
        if not detail and case_writing["error"]:
            detail = case_writing["error"]["message"]
        episode["findings"] = [issue(
            "CASE_WRITING_FAILED", f"Case Writer 未生成可接受的写入计划：{detail}",
            "case_writer",
        )]
        return finish(
            "failed", "CASE_WRITING_FAILED",
            "Case Writer 决策未通过，未创建 OpenFOAM case。",
        )
    try:
        reference = Path(generate_case(
            task, runs_dir=root / "reference", intent=case_writing["intent"],
        )["run_path"])
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
            "findings": [], "runtime_blockers": [], "environment": None,
            "mesh_evidence": None, "result_evidence": None,
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
            if index == 0 and cached_case is not None:
                shutil.copytree(cached_case, round_dir / "case", dirs_exist_ok=True)
                episode["memory"]["cache_hit"] = True
                write_record(round_dir / "cache-reuse.json", {
                    "cache_key": episode["memory"]["cache_key"],
                    "cached_case_path": str(cached_case),
                    "scope": "complete_case_exact_task",
                })
            if index == 0 and case_writing["preventive_files"]:
                prevented_files = []
                for relative in case_writing["preventive_files"]:
                    current = round_dir / "case" / relative
                    trusted = reference / "case" / relative
                    if current.read_bytes() != trusted.read_bytes():
                        shutil.copyfile(trusted, current)
                        prevented_files.append(relative)
                if prevented_files:
                    prevention = {
                        "round_index": index,
                        "experience_ids": case_writing["experience_ids"],
                        "files": prevented_files,
                        "action": "restore_from_trusted_reference",
                    }
                    episode["preventions"].append(prevention)
                    write_record(round_dir / "memory-prevention.json", prevention)
                    manager.note_use(
                        case_writing["experience_ids"], agent="case_writer", round_index=index,
                        decision="prevent_configuration_drift",
                        effect="prevented_known_configuration_drift",
                    )
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
        review_input = {
            "round_index": index,
            "runner_mode": mode,
            "outcome_status": outcome["status"],
            "config_valid": record["config_valid"],
            "task_summary": {
                "task_id": task["task_id"], "case_type": task["case_type"],
                "solver": task["solver"],
            },
            "findings": record["findings"],
            "runtime_blockers": record["runtime_blockers"],
            "physical_validated": False,
        }
        memories = (
            manager.retrieve(
                task, problem_codes=[item["code"] for item in record["findings"]],
                query_text=" ".join(item["message"] for item in record["findings"]),
                stage="reviewer",
            ) if manager is not None and record["findings"] else []
        )
        review_input["memory_context"] = (
            manager.context(memories) if manager is not None else []
        )
        review = review_evidence(
            review_input, settings=selected_settings, client=model_client,
            fallback=planner_fallback,
        )
        review_path = round_dir / "review.json"
        review.update({"round_index": index, "review_path": str(review_path)})
        episode["reviews"].append(review)
        write_record(review_path, review)
        if manager is not None and review["experience_ids"]:
            manager.note_use(
                review["experience_ids"], agent="reviewer", round_index=index,
                decision=review["decision"],
            )
        if review["status"] != "reviewed":
            return finish(
                "failed", "REVIEW_FAILED",
                "Reviewer 决策未通过；未实施修改，也未接受本轮结果。",
            )
        if outcome["status"] in {"simulated_success", "completed"} and review["decision"] == "accept":
            return finish(outcome["status"], "COMPLETED", "本次执行模式的流程已完成；单次运行不等于物理结果已验证。")
        if mode == "real" and any(p["code"] != "MESH_NOT_VERIFIED" for p in record["runtime_blockers"]):
            return finish("blocked", "RUNTIME_BLOCKERS", "真实网格或几何验证尚未完成，需在 C6 处理。")
        if outcome["status"] in {"timeout", "environment_error", "execution_error"}:
            status = "blocked" if outcome["status"] == "environment_error" else outcome["status"]
            return finish(status, outcome["status"].upper(), "执行超时或环境/文件错误，需人工处理后再运行。")
        if not record["findings"]:
            return finish("blocked", "NO_DIAGNOSTIC", "没有足够诊断证据，停止自动修正。")
        proposal = propose_repairs(task, round_dir / "case", reference / "case", record["findings"])
        if review["decision"] != "repair":
            return finish(
                "failed", proposal["stop_code"] or "REVIEW_STOPPED",
                "Reviewer 根据现有证据停止自动修正，需人工核对。",
            )
        if not proposal["changes"]:
            return finish("failed", proposal["stop_code"], "没有可实施的安全修正或没有实际变更，需人工核对。")
        if len(episode["corrections"]) >= episode["max_corrections"]:
            return finish("failed", "CORRECTION_BUDGET_EXHAUSTED", "修正次数已达到上限。")
        pending = {"from_round": index, "to_round": index + 1,
                   "reason_codes": sorted({p["code"] for p in record["findings"]}),
                   "changes": proposal["changes"]}
        previous = round_dir
