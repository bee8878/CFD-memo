import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.generator.case import PROJECT_ROOT, REQUIRED_FILES
from cfd_memo_agent.planner import plan_task


def test_generate_case_writes_task_parameters_and_copies_files(tmp_path):
    task = plan_task("cylinder flow Re=200 U=2 D=3")
    task["geometry"].update(domain_length=30, domain_height=12)
    task["time_control"].update(start_time=1, end_time=20, delta_t=0.002, write_interval=1)
    result = generate_case(task, runs_dir=tmp_path)
    case = Path(result["case_path"])

    assert all((case / name).is_file() for name in REQUIRED_FILES)
    assert "value uniform (2 0 0);" in (case / "0/U").read_text()
    assert "nu    [0 2 -1 0 0 0 0] 0.03;" in (case / "constant/physicalProperties").read_text()
    controls = (case / "system/controlDict").read_text()
    for entry in ("application    icoFoam;", "startTime    1;", "endTime    20;",
                  "deltaT    0.002;", "writeInterval    1;"):
        assert entry in controls
    assert "(-15 -6 -0.05)" in (case / "system/blockMeshDict").read_text()
    geometry = json.loads((case / "constant/geometry.json").read_text())
    assert geometry["cylinder_diameter"] == 3
    assert geometry["mesh_verified"] is False
    assert result["warnings"]
    assert json.loads(Path(result["task_path"]).read_text(encoding="utf-8")) == task
    assert json.loads((Path(result["run_path"]) / "generation.json").read_text()) == result
    for name in ("0/p", "system/fvSchemes", "system/fvSolution"):
        assert (case / name).read_bytes() == (
            PROJECT_ROOT / "cases/templates/cylinder-2d" / name
        ).read_bytes()


def test_repeated_generation_preserves_template_task_and_previous_run(tmp_path):
    source = PROJECT_ROOT / "cases/templates/cylinder-2d"
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    task = plan_task("cylinder flow Re=100")
    original = deepcopy(task)
    first = generate_case(task, runs_dir=tmp_path)
    marker = Path(first["case_path"]) / "keep.txt"
    marker.write_text("previous run")
    second = generate_case(task, runs_dir=tmp_path)

    assert first["run_id"] != second["run_id"]
    assert marker.read_text() == "previous run"
    assert task == original
    assert before == {str(p.relative_to(source)): p.read_bytes()
                      for p in source.rglob("*") if p.is_file()}


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_velocity_creates_no_run(tmp_path, value):
    task = plan_task("cylinder flow")
    task["physics"]["inlet_velocity"] = value
    with pytest.raises(ValueError):
        generate_case(task, runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unsupported_solver_and_boundary_settings_are_not_silently_ignored(tmp_path):
    task = plan_task("cylinder flow")
    task["solver"] = "pimpleFoam"
    with pytest.raises(ValueError, match="icoFoam"):
        generate_case(task, runs_dir=tmp_path)
    task["solver"] = "icoFoam"
    task["boundary_conditions"]["inlet"]["U"] = "zeroGradient"
    with pytest.raises(ValueError, match="boundary"):
        generate_case(task, runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_missing_or_changed_template_is_rejected_before_creating_run(tmp_path):
    source = tmp_path / "template"
    source.mkdir()
    runs = tmp_path / "runs"
    with pytest.raises(FileNotFoundError):
        generate_case(plan_task("cylinder flow"), runs_dir=runs, template_dir=source)
    assert not runs.exists()


def test_output_inside_template_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="source template"):
        generate_case(plan_task("cylinder flow"),
                      runs_dir=tmp_path / "runs", template_dir=tmp_path)


def test_generate_cli_accepts_text_and_task_file_from_other_directory(tmp_path):
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps(plan_task("cylinder flow Re=200 U=2 D=1")))
    for inputs in (["cylinder flow Re=200 U=2 D=1"], ["--task", str(task_file)]):
        completed = subprocess.run(
            [sys.executable, "-m", "cfd_memo_agent.cli", "generate", *inputs,
             "--runs-dir", str(tmp_path / "runs")],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        )
        result = json.loads(completed.stdout)
        assert result["status"] == "generated"
        assert Path(result["case_path"]).is_dir()
        assert result["mesh_verified"] is False
