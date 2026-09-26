"""LLM-backed planning for the fixed cylinder-flow MVP."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from cfd_memo_agent.models import ModelClient, ModelOutputError, ModelRequest
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.task import schema_validator

from . import DEFAULT_CYLINDER_TASK

PROMPT_VERSION = "planner-v1"

INSTRUCTIONS = """你是 CFD-Memo 的 Planner Agent。你的唯一任务是把用户需求整理为当前 MVP
支持的二维圆柱绕流任务。只支持 icoFoam、不可压缩层流、manual-template 网格和默认六个边界。

必须遵守：
1. 若需求属于支持范围且没有互相冲突的关键参数，status=ready，并返回完整 task。
2. 缺少的普通参数可以使用 schema 中体现的默认任务值，并在 assumptions 中逐项说明。
3. 若需求超出支持范围、关键参数冲突，或无法可靠确定物理意图，status=needs_clarification，
   task=null，并在 questions 中给出简短、具体的问题。
4. 满足 Re=U*D/nu；不得捏造求解结果，不得输出 Markdown，不得添加 schema 外字段。
5. task_id 使用 task-cylinder-2d-re<雷诺数>；小数点用 p 替代。
"""


def _strict_schema(
    source: dict[str, Any], template: Any, path: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Project the repository task schema onto the complete MVP template."""
    if isinstance(template, dict):
        declared = source.get("properties", {})
        fallback = source.get("additionalProperties")
        properties: dict[str, Any] = {}
        for key, value in template.items():
            child = declared.get(key, fallback if isinstance(fallback, dict) else {})
            properties[key] = _strict_schema(child, value, (*path, key))
        return {
            "type": "object",
            "properties": properties,
            "required": list(template),
            "additionalProperties": False,
        }
    if isinstance(template, str) and path != ("task_id",):
        return {"const": template}
    result = deepcopy(source)
    for key in ("$schema", "$id", "title", "dependentRequired"):
        result.pop(key, None)
    return result


STRICT_TASK_SCHEMA = _strict_schema(schema_validator().schema, DEFAULT_CYLINDER_TASK)
PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ready", "needs_clarification"]},
        "task": {"anyOf": [STRICT_TASK_SCHEMA, {"type": "null"}]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "task", "assumptions", "questions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PlanningDecision:
    status: str
    task: dict[str, Any] | None
    assumptions: list[str]
    questions: list[str]
    trace: dict[str, Any]


class PlannerDecisionError(ModelOutputError):
    """A model answered, but its planning decision cannot be accepted."""

    def __init__(self, message: str, trace: dict[str, Any]):
        super().__init__(message)
        self.trace = trace


class PlannerAgent:
    """Ask a model for a candidate task, then enforce deterministic checks."""

    def __init__(self, client: ModelClient):
        self.client = client

    def plan(self, description: str) -> PlanningDecision:
        normalized = description.strip()
        if not normalized:
            raise ValueError("Task description cannot be empty.")
        request = ModelRequest(
            role="planner",
            instructions=INSTRUCTIONS,
            input_text=normalized,
            output_schema=PLANNER_OUTPUT_SCHEMA,
            schema_name="cfd_memo_planning_decision",
            prompt_version=PROMPT_VERSION,
        )
        result = self.client.complete_json(request)
        output = result.output
        status = output["status"]
        task = output["task"]
        assumptions = output["assumptions"]
        questions = output["questions"]
        if status == "needs_clarification":
            if task is not None or not questions:
                raise PlannerDecisionError(
                    "需要澄清时 task 必须为空，并至少提供一个问题", result.trace)
        else:
            if task is None:
                raise PlannerDecisionError("规划完成时必须返回 task", result.trace)
            if questions:
                raise PlannerDecisionError("规划完成时 questions 必须为空", result.trace)
            report = validate_task(task)
            if not report["task_valid"]:
                details = "；".join(
                    f"{item['location']}: {item['message']}" for item in report["errors"]
                )
                raise PlannerDecisionError(
                    f"模型任务未通过 CFD 规则检查：{details}", result.trace)
        return PlanningDecision(status, task, assumptions, questions, result.trace)


__all__ = [
    "INSTRUCTIONS", "PLANNER_OUTPUT_SCHEMA", "PROMPT_VERSION", "PlannerAgent",
    "PlannerDecisionError", "PlanningDecision", "STRICT_TASK_SCHEMA",
]
