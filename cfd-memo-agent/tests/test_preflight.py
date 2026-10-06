from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest

from cfd_memo_agent.planner import DEFAULT_CYLINDER_TASK
from cfd_memo_agent.preflight import (
    build_preflight, confirm_preflight, verify_confirmed_task,
)
from cfd_memo_agent.memory.episodes import episode_validator
from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.workflow import run_workflow


def write_task(path, task=None):
    value = deepcopy(DEFAULT_CYLINDER_TASK if task is None else task)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def test_preflight_shows_parameters_warnings_and_real_risks(tmp_path):
    path = tmp_path / "task.json"
    write_task(path)

    preview = build_preflight(path, runner_mode="real")

    assert preview["status"] == "ready"
    assert preview["parameters"]["reynolds_number"] == 100
    assert preview["parameters"]["target_cells"] == 10000
    assert preview["validation_warnings"]
    assert {item["code"] for item in preview["risks"]} == {
        "PHYSICS_NOT_YET_VALIDATED", "MESH_TARGET_IS_ESTIMATE",
        "RUNTIME_ENVIRONMENT_PENDING",
    }
    assert preview["confirmation_required"] is True
    assert len(preview["confirmation_token"]) == 64


def test_preflight_reads_saved_planner_assumptions_and_questions(tmp_path):
    path = tmp_path / "task.json"
    write_task(path)
    path.with_name("task.plan.json").write_text(json.dumps({
        "assumptions": ["入口使用默认均匀速度"],
        "questions": ["请确认流体温度"],
    }), encoding="utf-8")

    preview = build_preflight(path, runner_mode="real")

    assert preview["status"] == "needs_input"
    assert preview["assumptions"] == ["入口使用默认均匀速度"]
    assert preview["questions"] == ["请确认流体温度"]
    with pytest.raises(ValueError, match="不能执行"):
        confirm_preflight(preview, preview["confirmation_token"])


def test_confirmation_is_bound_to_task_and_options(tmp_path):
    path = tmp_path / "task.json"
    task = write_task(path)
    preview = build_preflight(path, runner_mode="real", timeout=30)
    confirmed = confirm_preflight(preview, preview["confirmation_token"])

    verify_confirmed_task(task, confirmed)
    changed = deepcopy(task)
    changed["time_control"]["end_time"] = 20
    with pytest.raises(ValueError, match="确认后发生变化"):
        verify_confirmed_task(changed, confirmed)
    changed_options = build_preflight(path, runner_mode="real", timeout=31)
    with pytest.raises(ValueError, match="确认码无效"):
        confirm_preflight(changed_options, preview["confirmation_token"])


def test_invalid_confirmation_is_rejected(tmp_path):
    path = tmp_path / "task.json"
    write_task(path)
    preview = build_preflight(path, runner_mode="real")

    with pytest.raises(ValueError, match="确认码无效"):
        confirm_preflight(preview, "0" * 64)


def test_workflow_records_confirmation_and_rechecks_task(tmp_path):
    path = tmp_path / "task.json"
    task = write_task(path)
    preview = build_preflight(path, runner_mode="simulated")
    confirmed = confirm_preflight(preview, preview["confirmation_token"])

    result = run_workflow(
        task_path=path, mode="simulated", runs_dir=tmp_path / "runs",
        confirmed_preflight=confirmed,
        model_settings=ModelSettings(provider="rules", model=None),
    )

    assert result["status"] == "simulated_success"
    assert result["preflight"]["task_sha256"] == preview["task_sha256"]
    root = tmp_path / "runs" / result["episode_id"]
    assert (root / "preflight.json").is_file()
    assert "执行前确认" in (root / "report.md").read_text(encoding="utf-8")
    episode_validator().validate(result)

    changed = deepcopy(task)
    changed["time_control"]["end_time"] = 20
    path.write_text(json.dumps(changed), encoding="utf-8")
    rejected = run_workflow(
        task_path=path, mode="simulated", runs_dir=tmp_path / "changed",
        confirmed_preflight=confirmed,
        model_settings=ModelSettings(provider="rules", model=None),
    )
    assert rejected["status"] == "failed"
    assert rejected["stop_reason"]["code"] == "INPUT_FAILED"
    assert not (tmp_path / "changed" / rejected["episode_id"] / "reference").exists()


def test_cli_real_task_previews_before_creating_workflow(tmp_path):
    path = tmp_path / "task.json"
    write_task(path)
    runs = tmp_path / "runs"
    env = {**os.environ, "CFD_MEMO_MODEL_PROVIDER": "rules"}

    command = [
        sys.executable, "-m", "cfd_memo_agent.cli", "run", "--task", str(path),
        "--runner", "real", "--runs-dir", str(runs),
    ]
    stopped = subprocess.run(
        command, text=True, encoding="utf-8", capture_output=True, env=env,
    )
    preview = json.loads(stopped.stdout)

    assert stopped.returncode == 1
    assert preview["status"] == "ready"
    assert preview["confirmation_required"] is True
    assert not runs.exists()

    explicit = subprocess.run(
        [*command, "--preview"], text=True, encoding="utf-8",
        capture_output=True, env=env,
    )
    assert explicit.returncode == 0
    assert json.loads(explicit.stdout)["confirmation_token"] == preview["confirmation_token"]

    invalid = subprocess.run(
        [*command, "--confirm-plan", "0" * 64],
        text=True, encoding="utf-8", capture_output=True, env=env,
    )
    assert invalid.returncode == 2
    assert "确认码无效" in invalid.stderr
    assert not runs.exists()
