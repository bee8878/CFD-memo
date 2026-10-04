"""Generate the registered OpenFOAM 10 lid-driven cavity case."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

from cfd_memo_agent.case_spec import CaseSpec
from cfd_memo_agent.case_writer import validate_case_intent
from cfd_memo_agent.tutorial_capabilities import (
    get_tutorial_capability, tutorial_manifest,
)
from cfd_memo_agent.validator import validate_task

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_FILES = (
    "system/blockMeshDict", "constant/physicalProperties", "0/U", "0/p",
    "system/controlDict", "system/fvSchemes", "system/fvSolution",
)
MESH_WARNING = (
    "OpenFOAM-10-style single-block cavity mesh; blockMesh/checkMesh and result "
    "evidence are still required. Physical accuracy has not been validated."
)


def _number(value: Any, name: str, *, positive: bool = True) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return format(value, ".12g")


def _replace_once(text: str, pattern: str, replacement: str, name: str) -> str:
    if len(re.findall(pattern, text, flags=re.MULTILINE)) != 1:
        raise ValueError(f"Template must contain exactly one {name}")
    return re.sub(pattern, lambda _: replacement, text, flags=re.MULTILINE)


def _entry(text: str, key: str, value: str) -> str:
    return _replace_once(text, rf"^{re.escape(key)}\s+[^;\n]+;",
                         f"{key}    {value};", key)


def _expand_pressure_solver(text: str) -> str:
    """Expand the one reviewed `$p` dictionary reference from the OF10 tutorial."""
    references = re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*\s*;", text)
    if not references:
        return text
    if references != ["$p;"]:
        raise ValueError("Official cavity fvSolution contains an unsupported reference")
    match = re.search(r"(?ms)^\s*p\s*\{(?P<body>.*?)^\s*\}", text)
    if match is None:
        raise ValueError("Cannot resolve the official cavity p solver reference")
    inherited = re.sub(
        r"(?m)^\s*relTol\s+[^;]+;\s*$", "", match.group("body"),
    ).strip()
    if not inherited:
        raise ValueError("Official cavity p solver reference is empty")
    return _replace_once(
        text, r"(?m)^\s*\$p\s*;", inherited, "p solver dictionary reference",
    )


def _block_mesh(task: dict[str, Any]) -> str:
    geometry, mesh = task["geometry"], task["mesh"]
    width = _number(geometry["width"], "width")
    height = _number(geometry["height"], "height")
    depth = _number(geometry["depth"], "depth")
    nx, ny = mesh["cells_x"], mesh["cells_y"]
    return f"""FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    object blockMeshDict;
}}

