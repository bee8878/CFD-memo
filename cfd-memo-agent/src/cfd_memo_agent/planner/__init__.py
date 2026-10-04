"""Rule-based planning from natural language to structured CFD tasks."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


DEFAULT_CYLINDER_TASK: dict[str, Any] = {
    "task_id": "task-cylinder-2d-re100",
    "case_type": "cylinder-2d",
    "solver": "icoFoam",
    "geometry": {
        "dimension": "2D",
        "cylinder_diameter": 1.0,
        "domain_length": 20.0,
        "domain_height": 10.0,
    },
    "physics": {
        "flow_model": "incompressible_laminar",
        "reynolds_number": 100,
        "inlet_velocity": 1.0,
        "kinematic_viscosity": 0.01,
    },
    "boundary_conditions": {
        "inlet": {"U": "fixedValue", "p": "zeroGradient"},
        "outlet": {"U": "zeroGradient", "p": "fixedValue"},
        "cylinder": {"U": "noSlip", "p": "zeroGradient"},
        "top": {"U": "slip", "p": "zeroGradient"},
        "bottom": {"U": "slip", "p": "zeroGradient"},
        "frontAndBack": {"U": "empty", "p": "empty"},
    },
    "mesh": {
        "generator": "manual-template",
        "target_cells": 10000,
    },
    "time_control": {
        "start_time": 0,
        "end_time": 10,
        "delta_t": 0.005,
        "write_interval": 0.5,
    },
    "convergence": {
        "residual_target": 0.000001,
        "max_corrections": 2,
    },
}

DEFAULT_CAVITY_TASK: dict[str, Any] = {
    "task_id": "task-cavity-2d-re100",
    "case_type": "cavity-2d",
    "solver": "icoFoam",
    "geometry": {"dimension": "2D", "width": 1.0, "height": 1.0, "depth": 0.1},
    "physics": {
        "flow_model": "incompressible_laminar",
        "reynolds_number": 100,
        "lid_velocity": 1.0,
        "kinematic_viscosity": 0.01,
    },
    "boundary_conditions": {
        "movingWall": {"U": "fixedValue", "p": "zeroGradient"},
        "fixedWalls": {"U": "noSlip", "p": "zeroGradient"},
        "frontAndBack": {"U": "empty", "p": "empty"},
    },
    "mesh": {"generator": "manual-template", "cells_x": 20, "cells_y": 20},
    "time_control": {"start_time": 0, "end_time": 0.5, "delta_t": 0.005,
                     "write_interval": 0.1},
    "convergence": {"residual_target": 0.000001, "max_corrections": 2},
}


def plan_task(description: str) -> dict[str, Any]:
    """Create a Stage C structured task from a natural language description."""
    normalized = description.strip()
    if not normalized:
        raise ValueError("Task description cannot be empty.")

    cavity = _looks_like_cavity_flow(normalized)
    if not cavity and not _looks_like_cylinder_flow(normalized):
        raise ValueError("Planner currently supports 2D cylinder flow and lid-driven cavity tasks.")

    task = deepcopy(DEFAULT_CAVITY_TASK if cavity else DEFAULT_CYLINDER_TASK)
    reynolds_number = _extract_reynolds_number(normalized)
    if reynolds_number is not None:
        task["physics"]["reynolds_number"] = reynolds_number
    if cavity:
        velocity = _extract_named_number(normalized, ["U", "顶盖速度", "盖板速度", "速度"])
        width = _extract_named_number(normalized, ["L", "宽度", "方腔宽度"])
        if velocity is not None:
            task["physics"]["lid_velocity"] = velocity
        if width is not None:
            task["geometry"]["width"] = width
        task["physics"]["kinematic_viscosity"] = _compute_nu(
            task["physics"]["lid_velocity"], task["geometry"]["width"],
            task["physics"]["reynolds_number"])
    else:
        velocity = _extract_named_number(normalized, ["U", "入口速度", "速度"])
        diameter = _extract_named_number(normalized, ["D", "直径", "圆柱直径"])
        if velocity is not None:
            task["physics"]["inlet_velocity"] = velocity
        if diameter is not None:
            task["geometry"]["cylinder_diameter"] = diameter
        task["physics"]["kinematic_viscosity"] = _compute_nu(
            task["physics"]["inlet_velocity"], task["geometry"]["cylinder_diameter"],
            task["physics"]["reynolds_number"])
    task["task_id"] = _make_task_id(task)
    return task


def _looks_like_cylinder_flow(text: str) -> bool:
    lowered = text.lower()
    has_cylinder = "圆柱" in text or "cylinder" in lowered
    has_flow = "绕流" in text or "flow" in lowered
    return has_cylinder and has_flow


def _looks_like_cavity_flow(text: str) -> bool:
    lowered = text.lower()
    return ("方腔" in text or "顶盖驱动" in text
            or "lid-driven cavity" in lowered or "lid driven cavity" in lowered)


def _extract_reynolds_number(text: str) -> int | float | None:
    patterns = [
        r"\bRe\s*=?\s*(\d+(?:\.\d+)?)",
        r"雷诺数\s*=?\s*(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _number(match.group(1))
    return None


def _extract_named_number(text: str, names: list[str]) -> float | None:
    for name in names:
        pattern = rf"{re.escape(name)}\s*=?\s*(\d+(?:\.\d+)?)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _compute_nu(inlet_velocity: float, diameter: float, reynolds_number: float) -> float:
    return round((inlet_velocity * diameter) / reynolds_number, 10)


def _make_task_id(task: dict[str, Any]) -> str:
    re_value = task["physics"]["reynolds_number"]
    re_text = str(re_value).replace(".", "p")
    return f"task-{task['case_type']}-re{re_text}"


def _number(value: str) -> int | float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


__all__ = ["DEFAULT_CAVITY_TASK", "DEFAULT_CYLINDER_TASK", "plan_task"]
