"""LLM Reviewer constrained by deterministic validation and runner evidence."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from cfd_memo_agent.models import ModelClient, ModelOutputError, ModelRequest

PROMPT_VERSION = "reviewer-v1"
DECISIONS = ("accept", "repair", "stop")
REPAIR_SCOPES = ("none", "boundary_fields", "transport", "boundary_and_transport")

INSTRUCTIONS = """你是 CFD-Memo 的 Reviewer Agent。输入只包含 task 摘要以及 validator/runner
产生的结构化证据。你负责解释证据并给出 accept、repair 或 stop 决策。

必须遵守：
1. 没有错误且本轮执行完成时才能 accept；accept 不等于物理结果已验证。
2. 只有所有 finding 都标记为 rule_candidate 时才能 repair，并准确返回全部 finding_codes。
3. 未知错误、超时、环境问题、人工处理项或证据不足必须 stop。
4. repair_scope 只能描述边界字段或运动黏度；不得提出任意文件、命令或新的物理参数。
5. 建议只是候选，真正修改必须由确定性修正器执行并重新验证。
6. 不得捏造日志、网格、收敛或物理结论，不得输出 Markdown 或 schema 外字段。
7. decision 以 findings 和 outcome_status 为准；显式模拟模式中的 runtime_blockers 只写入
   limitations，不得用 MESH_NOT_VERIFIED 否决当前配置修复或模拟流程。
8. memory_context 只包含已验证的结构化经验；仅在 repair 时引用与当前 finding 匹配的
   experience_id。历史经验不能覆盖本轮确定性证据。
