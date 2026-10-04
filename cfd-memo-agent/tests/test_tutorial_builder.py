import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cfd_memo_agent.capabilities import get_capability
from cfd_memo_agent.case_adapters import get_case_adapter
from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.case_adapters import backward_step_evidence
from cfd_memo_agent.tutorial_builder import build_tutorial_case
from cfd_memo_agent.tutorial_capabilities import list_tutorial_capabilities
from cfd_memo_agent.validator import validate_case


TUTORIAL_ID = "incompressible/simpleFoam/pitzDaily"
CAVITY_TUTORIAL_ID = "incompressible/icoFoam/cavity/cavity"
PROJECT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def header(obj: str, cls: str = "dictionary") -> str:
    return f"FoamFile {{ format ascii; class {cls}; object {obj}; }}\n"


BOUNDARIES = {
    "U": {"inlet": "fixedValue", "outlet": "zeroGradient", "upperWall": "noSlip",
          "lowerWall": "noSlip", "frontAndBack": "empty"},
    "p": {"inlet": "zeroGradient", "outlet": "fixedValue", "upperWall": "zeroGradient",
          "lowerWall": "zeroGradient", "frontAndBack": "empty"},
    "k": {"inlet": "fixedValue", "outlet": "zeroGradient",
          "upperWall": "kqRWallFunction", "lowerWall": "kqRWallFunction",
          "frontAndBack": "empty"},
    "epsilon": {"inlet": "fixedValue", "outlet": "zeroGradient",
                "upperWall": "epsilonWallFunction", "lowerWall": "epsilonWallFunction",
                "frontAndBack": "empty"},
    "nut": {"inlet": "calculated", "outlet": "calculated",
            "upperWall": "nutkWallFunction", "lowerWall": "nutkWallFunction",
            "frontAndBack": "empty"},
}


def field(name: str) -> str:
    cls = "volVectorField" if name == "U" else "volScalarField"
    dimensions = "[0 1 -1 0 0 0 0]" if name == "U" else "[0 2 -2 0 0 0 0]"
    internal = "uniform (0 0 0)" if name == "U" else "uniform 0"
    patches = []
    for patch, kind in BOUNDARIES[name].items():
        value = ""
        if name == "U" and patch == "inlet":
            value = " value uniform (10 0 0);"
        patches.append(f"{patch} {{ type {kind};{value} }}")
    return (header(name, cls) + f"dimensions {dimensions};\ninternalField {internal};\n"
            + "boundaryField { " + " ".join(patches) + " }\n")


def tutorial_fixture(tmp_path: Path):
    root = tmp_path / "tutorials"
    case = root / TUTORIAL_ID
    for name in BOUNDARIES:
        write(case / "0" / name, field(name))
    write(case / "constant/physicalProperties",
          header("physicalProperties") + "viscosityModel constant;\nnu 1e-05;\n")
    write(case / "constant/momentumTransport",
          header("momentumTransport")
          + "simulationType RAS;\nRAS { model kEpsilon; turbulence on; printCoeffs on; }\n")
    boundaries = "\n".join(
        f"{name} {{ type {'empty' if name == 'frontAndBack' else 'wall' if 'Wall' in name else 'patch'}; faces ((0 1 2 3)); }}"
        for name in ("inlet", "outlet", "upperWall", "lowerWall", "frontAndBack")
    )
    blocks = " ".join("hex (0 1 2 3 4 5 6 7) (1 1 1) simpleGrading (1 $g 1)" for _ in range(5))
    write(case / "system/blockMeshDict", header("blockMeshDict")
          + f"convertToMeters 0.001;\ng ((1 1 1));\nblocks ({blocks});\n"
          + f"boundary (\n{boundaries}\n);\n")
    write(case / "system/controlDict", header("controlDict")
          + "application simpleFoam;\nstartTime 0;\nendTime 2000;\n"
          + "deltaT 1;\nwriteInterval 100;\n"
          + "functions { #includeFunc ignored }\n")
    write(case / "system/fvSchemes", header("fvSchemes"))
    write(case / "system/fvSolution", header("fvSolution"))
    write(case / "Allrun", "exit 99\n")
    index = {
        "schema_version": 1, "openfoam_version": "10", "tutorial_root": str(root),
        "cases": [{"tutorial_id": TUTORIAL_ID, "solver": "simpleFoam",
                   "mesh_tools": ["blockMesh"],
                   "fields": ["U", "p", "k", "epsilon", "nut"]}],
    }
    index_path = tmp_path / "index.json"
    write(index_path, json.dumps(index))
    proposal = {
        "proposal_status": "reference_only", "source_type": "tutorial_reference",
        "tutorial_ids": [TUTORIAL_ID], "solver": "simpleFoam", "executable": False,
    }
    proposal_path = tmp_path / "proposal.json"
    write(proposal_path, json.dumps(proposal))
    return case, index_path, proposal_path


