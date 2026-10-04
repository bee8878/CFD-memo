"""Task checks independent of the planner and generator."""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from cfd_memo_agent.case_adapters import CYLINDER_BOUNDARIES, get_case_adapter
from .foam import read_json

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas/task.schema.json"
BOUNDARIES = CYLINDER_BOUNDARIES


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

    try:
        adapter = get_case_adapter(task)
    except ValueError as exc:
        errors.append(issue("UNSUPPORTED_CASE", str(exc), "task.case_type"))
    else:
        for problem in adapter.support_issues(task):
            errors.append(issue(problem["code"], problem["message"], problem["location"]))
        for problem in adapter.physical_issues(task):
            errors.append(issue(problem["code"], problem["message"], problem["location"]))
        for warning in adapter.task_warnings(task):
            report["warnings"].append(issue(
                warning["code"], warning["message"], warning["location"]))

    time = task["time_control"]
    if time["end_time"] <= time["start_time"]:
        errors.append(issue("TIME_RANGE", "结束时间必须大于起始时间", "task.time_control"))
    if task.get("convergence"):
        report["warnings"].append(issue(
            "CONVERGENCE_NOT_VERIFIED", "收敛目标仅记录，尚未验证求解效果", "task.convergence"))
    report["task_valid"] = not errors
    return report
