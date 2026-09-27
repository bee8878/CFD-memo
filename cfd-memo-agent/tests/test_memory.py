import json
from pathlib import Path

import pytest

from cfd_memo_agent.memory import MemoryManager
from cfd_memo_agent.memory.episodes import episode_validator
from cfd_memo_agent.validator.foam import read_json
from cfd_memo_agent.workflow import run_workflow


def test_structured_memory_survives_restart_and_is_cited_by_reviewer(tmp_path):
    memory_dir = tmp_path / "memory"
    first = run_workflow(
        "做 Re=100 的二维圆柱绕流，入口速度=1，D=1",
        mode="simulated", fault="missing-boundary",
        runs_dir=tmp_path / "runs-1", memory_mode="cfd_memo",
        memory_dir=memory_dir,
    )

    assert first["status"] == "simulated_success"
    assert first["schema_version"] == 3 and first["mode"] == "cfd_memo"
    assert not first["metrics"]["experience_reused"]
    assert len(first["memory"]["learned_experience_ids"]) == 1
    assert len(first["memory"]["learned_procedure_ids"]) == 1
    experience_id = first["memory"]["learned_experience_ids"][0]
    knowledge_path = memory_dir / "knowledge" / f"{experience_id}.json"
    assert read_json(knowledge_path)["status"] == "verified"
    assert Path(first["memory"]["archived_episode_path"]).is_file()
    episode_validator().validate(first)

    # run_workflow constructs a new MemoryManager, which proves disk-backed retrieval.
    second = run_workflow(
        "做 Re=200 的二维圆柱绕流，入口速度=2，D=1",
        mode="simulated", fault="missing-boundary",
        runs_dir=tmp_path / "runs-2", memory_mode="cfd_memo",
        memory_dir=memory_dir,
    )

    assert second["status"] == "simulated_success"
    assert second["metrics"]["experience_reused"]
    assert experience_id in second["memory"]["retrieved_experience_ids"]
    assert second["reviews"][0]["decision"] == "accept"
    assert second["reviews"][0]["experience_ids"] == []
    assert {item["agent"] for item in second["memory"]["uses"]} == {
        "planner", "case_writer",
    }
    assert second["planning"]["experience_ids"] == [experience_id]
    assert second["case_writing"]["experience_ids"] == [experience_id]
    assert second["case_writing"]["preventive_files"] == ["0/U"]
    assert {item["stage"] for item in second["memory"]["retrievals"]} == {
        "planner", "case_writer",
    }
    assert all(
        match["vector_score"] >= 0 and match["confidence"] > 0
        for retrieval in second["memory"]["retrievals"]
        for match in retrieval["matches"]
    )
    assert len(list((memory_dir / "episodes").glob("*.json"))) == 2
    assert second["preventions"] == [{
        "round_index": 0, "experience_ids": [experience_id], "files": ["0/U"],
        "action": "restore_from_trusted_reference",
    }]
    assert len(second["rounds"]) == 1
    assert second["rounds"][0]["status"] == "simulated_success"
    assert second["corrections"] == []
    assert len(read_json(knowledge_path)["evidence"]) == 1
    episode_validator().validate(second)


def test_vector_retrieval_understands_boundary_language_and_reynolds(tmp_path):
    memory_dir = tmp_path / "memory"
    first = run_workflow(
        "做 Re=100 的二维圆柱绕流，入口速度=1，D=1",
        mode="simulated", fault="missing-boundary", runs_dir=tmp_path / "runs",
        memory_mode="cfd_memo", memory_dir=memory_dir,
    )
    experience_id = first["memory"]["learned_experience_ids"][0]
    task = read_json(Path(__file__).resolve().parents[1] / "examples/task.cylinder-2d.json")
    task["physics"].update({
        "reynolds_number": 200, "inlet_velocity": 2.0, "kinematic_viscosity": 0.01,
    })
    manager = MemoryManager(memory_dir)
    manager.start_task(task)

    matches = manager.retrieve(
        task, query_text="入口速度边界缺失", stage="case_writer",
    )

    assert matches[0]["experience_id"] == experience_id
    assert matches[0]["retrieval_score"]["vector"] > 0
    assert matches[0]["retrieval_score"]["reynolds"] == 0.5


