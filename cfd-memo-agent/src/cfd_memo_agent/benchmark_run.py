"""Resumable M2 execution for the frozen cross-task benchmark."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from cfd_memo_agent.benchmark import (
    DEFAULT_MANIFEST, audit_benchmark, load_benchmark_manifest, materialize_task,
)
from cfd_memo_agent.case_adapters.backward_step_generation import tutorial_manifest
from cfd_memo_agent.models import ModelSettings
from cfd_memo_agent.preflight import build_preflight, confirm_preflight
from cfd_memo_agent.tutorials import wsl_tutorial_root
from cfd_memo_agent.workflow import run_workflow


PROJECT = Path(__file__).resolve().parents[2]
PRICE_SNAPSHOT = {
    "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "retrieved_at": "2026-10-06",
    "currency": "USD",
    "unit": "per_1m_tokens",
    "model": "deepseek-flash",
    "off_peak": {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.6},
    "peak": {"input_cache_hit": 0.006, "input_cache_miss": 0.3, "output": 1.2},
    "note": "Store both tiers because holiday and call-time billing can change the exact rate.",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _tree_sha256(root: Path) -> str:
    digest = sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _task_spec(manifest: dict[str, Any], task_id: str) -> dict[str, Any]:
    for spec in manifest["task_specs"]:
        if spec["id"] == task_id:
            return spec
    raise ValueError(f"benchmark 不存在任务：{task_id}")


def _save_task(root: Path, task: dict[str, Any]) -> Path:
    target = root / "tasks" / f"{task['task_id']}.json"
    _write_json(target, task)
    return target


def _train_snapshots(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Create train-only stores, then freeze independent evaluation copies."""
    rules = ModelSettings(provider="rules")
    training = root / "training"
    simple_source = training / "stores/simple-cache"
    knowledge_source = training / "stores/knowledge"
    clean_ids = ("cylinder-03-re100", "cavity-03-re60")
    knowledge_ids = ("cylinder-04-re120", "cavity-06-re120-transport")
    records = []
    for task_id in clean_ids:
        task_path = _save_task(training, materialize_task(_task_spec(manifest, task_id)))
        episode = run_workflow(
            task_path=task_path, mode="simulated", runs_dir=training / "episodes/simple-cache",
            memory_mode="simple_cache", memory_dir=simple_source,
            memory_learning=True, model_settings=rules,
        )
        records.append({"task_id": task_id, "fault": None, "status": episode["status"]})
    for task_id in knowledge_ids:
        for fault in ("missing-boundary", "bad-transport"):
            task_path = _save_task(training, materialize_task(_task_spec(manifest, task_id)))
            episode = run_workflow(
                task_path=task_path, mode="simulated", fault=fault,
                runs_dir=training / "episodes/knowledge", memory_mode="cfd_memo",
                memory_dir=knowledge_source, memory_learning=True, model_settings=rules,
                max_corrections=manifest["correction_budget"],
            )
            records.append({"task_id": task_id, "fault": fault, "status": episode["status"]})
    if any(item["status"] != "simulated_success" for item in records):
        raise ValueError("训练快照生成失败；不会开始正式评估")

    snapshots = root / "snapshots"
    mapping = {
        "simple_cache": simple_source,
        "retrieval_only": knowledge_source,
        "cfd_memo": knowledge_source,
    }
    result = {"no_memory": None}
    for group, source in mapping.items():
        target = snapshots / group
        shutil.copytree(source, target)
        result[group] = {
            "path": str(target), "sha256": _tree_sha256(target),
            "learning_enabled": False,
        }
    _write_json(root / "training/training.json", {"records": records, "snapshots": result})
    return result


