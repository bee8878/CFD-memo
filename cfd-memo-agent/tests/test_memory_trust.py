import hashlib
import json
from pathlib import Path
import subprocess
import sys

from cfd_memo_agent.memory import MemoryManager
from cfd_memo_agent.memory.transfer import evaluate_transfer, evaluate_transfer_manifest
from cfd_memo_agent.validator.foam import read_json


def _save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _evidence_episode(tmp_path, *, physical=False, tampered=False,
                      change_file="0/U", episode_id="episode-trust", task_id="task-trust"):
    run = tmp_path / "run"
    attempt = run / "attempts" / "attempt-a"
    validation_path = attempt / "validation.json"
    execution_path = attempt / "execution.json"
    _save(validation_path, {"config_valid": True})
    _save(execution_path, {"status": "completed"})
    mesh_path = attempt / "mesh-evidence.json"
    result_path = attempt / "result-evidence.json"
    log_path = attempt / "icoFoam.log"
    velocity_path = run / "case" / "1" / "U"
    pressure_path = run / "case" / "1" / "p"
    _save(mesh_path, {"mesh_verified": True})
    _save(result_path, {"fields_valid": True})
    log_path.write_text("End\n", encoding="utf-8")
    velocity_path.parent.mkdir(parents=True, exist_ok=True)
    velocity_path.write_text("U", encoding="utf-8")
    pressure_path.write_text("p", encoding="utf-8")
    artifacts = []
    for role, path in (
        ("evidence", validation_path), ("evidence", execution_path),
        ("evidence", mesh_path), ("evidence", result_path), ("log", log_path),
        ("field", velocity_path), ("field", pressure_path),
    ):
        data = path.read_bytes()
        artifacts.append({
            "role": role, "path": str(path.relative_to(run)),
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
    _save(attempt / "result-index.json", {
        "schema_version": 1, "status": "completed", "attempt_path": str(attempt),
        "input_sha256": "0" * 64, "physical_validated": physical,
        "artifacts": artifacts,
    })
    if tampered:
        execution_path.write_text("tampered", encoding="utf-8")
    task = {
        "task_id": task_id, "case_type": "cylinder-2d", "solver": "icoFoam",
        "geometry": {"dimension": "2D"},
        "physics": {"flow_model": "incompressible_laminar", "reynolds_number": 100},
    }
    return {
        "episode_id": episode_id, "task_id": task_id, "runner_mode": "real",
        "status": "completed", "task": task,
        "reflection": {"reusable_rules": ["模型声称物理验证完成"]},
        "rounds": [{
            "index": 1, "run_path": str(run), "status": "completed", "config_valid": True,
            "validation_path": str(validation_path), "execution_path": str(execution_path),
            "result_evidence": {"physical_validated": physical},
        }],
        "corrections": [{
            "from_round": 0, "to_round": 1, "reason_codes": ["BOUNDARY_MISSING"],
            "changes": [{"file": change_file}],
        }],
    }, task


def test_real_attempt_evidence_promotes_to_run_verified(tmp_path):
    episode, _ = _evidence_episode(tmp_path)
    manager = MemoryManager(tmp_path / "memory")

    learned = manager.learn_from_episode(episode)
    record = manager.get_experience(learned["experience_ids"][0])

    assert record["status"] == "run_verified"
    assert record["verification_scope"] == "run"
    assert record["evidence"][0]["derived_level"] == "run_verified"
    assert record["evidence"][0]["integrity_verified"]


def test_physics_level_requires_machine_evidence_not_reflection(tmp_path):
    episode, _ = _evidence_episode(tmp_path / "unverified", physical=False)
    manager = MemoryManager(tmp_path / "memory")
    first = manager.learn_from_episode(episode)
    assert manager.get_experience(first["experience_ids"][0])["status"] == "run_verified"

    verified, _ = _evidence_episode(tmp_path / "verified", physical=True)
    verified["episode_id"] = "episode-physics"
    second = manager.learn_from_episode(verified)
    assert manager.get_experience(second["experience_ids"][0])["status"] == "physics_verified"


def test_tampered_result_index_cannot_reach_run_verified(tmp_path):
    episode, _ = _evidence_episode(tmp_path, tampered=True)
    manager = MemoryManager(tmp_path / "memory")

    learned = manager.learn_from_episode(episode)
    record = manager.get_experience(learned["experience_ids"][0])

    assert record["status"] == "config_verified"
    assert all(item["role"] != "result_index" for item in record["evidence"][0]["artifacts"])


def test_user_can_disable_approve_and_delete_experience(tmp_path):
    episode, task = _evidence_episode(tmp_path)
    manager = MemoryManager(tmp_path / "memory")
    experience_id = manager.learn_from_episode(episode)["experience_ids"][0]

    approved = manager.set_user_control(experience_id, approved=True, note="人工复核")
    assert approved["user_control"]["approved"]
    assert manager.retrieve(task, problem_codes=["BOUNDARY_MISSING"])

    disabled = manager.set_user_control(experience_id, state="disabled")
    assert disabled["user_control"]["state"] == "disabled"
    assert manager.retrieve(task, problem_codes=["BOUNDARY_MISSING"]) == []

    manager.delete_experience(experience_id)
    assert manager.list_experiences() == []


def test_legacy_verified_record_is_migrated_to_untrusted_candidate(tmp_path):
    manager = MemoryManager(tmp_path / "memory")
    path = manager.knowledge_dir / "exp-0123456789abcdef.json"
    _save(path, {
        "schema_version": 1, "experience_id": "exp-0123456789abcdef",
        "record_type": "repair_knowledge", "status": "verified",
        "problem_codes": ["BOUNDARY_MISSING"],
        "applicability": {"case_type": "cylinder-2d", "solver": "icoFoam",
                          "flow_model": "incompressible_laminar", "dimension": "2D"},
        "action": {"type": "restore_from_trusted_reference", "files": ["0/U"]},
        "evidence": [{"episode_id": "old", "from_round": 0, "to_round": 1,
                      "outcome_status": "simulated_success"}],
        "verification_scope": "configuration",
        "trust": {"positive_outcomes": 1, "negative_outcomes": 0, "confidence": 0.6,
                  "evaluated_episode_ids": ["old"]},
        "created_at": "old", "updated_at": "old",
    })

    record = manager.get_experience("exp-0123456789abcdef")

    assert record["schema_version"] == 3
    assert record["status"] == "candidate"
    assert not record["evidence"][0]["integrity_verified"]
    assert read_json(path)["schema_version"] == 1


def test_conflicting_experiences_are_blocked_revised_and_resolved(tmp_path):
    manager = MemoryManager(tmp_path / "memory")
    first, task = _evidence_episode(
        tmp_path / "first", change_file="0/U", episode_id="episode-u")
    second, _ = _evidence_episode(
        tmp_path / "second", change_file="0/p", episode_id="episode-p")
    first_id = manager.learn_from_episode(first)["experience_ids"][0]
    second_learned = manager.learn_from_episode(second)
    second_id = second_learned["experience_ids"][0]

    assert set(second_learned["conflict_ids"]) == {first_id, second_id}
    left = manager.get_experience(first_id)
    right = manager.get_experience(second_id)
    assert left["conflict_state"] == right["conflict_state"] == "conflicted"
    assert "conflict_detected" in {item["event"] for item in left["revision_history"]}
    assert manager.retrieve(task, problem_codes=["BOUNDARY_MISSING"]) == []

    resolution = manager.resolve_conflict(first_id, second_id, note="保留速度场修正规则")

    assert resolution["preferred"]["conflict_state"] == "clear"
    assert resolution["preferred"]["user_control"]["approved"]
    assert resolution["rejected"]["user_control"]["state"] == "disabled"
    assert manager.retrieve(task, problem_codes=["BOUNDARY_MISSING"])[0]["experience_id"] == first_id
    revisions = resolution["preferred"]["revision_history"]
    assert [item["revision"] for item in revisions] == list(range(1, len(revisions) + 1))


def test_real_cross_task_transfer_acceptance_and_manifest(tmp_path):
    manager = MemoryManager(tmp_path / "memory")
    source, _ = _evidence_episode(
        tmp_path / "source", episode_id="episode-source", task_id="task-source")
    experience_id = manager.learn_from_episode(source)["experience_ids"][0]
    source["memory"] = {"learned_experience_ids": [experience_id]}
    target = {
        "episode_id": "episode-target", "task_id": "task-target",
        "task": {"task_id": "task-target"}, "runner_mode": "real", "status": "completed",
        "corrections": [], "memory": {"uses": [{
            "experience_id": experience_id, "outcome": "effective",
        }]},
    }

    result = evaluate_transfer(source, target, manager)

    assert result["status"] == "passed"
    source_path = tmp_path / "source-episode.json"
    target_path = tmp_path / "target-episode.json"
    _save(source_path, source)
    _save(target_path, target)
    manifest = tmp_path / "manifest.json"
    _save(manifest, {"schema_version": 1, "cases": [{
        "case_id": "real-transfer", "source_episode": source_path.name,
        "target_episode": target_path.name,
    }]})
    evaluated = evaluate_transfer_manifest(manifest, manager)
    assert evaluated["status"] == "passed" and evaluated["case_count"] == 1
    output = tmp_path / "acceptance-result.json"
    cli = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "memory", "acceptance",
         "--manifest", str(manifest), "--memory-dir", str(manager.root),
         "--output", str(output)],
        cwd=tmp_path, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    assert cli.returncode == 0
    assert read_json(output)["status"] == "passed"


def test_transfer_acceptance_rejects_simulated_target(tmp_path):
    manager = MemoryManager(tmp_path / "memory")
    source, _ = _evidence_episode(tmp_path / "source")
    experience_id = manager.learn_from_episode(source)["experience_ids"][0]
    source["memory"] = {"learned_experience_ids": [experience_id]}
    target = {"episode_id": "target", "task_id": "other", "runner_mode": "simulated",
              "status": "simulated_success", "corrections": [],
              "memory": {"uses": [{"experience_id": experience_id,
                                     "outcome": "effective"}]}}

    result = evaluate_transfer(source, target, manager)

    assert result["status"] == "failed"
    assert not next(item for item in result["checks"] if item["code"] == "TARGET_REAL")["passed"]


def test_memory_cli_lists_and_disables_from_another_directory(tmp_path):
    episode, _ = _evidence_episode(tmp_path)
    store = tmp_path / "memory"
    manager = MemoryManager(store)
    experience_id = manager.learn_from_episode(episode)["experience_ids"][0]

    listed = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "memory", "list",
         "--memory-dir", str(store)],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )
    disabled = subprocess.run(
        [sys.executable, "-m", "cfd_memo_agent.cli", "memory", "disable",
         experience_id, "--memory-dir", str(store), "--note", "测试停用"],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )

    assert listed.returncode == 0
    assert json.loads(listed.stdout)["records"][0]["experience_id"] == experience_id
    assert disabled.returncode == 0
    assert json.loads(disabled.stdout)["user_control"]["state"] == "disabled"


def test_extract_accepts_stable_historical_fields_and_rejects_malformed_episode(tmp_path):
    episode, _ = _evidence_episode(tmp_path / "source")
    source = tmp_path / "episode.json"
    _save(source, episode)
    manager = MemoryManager(tmp_path / "memory")

    extracted = manager.extract_episode_file(source)

    assert len(extracted["experience_ids"]) == 1
    episode.pop("runner_mode")
    _save(tmp_path / "broken.json", episode)
    try:
        manager.extract_episode_file(tmp_path / "broken.json")
    except ValueError as exc:
        assert "稳定字段" in str(exc)
    else:
        raise AssertionError("malformed episode should be rejected")