def test_repeated_failed_reuse_downweights_experience(tmp_path):
    memory_dir = tmp_path / "memory"
    first = run_workflow(
        "做 Re=100 的二维圆柱绕流", mode="simulated", fault="missing-boundary",
        runs_dir=tmp_path / "runs", memory_mode="cfd_memo", memory_dir=memory_dir,
    )
    experience_id = first["memory"]["learned_experience_ids"][0]
    task = first["task"]
    manager = MemoryManager(memory_dir)
    manager.start_task(task)
    for index in range(2):
        manager.note_use(
            [experience_id], agent="planner", round_index=0,
            decision="plan_with_memory", effect="added_planning_guardrails",
        )
        manager.record_use_outcomes({
            "episode_id": f"failed-{index}", "status": "failed",
            "metrics": {"config_valid": False},
        })

    record = read_json(memory_dir / "knowledge" / f"{experience_id}.json")
    assert record["trust"]["negative_outcomes"] == 2
    assert record["trust"]["confidence"] < 0.5
    assert record["status"] == "candidate"
    assert manager.retrieve(task, query_text="圆柱边界缺失") == []


def test_simple_cache_only_reuses_an_exact_task(tmp_path):
    store = tmp_path / "cache-store"
    run_workflow(
        "做 Re=100 的二维圆柱绕流，入口速度=1，D=1",
        mode="simulated", runs_dir=tmp_path / "train", memory_mode="simple_cache",
        memory_dir=store,
    )
    repeated = run_workflow(
        "做 Re=100 的二维圆柱绕流，入口速度=1，D=1",
        mode="simulated", fault="missing-boundary", runs_dir=tmp_path / "repeat",
        memory_mode="simple_cache", memory_dir=store, memory_learning=False,
    )
    novel = run_workflow(
        "做 Re=200 的二维圆柱绕流，入口速度=2，D=1",
        mode="simulated", fault="missing-boundary", runs_dir=tmp_path / "novel",
        memory_mode="simple_cache", memory_dir=store, memory_learning=False,
    )

    assert repeated["memory"]["cache_hit"]
    assert len(repeated["rounds"]) == 1 and repeated["corrections"] == []
    assert not novel["memory"]["cache_hit"]
    assert len(novel["rounds"]) == 2 and len(novel["corrections"]) == 1


def test_retrieval_only_uses_reviewer_but_does_not_learn_or_prevent(tmp_path):
    store = tmp_path / "knowledge-store"
    trained = run_workflow(
        "做 Re=100 的二维圆柱绕流", mode="simulated", fault="missing-boundary",
        runs_dir=tmp_path / "train", memory_mode="cfd_memo", memory_dir=store,
    )
    experience_id = trained["memory"]["learned_experience_ids"][0]
    evidence_before = read_json(store / "knowledge" / f"{experience_id}.json")["evidence"]

    evaluated = run_workflow(
        "做 Re=200 的二维圆柱绕流，入口速度=2，D=1",
        mode="simulated", fault="missing-boundary", runs_dir=tmp_path / "evaluate",
        memory_mode="retrieval_only", memory_dir=store, memory_learning=False,
    )

    assert evaluated["planning"]["experience_ids"] == []
    assert evaluated["case_writing"]["experience_ids"] == []
    assert evaluated["reviews"][0]["experience_ids"] == [experience_id]
    assert [item["stage"] for item in evaluated["memory"]["retrievals"]] == ["reviewer"]
    assert len(evaluated["rounds"]) == 2 and len(evaluated["corrections"]) == 1
    assert evaluated["memory"]["learned_experience_ids"] == []
    assert read_json(store / "knowledge" / f"{experience_id}.json")["evidence"] == evidence_before


def test_no_memory_mode_keeps_v2_and_does_not_create_store(tmp_path):
    memory_dir = tmp_path / "unused-memory"
    result = run_workflow(
        "cylinder flow Re=100", mode="simulated", runs_dir=tmp_path / "runs",
    )

    assert result["schema_version"] == 2 and result["mode"] == "no_memory"
    assert "memory" not in result and not memory_dir.exists()
    assert not result["metrics"]["experience_reused"]
    episode_validator().validate(result)


def test_memory_rejects_corrupt_or_inapplicable_knowledge(tmp_path):
    manager = MemoryManager(tmp_path / "memory")
    task = read_json(Path(__file__).resolve().parents[1] / "examples/task.cylinder-2d.json")
    manager.start_task(task)
    corrupt = manager.knowledge_dir / "broken.json"
    corrupt.write_text(json.dumps({"experience_id": "bad"}), encoding="utf-8")

    with pytest.raises(ValueError, match="损坏的经验记录"):
        manager.retrieve(task)


@pytest.mark.parametrize("options", [
    {"memory_mode": "unknown"},
    {"memory_mode": "no_memory", "memory_dir": "unused"},
])
def test_invalid_memory_options_create_no_workflow(tmp_path, options):
    with pytest.raises(ValueError):
        run_workflow(
            "cylinder flow", mode="simulated", runs_dir=tmp_path / "runs", **options,
        )
    assert not (tmp_path / "runs").exists()