"""


def reviewer_output_schema(evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "status": {"const": "reviewed"},
            "decision": {"type": "string", "enum": list(DECISIONS)},
            "finding_codes": {"type": "array", "items": {"type": "string"}},
            "experience_ids": {"type": "array", "items": {"type": "string"}},
            "repair_scope": {"type": "string", "enum": list(REPAIR_SCOPES)},
            "root_cause": {"type": "string"},
            "recommendation": {"type": "string"},
            "applicability_conditions": {
                "type": "array", "items": {"type": "string"},
            },
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "status", "decision", "finding_codes", "experience_ids", "repair_scope", "root_cause",
            "recommendation", "applicability_conditions", "limitations",
        ],
        "additionalProperties": False,
    }
    if evidence is not None:
        decision = _expected_decision(evidence)
        scope = _repair_scope(evidence["findings"]) if decision == "repair" else "none"
        schema["properties"]["decision"] = {"const": decision}
        schema["properties"]["repair_scope"] = {"const": scope}
        codes = list(dict.fromkeys(item["code"] for item in evidence["findings"]))
        schema["properties"]["finding_codes"].update({
            "minItems": len(codes), "maxItems": len(codes), "uniqueItems": True,
        })
        if codes:
            schema["properties"]["finding_codes"]["items"] = {"enum": codes}
        experience_ids = _expected_experience_ids(evidence)
        schema["properties"]["experience_ids"].update({
            "minItems": len(experience_ids), "maxItems": len(experience_ids),
            "uniqueItems": True,
        })
        if experience_ids:
            schema["properties"]["experience_ids"]["items"] = {
                "enum": experience_ids,
            }
    return schema


def _expected_decision(evidence: dict[str, Any]) -> str:
    findings = evidence["findings"]
    if evidence["outcome_status"] in {"simulated_success", "completed"} and not findings:
        return "accept"
    if findings and all(item.get("action") == "rule_candidate" for item in findings):
        return "repair"
    return "stop"


def _repair_scope(findings: list[dict[str, Any]]) -> str:
    boundary = any(
        item.get("code") == "MISSING_BOUNDARY"
        or str(item.get("location", "")).startswith(("0/U", "0/p"))
        for item in findings
    )
    transport = any(
        item.get("code") == "BAD_TRANSPORT"
        or str(item.get("location", "")).startswith("constant/physicalProperties")
        for item in findings
    )
    if boundary and transport:
        return "boundary_and_transport"
    if boundary:
        return "boundary_fields"
    if transport:
        return "transport"
    return "none"


def _expected_experience_ids(evidence: dict[str, Any]) -> list[str]:
    if _expected_decision(evidence) != "repair":
        return []
    return list(dict.fromkeys(
        item["experience_id"] for item in evidence.get("memory_context", [])
    ))


def build_rule_review(evidence: dict[str, Any]) -> dict[str, Any]:
    """Create the deterministic Reviewer decision used offline and as a guardrail."""
    findings = evidence["findings"]
    decision = _expected_decision(evidence)
    scope = _repair_scope(findings) if decision == "repair" else "none"
    codes = list(dict.fromkeys(item["code"] for item in findings))
    if decision == "accept":
        cause = "本轮没有错误证据，所选执行模式已完成。"
        recommendation = "接受本轮工程结果；物理准确性仍由独立验收判断。"
    elif decision == "repair":
        cause = "；".join(item["message"] for item in findings)
        recommendation = "仅按可信 reference 恢复受支持字段，随后重新验证和执行。"
    else:
        cause = "；".join(item["message"] for item in findings) or "证据不足或执行未完成。"
        recommendation = "停止自动修改，保留证据并人工核对。"
    return {
        "status": "reviewed", "decision": decision, "finding_codes": codes,
        "experience_ids": _expected_experience_ids(evidence),
        "repair_scope": scope, "root_cause": cause,
        "recommendation": recommendation,
        "applicability_conditions": ["仅适用于当前已验证 task 和本轮证据。"],
        "limitations": ["Reviewer 决策不代表物理结果已经验证。"],
    }


def _validate_review(evidence: dict[str, Any], output: dict[str, Any]) -> None:
    expected_codes = list(dict.fromkeys(item["code"] for item in evidence["findings"]))
    if len(output["finding_codes"]) != len(set(output["finding_codes"])):
        raise ValueError("Reviewer finding_codes 不得重复")
    if set(output["finding_codes"]) != set(expected_codes):
        raise ValueError("Reviewer finding_codes 与确定性证据不一致")
    expected_experiences = _expected_experience_ids(evidence)
    if set(output["experience_ids"]) != set(expected_experiences):
        raise ValueError("Reviewer experience_ids 与检索到的适用经验不一致")
    expected_decision = _expected_decision(evidence)
    if output["decision"] != expected_decision:
        raise ValueError(f"Reviewer 决策与证据不一致，应为 {expected_decision}")
    expected_scope = _repair_scope(evidence["findings"]) if expected_decision == "repair" else "none"
    if output["repair_scope"] != expected_scope:
        raise ValueError(f"Reviewer repair_scope 与证据不一致，应为 {expected_scope}")


@dataclass(frozen=True)
class ReviewerDecision:
    status: str
    decision: str
    finding_codes: list[str]
    experience_ids: list[str]
    repair_scope: str
    root_cause: str
    recommendation: str
    applicability_conditions: list[str]
    limitations: list[str]
    trace: dict[str, Any]


class ReviewerDecisionError(ModelOutputError):
    """A model review was returned but contradicted deterministic evidence."""

    def __init__(self, message: str, trace: dict[str, Any]):
        super().__init__(message)
        self.trace = trace


class ReviewerAgent:
    """Review structured evidence without reading or mutating case files."""

    def __init__(self, client: ModelClient):
        self.client = client

    def review(self, evidence: dict[str, Any]) -> ReviewerDecision:
        result = self.client.complete_json(ModelRequest(
            role="reviewer", instructions=INSTRUCTIONS,
            input_text=json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False),
            output_schema=reviewer_output_schema(evidence),
            schema_name="cfd_memo_review_decision",
            prompt_version=PROMPT_VERSION,
        ))
        try:
            _validate_review(evidence, result.output)
        except ValueError as exc:
            raise ReviewerDecisionError(str(exc), result.trace) from exc
        output = result.output
        return ReviewerDecision(
            output["status"], output["decision"], output["finding_codes"],
            output["experience_ids"],
            output["repair_scope"], output["root_cause"], output["recommendation"],
            output["applicability_conditions"], output["limitations"], result.trace,
        )


__all__ = [
    "DECISIONS", "INSTRUCTIONS", "PROMPT_VERSION", "REPAIR_SCOPES",
    "ReviewerAgent", "ReviewerDecision", "ReviewerDecisionError",
    "build_rule_review", "reviewer_output_schema",
]
