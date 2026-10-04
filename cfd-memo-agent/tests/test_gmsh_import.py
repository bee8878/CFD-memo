import json
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent.gmsh import prepare_gmsh
from cfd_memo_agent.gmsh_import import import_gmsh_mesh
from cfd_memo_agent.mesh_capabilities import load_mesh_spec, mesh_command_plan
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.runner import execution
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.task import SCHEMA_PATH

PROJECT = SCHEMA_PATH.parent.parent
MESH = PROJECT / "examples/cavity-2x2.msh"
TASK = PROJECT / "examples/task.cavity-2d-gmsh.json"
MAPPING = PROJECT / "examples/gmsh-boundary-map.json"


def test_import_gmsh_maps_boundaries_and_saves_provenance(tmp_path):
    source_before = MESH.read_bytes()
    result = import_gmsh_mesh(
        MESH, task_path=TASK, boundary_map=MAPPING, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    task = json.loads((run / "task.json").read_text(encoding="utf-8"))
    spec = load_mesh_spec(run / "mesh-spec.json")

    assert result["status"] == "mesh_imported"
    assert result["validation"]["config_valid"]
    assert result["mesh_inspection"]["cell_count"] == 4
    assert task["mesh"]["generator"] == "gmsh"
    assert task["mesh"]["boundary_map"] == {
        "lid": "movingWall", "walls": "fixedWalls", "span": "frontAndBack",
    }
    assert not (run / "case/system/blockMeshDict").exists()
    assert spec.capability_id == "gmsh"
    assert mesh_command_plan(spec, task["solver"]) == (
        "gmshToFoam", "checkMesh", "icoFoam",
    )
    assert MESH.read_bytes() == source_before


def test_import_rejects_bad_mapping_or_geometry_before_creating_run(tmp_path):
    task = json.loads(TASK.read_text(encoding="utf-8"))
    task["geometry"]["width"] = 2
    task["physics"]["kinematic_viscosity"] = 0.02
    runs = tmp_path / "runs"

    with pytest.raises(ValueError, match="网格范围"):
        import_gmsh_mesh(MESH, task_path=task, boundary_map=json.loads(
            MAPPING.read_text(encoding="utf-8")), runs_dir=runs)
    with pytest.raises(ValueError, match="一一对应"):
        import_gmsh_mesh(MESH, task_path=TASK, boundary_map={"lid": "movingWall"},
                         runs_dir=runs)
    assert not runs.exists()


def test_imported_mesh_hash_tampering_is_detected(tmp_path):
    result = import_gmsh_mesh(
        MESH, task_path=TASK, boundary_map=MAPPING, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    copied = run / "case/mesh/mesh.msh"
    copied.write_text(copied.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    task = json.loads((run / "task.json").read_text(encoding="utf-8"))

    report = validate_case(task, run / "case")
    assert not report["config_valid"]
    assert {item["code"] for item in report["errors"]} == {"GMSH_IMPORT_INVALID"}


def test_runner_uses_fixed_gmsh_arguments_and_conversion_hook(tmp_path, monkeypatch):
    result = import_gmsh_mesh(
        MESH, task_path=TASK, boundary_map=MAPPING, runs_dir=tmp_path / "runs")
    run = Path(result["run_path"])
    calls = []

    class EvidenceAdapter:
        def collect_mesh_evidence(self, case, task, default):
            return {"mesh_verified": True, "actual_cells": 4,
                    "input_sha256": execution.fingerprint(case), "physical_validated": False}

        def collect_field_evidence(self, case, task, mesh, default):
            return {"end_time": 0.05, "physical_validated": False}

        def collect_physical_evidence(self, case, task, default):
            return None

    monkeypatch.setattr(execution, "get_case_adapter", lambda task: EvidenceAdapter())
    monkeypatch.setattr(execution, "discover", lambda stages: {
        "backend": "native", "version": "10",
        "executables": {stage: f"/mock/{stage}" for stage in stages},
    })
    monkeypatch.setattr(execution, "normalize_openfoam_boundary", lambda case, types: {
        "boundary_file": str(case / "constant/polyMesh/boundary"), "changes": {},
    })

    def fake_execute(command, case, log, timeout):
        calls.append(command)
        stage = Path(command[0]).name
        log.write_text("Mesh OK.\nEnd\n" if stage == "checkMesh" else "End\n")
        return {"returncode": 0, "timed_out": False, "duration_seconds": 0.01}

    monkeypatch.setattr(execution, "_execute", fake_execute)
    report = run_case(run, mode="real", timeout=2)

    assert report["status"] == "completed"
    assert [Path(command[0]).name for command in calls] == [
        "gmshToFoam", "checkMesh", "icoFoam",
    ]
    assert calls[0][1] == str(Path(report["case_path"]) / "mesh/mesh.msh")
    assert calls[0][2:] == ["-case", report["case_path"]]
    assert report["mesh_conversion"]["source_sha256"]


def test_prepare_gmsh_rejects_binary_or_unsupported_version(tmp_path):
    invalid = tmp_path / "invalid.msh"
    invalid.write_bytes(b"$MeshFormat\n4.1 0 8\n$EndMeshFormat\n")
    with pytest.raises(ValueError, match="2.2 ASCII"):
        prepare_gmsh(
            invalid,
            boundary_map={"lid": "movingWall", "walls": "fixedWalls",
                          "span": "frontAndBack"},
            expected_boundaries={"movingWall", "fixedWalls", "frontAndBack"},
            geometry={"width": 1, "height": 1, "depth": 0.1}, expected_cells=4,
        )


def test_import_mesh_cli_works_from_another_directory(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "import-mesh", str(MESH),
         "--task", str(TASK), "--boundary-map", str(MAPPING),
         "--runs-dir", str(tmp_path / "runs")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "mesh_imported"
    assert result["validation"]["config_valid"]
