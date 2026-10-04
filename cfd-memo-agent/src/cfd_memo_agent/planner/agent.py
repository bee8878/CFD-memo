"""LLM-backed planning for registered generated-case scenarios."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from cfd_memo_agent.models import ModelClient, ModelOutputError, ModelRequest
from cfd_memo_agent.tutorials import build_case_spec_proposal
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.validator.task import schema_validator

from . import DEFAULT_CAVITY_TASK, DEFAULT_CYLINDER_TASK

PROMPT_VERSION = "planner-v2"

INSTRUCTIONS = """你是 CFD-Memo 的 Planner Agent。你的任务是把用户需求整理为当前已注册能力：
二维圆柱绕流或二维顶盖驱动方腔。两者只支持 icoFoam、不可压缩层流和 manual-template 网格。

必须遵守：
1. 若需求属于支持范围且没有互相冲突的关键参数，status=ready，并返回完整 task。
2. 缺少的普通参数可以使用 schema 中体现的默认任务值，并在 assumptions 中逐项说明。
3. 若需求超出支持范围、关键参数冲突，或无法可靠确定物理意图，status=needs_clarification，
   task=null，并在 questions 中给出简短、具体的问题。
4. 满足 Re=U*D/nu；不得捏造求解结果，不得输出 Markdown，不得添加 schema 外字段。
5. task_id 使用 task-<case_type>-re<雷诺数>；小数点用 p 替代。
6. memory_context 是已通过适用性筛选的历史配置经验。它只能用于补充防错假设，不能覆盖
   用户明确参数或声称物理结果正确；experience_ids 必须准确列出给定经验编号。
7. tutorial_context 只包含本机官方教程索引证据。已注册场景必须优先返回 ready task；
   未注册场景若有候选，只能返回 reference_proposal 和给定的非执行提案，不能编造 task。
