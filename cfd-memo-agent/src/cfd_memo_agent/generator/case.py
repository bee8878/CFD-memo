"""Dispatch generated cases through the registered case adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cfd_memo_agent.case_adapters import get_case_adapter

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_FILES = get_case_adapter({"case_type": "cylinder-2d"}).required_files


def generate_case(
    task: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    template_dir: Path | None = None,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one adapter and generate an isolated case through its interface."""
    adapter = get_case_adapter(task)
    return adapter.generate(
        task, runs_dir=runs_dir, template_dir=template_dir, intent=intent,
    )


__all__ = ["generate_case", "PROJECT_ROOT", "REQUIRED_FILES"]
