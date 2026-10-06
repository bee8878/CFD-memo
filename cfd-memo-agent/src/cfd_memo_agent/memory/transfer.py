"""Acceptance checks for evidence-backed experience transfer between real tasks."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from cfd_memo_agent.validator.foam import read_json

from .episodes import write_record


def _task_id(episode: dict[str, Any]) -> str | None:
    task = episode.get("task")
    return episode.get("task_id") or (task.get("task_id") if isinstance(task, dict) else None)


def _check(code: str, passed: bool, message: str) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), "message": message}


def evaluate_transfer(source: dict[str, Any], target: dict[str, Any], manager) -> dict[str, Any]:
    """Verify that a real corrected source experience helped a different real task."""
    source_ids = set((source.get("memory") or {}).get("learned_experience_ids", []))
    target_uses = (target.get("memory") or {}).get("uses", [])
    effective_ids = {
        item.get("experience_id") for item in target_uses
        if item.get("outcome") == "effective"
    }
    reused = sorted(source_ids.intersection(effective_ids))
    trusted = []
    for experience_id in reused:
        try:
            record = manager.get_experience(experience_id)
        except ValueError:
            continue
        if record["status"] in {"run_verified", "physics_verified"}:
            trusted.append(experience_id)
    source_corrections = len(source.get("corrections", []))
    target_corrections = len(target.get("corrections", []))
    source_task = _task_id(source)
    target_task = _task_id(target)
    checks = [
        _check("SOURCE_REAL", source.get("runner_mode") == "real",
               "来源 episode 必须是真实 runner。"),
        _check("SOURCE_RECOVERED", source.get("status") == "completed" and source_corrections > 0,
               "来源任务必须经历至少一次修正并真实完成。"),
        _check("TARGET_REAL", target.get("runner_mode") == "real" and target.get("status") == "completed",
               "目标 episode 必须真实完成。"),
        _check("DISTINCT_TASK", bool(source_task and target_task and source_task != target_task),
               "来源和目标必须是不同任务。"),
        _check("EVIDENCE_REUSED", bool(reused),
               "目标任务必须有效引用来源任务学习的经验。"),
        _check("RUN_VERIFIED_MEMORY", bool(trusted),
               "被复用经验必须至少达到 run_verified。"),
        _check("FEWER_CORRECTIONS", target_corrections < source_corrections,
               "目标任务修正次数必须少于来源任务。"),
    ]
    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "source_episode_id": source.get("episode_id"),
        "target_episode_id": target.get("episode_id"),
        "source_task_id": source_task, "target_task_id": target_task,
        "source_corrections": source_corrections,
        "target_corrections": target_corrections,
        "reused_experience_ids": reused,
        "run_verified_experience_ids": trusted,
        "checks": checks,
    }


def evaluate_transfer_manifest(path: Path | str, manager) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("cases"), list):
        raise ValueError("迁移验收 manifest 必须是 schema_version=1 且包含 cases")
    results = []
    for item in manifest["cases"]:
        if (not isinstance(item, dict) or not isinstance(item.get("case_id"), str)
                or not isinstance(item.get("source_episode"), str)
                or not isinstance(item.get("target_episode"), str)):
            raise ValueError("迁移验收 case 缺少 case_id/source_episode/target_episode")
        source_path = (manifest_path.parent / item["source_episode"]).resolve()
        target_path = (manifest_path.parent / item["target_episode"]).resolve()
        result = evaluate_transfer(read_json(source_path), read_json(target_path), manager)
        result.update({"case_id": item["case_id"], "source_path": str(source_path),
                       "target_path": str(target_path)})
        results.append(result)
    return {
        "schema_version": 1,
        "status": "passed" if results and all(item["status"] == "passed" for item in results)
        else "failed",
        "manifest_path": str(manifest_path), "case_count": len(results), "cases": results,
    }


def _study_task(base: dict[str, Any], suffix: str, *, reynolds_scale: float) -> dict[str, Any]:
    task = deepcopy(base)
    task["task_id"] = f"{base['task_id']}-{suffix}"
    physics = task["physics"]
    physics["reynolds_number"] = float(physics["reynolds_number"]) * reynolds_scale
    physics["kinematic_viscosity"] = (
        float(physics["inlet_velocity"]) * float(task["geometry"]["cylinder_diameter"])
        / float(physics["reynolds_number"])
    )
    task["mesh"]["target_cells"] = min(int(task["mesh"]["target_cells"]), 3000)
    task["time_control"].update({"end_time": 0.1, "delta_t": 0.005,
                                 "write_interval": 0.05})
    task["convergence"]["max_corrections"] = max(
        1, int(task["convergence"]["max_corrections"]))
    return task


def run_real_transfer_study(
    task_path: Path | str, *, memory_dir: Path | str | None = None,
    runs_dir: Path | str | None = None, fault: str = "missing-boundary",
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Collect one isolated real failure/recovery and cross-task reuse pair."""
    from cfd_memo_agent.models import ModelSettings
    from cfd_memo_agent.workflow import run_workflow
    from .manager import MemoryManager

    base = read_json(Path(task_path).resolve())
    if base.get("case_type") != "cylinder-2d":
        raise ValueError("首个真实迁移验收仅支持 cylinder-2d")
    parent = (Path(runs_dir).resolve() if runs_dir is not None
              else Path(__file__).resolve().parents[3] / "cases/runs")
    root = parent / f"memory-transfer-{uuid4().hex}"
    root.mkdir(parents=True)
    store = Path(memory_dir).resolve() if memory_dir is not None else root / "memory"
    source_task = _study_task(base, "transfer-source", reynolds_scale=1.0)
    target_task = _study_task(base, "transfer-target", reynolds_scale=1.2)
    source_task_path = root / "source-task.json"
    target_task_path = root / "target-task.json"
    write_record(source_task_path, source_task)
    write_record(target_task_path, target_task)
    settings = ModelSettings(provider="rules")
    source = run_workflow(
        task_path=source_task_path, mode="real", runs_dir=root / "source",
        timeout=timeout, fault=fault, planner_fallback="rules", model_settings=settings,
        memory_mode="cfd_memo", memory_dir=store, controlled_acceptance=True,
    )
    if source["status"] != "completed":
        result = {
            "schema_version": 1, "status": "source_failed", "study_path": str(root),
            "memory_path": str(store), "source_episode_path": source["episode_path"],
            "target_episode_path": None, "acceptance_path": None,
            "model_provider": "rules", "token_cost_expected": False,
        }
        write_record(root / "study.json", result)
        return result
    target = run_workflow(
        task_path=target_task_path, mode="real", runs_dir=root / "target",
        timeout=timeout, fault=fault, planner_fallback="rules", model_settings=settings,
        memory_mode="cfd_memo", memory_dir=store, controlled_acceptance=True,
    )
    manifest = {
        "schema_version": 1,
        "cases": [{
            "case_id": "real-config-repair-transfer",
            "source_episode": str(Path(source["episode_path"]).resolve().relative_to(root)),
            "target_episode": str(Path(target["episode_path"]).resolve().relative_to(root)),
        }],
    }
    manifest_path = root / "acceptance-manifest.json"
    write_record(manifest_path, manifest)
    acceptance = evaluate_transfer_manifest(manifest_path, MemoryManager(store))
    acceptance_path = root / "acceptance-result.json"
    write_record(acceptance_path, acceptance)
    result = {
        "schema_version": 1,
        "status": "passed" if acceptance["status"] == "passed" else "failed",
        "study_path": str(root), "memory_path": str(store),
        "source_episode_path": source["episode_path"],
        "target_episode_path": target["episode_path"],
        "acceptance_path": str(acceptance_path),
        "source_corrections": len(source["corrections"]),
        "target_corrections": len(target["corrections"]),
        "model_provider": "rules", "token_cost_expected": False,
    }
    write_record(root / "study.json", result)
    return result


__all__ = ["evaluate_transfer", "evaluate_transfer_manifest", "run_real_transfer_study"]
