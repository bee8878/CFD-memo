from copy import deepcopy
import json
from pathlib import Path

from cfd_memo_agent.physical_study import assess_physical_study, continue_physical_study


def record(name, cd=1.35, cl=0.28, st=0.164, signal=True):
    return {
        "name": name,
        "actual_cells": 10000,
        "task": {"time_control": {"delta_t": 0.005}},
        "force_coefficients": {
            "mean_cd": cd, "cl_amplitude": cl, "strouhal": st,
            "signal_valid": signal,
            "reference_comparison": {"passed": True} if name == "baseline" else None,
        },
    }


def complete_records():
    return [record("baseline"), record("mesh-coarse", 1.38, 0.3, 0.167),
            record("mesh-fine", 1.34, 0.27, 0.163),
            record("mesh-extra-fine", 1.335, 0.269, 0.1625),
            record("mesh-local-fine", 1.334, 0.268, 0.1624),
            record("mesh-local-extra-fine", 1.332, 0.267, 0.1622),
            record("time-coarse", 1.37, 0.29, 0.166),
            record("time-fine", 1.345, 0.275, 0.1635)]


def test_physical_study_requires_all_cases_reference_signal_and_independence():
    records = complete_records()
    next(item for item in records if item["name"] == "mesh-local-extra-fine")[
        "force_coefficients"]["reference_comparison"] = {"passed": True}
    result = assess_physical_study(records)
    assert result["physical_validated"]
    assert result["mesh_independence"]["passed"]
    assert result["time_step_independence"]["passed"]


def test_physical_study_reports_missing_and_failed_gates():
    assert "time-fine" in assess_physical_study(complete_records()[:-1])["missing_cases"]
    damaged = deepcopy(complete_records())
    extra = next(item for item in damaged if item["name"] == "mesh-local-extra-fine")
    extra["force_coefficients"]["reference_comparison"] = {"passed": False}
    extra["force_coefficients"]["mean_cd"] = 1.5
    result = assess_physical_study(damaged)
    assert not result["physical_validated"]
    assert not result["reference_passed"] and not result["mesh_independence"]["passed"]


def test_continue_physical_study_runs_only_missing_variant(tmp_path, monkeypatch):
    records = [item for item in complete_records() if item["name"] != "mesh-local-extra-fine"]
    study = {"study_id": "study", "status": "failed", "error": "stale failure",
             "physical_validated": False, "cases": records, "assessment": None}
    (tmp_path / "study.json").write_text(json.dumps(study), encoding="utf-8")
    task_path = Path(__file__).parents[1] / "examples/task.cylinder-2d-physical.json"
    calls = []

    def fake_workflow(*, task_path, **kwargs):
        task = json.loads(Path(task_path).read_text(encoding="utf-8"))
        calls.append(task)
        force = record("mesh-local-extra-fine", 1.332, 0.267, 0.1622)["force_coefficients"]
        force["reference_comparison"] = {"passed": True}
        return {"runner_mode": "real", "status": "completed", "task": task,
                "episode_path": "episode.json", "report_path": "report.md",
                "rounds": [{"mesh_evidence": {"actual_cells": 40328},
                            "result_evidence": {"force_coefficients": force}}]}

    monkeypatch.setattr("cfd_memo_agent.physical_study.run_workflow", fake_workflow)
    result = continue_physical_study(study_path=tmp_path, task_path=task_path)

    assert [task["task_id"].rsplit("-", 4)[-4:] for task in calls] == [
        ["mesh", "local", "extra", "fine"]]
    assert result["physical_validated"]
    assert "error" not in result
    assert {item["name"] for item in result["cases"]} == {
        "baseline", "mesh-coarse", "mesh-fine", "mesh-extra-fine",
        "mesh-local-fine", "mesh-local-extra-fine",
        "time-coarse", "time-fine"}
