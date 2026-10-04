from copy import deepcopy

from jsonschema import Draft202012Validator

from cfd_memo_agent.models import FakeModelClient, ModelInvocationError, ModelSettings
from cfd_memo_agent.orchestrator import plan_description
from cfd_memo_agent.case_writer import build_case_intent
from cfd_memo_agent.planner import DEFAULT_CYLINDER_TASK
from cfd_memo_agent.planner.agent import PLANNER_OUTPUT_SCHEMA
from cfd_memo_agent.tutorials import build_case_spec_proposal
from cfd_memo_agent.memory.episodes import episode_validator
from cfd_memo_agent.workflow import run_workflow


OPENAI = ModelSettings(provider="openai", model="test-model")
DEEPSEEK = ModelSettings(provider="deepseek", model="deepseek-flash")


def output(*, task=None, status="ready", assumptions=None, questions=None,
           tutorial_ids=None, proposal=None):
    return {
        "status": status,
        "task": deepcopy(DEFAULT_CYLINDER_TASK) if task is None and status == "ready" else task,
        "assumptions": assumptions or [],
        "questions": questions or [],
        "experience_ids": [],
        "tutorial_ids": tutorial_ids or [],
        "case_spec_proposal": proposal,
    }


def writer_output(task=None):
    task = deepcopy(DEFAULT_CYLINDER_TASK) if task is None else task
    return {
        "status": "ready", "intent": build_case_intent(task),
        "rationale": ["使用固定模板映射。"],
        "warnings": ["仍需验证和运行。"], "blockers": [],
        "experience_ids": [], "preventive_files": [],
    }


def reviewer_output(*, decision="accept", codes=None, scope="none", experiences=None):
    return {
        "status": "reviewed", "decision": decision, "finding_codes": codes or [],
        "experience_ids": experiences or [],
        "repair_scope": scope, "root_cause": "基于本轮结构化证据。",
        "recommendation": "仅由确定性工具修改并重新验证。",
        "applicability_conditions": ["仅适用于当前轮次。"],
        "limitations": ["物理结果尚未由本次审查验证。"],
    }


def test_rules_mode_preserves_the_existing_planner():
    state = plan_description("做 Re=200 的二维圆柱绕流，U=2，D=1")

    assert state["status"] == "ready"
    assert state["task"]["physics"]["reynolds_number"] == 200
    assert state["planning"]["actual_mode"] == "rules"
    assert state["planning"]["fallback_used"] is False


def test_model_planner_returns_validated_task_and_trace():
    client = FakeModelClient([output(assumptions=["使用默认计算域"])])
    state = plan_description("做二维圆柱绕流", settings=OPENAI, client=client)

    assert state["status"] == "ready"
    assert state["task"]["solver"] == "icoFoam"
    assert state["assumptions"] == ["使用默认计算域"]
    assert state["planning"]["actual_mode"] == "openai"
    assert state["planning"]["trace"]["provider"] == "fake"
    assert client.requests[0].role == "planner"
    Draft202012Validator.check_schema(client.requests[0].output_schema)


def test_orchestrator_records_deepseek_as_the_actual_provider():
    state = plan_description(
        "做二维圆柱绕流", settings=DEEPSEEK,
        client=FakeModelClient([output()]),
    )

    assert state["status"] == "ready"
    assert state["planning"]["requested_provider"] == "deepseek"
    assert state["planning"]["actual_mode"] == "deepseek"
    assert state["planning"]["prompt_version"] == "planner-v2"


def test_model_can_request_clarification_without_creating_task():
    client = FakeModelClient([output(
        status="needs_clarification", task=None,
        questions=["请确认要使用圆柱绕流还是管道流。"],
    )])
    state = plan_description("帮我算一下", settings=OPENAI, client=client)

    assert state["status"] == "needs_clarification"
    assert state["task"] is None
    assert state["questions"]