convertToMeters 1;
vertices
(
    (0 0 0) ({width} 0 0) ({width} {height} 0) (0 {height} 0)
    (0 0 {depth}) ({width} 0 {depth}) ({width} {height} {depth}) (0 {height} {depth})
);
blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} 1) simpleGrading (1 1 1)
);
boundary
(
    movingWall
    {{
        type wall;
        faces ((3 7 6 2));
    }}
    fixedWalls
    {{
        type wall;
        faces ((0 4 7 3) (2 6 5 1) (1 5 4 0));
    }}
    frontAndBack
    {{
        type empty;
        faces ((0 3 2 1) (4 5 6 7));
    }}
);
"""


def generate_cavity_case(
    task: dict[str, Any], *, runs_dir: Path | None = None,
    template_dir: Path | None = None, intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation = validate_task(task)
    if not validation["task_valid"]:
        raise ValueError("; ".join(
            f"{item['location']}: {item['message']}" for item in validation["errors"]))
    reference = task.get("tutorial_reference")
    if intent is not None and reference is not None:
        raise ValueError("教程 Builder 不接受通用 Case Writer intent")
    if intent is not None:
        validate_case_intent(task, intent)

    source = (Path(template_dir) if template_dir else
              PROJECT_ROOT / "cases/templates/cavity-2d").resolve()
    destination = (Path(runs_dir) if runs_dir else PROJECT_ROOT / "cases/runs").resolve()
    if destination == source or source in destination.parents:
        raise ValueError("runs_dir must not be inside the source template")
    capability = None
    source_hash = None
    source_files = None
    approved_files = REQUIRED_FILES
    if reference is not None:
        capability = get_tutorial_capability(reference["tutorial_id"])
        if capability.task_template["case_type"] != "cavity-2d":
            raise ValueError("教程能力不是方腔适配器")
        source_hash, source_files = tutorial_manifest(source, capability)
        if source_hash != reference["manifest_sha256"]:
            raise ValueError("教程白名单内容与已确认 task 的指纹不一致")
        approved_files = capability.whitelist
    texts = {}
    for relative in approved_files:
        path = source / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing template file: {path}")
        texts[relative] = path.read_text(encoding="utf-8")
    if reference is not None:
        texts["system/fvSolution"] = _expand_pressure_solver(
            texts["system/fvSolution"])

    physics, time = task["physics"], task["time_control"]
    velocity = _number(physics["lid_velocity"], "lid_velocity")
    viscosity = _number(physics["kinematic_viscosity"], "kinematic_viscosity")
    texts["system/blockMeshDict"] = _block_mesh(task)
    texts["0/U"] = _replace_once(
        texts["0/U"],
        r"\bmovingWall\s*\{\s*type\s+fixedValue;\s*value\s+uniform\s*\([^)]*\);\s*\}",
        f"movingWall\n    {{\n        type fixedValue;\n        value uniform ({velocity} 0 0);\n    }}",
        "moving-wall velocity")
    texts["constant/physicalProperties"] = _entry(
        texts["constant/physicalProperties"], "nu", f"[0 2 -1 0 0 0 0] {viscosity}")
    controls = {
        "application": task["solver"],
        "startTime": _number(time["start_time"], "start_time", positive=False),
        "endTime": _number(time["end_time"], "end_time"),
        "deltaT": _number(time["delta_t"], "delta_t"),
        "writeControl": "runTime",
        "writeInterval": _number(time["write_interval"], "write_interval"),
    }
    for key, value in controls.items():
        texts["system/controlDict"] = _entry(texts["system/controlDict"], key, value)

    geometry = task["geometry"]
    geometry_record = {
        "dimension": "2D", "width": geometry["width"], "height": geometry["height"],
        "depth": geometry["depth"], "units": "m", "mesh_verified": False,
        "warning": MESH_WARNING,
        "mesh_source": reference["tutorial_id"] if reference else "OpenFOAM-10 cavity topology",
        "planned_cells": task["mesh"]["cells_x"] * task["mesh"]["cells_y"],
        "actual_cells": None,
    }
    serialized = {
        "task": json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "geometry": json.dumps(geometry_record, indent=2, allow_nan=False) + "\n",
    }
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid4().hex
    destination.mkdir(parents=True, exist_ok=True)
    run_dir = destination / run_id
    run_dir.mkdir(exist_ok=False)
    case_dir = run_dir / "case"
    if reference is None:
        shutil.copytree(source, case_dir)
    else:
        case_dir.mkdir()
    for relative, text in texts.items():
        target = case_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (case_dir / "constant/geometry.json").write_text(serialized["geometry"], encoding="utf-8")
    if reference is not None:
        provenance = {
            "schema_version": 1, "source": "OpenFOAM Foundation tutorials",
            "openfoam_version": capability.openfoam_version,
            "tutorial_id": capability.tutorial_id,
            "capability_id": capability.capability_id,
            "source_manifest_sha256": source_hash, "source_files": source_files,
            "copied_files": list(approved_files), "scripts_executed": False,
            "scripts_copied": False, "physical_validated": False,
        }
        (case_dir / "constant/tutorial-provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "task.json").write_text(serialized["task"], encoding="utf-8")
    case_spec = CaseSpec.from_task(
        task, source_type="tutorial" if reference else "generated",
        source_path=reference["tutorial_id"] if reference else None,
    ).to_dict()
    (run_dir / "case-spec.json").write_text(
        json.dumps(case_spec, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    result = {
        "status": "generated", "run_id": run_id, "run_path": str(run_dir),
        "case_path": str(case_dir), "task_path": str(run_dir / "task.json"),
        "case_spec_path": str(run_dir / "case-spec.json"),
        "generated_files": [*approved_files, "constant/geometry.json"] + (
            ["constant/tutorial-provenance.json"] if reference else []),
        "mesh_verified": False,
        "intent_fingerprint": intent["task_fingerprint"] if intent else None,
        "scripts_executed": False if reference else None,
        "scripts_copied": False if reference else None,
        "warnings": [MESH_WARNING],
    }
    (run_dir / "generation.json").write_text(json.dumps(result, indent=2) + "\n",
                                               encoding="utf-8")
    return result


__all__ = ["generate_cavity_case"]
