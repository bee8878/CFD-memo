import json
from pathlib import Path

from cfd_memo_agent.case_adapters import get_case_adapter, list_case_adapters
from cfd_memo_agent.case_writer import build_case_intent
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.planner import plan_task
from cfd_memo_agent.validator import validate_case, validate_task


PROJECT = Path(__file__).resolve().parents[1]


def cavity_task():
    return json.loads((PROJECT / "examples/task.cavity-2d.json").read_text(encoding="utf-8"))


def test_cavity_is_a_registered_generated_case_capability():
    task = cavity_task()
    adapter = get_case_adapter(task)
    assert adapter.adapter_id == "cavity-2d-laminar-v1"
    assert {item["case_type"] for item in list_case_adapters()} == {
        "cylinder-2d", "cavity-2d", "backward-step-2d",
    }
    assert validate_task(task)["task_valid"]
    assert build_case_intent(task)["template_id"] == "cavity-2d"
    assert build_case_intent(task)["file_bindings"]["lid_velocity"] == "0/U"


def test_rule_planner_builds_cavity_task_and_reynolds_relation():
    task = plan_task("做 Re=200、顶盖速度=2、L=1 的二维顶盖驱动方腔")
    assert task["case_type"] == "cavity-2d"
    assert task["physics"]["lid_velocity"] == 2
    assert task["physics"]["kinematic_viscosity"] == 0.01
    assert validate_task(task)["task_valid"]


def test_cavity_generation_and_static_validation(tmp_path):
    task = cavity_task()
    result = generate_case(task, runs_dir=tmp_path)
    run, case = Path(result["run_path"]), Path(result["case_path"])
    report = validate_case(task, case)
    assert report["task_valid"] and report["config_valid"]
    assert report["runtime_blockers"][0]["code"] == "MESH_NOT_VERIFIED"
    assert "value uniform (1 0 0);" in (case / "0/U").read_text(encoding="utf-8")
    assert "(20 20 1)" in (case / "system/blockMeshDict").read_text(encoding="utf-8")
    assert json.loads((run / "case-spec.json").read_text())["case_type"] == "cavity-2d"


def test_cavity_validation_finds_task_and_case_mismatch(tmp_path):
    task = cavity_task()
    broken = json.loads(json.dumps(task))
    broken["physics"]["kinematic_viscosity"] = 0.02
    assert {item["code"] for item in validate_task(broken)["errors"]} == {"REYNOLDS_MISMATCH"}

    case = Path(generate_case(task, runs_dir=tmp_path)["case_path"])
    velocity = case / "0/U"
    velocity.write_text(velocity.read_text().replace("(1 0 0)", "(2 0 0)"))
    report = validate_case(task, case)
    assert not report["config_valid"]
    assert any(item["location"] == "0/U" for item in report["errors"])
