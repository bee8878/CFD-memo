"""Generate isolated case directories from the Stage C cylinder task."""

from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cfd_memo_agent.planner import DEFAULT_CYLINDER_TASK
from cfd_memo_agent.case_writer import validate_case_intent
from cfd_memo_agent.validator import validate_task
from cfd_memo_agent.mesh import SPAN, cylinder_mesh, planned_cells

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_FILES = (
    "system/blockMeshDict",
    "constant/physicalProperties",
    "0/U",
    "0/p",
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
)
MESH_WARNING = (
    "Project-authored cylinder O-grid; actual mesh quality requires blockMesh/checkMesh. "
    "Physical accuracy has not been validated."
)


def _number(value: Any, name: str, *, positive: bool = True) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive." if positive else "."))
    return format(value, ".12g")


def _replace_once(text: str, pattern: str, replacement: str, name: str) -> str:
    # These patterns target the bundled scaffold, not arbitrary OpenFOAM dictionaries.
    if len(re.findall(pattern, text, flags=re.MULTILINE)) != 1:
        raise ValueError(f"Template must contain exactly one {name}.")
    return re.sub(pattern, lambda _: replacement, text, flags=re.MULTILINE)


def _entry(text: str, key: str, value: str) -> str:
    return _replace_once(
        text, rf"^{re.escape(key)}\s+[^;\n]+;", f"{key}    {value};", key
    )


def generate_case(
    task: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    template_dir: Path | None = None,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy the bundled scaffold into a new run; never run OpenFOAM."""
    validation = validate_task(task)
    if not validation["task_valid"]:
        raise ValueError("; ".join(
            f"{item['location']}: {item['message']}" for item in validation["errors"]
        ))
    if task["case_type"] != "cylinder-2d" or task["solver"] != "icoFoam":
        raise ValueError("C2 supports only cylinder-2d with icoFoam.")
    if task["geometry"]["dimension"] != "2D":
        raise ValueError("C2 supports only 2D geometry.")
    if task["physics"]["flow_model"] != "incompressible_laminar":
        raise ValueError("C2 supports only incompressible laminar flow.")
    if task["boundary_conditions"] != DEFAULT_CYLINDER_TASK["boundary_conditions"]:
        raise ValueError("C2 supports only the default cylinder boundary conditions.")
    if intent is not None:
        validate_case_intent(task, intent)

    geometry = task["geometry"]
    velocity = _number(task["physics"]["inlet_velocity"], "inlet_velocity")
    viscosity = _number(task["physics"]["kinematic_viscosity"], "kinematic_viscosity")
    _number(geometry["cylinder_diameter"], "cylinder_diameter")
    _number(geometry["domain_length"], "domain_length")
    _number(geometry["domain_height"], "domain_height")
    time = task["time_control"]
    controls = {
        "application": "icoFoam",
        "startTime": _number(time["start_time"], "start_time", positive=False),
        "endTime": _number(time["end_time"], "end_time"),
        "deltaT": _number(time["delta_t"], "delta_t"),
        "writeInterval": _number(time["write_interval"], "write_interval"),
    }
    if time["end_time"] <= time["start_time"]:
        raise ValueError("end_time must be greater than start_time.")

    source = (Path(template_dir) if template_dir is not None else
              PROJECT_ROOT / "cases/templates/cylinder-2d").resolve()
    destination = (Path(runs_dir) if runs_dir is not None else
                   PROJECT_ROOT / "cases/runs").resolve()
    if destination == source or source in destination.parents:
        raise ValueError("runs_dir must not be inside the source template.")
    texts = {}
    for relative in REQUIRED_FILES:
        path = source / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing template file: {path}")
        texts[relative] = path.read_text(encoding="utf-8")

    target_cells = task.get("mesh", {}).get("target_cells", 10000)
    radial_grading = task.get("mesh", {}).get("radial_grading", 10)
    mesh_options = task.get("mesh", {})
    texts["system/blockMeshDict"] = cylinder_mesh(
        geometry, target_cells, radial_grading, mesh_options)
    texts["constant/physicalProperties"] = _entry(
        texts["constant/physicalProperties"], "nu", f"[0 2 -1 0 0 0 0] {viscosity}"
    )
    texts["0/U"] = _replace_once(
        texts["0/U"],
        r"\binlet\s*\{\s*type\s+fixedValue;\s*value\s+uniform\s*\([^)]*\);\s*\}",
        f"inlet\n    {{\n        type fixedValue;\n        value uniform ({velocity} 0 0);\n    }}",
        "fixed-value inlet velocity",
    )
    for key, value in controls.items():
        texts["system/controlDict"] = _entry(texts["system/controlDict"], key, value)
    force_controls = {
        "magUInf": velocity,
        "lRef": _number(geometry["cylinder_diameter"], "cylinder_diameter"),
        "Aref": _number(geometry["cylinder_diameter"] * SPAN, "force_reference_area"),
    }
    for key, value in force_controls.items():
        texts["system/controlDict"] = _replace_once(
            texts["system/controlDict"], rf"^        {key}\s+[^;\n]+;",
            f"        {key}            {value};", f"forceCoeffs {key}",
        )

    geometry_record = {
        "dimension": "2D",
        "cylinder_diameter": geometry["cylinder_diameter"],
        "domain_length": geometry["domain_length"],
        "domain_height": geometry["domain_height"],
        "upstream_length": geometry.get("upstream_length", geometry["domain_length"] / 2),
        "downstream_length": geometry.get("downstream_length", geometry["domain_length"] / 2),
        "units": "m",
        "span": SPAN,
        "mesh_verified": False,
        "warning": MESH_WARNING,
        "mesh_source": ("CFD-Memo two-ring wake-focused O-grid v2"
                        if "near_field_radius" in mesh_options
                        else "CFD-Memo eight-sector O-grid v1"),
        "planned_cells": planned_cells(target_cells, mesh_options),
        "radial_grading": radial_grading,
        "local_refinement": {key: mesh_options[key] for key in (
            "near_field_radius", "angular_cells", "wake_angular_cells",
            "near_radial_cells", "far_radial_cells", "near_radial_grading",
            "far_radial_grading") if key in mesh_options},
        "actual_cells": None,
    }
    # Serialize before allocating the run so invalid input leaves no partial directory.
    task_json = json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    geometry_json = json.dumps(geometry_record, indent=2, allow_nan=False) + "\n"
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid4().hex
    destination.mkdir(parents=True, exist_ok=True)
    run_dir = destination / run_id
    run_dir.mkdir(exist_ok=False)
    case_dir = run_dir / "case"
    shutil.copytree(source, case_dir)
    for relative in ("system/blockMeshDict", "constant/physicalProperties", "0/U",
                     "system/controlDict"):
        (case_dir / relative).write_text(texts[relative], encoding="utf-8")
    (case_dir / "constant/geometry.json").write_text(geometry_json, encoding="utf-8")
    (run_dir / "task.json").write_text(task_json, encoding="utf-8")
    result = {
        "status": "generated",
        "run_id": run_id,
        "run_path": str(run_dir),
        "case_path": str(case_dir),
        "task_path": str(run_dir / "task.json"),
        "generated_files": [*REQUIRED_FILES, "constant/geometry.json"],
        "mesh_verified": False,
        "intent_fingerprint": intent["task_fingerprint"] if intent is not None else None,
        "warnings": [MESH_WARNING],
    }
    (run_dir / "generation.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


__all__ = ["generate_case"]
