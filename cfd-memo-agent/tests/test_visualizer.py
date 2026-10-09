"""Explicit ParaView result selection and launch tests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent import visualizer


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _artifact(run: Path, path: Path) -> dict:
    data = path.read_bytes()
    return {
        "role": "field", "path": path.relative_to(run).as_posix(),
        "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }


def completed_attempt(run: Path, name: str = "attempt-real") -> Path:
    attempt = run / "attempts" / name
    case = attempt / "case"
    mesh = case / "constant/polyMesh"
    mesh.mkdir(parents=True)
    for item in visualizer.MESH_FILES:
        (mesh / item).write_text(item, encoding="utf-8")
    time_dir = case / "10"
    time_dir.mkdir()
    for field in ("U", "p"):
        (time_dir / field).write_text(f"field {field}\n", encoding="utf-8")
    _save(attempt / "execution.json", {
        "status": "completed", "mode": "real", "simulated": False,
        "case_path": str(case), "attempt_path": str(attempt), "run_path": str(run),
    })
    _save(attempt / "result-evidence.json", {
        "end_time": 10, "actual_cells": 400,
        "fields": {"U": str(time_dir / "U"), "p": str(time_dir / "p")},
        "physical_validated": False,
    })
    _save(attempt / "result-index.json", {
        "schema_version": 1, "status": "completed", "attempt_path": str(attempt),
        "physical_validated": False,
        "artifacts": [_artifact(run, time_dir / "U"), _artifact(run, time_dir / "p")],
    })
    return attempt


def test_resolves_and_checks_latest_completed_real_attempt(tmp_path):
    workflow = tmp_path / "workflow"
    old = completed_attempt(workflow / "rounds/round-000", "attempt-old")
    newest = completed_attempt(workflow / "rounds/round-001", "attempt-new")
    old_time = old / "execution.json"
    os.utime(old_time, (1, 1))
    os.utime(newest / "execution.json", (2, 2))
    simulated = workflow / "rounds/round-002/attempts/attempt-simulated"
    _save(simulated / "execution.json", {
        "status": "simulated_success", "mode": "simulated", "simulated": True,
    })

    ready = visualizer.check_visualization_ready(
        visualizer.resolve_result_case(workflow))

    assert Path(ready["attempt_path"]) == newest.resolve()
    assert ready["actual_cells"] == 400 and ready["end_time"] == 10
    assert set(ready["fields"]) == {"U", "p"}
    assert ready["physical_validated"] is False


def test_rejects_simulation_missing_mesh_and_tampered_field(tmp_path):
    simulated = tmp_path / "simulated"
    _save(simulated / "attempts/a/execution.json", {
        "status": "simulated_success", "mode": "simulated", "simulated": True,
    })
    with pytest.raises(ValueError, match="真实 OpenFOAM"):
        visualizer.resolve_result_case(simulated)

    run = tmp_path / "run"
    attempt = completed_attempt(run)
    (attempt / "case/constant/polyMesh/points").unlink()
    with pytest.raises(ValueError, match="网格文件不完整"):
        visualizer.check_visualization_ready(visualizer.resolve_result_case(run))

    attempt = completed_attempt(tmp_path / "tampered")
    (attempt / "case/10/U").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="result-index 不一致"):
        visualizer.check_visualization_ready(
            visualizer.resolve_result_case(tmp_path / "tampered"))


def test_wsl_launch_uses_argument_list_and_creates_marker(tmp_path, monkeypatch):
    run = tmp_path / "path with spaces/run"
    ready = visualizer.check_visualization_ready(
        visualizer.resolve_result_case(completed_attempt(run).parent.parent))
    monkeypatch.setattr(visualizer, "wsl_path", lambda environment, path: "/mnt/d/case path/" + Path(path).name)
    calls = []

    class Process:
        pid = 4321

    def fake_popen(command, **options):
        calls.append((command, options))
        return Process()

    environment = {
        "backend": "wsl", "distribution": "Ubuntu-22.04",
        "prefix": ["C:/Windows/System32/wsl.exe", "--distribution", "Ubuntu-22.04", "--exec"],
        "viewer": "/usr/bin/paraFoam", "viewer_name": "paraFoam",
    }
    result = visualizer.launch_paraview(ready, environment, popen=fake_popen)

    command, options = calls[0]
    assert result["pid"] == 4321
    assert command[-4:] == ["/usr/bin/paraFoam", "-builtin", "-case", "/mnt/d/case path/case"]
    assert options["shell"] is False
    assert Path(result["marker_path"]).is_file()


def test_open_result_reports_launch_without_claiming_physics(tmp_path):
    run = tmp_path / "run"
    completed_attempt(run)
    observed = {}

    def launch(ready, environment):
        observed.update(ready)
        marker = Path(ready["case_path"]) / "CFD-Memo.foam"
        marker.touch()
        return {"pid": 99, "command": [], "marker_path": str(marker)}

    result = visualizer.open_result(
        run,
        discoverer=lambda: {"backend": "wsl", "viewer_name": "paraFoam"},
        launcher=launch,
    )
    assert result["status"] == "launched" and result["pid"] == 99
    assert result["physical_validated"] is False
    assert observed["actual_cells"] == 400


def test_cli_view_blocks_non_real_result_without_traceback(tmp_path):
    run = tmp_path / "simulated"
    _save(run / "attempts/a/execution.json", {
        "status": "simulated_success", "mode": "simulated", "simulated": True,
    })
    result = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "view", "--run", str(run)],
        cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert json.loads(result.stdout)["status"] == "blocked"
