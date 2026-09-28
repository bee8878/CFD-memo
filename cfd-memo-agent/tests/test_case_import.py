import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cfd_memo_agent.capabilities import get_capability, list_capabilities
from cfd_memo_agent.case_import import import_case, inspect_case, load_case_spec, validate_imported_case
from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.generator.case import PROJECT_ROOT
from cfd_memo_agent.planner import plan_task
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.runner import execution, imported


TEMPLATE = PROJECT_ROOT / "cases/templates/cylinder-2d"


def test_case_spec_converts_existing_task_without_changing_it():
    task = plan_task("cylinder flow Re=200 U=2 D=1")
    before = json.dumps(task, sort_keys=True)
    spec = CaseSpec.from_task(task)

    assert spec.source_type == "generated"
    assert spec.solver == "icoFoam"
    assert spec.physics_model == "incompressible_laminar"
    assert spec.fields == ("U", "p")
    assert set(spec.boundaries) == set(task["boundary_conditions"])
    assert CaseSpec.from_dict(spec.to_dict()) == spec
    assert json.dumps(task, sort_keys=True) == before


def test_capability_registry_is_explicit_and_fails_closed():
    names = {item["name"] for item in list_capabilities()}
    assert names == {"icoFoam", "simpleFoam"}
    assert get_capability("icoFoam").generated_case_support
    assert not get_capability("simpleFoam").imported_case_support
    with pytest.raises(ValueError, match="不支持求解器"):
        get_capability("unknownFoam")


def test_import_creates_original_work_copy_spec_and_validation(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(TEMPLATE, source)
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    result = import_case(source, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    spec = load_case_spec(run / "case-spec.json")

    assert result["status"] == "imported" and result["scripts_executed"] is False
    assert result["validation"]["config_valid"]
    assert spec.source_type == "imported" and spec.mesh_source == "blockMesh"
    assert spec.dimension == "2D" and spec.solver == "icoFoam"
    assert set(spec.boundaries) == {"inlet", "outlet", "cylinder", "top", "bottom", "frontAndBack"}
    assert validate_imported_case(spec, run / "case")["config_valid"]
    (run / "case/0/U").write_text("changed", encoding="utf-8")
    assert (run / "original/0/U").read_bytes() == before[Path("0/U")]
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_import_rejects_invalid_case_before_creating_output(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(TEMPLATE, source)
    control = source / "system/controlDict"
    control.write_text(control.read_text().replace("application     icoFoam;", "application     magicFoam;"))
    runs = tmp_path / "runs"

    with pytest.raises(ValueError, match="不支持求解器"):
        import_case(source, runs_dir=runs)
    assert not runs.exists()


def test_imported_run_uses_registered_commands_not_allrun(tmp_path, monkeypatch):
    source = tmp_path / "source"
    shutil.copytree(TEMPLATE, source)
    (source / "Allrun").write_text("must not run", encoding="utf-8")
    run = Path(import_case(source, runs_dir=tmp_path / "runs")["run_path"])
    calls = []

    monkeypatch.setattr(imported, "discover", lambda stages: {
        "backend": "native", "version": "10",
        "executables": {stage: f"/mock/{stage}" for stage in stages},
    })
    monkeypatch.setattr(imported, "_mesh_hash", lambda case: "mesh-hash")
    monkeypatch.setattr(imported, "_result_evidence", lambda *args: {
        "end_time": 10, "fields": {"U": "U", "p": "p"},
        "mesh_sha256": "mesh-hash", "physical_validated": False,
    })

    def fake_execute(command, case, log, timeout):
        stage = Path(command[0]).name
        calls.append(stage)
        log.write_text("Mesh OK.\nEnd\n" if stage == "checkMesh" else "End\n")
        return {"returncode": 0, "timed_out": False, "duration_seconds": 0.01}

    monkeypatch.setattr(execution, "_execute", fake_execute)
    result = run_case(run, mode="real", timeout=2)

    assert result["status"] == "completed"
    assert calls == ["blockMesh", "checkMesh", "icoFoam"]
    assert all("Allrun" not in str(step["command"]) for step in result["steps"])
    assert result["imported_case"] and not result["physical_validated"]


def test_imported_case_refuses_simulated_runner(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(TEMPLATE, source)
    run = Path(import_case(source, runs_dir=tmp_path / "runs")["run_path"])
    with pytest.raises(ValueError, match="只支持真实运行"):
        run_case(run, mode="simulated")
    assert not (run / "attempts").exists()


def test_import_and_capabilities_cli_work_from_other_directory(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(TEMPLATE, source)
    capabilities = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "capabilities"],
        cwd=tmp_path, capture_output=True, text=True, check=True)
    assert {item["name"] for item in json.loads(capabilities.stdout)["capabilities"]} == {
        "icoFoam", "simpleFoam",
    }
    imported_result = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "import-case", str(source),
         "--runs-dir", str(tmp_path / "runs")],
        cwd=tmp_path, capture_output=True, text=True, check=True)
    run = Path(json.loads(imported_result.stdout)["run_path"])
    validated = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "validate", "--run", str(run)],
        cwd=tmp_path, capture_output=True, text=True, check=True)
    assert json.loads(validated.stdout)["config_valid"]
