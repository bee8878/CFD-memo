"""LLM Case Writer that proposes a bounded OpenFOAM file-writing intent."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from cfd_memo_agent.models import ModelClient, ModelOutputError, ModelRequest
from cfd_memo_agent.validator import validate_task

PROMPT_VERSION = "case-writer-v1"

FILE_BINDINGS = {
    "inlet_velocity": "0/U",
    "pressure_boundaries": "0/p",
    "kinematic_viscosity": "constant/physicalProperties",
    "time_control": "system/controlDict",
    "mesh_geometry": "system/blockMeshDict",
    "geometry_metadata": "constant/geometry.json",
}

INSTRUCTIONS = """你是 CFD-Memo 的 Case Writer Agent。输入是已经通过确定性验证的 CFD task。
你的职责是确认当前 MVP 应使用的模板、求解器和参数到 OpenFOAM 文件的映射。

必须遵守：
1. 只允许 cylinder-2d、icoFoam 和给定 schema 中固定的文件映射。
2. 不得输出文件正文、路径外的文件、shell 命令或求解结果。
3. task 可安全映射时 status=ready、intent 非空、blockers 为空。
4. 发现无法安全表达的矛盾时 status=blocked、intent=null，并说明 blockers。
5. rationale 简要说明写入计划；warnings 只记录尚需 validator/runner 验证的事项。
6. 不得声称网格、求解或物理结果已经通过验证。
7. memory_context 只能用于增加受限防错检查；experience_ids 必须准确引用给定经验，
   preventive_files 只能列出经验 action 中已有的允许文件。
"""


def _fingerprint(task: dict[str, Any]) -> str:
    encoded = json.dumps(
        task, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_case_intent(task: dict[str, Any]) -> dict[str, Any]:
    """Build the only file-writing intent accepted by the current generator."""
    report = validate_task(task)
    if not report["task_valid"]:
        raise ValueError("Case Writer 只能接收已通过验证的 task")
    return {
        "template_id": "cylinder-2d",
        "solver": "icoFoam",
        "task_fingerprint": _fingerprint(task),
        "file_bindings": dict(FILE_BINDINGS),
    }


def validate_case_intent(task: dict[str, Any], intent: Any) -> None:
    """Reject stale, altered, or out-of-scope model writing plans."""
    expected = build_case_intent(task)
    if intent != expected:
        raise ValueError("Case Writer intent 与已验证 task 或允许的文件映射不一致")


def _memory_expectations(memory_context: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    ids = list(dict.fromkeys(item["experience_id"] for item in memory_context))
    files = sorted({
        path for item in memory_context for path in item["action"]["files"]
    })
    return ids, files


def case_writer_output_schema(
    task: dict[str, Any], memory_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected = build_case_intent(task)
    experience_ids, preventive_files = _memory_expectations(memory_context or [])
    bindings = {
        key: {"const": value} for key, value in expected["file_bindings"].items()
    }
    intent_schema = {
        "type": "object",
        "properties": {
            "template_id": {"const": expected["template_id"]},
            "solver": {"const": expected["solver"]},
            "task_fingerprint": {"const": expected["task_fingerprint"]},
            "file_bindings": {
                "type": "object", "properties": bindings,
                "required": list(bindings), "additionalProperties": False,
            },
        },
        "required": ["template_id", "solver", "task_fingerprint", "file_bindings"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ready", "blocked"]},
            "intent": {"anyOf": [intent_schema, {"type": "null"}]},
            "rationale": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "experience_ids": {
                "type": "array", "minItems": len(experience_ids),
                "maxItems": len(experience_ids), "uniqueItems": True,
                "items": {"enum": experience_ids} if experience_ids else {"type": "string"},
            },
            "preventive_files": {
                "type": "array", "minItems": len(preventive_files),
                "maxItems": len(preventive_files), "uniqueItems": True,
                "items": {"enum": preventive_files} if preventive_files else {"type": "string"},
            },
        },
        "required": ["status", "intent", "rationale", "warnings", "blockers",
                     "experience_ids", "preventive_files"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class CaseWriterDecision:
    status: str
    intent: dict[str, Any] | None
    rationale: list[str]
    warnings: list[str]
    blockers: list[str]
    experience_ids: list[str]
    preventive_files: list[str]
    trace: dict[str, Any]


class CaseWriterDecisionError(ModelOutputError):
    """A model answered, but its case-writing decision cannot be accepted."""

    def __init__(self, message: str, trace: dict[str, Any]):
        super().__init__(message)
        self.trace = trace


class CaseWriterAgent:
    """Ask a model for a bounded intent; never write files directly."""

    def __init__(self, client: ModelClient):
        self.client = client

    def prepare(
        self, task: dict[str, Any], *, memory_context: list[dict[str, Any]] | None = None,
    ) -> CaseWriterDecision:
        memories = memory_context or []
        expected_ids, expected_files = _memory_expectations(memories)
        schema = case_writer_output_schema(task, memories)
        result = self.client.complete_json(ModelRequest(
            role="case_writer",
            instructions=INSTRUCTIONS,
            input_text=json.dumps({"task": task, "memory_context": memories},
                                  ensure_ascii=False, indent=2, allow_nan=False),
            output_schema=schema,
            schema_name="cfd_memo_case_writing_decision",
            prompt_version=PROMPT_VERSION,
        ))
        output = result.output
        if set(output["experience_ids"]) != set(expected_ids):
            raise CaseWriterDecisionError(
                "Case Writer experience_ids 与注入经验不一致", result.trace,
            )
        if set(output["preventive_files"]) != set(expected_files):
            raise CaseWriterDecisionError(
                "Case Writer preventive_files 与经验允许文件不一致", result.trace,
            )
        if output["status"] == "blocked":
            if output["intent"] is not None or not output["blockers"]:
                raise CaseWriterDecisionError(
                    "Case Writer 阻止生成时 intent 必须为空且 blockers 不能为空", result.trace,
                )
        else:
            if output["intent"] is None or output["blockers"]:
                raise CaseWriterDecisionError(
                    "Case Writer 准备完成时必须提供 intent 且 blockers 为空", result.trace,
                )
            try:
                validate_case_intent(task, output["intent"])
            except ValueError as exc:
                raise CaseWriterDecisionError(str(exc), result.trace) from exc
        return CaseWriterDecision(
            output["status"], output["intent"], output["rationale"],
            output["warnings"], output["blockers"], output["experience_ids"],
            output["preventive_files"], result.trace,
        )


__all__ = [
    "FILE_BINDINGS", "INSTRUCTIONS", "PROMPT_VERSION", "CaseWriterAgent",
    "CaseWriterDecision", "CaseWriterDecisionError", "build_case_intent",
    "case_writer_output_schema", "validate_case_intent",
]