def cavity_tutorial_fixture(tmp_path: Path):
    root = tmp_path / "tutorials"
    case = root / CAVITY_TUTORIAL_ID
    shutil.copytree(PROJECT / "cases/templates/cavity-2d", case)
    solution = (case / "system/fvSolution").read_text(encoding="utf-8")
    solution = solution.replace(
        "solver PCG;\n        preconditioner DIC;\n        tolerance 1e-06;\n        relTol 0;",
        "$p;\n        relTol 0;",
        1,
    )
    write(case / "system/fvSolution", solution)
    control = (case / "system/controlDict").read_text(encoding="utf-8")
    control = control.replace("writeControl runTime;", "writeControl timeStep;")
    control = control.replace("writeInterval 0.1;", "writeInterval 20;")
    write(case / "system/controlDict", control)
    write(case / "Allrun", "exit 99\n")
    index = {
        "schema_version": 1, "openfoam_version": "10", "tutorial_root": str(root),
        "cases": [{
            "tutorial_id": CAVITY_TUTORIAL_ID, "solver": "icoFoam",
            "mesh_tools": ["blockMesh"], "fields": ["U", "p"],
        }],
    }
    index_path = tmp_path / "cavity-index.json"
    write(index_path, json.dumps(index))
    proposal = {
        "proposal_status": "reference_only", "source_type": "tutorial_reference",
        "tutorial_ids": [CAVITY_TUTORIAL_ID], "solver": "icoFoam",
        "executable": False,
    }
    proposal_path = tmp_path / "cavity-proposal.json"
    write(proposal_path, json.dumps(proposal))
    return case, index_path, proposal_path


