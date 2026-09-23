"""Bounded repairs for the bundled template, backed by static evidence."""
from __future__ import annotations

from pathlib import Path
import re

from cfd_memo_agent.validator import validate_case, validate_task
from cfd_memo_agent.validator.foam import parse_foam

FAULTS = ("missing-boundary", "bad-transport")
FIELDS = ("0/U", "0/p")
TRANSPORT = "constant/physicalProperties"
FIELD_CODES = {"BOUNDARY_NAMES", "BOUNDARY_TYPE", "FIELD_VALUE", "VALUE_MISMATCH",
               "CONFIG_STRUCTURE", "CONFIG_ENTRY"}
TRANSPORT_CODES = {"CONFIG_NUMBER", "CONFIG_ENTRY", "DIMENSIONS", "VALUE_MISMATCH"}


def _change(relative, before, after):
    return {"file": relative, "before": before, "after": after}


def inject_fault(case_dir: Path, fault: str) -> dict:
    """Modify only a fresh workflow-owned first-round copy."""
    if fault not in FAULTS:
        raise ValueError("未知受控故障")
    relative = "0/U" if fault == "missing-boundary" else TRANSPORT
    path = case_dir / relative
    before = path.read_text(encoding="utf-8")
    document = parse_foam(before)
    if fault == "missing-boundary":
        if "outlet" not in document["boundaryField"]:
            raise ValueError("默认模板缺少待注入故障的 outlet")
        # This mutation deliberately targets the bundled template, not arbitrary dictionaries.
        after, count = re.subn(r"\boutlet\s*\{[^{}]*\}", "", before)
    else:
        after, count = re.subn(r"(?m)^nu\s+[^;]+;", "nu [0 2 -1 0 0 0 0] -1;", before)
    if count != 1:
        raise ValueError("受控故障要求唯一的默认模板配置项")
    parse_foam(after)
    path.write_text(after, encoding="utf-8")
    return {"name": fault, "changes": [_change(relative, before, after)]}


def propose_repairs(task: dict, case_dir: Path, reference_dir: Path, findings: list[dict]) -> dict:
    """Return replacements only when every error belongs to an implemented rule."""
    if not validate_task(task)["task_valid"]:
        return {"changes": [], "stop_code": "TASK_INVALID"}
    if not validate_case(task, reference_dir)["config_valid"]:
        return {"changes": [], "stop_code": "REFERENCE_INVALID"}
    static = validate_case(task, case_dir)
    allowed_runtime = {"MISSING_BOUNDARY", "BAD_TRANSPORT"}
    static_codes = {item["code"] for item in static["errors"]}
    if any(item["code"] not in static_codes | allowed_runtime for item in findings):
        return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
    if not static["errors"]:
        return {"changes": [], "stop_code": "NO_EFFECTIVE_CHANGE"}
    targets = set()
    for problem in static["errors"]:
        code, location = problem["code"], problem["location"]
        if code in FIELD_CODES and any(location == f or location.startswith(f + ".") for f in FIELDS):
            targets.add(next(f for f in FIELDS if location == f or location.startswith(f + ".")))
        elif code in TRANSPORT_CODES and (location == TRANSPORT or location.startswith(TRANSPORT + ".")):
            targets.add(TRANSPORT)
        else:
            return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
    changes = []
    for relative in sorted(targets):
        try:
            before = (case_dir / relative).read_text(encoding="utf-8")
            after = (reference_dir / relative).read_text(encoding="utf-8")
            current, reference = parse_foam(before), parse_foam(after)
        except (OSError, UnicodeError, ValueError, RecursionError):
            return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
        key = "nu" if relative == TRANSPORT else "boundaryField"
        current.pop(key, None)
        reference.pop(key, None)
        # Whole-file replacement is safe only if all other parsed settings agree.
        if current != reference:
            return {"changes": [], "stop_code": "UNSUPPORTED_ERROR"}
        if before != after:
            changes.append(_change(relative, before, after))
    return {"changes": changes, "stop_code": None if changes else "NO_EFFECTIVE_CHANGE"}


def apply_repairs(case_dir: Path, changes: list[dict]) -> None:
    """Apply reviewed replacements to an already isolated next-round copy."""
    for change in changes:
        if change["file"] not in {*FIELDS, TRANSPORT}:
            raise ValueError("修正规则不允许修改此文件")
        path = case_dir / change["file"]
        if path.read_text(encoding="utf-8") != change["before"]:
            raise ValueError("待修改文件与诊断时内容不同")
    for change in changes:
        (case_dir / change["file"]).write_text(change["after"], encoding="utf-8")
