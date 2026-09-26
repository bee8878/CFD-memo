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
from cfd_memo_agent.reviewer import build_rule_review
from cfd_memo_agent.reviewer.agent import (
    PROMPT_VERSION as REVIEWER_PROMPT_VERSION, ReviewerAgent,
)

FALLBACKS = ("stop", "rules")


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
) -> dict[str, Any]:
    """Plan one description and return serializable shared state."""
    if fallback not in FALLBACKS:
        raise ValueError(f"planner fallback 必须是：{', '.join(FALLBACKS)}")
    selected = settings or ModelSettings.from_env()
    state: dict[str, Any] = {
        "state_version": 1,
        "status": "failed",
        "input_summary": description.strip()[:200] if isinstance(description, str) else "",
        "task": None,
        "assumptions": [],
        "questions": [],
        "error": None,
        "planning": _planning_metadata(selected),
    }
    if selected.provider == "rules":
        try:
            state["task"] = plan_task(description)
        except (ValueError, ArithmeticError) as exc:
            state["error"] = {"type": type(exc).__name__, "message": str(exc)}
            return state
        state["status"] = "ready"
        state["planning"]["actual_mode"] = "rules"
        state["planning"]["trace"] = {
            "provider": "rules", "model": None, "role": "planner",
            "prompt_version": "rules-v1", "status": "completed", "usage": None,
        }
        return state

    try:
        selected_client = client or create_model_client(selected)
        decision = PlannerAgent(selected_client).plan(description)
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
        try:
            state["task"] = plan_task(description)
        except (ValueError, ArithmeticError) as fallback_exc:
            state["error"] = {
                "type": type(fallback_exc).__name__,
                "message": f"模型规划失败：{exc}；规则回退也失败：{fallback_exc}",
            }
            return state
        state["status"] = "ready"
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
    }


def prepare_case_writing(
    task: dict[str, Any],
    *,
    settings: ModelSettings | None = None,
    client: ModelClient | None = None,
    fallback: str = "stop",
) -> dict[str, Any]:
    """Produce a traceable, bounded intent before the generator writes files."""
    if fallback not in FALLBACKS:
        raise ValueError(f"case writer fallback 必须是：{', '.join(FALLBACKS)}")
    selected = settings or ModelSettings.from_env()
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
    }

    def use_rules(*, reason: str | None = None) -> dict[str, Any]:
        state.update({
            "status": "ready", "actual_mode": "rules_fallback" if reason else "rules",
            "fallback_used": reason is not None, "fallback_reason": reason,
            "intent": build_case_intent(task),
            "rationale": ["按已验证 task 使用固定圆柱模板和受限文件映射。"],
            "warnings": ["生成后的 case 仍须经过 validator 与 runner 检查。"],
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
        decision = CaseWriterAgent(selected_client).prepare(task)
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
        "error": state["error"],
    }


__all__ = [
    "FALLBACKS", "plan_description", "planning_record", "prepare_case_writing",
    "review_evidence", "task_file_planning_state",
]
