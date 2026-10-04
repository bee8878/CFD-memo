"""Shared Stage D planning state and provider selection."""
from __future__ import annotations

from typing import Any

from cfd_memo_agent.models import (
    ModelClient, ModelError, ModelSettings, create_model_client,
)
from cfd_memo_agent.case_writer import build_case_intent
from cfd_memo_agent.case_writer.agent import (
    PROMPT_VERSION as CASE_WRITER_PROMPT_VERSION, CaseWriterAgent,
)
from cfd_memo_agent.planner import plan_task
from cfd_memo_agent.planner.agent import PROMPT_VERSION, PlannerAgent
from cfd_memo_agent.tutorials import (
    DEFAULT_INDEX_PATH, TutorialRetriever, build_case_spec_proposal,
)
from cfd_memo_agent.reviewer import build_rule_review
from cfd_memo_agent.reviewer.agent import (
    PROMPT_VERSION as REVIEWER_PROMPT_VERSION, ReviewerAgent,
)

FALLBACKS = ("stop", "rules")


def _mentions_registered_case(description: str) -> bool:
    lowered = description.lower() if isinstance(description, str) else ""
    return any(marker in lowered for marker in (
        "圆柱", "cylinder", "方腔", "顶盖驱动", "lid-driven cavity", "lid driven cavity",
    ))


def _planning_metadata(settings: ModelSettings) -> dict[str, Any]:
    return {
        "requested_provider": settings.provider,
        "actual_mode": None,
        "fallback_used": False,
        "fallback_reason": None,
        "model": settings.model,
        "prompt_version": (
            PROMPT_VERSION if settings.provider in {"openai", "deepseek"} else "rules-v1"
        ),
        "trace": None,
    }


def plan_description(
    description: str,
    *,
    settings: ModelSettings | None = None,
    client: ModelClient | None = None,
    fallback: str = "stop",
    memory_context: list[dict[str, Any]] | None = None,
    tutorial_context: dict[str, Any] | None = None,
    tutorial_index_path=None,
) -> dict[str, Any]:
    """Plan one description and return serializable shared state."""
    if fallback not in FALLBACKS:
        raise ValueError(f"planner fallback 必须是：{', '.join(FALLBACKS)}")
    selected = settings or ModelSettings.from_env()
    memories = memory_context or []
    memory_ids = list(dict.fromkeys(item["experience_id"] for item in memories))
    supported_task = None
    rule_error = None
    try:
        supported_task = plan_task(description)
    except (ValueError, ArithmeticError) as exc:
        rule_error = exc
    if tutorial_context is None:
        if supported_task is not None or _mentions_registered_case(description):
            tutorials = {
                "status": "not_needed", "query": description,
                "index_path": str(tutorial_index_path or DEFAULT_INDEX_PATH),
                "candidate_count": 0, "candidates": [], "error": None,
            }
        else:
            tutorials = TutorialRetriever(
                tutorial_index_path or DEFAULT_INDEX_PATH).retrieve(description)
    else:
        tutorials = tutorial_context
    state: dict[str, Any] = {
        "state_version": 1,
        "status": "failed",
        "input_summary": description.strip()[:200] if isinstance(description, str) else "",
        "task": None,
        "assumptions": [],
        "questions": [],
        "experience_ids": [],
        "tutorial_ids": [],
        "case_spec_proposal": None,
        "tutorial_retrieval": tutorials,
        "error": None,
        "planning": _planning_metadata(selected),
    }
    if selected.provider == "rules":
        if supported_task is None and tutorials.get("candidates"):
            proposal = build_case_spec_proposal(description, tutorials)
            state.update({
                "status": "reference_proposal", "case_spec_proposal": proposal,
                "tutorial_ids": proposal["tutorial_ids"],
                "assumptions": ["当前场景没有生成适配器；仅形成官方教程参考提案。"],
            })
            state["planning"]["actual_mode"] = "rules"
            state["planning"]["prompt_version"] = "rules-v2"
            state["planning"]["trace"] = {
                "provider": "rules", "model": None, "role": "planner",
                "prompt_version": "rules-v2", "status": "completed", "usage": None,
            }
            return state
        if supported_task is None:
            state["error"] = {"type": type(rule_error).__name__, "message": str(rule_error)}
            return state
        state["task"] = supported_task
        state["status"] = "ready"
        state["experience_ids"] = memory_ids
        if memory_ids:
            state["assumptions"].append("已参考适用的历史配置经验，仅用于规划防错。")
        state["planning"]["actual_mode"] = "rules"
        state["planning"]["trace"] = {
            "provider": "rules", "model": None, "role": "planner",
            "prompt_version": "rules-v1", "status": "completed", "usage": None,
        }
        return state

    try:
        selected_client = client or create_model_client(selected)
        decision = PlannerAgent(selected_client).plan(
            description, memory_context=memories, tutorial_context=tutorials,
        )
    except (ModelError, ValueError, ArithmeticError) as exc:
        state["error"] = {"type": type(exc).__name__, "message": str(exc)}
        trace = getattr(exc, "trace", None)
        if trace is not None:
            state["planning"].update({
                "actual_mode": selected.provider,
                "trace": trace,
            })
        if fallback != "rules":
            return state
        if supported_task is None and tutorials.get("candidates"):
            proposal = build_case_spec_proposal(description, tutorials)
            state.update({
                "status": "reference_proposal", "case_spec_proposal": proposal,
                "tutorial_ids": proposal["tutorial_ids"],
                "assumptions": ["模型失败后使用确定性教程检索提案；不会生成 case。"],
                "error": None,
            })
            state["planning"].update({
                "actual_mode": "rules_fallback", "fallback_used": True,
                "fallback_reason": str(exc),
                "prompt_version": "rules-v2",
                "trace": {"provider": "rules", "model": None, "role": "planner",
                          "prompt_version": "rules-v2", "status": "completed", "usage": None},
            })
            return state
        if supported_task is None:
            fallback_exc = rule_error
            state["error"] = {
                "type": type(fallback_exc).__name__,
                "message": f"模型规划失败：{exc}；规则回退也失败：{fallback_exc}",
            }
            return state
        state["task"] = supported_task
        state["status"] = "ready"
        state["experience_ids"] = memory_ids
        if memory_ids:
            state["assumptions"].append("规则回退参考了适用的历史配置经验，仅用于规划防错。")
        state["error"] = None
        state["planning"].update({
            "actual_mode": "rules_fallback",
            "fallback_used": True,
            "fallback_reason": str(exc),
            "trace": {
                "provider": "rules", "model": None, "role": "planner",
                "prompt_version": "rules-v1", "status": "completed", "usage": None,
            },
        })
        return state

    state.update({
        "status": decision.status,
        "task": decision.task,
        "assumptions": decision.assumptions,
        "questions": decision.questions,
        "experience_ids": decision.experience_ids,
        "tutorial_ids": decision.tutorial_ids,
        "case_spec_proposal": decision.case_spec_proposal,
    })
    state["planning"].update({"actual_mode": selected.provider, "trace": decision.trace})
    return state


