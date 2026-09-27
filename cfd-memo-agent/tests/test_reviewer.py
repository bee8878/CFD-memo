import pytest
from jsonschema import Draft202012Validator

from cfd_memo_agent.models import FakeModelClient, ModelOutputError, ModelSettings
from cfd_memo_agent.orchestrator import review_evidence
from cfd_memo_agent.reviewer import ReviewerAgent, build_rule_review, reviewer_output_schema


OPENAI = ModelSettings(provider="openai", model="test-model")


def evidence(*, status="failed", findings=None):
    return {
        "round_index": 0, "runner_mode": "simulated", "outcome_status": status,
        "config_valid": status == "simulated_success",
        "task_summary": {
            "task_id": "task-cylinder-2d-re100", "case_type": "cylinder-2d",
            "solver": "icoFoam",
        },
        "findings": findings or [], "runtime_blockers": [],
        "physical_validated": False, "memory_context": [],
    }


def output(*, decision="accept", codes=None, scope="none", experiences=None):
    return {
        "status": "reviewed", "decision": decision,
        "finding_codes": codes or [], "experience_ids": experiences or [],
        "repair_scope": scope,
        "root_cause": "基于提供的结构化证据。",
        "recommendation": "交由确定性工具处理并复验。",
        "applicability_conditions": ["仅适用于当前 task。"],
        "limitations": ["不代表物理结果已验证。"],
    }


def boundary_finding(action="rule_candidate"):
    return {
        "code": "BOUNDARY_NAMES", "message": "缺少 outlet", "location": "0/U",
        "evidence": "outlet missing", "action": action,
        "suggestion": "从可信配置恢复。",
    }


def test_rule_reviewer_accepts_repairs_or_stops_from_evidence():
    accepted = build_rule_review(evidence(status="simulated_success"))
    repaired = build_rule_review(evidence(findings=[boundary_finding()]))
    stopped = build_rule_review(evidence(findings=[boundary_finding("manual_required")]))

    assert accepted["decision"] == "accept" and accepted["repair_scope"] == "none"
    assert repaired["decision"] == "repair"
    assert repaired["repair_scope"] == "boundary_fields"
    assert stopped["decision"] == "stop" and stopped["repair_scope"] == "none"


def test_model_reviewer_uses_structured_evidence_and_trace():
    item = evidence(status="simulated_success")
    client = FakeModelClient([output()])
    decision = ReviewerAgent(client).review(item)

    Draft202012Validator.check_schema(reviewer_output_schema())
    assert decision.decision == "accept"
    assert client.requests[0].role == "reviewer"
    assert client.requests[0].prompt_version == "reviewer-v1"
    assert decision.trace["provider"] == "fake"


def test_model_reviewer_cannot_accept_a_repairable_failure():
    item = evidence(findings=[boundary_finding()])
    client = FakeModelClient([output(codes=["BOUNDARY_NAMES"])])
    with pytest.raises(ModelOutputError, match="repair"):
        ReviewerAgent(client).review(item)
    assert client.requests[0].output_schema["properties"]["decision"] == {
        "const": "repair",
    }


def test_reviewer_orchestrator_records_model_and_explicit_rules_fallback():
    item = evidence(status="simulated_success")
    model = review_evidence(item, settings=OPENAI, client=FakeModelClient([output()]))
    fallback = review_evidence(
        item, settings=OPENAI, client=FakeModelClient([ValueError("bad review")]),
        fallback="rules",
    )

    assert model["actual_mode"] == "openai" and model["decision"] == "accept"
    assert fallback["actual_mode"] == "rules_fallback"
    assert fallback["fallback_used"] and fallback["decision"] == "accept"