def _usage(episode: dict[str, Any]) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    traces = [episode.get("planning", {}).get("trace"),
              episode.get("case_writing", {}).get("trace")]
    traces.extend(item.get("trace") for item in episode.get("reviews", []))
    for trace in traces:
        usage = trace.get("usage") if isinstance(trace, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                totals[key] += value
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


def _cost_range(usage: dict[str, int], model: str | None) -> dict[str, float] | None:
    if model != PRICE_SNAPSHOT["model"]:
        return None
    cached = min(usage["cached_input_tokens"], usage["input_tokens"])
    uncached = usage["input_tokens"] - cached
    values = {}
    for tier in ("off_peak", "peak"):
        rates = PRICE_SNAPSHOT[tier]
        values[tier] = (
            uncached * rates["input_cache_miss"]
            + cached * rates["input_cache_hit"]
            + usage["output_tokens"] * rates["output"]
        ) / 1_000_000
    return values


def _resolve_tutorial(task: dict[str, Any], tutorial_root: Path | None) -> tuple[dict, Path | None]:
    if task["case_type"] != "backward-step-2d":
        return task, None
    root = Path(tutorial_root) if tutorial_root is not None else wsl_tutorial_root()
    source = root / task["tutorial_reference"]["tutorial_id"]
    if not source.is_dir():
        raise ValueError(f"未找到 pitzDaily 教程目录：{source}")
    source_hash, _ = tutorial_manifest(source)
    resolved = deepcopy(task)
    resolved["tutorial_reference"]["manifest_sha256"] = source_hash
    return resolved, source


def _execution_settings(runner: str, supplied: ModelSettings | None) -> ModelSettings:
    if supplied is not None:
        return supplied
    # A simulated benchmark is an offline scheduler acceptance, even when .env selects an LLM.
    return ModelSettings(provider="rules") if runner == "simulated" else ModelSettings.from_env()


def prepare_benchmark_run(
    *, manifest_path: Path | str = DEFAULT_MANIFEST, output_dir: Path | None = None,
    runner: str = "real", model_settings: ModelSettings | None = None,
) -> dict[str, Any]:
    if runner not in {"simulated", "real"}:
        raise ValueError("runner 必须为 simulated 或 real")
    audit = audit_benchmark(manifest_path)
    manifest = load_benchmark_manifest(manifest_path)
    settings = _execution_settings(runner, model_settings)
    settings.validate(require_credentials=runner == "real")
    if runner == "real" and settings.provider != "deepseek":
        raise ValueError("正式 M2 运行固定使用 DeepSeek；当前 provider 不一致")
    root = Path(output_dir).resolve() if output_dir else (
        PROJECT / "cases/runs" / f"cross-task-study-{uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(manifest_path).resolve(), root / "protocol.json")
    snapshots = _train_snapshots(root, manifest)
    state = {
        "schema_version": 1, "study_id": root.name, "study_path": str(root),
        "status": "prepared", "runner": runner,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": audit["manifest_sha256"],
        "protocol_model": manifest["model"],
        "execution_model": settings.public_status(),
        "pricing_snapshot": PRICE_SNAPSHOT,
        "snapshots": snapshots,
        "evaluations": [{**item, "status": "pending", "episode_path": None,
                         "error": None, "usage": None, "estimated_cost_usd": None}
                        for item in manifest["real_evaluations"]],
        "completed_count": 0, "failed_count": 0,
        "physical_validated": False,
    }
    _write_json(root / "state.json", state)
    return state


def run_benchmark(
    *, manifest_path: Path | str = DEFAULT_MANIFEST, output_dir: Path | None = None,
    resume: Path | None = None, runner: str = "real", limit: int | None = None,
    model_settings: ModelSettings | None = None, tutorial_root: Path | None = None,
) -> dict[str, Any]:
    if limit is not None and (isinstance(limit, bool) or limit < 1):
        raise ValueError("limit 必须为正整数")
    if resume is None:
        state = prepare_benchmark_run(
            manifest_path=manifest_path, output_dir=output_dir,
            runner=runner, model_settings=model_settings,
        )
        root = Path(state["study_path"])
    else:
        root = Path(resume).resolve()
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        runner = state["runner"]
    manifest = load_benchmark_manifest(root / "protocol.json")
    settings = _execution_settings(runner, model_settings)
    settings.validate(require_credentials=runner == "real")
    state["status"] = "running"
    _write_json(root / "state.json", state)
    processed = 0
    for item in state["evaluations"]:
        if item["status"] != "pending" or (limit is not None and processed >= limit):
            continue
        spec = _task_spec(manifest, item["task_id"])
        try:
            task, source = _resolve_tutorial(materialize_task(spec), tutorial_root)
            task_path = _save_task(root, task)
            snapshot = state["snapshots"].get(item["group"])
            memory_dir = Path(snapshot["path"]) if snapshot else None
            before = _tree_sha256(memory_dir) if memory_dir else None
            preview = build_preflight(
                task_path, runner_mode=runner, memory_mode=item["group"],
                memory_dir=memory_dir, max_corrections=manifest["correction_budget"],
                timeout=manifest["command_timeout_seconds"],
            )
            confirmed = confirm_preflight(preview, preview["confirmation_token"])
            episode = run_workflow(
                task_path=task_path, mode=runner,
                runs_dir=root / "evaluation" / item["id"],
                max_corrections=manifest["correction_budget"],
                timeout=manifest["command_timeout_seconds"],
                fault=spec["fault_profile"], controlled_acceptance=runner == "real",
                planner_fallback="stop", model_settings=settings,
                memory_mode=item["group"], memory_dir=memory_dir,
                memory_learning=False, confirmed_preflight=confirmed,
                generation_template_dir=source,
            )
            after = _tree_sha256(memory_dir) if memory_dir else None
            if before != after:
                raise RuntimeError("冻结记忆快照在评估期间发生变化")
            usage = _usage(episode)
            item.update({
                "status": "completed" if episode["status"] in {
                    "completed", "simulated_success"
                } else "failed",
                "workflow_status": episode["status"],
                "episode_path": episode["episode_path"], "usage": usage,
                "estimated_cost_usd": _cost_range(usage, settings.model),
            })
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            item.update({"status": "infrastructure_failed", "error": str(exc)})
            state["status"] = "interrupted"
            _write_json(root / "state.json", state)
            break
        processed += 1
        state["completed_count"] = sum(
            row["status"] in {"completed", "failed"} for row in state["evaluations"])
        state["failed_count"] = sum(row["status"] == "failed" for row in state["evaluations"])
        _write_json(root / "state.json", state)
    pending = sum(item["status"] == "pending" for item in state["evaluations"])
    if state["status"] != "interrupted":
        state["status"] = "completed" if pending == 0 else "partial"
    state["completed_count"] = sum(
        row["status"] in {"completed", "failed"} for row in state["evaluations"])
    state["failed_count"] = sum(row["status"] == "failed" for row in state["evaluations"])
    state["pending_count"] = pending
    _write_json(root / "state.json", state)
    return state


__all__ = ["PRICE_SNAPSHOT", "prepare_benchmark_run", "run_benchmark"]