def test_builder_creates_valid_case_without_copying_or_executing_scripts(tmp_path):
    source, index, proposal = tutorial_fixture(tmp_path)
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob("*") if path.is_file()}
    result = build_tutorial_case(proposal, index_path=index, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    task = json.loads((run / "task.json").read_text(encoding="utf-8"))
    spec = CaseSpec.from_dict(json.loads((run / "case-spec.json").read_text(encoding="utf-8")))

    assert result["status"] == "built" and result["validation"]["config_valid"]
    assert result["scripts_executed"] is False and result["scripts_copied"] is False
    assert not (run / "case/Allrun").exists()
    assert spec.source_type == "tutorial" and spec.solver == "simpleFoam"
    assert task["tutorial_reference"]["confirmed"] is True
    assert get_capability("simpleFoam").generated_case_support is True
    assert get_case_adapter(task).command_plan(task) == ("blockMesh", "checkMesh", "simpleFoam")
    assert validate_case(task, run / "case")["config_valid"]
    assert before == {path.relative_to(source): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}


def test_builder_rejects_unapproved_proposal_before_creating_run(tmp_path):
    _, index, proposal = tutorial_fixture(tmp_path)
    value = json.loads(proposal.read_text())
    value["tutorial_ids"] = ["incompressible/simpleFoam/other"]
    proposal.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="尚未批准"):
        build_tutorial_case(proposal, index_path=index, runs_dir=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_capability_data_registers_two_solvers_and_adapters():
    capabilities = list_tutorial_capabilities()

    assert {item["tutorial_id"] for item in capabilities} == {
        TUTORIAL_ID, CAVITY_TUTORIAL_ID,
    }
    assert {item["solver"] for item in capabilities} == {"icoFoam", "simpleFoam"}
    assert all(item["whitelist"] for item in capabilities)


def test_builder_uses_second_data_capability_for_official_cavity(tmp_path):
    source, index, proposal = cavity_tutorial_fixture(tmp_path)
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob("*") if path.is_file()}

    result = build_tutorial_case(proposal, index_path=index, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    task = json.loads((run / "task.json").read_text(encoding="utf-8"))
    spec = CaseSpec.from_dict(json.loads((run / "case-spec.json").read_text()))

    assert result["status"] == "built" and result["validation"]["config_valid"]
    assert result["capability_id"] == "openfoam10-icofoam-cavity-v1"
    assert task["case_type"] == "cavity-2d"
    assert task["mesh"]["generator"] == "tutorial-template"
    assert spec.source_type == "tutorial" and spec.solver == "icoFoam"
    assert spec.mesh_source == "blockMesh"
    assert not (run / "case/Allrun").exists()
    assert "$p;" not in (run / "case/system/fvSolution").read_text(encoding="utf-8")
    control = (run / "case/system/controlDict").read_text(encoding="utf-8")
    assert "writeControl    runTime;" in control
    assert "writeInterval    0.1;" in control
    assert validate_case(task, run / "case")["config_valid"]
    assert before == {path.relative_to(source): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}


def test_cavity_tutorial_validator_rejects_non_whitelist_file(tmp_path):
    _, index, proposal = cavity_tutorial_fixture(tmp_path)
    result = build_tutorial_case(proposal, index_path=index, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    task = json.loads((run / "task.json").read_text(encoding="utf-8"))
    write(run / "case/Allrun", "must not run")

    report = validate_case(task, run / "case")

    assert not report["config_valid"]
    assert any(item["code"] == "TUTORIAL_WHITELIST" for item in report["errors"])


def test_generated_case_detects_non_whitelist_and_parameter_drift(tmp_path):
    _, index, proposal = tutorial_fixture(tmp_path)
    result = build_tutorial_case(proposal, index_path=index, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    task = json.loads((run / "task.json").read_text(encoding="utf-8"))
    write(run / "case/Allrun", "must not run")
    report = validate_case(task, run / "case")
    assert not report["config_valid"]
    assert any(item["code"] == "TUTORIAL_WHITELIST" for item in report["errors"])


def test_tutorial_build_cli_works_from_other_directory(tmp_path):
    _, index, proposal = tutorial_fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "tutorials", "build",
         "--proposal", str(proposal), "--index", str(index),
         "--runs-dir", str(tmp_path / "runs")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "built"


def test_steady_result_uses_latest_written_iteration(tmp_path, monkeypatch):
    root = tmp_path / "case"
    boundaries = " ".join(
        f"{name} {{ type {kind}; }}" for name, kind in BOUNDARIES["U"].items())
    write(root / "287/U", header("U", "volVectorField")
          + "internalField nonuniform List<vector> 2 ((1 0 0) (2 0 0));\n"
          + f"boundaryField {{ {boundaries} }}\n")
    boundaries = " ".join(
        f"{name} {{ type {kind}; }}" for name, kind in BOUNDARIES["p"].items())
    write(root / "287/p", header("p", "volScalarField")
          + "internalField nonuniform List<scalar> 2 (0 1);\n"
          + f"boundaryField {{ {boundaries} }}\n")
    monkeypatch.setattr(backward_step_evidence, "fingerprint", lambda case: "config")
    monkeypatch.setattr(backward_step_evidence, "mesh_fingerprint", lambda mesh: "mesh")
    task = {
        "boundary_conditions": {name: {"U": u, "p": BOUNDARIES["p"][name]}
                                for name, u in BOUNDARIES["U"].items()},
        "time_control": {"start_time": 0, "end_time": 2000},
    }
    result = backward_step_evidence.backward_step_field_evidence(
        root, task, {"actual_cells": 2, "input_sha256": "config", "mesh_sha256": "mesh"})
    assert result["final_iteration"] == 287
    assert result["configured_end_iteration"] == 2000
    assert result["stopped_before_end"] is True
    assert result["physical_validated"] is False
