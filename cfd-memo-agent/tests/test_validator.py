"""C3 checks use explicit tasks, then exercise C2 and CLI integration."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.validator import validate_case, validate_task
from cfd_memo_agent.validator.foam import parse_foam
from cfd_memo_agent.validator.task import SCHEMA_PATH

PROJECT = SCHEMA_PATH.parent.parent


@pytest.fixture
def task():
    return json.loads((PROJECT / "examples/task.cylinder-2d.json").read_text(encoding="utf-8"))


@pytest.fixture
def generated(task, tmp_path):
    return generate_case(task, runs_dir=tmp_path / "runs")


def codes(report):
    return {item["code"] for item in report["errors"]}


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_valid_task_and_generated_case_remain_unverified(task, generated):
    case = Path(generated["case_path"])
    original = deepcopy(task)
    before = snapshot(case)
    template = PROJECT / "cases/templates/cylinder-2d"
    source_before = snapshot(template)
    task_report = validate_task(task)
    report = validate_case(task, case)
    assert task_report["task_valid"] is True
    assert task_report["config_valid"] is False
    assert report["task_valid"] is True
    assert report["config_valid"] is True
    assert report["errors"] == []
    assert report["mesh_verified"] is False
    assert {item["code"] for item in report["runtime_blockers"]} == {"MESH_NOT_VERIFIED"}
    assert len(report["warnings"]) == 2
    assert task == original and snapshot(case) == before and snapshot(template) == source_before
    for collection in ("errors", "warnings", "runtime_blockers"):
        for item in report[collection]:
            assert set(item) == {"code", "message", "location"}


@pytest.mark.parametrize("section,key,value,code", [
    ("physics", "inlet_velocity", 0, "TASK_SCHEMA"),
    ("physics", "inlet_velocity", -1, "TASK_SCHEMA"),
    ("physics", "inlet_velocity", True, "TASK_SCHEMA"),
    ("physics", "inlet_velocity", "2", "TASK_SCHEMA"),
    ("physics", "reynolds_number", 0, "TASK_SCHEMA"),
    ("physics", "kinematic_viscosity", 0.02, "REYNOLDS_MISMATCH"),
    ("physics", "flow_model", "turbulent", "TASK_SCHEMA"),
    ("physics", "inlet_velocity", float("nan"), "NON_FINITE"),
    ("physics", "inlet_velocity", float("inf"), "NON_FINITE"),
    ("geometry", "cylinder_diameter", 10, "GEOMETRY_RANGE"),
    ("geometry", "dimension", "3D", "TASK_SCHEMA"),
    ("time_control", "start_time", -1, "TASK_SCHEMA"),
    ("time_control", "start_time", 10, "TIME_RANGE"),
    ("time_control", "delta_t", 0, "TASK_SCHEMA"),
    ("mesh", "generator", "snappyHexMesh", "UNSUPPORTED_MESH"),
])
def test_bad_task_is_reported_and_generation_leaves_no_output(task, tmp_path, section, key, value, code):
    task[section][key] = value
    report = validate_task(task)
    assert report["task_valid"] is False
    assert code in codes(report)
    with pytest.raises(ValueError):
        generate_case(task, runs_dir=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("path", [
    ("geometry", "domain_length"), ("geometry", "domain_height"),
    ("boundary_conditions", "inlet", "U"), ("boundary_conditions", "outlet", "p"),
    ("physics",), ("time_control",),
])
def test_missing_task_fields(task, path):
    parent = task
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    report = validate_task(task)
    assert "TASK_SCHEMA" in codes(report)


@pytest.mark.parametrize("root", [None, [], "task", 12])
def test_non_object_task_returns_report(root):
    assert validate_task(root)["task_valid"] is False


def test_schema_allows_future_solver_but_implementation_rejects_it(task):
    task["solver"] = "pimpleFoam"
    assert "UNSUPPORTED_SOLVER" in codes(validate_task(task))
    assert "TASK_SCHEMA" not in codes(validate_task(task))


def test_formula_accepts_existing_rounding_and_does_not_accept_large_error(task):
    task["physics"].update(reynolds_number=3, kinematic_viscosity=0.3333333333)
    assert validate_task(task)["task_valid"]
    task["physics"]["kinematic_viscosity"] = 0.33
    assert "REYNOLDS_MISMATCH" in codes(validate_task(task))


@pytest.mark.parametrize("relative,old,new,code", [
    ("0/U", "uniform (1 0 0)", "uniform (2 0 0)", "VALUE_MISMATCH"),
    ("0/U", "[0 1 -1 0 0 0 0]", "[0 2 -1 0 0 0 0]", "DIMENSIONS"),
    ("0/p", "[0 2 -2 0 0 0 0]", "[0 1 -2 0 0 0 0]", "DIMENSIONS"),
    ("0/p", "type            zeroGradient;", "type            fixedValue;", "BOUNDARY_TYPE"),
    ("0/U", "inlet", "wrongInlet", "BOUNDARY_NAMES"),
    ("constant/physicalProperties", "] 0.01;", "] 0.02;", "VALUE_MISMATCH"),
    ("constant/physicalProperties", "[0 2 -1 0 0 0 0]", "[0 1 -1 0 0 0 0]", "DIMENSIONS"),
    ("system/controlDict", "application    icoFoam;", "application    pimpleFoam;", "CONTROL_SETTING"),
    ("system/controlDict", "deltaT    0.005;", "deltaT    0.01;", "VALUE_MISMATCH"),
    ("system/controlDict", "startTime    0;", "startTime    1;", "VALUE_MISMATCH"),
    ("system/controlDict", "writeInterval    0.5;", "writeInterval    2;", "VALUE_MISMATCH"),
    ("system/blockMeshDict", "(-10 -5 -0.05)", "(-12 -5 -0.05)", "MESH_TOPOLOGY"),
    ("system/blockMeshDict", "inlet", "anotherInlet", "MESH_TOPOLOGY"),
    ("system/blockMeshDict", "type wall;", "type patch;", "MESH_TOPOLOGY"),
    ("system/blockMeshDict", "(36 36 1)", "(36 36 2)", "MESH_TOPOLOGY"),
    ("system/fvSolution", "tolerance       1e-06;", "tolerance       -1;", "SOLVER_TOLERANCE"),
    ("system/fvSchemes", "divSchemes", "wrongSchemes", "CONFIG_STRUCTURE"),
])
def test_case_mutations_are_located(task, generated, relative, old, new, code):
    case = Path(generated["case_path"])
    path = case / relative
    content = path.read_text()
    assert old in content
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    report = validate_case(task, case)
    assert report["task_valid"] is True and report["config_valid"] is False
    assert any(item["code"] == code and item["location"].startswith(relative) for item in report["errors"])


@pytest.mark.parametrize("relative", [
    "0/U", "0/p", "constant/physicalProperties", "system/controlDict",
    "system/blockMeshDict", "system/fvSchemes", "system/fvSolution", "constant/geometry.json",
])
def test_missing_files_are_reported(task, generated, relative):
    case = Path(generated["case_path"])
    (case / relative).unlink()
    report = validate_case(task, case)
    assert not report["config_valid"]
    assert any(item["location"] == relative for item in report["errors"])


@pytest.mark.parametrize("key,value", [
    ("cylinder_diameter", 2), ("domain_height", 11), ("domain_length", 21),
    ("units", "mm"), ("dimension", "3D"), ("cylinder_diameter", True),
])
def test_geometry_metadata_must_match_task(task, generated, key, value):
    case = Path(generated["case_path"])
    path = case / "constant/geometry.json"
    metadata = json.loads(path.read_text())
    metadata[key] = value
    path.write_text(json.dumps(metadata))
    assert not validate_case(task, case)["config_valid"]


@pytest.mark.parametrize("content", [
    "FoamFile {", "FoamFile { object U; object U; }",
    '#include "anotherFile"', "a $other;", "a (1 2];", "a 1",
    "/* unfinished", 'a "unfinished',
])
def test_parser_rejects_broken_and_unsupported_syntax(content):
    with pytest.raises(ValueError):
        parse_foam(content)


def test_formatting_comments_and_scientific_numbers_are_supported(task, generated):
    case = Path(generated["case_path"])
    path = case / "system/controlDict"
    text = path.read_text().replace("deltaT    0.005;", "deltaT \n 5e-3 ;")
    path.write_text("// deltaT 999;\n/* application wrong; */\n" + text)
    assert validate_case(task, case)["config_valid"]


def test_comment_cannot_replace_boundary(task, generated):
    case = Path(generated["case_path"])
    path = case / "0/U"
    text = path.read_text().replace("inlet", "fakeInlet", 1)
    path.write_text("// inlet { type fixedValue; value uniform (1 0 0); }\n" + text)
    assert "BOUNDARY_NAMES" in codes(validate_case(task, case))


def test_duplicate_boundary_and_config_syntax_are_reported(task, generated):
    case = Path(generated["case_path"])
    mesh = case / "system/blockMeshDict"
    mesh.write_text(mesh.read_text().replace("outlet", "inlet"))
    control = case / "system/controlDict"
    control.write_text(control.read_text() + "\ndeltaT 0.01;\n")
    report = validate_case(task, case)
    assert {"DUPLICATE_BOUNDARY", "FOAM_SYNTAX"} <= codes(report)


def test_unreadable_file_is_a_report_not_exception(task, generated, monkeypatch):
    case = Path(generated["case_path"])
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == case / "0/U":
            raise PermissionError("test denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert "FILE_READ" in codes(validate_case(task, case))


def run_cli(cwd, *args):
    return subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "validate", *map(str, args)],
        cwd=cwd, capture_output=True, encoding="utf-8",
    )


def test_cli_report_exit_codes_and_no_overwrite(generated, tmp_path):
    run = Path(generated["run_path"])
    output = tmp_path / "validation.json"
    completed = run_cli(tmp_path, "--run", run, "--output", output)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report == json.loads(output.read_text(encoding="utf-8"))
    assert report["config_valid"] and report["runtime_blockers"] and not report["mesh_verified"]
    before = output.read_bytes()
    again = run_cli(tmp_path, "--run", run, "--output", output)
    assert again.returncode == 2 and output.read_bytes() == before
    (run / "case/0/U").unlink()
    failed = run_cli(tmp_path, "--run", run)
    assert failed.returncode == 1
    assert not json.loads(failed.stdout)["config_valid"]
    assert run_cli(tmp_path).returncode == 2


@pytest.mark.parametrize("text", ['{broken', '{"a":1,"a":2}', '{"a": NaN}', '[]'])
def test_cli_invalid_task_file_returns_json_report(tmp_path, text):
    run = tmp_path / "bad-run"
    run.mkdir()
    (run / "task.json").write_text(text)
    result = run_cli(tmp_path, "--run", run)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["task_valid"] is False
    assert "Traceback" not in result.stderr


def test_cli_missing_run_is_validation_failure(tmp_path):
    result = run_cli(tmp_path, "--run", tmp_path / "missing")
    assert result.returncode == 1
    assert "TASK_READ" in codes(json.loads(result.stdout))
