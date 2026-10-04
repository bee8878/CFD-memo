"""Dispatch generated cases through the registered case adapter."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from cfd_memo_agent.case_adapters import get_case_adapter
from cfd_memo_agent.mesh_capabilities import mesh_spec_from_task, save_mesh_spec

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
    result = adapter.generate(
        task, runs_dir=runs_dir, template_dir=template_dir, intent=intent,
    )
    run_path = Path(result["run_path"])
    mesh_spec_path = save_mesh_spec(run_path / "mesh-spec.json", mesh_spec_from_task(task))
    result["mesh_spec_path"] = str(mesh_spec_path)
    (run_path / "generation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


__all__ = ["generate_case", "PROJECT_ROOT", "REQUIRED_FILES"]
