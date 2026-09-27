"""Exact-task whole-case cache used as the simple-cache experiment baseline."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Any


def task_cache_key(task: dict[str, Any]) -> str:
    encoded = json.dumps(
        task, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "task-" + sha256(encoded).hexdigest()[:16]


class CaseCache:
    """Store and retrieve a complete case only for an exactly matching task."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.cases_dir = self.root / "cache" / "cases"
        self.cases_dir.mkdir(parents=True, exist_ok=True)

    def lookup(self, task: dict[str, Any]) -> tuple[str, Path | None]:
        key = task_cache_key(task)
        path = self.cases_dir / key / "case"
        return key, path if path.is_dir() else None

    def store(self, task: dict[str, Any], case_dir: Path) -> tuple[str, Path]:
        key = task_cache_key(task)
        target = self.cases_dir / key
        case_target = target / "case"
        if not case_target.exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(Path(case_dir), case_target)
            (target / "task.json").write_text(
                json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        return key, case_target


__all__ = ["CaseCache", "task_cache_key"]
