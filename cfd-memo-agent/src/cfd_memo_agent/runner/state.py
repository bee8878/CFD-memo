"""Persistent stage state and immutable checkpoints for safe attempt recovery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


def tree_fingerprint(root: Path | str) -> str:
    base = Path(root)
    digest = hashlib.sha256()
    if not base.is_dir():
        raise ValueError(f"无法计算目录指纹：{base}")
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"状态目录不允许符号链接：{path}")
        if path.is_file():
            digest.update(path.relative_to(base).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_input_fingerprint(run_root: Path | str) -> str:
    root = Path(run_root)
    digest = hashlib.sha256()
    for name in ("task.json", "case-spec.json", "mesh-spec.json", "import.json"):
        path = root / name
        if path.is_file():
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
    digest.update(tree_fingerprint(root / "case").encode("ascii"))
    return digest.hexdigest()


def atomic_save(path: Path | str, value: dict[str, Any]) -> None:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def new_state(
    run_root: Path, attempt: Path, plan: tuple[str, ...], input_sha256: str,
    *, parent_attempt: Path | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1, "status": "running", "created_at": now,
        "updated_at": now, "run_path": str(run_root), "attempt_path": str(attempt),
        "parent_attempt": str(parent_attempt) if parent_attempt else None,
        "input_sha256": input_sha256, "plan": list(plan),
        "stages": {stage: {"status": "pending"} for stage in plan},
    }


def save_state(attempt: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_save(attempt / "stage-state.json", state)


def update_stage(
    attempt: Path, state: dict[str, Any], stage: str, status: str, **values: Any,
) -> None:
    state["stages"][stage] = {"status": status, **values}
    save_state(attempt, state)


def checkpoint_case(attempt: Path, stage: str, case: Path) -> dict[str, str]:
    checkpoint = attempt / "checkpoints" / stage / "case"
    if checkpoint.exists():
        raise FileExistsError(f"阶段 checkpoint 已存在：{checkpoint}")
    checkpoint.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(case, checkpoint)
    return {"path": str(checkpoint), "sha256": tree_fingerprint(checkpoint)}


def _inside_attempts(run_root: Path, attempt: Path) -> bool:
    attempts = (run_root / "attempts").resolve()
    resolved = attempt.resolve()
    return resolved.parent == attempts and resolved.name.startswith("attempt-")


def resume_checkpoint(
    run_root: Path, source_attempt: Path | str, plan: tuple[str, ...],
    input_sha256: str,
) -> dict[str, Any]:
    source = Path(source_attempt).resolve()
    if not _inside_attempts(run_root, source):
        raise ValueError("恢复来源必须是当前 run 的直接 attempt 子目录")
    try:
        state = json.loads((source / "stage-state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取恢复状态：{exc}") from exc
    if (state.get("schema_version") != 1 or state.get("plan") != list(plan)
            or state.get("input_sha256") != input_sha256):
        raise ValueError("恢复状态与当前输入或执行计划不一致")
    if state.get("status") == "completed":
        raise ValueError("来源 attempt 已完成，无需恢复")
    check_index = plan.index("checkMesh")
    reusable = []
    for stage in plan[:check_index]:
        if state.get("stages", {}).get(stage, {}).get("status") == "completed":
            reusable.append(stage)
        else:
            break
    checkpoint_stage = None
    for stage in (*reusable, "checkMesh"):
        entry = state.get("stages", {}).get(stage, {})
        if entry.get("status") == "completed" and isinstance(entry.get("checkpoint"), dict):
            checkpoint_stage = stage
    if checkpoint_stage is None:
        return {"source_attempt": source, "checkpoint": None, "skipped": ()}
    checkpoint = state["stages"][checkpoint_stage]["checkpoint"]
    path = Path(checkpoint["path"])
    if (not path.is_dir() or source not in path.resolve().parents
            or tree_fingerprint(path) != checkpoint.get("sha256")):
        raise ValueError("恢复 checkpoint 缺失、越界或内容指纹不一致")
    return {
        "source_attempt": source, "checkpoint": path,
        "checkpoint_stage": checkpoint_stage, "skipped": tuple(reusable),
    }


def ensure_disk_space(path: Path | str, minimum_free_bytes: int) -> int:
    free = shutil.disk_usage(Path(path)).free
    if free < minimum_free_bytes:
        raise OSError(f"可用磁盘空间不足：需要至少 {minimum_free_bytes} 字节，当前 {free} 字节")
    return free


def build_result_index(
    run_root: Path, attempt: Path, report: dict[str, Any],
) -> dict[str, Any]:
    """Hash declared inputs and outputs without indexing arbitrary external paths."""
    root = run_root.resolve()
    candidates: dict[Path, str] = {}
    for name in ("task.json", "case-spec.json", "mesh-spec.json", "generation.json",
                 "import.json"):
        candidates[root / name] = "input"
    for name in ("stage-state.json", "validation.json", "diagnosis.json", "execution.json",
                 "mesh-conversion.json", "mesh-evidence.json", "result-evidence.json",
                 "force-evidence.json"):
        candidates[attempt / name] = "evidence"
    for step in report.get("steps", []):
        if step.get("log_path"):
            candidates[Path(step["log_path"])] = "log"
    fields = (report.get("result_evidence") or {}).get("fields", {})
    if isinstance(fields, dict):
        for path in fields.values():
            if isinstance(path, str):
                candidates[Path(path)] = "field"
    artifacts = []
    for path, role in sorted(candidates.items(), key=lambda item: str(item[0])):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file() or root not in resolved.parents:
            continue
        data = resolved.read_bytes()
        artifacts.append({
            "role": role, "path": str(resolved.relative_to(root)),
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
    return {
        "schema_version": 1, "status": report.get("status"),
        "attempt_path": str(attempt), "input_sha256": run_input_fingerprint(root),
        "physical_validated": report.get("physical_validated") is True,
        "artifacts": artifacts,
    }


__all__ = [
    "atomic_save", "build_result_index", "checkpoint_case", "ensure_disk_space", "new_state",
    "resume_checkpoint", "run_input_fingerprint", "save_state", "tree_fingerprint",
    "update_stage",
]