8. tutorial_ids 只能引用 tutorial_context 中存在的编号；教程相似不代表 case 可直接运行。
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
STRICT_CAVITY_TASK_SCHEMA = _strict_schema(schema_validator().schema, DEFAULT_CAVITY_TASK)
CASE_SPEC_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"const": 1},
        "proposal_status": {"const": "reference_only"},
        "requested_description": {"type": "string"},
        "source_type": {"const": "tutorial_reference"},
        "case_type": {"const": "unregistered"},
        "solver": {"type": "string"},
        "physics_model": {"type": "string"},
        "dimension": {"const": "unknown"},
        "mesh_tools": {"type": "array", "items": {"type": "string"}},
        "fields": {"type": "array", "items": {"type": "string"}},
        "boundaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "types": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "types"],
                "additionalProperties": False,
            },
        },
        "tutorial_ids": {"type": "array", "items": {"type": "string"}},
        "executable": {"const": False},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "schema_version", "proposal_status", "requested_description", "source_type",
        "case_type", "solver", "physics_model", "dimension", "mesh_tools", "fields",
        "boundaries", "tutorial_ids", "executable", "limitations",
    ],
    "additionalProperties": False,
}
PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": [
            "ready", "needs_clarification", "reference_proposal",
        ]},
        "task": {"anyOf": [STRICT_TASK_SCHEMA, STRICT_CAVITY_TASK_SCHEMA,
                            {"type": "null"}]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "experience_ids": {"type": "array", "items": {"type": "string"}},
        "tutorial_ids": {"type": "array", "items": {"type": "string"}},
        "case_spec_proposal": {"anyOf": [CASE_SPEC_PROPOSAL_SCHEMA, {"type": "null"}]},
    },
    "required": ["status", "task", "assumptions", "questions", "experience_ids",
                 "tutorial_ids", "case_spec_proposal"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PlanningDecision:
    status: str
    task: dict[str, Any] | None
    assumptions: list[str]
    questions: list[str]
    experience_ids: list[str]
    tutorial_ids: list[str]
    case_spec_proposal: dict[str, Any] | None
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

    def plan(
        self, description: str, *, memory_context: list[dict[str, Any]] | None = None,
        tutorial_context: dict[str, Any] | None = None,
    ) -> PlanningDecision:
        normalized = description.strip()
        if not normalized:
            raise ValueError("Task description cannot be empty.")
        memories = memory_context or []
        tutorials = tutorial_context or {
            "status": "not_needed", "query": normalized, "index_path": None,
            "candidate_count": 0, "candidates": [], "error": None,
        }
        candidate_ids = [item["tutorial_id"] for item in tutorials.get("candidates", [])]
        expected_proposal = (
            build_case_spec_proposal(normalized, tutorials) if candidate_ids else None
        )
        expected_ids = list(dict.fromkeys(item["experience_id"] for item in memories))
        output_schema = deepcopy(PLANNER_OUTPUT_SCHEMA)
        ids_schema = output_schema["properties"]["experience_ids"]
        ids_schema.update({"minItems": len(expected_ids), "maxItems": len(expected_ids),
                           "uniqueItems": True})
        if expected_ids:
            ids_schema["items"] = {"enum": expected_ids}
        tutorial_schema = output_schema["properties"]["tutorial_ids"]
        tutorial_schema.update({"maxItems": len(candidate_ids), "uniqueItems": True})
        if candidate_ids:
            tutorial_schema["items"] = {"enum": candidate_ids}
            output_schema["properties"]["case_spec_proposal"] = {
                "anyOf": [{"const": expected_proposal}, {"type": "null"}],
            }
        request = ModelRequest(
            role="planner",
            instructions=INSTRUCTIONS,
            input_text=json.dumps({"description": normalized, "memory_context": memories,
                                   "tutorial_context": tutorials},
                                  ensure_ascii=False, indent=2, allow_nan=False),
            output_schema=output_schema,
            schema_name="cfd_memo_planning_decision",
            prompt_version=PROMPT_VERSION,
        )
        result = self.client.complete_json(request)
        output = result.output
        status = output["status"]
        task = output["task"]
        assumptions = output["assumptions"]
        questions = output["questions"]
        experience_ids = output["experience_ids"]
        tutorial_ids = output["tutorial_ids"]
        proposal = output["case_spec_proposal"]
        if set(experience_ids) != set(expected_ids):
            raise PlannerDecisionError("Planner experience_ids 与注入经验不一致", result.trace)
        if not set(tutorial_ids) <= set(candidate_ids):
            raise PlannerDecisionError("Planner tutorial_ids 包含未检索到的教程", result.trace)
        if status == "needs_clarification":
            if task is not None or proposal is not None or not questions:
                raise PlannerDecisionError(
                    "需要澄清时 task/提案必须为空，并至少提供一个问题", result.trace)
        elif status == "reference_proposal":
            if task is not None or questions or proposal != expected_proposal or not tutorial_ids:
                raise PlannerDecisionError(
                    "教程提案必须为空 task、准确引用候选并使用受限提案", result.trace)
        else:
            if task is None:
                raise PlannerDecisionError("规划完成时必须返回 task", result.trace)
            if questions:
                raise PlannerDecisionError("规划完成时 questions 必须为空", result.trace)
            if proposal is not None:
                raise PlannerDecisionError("已注册任务不能同时返回教程提案", result.trace)
            report = validate_task(task)
            if not report["task_valid"]:
                details = "；".join(
                    f"{item['location']}: {item['message']}" for item in report["errors"]
                )
                raise PlannerDecisionError(
                    f"模型任务未通过 CFD 规则检查：{details}", result.trace)
        return PlanningDecision(
            status, task, assumptions, questions, experience_ids,
            tutorial_ids, proposal, result.trace,
        )


__all__ = [
    "CASE_SPEC_PROPOSAL_SCHEMA", "INSTRUCTIONS", "PLANNER_OUTPUT_SCHEMA",
    "PROMPT_VERSION", "PlannerAgent",
    "PlannerDecisionError", "PlanningDecision", "STRICT_CAVITY_TASK_SCHEMA",
    "STRICT_TASK_SCHEMA",
]
