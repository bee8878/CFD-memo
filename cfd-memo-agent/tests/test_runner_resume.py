import json
from pathlib import Path
import sys

import pytest

from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.runner import execution
from cfd_memo_agent.runner.state import tree_fingerprint
from cfd_memo_agent.validator.task import SCHEMA_PATH

PROJECT = SCHEMA_PATH.parent.parent


@pytest.fixture
def generated(tmp_path):
    task = json.loads((PROJECT / "examples/task.cylinder-2d.json").read_text(encoding="utf-8"))
    return Path(generate_case(task, runs_dir=tmp_path / "runs")["run_path"])


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(execution, "discover", lambda stages=execution.COMMANDS: {
        "backend": "native", "version": "10",
        "executables": {stage: f"/mock/{stage}" for stage in stages},
    })
    monkeypatch.setattr(execution, "mesh_evidence", lambda *args: {
        "mesh_verified": True, "actual_cells": 1, "input_sha256": "input",
        "mesh_sha256": "mesh", "physical_validated": False,
    })
    monkeypatch.setattr(execution, "field_evidence", lambda *args: {
        "end_time": 10, "fields": {"U": "U", "p": "p"},
        "actual_cells": 1, "physical_validated": False,
    })
    monkeypatch.setattr(execution, "force_coefficient_evidence", lambda *args: None)


def test_failed_solver_resumes_in_new_attempt_and_reuses_mesh_stage(
        generated, backend, monkeypatch):
    first_calls = []

    def fail_solver(command, case, log, timeout):
        stage = Path(command[0]).name
        first_calls.append(stage)
        failed = stage == "icoFoam"
        log.write_text("failure\n" if failed else "Mesh OK.\nEnd\n")
        return {"returncode": 7 if failed else 0, "timed_out": False,
                "duration_seconds": 0.01}

    monkeypatch.setattr(execution, "_execute", fail_solver)
    first = run_case(generated, mode="real", timeout=2, minimum_free_mb=1)
    first_attempt = Path(first["attempt_path"])
    state_before = (first_attempt / "stage-state.json").read_bytes()
    checkpoint = first_attempt / "checkpoints/checkMesh/case"

    assert first["status"] == "failed"
    assert first_calls == ["blockMesh", "checkMesh", "icoFoam"]
    assert checkpoint.is_dir() and tree_fingerprint(checkpoint)

    resumed_calls = []

    def succeed(command, case, log, timeout):
        stage = Path(command[0]).name
        resumed_calls.append(stage)
        log.write_text("Mesh OK.\nEnd\n" if stage == "checkMesh" else "End\n")
        return {"returncode": 0, "timed_out": False, "duration_seconds": 0.01}

    monkeypatch.setattr(execution, "_execute", succeed)
    resumed = run_case(
        generated, mode="real", timeout=2, minimum_free_mb=1,
        resume_attempt=first_attempt,
    )

    assert resumed["status"] == "completed"
    assert resumed_calls == ["checkMesh", "icoFoam"]
    assert resumed["resume"]["reused_stages"] == ["blockMesh"]
    assert Path(resumed["attempt_path"]) != first_attempt
    assert (first_attempt / "stage-state.json").read_bytes() == state_before
    assert resumed["steps"][0]["reused"] is True
    result_index = json.loads(Path(resumed["result_index_path"]).read_text(encoding="utf-8"))
    roles = {item["role"] for item in result_index["artifacts"]}
    assert {"input", "evidence", "log"} <= roles
    assert all(len(item["sha256"]) == 64 for item in result_index["artifacts"])


def test_resume_rejects_changed_input_and_checkpoint(generated, backend, monkeypatch):
    def fail(command, case, log, timeout):
        stage = Path(command[0]).name
        log.write_text("failure\n" if stage == "icoFoam" else "Mesh OK.\nEnd\n")
        return {"returncode": 7 if stage == "icoFoam" else 0,
                "timed_out": False, "duration_seconds": 0.01}

    monkeypatch.setattr(execution, "_execute", fail)
    first = run_case(generated, mode="real", timeout=2, minimum_free_mb=1)
    source = Path(first["attempt_path"])
    task_path = generated / "task.json"
    task_path.write_text(task_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    changed = run_case(
        generated, mode="real", timeout=2, minimum_free_mb=1,
        resume_attempt=source,
    )
    assert changed["status"] == "blocked"
    assert {item["code"] for item in changed["findings"]} == {"RESUME_INVALID"}


def test_log_limit_terminates_process_and_records_reason(tmp_path):
    log = tmp_path / "large.log"
    result = execution._execute(
        [sys.executable, "-u", "-c", "print('x' * 2000000)"],
        tmp_path, log, 10, max_log_bytes=100,
    )
    assert result["resource_limited"] is True
    assert not result["timed_out"]


def test_disk_preflight_stops_before_environment_or_command(generated, monkeypatch):
    monkeypatch.setattr(
        execution, "ensure_disk_space",
        lambda *args: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(execution, "discover", lambda *args: pytest.fail("environment probed"))
    result = run_case(generated, mode="real", minimum_free_mb=1)
    assert result["status"] == "resource_limit"
    assert {item["code"] for item in result["findings"]} == {"DISK_SPACE_LIMIT"}
    assert result["steps"] == []
