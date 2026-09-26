"""Task checks independent of the planner and generator."""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .foam import read_json

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas/task.schema.json"
BOUNDARIES = {
    "inlet": {"U": "fixedValue", "p": "zeroGradient"},
    "outlet": {"U": "zeroGradient", "p": "fixedValue"},
    "cylinder": {"U": "noSlip", "p": "zeroGradient"},
    "top": {"U": "slip", "p": "zeroGradient"},
    "bottom": {"U": "slip", "p": "zeroGradient"},
    "frontAndBack": {"U": "empty", "p": "empty"},
}


def issue(code, message, location):
    return {"code": code, "message": message, "location": location}


def new_report():
    return {
        "task_valid": False, "config_valid": False, "errors": [],
        "warnings": [], "runtime_blockers": [], "mesh_verified": False,
    }


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def close(actual, expected):
    return finite(actual) and finite(expected) and math.isclose(
        actual, expected, rel_tol=1e-8, abs_tol=1e-10
    )


@lru_cache(maxsize=1)
def schema_validator():
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_task(task) -> dict:
    report = new_report()
    errors = report["errors"]

    def check_finite(value, location):
        if isinstance(value, dict):
            for key, child in value.items():
                check_finite(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_finite(child, f"{location}[{index}]")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not finite(value):
                errors.append(issue("NON_FINITE", "数值必须有限且可计算", location))

    check_finite(task, "task")
    for error in sorted(schema_validator().iter_errors(task),
                        key=lambda e: str(list(e.absolute_path))):
        location = "task" + "".join(f".{part}" for part in error.absolute_path)
        errors.append(issue("TASK_SCHEMA", f"任务字段不符合结构要求：{error.message}", location))
    if errors:
        return report

    if task["solver"] != "icoFoam":
        errors.append(issue("UNSUPPORTED_SOLVER", "当前只支持 icoFoam", "task.solver"))
    if task["boundary_conditions"] != BOUNDARIES:
        errors.append(issue("UNSUPPORTED_BOUNDARY", "当前只支持默认边界条件组合",
                            "task.boundary_conditions"))
    mesh = task.get("mesh", {})
    if mesh.get("generator", "manual-template") != "manual-template":
        errors.append(issue("UNSUPPORTED_MESH", "当前仅实现 manual-template 网格模板",
                            "task.mesh.generator"))

    geometry, physics, time = task["geometry"], task["physics"], task["time_control"]
    diameter = geometry["cylinder_diameter"]
    if diameter >= min(geometry["domain_length"], geometry["domain_height"]):
        errors.append(issue("GEOMETRY_RANGE", "圆柱直径必须小于计算域长和高", "task.geometry"))
    upstream = geometry.get("upstream_length")
    downstream = geometry.get("downstream_length")
    if (upstream is None) != (downstream is None):
        errors.append(issue("GEOMETRY_RANGE", "上游和下游长度必须同时提供", "task.geometry"))
    elif upstream is not None and not close(upstream + downstream, geometry["domain_length"]):
        errors.append(issue("GEOMETRY_RANGE", "上游与下游长度之和必须等于计算域长度", "task.geometry"))
    if "near_field_radius" in mesh:
        outer_limit = min(upstream or geometry["domain_length"] / 2,
                          downstream or geometry["domain_length"] / 2,
                          geometry["domain_height"] / 2)
        if not diameter / 2 < mesh["near_field_radius"] < outer_limit:
            errors.append(issue("MESH_RANGE", "近场半径必须位于圆柱和外边界之间",
                                "task.mesh.near_field_radius"))
    if time["end_time"] <= time["start_time"]:
        errors.append(issue("TIME_RANGE", "结束时间必须大于起始时间", "task.time_control"))
    expected_nu = physics["inlet_velocity"] * diameter / physics["reynolds_number"]
    if not close(physics["kinematic_viscosity"], expected_nu):
        errors.append(issue("REYNOLDS_MISMATCH", "黏度不满足 nu=U*D/Re", "task.physics"))
    if "target_cells" in mesh:
        report["warnings"].append(issue(
            "MESH_TARGET_ROUNDED", "目标单元数是规划值；实际数量以生成网格为准",
            "task.mesh.target_cells"))
    if task.get("convergence"):
        report["warnings"].append(issue(
            "CONVERGENCE_NOT_VERIFIED", "收敛目标仅记录，尚未验证求解效果", "task.convergence"))
    report["task_valid"] = not errors
    return report