def test_invalid_model_task_stops_before_generation():
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    task["physics"]["kinematic_viscosity"] = 0.02
    state = plan_description(
        "做圆柱绕流", settings=OPENAI, client=FakeModelClient([output(task=task)]),
    )

    assert state["status"] == "failed"
    assert state["task"] is None
    assert state["error"]["type"] == "PlannerDecisionError"
    assert "CFD 规则检查" in state["error"]["message"]
    assert state["planning"]["actual_mode"] == "openai"
    assert state["planning"]["trace"]["provider"] == "fake"


def test_model_failure_stops_by_default_or_uses_explicit_rules_fallback():
    failure = ModelInvocationError("network unavailable")
    stopped = plan_description(
        "cylinder flow Re=100", settings=OPENAI,
        client=FakeModelClient([failure]),
    )
    assert stopped["status"] == "failed"
    assert stopped["planning"]["actual_mode"] is None
    assert stopped["planning"]["fallback_used"] is False

    recovered = plan_description(
        "cylinder flow Re=100", settings=OPENAI,
        client=FakeModelClient([ModelInvocationError("network unavailable")]),
        fallback="rules",
    )
    assert recovered["status"] == "ready"
    assert recovered["planning"]["actual_mode"] == "rules_fallback"
    assert recovered["planning"]["fallback_used"] is True
    assert recovered["planning"]["fallback_reason"] == "network unavailable"
    assert recovered["error"] is None


def test_planner_schema_requires_every_object_property_for_strict_output():
    def check(schema):
        if schema.get("type") == "object":
            assert set(schema["required"]) == set(schema["properties"])
            assert schema["additionalProperties"] is False
            for child in schema["properties"].values():
                check(child)
        for branch in schema.get("anyOf", []):
            check(branch)
        if isinstance(schema.get("items"), dict):
            check(schema["items"])

    check(PLANNER_OUTPUT_SCHEMA)


def test_planner_schema_locks_currently_supported_template_choices():
    task_schema = PLANNER_OUTPUT_SCHEMA["properties"]["task"]["anyOf"][0]
    properties = task_schema["properties"]

    assert properties["solver"] == {"const": "icoFoam"}
    assert properties["case_type"] == {"const": "cylinder-2d"}
    assert properties["mesh"]["properties"]["generator"] == {
        "const": "manual-template",
    }


def tutorial_context():
    return {
        "status": "ready", "query": "做二维后台阶流动", "index_path": "index.json",
        "candidate_count": 1, "error": None,
        "candidates": [{
            "tutorial_id": "incompressible/simpleFoam/RAS/pitzDaily",
            "case_path": "/opt/openfoam10/tutorials/incompressible/simpleFoam/RAS/pitzDaily",
            "solver": "simpleFoam", "physics_model": "incompressible_rans",
            "fields": ["U", "p"],
            "boundaries": {"inlet": ["fixedValue"], "outlet": ["zeroGradient"]},
            "boundary_types": ["fixedValue", "zeroGradient"],
            "mesh_tools": ["blockMesh"],
            "config_files": ["system/controlDict", "system/blockMeshDict"],
            "warnings": [], "score": 20, "match_reasons": ["alias:后台阶"],
        }],
    }


def test_rules_planner_creates_non_executable_tutorial_proposal():
    context = tutorial_context()
    state = plan_description("做二维后台阶流动", tutorial_context=context)

    assert state["status"] == "reference_proposal"
    assert state["task"] is None
    assert state["tutorial_ids"] == [context["candidates"][0]["tutorial_id"]]
    assert state["case_spec_proposal"]["executable"] is False
    assert state["case_spec_proposal"]["solver"] == "simpleFoam"


def test_model_tutorial_proposal_is_bounded_and_cannot_forge_source():
    context = tutorial_context()
    proposal = build_case_spec_proposal("做二维后台阶流动", context)
    accepted = plan_description(
        "做二维后台阶流动", settings=OPENAI, tutorial_context=context,
        client=FakeModelClient([output(
            status="reference_proposal", task=None,
            tutorial_ids=proposal["tutorial_ids"], proposal=proposal,
        )]),
    )
    assert accepted["status"] == "reference_proposal"

    forged = deepcopy(proposal)
    forged["tutorial_ids"] = ["invented/tutorial"]
    stopped = plan_description(
        "做二维后台阶流动", settings=OPENAI, tutorial_context=context,
        client=FakeModelClient([output(
            status="reference_proposal", task=None,
            tutorial_ids=["invented/tutorial"], proposal=forged,
        )]),
    )
    assert stopped["status"] == "failed"
    assert stopped["task"] is None


