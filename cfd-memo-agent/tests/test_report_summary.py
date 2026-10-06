from copy import deepcopy
import json

from cfd_memo_agent.memory.episodes import episode_validator
from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.planner import DEFAULT_CYLINDER_TASK
from cfd_memo_agent.reporter.summary import build_report_summary, summary_validator
from cfd_memo_agent.workflow import run_workflow


RULES = ModelSettings(provider="rules", model=None)


def test_workflow_writes_five_answer_report_and_json_summary(tmp_path):
    result = run_workflow(
        "做 Re=100 的二维圆柱绕流", mode="simulated", runs_dir=tmp_path,
        model_settings=RULES,
    )
    root = tmp_path / result["episode_id"]
    summary = json.loads((root / "report-summary.json").read_text(encoding="utf-8"))
    report = (root / "report.md").read_text(encoding="utf-8")

    summary_validator().validate(summary)
    assert summary["execution"]["completed"] is True
    assert summary["physical_result"]["verdict"] == "not_validated"
    assert summary["memory_contribution"]["mode"] == "no_memory"
    assert summary["next_steps"]
    for heading in ("做了什么", "是否运行完成", "物理结果是否可信", "经验起了什么作用", "下一步"):
        assert heading in report


def test_summary_attributes_only_effective_memory_use():
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    episode = {
        "episode_id": "workflow-memory", "task_id": task["task_id"], "task": task,
        "input": {"description": None, "task_path": "task.json"},
        "status": "completed", "runner_mode": "real", "mode": "cfd_memo",
        "physical_validated": False,
        "rounds": [{"case_path": "case", "log_paths": ["run.log"],
                    "result_evidence": {"fields": {"U": "10/U", "p": "10/p"}}}],
        "diagnosis": {"correction_count": 0}, "corrections": [],
        "metrics": {"elapsed_seconds": 1.5},
        "stop_reason": {"code": "COMPLETED", "message": "done"},
        "preventions": [{"experience_ids": ["exp-effective"], "files": ["0/U"]}],
        "memory": {
            "retrieved_experience_ids": ["exp-effective", "exp-unused", "exp-bad"],
            "uses": [
                {"experience_id": "exp-effective", "outcome": "effective"},
                {"experience_id": "exp-bad", "outcome": "ineffective"},
            ],
            "learned_experience_ids": ["exp-new"], "learned_procedure_ids": [],
        },
        "episode_path": "episode.json", "report_path": "report.md",
    }

    summary = build_report_summary(episode)

    memory = summary["memory_contribution"]
    assert memory["used_experience_ids"] == ["exp-effective", "exp-bad"]
    assert memory["effective_experience_ids"] == ["exp-effective"]
    assert memory["prevented_files"] == ["0/U"]
    assert "有效引用" in memory["conclusion"]
    assert summary["physical_result"]["validated"] is False


def test_failed_episode_reports_no_completed_physics_and_action():
    task = deepcopy(DEFAULT_CYLINDER_TASK)
    episode = {
        "episode_id": "workflow-failed", "task_id": task["task_id"], "task": task,
        "input": {"description": None, "task_path": "task.json"},
        "status": "failed", "runner_mode": "real", "mode": "no_memory",
        "physical_validated": False, "rounds": [], "diagnosis": {"correction_count": 0},
        "corrections": [], "metrics": {"elapsed_seconds": 0.2},
        "stop_reason": {"code": "TASK_INVALID", "message": "参数不一致"},
        "preventions": [], "episode_path": "episode.json", "report_path": "report.md",
    }

    summary = build_report_summary(episode)

    assert summary["execution"]["completed"] is False
    assert summary["physical_result"]["verdict"] == "no_completed_run"
    assert "参数不一致" in summary["next_steps"][0]
