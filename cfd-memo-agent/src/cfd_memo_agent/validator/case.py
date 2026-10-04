"""Dispatch generated-case validation through the registered case adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cfd_memo_agent.case_adapters import get_case_adapter
from .task import issue, validate_task


def validate_case(task: dict[str, Any], case_dir: Path | str) -> dict:
    """Select one adapter and validate the task/case pair through its interface."""
    task_report = validate_task(task)
    if not task_report["task_valid"]:
        return task_report
    try:
        adapter = get_case_adapter(task)
    except ValueError as exc:
        task_report["errors"].append(issue(
            "UNSUPPORTED_CASE", str(exc), "task.case_type"))
        task_report["task_valid"] = False
        return task_report
    return adapter.validate(task, case_dir)


__all__ = ["validate_case"]
