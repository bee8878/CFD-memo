"""C4 integration and process tests; no OpenFOAM installation required."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent.diagnoser import diagnose_log, diagnose_validation
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.runner import run_case
from cfd_memo_agent.runner import execution
from cfd_memo_agent.validator.task import SCHEMA_PATH

PROJECT = SCHEMA_PATH.parent.parent


@pytest.fixture
def generated(tmp_path):
    task = json.loads((PROJECT / "examples/task.cylinder-2d.json").read_text(encoding="utf-8"))
    return Path(generate_case(task, runs_dir=tmp_path / "runs")["run_path"])


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def codes(report):
    return {item["code"] for item in report["findings"]}


@pytest.mark.parametrize("scenario,status,code", [
    ("success", "simulated_success", None),
    ("missing-boundary", "failed", "MISSING_BOUNDARY"),
    ("bad-transport", "failed", "BAD_TRANSPORT"),
    ("unknown-failure", "failed", "UNKNOWN_FAILURE"),
])
def test_simulated_scenarios_save_reports_without_touching_input(generated, monkeypatch, scenario, status, code):
    before = snapshot(generated / "case")
    task_before = (generated / "task.json").read_bytes()
    template_before = snapshot(PROJECT / "cases/templates/cylinder-2d")
    monkeypatch.setattr(execution, "_execute", lambda *a: pytest.fail("simulation launched process"))
    result = run_case(generated, mode="simulated", scenario=scenario)
    assert result["status"] == status and result["simulated"]
    assert result["physical_validated"] is False
    assert result["validation"]["runtime_blockers"]
    assert result["validation_findings"]
    step = result["steps"][0]
    assert step["command"] is None
    if code:
        assert code in codes(result)
        item = result["findings"][0]
        assert item["location"] and item["evidence"] and item["suggestion"]
    else:
        assert step["diagnosis"]["last_time"] == 10
        assert len(step["diagnosis"]["residuals"]) == 2
        assert step["diagnosis"]["residuals"][0]["final"] == 8e-7
    attempt = Path(result["attempt_path"])
    assert json.loads((attempt / "execution.json").read_text(encoding="utf-8")) == result
    assert (attempt / "diagnosis.json").is_file()
    assert (attempt / "validation.json").is_file()
    assert Path(step["log_path"]).read_bytes() == (execution.SAMPLE_LOGS / f"{scenario}.log").read_bytes()
    assert snapshot(generated / "case") == before
    assert (generated / "task.json").read_bytes() == task_before
    assert snapshot(PROJECT / "cases/templates/cylinder-2d") == template_before


def test_repeated_runs_keep_previous_attempt(generated):
    first = run_case(generated, mode="simulated")
    before = snapshot(Path(first["attempt_path"]))
    second = run_case(generated, mode="simulated", scenario="bad-transport")
    assert first["attempt_path"] != second["attempt_path"]
    assert snapshot(Path(first["attempt_path"])) == before


@pytest.mark.parametrize("damage", ["missing-task", "broken-task", "bad-config"])
def test_validation_blocks_even_simulation(generated, damage, monkeypatch):
    if damage == "missing-task":
        (generated / "task.json").unlink()
    elif damage == "broken-task":
        (generated / "task.json").write_text("{", encoding="utf-8")
    else:
        (generated / "case/0/U").unlink()
    monkeypatch.setattr(execution, "_execute", lambda *a: pytest.fail("invalid input executed"))
    result = run_case(generated, mode="simulated")
    assert result["status"] == "blocked" and not result["steps"]
    assert result["findings"]


def test_real_reports_mesh_and_environment_blockers(generated, monkeypatch):
    monkeypatch.setattr(execution.shutil, "which", lambda name: None)
    monkeypatch.setattr(execution, "_execute", lambda *a: pytest.fail("blocked case executed"))
    result = run_case(generated, mode="real")
    assert result["status"] == "environment_error"
    assert "OPENFOAM_UNAVAILABLE" in codes(result)
    assert result["validation"]["runtime_blockers"][0]["code"] == "MESH_NOT_VERIFIED"
    assert not result["simulated"] and result["steps"] == []


@pytest.fixture
def ready_backend(monkeypatch):
    # Stage-order unit tests use mocked evidence; C6 tests exercise output parsing.
    monkeypatch.setenv("WM_PROJECT_VERSION", "10")
    monkeypatch.setattr(execution.shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(execution, "mesh_evidence", lambda *a: {"mesh_verified": True})
    monkeypatch.setattr(execution, "field_evidence", lambda *a: {"physical_validated": False})


def test_real_missing_environment_never_falls_back(generated, ready_backend, monkeypatch):
    monkeypatch.setattr(execution.shutil, "which", lambda name: None)
    result = run_case(generated, mode="real")
    assert result["status"] == "environment_error"
    assert result["steps"] == [] and not result["simulated"]


@pytest.mark.parametrize("failure", [None, "blockMesh", "checkMesh", "icoFoam", "timeout"])
def test_real_stage_order_and_stop_on_failure(generated, ready_backend, monkeypatch, failure):
    calls = []
    before = snapshot(generated / "case")

    def execute(command, case, log, timeout):
        stage = Path(command[0]).name
        calls.append(stage)
        assert command[1:] == ["-case", str(case)]
        assert timeout == 2
        assert case != generated / "case"
        timed_out = failure == "timeout" and stage == "blockMesh"
        failed = stage == failure
        text = "unknown failure\n" if failed else "Mesh OK.\nEnd\n" if stage == "checkMesh" else "End\n"
        log.write_text(text, encoding="utf-8")
        return {"returncode": 7 if failed or timed_out else 0, "timed_out": timed_out,
                "duration_seconds": 0.01}

    monkeypatch.setattr(execution, "_execute", execute)
    result = run_case(generated, mode="real", timeout=2)
    expected = 1 if failure in {"timeout", "blockMesh"} else 2 if failure == "checkMesh" else 3
    assert calls == list(execution.COMMANDS[:expected])
    assert result["status"] == ("timeout" if failure == "timeout" else "failed" if failure else "completed")
    assert result["physical_validated"] is False
    assert snapshot(generated / "case") == before
    if failure:
        assert result["steps"][-1]["returncode"] == 7


def test_execute_captures_stdout_stderr_and_exit_code(tmp_path):
    log = tmp_path / "process.log"
    result = execution._execute(
        [sys.executable, "-c", "import sys; print('output'); print('error', file=sys.stderr); sys.exit(7)"],
        tmp_path, log, 5)
    assert result["returncode"] == 7 and not result["timed_out"]
    assert {"output", "error"} <= set(log.read_text().splitlines())


def test_real_launch_error_keeps_command_and_diagnostic(generated, ready_backend, monkeypatch):
    def fail(*args):
        raise OSError("executable cannot start")
    monkeypatch.setattr(execution, "_execute", fail)
    result = run_case(generated, mode="real")
    assert result["status"] == "execution_error"
    assert "EXECUTION_IO" in codes(result)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["command"][0] == "/mock/blockMesh"
    assert result["steps"][0]["returncode"] is None


def test_missing_sample_is_execution_error_not_simulated_success(generated, monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "SAMPLE_LOGS", tmp_path / "missing")
    result = run_case(generated, mode="simulated")
    assert result["status"] == "execution_error" and "EXECUTION_IO" in codes(result)


def test_mesh_blockers_stop_real_commands_even_when_environment_is_available(generated, monkeypatch):
    original_validate = execution.validate_case
    def blocked(*args):
        report = original_validate(*args)
        report["runtime_blockers"].append({"code": "EMPTY_BOUNDARY", "message": "empty", "location": "mesh"})
        return report
    monkeypatch.setattr(execution, "validate_case", blocked)
    monkeypatch.setenv("WM_PROJECT_VERSION", "10")
    monkeypatch.setattr(execution.shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(execution, "_execute", lambda *args: pytest.fail("mesh blocker ignored"))
    result = run_case(generated, mode="real")
    assert result["status"] == "blocked" and result["steps"] == []
    assert "EMPTY_BOUNDARY" in codes(result)


def test_execute_timeout_terminates_process_and_keeps_partial_log(tmp_path):
    log = tmp_path / "timeout.log"
    result = execution._execute(
        [sys.executable, "-u", "-c", "import time; print('started', flush=True); time.sleep(30)"],
        tmp_path, log, 1)
    assert result["timed_out"] and result["returncode"] != 0
    assert "started" in log.read_text()
    assert result["duration_seconds"] < 15


@pytest.mark.parametrize("options", [
    {"mode": "unknown"}, {"mode": "real", "scenario": "success"},
    {"mode": "simulated", "scenario": "invalid"}, {"mode": "simulated", "timeout": 0},
    {"mode": "simulated", "timeout": float("nan")}, {"mode": "simulated", "timeout": True},
])
def test_invalid_options_create_no_attempt(generated, options):
    with pytest.raises(ValueError):
        run_case(generated, **options)
    assert not (generated / "attempts").exists()


@pytest.mark.parametrize("text,returncode,stage,code", [
    ("End\n", 3, "icoFoam", "UNKNOWN_FAILURE"),
    ("FOAM FATAL ERROR:\nEnd\n", 0, "icoFoam", "UNKNOWN_FAILURE"),
    ("ExecutionTime = 1\n", 0, "icoFoam", "INCOMPLETE_LOG"),
    ("End\n", 0, "checkMesh", "INCOMPLETE_LOG"),
    ("Mesh OK.\nFailed 1 mesh checks\nEnd\n", 0, "checkMesh", "MESH_CHECK_FAILED"),
    ("Solving for p, Initial residual = nan, Final residual = inf\nEnd\n", 0, "icoFoam", "UNKNOWN_FAILURE"),
])
def test_logs_do_not_claim_false_success(text, returncode, stage, code):
    report = diagnose_log(text, returncode=returncode, stage=stage)
    assert report["status"] == "failed" and code in codes(report)


def test_normal_sigfpe_banner_is_not_a_floating_point_failure():
    report = diagnose_log(
        "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).\n"
        "Time = 10s\n"
        "Solving for Ux, Initial residual = 1e-4, Final residual = 1e-7, No Iterations 1\n"
        "End\n",
        returncode=0,
    )
    assert report["status"] == "completed" and not report["findings"]
    assert report["last_time"] == 10 and report["residuals"][0]["time"] == 10


def test_validation_diagnosis_distinguishes_actions_without_mutation():
    report = {"task_valid": True, "errors": [
        {"code": "TIME_RANGE", "location": "task.time_control", "message": "time conflict"},
        {"code": "VALUE_MISMATCH", "location": "0/U", "message": "wrong velocity"}],
        "runtime_blockers": [{"code": "EMPTY_BOUNDARY", "location": "mesh", "message": "empty"}]}
    before = deepcopy(report)
    result = diagnose_validation(report)
    assert [item["action"] for item in result["findings"]] == [
        "user_clarification", "rule_candidate", "manual_required"]
    assert report == before


def cli(cwd, *args):
    return subprocess.run([sys.executable, "-m", "cfd_memo_agent.cli", *map(str, args)],
                          cwd=cwd, capture_output=True, encoding="utf-8", timeout=20)


@pytest.mark.parametrize("scenario,exitcode", [("success", 0), ("missing-boundary", 1)])
def test_cli_run_from_other_directory(generated, tmp_path, scenario, exitcode):
    result = cli(tmp_path, "run", "--run", generated, "--runner", "simulated", "--scenario", scenario)
    assert result.returncode == exitcode, result.stderr
    report = json.loads(result.stdout)
    assert report["simulated"] and Path(report["attempt_path"]).is_dir()
    if exitcode:
        assert "缺少边界" in report["findings"][0]["message"]


def test_cli_usage_errors(generated, tmp_path):
    for args in [
        ["run", "--run", generated],
        ["run", "--run", generated, "--runner", "real", "--scenario", "success"],
        ["run", "--run", generated, "--runner", "simulated", "--timeout", "nan"],
    ]:
        result = cli(tmp_path, *args)
        assert result.returncode == 2 and "Traceback" not in result.stderr


def test_cli_diagnose_saves_report_and_refuses_overwrite(tmp_path):
    output = tmp_path / "diagnosis.json"
    args = ["diagnose", "--log", execution.SAMPLE_LOGS / "bad-transport.log",
            "--returncode", "1", "--simulated", "--output", output]
    result = cli(tmp_path, *args)
    assert result.returncode == 1
    assert "BAD_TRANSPORT" in codes(json.loads(result.stdout))
    before = output.read_bytes()
    assert cli(tmp_path, *args).returncode == 2
    assert output.read_bytes() == before
