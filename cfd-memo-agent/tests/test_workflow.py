"""Exercise actual C5 repairs and records, plus bounded failure handling."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import ValidationError

from cfd_memo_agent import workflow
from cfd_memo_agent.correction import apply_repairs, inject_fault, propose_repairs
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.memory.episodes import episode_validator, save_episode
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.foam import read_json

PROJECT = workflow.PROJECT


@pytest.fixture
def task():
    return read_json(PROJECT / "examples/task.cylinder-2d.json")


@pytest.fixture
def task_path(task, tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task), encoding="utf-8")
    return path


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def check_episode(result):
    episode_validator().validate(result)
    assert read_json(Path(result["episode_path"])) == result
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "CFD-Memo 任务报告" in report
    assert result["stop_reason"]["message"] in report
    for record in result["rounds"]:
        assert read_json(Path(record["run_path"]) / "round.json") == record
    assert not result["metrics"]["experience_reused"]
    assert result["mode"] == "no_memory"
    assert result["physical_validated"] is False
    return report


@pytest.mark.parametrize("use_task", [False, True])
def test_first_round_success(task_path, tmp_path, use_task):
    args = {"task_path": task_path} if use_task else {"description": "做 Re=200 的二维圆柱绕流，入口速度=2，D=1"}
    result = workflow.run_workflow(**args, mode="simulated", runs_dir=tmp_path / "runs")
    assert result["status"] == "simulated_success"
    assert len(result["rounds"]) == 1 and result["corrections"] == []
    assert result["rounds"][0]["log_paths"] and result["rounds"][0]["runtime_blockers"]
    assert result["log_summary"]["log_path"]
    check_episode(result)


@pytest.mark.parametrize("fault,file", [
    ("missing-boundary", "0/U"), ("bad-transport", "constant/physicalProperties")])
def test_real_file_repair_then_validation_success(task_path, tmp_path, fault, file):
    template = PROJECT / "cases/templates/cylinder-2d"
    before = snapshot(template)
    input_before = task_path.read_bytes()
    result = workflow.run_workflow(task_path=task_path, mode="simulated",
                                   fault=fault, runs_dir=tmp_path / "runs")
    assert result["status"] == "simulated_success"
    assert [r["status"] for r in result["rounds"]] == ["validation_failed", "simulated_success"]
    assert result["diagnosis"]["correction_count"] == 1
    first, second = result["rounds"]
    assert first["log_paths"] == [] and result["injected_fault"]["name"] == fault
    first_case, second_case = Path(first["case_path"]), Path(second["case_path"])
    delta = result["corrections"][0]
    assert [change["file"] for change in delta["changes"]] == [file]
    change = delta["changes"][0]
    assert change["before"] != change["after"]
    assert (first_case / file).read_text(encoding="utf-8") == change["before"]
    assert (second_case / file).read_text(encoding="utf-8") == change["after"]
    assert not validate_case(result["task"], first_case)["config_valid"]
    assert validate_case(result["task"], second_case)["config_valid"]
    assert result["rounds"][0]["findings"]
    assert read_json(Path(first["execution_path"]))["steps"] == []
    first_snapshot = snapshot(first_case)
    assert snapshot(template) == before and task_path.read_bytes() == input_before
    assert read_json(Path(first["run_path"]) / "task.json") == read_json(Path(second["run_path"]) / "task.json")
    for relative, content in snapshot(first_case).items():
        if relative.replace("\\", "/") != file:
            assert (second_case / relative).read_bytes() == content
    report = check_episode(result)
    assert "执行前配置检查失败" in report and "0 → 1" in report
    assert snapshot(first_case) == first_snapshot


@pytest.mark.parametrize("input_text", ["{}", '{"x": NaN}', '{"task_id": "a", "task_id": "b"}',
                                      '{"physics":', "null"])
def test_invalid_input_has_episode_but_no_generated_case(tmp_path, input_text):
    path = tmp_path / "input.json"
    path.write_text(input_text, encoding="utf-8")
    result = workflow.run_workflow(task_path=path, mode="simulated", runs_dir=tmp_path / "runs")
    assert result["status"] == "failed" and result["rounds"] == []
    assert result["task"] is None and result["task_id"] is None
    assert result["case_summary"] is None and result["log_summary"] is None
    assert result["input"]["raw_text"] == input_text
    assert not (Path(result["workflow_path"]) / "reference").exists()
    check_episode(result)


@pytest.mark.parametrize("description", ["", "热传导", "cylinder flow Re=0"])
def test_planning_failure_is_reported(tmp_path, description):
    result = workflow.run_workflow(description, mode="simulated", runs_dir=tmp_path)
    assert result["status"] == "failed" and not result["rounds"]
    assert result["stop_reason"]["code"] == "INPUT_FAILED"
    check_episode(result)


def test_missing_input_and_generator_failure_are_recorded(tmp_path, monkeypatch):
    result = workflow.run_workflow(task_path=tmp_path / "missing", mode="simulated", runs_dir=tmp_path)
    assert result["input"]["raw_text"] is None
    check_episode(result)
    def fail(*args, **kwargs):
        raise ValueError("template unavailable")
    monkeypatch.setattr(workflow, "generate_case", fail)
    result = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    assert result["stop_reason"]["code"] == "GENERATION_FAILED"
    check_episode(result)


@pytest.mark.parametrize("task_budget,override,expected", [(0, None, 0), (0, 1, 1), (3, 0, 0)])
def test_budget_precedence(task, tmp_path, task_budget, override, expected):
    task["convergence"]["max_corrections"] = task_budget
    path = tmp_path / "input.json"
    path.write_text(json.dumps(task), encoding="utf-8")
    result = workflow.run_workflow(task_path=path, mode="simulated", max_corrections=override,
                                   fault="bad-transport", runs_dir=tmp_path / "runs")
    assert result["max_corrections"] == expected
    assert result["diagnosis"]["correction_count"] == min(expected, 1)
    assert result["status"] == ("failed" if expected == 0 else "simulated_success")
    if expected == 0:
        assert result["stop_reason"]["code"] == "CORRECTION_BUDGET_EXHAUSTED"
        assert len(result["rounds"]) == 1
    check_episode(result)


def test_default_budget_when_task_omits_convergence(task, tmp_path):
    task.pop("convergence")
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task), encoding="utf-8")
    result = workflow.run_workflow(task_path=path, mode="simulated", runs_dir=tmp_path / "runs")
    assert result["max_corrections"] == 2


def test_recurring_real_fault_exhausts_budget_without_infinite_retry(tmp_path, monkeypatch):
    actual = workflow.run_case
    def recurring(run_dir, **kwargs):
        inject_fault(run_dir / "case", "bad-transport")
        return actual(run_dir, **kwargs)
    monkeypatch.setattr(workflow, "run_case", recurring)
    result = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    assert len(result["rounds"]) == 3 and len(result["corrections"]) == 2
    assert result["stop_reason"]["code"] == "CORRECTION_BUDGET_EXHAUSTED"
    assert all(not r["log_paths"] for r in result["rounds"])
    check_episode(result)


@pytest.mark.parametrize("scenario,code", [("unknown-failure", "UNSUPPORTED_ERROR"),
                                          ("missing-boundary", "NO_EFFECTIVE_CHANGE")])
def test_log_failure_without_supported_file_change_stops(tmp_path, monkeypatch, scenario, code):
    actual = workflow.run_case
    def log_failure(run_dir, **kwargs):
        return actual(run_dir, **kwargs, scenario=scenario)
    monkeypatch.setattr(workflow, "run_case", log_failure)
    result = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    assert result["status"] == "failed" and not result["corrections"]
    assert result["stop_reason"]["code"] == code
    check_episode(result)


def test_real_mode_keeps_current_blockers(tmp_path, monkeypatch):
    from cfd_memo_agent.runner import execution
    def unavailable():
        raise OSError("OpenFOAM unavailable")
    monkeypatch.setattr(execution, "discover", unavailable)
    result = workflow.run_workflow("cylinder flow", mode="real", runs_dir=tmp_path)
    assert result["status"] == "blocked" and not result["corrections"]
    assert result["stop_reason"]["code"] == "ENVIRONMENT_ERROR"
    assert any(i["code"] == "MESH_NOT_VERIFIED" for i in result["rounds"][0]["runtime_blockers"])
    assert result["log_summary"] is None
    check_episode(result)


def test_timeout_stops_workflow(tmp_path, monkeypatch):
    actual = workflow.run_case
    def timeout(run_dir, **kwargs):
        result = actual(run_dir, **kwargs)
        result["status"] = "timeout"
        result["findings"] = [{"code": "TIMEOUT", "message": "timeout", "location": "icoFoam"}]
        return result
    monkeypatch.setattr(workflow, "run_case", timeout)
    result = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    assert result["status"] == "timeout" and len(result["rounds"]) == 1
    assert not result["corrections"]
    check_episode(result)


def test_interrupt_saves_episode(tmp_path, monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(workflow, "run_case", interrupt)
    result = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    assert result["status"] == "interrupted"
    assert result["rounds"][0]["execution_path"] is None
    assert not result["corrections"]
    check_episode(result)


def test_process_interrupt_terminates_child_before_reraising(tmp_path, monkeypatch):
    from cfd_memo_agent.runner import execution
    class Process:
        pid = 12345
        killed = False
        waits = 0
        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return -1
        def poll(self):
            return None
        def kill(self):
            self.killed = True
    process = Process()
    monkeypatch.setattr(execution.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(execution.subprocess, "run", lambda *a, **k: None)
    if hasattr(execution.os, "killpg"):
        monkeypatch.setattr(execution.os, "killpg", lambda *a: None)
    with pytest.raises(KeyboardInterrupt):
        execution._execute(["fake-command"], tmp_path, tmp_path / "interrupt.log", 10)
    assert process.killed and process.waits == 2


@pytest.mark.parametrize("options", [
    {}, {"description": "cylinder flow", "task_path": "task.json"},
    {"description": "cylinder flow", "mode": "unknown"},
    {"description": "cylinder flow", "max_corrections": -1},
    {"description": "cylinder flow", "max_corrections": True},
    {"description": "cylinder flow", "timeout": float("nan")},
    {"description": "cylinder flow", "mode": "real", "fault": "bad-transport"},
])
def test_bad_options_do_not_create_workflow(tmp_path, options):
    options.setdefault("mode", "simulated")
    with pytest.raises(ValueError):
        workflow.run_workflow(**options, runs_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_episode_schema_accepts_legacy_and_rejects_missing_v2_fields(tmp_path):
    episode_validator().validate(read_json(PROJECT / "examples/episode.sample.json"))
    result = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    for field in ("rounds", "runner_mode", "stop_reason"):
        damaged = deepcopy(result)
        del damaged[field]
        with pytest.raises(ValidationError):
            episode_validator().validate(damaged)
    damaged = deepcopy(result)
    damaged["physical_validated"] = True
    with pytest.raises(ValidationError):
        episode_validator().validate(damaged)
    before = Path(result["episode_path"]).read_bytes()
    with pytest.raises(FileExistsError):
        save_episode(Path(result["episode_path"]), result)
    assert Path(result["episode_path"]).read_bytes() == before


def test_repeated_workflows_keep_prior_records(tmp_path):
    first = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    before = snapshot(Path(first["workflow_path"]))
    second = workflow.run_workflow("cylinder flow", mode="simulated", runs_dir=tmp_path)
    assert first["workflow_path"] != second["workflow_path"]
    assert snapshot(Path(first["workflow_path"])) == before


@pytest.fixture
def repair_cases(task, tmp_path):
    reference = Path(generate_case(task, runs_dir=tmp_path / "reference")["case_path"])
    damaged = Path(generate_case(task, runs_dir=tmp_path / "damaged")["case_path"])
    return reference, damaged


@pytest.mark.parametrize("file,before,after", [
    ("0/U", "type            slip;", "type            noSlip;"),
    ("0/p", "type            zeroGradient;", "type            fixedValue;"),
    ("constant/physicalProperties", "[0 2 -1 0 0 0 0]", "[0 1 -1 0 0 0 0]"),
])
def test_repair_supported_field_changes(task, repair_cases, file, before, after):
    reference, damaged = repair_cases
    path = damaged / file
    path.write_text(path.read_text(encoding="utf-8").replace(before, after, 1), encoding="utf-8")
    errors = validate_case(task, damaged)["errors"]
    assert errors
    proposal = propose_repairs(task, damaged, reference, errors)
    assert [c["file"] for c in proposal["changes"]] == [file]
    apply_repairs(damaged, proposal["changes"])
    assert validate_case(task, damaged)["config_valid"]


def test_repair_refuses_unrelated_changes_and_damaged_syntax(task, repair_cases):
    reference, damaged = repair_cases
    inject_fault(damaged, "missing-boundary")
    path = damaged / "0/U"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("uniform (0 0 0)", "uniform (3 0 0)"), encoding="utf-8")
    proposal = propose_repairs(task, damaged, reference, validate_case(task, damaged)["errors"])
    assert proposal["changes"] == [] and proposal["stop_code"] == "UNSUPPORTED_ERROR"
    path.write_text(text + "{", encoding="utf-8")
    assert not propose_repairs(task, damaged, reference, validate_case(task, damaged)["errors"])["changes"]


def cli(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "cfd_memo_agent.cli", *map(str, args)],
                          cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=30)


def test_cli_workflow_and_fault_from_other_directory(tmp_path):
    result = cli(tmp_path, "run", "做 Re=100 的二维圆柱绕流", "--runner", "simulated",
                 "--fault", "missing-boundary", "--runs-dir", tmp_path / "runs")
    assert result.returncode == 0, result.stderr
    episode = json.loads(result.stdout)
    assert episode["status"] == "simulated_success" and len(episode["corrections"]) == 1
    check_episode(episode)


def test_cli_failed_task_and_zero_budget_exit_one(tmp_path, task_path):
    failed = cli(tmp_path, "run", "unhandled task", "--runner", "simulated", "--runs-dir", tmp_path)
    assert failed.returncode == 1 and json.loads(failed.stdout)["status"] == "failed"
    zero = cli(tmp_path, "run", "--task", task_path, "--runner", "simulated",
               "--fault", "bad-transport", "--max-corrections", 0, "--runs-dir", tmp_path)
    assert zero.returncode == 1 and json.loads(zero.stdout)["stop_reason"]["code"] == "CORRECTION_BUDGET_EXHAUSTED"


@pytest.mark.parametrize("args", [
    ["run", "--runner", "simulated"],
    ["run", "cylinder flow", "--task", "task.json", "--runner", "simulated"],
    ["run", "--run", "old", "--fault", "bad-transport", "--runner", "simulated"],
    ["run", "cylinder flow", "--scenario", "success", "--runner", "simulated"],
    ["run", "cylinder flow", "--fault", "bad-transport", "--runner", "real"],
])
def test_cli_invalid_combinations_exit_two(tmp_path, args):
    result = cli(tmp_path, *args)
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_cli_output_io_error_exit_two(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.write_text("file", encoding="utf-8")
    result = cli(tmp_path, "run", "cylinder flow", "--runner", "simulated", "--runs-dir", occupied)
    assert result.returncode == 2 and "Traceback" not in result.stderr
