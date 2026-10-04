"""Build one reviewed OpenFOAM 10 pitzDaily case from dictionary files only."""

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
from cfd_memo_agent.tutorial_capabilities import (
    get_tutorial_capability, tutorial_manifest as capability_manifest,
)
from cfd_memo_agent.validator import validate_task

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TUTORIAL_ID = "incompressible/simpleFoam/pitzDaily"
WHITELIST = get_tutorial_capability(TUTORIAL_ID).whitelist


def tutorial_manifest(source: Path | str) -> tuple[str, dict[str, str]]:
    """Compatibility wrapper around the data-driven capability manifest."""
    return capability_manifest(source, get_tutorial_capability(TUTORIAL_ID))


def _number(value: Any, name: str, *, positive: bool = True) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是有限数字")
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} 必须是有限" + ("正数" if positive else "数字"))
    return format(value, ".12g")


def _replace_once(text: str, pattern: str, replacement: str, name: str) -> str:
    if len(re.findall(pattern, text, flags=re.MULTILINE)) != 1:
        raise ValueError(f"教程必须且只能包含一个 {name}")
    return re.sub(pattern, lambda _: replacement, text, flags=re.MULTILINE)


def _entry(text: str, key: str, value: str) -> str:
    return _replace_once(
        text, rf"^{re.escape(key)}\s+[^;\n]+;", f"{key}    {value};", key,
    )


def _remove_functions(text: str) -> str:
    match = re.search(r"(?m)^functions\s*\{", text)
    if match is None:
        return text
    opening = text.find("{", match.start())
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[:match.start()] + text[index + 1:]
    raise ValueError("controlDict 的 functions 块括号不完整")


def generate_backward_step_case(
    task: dict[str, Any], *, runs_dir: Path | None = None,
    template_dir: Path | None = None, intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy the approved tutorial whitelist and apply bounded task values."""
    validation = validate_task(task)
    if not validation["task_valid"]:
        raise ValueError("; ".join(
            f"{item['location']}: {item['message']}" for item in validation["errors"]))
    if intent is not None:
        raise ValueError("教程 Builder 不接受通用 Case Writer intent")
    if template_dir is None:
        raise ValueError("后台阶 Builder 必须显式提供已验证的官方教程目录")
    source = Path(template_dir).resolve()
    destination = (Path(runs_dir) if runs_dir else PROJECT_ROOT / "cases/runs").resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError("输出目录与教程源目录不能相同或互相包含")
    source_hash, source_files = tutorial_manifest(source)
    expected_hash = task["tutorial_reference"]["manifest_sha256"]
    if source_hash != expected_hash:
        raise ValueError("教程白名单内容与已确认 task 的指纹不一致")

    texts = {
        relative: (source / relative).read_text(encoding="utf-8-sig")
        for relative in WHITELIST
    }
    physics, time = task["physics"], task["time_control"]
    velocity = _number(physics["inlet_velocity"], "inlet_velocity")
    texts["0/U"] = _replace_once(
        texts["0/U"],
        r"\binlet\s*\{\s*type\s+fixedValue;\s*value\s+uniform\s*\([^)]*\);\s*\}",
        f"inlet\n    {{\n        type fixedValue;\n        value uniform ({velocity} 0 0);\n    }}",
        "inlet velocity",
    )
    texts["constant/physicalProperties"] = _entry(
        texts["constant/physicalProperties"], "nu",
        _number(physics["kinematic_viscosity"], "kinematic_viscosity"),
    )
    control = _remove_functions(texts["system/controlDict"])
    controls = {
        "application": task["solver"],
        "startTime": _number(time["start_time"], "start_time", positive=False),
        "endTime": _number(time["end_time"], "end_time"),
        "deltaT": _number(time["delta_t"], "delta_t"),
        "writeInterval": _number(time["write_interval"], "write_interval"),
    }
    for key, value in controls.items():
        control = _entry(control, key, value)
    texts["system/controlDict"] = control

    run_id = datetime.now(timezone.utc).strftime("tutorial-%Y%m%dT%H%M%SZ-") + uuid4().hex
    run_dir = destination / run_id
    case_dir = run_dir / "case"
    provenance = {
        "schema_version": 1,
        "source": "OpenFOAM Foundation tutorials",
        "openfoam_version": "10",
        "tutorial_id": TUTORIAL_ID,
        "source_manifest_sha256": source_hash,
        "source_files": source_files,
        "copied_files": list(WHITELIST),
        "scripts_executed": False,
        "scripts_copied": False,
        "physical_validated": False,
    }
    geometry = {
        **task["geometry"], "units": "m", "planned_cells": task["mesh"]["target_cells"],
        "actual_cells": None, "mesh_verified": False,
        "mesh_source": TUTORIAL_ID, "physical_validated": False,
    }
    try:
        destination.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(exist_ok=False)
        for relative, text in texts.items():
            target = case_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        (case_dir / "constant/geometry.json").write_text(
            json.dumps(geometry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (case_dir / "constant/tutorial-provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (run_dir / "task.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        spec = CaseSpec.from_task(
            task, source_type="tutorial", source_path=TUTORIAL_ID,
        ).to_dict()
        (run_dir / "case-spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = {
            "status": "generated", "run_id": run_id, "run_path": str(run_dir),
            "case_path": str(case_dir), "task_path": str(run_dir / "task.json"),
            "case_spec_path": str(run_dir / "case-spec.json"),
            "generated_files": [*WHITELIST, "constant/geometry.json",
                                "constant/tutorial-provenance.json"],
            "mesh_verified": False, "intent_fingerprint": None,
            "scripts_executed": False, "scripts_copied": False,
            "warnings": ["教程工程执行尚未证明物理结果准确。"],
        }
        (run_dir / "generation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    except BaseException:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


__all__ = ["TUTORIAL_ID", "WHITELIST", "generate_backward_step_case", "tutorial_manifest"]
