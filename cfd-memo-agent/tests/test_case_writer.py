from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from cfd_memo_agent.case_writer import (
    CaseWriterAgent, build_case_intent, case_writer_output_schema,
    validate_case_intent,
)
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.models import FakeModelClient, ModelSettings
from cfd_memo_agent.orchestrator import prepare_case_writing
from cfd_memo_agent.planner import DEFAULT_CYLINDER_TASK


OPENAI = ModelSettings(provider="openai", model="test-model")


def output(task=None, **overrides):
    task = deepcopy(DEFAULT_CYLINDER_TASK) if task is None else task
    value = {
        "status": "ready",
        "intent": build_case_intent(task),
        "rationale": ["将 task 参数映射到固定 OpenFOAM 模板。"],
        "warnings": ["仍需执行确定性验证。"],
        "blockers": [],
    }
    value.update(overrides)
    return value


def test_case_writer_builds_task_bound_intent_and_valid_schema():
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    intent = build_case_intent(task)

    validate_case_intent(task, intent)
    Draft202012Validator.check_schema(case_writer_output_schema(task))
    assert intent["file_bindings"]["inlet_velocity"] == "0/U"
    changed = deepcopy(task)
    changed["physics"]["inlet_velocity"] = 2.0
    changed["physics"]["kinematic_viscosity"] = 0.02
    with pytest.raises(ValueError, match="不一致"):
        validate_case_intent(changed, intent)


def test_model_case_writer_returns_bounded_decision_and_trace():
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    client = FakeModelClient([output(task)])
    decision = CaseWriterAgent(client).prepare(task)

    assert decision.status == "ready"
    assert decision.trace["provider"] == "fake"
    assert client.requests[0].role == "case_writer"
    assert client.requests[0].prompt_version == "case-writer-v1"
    assert "shell" not in decision.intent["file_bindings"]


def test_case_writer_orchestrator_supports_rules_model_and_explicit_fallback():
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    rules = prepare_case_writing(task)
    model = prepare_case_writing(
        task, settings=OPENAI, client=FakeModelClient([output(task)]),
    )
    fallback = prepare_case_writing(
        task, settings=OPENAI, client=FakeModelClient([ValueError("bad output")]),
        fallback="rules",
    )

    assert rules["actual_mode"] == "rules"
    assert model["actual_mode"] == "openai" and model["status"] == "ready"
    assert fallback["actual_mode"] == "rules_fallback"
    assert fallback["fallback_used"] and fallback["fallback_reason"] == "bad output"


def test_generator_rejects_stale_intent_before_creating_run(tmp_path):
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    intent = build_case_intent(task)
    task["physics"]["inlet_velocity"] = 2.0
    task["physics"]["kinematic_viscosity"] = 0.02

    with pytest.raises(ValueError, match="不一致"):
        generate_case(task, runs_dir=tmp_path, intent=intent)
    assert list(tmp_path.iterdir()) == []
