import json
from pathlib import Path

from cfd_memo_agent.case_adapters import get_case_adapter, list_case_adapters
from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.generator import case as generator_case
from cfd_memo_agent.planner import plan_task
from cfd_memo_agent.validator import validate_case, validate_task
from cfd_memo_agent.validator import case as validator_case


def test_cylinder_adapter_describes_current_generation_capability():
    task = plan_task("cylinder flow Re=100")
    adapter = get_case_adapter(task)
    status = list_case_adapters()

    assert adapter.adapter_id == "cylinder-2d-laminar-v1"
    assert adapter.to_case_spec(task) == CaseSpec.from_task(task)
    assert adapter.public_status() in status
    assert status[0]["case_type"] == "cylinder-2d"
    assert status[0]["solver"] == "icoFoam"


def test_task_support_is_reported_by_adapter_not_generator(tmp_path):
    task = plan_task("cylinder flow")
    task["solver"] = "pimpleFoam"
    report = validate_task(task)

    assert any(item["code"] == "UNSUPPORTED_SOLVER" for item in report["errors"])
    try:
        generate_case(task, runs_dir=tmp_path)
    except ValueError as exc:
        assert "icoFoam" in str(exc)
    else:
        raise AssertionError("unsupported adapter settings generated a case")
    assert list(tmp_path.iterdir()) == []


def test_generator_core_dispatches_through_adapter(monkeypatch, tmp_path):
    task = plan_task("cylinder flow")
    calls = []

    class FakeAdapter:
        required_files = ("fake",)

        def generate(self, actual, **options):
            calls.append((actual, options))
            return {"status": "fake-generated"}

    monkeypatch.setattr(generator_case, "get_case_adapter", lambda actual: FakeAdapter())
    result = generator_case.generate_case(task, runs_dir=tmp_path, intent={"safe": True})

    assert result == {"status": "fake-generated"}
    assert calls == [(task, {"runs_dir": tmp_path, "template_dir": None,
                             "intent": {"safe": True}})]


def test_validator_core_dispatches_through_adapter(monkeypatch, tmp_path):
    task = plan_task("cylinder flow")
    expected = {"task_valid": True, "config_valid": True, "adapter": "fake"}
    calls = []

    class FakeAdapter:
        def validate(self, actual, case_dir):
            calls.append((actual, case_dir))
            return expected

    monkeypatch.setattr(validator_case, "get_case_adapter", lambda actual: FakeAdapter())
    assert validator_case.validate_case(task, tmp_path) is expected
    assert calls == [(task, tmp_path)]


def test_generated_case_saves_case_spec_without_changing_validation(tmp_path):
    task = plan_task("cylinder flow Re=200 U=2 D=1")
    result = generate_case(task, runs_dir=tmp_path)
    run = Path(result["run_path"])
    saved = CaseSpec.from_dict(json.loads((run / "case-spec.json").read_text(encoding="utf-8")))

    assert saved == CaseSpec.from_task(task)
    assert result["case_spec_path"] == str(run / "case-spec.json")
    assert validate_case(task, run / "case")["config_valid"]