def task_file_planning_state() -> dict[str, Any]:
    """Describe a workflow that starts from an existing task JSON."""
    return {
        "status": "not_applicable", "requested_provider": "task_file",
        "actual_mode": "task_file", "fallback_used": False,
        "fallback_reason": None, "model": None, "prompt_version": None,
        "trace": None, "assumptions": [], "questions": [], "error": None,
        "experience_ids": [],
        "tutorial_ids": [], "case_spec_proposal": None,
        "tutorial_retrieval": {
            "status": "not_applicable", "query": None, "index_path": None,
            "candidate_count": 0, "candidates": [], "error": None,
        },
    }


def prepare_case_writing(
    task: dict[str, Any],
    *,
    settings: ModelSettings | None = None,
    client: ModelClient | None = None,
    fallback: str = "stop",
    memory_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a traceable, bounded intent before the generator writes files."""
    if fallback not in FALLBACKS:
        raise ValueError(f"case writer fallback 必须是：{', '.join(FALLBACKS)}")
    selected = settings or ModelSettings.from_env()
    memories = memory_context or []
    memory_ids = list(dict.fromkeys(item["experience_id"] for item in memories))
    preventive_files = sorted({
        path for item in memories for path in item["action"]["files"]
    })
    state = {
        "status": "failed", "requested_provider": selected.provider,
        "actual_mode": None, "fallback_used": False, "fallback_reason": None,
        "model": selected.model,
        "prompt_version": (
            CASE_WRITER_PROMPT_VERSION
            if selected.provider in {"openai", "deepseek"} else "rules-v1"
        ),
        "trace": None, "intent": None, "rationale": [], "warnings": [],
        "blockers": [], "error": None,
        "experience_ids": [], "preventive_files": [],
    }

    def use_rules(*, reason: str | None = None) -> dict[str, Any]:
        state.update({
            "status": "ready", "actual_mode": "rules_fallback" if reason else "rules",
            "fallback_used": reason is not None, "fallback_reason": reason,
            "intent": build_case_intent(task),
            "rationale": ["按已验证 task 使用固定圆柱模板和受限文件映射。"],
            "warnings": ["生成后的 case 仍须经过 validator 与 runner 检查。"],
            "experience_ids": memory_ids,
            "preventive_files": preventive_files,
            "trace": {
                "provider": "rules", "model": None, "role": "case_writer",
                "prompt_version": "rules-v1", "status": "completed", "usage": None,
            },
        })
        return state

    if selected.provider == "rules":
        return use_rules()
    try:
        selected_client = client or create_model_client(selected)
        decision = CaseWriterAgent(selected_client).prepare(
            task, memory_context=memories,
        )
    except (ModelError, ValueError, ArithmeticError) as exc:
        state["error"] = {"type": type(exc).__name__, "message": str(exc)}
        trace = getattr(exc, "trace", None)
        if trace is not None:
            state.update({"actual_mode": selected.provider, "trace": trace})
        if fallback == "rules":
            state["error"] = None
            return use_rules(reason=str(exc))
        return state
    state.update({
        "status": decision.status, "actual_mode": selected.provider,
        "trace": decision.trace, "intent": decision.intent,
        "rationale": decision.rationale, "warnings": decision.warnings,
        "blockers": decision.blockers,
        "experience_ids": decision.experience_ids,
        "preventive_files": decision.preventive_files,
    })
    return state


def review_evidence(
    evidence: dict[str, Any],
    *,
    settings: ModelSettings | None = None,
    client: ModelClient | None = None,
    fallback: str = "stop",
) -> dict[str, Any]:
    """Ask Reviewer for a bounded decision and retain a serializable trace."""
    if fallback not in FALLBACKS:
        raise ValueError(f"reviewer fallback 必须是：{', '.join(FALLBACKS)}")
    selected = settings or ModelSettings.from_env()
    state = {
        "status": "failed", "requested_provider": selected.provider,
        "actual_mode": None, "fallback_used": False, "fallback_reason": None,
        "model": selected.model,
        "prompt_version": (
            REVIEWER_PROMPT_VERSION
            if selected.provider in {"openai", "deepseek"} else "rules-v1"
        ),
        "trace": None, "decision": None, "finding_codes": [],
        "experience_ids": [],
        "repair_scope": "none", "root_cause": "", "recommendation": "",
        "applicability_conditions": [], "limitations": [], "error": None,
    }

    def use_rules(*, reason: str | None = None) -> dict[str, Any]:
        decision = build_rule_review(evidence)
        state.update(decision)
        state.update({
            "actual_mode": "rules_fallback" if reason else "rules",
            "fallback_used": reason is not None, "fallback_reason": reason,
            "trace": {
                "provider": "rules", "model": None, "role": "reviewer",
                "prompt_version": "rules-v1", "status": "completed", "usage": None,
            },
        })
        return state

    if selected.provider == "rules":
        return use_rules()
    try:
        selected_client = client or create_model_client(selected)
        decision = ReviewerAgent(selected_client).review(evidence)
    except (ModelError, ValueError, ArithmeticError) as exc:
        state["error"] = {"type": type(exc).__name__, "message": str(exc)}
        trace = getattr(exc, "trace", None)
        if trace is not None:
            state.update({"actual_mode": selected.provider, "trace": trace})
        if fallback == "rules":
            state["error"] = None
            return use_rules(reason=str(exc))
        return state
    state.update({
        "status": decision.status, "actual_mode": selected.provider,
        "trace": decision.trace, "decision": decision.decision,
        "finding_codes": decision.finding_codes,
        "experience_ids": decision.experience_ids,
        "repair_scope": decision.repair_scope, "root_cause": decision.root_cause,
        "recommendation": decision.recommendation,
        "applicability_conditions": decision.applicability_conditions,
        "limitations": decision.limitations,
    })
    return state


def planning_record(state: dict[str, Any]) -> dict[str, Any]:
    """Remove the duplicated task and keep the durable decision metadata."""
    return {
        "status": state["status"], **state["planning"],
        "assumptions": state["assumptions"], "questions": state["questions"],
        "experience_ids": state["experience_ids"],
        "tutorial_ids": state["tutorial_ids"],
        "case_spec_proposal": state["case_spec_proposal"],
        "tutorial_retrieval": state["tutorial_retrieval"],
        "error": state["error"],
    }


__all__ = [
    "FALLBACKS", "plan_description", "planning_record", "prepare_case_writing",
    "review_evidence", "task_file_planning_state",
]
