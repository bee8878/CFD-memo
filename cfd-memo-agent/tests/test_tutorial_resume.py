import json
from pathlib import Path
import subprocess
import sys

import pytest

from cfd_memo_agent.memory.episodes import episode_validator
from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.tutorial_builder import build_backward_step_task
from cfd_memo_agent.tutorial_resume import resume_tutorial_workflow
from cfd_memo_agent.workflow import run_workflow


TUTORIAL_ID = "incompressible/simpleFoam/pitzDaily"


def tutorial_context():
    return {
        "status": "ready", "query": "做二维后台阶流动", "index_path": "index.json",
        "candidate_count": 1, "error": None,
        "candidates": [{
            "tutorial_id": TUTORIAL_ID,
            "case_path": "/opt/openfoam10/tutorials/incompressible/simpleFoam/pitzDaily",
            "solver": "simpleFoam", "physics_model": "incompressible_rans",
            "fields": ["U", "p"],
            "boundaries": {"inlet": ["fixedValue"], "outlet": ["zeroGradient"]},
            "boundary_types": ["fixedValue", "zeroGradient"],
            "mesh_tools": ["blockMesh"],
            "config_files": ["system/controlDict", "system/blockMeshDict"],
            "warnings": [], "score": 20, "match_reasons": ["alias:后台阶"],
        }],
    }


def proposal_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cfd_memo_agent.orchestrator.TutorialRetriever.retrieve",
        lambda self, query: tutorial_context(),
    )
    episode = run_workflow(
        "做二维后台阶流动", mode="simulated", runs_dir=tmp_path,
        model_settings=ModelSettings(provider="rules", model=None),
    )
    return tmp_path / episode["episode_id"]


def fake_build(proposal, *, index_path, runs_dir):
    root = Path(runs_dir) / "tutorial-run"
    (root / "case").mkdir(parents=True)
    task = build_backward_step_task("0" * 64)
    (root / "task.json").write_text(json.dumps(task), encoding="utf-8")
    (root / "generation.json").write_text(
        json.dumps({"generated_files": ["0/U", "0/p"]}), encoding="utf-8")
    build = {
        "status": "built", "run_path": str(root),
        "capability_id": "openfoam10-pitzdaily-v1", "tutorial_id": TUTORIAL_ID,
        "source_manifest_sha256": "0" * 64,
        "validation": {"config_valid": True},
    }
    (root / "tutorial-build.json").write_text(json.dumps(build), encoding="utf-8")
    return build


def fake_run(run_path, *, mode, timeout):
    root = Path(run_path)
    attempt = root / "attempt-test"
    attempt.mkdir()
    for name in ("execution.json", "validation.json", "simpleFoam.log"):
        (attempt / name).write_text("{}", encoding="utf-8")
    return {
        "status": "completed", "attempt_path": str(attempt),
        "case_path": str(root / "case"),
        "validation": {
            "config_valid": True,
            "runtime_blockers": [{
                "code": "MESH_NOT_VERIFIED", "message": "运行前尚未检查网格",
                "location": "system/blockMeshDict",
            }],
        },
        "environment": {"backend": "wsl", "version": "10"},
        "mesh_evidence": {"actual_cells": 12225},
        "result_evidence": {
            "final_iteration": 287, "configured_end_iteration": 2000,
            "stopped_before_end": True,
            "fields": {"U": str(root / "case/287/U"), "p": str(root / "case/287/p")},
            "actual_cells": 12225, "physical_validated": False,
        },
        "steps": [{
            "log_path": str(attempt / "simpleFoam.log"),
            "diagnosis": {"residuals": [{"field": "U", "final": 1e-6}]},
        }],
        "findings": [],
    }


def test_resume_requires_explicit_approval(tmp_path, monkeypatch):
    parent = proposal_workflow(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="显式使用"):
        resume_tutorial_workflow(parent, approved=False, mode="real")

    assert not (parent / "resumes").exists()


def test_resume_preserves_parent_and_records_real_execution(tmp_path, monkeypatch):
    parent = proposal_workflow(tmp_path, monkeypatch)
    before = (parent / "episode.json").read_bytes()
    monkeypatch.setattr("cfd_memo_agent.tutorial_resume.build_tutorial_case", fake_build)
    monkeypatch.setattr("cfd_memo_agent.tutorial_resume.run_case", fake_run)

    result = resume_tutorial_workflow(parent, approved=True, mode="real")
    resume_root = Path(result["workflow_path"])

    assert result["status"] == "completed"
    assert result["runner_mode"] == "real" and result["physical_validated"] is False
    assert result["tutorial_resume"]["parent_workflow_path"] == str(parent.resolve())
    assert json.loads((resume_root / "approval.json").read_text())["approved"] is True
    assert (parent / "episode.json").read_bytes() == before
    assert resume_root.parent == parent / "resumes"
    assert result["rounds"][0]["runtime_blockers"] == []
    report = (resume_root / "report.md").read_text(encoding="utf-8")
    assert "教程批准链" in report and "最终迭代：287 / 配置上限 2000" in report
    assert "物理准确性已验证" in report
    episode_validator().validate(result)


def test_resume_rejects_tampered_saved_proposal(tmp_path, monkeypatch):
    parent = proposal_workflow(tmp_path, monkeypatch)
    proposal_path = parent / "case-spec-proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["solver"] = "icoFoam"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    with pytest.raises(ValueError, match="不一致"):
        resume_tutorial_workflow(parent, approved=True, mode="real")

    assert not (parent / "resumes").exists()


def test_build_failure_is_saved_as_a_child_episode(tmp_path, monkeypatch):
    parent = proposal_workflow(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "cfd_memo_agent.tutorial_resume.build_tutorial_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("source changed")),
    )

    result = resume_tutorial_workflow(parent, approved=True, mode="real")

    assert result["status"] == "failed"
    assert result["stop_reason"]["code"] == "TUTORIAL_BUILD_FAILED"
    assert Path(result["episode_path"]).is_file()
    assert result["rounds"] == [] and result["task"] is None
    episode_validator().validate(result)


def test_invalid_build_stops_before_runner(tmp_path, monkeypatch):
    parent = proposal_workflow(tmp_path, monkeypatch)

    def invalid_build(*args, **kwargs):
        result = fake_build(*args, **kwargs)
        result["status"] = "invalid"
        result["validation"]["config_valid"] = False
        return result

    monkeypatch.setattr("cfd_memo_agent.tutorial_resume.build_tutorial_case", invalid_build)
    monkeypatch.setattr(
        "cfd_memo_agent.tutorial_resume.run_case",
        lambda *args, **kwargs: pytest.fail("invalid build must not reach runner"),
    )

    result = resume_tutorial_workflow(parent, approved=True, mode="real")

    assert result["status"] == "failed"
    assert result["stop_reason"]["code"] == "TUTORIAL_BUILD_INVALID"
    assert result["rounds"] == []
    episode_validator().validate(result)


def test_resume_cli_refuses_missing_approval_without_traceback(tmp_path, monkeypatch):
    parent = proposal_workflow(tmp_path, monkeypatch)
    completed = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "resume",
         "--workflow", str(parent), "--runner", "real"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
    )

    assert completed.returncode == 2
    assert "--approve-tutorial" in completed.stderr
    assert "Traceback" not in completed.stderr
