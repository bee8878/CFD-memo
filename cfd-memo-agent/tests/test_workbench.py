from copy import deepcopy
import json

import pytest

from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.planner import DEFAULT_CYLINDER_TASK
from cfd_memo_agent.workbench import (
    create_task, explain_record, inspect_record, list_history,
)


RULES = ModelSettings(provider="rules", model=None)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def episode(*, episode_id="workflow-one", status="completed", physical=False):
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    return {
        "episode_id": episode_id,
        "task_id": task["task_id"],
        "task": task,
        "status": status,
        "runner_mode": "real",
        "physical_validated": physical,
        "rounds": [{
            "case_path": "C:/runs/case", "log_paths": ["C:/runs/log.icoFoam"],
        }],
        "corrections": [],
        "diagnosis": {"correction_count": 0},
        "memory": {"uses": [{"experience_id": "exp-1"}]},
        "stop_reason": {"code": "COMPLETED", "message": "done"},
        "input": {"task_path": "C:/tasks/task.json"},
        "report_path": "C:/runs/report.md",
    }


def test_create_task_saves_task_and_trace_without_running(tmp_path):
    target = tmp_path / "task.json"
    result = create_task(
        "做 Re=200 的二维圆柱绕流，入口速度=2，D=1",
        output=target,
        settings=RULES,
    )

    assert result["status"] == "ready"
    assert result["task_path"] == str(target.resolve())
    assert json.loads(target.read_text(encoding="utf-8"))["physics"]["reynolds_number"] == 200
    assert target.with_name("task.plan.json").is_file()
    assert not (tmp_path / "case").exists()


def test_create_task_refuses_to_overwrite(tmp_path):
    target = tmp_path / "task.json"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        create_task("做二维圆柱绕流", output=target, settings=RULES)


def test_inspect_task_reports_parameters_and_next_command(tmp_path):
    path = tmp_path / "task.json"
    write_json(path, DEFAULT_CYLINDER_TASK)

    result = inspect_record(path)

    assert result["record_type"] == "task"
    assert result["status"] == "ready"
    assert result["parameters"]["reynolds_number"] == 100
    assert "run --task" in result["next_action"]


def test_inspect_episode_unifies_status_evidence_and_memory(tmp_path):
    root = tmp_path / "workflow-one"
    path = root / "episode.json"
    write_json(path, episode())

    result = inspect_record(root)

    assert result["execution_completed"] is True
    assert result["physical_validated"] is False
    assert result["experience_ids"] == ["exp-1"]
    assert result["outputs"]["logs"] == ["C:/runs/log.icoFoam"]
    assert "物理可信度" in result["next_action"]


def test_history_is_recent_first_and_can_filter_status(tmp_path):
    old = tmp_path / "old" / "episode.json"
    new = tmp_path / "new" / "episode.json"
    write_json(old, episode(episode_id="old", status="failed"))
    write_json(new, episode(episode_id="new", status="completed"))
    old.touch()
    new.touch()

    result = list_history(tmp_path, status="completed")

    assert result["count"] == 1
    assert result["records"][0]["episode_id"] == "new"


def test_history_skips_invalid_episode_instead_of_crashing(tmp_path):
    write_json(tmp_path / "good" / "episode.json", episode())
    bad = tmp_path / "bad" / "episode.json"
    bad.parent.mkdir()
    bad.write_text("not json", encoding="utf-8")

    result = list_history(tmp_path)

    assert result["count"] == 1
    assert len(result["skipped"]) == 1


def test_explain_distinguishes_execution_from_physical_validation(tmp_path):
    path = tmp_path / "episode.json"
    write_json(path, episode())

    result = explain_record(path)

    assert "execution_completed" in result["meaning"]
    assert result["summary"]["physical_validated"] is False
    assert any(item.endswith("report.md") for item in result["evidence"])