def test_workflow_saves_tutorial_proposal_without_generating_case(tmp_path, monkeypatch):
    context = tutorial_context()
    monkeypatch.setattr(
        "cfd_memo_agent.orchestrator.TutorialRetriever.retrieve",
        lambda self, query: context,
    )
    result = run_workflow(
        "做二维后台阶流动", mode="simulated", runs_dir=tmp_path,
        model_settings=ModelSettings(provider="rules", model=None),
    )

    root = tmp_path / result["episode_id"]
    assert result["status"] == "proposal_ready"
    assert result["stop_reason"]["code"] == "REFERENCE_PROPOSAL_READY"
    assert result["rounds"] == [] and result["task"] is None
    assert (root / "case-spec-proposal.json").is_file()
    assert not (root / "reference").exists()
    report = (root / "report.md").read_text(encoding="utf-8")
    assert "执行：未进入" in report
    assert "没有生成 case 或运行求解器" in report
    episode_validator().validate(result)


def test_workflow_records_model_planning_without_exposing_credentials(tmp_path):
    result = run_workflow(
        "做二维圆柱绕流", mode="simulated", runs_dir=tmp_path,
        model_settings=DEEPSEEK,
        model_client=FakeModelClient([
            output(assumptions=["使用默认 Re=100"]), writer_output(), reviewer_output(),
        ]),
    )

    assert result["status"] == "simulated_success"
    assert result["planning"]["actual_mode"] == "deepseek"
    assert result["planning"]["assumptions"] == ["使用默认 Re=100"]
    assert result["planning"]["trace"]["provider"] == "fake"
    assert result["case_writing"]["actual_mode"] == "deepseek"
    assert result["case_writing"]["trace"]["provider"] == "fake"
    assert result["reviews"][0]["actual_mode"] == "deepseek"
    assert result["reviews"][0]["decision"] == "accept"
    assert result["reviews"][0]["trace"]["provider"] == "fake"
    assert (tmp_path / result["episode_id"] / "case-writing.json").is_file()
    assert (tmp_path / result["episode_id"] / "rounds/round-000/review.json").is_file()
    episode_validator().validate(result)
    assert "test-secret" not in str(result)


def test_workflow_stops_for_clarification_before_case_generation(tmp_path):
    result = run_workflow(
        "帮我计算", mode="simulated", runs_dir=tmp_path,
        model_settings=OPENAI,
        model_client=FakeModelClient([output(
            status="needs_clarification", task=None,
            questions=["请说明是否为二维圆柱绕流。"],
        )]),
    )

    assert result["status"] == "failed"
    assert result["stop_reason"]["code"] == "CLARIFICATION_REQUIRED"
    assert result["rounds"] == [] and result["task"] is None
    assert not (tmp_path / result["episode_id"] / "reference").exists()
    episode_validator().validate(result)


def test_three_model_agents_repair_controlled_failure_and_rerun(tmp_path):
    client = FakeModelClient([
        output(), writer_output(),
        reviewer_output(
            decision="repair", codes=["BOUNDARY_NAMES"], scope="boundary_fields",
        ),
        reviewer_output(),
    ])
    result = run_workflow(
        "做二维圆柱绕流", mode="simulated", fault="missing-boundary",
        runs_dir=tmp_path, model_settings=DEEPSEEK, model_client=client,
    )

    assert result["status"] == "simulated_success"
    assert [request.role for request in client.requests] == [
        "planner", "case_writer", "reviewer", "reviewer",
    ]
    assert [review["decision"] for review in result["reviews"]] == ["repair", "accept"]
    assert [record["status"] for record in result["rounds"]] == [
        "validation_failed", "simulated_success",
    ]
    assert len(result["corrections"]) == 1
    episode_validator().validate(result)
